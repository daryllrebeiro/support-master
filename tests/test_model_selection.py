import unittest

from supportmaster.agent import create_root_agent, root_agent
from supportmaster.config import DEFAULT_MODEL, select_model, supported_models
from supportmaster.web import MOCK_JIRA_ISSUE, render_page


class ModelSelectionTests(unittest.TestCase):
    def test_default_model_is_an_allowed_picker_option(self) -> None:
        self.assertIn(DEFAULT_MODEL, supported_models())
        self.assertEqual(select_model(), DEFAULT_MODEL)

    def test_gemini_3_flash_models_are_picker_options(self) -> None:
        self.assertTrue(
            {
                "gemini-3.5-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.6-flash",
            }.issubset(supported_models())
        )

    def test_selected_model_is_applied_to_every_workflow_agent(self) -> None:
        model_name = "gemini-3.6-flash"
        workflow = create_root_agent(model_name)

        self.assertIsNot(workflow, root_agent)
        self.assertTrue(workflow.graph)
        selected_agents = [
            node for node in workflow.graph.nodes if hasattr(node, "model")
        ]
        default_agents = [
            node for node in root_agent.graph.nodes if hasattr(node, "model")
        ]
        self.assertTrue(selected_agents)
        self.assertTrue(all(agent.model == model_name for agent in selected_agents))
        self.assertTrue(all(agent.model == DEFAULT_MODEL for agent in default_agents))

    def test_unapproved_model_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_root_agent("gemini-image-model")

    def test_model_picker_renders_all_approved_models(self) -> None:
        page = render_page(DEFAULT_MODEL)

        self.assertIn('id="model"', page)
        self.assertIn("Run SupportMaster", page)
        self.assertIn(MOCK_JIRA_ISSUE.splitlines()[0], page)
        for model_name in supported_models():
            self.assertIn(f'value="{model_name}"', page)


if __name__ == "__main__":
    unittest.main()
