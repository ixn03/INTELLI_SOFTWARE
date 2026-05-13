from lxml import etree

from app.connectors.base import ConnectorMatch, PlatformConnector
from app.models.control_model import (
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.parsers.ladder import extract_operand_tags, parse_ladder_rung_text
from app.parsers.st_comments import strip_st_comments_for_parsing
from app.parsers.structured_text import (
    extract_structured_text_tags,
    parse_structured_text,
)
from app.services.version_service import sha256_bytes


def _attr(element: etree._Element | None, name: str, default: str = "") -> str:
    if element is None:
        return default
    return str(element.get(name) or default)


def _tag_from_element(element: etree._Element, scope: str) -> ControlTag:
    description = element.findtext(".//Description")

    return ControlTag(
        name=_attr(element, "Name"),
        data_type=element.get("DataType"),
        description=description.strip() if description else None,
        scope=scope,
        platform_source="rockwell_l5x",
        metadata={
            "tag_type": element.get("TagType"),
            "external_access": element.get("ExternalAccess"),
        },
    )


def _extract_structured_text(routine_element: etree._Element) -> str:
    """Join ST routine lines in document order; preserve comments and blank lines."""

    line_elements = routine_element.findall(".//Line")
    if line_elements:

        def _line_key(el: etree._Element) -> int:
            try:
                return int(el.get("Number") or 0)
            except ValueError:
                return 0

        ordered = sorted(line_elements, key=_line_key)
        lines: list[str] = []
        for line_element in ordered:
            t = line_element.text if line_element.text is not None else ""
            lines.append(t.rstrip("\n\r"))
        return "\n".join(lines)

    text_elements = routine_element.findall(".//Text")
    if text_elements:
        parts = [t.text.strip() for t in text_elements if t.text and t.text.strip()]
        if parts:
            return "\n".join(parts)

    return "".join(routine_element.itertext()).strip()


def _normalize_routine_language(type_raw: str) -> tuple[str, str]:
    """Map Rockwell ``Routine/@Type`` to ``ControlRoutine.language``.

    Returns ``(language, normalized_lower)`` where ``normalized_lower``
    is the Rockwell type string lowercased for metadata. Intentionally
    unsupported: SFC / FBD / unknown AOI wrapper types -> ``unknown``.
    """

    low = type_raw.strip().lower().replace("-", "_")
    if low in {"rll", "ladder", "ld"}:
        return "ladder", low
    if low in {"st", "structuredtext", "structured_text"}:
        return "structured_text", low
    return "unknown", low


class RockwellL5XConnector(PlatformConnector):
    platform = "rockwell"
    display_name = "Rockwell Studio 5000 L5X"

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        if filename.lower().endswith(".l5x"):
            return ConnectorMatch(platform=self.platform, confidence=0.9)

        if b"<RSLogix5000Content" in content[:4096]:
            return ConnectorMatch(platform=self.platform, confidence=0.8)

        return ConnectorMatch(platform=self.platform, confidence=0.0)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        root = etree.fromstring(content)

        controller_element = root.find(".//Controller")

        controller_name = _attr(
            controller_element,
            "Name",
            "Unknown Controller",
        )

        controller_tags = [
            _tag_from_element(tag, "controller")
            for tag in root.findall(".//Controller/Tags/Tag")
            if tag.get("Name")
        ]

        programs: list[ControlProgram] = []

        for program_element in root.findall(".//Programs/Program"):

            program_name = _attr(
                program_element,
                "Name",
                "Unknown Program",
            )

            program_tags = [
                _tag_from_element(tag, program_name)
                for tag in program_element.findall(".//Tags/Tag")
                if tag.get("Name")
            ]

            routines = [
                self._parse_routine(routine_element)
                for routine_element in program_element.findall("./Routines/Routine")
            ]

            programs.append(
                ControlProgram(
                    name=program_name,
                    tags=program_tags,
                    routines=routines,
                )
            )

        project = ControlProject(
            project_name=controller_name,
            source_file=filename,
            file_hash=sha256_bytes(content),
            controllers=[
                ControlController(
                    name=controller_name,
                    platform="rockwell",
                    controller_tags=controller_tags,
                    programs=programs,
                )
            ],
            metadata={
                "connector": self.display_name,
            },
        )

        return self._add_discovered_tags(project)

    def _parse_routine(self, routine_element: etree._Element) -> ControlRoutine:
        type_raw = _attr(routine_element, "Type", "unknown")
        language, norm_type = _normalize_routine_language(type_raw)
        routine_name = _attr(routine_element, "Name", "Unknown Routine")

        if language == "ladder":

            instructions = []
            raw_rungs: list[str] = []

            rung_elements = routine_element.findall(".//Rung")

            def _rung_sort_key(el: etree._Element) -> tuple[int, int]:
                try:
                    return (0, int(el.get("Number") or 0))
                except ValueError:
                    return (1, 0)

            for rung_element in sorted(rung_elements, key=_rung_sort_key):

                rung_number = int(
                    rung_element.get("Number") or 0
                )

                rung_text = rung_element.findtext(".//Text")
                if rung_text is None:
                    rung_text = ""
                else:
                    rung_text = rung_text.strip()

                raw_rungs.append(rung_text)

                if rung_text:
                    instructions.extend(
                        parse_ladder_rung_text(
                            rung_text,
                            rung_number,
                        )
                    )

            return ControlRoutine(
                name=routine_name,
                language="ladder",
                instructions=instructions,
                raw_logic="\n".join(raw_rungs) if raw_rungs else None,
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                    "rockwell_type_normalized": norm_type,
                },
            )

        if language == "structured_text":

            routine_text = _extract_structured_text(
                routine_element
            )

            # Instruction-level ST parse strips comments so tag / call
            # extraction is stable; ``raw_logic`` stays verbatim for audit.
            st_for_instructions = (
                strip_st_comments_for_parsing(routine_text)
                if routine_text
                else ""
            )

            return ControlRoutine(
                name=routine_name,
                language="structured_text",
                instructions=parse_structured_text(
                    st_for_instructions,
                    routine_name,
                ),
                raw_logic=routine_text or None,
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                    "rockwell_type_normalized": norm_type,
                },
            )

        return ControlRoutine(
            name=routine_name,
            language="unknown",
            instructions=[],
            raw_logic=None,
            metadata={
                "rockwell_type": routine_element.get("Type"),
                "rockwell_type_normalized": norm_type,
            },
        )

    def _add_discovered_tags(
        self,
        project: ControlProject,
    ) -> ControlProject:

        known_tags = {
            tag.name
            for controller in project.controllers
            for tag in controller.controller_tags
        }

        for controller in project.controllers:

            for program in controller.programs:

                known_tags.update(
                    tag.name for tag in program.tags
                )

                discovered = set()

                for routine in program.routines:

                    if routine.language == "structured_text":

                        discovered.update(
                            extract_structured_text_tags(
                                routine.instructions
                            )
                        )

                    else:

                        discovered.update(
                            extract_operand_tags(
                                routine.instructions
                            )
                        )

                for tag_name in sorted(discovered - known_tags):

                    program.tags.append(
                        ControlTag(
                            name=tag_name,
                            scope=program.name,
                            platform_source="rockwell_l5x_discovered_operand",
                        )
                    )

                    known_tags.add(tag_name)

        return project
