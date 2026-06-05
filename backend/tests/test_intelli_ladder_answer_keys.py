"""Answer-key acceptance tests for INTELLI_Ladder_Test branched rungs 4 and 8.

Keys are controls-engineer confirmed and live in
``fixtures/l5x/INTELLI_Ladder_Test.logic_answer_keys.json``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.reasoning import (  # noqa: E402
    ControlObjectType,
    LogicExpression,
    LogicExpressionKind,
    RelationshipType,
    WriteBehaviorType,
)
from app.parsers.ladder import parse_ladder_rung_text  # noqa: E402
from app.parsers.ladder_logic import build_rung_logic_expression  # noqa: E402
from app.services.logic_expression_eval import (  # noqa: E402
    evaluate_logic_expression,
    find_blocking_terms,
)
from app.services.normalization_service import normalize_l5x_project  # noqa: E402
from app.services.trace_v2_service import trace_object_v2  # noqa: E402

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "l5x"
_ANSWER_KEYS = json.loads(
    (_FIXTURE_DIR / "INTELLI_Ladder_Test.logic_answer_keys.json").read_text(
        encoding="utf-8"
    )
)


def _expr_snapshot(expr: LogicExpression | None) -> dict | None:
    if expr is None:
        return None

    snap: dict = {"kind": expr.kind.value}
    if expr.tag is not None:
        snap["tag"] = expr.tag
    if expr.instruction_type is not None:
        snap["instruction_type"] = expr.instruction_type
    if expr.examined_value is not None:
        snap["examined_value"] = expr.examined_value
    if expr.operands:
        snap["operands"] = list(expr.operands)
    if expr.children:
        snap["children"] = [_expr_snapshot(c) for c in expr.children]
    return snap


class IntelliLadderAnswerKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = _FIXTURE_DIR / "INTELLI_Ladder_Test.L5X"
        if not path.exists():
            raise unittest.SkipTest("INTELLI_Ladder_Test.L5X fixture missing")
        conn = RockwellL5XConnector()
        cls.project = conn.parse(path.name, path.read_bytes())
        cls.norm = normalize_l5x_project(cls.project)
        cls.rungs_by_number = {
            o.attributes["rung_number"]: o
            for o in cls.norm["control_objects"]
            if o.object_type == ControlObjectType.RUNG
        }
        cls.tags_by_name = {
            o.name: o
            for o in cls.norm["control_objects"]
            if o.object_type == ControlObjectType.TAG and o.name
        }

    def _logic_expr_for_rung(self, rung_number: int):
        rung = self.rungs_by_number[rung_number]
        writes = [
            r
            for r in self.norm["relationships"]
            if r.source_id == rung.id
            and r.relationship_type
            in {
                RelationshipType.WRITES,
                RelationshipType.LATCHES,
                RelationshipType.UNLATCHES,
                RelationshipType.RESETS,
            }
            and r.logic_expression is not None
        ]
        self.assertGreater(len(writes), 0)
        self.assertTrue(
            (writes[0].platform_specific or {}).get("logic_expression_resolved")
        )
        return writes[0].logic_expression, rung

    def test_rung4_structure_matches_answer_key(self) -> None:
        key = _ANSWER_KEYS["rungs"]["4"]
        expr, _ = self._logic_expr_for_rung(4)
        self.assertEqual(_expr_snapshot(expr), key["structure"])

    def test_rung8_structure_matches_answer_key(self) -> None:
        key = _ANSWER_KEYS["rungs"]["8"]
        expr, _ = self._logic_expr_for_rung(8)
        self.assertEqual(_expr_snapshot(expr), key["structure"])

    def test_rung4_eval_pass_and_fail(self) -> None:
        key = _ANSWER_KEYS["rungs"]["4"]
        insts = parse_ladder_rung_text(key["rung_text"], rung_number=4)
        expr, _, resolved = build_rung_logic_expression(
            insts, rung_raw_text=key["rung_text"], rung_number=4
        )
        self.assertTrue(resolved)
        pass_result, _ = evaluate_logic_expression(expr, key["eval_pass"])
        self.assertTrue(pass_result)
        fail_result, terms = evaluate_logic_expression(expr, key["eval_fail"])
        self.assertFalse(fail_result)
        displays = {t.display for t in terms if t.reason == "false"}
        for expected in key["trace_fail_unsatisfied"]:
            self.assertIn(expected, displays)

    def test_rung8_eval_pass_and_fail(self) -> None:
        key = _ANSWER_KEYS["rungs"]["8"]
        insts = parse_ladder_rung_text(key["rung_text"], rung_number=8)
        expr, _, resolved = build_rung_logic_expression(
            insts, rung_raw_text=key["rung_text"], rung_number=8
        )
        self.assertTrue(resolved)
        for case_name in ("eval_pass_stop", "eval_pass_high"):
            result, _ = evaluate_logic_expression(expr, key[case_name])
            self.assertTrue(result, msg=case_name)
        fail_result, terms = evaluate_logic_expression(expr, key["eval_fail"])
        self.assertFalse(fail_result)
        displays = {t.display for t in terms if t.reason == "false"}
        for expected in key["trace_fail_unsatisfied"]:
            self.assertIn(expected, displays)

    def _trace_with_states(self, rung_number: int, states: dict) -> list:
        objs = list(self.norm["control_objects"])
        for name, value in states.items():
            tag = self.tags_by_name.get(name)
            if tag is None:
                continue
            updated = tag.model_copy(
                update={"current_state": {"value": value, "quality": "good"}}
            )
            objs = [updated if o.id == tag.id else o for o in objs]
        target_name = _ANSWER_KEYS["rungs"][str(rung_number)]["trace_target"]
        target = self.tags_by_name[target_name]
        return trace_object_v2(target.id, objs, self.norm["relationships"]).conclusions

    def test_rung4_trace_unsatisfied_terms(self) -> None:
        key = _ANSWER_KEYS["rungs"]["4"]
        conclusions = self._trace_with_states(4, key["eval_fail"])
        unsatisfied = [
            c
            for c in conclusions
            if (c.platform_specific or {}).get("trace_v2_kind")
            == "logic_expression_unsatisfied"
        ]
        self.assertEqual(len(unsatisfied), 1)
        for phrase in key["trace_fail_unsatisfied"]:
            self.assertIn(phrase, unsatisfied[0].statement)

    def test_rung8_trace_unsatisfied_terms(self) -> None:
        key = _ANSWER_KEYS["rungs"]["8"]
        conclusions = self._trace_with_states(8, key["eval_fail"])
        unsatisfied = [
            c
            for c in conclusions
            if (c.platform_specific or {}).get("trace_v2_kind")
            == "logic_expression_unsatisfied"
        ]
        self.assertEqual(len(unsatisfied), 1)
        for phrase in key["trace_fail_unsatisfied"]:
            self.assertIn(phrase, unsatisfied[0].statement)


class ComparisonEvalTests(unittest.TestCase):
    def test_all_comparison_instructions(self) -> None:
        cases = [
            ("GEQ", ["A", "B"], {"A": 10.0, "B": 5.0}, True),
            ("GEQ", ["A", "B"], {"A": 5.0, "B": 10.0}, False),
            ("GRT", ["A", "B"], {"A": 10.0, "B": 5.0}, True),
            ("LES", ["A", "B"], {"A": 3.0, "B": 5.0}, True),
            ("LEQ", ["A", "B"], {"A": 5.0, "B": 5.0}, True),
            ("EQU", ["A", "B"], {"A": 7.0, "B": 7.0}, True),
            ("NEQ", ["A", "B"], {"A": 7.0, "B": 8.0}, True),
            ("LIM", ["0", "A", "100"], {"A": 50.0}, True),
            ("LIM", ["100", "A", "0"], {"A": 50.0}, True),
            ("LIM", ["0", "A", "100"], {"A": 150.0}, False),
        ]
        for itype, operands, values, expected in cases:
            with self.subTest(itype=itype, operands=operands):
                expr = LogicExpression(
                    kind=LogicExpressionKind.COMPARE,
                    instruction_type=itype,
                    operands=operands,
                )
                result, _ = evaluate_logic_expression(expr, values)
                self.assertEqual(result, expected)

    def test_compare_unknown_when_operand_missing(self) -> None:
        expr = LogicExpression(
            kind=LogicExpressionKind.COMPARE,
            instruction_type="GEQ",
            operands=["Tank_Level", "Target_Level"],
        )
        result, terms = evaluate_logic_expression(expr, {"Tank_Level": 50.0})
        self.assertIsNone(result)
        self.assertEqual(terms[0].reason, "unknown")
        self.assertIn("must be greater than or equal to", terms[0].display)


class Pf525BranchedRungTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = _FIXTURE_DIR / "ext_logixlib_pf525_program.L5X"
        if not path.exists():
            raise unittest.SkipTest("ext_logixlib_pf525_program.L5X missing")
        from app.connectors.registry import get_connector

        raw = path.read_bytes()
        conn = get_connector(path.name, raw)
        cls.norm = normalize_l5x_project(conn.parse(path.name, raw))

    def test_all_branched_rungs_resolve(self) -> None:
        rungs = [
            o
            for o in self.norm["control_objects"]
            if o.object_type == ControlObjectType.RUNG
            and o.attributes.get("has_branches")
        ]
        self.assertEqual(len(rungs), 5)
        resolved = [
            r for r in rungs if r.attributes.get("logic_expression_resolved")
        ]
        self.assertEqual(len(resolved), 5)


if __name__ == "__main__":
    unittest.main()
