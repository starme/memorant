"""SourceDoc Pydantic 模型（资料留存元数据，extra=forbid）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["markdown", "text", "word", "pdf", "url", "paste"]
SourceStatus = Literal["stored", "unparseable"]


class SourceDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: str = Field(default="source_doc", frozen=True)
    doc_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_type: SourceType
    origin: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=200)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    ingested_at: datetime
    project: str | None = Field(default=None, max_length=256)
    project_key: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{16,64}$", max_length=64
    )
    status: SourceStatus = "stored"  # type: ignore[assignment]
