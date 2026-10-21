"""Private S3-compatible object storage and malware-scanning boundaries."""

import hashlib
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import boto3
import httpx
from botocore.config import Config

from aurinara_platform.config import Settings


@dataclass(frozen=True, slots=True)
class ObjectFacts:
    size_bytes: int
    sha256: str
    media_type: str


class ObjectStorage(Protocol):
    def create_multipart(
        self,
        key: str,
        media_type: str,
        sha256: str,
        retention_until: datetime | None = None,
    ) -> str: ...

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> str: ...

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[tuple[int, str]]
    ) -> None: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...

    def facts(self, key: str) -> ObjectFacts: ...

    def presign_download(self, key: str) -> str: ...

    def read_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes: ...

    def iter_bytes(
        self, key: str, *, chunk_size: int = 1024 * 1024, max_bytes: int | None = None
    ) -> Iterator[bytes]: ...

    def write_bytes(self, key: str, data: bytes, media_type: str, sha256: str) -> ObjectFacts: ...

    def write_stream(
        self,
        key: str,
        chunks: Iterable[bytes],
        media_type: str,
        sha256: str,
        *,
        size_bytes: int | None = None,
    ) -> ObjectFacts: ...

    def delete(self, key: str) -> None: ...


class S3ObjectStorage:
    """S3 adapter that never exposes credentials or permanent object URLs."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.object_store_bucket
        self._presign_ttl = settings.object_presign_ttl_seconds
        self._sse_algorithm = settings.object_store_sse_algorithm
        self._kms_key_id = settings.object_store_kms_key_id
        self._object_lock_enabled = settings.object_lock_enabled
        self._object_lock_mode = settings.object_lock_mode
        access_key = settings.object_store_access_key.get_secret_value()
        secret_key = settings.object_store_secret_key.get_secret_value()
        credentials = (
            {"aws_access_key_id": access_key, "aws_secret_access_key": secret_key}
            if access_key and secret_key
            else {}
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.object_store_endpoint or None,
            region_name=settings.object_store_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.object_store_path_style else "auto"},
            ),
            **credentials,
        )

    def create_multipart(
        self,
        key: str,
        media_type: str,
        sha256: str,
        retention_until: datetime | None = None,
    ) -> str:
        options: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": media_type,
            "Metadata": {"sha256": sha256},
        }
        if self._sse_algorithm != "none":
            options["ServerSideEncryption"] = self._sse_algorithm
        if self._kms_key_id:
            options["SSEKMSKeyId"] = self._kms_key_id
        if self._object_lock_enabled and retention_until is not None:
            options["ObjectLockMode"] = self._object_lock_mode
            options["ObjectLockRetainUntilDate"] = retention_until
        result = self._client.create_multipart_upload(
            **options,
        )
        return str(result["UploadId"])

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> str:
        return str(
            self._client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=self._presign_ttl,
            )
        )

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": part_number, "ETag": etag} for part_number, etag in parts]
            },
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)

    def facts(self, key: str) -> ObjectFacts:
        # Multipart ETags and caller-provided metadata are not content hashes.
        # Finalization therefore hashes the stored bytes before publication.
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
                    size += len(chunk)
        finally:
            body.close()
        return ObjectFacts(
            size_bytes=size,
            sha256=digest.hexdigest(),
            media_type=str(response.get("ContentType") or "application/octet-stream"),
        )

    def presign_download(self, key: str) -> str:
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._presign_ttl,
            )
        )

    def read_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        return b"".join(self.iter_bytes(key, max_bytes=max_bytes))

    def iter_bytes(
        self, key: str, *, chunk_size: int = 1024 * 1024, max_bytes: int | None = None
    ) -> Iterator[bytes]:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        read = 0
        try:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if not chunk:
                    continue
                read += len(chunk)
                if max_bytes is not None and read > max_bytes:
                    raise ValueError("object exceeds the requested read limit")
                yield bytes(chunk)
        finally:
            body.close()

    def write_bytes(self, key: str, data: bytes, media_type: str, sha256: str) -> ObjectFacts:
        return self.write_stream(key, (data,), media_type, sha256, size_bytes=len(data))

    def write_stream(
        self,
        key: str,
        chunks: Iterable[bytes],
        media_type: str,
        sha256: str,
        *,
        size_bytes: int | None = None,
    ) -> ObjectFacts:
        digest = hashlib.sha256()
        actual_size = 0
        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as body:
            for chunk in chunks:
                if not chunk:
                    continue
                digest.update(chunk)
                actual_size += len(chunk)
                body.write(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != sha256 or (size_bytes is not None and actual_size != size_bytes):
                raise ValueError("stream checksum or size does not match")
            body.seek(0)
            options: dict[str, object] = {
                "Bucket": self._bucket,
                "Key": key,
                "Body": body,
                "ContentType": media_type,
                "Metadata": {"sha256": sha256},
            }
            if self._sse_algorithm != "none":
                options["ServerSideEncryption"] = self._sse_algorithm
            if self._kms_key_id:
                options["SSEKMSKeyId"] = self._kms_key_id
            self._client.put_object(**options)
        return ObjectFacts(actual_size, actual_sha256, media_type)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


class MalwareScanner(Protocol):
    def scan(self, object_id: str) -> bool: ...


class DevelopmentScanner:
    """Explicit non-production scanner used only when scanning is not required."""

    def scan(self, object_id: str) -> bool:
        del object_id
        return True


class HttpMalwareScanner:
    def __init__(self, settings: Settings) -> None:
        if not settings.malware_scanner_url:
            raise ValueError("malware scanner URL is required")
        self._url = settings.malware_scanner_url
        token = settings.malware_scanner_token
        self._headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}

    def scan(self, object_id: str) -> bool:
        response = httpx.post(
            self._url,
            json={"object_id": object_id},
            headers=self._headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result") == "clean"


def checksum_key(environment: str, study_id: str, sha256: str) -> str:
    """Create an immutable, study-isolated key without using a filename."""

    return f"{environment}/studies/{study_id}/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"
