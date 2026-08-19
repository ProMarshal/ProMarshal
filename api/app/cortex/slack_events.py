"""Slack event contracts and handoff helper for Cortex ingress."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.cortex.ingress import (
    SlackIngressClassification,
    classify_slack_event_for_cortex,
    normalize_slack_event_for_cortex,
)
from app.cortex.orchestrator import CortexOrchestrator
from app.cortex.queue import enqueue_cortex_run


@dataclass(frozen=True)
class SlackEventEnvelope:
    """Normalized Slack event envelope for Cortex routing."""

    team_id: str
    channel_id: str
    user_id: str
    text: str
    event_ts: str
    thread_ts: Optional[str] = None
    raw_event: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CortexSlackHandoffResult:
    """Result of attempting Cortex handoff from Slack integrations entrypoint."""

    accepted: bool
    response_text: Optional[str] = None
    reason: str = ""
    used_tool_call: bool = False
    tool_calls_count: int = 0


def _delivery_succeeded(metadata: Optional[Dict[str, Any]]) -> bool:
    payload = metadata or {}
    if bool(payload.get("delivered", False)):
        return True
    delivery = payload.get("delivery")
    if isinstance(delivery, dict) and bool(delivery.get("ok", False)):
        return True
    return False


async def handoff_slack_event_to_cortex(
    event: SlackEventEnvelope,
    project_id: str,
    identity_context: Optional[Dict[str, Any]] = None,
) -> CortexSlackHandoffResult:
    """
    Handoff Slack event from legacy integrations route into Cortex ingress path.

    Returns non-accepted result when Cortex runtime is not ready so callers can
    safely fall back to legacy behavior.
    """
    classification = classify_slack_event_for_cortex(event)
    if not classification.accepted:
        return CortexSlackHandoffResult(
            accepted=False,
            reason=classification.reason or "ingress_not_accepted",
        )

    ingress = await normalize_slack_event_for_cortex(
        event=event,
        project_id=project_id,
        classification=classification,
        metadata_updates=identity_context or {},
    )

    enqueue_result = enqueue_cortex_run(
        ingress=asdict(ingress),
        raw_event=event.raw_event or {},
    )
    if enqueue_result.queued:
        return CortexSlackHandoffResult(
            accepted=True,
            reason=f"cortex_queued:{enqueue_result.job_id}",
        )

    try:
        result = await CortexOrchestrator().handle_turn(
            {"ingress": asdict(ingress), "raw_event": event.raw_event or {}}
        )
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        executor = metadata.get("executor") if isinstance(metadata.get("executor"), dict) else {}
        tool_calls_count = int(executor.get("tool_calls_count") or 0)
        used_tool_call = tool_calls_count > 0
        delivery_ok = _delivery_succeeded(result.metadata)
        if not result.ok:
            if delivery_ok:
                return CortexSlackHandoffResult(
                    accepted=True,
                    response_text=None,
                    reason=result.error or "cortex_orchestrator_completed_with_error",
                    used_tool_call=used_tool_call,
                    tool_calls_count=tool_calls_count,
                )
            if str(result.response_text or "").strip():
                return CortexSlackHandoffResult(
                    accepted=True,
                    response_text=result.response_text,
                    reason=result.error or "cortex_orchestrator_completed_with_error",
                    used_tool_call=used_tool_call,
                    tool_calls_count=tool_calls_count,
                )
            return CortexSlackHandoffResult(
                accepted=False,
                reason=result.error or enqueue_result.reason or "cortex_orchestrator_failed",
                used_tool_call=used_tool_call,
                tool_calls_count=tool_calls_count,
            )
        return CortexSlackHandoffResult(
            accepted=True,
            response_text=None if delivery_ok else result.response_text,
            reason="cortex_orchestrator_completed",
            used_tool_call=used_tool_call,
            tool_calls_count=tool_calls_count,
        )
    except Exception as exc:
        print(f"Cortex handoff exception: {exc}")
        return CortexSlackHandoffResult(
            accepted=False,
            reason="cortex_orchestrator_exception",
        )


def classify_event_for_handoff(event: SlackEventEnvelope) -> SlackIngressClassification:
    """Expose Stage 1-2 classification for router-level decisions (ack/fallback)."""
    return classify_slack_event_for_cortex(event)
