import unittest

from app.agent_runtime.gateway_model_provider import _message_tool_calls_to_gateway


class GatewayToolCallMessageMappingTests(unittest.TestCase):
    def test_maps_openai_style_tool_calls(self):
        parsed = _message_tool_calls_to_gateway(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "jira_search_work_items",
                        "arguments": '{"status":"in_progress"}',
                    },
                }
            ]
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].call_id, "call_1")
        self.assertEqual(parsed[0].name, "jira_search_work_items")
        self.assertEqual(parsed[0].arguments_json, '{"status":"in_progress"}')


if __name__ == "__main__":
    unittest.main()

