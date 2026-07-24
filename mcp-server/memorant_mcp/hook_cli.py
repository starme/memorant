"""Fail-open Claude Code hook JSON to Memorant journal + recall adapter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .config import load_flags
    from .hook_core import append_hook_event, discover_root
except ImportError:  # Direct plugin-source execution by the shell shim.
    from config import load_flags  # type: ignore
    from hook_core import append_hook_event, discover_root  # type: ignore

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
RECALL_MAX_CHARS = 8000


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _project(payload: dict[str, Any]) -> str:
    explicit = _text(payload.get("project"), 256).strip()
    if explicit:
        return explicit
    cwd = _text(payload.get("cwd"), 1024).rstrip("/")
    return Path(cwd).name[:256] if cwd else "unknown"


def _post_tool_event(
    payload: dict[str, Any], *, hook_failed: bool = False
) -> tuple[str, str, str] | None:
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    command = _text(tool_input.get("command"), 2048) if isinstance(tool_input, dict) else ""
    stdout = _text(response.get("stdout"), 2048) if isinstance(response, dict) else ""
    stderr = _text(response.get("stderr"), 2048) if isinstance(response, dict) else ""
    evidence = "\n".join(part for part in (command, stdout, stderr) if part)
    exit_code = response.get("exit_code") if isinstance(response, dict) else None
    if exit_code is None:
        exit_code = payload.get("exit_code")
    structured_failure = isinstance(exit_code, int) and exit_code != 0
    if re.search(r"(^|\s)git\s+commit\b", command):
        outcome = "failure" if hook_failed or structured_failure else "success"
        return "git.commit", outcome, evidence
    if _TEST_COMMAND.search(command):
        failed = hook_failed or structured_failure
        return ("test.failure" if failed else "test.success"), (
            "failure" if failed else "success"
        ), evidence
    return None


def _load_recall():
    try:
        from .recall import format_recall_context, recall_memories

        return format_recall_context, recall_memories
    except Exception:
        try:
            from recall import format_recall_context, recall_memories  # type: ignore

            return format_recall_context, recall_memories
        except Exception:
            return None, None


def _load_session_summary():
    try:
        from .activity import session_end_summary

        return session_end_summary
    except Exception:
        try:
            from activity import session_end_summary  # type: ignore

            return session_end_summary
        except Exception:
            return None


def _recall_context(query: str, project: str, trigger: str) -> str:
    try:
        if not load_flags().event_recall:
            return ""
    except Exception:
        return ""
    format_recall_context, recall_memories = _load_recall()
    if recall_memories is None or format_recall_context is None:
        return ""
    try:
        discover_root()
    except Exception:
        return ""
    try:
        payload = recall_memories(
            query,
            project=project,
            trigger=trigger,
            limit=5,
            include_provisional=True,
            mark=False,
        )
        if not payload.get("count"):
            return ""
        return format_recall_context(payload, max_chars=RECALL_MAX_CHARS)
    except Exception:
        return ""


def _context_output(hook_name: str, context: str) -> dict[str, Any]:
    if not context:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_name,
            "additionalContext": context[:RECALL_MAX_CHARS],
        }
    }


def _maybe_append(event: dict[str, Any]) -> bool:
    try:
        if not load_flags().auto_capture:
            return False
        append_hook_event(event)
        return True
    except Exception:
        return False


def process(payload: dict[str, Any]) -> dict[str, Any]:
    hook_name = _text(payload.get("hook_event_name"), 64)
    project = _project(payload)

    if hook_name == "UserPromptSubmit":
        prompt = _text(payload.get("prompt"), 2000) or _text(
            payload.get("user_prompt"), 2000
        )
        context = _recall_context(prompt or project, project, "UserPromptSubmit")
        return _context_output(hook_name, context)

    if hook_name == "SessionStart":
        wrote = _maybe_append(
            {
                "event_type": "session.start",
                "session_id": _text(payload.get("session_id"), 256) or "unknown",
                "project": project,
                "source": "claude-code-hook",
                "tool_name": None,
                "outcome": "observed",
                "evidence_excerpt": None,
                "tags": ["hook", hook_name],
            }
        )
        context = _recall_context(project, project, "SessionStart")
        if context:
            return _context_output(hook_name, context)
        if wrote:
            return _context_output(hook_name, "Memorant journal event recorded.")
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
        selected = _post_tool_event(payload, hook_failed=True)
        if selected is not None and selected[0] == "test.failure":
            event_type, outcome, command_evidence = selected
        else:
            command_evidence = ""
        evidence = _text(payload.get("error"), 4096)
        if not evidence:
            response = payload.get("tool_response")
            if isinstance(response, dict):
                evidence = _text(response.get("stderr"), 4096)
        evidence = "\n".join(
            part for part in (command_evidence, evidence) if part
        )
    else:
        evidence = _text(payload.get("reason"), 1024) or _text(
            payload.get("error"), 1024
        )

    wrote = _maybe_append(
        {
            "event_type": event_type,
            "session_id": _text(payload.get("session_id"), 256) or "unknown",
            "project": project,
            "source": "claude-code-hook",
            "tool_name": _text(payload.get("tool_name"), 128) or None,
            "outcome": outcome,
            "evidence_excerpt": evidence or None,
            "tags": ["hook", hook_name],
        }
    )

    if hook_name == "PostToolUseFailure":
        context = ""
        if evidence:
            context = _recall_context(evidence[:500], project, "PostToolUseFailure")
        if context:
            return _context_output(hook_name, context)
        if wrote:
            return _context_output(hook_name, "Memorant journal event recorded.")
        return {}

    if hook_name == "SessionEnd":
        try:
            if not load_flags().activity_summary:
                return {}
        except Exception:
            return {}
        summary_fn = _load_session_summary()
        summary = ""
        if summary_fn is not None:
            try:
                summary = summary_fn(project=project)
            except Exception:
                summary = ""
        return _context_output(hook_name, summary)

    if hook_name == "PreCompact":
        return _context_output(
            hook_name,
            "Memorant: pending journal events may need Distill before compact.",
        )

    if wrote:
        return {
            "hookSpecificOutput": {
                "hookEventName": hook_name,
                "additionalContext": "Memorant journal event recorded.",
            }
        }
    return {}


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
