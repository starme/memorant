"""Codex cloud/IDE host adapter: read-only recall + manual write only."""

from __future__ import annotations

from . import CODEX_CLOUD_CAPABILITIES, HostCapabilities
from .base import DegradationText, HostAdapter


class CodexCloudAdapter(HostAdapter):
    name = "codex_cloud"

    def detect(self) -> bool:
        # 契约 §3.4：Codex 云端/IDE 平台无可靠探测信号，默认不主动判定——
        # 探测不稳定时降级为只读并提示，不硬判。这里返回 False，由 detect_host
        # 的探测链兜底；显式按 codex_cloud 接入时由 get_adapter 指定。
        return False

    @property
    def capabilities(self) -> HostCapabilities:
        return CODEX_CLOUD_CAPABILITIES

    def describe_degradation(self, ability: str) -> str:
        if ability == "event_capture":
            return DegradationText.EVENT_CAPTURE
        if ability == "auto_distill":
            return DegradationText.AUTO_DISTILL
        return ""
