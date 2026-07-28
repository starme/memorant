from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memorant_mcp.config import PersonaBehavior
from memorant_mcp.memory_schema import (
    EvidenceRef,
    LifecycleState,
    MemoryKind,
    MemoryWriteInput,
    TrustTier,
)
from memorant_mcp.memory_store import update_memory_fields, write_memory
from memorant_mcp.recall import format_recall_context, recall_memories
from memorant_mcp.trust_field import build_trust_view, classify_tide


def test_corrected_memory_serves_as_negative_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    written = write_memory(
        MemoryWriteInput(
            title="pool size fix",
            claim="Increase pool size when exhausted",
            kind=MemoryKind.procedural,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            scope={"project": "demo"},
            evidence=[
                EvidenceRef(source="t", excerpt="pool exhausted", session_id="s1")
            ],
            source_event_ids=["a" * 32],
        )
    )
    update_memory_fields(
        written["path"], {"lifecycle": LifecycleState.corrected.value}
    )
    payload = recall_memories("pool exhausted", project="demo", mark=False)
    hit = [r for r in payload["results"] if r.get("path") == written["path"]]
    assert hit, "negative-trust near-pit should appear on high similarity"
    assert hit[0]["modality"] == "negative"
    assert "NEGATIVE" in hit[0]["label"]
    assert "DENY" in hit[0]["agent_rail"]
    ctx = format_recall_context(payload)
    assert "DENY" in ctx
    assert "Human channel" in ctx or "ASK" in ctx


def test_dusty_verified_gets_verify_first_rail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    old = datetime.now(timezone.utc) - timedelta(days=120)
    written = write_memory(
        MemoryWriteInput(
            title="legacy cache tip",
            claim="Use redis for session cache",
            kind=MemoryKind.decision,
            trust_tier=TrustTier.verified,
            confidence=0.85,
            scope={"project": "app"},
            evidence=[EvidenceRef(source="t", excerpt="redis cache", session_id="s")],
            source_event_ids=["b" * 32],
        )
    )
    update_memory_fields(
        written["path"],
        {
            "updated_at": old.isoformat(),
            "last_recalled_at": old.isoformat(),
        },
    )
    payload = recall_memories("redis cache session", project="app", mark=False)
    hit = next(r for r in payload["results"] if r.get("path") == written["path"])
    assert hit["modality"] == "dusty"
    assert hit["tide"] == "dusty"
    assert "VERIFY-FIRST" in hit["agent_rail"]
    assert "DUSTY" in hit["label"]


def test_tide_thresholds_respect_freshness() -> None:
    cold = {
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=50)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=50)).isoformat(),
    }
    assert classify_tide(cold, freshness="high") == "dusty"
    assert classify_tide(cold, freshness="mid") == "aging"
    assert classify_tide(cold, freshness="low") == "hot"


def test_build_trust_view_provisional() -> None:
    from memorant_mcp.config import MemorantSettings

    view = build_trust_view(
        {
            "title": "maybe",
            "claim": "try X",
            "trust_tier": "provisional",
            "lifecycle": "active",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        settings=MemorantSettings(behavior=PersonaBehavior()),
    )
    assert view.modality == "suspicious"
    assert "HOLD" in view.agent_rail
