from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.connectors.registry import connector_catalog, get_connector
from app.models.control_model import ControlProject, ExplanationResult, TraceResult
from app.models.reasoning import TraceResult as ReasoningTraceResult
from app.services.explanation_service import explain_trace
from app.services.graph_service import graph_summary
from app.services.project_store import project_store
from app.services.question_router_service import answer_question
from app.services.trace_service import trace_object, trace_tag
from app.services.trace_v2_service import trace_object_v2


router = APIRouter()


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
        return project_store.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    relationship_count: int
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


@router.get(
    "/api/normalized-summary",
    response_model=NormalizedSummaryResponse,
)
def normalized_summary_v1() -> NormalizedSummaryResponse:
    """Inspect the normalized reasoning graph for the latest upload.

    Returns counts plus the first 20 control objects and first 20
    relationships. Use this to find valid ``target_object_id`` values
    before calling ``POST /api/trace-v1``.
    """

    project_id, normalized = _require_latest_normalized()
    control_objects = normalized["control_objects"]
    relationships = normalized["relationships"]
    execution_contexts = normalized["execution_contexts"]

    return NormalizedSummaryResponse(
        project_id=project_id,
        control_object_count=len(control_objects),
        relationship_count=len(relationships),
        execution_context_count=len(execution_contexts),
        control_objects=[
            NormalizedControlObjectSummary(
                id=o.id,
                name=o.name,
                object_type=o.object_type.value,
                source_location=o.source_location,
            )
            for o in control_objects[:20]
        ],
        relationships=[
            NormalizedRelationshipSummary(
                source_id=r.source_id,
                target_id=r.target_id,
                relationship_type=r.relationship_type.value,
                source_location=r.source_location,
                platform_specific=dict(r.platform_specific),
            )
            for r in relationships[:20]
        ],
    )
