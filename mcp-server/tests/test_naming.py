from pathlib import Path

import pytest

from memorant_mcp.hook_core import discover_root
from memorant_mcp.naming import (
    PathForbiddenError,
    _slug,
    filename_for,
    resolve_safe_path,
    vault_root,
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
    vault_env = tmp_path / "vault-env"
    _write_config(
        tmp_path / ".claude" / "memorant.local.md",
        tmp_path / "memorant-config",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMORANT_ROOT", str(memorant_env))
    monkeypatch.setenv("VAULT_ROOT", str(vault_env))

    assert vault_root() == str(memorant_env.resolve())


def test_memorant_local_precedes_legacy_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memorant_config = tmp_path / "memorant-config"
    _write_config(
        tmp_path / ".claude" / "memorant.local.md",
        memorant_config,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "vault-env"))

    assert vault_root() == str(memorant_config.resolve())


def test_legacy_environment_precedes_legacy_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_env = tmp_path / "vault-env"
    _write_config(
        tmp_path / ".claude" / "vault.local.md",
        tmp_path / "vault-config",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)
    monkeypatch.setenv("VAULT_ROOT", str(vault_env))

    assert vault_root() == str(vault_env.resolve())


def test_legacy_local_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_config = tmp_path / "vault-config"
    config = tmp_path / ".claude" / "vault.local.md"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"---\nVAULT_ROOT: {vault_config}\n---\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    assert vault_root() == str(vault_config.resolve())


def test_hook_and_server_share_exact_root_priority_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    memorant_env = tmp_path / "memorant-env"
    memorant_config = tmp_path / "memorant-config"
    vault_env = tmp_path / "vault-env"
    vault_config = tmp_path / "vault-config"
    memorant_file = tmp_path / ".claude" / "memorant.local.md"
    vault_file = tmp_path / ".claude" / "vault.local.md"
    _write_config(memorant_file, memorant_config)
    vault_file.write_text(
        f"---\nVAULT_ROOT: {vault_config}\n---\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MEMORANT_ROOT", str(memorant_env))
    monkeypatch.setenv("VAULT_ROOT", str(vault_env))

    assert discover_root() == vault_root() == str(memorant_env.resolve())

    monkeypatch.delenv("MEMORANT_ROOT")
    assert discover_root() == vault_root() == str(memorant_config.resolve())

    memorant_file.unlink()
    assert discover_root() == vault_root() == str(vault_env.resolve())

    monkeypatch.delenv("VAULT_ROOT")
    assert discover_root() == vault_root() == str(vault_config.resolve())


def test_vault_local_prefers_root_over_legacy_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preferred = tmp_path / "preferred"
    legacy = tmp_path / "legacy"
    config = tmp_path / ".claude" / "vault.local.md"
    config.parent.mkdir()
    config.write_text(
        f"---\nVAULT_ROOT: {legacy}\nroot: {preferred}\n---\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    assert discover_root() == vault_root() == str(preferred.resolve())


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
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    with pytest.raises(RuntimeError):
        vault_root()


def test_local_config_preserves_spaces_and_quote_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "Tal's Memorant Notes"
    _write_config(tmp_path / ".claude" / "memorant.local.md", configured)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMORANT_ROOT", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    assert vault_root() == str(configured.resolve())


def test_resolve_safe_path_rejects_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "memorant"))

    with pytest.raises(PathForbiddenError):
        resolve_safe_path("../outside.md")
