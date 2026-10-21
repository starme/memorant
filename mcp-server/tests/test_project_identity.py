from pathlib import Path

import pytest

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


def _init_repo(path: Path, remote: str | None = None) -> Path:
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=path,
            check=True,
            capture_output=True,
        )
    return path


def test_workspace_marker_unifies_root_and_nested_repo(tmp_path: Path) -> None:
    """B2: the same logical project reached from two cwds must keep one key."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".devflow").mkdir()
    (root / ".devflow" / "project.yaml").write_text('project:\n  name: "acme"\n')
    nested = _init_repo(root / "devflow", "git@github.com:acme/devflow.git")

    from_root = resolve_project_identity(str(root))
    from_nested = resolve_project_identity(str(nested))

    assert from_root.project_key == from_nested.project_key
    assert from_root.project_label == "acme"


def test_unrelated_repos_keep_distinct_keys(tmp_path: Path) -> None:
    """B3: unification must not over-merge separate projects."""
    first = _init_repo(tmp_path / "one", "git@github.com:acme/one.git")
    second = _init_repo(tmp_path / "two", "git@github.com:acme/two.git")

    assert (
        resolve_project_identity(str(first)).project_key
        != resolve_project_identity(str(second)).project_key
    )


def test_explicit_project_controls_key_everywhere(tmp_path: Path) -> None:
    """B4: an explicit name wins in marker / remote / plain-cwd contexts alike."""
    with_marker = tmp_path / "marker"
    with_marker.mkdir()
    (with_marker / ".devflow").mkdir()
    (with_marker / ".devflow" / "project.yaml").write_text(
        'project:\n  name: "other"\n'
    )
    with_remote = _init_repo(tmp_path / "remote", "git@github.com:acme/remote.git")
    plain = tmp_path / "plain"
    plain.mkdir()

    keys = {
        resolve_project_identity(str(path), explicit_project="shared").project_key
        for path in (with_marker, with_remote, plain)
    }

    assert len(keys) == 1


def test_identity_source_is_reported(tmp_path: Path) -> None:
    """B5: every derivation path reports where the identity came from."""
    marker = tmp_path / "marker"
    marker.mkdir()
    (marker / ".devflow").mkdir()
    (marker / ".devflow" / "project.yaml").write_text('project:\n  name: "acme"\n')
    remote = _init_repo(tmp_path / "remote", "git@github.com:acme/remote.git")
    local = _init_repo(tmp_path / "local")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert resolve_project_identity(str(marker)).source == "workspace_marker"
    assert resolve_project_identity(str(remote)).source == "git_remote"
    assert resolve_project_identity(str(local)).source == "git_root"
    assert resolve_project_identity(str(plain)).source == "cwd"
    assert (
        resolve_project_identity(str(plain), explicit_project="x").source == "explicit"
    )


def test_declared_project_name_beats_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B4b: memorant.settings.json project.name is honoured end to end."""
    import json

    from memorant_mcp.config import load_settings
    from memorant_mcp.hook_cli import _project_fields

    settings = tmp_path / ".claude"
    settings.mkdir()
    (settings / "memorant.settings.json").write_text(
        json.dumps({"project": {"name": "declared-project"}})
    )
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))

    assert load_settings().project_name == "declared-project"
    label, key, source = _project_fields({"cwd": str(tmp_path)})
    assert label == "declared-project"
    assert source == "explicit"
    assert (
        key
        == resolve_project_identity(
            str(tmp_path), explicit_project="declared-project"
        ).project_key
    )


def test_local_markdown_project_name_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B4c: memorant.local.md frontmatter declares the same thing."""
    from memorant_mcp.config import load_settings

    settings = tmp_path / ".claude"
    settings.mkdir()
    (settings / "memorant.local.md").write_text(
        "---\nproject: local-project\nrecall_enabled: true\n---\n"
    )
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))

    assert load_settings().project_name == "local-project"
