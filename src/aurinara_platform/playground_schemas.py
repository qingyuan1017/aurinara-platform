"""Transport contracts for the shared Playground."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PRODUCTS = {"platform", "aurinara", "aurinara-edc"}


class AgentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None


class AgentAdminResponse(AgentSummary):
    product_codes: list[str]
    runtime_arn: str
    memory_id: str | None
    runtime_qualifier: str
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    product_codes: list[str] = Field(min_length=1)
    runtime_arn: str = Field(min_length=20, max_length=512)
    memory_id: str | None = Field(default=None, min_length=1, max_length=255)
    runtime_qualifier: str = Field(default="DEFAULT", min_length=1, max_length=100)
    enabled: bool = False

    @field_validator("product_codes")
    @classmethod
    def validate_products(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if not normalized or any(item not in PRODUCTS for item in normalized):
            raise ValueError("product_codes contains an unsupported product")
        return normalized

    @field_validator("runtime_arn")
    @classmethod
    def validate_runtime_arn(cls, value: str) -> str:
        parts = value.split(":", 5)
        if len(parts) != 6 or parts[2] != "bedrock-agentcore" or not parts[3]:
            raise ValueError("runtime_arn must be an AgentCore runtime ARN")
        return value


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    product_codes: list[str] | None = None
    runtime_arn: str | None = Field(default=None, min_length=20, max_length=512)
    memory_id: str | None = Field(default=None, min_length=1, max_length=255)
    runtime_qualifier: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None

    @field_validator("product_codes")
    @classmethod
    def validate_products(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return AgentCreate.validate_products(value)

    @field_validator("runtime_arn")
    @classmethod
    def validate_runtime_arn(cls, value: str | None) -> str | None:
        return AgentCreate.validate_runtime_arn(value) if value is not None else value


class ConversationCreate(BaseModel):
    agent_id: UUID
    title: str | None = Field(default=None, max_length=160)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    title: str | None
    state: str
    pending_interrupt_id: str | None
    pending_tool_name: str | None
    created_at: datetime
    updated_at: datetime


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    state: Literal["active", "archived"] | None = None


class MessageResponse(BaseModel):
    id: str
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime


class MessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]
