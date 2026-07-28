from pathlib import Path

from memorant_mcp.project_identity import normalize_git_remote, resolve_project_identity


def test_normalize_git_remote_variants() -> None:
    assert (
        normalize_git_remote("git@github.com:org/talent-kpi.git")
        == "github.com/org/talent-kpi"
    )
    assert (
        normalize_git_remote("https://github.com/org/talent-kpi.git")
        == "github.com/org/talent-kpi"
    )


def test_resolve_falls_back_to_cwd_basename(tmp_path: Path) -> None:
    project = tmp_path / "my-app"
    project.mkdir()
    identity = resolve_project_identity(str(project))
    assert identity.project_label == "my-app"
    assert len(identity.project_key) == 16
    assert "my-app" in identity.aliases


def test_resolve_uses_git_remote_when_present(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "kpi"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:acme/talent-kpi.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    identity = resolve_project_identity(str(repo))
    assert identity.project_key == resolve_project_identity(str(repo)).project_key
    assert identity.project_label in {"talent-kpi", "kpi"}
    assert len(identity.project_key) == 16
