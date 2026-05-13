from __future__ import annotations

import re
from pathlib import PurePath

from lxml import etree

from app.connectors.base import ConnectorMatch, PlatformConnector
from app.connectors.preserved_import import MAX_RAW_LOGIC_CHARS, decode_content_as_text
from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
)
from app.services.version_service import sha256_bytes


_FILENAME_MARKERS = ("honeywell", "experion", "c300", "controlbuilder")
_CONTENT_MARKERS = ("experion", "c300", "cee", "control builder", "controlbuilder")
_SUPPORTED_EXTENSIONS = (".xml", ".txt", ".csv", ".cl", ".zip", ".hwl", ".hwh", ".hsc", ".epr")
_MODULE_RE = re.compile(
    r"(?im)^\s*(CONTROL\s+MODULE|CONTROL_MODULE|STRATEGY|MODULE|CEE|C300)\s*[:,]?\s*\"?([^\",\r\n]+)\"?"
)


def _confidence_reason(filename: str, text: str) -> tuple[float, str]:
    low_name = filename.lower()
    low_text = text[:65536].lower()
    name_hits = [marker for marker in _FILENAME_MARKERS if marker in low_name]
    content_hits = [marker for marker in _CONTENT_MARKERS if marker in low_text]
    ext_match = low_name.endswith(_SUPPORTED_EXTENSIONS)
    if name_hits and (ext_match or content_hits):
        return 0.78, f"filename markers: {', '.join(name_hits)}"
    if content_hits and ext_match:
        return 0.58, f"content markers: {', '.join(content_hits)}"
    if name_hits:
        return 0.45, f"filename markers: {', '.join(name_hits)}"
    if ext_match and low_name.endswith((".hwl", ".hwh", ".hsc", ".epr")):
        return 0.4, "legacy Honeywell export extension"
    return 0.0, "no Honeywell/Experion markers"


def _detect_export_format(filename: str, text: str) -> str:
    low = filename.lower()
    stripped = text.lstrip()
    if low.endswith(".zip"):
        return "zip_placeholder"
    if low.endswith(".csv") or "," in text[:2048]:
        return "csv_like"
    if stripped.startswith("<") or low.endswith(".xml"):
        return "xml_like"
    if low.endswith(".cl"):
        return "control_language_text"
    return "text"


def _project_name_from_text(text: str) -> str:
    for pattern in (
        r"(?im)^\s*PROJECT\s*[:,=]\s*\"?([^\",\r\n]+)\"?",
        r"(?im)^\s*SYSTEM\s*[:,=]\s*\"?([^\",\r\n]+)\"?",
        r"(?im)^\s*EXPORT\s*[:,=]\s*\"?([^\",\r\n]+)\"?",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return "Honeywell Experion Export"


def _xml_names(text: str) -> list[str]:
    try:
        root = etree.fromstring(
            text.encode("utf-8", errors="ignore"),
            parser=etree.XMLParser(recover=True),
        )
    except etree.XMLSyntaxError:
        return []
    names: list[str] = []
    for element in root.iter():
        local = etree.QName(element).localname.lower()
        if local in {"controlmodule", "control_module", "strategy", "module"}:
            name = element.get("Name") or element.get("name")
            if name:
                names.append(name)
    return names


def _module_names(text: str, export_format: str) -> list[str]:
    names = [m.group(2).strip().strip('"') for m in _MODULE_RE.finditer(text)]
    if names:
        return names
    if export_format == "xml_like":
        return _xml_names(text)
    return []


class HoneywellExperionConnector(PlatformConnector):
    platform = "honeywell"
    display_name = "Honeywell Experion / Control Builder"
    supported_extensions = _SUPPORTED_EXTENSIONS
    parser_version = "0.2.0"

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        text = decode_content_as_text(content[:65536])
        confidence, _reason = _confidence_reason(filename, text)
        return ConnectorMatch(platform=self.platform, confidence=confidence)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        text = decode_content_as_text(content)
        project_name = _project_name_from_text(text)
        export_format = _detect_export_format(filename, text)
        _confidence, reason = _confidence_reason(filename, text)
        module_names = _module_names(text, export_format)

        routines: list[ControlRoutine] = []
        if module_names:
            for name in module_names:
                routines.append(
                    ControlRoutine(
                        name=name,
                        language="unknown",
                        raw_logic=text[:MAX_RAW_LOGIC_CHARS],
                        parse_status="preserved_only",
                        metadata={
                            "platform": "honeywell_experion",
                            "parse_status": "preserved_only",
                            "export_format_detected": export_format,
                            "raw_source_present": bool(text),
                            "confidence_reason": reason,
                            "identified_as": "control_module_or_strategy",
                        },
                    )
                )
        else:
            routines.append(
                ControlRoutine(
                    name="Preserved Export",
                    language="unknown",
                    raw_logic=text[:MAX_RAW_LOGIC_CHARS],
                    parse_status="preserved_only",
                    metadata={
                        "platform": "honeywell_experion",
                        "parse_status": "preserved_only",
                        "export_format_detected": export_format,
                        "raw_source_present": bool(text),
                        "confidence_reason": reason,
                        "preserved_only": True,
                    },
                )
            )

        return ControlProject(
            project_name=project_name,
            source_file=filename,
            file_hash=sha256_bytes(content),
            controllers=[
                ControlController(
                    name=project_name,
                    platform="honeywell",
                    programs=[
                        ControlProgram(
                            name="Experion Export",
                            routines=routines,
                        )
                    ],
                )
            ],
            metadata={
                "connector": self.display_name,
                "connector_platform": self.platform,
                "parser_version": self.parser_version,
                "source_platform": "honeywell_experion",
                "parse_status": "preserved_only",
                "export_format_detected": export_format,
                "raw_source_present": bool(text),
                "confidence_reason": reason,
                "source_basename": PurePath(filename).name,
            },
        )
