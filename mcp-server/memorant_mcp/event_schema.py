"""Strict schemas for append-only Memorant journal events."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    session_start = "session.start"
    session_end = "session.end"
    context_precompact = "context.precompact"
    tool_failure = "tool.failure"
    test_failure = "test.failure"
    test_success = "test.success"
    git_commit = "git.commit"


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: EventType
    session_id: str = Field(min_length=1, max_length=256)
    project: str = Field(default="unknown", min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    tool_name: str | None = Field(default=None, max_length=128)
    outcome: str | None = Field(default=None, max_length=64)
    evidence_excerpt: str | None = Field(default=None, max_length=20_000)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=32
    )

    def canonical_payload(self) -> bytes:
        data = self.model_dump(mode="json", exclude_none=False)
        return json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    def to_event(self) -> "Event":
        return Event(
            event_id=uuid.uuid4().hex,
            observed_at=datetime.now(timezone.utc),
            payload_hash=hashlib.sha256(self.canonical_payload()).hexdigest(),
            **self.model_dump(),
        )


class Event(EventInput):
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    observed_at: datetime
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
