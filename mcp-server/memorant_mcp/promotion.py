"""B→A promotion gates and feedback/conflict handling."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from .memory_schema import (
    EvidenceRef,
    LifecycleState,
    MemoryKind,
    MemoryScope,
    MemoryWriteInput,
    TrustTier,
)
from .memory_store import read_memory, update_memory_fields, write_memory

FeedbackAction = Literal[
    "adopted",
    "ignored",
    "corrected",
    "contradicted",
    "successful_reuse",
]


def _success_outcome(outcome: str | None) -> bool:
    if not outcome:
        return False
    return outcome.strip().lower() in {"success", "passed", "ok", "verified"}


def promote_memory(
    memory_id_or_path: str,
    *,
    evidence_session_id: str,
    success_outcome: str,
    evidence_excerpt: str | None = None,
) -> dict[str, Any]:
    current = read_memory(memory_id_or_path)
    if current.get("legacy"):
        return {"error": "LEGACY", "message": "legacy entries are already verified"}
    if current.get("trust_tier") != TrustTier.provisional.value:
        return {
            "error": "NOT_PROVISIONAL",
            "message": "only provisional memories can be promoted",
            "path": current.get("path"),
        }
    if not _success_outcome(success_outcome):
        return {"error": "NO_SUCCESS", "message": "promotion requires a success outcome"}

    origin_sessions = [
        str(s) for s in (current.get("origin_session_ids") or []) if s
    ]
    for item in current.get("evidence") or []:
        if isinstance(item, dict) and item.get("session_id"):
            origin_sessions.append(str(item["session_id"]))
    origin_sessions = list(dict.fromkeys(origin_sessions))

    if evidence_session_id in origin_sessions:
        return {
            "error": "SAME_SESSION",
            "message": "B→A requires an independent session_id",
            "origin_session_ids": origin_sessions,
        }

    success_key = f"{evidence_session_id}:{success_outcome.strip().lower()}"
    keys = [str(k) for k in (current.get("independent_success_keys") or [])]
    if success_key not in keys:
        keys.append(success_key)

    evidence = list(current.get("evidence") or [])
    if evidence_excerpt:
        evidence.append(
            {
                "source": "independent-success",
                "excerpt": evidence_excerpt[:4096],
                "observed_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "session_id": evidence_session_id,
            }
        )

    updated = update_memory_fields(
        current["path"],
        {
            "trust_tier": TrustTier.verified.value,
            "lifecycle": LifecycleState.reinforced.value,
            "confidence": max(float(current.get("confidence") or 0.5), 0.8),
            "independent_success_keys": keys,
            "evidence": evidence,
            "origin_session_ids": origin_sessions,
        },
    )
    return {
        "promoted": True,
        "path": updated["path"],
        "memory_id": updated.get("memory_id"),
        "trust_tier": updated.get("trust_tier"),
        "lifecycle": updated.get("lifecycle"),
        "independent_success_keys": updated.get("independent_success_keys"),
    }


def apply_feedback(
    memory_id_or_path: str,
    action: FeedbackAction,
    *,
    note: str | None = None,
    session_id: str | None = None,
    replacement_claim: str | None = None,
    success_outcome: str | None = None,
) -> dict[str, Any]:
    current = read_memory(memory_id_or_path)
    if current.get("legacy"):
        return {"error": "LEGACY", "message": "use vault tools for legacy entries"}

    if action == "successful_reuse":
        if not session_id:
            return {"error": "VALIDATION_ERROR", "message": "session_id required"}
        return promote_memory(
            current["path"],
            evidence_session_id=session_id,
            success_outcome=success_outcome or "success",
            evidence_excerpt=note,
        )

    if action == "adopted":
        return update_memory_fields(
            current["path"],
            {"lifecycle": LifecycleState.active.value},
        ) | {"feedback": action}

    if action == "ignored":
        return {"feedback": action, "path": current["path"], "unchanged": True}

    if action in {"corrected", "contradicted"}:
        # User-confirmed delist: no replacement → rejected (audited negative);
        # with replacement_claim → corrected + new memory (evolution/correction pair).
        if action == "contradicted":
            next_lifecycle = LifecycleState.superseded.value
        elif replacement_claim:
            next_lifecycle = LifecycleState.corrected.value
        else:
            next_lifecycle = LifecycleState.rejected.value
        updated = update_memory_fields(
            current["path"],
            {"lifecycle": next_lifecycle},
        )
        replacement = None
        if replacement_claim:
            prior_ids = current.get("source_event_ids") or []
            if not isinstance(prior_ids, list) or not prior_ids:
                # Feedback corrections must still satisfy write contract.
                seed = (current.get("memory_id") or current.get("path") or "feedback").encode()
                prior_ids = [hashlib.md5(seed).hexdigest()]
            write = MemoryWriteInput(
                title=f"Correction: {current.get('title')}",
                claim=replacement_claim,
                kind=MemoryKind(current.get("kind") or MemoryKind.episodic.value),
                trust_tier=TrustTier.provisional,
                confidence=0.55,
                scope=MemoryScope(**(current.get("scope") or {})),
                evidence=[
                    EvidenceRef(
                        source="user-feedback",
                        excerpt=(note or replacement_claim)[:4096],
                        session_id=session_id,
                    )
                ],
                source_event_ids=[str(item) for item in prior_ids][:64],
                supersedes=current.get("path"),
                origin_session_ids=[session_id] if session_id else [],
            )
            replacement = write_memory(write)
        return {
            "feedback": action,
            "updated": updated,
            "replacement": replacement,
        }

    return {"error": "UNKNOWN_ACTION", "message": f"unsupported action {action}"}
