from pydantic import SecretStr

from aurinara_platform.config import Settings
from aurinara_platform.storage import S3ObjectStorage


def test_s3_storage_uses_task_role_chain_when_static_credentials_are_empty(monkeypatch):
    captured: dict[str, object] = {}

    def fake_client(service: str, **kwargs: object) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("aurinara_platform.storage.boto3.client", fake_client)
    settings = Settings(
        object_store_endpoint="",
        object_store_access_key=SecretStr(""),
        object_store_secret_key=SecretStr(""),
        object_store_path_style=False,
    )

    S3ObjectStorage(settings)

    assert captured["service"] == "s3"
    assert captured["endpoint_url"] is None
    assert "aws_access_key_id" not in captured
    assert "aws_secret_access_key" not in captured
