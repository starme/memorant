"""Schemas for Memorant Memory Envelopes (A/B trust model)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TrustTier(str, Enum):
    verified = "verified"  # A
    provisional = "provisional"  # B


class MemoryKind(str, Enum):
    episodic = "episodic"
    procedural = "procedural"
    semantic = "semantic"
    decision = "decision"
    preference = "preference"


class LifecycleState(str, Enum):
    observed = "observed"
    candidate = "candidate"
    active = "active"
    reinforced = "reinforced"
    corrected = "corrected"
    superseded = "superseded"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=256)
    excerpt: str = Field(min_length=1, max_length=4096)
    observed_at: datetime | None = None
    session_id: str | None = Field(default=None, max_length=256)


class MemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project: str | None = Field(default=None, max_length=256)
    stack: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=16
    )
    version: str | None = Field(default=None, max_length=64)


class MemoryEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    memory_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind
    trust_tier: TrustTier
    lifecycle: LifecycleState = LifecycleState.active
    confidence: float = Field(ge=0.0, le=1.0)
    scope: MemoryScope = Field(default_factory=MemoryScope)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=32)
    source_event_ids: list[Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]] = Field(
        default_factory=list, max_length=64
    )
    independent_success_keys: list[Annotated[str, Field(min_length=1, max_length=256)]] = (
        Field(default_factory=list, max_length=64)
    )
    related: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32
    )
    supersedes: str | None = Field(default=None, max_length=256)
    created_at: datetime
    updated_at: datetime
    recall_count: int = Field(default=0, ge=0)
    last_recalled_at: datetime | None = None
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_session_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32
    )
    legacy_path: str | None = Field(default=None, max_length=512)

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        return round(float(value), 4)

    @model_validator(mode="after")
    def _trust_rules(self) -> "MemoryEnvelope":
        if self.trust_tier == TrustTier.verified:
            if self.lifecycle not in {
                LifecycleState.active,
                LifecycleState.reinforced,
                LifecycleState.corrected,
                LifecycleState.superseded,
            }:
                raise ValueError("verified memory requires active/reinforced lifecycle")
            if not self.evidence and not self.legacy_path:
                raise ValueError("verified memory requires evidence")
        if self.trust_tier == TrustTier.provisional and self.lifecycle in {
            LifecycleState.reinforced,
        }:
            raise ValueError("provisional memory cannot be reinforced before promotion")
        return self


class MemoryWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind
    trust_tier: TrustTier
    lifecycle: LifecycleState = LifecycleState.active
    confidence: float = Field(ge=0.0, le=1.0)
    scope: MemoryScope = Field(default_factory=MemoryScope)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=32)
    source_event_ids: list[Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]] = Field(
        default_factory=list, max_length=64
    )
    related: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32
    )
    supersedes: str | None = Field(default=None, max_length=256)
    origin_session_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32
    )
    body: str | None = Field(default=None, max_length=20_000)

    def fingerprint(self) -> str:
        payload = {
            "title": self.title,
            "claim": self.claim,
            "kind": self.kind.value,
            "scope": self.scope.model_dump(mode="json"),
            "source_event_ids": sorted(self.source_event_ids),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    def to_envelope(self) -> MemoryEnvelope:
        now = datetime.now(timezone.utc)
        sessions = list(self.origin_session_ids)
        for item in self.evidence:
            if item.session_id and item.session_id not in sessions:
                sessions.append(item.session_id)
        return MemoryEnvelope(
            memory_id=uuid.uuid4().hex,
            title=self.title,
            claim=self.claim,
            kind=self.kind,
            trust_tier=self.trust_tier,
            lifecycle=self.lifecycle,
            confidence=self.confidence,
            scope=self.scope,
            evidence=self.evidence,
            source_event_ids=self.source_event_ids,
            related=self.related,
            supersedes=self.supersedes,
            created_at=now,
            updated_at=now,
            source_fingerprint=self.fingerprint(),
            origin_session_ids=sessions,
        )


def envelope_to_frontmatter(envelope: MemoryEnvelope) -> dict[str, Any]:
    data = envelope.model_dump(mode="json", exclude_none=True)
    data["type"] = "memory"
    return data
