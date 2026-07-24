"""Event-driven memory recall with trust-aware ranking."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .memory_schema import LifecycleState, TrustTier
from .memory_store import list_memories, mark_recalled

PROVISIONAL_WEIGHT = 0.35
DEFAULT_LIMIT = 5
MAX_CHARS = 8000
INACTIVE = {
    LifecycleState.corrected.value,
    LifecycleState.superseded.value,
    LifecycleState.candidate.value,
    LifecycleState.observed.value,
}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", text.lower(), flags=re.UNICODE) if len(t) >= 2}


def _parse_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _score(memory: dict[str, Any], query_tokens: set[str], project: str | None) -> float:
    title = str(memory.get("title") or "")
    claim = str(memory.get("claim") or "")
    hay = _tokenize(f"{title} {claim}")
    overlap = len(query_tokens & hay) if query_tokens else 0
    score = float(overlap) * 2.0

    scope = memory.get("scope") or {}
    scope_project = scope.get("project") if isinstance(scope, dict) else None
    if project and scope_project == project:
        score += 3.0
    elif project and memory.get("project") == project:
        score += 2.0

    trust = memory.get("trust_tier")
    if trust == TrustTier.verified.value or memory.get("legacy"):
        score += 2.0
    elif trust == TrustTier.provisional.value:
        score *= PROVISIONAL_WEIGHT
        score += 0.2

    score += min(int(memory.get("recall_count") or 0), 10) * 0.15
    # mild recency boost
    score += min(_parse_time(memory.get("updated_at") or memory.get("created_at")) / 1e12, 1.0)
    return score


def _label(memory: dict[str, Any]) -> str:
    if memory.get("trust_tier") == TrustTier.provisional.value and not memory.get("legacy"):
        return "[PROVISIONAL—待验证]"
    if memory.get("legacy"):
        return "[VERIFIED·legacy]"
    return "[VERIFIED]"


def recall_memories(
    query: str,
    *,
    project: str | None = None,
    trigger: str | None = None,
    limit: int = DEFAULT_LIMIT,
    include_provisional: bool = True,
    mark: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 20))
    tokens = _tokenize(query or "")
    candidates = list_memories(project=None, limit=200, include_legacy=True)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for memory in candidates:
        lifecycle = memory.get("lifecycle") or LifecycleState.active.value
        if lifecycle in INACTIVE:
            continue
        trust = memory.get("trust_tier")
        if (
            not include_provisional
            and trust == TrustTier.provisional.value
            and not memory.get("legacy")
        ):
            continue
        score = _score(memory, tokens, project)
        if tokens and score < 0.2 and not (
            project and (memory.get("scope") or {}).get("project") == project
        ):
            continue
        ranked.append((score, memory))
    ranked.sort(key=lambda item: (-item[0], -_parse_time(item[1].get("updated_at"))))

    results: list[dict[str, Any]] = []
    for score, memory in ranked[:limit]:
        item = {
            "memory_id": memory.get("memory_id"),
            "path": memory.get("path") or memory.get("legacy_path"),
            "title": memory.get("title"),
            "claim": memory.get("claim"),
            "trust_tier": memory.get("trust_tier"),
            "lifecycle": memory.get("lifecycle"),
            "kind": memory.get("kind"),
            "score": round(score, 4),
            "label": _label(memory),
            "legacy": bool(memory.get("legacy")),
            "trigger": trigger,
        }
        results.append(item)
        if mark and not memory.get("legacy") and memory.get("path"):
            mark_recalled(str(memory["path"]))
    return {
        "results": results,
        "count": len(results),
        "query": query,
        "project": project,
        "trigger": trigger,
    }


def format_recall_context(payload: dict[str, Any], *, max_chars: int = MAX_CHARS) -> str:
    lines = ["Memorant recall:"]
    for item in payload.get("results") or []:
        lines.append(
            f"- {item.get('label')} {item.get('title')}: {item.get('claim')}"
        )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n… [truncated]"
