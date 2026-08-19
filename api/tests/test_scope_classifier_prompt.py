import unittest

from app.domain.scope.classifier import ScopeLlmClassifier


class ScopeClassifierPromptTests(unittest.TestCase):
    def test_prompt_contains_schema_and_registry_hints(self):
        classifier = ScopeLlmClassifier()
        prompt = classifier._build_prompt()
        self.assertIn('"scope_classification"', prompt)
        self.assertIn('"detail_classification"', prompt)
        self.assertIn("Project entities/hints include:", prompt)
        self.assertIn("Project analytics/hints include:", prompt)
        self.assertIn("burnout", prompt.lower())
        self.assertIn("task", prompt.lower())
        self.assertIn("check with a team/group for project updates", prompt)


if __name__ == "__main__":
    unittest.main()
