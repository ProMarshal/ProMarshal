"""Provider-overlay policy registry for prompt composition."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List


logger = logging.getLogger(__name__)
OVERLAY_ROOT = Path(__file__).resolve().parent / "provider_overlays"
_WARNED_KEYS: set[str] = set()

DEFAULT_PROVIDER_OVERLAYS: Dict[str, str] = {
    "jira": (
        "Provider overlay: Jira\n"
        "- Route task operations to Jira tools only when Jira is the active/materialized provider.\n"
        "- Keep write targets explicit (task id, status, assignee, comment, or description payload).\n"
        "- Treat Jira as source of truth for write confirmation."
    ),
    "linear": (
        "Provider overlay: Linear\n"
        "- Route task operations to Linear tools only when Linear is the active/materialized provider.\n"
        "- Keep mutation targets explicit and avoid inferred cross-provider writes."
    ),
}


def _normalize_provider(value: str) -> str:
    return str(value or "").strip().lower()


def _load_overlay_file(*, provider: str) -> str:
    normalized = _normalize_provider(provider)
    if not normalized:
        return ""
    path = OVERLAY_ROOT / f"{normalized}.md"
    warned_key = str(path)
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
        if warned_key not in _WARNED_KEYS:
            logger.warning("Provider overlay file is empty, using fallback: %s", path)
            _WARNED_KEYS.add(warned_key)
    except FileNotFoundError:
        return ""
    except Exception as exc:
        if warned_key not in _WARNED_KEYS:
            logger.warning("Provider overlay load failed (%s), using fallback: %s", str(exc), path)
            _WARNED_KEYS.add(warned_key)
    return ""


def load_provider_overlay(provider: str) -> str:
    """Load provider overlay text from file or fallback registry."""
    normalized = _normalize_provider(provider)
    if not normalized:
        return ""
    file_text = _load_overlay_file(provider=normalized)
    if file_text:
        return file_text
    return str(DEFAULT_PROVIDER_OVERLAYS.get(normalized, "")).strip()


def compose_provider_overlays(*, providers: Iterable[str]) -> List[str]:
    """Return ordered provider overlays for current materialized providers."""
    ordered: List[str] = []
    seen: set[str] = set()
    for item in providers or []:
        normalized = _normalize_provider(str(item or ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        overlay = load_provider_overlay(normalized)
        if overlay:
            ordered.append(overlay)
    return ordered

