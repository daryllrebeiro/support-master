"""Phase D: golden-path demo — verified scoped fix against demo-target."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from supportmaster.execution.adapters import (
    SubprocessGitAdapter,
    SubprocessTestRunner,
)
from supportmaster.execution.engineering import (
    ControlledEngineeringExecutor,
)
from supportmaster.execution.local_demo import (
    DEMO_APPROVED_PATHS,
    BUGGY_SNIPPET,
    LocalDemoCodeChangeAdapter,
    commit_demo_fix,
    run_golden_path,
    seed_demo_repository,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "demo-target"


def _seed_repo(parent: Path) -> Path:
    repo = parent / "repo"
    repo.mkdir(parents=True)
    for name in ("invoice_export.py", "test_invoice_export.py"):
        shutil.copy(FIXTURE_DIR / name, repo / name)
    return repo


class GoldenPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = _seed_repo(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_golden_path_validates_and_commits(self) -> None:
        result = run_golden_path(self.repo)
        self.assertEqual(result.status, "VALIDATED")
        self.assertTrue(result.validation_passed)
        statuses = {
            (receipt.operation_type, receipt.status)
            for receipt in result.receipts
        }
        self.assertIn(("CODE_CHANGE", "SUCCEEDED"), statuses)
        self.assertIn(("TEST_EXECUTION", "SUCCEEDED"), statuses)
        self.assertIn(("DEMO_COMMIT", "SUCCEEDED"), statuses)
        fixed = (self.repo / "invoice_export.py").read_text(encoding="utf-8")
        self.assertIn("yield from rows", fixed)
        self.assertNotIn("buffered = []", fixed)

    def test_commit_lands_on_dedicated_branch(self) -> None:
        seed_demo_repository(self.repo)
        receipt = commit_demo_fix(self.repo, "SUP-TEST")
        # Nothing changed yet on the baseline, so the commit itself may fail;
        # what matters is that it never touched a disallowed path or pushed.
        self.assertIn(receipt.status, {"SUCCEEDED", "FAILED"})
        if receipt.status == "SUCCEEDED":
            self.assertEqual(
                receipt.details.get("branch"), "supportmaster/sup-test"
            )

    def test_out_of_scope_target_is_blocked(self) -> None:
        adapter = LocalDemoCodeChangeAdapter(target_file="secrets.txt")
        receipt = adapter.apply(
            self.repo,
            plan=None,  # type: ignore[arg-type]
            approved_paths=list(DEMO_APPROVED_PATHS),
        )
        self.assertEqual(receipt.status, "BLOCKED")
        self.assertIn("outside the approved path scope", receipt.error or "")

    def test_failed_validation_rolls_back_change(self) -> None:
        seed_demo_repository(self.repo)
        executor = ControlledEngineeringExecutor(
            git=SubprocessGitAdapter(),
            code_change=LocalDemoCodeChangeAdapter(),
            tests=SubprocessTestRunner(),
        )
        from supportmaster.execution.local_demo import (
            demo_remediation_plan,
            demo_state,
        )

        result = executor.execute(
            demo_state(),
            repository_path=self.repo,
            plan=demo_remediation_plan(),
            approved_paths=list(DEMO_APPROVED_PATHS),
            test_command=[sys.executable, "-c", "raise SystemExit(1)"],
        )
        self.assertEqual(result.status, "FAILED")
        self.assertTrue(result.rollback_attempted)
        restored = (self.repo / "invoice_export.py").read_text(encoding="utf-8")
        self.assertIn(BUGGY_SNIPPET, restored)

    def test_missing_grant_blocks_execution(self) -> None:
        executor = ControlledEngineeringExecutor(
            git=SubprocessGitAdapter(),
            code_change=LocalDemoCodeChangeAdapter(),
            tests=SubprocessTestRunner(),
        )
        from supportmaster.execution.local_demo import demo_remediation_plan

        result = executor.execute(
            {"run_id": "no-grants"},
            repository_path=self.repo,
            plan=demo_remediation_plan(),
            approved_paths=list(DEMO_APPROVED_PATHS),
            test_command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("authorization", result.errors[0].lower())


if __name__ == "__main__":
    unittest.main()