"""Migration engine tests: mapping, idempotency, conflict, isolation, redaction,
path traversal, backup retention, daily extraction."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter
import pytest

from memorant_mcp import migration
from memorant_mcp.naming import PathForbiddenError


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bug(title: str, body: str, status: str = "resolved") -> str:
    post = frontmatter.Post(
        body,
        type="bug",
        date=date(2026, 7, 23),
        title=title,
        stack=["Python"],
        version={"python": "3.12"},
        status=status,
    )
    return frontmatter.dumps(post)


def _snippet(title: str, body: str) -> str:
    post = frontmatter.Post(
        body,
        type="snippet",
        date=date(2026, 7, 23),
        title=title,
        stack=["Python"],
        where="限流场景",
        dont=["不要同步等待"],
        version={"python": "3.12"},
    )
    return frontmatter.dumps(post)


def _arch(title: str, body: str, status: str = "active") -> str:
    post = frontmatter.Post(
        body,
        type="arch",
        date=date(2026, 7, 23),
        title=title,
        status=status,
        decision_by=["tal"],
    )
    return frontmatter.dumps(post)


def _daily(path: Path, content: str) -> None:
    post = frontmatter.Post(
        "",
        type="daily",
        date=date(2026, 7, 24),
        title="daily",
        project=["memorant"],
    )
    text = frontmatter.dumps(post)
    _write(path, text + content)


def _make_legacy(root: Path) -> Path:
    """Create a minimal legacy dataset under root."""
    _write(
        root / "bugs" / "pytest-flaky.md",
        _bug("pytest 偶发超时", "## 根因\n\n网络抖动导致。", "resolved"),
    )
    _write(root / "snippets" / "rate-limit.md", _snippet("限流", "## 做法\n\n令牌桶。"))
    _write(
        root / "arch" / "adr-001-cache.md",
        _arch("选本地优先", "## Decision\n\n本地文件。", "active"),
    )
    _daily(
        root / "daily" / "2026-07-24.md",
        "## 踩坑/线索\n【待升bugs】docker 端口映射失效\n【待升snippets】限流算法\n\n正文流水",
    )
    return root


@pytest.fixture
def legacy_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    return _make_legacy(root)


def _memories(root: Path) -> list[dict]:
    out: list[dict] = []
    for p in sorted((root / "memories").glob("*.md")):
        post = frontmatter.load(p)
        out.append({"path": p.name, **dict(post.metadata), "body": post.content})
    return out


def test_detect_legacy_data_counts_nonempty_dirs(legacy_root: Path) -> None:
    result = migration.detect_legacy_data(str(legacy_root))
    assert result == {"bugs": 1, "snippets": 1, "daily": 1, "arch": 1}


def test_detect_legacy_data_empty_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "empty"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    result = migration.detect_legacy_data(str(root))
    assert result == {"bugs": 0, "snippets": 0, "daily": 0, "arch": 0}


def test_migrate_maps_bug_snippet_arch_to_envelopes(legacy_root: Path) -> None:
    result = migration.migrate_legacy(str(legacy_root))
    assert result["completed"] is True
    assert result["failed"] == 0
    assert result["migrated"] == 5
    memories = _memories(legacy_root)
    assert len(memories) == 5

    by_kind = {m["kind"]: m for m in memories}
    assert "episodic" in by_kind or "semantic" in by_kind  # bug
    assert "procedural" in by_kind  # snippet
    assert "decision" in by_kind  # arch

    for m in memories:
        assert m.get("legacy_path")
        assert m.get("source_event_ids")
        for ev in m.get("evidence") or []:
            assert ev.get("source")


def test_arch_deprecated_maps_to_superseded_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    _write(
        root / "arch" / "adr-001-old.md",
        _arch("旧决策", "## Decision\n\n旧的。", "deprecated"),
    )
    result = migration.migrate_legacy(str(root))
    assert result["migrated"] == 1
    mem = _memories(root)[0]
    assert mem["kind"] == "decision"
    assert mem["lifecycle"] == "superseded"


def test_migrate_is_idempotent(legacy_root: Path) -> None:
    first = migration.migrate_legacy(str(legacy_root))
    first_ids = set(first["memory_ids"])
    second = migration.migrate_legacy(str(legacy_root))
    assert second["migrated"] == 0
    assert second["skipped_idempotent"] == first["total"]
    assert all(item["status"] == "skipped_idempotent" for item in second["items"])
    assert set(second["memory_ids"]) <= first_ids
    assert len(_memories(legacy_root)) == len(first_ids)


def test_conflict_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    legacy_path = "bugs/pytest-flaky.md"
    _write(root / legacy_path, _bug("pytest 偶发超时", "## 根因\n\n网络抖动。"))

    memory_id = migration.migration_memory_id(legacy_path)
    _write(
        root / "memories" / f"{memory_id}.md", "---\ntype: memory\n---\nconflicting\n"
    )

    result = migration.migrate_legacy(str(root))
    assert result["migrated"] == 0
    assert result["skipped_conflict"] == 1
    assert (root / "memories" / f"{memory_id}.md").read_text().endswith("conflicting\n")


def test_failure_isolation_continues_other_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    _write(root / "snippets" / "good.md", _snippet("好的", "## 做法\n\n内容。"))
    _write(root / "bugs" / "broken.md", "\x00invalid\xffbinary")

    result = migration.migrate_legacy(str(root))
    assert result["migrated"] == 1
    assert result["failed"] == 1
    statuses = {it["legacy_path"]: it["status"] for it in result["items"]}
    assert statuses["snippets/good.md"] == "migrated"
    assert statuses["bugs/broken.md"] == "failed"


def test_failure_report_redacts_root_path_and_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "private-root"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    _write(root / "bugs" / "broken.md", "\x00invalid\xff token=secret-value")

    def fail_with_sensitive_error(
        migration_root: Path, unit: dict[str, object], dry_run: bool
    ) -> dict[str, object]:
        raise RuntimeError(f"failed at {migration_root}/private token=secret-value")

    monkeypatch.setattr(migration, "_migrate_one", fail_with_sensitive_error)
    result = migration.migrate_legacy(str(root))
    report = (root / result["report_path"]).read_text(encoding="utf-8")
    assert str(root) not in report
    assert "secret-value" not in report
    assert "<MEMORANT_ROOT>" in report


def test_redacts_secrets_in_memory_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    secret = "sk-1234567890abcdef"
    _write(root / "bugs" / "leak.md", _bug("泄漏", f"## 根因\n\ntoken={secret}"))

    result = migration.migrate_legacy(str(root))
    assert result["migrated"] == 1
    memory_text = next((root / "memories").glob("*.md")).read_text()
    assert secret not in memory_text
    assert "[REDACTED]" in memory_text
    report = (root / result["report_path"]).read_text()
    assert secret not in report


def test_backup_retained_and_original_preserved(legacy_root: Path) -> None:
    result = migration.migrate_legacy(str(legacy_root))
    backup = legacy_root / result["backup_path"]
    assert backup.is_dir()
    assert (legacy_root / "bugs" / "pytest-flaky.md").is_file()
    assert (backup / "bugs" / "pytest-flaky.md").is_file()


def test_backup_uses_unique_timestamp_and_keeps_old(legacy_root: Path) -> None:
    first = migration.migrate_legacy(str(legacy_root))
    second = migration.migrate_legacy(str(legacy_root))
    assert first["backup_path"] != second["backup_path"]
    backups = list((legacy_root / ".memorant" / "migration-backup").iterdir())
    assert len(backups) == 2


def test_daily_extracts_cue_lines_as_provisional(legacy_root: Path) -> None:
    result = migration.migrate_legacy(str(legacy_root))
    daily_items = [
        it for it in result["items"] if it["legacy_path"].startswith("daily/")
    ]
    assert len(daily_items) == 2
    assert all(it["status"] == "migrated" for it in daily_items)
    daily_mems = [
        m
        for m in _memories(legacy_root)
        if (m.get("legacy_path") or "").startswith("daily/")
    ]
    assert daily_mems
    assert all(m["trust_tier"] == "provisional" for m in daily_mems)


def test_daily_pure_noise_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    _daily(
        root / "daily" / "2026-07-24.md",
        "## 随手记\n\n今天天气不错\n\n没有任何可迁移经验",
    )
    result = migration.migrate_legacy(str(root))
    assert result["migrated"] == 0
    assert result["skipped_no_form"] == 1


def test_dry_run_writes_nothing(legacy_root: Path) -> None:
    result = migration.migrate_legacy(str(legacy_root), dry_run=True)
    assert result.get("dry_run") is True
    assert not (legacy_root / "memories").exists()
    assert not (legacy_root / ".memorant" / "migration-backup").exists()


def test_path_traversal_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    from memorant_mcp.naming import resolve_safe_path

    with pytest.raises(PathForbiddenError):
        resolve_safe_path("../outside.md")


def test_migration_memory_id_is_32_hex_and_stable() -> None:
    mid = migration.migration_memory_id("bugs/x.md")
    assert len(mid) == 32
    assert all(c in "0123456789abcdef" for c in mid)
    assert migration.migration_memory_id("bugs/x.md") == mid


def test_migration_fingerprint_is_64_hex_and_stable() -> None:
    fp = migration.migration_source_fingerprint("bugs/x.md", "abc123")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)
    assert migration.migration_source_fingerprint("bugs/x.md", "abc123") == fp


def test_migration_event_id_is_32_hex() -> None:
    eid = migration.migration_event_id("bugs/x.md")
    assert len(eid) == 32
    assert all(c in "0123456789abcdef" for c in eid)


def test_first_start_hint_detects_legacy_data(legacy_root: Path) -> None:
    hint = migration.first_start_legacy_hint(str(legacy_root))
    assert hint is not None
    assert "迁移" in hint


def test_first_start_hint_none_when_no_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "empty"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    assert migration.first_start_legacy_hint(str(root)) is None


def test_mark_migration_reminded_suppresses_hint(legacy_root: Path) -> None:
    migration.mark_migration_reminded(str(legacy_root))
    assert migration.first_start_legacy_hint(str(legacy_root)) is None


def test_migrate_command_confirm_red_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from memorant_mcp.server import memorant_migrate

    root = tmp_path / "memorant"
    monkeypatch.setenv("MEMORANT_ROOT", str(root))
    _make_legacy(root)
    # confirm=False 且有历史数据 → 仅提示，不落盘。
    result = asyncio.run(memorant_migrate())
    assert result["action_required"] == "confirm"
    assert result["total"] > 0
    assert not (root / "memories").exists()


def test_list_memories_does_not_duplicate_migrated_sources(legacy_root: Path) -> None:
    """迁移后 list_memories(include_legacy=True) 不得重复投影已迁移来源。

    迁移是「投影生成」——原始 bugs/snippets/arch 目录保留原位，但对应的
    Memory Envelope 已在 memories/ 下生成。legacy 投影（_legacy_memories）
    必须排除这些已生成 envelope 的 legacy_path，否则同一来源返回两条
    content 同源、memory_id 不同的记录。
    """
    from memorant_mcp.memory_store import list_memories

    migration.migrate_legacy(str(legacy_root))

    items = list_memories(include_legacy=True)
    # 5 条迁移 envelope（bug1 + snippet1 + arch1 + daily2）。
    # 修复前会再叠加 bugs/snippets/arch 的 legacy 投影（+3），共 8 条。
    assert len(items) == 5

    # 不残留任何 legacy 影子投影。
    assert [i for i in items if i.get("legacy")] == []

    # 每条 legacy_path 唯一，无同源重复。
    legacy_paths = [i.get("legacy_path") for i in items if i.get("legacy_path")]
    assert len(legacy_paths) == len(set(legacy_paths))


def test_recall_does_not_duplicate_migrated_sources(legacy_root: Path) -> None:
    """迁移后 recall_memories 不得对同一来源返回迁移 envelope + legacy 投影两条。"""
    from memorant_mcp.recall import recall_memories

    migration.migrate_legacy(str(legacy_root))

    payload = recall_memories("pytest 限流 本地", include_provisional=True, mark=False)
    results = payload["results"]
    # 每个 memory_id 唯一；不应出现同源（相同 legacy_path）两条。
    ids = [r["memory_id"] for r in results]
    assert len(ids) == len(set(ids))
    # 迁移后的召回只来自真实 envelope（legacy=False），无 legacy 影子。
    assert all(r.get("legacy") is False for r in results)
