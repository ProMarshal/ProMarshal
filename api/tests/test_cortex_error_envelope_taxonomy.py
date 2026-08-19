import unittest

from app.cortex.tools.error_envelope import build_tool_error


class CortexErrorEnvelopeTaxonomyTests(unittest.TestCase):
    def test_policy_error_class_is_preserved(self):
        envelope = build_tool_error(
            error_code="approval_required",
            error_class="policy",
            retryable=False,
            http_status=409,
            user_message="approval required",
            details={},
        )
        self.assertEqual(envelope.get("error_class"), "policy")

    def test_internal_error_class_is_preserved(self):
        envelope = build_tool_error(
            error_code="tool_execution_exception",
            error_class="internal",
            retryable=True,
            http_status=500,
            user_message="failed",
            details={},
        )
        self.assertEqual(envelope.get("error_class"), "internal")

    def test_unknown_error_class_still_normalizes_to_unknown(self):
        envelope = build_tool_error(
            error_code="x",
            error_class="not_a_real_class",
            retryable=False,
            http_status=400,
            user_message="invalid",
            details={},
        )
        self.assertEqual(envelope.get("error_class"), "unknown")


if __name__ == "__main__":
    unittest.main()

