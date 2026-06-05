"""Unit tests for :mod:`app.parsers.ladder`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.parsers.ladder import parse_ladder_rung_text  # noqa: E402


class TestLadderParser(unittest.TestCase):
    def test_branch_level_metadata(self) -> None:
        rung = "BST XIC(A) NXB XIC(B) BND OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        by_type = {i.instruction_type: i for i in insts}
        self.assertEqual(by_type["BST"].metadata.get("branch_level"), 1)
        self.assertEqual(by_type["XIC"].metadata.get("branch_level"), 1)
        self.assertEqual(by_type["NXB"].metadata.get("branch_index"), 1)
        self.assertEqual(by_type["BND"].metadata.get("branch_level"), 0)

    def test_parallel_bracket_recursive_parse(self) -> None:
        rung = "XIC(S) [XIC(P1),XIC(P2)] OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=2)
        types = {i.instruction_type for i in insts}
        self.assertIn("PARALLEL_BRANCH", types)
        self.assertIn("XIC", types)
        self.assertTrue(any(i.metadata.get("parallel_arm") for i in insts))

    def test_instruction_family_math_and_move(self) -> None:
        rung = "ADD(A,B,Dest) MOV(Src,Dst);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        families = {i.instruction_type: i.metadata.get("instruction_family") for i in insts}
        self.assertEqual(families["ADD"], "math")
        self.assertEqual(families["MOV"], "move_copy")

    def test_build_logic_expression_on_parsed_rung(self) -> None:
        from app.parsers.ladder_logic import build_rung_logic_expression

        rung = "BST XIC(A) NXB XIC(B) BND OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=1)
        expr, warnings, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved, warnings)
        self.assertIsNotNone(expr)
        self.assertEqual(expr.kind.value, "or")


if __name__ == "__main__":
    unittest.main()
