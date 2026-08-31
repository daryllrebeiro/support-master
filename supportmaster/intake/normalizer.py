"""Normalize manual, webhook, and issue-tracker payloads to ``SupportCase``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from ..models.support_case import SupportCase


class IntakeResult(BaseModel):
    status: Literal["CREATED", "REPLAYED"]
    case: SupportCase
    duplicate_case_id: str | None = None


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _steps(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


import re


def _extract_header(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def normalize_case(
    payload: Mapping[str, Any],
    *,
    source_system: str,
    tenant_id: str = "default",
) -> SupportCase:
    """Map common aliases without imposing a vendor-specific schema."""
    title = str(_first(payload, "title", "summary", "subject", "name") or "Untitled support case").strip()
    description = str(_first(payload, "description", "body", "details", "problem", "text") or "").strip()
    if not description:
        raise ValueError("A support case description is required.")
    known = {
        "title", "summary", "subject", "name", "description", "body", "details", "problem", "text",
        "id", "case_id", "ticket_id", "key", "external_id", "requester", "reporter", "customer", "customer_account",
        "priority", "severity", "product", "service", "environment", "application_version", "version",
        "reproduction_steps", "reproduction", "steps", "expected_behavior", "expected", "actual_behavior", "actual",
        "customer_impact", "impact", "attachments", "metadata",
    }
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), Mapping) else {}
    metadata.update({str(key): value for key, value in payload.items() if key not in known})

    # Extract structured fields from description if not provided in top-level payload
    service = _first(payload, "service") or _extract_header(r"^(?:[-*]\s*)?(?:Affected\s+)?service\s*:\s*(.+)$", description)
    product = _first(payload, "product") or _extract_header(r"^(?:[-*]\s*)?(?:Affected\s+)?product\s*:\s*(.+)$", description)
    environment = _first(payload, "environment") or _extract_header(r"^(?:[-*]\s*)?environment\s*:\s*(.+)$", description)
    priority = _first(payload, "priority") or _extract_header(r"^(?:[-*]\s*)?priority\s*:\s*(.+)$", description)
    severity = _first(payload, "severity") or _extract_header(r"^(?:[-*]\s*)?severity\s*:\s*(.+)$", description)
    requester = _first(payload, "requester", "reporter") or _extract_header(r"^(?:[-*]\s*)?reported\s+by\s*:\s*(.+)$", description) or _extract_header(r"^(?:[-*]\s*)?reporter\s*:\s*(.+)$", description)
    customer_account = _first(payload, "customer_account", "customer") or _extract_header(r"^(?:[-*]\s*)?customer(?:\s+account)?\s*:\s*(.+)$", description)
    external_id = str(_first(payload, "external_id", "ticket_id", "case_id", "key", "id") or "") or None

    # If title or description has 'Ticket: KEY — Title' pattern, extract external_id and clean title
    ticket_match = re.search(r"^(?:##\s*)?Ticket\s*:\s*([A-Z0-9_-]+)\s*[—:-]\s*(.+)$", title, re.IGNORECASE)
    if ticket_match:
        if not external_id:
            external_id = ticket_match.group(1).strip()
        title = ticket_match.group(2).strip()
    elif "Ticket:" in description and (title == "Untitled support case" or title.startswith("##")):
        desc_match = re.search(r"^(?:##\s*)?Ticket\s*:\s*([A-Z0-9_-]+)\s*[—:-]\s*(.+)$", description, re.IGNORECASE | re.MULTILINE)
        if desc_match:
            if not external_id:
                external_id = desc_match.group(1).strip()
            title = desc_match.group(2).strip()

    # If service is a single word or repo name without owner, normalize to user's github org if known
    if service and not service.startswith("https://github.com/"):
        if "/" not in service and service not in {"default", "Core Service"}:
            service = f"daryllrebeiro/{service}"

    steps_val = _first(payload, "reproduction_steps", "reproduction", "steps")
    if not steps_val:
        steps_match = re.search(r"(?:##\s*Reproduction\s+steps|Reproduction\s+steps\s*:|Steps\s+to\s+reproduce\s*:)\s*\n((?:\s*(?:\d+[\.\)]|[-*])\s+.+\n?)+)", description, re.IGNORECASE)
        if steps_match:
            steps_val = [line.strip(" -\t*") for line in steps_match.group(1).splitlines() if line.strip()]

    return SupportCase(
        tenant_id=tenant_id,
        source_system=source_system,
        external_id=external_id,
        title=title,
        description=description,
        requester=requester,
        customer_account=customer_account,
        priority=priority,
        severity=severity,
        product=product,
        service=service,
        environment=environment,
        application_version=_first(payload, "application_version", "version") or _extract_header(r"^(?:[-*]\s*)?service\s+version\s*:\s*(.+)$", description),
        reproduction_steps=_steps(steps_val),
        expected_behavior=_first(payload, "expected_behavior", "expected") or _extract_header(r"^(?:[-*]\s*)?expected\s+behavior\s*:\s*(.+)$", description),
        actual_behavior=_first(payload, "actual_behavior", "actual") or _extract_header(r"^(?:[-*]\s*)?actual\s+behavior\s*:\s*(.+)$", description),
        customer_impact=_first(payload, "customer_impact", "impact"),
        attachments=payload.get("attachments") or [],
        metadata=metadata,
        status="NORMALIZED",
    )


class CaseIntakeService:
    """Normalize and persist cases with tenant-scoped external-id idempotency."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        source_system: str,
        tenant_id: str = "default",
    ) -> IntakeResult:
        case = normalize_case(payload, source_system=source_system, tenant_id=tenant_id)
        if case.external_id:
            existing = self.store.find_case_by_external_id(tenant_id, source_system, case.external_id)
            if existing is not None:
                return IntakeResult(status="REPLAYED", case=existing, duplicate_case_id=existing.case_id)
        self.store.save_case(case)
        return IntakeResult(status="CREATED", case=case)
