from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field


class ControlTag(BaseModel):
    name: str
    data_type: Optional[str] = None
    description: Optional[str] = None
    scope: Optional[str] = None
    platform_source: Optional[str] = None
    # When the tag is an alias (Rockwell TagType="Alias", Siemens symbol
    # alias, ...), ``alias_for`` names the base tag/expression it points
    # at so normalization can follow the alias to the real signal.
    alias_for: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AOIParameter(BaseModel):
    """One parameter of an Add-On Instruction / Function Block definition.

    Vendor-flavored staging model. ``usage`` is the raw direction string
    ("Input" / "Output" / "InOut" for Rockwell); the universal direction
    mapping (READS / WRITES / READS+WRITES) is applied in the normalizer.
    """

    name: str
    usage: Optional[str] = None  # Input | Output | InOut (Rockwell)
    data_type: Optional[str] = None
    required: bool = False
    visible: bool = True
    # AOI-internal parameters can themselves be aliases (e.g. ``Inp``
    # aliasing a bit of a hidden member). Preserved but not required.
    alias_for: Optional[str] = None


class AddOnInstructionDef(BaseModel):
    """A reusable logic-block definition (Rockwell AOI today).

    This is the connector's private staging shape. The *universal*
    representation of an instance call is a ``FUNCTION_BLOCK``
    ``ControlObject`` plus typed parameter-binding edges emitted by the
    normalizer (see ``reasoning.py``). A future Siemens FB definition maps
    onto this same staging model.
    """

    name: str
    parameters: List[AOIParameter] = Field(default_factory=list)
    # Name of the AOI's internal logic routine (if exposed), so an
    # instance can be linked to its body via a CALLS edge.
    logic_routine: Optional[str] = None
    revision: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataTypeMember(BaseModel):
    name: str
    data_type: Optional[str] = None
    description: Optional[str] = None


class DataTypeDef(BaseModel):
    """A user-defined type / UDT (Rockwell DataType, Siemens UDT/DB type)."""

    name: str
    members: List[DataTypeMember] = Field(default_factory=list)
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlInstruction(BaseModel):
    instruction_type: str
    operands: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = None
    language: Optional[str] = None
    id: Optional[str] = None
    output: Optional[str] = None
    rung_number: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlRoutine(BaseModel):
    name: str
    language: Optional[
        Literal[
            "ladder",
            "structured_text",
            "function_block",
            "sfc",
            "tia_ob",
            "tia_fb",
            "tia_fc",
            "tia_db",
            "unknown",
        ]
    ] = "unknown"
    instructions: List[ControlInstruction] = Field(default_factory=list)
    raw_logic: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parse_status: Optional[Literal["parsed", "unsupported", "preserved_only"]] = None


class ControlProgram(BaseModel):
    name: str
    tags: List[ControlTag] = Field(default_factory=list)
    routines: List[ControlRoutine] = Field(default_factory=list)


class ControlController(BaseModel):
    name: str
    platform: Literal["rockwell", "siemens_tia", "honeywell", "deltav", "unknown"] = "unknown"
    controller_tags: List[ControlTag] = Field(default_factory=list)
    programs: List[ControlProgram] = Field(default_factory=list)
    # Reusable definitions discovered in the export. Vendor-flavored
    # staging only; the universal graph models instances/edges in
    # reasoning.py.
    add_on_instruction_defs: List[AddOnInstructionDef] = Field(default_factory=list)
    data_type_defs: List[DataTypeDef] = Field(default_factory=list)


class ControlProject(BaseModel):
    project_name: str
    source_file: Optional[str] = None
    file_hash: Optional[str] = None
    controllers: List[ControlController] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceCause(BaseModel):
    tag: str
    relationship: str
    instruction_type: Optional[str] = None
    routine: Optional[str] = None
    program: Optional[str] = None
    raw_text: Optional[str] = None


class TraceResult(BaseModel):
    target_tag: str
    question: str = "why_false"
    status: Optional[str] = None
    summary: str
    causes: List[TraceCause] = Field(default_factory=list)
    evidence: Optional[dict[str, Any]] = None


class ExplanationResult(BaseModel):
    target_tag: str
    explanation: str
    trace: TraceResult
