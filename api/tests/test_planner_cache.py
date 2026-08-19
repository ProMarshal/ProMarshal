import unittest
from unittest.mock import patch

from app.planner import planner_cache as cache


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for key in keys:
            self.kv.pop(key, None)
        return 1

    def exists(self, key):
        return 1 if key in self.kv else 0

    def scan_iter(self, match=None, count=200):
        if not match:
            for key in list(self.kv.keys()):
                yield key
            return
        prefix = str(match).replace("*", "")
        for key in list(self.kv.keys()):
            if key.startswith(prefix):
                yield key


class BrokenRedis:
    def get(self, key):
        raise RuntimeError("boom")

    def set(self, key, value, ex=None):
        raise RuntimeError("boom")

    def delete(self, *keys):
        raise RuntimeError("boom")

    def exists(self, key):
        raise RuntimeError("boom")

    def scan_iter(self, match=None, count=200):
        raise RuntimeError("boom")


class PlannerCacheTests(unittest.TestCase):
    def test_status_roundtrip(self):
        redis = FakeRedis()
        payload = {"current_stage": "goal", "stages": {"goal": "pending"}}
        with patch("app.planner.planner_cache.get_redis_connection", return_value=redis):
            cache.set_cached_planner_status("proj-1111-2222", payload)
            loaded = cache.get_cached_planner_status("proj-1111-2222")
        self.assertEqual(loaded, payload)

    def test_entities_roundtrip_and_invalidate(self):
        redis = FakeRedis()
        payload = {"items": ["a"], "status": "draft"}
        with patch("app.planner.planner_cache.get_redis_connection", return_value=redis):
            cache.set_cached_stage_entities("proj-1111-2222", "goal", payload)
            loaded = cache.get_cached_stage_entities("proj-1111-2222", "goal")
            cache.invalidate_stage_entities("proj-1111-2222", "goal")
            missing = cache.get_cached_stage_entities("proj-1111-2222", "goal")
        self.assertEqual(loaded, payload)
        self.assertIsNone(missing)

    def test_invalidate_all_planner_cache_clears_stage_keys(self):
        redis = FakeRedis()
        with patch("app.planner.planner_cache.get_redis_connection", return_value=redis):
            cache.set_cached_planner_status("proj-1111-2222", {"ok": True})
            cache.set_cached_current_stage("proj-1111-2222", {"current_stage": "goal"})
            cache.set_cached_stage_entities("proj-1111-2222", "goal", {"items": ["x"]})
            cache.set_cached_stage_entities("proj-1111-2222", "scope", {"items": ["y"]})
            cache.invalidate_all_planner_cache("proj-1111-2222")
            status = cache.get_cached_planner_status("proj-1111-2222")
            current_stage = cache.get_cached_current_stage("proj-1111-2222")
            goal = cache.get_cached_stage_entities("proj-1111-2222", "goal")
            scope = cache.get_cached_stage_entities("proj-1111-2222", "scope")
        self.assertIsNone(status)
        self.assertIsNone(current_stage)
        self.assertIsNone(goal)
        self.assertIsNone(scope)

    def test_fail_open_when_redis_errors(self):
        with patch("app.planner.planner_cache.get_redis_connection", return_value=BrokenRedis()):
            self.assertIsNone(cache.get_cached_planner_status("proj-1111-2222"))
            cache.set_cached_planner_status("proj-1111-2222", {"ok": True})
            cache.invalidate_all_planner_cache("proj-1111-2222")

    def test_bootstrap_payload_and_lock_roundtrip(self):
        redis = FakeRedis()
        payload = {"project_id": "proj-1111-2222", "entities_by_stage": {"goal": {"items": ["x"], "status": "draft"}}}
        with patch("app.planner.planner_cache.get_redis_connection", return_value=redis):
            cache.set_cached_planner_bootstrap_payload("proj-1111-2222", payload)
            loaded = cache.get_cached_planner_bootstrap_payload("proj-1111-2222")
            acquired = cache.acquire_planner_bootstrap_job_lock("proj-1111-2222")
            held = cache.has_planner_bootstrap_job_lock("proj-1111-2222")
            cache.release_planner_bootstrap_job_lock("proj-1111-2222")
            held_after = cache.has_planner_bootstrap_job_lock("proj-1111-2222")
        self.assertEqual(loaded, payload)
        self.assertTrue(acquired)
        self.assertTrue(held)
        self.assertFalse(held_after)


if __name__ == "__main__":
    unittest.main()
