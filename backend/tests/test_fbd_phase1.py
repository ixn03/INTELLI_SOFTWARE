"""Synthetic tests for Rockwell FBD Phase 1 structural extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.control_model import FBDBlock  # noqa: E402
from app.models.reasoning import ControlObjectType, RelationshipType  # noqa: E402
from app.services.dependency_graph_service import (  # noqa: E402
    build_control_dependency_graph,
)
from app.services.logic_ir_validation import (  # noqa: E402
    validate_fbd_blocks_ir,
    validate_normalized_output,
)
from app.services.normalization_service import normalize_l5x_project  # noqa: E402


_SYNTH_FBD_PHASE1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Programs>
      <Program Name="PRG_FBD">
        <Tags/>
        <Routines>
          <Routine Name="FBD_Main" Type="FBD">
            <FBDContent>
              <Sheet Number="0" Name="Sheet_A">
                <Block ID="1" BlockType="CALC" Name="Calc_A">
                  <Input Name="In" Tag="Pump_A"/>
                  <Output Name="Out" Tag="Motor_C"/>
                </Block>
                <Block ID="2" BlockType="FILTER" Name="Filter_A">
                  <Input Name="In"/>
                  <Output Name="Out"/>
                </Block>
                <Block ID="3" BlockType="LIMIT" Name="Limit_A">
                  <Input Name="In"/>
                  <Output Name="Out" Tag="Valve_B"/>
                </Block>
                <Block ID="4" BlockType="UNKNOWN_BLOCK" Name="Generic_A">
                  <Parameter Name="Mystery" Value="Permissive_1"/>
                </Block>
                <Wire FromID="2" ToID="3" FromParam="Out" ToParam="In"/>
              </Sheet>
            </FBDContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


_SYNTH_FBD_MISSING_WIRE = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Programs>
      <Program Name="PRG_FBD">
        <Routines>
          <Routine Name="FBD_Main" Type="FBD">
            <FBDContent>
              <Sheet Number="0">
                <Block ID="1" BlockType="CALC" Name="Calc_A">
                  <Input Name="In"/>
                </Block>
                <Wire FromID="404" ToID="1" FromParam="Out" ToParam="In"/>
              </Sheet>
            </FBDContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


_SYNTH_FBD_VISIBLE_PINS = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Programs>
      <Program Name="PRG_FBD">
        <Routines>
          <Routine Name="FBD_Main" Type="FBD">
            <FBDContent>
              <Sheet Number="0">
                <IRef ID="1" Operand="Pump_A" X="10" Y="20"/>
                <Block ID="2" Type="GENERIC_BLOCK" Operand="Block_A" VisiblePins="In Out Aux" X="30" Y="40"/>
                <ORef ID="3" Operand="Motor_C" X="50" Y="60"/>
                <Wire FromID="1" ToID="2" FromParam="Out" ToParam="In"/>
                <Wire FromID="2" ToID="3" FromParam="Out" ToParam="In"/>
              </Sheet>
            </FBDContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


class FBDPhase1Tests(unittest.TestCase):
    def test_parser_populates_blocks_pins_and_wires(self) -> None:
        project = RockwellL5XConnector().parse("synthetic_fbd.L5X", _SYNTH_FBD_PHASE1)
        routine = project.controllers[0].programs[0].routines[0]

        self.assertEqual(routine.language, "function_block")
        self.assertEqual(routine.parse_status, "parsed")
        self.assertIsNone(routine.raw_logic)
        self.assertEqual(len(routine.fbd_blocks), 4)
        self.assertEqual(validate_fbd_blocks_ir(routine.fbd_blocks), [])

        pin_directions = {
            pin.direction
            for block in routine.fbd_blocks
            for pin in block.pins
        }
        self.assertIn("input", pin_directions)
        self.assertIn("output", pin_directions)
        self.assertIn("unknown", pin_directions)
        self.assertTrue(any(block.wires for block in routine.fbd_blocks))

    def test_normalization_emits_deterministic_fbd_reads_writes(self) -> None:
        project = RockwellL5XConnector().parse("synthetic_fbd.L5X", _SYNTH_FBD_PHASE1)
        out = normalize_l5x_project(project)

        self.assertEqual(validate_normalized_output(out), [])
        object_types = {obj.object_type for obj in out["control_objects"]}
        self.assertIn(ControlObjectType.FUNCTION_BLOCK, object_types)
        self.assertIn(ControlObjectType.FUNCTION_BLOCK_PIN, object_types)

        fbd_rels = [
            rel
            for rel in out["relationships"]
            if (rel.platform_specific or {}).get("language") == "fbd"
        ]
        self.assertTrue(
            any(rel.relationship_type == RelationshipType.READS for rel in fbd_rels)
        )
        self.assertTrue(
            any(rel.relationship_type == RelationshipType.WRITES for rel in fbd_rels)
        )
        self.assertTrue(
            any(
                rel.relationship_type == RelationshipType.SIGNAL_CONNECTS
                for rel in fbd_rels
            )
        )

    def test_unknown_direction_pin_references_without_causality(self) -> None:
        project = RockwellL5XConnector().parse("synthetic_fbd.L5X", _SYNTH_FBD_PHASE1)
        out = normalize_l5x_project(project)
        unknown_refs = [
            rel
            for rel in out["relationships"]
            if rel.relationship_type == RelationshipType.REFERENCES
            and (rel.platform_specific or {}).get("binding_status")
            == "direction_unknown"
        ]

        self.assertTrue(unknown_refs)
        graph = build_control_dependency_graph(out["control_objects"], out["relationships"])
        unknown_ids = {rel.target_id for rel in unknown_refs}
        causal_ids = {
            edge.upstream_tag_id
            for edge in graph.edges
        } | {
            edge.downstream_tag_id
            for edge in graph.edges
        }
        self.assertTrue(unknown_ids.isdisjoint(causal_ids))

    def test_dependency_graph_includes_deterministic_fbd_edges(self) -> None:
        project = RockwellL5XConnector().parse("synthetic_fbd.L5X", _SYNTH_FBD_PHASE1)
        out = normalize_l5x_project(project)
        graph = build_control_dependency_graph(out["control_objects"], out["relationships"])
        edge_names = {
            (edge.upstream_tag_name, edge.downstream_tag_name)
            for edge in graph.edges
        }

        self.assertIn(("Pump_A", "Motor_C"), edge_names)
        self.assertTrue(
            any(
                edge.relationship_type == RelationshipType.SIGNAL_CONNECTS.value
                for edge in graph.edges
            )
        )

    def test_missing_wire_endpoint_validation(self) -> None:
        project = RockwellL5XConnector().parse(
            "synthetic_fbd_missing.L5X",
            _SYNTH_FBD_MISSING_WIRE,
        )
        routine = project.controllers[0].programs[0].routines[0]
        issues = validate_fbd_blocks_ir(routine.fbd_blocks)

        self.assertTrue(
            any(issue.code == "fbd_wire_source_pin_missing" for issue in issues)
        )

    def test_visible_pins_are_preserved_and_wire_directions_upgrade(self) -> None:
        project = RockwellL5XConnector().parse(
            "synthetic_fbd_visible_pins.L5X",
            _SYNTH_FBD_VISIBLE_PINS,
        )
        routine = project.controllers[0].programs[0].routines[0]
        block = next(
            block
            for block in routine.fbd_blocks
            if block.definition_name == "GENERIC_BLOCK"
        )
        pins = {pin.name: pin for pin in block.pins}

        self.assertEqual(block.metadata["visible_pin_count"], 3)
        self.assertEqual(block.metadata["x"], "30")
        self.assertEqual(block.metadata["y"], "40")
        self.assertEqual(pins["In"].direction, "input")
        self.assertEqual(
            pins["In"].metadata["prior_direction_source"],
            "visible_pins_attribute",
        )
        self.assertEqual(pins["Out"].direction, "output")
        self.assertEqual(pins["Aux"].direction, "unknown")

        out = normalize_l5x_project(project)
        graph = build_control_dependency_graph(out["control_objects"], out["relationships"])
        edge_names = {
            (edge.upstream_tag_name, edge.downstream_tag_name)
            for edge in graph.edges
        }
        self.assertIn(("Pump_A", "Out"), edge_names)
        self.assertIn(("Out", "Motor_C"), edge_names)
        self.assertNotIn(("Pump_A", "Motor_C"), edge_names)

    def test_validation_reports_block_without_source_location(self) -> None:
        block = FBDBlock(id="fbd_block::Missing", definition_name="CALC")
        issues = validate_fbd_blocks_ir([block])

        self.assertTrue(
            any(issue.code == "fbd_block_missing_source_location" for issue in issues)
        )


if __name__ == "__main__":
    unittest.main()
