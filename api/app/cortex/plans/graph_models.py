"""Execution graph contracts for conditional multi-tool plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class GraphNodeKind(str, Enum):
    """Execution node class for runtime policy decisions."""

    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class GraphNode:
    """One DAG node wrapping a tool call."""

    node_id: str
    source_index: int
    tool_name: str
    args: Dict[str, Any]
    kind: GraphNodeKind
    depends_on: List[str] = field(default_factory=list)
    predicate: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ExecutionGraph:
    """Dependency graph for one request execution."""

    nodes: Dict[str, GraphNode]
    node_order: List[str]

