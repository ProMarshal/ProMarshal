import importlib
import unittest


database_module = importlib.import_module("app.core.database")


class _FakeCollection:
    def __init__(self) -> None:
        self.created_calls = []
        self.drop_calls = []

    async def create_index(self, keys, **kwargs):
        self.created_calls.append((keys, kwargs))
        return "idx"

    async def drop_index(self, name):
        self.drop_calls.append(name)
        return None

    async def list_indexes(self):
        if False:
            yield {}


class DatabaseIndexHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_named_index_omits_expire_after_seconds_when_none(self):
        collection = _FakeCollection()

        await database_module._ensure_named_index(
            collection,
            existing_indexes={},
            keys=[("entity_type", 1), ("status", 1)],
            name="action_item_entity_status_idx",
            partial_filter_expression={"entity_type": "action_item"},
            expire_after_seconds=None,
        )

        self.assertEqual(len(collection.created_calls), 1)
        _keys, kwargs = collection.created_calls[0]
        self.assertNotIn("expireAfterSeconds", kwargs)

    async def test_ensure_named_index_includes_expire_after_seconds_for_ttl(self):
        collection = _FakeCollection()

        await database_module._ensure_named_index(
            collection,
            existing_indexes={},
            keys=[("expires_at", 1)],
            name="ttl_idx",
            expire_after_seconds=0,
        )

        self.assertEqual(len(collection.created_calls), 1)
        _keys, kwargs = collection.created_calls[0]
        self.assertEqual(kwargs.get("expireAfterSeconds"), 0)


if __name__ == "__main__":
    unittest.main()

