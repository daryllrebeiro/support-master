"""Gemma-powered ticket triage: cheap classification before reasoning stages.

SUPPORTMASTER BONUS INTEGRATION
Uses Gemma (via the google-genai SDK against AI Studio or a Vertex AI Model
Garden endpoint) for high-volume, low-risk triage: severity, category, and
duplicate suspicion. Reasoning-heavy stages remain on Gemini.

Safety properties:
- Triage output is ADVISORY ONLY. No gate reads it; no authorization derives
  from it.
- Fail-open: if Gemma is unavailable (no API key, network error, malformed
  output), a deterministic keyword heuristic classifies instead and the result
  is marked ``engine="heuristic"``.
"""

from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import BaseModel, Field

TRIAGE_MODEL = os.getenv("SUPPORTMASTER_TRIAGE_MODEL", "gemma-3-27b-it")

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

_SEVERITY_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

_CRITICAL_PATTERNS = (
    r"data loss",
    r"security",
    r"breach",
    r"outage",
    r"production down",
    r"cannot log?in",
    r"unauthorized",
)
_HIGH_PATTERNS = (
    r"outofmemory",
    r"crash",
    r"timeout",
    r"500 error",
    r"exception",
    r"fail(?:ure|ed|ing)",
    r"corrupt",
)


class TriageResult(BaseModel):
    """Advisory triage classification for one inbound ticket."""

    severity: Severity = "MEDIUM"
    category: str = "general"
    duplicate_suspected: bool = False
    rationale: str = ""
    engine: Literal["gemma", "heuristic"] = "heuristic"
    model_name: str | None = None


def classify_heuristic(text: str) -> TriageResult:
    """Deterministic keyword fallback used whenever Gemma is unavailable."""
    lowered = text.lower()
    severity: Severity = "MEDIUM"
    if any(re.search(pattern, lowered) for pattern in _CRITICAL_PATTERNS):
        severity = "CRITICAL"
    elif any(re.search(pattern, lowered) for pattern in _HIGH_PATTERNS):
        severity = "HIGH"
    elif not lowered.strip():
        severity = "LOW"
    duplicate_suspected = bool(
        re.search(r"\b(already reported|duplicate of|same issue|reopened?)\b", lowered)
    )
    return TriageResult(
        severity=severity,
        category=(
            "bug_report"
            if re.search(r"(error|exception|fail|outofmemory|crash)", lowered)
            else "general"
        ),
        duplicate_suspected=duplicate_suspected,
        rationale="Keyword heuristic (Gemma unavailable).",
        engine="heuristic",
    )


def _build_prompt(text: str) -> str:
    return (
        "Classify this customer-support ticket. Respond with JSON matching:\n"
        '{"severity": "LOW|MEDIUM|HIGH|CRITICAL", "category": string, '
        '"duplicate_suspected": boolean, "rationale": string}\n\n'
        f"TICKET:\n{text[:4000]}"
    )


def classify_ticket(text: str, *, client: object | None = None) -> TriageResult:
    """Classify one ticket with Gemma, falling back to heuristics on failure."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if client is None and not api_key:
        return classify_heuristic(text)
    try:
        if client is None:
            from google import genai as google_genai

            client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(  # type: ignore[attr-defined]
            model=TRIAGE_MODEL,
            contents=_build_prompt(text),
            config={
                "response_mime_type": "application/json",
                "response_schema": TriageResult,
            },
        )
        parsed = TriageResult.model_validate_json(response.text)
        if parsed.severity not in _SEVERITY_ORDER:
            raise ValueError(f"Invalid severity from Gemma: {parsed.severity}")
        return parsed.model_copy(
            update={"engine": "gemma", "model_name": TRIAGE_MODEL}
        )
    except Exception:  # noqa: BLE001 - triage must never break intake
        fallback = classify_heuristic(text)
        return fallback.model_copy(update={"model_name": None})