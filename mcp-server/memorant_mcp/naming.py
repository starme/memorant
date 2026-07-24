"""Filename generation and path-safety enforcement.

`resolve_safe_path` is the security boundary: every write goes through it.
MCP server file I/O bypasses Claude's Edit/Write tools (and thus the user's
protect-files.sh hook), so the server must enforce its own configured root
whitelist here to prevent path traversal.
"""

from __future__ import annotations

import os
import re
from datetime import date

from .hook_core import discover_root
from .schema import EntryType


class PathForbiddenError(Exception):
    """Raised when a resolved path escapes the configured Memorant root."""


class ConflictError(Exception):
    """Raised when a target file already exists."""


def vault_root() -> str:
    """Resolve the Memorant root while preserving Vault compatibility.

    Priority: MEMORANT_ROOT > memorant.local.md > VAULT_ROOT > vault.local.md.
    """
    try:
        return discover_root()
    except RuntimeError:
        raise RuntimeError(
            "MEMORANT_ROOT is not set. Configure MEMORANT_ROOT or "
            ".claude/memorant.local.md; legacy VAULT_ROOT and "
            ".claude/vault.local.md remain supported."
        )


def resolve_safe_path(rel_path: str) -> str:
    """Resolve a relative path against the configured root, refusing traversal.

    Accepts forward slashes. After realpath, the result must start with
    the root plus a separator — anything else is path_forbidden.
    """
    root = vault_root()
    # Join then realpath; realpath collapses '..' and symlinks.
    candidate = os.path.realpath(os.path.join(root, rel_path))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise PathForbiddenError(
            f"path escapes MEMORANT_ROOT: {rel_path!r} -> {candidate}"
        )
    return candidate


def _slug(text: str, max_len: int = 40) -> str:
    """Filename-safe slug preserving Unicode letters/digits (CJK stays readable).

    Why: titles are often Chinese; the old ASCII-only whitelist stripped every
    CJK char to empty and fell back to the uninformative 'untitled'. Treat
    filesystem-illegal chars and runs of non-word chars as separators instead,
    so '限流治理：漏斗模型' -> '限流治理-漏斗模型' and 'PHP/Laravel' -> 'php-laravel'.
    """
    # Filesystem-illegal chars + control chars act as separators (not deleted),
    # so 'PHP/Laravel' -> 'php-laravel' not 'PHPLaravel'.
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", text)
    # Runs of non-word chars (punctuation, spaces, CJK punctuation like ：) -> '-'.
    s = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-").lower()
    return s[:max_len] or "untitled"


def filename_for(
    entry_type: EntryType,
    date_str: str,
    title: str,
    *,
    stack: list[str] | None = None,
    project: str | list[str] | None = None,
    sequence: int | None = None,
) -> str:
    """Build the filename (relative to its dir) per the naming rules.

    - bug:      bugs/{stack}-{短描述}-{YYYYMMDD}.md
    - snippet:  snippets/{场景}-{技术栈}.md
    - daily:    daily/{YYYY-MM-DD}.md
    - arch:     arch/adr-{序号}-{项目}-{短描述}.md
    """
    if entry_type == EntryType.bug:
        stack_part = _slug("-".join(stack or ["misc"]))
        desc = _slug(title)
        return f"bugs/{stack_part}-{desc}-{date_str.replace('-', '')}.md"

    if entry_type == EntryType.snippet:
        # `where` is the scenario; stack is the tech. Caller passes them
        # pre-slug'd via title (scenario) and stack.
        scenario = _slug(title)
        tech = _slug("-".join(stack or ["misc"]))
        return f"snippets/{scenario}-{tech}.md"

    if entry_type == EntryType.daily:
        return f"daily/{date_str}.md"

    if entry_type == EntryType.arch:
        if sequence is None:
            raise ValueError("arch filename requires a sequence number")
        proj = _slug(project if isinstance(project, str) else (project[0] if project else "misc"))
        desc = _slug(title)
        return f"arch/adr-{sequence:03d}-{proj}-{desc}.md"

    raise ValueError(f"unknown entry type: {entry_type}")


def ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def next_arch_sequence() -> int:
    """Atomically read-increment-write arch/.sequence under flock."""
    import fcntl

    seq_path = os.path.join(vault_root(), "arch", ".sequence")
    os.makedirs(os.path.dirname(seq_path), exist_ok=True)
    # Touch if missing
    if not os.path.exists(seq_path):
        with open(seq_path, "w") as f:
            f.write("0")
    with open(seq_path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            n = int(f.read().strip() or "0")
            n += 1
            f.seek(0)
            f.write(str(n))
            f.truncate()
            return n
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
