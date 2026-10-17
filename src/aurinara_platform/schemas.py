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


class StudyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    display_name: str = Field(min_length=1, max_length=500)


class StudyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["active", "archived"] | None = None


class StudyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    display_name: str
    status: str
    revision: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class MembershipReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: list[UUID] = Field(min_length=1)
    site_id: UUID | None = None


class MembershipResponse(BaseModel):
    id: UUID
    study_id: UUID
    user_id: UUID
    role_id: UUID
    role_name: str
    product: str
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


class ObjectReferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=64)
    external_reference: str = Field(min_length=1, max_length=255)


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
