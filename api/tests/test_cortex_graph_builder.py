import unittest

from app.cortex.plans.graph_builder import (
    build_execution_graph,
    graph_has_read_after_write,
)
from app.cortex.plans.graph_models import GraphNodeKind
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.cortex.tools.registry import CortexToolRegistry


class _ReadTool(CortexTool):
    spec = ToolSpec(
        name="graph_read_tool",
        description="graph read tool",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True}


class _WriteTool(CortexTool):
    spec = ToolSpec(
        name="graph_write_tool",
        description="graph write tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True}


class CortexGraphBuilderTests(unittest.TestCase):
    def setUp(self):
        self.registry = CortexToolRegistry()
        self.registry.register(_ReadTool())
        self.registry.register(_WriteTool())

    def test_build_graph_assigns_kind_and_order(self):
        graph = build_execution_graph(
            calls=[
                {"tool_name": "graph_read_tool", "args": {}},
                {"tool_name": "graph_write_tool", "args": {"title": "x"}},
            ],
            tool_registry=self.registry,
        )
        self.assertEqual(len(graph.node_order), 2)
        n1 = graph.nodes[graph.node_order[0]]
        n2 = graph.nodes[graph.node_order[1]]
        self.assertEqual(n1.kind, GraphNodeKind.READ)
        self.assertEqual(n2.kind, GraphNodeKind.WRITE)
        self.assertEqual(n2.depends_on, [n1.node_id])
        self.assertFalse(graph_has_read_after_write(graph))

    def test_read_after_write_detection(self):
        graph = build_execution_graph(
            calls=[
                {"tool_name": "graph_write_tool", "args": {"title": "x"}},
                {"tool_name": "graph_read_tool", "args": {}},
            ],
            tool_registry=self.registry,
        )
        self.assertTrue(graph_has_read_after_write(graph))


if __name__ == "__main__":
    unittest.main()

