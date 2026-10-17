"""Environment-backed control-plane configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration with production-safe validation."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: Literal["development", "test", "staging", "production"] = "development"
    api_prefix: str = "/v1"
    database_url: str = (
        "postgresql+psycopg://aurinara_platform:aurinara_platform@localhost:55433/aurinara_platform"
    )

    oidc_issuer: str = "https://local-issuer.example"
    oidc_audience: str = "aurinara-platform"
    oidc_user_audiences: str = (
        "aurinara-platform,aurinara,aurinara-edc,aurinara-edc-api,"
        "clinical-artifact-platform,clinical-edc"
    )
    oidc_jwks_url: str = "http://localhost:8081/.well-known/jwks.json"
    oidc_algorithms: str = "RS256"
    oidc_service_client_ids: str = ""
    oidc_service_scope: str = "https://aurinara-platform.local/service"
    oidc_service_audience: str | None = None
    oidc_clock_skew_seconds: int = 30

    authorization_decision_ttl_seconds: int = 30
    bootstrap_first_user_as_admin: bool = False

    object_store_endpoint: str = "http://localhost:9000"
    object_store_bucket: str = "aurinara-shared-objects"
    object_store_region: str = "us-east-1"
    object_store_access_key: SecretStr = SecretStr("minioadmin")
    object_store_secret_key: SecretStr = SecretStr("minioadmin")
    object_store_path_style: bool = True
    object_store_sse_algorithm: Literal["AES256", "aws:kms", "none"] = "AES256"
    object_store_kms_key_id: str | None = None
    object_lock_enabled: bool = False
    object_lock_mode: Literal["GOVERNANCE", "COMPLIANCE"] = "GOVERNANCE"
    object_presign_ttl_seconds: int = 300
    object_max_parts: int = 10_000
    object_max_bytes: int = 5 * 1024 * 1024 * 1024

    malware_scanner_url: str | None = None
    malware_scanner_token: SecretStr | None = None
    malware_scan_required: bool = False
    eventbridge_bus_name: str | None = None
    eventbridge_source: str = "aurinara.platform"
    eventbridge_endpoint_url: str | None = None
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175"

    @property
    def oidc_algorithm_list(self) -> list[str]:
        return [item.strip() for item in self.oidc_algorithms.split(",") if item.strip()]

    @property
    def oidc_service_client_id_set(self) -> set[str]:
        return {item.strip() for item in self.oidc_service_client_ids.split(",") if item.strip()}

    @property
    def oidc_user_audience_set(self) -> set[str]:
        values = {item.strip() for item in self.oidc_user_audiences.split(",") if item.strip()}
        values.add(self.oidc_audience)
        return values

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @field_validator(
        "authorization_decision_ttl_seconds",
        "object_presign_ttl_seconds",
        "object_max_parts",
        "oidc_clock_skew_seconds",
    )
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("oidc_clock_skew_seconds")
    @classmethod
    def validate_clock_skew(cls, value: int) -> int:
        if value > 300:
            raise ValueError("OIDC clock skew may not exceed five minutes")
        return value

    @field_validator("authorization_decision_ttl_seconds")
    @classmethod
    def validate_decision_ttl(cls, value: int) -> int:
        if value > 60:
            raise ValueError("authorization decisions may be cached for at most 60 seconds")
        return value

    @model_validator(mode="after")
    def reject_unsafe_production_settings(self) -> Self:
        if self.environment == "production":
            if self.bootstrap_first_user_as_admin:
                raise ValueError("first-user bootstrap is forbidden in production")
            if self.object_store_access_key.get_secret_value() == "minioadmin":
                raise ValueError("default object-store credentials are forbidden in production")
            if self.object_store_secret_key.get_secret_value() == "minioadmin":
                raise ValueError("default object-store credentials are forbidden in production")
            if self.object_store_sse_algorithm == "none":
                raise ValueError("object-store encryption is required in production")
            if not self.malware_scan_required or not self.malware_scanner_url:
                raise ValueError("a malware scanner is required in production")
            if self.object_store_sse_algorithm == "aws:kms" and not self.object_store_kms_key_id:
                raise ValueError("an object-store KMS key ID is required for aws:kms encryption")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
