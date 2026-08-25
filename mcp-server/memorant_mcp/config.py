"""Memorant settings: memorant.settings.json + legacy flags/env compatibility."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_FLAG_DEFAULTS = {
    "auto_capture": True,
    "auto_write_verified": True,
    "auto_write_provisional": True,
    "event_recall": True,
    "activity_summary": True,
}

_PRESETS: dict[str, dict[str, str]] = {
    "rigorous": {
        "selectivity": "high",
        "voice": "mid",
        "guardrail": "high",
        "freshness": "mid",
    },
    "diligent": {
        "selectivity": "mid",
        "voice": "high",
        "guardrail": "mid",
        "freshness": "mid",
    },
    "silent": {
        "selectivity": "high",
        "voice": "low",
        "guardrail": "high",
        "freshness": "mid",
    },
}

_LEVELS = {"low", "mid", "high"}
_TONES = {"serious", "warm", "playful"}
_CLOUD = {"line_draft", "fuller_skeleton"}
_GATE_SKIP = {"session_aggregate", "notable_only", "off"}


@dataclass(frozen=True)
class MemorantFlags:
    auto_capture: bool = True
    auto_write_verified: bool = True
    auto_write_provisional: bool = True
    event_recall: bool = True
    activity_summary: bool = True


@dataclass(frozen=True)
class PersonaBehavior:
    preset: str = "rigorous"
    selectivity: str = "high"
    voice: str = "mid"
    guardrail: str = "high"
    freshness: str = "mid"


@dataclass(frozen=True)
class MemorantSettings:
    root: str | None = None
    flags: MemorantFlags = field(default_factory=MemorantFlags)
    hook_timeout_seconds: float | None = None
    cloud_projection: str = "line_draft"
    gate_skip_logging: str = "session_aggregate"
    behavior: PersonaBehavior = field(default_factory=PersonaBehavior)
    tone: str = "warm"


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
        if normalized not in _FLAG_DEFAULTS:
            continue
        found[normalized] = _parse_bool(
            value.strip().strip("\"'"), _FLAG_DEFAULTS[normalized]
        )
    return found


def _walk_claude_files(filename: str) -> list[Path]:
    """User file first, then nearest project file walking up from cwd."""
    home = Path.home()
    found: list[Path] = []
    user = home / ".claude" / filename
    if user.is_file():
        found.append(user)
    current = Path.cwd()
    while True:
        candidate = current / ".claude" / filename
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
        if current.resolve() == home.resolve():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return found


def _find_local_flag_config() -> dict[str, bool]:
    for filename in ("memorant.local.md",):
        for path in _walk_claude_files(filename):
            values = _read_frontmatter_bools(path)
            if values:
                return values
    return {}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_settings_json_merged() -> dict[str, Any]:
    """Merge user then project settings (project wins on conflicts)."""
    paths = _walk_claude_files("memorant.settings.json")
    # _walk returns user first then project ancestors; apply in order so later wins.
    # Prefer: user base, then nearest project (cwd's .claude) last.
    user = Path.home() / ".claude" / "memorant.settings.json"
    ordered: list[Path] = []
    if user.is_file():
        ordered.append(user)
    # project chain from root toward cwd so cwd wins
    chain: list[Path] = []
    current = Path.cwd()
    home = Path.home()
    while True:
        candidate = current / ".claude" / "memorant.settings.json"
        if candidate.is_file() and candidate != user:
            chain.append(candidate)
        if current.resolve() == home.resolve():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    ordered.extend(reversed(chain))
    merged: dict[str, Any] = {}
    for path in ordered:
        merged = _deep_merge(merged, _read_json_file(path))
    # Also accept any path discovered that we missed
    for path in paths:
        if path not in ordered:
            merged = _deep_merge(merged, _read_json_file(path))
    return merged


def _coerce_level(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in _LEVELS:
        return value.strip().lower()
    return default


def _behavior_from_dict(raw: dict[str, Any] | None) -> PersonaBehavior:
    data = raw if isinstance(raw, dict) else {}
    preset = data.get("preset", "rigorous")
    if not isinstance(preset, str) or preset not in {*_PRESETS, "custom"}:
        preset = "rigorous"
    base = dict(
        _PRESETS.get(preset if preset != "custom" else "rigorous", _PRESETS["rigorous"])
    )
    selectivity = _coerce_level(data.get("selectivity"), base["selectivity"])
    voice = _coerce_level(data.get("voice"), base["voice"])
    guardrail = _coerce_level(data.get("guardrail"), base["guardrail"])
    freshness = _coerce_level(data.get("freshness"), base["freshness"])
    if preset in _PRESETS:
        expected = _PRESETS[preset]
        if (
            selectivity != expected["selectivity"]
            or voice != expected["voice"]
            or guardrail != expected["guardrail"]
            or freshness != expected["freshness"]
        ):
            preset = "custom"
    else:
        preset = "custom"
    return PersonaBehavior(
        preset=preset,
        selectivity=selectivity,
        voice=voice,
        guardrail=guardrail,
        freshness=freshness,
    )


def _flags_from_sources(
    settings_system: dict[str, Any], local_md: dict[str, bool]
) -> MemorantFlags:
    values: dict[str, bool] = {}
    for name, default in _FLAG_DEFAULTS.items():
        env_key = f"MEMORANT_{name.upper()}"
        if env_key in os.environ:
            values[name] = _parse_bool(os.environ.get(env_key), default)
        elif name in settings_system and isinstance(
            settings_system[name], (bool, str, int)
        ):
            if isinstance(settings_system[name], bool):
                values[name] = settings_system[name]
            else:
                values[name] = _parse_bool(str(settings_system[name]), default)
        elif name in local_md:
            values[name] = local_md[name]
        else:
            values[name] = default
    return MemorantFlags(**values)


def load_settings() -> MemorantSettings:
    raw = _load_settings_json_merged()
    system = raw.get("system") if isinstance(raw.get("system"), dict) else {}
    privacy = raw.get("privacy") if isinstance(raw.get("privacy"), dict) else {}
    activity = raw.get("activity") if isinstance(raw.get("activity"), dict) else {}
    persona = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    local_md = _find_local_flag_config()

    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        root = None
    else:
        root = os.path.realpath(os.path.expanduser(root.strip()))

    timeout = system.get("hook_timeout_seconds")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = None

    cloud = privacy.get("cloud_projection", "line_draft")
    if cloud not in _CLOUD:
        cloud = "line_draft"
    gate_skip = activity.get("gate_skip_logging", "session_aggregate")
    if gate_skip not in _GATE_SKIP:
        gate_skip = "session_aggregate"

    behavior_raw = (
        persona.get("behavior") if isinstance(persona.get("behavior"), dict) else {}
    )
    tone = persona.get("tone", "warm")
    if not isinstance(tone, str) or tone not in _TONES:
        tone = "warm"

    return MemorantSettings(
        root=root,
        flags=_flags_from_sources(system, local_md),
        hook_timeout_seconds=timeout,
        cloud_projection=cloud,
        gate_skip_logging=gate_skip,
        behavior=_behavior_from_dict(behavior_raw),
        tone=tone,
    )


def load_flags() -> MemorantFlags:
    """Backward-compatible flag loader used by hooks and write gates."""
    return load_settings().flags


_INGEST_DEFAULTS: dict[str, Any] = {
    "max_bytes_file": 5 * 1024 * 1024,  # markdown/text 单文件
    "max_bytes_paste": 1 * 1024 * 1024,  # 粘贴文本
    "max_bytes_binary": 10 * 1024 * 1024,  # word/pdf 单文件
    "max_bytes_url": 5 * 1024 * 1024,  # url 抓取
    "extract_max_bytes": 100 * 1024,  # 提取纯文本缓存上限
    "url_timeout_seconds": 10.0,
    "source_allow_dirs": [],  # 本地文件额外白名单（默认仅 MEMORANT_ROOT）
    "batch_max_files": 1000,  # 批量目录导入数量上限（扫描阶段截断）
    "batch_max_bytes": 524288000,  # 批量总字节上限 500MB（仅统计成功落盘项）
    "batch_max_seconds": 300,  # 批量处理时间上限（秒）
}


def get_ingest_settings() -> dict[str, Any]:
    """资料投喂（ingest）配置节，缺省用默认值，不报错。

    source_allow_dirs 默认空 = 仅允许 MEMORANT_ROOT；resolve 白名单在
    source_docs._allowed_source_roots 里补入 MEMORANT_ROOT。
    """
    raw = _load_settings_json_merged()
    ingest = raw.get("ingest") if isinstance(raw.get("ingest"), dict) else {}
    return {
        **{k: v for k, v in _INGEST_DEFAULTS.items()},
        **{k: v for k, v in ingest.items() if k in _INGEST_DEFAULTS},
    }


def settings_root() -> str | None:
    """Root from settings.json only (no env)."""
    return load_settings().root


def persona_distill_guidance(settings: MemorantSettings | None = None) -> str:
    """Short host-facing guidance derived from persona (does not weaken constitution)."""
    cfg = settings or load_settings()
    b = cfg.behavior
    lines = [
        (
            f"Memorant persona: behavior={b.preset} "
            f"(selectivity={b.selectivity}, voice={b.voice}, "
            f"guardrail={b.guardrail}, freshness={b.freshness}); tone={cfg.tone}."
        ),
        (
            f"Privacy cloud_projection={cfg.cloud_projection}; "
            f"gate_skip_logging={cfg.gate_skip_logging}."
        ),
    ]
    if b.selectivity == "high":
        lines.append(
            "Selectivity high: prefer skip over thin/episodic cards; "
            "require work-general scene fingerprint; false forms worse than omission."
        )
    elif b.selectivity == "low":
        lines.append(
            "Selectivity low: capture more candidates, but still never promote "
            "impressions to usable truth without evidence+fingerprint."
        )
    if b.voice == "low":
        lines.append(
            "Voice low: prefer Activity over session interrupts for non-judicial notes."
        )
    elif b.voice == "high":
        lines.append(
            "Voice high: short-ask more readily on suspicious/dusty collisions "
            "(still one short question; decision stays with the user)."
        )
    if b.guardrail == "high":
        lines.append(
            "Guardrail high: on negative-trust / dusty hits, apply Agent denial "
            "rails early; keep Agent text neutral/structured."
        )
    if cfg.tone != "warm":
        lines.append(
            f"Human-facing tone={cfg.tone}; Agent injection stays neutral structured."
        )
    return " ".join(lines)


def with_behavior_override(
    settings: MemorantSettings, **levels: str
) -> MemorantSettings:
    """Test helper: return settings with custom behavior levels → custom preset."""
    behavior = replace(settings.behavior, preset="custom", **levels)
    return replace(settings, behavior=behavior)
