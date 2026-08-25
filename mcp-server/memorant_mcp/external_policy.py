"""外发策略（默认全禁）与注入防护标记。

- load_policy() 读 $MEMORANT_ROOT/external_source_policy.yaml，缺省=全禁。
- external_allowed(source_type) 纯函数：enabled AND NOT prohibited AND require_confirmation。
- default_allowed 字段仅作显式声明，代码恒按 false 处理。
- prohibited_by_type 优先于 enabled_sources。
- detect_directives(text) 只标记强指令特征，不阻断留存，不执行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .naming import vault_root


@dataclass(frozen=True)
class ExternalPolicy:
    enabled_sources: list[str] = field(default_factory=list)
    prohibited_by_type: list[str] = field(default_factory=list)
    # 恒按 false 语义处理；字段保留仅为显式声明与可读性。
    default_allowed: bool = False
    require_confirmation: bool = True


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str)]
    return []


def policy_from_dict(raw: dict[str, Any] | None) -> ExternalPolicy:
    data = raw if isinstance(raw, dict) else {}
    enabled = _as_list(data.get("enabled_sources"))
    prohibited = _as_list(data.get("prohibited_by_type"))
    # require_confirmation 恒 true：配置缺失或显式 false 都不允许绕过逐次确认。
    require_confirmation = True
    return ExternalPolicy(
        enabled_sources=enabled,
        prohibited_by_type=prohibited,
        default_allowed=False,
        require_confirmation=require_confirmation,
    )


def load_policy() -> ExternalPolicy:
    root = Path(vault_root())
    path = root / "external_source_policy.yaml"
    if not path.is_file():
        return ExternalPolicy()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ExternalPolicy()
    if not isinstance(data, dict):
        return ExternalPolicy()
    external = data.get("external_sources")
    if not isinstance(external, dict):
        return ExternalPolicy()
    return policy_from_dict(external)


def external_allowed(source_type: str, policy: ExternalPolicy | None = None) -> bool:
    """判定某类型是否允许外发（缺省=全禁，default_allowed 恒 false）。"""
    p = policy or load_policy()
    enabled = source_type in p.enabled_sources
    prohibited = source_type in p.prohibited_by_type
    # default_allowed 恒 false：不信任配置可能写 true。
    _ = p.default_allowed
    return enabled and not prohibited and p.require_confirmation


# ── 注入防护 ──────────────────────────────────────────────
# 强指令特征（仅标记，不执行）。中英双语覆盖常见提示词注入句式。
_DIRECTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"请执行"),
    re.compile(r"忽略(上述|之前|以上)?指令"),
    re.compile(r"你现在的任务是"),
    re.compile(r"不要(遵循|遵守).*(指令|规则)"),
    re.compile(
        r"ignore\s+(the\s+|all\s+)?(previous|prior|above|earlier)\s+instructions?",
        re.IGNORECASE,
    ),
    re.compile(r"you\s+are\s+now\s+.*(task|instructed)", re.IGNORECASE),
    re.compile(r"disregard\s+.*(instruction|rule)", re.IGNORECASE),
]


def detect_directives(text: str | None) -> bool:
    """检测强指令特征，返回是否含指令性内容（仅标记，不阻断留存/提取）。"""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _DIRECTIVE_PATTERNS)
