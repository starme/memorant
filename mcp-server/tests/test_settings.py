from pathlib import Path

import pytest

from memorant_mcp.config import (
    load_flags,
    load_settings,
    persona_distill_guidance,
)
from memorant_mcp.hook_cli import PLAIN_TEXT, process
from memorant_mcp.hook_core import discover_root


def _clear_flag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MEMORANT_AUTO_CAPTURE",
        "MEMORANT_AUTO_WRITE_VERIFIED",
        "MEMORANT_AUTO_WRITE_PROVISIONAL",
        "MEMORANT_EVENT_RECALL",
        "MEMORANT_ACTIVITY_SUMMARY",
        "MEMORANT_ROOT",
        "VAULT_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_rigorous_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.behavior.preset == "rigorous"
    assert settings.behavior.selectivity == "high"
    assert settings.behavior.voice == "mid"
    assert settings.behavior.guardrail == "high"
    assert settings.tone == "warm"
    assert settings.cloud_projection == "line_draft"
    assert settings.flags.auto_capture is True


def test_project_settings_override_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.settings.json").write_text(
        '{"persona":{"behavior":{"preset":"diligent"},"tone":"serious"},'
        '"system":{"event_recall":true}}',
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "memorant.settings.json").write_text(
        '{"persona":{"behavior":{"preset":"silent"},"tone":"playful"},'
        '"system":{"event_recall":false}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    settings = load_settings()
    assert settings.behavior.preset == "silent"
    assert settings.behavior.voice == "low"
    assert settings.tone == "playful"
    assert settings.flags.event_recall is False


def test_custom_dims_flip_preset_to_custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.settings.json").write_text(
        '{"persona":{"behavior":{"preset":"rigorous","voice":"high"}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.behavior.preset == "custom"
    assert settings.behavior.voice == "high"
    assert settings.behavior.selectivity == "high"


def test_env_still_overrides_settings_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.settings.json").write_text(
        '{"system":{"auto_capture":true}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORANT_AUTO_CAPTURE", "false")
    flags = load_flags()
    assert flags.auto_capture is False


def test_settings_root_used_by_discover_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    root = tmp_path / "memorant-lib"
    root.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "memorant.settings.json").write_text(
        f'{{"root":"{root}"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    assert discover_root() == str(root.resolve())


def test_persona_guidance_mentions_selectivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    text = persona_distill_guidance(load_settings())
    assert "selectivity=high" in text
    assert (
        "false forms worse" in text.lower()
        or "False forms" in text
        or "prefer skip" in text
    )


def test_distill_context_includes_persona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    out = process(
        {
            "hook_event_name": "PreCompact",
            "session_id": "s1",
            "cwd": str(tmp_path),
        }
    )
    # PreCompact returns a plain-text marker (context reaches the model via
    # stdout, not hookSpecificOutput.additionalContext).
    assert isinstance(out, str)
    assert out.startswith(PLAIN_TEXT)
    ctx = out[len(PLAIN_TEXT) :]
    assert "Memorant Distill" in ctx
    assert "persona: behavior=rigorous" in ctx


def test_bash_failure_evidence_thickened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_flag_env(monkeypatch)
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    out = process(
        {
            "hook_event_name": "PostToolUseFailure",
            "session_id": "s-fail",
            "cwd": str(tmp_path / "demo"),
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_x.py"},
            "tool_response": {
                "stdout": "1 failed",
                "stderr": "AssertionError: boom",
                "exit_code": 1,
            },
            "error": "command failed",
        }
    )
    assert out  # may have recall or recorded note
    journal = list(tmp_path.glob("journal/**/*.md"))
    assert journal
    body = journal[0].read_text(encoding="utf-8")
    assert "command: pytest tests/test_x.py" in body
    assert "exit_code: 1" in body
    assert "stderr: AssertionError: boom" in body
