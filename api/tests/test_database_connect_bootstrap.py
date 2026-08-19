import importlib
import unittest
from unittest.mock import AsyncMock, patch


database_module = importlib.import_module("app.core.database")


class _FakeAdmin:
    async def command(self, _name):
        return {"ok": 1}


class _FakeDatabase:
    name = "test-db"


class _FakeClient:
    def __init__(self) -> None:
        self.admin = _FakeAdmin()

    def get_default_database(self):
        return _FakeDatabase()

    def __getitem__(self, _name):
        return object()

    def close(self):
        return None


class DatabaseConnectBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_to_mongo_initializes_indexes_once_per_process(self):
        fake_client = _FakeClient()
        init_indexes_mock = AsyncMock()

        original_client = database_module.client
        original_database = database_module.database
        original_brain_db = database_module.brain_db
        original_ready = database_module._DATABASE_INDEXES_READY
        original_runtime_owner = database_module._CLIENT_RUNTIME_OWNER
        try:
            database_module.client = None
            database_module.database = None
            database_module.brain_db = None
            database_module._DATABASE_INDEXES_READY = False
            database_module._CLIENT_RUNTIME_OWNER = None

            with patch.object(
                database_module,
                "AsyncIOMotorClient",
                return_value=fake_client,
            ), patch.object(
                database_module,
                "init_database_indexes",
                new=init_indexes_mock,
            ):
                await database_module.connect_to_mongo(runtime_owner="unit-test-owner")
                await database_module.connect_to_mongo(runtime_owner="unit-test-owner")

            init_indexes_mock.assert_awaited_once()
            self.assertEqual(database_module._CLIENT_RUNTIME_OWNER, "unit-test-owner")
            await database_module.close_mongo_connection()
            self.assertIsNone(database_module._CLIENT_RUNTIME_OWNER)
        finally:
            database_module.client = original_client
            database_module.database = original_database
            database_module.brain_db = original_brain_db
            database_module._DATABASE_INDEXES_READY = original_ready
            database_module._CLIENT_RUNTIME_OWNER = original_runtime_owner

    async def test_runtime_owner_binding_helper(self):
        original_client = database_module.client
        original_database = database_module.database
        original_runtime_owner = database_module._CLIENT_RUNTIME_OWNER
        try:
            database_module.client = object()
            database_module.database = object()
            database_module._CLIENT_RUNTIME_OWNER = "owner-a"
            self.assertTrue(database_module.is_mongo_client_bound_to_current_loop())
            self.assertTrue(database_module.is_mongo_client_bound_to_current_loop(runtime_owner="owner-a"))
            self.assertFalse(database_module.is_mongo_client_bound_to_current_loop(runtime_owner="owner-b"))
        finally:
            database_module.client = original_client
            database_module.database = original_database
            database_module._CLIENT_RUNTIME_OWNER = original_runtime_owner


if __name__ == "__main__":
    unittest.main()
