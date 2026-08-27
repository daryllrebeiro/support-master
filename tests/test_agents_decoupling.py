"""Tests for Phase 34: Stage agents are decoupled from vendor SDKs."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / "supportmaster" / "agents"

FORBIDDEN_VENDOR_MODULES = frozenset(
    {
        "github",
        "gitlab",
        "jira",
        "atlassian",
        "linear",
        "slack",
        "slack_sdk",
        "zendesk",
        "datadog",
    }
)


class StageAgentsDecouplingTests(unittest.TestCase):
    def test_zero_vendor_imports_in_agents(self) -> None:
        """Verify that stage agents contain zero vendor SDK imports."""
        self.assertTrue(AGENTS_DIR.is_dir(), f"Missing {AGENTS_DIR}")
        violations: list[str] = []

        for py_file in AGENTS_DIR.glob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0].lower()
                        if top_pkg in FORBIDDEN_VENDOR_MODULES:
                            violations.append(
                                f"{py_file.name}:{node.lineno} imports vendor SDK {alias.name!r}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_pkg = node.module.split(".")[0].lower()
                        if top_pkg in FORBIDDEN_VENDOR_MODULES:
                            violations.append(
                                f"{py_file.name}:{node.lineno} imports from vendor SDK {node.module!r}"
                            )

        self.assertEqual(
            violations,
            [],
            "Vendor imports found in supportmaster/agents/:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
