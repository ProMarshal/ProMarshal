import unittest

from app.cortex.prompt_builder import CortexPromptBuilder, PromptBuildInput


class CortexPromptBuilderCapabilityTests(unittest.TestCase):
    def test_fastpath_compose_mode_uses_read_capability(self):
        builder = CortexPromptBuilder()
        request = PromptBuildInput(
            project_context={},
            session_context={},
            user_message="compose",
            visible_tools=[],
            metadata={"fastpath_compose_mode": True},
        )
        capability = builder._resolve_prompt_capability(request)
        self.assertEqual(capability, "task_assistant_read")

    def test_default_mode_uses_task_assistant_capability(self):
        builder = CortexPromptBuilder()
        request = PromptBuildInput(
            project_context={},
            session_context={},
            user_message="normal",
            visible_tools=[],
            metadata={},
        )
        capability = builder._resolve_prompt_capability(request)
        self.assertEqual(capability, "task_assistant")


if __name__ == "__main__":
    unittest.main()
