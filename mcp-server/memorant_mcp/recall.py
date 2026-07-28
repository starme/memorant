"""Event-driven memory recall with trust-aware ranking and dual-channel rails."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .config import load_settings
from .memory_schema import LifecycleState, TrustTier
from .memory_store import list_memories, mark_recalled
from .trust_field import (
    build_trust_view,
    classify_tide,
    is_negative_lifecycle,
    negative_overlap_threshold,
)

PROVISIONAL_WEIGHT = 0.35
NEGATIVE_WEIGHT = 0.55
DUSTY_WEIGHT = 0.7
DEFAULT_LIMIT = 5
MAX_CHARS = 8000
INACTIVE = {
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


def _overlap(memory: dict[str, Any], query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    title = str(memory.get("title") or "")
    claim = str(memory.get("claim") or "")
    return len(query_tokens & _tokenize(f"{title} {claim}"))


def _score(
    memory: dict[str, Any],
    query_tokens: set[str],
    project: str | None,
    *,
    freshness: str,
) -> float:
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

    if is_negative_lifecycle(memory):
        # Rank near-pit hits when similar; still below hot verified.
        score *= NEGATIVE_WEIGHT
        score += 0.5
    else:
        trust = memory.get("trust_tier")
        if trust == TrustTier.verified.value or memory.get("legacy"):
            score += 2.0
        elif trust == TrustTier.provisional.value:
            score *= PROVISIONAL_WEIGHT
            score += 0.2

        tide = classify_tide(memory, freshness=freshness)
        if tide == "dusty":
            score *= DUSTY_WEIGHT
        elif tide == "aging":
            score *= 0.85

    score += min(int(memory.get("recall_count") or 0), 10) * 0.15
    score += min(
        _parse_time(memory.get("updated_at") or memory.get("created_at")) / 1e12, 1.0
    )
    return score


def recall_memories(
    query: str,
    *,
    project: str | None = None,
    trigger: str | None = None,
    limit: int = DEFAULT_LIMIT,
    include_provisional: bool = True,
    include_negative: bool = True,
    mark: bool = True,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 20))
    tokens = _tokenize(query or "")
    try:
        settings = load_settings()
    except Exception:
        settings = None
    freshness = settings.behavior.freshness if settings else "mid"
    guardrail = settings.behavior.guardrail if settings else "high"
    neg_min = negative_overlap_threshold(guardrail)

    candidates = list_memories(project=None, limit=200, include_legacy=True)
    ranked: list[tuple[float, dict[str, Any], int]] = []
    for memory in candidates:
        lifecycle = memory.get("lifecycle") or LifecycleState.active.value
        if lifecycle in INACTIVE:
            continue
        negative = is_negative_lifecycle(memory)
        if negative and not include_negative:
            continue
        trust = memory.get("trust_tier")
        if (
            not include_provisional
            and trust == TrustTier.provisional.value
            and not memory.get("legacy")
            and not negative
        ):
            continue
        overlap = _overlap(memory, tokens)
        if negative and tokens and overlap < neg_min:
            continue
        score = _score(memory, tokens, project, freshness=freshness)
        if tokens and score < 0.2 and not (
            project and (memory.get("scope") or {}).get("project") == project
        ):
            if not (negative and overlap >= neg_min):
                continue
        ranked.append((score, memory, overlap))
    ranked.sort(key=lambda item: (-item[0], -_parse_time(item[1].get("updated_at"))))

    results: list[dict[str, Any]] = []
    for score, memory, overlap in ranked[:limit]:
        view = build_trust_view(
            memory, settings=settings, overlap=overlap
        )
        item = {
            "memory_id": memory.get("memory_id"),
            "path": memory.get("path") or memory.get("legacy_path"),
            "title": memory.get("title"),
            "claim": memory.get("claim"),
            "trust_tier": memory.get("trust_tier"),
            "lifecycle": memory.get("lifecycle"),
            "kind": memory.get("kind"),
            "score": round(score, 4),
            "label": view.label,
            "modality": view.modality,
            "tide": view.tide,
            "agent_rail": view.agent_rail,
            "human_hint": view.human_hint,
            "channel": view.channel,
            "legacy": bool(memory.get("legacy")),
            "trigger": trigger,
        }
        results.append(item)
        # Do not warm negative-trust via recall marks (would fake "heat").
        if (
            mark
            and not memory.get("legacy")
            and memory.get("path")
            and view.modality != "negative"
        ):
            mark_recalled(str(memory["path"]))
    return {
        "results": results,
        "count": len(results),
        "query": query,
        "project": project,
        "trigger": trigger,
    }


def format_recall_context(payload: dict[str, Any], *, max_chars: int = MAX_CHARS) -> str:
    """Format dual-channel recall: Agent rails primary; human short-asks when present."""
    lines = ["Memorant recall (trust-aware):"]
    human_hints: list[str] = []
    for item in payload.get("results") or []:
        label = item.get("label") or ""
        title = item.get("title") or ""
        modality = item.get("modality") or ""
        tide = item.get("tide") or ""
        rail = item.get("agent_rail") or f"{title}: {item.get('claim')}"
        lines.append(f"- {label} [{modality}/{tide}] {rail}")
        hint = item.get("human_hint")
        if hint:
            human_hints.append(str(hint))
    if human_hints:
        # Constitution: if short-ask present, keep agent rails but surface human channel.
        lines.append("Human channel (one decision; do not spam):")
        for hint in human_hints[:2]:
            lines.append(f"- ASK: {hint}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n… [truncated]"
