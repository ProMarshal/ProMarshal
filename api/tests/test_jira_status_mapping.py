import unittest

from app.integrations.jira.oauth import JiraOAuthService


class JiraStatusMappingTests(unittest.TestCase):
    def test_in_review_target_mapping_exists(self):
        service = JiraOAuthService()
        mapping = service._status_mapping_for_target()
        self.assertIn("in_review", mapping)
        self.assertIn("In Review", mapping.get("in_review") or [])


if __name__ == "__main__":
    unittest.main()

