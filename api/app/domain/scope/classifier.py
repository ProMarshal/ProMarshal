"""Stage-2 LLM scope classifier for project-related messages."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.domain.scope.models import ScopeLlmDecision
from app.domain.scope import registry as scope_registry


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


class ScopeLlmClassifier:
    """LLM-backed secondary scope classifier with strict JSON output contract."""

    PROMPT = (
        "Classify whether the user message is a ProMarshal project operation request.\n"
        "Return STRICT JSON only (no markdown, no prose).\n"
        "Schema:\n"
        "{\n"
        '  "scope_classification": "in_scope_action" | "in_scope_info" | "out_of_scope" | "ambiguous",\n'
        '  "detail_classification": "project_specific" | "project_general" | "project_capability" | "out_of_scope" | "ambiguous",\n'
        '  "reason_code": string,\n'
        '  "confidence": number\n'
        "}\n"
    )

    async def classify(
        self,
        *,
        executor: Any,
        user_message: str,
        gate_decision: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[ScopeLlmDecision]:
        llm_input = {
            "user_message": str(user_message or "").strip(),
            "gate_decision": gate_decision or {},
        }
        result = await executor.run(
            {
                "prompt": self._build_prompt(),
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

        scope_classification = str(payload.get("scope_classification") or "").strip().lower()
        if scope_classification not in {"in_scope_action", "in_scope_info", "out_of_scope", "ambiguous"}:
            return None
        detail_classification = str(payload.get("detail_classification") or "").strip().lower()
        if detail_classification not in {
            "project_specific",
            "project_general",
            "project_capability",
            "out_of_scope",
            "ambiguous",
        }:
            detail_classification = "ambiguous"
        reason_code = (
            str(payload.get("reason_code") or "").strip().lower().replace(" ", "_")
            or "llm_scope_classification"
        )
        try:
            confidence = float(payload.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return ScopeLlmDecision(
            scope_classification=scope_classification,
            reason_code=reason_code,
            detail_classification=detail_classification,
            confidence=confidence,
        )

    def _build_prompt(self) -> str:
        analytics_hints = ", ".join(sorted(list(scope_registry.PM_ANALYTICS_HINTS))[:12])
        project_entities = ", ".join(sorted(list(scope_registry.PROJECT_ENTITY_HINTS))[:12])
        return (
            f"{self.PROMPT}"
            "Rules:\n"
            "1) project_specific: asks about a concrete project entity/action (task/story/epic/work item/key).\n"
            "2) project_general: project operations without a specific key.\n"
            "3) project_capability: asks what ProMarshal can do.\n"
            "4) out_of_scope: general conceptual or non-project question.\n"
            "5) ambiguous: unclear if project operation is intended.\n"
            "6) Keep reason_code concise_snake_case.\n"
            f"7) Project entities/hints include: {project_entities}.\n"
            f"8) Project analytics/hints include: {analytics_hints}.\n"
            "9) If the message is operational analytics about team/project workload or delivery health, prefer in_scope_action.\n"
            "10) If the message asks to check with a team/group for project updates or responses, classify as in_scope_action (project_general or project_specific based on context)."
        )

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
