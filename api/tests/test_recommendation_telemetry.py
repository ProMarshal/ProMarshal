import unittest

from app.projects.recommendations.recommendation_telemetry import record_recommendation_event


class RecommendationTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_recommendation_event_is_noop_true_for_valid_action(self):
        ok = await record_recommendation_event(
            project_id="mongo-id",
            custom_project_id="proj-1111-2222",
            action="shown",
            task_key="PROJ-1",
            recommendation_id="reco-1",
            source="pm_board_summary",
        )
        self.assertTrue(ok)

    async def test_record_recommendation_event_returns_false_for_invalid_action(self):
        ok = await record_recommendation_event(
            project_id="mongo-id",
            custom_project_id="proj-1111-2222",
            action="invalid",
            task_key="PROJ-1",
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
