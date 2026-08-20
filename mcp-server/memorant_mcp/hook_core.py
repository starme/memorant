"""Pure-stdlib event redaction and append-only journal writer."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_EVIDENCE = 4096

# Keep in sync with EventType (event_schema.py). hook_core runs under stdlib-only
# -S execution where relative imports may fail, so we don't derive at import time.
EVENT_TYPES: set[str] = {
    "session.start",
    "session.end",
    "context.precompact",
    "tool.failure",
    "test.failure",
    "test.success",
    "git.commit",
    "doc.commit",
    "decision.adopt",
}
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL | re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SENSITIVE_KEY = (
    r"(?:[A-Za-z0-9]+[_-])*(?:secret|token|password|passwd|pwd|api[_-]?key"
    r"|access[_-]?key|client[_-]?secret|private[_-]?key|auth)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
_CREDENTIAL = re.compile(
    rf"(?i)(?<![A-Za-z0-9_-])({_SENSITIVE_KEY})"
    r"(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://[^:/\s]+:)[^@\s/]+(@)")
_UNSAFE_STRUCTURED = re.compile(
    r'(?is)[{"\']\s*(env|environment|tool_params|tool_input|params)\s*["\']?\s*:'
)
_TRANSCRIPT_ROLE = re.compile(r'(?i)["\']role["\']\s*:')
_TRANSCRIPT_CONTENT = re.compile(r'(?i)["\']content["\']\s*:')
_ENV_LINE = re.compile(r"(?m)^[A-Z][A-Z0-9_]{1,63}=.*$")
_PAYLOAD_HASH = re.compile(r"(?m)^payload_hash:\s*['\"]?([0-9a-f]{64})")


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""
    if (
        _UNSAFE_STRUCTURED.search(text)
        or (_TRANSCRIPT_ROLE.search(text) and _TRANSCRIPT_CONTENT.search(text))
        or len(_ENV_LINE.findall(text)) >= 3
    ):
        return "[CONTENT OMITTED: unsafe structured payload]"
    text = _PRIVATE_KEY_BLOCK.sub("[REDACTED]", text)
    text = _AUTHORIZATION.sub("Authorization: [REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]\2", text)
    return _CREDENTIAL.sub(r"\1\2[REDACTED]", text)


def bounded_evidence(text: str | None) -> str:
    safe = redact_secrets(text)
    if len(safe) <= MAX_EVIDENCE:
        return safe
    return safe[: MAX_EVIDENCE - 16] + "\n… [truncated]"


_bounded_evidence = bounded_evidence


def semantic_payload_hash(data: dict[str, Any]) -> str:
    """Hash redacted/normalized event semantics (excludes id/time/hash)."""
    payload = {
        "event_type": data.get("event_type"),
        "session_id": data.get("session_id"),
        "project": data.get("project"),
        "source": data.get("source"),
        "tool_name": data.get("tool_name"),
        "outcome": data.get("outcome"),
        "evidence_excerpt": bounded_evidence(data.get("evidence_excerpt")) or None,
        "tags": data.get("tags") or [],
    }
    # Only include when set — keeps legacy events' hashes stable.
    if data.get("project_key"):
        payload["project_key"] = data.get("project_key")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _read_local_root(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        key, separator, value = line.partition(":")
        normalized = key.strip().lower()
        if separator and normalized == "root":
            value = value.strip().strip("\"'")
            if value:
                return value
    return None


def _find_local(filename: str) -> str | None:
    home = Path.home()
    user_config = home / ".claude" / filename
    root = _read_local_root(user_config)
    if root:
        return root
    current = Path.cwd()
    while True:
        root = _read_local_root(current / ".claude" / filename)
        if root:
            return root
        if current.resolve() == home.resolve():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def discover_root() -> str:
    """Priority: MEMORANT_ROOT > settings.json root > memorant.local.md."""
    root = os.environ.get("MEMORANT_ROOT", "").strip()
    if not root:
        try:
            from .config import settings_root
        except ImportError:
            try:
                from config import settings_root  # type: ignore
            except ImportError:
                settings_root = None  # type: ignore
        if settings_root is not None:
            try:
                root = (settings_root() or "").strip()
            except Exception:
                root = ""
    if not root:
        root = (_find_local("memorant.local.md") or "").strip()
    if root:
        return os.path.realpath(os.path.expanduser(root))
    raise RuntimeError("MEMORANT_ROOT is not configured")


def _validate_input(data: dict[str, Any]) -> dict[str, Any]:
    limits = {
        "session_id": 256,
        "project": 256,
        "source": 128,
        "tool_name": 128,
        "outcome": 64,
        "evidence_excerpt": 20_000,
    }
    if data.get("event_type") not in EVENT_TYPES:
        raise ValueError("unknown event_type")
    cleaned: dict[str, Any] = {"event_type": data["event_type"]}
    for key, limit in limits.items():
        value = data.get(key)
        if value is None and key in {"tool_name", "outcome", "evidence_excerpt"}:
            cleaned[key] = None
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"invalid {key}")
        cleaned[key] = value.strip()
    tags = data.get("tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 32
        or any(not isinstance(tag, str) or not 1 <= len(tag) <= 64 for tag in tags)
    ):
        raise ValueError("invalid tags")
    cleaned["tags"] = tags
    project_key = data.get("project_key")
    if project_key is None or project_key == "":
        cleaned["project_key"] = None
    elif (
        isinstance(project_key, str)
        and re.fullmatch(r"[0-9a-f]{16,64}", project_key.strip().lower())
    ):
        cleaned["project_key"] = project_key.strip().lower()
    else:
        raise ValueError("invalid project_key")
    return cleaned


def build_event(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = _validate_input(data)
    cleaned["evidence_excerpt"] = (
        bounded_evidence(cleaned.get("evidence_excerpt")) or None
    )
    if not cleaned.get("project_key"):
        cleaned.pop("project_key", None)
    return {
        **cleaned,
        "event_id": uuid.uuid4().hex,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload_hash": semantic_payload_hash(cleaned),
    }


def _yaml_scalar(key: str, value: Any) -> str:
    if value is None:
        return "null"
    if key in {"event_id", "event_type", "payload_hash"}:
        return str(value)
    if (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", value)
        and value.lower() not in {"null", "true", "false", "yes", "no", "on", "off"}
    ):
        return value
    return json.dumps(value, ensure_ascii=False)


def _render_event(event: dict[str, Any]) -> str:
    evidence = _bounded_evidence(event.get("evidence_excerpt"))
    event["evidence_excerpt"] = evidence or None
    fields = [
        "event_id",
        "event_type",
        "observed_at",
        "session_id",
        "project",
        "source",
        "tool_name",
        "outcome",
        "evidence_excerpt",
        "payload_hash",
        "tags",
    ]
    if event.get("project_key"):
        # Keep project_key next to project for readability.
        fields.insert(fields.index("project") + 1, "project_key")
    frontmatter = "\n".join(
        f"{key}: {_yaml_scalar(key, event.get(key))}" for key in fields
    )
    body = f"# {event['event_type']}\n"
    if evidence:
        body += f"\n{evidence}\n"
    return f"---\n{frontmatter}\n---\n\n{body}"


def _single_quoted_closes(raw: str) -> bool:
    if not raw.startswith("'"):
        return False
    i = 1
    while i < len(raw):
        if raw[i] == "'":
            if i + 1 < len(raw) and raw[i + 1] == "'":
                i += 2
                continue
            return i == len(raw) - 1
        i += 1
    return False


def _fold_yaml_single_quoted_parts(parts: list[str]) -> str:
    folded: list[str] = []
    pending_blank = False
    for part in parts:
        if part == "":
            pending_blank = True
            continue
        if not folded:
            folded.append(part)
        elif pending_blank:
            folded.append("\n" + part)
            pending_blank = False
        else:
            folded.append(" " + part)
    return "".join(folded).replace("''", "'")


def _parse_single_quoted(lines: list[str], index: int, first: str) -> tuple[Any, int]:
    """Parse a YAML single-quoted scalar that may span lines."""
    if _single_quoted_closes(first):
        return first[1:-1].replace("''", "'"), index + 1

    parts: list[str] = [first[1:]]
    index += 1
    while index < len(lines):
        stripped = lines[index].lstrip(" ")
        close_at = None
        i = 0
        while i < len(stripped):
            if stripped[i] == "'":
                if i + 1 < len(stripped) and stripped[i + 1] == "'":
                    i += 2
                    continue
                close_at = i
                break
            i += 1
        if close_at is not None:
            parts.append(stripped[:close_at])
            return _fold_yaml_single_quoted_parts(parts), index + 1
        parts.append(stripped)
        index += 1
    raise ValueError("unterminated single-quoted scalar")


def _read_event(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated frontmatter")

    def parse_scalar(raw: str) -> Any:
        if raw == "null":
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if len(raw) >= 2 and raw[0] == raw[-1] == "'":
                return raw[1:-1].replace("''", "'")
            return raw

    event: dict[str, Any] = {}
    lines = text[3:end].splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        key, separator, raw = line.partition(":")
        if not separator:
            index += 1
            continue
        raw = raw.strip()
        if not raw:
            values: list[Any] = []
            index += 1
            while index < len(lines) and lines[index].startswith("- "):
                values.append(parse_scalar(lines[index][2:].strip()))
                index += 1
            value: Any = values
        elif raw.startswith("'") and not _single_quoted_closes(raw):
            value, index = _parse_single_quoted(lines, index, raw)
        else:
            value = parse_scalar(raw)
            index += 1
        event[key.strip()] = value
    required = {
        "event_id",
        "event_type",
        "observed_at",
        "session_id",
        "project",
        "source",
        "tool_name",
        "outcome",
        "evidence_excerpt",
        "payload_hash",
        "tags",
    }
    if not required.issubset(event):
        raise ValueError("incomplete event frontmatter")
    return event


def _validate_stored_event(
    event: dict[str, Any],
    payload_hash: str,
    *,
    evidence_limit: int = MAX_EVIDENCE,
) -> None:
    fixed_fields = {
        "event_id",
        "event_type",
        "observed_at",
        "session_id",
        "project",
        "source",
        "tool_name",
        "outcome",
        "evidence_excerpt",
        "payload_hash",
        "tags",
    }
    optional_fields = {"project_key"}
    keys = set(event)
    if not fixed_fields.issubset(keys) or not keys.issubset(fixed_fields | optional_fields):
        raise ValueError("unexpected event fields")
    project_key = event.get("project_key")
    if project_key is not None and not (
        isinstance(project_key, str)
        and re.fullmatch(r"[0-9a-f]{16,64}", project_key)
    ):
        raise ValueError("invalid project_key")
    if not isinstance(event["event_id"], str) or not re.fullmatch(
        r"[0-9a-f]{32}", event["event_id"]
    ):
        raise ValueError("invalid event_id")
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError("invalid event_type")
    if not isinstance(event["observed_at"], str):
        raise ValueError("invalid observed_at")
    observed = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if (
        not isinstance(event["payload_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", event["payload_hash"])
        or event["payload_hash"] != payload_hash
    ):
        raise ValueError("invalid payload_hash")
    if semantic_payload_hash(event) != payload_hash:
        raise ValueError("semantic payload_hash mismatch")
    limits = {
        "session_id": 256,
        "project": 256,
        "source": 128,
        "tool_name": 128,
        "outcome": 64,
        "evidence_excerpt": evidence_limit,
    }
    for key, limit in limits.items():
        value = event[key]
        if key in {"tool_name", "outcome", "evidence_excerpt"} and value is None:
            continue
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"invalid {key}")
        if key in {"session_id", "project", "source"} and not value:
            raise ValueError(f"invalid {key}")
    tags = event["tags"]
    if (
        not isinstance(tags, list)
        or len(tags) > 32
        or any(not isinstance(tag, str) or not 1 <= len(tag) <= 64 for tag in tags)
    ):
        raise ValueError("invalid tags")


def _existing_by_hash(journal: Path, payload_hash: str) -> tuple[Path, dict[str, Any]] | None:
    for path in sorted(journal.glob("**/*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            match = _PAYLOAD_HASH.search(text)
            if match and match.group(1) == payload_hash:
                event = _read_event(path)
                _validate_stored_event(event, payload_hash)
                return path, event
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return None


def _resolve_inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        inside = os.path.commonpath((str(root), str(resolved))) == str(root)
    except ValueError:
        inside = False
    if not inside or resolved == root:
        raise ValueError(f"path escapes MEMORANT_ROOT: {path}")
    return resolved


def _ensure_dir_inside(root: Path, relative: Path) -> Path:
    """Create each path segment under root, refusing symlink escapes."""
    current = root
    for part in relative.parts:
        nxt = current / part
        if nxt.is_symlink():
            _resolve_inside(root, nxt)
        elif nxt.exists() and not nxt.is_dir():
            raise ValueError(f"path escapes MEMORANT_ROOT: {nxt}")
        else:
            nxt.mkdir(exist_ok=True)
        current = _resolve_inside(root, nxt)
    return current


def append_event_data(event: dict[str, Any], *, root: str) -> dict[str, Any]:
    payload_hash = event.get("payload_hash")
    if not isinstance(payload_hash, str):
        raise ValueError("invalid payload_hash")
    _validate_stored_event(event, payload_hash, evidence_limit=20_000)
    root_candidate = Path(root)
    root_candidate.mkdir(parents=True, exist_ok=True)
    root_path = root_candidate.resolve()
    journal = _ensure_dir_inside(root_path, Path("journal"))
    lock_path = journal / ".append.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = _existing_by_hash(journal, str(event["payload_hash"]))
            if existing:
                path, stored = existing
                return {**stored, "path": path.relative_to(root_path).as_posix()}
            observed = datetime.fromisoformat(
                str(event["observed_at"]).replace("Z", "+00:00")
            )
            base_rel = (
                f"journal/{observed:%Y/%m/%d}/"
                f"{observed:%Y%m%dT%H%M%S%fZ}-{event['event_id']}.md"
            )
            rendered = _render_event(event)
            target_dir = _ensure_dir_inside(root_path, Path(base_rel).parent)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{event['event_id']}-", suffix=".tmp", dir=target_dir
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as temp:
                    temp.write(rendered)
                    temp.flush()
                    os.fsync(temp.fileno())
                for suffix in range(101):
                    rel = (
                        base_rel
                        if not suffix
                        else base_rel.removesuffix(".md") + f"-{suffix}.md"
                    )
                    target = target_dir / Path(rel).name
                    try:
                        os.link(temp_name, target)
                        _resolve_inside(root_path, target)
                        return {**event, "path": rel}
                    except FileExistsError:
                        continue
                    except ValueError:
                        try:
                            target.unlink()
                        except OSError:
                            pass
                        raise
                raise FileExistsError("journal target collision limit exceeded")
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_hook_event(data: dict[str, Any]) -> dict[str, Any]:
    return append_event_data(build_event(data), root=discover_root())
