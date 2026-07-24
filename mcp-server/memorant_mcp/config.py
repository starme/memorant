"""Memorant feature flags from env and local markdown config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# Defaults for the first compat release: capture/recall/auto-write on.
# Disable via MEMORANT_<FLAG> or .claude/memorant.local.md frontmatter.
_DEFAULTS = {
    "auto_capture": True,
    "auto_write_verified": True,
    "auto_write_provisional": True,
    "event_recall": True,
    "activity_summary": True,
}


@dataclass(frozen=True)
class MemorantFlags:
    auto_capture: bool = True
    auto_write_verified: bool = True
    auto_write_provisional: bool = True
    event_recall: bool = True
    activity_summary: bool = True


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _read_frontmatter_bools(path: Path) -> dict[str, bool]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    found: dict[str, bool] = {}
    for line in text[3:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = key.strip().lower()
        if normalized not in _DEFAULTS:
            continue
        found[normalized] = _parse_bool(value.strip().strip("\"'"), _DEFAULTS[normalized])
    return found


def _find_local_config() -> dict[str, bool]:
    home = Path.home()
    for filename in ("memorant.local.md", "vault.local.md"):
        candidate = home / ".claude" / filename
        values = _read_frontmatter_bools(candidate)
        if values:
            return values
        current = Path.cwd()
        while True:
            values = _read_frontmatter_bools(current / ".claude" / filename)
            if values:
                return values
            if current.resolve() == home.resolve():
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
    return {}


def load_flags() -> MemorantFlags:
    local = _find_local_config()
    values: dict[str, bool] = {}
    for name, default in _DEFAULTS.items():
        env_key = f"MEMORANT_{name.upper()}"
        if env_key in os.environ:
            values[name] = _parse_bool(os.environ.get(env_key), default)
        elif name in local:
            values[name] = local[name]
        else:
            values[name] = default
    return MemorantFlags(**values)
