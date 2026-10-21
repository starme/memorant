from datetime import date

import pytest
from pydantic import ValidationError

from memorant_mcp.schema import (
    BugFrontmatter,
    DailyFrontmatter,
    SnippetFrontmatter,
)


def test_bug_requires_stack_and_version() -> None:
    with pytest.raises(ValidationError):
        BugFrontmatter(date=date(2026, 7, 23), title="missing fields")


def test_snippet_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SnippetFrontmatter(
            date=date(2026, 7, 23),
            title="retry pattern",
            stack=["Python"],
            where="HTTP clients",
            dont=["non-idempotent calls"],
            version={"python": "3.12"},
            typo=True,
        )


def test_daily_supports_multiple_projects() -> None:
    daily = DailyFrontmatter(
        date=date(2026, 7, 23),
        title="Daily",
        project=["memorant", "service-api"],
    )

    assert daily.project == ["memorant", "service-api"]


def test_scope_language_defaults_empty_and_accepts_many() -> None:
    from memorant_mcp.memory_schema import MemoryScope

    assert MemoryScope().language == []
    assert MemoryScope(language=["python", "go"]).language == ["python", "go"]


def test_legacy_envelope_without_language_still_loads() -> None:
    from memorant_mcp.memory_schema import MemoryScope

    scope = MemoryScope.model_validate({"project": "app", "stack": ["pytest"]})

    assert scope.language == []
