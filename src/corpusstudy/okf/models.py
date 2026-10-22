"""OKF v0.2 frontmatter as a tolerant consumer reads it.

Only `type` is required. Unknown keys are kept (`extra="allow"`), unknown types are
accepted, and every optional family may be missing (OKF section 11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

OKF_VERSION = "0.2"


class TrustTier(StrEnum):
    """OKF section 5.3, lowest to highest."""

    UNVERIFIED = "unverified"
    MACHINE_CONFIRMED = "machine-confirmed"
    HUMAN_REVIEWED = "human-reviewed"


class Status(StrEnum):
    DRAFT = "draft"
    STABLE = "stable"
    DEPRECATED = "deprecated"


def actor_kind(actor: str) -> str:
    """'human' | 'process' | 'agent' | 'unknown' for the OKF actor convention (section 7)."""
    if actor.startswith("human:"):
        return "human"
    if actor.startswith("process:"):
        return "process"
    if "/" in actor and not actor.startswith("/"):
        return "agent"
    return "unknown"


class Stamp(BaseModel):
    """`generated` or one `verified` event: `{ by, at }`."""

    model_config = ConfigDict(extra="allow")

    by: str = Field(min_length=1)
    at: datetime | None = None


class SourceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    resource: str = Field(min_length=1)
    id: str | None = None
    title: str | None = None
    author: str | None = None
    usage_count: int | None = None
    last_modified: datetime | None = None


class OkfFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str = Status.STABLE  # absent => stable (section 5.4)
    generated: Stamp | None = None
    verified: list[Stamp] = Field(default_factory=list)
    sources: list[SourceEntry] = Field(default_factory=list)
    usage_window: dict[str, Any] | None = None
    stale_after: datetime | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _type_non_empty(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            raise ValueError("`type` must be a non-empty string")
        return str(v).strip()

    @field_validator("verified", mode="before")
    @classmethod
    def _bare_mapping(cls, v: Any) -> Any:
        # A single verifier may be written as one mapping (section 5.2).
        return [v] if isinstance(v, dict) else v

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_list(cls, v: Any) -> Any:
        if v is None:
            return []
        return [v] if isinstance(v, str) else v

    @property
    def extra(self) -> dict[str, Any]:
        """Producer-defined keys beyond the OKF families."""
        return dict(self.model_extra or {})

    @property
    def trust_tier(self) -> TrustTier:
        if not self.verified:
            return TrustTier.UNVERIFIED
        if any(actor_kind(v.by) == "human" for v in self.verified):
            return TrustTier.HUMAN_REVIEWED
        return TrustTier.MACHINE_CONFIRMED

    @property
    def lifecycle(self) -> Status | None:
        """The status, or None for a value outside draft/stable/deprecated."""
        try:
            return Status(self.status)
        except ValueError:
            return None

    @property
    def last_verified(self) -> datetime | None:
        times = [v.at for v in self.verified if v.at is not None]
        return max(times) if times else None

    def is_stale(self, now: datetime | None = None) -> bool:
        if self.stale_after is None:
            return False
        return (now or datetime.now(UTC)) >= self.stale_after
