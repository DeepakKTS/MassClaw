from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.base import AgentStatus


class AgentCreate(BaseModel):
    """Request schema for registering a new agent."""

    name: str = Field(..., min_length=3, max_length=255, description="Unique agent name")
    description: str = Field(..., min_length=10, max_length=5000, description="Agent description")
    capabilities: list[str] = Field(..., min_length=1, description="List of capabilities")
    endpoint: str = Field(..., max_length=2048, description="Agent endpoint URL or handler ID")
    supported_tools: list[str] = Field(default_factory=list, description="Tools this agent can use")
    cost_profile: dict = Field(
        default_factory=dict,
        description="Cost profile: e.g. {'avg_cost_per_call': 0.01, 'model': 'claude-sonnet'}",
    )
    latency_profile: dict = Field(
        default_factory=dict,
        description="Latency profile: e.g. {'p50_ms': 500, 'p95_ms': 2000, 'p99_ms': 5000}",
    )
    version: str = Field(default="1.0.0", max_length=50)
    safety_level: int = Field(default=1, ge=1, le=10)
    input_schema: dict | None = Field(default=None, description="JSON Schema for agent input")
    output_schema: dict | None = Field(default=None, description="JSON Schema for agent output")
    health_check_url: str | None = Field(default=None, max_length=2048, description="URL for health checks")
    metadata: dict = Field(default_factory=dict)
    protocol_type: str | None = Field(default="http", description="Protocol type: http, mcp, or websocket")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not all(c.isalnum() or c in "-_ " for c in v):
            raise ValueError("Name must contain only alphanumeric characters, hyphens, underscores, or spaces")
        return v.strip()

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip().lower() for c in v if c.strip()]
        if not cleaned:
            raise ValueError("At least one capability is required")
        return cleaned


class AgentUpdate(BaseModel):
    """Request schema for updating an agent. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = Field(default=None, min_length=10, max_length=5000)
    capabilities: list[str] | None = None
    endpoint: str | None = Field(default=None, max_length=2048)
    supported_tools: list[str] | None = None
    cost_profile: dict | None = None
    latency_profile: dict | None = None
    version: str | None = Field(default=None, max_length=50)
    safety_level: int | None = Field(default=None, ge=1, le=10)
    input_schema: dict | None = None
    output_schema: dict | None = None
    status: AgentStatus | None = None
    health_check_url: str | None = Field(default=None, max_length=2048)
    metadata: dict | None = None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            cleaned = [c.strip().lower() for c in v if c.strip()]
            if not cleaned:
                raise ValueError("At least one capability is required")
            return cleaned
        return v


class AgentResponse(BaseModel):
    """Full agent response."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    name: str
    description: str
    capabilities: list
    endpoint: str
    supported_tools: list
    cost_profile: dict
    latency_profile: dict
    trust_score: float
    version: str
    safety_level: int
    input_schema: dict | None
    output_schema: dict | None
    status: AgentStatus
    health_check_url: str | None
    last_health_check: datetime | None
    metadata: dict = Field(alias="metadata_")
    protocol_type: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentSummary(BaseModel):
    """Lightweight agent representation for list views."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    name: str
    capabilities: list
    trust_score: float
    status: AgentStatus
    version: str
    safety_level: int
    protocol_type: str | None = None
    created_at: datetime


class AgentSearchParams(BaseModel):
    """Parameters for agent discovery search."""

    capabilities: list[str] | None = Field(default=None, description="Required capabilities")
    status: AgentStatus | None = Field(default=None, description="Filter by status")
    min_trust: float | None = Field(default=None, ge=0, le=1, description="Minimum trust score")
    max_cost: float | None = Field(default=None, ge=0, description="Maximum average cost per call")
    name_query: str | None = Field(default=None, max_length=255, description="Fuzzy name search")

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            return [c.strip().lower() for c in v if c.strip()]
        return v


class HealthCheckResponse(BaseModel):
    """Agent health check result."""

    agent_id: uuid.UUID
    healthy: bool
    status: AgentStatus
    latency_ms: float | None = None
    checked_at: datetime
    error: str | None = None
