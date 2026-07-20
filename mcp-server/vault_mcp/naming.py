"""Filename generation and path-safety enforcement.

`resolve_safe_path` is the security boundary: every write goes through it.
MCP server file I/O bypasses Claude's Edit/Write tools (and thus the user's
protect-files.sh hook), so the server must enforce its own $VAULT_ROOT whitelist
here to prevent path traversal.
"""

from __future__ import annotations

import os
import re
from datetime import date

from .schema import EntryType


class PathForbiddenError(Exception):
    """Raised when a resolved path escapes $VAULT_ROOT."""


class ConflictError(Exception):
    """Raised when a target file already exists."""


def vault_root() -> str:
    root = os.environ.get("VAULT_ROOT", "").strip()
    if not root:
        raise RuntimeError(
            "VAULT_ROOT is not set. Configure it via env var or .claude/vault.local.md."
        )
    return os.path.realpath(root)


def resolve_safe_path(rel_path: str) -> str:
    """Resolve a relative path against $VAULT_ROOT, refusing traversal.

    Accepts forward slashes. After realpath, the result must start with
    $VAULT_ROOT + separator — anything else is path_forbidden.
    """
    root = vault_root()
    # Join then realpath; realpath collapses '..' and symlinks.
    candidate = os.path.realpath(os.path.join(root, rel_path))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise PathForbiddenError(
            f"path escapes VAULT_ROOT: {rel_path!r} -> {candidate}"
        )
    return candidate


def _slug(text: str, max_len: int = 40) -> str:
    """Lowercase ASCII slug, non-alphanumerics -> '-'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len] or "untitled"


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
