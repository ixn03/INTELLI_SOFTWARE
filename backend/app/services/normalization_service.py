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
* One ``ExecutionContext`` per routine
  (``ExecutionContextType.ROUTINE``); cause/effect edges reference it
  via ``execution_context_id``.

Out of scope today (registered with implemented=False so the inventory
is captured and future passes can implement them):

* Comparisons (``EQU`` / ``NEQ`` / ``LES`` / ``LEQ`` / ``GRT`` /
  ``GEQ`` / ``LIM``).
* Math (``ADD`` / ``SUB`` / ``MUL`` / ``DIV`` / ``CPT``).
* Move / copy (``MOV`` / ``COP``).
* One-shots (``ONS`` / ``OSR`` / ``OSF``).
* PID / control loops (``PID``).

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

TODO(intelli/normalization): Structured Text normalization. The ST
    parser already exposes ``output`` for ASSIGN / MOV / COP / CPT /
    timer/counter function calls. Wire ST routines through a family
    handler analogous to the ladder one, with an instruction-level
    source (instead of rung-level) for WRITES/READS edges.
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

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

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

    # ---- Comparisons (registered, not yet emitting edges) -----------------
    # TODO(intelli/normalization): Comparisons read both operands and gate
    # the rung. A future implementation should emit READS for both
    # operands when they look like tag references, and potentially attach
    # the comparison expression to the rung as evidence.
    "EQU": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Equal: rung true when operand[0] == operand[1].",
    ),
    "NEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Not Equal.",
    ),
    "LES": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Less Than.",
    ),
    "LEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Less Than Or Equal.",
    ),
    "GRT": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Greater Than.",
    ),
    "GEQ": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1),
        notes="Greater Than Or Equal.",
    ),
    "LIM": _InstructionSemantics(
        family=_InstructionFamily.COMPARISON,
        read_operand_indices=(0, 1, 2),
        notes="Limit Test: low/test/high.",
    ),

    # ---- Math (registered, not yet emitting edges) ------------------------
    # TODO(intelli/normalization): Math instructions read source operands
    # and write a destination operand. Rockwell convention:
    #     ADD/SUB/MUL/DIV(Source_A, Source_B, Dest)  -> write index 2
    #     CPT(Dest, Expression)                       -> write index 0
    # Both ladder and ST parsers leave ``ControlInstruction.output``
    # unset for ladder math today, so the registry already records the
    # write index but the dispatcher leaves them off until verified.
    "ADD": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Addition: Dest = Source_A + Source_B.",
    ),
    "SUB": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Subtraction.",
    ),
    "MUL": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Multiplication.",
    ),
    "DIV": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        read_operand_indices=(0, 1),
        write_operand_index=2,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes="Division.",
    ),
    "CPT": _InstructionSemantics(
        family=_InstructionFamily.MATH,
        write_operand_index=0,
        write_behavior=WriteBehaviorType.CALCULATES,
        notes=(
            "Compute: Dest = Expression. The expression's tag operands "
            "are not exposed by the current ladder parser, so reads are "
            "deferred until parser support lands."
        ),
    ),

    # ---- Move / copy (registered, not yet emitting edges) -----------------
    "MOV": _InstructionSemantics(
        family=_InstructionFamily.MOVE_COPY,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.MOVES_VALUE,
        notes="Move: Dest = Source.",
    ),
    "COP": _InstructionSemantics(
        family=_InstructionFamily.MOVE_COPY,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.MOVES_VALUE,
        notes="Copy File: COP(Source, Dest, Length).",
    ),

    # ---- One-shots (registered, not yet emitting edges) -------------------
    # TODO(intelli/normalization): One-shots have a storage bit that is
    # both read (previous scan) and written (this scan). Encoding both
    # correctly requires per-edge "examined_value" + "write_behavior=pulses".
    "ONS": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=0,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot: pulses when rung transitions false->true.",
    ),
    "OSR": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot Rising: OSR(StorageBit, OutputBit).",
    ),
    "OSF": _InstructionSemantics(
        family=_InstructionFamily.ONE_SHOT,
        read_operand_indices=(0,),
        write_operand_index=1,
        write_behavior=WriteBehaviorType.PULSES,
        notes="One Shot Falling: OSF(StorageBit, OutputBit).",
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_l5x_project(parsed_project: ControlProject) -> dict:
    """Convert a parsed L5X ``ControlProject`` into reasoning-schema lists.

    Pure function: does not mutate ``parsed_project``. All output models
    are freshly constructed.

    Returns:
        A dict with three lists:
            * ``control_objects``    - list[ControlObject]
            * ``relationships``      - list[Relationship]
            * ``execution_contexts`` - list[ExecutionContext]
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

    # Non-ladder languages: still emit instruction ControlObjects so the
    # graph captures structure, but do not emit cause/effect edges.
    # Writes/reads for ST and other languages are deliberately left for a
    # later pass. See module-level TODOs.
    for instruction in routine.instructions:
        instr_id = _instruction_id(
            controller_name=controller_name,
            program_name=program_name,
            routine_name=routine.name,
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


# ---------------------------------------------------------------------------
# Ladder rung / instruction handling
# ---------------------------------------------------------------------------


@dataclass
class _RungContext:
    """Bundle of state passed to instruction family handlers.

    The handlers append to ``control_objects`` / ``relationships`` and
    read from the resolver indices. Keeping the bundle as a single
    object keeps handler signatures short and consistent.
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
        rung_raw_text = " ".join(
            i.raw_text for i in rung_instructions if i.raw_text
        ).strip() or None

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
                },
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "raw_rung_text": rung_raw_text,
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
        # are shared with the caller so handlers append directly.
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
    # Other families are intentionally not dispatched yet — their
    # registry entries carry ``implemented=False`` and the early return
    # above catches them. See the module-level TODOs.


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
                platform_specific={
                    "instruction_type": instruction.instruction_type,
                    "instruction_id": instruction.id,
                    "examined_value": sem.examined_value,
                },
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
            platform_specific={
                "instruction_type": instruction.instruction_type,
                "instruction_id": instruction.id,
            },
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
            platform_specific={
                "instruction_type": instruction.instruction_type,
                "instruction_id": instruction.id,
            },
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
            platform_specific={
                "instruction_type": instruction.instruction_type,
                "instruction_id": instruction.id,
            },
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
            platform_specific={
                "instruction_type": instruction.instruction_type,
                "instruction_id": instruction.id,
                "jsr_parameters": list(instruction.operands[1:]),
            },
        )
    )


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
) -> ControlObject:

    sem = INSTRUCTION_SEMANTICS.get(instruction.instruction_type)
    family_value = sem.family.value if sem else _InstructionFamily.UNKNOWN.value

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
        confidence=ConfidenceLevel.HIGH,
        platform_specific={
            "raw_text": instruction.raw_text,
            "instruction_local_id": instruction.id,
            "rockwell_metadata": instruction.metadata or {},
            "semantic_notes": sem.notes if sem else "",
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
