"""Legacy notes → Memory Envelope migration engine.

迁移是「投影生成」：历史 `bugs/`/`snippets/`/`daily/`/`arch/` 先整体备份到
`.memorant/migration-backup/<timestamp>/`（保留原位、不删除），再在 `memories/`
下生成可被新闭环召回的 Memory Envelope。同一来源重复迁移命中幂等键（memory_id
由 legacy_path 稳定派生），不重复写入、不覆盖冲突（红线）。

合成 `source_event_ids`：旧 notes 无事件 id，而 `MemoryEnvelope.source_event_ids`
强制 min_length=1 且 `[0-9a-f]{32}`。迁移为每条来源合成一个稳定 event_id 的
`doc.commit` journal 事件（session_id/source 均为 "migration"），复用
`append_event_data` 的 payload_hash 去重，保证重跑不产生重复合成事件。
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .hook_core import append_event_data, redact_secrets, semantic_payload_hash
from .memory_schema import (
    EvidenceRef,
    LifecycleState,
    MemoryEnvelope,
    MemoryKind,
    TrustTier,
    envelope_to_frontmatter,
)
from .memory_store import _atomic_write

LEGACY_DIRS = ("bugs", "snippets", "daily", "arch")

# 冻结契约 §1.4：idempotent-key 派生。memory_id 32 hex，source_fingerprint 64 hex。
_MEMORY_ID_PREFIX = "migration:"
_FINGERPRINT_PREFIX = "migration:"
_EVENT_ID_PREFIX = "migration-event:"

# daily 中仅在「## 踩坑/线索」下、以这三类前缀开头的行才算可识别经验线索。
_DAILY_CUE_PREFIXES = {
    "【待升bugs】": MemoryKind.episodic,
    "【待升snippets】": MemoryKind.procedural,
    "【待补ADR】": MemoryKind.decision,
}

_DAILY_CUE_SECTION = "## 踩坑/线索"

# 各来源迁移后的 evidence.source 与生命周期映射。
_SOURCE_ATTRS = {
    "bugs": {"source": "legacy-bug", "kind": None},
    "snippets": {"source": "legacy-snippet", "kind": MemoryKind.procedural},
    "arch": {"source": "legacy-arch", "kind": MemoryKind.decision},
    "daily": {"source": "legacy-daily", "kind": None},
}


def _timestamp() -> str:
    # 微秒精度保证连续两次迁移产生不同备份目录（chronological 可排序）。
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rel_of(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def migration_event_id(legacy_path: str) -> str:
    """合成事件 id：由 legacy_path 稳定派生（32 hex），重跑命中同一事件。"""
    return hashlib.sha256(f"{_EVENT_ID_PREFIX}{legacy_path}".encode()).hexdigest()[:32]


def migration_memory_id(legacy_path: str) -> str:
    """记忆 memory_id：由 legacy_path 稳定派生（32 hex），保证迁移幂等。"""
    return hashlib.sha256(f"{_MEMORY_ID_PREFIX}{legacy_path}:0".encode()).hexdigest()[:32]


def migration_source_fingerprint(legacy_path: str, content_sha256: str) -> str:
    """source_fingerprint：由 legacy_path + 脱敏后内容 sha256 派生（64 hex）。"""
    return hashlib.sha256(
        f"{_FINGERPRINT_PREFIX}{legacy_path}:{content_sha256}".encode()
    ).hexdigest()


def _content_sha256(raw: str) -> str:
    return hashlib.sha256(redact_secrets(raw).encode()).hexdigest()


def detect_legacy_data(root: str | Path) -> dict[str, int]:
    """扫描 root 下四类历史目录，返回各目录的 .md 条目数（非空判定）。"""
    base = Path(root)
    counts: dict[str, int] = {}
    for directory in LEGACY_DIRS:
        path = base / directory
        if not path.is_dir():
            counts[directory] = 0
            continue
        counts[directory] = sum(
            1 for p in path.rglob("*.md") if p.is_file()
        )
    return counts


def _has_legacy_data(counts: dict[str, int]) -> bool:
    return any(counts.get(d) for d in LEGACY_DIRS)


def _resolve_inside(root: Path, rel: str) -> Path:
    """将相对路径解析到 root 内，拒绝穿越（复用 resolve_safe_path 的白名单语义）。

    root 与 candidate 均走 resolve，规避 macOS /var→/private/var 等符号链接
    前缀差异导致的误判。
    """
    import os

    root_resolved = root.resolve()
    resolved = (root / rel).resolve()
    try:
        inside = os.path.commonpath((str(root_resolved), str(resolved))) == str(root_resolved)
    except ValueError:
        inside = False
    if not inside or resolved == root_resolved:
        raise ValueError(f"path escapes MEMORANT_ROOT: {rel!r}")
    return resolved


def _backup_legacy(root: Path, timestamp: str) -> str | None:
    """将存在的历史目录整体 copytree 到备份目录，返回备份相对路径或 None。"""
    backup_rel = f".memorant/migration-backup/{timestamp}"
    backup_dir = _resolve_inside(root, backup_rel)
    copied = False
    for directory in LEGACY_DIRS:
        src = root / directory
        if not src.is_dir() or not any(src.rglob("*.md")):
            continue
        dst = backup_dir / directory
        shutil.copytree(src, dst, dirs_exist_ok=False)
        copied = True
    if not copied:
        return None
    return backup_rel


def _load_legacy(root: Path, rel: str) -> tuple[dict[str, Any], str]:
    """读取一条 legacy 文件，返回 (metadata, body)。解析失败抛异常。"""
    path = _resolve_inside(root, rel)
    post = frontmatter.load(path)
    return dict(post.metadata), post.content or ""


def _legacy_title(metadata: dict[str, Any], fallback: str) -> str:
    title = str(metadata.get("title") or "").strip()
    return title or fallback


def _first_claim(body: str, title: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return title


def _bug_kind(body: str) -> MemoryKind:
    """bugs 默认 episodic；存在明确「避坑」章节才升级 semantic。"""
    if "避坑" in body:
        return MemoryKind.semantic
    return MemoryKind.episodic


def _arch_lifecycle(metadata: dict[str, Any]) -> LifecycleState:
    status = str(metadata.get("status") or "").lower()
    if status in {"deprecated", "superseded"}:
        return LifecycleState.superseded
    return LifecycleState.active


def _extract_daily_cues(body: str) -> list[str]:
    """提取 daily 正文中「## 踩坑/线索」下的可识别经验线索行（纯文本）。

    直接对全文（含 frontmatter）扫描章节标记，而非依赖 frontmatter 库的
    content 切分——兼容 frontmatter.dumps 对空正文生成「--- 后无换行」的
    历史文件格式，保证章节提取健壮。
    """
    lines = _normalize_frontmatter_separator(body).splitlines()
    in_section = False
    cues: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped == _DAILY_CUE_SECTION:
                in_section = True
            continue
        if stripped.startswith("## "):
            break
        if any(stripped.startswith(prefix) for prefix in _DAILY_CUE_PREFIXES):
            cues.append(stripped)
    return cues


def _normalize_frontmatter_separator(text: str) -> str:
    """容错：frontmatter.dumps 对空正文可能生成「--- 后无换行」，补回换行。

    历史 daily 文件可能存在「---## 踩坑/线索」粘连，归一化后章节标记成为
    独立行，使章节提取可靠。
    """
    return text.replace("---##", "---\n##")


def _daily_kind(cue: str) -> MemoryKind:
    for prefix, kind in _DAILY_CUE_PREFIXES.items():
        if cue.startswith(prefix):
            return kind
    return MemoryKind.episodic


def _daily_title(cue: str) -> str:
    for prefix in _DAILY_CUE_PREFIXES:
        if cue.startswith(prefix):
            return cue[len(prefix):].strip()
    return cue.strip()


def _append_synthetic_event(root: Path, legacy_path: str, excerpt: str) -> dict[str, Any]:
    """写入一条合成 journal 事件，返回带 event_id 的存储结果。"""
    event_id = migration_event_id(legacy_path)
    event: dict[str, Any] = {
        "event_type": "doc.commit",
        "event_id": event_id,
        "observed_at": _now_iso(),
        "session_id": "migration",
        "project": "migration",
        "source": "migration",
        "tool_name": None,
        "outcome": None,
        "evidence_excerpt": redact_secrets(excerpt) or None,
        "tags": ["migration"],
    }
    event["payload_hash"] = semantic_payload_hash(event)
    return append_event_data(event, root=str(root))


def _build_evidence(source: str, claim: str) -> list[EvidenceRef]:
    return [EvidenceRef(source=source, excerpt=redact_secrets(claim)[:4096])]


def _envelope(
    *,
    legacy_path: str,
    title: str,
    claim: str,
    kind: MemoryKind,
    trust_tier: TrustTier,
    confidence: float,
    lifecycle: LifecycleState,
    evidence: list[EvidenceRef],
    source_event_ids: list[str],
    source_fingerprint: str,
) -> MemoryEnvelope:
    now = datetime.now(timezone.utc)
    safe_title = redact_secrets(title)[:200] or "untitled"
    return MemoryEnvelope(
        memory_id=migration_memory_id(legacy_path),
        title=safe_title,
        claim=redact_secrets(claim)[:4000],
        kind=kind,
        trust_tier=trust_tier,
        lifecycle=lifecycle,
        confidence=confidence,
        evidence=evidence,
        source_event_ids=source_event_ids,
        created_at=now,
        updated_at=now,
        source_fingerprint=source_fingerprint,
        legacy_path=legacy_path,
    )


def _existing_memory(root: Path, memory_id: str) -> dict[str, Any] | None:
    path = _resolve_inside(root, f"memories/{memory_id}.md")
    if not path.is_file():
        return None
    try:
        post = frontmatter.load(path)
        return {**dict(post.metadata), "body": post.content}
    except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return None


def _write_envelope(root: Path, envelope: MemoryEnvelope) -> None:
    rel = f"memories/{envelope.memory_id}.md"
    safe = _resolve_inside(root, rel)
    post = frontmatter.Post("", **envelope_to_frontmatter(envelope))
    _atomic_write(safe, frontmatter.dumps(post))


def _map_sources(root: Path) -> list[dict[str, Any]]:
    """扫描并规范化所有 legacy 来源，产出「待迁移单元」列表。"""
    units: list[dict[str, Any]] = []
    counts = detect_legacy_data(root)
    for directory in LEGACY_DIRS:
        if not counts[directory]:
            continue
        base_dir = root / directory
        for path in sorted(base_dir.rglob("*.md")):
            if not path.is_file():
                continue
            rel = _rel_of(root, path)
            if directory == "daily":
                raw = _read_raw(root, rel)
                cues = _extract_daily_cues(raw)
                if not cues:
                    units.append(
                        {"legacy_path": rel, "source": "daily", "cues": []}
                    )
                else:
                    for index, cue in enumerate(cues, start=1):
                        units.append(
                            {
                                "legacy_path": f"{rel}#{index}",
                                "source": "daily",
                                "cue": cue,
                            }
                        )
            else:
                units.append({"legacy_path": rel, "source": directory})
    return units


def _read_raw(root: Path, rel: str) -> str:
    try:
        return _resolve_inside(root, rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _unit_content(root: Path, unit: dict[str, Any]) -> str:
    """返回该单元用于 fingerprint 的原始内容（脱敏前）。"""
    rel = unit["legacy_path"].split("#")[0]
    return _read_raw(root, rel)


def _migrate_one(root: Path, unit: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """迁移单个单元，返回 item 结果。异常捕获由调用方处理为 failed。"""
    legacy_path = unit["legacy_path"]
    source = unit["source"]

    if source == "daily":
        cue = unit.get("cue")
        if cue is None:
            return {"legacy_path": legacy_path, "memory_id": None, "status": "skipped_no_form", "reason": "no migratable form"}
        kind = _daily_kind(cue)
        title = _daily_title(cue)
        claim = title
        trust_tier = TrustTier.provisional
        confidence = 0.5
        lifecycle = LifecycleState.active
        ev_source = _SOURCE_ATTRS["daily"]["source"]
    else:
        metadata, body = _load_legacy(root, legacy_path.split("#")[0])
        if metadata.get("type") not in {"bug", "snippet", "arch"}:
            raise ValueError("invalid legacy frontmatter (missing type)")
        title = _legacy_title(metadata, Path(legacy_path).stem)
        claim = _first_claim(body, title)
        trust_tier = TrustTier.verified
        confidence = 0.8
        ev_source = _SOURCE_ATTRS[source]["source"]
        if source == "bugs":
            kind = _bug_kind(body)
            lifecycle = LifecycleState.active
        elif source == "snippets":
            kind = _SOURCE_ATTRS["snippets"]["kind"] or MemoryKind.procedural
            lifecycle = LifecycleState.active
        else:  # arch
            kind = _SOURCE_ATTRS["arch"]["kind"] or MemoryKind.decision
            lifecycle = _arch_lifecycle(metadata)

    raw = _unit_content(root, unit)
    fingerprint = migration_source_fingerprint(legacy_path, _content_sha256(raw))
    memory_id = migration_memory_id(legacy_path)

    existing = _existing_memory(root, memory_id)
    if existing is not None:
        if existing.get("source_fingerprint") == fingerprint:
            return {"legacy_path": legacy_path, "memory_id": memory_id, "status": "skipped_no_form", "reason": "already migrated"}
        return {"legacy_path": legacy_path, "memory_id": memory_id, "status": "skipped_conflict", "reason": "target exists with different fingerprint"}

    if dry_run:
        return {"legacy_path": legacy_path, "memory_id": memory_id, "status": "will_migrate", "kind": kind.value, "trust_tier": trust_tier.value}

    event = _append_synthetic_event(root, legacy_path, claim)
    event_id = event["event_id"]
    evidence = _build_evidence(ev_source, claim)
    envelope = _envelope(
        legacy_path=legacy_path,
        title=title,
        claim=claim,
        kind=kind,
        trust_tier=trust_tier,
        confidence=confidence,
        lifecycle=lifecycle,
        evidence=evidence,
        source_event_ids=[event_id],
        source_fingerprint=fingerprint,
    )
    _write_envelope(root, envelope)
    return {"legacy_path": legacy_path, "memory_id": memory_id, "status": "migrated"}


def _write_report(root: Path, timestamp: str, summary: dict[str, Any]) -> str:
    rel = f".memorant/migration-report-{timestamp}.md"
    safe = _resolve_inside(root, rel)
    safe.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Memorant 迁移报告",
        "",
        f"- 开始时间: {summary['started_at']}",
        f"- 结束时间: {summary['ended_at']}",
        f"- 备份路径: {summary.get('backup_path') or '-'}",
        (
            f"- 统计: total={summary['total']} migrated={summary['migrated']} "
            f"skipped_no_form={summary['skipped_no_form']} "
            f"skipped_conflict={summary['skipped_conflict']} failed={summary['failed']}"
        ),
        "",
        "## 明细",
        "",
        "| legacy_path | memory_id | status | reason |",
        "|---|---|---|---|",
    ]
    for item in summary["items"]:
        legacy_path = redact_secrets(str(item.get("legacy_path") or "")) or "-"
        memory_id = item.get("memory_id") or "-"
        status = str(item.get("status") or "")
        reason = redact_secrets(str(item.get("reason") or "")) or "-"
        lines.append(f"| {legacy_path} | {memory_id} | {status} | {reason} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 原始文件已保留原位，并备份至上述备份路径（永久保留，可回滚）。",
        "- 所有 excerpt 已脱敏。",
    ]
    safe.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rel


def migrate_legacy(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """执行 legacy 数据迁移。

    dry_run=True 只预览，不写 memories、不写 journal、不备份、不落报告。
    """
    base = Path(root)
    counts = detect_legacy_data(base)
    units = _map_sources(base)

    if dry_run:
        preview: list[dict[str, Any]] = []
        will_migrate = 0
        will_skip = 0
        will_conflict = 0
        for unit in units:
            item = _migrate_one(base, unit, dry_run=True)
            if item["status"] == "will_migrate":
                will_migrate += 1
                preview.append(item)
            elif item["status"] == "skipped_conflict":
                will_conflict += 1
            else:
                will_skip += 1
        return {
            "dry_run": True,
            "total": len(units),
            "will_migrate": will_migrate,
            "will_skip_no_form": will_skip,
            "will_conflict": will_conflict,
            "preview": preview,
        }

    if not _has_legacy_data(counts):
        return {
            "completed": True,
            "total": 0,
            "migrated": 0,
            "skipped_no_form": 0,
            "skipped_conflict": 0,
            "failed": 0,
            "memory_ids": [],
            "items": [],
        }

    timestamp = _timestamp()
    started_at = _now_iso()
    backup_path = _backup_legacy(base, timestamp)

    items: list[dict[str, Any]] = []
    memory_ids: list[str] = []
    skipped_no_form = 0
    skipped_conflict = 0
    failed = 0
    migrated = 0

    for unit in units:
        try:
            item = _migrate_one(base, unit, dry_run=False)
        except Exception as exc:  # noqa: BLE001 — 单条失败隔离（PRD §6.1）：捕获任意异常并继续下一条
            item = {
                "legacy_path": unit["legacy_path"],
                "memory_id": None,
                "status": "failed",
                "reason": str(exc),
            }
        status = item["status"]
        if status == "migrated":
            migrated += 1
            memory_ids.append(item["memory_id"])
        elif status == "skipped_conflict":
            skipped_conflict += 1
        elif status == "skipped_no_form":
            skipped_no_form += 1
        else:
            failed += 1
        items.append(item)

    summary = {
        "started_at": started_at,
        "ended_at": _now_iso(),
        "backup_path": backup_path,
        "total": len(units),
        "migrated": migrated,
        "skipped_no_form": skipped_no_form,
        "skipped_conflict": skipped_conflict,
        "failed": failed,
        "items": items,
    }
    report_path = _write_report(base, timestamp, summary)

    return {
        "completed": True,
        "backup_path": backup_path,
        "report_path": report_path,
        "total": summary["total"],
        "migrated": migrated,
        "skipped_no_form": skipped_no_form,
        "skipped_conflict": skipped_conflict,
        "failed": failed,
        "memory_ids": memory_ids,
        "items": items,
    }


def _migration_state_path(root: Path) -> Path:
    return _resolve_inside(root, ".memorant/migration-state.json")


def first_start_legacy_hint(root: str | Path) -> str | None:
    """首次启动迁移检测（P0-3）：有历史数据时返回「是否迁移」提示，仅提示不迁移。

    用户若拒绝（不迁移），将记录 migration-state.json 的 reminded 标记，避免每次
    SessionStart 重复打扰。无历史数据时返回 None。
    """
    import json

    base = Path(root)
    counts = detect_legacy_data(base)
    if not _has_legacy_data(counts):
        return None

    state_path = _migration_state_path(base)
    reminded = False
    try:
        reminded = json.loads(state_path.read_text(encoding="utf-8")).get("reminded", False)
    except (OSError, ValueError, json.JSONDecodeError):
        reminded = False
    if reminded:
        return None

    total = sum(counts.values())
    return (
        f"检测到 {total} 条历史数据（bugs/snippets/daily/arch），是否迁移为 Memorant 记忆？"
        "请运行 /memorant-migrate 或调用 memorant_migrate(confirm=true)。"
    )


def mark_migration_reminded(root: str | Path) -> None:
    """记录「已提醒未迁移」状态，避免后续 SessionStart 重复打扰（P0-3 用户拒绝路径）。"""
    import json

    base = Path(root)
    state_path = _migration_state_path(base)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"reminded": True}, ensure_ascii=False), encoding="utf-8")
