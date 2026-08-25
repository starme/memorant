"""Claude Code host adapter: full capabilities (Hooks 全链路 + Skill + MCP)."""

from __future__ import annotations

import os

from . import CLAUDE_CODE_CAPABILITIES, HostCapabilities
from .base import HostAdapter


class ClaudeCodeAdapter(HostAdapter):
    name = "claude_code"

    def detect(self) -> bool:
        # Claude Code 通过 CLAUDE_PLUGIN_ROOT 环境变量标识（契约 §3.4）。
        return bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))

    @property
    def capabilities(self) -> HostCapabilities:
        return CLAUDE_CODE_CAPABILITIES

    def describe_degradation(self, ability: str) -> str:
        # 四类能力全链路可用，无降级。
        return ""
