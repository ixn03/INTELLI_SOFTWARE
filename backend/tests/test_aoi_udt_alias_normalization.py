"""Unit tests for Phase 1 (NOP / MOVE) and Phase 2 (AOI / UDT / alias).

Covers:

* ``NOP`` is a recognized no-op: registered, emits no cause/effect edges,
  and no longer counts as an unknown opcode.
* ``MOVE`` is a genuine move-style opcode (mirror of ``MOV``): source
  READS + destination WRITES(``moves_value``).
* AOI instance calls resolve to a universal ``FUNCTION_BLOCK`` LogicBlock
  with typed parameter bindings: Input -> READS, Output -> WRITES,
  InOut -> READS + WRITES, backing tag -> REFERENCES, and a CALLS edge
  into the AOI body routine.
* Alias tags emit an ``alias -> base`` REFERENCES edge.
* UDT member accesses carry ``udt_type`` / ``udt_member`` metadata.
* Conservative fallback: an AOI call whose operand count matches neither
  the Required-only nor full parameter list is flagged, not guessed.

Run with::

    python -m pytest tests/test_aoi_udt_alias_normalization.py -q
"""

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.control_model import (  # noqa: E402
    AddOnInstructionDef,
    AOIParameter,
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
    DataTypeDef,
    DataTypeMember,
)
from app.models.reasoning import (  # noqa: E402
    ControlObjectType,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.normalization_service import (  # noqa: E402
    INSTRUCTION_SEMANTICS,
    normalize_l5x_project,
)

_FIXTURES = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"


def _ladder_instr(iid, itype, operands, rung, rung_text=None):
    metadata = {"rung_text": rung_text} if rung_text else {}
    return ControlInstruction(
        id=iid,
        instruction_type=itype,
        operands=list(operands),
        raw_text=f"{itype}({','.join(operands)})",
        language="ladder",
        rung_number=rung,
        metadata=metadata,
    )


def _rels_for_instruction(output, instr_id):
    return [
        r for r in output["relationships"]
        if (r.platform_specific or {}).get("instruction_id") == instr_id
    ]


# ---------------------------------------------------------------------------
# Part A: NOP / MOVE
# ---------------------------------------------------------------------------


class NopMoveTests(unittest.TestCase):
    def test_nop_is_registered_and_recognized(self) -> None:
        self.assertIn("NOP", INSTRUCTION_SEMANTICS)
        self.assertTrue(INSTRUCTION_SEMANTICS["NOP"].implemented)

    def test_nop_emits_no_cause_effect_edges(self) -> None:
        instr = _ladder_instr("r0_i0", "NOP", [], rung=0, rung_text="NOP();")
        project = _project_from_instructions([instr], program_tags=[])
        output = normalize_l5x_project(project)
        edges = _rels_for_instruction(output, "r0_i0")
        # No READS / WRITES — NOP touches nothing.
        self.assertEqual(edges, [])
        # The instruction object is still present and marked recognized,
        # so it does not show up in the unsupported inventory.
        inv = output["normalization_metadata"][
            "unsupported_ladder_instruction_inventory"
        ]
        self.assertNotIn("NOP", inv)

    def test_move_mirrors_mov_source_reads_dest_writes(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "MOVE", ["Src", "Dest"], rung=0,
            rung_text="MOVE(Src,Dest);",
        )
        project = _project_from_instructions(
            [instr], program_tags=["Src", "Dest"]
        )
        output = normalize_l5x_project(project)
        edges = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in edges if r.relationship_type == RelationshipType.READS]
        writes = [r for r in edges if r.relationship_type == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].write_behavior, WriteBehaviorType.MOVES_VALUE)


# ---------------------------------------------------------------------------
# Part B: AOI instance resolution (constructed project, no I/O)
# ---------------------------------------------------------------------------


def _project_from_instructions(instructions, program_tags, controller_tags=None):
    routine = ControlRoutine(
        name="R",
        language="ladder",
        instructions=instructions,
        raw_logic="\n".join(
            (i.metadata or {}).get("rung_text", "") for i in instructions
        ),
        parse_status="parsed",
        metadata={"rockwell_type": "RLL"},
    )
    program = ControlProgram(
        name="PRG",
        tags=[
            ControlTag(name=n, data_type="BOOL", scope="PRG",
                       platform_source="rockwell_l5x")
            for n in program_tags
        ],
        routines=[routine],
    )
    return ControlProject(
        project_name="PLC",
        controllers=[
            ControlController(
                name="PLC",
                platform="rockwell",
                controller_tags=[
                    ControlTag(name=n, data_type="BOOL", scope="controller",
                               platform_source="rockwell_l5x")
                    for n in (controller_tags or [])
                ],
                programs=[program],
            )
        ],
    )


class AoiInstanceResolutionTests(unittest.TestCase):
    def _build(self):
        aoi = AddOnInstructionDef(
            name="Pump",
            logic_routine="Logic",
            parameters=[
                AOIParameter(name="EnableIn", usage="Input", required=False, visible=False),
                AOIParameter(name="EnableOut", usage="Output", required=False, visible=False),
                AOIParameter(name="Start", usage="Input", required=True),
                AOIParameter(name="FaultRef", usage="InOut", required=True),
                AOIParameter(name="Run", usage="Output", required=True),
            ],
        )
        call = _ladder_instr(
            "r0_i0", "PUMP",
            ["Pump_01", "Start_PB", "FaultWord", "Run_Out"],
            rung=0,
            rung_text="PUMP(Pump_01,Start_PB,FaultWord,Run_Out);",
        )
        project = _project_from_instructions(
            [call],
            program_tags=["Pump_01", "Start_PB", "FaultWord", "Run_Out"],
        )
        project.controllers[0].add_on_instruction_defs = [aoi]
        return normalize_l5x_project(project)

    def test_aoi_call_becomes_function_block(self) -> None:
        output = self._build()
        fb = [
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.FUNCTION_BLOCK
        ]
        self.assertEqual(len(fb), 1)
        self.assertEqual(fb[0].attributes.get("aoi_name"), "Pump")
        self.assertTrue(fb[0].attributes.get("is_aoi_instance"))
        self.assertEqual(fb[0].attributes.get("aoi_binding_basis"), "required_params")

    def test_input_output_inout_directions(self) -> None:
        output = self._build()
        edges = _rels_for_instruction(output, "r0_i0")
        by_param = {}
        for r in edges:
            pname = (r.platform_specific or {}).get("parameter_name")
            by_param.setdefault(pname, []).append(r.relationship_type)
        # Input -> READS
        self.assertEqual(by_param.get("Start"), [RelationshipType.READS])
        # Output -> WRITES
        self.assertEqual(by_param.get("Run"), [RelationshipType.WRITES])
        # InOut -> READS + WRITES
        self.assertEqual(
            sorted(t.value for t in by_param.get("FaultRef")),
            ["reads", "writes"],
        )

    def test_backing_tag_is_referenced(self) -> None:
        output = self._build()
        edges = _rels_for_instruction(output, "r0_i0")
        backing = [
            r for r in edges
            if (r.platform_specific or {}).get("aoi_role") == "backing_tag"
        ]
        self.assertEqual(len(backing), 1)
        self.assertEqual(backing[0].relationship_type, RelationshipType.REFERENCES)

    def test_calls_into_body_routine(self) -> None:
        output = self._build()
        calls = [
            r for r in _rels_for_instruction(output, "r0_i0")
            if r.relationship_type == RelationshipType.CALLS
        ]
        self.assertEqual(len(calls), 1)
        body_objs = [
            o for o in output["control_objects"]
            if o.id == calls[0].target_id
        ]
        self.assertTrue(body_objs and body_objs[0].attributes.get("is_aoi_body"))

    def test_operand_count_mismatch_is_flagged_not_guessed(self) -> None:
        aoi = AddOnInstructionDef(
            name="Pump",
            parameters=[
                AOIParameter(name="EnableIn", usage="Input", required=False, visible=False),
                AOIParameter(name="Start", usage="Input", required=True),
                AOIParameter(name="Run", usage="Output", required=True),
            ],
        )
        # 3 args after backing, but only 2 required params and 2 total
        # call params -> mismatch -> conservative.
        call = _ladder_instr(
            "r0_i0", "PUMP",
            ["Pump_01", "A", "B", "C"], rung=0,
            rung_text="PUMP(Pump_01,A,B,C);",
        )
        project = _project_from_instructions(
            [call], program_tags=["Pump_01", "A", "B", "C"]
        )
        project.controllers[0].add_on_instruction_defs = [aoi]
        output = normalize_l5x_project(project)
        fb = [
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.FUNCTION_BLOCK
        ][0]
        self.assertEqual(
            fb.platform_specific.get("aoi_binding_status"),
            "operand_count_mismatch",
        )
        # Only the backing reference is emitted; no guessed param edges.
        param_edges = [
            r for r in _rels_for_instruction(output, "r0_i0")
            if (r.platform_specific or {}).get("gating_kind") == "aoi_parameter"
        ]
        self.assertEqual(param_edges, [])


# ---------------------------------------------------------------------------
# Part B: end-to-end against the synthetic fixture
# ---------------------------------------------------------------------------


class SyntheticFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = (_FIXTURES / "synthetic_aoi_udt_system.L5X").read_bytes()
        proj = RockwellL5XConnector().parse("synthetic_aoi_udt_system.L5X", raw)
        cls.proj = proj
        cls.output = normalize_l5x_project(proj)
        cls.objs = {o.id: o for o in cls.output["control_objects"]}

    def test_connector_exposes_aoi_udt_alias(self) -> None:
        c = self.proj.controllers[0]
        self.assertTrue(any(d.name == "MotorStarter" for d in c.add_on_instruction_defs))
        self.assertTrue(any(d.name == "Valve_UDT" for d in c.data_type_defs))
        self.assertTrue(any(t.alias_for == "HMI_Start" for t in c.controller_tags))

    def test_motorstarter_resolves_directions(self) -> None:
        # Rung 6: MotorStarter(Agitator_Motor,Run_Cmd,Stop_PB,Permissive_OK,
        #                      Pump_Transfer.Overload,Pump_Transfer.Run_Cmd)
        fb = [
            o for o in self.output["control_objects"]
            if o.object_type == ControlObjectType.FUNCTION_BLOCK
            and o.attributes.get("aoi_name") == "MotorStarter"
        ]
        self.assertEqual(len(fb), 1)
        edges = [
            r for r in self.output["relationships"]
            if r.source_id == fb[0].id
        ]
        # The Output parameter Run writes Pump_Transfer.Run_Cmd.
        writes = [
            r for r in edges
            if r.relationship_type == RelationshipType.WRITES
            and (r.platform_specific or {}).get("parameter_name") == "Run"
        ]
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.objs[writes[0].target_id].name, "Pump_Transfer.Run_Cmd")
        # Five Input params are read.
        reads = [
            r for r in edges
            if r.relationship_type == RelationshipType.READS
            and (r.platform_specific or {}).get("gating_kind") == "aoi_parameter"
        ]
        self.assertEqual(len(reads), 4)  # Start, Stop, Permissive, Overload

    def test_alias_reference_edge(self) -> None:
        alias_edges = [
            r for r in self.output["relationships"]
            if r.relationship_type == RelationshipType.REFERENCES
            and (r.platform_specific or {}).get("reference_kind") == "alias"
        ]
        pairs = {
            (self.objs[r.source_id].name, self.objs[r.target_id].name)
            for r in alias_edges
        }
        self.assertIn(("Start_PB", "HMI_Start"), pairs)

    def test_udt_member_metadata(self) -> None:
        hits = [
            r for r in self.output["relationships"]
            if (r.platform_specific or {}).get("udt_member") == "Cmd"
            and (r.platform_specific or {}).get("udt_type") == "Valve_UDT"
        ]
        self.assertTrue(hits)
        self.assertEqual(hits[0].platform_specific.get("udt_member_type"), "BOOL")

    def test_motorstarter_ladder_body_normalized(self) -> None:
        """Ladder AOI body emits rung-level READS/WRITES under the body routine."""
        body_objs = [
            o for o in self.output["control_objects"]
            if o.attributes.get("is_aoi_body")
            and o.attributes.get("aoi_name") == "MotorStarter"
        ]
        self.assertEqual(len(body_objs), 1)
        body = body_objs[0]
        self.assertEqual(
            (body.platform_specific or {}).get("parse_status"), "ok"
        )
        self.assertTrue(
            (body.platform_specific or {}).get("aoi_body_normalized")
        )
        rungs = [
            o for o in self.output["control_objects"]
            if o.parent_ids and body.id in o.parent_ids
            and o.object_type.value == "rung"
        ]
        self.assertGreaterEqual(len(rungs), 1)
        body_writes = [
            r for r in self.output["relationships"]
            if r.relationship_type == RelationshipType.WRITES
            and r.execution_context_id
            and "MotorStarter" in r.execution_context_id
            and "Logic" in r.execution_context_id
        ]
        self.assertGreaterEqual(len(body_writes), 1)

    def test_st_aoi_body_stays_stub(self) -> None:
        """ST-bodied AOI definitions remain ``aoi_body_not_extracted``."""
        raw = (_FIXTURES / "Array_Scroll.L5X").read_bytes()
        proj = RockwellL5XConnector().parse("Array_Scroll.L5X", raw)
        output = normalize_l5x_project(proj)
        bodies = [
            o for o in output["control_objects"]
            if o.attributes.get("is_aoi_body")
        ]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertEqual(
                (body.platform_specific or {}).get("parse_status"),
                "aoi_body_not_extracted",
            )


# ---------------------------------------------------------------------------
# Part B: real logixlib AOI fixtures resolve instead of staying unknown
# ---------------------------------------------------------------------------


class ExtLibAoiTests(unittest.TestCase):
    def _function_block_aoi_names(self, fixture_name):
        raw = (_FIXTURES / fixture_name).read_bytes()
        proj = RockwellL5XConnector().parse(fixture_name, raw)
        output = normalize_l5x_project(proj)
        return {
            (o.attributes.get("aoi_name") or "").upper()
            for o in output["control_objects"]
            if o.object_type == ControlObjectType.FUNCTION_BLOCK
            and o.attributes.get("is_aoi_instance")
        }

    def test_pf525_aois_resolve_to_function_blocks(self) -> None:
        names = self._function_block_aoi_names("ext_logixlib_pf525_program.L5X")
        for expected in ("DVC_PF525", "OP_PERMISSIVE", "SYS_ALARM"):
            self.assertIn(expected, names)

    def test_packml_op_permissive_resolves(self) -> None:
        names = self._function_block_aoi_names(
            "ext_logixlib_packml_state_program.L5X"
        )
        self.assertIn("OP_PERMISSIVE", names)


if __name__ == "__main__":
    unittest.main()
