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


@pytest.mark.parametrize("hook_event_name", ["PreCompact", "SessionEnd"])
def test_boundary_events_write_and_return_nonblocking_context(
    tmp_path: Path, hook_event_name: str
) -> None:
    result, files = run_cli(
        tmp_path,
        {
            "hook_event_name": hook_event_name,
            "session_id": "s1",
            "cwd": "/work/project",
        },
    )
    output = json.loads(result.stdout)
    assert len(files) == 1
    assert output["hookSpecificOutput"]["hookEventName"] == hook_event_name
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context
    assert "Distill" in context


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
