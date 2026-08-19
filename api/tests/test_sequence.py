import importlib
import unittest
from unittest.mock import AsyncMock


sequence_module = importlib.import_module("app.core.sequence")


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_sequence_does_not_set_value_in_set_on_insert(self):
        collection = AsyncMock()
        collection.find_one_and_update = AsyncMock(
            return_value={"entity_type": "counter", "counter_key": "seq", "value": 1}
        )

        value = await sequence_module.next_sequence(
            collection,
            project_id="proj-1234-5678",
            sequence_name="action_item_display_key",
        )

        self.assertEqual(value, 1)
        collection.find_one_and_update.assert_awaited_once()
        _filter, update_doc = collection.find_one_and_update.await_args.args[:2]
        self.assertIn("$setOnInsert", update_doc)
        self.assertNotIn("value", update_doc["$setOnInsert"])
        self.assertEqual(update_doc["$inc"].get("value"), 1)


if __name__ == "__main__":
    unittest.main()

