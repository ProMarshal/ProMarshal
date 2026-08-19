"""LLM service for generating conversational Cadence messages.

Uses the shared LLM gateway contract so Cadence follows the same provider
routing, retries, and observability path as other migrated features.

Interface unchanged so existing Cadence callers require zero changes.
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.promarshal.prompt_registry import (
    compose_identity,
    compose_response_rules,
    compose_scope_policy,
)
from app.llm import LLMGatewayRequest, LLMMessage, LLMMessageRole, get_shared_llm_gateway

logger = logging.getLogger("app.cadence.llm_service")

# ---------------------------------------------------------------------------
# Model selection helpers
# ---------------------------------------------------------------------------
def _default_model() -> str:
    """Return the provider-qualified model string from Cortex settings."""
    try:
        from app.cortex.llm.litellm_config import (
            build_litellm_runtime_config,
            to_litellm_model,
        )
        cfg = build_litellm_runtime_config()
        return to_litellm_model(cfg.primary_provider, cfg.primary_model)
    except Exception:
        return "groq/llama-3.3-70b-versatile"  # sensible fallback


# ---------------------------------------------------------------------------
# Prompt builders (preserved from legacy service)
# ---------------------------------------------------------------------------

def _get_time_of_day() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    return "evening"

def _build_promarshal_system_prompt(*, task_rules: str) -> str:
    identity = compose_identity(
        capability="cadence",
        legacy_identity=(
            "You are ProMarshal, a project operations assistant running a quick daily check-in."
        ),
        legacy_overlay="",
    )
    scope_policy = compose_scope_policy(legacy_policy="", capability="cadence")
    response_rules = compose_response_rules(capability="cadence", legacy_rules="")
    parts = [
        identity.strip(),
        scope_policy.strip(),
        response_rules.strip(),
        str(task_rules or "").strip(),
    ]
    return "\n\n".join([part for part in parts if part])


_SYSTEM_PROMPT = _build_promarshal_system_prompt(task_rules="""CRITICAL RULES:
1. Your ONLY job is to write a SHORT transition phrase between tasks (1 sentence, max 20 words).
2. NEVER mention the task's status, priority, or title — those are shown separately in the UI.
3. NEVER use greetings (no "Good morning", "Hope you're well", etc.)
4. NEVER say "Let's review" or "Now let's check" — be more natural.
5. Be warm, brief, and human. Sound like a teammate, not a bot.

GOOD examples:
- "Got it, moving on!"
- "Nice, one down! Next up:"
- "Noted. Here's the next one:"
- "Alright, let's keep going."
- "Great progress! Next:"

BAD examples (too long / repeats UI info):
- "Now, let's review SCRUM-6: feature charter, which is currently in progress with a medium priority."
- "Great job updating that task! Let's move on to the next one in your project."
""")

_KEY_INPUT_SYSTEM_PROMPT = _build_promarshal_system_prompt(task_rules="""CRITICAL RULES:
1. Your ONLY job is to infer one key check-in insight from task comments.
2. Condense the comments into ONE short insight sentence (max 30 words).
3. Never quote or repeat the raw comment text verbatim.
4. Infer the THEME: blocker, dependency, testing status, progress update, risk flag, etc.
5. Be specific about WHAT is affected (reference task keys if mentioned).
6. Do NOT start with the member's name.
7. Do NOT use greetings or filler phrases.

GOOD examples:
- "Flagged a dependency issue on PROJ-45 -- waiting on security team for API access"
- "Noted that testing is pending for PROJ-18 before it can move to Done"
- "Reported progress on integration work, nearing completion"

BAD examples:
- "The user said they are blocked" (too vague)
- "Comment: I need to wait for the API" (quoting raw text)
""")

_PM_ATTENTION_SYSTEM_PROMPT = _build_promarshal_system_prompt(task_rules="""CRITICAL RULES:
1. Your ONLY job is to produce a concise PM attention list from cadence summary signals.
2. Return STRICT JSON only in this format: {"points": ["...", "..."]}.
3. Keep each point under 140 characters.
4. Focus on actionable items: blockers, dependencies, non-responders, at-risk work, explicit overall updates, and members needing next-task alignment.
5. NEVER mention task IDs/keys (for example SCRUM-10, PROJ-4); refer to task titles or plain-language summaries.
6. Do NOT mention internal workflow or policy internals (for example backlog prompt skipped/threshold/policy state).
7. Do NOT include greetings, preambles, markdown, numbering, or tool names.
8. If there is nothing critical, return {"points": []}.
""")

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


def _build_first_task_prompt(
    project_name: str,
    user_name: str,
    task_key: str,
    task_title: str,
    status: str,
    priority: str,
    total_tasks: int,
) -> str:
    # For the first task, the greeting is already sent separately.
    # LLM just needs a brief intro to kick off the first task.
    return f"""This is the FIRST task in a daily check-in session for {user_name} on project {project_name}.
A greeting message was already sent separately.

Write a VERY SHORT transition phrase (max 15 words) to introduce the first task.
Do NOT mention the task name, status, or priority — those are shown in the UI.

Examples:
- "Let's start with your first one:"
- "Here's what's on your plate:"
- "First up:"

Generate the transition phrase now:"""


def _build_next_task_prompt(
    project_name: str,
    user_name: str,
    task_key: str,
    task_title: str,
    status: str,
    priority: str,
    task_index: int,
    total_tasks: int,
    previous_context: Dict[str, Any],
) -> str:
    prev_key = previous_context.get("task_key", "")
    prev_new_status = previous_context.get("new_status", "")
    prev_comment = previous_context.get("comment", "")
    status_changed = previous_context.get("status_changed", False)
    has_comment = previous_context.get("has_comment", False)

    is_generic_comment = (
        has_comment
        and (
            len(prev_comment) < 10
            or prev_comment.lower()
            in ["done", "completed", "ok", "okay", "test", "test comment"]
        )
    )

    # Build context description
    if status_changed and prev_new_status == "done":
        context_desc = f"User just COMPLETED {prev_key}."
    elif status_changed:
        context_desc = f"User changed {prev_key} to {prev_new_status}."
    elif has_comment and not is_generic_comment:
        context_desc = f'User left a note on {prev_key}: "{prev_comment[:40]}"'
    elif has_comment:
        context_desc = f"User left a brief note on {prev_key}."
    else:
        context_desc = f"User skipped {prev_key} (no changes)."

    return f"""PREVIOUS: {context_desc}

Write a SHORT transition phrase (max 20 words) that:
1. Briefly acknowledges what happened with the previous task (1-3 words)
2. Moves on to the next task

Do NOT mention the next task's name, status, or priority — those are shown in the UI.
Do NOT use greetings.

Generate the transition phrase now:"""


# ---------------------------------------------------------------------------
# Core LLM calls via shared gateway
# ---------------------------------------------------------------------------

async def _call_llm(
    user_prompt: str,
    *,
    max_tokens: int = 40,
    temperature: float = 0.7,
    system_prompt: Optional[str] = None,
) -> Optional[str]:
    """Single LLM call through shared gateway with graceful fallback.

    Args:
        user_prompt: The user-level prompt text.
        max_tokens: Maximum response length.
        temperature: Sampling temperature.
        system_prompt: Optional override for the system prompt.
            Defaults to ``_SYSTEM_PROMPT`` if not provided.
    """
    try:
        gateway = get_shared_llm_gateway()
        request = LLMGatewayRequest(
            messages=[
                LLMMessage(role=LLMMessageRole.SYSTEM, content=system_prompt or _SYSTEM_PROMPT),
                LLMMessage(role=LLMMessageRole.USER, content=user_prompt),
            ],
            temperature=float(temperature),
            max_tokens=max(1, int(max_tokens)),
            timeout_seconds=5,
            retries=0,
            model=_default_model(),
            metadata={"feature": "cadence_llm_service"},
        )
        response = await gateway.generate(request)
        if not response.ok:
            return None
        text = str(response.text or "").strip()
        return text or None
    except Exception as exc:
        logger.warning("cadence_llm_call_failed error=%s", exc)
        return None


# ---------------------------------------------------------------------------
# Public interface  (unchanged from legacy Groq service)
# ---------------------------------------------------------------------------

class CadenceLLMService:
    """LLM-powered message generator for Cadence sessions.

    Drop-in replacement for the legacy ``GroqLLMService``: same public methods,
    same signatures, now routed through the shared LLM gateway.
    """

    async def generate_task_message(
        self,
        project_name: str,
        user_name: str,
        task: Dict[str, Any],
        task_index: int,
        total_tasks: int,
        previous_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        task_key = task.get("external_id", "N/A")
        task_title = task.get("title", "Untitled")
        status = task.get("status", "unknown")
        priority = task.get("priority", "medium")

        if previous_context is None:
            prompt = _build_first_task_prompt(
                project_name, user_name, task_key, task_title,
                status, priority, total_tasks,
            )
        else:
            prompt = _build_next_task_prompt(
                project_name, user_name, task_key, task_title,
                status, priority, task_index + 1, total_tasks,
                previous_context,
            )

        result = await _call_llm(prompt)
        if result:
            print(f"LLM generated message for {task_key}: {result[:50]}...")
            return result

        return self._generate_fallback_message(task, user_name, task_index, previous_context)

    async def generate_session_summary(
        self,
        user_name: str,
        total_tasks: int,
        tasks_completed: int,
        tasks_updated: int,
        tasks_skipped: int,
    ) -> str:
        prompt = f"""User: {user_name}
Total tasks reviewed: {total_tasks}
Tasks completed (marked as done): {tasks_completed}
Tasks updated (status changed or comments added, but not done): {tasks_updated}
Tasks skipped (no changes): {tasks_skipped}

INSTRUCTIONS:
Generate a brief, friendly closing message for the task review session.
Keep it under 50 words (2-3 sentences).
End with something like "See you tomorrow!" or "Let's catch up tomorrow!"

Generate the closing message now:"""

        result = await _call_llm(prompt, max_tokens=100)
        if result:
            print(f"LLM generated summary: {result[:50]}...")
            return result

        return self._generate_fallback_summary(tasks_completed, total_tasks)

    async def generate_key_input(
        self,
        member_name: str,
        task_comments: List[Dict[str, str]],
    ) -> Optional[str]:
        """Infer a one-line insight from a member's task comments.

        Called once per session at completion time.  The result is stored
        permanently on the session document as ``session_summary.key_input``.

        Args:
            member_name: Display name of the team member.
            task_comments: List of dicts with ``task_key`` and ``comment``.

        Returns:
            A single-line inferred highlight, or a rule-based fallback if
            the LLM is unavailable or fails.  Returns ``None`` only when
            there are no meaningful comments at all.
        """
        meaningful = [
            c for c in task_comments
            if c.get("comment") and c["comment"].strip()
        ]
        if not meaningful:
            return None

        comments_block = "\n".join(
            f"- {c['task_key']}: \"{c['comment'].strip()}\""
            for c in meaningful
        )

        prompt = f"""Member: {member_name}
Comments left during cadence check-in:
{comments_block}

INSTRUCTIONS:
Condense ALL the comments above into ONE short sentence (max 30 words).
Focus on the overall theme or key takeaway -- blockers, progress notes, dependency flags, testing status, etc.
Do NOT quote the raw comment text. Infer the meaning.
Do NOT start with the member's name.

Generate the one-line insight now:"""

        result = await _call_llm(
            prompt,
            max_tokens=60,
            temperature=0.3,
            system_prompt=_KEY_INPUT_SYSTEM_PROMPT,
        )
        if result:
            print(f"[cadence/llm_service] Key input for {member_name}: {result[:60]}")
            return result

        return self._generate_fallback_key_input(meaningful)

    async def generate_pm_attention_points(
        self,
        *,
        overall_summary: str,
        non_responders: List[str],
        member_updates: List[Dict[str, Any]],
        deterministic_points: Optional[List[str]] = None,
        include_backlog_context: bool = True,
        max_points: int = 5,
    ) -> List[str]:
        """Generate PM attention points for cadence daily summary.

        Uses LLM-first generation with strict JSON output and deterministic
        fallback when unavailable or parsing fails.
        """
        capped_max = max(1, min(int(max_points or 5), 8))
        deterministic = [str(p or "").strip() for p in (deterministic_points or []) if str(p or "").strip()]

        compact_member_updates: List[Dict[str, Any]] = []
        for entry in member_updates[:20]:
            if not isinstance(entry, dict):
                continue
            notable_items: List[Dict[str, str]] = []
            for note in (entry.get("notable_comments") or [])[:6]:
                if not isinstance(note, dict):
                    continue
                title = str(note.get("task_title") or "").strip()
                comment = str(note.get("comment") or "").strip()
                tag = str(note.get("tag") or "").strip().lower()
                if not comment:
                    continue
                notable_items.append(
                    {
                        "task_title": title,
                        "comment": comment[:220],
                        "tag": tag,
                    }
                )
            task_titles: List[str] = []
            for task_bucket in ("closed_tasks", "in_progress_tasks", "backlog_pickups"):
                for task in (entry.get(task_bucket) or [])[:6]:
                    if not isinstance(task, dict):
                        continue
                    title = str(task.get("title") or "").strip()
                    if title and title not in task_titles:
                        task_titles.append(title)
            compact_member_updates.append(
                {
                    "member": str(entry.get("member") or "").strip(),
                    "closed_count": len(entry.get("closed_tasks") or []),
                    "in_progress_count": len(entry.get("in_progress_tasks") or []),
                    "active_task_count": len(entry.get("in_progress_tasks") or []),
                    "overall_update": str(entry.get("overall_update") or "").strip(),
                    "task_titles": task_titles[:8],
                    "notable_items": notable_items,
                    "key_input": str(entry.get("key_input") or "").strip(),
                }
            )
            if include_backlog_context:
                compact_member_updates[-1].update(
                    {
                        "backlog_pickups_count": len(entry.get("backlog_pickups") or []),
                        "backlog_action": str(entry.get("backlog_action") or "").strip().lower() or None,
                        "backlog_remaining_count": int(entry.get("backlog_remaining_count") or 0),
                    }
                )

        payload = {
            "overall_summary": str(overall_summary or "").strip(),
            "non_responders": [str(name or "").strip() for name in (non_responders or []) if str(name or "").strip()],
            "member_updates": compact_member_updates,
            "deterministic_fallback_points": deterministic[:capped_max],
            "max_points": capped_max,
        }

        prompt = (
            "Generate PM attention points from this cadence summary payload.\n"
            "Return strict JSON only.\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=True)}"
        )
        result = await _call_llm(
            prompt,
            max_tokens=220,
            temperature=0.2,
            system_prompt=_PM_ATTENTION_SYSTEM_PROMPT,
        )
        if not result:
            return deterministic[:capped_max]

        parsed_points = self._parse_attention_points_payload(result)
        if not parsed_points:
            return deterministic[:capped_max]

        clean_points: List[str] = []
        for point in parsed_points:
            normalized = str(point or "").strip()
            if not normalized:
                continue
            if normalized not in clean_points:
                clean_points.append(normalized)
            if len(clean_points) >= capped_max:
                break
        return clean_points if clean_points else deterministic[:capped_max]

    @staticmethod
    def _parse_attention_points_payload(raw_text: str) -> List[str]:
        text = str(raw_text or "").strip()
        if not text:
            return []

        payload: Any = None
        try:
            payload = json.loads(text)
        except Exception:
            match = _JSON_BLOCK_RE.search(text)
            if match:
                try:
                    payload = json.loads(match.group(0))
                except Exception:
                    payload = None

        if isinstance(payload, dict):
            points = payload.get("points")
            if isinstance(points, list):
                return [str(item or "").strip() for item in points if str(item or "").strip()]
            return []
        if isinstance(payload, list):
            return [str(item or "").strip() for item in payload if str(item or "").strip()]
        return []

    # -- Fallbacks (zero-dependency, always work) --

    @staticmethod
    def _generate_fallback_summary(tasks_completed: int, total_tasks: int) -> str:
        if tasks_completed == total_tasks:
            return f"Great work! You completed all {total_tasks} tasks! See you tomorrow!"
        elif tasks_completed > 0:
            return f"Nice work! You completed {tasks_completed} out of {total_tasks} tasks. See you tomorrow!"
        return "Thanks for reviewing your tasks! See you tomorrow!"

    @staticmethod
    def _generate_fallback_key_input(
        meaningful_comments: List[Dict[str, str]],
    ) -> str:
        """Rule-based fallback when LLM is unavailable for key_input."""
        texts = " ".join(
            c.get("comment", "").lower() for c in meaningful_comments
        )
        if any(kw in texts for kw in ("block", "wait", "depend", "stuck")):
            return "Flagged a potential blocker or dependency"
        if any(kw in texts for kw in ("test", "qa", "verify")):
            return "Noted testing or QA activity pending"
        if any(kw in texts for kw in ("done", "complete", "finish", "ship")):
            return "Reported progress toward completion"
        if any(kw in texts for kw in ("review", "pr", "merge")):
            return "Mentioned code review or merge activity"
        return "Left progress notes"

    @staticmethod
    def _generate_fallback_message(
        task: Dict[str, Any],
        user_name: str,
        task_index: int,
        previous_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        if previous_context is None:
            return "First up:"

        if previous_context.get("status_changed"):
            if previous_context.get("new_status") == "done":
                return "Nice work! Next:"
            return "Got it. Next one:"

        if previous_context.get("has_comment"):
            return "Noted! Moving on:"

        return "Alright, next:"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_cadence_llm: Optional[CadenceLLMService] = None


def initialize_llm_service(api_key: str = ""):
    """Eagerly create the Cadence LLM service singleton.

    Called from cron/main startup. Shared gateway config is resolved from
    Cortex runtime settings; ``api_key`` remains ignored for compatibility.
    """
    global _cadence_llm
    _cadence_llm = CadenceLLMService()


def get_llm_service() -> Optional[CadenceLLMService]:
    """Return the Cadence LLM service (auto-creates if needed)."""
    global _cadence_llm
    if _cadence_llm is None:
        _cadence_llm = CadenceLLMService()
    return _cadence_llm

