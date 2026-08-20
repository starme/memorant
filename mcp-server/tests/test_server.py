import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memorant_mcp.server import mcp


def test_server_has_no_legacy_vault_tools() -> None:
    assert mcp.name == "memorant"
    registered = asyncio.run(mcp.list_tools())
    names = [tool.name for tool in registered]
    for legacy in [
        "vault_search",
        "vault_create_entry",
        "vault_append_entry",
        "vault_update_frontmatter",
        "vault_delete_entry",
        "vault_get_recent",
    ]:
        assert legacy not in names
    for modern in [
        "memorant_append_event",
        "memorant_list_pending_events",
        "memorant_write_memory",
        "memorant_recall",
        "memorant_feedback",
        "memorant_promote",
        "memorant_activity",
        "memorant_migrate",
        "memorant_host_info",
    ]:
        assert modern in names


def test_append_event_tool_schema_exposes_enum_and_limits() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(item for item in tools if item.name == "memorant_append_event")
    properties = tool.parameters["properties"]

    assert properties["event_type"]["enum"] == [
        "session.start",
        "session.end",
        "context.precompact",
        "tool.failure",
        "test.failure",
        "test.success",
        "git.commit",
        "doc.commit",
        "decision.adopt",
    ]
    assert properties["session_id"]["minLength"] == 1
    assert properties["session_id"]["maxLength"] == 256
    assert properties["project"]["maxLength"] == 256
    assert properties["source"]["maxLength"] == 128
    assert properties["evidence_excerpt"]["anyOf"][0]["maxLength"] == 20_000
    assert properties["tags"]["anyOf"][0]["maxItems"] == 32
    assert properties["tags"]["anyOf"][0]["items"]["maxLength"] == 64


def test_migrate_tool_signature_has_confirm_and_dry_run() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(item for item in tools if item.name == "memorant_migrate")
    assert "dry_run" in tool.parameters["properties"]
    assert "confirm" in tool.parameters["properties"]


def test_host_info_tool_is_read_only() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(item for item in tools if item.name == "memorant_host_info")
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True


def test_installed_cli_entrypoints_smoke(tmp_path: Path) -> None:
    executable = Path(sys.executable).with_name("memorant-mcp")
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("MEMORANT_ROOT", None)

    result = subprocess.run(
        [str(executable)],
        input="",
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr



def test_list_pending_events_attaches_degradation_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from memorant_mcp.server import memorant_list_pending_events
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    monkeypatch.setenv("CODEX_CLI", "1")
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    result = asyncio.run(memorant_list_pending_events())
    assert "degradation_note" in result
    assert "手动调用" in result["degradation_note"]
