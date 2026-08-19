import importlib
import unittest


schemas_module = importlib.import_module("app.action_items.schemas")


class ActionItemSchemaTests(unittest.TestCase):
    def test_remind_at_not_present_in_create_schema(self):
        fields = schemas_module.ActionItemCreateRequest.model_fields
        self.assertNotIn("remind_at", fields)

    def test_needs_followup_set_at_present_in_response_schema(self):
        fields = schemas_module.ActionItemResponse.model_fields
        self.assertIn("needs_followup_set_at", fields)


if __name__ == "__main__":
    unittest.main()
