"""RQ job entrypoints for Action Items."""

from __future__ import annotations

from typing import Any, Dict

from app.action_items.extraction import (
    process_action_item_extraction_job,
    process_action_item_extraction_job_async,
)


def _is_non_retryable_failure(result: Dict[str, Any]) -> bool:
    return bool((result or {}).get("retryable") is False)


def run_action_item_extraction(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Worker-safe job entrypoint for action-item extraction payloads."""
    result = process_action_item_extraction_job(payload)
    if not bool((result or {}).get("ok", False)):
        if _is_non_retryable_failure(result):
            return result
        raise RuntimeError(str((result or {}).get("error") or "action_item_extraction_failed"))
    return result


async def run_action_item_extraction_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Async worker-safe job entrypoint for action-item extraction payloads."""
    result = await process_action_item_extraction_job_async(payload)
    if not bool((result or {}).get("ok", False)):
        if _is_non_retryable_failure(result):
            return result
        raise RuntimeError(str((result or {}).get("error") or "action_item_extraction_failed"))
    return result
