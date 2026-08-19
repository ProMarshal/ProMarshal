"""Cortex prompt assembly baseline with canonical 7-section ordering."""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.domain.capabilities.materialization import build_capability_context
from app.promarshal.policy.provider_overlays import compose_provider_overlays
from app.promarshal.prompt_registry import (
    compose_identity,
    compose_response_rules,
    compose_scope_policy,
)


ToolVisibilityHook = Callable[[Dict[str, Any], "PromptBuildInput"], bool]
logger = logging.getLogger(__name__)

POLICY_ROOT = Path(__file__).resolve().parent / "policies"

DEFAULT_PERSONA_POLICY = (
    "ProMarshal persona policy:\n"
    "- Be precise, pragmatic, and execution-focused.\n"
    "- Do not fabricate facts, outputs, or tool results.\n"
    "- Prefer concrete next steps over abstract advice.\n"
    "- Keep replies concise unless the user asks for depth.\n"
    "- Preserve project and member trust through accurate state reporting."
)

DEFAULT_RESPONSE_RULES = (
    "Response rules:\n"
    "- Never present assumptions or implications as confirmed facts.\n"
    "- State only what is supported by authoritative evidence in this turn (tool output or project context fields).\n"
    "- Treat historical artifacts (for example existing work items) as non-authoritative for live state (for example integration connected/disconnected).\n"
    "- If evidence is partial or indirect, explicitly state uncertainty and separate what is known from what is unknown.\n"
    "- For field-specific read questions (for example creator, description, link), answer directly from read data instead of reverting to generic list summaries.\n"
    "- If a requested field is missing, explicitly say that field is not available in current data.\n"
    "- Separate committed outcomes from pending or proposed actions.\n"
    "- If blocked, state exactly what is missing and who must provide it.\n"
    "- Use plain, direct language and avoid filler phrases.\n"
    "- For risky or irreversible operations, restate intent before execution.\n"
    "- Never claim completion for writes unless persistence succeeded.\n"
    "- For fully completed outcomes, do not include a Not completed section.\n"
    "- For terminal outcomes with no required follow-up, do not include a Next step section.\n"
    "- Never emit placeholders like None, N/A, null, or empty values in status sections.\n"
    "- Use Slack-friendly formatting: bold section headings only in single-star form (*Heading:*). "
    "Avoid double-asterisk body formatting (**...**) for normal sentences or list items.\n"
    "- For any analytical question (workload, distribution, progress, priority breakdown, stalled tasks, "
    "team overview, trends), after presenting the data always append a brief Insight section with 1-2 "
    "observations — identify imbalances, bottlenecks, risks, or patterns (e.g., overloaded members, "
    "high unassigned count, stalled high-priority tasks). For capacity-risk questions (overburdened, "
    "burnout risk, team load), assess risk from assigned non-closed work per member and report "
    "unassigned work as allocation pressure, not personal burden. Keep it concise and actionable."
)

DEFAULT_ROLE_POLICIES = {
    "owner": (
        "Role policy: owner\n"
        "- Treat the owner as final decision authority for project-level tradeoffs.\n"
        "- Propose decisive plans, while surfacing key risks briefly.\n"
        "- For destructive or high-impact operations, ask for explicit confirmation unless policy allows direct execution."
    ),
    "admin": (
        "Role policy: admin\n"
        "- Treat admin users as operational leaders with elevated coordination responsibilities.\n"
        "- Provide clear execution options with tradeoffs when choices affect team workflow.\n"
        "- Respect approval boundaries for writes and high-impact changes."
    ),
    "member": (
        "Role policy: member\n"
        "- Keep responses practical and task-focused for day-to-day execution.\n"
        "- Do not imply authority the caller does not have.\n"
        "- When action is restricted by role, explain the boundary and required approver."
    ),
}

ROLE_POLICY_FILES = {
    "owner": "roles/owner.md",
    "admin": "roles/admin.md",
    "member": "roles/member.md",
}

DEFAULT_TOOL_COMMON_POLICY = (
    "Tool usage policy (common):\n"
    "- Use tools only when needed to verify facts or perform actions.\n"
    "- For status/state questions, verify with authoritative reads before answering.\n"
    "- Do not infer live status from historical records alone.\n"
    "- Prefer read tools first when mutation is not required.\n"
    "- For write tools, ensure scope and target are explicit.\n"
    "- If required tool access is unavailable, state the missing integration or permission.\n"
    "- Do not use generic capability disclaimers when tools are available; execute, clarify, or request approval.\n"
    "- Report tool outcomes faithfully, including partial failures."
)


@dataclass(frozen=True)
class PromptBuildInput:
    """Input contract for prompt construction."""

    project_context: Dict[str, Any]
    session_context: Dict[str, Any]
    user_message: str
    visible_tools: List[Dict[str, Any]]
    role: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Output of DeterministicInterpretationService.interpret() — injected into
    # the system prompt as a pre-processed intent hint for the LLM.
    pre_processed: Optional[Dict[str, Any]] = None


class CortexPromptBuilder:
    """Builds structured prompt payload for executor instructions."""

    SECTION_TITLES = [
        "Agent identity and behavior",
        "Project context (name, members, integrations, timezone)",
        "Tool instructions and available tools",
        "Safety guardrails and hard constraints",
        "Output formatting rules",
        "Conversation history (recent turns + summaries as applicable)",
        "Current user message",
    ]

    def __init__(
        self,
        *,
        tool_visibility_hook: Optional[ToolVisibilityHook] = None,
        max_prompt_tokens: Optional[int] = None,
    ):
        self.tool_visibility_hook = tool_visibility_hook
        self.max_prompt_tokens = max_prompt_tokens or int(settings.cortex_llm_max_tokens)
        self._policy_warned_keys: set[str] = set()

    def build(self, request: PromptBuildInput) -> Dict[str, Any]:
        """Return structured prompt payload with canonical section ordering."""
        filtered_tools = self._filter_visible_tools(request)
        sections = self._build_sections(request, filtered_tools)
        prompt_text, budget_meta = self._apply_minimal_budget_guard(sections)

        return {
            "instructions": prompt_text,
            "sections": sections,
            "tools": filtered_tools,
            "meta": {
                "tool_count": len(filtered_tools),
                **budget_meta,
            },
        }

    def _filter_visible_tools(self, request: PromptBuildInput) -> List[Dict[str, Any]]:
        allowed_integrations = self._connected_integrations(request.project_context)
        caller_role = (request.role or "").strip().lower()
        filtered: List[Dict[str, Any]] = []

        for tool in request.visible_tools:
            if not isinstance(tool, dict):
                continue

            role_requirements = [
                str(role).strip().lower()
                for role in (tool.get("role_requirements") or [])
                if str(role).strip()
            ]
            if role_requirements and caller_role not in set(role_requirements):
                continue

            integration_requirements = {
                str(name).strip().lower()
                for name in (tool.get("integration_requirements") or [])
                if str(name).strip()
            }
            if integration_requirements and not integration_requirements.issubset(allowed_integrations):
                continue

            if self.tool_visibility_hook and not self.tool_visibility_hook(tool, request):
                continue

            filtered.append(tool)

        return filtered

    def _build_sections(
        self,
        request: PromptBuildInput,
        filtered_tools: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        capability = self._resolve_prompt_capability(request)
        return [
            {
                "id": "identity",
                "title": self.SECTION_TITLES[0],
                "content": self._build_identity_section(request),
            },
            {
                "id": "project_context",
                "title": self.SECTION_TITLES[1],
                "content": self._build_project_context_section(request.project_context),
            },
            {
                "id": "tools",
                "title": self.SECTION_TITLES[2],
                "content": self._build_tools_section(
                    tools=filtered_tools,
                    project_context=request.project_context,
                ),
            },
            *([{
                "id": "pre_processed_intent",
                "title": "Pre-processed intent (deterministic hints)",
                "content": self._build_pre_processed_section(request.pre_processed),
            }] if request.pre_processed else []),
            {
                "id": "guardrails",
                "title": self.SECTION_TITLES[3],
                "content": self._build_guardrails_section(capability=capability),
            },
            {
                "id": "formatting",
                "title": self.SECTION_TITLES[4],
                "content": self._build_formatting_section(capability=capability),
            },
            {
                "id": "history",
                "title": self.SECTION_TITLES[5],
                "content": self._build_history_section(request.session_context),
            },
            {
                "id": "current_user_message",
                "title": self.SECTION_TITLES[6],
                "content": self._build_current_user_message_section(request),
            },
        ]

    def _build_identity_section(self, request: PromptBuildInput) -> str:
        capability = self._resolve_prompt_capability(request)
        role_text = (request.role or "member").strip().lower() or "member"
        persona_policy = self._load_policy(
            relative_path="core/persona.md",
            fallback=DEFAULT_PERSONA_POLICY,
        )
        role_policy = self._load_role_policy(role_text)
        legacy_identity = (
            "You are ProMarshal, a project operations assistant.\n"
            "You must be accurate, non-fabricating, and policy-compliant.\n"
            f"\n{persona_policy}"
        )
        identity_text = compose_identity(
            capability=capability,
            legacy_identity=legacy_identity,
            legacy_overlay="",
        )
        return (
            f"{identity_text}\n"
            f"\nCurrent caller role: {role_text}.\n"
            f"\n{role_policy}"
        )

    def _build_project_context_section(self, project_context: Dict[str, Any]) -> str:
        project_name = project_context.get("project_name") or project_context.get("name") or "Unknown Project"
        project_id = project_context.get("project_id") or ""
        timezone = project_context.get("timezone") or "UTC"

        members = project_context.get("members") or []
        active_members = [m for m in members if (m.get("status") or "active") == "active"]
        member_names = [m.get("name") or m.get("email") or m.get("user_id") for m in active_members]
        member_preview = ", ".join([str(x) for x in member_names[:10] if x]) or "none"

        integrations = sorted(self._connected_integrations(project_context))
        integration_text = ", ".join(integrations) if integrations else "none"

        return (
            f"Project: {project_name} ({project_id})\n"
            f"Timezone: {timezone}\n"
            f"Active members ({len(active_members)}): {member_preview}\n"
            f"Connected integrations: {integration_text}"
        )

    def _build_tools_section(
        self,
        *,
        tools: List[Dict[str, Any]],
        project_context: Dict[str, Any],
    ) -> str:
        lines: List[str] = []
        common_policy = self._load_policy(
            relative_path="tools/common.md",
            fallback=DEFAULT_TOOL_COMMON_POLICY,
        )
        if common_policy:
            lines.append(common_policy)
        lines.append(
            "Approval gating is currently disabled for this runtime. "
            "If role/integration checks pass and inputs are clear, execute directly."
        )

        capability_context = build_capability_context(project_context=project_context or {})
        provider_overlays = compose_provider_overlays(
            providers=capability_context.materialized_pm_providers,
        )
        if provider_overlays:
            lines.extend(provider_overlays)

        if not tools:
            lines.append("No tools are available for this turn.")
            return "\n\n".join(lines)

        tool_lines = []
        for tool in tools:
            name = tool.get("name") or "unnamed_tool"
            description = tool.get("description") or ""
            requires_confirmation = False
            idempotency = tool.get("idempotency") or "unknown"
            tool_lines.append(
                f"- {name}: {description} | confirmation={requires_confirmation} | idempotency={idempotency}"
            )
        lines.append("Available tools for this turn:\n" + "\n".join(tool_lines))
        return "\n\n".join(lines)

    def _build_pre_processed_section(self, pre_processed: Optional[Dict[str, Any]]) -> str:
        """
        Render deterministic pre-processing output as a structured hint block for the LLM.

        The LLM should use this as a starting point, validate against the user message,
        and proceed with its own tool selection and execution.
        """
        if not pre_processed:
            return ""
        lines = ["[Pre-processed Intent — deterministic hints, use as starting point]"]
        confidence = pre_processed.get("confidence")
        if confidence is not None:
            lines.append(f"confidence: {float(confidence):.2f}")
        slot_coverage = pre_processed.get("slot_coverage")
        if slot_coverage is not None:
            lines.append(f"slot_coverage: {float(slot_coverage):.2f}")
        matches = pre_processed.get("matches") or []
        if matches:
            first = matches[0] if isinstance(matches[0], dict) else {}
            action_type = str(first.get("action_type") or "").strip()
            tool_name = str(first.get("tool_name") or "").strip()
            extracted_slots = first.get("extracted_slots") or {}
            missing_fields = first.get("missing_required_fields") or []
            if action_type:
                lines.append(f"matched_action: {action_type}")
            if tool_name:
                lines.append(f"matched_tool: {tool_name}")
            if extracted_slots:
                lines.append(f"extracted_slots: {extracted_slots}")
            if missing_fields:
                lines.append(f"missing_slots: {missing_fields}")
        missing_slots = pre_processed.get("missing_slots") or {}
        if missing_slots and not matches:
            lines.append(f"missing_slots: {missing_slots}")
        lines.append(
            "\nUse these hints to guide your response. "
            "Verify against the user message before executing any tool."
        )
        return "\n".join(lines)

    def _build_guardrails_section(self, *, capability: str) -> str:
        hard_constraints = (
            "Hard constraints:\n"
            "- Preserve project isolation; do not cross project boundaries.\n"
            "- Respect role and integration constraints before taking actions.\n"
            "- Never claim a write succeeded unless persistence confirms success.\n"
            "- Do not reveal secrets, tokens, or credentials in output.\n"
            "- When multiple operations target the same entity (e.g., create then update or comment on the same task), "
            "call tools one at a time and use the result of each call to inform the next. "
            "Never call jira_update_status or jira_add_comment on a task before jira_create_work_item has returned its task ID."
        )
        if not bool(getattr(settings, "promarshal_prompt_registry_enabled", False)):
            return hard_constraints
        scope_policy = compose_scope_policy(legacy_policy="", capability=capability)
        if not scope_policy:
            return hard_constraints
        return f"{scope_policy}\n\n{hard_constraints}"

    def _build_formatting_section(self, *, capability: str) -> str:
        legacy_rules = self._load_policy(
            relative_path="core/response_rules.md",
            fallback=DEFAULT_RESPONSE_RULES,
        )
        return compose_response_rules(
            capability=capability,
            legacy_rules=legacy_rules,
        )

    def _resolve_prompt_capability(self, request: PromptBuildInput) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        if bool(metadata.get("fastpath_compose_mode")):
            return "task_assistant_read"
        return "task_assistant"

    def _load_role_policy(self, role: str) -> str:
        normalized_role = (role or "member").strip().lower() or "member"
        if normalized_role not in ROLE_POLICY_FILES:
            normalized_role = "member"
        return self._load_policy(
            relative_path=ROLE_POLICY_FILES[normalized_role],
            fallback=DEFAULT_ROLE_POLICIES[normalized_role],
        )

    def _load_policy(self, *, relative_path: str, fallback: str) -> str:
        policy_path = POLICY_ROOT / relative_path
        warned_key = str(policy_path)
        try:
            content = policy_path.read_text(encoding="utf-8").strip()
            if content:
                return content
            if warned_key not in self._policy_warned_keys:
                logger.warning("Cortex policy file is empty, using fallback: %s", policy_path)
                self._policy_warned_keys.add(warned_key)
        except FileNotFoundError:
            if warned_key not in self._policy_warned_keys:
                logger.warning("Cortex policy file not found, using fallback: %s", policy_path)
                self._policy_warned_keys.add(warned_key)
        except Exception as exc:
            if warned_key not in self._policy_warned_keys:
                logger.warning(
                    "Cortex policy file load failed (%s), using fallback: %s",
                    str(exc),
                    policy_path,
                )
                self._policy_warned_keys.add(warned_key)

        return (fallback or "").strip()

    def _build_history_section(self, session_context: Dict[str, Any]) -> str:
        summaries = session_context.get("summary_history") or []
        recent_turns = session_context.get("recent_turns") or []
        transcript = session_context.get("transcript") or []
        session_snapshot = session_context.get("session_snapshot")

        lines: List[str] = []
        if summaries:
            latest_summary = summaries[-1]
            lines.append(f"Latest summary: {latest_summary}")

        recent_window = max(1, int(settings.cortex_recent_turns_window))
        if isinstance(session_snapshot, dict) and session_snapshot:
            # Keep history compact when a structured snapshot is present.
            recent_window = min(recent_window, 3)
        for turn in recent_turns[-recent_window:]:
            lines.append(self._format_turn(turn))

        if not recent_turns and transcript:
            for event in transcript[-recent_window:]:
                lines.append(self._format_turn(event))

        if not lines:
            return "No prior conversation history."
        return "\n".join(lines)

    def _build_current_user_message_section(self, request: PromptBuildInput) -> str:
        lines: List[str] = []
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        context_source = str(metadata.get("message_context_source") or "").strip()
        if context_source:
            lines.append(f"Message context source: {context_source}")

        raw_candidates = metadata.get("member_candidates")
        if isinstance(raw_candidates, list) and raw_candidates:
            rendered: List[str] = []
            for item in raw_candidates[:5]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                email = str(item.get("email") or "").strip().lower()
                source = str(item.get("source") or "").strip().lower() or "unknown"
                confidence = item.get("confidence")
                label = name or email or str(item.get("user_id") or "").strip()
                if not label:
                    continue
                confidence_text = ""
                if isinstance(confidence, (int, float)):
                    confidence_text = f", confidence={float(confidence):.2f}"
                rendered.append(f"- {label} (source={source}{confidence_text})")
            if rendered:
                lines.append("Resolved member references:")
                lines.extend(rendered)

        session_snapshot = metadata.get("session_snapshot")
        snapshot_section = self._build_session_snapshot_section(session_snapshot)
        if snapshot_section:
            lines.append(snapshot_section)

        lines.append((request.user_message or "").strip())
        return "\n".join(line for line in lines if str(line).strip())

    def _build_session_snapshot_section(self, snapshot: Any) -> str:
        if not isinstance(snapshot, dict) or not snapshot:
            return ""
        try:
            compact_snapshot = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            return ""
        if not compact_snapshot:
            return ""
        max_chars = 1200
        if len(compact_snapshot) > max_chars:
            compact_snapshot = compact_snapshot[:max_chars] + "...(truncated)"
        return (
            "Session snapshot (structured):\n"
            "```json\n"
            f"{compact_snapshot}\n"
            "```"
        )

    def _format_turn(self, turn: Any) -> str:
        if isinstance(turn, str):
            return f"- {turn}"
        if not isinstance(turn, dict):
            return f"- {str(turn)}"

        event_type = str(turn.get("event_type") or turn.get("role") or "event").strip()
        payload = turn.get("payload") if isinstance(turn.get("payload"), dict) else {}
        text = str(turn.get("text") or turn.get("message") or turn.get("content") or "").strip()

        if event_type == "user_message":
            payload_text = str(payload.get("text") or "").strip() if payload else ""
            return f"- user: {payload_text or text}"

        if event_type == "llm_response":
            response_text = str(payload.get("text") or "").strip() if payload else ""
            executor = payload.get("executor") if isinstance(payload, dict) else {}
            tool_names = (
                executor.get("tool_names_called")
                if isinstance(executor, dict)
                else []
            )
            target_refs = (
                executor.get("target_refs")
                if isinstance(executor, dict)
                else []
            )
            tool_count = len(tool_names) if isinstance(tool_names, list) else 0
            target_count = len(target_refs) if isinstance(target_refs, list) else 0
            summary = response_text or text
            if summary and len(summary) > 180:
                summary = summary[:180] + "...(truncated)"
            suffix = f" | tools={tool_count} targets={target_count}" if (tool_count or target_count) else ""
            return f"- assistant: {summary or '<no text>'}{suffix}"

        if event_type == "error":
            error_text = str(payload.get("user_message") or "").strip() if payload else ""
            return f"- error: {error_text or text or 'runtime failure'}"

        if event_type == "llm_request":
            tool_count = payload.get("tool_count") if payload else None
            if tool_count is not None:
                return f"- llm_request: tool_count={tool_count}"
            return "- llm_request"

        generic = text
        if not generic and payload:
            generic = str(payload.get("text") or payload.get("message") or payload.get("result_summary") or "").strip()
        if generic and len(generic) > 160:
            generic = generic[:160] + "...(truncated)"
        return f"- {event_type}: {generic or '<event>'}"

    def _connected_integrations(self, project_context: Dict[str, Any]) -> set[str]:
        connected = set()
        integrations = project_context.get("integrations") or {}
        if not isinstance(integrations, dict):
            return connected
        for name, cfg in integrations.items():
            if isinstance(cfg, dict) and cfg.get("status") == "connected":
                connected.add(str(name).strip().lower())
        return connected

    def _render_sections(self, sections: List[Dict[str, str]]) -> str:
        rendered = []
        for index, section in enumerate(sections, start=1):
            rendered.append(f"## {index}. {section['title']}\n{section['content']}".strip())
        return "\n\n".join(rendered)

    def _apply_minimal_budget_guard(self, sections: List[Dict[str, str]]) -> tuple[str, Dict[str, Any]]:
        # Simple approximation: ~4 characters per token.
        budget_chars = max(1000, self.max_prompt_tokens * 4)
        history_truncated = False
        user_message_truncated = False

        prompt_text = self._render_sections(sections)
        if len(prompt_text) <= budget_chars:
            return prompt_text, {
                "prompt_chars": len(prompt_text),
                "budget_chars": budget_chars,
                "history_truncated": history_truncated,
                "user_message_truncated": user_message_truncated,
            }

        for section in sections:
            if section["id"] == "history" and len(section["content"]) > 200:
                keep = max(200, len(section["content"]) // 2)
                section["content"] = "[History truncated for budget]\n" + section["content"][-keep:]
                history_truncated = True
                break

        prompt_text = self._render_sections(sections)
        if len(prompt_text) > budget_chars:
            for section in sections:
                if section["id"] == "current_user_message" and len(section["content"]) > 200:
                    overflow = len(prompt_text) - budget_chars
                    keep = max(200, len(section["content"]) - overflow - 64)
                    section["content"] = section["content"][:keep] + " [truncated]"
                    user_message_truncated = True
                    break

        prompt_text = self._render_sections(sections)
        return prompt_text, {
            "prompt_chars": len(prompt_text),
            "budget_chars": budget_chars,
            "history_truncated": history_truncated,
            "user_message_truncated": user_message_truncated,
        }
