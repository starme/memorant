"""Append-only, redacted Markdown journal storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .event_schema import Event, EventInput
from .hook_core import (  # noqa: F401 - re-export for tests
    append_event_data,
    redact_secrets,
)
from .naming import vault_root


def _event_dict(event: Event, path: str) -> dict[str, Any]:
    return {**event.model_dump(mode="json"), "path": path}


def append_event(event_input: EventInput) -> dict[str, Any]:
    """Create one immutable event file, deduplicated by canonical payload hash."""
    event = event_input.to_event()
    data = event.model_dump(mode="json")
    # Empty optionals are dropped so the stored event and the caller's event
    # describe the same field set; otherwise an idempotent append would return
    # a different shape than the original call.
    for optional in ("project_key", "project_source"):
        if not data.get(optional):
            data.pop(optional, None)
    return append_event_data(data, root=vault_root())


def _referenced_event_ids() -> set[str]:
    root = Path(vault_root())
    result: set[str] = set()
    for path in (root / "memories").glob("*.md"):
        try:
            values = frontmatter.load(path).get("source_event_ids", [])
        except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            continue
        if isinstance(values, list):
            result.update(str(value) for value in values)
    return result


def list_pending_events(
    session_id: str | None = None,
    project: str | None = None,
    project_filter: str | None = None,
) -> list[dict[str, Any]]:
    """List unreferenced journal events.

    ``project`` is a legacy alias that still hard-filters when supplied, so
    existing callers keep their behaviour. ``project_filter`` makes that intent
    explicit. With neither set the queue is global: project is provenance
    metadata, not a partition — filtering here used to hide half of a user's
    events whenever the workspace derived two project labels.
    """
    root = Path(vault_root())
    referenced = _referenced_event_ids()
    events: list[dict[str, Any]] = []
    active_filter = project_filter or project
    for path in sorted((root / "journal").glob("**/*.md")):
        try:
            event = Event(**dict(frontmatter.load(path).metadata))
        except (
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            yaml.YAMLError,
        ):
            continue
        if event.event_id in referenced:
            continue
        # session.start is a session-boundary marker, not a Distill candidate:
        # no migratable form, pure noise. Keep in journal (audit), skip from pending.
        if event.event_type == "session.start":
            continue
        if session_id is not None and event.session_id != session_id:
            continue
        if active_filter is not None and event.project != active_filter:
            continue
        events.append(_event_dict(event, path.relative_to(root).as_posix()))
    return sorted(events, key=lambda item: (item["observed_at"], item["event_id"]))
