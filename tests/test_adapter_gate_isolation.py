"""Guardrail test enforcing Design Invariant 3: Adapters cannot touch gates.

Structurally verifies via AST inspection that no class or function in
`supportmaster/integrations/**` imports, references, or calls gate-mutating
routines or state-contract gate decision writers.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS_DIR = ROOT / "supportmaster" / "integrations"

FORBIDDEN_GATE_SYMBOLS = frozenset(
    {
        "evaluate_duplicate_gate",
        "evaluate_review_gate",
        "evaluate_implementation_authorization_gate",
        "evaluate_validation_gate",
        "evaluate_publish_authorization_gate",
        "evaluate_audit_gate",
        "issue_authorization",
        "append_gate_history",
        "append_policy_decision",
        "GateDecision",
    }
)


class AdapterGateIsolationTests(unittest.TestCase):
    def test_integrations_do_not_import_or_mutate_gates(self) -> None:
        """Every python file in integrations/ must not import or use gate logic."""
        self.assertTrue(INTEGRATIONS_DIR.is_dir(), f"Missing {INTEGRATIONS_DIR}")
        violations: list[str] = []

        for py_file in INTEGRATIONS_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

            for node in ast.walk(tree):
                # Check direct imports: from ... import evaluate_audit_gate
                if isinstance(node, ast.ImportFrom):
                    if node.module and ("control_gates" in node.module or "workflow_state" in node.module):
                        for name in node.names:
                            if name.name in FORBIDDEN_GATE_SYMBOLS:
                                violations.append(
                                    f"{py_file.name}:{node.lineno} imports forbidden gate symbol {name.name!r}"
                                )

                # Check attribute accesses or calls to forbidden symbols
                if isinstance(node, ast.Name) and node.id in FORBIDDEN_GATE_SYMBOLS:
                    violations.append(
                        f"{py_file.name}:{node.lineno} references gate symbol {node.id!r}"
                    )

        self.assertEqual(
            violations,
            [],
            f"Gate isolation violations found in integrations:\n" + "\n".join(violations),
        )

    def test_adapters_do_not_expose_gate_methods(self) -> None:
        """No adapter class may declare methods containing 'gate' or 'authorize'."""
        violations: list[str] = []
        for py_file in INTEGRATIONS_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if "gate" in item.name.lower() or "authorize" in item.name.lower():
                                violations.append(
                                    f"Class {node.name}.{item.name} in {py_file.name}:{item.lineno} exposes gate-touching method"
                                )
        self.assertEqual(
            violations,
            [],
            "Adapters must not expose gate methods:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
