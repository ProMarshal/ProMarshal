"""
Agent Router
New /agent/* endpoints for the orchestrator-based charter flow.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List

from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_any
from app.core.database import get_database
from app.planner.agent import CharterOrchestrator, AgentMode, AgentAction
from app.planner.service import planner_service


router = APIRouter(
    prefix="/api/agent",
    tags=["agent"],
    dependencies=[Depends(get_current_user)],
)


async def _ensure_agent_project_access(project_id: str, current_user: dict) -> None:
    db = get_database()
    await assert_project_access_any(project_id, str(current_user.get("user_id") or ""), db)


# Request/Response Models

class StartSessionRequest(BaseModel):
    project_id: str
    topic: str
    mode: str  # "brainstorm" or "document"
    project_type: str = "Software / Product Development"
    project_name: str = ""


class ChatRequest(BaseModel):
    project_id: str
    topic: str
    message: str
    project_type: str = "Software / Product Development"


class FinalizeRequest(BaseModel):
    project_id: str
    topic: str
    project_type: str = "Software / Product Development"


class DocumentExtractRequest(BaseModel):
    project_id: str
    file_ids: List[str]
    project_type: str = "Software / Product Development"
    project_name: str = ""


class AgentResponseModel(BaseModel):
    action: str
    message: Optional[str] = None
    draft: List[str] = []
    gaps: List[str] = []
    validation_result: Optional[dict] = None
    error: Optional[str] = None
    metadata: dict = {}


# Helper to convert internal response to API response
def to_response_model(response) -> AgentResponseModel:
    return AgentResponseModel(
        action=response.action.value,
        message=response.message,
        draft=response.draft,
        gaps=response.gaps,
        validation_result=response.validation_result,
        error=response.error,
        metadata=response.metadata
    )


# Endpoints

@router.post("/start", response_model=AgentResponseModel)
async def start_session(
    request: StartSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Start a new agent session.
    
    Mode: "brainstorm" or "document"
    """
    await _ensure_agent_project_access(request.project_id, current_user)
    orchestrator = CharterOrchestrator(planner_service)
    
    mode = AgentMode.BRAINSTORM if request.mode == "brainstorm" else AgentMode.DOCUMENT
    
    response = await orchestrator.start_session(
        project_id=request.project_id,
        topic=request.topic,
        mode=mode,
        project_type=request.project_type,
        project_name=request.project_name
    )
    
    return to_response_model(response)


@router.post("/chat", response_model=AgentResponseModel)
async def agent_chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Send a chat message in the current session.
    """
    await _ensure_agent_project_access(request.project_id, current_user)
    orchestrator = CharterOrchestrator(planner_service)
    
    response = await orchestrator.process_chat(
        project_id=request.project_id,
        topic=request.topic,
        message=request.message,
        project_type=request.project_type
    )
    
    return to_response_model(response)


@router.post("/upload", response_model=AgentResponseModel)
async def upload_document(
    project_id: str = Form(...),
    topic: str = Form(...),
    project_type: str = Form("Software / Product Development"),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload and process a document for extraction.
    
    Returns extracted content with validation results.
    """
    await _ensure_agent_project_access(project_id, current_user)
    orchestrator = CharterOrchestrator(planner_service)
    
    # Read file content
    file_content = await file.read()
    
    response = await orchestrator.process_document(
        project_id=project_id,
        topic=topic,
        file_content=file_content,
        filename=file.filename,
        project_type=project_type
    )
    
    return to_response_model(response)


@router.post("/document-extract")
async def document_extract(
    request: DocumentExtractRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Extract project charter content from uploaded documents via agent flow.
    """
    await _ensure_agent_project_access(request.project_id, current_user)
    if not request.file_ids:
        raise HTTPException(status_code=400, detail="No file IDs provided")

    orchestrator = CharterOrchestrator(planner_service)

    extraction_result = await orchestrator.process_document_extraction(
        project_id=request.project_id,
        file_ids=request.file_ids,
        project_type=request.project_type,
        project_name=request.project_name
    )

    return {
        "success": True,
        **extraction_result
    }


@router.post("/finalize", response_model=AgentResponseModel)
async def finalize_topic(
    request: FinalizeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Finalize a topic.
    
    Runs validation first. If issues found, returns to chat.
    """
    await _ensure_agent_project_access(request.project_id, current_user)
    orchestrator = CharterOrchestrator(planner_service)
    
    response = await orchestrator.process_finalize(
        project_id=request.project_id,
        topic=request.topic,
        project_type=request.project_type
    )
    
    return to_response_model(response)


@router.get("/draft/{project_id}/{topic}", response_model=AgentResponseModel)
async def get_draft(
    project_id: str,
    topic: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get current draft state for a topic.
    """
    await _ensure_agent_project_access(project_id, current_user)
    orchestrator = CharterOrchestrator(planner_service)
    
    response = await orchestrator.get_draft(
        project_id=project_id,
        topic=topic
    )
    
    return to_response_model(response)
