"""Rich-file processing and collaboration for the central Repository.

The module deliberately operates on immutable Repository versions.  Format
metadata is advisory; authorization is always re-evaluated against the node.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import duckdb
import httpx
import jwt
from pypdf import PdfReader
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    ObjectReference,
    RepositoryFileVersion,
    RepositoryJob,
    RepositoryNode,
    RepositoryNodeRevision,
    RepositoryOfficeParticipant,
    RepositoryOfficeSession,
    RepositoryPdfAnnotation,
    RepositoryPdfAnnotationRevision,
    RepositorySourceLink,
    RepositoryVersionProfile,
    StoredObject,
    User,
    utc_now,
)
from aurinara_platform.problems import Problem
from aurinara_platform.repository import RepositoryService, _normalized_name
from aurinara_platform.schemas import (
    OfficeSessionResponse,
    PdfAnnotationCreate,
    PdfAnnotationPatch,
    RepositoryCapabilities,
    RepositoryConversionCreate,
    RepositoryExportCreate,
    TabularPreviewRequest,
    TabularPreviewResponse,
)
from aurinara_platform.storage import MalwareScanner, ObjectStorage, checksum_key

ANALYZER_VERSION = "rich-v1"
OFFICE_KINDS = {"docx": "word", "xlsx": "cell", "pptx": "slide"}
OFFICE_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RichRepositoryService:
    def __init__(
        self,
        settings: Settings,
        repository: RepositoryService,
        storage: ObjectStorage,
        scanner: MalwareScanner,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.scanner = scanner

    # ---------- capabilities and profiles ----------

    def profile(self, session: Session, version_id: UUID) -> RepositoryVersionProfile | None:
        return session.get(RepositoryVersionProfile, version_id)

    def capabilities(
        self,
        session: Session,
        principal: Principal,
        node: RepositoryNode,
        version: RepositoryFileVersion | None,
    ) -> RepositoryCapabilities:
        can_manage = False
        try:
            self.repository.get_node(session, node.id, principal, write=True)
            can_manage = node.lifecycle_status == "active"
        except Problem:
            pass
        profile = session.get(RepositoryVersionProfile, version.id) if version else None
        kind = profile.file_kind if profile and profile.processing_status == "ready" else "unknown"
        conversion_targets: list[str] = []
        if self.settings.repository_conversion_enabled and can_manage:
            if kind == "csv":
                conversion_targets = ["parquet"]
            elif kind == "parquet":
                conversion_targets = ["csv"]
        return RepositoryCapabilities(
            can_preview=self.settings.repository_rich_preview_enabled
            and kind in {"pdf", "csv", "parquet", *OFFICE_KINDS},
            can_edit=self.settings.repository_office_enabled
            and can_manage
            and kind in OFFICE_KINDS
            and version is not None
            and version.version_number == node.current_version_number,
            can_annotate=self.settings.repository_pdf_annotations_enabled
            and can_manage
            and kind == "pdf",
            can_manage=can_manage,
            conversion_targets=conversion_targets,  # type: ignore[arg-type]
        )

    # ---------- durable jobs ----------

    def enqueue(
        self,
        session: Session,
        *,
        job_type: str,
        version: RepositoryFileVersion,
        actor: User,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> RepositoryJob:
        existing = session.scalar(
            select(RepositoryJob).where(RepositoryJob.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.job_type != job_type or existing.version_id != version.id:
                raise Problem(409, "idempotency_conflict", "The idempotency key is already used")
            return existing
        job = RepositoryJob(
            job_type=job_type,
            version_id=version.id,
            requested_by=actor.id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        session.add(job)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "idempotency_conflict", "The idempotency key is already used"
            ) from error
        return job

    def get_job(self, session: Session, job_id: UUID, principal: Principal) -> RepositoryJob:
        job = session.get(RepositoryJob, job_id)
        if job is None:
            raise Problem(404, "repository_job_not_found", "The Repository job was not found")
        self.repository.resolve_version(session, job.version_id, principal)
        return job

    def claim_job(
        self, session: Session, worker_id: str, lease_seconds: int = 120
    ) -> RepositoryJob | None:
        now = utc_now()
        job = session.scalar(
            select(RepositoryJob)
            .where(
                RepositoryJob.available_at <= now,
                RepositoryJob.attempts < RepositoryJob.max_attempts,
                or_(
                    RepositoryJob.state == "queued",
                    (RepositoryJob.state == "running") & (RepositoryJob.lease_expires_at < now),
                ),
            )
            .order_by(RepositoryJob.available_at, RepositoryJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.state = "running"
        job.attempts += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        session.flush()
        return job

    def execute_job(self, session: Session, job: RepositoryJob) -> None:
        try:
            if job.job_type == "analyze":
                result = self._analyze(session, job.version_id)
            elif job.job_type == "convert":
                result = self._convert(session, job)
            elif job.job_type == "annotated_export":
                result = self._annotated_export(session, job)
            else:
                raise RuntimeError("unsupported_job_type")
        except Exception as error:
            job.failure_code = getattr(error, "code", None) or type(error).__name__
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = utc_now()
            if job.attempts >= job.max_attempts:
                job.state = "failed"
                job.completed_at = utc_now()
            else:
                job.state = "queued"
                job.available_at = utc_now() + timedelta(seconds=min(300, 2**job.attempts))
            return
        job.result = result
        job.state = "succeeded"
        job.failure_code = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.completed_at = utc_now()
        job.updated_at = job.completed_at

    # ---------- format analysis ----------

    def _analyze(self, session: Session, version_id: UUID) -> dict[str, object]:
        version = session.get(RepositoryFileVersion, version_id)
        if version is None:
            raise RuntimeError("version_missing")
        stored = session.get(StoredObject, version.object_id)
        if stored is None:
            raise RuntimeError("object_missing")
        profile = session.get(RepositoryVersionProfile, version.id)
        if profile is None:
            profile = RepositoryVersionProfile(version_id=version.id)
            session.add(profile)
        profile.processing_status = "pending"
        profile.failure_code = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                source_path = Path(directory, "source")
                self._download_to_path(stored.object_key, source_path)
                kind, media_type, metadata = self.detect_path(source_path)
                profile.file_kind = kind
                profile.detected_media_type = media_type
                profile.metadata_json = metadata
                profile.analyzer_version = ANALYZER_VERSION
                profile.processing_status = "ready" if kind != "unknown" else "unsupported"
                if (
                    kind == "csv"
                    and source_path.stat().st_size <= self.settings.repository_tabular_max_bytes
                ):
                    preview = self._csv_preview_object(session, stored, source_path, metadata)
                    profile.preview_object_id = preview.id
        except Exception as error:
            profile.processing_status = "failed"
            profile.failure_code = type(error).__name__
            raise
        finally:
            profile.updated_at = utc_now()
        return {"file_kind": profile.file_kind, "processing_status": profile.processing_status}

    @staticmethod
    def detect_path(
        path: Path, override: dict[str, object] | None = None
    ) -> tuple[str, str, dict[str, object]]:
        size = path.stat().st_size
        with path.open("rb") as source:
            prefix = source.read(65536)
            suffix = b""
            if size >= 4:
                source.seek(-4, os.SEEK_END)
                suffix = source.read(4)
        if prefix.startswith(b"%PDF-"):
            try:
                reader = PdfReader(str(path))
                return "pdf", "application/pdf", {"page_count": len(reader.pages)}
            except Exception:
                return "unknown", "application/octet-stream", {"signature_mismatch": "pdf"}
        if size >= 8 and prefix[:4] == b"PAR1" and suffix == b"PAR1":
            connection = duckdb.connect(config={"threads": 1})
            try:
                description = connection.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
                ).fetchall()
                count = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(path)]
                ).fetchone()[0]
            finally:
                connection.close()
            return (
                "parquet",
                "application/vnd.apache.parquet",
                {
                    "schema": [{"name": row[0], "type": row[1]} for row in description],
                    "row_count": count,
                },
            )
        if prefix.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names:
                        kind = "docx"
                    elif "xl/workbook.xml" in names:
                        kind = "xlsx"
                    elif "ppt/presentation.xml" in names:
                        kind = "pptx"
                    else:
                        return "unknown", "application/zip", {}
            except zipfile.BadZipFile:
                return "unknown", "application/octet-stream", {"signature_mismatch": "zip"}
            return kind, OFFICE_MEDIA_TYPES[kind], {"office_document_type": OFFICE_KINDS[kind]}
        return RichRepositoryService._detect_csv(prefix, override)

    @staticmethod
    def detect(
        data: bytes, override: dict[str, object] | None = None
    ) -> tuple[str, str, dict[str, object]]:
        if data.startswith(b"%PDF-"):
            try:
                reader = PdfReader(io.BytesIO(data))
                return "pdf", "application/pdf", {"page_count": len(reader.pages)}
            except Exception:
                return "unknown", "application/octet-stream", {"signature_mismatch": "pdf"}
        if len(data) >= 8 and data[:4] == b"PAR1" and data[-4:] == b"PAR1":
            with tempfile.NamedTemporaryFile(suffix=".parquet") as handle:
                handle.write(data)
                handle.flush()
                connection = duckdb.connect(config={"threads": 1})
                try:
                    description = connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?)", [handle.name]
                    ).fetchall()
                    count = connection.execute(
                        "SELECT count(*) FROM read_parquet(?)", [handle.name]
                    ).fetchone()[0]
                finally:
                    connection.close()
            return (
                "parquet",
                "application/vnd.apache.parquet",
                {
                    "schema": [{"name": row[0], "type": row[1]} for row in description],
                    "row_count": count,
                },
            )
        if data.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = set(archive.namelist())
                    if "word/document.xml" in names:
                        kind = "docx"
                    elif "xl/workbook.xml" in names:
                        kind = "xlsx"
                    elif "ppt/presentation.xml" in names:
                        kind = "pptx"
                    else:
                        return "unknown", "application/zip", {}
            except zipfile.BadZipFile:
                return "unknown", "application/octet-stream", {"signature_mismatch": "zip"}
            return kind, OFFICE_MEDIA_TYPES[kind], {"office_document_type": OFFICE_KINDS[kind]}
        return RichRepositoryService._detect_csv(data, override)

    @staticmethod
    def _detect_csv(
        data: bytes, override: dict[str, object] | None = None
    ) -> tuple[str, str, dict[str, object]]:
        if b"\x00" in data[:8192]:
            return "unknown", "application/octet-stream", {}
        override = override or {}
        encodings = (
            [str(override["encoding"])]
            if override.get("encoding")
            else [
                "utf-8-sig",
                "utf-8",
                "windows-1252",
            ]
        )
        text = None
        encoding = None
        for candidate in encodings:
            try:
                text = data.decode(candidate)
                encoding = candidate
                break
            except UnicodeDecodeError:
                continue
        if text is None or not text.strip():
            return "unknown", "application/octet-stream", {}
        sample = text[:65536]
        delimiter = override.get("delimiter")
        has_header = override.get("has_header")
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|") if not delimiter else None
            delimiter = delimiter or dialect.delimiter
            has_header = csv.Sniffer().has_header(sample) if has_header is None else has_header
            rows = list(csv.reader(io.StringIO(sample), delimiter=str(delimiter)))
        except csv.Error:
            return "unknown", "text/plain", {}
        if not rows or max(len(row) for row in rows) < 2:
            return "unknown", "text/plain", {}
        return (
            "csv",
            "text/csv",
            {
                "csv": {
                    "delimiter": delimiter,
                    "encoding": encoding,
                    "has_header": bool(has_header),
                }
            },
        )

    def _csv_preview_object(
        self,
        session: Session,
        source: StoredObject,
        source_path: Path,
        metadata: dict[str, object],
    ) -> StoredObject:
        csv_meta = metadata["csv"]
        assert isinstance(csv_meta, dict)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory, "preview.parquet")
            connection = duckdb.connect(config={"threads": self.settings.repository_worker_threads})
            try:
                connection.execute(
                    f"SET memory_limit='{self.settings.repository_worker_memory_limit}'"
                )
                safe_source = str(source_path).replace("'", "''")
                delimiter = str(csv_meta["delimiter"]).replace("'", "''")
                header = "true" if csv_meta["has_header"] else "false"
                encoding = (
                    "utf-8" if csv_meta["encoding"] == "utf-8-sig" else str(csv_meta["encoding"])
                ).replace("'", "''")
                connection.execute(
                    f"COPY (SELECT * FROM read_csv('{safe_source}', delim='{delimiter}', "
                    f"header={header}, encoding='{encoding}')) "
                    "TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                    [str(output_path)],
                )
                count = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(output_path)]
                ).fetchone()[0]
                schema = connection.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [str(output_path)]
                ).fetchall()
            finally:
                connection.close()
            digest, size = self._path_facts(output_path)
            key = checksum_key(self.settings.environment, str(source.study_id), digest)
            existing = session.scalar(
                select(StoredObject).where(
                    StoredObject.study_id == source.study_id,
                    StoredObject.sha256 == digest,
                    StoredObject.status == "available",
                )
            )
            if existing is not None:
                result = existing
            else:
                self.storage.write_stream(
                    key,
                    self._path_chunks(output_path),
                    "application/vnd.apache.parquet",
                    digest,
                    size_bytes=size,
                )
                result = StoredObject(
                    study_id=source.study_id,
                    owner_product="platform",
                    classification=source.classification,
                    sha256=digest,
                    size_bytes=size,
                    media_type="application/vnd.apache.parquet",
                    object_key=key,
                    status="available",
                    retention_until=source.retention_until,
                    created_by=source.created_by,
                    finalized_at=utc_now(),
                )
                session.add(result)
                session.flush()
        metadata["row_count"] = count
        metadata["schema"] = [{"name": row[0], "type": row[1]} for row in schema]
        return result

    # ---------- tabular preview and conversion ----------

    def tabular_preview(
        self,
        session: Session,
        version_id: UUID,
        command: TabularPreviewRequest,
        principal: Principal,
    ) -> TabularPreviewResponse:
        _, version, stored = self.repository.resolve_version(session, version_id, principal)
        profile = session.get(RepositoryVersionProfile, version.id)
        if profile is None or profile.processing_status != "ready":
            return TabularPreviewResponse(
                processing_status=profile.processing_status if profile else "pending",
                schema_=[],
                rows=[],
                total_rows=0,
                page=command.page,
                page_size=command.page_size,
                parsing={},
            )
        if profile.file_kind not in {"csv", "parquet"}:
            raise Problem(409, "tabular_preview_unsupported", "This version is not tabular")
        if (stored.size_bytes or 0) > self.settings.repository_tabular_max_bytes:
            raise Problem(
                413, "tabular_preview_too_large", "Interactive preview is limited to 1 GiB"
            )
        source = stored
        if (
            profile.file_kind == "csv"
            and profile.preview_object_id is not None
            and command.csv is None
        ):
            source = session.get(StoredObject, profile.preview_object_id) or stored
        suffix = ".parquet" if source.media_type == "application/vnd.apache.parquet" else ".csv"
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "source" + suffix)
            self._download_to_path(
                source.object_key,
                source_path,
                max_bytes=self.settings.repository_tabular_max_bytes,
            )
            connection = duckdb.connect(config={"threads": self.settings.repository_worker_threads})
            timeout = threading.Timer(
                self.settings.repository_query_timeout_seconds, connection.interrupt
            )
            timeout.start()
            try:
                connection.execute(
                    f"SET memory_limit='{self.settings.repository_worker_memory_limit}'"
                )
                relation, parsing = self._tabular_relation(profile, str(source_path), command)
                schema_rows = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
                schema = [{"name": row[0], "type": row[1]} for row in schema_rows]
                available = {item["name"] for item in schema}
                columns = command.columns or [item["name"] for item in schema]
                if not set(columns).issubset(available):
                    raise Problem(422, "unknown_tabular_column", "A selected column does not exist")
                clauses: list[str] = []
                values: list[object] = []
                operators = {
                    "eq": "=",
                    "ne": "!=",
                    "lt": "<",
                    "lte": "<=",
                    "gt": ">",
                    "gte": ">=",
                }
                for item in command.filters:
                    if item.column not in available:
                        raise Problem(
                            422, "unknown_tabular_column", "A filter column does not exist"
                        )
                    quoted = _quote_identifier(item.column)
                    if item.operator == "is_null":
                        clauses.append(f"{quoted} IS NULL")
                    elif item.operator == "contains":
                        clauses.append(f"CAST({quoted} AS VARCHAR) ILIKE ?")
                        values.append(f"%{item.value}%")
                    else:
                        clauses.append(f"{quoted} {operators[item.operator]} ?")
                        values.append(item.value)
                where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                order_parts = []
                for item in command.sort:
                    if item.column not in available:
                        raise Problem(422, "unknown_tabular_column", "A sort column does not exist")
                    order_parts.append(f"{_quote_identifier(item.column)} {item.direction.upper()}")
                order = f" ORDER BY {', '.join(order_parts)}" if order_parts else ""
                total = connection.execute(
                    f"SELECT count(*) FROM {relation}{where}", values
                ).fetchone()[0]
                select_columns = ", ".join(_quote_identifier(item) for item in columns)
                offset = (command.page - 1) * command.page_size
                cursor = connection.execute(
                    f"SELECT {select_columns} FROM {relation}{where}{order} LIMIT ? OFFSET ?",
                    [*values, command.page_size, offset],
                )
                rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            finally:
                timeout.cancel()
                connection.close()
        return TabularPreviewResponse(
            processing_status="ready",
            schema_=[{"name": item["name"], "type": item["type"]} for item in schema],
            rows=rows,
            total_rows=total,
            page=command.page,
            page_size=command.page_size,
            parsing=parsing,
        )

    @staticmethod
    def _tabular_relation(
        profile: RepositoryVersionProfile, path: str, command: TabularPreviewRequest
    ) -> tuple[str, dict[str, object]]:
        if path.endswith(".parquet"):
            safe = path.replace("'", "''")
            return f"read_parquet('{safe}')", {}
        base = dict(profile.metadata_json.get("csv", {}))
        if command.csv is not None:
            base.update(command.csv.model_dump(exclude_none=True))
        safe = path.replace("'", "''")
        delimiter = str(base.get("delimiter", ",")).replace("'", "''")
        header = "true" if base.get("has_header", True) else "false"
        encoding = str(base.get("encoding", "utf-8")).replace("'", "''")
        if encoding == "utf-8-sig":
            encoding = "utf-8"
        return (
            f"read_csv('{safe}', delim='{delimiter}', header={header}, encoding='{encoding}')",
            base,
        )

    def create_conversion(
        self,
        session: Session,
        version_id: UUID,
        command: RepositoryConversionCreate,
        principal: Principal,
        actor: User,
    ) -> RepositoryJob:
        node, version, _ = self.repository.resolve_version(session, version_id, principal)
        self.repository.get_node(session, node.id, principal, write=True)
        profile = session.get(RepositoryVersionProfile, version.id)
        expected = {"csv": "parquet", "parquet": "csv"}
        if profile is None or expected.get(profile.file_kind) != command.target:
            raise Problem(409, "conversion_unsupported", "The requested conversion is unavailable")
        self.repository._assert_name_available(
            session, node.study_id, node.parent_id, _normalized_name(command.filename)
        )
        return self.enqueue(
            session,
            job_type="convert",
            version=version,
            actor=actor,
            idempotency_key=command.idempotency_key,
            payload={"filename": command.filename, "target": command.target},
        )

    def _convert(self, session: Session, job: RepositoryJob) -> dict[str, object]:
        version = session.get(RepositoryFileVersion, job.version_id)
        if version is None:
            raise RuntimeError("version_missing")
        node = session.get(RepositoryNode, version.node_id)
        source = session.get(StoredObject, version.object_id)
        profile = session.get(RepositoryVersionProfile, version.id)
        if node is None or source is None or profile is None:
            raise RuntimeError("conversion_source_missing")
        target = str(job.payload["target"])
        filename = str(job.payload["filename"])
        source_object = source
        if profile.file_kind == "csv" and profile.preview_object_id:
            source_object = session.get(StoredObject, profile.preview_object_id) or source
        source_suffix = (
            ".parquet" if source_object.media_type == "application/vnd.apache.parquet" else ".csv"
        )
        target_suffix = ".parquet" if target == "parquet" else ".csv"
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "source" + source_suffix)
            target_path = Path(directory, "target" + target_suffix)
            self._download_to_path(source_object.object_key, source_path)
            connection = duckdb.connect(config={"threads": self.settings.repository_worker_threads})
            try:
                connection.execute(
                    f"SET memory_limit='{self.settings.repository_worker_memory_limit}'"
                )
                relation = f"read_parquet('{str(source_path).replace(os.sep, '/')}')"
                if source_suffix == ".csv":
                    relation, _ = self._tabular_relation(
                        profile, str(source_path).replace(os.sep, "/"), TabularPreviewRequest()
                    )
                fmt = "PARQUET, COMPRESSION ZSTD" if target == "parquet" else "CSV, HEADER"
                connection.execute(
                    f"COPY (SELECT * FROM {relation}) TO ? (FORMAT {fmt})", [str(target_path)]
                )
            finally:
                connection.close()
            media = "application/vnd.apache.parquet" if target == "parquet" else "text/csv"
            derived = self._create_derived_path(
                session,
                node,
                version,
                target_path,
                media,
                filename,
                job.requested_by,
                "conversion",
            )
        return {"node_id": str(derived.id), "filename": filename}

    # ---------- PDF annotations ----------

    def list_annotations(
        self, session: Session, version_id: UUID, principal: Principal
    ) -> list[RepositoryPdfAnnotation]:
        self._require_pdf(session, version_id, principal, write=False)
        return list(
            session.scalars(
                select(RepositoryPdfAnnotation)
                .where(
                    RepositoryPdfAnnotation.version_id == version_id,
                    RepositoryPdfAnnotation.deleted_at.is_(None),
                )
                .order_by(RepositoryPdfAnnotation.page_number, RepositoryPdfAnnotation.created_at)
            )
        )

    def create_annotation(
        self,
        session: Session,
        version_id: UUID,
        command: PdfAnnotationCreate,
        principal: Principal,
        actor: User,
    ) -> RepositoryPdfAnnotation:
        _, version = self._require_pdf(session, version_id, principal, write=True)
        profile = session.get(RepositoryVersionProfile, version.id)
        assert profile is not None
        page_count = profile.metadata_json.get("page_count")
        if isinstance(page_count, int) and command.page_number > page_count:
            raise Problem(422, "annotation_page_invalid", "The PDF page does not exist")
        self._validate_geometry(command.geometry)
        annotation = RepositoryPdfAnnotation(
            version_id=version_id,
            page_number=command.page_number,
            geometry=command.geometry,
            selected_text=command.selected_text,
            comment=command.comment,
            category=command.category,
            status=command.status,
            style=command.style.model_dump(),
            created_by=actor.id,
            updated_by=actor.id,
        )
        session.add(annotation)
        session.flush()
        self._record_annotation(session, annotation, "created", actor)
        return annotation

    def patch_annotation(
        self,
        session: Session,
        annotation_id: UUID,
        command: PdfAnnotationPatch,
        revision: int,
        principal: Principal,
        actor: User,
    ) -> RepositoryPdfAnnotation:
        annotation = self._annotation(session, annotation_id)
        self._require_pdf(session, annotation.version_id, principal, write=True)
        if annotation.revision != revision:
            raise Problem(412, "annotation_revision_mismatch", "The annotation has changed")
        if command.geometry is not None:
            self._validate_geometry(command.geometry)
        for field in command.model_fields_set:
            value = getattr(command, field)
            setattr(annotation, field, value.model_dump() if field == "style" and value else value)
        annotation.revision += 1
        annotation.updated_by = actor.id
        annotation.updated_at = utc_now()
        self._record_annotation(session, annotation, "updated", actor)
        return annotation

    def delete_annotation(
        self,
        session: Session,
        annotation_id: UUID,
        revision: int,
        principal: Principal,
        actor: User,
    ) -> None:
        annotation = self._annotation(session, annotation_id)
        self._require_pdf(session, annotation.version_id, principal, write=True)
        if annotation.revision != revision:
            raise Problem(412, "annotation_revision_mismatch", "The annotation has changed")
        annotation.revision += 1
        annotation.updated_by = actor.id
        annotation.updated_at = utc_now()
        annotation.deleted_at = annotation.updated_at
        self._record_annotation(session, annotation, "deleted", actor)

    def create_annotated_export(
        self,
        session: Session,
        version_id: UUID,
        command: RepositoryExportCreate,
        principal: Principal,
        actor: User,
    ) -> RepositoryJob:
        node, version = self._require_pdf(session, version_id, principal, write=True)
        self.repository._assert_name_available(
            session, node.study_id, node.parent_id, _normalized_name(command.filename)
        )
        return self.enqueue(
            session,
            job_type="annotated_export",
            version=version,
            actor=actor,
            idempotency_key=command.idempotency_key,
            payload={"filename": command.filename},
        )

    def _annotated_export(self, session: Session, job: RepositoryJob) -> dict[str, object]:
        from reportlab.lib.colors import Color
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen.canvas import Canvas

        version = session.get(RepositoryFileVersion, job.version_id)
        node = session.get(RepositoryNode, version.node_id) if version else None
        stored = session.get(StoredObject, version.object_id) if version else None
        if version is None or node is None or stored is None:
            raise RuntimeError("pdf_source_missing")
        annotations = list(
            session.scalars(
                select(RepositoryPdfAnnotation).where(
                    RepositoryPdfAnnotation.version_id == version.id,
                    RepositoryPdfAnnotation.deleted_at.is_(None),
                )
            )
        )
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "source.pdf")
            output_path = Path(directory, "annotated.pdf")
            self._download_to_path(stored.object_key, source_path)
            reader = PdfReader(str(source_path))
            # Overlay normalized highlights and comments without changing the source.
            for page_number in range(1, len(reader.pages) + 1):
                page = reader.pages[page_number - 1]
                width, height = float(page.mediabox.width), float(page.mediabox.height)
                overlay = io.BytesIO()
                canvas = Canvas(overlay, pagesize=(width, height))
                for item in (value for value in annotations if value.page_number == page_number):
                    geometry = item.geometry
                    x = float(geometry.get("x", 0)) * width
                    y = (
                        1 - float(geometry.get("y", 0)) - float(geometry.get("height", 0))
                    ) * height
                    w = float(geometry.get("width", 0)) * width
                    h = float(geometry.get("height", 0)) * height
                    style = item.style or {}

                    def color(value: object, fallback: str, alpha: float = 1) -> Color:
                        raw = value if isinstance(value, str) and len(value) == 7 else fallback
                        try:
                            return Color(
                                int(raw[1:3], 16) / 255,
                                int(raw[3:5], 16) / 255,
                                int(raw[5:7], 16) / 255,
                                alpha=alpha,
                            )
                        except ValueError:
                            return Color(0, 0, 0, alpha=alpha)

                    opacity = min(1.0, max(0.05, float(style.get("opacity", 0.32))))
                    canvas.setFillColor(color(style.get("fill_color"), "#facc15", opacity))
                    canvas.setStrokeColor(color(style.get("border_color"), "#d97706"))
                    canvas.setLineWidth(1)
                    canvas.rect(x, y, w, h, fill=1, stroke=1)
                    if item.comment:
                        family = str(style.get("font_family", "Helvetica"))
                        if family not in {"Helvetica", "Times-Roman", "Courier"}:
                            family = "Helvetica"
                        font_size = min(48.0, max(6.0, float(style.get("font_size", 10))))
                        canvas.setFillColor(color(style.get("text_color"), "#1f2937"))
                        canvas.setFont(family, font_size)
                        available = max(1.0, w - 6)
                        words = item.comment.replace("\n", " \n ").split(" ")
                        lines: list[str] = []
                        current = ""
                        for word in words:
                            if word == "\n":
                                if current:
                                    lines.append(current)
                                current = ""
                                continue
                            candidate = f"{current} {word}".strip()
                            if current and stringWidth(candidate, family, font_size) > available:
                                lines.append(current)
                                current = word
                            else:
                                current = candidate
                        if current:
                            lines.append(current)
                        baseline = y + h - font_size - 3
                        for line in lines:
                            if baseline < y + 2:
                                break
                            canvas.drawString(x + 3, baseline, line[:300])
                            baseline -= font_size * 1.2
                canvas.save()
                overlay.seek(0)
                page.merge_page(PdfReader(overlay).pages[0])
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.add_metadata({"/AurinaraSourceVersion": str(version.id)})
            writer.write(str(output_path))
            filename = str(job.payload["filename"])
            derived = self._create_derived_path(
                session,
                node,
                version,
                output_path,
                "application/pdf",
                filename,
                job.requested_by,
                "annotated_export",
            )
        return {"node_id": str(derived.id), "filename": filename}

    # ---------- ONLYOFFICE ----------

    def office_session(
        self,
        session: Session,
        version_id: UUID,
        mode: str,
        principal: Principal,
        actor: User,
    ) -> OfficeSessionResponse:
        if not self.settings.repository_office_enabled:
            raise Problem(503, "office_unavailable", "Office viewing and editing are unavailable")
        try:
            health = httpx.get(
                f"{self.settings.onlyoffice_internal_url.rstrip('/')}/healthcheck",
                timeout=2,
            )
            if health.status_code != 200:
                raise httpx.HTTPStatusError(
                    "ONLYOFFICE unhealthy", request=health.request, response=health
                )
        except httpx.HTTPError as error:
            raise Problem(
                503, "office_unavailable", "Office viewing and editing are unavailable"
            ) from error
        node, version, stored = self.repository.resolve_version(session, version_id, principal)
        profile = session.get(RepositoryVersionProfile, version.id)
        if (
            profile is None
            or profile.file_kind not in OFFICE_KINDS
            or profile.processing_status != "ready"
        ):
            raise Problem(409, "office_unsupported", "This version is not an editable Office file")
        editable = mode == "edit"
        if editable:
            self.repository.get_node(session, node.id, principal, write=True)
            if version.version_number != node.current_version_number:
                raise Problem(
                    409, "office_version_not_current", "Only the current version can be edited"
                )
        now = utc_now()
        active = session.scalar(
            select(RepositoryOfficeSession).where(
                RepositoryOfficeSession.node_id == node.id,
                RepositoryOfficeSession.base_version_id == version.id,
                RepositoryOfficeSession.state == "active",
                RepositoryOfficeSession.lease_expires_at > now,
            )
        )
        if active is None:
            key_material = f"{self.settings.environment}:{version.id}:{stored.sha256}".encode()
            document_key = hashlib.sha256(key_material).hexdigest()
            active = RepositoryOfficeSession(
                node_id=node.id,
                base_version_id=version.id,
                document_key=document_key,
                editable=editable,
                callback_token_id=uuid4().hex,
                lease_expires_at=now
                + timedelta(seconds=self.settings.onlyoffice_session_ttl_seconds),
                last_saved_sha256=stored.sha256,
            )
            session.add(active)
            session.flush()
        elif editable:
            active.editable = True
        participant = session.get(RepositoryOfficeParticipant, (active.id, actor.id))
        if participant is None:
            session.add(RepositoryOfficeParticipant(session_id=active.id, user_id=actor.id))
        else:
            participant.last_seen_at = now
        content_token = self._office_token(
            {"typ": "content", "session_id": str(active.id), "version_id": str(version.id)},
            minutes=15,
        )
        callback_token = self._office_token(
            {"typ": "callback", "session_id": str(active.id), "jti": active.callback_token_id},
            seconds=self.settings.onlyoffice_session_ttl_seconds,
        )
        base = self.settings.onlyoffice_platform_url.rstrip("/")
        content_url = f"{base}/v1/integrations/onlyoffice/content/{active.id}?token={content_token}"
        callback_url = (
            f"{base}/v1/integrations/onlyoffice/callback/{active.id}?token={callback_token}"
        )
        config: dict[str, object] = {
            "document": {
                "fileType": profile.file_kind,
                "key": active.document_key,
                "title": node.display_name,
                "url": content_url,
                "permissions": {"edit": editable, "download": True, "print": True},
            },
            "documentType": OFFICE_KINDS[profile.file_kind],
            "editorConfig": {
                "mode": "edit" if editable else "view",
                "callbackUrl": callback_url,
                "user": {
                    "id": str(actor.id),
                    "name": actor.display_name or actor.email or principal.subject,
                },
                "customization": {"forcesave": True},
            },
        }
        config["token"] = jwt.encode(
            config, self.settings.onlyoffice_jwt_secret.get_secret_value(), algorithm="HS256"
        )
        return OfficeSessionResponse(
            session_id=active.id,
            document_server_url=self.settings.onlyoffice_public_url,
            config=config,
        )

    def office_content(
        self, session: Session, session_id: UUID, token: str
    ) -> tuple[Iterator[bytes], str, str, int | None]:
        claims = self._decode_office_token(token, "content", session_id)
        office = session.get(RepositoryOfficeSession, session_id)
        if office is None or office.state != "active" or _utc(office.lease_expires_at) <= utc_now():
            raise Problem(410, "office_session_expired", "The Office session has expired")
        if claims.get("version_id") != str(office.base_version_id):
            raise Problem(401, "office_token_invalid", "The Office token is invalid")
        version = session.get(RepositoryFileVersion, office.base_version_id)
        stored = session.get(StoredObject, version.object_id) if version else None
        node = session.get(RepositoryNode, office.node_id)
        if version is None or stored is None or node is None:
            raise Problem(404, "office_source_missing", "The Office source is unavailable")
        return (
            self.storage.iter_bytes(stored.object_key),
            stored.media_type,
            node.display_name,
            stored.size_bytes,
        )

    def office_callback(
        self, session: Session, session_id: UUID, token: str, payload: dict[str, object]
    ) -> dict[str, int]:
        claims = self._decode_office_token(token, "callback", session_id)
        office = session.get(RepositoryOfficeSession, session_id)
        if office is None or claims.get("jti") != office.callback_token_id:
            raise Problem(401, "office_token_invalid", "The Office token is invalid")
        status = int(payload.get("status", 0))
        if status not in {2, 6}:
            return {"error": 0}
        if payload.get("key") != office.document_key:
            raise Problem(401, "office_document_key_invalid", "The Office document key is invalid")
        url = payload.get("url")
        if not isinstance(url, str):
            raise Problem(422, "office_callback_invalid", "The saved document URL is missing")
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory, "office-output")
            media_type = self._fetch_onlyoffice(url, output_path)
            kind, detected, _ = self.detect_path(output_path)
            base_profile = session.get(RepositoryVersionProfile, office.base_version_id)
            if (
                base_profile is None
                or kind != base_profile.file_kind
                or media_type not in {detected, "application/octet-stream"}
            ):
                raise Problem(422, "office_output_invalid", "The saved document type is invalid")
            digest, _ = self._path_facts(output_path)
            if digest == office.last_saved_sha256:
                return {"error": 0}
            self._save_office_version(session, office, output_path, detected, digest, payload)
        office.last_saved_sha256 = digest
        office.state = "saved" if status == 2 else "active"
        office.updated_at = utc_now()
        return {"error": 0}

    # ---------- helpers ----------

    def _download_to_path(self, key: str, path: Path, *, max_bytes: int | None = None) -> None:
        with path.open("wb") as target:
            for chunk in self.storage.iter_bytes(key, max_bytes=max_bytes):
                target.write(chunk)

    @staticmethod
    def _path_chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    @staticmethod
    def _path_facts(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _create_derived_file(
        self,
        session: Session,
        source_node: RepositoryNode,
        source_version: RepositoryFileVersion,
        data: bytes,
        media_type: str,
        filename: str,
        actor_id: UUID,
        source_type: str,
    ) -> RepositoryNode:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "derived")
            path.write_bytes(data)
            return self._create_derived_path(
                session,
                source_node,
                source_version,
                path,
                media_type,
                filename,
                actor_id,
                source_type,
            )

    def _create_derived_path(
        self,
        session: Session,
        source_node: RepositoryNode,
        source_version: RepositoryFileVersion,
        path: Path,
        media_type: str,
        filename: str,
        actor_id: UUID,
        source_type: str,
    ) -> RepositoryNode:
        self.repository._assert_name_available(
            session, source_node.study_id, source_node.parent_id, _normalized_name(filename)
        )
        digest, size = self._path_facts(path)
        stored = session.scalar(
            select(StoredObject).where(
                StoredObject.study_id == source_node.study_id,
                StoredObject.sha256 == digest,
                StoredObject.status == "available",
            )
        )
        if stored is None:
            key = checksum_key(self.settings.environment, str(source_node.study_id), digest)
            self.storage.write_stream(
                key,
                self._path_chunks(path),
                media_type,
                digest,
                size_bytes=size,
            )
            stored = StoredObject(
                study_id=source_node.study_id,
                owner_product=source_node.source_product,
                classification=source_node.classification,
                sha256=digest,
                size_bytes=size,
                media_type=media_type,
                object_key=key,
                status="available",
                created_by=actor_id,
                finalized_at=utc_now(),
            )
            session.add(stored)
            session.flush()
        external = f"repository-derived:{source_type}:{source_version.id}:{digest[:16]}"
        reference = ObjectReference(
            object_id=stored.id,
            product=source_node.source_product,
            authorization_action=source_node.required_write_action,
            external_reference=external,
            idempotency_key=external,
            created_by=actor_id,
        )
        session.add(reference)
        session.flush()
        node = RepositoryNode(
            study_id=source_node.study_id,
            parent_id=source_node.parent_id,
            kind="file",
            display_name=filename.strip(),
            normalized_name=_normalized_name(filename),
            source_product=source_node.source_product,
            module=source_node.module,
            classification=source_node.classification,
            required_read_action=source_node.required_read_action,
            required_write_action=source_node.required_write_action,
            site_id=source_node.site_id,
            source_resource_type=source_type,
            source_resource_id=str(source_version.id),
            source_status="available",
            retention_metadata=source_node.retention_metadata,
            current_version_number=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(node)
        session.flush()
        version = RepositoryFileVersion(
            node_id=node.id,
            version_number=1,
            object_id=stored.id,
            object_reference_id=reference.id,
            derived_from_version_id=source_version.id,
            created_by=actor_id,
        )
        session.add(version)
        session.flush()
        session.add(
            RepositorySourceLink(
                node_id=node.id,
                version_id=version.id,
                object_reference_id=reference.id,
                product=node.source_product,
                external_reference=external,
                source_type=source_type,
                source_id=str(source_version.id),
                source_status="available",
                provenance={"derived_from_version_id": str(source_version.id)},
            )
        )
        session.add(
            RepositoryNodeRevision(
                node_id=node.id,
                revision=1,
                action="created",
                actor_id=actor_id,
                snapshot=self.repository._snapshot(node),
            )
        )
        self.repository.queue_analysis(session, version)
        return node

    def _require_pdf(
        self, session: Session, version_id: UUID, principal: Principal, *, write: bool
    ) -> tuple[RepositoryNode, RepositoryFileVersion]:
        node, version, _ = self.repository.resolve_version(session, version_id, principal)
        if write:
            self.repository.get_node(session, node.id, principal, write=True)
        profile = session.get(RepositoryVersionProfile, version.id)
        if profile is None or profile.file_kind != "pdf" or profile.processing_status != "ready":
            raise Problem(409, "pdf_annotations_unsupported", "This version is not a PDF")
        return node, version

    @staticmethod
    def _validate_geometry(geometry: dict[str, object]) -> None:
        for key in ("x", "y", "width", "height"):
            value = geometry.get(key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1
            ):
                raise Problem(422, "annotation_geometry_invalid", "Coordinates must be normalized")
        if float(geometry["x"]) + float(geometry["width"]) > 1.000001:
            raise Problem(422, "annotation_geometry_invalid", "Annotation exceeds the page")
        if float(geometry["y"]) + float(geometry["height"]) > 1.000001:
            raise Problem(422, "annotation_geometry_invalid", "Annotation exceeds the page")

    @staticmethod
    def _annotation(session: Session, annotation_id: UUID) -> RepositoryPdfAnnotation:
        value = session.get(RepositoryPdfAnnotation, annotation_id)
        if value is None or value.deleted_at is not None:
            raise Problem(404, "annotation_not_found", "The annotation was not found")
        return value

    @staticmethod
    def _annotation_snapshot(annotation: RepositoryPdfAnnotation) -> dict[str, object]:
        return {
            "version_id": str(annotation.version_id),
            "page_number": annotation.page_number,
            "geometry": annotation.geometry,
            "selected_text": annotation.selected_text,
            "comment": annotation.comment,
            "category": annotation.category,
            "status": annotation.status,
            "style": annotation.style,
            "deleted": annotation.deleted_at is not None,
        }

    def _record_annotation(
        self, session: Session, annotation: RepositoryPdfAnnotation, action: str, actor: User
    ) -> None:
        session.add(
            RepositoryPdfAnnotationRevision(
                annotation_id=annotation.id,
                revision=annotation.revision,
                action=action,
                actor_id=actor.id,
                snapshot=self._annotation_snapshot(annotation),
            )
        )

    def _office_token(
        self, claims: dict[str, object], *, minutes: int = 0, seconds: int = 0
    ) -> str:
        now = utc_now()
        return jwt.encode(
            {**claims, "iat": now, "exp": now + timedelta(minutes=minutes, seconds=seconds)},
            self.settings.onlyoffice_jwt_secret.get_secret_value(),
            algorithm="HS256",
        )

    def _decode_office_token(self, token: str, kind: str, session_id: UUID) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self.settings.onlyoffice_jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                options={"require": ["iat", "exp", "typ", "session_id"]},
            )
        except jwt.PyJWTError as error:
            raise Problem(401, "office_token_invalid", "The Office token is invalid") from error
        if claims.get("typ") != kind or claims.get("session_id") != str(session_id):
            raise Problem(401, "office_token_invalid", "The Office token is invalid")
        return claims

    def _fetch_onlyoffice(self, url: str, target: Path) -> str:
        allowed_hosts = {
            urlparse(self.settings.onlyoffice_public_url).hostname,
            urlparse(self.settings.onlyoffice_internal_url).hostname,
        }
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts:
            raise Problem(
                422, "office_callback_host_invalid", "The callback download host is invalid"
            )
        with httpx.stream("GET", url, follow_redirects=False, timeout=30) as response:
            if response.is_redirect:
                raise Problem(
                    422, "office_callback_redirect_rejected", "Office redirects are rejected"
                )
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > self.settings.onlyoffice_max_output_bytes:
                raise Problem(413, "office_output_too_large", "The saved Office file is too large")
            size = 0
            with target.open("wb") as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.settings.onlyoffice_max_output_bytes:
                        raise Problem(
                            413,
                            "office_output_too_large",
                            "The saved Office file is too large",
                        )
                    output.write(chunk)
            return response.headers.get("content-type", "application/octet-stream").split(";")[0]

    def _save_office_version(
        self,
        session: Session,
        office: RepositoryOfficeSession,
        path: Path,
        media_type: str,
        digest: str,
        payload: dict[str, object],
    ) -> None:
        node = session.get(RepositoryNode, office.node_id)
        base_version = session.get(RepositoryFileVersion, office.base_version_id)
        if node is None or base_version is None:
            raise Problem(404, "office_source_missing", "The Office source is unavailable")
        actor_id = office.last_editor_id or base_version.created_by
        users = payload.get("users")
        if isinstance(users, list):
            for value in users:
                try:
                    candidate = UUID(str(value))
                except ValueError:
                    continue
                if session.get(User, candidate) is not None:
                    actor_id = candidate
                    office.last_editor_id = candidate
                    break
        key = checksum_key(self.settings.environment, str(node.study_id), digest)
        stored = session.scalar(
            select(StoredObject).where(
                StoredObject.study_id == node.study_id,
                StoredObject.sha256 == digest,
                StoredObject.status == "available",
            )
        )
        if stored is None:
            _, size = self._path_facts(path)
            self.storage.write_stream(
                key,
                self._path_chunks(path),
                media_type,
                digest,
                size_bytes=size,
            )
            stored = StoredObject(
                study_id=node.study_id,
                owner_product=node.source_product,
                classification=node.classification,
                sha256=digest,
                size_bytes=size,
                media_type=media_type,
                object_key=key,
                status="pending",
                created_by=actor_id,
                finalized_at=utc_now(),
            )
            session.add(stored)
            session.flush()
            if not self.scanner.scan(str(stored.id)):
                self.storage.delete(key)
                stored.status = "rejected"
                raise Problem(422, "malware_detected", "The saved Office document was rejected")
            stored.status = "available"
        external = f"onlyoffice:{office.id}:{digest}"
        reference = session.scalar(
            select(ObjectReference).where(
                ObjectReference.product == node.source_product,
                ObjectReference.external_reference == external,
            )
        )
        if reference is None:
            reference = ObjectReference(
                object_id=stored.id,
                product=node.source_product,
                authorization_action=node.required_write_action,
                external_reference=external,
                idempotency_key=external,
                created_by=actor_id,
            )
            session.add(reference)
            session.flush()
        existing = session.scalar(
            select(RepositoryFileVersion).where(
                RepositoryFileVersion.node_id == node.id,
                RepositoryFileVersion.object_id == stored.id,
            )
        )
        if existing is not None:
            return
        version = RepositoryFileVersion(
            node_id=node.id,
            version_number=(node.current_version_number or 0) + 1,
            object_id=stored.id,
            object_reference_id=reference.id,
            derived_from_version_id=base_version.id,
            created_by=actor_id,
        )
        session.add(version)
        session.flush()
        session.add(
            RepositorySourceLink(
                node_id=node.id,
                version_id=version.id,
                object_reference_id=reference.id,
                product=node.source_product,
                external_reference=external,
                source_type="onlyoffice_save",
                source_id=str(office.id),
                source_status="available",
                provenance={
                    "base_version_id": str(base_version.id),
                    "participants": [str(item) for item in (payload.get("users") or [])],
                },
            )
        )
        node.current_version_number = version.version_number
        node.revision += 1
        node.updated_by = actor_id
        node.updated_at = utc_now()
        session.add(
            RepositoryNodeRevision(
                node_id=node.id,
                revision=node.revision,
                action="version_added",
                actor_id=actor_id,
                snapshot=self.repository._snapshot(node),
            )
        )
        self.repository.queue_analysis(session, version)
