import unittest
from typing import Optional

from app.llm import LLMGatewayRequest, LLMGatewayResponse
from app.projects.recommendations.recommendation_text_extractor import RecommendationTextExtractor


class FakeGateway:
    def __init__(self, response: LLMGatewayResponse):
        self._response = response
        self.last_request: Optional[LLMGatewayRequest] = None

    async def generate(self, request: LLMGatewayRequest, *, on_health_event: Optional[object] = None) -> LLMGatewayResponse:
        self.last_request = request
        return self._response


class RecommendationTextExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_signals_uses_gateway_when_schema_valid(self):
        gateway = FakeGateway(
            LLMGatewayResponse(
                ok=True,
                text=(
                    '[{"task_key":"ABC-1","risk_summary":"Blocked","action_hint":"Escalate blocker","confidence":0.9}]'
                ),
                provider="openai",
                model="gpt-4o-mini",
            )
        )
        extractor = RecommendationTextExtractor(gateway=gateway)
        signals = await extractor.extract_signals(
            [{"external_id": "ABC-1", "title": "Task", "status": "in_progress"}],
            project_id="proj-1111-2222",
        )
        self.assertIn("ABC-1", signals)
        self.assertEqual(signals["ABC-1"]["risk_summary"], "Blocked")
        self.assertEqual(signals["ABC-1"]["action_hint"], "Escalate blocker")
        self.assertIsNotNone(gateway.last_request)
        self.assertIsNotNone(gateway.last_request.response_format)
        self.assertEqual(gateway.last_request.response_format.get("type"), "json_schema")

    async def test_extract_signals_falls_back_when_schema_invalid(self):
        extractor = RecommendationTextExtractor(
            gateway=FakeGateway(
                LLMGatewayResponse(
                    ok=True,
                    text='{"not":"a_list"}',
                    provider="openai",
                    model="gpt-4o-mini",
                )
            )
        )
        signals = await extractor.extract_signals(
            [{"external_id": "ABC-2", "title": "Task", "status": "in_review", "last_comment": "waiting on review"}],
            project_id="proj-1111-2222",
        )
        self.assertIn("ABC-2", signals)
        self.assertIn("risk_summary", signals["ABC-2"])
        self.assertIn("action_hint", signals["ABC-2"])

    async def test_extract_signals_accepts_markdown_fenced_json(self):
        extractor = RecommendationTextExtractor(
            gateway=FakeGateway(
                LLMGatewayResponse(
                    ok=True,
                    text=(
                        "```json\n"
                        '[{"task_key":"ABC-3","risk_summary":"At risk","action_hint":"Follow up","confidence":0.77}]'
                        "\n```"
                    ),
                    provider="openai",
                    model="gpt-4o-mini",
                )
            )
        )
        signals = await extractor.extract_signals(
            [{"external_id": "ABC-3", "title": "Task", "status": "in_progress"}],
            project_id="proj-1111-2222",
        )
        self.assertIn("ABC-3", signals)
        self.assertEqual(signals["ABC-3"]["risk_summary"], "At risk")
        self.assertEqual(signals["ABC-3"]["action_hint"], "Follow up")

    async def test_extract_signals_falls_back_for_prose_prefixed_json(self):
        extractor = RecommendationTextExtractor(
            gateway=FakeGateway(
                LLMGatewayResponse(
                    ok=True,
                    text=(
                        "Here are the extracted signals:\n"
                        '[{"task_key":"ABC-4","risk_summary":"At risk","action_hint":"Follow up","confidence":0.77}]'
                    ),
                    provider="openai",
                    model="gpt-4o-mini",
                )
            )
        )
        signals = await extractor.extract_signals(
            [{"external_id": "ABC-4", "title": "Task", "status": "in_progress"}],
            project_id="proj-1111-2222",
        )
        self.assertIn("ABC-4", signals)
        self.assertNotEqual(signals["ABC-4"]["risk_summary"], "At risk")


if __name__ == "__main__":
    unittest.main()
