import tempfile
import unittest
from pathlib import Path

from supportmaster.models.organization import (
    DiscoveryPolicy,
    OrganizationProfile,
    WorkspaceConnection,
)
from supportmaster.persistence import SQLiteRunStore
from supportmaster.release import run_release_readiness


def _release_environ() -> dict[str, str]:
    return {
        "SUPPORTMASTER_AUTH_MODE": "REQUIRED",
        "SUPPORTMASTER_API_KEYS": "demo-secret|demo|tenant-a|RUN_EXECUTE,HEALTH_READ,AUDIT_READ",
    }


class ReleaseReadinessTests(unittest.TestCase):
    def test_production_posture_requires_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_release_readiness(
                SQLiteRunStore(Path(directory) / "release.db"),
                Path(__file__).parents[1] / "fixtures" / "cases",
                environ={"SUPPORTMASTER_AUTH_MODE": "DISABLED"},
            )
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(next(check for check in result.checks if check.name == "authentication").status, "FAIL")

    def test_configured_release_posture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_release_readiness(
                SQLiteRunStore(Path(directory) / "release.db"),
                Path(__file__).parents[1] / "fixtures" / "cases",
                environ={"SUPPORTMASTER_AUTH_MODE": "REQUIRED", "SUPPORTMASTER_API_KEYS": "demo-secret|demo|tenant-a|RUN_EXECUTE,HEALTH_READ,AUDIT_READ"},
            )
        self.assertEqual(result.status, "PASS")
        self.assertTrue(all(check.status == "PASS" for check in result.checks))


class DiscoveryReadinessTests(unittest.TestCase):
    """Phase 32: release checks verify workspace connections when enabled."""

    def _run(self, store: SQLiteRunStore, environ: dict[str, str]):
        return run_release_readiness(
            store,
            Path(__file__).parents[1] / "fixtures" / "cases",
            environ=environ,
        )

    def test_disabled_discovery_passes_without_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                SQLiteRunStore(Path(directory) / "release.db"),
                _release_environ(),
            )
        check = next(c for c in result.checks if c.name == "workspace_discovery")
        self.assertEqual(check.status, "PASS")
        self.assertIn("disabled", check.detail)

    def test_enabled_discovery_with_unresolved_secret_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "release.db")
            store.save_organization(
                OrganizationProfile(
                    organization_id="tenant-a",
                    display_name="Tenant A",
                    discovery_policy=DiscoveryPolicy(enabled=True),
                    workspace_connections=[
                        WorkspaceConnection(
                            provider="github",
                            workspace_id="acme",
                            secret_ref="env:DEFINITELY_UNSET_SECRET_XYZ",
                        )
                    ],
                )
            )
            result = self._run(store, {**_release_environ(), "SUPPORTMASTER_DISCOVERY_ENABLED": "true"})
        check = next(c for c in result.checks if c.name == "workspace_discovery")
        self.assertEqual(check.status, "FAIL")
        self.assertIn("DEFINITELY_UNSET_SECRET_XYZ", check.detail)

    def test_enabled_discovery_with_resolvable_secret_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRunStore(Path(directory) / "release.db")
            store.save_organization(
                OrganizationProfile(
                    organization_id="tenant-a",
                    display_name="Tenant A",
                    discovery_policy=DiscoveryPolicy(enabled=True),
                    workspace_connections=[
                        WorkspaceConnection(
                            provider="github",
                            workspace_id="acme",
                            secret_ref="env:RELEASE_TEST_TOKEN",
                        )
                    ],
                )
            )
            result = self._run(
                store,
                {
                    **_release_environ(),
                    "SUPPORTMASTER_DISCOVERY_ENABLED": "true",
                    "RELEASE_TEST_TOKEN": "tok",
                },
            )
        check = next(c for c in result.checks if c.name == "workspace_discovery")
        self.assertEqual(check.status, "PASS")
        self.assertIn("1 connection(s)", check.detail)


if __name__ == "__main__":
    unittest.main()
