import unittest
from unittest.mock import AsyncMock, patch

from app.action_items import jobs as jobs_module


class ActionItemJobsTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_action_item_extraction_async_non_retryable_returns_result(self):
        with patch.object(
            jobs_module,
            "process_action_item_extraction_job_async",
            new=AsyncMock(
                return_value={
                    "ok": False,
                    "error": "invalid_payload",
                    "retryable": False,
                    "error_code": "invalid_payload",
                }
            ),
        ):
            result = await jobs_module.run_action_item_extraction_async({"source_type": ""})

        self.assertEqual(result.get("ok"), False)
        self.assertEqual(result.get("retryable"), False)

    async def test_run_action_item_extraction_async_retryable_raises(self):
        with patch.object(
            jobs_module,
            "process_action_item_extraction_job_async",
            new=AsyncMock(
                return_value={
                    "ok": False,
                    "error": "temporary_backend_error",
                    "retryable": True,
                }
            ),
        ):
            with self.assertRaises(RuntimeError):
                await jobs_module.run_action_item_extraction_async({"source_type": "task_comment_mutation"})


if __name__ == "__main__":
    unittest.main()
