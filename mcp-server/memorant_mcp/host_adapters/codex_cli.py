"""Codex CLI host adapter: recall/promotion/activity, no auto event-capture/distill."""

from __future__ import annotations

import os
from pathlib import Path

from . import CODEX_CLI_CAPABILITIES, HostCapabilities
from .base import DegradationText, HostAdapter


class CodexCliAdapter(HostAdapter):
    name = "codex_cli"

    def detect(self) -> bool:
        # 契约 §3.4：CODEX_CLI 环境变量或 ~/.codex/config.toml 存在。
        if os.environ.get("CODEX_CLI"):
            return True
        try:
            return (Path.home() / ".codex" / "config.toml").is_file()
        except OSError:
            return False

    @property
    def capabilities(self) -> HostCapabilities:
        return CODEX_CLI_CAPABILITIES

    def describe_degradation(self, ability: str) -> str:
        # Codex CLI 无宿主事件钩子、无主动 Distill 注入——如实声明降级。
        if ability == "event_capture":
            return DegradationText.EVENT_CAPTURE
        if ability == "auto_distill":
            return DegradationText.AUTO_DISTILL
        return ""
