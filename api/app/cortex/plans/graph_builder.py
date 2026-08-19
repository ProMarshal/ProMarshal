"""Graph builder for cross-tool conditional execution."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, List

from app.cortex.plans.graph_models import ExecutionGraph, GraphNode, GraphNodeKind
from app.cortex.tools.base import IdempotencyClass
from app.cortex.tools.registry import CortexToolRegistry


def build_execution_graph(
    *,
    calls: List[Dict[str, Any]],
    tool_registry: CortexToolRegistry,
) -> ExecutionGraph:
    """Build dependency-aware execution graph from proposed tool calls."""
    nodes: Dict[str, GraphNode] = {}
    ids_in_order: List[str] = []
    previous_id = ""

    for idx, item in enumerate(calls):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        args = item.get("args")
        if not tool_name or not isinstance(args, dict):
            continue

        raw_node_id = str(item.get("node_id") or "").strip()
        node_id = raw_node_id or f"n{idx + 1}"
        if node_id in nodes:
            node_id = f"{node_id}_{idx + 1}"

        raw_depends_on = item.get("depends_on")
        depends_on: List[str] = []
        if isinstance(raw_depends_on, list):
            depends_on = [str(dep).strip() for dep in raw_depends_on if str(dep).strip()]
        elif previous_id:
            depends_on = [previous_id]

        try:
            idempotency = tool_registry.get(tool_name).normalized_spec().idempotency
            kind = (
                GraphNodeKind.READ
                if idempotency == IdempotencyClass.READ_ONLY.value
                else GraphNodeKind.WRITE
            )
        except Exception:
            # Unknown tools are treated as write-risk by default.
            kind = GraphNodeKind.WRITE

        predicate = item.get("predicate") if isinstance(item.get("predicate"), dict) else None
        node = GraphNode(
            node_id=node_id,
            source_index=idx,
            tool_name=tool_name,
            args=dict(args),
            kind=kind,
            depends_on=depends_on,
            predicate=predicate,
        )
        nodes[node_id] = node
        ids_in_order.append(node_id)
        previous_id = node_id

    # Sanitize dependencies to known ids only.
    known_ids = set(nodes.keys())
    sanitized: Dict[str, GraphNode] = {}
    for node_id in ids_in_order:
        node = nodes[node_id]
        deps = [dep for dep in node.depends_on if dep in known_ids and dep != node_id]
        sanitized[node_id] = GraphNode(
            node_id=node.node_id,
            source_index=node.source_index,
            tool_name=node.tool_name,
            args=node.args,
            kind=node.kind,
            depends_on=deps,
            predicate=node.predicate,
        )

    topo = _topological_order(nodes=sanitized, insertion_order=ids_in_order)
    return ExecutionGraph(nodes=sanitized, node_order=topo)


def _topological_order(*, nodes: Dict[str, GraphNode], insertion_order: List[str]) -> List[str]:
    indegree: Dict[str, int] = {node_id: 0 for node_id in nodes.keys()}
    edges: Dict[str, List[str]] = defaultdict(list)
    for node in nodes.values():
        for dep in node.depends_on:
            if dep not in nodes:
                continue
            edges[dep].append(node.node_id)
            indegree[node.node_id] = int(indegree.get(node.node_id) or 0) + 1

    queue = deque([node_id for node_id in insertion_order if indegree.get(node_id, 0) == 0])
    ordered: List[str] = []
    seen: set[str] = set()
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(node_id)
        for nxt in edges.get(node_id) or []:
            indegree[nxt] = max(0, int(indegree.get(nxt, 0)) - 1)
            if indegree[nxt] == 0:
                queue.append(nxt)

    # Cycle or unresolved graph fallback: preserve insertion order for deterministic behavior.
    if len(ordered) != len(nodes):
        return [node_id for node_id in insertion_order if node_id in nodes]
    return ordered


def graph_has_read_after_write(graph: ExecutionGraph) -> bool:
    """Return True when graph order includes read nodes after first write node."""
    write_seen = False
    for node_id in graph.node_order:
        node = graph.nodes.get(node_id)
        if not node:
            continue
        if node.kind == GraphNodeKind.WRITE:
            write_seen = True
            continue
        if node.kind == GraphNodeKind.READ and write_seen:
            return True
    return False

