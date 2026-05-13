from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.connectors.registry import connector_catalog, get_connector
from app.models.control_model import ControlProject, ExplanationResult, TraceResult
from app.models.reasoning import TraceResult as ReasoningTraceResult
from app.services.explanation_service import explain_trace
from app.services.graph_service import graph_summary
from app.services.project_store import project_store
from app.services.ask_v2_service import answer_question_v2
from app.services.question_router_service import answer_question
from app.services.runtime_ingestion_service import (
    normalize_csv_runtime_values,
    normalize_runtime_snapshot,
)
from app.services.runtime_evaluation_v2_service import (
    evaluate_trace_runtime_v2,
)
from app.services.runtime_snapshot_service import evaluate_trace_conditions
from app.services.trace_service import trace_object, trace_tag
from app.services.trace_v2_service import trace_object_v2
from app.services.sequence_reasoning_service import (
    analyze_sequences,
    filter_sequence_result_for_tag,
)
from app.services.sequence_semantics_service import analyze_sequence_semantics
from app.models.reasoning import ControlObject, ConfidenceLevel
from app.models.knowledge import (
    KnowledgeItem,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeVerification,
)
from app.services.knowledge_service import knowledge_service
from app.services.version_compare_service import compare_projects
from app.services.version_intelligence_service import analyze_version_impact
from app.services.llm_assist_service import answer_with_llm_assist
from app.services.runtime_adapter_registry import get_adapter, list_adapter_descriptors


router = APIRouter()

_OBJECT_TYPE_ALIASES: dict[str, str] = {
    "tags": "tag",
    "routines": "routine",
    "rungs": "rung",
    "instructions": "instruction",
    "controllers": "controller",
    "programs": "program",
}


def _coerce_query_int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _canonical_object_type_filter(raw: Any) -> Optional[str]:
    if raw is None or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if s == "all":
        return None
    mapped = _OBJECT_TYPE_ALIASES.get(s, s)
    if mapped == "":
        return None
    return mapped


def _control_object_matches_filters(
    o: ControlObject,
    *,
    object_type: Optional[str],
    search: Optional[str],
) -> bool:
    if object_type is not None:
        if o.object_type.value != object_type:
            return False
    if search is not None and isinstance(search, str) and search.strip():
        q = search.strip().lower()
        hay = " ".join(
            [
                o.id,
                o.name or "",
                o.object_type.value,
                o.source_location or "",
            ]
        ).lower()
        if q not in hay:
            return False
    return True


def _filtered_control_objects(
    control_objects: list[ControlObject],
    object_type: Optional[str],
    search: Optional[str],
) -> list[ControlObject]:
    ot = _canonical_object_type_filter(object_type)
    return [
        o
        for o in control_objects
        if _control_object_matches_filters(o, object_type=ot, search=search)
    ]


class UploadResponse(BaseModel):
    project_id: str
    connector: str
    project: ControlProject
    graph: dict[str, int]


class ExplainRequest(BaseModel):
    project_id: str
    target_tag: str
    question: str = "why_false"


@router.get("/connectors")
def list_connectors() -> list[dict[str, str]]:
    return connector_catalog()


@router.get("/projects")
def list_projects() -> list[ControlProject]:
    return project_store.list()


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> ControlProject:
    try:
        project = project_store.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Align ``latest`` with the project the UI restored so /api/* matches it.
    project_store.set_latest(project_id)
    return project


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    content = await file.read()
    filename = file.filename or "uploaded_file"

    try:
        connector = get_connector(filename, content)
        project = connector.parse(filename, content)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project_store.save(project)

    return UploadResponse(
        project_id=project.file_hash or "",
        connector=connector.display_name,
        project=project,
        graph=graph_summary(project),
    )


@router.get("/projects/{project_id}/trace/{target_tag}", response_model=TraceResult)
def trace_project_tag(
    project_id: str,
    target_tag: str,
    question: str = "why_false",
) -> TraceResult:
    try:
        project = project_store.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return trace_tag(project, target_tag, question)


@router.post("/explain", response_model=ExplanationResult)
def explain_project_trace(request: ExplainRequest) -> ExplanationResult:
    try:
        project = project_store.get(request.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    trace = trace_tag(project, request.target_tag, request.question)
    return explain_trace(trace)


# ---------------------------------------------------------------------------
# v1 reasoning endpoints (development/debug only)
#
# These expose the new normalized-reasoning pipeline:
#     parsed ControlProject  ->  normalize_l5x_project  ->  trace_object
# without disturbing any existing route or frontend behavior. Both
# endpoints operate against the most recently uploaded project (tracked
# by ``project_store.latest()``) so the existing upload flow doubles as
# the input mechanism. Re-uploading a project invalidates the
# normalized cache for that file hash automatically.
# ---------------------------------------------------------------------------


class TraceV1Request(BaseModel):
    target_object_id: str


class NormalizedControlObjectSummary(BaseModel):
    id: str
    name: Optional[str] = None
    object_type: str
    source_location: Optional[str] = None


class NormalizedRelationshipSummary(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str
    source_location: Optional[str] = None
    platform_specific: dict[str, Any]


class NormalizedSummaryResponse(BaseModel):
    project_id: Optional[str] = None
    control_object_count: int
    total_control_object_count: int
    returned_control_object_count: int
    offset: int = 0
    limit: int = 100
    relationship_count: int
    total_relationship_count: int
    returned_relationship_count: int
    rel_offset: int = 0
    rel_limit: int = 100
    execution_context_count: int
    control_objects: list[NormalizedControlObjectSummary]
    relationships: list[NormalizedRelationshipSummary]


def _require_latest_normalized() -> tuple[str, dict[str, Any]]:
    """Return ``(project_id, normalized_output)`` for the latest upload.

    Raises ``HTTPException(404)`` when no project has been uploaded
    yet, so v1 endpoints all share consistent "you need to upload
    first" semantics.
    """

    project_id = project_store.latest_id()
    if project_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No project has been uploaded yet. POST a file to "
                "/upload before calling the /api/* reasoning endpoints."
            ),
        )
    try:
        normalized = project_store.get_normalized(project_id)
    except KeyError as exc:
        # Defensive: latest_id pointed at a project that vanished.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return project_id, normalized


@router.post("/api/trace-v1", response_model=ReasoningTraceResult)
def trace_object_v1(request: TraceV1Request) -> ReasoningTraceResult:
    """Run a deterministic Trace v1 against the most recently uploaded
    project. Returns the reasoning-schema ``TraceResult`` (JSON).

    Request body::

        { "target_object_id": "tag::PLC01/MainProgram/Motor_Run" }

    To discover valid ids, hit ``GET /api/normalized-summary`` first.
    """

    _, normalized = _require_latest_normalized()
    return trace_object(
        target_object_id=request.target_object_id,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
    )


@router.post("/api/trace-v2", response_model=ReasoningTraceResult)
def trace_object_v2_endpoint(
    request: TraceV1Request,
) -> ReasoningTraceResult:
    """Run a deterministic Trace v2 against the most recently uploaded
    project. Returns the same ``TraceResult`` shape as Trace v1, with
    natural-language and condition-aware conclusions prepended to
    ``conclusions`` and surfaced in ``summary``.

    Request body is identical to ``/api/trace-v1``::

        { "target_object_id": "tag::PLC01/MainProgram/Motor_Run" }
    """

    _, normalized = _require_latest_normalized()
    return trace_object_v2(
        target_object_id=request.target_object_id,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
    )


class EvaluateRuntimeV1Request(BaseModel):
    target_object_id: str
    runtime_snapshot: dict[str, Any]


@router.post("/api/evaluate-runtime-v1", response_model=ReasoningTraceResult)
def evaluate_runtime_v1(
    request: EvaluateRuntimeV1Request,
) -> ReasoningTraceResult:
    """Trace the target and overlay a caller-supplied runtime snapshot.

    Runs Trace v2 to produce the design-time writer conditions, then
    calls :func:`evaluate_trace_conditions` to compare each
    ``(tag, required_value)`` condition against the provided
    ``runtime_snapshot``. Returns the augmented ``TraceResult`` whose
    ``conclusions`` now lead with one per satisfied / blocking /
    missing condition, whose ``summary`` includes a single-sentence
    runtime verdict, and whose ``platform_specific`` carries the
    structured per-bucket data (``trace_version="runtime_v1"``,
    ``runtime_snapshot_evaluated=True``, plus the three condition
    lists).

    Request body::

        {
            "target_object_id": "tag::PLC01/MainProgram/Pump_Run",
            "runtime_snapshot": {
                "StartPB": true,
                "AutoMode": true,
                "Faulted": false
            }
        }
    """

    _, normalized = _require_latest_normalized()
    base = trace_object_v2(
        target_object_id=request.target_object_id,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
    )
    return evaluate_trace_conditions(base, request.runtime_snapshot)


class EvaluateRuntimeV2Request(BaseModel):
    target_object_id: str
    runtime_snapshot: dict[str, Any]


@router.post("/api/evaluate-runtime-v2", response_model=ReasoningTraceResult)
def evaluate_runtime_v2(
    request: EvaluateRuntimeV2Request,
) -> ReasoningTraceResult:
    """Trace the target and overlay a runtime snapshot with v2 semantics.

    Runs Trace v2 to produce the design-time writer conditions, then
    calls :func:`evaluate_trace_runtime_v2` which:

    * evaluates boolean, timer-member and comparison conditions,
    * groups them with ``or_branch_index`` to handle AND / OR / AND-OR,
    * classifies each writer path (satisfied / blocked / incomplete /
      unsupported),
    * combines write-effect categories (sets-true vs sets-false vs
      other) into one of five overall verdicts
      (``target_can_be_on``, ``target_likely_off_or_reset``,
      ``conflict_or_scan_order_dependent``, ``blocked``, ``incomplete``),
    * prepends a single primary operational conclusion ahead of the
      existing Trace v2 / v1 conclusions, and
    * records the structured per-bucket data plus per-path results in
      ``platform_specific``.

    The legacy ``POST /api/evaluate-runtime-v1`` endpoint is preserved
    unchanged for callers that only need boolean checks.

    Request body::

        {
            "target_object_id": "tag::PLC01/MainProgram/Pump_Run",
            "runtime_snapshot": {
                "StartPB": true,
                "AutoMode": true,
                "Faulted": false,
                "Tank_Level": 82,
                "State": 1,
                "Timer1.DN": true
            }
        }
    """

    _, normalized = _require_latest_normalized()
    base = trace_object_v2(
        target_object_id=request.target_object_id,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
    )
    return evaluate_trace_runtime_v2(base, request.runtime_snapshot)


class RuntimeNormalizeRequest(BaseModel):
    snapshot: dict[str, Any]


class RuntimeNormalizeCsvRequest(BaseModel):
    csv_text: str


@router.post("/api/runtime/normalize")
def runtime_normalize(request: RuntimeNormalizeRequest) -> dict[str, Any]:
    """Normalize a JSON runtime snapshot to :class:`RuntimeSnapshotModel` JSON."""

    model = normalize_runtime_snapshot(request.snapshot)
    return model.model_dump(mode="json")


@router.post("/api/runtime/normalize-csv")
def runtime_normalize_csv(request: RuntimeNormalizeCsvRequest) -> dict[str, Any]:
    """Parse CSV rows into a normalized :class:`RuntimeSnapshotModel`."""

    model = normalize_csv_runtime_values(request.csv_text)
    return model.model_dump(mode="json")


class AskV1Request(BaseModel):
    question: str


@router.post("/api/ask-v1", response_model=ReasoningTraceResult)
def ask_v1(request: AskV1Request) -> ReasoningTraceResult:
    """Deterministic question router (v1).

    Accepts a free-text question, identifies a target control object
    by exact id or name match, classifies intent via keywords, and
    routes the call to ``trace_object_v2``. The returned
    ``TraceResult`` is decorated with router metadata in
    ``platform_specific`` (``question``, ``detected_target_object_id``,
    ``detected_intent``, ``router_version``).

    If the question doesn't name a known control object, a
    low-confidence result is returned with a clear recovery hint --
    no exception is raised for "no match" so the UI can render the
    same result shape on every call.

    Request body::

        { "question": "Why is PMP_LiOH_B_Run not running?" }
    """

    _, normalized = _require_latest_normalized()
    return answer_question(
        question=request.question,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
    )


class AskV2Request(BaseModel):
    question: str
    runtime_snapshot: Optional[dict[str, Any]] = None


class SequenceTraceRequest(BaseModel):
    state_tag_id: str


@router.post("/api/ask-v2", response_model=ReasoningTraceResult)
def ask_v2(request: AskV2Request) -> ReasoningTraceResult:
    """Deterministic orchestration: trace v2, optionally + runtime v2.

    See :mod:`app.services.ask_v2_service` for routing rules. No LLM.
    """

    _, normalized = _require_latest_normalized()
    return answer_question_v2(
        question=request.question,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized["execution_contexts"],
        runtime_snapshot=request.runtime_snapshot,
    )


@router.get(
    "/api/normalized-summary",
    response_model=NormalizedSummaryResponse,
)
def normalized_summary_v1(
    limit: int = Query(100, ge=1, le=5000, description="Page size for control objects."),
    offset: int = Query(0, ge=0, description="Offset into filtered control objects."),
    object_type: Optional[str] = Query(
        None,
        description="Filter by object type (e.g. tag, routine, rung, instruction).",
    ),
    search: Optional[str] = Query(
        None,
        description="Case-insensitive substring match on id, name, type, location.",
    ),
    rel_limit: int = Query(
        100,
        ge=1,
        le=5000,
        description="Page size for relationships.",
    ),
    rel_offset: int = Query(0, ge=0, description="Offset into relationships."),
) -> NormalizedSummaryResponse:
    """Inspect the normalized reasoning graph for the latest upload.

    Control objects and relationships are paginated independently.
    """

    project_id, normalized = _require_latest_normalized()
    control_objects: list[ControlObject] = normalized["control_objects"]
    relationships = normalized["relationships"]
    execution_contexts = normalized["execution_contexts"]

    limit = _coerce_query_int(limit, 100)
    offset = _coerce_query_int(offset, 0)
    rel_limit = _coerce_query_int(rel_limit, 100)
    rel_offset = _coerce_query_int(rel_offset, 0)
    object_type = object_type if isinstance(object_type, str) else None
    search = search if isinstance(search, str) else None

    filtered = _filtered_control_objects(control_objects, object_type, search)
    project_total = len(control_objects)
    matching_total = len(filtered)
    slice_co = filtered[offset : offset + limit]

    total_rel = len(relationships)
    slice_rel = relationships[rel_offset : rel_offset + rel_limit]

    return NormalizedSummaryResponse(
        project_id=project_id,
        control_object_count=project_total,
        total_control_object_count=matching_total,
        returned_control_object_count=len(slice_co),
        offset=offset,
        limit=limit,
        relationship_count=total_rel,
        total_relationship_count=total_rel,
        returned_relationship_count=len(slice_rel),
        rel_offset=rel_offset,
        rel_limit=rel_limit,
        execution_context_count=len(execution_contexts),
        control_objects=[
            NormalizedControlObjectSummary(
                id=o.id,
                name=o.name,
                object_type=o.object_type.value,
                source_location=o.source_location,
            )
            for o in slice_co
        ],
        relationships=[
            NormalizedRelationshipSummary(
                source_id=r.source_id,
                target_id=r.target_id,
                relationship_type=r.relationship_type.value,
                source_location=r.source_location,
                platform_specific=dict(r.platform_specific),
            )
            for r in slice_rel
        ],
    )


class ControlObjectsPageResponse(BaseModel):
    project_id: str
    project_control_object_count: int
    total_control_object_count: int
    returned_control_object_count: int
    offset: int
    limit: int
    control_objects: list[NormalizedControlObjectSummary]


@router.get("/api/control-objects", response_model=ControlObjectsPageResponse)
def list_control_objects(
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    object_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
) -> ControlObjectsPageResponse:
    """Paginated control objects for the latest normalized project."""

    project_id, normalized = _require_latest_normalized()
    control_objects: list[ControlObject] = normalized["control_objects"]
    limit = _coerce_query_int(limit, 100)
    offset = _coerce_query_int(offset, 0)
    object_type = object_type if isinstance(object_type, str) else None
    search = search if isinstance(search, str) else None

    filtered = _filtered_control_objects(control_objects, object_type, search)
    project_total = len(control_objects)
    matching_total = len(filtered)
    slice_co = filtered[offset : offset + limit]
    return ControlObjectsPageResponse(
        project_id=project_id,
        project_control_object_count=project_total,
        total_control_object_count=matching_total,
        returned_control_object_count=len(slice_co),
        offset=offset,
        limit=limit,
        control_objects=[
            NormalizedControlObjectSummary(
                id=o.id,
                name=o.name,
                object_type=o.object_type.value,
                source_location=o.source_location,
            )
            for o in slice_co
        ],
    )


def _sequence_summary_payload() -> dict[str, Any]:
    """Shared body for sequence summary routes."""

    project_id, normalized = _require_latest_normalized()
    out = analyze_sequences(
        normalized["control_objects"],
        normalized["relationships"],
        normalized.get("execution_contexts") or [],
    )
    return {"project_id": project_id, **out}


@router.get("/api/sequence-summary")
def sequence_summary_v1() -> dict[str, Any]:
    """Deterministic sequence/state analysis for the latest upload."""

    return _sequence_summary_payload()


@router.get("/sequence-summary")
def sequence_summary_no_api_prefix() -> dict[str, Any]:
    """Alias without ``/api`` for older clients or misconfigured proxies."""

    return _sequence_summary_payload()


@router.post("/api/sequence-trace")
def sequence_trace_v1(request: SequenceTraceRequest) -> dict[str, Any]:
    """Sequence/state slice for a single state tag id."""

    _, normalized = _require_latest_normalized()
    full = analyze_sequences(
        normalized["control_objects"],
        normalized["relationships"],
        normalized.get("execution_contexts") or [],
    )
    return filter_sequence_result_for_tag(full, request.state_tag_id)


class SequenceSemanticsRequest(BaseModel):
    runtime_snapshot: Optional[dict[str, Any]] = None


@router.post("/api/sequence-semantics")
def sequence_semantics_v1(request: SequenceSemanticsRequest) -> dict[str, Any]:
    _, normalized = _require_latest_normalized()
    return analyze_sequence_semantics(
        normalized["control_objects"],
        normalized["relationships"],
        normalized.get("execution_contexts") or [],
        runtime_snapshot=request.runtime_snapshot,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Knowledge builder (v1)
# ---------------------------------------------------------------------------


class KnowledgeCreateRequest(BaseModel):
    knowledge_type: KnowledgeType
    statement: str
    target_object_id: Optional[str] = None
    target_name: Optional[str] = None
    source: str = "engineer"
    confidence: Optional[str] = None
    verified_by: Optional[str] = None
    status: KnowledgeStatus = KnowledgeStatus.PROPOSED
    evidence_links: list[str] = Field(default_factory=list)
    version_range: Optional[str] = None
    verification: KnowledgeVerification = Field(default_factory=KnowledgeVerification)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgePatchRequest(BaseModel):
    statement: Optional[str] = None
    status: Optional[KnowledgeStatus] = None
    verified_by: Optional[str] = None
    rejected_by: Optional[str] = None
    superseded_by: Optional[str] = None
    evidence_links: Optional[list[str]] = None
    version_range: Optional[str] = None
    verification: Optional[KnowledgeVerification] = None
    metadata: Optional[dict[str, Any]] = None


class KnowledgeDecisionRequest(BaseModel):
    actor: str
    verification_reason: Optional[str] = None
    plant_scope: Optional[str] = None
    equipment_scope: Optional[str] = None
    superseded_by: Optional[str] = None


@router.post("/api/knowledge")
def create_knowledge(req: KnowledgeCreateRequest) -> dict[str, Any]:
    conf = ConfidenceLevel.MEDIUM
    if req.confidence:
        try:
            conf = ConfidenceLevel(req.confidence)
        except ValueError:
            conf = ConfidenceLevel.MEDIUM
    item = KnowledgeItem(
        knowledge_type=req.knowledge_type,
        statement=req.statement,
        target_object_id=req.target_object_id,
        target_name=req.target_name,
        source=req.source,
        confidence=conf,
        verified_by=req.verified_by,
        status=req.status,
        evidence_links=list(req.evidence_links),
        version_range=req.version_range,
        verification=req.verification,
        metadata=dict(req.metadata),
    )
    knowledge_service.create(item)
    return item.model_dump(mode="json")


@router.get("/api/knowledge")
def list_knowledge() -> list[dict[str, Any]]:
    return [i.model_dump(mode="json") for i in knowledge_service.list_all()]


@router.get("/api/knowledge/by-target/{target_object_id}")
def list_knowledge_by_target(target_object_id: str) -> list[dict[str, Any]]:
    return [
        i.model_dump(mode="json")
        for i in knowledge_service.list_by_target(target_object_id)
    ]


@router.patch("/api/knowledge/{item_id}")
def patch_knowledge(item_id: str, req: KnowledgePatchRequest) -> dict[str, Any]:
    updated = knowledge_service.patch(
        item_id,
        statement=req.statement,
        status=req.status,
        verified_by=req.verified_by,
        rejected_by=req.rejected_by,
        superseded_by=req.superseded_by,
        evidence_links=req.evidence_links,
        version_range=req.version_range,
        verification=req.verification,
        metadata=req.metadata,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return updated.model_dump(mode="json")


@router.post("/api/knowledge/{item_id}/approve")
def approve_knowledge(item_id: str, req: KnowledgeDecisionRequest) -> dict[str, Any]:
    updated = knowledge_service.approve(
        item_id,
        verified_by=req.actor,
        verification_reason=req.verification_reason,
        plant_scope=req.plant_scope,
        equipment_scope=req.equipment_scope,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return updated.model_dump(mode="json")


@router.post("/api/knowledge/{item_id}/reject")
def reject_knowledge(item_id: str, req: KnowledgeDecisionRequest) -> dict[str, Any]:
    updated = knowledge_service.reject(
        item_id,
        rejected_by=req.actor,
        verification_reason=req.verification_reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return updated.model_dump(mode="json")


@router.post("/api/knowledge/{item_id}/supersede")
def supersede_knowledge(item_id: str, req: KnowledgeDecisionRequest) -> dict[str, Any]:
    updated = knowledge_service.supersede(
        item_id,
        superseded_by=req.superseded_by or req.actor,
        verification_reason=req.verification_reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return updated.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Version compare (v1)
# ---------------------------------------------------------------------------


class CompareProjectsRequest(BaseModel):
    old_project_id: str
    new_project_id: str


@router.post("/api/compare-projects")
def compare_projects_endpoint(req: CompareProjectsRequest) -> dict[str, Any]:
    try:
        old_n = project_store.get_normalized(req.old_project_id)
        new_n = project_store.get_normalized(req.new_project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    diff = compare_projects(old_n, new_n)
    return asdict(diff)


@router.post("/api/version-impact")
def version_impact_endpoint(req: CompareProjectsRequest) -> dict[str, Any]:
    try:
        old_n = project_store.get_normalized(req.old_project_id)
        new_n = project_store.get_normalized(req.new_project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return asdict(analyze_version_impact(old_n, new_n))


@router.post("/api/compare-latest-to-upload")
def compare_latest_to_upload(req: CompareProjectsRequest) -> dict[str, Any]:
    """Alias: compares two stored project ids (name kept for roadmap compatibility)."""

    return compare_projects_endpoint(req)


# ---------------------------------------------------------------------------
# Runtime adapters (v1)
# ---------------------------------------------------------------------------


@router.get("/api/runtime/adapters")
def list_runtime_adapters() -> list[dict[str, str]]:
    return list_adapter_descriptors()


class RuntimeReadRequest(BaseModel):
    adapter: str = "simulated"
    tag_names: list[str] = Field(default_factory=list)
    csv_text: Optional[str] = None
    values: Optional[dict[str, Any]] = None


@router.post("/api/runtime/read")
def runtime_read(req: RuntimeReadRequest) -> dict[str, Any]:
    try:
        adapter = get_adapter(
            req.adapter,
            csv_text=req.csv_text or "",
            values=req.values or {},
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snap = adapter.read_tags(req.tag_names)
    return snap.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Ask v3 (LLM assist, feature-flagged)
# ---------------------------------------------------------------------------


class AskV3Request(BaseModel):
    question: str
    runtime_snapshot: Optional[dict[str, Any]] = None
    current_selected_object: Optional[str] = None
    last_discussed_state: Optional[str] = None
    prior_runtime_snapshot: Optional[dict[str, Any]] = None
    prior_sequence_discussion: Optional[dict[str, Any]] = None
    answer_style: str = "controls_engineer"


class LLMAssistResponse(BaseModel):
    """Structured ask-v3 payload: natural answer plus full deterministic trace."""

    answer: str
    confidence: str
    target_object_id: Optional[str] = None
    detected_intent: str
    evidence_used: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    deterministic_result: ReasoningTraceResult


@router.post("/api/ask-v3", response_model=LLMAssistResponse)
def ask_v3(request: AskV3Request) -> LLMAssistResponse:
    """Deterministic-first assist; optional LLM rewrite when ``ENABLE_LLM_ASSIST`` is on."""

    _, normalized = _require_latest_normalized()
    raw = answer_with_llm_assist(
        question=request.question,
        control_objects=normalized["control_objects"],
        relationships=normalized["relationships"],
        execution_contexts=normalized.get("execution_contexts") or [],
        runtime_snapshot=request.runtime_snapshot,
        llm=None,
        enable_llm=None,
        conversation_context={
            "current_selected_object": request.current_selected_object,
            "last_discussed_state": request.last_discussed_state,
            "prior_runtime_snapshot_present": bool(request.prior_runtime_snapshot),
            "prior_sequence_discussion": request.prior_sequence_discussion or {},
        },
        answer_style=request.answer_style,
    )
    return LLMAssistResponse(
        answer=raw["answer"],
        confidence=raw["confidence"],
        target_object_id=raw.get("target_object_id"),
        detected_intent=raw.get("detected_intent", "unknown"),
        evidence_used=dict(raw.get("evidence_used") or {}),
        warnings=list(raw.get("warnings") or []),
        deterministic_result=raw["deterministic_result"],
    )
