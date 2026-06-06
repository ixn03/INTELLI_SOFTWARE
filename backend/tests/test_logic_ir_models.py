"""Synthetic tests for the vendor-neutral parsed logic IR."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
    LadderBranch,
    LogicBlock,
)
from app.models.reasoning import RelationshipType, WriteBehaviorType  # noqa: E402
from app.parsers.ladder import parse_ladder_rung_text  # noqa: E402
from app.services.logic_ir_validation import (  # noqa: E402
    validate_ladder_rung_ir,
    validate_normalized_output,
)
from app.services.normalization_service import (  # noqa: E402
    INSTRUCTION_SEMANTICS,
    normalize_l5x_project,
)


_SYNTH_COMPLEX_LADDER = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Tags/>
    <Programs>
      <Program Name="PRG_Main">
        <Tags>
          <Tag Name="Permissive_1" DataType="BOOL" TagType="Base"/>
          <Tag Name="Pump_A_Ready" DataType="BOOL" TagType="Base"/>
          <Tag Name="Valve_B_Open" DataType="BOOL" TagType="Base"/>
          <Tag Name="Fault_1" DataType="BOOL" TagType="Base"/>
          <Tag Name="Motor_C_Run" DataType="BOOL" TagType="Base"/>
        </Tags>
        <Routines>
          <Routine Name="R_Ladder" Type="RLL">
            <RLLContent>
              <Rung Number="0">
                <Text><![CDATA[XIC(Permissive_1) [XIC(Pump_A_Ready),[XIC(Valve_B_Open),XIO(Fault_1)]] OTE(Motor_C_Run);]]></Text>
              </Rung>
            </RLLContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


def _project_from_instructions(
    instructions: list[ControlInstruction],
    *,
    program_tags: list[str],
) -> ControlProject:
    routine = ControlRoutine(
        name="R",
        language="ladder",
        instructions=instructions,
        raw_logic="\n".join((i.metadata or {}).get("rung_text", "") for i in instructions),
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
                            for name in program_tags
                        ],
                        routines=[routine],
                    )
                ],
            )
        ],
    )


def _parallel_count(branch: LadderBranch | None) -> int:
    if branch is None:
        return 0
    return (1 if branch.branch_type == "parallel" else 0) + sum(
        _parallel_count(child) for child in branch.children
    )


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(v, key) for v in node)
    return False


class LogicIRModelTests(unittest.TestCase):
    def test_model_vocabulary_is_importable(self) -> None:
        block = LogicBlock(id="block:Pump_A", block_type="function_block")
        self.assertEqual(block.block_type, "function_block")

    def test_connector_populates_ladder_rung_ir(self) -> None:
        project = RockwellL5XConnector().parse(
            "synthetic_complex.xml",
            _SYNTH_COMPLEX_LADDER,
        )
        routine = project.controllers[0].programs[0].routines[0]
        self.assertEqual(len(routine.ladder_rungs), 1)
        rung = routine.ladder_rungs[0]
        self.assertEqual(rung.rung_number, 0)
        self.assertTrue(rung.source_location.path)
        self.assertGreaterEqual(_parallel_count(rung.root_branch), 2)
        self.assertEqual([w.target.tag_name for w in rung.writes], ["Motor_C_Run"])
        self.assertTrue(rung.logic_condition)
        self.assertTrue(rung.logic_condition.resolved)
        expr = rung.logic_condition.expression or {}
        self.assertFalse(_contains_key(expr, "raw_text"))
        self.assertEqual(validate_ladder_rung_ir(rung), [])

    def test_fll_is_registered_and_normalized(self) -> None:
        rung_text = "FLL(Fill_Value,Array_Buffer[0],10);"
        insts = parse_ladder_rung_text(rung_text, rung_number=0)
        self.assertEqual(insts[0].metadata.get("instruction_family"), "move_copy")
        self.assertIn("FLL", INSTRUCTION_SEMANTICS)

        project = _project_from_instructions(
            insts,
            program_tags=["Fill_Value", "Array_Buffer"],
        )
        out = normalize_l5x_project(project)
        rels = [
            r
            for r in out["relationships"]
            if (r.platform_specific or {}).get("instruction_type") == "FLL"
        ]
        reads = [r for r in rels if r.relationship_type == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].write_behavior, WriteBehaviorType.MOVES_VALUE)
        self.assertTrue(writes[0].source_location)
        self.assertEqual(validate_normalized_output(out), [])


if __name__ == "__main__":
    unittest.main()
