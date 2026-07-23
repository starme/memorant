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
from typing import Any, Optional

import frontmatter
from mcp.server.fastmcp import FastMCP

from .naming import (
    ConflictError,
    PathForbiddenError,
    ensure_dir,
    filename_for,
    next_arch_sequence,
    resolve_safe_path,
    vault_root,
)
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
        return f"VAULT_ERROR: {e}"
    if not results:
        return "no matches found"
    lines = [f"{r['file']}:{r['line']} — {r['match']}" for r in results]
    return f"{len(results)} match(es):\n" + "\n".join(lines)


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
        return f"created: {rel}"
    except PathForbiddenError as e:
        return f"PATH_FORBIDDEN: {e}"
    except ConflictError as e:
        return f"CONFLICT: {e}"
    except ValueError as e:
        return f"VALIDATION_ERROR: {e}"
    except Exception as e:
        return f"ERROR: {e}"


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
        if not os.path.exists(safe):
            return f"NOT_FOUND: {path}"
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
        return f"appended to: {path}"
    except PathForbiddenError as e:
        return f"PATH_FORBIDDEN: {e}"
    except Exception as e:
        return f"ERROR: {e}"


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
        if not os.path.exists(safe):
            return f"NOT_FOUND: {path}"
        with open(safe, "r", encoding="utf-8") as f:
            post = frontmatter.load(f)
        post[key] = value
        with open(safe, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))
        return f"updated {path}: {key}={value}"
    except PathForbiddenError as e:
        return f"PATH_FORBIDDEN: {e}"
    except Exception as e:
        return f"ERROR: {e}"


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
        return "REFUSED: pass confirm=true to delete"
    try:
        safe = resolve_safe_path(path)
        if not os.path.exists(safe):
            return f"NOT_FOUND: {path}"
        os.remove(safe)
        return f"deleted: {path}"
    except PathForbiddenError as e:
        return f"PATH_FORBIDDEN: {e}"
    except Exception as e:
        return f"ERROR: {e}"


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
            return "PATH_FORBIDDEN: dir escapes MEMORANT_ROOT"
        if not os.path.isdir(target):
            return f"NOT_FOUND: {dir}"
        files = [
            os.path.join(dp, fn)
            for dp, _ds, fns in os.walk(target)
            for fn in fns
            if fn.endswith(".md")
        ]
        files.sort(key=os.path.getmtime, reverse=True)
        files = files[:limit]
        if not files:
            return f"no files in {dir}"
        lines = [f"- {os.path.relpath(p, root)}" for p in files]
        return f"{len(files)} recent in {dir}:\n" + "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
