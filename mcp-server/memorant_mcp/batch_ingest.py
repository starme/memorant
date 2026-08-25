"""批量目录导入（batch ingest）。

复用单文件 `ingest_source` 的留存 / 幂等 / hash 语义，将「目录 → 匹配文件
清单 → 逐项投喂」编排为一个确定性顺序执行的批处理，提供 dry-run 只读预览、
失败隔离、数量 / 字节 / 时间三类资源上限、脱敏汇总报告与幂等重跑。

安全边界（契约 §11）：
- 目录与每个文件均经 `_allowed_source_roots()` realpath 白名单校验。
- 扫描 `followlinks=False`，不跟随目录 symlink；单文件 symlink 逃逸 -> 该文件
  `failed(READ_FORBIDDEN)`。
- 拒绝 `..` 穿越与 symlink 逃逸。
- `source_docs/` 只读：批量只新增，不修改 / 不删除既有条目。
- 不引入并发（线程 / 进程）：顺序执行保证幂等去重无竞态。
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .external_policy import detect_directives, external_allowed, load_policy
from .hook_core import (
    append_event_data,
    bounded_evidence,
    redact_secrets,
    semantic_payload_hash,
)
from .naming import vault_root
from .source_docs import (
    _find_by_content_sha256,
    _resolve_local_dir,
    _resolve_local_file,
    _size_limits,
    ingest_source,
    read_source_doc,
)

# 扩展名 -> source_type 映射（大小写不敏感，契约 §3）。
_SUFFIX_TO_TYPE: dict[str, str] = {
    ".md": "markdown",
    ".txt": "text",
    ".docx": "word",
    ".pdf": "pdf",
}
# types 字段对外展示用的扩展名缩写（契约 §6 固定顺序）。
_TYPE_SUFFIXES: list[str] = ["md", "txt", "docx", "pdf"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rel_path(path: str) -> str:
    """脱敏为相对 MEMORANT_ROOT 的路径，不暴露绝对路径。

    优先用扫描路径（不解析 symlink）做相对化：symlink 逃逸文件仍显示其在
    扫描目录内的相对位置（如 notes/leak.md），而非其指向的白名单外目标。
    仅当路径本身就在 root 外（真实越界）时才 realpath 判定。
    """
    root = os.path.realpath(vault_root())
    abs_path = os.path.abspath(path)
    if abs_path == root or abs_path.startswith(root + os.sep):
        return os.path.relpath(abs_path, root)
    # 白名单外路径（理论上不应出现）：realpath 后仍越界，脱敏不暴露绝对路径。
    real = os.path.realpath(path)
    if real == root or real.startswith(root + os.sep):
        return os.path.relpath(real, root)
    return redact_secrets(os.path.basename(path))


def _validate_types(types: list[str] | None) -> set[str]:
    """校验并归一化 types 过滤参数；非法元素抛 ValueError。

    返回 None 表示全部四类，否则为 source_type 集合。
    """
    if types is None:
        return set()
    if not isinstance(types, list):
        raise ValueError("types must be a list")
    result: set[str] = set()
    for item in types:
        if not isinstance(item, str):
            raise ValueError(f"invalid types element: {item!r}")
        normalized = item.strip().lower()
        # 大小写不敏感：md/MD 等价（契约 §3 合法值 ∈ {md,txt,docx,pdf}）。
        suffix = f".{normalized}"
        if suffix in _SUFFIX_TO_TYPE:
            result.add(_SUFFIX_TO_TYPE[suffix])
        else:
            raise ValueError(
                f"invalid types element: {item!r}; expected one of "
                f"{sorted(_TYPE_SUFFIXES)}"
            )
    return result


def _coerce_limit(value: Any, default: Any, name: str) -> Any:
    """校验上限参数为正数；None 用默认值；<=0 或非数值抛 ValueError。"""
    if value is None:
        return default
    try:
        parsed = float(value) if name == "max_seconds" else int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive number")
    if parsed <= 0:
        raise ValueError(f"{name} must be > 0")
    return parsed


def _scan_directory(directory: str, types: set[str]) -> list[dict[str, Any]]:
    """递归扫描目录，返回按 realpath 排序的文件清单。

    - os.walk(root, followlinks=False)：不跟随目录 symlink。
    - dirnames/filenames 均 sort，保证确定性顺序。
    - 按 path.suffix.lower() 匹配支持类型；不在映射内的扩展名默认忽略。
    - types 为空 = 全部四类。
    """
    real_dir = _resolve_local_dir(directory)
    files: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(real_dir, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            suffix = Path(filename).suffix.lower()
            source_type = _SUFFIX_TO_TYPE.get(suffix)
            if source_type is None:
                continue
            if types and source_type not in types:
                continue
            try:
                byte_size = os.path.getsize(full)
            except OSError:
                byte_size = 0
            files.append(
                {
                    "path": full,
                    "source_type": source_type,
                    "byte_size": byte_size,
                }
            )
    files.sort(key=lambda f: os.path.abspath(f["path"]))
    return files


def _content_sha256_for(kind: str, path: str) -> str:
    """计算 content_sha256，口径与 ingest_source 一致。

    markdown/text 直读 decode；word/pdf 走 extract_text。
    只读，不落盘。
    """
    from .parsers import extract_text

    raw = Path(path).read_bytes()
    if kind in {"markdown", "text"}:
        extracted = raw.decode("utf-8", errors="replace")
    else:
        extracted, _ok = extract_text(kind, path=path)
    return hashlib.sha256(extracted.encode("utf-8")).hexdigest()


def _classify_file(file: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """dry-run 只读判定单文件，返回 (status, extra)。

    status ∈ {"importable", "skipped", "failed"}，extra 为失败原因/重复信息。
    判定顺序与 ingest_source 镜像：
      1) 越界 -> failed(READ_FORBIDDEN)
      2) 不存在 -> failed(NOT_FOUND)
      3) 单文件超限 -> failed(SIZE_EXCEEDED)
      4) content_sha256 命中 -> skipped(duplicate)
      5) 否则 importable
    纯只读：不落盘、不写 journal/activity/memories、不触发网络。
    """
    path = file["path"]
    kind = file["source_type"]
    try:
        real = _resolve_local_file(path)
    except PermissionError:
        return "failed", {"reason": "READ_FORBIDDEN"}
    except FileNotFoundError:
        return "failed", {"reason": "NOT_FOUND"}

    try:
        byte_size = os.path.getsize(real)
    except OSError:
        return "failed", {"reason": "NOT_FOUND"}

    limit = _size_limits()[kind]
    if byte_size > limit:
        return "failed", {"reason": "SIZE_EXCEEDED"}

    try:
        content_sha256 = _content_sha256_for(kind, real)
    except Exception:
        return "failed", {"reason": "UNPARSEABLE"}

    existing = _find_by_content_sha256(content_sha256)
    if existing:
        return "skipped", {
            "reason": "duplicate",
            "doc_id": existing.get("doc_id"),
            "content_sha256": content_sha256,
        }
    return "importable", {"content_sha256": content_sha256}


def _display_types(types: list[str] | None) -> list[str]:
    """规范 types 字段对外展示：用户传入的扩展名缩写（大小写不敏感）。

    None -> 全部四类 [md, txt, docx, pdf]；否则归一化为小写缩写。
    """
    if types is None:
        return list(_TYPE_SUFFIXES)
    return [str(t).strip().lower() for t in types]


def scan_dir(directory: str, types: list[str] | None = None) -> dict[str, Any]:
    """只读扫描目录，返回匹配文件清单（不落盘、不判定重复）。

    对应 MCP 工具 memorant_batch_scan_dir。
    """
    type_set = _validate_types(types)
    files = _scan_directory(directory, type_set)
    return {
        "directory": _rel_path(directory),
        "types": _display_types(types),
        "count": len(files),
        "files": [
            {
                "path": _rel_path(f["path"]),
                "source_type": f["source_type"],
                "byte_size": f["byte_size"],
            }
            for f in files
        ],
    }


def _synthetic_event_id_for(doc_id: str) -> str:
    """与 server._synthetic_event_id 一致：稳定合成事件 id（幂等）。"""
    return hashlib.sha256(f"source-doc:{doc_id}".encode()).hexdigest()[:32]


def _prepare_distill_for_doc(doc_id: str) -> dict[str, Any]:
    """对成功项执行 distill 准备阶段（契约 §10）。

    复用 memorant_distill_source 的准备语义：外发判定 → 注入防护 →
    合成 doc.commit 事件 → 返回脱敏提取文本。绝不调用 promotion /
    governance / write_memory（记忆写入由宿主批量后逐份执行）。
    """
    doc = read_source_doc(doc_id)
    if doc.get("status") == "unparseable":
        return {"doc_id": doc_id, "error": "UNPARSEABLE"}

    source_type = doc.get("source_type", "")
    policy = load_policy()
    allowed = external_allowed(source_type, policy)
    prohibited = source_type in policy.prohibited_by_type

    extracted = doc.get("extracted_text") or ""
    contains_directives = detect_directives(extracted)
    safe_excerpt = redact_secrets(extracted)
    excerpt = bounded_evidence(safe_excerpt)

    synthetic_id = _synthetic_event_id_for(doc_id)
    event_data = {
        "event_type": "doc.commit",
        "session_id": "ingest",
        "project": doc.get("project") or "unknown",
        "source": "source-doc",
        "tool_name": None,
        "outcome": None,
        "evidence_excerpt": excerpt,
        "tags": ["source-doc", doc_id],
    }
    key = doc.get("project_key")
    if isinstance(key, str) and key:
        event_data["project_key"] = key
    event_data["event_id"] = synthetic_id
    event_data["observed_at"] = _now_iso()
    event_data["payload_hash"] = semantic_payload_hash(event_data)
    append_event_data(event_data, root=vault_root())

    return {
        "doc_id": doc_id,
        "synthetic_event_id": synthetic_id,
        "external_would_be_used": False,
        "external_allowed": allowed,
        "prohibited_by_type": prohibited,
        "contains_directives": contains_directives,
        "trust_tier": "provisional",
    }


def batch_ingest_dir(
    directory: str,
    dry_run: bool = False,
    types: list[str] | None = None,
    max_files: int | None = None,
    max_bytes: int | None = None,
    max_seconds: float | None = None,
    distill: bool = False,
) -> dict[str, Any]:
    """批量投喂一个目录下的 md/txt/docx/pdf 文件。

    顺序调度：对清单按序逐项调用 ingest_source，单文件失败不阻断；
    目录级错误（越界 / 不存在）整体终止。返回结构化汇总报告。
    """
    from .config import get_ingest_settings

    # 目录级错误在逐项循环前整体返回，不产生部分结果。
    try:
        real_dir = _resolve_local_dir(directory)
    except PermissionError as e:
        return {"error": "READ_FORBIDDEN", "message": str(e)}
    except FileNotFoundError as e:
        return {"error": "NOT_FOUND", "message": str(e)}

    # 参数校验（契约 §3）：types 非法 / 上限非法 -> VALIDATION_ERROR。
    try:
        type_set = _validate_types(types)
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}

    settings = get_ingest_settings()
    try:
        max_files = _coerce_limit(
            max_files, int(settings.get("batch_max_files", 1000)), "max_files"
        )
        max_bytes = _coerce_limit(
            max_bytes, int(settings.get("batch_max_bytes", 524288000)), "max_bytes"
        )
        max_seconds = _coerce_limit(
            max_seconds, float(settings.get("batch_max_seconds", 300)), "max_seconds"
        )
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}

    started_at = _now_iso()
    start_monotonic = time.monotonic()

    # 扫描（扫描阶段截断 max_files）。
    files = _scan_directory(real_dir, type_set)
    total = len(files)
    files = files[:max_files]
    remaining_unprocessed = max(0, total - max_files)
    if total > max_files:
        stopped_reason = "max_files_reached"
    else:
        stopped_reason = None

    items: list[dict[str, Any]] = []
    distilled: list[dict[str, Any]] = []
    success = 0
    skipped = 0
    failed = 0
    success_bytes = 0

    for file in files:
        # 时间上限检查（每次迭代前）。
        if time.monotonic() - start_monotonic >= max_seconds:
            if stopped_reason is None:
                stopped_reason = "max_seconds_reached"
            break

        rel = _rel_path(file["path"])
        source_type = file["source_type"]
        kind = source_type

        if dry_run:
            status, extra = _classify_file(file)
            item: dict[str, Any] = {
                "path": rel,
                "source_type": source_type,
                "status": status,
            }
            if status == "importable":
                item["content_sha256"] = (extra or {}).get("content_sha256")
                item["reason"] = None
            elif status == "skipped":
                item["doc_id"] = (extra or {}).get("doc_id")
                item["content_sha256"] = (extra or {}).get("content_sha256")
                item["reason"] = (extra or {}).get("reason")
            else:
                item["reason"] = (extra or {}).get("reason")
            items.append(item)
            if status == "importable":
                success += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
            # dry-run 不消耗 max_bytes。
            continue

        # 正式导入：逐项复用 ingest_source，单文件异常不阻断。

        # 字节预算检查（契约 §5：达到上限即停止后续投喂）。
        # 只有实际会成功落盘的项才消耗预算；重复/失败不累计。
        # 本地文件 byte_size 与 ingest_source 的 byte_size(=len(raw)) 一致。
        pending_bytes = int(file.get("byte_size", 0))
        if success_bytes + pending_bytes > max_bytes:
            if stopped_reason is None:
                stopped_reason = "max_bytes_reached"
            break

        try:
            result = ingest_source(kind=kind, path=file["path"])
        except Exception as e:  # 防御性隔离单文件，不阻断后续
            items.append(
                {
                    "path": rel,
                    "source_type": source_type,
                    "status": "failed",
                    "reason": redact_secrets(f"UNPARSEABLE: {e}")[:128],
                }
            )
            failed += 1
            continue

        if result.get("duplicate") is True:
            items.append(
                {
                    "path": rel,
                    "source_type": source_type,
                    "status": "skipped",
                    "doc_id": result.get("doc_id"),
                    "content_sha256": result.get("content_sha256"),
                    "reason": "duplicate",
                }
            )
            skipped += 1
            continue

        if "error" in result:
            items.append(
                {
                    "path": rel,
                    "source_type": source_type,
                    "status": "failed",
                    "reason": result.get("error"),
                }
            )
            failed += 1
            continue

        # 成功落盘：累计字节预算（契约 §5 仅统计成功落盘项）。
        byte_size = result.get("byte_size", pending_bytes)
        success_bytes += int(byte_size)

        doc_id = result.get("doc_id")
        items.append(
            {
                "path": rel,
                "source_type": source_type,
                "status": "success",
                "doc_id": doc_id,
                "content_sha256": result.get("content_sha256"),
                "byte_size": byte_size,
                "reason": None,
            }
        )
        success += 1

        if distill and doc_id:
            try:
                prep = _prepare_distill_for_doc(doc_id)
                if "error" not in prep:
                    distilled.append(prep)
            except Exception as e:  # distill 准备失败不阻断批量，记录后可继续
                distilled.append(
                    {
                        "doc_id": doc_id,
                        "error": "DISTILL_FAILED",
                        "message": redact_secrets(str(e))[:256],
                    }
                )

    finished_at = _now_iso()

    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "directory": _rel_path(real_dir),
        "types": _display_types(types),
        "started_at": started_at,
        "finished_at": finished_at,
        "total": total,
        "success": success,
        "skipped": skipped,
        "failed": failed,
        "stopped_reason": stopped_reason,
        "remaining_unprocessed": remaining_unprocessed,
        "items": items,
        "distilled": distilled,
    }

    if not dry_run:
        _write_report(summary)

    return summary


def _write_report(summary: dict[str, Any]) -> None:
    """正式导入报告落盘 .memorant/batch/<ts>.md（契约 §7）。

    dry-run 不落盘。报告不含明文敏感字段（路径已脱敏为相对路径）。
    """
    root = Path(vault_root())
    batch_dir = root / ".memorant" / "batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = batch_dir / f"{ts}.md"

    lines = [
        "# 批量导入报告",
        "",
        f"- started_at: {summary['started_at']}",
        f"- finished_at: {summary['finished_at']}",
        f"- directory: {summary['directory']}",
        f"- types: {summary['types']}",
        f"- total: {summary['total']}",
        f"- success: {summary['success']}",
        f"- skipped: {summary['skipped']}",
        f"- failed: {summary['failed']}",
        f"- stopped_reason: {summary['stopped_reason']}",
        f"- remaining_unprocessed: {summary['remaining_unprocessed']}",
        "",
        "## 明细",
        "",
    ]
    for item in summary["items"]:
        reason = item.get("reason") or ""
        doc_id = item.get("doc_id") or ""
        lines.append(
            f"- [{item['status']}] {item['source_type']} {item['path']}"
            f"{(' (doc_id=' + doc_id + ')') if doc_id else ''}"
            f"{(' (' + reason + ')') if reason else ''}"
        )
    lines.append("")

    import tempfile

    fd, temp_name = tempfile.mkstemp(
        prefix=".batch-", suffix=".tmp", dir=str(batch_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
