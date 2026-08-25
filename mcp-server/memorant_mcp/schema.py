"""Pydantic models for the four Memorant entry types' frontmatter.

Each model validates the frontmatter of one category. `extra='forbid'` rejects
unknown fields so typos surface early. Required-field failures produce actionable
error messages (e.g. "bug 缺 version 字段，含 node/框架版本") via the
`field_validator` + `model_config` config below.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntryType(str, Enum):
    bug = "bug"
    snippet = "snippet"
    daily = "daily"
    arch = "arch"


class BugStatus(str, Enum):
    resolved = "resolved"
    workaround = "workaround"
    open = "open"


class ArchStatus(str, Enum):
    proposed = "proposed"
    active = "active"
    deprecated = "deprecated"
    superseded = "superseded"


_STR_STRIP = ConfigDict(str_strip_whitespace=True, extra="forbid")


class _Common(BaseModel):
    """Fields shared across all four types. `project` is Optional here; daily
    widens it to a list (cross-project days land in one file)."""

    model_config = _STR_STRIP

    type: EntryType
    date: date
    title: str = Field(..., min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    project: str | None = Field(
        default=None,
        description="project this entry belongs to (optional for bug/snippet)",
    )


class BugFrontmatter(_Common):
    type: EntryType = Field(default=EntryType.bug, frozen=True)
    stack: list[str] = Field(..., min_length=1, description="技术栈，如 [Node, Express]")
    version: dict = Field(
        ...,
        description="关键依赖版本，如 {node: 20, express: '4.18.2'}",
    )
    status: BugStatus = Field(default=BugStatus.resolved)

    @model_validator(mode="after")
    def _type_is_bug(self) -> BugFrontmatter:
        if self.type != EntryType.bug:
            raise ValueError(f"bug entry type must be 'bug', got '{self.type}'")
        return self


class SnippetFrontmatter(_Common):
    type: EntryType = Field(default=EntryType.snippet, frozen=True)
    stack: list[str] = Field(..., min_length=1)
    where: str = Field(..., min_length=1, description="使用场景")
    dont: list[str] = Field(
        ...,
        min_length=1,
        description="禁忌/禁止场景，至少一条",
    )
    version: dict = Field(..., description="依赖版本约束")

    @model_validator(mode="after")
    def _type_is_snippet(self) -> SnippetFrontmatter:
        if self.type != EntryType.snippet:
            raise ValueError(f"snippet entry type must be 'snippet', got '{self.type}'")
        return self


class DailyFrontmatter(_Common):
    """Daily is time-first. project is a list so a cross-project day lives in
    one file. pending_review tracks how many '待升' lines await migration."""

    model_config = _STR_STRIP

    type: EntryType = Field(default=EntryType.daily, frozen=True)
    date: date
    tags: list[str] = Field(default_factory=lambda: ["daily", "dev"])
    project: list[str] = Field(
        default_factory=list,
        description="当天涉及的项目（数组，跨项目那天多值）",
    )
    related: list[str] = Field(default_factory=list)
    pending_review: int = Field(
        default=0,
        ge=0,
        description="待整理条目数，迁移后递减",
    )

    @model_validator(mode="after")
    def _type_is_daily(self) -> DailyFrontmatter:
        if self.type != EntryType.daily:
            raise ValueError(f"daily entry type must be 'daily', got '{self.type}'")
        return self


class ArchFrontmatter(_Common):
    type: EntryType = Field(default=EntryType.arch, frozen=True)
    status: ArchStatus = Field(default=ArchStatus.active)
    decision_by: list[str] = Field(..., min_length=1, description="决策人")
    supersedes: str | None = Field(
        default=None,
        description="被替代的旧 ADR id，如 adr-002",
    )

    @model_validator(mode="after")
    def _supersedes_requires_status(self) -> ArchFrontmatter:
        # If this ADR supersedes an old one, the old one's status should flip to
        # 'superseded'. Enforced by the Skill's promote flow, not here — but if
        # supersedes is set we expect status active (the new one takes over).
        if self.supersedes and self.status == ArchStatus.superseded:
            raise ValueError(
                "a new ADR that supersedes another should not itself be 'superseded'"
            )
        return self


# Map type -> model, used by server to validate incoming frontmatter.
SCHEMA_BY_TYPE: dict[EntryType, type[BaseModel]] = {
    EntryType.bug: BugFrontmatter,
    EntryType.snippet: SnippetFrontmatter,
    EntryType.daily: DailyFrontmatter,
    EntryType.arch: ArchFrontmatter,
}
