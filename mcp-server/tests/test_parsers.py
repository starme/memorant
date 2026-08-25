"""类型解析器测试：md/text/paste/word/pdf/url 及优雅降级。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from memorant_mcp.parsers import extract_text


def test_markdown_extracts_verbatim(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("# Hi\n\nBody text\n", encoding="utf-8")
    text, ok = extract_text("markdown", path=str(p))
    assert ok is True
    assert "Body text" in text


def test_text_extracts(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("plain text", encoding="utf-8")
    text, ok = extract_text("text", path=str(p))
    assert ok is True
    assert text == "plain text"


def test_paste_uses_content() -> None:
    text, ok = extract_text("paste", content="pasted body")
    assert ok is True
    assert text == "pasted body"


def _docx(tmp_path: Path, paragraphs: list[str]) -> Path:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + "".join(
            f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>'
            for p in paragraphs
        )
        + "</w:body></w:document>"
    )
    p = tmp_path / "doc.docx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
    return p


def test_word_docx_extracts_paragraphs(tmp_path: Path) -> None:
    p = _docx(tmp_path, ["第一段", "第二段"])
    text, ok = extract_text("word", path=str(p))
    assert ok is True
    assert "第一段" in text
    assert "第二段" in text


def test_word_doc_returns_unsupported(tmp_path: Path) -> None:
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")  # 旧 OLE 魔数，非 zip
    text, ok = extract_text("word", path=str(p))
    assert ok is False
    assert "不支持" in text or "docx" in text


def test_pdf_without_pypdf_returns_unparseable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 确保 pypdf 不可用（运行时探测为 None）。
    import importlib.util

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, package=None: None
    )
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    text, ok = extract_text("pdf", path=str(p))
    assert ok is False
    assert "pypdf" in text


def test_pdf_with_pypdf_extracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: "pypdf" if name == "pypdf" else None,
    )
    # 用假 pypdf 模块验证探测路径：monkeypatch parsers 内的提取函数。
    from memorant_mcp import parsers

    called: list[str] = []

    def fake_pdf_extract(path: str) -> str:
        called.append(path)
        return "extracted from pdf"

    monkeypatch.setattr(parsers, "_extract_pdf_text", fake_pdf_extract)
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4")
    text, ok = extract_text("pdf", path=str(p))
    assert ok is True
    assert text == "extracted from pdf"
    assert called == [str(p)]


def test_url_refuses_file_scheme() -> None:
    _text, ok = extract_text("url", url="file:///etc/passwd")
    assert ok is False


def test_url_http_returns_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from memorant_mcp import parsers

    def fake_fetch(url: str, timeout: float, max_bytes: int) -> bytes:
        return b"<html><body><p>Hello World</p></body></html>"

    monkeypatch.setattr(parsers, "_fetch_url_bytes", fake_fetch)
    text, ok = extract_text("url", url="https://example.com/page")
    assert ok is True
    assert "Hello World" in text
    assert "<html>" not in text
