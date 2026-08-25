"""资料类型解析器（stdlib 优先，PDF 可选依赖优雅降级）。

extract_text(kind, *, path=None, url=None, content=None) -> (text, parsed_ok)
- markdown/text/paste：stdlib 直读
- word(.docx)：stdlib zipfile + xml.etree 提取 word/document.xml 文本；.doc 不支持
- pdf：importlib.util.find_spec("pypdf") 运行时探测，可用则提取，不可用降级
- url：stdlib urllib（timeout + size + scheme 白名单），去标签取纯文本
提取结果不在此模块脱敏（脱敏在 source_docs/evidence 层统一做）。
"""

from __future__ import annotations

import html
import importlib.util
import re
import urllib.request
import zipfile
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .config import get_ingest_settings

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _pypdf_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


def _read_local(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _extract_docx_text(path: str) -> str:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as zf, zf.open("word/document.xml") as f:
        tree = ElementTree.parse(f)
    for node in tree.iter(f"{_WORD_NS}t"):
        if node.text:
            paragraphs.append(node.text)
    return "\n".join(paragraphs)


def _extract_pdf_text(path: str) -> str:
    """用 pypdf 提取文本；调用前已确认 pypdf 可用。"""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _fetch_url_bytes(url: str, timeout: float, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "memorant-ingest/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"url content exceeds {max_bytes} bytes")
    return data


def _strip_tags(raw: str) -> str:
    # 去掉 script/style 整块，再剥标签，转义 HTML 实体。
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def extract_text(
    kind: str,
    *,
    path: str | None = None,
    url: str | None = None,
    content: str | None = None,
) -> tuple[str, bool]:
    """提取纯文本。返回 (extracted_text, parsed_ok)。"""
    settings = get_ingest_settings()

    if kind in {"markdown", "text"}:
        if path:
            try:
                return _read_local(path), True
            except OSError as e:
                return f"read failed: {e}", False
        if content is not None:
            return content, True
        return "requires path or content", False

    if kind == "paste":
        if content is None:
            return "paste requires content", False
        return content, True

    if kind == "word":
        if not path:
            return "word requires path", False
        # 旧二进制 .doc 不支持（契约 §3.2）。
        if path.endswith(".doc"):
            return "不支持旧版 .doc 格式，请转存 .docx 或粘贴文本", False
        try:
            if not zipfile.is_zipfile(path):
                return "无法解析 Word（加密/损坏或旧 .doc），请转存 .docx", False
            return _extract_docx_text(path), True
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as e:
            return f"无法解析 Word：{e}", False

    if kind == "pdf":
        if not path:
            return "pdf requires path", False
        if not _pypdf_available():
            return (
                "PDF 解析需要 pypdf，安装后可解析（uv sync --group pdf）",
                False,
            )
        try:
            return _extract_pdf_text(path), True
        except Exception as e:
            return f"无法解析 PDF：{e}", False

    if kind == "url":
        if not url:
            return "url requires url", False
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            return f"URL scheme 拒绝：{parts.scheme or '(空)'}（仅 http/https）", False
        if len(url) > 2048:
            return "URL 超长（>2048）", False
        timeout = float(settings.get("url_timeout_seconds", 10.0))
        max_bytes = int(settings.get("max_bytes_url", 5 * 1024 * 1024))
        try:
            raw = _fetch_url_bytes(url, timeout, max_bytes)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            return f"抓取失败：{e}", False
        raw_text = raw.decode("utf-8", errors="replace")
        text = _strip_tags(raw_text)
        if not text:
            return "抓取结果为空（可能为 JS 渲染页面或需登录）", False
        return text, True

    return f"unsupported kind: {kind}", False
