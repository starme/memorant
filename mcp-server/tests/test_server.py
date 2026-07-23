import asyncio
from pathlib import Path

import pytest

from memorant_mcp.server import (
    mcp,
    vault_append_entry,
    vault_create_entry,
    vault_delete_entry,
    vault_get_recent,
    vault_search,
    vault_update_frontmatter,
)


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

    assert result == "created: bugs/python-连接超时-20260723.md"
    assert "stack:\n- Python" in (tmp_path / result.removeprefix("created: ")).read_text()


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

    assert result == "created: arch/adr-001-memorant-选择本地优先存储.md"


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

    assert result.startswith("VALIDATION_ERROR:")
    assert expected in result


def test_server_keeps_legacy_tool_names() -> None:
    assert mcp.name == "memorant"
    tools = [
        vault_search,
        vault_create_entry,
        vault_append_entry,
        vault_update_frontmatter,
        vault_delete_entry,
        vault_get_recent,
    ]
    assert [tool.__name__ for tool in tools] == [
        "vault_search",
        "vault_create_entry",
        "vault_append_entry",
        "vault_update_frontmatter",
        "vault_delete_entry",
        "vault_get_recent",
    ]
