"""Source document 留存与只读读取。

只读保证（PRD P0-2 红线）：本模块是 source_docs/ 的唯一写入点，写入后
不提供任何修改/删除路径。写入经 resolve_safe_path + _resolve_inside，
拒绝 .. 穿越与 symlink 逃逸。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import frontmatter

from .hook_core import redact_secrets
from .naming import resolve_safe_path, vault_root
from .source_schema import SourceDoc

_SOURCE_TYPES = {"markdown", "text", "word", "pdf", "url", "paste"}


def _strip_url_credentials(url: str) -> str:
    """剥离 URL 中的 query 凭证（不记录 query 里的 token/key）。"""
    parts = urlsplit(url)
    if parts.query:
        url = url.split("?", 1)[0]
    return url


def _canonical_source(kind: str, *, path: str | None, url: str | None, content: str | None) -> str:
    """规范化来源，用于 doc_id 派生（契约 §3.4）。"""
    if kind == "paste":
        text = content or ""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"paste:{digest}"
    if kind == "url":
        u = _strip_url_credentials(url or "")
        # 去除 fragment 与 trailing slash
        parts = urlsplit(u)
        normalized = parts._replace(fragment="").geturl()
        if normalized.endswith("/") and not parts.path.endswith("/"):
            normalized = normalized.rstrip("/")
        return f"url:{normalized}"
    # markdown / text / word / pdf：本地文件
    real = os.path.realpath(os.path.expanduser(path or ""))
    return f"file:{real}"


def doc_id_for(kind: str, *, path: str | None = None, url: str | None = None, content: str | None = None) -> str:
    canonical = _canonical_source(kind, path=path, url=url, content=content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _doc_rel(doc_id: str, suffix: str) -> str:
    return f"source_docs/{doc_id}.{suffix}"


def _allowed_source_roots() -> list[str]:
    """本地文件允许目录：默认仅 MEMORANT_ROOT，可经 ingest.source_allow_dirs 扩展。"""
    from .config import get_ingest_settings

    ingest = get_ingest_settings()
    allow = ingest.get("source_allow_dirs") or []
    roots: list[str] = [vault_root()]
    for d in allow:
        if isinstance(d, str) and d.strip():
            roots.append(os.path.realpath(os.path.expanduser(d.strip())))
    return roots


def _resolve_local_file(path: str) -> str:
    """校验本地文件 realpath 后处于允许目录内，否则 READ_FORBIDDEN。"""
    real = os.path.realpath(os.path.expanduser(path))
    if not os.path.isfile(real):
        raise FileNotFoundError(path)
    allowed = _allowed_source_roots()
    for root in allowed:
        if real == root or real.startswith(root + os.sep):
            return real
    raise PermissionError(f"path outside allowed source dirs: {path}")


def _read_raw_source(kind: str, *, path: str | None, url: str | None, content: str | None) -> tuple[bytes, str]:
    """读取原始字节与规范化来源；URL 拒绝 file://。"""
    if kind in {"markdown", "text", "word", "pdf"}:
        if path:
            real = _resolve_local_file(path)
            raw = Path(real).read_bytes()
            return raw, real
        if content is not None:
            return content.encode("utf-8"), "paste"
        raise ValueError(f"{kind} requires path or content")
    if kind == "paste":
        if content is None:
            raise ValueError("paste requires content")
        return content.encode("utf-8"), "paste"
    if kind == "url":
        if not url:
            raise ValueError("url requires url")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise PermissionError(f"url scheme forbidden: {parts.scheme}")
        from .parsers import extract_text

        text, ok = extract_text("url", url=url)
        if not ok:
            raise RuntimeError(f"fetch failed: {text}")
        return text.encode("utf-8"), _strip_url_credentials(url)
    raise ValueError(f"unsupported kind: {kind}")


def _size_limits() -> dict[str, int]:
    from .config import get_ingest_settings

    ingest = get_ingest_settings()
    return {
        "markdown": int(ingest.get("max_bytes_file", 5 * 1024 * 1024)),
        "text": int(ingest.get("max_bytes_file", 5 * 1024 * 1024)),
        "word": int(ingest.get("max_bytes_binary", 10 * 1024 * 1024)),
        "pdf": int(ingest.get("max_bytes_binary", 10 * 1024 * 1024)),
        "paste": int(ingest.get("max_bytes_paste", 1 * 1024 * 1024)),
        "url": int(ingest.get("max_bytes_url", 5 * 1024 * 1024)),
    }


def _extract_text_for_kind(kind: str, *, path: str | None, url: str | None, content: str | None, raw: bytes) -> tuple[str, bool, bool]:
    """提取文本；返回 (text, parsed_ok, extractable)。binary 类型委托 parsers。"""
    from .parsers import extract_text

    if kind in {"markdown", "text", "paste"}:
        return raw.decode("utf-8", errors="replace"), True, True
    # word / pdf / url 走 parsers
    text, ok = extract_text(kind, path=path, url=url, content=content)
    return text, True, ok  # parsed_ok 恒 True（不阻断留存），extractable 反映可解析性


class SizeExceededError(Exception):
    def __init__(self, limit: int, actual: int):
        self.limit = limit
        self.actual = actual
        super().__init__(f"size exceeded: {actual} > {limit}")


def ingest_source(kind: str, *, path: str | None = None, url: str | None = None, content: str | None = None) -> dict[str, Any]:
    """留存一份资料。返回 dict，携带 doc_id/content_sha256/duplicate/status。"""
    if kind not in _SOURCE_TYPES:
        return {"error": "UNSUPPORTED_TYPE", "message": f"unsupported kind: {kind}"}

    try:
        raw, origin_source = _read_raw_source(kind, path=path, url=url, content=content)
    except PermissionError as e:
        return {"error": "READ_FORBIDDEN", "message": str(e)}
    except FileNotFoundError as e:
        return {"error": "NOT_FOUND", "message": str(e)}
    except RuntimeError as e:
        return {"error": "FETCH_FAILED", "message": str(e)}
    except ValueError as e:
        return {"error": "VALIDATION_ERROR", "message": str(e)}

    byte_size = len(raw)
    limit = _size_limits()[kind]
    if byte_size > limit:
        return {
            "error": "SIZE_EXCEEDED",
            "message": "size exceeded",
            "limit": limit,
            "actual": byte_size,
        }

    doc_id = doc_id_for(kind, path=path, url=url, content=content)

    extracted, _parsed_ok, extractable = _extract_text_for_kind(
        kind, path=path, url=url, content=content, raw=raw
    )
    content_sha256 = hashlib.sha256(extracted.encode("utf-8")).hexdigest()

    # 幂等：content_sha256 去重（不区分 source_type）。
    existing = _find_by_content_sha256(content_sha256)
    if existing:
        return {
            **existing,
            "duplicate": True,
            "content_sha256": content_sha256,
            "origin": existing.get("origin", _safe_origin(origin_source, kind)),
        }

    status = "stored" if extractable else "unparseable"
    origin = _safe_origin(origin_source, kind)

    title = _derive_title(kind, origin_source, extracted)
    now = datetime.now(timezone.utc)
    doc = SourceDoc(
        doc_id=doc_id,
        source_type=kind,
        origin=origin,
        title=title[:200],
        content_sha256=content_sha256,
        byte_size=byte_size,
        ingested_at=now,
        status=status,
    )

    # 写元数据 md
    meta_rel = _doc_rel(doc_id, "md")
    meta_safe = Path(resolve_safe_path(meta_rel))
    _atomic_write(
        meta_safe,
        frontmatter.dumps(frontmatter.Post("", **doc.model_dump(mode="json"))),
    )
    # 写提取文本 txt（不可解析时缺失）
    if extractable:
        txt_rel = _doc_rel(doc_id, "txt")
        txt_safe = Path(resolve_safe_path(txt_rel))
        _atomic_write(txt_safe, extracted)

    # 写原始字节 raw（word/pdf 二进制）
    if kind in {"word", "pdf"}:
        raw_rel = _doc_rel(doc_id, "raw")
        raw_safe = Path(resolve_safe_path(raw_rel))
        _atomic_write_bytes(raw_safe, raw)

    return {
        "doc_id": doc_id,
        "duplicate": False,
        "source_type": kind,
        "origin": origin,
        "status": status,
        "byte_size": byte_size,
        "content_sha256": content_sha256,
        "extractable": extractable,
    }


def _find_by_content_sha256(content_sha256: str) -> dict[str, Any] | None:
    root = Path(vault_root())
    src_dir = root / "source_docs"
    if not src_dir.is_dir():
        return None
    for path in sorted(src_dir.glob("*.md")):
        try:
            post = frontmatter.load(path)
        except (OSError, TypeError, UnicodeDecodeError, ValueError):
            continue
        if post.get("content_sha256") == content_sha256:
            data = dict(post.metadata)
            data["doc_id"] = data.get("doc_id") or path.stem
            return data
    return None


def _safe_origin(origin_source: str, kind: str) -> str:
    """溯源脱敏：本地→相对路径；url→剥离凭证；paste→'paste'。"""
    if kind == "paste":
        return "paste"
    if kind == "url":
        return _strip_url_credentials(origin_source)
    # 本地文件：优先相对路径
    root = os.path.realpath(vault_root())
    if origin_source == root or origin_source.startswith(root + os.sep):
        return os.path.relpath(origin_source, root)
    return redact_secrets(origin_source)


def _derive_title(kind: str, origin_source: str, extracted: str) -> str:
    if kind == "paste":
        first = next((l.strip() for l in extracted.splitlines() if l.strip()), "")
        return first[:200] or "paste"
    if kind == "url":
        return _strip_url_credentials(origin_source)[:200]
    # 本地文件
    base = os.path.basename(origin_source)
    return base[:200]


def read_source_doc(doc_id: str) -> dict[str, Any]:
    rel = _doc_rel(doc_id, "md")
    safe = Path(resolve_safe_path(rel))
    if not safe.is_file():
        raise FileNotFoundError(doc_id)
    post = frontmatter.load(safe)
    data = dict(post.metadata)
    data["doc_id"] = data.get("doc_id") or doc_id
    txt_path = Path(resolve_safe_path(_doc_rel(doc_id, "txt")))
    if txt_path.is_file():
        data["extracted_text"] = txt_path.read_text(encoding="utf-8")
    return data


def list_sources(
    *,
    status: str | None = None,
    source_type: str | None = None,
    project: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    root = Path(vault_root())
    src_dir = root / "source_docs"
    results: list[dict[str, Any]] = []
    if src_dir.is_dir():
        for path in sorted(src_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                post = frontmatter.load(path)
            except (OSError, TypeError, UnicodeDecodeError, ValueError):
                continue
            data = dict(post.metadata)
            data["doc_id"] = data.get("doc_id") or path.stem
            if status and data.get("status") != status:
                continue
            if source_type and data.get("source_type") != source_type:
                continue
            if project and data.get("project") != project:
                continue
            results.append(data)
            if len(results) >= limit:
                break
    return {"sources": results, "count": len(results)}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

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


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
