"""Shared helpers for vendor exports that are preserved, not deeply parsed."""

from __future__ import annotations

from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
)
from app.services.version_service import sha256_bytes

# Cap embedded raw text to keep memory predictable; full byte length is recorded.
MAX_RAW_LOGIC_CHARS = 256 * 1024


def decode_content_as_text(content: bytes) -> str:
    """Lossy UTF-8 decode; never raises."""

    return content.decode("utf-8", errors="replace")


def project_stem(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        return base.rsplit(".", 1)[0]
    return base or "imported_project"


def build_preservation_shell_project(
    *,
    filename: str,
    content: bytes,
    controller_platform: str,
    connector_platform: str,
    display_name: str,
    parser_version: str,
    routine_name: str = "Export",
) -> ControlProject:
    """Minimal ``ControlProject``: one controller/program and one preserved routine.

    No ladder/ST semantics are inferred. ``raw_logic`` holds a bounded text slice
    when the payload decodes as text; binary blobs omit ``raw_logic`` but record
    length in metadata.
    """

    pname = project_stem(filename)
    text = decode_content_as_text(content)
    truncated = len(text) > MAX_RAW_LOGIC_CHARS
    raw_slice = text[:MAX_RAW_LOGIC_CHARS] if text else ""

    routine_meta: dict[str, object] = {
        "connector_platform": connector_platform,
        "connector_display_name": display_name,
        "parser_version": parser_version,
        "preservation_mode": "shell_only",
        "source_byte_length": len(content),
        "text_decode_used": True,
        "raw_logic_truncated": truncated,
    }

    raw_logic: str | None
    if truncated or raw_slice:
        raw_logic = raw_slice
    else:
        raw_logic = None
        routine_meta["binary_or_empty_text"] = True

    routine = ControlRoutine(
        name=routine_name,
        language="unknown",
        instructions=[],
        raw_logic=raw_logic,
        parse_status="preserved_only",
        metadata=routine_meta,
    )

    return ControlProject(
        project_name=pname,
        source_file=filename,
        file_hash=sha256_bytes(content),
        controllers=[
            ControlController(
                name=pname,
                platform=controller_platform,
                controller_tags=[],
                programs=[
                    ControlProgram(
                        name="Imported",
                        tags=[],
                        routines=[routine],
                    )
                ],
            )
        ],
        metadata={
            "connector": display_name,
            "connector_platform": connector_platform,
            "parser_version": parser_version,
            "ingest_mode": "preservation_shell",
            "original_byte_length": len(content),
        },
    )


__all__ = [
    "MAX_RAW_LOGIC_CHARS",
    "build_preservation_shell_project",
    "decode_content_as_text",
    "project_stem",
]
