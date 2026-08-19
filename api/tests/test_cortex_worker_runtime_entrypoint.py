import importlib
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


worker_module = importlib.import_module("app.cortex.worker")


class CortexWorkerRuntimeEntrypointTests(unittest.TestCase):
    def test_run_async_entrypoint_uses_reusable_loop_in_non_fork_runtime(self):
        class _FakeLoop:
            def __init__(self) -> None:
                self.called = False

            def run_until_complete(self, coro):
                self.called = True
                coro.close()

        fake_loop = _FakeLoop()
        sample_coro = AsyncMock()()
        with patch.object(worker_module, "_is_non_fork_runtime", return_value=True), patch.object(
            worker_module,
            "_get_worker_loop",
            return_value=fake_loop,
        ), patch.object(worker_module.asyncio, "run") as asyncio_run_mock:
            worker_module._run_async_entrypoint(sample_coro)

        self.assertTrue(fake_loop.called)
        asyncio_run_mock.assert_not_called()

    def test_run_async_entrypoint_uses_asyncio_run_in_fork_runtime(self):
        sample_coro = AsyncMock()()
        with patch.object(worker_module, "_is_non_fork_runtime", return_value=False), patch.object(
            worker_module,
            "_get_worker_loop",
        ) as get_loop_mock, patch.object(worker_module.asyncio, "run") as asyncio_run_mock:
            worker_module._run_async_entrypoint(sample_coro)

        asyncio_run_mock.assert_called_once_with(sample_coro)
        get_loop_mock.assert_not_called()
        sample_coro.close()

    def test_run_async_entrypoint_uses_dramatiq_event_loop_thread_when_enabled(self):
        class _FakeFuture:
            def __init__(self) -> None:
                self.waited = False

            def result(self):
                self.waited = True
                return None

        class _FakeLoopThread:
            def __init__(self) -> None:
                self.future = _FakeFuture()
                self.called = False

            def run_coroutine(self, coro):
                self.called = True
                coro.close()
                return self.future

        loop_thread = _FakeLoopThread()
        sample_coro = AsyncMock()()
        dramatiq_asyncio_module = types.ModuleType("dramatiq.asyncio")
        dramatiq_asyncio_module.get_event_loop_thread = lambda: loop_thread
        with patch.object(
            worker_module,
            "is_dramatiq_enabled_for_queue",
            return_value=True,
        ), patch.dict(
            "sys.modules",
            {"dramatiq.asyncio": dramatiq_asyncio_module},
        ), patch.object(worker_module.asyncio, "run") as asyncio_run_mock:
            worker_module._run_async_entrypoint(sample_coro)

        self.assertTrue(loop_thread.called)
        self.assertTrue(loop_thread.future.waited)
        asyncio_run_mock.assert_not_called()

    def test_shutdown_non_fork_runtime_closes_loop_and_resets_handle(self):
        class _FakeLoop:
            def __init__(self) -> None:
                self.closed = False
                self.ran = False

            def is_closed(self):
                return False

            def is_running(self):
                return False

            def run_until_complete(self, coro):
                self.ran = True
                coro.close()

            def close(self):
                self.closed = True

        fake_loop = _FakeLoop()
        original_loop = worker_module._WORKER_LOOP
        worker_module._WORKER_LOOP = fake_loop
        with patch.object(worker_module, "_is_non_fork_runtime", return_value=True):
            worker_module._shutdown_non_fork_runtime()

        try:
            self.assertTrue(fake_loop.ran)
            self.assertTrue(fake_loop.closed)
            self.assertIsNone(worker_module._WORKER_LOOP)
        finally:
            worker_module._WORKER_LOOP = original_loop


class CortexWorkerCloseBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_async_keeps_mongo_open_in_non_fork_runtime_when_redis_unavailable(self):
        close_mock = AsyncMock()
        with patch.object(worker_module, "install_builtin_bundles"), patch.object(
            worker_module,
            "_ensure_mongo_ready",
            new=AsyncMock(),
        ), patch.object(worker_module, "_is_non_fork_runtime", return_value=True), patch.object(
            worker_module,
            "get_redis_connection",
            return_value=None,
        ), patch.object(
            worker_module,
            "close_mongo_connection",
            new=close_mock,
        ):
            await worker_module.process_cortex_run_async(ingress={}, raw_event={})

        close_mock.assert_not_awaited()

    async def test_process_async_keeps_mongo_open_in_fork_runtime_when_redis_unavailable(self):
        close_mock = AsyncMock()
        with patch.object(worker_module, "install_builtin_bundles"), patch.object(
            worker_module,
            "_ensure_mongo_ready",
            new=AsyncMock(),
        ), patch.object(worker_module, "_is_non_fork_runtime", return_value=False), patch.object(
            worker_module,
            "get_redis_connection",
            return_value=None,
        ), patch.object(
            worker_module,
            "close_mongo_connection",
            new=close_mock,
        ):
            await worker_module.process_cortex_run_async(ingress={}, raw_event={})

        close_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
