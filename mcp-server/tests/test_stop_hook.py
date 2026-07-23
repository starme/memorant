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


def test_stop_hook_uses_project_memorant_config_before_legacy_environment(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    worktree = project / "nested"
    memorant_root = tmp_path / "memorant-root"
    legacy_root = tmp_path / "legacy-root"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.local.md").write_text(
        "---\nunrelated: true\n---\n", encoding="utf-8"
    )
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "memorant.local.md").write_text(
        f"---\nMEMORANT_ROOT: {memorant_root}\n---\n", encoding="utf-8"
    )
    worktree.mkdir()
    _daily(memorant_root, 7)
    _daily(legacy_root, 2)

    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=worktree,
        env={
            **os.environ,
            "HOME": str(home),
            "VAULT_ROOT": str(legacy_root),
            "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.startswith("[memorant]")
    assert "7 pending_review lead(s)" in result.stdout
