"""Production release-readiness checks for SupportMaster."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from .evaluation.quality import run_fixture_quality_pack
from .integrations import IntegrationPolicy
from .models.evaluation import ReleaseCheck, ReleaseReadinessResult
from .operations import HealthReporter, load_operation_settings
from .persistence import SQLiteRunStore
from .security import load_security_settings


ROOT = Path(__file__).resolve().parents[1]


def run_release_readiness(
    store: SQLiteRunStore,
    scenarios_directory: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    require_auth: bool = True,
) -> ReleaseReadinessResult:
    """Validate runtime safety posture and deterministic product quality."""
    values = dict(environ or os.environ)
    checks: list[ReleaseCheck] = []
    try:
        load_operation_settings(values)
        checks.append(ReleaseCheck(name="operation_limits", status="PASS", detail="Operational limits are valid."))
    except Exception as error:
        checks.append(ReleaseCheck(name="operation_limits", status="FAIL", detail=str(error)))
    try:
        security = load_security_settings(values)
        secure = security.auth_mode != "DISABLED" if require_auth else True
        checks.append(ReleaseCheck(name="authentication", status="PASS" if secure else "FAIL", detail=f"Authentication mode: {security.auth_mode}."))
    except Exception as error:
        checks.append(ReleaseCheck(name="authentication", status="FAIL", detail=str(error)))
    integration = IntegrationPolicy()
    safe_defaults = integration.mode == "DRY_RUN" and not any(permission.startswith("WRITE_") or permission in {"TRIGGER_CI", "SEND_NOTIFICATIONS"} for permission in integration.allowed_permissions)
    checks.append(ReleaseCheck(name="integration_defaults", status="PASS" if safe_defaults else "FAIL", detail="Default integration policy is read-only dry-run."))
    health = HealthReporter(run_db=store.db_path).readiness()
    checks.append(ReleaseCheck(name="run_store", status="PASS" if health.status == "READY" else "FAIL", detail=str(health.checks)))
    checks.append(_discovery_readiness_check(store, values))
    checks.append(_adapter_compatibility_check(store))
    quality = run_fixture_quality_pack(store, scenarios_directory)
    checks.append(ReleaseCheck(name="quality_pack", status=quality.status, detail=f"Functional {quality.functional.passed}/{len(quality.functional.scenarios)}; end-to-end {quality.end_to_end.passed}/{len(quality.end_to_end.simulations)}."))
    return ReleaseReadinessResult(status="PASS" if all(check.status == "PASS" for check in checks) else "FAIL", checks=checks, quality=quality)


def _adapter_compatibility_check(store: SQLiteRunStore) -> ReleaseCheck:
    """Phase 39: verify that all tenant adapter bindings are registered and compatible."""
    from .pipeline.registry import default_registry

    try:
        profiles = store.list_organizations()
    except Exception as error:
        return ReleaseCheck(name="adapter_compatibility", status="FAIL", detail=f"Could not list organizations: {error}")

    checked = 0
    problems: list[str] = []
    for profile in profiles:
        bindings_config = getattr(profile, "adapter_bindings", None)
        if bindings_config is None:
            continue
        bindings = getattr(bindings_config, "bindings", {}) or {}
        for node_id, entry in bindings.items():
            checked += 1
            adapter_id = getattr(entry, "adapter_id", "")
            reg = default_registry.get_registration(adapter_id)
            if reg is None:
                problems.append(f"{profile.organization_id}:{node_id} uses unregistered adapter {adapter_id!r}")
            elif reg.interface_version != "capability-v1":
                problems.append(
                    f"{profile.organization_id}:{node_id} adapter {adapter_id} has incompatible interface_version {reg.interface_version!r}"
                )

    if problems:
        return ReleaseCheck(name="adapter_compatibility", status="FAIL", detail="; ".join(problems))
    return ReleaseCheck(
        name="adapter_compatibility",
        status="PASS",
        detail=f"All {checked} bound adapter(s) verified against registry capability-v1 matrix.",
    )


def _discovery_readiness_check(store: SQLiteRunStore, values: Mapping[str, str]) -> ReleaseCheck:
    """Phase 32: when discovery is enabled, workspace connections must be ready.

    Every tenant that enables ``discovery_policy`` must have READ_ONLY
    connections whose ``env:``-scheme secret refs resolve in this process.
    Non-``env:`` schemes (e.g. Secret Manager) are deployment-managed and
    pass here; they are verified at runtime by the provider registry.
    """
    enabled = str(values.get("SUPPORTMASTER_DISCOVERY_ENABLED", "")).strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return ReleaseCheck(name="workspace_discovery", status="PASS", detail="Discovery disabled; no workspace connections to verify.")
    problems: list[str] = []
    try:
        profiles = store.list_organizations()
    except Exception as error:
        return ReleaseCheck(name="workspace_discovery", status="FAIL", detail=f"Could not list organizations: {error}")
    checked = 0
    for profile in profiles:
        policy = getattr(profile, "discovery_policy", None)
        if policy is None or not getattr(policy, "enabled", False):
            continue
        for connection in getattr(profile, "workspace_connections", []) or []:
            checked += 1
            label = f"{profile.organization_id}:{connection.provider}/{connection.workspace_id}"
            if not connection.workspace_id:
                problems.append(f"{label}: empty workspace_id")
            if connection.scope != "READ_ONLY":
                problems.append(f"{label}: scope {connection.scope!r} is not READ_ONLY")
            if connection.secret_ref.startswith("env:"):
                secret_name = connection.secret_ref.split(":", 1)[1]
                if not str(values.get(secret_name, "")).strip():
                    problems.append(
                        f"{label}: environment secret {secret_name!r} is not set"
                    )
    if problems:
        return ReleaseCheck(name="workspace_discovery", status="FAIL", detail="; ".join(problems))
    return ReleaseCheck(name="workspace_discovery", status="PASS", detail=f"Discovery enabled; {checked} connection(s) verified read-only with resolvable secrets.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SupportMaster release-readiness checks.")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "fixtures" / "cases")
    parser.add_argument("--db", type=Path, default=ROOT / ".supportmaster" / "release.db")
    parser.add_argument("--allow-anonymous", action="store_true", help="Do not require authentication for local-only checks.")
    args = parser.parse_args(argv)
    result = run_release_readiness(SQLiteRunStore(args.db), args.fixtures, require_auth=not args.allow_anonymous)
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
