from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.connectors.registry import connector_catalog, get_connector
from app.models.control_model import ControlProject, ExplanationResult, TraceResult
from app.services.explanation_service import explain_trace
from app.services.graph_service import graph_summary
from app.services.project_store import project_store
from app.services.trace_service import trace_tag


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
