"""Registry for provider/capability-scoped Cadence adapters."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

from app.cadence.action_adapters.base import CadenceActionAdapter
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty


def _normalize(value: str) -> str:
    return str(value or "").strip().lower()


@dataclass(frozen=True)
class RegisteredCadenceAdapter:
    provider: str
    capability_id: str
    bundle_id: str
    adapter: CadenceActionAdapter


class CadenceAdapterNotFoundError(KeyError):
    """Raised when a provider/capability adapter lookup fails."""


class CadenceAdapterDispatchError(RuntimeError):
    """Raised when adapter execution fails."""


class CadenceActionAdapterRegistry:
    """In-memory registry for Cadence action adapters."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: Dict[Tuple[str, str], RegisteredCadenceAdapter] = {}
        self._dirty_capability_ids = {
            "workitem_update_status",
            "workitem_add_comment",
            "workitem_assign",
            "workitem_update_description",
        }
        self._dirty_capability_fragments = {
            "comment",
            "assign",
            "status",
            "description",
            "due",
            "start",
            "create",
            "delete",
        }

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def register(
        self,
        adapter: CadenceActionAdapter,
        *,
        provider: Optional[str] = None,
        bundle_id: str = "",
    ) -> None:
        normalized_provider = _normalize(provider or getattr(adapter, "provider", ""))
        capability_id = _normalize(getattr(adapter, "capability_id", ""))
        if not normalized_provider:
            raise ValueError("cadence adapter provider is required")
        if not capability_id:
            raise ValueError("cadence adapter capability_id is required")
        key = (normalized_provider, capability_id)
        record = RegisteredCadenceAdapter(
            provider=normalized_provider,
            capability_id=capability_id,
            bundle_id=_normalize(bundle_id),
            adapter=adapter,
        )
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                if existing.adapter is adapter:
                    return
                if type(existing.adapter) is type(adapter):
                    return
                raise ValueError(
                    "duplicate_cadence_adapter:"
                    f"provider={normalized_provider},capability_id={capability_id}"
                )
            self._items[key] = record

    def register_many(
        self,
        adapters: Iterable[CadenceActionAdapter],
        *,
        provider: Optional[str] = None,
        bundle_id: str = "",
    ) -> None:
        for adapter in adapters or []:
            self.register(adapter, provider=provider, bundle_id=bundle_id)

    def get(self, *, provider: str, capability_id: str) -> Optional[CadenceActionAdapter]:
        key = (_normalize(provider), _normalize(capability_id))
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            return item.adapter

    async def dispatch(
        self,
        *,
        provider: str,
        capability_id: str,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Optional[Mapping[str, Any]] = None,
    ) -> MutableMapping[str, Any]:
        adapter = self.get(provider=provider, capability_id=capability_id)
        if adapter is None:
            raise CadenceAdapterNotFoundError(
                f"cadence adapter not found: provider={_normalize(provider)}, capability_id={_normalize(capability_id)}"
            )
        try:
            result = await adapter.execute(
                db=db,
                session=session,
                payload=payload,
                context=context or {},
            )
        except Exception as exc:
            raise CadenceAdapterDispatchError(
                "cadence adapter dispatch failed: "
                f"provider={_normalize(provider)}, capability_id={_normalize(capability_id)}, error={exc}"
            ) from exc
        if isinstance(result, dict):
            self._maybe_mark_recommendation_dirty(
                capability_id=capability_id,
                session=session,
                payload=payload,
                context=context or {},
                result=result,
            )
            return result
        if result is None:
            return {}
        return dict(result)

    def _maybe_mark_recommendation_dirty(
        self,
        *,
        capability_id: str,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        normalized_capability = _normalize(capability_id)
        if not self._should_mark_dirty_for_capability(normalized_capability):
            return
        if not bool(result.get("ok")):
            return
        project_id = str(
            context.get("project_id")
            or session.get("project_id")
            or ""
        ).strip()
        task_key = self._resolve_task_key_for_dirty_mark(payload=payload, result=result)
        if not project_id or not task_key:
            return
        try:
            mark_project_task_dirty(project_id, task_key)
        except Exception:
            return

    def _resolve_task_key_for_dirty_mark(
        self,
        *,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> str:
        def _candidate(source: Mapping[str, Any], key: str) -> str:
            return str(source.get(key) or "").strip()

        for key in (
            "task_key",
            "task_id",
            "external_id",
            "workitem_key",
            "workitem_id",
            "key",
            "id",
        ):
            value = _candidate(payload, key)
            if value:
                return value

        payload_task = payload.get("task")
        if isinstance(payload_task, Mapping):
            for key in ("task_key", "task_id", "external_id", "workitem_key", "workitem_id", "key", "id"):
                value = _candidate(payload_task, key)
                if value:
                    return value

        payload_workitem = payload.get("workitem")
        if isinstance(payload_workitem, Mapping):
            for key in ("task_key", "task_id", "external_id", "workitem_key", "workitem_id", "key", "id"):
                value = _candidate(payload_workitem, key)
                if value:
                    return value

        for key in (
            "task_key",
            "task_id",
            "external_id",
            "workitem_key",
            "workitem_id",
            "key",
            "id",
        ):
            value = _candidate(result, key)
            if value:
                return value

        task_payload = result.get("task")
        if isinstance(task_payload, Mapping):
            for key in ("task_key", "task_id", "external_id", "workitem_key", "workitem_id", "key", "id"):
                value = _candidate(task_payload, key)
                if value:
                    return value

        return ""

    def _should_mark_dirty_for_capability(self, capability_id: str) -> bool:
        if capability_id in self._dirty_capability_ids:
            return True
        if not capability_id:
            return False
        if "task" not in capability_id and "workitem" not in capability_id:
            return False
        return any(fragment in capability_id for fragment in self._dirty_capability_fragments)


cadence_action_adapter_registry = CadenceActionAdapterRegistry()
