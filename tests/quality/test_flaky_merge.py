from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import subprocess

import pytest

from quality.flaky_merge import (
    CliRecoveryCloser,
    GitFastForwardMerger,
    MergeAndCloseService,
    MergeResult,
)
from quality.flaky_probe import ProbePlan
from quality.flaky_store import FlakyStoreError, migrate_store


TARGET_SHA = "b" * 40
CONTROLLER_SHA = "c" * 40


def _git(directory: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    remote.mkdir()
    work.mkdir()
    _git(remote, "init", "--bare")
    _git(work, "init", "-b", "dev3")
    _git(work, "config", "user.name", "Flaky Test")
    _git(work, "config", "user.email", "flaky@example.test")
    (work / "case.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "case.txt")
    _git(work, "commit", "-m", "base")
    _git(work, "remote", "add", "gitlab", str(remote))
    _git(work, "push", "-u", "gitlab", "dev3")
    _git(work, "checkout", "-b", "fix/flaky-case")
    (work / "case.txt").write_text("fixed\n", encoding="utf-8")
    _git(work, "commit", "-am", "fix flaky case")
    target_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "-u", "gitlab", "fix/flaky-case")
    return work, remote, target_sha


def test_git_merger_fast_forwards_dev3_to_exact_verified_sha(tmp_path):
    work, remote, target_sha = _repository(tmp_path)

    result = GitFastForwardMerger(work, remote="gitlab").merge(
        target_branch="fix/flaky-case", target_commit_sha=target_sha
    )

    assert result.status == "MERGED"
    assert result.dev3_after == target_sha
    assert _git(remote, "rev-parse", "refs/heads/dev3") == target_sha
    replay = GitFastForwardMerger(work, remote="gitlab").merge(
        target_branch="fix/flaky-case", target_commit_sha=target_sha
    )
    assert replay.status == "ALREADY_MERGED"


def test_git_merger_rejects_source_drift_and_non_fast_forward(tmp_path):
    work, _remote, target_sha = _repository(tmp_path)
    (work / "case.txt").write_text("changed again\n", encoding="utf-8")
    _git(work, "commit", "-am", "move source branch")
    _git(work, "push", "gitlab", "fix/flaky-case")

    with pytest.raises(FlakyStoreError) as drift:
        GitFastForwardMerger(work, remote="gitlab").merge(
            target_branch="fix/flaky-case", target_commit_sha=target_sha
        )
    assert drift.value.code == "verified_branch_head_mismatch"

    current_target = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "dev3")
    (work / "dev3.txt").write_text("advanced\n", encoding="utf-8")
    _git(work, "add", "dev3.txt")
    _git(work, "commit", "-m", "advance dev3 separately")
    _git(work, "push", "gitlab", "dev3")

    with pytest.raises(FlakyStoreError) as conflict:
        GitFastForwardMerger(work, remote="gitlab").merge(
            target_branch="fix/flaky-case", target_commit_sha=current_target
        )
    assert conflict.value.code == "dev3_not_fast_forward"


def test_cli_closer_uses_argument_vector_without_shell(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr("quality.flaky_merge.subprocess.run", fake_run)
    database = (tmp_path / "flaky.sqlite3").resolve()
    CliRecoveryCloser(database, tmp_path).close(
        attempt_id="attempt-1",
        expected_row_version=2,
        verified_branch_head="a" * 40,
        actor="dashboard-auto",
        reason="verified; still one argument",
    )

    command, options = calls[0]
    assert command[1:4] == ["-m", "quality.cli", "flaky-recovery-close"]
    assert command[command.index("--reason") + 1] == "verified; still one argument"
    assert options["cwd"] == tmp_path.resolve()
    assert "shell" not in options


def test_merge_service_closes_only_after_successful_merge(tmp_path):
    database = (tmp_path / "flaky.sqlite3").resolve()
    migrate_store(database)
    now = datetime(2026, 9, 2, tzinfo=UTC).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO flaky_identity(
                   flaky_key, epoch_scope_key, case_id, param_hash, environment,
                   execution_profile, state_epoch, current_detection_generation,
                   created_at, updated_at
               ) VALUES('flaky-1','scope-1','module/smoke/test_case.py::test_case',
                        'param-1','overseas','serial',1,1,?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO flaky_governance(
                   governance_id, flaky_key, status, owner, reason, created_by,
                   created_at, expires_at, row_version, recovery_started_by,
                   recovery_started_at, recovery_reason
               ) VALUES('governance-1','flaky-1','RECOVERING','owner','reason','actor',
                        ?,?,2,'dashboard-anonymous',?,'verify')""",
            (now, "2026-09-05T00:00:00+00:00", now),
        )
        connection.execute(
            """INSERT INTO flaky_verification_attempt(
                   attempt_id, governance_id, attempt_no, status, target_commit_sha,
                   policy_revision, required_consecutive_passes, min_interval_minutes,
                   max_non_counting_runs, counted_passes, non_counting_runs,
                   started_by, start_reason, started_at, expires_at, created_at, updated_at
               ) VALUES('attempt-1','governance-1',1,'READY_TO_CLOSE',?,
                        'flaky-governance.v1',5,30,3,5,0,'actor','verify',?,?,?,?)""",
            (TARGET_SHA, now, "2026-09-05T00:00:00+00:00", now, now),
        )

    plan = ProbePlan(
        attempt_id="attempt-1",
        governance_id="governance-1",
        flaky_key="flaky-1",
        case_id="module/smoke/test_case.py::test_case",
        param_hash="param-1",
        environment="overseas",
        execution_profile="serial",
        state_epoch=1,
        target_branch="fix/flaky-case",
        target_commit_sha=TARGET_SHA,
        controller_commit_sha=CONTROLLER_SHA,
        policy_revision="flaky-governance.v1",
        allowed_job_full_name="quality-probe",
    )
    events = []

    class Control:
        def get_plan(self, attempt_id):
            assert attempt_id == "attempt-1"
            return plan

    class Merger:
        def merge(self, **kwargs):
            events.append(("merge", kwargs))
            return MergeResult("MERGED", "a" * 40, TARGET_SHA, TARGET_SHA)

    class Closer:
        def close(self, **kwargs):
            events.append(("close", kwargs))
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """UPDATE flaky_verification_attempt
                       SET status='CLOSED', ended_at=?, end_reason='automatic close'
                       WHERE attempt_id='attempt-1'""",
                    (now,),
                )
                connection.execute(
                    """UPDATE flaky_governance
                       SET status='CLOSED', row_version=3, closed_at=?,
                           closed_by='dashboard-auto', close_reason='automatic close',
                           close_attempt_id='attempt-1', resolution='recovered'
                       WHERE governance_id='governance-1'""",
                    (now,),
                )

    result = MergeAndCloseService(database, Control(), Merger(), Closer()).execute(
        attempt_id="attempt-1",
        expected_row_version=2,
        reason="automatic close",
    )

    assert [event[0] for event in events] == ["merge", "close"]
    assert result["status"] == "CLOSED"
    assert events[1][1]["actor"] == "dashboard-auto"
