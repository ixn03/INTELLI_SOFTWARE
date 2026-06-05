"""Build vendor-neutral :class:`LogicExpression` trees from ladder rungs.

Rockwell ``BST`` / ``NXB`` / ``BND`` markers and square-bracket parallel
notation ``[XIC(A),XIC(B)]`` collapse to ``OR`` nodes; series contacts
collapse to ``AND``. Output instructions (``OTE``, ``OTL``, timers, …)
are excluded from the gating tree.
"""

from __future__ import annotations

from app.models.control_model import ControlInstruction
from app.models.reasoning import LogicExpression, LogicExpressionKind
from app.parsers.ladder import (
    COMPARISON_INSTRUCTIONS,
    CONDITION_INSTRUCTIONS,
    NO_OP_INSTRUCTIONS,
    parse_ladder_rung_text,
)

_BRANCH_MARKERS = frozenset({"BST", "NXB", "BND"})
_ONE_SHOT_INSTRUCTIONS = frozenset({"ONS", "OSR", "OSF"})
_SKIP_FOR_TREE = frozenset({"PARALLEL_BRANCH"})


def build_rung_logic_expression(
    instructions: list[ControlInstruction],
    *,
    rung_raw_text: str | None = None,
    rung_number: int | None = None,
) -> tuple[LogicExpression | None, list[str], bool]:
    """Build a structured gating expression for one rung.

    Returns ``(expression, warnings, resolved)``. When parsing from
    hand-built fixtures that omit branch-marker instructions, falls
    back to re-tokenizing ``rung_raw_text`` if the first pass fails.
    """

    source = instructions
    if rung_raw_text and _text_has_branch_markers(rung_raw_text):
        if not _instructions_have_branch_markers(instructions):
            source = parse_ladder_rung_text(rung_raw_text, rung_number=rung_number)

    expr, warnings = _build_from_instructions(source)
    if expr is None and rung_raw_text and source is instructions:
        reparsed = parse_ladder_rung_text(rung_raw_text, rung_number=rung_number)
        expr, reparsed_warnings = _build_from_instructions(reparsed)
        warnings = list(dict.fromkeys(warnings + reparsed_warnings))
    resolved = expr is not None and not _blocking_warnings(warnings)
    return expr, warnings, resolved


def _text_has_branch_markers(text: str) -> bool:
    upper = text.upper()
    return any(kw in upper for kw in ("BST", "NXB", "BND", "["))


def _instructions_have_branch_markers(
    instructions: list[ControlInstruction],
) -> bool:
    return any(
        inst.instruction_type.upper() in _BRANCH_MARKERS
        or inst.instruction_type.upper() == "PARALLEL_BRANCH"
        for inst in instructions
    )


def logic_expression_to_text(expr: LogicExpression | None) -> str | None:
    """Render a compact human-readable form of a logic tree."""

    if expr is None:
        return None
    return _render(expr)


def _blocking_warnings(warnings: list[str]) -> bool:
    return any(
        w in warnings
        for w in (
            "unclosed_bst",
            "bnd_without_bst",
            "nxb_without_bst",
            "empty_expression",
        )
    )


def _build_from_instructions(
    instructions: list[ControlInstruction],
) -> tuple[LogicExpression | None, list[str]]:
    warnings: list[str] = []
    series: list[LogicExpression] = []
    branch_stack: list[list[list[LogicExpression]]] = []
    skip_parallel_arm = False
    i = 0

    while i < len(instructions):
        inst = instructions[i]
        itype = inst.instruction_type.upper()

        if skip_parallel_arm:
            if (inst.metadata or {}).get("parallel_arm"):
                i += 1
                continue
            skip_parallel_arm = False

        if itype in _BRANCH_MARKERS:
            if itype == "BST":
                branch_stack.append([[]])
            elif itype == "NXB":
                if not branch_stack:
                    warnings.append("nxb_without_bst")
                else:
                    branch_stack[-1].append([])
            elif itype == "BND":
                if not branch_stack:
                    warnings.append("bnd_without_bst")
                else:
                    branches = branch_stack.pop()
                    branch_exprs: list[LogicExpression] = []
                    for bi, branch in enumerate(branches):
                        sub = _series(branch)
                        if sub is not None:
                            if len(branches) > 1:
                                sub = sub.model_copy(update={"branch_index": bi})
                            branch_exprs.append(sub)
                    or_node = _parallel(branch_exprs)
                    if or_node is not None:
                        if branch_stack:
                            branch_stack[-1][-1].append(or_node)
                        else:
                            series.append(or_node)
            i += 1
            continue

        if itype == "PARALLEL_BRANCH":
            arm_exprs: list[LogicExpression] = []
            for arm in inst.operands:
                arm_insts = parse_ladder_rung_text(arm.strip())
                arm_expr, arm_warnings = _build_from_instructions(arm_insts)
                # Output-only parallel arms (AOI calls, bare OTE) have no
                # gating leaves — suppress their empty_expression warnings.
                arm_warnings = [
                    w for w in arm_warnings if w != "empty_expression"
                ]
                warnings.extend(arm_warnings)
                if arm_expr is not None:
                    arm_exprs.append(arm_expr)
            or_node = _parallel(arm_exprs)
            if or_node is not None:
                if branch_stack:
                    branch_stack[-1][-1].append(or_node)
                else:
                    series.append(or_node)
            skip_parallel_arm = True
            i += 1
            continue

        if itype in NO_OP_INSTRUCTIONS or itype in _SKIP_FOR_TREE:
            i += 1
            continue

        if not _is_gating_instruction(inst):
            i += 1
            continue

        leaf = _instruction_to_leaf(inst)
        if branch_stack:
            branch_stack[-1][-1].append(leaf)
        else:
            series.append(leaf)
        i += 1

    if branch_stack:
        warnings.append("unclosed_bst")
        return None, warnings

    result = _series(series)
    if result is None:
        warnings.append("empty_expression")
    return result, warnings


def _is_gating_instruction(inst: ControlInstruction) -> bool:
    itype = inst.instruction_type.upper()
    if itype in CONDITION_INSTRUCTIONS or itype in _ONE_SHOT_INSTRUCTIONS:
        return True
    role = (inst.metadata or {}).get("instruction_role")
    return role == "condition"


def _instruction_to_leaf(inst: ControlInstruction) -> LogicExpression:
    itype = inst.instruction_type.upper()
    meta = inst.metadata or {}
    common = {
        "instruction_type": itype,
        "instruction_id": inst.id,
        "raw_text": inst.raw_text,
    }

    if itype == "XIC":
        tag = inst.operands[0] if inst.operands else None
        return LogicExpression(
            kind=LogicExpressionKind.CONTACT,
            tag=tag,
            examined_value=True,
            operands=list(inst.operands),
            **common,
        )
    if itype == "XIO":
        tag = inst.operands[0] if inst.operands else None
        return LogicExpression(
            kind=LogicExpressionKind.CONTACT,
            tag=tag,
            examined_value=False,
            operands=list(inst.operands),
            **common,
        )
    if itype in COMPARISON_INSTRUCTIONS:
        return LogicExpression(
            kind=LogicExpressionKind.COMPARE,
            operands=list(inst.operands),
            **common,
        )
    return LogicExpression(
        kind=LogicExpressionKind.INSTRUCTION,
        operands=list(inst.operands),
        tag=inst.operands[0] if inst.operands else None,
        examined_value=meta.get("examined_value"),
        **common,
    )


def _series(nodes: list[LogicExpression]) -> LogicExpression | None:
    cleaned = [n for n in nodes if n is not None]
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return LogicExpression(kind=LogicExpressionKind.AND, children=cleaned)


def _parallel(nodes: list[LogicExpression]) -> LogicExpression | None:
    cleaned = [n for n in nodes if n is not None]
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return LogicExpression(kind=LogicExpressionKind.OR, children=cleaned)


def _render(expr: LogicExpression) -> str:
    kind = expr.kind
    if kind == LogicExpressionKind.AND:
        parts = [_render(c) for c in expr.children]
        inner = " AND ".join(parts)
        return f"({inner})" if len(parts) > 1 else inner
    if kind == LogicExpressionKind.OR:
        parts = [_render(c) for c in expr.children]
        inner = " OR ".join(parts)
        return f"({inner})" if len(parts) > 1 else inner
    if kind == LogicExpressionKind.NOT:
        child = _render(expr.children[0]) if expr.children else "?"
        return f"NOT ({child})"
    if kind == LogicExpressionKind.CONTACT:
        tag = expr.tag or "?"
        if expr.examined_value is False:
            return f"{tag} is FALSE"
        return f"{tag} is TRUE"
    if expr.raw_text:
        return expr.raw_text
    if expr.instruction_type and expr.operands:
        inner = ",".join(expr.operands)
        return f"{expr.instruction_type}({inner})"
    return expr.instruction_type or "?"
