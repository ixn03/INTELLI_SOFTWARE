from lxml import etree

from app.connectors.base import ConnectorMatch, PlatformConnector
from app.models.control_model import (
    AddOnInstructionDef,
    AOIParameter,
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
    DataTypeDef,
    DataTypeMember,
)
from app.parsers.fbd import (
    extract_fbd_block_tags,
    extract_fbd_tags,
    parse_l5x_fbd_blocks,
    parse_l5x_fbd_routine,
)
from app.parsers.ladder import (
    build_ladder_rung_ir,
    extract_operand_tags,
    parse_ladder_rung_text,
)
from app.parsers.ladder_logic import build_rung_logic_expression
from app.parsers.sfc import extract_sfc_tags, parse_l5x_sfc_routine
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


def _controller_name_from_export(
    root: etree._Element,
    controller_element: etree._Element | None,
) -> tuple[str, dict[str, object]]:
    """Return a deterministic controller name without inventing plant data."""

    explicit = _attr(controller_element, "Name")
    if explicit:
        return explicit, {
            "name_source": "controller_name_attribute",
            "source_name_missing": False,
        }
    target_name = _attr(root, "TargetName")
    target_type = _attr(root, "TargetType")
    if target_name:
        return target_name, {
            "name_source": "root_target_name",
            "source_name_missing": True,
            "root_target_type": target_type,
        }
    return "Controller_001", {
        "name_source": "deterministic_fallback",
        "source_name_missing": True,
        "root_target_type": target_type,
    }


def _program_name_from_export(
    program_element: etree._Element,
    index: int,
) -> tuple[str, dict[str, object]]:
    """Return explicit program name or a stable structural fallback."""

    explicit = _attr(program_element, "Name")
    if explicit:
        return explicit, {
            "name_source": "program_name_attribute",
            "source_name_missing": False,
            "program_index": index,
        }
    return f"Program_{index:03d}", {
        "name_source": "deterministic_fallback",
        "source_name_missing": True,
        "program_index": index,
    }


def _tag_from_element(element: etree._Element, scope: str) -> ControlTag:
    description = element.findtext(".//Description")

    return ControlTag(
        name=_attr(element, "Name"),
        data_type=element.get("DataType"),
        description=description.strip() if description else None,
        scope=scope,
        platform_source="rockwell_l5x",
        alias_for=element.get("AliasFor") or None,
        metadata={
            "tag_type": element.get("TagType"),
            "external_access": element.get("ExternalAccess"),
            "alias_for": element.get("AliasFor"),
        },
    )


def _bool_attr(element: etree._Element, name: str, default: bool = False) -> bool:
    raw = element.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"true", "1", "yes"}


def _parse_aoi_definitions(
    controller_element: etree._Element | None,
) -> list[AddOnInstructionDef]:
    """Parse ``<AddOnInstructionDefinitions>`` into staging models.

    Captures each AOI's name, its declared parameters (Name / Usage /
    DataType / Required / Visible / alias target) in document order, and
    the name of its internal logic routine (if present) so the normalizer
    can optionally link an instance to its body via a CALLS edge.
    """

    if controller_element is None:
        return []

    defs: list[AddOnInstructionDef] = []
    for aoi in controller_element.findall(
        "./AddOnInstructionDefinitions/AddOnInstructionDefinition"
    ):
        name = _attr(aoi, "Name")
        if not name:
            continue

        params = _parse_l5x_parameters(aoi)

        # Internal logic routine: prefer one literally named "Logic",
        # else the first RLL/ST routine, else the first routine.
        logic_routine: str | None = None
        routine_els = aoi.findall("./Routines/Routine")
        for r in routine_els:
            if (r.get("Name") or "").lower() == "logic":
                logic_routine = r.get("Name")
                break
        if logic_routine is None:
            for r in routine_els:
                if (r.get("Type") or "").upper() in {"RLL", "ST"}:
                    logic_routine = r.get("Name")
                    break
        if logic_routine is None and routine_els:
            logic_routine = routine_els[0].get("Name")

        description = aoi.findtext("./Description")
        defs.append(
            AddOnInstructionDef(
                name=name,
                parameters=params,
                logic_routine=logic_routine,
                revision=aoi.get("Revision"),
                description=description.strip() if description else None,
                metadata={"class": aoi.get("Class"), "vendor": aoi.get("Vendor")},
            )
        )
    return defs


def _parse_data_type_defs(
    controller_element: etree._Element | None,
) -> list[DataTypeDef]:
    """Parse user-defined ``<DataTypes>`` (UDTs) into staging models.

    Hidden host members (Rockwell pads UDTs with ``ZZZZZZZZZZ...`` filler
    and ``Hidden="true"`` members) are skipped so only the engineer-facing
    members survive.
    """

    if controller_element is None:
        return []

    defs: list[DataTypeDef] = []
    for dt in controller_element.findall("./DataTypes/DataType"):
        # Only user-defined types are UDTs; predefined/module types are
        # already understood structurally.
        if (dt.get("Class") or "User") not in {"User", None}:
            continue
        name = _attr(dt, "Name")
        if not name:
            continue
        members: list[DataTypeMember] = []
        for m in dt.findall("./Members/Member"):
            mname = _attr(m, "Name")
            if not mname:
                continue
            if _bool_attr(m, "Hidden", False):
                continue
            if mname.upper().startswith("ZZZZZZZZZZ"):
                continue
            mdesc = m.findtext("./Description")
            members.append(
                DataTypeMember(
                    name=mname,
                    data_type=m.get("DataType"),
                    description=mdesc.strip() if mdesc else None,
                )
            )
        dtdesc = dt.findtext("./Description")
        defs.append(
            DataTypeDef(
                name=name,
                members=members,
                description=dtdesc.strip() if dtdesc else None,
                metadata={"family": dt.get("Family")},
            )
        )
    return defs


def _parse_l5x_parameters(parent: etree._Element) -> list[AOIParameter]:
    """Parse ``./Parameters/Parameter`` children in document order."""

    params: list[AOIParameter] = []
    for p in parent.findall("./Parameters/Parameter"):
        pname = _attr(p, "Name")
        if not pname:
            continue
        params.append(
            AOIParameter(
                name=pname,
                usage=p.get("Usage"),
                data_type=p.get("DataType"),
                required=_bool_attr(p, "Required", False),
                visible=_bool_attr(p, "Visible", True),
                alias_for=p.get("AliasFor") or None,
            )
        )
    return params


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


def _logic_expression_payload(expression: object | None) -> dict | None:
    """Serialize a LogicExpression without preserving raw instruction text."""

    if expression is None or not hasattr(expression, "model_dump"):
        return None
    payload = expression.model_dump(mode="json")  # type: ignore[attr-defined]

    def scrub(node: dict) -> None:
        node.pop("raw_text", None)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                scrub(child)

    scrub(payload)
    return payload


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
    if low in {"fbd", "function_block", "functionblockdiagram"}:
        return "function_block", low
    if low in {"sfc", "sequential_function_chart"}:
        return "sfc", low
    return "unknown", low


class RockwellL5XConnector(PlatformConnector):
    platform = "rockwell"
    display_name = "Rockwell Studio 5000 L5X"
    supported_extensions = (".l5x",)
    parser_version = "1.0.0"

    def can_parse(self, filename: str, content: bytes) -> ConnectorMatch:
        if filename.lower().endswith(".l5x"):
            return ConnectorMatch(platform=self.platform, confidence=0.9)

        if b"<RSLogix5000Content" in content[:4096]:
            return ConnectorMatch(platform=self.platform, confidence=0.8)

        return ConnectorMatch(platform=self.platform, confidence=0.0)

    def parse(self, filename: str, content: bytes) -> ControlProject:
        root = etree.fromstring(content)

        controller_element = root.find(".//Controller")

        controller_name, controller_name_metadata = _controller_name_from_export(
            root,
            controller_element,
        )

        controller_tags = [
            _tag_from_element(tag, "controller")
            for tag in root.findall(".//Controller/Tags/Tag")
            if tag.get("Name")
        ]

        aoi_defs = _parse_aoi_definitions(controller_element)
        data_type_defs = _parse_data_type_defs(controller_element)

        programs: list[ControlProgram] = []

        missing_program_names = 0

        for program_index, program_element in enumerate(
            root.findall(".//Programs/Program"),
            start=1,
        ):

            program_name, program_name_metadata = _program_name_from_export(
                program_element,
                program_index,
            )
            if program_name_metadata.get("source_name_missing"):
                missing_program_names += 1

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
                    metadata=program_name_metadata,
                )
            )

        # AOI-definition-only exports (TargetType=AddOnInstructionDefinition)
        # have no Programs section; surface each AOI's internal routines so
        # ST/RLL bodies participate in grading and normalization.
        if controller_element is not None:
            for aoi_el in controller_element.findall(
                "./AddOnInstructionDefinitions/AddOnInstructionDefinition"
            ):
                aoi_name = _attr(aoi_el, "Name")
                if not aoi_name:
                    continue
                aoi_routines = [
                    self._parse_routine(routine_el)
                    for routine_el in aoi_el.findall("./Routines/Routine")
                ]
                if not aoi_routines:
                    continue
                if any(p.name == f"__AOI__/{aoi_name}" for p in programs):
                    continue
                programs.append(
                    ControlProgram(
                        name=f"__AOI__/{aoi_name}",
                        tags=[],
                        routines=aoi_routines,
                        metadata={
                            "name_source": "aoi_definition_name",
                            "source_name_missing": False,
                        },
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
                    add_on_instruction_defs=aoi_defs,
                    data_type_defs=data_type_defs,
                    metadata=controller_name_metadata,
                )
            ],
            metadata={
                "connector": self.display_name,
                "controller_name_metadata": controller_name_metadata,
                "missing_program_name_count": missing_program_names,
                "program_count": len(programs),
            },
        )

        return self._add_discovered_tags(project)

    def _parse_routine(self, routine_element: etree._Element) -> ControlRoutine:
        type_raw = _attr(routine_element, "Type", "unknown")
        language, norm_type = _normalize_routine_language(type_raw)
        routine_name = _attr(routine_element, "Name", "Unknown Routine")
        routine_params = _parse_l5x_parameters(routine_element)

        if language == "ladder":

            instructions = []
            ladder_rungs = []
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

                rung_instructions = []
                if rung_text:
                    rung_instructions = parse_ladder_rung_text(
                        rung_text,
                        rung_number,
                    )
                    instructions.extend(rung_instructions)

                expression, warnings, resolved = build_rung_logic_expression(
                    rung_instructions,
                    rung_raw_text=rung_text,
                    rung_number=rung_number,
                )
                ladder_rungs.append(
                    build_ladder_rung_ir(
                        rung_text,
                        rung_number,
                        instructions=rung_instructions,
                        logic_expression=_logic_expression_payload(expression),
                        logic_expression_resolved=resolved,
                        logic_warnings=warnings,
                        routine=routine_name,
                    )
                )

            return ControlRoutine(
                name=routine_name,
                language="ladder",
                instructions=instructions,
                raw_logic="\n".join(raw_rungs) if raw_rungs else None,
                ladder_rungs=ladder_rungs,
                parameters=routine_params,
                parse_status="parsed",
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
                parameters=routine_params,
                parse_status="parsed",
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                    "rockwell_type_normalized": norm_type,
                },
            )

        if language == "function_block":
            instructions = parse_l5x_fbd_routine(routine_element)
            fbd_blocks = parse_l5x_fbd_blocks(
                routine_element,
                source_file=None,
                controller=None,
                program=None,
                routine=routine_name,
            )
            return ControlRoutine(
                name=routine_name,
                language="function_block",
                instructions=instructions,
                raw_logic=None,
                fbd_blocks=fbd_blocks,
                parameters=routine_params,
                parse_status="parsed" if fbd_blocks or instructions else "unsupported",
                metadata={
                    "rockwell_type": routine_element.get("Type"),
                    "rockwell_type_normalized": norm_type,
                    "raw_logic_preserved": False,
                    "fbd_block_count": len(fbd_blocks),
                },
            )

        if language == "sfc":
            instructions = parse_l5x_sfc_routine(routine_element)
            raw_logic = etree.tostring(
                routine_element,
                encoding="unicode",
                method="xml",
            ).strip()
            return ControlRoutine(
                name=routine_name,
                language="sfc",
                instructions=instructions,
                raw_logic=raw_logic or None,
                parameters=routine_params,
                parse_status="parsed" if instructions else "unsupported",
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
            parameters=routine_params,
            parse_status="unsupported",
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
                    elif routine.language == "function_block":
                        discovered.update(extract_fbd_tags(routine.instructions))
                        discovered.update(extract_fbd_block_tags(routine.fbd_blocks))
                    elif routine.language == "sfc":
                        discovered.update(extract_sfc_tags(routine.instructions))
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
