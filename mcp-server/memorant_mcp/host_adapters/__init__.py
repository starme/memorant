"""Host adapter layer: capability matrix + host adapters.

通用核心（journal/memory_store/recall/promotion/activity/migration）不得 import
本包；本包可以 import 核心。宿主能力差异在此显式表达（PRD §7.1，验收标准 16）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostCapabilities:
    event_capture: bool  # 自动事件采集（宿主事件钩子）
    recall: bool  # 召回
    auto_distill: bool  # 自动提炼（主动 Distill 注入）
    promotion: bool  # A/B promotion
    activity: bool  # Activity 审计


# 三宿主能力值（契约 §3.3，文档与代码注释须一致）。
# Codex CLI / 云端的 event_capture 与 auto_distill 必须为 false——不伪装完整自动能力。
CLAUDE_CODE_CAPABILITIES = HostCapabilities(
    event_capture=True, recall=True, auto_distill=True, promotion=True, activity=True
)
CODEX_CLI_CAPABILITIES = HostCapabilities(
    event_capture=False, recall=True, auto_distill=False, promotion=True, activity=True
)
CODEX_CLOUD_CAPABILITIES = HostCapabilities(
    event_capture=False, recall=True, auto_distill=False, promotion=True, activity=True
)

from .base import HostAdapter
from .claude_code import ClaudeCodeAdapter
from .codex_cli import CodexCliAdapter
from .codex_cloud import CodexCloudAdapter

_ADAPTERS: dict[str, HostAdapter] = {
    "claude_code": ClaudeCodeAdapter(),
    "codex_cli": CodexCliAdapter(),
    "codex_cloud": CodexCloudAdapter(),
}


def get_adapter(name: str) -> HostAdapter:
    """返回指定宿主名的适配器；未知宿主按只读降级（codex_cloud）。"""
    return _ADAPTERS.get(name, _ADAPTERS["codex_cloud"])


def detect_host() -> str:
    """探测当前宿主，按契约 §3.4 信号判定；探测不稳定时降级为只读。

    无可靠探测信号时返回 codex_cloud（只读召回 + 手动写，无自动采集/提炼），
    而非默认 claude_code 的完整能力——避免 Codex 用户误判已具备自动能力
    （PRD §6 风险缓解：Codex 云端/IDE 探测不稳定 → 降级只读并提示）。
    """
    for name, adapter in _ADAPTERS.items():
        if adapter.detect():
            return name
    # codex_cloud.detect() 恒为 False（不主动判定），故探测链兜底到只读降级。
    return "codex_cloud"
