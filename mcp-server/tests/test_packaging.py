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
    # `uv run` may create a local lockfile; it must stay untracked/gitignored.
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


INGEST_TOOLS = [
    "memorant_ingest_source",
    "memorant_distill_source",
    "memorant_list_sources",
    "memorant_external_policy",
    "memorant_confirm_external",
    "memorant_govern_source",
    "memorant_confirm_promote",
]


def test_ingest_command_uses_plugin_scoped_tool_names() -> None:
    content = (REPO_ROOT / "commands" / "memorant-ingest.md").read_text()
    scoped = [f"{TOOL_PREFIX}{name}" for name in INGEST_TOOLS]
    allowed_line = "allowed-tools: " + ", ".join(scoped)
    assert allowed_line in content
    # 写入/召回/feedback/promote 也应在上面的白名单行里（追加在 INGEST_TOOLS 之后）。
    for name in ("memorant_write_memory", "memorant_recall", "memorant_feedback", "memorant_promote"):
        assert f"{TOOL_PREFIX}{name}" in content
