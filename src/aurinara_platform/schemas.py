"""Versioned public transport contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    issuer: str
    subject: str
    email: str | None
    display_name: str | None
    status: str
    revision: int


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=255)


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=320)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "inactive"] | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    product: str
    name: str
    description: str | None
    scope_level: Literal["system", "study", "site"]
    assignable: bool
    built_in: bool
    permission_policy: Literal["explicit", "all", "all_scoped", "read_scoped"]
    status: Literal["active", "inactive"]
    source_role_id: UUID | None
    revision: int
    permission_codes: list[str]
    updated_at: datetime


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    product: str
    description: str | None
    module: str | None
    allowed_scope_levels: list[Literal["system", "study", "site"]]
    status: Literal["active", "inactive"]


class ProductPermissionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    description: str = Field(min_length=1, max_length=2000)
    module: str = Field(min_length=1, max_length=64)
    allowed_scope_levels: list[Literal["system", "study", "site"]] = Field(min_length=1)


class ProductPermissionSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[ProductPermissionDefinition] = Field(min_length=1, max_length=1000)


class PlatformRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,159}$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    scope_level: Literal["system", "study", "site"]
    permission_codes: list[str] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=3, max_length=2000)


class PlatformRolePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    scope_level: Literal["system", "study", "site"] | None = None
    permission_codes: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    status: Literal["active", "inactive"] | None = None
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def contains_change(self) -> "PlatformRolePatch":
        if not self.model_fields_set.difference({"reason"}):
            raise ValueError("At least one role field must be changed")
        return self


class RoleChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role_id: UUID
    actor_id: UUID
    action: str
    reason: str
    previous_value: dict[str, object] | None
    new_value: dict[str, object]
    policy_version: int
    created_at: datetime


class SystemAssignmentReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    role_ids: list[UUID]


class SystemAssignmentResponse(BaseModel):
    id: UUID
    user_id: UUID
    role_id: UUID
    role_name: str
    product: str
    assigned_at: datetime


class StudyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    display_name: str = Field(min_length=1, max_length=500)
    sponsor: str = Field(min_length=1, max_length=255)


class StudyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=500)
    sponsor: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "archived"] | None = None


class StudyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    display_name: str
    sponsor: str | None
    status: str
    revision: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class SiteSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    status: Literal["active", "inactive"] = "active"


class SiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    study_id: UUID
    name: str
    country: str | None
    state: str | None
    city: str | None
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime


class MembershipReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    role_ids: list[UUID] = Field(min_length=1)
    site_id: UUID | None = None


class MembershipResponse(BaseModel):
    id: UUID
    study_id: UUID
    user_id: UUID
    role_id: UUID
    role_name: str
    product: str
    user_display_name: str | None
    user_email: str | None
    site_id: UUID | None
    assigned_at: datetime


class AuthorizationPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=255)


class AuthorizationResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=100)
    id: str | None = Field(default=None, max_length=255)
    study_id: UUID | None = None
    site_id: UUID | None = None

    @model_validator(mode="after")
    def site_requires_study(self) -> "AuthorizationResource":
        if self.site_id is not None and self.study_id is None:
            raise ValueError("site_id requires study_id")
        return self


class AuthorizationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: AuthorizationPrincipal
    action: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    resource: AuthorizationResource


class AuthorizationDecisionResponse(BaseModel):
    allowed: bool
    decision_id: UUID
    policy_version: int
    expires_at: datetime
    reason_code: str


class AuthorizationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[AuthorizationDecisionRequest] = Field(min_length=1, max_length=100)


class AuthorizationBatchResponse(BaseModel):
    decisions: list[AuthorizationDecisionResponse]


class UploadSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_id: UUID
    owner_product: str = Field(min_length=1, max_length=64)
    authorization_action: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    classification: str = Field(min_length=1, max_length=64)
    media_type: str = Field(min_length=1, max_length=255)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_size_bytes: int = Field(gt=0)
    part_count: int = Field(ge=1, le=10_000)
    retention_until: datetime | None = None


class UploadPart(BaseModel):
    part_number: int
    url: str


class UploadSessionResponse(BaseModel):
    object_id: UUID
    upload_id: str
    expires_at: datetime
    parts: list[UploadPart]


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=256)


class UploadFinalize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[CompletedPart] = Field(min_length=1, max_length=10_000)


class ObjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    study_id: UUID
    owner_product: str
    classification: str
    sha256: str | None
    size_bytes: int | None
    media_type: str
    status: str
    retention_until: datetime | None
    retention_state: str
    created_by: UUID
    created_at: datetime
    finalized_at: datetime | None


class ObjectDownloadAuthorize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_action: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")


class ObjectReferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    authorization_action: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    external_reference: str = Field(min_length=1, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=255)
    repository: "RepositoryPublication | None" = None


class ObjectReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    object_id: UUID
    product: str
    external_reference: str
    created_by: UUID
    created_at: datetime
    released_at: datetime | None


class DownloadResponse(BaseModel):
    url: str
    expires_at: datetime


class RepositoryAccessEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_action: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    write_action: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,159}$")
    site_id: UUID | None = None


class RepositoryPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None
    source_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=255)
    source_status: str = Field(min_length=1, max_length=64)
    module: str | None = Field(default=None, max_length=64)
    solution: str | None = Field(default=None, min_length=1, max_length=100)
    solution_label: str | None = Field(default=None, min_length=1, max_length=255)
    purpose: str | None = Field(default=None, min_length=1, max_length=100)
    classification: str = Field(min_length=1, max_length=64)
    access: RepositoryAccessEnvelope
    retention_metadata: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def contains_complete_taxonomy(self) -> "RepositoryPublication":
        if (self.solution is None) != (self.purpose is None):
            raise ValueError("solution and purpose must be declared together")
        if self.solution_label is not None and self.solution is None:
            raise ValueError("solution_label requires solution")
        return self


class RepositoryFolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None
    classification: str = Field(default="internal", min_length=1, max_length=64)
    access: RepositoryAccessEnvelope | None = None


class RepositoryNodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: UUID | None = None
    source_status: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def contains_repository_change(self) -> "RepositoryNodePatch":
        if not self.model_fields_set:
            raise ValueError("At least one Repository field must be changed")
        return self


class RepositoryStatusPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_status: str = Field(min_length=1, max_length=64)
    external_reference: str = Field(min_length=1, max_length=255)


class RepositoryVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: UUID
    object_reference_id: UUID
    product: str = Field(min_length=1, max_length=64)
    external_reference: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=255)
    source_status: str = Field(min_length=1, max_length=64)
    provenance: dict[str, object] = Field(default_factory=dict)
    derived_from_version_id: UUID | None = None


class RepositoryCapabilities(BaseModel):
    can_preview: bool = False
    can_edit: bool = False
    can_annotate: bool = False
    can_manage: bool = False
    conversion_targets: list[Literal["csv", "parquet"]] = Field(default_factory=list)


class RepositoryCollectionCapabilities(BaseModel):
    can_upload: bool = False
    can_create_folder: bool = False


class RepositoryVersionProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_kind: str
    detected_media_type: str
    processing_status: Literal["pending", "ready", "failed", "unsupported"]
    metadata_json: dict[str, object]
    analyzer_version: str
    failure_code: str | None


class RepositoryNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    study_id: UUID
    parent_id: UUID | None
    kind: Literal["folder", "file"]
    display_name: str
    source_product: str
    module: str | None
    classification: str
    required_read_action: str
    required_write_action: str
    site_id: UUID | None
    source_resource_type: str
    source_resource_id: str | None
    source_status: str
    retention_metadata: dict[str, object]
    lifecycle_status: Literal["active", "archived"]
    revision: int
    current_version_number: int | None
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    current_version: "RepositoryVersionResponse | None" = None
    capabilities: RepositoryCapabilities = Field(default_factory=RepositoryCapabilities)


class RepositoryNodePage(BaseModel):
    items: list[RepositoryNodeResponse]
    next_cursor: str | None = None
    capabilities: RepositoryCollectionCapabilities = Field(
        default_factory=RepositoryCollectionCapabilities
    )


class RepositoryVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_id: UUID
    version_number: int
    object_id: UUID
    object_reference_id: UUID | None
    sha256: str
    size_bytes: int
    media_type: str
    derived_from_version_id: UUID | None = None
    profile: RepositoryVersionProfileResponse | None = None
    capabilities: RepositoryCapabilities = Field(default_factory=RepositoryCapabilities)
    created_by: UUID
    created_at: datetime


class RepositoryVersionBindingResponse(RepositoryVersionResponse):
    study_id: UUID
    filename: str
    classification: str
    source_product: str
    source_status: str
    site_id: UUID | None


class RepositoryUploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=255)
    part_count: int = Field(ge=1, le=10_000)
    parent_id: UUID | None = None
    replacement_node_id: UUID | None = None
    classification: str = Field(default="internal", min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @model_validator(mode="after")
    def destination_is_unambiguous(self) -> "RepositoryUploadCreate":
        if self.parent_id is not None and self.replacement_node_id is not None:
            raise ValueError("parent_id and replacement_node_id are mutually exclusive")
        return self


class RepositoryUploadComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[CompletedPart] = Field(default_factory=list, max_length=10_000)


class RepositoryUploadResponse(UploadSessionResponse):
    filename: str
    replacement_node_id: UUID | None = None
    already_available: bool = False


class TabularFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=255)
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "contains", "is_null"]
    value: str | int | float | bool | None = None


class TabularSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=255)
    direction: Literal["asc", "desc"] = "asc"


class CsvParsingOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delimiter: Literal[",", ";", "\t", "|"] | None = None
    encoding: Literal["utf-8", "utf-8-sig", "windows-1252"] | None = None
    has_header: bool | None = None


class TabularPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=500)
    columns: list[str] = Field(default_factory=list, max_length=100)
    filters: list[TabularFilter] = Field(default_factory=list, max_length=10)
    sort: list[TabularSort] = Field(default_factory=list, max_length=3)
    csv: CsvParsingOverride | None = None


class TabularColumn(BaseModel):
    name: str
    type: str


class TabularPreviewResponse(BaseModel):
    processing_status: str
    schema_: list[TabularColumn] = Field(alias="schema", serialization_alias="schema")
    rows: list[dict[str, object | None]]
    total_rows: int
    page: int
    page_size: int
    parsing: dict[str, object]


class RepositoryConversionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["csv", "parquet"]
    filename: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=255)


class RepositoryExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=255)


class RepositoryJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    version_id: UUID
    state: str
    attempts: int
    result: dict[str, object]
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class PdfAnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    geometry: dict[str, object]
    selected_text: str | None = Field(default=None, max_length=20_000)
    comment: str | None = Field(default=None, max_length=20_000)
    category: str = Field(default="comment", min_length=1, max_length=64)
    status: str = Field(default="open", min_length=1, max_length=32)


class PdfAnnotationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry: dict[str, object] | None = None
    selected_text: str | None = Field(default=None, max_length=20_000)
    comment: str | None = Field(default=None, max_length=20_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    status: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def contains_change(self) -> "PdfAnnotationPatch":
        if not self.model_fields_set:
            raise ValueError("At least one annotation field must be changed")
        return self


class PdfAnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_id: UUID
    page_number: int
    geometry: dict[str, object]
    selected_text: str | None
    comment: str | None
    category: str
    status: str
    revision: int
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class OfficeSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["view", "edit"] = "view"


class OfficeSessionResponse(BaseModel):
    session_id: UUID
    document_server_url: str
    config: dict[str, object]
