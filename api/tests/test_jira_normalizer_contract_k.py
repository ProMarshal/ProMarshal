import unittest
from importlib import util
from pathlib import Path
import sys
import types


if "bson" not in sys.modules:
    bson_module = types.ModuleType("bson")

    class _ObjectId:
        pass

    bson_module.ObjectId = _ObjectId
    sys.modules["bson"] = bson_module


_NORMALIZER_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "integrations"
    / "jira"
    / "normalizer.py"
)
_SPEC = util.spec_from_file_location("jira_normalizer_module", _NORMALIZER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load Jira normalizer module at {_NORMALIZER_PATH}")
_MODULE = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
JiraNormalizer = _MODULE.JiraNormalizer


class JiraNormalizerContractKTests(unittest.TestCase):
    def test_normalized_task_includes_contract_k_fields(self):
        normalizer = JiraNormalizer(
            cloud_id="cloud-1",
            site_url="https://example.atlassian.net",
        )
        raw = {
            "key": "PROJ-42",
            "fields": {
                "summary": "Ship release note updates",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {
                    "emailAddress": "owner@example.com",
                    "displayName": "Owner",
                    "accountId": "acct-1",
                },
                "comment": {"comments": []},
            },
        }
        normalized = normalizer.normalize_task(raw)
        self.assertEqual(normalized.get("provider"), "jira")
        self.assertEqual(normalized.get("entity_type"), "work_item")
        self.assertEqual(normalized.get("source"), "jira")
        self.assertEqual(normalized.get("schema_version"), "1.0")
        self.assertEqual(normalized.get("provider_type"), None)
        self.assertEqual(normalized.get("work_item_type"), "custom")
        assignee = normalized.get("assignee") or {}
        self.assertEqual(assignee.get("email"), "owner@example.com")
        self.assertEqual(assignee.get("name"), "Owner")
        self.assertEqual(assignee.get("account_id"), "acct-1")

    def test_normalized_task_classifies_provider_type_to_work_item_type(self):
        normalizer = JiraNormalizer(
            cloud_id="cloud-1",
            site_url="https://example.atlassian.net",
        )
        raw = {
            "key": "PROJ-99",
            "fields": {
                "summary": "Fix sign-in bug",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Bug"},
                "comment": {"comments": []},
            },
        }
        normalized = normalizer.normalize_task(raw)
        self.assertEqual(normalized.get("provider_type"), "Bug")
        self.assertEqual(normalized.get("work_item_type"), "defect")


if __name__ == "__main__":
    unittest.main()
