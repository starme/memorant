"""End-to-end Memorant path on a temporary vault root."""

from pathlib import Path

import pytest

from memorant_mcp.event_schema import EventInput
from memorant_mcp.journal import append_event, list_pending_events
from memorant_mcp.memory_schema import EvidenceRef, MemoryKind, MemoryWriteInput, TrustTier
from memorant_mcp.memory_store import write_memory
from memorant_mcp.promotion import promote_memory
from memorant_mcp.recall import recall_memories
from memorant_mcp.activity import append_activity, read_activity
from memorant_mcp.promotion import apply_feedback


def test_failure_to_promotion_to_correction_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))

    fail = append_event(
        EventInput(
            event_type="test.failure",
            session_id="sess-a",
            project="demo",
            source="claude-code-hook",
            tool_name="Bash",
            outcome="failure",
            evidence_excerpt="pytest failed: pool exhausted",
            tags=["test"],
        )
    )
    success = append_event(
        EventInput(
            event_type="test.success",
            session_id="sess-a",
            project="demo",
            source="claude-code-hook",
            tool_name="Bash",
            outcome="success",
            evidence_excerpt="pytest passed after pool bump",
            tags=["test"],
        )
    )
    pending = list_pending_events(project="demo")
    assert {e["event_id"] for e in pending} >= {fail["event_id"], success["event_id"]}

    # Evidence thin for A → write B
    memory = write_memory(
        MemoryWriteInput(
            title="Pool size fix",
            claim="Increase DB pool size when seeing pool exhausted",
            kind=MemoryKind.episodic,
            trust_tier=TrustTier.provisional,
            confidence=0.6,
            scope={"project": "demo"},
            evidence=[
                EvidenceRef(
                    source="test.failure",
                    excerpt="pool exhausted",
                    session_id="sess-a",
                )
            ],
            source_event_ids=[fail["event_id"], success["event_id"]],
            origin_session_ids=["sess-a"],
        )
    )
    append_activity(
        "memory.write",
        "provisional Pool size fix",
        project="demo",
        memory_path=memory["path"],
    )

    recalled = recall_memories("pool exhausted", project="demo", mark=False)
    assert recalled["count"] >= 1
    assert recalled["results"][0]["label"].startswith("[PROVISIONAL")

    promoted = promote_memory(
        memory["path"],
        evidence_session_id="sess-b",
        success_outcome="success",
        evidence_excerpt="reused in new session, tests green",
    )
    assert promoted["promoted"] is True

    corrected = apply_feedback(
        memory["path"],
        "corrected",
        note="real cause was DNS",
        session_id="sess-c",
        replacement_claim="Check DNS TTL before tuning pool size",
    )
    assert corrected["updated"]["lifecycle"] == "corrected"

    stopped = recall_memories("pool exhausted", project="demo", mark=False)
    # corrected memory serves as negative-trust near-pit (反面服役), not as usable truth
    neg = [r for r in stopped["results"] if r.get("path") == memory["path"]]
    assert neg and neg[0].get("modality") == "negative"
    assert "DENY" in (neg[0].get("agent_rail") or "")

    activity = read_activity(project="demo")
    assert activity["count"] >= 1
    assert (tmp_path / "activity").is_dir()
    assert (tmp_path / "journal").is_dir()
    assert (tmp_path / "memories").is_dir()
