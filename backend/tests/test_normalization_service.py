"""Unit tests for ``app.services.normalization_service``.

These tests verify the *behaviour* of the deterministic L5X normalizer
without exercising the L5X parser itself: we hand-build a synthetic
``ControlProject`` covering every instruction we care about, run it
through ``normalize_l5x_project``, and assert on the emitted
``ControlObject`` / ``Relationship`` / ``ExecutionContext`` lists.

Run with::

    python -m unittest discover -s backend/tests
    # or, from the backend/ directory:
    python -m unittest tests.test_normalization_service

Or directly::

    python backend/tests/test_normalization_service.py

No external test dependencies; uses the standard library's
``unittest`` module so the existing requirements.txt is unchanged.
"""

import sys
import unittest
from pathlib import Path

# Make ``app.*`` importable regardless of the current working directory.
# The tests live in backend/tests/, so the backend root is the parent
# of this file's parent.
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
from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObjectType,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)


def _ladder_instr(
    iid: str,
    itype: str,
    operands: list[str],
    rung: int,
    output: str | None = None,
) -> ControlInstruction:
    """Build a ladder ``ControlInstruction`` with sensible defaults."""

    return ControlInstruction(
        id=iid,
        instruction_type=itype,
        operands=list(operands),
        output=output,
        raw_text=f"{itype}({','.join(operands)})",
        language="ladder",
        rung_number=rung,
    )


class NormalizationServiceTests(unittest.TestCase):
    """End-to-end behaviour tests for ``normalize_l5x_project``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = cls._make_project()
        cls.output = normalize_l5x_project(cls.project)
        cls.control_objects = cls.output["control_objects"]
        cls.relationships = cls.output["relationships"]
        cls.execution_contexts = cls.output["execution_contexts"]

    # -- Fixture --------------------------------------------------------

    @staticmethod
    def _make_project() -> ControlProject:
        """A small synthetic project covering every instruction we test.

        Layout::

            PLC01 (controller)
              controller-scope tags: LatchedAlarm
              MainProgram
                tags: Start_PB, EStop, MotorRun, DelayTimer,
                       Foo, Bar, SubOut
                MainRoutine (ladder)
                  Rung 0: XIC(Start_PB) XIO(EStop) OTE(MotorRun)
                  Rung 1: OTL(LatchedAlarm)
                  Rung 2: OTU(LatchedAlarm)
                  Rung 3: TON(DelayTimer, 5000, 0)
                  Rung 4: RES(DelayTimer)
                  Rung 5: JSR(SubRoutine)
                  Rung 6: MyAOI(Foo, Bar)        # unknown instruction
                SubRoutine (ladder)
                  Rung 0: OTE(SubOut)
        """

        main_instructions = [
            _ladder_instr("r0_i0", "XIC", ["Start_PB"], rung=0),
            _ladder_instr("r0_i1", "XIO", ["EStop"], rung=0),
            _ladder_instr("r0_i2", "OTE", ["MotorRun"], rung=0,
                          output="MotorRun"),

            _ladder_instr("r1_i0", "OTL", ["LatchedAlarm"], rung=1,
                          output="LatchedAlarm"),

            _ladder_instr("r2_i0", "OTU", ["LatchedAlarm"], rung=2,
                          output="LatchedAlarm"),

            _ladder_instr("r3_i0", "TON",
                          ["DelayTimer", "5000", "0"], rung=3,
                          output="DelayTimer"),

            _ladder_instr("r4_i0", "RES", ["DelayTimer"], rung=4),

            _ladder_instr("r5_i0", "JSR", ["SubRoutine"], rung=5),

            _ladder_instr("r6_i0", "MyAOI", ["Foo", "Bar"], rung=6),
        ]
        main_routine = ControlRoutine(
            name="MainRoutine",
            language="ladder",
            instructions=main_instructions,
            raw_logic="...",
            metadata={"rockwell_type": "RLL"},
        )

        sub_routine = ControlRoutine(
            name="SubRoutine",
            language="ladder",
            instructions=[
                _ladder_instr("sr0_i0", "OTE", ["SubOut"], rung=0,
                              output="SubOut"),
            ],
            raw_logic="...",
            metadata={"rockwell_type": "RLL"},
        )

        program = ControlProgram(
            name="MainProgram",
            tags=[
                ControlTag(name=name, data_type="BOOL",
                           scope="MainProgram",
                           platform_source="rockwell_l5x")
                for name in [
                    "Start_PB", "EStop", "MotorRun",
                    "DelayTimer", "Foo", "Bar", "SubOut",
                ]
            ],
            routines=[main_routine, sub_routine],
        )
        controller = ControlController(
            name="PLC01",
            platform="rockwell",
            controller_tags=[
                ControlTag(
                    name="LatchedAlarm",
                    data_type="BOOL",
                    scope="controller",
                    platform_source="rockwell_l5x",
                ),
            ],
            programs=[program],
        )
        return ControlProject(
            project_name="PLC01",
            source_file="test.L5X",
            controllers=[controller],
        )

    # -- Helpers --------------------------------------------------------

    def _objects_of(self, object_type: ControlObjectType) -> list:
        return [
            o for o in self.control_objects if o.object_type == object_type
        ]

    def _rels_of(self, rel_type: RelationshipType) -> list:
        return [
            r for r in self.relationships if r.relationship_type == rel_type
        ]

    def _rels_from_instruction(
        self, rel_type: RelationshipType, instruction_type: str,
    ) -> list:
        return [
            r for r in self._rels_of(rel_type)
            if r.platform_specific.get("instruction_type")
            == instruction_type
        ]

    # -- 1. ControlObjects of each core type are emitted ----------------

    def test_emits_controller_program_routine_rung_instruction_tag_objects(
        self,
    ) -> None:
        for object_type in [
            ControlObjectType.CONTROLLER,
            ControlObjectType.PROGRAM,
            ControlObjectType.ROUTINE,
            ControlObjectType.RUNG,
            ControlObjectType.INSTRUCTION,
            ControlObjectType.TAG,
        ]:
            with self.subTest(object_type=object_type.value):
                self.assertGreater(
                    len(self._objects_of(object_type)),
                    0,
                    f"expected at least one {object_type.value} "
                    "ControlObject",
                )

    # -- 2. Structural CONTAINS relationships are emitted ---------------

    def test_emits_structural_contains_relationships(self) -> None:
        contains_pairs = {
            (r.source_id.split("::")[0], r.target_id.split("::")[0])
            for r in self._rels_of(RelationshipType.CONTAINS)
        }
        expected_pairs = {
            ("controller", "program"),
            ("controller", "tag"),
            ("program", "routine"),
            ("program", "tag"),
            ("routine", "rung"),
            ("rung", "instr"),
        }
        missing = expected_pairs - contains_pairs
        self.assertEqual(
            missing,
            set(),
            f"missing CONTAINS pairs: {missing}",
        )

    # -- 3. XIC emits READS with examined_value=True --------------------

    def test_xic_emits_reads_with_examined_value_true(self) -> None:
        xic_reads = self._rels_from_instruction(
            RelationshipType.READS, "XIC"
        )
        self.assertEqual(len(xic_reads), 1)
        rel = xic_reads[0]
        self.assertEqual(
            rel.platform_specific.get("examined_value"), True
        )

    # -- 4. XIO emits READS with examined_value=False -------------------

    def test_xio_emits_reads_with_examined_value_false(self) -> None:
        xio_reads = self._rels_from_instruction(
            RelationshipType.READS, "XIO"
        )
        self.assertEqual(len(xio_reads), 1)
        rel = xio_reads[0]
        self.assertEqual(
            rel.platform_specific.get("examined_value"), False
        )

    # -- 5. OTE emits WRITES with write_behavior=SETS_TRUE --------------

    def test_ote_emits_writes_with_sets_true(self) -> None:
        # Two OTEs in the fixture: MotorRun (main) and SubOut (sub).
        ote_writes = self._rels_from_instruction(
            RelationshipType.WRITES, "OTE"
        )
        self.assertEqual(len(ote_writes), 2)
        for rel in ote_writes:
            with self.subTest(target=rel.target_id):
                self.assertEqual(
                    rel.write_behavior, WriteBehaviorType.SETS_TRUE
                )

    # -- 6. OTL emits WRITES with write_behavior=LATCHES ----------------

    def test_otl_emits_writes_with_latches(self) -> None:
        otl_writes = self._rels_from_instruction(
            RelationshipType.WRITES, "OTL"
        )
        self.assertEqual(len(otl_writes), 1)
        self.assertEqual(
            otl_writes[0].write_behavior, WriteBehaviorType.LATCHES
        )

    # -- 7. OTU emits WRITES with write_behavior=UNLATCHES --------------

    def test_otu_emits_writes_with_unlatches(self) -> None:
        otu_writes = self._rels_from_instruction(
            RelationshipType.WRITES, "OTU"
        )
        self.assertEqual(len(otu_writes), 1)
        self.assertEqual(
            otu_writes[0].write_behavior, WriteBehaviorType.UNLATCHES
        )

    # -- 8. TON emits WRITES with medium confidence ---------------------

    def test_ton_emits_writes_with_medium_confidence(self) -> None:
        ton_writes = self._rels_from_instruction(
            RelationshipType.WRITES, "TON"
        )
        self.assertEqual(len(ton_writes), 1)
        rel = ton_writes[0]
        self.assertEqual(rel.confidence, ConfidenceLevel.MEDIUM)
        # Stateful outputs intentionally carry no write_behavior yet.
        self.assertIsNone(rel.write_behavior)

    # -- 9. RES emits RESETS --------------------------------------------

    def test_res_emits_resets(self) -> None:
        res_resets = self._rels_from_instruction(
            RelationshipType.RESETS, "RES"
        )
        self.assertEqual(len(res_resets), 1)
        target_id = res_resets[0].target_id
        # The RES target should be the existing DelayTimer tag, not a
        # stub (resolution worked).
        self.assertFalse(target_id.endswith("#unresolved"))
        target = next(o for o in self.control_objects if o.id == target_id)
        self.assertEqual(target.object_type, ControlObjectType.TAG)

    # -- 10. JSR emits CALLS to a resolved routine ----------------------

    def test_jsr_emits_calls_to_resolved_routine(self) -> None:
        jsr_calls = self._rels_from_instruction(
            RelationshipType.CALLS, "JSR"
        )
        self.assertEqual(len(jsr_calls), 1)
        rel = jsr_calls[0]
        # Resolved routine -> no #unresolved suffix.
        self.assertFalse(rel.target_id.endswith("#unresolved"))
        target = next(
            o for o in self.control_objects if o.id == rel.target_id
        )
        self.assertEqual(target.object_type, ControlObjectType.ROUTINE)
        self.assertEqual(target.name, "SubRoutine")

    # -- 11. Unknown instruction -> ControlObject but no cause/effect ----

    def test_unknown_instruction_emits_object_but_no_cause_effect(
        self,
    ) -> None:
        # The fixture includes one ``MyAOI`` instruction with no
        # registry entry. It should still appear as an INSTRUCTION
        # ControlObject (so the graph is complete) but must not produce
        # any READS / WRITES / RESETS / CALLS edges.
        my_aoi_objects = [
            o for o in self.control_objects
            if o.object_type == ControlObjectType.INSTRUCTION
            and o.name == "MyAOI"
        ]
        self.assertEqual(len(my_aoi_objects), 1)

        non_containment_edges = [
            r for r in self.relationships
            if r.relationship_type != RelationshipType.CONTAINS
            and r.platform_specific.get("instruction_type") == "MyAOI"
        ]
        self.assertEqual(
            non_containment_edges,
            [],
            "unknown instruction must not emit cause/effect edges",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
