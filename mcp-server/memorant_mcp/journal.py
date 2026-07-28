"""Append-only, redacted Markdown journal storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .event_schema import Event, EventInput
from .hook_core import append_event_data, redact_secrets
from .naming import vault_root


def _event_dict(event: Event, path: str) -> dict[str, Any]:
    return {**event.model_dump(mode="json"), "path": path}


def append_event(event_input: EventInput) -> dict[str, Any]:
    """Create one immutable event file, deduplicated by canonical payload hash."""
    event = event_input.to_event()
    data = event.model_dump(mode="json")
    if not data.get("project_key"):
        data.pop("project_key", None)
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
    session_id: str | None = None, project: str | None = None
) -> list[dict[str, Any]]:
    root = Path(vault_root())
    referenced = _referenced_event_ids()
    events: list[dict[str, Any]] = []
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
        if project is not None and event.project != project:
            continue
        events.append(_event_dict(event, path.relative_to(root).as_posix()))
    return sorted(events, key=lambda item: (item["observed_at"], item["event_id"]))
