"""Append-only Memory Activity markdown surface."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from .hook_core import redact_secrets
from .naming import resolve_safe_path, vault_root

AttentionLevel = str  # info | needs_attention | conflict


def _today() -> str:
    return date.today().isoformat()


def _activity_rel(day: str | None = None) -> str:
    return f"activity/{day or _today()}.md"


def _atomic_append(path: Path, header_fm: dict[str, Any], line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        text += line
        if not text.endswith("\n"):
            text += "\n"
    else:
        post = frontmatter.Post("", **header_fm)
        text = frontmatter.dumps(post)
        if not text.endswith("\n"):
            text += "\n"
        text += line + ("\n" if not line.endswith("\n") else "")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_activity(
    kind: str,
    summary: str,
    *,
    project: str | None = None,
    memory_path: str | None = None,
    attention: AttentionLevel = "info",
    day: str | None = None,
) -> dict[str, Any]:
    day = day or _today()
    rel = _activity_rel(day)
    safe = Path(resolve_safe_path(rel))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    clean_summary = redact_secrets(summary).replace("\n", " ").strip()[:500]
    parts = [f"- {now}", f"`{kind}`", clean_summary]
    if project:
        parts.append(f"project={project}")
    if memory_path:
        parts.append(f"memory={memory_path}")
    if attention != "info":
        parts.append(f"attention={attention}")
    line = " ".join(parts)
    _atomic_append(
        safe,
        {
            "type": "activity",
            "date": day,
            "title": f"Memorant activity {day}",
        },
        line,
    )
    return {"path": rel, "line": line, "attention": attention}


def read_activity(
    *,
    day: str | None = None,
    project: str | None = None,
    attention_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    day = day or _today()
    rel = _activity_rel(day)
    try:
        safe = Path(resolve_safe_path(rel))
    except Exception as e:
        return {"error": str(e), "entries": [], "count": 0, "day": day}
    if not safe.is_file():
        return {"day": day, "entries": [], "count": 0, "path": rel}
    text = safe.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        if project and f"project={project}" not in line:
            continue
        attention = "needs_attention" if "attention=needs_attention" in line else (
            "conflict" if "attention=conflict" in line else "info"
        )
        if attention_only and attention == "info":
            continue
        entries.append({"line": line[2:], "attention": attention})
        if len(entries) >= limit:
            break
    return {"day": day, "path": rel, "entries": entries, "count": len(entries)}


def session_end_summary(*, project: str | None = None) -> str:
    payload = read_activity(project=project, limit=200)
    entries = payload.get("entries") or []
    added = sum(1 for e in entries if "`write`" in e["line"] or "`memory.write`" in e["line"])
    promoted = sum(1 for e in entries if "`promote`" in e["line"])
    conflicts = sum(1 for e in entries if e.get("attention") == "conflict")
    attention = sum(1 for e in entries if e.get("attention") == "needs_attention")
    text = (
        f"Memorant: +{added} memories, {promoted} promotions, "
        f"{conflicts} conflicts, {attention} need attention."
    )
    return text[:1000]
