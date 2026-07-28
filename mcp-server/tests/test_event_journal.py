import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
import pytest
from pydantic import ValidationError

from memorant_mcp.event_schema import Event, EventInput
from memorant_mcp.hook_core import _read_event, append_event_data
from memorant_mcp.journal import append_event, list_pending_events, redact_secrets
from memorant_mcp.server import (
    memorant_append_event,
    memorant_list_pending_events,
    vault_append_entry,
    vault_delete_entry,
    vault_update_frontmatter,
)


BASE = {
    "event_type": "tool.failure",
    "session_id": "session-1",
    "project": "memorant",
    "source": "claude-code-hook",
    "tool_name": "Bash",
    "outcome": "failure",
    "evidence_excerpt": "pytest failed",
    "tags": ["test"],
}


def test_event_input_generates_strict_server_fields() -> None:
    event = EventInput(**BASE).to_event()
    assert event.event_id
    assert event.observed_at.tzinfo is not None
    assert event.payload_hash == hashlib.sha256(
        EventInput(**BASE).canonical_payload()
    ).hexdigest()
    with pytest.raises(ValidationError):
        EventInput(**{**BASE, "event_type": "made.up"})
    with pytest.raises(ValidationError):
        EventInput(**{**BASE, "unknown": "value"})
    with pytest.raises(ValidationError):
        EventInput(**{**BASE, "session_id": "x" * 300})


def test_payload_hash_uses_redacted_normalized_semantics() -> None:
    first = EventInput(
        **{**BASE, "evidence_excerpt": "token=first-secret"}
    ).to_event()
    second = EventInput(
        **{**BASE, "evidence_excerpt": "token=second-secret"}
    ).to_event()
    assert first.payload_hash == second.payload_hash


def test_append_is_redacted_bounded_idempotent_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    raw = {
        **BASE,
        "evidence_excerpt": (
            "Authorization: Bearer abc.secret token=xyz password=hunter2 "
            "api_key=sk-live\n-----BEGIN PRIVATE KEY-----\nSECRET\n"
            "-----END PRIVATE KEY-----\n" + "z" * 10_000
        ),
    }
    first = append_event(EventInput(**raw))
    path = tmp_path / first["path"]
    original = path.read_text()
    second = append_event(EventInput(**raw))

    assert first == second
    assert len(list(tmp_path.glob("journal/**/*.md"))) == 1
    assert "[REDACTED]" in original
    assert "hunter2" not in original
    assert "abc.secret" not in original
    assert "PRIVATE KEY" not in original
    assert len(frontmatter.loads(original).content) <= 4200
    assert path.read_text() == original
    assert first["path"].startswith("journal/")
    assert ".." not in first["path"]


@pytest.mark.parametrize(
    "unsafe",
    [
        '{"env":{"HOME":"x","TOKEN":"secret"},"params":{"a":1}}',
        '{"role":"user","content":"full transcript line"}\n'
        '{"role":"assistant","content":"another line"}',
        "HOME=/Users/example\nPATH=/usr/bin\nSHELL=/bin/zsh\nUSER=example",
    ],
)
def test_redaction_falls_back_when_content_is_unsafe(unsafe: str) -> None:
    assert redact_secrets(unsafe) == "[CONTENT OMITTED: unsafe structured payload]"


@pytest.mark.parametrize(
    ("credential", "secret_value"),
    [
        ("AWS_SECRET_ACCESS_KEY=aws-secret", "aws-secret"),
        ("GITHUB_TOKEN=github-secret", "github-secret"),
        ("OPENAI_API_KEY=openai-secret", "openai-secret"),
        ("AUTH=auth-secret", "auth-secret"),
        ("Auth: mixed-secret", "mixed-secret"),
        ("private_key=underscore-secret", "underscore-secret"),
        ("PRIVATE-KEY: dash-secret", "dash-secret"),
        ("https://alice:db-secret@example.com/database", "db-secret"),
    ],
)
def test_redaction_covers_common_credentials(
    credential: str, secret_value: str
) -> None:
    assert secret_value not in redact_secrets(credential)


def test_redaction_variants_never_reach_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    secrets = "AUTH=alpha private_key=beta PRIVATE-KEY: gamma"
    created = append_event(EventInput(**{**BASE, "evidence_excerpt": secrets}))
    stored = (tmp_path / created["path"]).read_text()
    assert "alpha" not in stored
    assert "beta" not in stored
    assert "gamma" not in stored


def test_auth_redaction_respects_key_boundaries_and_quotes() -> None:
    text = (
        'author="Tal" authority=local auth="alpha" '
        "AUTH:'beta' Auth = gamma"
    )
    redacted = redact_secrets(text)
    assert 'author="Tal"' in redacted
    assert "authority=local" in redacted
    assert "alpha" not in redacted
    assert "beta" not in redacted
    assert "gamma" not in redacted


def test_pending_events_exclude_memory_references_and_sort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    first = append_event(EventInput(**BASE))
    second = append_event(EventInput(**{**BASE, "session_id": "session-2"}))
    assert [x["event_id"] for x in list_pending_events(session_id="session-1")] == [
        first["event_id"]
    ]
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "one.md").write_text(
        f"---\nsource_event_ids:\n- {first['event_id']}\n---\nbody\n"
    )
    pending = list_pending_events(project="memorant")
    assert [x["event_id"] for x in pending] == [second["event_id"]]


def test_pending_events_skip_session_start_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session.start is pure noise for Distill: it carries no migratable form, only
    a session boundary marker. Keep it in the journal (audit trail) but exclude
    from pending candidates so the Distill list stays signal, not every-session spam."""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    start = append_event(EventInput(**{**BASE, "event_type": "session.start"}))
    failure = append_event(EventInput(**BASE))
    pending_ids = [x["event_id"] for x in list_pending_events()]
    assert failure["event_id"] in pending_ids
    assert start["event_id"] not in pending_ids


def test_non_dev_events_append_and_enter_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-dev milestones (doc.commit / decision.adopt) are the non-dev entry to the
    journal→Distill chain (backlog P2). They append like any event and surface in
    pending so Flow E can distill them — no Bash hook required, observer/command triggered."""
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    doc = append_event(
        EventInput(**{**BASE, "event_type": "doc.commit", "evidence_excerpt": "PRD 定稿"})
    )
    decision = append_event(
        EventInput(
            **{**BASE, "event_type": "decision.adopt", "evidence_excerpt": "采纳 Redis 缓存"}
        )
    )
    pending_ids = {x["event_id"] for x in list_pending_events()}
    assert doc["event_id"] in pending_ids
    assert decision["event_id"] in pending_ids


def test_structured_mcp_tools_return_dicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    created = asyncio.run(memorant_append_event(**BASE))
    pending = asyncio.run(memorant_list_pending_events(session_id="session-1"))
    assert created["event_type"] == "tool.failure"
    assert pending["events"][0]["event_id"] == created["event_id"]


def test_legacy_mutation_tools_cannot_change_journal_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    created = append_event(EventInput(**BASE))
    path = tmp_path / created["path"]
    original = path.read_text()

    append_result = asyncio.run(vault_append_entry(created["path"], "changed"))
    update_result = asyncio.run(
        vault_update_frontmatter(created["path"], "outcome", "changed")
    )
    delete_result = asyncio.run(vault_delete_entry(created["path"], confirm=True))

    assert "IMMUTABLE:" in append_result
    assert "IMMUTABLE:" in update_result
    assert "IMMUTABLE:" in delete_result
    assert append_result.startswith("[deprecated:")
    assert path.read_text() == original


def test_append_never_overwrites_colliding_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    fixed = Event(
        **BASE,
        event_id="a" * 32,
        observed_at=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
        payload_hash=EventInput(**BASE).to_event().payload_hash,
    )
    monkeypatch.setattr(EventInput, "to_event", lambda self: fixed)
    original_rel = (
        "journal/2026/07/23/"
        "20260723T120000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md"
    )
    original = tmp_path / original_rel
    original.parent.mkdir(parents=True)
    original.write_text("collision sentinel")

    created = append_event(EventInput(**BASE))

    assert original.read_text() == "collision sentinel"
    assert created["path"] != original_rel
    assert (tmp_path / created["path"]).is_file()


@pytest.mark.parametrize("symlink_level", ["journal", "year", "date"])
def test_append_rejects_symlink_escape_without_external_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlink_level: str
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    if symlink_level == "journal":
        (root / "journal").symlink_to(outside, target_is_directory=True)
    elif symlink_level == "year":
        today = datetime.now(timezone.utc)
        journal = root / "journal"
        journal.mkdir()
        (journal / f"{today:%Y}").symlink_to(outside, target_is_directory=True)
    else:
        today = datetime.now(timezone.utc)
        parent = root / f"journal/{today:%Y/%m}"
        parent.mkdir(parents=True)
        (parent / f"{today:%d}").symlink_to(outside, target_is_directory=True)

    with pytest.raises((OSError, ValueError)):
        append_event(EventInput(**BASE))

    assert list(outside.iterdir()) == []


def test_frontmatter_json_scalars_roundtrip_without_yaml_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    project = "project\n---\noutcome: injected"
    tags = ["null", "tag\n---\nsource: injected"]
    created = append_event(
        EventInput(
            **{
                **BASE,
                "project": project,
                "outcome": "null",
                "tags": tags,
            }
        )
    )

    post = frontmatter.load(tmp_path / created["path"])
    pending = list_pending_events()
    assert post["project"] == project
    assert post["outcome"] == "null"
    assert post["tags"] == tags
    assert pending[0]["project"] == project
    assert pending[0]["outcome"] == "null"
    assert pending[0]["tags"] == tags


def test_stdlib_writer_rejects_invalid_fixed_fields_before_writing(
    tmp_path: Path,
) -> None:
    event = EventInput(**BASE).to_event().model_dump(mode="json")
    event["event_id"] = "bad\n---\nproject: injected"

    with pytest.raises(ValueError):
        append_event_data(event, root=str(tmp_path))

    assert not (tmp_path / "journal").exists()


def test_stdlib_writer_rejects_mismatched_semantic_hash_before_writing(
    tmp_path: Path,
) -> None:
    event = EventInput(**BASE).to_event().model_dump(mode="json")
    event["project"] = "tampered"

    with pytest.raises(ValueError):
        append_event_data(event, root=str(tmp_path))

    assert not (tmp_path / "journal").exists()


def test_corrupt_journal_and_memory_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    journal = tmp_path / "journal"
    memories = tmp_path / "memories"
    journal.mkdir()
    memories.mkdir()
    (journal / "corrupt.md").write_text("---\npayload_hash: [\n---\n")
    (memories / "corrupt.md").write_text("---\nsource_event_ids: [\n---\n")

    created = append_event(EventInput(**BASE))
    pending = list_pending_events()

    assert (journal / "corrupt.md").read_text() == "---\npayload_hash: [\n---\n"
    assert (memories / "corrupt.md").read_text() == "---\nsource_event_ids: [\n---\n"
    assert [event["event_id"] for event in pending] == [created["event_id"]]


def test_semantically_corrupt_matching_journal_does_not_block_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    payload_hash = EventInput(**BASE).to_event().payload_hash
    corrupt = tmp_path / "journal" / "incomplete.md"
    corrupt.parent.mkdir()
    corrupt.write_text(f"---\npayload_hash: {payload_hash}\n---\n")

    created = append_event(EventInput(**BASE))

    assert created["path"] != "journal/incomplete.md"
    assert corrupt.read_text() == f"---\npayload_hash: {payload_hash}\n---\n"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("event_id", "not-an-id"),
        ("event_type", "poison.event"),
        ("observed_at", "not-a-time"),
        ("session_id", 123),
        ("project", None),
        ("source", ["wrong"]),
        ("tags", "not-a-list"),
    ],
)
def test_same_hash_poisoned_event_is_never_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    bad_value: object,
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    valid = EventInput(**BASE).to_event().model_dump(mode="json")
    valid[field] = bad_value
    corrupt = tmp_path / "journal" / f"poison-{field}.md"
    corrupt.parent.mkdir()
    lines = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in valid.items()
    )
    original = f"---\n{lines}\n---\n"
    corrupt.write_text(original)

    created = append_event(EventInput(**BASE))

    assert created["path"] != f"journal/poison-{field}.md"
    assert corrupt.read_text() == original


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("project", "other-project"),
        ("outcome", "success"),
        ("evidence_excerpt", "tampered evidence"),
        ("tags", ["tampered"]),
    ],
)
def test_same_hash_semantically_tampered_event_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    tampered: object,
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    valid = EventInput(**BASE).to_event().model_dump(mode="json")
    valid[field] = tampered
    corrupt = tmp_path / "journal" / f"tampered-{field}.md"
    corrupt.parent.mkdir()
    original = frontmatter.dumps(frontmatter.Post("# event\n", **valid))
    corrupt.write_text(original)

    created = append_event(EventInput(**BASE))

    assert created["path"] != f"journal/tampered-{field}.md"
    assert corrupt.read_text() == original


def test_stdlib_reader_matches_legacy_multiline_yaml_scalar(
    tmp_path: Path,
) -> None:
    evidence = "first line\nsecond: value\n--- literal marker\nlast line"
    event = EventInput(
        **{**BASE, "evidence_excerpt": evidence}
    ).to_event().model_dump(mode="json")
    path = tmp_path / "legacy-multiline.md"
    path.write_text(frontmatter.dumps(frontmatter.Post("# event\n", **event)))

    expected = dict(frontmatter.load(path).metadata)
    actual = _read_event(path)

    assert actual["evidence_excerpt"] == expected["evidence_excerpt"] == evidence


def test_valid_legacy_yaml_event_remains_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    event = EventInput(**BASE).to_event()
    legacy = tmp_path / "journal" / "legacy.md"
    legacy.parent.mkdir()
    legacy.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "# tool.failure\n",
                **event.model_dump(mode="json"),
            )
        )
    )

    created = append_event(EventInput(**BASE))

    assert created["path"] == "journal/legacy.md"
    assert len(list((tmp_path / "journal").glob("*.md"))) == 1
