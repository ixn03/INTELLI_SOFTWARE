"""Tests for universal LogicExpression build + evaluation (Phase 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.reasoning import LogicExpressionKind  # noqa: E402
from app.parsers.ladder import parse_ladder_rung_text  # noqa: E402
from app.parsers.ladder_logic import (  # noqa: E402
    build_rung_logic_expression,
    logic_expression_to_text,
)
from app.services.logic_expression_eval import (  # noqa: E402
    evaluate_logic_expression,
    find_blocking_terms,
)


class LogicExpressionBuilderTests(unittest.TestCase):
    def test_series_and(self) -> None:
        rung = "XIC(A) XIC(B) OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, warnings, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved)
        self.assertEqual(warnings, [])
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.AND)
        self.assertEqual(len(expr.children), 2)
        self.assertEqual(expr.children[0].tag, "A")
        self.assertEqual(expr.children[1].tag, "B")

    def test_parallel_or_from_bst(self) -> None:
        rung = "BST XIC(A) NXB XIC(B) BND OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.OR)
        tags = {c.tag for c in expr.children}
        self.assertEqual(tags, {"A", "B"})

    def test_series_then_parallel(self) -> None:
        rung = "XIC(S) BST XIC(A) NXB XIC(B) BND OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.AND)
        self.assertEqual(expr.children[0].tag, "S")
        self.assertEqual(expr.children[1].kind, LogicExpressionKind.OR)

    def test_xio_examined_false(self) -> None:
        rung = "XIO(Fault) OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved)
        assert expr is not None
        self.assertEqual(expr.examined_value, False)

    def test_square_bracket_parallel(self) -> None:
        rung = "XIC(S) [XIC(P1),XIC(P2)] OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=2)
        expr, _, resolved = build_rung_logic_expression(insts)
        self.assertTrue(resolved)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.AND)
        or_node = expr.children[1]
        self.assertEqual(or_node.kind, LogicExpressionKind.OR)

    def test_reparse_from_rung_text_when_markers_missing(self) -> None:
        rung_text = "BST XIC(A) NXB XIC(B) BND OTE(C)"
        insts = [
            parse_ladder_rung_text("XIC(A)", rung_number=0)[0],
            parse_ladder_rung_text("XIC(B)", rung_number=0)[0],
            parse_ladder_rung_text("OTE(C)", rung_number=0)[0],
        ]
        for inst in insts:
            inst.metadata["rung_text"] = rung_text
        expr, _, resolved = build_rung_logic_expression(
            insts, rung_raw_text=rung_text, rung_number=0
        )
        self.assertTrue(resolved)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.OR)

    def test_logic_expression_to_text(self) -> None:
        rung = "XIC(S) BST XIC(A) NXB XIC(B) BND OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        text = logic_expression_to_text(expr)
        self.assertIn("S is TRUE", text or "")
        self.assertIn(" OR ", text or "")


class LogicExpressionEvalTests(unittest.TestCase):
    def test_and_both_true(self) -> None:
        rung = "XIC(A) XIC(B) OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        result, terms = evaluate_logic_expression(
            expr, {"A": True, "B": True}
        )
        self.assertTrue(result)
        self.assertEqual(terms, [])

    def test_and_one_false(self) -> None:
        rung = "XIC(A) XIC(B) OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        result, terms = evaluate_logic_expression(
            expr, {"A": True, "B": False}
        )
        self.assertFalse(result)
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].tag, "B")

    def test_or_one_branch_satisfied(self) -> None:
        rung = "BST XIC(A) NXB XIC(B) BND OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        result, terms = evaluate_logic_expression(
            expr, {"A": False, "B": True}
        )
        self.assertTrue(result)
        self.assertEqual(terms, [])

    def test_or_all_branches_fail(self) -> None:
        rung = "BST XIC(A) NXB XIC(B) BND OTE(C);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        blocking = find_blocking_terms(expr, {"A": False, "B": False})
        tags = {t.tag for t in blocking if t.reason == "false"}
        self.assertEqual(tags, {"A", "B"})

    def test_geq_compare_true_and_false(self) -> None:
        rung = "GEQ(Tank_Level,Target_Level) OTE(Out);"
        insts = parse_ladder_rung_text(rung, rung_number=0)
        expr, _, _ = build_rung_logic_expression(insts)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.COMPARE)
        result, _ = evaluate_logic_expression(
            expr, {"Tank_Level": 90.0, "Target_Level": 80.0}
        )
        self.assertTrue(result)
        result, terms = evaluate_logic_expression(
            expr, {"Tank_Level": 50.0, "Target_Level": 80.0}
        )
        self.assertFalse(result)
        self.assertIn("must be greater than or equal to", terms[0].display)

    def test_parallel_output_only_arm_does_not_block(self) -> None:
        rung = "[XIC(A) OTE(Out1) ,Sys_Alarm(Alm,Sys) ];"
        insts = parse_ladder_rung_text(rung, rung_number=1)
        expr, warnings, resolved = build_rung_logic_expression(
            insts, rung_raw_text=rung, rung_number=1
        )
        self.assertTrue(resolved)
        self.assertNotIn("empty_expression", warnings)
        assert expr is not None
        self.assertEqual(expr.kind, LogicExpressionKind.CONTACT)
        self.assertEqual(expr.tag, "A")


if __name__ == "__main__":
    unittest.main()
