"""Tests for :mod:`app.services.structured_text_extraction`.

The extractor is intentionally narrow. These tests pin both the
supported envelope (assignment, IF-THEN, AND/NOT) and the deliberate
non-support (OR, parens, arithmetic, multi-statement bodies) so
regressions surface immediately.
"""

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.structured_text_extraction import (  # noqa: E402
    extract_simple_st_conditions,
)


class LiteralAssignmentTests(unittest.TestCase):
    def test_assigns_true_literal(self) -> None:
        r = extract_simple_st_conditions("Motor_Run := TRUE;")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_target, "Motor_Run")
        self.assertEqual(r.assigned_value, "TRUE")
        self.assertEqual(r.conditions, [])
        self.assertEqual(r.natural_language, "Motor_Run is assigned TRUE.")

    def test_assigns_false_literal_case_insensitive(self) -> None:
        r = extract_simple_st_conditions("Motor_Run := false;")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_value, "FALSE")
        self.assertEqual(r.natural_language, "Motor_Run is assigned FALSE.")

    def test_trailing_semicolon_optional(self) -> None:
        r = extract_simple_st_conditions("Motor_Run := TRUE")
        self.assertIsNotNone(r)


class ConjunctionAssignmentTests(unittest.TestCase):
    def test_and_chain_renders_with_oxford_comma(self) -> None:
        r = extract_simple_st_conditions(
            "Motor_Run := StartPB AND AutoMode AND NOT Faulted;"
        )
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_target, "Motor_Run")
        self.assertEqual(r.assigned_value, "(boolean expression)")
        self.assertEqual(
            [c.tag for c in r.conditions],
            ["StartPB", "AutoMode", "Faulted"],
        )
        self.assertEqual(
            [c.required_value for c in r.conditions],
            [True, True, False],
        )
        self.assertEqual(
            r.natural_language,
            "Motor_Run is assigned TRUE when StartPB is TRUE, "
            "AutoMode is TRUE, and Faulted is FALSE.",
        )

    def test_two_operand_uses_no_oxford_comma(self) -> None:
        r = extract_simple_st_conditions("X := A AND NOT B;")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(
            r.natural_language,
            "X is assigned TRUE when A is TRUE and B is FALSE.",
        )

    def test_single_operand_assignment(self) -> None:
        r = extract_simple_st_conditions("X := A;")
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(
            r.natural_language, "X is assigned TRUE when A is TRUE."
        )


class IfBlockTests(unittest.TestCase):
    def test_if_then_block_with_and_not(self) -> None:
        text = (
            "IF StartPB AND AutoMode AND NOT Faulted THEN\n"
            "    Motor_Run := TRUE;\n"
            "END_IF;"
        )
        r = extract_simple_st_conditions(text)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_target, "Motor_Run")
        self.assertEqual(r.assigned_value, "TRUE")
        self.assertEqual(
            r.natural_language,
            "Motor_Run is assigned TRUE when StartPB is TRUE, "
            "AutoMode is TRUE, and Faulted is FALSE.",
        )

    def test_if_then_with_false_assignment(self) -> None:
        text = (
            "IF Faulted THEN\n"
            "    Motor_Run := FALSE;\n"
            "END_IF"
        )
        r = extract_simple_st_conditions(text)
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_value, "FALSE")
        self.assertEqual(
            r.natural_language,
            "Motor_Run is assigned FALSE when Faulted is TRUE.",
        )

    def test_if_then_with_non_literal_rhs_rejected(self) -> None:
        # IF cond THEN tag := <expression> isn't part of the supported
        # envelope; we want a graceful "too complex" upstream.
        text = (
            "IF StartPB THEN Motor_Run := AnotherTag; END_IF;"
        )
        self.assertIsNone(extract_simple_st_conditions(text))


class UnsupportedPatternsTests(unittest.TestCase):
    """Cases that must return None so Trace v2 emits its canonical
    'too complex' message instead of fabricating a sentence."""

    def test_or_is_rejected(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions("X := A OR B;")
        )

    def test_parentheses_rejected(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions("X := (A AND B);")
        )

    def test_comparison_rejected(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions("X := A >= 5;")
        )

    def test_arithmetic_rejected(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions("X := A + B;")
        )

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(extract_simple_st_conditions(""))
        self.assertIsNone(extract_simple_st_conditions("   "))
        self.assertIsNone(extract_simple_st_conditions(None))

    def test_garbage_text_returns_none(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions("this is not ST code at all")
        )

    def test_if_without_end_if_rejected(self) -> None:
        self.assertIsNone(
            extract_simple_st_conditions(
                "IF X THEN Y := TRUE;"
            )
        )

    def test_dotted_identifier_accepted(self) -> None:
        r = extract_simple_st_conditions(
            "Pump_01.Run := AutoMode AND NOT Pump_01.Fault;"
        )
        self.assertIsNotNone(r)
        assert r is not None
        self.assertEqual(r.assigned_target, "Pump_01.Run")
        self.assertEqual(
            [c.tag for c in r.conditions],
            ["AutoMode", "Pump_01.Fault"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
