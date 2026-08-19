import unittest
from pathlib import Path


class CortexLegacyFlowDecommissionTests(unittest.TestCase):
    def test_removed_legacy_quality_helpers_are_not_reintroduced(self):
        repo_root = Path(__file__).resolve().parents[1]
        executor_path = repo_root / "app" / "cortex" / "plans" / "executor.py"
        orchestrator_path = repo_root / "app" / "cortex" / "orchestrator.py"

        executor_text = executor_path.read_text(encoding="utf-8")
        orchestrator_text = orchestrator_path.read_text(encoding="utf-8")

        forbidden_executor_symbols = [
            "def _ensure_write_argument_quality(",
            "def _attempt_write_args_repair(",
            "def _prepare_non_idempotent_args(",
            "def _tool_required_arg_fields(",
        ]
        forbidden_orchestrator_symbols = [
            "def _validate_tool_args_for_execution(",
        ]

        for symbol in forbidden_executor_symbols:
            self.assertNotIn(
                symbol,
                executor_text,
                msg=f"Legacy executor helper reintroduced: {symbol}",
            )
        for symbol in forbidden_orchestrator_symbols:
            self.assertNotIn(
                symbol,
                orchestrator_text,
                msg=f"Legacy orchestrator helper reintroduced: {symbol}",
            )

        self.assertIn(
            "def _prepare_tool_args_for_orchestrator_execution(",
            orchestrator_text,
            msg="Canonical orchestrator prepare helper is missing.",
        )


if __name__ == "__main__":
    unittest.main()
