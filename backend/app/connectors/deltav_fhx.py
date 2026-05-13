from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from app.connectors.base import ConnectorMatch, PlatformConnector
from app.connectors.preserved_import import MAX_RAW_LOGIC_CHARS
from app.models.control_model import (
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
)
from app.services.version_service import sha256_bytes


_MODULE_RE = re.compile(
    r"(?im)^\s*(CONTROL_MODULE|EQUIPMENT_MODULE|MODULE)\s+\"?([^\"\r\n{]+)\"?"
)
_AREA_RE = re.compile(r"(?im)^\s*AREA\s+\"?([^\"\r\n{]+)\"?")
_BLOCK_RE = re.compile(
    r"(?im)^\s*(FUNCTION_BLOCK|BLOCK|FB)\s+\"?([^\"\r\n{]+)\"?(?:\s+TYPE\s+\"?([^\"\r\n{]+)\"?)?"
)
_PARAM_RE = re.compile(
    r"(?im)^\s*(PARAMETER|PARAM|IN|OUT|INPUT|OUTPUT)\s+\"?([^\"\r\n:=]+)\"?\s*(?::=|=|:)?\s*([^;\r\n]*)"
)
_LINK_RE = re.compile(r"(?im)^\s*(LINK|REFERENCE|REF)\s+(.+)$")


@dataclass(frozen=True)
class _Section:
    section_type: str
    name: str
    start: int
    end: int
    raw_text: str


def decode_fhx_text(content: bytes) -> tuple[str, str]:
    """Decode FHX text without assuming a single export encoding."""

    if content.startswith(b"\xff\xfe"):
        return content.decode("utf-16-le", errors="replace"), "utf-16-le"
    if content.startswith(b"\xfe\xff"):
        return content.decode("utf-16-be", errors="replace"), "utf-16-be"
    sample = content[:4096]
    if sample.count(b"\x00") > max(8, len(sample) // 8):
        return content.decode("utf-16-le", errors="replace"), "utf-16-le"
    return content.decode("utf-8", errors="replace"), "utf-8"


def _find_system_name(text: str, fallback: str) -> str:
    for pattern in (
        r"(?im)^\s*SYSTEM\s+\"?([^\"\r\n{]+)\"?",
        r"(?im)^\s*PROJECT\s+\"?([^\"\r\n{]+)\"?",
        r"(?im)^\s*DATABASE\s+\"?([^\"\r\n{]+)\"?",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return fallback


def _line_end(text: str, pos: int) -> int:
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _find_sections(text: str) -> list[_Section]:
    matches = list(_MODULE_RE.finditer(text))
    if not matches:
        return []
    sections: list[_Section] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        name = match.group(2).strip().strip('"')
        sections.append(
            _Section(
                section_type=match.group(1).upper(),
                name=name or f"Module_{idx + 1}",
                start=start,
                end=end,
                raw_text=text[start:end].strip(),
            )
        )
    return sections


def _extract_parameters(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for match in _PARAM_RE.finditer(raw):
        value = match.group(3).strip().strip('"')
        params[match.group(2).strip().strip('"')] = value
    return params


def _extract_links(raw: str) -> list[str]:
    return [m.group(2).strip() for m in _LINK_RE.finditer(raw) if m.group(2).strip()]


def _extract_blocks(raw: str) -> list[ControlInstruction]:
    blocks: list[ControlInstruction] = []
    for idx, match in enumerate(_BLOCK_RE.finditer(raw), start=1):
        block_name = match.group(2).strip().strip('"')
        block_type = (match.group(3) or match.group(1)).strip().strip('"')
        block_start = match.start()
        block_end = _line_end(raw, block_start)
        block_raw = raw[block_start:block_end].strip()
        blocks.append(
            ControlInstruction(
                instruction_type=block_type or "FUNCTION_BLOCK",
                operands=[],
                raw_text=block_raw,
                language="fbd",
                id=f"fb_{idx}",
                metadata={
                    "object_subtype": "function_block",
                    "block_type": block_type or "FUNCTION_BLOCK",
                    "block_name": block_name,
                    "parameters": _extract_parameters(raw[block_start:]),
                    "references": _extract_links(raw[block_start:]),
                    "parse_status": "parsed",
                },
            )
        )
    return blocks


def _fallback_unknown_routine(text: str) -> ControlRoutine:
    return ControlRoutine(
        name="Preserved Export",
        language="unknown",
        instructions=[],
        raw_logic=text[:MAX_RAW_LOGIC_CHARS],
        parse_status="preserved_only",
        metadata={
            "platform": "deltav",
            "parse_status": "preserved_only",
            "raw_source_present": bool(text),
            "preserved_only": True,
        },
    )


class DeltaVFHXConnector(PlatformConnector):
    platform = "deltav"
    display_name = "Emerson DeltaV FHX"
    supported_extensions = (".fhx",)
    parser_version = "0.2.0"

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        low = filename.lower()
        text, _encoding = decode_fhx_text(content[:65536])
        marker_text = text.lower()
        marker_hits = sum(
            marker in marker_text
            for marker in (
                "deltav",
                "fhx",
                "control_module",
                "function_block",
                "moduleclass",
                "control module",
            )
        )
        if low.endswith(".fhx"):
            return ConnectorMatch(
                platform=self.platform,
                confidence=0.92 if marker_hits else 0.72,
            )
        if marker_hits >= 1 and ("deltav" in marker_text or "moduleclass" in marker_text):
            return ConnectorMatch(platform=self.platform, confidence=0.55)
        return ConnectorMatch(platform=self.platform, confidence=0.0)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        text, encoding = decode_fhx_text(content)
        project_name = _find_system_name(text, "DeltaV FHX Export")
        area_match = _AREA_RE.search(text)
        program_name = area_match.group(1).strip().strip('"') if area_match else "Area"
        sections = _find_sections(text)

        routines: list[ControlRoutine] = []
        for section in sections:
            instructions = _extract_blocks(section.raw_text)
            section_type = section.section_type.lower()
            parse_status = "parsed" if instructions else "preserved_only"
            routines.append(
                ControlRoutine(
                    name=section.name,
                    language="function_block",
                    instructions=instructions,
                    raw_logic=section.raw_text[:MAX_RAW_LOGIC_CHARS],
                    parse_status=parse_status,
                    metadata={
                        "platform": "deltav",
                        "module_type": section_type,
                        "parse_status": parse_status,
                        "raw_module_text_present": bool(section.raw_text),
                        "parameters": _extract_parameters(section.raw_text),
                        "references": _extract_links(section.raw_text),
                        "preserved_only": not bool(instructions),
                    },
                )
            )

        if not routines:
            routines.append(_fallback_unknown_routine(text))

        return ControlProject(
            project_name=project_name,
            source_file=filename,
            file_hash=sha256_bytes(content),
            controllers=[
                ControlController(
                    name=project_name,
                    platform="deltav",
                    programs=[
                        ControlProgram(
                            name=program_name or "Area",
                            routines=routines,
                        )
                    ],
                )
            ],
            metadata={
                "connector": self.display_name,
                "connector_platform": self.platform,
                "parser_version": self.parser_version,
                "source_platform": "deltav",
                "ingest_mode": "preservation_shell" if not sections else "foundation_parse",
                "text_encoding": encoding,
                "source_basename": PurePath(filename).name,
                "parse_status": "parsed" if sections else "preserved_only",
            },
        )
