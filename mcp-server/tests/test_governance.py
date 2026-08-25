"""治理动作测试：archive 置 superseded，候选/冲突只产标记不改 memory 状态。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.governance import govern_source
from memorant_mcp.memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryWriteInput,
    TrustTier,
)
from memorant_mcp.memory_store import read_memory, write_memory


def _write_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str = "DB pool"
) -> dict:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    return write_memory(
        MemoryWriteInput(
            title=title,
            claim="Increase pool size on exhaustion",
            kind=MemoryKind.procedural,
            trust_tier=TrustTier.provisional,
            confidence=0.6,
            evidence=[EvidenceRef(source="src", excerpt="e", session_id="s")],
            source_event_ids=["a" * 32],
            origin_session_ids=["sess-a"],
        )
    )


def test_archive_sets_superseded_no_touch_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    mem = _write_memory(tmp_path, monkeypatch)
    result = govern_source("d" * 32, "archive", related_memory_id=mem["memory_id"])
    assert result["action"] == "archive"
    updated = read_memory(mem["memory_id"])
    assert updated["lifecycle"] == "superseded"


def test_dedupe_reinforces_existing_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    mem = _write_memory(tmp_path, monkeypatch)
    result = govern_source("d" * 32, "dedupe", related_memory_id=mem["memory_id"])
    assert result["action"] == "dedupe"
    updated = read_memory(mem["memory_id"])
    assert updated["lifecycle"] == "active"  # 续燃不改变 lifecycle


def test_merge_candidate_produces_activity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    mem = _write_memory(tmp_path, monkeypatch)
    result = govern_source(
        "d" * 32, "merge_candidate", related_memory_id=mem["memory_id"]
    )
    assert result["action"] == "merge_candidate"
    assert result["attention"] == "needs_attention"
    # 不改 memory 状态
    updated = read_memory(mem["memory_id"])
    assert updated["lifecycle"] == "active"


def test_conflict_marks_without_changing_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    mem = _write_memory(tmp_path, monkeypatch)
    result = govern_source(
        "d" * 32,
        "conflict",
        related_memory_id=mem["memory_id"],
        replacement_claim="new claim",
    )
    assert result["action"] == "conflict"
    assert result["attention"] == "conflict"
    updated = read_memory(mem["memory_id"])
    assert updated["lifecycle"] == "active"


def test_archive_missing_memory_returns_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = govern_source("d" * 32, "archive", related_memory_id="e" * 32)
    assert result.get("error") == "NOT_FOUND"
