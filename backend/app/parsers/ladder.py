import re
from typing import Iterable

from app.models.control_model import (
    ControlInstruction,
    LadderBranch,
    LadderRung,
    LogicCondition,
    LogicRead,
    LogicWrite,
    SourceLocation,
    TagReference,
)


# Legacy pattern kept for tests that might import it; the scanner below
# supersedes its behaviour for real rung text.
ROCKWELL_INSTRUCTION_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]+)\s*\(([^)]*)\)")

BOOLEAN_OUTPUT_INSTRUCTIONS = {"OTE", "OTL", "OTU"}
STATEFUL_OUTPUT_INSTRUCTIONS = {
    "TON",
    "TONR",
    "TOF",
    "RTO",
    "CTU",
    "CTD",
    "CTUD",
}
RESET_INSTRUCTIONS = {"RES"}
CONDITION_INSTRUCTIONS = {
    "XIC",
    "XIO",
    "ONS",
    "OSR",
    "OSF",
    "EQU",
    "NEQ",
    "GRT",
    "GEQ",
    "LES",
    "LEQ",
    "LIM",
    "CMP",
}
COMPARISON_INSTRUCTIONS = {"EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM", "CMP"}
MATH_INSTRUCTIONS = {"ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "AND", "OR", "XOR"}
MOVE_INSTRUCTIONS = {"MOV", "MOVE", "COP", "CPS", "FLL", "SIZE", "BTR", "BTW", "BTS", "BTT"}
SYSTEM_ACCESS_INSTRUCTIONS = {"GSV", "SSV"}
COMMUNICATION_INSTRUCTIONS = {"MSG"}
CALL_INSTRUCTIONS = {"JSR", "SBR", "RET"}
NO_OP_INSTRUCTIONS = {"NOP"}
WRITE_INSTRUCTIONS = (
    BOOLEAN_OUTPUT_INSTRUCTIONS
    | STATEFUL_OUTPUT_INSTRUCTIONS
    | RESET_INSTRUCTIONS
    | {"GSV", "MSG"}
)

_BRANCH_KEYWORDS = frozenset({"BST", "NXB", "BND"})

_INSTR_HEAD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*\(")


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def _branch_keyword_at(s: str, i: int) -> str | None:
    """Return BST/NXB/BND when a bare branch token starts at ``i``."""

    for kw in ("BST", "NXB", "BND"):
        ln = len(kw)
        if i + ln > len(s):
            continue
        if s[i : i + ln].upper() != kw:
            continue
        if i > 0 and _is_ident_char(s[i - 1]):
            continue
        after = i + ln
        if after < len(s) and _is_ident_char(s[after]):
            continue
        if after < len(s) and s[after] == "(":
            continue
        return kw
    return None


def _matching_close_paren(s: str, open_paren: int) -> int:
    depth = 0
    j = open_paren
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _matching_close_bracket(s: str, open_bracket: int) -> int:
    depth_sq = 0
    depth_paren = 0
    j = open_bracket
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "[" and depth_paren == 0:
            depth_sq += 1
        elif ch == "]" and depth_paren == 0:
            depth_sq -= 1
            if depth_sq == 0:
                return j
        elif ch == "(":
            depth_paren += 1
        elif ch == ")" and depth_paren > 0:
            depth_paren -= 1
        j += 1
    return -1


def _split_operand_list_respecting_nesting(inner: str) -> list[str]:
    """Split ``inner`` on commas not inside ``()`` or ``[]``."""

    parts: list[str] = []
    cur: list[str] = []
    d_paren = d_bracket = 0
    for ch in inner:
        if ch == "(":
            d_paren += 1
        elif ch == ")" and d_paren > 0:
            d_paren -= 1
        elif ch == "[":
            d_bracket += 1
        elif ch == "]" and d_bracket > 0:
            d_bracket -= 1
        elif ch == "," and d_paren == 0 and d_bracket == 0:
            piece = "".join(cur).strip()
            if piece:
                parts.append(piece)
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_ladder_rung_text(
    rung_text: str,
    rung_number: int | None = None,
) -> list[ControlInstruction]:
    """Tokenize a single Rockwell ladder rung string.

    * Instructions ``NAME(...)`` use parenthesis-depth counting so
      nested ``()`` inside operands (e.g. UDT constructors in JSR
      parameter lists) do not truncate early.
    * Bare branch markers ``BST`` / ``NXB`` / ``BND`` (no parentheses)
      become synthetic instructions with ``metadata["branch_marker"]``.
      We do **not** infer which logical branch an operand instruction
      belongs to — that remains an unsupported branch-attribution
      problem for a future analyzer.
    * Square-bracket parallel notation ``[XIC(A),XIC(B)]`` emits a
      ``PARALLEL_BRANCH`` instruction (operands = raw arm strings) and
      **recursively** parses each arm so XIC/OTE/etc. still appear as
      first-class instructions for tag discovery. Recursive fragments
      inherit the same ``rung_number``; ``metadata["parallel_arm"]``
      marks instructions originating inside a bracket arm.
    * ``PARALLEL_BRANCH`` is not a real Rockwell opcode; it exists only
      as a structural placeholder with ``platform_specific`` metadata
      in the normalizer's generic unknown-instruction path.

    Unsupported / intentionally narrow:

    * ASCII-art rung graphics, /OT latch bars, and FBD-in-text exports.
    * Branch levels beyond one ``BST..BND`` group interpreted together
      (we only tokenize; boolean trees are built separately by
      :mod:`app.parsers.ladder_logic`).
    """

    instructions: list[ControlInstruction] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        cid = f"r{rung_number or 0}_i{counter}"
        counter += 1
        return cid

    def append_instruction(
        instruction_type: str,
        operands: list[str],
        raw_span: str,
        *,
        output: str | None = None,
        extra_meta: dict | None = None,
    ) -> None:
        meta: dict = {
            "vendor": "rockwell",
            "parser": "rockwell_rung_text_tokenizer_v3",
            "instruction_role": _instruction_role(instruction_type),
            "instruction_family": _instruction_family(instruction_type),
            "output_role": _output_role(instruction_type),
            "source_span": {
                "start": 0,
                "end": len(raw_span),
            },
            "rung_text": rung_text,
        }
        if extra_meta:
            meta.update(extra_meta)
        instructions.append(
            ControlInstruction(
                id=next_id(),
                instruction_type=instruction_type,
                operands=operands,
                output=output if output is not None else _get_output_operand(instruction_type, operands),
                raw_text=raw_span,
                language="ladder",
                rung_number=rung_number,
                metadata=meta,
            )
        )

    branch_level = 0
    branch_index = 0

    def scan_fragment(fragment: str, *, parallel_arm: bool) -> None:
        nonlocal counter, branch_level, branch_index
        i = 0
        n = len(fragment)
        while i < n:
            while i < n and fragment[i].isspace():
                i += 1
            if i >= n:
                break

            kw = _branch_keyword_at(fragment, i)
            if kw:
                if kw == "BST":
                    branch_level += 1
                    branch_index = 0
                elif kw == "NXB":
                    branch_index += 1
                elif kw == "BND" and branch_level > 0:
                    branch_level -= 1
                append_instruction(
                    kw,
                    [],
                    kw,
                    extra_meta={
                        "branch_marker": True,
                        "parallel_branch_notation": "bst_nxb_bnd",
                        "branch_level": branch_level,
                        "branch_index": branch_index,
                    },
                )
                i += len(kw)
                continue

            if fragment[i] == "[":
                close_b = _matching_close_bracket(fragment, i)
                if close_b == -1:
                    i += 1
                    continue
                inner = fragment[i + 1 : close_b]
                raw_bracket = fragment[i : close_b + 1]
                arms = _split_operand_list_respecting_nesting(inner)
                append_instruction(
                    "PARALLEL_BRANCH",
                    arms,
                    raw_bracket,
                    output=None,
                    extra_meta={
                        "parallel_branch_notation": "square_bracket",
                        "parallel_arm_count": len(arms),
                    },
                )
                for arm in arms:
                    scan_fragment(arm.strip(), parallel_arm=True)
                i = close_b + 1
                continue

            m = _INSTR_HEAD_RE.match(fragment, i)
            if not m:
                i += 1
                continue

            name = m.group(1)
            if name in _BRANCH_KEYWORDS:
                i += 1
                continue

            # ``m`` is anchored at ``i``; the opening ``(`` is always the
            # last character of the full regex match (``NAME\s*(``).
            open_paren = m.end(0) - 1
            if open_paren < 0 or open_paren >= n or fragment[open_paren] != "(":
                i += 1
                continue

            close_p = _matching_close_paren(fragment, open_paren)
            if close_p == -1:
                i += 1
                continue

            inner = fragment[open_paren + 1 : close_p]
            operands = _parse_operands(inner)
            raw_span = fragment[i : close_p + 1]
            span_start = m.start(0)
            span_end = close_p + 1
            extra: dict = {}
            if parallel_arm:
                extra["parallel_arm"] = True
            if branch_level > 0:
                extra["branch_level"] = branch_level
                extra["branch_index"] = branch_index
            meta = {
                "vendor": "rockwell",
                "parser": "rockwell_rung_text_tokenizer_v3",
                "instruction_role": _instruction_role(name),
                "instruction_family": _instruction_family(name),
                "output_role": _output_role(name),
                "source_span": {"start": span_start, "end": span_end},
                "rung_text": rung_text,
                **extra,
            }
            instructions.append(
                ControlInstruction(
                    id=next_id(),
                    instruction_type=name,
                    operands=operands,
                    output=_get_output_operand(name, operands),
                    raw_text=raw_span,
                    language="ladder",
                    rung_number=rung_number,
                    metadata=meta,
                )
            )
            i = close_p + 1

    scan_fragment(rung_text, parallel_arm=False)

    for inst in instructions:
        span = inst.metadata.get("source_span")
        if isinstance(span, dict) and "start" in span:
            if inst.metadata.get("parallel_arm"):
                continue
            raw = inst.raw_text or ""
            if raw and rung_text.count(raw) == 1:
                real_start = rung_text.find(raw)
                if real_start != -1:
                    span["start"] = real_start
                    span["end"] = real_start + len(raw)

    return instructions


def build_ladder_rung_ir(
    rung_text: str,
    rung_number: int,
    *,
    instructions: list[ControlInstruction] | None = None,
    logic_expression: object | None = None,
    logic_expression_resolved: bool = False,
    logic_warnings: list[str] | None = None,
    source_file: str | None = None,
    controller: str | None = None,
    program: str | None = None,
    routine: str | None = None,
) -> LadderRung:
    """Build a neutral, content-safe IR object for one ladder rung.

    The returned object references instruction ids, tag operands, branch
    nesting, and source locations. It does not need to copy rung text or
    comments; callers that need legacy raw text still have
    ``ControlRoutine.raw_logic`` and ``ControlInstruction.raw_text``.
    """

    insts = instructions if instructions is not None else parse_ladder_rung_text(
        rung_text,
        rung_number,
    )
    source_location = SourceLocation(
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
        rung_number=rung_number,
        vendor="rockwell",
        path=_source_path(
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            rung_number=rung_number,
        ),
    )
    reads, writes = _read_write_ir_from_instructions(insts, source_location)
    branch_root = _build_ladder_branch_tree(insts, source_location)
    condition = LogicCondition(
        id=f"rung:{rung_number}:condition",
        expression=logic_expression,
        reads=reads,
        source_location=source_location,
        resolved=logic_expression_resolved,
        warnings=list(logic_warnings or []),
        metadata={
            "logic_expression_resolved": logic_expression_resolved,
        },
    )
    return LadderRung(
        id=f"ladder_rung:{rung_number}",
        rung_number=rung_number,
        source_location=source_location,
        instructions=[inst.id for inst in insts if inst.id],
        reads=reads,
        writes=writes,
        logic_condition=condition,
        root_branch=branch_root,
        metadata={
            "parser": "intelli_ladder_ir_v1",
            "instruction_count": len(insts),
            "has_branches": _branch_has_parallel(branch_root),
        },
    )


def extract_operand_tags(instructions: Iterable[ControlInstruction]) -> set[str]:
    tags: set[str] = set()

    for instruction in instructions:
        for operand in _tag_discovery_operands(instruction):
            if _looks_like_tag_reference(operand):
                tags.add(operand)

    return tags


def _tag_discovery_operands(instruction: ControlInstruction) -> list[str]:
    itype = instruction.instruction_type.upper()
    if itype == "GSV":
        return [instruction.operands[3]] if len(instruction.operands) > 3 else []
    if itype == "SSV":
        return [instruction.operands[3]] if len(instruction.operands) > 3 else []
    if itype == "MSG":
        return [instruction.operands[0]] if instruction.operands else []
    return list(instruction.operands)


def _source_path(
    *,
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
    rung_number: int | None,
    instruction_id: str | None = None,
) -> str:
    parts = []
    if source_file:
        parts.append(f"File:{source_file}")
    if controller:
        parts.append(f"Controller:{controller}")
    if program:
        parts.append(f"Program:{program}")
    if routine:
        parts.append(f"Routine:{routine}")
    if rung_number is not None:
        parts.append(f"Rung:{rung_number}")
    if instruction_id:
        parts.append(f"Instruction:{instruction_id}")
    return "/".join(parts)


def _instruction_source_location(
    base: SourceLocation,
    instruction: ControlInstruction,
) -> SourceLocation:
    return SourceLocation(
        source_file=base.source_file,
        controller=base.controller,
        program=base.program,
        routine=base.routine,
        rung_number=base.rung_number,
        instruction_id=instruction.id,
        vendor=base.vendor,
        path=_source_path(
            source_file=base.source_file,
            controller=base.controller,
            program=base.program,
            routine=base.routine,
            rung_number=base.rung_number,
            instruction_id=instruction.id,
        ),
    )


def _tag_ref_from_operand(operand: str) -> TagReference:
    raw = operand.strip()
    base = raw.split(".", 1)[0].split("[", 1)[0]
    member_path: list[str] = []
    if "." in raw:
        tail = raw.split(".", 1)[1]
        member_path = [
            piece.split("[", 1)[0]
            for piece in tail.split(".")
            if piece.split("[", 1)[0]
        ]
    return TagReference(
        raw=raw,
        tag_name=base or raw,
        member_path=member_path,
    )


def _read_indices_for_instruction(
    instruction: ControlInstruction,
) -> list[int]:
    itype = instruction.instruction_type.upper()
    if itype in CONDITION_INSTRUCTIONS or itype in {"ONS", "OSR", "OSF"}:
        return [0]
    if itype in COMPARISON_INSTRUCTIONS:
        return list(range(len(instruction.operands)))
    if itype in {"ADD", "SUB", "MUL", "DIV", "MOD", "AND", "OR", "XOR"}:
        return [0, 1]
    if itype in {"MOV", "MOVE", "COP", "CPS", "FLL", "SIZE"}:
        return [0]
    if itype == "GSV":
        return []
    if itype == "SSV":
        return [3]
    if itype == "MSG":
        return [0]
    if itype in STATEFUL_OUTPUT_INSTRUCTIONS and len(instruction.operands) > 1:
        return list(range(1, len(instruction.operands)))
    return []


def _write_behavior_for_instruction(instruction_type: str) -> str | None:
    itype = instruction_type.upper()
    if itype == "OTE":
        return "sets_true"
    if itype == "OTL":
        return "latches"
    if itype == "OTU":
        return "unlatches"
    if itype in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "writes_instruction_structure"
    if itype == "RES":
        return "resets"
    if itype in {"MOV", "MOVE", "COP", "CPS", "FLL", "SIZE"}:
        return "moves_value"
    if itype == "GSV":
        return "moves_system_value"
    if itype == "SSV":
        return "sets_system_value"
    if itype == "MSG":
        return "updates_message_control"
    if itype in {"ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "AND", "OR", "XOR"}:
        return "calculates"
    if itype in {"ONS", "OSR", "OSF"}:
        return "pulses"
    return None


def _read_write_ir_from_instructions(
    instructions: list[ControlInstruction],
    rung_location: SourceLocation,
) -> tuple[list[LogicRead], list[LogicWrite]]:
    reads: list[LogicRead] = []
    writes: list[LogicWrite] = []
    for inst in instructions:
        if (inst.metadata or {}).get("branch_marker"):
            continue
        loc = _instruction_source_location(rung_location, inst)
        for index in _read_indices_for_instruction(inst):
            if index >= len(inst.operands):
                continue
            operand = inst.operands[index]
            if not _looks_like_tag_reference(operand):
                continue
            reads.append(
                LogicRead(
                    tag=_tag_ref_from_operand(operand),
                    source_location=loc,
                    instruction_id=inst.id,
                    role=(inst.metadata or {}).get("instruction_role"),
                    metadata={
                        "operand_index": index,
                        "instruction_type": inst.instruction_type,
                    },
                )
            )

        output = inst.output or _get_output_operand(inst.instruction_type, inst.operands)
        if output and _looks_like_tag_reference(output):
            writes.append(
                LogicWrite(
                    target=_tag_ref_from_operand(output),
                    source_location=loc,
                    instruction_id=inst.id,
                    behavior=_write_behavior_for_instruction(inst.instruction_type),
                    metadata={"instruction_type": inst.instruction_type},
                )
            )
    return reads, writes


def _build_ladder_branch_tree(
    instructions: list[ControlInstruction],
    rung_location: SourceLocation,
) -> LadderBranch:
    root = LadderBranch(
        id=f"r{rung_location.rung_number or 0}:root",
        branch_type="series",
        source_location=rung_location,
    )
    current = root
    stack: list[tuple[LadderBranch, LadderBranch]] = []
    skip_parallel_arm = False
    synthetic_branch_index = 0

    for inst in instructions:
        itype = inst.instruction_type.upper()
        if skip_parallel_arm:
            if (inst.metadata or {}).get("parallel_arm"):
                continue
            skip_parallel_arm = False

        if itype == "BST":
            synthetic_branch_index += 1
            group = LadderBranch(
                id=f"r{rung_location.rung_number or 0}:branch:{synthetic_branch_index}",
                branch_type="parallel",
                source_location=rung_location,
                metadata={"notation": "bst_nxb_bnd"},
            )
            current.children.append(group)
            arm = _new_ladder_arm(group, rung_location)
            group.children.append(arm)
            stack.append((current, group))
            current = arm
            continue

        if itype == "NXB":
            if stack:
                _, group = stack[-1]
                arm = _new_ladder_arm(group, rung_location)
                group.children.append(arm)
                current = arm
            continue

        if itype == "BND":
            if stack:
                parent, _ = stack.pop()
                current = parent
            continue

        if itype == "PARALLEL_BRANCH":
            synthetic_branch_index += 1
            group = LadderBranch(
                id=f"r{rung_location.rung_number or 0}:bracket:{synthetic_branch_index}",
                branch_type="parallel",
                source_location=rung_location,
                metadata={
                    "notation": "square_bracket",
                    "arm_count": len(inst.operands),
                },
            )
            for arm_index, arm_text in enumerate(inst.operands):
                arm_insts = parse_ladder_rung_text(
                    arm_text.strip(),
                    rung_number=rung_location.rung_number,
                )
                arm_root = _build_ladder_branch_tree(arm_insts, rung_location)
                arm = LadderBranch(
                    id=f"{group.id}:arm:{arm_index}",
                    branch_type="parallel_arm",
                    children=list(arm_root.children),
                    instruction_ids=list(arm_root.instruction_ids),
                    conditions=list(arm_root.conditions),
                    source_location=rung_location,
                    metadata={"arm_index": arm_index},
                )
                group.children.append(arm)
            current.children.append(group)
            skip_parallel_arm = True
            continue

        if itype in _BRANCH_KEYWORDS:
            continue

        if inst.id:
            current.instruction_ids.append(inst.id)
        cond = _condition_from_instruction(inst, rung_location)
        if cond is not None:
            current.conditions.append(cond)

    return root


def _new_ladder_arm(
    group: LadderBranch,
    rung_location: SourceLocation,
) -> LadderBranch:
    arm_index = len(group.children)
    return LadderBranch(
        id=f"{group.id}:arm:{arm_index}",
        branch_type="parallel_arm",
        source_location=rung_location,
        metadata={"arm_index": arm_index},
    )


def _condition_from_instruction(
    instruction: ControlInstruction,
    rung_location: SourceLocation,
) -> LogicCondition | None:
    itype = instruction.instruction_type.upper()
    if itype not in CONDITION_INSTRUCTIONS and itype not in {"OSR", "OSF"}:
        return None
    loc = _instruction_source_location(rung_location, instruction)
    reads: list[LogicRead] = []
    for index in _read_indices_for_instruction(instruction):
        if index >= len(instruction.operands):
            continue
        operand = instruction.operands[index]
        if not _looks_like_tag_reference(operand):
            continue
        reads.append(
            LogicRead(
                tag=_tag_ref_from_operand(operand),
                source_location=loc,
                instruction_id=instruction.id,
                role="condition",
                metadata={
                    "operand_index": index,
                    "instruction_type": instruction.instruction_type,
                },
            )
        )
    if not reads and itype not in COMPARISON_INSTRUCTIONS:
        return None
    return LogicCondition(
        id=f"{instruction.id}:condition" if instruction.id else None,
        reads=reads,
        source_location=loc,
        resolved=True,
        metadata={"instruction_type": instruction.instruction_type},
    )


def _branch_has_parallel(branch: LadderBranch | None) -> bool:
    if branch is None:
        return False
    if branch.branch_type == "parallel":
        return True
    return any(_branch_has_parallel(child) for child in branch.children)


def _parse_operands(operand_text: str) -> list[str]:
    operands: list[str] = []
    current = []
    depth = 0

    for char in operand_text:
        if char == "," and depth == 0:
            operand = "".join(current).strip()
            operands.append(operand)
            current = []
            continue

        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1

        current.append(char)

    operand = "".join(current).strip()
    if operand or operands:
        operands.append(operand)

    return operands


def _get_output_operand(instruction_type: str, operands: list[str]) -> str | None:
    itype = instruction_type.upper()
    if itype in {"ADD", "SUB", "MUL", "DIV", "MOD", "AND", "OR", "XOR"}:
        return operands[2] if len(operands) > 2 else None
    if itype == "CPT":
        return operands[0] if operands else None
    if itype in {"MOV", "MOVE", "COP", "CPS", "FLL"}:
        return operands[1] if len(operands) > 1 else None
    if itype == "SIZE":
        return operands[2] if len(operands) > 2 else None
    if itype == "GSV":
        return operands[3] if len(operands) > 3 else None
    if itype == "MSG":
        return operands[0] if operands else None
    if itype not in WRITE_INSTRUCTIONS or not operands:
        return None

    return operands[0]


def _instruction_role(instruction_type: str) -> str:
    if instruction_type in BOOLEAN_OUTPUT_INSTRUCTIONS:
        return "boolean_output"
    if instruction_type in RESET_INSTRUCTIONS:
        return "reset"
    if instruction_type in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "stateful_output"
    if instruction_type in CONDITION_INSTRUCTIONS:
        return "condition"
    if instruction_type in MATH_INSTRUCTIONS:
        return "math"
    if instruction_type in MOVE_INSTRUCTIONS:
        return "move"
    if instruction_type in SYSTEM_ACCESS_INSTRUCTIONS:
        return "system_access"
    if instruction_type in COMMUNICATION_INSTRUCTIONS:
        return "communication"
    if instruction_type in CALL_INSTRUCTIONS:
        return "call"
    if instruction_type in NO_OP_INSTRUCTIONS:
        return "no_op"
    return "unknown"


def _instruction_family(instruction_type: str) -> str:
    """Coarse family label aligned with normalization registry groupings."""

    if instruction_type in BOOLEAN_OUTPUT_INSTRUCTIONS:
        return "boolean_output"
    if instruction_type in RESET_INSTRUCTIONS:
        return "reset"
    if instruction_type in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "stateful_output"
    if instruction_type in COMPARISON_INSTRUCTIONS:
        return "comparison"
    if instruction_type in MATH_INSTRUCTIONS:
        return "math"
    if instruction_type in MOVE_INSTRUCTIONS:
        return "move_copy"
    if instruction_type in SYSTEM_ACCESS_INSTRUCTIONS:
        return "system_access"
    if instruction_type in COMMUNICATION_INSTRUCTIONS:
        return "communication"
    if instruction_type in CALL_INSTRUCTIONS:
        return "routine_call"
    if instruction_type in NO_OP_INSTRUCTIONS:
        return "no_op"
    if instruction_type in {"XIC", "XIO"}:
        return "condition"
    if instruction_type in {"ONS", "OSR", "OSF"}:
        return "one_shot"
    if instruction_type in _BRANCH_KEYWORDS:
        return "branch_marker"
    if instruction_type == "PARALLEL_BRANCH":
        return "parallel_branch"
    return "unknown"


def _output_role(instruction_type: str) -> str | None:
    if instruction_type in BOOLEAN_OUTPUT_INSTRUCTIONS:
        return "drives_boolean_tag"
    if instruction_type in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "writes_instruction_structure"
    return None


def _looks_like_tag_reference(value: str) -> bool:
    if not value:
        return False

    normalized = value.strip()

    if normalized.startswith(('"', "'")):
        return False

    if _is_number(normalized):
        return False

    if normalized.upper() in {"TRUE", "FALSE"}:
        return False

    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_\[\].:]*$", normalized))


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
