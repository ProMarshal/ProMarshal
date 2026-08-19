import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch


worker_module = importlib.import_module("app.cortex.worker")


class CortexWorkerMongoBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_mongo_ready_passthrough_state(self):
        ensure_mock = AsyncMock(return_value="connected")
        with patch.object(
            worker_module,
            "is_dramatiq_enabled_for_queue",
            return_value=False,
        ), patch.object(worker_module, "ensure_mongo_ready", new=ensure_mock):
            result = await worker_module._ensure_mongo_ready()

        ensure_mock.assert_awaited_once_with(runtime_owner=None)
        self.assertEqual(result, "connected")

    async def test_ensure_mongo_ready_passes_dramatiq_runtime_owner_when_enabled(self):
        ensure_mock = AsyncMock(return_value="reused")
        with patch.object(
            worker_module,
            "is_dramatiq_enabled_for_queue",
            return_value=True,
        ), patch.object(worker_module, "ensure_mongo_ready", new=ensure_mock):
            result = await worker_module._ensure_mongo_ready()

        ensure_mock.assert_awaited_once_with(runtime_owner=f"dramatiq_event_loop_thread:{os.getpid()}")
        self.assertEqual(result, "reused")

    async def test_ensure_mongo_ready_passes_none_runtime_owner_when_dramatiq_disabled(self):
        ensure_mock = AsyncMock(return_value="reused")
        with patch.object(
            worker_module,
            "is_dramatiq_enabled_for_queue",
            return_value=False,
        ), patch.object(worker_module, "ensure_mongo_ready", new=ensure_mock):
            result = await worker_module._ensure_mongo_ready()

        ensure_mock.assert_awaited_once_with(runtime_owner=None)
        self.assertEqual(result, "reused")


if __name__ == "__main__":
    unittest.main()
