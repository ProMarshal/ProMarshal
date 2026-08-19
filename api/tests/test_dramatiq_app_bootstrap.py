import importlib
import unittest
from unittest.mock import patch


dramatiq_app = importlib.import_module("app.workers.dramatiq_app")


class _FakeBroker:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.middleware = []

    def add_middleware(self, value) -> None:
        self.middleware.append(value)


class DramatiqBootstrapTests(unittest.TestCase):
    def test_build_broker_configures_expected_middleware(self):
        with patch.object(dramatiq_app, "dramatiq", object()), patch.object(
            dramatiq_app,
            "RedisBroker",
            _FakeBroker,
        ), patch.object(
            dramatiq_app,
            "AsyncIO",
            lambda: "asyncio-mw",
        ), patch.object(
            dramatiq_app,
            "Retries",
            lambda: "retries-mw",
        ), patch.object(
            dramatiq_app,
            "TimeLimit",
            lambda: "timelimit-mw",
        ), patch.object(
            dramatiq_app,
            "initialize_redis",
        ):
            broker = dramatiq_app.build_broker()

        self.assertIsInstance(broker, _FakeBroker)
        self.assertEqual(broker.middleware, ["asyncio-mw", "retries-mw", "timelimit-mw"])


if __name__ == "__main__":
    unittest.main()
