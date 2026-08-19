import unittest
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.cortex.quality.engine import QualityEngine
from app.cortex.quality.models import OperationProfile
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec


class _FakeProposalService:
    def __init__(self, *, assessment: Optional[Dict[str, Any]] = None):
        self.assessment = assessment or {"is_sufficient": True, "confidence": 0.9, "reason": ""}
        self.assess_calls = 0

    async def assess_field_quality(self, **kwargs):
        self.assess_calls += 1
        return dict(self.assessment)

    async def repair_tool_args(self, **kwargs):
        return None


class _WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    operation_id: str = Field(min_length=3)


class _ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: Optional[str] = Field(default=None, max_length=200)


class _WriteTool(CortexTool):
    input_model = _WriteInput
    spec = ToolSpec(
        name="test_write_tool",
        description="write test tool",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args):
        return {"ok": True}


class _ReadTool(CortexTool):
    input_model = _ReadInput
    spec = ToolSpec(
        name="test_read_tool",
        description="read test tool",
        idempotency=IdempotencyClass.READ_ONLY.value,
    )

    async def execute(self, args):
        return {"ok": True}


class CortexQualityEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_explicit_value_skips_semantic_block(self):
        proposal_service = _FakeProposalService(
            assessment={"is_sufficient": False, "confidence": 0.1, "reason": "too vague"}
        )
        engine = QualityEngine(proposal_service=proposal_service)

        decision = await engine.evaluate(
            executor=None,
            tool=_WriteTool(),
            args={"title": "cadence summary"},
            execution_context={
                "user_message": "create a task for cadence summary",
                "latest_user_message": "create a task for cadence summary",
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )

        self.assertFalse(decision.blocking)
        self.assertEqual(decision.status, "accepted")
        self.assertEqual(proposal_service.assess_calls, 0)

    async def test_write_inferred_vague_value_blocks(self):
        proposal_service = _FakeProposalService(
            assessment={"is_sufficient": False, "confidence": 0.2, "reason": "too vague"}
        )
        engine = QualityEngine(proposal_service=proposal_service)

        decision = await engine.evaluate(
            executor=None,
            tool=_WriteTool(),
            args={"title": "do it"},
            execution_context={
                "user_message": "create a task",
                "latest_user_message": "create a task",
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )

        self.assertTrue(decision.blocking)
        self.assertEqual(decision.status, "clarification_required")
        self.assertIn("test_write_tool.title", decision.missing_fields)
        self.assertGreaterEqual(proposal_service.assess_calls, 1)

    async def test_write_lexical_overlap_skips_semantic_block(self):
        proposal_service = _FakeProposalService(
            assessment={"is_sufficient": False, "confidence": 0.1, "reason": "too vague"}
        )
        engine = QualityEngine(proposal_service=proposal_service)

        decision = await engine.evaluate(
            executor=None,
            tool=_WriteTool(),
            args={"title": "implemented successfully and tested"},
            execution_context={
                "user_message": "add an update that it was implemented and tested successfully",
                "latest_user_message": "add an update that it was implemented and tested successfully",
            },
            operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
        )

        self.assertFalse(decision.blocking)
        self.assertEqual(decision.status, "accepted")
        self.assertEqual(proposal_service.assess_calls, 0)

    async def test_read_accepts_without_semantic_assessment(self):
        proposal_service = _FakeProposalService(
            assessment={"is_sufficient": False, "confidence": 0.1, "reason": "too vague"}
        )
        engine = QualityEngine(proposal_service=proposal_service)

        decision = await engine.evaluate(
            executor=None,
            tool=_ReadTool(),
            args={"keyword": "roadmap"},
            execution_context={"user_message": "show roadmap tasks"},
            operation_profile=OperationProfile.READ_ONLY,
        )

        self.assertFalse(decision.blocking)
        self.assertEqual(decision.status, "accepted")
        self.assertEqual(proposal_service.assess_calls, 0)


if __name__ == "__main__":
    unittest.main()
