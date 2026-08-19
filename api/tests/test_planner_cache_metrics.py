import unittest
from unittest.mock import patch

from app.planner import planner_cache as cache
from app.planner.router import get_planner_cache_metrics_route


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.kv:
                removed += 1
            self.kv.pop(key, None)
        return removed

    def scan_iter(self, match=None, count=200):
        prefix = str(match or "").replace("*", "")
        for key in list(self.kv.keys()):
            if key.startswith(prefix):
                yield key


class PlannerCacheMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_metrics_accumulate_and_route_returns_snapshot(self):
        redis = FakeRedis()
        cache.reset_planner_cache_metrics()
        with patch("app.planner.planner_cache.get_redis_connection", return_value=redis):
            cache.set_cached_planner_status("proj-1111-2222", {"current_stage": "goal"})
            cache.get_cached_planner_status("proj-1111-2222")
            cache.get_cached_planner_status("proj-1111-3333")
            cache.set_cached_stage_entities("proj-1111-2222", "goal", {"items": ["a"], "status": "draft"})
            cache.get_cached_stage_entities("proj-1111-2222", "goal")
            cache.invalidate_all_planner_cache("proj-1111-2222")

        metrics = await get_planner_cache_metrics_route()
        self.assertGreaterEqual(int(metrics.get("status_hits", 0)), 1)
        self.assertGreaterEqual(int(metrics.get("status_misses", 0)), 1)
        self.assertGreaterEqual(int(metrics.get("entities_hits", 0)), 1)
        self.assertGreaterEqual(int(metrics.get("writes", 0)), 2)
        self.assertGreaterEqual(int(metrics.get("invalidations", 0)), 2)
        self.assertGreaterEqual(int(metrics.get("invalidate_all_calls", 0)), 1)


if __name__ == "__main__":
    unittest.main()

