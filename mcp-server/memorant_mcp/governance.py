"""资料经验治理（去重/合并候选/冲突/归档）。

只改 memories/ 与 index/，不触碰 source_docs/。archive 置 lifecycle=superseded，
merge_candidate/conflict 仅产 activity 标记不改 memory 状态。
"""

from __future__ import annotations

from typing import Any, Literal

from .activity import append_activity
from .memory_schema import LifecycleState
from .memory_store import read_memory, update_memory_fields

GovernAction = Literal["dedupe", "merge_candidate", "conflict", "archive"]


def govern_source(
    doc_id: str,
    action: GovernAction,
    related_memory_id: str | None = None,
    replacement_claim: str | None = None,
) -> dict[str, Any]:
    """执行治理动作。返回 {action, produced, doc_id, memory_path, attention}。"""
    if action == "archive":
        if not related_memory_id:
            return {"error": "VALIDATION_ERROR", "message": "archive requires related_memory_id"}
        try:
            current = read_memory(related_memory_id)
        except FileNotFoundError:
            return {"error": "NOT_FOUND", "message": related_memory_id}
        updated = update_memory_fields(current["path"], {"lifecycle": LifecycleState.superseded.value})
        append_activity(
            "memory.archive",
            f"doc={doc_id} -> {related_memory_id}",
            memory_path=current["path"],
            attention="info",
        )
        return {
            "action": action,
            "produced": True,
            "doc_id": doc_id,
            "memory_path": updated["path"],
            "attention": "info",
        }

    if action == "dedupe":
        if not related_memory_id:
            return {"error": "VALIDATION_ERROR", "message": "dedupe requires related_memory_id"}
        try:
            current = read_memory(related_memory_id)
        except FileNotFoundError:
            return {"error": "NOT_FOUND", "message": related_memory_id}
        # 续燃：更新 updated_at（不新建、不改 lifecycle）。
        updated = update_memory_fields(current["path"], {})
        append_activity(
            "memory.dedupe",
            f"doc={doc_id} -> {related_memory_id} (reinforced)",
            memory_path=current["path"],
            attention="info",
        )
        return {
            "action": action,
            "produced": True,
            "doc_id": doc_id,
            "memory_path": updated["path"],
            "attention": "info",
        }

    if action == "merge_candidate":
        append_activity(
            "memory.merge_candidate",
            f"doc={doc_id} related={related_memory_id}",
            memory_path=None,
            attention="needs_attention",
        )
        return {
            "action": action,
            "produced": True,
            "doc_id": doc_id,
            "memory_path": None,
            "attention": "needs_attention",
        }

    if action == "conflict":
        append_activity(
            "memory.conflict",
            f"doc={doc_id} related={related_memory_id} replacement={replacement_claim or ''}",
            memory_path=None,
            attention="conflict",
        )
        return {
            "action": action,
            "produced": True,
            "doc_id": doc_id,
            "memory_path": None,
            "attention": "conflict",
        }

    return {"error": "UNKNOWN_ACTION", "message": f"unsupported action {action}"}
