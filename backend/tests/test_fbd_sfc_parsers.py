"""Tests for FBD/SFC L5X parsers using original synthetic XML only."""

from __future__ import annotations

import unittest

from app.connectors.rockwell_l5x import RockwellL5XConnector
from app.models.reasoning import ControlObjectType, RelationshipType
from app.services.normalization_service import normalize_l5x_project

_SYNTH_FBD_SFC_L5X = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="Synth" TargetType="Controller">
  <Controller Name="Synth">
    <Tags/>
    <Programs>
      <Program Name="PRG1">
        <Tags/>
        <Routines>
          <Routine Name="FBD_Main" Type="FBD">
            <FBDContent>
              <Sheet Number="0" Name="Sheet1">
                <Block ID="1" BlockType="TON" Operand="Tmr1">
                  <Parameter Name="IN" Value="StartCmd"/>
                  <Parameter Name="PRE" Value="5000"/>
                </Block>
                <IRef ID="2" Operand="InputTag"/>
                <ORef ID="3" Operand="OutputTag"/>
                <Wire FromID="2" ToID="1" FromParam="" ToParam="IN"/>
              </Sheet>
            </FBDContent>
          </Routine>
          <Routine Name="Seq_Main" Type="SFC">
            <SFCContent>
              <Step ID="0" Name="Init" Initial="true" NextStep="1"/>
              <Transition ID="1" Name="T0" TargetStep="1">
                <Condition>StartPB</Condition>
              </Transition>
              <Step ID="1" Name="Running"/>
              <Action ID="2" Name="RunMotor" StepID="1" Qualifier="N"/>
            </SFCContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


class TestFbdSfcParsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._project = RockwellL5XConnector().parse("synth_fbd_sfc.L5X", _SYNTH_FBD_SFC_L5X)

    def test_fbd_routine_parsed(self) -> None:
        prog = self._project.controllers[0].programs[0]
        fbd = next(r for r in prog.routines if r.name == "FBD_Main")
        self.assertEqual(fbd.language, "function_block")
        self.assertEqual(fbd.parse_status, "parsed")
        self.assertGreater(len(fbd.instructions), 0)
        subtypes = {(i.metadata or {}).get("object_subtype") for i in fbd.instructions}
        self.assertIn("function_block", subtypes)

    def test_sfc_routine_parsed(self) -> None:
        prog = self._project.controllers[0].programs[0]
        sfc = next(r for r in prog.routines if r.name == "Seq_Main")
        self.assertEqual(sfc.language, "sfc")
        self.assertEqual(sfc.parse_status, "parsed")
        subtypes = {(i.metadata or {}).get("object_subtype") for i in sfc.instructions}
        self.assertIn("sfc_step", subtypes)
        self.assertIn("sfc_transition", subtypes)
        self.assertIn("sfc_action", subtypes)

    def test_sfc_normalization_graph(self) -> None:
        out = normalize_l5x_project(self._project)
        types = {o.object_type for o in out["control_objects"]}
        self.assertIn(ControlObjectType.SFC_STEP, types)
        self.assertIn(ControlObjectType.SFC_TRANSITION, types)
        self.assertIn(ControlObjectType.SFC_ACTION, types)
        rel_types = {r.relationship_type for r in out["relationships"]}
        self.assertIn(RelationshipType.SEQUENCES, rel_types)
        self.assertIn(RelationshipType.CONDITION_FOR, rel_types)
        self.assertIn(RelationshipType.ACTION_OF, rel_types)

    def test_fbd_discovered_tags(self) -> None:
        prog = self._project.controllers[0].programs[0]
        tag_names = {t.name for t in prog.tags}
        self.assertIn("StartCmd", tag_names)
        self.assertIn("InputTag", tag_names)


if __name__ == "__main__":
    unittest.main()
