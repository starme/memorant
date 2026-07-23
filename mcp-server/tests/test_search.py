from pathlib import Path

import pytest

from memorant_mcp import search as search_module


def _write_entry(root: Path, rel_path: str, project: str, body: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nproject: {project}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_search_uses_isolated_root_and_project_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_entry(tmp_path, "bugs/one.md", "memorant", "timeout while indexing")
    _write_entry(tmp_path, "bugs/two.md", "other", "timeout from upstream")
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    monkeypatch.setattr(search_module, "_rg_available", lambda: False)

    results = search_module.search("timeout", dirs=["bugs"], project="memorant")

    assert [result["file"] for result in results] == ["bugs/one.md"]
    assert results[0]["match"] == "timeout while indexing"


def test_search_honors_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_entry(tmp_path, "bugs/one.md", "memorant", "same needle")
    _write_entry(tmp_path, "bugs/two.md", "memorant", "same needle")
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    monkeypatch.setattr(search_module, "_rg_available", lambda: False)

    assert len(search_module.search("needle", dirs=["bugs"], limit=1)) == 1
