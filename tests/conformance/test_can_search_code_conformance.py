"""Conformance test suite for CanSearchCode capability adapters."""

from __future__ import annotations

import unittest

from supportmaster.integrations.workspace_providers.base import FakeWorkspaceProvider
from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.models.discovery import CodeMatch, RepoRef, RepositoryDescriptor
from supportmaster.pipeline.capabilities import CanSearchCode


class CanSearchCodeConformanceTests(unittest.TestCase):
    def test_can_search_code_contract(self) -> None:
        repo_ref = RepoRef(provider="github", workspace_id="acme", repo="backend-service")
        descriptor = RepositoryDescriptor(ref=repo_ref, default_branch="main")
        match = CodeMatch(ref=repo_ref, path="src/auth.py", snippet="def verify_token(): pass")

        adapter = FakeWorkspaceProvider(
            workspace_id="acme",
            repositories=[descriptor],
            code_matches=[match],
        )
        self.assertIsInstance(adapter, CanSearchCode)

        matches, receipt = adapter.search_code("verify_token", repos=["backend-service"])
        self.assertIsInstance(receipt, ExternalOperationReceipt)
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].path, "src/auth.py")


if __name__ == "__main__":
    unittest.main()
