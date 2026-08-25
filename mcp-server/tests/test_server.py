import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from memorant_mcp.server import (
    _DEPRECATED_PREFIX,
    mcp,
    vault_create_entry,
)


def _created_path(result: str) -> str:
    assert result.startswith(_DEPRECATED_PREFIX)
    body = result.removeprefix(_DEPRECATED_PREFIX)
    assert body.startswith("created: ")
    return body.removeprefix("created: ")


def test_stack_syncs_from_top_level_to_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    result = asyncio.run(
        vault_create_entry(
            type="bug",
            title="连接超时",
            body="# body",
            frontmatter={"version": {"python": "3.12"}, "status": "resolved"},
            date_str="2026-07-23",
            stack=["Python"],
        )
    )

    path = _created_path(result)
    assert path == "bugs/python-连接超时-20260723.md"
    assert "stack:\n- Python" in (tmp_path / path).read_text()


def test_stack_syncs_from_frontmatter_to_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    result = asyncio.run(
        vault_create_entry(
            type="bug",
            title="连接重置",
            body="# body",
            frontmatter={
                "stack": ["Go"],
                "version": {"go": "1.24"},
                "status": "resolved",
            },
            date_str="2026-07-23",
        )
    )

    assert _created_path(result) == "bugs/go-连接重置-20260723.md"


def test_project_syncs_from_top_level_to_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    result = asyncio.run(
        vault_create_entry(
            type="bug",
            title="project sync",
            body="# body",
            frontmatter={
                "stack": ["Python"],
                "version": {"python": "3.12"},
                "status": "resolved",
            },
            project="Memorant",
            date_str="2026-07-23",
        )
    )

    content = (tmp_path / _created_path(result)).read_text()
    assert "project: Memorant" in content


def test_project_syncs_from_frontmatter_to_arch_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    result = asyncio.run(
        vault_create_entry(
            type="arch",
            title="选择本地优先存储",
            body="# body",
            frontmatter={
                "project": "Memorant",
                "status": "active",
                "decision_by": ["tal"],
            },
            date_str="2026-07-23",
        )
    )

    assert _created_path(result) == "arch/adr-001-memorant-选择本地优先存储.md"


@pytest.mark.parametrize(
    ("top_level", "frontmatter", "expected"),
    [
        ({"stack": ["Go"]}, {"stack": ["Python"]}, "stack mismatch"),
        ({"project": "one"}, {"project": "two"}, "project mismatch"),
    ],
)
def test_dual_channel_mismatch_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    top_level: dict,
    frontmatter: dict,
    expected: str,
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    result = asyncio.run(
        vault_create_entry(
            type="bug",
            title="mismatch",
            body="# body",
            frontmatter={
                "version": {"python": "3.12"},
                "status": "resolved",
                **frontmatter,
            },
            date_str="2026-07-23",
            **top_level,
        )
    )

    assert result.startswith(_DEPRECATED_PREFIX + "VALIDATION_ERROR:")
    assert expected in result


def test_server_keeps_legacy_tool_names() -> None:
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
        assert legacy in names
    for modern in [
        "memorant_append_event",
        "memorant_list_pending_events",
        "memorant_write_memory",
        "memorant_recall",
        "memorant_feedback",
        "memorant_promote",
        "memorant_activity",
    ]:
        assert modern in names
    for ingest in [
        "memorant_ingest_source",
        "memorant_distill_source",
        "memorant_list_sources",
        "memorant_external_policy",
        "memorant_confirm_external",
        "memorant_govern_source",
        "memorant_confirm_promote",
    ]:
        assert ingest in names


def test_append_event_tool_schema_exposes_enum_and_limits() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(item for item in tools if item.name == "memorant_append_event")
    properties = tool.parameters["properties"]

    # fastmcp 3.x inlines the enum on the field instead of routing through
    # $ref/$defs; the asserted contract (the 9 event types + length caps) is
    # unchanged, only the schema shape differs.
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


@pytest.mark.parametrize("command", ["memorant-mcp", "memorant-hook"])
def test_installed_cli_entrypoints_smoke(
    command: str, tmp_path: Path
) -> None:
    executable = Path(sys.executable).with_name(command)
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("MEMORANT_ROOT", None)
    env.pop("VAULT_ROOT", None)

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
