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

import hashlib
import os
import re as _re
from datetime import date
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Annotated, Any, Literal

import frontmatter
from fastmcp import FastMCP
from pydantic import Field

from .activity import append_activity, read_activity, session_end_summary
from .batch_ingest import batch_ingest_dir, scan_dir
from .event_schema import EventInput, EventType
from .external_policy import detect_directives, external_allowed, load_policy
from .governance import govern_source
from .hook_core import (
    append_event_data,
    bounded_evidence,
    redact_secrets,
    semantic_payload_hash,
)
from .host_adapters import detect_host, get_adapter
from .journal import append_event, list_pending_events
from .memory_schema import (
    EvidenceRef,
    LifecycleState,
    MemoryKind,
    MemoryScope,
    MemoryWriteInput,
    TrustTier,
)
from .memory_store import list_memories, read_memory, write_memory
from .migration import detect_legacy_data, migrate_legacy
from .naming import (
    ConflictError,
    PathForbiddenError,
    ensure_dir,
    filename_for,
    memorant_root,
    next_arch_sequence,
    resolve_safe_path,
    vault_root,
)
from .promotion import apply_feedback, promote_memory
from .recall import format_recall_context, recall_memories
from .schema import SCHEMA_BY_TYPE, EntryType
from .search import search as do_search
from .source_docs import (
    check_source_integrity,
    ingest_source,
    list_sources,
    read_source_doc,
)

mcp = FastMCP(
    "memorant",
    instructions=(
        "书童 · Memorant: local long-term memory runtime. Hooks capture "
        "deterministic journal events; the host Claude distills them into "
        "A/B Memory Envelopes (verified / provisional) with explicit trust "
        "fields; recall ranks memories by trust + tide. Markdown is the "
        "only source of truth. Legacy vault_* tools (bugs/snippets/daily/"
        "arch) remain as a compat layer for Obsidian-facing notes."
    ),
)


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
            raise ValueError(
                f"{entry_type} frontmatter invalid at '{loc}': {e0.get('msg')}"
            )
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
    dirs: list[str] | None = None,
    project: str | None = None,
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
    date_str: str | None = None,
    stack: list[str] | None = None,
    project: Any | None = None,
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
        elif (
            stack is not None and fm_stack is not None and list(stack) != list(fm_stack)
        ):
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
        elif project is not None and fm_project is not None and project != fm_project:
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
    section: str | None = None,
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
    tool_name: Annotated[str | None, Field(max_length=128)] = None,
    outcome: Annotated[str | None, Field(max_length=64)] = None,
    evidence_excerpt: Annotated[str | None, Field(max_length=20_000)] = None,
    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=64)]] | None,
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
    session_id: str | None = None,
    project: str | None = None,
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
    project: str | None = None,
    project_key: str | None = None,
    stack: list[str] | None = None,
    related: list[str] | None = None,
    supersedes: str | None = None,
    origin_session_ids: list[str] | None = None,
    body: str | None = None,
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
    project: str | None = None,
    trigger: str | None = None,
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
        Field(pattern=r"^(adopted|ignored|corrected|contradicted|successful_reuse)$"),
    ],
    note: str | None = None,
    session_id: str | None = None,
    replacement_claim: str | None = None,
    success_outcome: str | None = None,
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
        attention = "conflict" if action in {"corrected", "contradicted"} else "info"
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
    evidence_excerpt: str | None = None,
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
    day: str | None = None,
    project: str | None = None,
    attention_only: bool = False,
    include_session_summary: bool = False,
) -> dict[str, Any]:
    """Read non-blocking Memory Activity for a day/project."""
    try:
        payload = read_activity(day=day, project=project, attention_only=attention_only)
        if include_session_summary:
            payload["session_summary"] = session_end_summary(project=project)
        return payload
    except Exception as e:
        return {"error": "ERROR", "message": str(e), "entries": [], "count": 0}


# ── 资料落地记忆（Learning-Grounded Memory）工具 ──────────────────


def _synthetic_event_id(doc_id: str) -> str:
    return hashlib.sha256(f"source-doc:{doc_id}".encode()).hexdigest()[:32]


@mcp.tool(name="memorant_ingest_source")
async def memorant_ingest_source(
    kind: Literal["markdown", "text", "word", "pdf", "url", "paste"],
    content: str | None = None,
    path: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """留存一份资料（markdown/text/word/pdf/url/paste），返回 doc_id + content_sha256。原始资料只读留存，不提炼。"""
    return ingest_source(kind=kind, path=path, url=url, content=content)


@mcp.tool(name="memorant_list_sources")
async def memorant_list_sources(
    status: Literal["stored", "unparseable"] | None = None,
    source_type: str | None = None,
    project: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """列出已留存的资料（只读）。按 status/source_type/project 过滤。"""
    return list_sources(
        status=status, source_type=source_type, project=project, limit=limit
    )


@mcp.tool(
    name="memorant_check_source_integrity",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_check_source_integrity(doc_id: str) -> dict[str, Any]:
    """只读检测 source_docs/<doc_id> 是否被外部编辑器篡改（hash 变化）。

    只报告，不重写、不静默覆盖：读取 .md 记录的 content_sha256 与 .txt 实际
    内容重算的 sha256 比对，返回 intact / modified / not_detectable / NOT_FOUND。
    """
    try:
        return check_source_integrity(doc_id)
    except Exception as e:
        return {"error": "ERROR", "message": str(e), "doc_id": doc_id}


@mcp.tool(name="memorant_external_policy")
async def memorant_external_policy(
    allowed: bool = False,
    by_type: list[str] | None = None,
) -> dict[str, Any]:
    """查询/请求外发授权。allowed=false 返回当前策略（默认无外发）；allowed=true 要求宿主再次确认。"""
    policy = load_policy()
    types = [t for t in (by_type or []) if isinstance(t, str)]
    summary = {
        "enabled_sources": policy.enabled_sources,
        "prohibited_by_type": policy.prohibited_by_type,
        "default_allowed": False,
        "require_confirmation": policy.require_confirmation,
    }
    if not allowed:
        return {**summary, "external_used": False, "external_allowed": False}
    # allowed=true：真实外发必须宿主再次确认（强约束）。
    prompt_types = types or policy.enabled_sources
    return {
        **summary,
        "external_allowed": True,
        "prompt_required": True,
        "prompt_text": f"此资料将发送到外部服务，是否继续？涉及类型 {prompt_types}",
    }


@mcp.tool(name="memorant_confirm_external")
async def memorant_confirm_external(
    doc_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """逐次外发确认闸门。confirm=false 返回 REFUSED；confirm=true 返回单次授权范围。确认不可缓存、不可默认。"""
    if not confirm:
        return {"error": "REFUSED", "message": "未确认外发"}
    try:
        doc = read_source_doc(doc_id)
    except FileNotFoundError:
        return {"error": "NOT_FOUND", "doc_id": doc_id}
    return {
        "external_confirmed": True,
        "scope": {"doc_id": doc_id, "source_type": doc.get("source_type")},
    }


@mcp.tool(name="memorant_distill_source")
async def memorant_distill_source(
    doc_id: str,
    project: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    """准备提炼：外发判定 → 注入防护 → 合成 doc.commit 事件 → 返回脱敏提取文本。不做模型推理，提炼由宿主据 extracted_text 完成并复用 memorant_write_memory。

    external_allowed 与 external_would_be_used 是两个独立语义：
    - external_allowed：该 source_type 在策略层面是否被允许外发（enabled_sources
      命中 且 未被 prohibited_by_type 禁止 且 require_confirmation），可 true/false。
    - external_would_be_used：本次蒸馏是否真的会调用外部模型（实际外发动作），
      当前无外部模型调用路径，恒为 False。

    因此 external_allowed=True 与 external_would_be_used=False 不矛盾：前者说
    「这类资料策略上允许外发」，后者说「本次操作没有实际外发」。即便策略允许，
    真正外发仍须经 memorant_confirm_external 逐次确认后才会发生；未来接入外部
    模型时 external_would_be_used 应反映「确认后实际外发」的结果，而非仅被允许。
    """
    try:
        doc = read_source_doc(doc_id)
    except (FileNotFoundError, ValueError):
        return {"error": "NOT_FOUND", "doc_id": doc_id}

    if doc.get("status") == "unparseable":
        return {"error": "UNPARSEABLE", "doc_id": doc_id}

    source_type = doc.get("source_type", "")
    policy = load_policy()
    allowed = external_allowed(source_type, policy)
    prohibited = source_type in policy.prohibited_by_type

    extracted = doc.get("extracted_text") or ""
    contains_directives = detect_directives(extracted)
    safe_excerpt = redact_secrets(extracted)
    excerpt = bounded_evidence(safe_excerpt)

    synthetic_id = _synthetic_event_id(doc_id)
    event_data = {
        "event_type": "doc.commit",
        "session_id": "ingest",
        "project": project or doc.get("project") or "unknown",
        "source": "source-doc",
        "tool_name": None,
        "outcome": None,
        "evidence_excerpt": excerpt,
        "tags": ["source-doc", doc_id],
    }
    if project_key or doc.get("project_key"):
        key = project_key or doc.get("project_key")
        if isinstance(key, str) and _re.fullmatch(r"[0-9a-f]{16,64}", key):
            event_data["project_key"] = key
    event_data["event_id"] = synthetic_id
    event_data["observed_at"] = _dt.now(_tz.utc).isoformat().replace("+00:00", "Z")
    event_data["payload_hash"] = semantic_payload_hash(event_data)
    # 合成 doc.commit 事件按 payload_hash 去重，重复蒸馏命中同一事件（幂等）。
    append_event_data(event_data, root=vault_root())

    return {
        "doc_id": doc_id,
        "synthetic_event_id": synthetic_id,
        "external_would_be_used": False,
        "external_allowed": allowed,
        "prohibited_by_type": prohibited,
        "contains_directives": contains_directives,
        "extracted_text": safe_excerpt[:20_000],
        "memory_id": None,
    }


@mcp.tool(name="memorant_govern_source")
async def memorant_govern_source(
    doc_id: str,
    action: Literal["dedupe", "merge_candidate", "conflict", "archive"],
    related_memory_id: str | None = None,
    replacement_claim: str | None = None,
) -> dict[str, Any]:
    """治理去重/合并候选/冲突/归档。只改 memories/ 与 index/，不触碰 source_docs/。"""
    return govern_source(
        doc_id,
        action,
        related_memory_id=related_memory_id,
        replacement_claim=replacement_claim,
    )


@mcp.tool(name="memorant_confirm_promote")
async def memorant_confirm_promote(
    memory_id: str,
) -> dict[str, Any]:
    """资料经验用户显式确认 promotion（provisional → verified）。复用 update_memory_fields 写 trust_tier=verified + lifecycle=reinforced + 审计。"""
    from .memory_store import read_memory, update_memory_fields

    try:
        current = read_memory(memory_id)
    except (FileNotFoundError, ValueError):
        return {"error": "NOT_FOUND", "message": memory_id}
    if current.get("trust_tier") != "provisional":
        return {
            "error": "NOT_PROVISIONAL",
            "message": "only provisional memories can be confirmed",
            "path": current.get("path"),
        }

    # ── 证据校验（必须在 update_memory_fields 之前，杜绝其静默降级写坏） ──
    # 契约 §1.3：verified 证据约束 = evidence 非空 或 legacy_path 非空；
    # 资料经验记忆额外要求至少一条 source.startswith("source-doc:") 的证据。
    evidence = current.get("evidence") or []
    legacy_path = current.get("legacy_path")
    if not evidence and not legacy_path:
        return {
            "error": "VALIDATION_ERROR",
            "message": "verified memory requires evidence",
            "memory_id": memory_id,
        }
    has_source_doc = any(
        isinstance(e, dict) and str(e.get("source", "")).startswith("source-doc:")
        for e in evidence
    )
    if not has_source_doc and not legacy_path:
        return {
            "error": "VALIDATION_ERROR",
            "message": "source-doc memory requires at least one source-doc: evidence",
            "memory_id": memory_id,
        }

    updated = update_memory_fields(
        current["path"],
        {
            "trust_tier": "verified",
            "lifecycle": LifecycleState.reinforced.value,
        },
    )
    append_activity(
        "memory.confirm_promote",
        f"资料经验确认 promotion {memory_id}",
        memory_path=current["path"],
        attention="info",
    )
    return {
        "confirmed": True,
        "path": updated["path"],
        "memory_id": updated.get("memory_id"),
        "trust_tier": updated.get("trust_tier"),
        "lifecycle": updated.get("lifecycle"),
    }


@mcp.tool(name="memorant_batch_ingest_dir")
async def memorant_batch_ingest_dir(
    directory: str,
    dry_run: bool = False,
    types: list[str] | None = None,
    max_files: int | None = None,
    max_bytes: int | None = None,
    max_seconds: float | None = None,
    distill: bool = False,
) -> dict[str, Any]:
    """批量投喂一个目录下的 md/txt/docx/pdf 文件，复用单文件 ingest 的留存/幂等/hash 语义。

    dry_run=true 只读预览（不落盘、不写 journal/activity/memories）。
    逐项复用 ingest_source；单文件失败不阻断；目录级错误（越界/不存在）整体终止。
    数量/字节/时间三类上限超限置 stopped_reason 并停止后续；重跑幂等只补齐失败项。
    distill=true 仅对成功项做蒸馏准备阶段（外发判定+注入防护+合成 doc.commit 事件），
    不自动推理/promotion/governance；产物 trust_tier=provisional。
    """
    try:
        return batch_ingest_dir(
            directory=directory,
            dry_run=dry_run,
            types=types,
            max_files=max_files,
            max_bytes=max_bytes,
            max_seconds=max_seconds,
            distill=distill,
        )
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}
    except PermissionError as e:
        return {"error": "READ_FORBIDDEN", "message": str(e)}
    except FileNotFoundError as e:
        return {"error": "NOT_FOUND", "message": str(e)}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


@mcp.tool(
    name="memorant_batch_scan_dir",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def memorant_batch_scan_dir(
    directory: str,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """只读扫描目录，返回匹配文件清单（不落盘、不判定重复）。用于 dry-run 前预览。"""
    try:
        return scan_dir(directory, types=types)
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}
    except PermissionError as e:
        return {"error": "READ_FORBIDDEN", "message": str(e)}
    except FileNotFoundError as e:
        return {"error": "NOT_FOUND", "message": str(e)}
    except Exception as e:
        return {"error": "ERROR", "message": str(e)}


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
        return {
            "error": "ERROR",
            "message": "migration failed; see the local migration report",
        }
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
