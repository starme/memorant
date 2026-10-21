from pathlib import Path

import pytest

from memorant_mcp.memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryWriteInput,
    TrustTier,
)
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
            evidence=[
                EvidenceRef(source="chat", excerpt="prefer redis", session_id="s1")
            ],
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
                EvidenceRef(
                    source="incident", excerpt="cache outage fixed", session_id="s1"
                )
            ],
            source_event_ids=["f" * 32],
            origin_session_ids=["s1"],
        )
    )
    payload = recall_memories("redis cache", project="app", limit=5, mark=False)
    assert payload["count"] >= 2
    assert payload["results"][0]["trust_tier"] == "verified"
    assert (
        "待验证" in payload["results"][1]["label"]
        or payload["results"][1]["trust_tier"] == "provisional"
    )
    context = format_recall_context(payload)
    assert "Memorant recall" in context
    assert len(context) <= 8000


def _write_tagged(
    title: str,
    claim: str,
    *,
    project: str,
    stack: list[str] | None = None,
    language: list[str] | None = None,
    event_seed: str,
) -> None:
    write_memory(
        MemoryWriteInput(
            title=title,
            claim=claim,
            kind=MemoryKind.procedural,
            trust_tier=TrustTier.verified,
            confidence=0.9,
            scope={
                "project": project,
                "stack": stack or [],
                "language": language or [],
            },
            evidence=[EvidenceRef(source="chat", excerpt=claim[:80], session_id="s1")],
            source_event_ids=[event_seed * 32],
            origin_session_ids=["s1"],
        )
    )


def test_stack_tag_raises_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    _write_tagged(
        "retry helper",
        "retry helper for flaky calls",
        project="app",
        stack=["pytest"],
        event_seed="a",
    )
    _write_tagged(
        "retry helper other",
        "retry helper for flaky calls",
        project="app",
        event_seed="b",
    )

    payload = recall_memories("pytest retry helper flaky", limit=5, mark=False)
    by_title = {item["title"]: item["score"] for item in payload["results"]}

    assert by_title["retry helper"] > by_title["retry helper other"]


def test_language_tag_raises_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    _write_tagged(
        "retry backoff",
        "exponential backoff for retried calls",
        project="app",
        language=["python"],
        event_seed="c",
    )
    _write_tagged(
        "retry backoff other",
        "exponential backoff for retried calls",
        project="app",
        event_seed="d",
    )

    payload = recall_memories("python exponential backoff retried", limit=5, mark=False)
    by_title = {item["title"]: item["score"] for item in payload["results"]}

    assert by_title["retry backoff"] > by_title["retry backoff other"]


def test_cross_language_lesson_is_retrievable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lesson tagged for several languages still ranks on either query."""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    _write_tagged(
        "backoff lesson",
        "exponential backoff prevents thundering herd",
        project="app",
        language=["python", "go"],
        event_seed="e",
    )

    for query in ("python exponential backoff", "go exponential backoff"):
        payload = recall_memories(query, limit=5, mark=False)
        assert any(item["title"] == "backoff lesson" for item in payload["results"])


def test_untagged_memory_is_not_penalised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing labels are a normal state: no negative weight, still recallable."""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    _write_tagged(
        "generic lesson",
        "write tests before implementation",
        project="app",
        event_seed="f",
    )

    payload = recall_memories("write tests implementation", limit=5, mark=False)

    assert any(item["title"] == "generic lesson" for item in payload["results"])


def test_tag_bonus_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memorant_mcp.recall import TAG_BONUS_CAP, _score

    memory = {
        "title": "many tags",
        "claim": "many tags lesson",
        "scope": {
            "project": "app",
            "stack": ["python", "go", "rust", "java", "ruby"],
            "language": ["python", "go"],
        },
    }
    tokens = {"python", "go", "rust", "java", "ruby"}

    score = _score(memory, tokens, None, freshness="mid")

    # 5 stack hits would be 5.0 uncapped; project/tag credits stay bounded.
    assert score <= 10.0 + TAG_BONUS_CAP


def test_unrelated_project_memory_still_recalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A memory from another project must not be excluded, only un-bumped."""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    _write_tagged(
        "foreign lesson",
        "journal files are append only",
        project="other-project",
        event_seed="1",
    )
    _write_tagged(
        "home lesson",
        "journal files are append only now",
        project="app",
        event_seed="2",
    )

    payload = recall_memories(
        "journal files append only", project="app", limit=5, mark=False
    )
    titles = [item["title"] for item in payload["results"]]

    assert "home lesson" in titles
    assert "foreign lesson" in titles
    assert titles.index("home lesson") < titles.index("foreign lesson")
