import importlib
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch


orchestrator_module = importlib.import_module("app.projects.recommendations.recommendation_orchestrator")


class FakeCursor:
    def __init__(self, items):
        self._items = list(items)

    async def to_list(self, length=None):
        return list(self._items)


class FakeBrainCollection:
    def __init__(self, by_filter=None):
        self._by_filter = by_filter or {}

    def find(self, query):
        query_key = tuple(sorted((query or {}).items()))
        for matcher, items in self._by_filter.items():
            if matcher == query_key:
                return FakeCursor(items)
        return FakeCursor([])


class CompletedLookupCollection:
    def __init__(self, completed_external_ids):
        self._completed_external_ids = list(completed_external_ids)

    def find(self, query):
        external_filter = ((query or {}).get("external_id") or {}).get("$in") or []
        target = set(str(item) for item in external_filter)
        rows = [
            {"external_id": key, "status": "done"}
            for key in self._completed_external_ids
            if str(key) in target
        ]
        return FakeCursor(rows)


class RecommendationOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_project_recommendations_writes_runtime_payload(self):
        tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-1",
                "title": "Task A",
                "due_date": datetime(2030, 1, 1, 10, 0, 0),
            }
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-1",
                "taskKey": "ABC-1",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.8,
            }
        ]

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(
                return_value={
                    "thresholds": {},
                    "project_timezone": "Asia/Kolkata",
                    "workflow_capabilities": {"has_sprints": False},
                }
            ),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ) as build_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ) as set_meta_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["count"], 1)
        set_task_mock.assert_called_once()
        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_eta"), "2030-01-01T10:00:00")
        self.assertEqual(state.get("last_signal_source"), "due_date")
        set_project_mock.assert_called_once()
        set_meta_mock.assert_called_once()
        call_health_payload = build_mock.call_args.args[0]
        self.assertEqual(call_health_payload.get("project_timezone"), "Asia/Kolkata")
        self.assertEqual(call_health_payload.get("workflow_capabilities"), {"has_sprints": False})

    async def test_generate_project_recommendations_prunes_completed_before_runtime_write(self):
        tasks = [
            {"entity_type": "work_item", "external_id": "ABC-1", "title": "Done Task", "status": "done"},
            {"entity_type": "work_item", "external_id": "ABC-2", "title": "Open Task", "status": "in_progress"},
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-1",
                "taskKey": "ABC-1",
                "recommendation": "Should be pruned",
                "severity": "critical",
                "why": ["Done"],
                "confidence": 0.9,
            },
            {
                "id": "reco-ABC-2",
                "taskKey": "ABC-2",
                "recommendation": "Keep this",
                "severity": "at_risk",
                "why": ["Open"],
                "confidence": 0.7,
            },
        ]
        prune_result = {"recommendations": [fake_recommendations[1]], "removed": 1}

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._prune_completed_recommendations_from_list",
            new=AsyncMock(return_value=prune_result),
        ) as prune_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        prune_mock.assert_awaited_once()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["completed_removed"], 1)
        set_task_mock.assert_called_once()
        written_task_key = str(set_task_mock.call_args.args[1] or "")
        self.assertEqual(written_task_key, "ABC-2")
        saved_payload = set_project_mock.call_args.args[1]
        saved_keys = [str(item.get("taskKey") or "") for item in saved_payload.get("recommendations", [])]
        self.assertEqual(saved_keys, ["ABC-2"])

    async def test_incremental_recompute_skips_when_no_dirty_tasks(self):
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=[],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="event",
            )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "no_dirty_tasks")

    async def test_incremental_no_dirty_cleans_completed_recommendations(self):
        fake_brain = CompletedLookupCollection(completed_external_ids=["ABC-1"])
        existing_payload = {
            "project_id": "proj-1111-2222",
            "recommendations": [
                {"id": "reco-ABC-1", "taskKey": "ABC-1", "severity": "critical"},
                {"id": "reco-ABC-2", "taskKey": "ABC-2", "severity": "at_risk"},
            ],
        }
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=[],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value=existing_payload,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.delete_task_recommendation",
            return_value=None,
        ) as delete_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ) as set_meta_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="scheduler_incremental",
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["completed_removed"], 1)
        delete_task_mock.assert_called_once_with("proj-1111-2222", "ABC-1")
        self.assertGreaterEqual(set_project_mock.call_count, 1)
        self.assertGreaterEqual(set_meta_mock.call_count, 1)

    async def test_incremental_no_dirty_transitions_missed_commitment_eta(self):
        existing_payload = {
            "project_id": "proj-1111-2222",
            "recommendations": [
                {
                    "id": "reco-ABC-1",
                    "taskKey": "ABC-1",
                    "severity": "at_risk",
                    "recommendation": "Follow up with owner",
                }
            ],
        }
        task_runtime = {
            "task_key": "ABC-1",
            "severity": "at_risk",
            "recommendation": "Follow up with owner",
            "state": {
                "commitment_eta": "2024-01-01T00:00:00",
                "commitment_status": "open",
                "last_signal_at": "2024-01-01T00:00:00",
                "last_signal_source": "health",
            },
        }
        fake_brain = CompletedLookupCollection(completed_external_ids=[])
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=[],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value=existing_payload,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_task_recommendation",
            return_value=task_runtime,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="scheduler_incremental",
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["commitment_missed_transitions"], 1)
        set_task_mock.assert_called_once()
        self.assertGreaterEqual(set_project_mock.call_count, 1)

    async def test_incremental_no_dirty_transitions_commitment_open_to_met_on_completion(self):
        existing_payload = {
            "project_id": "proj-1111-2222",
            "recommendations": [
                {
                    "id": "reco-ABC-11",
                    "taskKey": "ABC-11",
                    "severity": "at_risk",
                    "recommendation": "Follow up with owner",
                }
            ],
        }
        task_runtime = {
            "task_key": "ABC-11",
            "severity": "at_risk",
            "recommendation": "Follow up with owner",
            "state": {
                "commitment_eta": "2038-01-01T00:00:00",
                "commitment_status": "open",
                "last_signal_at": "2037-12-01T00:00:00",
                "last_signal_source": "health",
            },
        }
        fake_brain = CompletedLookupCollection(completed_external_ids=["ABC-11"])
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=[],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value=existing_payload,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_task_recommendation",
            return_value=task_runtime,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="scheduler_incremental",
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["commitment_missed_transitions"], 0)
        self.assertEqual(result["commitment_met_transitions"], 1)
        set_task_mock.assert_called_once()
        saved_runtime = set_task_mock.call_args.args[2]
        self.assertEqual((saved_runtime.get("state") or {}).get("commitment_status"), "met")
        self.assertGreaterEqual(set_project_mock.call_count, 1)

    async def test_incremental_dirty_recompute_sets_commitment_eta_from_due_date(self):
        class DirtyTaskCollection:
            def __init__(self, rows):
                self._rows = list(rows)

            def find(self, query):
                return FakeCursor(self._rows)

        dirty_tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-9",
                "title": "Task Dirty",
                "due_date": datetime(2031, 2, 2, 9, 0, 0),
            }
        ]
        fake_recommendations = [
            {
                "id": "reco-ABC-9",
                "taskKey": "ABC-9",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.9,
            }
        ]
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=["ABC-9"],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=DirtyTaskCollection(dirty_tasks),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value={"project_id": "proj-1111-2222", "recommendations": []},
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.clear_dirty_task_keys",
            return_value=1,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="event",
            )

        self.assertEqual(result["updated_count"], 1)
        set_task_mock.assert_called_once()
        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_eta"), "2031-02-02T09:00:00")
        self.assertEqual(state.get("last_signal_source"), "due_date")

    async def test_incremental_dirty_recompute_prunes_completed_from_existing_payload(self):
        class DirtyTaskCollection:
            def __init__(self, rows):
                self._rows = list(rows)

            def find(self, query):
                query_dict = dict(query or {})
                ext_id_filter = query_dict.get("external_id")
                if isinstance(ext_id_filter, dict) and "$in" in ext_id_filter:
                    target_keys = set(str(item) for item in (ext_id_filter.get("$in") or []))
                    rows = [row for row in self._rows if str(row.get("external_id")) in target_keys]
                    if "ABC-2" in target_keys:
                        rows.append({"external_id": "ABC-2", "status": "done"})
                    return FakeCursor(rows)
                return FakeCursor([])

        dirty_tasks = [{"entity_type": "work_item", "external_id": "ABC-1", "title": "Task Dirty"}]
        fake_recommendations = [
            {
                "id": "reco-ABC-1",
                "taskKey": "ABC-1",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.9,
            }
        ]
        existing_payload = {
            "project_id": "proj-1111-2222",
            "recommendations": [
                {"id": "reco-ABC-2", "taskKey": "ABC-2", "severity": "critical", "recommendation": "Old"},
            ],
        }
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=["ABC-1"],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=DirtyTaskCollection(dirty_tasks),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value=existing_payload,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.delete_task_recommendation",
            return_value=None,
        ) as delete_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ) as set_project_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.clear_dirty_task_keys",
            return_value=1,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="event",
            )

        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["completed_removed"], 1)
        delete_task_mock.assert_called_once_with("proj-1111-2222", "ABC-2")
        saved_payload = set_project_mock.call_args.args[1]
        saved_keys = [str(item.get("taskKey") or "") for item in saved_payload.get("recommendations", [])]
        self.assertEqual(saved_keys, ["ABC-1"])

    async def test_incremental_no_dirty_cleans_completed_recommendations_when_is_closed_true(self):
        class ClosedFlagCollection:
            def find(self, query):
                ext_id_filter = ((query or {}).get("external_id") or {}).get("$in") or []
                target = set(str(item) for item in ext_id_filter)
                rows = []
                if "ABC-7" in target:
                    rows.append({"external_id": "ABC-7", "status": "in_progress", "is_closed": True})
                return FakeCursor(rows)

        existing_payload = {
            "project_id": "proj-1111-2222",
            "recommendations": [
                {"id": "reco-ABC-7", "taskKey": "ABC-7", "severity": "critical"},
                {"id": "reco-ABC-8", "taskKey": "ABC-8", "severity": "at_risk"},
            ],
        }
        with patch(
            "app.projects.recommendations.recommendation_orchestrator.list_dirty_task_keys",
            return_value=[],
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_project_recommendations",
            return_value=existing_payload,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=ClosedFlagCollection(),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.delete_task_recommendation",
            return_value=None,
        ) as delete_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            result = await orchestrator_module.recompute_dirty_recommendations(
                "proj-1111-2222",
                trigger="scheduler_incremental",
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["completed_removed"], 1)
        delete_task_mock.assert_called_once_with("proj-1111-2222", "ABC-7")

    async def test_generate_project_recommendations_prefers_explicit_commitment_field_over_due_date(self):
        tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-3",
                "title": "Task Explicit Commitment",
                "due_date": datetime(2035, 1, 1, 10, 0, 0),
                "commitment": {"eta": "2034-12-31T16:30:00"},
            }
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-3",
                "taskKey": "ABC-3",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.8,
            }
        ]

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_eta"), "2034-12-31T16:30:00")
        self.assertEqual(state.get("last_signal_source"), "commitment_field")

    async def test_generate_project_recommendations_extracts_commitment_eta_from_comment_text(self):
        tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-4",
                "title": "Task Text Commitment",
                "last_comment": "Owner update: will finish by 2036-06-15T09:45:00Z",
            }
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-4",
                "taskKey": "ABC-4",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.8,
            }
        ]

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_eta"), "2036-06-15T09:45:00")
        self.assertEqual(state.get("last_signal_source"), "commitment_text")

    async def test_generate_project_recommendations_extracts_commitment_eta_with_compact_tz_offset(self):
        tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-5",
                "title": "Task Compact TZ",
                "last_comment": "ETA confirmed as 2036-06-15T09:45:00+0530",
            }
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-5",
                "taskKey": "ABC-5",
                "recommendation": "Recover today",
                "severity": "critical",
                "why": ["Overdue"],
                "confidence": 0.8,
            }
        ]

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_eta"), "2036-06-15T09:45:00")
        self.assertEqual(state.get("last_signal_source"), "commitment_text")

    async def test_generate_project_recommendations_initializes_commitment_status_met_for_completed_task(self):
        tasks = [
            {
                "entity_type": "work_item",
                "external_id": "ABC-10",
                "title": "Completed Task",
                "status": "done",
                "due_date": datetime(2037, 1, 1, 9, 0, 0),
            }
        ]
        fake_brain = FakeBrainCollection(
            by_filter={tuple(sorted({"entity_type": "work_item"}.items())): tasks}
        )
        fake_recommendations = [
            {
                "id": "reco-ABC-10",
                "taskKey": "ABC-10",
                "recommendation": "Confirm closure notes",
                "severity": "info",
                "why": ["Completion verification"],
                "confidence": 0.6,
            }
        ]

        with patch(
            "app.projects.recommendations.recommendation_orchestrator.get_brain_collection",
            return_value=fake_brain,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._resolve_project_runtime_config",
            new=AsyncMock(return_value={"thresholds": {}, "project_timezone": "Asia/Kolkata"}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator._TEXT_EXTRACTOR.extract_signals",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.build_recommendations_from_health",
            return_value=fake_recommendations,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_task_recommendation",
            return_value=None,
        ) as set_task_mock, patch(
            "app.projects.recommendations.recommendation_orchestrator.set_project_recommendations",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.set_recommendation_meta",
            return_value=None,
        ), patch(
            "app.projects.recommendations.recommendation_orchestrator.invalidate_pm_board_summary_cache",
            return_value=None,
        ):
            await orchestrator_module.generate_project_recommendations(
                "proj-1111-2222",
                trigger="scheduler_full",
            )

        state = set_task_mock.call_args.args[2].get("state") or {}
        self.assertEqual(state.get("commitment_status"), "met")


if __name__ == "__main__":
    unittest.main()
