import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PREFIX = "mcp__plugin_memorant_memorant__"
COMMAND_TOOLS = {
    "search": ["memorant_recall"],
    "log": [
        "memorant_append_event",
        "memorant_list_pending_events",
        "memorant_write_memory",
    ],
    "adr": [
        "memorant_append_event",
        "memorant_write_memory",
    ],
}


def test_mcp_config_uses_claude_code_server_envelope() -> None:
    config = json.loads((REPO_ROOT / ".mcp.json").read_text())

    assert list(config) == ["mcpServers"]
    assert list(config["mcpServers"]) == ["memorant"]
    assert config["mcpServers"]["memorant"]["args"][-1] == "memorant-mcp"


@pytest.mark.parametrize("command", ["search", "log", "adr"])
def test_command_uses_plugin_scoped_mcp_tool_names(command: str) -> None:
    content = (REPO_ROOT / "commands" / f"memorant-{command}.md").read_text()
    scoped = [f"{TOOL_PREFIX}{name}" for name in COMMAND_TOOLS[command]]
    allowed_line = "allowed-tools: " + ", ".join(scoped)

    assert allowed_line in content
    for name in scoped:
        assert content.count(name) >= 2


def test_no_vault_named_tools_or_commands_in_repo() -> None:
    """CI 断言：源码/配置不再存在 vault_* 工具 / 命令 / entry point 功能引用。

    只扫描源码与配置，排除 tests/（测试里必然有「断言 vault 不存在」的否定式字符串）。
    """
    offenders: list[str] = []
    scan_roots = (REPO_ROOT / "mcp-server" / "memorant_mcp", REPO_ROOT / "commands", REPO_ROOT / "hooks")
    scan_files = [REPO_ROOT / "mcp-server" / "pyproject.toml", REPO_ROOT / ".claude-plugin" / "plugin.json"]
    for base in scan_roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".md", ".sh"}:
                scan_files.append(path)
    for path in scan_files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "vault_" in text or "vault-mcp" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"residual vault references: {offenders}"


def test_marketplace_has_strict_validation_description() -> None:
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )

    assert marketplace["description"].strip()


def test_repository_does_not_commit_environment_specific_uv_lock() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "/mcp-server/uv.lock" in gitignore
    tracked = __import__("subprocess").run(
        ["git", "ls-files", "--error-unmatch", "mcp-server/uv.lock"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert tracked.returncode != 0


def test_plugin_registers_journal_observer_hooks() -> None:
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    expected = {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUseFailure",
        "PostToolUse",
        "PreCompact",
        "SessionEnd",
    }
    assert expected.issubset(plugin["hooks"])
    for event in expected:
        entries = plugin["hooks"][event]
        commands = [
            hook["command"]
            for entry in entries
            for hook in entry["hooks"]
        ]
        if event == "PostToolUse":
            assert commands == [
                "${CLAUDE_PLUGIN_ROOT}/hooks/memorant-hook.sh",
                "${CLAUDE_PLUGIN_ROOT}/hooks/on-git-commit.sh",
            ]
        else:
            assert commands == ["${CLAUDE_PLUGIN_ROOT}/hooks/memorant-hook.sh"]
