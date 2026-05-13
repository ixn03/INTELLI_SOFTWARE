from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field


class ControlTag(BaseModel):
    name: str
    data_type: Optional[str] = None
    description: Optional[str] = None
    scope: Optional[str] = None
    platform_source: Optional[str] = None
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
