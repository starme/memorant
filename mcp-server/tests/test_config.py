from pathlib import Path

import pytest

from memorant_mcp.config import get_ingest_settings, load_flags
from memorant_mcp.hook_cli import process
from memorant_mcp.memory_schema import (
    EvidenceRef,
    MemoryKind,
    MemoryWriteInput,
    TrustTier,
)
from memorant_mcp.memory_store import write_memory


def _clear_flag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEMORANT_AUTO_CAPTURE",
        "MEMORANT_AUTO_WRITE_VERIFIED",
        "MEMORANT_AUTO_WRITE_PROVISIONAL",
        "MEMORANT_EVENT_RECALL",
        "MEMORANT_ACTIVITY_SUMMARY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_env_overrides_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORANT_AUTO_CAPTURE", "false")
    monkeypatch.setenv("MEMORANT_EVENT_RECALL", "0")
    flags = load_flags()
    assert flags.auto_capture is False
    assert flags.event_recall is False
    assert flags.auto_write_verified is True


def test_env_disables_auto_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORANT_AUTO_CAPTURE", "false")
    out = process(
        {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": "/tmp/demo",
        }
    )
    assert out == {}
    assert list(tmp_path.glob("journal/**/*.md")) == []


def test_auto_write_provisional_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORANT_AUTO_WRITE_PROVISIONAL", "0")
    with pytest.raises(ValueError, match="auto_write_provisional"):
        write_memory(
            MemoryWriteInput(
                title="blocked",
                claim="should not write",
                kind=MemoryKind.semantic,
                trust_tier=TrustTier.provisional,
                confidence=0.4,
                evidence=[EvidenceRef(source="t", excerpt="x", session_id="s")],
                source_event_ids=["c" * 32],
            )
        )


def test_local_md_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "memorant.local.md").write_text(
        "---\nroot: /tmp/unused\nevent_recall: false\n---\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    flags = load_flags()
    assert flags.event_recall is False


def test_ingest_settings_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    ingest = get_ingest_settings()
    assert ingest["max_bytes_file"] == 5 * 1024 * 1024
    assert ingest["max_bytes_paste"] == 1 * 1024 * 1024
    assert ingest["source_allow_dirs"] == []


def test_ingest_settings_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "memorant.settings.json").write_text(
        '{"ingest": {"max_bytes_paste": 100, "source_allow_dirs": ["/tmp/allowed"]}}'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    ingest = get_ingest_settings()
    assert ingest["max_bytes_paste"] == 100
    assert ingest["source_allow_dirs"] == ["/tmp/allowed"]
