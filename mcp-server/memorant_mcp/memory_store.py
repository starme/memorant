"""Markdown Memory Envelope store with legacy vault compatibility."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from .config import load_flags
from .hook_core import redact_secrets
from .memory_schema import (
    EvidenceRef,
    LifecycleState,
    MemoryEnvelope,
    MemoryKind,
    MemoryScope,
    MemoryWriteInput,
    TrustTier,
    envelope_to_frontmatter,
)
from .naming import PathForbiddenError, memorant_root, resolve_safe_path


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _memory_rel(memory_id: str) -> str:
    return f"memories/{memory_id}.md"


def _find_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    root = Path(memorant_root())
    memories = root / "memories"
    if not memories.is_dir():
        return None
    for path in memories.glob("*.md"):
        try:
            post = frontmatter.load(path)
            if post.get("source_fingerprint") == fingerprint:
                rel = path.relative_to(root).as_posix()
                return {**dict(post.metadata), "path": rel, "body": post.content}
        except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            continue
    return None


def _inherit_scope_from_events(write: MemoryWriteInput) -> MemoryWriteInput:
    """Fill scope.project_key / project from source journal events when missing."""
    if write.scope.project_key and write.scope.project:
        return write
    root = Path(memorant_root())
    key = write.scope.project_key
    label = write.scope.project
    for event_id in write.source_event_ids:
        for path in (root / "journal").glob(f"**/*-{event_id}.md"):
            try:
                meta = dict(frontmatter.load(path).metadata)
            except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            if not key and isinstance(meta.get("project_key"), str):
                key = meta["project_key"]
            if not label and isinstance(meta.get("project"), str):
                label = meta["project"]
            break
        if key and label:
            break
    if key == write.scope.project_key and label == write.scope.project:
        return write
    scope = write.scope.model_copy(
        update={
            "project_key": key or write.scope.project_key,
            "project": label or write.scope.project,
        }
    )
    return write.model_copy(update={"scope": scope})


def write_memory(write: MemoryWriteInput) -> dict[str, Any]:
    flags = load_flags()
    if write.trust_tier == TrustTier.verified and not flags.auto_write_verified:
        raise ValueError("auto_write_verified is disabled")
    if write.trust_tier == TrustTier.provisional and not flags.auto_write_provisional:
        raise ValueError("auto_write_provisional is disabled")

    write = _inherit_scope_from_events(write)

    existing = _find_by_fingerprint(write.fingerprint())
    if existing:
        return {**existing, "deduped": True}

    envelope = write.to_envelope()
    if envelope.trust_tier == TrustTier.verified and not envelope.evidence:
        raise ValueError("verified memory requires evidence")
    envelope.evidence = [
        item.model_copy(update={"excerpt": redact_secrets(item.excerpt)})
        for item in envelope.evidence
    ]
    envelope.claim = redact_secrets(envelope.claim)
    envelope.title = redact_secrets(envelope.title)[:200] or envelope.title

    body_parts = [f"# {envelope.title}", "", envelope.claim]
    if write.body:
        body_parts.extend(["", redact_secrets(write.body)])
    if envelope.evidence:
        body_parts.extend(["", "## Evidence"])
        for item in envelope.evidence:
            body_parts.append(f"- {item.source}: {item.excerpt}")
    body = "\n".join(body_parts) + "\n"

    rel = _memory_rel(envelope.memory_id)
    safe = Path(resolve_safe_path(rel))
    if safe.exists():
        raise ValueError(f"memory already exists: {rel}")
    post = frontmatter.Post(body, **envelope_to_frontmatter(envelope))
    _atomic_write(safe, frontmatter.dumps(post))
    return {**envelope.model_dump(mode="json"), "path": rel, "deduped": False}


def read_memory(memory_id_or_path: str) -> dict[str, Any]:
    root = Path(memorant_root())
    candidate = memory_id_or_path
    if "/" not in memory_id_or_path and not memory_id_or_path.endswith(".md"):
        candidate = _memory_rel(memory_id_or_path)
    safe = Path(resolve_safe_path(candidate))
    if not safe.is_file():
        raise FileNotFoundError(candidate)
    post = frontmatter.load(safe)
    data = dict(post.metadata)
    data["path"] = safe.relative_to(root).as_posix()
    data["body"] = post.content
    return data


def update_memory_fields(
    memory_id_or_path: str, updates: dict[str, Any]
) -> dict[str, Any]:
    current = read_memory(memory_id_or_path)
    path = current.pop("path")
    body = current.pop("body", "")
    forbidden = {"memory_id", "source_fingerprint", "created_at", "type"}
    for key in forbidden:
        updates.pop(key, None)
    current.update(updates)
    if "updated_at" not in updates:
        current["updated_at"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    # Re-validate when possible.
    try:
        envelope = MemoryEnvelope(**{k: v for k, v in current.items() if k != "type"})
        fm = envelope_to_frontmatter(envelope)
    except Exception:
        fm = {**current, "type": "memory"}
    safe = Path(resolve_safe_path(path))
    _atomic_write(safe, frontmatter.dumps(frontmatter.Post(body, **fm)))
    return {**fm, "path": path, "body": body}


def list_memories(
    *,
    project: str | None = None,
    trust_tier: str | None = None,
    lifecycle: str | None = None,
    limit: int = 50,
    include_legacy: bool = True,
) -> list[dict[str, Any]]:
    root = Path(memorant_root())
    results: list[dict[str, Any]] = []
    memories = root / "memories"
    if memories.is_dir():
        for path in sorted(
            memories.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                post = frontmatter.load(path)
                item = dict(post.metadata)
                item["path"] = path.relative_to(root).as_posix()
                item["body"] = post.content
                item["legacy"] = False
            except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            if project and (item.get("scope") or {}).get("project") != project:
                # also allow top-level project for flexibility
                if item.get("project") != project:
                    continue
            if trust_tier and item.get("trust_tier") != trust_tier:
                continue
            if lifecycle and item.get("lifecycle") != lifecycle:
                continue
            results.append(item)
            if len(results) >= limit:
                return results
    if include_legacy and len(results) < limit:
        results.extend(_legacy_memories(project=project, limit=limit - len(results)))
    return results[:limit]


def _legacy_kind(entry_type: str) -> MemoryKind:
    if entry_type == "arch":
        return MemoryKind.decision
    if entry_type == "snippet":
        return MemoryKind.procedural
    return MemoryKind.episodic


def _migrated_legacy_paths(root: Path) -> set[str]:
    """收集 memories/ 下已生成真实 Memory Envelope 的 legacy_path 集合。

    迁移是「投影生成」（原 legacy 目录保留原位），迁移后在 memories/ 写入了
    带 legacy_path 的 envelope。legacy 投影必须排除这些来源，否则同一来源
    会以两条不同 memory_id 的记录重复召回。保持未迁移的旧数据仍可读。
    """
    migrated: set[str] = set()
    memories = root / "memories"
    if not memories.is_dir():
        return migrated
    for path in memories.glob("*.md"):
        try:
            post = frontmatter.load(path)
        except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            continue
        legacy_path = post.get("legacy_path")
        if isinstance(legacy_path, str) and legacy_path:
            migrated.add(legacy_path)
    return migrated


def _legacy_memories(*, project: str | None, limit: int) -> list[dict[str, Any]]:
    root = Path(memorant_root())
    migrated = _migrated_legacy_paths(root)
    out: list[dict[str, Any]] = []
    for folder, entry_type in (
        ("bugs", "bug"),
        ("snippets", "snippet"),
        ("arch", "arch"),
    ):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(
            base.glob("**/*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if path.relative_to(root).as_posix() in migrated:
                continue
            try:
                post = frontmatter.load(path)
            except (OSError, TypeError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                continue
            fm_project = post.get("project")
            if project:
                if isinstance(fm_project, list):
                    if project not in fm_project:
                        continue
                elif fm_project not in {None, project}:
                    continue
            title = str(post.get("title") or path.stem)
            claim = (post.content or "").strip().splitlines()
            claim_text = next((line for line in claim if line.strip()), title)[:4000]
            stack = post.get("stack") or []
            if not isinstance(stack, list):
                stack = [str(stack)]
            created = (
                post.get("date")
                or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                .date()
                .isoformat()
            )
            item = {
                "memory_id": hashlib_legacy_id(path.relative_to(root).as_posix()),
                "title": title,
                "claim": claim_text,
                "kind": _legacy_kind(entry_type).value,
                "trust_tier": TrustTier.verified.value,
                "lifecycle": LifecycleState.active.value,
                "confidence": 0.8,
                "scope": MemoryScope(
                    project=fm_project if isinstance(fm_project, str) else project,
                    stack=[str(s) for s in stack][:16],
                ).model_dump(mode="json"),
                "evidence": [
                    EvidenceRef(
                        source="legacy-vault",
                        excerpt=claim_text[:500],
                    ).model_dump(mode="json")
                ],
                "source_event_ids": [],
                "independent_success_keys": [],
                "related": post.get("related") or [],
                "created_at": str(created),
                "updated_at": str(created),
                "recall_count": 0,
                "source_fingerprint": hashlib_legacy_id(
                    path.relative_to(root).as_posix() + ":fp"
                ),
                "legacy": True,
                "legacy_path": path.relative_to(root).as_posix(),
                "path": path.relative_to(root).as_posix(),
                "body": post.content,
            }
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def hashlib_legacy_id(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def mark_recalled(memory_id_or_path: str) -> None:
    try:
        current = read_memory(memory_id_or_path)
    except (FileNotFoundError, PathForbiddenError, ValueError):
        return
    if current.get("legacy"):
        return
    count = int(current.get("recall_count") or 0) + 1
    update_memory_fields(
        current["path"],
        {
            "recall_count": count,
            "last_recalled_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )
