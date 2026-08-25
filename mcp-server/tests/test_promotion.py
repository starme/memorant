import asyncio
from pathlib import Path

import pytest

from memorant_mcp.memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryWriteInput,
    TrustTier,
)
from memorant_mcp.memory_store import read_memory, write_memory
from memorant_mcp.promotion import apply_feedback, promote_memory


def _provisional_with_source_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict:
    """构造带 source-doc 证据的 provisional 记忆（资料经验来源）。"""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    return write_memory(
        MemoryWriteInput(
            title="经验",
            claim="某资料经验",
            kind=MemoryKind.semantic,
            trust_tier=TrustTier.provisional,
            confidence=0.5,
            evidence=[
                EvidenceRef(source="source-doc:abc", excerpt="e", session_id="s")
            ],
            source_event_ids=["a" * 32],
        )
    )


def _provisional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    return write_memory(
        MemoryWriteInput(
            title="Maybe root cause",
            claim="Timeouts caused by connection pool size",
            kind=MemoryKind.episodic,
            trust_tier=TrustTier.provisional,
            confidence=0.5,
            scope={"project": "api"},
            evidence=[
                EvidenceRef(source="log", excerpt="pool exhausted", session_id="s-origin")
            ],
            source_event_ids=["d" * 32],
            origin_session_ids=["s-origin"],
        )
    )


def test_same_session_cannot_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = _provisional(tmp_path, monkeypatch)
    result = promote_memory(
        mem["path"],
        evidence_session_id="s-origin",
        success_outcome="success",
        evidence_excerpt="worked again",
    )
    assert result["error"] == "SAME_SESSION"


def test_independent_success_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = _provisional(tmp_path, monkeypatch)
    result = promote_memory(
        mem["path"],
        evidence_session_id="s-other",
        success_outcome="success",
        evidence_excerpt="reused fix, tests passed",
    )
    assert result.get("promoted") is True
    assert result["trust_tier"] == "verified"


def test_contradiction_supersedes_and_writes_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = _provisional(tmp_path, monkeypatch)
    result = apply_feedback(
        mem["path"],
        "contradicted",
        note="pool size was a red herring",
        session_id="s2",
        replacement_claim="Timeouts caused by DNS TTL misconfig",
    )
    assert result["feedback"] == "contradicted"
    assert result["updated"]["lifecycle"] == "superseded"
    assert result["replacement"]["path"].startswith("memories/")


def test_correct_without_replacement_marks_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = _provisional(tmp_path, monkeypatch)
    result = apply_feedback(
        mem["path"],
        "corrected",
        note="user confirmed delist",
        session_id="s3",
    )
    assert result["feedback"] == "corrected"
    assert result["updated"]["lifecycle"] == "rejected"
    assert result.get("replacement") is None


# ── confirm_promote 证据校验 / 幂等 / 审计 ──────────────────


def _confirm_promote_activity_count(tmp_path: Path) -> int:
    """统计今日 Activity 中 memory.confirm_promote 的条数。"""
    from memorant_mcp.activity import read_activity

    payload = read_activity()
    return sum(
        1 for e in payload.get("entries", []) if "`memory.confirm_promote`" in e["line"]
    )


def test_confirm_promote_provisional_without_source_doc_evidence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provisional 但证据全非 source-doc → VALIDATION_ERROR，不写 verified。"""
    from memorant_mcp.server import memorant_confirm_promote

    mem = _provisional(tmp_path, monkeypatch)
    result = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert result["error"] == "VALIDATION_ERROR"
    # 不落半态：仍为 provisional
    after = read_memory(mem["path"])
    assert after["trust_tier"] == "provisional"


def test_confirm_promote_with_source_doc_evidence_confirms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memorant_mcp.server import memorant_confirm_promote

    mem = _provisional_with_source_doc(tmp_path, monkeypatch)
    result = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert result["confirmed"] is True
    assert result["trust_tier"] == "verified"
    assert result["lifecycle"] == "reinforced"


def test_confirm_promote_non_provisional_returns_not_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memorant_mcp.server import memorant_confirm_promote

    # 直接写 verified 记忆 → 非 provisional
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    mem = write_memory(
        MemoryWriteInput(
            title="已验证",
            claim="已验证记忆",
            kind=MemoryKind.semantic,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            evidence=[EvidenceRef(source="source-doc:abc", excerpt="e")],
            source_event_ids=["a" * 32],
        )
    )
    result = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert result["error"] == "NOT_PROVISIONAL"
    assert result.get("path") == mem["path"]


def test_confirm_promote_idempotent_no_second_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已 verified 重复调用 → NOT_PROVISIONAL，不产生第二次审计。"""
    from memorant_mcp.server import memorant_confirm_promote

    mem = _provisional_with_source_doc(tmp_path, monkeypatch)
    first = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert first["confirmed"] is True

    audits_after_first = _confirm_promote_activity_count(tmp_path)

    second = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert second["error"] == "NOT_PROVISIONAL"

    after = read_memory(mem["path"])
    assert after["trust_tier"] == "verified"
    assert after["lifecycle"] == "reinforced"

    # 幂等：不产生第二次审计（confirm_promote 计数不变）
    audits_after_second = _confirm_promote_activity_count(tmp_path)
    assert audits_after_second == audits_after_first


def test_confirm_promote_success_writes_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功确认 → 写一条 memory.confirm_promote Activity。"""
    from memorant_mcp.server import memorant_confirm_promote

    mem = _provisional_with_source_doc(tmp_path, monkeypatch)
    before = _confirm_promote_activity_count(tmp_path)
    result = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert result["confirmed"] is True
    after = _confirm_promote_activity_count(tmp_path)
    assert after == before + 1


def test_confirm_promote_missing_memory_returns_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不存在的 memory_id → NOT_FOUND。"""
    from memorant_mcp.server import memorant_confirm_promote

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    result = asyncio.run(memorant_confirm_promote("f" * 32))
    assert result["error"] == "NOT_FOUND"


def test_confirm_promote_validation_error_writes_no_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据校验失败 → VALIDATION_ERROR 且不写审计、不落半态。"""
    from memorant_mcp.server import memorant_confirm_promote

    mem = _provisional(tmp_path, monkeypatch)
    before = _confirm_promote_activity_count(tmp_path)
    result = asyncio.run(memorant_confirm_promote(mem["memory_id"]))
    assert result["error"] == "VALIDATION_ERROR"
    after = _confirm_promote_activity_count(tmp_path)
    assert after == before
    assert read_memory(mem["path"])["trust_tier"] == "provisional"
