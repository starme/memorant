"""Strict schemas for append-only Memorant journal events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .hook_core import bounded_evidence, semantic_payload_hash


class EventType(str, Enum):
    session_start = "session.start"
    session_end = "session.end"
    context_precompact = "context.precompact"
    tool_failure = "tool.failure"
    test_failure = "test.failure"
    test_success = "test.success"
    git_commit = "git.commit"
    # Non-dev proactive capture (backlog P2): doc/decision milestones trigger
    # Distill via an observer command, not a Bash hook — same journal→Distill
    # chain, different entry. "换 Observer，不换等人吩咐".
    doc_commit = "doc.commit"
    decision_adopt = "decision.adopt"


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: EventType
    session_id: str = Field(min_length=1, max_length=256)
    project: str = Field(default="unknown", min_length=1, max_length=256)
    project_key: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{16,64}$", max_length=64
    )
    source: str = Field(min_length=1, max_length=128)
    tool_name: str | None = Field(default=None, max_length=128)
    outcome: str | None = Field(default=None, max_length=64)
    evidence_excerpt: str | None = Field(default=None, max_length=20_000)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=32
    )

    def canonical_payload(self) -> bytes:
        data = self.model_dump(mode="json", exclude_none=False)
        data["evidence_excerpt"] = bounded_evidence(data.get("evidence_excerpt")) or None
        # Match semantic_payload_hash field set / encoding exactly.
        payload = {
            "event_type": data.get("event_type"),
            "session_id": data.get("session_id"),
            "project": data.get("project"),
            "source": data.get("source"),
            "tool_name": data.get("tool_name"),
            "outcome": data.get("outcome"),
            "evidence_excerpt": data.get("evidence_excerpt"),
            "tags": data.get("tags") or [],
        }
        if data.get("project_key"):
            payload["project_key"] = data.get("project_key")
        import json

        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    def to_event(self) -> Event:
        data = self.model_dump(mode="json")
        data["evidence_excerpt"] = bounded_evidence(data.get("evidence_excerpt")) or None
        return Event(
            event_id=uuid.uuid4().hex,
            observed_at=datetime.now(timezone.utc),
            payload_hash=semantic_payload_hash(data),
            **data,
        )


class Event(EventInput):
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    observed_at: datetime
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
