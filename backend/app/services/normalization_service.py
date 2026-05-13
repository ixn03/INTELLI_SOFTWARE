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
      contains ``BST`` / ``NXB`` / ``BND`` tokens. This is a
      conservative signal: we tell consumers that parallel paths
      exist and how many top-level separators were seen, but we
      deliberately do not attribute individual instructions to
      individual branches.
* One ``ExecutionContext`` per routine
  (``ExecutionContextType.ROUTINE``); cause/effect edges reference it
  via ``execution_context_id``.

Out of scope today (registered with implemented=False so the inventory
is captured and future passes can implement them):

* PID / control loops (``PID``).
* Per-branch attribution of instructions (we flag a rung as branched
  but do not say which branch a given XIC belongs to).

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
TODO(intelli/normalization): Add-On Instruction (AOI) handling.
    Instances should resolve InOut / Input / Output parameters into
    REFERENCES / READS / WRITES against the binding tags. Needs L5X
    connector cooperation to expose AddOnInstructionDefinition.
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
    ControlController,
    ControlInstruction,
    ControlProject,
    ControlTag,
)
from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    ExecutionContext,
    ExecutionContextType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
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
    STIfBlock,
    STIfElsifChain,
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
    UNKNOWN = "unknown"                # AOI / vendor / unrecognized


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

    for controller in parsed_project.controllers:
        _normalize_controller(
            controller=controller,
            parsed_project=parsed_project,
            routine_index=routine_index,
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
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
) -> None:

    controller_id = _controller_id(controller.name)

    # Tag lookup keyed by (scope_key, name). scope_key is the program name
    # for program tags or CONTROLLER_SCOPE_KEY for controller-scope tags.
    tag_index: dict[tuple[str, str], str] = {}

    control_objects.append(
        ControlObject(
            id=controller_id,
            name=controller.name,
            object_type=ControlObjectType.CONTROLLER,
            source_platform=controller.platform or "rockwell",
            source_location=f"Controller:{controller.name}",
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "rockwell_platform": controller.platform,
                "source_file": parsed_project.source_file,
                "file_hash": parsed_project.file_hash,
                "project_metadata": parsed_project.metadata or {},
            },
        )
    )

    for tag in controller.controller_tags:
        tag_id = _tag_id(controller.name, CONTROLLER_SCOPE_KEY, tag.name)
        tag_index[(CONTROLLER_SCOPE_KEY, tag.name)] = tag_id

        control_objects.append(
            _tag_to_control_object(
                tag=tag,
                tag_id=tag_id,
                source_location=(
                    f"Controller:{controller.name}/Tag:{tag.name}"
                ),
                parent_ids=[controller_id],
            )
        )
        relationships.append(
            Relationship(
                source_id=controller_id,
                target_id=tag_id,
                relationship_type=RelationshipType.CONTAINS,
                confidence=ConfidenceLevel.HIGH,
                source_platform="rockwell",
            )
        )

    for program in controller.programs:
        program_id = _program_id(controller.name, program.name)
        program_loc = (
            f"Controller:{controller.name}/Program:{program.name}"
        )

        control_objects.append(
            ControlObject(
                id=program_id,
                name=program.name,
                object_type=ControlObjectType.PROGRAM,
                source_platform="rockwell",
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
                source_platform="rockwell",
            )
        )

        for tag in program.tags:
            tag_id = _tag_id(controller.name, program.name, tag.name)
            tag_index[(program.name, tag.name)] = tag_id

            control_objects.append(
                _tag_to_control_object(
                    tag=tag,
                    tag_id=tag_id,
                    source_location=f"{program_loc}/Tag:{tag.name}",
                    parent_ids=[program_id, controller_id],
                )
            )
            relationships.append(
                Relationship(
                    source_id=program_id,
                    target_id=tag_id,
                    relationship_type=RelationshipType.CONTAINS,
                    confidence=ConfidenceLevel.HIGH,
                    source_platform="rockwell",
                )
            )

        for routine in program.routines:
            _normalize_routine(
                controller_name=controller.name,
                controller_id=controller_id,
                program_name=program.name,
                program_id=program_id,
                program_loc=program_loc,
                routine=routine,
                tag_index=tag_index,
                routine_index=routine_index,
                control_objects=control_objects,
                relationships=relationships,
                execution_contexts=execution_contexts,
            )


# ---------------------------------------------------------------------------
# Per-routine normalization
# ---------------------------------------------------------------------------


def _normalize_routine(
    controller_name: str,
    controller_id: str,
    program_name: str,
    program_id: str,
    program_loc: str,
    routine,
    tag_index: dict[tuple[str, str], str],
    routine_index: dict[tuple[str, str, str], str],
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
) -> None:

    routine_id = _routine_id(controller_name, program_name, routine.name)
    routine_loc = f"{program_loc}/Routine:{routine.name}"

    control_objects.append(
        ControlObject(
            id=routine_id,
            name=routine.name,
            object_type=ControlObjectType.ROUTINE,
            source_platform="rockwell",
            source_location=routine_loc,
            parent_ids=[program_id, controller_id],
            attributes={
                "language": routine.language,
                "instruction_count": len(routine.instructions),
            },
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "rockwell_metadata": routine.metadata or {},
                "raw_logic_present": bool(routine.raw_logic),
            },
        )
    )
    relationships.append(
        Relationship(
            source_id=program_id,
            target_id=routine_id,
            relationship_type=RelationshipType.CONTAINS,
            confidence=ConfidenceLevel.HIGH,
            source_platform="rockwell",
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
            source_platform="rockwell",
            source_location=routine_loc,
            confidence=ConfidenceLevel.MEDIUM,
        )
    )

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
            control_objects=control_objects,
            relationships=relationships,
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
            control_objects=control_objects,
            relationships=relationships,
        )
        return

    # FBD / SFC: preserve routine + instructions with explicit unsupported
    # parse status (no vendor diagram parser yet).
    if routine.language in ("function_block", "sfc"):
        routine_co = control_objects[-1]
        ps_r = dict(routine_co.platform_specific)
        ps_r["parse_status"] = "unsupported_language"
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
        routine_co.confidence = ConfidenceLevel.LOW

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
    control_objects: list[ControlObject] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    rung_has_branches: bool = False
    rung_branch_count: int = 1
    branch_warnings: list[str] = field(default_factory=list)


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
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:

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
                },
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "raw_rung_text": rung_raw_text,
                    "rung_has_branches": rung_has_branches,
                    "rung_branch_count": rung_branch_count,
                    "branch_warnings": branch_warnings,
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
            control_objects=control_objects,
            relationships=relationships,
            rung_has_branches=rung_has_branches,
            rung_branch_count=rung_branch_count,
            branch_warnings=branch_warnings,
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

            _dispatch_instruction_semantics(
                instruction=instruction,
                ctx=rung_ctx,
            )


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
    control_objects: list[ControlObject],
    relationships: list[Relationship],
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
            control_objects=control_objects,
            relationships=relationships,
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
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> None:
    """Dispatch on block type and emit the synthetic statement + edges."""

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
    if isinstance(block, STComplexBlock):
        ps = dict(stmt_obj.platform_specific)
        if block.fragment_kind:
            ps["fragment_kind"] = block.fragment_kind
        if block.callee_name:
            ps["callee_name"] = block.callee_name
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
        return "if", status, block.raw_text
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
    if isinstance(block, STComplexBlock):
        if block.fragment_kind and block.fragment_kind.startswith("loop_"):
            return "loop", "too_complex", block.raw_text
        if block.fragment_kind == "fb_invocation":
            return "fb_invocation", "ok", block.raw_text
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
) -> None:
    """Emit edges for each THEN / ELSE assignment under an IF block.

    THEN-branch assignments inherit ``block.condition_expression`` as
    the gating expression -- this is what carries OR / comparison
    information through to the assignment-level emitter.
    ELSE-branch assignments inherit the boolean negation of the
    THEN condition when it is exactly one term (single identifier
    or single comparison); anything else marks
    ``branch_label="ELSE_too_complex"`` and the ELSE assignments
    carry no parsed gating reads.
    """

    # THEN: pass the full expression through unmodified.
    then_expression = (
        block.condition_expression
        if (
            block.condition_expression is not None
            and not block.too_complex_condition
        )
        else None
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
            gating_expression=then_expression,
        )

    if not block.else_assignments:
        return

    else_expression, else_label = _build_else_gating(block)

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
            gating_expression=else_expression,
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
    # PID / CONTROL_LOOP and UNKNOWN families remain undispatched -- they
    # are still represented structurally as INSTRUCTION ControlObjects
    # via the CONTAINS pass.


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
    # TODO(intelli/normalization): JSR additionally passes input / output
    # parameters (operands 1..N), which should become READS / WRITES
    # against the caller-side and parameter-side tags once we model
    # routine parameters.
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
    for idx, op in enumerate(instruction.operands[1:], start=1):
        if not op or not _looks_like_tag_operand(op):
            continue
        tag_id = _resolve_tag_id_or_stub(
            operand=op,
            controller_name=ctx.controller_name,
            program_name=ctx.program_name,
            tag_index=ctx.tag_index,
            control_objects=ctx.control_objects,
        )
        ctx.relationships.append(
            Relationship(
                source_id=ctx.rung_id,
                target_id=tag_id,
                relationship_type=RelationshipType.READS,
                execution_context_id=ctx.exec_ctx_id,
                logic_condition=ctx.rung_raw_text,
                source_platform="rockwell",
                source_location=ctx.rung_loc,
                confidence=ConfidenceLevel.MEDIUM,
                platform_specific=_rel_meta(
                    ctx,
                    instruction,
                    operand=op,
                    extras={
                        "operand_index": idx,
                        "operand_role": "jsr_parameter",
                        "gating_kind": "jsr_parameter",
                    },
                ),
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
    if ctx.rung_has_branches:
        meta["rung_has_branches"] = True
        meta["rung_branch_count"] = ctx.rung_branch_count
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
) -> ControlObject:

    return ControlObject(
        id=tag_id,
        name=tag.name,
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
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
        },
    )


def _instruction_to_control_object(
    instruction: ControlInstruction,
    instr_id: str,
    source_location: str,
    parent_ids: list[str],
    *,
    confidence_override: Optional[ConfidenceLevel] = None,
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
        source_platform="rockwell",
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
            "semantic_notes": sem.notes if sem else "",
            **one_shot_meta,
        },
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
