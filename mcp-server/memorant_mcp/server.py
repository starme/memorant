"""FastMCP server for 书童 · Memorant.

Six tools (all `vault_` prefixed):
  - vault_search            read-only full-text search
  - vault_create_entry      new file with schema validation + path whitelist
  - vault_append_entry      append to an existing file (daily log growth)
  - vault_update_frontmatter atomic frontmatter key change (pending_review, ADR status)
  - vault_delete_entry      destructive: delete a file (promote migration)
  - vault_get_recent        read-only: newest N entries in a dir

The server validates frontmatter (Pydantic, extra=forbid) and refuses any path
that resolves outside the configured Memorant root (resolve_safe_path). This is the security
boundary since MCP file I/O bypasses Claude's Edit/Write tools (and thus the
user's protect-files.sh hook).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Annotated, Any, Literal, Optional

import frontmatter
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .activity import append_activity, read_activity, session_end_summary
from .event_schema import EventInput, EventType
from .journal import append_event, list_pending_events
from .memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryScope,
    MemoryWriteInput,
    TrustTier,
)
from .memory_store import list_memories, read_memory, write_memory
from .naming import (
    ConflictError,
    PathForbiddenError,
    ensure_dir,
    filename_for,
    next_arch_sequence,
    resolve_safe_path,
    vault_root,
)
from .promotion import apply_feedback, promote_memory
from .recall import format_recall_context, recall_memories
from .schema import EntryType, SCHEMA_BY_TYPE
from .search import search as do_search

mcp = FastMCP(
    "memorant",
    instructions=(
        "书童 · Memorant: search and record development experience in a "
        "local Markdown knowledge base. Legacy vault_* tools remain supported."
    ),
)


def _validate_and_serialize(entry_type: str, fm: dict, title: str) -> dict:
    """Validate frontmatter dict against the type's schema; return cleaned dict."""
    try:
        et = EntryType(entry_type)
    except ValueError:
        raise ValueError(
            f"unknown entry type {entry_type!r}; expected one of {[e.value for e in EntryType]}"
        )
    model_cls = SCHEMA_BY_TYPE[et]
    # Ensure type/title present for the validator.
    fm = {**fm, "type": et.value, "title": title}
    try:
        instance = model_cls(**fm)
    except Exception as e:
        # Pydantic ValidationError — surface a single actionable message.
        errs = e.errors() if hasattr(e, "errors") else []
        if errs:
            e0 = errs[0]
            loc = ".".join(str(x) for x in e0.get("loc", []))
            raise ValueError(f"{entry_type} frontmatter invalid at '{loc}': {e0.get('msg')}")
        raise ValueError(f"{entry_type} frontmatter invalid: {e}")
    # Serialize back: dates -> ISO, enums -> values. Exclude None so optional
    # fields that weren't set don't pollute the frontmatter with `null`.
    data = instance.model_dump(mode="json", exclude_none=True)
    return data


def _write_entry(rel_path: str, fm: dict, body: str) -> str:
    safe = resolve_safe_path(rel_path)
    if os.path.exists(safe):
        raise ConflictError(f"file already exists: {rel_path}")
    ensure_dir(safe)
    post = frontmatter.Post(body, **fm)
    with open(safe, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    return rel_path


def _is_journal_path(path: str) -> bool:
    journal = os.path.join(vault_root(), "journal")
    return path == journal or path.startswith(journal + os.sep)


_DEPRECATED_PREFIX = "[deprecated: prefer memorant_* tools] "


def _deprecated(message: str) -> str:
    if message.startswith(_DEPRECATED_PREFIX):
        return message
    return f"{_DEPRECATED_PREFIX}{message}"


@mcp.tool(
    name="vault_search",
    annotations={
        "title": "Search dev experience vault",
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def vault_search(
    query: str,
    dirs: Optional[list[str]] = None,
    project: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Search the vault for past dev experience — bugs, snippets, daily notes, ADRs.

    Use this BEFORE debugging a bug, making a tech choice, or looking up an API,
    to surface relevant past experience. `query` should be the error keyword /
    concept / stack name. `dirs` filters to a subset of [bugs, snippets, daily, arch].
    `project` filters by frontmatter project (cross-category). Returns matched
    file + line + snippet.
    """
    try:
        results = do_search(query, dirs, project, limit)
    except RuntimeError as e:
        return _deprecated(f"VAULT_ERROR: {e}")
    if not results:
        return _deprecated("no matches found")
    lines = [f"{r['file']}:{r['line']} — {r['match']}" for r in results]
    return _deprecated(f"{len(results)} match(es):\n" + "\n".join(lines))

@mcp.tool(name="vault_create_entry")
async def vault_create_entry(
    type: str,
    title: str,
    body: str,
    frontmatter: dict[str, Any],
    date_str: Optional[str] = None,
    stack: Optional[list[str]] = None,
    project: Optional[Any] = None,
) -> str:
    """Create a new Memorant entry file with schema validation.

    For arch: a global sequence number is auto-assigned (do not pass one).
    For bug/snippet: pass `stack` — either as the top-level param, or in
    `frontmatter`, or both (they must agree). For daily: `project` may be a list.
    `project` follows the same dual-pass rule: pass it top-level, in frontmatter,
    or both (must agree) — arch filenames use it.
    Required frontmatter fields vary by type — the validator reports which are
    missing. Writes are refused if the path escapes the configured root or the file
    already exists.
    """
    try:
        the_date = date_str or date.today().isoformat()
        # Validate frontmatter FIRST (before allocating an arch sequence), so a
        # validation failure doesn't burn a sequence number.
        fm = dict(frontmatter)
        fm["date"] = the_date
        # `stack` is both a top-level param (used for the filename) and a
        # required frontmatter field (used for validation). Accept either one
        # so callers don't have to pass it twice — but reject a mismatch instead
        # of silently picking one.
        fm_stack = fm.get("stack")
        if stack is not None and fm_stack is None:
            fm["stack"] = stack
        elif stack is None and fm_stack is not None:
            stack = fm_stack  # back-fill the filename param
        elif stack is not None and fm_stack is not None and list(stack) != list(fm_stack):
            raise ValueError(
                f"stack mismatch: top-level {stack!r} vs frontmatter {fm_stack!r}; pass one or make them equal"
            )
        # `project` has the same dual-pass problem: it's a top-level param (arch
        # filename uses it) and may also live in frontmatter. Without sync, a
        # caller passing project only in frontmatter gets filename fallback
        # 'misc'. Same rule — accept either, reject a mismatch.
        fm_project = fm.get("project")
        if project is not None and fm_project is None:
            fm["project"] = project
        elif project is None and fm_project is not None:
            project = fm_project  # back-fill the filename param
        elif (
            project is not None
            and fm_project is not None
            and project != fm_project
        ):
            raise ValueError(
                f"project mismatch: top-level {project!r} vs frontmatter {fm_project!r}; pass one or make them equal"
            )
        cleaned = _validate_and_serialize(type, fm, title)

        # Allocate the real sequence only after validation passes.
        sequence = next_arch_sequence() if type == "arch" else None
        rel = filename_for(
            EntryType(type),
            the_date,
            title,
            stack=stack,
            project=project,
            sequence=sequence,
        )
        if sequence is not None:
            cleaned["sequence"] = sequence
        _write_entry(rel, cleaned, body)
        return _deprecated(f"created: {rel}")
    except PathForbiddenError as e:
        return _deprecated(f"PATH_FORBIDDEN: {e}")
    except ConflictError as e:
        return _deprecated(f"CONFLICT: {e}")
    except ValueError as e:
        return _deprecated(f"VALIDATION_ERROR: {e}")
    except Exception as e:
        return _deprecated(f"ERROR: {e}")


@mcp.tool(name="vault_append_entry")
async def vault_append_entry(
    path: str,
    content: str,
    section: Optional[str] = None,
) -> str:
    """Append content to an existing Memorant file. If `section` is given, append
    under that heading (creating it if missing). Used for daily log growth.
    """
    try:
        safe = resolve_safe_path(path)
        if _is_journal_path(safe):
            return _deprecated(f"IMMUTABLE: journal event cannot be changed: {path}")
        if not os.path.exists(safe):
            return _deprecated(f"NOT_FOUND: {path}")
        with open(safe, "r", encoding="utf-8") as f:
            text = f.read()
        if section:
            marker = f"\n## {section}\n"
            idx = text.find(f"\n## {section}")
            if idx == -1:
                if not text.endswith("\n"):
                    text += "\n"
                text += marker + content + "\n"
            else:
                insert_at = idx + len(f"\n## {section}\n")
                text = text[:insert_at] + content + "\n" + text[insert_at:]
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += content + "\n"
        with open(safe, "w", encoding="utf-8") as f:
            f.write(text)
        return _deprecated(f"appended to: {path}")
    except PathForbiddenError as e:
        return _deprecated(f"PATH_FORBIDDEN: {e}")
    except Exception as e:
        return _deprecated(f"ERROR: {e}")


@mcp.tool(name="vault_update_frontmatter")
async def vault_update_frontmatter(
    path: str,
    key: str,
    value: Any,
) -> str:
    """Atomically set one frontmatter key on a Memorant file.

    Use for pending_review increment/decrement (pass an int) and ADR status
    flip to 'superseded' when a newer ADR supersedes it.
    """
    try:
        safe = resolve_safe_path(path)
        if _is_journal_path(safe):
            return _deprecated(f"IMMUTABLE: journal event cannot be changed: {path}")
        if not os.path.exists(safe):
            return _deprecated(f"NOT_FOUND: {path}")
        with open(safe, "r", encoding="utf-8") as f:
            post = frontmatter.load(f)
        post[key] = value
        with open(safe, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))
        return _deprecated(f"updated {path}: {key}={value}")
    except PathForbiddenError as e:
        return _deprecated(f"PATH_FORBIDDEN: {e}")
    except Exception as e:
        return _deprecated(f"ERROR: {e}")


@mcp.tool(
    name="vault_delete_entry",
    annotations={
        "title": "Delete Memorant entry",
        "destructiveHint": True,
        "idempotentHint": True,
    },
)
async def vault_delete_entry(path: str, confirm: bool = False) -> str:
    """Delete a Memorant file. Used by the promote flow to remove a migrated
    daily line's source (when the line is the whole file) — typically you
    update the daily file instead. `confirm` must be true to proceed.
    """
    if not confirm:
        return _deprecated("REFUSED: pass confirm=true to delete")
    try:
        safe = resolve_safe_path(path)
        if _is_journal_path(safe):
            return _deprecated(f"IMMUTABLE: journal event cannot be changed: {path}")
        if not os.path.exists(safe):
            return _deprecated(f"NOT_FOUND: {path}")
        os.remove(safe)
        return _deprecated(f"deleted: {path}")
    except PathForbiddenError as e:
        return _deprecated(f"PATH_FORBIDDEN: {e}")
    except Exception as e:
        return _deprecated(f"ERROR: {e}")


@mcp.tool(
    name="vault_get_recent",
    annotations={
        "title": "Get recent vault entries",
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def vault_get_recent(dir: str, limit: int = 10) -> str:
    """Return the newest N files in a Memorant dir (bugs/snippets/daily/arch),
    by mtime. Use for daily review / pending_review triage.
    """
    try:
        root = vault_root()
        target = os.path.realpath(os.path.join(root, dir))
        if target != root and not target.startswith(root + os.sep):
            return _deprecated("PATH_FORBIDDEN: dir escapes MEMORANT_ROOT")
        if not os.path.isdir(target):
            return _deprecated(f"NOT_FOUND: {dir}")
        files = [
            os.path.join(dp, fn)
            for dp, _ds, fns in os.walk(target)
            for fn in fns
            if fn.endswith(".md")
        ]
        files.sort(key=os.path.getmtime, reverse=True)
        files = files[:limit]
        if not files:
            return _deprecated(f"no files in {dir}")
        lines = [f"- {os.path.relpath(p, root)}" for p in files]
        return _deprecated(f"{len(files)} recent in {dir}:\n" + "\n".join(lines))
    except Exception as e:
        return _deprecated(f"ERROR: {e}")


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
        return {"events": events, "count": len(events)}
    except Exception as e:
        return {"error": "ERROR", "message": str(e), "events": [], "count": 0}


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
