"""SourceDoc 留存：幂等、只读、路径安全、超限。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.source_docs import (
    doc_id_for,
    ingest_source,
    list_sources,
    read_source_doc,
)
from memorant_mcp.source_schema import SourceDoc


def _md_file(root: Path, name: str, text: str) -> Path:
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def test_ingest_markdown_file_stores_and_returns_doc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    src = _md_file(tmp_path, "team.md", "# Team spec\n\nUse local-first.\n")
    result = ingest_source(kind="markdown", path=str(src))

    assert result["duplicate"] is False
    assert result["status"] == "stored"
    assert result["extractable"] is True
    assert len(result["doc_id"]) == 32
    assert len(result["content_sha256"]) == 64
    assert result["source_type"] == "markdown"
    assert (tmp_path / "source_docs" / f"{result['doc_id']}.md").is_file()
    assert (tmp_path / "source_docs" / f"{result['doc_id']}.txt").is_file()


def test_ingest_paste_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = ingest_source(kind="paste", content="pasted experience text")
    assert result["status"] == "stored"
    assert result["source_type"] == "paste"
    assert result["origin"] == "paste"


def test_duplicate_content_returns_existing_doc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    first = ingest_source(kind="paste", content="同一条经验")
    second = ingest_source(kind="paste", content="同一条经验")

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["doc_id"] == first["doc_id"]
    # 不重复落盘（同名文件只有一个）。
    files = list((tmp_path / "source_docs").glob("*.md"))
    assert len(files) == 1


def test_cross_type_same_content_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    src = _md_file(tmp_path, "note.md", "跨类型同文")
    from_md = ingest_source(kind="markdown", path=str(src))
    from_paste = ingest_source(kind="paste", content="跨类型同文")

    assert from_paste["duplicate"] is True
    assert from_paste["doc_id"] == from_md["doc_id"]


def test_doc_id_is_stable_sha256_hex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    src = _md_file(tmp_path, "a.md", "body")
    did = doc_id_for(kind="markdown", path=str(src), content=None)
    assert len(did) == 32
    assert all(c in "0123456789abcdef" for c in did)


def test_local_file_outside_root_is_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    outside = _md_file(tmp_path, "secret.md", "outside content")
    result = ingest_source(kind="markdown", path=str(outside))
    assert result.get("error") == "READ_FORBIDDEN"


def test_read_and_list_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = ingest_source(kind="paste", content="列表测试")
    doc = read_source_doc(result["doc_id"])
    assert doc["doc_id"] == result["doc_id"]
    assert doc["source_type"] == "paste"

    listing = list_sources()
    assert listing["count"] == 1
    assert listing["sources"][0]["doc_id"] == result["doc_id"]


def test_read_missing_doc_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        read_source_doc("a" * 32)


def test_source_docs_module_has_no_mutate_or_delete_functions() -> None:
    """只读保证：source_docs.py 不暴露任何修改/删除 source_docs 的路径。"""
    import inspect

    import memorant_mcp.source_docs as sd

    forbidden_names = {"update", "delete", "remove", "unlink", "overwrite"}
    for name, _fn in inspect.getmembers(sd, inspect.isfunction):
        lower = name.lower()
        assert not any(word in lower for word in forbidden_names), name
        if name == "ingest_source":
            continue
        assert not lower.startswith("write_"), name


def test_source_doc_model_forbids_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SourceDoc(
            doc_id="a" * 32,
            source_type="paste",
            origin="paste",
            title="t",
            content_sha256="b" * 64,
            byte_size=1,
            extra_field="nope",
        )
