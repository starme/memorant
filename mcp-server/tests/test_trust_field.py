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
    update_memory_fields(written["path"], {"lifecycle": LifecycleState.corrected.value})
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


def test_seasonal_clock_keeps_annual_out_of_dusty() -> None:
    """冻结文档 §3.4 季节钟防误伤：冷门但正确的年度类经验用预期复现节奏量沉睡，
    不用绝对天数一刀切。annual 记忆在预期复现窗口（365d）内即使超 dusty_days
    也不判 dusty（窗口内可唤醒），但也不判 hot（唤醒≠自动深信，line 122）——
    降为 aging 带复核语气。超出窗口（>365d）才按绝对天数判 dusty。"""
    # 200d: mid 的 dusty_days=90，ad-hoc 应判 dusty；annual 在 365d 窗口内 → aging
    cold_in_window = {
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
    }
    ad_hoc = dict(cold_in_window)
    annual = {**cold_in_window, "recurrence_cadence": "annual"}
    assert classify_tide(ad_hoc, freshness="mid") == "dusty"
    assert classify_tide(annual, freshness="mid") == "aging"
    # 超出 annual 窗口（400d > 365d）→ 恢复绝对天数判定 → dusty
    out_of_window = {
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        "recurrence_cadence": "annual",
    }
    assert classify_tide(out_of_window, freshness="mid") == "dusty"


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
