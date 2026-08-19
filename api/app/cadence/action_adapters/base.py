"""Protocol contract for Cadence action adapters."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Protocol, runtime_checkable


@runtime_checkable
class CadenceActionAdapter(Protocol):
    """Provider-scoped adapter that executes one capability action."""

    provider: str
    capability_id: str

    async def execute(
        self,
        *,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        ...
