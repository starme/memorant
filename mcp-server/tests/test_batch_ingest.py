"""批量目录导入（batch ingest）核心回归测试。

testing agent 会补充全量 te-1；此文件覆盖后端核心场景：
扫描 / 类型过滤 / dry-run 判定 / 上限 / 导入 / 幂等 / 失败隔离 / 脱敏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.batch_ingest import (
    _classify_file,
    _scan_directory,
    batch_ingest_dir,
    scan_dir,
)
from memorant_mcp.source_docs import ingest_source


def _md(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── 扫描（be-3）───────────────────────────────────────────


def test_scan_discovers_all_four_types_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "b.md", "# b")
    _md(d, "a.txt", "a")
    _md(d, "c.docx", "docx-placeholder")
    _md(d, "sub/d.pdf", "pdf-placeholder")
    (d / "ignored.doc").write_bytes(b"\xd0\xcf\x11\xe0")

    files = _scan_directory(str(d), set())
    types = sorted(f["source_type"] for f in files)
    assert types == ["markdown", "pdf", "text", "word"]
    # 确定性排序：按 realpath 排序，a.txt 在 b.md 前，c.docx 前
    paths = [Path(f["path"]).name for f in files]
    assert paths[0] == "a.txt"


def test_scan_type_filter_and_case_insensitive_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.MD", "# A")
    _md(d, "b.txt", "b")
    files = _scan_directory(str(d), {"markdown"})
    assert [Path(f["path"]).name for f in files] == ["a.MD"]


def test_scan_invalid_type_raises_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    d.mkdir()
    result = batch_ingest_dir(str(d), types=["doc"])
    assert result["error"] == "VALIDATION_ERROR"


def test_scan_empty_dir_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    d.mkdir()
    result = scan_dir(str(d))
    assert result["count"] == 0
    assert result["files"] == []


# ── dry-run 判定（be-4）───────────────────────────────────


def test_dry_run_classifies_importable_skipped_failed_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "new.md", "# new content unique")
    dup = _md(d, "dup.txt", "same content")
    # 先单文件导入 dup，使其在 batch dry-run 时被判定为 duplicate
    ingest_source(kind="text", path=str(dup))

    result = batch_ingest_dir(str(d), dry_run=True)
    statuses = {i["path"].rsplit("/", 1)[-1]: i["status"] for i in result["items"]}
    assert statuses["new.md"] == "importable"
    assert statuses["dup.txt"] == "skipped"
    # dry-run 不落盘：source_docs 只有之前单文件导入的 1 个
    assert result["success"] == 1
    assert result["skipped"] == 1


def test_dry_run_does_not_write_source_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.md", "# fresh content")
    batch_ingest_dir(str(d), dry_run=True)
    src_dir = tmp_path / "source_docs"
    assert not src_dir.exists() or list(src_dir.glob("*.md")) == []


def test_classify_file_out_of_root_fails_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    outside = _md(tmp_path, "outside.md", "# outside")
    status, extra = _classify_file(
        {"path": str(outside), "source_type": "markdown", "byte_size": 10}
    )
    assert status == "failed"
    assert extra["reason"] == "READ_FORBIDDEN"


# ── 上限（be-4）───────────────────────────────────────────


def test_max_files_truncates_with_stopped_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    for i in range(5):
        _md(d, f"{i}.md", f"# file {i}")
    result = batch_ingest_dir(str(d), max_files=2)
    assert result["total"] == 5
    assert result["success"] == 2
    assert result["stopped_reason"] == "max_files_reached"
    assert result["remaining_unprocessed"] == 3


def test_max_bytes_stops_after_successful_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.md", "# " + "x" * 100)
    _md(d, "b.md", "# " + "y" * 100)
    _md(d, "c.md", "# " + "z" * 100)
    result = batch_ingest_dir(str(d), max_bytes=250)
    assert result["stopped_reason"] == "max_bytes_reached"
    # 至少成功落盘了前几项，且总成功字节 <= max_bytes
    assert result["success"] >= 1


def test_max_seconds_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    for i in range(3):
        _md(d, f"{i}.md", f"# file {i}")
    # 极小时间上限，第一次迭代后即超时
    result = batch_ingest_dir(str(d), max_seconds=1e-9)
    assert result["stopped_reason"] == "max_seconds_reached"


# ── 导入 + 幂等（be-5）────────────────────────────────────


def test_batch_ingest_success_and_idempotent_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "one.md", "# one content")
    _md(d, "two.txt", "two content")

    first = batch_ingest_dir(str(d))
    assert first["success"] == 2
    assert first["failed"] == 0
    assert first["skipped"] == 0

    # 重跑：全部识别为 duplicate 跳过，不产生新 source_docs
    second = batch_ingest_dir(str(d))
    assert second["success"] == 0
    assert second["skipped"] == 2
    assert len(list((tmp_path / "source_docs").glob("*.md"))) == 2


def test_batch_single_failure_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "ok.md", "# good content")
    # 单文件超限：markdown 上限 5MB
    big = d / "big.md"
    big.write_bytes(b"#" + b"x" * (5 * 1024 * 1024 + 10))

    result = batch_ingest_dir(str(d))
    assert result["success"] == 1
    assert result["failed"] == 1
    failed_item = next(i for i in result["items"] if i["status"] == "failed")
    assert failed_item["reason"] == "SIZE_EXCEEDED"


def test_batch_directory_outside_root_rejected_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _md(outside, "x.md", "# x")
    result = batch_ingest_dir(str(outside))
    assert result["error"] == "READ_FORBIDDEN"


def test_batch_directory_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = batch_ingest_dir(str(tmp_path / "nope"))
    assert result["error"] == "NOT_FOUND"


def test_batch_symlink_file_escape_fails_per_item_not_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录内单个文件经 symlink 指向白名单外 -> 该文件 failed(READ_FORBIDDEN)，
    其余正常；且路径脱敏为相对路径，不暴露绝对路径。"""
    import os as _os

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "root"))
    root = tmp_path / "root"
    root.mkdir()
    d = root / "notes"
    d.mkdir()
    _md(d, "ok.md", "# ok content")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# secret", encoding="utf-8")
    try:
        _os.symlink(str(outside / "secret.md"), str(d / "leak.md"))
    except OSError:
        pytest.skip("symlink not supported on this platform")

    result = batch_ingest_dir(str(d))
    assert result["success"] == 1
    assert result["failed"] == 1
    failed = next(i for i in result["items"] if i["status"] == "failed")
    assert failed["reason"] == "READ_FORBIDDEN"
    # 路径脱敏：不暴露白名单外绝对路径
    assert failed["path"] == "notes/leak.md"


def test_batch_dotdot_traversal_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path / "root"))
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("# x", encoding="utf-8")

    result = batch_ingest_dir(str(root / "notes" / ".." / ".." / "outside"))
    assert result["error"] == "READ_FORBIDDEN"


def test_report_written_only_for_real_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.md", "# report content")

    batch_ingest_dir(str(d), dry_run=True)
    assert not (tmp_path / ".memorant" / "batch").exists()

    batch_ingest_dir(str(d))
    reports = list((tmp_path / ".memorant" / "batch").glob("*.md"))
    assert len(reports) == 1


# ── distill 准备阶段（be-5，契约 §10）──────────────────────


def test_distill_true_prepares_only_no_memory_no_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.md", "# distill target content")

    result = batch_ingest_dir(str(d), distill=True)
    assert result["success"] == 1
    # distill 只做准备，返回 synthetic_event_id，不写 memory
    assert len(result["distilled"]) == 1
    prep = result["distilled"][0]
    assert len(prep["synthetic_event_id"]) == 32
    assert prep["trust_tier"] == "provisional"
    assert prep["external_would_be_used"] is False
    # 不自动 promotion：memories/ 目录不存在（未写任何 memory）
    assert not (tmp_path / "memories").exists()


def test_distill_synthesizes_doc_commit_event_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    d = tmp_path / "notes"
    _md(d, "a.md", "# idempotent distill")

    first = batch_ingest_dir(str(d), distill=True)
    # 重跑：已成功项跳过，不再 distill（幂等，不重复合成事件）
    second = batch_ingest_dir(str(d), distill=True)
    assert second["skipped"] == 1
    assert second["distilled"] == []
    # 合成事件只写一次：journal 下只有一个 doc.commit 事件文件
    journal_files = list((tmp_path / "journal").glob("**/*.md"))
    doc_commit = [
        f
        for f in journal_files
        if "doc.commit" in f.read_text(encoding="utf-8")
    ]
    assert len(doc_commit) == 1
    assert first["distilled"][0]["synthetic_event_id"]
