"""LiteLLM runtime config helpers for Cortex executor wiring."""

from dataclasses import dataclass
import asyncio
import os
from typing import Optional

from app.core.config import settings


@dataclass(frozen=True)
class LiteLLMRuntimeConfig:
    """Runtime routing options for Cortex provider calls."""

    primary_provider: str
    primary_model: str
    fallback_provider: str
    fallback_model: str
    retries: int = 3
    max_tokens: int = 2048
    temperature: float = 0.3
    timeout_seconds: int = 60
    max_turns: int = 10


def build_litellm_runtime_config() -> LiteLLMRuntimeConfig:
    """Build typed runtime config from canonical Cortex settings."""
    return LiteLLMRuntimeConfig(
        primary_provider=(settings.cortex_llm_primary_provider or "openai").strip().lower(),
        primary_model=(settings.cortex_llm_primary_model or "gpt-4o-mini").strip(),
        fallback_provider=(settings.cortex_llm_fallback_provider or "openai").strip().lower(),
        fallback_model=(settings.cortex_llm_fallback_model or "gpt-4o-mini").strip(),
        retries=max(0, int(settings.cortex_llm_num_retries)),
        max_tokens=max(1, int(settings.cortex_llm_max_tokens)),
        temperature=float(settings.cortex_llm_temperature),
        timeout_seconds=max(1, int(settings.cortex_run_timeout)),
        max_turns=max(1, int(settings.cortex_max_tool_iterations)),
    )


def to_litellm_model(provider: str, model: str) -> str:
    """Return provider-qualified model id accepted by LiteLLM."""
    clean_model = (model or "").strip()
    if not clean_model:
        return ""
    if "/" in clean_model:
        return clean_model
    clean_provider = (provider or "").strip().lower() or "openai"
    return f"{clean_provider}/{clean_model}"


def seed_litellm_api_keys(*, provider: str, fallback_provider: Optional[str] = None) -> None:
    """
    Seed LiteLLM expected API key env vars from app settings when available.

    This keeps runtime setup local to Cortex executor without changing global
    env requirements for existing features.
    """
    provider_keys = {
        "openai": ("OPENAI_API_KEY", settings.openai_api_key),
        "anthropic": ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        "groq": ("GROQ_API_KEY", settings.groq_api_key),
    }

    for name in {str(provider or "").lower(), str(fallback_provider or "").lower()}:
        if not name:
            continue
        env_name, value = provider_keys.get(name, ("", ""))
        if env_name and value:
            os.environ.setdefault(env_name, value)

    _stabilize_litellm_logging_worker()


def _stabilize_litellm_logging_worker() -> None:
    """
    Patch LiteLLM logging worker queue rollover for asyncio.run()-based workers.

    RQ jobs use asyncio.run() per job, which creates a new event loop each time.
    LiteLLM's global logging worker may drop queued coroutines on loop change,
    which can emit "coroutine ... was never awaited" RuntimeWarning noise.
    """
    try:
        from litellm.litellm_core_utils.logging_worker import LoggingWorker
    except Exception:
        return

    if bool(getattr(LoggingWorker, "_promarshal_loop_safe_patch_applied", False)):
        return

    original_ensure_queue = LoggingWorker._ensure_queue

    def _close_stale_queue_coroutines(worker: "LoggingWorker") -> None:
        queue = getattr(worker, "_queue", None)
        if queue is None:
            return
        while True:
            try:
                task = queue.get_nowait()
            except Exception:
                break
            try:
                coroutine = task.get("coroutine") if isinstance(task, dict) else None
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
            except Exception:
                pass

    def _patched_ensure_queue(self: "LoggingWorker") -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if self._queue is not None and self._bound_loop is not current_loop:
            _close_stale_queue_coroutines(self)
        original_ensure_queue(self)

    LoggingWorker._ensure_queue = _patched_ensure_queue
    LoggingWorker._promarshal_loop_safe_patch_applied = True
