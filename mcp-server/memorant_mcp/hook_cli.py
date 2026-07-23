"""Fail-open Claude Code hook JSON to Memorant journal adapter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .event_schema import EventInput
from .journal import append_event

_EVENT_MAP = {
    "SessionStart": "session.start",
    "SessionEnd": "session.end",
    "PreCompact": "context.precompact",
    "PostToolUseFailure": "tool.failure",
}
_TEST_COMMAND = re.compile(
    r"(^|[;&|]\s*)(pytest|python\s+-m\s+pytest|npm\s+(run\s+)?test|"
    r"pnpm\s+(run\s+)?test|yarn\s+test|go\s+test|cargo\s+test)\b",
    re.IGNORECASE,
)
_FAILURE = re.compile(
    r"\b(fail(?:ed|ure)?|error|exception|traceback|not ok)\b", re.IGNORECASE
)


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _project(payload: dict[str, Any]) -> str:
    explicit = _text(payload.get("project"), 256).strip()
    if explicit:
        return explicit
    cwd = _text(payload.get("cwd"), 1024).rstrip("/")
    return Path(cwd).name[:256] if cwd else "unknown"


def _post_tool_event(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    command = _text(tool_input.get("command"), 2048) if isinstance(tool_input, dict) else ""
    stdout = _text(response.get("stdout"), 2048) if isinstance(response, dict) else ""
    stderr = _text(response.get("stderr"), 2048) if isinstance(response, dict) else ""
    evidence = "\n".join(part for part in (command, stdout, stderr) if part)
    if re.search(r"(^|\s)git\s+commit\b", command):
        return "git.commit", "success", evidence
    if _TEST_COMMAND.search(command):
        failed = bool(_FAILURE.search(stdout + "\n" + stderr))
        return ("test.failure" if failed else "test.success"), (
            "failure" if failed else "success"
        ), evidence
    return None


def process(payload: dict[str, Any]) -> dict[str, Any]:
    hook_name = _text(payload.get("hook_event_name"), 64)
    if hook_name == "UserPromptSubmit":
        return {}
    event_type = _EVENT_MAP.get(hook_name)
    outcome = "observed"
    evidence = ""
    if hook_name == "PostToolUse":
        selected = _post_tool_event(payload)
        if selected is None:
            return {}
        event_type, outcome, evidence = selected
    elif event_type is None:
        return {}
    elif hook_name == "PostToolUseFailure":
        outcome = "failure"
        evidence = _text(payload.get("error"), 4096)
        if not evidence:
            response = payload.get("tool_response")
            if isinstance(response, dict):
                evidence = _text(response.get("stderr"), 4096)
    else:
        evidence = _text(payload.get("reason"), 1024) or _text(
            payload.get("error"), 1024
        )

    append_event(
        EventInput(
            event_type=event_type,
            session_id=_text(payload.get("session_id"), 256) or "unknown",
            project=_project(payload),
            source="claude-code-hook",
            tool_name=_text(payload.get("tool_name"), 128) or None,
            outcome=outcome,
            evidence_excerpt=evidence or None,
            tags=["hook", hook_name],
        )
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_name,
            "additionalContext": "Memorant journal event recorded.",
        }
    }


def main() -> None:
    output: dict[str, Any] = {}
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            output = process(payload)
    except Exception:
        output = {}
    rendered = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > 10_000:
        rendered = "{}"
    sys.stdout.write(rendered + "\n")


if __name__ == "__main__":
    main()
