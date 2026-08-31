"""SupportMaster Configurable Model Provider Layer (Phase 46).

Resolves models for Google ADK workflows across Vertex AI (default),
Google AI Studio (Gemini API), Anthropic, OpenAI, and OpenRouter via LiteLLM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("supportmaster.model_resolver")

DEFAULT_CONFIG_PATH = Path("config/models.yaml")

DEFAULT_FALLBACK_CONFIG: dict[str, Any] = {
    "default_provider": "vertex_ai",
    "default_model": "gemini-3.5-flash",
    "fallback_chain": [
        {"provider": "vertex_ai", "model": "gemini-3.5-flash"},
        {"provider": "gemini_api", "model": "gemini-3.5-flash"},
        {"provider": "openrouter", "model": "google/gemini-2.5-flash"},
    ],
    "providers": {
        "vertex_ai": {
            "project_env": "GOOGLE_CLOUD_PROJECT",
            "location_env": "GOOGLE_CLOUD_LOCATION",
            "supported_models": [
                {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash (Vertex AI) — default"},
                {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite (Vertex AI)"},
                {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash (Vertex AI)"},
            ],
        },
        "gemini_api": {
            "api_key_env": "GOOGLE_API_KEY",
            "supported_models": [
                {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash (Google AI Studio)"},
                {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite (Google AI Studio)"},
            ],
        },
        "openrouter": {
            "api_key_env": "OPENROUTER_API_KEY",
            "supported_models": [
                {"id": "google/gemini-2.5-flash", "label": "Gemini 3.5 Flash (via OpenRouter) — fallback"},
                {"id": "google/gemini-2.0-flash-001", "label": "Gemini 2.0 Flash (via OpenRouter) — fallback"},
                {"id": "anthropic/claude-3.5-sonnet", "label": "Claude 3.5 Sonnet (OpenRouter via LiteLLM)"},
                {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B (OpenRouter via LiteLLM)"},
            ],
        },
        "anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "supported_models": [
                {"id": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet (Anthropic via LiteLLM)"},
                {"id": "claude-3-5-haiku-20241022", "label": "Claude 3.5 Haiku (Anthropic via LiteLLM)"},
            ],
        },
        "openai": {
            "api_key_env": "OPENAI_API_KEY",
            "supported_models": [
                {"id": "gpt-4o", "label": "GPT-4o (OpenAI via LiteLLM)"},
                {"id": "gpt-4o-mini", "label": "GPT-4o Mini (OpenAI via LiteLLM)"},
            ],
        },
    },
}


class ModelConfigError(ValueError):
    """Raised when a selected provider or model configuration is invalid or missing credentials."""
    pass


class ModelResolver:
    """Config-driven resolver that produces ADK-compatible models and executes fallback chains."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(
            config_path
            or os.getenv("SUPPORTMASTER_MODEL_CONFIG", DEFAULT_CONFIG_PATH)
        )
        self._config: dict[str, Any] = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        return loaded
            except Exception as e:
                logger.warning(f"Failed to read model config from {self.config_path}: {e}. Using defaults.")
        return DEFAULT_FALLBACK_CONFIG

    @property
    def default_provider(self) -> str:
        return self._config.get("default_provider", "vertex_ai")

    @property
    def default_model(self) -> str:
        return self._config.get("default_model", "gemini-3.5-flash")

    @property
    def fallback_chain(self) -> list[dict[str, str]]:
        return list(self._config.get("fallback_chain", []))

    def validate_provider_env(self, provider: str) -> None:
        """Validate that all required environment variables for the provider are set."""
        providers = self._config.get("providers", {})
        provider_cfg = providers.get(provider)
        if not provider_cfg:
            raise ModelConfigError(f"Unknown provider '{provider}'. Configured providers: {list(providers.keys())}")

        missing = []
        if provider == "vertex_ai":
            # Vertex AI can use GOOGLE_CLOUD_PROJECT or Application Default Credentials (ADC)
            # If project_env is defined, check it, but allow standard ADC / GOOGLE_GENAI_USE_VERTEXAI
            project_env = provider_cfg.get("project_env", "GOOGLE_CLOUD_PROJECT")
            if not os.getenv(project_env) and not os.getenv("GOOGLE_GENAI_USE_VERTEXAI"):
                # Warning only if not set, since local gcloud auth ADC might still work
                pass
        else:
            api_key_env = provider_cfg.get("api_key_env")
            if api_key_env and not os.getenv(api_key_env):
                missing.append(api_key_env)

        if missing:
            raise ModelConfigError(
                f"Provider '{provider}' requires environment variable(s) {missing} to be set."
            )

    def is_provider_available(self, provider: str) -> bool:
        """Return True if the provider's required credentials exist in the runtime environment."""
        try:
            self.validate_provider_env(provider)
            return True
        except ModelConfigError:
            return False

    def get_available_models(self) -> list[dict[str, Any]]:
        """Return all supported models with availability flags based on active environment credentials."""
        results = []
        providers = self._config.get("providers", {})
        for provider_name, provider_cfg in providers.items():
            available = self.is_provider_available(provider_name)
            for model_info in provider_cfg.get("supported_models", []):
                model_id = model_info["id"]
                label = model_info.get("label", f"{model_id} ({provider_name})")
                is_default = (provider_name == self.default_provider and model_id == self.default_model)
                results.append({
                    "id": model_id,
                    "provider": provider_name,
                    "label": label,
                    "is_default": is_default,
                    "available": available,
                })
        return results

    def resolve_model(
        self,
        model_name: str | None = None,
        provider: str | None = None,
    ) -> Any:
        """Resolve a model name or provider spec into an ADK-compatible model target.

        Returns:
            - A plain string for Vertex AI or Gemini API.
            - A `google.adk.models.lite_llm.LiteLlm` instance for non-Google providers.
        """
        # Parse composite model strings like "anthropic:claude-3-5-sonnet-20241022" or "claude-3-5-sonnet-20241022"
        target_model = model_name or self.default_model
        target_provider = provider or self.default_provider

        if target_model and ":" in target_model:
            parts = target_model.split(":", 1)
            target_provider = parts[0]
            target_model = parts[1]
        elif target_model and "/" in target_model and not target_model.startswith("gemini"):
            # e.g. "anthropic/claude-3.5-sonnet" or "openai/gpt-4o"
            prefix = target_model.split("/")[0]
            if prefix in self._config.get("providers", {}):
                target_provider = prefix
                target_model = target_model.split("/", 1)[1]

        # Validate that the selected provider has required credentials
        self.validate_provider_env(target_provider)

        if target_provider == "vertex_ai":
            # Vertex AI uses native ADK Gemini string with Vertex routing
            # Set GOOGLE_GENAI_USE_VERTEXAI so google-genai routes to Vertex AI endpoint
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
            logger.info(f"Resolved model '{target_model}' via provider 'vertex_ai'")
            return target_model

        if target_provider == "gemini_api":
            # Google AI Studio (Developer API) uses plain Gemini string
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
            logger.info(f"Resolved model '{target_model}' via provider 'gemini_api'")
            return target_model

        # Non-Google providers via ADK's LiteLlm wrapper
        try:
            from google.adk.models.lite_llm import LiteLlm
        except ImportError as exc:
            raise ModelConfigError(
                f"Provider '{target_provider}' requires LiteLLM support in ADK. "
                "Install with: pip install google-adk[extensions] or pip install litellm"
            ) from exc

        # Format LiteLLM model identifier
        if target_provider == "anthropic":
            litellm_model = f"anthropic/{target_model.removeprefix('anthropic/')}"
        elif target_provider == "openai":
            litellm_model = f"openai/{target_model.removeprefix('openai/')}"
        elif target_provider == "openrouter":
            litellm_model = f"openrouter/{target_model.removeprefix('openrouter/')}"
        else:
            litellm_model = f"{target_provider}/{target_model}"

        logger.info(f"Resolved model '{litellm_model}' via provider '{target_provider}' using LiteLlm")
        return LiteLlm(model=litellm_model)


# Global singleton instance
MODEL_RESOLVER = ModelResolver()
