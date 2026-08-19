import unittest
from unittest.mock import patch

from app.projects.recommendations.recommendation_thresholds import (
    normalize_recommendation_thresholds,
    resolve_project_recommendation_thresholds,
    resolve_project_recommendation_thresholds_with_sources,
)


class RecommendationThresholdsTests(unittest.TestCase):
    def test_normalize_recommendation_thresholds_clamps_values(self):
        normalized = normalize_recommendation_thresholds(
            {
                "overload_threshold": 999,
                "due_soon_hours": -1,
                "sprint_end_soon_hours": 9999,
                "sprint_open_critical_threshold": 0,
            }
        )
        self.assertEqual(normalized["overload_threshold"], 100.0)
        self.assertEqual(normalized["due_soon_hours"], 1)
        self.assertEqual(normalized["sprint_end_soon_hours"], 336)
        self.assertEqual(normalized["sprint_open_critical_threshold"], 1)

    def test_resolve_project_recommendation_thresholds_uses_project_override(self):
        project_doc = {"recommendation_thresholds": {"overload_threshold": 85}}
        resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["overload_threshold"], 85.0)

    def test_resolve_project_recommendation_thresholds_applies_builtin_project_type_profile(self):
        project_doc = {"project_type": "Implementation"}
        resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["due_soon_hours"], 72)
        self.assertEqual(resolved["sprint_end_soon_hours"], 96)

    def test_project_override_wins_over_project_type_profile(self):
        project_doc = {
            "project_type": "Implementation",
            "recommendation_thresholds": {"due_soon_hours": 60},
        }
        resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["due_soon_hours"], 60)

    def test_settings_project_type_profile_override_applies(self):
        project_doc = {"project_type": "Implementation"}
        with patch(
            "app.projects.recommendations.recommendation_thresholds.settings.recommendation_project_type_thresholds_json",
            '{"implementation":{"overload_threshold":82,"due_soon_hours":66}}',
        ):
            resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["overload_threshold"], 82.0)
        self.assertEqual(resolved["due_soon_hours"], 66)

    def test_settings_workspace_profile_override_applies(self):
        project_doc = {"integrations": {"slack": {"team_id": "T123"}}}
        with patch(
            "app.projects.recommendations.recommendation_thresholds.settings.recommendation_workspace_thresholds_json",
            '{"T123":{"overload_threshold":78,"due_soon_hours":52}}',
        ):
            resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["overload_threshold"], 78.0)
        self.assertEqual(resolved["due_soon_hours"], 52)

    def test_project_workspace_override_wins_over_settings_workspace_profile(self):
        project_doc = {
            "workspace_id": "T123",
            "recommendation_thresholds_by_workspace": {
                "T123": {"sprint_open_critical_threshold": 4}
            },
        }
        with patch(
            "app.projects.recommendations.recommendation_thresholds.settings.recommendation_workspace_thresholds_json",
            '{"T123":{"sprint_open_critical_threshold":2}}',
        ):
            resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["sprint_open_critical_threshold"], 4)

    def test_project_override_wins_over_workspace_and_project_type_profiles(self):
        project_doc = {
            "workspace_id": "T123",
            "project_type": "Implementation",
            "recommendation_thresholds": {"due_soon_hours": 40},
            "recommendation_thresholds_by_workspace": {"T123": {"due_soon_hours": 56}},
        }
        with patch(
            "app.projects.recommendations.recommendation_thresholds.settings.recommendation_workspace_thresholds_json",
            '{"T123":{"due_soon_hours":58}}',
        ):
            resolved = resolve_project_recommendation_thresholds(project_doc)
        self.assertEqual(resolved["due_soon_hours"], 40)

    def test_resolve_thresholds_with_sources_includes_each_layer(self):
        project_doc = {
            "workspace_id": "T123",
            "project_type": "Implementation",
            "recommendation_thresholds": {"overload_threshold": 81},
            "recommendation_thresholds_by_workspace": {"T123": {"due_soon_hours": 55}},
        }
        state = resolve_project_recommendation_thresholds_with_sources(project_doc)
        self.assertEqual(state["sources"]["workspace_profile"]["due_soon_hours"], 55)
        self.assertEqual(state["sources"]["project_type_profile"]["sprint_end_soon_hours"], 96)
        self.assertEqual(state["sources"]["project_override"]["overload_threshold"], 81)
        self.assertEqual(state["resolved"]["overload_threshold"], 81.0)


if __name__ == "__main__":
    unittest.main()
