"""Normalization service: parsed L5X -> reasoning schema.

This module converts the existing parsed-project data produced by the
Rockwell L5X connector (``app.models.control_model.ControlProject``)
into the platform-agnostic reasoning schema defined in
``app.models.reasoning``.

The conversion is deterministic and rule-based. No LLM is involved and
no inferences are drawn beyond documented controls-language semantics
we have explicitly enrolled in the registry.

Architecture
------------

All instruction-level knowledge is consolidated in a single
**Instruction Semantics Registry** (``INSTRUCTION_SEMANTICS``). Each
entry classifies an instruction into an ``_InstructionFamily`` and
declares its operand semantics (which positions are read, which is
written, whether a ``WriteBehaviorType`` applies, etc.). A small set
of family handlers consume those descriptors to emit ``Relationship``
edges. The dispatcher emits nothing for instructions whose semantics
are registered but ``implemented=False`` — they appear in the graph
as ``CONTAINS``-attached ``INSTRUCTION`` ControlObjects, ready for a
later pass to flesh out edges without having to re-discover the
instruction inventory. Unknown / AOI / vendor-specific instructions
also pass through structurally with no cause/effect edges.

The goal of this layout is to make INTELLI's controls-language
coverage *additive*: enabling new instructions is a registry edit,
not a new code path.

Scope today (what this module emits)
------------------------------------

* ``ControlObject`` nodes for controllers, programs, routines, ladder
  rungs, instructions, and tags.
* Structural ``CONTAINS`` relationships
  (controller -> program -> routine -> rung -> instruction, plus
  controller/program -> tag).
* Ladder cause/effect relationships, registry-driven:
    - ``XIC`` / ``XIO`` -> ``READS`` (with ``examined_value`` recorded).
    - ``OTE`` / ``OTL`` / ``OTU`` -> ``WRITES`` with the matching
      ``WriteBehaviorType`` (``sets_true`` / ``latches`` / ``unlatches``).
    - ``TON`` / ``TOF`` / ``RTO`` / ``CTU`` / ``CTD`` -> ``WRITES`` to
      the stateful structure (no ``write_behavior`` yet).
    - ``RES`` -> ``RESETS`` against the targeted timer/counter.
    - ``JSR`` -> ``CALLS`` against the resolved routine (same-program
      lookup; cross-program is TODO).
    - Comparisons (``EQU`` / ``NEQ`` / ``LES`` / ``LEQ`` / ``GRT`` /
      ``GEQ`` / ``LIM``) -> ``READS`` per tag operand (literals skipped),
      with ``comparison_operator`` + ``compared_operands`` in
      ``platform_specific`` and ``gating_kind="comparison"`` so Trace v2
      doesn't fold them into the XIC/XIO conjunction.
    - Math (``ADD`` / ``SUB`` / ``MUL`` / ``DIV``) -> ``READS`` for each
      source operand that looks like a tag and ``WRITES`` with
      ``write_behavior=calculates`` for the destination. ``CPT`` emits
      only the destination ``WRITES`` because Rockwell encodes the
      expression as a quoted string the ladder tokenizer doesn't crack.
    - Move / copy (``MOV`` / ``COP``) -> ``READS`` for the source,
      ``WRITES`` with ``write_behavior=moves_value`` for the destination.
    - One-shots (``ONS`` / ``OSR`` / ``OSF``) -> ``READS`` for the
      storage bit, ``WRITES`` with ``write_behavior=pulses`` for the
      output (``ONS`` writes back to its own storage bit).
* Per-relationship ``platform_specific`` metadata:
    - ``member`` / ``member_semantic`` when an operand accesses a known
      timer/counter member (``.DN`` / ``.TT`` / ``.EN`` / ``.ACC`` /
      ``.PRE``). ``.DN`` -> ``done``, etc.
    - ``rung_has_branches`` / ``rung_branch_count`` whenever the rung
      contains ``BST`` / ``NXB`` / ``BND`` tokens. When a structured
      :class:`~app.models.reasoning.LogicExpression` is resolved for the
      rung, ``logic_expression_resolved`` is also set and branch paths
      are represented in the universal boolean tree on each edge.
* One ``ExecutionContext`` per routine
  (``ExecutionContextType.ROUTINE``); cause/effect edges reference it
  via ``execution_context_id``.

Out of scope today (registered with implemented=False so the inventory
is captured and future passes can implement them):

* PID / control loops (``PID``).
* Per-branch attribution of instructions when ``LogicExpression`` cannot
  be resolved (unclosed ``BST``, ambiguous notation) — resolved rungs
  carry a structured boolean tree instead.

Public entry point
------------------

``normalize_l5x_project(parsed_project) -> dict``

Returns a dict shaped like::

    {
        "control_objects":   list[ControlObject],
        "relationships":     list[Relationship],
        "execution_contexts": list[ExecutionContext],
    }


Future work (intentionally not implemented here)
------------------------------------------------

Structured Text normalization (NEW)
    Simple ST routines are now parsed via
    :mod:`app.parsers.structured_text_blocks` into block dataclasses
    (assignment, IF / IF-ELSE, simple CASE, complex) and converted to
    ``WRITES`` / ``READS`` edges in :func:`_normalize_structured_text_routine`.
    Each top-level block emits one synthetic ``STATEMENT``-style
    ``ControlObject`` (typed ``INSTRUCTION`` with
    ``attributes["language"]="structured_text"`` so Trace v2's ST
    writer path lights up) and the spec's ``platform_specific`` fields
    (``language``, ``raw_text``, ``statement_type``, ``assigned_value``,
    ``extracted_conditions``). Anything outside the supported envelope
    is preserved with ``st_parse_status="too_complex"``. Expanding the
    envelope (WHILE / FOR / REPEAT / OR / parens / arithmetic) is a
    future extension of the parser, not the normalizer.
TODO(intelli/normalization): Function Block Diagram (FBD)
    normalization. Each block instance is its own ControlObject; pin
    connections become explicit READS/WRITES/REFERENCES relationships.
TODO(intelli/normalization): Sequential Function Chart (SFC)
    normalization. SFC steps become ``ControlObjectType.SFC_STEP``,
    transitions become ``Relationship`` edges of type ``SEQUENCES`` or
    ``CONDITION_FOR``.
Add-On Instruction (AOI) handling (NEW — universal LogicBlock)
    An AOI instance call is normalized as a vendor-neutral **LogicBlock**:
    the call's ``INSTRUCTION`` ControlObject is retyped to
    ``FUNCTION_BLOCK`` and its operands are resolved against the
    ``AddOnInstructionDef`` parameter list by position. Operand 0 is the
    backing/instance tag (instance ``REFERENCES`` it); the remaining
    operands bind to the AOI's call parameters (Required parameters in
    declared order, excluding the system EnableIn/EnableOut). Direction
    maps universally: ``Input`` -> instance ``READS`` bound tag,
    ``Output`` -> instance ``WRITES`` bound tag, ``InOut`` -> ``READS`` +
    ``WRITES``. The instance is linked to its internal logic routine via
    ``CALLS`` when the connector exposed it. This is the *same* shape a
    Siemens FB will use (``Rockwell AOI`` and ``Siemens FB`` both ->
    ``FUNCTION_BLOCK`` + typed parameter bindings). When no definition is
    found, behavior is unchanged (structural-only, unknown). Deferred:
    extracting the AOI body's own instructions into edges.
Alias / UDT resolution (NEW)
    Alias tags emit a ``REFERENCES`` edge (alias -> base) so trace follows
    the alias to the real signal; relationships referencing an alias carry
    ``alias_for`` metadata. Operands that access a UDT member
    (``Valve_DIW.Cmd``) resolve to the base tag (existing behavior) and now
    additionally carry ``udt_type`` / ``udt_member`` / ``udt_member_type``
    metadata when the ``DataTypeDef`` is known.
TODO(intelli/normalization): Branch-aware ladder logic. A rung's
    parallel branches all contribute to its boolean condition, but we
    currently treat the rung text as a single condition string. A
    branch-aware analyzer should produce structured logic expressions
    and per-branch evidence.
TODO(intelli/normalization): Tag role inference. Today
    ``ControlObject.role`` defaults to ``ControlRole.UNKNOWN``. A
    follow-up pass should infer ``COMMAND`` / ``FEEDBACK`` /
    ``PERMISSIVE`` / ``ALARM`` / ``SETPOINT`` / etc. from naming
    conventions, descriptions, and how the tag is written/read.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.models.control_model import (
    AddOnInstructionDef,
    AOIParameter,
    ControlController,
    ControlInstruction,
    ControlProject,
    ControlRoutine,
    ControlTag,
    DataTypeDef,
)
from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    ExecutionContext,
    ExecutionContextType,
    LogicExpression,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.parsers.ladder_logic import (
    build_rung_logic_expression,
    logic_expression_to_text,
)
from app.parsers.st_arithmetic import (
    parse_st_arithmetic_operands,
    parse_st_function_call_operands,
)
from app.parsers.st_expression import parse_st_expression
from app.parsers.structured_text_blocks import (
    STAssignment,
    STBlock,
    STCaseBlock,
    STComparisonTerm,
    STComplexBlock,
    STCondition,
    STConjunction,
    STExpressionParse,
    STFbInvocation,
    STForMetadata,
    STIfBlock,
    STIfElsifChain,
    STLoopBlock,
    STReturnBlock,
    STTerm,
    parse_structured_text_blocks,
)


# ===========================================================================
# Instruction Semantics Registry
# ===========================================================================
#
# This is the single source of truth for "what does each controls-language
# instruction mean to the reasoning layer?". To enable a new instruction:
#   1. Add an entry below with the right ``_InstructionFamily``.
#   2. Fill in ``read_operand_indices`` / ``write_operand_index`` /
#      ``write_behavior`` / ``write_relationship_type`` as appropriate.
#   3. Flip ``implemented=True`` once the matching family handler is
#      verified end-to-end.
#
# Family handlers (below the registry) consume these descriptors. Adding a
# brand-new family of instructions usually means adding one family handler
# plus one or more registry entries, with no other changes.
# ===========================================================================


class _InstructionFamily(str, Enum):
    """High-level grouping of instructions by how they affect the graph."""

    CONDITION = "condition"            # XIC, XIO
    BOOLEAN_OUTPUT = "boolean_output"  # OTE, OTL, OTU
    STATEFUL_OUTPUT = "stateful_output"  # TON, TOF, RTO, CTU, CTD
    RESET = "reset"                    # RES
    COMPARISON = "comparison"          # EQU, NEQ, LES, LEQ, GRT, GEQ, LIM
    MATH = "math"                      # ADD, SUB, MUL, DIV, CPT
    MOVE_COPY = "move_copy"            # MOV, COP
    ROUTINE_CALL = "routine_call"      # JSR
    ONE_SHOT = "one_shot"              # ONS, OSR, OSF
    CONTROL_LOOP = "control_loop"      # PID
    NO_OP = "no_op"                    # NOP (recognized; emits no edges)
    LOGIC_BLOCK = "logic_block"        # AOI instance call -> universal LogicBlock
    UNKNOWN = "unknown"                # vendor / unrecognized


@dataclass(frozen=True)
class _InstructionSemantics:
    """Per-instruction semantic descriptor consumed by family handlers.

    Fields are deliberately small and declarative. A family handler reads
    only the fields relevant to its family; other fields are ignored.

    Attributes:
        family: Which ``_InstructionFamily`` this instruction belongs to.
        notes: Short docstring (used in comments and platform_specific).
        implemented: When False, the dispatcher emits no cause/effect
            edges for this instruction even if descriptors are present.
            The instruction still appears as a ``ControlObject``.
        read_operand_indices: Operand positions to emit READS from
            (used by the CONDITION family; future: MATH/COMPARISON).
        write_operand_index: Operand position whose value is being
            written / latched / unlatched / reset (used by
            BOOLEAN_OUTPUT, STATEFUL_OUTPUT, RESET, and future
            MATH/MOVE_COPY families). ``None`` means no write.
        write_behavior: Optional ``WriteBehaviorType`` to attach to the
            emitted write edge (only when statically obvious).
        write_relationship_type: ``RelationshipType`` to use for the
            write edge. Defaults to ``WRITES``; overridden for
            ``RES`` -> ``RESETS``, ``JSR`` -> ``CALLS``, etc.
        examined_value: For ``CONDITION`` instructions, the boolean
            value the rung examines for (True for XIC, False for XIO).
            Stashed in ``platform_specific`` on the READS edge.
        target_kind: ``"tag"`` (default) or ``"routine"`` -- what kind of
            ``ControlObject`` the write/call resolves against. ``JSR``
            uses ``"routine"``.
    """

    family: _InstructionFamily
    notes: str = ""
    implemented: bool = False
    read_operand_indices: tuple[int, ...] = ()
    write_operand_index: Optional[int] = None
    write_behavior: Optional[WriteBehaviorType] = None
    write_relationship_type: RelationshipType = RelationshipType.WRITES
    examined_value: Optional[bool] = None
    target_kind: str = "tag"


# The registry. Order is for readability only; lookups are by key.
INSTRUCTION_SEMANTICS: dict[str, _InstructionSemantics] = {
    # ---- Condition instructions ------------------------------------------
    "XIC": _InstructionSemantics(
        family=_InstructionFamily.CONDITION,
        read_operand_indices=(0,),
        examined_value=True,
        notes="Examine If Closed: rung-condition true when operand True.",
        implemented=True,
    ),
    "XIO": _InstructionSemantics(
        family=_InstructionFamily.CONDITION,
        read_operand_indices=(0,),
        examined_value=False,
        notes="Examine If Open: rung-condition true when operand False.",
        implemented=True,
    ),

    # ---- Boolean output instructions -------------------------------------
    # Relationship stays WRITES (with write_behavior set) to preserve the
    # existing graph shape; specialized LATCHES / UNLATCHES edge types
    # can be opted into later without breaking consumers.
    "OTE": _InstructionSemantics(
        family=_InstructionFamily.BOOLEAN_OUTPUT,
        write_operand_index=0,
        write_behavior=WriteBehaviorType.SETS_TRUE,
        notes="Output Energize: rung state drives the coil each scan.",
        implemented=True,
    ),
    "OTL": _InstructionSemantics(
        family=_InstructionFamily.BOOLEAN_OUTPUT,
        write_operand_index=0,
        write_behavior=WriteBehaviorType.LATCHES,
        notes="Output Latch: sets the coil; stays true until OTU/RES.",
        implemented=True,
    ),
    "OTU": _InstructionSemantics(
        family=_InstructionFamily.BOOLEAN_OUTPUT,
        write_operand_index=0,
        write_behavior=WriteBehaviorType.UNLATCHES,
        notes="Output Unlatch: clears a previously latched coil.",
        implemented=True,
    ),

    # ---- Stateful outputs (timers / counters) ----------------------------
    # No write_behavior: TON/TOF/RTO/CTU/CTD operate on whole structures
    # (.EN, .TT, .DN, .ACC, .PRE, ...). Encoding a single behavior would
    # over-simplify; structure-aware semantics is future work.
    "TON": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Timer On Delay: counts up while rung true.",
        implemented=True,
    ),
    "TONR": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Retentive Timer On: same structure semantics as TON (ACC retained).",
        implemented=True,
    ),
    "TOF": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Timer Off Delay: counts up while rung false.",
        implemented=True,
    ),
    "RTO": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Retentive Timer: accumulator retained across rung false.",
        implemented=True,
    ),
    "CTU": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Count Up.",
        implemented=True,
    ),
    "CTD": _InstructionSemantics(
        family=_InstructionFamily.STATEFUL_OUTPUT,
        write_operand_index=0,
        notes="Count Down.",
        implemented=True,
    ),

    # ---- Reset ------------------------------------------------------------
    "RES": _InstructionSemantics(
        family=_InstructionFamily.RESET,
        write_operand_index=0,
        write_relationship_type=RelationshipType.RESETS,
        notes="Reset the timer / counter / latched bit at operand 0.",
        implemented=True,
    ),

    # ---- Routine calls ----------------------------------------------------
    "JSR": _InstructionSemantics(
        family=_InstructionFamily.ROUTINE_CALL,
        write_operand_index=0,
        write_relationship_type=RelationshipType.CALLS,
        target_kind="routine",
        notes=(
            "Jump to Subroutine: operand 0 is the target routine name. "
            "Operands 1..N are input/return parameters (TODO)."
        ),
        implemented=True,
    ),

    # ---- Comparisons ------------------------------------------------------
    # Comparisons read all of their operands (those that look like tags)
    # and gate the rung. The rung-condition implied by the comparison
    # is recorded in ``platform_specific["comparison_operator"]`` so
    # downstream consumers (Trace v2 / v3) can render natural language
    # without re-parsing the rung text.
    "EQU": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Equal: rung true when operand[0] == operand[1].",
        implemented=True,
    ),
    "NEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Not Equal.",
        implemented=True,
    ),
    "LES": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Less Than.",
        implemented=True,
    ),
    "LEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Less Than Or Equal.",
        implemented=True,
    ),
    "GRT": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Greater Than.",
        implemented=True,
    ),
    "GEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Greater Than Or Equal.",
        implemented=True,
    ),
    "LIM": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1, 2),
        notes=(
            "Limit Test: LIM(Low, Test, High). Rung is true while "
            "Low <= Test <= High (or High <= Test <= Low when Low > High)."
        ),
        implemented=True,
    ),

    # ---- Math -------------------------------------------------------------
    # Rockwell convention:
    #     ADD/SUB/MUL/DIV(Source_A, Source_B, Dest)  -> write index 2
    #     CPT(Dest, Expression)                       -> write index 0
    # The ladder parser does not crack the CPT expression today, so CPT
    # only contributes a WRITES edge for ``Dest``. ADD/SUB/MUL/DIV emit
    # READS for their two source operands when those look like tags.
    "ADD": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Addition: Dest = Source_A + Source_B.",
        implemented=True,
    ),
    "SUB": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Subtraction.",
        implemented=True,
    ),
    "MUL": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Multiplication.",
        implemented=True,
    ),
    "DIV": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Division.",
        implemented=True,
    ),
    "CPT": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        write_operand_index=0,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes=(
            "Compute: Dest = Expression. The expression operand is a "
            "quoted string the ladder parser does not crack; reads "
            "would need an ST-style expression pass. For now, only "
            "the destination WRITES is emitted."
        ),
        implemented=True,
    ),

    # ---- Move / copy ------------------------------------------------------
    "MOV": _InstructionSemantics(
        family=_InstructionFamily.MOVE_COPY,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.MOVES_VALUE,
        notes="Move: Dest = Source.",
        implemented=True,
    ),
    "MOVE": _InstructionSemantics(
        family=_InstructionFamily.MOVE_COPY,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.MOVES_VALUE,
        notes=(
            "Move (verbose mnemonic, ``MOVE(Source, Dest)``): same data "
            "flow as MOV. Seen in the logixlib numeric library as a "
            "self-move (``MOVE(X, X)``) that refreshes a string output "
            "after an AOI writes it. Treated as a genuine move-style "
            "opcode, NOT an AOI: no AddOnInstructionDefinition named MOVE "
            "exists in the corpus and the two-operand source/dest shape "
            "mirrors MOV exactly."
        ),
        implemented=True,
    ),
    "COP": _InstructionSemantics(
        family=_InstructionFamily.MOVE_COPY,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.MOVES_VALUE,
        notes="Copy File: COP(Source, Dest, Length).",
        implemented=True,
    ),

    # ---- One-shots --------------------------------------------------------
    # ONS storage bit is both read (previous scan) and written (current
    # scan); the rung is true for exactly one scan on the false->true
    # transition. OSR/OSF use a separate storage bit and a separate
    # output bit.
    "ONS": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=0,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot: pulses when rung transitions false->true.",
        implemented=True,
    ),
    "OSR": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot Rising: OSR(StorageBit, OutputBit).",
        implemented=True,
    ),
    "OSF": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot Falling: OSF(StorageBit, OutputBit).",
        implemented=True,
    ),

    # ---- PID / control loops (registered, not yet emitting edges) ---------
    # TODO(intelli/normalization): PID(PID_block, PV, Tieback, CV, ...) ties
    # together SP/PV/CV across the block. The reasoning layer needs to
    # break that into role-aware edges (PV READS, CV WRITES+CALCULATES,
    # SP CONDITION_FOR, etc.) once tag role inference is in place.
    "PID": _InstructionSemantics(
        family=_InstructionFamily.CONTROL_LOOP,
        notes="PID closed-loop control block.",
    ),

    # ---- No-operation -----------------------------------------------------
    # NOP is Rockwell's explicit no-operation placeholder (``NOP()`` with no
    # operands). It reads and writes nothing, so it is a *recognized* opcode
    # that deliberately emits no cause/effect edges. ``implemented=True`` so
    # it is not flagged in the unsupported-instruction inventory and the
    # grader stops counting it as unknown.
    "NOP": _InstructionSemantics(
        family=_InstructionFamily.NO_OP,
        notes="No Operation: recognized placeholder; reads/writes nothing.",
        implemented=True,
    ),
}


CONTROLLER_SCOPE_KEY = "__controller__"


def _unsupported_ladder_instruction_inventory(
    control_objects: list[ControlObject],
) -> dict[str, int]:
    """Count ladder instructions with no implemented semantic handler."""

    counts: Counter[str] = Counter()
    for o in control_objects:
        if o.object_type != ControlObjectType.INSTRUCTION:
            continue
        attrs = o.attributes or {}
        if attrs.get("language") != "ladder":
            continue
        if attrs.get("semantic_implemented"):
            continue
        key = str(attrs.get("instruction_type") or o.name or "?")
        counts[key] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_l5x_project(parsed_project: ControlProject) -> dict:
    """Convert a parsed L5X ``ControlProject`` into reasoning-schema lists.

    Pure function: does not mutate ``parsed_project``. All output models
    are freshly constructed.

    Returns:
        A dict with:
            * ``control_objects``    - list[ControlObject]
            * ``relationships``      - list[Relationship]
            * ``execution_contexts`` - list[ExecutionContext]
            * ``normalization_metadata`` - e.g. unsupported ladder inventory
    """

    control_objects: list[ControlObject] = []
    relationships: list[Relationship] = []
    execution_contexts: list[ExecutionContext] = []

    # Pre-pass: build a routine_index so JSR (and future cross-routine
    # references) can resolve targets regardless of iteration order.
    routine_index: dict[tuple[str, str, str], str] = _build_routine_index(
        parsed_project
    )
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]] = (
        _build_routine_param_index(parsed_project)
    )

    for controller in parsed_project.controllers:
        _normalize_controller(
            controller=controller,
            parsed_project=parsed_project,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            execution_contexts=execution_contexts,
        )

    return {
        "control_objects": control_objects,
        "relationships": relationships,
        "execution_contexts": execution_contexts,
        "normalization_metadata": {
            "unsupported_ladder_instruction_inventory": (
                _unsupported_ladder_instruction_inventory(control_objects)
            ),
        },
    }


# ---------------------------------------------------------------------------
# Per-controller normalization
# ---------------------------------------------------------------------------


def _normalize_controller(
    controller: ControlController,
    parsed_project: ControlProject,
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
) -> None:

    source_platform = controller.platform or "rockwell"
    controller_id = _controller_id(controller.name)

    # Tag lookup keyed by (scope_key, name). scope_key is the program name
    # for program tags or CONTROLLER_SCOPE_KEY for controller-scope tags.
    tag_index: dict[tuple[str, str], str] = {}

    # Vendor-neutral resolution maps consumed by ladder normalization:
    #   * aoi_defs_by_name  -> resolve AOI instance calls to LogicBlocks.
    #   * udt_defs_by_name  -> resolve UDT member accesses.
    #   * tag_type_map      -> tag name -> declared data type (UDT lookup).
    #   * aoi_body_routine_ids -> AOI name -> synthetic body-routine id
    #     for the optional instance -> body CALLS edge.
    # Keyed case-insensitively because rung mnemonics are upper-cased by
    # some exporters while the AOI definition keeps its declared casing.
    aoi_defs_by_name: dict[str, AddOnInstructionDef] = {
        d.name.upper(): d for d in controller.add_on_instruction_defs
    }
    udt_defs_by_name: dict[str, DataTypeDef] = {
        d.name: d for d in controller.data_type_defs
    }
    tag_type_map: dict[str, str] = {}
    aoi_body_routine_ids: dict[str, str] = {}

    # alias name -> base name, for annotating reads/writes that go through
    # an alias (the alias -> base REFERENCES edge is emitted separately).
    alias_map: dict[str, str] = {}
    for _t in controller.controller_tags:
        if _t.alias_for:
            alias_map[_t.name] = _t.alias_for
    for _prog in controller.programs:
        for _t in _prog.tags:
            if _t.alias_for:
                alias_map.setdefault(_t.name, _t.alias_for)

    aoi_body_routines: dict[str, ControlRoutine] = {}
    for prog in controller.programs:
        if not prog.name.startswith("__AOI__/"):
            continue
        aoi_name_upper = prog.name.split("/", 1)[1].upper()
        aoi_def = aoi_defs_by_name.get(aoi_name_upper)
        logic_name = aoi_def.logic_routine if aoi_def else None
        for routine in prog.routines:
            if logic_name is None or routine.name == logic_name:
                aoi_body_routines[aoi_name_upper] = routine
                break

    # Materialize one synthetic ROUTINE object per AOI definition that
    # exposes an internal logic routine, so instances can CALLS into the
    # body. Ladder (RLL) bodies are normalized into READS/WRITES here;
    # ST bodies remain stubbed until a future pass.
    for upper_name, aoi_def in aoi_defs_by_name.items():
        if not aoi_def.logic_routine:
            continue
        body_id = (
            f"routine::{controller.name}/__AOI__/{aoi_def.name}"
            f"/{aoi_def.logic_routine}"
        )
        aoi_body_routine_ids[upper_name] = body_id
        body_routine = aoi_body_routines.get(upper_name)
        is_ladder_body = (
            body_routine is not None
            and body_routine.language == "ladder"
            and body_routine.instructions
        )
        body_co = ControlObject(
            id=body_id,
            name=aoi_def.logic_routine,
            object_type=ControlObjectType.ROUTINE,
            source_platform=source_platform,
            source_location=(
                f"Controller:{controller.name}"
                f"/AddOnInstructionDefinition:{aoi_def.name}"
                f"/Routine:{aoi_def.logic_routine}"
            ),
            parent_ids=[controller_id],
            attributes={
                "is_aoi_body": True,
                "aoi_name": aoi_def.name,
                "language": body_routine.language if body_routine else None,
            },
            confidence=ConfidenceLevel.MEDIUM,
            platform_specific={
                "parse_status": (
                    "ok" if is_ladder_body else "aoi_body_not_extracted"
                ),
                "aoi_revision": aoi_def.revision,
            },
        )
        control_objects.append(body_co)
        if is_ladder_body and body_routine is not None:
            _normalize_aoi_ladder_body(
                controller_name=controller.name,
                controller_id=controller_id,
                aoi_def=aoi_def,
                body_routine=body_routine,
                body_id=body_id,
                body_co=body_co,
                source_platform=source_platform,
                tag_index=tag_index,
                routine_index=routine_index,
                routine_param_index=routine_param_index,
                control_objects=control_objects,
                relationships=relationships,
                execution_contexts=execution_contexts,
                aoi_defs=aoi_defs_by_name,
                udt_defs=udt_defs_by_name,
                tag_type_map=tag_type_map,
                aoi_body_routine_ids=aoi_body_routine_ids,
                alias_map=alias_map,
            )

    control_objects.append(
        ControlObject(
            id=controller_id,
            name=controller.name,
            object_type=ControlObjectType.CONTROLLER,
            source_platform=source_platform,
            source_location=f"Controller:{controller.name}",
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "rockwell_platform": controller.platform,
                "source_platform": source_platform,
                "source_file": parsed_project.source_file,
                "file_hash": parsed_project.file_hash,
                "project_metadata": parsed_project.metadata or {},
            },
        )
    )

    # Collected so alias -> base REFERENCES edges can be emitted after all
    # tags of the relevant scope exist. Each entry: (alias_tag, alias_id,
    # lookup_program_name).
    pending_alias_edges: list[tuple[ControlTag, str, str]] = []

    for tag in controller.controller_tags:
        tag_id = _tag_id(controller.name, CONTROLLER_SCOPE_KEY, tag.name)
        tag_index[(CONTROLLER_SCOPE_KEY, tag.name)] = tag_id
        if tag.data_type:
            tag_type_map.setdefault(tag.name, tag.data_type)

        control_objects.append(
            _tag_to_control_object(
                tag=tag,
                tag_id=tag_id,
                source_location=(
                    f"Controller:{controller.name}/Tag:{tag.name}"
                ),
                parent_ids=[controller_id],
                source_platform=source_platform,
            )
        )
        relationships.append(
            Relationship(
                source_id=controller_id,
                target_id=tag_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform=source_platform,
            )
        )
        if tag.alias_for:
            pending_alias_edges.append((tag, tag_id, CONTROLLER_SCOPE_KEY))

    # Controller-scope alias edges: base must be controller-scope.
    for alias_tag, alias_id, _scope in pending_alias_edges:
        _emit_alias_edge(
            alias_tag=alias_tag,
            alias_id=alias_id,
            controller_name=controller.name,
            program_name=CONTROLLER_SCOPE_KEY,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            source_platform=source_platform,
        )
    pending_alias_edges.clear()

    for program in controller.programs:
        # AOI bodies are normalized via the synthetic body-routine path
        # above; skip duplicate program/routine materialization.
        if program.name.startswith("__AOI__/"):
            continue

        program_id = _program_id(controller.name, program.name)
        program_loc = (
            f"Controller:{controller.name}/Program:{program.name}"
        )

        control_objects.append(
            ControlObject(
                id=program_id,
                name=program.name,
                object_type=ControlObjectType.PROGRAM,
                source_platform=source_platform,
                source_location=program_loc,
                parent_ids=[controller_id],
                confidence=ConfidenceLevel.HIGH,
            )
        )
        relationships.append(
            Relationship(
                source_id=controller_id,
                target_id=program_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform=source_platform,
            )
        )

        for tag in program.tags:
            tag_id = _tag_id(controller.name, program.name, tag.name)
            tag_index[(program.name, tag.name)] = tag_id
            if tag.data_type:
                tag_type_map.setdefault(tag.name, tag.data_type)

            control_objects.append(
                _tag_to_control_object(
                    tag=tag,
                    tag_id=tag_id,
                    source_location=f"{program_loc}/Tag:{tag.name}",
                    parent_ids=[program_id, controller_id],
                    source_platform=source_platform,
                )
            )
            relationships.append(
                Relationship(
                    source_id=program_id,
                    target_id=tag_id,
                    relationship_type=RelationshipType.CONTAINS,
                    confidence=ConfidenceLevel.HIGH,
                    source_platform=source_platform,
                )
            )
            if tag.alias_for:
                # Program-scope alias: base resolves program-scope first,
                # then controller-scope (handled by the resolver).
                _emit_alias_edge(
                    alias_tag=tag,
                    alias_id=tag_id,
                    controller_name=controller.name,
                    program_name=program.name,
                    tag_index=tag_index,
                    control_objects=control_objects,
                    relationships=relationships,
                    source_platform=source_platform,
                )

        for routine in program.routines:
            _normalize_routine(
                controller_name=controller.name,
                controller_id=controller_id,
                program_name=program.name,
                program_id=program_id,
                program_loc=program_loc,
                routine=routine,
                source_platform=source_platform,
                tag_index=tag_index,
                routine_index=routine_index,
                routine_param_index=routine_param_index,
                control_objects=control_objects,
                relationships=relationships,
                execution_contexts=execution_contexts,
                aoi_defs=aoi_defs_by_name,
                udt_defs=udt_defs_by_name,
                tag_type_map=tag_type_map,
                aoi_body_routine_ids=aoi_body_routine_ids,
                alias_map=alias_map,
            )


# ---------------------------------------------------------------------------
# Per-routine normalization
# ---------------------------------------------------------------------------


def _normalize_aoi_ladder_body(
    *,
    controller_name: str,
    controller_id: str,
    aoi_def: AddOnInstructionDef,
    body_routine: ControlRoutine,
    body_id: str,
    body_co: ControlObject,
    source_platform: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
    aoi_defs: dict[str, AddOnInstructionDef],
    udt_defs: dict[str, DataTypeDef],
    tag_type_map: dict[str, str],
    aoi_body_routine_ids: dict[str, str],
    alias_map: dict[str, str],
) -> None:
    """Normalize a ladder AOI logic routine into the synthetic body object."""

    program_name = f"__AOI__/{aoi_def.name}"
    routine_name = body_routine.name
    routine_loc = (
        f"Controller:{controller_name}"
        f"/AddOnInstructionDefinition:{aoi_def.name}"
        f"/Routine:{routine_name}"
    )
    exec_ctx_id = _exec_ctx_id(controller_name, program_name, routine_name)
    execution_contexts.append(
        ExecutionContext(
            id=exec_ctx_id,
            name=f"{routine_name} AOI body scan",
            context_type=ExecutionContextType.ROUTINE,
            description=f"AOI body scope for {routine_loc}",
            controller_id=controller_id,
            source_platform=source_platform,
            source_location=routine_loc,
            confidence=ConfidenceLevel.MEDIUM,
        )
    )
    attrs = dict(body_co.attributes or {})
    attrs["language"] = "ladder"
    attrs["instruction_count"] = len(body_routine.instructions)
    body_co.attributes = attrs
    ps = dict(body_co.platform_specific or {})
    ps["parse_status"] = "ok"
    ps["aoi_body_normalized"] = True
    body_co.platform_specific = ps
    body_co.confidence = ConfidenceLevel.HIGH

    _normalize_ladder_routine(
        controller_name=controller_name,
        controller_id=controller_id,
        program_name=program_name,
        program_id=controller_id,
        routine_name=routine_name,
        routine_id=body_id,
        routine_loc=routine_loc,
        instructions=list(body_routine.instructions),
        exec_ctx_id=exec_ctx_id,
        tag_index=tag_index,
        routine_index=routine_index,
        routine_param_index=routine_param_index,
        control_objects=control_objects,
        relationships=relationships,
        aoi_defs=aoi_defs,
        udt_defs=udt_defs,
        tag_type_map=tag_type_map,
        aoi_body_routine_ids=aoi_body_routine_ids,
        alias_map=alias_map,
    )


def _normalize_routine(
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    program_loc: str,
    routine,
    source_platform: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    udt_defs: Optional[dict[str, DataTypeDef]] = None,
    tag_type_map: Optional[dict[str, str]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
    alias_map: Optional[dict[str, str]] = None,
) -> None:

    aoi_defs = aoi_defs or {}
    udt_defs = udt_defs or {}
    tag_type_map = tag_type_map or {}
    aoi_body_routine_ids = aoi_body_routine_ids or {}
    alias_map = alias_map or {}

    routine_id = _routine_id(controller_name, program_name, routine.name)
    routine_loc = f"{program_loc}/Routine:{routine.name}"

    control_objects.append(
        ControlObject(
            id=routine_id,
            name=routine.name,
            object_type=ControlObjectType.ROUTINE,
            source_platform=source_platform,
            source_location=routine_loc,
            parent_ids=[program_id, controller_id],
            attributes={
                "language": routine.language,
                "instruction_count": len(routine.instructions),
            },
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "rockwell_metadata": routine.metadata or {},
                "platform_metadata": routine.metadata or {},
                "raw_logic_present": bool(routine.raw_logic),
                **(routine.metadata or {}),
            },
        )
    )
    relationships.append(
        Relationship(
            source_id=program_id,
            target_id=routine_id,
            relationship_type=RelationshipType.CONTAINS,
            confidence=ConfidenceLevel.HIGH,
            source_platform=source_platform,
        )
    )

    exec_ctx_id = _exec_ctx_id(controller_name, program_name, routine.name)
    execution_contexts.append(
        ExecutionContext(
            id=exec_ctx_id,
            name=f"{routine.name} routine scan",
            context_type=ExecutionContextType.ROUTINE,
            description=f"Routine scope for {routine_loc}",
            controller_id=controller_id,
            source_platform=source_platform,
            source_location=routine_loc,
            confidence=ConfidenceLevel.MEDIUM,
        )
    )

    if routine.parse_status == "preserved_only":
        routine_co = control_objects[-1]
        ps_r = dict(routine_co.platform_specific or {})
        ps_r["parse_status"] = "preserved_only"
        ps_r["raw_logic_present"] = bool(routine.raw_logic)
        if routine.metadata:
            ps_r["connector_routine_metadata"] = dict(routine.metadata)
        routine_co.platform_specific = ps_r
        routine_co.confidence = ConfidenceLevel.LOW
        return

    if routine.language == "ladder":
        _normalize_ladder_routine(
            controller_name=controller_name,
            controller_id=controller_id,
            program_name=program_name,
            program_id=program_id,
            routine_name=routine.name,
            routine_id=routine_id,
            routine_loc=routine_loc,
            instructions=list(routine.instructions),
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            aoi_defs=aoi_defs,
            udt_defs=udt_defs,
            tag_type_map=tag_type_map,
            aoi_body_routine_ids=aoi_body_routine_ids,
            alias_map=alias_map,
        )
        return

    if routine.language == "structured_text":
        _normalize_structured_text_routine(
            controller_name=controller_name,
            controller_id=controller_id,
            program_name=program_name,
            program_id=program_id,
            routine_name=routine.name,
            routine_id=routine_id,
            routine_loc=routine_loc,
            raw_logic=routine.raw_logic or "",
            instructions=list(routine.instructions),
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            aoi_defs=aoi_defs,
            aoi_body_routine_ids=aoi_body_routine_ids,
        )
        return

    # FBD / SFC: preserve routine + instructions with explicit unsupported
    # parse status (no vendor diagram parser yet).
    if routine.language in ("function_block", "sfc"):
        routine_co = control_objects[-1]
        ps_r = dict(routine_co.platform_specific)
        if routine.parse_status == "parsed" and routine.instructions:
            ps_r["parse_status"] = "parsed"
            routine_co.confidence = ConfidenceLevel.MEDIUM
        else:
            ps_r["parse_status"] = "unsupported_language"
            routine_co.confidence = ConfidenceLevel.LOW
        ps_r["raw_logic_present"] = bool(routine.raw_logic)
        ps_r["language"] = routine.language
        ps_r["schema_hints"] = {
            "fbd_object_types": [
                "function_block",
                "fbd_input_pin",
                "fbd_output_pin",
                "fbd_block_instance",
                "fbd_parameter_binding",
            ],
            "sfc_object_types": [
                "sfc_step",
                "sfc_transition",
                "sfc_action",
                "sfc_condition",
                "active_step_tag",
                "sequence_order",
            ],
        }
        routine_co.platform_specific = ps_r

    # Other non-ladder languages (FBD / SFC / unknown): still emit
    # instruction ControlObjects so the graph captures structure, but
    # do not emit cause/effect edges. See module-level TODOs.
    for instruction in routine.instructions:
        instr_id = _instruction_id(
            controller_name=controller_name,
            program_name=program_name,
            routine_name=routine.name,
            rung_number=None,
            instruction=instruction,
        )
        low = (
            ConfidenceLevel.LOW
            if routine.language in ("function_block", "sfc")
            else None
        )
        control_objects.append(
            _instruction_to_control_object(
                instruction=instruction,
                instr_id=instr_id,
                source_location=(
                    f"{routine_loc}/Instr:"
                    f"{instruction.id or instruction.instruction_type}"
                ),
                parent_ids=[routine_id, program_id, controller_id],
                confidence_override=low,
                source_platform=source_platform,
            )
        )
        instr_obj = control_objects[-1]
        _apply_explicit_fbd_sfc_object_type(instr_obj, instruction, routine.language)
        relationships.append(
            Relationship(
                source_id=routine_id,
                target_id=instr_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform=source_platform,
            )
        )
        _emit_explicit_fbd_sfc_relationships(
            instruction=instruction,
            instr_id=instr_id,
            relationships=relationships,
            source_platform=source_platform,
        )


# ---------------------------------------------------------------------------
# Ladder rung / instruction handling
# ---------------------------------------------------------------------------


@dataclass
class _RungContext:
    """Bundle of state passed to instruction family handlers.

    The handlers append to ``control_objects`` / ``relationships`` and
    read from the resolver indices. Keeping the bundle as a single
    object keeps handler signatures short and consistent.

    ``rung_has_branches`` / ``rung_branch_count`` are derived once per
    rung from the raw rung text (``BST`` / ``NXB`` / ``BND`` tokens)
    and propagated onto every emitted relationship's
    ``platform_specific`` so consumers can tell when a rung has
    parallel branches without re-scanning the source. We deliberately
    do **not** attribute individual instructions to specific branches
    -- that is a follow-up "branch-aware analyzer" task.
    """

    controller_name: str
    program_name: str
    rung_id: str
    rung_loc: str
    rung_raw_text: Optional[str]
    exec_ctx_id: str
    tag_index: dict[tuple[str, str], str]
    routine_index: dict[tuple[str, str, str], str]
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]]
    control_objects: list[ControlObject] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    rung_has_branches: bool = False
    rung_branch_count: int = 1
    branch_warnings: list[str] = field(default_factory=list)
    logic_expression: LogicExpression | None = None
    logic_expression_resolved: bool = False
    logic_expression_warnings: list[str] = field(default_factory=list)
    # Vendor-neutral resolution maps (AOI / UDT / alias enrichment).
    aoi_defs: dict[str, AddOnInstructionDef] = field(default_factory=dict)
    udt_defs: dict[str, DataTypeDef] = field(default_factory=dict)
    tag_type_map: dict[str, str] = field(default_factory=dict)
    aoi_body_routine_ids: dict[str, str] = field(default_factory=dict)
    alias_map: dict[str, str] = field(default_factory=dict)


def _normalize_ladder_routine(
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    routine_name: str,
    routine_id: str,
    routine_loc: str,
    instructions: list[ControlInstruction],
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    udt_defs: Optional[dict[str, DataTypeDef]] = None,
    tag_type_map: Optional[dict[str, str]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
    alias_map: Optional[dict[str, str]] = None,
) -> None:

    aoi_defs = aoi_defs or {}
    udt_defs = udt_defs or {}
    tag_type_map = tag_type_map or {}
    aoi_body_routine_ids = aoi_body_routine_ids or {}
    alias_map = alias_map or {}

    rungs_by_number: dict[int, list[ControlInstruction]] = {}
    for instruction in instructions:
        rung_number = (
            instruction.rung_number
            if instruction.rung_number is not None
            else -1
        )
        rungs_by_number.setdefault(rung_number, []).append(instruction)

    for rung_number in sorted(rungs_by_number):
        rung_instructions = rungs_by_number[rung_number]
        rung_id = _rung_id(
            controller_name, program_name, routine_name, rung_number
        )
        rung_loc = f"{routine_loc}/Rung[{rung_number}]"
        # Prefer the original rung text (preserved in instruction
        # metadata by the ladder parser) so branch markers ``BST`` /
        # ``NXB`` / ``BND`` are visible to ``_detect_rung_branches``;
        # the tokenizer otherwise drops them because they're bare
        # keywords without parentheses. Fall back to the joined
        # instruction tokens for hand-built fixtures and any future
        # parser that doesn't supply the metadata key.
        rung_raw_text = next(
            (
                (i.metadata or {}).get("rung_text")
                for i in rung_instructions
                if (i.metadata or {}).get("rung_text")
            ),
            None,
        )
        if not rung_raw_text:
            rung_raw_text = " ".join(
                i.raw_text for i in rung_instructions if i.raw_text
            ).strip() or None

        # Branch detection runs once per rung; the same result feeds
        # the rung ControlObject's metadata and every emitted
        # cause/effect relationship below (via ``_RungContext``).
        rung_has_branches, rung_branch_count = _detect_rung_branches(
            rung_raw_text
        )
        branch_warnings: list[str] = []
        if (
            rung_raw_text
            and _BRANCH_BST_RE.search(rung_raw_text)
            and not _BRANCH_BND_RE.search(rung_raw_text)
        ):
            branch_warnings.append("missing_bnd_after_bst")

        logic_expr, logic_warnings, logic_resolved = (
            build_rung_logic_expression(
                rung_instructions,
                rung_raw_text=rung_raw_text,
                rung_number=rung_number,
            )
        )
        logic_expr_text = logic_expression_to_text(logic_expr)

        control_objects.append(
            ControlObject(
                id=rung_id,
                name=f"Rung[{rung_number}]",
                object_type=ControlObjectType.RUNG,
                source_platform="rockwell",
                source_location=rung_loc,
                parent_ids=[routine_id, program_id, controller_id],
                attributes={
                    "rung_number": rung_number,
                    "instruction_count": len(rung_instructions),
                    "has_branches": rung_has_branches,
                    "branch_count": rung_branch_count,
                    "logic_expression_resolved": logic_resolved,
                },
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "raw_rung_text": rung_raw_text,
                    "rung_has_branches": rung_has_branches,
                    "rung_branch_count": rung_branch_count,
                    "branch_warnings": branch_warnings,
                    "logic_expression_text": logic_expr_text,
                    "logic_expression_warnings": logic_warnings,
                },
            )
        )
        relationships.append(
            Relationship(
                source_id=routine_id,
                target_id=rung_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform="rockwell",
            )
        )

        # Construct the per-rung handler context once. The mutable lists
        # are shared with the caller so handlers append directly. The
        # branch flags computed above are passed through unchanged so
        # every emitted relationship can carry the rung-level flag
        # without per-handler rescanning.
        rung_ctx = _RungContext(
            controller_name=controller_name,
            program_name=program_name,
            rung_id=rung_id,
            rung_loc=rung_loc,
            rung_raw_text=rung_raw_text,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            rung_has_branches=rung_has_branches,
            rung_branch_count=rung_branch_count,
            branch_warnings=branch_warnings,
            logic_expression=logic_expr,
            logic_expression_resolved=logic_resolved,
            logic_expression_warnings=logic_warnings,
            aoi_defs=aoi_defs,
            udt_defs=udt_defs,
            tag_type_map=tag_type_map,
            aoi_body_routine_ids=aoi_body_routine_ids,
            alias_map=alias_map,
        )

        for instruction in rung_instructions:
            instr_id = _instruction_id(
                controller_name=controller_name,
                program_name=program_name,
                routine_name=routine_name,
                rung_number=rung_number,
                instruction=instruction,
            )
            control_objects.append(
                _instruction_to_control_object(
                    instruction=instruction,
                    instr_id=instr_id,
                    source_location=(
                        f"{rung_loc}/Instr:"
                        f"{instruction.id or instruction.instruction_type}"
                    ),
                    parent_ids=[
                        rung_id, routine_id, program_id, controller_id
                    ],
                )
            )
            relationships.append(
                Relationship(
                    source_id=rung_id,
                    target_id=instr_id,
                    relationship_type=RelationshipType.CONTAINS,
                    confidence=ConfidenceLevel.HIGH,
                    source_platform="rockwell",
                )
            )

            # AOI instance calls are resolved as universal LogicBlocks
            # (retype the instruction object to FUNCTION_BLOCK + emit
            # typed parameter-binding edges). Everything else flows
            # through the registry-driven dispatcher.
            aoi_def = rung_ctx.aoi_defs.get(
                instruction.instruction_type.upper()
            )
            if aoi_def is not None:
                _handle_aoi_instance(
                    instruction=instruction,
                    instr_id=instr_id,
                    instr_obj=control_objects[-1],
                    aoi_def=aoi_def,
                    ctx=rung_ctx,
                )
            else:
                _dispatch_instruction_semantics(
                    instruction=instruction,
                    ctx=rung_ctx,
                )

        if logic_expr is not None:
            _attach_rung_logic_expression(
                relationships=relationships,
                rung_id=rung_id,
                logic_expression=logic_expr,
                logic_resolved=logic_resolved,
            )


def _attach_rung_logic_expression(
    *,
    relationships: list[Relationship],
    rung_id: str,
    logic_expression: LogicExpression,
    logic_resolved: bool,
) -> None:
    """Copy the rung's structured gating tree onto cause/effect edges."""

    for idx, rel in enumerate(relationships):
        if rel.source_id != rung_id:
            continue
        if rel.relationship_type == RelationshipType.CONTAINS:
            continue
        updates: dict = {"logic_expression": logic_expression}
        if logic_resolved:
            ps = dict(rel.platform_specific or {})
            ps["logic_expression_resolved"] = True
            updates["platform_specific"] = ps
        relationships[idx] = rel.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Structured Text routine normalization
# ---------------------------------------------------------------------------
#
# Each top-level ST block (assignment / IF / CASE / complex) becomes
# one synthetic ``ControlObject`` of type ``INSTRUCTION`` carrying
# ``attributes["language"]="structured_text"`` and a stable
# ``source_location`` of the form
# ``<routine_loc>/Statement[<idx>]``. WRITES and READS hang off that
# synthetic source so Trace v1/v2 see all gating conditions and the
# write as siblings of the same statement (just like ladder rungs).
#
# For each block we set ``platform_specific`` fields per spec:
#
#   language:            "structured_text"
#   raw_text:            the original ST snippet
#   statement_type:      "assignment" | "if" | "case" | "complex"
#   assigned_value:      "TRUE" / "FALSE" / "(boolean expression)" /
#                        None (for too-complex)
#   extracted_conditions: list of {tag, required_value, source} dicts
#   st_parse_status:     "ok" | "too_complex"
#
# ``write_behavior`` is set to ``SETS_TRUE`` / ``SETS_FALSE`` when the
# RHS is a boolean literal; otherwise it is left ``None`` and the
# raw expression is recorded in ``platform_specific["assigned_value"]``.
# ---------------------------------------------------------------------------


_ST_LANGUAGE_KEY = "structured_text"


def _normalize_structured_text_routine(
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    routine_name: str,
    routine_id: str,
    routine_loc: str,
    raw_logic: str,
    instructions: list[ControlInstruction],
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
) -> None:
    """Convert an ST routine into per-block WRITES / READS edges.

    We deliberately drive normalization off ``raw_logic`` rather than
    ``instructions``: the existing per-instruction list produced by
    :mod:`app.parsers.structured_text` mixes IF / ASSIGN / END_IF as
    flat siblings, which loses the block structure required to
    associate a body assignment with its enclosing IF condition.
    Re-parsing the raw text gives us the block tree directly.

    The per-instruction ControlObjects + CONTAINS edges are still
    emitted (after the block pass) so the graph keeps its existing
    structural surface area for tag discovery and explorer UIs.
    """

    blocks = parse_structured_text_blocks(raw_logic) if raw_logic else []
    aoi_defs = aoi_defs or {}
    aoi_body_routine_ids = aoi_body_routine_ids or {}

    for block in blocks:
        _normalize_st_block(
            block=block,
            controller_name=controller_name,
            controller_id=controller_id,
            program_name=program_name,
            program_id=program_id,
            routine_name=routine_name,
            routine_id=routine_id,
            routine_loc=routine_loc,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            aoi_defs=aoi_defs,
            aoi_body_routine_ids=aoi_body_routine_ids,
        )

    # Preserve the existing per-instruction structural surface so the
    # explorer UI and tag-discovery passes keep working. These objects
    # do NOT carry cause/effect edges (those come from the block pass
    # above); they exist purely to mirror the parser's instruction list.
    for instruction in instructions:
        instr_id = _instruction_id(
            controller_name=controller_name,
            program_name=program_name,
            routine_name=routine_name,
            rung_number=None,
            instruction=instruction,
        )
        control_objects.append(
            _instruction_to_control_object(
                instruction=instruction,
                instr_id=instr_id,
                source_location=(
                    f"{routine_loc}/Instr:"
                    f"{instruction.id or instruction.instruction_type}"
                ),
                parent_ids=[routine_id, program_id, controller_id],
            )
        )
        relationships.append(
            Relationship(
                source_id=routine_id,
                target_id=instr_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform="rockwell",
            )
        )


def _normalize_st_block(
    block: STBlock,
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    routine_name: str,
    routine_id: str,
    routine_loc: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
) -> None:
    """Dispatch on block type and emit the synthetic statement + edges."""

    aoi_defs = aoi_defs or {}
    aoi_body_routine_ids = aoi_body_routine_ids or {}

    statement_id, statement_loc = _make_st_statement_object(
        controller_name=controller_name,
        controller_id=controller_id,
        program_name=program_name,
        program_id=program_id,
        routine_name=routine_name,
        routine_id=routine_id,
        routine_loc=routine_loc,
        block=block,
        control_objects=control_objects,
        relationships=relationships,
    )

    if isinstance(block, STAssignment):
        _emit_st_assignment_edges(
            assignment=block,
            extra_conditions=[],
            condition_source="rhs",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type="assignment",
            block_raw_text=block.raw_text,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        return

    if isinstance(block, STIfBlock):
        _emit_st_if_edges(
            block=block,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            aoi_defs=aoi_defs,
            aoi_body_routine_ids=aoi_body_routine_ids,
        )
        return

    if isinstance(block, STReturnBlock):
        _emit_st_return_edges(
            block=block,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        return

    if isinstance(block, STIfElsifChain):
        _emit_st_if_elsif_chain_edges(
            block=block,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        return

    if isinstance(block, STLoopBlock):
        _emit_st_loop_edges(
            block=block,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        return

    if isinstance(block, STFbInvocation):
        aoi_def = aoi_defs.get(block.callee_name.upper())
        if aoi_def is not None:
            _emit_st_aoi_invocation_edges(
                block=block,
                statement_id=statement_id,
                statement_obj=control_objects[-1],
                statement_loc=statement_loc,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                aoi_def=aoi_def,
                aoi_body_routine_ids=aoi_body_routine_ids,
                control_objects=control_objects,
                relationships=relationships,
            )
        else:
            _emit_st_fb_invocation_edges(
                block=block,
                statement_id=statement_id,
                statement_loc=statement_loc,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                routine_index=routine_index,
                routine_param_index=routine_param_index,
                control_objects=control_objects,
                relationships=relationships,
            )
        return

    if isinstance(block, STCaseBlock):
        _emit_st_case_edges(
            block=block,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            aoi_defs=aoi_defs,
            aoi_body_routine_ids=aoi_body_routine_ids,
        )
        return

    # STComplexBlock: emit no cause/effect edges, but the statement
    # ControlObject already carries ``st_parse_status="too_complex"``
    # so trace explorers can surface the unparsed text to the user.


def _make_st_statement_object(
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    routine_name: str,
    routine_id: str,
    routine_loc: str,
    block: STBlock,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> tuple[str, str]:
    """Append a synthetic ST-statement ControlObject and return its id+loc.

    Type is ``INSTRUCTION`` rather than ``RUNG`` because (1) ST has no
    rungs and (2) Trace v2's ST-writer predicate explicitly excludes
    ``RUNG`` sources -- emitting as INSTRUCTION with
    ``attributes["language"]="structured_text"`` is what lets v2 pick
    these writers up as ST, not ladder.
    """

    idx = block.statement_index
    statement_id = (
        f"stmt::{controller_name}/{program_name}/{routine_name}"
        f"/Statement[{idx}]"
    )
    statement_loc = f"{routine_loc}/Statement[{idx}]"

    statement_type, parse_status, raw_text = _st_block_summary(block)

    control_objects.append(
        ControlObject(
            id=statement_id,
            name=f"Statement[{idx}]",
            object_type=ControlObjectType.INSTRUCTION,
            source_platform="rockwell",
            source_location=statement_loc,
            parent_ids=[routine_id, program_id, controller_id],
            attributes={
                "language": _ST_LANGUAGE_KEY,
                "statement_type": statement_type,
                "statement_index": idx,
            },
            confidence=(
                ConfidenceLevel.HIGH
                if parse_status == "ok"
                else ConfidenceLevel.LOW
            ),
            platform_specific={
                "language": _ST_LANGUAGE_KEY,
                "statement_type": statement_type,
                "st_parse_status": parse_status,
                "raw_text": raw_text,
            },
        )
    )
    stmt_obj = control_objects[-1]
    if isinstance(block, STLoopBlock) and block.for_metadata:
        ps = dict(stmt_obj.platform_specific)
        fm = block.for_metadata
        ps["for_loop"] = {
            "loop_var": fm.loop_var,
            "start": fm.start_expr,
            "end": fm.end_expr,
            "step": fm.step_expr,
            "display": fm.display,
        }
        stmt_obj.platform_specific = ps
    if isinstance(block, (STComplexBlock, STFbInvocation)):
        ps = dict(stmt_obj.platform_specific)
        if isinstance(block, STComplexBlock) and block.fragment_kind:
            ps["fragment_kind"] = block.fragment_kind
        if isinstance(block, STComplexBlock) and block.callee_name:
            ps["callee_name"] = block.callee_name
        if isinstance(block, STFbInvocation):
            ps["callee_name"] = block.callee_name
            ps["parameter_count"] = len(block.parameters)
        stmt_obj.platform_specific = ps
    relationships.append(
        Relationship(
            source_id=routine_id,
            target_id=statement_id,
            relationship_type=RelationshipType.CONTAINS,
            confidence=ConfidenceLevel.HIGH,
            source_platform="rockwell",
        )
    )
    return statement_id, statement_loc


def _st_block_summary(block: STBlock) -> tuple[str, str, str]:
    """Return ``(statement_type, parse_status, raw_text)`` for a block."""

    if isinstance(block, STAssignment):
        status = "too_complex" if block.too_complex else "ok"
        return "assignment", status, block.raw_text
    if isinstance(block, STIfBlock):
        status = (
            "too_complex"
            if block.too_complex_condition
            else "ok"
        )
        for assign in block.then_assignments + block.else_assignments:
            if assign.too_complex:
                status = "too_complex"
                break
        for nested in block.then_nested_ifs + block.else_nested_ifs:
            _, nested_status, _ = _st_block_summary(nested)
            if nested_status == "too_complex":
                status = "too_complex"
                break
        for fb in block.then_fb_calls + block.else_fb_calls:
            if fb.too_complex_parameters:
                status = "too_complex"
                break
        return "if", status, block.raw_text
    if isinstance(block, STReturnBlock):
        status = "too_complex" if block.too_complex_value else "ok"
        return "return", status, block.raw_text
    if isinstance(block, STCaseBlock):
        status = (
            "too_complex"
            if block.too_complex_selector or not block.branches
            else "ok"
        )
        return "case", status, block.raw_text
    if isinstance(block, STIfElsifChain):
        bad = False
        for cond, _ in block.branches:
            if cond:
                ex = parse_st_expression(cond)
                if ex.too_complex:
                    bad = True
        return "if_elsif_chain", ("too_complex" if bad else "ok"), block.raw_text
    if isinstance(block, STLoopBlock):
        status = "too_complex" if block.too_complex_body else "ok"
        if block.too_complex_condition:
            status = "too_complex"
        if not block.too_complex_body:
            for assign in block.body_assignments:
                if assign.too_complex:
                    status = "too_complex"
                    break
        return f"loop_{block.loop_kind.lower()}", status, block.raw_text
    if isinstance(block, STFbInvocation):
        status = "too_complex" if block.too_complex_parameters else "ok"
        return "fb_invocation", status, block.raw_text
    if isinstance(block, STComplexBlock):
        if block.fragment_kind and block.fragment_kind.startswith("loop_"):
            return "loop", "too_complex", block.raw_text
        return "complex", "too_complex", block.raw_text
    return "complex", "too_complex", getattr(block, "raw_text", "")


# ---------------------------------------------------------------------------
# Edge emitters per block type
# ---------------------------------------------------------------------------


# ``_STReadPlan`` describes one would-be READS edge plus the metadata
# needed to render it. We collect plans first so the emitter can
# dedupe, attach OR-branch indices, and merge platform_specific keys
# in a single pass.
@dataclass(frozen=True)
class _STReadPlan:
    target_tag: str
    examined_value: bool
    # ``comparison_operator`` is non-None only for comparison-term
    # reads (e.g. "A > 5"). For identifier reads it stays None.
    comparison_operator: Optional[str] = None
    compared_with: Optional[str] = None


@dataclass(frozen=True)
class _STBranchPlan:
    or_branch_index: int
    total_or_branches: int
    reads: list[_STReadPlan]

    def to_extracted(self, source_label: str) -> list[dict]:
        """Render every read in this branch as a ``extracted_conditions``
        entry the WRITES carries in ``platform_specific``.
        """

        rows: list[dict] = []
        for r in self.reads:
            row: dict = {
                "tag": r.target_tag,
                "required_value": r.examined_value,
                "source": source_label,
            }
            if self.total_or_branches > 1:
                row["or_branch_index"] = self.or_branch_index
            if r.comparison_operator is not None:
                row["comparison_operator"] = r.comparison_operator
                row["compared_with"] = r.compared_with
            rows.append(row)
        return rows


def _plan_st_reads_from_conditions(
    conditions: list[STCondition],
) -> list[_STBranchPlan]:
    """Treat a legacy flat-conjunction as a single-branch plan."""

    if not conditions:
        return []
    return [
        _STBranchPlan(
            or_branch_index=0,
            total_or_branches=1,
            reads=[
                _STReadPlan(
                    target_tag=c.tag,
                    examined_value=c.required_value,
                )
                for c in conditions
            ],
        )
    ]


def _plan_st_reads_from_expression(
    expression: Optional[STExpressionParse],
) -> list[_STBranchPlan]:
    """Decompose an :class:`STExpressionParse` into READ plans.

    One plan per OR branch. Each plan contains every term in that
    branch projected onto its READ contribution:

    * ``STCondition`` -> identifier read with ``examined_value``.
    * ``STComparisonTerm`` -> one read per tag-shaped operand, with
      ``comparison_operator`` recorded so the emitter can render the
      relationship without re-parsing the source.

    Too-complex expressions yield no plans (the caller is expected to
    set ``st_parse_status=too_complex`` on the WRITES it emits).
    """

    if expression is None or expression.too_complex:
        return []
    total = len(expression.branches)
    if total == 0:
        return []
    plans: list[_STBranchPlan] = []
    for idx, branch in enumerate(expression.branches):
        reads: list[_STReadPlan] = []
        for term in branch.terms:
            if isinstance(term, STCondition):
                reads.append(
                    _STReadPlan(
                        target_tag=term.tag,
                        examined_value=term.required_value,
                    )
                )
                continue
            if isinstance(term, STComparisonTerm):
                # LHS is always a tag (the parser guarantees this);
                # RHS is a tag only when it parses as an identifier.
                reads.append(
                    _STReadPlan(
                        target_tag=term.lhs,
                        # Comparison terms don't have a single
                        # boolean examined_value -- they're true when
                        # the comparison holds. We default the
                        # examined_value to True (the term must hold
                        # for the conjunction to be true) and rely on
                        # ``comparison_operator`` + ``compared_with``
                        # to carry the rest of the meaning.
                        examined_value=True,
                        comparison_operator=term.operator,
                        compared_with=term.rhs,
                    )
                )
                if term.rhs_is_tag:
                    reads.append(
                        _STReadPlan(
                            target_tag=term.rhs,
                            examined_value=True,
                            comparison_operator=term.operator,
                            compared_with=term.lhs,
                        )
                    )
        plans.append(
            _STBranchPlan(
                or_branch_index=idx,
                total_or_branches=total,
                reads=reads,
            )
        )
    return plans


def _aggregate_gating_logic(
    *,
    gating_expression: Optional[STExpressionParse],
    rhs_expression: Optional[STExpressionParse],
    legacy_conditions: list[STCondition],
    too_complex: bool,
) -> str:
    """Pick one of ``"and"`` / ``"or"`` / ``"and_or"`` /
    ``"comparison"`` / ``"too_complex"`` to summarize how the
    inputs to this assignment combine.
    """

    if too_complex:
        return "too_complex"
    shapes: list[str] = []
    if gating_expression is not None and not gating_expression.too_complex:
        shapes.append(gating_expression.gating_logic_type)
    elif legacy_conditions:
        shapes.append("and")
    if rhs_expression is not None and not rhs_expression.too_complex:
        shapes.append(rhs_expression.gating_logic_type)
    if not shapes:
        return "and"
    if "comparison" in shapes:
        return "comparison"
    if "and_or" in shapes:
        return "and_or"
    if "or" in shapes:
        return "or"
    return "and"


def _emit_st_assignment_edges(
    assignment: STAssignment,
    extra_conditions: list[STCondition],
    condition_source: str,
    statement_id: str,
    statement_loc: str,
    statement_type: str,
    block_raw_text: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    branch_label: Optional[str] = None,
    gating_expression: Optional[STExpressionParse] = None,
    case_condition_summary: Optional[str] = None,
) -> None:
    """Emit WRITES (1) + READS (per condition tag, deduped) for one ST
    assignment.

    ``extra_conditions`` is the *legacy* flat-conjunction view of the
    enclosing block's gating condition. It is preserved so existing
    callers and tests that assume identifier-only conjunctions
    continue to work unchanged. When the enclosing block has a
    richer condition (``OR`` / comparisons / parens),
    ``gating_expression`` carries the full DNF parse and supersedes
    ``extra_conditions`` for READS emission.

    ``case_condition_summary``, when given (e.g. ``"State = 1"`` for
    a CASE branch), is recorded on both the WRITES and every READS
    edge so consumers can render the branch in natural language.
    """

    # ---- Resolve target & basic write metadata --------------------------
    target_id = _resolve_tag_id_or_stub(
        operand=assignment.target,
        controller_name=controller_name,
        program_name=program_name,
        tag_index=tag_index,
        control_objects=control_objects,
    )
    write_behavior = _write_behavior_for_assignment(assignment)
    assigned_value_meta = _assigned_value_meta(assignment)

    # ---- Plan the gating reads ------------------------------------------
    # Two sources of conditions feed a single assignment:
    #   1. ``extra_conditions`` (or ``gating_expression``): inherited
    #      from the enclosing block (IF / CASE / ELSE).
    #   2. The assignment's own RHS expression.
    # Each source is processed independently into a list of "branch
    # plans" (one entry per OR branch). Identifier terms produce
    # READS with an ``examined_value``; comparison terms produce
    # READS for tag-shaped operands with a ``comparison_operator``.
    gating_plans = _plan_st_reads_from_expression(
        gating_expression
    ) if gating_expression is not None else (
        _plan_st_reads_from_conditions(extra_conditions)
    )
    rhs_plans = _plan_st_reads_from_expression(assignment.expression)
    if not rhs_plans and assignment.arithmetic_operands:
        rhs_plans = [
            _STBranchPlan(
                or_branch_index=0,
                total_or_branches=1,
                reads=[
                    _STReadPlan(target_tag=tag, examined_value=True)
                    for tag in assignment.arithmetic_operands
                ],
            )
        ]

    too_complex_rhs = (
        assignment.too_complex and assignment.assigned_value is None
    )
    gating_too_complex = (
        gating_expression is not None and gating_expression.too_complex
    )
    write_too_complex = (
        (too_complex_rhs and not gating_plans)
        or gating_too_complex
    )
    write_parse_status = (
        "too_complex" if write_too_complex else "ok"
    )

    # ---- Aggregate gating_logic_type ------------------------------------
    # Used by consumers (and tests) to tell apart pure ANDs, ORs,
    # comparison-bearing expressions, and too-complex fallbacks.
    gating_logic_type = _aggregate_gating_logic(
        gating_expression=gating_expression,
        rhs_expression=assignment.expression,
        legacy_conditions=extra_conditions,
        too_complex=write_too_complex,
    )

    # ---- Build the WRITES platform_specific -----------------------------
    extracted: list[dict] = []
    for plan in gating_plans:
        extracted.extend(plan.to_extracted(condition_source))
    for plan in rhs_plans:
        extracted.extend(plan.to_extracted("rhs"))

    write_platform: dict = {
        "language": _ST_LANGUAGE_KEY,
        "raw_text": assignment.raw_text,
        "statement_type": statement_type,
        "assigned_value": assigned_value_meta,
        "extracted_conditions": extracted,
        "st_parse_status": write_parse_status,
        "instruction_type": "ST_ASSIGN",
        "gating_logic_type": gating_logic_type,
    }
    if branch_label is not None:
        write_platform["branch_label"] = branch_label
    if case_condition_summary is not None:
        write_platform["case_condition_summary"] = case_condition_summary
    if block_raw_text and block_raw_text != assignment.raw_text:
        write_platform["block_raw_text"] = block_raw_text

    relationships.append(
        Relationship(
            source_id=statement_id,
            target_id=target_id,
            relationship_type=RelationshipType.WRITES,
            write_behavior=write_behavior,
            execution_context_id=exec_ctx_id,
            logic_condition=block_raw_text or assignment.raw_text,
            source_platform="rockwell",
            source_location=statement_loc,
            confidence=(
                ConfidenceLevel.HIGH
                if write_parse_status == "ok"
                else ConfidenceLevel.LOW
            ),
            platform_specific=write_platform,
        )
    )

    # ---- Emit READS edges ------------------------------------------------
    # Plans already carry their (tag, examined_value, branch_index)
    # tuples, so we just dedupe and emit. Dedup key intentionally
    # includes the OR-branch index: the same tag appearing in two
    # different OR branches produces two READS so consumers can
    # see the branch structure.
    seen_keys: set[tuple] = set()
    for plan_idx, plan_set in enumerate(
        [(gating_plans, condition_source), (rhs_plans, "rhs")]
    ):
        plans, source_label = plan_set
        del plan_idx  # iteration index, unused
        for plan in plans:
            for read in plan.reads:
                key = (
                    read.target_tag,
                    read.examined_value,
                    plan.or_branch_index,
                    source_label,
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                cond_target_id = _resolve_tag_id_or_stub(
                    operand=read.target_tag,
                    controller_name=controller_name,
                    program_name=program_name,
                    tag_index=tag_index,
                    control_objects=control_objects,
                )
                cond_platform: dict = {
                    "language": _ST_LANGUAGE_KEY,
                    "statement_type": statement_type,
                    "examined_value": read.examined_value,
                    # XIC/XIO mirror keeps Trace v2's ladder
                    # condition aggregator working unchanged for
                    # simple identifier reads. Comparison reads
                    # use ``ST_COMPARE`` so v2 skips them rather
                    # than rendering them as XIC/XIO gates.
                    "instruction_type": (
                        "ST_COMPARE"
                        if read.comparison_operator is not None
                        else ("XIC" if read.examined_value else "XIO")
                    ),
                    "condition_source": source_label,
                    "raw_text": assignment.raw_text,
                }
                # Only annotate OR-branch index when more than one
                # branch exists (the simple single-conjunction case
                # stays compact for existing tests / consumers).
                if plan.total_or_branches > 1:
                    cond_platform["or_branch_index"] = (
                        plan.or_branch_index
                    )
                    cond_platform["or_branch_count"] = (
                        plan.total_or_branches
                    )
                if read.comparison_operator is not None:
                    cond_platform["comparison_operator"] = (
                        read.comparison_operator
                    )
                    cond_platform["compared_with"] = read.compared_with
                    cond_platform["gating_kind"] = "comparison"
                if branch_label is not None:
                    cond_platform["branch_label"] = branch_label
                if case_condition_summary is not None:
                    cond_platform["case_condition_summary"] = (
                        case_condition_summary
                    )
                relationships.append(
                    Relationship(
                        source_id=statement_id,
                        target_id=cond_target_id,
                        relationship_type=RelationshipType.READS,
                        execution_context_id=exec_ctx_id,
                        logic_condition=(
                            block_raw_text or assignment.raw_text
                        ),
                        source_platform="rockwell",
                        source_location=statement_loc,
                        confidence=ConfidenceLevel.HIGH,
                        platform_specific=cond_platform,
                    )
                )


def _emit_st_reads_from_plans(
    *,
    plans: list[_STBranchPlan],
    condition_source: str,
    statement_id: str,
    statement_loc: str,
    statement_type: str,
    logic_condition: str,
    exec_ctx_id: str,
    controller_name: str,
    program_name: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Emit READS edges from pre-built branch plans."""

    seen_keys: set[tuple] = set()
    for plan in plans:
        for read in plan.reads:
            key = (
                read.target_tag,
                read.examined_value,
                plan.or_branch_index,
                condition_source,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            cond_target_id = _resolve_tag_id_or_stub(
                operand=read.target_tag,
                controller_name=controller_name,
                program_name=program_name,
                tag_index=tag_index,
                control_objects=control_objects,
            )
            cond_platform: dict = {
                "language": _ST_LANGUAGE_KEY,
                "statement_type": statement_type,
                "examined_value": read.examined_value,
                "instruction_type": (
                    "ST_COMPARE"
                    if read.comparison_operator is not None
                    else ("XIC" if read.examined_value else "XIO")
                ),
                "condition_source": condition_source,
                "raw_text": logic_condition,
            }
            if plan.total_or_branches > 1:
                cond_platform["or_branch_index"] = plan.or_branch_index
                cond_platform["or_branch_count"] = plan.total_or_branches
            if read.comparison_operator is not None:
                cond_platform["comparison_operator"] = read.comparison_operator
                cond_platform["compared_with"] = read.compared_with
                cond_platform["gating_kind"] = "comparison"
            relationships.append(
                Relationship(
                    source_id=statement_id,
                    target_id=cond_target_id,
                    relationship_type=RelationshipType.READS,
                    execution_context_id=exec_ctx_id,
                    logic_condition=logic_condition,
                    source_platform="rockwell",
                    source_location=statement_loc,
                    confidence=ConfidenceLevel.HIGH,
                    platform_specific=cond_platform,
                )
            )


def _emit_st_loop_edges(
    *,
    block: STLoopBlock,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Emit loop-condition READS and body assignment edges."""

    if block.condition_expression is not None and not block.too_complex_condition:
        cond_plans = _plan_st_reads_from_expression(block.condition_expression)
        _emit_st_reads_from_plans(
            plans=cond_plans,
            condition_source="loop_condition",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type=f"loop_{block.loop_kind.lower()}",
            logic_condition=block.raw_text,
            exec_ctx_id=exec_ctx_id,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
    elif block.condition_operands and not block.too_complex_condition:
        cond_plans = [
            _STBranchPlan(
                or_branch_index=0,
                total_or_branches=1,
                reads=[
                    _STReadPlan(target_tag=tag, examined_value=True)
                    for tag in block.condition_operands
                ],
            )
        ]
        _emit_st_reads_from_plans(
            plans=cond_plans,
            condition_source="loop_condition",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type=f"loop_{block.loop_kind.lower()}",
            logic_condition=block.raw_text,
            exec_ctx_id=exec_ctx_id,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )

    for assign in block.body_assignments:
        _emit_st_assignment_edges(
            assignment=assign,
            extra_conditions=[],
            condition_source="rhs",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type="loop",
            block_raw_text=block.raw_text,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )


def _resolve_st_callee_id_or_stub(
    *,
    callee_name: str,
    controller_name: str,
    program_name: str,
    routine_index: dict[tuple[str, str, str], str],
    control_objects: list[ControlObject],
    is_jsr: bool,
) -> str:
    """Resolve a CALLS target for an ST invocation."""

    if is_jsr:
        return _resolve_routine_id_or_stub(
            target_name=callee_name,
            controller_name=controller_name,
            program_name=program_name,
            routine_index=routine_index,
            control_objects=control_objects,
        )

    stub_id = f"fb::{controller_name}/{callee_name}#stub"
    if any(obj.id == stub_id for obj in control_objects):
        return stub_id
    control_objects.append(
        ControlObject(
            id=stub_id,
            name=callee_name,
            object_type=ControlObjectType.FUNCTION_BLOCK,
            source_platform="rockwell",
            source_location=stub_id,
            parent_ids=[],
            attributes={"callee_kind": "st_invocation"},
            confidence=ConfidenceLevel.LOW,
            platform_specific={
                "language": _ST_LANGUAGE_KEY,
                "callee_name": callee_name,
                "stub_reason": "st_fb_invocation",
            },
        )
    )
    return stub_id


def _plan_st_reads_from_invocation_parameter(param: str) -> list[_STBranchPlan]:
    """Best-effort READ plans for one FB/JSR call parameter."""

    p = param.strip()
    if not p:
        return []
    if _looks_like_tag_operand(p):
        return [
            _STBranchPlan(
                or_branch_index=0,
                total_or_branches=1,
                reads=[_STReadPlan(target_tag=p, examined_value=True)],
            )
        ]
    if re.fullmatch(r"-?\d+(?:\.\d+)?", p):
        return []
    expr = parse_st_expression(p)
    if not expr.too_complex:
        return _plan_st_reads_from_expression(expr)
    arith = parse_st_arithmetic_operands(p)
    if not arith.too_complex and arith.operand_tags:
        return [
            _STBranchPlan(
                or_branch_index=0,
                total_or_branches=1,
                reads=[
                    _STReadPlan(target_tag=tag, examined_value=True)
                    for tag in arith.operand_tags
                ],
            )
        ]
    fn = parse_st_function_call_operands(p)
    if not fn.too_complex and fn.operand_tags:
        return [
            _STBranchPlan(
                or_branch_index=0,
                total_or_branches=1,
                reads=[
                    _STReadPlan(target_tag=tag, examined_value=True)
                    for tag in fn.operand_tags
                ],
            )
        ]
    return []


def _emit_st_fb_invocation_edges(
    *,
    block: STFbInvocation,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Emit CALLS and parameter READS/WRITES for ``Callee(...);`` lines."""

    callee_upper = block.callee_name.upper()
    is_jsr = callee_upper == "JSR"
    call_target_name = block.callee_name
    param_operands = list(block.parameters)
    if is_jsr and block.parameters:
        call_target_name = block.parameters[0].strip()
        param_operands = block.parameters[1:]

    target_id = _resolve_st_callee_id_or_stub(
        callee_name=call_target_name,
        controller_name=controller_name,
        program_name=program_name,
        routine_index=routine_index,
        control_objects=control_objects,
        is_jsr=is_jsr,
    )
    relationships.append(
        Relationship(
            source_id=statement_id,
            target_id=target_id,
            relationship_type=RelationshipType.CALLS,
            execution_context_id=exec_ctx_id,
            logic_condition=block.raw_text,
            source_platform="rockwell",
            source_location=statement_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "language": _ST_LANGUAGE_KEY,
                "statement_type": "fb_invocation",
                "callee_name": block.callee_name,
                "call_target": call_target_name,
                "parameters": list(block.parameters),
                "st_parse_status": (
                    "too_complex" if block.too_complex_parameters else "ok"
                ),
            },
        )
    )

    if is_jsr:
        _emit_jsr_parameter_edges(
            source_id=statement_id,
            source_loc=statement_loc,
            logic_condition=block.raw_text,
            exec_ctx_id=exec_ctx_id,
            controller_name=controller_name,
            program_name=program_name,
            target_routine_name=call_target_name,
            param_operands=param_operands,
            tag_index=tag_index,
            routine_param_index=routine_param_index,
            control_objects=control_objects,
            relationships=relationships,
            platform_base={
                "language": _ST_LANGUAGE_KEY,
                "statement_type": "fb_invocation",
                "gating_kind": "jsr_parameter",
            },
            expression_reads=True,
        )
        return

    for idx, param in enumerate(block.parameters, start=0):
        plans = _plan_st_reads_from_invocation_parameter(param)
        if not plans:
            continue
        rel_count_before = len(relationships)
        _emit_st_reads_from_plans(
            plans=plans,
            condition_source="call_parameter",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type="fb_invocation",
            logic_condition=block.raw_text,
            exec_ctx_id=exec_ctx_id,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        for rel in relationships[rel_count_before:]:
            if rel.relationship_type != RelationshipType.READS:
                continue
            ps = dict(rel.platform_specific or {})
            ps["parameter_index"] = idx
            ps["gating_kind"] = "call_parameter"
            rel.platform_specific = ps


def _emit_st_if_elsif_chain_edges(
    block: STIfElsifChain,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Conservative IF/ELSIF/ELSE: each assignment gets branch gating when
    the branch condition parses; ELSE has no parsed condition reads."""

    for bi, (cond, assigns) in enumerate(block.branches):
        expr = (
            parse_st_expression(cond)
            if cond
            else None
        )
        for assignment in assigns:
            _emit_st_assignment_edges(
                assignment=assignment,
                extra_conditions=[],
                condition_source="if_elsif_chain",
                statement_id=statement_id,
                statement_loc=statement_loc,
                statement_type="if_elsif_chain",
                block_raw_text=block.raw_text,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                control_objects=control_objects,
                relationships=relationships,
                branch_label=f"branch_{bi}",
                gating_expression=expr,
            )


def _emit_st_if_edges(
    block: STIfBlock,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    routine_index: Optional[dict[tuple[str, str, str], str]] = None,
    routine_param_index: Optional[dict[tuple[str, str, str], list[AOIParameter]]] = None,
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
    enclosing_gating_expression: Optional[STExpressionParse] = None,
) -> None:
    """Emit edges for each THEN / ELSE assignment under an IF block.

    THEN-branch assignments inherit ``block.condition_expression`` as
    the gating expression -- this is what carries OR / comparison
    information through to the assignment-level emitter. When nested
    inside another IF branch, ``enclosing_gating_expression`` is ANDed
    onto the local condition so nested THEN writes carry the full
    gating chain.
    ELSE-branch assignments inherit the boolean negation of the
    THEN condition when it is exactly one term (single identifier
    or single comparison); anything else marks
    ``branch_label="ELSE_too_complex"`` and the ELSE assignments
    carry no parsed gating reads.
    """

    # THEN: pass the full expression through unmodified, ANDed with any
    # enclosing IF gating from outer blocks.
    then_expression = (
        block.condition_expression
        if (
            block.condition_expression is not None
            and not block.too_complex_condition
        )
        else None
    )
    effective_then_gating = _and_st_gating_expressions(
        enclosing_gating_expression, then_expression
    )

    for assignment in block.then_assignments:
        _emit_st_assignment_edges(
            assignment=assignment,
            extra_conditions=[],
            condition_source="if_condition",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type="if",
            block_raw_text=block.raw_text,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            branch_label="THEN",
            gating_expression=effective_then_gating,
        )

    _emit_st_if_body_extras(
        nested_ifs=block.then_nested_ifs,
        fb_calls=block.then_fb_calls,
        branch_label="THEN",
        statement_id=statement_id,
        statement_loc=statement_loc,
        controller_name=controller_name,
        program_name=program_name,
        exec_ctx_id=exec_ctx_id,
        tag_index=tag_index,
        control_objects=control_objects,
        relationships=relationships,
        routine_index=routine_index or {},
        routine_param_index=routine_param_index or {},
        aoi_defs=aoi_defs or {},
        aoi_body_routine_ids=aoi_body_routine_ids or {},
        enclosing_gating_expression=effective_then_gating,
    )

    if not block.else_assignments and not block.else_nested_ifs and not block.else_fb_calls:
        return

    else_expression, else_label = _build_else_gating(block)
    effective_else_gating = _and_st_gating_expressions(
        enclosing_gating_expression, else_expression
    )

    for assignment in block.else_assignments:
        _emit_st_assignment_edges(
            assignment=assignment,
            extra_conditions=[],
            condition_source="if_else_condition",
            statement_id=statement_id,
            statement_loc=statement_loc,
            statement_type="if",
            block_raw_text=block.raw_text,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            branch_label=else_label,
            gating_expression=effective_else_gating,
        )

    _emit_st_if_body_extras(
        nested_ifs=block.else_nested_ifs,
        fb_calls=block.else_fb_calls,
        branch_label=else_label,
        statement_id=statement_id,
        statement_loc=statement_loc,
        controller_name=controller_name,
        program_name=program_name,
        exec_ctx_id=exec_ctx_id,
        tag_index=tag_index,
        control_objects=control_objects,
        relationships=relationships,
        routine_index=routine_index or {},
        routine_param_index=routine_param_index or {},
        aoi_defs=aoi_defs or {},
        aoi_body_routine_ids=aoi_body_routine_ids or {},
        enclosing_gating_expression=effective_else_gating,
    )


def _emit_st_if_body_extras(
    *,
    nested_ifs: list[STIfBlock],
    fb_calls: list[STFbInvocation],
    branch_label: str,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    routine_index: dict[tuple[str, str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    aoi_defs: dict[str, AddOnInstructionDef],
    aoi_body_routine_ids: dict[str, str],
    enclosing_gating_expression: Optional[STExpressionParse] = None,
) -> None:
    for nested in nested_ifs:
        _emit_st_if_edges(
            block=nested,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            routine_index=routine_index,
            routine_param_index=routine_param_index,
            aoi_defs=aoi_defs,
            aoi_body_routine_ids=aoi_body_routine_ids,
            enclosing_gating_expression=enclosing_gating_expression,
        )
    for fb in fb_calls:
        aoi_def = aoi_defs.get(fb.callee_name.upper())
        if aoi_def is not None:
            _emit_st_aoi_invocation_edges(
                block=fb,
                statement_id=statement_id,
                statement_obj=control_objects[-1],
                statement_loc=statement_loc,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                aoi_def=aoi_def,
                aoi_body_routine_ids=aoi_body_routine_ids,
                control_objects=control_objects,
                relationships=relationships,
            )
        else:
            _emit_st_fb_invocation_edges(
                block=fb,
                statement_id=statement_id,
                statement_loc=statement_loc,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                routine_index=routine_index,
                routine_param_index=routine_param_index,
                control_objects=control_objects,
                relationships=relationships,
            )


def _emit_st_return_edges(
    *,
    block: STReturnBlock,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    if block.too_complex_value or not block.value_operands:
        return
    for tag in block.value_operands:
        target_id = _resolve_tag_id_or_stub(
            operand=tag,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
        )
        relationships.append(
            Relationship(
                source_id=statement_id,
                target_id=target_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=exec_ctx_id,
                logic_condition=block.raw_text,
                source_platform="rockwell",
                source_location=statement_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "language": _ST_LANGUAGE_KEY,
                    "statement_type": "return",
                    "condition_source": "return_value",
                },
            )
        )


def _build_else_gating(
    block: STIfBlock,
) -> tuple[Optional[STExpressionParse], str]:
    """Mechanically invert a single-term THEN condition for the ELSE
    branch.

    Returns ``(STExpressionParse | None, label)``. The
    ``STExpressionParse`` is the negated condition (or ``None`` when
    inversion isn't mechanically possible); ``label`` is the
    ``branch_label`` to attach to ELSE-side relationships.
    """

    if block.else_too_complex:
        return None, "ELSE_too_complex"

    expr = block.condition_expression
    if expr is None or expr.too_complex:
        return None, "ELSE_too_complex"
    if len(expr.branches) != 1 or len(expr.branches[0].terms) != 1:
        return None, "ELSE_too_complex"

    sole = expr.branches[0].terms[0]
    if isinstance(sole, STCondition):
        negated_term: STTerm = STCondition(
            tag=sole.tag,
            required_value=not sole.required_value,
            natural_language=(
                f"{sole.tag} is "
                f"{'TRUE' if not sole.required_value else 'FALSE'}"
            ),
        )
    elif isinstance(sole, STComparisonTerm):
        inverted_op = _invert_comparison_operator(sole.operator)
        if inverted_op is None:
            return None, "ELSE_too_complex"
        negated_term = STComparisonTerm(
            lhs=sole.lhs,
            operator=inverted_op,
            rhs=sole.rhs,
            lhs_is_tag=sole.lhs_is_tag,
            rhs_is_tag=sole.rhs_is_tag,
            natural_language=f"{sole.lhs} {inverted_op} {sole.rhs}",
        )
    else:
        return None, "ELSE_too_complex"

    return (
        STExpressionParse(
            branches=[STConjunction(terms=[negated_term])],
            too_complex=False,
            raw_text=f"NOT ({expr.raw_text})",
            gating_logic_type=(
                "comparison"
                if isinstance(negated_term, STComparisonTerm)
                else "and"
            ),
        ),
        "ELSE",
    )


# Mirror of ``app.parsers.st_expression._invert_comparison_operator``.
# Kept module-local so the normalization service doesn't reach into
# the parser's private helpers.
def _invert_comparison_operator(op: str) -> Optional[str]:
    return {
        "=":  "<>",
        "<>": "=",
        "<":  ">=",
        "<=": ">",
        ">":  "<=",
        ">=": "<",
    }.get(op)


def _emit_st_case_edges(
    block: STCaseBlock,
    statement_id: str,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    routine_index: Optional[dict[tuple[str, str, str], str]] = None,
    routine_param_index: Optional[dict[tuple[str, str, str], list[AOIParameter]]] = None,
    aoi_defs: Optional[dict[str, AddOnInstructionDef]] = None,
    aoi_body_routine_ids: Optional[dict[str, str]] = None,
) -> None:
    """Emit edges for each branch assignment under a CASE block.

    Each branch assignment WRITES its target. The CASE selector
    contributes a single READS edge per branch (deduped by tag) so
    Trace v2 can see that the selector gates the branch. The exact
    label comparison (``selector == 1``) is *not* modeled as a
    boolean condition today; the WRITES is still emitted with
    ``branch_label`` in ``platform_specific`` so a future trace can
    render the per-branch comparison naturally.
    """

    if not block.branches:
        return

    selector_id: Optional[str] = None
    if block.selector_tag and not block.too_complex_selector:
        selector_id = _resolve_tag_id_or_stub(
            operand=block.selector_tag,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
        )

    for branch in block.branches:
        for assignment in branch.assignments:
            _emit_st_assignment_edges(
                assignment=assignment,
                extra_conditions=[],
                condition_source="case_branch",
                statement_id=statement_id,
                statement_loc=statement_loc,
                statement_type="case",
                block_raw_text=block.raw_text,
                controller_name=controller_name,
                program_name=program_name,
                exec_ctx_id=exec_ctx_id,
                tag_index=tag_index,
                control_objects=control_objects,
                relationships=relationships,
                branch_label=branch.label,
                case_condition_summary=branch.condition_summary,
            )
        _emit_st_if_body_extras(
            nested_ifs=branch.nested_ifs,
            fb_calls=branch.fb_calls,
            branch_label=branch.label,
            statement_id=statement_id,
            statement_loc=statement_loc,
            controller_name=controller_name,
            program_name=program_name,
            exec_ctx_id=exec_ctx_id,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
            routine_index=routine_index or {},
            routine_param_index=routine_param_index or {},
            aoi_defs=aoi_defs or {},
            aoi_body_routine_ids=aoi_body_routine_ids or {},
        )
        # Emit one READS edge per branch for the selector so the
        # graph captures the selector dependency. We do this per
        # branch (rather than once for the whole CASE) so each branch
        # WRITES has a sibling READS, just like ladder rungs.
        if selector_id is not None:
            selector_platform: dict = {
                "language": _ST_LANGUAGE_KEY,
                "statement_type": "case",
                "branch_label": branch.label,
                "condition_source": "case_selector",
                "instruction_type": "CASE_SELECTOR",
                "selector_raw": block.selector_raw,
            }
            if branch.condition_summary is not None:
                selector_platform["case_condition_summary"] = (
                    branch.condition_summary
                )
            relationships.append(
                Relationship(
                    source_id=statement_id,
                    target_id=selector_id,
                    relationship_type=RelationshipType.READS,
                    execution_context_id=exec_ctx_id,
                    logic_condition=block.raw_text,
                    source_platform="rockwell",
                    source_location=statement_loc,
                    confidence=ConfidenceLevel.HIGH,
                    platform_specific=selector_platform,
                )
            )


# ---------------------------------------------------------------------------
# Small ST helpers
# ---------------------------------------------------------------------------


def _and_st_gating_expressions(
    outer: Optional[STExpressionParse],
    inner: Optional[STExpressionParse],
) -> Optional[STExpressionParse]:
    """Conjoin two boolean gating expressions (DNF cartesian product).

    Used to AND parent IF conditions onto nested IF THEN/ELSE writes.
    Returns ``None`` when both inputs are absent; marks ``too_complex``
    when either operand is too complex.
    """

    if outer is None:
        return inner
    if inner is None:
        return outer
    if outer.too_complex or inner.too_complex:
        return STExpressionParse(
            branches=[],
            too_complex=True,
            raw_text=f"({outer.raw_text}) AND ({inner.raw_text})",
            gating_logic_type="too_complex",
        )
    merged_branches: list[STConjunction] = []
    for outer_branch in outer.branches:
        for inner_branch in inner.branches:
            merged_branches.append(
                STConjunction(
                    terms=list(outer_branch.terms) + list(inner_branch.terms)
                )
            )
    raw = f"({outer.raw_text}) AND ({inner.raw_text})"
    has_compare = any(
        any(isinstance(t, STComparisonTerm) for t in conj.terms)
        for conj in merged_branches
    )
    if has_compare:
        gating = "comparison"
    elif len(merged_branches) > 1 and any(
        len(c.terms) > 1 for c in merged_branches
    ):
        gating = "and_or"
    elif len(merged_branches) > 1:
        gating = "or"
    else:
        gating = "and"
    return STExpressionParse(
        branches=merged_branches,
        too_complex=False,
        raw_text=raw,
        gating_logic_type=gating,
    )


def _write_behavior_for_assignment(
    assignment: STAssignment,
) -> Optional[WriteBehaviorType]:
    """Pick a ``WriteBehaviorType`` for a literal RHS, else None.

    Per spec: ``TRUE`` -> ``SETS_TRUE``, ``FALSE`` -> ``SETS_FALSE``,
    anything else -> ``None`` (raw expression goes to
    ``platform_specific["assigned_value"]``).
    """

    if assignment.assigned_value is True:
        return WriteBehaviorType.SETS_TRUE
    if assignment.assigned_value is False:
        return WriteBehaviorType.SETS_FALSE
    return None


def _assigned_value_meta(assignment: STAssignment) -> Optional[str]:
    """Render the spec's ``assigned_value`` metadata string.

    The label is ``"TRUE"`` / ``"FALSE"`` for boolean literals,
    ``"(boolean expression)"`` for anything we successfully parsed
    (conjunctions, disjunctions, comparisons), and the raw RHS text
    for too-complex expressions so consumers can still surface it.
    """

    if assignment.assigned_value is True:
        return "TRUE"
    if assignment.assigned_value is False:
        return "FALSE"
    if assignment.conditions:
        return "(boolean expression)"
    expr = assignment.expression
    if expr is not None and not expr.too_complex and expr.branches:
        return "(boolean expression)"
    return assignment.raw_expression or None


# ---------------------------------------------------------------------------
# Instruction dispatcher + family handlers
# ---------------------------------------------------------------------------
#
# Each handler consumes a single ``_InstructionSemantics`` and the rung
# context, and appends 0..N Relationships. Handlers must be deterministic
# and side-effect-free apart from list appends. To add a new family,
# write a handler here and route to it from ``_dispatch_instruction_semantics``.
# ---------------------------------------------------------------------------


def _dispatch_instruction_semantics(
    instruction: ControlInstruction,
    ctx: _RungContext,
) -> None:
    """Look the instruction up in the registry and dispatch by family.

    Instructions with no registry entry, or entries with
    ``implemented=False``, produce no cause/effect edges; the
    instruction is still attached via the ``CONTAINS`` edge emitted
    by the caller. TODO(intelli/normalization): AOI handling
    (resolve operand bindings against an AddOnInstructionDefinition).
    """

    sem = INSTRUCTION_SEMANTICS.get(instruction.instruction_type)
    if sem is None or not sem.implemented:
        return

    family = sem.family
    if family == _InstructionFamily.CONDITION:
        _handle_condition(instruction, sem, ctx)
    elif family == _InstructionFamily.BOOLEAN_OUTPUT:
        _handle_boolean_output(instruction, sem, ctx)
    elif family == _InstructionFamily.STATEFUL_OUTPUT:
        _handle_stateful_output(instruction, sem, ctx)
    elif family == _InstructionFamily.RESET:
        _handle_reset(instruction, sem, ctx)
    elif family == _InstructionFamily.ROUTINE_CALL:
        _handle_routine_call(instruction, sem, ctx)
    elif family == _InstructionFamily.COMPARISON:
        _handle_comparison(instruction, sem, ctx)
    elif family == _InstructionFamily.MATH:
        _handle_math(instruction, sem, ctx)
    elif family == _InstructionFamily.MOVE_COPY:
        _handle_move_copy(instruction, sem, ctx)
    elif family == _InstructionFamily.ONE_SHOT:
        _handle_one_shot(instruction, sem, ctx)
    # NO_OP (NOP), PID / CONTROL_LOOP, LOGIC_BLOCK, and UNKNOWN families
    # remain undispatched here. NO_OP is intentionally edge-free but
    # recognized; AOI instances (LOGIC_BLOCK) are resolved by
    # ``_handle_aoi_instance`` before this dispatcher runs; the rest are
    # still represented structurally as INSTRUCTION ControlObjects via
    # the CONTAINS pass.


def _handle_condition(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:

    for index in sem.read_operand_indices:
        if index >= len(instruction.operands):
            continue
        operand = instruction.operands[index]
        if not operand:
            continue
        target_id = _resolve_tag_id_or_stub(
            operand=operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=target_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=operand,
                    extras={
                        "examined_value": sem.examined_value,
                    },
                ),
            )
        )


def _handle_boolean_output(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:

    target_operand = _operand_at(instruction, sem.write_operand_index)
    if target_operand is None:
        return
    target_id = _resolve_tag_id_or_stub(
        operand=target_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=target_id,
            relationship_type=sem.write_relationship_type,
            write_behavior=sem.write_behavior,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx, instruction, operand=target_operand
            ),
        )
    )


def _handle_stateful_output(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:

    target_operand = _operand_at(instruction, sem.write_operand_index)
    if target_operand is None:
        return
    target_id = _resolve_tag_id_or_stub(
        operand=target_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    # No write_behavior unless explicitly registered: stateful outputs
    # (timers/counters) have richer semantics than a single behavior
    # value can capture. Member-level edges are future work.
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=target_id,
            relationship_type=sem.write_relationship_type,
            write_behavior=sem.write_behavior,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.MEDIUM,
            platform_specific=_rel_meta(
                ctx, instruction, operand=target_operand
            ),
        )
    )


def _handle_reset(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:

    target_operand = _operand_at(instruction, sem.write_operand_index)
    if target_operand is None:
        return
    target_id = _resolve_tag_id_or_stub(
        operand=target_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=target_id,
            relationship_type=sem.write_relationship_type,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx, instruction, operand=target_operand
            ),
        )
    )


def _handle_routine_call(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:

    target_operand = _operand_at(instruction, sem.write_operand_index)
    if target_operand is None:
        return
    target_id = _resolve_routine_id_or_stub(
        target_name=target_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        routine_index=ctx.routine_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=target_id,
            relationship_type=sem.write_relationship_type,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx,
                instruction,
                operand=target_operand,
                extras={
                    "jsr_parameters": list(instruction.operands[1:]),
                },
            ),
        )
    )
    _emit_jsr_parameter_edges(
        source_id=ctx.rung_id,
        source_loc=ctx.rung_loc,
        logic_condition=ctx.rung_raw_text or "",
        exec_ctx_id=ctx.exec_ctx_id,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        target_routine_name=target_operand,
        param_operands=list(instruction.operands[1:]),
        tag_index=ctx.tag_index,
        routine_param_index=ctx.routine_param_index,
        control_objects=ctx.control_objects,
        relationships=ctx.relationships,
        platform_base=_rel_meta(ctx, instruction, extras={"operand_role": "jsr_parameter"}),
        expression_reads=False,
        rel_meta_fn=lambda operand, extras: _rel_meta(
            ctx, instruction, operand=operand, extras=extras
        ),
    )


# ---------------------------------------------------------------------------
# Add-On Instruction (AOI) instance resolution -> universal LogicBlock
# ---------------------------------------------------------------------------

# System-defined AOI parameters that are NEVER passed positionally at the
# call site (EnableIn is driven by the rung condition; EnableOut is the
# rung-out state). Compared upper-case.
_AOI_SYSTEM_PARAMS = frozenset({"ENABLEIN", "ENABLEOUT"})


def _resolve_call_param_mapping(
    param_defs: list[AOIParameter],
    bound_operands: list[str],
    *,
    exclude_system_params: bool,
) -> tuple[Optional[list[AOIParameter]], Optional[str]]:
    """Map positional operands to declared parameters when counts align."""

    params_all = [
        p
        for p in param_defs
        if not exclude_system_params or p.name.upper() not in _AOI_SYSTEM_PARAMS
    ]
    params_required = [p for p in params_all if p.required]
    n_args = len(bound_operands)
    if n_args == len(params_required):
        return params_required, "required_params"
    if n_args == len(params_all):
        return params_all, "all_visible_params"
    return None, None


def _emit_bound_parameter_edges(
    *,
    source_id: str,
    source_loc: str,
    logic_condition: str,
    exec_ctx_id: str,
    controller_name: str,
    program_name: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    mapped_params: list[AOIParameter],
    bound_operands: list[str],
    platform_base: dict,
    binding_kind: str,
    block_name: Optional[str] = None,
    expression_reads: bool = False,
    rel_meta_fn=None,
) -> None:
    """Emit READS / WRITES per parameter Usage for aligned call bindings."""

    for idx, (param, operand) in enumerate(
        zip(mapped_params, bound_operands), start=1
    ):
        operand = (operand or "").strip()
        if not operand:
            continue
        rel_types = _aoi_usage_to_relationships(param.usage)
        if not rel_types:
            continue

        extras_base = {
            **platform_base,
            "parameter_name": param.name,
            "parameter_usage": param.usage,
            "parameter_data_type": param.data_type,
            "operand_index": idx,
            "gating_kind": binding_kind,
        }
        if block_name:
            extras_base["aoi_name"] = block_name

        if _looks_like_tag_operand(operand):
            target_id = _resolve_tag_id_or_stub(
                operand=operand,
                controller_name=controller_name,
                program_name=program_name,
                tag_index=tag_index,
                control_objects=control_objects,
            )
            for rel_type in rel_types:
                write_behavior = (
                    WriteBehaviorType.MOVES_VALUE
                    if rel_type == RelationshipType.WRITES
                    else None
                )
                ps = {**extras_base, "operand": operand}
                if rel_meta_fn is not None:
                    ps = rel_meta_fn(operand, extras_base)
                relationships.append(
                    Relationship(
                        source_id=source_id,
                        target_id=target_id,
                        relationship_type=rel_type,
                        write_behavior=write_behavior,
                        execution_context_id=exec_ctx_id,
                        logic_condition=logic_condition,
                        source_platform="rockwell",
                        source_location=source_loc,
                        confidence=ConfidenceLevel.HIGH,
                        platform_specific=ps,
                    )
                )
            continue

        if not expression_reads or RelationshipType.READS not in rel_types:
            continue
        plans = _plan_st_reads_from_invocation_parameter(operand)
        if not plans:
            continue
        rel_count_before = len(relationships)
        _emit_st_reads_from_plans(
            plans=plans,
            condition_source="call_parameter",
            statement_id=source_id,
            statement_loc=source_loc,
            statement_type=binding_kind,
            logic_condition=logic_condition,
            exec_ctx_id=exec_ctx_id,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
            relationships=relationships,
        )
        for rel in relationships[rel_count_before:]:
            if rel.relationship_type != RelationshipType.READS:
                continue
            ps = dict(rel.platform_specific or {})
            ps.update(extras_base)
            rel.platform_specific = ps


def _emit_jsr_parameter_edges(
    *,
    source_id: str,
    source_loc: str,
    logic_condition: str,
    exec_ctx_id: str,
    controller_name: str,
    program_name: str,
    target_routine_name: str,
    param_operands: list[str],
    tag_index: dict[tuple[str, str], str],
    routine_param_index: dict[tuple[str, str, str], list[AOIParameter]],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    platform_base: dict,
    expression_reads: bool = False,
    rel_meta_fn=None,
) -> None:
    """Map JSR operands to subroutine parameters when the interface is known."""

    key = (controller_name, program_name, target_routine_name)
    routine_params = routine_param_index.get(key)
    if routine_params:
        mapped, basis = _resolve_call_param_mapping(
            routine_params,
            param_operands,
            exclude_system_params=False,
        )
        if mapped is not None:
            base = dict(platform_base)
            base["jsr_binding_basis"] = basis
            base["target_routine"] = target_routine_name
            _emit_bound_parameter_edges(
                source_id=source_id,
                source_loc=source_loc,
                logic_condition=logic_condition,
                exec_ctx_id=exec_ctx_id,
                controller_name=controller_name,
                program_name=program_name,
                tag_index=tag_index,
                control_objects=control_objects,
                relationships=relationships,
                mapped_params=mapped,
                bound_operands=param_operands,
                platform_base=base,
                binding_kind="jsr_parameter",
                expression_reads=expression_reads,
                rel_meta_fn=rel_meta_fn,
            )
            return

    for idx, op in enumerate(param_operands, start=1):
        if not op or not _looks_like_tag_operand(op):
            continue
        tag_id = _resolve_tag_id_or_stub(
            operand=op,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
        )
        ps = dict(platform_base)
        ps["operand_index"] = idx
        ps["operand"] = op
        ps["gating_kind"] = "jsr_parameter"
        ps["jsr_binding_status"] = "interface_unknown"
        if rel_meta_fn is not None:
            ps = rel_meta_fn(op, ps)
        relationships.append(
            Relationship(
                source_id=source_id,
                target_id=tag_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=exec_ctx_id,
                logic_condition=logic_condition,
                source_platform="rockwell",
                source_location=source_loc,
                confidence=ConfidenceLevel.MEDIUM,
                platform_specific=ps,
            )
        )


def _emit_st_aoi_invocation_edges(
    *,
    block: STFbInvocation,
    statement_id: str,
    statement_obj: ControlObject,
    statement_loc: str,
    controller_name: str,
    program_name: str,
    exec_ctx_id: str,
    tag_index: dict[tuple[str, str], str],
    aoi_def: AddOnInstructionDef,
    aoi_body_routine_ids: dict[str, str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Resolve an ST AOI call into a universal LogicBlock (Phase 2 parity)."""

    statement_obj.object_type = ControlObjectType.FUNCTION_BLOCK
    attrs = dict(statement_obj.attributes or {})
    attrs["is_aoi_instance"] = True
    attrs["aoi_name"] = aoi_def.name
    attrs["block_kind"] = "add_on_instruction"
    attrs["semantic_family"] = _InstructionFamily.LOGIC_BLOCK.value
    attrs["semantic_implemented"] = True
    statement_obj.attributes = attrs
    ps = dict(statement_obj.platform_specific or {})
    ps["aoi_name"] = aoi_def.name
    ps["aoi_revision"] = aoi_def.revision
    ps["callee_name"] = block.callee_name
    ps["parameter_count"] = len(block.parameters)
    statement_obj.platform_specific = ps

    if not block.parameters:
        return

    backing_operand = block.parameters[0].strip()
    if backing_operand and _looks_like_tag_operand(backing_operand):
        backing_id = _resolve_tag_id_or_stub(
            operand=backing_operand,
            controller_name=controller_name,
            program_name=program_name,
            tag_index=tag_index,
            control_objects=control_objects,
        )
        relationships.append(
            Relationship(
                source_id=statement_id,
                target_id=backing_id,
                relationship_type=RelationshipType.REFERENCES,
                execution_context_id=exec_ctx_id,
                logic_condition=block.raw_text,
                source_platform="rockwell",
                source_location=statement_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "language": _ST_LANGUAGE_KEY,
                    "aoi_name": aoi_def.name,
                    "aoi_role": "backing_tag",
                    "gating_kind": "aoi_backing",
                    "operand": backing_operand,
                },
            )
        )

    bound_operands = [p.strip() for p in block.parameters[1:]]
    mapped, basis = _resolve_call_param_mapping(
        aoi_def.parameters,
        bound_operands,
        exclude_system_params=True,
    )
    if mapped is None:
        ps = dict(statement_obj.platform_specific or {})
        params_all = [
            p
            for p in aoi_def.parameters
            if p.name.upper() not in _AOI_SYSTEM_PARAMS
        ]
        params_required = [p for p in params_all if p.required]
        ps["aoi_binding_status"] = "operand_count_mismatch"
        ps["aoi_arg_count"] = len(bound_operands)
        ps["aoi_required_param_count"] = len(params_required)
        ps["aoi_total_param_count"] = len(params_all)
        statement_obj.platform_specific = ps
        return

    statement_obj.attributes["aoi_binding_basis"] = basis
    _emit_bound_parameter_edges(
        source_id=statement_id,
        source_loc=statement_loc,
        logic_condition=block.raw_text,
        exec_ctx_id=exec_ctx_id,
        controller_name=controller_name,
        program_name=program_name,
        tag_index=tag_index,
        control_objects=control_objects,
        relationships=relationships,
        mapped_params=mapped,
        bound_operands=bound_operands,
        platform_base={
            "language": _ST_LANGUAGE_KEY,
            "statement_type": "fb_invocation",
        },
        binding_kind="aoi_parameter",
        block_name=aoi_def.name,
        expression_reads=True,
    )

    body_id = aoi_body_routine_ids.get(aoi_def.name.upper())
    if body_id:
        relationships.append(
            Relationship(
                source_id=statement_id,
                target_id=body_id,
                relationship_type=RelationshipType.CALLS,
                execution_context_id=exec_ctx_id,
                source_platform="rockwell",
                source_location=statement_loc,
                confidence=ConfidenceLevel.MEDIUM,
                platform_specific={
                    "language": _ST_LANGUAGE_KEY,
                    "aoi_name": aoi_def.name,
                    "calls_kind": "aoi_body",
                    "callee_name": block.callee_name,
                },
            )
        )


def _aoi_usage_to_relationships(
    usage: Optional[str],
) -> tuple[RelationshipType, ...]:
    """Map an AOI parameter Usage to universal relationship direction(s).

    ``Input`` -> the instance READS the bound tag.
    ``Output`` -> the instance WRITES the bound tag.
    ``InOut`` -> the instance both READS and WRITES (by-reference).
    Unknown / missing usage -> no edge (do not guess direction).
    """

    u = (usage or "").strip().lower()
    if u == "input":
        return (RelationshipType.READS,)
    if u == "output":
        return (RelationshipType.WRITES,)
    if u == "inout":
        return (RelationshipType.READS, RelationshipType.WRITES)
    return ()


def _handle_aoi_instance(
    instruction: ControlInstruction,
    instr_id: str,
    instr_obj: ControlObject,
    aoi_def: AddOnInstructionDef,
    ctx: _RungContext,
) -> None:
    """Resolve an AOI instance call into a vendor-neutral LogicBlock.

    The call's INSTRUCTION ControlObject is retyped to ``FUNCTION_BLOCK``
    (the universal LogicBlock). Operand 0 is the backing/instance tag
    (instance ``REFERENCES`` it). The remaining operands bind to the AOI's
    *call parameters* by position and emit ``READS`` / ``WRITES`` per the
    parameter Usage. This is the same shape a Siemens FB instance will map
    to.

    Call-parameter ordering (Rockwell neutral text): operands after the
    backing tag correspond to the parameters with ``Required="true"`` in
    declared order, excluding the system EnableIn/EnableOut. We verify the
    operand count matches before binding; on a mismatch we stay
    conservative (backing reference only) and flag it rather than guessing
    a misaligned mapping.
    """

    operands = list(instruction.operands)

    # Retype the instruction object into the universal LogicBlock.
    instr_obj.object_type = ControlObjectType.FUNCTION_BLOCK
    attrs = dict(instr_obj.attributes or {})
    attrs["is_aoi_instance"] = True
    attrs["aoi_name"] = aoi_def.name
    attrs["block_kind"] = "add_on_instruction"
    attrs["semantic_family"] = _InstructionFamily.LOGIC_BLOCK.value
    attrs["semantic_implemented"] = True
    instr_obj.attributes = attrs
    ps = dict(instr_obj.platform_specific or {})
    ps["aoi_name"] = aoi_def.name
    ps["aoi_revision"] = aoi_def.revision
    instr_obj.platform_specific = ps

    if not operands:
        return

    # --- Backing / instance tag (operand 0) -------------------------------
    backing_operand = operands[0]
    if backing_operand and _looks_like_tag_operand(backing_operand):
        backing_id = _resolve_tag_id_or_stub(
            operand=backing_operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=backing_id,
                relationship_type=RelationshipType.REFERENCES,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=backing_operand,
                    extras={
                        "aoi_name": aoi_def.name,
                        "aoi_role": "backing_tag",
                        "gating_kind": "aoi_backing",
                    },
                ),
            )
        )

    # --- Determine the call-parameter ordering ----------------------------
    bound_operands = operands[1:]
    mapped_params, binding_basis = _resolve_call_param_mapping(
        aoi_def.parameters,
        bound_operands,
        exclude_system_params=True,
    )

    if mapped_params is None:
        params_all = [
            p for p in aoi_def.parameters
            if p.name.upper() not in _AOI_SYSTEM_PARAMS
        ]
        params_required = [p for p in params_all if p.required]
        ps = dict(instr_obj.platform_specific or {})
        ps["aoi_binding_status"] = "operand_count_mismatch"
        ps["aoi_arg_count"] = len(bound_operands)
        ps["aoi_required_param_count"] = len(params_required)
        ps["aoi_total_param_count"] = len(params_all)
        instr_obj.platform_specific = ps
        return

    instr_obj.attributes["aoi_binding_basis"] = binding_basis

    _emit_bound_parameter_edges(
        source_id=instr_id,
        source_loc=ctx.rung_loc,
        logic_condition=ctx.rung_raw_text or "",
        exec_ctx_id=ctx.exec_ctx_id,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
        relationships=ctx.relationships,
        mapped_params=mapped_params,
        bound_operands=bound_operands,
        platform_base={},
        binding_kind="aoi_parameter",
        block_name=aoi_def.name,
        expression_reads=False,
        rel_meta_fn=lambda operand, extras: _rel_meta(
            ctx,
            instruction,
            operand=operand,
            extras=extras,
        ),
    )

    # --- Optional: link the instance to its internal logic routine --------
    body_id = ctx.aoi_body_routine_ids.get(aoi_def.name.upper())
    if body_id:
        ctx.relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=body_id,
                relationship_type=RelationshipType.CALLS,
                execution_context_id=ctx.exec_ctx_id,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.MEDIUM,
                platform_specific={
                    "instruction_type": instruction.instruction_type,
                    "instruction_id": instruction.id,
                    "aoi_name": aoi_def.name,
                    "calls_kind": "aoi_body",
                },
            )
        )


# ---------------------------------------------------------------------------
# Newer family handlers
# ---------------------------------------------------------------------------


# Maps comparison instruction to its mathematical operator. Used in
# ``platform_specific["comparison_operator"]`` so downstream consumers
# don't have to mirror this table.
_COMPARISON_OPERATOR: dict[str, str] = {
    "EQU": "=",
    "NEQ": "<>",
    "LES": "<",
    "LEQ": "<=",
    "GRT": ">",
    "GEQ": ">=",
    "LIM": "<=lim<=",
}


def _handle_comparison(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:
    """EQU / NEQ / LES / LEQ / GRT / GEQ / LIM read tag operands.

    Numeric / string-literal operands are deliberately *not* emitted as
    READS (they're constants, not tags). The full operand list is
    still recorded on each READS edge in
    ``platform_specific["compared_operands"]`` so a reader can tell
    "Tag_A compared with constant 5" without re-parsing the rung.
    """

    itype = instruction.instruction_type.upper()
    operator = _COMPARISON_OPERATOR.get(itype, itype)

    # Snapshot all operands once so each emitted READS can reference
    # the full comparison context.
    all_operands = list(instruction.operands)

    for index in sem.read_operand_indices:
        if index >= len(instruction.operands):
            continue
        operand = instruction.operands[index]
        if not operand or not _looks_like_tag_operand(operand):
            continue
        target_id = _resolve_tag_id_or_stub(
            operand=operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=target_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=operand,
                    extras={
                        "operand_index": index,
                        "comparison_operator": operator,
                        "compared_operands": all_operands,
                        # Mark with a non-XIC/XIO instruction_type so
                        # Trace v2's ladder condition aggregator skips
                        # comparison reads (which would otherwise look
                        # like ANDed XIC/XIO gating conditions).
                        "gating_kind": "comparison",
                    },
                ),
            )
        )


def _handle_math(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:
    """ADD / SUB / MUL / DIV / CPT.

    Source operands that look like tags become READS; the destination
    operand always becomes a WRITES with ``write_behavior=CALCULATES``.
    CPT's expression operand is *not* cracked (Rockwell encodes it as
    a quoted string the ladder parser doesn't tokenize); only the
    destination WRITES is emitted for CPT.
    """

    itype = instruction.instruction_type.upper()
    operator = _MATH_OPERATOR.get(itype, itype)

    # Reads for source operands when they're tags.
    for index in sem.read_operand_indices:
        if index >= len(instruction.operands):
            continue
        operand = instruction.operands[index]
        if not operand or not _looks_like_tag_operand(operand):
            continue
        target_id = _resolve_tag_id_or_stub(
            operand=operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=target_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=operand,
                    extras={
                        "operand_index": index,
                        "operand_role": "math_source",
                        "math_operator": operator,
                        "gating_kind": "math_source",
                    },
                ),
            )
        )

    # Write for the destination operand.
    dest_operand = _operand_at(instruction, sem.write_operand_index)
    if dest_operand is None or not _looks_like_tag_operand(dest_operand):
        return
    dest_id = _resolve_tag_id_or_stub(
        operand=dest_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=dest_id,
            relationship_type=sem.write_relationship_type,
            write_behavior=sem.write_behavior,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx,
                instruction,
                operand=dest_operand,
                extras={
                    "operand_role": "math_destination",
                    "math_operator": operator,
                    "source_operands": (
                        [
                            instruction.operands[i]
                            for i in sem.read_operand_indices
                            if i < len(instruction.operands)
                        ]
                    ),
                },
            ),
        )
    )


def _handle_move_copy(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:
    """MOV(Source, Dest) / COP(Source, Dest, Length).

    Source becomes READS when it looks like a tag; destination becomes
    WRITES with ``write_behavior=MOVES_VALUE``.
    """

    # Source
    for index in sem.read_operand_indices:
        if index >= len(instruction.operands):
            continue
        operand = instruction.operands[index]
        if not operand or not _looks_like_tag_operand(operand):
            continue
        target_id = _resolve_tag_id_or_stub(
            operand=operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=target_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=operand,
                    extras={
                        "operand_index": index,
                        "operand_role": "move_source",
                        "gating_kind": "move_source",
                    },
                ),
            )
        )

    # Destination
    dest_operand = _operand_at(instruction, sem.write_operand_index)
    if dest_operand is None or not _looks_like_tag_operand(dest_operand):
        return
    dest_id = _resolve_tag_id_or_stub(
        operand=dest_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=dest_id,
            relationship_type=sem.write_relationship_type,
            write_behavior=sem.write_behavior,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx,
                instruction,
                operand=dest_operand,
                extras={
                    "operand_role": "move_destination",
                },
            ),
        )
    )


def _handle_one_shot(
    instruction: ControlInstruction,
    sem: _InstructionSemantics,
    ctx: _RungContext,
) -> None:
    """ONS(StorageBit) / ONS(StorageBit, OutputBit) / OSR / OSF.

    The storage bit is READ; the written pulse target is operand 0 for
    single-argument ``ONS`` (storage is pulsed), or operand 1 when a
    second tag is supplied (vendor-specific two-operand ``ONS`` form).
    ``OSR`` / ``OSF`` always use operand 1 as the output bit.
    """

    storage_operand = _operand_at(instruction, sem.read_operand_indices[0])
    if storage_operand is not None and _looks_like_tag_operand(storage_operand):
        storage_id = _resolve_tag_id_or_stub(
            operand=storage_operand,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=storage_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.HIGH,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=storage_operand,
                    extras={
                        "operand_role": "one_shot_storage",
                        "examined_value": True,
                        "gating_kind": "one_shot",
                    },
                ),
            )
        )

    is_ons = instruction.instruction_type.upper() == "ONS"
    ons_two_operand = is_ons and len(instruction.operands) >= 2
    write_idx = 1 if ons_two_operand else sem.write_operand_index

    out_operand = _operand_at(instruction, write_idx)
    if out_operand is None or not _looks_like_tag_operand(out_operand):
        return
    out_id = _resolve_tag_id_or_stub(
        operand=out_operand,
        controller_name=ctx.controller_name,
        program_name=ctx.program_name,
        tag_index=ctx.tag_index,
        control_objects=ctx.control_objects,
    )
    ctx.relationships.append(
        Relationship(
            source_id=ctx.rung_id,
            target_id=out_id,
            relationship_type=sem.write_relationship_type,
            write_behavior=sem.write_behavior,
            execution_context_id=ctx.exec_ctx_id,
            logic_condition=ctx.rung_raw_text,
            source_platform="rockwell",
            source_location=ctx.rung_loc,
            confidence=ConfidenceLevel.HIGH,
            platform_specific=_rel_meta(
                ctx,
                instruction,
                operand=out_operand,
                extras={
                    "operand_role": (
                        "one_shot_output"
                        if (not is_ons) or ons_two_operand
                        else "one_shot_storage"
                    ),
                },
            ),
        )
    )


# ---------------------------------------------------------------------------
# Ladder helpers: tag detection, member access, branch detection,
# relationship metadata.
# ---------------------------------------------------------------------------


# Maps a math instruction to its mathematical operator. Mirrors
# ``_COMPARISON_OPERATOR`` for math.
_MATH_OPERATOR: dict[str, str] = {
    "ADD": "+",
    "SUB": "-",
    "MUL": "*",
    "DIV": "/",
    "CPT": "expression",
}


# Suffixes that identify a member access against a timer / counter
# structure. Encoded as a tuple of ``(suffix, semantic_label)`` pairs.
# The semantic label is what we surface in ``platform_specific``.
_TIMER_COUNTER_MEMBERS: tuple[tuple[str, str], ...] = (
    (".DN", "done"),
    (".TT", "timing"),
    (".EN", "enabled"),
    (".ACC", "accumulated_value"),
    (".PRE", "preset_value"),
    (".CU", "count_up_enable"),
    (".CD", "count_down_enable"),
)


def _looks_like_tag_operand(value: str) -> bool:
    """Conservative tag-vs-literal check for ladder operand strings.

    Strings starting with a letter or underscore and containing only
    identifier characters (incl. ``.`` for members and ``[N]`` for
    indices) are considered tag references. Numeric literals,
    boolean literals, quoted strings, and expressions with operators
    are not.
    """

    if not value:
        return False
    s = value.strip()
    if not s:
        return False
    if s.startswith(('"', "'")):
        return False
    upper = s.upper()
    if upper in ("TRUE", "FALSE"):
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return False
    return bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])*", s)
    )


def _emit_alias_edge(
    alias_tag: ControlTag,
    alias_id: str,
    controller_name: str,
    program_name: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    source_platform: str,
) -> None:
    """Emit an ``alias -> base`` REFERENCES edge so trace follows aliases.

    The base tag is resolved through the normal tag resolver (program
    scope then controller scope), creating a low-confidence stub if the
    base is not present. Also records ``alias_for`` on the alias tag's
    own ControlObject attributes for display.
    """

    base_name = alias_tag.alias_for
    if not base_name:
        return

    base_id = _resolve_tag_id_or_stub(
        operand=base_name,
        controller_name=controller_name,
        program_name=program_name,
        tag_index=tag_index,
        control_objects=control_objects,
    )

    # Annotate the alias tag object itself.
    for obj in reversed(control_objects):
        if obj.id == alias_id:
            obj.attributes = dict(obj.attributes or {})
            obj.attributes["alias_for"] = base_name
            ps = dict(obj.platform_specific or {})
            ps["alias_for"] = base_name
            obj.platform_specific = ps
            break

    relationships.append(
        Relationship(
            source_id=alias_id,
            target_id=base_id,
            relationship_type=RelationshipType.REFERENCES,
            source_platform=source_platform,
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "alias_for": base_name,
                "reference_kind": "alias",
            },
        )
    )


def _alias_base_for(
    operand: Optional[str], ctx: "_RungContext"
) -> Optional[str]:
    """Return the alias base name if ``operand`` (or its root) is an alias."""

    if not operand or not ctx.alias_map:
        return None
    s = operand.strip()
    if s in ctx.alias_map:
        return ctx.alias_map[s]
    root = s.split(".", 1)[0].split("[", 1)[0]
    if root in ctx.alias_map:
        return ctx.alias_map[root]
    return None


def _udt_member_meta(
    operand: Optional[str],
    tag_type_map: dict[str, str],
    udt_defs: dict[str, "DataTypeDef"],
) -> Optional[dict[str, str]]:
    """Resolve a ``Base.Member`` operand against a known UDT definition.

    Returns ``{"udt_type", "udt_base", "udt_member", "udt_member_type"}``
    when ``Base`` is a tag of a known UDT type and ``Member`` is one of
    that type's declared members; else None. Nested members
    (``Base.Sub.Bit``) resolve the first level only.
    """

    if not operand or "." not in operand:
        return None
    s = operand.strip()
    base = s.split(".", 1)[0].split("[", 1)[0]
    member = s.split(".", 1)[1].split(".", 1)[0].split("[", 1)[0]
    if not base or not member:
        return None
    udt_type = tag_type_map.get(base)
    if not udt_type:
        return None
    udt_def = udt_defs.get(udt_type)
    if udt_def is None:
        return None
    for m in udt_def.members:
        if m.name == member:
            meta = {
                "udt_type": udt_type,
                "udt_base": base,
                "udt_member": member,
            }
            if m.data_type:
                meta["udt_member_type"] = m.data_type
            return meta
    return None


def _member_suffix(operand: Optional[str]) -> Optional[dict[str, str]]:
    """Return ``{'member': '.DN', 'semantic': 'done'}`` if ``operand``
    accesses a known timer/counter member, else None.
    """

    if not operand:
        return None
    s = operand.strip()
    for suffix, label in _TIMER_COUNTER_MEMBERS:
        if s.upper().endswith(suffix):
            return {"member": suffix.lstrip("."), "semantic": label}
    return None


# Branch tokens used in Rockwell rung text. ``BST`` opens a branch
# group, ``NXB`` separates parallel siblings, ``BND`` closes the
# group. These tokens are bare (no parens) so the ladder
# instruction-tokenizer skips them; we look for them in the raw
# rung text directly.
_BRANCH_BST_RE = re.compile(r"\bBST\b", re.IGNORECASE)
_BRANCH_NXB_RE = re.compile(r"\bNXB\b", re.IGNORECASE)
_BRANCH_BND_RE = re.compile(r"\bBND\b", re.IGNORECASE)

# Logix parallel-OR bracket notation ``[XIC(a),XIC(b)]`` (coarse signal
# only — we do not attribute operands to branch arms).
_SQUARE_PARALLEL_RE = re.compile(
    r"\[[^\]]*(?:XIC|XIO|OTE|OTL|OTU)\s*\([^\)]*\)[^\]]*,",
    re.IGNORECASE,
)


def _detect_rung_branches(
    rung_raw_text: Optional[str],
) -> tuple[bool, int]:
    """Conservative branch detection: return ``(has_branches, count)``.

    ``BST`` / ``NXB`` / ``BND``: ``count`` is ``num_NXB + 1`` when a
    closing ``BND`` exists; otherwise ``1`` when ``BST`` appears alone.

    Square-bracket parallel OR (e.g. ``[XIC(A),XIC(B)]``) yields
    ``(True, 2)`` when the heuristic matches. Full branch attribution
    (which instruction sits on which path) remains unsupported.
    """

    if not rung_raw_text:
        return False, 1

    if _BRANCH_BST_RE.search(rung_raw_text):
        if not _BRANCH_BND_RE.search(rung_raw_text):
            return True, 1
        nxb = len(_BRANCH_NXB_RE.findall(rung_raw_text))
        return True, nxb + 1

    if _SQUARE_PARALLEL_RE.search(rung_raw_text):
        return True, 2

    return False, 1


def _rel_meta(
    ctx: _RungContext,
    instruction: ControlInstruction,
    *,
    operand: Optional[str] = None,
    extras: Optional[dict] = None,
) -> dict:
    """Build the common ``platform_specific`` for a relationship.

    Always includes ``instruction_type`` + ``instruction_id``. Adds
    ``member`` / ``member_semantic`` when ``operand`` is a known
    timer/counter member access, and ``rung_has_branches`` /
    ``rung_branch_count`` whenever the rung itself is branched.
    Per-call ``extras`` are merged last and may override defaults.
    """

    meta: dict = {
        "instruction_type": instruction.instruction_type,
        "instruction_id": instruction.id,
    }
    member_info = _member_suffix(operand)
    if member_info:
        meta["member"] = member_info["member"]
        meta["member_semantic"] = member_info["semantic"]
    udt_info = _udt_member_meta(operand, ctx.tag_type_map, ctx.udt_defs)
    if udt_info:
        meta.update(udt_info)
    alias_base = _alias_base_for(operand, ctx)
    if alias_base:
        meta["alias_for"] = alias_base
    if ctx.rung_has_branches:
        meta["rung_has_branches"] = True
        meta["rung_branch_count"] = ctx.rung_branch_count
    if ctx.logic_expression_resolved:
        meta["logic_expression_resolved"] = True
    if ctx.branch_warnings:
        meta["branch_warnings"] = list(ctx.branch_warnings)
    if extras:
        meta.update(extras)
    return meta


def _operand_at(
    instruction: ControlInstruction, index: Optional[int]
) -> Optional[str]:
    """Safely read ``instruction.operands[index]`` or return None."""
    if index is None:
        return None
    if index >= len(instruction.operands):
        return None
    operand = instruction.operands[index]
    return operand or None


# ---------------------------------------------------------------------------
# Object construction helpers
# ---------------------------------------------------------------------------


def _tag_to_control_object(
    tag: ControlTag,
    tag_id: str,
    source_location: str,
    parent_ids: Optional[list[str]] = None,
    source_platform: str = "rockwell",
) -> ControlObject:

    return ControlObject(
        id=tag_id,
        name=tag.name,
        object_type=ControlObjectType.TAG,
        source_platform=source_platform,
        source_location=source_location,
        parent_ids=parent_ids or [],
        description=tag.description,
        attributes={
            "data_type": tag.data_type,
            "scope": tag.scope,
        },
        confidence=(
            ConfidenceLevel.HIGH
            if (tag.platform_source or "").startswith("rockwell_l5x")
            and "discovered" not in (tag.platform_source or "")
            else ConfidenceLevel.MEDIUM
        ),
        platform_specific={
            "platform_source": tag.platform_source,
            "rockwell_metadata": tag.metadata or {},
            "platform_metadata": tag.metadata or {},
        },
    )


def _instruction_to_control_object(
    instruction: ControlInstruction,
    instr_id: str,
    source_location: str,
    parent_ids: list[str],
    *,
    confidence_override: Optional[ConfidenceLevel] = None,
    source_platform: str = "rockwell",
) -> ControlObject:

    sem = INSTRUCTION_SEMANTICS.get(instruction.instruction_type)
    family_value = sem.family.value if sem else _InstructionFamily.UNKNOWN.value
    itype = instruction.instruction_type.upper()
    one_shot_meta: dict[str, Any] = {}
    if itype in {"ONS", "OSR", "OSF"}:
        one_shot_meta = {
            "one_shot_variant": itype,
            "operand_count": len(instruction.operands),
            "operands_roles": (
                ["storage", "output"] if len(instruction.operands) >= 2 else ["storage"]
            ),
        }

    return ControlObject(
        id=instr_id,
        name=instruction.instruction_type,
        object_type=ControlObjectType.INSTRUCTION,
        source_platform=source_platform,
        source_location=source_location,
        parent_ids=parent_ids,
        attributes={
            "instruction_type": instruction.instruction_type,
            "operands": list(instruction.operands),
            "output": instruction.output,
            "language": instruction.language,
            "rung_number": instruction.rung_number,
            "semantic_family": family_value,
            "semantic_implemented": bool(sem and sem.implemented),
        },
        confidence=confidence_override or ConfidenceLevel.HIGH,
        platform_specific={
            "raw_text": instruction.raw_text,
            "instruction_local_id": instruction.id,
            "rockwell_metadata": instruction.metadata or {},
            "platform_metadata": instruction.metadata or {},
            "semantic_notes": sem.notes if sem else "",
            **one_shot_meta,
        },
    )


def _apply_explicit_fbd_sfc_object_type(
    obj: ControlObject,
    instruction: ControlInstruction,
    routine_language: Optional[str],
) -> None:
    metadata = instruction.metadata or {}
    subtype = str(metadata.get("object_subtype") or "").lower()
    language = str(instruction.language or routine_language or "").lower()

    if subtype in {"function_block", "fbd_block", "fbd_block_instance"}:
        obj.object_type = ControlObjectType.FUNCTION_BLOCK
        obj.attributes["language"] = "fbd"
        obj.attributes["object_subtype"] = "function_block"
    elif subtype in {"function_block_pin", "pin", "fbd_pin"}:
        obj.object_type = ControlObjectType.FUNCTION_BLOCK_PIN
        obj.attributes["language"] = "fbd"
        obj.attributes["object_subtype"] = "function_block_pin"
    elif subtype in {"sfc_step", "step"}:
        obj.object_type = ControlObjectType.SFC_STEP
        obj.attributes["language"] = "sfc"
        obj.attributes["object_subtype"] = "sfc_step"
    elif subtype in {"sfc_transition", "transition"}:
        obj.object_type = ControlObjectType.SFC_TRANSITION
        obj.attributes["language"] = "sfc"
        obj.attributes["object_subtype"] = "sfc_transition"
    elif subtype in {"sfc_action", "action"}:
        obj.object_type = ControlObjectType.SFC_ACTION
        obj.attributes["language"] = "sfc"
        obj.attributes["object_subtype"] = "sfc_action"
    elif language == "fbd":
        obj.attributes["language"] = "fbd"
        obj.attributes.setdefault("object_subtype", "function_block")
    elif language == "sfc":
        obj.attributes["language"] = "sfc"
        obj.attributes.setdefault("object_subtype", "unsupported")

    ps = dict(obj.platform_specific or {})
    ps["parse_status"] = metadata.get("parse_status", "unsupported")
    for key in ("block_type", "block_name", "parameters", "pin", "direction"):
        if key in metadata:
            ps[key] = metadata[key]
    obj.platform_specific = ps


def _emit_explicit_fbd_sfc_relationships(
    instruction: ControlInstruction,
    instr_id: str,
    relationships: list[Relationship],
    source_platform: str,
) -> None:
    metadata = instruction.metadata or {}
    for target in metadata.get("connects_to", []) or []:
        relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=str(target),
                relationship_type=RelationshipType.CONNECTS,
                confidence=ConfidenceLevel.MEDIUM,
                source_platform=source_platform,
                platform_specific={"parse_status": "explicit_connection"},
            )
        )
    for target in metadata.get("references", []) or []:
        relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=str(target),
                relationship_type=RelationshipType.REFERENCES,
                confidence=ConfidenceLevel.LOW,
                source_platform=source_platform,
                platform_specific={"parse_status": "explicit_reference"},
            )
        )
    for target in metadata.get("sequences_to", []) or []:
        relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=str(target),
                relationship_type=RelationshipType.SEQUENCES,
                confidence=ConfidenceLevel.MEDIUM,
                source_platform=source_platform,
                platform_specific={"parse_status": "explicit_sequence"},
            )
        )
    for target in metadata.get("condition_for", []) or []:
        relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=str(target),
                relationship_type=RelationshipType.CONDITION_FOR,
                confidence=ConfidenceLevel.MEDIUM,
                source_platform=source_platform,
                platform_specific={"parse_status": "explicit_condition"},
            )
        )
    for target in metadata.get("action_of", []) or []:
        relationships.append(
            Relationship(
                source_id=instr_id,
                target_id=str(target),
                relationship_type=RelationshipType.ACTION_OF,
                confidence=ConfidenceLevel.MEDIUM,
                source_platform=source_platform,
                platform_specific={"parse_status": "explicit_action"},
            )
        )


# ---------------------------------------------------------------------------
# Tag / routine resolution
# ---------------------------------------------------------------------------


def _resolve_tag_id_or_stub(
    operand: str,
    controller_name: str,
    program_name: str,
    tag_index: dict[tuple[str, str], str],
    control_objects: list[ControlObject],
) -> str:
    """Resolve an operand to an existing tag id, or create a stub.

    Resolution order:
        1. Exact match in program scope.
        2. Exact match in controller scope.
        3. UDT/array root (e.g. ``Pump_01.Run`` -> ``Pump_01``) in
           program scope, then controller scope.
        4. Unresolved: append a low-confidence stub ``ControlObject`` of
           type ``TAG`` and return its id, so downstream graph consumers
           always have something to point at.

    Stubs are cached in ``tag_index`` so repeated lookups for the same
    operand within the same program do not create duplicates.
    """

    if (program_name, operand) in tag_index:
        return tag_index[(program_name, operand)]
    if (CONTROLLER_SCOPE_KEY, operand) in tag_index:
        return tag_index[(CONTROLLER_SCOPE_KEY, operand)]

    root = operand.split(".", 1)[0].split("[", 1)[0]
    if root and root != operand:
        if (program_name, root) in tag_index:
            return tag_index[(program_name, root)]
        if (CONTROLLER_SCOPE_KEY, root) in tag_index:
            return tag_index[(CONTROLLER_SCOPE_KEY, root)]

    stub_id = (
        f"tag::{controller_name}/{program_name}/{operand}#unresolved"
    )
    control_objects.append(
        ControlObject(
            id=stub_id,
            name=operand,
            object_type=ControlObjectType.TAG,
            source_platform="rockwell",
            source_location=(
                f"Controller:{controller_name}/Program:{program_name}"
                f"/Unresolved:{operand}"
            ),
            confidence=ConfidenceLevel.LOW,
            platform_specific={
                "unresolved": True,
                "lookup_program": program_name,
            },
        )
    )
    tag_index[(program_name, operand)] = stub_id
    return stub_id


def _resolve_routine_id_or_stub(
    target_name: str,
    controller_name: str,
    program_name: str,
    routine_index: dict[tuple[str, str, str], str],
    control_objects: list[ControlObject],
) -> str:
    """Resolve a routine reference to an existing routine id, or stub.

    Resolution order:
        1. Same-program lookup ``(controller, program, target_name)``.
        2. Unresolved: append a low-confidence stub ``ControlObject``
           of type ``ROUTINE``.

    TODO(intelli/normalization): cross-program routine references
    (rare but valid in some Rockwell setups). Would require iterating
    all programs in ``routine_index`` filtered by controller.
    """

    key = (controller_name, program_name, target_name)
    if key in routine_index:
        return routine_index[key]

    cross_program = [
        rid
        for (c, p, n), rid in routine_index.items()
        if c == controller_name and n == target_name and p != program_name
    ]
    if len(cross_program) == 1:
        return cross_program[0]

    stub_id = (
        f"routine::{controller_name}/{program_name}/{target_name}"
        f"#unresolved"
    )
    control_objects.append(
        ControlObject(
            id=stub_id,
            name=target_name,
            object_type=ControlObjectType.ROUTINE,
            source_platform="rockwell",
            source_location=(
                f"Controller:{controller_name}/Program:{program_name}"
                f"/UnresolvedRoutine:{target_name}"
            ),
            confidence=ConfidenceLevel.LOW,
            platform_specific={
                "unresolved": True,
                "lookup_program": program_name,
            },
        )
    )
    routine_index[key] = stub_id
    return stub_id


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def _build_routine_index(
    parsed_project: ControlProject,
) -> dict[tuple[str, str, str], str]:
    """Pre-pass: map (controller, program, routine_name) -> routine_id.

    Built ahead of the main walk so that JSR (and any future cross-
    routine references) can resolve their target regardless of
    iteration order.
    """

    index: dict[tuple[str, str, str], str] = {}
    for controller in parsed_project.controllers:
        for program in controller.programs:
            for routine in program.routines:
                key = (controller.name, program.name, routine.name)
                index[key] = _routine_id(
                    controller.name, program.name, routine.name
                )
    return index


def _build_routine_param_index(
    parsed_project: ControlProject,
) -> dict[tuple[str, str, str], list[AOIParameter]]:
    """Map (controller, program, routine) -> declared subroutine parameters."""

    index: dict[tuple[str, str, str], list[AOIParameter]] = {}
    for controller in parsed_project.controllers:
        for program in controller.programs:
            for routine in program.routines:
                if routine.parameters:
                    key = (controller.name, program.name, routine.name)
                    index[key] = list(routine.parameters)
    return index


# ---------------------------------------------------------------------------
# Canonical id helpers
# ---------------------------------------------------------------------------


def _controller_id(controller_name: str) -> str:
    return f"controller::{controller_name}"


def _program_id(controller_name: str, program_name: str) -> str:
    return f"program::{controller_name}/{program_name}"


def _routine_id(
    controller_name: str, program_name: str, routine_name: str
) -> str:
    return f"routine::{controller_name}/{program_name}/{routine_name}"


def _exec_ctx_id(
    controller_name: str, program_name: str, routine_name: str
) -> str:
    return f"exec::{controller_name}/{program_name}/{routine_name}"


def _rung_id(
    controller_name: str,
    program_name: str,
    routine_name: str,
    rung_number: int,
) -> str:
    return (
        f"rung::{controller_name}/{program_name}/{routine_name}"
        f"/Rung[{rung_number}]"
    )


def _instruction_id(
    controller_name: str,
    program_name: str,
    routine_name: str,
    rung_number: Optional[int],
    instruction: ControlInstruction,
) -> str:
    local = instruction.id or instruction.instruction_type
    if rung_number is None:
        return (
            f"instr::{controller_name}/{program_name}/{routine_name}"
            f"/{local}"
        )
    return (
        f"instr::{controller_name}/{program_name}/{routine_name}"
        f"/Rung[{rung_number}]/{local}"
    )


def _tag_id(
    controller_name: str,
    scope_key: str,
    tag_name: str,
) -> str:
    if scope_key == CONTROLLER_SCOPE_KEY:
        return f"tag::{controller_name}/{tag_name}"
    return f"tag::{controller_name}/{scope_key}/{tag_name}"


__all__ = [
    "normalize_l5x_project",
    "INSTRUCTION_SEMANTICS",
]
