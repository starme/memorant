"""外发策略纯函数测试：默认全禁、禁止优先、default_allowed 恒 false。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memorant_mcp.external_policy import (
    external_allowed,
    load_policy,
    policy_from_dict,
)


def test_empty_policy_allows_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    policy = load_policy()
    assert policy.enabled_sources == []
    assert policy.prohibited_by_type == []
    assert policy.default_allowed is False
    assert policy.require_confirmation is True
    for t in ("markdown", "text", "word", "pdf", "url", "paste"):
        assert external_allowed(t, policy) is False


def test_enabled_source_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    policy = policy_from_dict(
        {
            "enabled_sources": ["url"],
            "prohibited_by_type": [],
            "require_confirmation": True,
        }
    )
    assert external_allowed("url", policy) is True
    assert external_allowed("pdf", policy) is False


def test_prohibited_overrides_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    policy = policy_from_dict(
        {"enabled_sources": ["url", "pdf"], "prohibited_by_type": ["pdf"]}
    )
    assert external_allowed("pdf", policy) is False  # 禁止优先
    assert external_allowed("url", policy) is True


def test_default_allowed_true_is_still_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    # 防御「模糊配置绕过」：配置写 default_allowed=true 也不放行。
    policy = policy_from_dict(
        {
            "enabled_sources": [],
            "prohibited_by_type": [],
            "default_allowed": True,
            "require_confirmation": True,
        }
    )
    assert external_allowed("url", policy) is False


def test_missing_require_confirmation_treated_true() -> None:
    # require_confirmation 恒 true，配置缺失也不能绕过。
    policy = policy_from_dict({"enabled_sources": ["url"]})
    assert policy.require_confirmation is True
    assert external_allowed("url", policy) is True  # enabled + require_confirmation


def test_unknown_source_type_not_allowed() -> None:
    policy = policy_from_dict({"enabled_sources": ["url"]})
    assert external_allowed("weird_type", policy) is False


def test_load_policy_from_yaml_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORANT_ROOT", str(tmp_path))
    (tmp_path / "external_source_policy.yaml").write_text(
        "external_sources:\n"
        "  enabled_sources: [pdf]\n"
        "  prohibited_by_type: [url]\n"
        "  default_allowed: false\n"
        "  require_confirmation: true\n",
        encoding="utf-8",
    )
    policy = load_policy()
    assert external_allowed("pdf", policy) is True
    assert external_allowed("url", policy) is False
    assert external_allowed("markdown", policy) is False
