"""LLM-backed tool proposal extraction for implicit actionable requests."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.cortex.proposals.models import ToolProposal


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


class CortexProposalService:
    """Generate structured proposal JSON using the existing Cortex executor."""

    PROPOSAL_PROMPT = (
        "You convert a user message into an execution proposal.\n"
        "Return STRICT JSON only (no markdown, no prose).\n"
        "Schema:\n"
        "{\n"
        '  "is_actionable": boolean,\n'
        '  "confidence": number,\n'
        '  "summary": string,\n'
        '  "scope_classification": "in_scope_action" | "in_scope_info" | "out_of_scope" | "ambiguous",\n'
        '  "policy_reason_code": string,\n'
        '  "needs_confirmation": boolean,\n'
        '  "missing_fields": string[],\n'
        '  "proposed_tool_calls": [\n'
        "    {\n"
        '      "tool_name": string (optional),\n'
        '      "capability": string (optional),\n'
        '      "provider": string (optional),\n'
        '      "args": object,\n'
        '      "field_meta": object (optional; keys are arg names, values include provenance/confidence),\n'
        '      "confidence": number,\n'
        '      "reason": string\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "1) For actionable operations, set capability using a value from visible_capabilities.\n"
        "2) If tool_name is provided, it must be from visible_tools and consistent with the capability.\n"
        "3) For args, use exact canonical parameter names from tool_contracts[].fields only.\n"
        "3a) Do not output alias keys when a canonical key exists.\n"
        "3b) Never use empty placeholders for arg values (for example: \"\", null, \"none\", \"n/a\"). "
        "If unknown, omit the field.\n"
        "3c) If possible, include field_meta per arg key with:\n"
        '    - provenance in {"explicit_user_text","deterministic_parse","llm_inferred","llm_repaired","followup_user"}\n'
        "    - confidence in [0,1].\n"
        "4) If the request is actionable but inferred, set needs_confirmation=true.\n"
        "5) If info is missing, include missing_fields and keep args partial.\n"
        "6) Never claim tools are unavailable unless visible_tools is empty.\n"
        "7) Prefer concise, factual summary.\n"
        "8) If not actionable, set is_actionable=false and proposed_tool_calls=[].\n"
        "9) Scope policy: if user asks general conceptual knowledge/advice (for example definitions/explanations) "
        "without a concrete ProMarshal project operation, set scope_classification=out_of_scope.\n"
        "10) Capability/help asks about ProMarshal itself are in_scope_info.\n"
        "11) If uncertain whether request is project-operational, set scope_classification=ambiguous."
    )
    REPAIR_PROMPT = (
        "You repair proposed tool-call args using context and strict contracts.\n"
        "Return STRICT JSON only (no markdown, no prose).\n"
        "Schema:\n"
        "{\n"
        '  "repaired_tool_calls": [\n'
        "    {\n"
        '      "tool_name": string,\n'
        '      "args": object\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "1) Use only tool names present in incoming tool_calls.\n"
        "2) For args, use exact canonical field names from tool_contracts[].fields.\n"
        "3) Keep existing values unless context clearly provides a better value.\n"
        "4) Never invent unknown facts.\n"
        "4a) Never emit placeholder values (for example: \"\", null, \"none\", \"n/a\"). "
        "If unresolved, omit the field.\n"
        "5) Prefer values from original_request first, then followup_message.\n"
        "6) If missing fields cannot be resolved, leave them absent.\n"
        "7) For title/summary-like fields, output the concrete deliverable/topic, "
        "not procedural command text."
    )
    QUALITY_PROMPT = (
        "You assess whether one proposed field value is sufficiently specific for persistent execution.\n"
        "Return STRICT JSON only (no markdown, no prose).\n"
        "Schema:\n"
        "{\n"
        '  "is_sufficient": boolean,\n'
        '  "confidence": number,\n'
        '  "reason": string\n'
        "}\n"
        "Rules:\n"
        "1) Decide based on current_value plus available context.\n"
        "2) If value is vague/deictic/template-like and context does not disambiguate clearly, mark false.\n"
        "2a) If value sounds like procedural instruction text (for example command phrasing), mark false.\n"
        "3) If context clearly resolves intent into a concrete value, mark true.\n"
        "3a) Concise deliverable/topic titles are acceptable when they are concrete in context.\n"
        "4) Be conservative: avoid false positives.\n"
        "5) Keep reason concise."
    )

    async def propose(
        self,
        *,
        executor: Any,
        user_message: str,
        visible_tools: List[Dict[str, Any]],
        tool_contracts: List[Dict[str, Any]],
        member_candidates: List[Dict[str, Any]],
        message_context_source: str,
        context: Dict[str, Any],
    ) -> Optional[ToolProposal]:
        visible_tool_names = [
            str(item.get("name") or "").strip()
            for item in visible_tools
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        visible_capabilities = sorted(
            {
                str(capability).strip().lower()
                for descriptor in visible_tools
                if isinstance(descriptor, dict)
                for capability in (descriptor.get("capabilities") or [])
                if str(capability).strip()
            }
        )
        if not visible_tool_names:
            return None

        llm_input = {
            "user_message": str(user_message or "").strip(),
            "message_context_source": str(message_context_source or "").strip(),
            "visible_tools": visible_tool_names,
            "visible_capabilities": visible_capabilities,
            "tool_contracts": tool_contracts,
            "member_candidates": member_candidates[:8] if isinstance(member_candidates, list) else [],
        }
        result = await executor.run(
            {
                "prompt": self.PROPOSAL_PROMPT,
                "input": json.dumps(llm_input, ensure_ascii=True),
                "tools": [],
                "context": {
                    "project_id": str(context.get("project_id") or ""),
                    "user_id": str(context.get("user_id") or ""),
                    "session_key": str(context.get("session_key") or ""),
                    "source": str(context.get("source") or ""),
                    "run_id": str(context.get("run_id") or ""),
                    "role": str(context.get("role") or ""),
                    "connected_integrations": [],
                    "planning_only": True,
                },
            }
        )
        if not result.ok:
            return None

        payload = self._extract_json_payload(str(result.output_text or ""))
        if not isinstance(payload, dict):
            return None
        try:
            proposal = ToolProposal.model_validate(payload)
        except Exception:
            return None

        cleaned_calls = [
            call
            for call in proposal.proposed_tool_calls
            if (
                bool(str(call.tool_name or "").strip())
                or bool(str(call.capability or "").strip())
            )
        ]
        proposal.proposed_tool_calls = cleaned_calls

        # Keep proposal available for scope enforcement even when not actionable.
        return proposal

    async def repair_tool_args(
        self,
        *,
        executor: Any,
        tool_calls: List[Dict[str, Any]],
        tool_contracts: List[Dict[str, Any]],
        original_request: str,
        followup_message: str,
        message_context_source: str,
        context: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(tool_calls, list) or not tool_calls:
            return None

        llm_input = {
            "tool_calls": tool_calls,
            "tool_contracts": tool_contracts,
            "original_request": str(original_request or "").strip(),
            "followup_message": str(followup_message or "").strip(),
            "message_context_source": str(message_context_source or "").strip(),
        }
        result = await executor.run(
            {
                "prompt": self.REPAIR_PROMPT,
                "input": json.dumps(llm_input, ensure_ascii=True),
                "tools": [],
                "context": {
                    "project_id": str(context.get("project_id") or ""),
                    "user_id": str(context.get("user_id") or ""),
                    "session_key": str(context.get("session_key") or ""),
                    "source": str(context.get("source") or ""),
                    "run_id": str(context.get("run_id") or ""),
                    "role": str(context.get("role") or ""),
                    "connected_integrations": [],
                    "planning_only": True,
                },
            }
        )
        if not result.ok:
            return None

        payload = self._extract_json_payload(str(result.output_text or ""))
        if not isinstance(payload, dict):
            return None
        repaired = payload.get("repaired_tool_calls")
        if not isinstance(repaired, list):
            return None

        cleaned: List[Dict[str, Any]] = []
        incoming_names = {
            str(item.get("tool_name") or "").strip()
            for item in tool_calls
            if isinstance(item, dict)
        }
        for item in repaired:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or tool_name not in incoming_names or not isinstance(args, dict):
                continue
            cleaned.append({"tool_name": tool_name, "args": args})
        return cleaned or None

    async def assess_field_quality(
        self,
        *,
        executor: Any,
        tool_name: str,
        field_name: str,
        current_value: Any,
        policy: Dict[str, Any],
        original_request: str,
        followup_message: str,
        message_context_source: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        llm_input = {
            "tool_name": str(tool_name or "").strip(),
            "field_name": str(field_name or "").strip(),
            "current_value": current_value,
            "policy": policy or {},
            "original_request": str(original_request or "").strip(),
            "followup_message": str(followup_message or "").strip(),
            "message_context_source": str(message_context_source or "").strip(),
        }
        result = await executor.run(
            {
                "prompt": self.QUALITY_PROMPT,
                "input": json.dumps(llm_input, ensure_ascii=True),
                "tools": [],
                "context": {
                    "project_id": str(context.get("project_id") or ""),
                    "user_id": str(context.get("user_id") or ""),
                    "session_key": str(context.get("session_key") or ""),
                    "source": str(context.get("source") or ""),
                    "run_id": str(context.get("run_id") or ""),
                    "role": str(context.get("role") or ""),
                    "connected_integrations": [],
                    "planning_only": True,
                },
            }
        )
        if not result.ok:
            return None

        payload = self._extract_json_payload(str(result.output_text or ""))
        if not isinstance(payload, dict):
            return None
        if "is_sufficient" not in payload:
            return None
        try:
            return {
                "is_sufficient": bool(payload.get("is_sufficient")),
                "confidence": float(payload.get("confidence") or 0.0),
                "reason": str(payload.get("reason") or "").strip(),
            }
        except Exception:
            return None

    def _extract_json_payload(self, text: str) -> Optional[Dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            return None
        snippet = str(match.group(0) or "").strip()
        if not snippet:
            return None
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
        return None
