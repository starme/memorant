from pathlib import Path

import pytest

from memorant_mcp.memory_schema import EvidenceRef, MemoryKind, MemoryWriteInput, TrustTier
from memorant_mcp.memory_store import list_memories, read_memory, write_memory


def _write_b(project: str = "memorant") -> dict:
    return write_memory(
        MemoryWriteInput(
            title="Prefer bounded evidence",
            claim="Journal evidence must be redacted before persistence.",
            kind=MemoryKind.procedural,
            trust_tier=TrustTier.provisional,
            confidence=0.55,
            scope={"project": project},
            evidence=[
                EvidenceRef(source="session", excerpt="token=secret", session_id="s1")
            ],
            source_event_ids=["b" * 32],
            origin_session_ids=["s1"],
        )
    )


def _write_a() -> dict:
    return write_memory(
        MemoryWriteInput(
            title="Closed loop fix",
            claim="Fixing X requires Y; verified by test.success.",
            kind=MemoryKind.episodic,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            scope={"project": "memorant", "stack": ["pytest"]},
            evidence=[
                EvidenceRef(
                    source="test.failure",
                    excerpt="AssertionError in test_x",
                    session_id="s1",
                ),
                EvidenceRef(
                    source="test.success",
                    excerpt="1 passed",
                    session_id="s1",
                ),
            ],
            source_event_ids=["a" * 32],
            origin_session_ids=["s1"],
        )
    )


def test_write_a_and_b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    a = _write_a()
    b = _write_b()
    assert a["trust_tier"] == "verified"
    assert b["trust_tier"] == "provisional"
    assert (tmp_path / a["path"]).is_file()
    assert (tmp_path / b["path"]).is_file()
    loaded = read_memory(a["memory_id"])
    assert loaded["title"] == "Closed loop fix"


def test_fingerprint_dedup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    first = _write_b()
    second = _write_b()
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["path"] == second["path"]
    assert len(list((tmp_path / "memories").glob("*.md"))) == 1


def test_write_requires_evidence_and_source_events() -> None:
    with pytest.raises(Exception):
        MemoryWriteInput(
            title="No evidence",
            claim="Should fail",
            kind=MemoryKind.semantic,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            source_event_ids=["a" * 32],
        )


def test_write_requires_source_event_ids() -> None:
    with pytest.raises(Exception):
        MemoryWriteInput(
            title="No events",
            claim="Should fail",
            kind=MemoryKind.semantic,
            trust_tier=TrustTier.provisional,
            confidence=0.5,
            evidence=[EvidenceRef(source="t", excerpt="x")],
        )


def test_legacy_bug_listed_as_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    bug = tmp_path / "bugs" / "pytest-flaky-20260723.md"
    bug.parent.mkdir()
    bug.write_text(
        "---\ntype: bug\ntitle: flaky test\nstack: [pytest]\nproject: memorant\n"
        "date: 2026-07-23\nstatus: open\nversion: '1'\n---\n\n# flaky\n\nroot cause\n"
    )
    items = list_memories(project="memorant", include_legacy=True)
    assert any(i.get("legacy") and i.get("trust_tier") == "verified" for i in items)
