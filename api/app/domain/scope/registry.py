"""Canonical scope policy registry for deterministic gate/classifier flows."""

from __future__ import annotations

import re
from typing import Set


# Reason-code constants (single source)
REASON_EMPTY_MESSAGE = "empty_message"
REASON_NO_TEXT_TOKENS = "no_text_tokens"
REASON_GREETING_ONLY = "greeting_only"
REASON_PM_DEFINITION_QUERY = "pm_definition_query"
REASON_GENERAL_WORKLOAD_QUERY = "general_workload_query"
REASON_PM_ANALYTICS_QUERY = "pm_analytics_query"
REASON_PROJECT_BROADCAST_OPERATION = "project_broadcast_operation"
REASON_PROJECT_SIGNAL_DETECTED = "project_signal_detected"
REASON_GENERAL_QA_TOPIC = "general_qa_topic"
REASON_CONCEPTUAL_QUERY_WITHOUT_PROJECT_SIGNAL = "conceptual_query_without_project_signal"
REASON_INSUFFICIENT_PROJECT_SIGNALS = "insufficient_project_signals"


# Signal constants (single source)
SIGNAL_GREETING_ONLY = "greeting_only"
SIGNAL_WORK_ITEM_KEY = "work_item_key"
SIGNAL_PROJECT_ACTION_HINT = "project_action_hint"
SIGNAL_PROJECT_ENTITY_HINT = "project_entity_hint"
SIGNAL_CAPABILITY_QUERY = "capability_query"
SIGNAL_GENERAL_QA_TOPIC = "general_qa_topic"
SIGNAL_CONCEPTUAL_QUERY = "conceptual_query"
SIGNAL_ANALYTICS_QUERY = "analytics_query"
SIGNAL_BROADCAST_OPERATION = "broadcast_operation"


# Core lexical patterns/sets
WORD_RE = re.compile(r"[a-z0-9']+")
WORK_ITEM_KEY_RE = re.compile(r"\b[a-z][a-z0-9_]+-\d+\b", re.IGNORECASE)
PM_DEFINITION_RE = re.compile(r"^(what is|define|explain)\s+project management\b", re.IGNORECASE)
NON_PM_WORKLOAD_RE = re.compile(
    r"\b(study|exam|college|school|homework|personal|life)\s+workload\b",
    re.IGNORECASE,
)
PM_ANALYTICS_SCOPE_PHRASE_RE = re.compile(
    r"\b(?:by|per|for each|across|among|between)\s+(?:team|member|members|assignee|assignees|owner|owners)\b",
    re.IGNORECASE,
)
BROADCAST_AUDIENCE_RE = re.compile(
    r"\b(?:team|everyone|members|all|group|folks)\b",
    re.IGNORECASE,
)

GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "heyy",
    "heyyy",
    "hii",
    "hiii",
    "yo",
    "hola",
    "greetings",
    "gm",
    "good",
    "morning",
    "afternoon",
    "evening",
    "there",
    "team",
    "everyone",
    "thanks",
    "thank",
    "you",
    "promarshal",
    "bot",
}

PROJECT_ACTION_HINTS = {
    "create",
    "add",
    "update",
    "edit",
    "change",
    "assign",
    "reassign",
    "unassign",
    "comment",
    "set",
    "close",
    "resolve",
    "delete",
    "remove",
    "list",
    "show",
    "find",
    "check",
    "search",
    "summarize",
    "plan",
    "track",
    "review",
    "prioritize",
    "start",
    "complete",
    "blocked",
    "blocker",
    "status",
}

PROJECT_ENTITY_HINTS = {
    "project",
    "projects",
    "task",
    "tasks",
    "subtask",
    "subtasks",
    "story",
    "stories",
    "epic",
    "epics",
    "bug",
    "bugs",
    "defect",
    "defects",
    "feature",
    "features",
    "incident",
    "incidents",
    "problem",
    "problems",
    "requirement",
    "requirements",
    "impediment",
    "impediments",
    "ticket",
    "tickets",
    "issue",
    "issues",
    "request",
    "requests",
    "card",
    "cards",
    "workitem",
    "workitems",
    "item",
    "items",
    "backlog",
    "sprint",
    "milestone",
    "deadline",
    "owner",
    "assignee",
    "dependency",
    "dependencies",
    "risk",
    "risks",
    "roadmap",
    "charter",
    "jira",
    "linear",
}

PM_ANALYTICS_HINTS = {
    "workload",
    "distributed",
    "distribution",
    "capacity",
    "allocation",
    "bandwidth",
    "utilization",
    "overload",
    "overloaded",
    "underutilized",
    "overburdened",
    "burnout",
    "risk",
    "stalled",
    "aging",
    "throughput",
    "velocity",
    "bottleneck",
    "backlog",
    "overdue",
    "pending",
    "blocked",
    "blockers",
}

PM_ANALYTICS_CONTEXT_HINTS = {
    "team",
    "member",
    "members",
    "sprint",
    "project",
    "task",
    "tasks",
    "action",
    "actions",
    "followup",
    "followups",
    "reminder",
    "reminders",
    "item",
    "items",
    "assignee",
    "assignees",
    "owner",
    "owners",
    "status",
    "priority",
    "priorities",
    "due",
    "deadline",
    "deadlines",
}

BROADCAST_REQUEST_HINTS = {
    "ask",
    "check",
    "collect",
    "gather",
    "request",
    "run",
    "start",
    "send",
    "ping",
}

BROADCAST_RESPONSE_HINTS = {
    "status",
    "update",
    "updates",
    "progress",
    "response",
    "responses",
    "reply",
    "replies",
    "checkin",
    "input",
    "inputs",
    "confirm",
    "confirmation",
    "blocker",
    "blockers",
    "risk",
    "risks",
    "submission",
    "submitted",
}

CAPABILITY_QUERY_HINTS = {
    "help",
    "capability",
    "capabilities",
    "can",
    "do",
    "support",
}

GENERAL_QA_HINTS = {
    "weather",
    "temperature",
    "news",
    "stock",
    "bitcoin",
    "crypto",
    "joke",
    "poem",
    "recipe",
    "movie",
    "song",
    "translate",
    "capital",
    "president",
    "prime",
    "minister",
}

CONCEPTUAL_QUERY_PREFIXES = (
    "what is ",
    "what are ",
    "define ",
    "definition of ",
    "explain ",
    "tell me about ",
)


def is_pm_analytics_query(*, normalized: str, token_set: Set[str]) -> bool:
    """Return True when text clearly asks project analytics/overview signals."""
    if not token_set.intersection(PM_ANALYTICS_HINTS):
        return False
    if token_set.intersection(PM_ANALYTICS_CONTEXT_HINTS):
        return True
    return bool(PM_ANALYTICS_SCOPE_PHRASE_RE.search(normalized))


def is_project_broadcast_operation(*, normalized: str, token_set: Set[str]) -> bool:
    """
    Return True for project-operational broadcast asks (poll/check-in style).

    Intent-shape rule:
    - request verb (ask/check/collect/...) +
    - audience target (team/everyone/members/...) +
    - response/update semantics OR explicit project entity signal.
    """
    has_request = bool(token_set.intersection(BROADCAST_REQUEST_HINTS))
    if not has_request:
        return False
    has_audience = bool(BROADCAST_AUDIENCE_RE.search(normalized))
    if not has_audience:
        return False
    has_response_semantics = bool(token_set.intersection(BROADCAST_RESPONSE_HINTS))
    has_project_entity = bool(token_set.intersection(PROJECT_ENTITY_HINTS) or WORK_ITEM_KEY_RE.search(normalized))
    return bool(has_response_semantics or has_project_entity)
