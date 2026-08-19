import unittest

from app.core.queue_runtime import (
    QUEUE_ACTION_ITEM_EXTRACTION,
    QUEUE_CADENCE_DM,
    QUEUE_CHAT_COMMANDS,
    QUEUE_CHAT_INTERACTIONS,
    QUEUE_CORTEX_RUNS,
    QUEUE_WORKITEM_EVENTS,
    get_queue_runtime,
    is_dramatiq_enabled_for_queue,
    is_dramatiq_runtime_enabled,
)


class QueueRuntimeSelectionTests(unittest.TestCase):
    def test_runtime_is_dramatiq_only(self):
        self.assertEqual(get_queue_runtime(), "dramatiq")
        self.assertTrue(is_dramatiq_runtime_enabled())

    def test_supported_queues_are_dramatiq_enabled(self):
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_CORTEX_RUNS))
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_WORKITEM_EVENTS))
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_CHAT_INTERACTIONS))
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_CHAT_COMMANDS))
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_ACTION_ITEM_EXTRACTION))
        self.assertTrue(is_dramatiq_enabled_for_queue(QUEUE_CADENCE_DM))

    def test_unknown_queue_is_not_enabled(self):
        self.assertFalse(is_dramatiq_enabled_for_queue("unknown_queue"))


if __name__ == "__main__":
    unittest.main()
