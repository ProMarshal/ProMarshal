"""Shared schema primitives for Cortex launch tool inputs."""

from datetime import datetime, timedelta
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, model_validator


EXTERNAL_ID_PATTERN = r"^[A-Z][A-Z0-9]*-\d+$"
OPERATION_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9:_-]{7,127}$"
_KEYWORD_FORBIDDEN_CHARS_RE = re.compile(r"[\$\{\}]")
_EXTERNAL_ID_SEPARATED_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\s*(?:-|_|\s)\s*(\d+)$")
_EXTERNAL_ID_TRAILING_RE = re.compile(r"^([A-Za-z][A-Za-z_]*?)(\d+)$")


class Status(str, Enum):
    """Allowed task status values for bounded tool inputs."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Priority(str, Enum):
    """Allowed task priority values for bounded tool inputs."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EntityType(str, Enum):
    """Allowlisted entity types for Brain queries."""

    TASK = "task"
    PROJECT = "project"


class RelationshipType(str, Enum):
    """Allowlisted relationship types for Brain graph lookups."""

    BLOCKS = "blocks"
    BLOCKED_BY = "blocked_by"
    PARENT = "parent"
    CHILD = "child"
    RELATES_TO = "relates_to"


class DateRange(BaseModel):
    """Bounded date range model shared by search/query tools."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError("date_range.start must be before or equal to date_range.end")
        if self.end - self.start > timedelta(days=365):
            raise ValueError("date_range window cannot exceed 365 days")
        return self


def normalize_text(value: str) -> str:
    """Normalize free-text input by trimming whitespace."""
    return value.strip()


def validate_bounded_keyword(value: str) -> str:
    """Validate keyword does not contain forbidden operator fragments."""
    keyword = normalize_text(value)
    if _KEYWORD_FORBIDDEN_CHARS_RE.search(keyword):
        raise ValueError("keyword contains forbidden characters")
    return keyword


def normalize_operation_id(value: str) -> str:
    """Normalize and validate operation_id format for non-idempotent writes."""
    operation_id = normalize_text(value)
    if not operation_id:
        raise ValueError("operation_id is required")
    if not re.fullmatch(OPERATION_ID_PATTERN, operation_id):
        raise ValueError(
            "operation_id must be 8-128 chars and include only letters, numbers, colon, underscore, or hyphen"
        )
    return operation_id


def normalize_external_id(value: str) -> str:
    """
    Normalize Jira external id into canonical KEY-123 form.

    Accepts common variants:
    - KEY-123
    - key123
    - key 123
    - key_123
    """
    text = normalize_text(value)
    if not text:
        raise ValueError("external_id is required")

    canonical = text.upper()
    if re.fullmatch(EXTERNAL_ID_PATTERN, canonical):
        return canonical

    match = _EXTERNAL_ID_SEPARATED_RE.fullmatch(text)
    if match:
        key_part = str(match.group(1) or "").upper()
        number_part = str(match.group(2) or "").lstrip("0") or "0"
    else:
        compact = re.sub(r"\s+", "", text)
        trailing = _EXTERNAL_ID_TRAILING_RE.fullmatch(compact)
        if not trailing:
            raise ValueError("external_id must look like KEY-123 (e.g., SCRUM-1)")
        key_part = str(trailing.group(1) or "").upper()
        number_part = str(trailing.group(2) or "").lstrip("0") or "0"

    normalized = f"{key_part}-{number_part}"

    if not re.fullmatch(EXTERNAL_ID_PATTERN, normalized):
        raise ValueError("external_id must look like KEY-123 (e.g., SCRUM-1)")
    return normalized
