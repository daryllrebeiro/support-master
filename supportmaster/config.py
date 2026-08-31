import os
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv

from .model_resolver import MODEL_RESOLVER, ModelResolver

load_dotenv()


DEFAULT_MODEL = os.getenv(
    "SUPPORTMASTER_MODEL",
    MODEL_RESOLVER.default_model,
)

# Backwards-compatible name used by the existing, default workflow instance.
MODEL_NAME = DEFAULT_MODEL

_DEFAULT_SUPPORTED_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
)


def supported_models() -> tuple[str, ...]:
    """Return the models the application may present in its picker.

    Sources choices from the model resolver's configured catalog as well as
    any legacy SUPPORTMASTER_MODELS environment variable allow-list.
    """
    configured_models = os.getenv("SUPPORTMASTER_MODELS", "")
    if configured_models:
        models = [m.strip() for m in configured_models.split(",") if m.strip()]
    else:
        available = [m["id"] for m in MODEL_RESOLVER.get_available_models() if m.get("available", True)]
        models = available or list(_DEFAULT_SUPPORTED_MODELS)

    if DEFAULT_MODEL not in models:
        models.insert(0, DEFAULT_MODEL)
    return tuple(dict.fromkeys(models))


def discovery_enabled() -> bool:
    """Global kill-switch for repository workspace discovery (Phase 32)."""
    return os.getenv("SUPPORTMASTER_DISCOVERY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def select_model(model_name: str | None = None) -> Any:
    """Validate and return the model selected for one workflow execution."""
    selected_model = (model_name or DEFAULT_MODEL).strip()
    if not selected_model:
        raise ValueError("A SupportMaster model must be selected.")

    supported = supported_models()
    base_id = selected_model.split(":")[-1].split("/")[-1]
    if selected_model not in supported and base_id not in supported:
        choices = ", ".join(supported)
        raise ValueError(
            f"Unsupported SupportMaster model: {selected_model!r}. "
            f"Choose one of: {choices}."
        )

    return MODEL_RESOLVER.resolve_model(selected_model)
