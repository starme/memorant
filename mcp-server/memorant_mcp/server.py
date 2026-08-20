"""FastMCP server for 书童 · Memorant.

Exposes the `memorant_*` tools for journal events, A/B Memory Envelopes,
trust-aware recall, promotion, feedback, activity audit, and legacy migration.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from pydantic import Field

from .activity import append_activity, read_activity, session_end_summary
from .event_schema import EventInput, EventType
from .host_adapters import detect_host, get_adapter
from .journal import append_event, list_pending_events
from .migration import detect_legacy_data, migrate_legacy
from .memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryScope,
    MemoryWriteInput,
    TrustTier,
)
from .memory_store import list_memories, read_memory, write_memory
from .naming import memorant_root
from .promotion import apply_feedback, promote_memory
from .recall import format_recall_context, recall_memories

mcp = FastMCP(
    "memorant",
    instructions=(
        "书童 · Memorant: local long-term memory runtime. Hooks capture "
        "deterministic journal events; the host Claude distills them into "
        "A/B Memory Envelopes (verified / provisional) with explicit trust "
        "fields; recall ranks memories by trust + tide. Markdown is the "
        "only source of truth."
    ),
)


@mcp.tool(name="memorant_append_event")
async def memorant_append_event(
    event_type: EventType,
    session_id: Annotated[str, Field(min_length=1, max_length=256)],
    project: Annotated[str, Field(min_length=1, max_length=256)],
    source: Annotated[str, Field(min_length=1, max_length=128)],
    tool_name: Annotated[Optional[str], Field(max_length=128)] = None,
    outcome: Annotated[Optional[str], Field(max_length=64)] = None,
    evidence_excerpt: Annotated[Optional[str], Field(max_length=20_000)] = None,
    tags: Annotated[
        Optional[list[Annotated[str, Field(min_length=1, max_length=64)]]],
        Field(max_length=32),
    ] = None,
) -> dict[str, Any]:
    """Append a strict, redacted event to the immutable local journal."""
    try:
        event = EventInput(
            event_type=event_type,
            session_id=session_id,
            project=project,
            source=source,
            tool_name=tool_name,
            outcome=outcome,
            evidence_excerpt=evidence_excerpt,
            tags=tags or [],
        )
        return append_event(event)
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(
    name="memorant_list_pending_events",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_list_pending_events(
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """List journal events not referenced by any memory source_event_ids."""
    try:
        events = list_pending_events(session_id=session_id, project=project)
        payload: dict[str, Any] = {"events": events, "count": len(events)}
        _attach_degradation_note(payload)
        return payload
    except Exception as e:
        return {"error": "ERROR", "message": str(e), "events": [], "count": 0}


def _attach_degradation_note(payload: dict[str, Any]) -> None:
    """当当前宿主不具备自动提炼时，附一致的降级说明（非破坏性，不覆盖既有字段）。"""
    try:
        adapter = get_adapter(detect_host())
    except Exception:
        return
    if adapter.capabilities.auto_distill:
        return
    note = adapter.describe_degradation("auto_distill")
    if note:
        payload["degradation_note"] = note


@mcp.tool(name="memorant_write_memory")
async def memorant_write_memory(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    claim: Annotated[str, Field(min_length=1, max_length=4000)],
    kind: MemoryKind,
    trust_tier: TrustTier,
    confidence: Annotated[float, Field(ge=0.0, le=1.0)],
    evidence: Annotated[list[dict[str, Any]], Field(min_length=1)],
    source_event_ids: Annotated[list[str], Field(min_length=1)],
    project: Optional[str] = None,
    project_key: Optional[str] = None,
    stack: Optional[list[str]] = None,
    related: Optional[list[str]] = None,
    supersedes: Optional[str] = None,
    origin_session_ids: Optional[list[str]] = None,
    body: Optional[str] = None,
    recurrence_cadence: Literal["ad-hoc", "quarterly", "annual"] = "ad-hoc",
) -> dict[str, Any]:
    """Write an A/B Memory Envelope. Requires source_event_ids + evidence. Dedupes by fingerprint.

    recurrence_cadence: 季节钟（冻结 §3.4）——标 annual/quarterly 的年度类经验在
    预期复现窗口内不判 dusty（窗口内可唤醒、仍带复核），防绝对天数一刀切误伤。默认 ad-hoc。
    """
    try:
        refs = [EvidenceRef(**item) for item in evidence]
        write = MemoryWriteInput(
            title=title,
            claim=claim,
            kind=kind,
            trust_tier=trust_tier,
            confidence=confidence,
            scope=MemoryScope(
                project=project, project_key=project_key, stack=stack or []
            ),
            evidence=refs,
            source_event_ids=source_event_ids,
            related=related or [],
            supersedes=supersedes,
            origin_session_ids=origin_session_ids or [],
            body=body,
            recurrence_cadence=recurrence_cadence,
        )
        result = write_memory(write)
        append_activity(
            "memory.write",
            f"{trust_tier.value} {title}",
            project=project,
            memory_path=result.get("path"),
            attention="info",
        )
        return result
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(
    name="memorant_recall",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_recall(
    query: str,
    project: Optional[str] = None,
    trigger: Optional[str] = None,
    limit: int = 5,
    include_provisional: bool = True,
) -> dict[str, Any]:
    """Recall ranked memories with trust labels for event-driven injection."""
    try:
        payload = recall_memories(
            query,
            project=project,
            trigger=trigger,
            limit=limit,
            include_provisional=include_provisional,
        )
        payload["context"] = format_recall_context(payload)
        append_activity(
            "memory.recall",
            f"query={query[:80]} hits={payload['count']}",
            project=project,
        )
        return payload
    except Exception as e:
        return {
            "error": "ERROR",
            "message": str(e),
            "results": [],
            "count": 0,
        }


@mcp.tool(name="memorant_feedback")
async def memorant_feedback(
    path: str,
    action: Annotated[
        str,
        Field(
            pattern=r"^(adopted|ignored|corrected|contradicted|successful_reuse)$"
        ),
    ],
    note: Optional[str] = None,
    session_id: Optional[str] = None,
    replacement_claim: Optional[str] = None,
    success_outcome: Optional[str] = None,
) -> dict[str, Any]:
    """Apply async feedback: adopt/ignore/correct/contradict/successful_reuse."""
    try:
        result = apply_feedback(
            path,
            action,  # type: ignore[arg-type]
            note=note,
            session_id=session_id,
            replacement_claim=replacement_claim,
            success_outcome=success_outcome,
        )
        attention = (
            "conflict"
            if action in {"corrected", "contradicted"}
            else "info"
        )
        append_activity(
            f"memory.feedback.{action}",
            note or action,
            memory_path=path,
            attention=attention,
        )
        return result
    except FileNotFoundError:
        return {"error": "NOT_FOUND", "message": path}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(name="memorant_promote")
async def memorant_promote(
    path: str,
    evidence_session_id: Annotated[str, Field(min_length=1, max_length=256)],
    success_outcome: Annotated[str, Field(min_length=1, max_length=64)],
    evidence_excerpt: Optional[str] = None,
) -> dict[str, Any]:
    """Promote provisional (B) → verified (A) with independent success evidence."""
    try:
        result = promote_memory(
            path,
            evidence_session_id=evidence_session_id,
            success_outcome=success_outcome,
            evidence_excerpt=evidence_excerpt,
        )
        if result.get("promoted"):
            append_activity(
                "memory.promote",
                f"B→A {path}",
                memory_path=path,
            )
        return result
    except FileNotFoundError:
        return {"error": "NOT_FOUND", "message": path}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(
    name="memorant_activity",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_activity(
    day: Optional[str] = None,
    project: Optional[str] = None,
    attention_only: bool = False,
    include_session_summary: bool = False,
) -> dict[str, Any]:
    """Read non-blocking Memory Activity for a day/project."""
    try:
        payload = read_activity(
            day=day, project=project, attention_only=attention_only
        )
        if include_session_summary:
            payload["session_summary"] = session_end_summary(project=project)
        return payload
    except Exception as e:
        return {"error": "ERROR", "message": str(e), "entries": [], "count": 0}


# Keep list_memories import used for future tooling / tests.
_ = (list_memories, read_memory)


@mcp.tool(
    name="memorant_host_info",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_host_info() -> dict[str, Any]:
    """Report the current host adapter and its capability matrix (read-only)."""
    try:
        name = detect_host()
        adapter = get_adapter(name)
        caps = adapter.capabilities
        return {
            "host": name,
            "capabilities": {
                "event_capture": caps.event_capture,
                "recall": caps.recall,
                "auto_distill": caps.auto_distill,
                "promotion": caps.promotion,
                "activity": caps.activity,
            },
        }
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(name="memorant_migrate")
async def memorant_migrate(
    dry_run: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Migrate legacy bugs/snippets/daily/arch notes into Memory Envelopes.

    - dry_run=True: preview only (list what would migrate), write nothing.
    - confirm must be True to actually migrate (red line: never auto-migrate).
    """
    try:
        root = memorant_root()
    except RuntimeError as e:
        return {"error": "NOT_CONFIGURED", "message": str(e)}

    if dry_run:
        try:
            return migrate_legacy(root, dry_run=True)
        except Exception:
            return {"error": "ERROR", "message": "migration preview failed"}

    counts = detect_legacy_data(root)
    total = sum(counts.values())
    if not confirm:
        if total == 0:
            return {
                "action_required": "none",
                "legacy_counts": counts,
                "total": 0,
            }
        return {
            "action_required": "confirm",
            "legacy_counts": counts,
            "total": total,
            "message": (
                f"检测到 {total} 条历史数据，是否迁移为 Memorant 记忆？"
                "请调用 memorant_migrate(confirm=true)。"
            ),
        }

    try:
        result = migrate_legacy(root)
    except Exception:
        return {"error": "ERROR", "message": "migration failed; see the local migration report"}
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
