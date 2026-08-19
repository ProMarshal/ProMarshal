import unittest
from unittest.mock import AsyncMock, patch

from app.team_poll.relevance import evaluate_pending_poll_relevance


class TeamPollRelevanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_high_confidence_workitem_mutation_bypasses_to_cortex(self):
        decision = await evaluate_pending_poll_relevance(
            question_text="Did everyone submit update?",
            reply_text="add comment to PRM-80 and assign to sridevi",
            poll_id="poll-1",
            is_owner=False,
        )
        self.assertEqual(decision.action, "pass_to_cortex")
        self.assertEqual(decision.reason, "workitem_mutation_bypass")

    async def test_owner_control_bypass_accepts(self):
        decision = await evaluate_pending_poll_relevance(
            question_text="Did everyone submit update?",
            reply_text="status",
            poll_id="poll-1",
            is_owner=True,
        )
        self.assertEqual(decision.action, "accept_poll_response")
        self.assertEqual(decision.reason, "owner_control_bypass")

    async def test_embedding_above_threshold_accepts(self):
        with patch("app.team_poll.relevance._embed_text", new=AsyncMock(side_effect=[[1.0, 0.0], [1.0, 0.0]])):
            decision = await evaluate_pending_poll_relevance(
                question_text="Have you submitted your update?",
                reply_text="Yes, update submitted.",
                poll_id="poll-1",
                is_owner=False,
            )
        self.assertEqual(decision.action, "accept_poll_response")
        self.assertEqual(decision.path, "embedding")

    async def test_embedding_below_threshold_reminds(self):
        with patch("app.team_poll.relevance._embed_text", new=AsyncMock(side_effect=[[1.0, 0.0], [0.0, 1.0]])), patch(
            "app.team_poll.relevance._llm_tiebreak_relevance",
            new=AsyncMock(return_value={"possible_answer": False, "likely_new_intent": False, "confidence": 0.92, "reason": "unrelated"}),
        ):
            decision = await evaluate_pending_poll_relevance(
                question_text="Have you submitted your update?",
                reply_text="Can you remind me what this is about?",
                poll_id="poll-1",
                is_owner=False,
            )
        self.assertEqual(decision.action, "remind_pending_poll")
        self.assertEqual(decision.path, "llm_fallback")

    async def test_non_high_embedding_with_llm_possible_answer_accepts(self):
        with patch("app.team_poll.relevance._embed_text", new=AsyncMock(side_effect=[[1.0, 0.0], [0.62, 0.78]])), patch(
            "app.team_poll.relevance._llm_tiebreak_relevance",
            new=AsyncMock(return_value={"possible_answer": True, "likely_new_intent": False, "confidence": 0.91, "reason": "possible_answer"}),
        ):
            decision = await evaluate_pending_poll_relevance(
                question_text="Have you submitted your update?",
                reply_text="I think so",
                poll_id="poll-1",
                is_owner=False,
            )
        self.assertEqual(decision.action, "accept_poll_response")
        self.assertEqual(decision.path, "llm_fallback")

    async def test_non_high_embedding_with_llm_new_intent_still_reminds(self):
        with patch("app.team_poll.relevance._embed_text", new=AsyncMock(side_effect=[[1.0, 0.0], [0.62, 0.78]])), patch(
            "app.team_poll.relevance._llm_tiebreak_relevance",
            new=AsyncMock(return_value={"possible_answer": False, "likely_new_intent": True, "confidence": 0.90, "reason": "new_intent"}),
        ):
            decision = await evaluate_pending_poll_relevance(
                question_text="Have you submitted your update?",
                reply_text="can you start a different poll for this",
                poll_id="poll-1",
                is_owner=False,
            )
        self.assertEqual(decision.action, "remind_pending_poll")
        self.assertEqual(decision.path, "llm_fallback")

    async def test_llm_fallback_disabled_reminds(self):
        with patch("app.team_poll.relevance._embed_text", new=AsyncMock(side_effect=[[1.0, 0.0], [0.62, 0.78]])), patch(
            "app.team_poll.relevance.settings",
        ) as cfg:
            cfg.team_poll_relevance_enabled = True
            cfg.team_poll_relevance_threshold = 0.62
            cfg.team_poll_relevance_gray_band = 0.08
            cfg.team_poll_relevance_llm_fallback_enabled = False
            cfg.team_poll_relevance_model = "text-embedding-3-small"
            cfg.team_poll_relevance_cache_ttl_seconds = 3600
            decision = await evaluate_pending_poll_relevance(
                question_text="Have you submitted your update?",
                reply_text="I think so",
                poll_id="poll-1",
                is_owner=False,
            )
        self.assertEqual(decision.action, "remind_pending_poll")
        self.assertEqual(decision.reason, "llm_fallback_disabled")


if __name__ == "__main__":
    unittest.main()
