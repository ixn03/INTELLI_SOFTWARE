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
    lines: list[str] = []

    for line_element in routine_element.findall(".//Line"):
        if line_element.text and line_element.text.strip():
            lines.append(line_element.text.strip())

    if lines:
        return "\n".join(lines)

    for text_element in routine_element.findall(".//Text"):
        if text_element.text and text_element.text.strip():
            lines.append(text_element.text.strip())

    if lines:
        return "\n".join(lines)

    return "".join(routine_element.itertext()).strip()


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
                for tag in program_element.findall("./Tags/Tag")
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
        routine_type = (
            _attr(routine_element, "Type", "unknown")
            .strip()
            .lower()
        )
        routine_name = _attr(routine_element, "Name", "Unknown Routine")

        if routine_type in {"rll", "ladder"}:

            instructions = []
            raw_rungs: list[str] = []

            for rung_element in routine_element.findall(".//Rung"):

                rung_number = int(
                    rung_element.get("Number") or 0
                )

                rung_text = rung_element.findtext("./Text") or ""

                if rung_text:
                    raw_rungs.append(rung_text)

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
                raw_logic="\n".join(raw_rungs) or None,
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                },
            )

        if "st" in routine_type:

            routine_text = _extract_structured_text(
                routine_element
            )

            return ControlRoutine(
                name=routine_name,
                language="structured_text",
                instructions=parse_structured_text(
                    routine_text,
                    routine_name,
                ),
                raw_logic=routine_text or None,
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                },
            )

        return ControlRoutine(
            name=routine_name,
            language="unknown",
            instructions=[],
            raw_logic=None,
            metadata={
                "rockwell_type": routine_element.get("Type"),
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
