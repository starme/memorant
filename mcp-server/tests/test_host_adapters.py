"""Host adapter contract tests: three hosts' capabilities + degradation text."""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.host_adapters import (
    CLAUDE_CODE_CAPABILITIES,
    CODEX_CLI_CAPABILITIES,
    CODEX_CLOUD_CAPABILITIES,
    HostCapabilities,
    detect_host,
    get_adapter,
)
from memorant_mcp.host_adapters.base import HostAdapter


def _adapter_by_name(name: str) -> HostAdapter:
    return get_adapter(name)


def test_claude_code_has_full_capabilities() -> None:
    caps = CLAUDE_CODE_CAPABILITIES
    assert caps.event_capture is True
    assert caps.recall is True
    assert caps.auto_distill is True
    assert caps.promotion is True
    assert caps.activity is True


def test_codex_cli_event_capture_and_auto_distill_disabled() -> None:
    caps = CODEX_CLI_CAPABILITIES
    assert caps.event_capture is False
    assert caps.auto_distill is False
    # 其余能力仍可用（召回 / promotion / activity）。
    assert caps.recall is True
    assert caps.promotion is True
    assert caps.activity is True


def test_codex_cloud_event_capture_and_auto_distill_disabled() -> None:
    caps = CODEX_CLOUD_CAPABILITIES
    assert caps.event_capture is False
    assert caps.auto_distill is False
    assert caps.recall is True
    assert caps.promotion is True
    assert caps.activity is True


def test_capabilities_is_frozen_dataclass() -> None:
    caps = HostCapabilities(
        event_capture=True, recall=True, auto_distill=False, promotion=True, activity=True
    )
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        caps.event_capture = False  # type: ignore[misc]


def test_codex_cli_degradation_text_is_readable() -> None:
    adapter = _adapter_by_name("codex_cli")
    text = adapter.describe_degradation("event_capture")
    assert "memorant_append_event" in text
    text2 = adapter.describe_degradation("auto_distill")
    assert "memorant_write_memory" in text2


def test_codex_cloud_degradation_text_is_readable() -> None:
    adapter = _adapter_by_name("codex_cloud")
    text = adapter.describe_degradation("auto_distill")
    assert "memorant_write_memory" in text


def test_claude_code_no_degradation_for_full_ability() -> None:
    adapter = _adapter_by_name("claude_code")
    text = adapter.describe_degradation("event_capture")
    assert text == "" or "不支持" not in text


def test_detect_host_prefers_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/tmp/claude")
    monkeypatch.delenv("CODEX_CLI", raising=False)
    assert detect_host() == "claude_code"


def test_detect_host_codex_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CODEX_CLI", "1")
    assert detect_host() == "codex_cli"


def test_detect_host_unknown_degrades_to_readonly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 无任何可靠探测信号（无 CLAUDE_PLUGIN_ROOT、无 CODEX_CLI、无 ~/.codex/config.toml）
    # → 必须降级为只读宿主（codex_cloud），不可默认为 claude_code 的完整能力，
    # 否则 Codex 用户会误判已具备自动事件采集/主动提炼能力（契约 §3.4 / PRD §6）。
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CODEX_CLI", raising=False)
    # 隔离 ~/.codex/config.toml，避免宿主机真实状态干扰。
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = detect_host()
    assert result == "codex_cloud"
    # 降级后的能力语义：只读召回 + 手动写，无自动采集/提炼。
    adapter = get_adapter(result)
    assert adapter.capabilities.event_capture is False
    assert adapter.capabilities.auto_distill is False
    assert adapter.capabilities.recall is True


def test_detect_host_claude_code_has_full_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 明确 Claude Code 信号 → 完整能力。
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/tmp/claude")
    monkeypatch.delenv("CODEX_CLI", raising=False)
    result = detect_host()
    assert result == "claude_code"
    assert get_adapter(result).capabilities.auto_distill is True


def test_detect_host_codex_cli_keeps_readonly_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 明确 Codex CLI 信号 → 召回可用但无自动采集/提炼。
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CODEX_CLI", "1")
    result = detect_host()
    assert result == "codex_cli"
    assert get_adapter(result).capabilities.event_capture is False
    assert get_adapter(result).capabilities.auto_distill is False
