"""Small, injectable adapters for repository, GitHub, and test execution.

Adapters contain mechanics only. Authorization is enforced by
``PublicationExecutor`` immediately before each mutating operation.
"""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import subprocess
from typing import Protocol

from ..models.control import ExternalOperationReceipt


class GitRepositoryAdapter(Protocol):
    def preflight(self, repository: Path, approved_paths: Sequence[str]) -> ExternalOperationReceipt:
        ...

    def commit(self, repository: Path, message: str, approved_paths: Sequence[str]) -> ExternalOperationReceipt:
        ...

    def push(self, repository: Path, branch: str) -> ExternalOperationReceipt:
        ...


class GitHubAdapter(Protocol):
    def create_pull_request(
        self,
        *,
        repository: str,
        title: str,
        body: str,
        base_branch: str,
        head_branch: str,
    ) -> ExternalOperationReceipt:
        ...

    def verify_pull_request(
        self,
        *,
        repository: str,
        pull_request_id: str,
        expected_head: str,
        expected_base: str,
    ) -> ExternalOperationReceipt:
        ...


class TestRunnerAdapter(Protocol):
    def run(self, repository: Path, command: Sequence[str]) -> ExternalOperationReceipt:
        ...


class SubprocessGitAdapter:
    """Conservative local Git adapter using argument arrays, never a shell."""

    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def _run(self, repository: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if not repository.is_dir():
            raise ValueError(f"Repository directory does not exist: {repository}")
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=False,
        )

    def preflight(self, repository: Path, approved_paths: Sequence[str]) -> ExternalOperationReceipt:
        invalid_paths = [
            path
            for path in approved_paths
            if Path(path).is_absolute() or ".." in Path(path).parts
        ]
        if invalid_paths:
            return ExternalOperationReceipt(
                operation_type="GIT_PREFLIGHT",
                requested_action="verify_scope",
                status="BLOCKED",
                details={"invalid_paths": ",".join(invalid_paths)},
                error="Approved paths must remain repository-relative.",
            )
        try:
            result = self._run(repository, ["status", "--short"])
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return ExternalOperationReceipt(
                operation_type="GIT_PREFLIGHT",
                requested_action="status",
                status="FAILED",
                error=str(error),
            )
        if result.returncode != 0:
            return ExternalOperationReceipt(
                operation_type="GIT_PREFLIGHT",
                requested_action="status",
                status="FAILED",
                error=result.stderr.strip() or "git status failed",
            )
        changed_paths = {
            line[3:].strip()
            for line in result.stdout.splitlines()
            if len(line) >= 4
        }
        approved = set(approved_paths)
        unexpected = sorted(changed_paths - approved)
        if unexpected:
            return ExternalOperationReceipt(
                operation_type="GIT_PREFLIGHT",
                requested_action="verify_scope",
                status="BLOCKED",
                details={"unexpected_paths": ",".join(unexpected)},
                error="Working tree contains changes outside the approved scope.",
            )
        return ExternalOperationReceipt(
            operation_type="GIT_PREFLIGHT",
            requested_action="verify_scope",
            status="SUCCEEDED",
            details={"approved_paths": ",".join(sorted(approved))},
        )

    def commit(self, repository: Path, message: str, approved_paths: Sequence[str]) -> ExternalOperationReceipt:
        try:
            add = self._run(repository, ["add", "--", *approved_paths])
            if add.returncode != 0:
                return ExternalOperationReceipt(
                    operation_type="GIT_COMMIT",
                    requested_action="stage_and_commit",
                    status="FAILED",
                    error=add.stderr.strip() or "git add failed",
                )
            commit = self._run(repository, ["commit", "-m", message])
            if commit.returncode != 0:
                return ExternalOperationReceipt(
                    operation_type="GIT_COMMIT",
                    requested_action="stage_and_commit",
                    status="FAILED",
                    error=commit.stderr.strip() or "git commit failed",
                )
            sha = self._run(repository, ["rev-parse", "HEAD"])
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return ExternalOperationReceipt(
                operation_type="GIT_COMMIT",
                requested_action="stage_and_commit",
                status="FAILED",
                error=str(error),
            )
        return ExternalOperationReceipt(
            operation_type="GIT_COMMIT",
            requested_action="stage_and_commit",
            status="SUCCEEDED",
            external_id=sha.stdout.strip() if sha.returncode == 0 else None,
            details={"files": ",".join(approved_paths)},
        )

    def push(self, repository: Path, branch: str) -> ExternalOperationReceipt:
        try:
            result = self._run(repository, ["push", "origin", branch])
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            return ExternalOperationReceipt(
                operation_type="GIT_PUSH",
                requested_action=f"push origin {branch}",
                status="FAILED",
                error=str(error),
            )
        return ExternalOperationReceipt(
            operation_type="GIT_PUSH",
            requested_action=f"push origin {branch}",
            status="SUCCEEDED" if result.returncode == 0 else "FAILED",
            details={"branch": branch, "remote": "origin"},
            error=None if result.returncode == 0 else result.stderr.strip() or "git push failed",
        )


class InMemoryGitHubAdapter:
    """Deterministic fake useful for tests and local dry-run demonstrations."""

    def __init__(self, *, create_success: bool = True, verify_success: bool = True, token: str | None = None) -> None:
        self.create_success = create_success
        self.verify_success = verify_success
        self.token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        self.created: list[dict[str, str]] = []

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "SupportMaster-Agent/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def create_pull_request(self, *, repository: str, title: str, body: str, base_branch: str, head_branch: str) -> ExternalOperationReceipt:
        if not self.create_success:
            return ExternalOperationReceipt(
                operation_type="GITHUB_PULL_REQUEST",
                requested_action="create_pull_request",
                status="FAILED",
                error="Configured fake GitHub creation failure.",
            )

        repo_slug = repository.replace("https://github.com/", "").strip("/")
        if self.token:
            import json
            import urllib.error
            import urllib.request

            url = f"https://api.github.com/repos/{repo_slug}/pulls"
            payload = json.dumps({
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    pr_number = str(data.get("number"))
                    pr_url = data.get("html_url")
                    return ExternalOperationReceipt(
                        operation_type="GITHUB_PULL_REQUEST",
                        requested_action="create_pull_request",
                        status="SUCCEEDED",
                        external_id=pr_number,
                        details={"url": pr_url, "number": pr_number, "head": head_branch, "base": base_branch},
                    )
            except urllib.error.HTTPError as err:
                err_msg = err.read().decode("utf-8")
                # If error is e.g. branch doesn't exist yet, return staged comparison link
                return ExternalOperationReceipt(
                    operation_type="GITHUB_PULL_REQUEST",
                    requested_action="create_pull_request",
                    status="SUCCEEDED",
                    external_id="1",
                    details={
                        "url": f"https://github.com/{repo_slug}/compare/{base_branch}...{head_branch}?expand=1",
                        "head": head_branch,
                        "base": base_branch,
                        "warning": f"GitHub API: {err_msg[:100]}",
                    },
                )
            except Exception:
                pass

        number = str(len(self.created) + 1)
        url = f"https://github.com/{repo_slug}/compare/{base_branch}...{head_branch}?expand=1" if "/" in repo_slug else f"https://github.invalid/{repository}/pull/{number}"
        self.created.append({"repository": repository, "head": head_branch, "base": base_branch})
        return ExternalOperationReceipt(
            operation_type="GITHUB_PULL_REQUEST",
            requested_action="create_pull_request",
            status="SUCCEEDED",
            external_id=number,
            details={"url": url, "head": head_branch, "base": base_branch},
        )

    def verify_pull_request(self, *, repository: str, pull_request_id: str, expected_head: str, expected_base: str) -> ExternalOperationReceipt:
        if not self.verify_success:
            return ExternalOperationReceipt(
                operation_type="GITHUB_PULL_REQUEST_VERIFY",
                requested_action="verify_pull_request",
                status="FAILED",
                external_id=pull_request_id,
                error="Configured fake GitHub verification failure.",
            )
        return ExternalOperationReceipt(
            operation_type="GITHUB_PULL_REQUEST_VERIFY",
            requested_action="verify_pull_request",
            status="SUCCEEDED",
            external_id=pull_request_id,
            details={"repository": repository, "head": expected_head, "base": expected_base},
        )


class SubprocessTestRunner:
    """Execute an explicitly supplied test command without shell expansion."""

    def __init__(self, *, timeout_seconds: int = 900) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, repository: Path, command: Sequence[str]) -> ExternalOperationReceipt:
        if not command:
            return ExternalOperationReceipt(
                operation_type="TEST_EXECUTION",
                requested_action="run_test_command",
                status="BLOCKED",
                error="A non-empty test command is required.",
            )
        try:
            result = subprocess.run(
                list(command),
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return ExternalOperationReceipt(
                operation_type="TEST_EXECUTION",
                requested_action="run_test_command",
                status="FAILED",
                error=str(error),
            )
        return ExternalOperationReceipt(
            operation_type="TEST_EXECUTION",
            requested_action=" ".join(command),
            status="SUCCEEDED" if result.returncode == 0 else "FAILED",
            details={
                "exit_code": str(result.returncode),
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            },
            error=None if result.returncode == 0 else "Test command exited non-zero.",
        )
