from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Query parameters for pagination."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class SortParams(BaseModel):
    """Query parameters for sorting."""

    sort_by: str = Field(default="created_at", description="Field to sort by")
    sort_order: str = Field(
        default="desc",
        pattern="^(asc|desc)$",
        description="Sort direction: asc or desc",
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class ErrorResponse(BaseModel):
    """Standard error response."""

    error_code: str
    detail: str
    correlation_id: str | None = None


class TimestampMixin(BaseModel):
    """Mixin for responses that include audit timestamps."""

    created_at: datetime
    updated_at: datetime
