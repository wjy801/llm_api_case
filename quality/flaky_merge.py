from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Protocol

from quality.flaky_probe import (
    GitTargetResolver,
    ProbeControlService,
    validate_git_remote,
    validate_target_branch,
)
from quality.flaky_store import FlakyStoreError
from quality.flaky_store.v3_service import FlakyV3Service


@dataclass(frozen=True)
class MergeResult:
    status: str
    dev3_before: str
    dev3_after: str
    target_commit_sha: str


class VerifiedCommitMerger(Protocol):
    def merge(
        self, *, target_branch: str, target_commit_sha: str
    ) -> MergeResult: ...


class RecoveryCloser(Protocol):
    def close(
        self,
        *,
        attempt_id: str,
        expected_row_version: int,
        verified_branch_head: str,
        actor: str,
        reason: str,
    ) -> None: ...


class GitFastForwardMerger:
    """Advance dev3 to an immutable verified commit without checking out either branch."""

    def __init__(self, repository_root: str | Path, *, remote: str) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.remote = validate_git_remote(remote)
        self._resolver = GitTargetResolver(self.repository_root, remote=self.remote)
        self._lock = threading.Lock()

    def merge(self, *, target_branch: str, target_commit_sha: str) -> MergeResult:
        branch = validate_target_branch(target_branch)
        if re.fullmatch(r"[0-9a-f]{40}", target_commit_sha) is None:
            raise FlakyStoreError(
                "invalid_merge_target", "verified commit SHA is invalid"
            )
        if branch == "dev3":
            raise FlakyStoreError(
                "invalid_merge_target", "a verified pre-merge branch is required"
            )
        with self._lock:
            current_target = self._resolver.resolve_branch(branch)
            if current_target != target_commit_sha:
                raise FlakyStoreError(
                    "verified_branch_head_mismatch",
                    "verified branch HEAD changed after Probe",
                )
            dev3_before = self._resolver.resolve_dev3()
            if dev3_before == target_commit_sha:
                return MergeResult(
                    status="ALREADY_MERGED",
                    dev3_before=dev3_before,
                    dev3_after=dev3_before,
                    target_commit_sha=target_commit_sha,
                )

            self._git(
                "fetch",
                "--no-tags",
                "--quiet",
                self.remote,
                "refs/heads/dev3",
                f"refs/heads/{branch}",
                error_code="git_fetch_failed",
            )
            # Re-read the remote after fetching so a moving source branch can
            # never silently change the commit that passed Probe.
            if self._resolver.resolve_branch(branch) != target_commit_sha:
                raise FlakyStoreError(
                    "verified_branch_head_mismatch",
                    "verified branch HEAD changed after Probe",
                )
            dev3_before = self._resolver.resolve_dev3()
            ancestor = self._git(
                "merge-base",
                "--is-ancestor",
                dev3_before,
                target_commit_sha,
                allowed_return_codes={0, 1},
                error_code="git_merge_check_failed",
            )
            if ancestor.returncode == 1:
                raise FlakyStoreError(
                    "dev3_not_fast_forward",
                    "dev3 changed and cannot be fast-forwarded to the verified commit",
                )

            self._git(
                "push",
                "--porcelain",
                self.remote,
                f"{target_commit_sha}:refs/heads/dev3",
                error_code="git_merge_rejected",
            )
            dev3_after = self._resolver.resolve_dev3()
            if dev3_after != target_commit_sha:
                raise FlakyStoreError(
                    "git_merge_verification_failed",
                    "dev3 did not resolve to the verified commit after push",
                )
            return MergeResult(
                status="MERGED",
                dev3_before=dev3_before,
                dev3_after=dev3_after,
                target_commit_sha=target_commit_sha,
            )

    def _git(
        self,
        *arguments: str,
        allowed_return_codes: set[int] | None = None,
        error_code: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=self.repository_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise FlakyStoreError(error_code, "Git operation failed") from error
        accepted = allowed_return_codes or {0}
        if completed.returncode not in accepted:
            raise FlakyStoreError(error_code, "Git operation failed")
        return completed


class CliRecoveryCloser:
    """Run the existing close command so automatic and manual close share one gate."""

    def __init__(self, database_path: str | Path, repository_root: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.repository_root = Path(repository_root).resolve()

    def close(
        self,
        *,
        attempt_id: str,
        expected_row_version: int,
        verified_branch_head: str,
        actor: str,
        reason: str,
    ) -> None:
        command = [
            sys.executable,
            "-m",
            "quality.cli",
            "flaky-recovery-close",
            "--db",
            str(self.database_path),
            "--attempt-id",
            attempt_id,
            "--actor",
            actor,
            "--reason",
            reason,
            "--expected-row-version",
            str(expected_row_version),
            "--verified-branch-head",
            verified_branch_head,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repository_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise FlakyStoreError(
                "automatic_close_failed", "automatic close command failed"
            ) from error
        if completed.returncode != 0:
            raise FlakyStoreError(
                "automatic_close_failed", "automatic close command failed"
            )


class MergeAndCloseService:
    def __init__(
        self,
        database_path: str | Path,
        probe_control: ProbeControlService,
        merger: VerifiedCommitMerger,
        closer: RecoveryCloser,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.probe_control = probe_control
        self.merger = merger
        self.closer = closer

    def execute(
        self,
        *,
        attempt_id: str,
        expected_row_version: int,
        reason: str,
        actor: str = "dashboard-auto",
    ) -> dict[str, object]:
        plan = self.probe_control.get_plan(attempt_id)
        state = FlakyV3Service(self.database_path).recovery_status(plan.flaky_key)
        governance = state.get("governance")
        attempt = state.get("attempt")
        if not isinstance(governance, dict) or not isinstance(attempt, dict):
            raise FlakyStoreError("attempt_not_ready", "attempt is not ready")
        if attempt.get("attempt_id") != attempt_id:
            raise FlakyStoreError("attempt_not_ready", "attempt is not current")
        if governance.get("status") == "CLOSED" and attempt.get("status") == "CLOSED":
            return self._result("ALREADY_CLOSED", None, plan.target_commit_sha)
        if (
            governance.get("status") != "RECOVERING"
            or attempt.get("status") != "READY_TO_CLOSE"
        ):
            raise FlakyStoreError("attempt_not_ready", "attempt is not ready to merge")
        if int(governance.get("row_version", -1)) != int(expected_row_version):
            raise FlakyStoreError("row_version_conflict", "governance row changed")
        if attempt.get("target_commit_sha") != plan.target_commit_sha:
            raise FlakyStoreError(
                "verified_branch_head_mismatch", "attempt and Probe plan differ"
            )

        merge = self.merger.merge(
            target_branch=plan.target_branch,
            target_commit_sha=plan.target_commit_sha,
        )
        try:
            self.closer.close(
                attempt_id=attempt_id,
                expected_row_version=expected_row_version,
                verified_branch_head=plan.target_commit_sha,
                actor=actor,
                reason=reason,
            )
        except FlakyStoreError:
            # A lost HTTP response may retry after the first close committed.
            # Report that terminal state instead of turning the retry into an error.
            refreshed = FlakyV3Service(self.database_path).recovery_status(plan.flaky_key)
            if (
                isinstance(refreshed.get("governance"), dict)
                and refreshed["governance"].get("status") == "CLOSED"
                and isinstance(refreshed.get("attempt"), dict)
                and refreshed["attempt"].get("attempt_id") == attempt_id
                and refreshed["attempt"].get("status") == "CLOSED"
            ):
                return self._result("CLOSED", merge, plan.target_commit_sha)
            raise

        refreshed = FlakyV3Service(self.database_path).recovery_status(plan.flaky_key)
        if (
            not isinstance(refreshed.get("governance"), dict)
            or refreshed["governance"].get("status") != "CLOSED"
        ):
            raise FlakyStoreError(
                "automatic_close_failed", "automatic close did not reach CLOSED"
            )
        return self._result("CLOSED", merge, plan.target_commit_sha)

    @staticmethod
    def _result(
        status: str, merge: MergeResult | None, target_commit_sha: str
    ) -> dict[str, object]:
        return {
            "schema_version": "quality.flaky-merge-close.v1",
            "status": status,
            "merge_status": merge.status if merge is not None else "ALREADY_MERGED",
            "target_branch": "dev3",
            "target_commit_sha": target_commit_sha,
        }


__all__ = (
    "CliRecoveryCloser",
    "GitFastForwardMerger",
    "MergeAndCloseService",
    "MergeResult",
    "RecoveryCloser",
    "VerifiedCommitMerger",
)
