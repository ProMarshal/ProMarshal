"""API routes for Project Planner - PM Agent chat endpoints"""

import asyncio
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Response
import io

from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_any
from app.core.config import settings
from app.core.database import get_database, get_brain_collection
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_CORTEX_RUNS
from app.planner.schemas import ChatMessage, ChatResponse, FinalizeRequest, DocumentExtractionRequest, CharterModeRequest, DraftUpdateRequest
from app.planner.planner_cache import (
    acquire_planner_bootstrap_job_lock,
    get_cached_planner_bootstrap_payload,
    get_planner_cache_metrics,
    get_planner_bootstrap_job_status,
    get_cached_current_stage,
    get_cached_planner_status,
    get_cached_stage_entities,
    has_planner_bootstrap_job_lock,
    invalidate_all_planner_cache,
    invalidate_current_stage,
    invalidate_planner_bootstrap_payload,
    invalidate_planner_status,
    invalidate_stage_entities,
    planner_cache_enabled,
    release_planner_bootstrap_job_lock,
    set_cached_planner_bootstrap_payload,
    set_cached_current_stage,
    set_cached_planner_status,
    set_cached_stage_entities,
    set_planner_bootstrap_job_status,
)
from app.planner.service import planner_service
from app.planner.agent import CharterOrchestrator

logger = logging.getLogger("app.planner.router")


async def _require_planner_auth_and_project_access(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    if project_id:
        db = get_database()
        await assert_project_access_any(project_id, str(current_user.get("user_id") or ""), db)
    return current_user


router = APIRouter(
    prefix="/api/planner",
    tags=["planner"],
    dependencies=[Depends(_require_planner_auth_and_project_access)],
)


@router.get("/ops/cache-metrics")
async def get_planner_cache_metrics_route():
    """Internal diagnostics for planner cache behavior."""
    return get_planner_cache_metrics()


def _invalidate_planner_read_caches(
    project_id: str,
    *,
    stage: Optional[str] = None,
    invalidate_all_entities: bool = False,
) -> None:
    if not planner_cache_enabled():
        return
    try:
        invalidate_planner_bootstrap_payload(project_id)
        if invalidate_all_entities:
            invalidate_all_planner_cache(project_id)
            return
        invalidate_planner_status(project_id)
        invalidate_current_stage(project_id)
        if stage:
            invalidate_stage_entities(project_id, stage)
    except Exception as exc:
        print(
            "planner_cache_invalidation_failed: "
            f"project_id={project_id} stage={stage} all={invalidate_all_entities} error={exc}"
        )


async def validate_topic_stage(project_id: str, stage: str) -> None:
    topic_data = await planner_service.get_project_topics(project_id)
    active_ids = {t.get("topic_id") for t in topic_data.get("active_topics", [])}
    optional_ids = {t.get("id") for t in topic_data.get("available_optional_topics", [])}
    if stage not in active_ids and stage not in optional_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid stage"
        )


def _planner_bootstrap_async_enabled() -> bool:
    return bool(getattr(settings, "planner_bootstrap_async_enabled", True))


def _normalize_stage_status(topic_status: str) -> str:
    normalized = str(topic_status or "").strip().lower()
    if normalized == "finalized":
        return "finalized"
    if normalized in {"in_progress", "draft", "active"}:
        return "in_progress"
    return "pending"


def _stage_ids_from_topics(topics: Dict[str, Any]) -> List[str]:
    active_topics = topics.get("active_topics", []) if isinstance(topics, dict) else []
    stage_ids: List[str] = []
    for topic in sorted(active_topics, key=lambda t: t.get("display_order", 99)):
        topic_id = str(topic.get("topic_id") or "").strip()
        if topic_id and topic_id not in stage_ids:
            stage_ids.append(topic_id)
    return stage_ids


async def _compose_status_payload_from_entities(
    project_id: str,
    topics: Dict[str, Any],
    stage_ids: List[str],
    *,
    preloaded_entities: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    topic_by_id = {
        str(topic.get("topic_id")): topic
        for topic in (topics.get("active_topics", []) if isinstance(topics, dict) else [])
        if topic.get("topic_id")
    }

    entities_by_stage: Dict[str, Dict[str, Any]] = dict(preloaded_entities or {})
    missing_stages = [stage for stage in stage_ids if stage not in entities_by_stage]
    if missing_stages:
        loaded_entities = await asyncio.gather(
            *[_load_stage_entities_payload(project_id, stage) for stage in missing_stages],
            return_exceptions=True,
        )
        for idx, result in enumerate(loaded_entities):
            if isinstance(result, Exception):
                continue
            entities_by_stage[missing_stages[idx]] = (
                result if isinstance(result, dict) else {"items": [], "status": "pending"}
            )

    stages: Dict[str, str] = {}
    finalized_content: Dict[str, List[str]] = {}
    summaries: Dict[str, Any] = {}
    current_stage = stage_ids[0] if stage_ids else "goal"

    for stage in stage_ids:
        entity_payload = entities_by_stage.get(stage) or {"items": [], "status": "pending"}
        mapped_status = _normalize_stage_status(str(entity_payload.get("status") or ""))
        stages[stage] = mapped_status
        if mapped_status == "finalized":
            items = entity_payload.get("items")
            finalized_content[stage] = items if isinstance(items, list) else []
        topic = topic_by_id.get(stage, {})
        summary = topic.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries[stage] = summary.strip()

    for stage in stage_ids:
        if stages.get(stage) != "finalized":
            current_stage = stage
            break

    return {
        "current_stage": current_stage,
        "stages": stages,
        "finalized_content": finalized_content,
        "summaries": summaries,
    }


async def _load_stage_entities_payload(project_id: str, stage: str) -> Dict[str, Any]:
    if planner_cache_enabled():
        cached_stage = get_cached_stage_entities(project_id, stage)
        if isinstance(cached_stage, dict):
            return cached_stage
    payload = await planner_service.get_stage_entities(project_id, stage)
    if planner_cache_enabled() and isinstance(payload, dict):
        set_cached_stage_entities(project_id, stage, payload)
    return payload if isinstance(payload, dict) else {"items": [], "status": "pending"}


async def _compose_planner_bootstrap(
    project_id: str,
    *,
    stage_ids_override: Optional[List[str]] = None,
) -> Dict[str, Any]:
    charter_mode = None
    try:
        brain_collection = get_brain_collection(project_id)
        project_doc = await brain_collection.find_one({"entity_type": "project"})
        charter_mode = project_doc.get("charter_mode") if project_doc else None
    except Exception:
        charter_mode = None

    topics = await planner_service.get_project_topics(project_id, include_entity_details=False)
    stage_ids = stage_ids_override if stage_ids_override is not None else _stage_ids_from_topics(topics)

    entities_by_stage: Dict[str, Dict[str, Any]] = {}
    if stage_ids:
        loaded_entities = await asyncio.gather(
            *[_load_stage_entities_payload(project_id, stage) for stage in stage_ids],
            return_exceptions=True,
        )
        for idx, result in enumerate(loaded_entities):
            if isinstance(result, Exception):
                continue
            entities_by_stage[stage_ids[idx]] = result
    status_payload = await _compose_status_payload_from_entities(
        project_id,
        topics,
        stage_ids,
        preloaded_entities=entities_by_stage,
    )
    current_stage = str(status_payload.get("current_stage") or "").strip() or "goal"

    if planner_cache_enabled():
        set_cached_planner_status(project_id, status_payload)
        set_cached_current_stage(project_id, {"current_stage": current_stage})

    documents = await planner_service.get_uploaded_documents(project_id)
    return {
        "project_id": project_id,
        "partial": False,
        "charter_mode": charter_mode,
        "topics": topics,
        "status": status_payload,
        "current_stage": current_stage,
        "entities_by_stage": entities_by_stage,
        "documents": documents,
    }


async def _compose_planner_bootstrap_partial(project_id: str) -> Dict[str, Any]:
    """
    Best-effort partial bootstrap payload for immediate first paint.
    Must fail-open to avoid blocking async job enqueue contract.
    """
    charter_mode = None
    try:
        brain_collection = get_brain_collection(project_id)
        project_doc = await brain_collection.find_one({"entity_type": "project"})
        charter_mode = project_doc.get("charter_mode") if project_doc else None
    except Exception:
        charter_mode = None

    topics: Dict[str, Any] = {"active_topics": [], "available_optional_topics": [], "groups": []}
    stage_ids: List[str] = []
    try:
        topics = await planner_service.get_project_topics(project_id, include_entity_details=False)
        stage_ids = _stage_ids_from_topics(topics)
    except Exception:
        logger.exception("planner_bootstrap_partial_topics_failed project_id=%s", project_id)
        topics = {"active_topics": [], "available_optional_topics": [], "groups": []}
        stage_ids = []

    status_payload = await _compose_status_payload_from_entities(project_id, topics, stage_ids)
    current_stage = str(status_payload.get("current_stage") or "").strip() or (stage_ids[0] if stage_ids else "goal")
    if not status_payload.get("stages") and current_stage:
        status_payload = {
            "current_stage": current_stage,
            "stages": {current_stage: "pending"},
            "finalized_content": {},
            "summaries": {},
        }

    entities_by_stage: Dict[str, Dict[str, Any]] = {}
    if current_stage:
        try:
            entities_by_stage[current_stage] = await _load_stage_entities_payload(project_id, current_stage)
        except Exception:
            logger.exception(
                "planner_bootstrap_partial_entities_failed project_id=%s stage=%s",
                project_id,
                current_stage,
            )
            entities_by_stage[current_stage] = {"items": [], "status": "pending"}

    documents: List[Dict[str, Any]] = []
    try:
        documents = await planner_service.get_uploaded_documents(project_id)
    except Exception:
        logger.exception("planner_bootstrap_partial_documents_failed project_id=%s", project_id)
        documents = []

    return {
        "project_id": project_id,
        "partial": True,
        "charter_mode": charter_mode,
        "topics": topics,
        "status": status_payload,
        "current_stage": current_stage,
        "entities_by_stage": entities_by_stage,
        "documents": documents,
    }


def _set_planner_bootstrap_status(
    project_id: str,
    *,
    status_value: str,
    job_id: str = "",
    error: str = "",
) -> Dict[str, Any]:
    payload = {
        "status": str(status_value or "").strip().lower() or "idle",
        "job_id": str(job_id or "").strip() or None,
        "error": str(error or "").strip() or None,
        "updated_at": datetime.utcnow().isoformat(),
    }
    set_planner_bootstrap_job_status(project_id, payload)
    return payload


def _enqueue_planner_bootstrap_job(project_id: str) -> Dict[str, Any]:
    if not acquire_planner_bootstrap_job_lock(project_id):
        existing = get_planner_bootstrap_job_status(project_id) or {}
        return {"accepted": True, "reason": "already_processing", "status": existing}
    try:
        enqueue_result = dramatiq_enqueue(
            queue_name=QUEUE_CORTEX_RUNS,
            actor_name="process_planner_bootstrap_job",
            kwargs={"project_id": project_id},
        )
        if not bool(enqueue_result.get("accepted")):
            raise RuntimeError(str(enqueue_result.get("reason") or "dramatiq_enqueue_failed"))
        job_id = str(enqueue_result.get("job_id") or "")
        status_payload = _set_planner_bootstrap_status(
            project_id,
            status_value="queued",
            job_id=job_id,
        )
        return {
            "accepted": True,
            "reason": "queued",
            "job_id": job_id,
            "status": status_payload,
        }
    except Exception as exc:
        release_planner_bootstrap_job_lock(project_id)
        status_payload = _set_planner_bootstrap_status(
            project_id,
            status_value="failed",
            error=str(exc),
        )
        return {
            "accepted": False,
            "reason": "enqueue_failed",
            "error": str(exc),
            "status": status_payload,
        }


async def _process_planner_bootstrap_job_async(project_id: str) -> None:
    try:
        _set_planner_bootstrap_status(project_id, status_value="processing")
        full_payload = await _compose_planner_bootstrap(project_id)
        set_cached_planner_bootstrap_payload(project_id, full_payload)
        _set_planner_bootstrap_status(project_id, status_value="ready")
    except Exception as exc:
        _set_planner_bootstrap_status(project_id, status_value="failed", error=str(exc))
    finally:
        release_planner_bootstrap_job_lock(project_id)


def process_planner_bootstrap_job(project_id: str) -> None:
    asyncio.run(_process_planner_bootstrap_job_async(project_id=project_id))


@router.get("/{project_id}/status")
async def get_planner_status(project_id: str):
    """
    Get the current planning status for a project
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        if planner_cache_enabled():
            cached = get_cached_planner_status(project_id)
            if isinstance(cached, dict):
                return cached

        topics = await planner_service.get_project_topics(project_id, include_entity_details=False)
        stage_ids = _stage_ids_from_topics(topics)
        payload = await _compose_status_payload_from_entities(project_id, topics, stage_ids)
        if planner_cache_enabled() and isinstance(payload, dict):
            set_cached_planner_status(project_id, payload)
            current_stage = str(payload.get("current_stage") or "").strip()
            if current_stage:
                set_cached_current_stage(project_id, {"current_stage": current_stage})
        return payload
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting planner status: {str(e)}"
        )


@router.get("/{project_id}/current-stage")
async def get_current_stage(project_id: str):
    """
    Get the current stage for a project without loading full status.
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        if planner_cache_enabled():
            cached = get_cached_current_stage(project_id)
            if isinstance(cached, dict):
                return cached

        topics = await planner_service.get_project_topics(project_id, include_entity_details=False)
        stage_ids = _stage_ids_from_topics(topics)
        status_payload = await _compose_status_payload_from_entities(project_id, topics, stage_ids)
        payload = {"current_stage": str(status_payload.get("current_stage") or "goal").strip() or "goal"}
        if planner_cache_enabled() and isinstance(payload, dict):
            set_cached_current_stage(project_id, payload)
        return payload

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting current stage: {str(e)}"
        )


@router.get("/{project_id}/bootstrap")
async def get_planner_bootstrap(project_id: str, response: Response):
    """
    Get planner bootstrap payload for initial charter page load.

    This endpoint intentionally combines several planner reads to reduce
    frontend request fan-out and avoid stage-selection races.
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        cached_full = get_cached_planner_bootstrap_payload(project_id)
        if isinstance(cached_full, dict):
            return cached_full

        if not _planner_bootstrap_async_enabled():
            full_payload = await _compose_planner_bootstrap(project_id)
            set_cached_planner_bootstrap_payload(project_id, full_payload)
            return full_payload

        enqueue_result = _enqueue_planner_bootstrap_job(project_id)
        if not enqueue_result.get("accepted"):
            logger.warning(
                "planner_bootstrap_enqueue_failed_sync_fallback project_id=%s reason=%s error=%s",
                project_id,
                enqueue_result.get("reason"),
                enqueue_result.get("error"),
            )
            full_payload = await _compose_planner_bootstrap(project_id)
            set_cached_planner_bootstrap_payload(project_id, full_payload)
            return full_payload

        try:
            partial_payload = await _compose_planner_bootstrap_partial(project_id)
        except Exception:
            logger.exception("planner_bootstrap_partial_compose_failed project_id=%s", project_id)
            partial_payload = {
                "project_id": project_id,
                "partial": True,
                "charter_mode": None,
                "topics": {"active_topics": [], "available_optional_topics": [], "groups": []},
                "status": {"current_stage": "goal", "stages": {"goal": "pending"}, "finalized_content": {}, "summaries": {}},
                "current_stage": "goal",
                "entities_by_stage": {"goal": {"items": [], "status": "pending"}},
                "documents": [],
            }

        response.headers["Retry-After"] = "1"
        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "status": "processing",
            "job_id": enqueue_result.get("job_id"),
            "retry_after_ms": 1000,
            "bootstrap": partial_payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting planner bootstrap: {str(e)}"
        )


@router.get("/{project_id}/bootstrap-status")
async def get_planner_bootstrap_status(project_id: str, job_id: str = Query(default="")):
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        cached_payload = get_cached_planner_bootstrap_payload(project_id)
        has_data = isinstance(cached_payload, dict)
        status_payload = get_planner_bootstrap_job_status(project_id) or {}
        status_value = str(status_payload.get("status") or ("ready" if has_data else "idle")).strip().lower()
        current_job_id = str(status_payload.get("job_id") or "").strip()
        requested_job_id = str(job_id or "").strip()
        if requested_job_id and current_job_id and requested_job_id != current_job_id and status_value in {"queued", "processing"}:
            status_value = "processing"

        if status_value == "queued" and not has_data and not has_planner_bootstrap_job_lock(project_id):
            status_value = "failed"
            _set_planner_bootstrap_status(
                project_id,
                status_value="failed",
                error="stale_queued_without_lock",
            )

        retry_after_ms = 0
        if status_value in {"queued", "processing"}:
            retry_after_ms = 1000
        elif status_value == "failed":
            retry_after_ms = 5000

        return {
            "project_id": project_id,
            "status": status_value,
            "has_data": has_data,
            "job_id": current_job_id or None,
            "error": status_payload.get("error"),
            "updated_at": status_payload.get("updated_at"),
            "retry_after_ms": retry_after_ms,
            "bootstrap": cached_payload if status_value == "ready" and has_data else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting planner bootstrap status: {str(e)}"
        )


@router.get("/{project_id}/charter-mode")
async def get_charter_mode(project_id: str):
    """
    Get stored charter mode for a project.
    """
    try:
        brain_collection = get_brain_collection(project_id)
        project_doc = await brain_collection.find_one({"entity_type": "project"})
        return {"mode": project_doc.get("charter_mode") if project_doc else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting charter mode: {str(e)}"
        )


@router.post("/{project_id}/charter-mode")
async def set_charter_mode(project_id: str, request: CharterModeRequest):
    """
    Set stored charter mode for a project.
    """
    try:
        brain_collection = get_brain_collection(project_id)
        await brain_collection.update_one(
            {"entity_type": "project"},
            {"$set": {"entity_type": "project", "charter_mode": request.mode}},
            upsert=True
        )
        _invalidate_planner_read_caches(project_id)
        return {"mode": request.mode}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error setting charter mode: {str(e)}"
        )


@router.get("/{project_id}/entities/{stage}")
async def get_stage_entities(project_id: str, stage: str):
    """
    Get entity items and status for a stage from brain collection.
    """
    try:
        await validate_topic_stage(project_id, stage)
        if planner_cache_enabled():
            cached = get_cached_stage_entities(project_id, stage)
            if isinstance(cached, dict):
                return cached

        payload = await planner_service.get_stage_entities(project_id, stage)
        if planner_cache_enabled() and isinstance(payload, dict):
            set_cached_stage_entities(project_id, stage, payload)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting stage entities: {str(e)}"
        )


@router.get("/{project_id}/history/{stage}")
async def get_chat_history(project_id: str, stage: str):
    """
    Get chat history for a specific planning stage
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        messages = await planner_service.get_chat_history(project_id, stage)
        
        # Serialize timestamps
        serialized = []
        for msg in messages:
            serialized.append({
                "role": msg["role"],
                "content": msg["content"],
                "timestamp": msg.get("timestamp")
            })
        
        return {"messages": serialized, "stage": stage}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting chat history: {str(e)}"
        )


@router.get("/{project_id}/summary/{stage}")
async def get_discussion_summary(project_id: str, stage: str):
    """
    Get the summary of active discussion for a stage
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        summary = await planner_service.get_discussion_summary(project_id, stage)
        messages = await planner_service.get_chat_history(project_id, stage)
        
        return {
            "has_history": len(messages) > 0,
            "summary": summary,
            "message_count": len(messages)
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting summary: {str(e)}"
        )


@router.post("/{project_id}/chat")
async def chat_with_agent(project_id: str, message: ChatMessage):
    """
    Send a message to PM Agent and get a response
    """
    try:
        await validate_topic_stage(project_id, message.stage)

        response = await planner_service.chat(
            project_id=project_id,
            user_message=message.content,
            stage=message.stage
        )
        _invalidate_planner_read_caches(project_id, stage=message.stage)
        return ChatResponse(content=response, stage=message.stage)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error chatting with agent: {str(e)}"
        )


@router.post("/{project_id}/init/{stage}")
async def initialize_stage(project_id: str, stage: str):
    """
    Initialize a planning stage and get the initial PM Agent message
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        initial_message = await planner_service.get_initial_message(project_id, stage)
        
        if not initial_message:
            messages = await planner_service.get_chat_history(project_id, stage)
            return {
                "has_history": True,
                "message_count": len(messages)
            }
        
        return {
            "has_history": False,
            "initial_message": initial_message
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error initializing stage: {str(e)}"
        )


@router.post("/{project_id}/start-fresh/{stage}")
async def start_fresh(project_id: str, stage: str):
    """
    Archive current discussion and start fresh
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        result = await planner_service.start_fresh(project_id, stage)
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting fresh: {str(e)}"
        )


@router.post("/{project_id}/reactivate/{stage}")
async def reactivate_discussion(project_id: str, stage: str):
    """
    Reactivate a finalized discussion to continue editing
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        success = await planner_service.reactivate_discussion(project_id, stage)
        _invalidate_planner_read_caches(project_id, stage=stage)
        return {"success": success, "stage": stage}
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reactivating discussion: {str(e)}"
        )


@router.post("/{project_id}/finalize/{stage}")
async def finalize_stage(project_id: str, stage: str, request: FinalizeRequest):
    """
    Finalize a planning stage with the provided content
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        if not request.content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content cannot be empty"
            )
        
        result = await planner_service.finalize_stage(
            project_id=project_id,
            stage=stage,
            content=request.content,
            out_of_scope=request.out_of_scope
        )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error finalizing stage: {str(e)}"
        )


@router.post("/{project_id}/extract/{stage}")
async def extract_stage_content(project_id: str, stage: str):
    """
    Auto-extract finalized content from chat history using LLM
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        content = await planner_service.extract_stage_content(project_id, stage)
        
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not extract content. Please continue the discussion."
            )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return {"content": content, "stage": stage}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting content: {str(e)}"
        )


@router.post("/{project_id}/start-revision/{stage}")
async def start_revision(project_id: str, stage: str):
    """
    Start a fresh revision session for a finalized stage
    Returns initial message listing finalized points
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        result = await planner_service.start_revision_session(project_id, stage)
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting revision: {str(e)}"
        )


@router.post("/{project_id}/revision-chat")
async def revision_chat(project_id: str, message: ChatMessage):
    """
    Handle chat messages in revision mode
    Uses LLM to intelligently detect which point the user wants to revise
    """
    try:
        await validate_topic_stage(project_id, message.stage)

        # Get current revision to access finalized content
        revision = await planner_service.get_active_revision(project_id, message.stage)
        if not revision:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active revision session"
            )
        
        finalized_content = revision["revision_context"]["finalized_content"]
        current_point_index = revision["revision_context"].get("point_index")
        
        # Use LLM to detect which point the user is referring to
        # Only if they haven't already selected a point
        point_index = None
        
        if current_point_index is None:
            # Build numbered list of points
            points_list = "\n".join([f"{i+1}. {p}" for i, p in enumerate(finalized_content)])
            
            detection_prompt = f"""The user wants to revise one of these items:

{points_list}

User's message: "{message.content}"

Which item number (1-{len(finalized_content)}) is the user referring to?

Rules:
- If they mention a specific number (e.g., "2", "point 3"), return that number
- If they describe content (e.g., "the web app one"), match it to the item
- If they say "first", "second", etc., return that position
- If completely unclear, return "0"

Respond with ONLY the number (1-{len(finalized_content)} or 0), nothing else."""

            try:
                detected = await planner_service._call_llm(
                    messages=[
                        {"role": "system", "content": detection_prompt},
                        {"role": "user", "content": message.content}
                    ],
                    max_tokens=10,
                    temperature=0.1
                )
                
                if detected and detected.strip().isdigit():
                    detected_num = int(detected.strip())
                    if 1 <= detected_num <= len(finalized_content):
                        point_index = detected_num - 1  # Convert to 0-indexed
            except Exception:
                pass
        
        # Send message to revision chat
        response = await planner_service.revision_chat(
            project_id=project_id,
            stage=message.stage,
            user_message=message.content,
            point_index=point_index
        )
        
        # Get updated point index after chat
        revision = await planner_service.get_active_revision(project_id, message.stage)
        current_point_index = revision["revision_context"]["point_index"] if revision else None
        
        _invalidate_planner_read_caches(project_id, stage=message.stage)
        return {
            "content": response,
            "stage": message.stage,
            "point_index": current_point_index
        }
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error in revision chat: {str(e)}"
        )


@router.post("/{project_id}/extract-revised-point/{stage}")
async def extract_revised_point(project_id: str, stage: str):
    """
    Extract the revised point from the revision conversation
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        revised_point = await planner_service.extract_revised_point(project_id, stage)
        
        # Get the point index from revision context
        revision = await planner_service.get_active_revision(project_id, stage)
        point_index = revision["revision_context"]["point_index"] if revision else None
        
        _invalidate_planner_read_caches(project_id, stage=stage)
        return {
            "revised_point": revised_point,
            "point_index": point_index
        }
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting revised point: {str(e)}"
        )


@router.post("/{project_id}/update-point/{stage}")
async def update_point(project_id: str, stage: str, request: FinalizeRequest):
    """
    Update a specific point in finalized content
    Request body should have: {point_index: int, new_content: str}
    """
    from app.planner.schemas import UpdatePointRequest
    
    try:
        await validate_topic_stage(project_id, stage)
        
        # Get point_index from revision context
        revision = await planner_service.get_active_revision(project_id, stage)
        if not revision:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active revision session"
            )
        
        point_index = revision["revision_context"]["point_index"]
        if point_index is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No point selected for revision"
            )
        
        # Expect single item in content array (the revised point)
        if not request.content or len(request.content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No revised content provided"
            )
        
        new_content = request.content[0]
        
        result = await planner_service.update_finalized_point(
            project_id=project_id,
            stage=stage,
            point_index=point_index,
            new_content=new_content
        )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating point: {str(e)}"
        )


@router.post("/{project_id}/add-point/{stage}")
async def add_point(project_id: str, stage: str, request: dict):
    """
    Add a new point to finalized content
    """
    try:
        await validate_topic_stage(project_id, stage)
        
        new_point = request.get("new_point", "").strip()
        if not new_point:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No point text provided"
            )
        
        # Add the point to finalized content
        result = await planner_service.add_point_to_finalized(
            project_id=project_id,
            stage=stage,
            new_point=new_point
        )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding point: {str(e)}"
        )


@router.post("/{project_id}/draft/{stage}")
async def update_draft(project_id: str, stage: str, request: DraftUpdateRequest):
    """
    Overwrite draft content for a stage.
    """
    try:
        await validate_topic_stage(project_id, stage)
        await planner_service.save_entity_draft(
            project_id=project_id,
            entity_type=stage,
            items=request.items or [],
            out_of_scope=request.out_of_scope or []
        )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return {"status": "draft"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating draft: {str(e)}"
        )


@router.delete("/{project_id}/delete-point/{stage}/{point_index}")
async def delete_point(project_id: str, stage: str, point_index: int):
    """
    Delete a point from finalized content by index
    """
    try:
        await validate_topic_stage(project_id, stage)

        result = await planner_service.delete_point_from_finalized(
            project_id=project_id,
            stage=stage,
            point_index=point_index
        )
        _invalidate_planner_read_caches(project_id, stage=stage)
        return result

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting point: {str(e)}"
        )


@router.post("/{project_id}/generate-tasks")
async def generate_tasks_for_feature(project_id: str, request: dict):
    """
    Auto-generate tasks for a feature using AI
    """
    try:
        feature = request.get("feature")
        stage = request.get("stage", "features_tasks")

        if not feature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feature name is required"
            )

        tasks = await planner_service.generate_tasks_for_feature(
            project_id=project_id,
            feature=feature,
            stage=stage
        )

        return {"tasks": tasks}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating tasks: {str(e)}"
        )


@router.post("/{project_id}/task-brainstorm")
async def task_brainstorm(project_id: str, request: dict):
    """
    Chat endpoint for brainstorming tasks for a feature
    """
    try:
        feature = request.get("feature")
        message = request.get("message")
        existing_tasks = request.get("existing_tasks", [])

        if not feature or not message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feature and message are required"
            )

        response = await planner_service.task_brainstorm_chat(
            project_id=project_id,
            feature=feature,
            message=message,
            existing_tasks=existing_tasks
        )

        return {"content": response}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error in task brainstorm: {str(e)}"
        )


@router.post("/{project_id}/extract-tasks")
async def extract_tasks(project_id: str, request: dict):
    """
    Extract final task list from brainstorm conversation
    """
    try:
        feature = request.get("feature")
        messages = request.get("messages", [])

        if not feature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feature name is required"
            )

        tasks = await planner_service.extract_tasks_from_chat(
            project_id=project_id,
            feature=feature,
            messages=messages
        )

        return {"tasks": tasks}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting tasks: {str(e)}"
        )


@router.post("/{project_id}/upload-documents")
async def upload_documents(
    project_id: str,
    files: List[UploadFile] = File(...)
):
    """
    Upload project documents (Word, PDF, MD, TXT) for extraction
    """
    try:
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided"
            )

        uploaded_docs = await planner_service.upload_documents(
            project_id=project_id,
            files=files
        )

        _invalidate_planner_read_caches(project_id, invalidate_all_entities=True)
        return {
            "success": True,
            "documents": uploaded_docs,
            "count": len(uploaded_docs)
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading documents: {str(e)}"
        )


@router.get("/{project_id}/documents")
async def get_uploaded_documents(project_id: str):
    """
    Get list of uploaded documents for a project
    """
    try:
        documents = await planner_service.get_uploaded_documents(project_id)

        return {
            "documents": documents,
            "count": len(documents)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting documents: {str(e)}"
        )


@router.post("/{project_id}/extract-from-documents")
async def extract_from_documents(project_id: str, request: DocumentExtractionRequest):
    """
    Extract project charter content from uploaded documents
    """
    try:
        if not request.file_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No file IDs provided"
            )

        orchestrator = CharterOrchestrator(planner_service)
        extraction_result = await orchestrator.process_document_extraction(
            project_id=project_id,
            file_ids=request.file_ids
        )
        _invalidate_planner_read_caches(project_id, invalidate_all_entities=True)

        return {
            "success": True,
            **extraction_result
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting from documents: {str(e)}"
        )


@router.delete("/{project_id}/documents/{file_id}")
async def delete_document(project_id: str, file_id: str):
    """
    Delete an uploaded document
    """
    try:
        success = await planner_service.delete_document(project_id, file_id)
        _invalidate_planner_read_caches(project_id, invalidate_all_entities=True)
        return {"success": success}

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting document: {str(e)}"
        )


# =============================================================================
# RESET CHARTER (START FRESH)
# =============================================================================

@router.delete("/{project_id}/reset-charter")
async def reset_charter(project_id: str):
    """
    Reset entire charter - clears all discussions, entities, and extracted content.
    Returns user to welcome page (mode selection).
    """
    try:
        result = await planner_service.reset_charter(project_id)
        _invalidate_planner_read_caches(project_id, invalidate_all_entities=True)
        return {
            "success": True,
            "message": "Charter reset successfully",
            "redirect": "welcome"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting charter: {str(e)}"
        )


# =============================================================================
# TOPIC MANAGEMENT ENDPOINTS
# =============================================================================

@router.get("/topics/registry")
async def get_topic_registry():
    """
    Get the full topic registry with all groups, topics, and project type mappings.
    """
    from app.planner.topic_registry import get_full_registry
    
    try:
        return get_full_registry()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting topic registry: {str(e)}"
        )


@router.get("/{project_id}/topics")
async def get_project_topics(project_id: str):
    """
    Get all topics for a project based on its type.
    Returns active topics with status and available optional topics.
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        return await planner_service.get_project_topics(project_id)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting project topics: {str(e)}"
        )


@router.post("/{project_id}/topics/{topic_id}/activate")
async def activate_optional_topic(project_id: str, topic_id: str):
    """
    Activate an optional topic for a project.
    Creates a topic document ready for brainstorm/extraction.
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        result = await planner_service.activate_optional_topic(project_id, topic_id)
        _invalidate_planner_read_caches(project_id, invalidate_all_entities=True)
        return result
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error activating topic: {str(e)}"
        )


@router.get("/{project_id}/topics/{topic_id}/dependencies")
async def get_topic_dependencies(project_id: str, topic_id: str):
    """
    Get dependency information for a topic.
    Returns which dependencies are completed/pending for banner display.
    """
    try:
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        return await planner_service.get_topic_dependency_info(project_id, topic_id)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting topic dependencies: {str(e)}"
        )
