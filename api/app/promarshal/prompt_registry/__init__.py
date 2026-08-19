"""Global prompt registry for ProMarshal user-facing behavior."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings


logger = logging.getLogger(__name__)
PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
_WARNED_MISSING: set[str] = set()


def _load_text(*, relative_path: str, fallback: str = "") -> str:
    path = PROMPT_ROOT / relative_path
    warn_key = str(path)
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
        if warn_key not in _WARNED_MISSING:
            logger.warning("ProMarshal prompt file is empty, using fallback: %s", path)
            _WARNED_MISSING.add(warn_key)
    except FileNotFoundError:
        if warn_key not in _WARNED_MISSING:
            logger.warning("ProMarshal prompt file not found, using fallback: %s", path)
            _WARNED_MISSING.add(warn_key)
    except Exception as exc:
        if warn_key not in _WARNED_MISSING:
            logger.warning("ProMarshal prompt load failed (%s), using fallback: %s", str(exc), path)
            _WARNED_MISSING.add(warn_key)
    return str(fallback or "").strip()


def prompt_registry_enabled() -> bool:
    return bool(getattr(settings, "promarshal_prompt_registry_enabled", False))


def load_core_identity(*, fallback: str = "") -> str:
    return _load_text(relative_path="core_identity.md", fallback=fallback)


def load_core_response_rules(*, fallback: str = "") -> str:
    return _load_text(relative_path="core_response_rules.md", fallback=fallback)


def load_core_scope_policy(*, fallback: str = "") -> str:
    return _load_text(relative_path="core_scope_policy.md", fallback=fallback)


def load_overlay(capability: str, *, fallback: str = "") -> str:
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return str(fallback or "").strip()
    return _load_text(relative_path=f"overlays/{normalized}.md", fallback=fallback)


def _load_section_overlay(
    *,
    capability: str,
    section: str,
    fallback: str = "",
) -> str:
    normalized_capability = str(capability or "").strip().lower()
    normalized_section = str(section or "").strip().lower()
    if not normalized_capability or not normalized_section:
        return str(fallback or "").strip()
    relative_path = f"overlays/{normalized_capability}.{normalized_section}.md"
    path = PROMPT_ROOT / relative_path
    if not path.exists():
        return str(fallback or "").strip()
    return _load_text(relative_path=relative_path, fallback=fallback)


def compose_identity(
    *,
    capability: str,
    legacy_identity: str = "",
    legacy_overlay: str = "",
) -> str:
    """
    Compose identity text from global core + capability overlay.

    Falls back to legacy strings when the registry is disabled or files are missing.
    """
    if not prompt_registry_enabled():
        pieces = [str(legacy_identity or "").strip(), str(legacy_overlay or "").strip()]
        return "\n\n".join([item for item in pieces if item])

    core = load_core_identity(fallback=legacy_identity)
    overlay = _load_section_overlay(
        capability=capability,
        section="identity",
        fallback=load_overlay(capability, fallback=legacy_overlay),
    )
    return "\n\n".join([item for item in [core, overlay] if str(item or "").strip()])


def compose_response_rules(*, capability: str, legacy_rules: str = "") -> str:
    if not prompt_registry_enabled():
        return str(legacy_rules or "").strip()

    core = load_core_response_rules(fallback=legacy_rules)
    overlay = _load_section_overlay(capability=capability, section="response_rules", fallback="")
    if not overlay:
        return core
    return "\n\n".join([core, overlay])


def load_capability_scope_policy(capability: str, *, fallback: str = "") -> str:
    """Load a capability-specific scope policy as a full replacement (not appended to core).

    Use this when a feature (e.g. Cadence) has its own scope rules that should
    completely replace the generic core_scope_policy.md rather than extend it.
    """
    return _load_section_overlay(capability=capability, section="scope_policy", fallback=fallback)


def compose_scope_policy(*, legacy_policy: str = "", capability: Optional[str] = None) -> str:
    if not prompt_registry_enabled():
        return str(legacy_policy or "").strip()

    core = load_core_scope_policy(fallback=legacy_policy)
    if not capability:
        return core
    overlay = _load_section_overlay(
        capability=str(capability),
        section="scope_policy",
        fallback="",
    )
    if not overlay:
        return core
    return "\n\n".join([core, overlay])
