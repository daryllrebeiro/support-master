"""Golden-path demo: verified local execution against ``demo-target/``.

SUPPORTMASTER DEMO FIXTURE
Wires the real authorization-aware engineering executor (grant checks,
Git preflight, scoped change, real unittest run, receipted commit inputs)
to a repository-local fixture so the full autonomous fix path can be
demonstrated offline with one command.

Safety properties inherited from ``ControlledEngineeringExecutor``:
- Requires an active IMPLEMENTATION authorization grant.
- Refuses absolute paths or ``..`` traversal in approved paths.
- Rolls back the applied change when validation fails.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..models.control import AuthorizationGrant, ExternalOperationReceipt
from ..models.remediation import RemediationPlan
from .adapters import SubprocessGitAdapter, SubprocessTestRunner
from .engineering import (
    CodeChangeAdapter,
    ControlledEngineeringExecutor,
    EngineeringExecutionResult,
)

DEMO_TARGET_FILE = "invoice_export.py"
DEMO_APPROVED_PATHS = [DEMO_TARGET_FILE]
DEMO_TEST_COMMAND = [
    sys.executable,
    "-m",
    "unittest",
    "-v",
    "test_invoice_export",
]

# Canonical scoped fix for the injected demo defect.
BUGGY_SNIPPET = """    buffered = []
    for row in rows:
        buffered.append(row)
    return iter(buffered)"""
FIXED_SNIPPET = """    yield from rows"""


class LocalDemoCodeChangeAdapter(CodeChangeAdapter):
    """Apply one canonical replacement strictly inside approved paths."""

    def __init__(
        self,
        *,
        target_file: str = DEMO_TARGET_FILE,
        old_text: str = BUGGY_SNIPPET,
        new_text: str = FIXED_SNIPPET,
    ) -> None:
        self.target_file = target_file
        self.old_text = old_text
        self.new_text = new_text
        self._snapshot: dict[Path, str] = {}

    def apply(
        self,
        repository: Path,
        plan: RemediationPlan,
        approved_paths: Sequence[str],
    ) -> ExternalOperationReceipt:
        if self.target_file not in approved_paths:
            return ExternalOperationReceipt(
                operation_type="CODE_CHANGE",
                requested_action=f"apply_scoped_patch:{self.target_file}",
                status="BLOCKED",
                error=(
                    f"Target file {self.target_file!r} is outside the "
                    f"approved path scope: {sorted(approved_paths)}"
                ),
            )
        target = repository / self.target_file
        if not target.is_file():
            return ExternalOperationReceipt(
                operation_type="CODE_CHANGE",
                requested_action=f"apply_scoped_patch:{self.target_file}",
                status="BLOCKED",
                error=f"Target file does not exist: {target}",
            )
        original = target.read_text(encoding="utf-8")
        if self.old_text not in original:
            return ExternalOperationReceipt(
                operation_type="CODE_CHANGE",
                requested_action=f"apply_scoped_patch:{self.target_file}",
                status="FAILED",
                error=(
                    "Expected defective snippet not found; the demo fix was "
                    "already applied or the source changed."
                ),
            )
        self._snapshot[target] = original
        target.write_text(original.replace(self.old_text, self.new_text), encoding="utf-8")
        return ExternalOperationReceipt(
            operation_type="CODE_CHANGE",
            requested_action=f"apply_scoped_patch:{self.target_file}",
            status="SUCCEEDED",
            details={
                "file": self.target_file,
                "strategy": "canonical_streaming_fix",
                "objective": plan.objective[:200],
            },
        )

    def rollback(
        self,
        repository: Path,
        approved_paths: Sequence[str],
    ) -> ExternalOperationReceipt:
        target = repository / self.target_file
        snapshot = self._snapshot.pop(target, None)
        if snapshot is None or not target.is_file():
            return ExternalOperationReceipt(
                operation_type="CODE_CHANGE_ROLLBACK",
                requested_action=f"rollback:{self.target_file}",
                status="BLOCKED",
                error="No in-memory snapshot exists for this repository.",
            )
        target.write_text(snapshot, encoding="utf-8")
        return ExternalOperationReceipt(
            operation_type="CODE_CHANGE_ROLLBACK",
            requested_action=f"rollback:{self.target_file}",
            status="SUCCEEDED",
            details={"file": self.target_file},
        )


def build_local_demo_executor(repository: Path) -> ControlledEngineeringExecutor:
    """Build the real engineering executor bound to the demo adapters."""
    return ControlledEngineeringExecutor(
        git=SubprocessGitAdapter(),
        code_change=LocalDemoCodeChangeAdapter(),
        tests=SubprocessTestRunner(),
    )


def seed_demo_repository(repository: Path) -> None:
    """Initialize a clean Git baseline for the demo fixture."""
    repository.mkdir(parents=True, exist_ok=True)

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
        )

    if not (repository / ".git").exists():
        _git("init", "-q")
        _git("config", "user.email", "supportmaster-demo@example.com")
        _git("config", "user.name", "SupportMaster Demo")
    _git("add", "--", ".")
    committed = subprocess.run(
        ["git", "commit", "-qm", "Baseline demo fixture"],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if committed.returncode != 0 and b"nothing to commit" not in committed.stdout:
        raise RuntimeError(committed.stderr.decode("utf-8", "replace"))


def demo_remediation_plan() -> RemediationPlan:
    """A READY plan describing the canonical streaming fix."""
    return RemediationPlan.model_validate(
        {
            "remediation_status": "READY",
            "objective": (
                "Make invoice export stream rows lazily instead of "
                "materializing the full dataset in memory."
            ),
            "root_cause": (
                "stream_rows buffers every row into a list before returning "
                "an iterator over it, defeating its streaming contract."
            ),
            "proposed_approach": (
                "Replace the buffering loop with 'yield from rows' so rows "
                "are produced lazily."
            ),
            "remediation_steps": [
                {
                    "step": 1,
                    "action": "Replace buffered list construction with lazy yield.",
                    "change_type": "CODE",
                    "priority": "HIGH",
                    "rationale": "Removes unbounded memory growth.",
                    "expected_result": "stream_rows is a generator function.",
                    "risk": "Low: single-function behavioral equivalence.",
                    "validation": "Run demo-target regression tests.",
                }
            ],
            "affected_components": ["invoice_export"],
            "files_or_areas_to_review": [DEMO_TARGET_FILE],
            "implementation_allowed": True,
            "next_action": "IMPLEMENT_FIX",
        }
    )


def demo_state(case_id: str = "SUP-GOLDEN") -> dict:
    """State carrying an active IMPLEMENTATION grant for the demo run."""
    grant = AuthorizationGrant(
        scope="IMPLEMENTATION",
        issued_at=datetime.now(timezone.utc),
        active=True,
        human_approval_id="demo-operator",
    )
    return {
        "run_id": f"{case_id}:golden-path",
        "case_id": case_id,
        "authorizations": [grant.model_dump()],
    }


def commit_demo_fix(repository: Path, case_id: str = "SUP-GOLDEN") -> ExternalOperationReceipt:
    """Commit the validated change on a dedicated branch (never pushes)."""
    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )

    branch = f"supportmaster/{case_id.lower()}"
    checkout = _git("checkout", "-qb", branch)
    if checkout.returncode != 0:
        checkout = _git("checkout", "-q", branch)
        if checkout.returncode != 0:
            return ExternalOperationReceipt(
                operation_type="DEMO_COMMIT",
                requested_action=f"checkout_branch:{branch}",
                status="BLOCKED",
                error=checkout.stderr.strip()[:300],
            )
    add = _git("add", "--", *DEMO_APPROVED_PATHS)
    commit = _git("commit", "-qm", f"SupportMaster {case_id}: stream invoice rows lazily")
    sha = _git("rev-parse", "--short", "HEAD")
    if commit.returncode != 0:
        return ExternalOperationReceipt(
            operation_type="DEMO_COMMIT",
            requested_action=f"commit_branch:{branch}",
            status="FAILED",
            error=commit.stdout.strip()[:300] or commit.stderr.strip()[:300],
        )
    return ExternalOperationReceipt(
        operation_type="DEMO_COMMIT",
        requested_action=f"commit_branch:{branch}",
        status="SUCCEEDED",
        external_id=sha.stdout.strip() if sha.returncode == 0 else None,
        details={"branch": branch, "files": ",".join(DEMO_APPROVED_PATHS)},
    )


def run_golden_path(repository: Path, case_id: str = "SUP-GOLDEN") -> EngineeringExecutionResult:
    """Execute the complete verified fix path against the demo fixture."""
    repository = Path(repository)
    seed_demo_repository(repository)
    executor = build_local_demo_executor(repository)
    result = executor.execute(
        demo_state(case_id),
        repository_path=repository,
        plan=demo_remediation_plan(),
        approved_paths=list(DEMO_APPROVED_PATHS),
        test_command=list(DEMO_TEST_COMMAND),
    )
    if result.status == "VALIDATED":
        commit_receipt = commit_demo_fix(repository, case_id)
        result.receipts.append(commit_receipt)
        if commit_receipt.status != "SUCCEEDED":
            result.warnings.append("Validation passed but the demo commit did not succeed.")
    return result


def _main() -> int:  # pragma: no cover - demo entrypoint
    """Run the golden path in ./.demo-workspace and print receipts."""
    import json
    import shutil

    workspace = Path(".demo-workspace")
    fixture_dir = Path(__file__).resolve().parents[2] / "demo-target"
    if fixture_dir.is_dir():
        workspace.mkdir(parents=True, exist_ok=True)
        for name in ("invoice_export.py", "test_invoice_export.py"):
            shutil.copy(fixture_dir / name, workspace / name)
    print(f"Seeding demo repository at {workspace.resolve()} ...")
    result = run_golden_path(workspace)
    payload = {
        "status": result.status,
        "validation_passed": result.validation_passed,
        "changed_files": result.changed_files,
        "warnings": result.warnings,
        "errors": result.errors,
        "receipts": [receipt.model_dump(mode="json") for receipt in result.receipts],
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.status == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
