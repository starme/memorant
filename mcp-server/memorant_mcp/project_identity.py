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
    # Which rule produced this identity. Audit trail for "why did this event
    # land under this project?" — without it a marker-derived key and a
    # cwd-derived key that merely look alike are indistinguishable.
    source: str = "cwd"


WORKSPACE_MARKERS = (
    (Path(".devflow") / "project.yaml", "workspace_marker"),
    (Path(".memorant") / "project", "workspace_marker"),
)


def _project_name_from_yaml(path: Path) -> str | None:
    """Read ``project.name`` from the minimal YAML shape without a YAML dep."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    in_project = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_project = line.strip().rstrip(":") == "project"
            continue
        if not in_project:
            continue
        key, separator, value = line.partition(":")
        if separator and key.strip() == "name":
            return value.strip().strip("\"'")[:256] or None
    return None


def _project_name_from_text(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return lines[0].strip()[:256] if lines and lines[0].strip() else None


def find_workspace_marker(
    path: Path, *, stop_at: Path | None = None
) -> tuple[str, Path] | None:
    """Walk up from ``path`` for a workspace marker.

    The walk is deliberately unbounded: a repository nested inside a workspace
    (project root holds ``.devflow/project.yaml``, the real git repo lives in a
    subdirectory) is exactly the split this resolver exists to prevent, so the
    outer marker must still win. Separation between unrelated projects is
    guaranteed by the marker itself — only a workspace that declares its name
    claims the cwds beneath it.
    """
    current = path
    while True:
        for relative, _source in WORKSPACE_MARKERS:
            candidate = current / relative
            if not candidate.is_file():
                continue
            name = (
                _project_name_from_yaml(candidate)
                if candidate.suffix == ".yaml"
                else _project_name_from_text(candidate)
            )
            if name:
                return name, candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


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
    """Derive a stable key and label for the logical project behind ``cwd``.

    Key precedence: explicit name > workspace marker > git remote > git root >
    cwd.

    Before this, the git rules were independent, so moving the cwd from a
    workspace root into a nested repository (or renaming a remote) minted a new
    key for the same project and split its history. The explicit name also
    touched only the label, never the key, so naming the project did not help.

    The marker walk is unbounded on purpose: a workspace that declares its name
    in ``.devflow/project.yaml`` / ``.memorant/project`` owns every path under
    it, including nested repositories. Unrelated projects stay separate because
    only a workspace that opted in carries a marker; anywhere else falls back
    to the git-derived rules.
    """
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

    explicit = (explicit_project or "").strip()[:256] or None
    marker = find_workspace_marker(path)
    marker_name = marker[0] if marker else None

    remote = None
    if git_root:
        remote = _run_git(Path(git_root), "config", "--get", "remote.origin.url")
        root_name = Path(git_root).name
        if root_name and root_name not in aliases:
            aliases.append(root_name[:256])

    for name in (explicit, marker_name):
        if name and name not in aliases:
            aliases.insert(0, name)

    if explicit:
        return ProjectIdentity(
            project_key=_key_from_normalized(f"project:{explicit}"),
            project_label=explicit,
            aliases=tuple(aliases),
            source="explicit",
        )

    if marker_name:
        return ProjectIdentity(
            project_key=_key_from_normalized(f"project:{marker_name}"),
            project_label=marker_name,
            aliases=tuple(aliases),
            source="workspace_marker",
        )

    if remote:
        normalized = normalize_git_remote(remote)
        return ProjectIdentity(
            project_key=_key_from_normalized(normalized),
            project_label=(Path(normalized).name or basename)[:256],
            aliases=tuple(aliases),
            source="git_remote",
        )

    if git_root:
        real = str(Path(git_root).resolve())
        return ProjectIdentity(
            project_key=_key_from_normalized(f"gitroot:{real}"),
            project_label=(Path(git_root).name or basename)[:256],
            aliases=tuple(aliases),
            source="git_root",
        )

    return ProjectIdentity(
        project_key=_key_from_normalized(f"cwd:{path}"),
        project_label=basename or "unknown",
        aliases=tuple(aliases),
        source="cwd",
    )
