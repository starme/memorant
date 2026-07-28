from pathlib import Path

import pytest

from memorant_mcp.memory_schema import EvidenceRef, MemoryKind, MemoryWriteInput, TrustTier
from memorant_mcp.memory_store import write_memory
from memorant_mcp.recall import format_recall_context, recall_memories


def test_verified_outranks_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    write_memory(
        MemoryWriteInput(
            title="provisional redis tip",
            claim="Maybe use redis for cache",
            kind=MemoryKind.decision,
            trust_tier=TrustTier.provisional,
            confidence=0.4,
            scope={"project": "app"},
            evidence=[EvidenceRef(source="chat", excerpt="prefer redis", session_id="s1")],
            source_event_ids=["e" * 32],
            origin_session_ids=["s1"],
        )
    )
    write_memory(
        MemoryWriteInput(
            title="verified redis tip",
            claim="Use redis for cache after outage",
            kind=MemoryKind.decision,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            scope={"project": "app"},
            evidence=[
                EvidenceRef(source="incident", excerpt="cache outage fixed", session_id="s1")
            ],
            source_event_ids=["f" * 32],
            origin_session_ids=["s1"],
        )
    )
    payload = recall_memories("redis cache", project="app", limit=5, mark=False)
    assert payload["count"] >= 2
    assert payload["results"][0]["trust_tier"] == "verified"
    assert "待验证" in payload["results"][1]["label"] or payload["results"][1][
        "trust_tier"
    ] == "provisional"
    context = format_recall_context(payload)
    assert "Memorant recall" in context
    assert len(context) <= 8000
