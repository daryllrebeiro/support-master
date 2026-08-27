"""Tests for Phase 36: Adapter Bindings Configuration & Validation."""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import (
    InMemoryCIAdapter,
    InMemoryIssueTrackerAdapter,
    InMemoryNotificationAdapter,
)
from supportmaster.models.organization import (
    AdapterBindingEntry,
    AdapterBindingsConfig,
)
from supportmaster.pipeline.bindings import (
    BindingValidationError,
    resolve_effective_nodes_and_bindings,
    validate_bindings,
)
from supportmaster.pipeline.capabilities import (
    CanFetchCase,
    CanPostComment,
    CanSendNotification,
    CanTriggerCI,
)
from supportmaster.pipeline.registry import AdapterRegistry


class AdapterBindingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AdapterRegistry()
        self.registry.register(
            "jira",
            InMemoryIssueTrackerAdapter,
            capabilities=[CanFetchCase, CanPostComment],
            vendor="atlassian",
        )
        self.registry.register(
            "slack",
            InMemoryNotificationAdapter,
            capabilities=[CanSendNotification],
            vendor="slack",
        )

    def test_valid_bindings_succeed(self) -> None:
        config = AdapterBindingsConfig(
            bindings={
                "ticket_intake": AdapterBindingEntry(adapter_id="jira", connection_ref="env:JIRA_TOKEN"),
                "notification": AdapterBindingEntry(adapter_id="slack", connection_ref="env:SLACK_WEBHOOK"),
            }
        )
        validated = validate_bindings(config, self.registry)
        self.assertEqual(len(validated), 2)
        self.assertEqual(validated["ticket_intake"].adapter_id, "jira")

    def test_unregistered_adapter_is_rejected(self) -> None:
        config = AdapterBindingsConfig(
            bindings={
                "ticket_intake": AdapterBindingEntry(adapter_id="unregistered_vendor", connection_ref=""),
            }
        )
        with self.assertRaises(BindingValidationError) as ctx:
            validate_bindings(config, self.registry)
        self.assertIn("not registered", str(ctx.exception))

    def test_unknown_capability_node_binding_is_rejected(self) -> None:
        config = AdapterBindingsConfig(
            bindings={
                "non_existent_node": AdapterBindingEntry(adapter_id="jira", connection_ref=""),
            }
        )
        with self.assertRaises(BindingValidationError) as ctx:
            validate_bindings(config, self.registry)
        self.assertIn("unknown capability node", str(ctx.exception))

    def test_env_secret_verification(self) -> None:
        config = AdapterBindingsConfig(
            bindings={
                "ticket_intake": AdapterBindingEntry(adapter_id="jira", connection_ref="env:MISSING_VAR"),
            }
        )
        with self.assertRaises(BindingValidationError) as ctx:
            validate_bindings(config, self.registry, environ={}, verify_env_secrets=True)
        self.assertIn("Environment secret 'MISSING_VAR'", str(ctx.exception))

    def test_effective_resolution(self) -> None:
        bindings = {
            "ticket_intake": AdapterBindingEntry(adapter_id="jira"),
            "notification": AdapterBindingEntry(adapter_id="slack"),
        }
        # Suppose topology disabled notification
        active_nodes = ["ticket_intake", "repository_discovery"]
        resolved = resolve_effective_nodes_and_bindings(active_nodes, bindings)
        self.assertIn("ticket_intake", resolved)
        self.assertEqual(resolved["ticket_intake"].adapter_id, "jira")
        self.assertIn("repository_discovery", resolved)
        self.assertIsNone(resolved["repository_discovery"])
        self.assertNotIn("notification", resolved)


if __name__ == "__main__":
    unittest.main()
