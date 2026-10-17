"""Configuration safety and optional integration tests."""

import pytest
from pydantic import ValidationError

from aurinara_platform.config import Settings


def test_production_can_start_without_malware_scanner() -> None:
    settings = Settings(
        environment="production",
        object_store_access_key="",
        object_store_secret_key="",
        object_store_sse_algorithm="aws:kms",
        object_store_kms_key_id="test-key",
        bootstrap_first_user_as_admin=False,
    )

    assert settings.malware_scan_required is False
    assert settings.malware_scanner_url is None


def test_enabled_malware_scanning_requires_url() -> None:
    with pytest.raises(ValidationError, match="malware scanner URL is required"):
        Settings(environment="test", malware_scan_required=True)
