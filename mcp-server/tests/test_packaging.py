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


def test_repository_keeps_legacy_vault_compatibility_without_vault_mcp_entrypoint() -> (
    None
):
    """Legacy vault paths remain supported while the package uses Memorant tooling."""
    scan_files = [
        REPO_ROOT / "mcp-server" / "pyproject.toml",
        REPO_ROOT / ".claude-plugin" / "plugin.json",
    ]
    for base in (
        REPO_ROOT / "mcp-server" / "memorant_mcp",
        REPO_ROOT / "commands",
        REPO_ROOT / "hooks",
    ):
        if base.is_dir():
            scan_files.extend(
                path
                for path in base.rglob("*")
                if path.is_file() and path.suffix in {".py", ".md", ".sh"}
            )

    assert all(
        "vault-mcp" not in path.read_text(encoding="utf-8", errors="ignore")
        for path in scan_files
        if path.is_file()
    )


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
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
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
    for name in (
        "memorant_write_memory",
        "memorant_recall",
        "memorant_feedback",
        "memorant_promote",
    ):
        assert f"{TOOL_PREFIX}{name}" in content
