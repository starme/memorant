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
