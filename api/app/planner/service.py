"""Planner service - PM Agent logic with LLM integration (Claude/Groq)"""

import asyncio
import httpx
import os
import io
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from bson import ObjectId
from fastapi import UploadFile

from app.core.config import settings
from app.core.database import get_database, get_planner_discussion_collection, get_brain_collection
from app.planner.prompt_templates import build_prompt, load_prompt
from app.planner.prompt_templates.config import get_model_for_task, ModelConfig
from app.planner.topic_registry import get_ordered_default_topics, get_storage_mode
from app.planner.document_processor import DocumentProcessor


class PlannerService:
    """Service for PM Agent chat and planning stage management"""

    def __init__(self):
        self.timeout = 60.0
        self.charter_source = "charter"

    def _charter_source_filter(self) -> Dict[str, Any]:
        return {"source": self.charter_source}

    def _legacy_source_filter(self) -> Dict[str, Any]:
        return {"$or": [{"source": {"$exists": False}}, {"source": None}]}

    async def _find_one_with_charter_fallback(
        self,
        collection: Any,
        query: Dict[str, Any],
        *,
        sort: Optional[List[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        charter_query = {**query, **self._charter_source_filter()}
        doc = await collection.find_one(charter_query, sort=sort)
        if doc:
            return doc
        legacy_query = {**query, **self._legacy_source_filter()}
        return await collection.find_one(legacy_query, sort=sort)

    async def _find_many_with_charter_fallback(
        self,
        collection: Any,
        query: Dict[str, Any],
        *,
        sort_field: str = "_id",
        sort_dir: int = 1,
    ) -> List[Dict[str, Any]]:
        charter_query = {**query, **self._charter_source_filter()}
        docs: List[Dict[str, Any]] = []
        cursor = collection.find(charter_query).sort(sort_field, sort_dir)
        async for doc in cursor:
            docs.append(doc)
        if docs:
            return docs
        legacy_query = {**query, **self._legacy_source_filter()}
        cursor = collection.find(legacy_query).sort(sort_field, sort_dir)
        async for doc in cursor:
            docs.append(doc)
        return docs

    def _get_provider_config(self, provider: str) -> tuple[str, str]:
        provider_key = provider.lower().strip()
        if provider_key == "anthropic":
            return settings.anthropic_api_key, "https://api.anthropic.com/v1/messages"
        if provider_key == "openai":
            return settings.openai_api_key, "https://api.openai.com/v1/chat/completions"
        if provider_key == "groq":
            return settings.groq_api_key, "https://api.groq.com/openai/v1/chat/completions"
        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _extract_text(self, result: Dict[str, Any]) -> str:
        if not result:
            return ""
        if "choices" in result and result["choices"]:
            first_choice = result["choices"][0]
            message = first_choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if text:
                            parts.append(text)
                return "\n".join(parts).strip()
            if isinstance(content, dict):
                text = content.get("text") or content.get("content")
                if text:
                    return str(text).strip()
            choice_text = first_choice.get("text")
            if isinstance(choice_text, str):
                return choice_text.strip()
        if "output_text" in result:
            return str(result["output_text"]).strip()
        if "output" in result and isinstance(result["output"], list):
            parts = []
            for item in result["output"]:
                if not isinstance(item, dict):
                    continue
                for content_item in item.get("content", []) or []:
                    if isinstance(content_item, dict):
                        text = content_item.get("text") or content_item.get("content")
                        if text:
                            parts.append(text)
                    elif isinstance(content_item, str):
                        parts.append(content_item)
            return "\n".join(parts).strip()
        return ""

    async def _call_llm(
        self,
        messages: List[Dict],
        max_tokens: int = 500,
        temperature: float = 0.7,
        model_config: Optional[ModelConfig] = None
    ) -> str:
        """
        Call an LLM using task-based model configuration.
        Retries on empty responses with adaptive JSON guidance when applicable.
        """
        def requires_json_format(msgs: List[Dict]) -> bool:
            for msg in msgs:
                content = str(msg.get("content", "")).lower()
                if "json" in content:
                    return True
                if "\"reply\"" in content or "'reply'" in content:
                    return True
                if "extracted_entities" in content or "still_needed" in content:
                    return True
            return False

        def with_system_instruction(msgs: List[Dict], instruction: str) -> List[Dict]:
            return [{"role": "system", "content": instruction}] + msgs

        async def perform_call(call_messages: List[Dict]) -> str:
            active_config = model_config or get_model_for_task("conversation")
            provider = active_config.provider.lower().strip()
            api_key, base_url = self._get_provider_config(provider)

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if provider == "anthropic":
                    system_content = ""
                    chat_messages = []

                    for msg in call_messages:
                        if msg["role"] == "system":
                            system_content = msg["content"]
                        else:
                            chat_messages.append({
                                "role": msg["role"],
                                "content": msg["content"]
                            })

                    response = await client.post(
                        base_url,
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": active_config.model,
                            "max_tokens": max_tokens,
                            "system": system_content,
                            "messages": chat_messages
                        }
                    )

                    if response.status_code == 200:
                        result = response.json()
                        return result["content"][0]["text"].strip()
                    print(f"Anthropic API error: {response.status_code} - {response.text}")
                    return ""

                model_name = active_config.model.lower()
                is_gpt5 = provider == "openai" and model_name.startswith("gpt-5")
                needs_max_completion_tokens = provider == "openai" and model_name.startswith(("gpt-5", "o3"))
                payload = {
                    "model": active_config.model,
                    "messages": call_messages
                }
                if not needs_max_completion_tokens:
                    payload["temperature"] = temperature
                if needs_max_completion_tokens:
                    payload["max_completion_tokens"] = max(max_tokens, 1500) if is_gpt5 else max_tokens
                else:
                    payload["max_tokens"] = max_tokens
                if is_gpt5:
                    payload["reasoning_effort"] = "low"

                response = await client.post(
                    base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )

                if response.status_code == 200:
                    result = response.json()
                    extracted = self._extract_text(result)
                    if extracted:
                        return extracted
                    print(f"LLM API error: Empty response content. Raw response: {str(result)[:1000]}")
                    return ""
                print(f"LLM API error: {response.status_code} - {response.text}")
                return ""

        try:
            json_required = requires_json_format(messages)
            attempts = 3
            for attempt in range(attempts):
                call_messages = messages
                if json_required and attempt == 1:
                    call_messages = with_system_instruction(
                        messages,
                        "Return ONLY valid JSON. Do not include prose, markdown, or code fences."
                    )
                elif json_required and attempt == 2:
                    call_messages = with_system_instruction(
                        messages,
                        "Return minimal valid JSON with keys: reply, extracted_entities, still_needed, awaiting_confirmation. reply must be a non-empty string."
                    )

                response_text = await perform_call(call_messages)
                if response_text:
                    return response_text
                if attempt < attempts - 1:
                    print(f"LLM retry: empty response (attempt {attempt + 1}/{attempts})")

            return ""
        except Exception as e:
            print(f"LLM API error: {str(e)}")
            return ""

    async def get_active_discussion(self, project_id: str, stage: str) -> Optional[Dict[str, Any]]:
        """Get the active discussion for a project stage"""
        collection = get_planner_discussion_collection(project_id)

        return await self._find_one_with_charter_fallback(
            collection,
            {
                "project_id": project_id,
                "stage": stage,
                "status": {"$in": ["active", "in_progress"]},
            },
        )

    async def ensure_initial_message(self, project_id: str, stage: str) -> str:
        """
        Ensure a single initial message exists for the active discussion.
        Returns the initial message (existing or newly generated).
        """
        collection = get_planner_discussion_collection(project_id)
        discussion = await self.get_active_discussion(project_id, stage)
        if not discussion:
            discussion = await self.create_discussion(project_id, stage)

        if discussion.get("initial_message_sent"):
            history = await self.get_chat_history(project_id, stage)
            if history:
                # Return the first assistant message if present
                for msg in history:
                    if msg.get("role") == "assistant" and msg.get("content"):
                        return msg["content"]
            return ""

        initial = await self.get_initial_message(project_id, stage)
        await collection.update_one(
            {"_id": discussion["_id"]},
            {"$set": {"initial_message_sent": True, "updated_at": datetime.utcnow()}}
        )
        return initial

    async def create_discussion(self, project_id: str, stage: str) -> Dict[str, Any]:
        """Create a new discussion document for a stage"""
        collection = get_planner_discussion_collection(project_id)
        
        # Get next session number
        last_discussion = await collection.find_one(
            {"project_id": project_id, "stage": stage, "source": self.charter_source},
            sort=[("session", -1)]
        )
        if not last_discussion:
            last_discussion = await collection.find_one(
                {"project_id": project_id, "stage": stage, **self._legacy_source_filter()},
                sort=[("session", -1)],
            )
        session = (last_discussion.get("session", 0) if last_discussion else 0) + 1
        
        discussion = {
            "project_id": project_id,
            "entity_type": "chat_history",
            "source": self.charter_source,
            "stage": stage,
            "session": session,
            "status": "active",
            "initial_message_sent": False,
            "messages": [],
            "summary": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await collection.insert_one(discussion)
        discussion["_id"] = result.inserted_id
        
        return discussion

    async def archive_discussion(self, project_id: str, stage: str) -> None:
        """Archive the current active discussion"""
        collection = get_planner_discussion_collection(project_id)
        
        await collection.update_many(
            {
                "project_id": project_id,
                "stage": stage,
                "status": {"$in": ["active", "in_progress"]},
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            },
            {"$set": {"status": "archived", "updated_at": datetime.utcnow()}}
        )

    async def reactivate_discussion(self, project_id: str, stage: str) -> bool:
        """Reactivate a finalized discussion to allow further editing"""
        collection = get_planner_discussion_collection(project_id)
        
        # Find the finalized discussion
        result = await collection.update_one(
            {
                "project_id": project_id,
                "stage": stage,
                "status": "finalized",
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            },
            {"$set": {"status": "in_progress", "updated_at": datetime.utcnow()}}
        )
        
        return result.modified_count > 0

    async def get_chat_history(self, project_id: str, stage: str) -> List[Dict[str, Any]]:
        """Get chat history for a specific stage"""
        discussion = await self.get_active_discussion(project_id, stage)

        if discussion and discussion.get("messages"):
            return discussion.get("messages", [])

        collection = get_planner_discussion_collection(project_id)
        discussion = await self._find_one_with_charter_fallback(
            collection,
            {
                "project_id": project_id,
                "stage": stage,
                "messages": {"$exists": True, "$ne": []},
            },
            sort=[("updated_at", -1), ("created_at", -1)],
        )

        if discussion:
            return discussion.get("messages", [])

        return []

    async def save_message(self, project_id: str, stage: str, role: str, content: str) -> None:
        """Save a chat message to the discussion document"""
        collection = get_planner_discussion_collection(project_id)
        
        discussion = await self.get_active_discussion(project_id, stage)
        
        if not discussion:
            discussion = await self.create_discussion(project_id, stage)
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        }
        
        await collection.update_one(
            {"_id": discussion["_id"]},
            {
                "$push": {"messages": message},
                "$set": {"updated_at": datetime.utcnow()}
            }
        )

    async def update_summary(self, project_id: str, stage: str, summary: str) -> None:
        """Update the discussion summary"""
        collection = get_planner_discussion_collection(project_id)
        
        await collection.update_one(
            {
                "project_id": project_id,
                "stage": stage,
                "status": {"$in": ["active", "in_progress"]},
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            },
            {"$set": {"summary": summary, "updated_at": datetime.utcnow(), "source": self.charter_source}}
        )

    async def generate_summary(self, project_id: str, stage: str) -> str:
        """Generate a one-line summary of the discussion using LLM"""
        messages = await self.get_chat_history(project_id, stage)
        
        if not messages or len(messages) < 2:
            return ""
        
        context = "\n".join([f"{m['role']}: {m['content']}" for m in messages[-10:]])
        
        summary = await self._call_llm(
            messages=[
                {"role": "system", "content": "Summarize this project planning discussion in ONE short sentence (max 15 words). Focus on what was discussed."},
                {"role": "user", "content": context}
            ],
            max_tokens=50,
            temperature=0.3,
            model_config=get_model_for_task("conversation")
        )
        
        if summary:
            await self.update_summary(project_id, stage, summary)
        
        return summary

    async def get_previous_context(self, project_id: str, stage: str) -> dict:
        """Get finalized content from previous stages for context"""
        context = {}
        stage_order = []
        try:
            topic_data = await self.get_project_topics(project_id)
            active_topics = topic_data.get("active_topics", [])
            stage_order = [
                t.get("topic_id")
                for t in sorted(active_topics, key=lambda x: x.get("display_order", 99))
                if t.get("topic_id")
            ]
        except Exception:
            stage_order = ["goal", "scope", "requirements", "features_tasks"]

        if stage not in stage_order:
            return context

        current_index = stage_order.index(stage)

        # Get content from all previous finalized stages
        for prev_stage in stage_order[:current_index]:
            content = await self.get_finalized_content(project_id, prev_stage)
            if content:
                context[prev_stage] = content

        return context

    async def extract_cross_stage_context(
        self,
        project_id: str,
        source_stage: str,
        target_stage: str
    ) -> str:
        """Extract target-stage-related mentions from source stage conversation"""

        # Get source stage conversation
        messages = await self.get_chat_history(project_id, source_stage)
        if not messages or len(messages) < 2:
            return ""

        # Build conversation context
        conversation = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages
        ])

        # Stage-specific extraction prompts
        extraction_prompts = {
            "scope": """Extract any mentions of:
- Timeline, deadlines, launch dates
- Budget, cost constraints
- Team size, solo vs team
- Resource constraints
- Technical constraints, platform limits

Return as natural sentence: "User mentioned [X, Y, Z]."
If nothing relevant, return empty string.""",

            "requirements": """Extract any mentions of:
- Specific features or capabilities
- Integrations (Slack, Jira, etc)
- User flows (login, signup, etc)
- Technical requirements (API, database, etc)
- Performance needs, security needs

Return as natural sentence: "User mentioned [X, Y, Z]."
If nothing relevant, return empty string.""",

            "features_tasks": """Extract any mentions of:
- User-facing features or screens
- Specific capabilities users need
- Workflows or user actions
- Dashboard, reports, analytics
- Integrations or third-party tools

Return as natural sentence: "User mentioned [X, Y, Z]."
If nothing relevant, return empty string."""
        }

        prompt = extraction_prompts.get(target_stage, "")
        if not prompt:
            return ""

        result = await self._call_llm(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Conversation:\n{conversation}"}
            ],
            max_tokens=150,
            temperature=0.3,
            model_config=get_model_for_task("inference")
        )

        return result.strip()

    async def extract_info_for_next_stage(
        self,
        project_id: str,
        source_stage: str,
        target_stage: str
    ) -> str:
        """
        Extract structured information from source stage for target stage.
        Returns formatted bullets/sections to show user at start of target stage.
        """

        # Get source stage conversation
        messages = await self.get_chat_history(project_id, source_stage)
        if not messages or len(messages) < 2:
            return ""

        # Build conversation context
        conversation = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in messages
        ])

        # Stage-specific extraction prompts that return structured bullets
        extraction_prompts = {
            "scope": """Extract scope-related information from this Goal conversation:
- Timeline and milestones
- Budget and cost constraints
- Team size and skills
- Resource constraints
- Technical constraints
- Non-goals for first release
- Must-have features
- Trade-offs mentioned

Format as bullets:
TIMELINE:
• [extracted timeline info]

CONSTRAINTS:
• [extracted constraints]

NON-GOALS:
• [what's out of scope]

MUST-HAVES:
• [must-have features]

If nothing found for a section, omit that section.
Return formatted bullets only, no other text.""",

            "requirements": """Extract requirements-related information from this Scope conversation:
- Build choices (build vs buy decisions)
- Third-party integrations and services
- Technical stack preferences
- Authentication methods
- Data storage needs
- Scale and performance expectations
- Security and compliance needs
- Key technical decisions

Format as bullets:
BUILD CHOICES:
• [what to build vs use as service]

INTEGRATIONS:
• [third-party services mentioned]

TECHNICAL STACK:
• [languages, frameworks, databases]

AUTHENTICATION:
• [auth methods]

DATA:
• [data model, storage]

SCALE/PERFORMANCE:
• [performance expectations]

If nothing found for a section, omit that section.
Return formatted bullets only, no other text.""",

            "features_tasks": """Extract features-related information from this Requirements conversation:
- User-facing features mentioned
- Key user flows
- Feature priorities
- Feature dependencies
- UI/UX requirements

Format as bullets:
FEATURES:
• [user-facing features]

USER FLOWS:
• [key user journeys]

PRIORITIES:
• [must-have vs nice-to-have]

DEPENDENCIES:
• [feature dependencies]

If nothing found for a section, omit that section.
Return formatted bullets only, no other text."""
        }

        prompt = extraction_prompts.get(target_stage, "")
        if not prompt:
            return ""

        result = await self._call_llm(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Conversation:\n{conversation}"}
            ],
            max_tokens=500,
            temperature=0.3,
            model_config=get_model_for_task("inference")
        )

        return result.strip()

    async def _get_cross_stage_context_for_stage(self, project_id: str, stage: str) -> str:
        """Helper to get cross-stage context based on current stage"""
        from app.planner.topic_registry import get_topic_dependencies

        deps = get_topic_dependencies(stage)
        if not deps:
            return ""

        for dep in deps:
            context = await self.extract_cross_stage_context(project_id, dep, stage)
            if context:
                return context
        return ""

    def _normalize_entity_type(self, stage: str) -> str:
        if stage == "requirements":
            return "requirement"
        if stage == "features_tasks":
            return "feature"
        return stage

    async def _get_current_focus_topic(
        self,
        project_id: str,
        project_type: str = "Software / Product Development"
    ) -> str:
        """
        Determine which topic to focus on based on extraction progress.
        Uses registry display_order for priority, returns first unfinalized default topic.
        
        This enables progressive topic loading - only one topic prompt is loaded at a time.
        """
        ordered_topics = get_ordered_default_topics(project_type)
        
        if not ordered_topics:
            return "goal"  # Fallback
        
        for topic in ordered_topics:
            topic_id = topic["id"]
            stage_data = await self.get_stage_entities(project_id, topic_id)
            if stage_data.get("status") != "finalized":
                return topic_id  # First unfinalized = current focus
        
        # All done, stay on last topic
        return ordered_topics[-1]["id"]


    async def chat(self, project_id: str, user_message: str, stage: str) -> str:
        """Send a message and get PM Agent response"""
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        project_name = project.get("project_name", "Project") if project else "Project"
        project_type = project.get("project_type", "Software / Product Development") if project else "Software / Product Development"

        await self.save_message(project_id, stage, "user", user_message)

        history = await self.get_chat_history(project_id, stage)

        # Get context from previous stages
        context = await self.get_previous_context(project_id, stage)

        # Determine focus topic based on extraction progress
        focus_topic = await self._get_current_focus_topic(project_id, project_type)

        # Build modular prompt: base_persona + project_type + topic + mode
        system_prompt = build_prompt(
            topic_id=focus_topic,  # Dynamic focus, not just stage
            project_type=project_type,
            mode="brainstorm",
            context={"previous_topics": context, "project_name": project_name},
            include_knowledge_base=True
        )
        
        messages = [
            {"role": "system", "content": system_prompt}
        ]

        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        assistant_message = await self._call_llm(
            messages,
            max_tokens=1000,
            temperature=0.7,
            model_config=get_model_for_task("conversation")
        )

        if not assistant_message:
            assistant_message = "I apologize, I'm having trouble processing that. Could you please rephrase?"

        await self.save_message(project_id, stage, "assistant", assistant_message)

        # Generate summary periodically
        history = await self.get_chat_history(project_id, stage)
        if len(history) % 4 == 0:
            await self.generate_summary(project_id, stage)

        return assistant_message


    async def get_initial_message(self, project_id: str, stage: str) -> str:
        """Get initial PM Agent message for a stage"""
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        project_name = project.get("project_name", "Project") if project else "Project"
        history = await self.get_chat_history(project_id, stage)

        if history:
            return ""

        stage_label = stage.replace("_", " ")
        initial = f"Hey, tell me about your {stage_label} for {project_name}."

        await self.save_message(project_id, stage, "assistant", initial)

        return initial

    async def start_fresh(self, project_id: str, stage: str) -> Dict[str, Any]:
        """Archive current discussion and start fresh"""
        await self.archive_discussion(project_id, stage)

        discussion = await self.create_discussion(project_id, stage)

        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        project_name = project.get("project_name", "Project") if project else "Project"

        # Get context from previous stages
        context = await self.get_previous_context(project_id, stage)

        # Get extracted structured info from previous stage conversation (core-only)
        extracted_info = ""
        if stage == "scope":
            extracted_info = await self.extract_info_for_next_stage(project_id, "goal", "scope")
            context["extracted_scope_from_goal"] = extracted_info

        initial = get_initial_message(stage, project_name, context)
        await self.save_message(project_id, stage, "assistant", initial)

        return {
            "session": discussion["session"],
            "initial_message": initial
        }

    async def reset_charter(self, project_id: str) -> Dict[str, Any]:
        """
        Reset entire charter - clears all discussions, entities, and extracted content.
        Used when user clicks "Start Fresh" button.
        
        Deletes:
        - All planner discussions for this project
        - All brain entities for this project
        - All planner_data for this project
        """
        db = get_database()
        
        # 1. Delete all planner discussions
        collection = get_planner_discussion_collection(project_id)
        discussions_result = await collection.delete_many({"project_id": project_id})
        
        # 2. Delete brain entities EXCEPT the project entity (created at project creation)
        brain_collection = get_brain_collection(project_id)

        # Keep the project entity even if older docs are missing entity_type.
        project_doc = await brain_collection.find_one({
            "$or": [
                {"entity_type": "project"},
                {"integration_type": "project"}
            ]
        })

        if not project_doc:
            project = await db.projects.find_one({"project_id": project_id})
            project_name = project.get("project_name", "Project") if project else "Project"
            insert_result = await brain_collection.insert_one({
                "project_id": project_id,
                "project_name": project_name,
                "entity_type": "project",
                "created_at": datetime.utcnow()
            })
            project_doc = {"_id": insert_result.inserted_id}

        # Only delete charter-related entities, keep Jira/Slack synced tasks
        if project_doc and project_doc.get("_id"):
            brain_result = await brain_collection.delete_many({
                "_id": {"$ne": project_doc["_id"]},
                "integration_type": {"$exists": False}  # Only delete non-integration entities
            })
        else:
            brain_result = await brain_collection.delete_many({
                "integration_type": {"$exists": False}
            })
        
        # 3. Delete planner_data
        planner_result = await db.planner_data.delete_many({"project_id": project_id})

        # 4. Clear charter mode on brain project entity
        await brain_collection.update_one(
            {"entity_type": "project"},
            {"$set": {"charter_mode": None}}
        )
        
        return {
            "discussions_deleted": discussions_result.deleted_count,
            "entities_deleted": brain_result.deleted_count,
            "planner_data_deleted": planner_result.deleted_count
        }


    async def extract_stage_content(self, project_id: str, stage: str) -> List[str]:
        """
        Return current draft items for a stage from stored entities.
        """
        stage_data = await self.get_stage_entities(project_id, stage)
        return stage_data.get("items", [])

    async def save_entity_draft(
        self,
        project_id: str,
        entity_type: str,
        items: List[str],
        out_of_scope: Optional[List[str]] = None
    ) -> None:
        brain_collection = get_brain_collection(project_id)
        now = datetime.utcnow()
        normalized = self._normalize_entity_type(entity_type)
        storage_mode = get_storage_mode(normalized)

        if storage_mode == "holistic":
            content = items[0].strip() if items else ""
            await brain_collection.update_one(
                {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                {"$set": {"entity_type": normalized, "source": self.charter_source, "content": content, "status": "draft", "updated_at": now}},
                upsert=True
            )
            return

        if storage_mode == "embedded":
            if normalized == "scope":
                scope_items = [{"id": f"s_{i+1}", "text": item} for i, item in enumerate(items or []) if item]
                out_items = [{"id": f"os_{i+1}", "text": item} for i, item in enumerate(out_of_scope or []) if item]
                await brain_collection.update_one(
                    {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                    {"$set": {
                        "entity_type": normalized,
                        "source": self.charter_source,
                        "items": scope_items,
                        "out_of_scope_items": out_items,
                        "status": "draft",
                        "updated_at": now
                    }},
                    upsert=True
                )
                return

            embedded_items = [{"id": f"{normalized}_{i+1}", "text": item} for i, item in enumerate(items or []) if item]
            await brain_collection.update_one(
                {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                {"$set": {
                    "entity_type": normalized,
                    "source": self.charter_source,
                    "items": embedded_items,
                    "status": "draft",
                    "updated_at": now
                }},
                upsert=True
            )
            return

        # collection mode: one doc per item
        await brain_collection.delete_many(
            {
                "entity_type": normalized,
                "status": "draft",
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            }
        )
        docs = [
            {
                "entity_type": normalized,
                "source": self.charter_source,
                "text": item,
                "status": "draft",
                "created_at": now,
                "updated_at": now,
            }
            for item in (items or [])
            if item
        ]
        if docs:
            await brain_collection.insert_many(docs)

    async def save_entity_final(
        self,
        project_id: str,
        entity_type: str,
        items: List[str],
        out_of_scope: Optional[List[str]] = None
    ) -> None:
        brain_collection = get_brain_collection(project_id)
        now = datetime.utcnow()
        normalized = self._normalize_entity_type(entity_type)
        storage_mode = get_storage_mode(normalized)

        if storage_mode == "holistic":
            content = items[0].strip() if items else ""
            await brain_collection.update_one(
                {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                {"$set": {"entity_type": normalized, "source": self.charter_source, "content": content, "status": "finalized", "updated_at": now}},
                upsert=True
            )
            return

        if storage_mode == "embedded":
            if normalized == "scope":
                scope_items = [{"id": f"s_{i+1}", "text": item} for i, item in enumerate(items or []) if item]
                out_items = [{"id": f"os_{i+1}", "text": item} for i, item in enumerate(out_of_scope or []) if item]
                await brain_collection.update_one(
                    {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                    {"$set": {
                        "entity_type": normalized,
                        "source": self.charter_source,
                        "items": scope_items,
                        "out_of_scope_items": out_items,
                        "status": "finalized",
                        "updated_at": now
                    }},
                    upsert=True
                )
                return

            embedded_items = [{"id": f"{normalized}_{i+1}", "text": item} for i, item in enumerate(items or []) if item]
            await brain_collection.update_one(
                {"entity_type": normalized, "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}]},
                {"$set": {
                    "entity_type": normalized,
                    "source": self.charter_source,
                    "items": embedded_items,
                    "status": "finalized",
                    "updated_at": now
                }},
                upsert=True
            )
            return

        # collection mode: replace finalized docs
        delete_filter = {
            "entity_type": normalized,
            "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
        }
        await brain_collection.delete_many({**delete_filter, "status": "finalized"})
        await brain_collection.delete_many({**delete_filter, "status": "draft"})
        docs = [
            {
                "entity_type": normalized,
                "source": self.charter_source,
                "text": item,
                "status": "finalized",
                "created_at": now,
                "updated_at": now,
            }
            for item in (items or [])
            if item
        ]
        if docs:
            await brain_collection.insert_many(docs)

    async def finalize_stage(
        self,
        project_id: str,
        stage: str,
        content: List[str],
        out_of_scope: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Finalize a planning stage with the provided content"""
        collection = get_planner_discussion_collection(project_id)

        await collection.update_one(
            {
                "project_id": project_id,
                "stage": stage,
                "status": {"$in": ["active", "in_progress"]},
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            },
            {"$set": {"status": "finalized", "updated_at": datetime.utcnow(), "source": self.charter_source}}
        )

        await self.save_entity_final(project_id, stage, content, out_of_scope)
        
        stage_order = []
        try:
            topic_data = await self.get_project_topics(project_id)
            active_topics = topic_data.get("active_topics", [])
            stage_order = [
                t.get("topic_id")
                for t in sorted(active_topics, key=lambda x: x.get("display_order", 99))
                if t.get("topic_id")
            ]
        except Exception:
            stage_order = ["goal", "scope", "requirements", "features_tasks"]

        if stage in stage_order:
            current_index = stage_order.index(stage)
            next_stage = stage_order[current_index + 1] if current_index < (len(stage_order) - 1) else None
        else:
            next_stage = None
        
        return {
            "stage": stage,
            "status": "finalized",
            "next_stage": next_stage
        }

    async def _clear_brain_entities(self, project_id: str, stage: str) -> None:
        """Remove brain entities for a stage (used when finalized content is cleared)."""
        brain_collection = get_brain_collection(project_id)
        normalized = self._normalize_entity_type(stage)
        await brain_collection.delete_many({"entity_type": normalized})

    async def get_stage_entities(self, project_id: str, stage: str) -> Dict[str, Any]:
        """Get entity items and status for a stage from brain collection."""
        brain_collection = get_brain_collection(project_id)
        normalized = self._normalize_entity_type(stage)
        storage_mode = get_storage_mode(normalized)
        source_filter = self._charter_source_filter()

        if storage_mode == "holistic":
            doc = await brain_collection.find_one({"entity_type": normalized, **source_filter})
            if not doc:
                doc = await brain_collection.find_one({"entity_type": normalized, **self._legacy_source_filter()})
            content = doc.get("content") if doc else None
            if content:
                return {"items": [content], "status": doc.get("status", "draft")}
            return {"items": [], "status": "pending"}

        if storage_mode == "embedded":
            doc = await brain_collection.find_one({"entity_type": normalized, **source_filter})
            if not doc:
                doc = await brain_collection.find_one({"entity_type": normalized, **self._legacy_source_filter()})
            items = []
            out_of_scope_items = []
            if doc and doc.get("items"):
                for item in doc.get("items", []):
                    text = item.get("text") if isinstance(item, dict) else item
                    if text:
                        items.append(text)
            if normalized == "scope" and doc and doc.get("out_of_scope_items"):
                for item in doc.get("out_of_scope_items", []):
                    text = item.get("text") if isinstance(item, dict) else item
                    if text:
                        out_of_scope_items.append(text)
            has_any = bool(items or out_of_scope_items)
            status = doc.get("status", "draft") if doc and has_any else "pending"
            return {"items": items, "out_of_scope": out_of_scope_items, "status": status}

        # collection mode
        items = []
        has_draft = False
        has_final = False
        draft_docs = await self._find_many_with_charter_fallback(
            brain_collection,
            {"entity_type": normalized, "status": "draft"},
            sort_field="_id",
            sort_dir=1,
        )
        for doc in draft_docs:
            text = doc.get("text")
            if text:
                items.append(text)
                has_draft = True
        if not items:
            final_docs = await self._find_many_with_charter_fallback(
                brain_collection,
                {"entity_type": normalized, "status": "finalized"},
                sort_field="_id",
                sort_dir=1,
            )
            for doc in final_docs:
                text = doc.get("text")
                if text:
                    items.append(text)
                    has_final = True
        status = "finalized" if has_final and not has_draft else ("draft" if has_draft else "pending")
        return {"items": items, "status": status}

    async def get_finalized_content(self, project_id: str, stage: str) -> Optional[List[str]]:
        """Get finalized content for a stage from brain entities."""
        data = await self.get_stage_entities(project_id, stage)
        if data.get("status") == "finalized":
            return data.get("items", [])
        return None

    async def get_discussion_summary(self, project_id: str, stage: str) -> Optional[str]:
        """Get the summary of active discussion"""
        discussion = await self.get_active_discussion(project_id, stage)
        
        if discussion:
            return discussion.get("summary")
        
        return None

    async def get_planner_status(self, project_id: str) -> Dict[str, Any]:
        """Get overall planner status for a project"""
        stages_status = {}
        finalized_content = {}
        summaries = {}
        stage_order: List[str] = []
        active_topics: List[Dict[str, Any]] = []
        try:
            topic_data = await self.get_project_topics(project_id)
            active_topics = topic_data.get("active_topics", [])
            stage_order = [
                t.get("topic_id")
                for t in sorted(active_topics, key=lambda x: x.get("display_order", 99))
                if t.get("topic_id")
            ]
        except Exception:
            stage_order = ["goal", "scope", "requirements", "features_tasks"]

        topic_by_id = {
            str(topic.get("topic_id")): topic
            for topic in active_topics
            if topic.get("topic_id")
        }
        for stage in stage_order:
            topic = topic_by_id.get(stage, {})
            topic_status = str(topic.get("status") or "").strip().lower()
            topic_items = topic.get("finalized_content") if isinstance(topic.get("finalized_content"), list) else []

            if topic_status == "finalized":
                stages_status[stage] = "finalized"
                finalized_content[stage] = topic_items
            elif topic_status in {"in_progress", "draft", "active"}:
                stages_status[stage] = "in_progress"
            else:
                stages_status[stage] = "pending"

            summary = topic.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries[stage] = summary.strip()

        current_stage = stage_order[0] if stage_order else "goal"
        for stage in stage_order:
            if stages_status.get(stage) != "finalized":
                current_stage = stage
                break
        
        return {
            "current_stage": current_stage,
            "stages": stages_status,
            "finalized_content": finalized_content,
            "summaries": summaries
        }

    async def get_current_stage(self, project_id: str) -> Dict[str, Any]:
        """Get only the current stage without computing full status."""
        stage_order: List[str] = []
        active_topics: List[Dict[str, Any]] = []
        try:
            topic_data = await self.get_project_topics(project_id)
            active_topics = topic_data.get("active_topics", [])
            stage_order = [
                t.get("topic_id")
                for t in sorted(active_topics, key=lambda x: x.get("display_order", 99))
                if t.get("topic_id")
            ]
        except Exception:
            stage_order = ["goal", "scope", "requirements", "features_tasks"]

        topic_by_id = {
            str(topic.get("topic_id")): topic
            for topic in active_topics
            if topic.get("topic_id")
        }
        current_stage = stage_order[0] if stage_order else "goal"
        for stage in stage_order:
            topic_status = str(topic_by_id.get(stage, {}).get("status") or "").strip().lower()
            if topic_status != "finalized":
                current_stage = stage
                break

        return {"current_stage": current_stage}

    # ==================== REVISION METHODS ====================

    async def get_active_revision(self, project_id: str, stage: str) -> Optional[Dict[str, Any]]:
        """Get the active revision session for a project stage"""
        collection = get_planner_discussion_collection(project_id)

        return await self._find_one_with_charter_fallback(
            collection,
            {
                "project_id": project_id,
                "stage": stage,
                "status": "revision",
            },
        )

    async def start_revision_session(self, project_id: str, stage: str) -> Dict[str, Any]:
        """
        Start a fresh revision session
        Returns initial message listing finalized points
        """
        from app.planner.prompt_templates import load_prompt
        
        # Get current finalized content
        finalized_content = await self.get_finalized_content(project_id, stage)
        
        if not finalized_content:
            raise ValueError("No finalized content to revise")
        
        # Archive any existing revision sessions
        collection = get_planner_discussion_collection(project_id)
        await collection.update_many(
            {
                "project_id": project_id,
                "stage": stage,
                "status": "revision",
                "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
            },
            {"$set": {"status": "archived", "updated_at": datetime.utcnow()}}
        )
        
        # Create new revision session
        revision_session = {
            "project_id": project_id,
            "stage": stage,
            "source": self.charter_source,
            "status": "revision",
            "revision_context": {
                "finalized_content": finalized_content,
                "point_index": None,  # Will be set when user selects a point
                "original_point": None
            },
            "messages": [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await collection.insert_one(revision_session)
        
        label = {
            "goal": "goals",
            "scope": "scope items"
        }.get(stage, stage.replace("_", " "))
        points_list = "\n".join([f"{i+1}. {point}" for i, point in enumerate(finalized_content)])
        template = load_prompt("modes/revision_initial.md")
        initial_message = template.format(
            label=label,
            points_list=points_list
        )
        
        # Save initial message
        await collection.update_one(
            {"_id": result.inserted_id},
            {
                "$push": {
                    "messages": {
                        "role": "assistant",
                        "content": initial_message,
                        "timestamp": datetime.utcnow()
                    }
                }
            }
        )
        
        return {
            "initial_message": initial_message,
            "finalized_content": finalized_content
        }

    async def revision_chat(self, project_id: str, stage: str, user_message: str, point_index: Optional[int] = None) -> str:
        """
        Handle chat in revision mode
        If point_index is provided, focus conversation on that point
        """
        from app.planner.prompt_templates import load_prompt
        
        collection = get_planner_discussion_collection(project_id)
        revision = await self.get_active_revision(project_id, stage)
        
        if not revision:
            raise ValueError("No active revision session")
        
        # Save user message
        await collection.update_one(
            {"_id": revision["_id"]},
            {
                "$push": {
                    "messages": {
                        "role": "user",
                        "content": user_message,
                        "timestamp": datetime.utcnow()
                    }
                },
                "$set": {"updated_at": datetime.utcnow()}
            }
        )
        
        # Get updated revision
        revision = await self.get_active_revision(project_id, stage)
        finalized_content = revision["revision_context"]["finalized_content"]
        
        # If point_index provided and valid, update context and use focused prompt
        if point_index is not None and 0 <= point_index < len(finalized_content):
            await collection.update_one(
                {"_id": revision["_id"]},
                {
                    "$set": {
                        "revision_context.point_index": point_index,
                        "revision_context.original_point": finalized_content[point_index]
                    }
                }
            )
            
            # Use revision-focused system prompt
            label = {
                "goal": "goal",
                "scope": "scope item"
            }.get(stage, stage.replace("_", " "))
            other_points = "\n".join([f"#{i+1}: {p}" for i, p in enumerate(finalized_content) if i != point_index])
            template = load_prompt("modes/revision_system.md")
            system_prompt = template.format(
                label=label,
                point_index=point_index + 1,
                original_point=finalized_content[point_index],
                other_points=other_points
            )
        else:
            # Check if point_index is already set from previous messages
            current_point_index = revision["revision_context"].get("point_index")
            if current_point_index is not None:
                # Continue with the already selected point
                label = {
                    "goal": "goal",
                    "scope": "scope item"
                }.get(stage, stage.replace("_", " "))
                other_points = "\n".join([f"#{i+1}: {p}" for i, p in enumerate(finalized_content) if i != current_point_index])
                template = load_prompt("modes/revision_system.md")
                system_prompt = template.format(
                    label=label,
                    point_index=current_point_index + 1,
                    original_point=finalized_content[current_point_index],
                    other_points=other_points
                )
            else:
                # Still asking which point to revise
                template = load_prompt("modes/revision_initial.md")
                label = stage.replace("_", " ")
                points_list = "\n".join([f"{i+1}. {point}" for i, point in enumerate(finalized_content)])
                system_prompt = template.format(
                    label=label,
                    points_list=points_list
                )
        
        # Build messages for LLM
        messages = [{"role": "system", "content": system_prompt}]
        
        for msg in revision["messages"]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Call LLM
        assistant_message = await self._call_llm(
            messages,
            max_tokens=400,
            temperature=0.7,
            model_config=get_model_for_task("conversation")
        )
        
        if not assistant_message:
            assistant_message = "I apologize, I'm having trouble processing that. Could you please rephrase?"
        
        # Save assistant message
        await collection.update_one(
            {"_id": revision["_id"]},
            {
                "$push": {
                    "messages": {
                        "role": "assistant",
                        "content": assistant_message,
                        "timestamp": datetime.utcnow()
                    }
                }
            }
        )
        
        return assistant_message

    async def extract_revised_point(self, project_id: str, stage: str) -> str:
        """
        Extract the revised point from the revision conversation
        """
        from app.planner.prompt_templates import load_prompt
        
        revision = await self.get_active_revision(project_id, stage)
        
        if not revision:
            raise ValueError("No active revision session")
        
        # Check if a point has been selected
        point_index = revision["revision_context"].get("point_index")
        if point_index is None:
            raise ValueError("No point selected for revision. Please mention which point number you want to revise.")
        
        messages = revision.get("messages", [])
        
        if len(messages) < 3:
            raise ValueError("Not enough conversation to extract revised point")
        
        # Build chat context
        chat_context = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])
        
        # Extract revised point
        label = stage.replace("_", " ")
        template = load_prompt("modes/revision_extract.md")
        extraction_prompt = template.format(
            label_upper=label.upper(),
            label_lower=label.lower()
        )
        
        result = await self._call_llm(
            messages=[
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": f"Conversation:\n{chat_context}"}
            ],
            max_tokens=200,
            temperature=0.3,
            model_config=get_model_for_task("extraction")
        )
        
        if not result:
            raise ValueError("Could not extract revised point")
        
        # Clean up the result - remove any list formatting
        revised_point = result.strip()
        
        # If the result contains multiple lines (numbered list), take only the first line
        lines = revised_point.split('\n')
        if len(lines) > 1:
            # Look for the line that matches the point_index
            for line in lines:
                # Remove numbered prefix
                cleaned = re.sub(r'^\d+\.\s*', '', line.strip())
                if cleaned and not cleaned.startswith('#'):  # Skip empty lines and comments
                    revised_point = cleaned
                    break
        
        # Remove numbered list prefixes (1. 2. etc.)
        import re
        revised_point = re.sub(r'^\d+\.\s*', '', revised_point)
        
        # Remove bullet points
        revised_point = revised_point.lstrip('•-*').strip()
        
        # If still multiple lines, take just the first non-empty line
        if '\n' in revised_point:
            revised_point = revised_point.split('\n')[0].strip()
        
        return revised_point

    async def update_finalized_point(self, project_id: str, stage: str, point_index: int, new_content: str) -> Dict[str, Any]:
        """
        Update a specific point in the finalized content
        """
        finalized_content = await self.get_finalized_content(project_id, stage)
        if not finalized_content:
            raise ValueError("No finalized content found")
        
        if point_index < 0 or point_index >= len(finalized_content):
            raise ValueError("Invalid point index")
        
        # Update the specific point
        finalized_content[point_index] = new_content

        await self.save_entity_final(project_id, stage, finalized_content)
        
        # Archive the revision session
        collection = get_planner_discussion_collection(project_id)
        await collection.update_many(
            {
                "project_id": project_id,
                "stage": stage,
                "status": "revision"
            },
            {"$set": {"status": "archived", "updated_at": datetime.utcnow()}}
        )
        
        return {
            "success": True,
            "updated_content": finalized_content
        }

    async def add_point_to_finalized(self, project_id: str, stage: str, new_point: str) -> Dict[str, Any]:
        """
        Add a new point to the finalized content
        """
        finalized_content = await self.get_finalized_content(project_id, stage)
        if not finalized_content:
            raise ValueError("No finalized content found")
        
        # Append the new point
        finalized_content.append(new_point)
        await self.save_entity_final(project_id, stage, finalized_content)
        
        return {
            "success": True,
            "updated_content": finalized_content
        }

    async def delete_point_from_finalized(self, project_id: str, stage: str, point_index: int) -> Dict[str, Any]:
        """Delete a point from the finalized content by index"""
        finalized_content = await self.get_finalized_content(project_id, stage)
        if not finalized_content:
            raise ValueError("No finalized content found")

        print(f"🗑️ Backend delete: stage={stage}, point_index={point_index}, list_length={len(finalized_content)}")
        print(f"📋 Current list: {finalized_content}")

        if point_index < 0 or point_index >= len(finalized_content):
            print(f"❌ Invalid index! point_index={point_index}, len={len(finalized_content)}")
            raise ValueError(f"Invalid point index: {point_index} (list has {len(finalized_content)} items)")

        # Remove the point
        finalized_content.pop(point_index)

        # If all points deleted, delete the finalized discussion entirely
        # This will reset the stage to show welcome screen
        if len(finalized_content) == 0:
            await self._clear_brain_entities(project_id, stage)
        else:
            await self.save_entity_final(project_id, stage, finalized_content)

        return {
            "success": True,
            "updated_content": finalized_content
        }

    async def generate_tasks_for_feature(self, project_id: str, feature: str, stage: str) -> List[str]:
        """Auto-generate tasks for a specific feature using AI"""
        # Get requirements context
        context = await self.get_previous_context(project_id, stage)
        requirements = context.get("requirements", [])

        prompt = f"""You are a project manager breaking down a feature into actionable tasks.

Feature: {feature}

Requirements context:
{chr(10).join([f"• {r}" for r in requirements]) if requirements else "None"}

Generate 3-5 specific, actionable tasks to implement this feature. Each task should:
- Be clear and specific
- Include size estimate (Size: S/M/L)
- Be achievable in 1-3 days

Format: Just list the tasks, one per line, like:
• Setup authentication flow (Size: M)
• Create login UI component (Size: S)

Return only the task list, nothing else."""

        response = await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500,
            model_config=get_model_for_task("conversation")
        )

        # Parse tasks from response
        tasks = []
        for line in response.strip().split('\n'):
            line = line.strip()
            if line and (line.startswith('•') or line.startswith('-') or line.startswith('*')):
                task = line.lstrip('•-* ').strip()
                if task:
                    tasks.append(task)

        return tasks if tasks else [f"Implement {feature} (Size: M)"]

    async def task_brainstorm_chat(self, project_id: str, feature: str, message: str, existing_tasks: List[str]) -> str:
        """Chat with user about task breakdown for a feature"""
        # Get requirements context
        context = await self.get_previous_context(project_id, "features_tasks")
        requirements = context.get("requirements", [])

        system_prompt = f"""You are a project manager helping break down a feature into tasks.

Feature: {feature}

Requirements context:
{chr(10).join([f"• {r}" for r in requirements]) if requirements else "None"}

{"Current tasks:" if existing_tasks else "No tasks yet."}
{chr(10).join([f"{i+1}. {t}" for i, t in enumerate(existing_tasks)]) if existing_tasks else ""}

Help the user refine the task breakdown. Be conversational and ask clarifying questions.
Each task should be achievable in 1-3 days and include a size estimate (S/M/L)."""

        response = await self._call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            temperature=0.7,
            max_tokens=500,
            model_config=get_model_for_task("conversation")
        )

        return response

    async def extract_tasks_from_chat(self, project_id: str, feature: str, messages: List[Dict]) -> List[str]:
        """Extract final task list from brainstorm conversation"""
        # Get last few messages for context
        conversation = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in messages[-6:]  # Last 6 messages
        ])

        prompt = f"""Based on this conversation about breaking down "{feature}", extract the final task list.

Conversation:
{conversation}

Return ONLY the task list, one per line, with size estimates. Format:
• Task description (Size: S/M/L)
• Another task (Size: S/M/L)

Return only the task list, nothing else."""

        response = await self._call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500,
            model_config=get_model_for_task("extraction")
        )

        # Parse tasks from response
        tasks = []
        for line in response.strip().split('\n'):
            line = line.strip()
            if line and (line.startswith('•') or line.startswith('-') or line.startswith('*')):
                task = line.lstrip('•-* ').strip()
                if task:
                    tasks.append(task)

        return tasks if tasks else [f"Implement {feature} (Size: M)"]

    async def upload_documents(self, project_id: str, files: List[UploadFile]) -> List[Dict[str, Any]]:
        """
        Upload and store project documents
        Returns list of uploaded document metadata
        """
        db = get_database()
        uploaded_docs = []

        # Create uploads directory if it doesn't exist
        upload_dir = Path("uploads") / project_id
        upload_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            # Validate file type
            filename = file.filename.lower()
            if not any(filename.endswith(ext) for ext in ['.docx', '.doc', '.pdf', '.txt', '.md', '.markdown']):
                raise ValueError(f"Unsupported file type: {file.filename}")

            # Generate unique file ID
            file_id = str(ObjectId())
            file_ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'txt'
            stored_filename = f"{file_id}.{file_ext}"
            file_path = upload_dir / stored_filename

            # Save file
            content = await file.read()
            with open(file_path, 'wb') as f:
                f.write(content)

            # Store metadata
            doc_metadata = {
                "_id": ObjectId(file_id),
                "project_id": project_id,
                "filename": file.filename,
                "stored_filename": stored_filename,
                "file_type": file_ext,
                "file_path": str(file_path),
                "file_size": len(content),
                "upload_date": datetime.utcnow(),
                "extracted": False
            }

            await db.project_documents.insert_one(doc_metadata)

            uploaded_docs.append({
                "file_id": file_id,
                "filename": file.filename,
                "file_type": file_ext,
                "upload_date": doc_metadata["upload_date"],
                "extracted": False
            })

        return uploaded_docs

    async def get_uploaded_documents(self, project_id: str) -> List[Dict[str, Any]]:
        """Get list of uploaded documents for a project"""
        db = get_database()
        cursor = db.project_documents.find({"project_id": project_id})

        documents = []
        async for doc in cursor:
            documents.append({
                "file_id": str(doc["_id"]),
                "filename": doc["filename"],
                "file_type": doc["file_type"],
                "upload_date": doc["upload_date"],
                "extracted": doc.get("extracted", False)
            })

        return documents

    async def extract_from_documents(self, project_id: str, file_ids: List[str]) -> Dict[str, List[str]]:
        """
        Extract project charter content from uploaded documents
        Returns extracted content organized by stage
        """
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        project_type = (
            project.get("project_type")
            if project and project.get("project_type")
            else project.get("type", "Software / Product Development") if project else "Software / Product Development"
        )

        topic_data = await self.get_project_topics(project_id)
        active_topics = topic_data.get("active_topics", [])
        ordered_topics = sorted(active_topics, key=lambda x: x.get("display_order", 99))
        topic_ids = [t.get("topic_id") for t in ordered_topics if t.get("topic_id")]
        topic_names = {t.get("topic_id"): t.get("name") for t in ordered_topics if t.get("topic_id")}
        if not topic_ids:
            topic_ids = ["goal", "scope"]

        # Retrieve documents
        documents = []
        for file_id in file_ids:
            doc = await db.project_documents.find_one({"_id": ObjectId(file_id)})
            if not doc:
                raise ValueError(f"Document not found: {file_id}")
            documents.append(doc)

        # Extract text from all documents
        all_text = []
        for doc in documents:
            file_path = doc["file_path"]
            filename = doc["filename"]

            try:
                with open(file_path, 'rb') as f:
                    text = DocumentProcessor.extract_text(f, filename)
                    all_text.append(f"=== From {filename} ===\n{text}")
            except Exception as e:
                print(f"Error extracting text from {filename}: {str(e)}")
                continue

        if not all_text:
            raise ValueError("Could not extract text from any documents")

        combined_text = "\n\n".join(all_text)

        base_topic = "goal" if "goal" in topic_ids else topic_ids[0]
        topic_list = "\n".join([f"- {tid}: {topic_names.get(tid, tid.replace('_', ' '))}" for tid in topic_ids])

        schema_lines = []
        if "goal" in topic_ids:
            schema_lines.append('"goal": "Single goal statement"')
        if "scope" in topic_ids:
            schema_lines.append('"scope_items": ["In-scope item 1", "In-scope item 2"]')
            schema_lines.append('"out_of_scope_items": ["Out of scope item 1", "Out of scope item 2"]')
        for tid in topic_ids:
            if tid in {"goal", "scope"}:
                continue
            schema_lines.append(f'"{tid}": ["Item 1", "Item 2"]')

        json_schema = "{\n  " + ",\n  ".join(schema_lines) + "\n}"

        system_prompt = build_prompt(
            topic_id=base_topic,
            project_type=project_type,
            mode="document_extraction",
            context={"document_text": combined_text, "topic_list": topic_list},
            include_knowledge_base=False
        )

        extraction_prompt = f"""{system_prompt}

---
## Topics to Extract:
{topic_list}

## Required JSON Schema:
{json_schema}

---
## Document Content to Extract From:

{combined_text}

---
## Your Task:
Extract the requested topics from the document. Return ONLY valid JSON that matches the schema.
"""

        extracted_text = await self._call_llm(
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=2000,
            temperature=0.3,
            model_config=get_model_for_task("extraction")
        )

        print(f"[document_extraction] project_id={project_id} extracted_text_len={len(extracted_text or '')}")
        if extracted_text:
            preview = extracted_text[:2000]
            print(f"[document_extraction] extracted_text_preview:\n{preview}")
        
        extracted_out_of_scope = []

        # Try to parse as JSON first
        import json
        import re
        try:
            # Try to find JSON in the response
            json_match = re.search(r'```json\s*(.*?)\s*```', extracted_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try parsing the entire response as JSON
                json_str = extracted_text.strip()
                # Remove any leading/trailing text
                if '{' in json_str:
                    json_str = json_str[json_str.index('{'):json_str.rindex('}')+1]
            
            parsed_json = json.loads(json_str)
            print(f"[document_extraction] parsed_json_keys={list(parsed_json.keys()) if isinstance(parsed_json, dict) else type(parsed_json)}")
            
            extracted_content = {tid: [] for tid in topic_ids}

            if "goal" in topic_ids:
                goal_value = parsed_json.get("goal")
                if isinstance(goal_value, str) and goal_value.strip():
                    extracted_content["goal"] = [goal_value.strip()]

            if "scope" in topic_ids:
                scope_items = parsed_json.get("scope_items", [])
                out_items = parsed_json.get("out_of_scope_items", [])
                extracted_content["scope"] = scope_items if isinstance(scope_items, list) else []
                extracted_out_of_scope = out_items if isinstance(out_items, list) else []

            for tid in topic_ids:
                if tid in {"goal", "scope"}:
                    continue
                value = parsed_json.get(tid, [])
                if isinstance(value, str):
                    value = [value] if value.strip() else []
                extracted_content[tid] = value if isinstance(value, list) else []
            
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[document_extraction] json_parse_error={str(e)}")
            try:
                print(f"[document_extraction] json_str_preview:\n{json_str[:2000] if 'json_str' in locals() else ''}")
            except Exception:
                pass
            extracted_content = {tid: [] for tid in topic_ids}
            extracted_out_of_scope = []

        # Mark documents as extracted
        for doc in documents:
            await db.project_documents.update_one(
                {"_id": doc["_id"]},
                {"$set": {"extracted": True, "extracted_at": datetime.utcnow()}}
            )

        planner_data = await db.planner_data.find_one({"project_id": project_id})
        existing_content = (planner_data or {}).get("extracted_content", {})
        merged_content = {}
        for stage, content in extracted_content.items():
            existing = existing_content.get(stage, [])
            if isinstance(existing, list):
                new_items = [item for item in content if item not in existing]
                merged_content[stage] = existing + new_items
            else:
                merged_content[stage] = content

        if not planner_data:
            await db.planner_data.insert_one({
                "project_id": project_id,
                "extracted_content": merged_content,
                "source": "document_extraction",
                "extracted_at": datetime.utcnow()
            })
        else:
            await db.planner_data.update_one(
                {"project_id": project_id},
                {
                    "$set": {
                        "extracted_content": merged_content,
                        "source": "document_extraction",
                        "extracted_at": datetime.utcnow()
                    }
                }
            )

        # Save extracted content as draft entities (no draft content stored in chat history)
        for stage_name, content in extracted_content.items():
            if stage_name == "scope":
                await self.save_entity_draft(
                    project_id=project_id,
                    entity_type=stage_name,
                    items=content,
                    out_of_scope=extracted_out_of_scope or []
                )
            else:
                await self.save_entity_draft(
                    project_id=project_id,
                    entity_type=stage_name,
                    items=content
                )

        return {
            "extracted_content": extracted_content,
            "out_of_scope": {"scope": extracted_out_of_scope}
        }

    async def delete_document(self, project_id: str, file_id: str) -> bool:
        """Delete an uploaded document"""
        db = get_database()

        # Find document
        doc = await db.project_documents.find_one({
            "_id": ObjectId(file_id),
            "project_id": project_id
        })

        if not doc:
            raise ValueError("Document not found")

        # Delete file from filesystem
        file_path = doc["file_path"]
        if os.path.exists(file_path):
            os.remove(file_path)

        # Delete metadata
        await db.project_documents.delete_one({"_id": ObjectId(file_id)})

        return True

    # ==================== TOPIC MANAGEMENT METHODS ====================

    async def get_project_topics(self, project_id: str, include_entity_details: bool = True) -> Dict[str, Any]:
        """
        Get all topics for a project based on its type.
        Returns active topics with their current status and available optional topics.
        """
        from app.planner.topic_registry import (
            get_topics_for_project_type,
            get_default_topics_for_project_type,
            get_optional_topics_for_project_type,
            get_topic_by_id,
            TOPIC_GROUPS,
            TOPIC_DEPENDENCIES,
            TopicStatus
        )
        
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        
        if not project:
            raise ValueError("Project not found")
        
        project_type = project.get("type", "Software / Product Development")
        collection = get_planner_discussion_collection(project_id)
        
        # Get all applicable topics for this project type
        applicable_topics = get_topics_for_project_type(project_type)
        default_topic_ids = get_default_topics_for_project_type(project_type)
        optional_topic_ids = get_optional_topics_for_project_type(project_type)
        
        # Get current topic statuses from brain collection
        active_topics = []
        available_optional = []
        
        async def _build_topic(topic: Dict[str, Any]) -> Dict[str, Any]:
            topic_id = topic["id"]
            discussion = await self._find_one_with_charter_fallback(
                collection,
                {
                    "project_id": project_id,
                    "stage": topic_id,
                    "status": {"$in": ["active", "in_progress", "finalized"]},
                },
                sort=[("updated_at", -1), ("created_at", -1)],
            )
            entity_data: Dict[str, Any] = {"status": "pending", "items": []}
            if include_entity_details:
                entity_data = await self.get_stage_entities(project_id, topic_id)
            return {
                "topic": topic,
                "topic_id": topic_id,
                "discussion": discussion,
                "entity_status": entity_data.get("status", "pending"),
                "entity_items": entity_data.get("items", []),
            }

        resolved_topics = await asyncio.gather(*[_build_topic(topic) for topic in applicable_topics])

        for resolved in resolved_topics:
            topic = resolved["topic"]
            topic_id = resolved["topic_id"]
            discussion = resolved["discussion"]
            entity_status = resolved["entity_status"]
            entity_items = resolved["entity_items"]
            is_default = topic_id in default_topic_ids

            if discussion or entity_status != "pending":
                topic_status = entity_status
                if topic_status == "pending" and discussion:
                    topic_status = discussion.get("status", "not_started")
                    if topic_status == "active":
                        topic_status = "in_progress"

                active_topics.append({
                    "topic_id": topic_id,
                    "name": topic["name"],
                    "group_id": topic["group_id"],
                    "description": topic.get("description", ""),
                    "display_order": topic.get("display_order", 99),
                    "status": topic_status,
                    "is_default": is_default,
                    "dependencies": topic.get("dependencies", []),
                    "finalized_content": entity_items if topic_status == "finalized" else None,
                    "summary": discussion.get("summary") if discussion else None,
                    "updated_at": discussion.get("updated_at") if discussion else None
                })
                continue

            if is_default:
                active_topics.append({
                    "topic_id": topic_id,
                    "name": topic["name"],
                    "group_id": topic["group_id"],
                    "description": topic.get("description", ""),
                    "display_order": topic.get("display_order", 99),
                    "status": "not_started",
                    "is_default": True,
                    "dependencies": topic.get("dependencies", []),
                    "finalized_content": None,
                    "summary": None,
                    "updated_at": None
                })
                continue

            available_optional.append({
                "id": topic_id,
                "name": topic["name"],
                "group_id": topic["group_id"],
                "description": topic.get("description", ""),
                "display_order": topic.get("display_order", 99),
                "status": "optional",
                "dependencies": topic.get("dependencies", [])
            })
        
        return {
            "project_id": project_id,
            "project_type": project_type,
            "active_topics": active_topics,
            "available_optional_topics": available_optional,
            "groups": TOPIC_GROUPS
        }

    async def activate_optional_topic(self, project_id: str, topic_id: str) -> Dict[str, Any]:
        """
        Activate an optional topic for a project.
        Creates a discussion document for the topic.
        """
        from app.planner.topic_registry import (
            get_optional_topics_for_project_type,
            get_topic_by_id
        )
        
        db = get_database()
        project = await db.projects.find_one({"project_id": project_id})
        
        if not project:
            raise ValueError("Project not found")
        
        project_type = project.get("type", "Software / Product Development")
        optional_topic_ids = get_optional_topics_for_project_type(project_type)
        
        if topic_id not in optional_topic_ids:
            raise ValueError(f"Topic '{topic_id}' is not an optional topic for this project type")
        
        topic = get_topic_by_id(topic_id)
        if not topic:
            raise ValueError(f"Topic '{topic_id}' not found in registry")
        
        collection = get_planner_discussion_collection(project_id)
        
        # Check if already activated
        existing = await collection.find_one({
            "project_id": project_id,
            "entity_type": topic_id,
            "$or": [{"source": self.charter_source}, {"source": {"$exists": False}}, {"source": None}],
        })
        
        if existing:
            raise ValueError(f"Topic '{topic_id}' is already activated")
        
        # Create topic document
        topic_doc = {
            "project_id": project_id,
            "entity_type": "chat_history",
            "source": self.charter_source,
            "stage": topic_id,
            "status": "not_started",
            "is_default": False,
            "messages": [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        await collection.insert_one(topic_doc)
        
        return {
            "success": True,
            "topic_id": topic_id,
            "name": topic["name"],
            "status": "not_started"
        }

    async def get_topic_dependency_info(self, project_id: str, topic_id: str) -> Dict[str, Any]:
        """
        Get dependency information for a topic.
        Returns which dependent topics are completed/pending for banner display.
        """
        from app.planner.topic_registry import (
            get_topic_dependencies,
            get_topic_by_id
        )
        
        dependencies = get_topic_dependencies(topic_id)
        
        if not dependencies:
            return {
                "topic_id": topic_id,
                "has_dependencies": False,
                "dependencies": []
            }
        
        dependency_info = []
        for dep_id in dependencies:
            dep_topic = get_topic_by_id(dep_id)

            entity_data = await self.get_stage_entities(project_id, dep_id)
            is_finalized = entity_data.get("status") == "finalized"
            
            dependency_info.append({
                "topic_id": dep_id,
                "name": dep_topic["name"] if dep_topic else dep_id,
                "is_finalized": is_finalized
            })
        
        pending_count = sum(1 for d in dependency_info if not d["is_finalized"])
        
        return {
            "topic_id": topic_id,
            "has_dependencies": True,
            "dependencies": dependency_info,
            "pending_count": pending_count,
            "all_complete": pending_count == 0
        }


# Global service instance
planner_service = PlannerService()


