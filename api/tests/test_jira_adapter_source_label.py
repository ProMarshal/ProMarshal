import importlib
import unittest

from app.tasks.providers.jira_adapter import JiraAdapter


class JiraAdapterSourceLabelTests(unittest.TestCase):
    def test_legacy_shim_import_removed(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("app.tasks.jira_adapter")

    def _build_adapter(self) -> JiraAdapter:
        project = {
            "integrations": {
                "jira": {
                    "cloud_id": "cloud-1",
                    "site_url": "https://example.atlassian.net",
                    "access_token": "",
                    "refresh_token": None,
                }
            }
        }
        return JiraAdapter(project, access_token_override="token")

    def test_display_source_label_maps_to_promarshal(self):
        adapter = self._build_adapter()
        self.assertEqual(adapter._display_source_label("cortex"), "ProMarshal")
        self.assertEqual(adapter._display_source_label("cadence"), "ProMarshal")
        self.assertEqual(adapter._display_source_label("promarshal"), "ProMarshal")

    def test_display_source_label_empty_when_missing(self):
        adapter = self._build_adapter()
        self.assertIsNone(adapter._display_source_label(""))
        self.assertIsNone(adapter._display_source_label(None))


if __name__ == "__main__":
    unittest.main()
