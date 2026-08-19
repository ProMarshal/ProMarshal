import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.cortex.quality.models import OperationProfile
from app.cortex.quality.policy import resolve_field_policy
from app.cortex.quality.policy_registry import (
    _reset_quality_policy_cache_for_tests,
    load_quality_policy_artifact,
    quality_policy_runtime_metadata,
)


class CortexQualityPolicyRegistryTests(unittest.TestCase):
    def setUp(self):
        self._orig_enabled = bool(getattr(settings, "cortex_quality_policy_registry_enabled", False))
        self._orig_path = str(getattr(settings, "cortex_quality_policy_artifact_path", "") or "")
        self._orig_fail_fast = bool(getattr(settings, "cortex_quality_policy_fail_fast", False))
        _reset_quality_policy_cache_for_tests()

    def tearDown(self):
        settings.cortex_quality_policy_registry_enabled = self._orig_enabled
        settings.cortex_quality_policy_artifact_path = self._orig_path
        settings.cortex_quality_policy_fail_fast = self._orig_fail_fast
        _reset_quality_policy_cache_for_tests()

    def test_loader_returns_none_when_registry_disabled(self):
        settings.cortex_quality_policy_registry_enabled = False
        artifact = load_quality_policy_artifact(force_reload=True)
        self.assertIsNone(artifact)

    def test_resolve_policy_applies_artifact_override(self):
        path = Path(__file__).resolve().parent / f"_tmp_quality_policy_{uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "field_class_defaults": {
                        "comment_like": {
                            "min_chars": 3,
                            "semantic_when": "inferred_only",
                        }
                    },
                    "tool_field_overrides": {
                        "jira_add_comment": {
                            "comment": {
                                "min_chars": 12,
                                "assessor_fail_mode": "fail_closed",
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        try:
            settings.cortex_quality_policy_registry_enabled = True
            settings.cortex_quality_policy_artifact_path = str(path)
            settings.cortex_quality_policy_fail_fast = True
            _reset_quality_policy_cache_for_tests()

            effective = resolve_field_policy(
                tool_name="jira_add_comment",
                field_name="comment",
                operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
            )
            self.assertEqual(effective.get("min_chars"), 12)
            self.assertEqual(effective.get("assessor_fail_mode"), "fail_closed")
            self.assertEqual(effective.get("semantic_when"), "inferred_only")
            metadata = quality_policy_runtime_metadata()
            self.assertTrue(metadata.get("quality_policy_loaded"))
            self.assertEqual(metadata.get("quality_policy_schema_version"), "1.0")
        finally:
            try:
                os.remove(str(path))
            except OSError:
                pass

    def test_loader_rejects_incompatible_schema_version_when_fail_fast(self):
        path = Path(__file__).resolve().parent / f"_tmp_quality_policy_invalid_{uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "field_class_defaults": {},
                    "tool_field_overrides": {},
                }
            ),
            encoding="utf-8",
        )

        try:
            settings.cortex_quality_policy_registry_enabled = True
            settings.cortex_quality_policy_artifact_path = str(path)
            settings.cortex_quality_policy_fail_fast = True
            _reset_quality_policy_cache_for_tests()

            with self.assertRaises(RuntimeError):
                load_quality_policy_artifact(force_reload=True)
        finally:
            try:
                os.remove(str(path))
            except OSError:
                pass

    def test_loader_rejects_unknown_field_class_when_fail_fast(self):
        path = Path(__file__).resolve().parent / f"_tmp_quality_policy_unknown_class_{uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "field_class_defaults": {
                        "comment_like_plus": {"min_chars": 2}
                    },
                    "tool_field_overrides": {},
                }
            ),
            encoding="utf-8",
        )

        try:
            settings.cortex_quality_policy_registry_enabled = True
            settings.cortex_quality_policy_artifact_path = str(path)
            settings.cortex_quality_policy_fail_fast = True
            _reset_quality_policy_cache_for_tests()

            with self.assertRaises(RuntimeError):
                load_quality_policy_artifact(force_reload=True)
        finally:
            try:
                os.remove(str(path))
            except OSError:
                pass

    def test_field_class_default_applies_to_other_tools(self):
        path = Path(__file__).resolve().parent / f"_tmp_quality_policy_description_{uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "field_class_defaults": {
                        "description_like": {
                            "min_chars": 7,
                            "assessor_fail_mode": "allow_if_deterministic_pass",
                        }
                    },
                    "tool_field_overrides": {},
                }
            ),
            encoding="utf-8",
        )

        try:
            settings.cortex_quality_policy_registry_enabled = True
            settings.cortex_quality_policy_artifact_path = str(path)
            settings.cortex_quality_policy_fail_fast = True
            _reset_quality_policy_cache_for_tests()

            effective = resolve_field_policy(
                tool_name="jira_update_description",
                field_name="description",
                operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
            )
            self.assertEqual(effective.get("min_chars"), 7)
            self.assertEqual(
                effective.get("assessor_fail_mode"),
                "allow_if_deterministic_pass",
            )
        finally:
            try:
                os.remove(str(path))
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
