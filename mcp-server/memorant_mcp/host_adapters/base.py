"""HostAdapter abstract base + degradation text contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注，避免与包 __init__ 循环导入
    from . import HostCapabilities


class HostAdapter(ABC):
    """宿主适配器抽象基类。每个宿主实现 detect / capabilities / describe_degradation。"""

    name: str = "base"

    @abstractmethod
    def detect(self) -> bool:
        """返回当前环境是否为本宿主（探测信号见契约 §3.4）。"""

    @property
    @abstractmethod
    def capabilities(self) -> HostCapabilities:
        """本宿主的能力矩阵。"""

    @abstractmethod
    def describe_degradation(self, ability: str) -> str:
        """返回一致、可读的能力降级文案；能力完整时返回空串。"""


class DegradationText:
    """统一的能力降级文案模板（契约 §3.2）。"""

    EVENT_CAPTURE = "当前宿主不支持自动事件采集，请手动调用 `memorant_append_event`。"
    AUTO_DISTILL = (
        "当前宿主不支持自动提炼，请手动调用 `memorant_list_pending_events` "
        "与 `memorant_write_memory`。"
    )
