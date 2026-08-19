"""Scheduler model helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class ScheduleSpec:
    mode: str
    time: Optional[str] = None
    minutes: Optional[int] = None


@dataclass
class ScheduleRunResult:
    status: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

