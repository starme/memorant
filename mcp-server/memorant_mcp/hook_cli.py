"""Fail-open Claude Code hook JSON to Memorant journal + recall adapter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .config import load_flags, load_settings, persona_distill_guidance
    from .hook_core import append_hook_event, discover_root
except ImportError:  # Direct plugin-source execution by the shell shim.
    from config import load_flags, load_settings, persona_distill_guidance  # type: ignore
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

_DISTILL_NOW = (
    "Memorant Distill (required, proactive): immediately call "
    "memorant_list_pending_events, run silent ontology gate, then "
    "memorant_write_memory for each qualified form (auto-write A/B). "
    "Do NOT ask the user whether to distill. Skip without writing when "
    "no migratable form (leave journal only). Never invent evidence."
)


def _distill_block(extra: str = "") -> str:
    """Distill instruction plus persona guidance (fail-open)."""
    parts = [_DISTILL_NOW]
    if extra:
        parts.insert(0, extra)
    try:
        parts.append(persona_distill_guidance(load_settings()))
    except Exception:
        pass
    return _join_context(*parts)


# Sentinel returned by process() when the hook's context must reach the model as
# stdout plain text rather than hookSpecificOutput.additionalContext. PreCompact
# is the only hook whose context channel is stdout (it does not accept
# additionalContext); the shim forwards its stdout verbatim without JSON checks.
PLAIN_TEXT = "__memorant_plain_text__"


def _plain(context: str) -> str:
    """Mark a context payload for plain-text stdout emission (PreCompact)."""
    return PLAIN_TEXT + context


def _bash_failure_evidence(payload: dict[str, Any]) -> str:
    """Thicken Bash failure evidence: command + exit + stderr/stdout labels."""
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    command = _text(tool_input.get("command"), 4096) if isinstance(tool_input, dict) else ""
    stdout = ""
    stderr = ""
    exit_code: Any = None
    if isinstance(response, dict):
        stdout = _text(response.get("stdout"), 4096)
        stderr = _text(response.get("stderr"), 4096)
        exit_code = response.get("exit_code")
    if exit_code is None:
        exit_code = payload.get("exit_code")
    error = _text(payload.get("error"), 4096)
    parts: list[str] = []
    if command:
        parts.append(f"command: {command}")
    if isinstance(exit_code, int) or (
        isinstance(exit_code, str) and exit_code.strip()
    ):
        parts.append(f"exit_code: {exit_code}")
    if stderr:
        parts.append(f"stderr: {stderr}")
    if error and error not in stderr:
        parts.append(f"error: {error}")
    if stdout:
        parts.append(f"stdout: {stdout}")
    return "\n".join(parts)


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _project_fields(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return (project_label, project_key)."""
    explicit = _text(payload.get("project"), 256).strip() or None
    cwd = _text(payload.get("cwd"), 1024).rstrip("/") or None
    try:
        from .project_identity import resolve_project_identity

        identity = resolve_project_identity(cwd, explicit_project=explicit)
        return identity.project_label, identity.project_key
    except Exception:
        try:
            from project_identity import resolve_project_identity  # type: ignore

            identity = resolve_project_identity(cwd, explicit_project=explicit)
            return identity.project_label, identity.project_key
        except Exception:
            if explicit:
                return explicit, None
            if cwd:
                return Path(cwd).name[:256] or "unknown", None
            return "unknown", None


def _join_context(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


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
    project, project_key = _project_fields(payload)

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
                "project_key": project_key,
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
            event_type, outcome, _command_evidence = selected
        evidence = _bash_failure_evidence(payload)
        if not evidence:
            evidence = _text(payload.get("error"), 4096)
    else:
        evidence = _text(payload.get("reason"), 1024) or _text(
            payload.get("error"), 1024
        )

    wrote = _maybe_append(
        {
            "event_type": event_type,
            "session_id": _text(payload.get("session_id"), 256) or "unknown",
            "project": project,
            "project_key": project_key,
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
        # SessionEnd cannot inject context into the model (the session is over,
        # there is no subsequent turn to receive it). Distill still runs via the
        # journal event written above; any needed recall happens on the next
        # SessionStart. Emit empty output — do not attempt additionalContext.
        return {}

    if hook_name == "PreCompact":
        # PreCompact does not accept hookSpecificOutput.additionalContext; its
        # context is injected via stdout plain text (appended as custom compact
        # instructions). Return a plain-text marker consumed by main().
        return _plain(
            _distill_block(
                "Memorant Distill (required before compact): pending journal events "
                "must be distilled now or they may be lost."
            )
        )

    if wrote and event_type in {"test.success", "git.commit"}:
        recorded = "Memorant journal event recorded."
        return _context_output(
            hook_name,
            _join_context(recorded, f"Trigger: {event_type}.", _distill_block()),
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
    output: dict[str, Any] | str = {}
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            output = process(payload)
    except Exception:
        output = {}
    if isinstance(output, str) and output.startswith(PLAIN_TEXT):
        # PreCompact: emit context as stdout plain text (no JSON envelope).
        text = output[len(PLAIN_TEXT) :][:RECALL_MAX_CHARS]
        sys.stdout.write(text + "\n")
        return
    rendered = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > 10_000:
        rendered = "{}"
    sys.stdout.write(rendered + "\n")


if __name__ == "__main__":
    main()
