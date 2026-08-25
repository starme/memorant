"""Resolve stable project_key / label from cwd (Project Identity)."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectIdentity:
    project_key: str
    project_label: str
    aliases: tuple[str, ...] = ()


def _run_git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def normalize_git_remote(url: str) -> str:
    value = url.strip()
    value = re.sub(r"^git@", "", value)
    value = value.replace(":", "/", 1) if value.startswith("github.com:") else value
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^ssh://git@", "", value)
    value = value.removesuffix(".git")
    return value.strip("/")


def _key_from_normalized(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def resolve_project_identity(
    cwd: str | None, *, explicit_project: str | None = None
) -> ProjectIdentity:
    """Derive stable key; label prefers package/basename; aliases include basename."""
    path = Path(cwd).expanduser() if cwd else Path.cwd()
    try:
        path = path.resolve()
    except OSError:
        path = Path(cwd) if cwd else Path.cwd()

    basename = path.name[:256] if path.name else "unknown"
    aliases: list[str] = []
    if basename and basename != "unknown":
        aliases.append(basename)

    git_root = _run_git(path, "rev-parse", "--show-toplevel")
    remote = None
    if git_root:
        remote = _run_git(Path(git_root), "config", "--get", "remote.origin.url")
        root_name = Path(git_root).name
        if root_name and root_name not in aliases:
            aliases.append(root_name[:256])

    if remote:
        normalized = normalize_git_remote(remote)
        key = _key_from_normalized(normalized)
        label = (explicit_project or Path(normalized).name or basename)[:256]
        if explicit_project and explicit_project not in aliases:
            aliases.insert(0, explicit_project[:256])
        return ProjectIdentity(
            project_key=key, project_label=label, aliases=tuple(aliases)
        )

    if git_root:
        real = str(Path(git_root).resolve())
        key = _key_from_normalized(f"gitroot:{real}")
        label = (explicit_project or Path(git_root).name or basename)[:256]
        if explicit_project and explicit_project not in aliases:
            aliases.insert(0, explicit_project[:256])
        return ProjectIdentity(
            project_key=key, project_label=label, aliases=tuple(aliases)
        )

    label = (explicit_project or basename)[:256] or "unknown"
    key = _key_from_normalized(f"cwd:{path}")
    if explicit_project and explicit_project not in aliases:
        aliases.insert(0, explicit_project[:256])
    return ProjectIdentity(project_key=key, project_label=label, aliases=tuple(aliases))
