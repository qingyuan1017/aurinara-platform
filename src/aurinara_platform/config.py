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
    # Service clients must belong to the same pool as oidc_issuer.
    oidc_service_client_ids: str = ""
    oidc_service_scope: str = "https://aurinara-platform.local/service"
    oidc_service_audience: str | None = None
    oidc_clock_skew_seconds: int = 30
    playground_product_audiences: str = (
        "platform=aurinara-platform;"
        "aurinara=aurinara,clinical-artifact-platform;"
        "aurinara-edc=aurinara-edc,aurinara-edc-api,clinical-edc"
    )
    playground_runtime_timeout_seconds: int = 600

    workspace_enabled: bool = False
    workspace_runtime_mode: Literal["disabled", "static", "ecs"] = "static"
    workspace_product_audiences: str = (
        "platform=aurinara-platform;"
        "aurinara=aurinara,clinical-artifact-platform;"
        "aurinara-edc=aurinara-edc,aurinara-edc-api,clinical-edc"
    )
    workspace_public_base_url: str = "http://localhost:8000"
    workspace_callback_base_url: str = "http://host.docker.internal:8000"
    workspace_static_targets: str = (
        "platform=http://127.0.0.1:18080;"
        "aurinara=http://127.0.0.1:18081;"
        "aurinara-edc=http://127.0.0.1:18082"
    )
    workspace_cluster: str = ""
    workspace_task_definition: str = ""
    workspace_task_family: str = "aurinara-platform-workspace"
    workspace_subnet_ids: str = ""
    workspace_security_group_id: str = ""
    workspace_bucket: str = "aurinara-platform-workspaces"
    workspace_sync_role_arn: str = ""
    workspace_region: str = "us-east-1"
    workspace_session_seconds: int = 2 * 60 * 60
    workspace_sync_credential_seconds: int = 2 * 60 * 60 + 15 * 60
    workspace_launch_grant_seconds: int = 60
    workspace_proxy_cache_seconds: int = 15
    workspace_max_running: int = 10
    workspace_quota_bytes: int = 5 * 1024 * 1024 * 1024
    workspace_sync_interval_seconds: int = 60
    workspace_callback_secret: SecretStr = SecretStr("local-workspace-callback-secret")

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
    identity_registration_queue_url: str | None = None
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175"

    repository_rich_preview_enabled: bool = True
    repository_conversion_enabled: bool = True
    repository_pdf_annotations_enabled: bool = True
    repository_office_enabled: bool = False
    repository_tabular_max_bytes: int = 1024 * 1024 * 1024
    repository_worker_memory_limit: str = "512MB"
    repository_worker_threads: int = 2
    repository_query_timeout_seconds: int = 20
    onlyoffice_public_url: str = "http://localhost:8083"
    onlyoffice_internal_url: str = "http://localhost:8083"
    onlyoffice_platform_url: str = "http://host.docker.internal:8000"
    onlyoffice_jwt_secret: SecretStr = SecretStr("local-onlyoffice-change-me")
    onlyoffice_image: str = (
        "onlyoffice/documentserver:9.3.1.2@"
        "sha256:290a0a1406a485acc8a6bc20418ac7158c5eec78b4844c218f8f975a9800ddbc"
    )
    onlyoffice_session_ttl_seconds: int = 6 * 60 * 60
    onlyoffice_max_output_bytes: int = 250 * 1024 * 1024

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

    @property
    def playground_audience_products(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for group in self.playground_product_audiences.split(";"):
            if not group.strip():
                continue
            product, separator, audiences = group.partition("=")
            if not separator or not product.strip():
                raise ValueError("playground product audiences must use product=audience,...")
            for audience in audiences.split(","):
                if audience.strip():
                    mapping[audience.strip()] = product.strip()
        return mapping

    @property
    def workspace_audience_products(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for group in self.workspace_product_audiences.split(";"):
            if not group.strip():
                continue
            product, separator, audiences = group.partition("=")
            if not separator or not product.strip():
                raise ValueError("workspace product audiences must use product=audience,...")
            for audience in audiences.split(","):
                if audience.strip():
                    mapping[audience.strip()] = product.strip()
        return mapping

    @property
    def workspace_subnet_id_list(self) -> list[str]:
        return [item.strip() for item in self.workspace_subnet_ids.split(",") if item.strip()]

    @property
    def workspace_static_target_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for group in self.workspace_static_targets.split(";"):
            if not group.strip():
                continue
            product, separator, target = group.partition("=")
            if not separator or not product.strip() or not target.strip():
                raise ValueError("workspace static targets must use product=url;...")
            mapping[product.strip()] = target.strip().rstrip("/")
        return mapping

    @field_validator(
        "authorization_decision_ttl_seconds",
        "object_presign_ttl_seconds",
        "object_max_parts",
        "oidc_clock_skew_seconds",
        "repository_worker_threads",
        "repository_query_timeout_seconds",
        "onlyoffice_session_ttl_seconds",
        "onlyoffice_max_output_bytes",
        "playground_runtime_timeout_seconds",
        "workspace_session_seconds",
        "workspace_sync_credential_seconds",
        "workspace_launch_grant_seconds",
        "workspace_proxy_cache_seconds",
        "workspace_max_running",
        "workspace_quota_bytes",
        "workspace_sync_interval_seconds",
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
        if self.malware_scan_required and not self.malware_scanner_url:
            raise ValueError("a malware scanner URL is required when malware scanning is enabled")
        if self.environment == "production":
            if self.bootstrap_first_user_as_admin:
                raise ValueError("first-user bootstrap is forbidden in production")
            if self.object_store_access_key.get_secret_value() == "minioadmin":
                raise ValueError("default object-store credentials are forbidden in production")
            if self.object_store_secret_key.get_secret_value() == "minioadmin":
                raise ValueError("default object-store credentials are forbidden in production")
            if self.object_store_sse_algorithm == "none":
                raise ValueError("object-store encryption is required in production")
            if self.object_store_sse_algorithm == "aws:kms" and not self.object_store_kms_key_id:
                raise ValueError("an object-store KMS key ID is required for aws:kms encryption")
            if (
                self.repository_office_enabled
                and self.onlyoffice_jwt_secret.get_secret_value() == "local-onlyoffice-change-me"
            ):
                raise ValueError("the default ONLYOFFICE JWT secret is forbidden in production")
        if self.workspace_enabled and self.environment in {"staging", "production"}:
            if self.workspace_runtime_mode != "ecs":
                raise ValueError("ECS workspace runtime is required outside local development")
            required = {
                "workspace_cluster": self.workspace_cluster,
                "workspace_task_definition": self.workspace_task_definition,
                "workspace_subnet_ids": self.workspace_subnet_ids,
                "workspace_security_group_id": self.workspace_security_group_id,
                "workspace_bucket": self.workspace_bucket,
                "workspace_sync_role_arn": self.workspace_sync_role_arn,
            }
            missing = sorted(name for name, value in required.items() if not value)
            if missing:
                raise ValueError(f"workspace ECS settings are missing: {', '.join(missing)}")
            if (
                self.workspace_callback_secret.get_secret_value()
                == "local-workspace-callback-secret"
            ):
                raise ValueError("the default workspace callback secret is forbidden")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
