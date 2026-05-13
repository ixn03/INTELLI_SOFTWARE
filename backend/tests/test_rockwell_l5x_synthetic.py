"""Rockwell L5X connector tests using inline synthetic XML only.

Real customer or example-studio L5X exports must not be committed to this
repository; keep them outside git. For local-only regression against a
specific export, developers may read a path from an environment variable
and parse it in a throwaway script or optional test marked ``@unittest.skip``.
"""

from __future__ import annotations

import unittest

from app.connectors.rockwell_l5x import RockwellL5XConnector
from app.parsers.st_comments import strip_st_comments_for_parsing
from app.parsers.structured_text_blocks import (
    STCaseBlock,
    STIfBlock,
    parse_structured_text_blocks,
)

# Minimal RSLogix5000Content: ladder parallel branch (BST/NXB/BND), ST with
# ``(* *)`` and ``//`` comments (preserved on raw_logic), routine type
# aliases (RLL / ladder), and an unsupported routine type.
_SYNTH_L5X = b"""<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" TargetName="SynthCtrl" TargetType="Controller">
  <Controller Name="SynthCtrl">
    <Tags/>
    <Programs>
      <Program Name="PRG1">
        <Tags>
          <Tag Name="InA" DataType="BOOL" TagType="Base"/>
        </Tags>
        <Routines>
          <Routine Name="RLadder" Type="RLL">
            <RLLContent>
              <Rung Number="0"><Text><![CDATA[XIC(InA) XIO(OperandRungOnly) OTE(OutZ);]]></Text></Rung>
              <Rung Number="1"><Text><![CDATA[XIC(A) BST XIC(B) NXB XIC(C) BND OTE(D);]]></Text></Rung>
            </RLLContent>
          </Routine>
          <Routine Name="AliasLadder" Type="ladder">
            <Rung Number="0"><Text><![CDATA[XIC(InA) OTE(OutL);]]></Text></Rung>
          </Routine>
          <Routine Name="StMain" Type="ST">
            <STContent>
              <Line Number="0"><![CDATA[OutPre := InA; (* trailing block *)]]></Line>
              <Line Number="1"><![CDATA[IF InA THEN]]></Line>
              <Line Number="2"><![CDATA[    OutQ := TRUE; // line cmt]]></Line>
              <Line Number="3"><![CDATA[ELSE]]></Line>
              <Line Number="4"><![CDATA[    OutQ := OperandStOnly;]]></Line>
              <Line Number="5"><![CDATA[END_IF;]]></Line>
              <Line Number="6"><![CDATA[CASE Sel OF]]></Line>
              <Line Number="7"><![CDATA[    1: BranchTag := 1;]]></Line>
              <Line Number="8"><![CDATA[    ELSE: BranchTag := 0;]]></Line>
              <Line Number="9"><![CDATA[END_CASE;]]></Line>
            </STContent>
          </Routine>
          <Routine Name="Unsupported" Type="SFC"/>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>"""


class TestRockwellL5XSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._project = RockwellL5XConnector().parse("synth.L5X", _SYNTH_L5X)

    def test_connector_sniff(self) -> None:
        c = RockwellL5XConnector()
        self.assertGreater(c.can_parse("x.l5x", b"").confidence, 0)
        self.assertGreater(
            c.can_parse("x.xml", b"<RSLogix5000Content/>").confidence,
            0,
        )

    def test_parse_structure(self) -> None:
        p = self._project
        self.assertEqual(p.project_name, "SynthCtrl")
        ctrl = p.controllers[0]
        self.assertEqual(len(ctrl.programs), 1)
        prog = ctrl.programs[0]
        self.assertEqual(prog.name, "PRG1")
        names = {r.name: r for r in prog.routines}
        self.assertEqual(set(names), {"RLadder", "AliasLadder", "StMain", "Unsupported"})

    def test_ladder_parallel_branch(self) -> None:
        prog = self._project.controllers[0].programs[0]
        lad = next(r for r in prog.routines if r.name == "RLadder")
        self.assertEqual(lad.language, "ladder")
        self.assertEqual(lad.metadata.get("rockwell_type"), "RLL")
        types = {ins.instruction_type for ins in lad.instructions}
        self.assertTrue({"BST", "NXB", "BND"}.issubset(types))
        self.assertTrue(any(ins.rung_number == 1 for ins in lad.instructions))

    def test_ladder_type_alias(self) -> None:
        prog = self._project.controllers[0].programs[0]
        lad = next(r for r in prog.routines if r.name == "AliasLadder")
        self.assertEqual(lad.language, "ladder")
        self.assertEqual(lad.metadata.get("rockwell_type"), "ladder")

    def test_st_raw_preserves_comments(self) -> None:
        prog = self._project.controllers[0].programs[0]
        st = next(r for r in prog.routines if r.name == "StMain")
        self.assertEqual(st.language, "structured_text")
        raw = st.raw_logic or ""
        self.assertIn("(*", raw)
        self.assertIn("//", raw)

    def test_st_blocks_after_comment_strip(self) -> None:
        prog = self._project.controllers[0].programs[0]
        st = next(r for r in prog.routines if r.name == "StMain")
        cleaned = strip_st_comments_for_parsing(st.raw_logic or "")
        blocks = parse_structured_text_blocks(cleaned)
        self.assertTrue(any(type(b) is STIfBlock for b in blocks))
        self.assertTrue(any(type(b) is STCaseBlock for b in blocks))

    def test_unknown_routine_type(self) -> None:
        prog = self._project.controllers[0].programs[0]
        unk = next(r for r in prog.routines if r.name == "Unsupported")
        self.assertEqual(unk.language, "unknown")
        self.assertEqual(unk.metadata.get("rockwell_type"), "SFC")

    def test_discovered_operands(self) -> None:
        prog = self._project.controllers[0].programs[0]
        tag_names = {t.name for t in prog.tags}
        self.assertIn("OperandRungOnly", tag_names)
        self.assertIn("OperandStOnly", tag_names)
        self.assertIn("InA", tag_names)


if __name__ == "__main__":
    unittest.main()
