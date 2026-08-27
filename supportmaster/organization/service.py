"""Tenant-scoped organization profile lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ..models.organization import OrganizationProfile
from ..pipeline.bindings import validate_bindings
from ..pipeline.topology import validate_topology


class OrganizationContextService:
    def __init__(self, store: Any) -> None:
        self.store = store

    def get(self, organization_id: str) -> OrganizationProfile:
        return self.store.get_organization(organization_id)

    def ensure(self, organization_id: str, *, display_name: str | None = None) -> OrganizationProfile:
        try:
            return self.get(organization_id)
        except KeyError:
            profile = OrganizationProfile(
                organization_id=organization_id,
                display_name=display_name or organization_id,
            )
            return self.save(profile)

    def save(self, profile: OrganizationProfile) -> OrganizationProfile:
        # Validate topology and bindings at write-time (ORG_ADMIN)
        if getattr(profile, "pipeline_topology", None) is not None:
            validate_topology(profile.pipeline_topology)
        if getattr(profile, "adapter_bindings", None) is not None:
            validate_bindings(profile.adapter_bindings)
        profile.updated_at = datetime.now(timezone.utc)
        return self.store.save_organization(profile)

    def update(self, organization_id: str, changes: Mapping[str, Any]) -> OrganizationProfile:
        current = self.get(organization_id)
        payload = current.model_dump(mode="json")
        for key, value in changes.items():
            if key in payload and key not in {"organization_id", "created_at", "updated_at"}:
                payload[key] = value
        return self.save(OrganizationProfile.model_validate(payload))
