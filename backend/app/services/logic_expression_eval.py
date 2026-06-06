"""Evaluate :class:`LogicExpression` trees for trace / troubleshooting."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.reasoning import LogicExpression, LogicExpressionKind

TagValue = bool | int | float | None

_COMPARISON_VERB: dict[str, str] = {
    "EQU": "must equal",
    "NEQ": "must not equal",
    "GRT": "must be greater than",
    "GEQ": "must be greater than or equal to",
    "LES": "must be less than",
    "LEQ": "must be less than or equal to",
}


@dataclass(frozen=True)
class UnsatisfiedTerm:
    """One leaf or branch that prevents the rung from evaluating true."""

    tag: str | None
    instruction_type: str | None
    required_value: bool | None
    branch_index: int | None
    reason: str
    path: tuple[int, ...] = field(default_factory=tuple)
    display: str = ""


def evaluate_logic_expression(
    expr: LogicExpression | None,
    tag_values: dict[str, TagValue],
) -> tuple[bool | None, list[UnsatisfiedTerm]]:
    """Return ``(result, unsatisfied_terms)``.

    ``result`` is ``None`` when any referenced tag value is unknown.
    """

    if expr is None:
        return None, []
    return _eval_node(expr, tag_values, path=())


def find_blocking_terms(
    expr: LogicExpression | None,
    tag_values: dict[str, TagValue],
) -> list[UnsatisfiedTerm]:
    """Return terms that are known-false or unknown and block the rung."""

    result, terms = evaluate_logic_expression(expr, tag_values)
    if result is True:
        return []
    return terms


def _eval_node(
    expr: LogicExpression,
    tag_values: dict[str, TagValue],
    *,
    path: tuple[int, ...],
) -> tuple[bool | None, list[UnsatisfiedTerm]]:
    kind = expr.kind

    if kind == LogicExpressionKind.AND:
        child_terms: list[UnsatisfiedTerm] = []
        saw_unknown = False
        for idx, child in enumerate(expr.children):
            val, terms = _eval_node(child, tag_values, path=path + (idx,))
            if val is False:
                child_terms.extend(terms)
            elif val is None:
                saw_unknown = True
                child_terms.extend(terms)
            if val is False:
                return False, child_terms
        if saw_unknown:
            return None, child_terms
        return True, []

    if kind == LogicExpressionKind.OR:
        child_terms: list[UnsatisfiedTerm] = []
        saw_unknown = False
        for idx, child in enumerate(expr.children):
            val, terms = _eval_node(child, tag_values, path=path + (idx,))
            if val is True:
                return True, []
            if val is False:
                child_terms.extend(terms)
            else:
                saw_unknown = True
                child_terms.extend(terms)
        if saw_unknown and not child_terms:
            return None, child_terms
        return False, child_terms

    if kind == LogicExpressionKind.NOT:
        if not expr.children:
            return None, [
                UnsatisfiedTerm(
                    tag=None,
                    instruction_type=expr.instruction_type,
                    required_value=None,
                    branch_index=expr.branch_index,
                    reason="unknown",
                    path=path,
                    display="NOT (?)",
                )
            ]
        val, terms = _eval_node(expr.children[0], tag_values, path=path + (0,))
        if val is True:
            return False, terms or [
                UnsatisfiedTerm(
                    tag=expr.children[0].tag,
                    instruction_type=expr.children[0].instruction_type,
                    required_value=False,
                    branch_index=expr.branch_index,
                    reason="false",
                    path=path,
                    display=f"NOT ({expr.children[0].tag or '?'})",
                )
            ]
        if val is False:
            return True, []
        return None, terms

    if kind == LogicExpressionKind.CONSTANT:
        if expr.constant_value is None:
            return None, [
                UnsatisfiedTerm(
                    tag=None,
                    instruction_type=expr.instruction_type,
                    required_value=None,
                    branch_index=expr.branch_index,
                    reason="unknown",
                    path=path,
                    display="constant condition unknown",
                )
            ]
        if expr.constant_value is True:
            return True, []
        return False, [
            UnsatisfiedTerm(
                tag=None,
                instruction_type=expr.instruction_type,
                required_value=True,
                branch_index=expr.branch_index,
                reason="false",
                path=path,
                display="unconditional FALSE",
            )
        ]

    if kind == LogicExpressionKind.CONTACT:
        tag = expr.tag
        required = expr.examined_value
        if not tag or required is None:
            return None, [
                UnsatisfiedTerm(
                    tag=tag,
                    instruction_type=expr.instruction_type,
                    required_value=required,
                    branch_index=expr.branch_index,
                    reason="unknown",
                    path=path,
                    display=expr.raw_text or tag or "?",
                )
            ]
        actual = tag_values.get(tag)
        display = f"{tag} is {'TRUE' if required else 'FALSE'}"
        if actual is None:
            return None, [
                UnsatisfiedTerm(
                    tag=tag,
                    instruction_type=expr.instruction_type,
                    required_value=required,
                    branch_index=expr.branch_index,
                    reason="unknown",
                    path=path,
                    display=display,
                )
            ]
        if bool(actual) == required:
            return True, []
        return False, [
            UnsatisfiedTerm(
                tag=tag,
                instruction_type=expr.instruction_type,
                required_value=required,
                branch_index=expr.branch_index,
                reason="false",
                path=path,
                display=display,
            )
        ]

    if kind == LogicExpressionKind.COMPARE:
        return _eval_compare(expr, tag_values, path=path)

    # INSTRUCTION leaves — no evaluation yet.
    display = expr.raw_text or expr.instruction_type or "?"
    return None, [
        UnsatisfiedTerm(
            tag=expr.tag,
            instruction_type=expr.instruction_type,
            required_value=expr.examined_value,
            branch_index=expr.branch_index,
            reason="unknown",
            path=path,
            display=display,
        )
    ]


def _eval_compare(
    expr: LogicExpression,
    tag_values: dict[str, TagValue],
    *,
    path: tuple[int, ...],
) -> tuple[bool | None, list[UnsatisfiedTerm]]:
    itype = (expr.instruction_type or "").upper()
    operands = list(expr.operands)
    display = _comparison_display(itype, operands) or (
        expr.raw_text or expr.instruction_type or "?"
    )
    common = {
        "instruction_type": itype,
        "branch_index": expr.branch_index,
        "path": path,
        "display": display,
    }

    if itype == "LIM":
        if len(operands) < 3:
            return None, [
                UnsatisfiedTerm(
                    tag=None,
                    required_value=None,
                    reason="unknown",
                    **common,
                )
            ]
        low_val = _resolve_operand(operands[0], tag_values)
        test_val = _resolve_operand(operands[1], tag_values)
        high_val = _resolve_operand(operands[2], tag_values)
        if low_val is None or test_val is None or high_val is None:
            return None, [
                UnsatisfiedTerm(
                    tag=operands[1],
                    required_value=None,
                    reason="unknown",
                    **common,
                )
            ]
        low_n, test_n, high_n = float(low_val), float(test_val), float(high_val)
        if low_n <= high_n:
            ok = low_n <= test_n <= high_n
        else:
            ok = high_n <= test_n <= low_n
        if ok:
            return True, []
        return False, [
            UnsatisfiedTerm(
                tag=operands[1],
                required_value=None,
                reason="false",
                **common,
            )
        ]

    if len(operands) < 2:
        return None, [
            UnsatisfiedTerm(
                tag=None,
                required_value=None,
                reason="unknown",
                **common,
            )
        ]

    left_val = _resolve_operand(operands[0], tag_values)
    right_val = _resolve_operand(operands[1], tag_values)
    if left_val is None or right_val is None:
        return None, [
            UnsatisfiedTerm(
                tag=operands[0],
                required_value=None,
                reason="unknown",
                **common,
            )
        ]

    left_n, right_n = float(left_val), float(right_val)
    result = _compare_numeric(itype, left_n, right_n)
    if result is None:
        return None, [
            UnsatisfiedTerm(
                tag=operands[0],
                required_value=None,
                reason="unknown",
                **common,
            )
        ]
    if result:
        return True, []
    return False, [
        UnsatisfiedTerm(
            tag=operands[0],
            required_value=None,
            reason="false",
            **common,
        )
    ]


def _compare_numeric(itype: str, left: float, right: float) -> bool | None:
    if itype == "GEQ":
        return left >= right
    if itype == "GRT":
        return left > right
    if itype == "LES":
        return left < right
    if itype == "LEQ":
        return left <= right
    if itype == "EQU":
        return left == right
    if itype == "NEQ":
        return left != right
    return None


def _resolve_operand(operand: str, tag_values: dict[str, TagValue]) -> TagValue:
    op = operand.strip()
    if not op:
        return None
    numeric = _parse_numeric_literal(op)
    if numeric is not None:
        return numeric
    if op in tag_values:
        val = tag_values[op]
        if isinstance(val, bool):
            return float(val)
        return val
    return None


def _parse_numeric_literal(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    if text.upper() in {"TRUE", "FALSE"}:
        return 1.0 if text.upper() == "TRUE" else 0.0
    try:
        return float(text)
    except ValueError:
        return None


def _comparison_display(itype: str, operands: list[str]) -> str | None:
    if itype == "LIM" and len(operands) >= 3:
        low, test, high = operands[0], operands[1], operands[2]
        return f"{test} must be between {low} and {high}"
    if len(operands) < 2:
        return None
    verb = _COMPARISON_VERB.get(itype)
    if verb is None:
        return None
    return f"{operands[0]} {verb} {operands[1]}"
