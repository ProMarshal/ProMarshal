import unittest
from datetime import datetime, timedelta

from app.projects.recommendations.recommendation_engine import build_recommendations_from_health


class RecommendationEngineTests(unittest.TestCase):
    def test_best_practice_rules_emit_for_open_item_missing_fields(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-BP-1",
                    "title": "Open task missing planning hygiene",
                    "health_status": "on_track",
                    "status": "in_progress",
                    "assignee_name": "",
                    "due_date": "",
                    "start_date": "",
                }
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        by_id = {item["id"]: item for item in recommendations}
        self.assertIn("reco-bp-missing-assignee-PROJ-BP-1", by_id)
        self.assertIn("reco-bp-missing-due-date-PROJ-BP-1", by_id)
        self.assertIn("reco-bp-missing-start-date-PROJ-BP-1", by_id)
        self.assertEqual(by_id["reco-bp-missing-assignee-PROJ-BP-1"]["category"], "best_practice")
        self.assertEqual(by_id["reco-bp-missing-due-date-PROJ-BP-1"]["category"], "best_practice")
        self.assertEqual(by_id["reco-bp-missing-start-date-PROJ-BP-1"]["category"], "best_practice")

    def test_best_practice_start_date_rule_skips_not_started_items(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-BP-NS-1",
                    "title": "Backlog task without start date",
                    "health_status": "on_track",
                    "status": "backlog",
                    "assignee_name": "Owner",
                    "due_date": "2026-03-15T00:00:00",
                    "start_date": "",
                }
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        ids = {item["id"] for item in recommendations}
        self.assertNotIn("reco-bp-missing-start-date-PROJ-BP-NS-1", ids)

    def test_best_practice_start_date_rule_emits_for_blocked_items(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-BP-BLK-1",
                    "title": "Blocked task without start date",
                    "health_status": "at_risk",
                    "status": "blocked",
                    "assignee_name": "Owner",
                    "due_date": "2026-03-15T00:00:00",
                    "start_date": "",
                }
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        ids = {item["id"] for item in recommendations}
        self.assertIn("reco-bp-missing-start-date-PROJ-BP-BLK-1", ids)

    def test_best_practice_rules_skip_done_items(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-BP-2",
                    "title": "Completed task with missing fields",
                    "health_status": "on_track",
                    "status": "done",
                    "assignee_name": "",
                    "due_date": "",
                    "start_date": "",
                }
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(recommendations, [])

    def test_build_recommendations_filters_and_sorts(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-2",
                    "title": "At risk task",
                    "health_status": "at_risk",
                    "health_reason": "No updates",
                    "health_action": "Post status update",
                },
                {
                    "task_key": "PROJ-1",
                    "title": "Critical task",
                    "health_status": "critical",
                    "health_reason": "Overdue",
                    "health_action": "Recover today",
                },
                {
                    "task_key": "PROJ-3",
                    "title": "On track task",
                    "health_status": "on_track",
                    "health_reason": "",
                    "health_action": "",
                },
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)

        self.assertEqual(len(recommendations), 2)
        self.assertEqual(recommendations[0]["taskKey"], "PROJ-1")
        self.assertEqual(recommendations[0]["severity"], "critical")
        self.assertEqual(recommendations[1]["taskKey"], "PROJ-2")
        self.assertEqual(recommendations[1]["severity"], "at_risk")

    def test_build_recommendations_uses_defaults(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-7",
                    "title": "Missing reason/action",
                    "health_status": "critical",
                }
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["reason"], "Needs attention")
        self.assertEqual(
            recommendations[0]["recommendation"],
            "Review and update execution plan",
        )
        self.assertEqual(recommendations[0]["confidence"], 0.8)

    def test_build_recommendations_dedupes_same_task_key(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-9",
                    "title": "Task duplicate first",
                    "health_status": "critical",
                    "health_reason": "Overdue",
                    "health_action": "Recover today",
                },
                {
                    "task_key": "PROJ-9",
                    "title": "Task duplicate second",
                    "health_status": "critical",
                    "health_reason": "Still overdue",
                    "health_action": "Recover now",
                },
            ]
        }

        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["taskKey"], "PROJ-9")
        self.assertEqual(recommendations[0]["title"], "Task duplicate first")

    def test_r7_reopen_pattern_sets_at_risk_action(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-10",
                    "title": "Flaky task",
                    "health_status": "at_risk",
                    "reopen_count": 3,
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "at_risk")
        self.assertIn("reopened 3 times", recommendations[0]["reason"])
        self.assertIn("peer validation checklist", recommendations[0]["recommendation"].lower())

    def test_r9_critical_stale_escalates(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-11",
                    "title": "Critical stale",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "updated_at": "2024-01-01T00:00:00",
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "critical")
        self.assertIn("owner acknowledgment", recommendations[0]["recommendation"].lower())

    def test_r9_does_not_escalate_when_updated_at_missing_and_no_stale_signal(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-11B",
                    "title": "Critical without update timestamp",
                    "health_status": "at_risk",
                    "priority": "critical",
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "at_risk")
        self.assertNotIn("owner acknowledgment", recommendations[0]["recommendation"].lower())

    def test_r9_skips_text_only_stale_escalation_when_review_capability_disabled(self):
        health_payload = {
            "workflow_capabilities": {"has_reviews": False},
            "work_items": [
                {
                    "task_key": "PROJ-11C",
                    "title": "Critical no-review stale text",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "health_reason": "No update from owner",
                }
            ],
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "at_risk")
        self.assertNotIn("owner acknowledgment", recommendations[0]["recommendation"].lower())

    def test_r9_allows_text_only_stale_escalation_when_review_capability_enabled(self):
        health_payload = {
            "workflow_capabilities": {"has_reviews": True},
            "work_items": [
                {
                    "task_key": "PROJ-11D",
                    "title": "Critical review stale text",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "health_reason": "No update from owner",
                }
            ],
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "critical")
        self.assertIn("owner acknowledgment", recommendations[0]["recommendation"].lower())

    def test_r6_overload_due_soon_sets_at_risk(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-12",
                    "title": "Near due overloaded",
                    "health_status": "at_risk",
                    "due_date": due_soon,
                    "assignee_load_score": 82,
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["severity"], "at_risk")
        self.assertIn("reassign or split", recommendations[0]["recommendation"].lower())

    def test_r6_respects_overload_threshold_override(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-12B",
                    "title": "Near due overloaded",
                    "health_status": "at_risk",
                    "due_date": due_soon,
                    "assignee_load_score": 82,
                }
            ]
        }
        recommendations = build_recommendations_from_health(
            health_payload,
            thresholds={"overload_threshold": 90},
        )
        self.assertEqual(len(recommendations), 1)
        self.assertNotIn("reassign or split", recommendations[0]["recommendation"].lower())

    def test_r6_uses_owner_metric_fallback_when_task_load_score_missing(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        overdue = (datetime.utcnow() - timedelta(days=2)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-12C",
                    "title": "Near due overloaded without explicit load",
                    "health_status": "at_risk",
                    "due_date": due_soon,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-12D",
                    "title": "Sam overdue 1",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-12E",
                    "title": "Sam overdue 2",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-12F",
                    "title": "Sam overdue 3",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
            ]
        }
        recommendations = build_recommendations_from_health(
            health_payload,
            thresholds={"overload_threshold": 60},
        )
        by_key = {item["taskKey"]: item for item in recommendations}
        self.assertIn("PROJ-12C", by_key)
        self.assertIn("reassign or split", by_key["PROJ-12C"]["recommendation"].lower())

    def test_not_started_non_risk_item_is_filtered_out(self):
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-12G",
                    "title": "Backlog low priority with no dates",
                    "health_status": "at_risk",
                    "status": "backlog",
                    "priority": "low",
                    "assignee_name": "Sam",
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(recommendations, [])

    def test_not_started_due_soon_unassigned_high_priority_is_included(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-12H",
                    "title": "Backlog urgent unassigned",
                    "health_status": "at_risk",
                    "status": "backlog",
                    "priority": "high",
                    "assignee_name": "",
                    "due_date": due_soon,
                }
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["taskKey"], "PROJ-12H")

    def test_custom_status_map_gates_not_started_non_risk_item(self):
        health_payload = {
            "status_category_map": {
                "ready_for_dev": "not_started",
            },
            "work_items": [
                {
                    "task_key": "PROJ-12I",
                    "title": "Custom status with no risk",
                    "health_status": "at_risk",
                    "status": "Ready For Dev",
                    "priority": "low",
                    "assignee_name": "Sam",
                }
            ],
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(recommendations, [])

    def test_unassigned_gets_assignee_suggestion_when_possible(self):
        overdue = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-13",
                    "title": "Unassigned task",
                    "health_status": "critical",
                    "priority": "critical",
                    "due_date": overdue,
                    "assignee_name": "",
                },
                {
                    "task_key": "PROJ-14",
                    "title": "Peer task",
                    "health_status": "on_track",
                    "assignee_name": "Sam",
                },
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["taskKey"], "PROJ-13")
        self.assertIsNotNone(recommendations[0]["assignee_suggestion"])
        self.assertIn("sam", recommendations[0]["recommendation"].lower())

    def test_r8_sprint_end_risk_escalates_when_open_critical_count_high(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-20",
                    "title": "Critical A",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint X", "state": "active", "end_at": due_soon},
                },
                {
                    "task_key": "PROJ-21",
                    "title": "Critical B",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint X", "state": "active", "end_at": due_soon},
                },
            ]
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 2)
        self.assertTrue(all(item["severity"] == "critical" for item in recommendations))
        self.assertIn("scope triage", recommendations[0]["recommendation"].lower())

    def test_r8_respects_open_critical_threshold_override(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "work_items": [
                {
                    "task_key": "PROJ-30",
                    "title": "Critical A",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Y", "state": "active", "end_at": due_soon},
                },
                {
                    "task_key": "PROJ-31",
                    "title": "Critical B",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Y", "state": "active", "end_at": due_soon},
                },
            ]
        }
        recommendations = build_recommendations_from_health(
            health_payload,
            thresholds={"sprint_open_critical_threshold": 3},
        )
        self.assertEqual(len(recommendations), 2)
        self.assertFalse(any("scope triage" in item["recommendation"].lower() for item in recommendations))

    def test_r8_skips_when_capability_flags_disable_sprints(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "workflow_capabilities": {"has_sprints": False},
            "work_items": [
                {
                    "task_key": "PROJ-40",
                    "title": "Critical A",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Z", "state": "active", "end_at": due_soon},
                },
                {
                    "task_key": "PROJ-41",
                    "title": "Critical B",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Z", "state": "active", "end_at": due_soon},
                },
            ],
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 2)
        self.assertFalse(any("scope triage" in item["recommendation"].lower() for item in recommendations))

    def test_r8_still_applies_when_capability_flags_enable_sprints(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        health_payload = {
            "workflow_capabilities": {"has_sprints": True},
            "work_items": [
                {
                    "task_key": "PROJ-42",
                    "title": "Critical A",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Z2", "state": "active", "end_at": due_soon},
                },
                {
                    "task_key": "PROJ-43",
                    "title": "Critical B",
                    "health_status": "at_risk",
                    "priority": "critical",
                    "status": "in_progress",
                    "updated_at": datetime.utcnow().isoformat(),
                    "sprint": {"name": "Sprint Z2", "state": "active", "end_at": due_soon},
                },
            ],
        }
        recommendations = build_recommendations_from_health(health_payload)
        self.assertEqual(len(recommendations), 2)
        self.assertTrue(all("scope triage" in item["recommendation"].lower() for item in recommendations))

    def test_r6_degrades_to_baseline_load_when_aux_signals_unavailable(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        overdue = (datetime.utcnow() - timedelta(days=2)).isoformat()
        recent_done = (datetime.utcnow() - timedelta(days=1)).isoformat()
        health_payload = {
            "workflow_capabilities": {"has_reviews": False},
            "work_items": [
                {
                    "task_key": "PROJ-50",
                    "title": "Near due overloaded without aux",
                    "health_status": "at_risk",
                    "due_date": due_soon,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-51",
                    "title": "Sam overdue 1",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-52",
                    "title": "Sam overdue 2",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-53",
                    "title": "Sam overdue 3",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-54",
                    "title": "Sam recently completed",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-55",
                    "title": "Sam recently completed 2",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-56",
                    "title": "Sam recently completed 3",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-57",
                    "title": "Sam recently completed 4",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-58",
                    "title": "Sam recently completed 5",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-59",
                    "title": "Sam recently completed 6",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
            ],
        }
        recommendations = build_recommendations_from_health(
            health_payload,
            thresholds={"overload_threshold": 80},
        )
        by_key = {item["taskKey"]: item for item in recommendations}
        self.assertIn("PROJ-50", by_key)
        self.assertIn("reassign or split", by_key["PROJ-50"]["recommendation"].lower())

    def test_r6_uses_aux_signals_when_available(self):
        due_soon = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        overdue = (datetime.utcnow() - timedelta(days=2)).isoformat()
        recent_done = (datetime.utcnow() - timedelta(days=1)).isoformat()
        health_payload = {
            "workflow_capabilities": {"has_reviews": True},
            "project_timezone": "Asia/Kolkata",
            "work_items": [
                {
                    "task_key": "PROJ-60",
                    "title": "Near due overload candidate",
                    "health_status": "at_risk",
                    "due_date": due_soon,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-61",
                    "title": "Sam overdue 1",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-62",
                    "title": "Sam overdue 2",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-63",
                    "title": "Sam overdue 3",
                    "health_status": "at_risk",
                    "status": "in_progress",
                    "due_date": overdue,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-64",
                    "title": "Sam recently completed",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-65",
                    "title": "Sam recently completed 2",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-66",
                    "title": "Sam recently completed 3",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-67",
                    "title": "Sam recently completed 4",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-68",
                    "title": "Sam recently completed 5",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
                {
                    "task_key": "PROJ-69",
                    "title": "Sam recently completed 6",
                    "health_status": "on_track",
                    "status": "done",
                    "updated_at": recent_done,
                    "assignee_name": "Sam",
                },
            ],
        }
        recommendations = build_recommendations_from_health(
            health_payload,
            thresholds={"overload_threshold": 80},
        )
        by_key = {item["taskKey"]: item for item in recommendations}
        self.assertIn("PROJ-60", by_key)
        self.assertNotIn("reassign or split", by_key["PROJ-60"]["recommendation"].lower())


if __name__ == "__main__":
    unittest.main()
