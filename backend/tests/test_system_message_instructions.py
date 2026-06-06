"""Synthetic normalization tests for GSV / SSV / MSG instructions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.reasoning import ControlObjectType, RelationshipType  # noqa: E402
from app.parsers.ladder import extract_operand_tags, parse_ladder_rung_text  # noqa: E402
from app.services.logic_ir_validation import validate_normalized_output  # noqa: E402
from app.services.normalization_service import (  # noqa: E402
    INSTRUCTION_SEMANTICS,
    normalize_l5x_project,
)


def _project_from_rung(
    rung_text: str,
    *,
    tags: list[str],
) -> ControlProject:
    instructions = parse_ladder_rung_text(rung_text, rung_number=0)
    routine = ControlRoutine(
        name="R_System",
        language="ladder",
        instructions=instructions,
        raw_logic=rung_text,
        parse_status="parsed",
        metadata={"rockwell_type": "RLL"},
    )
    return ControlProject(
        project_name="PLC",
        controllers=[
            ControlController(
                name="PLC",
                platform="rockwell",
                programs=[
                    ControlProgram(
                        name="PRG",
                        tags=[
                            ControlTag(
                                name=name,
                                data_type="DINT",
                                scope="PRG",
                                platform_source="rockwell_l5x",
                            )
                            for name in tags
                        ],
                        routines=[routine],
                    )
                ],
            )
        ],
    )


class SystemMessageInstructionTests(unittest.TestCase):
    def test_gsv_preserves_blank_instance_operand(self) -> None:
        inst = parse_ladder_rung_text(
            "GSV(WallClockTime,,DateTime,Clock_Readback);",
            rung_number=0,
        )[0]
        self.assertEqual(
            inst.operands,
            ["WallClockTime", "", "DateTime", "Clock_Readback"],
        )
        self.assertEqual(extract_operand_tags([inst]), {"Clock_Readback"})

    def test_gsv_reads_system_attribute_and_writes_destination_tag(self) -> None:
        self.assertTrue(INSTRUCTION_SEMANTICS["GSV"].implemented)
        out = normalize_l5x_project(
            _project_from_rung(
                "GSV(WallClockTime,,DateTime,Clock_Readback);",
                tags=["Clock_Readback"],
            )
        )
        system_objs = [
            o
            for o in out["control_objects"]
            if o.object_type == ControlObjectType.SYSTEM_ATTRIBUTE
        ]
        self.assertEqual(len(system_objs), 1)
        rels = [
            r
            for r in out["relationships"]
            if (r.platform_specific or {}).get("instruction_type") == "GSV"
        ]
        self.assertTrue(
            any(
                r.relationship_type == RelationshipType.READS
                and r.target_id == system_objs[0].id
                for r in rels
            )
        )
        self.assertTrue(
            any(
                r.relationship_type == RelationshipType.WRITES
                and (r.platform_specific or {}).get("operand_role")
                == "system_value_destination"
                for r in rels
            )
        )
        self.assertEqual(validate_normalized_output(out), [])

    def test_ssv_reads_source_and_writes_system_attribute(self) -> None:
        self.assertTrue(INSTRUCTION_SEMANTICS["SSV"].implemented)
        out = normalize_l5x_project(
            _project_from_rung(
                "SSV(Task,MainTask,Rate,New_Rate);",
                tags=["New_Rate"],
            )
        )
        system_obj = next(
            o
            for o in out["control_objects"]
            if o.object_type == ControlObjectType.SYSTEM_ATTRIBUTE
        )
        rels = [
            r
            for r in out["relationships"]
            if (r.platform_specific or {}).get("instruction_type") == "SSV"
        ]
        self.assertTrue(
            any(r.relationship_type == RelationshipType.READS for r in rels)
        )
        self.assertTrue(
            any(
                r.relationship_type == RelationshipType.WRITES
                and r.target_id == system_obj.id
                and r.source_location
                for r in rels
            )
        )
        self.assertEqual(validate_normalized_output(out), [])

    def test_msg_reads_and_writes_message_control_structure(self) -> None:
        self.assertTrue(INSTRUCTION_SEMANTICS["MSG"].implemented)
        out = normalize_l5x_project(
            _project_from_rung(
                "XIC(Permissive_1) MSG(Message_Control);",
                tags=["Permissive_1", "Message_Control"],
            )
        )
        rels = [
            r
            for r in out["relationships"]
            if (r.platform_specific or {}).get("instruction_type") == "MSG"
        ]
        self.assertEqual(
            sorted(r.relationship_type for r in rels),
            [RelationshipType.READS, RelationshipType.WRITES],
        )
        roles = {(r.platform_specific or {}).get("operand_role") for r in rels}
        self.assertEqual(roles, {"message_control_read", "message_control_write"})
        self.assertEqual(validate_normalized_output(out), [])


if __name__ == "__main__":
    unittest.main()
