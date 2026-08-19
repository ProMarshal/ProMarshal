import unittest
from pathlib import Path

from app.cadence.orchestrator import _reason_message as cadence_reason_message
from app.cortex.orchestrator import CortexOrchestrator


class ParsingHardeningFlagTests(unittest.TestCase):
    def test_legacy_rollout_flags_absent_from_runtime_paths(self):
        root = Path(__file__).resolve().parents[1] / "app"
        runtime_files = [
            root / "cadence" / "orchestrator.py",
            root / "cortex" / "orchestrator.py",
            root / "core" / "config.py",
            root / "main.py",
        ]
        content = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
        legacy_tokens = (
            "cadence_contradiction_parser_enabled",
            "global_parse_outcome_enabled",
            "global_ambiguity_gate_enabled",
            "global_reason_catalog_enabled",
            "cadence_clarification_state_enabled",
            "cadence_backlog_ambiguity_guard_enabled",
            "parsing_hardening_flag_snapshot",
        )
        for token in legacy_tokens:
            self.assertNotIn(token, content)

    def test_reason_catalog_lookup_is_shared_for_cadence_and_cortex(self):
        cadence_text = cadence_reason_message("required_tool_call_missing")
        cortex_text = CortexOrchestrator.__new__(CortexOrchestrator)._reason_message(
            "required_tool_call_missing"
        )
        self.assertEqual(cadence_text, "I cannot do that operation with the currently available tools.")
        self.assertEqual(cortex_text, "I cannot do that operation with the currently available tools.")

    def test_env_example_removes_rollout_flags(self):
        env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
        for key in (
            "CADENCE_CONTRADICTION_PARSER_ENABLED",
            "GLOBAL_PARSE_OUTCOME_ENABLED",
            "GLOBAL_AMBIGUITY_GATE_ENABLED",
            "GLOBAL_REASON_CATALOG_ENABLED",
            "CADENCE_CLARIFICATION_STATE_ENABLED",
            "CADENCE_BACKLOG_AMBIGUITY_GUARD_ENABLED",
        ):
            self.assertNotIn(key, env_example)


if __name__ == "__main__":
    unittest.main()
