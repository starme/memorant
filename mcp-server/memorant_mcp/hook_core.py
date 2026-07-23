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
EVENT_TYPES = {
    "session.start",
    "session.end",
    "context.precompact",
    "tool.failure",
    "test.failure",
    "test.success",
    "git.commit",
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


def _bounded_evidence(text: str | None) -> str:
    safe = redact_secrets(text)
    if len(safe) <= MAX_EVIDENCE:
        return safe
    return safe[: MAX_EVIDENCE - 16] + "\n… [truncated]"


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
        if separator and key.strip().lower() in {"root", "vault_root"}:
            value = value.strip().strip("\"'")
            if value:
                return value
    return None


def discover_root() -> str:
    for name in ("MEMORANT_ROOT", "VAULT_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            return os.path.realpath(value)
    home = Path.home()
    candidates = [
        home / ".claude" / "memorant.local.md",
        home / ".claude" / "vault.local.md",
    ]
    current = Path.cwd()
    while True:
        candidates.extend(
            [
                current / ".claude" / "memorant.local.md",
                current / ".claude" / "vault.local.md",
            ]
        )
        if current == current.parent:
            break
        current = current.parent
    for candidate in candidates:
        root = _read_local_root(candidate)
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
    return cleaned


def build_event(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = _validate_input(data)
    canonical = json.dumps(
        cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        **cleaned,
        "event_id": uuid.uuid4().hex,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload_hash": hashlib.sha256(canonical).hexdigest(),
    }


def _yaml_scalar(key: str, value: Any) -> str:
    if value is None:
        return "null"
    if key in {"event_id", "event_type", "outcome", "payload_hash"}:
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _render_event(event: dict[str, Any]) -> str:
    evidence = _bounded_evidence(event.get("evidence_excerpt"))
    event["evidence_excerpt"] = evidence or None
    fields = (
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
    )
    frontmatter = "\n".join(
        f"{key}: {_yaml_scalar(key, event.get(key))}" for key in fields
    )
    body = f"# {event['event_type']}\n"
    if evidence:
        body += f"\n{evidence}\n"
    return f"---\n{frontmatter}\n---\n\n{body}"


def _read_event(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    event: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        raw = raw.strip()
        if raw == "null":
            value: Any = None
        else:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw.strip("\"'")
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


def _existing_by_hash(journal: Path, payload_hash: str) -> tuple[Path, dict[str, Any]] | None:
    for path in sorted(journal.glob("**/*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            match = _PAYLOAD_HASH.search(text)
            if match and match.group(1) == payload_hash:
                return path, _read_event(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return None


def append_event_data(event: dict[str, Any], *, root: str) -> dict[str, Any]:
    root_path = Path(os.path.realpath(root))
    journal = root_path / "journal"
    journal.mkdir(parents=True, exist_ok=True)
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
            target_dir = root_path / Path(base_rel).parent
            target_dir.mkdir(parents=True, exist_ok=True)
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
                    target = root_path / rel
                    try:
                        os.link(temp_name, target)
                        return {**event, "path": rel}
                    except FileExistsError:
                        continue
                raise FileExistsError("journal target collision limit exceeded")
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_hook_event(data: dict[str, Any]) -> dict[str, Any]:
    return append_event_data(build_event(data), root=discover_root())
