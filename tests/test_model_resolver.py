import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from supportmaster.model_resolver import ModelResolver, ModelConfigError
from supportmaster.web import SupportMasterHandler


class MockSupportMasterHandler(SupportMasterHandler):
    def __init__(self, path, method="GET", body=b"", headers=None):
        self.path = path
        self.command = method
        self.rfile = None
        self.wfile = sys.modules["io"].BytesIO()
        self.headers = headers or {}
        self.response_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.response_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def log_message(self, format, *args):
        pass


class ModelResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "models.yaml"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_config_loading(self):
        resolver = ModelResolver(self.config_path)  # File does not exist -> uses defaults
        self.assertEqual(resolver.default_provider, "vertex_ai")
        self.assertEqual(resolver.default_model, "gemini-3.5-flash")
        self.assertEqual(len(resolver.fallback_chain), 3)
        self.assertEqual(resolver.fallback_chain[0]["provider"], "vertex_ai")
        self.assertEqual(resolver.fallback_chain[1]["provider"], "gemini_api")
        self.assertEqual(resolver.fallback_chain[2]["provider"], "openrouter")

    def test_vertex_ai_resolution(self):
        resolver = ModelResolver(self.config_path)
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=False):
            model = resolver.resolve_model("gemini-3.5-flash", provider="vertex_ai")
            self.assertEqual(model, "gemini-3.5-flash")
            self.assertEqual(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"), "true")

    def test_gemini_api_resolution(self):
        resolver = ModelResolver(self.config_path)
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}, clear=False):
            model = resolver.resolve_model("gemini-3.5-flash", provider="gemini_api")
            self.assertEqual(model, "gemini-3.5-flash")
            self.assertEqual(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"), "false")

    def test_missing_key_fail_closed(self):
        resolver = ModelResolver(self.config_path)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModelConfigError) as ctx:
                resolver.resolve_model("claude-3-5-sonnet-20241022", provider="anthropic")
            self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_openrouter_resolution_and_env_check(self):
        mock_litellm = MagicMock()
        mock_litellm_instance = MagicMock()
        mock_litellm.return_value = mock_litellm_instance

        fake_module = MagicMock()
        fake_module.LiteLlm = mock_litellm

        with patch.dict(sys.modules, {"google.adk.models.lite_llm": fake_module}):
            resolver = ModelResolver(self.config_path)

            # 1. When OPENROUTER_API_KEY is absent, provider is not available
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(resolver.is_provider_available("openrouter"))
                with self.assertRaises(ModelConfigError) as ctx:
                    resolver.resolve_model("google/gemini-2.5-flash", provider="openrouter")
                self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

            # 2. When OPENROUTER_API_KEY is present, provider is available and resolves model
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-key"}, clear=False):
                self.assertTrue(resolver.is_provider_available("openrouter"))
                res = resolver.resolve_model("google/gemini-2.5-flash", provider="openrouter")
                self.assertEqual(res, mock_litellm_instance)
                mock_litellm.assert_called_with(model="openrouter/google/gemini-2.5-flash")

    def test_fallback_chain_simulation(self):
        resolver = ModelResolver(self.config_path)
        mock_litellm = MagicMock()
        mock_litellm_instance = MagicMock()
        mock_litellm.return_value = mock_litellm_instance

        fake_module = MagicMock()
        fake_module.LiteLlm = mock_litellm

        with patch.dict(sys.modules, {"google.adk.models.lite_llm": fake_module}):
            with patch.dict(os.environ, {
                "GOOGLE_CLOUD_PROJECT": "demo-proj",
                "GOOGLE_API_KEY": "demo-key",
                "OPENROUTER_API_KEY": "sk-or-key"
            }, clear=True):
                resolved_attempts = []
                for entry in resolver.fallback_chain:
                    provider = entry["provider"]
                    model_id = entry["model"]
                    try:
                        # Simulate failure on first two providers (429 quota exhaustion)
                        if provider in {"vertex_ai", "gemini_api"}:
                            raise RuntimeError(f"{provider} returned 429 RESOURCE_EXHAUSTED")
                        resolved = resolver.resolve_model(model_id, provider=provider)
                        resolved_attempts.append((provider, resolved))
                        break
                    except Exception:
                        continue

                self.assertEqual(len(resolved_attempts), 1)
                self.assertEqual(resolved_attempts[0][0], "openrouter")
                self.assertEqual(resolved_attempts[0][1], mock_litellm_instance)

    def test_anthropic_litellm_mock(self):
        mock_litellm = MagicMock()
        mock_litellm_instance = MagicMock()
        mock_litellm.return_value = mock_litellm_instance

        fake_module = MagicMock()
        fake_module.LiteLlm = mock_litellm

        with patch.dict(sys.modules, {"google.adk.models.lite_llm": fake_module}):
            resolver = ModelResolver(self.config_path)
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}, clear=False):
                res = resolver.resolve_model("claude-3-5-sonnet-20241022", provider="anthropic")
                self.assertEqual(res, mock_litellm_instance)
                mock_litellm.assert_called_with(model="anthropic/claude-3-5-sonnet-20241022")

    def test_available_models_endpoint(self):
        headers = {}
        handler = MockSupportMasterHandler("/api/models/available", method="GET", headers=headers)
        handler.do_GET()
        self.assertEqual(handler.response_code, 200)
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("models", data)
        self.assertIn("default_model", data)
        self.assertEqual(data["default_provider"], "vertex_ai")
        # Ensure default model is present
        self.assertTrue(any(m["id"] == "gemini-3.5-flash" for m in data["models"]))


if __name__ == "__main__":
    unittest.main()
