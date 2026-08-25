"""SourceDoc 留存：幂等、只读、路径安全、超限。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.source_docs import (
    check_source_integrity,
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


def test_ingest_paste_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_read_and_list_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


# ── check_source_integrity 四态 ──────────────────────────────


def test_check_source_integrity_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = ingest_source(kind="paste", content="未改动内容")
    check = check_source_integrity(result["doc_id"])
    assert check["status"] == "intact"
    assert check["doc_id"] == result["doc_id"]
    assert len(check["content_sha256"]) == 64


def test_check_source_integrity_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = ingest_source(kind="paste", content="原始内容")
    doc_id = result["doc_id"]
    # 外部修改 .txt（模拟编辑器篡改），不重写 .md
    txt = tmp_path / "source_docs" / f"{doc_id}.txt"
    txt.write_text("被篡改的内容", encoding="utf-8")

    check = check_source_integrity(doc_id)
    assert check["status"] == "modified"
    assert check["recorded_sha256"] == result["content_sha256"]
    assert check["current_sha256"] != check["recorded_sha256"]
    assert check["path"].endswith(f"{doc_id}.txt")
    # 不重写 .md（recorded 保持原值）
    md = tmp_path / "source_docs" / f"{doc_id}.md"
    assert result["content_sha256"] in md.read_text(encoding="utf-8")


def test_check_source_integrity_not_detectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    # word 二进制不可解析 → 无 .txt → not_detectable
    doc = tmp_path / "old.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0")
    result = ingest_source(kind="word", path=str(doc))
    assert result["status"] == "unparseable"

    check = check_source_integrity(result["doc_id"])
    assert check["status"] == "not_detectable"
    assert "reason" in check


def test_check_source_integrity_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    check = check_source_integrity("f" * 32)
    assert check["error"] == "NOT_FOUND"
    assert check["doc_id"] == "f" * 32


def test_check_source_integrity_mcp_tool_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP 工具 memorant_check_source_integrity 接线：未改动返回 intact。"""
    import asyncio

    from memorant_mcp.server import memorant_check_source_integrity

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = ingest_source(kind="paste", content="MCP 接线测试")
    out = asyncio.run(memorant_check_source_integrity(result["doc_id"]))
    assert out["status"] == "intact"
    assert out["doc_id"] == result["doc_id"]


# ── P0-5 异常路径：URL 404 / 超时 / 磁盘权限 ─────────────────


def test_ingest_url_404_returns_fetch_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL 404 → ingest_source 返回 FETCH_FAILED，不崩溃、不落脏数据。"""
    import urllib.error

    from memorant_mcp import parsers

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    def fake_fetch_404(url: str, timeout: float, max_bytes: int) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(parsers, "_fetch_url_bytes", fake_fetch_404)
    result = ingest_source(kind="url", url="https://example.com/missing")

    assert result["error"] == "FETCH_FAILED"
    # 无脏数据：source_docs/ 下无任何 .md / .tmp 残留
    src_dir = tmp_path / "source_docs"
    assert not src_dir.exists() or list(src_dir.glob("*")) == []


def test_ingest_url_timeout_returns_fetch_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL 超时 → ingest_source 返回 FETCH_FAILED，在超时上限内返回，不无限阻塞。"""
    import time

    from memorant_mcp import parsers

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    def fake_fetch_timeout(url: str, timeout: float, max_bytes: int) -> bytes:
        raise TimeoutError("timed out")

    monkeypatch.setattr(parsers, "_fetch_url_bytes", fake_fetch_timeout)
    start = time.monotonic()
    result = ingest_source(kind="url", url="https://example.com/slow")
    elapsed = time.monotonic() - start

    assert result["error"] == "FETCH_FAILED"
    # 不无限阻塞：立即返回（mock 不真 sleep，< 5s 即证明未阻塞）
    assert elapsed < 5.0
    src_dir = tmp_path / "source_docs"
    assert not src_dir.exists() or list(src_dir.glob("*")) == []


def test_ingest_atomic_write_oserror_no_tmp_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """磁盘写入 OSError → 明确错误，不留 .tmp 半成品文件（mock _atomic_write）。"""
    from memorant_mcp import source_docs

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    def fail_write(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(source_docs, "_atomic_write", fail_write)

    # 断言 ingest 不吞异常、向上抛出（不静默丢数据）
    with pytest.raises(OSError, match="disk full"):
        ingest_source(kind="paste", content="写入失败的内容")

    # 无 .tmp 残留：source_docs/ 不存在或为空
    src_dir = tmp_path / "source_docs"
    assert not src_dir.exists() or list(src_dir.glob("*.tmp")) == []
    assert not src_dir.exists() or list(src_dir.glob("*")) == []


def test_ingest_atomic_write_cleanup_on_failure_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_atomic_write 本身在 os.replace 抛错时，finally 清理临时文件不留 .tmp。"""
    import os

    from memorant_mcp import source_docs

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    # 让 mkstemp 正常、但 os.replace 抛错，验证 finally 分支清理 temp 文件
    def failing_replace(src: str, dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    # 构造一个真实可写的 source_docs 目录，确保 mkstemp 成功
    src_dir = tmp_path / "source_docs"
    src_dir.mkdir()

    with pytest.raises(OSError, match="replace failed"):
        source_docs._atomic_write(src_dir / "target.md", "body")

    # 无 .tmp 残留
    leftovers = list(src_dir.glob("*.tmp"))
    assert leftovers == []
