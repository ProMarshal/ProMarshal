"""Shared work-item-read intent parsing and canonical filter enums."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional

from app.domain.parsing.models import (
    PARSE_STATUS_INVALID,
    PARSE_STATUS_RESOLVED,
    ParseOutcome,
    clamp_confidence,
)
from app.cortex.domain.normalization import resolve_enum_alias


class SearchStatus(str, Enum):
    """Search status values for PM read queries."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    PENDING = "pending"


class TaskLifecycleVisibility(str, Enum):
    """Default work-item lifecycle visibility for read flows."""

    NON_CLOSED = "non_closed"
    CLOSED_ONLY = "closed_only"


class AssigneeMode(str, Enum):
    """Assignee filter mode for PM work-item search."""

    ANY = "any"
    SPECIFIC = "specific"
    UNASSIGNED = "unassigned"
    ME = "me"


class DueDateMode(str, Enum):
    """Due-date filter mode for work-item reads."""

    ANY = "any"
    MISSING = "missing"
    PRESENT = "present"


LEGACY_UNASSIGNED_ASSIGNEE_ALIASES = {
    "unassigned",
    "none",
    "no one",
    "no-one",
    "not assigned",
    "without assignee",
}
LEGACY_SELF_ASSIGNEE_ALIASES = {"me", "myself"}
ASSIGNEE_MODE_ALIASES = {
    "all": AssigneeMode.ANY.value,
    "anyone": AssigneeMode.ANY.value,
    "self": AssigneeMode.ME.value,
    "none": AssigneeMode.UNASSIGNED.value,
    "no_assignee": AssigneeMode.UNASSIGNED.value,
    "without_assignee": AssigneeMode.UNASSIGNED.value,
    "not_assigned": AssigneeMode.UNASSIGNED.value,
    "no_one": AssigneeMode.UNASSIGNED.value,
    "nobody": AssigneeMode.UNASSIGNED.value,
}
STATUS_ALIASES = {
    "to_do": SearchStatus.TODO.value,
    "open": SearchStatus.PENDING.value,
    "inprogress": SearchStatus.IN_PROGRESS.value,
    "in-progress": SearchStatus.IN_PROGRESS.value,
    "in_review": SearchStatus.IN_PROGRESS.value,
    "in-review": SearchStatus.IN_PROGRESS.value,
    "review": SearchStatus.IN_PROGRESS.value,
    "completed": SearchStatus.DONE.value,
    "closed": SearchStatus.DONE.value,
    "resolved": SearchStatus.DONE.value,
    "pending_tasks": SearchStatus.PENDING.value,
    "not_completed": SearchStatus.PENDING.value,
    "not-completed": SearchStatus.PENDING.value,
    "incomplete": SearchStatus.PENDING.value,
    "all": SearchStatus.PENDING.value,
    "all_tasks": SearchStatus.PENDING.value,
}


WORK_ITEM_NOUN_PATTERN = re.compile(
    r"\b("
    r"task|tasks|story|stories|user\s+story|user\s+stories|epic|epics|"
    r"bug|bugs|defect|defects|sub[\s-]?task|sub[\s-]?tasks|"
    r"feature|features|incident|incidents|problem|problems|"
    r"service\s+request|service\s+requests|requirement|requirements|"
    r"impediment|impediments|milestone|milestones|"
    r"item|items|work\s+item|work\s+items|request|requests|issue|issues|ticket|tickets|"
    r"card|cards|backlog\s+item|backlog\s+items|work\s+request|work\s+requests"
    r")\b"
)
EMAIL_PATTERN = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
TASK_ID_PATTERN = re.compile(r"\b[a-z][a-z0-9_]+-\d+\b", re.IGNORECASE)
TASK_ID_RELAXED_PATTERN = re.compile(r"\b[a-z][a-z0-9_]*(?:[\s_-]?\d+)\b", re.IGNORECASE)
ASSIGNEE_ME_REGEX_PATTERNS = [
    re.compile(r"\b(?:assigned|assign)\s+to\s+me\b", re.IGNORECASE),
    re.compile(
        r"\bmy\b(?:\s+\w+){0,4}\s+\b("
        r"task|tasks|story|stories|epic|epics|bug|bugs|defect|defects|"
        r"sub[\s-]?task|sub[\s-]?tasks|feature|features|incident|incidents|problem|problems|"
        r"service\s+requests?|requirement|requirements|impediment|impediments|milestones?|"
        r"item|items|work\s+items|request|requests|issue|issues|ticket|tickets|"
        r"card|cards|backlog\s+items|work\s+requests"
        r")\b",
        re.IGNORECASE,
    ),
]

TASK_READ_MARKERS = [
    "list",
    "show",
    "give me",
    "how many",
    "count",
    "number of",
    "open",
    "pending",
    "unassigned",
    "not completed",
    "incomplete",
    "not done",
]
TASK_WRITE_MARKERS = [
    "create task",
    "add task",
    "new task",
    "update task",
    "update description",
    "change description",
    "edit description",
    "change status",
    "close task",
    "delete task",
    "add comment",
    "reassign",
    "assign task",
    "assign ",
]
ASSIGNEE_ME_MARKERS = [
    "my tasks",
    "assigned to me",
    "for me",
    "mine",
    "do i have",
    "i have",
    "that i have",
]
ASSIGNEE_UNASSIGNED_MARKERS = [
    "unassigned",
    "not assigned",
    "without assignee",
    "no assignee",
    "nobody assigned",
]
ASSIGNEE_ALL_SCOPE_MARKERS = [
    "all task",
    "all tasks",
    "all story",
    "all stories",
    "all user stories",
    "all user story",
    "all epic",
    "all epics",
    "all bug",
    "all bugs",
    "all defect",
    "all defects",
    "all feature",
    "all features",
    "all incident",
    "all incidents",
    "all problem",
    "all problems",
    "all service request",
    "all service requests",
    "all requirement",
    "all requirements",
    "all impediment",
    "all impediments",
    "all milestone",
    "all milestones",
    "all subtask",
    "all sub task",
    "all sub-task",
    "all subtasks",
    "all sub tasks",
    "all sub-tasks",
    "all issue",
    "all issues",
    "all ticket",
    "all tickets",
    "all item",
    "all items",
    "all work item",
    "all work items",
    "all request",
    "all requests",
    "all cards",
    "all backlog items",
    "all work requests",
    "including unassigned",
    "include unassigned",
    "team tasks",
    "team work items",
    "team tickets",
    "team requests",
]
COUNT_MARKERS = ["how many", "count", "number of"]
DUE_DATE_MISSING_MARKERS = [
    "without due date",
    "without a due date",
    "no due date",
    "without deadline",
    "no deadline",
    "due date not set",
    "due-date not set",
]
DUE_DATE_PRESENT_MARKERS = [
    "with due date",
    "with a due date",
    "has due date",
    "have due date",
    "with deadline",
]
WRITE_DISCOVERY_PREFIXES = [
    "how to ",
    "how do i ",
    "how can i ",
    "can you explain ",
    "what is ",
    "what are ",
]
WRITE_INTENT_PATTERNS = [
    re.compile(
        r"\b(assign|reassign)\s+[a-z][a-z0-9_]*(?:[\s_-]?\d+)\s+(to|for)\s+.+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(create|add|new)\s+(a\s+)?task\b", re.IGNORECASE),
    re.compile(
        r"\b(add|post)\s+comment\s+(to|on)\s+[a-z][a-z0-9_]*(?:[\s_-]?\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(update|change|move|set)\s+[a-z][a-z0-9_]*(?:[\s_-]?\d+)\s+(to|as)\s+[a-z_ -]+\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(update|change|edit|set)\s+(the\s+)?(task|issue|ticket)?\s*description\s+(of|for)\s+[a-z][a-z0-9_]*(?:[\s_-]?\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(update|change|edit|set)\s+[a-z][a-z0-9_]*(?:[\s_-]?\d+)\s+(task\s+|issue\s+|ticket\s+)?description\b",
        re.IGNORECASE,
    ),
]

CLOSED_STATUS_MARKERS = [
    "done",
    "completed",
    "closed",
    "resolved",
    "finished",
]
CLOSED_NEGATION_MARKERS = [
    "not done",
    "not completed",
    "not complete",
    "not closed",
    "unclosed",
    "incomplete",
    "unfinished",
]


@dataclass(frozen=True)
class TaskReadIntent:
    """Parsed shared work-item-read intent."""

    args: Dict[str, Any]
    is_count_request: bool
    status_explicit: bool = False


def is_task_write_intent(user_message: str) -> bool:
    """Return True when message clearly requests a task mutation action."""
    text = (user_message or "").strip().lower()
    if not text:
        return False

    if any(text.startswith(prefix) for prefix in WRITE_DISCOVERY_PREFIXES):
        return False

    if any(pattern.search(text) for pattern in WRITE_INTENT_PATTERNS):
        return True

    has_task_noun = bool(WORK_ITEM_NOUN_PATTERN.search(text))
    has_task_id = bool(TASK_ID_PATTERN.search(text) or TASK_ID_RELAXED_PATTERN.search(text))
    has_write_marker = any(marker in text for marker in TASK_WRITE_MARKERS)

    if has_write_marker and (has_task_noun or has_task_id):
        return True
    return False


def parse_task_read_intent(user_message: str) -> Optional[TaskReadIntent]:
    """Parse work-item-read query from natural language into canonical PM filters."""
    text = _normalize_text_for_task_intent(user_message)
    if not text:
        return None

    if any(marker in text for marker in TASK_WRITE_MARKERS):
        return None

    has_task_noun = bool(WORK_ITEM_NOUN_PATTERN.search(text))
    has_read_intent = any(marker in text for marker in TASK_READ_MARKERS)
    if not has_task_noun or not has_read_intent:
        return None

    args: Dict[str, Any] = {"max_results": 20}
    provider_types = _extract_provider_types(text)
    if provider_types:
        args["provider_types"] = provider_types

    if any(marker in text for marker in ASSIGNEE_UNASSIGNED_MARKERS):
        args["assignee_mode"] = AssigneeMode.UNASSIGNED.value
        args["include_unassigned"] = 1
    elif _is_assignee_me_intent_text(text):
        args["assignee_mode"] = AssigneeMode.ME.value
        args["include_unassigned"] = 0
    else:
        email_match = EMAIL_PATTERN.search(text)
        if email_match:
            args["assignee_mode"] = AssigneeMode.SPECIFIC.value
            args["assignee_value"] = email_match.group(0)
            args["include_unassigned"] = 0
        elif _is_all_scope_intent_text(text):
            args["assignee_mode"] = AssigneeMode.ANY.value
            args["include_unassigned"] = 1
        else:
            # Product policy: generic list queries default to all assignees,
            # including unassigned, unless explicitly scoped to self/specific.
            args["assignee_mode"] = AssigneeMode.ANY.value
            args["include_unassigned"] = 1

    lifecycle_visibility = parse_task_lifecycle_visibility(user_message)
    status_explicit = False
    if "in progress" in text:
        args["status"] = SearchStatus.IN_PROGRESS.value
        status_explicit = True
    elif "to do" in text or "todo" in text:
        args["status"] = SearchStatus.TODO.value
        status_explicit = True
    elif lifecycle_visibility == TaskLifecycleVisibility.CLOSED_ONLY:
        args["status"] = SearchStatus.DONE.value
        status_explicit = True
    elif any(
        marker in text
        for marker in ("open", "pending", "not completed", "not done", "incomplete", "unclosed", "unfinished")
    ):
        args["status"] = SearchStatus.PENDING.value
        status_explicit = True
    else:
        args["status"] = SearchStatus.PENDING.value

    if any(marker in text for marker in DUE_DATE_MISSING_MARKERS):
        args["due_date_mode"] = DueDateMode.MISSING.value
    elif any(marker in text for marker in DUE_DATE_PRESENT_MARKERS):
        args["due_date_mode"] = DueDateMode.PRESENT.value

    is_count_request = any(marker in text for marker in COUNT_MARKERS)
    return TaskReadIntent(
        args=args,
        is_count_request=is_count_request,
        status_explicit=status_explicit,
    )


def parse_task_lifecycle_visibility(user_message: str) -> TaskLifecycleVisibility:
    """Return default lifecycle scope for work-item-read requests."""
    text = _normalize_text_for_task_intent(user_message)
    if not text:
        return TaskLifecycleVisibility.NON_CLOSED

    has_closed_marker = any(marker in text for marker in CLOSED_STATUS_MARKERS)
    if not has_closed_marker:
        return TaskLifecycleVisibility.NON_CLOSED

    if any(marker in text for marker in CLOSED_NEGATION_MARKERS):
        return TaskLifecycleVisibility.NON_CLOSED

    return TaskLifecycleVisibility.CLOSED_ONLY


def is_task_read_self_scope_request(user_message: str) -> bool:
    """Return True when message requests work-item read scoped to the requesting actor."""
    intent = parse_task_read_intent(user_message)
    if intent is None:
        return False
    return str((intent.args or {}).get("assignee_mode") or "").strip().lower() == AssigneeMode.ME.value


def _normalize_text_for_task_intent(user_message: str) -> str:
    text = str(user_message or "").replace("\u00A0", " ").strip().lower()
    return re.sub(r"\s+", " ", text)


def _is_assignee_me_intent_text(text: str) -> bool:
    if any(marker in text for marker in ASSIGNEE_ME_MARKERS):
        return True
    for pattern in ASSIGNEE_ME_REGEX_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_all_scope_intent_text(text: str) -> bool:
    return any(marker in text for marker in ASSIGNEE_ALL_SCOPE_MARKERS)


def _extract_provider_types(text: str) -> List[str]:
    resolved: List[str] = []
    remaining = str(text or "")
    # Generic nouns should keep cross-type listing behavior and must not force
    # provider-type narrowing.
    if re.search(
        r"\b(tasks?|items?|work\s+items?|tickets?|issues?|requests?|work\s+requests?|cards?|backlog\s+items?)\b",
        remaining,
    ) and not re.search(
        r"\b(stories?|user\s+stories?|epics?|bugs?|defects?|sub[\s-]?tasks?|"
        r"features?|incidents?|problems?|service\s+requests?|requirements?|impediments?|milestones?)\b",
        remaining,
    ):
        return []
    for phrase, label in sorted(
        PROVIDER_TYPE_PHRASE_MAP,
        key=lambda item: len(str(item[0] or "")),
        reverse=True,
    ):
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if not re.search(pattern, remaining):
            continue
        if label not in resolved:
            resolved.append(label)
        # Consume matched spans so "sub tasks" does not also match "tasks".
        remaining = re.sub(pattern, " ", remaining)
    return resolved


def parse_task_read_status_outcome(raw_status: str) -> ParseOutcome:
    """Bridge parser that returns shared ParseOutcome for work-item-read status tokens."""
    mapped = resolve_enum_alias(str(raw_status or ""), STATUS_ALIASES)
    if mapped in {
        SearchStatus.TODO.value,
        SearchStatus.IN_PROGRESS.value,
        SearchStatus.DONE.value,
        SearchStatus.PENDING.value,
    }:
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(0.95),
            requires_clarification=False,
            normalized_payload={"status": mapped},
        )
    return ParseOutcome(
        status=PARSE_STATUS_INVALID,
        reason_code="invalid_input",
        confidence=clamp_confidence(0.0),
        requires_clarification=True,
        normalized_payload={"status": None},
    )
PROVIDER_TYPE_PHRASE_MAP = [
    ("service requests", "Service Request"),
    ("service request", "Service Request"),
    ("requirements", "Requirement"),
    ("requirement", "Requirement"),
    ("impediments", "Impediment"),
    ("impediment", "Impediment"),
    ("milestones", "Milestone"),
    ("milestone", "Milestone"),
    ("incidents", "Incident"),
    ("incident", "Incident"),
    ("problems", "Problem"),
    ("problem", "Problem"),
    ("features", "Feature"),
    ("feature", "Feature"),
    ("user stories", "Story"),
    ("user story", "Story"),
    ("stories", "Story"),
    ("story", "Story"),
    ("epics", "Epic"),
    ("epic", "Epic"),
    ("sub-tasks", "Sub-task"),
    ("sub tasks", "Sub-task"),
    ("subtasks", "Sub-task"),
    ("sub-task", "Sub-task"),
    ("sub task", "Sub-task"),
    ("subtask", "Sub-task"),
    ("defects", "Bug"),
    ("defect", "Bug"),
    ("bugs", "Bug"),
    ("bug", "Bug"),
    ("tasks", "Task"),
    ("task", "Task"),
]
