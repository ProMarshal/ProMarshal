"""Cortex permission gate scaffold."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.cortex.tools.base import CortexTool


@dataclass(frozen=True)
class PermissionDecision:
    """Decision envelope for tool-access authorization."""

    allowed: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class ApprovalDecision:
    """Decision envelope for tool-call approval policy."""

    required: bool
    reason: Optional[str] = None


class CortexPermissionService:
    """Permission checks for tool visibility and execution-time re-checks."""

    def can_use_tool(
        self,
        *,
        role: Optional[str],
        tool_name: str,
        role_requirements: Optional[List[str]] = None,
        integration_requirements: Optional[List[str]] = None,
        connected_integrations: Optional[List[str]] = None,
    ) -> PermissionDecision:
        """Return whether the caller can invoke the requested tool."""
        normalized_role = (role or "").strip().lower()
        required_roles = {
            str(item).strip().lower()
            for item in (role_requirements or [])
            if str(item).strip()
        }
        if required_roles and normalized_role not in required_roles:
            return PermissionDecision(
                allowed=False,
                reason=f"role_not_allowed:{tool_name}",
            )

        required_integrations = {
            str(item).strip().lower()
            for item in (integration_requirements or [])
            if str(item).strip()
        }
        available_integrations = {
            str(item).strip().lower()
            for item in (connected_integrations or [])
            if str(item).strip()
        }
        if required_integrations and not required_integrations.issubset(available_integrations):
            return PermissionDecision(
                allowed=False,
                reason=f"integration_not_connected:{tool_name}",
            )

        return PermissionDecision(allowed=True)

    def can_execute_tool(
        self,
        *,
        role: Optional[str],
        tool: "CortexTool",
        connected_integrations: Optional[List[str]] = None,
    ) -> PermissionDecision:
        """Execution-time tool permission re-check using canonical tool metadata."""
        spec = tool.normalized_spec()
        return self.can_use_tool(
            role=role,
            tool_name=spec.name,
            role_requirements=spec.role_requirements,
            integration_requirements=spec.integration_requirements,
            connected_integrations=connected_integrations or [],
        )

    def needs_approval(
        self,
        *,
        role: Optional[str],
        tool: "CortexTool",
        tool_args: Optional[Dict[str, Any]] = None,
    ) -> ApprovalDecision:
        """
        Return whether this tool call needs explicit approval.

        Launch policy:
        - jira_create_work_item: owner=no approval, admin/member=approval
        - other tools: respect ToolSpec.requires_confirmation
        """
        # Contract G: approvals flag is retired from core runtime path.
        # Current hardwired policy keeps explicit approval disabled.
        _ = role
        _ = tool
        _ = tool_args
        return ApprovalDecision(required=False, reason="approvals_disabled")
