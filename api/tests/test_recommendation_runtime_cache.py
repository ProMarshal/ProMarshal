import unittest
from unittest.mock import patch

from app.projects.recommendations import recommendation_runtime_cache as cache


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.sets = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        for key in keys:
            self.kv.pop(key, None)
        return 1

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return 1

    def smembers(self, key):
        return self.sets.get(key, set())

    def srem(self, key, *values):
        current = self.sets.get(key, set())
        removed = 0
        for value in values:
            if value in current:
                current.remove(value)
                removed += 1
        return removed

    def expire(self, key, ttl):
        return True


class RecommendationRuntimeCacheTests(unittest.TestCase):
    def test_mark_list_clear_dirty_tasks(self):
        redis = FakeRedis()
        with patch("app.projects.recommendations.recommendation_runtime_cache.get_redis_connection", return_value=redis):
            self.assertTrue(cache.mark_task_dirty("proj-1111-2222", "ABC-1"))
            self.assertTrue(cache.mark_task_dirty("proj-1111-2222", "ABC-2"))
            keys = cache.list_dirty_task_keys("proj-1111-2222")
            self.assertEqual(keys, ["ABC-1", "ABC-2"])
            removed = cache.clear_dirty_task_keys("proj-1111-2222", ["ABC-1"])
            self.assertEqual(removed, 1)
            remaining = cache.list_dirty_task_keys("proj-1111-2222")
            self.assertEqual(remaining, ["ABC-2"])

    def test_set_and_get_project_recommendations(self):
        redis = FakeRedis()
        payload = {"project_id": "proj-1111-2222", "recommendations": [{"taskKey": "ABC-1"}]}
        with patch("app.projects.recommendations.recommendation_runtime_cache.get_redis_connection", return_value=redis):
            cache.set_project_recommendations("proj-1111-2222", payload)
            loaded = cache.get_project_recommendations("proj-1111-2222")
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded.get("project_id"), "proj-1111-2222")

    def test_set_get_invalidate_cached_api_project_recommendations(self):
        redis = FakeRedis()
        payload = {"project_id": "mongo-id", "recommendations": [{"taskKey": "ABC-1"}]}
        with patch("app.projects.recommendations.recommendation_runtime_cache.get_redis_connection", return_value=redis):
            cache.set_cached_api_project_recommendations("proj-1111-2222", payload)
            loaded = cache.get_cached_api_project_recommendations("proj-1111-2222")
            cache.invalidate_cached_api_project_recommendations("proj-1111-2222")
            cleared = cache.get_cached_api_project_recommendations("proj-1111-2222")
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded.get("project_id"), "mongo-id")
        self.assertIsNone(cleared)


if __name__ == "__main__":
    unittest.main()
