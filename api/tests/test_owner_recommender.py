import unittest
from datetime import datetime, timedelta

from app.projects.recommendations.owner_recommender import (
    compute_owner_load_metrics,
    rank_owner_candidates,
    suggest_owner,
)


class OwnerRecommenderTests(unittest.TestCase):
    def test_compute_owner_load_metrics_derives_load_score(self):
        now = datetime.utcnow()
        items = [
            {
                "task_key": "A-1",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "task_key": "A-2",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now + timedelta(hours=10)).isoformat(),
            },
            {
                "task_key": "S-1",
                "assignee_name": "Sam",
                "status": "done",
                "updated_at": (now - timedelta(days=1)).isoformat(),
            },
        ]
        metrics = compute_owner_load_metrics(items, now=now)
        self.assertIn("Alex", metrics)
        self.assertIn("Sam", metrics)
        self.assertGreater(metrics["Alex"]["assignee_load_score"], metrics["Sam"]["assignee_load_score"])
        self.assertIn("assignee_timezone_overlap_score", metrics["Alex"])

    def test_rank_owner_candidates_prefers_lower_load(self):
        now = datetime.utcnow()
        items = [
            {
                "task_key": "A-1",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "task_key": "S-1",
                "assignee_name": "Sam",
                "status": "in_progress",
                "due_date": (now + timedelta(days=5)).isoformat(),
            },
        ]
        ranked = rank_owner_candidates(items, exclude_task_key="X-1")
        self.assertGreaterEqual(len(ranked), 2)
        self.assertEqual(ranked[0][0], "Sam")

    def test_suggest_owner_increases_confidence_for_low_load_high_throughput(self):
        now = datetime.utcnow()
        items = [
            {
                "task_key": "TARGET-1",
                "assignee_name": "",
                "status": "in_progress",
            },
            {
                "task_key": "S-1",
                "assignee_name": "Sam",
                "status": "done",
                "updated_at": (now - timedelta(days=1)).isoformat(),
            },
            {
                "task_key": "S-2",
                "assignee_name": "Sam",
                "status": "done",
                "updated_at": (now - timedelta(days=2)).isoformat(),
            },
            {
                "task_key": "A-1",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "task_key": "A-2",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now + timedelta(hours=12)).isoformat(),
            },
        ]
        owner, confidence = suggest_owner(items, task_key="TARGET-1")
        self.assertEqual(owner, "Sam")
        self.assertGreaterEqual(confidence, 0.8)

    def test_suggest_owner_keeps_confidence_lower_when_candidates_close(self):
        now = datetime.utcnow()
        items = [
            {
                "task_key": "TARGET-2",
                "assignee_name": "",
                "status": "in_progress",
            },
            {
                "task_key": "S-1",
                "assignee_name": "Sam",
                "status": "in_progress",
                "due_date": (now + timedelta(days=3)).isoformat(),
            },
            {
                "task_key": "A-1",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now + timedelta(days=3, hours=1)).isoformat(),
            },
        ]
        _, confidence = suggest_owner(items, task_key="TARGET-2")
        self.assertLess(confidence, 0.8)

    def test_rank_owner_candidates_prefers_timezone_overlap_when_load_equal(self):
        now = datetime.utcnow()
        items = [
            {
                "task_key": "TARGET-3",
                "assignee_name": "",
                "status": "in_progress",
                "project_timezone": "Asia/Kolkata",
            },
            {
                "task_key": "S-1",
                "assignee_name": "Sam",
                "status": "in_progress",
                "due_date": (now + timedelta(days=3)).isoformat(),
                "assignee_timezone": "Asia/Kolkata",
                "project_timezone": "Asia/Kolkata",
            },
            {
                "task_key": "A-1",
                "assignee_name": "Alex",
                "status": "in_progress",
                "due_date": (now + timedelta(days=3)).isoformat(),
                "assignee_timezone": "America/Los_Angeles",
                "project_timezone": "Asia/Kolkata",
            },
        ]
        ranked = rank_owner_candidates(
            items,
            exclude_task_key="TARGET-3",
            project_timezone="Asia/Kolkata",
        )
        self.assertGreaterEqual(len(ranked), 2)
        self.assertEqual(ranked[0][0], "Sam")


if __name__ == "__main__":
    unittest.main()
