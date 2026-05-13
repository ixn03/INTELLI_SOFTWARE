from __future__ import annotations

from pathlib import PurePath
from typing import Any, Optional

from lxml import etree

from app.connectors.base import ConnectorMatch, PlatformConnector
from app.connectors.preserved_import import MAX_RAW_LOGIC_CHARS, decode_content_as_text
from app.models.control_model import (
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.services.version_service import sha256_bytes


_SIEMENS_XML_MARKERS = (
    b"Siemens",
    b"TIA",
    b"DocumentInfo",
    b"FlgNet",
    b"Network",
    b"AttributeList",
    b"SW.Blocks.",
)


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _direct_child_text(element: etree._Element, name: str) -> Optional[str]:
    for child in element:
        if _local_name(child) == name and child.text:
            return child.text.strip()
    return None


def _first_desc_text(element: etree._Element, names: tuple[str, ...]) -> Optional[str]:
    for child in element.iter():
        if _local_name(child) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def _raw_xml(element: etree._Element) -> str:
    return etree.tostring(element, encoding="unicode")[:MAX_RAW_LOGIC_CHARS]


def _block_type(element: etree._Element) -> Optional[str]:
    local = _local_name(element)
    for block_type in ("OB", "FB", "FC", "DB"):
        if local == block_type or local.endswith(f".{block_type}"):
            return block_type
    return None


def _routine_language(block_type: str) -> str:
    return {
        "OB": "tia_ob",
        "FB": "tia_fb",
        "FC": "tia_fc",
        "DB": "tia_db",
    }.get(block_type, "unknown")


def _normalize_block_language(value: Optional[str]) -> str:
    low = (value or "").strip().lower().replace(" ", "").replace("-", "")
    if low in {"lad", "ladder", "kont"}:
        return "LAD"
    if low in {"fbd", "functionblockdiagram", "fup"}:
        return "FBD"
    if low in {"scl", "st", "structuredtext"}:
        return "SCL/ST"
    if low in {"stl", "statementlist", "awl"}:
        return "STL"
    return value.strip() if value and value.strip() else "unknown"


def _block_name(element: etree._Element, fallback: str) -> str:
    return (
        element.get("Name")
        or _direct_child_text(element, "Name")
        or _first_desc_text(element, ("Name",))
        or fallback
    )


def _extract_project_name(root: etree._Element) -> str:
    for key in ("ProjectName", "Project", "Name"):
        value = _first_desc_text(root, (key,))
        if value:
            return value
    return "Siemens TIA Project"


def _extract_controller_name(root: etree._Element) -> str:
    for key in ("DeviceName", "ControllerName", "CpuName", "Name"):
        value = _first_desc_text(root, (key,))
        if value:
            return value
    return "Siemens TIA Project"


def _extract_interface_tags(element: etree._Element, scope: str) -> list[ControlTag]:
    tags: list[ControlTag] = []
    seen: set[str] = set()
    for member in element.iter():
        local = _local_name(member)
        if local not in {"Member", "Parameter", "Param", "Tag", "Variable"}:
            continue
        name = member.get("Name") or _direct_child_text(member, "Name")
        if not name or name in seen:
            continue
        data_type = (
            member.get("Datatype")
            or member.get("DataType")
            or member.get("Type")
            or _direct_child_text(member, "Datatype")
            or _direct_child_text(member, "DataType")
            or _direct_child_text(member, "Type")
        )
        section = None
        parent = member.getparent()
        if parent is not None:
            section = parent.get("Name") or _local_name(parent)
        tags.append(
            ControlTag(
                name=name,
                data_type=data_type,
                scope=scope,
                platform_source="siemens_tia_interface",
                metadata={
                    "interface_section": section,
                    "declaration_kind": local,
                    "block_member": True,
                },
            )
        )
        seen.add(name)
    return tags


def _iter_block_elements(root: etree._Element) -> list[etree._Element]:
    blocks: list[etree._Element] = []
    for element in root.iter():
        if _block_type(element):
            blocks.append(element)
    return blocks


def _parse_xml(content: bytes) -> Optional[etree._Element]:
    parser = etree.XMLParser(recover=True, remove_blank_text=False, huge_tree=False)
    try:
        return etree.fromstring(content, parser=parser)
    except etree.XMLSyntaxError:
        return None


class SiemensTIAConnector(PlatformConnector):
    platform = "siemens_tia"
    display_name = "Siemens TIA Portal Openness XML"
    supported_extensions = (
        ".xml",
        ".scl",
        ".zap13",
        ".zap14",
        ".zap15",
        ".zap16",
        ".zap17",
        ".zap18",
        ".zap19",
    )
    parser_version = "0.2.0"

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        low = filename.lower()
        head = content[:65536]
        if low.endswith(".xml"):
            if b"RSLogix5000Content" in head[:4096]:
                return ConnectorMatch(platform=self.platform, confidence=0.0)
            specific_hits = sum(1 for marker in _SIEMENS_XML_MARKERS if marker in head)
            if b"SW.Blocks." in head or b"FlgNet" in head:
                return ConnectorMatch(platform=self.platform, confidence=0.82)
            if specific_hits >= 2:
                return ConnectorMatch(platform=self.platform, confidence=0.62)
            if head.lstrip().startswith(b"<?xml"):
                return ConnectorMatch(platform=self.platform, confidence=0.18)
        if low.endswith(".scl"):
            return ConnectorMatch(platform=self.platform, confidence=0.25)
        if any(low.endswith(ext) for ext in self.supported_extensions if ext.startswith(".zap")):
            return ConnectorMatch(platform=self.platform, confidence=0.72)
        return ConnectorMatch(platform=self.platform, confidence=0.0)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        if not filename.lower().endswith(".xml"):
            return self._preserved_text_project(filename, content, "unsupported_extension")

        root = _parse_xml(content)
        if root is None:
            return self._preserved_text_project(filename, content, "xml_parse_error")

        project_name = _extract_project_name(root)
        controller_name = _extract_controller_name(root)
        block_elements = _iter_block_elements(root)

        routines: list[ControlRoutine] = []
        program_tags: list[ControlTag] = []
        for idx, block in enumerate(block_elements, start=1):
            block_type = _block_type(block)
            if not block_type:
                continue
            name = _block_name(block, f"{block_type}_{idx}")
            block_language = _normalize_block_language(
                _first_desc_text(block, ("ProgrammingLanguage", "Language", "BlockLanguage"))
                or block.get("ProgrammingLanguage")
                or block.get("Language")
            )
            interface_tags = _extract_interface_tags(block, name)
            if block_type == "DB":
                program_tags.extend(interface_tags)
            instructions = self._extract_placeholder_instructions(block, block_language)
            routines.append(
                ControlRoutine(
                    name=name,
                    language=_routine_language(block_type),  # type: ignore[arg-type]
                    instructions=instructions,
                    raw_logic=_raw_xml(block),
                    parse_status="parsed",
                    metadata={
                        "platform": "siemens_tia",
                        "block_type": block_type,
                        "block_language": block_language,
                        "parse_status": "parsed",
                        "raw_xml_present": True,
                        "interface_tag_count": len(interface_tags),
                    },
                )
            )
            program_tags.extend(tag for tag in interface_tags if block_type != "DB")

        parse_status = "parsed" if routines else "preserved_only"
        if not routines:
            routines.append(
                ControlRoutine(
                    name="Export",
                    language="unknown",
                    raw_logic=decode_content_as_text(content)[:MAX_RAW_LOGIC_CHARS],
                    parse_status="preserved_only",
                    metadata={
                        "platform": "siemens_tia",
                        "parse_status": "preserved_only",
                        "raw_xml_present": True,
                    },
                )
            )

        return ControlProject(
            project_name=project_name,
            source_file=filename,
            file_hash=sha256_bytes(content),
            controllers=[
                ControlController(
                    name=controller_name,
                    platform="siemens_tia",
                    programs=[
                        ControlProgram(
                            name="Blocks",
                            tags=program_tags,
                            routines=routines,
                        )
                    ],
                )
            ],
            metadata={
                "connector": self.display_name,
                "connector_platform": self.platform,
                "parser_version": self.parser_version,
                "parse_status": parse_status,
                "source_platform": "siemens_tia",
                "source_basename": PurePath(filename).name,
            },
        )

    def _extract_placeholder_instructions(
        self,
        block: etree._Element,
        block_language: str,
    ) -> list[ControlInstruction]:
        if block_language == "FBD":
            out: list[ControlInstruction] = []
            for idx, call in enumerate(block.iter(), start=1):
                local = _local_name(call)
                if local not in {"Call", "Part", "Component"}:
                    continue
                block_name = (
                    call.get("Name")
                    or call.get("Type")
                    or call.get("UId")
                    or f"FBD_Block_{idx}"
                )
                out.append(
                    ControlInstruction(
                        instruction_type=block_name,
                        language="fbd",
                        id=call.get("UId") or str(idx),
                        raw_text=_raw_xml(call),
                        metadata={
                            "object_subtype": "function_block",
                            "block_type": call.get("Type") or local,
                            "parse_status": "unsupported",
                        },
                    )
                )
            return out
        return []

    def _preserved_text_project(
        self,
        filename: str,
        content: bytes,
        parse_status: str,
    ) -> ControlProject:
        text = decode_content_as_text(content)
        return ControlProject(
            project_name="Siemens TIA Project",
            source_file=filename,
            file_hash=sha256_bytes(content),
            controllers=[
                ControlController(
                    name="Siemens TIA Project",
                    platform="siemens_tia",
                    programs=[
                        ControlProgram(
                            name="Blocks",
                            routines=[
                                ControlRoutine(
                                    name="Export",
                                    language="unknown",
                                    raw_logic=text[:MAX_RAW_LOGIC_CHARS],
                                    parse_status="preserved_only",
                                    metadata={
                                        "platform": "siemens_tia",
                                        "parse_status": parse_status,
                                        "raw_xml_present": bool(text),
                                    },
                                )
                            ],
                        )
                    ],
                )
            ],
            metadata={
                "connector": self.display_name,
                "connector_platform": self.platform,
                "parser_version": self.parser_version,
                "parse_status": parse_status,
            },
        )
