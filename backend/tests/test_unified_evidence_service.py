"""Synthetic tests for the unified evidence aggregation layer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.normalization_service import normalize_l5x_project  # noqa: E402
from app.services.troubleshooting_workspace_service import build_signal_workspace  # noqa: E402
from app.services.unified_evidence_service import (  # noqa: E402
    get_readers,
    get_signal_evidence,
    get_writers,
    trace_downstream,
    trace_upstream,
)


def _tag(name: str) -> ControlObject:
    return ControlObject(
        id=f"tag::Synth/PRG_A/{name}",
        name=name,
        object_type=ControlObjectType.TAG,
        source_location=f"Controller:Synth/Program:PRG_A/Tag:{name}",
    )


def _rung(idx: int, routine: str = "Routine_A") -> ControlObject:
    return ControlObject(
        id=f"rung::Synth/PRG_A/{routine}/{idx}",
        name=f"Rung {idx}",
        object_type=ControlObjectType.RUNG,
        source_location=(
            f"Controller:Synth/Program:PRG_A/Routine:{routine}/Rung[{idx}]"
        ),
    )


def _fbd_pin(
    name: str,
    direction: str,
    block_id: str,
    block_name: str = "Block_A",
) -> ControlObject:
    return ControlObject(
        id=f"pin::Synth/PRG_A/{block_id}/{name}",
        name=name,
        object_type=ControlObjectType.FUNCTION_BLOCK_PIN,
        source_location=(
            f"Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:{block_id}/Pin:{name}"
        ),
        attributes={"direction": direction, "block_id": f"block::Synth/PRG_A/{block_id}"},
    )


def _fbd_block(block_id: str, block_type: str = "CALC") -> ControlObject:
    return ControlObject(
        id=f"block::Synth/PRG_A/{block_id}",
        name=block_id,
        object_type=ControlObjectType.FUNCTION_BLOCK,
        source_location=f"Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:{block_id}",
        attributes={"block_type": block_type},
        platform_specific={"language": "fbd"},
    )


def _read(source: str, target: str, idx: int, *, language: str = "ladder") -> Relationship:
    return Relationship(
        id=f"rel::read::{source}::{target}::{idx}",
        source_id=source,
        target_id=f"tag::Synth/PRG_A/{target}",
        relationship_type=RelationshipType.READS,
        source_location=f"Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
        platform_specific={"instruction_type": "XIC", "language": language},
    )


def _write(source: str, target: str, idx: int, *, language: str = "ladder") -> Relationship:
    return Relationship(
        id=f"rel::write::{source}::{target}::{idx}",
        source_id=source,
        target_id=f"tag::Synth/PRG_A/{target}",
        relationship_type=RelationshipType.WRITES,
        write_behavior=WriteBehaviorType.SETS_TRUE,
        source_location=f"Controller:Synth/Program:PRG_A/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
        platform_specific={"instruction_type": "OTE", "language": language},
    )


_SYNTH_FBD = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Programs>
      <Program Name="PRG_A">
        <Routines>
          <Routine Name="FBD_Main" Type="FBD">
            <FBDContent>
              <Sheet Number="0">
                <Block ID="1" BlockType="CALC" Name="Calc_A">
                  <Input Name="In" Tag="Permissive_A"/>
                  <Output Name="Out" Tag="Output_B"/>
                </Block>
              </Sheet>
            </FBDContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


class UnifiedEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ladder_objects = [
            _tag("Permissive_A"),
            _tag("Output_A"),
            _tag("Output_B"),
            _rung(1),
            _rung(2),
        ]
        self.ladder_relationships = [
            _read("rung::Synth/PRG_A/Routine_A/1", "Permissive_A", 1),
            _write("rung::Synth/PRG_A/Routine_A/1", "Output_A", 1),
            _read("rung::Synth/PRG_A/Routine_A/2", "Output_A", 2),
            _write("rung::Synth/PRG_A/Routine_A/2", "Output_B", 2),
        ]

    def test_ladder_only_writer(self) -> None:
        evidence = get_signal_evidence(
            "tag::Synth/PRG_A/Output_A",
            self.ladder_objects,
            self.ladder_relationships,
        )
        self.assertEqual(len(evidence.who_writes_this_signal), 1)
        writer = evidence.who_writes_this_signal[0]
        self.assertEqual(writer.writer_type, "ladder_rung")
        self.assertEqual(writer.source_provenance.originating_language, "ladder")
        self.assertEqual(writer.source_provenance.rung_number, 1)
        self.assertGreaterEqual(evidence.evidence_sources.ladder, 2)

    def test_multiple_writers(self) -> None:
        evidence = get_signal_evidence(
            "tag::Synth/PRG_A/Output_B",
            self.ladder_objects,
            self.ladder_relationships,
        )
        self.assertEqual(len(evidence.who_writes_this_signal), 1)
        self.assertIn("written in 1 location", evidence.summary.answer)

    def test_upstream_trace(self) -> None:
        upstream = trace_upstream(
            "tag::Synth/PRG_A/Output_A",
            self.ladder_objects,
            self.ladder_relationships,
        )
        names = {dep.upstream_signal_name for dep in upstream}
        self.assertIn("Permissive_A", names)

    def test_downstream_trace(self) -> None:
        downstream = trace_downstream(
            "tag::Synth/PRG_A/Output_A",
            self.ladder_objects,
            self.ladder_relationships,
        )
        self.assertEqual(len(downstream), 1)
        self.assertEqual(downstream[0].reader_type, "ladder_rung")

    def test_unknown_direction_not_treated_as_cause(self) -> None:
        objects = self.ladder_objects + [
            _fbd_block("Generic_A"),
            _tag("Output_C"),
        ]
        relationships = list(self.ladder_relationships) + [
            Relationship(
                id="rel::unknown::1",
                source_id="block::Synth/PRG_A/Generic_A",
                target_id="tag::Synth/PRG_A/Output_C",
                relationship_type=RelationshipType.REFERENCES,
                source_location="Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:Generic_A",
                confidence=ConfidenceLevel.LOW,
                platform_specific={
                    "language": "fbd",
                    "binding_status": "direction_unknown",
                },
            ),
        ]
        evidence = get_signal_evidence(
            "tag::Synth/PRG_A/Output_C",
            objects,
            relationships,
        )
        self.assertEqual(len(evidence.unknowns), 1)
        self.assertEqual(evidence.unknowns[0].source_provenance.causality, "direction_unknown")
        control_names = {c.signal_name for c in evidence.what_controls_this_signal}
        self.assertNotIn("Generic_A", control_names)

    def test_fbd_only_writer_from_normalized_project(self) -> None:
        project = RockwellL5XConnector().parse("synthetic_fbd.L5X", _SYNTH_FBD)
        normalized = normalize_l5x_project(project)
        output_b = next(
            obj for obj in normalized["control_objects"] if obj.name == "Output_B"
        )
        evidence = get_signal_evidence(
            output_b.id,
            normalized["control_objects"],
            normalized["relationships"],
        )
        self.assertGreaterEqual(len(evidence.who_writes_this_signal), 1)
        languages = {
            w.source_provenance.originating_language
            for w in evidence.who_writes_this_signal
        }
        self.assertIn("fbd", languages)
        self.assertGreaterEqual(evidence.evidence_sources.fbd, 1)

    def test_ladder_and_fbd_evidence_merged(self) -> None:
        objects = self.ladder_objects + [
            _fbd_block("Calc_A"),
            _fbd_pin("Out", "output", "Calc_A"),
            _fbd_pin("In", "input", "Calc_A"),
        ]
        relationships = list(self.ladder_relationships) + [
            _read("pin::Synth/PRG_A/Calc_A/In", "Permissive_A", 1, language="fbd"),
            _write("pin::Synth/PRG_A/Calc_A/Out", "Output_B", 1, language="fbd"),
        ]
        for rel in relationships[-2:]:
            rel.platform_specific = {
                **(rel.platform_specific or {}),
                "language": "fbd",
                "fbd_relation": "block_pin",
            }
            rel.source_location = (
                "Controller:Synth/Program:PRG_A/Routine:FBD_Main/Block:Calc_A"
            )

        evidence = get_signal_evidence(
            "tag::Synth/PRG_A/Output_B",
            objects,
            relationships,
        )
        writer_types = {w.writer_type for w in evidence.who_writes_this_signal}
        self.assertIn("ladder_rung", writer_types)
        self.assertIn("fbd_output_pin", writer_types)
        self.assertGreater(evidence.evidence_sources.ladder, 0)
        self.assertGreater(evidence.evidence_sources.fbd, 0)

    def test_get_writers_and_readers_helpers(self) -> None:
        writers = get_writers(
            "tag::Synth/PRG_A/Output_A",
            self.ladder_objects,
            self.ladder_relationships,
        )
        readers = get_readers(
            "tag::Synth/PRG_A/Output_A",
            self.ladder_objects,
            self.ladder_relationships,
        )
        self.assertEqual(len(writers), 1)
        self.assertEqual(len(readers), 1)

    def test_workspace_includes_unified_evidence(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Output_A not energizing?",
            control_objects=self.ladder_objects,
            relationships=self.ladder_relationships,
        )
        self.assertIsNotNone(workspace.unified_evidence)
        assert workspace.unified_evidence is not None
        self.assertEqual(workspace.unified_evidence.target_signal_name, "Output_A")
        self.assertGreater(len(workspace.unified_evidence.verification.ladder), 0)
        self.assertGreater(len(workspace.what_controls_this_signal.logic_paths), 0)
        self.assertIn("logic path", workspace.deterministic_explanation.lower())
        self.assertNotEqual(
            workspace.deterministic_explanation,
            workspace.unified_evidence.summary.answer,
        )


if __name__ == "__main__":
    unittest.main()
