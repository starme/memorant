from pathlib import Path

import pytest

from memorant_mcp.naming import (
    PathForbiddenError,
    _slug,
    filename_for,
    memorant_root,
    resolve_safe_path,
)
from memorant_mcp.schema import EntryType


def _write_config(path: Path, value: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nroot: {value}\n---\n", encoding="utf-8")


def test_cjk_slug_and_filename_stay_readable() -> None:
    assert _slug("限流治理：漏斗模型") == "限流治理-漏斗模型"
    assert filename_for(
        EntryType.bug,
        "2026-07-23",
        "限流治理：漏斗模型",
        stack=["Go", "Redis"],
    ) == "bugs/go-redis-限流治理-漏斗模型-20260723.md"


def test_memorant_root_environment_has_highest_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memorant_env = tmp_path / "memorant-env"
    _write_config(tmp_path / ".claude" / "memorant.local.md", tmp_path / "memorant-config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMORANT_ROOT", str(memorant_env))

    assert memorant_root() == str(memorant_env.resolve())


def test_memorant_local_precedes_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memorant_config = tmp_path / "memorant-config"
    _write_config(tmp_path / ".claude" / "memorant.local.md", memorant_config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)

    assert memorant_root() == str(memorant_config.resolve())


def test_local_config_reads_only_frontmatter_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".claude" / "memorant.local.md"
    config.parent.mkdir(parents=True)
    config.write_text(
        "---\nunrelated: true\n---\nroot: /must/not/be/read\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)

    with pytest.raises(RuntimeError):
        memorant_root()


def test_local_config_preserves_spaces_and_quote_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "Tal's Memorant Notes"
    _write_config(tmp_path / ".claude" / "memorant.local.md", configured)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)

    assert memorant_root() == str(configured.resolve())


def test_resolve_safe_path_rejects_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "memorant"))

    with pytest.raises(PathForbiddenError):
        resolve_safe_path("../outside.md")
