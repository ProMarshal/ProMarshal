"""Base tool contracts for Cortex."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ToolSpec:
    """Metadata for registering a Cortex tool."""

    name: str
    description: str
    provider: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    role_requirements: List[str] = field(default_factory=list)
    integration_requirements: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    idempotency: str = "idempotent"
    task_read_scope: bool = False
    lifecycle_status_arg: Optional[str] = None
    lifecycle_non_closed_value: Optional[str] = None
    lifecycle_closed_value: Optional[str] = None

    def normalized(self) -> "ToolSpec":
        """Return normalized metadata copy for deterministic filtering."""
        provider = str(self.provider or "").strip().lower() or None
        capabilities = sorted(
            {
                str(capability).strip().lower()
                for capability in self.capabilities
                if str(capability).strip()
            }
        )
        roles = sorted({str(role).strip().lower() for role in self.role_requirements if str(role).strip()})
        integrations = sorted(
            {
                str(integration).strip().lower()
                for integration in self.integration_requirements
                if str(integration).strip()
            }
        )
        idempotency = str(self.idempotency or IdempotencyClass.IDEMPOTENT.value).strip().lower()
        if idempotency not in {item.value for item in IdempotencyClass}:
            idempotency = IdempotencyClass.IDEMPOTENT.value
        lifecycle_status_arg = str(self.lifecycle_status_arg or "").strip() or None
        lifecycle_non_closed_value = str(self.lifecycle_non_closed_value or "").strip() or None
        lifecycle_closed_value = str(self.lifecycle_closed_value or "").strip() or None

        return ToolSpec(
            name=str(self.name).strip(),
            description=str(self.description).strip(),
            provider=provider,
            capabilities=capabilities,
            role_requirements=roles,
            integration_requirements=integrations,
            requires_confirmation=bool(self.requires_confirmation),
            idempotency=idempotency,
            task_read_scope=bool(self.task_read_scope),
            lifecycle_status_arg=lifecycle_status_arg,
            lifecycle_non_closed_value=lifecycle_non_closed_value,
            lifecycle_closed_value=lifecycle_closed_value,
        )

    def to_prompt_descriptor(self) -> Dict[str, Any]:
        """Convert to prompt-facing descriptor for builder filtering/rendering."""
        normalized = self.normalized()
        return {
            "name": normalized.name,
            "description": normalized.description,
            "provider": normalized.provider,
            "capabilities": normalized.capabilities,
            "role_requirements": normalized.role_requirements,
            "integration_requirements": normalized.integration_requirements,
            "requires_confirmation": normalized.requires_confirmation,
            "idempotency": normalized.idempotency,
            "task_read_scope": normalized.task_read_scope,
            "lifecycle_status_arg": normalized.lifecycle_status_arg,
            "lifecycle_non_closed_value": normalized.lifecycle_non_closed_value,
            "lifecycle_closed_value": normalized.lifecycle_closed_value,
        }


class IdempotencyClass(str, Enum):
    """Allowed idempotency classes for execution policy decisions."""

    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


class MemberRole(str, Enum):
    """Known project roles for launch-time policy filtering."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class CortexTool:
    """Base class for Cortex tool adapters."""

    spec: ToolSpec

    async def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tool behavior and return normalized result."""
        raise NotImplementedError("Tool execution not implemented")

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute tool behavior with optional runtime context."""
        return await self.execute(args)

    def normalized_spec(self) -> ToolSpec:
        """Return tool spec normalized for registry consistency."""
        return self.spec.normalized()

    def supports_role(self, role: Optional[str]) -> bool:
        """Return True when role satisfies tool role requirements."""
        normalized_spec = self.normalized_spec()
        if not normalized_spec.role_requirements:
            return True
        if not role:
            return False
        return str(role).strip().lower() in set(normalized_spec.role_requirements)

    def supports_integrations(self, connected_integrations: List[str]) -> bool:
        """Return True when required integrations are connected."""
        normalized_spec = self.normalized_spec()
        required = set(normalized_spec.integration_requirements)
        if not required:
            return True
        available = {str(name).strip().lower() for name in connected_integrations}
        return required.issubset(available)
