"""Integration tests for the memorant-hook.sh shim dispatch.

The shim is one script invoked by multiple hook types. PreCompact must emit
stdout plain text (it cannot accept hookSpecificOutput.additionalContext);
other hooks emit JSON. The shim must dispatch by hook_event_name and not run
plain-text output through JSON validation (which would discard it).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIM = REPO_ROOT / "hooks" / "memorant-hook.sh"


def _run_shim(
    tmp_path: Path,
    payload: dict,
    plugin_root: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "MEMORANT_ROOT": str(tmp_path),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
    }
    return subprocess.run(
        ["bash", str(SHIM)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=10,
        check=False,
    )


def test_shim_precompact_passes_plain_text(tmp_path: Path) -> None:
    result = _run_shim(
        tmp_path,
        {
            "hook_event_name": "PreCompact",
            "session_id": "s1",
            "cwd": "/work/project",
        },
    )
    assert result.returncode == 0
    stdout = result.stdout.strip()
    # PreCompact context is plain text, not JSON; must survive the shim.
    with pytest.raises(json.JSONDecodeError):
        json.loads(stdout)
    assert "Distill" in stdout
    assert "__memorant_plain_text__" not in stdout


def test_shim_precompact_unicode_context_bypasses_json_byte_gate(
    tmp_path: Path,
) -> None:
    fake_root = tmp_path / "plugin"
    hook_path = fake_root / "mcp-server" / "memorant_mcp" / "hook_cli.py"
    hook_path.parent.mkdir(parents=True)
    hook_path.write_text(
        "import sys\nsys.stdin.read()\nsys.stdout.write('记忆注入提示。' * 3000)\n"
    )
    result = _run_shim(
        tmp_path,
        {"hook_event_name": "PreCompact", "session_id": "s1", "cwd": "/work/project"},
        plugin_root=fake_root,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode()) > 10_000
    assert "记忆注入提示" in result.stdout
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_shim_non_precompact_keeps_json_gate(tmp_path: Path) -> None:
    result = _run_shim(
        tmp_path,
        {
            "hook_event_name": "PreCompactExtra",
            "session_id": "s1",
            "cwd": "/work/project",
        },
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
