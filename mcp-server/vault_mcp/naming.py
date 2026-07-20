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


def _read_vault_root_from(path: str) -> str | None:
    """Read VAULT_ROOT from the frontmatter of a vault.local.md file."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    fm = content[3:end]
    for line in fm.splitlines():
        if line.strip().lower().startswith("vault_root:"):
            val = line.split(":", 1)[1].strip()
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            return val if val else None
    return None


def _find_vault_local() -> str | None:
    """Find VAULT_ROOT from a `vault.local.md` plugin-settings file.

    Search order:
      1. ~/.claude/vault.local.md  (user-level — checked first so a global
         setting works regardless of CWD; the MCP server's CWD is often ~/.claude
         itself, where the project-level walk below would miss it)
      2. walk up from CWD looking for .claude/vault.local.md (project-level)

    Returns None if neither exists. Fallback when the VAULT_ROOT env var isn't
    set (e.g. the MCP subprocess didn't inherit zprofile exports).
    """
    home = os.path.expanduser("~")
    # 1. user-level config
    user_cfg = os.path.join(home, ".claude", "vault.local.md")
    if os.path.isfile(user_cfg):
        val = _read_vault_root_from(user_cfg)
        if val:
            return val
    # 2. project-level walk-up from CWD
    cwd = os.getcwd()
    while True:
        candidate = os.path.join(cwd, ".claude", "vault.local.md")
        if os.path.isfile(candidate):
            val = _read_vault_root_from(candidate)
            if val:
                return val
        if os.path.realpath(cwd) == os.path.realpath(home):
            break
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return None


def vault_root() -> str:
    """Resolve $VAULT_ROOT from env var, falling back to .claude/vault.local.md.

    The env var wins when set (e.g. in CI or an explicitly-configured shell).
    The local.md fallback handles the common case where the MCP server subprocess
    doesn't inherit the login shell's zprofile exports.
    """
    root = os.environ.get("VAULT_ROOT", "").strip()
    if not root:
        root = (_find_vault_local() or "").strip()
    if not root:
        raise RuntimeError(
            "VAULT_ROOT is not set. Configure it via the VAULT_ROOT env var, "
            "or create .claude/vault.local.md with `VAULT_ROOT: /path/to/vault` "
            "in its frontmatter (searched upward from the working directory)."
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
