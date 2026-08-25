import os
import subprocess
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "hooks" / "vault-stop.sh"


def _daily(root: Path, pending: int) -> None:
    path = root / "daily" / f"{date.today().isoformat()}.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\npending_review: {pending}\n---\n", encoding="utf-8")


def _assert_pending_mentioned(stdout: str, pending: int) -> None:
    # Weekday branch: Fridays use "leads (N currently)"; other days use "N pending_review".
    assert (
        f"{pending} pending_review lead(s)" in stdout
        or f"leads ({pending} currently)" in stdout
    )


def test_stop_hook_uses_project_memorant_config_before_legacy_environment(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    worktree = project / "nested"
    memorant_root = tmp_path / "Tal's Memorant Root"
    legacy_root = tmp_path / "legacy-root"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.local.md").write_text(
        "---\nunrelated: true\n---\n", encoding="utf-8"
    )
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "memorant.local.md").write_text(
        f"---\nroot: {memorant_root}\n---\n", encoding="utf-8"
    )
    worktree.mkdir()
    _daily(memorant_root, 7)
    _daily(legacy_root, 2)

    env = {
        **os.environ,
        "HOME": str(home),
        "VAULT_ROOT": str(legacy_root),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
    }
    env.pop("MEMORANT_ROOT", None)
    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=worktree,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.startswith("[memorant]")
    _assert_pending_mentioned(result.stdout, 7)


def test_stop_hook_ignores_root_outside_frontmatter(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    fake_root = tmp_path / "must-not-use"
    (home / ".claude").mkdir(parents=True)
    project.mkdir()
    (project / ".claude").mkdir()
    (project / ".claude" / "memorant.local.md").write_text(
        f"---\nunrelated: true\n---\nroot: {fake_root}\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
    }
    env.pop("MEMORANT_ROOT", None)
    env.pop("VAULT_ROOT", None)
    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.startswith("[memorant] MEMORANT_ROOT is not configured")


def test_stop_hook_matches_python_case_insensitive_root_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    configured = tmp_path / "configured"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "memorant.local.md").write_text(
        f'---\nROOT: "{configured}"\n---\n',
        encoding="utf-8",
    )
    _daily(configured, 9)

    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
    }
    env.pop("MEMORANT_ROOT", None)
    env.pop("VAULT_ROOT", None)
    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    _assert_pending_mentioned(result.stdout, 9)


def test_stop_hook_supports_historical_vault_root_frontmatter(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    configured = tmp_path / "legacy vault"
    (home / ".claude").mkdir(parents=True)
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "vault.local.md").write_text(
        f'---\nVAULT_ROOT: "{configured}"\n---\n',
        encoding="utf-8",
    )
    _daily(configured, 11)

    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
    }
    env.pop("MEMORANT_ROOT", None)
    env.pop("VAULT_ROOT", None)
    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    _assert_pending_mentioned(result.stdout, 11)
