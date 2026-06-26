"""ORM model for durable ControlProject and normalized graph storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.tag_registry import JSONType
from app.db.session import Base


class StoredProject(Base):
    __tablename__ = "stored_projects"

    file_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    project_name: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    connector: Mapped[str | None] = mapped_column(Text)
    project_blob: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    normalized_blob: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
