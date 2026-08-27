import os
from collections.abc import Sequence

from dotenv import load_dotenv


load_dotenv()


DEFAULT_MODEL = os.getenv(
    "SUPPORTMASTER_MODEL",
    "gemini-3.5-flash",
)

# Backwards-compatible name used by the existing, default workflow instance.
MODEL_NAME = DEFAULT_MODEL

# These models support the text, tool-calling, and structured-output workflow
# SupportMaster requires. The default catalog is limited to Gemini 3.5 or
# newer per hackathon eligibility rules. Deployments can replace this catalog
# without a code change when their Gemini account exposes a different
# approved set (for example via SUPPORTMASTER_MODELS).
_DEFAULT_SUPPORTED_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
)


def supported_models() -> tuple[str, ...]:
    """Return the models the application may present in its picker.

    ``SUPPORTMASTER_MODELS`` is a comma-separated allow-list. The configured
    default is always included so an existing deployment remains runnable when
    it uses an organization-specific Gemini model name.
    """
    configured_models = os.getenv("SUPPORTMASTER_MODELS", "")
    models: Sequence[str] = (
        tuple(model.strip() for model in configured_models.split(",") if model.strip())
        if configured_models
        else _DEFAULT_SUPPORTED_MODELS
    )
    return tuple(dict.fromkeys((*models, DEFAULT_MODEL)))


def discovery_enabled() -> bool:
    """Global kill-switch for repository workspace discovery (Phase 32).

    Effective enablement also requires the tenant's
    ``organization_profile.discovery_policy.enabled``; both must be on.
    """
    return os.getenv("SUPPORTMASTER_DISCOVERY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def select_model(model_name: str | None = None) -> str:
    """Validate and return the model selected for one workflow execution."""
    selected_model = (model_name or DEFAULT_MODEL).strip()
    if not selected_model:
        raise ValueError("A SupportMaster model must be selected.")

    if selected_model not in supported_models():
        choices = ", ".join(supported_models())
        raise ValueError(
            f"Unsupported SupportMaster model: {selected_model!r}. "
            f"Choose one of: {choices}."
        )
    return selected_model
