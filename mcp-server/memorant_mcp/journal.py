"""Append-only, redacted Markdown journal storage."""

from __future__ import annotations

import fcntl
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .event_schema import Event, EventInput
from .naming import resolve_safe_path, vault_root

MAX_EVIDENCE = 4096
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL | re.IGNORECASE,
)
_AUTH = re.compile(r"(?i)\bAuthorization\s*:\s*(?:Bearer\s+)?[^\s,;]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_CREDENTIAL = re.compile(
    r"(?i)\b([A-Za-z0-9_-]*(?:secret|token|password|passwd|pwd|api[_-]?key"
    r"|access[_-]?key|client[_-]?secret|private[_-]?key|auth)[A-Za-z0-9_-]*)"
    r"(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://[^:/\s]+:)[^@\s/]+(@)")
_UNSAFE_STRUCTURED = re.compile(
    r'(?is)[{"\']\s*(env|environment|tool_params|tool_input|params)\s*["\']?\s*:'
)
_TRANSCRIPT_ROLE = re.compile(r'(?i)["\']role["\']\s*:')
_TRANSCRIPT_CONTENT = re.compile(r'(?i)["\']content["\']\s*:')
_ENV_LINE = re.compile(r"(?m)^[A-Z][A-Z0-9_]{1,63}=.*$")


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""
    if (
        _UNSAFE_STRUCTURED.search(text)
        or (_TRANSCRIPT_ROLE.search(text) and _TRANSCRIPT_CONTENT.search(text))
        or len(_ENV_LINE.findall(text)) >= 3
    ):
        return "[CONTENT OMITTED: unsafe structured payload]"
    text = _PRIVATE_KEY.sub("[REDACTED]", text)
    text = _AUTH.sub("Authorization: [REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]\2", text)
    return _CREDENTIAL.sub(r"\1\2[REDACTED]", text)


def _bounded_evidence(text: str | None) -> str:
    safe = redact_secrets(text)
    if len(safe) <= MAX_EVIDENCE:
        return safe
    return safe[: MAX_EVIDENCE - 16] + "\n… [truncated]"


def _event_dict(event: Event, path: str) -> dict[str, Any]:
    return {**event.model_dump(mode="json"), "path": path}


def _existing_by_hash(payload_hash: str) -> dict[str, Any] | None:
    root = Path(vault_root())
    for path in sorted((root / "journal").glob("**/*.md")):
        try:
            post = frontmatter.load(path)
            if post.get("payload_hash") != payload_hash:
                continue
            event = Event(**dict(post.metadata))
        except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            continue
        return _event_dict(event, path.relative_to(root).as_posix())
    return None


def append_event(event_input: EventInput) -> dict[str, Any]:
    """Create one immutable event file, deduplicated by canonical payload hash."""
    event = event_input.to_event()
    root = Path(vault_root())
    lock_path = root / "journal" / ".append.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = _existing_by_hash(event.payload_hash)
            if existing:
                return existing
            observed = event.observed_at
            rel = (
                f"journal/{observed:%Y/%m/%d}/"
                f"{observed:%Y%m%dT%H%M%S%fZ}-{event.event_id}.md"
            )
            target = Path(resolve_safe_path(rel))
            target.parent.mkdir(parents=True, exist_ok=True)
            metadata = event.model_dump(mode="json")
            evidence = _bounded_evidence(event.evidence_excerpt)
            metadata["evidence_excerpt"] = evidence or None
            body = f"# {event.event_type.value}\n\n{evidence}\n" if evidence else f"# {event.event_type.value}\n"
            rendered = frontmatter.dumps(frontmatter.Post(body, **metadata))
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{event.event_id}-", suffix=".tmp", dir=target.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as temp:
                    temp.write(rendered)
                    temp.flush()
                    os.fsync(temp.fileno())
                for suffix in range(101):
                    if suffix:
                        candidate_rel = rel.removesuffix(".md") + f"-{suffix}.md"
                        target = Path(resolve_safe_path(candidate_rel))
                    else:
                        candidate_rel = rel
                    try:
                        os.link(temp_name, target)
                        rel = candidate_rel
                        break
                    except FileExistsError:
                        continue
                else:
                    raise FileExistsError("journal target collision limit exceeded")
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            stored = Event(**metadata)
            return _event_dict(stored, rel)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
        if session_id is not None and event.session_id != session_id:
            continue
        if project is not None and event.project != project:
            continue
        events.append(_event_dict(event, path.relative_to(root).as_posix()))
    return sorted(events, key=lambda item: (item["observed_at"], item["event_id"]))
