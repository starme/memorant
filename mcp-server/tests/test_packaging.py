import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PREFIX = "mcp__plugin_memorant_memorant__"
COMMAND_TOOLS = {
    "search": ["vault_search", "vault_get_recent"],
    "log": ["vault_search", "vault_create_entry", "vault_get_recent"],
    "adr": [
        "vault_search",
        "vault_create_entry",
        "vault_update_frontmatter",
        "vault_get_recent",
    ],
}


def test_mcp_config_uses_claude_code_server_envelope() -> None:
    config = json.loads((REPO_ROOT / ".mcp.json").read_text())

    assert list(config) == ["mcpServers"]
    assert list(config["mcpServers"]) == ["memorant"]
    assert config["mcpServers"]["memorant"]["args"][-1] == "memorant-mcp"


@pytest.mark.parametrize("brand", ["memorant", "vault"])
@pytest.mark.parametrize("command", ["search", "log", "adr"])
def test_command_uses_plugin_scoped_mcp_tool_names(
    brand: str, command: str
) -> None:
    content = (REPO_ROOT / "commands" / f"{brand}-{command}.md").read_text()
    scoped = [f"{TOOL_PREFIX}{name}" for name in COMMAND_TOOLS[command]]
    allowed_line = "allowed-tools: " + ", ".join(scoped)

    assert allowed_line in content
    for name in scoped:
        assert content.count(name) >= 2


def test_marketplace_has_strict_validation_description() -> None:
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )

    assert marketplace["description"].strip()


def test_repository_does_not_commit_environment_specific_uv_lock() -> None:
    assert not (REPO_ROOT / "mcp-server" / "uv.lock").exists()


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
        assert commands == ["${CLAUDE_PLUGIN_ROOT}/hooks/memorant-hook.sh"]
