from pathlib import Path

import pytest

from memorant_mcp.memory_schema import EvidenceRef, MemoryKind, MemoryWriteInput, TrustTier
from memorant_mcp.memory_store import write_memory
from memorant_mcp.promotion import apply_feedback, promote_memory


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


def test_legacy_feedback_does_not_reference_deleted_vault_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply_feedback 对 legacy 记忆的死消息不得引用已删除的 vault 工具。

    该分支（LEGACY）指向的 vault_* 工具已删除；错误文案须指向迁移路径，
    而非悬空引用 vault。
    """
    import memorant_mcp.promotion as promotion_module

    monkeypatch.setattr(promotion_module, "read_memory", lambda _: {"legacy": True})
    result = apply_feedback("bugs/example.md", "adopted")

    assert result["error"] == "LEGACY"
    assert "vault" not in result["message"]
    assert "migration" in result["message"]
