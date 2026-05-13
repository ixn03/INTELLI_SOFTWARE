"""Normalized reasoning schema for INTELLI.

This module defines the platform-agnostic data models used by the core
reasoning layer. The goal is to express control-system knowledge
(tags, rungs, routines, controllers, devices, alarms, sequences, ...)
and the relationships between them in a way that is independent of any
one vendor or file format (Rockwell L5X, Siemens TIA, Honeywell, etc.),
while still allowing platform-specific data to be carried alongside in
``platform_specific``.

Only schema is defined here. No parsing, graph building, persistence,
or I/O. Parsers, connectors, and the reasoning engine will emit
instances of these models in later modules.

Every reasoning-layer object exposes a common set of fields:
``id``, ``name``, ``source_platform``, ``source_location``,
``confidence``, ``confidence_score``, ``evidence``, and
``platform_specific``. These come from the shared ``_CoreFields`` base.

Example (schematic only)::

    obj = ControlObject(
        id="tag::Plant1.Pump_01.Run_PB",
        name="Pump_01.Run_PB",
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
        source_location="Controller/Program:MainProgram/Tag:Pump_01.Run_PB",
    )
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ControlObjectType(str, Enum):
    """The kind of control-system object a ``ControlObject`` represents.

    Examples:
        - ``tag``               -> ``Pump_01.Run_PB`` (boolean input)
        - ``parameter``         -> a routine/function-block parameter
        - ``rung``              -> a single rung in a ladder routine
        - ``routine``           -> ``MainRoutine``
        - ``program``           -> a Rockwell Program / Siemens block group
        - ``controller``        -> a CompactLogix / S7-1500 / etc.
        - ``task``              -> a scheduled task (Periodic / Continuous)
        - ``equipment_module``  -> ISA-88 equipment module
        - ``control_module``    -> ISA-88 control module
        - ``pid_loop``          -> a regulatory PID block / loop
        - ``alarm``             -> an alarm definition / instance
        - ``sequence`` / ``sfc_step`` -> SFC sequence and one of its steps
    """

    TAG = "tag"
    PARAMETER = "parameter"
    INSTRUCTION = "instruction"
    RUNG = "rung"
    ROUTINE = "routine"
    PROGRAM = "program"
    CONTROLLER = "controller"
    TASK = "task"
    EQUIPMENT = "equipment"
    DEVICE = "device"
    ALARM = "alarm"
    TIMER = "timer"
    COUNTER = "counter"
    PID_LOOP = "pid_loop"
    SEQUENCE = "sequence"
    SFC_STEP = "sfc_step"
    # --- SFC (schema placeholders for future chart parsers) ---
    SFC_TRANSITION = "sfc_transition"
    SFC_ACTION = "sfc_action"
    SFC_CONDITION = "sfc_condition"
    ACTIVE_STEP_TAG = "active_step_tag"
    # --- FBD (schema placeholders for future diagram parsers) ---
    FBD_INPUT_PIN = "fbd_input_pin"
    FBD_OUTPUT_PIN = "fbd_output_pin"
    FBD_BLOCK_INSTANCE = "fbd_block_instance"
    FBD_PARAMETER_BINDING = "fbd_parameter_binding"
    CONTROL_MODULE = "control_module"
    EQUIPMENT_MODULE = "equipment_module"
    FUNCTION_BLOCK = "function_block"
    UNKNOWN = "unknown"


class ControlRole(str, Enum):
    """Semantic role a ``ControlObject`` plays in the control strategy.

    ``object_type`` says *what kind of artifact* something is (a tag, a
    rung, a PID block, ...). ``ControlRole`` says *what job it does* in
    the control strategy. The same ``object_type=TAG`` can be a
    ``command``, a ``feedback``, a ``permissive``, an ``alarm``, etc.

    Examples:
        - ``command``           -> ``Pump_01.Run`` (operator/logic command bit)
        - ``feedback``          -> ``Pump_01.RunFeedback`` (run-confirm)
        - ``permissive``        -> ``Permit_OK`` (gates a START)
        - ``interlock``         -> ``EStop_OK`` (forces a STOP / blocks motion)
        - ``alarm``             -> ``HighLevel_ALM``
        - ``mode`` / ``state``  -> ``Pump_01.Mode`` / ``Pump_01.State``
        - ``setpoint`` / ``process_variable`` -> PID SP / PV
        - ``internal_memory``   -> scratch bit used only inside one routine
        - ``sequence_step``     -> a step inside an SFC sequence
    """

    COMMAND = "command"
    FEEDBACK = "feedback"
    PERMISSIVE = "permissive"
    INTERLOCK = "interlock"
    FAULT = "fault"
    ALARM = "alarm"
    MODE = "mode"
    STATE = "state"
    SETPOINT = "setpoint"
    PROCESS_VARIABLE = "process_variable"
    INTERNAL_MEMORY = "internal_memory"
    CALCULATION = "calculation"
    TIMER = "timer"
    COUNTER = "counter"
    SEQUENCE_STEP = "sequence_step"
    UNKNOWN = "unknown"


class RelationshipType(str, Enum):
    """Semantic relationship between two ``ControlObject`` nodes.

    The vocabulary is biased toward controls / cause-and-effect terms
    rather than generic graph edges. Every ``Relationship`` has a
    ``source_id`` and a ``target_id``; read the edge as
    ``source <relationship_type> target``.

    Examples:
        - rung A ``writes``      tag T (OTE / assignment)
        - rung B ``reads``       tag T (XIC / XIO / expression use)
        - tag X ``permits``      rung B (interlock / permissive)
        - alarm Z ``inhibits``   sequence S (alarm holds the sequence)
        - command C ``starts``   motor M
        - motor M ``confirms``   via run-feedback tag R
        - rung K ``latches`` / ``unlatches`` tag L (OTL / OTU)
        - PID  P  ``calculates`` CV from PV and SP
    """

    READS = "reads"
    WRITES = "writes"
    CONTAINS = "contains"
    CALLS = "calls"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    CONDITION_FOR = "condition_for"
    PERMITS = "permits"
    INHIBITS = "inhibits"
    COMMANDS = "commands"
    CONFIRMS = "confirms"
    STARTS = "starts"
    STOPS = "stops"
    FAULTS = "faults"
    ALARMS = "alarms"
    LATCHES = "latches"
    UNLATCHES = "unlatches"
    RESETS = "resets"
    CALCULATES = "calculates"
    SCALES = "scales"
    SEQUENCES = "sequences"
    OVERRIDES = "overrides"
    TRACKS = "tracks"
    AFFECTS = "affects"
    # --- FBD / wiring (placeholders; emit only when parsers supply pins) ---
    SIGNAL_CONNECTS = "signal_connects"
    BINDS_PARAMETER = "binds_parameter"
    UNKNOWN = "unknown"


class WriteBehaviorType(str, Enum):
    """How a ``WRITES``-style relationship actually modifies its target.

    ``RelationshipType.WRITES`` (and friends like ``LATCHES`` /
    ``CALCULATES``) says *that* something writes a target. This enum
    describes *how* the value is being changed when the write fires.
    It is attached to a ``Relationship`` via the optional
    ``write_behavior`` field.

    Examples:
        - ``sets_true`` / ``sets_false`` -> ``OTE`` energizing / de-energizing
        - ``latches`` / ``unlatches``    -> ``OTL`` / ``OTU``
        - ``moves_value``                -> ``MOV Source Dest``
        - ``calculates`` / ``scales``    -> ``CPT`` / ``SCL`` / PID CV
        - ``pulses``                     -> one-shot ``ONS`` / ``OSR``
        - ``holds_previous_value``       -> write that no-ops on this scan
        - ``increments`` / ``decrements`` -> ``ADD 1`` / ``SUB 1`` / counter step
        - ``transitions_state``          -> state-machine / SFC step move
    """

    SETS_TRUE = "sets_true"
    SETS_FALSE = "sets_false"
    LATCHES = "latches"
    UNLATCHES = "unlatches"
    MOVES_VALUE = "moves_value"
    CALCULATES = "calculates"
    SCALES = "scales"
    PULSES = "pulses"
    HOLDS_PREVIOUS_VALUE = "holds_previous_value"
    INCREMENTS = "increments"
    DECREMENTS = "decrements"
    TRANSITIONS_STATE = "transitions_state"
    UNKNOWN = "unknown"


class ExecutionContextType(str, Enum):
    """How / when a piece of control logic actually executes.

    Examples:
        - ``continuous``     -> always-on continuous task
        - ``periodic``       -> fixed-rate task (e.g. 100 ms)
        - ``event_driven``   -> triggered by an input / event
        - ``sequence_step``  -> active only inside an SFC / phase step
        - ``manual_command`` -> operator-initiated
        - ``fault_handler``  -> runs on the fault / shutdown path
        - ``simulation``     -> running against a sim, not real plant
    """

    CONTINUOUS = "continuous"
    PERIODIC = "periodic"
    EVENT_DRIVEN = "event_driven"
    TASK = "task"
    SCAN = "scan"
    ROUTINE = "routine"
    SEQUENCE_STEP = "sequence_step"
    TIMED = "timed"
    MANUAL_COMMAND = "manual_command"
    FAULT_HANDLER = "fault_handler"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    SIMULATION = "simulation"
    UNKNOWN = "unknown"


class TruthContextType(str, Enum):
    """Which "kind of truth" a conclusion or piece of evidence is grounded in.

    The reasoning layer carefully distinguishes static design intent
    from observed runtime behavior, plant-floor verification, and
    historical / documentation sources. A conclusion that combines
    several is tagged ``composite_truth``.

    Examples:
        - ``design_truth``         -> what the L5X / source code says
        - ``runtime_truth``        -> what the live controller reports
        - ``verified_plant_truth`` -> confirmed by a human on the floor
        - ``historical_truth``     -> from historian / trends
        - ``documentation_truth``  -> from P&IDs, narratives, SOPs
        - ``composite_truth``      -> derived by combining the above
    """

    DESIGN_TRUTH = "design_truth"
    RUNTIME_TRUTH = "runtime_truth"
    VERIFIED_PLANT_TRUTH = "verified_plant_truth"
    HISTORICAL_TRUTH = "historical_truth"
    DOCUMENTATION_TRUTH = "documentation_truth"
    COMPOSITE_TRUTH = "composite_truth"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    """What kind of artifact a single piece of ``Evidence`` comes from.

    Examples:
        - ``source_code``        -> an L5X / ST / LAD snippet
        - ``control_narrative``  -> text from a written control narrative
        - ``platform_metadata``  -> vendor metadata (revision, date, owner)
        - ``tag_name_pattern``   -> inference from a naming convention
        - ``runtime_value``      -> a sampled live tag value
        - ``historian_data``     -> a trend / aggregate from a historian
        - ``engineer_feedback``  -> an SME annotation
        - ``alarm_event``        -> an alarm log entry
        - ``derived_logic``      -> inferred by the reasoning engine
        - ``configuration``      -> from a config file / project setting
    """

    SOURCE_CODE = "source_code"
    CONTROL_NARRATIVE = "control_narrative"
    PLATFORM_METADATA = "platform_metadata"
    TAG_NAME_PATTERN = "tag_name_pattern"
    RUNTIME_VALUE = "runtime_value"
    HISTORIAN_DATA = "historian_data"
    ENGINEER_FEEDBACK = "engineer_feedback"
    ALARM_EVENT = "alarm_event"
    DERIVED_LOGIC = "derived_logic"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class ConfidenceLevel(str, Enum):
    """Qualitative confidence buckets.

    Pair with the optional numeric ``confidence_score`` (0.0 - 1.0) on
    core models when a finer-grained value is available.
    """

    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Default id generator: a UUID4 string.

    Callers are free to override with their own stable identifiers
    (e.g. ``"tag::Plant1.Pump_01.Run"``) — this default just guarantees
    that every object has a non-empty ``id`` without forcing the caller
    to invent one up front.
    """

    return str(uuid.uuid4())


class _CoreFields(BaseModel):
    """Mixin providing the fields every reasoning-layer object supports.

    Per spec, every object exposes:

        id, name, source_platform, source_location,
        confidence, confidence_score, evidence, platform_specific.

    ``platform_specific`` is intentionally an open ``dict[str, Any]`` so
    connectors can attach vendor-specific data (Rockwell L5X attributes,
    Siemens block headers, Honeywell point descriptors, ...) without
    polluting the normalized schema. Anything platform-agnostic should
    be promoted to a real field instead of living here.
    """

    model_config = ConfigDict(use_enum_values=False, extra="ignore")

    id: str = Field(default_factory=_new_id)
    name: Optional[str] = None
    source_platform: Optional[str] = None
    source_location: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    confidence_score: Optional[float] = None
    evidence: list["Evidence"] = Field(default_factory=list)
    platform_specific: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class Evidence(_CoreFields):
    """A single piece of evidence supporting (or contradicting) a claim.

    Evidence is itself a first-class object so it can be cited, audited,
    and combined. The ``evidence`` field inherited from ``_CoreFields``
    can be used to attach sub-evidence (for example, an aggregate piece
    of evidence built from multiple source-code snippets).

    Example (schematic only)::

        Evidence(
            id="ev::rung3_writes_pump01",
            evidence_type=EvidenceType.SOURCE_CODE,
            truth_context=TruthContextType.DESIGN_TRUTH,
            statement="Rung 3 in MainRoutine writes Pump_01.Run.",
            source_excerpt="OTE Pump_01.Run",
            source_platform="rockwell",
            source_location="MainProgram/MainRoutine/Rung[3]",
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.95,
        )
    """

    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    truth_context: TruthContextType = TruthContextType.UNKNOWN
    statement: Optional[str] = None
    source_excerpt: Optional[str] = None
    captured_at: Optional[str] = None  # ISO-8601 timestamp string, optional


# ---------------------------------------------------------------------------
# ControlObject
# ---------------------------------------------------------------------------


class ControlObject(_CoreFields):
    """A normalized control-system object (tag, rung, routine, ...).

    ``ControlObject`` is the central node of the reasoning graph. Every
    addressable piece of a control system maps to one ``ControlObject``,
    regardless of vendor. Cause/effect structure lives on
    ``Relationship`` edges between ``ControlObject`` nodes.

    Field notes:
        - ``role``            -> semantic role in the control strategy (``ControlRole``)
        - ``description``     -> human-readable description (free text)
        - ``parent_ids``      -> ids of containing objects (program, controller, ...)
        - ``attributes``      -> static, normalized attributes (data type, units, ...)
        - ``current_state``   -> last-known runtime state (value, quality, ...)
        - ``failure_impact``  -> what breaks downstream if this object fails
        - ``control_meaning`` -> 1-line plain-English meaning of this object

    Example (schematic only)::

        ControlObject(
            id="tag::Plant1.Pump_01.Run",
            name="Pump_01.Run",
            object_type=ControlObjectType.TAG,
            role=ControlRole.COMMAND,
            description="Command bit driving Pump 01 starter.",
            source_platform="rockwell",
            source_location="MainProgram/Tag:Pump_01.Run",
            parent_ids=["program::MainProgram"],
            attributes={"data_type": "BOOL"},
            current_state={"value": False, "quality": "good"},
            failure_impact=["Pump_01 cannot start"],
            control_meaning="If 1, the starter is energized.",
        )
    """

    object_type: ControlObjectType = ControlObjectType.UNKNOWN
    role: ControlRole = ControlRole.UNKNOWN
    description: Optional[str] = None
    parent_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    current_state: dict[str, Any] = Field(default_factory=dict)
    failure_impact: list[str] = Field(default_factory=list)
    control_meaning: Optional[str] = None


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------


class ExecutionContext(_CoreFields):
    """When / under what conditions a piece of logic actually runs.

    ``ExecutionContext`` lets the reasoning layer answer questions like
    "is this rung even being scanned right now?" — which is critical for
    troubleshooting why a tag isn't changing. Relationships can point at
    an ``ExecutionContext`` via ``Relationship.execution_context_id`` to
    say "this WRITE only happens inside this context".

    Example (schematic only)::

        ExecutionContext(
            id="exec::MainTask.scan",
            name="MainTask scan",
            context_type=ExecutionContextType.PERIODIC,
            period_ms=100,
            controller_id="controller::PLC01",
            active=True,
        )
    """

    context_type: ExecutionContextType = ExecutionContextType.UNKNOWN
    description: Optional[str] = None
    period_ms: Optional[float] = None
    trigger: Optional[str] = None
    priority: Optional[int] = None
    controller_id: Optional[str] = None
    parent_context_id: Optional[str] = None
    active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------


class Relationship(_CoreFields):
    """A directed cause/effect edge between two ``ControlObject`` nodes.

    Read as ``source_id <relationship_type> target_id``. For example,
    a rung that energizes a motor coil would be modeled as::

        Relationship(
            source_id="rung::MainProgram.MainRoutine.Rung[3]",
            target_id="tag::Pump_01.Run",
            relationship_type=RelationshipType.WRITES,
            execution_context_id="exec::MainTask.scan",
            logic_condition="Start_PB AND NOT EStop AND Permit_OK",
            timing_behavior="evaluated every scan (~100 ms)",
        )

    ``conflict_risk`` / ``conflict_notes`` let the reasoning engine flag
    cases like "two rungs both WRITE the same coil", "PERMITS and
    INHIBITS pointing the opposite way", or "OTL with no matching OTU".
    """

    source_id: str
    target_id: str
    relationship_type: RelationshipType = RelationshipType.UNKNOWN
    write_behavior: Optional[WriteBehaviorType] = None
    execution_context_id: Optional[str] = None
    logic_condition: Optional[str] = None
    timing_behavior: Optional[str] = None
    conflict_risk: Optional[bool] = None
    conflict_notes: Optional[str] = None


# ---------------------------------------------------------------------------
# TruthConclusion
# ---------------------------------------------------------------------------


class TruthConclusion(_CoreFields):
    """A reasoned claim about one or more ``ControlObject`` nodes.

    A ``TruthConclusion`` is the reasoning layer's "answer". It records
    the actual statement, what it's about (``subject_ids``), what
    evidence supports it, what evidence contradicts it, the kind of
    truth it's grounded in (``truth_context``), and what follow-up
    the engineer should perform if confidence isn't high enough.

    Example (schematic only)::

        TruthConclusion(
            statement="Pump_01 will not start because Permit_OK is FALSE.",
            subject_ids=["tag::Pump_01.Run", "tag::Permit_OK"],
            truth_context=TruthContextType.COMPOSITE_TRUTH,
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.88,
            supporting_evidence=[...],
            conflicting_evidence=[],
            recommended_checks=["Verify Permit_OK conditions on rung 7."],
        )
    """

    statement: str
    subject_ids: list[str] = Field(default_factory=list)
    truth_context: TruthContextType = TruthContextType.UNKNOWN
    supporting_evidence: list[Evidence] = Field(default_factory=list)
    conflicting_evidence: list[Evidence] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TraceResult
# ---------------------------------------------------------------------------


class TraceResult(_CoreFields):
    """The result of a cause/effect trace around a target ``ControlObject``.

    A trace answers questions like "why is this tag FALSE?" or "what
    would change if I force this bit?". It collects the upstream
    (causes) and downstream (effects) neighborhood of an object, the
    relevant relationships split into writers vs readers, the
    conclusions drawn, and any follow-up checks for the engineer.

    Note: ``writer_relationships`` and ``reader_relationships`` are
    convenience splits of ``relationships`` for callers that want to
    treat "who drives this tag" separately from "who consumes this
    tag". They are not required to be disjoint with ``relationships``.

    Example (schematic only)::

        TraceResult(
            target_object_id="tag::Pump_01.Run",
            upstream_object_ids=["tag::Start_PB", "tag::Permit_OK"],
            downstream_object_ids=["tag::Pump_01.RunFeedback"],
            writer_relationships=[...],
            reader_relationships=[...],
            relationships=[...],
            conclusions=[...],
            recommended_checks=["Confirm Permit_OK on HMI."],
            failure_impact=["Pump 01 down -> tank fill stalls."],
            summary="Pump_01.Run is held off by Permit_OK.",
        )
    """

    target_object_id: str
    upstream_object_ids: list[str] = Field(default_factory=list)
    downstream_object_ids: list[str] = Field(default_factory=list)
    writer_relationships: list[Relationship] = Field(default_factory=list)
    reader_relationships: list[Relationship] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    conclusions: list[TruthConclusion] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    failure_impact: list[str] = Field(default_factory=list)
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# TroubleshootingRecord
# ---------------------------------------------------------------------------


class TroubleshootingRecord(_CoreFields):
    """A historical record of a troubleshooting / failure event.

    Used to capture what went wrong, what was tried, what fixed it, and
    what was learned, so the reasoning layer can recall similar issues
    later. ``deprecated=True`` lets us retire records whose root cause
    no longer applies (e.g. after a code rewrite) without deleting
    history. ``access_level`` is a free-form string (e.g. ``"public"``,
    ``"internal"``, ``"restricted"``) so the application layer can
    enforce its own visibility rules.

    Example (schematic only)::

        TroubleshootingRecord(
            issue="Pump 01 would not start on day shift.",
            affected_object_ids=["tag::Pump_01.Run"],
            symptoms=["Start PB pressed, no motor amps"],
            root_cause="Permit_OK held FALSE by stuck level switch.",
            checks_performed=["Forced Permit_OK -> motor ran."],
            attempted_fixes=["Cleaned LS-101."],
            verified_fix="Replaced LS-101.",
            outcome="Resolved",
            downtime_duration="00:45:00",
            related_logic_version="v2024.08.01",
            confidence=ConfidenceLevel.HIGH,
            confidence_score=0.9,
            verified_by="J. Operator",
            access_level="internal",
            deprecated=False,
            lessons_learned=["Add diagnostic alarm for stuck LS-101."],
        )
    """

    issue: str
    affected_object_ids: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    root_cause: Optional[str] = None
    checks_performed: list[str] = Field(default_factory=list)
    attempted_fixes: list[str] = Field(default_factory=list)
    verified_fix: Optional[str] = None
    outcome: Optional[str] = None
    downtime_duration: Optional[str] = None
    related_logic_version: Optional[str] = None
    historian_window: Optional[dict[str, Any]] = None
    runtime_snapshot: Optional[dict[str, Any]] = None
    verified_by: Optional[str] = None
    access_level: Optional[str] = None
    deprecated: bool = False
    lessons_learned: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Forward-reference resolution
# ---------------------------------------------------------------------------

# ``_CoreFields`` declares ``evidence: list["Evidence"]`` before
# ``Evidence`` exists in the module namespace. Pydantic v2 normally
# resolves these forward references lazily, but we trigger
# ``model_rebuild()`` explicitly here so that any failure surfaces at
# import time rather than at first instantiation.
_CoreFields.model_rebuild()
Evidence.model_rebuild()
ControlObject.model_rebuild()
ExecutionContext.model_rebuild()
Relationship.model_rebuild()
TruthConclusion.model_rebuild()
TraceResult.model_rebuild()
TroubleshootingRecord.model_rebuild()


__all__ = [
    # Enums
    "ControlObjectType",
    "ControlRole",
    "RelationshipType",
    "WriteBehaviorType",
    "ExecutionContextType",
    "TruthContextType",
    "EvidenceType",
    "ConfidenceLevel",
    # Models
    "Evidence",
    "ControlObject",
    "ExecutionContext",
    "Relationship",
    "TruthConclusion",
    "TraceResult",
    "TroubleshootingRecord",
]
