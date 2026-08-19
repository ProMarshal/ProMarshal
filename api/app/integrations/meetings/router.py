"""Meeting integration routes — audio capture, transcription, ingest."""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_by_custom_id
from app.core.database import get_brain_collection, get_database
from app.core.config import settings
from app.core.security import decode_access_token
from app.integrations.meetings.deepgram import transcribe_audio_chunk
from app.integrations.meetings.extractor import extract_discussed_points
from app.integrations.meetings.speaker import (
    assign_speakers,
    build_transcript_lines,
    format_transcript,
)
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/meetings", tags=["meetings"])

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


# ── Request / Response models ─────────────────────────────────────────────────

class SpeakerEvent(BaseModel):
    name: str
    start: float
    end: float


class TranscribeChunkResponse(BaseModel):
    text: str
    words: List[Dict[str, Any]]


class IngestRequest(BaseModel):
    project_id: str
    session_id: str
    transcript: str
    transcript_lines: List[Dict[str, Any]]
    speaker_timeline: List[SpeakerEvent]
    meeting_url: Optional[str] = None
    started_at: Optional[str] = None


class SessionTokenRequest(BaseModel):
    project_id: str


class SessionTokenResponse(BaseModel):
    session_id: str
    project_id: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/session-token", response_model=SessionTokenResponse)
async def get_meeting_session_token(
    body: SessionTokenRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("user_id") or "").strip()
    logger.info("[meetings] session-token called | user=%s project=%s", user_id, body.project_id)

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    db = get_database()
    await assert_project_access_by_custom_id(body.project_id, user_id, db)

    session_id = str(uuid.uuid4())
    logger.info("[meetings] Session token issued | session=%s", session_id)
    return {"session_id": session_id, "project_id": body.project_id}


@router.websocket("/stream")
async def stream_meeting_audio(
    websocket: WebSocket,
    token: str = Query(...),
):
    """
    WebSocket endpoint for real-time audio streaming to Deepgram.

    Client connects with ?token=JWT, streams raw audio bytes, receives
    JSON messages: {"type": "transcript", "text": "...", "is_final": true/false}
    """
    # Authenticate before accepting
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub") or payload.get("user_id") or ""
        if not user_id:
            raise ValueError("No user_id in token")
    except Exception as e:
        logger.warning("[meetings/stream] Auth failed: %s", e)
        await websocket.close(code=4001)
        return

    await websocket.accept()
    logger.info("[meetings/stream] WebSocket accepted | user=%s", user_id)

    api_key = str(settings.deepgram_api_key or "").strip()
    if not api_key:
        logger.error("[meetings/stream] DEEPGRAM_API_KEY not configured")
        await websocket.send_json({"type": "error", "message": "Transcription not configured"})
        await websocket.close(code=1011)
        return

    params = {
        "model": "nova-2",
        "punctuate": "true",
        "language": "en",
        "words": "true",
        "interim_results": "true",
        "endpointing": "300",
        "encoding": "linear16",
        "sample_rate": "16000",
        "channels": "1",
    }
    dg_url = DEEPGRAM_WS_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                dg_url,
                headers={"Authorization": f"Token {api_key}"},
            ) as dg_ws:
                logger.info("[meetings/stream] Connected to Deepgram")

                async def forward_audio():
                    """Receive audio bytes from plugin, forward to Deepgram."""
                    bytes_received = 0
                    chunk_count = 0
                    try:
                        while True:
                            data = await websocket.receive_bytes()
                            chunk_count += 1
                            bytes_received += len(data)
                            if chunk_count <= 3 or chunk_count % 50 == 0:
                                logger.info("[meetings/stream] Audio chunk #%d | size=%d bytes | total=%d bytes", chunk_count, len(data), bytes_received)
                            if dg_ws.closed:
                                logger.warning("[meetings/stream] Deepgram WS already closed — cannot forward audio")
                                break
                            await dg_ws.send_bytes(data)
                    except WebSocketDisconnect:
                        logger.info("[meetings/stream] Plugin disconnected | total chunks=%d bytes=%d", chunk_count, bytes_received)
                    except Exception as e:
                        logger.error("[meetings/stream] forward_audio error: %s", e)
                    finally:
                        if not dg_ws.closed:
                            await dg_ws.send_str(json.dumps({"type": "CloseStream"}))

                async def forward_transcripts():
                    """Receive transcripts from Deepgram, forward to plugin."""
                    msg_count = 0
                    try:
                        async for msg in dg_ws:
                            msg_count += 1
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                msg_type = data.get("type")
                                logger.info("[meetings/stream] Deepgram msg #%d | type=%s", msg_count, msg_type)
                                if msg_type == "Results":
                                    alts = data.get("channel", {}).get("alternatives", [])
                                    if alts:
                                        text = alts[0].get("transcript", "").strip()
                                        is_final = data.get("is_final", False)
                                        logger.info("[meetings/stream] Result | final=%s | text=%r", is_final, text[:80])
                                        if text:
                                            try:
                                                await websocket.send_json({
                                                    "type": "transcript",
                                                    "text": text,
                                                    "is_final": is_final,
                                                })
                                            except Exception:
                                                # Plugin WebSocket already closed — stop forwarding
                                                return
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.info("[meetings/stream] Deepgram WS closed/error | msg_count=%d", msg_count)
                                break
                    except Exception as e:
                        logger.error("[meetings/stream] forward_transcripts error: %s", e)

                await asyncio.gather(forward_audio(), forward_transcripts())

    except Exception as e:
        logger.error("[meetings/stream] WebSocket session error: %s", e)
    finally:
        logger.info("[meetings/stream] Session ended | user=%s", user_id)


@router.post("/transcribe-chunk", response_model=TranscribeChunkResponse)
async def transcribe_chunk(
    audio: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("user_id") or "").strip()
    logger.info("[meetings] transcribe-chunk called | user=%s content_type=%s", user_id, audio.content_type)

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    audio_bytes = await audio.read()
    logger.info("[meetings] Audio chunk size: %d bytes", len(audio_bytes))

    if not audio_bytes:
        logger.warning("[meetings] Empty audio chunk received")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio chunk")

    content_type = audio.content_type or "audio/webm"

    try:
        result = await transcribe_audio_chunk(audio_bytes, content_type)
        logger.info("[meetings] Deepgram result | text=%r words=%d", result.get("text", "")[:80], len(result.get("words", [])))
    except ValueError as e:
        logger.error("[meetings] Deepgram config error: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logger.error("[meetings] Deepgram call failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Transcription failed: {str(e)}",
        )

    return result


def _fmt_elapsed(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


@router.post("/ingest")
async def ingest_meeting_transcript(
    body: IngestRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("user_id") or "").strip()
    logger.info(
        "[meetings] ingest called | user=%s project=%s session=%s transcript_len=%d speakers=%d",
        user_id, body.project_id, body.session_id,
        len(body.transcript), len(body.speaker_timeline),
    )

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    if not body.transcript.strip():
        logger.info("[meetings] Empty transcript — skipping ingest")
        return {"ok": True, "message": "Empty transcript — nothing to store"}

    db = get_database()
    await assert_project_access_by_custom_id(body.project_id, user_id, db)

    try:
        brain = get_brain_collection(body.project_id)
        now = datetime.now(timezone.utc)

        speaker_timeline_dicts = [e.model_dump() for e in body.speaker_timeline]

        # Extract structured "discussed points" via LLM. Returns [] on failure
        # so a failed extraction never blocks the transcript from being stored.
        discussed_points = await extract_discussed_points(
            body.transcript, speaker_timeline_dicts
        )

        doc = {
            "project_id": body.project_id,
            "entity_type": "meeting_transcript",
            "session_id": body.session_id,
            "transcript": body.transcript,
            "transcript_lines": body.transcript_lines,
            "speaker_timeline": speaker_timeline_dicts,
            "discussed_points": discussed_points,
            "meeting_url": body.meeting_url,
            "started_at": body.started_at,
            "captured_by": user_id,
            "synced_at": now,
        }

        # Upsert on (entity_type, session_id) — idempotent against client retries.
        # created_at is only set on first insert, never overwritten on retry.
        result = await brain.update_one(
            {"entity_type": "meeting_transcript", "session_id": body.session_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        doc_id = str(result.upserted_id) if result.upserted_id else body.session_id
        logger.info(
            "[meetings] Transcript stored | id=%s upserted=%s discussed_points=%d",
            doc_id, result.upserted_id is not None, len(discussed_points),
        )

        return {"ok": True, "id": doc_id, "discussed_points": len(discussed_points)}

    except Exception as e:
        logger.error("[meetings] Failed to store transcript: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store transcript: {str(e)}",
        )


@router.get("/context")
async def get_meeting_context(
    project_id: str,
    query: str,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user.get("user_id") or "").strip()
    logger.info("[meetings] context called | user=%s project=%s query=%r", user_id, project_id, query[:50])

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return {"context": [], "query": query, "project_id": project_id}


# ── Meeting Insights endpoints (list / detail / transcript download) ──────────

def _summarize_attendees(speaker_timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate speaking time per speaker, sorted by most-spoken first."""
    totals: Dict[str, float] = {}
    for ev in speaker_timeline or []:
        name = str(ev.get("name") or "").strip() or "Unknown"
        start = float(ev.get("start") or 0)
        end = float(ev.get("end") or 0)
        if end > start:
            totals[name] = totals.get(name, 0.0) + (end - start)
    return [
        {"name": name, "speaking_seconds": round(secs, 1)}
        for name, secs in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


def _duration_seconds(speaker_timeline: List[Dict[str, Any]]) -> float:
    if not speaker_timeline:
        return 0.0
    return float(max((ev.get("end") or 0) for ev in speaker_timeline))


def _derive_title(points: List[Dict[str, Any]], started_at: Optional[str]) -> str:
    """Derive a short title from extracted points or fall back to date-based label."""
    for p in points or []:
        text = str(p.get("text") or "").strip()
        if text:
            # First substantive point's text, trimmed
            return text[:60] + ("…" if len(text) > 60 else "")
    if started_at:
        return f"Meeting on {started_at[:10]}"
    return "Untitled meeting"


@router.get("/list")
async def list_meetings(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List meetings for a project, newest first. Returns lightweight summaries."""
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    db = get_database()
    await assert_project_access_by_custom_id(project_id, user_id, db)

    brain = get_brain_collection(project_id)
    cursor = brain.find(
        {"entity_type": "meeting_transcript"},
        projection={
            "session_id": 1,
            "started_at": 1,
            "speaker_timeline": 1,
            "discussed_points": 1,
        },
    ).sort("started_at", -1).limit(100)

    meetings: List[Dict[str, Any]] = []
    async for doc in cursor:
        speaker_timeline = doc.get("speaker_timeline") or []
        points = doc.get("discussed_points") or []
        meetings.append({
            "session_id": doc.get("session_id"),
            "started_at": doc.get("started_at"),
            "duration_seconds": _duration_seconds(speaker_timeline),
            "attendees": _summarize_attendees(speaker_timeline),
            "title": _derive_title(points, doc.get("started_at")),
            "has_insights": bool(points),
        })

    return {"meetings": meetings}


@router.get("/detail/{session_id}")
async def get_meeting(
    session_id: str,
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Full meeting record (metadata + attendees + discussed_points).

    Excludes the raw transcript and transcript_lines to keep the payload small —
    use the transcript.md endpoint to download the raw text.
    """
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    db = get_database()
    await assert_project_access_by_custom_id(project_id, user_id, db)

    brain = get_brain_collection(project_id)
    doc = await brain.find_one(
        {"entity_type": "meeting_transcript", "session_id": session_id},
        projection={
            "session_id": 1,
            "started_at": 1,
            "meeting_url": 1,
            "speaker_timeline": 1,
            "discussed_points": 1,
        },
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    speaker_timeline = doc.get("speaker_timeline") or []
    points = doc.get("discussed_points") or []
    return {
        "session_id": doc.get("session_id"),
        "started_at": doc.get("started_at"),
        "meeting_url": doc.get("meeting_url"),
        "duration_seconds": _duration_seconds(speaker_timeline),
        "attendees": _summarize_attendees(speaker_timeline),
        "discussed_points": points,
        "title": _derive_title(points, doc.get("started_at")),
    }


@router.get("/transcript/{session_id}.md")
async def download_meeting_transcript(
    session_id: str,
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Download the meeting transcript as a markdown file."""
    user_id = str(current_user.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    db = get_database()
    await assert_project_access_by_custom_id(project_id, user_id, db)

    brain = get_brain_collection(project_id)
    doc = await brain.find_one({"entity_type": "meeting_transcript", "session_id": session_id})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    started_at = doc.get("started_at") or ""
    date_str = started_at[:10] if started_at else datetime.now(timezone.utc).date().isoformat()
    transcript_lines = doc.get("transcript_lines") or []
    raw_transcript = (doc.get("transcript") or "").strip()
    speaker_timeline = doc.get("speaker_timeline") or []
    attendees = _summarize_attendees(speaker_timeline)
    duration = _duration_seconds(speaker_timeline)

    lines: List[str] = []
    lines.append(f"# Meeting Transcript")
    lines.append("")
    lines.append(f"**Date:** {date_str}")
    if duration:
        m, s = divmod(int(duration), 60)
        lines.append(f"**Duration:** {m} min {s} sec")
    if attendees:
        names = ", ".join(a["name"] for a in attendees)
        lines.append(f"**Attendees:** {names}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if transcript_lines:
        for entry in transcript_lines:
            if not isinstance(entry, dict):
                continue
            speaker = str(entry.get("speaker") or "Unknown")
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            start = float(entry.get("start") or 0)
            ts = _fmt_elapsed(start)
            lines.append(f"**[{ts}] {speaker}:** {text}")
            lines.append("")
    elif raw_transcript:
        lines.append(raw_transcript)

    body = "\n".join(lines)
    filename = f"Meeting-{date_str}-{session_id[:8]}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
