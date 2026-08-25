"""Layer boundary test: core modules must not import host_adapters.

验证架构「import 方向铁律」（PRD 验收标准 16 / 架构 §2.1）：通用核心不 import
宿主适配层，宿主适配层可 import 核心。
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "memorant_mcp"

# 通用核心模块清单（host-agnostic，不得 import host_adapters）。严格对齐架构
# §2.1 核心层清单：server.py / search.py / schema.py 属接入/遗留层，不在此列。
CORE_MODULES = {
    "journal.py",
    "memory_store.py",
    "recall.py",
    "promotion.py",
    "activity.py",
    "migration.py",
    "event_schema.py",
    "memory_schema.py",
    "hook_core.py",
    "naming.py",
    "config.py",
    "trust_field.py",
    "project_identity.py",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_core_modules_do_not_import_host_adapters() -> None:
    offenders: list[tuple[str, str]] = []
    for name in CORE_MODULES:
        path = PACKAGE / name
        if not path.is_file():
            continue
        for imported in _imports(path):
            if "host_adapters" in imported:
                offenders.append((name, imported))
    assert not offenders, (
        f"core modules must not import host_adapters; offenders: {offenders}"
    )


def test_host_adapters_may_import_core() -> None:
    # 方向单边约束：适配器可以 import 核心，这里仅确认适配器包存在且可导入。
    from memorant_mcp.host_adapters import (
        CLAUDE_CODE_CAPABILITIES,
        CODEX_CLI_CAPABILITIES,
        CODEX_CLOUD_CAPABILITIES,
    )

    assert CLAUDE_CODE_CAPABILITIES.event_capture is True
    assert CODEX_CLI_CAPABILITIES.event_capture is False
    assert CODEX_CLOUD_CAPABILITIES.auto_distill is False
