import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(tmp_path: Path, payload: dict | str) -> tuple[subprocess.CompletedProcess[str], list]:
    env = {**os.environ, "MEMORANT_ROOT": str(tmp_path)}
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    result = subprocess.run(
        [sys.executable, "-m", "memorant_mcp.hook_cli"],
        input=raw,
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=5,
    )
    files = list(tmp_path.glob("journal/**/*.md"))
    return result, files


@pytest.mark.parametrize(
    ("hook_event_name", "event_type"),
    [
        ("SessionStart", "session.start"),
        ("PostToolUseFailure", "tool.failure"),
    ],
)
def test_maps_lifecycle_and_failure_events(
    tmp_path: Path, hook_event_name: str, event_type: str
) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": hook_event_name,
            "session_id": "s1",
            "cwd": "/work/project",
            "tool_name": "Bash",
            "error": 'bad "quoted"\nsecret token=abc',
        },
    )
    output = json.loads(result.stdout)
    assert result.returncode == 0
    assert len(result.stdout) <= 10_000
    assert output["hookSpecificOutput"]["hookEventName"] == hook_event_name
    assert len(files) == 1
    text = files[0].read_text()
    assert f"event_type: {event_type}" in text
    if hook_event_name == "PostToolUseFailure":
        assert "token=[REDACTED]" in text


@pytest.mark.parametrize(
    ("command", "stdout", "event_type"),
    [
        ("git commit -m 'fix: safe'", "[main abc1234] fix: safe", "git.commit"),
        ("pytest -q", "2 passed", "test.success"),
        ("npm test", "0 errors", "test.success"),
    ],
)
def test_maps_selected_successful_post_tool_use(
    tmp_path: Path, command: str, stdout: str, event_type: str
) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": "/work/project",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": stdout},
        },
    )
    json.loads(result.stdout)
    assert len(files) == 1
    assert f"event_type: {event_type}" in files[0].read_text()


def test_failed_test_hook_maps_to_test_failure(tmp_path: Path) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "PostToolUseFailure",
            "session_id": "s1",
            "cwd": "/work/project",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"stderr": "collection interrupted"},
        },
    )
    json.loads(result.stdout)
    assert len(files) == 1
    assert "event_type: test.failure" in files[0].read_text()
    assert "outcome: failure" in files[0].read_text()


def test_structured_exit_code_overrides_post_tool_success(tmp_path: Path) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": "/work/project",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"stdout": "0 errors", "exit_code": 1},
        },
    )
    json.loads(result.stdout)
    assert "event_type: test.failure" in files[0].read_text()
    assert "outcome: failure" in files[0].read_text()


def test_precompact_emits_plain_text_context(tmp_path: Path) -> None:
    # PreCompact does not accept hookSpecificOutput.additionalContext; its context
    # is injected via stdout plain text (appended as custom compact instructions).
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "PreCompact",
            "session_id": "s1",
            "cwd": "/work/project",
        },
    )
    assert result.returncode == 0
    # PreCompact writes a journal event too.
    assert len(files) == 1
    stdout = result.stdout.strip()
    # Plain text, not JSON: must fail to parse as JSON and carry the distill block.
    with pytest.raises(json.JSONDecodeError):
        json.loads(stdout)
    assert "Distill" in stdout


def test_session_end_emits_empty_and_writes_journal(tmp_path: Path) -> None:
    # SessionEnd cannot inject context into the model (session already ended);
    # it only writes the journal event for audit and returns empty.
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "SessionEnd",
            "session_id": "s1",
            "cwd": "/work/project",
        },
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert len(files) == 1


def test_test_success_injects_proactive_distill(tmp_path: Path) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": "/work/project",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"stdout": "1 passed"},
        },
    )
    output = json.loads(result.stdout)
    assert len(files) == 1
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "Distill" in context
    assert "Do NOT ask" in context
    assert "project_key:" in files[0].read_text() or "project_key" in files[0].read_text()


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "PostToolUse", "tool_name": "Read"},
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
        {"hook_event_name": "UserPromptSubmit", "prompt": "do not record me"},
    ],
)
def test_ignores_ordinary_and_prompt_events(tmp_path: Path, payload: dict) -> None:
    result, files = run_cli(tmp_path, payload)
    assert json.loads(result.stdout) == {}
    assert files == []


def test_bad_json_and_missing_config_fail_open(tmp_path: Path) -> None:
    result, files = run_cli(tmp_path, "{")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert files == []

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env.pop("MEMORANT_ROOT", None)
    env.pop("VAULT_ROOT", None)
    result = subprocess.run(
        [sys.executable, "-m", "memorant_mcp.hook_cli"],
        input='{"hook_event_name":"SessionStart"}',
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=5,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}


def test_legacy_migration_hint_persists_reminded_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次启动提示落盘 reminded 标记，避免每次 SessionStart 重复打扰（P0-3）。"""
    from memorant_mcp import hook_cli

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    bug = tmp_path / "bugs" / "example.md"
    bug.parent.mkdir()
    bug.write_text("---\ntype: bug\ntitle: t\n---\n\nbody\n")

    first = hook_cli._legacy_migration_hint()
    assert first and "迁移" in first

    state_path = tmp_path / ".memorant" / "migration-state.json"
    assert state_path.is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["reminded"] is True

    # 已提醒 → 不再重复提示。
    assert hook_cli._legacy_migration_hint() == ""


def test_legacy_migration_hint_none_when_no_legacy_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memorant_mcp import hook_cli

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    assert hook_cli._legacy_migration_hint() == ""


def test_session_start_migration_hint_not_tied_to_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SessionStart 的迁移提示必须与召回结果解耦：有召回命中时仍提示迁移。"""
    from memorant_mcp import hook_cli

    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    bug = tmp_path / "bugs" / "example.md"
    bug.parent.mkdir()
    bug.write_text("---\ntype: bug\ntitle: t\n---\n\nbody\n")

    # 模拟召回有命中：迁移提示仍须出现（而非被召回短路）。
    monkeypatch.setattr(hook_cli, "_recall_context", lambda *a, **k: "RECALL_CONTEXT")

    output = hook_cli.process(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(tmp_path)}
    )
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "迁移" in context
    assert "RECALL_CONTEXT" in context
