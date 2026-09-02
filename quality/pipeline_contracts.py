from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quality.models import RunKind, RunRecord


PIPELINE_CURRENT_SCHEMA_VERSION = "quality.pipeline-current.v1"
PIPELINE_RUN_SUMMARY_SCHEMA_VERSION = "quality.pipeline-run-summary.v1"
PIPELINE_RUN_DETAIL_SCHEMA_VERSION = "quality.pipeline-run-detail.v1"
PIPELINE_RUNS_SCHEMA_VERSION = "quality.pipeline-runs.v1"


class PipelineActivityStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    UNKNOWN = "UNKNOWN"


class PipelineResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNSTABLE = "UNSTABLE"
    ABORTED = "ABORTED"
    NOT_BUILT = "NOT_BUILT"
    UNKNOWN = "UNKNOWN"


class PipelineQualityStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    NOT_RUN = "NOT_RUN"
    MISSING = "MISSING"
    INVALID = "INVALID"


class PipelineFreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class FrozenPipelineModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class PipelineIssue(FrozenPipelineModel):
    code: str = Field(min_length=1, max_length=128)
    source: Literal[
        "configuration",
        "jenkins",
        "quality",
        "flaky",
        "association",
    ]
    message: str = Field(min_length=1, max_length=512)

    @field_validator("code", "message")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("pipeline issue text must not be blank")
        return text


class PipelineStage(FrozenPipelineModel):
    name: str = Field(min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=64)
    started_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("name", "status")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("pipeline stage text must not be blank")
        return text

    @field_validator("started_at")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        return _require_aware_time(value, "started_at")


class PipelineTestSummary(FrozenPipelineModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_total(self) -> PipelineTestSummary:
        counted = self.passed + self.failed + self.errors + self.skipped
        if counted != self.total:
            raise ValueError("test summary counters must add up to total")
        return self


class PipelineFlakyDelta(FrozenPipelineModel):
    new_count: int = Field(ge=0)
    persistent_count: int = Field(ge=0)
    recovered_count: int = Field(ge=0)


class PipelineLinks(FrozenPipelineModel):
    jenkins: str | None = None
    junit: str | None = None
    allure: str | None = None
    pipeline_summary: str | None = None

    @field_validator("jenkins", "junit", "allure", "pipeline_summary")
    @classmethod
    def _optional_link(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("pipeline links must not be blank")
        return text


class PipelineRunView(FrozenPipelineModel):
    activity_status: PipelineActivityStatus
    result_status: PipelineResultStatus
    quality_status: PipelineQualityStatus
    freshness_status: PipelineFreshnessStatus
    job_name: str = Field(min_length=1, max_length=256)
    build_number: int | None = Field(default=None, ge=1)
    branch: str | None = None
    commit_sha: str | None = None
    trigger_kind: str | None = None
    started_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    current_stage: str | None = None
    stages: tuple[PipelineStage, ...] = ()
    test_summary: PipelineTestSummary | None = None
    flaky_delta: PipelineFlakyDelta | None = None
    links: PipelineLinks = Field(default_factory=PipelineLinks)
    observed_at: datetime
    last_successful_poll_at: datetime | None = None
    issues: tuple[PipelineIssue, ...] = ()

    @field_validator("job_name")
    @classmethod
    def _job_name(cls, value: str) -> str:
        return normalize_pipeline_job_name(value)

    @field_validator("branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        return normalize_pipeline_branch(value) if value is not None else None

    @field_validator("commit_sha")
    @classmethod
    def _commit_sha(cls, value: str | None) -> str | None:
        return normalize_pipeline_commit_sha(value) if value is not None else None

    @field_validator("trigger_kind", "current_stage")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("optional pipeline text must not be blank")
        return text

    @field_validator("started_at", "observed_at", "last_successful_poll_at")
    @classmethod
    def _aware_time(cls, value: datetime | None, info) -> datetime | None:
        return _require_aware_time(value, info.field_name)

    @model_validator(mode="after")
    def _validate_state(self) -> PipelineRunView:
        _validate_pipeline_status_combination(
            self.activity_status,
            self.result_status,
            self.freshness_status,
        )
        if self.activity_status is PipelineActivityStatus.RUNNING:
            if self.build_number is None or self.started_at is None:
                raise ValueError("running pipeline builds require build_number and started_at")
        if (
            self.last_successful_poll_at is not None
            and self.last_successful_poll_at > self.observed_at
        ):
            raise ValueError("last_successful_poll_at cannot be after observed_at")
        return self


class PipelineCurrent(PipelineRunView):
    schema_version: Literal["quality.pipeline-current.v1"] = (
        PIPELINE_CURRENT_SCHEMA_VERSION
    )


class PipelineRunSummary(FrozenPipelineModel):
    schema_version: Literal["quality.pipeline-run-summary.v1"] = (
        PIPELINE_RUN_SUMMARY_SCHEMA_VERSION
    )
    build_number: int = Field(ge=1)
    activity_status: PipelineActivityStatus
    result_status: PipelineResultStatus
    quality_status: PipelineQualityStatus
    freshness_status: PipelineFreshnessStatus
    branch: str | None = None
    commit_sha: str | None = None
    started_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        return normalize_pipeline_branch(value) if value is not None else None

    @field_validator("commit_sha")
    @classmethod
    def _commit_sha(cls, value: str | None) -> str | None:
        return normalize_pipeline_commit_sha(value) if value is not None else None

    @field_validator("started_at")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        return _require_aware_time(value, "started_at")

    @model_validator(mode="after")
    def _validate_state(self) -> PipelineRunSummary:
        _validate_pipeline_status_combination(
            self.activity_status,
            self.result_status,
            self.freshness_status,
        )
        if (
            self.activity_status is PipelineActivityStatus.RUNNING
            and self.started_at is None
        ):
            raise ValueError("running pipeline builds require started_at")
        return self


class PipelineRunDetail(PipelineRunView):
    schema_version: Literal["quality.pipeline-run-detail.v1"] = (
        PIPELINE_RUN_DETAIL_SCHEMA_VERSION
    )


class PipelineRuns(FrozenPipelineModel):
    schema_version: Literal["quality.pipeline-runs.v1"] = PIPELINE_RUNS_SCHEMA_VERSION
    job_name: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=10, ge=1, le=50)
    items: tuple[PipelineRunSummary, ...]
    observed_at: datetime
    issues: tuple[PipelineIssue, ...] = ()

    @field_validator("job_name")
    @classmethod
    def _job_name(cls, value: str) -> str:
        return normalize_pipeline_job_name(value)

    @field_validator("observed_at")
    @classmethod
    def _aware_time(cls, value: datetime) -> datetime:
        return _require_aware_time(value, "observed_at")

    @model_validator(mode="after")
    def _descending_build_numbers(self) -> PipelineRuns:
        build_numbers = tuple(item.build_number for item in self.items)
        if build_numbers != tuple(sorted(build_numbers, reverse=True)):
            raise ValueError("pipeline runs must be sorted by descending build number")
        if len(self.items) > self.limit:
            raise ValueError("pipeline runs exceed the requested limit")
        return self


class PipelineBuildIdentity(FrozenPipelineModel):
    job_name: str
    build_number: int
    branch: str
    commit_sha: str

    @field_validator("job_name")
    @classmethod
    def _job_name(cls, value: str) -> str:
        return normalize_pipeline_job_name(value)

    @field_validator("build_number", mode="before")
    @classmethod
    def _build_number(cls, value: object) -> int:
        return normalize_pipeline_build_number(value)

    @field_validator("branch")
    @classmethod
    def _branch(cls, value: str) -> str:
        return normalize_pipeline_branch(value)

    @field_validator("commit_sha")
    @classmethod
    def _commit_sha(cls, value: str) -> str:
        return normalize_pipeline_commit_sha(value)


class PipelineAssociationResult(FrozenPipelineModel):
    matched: bool
    issue_codes: tuple[str, ...]

    @model_validator(mode="after")
    def _consistent_result(self) -> PipelineAssociationResult:
        if self.matched == bool(self.issue_codes):
            raise ValueError("matched association cannot contain issues")
        return self


def associate_normal_run(
    build: PipelineBuildIdentity,
    run: RunRecord,
) -> PipelineAssociationResult:
    issues: list[str] = []
    if run.run_kind is not RunKind.NORMAL:
        issues.append("run_kind_not_normal")

    raw_identity = (run.job_name, run.build_number, run.branch, run.commit_sha)
    if any(value is None for value in raw_identity):
        issues.append("normal_run_identity_incomplete")
        return PipelineAssociationResult(matched=False, issue_codes=tuple(issues))

    try:
        run_job = normalize_pipeline_job_name(str(run.job_name))
        run_build = normalize_pipeline_build_number(run.build_number)
        run_branch = normalize_pipeline_branch(str(run.branch))
        run_commit = normalize_pipeline_commit_sha(str(run.commit_sha))
    except ValueError:
        issues.append("normal_run_identity_invalid")
        return PipelineAssociationResult(matched=False, issue_codes=tuple(issues))

    comparisons = (
        (build.job_name == run_job, "job_name_mismatch"),
        (build.build_number == run_build, "build_number_mismatch"),
        (build.branch == run_branch, "branch_mismatch"),
        (build.commit_sha == run_commit, "commit_mismatch"),
    )
    issues.extend(code for matched, code in comparisons if not matched)
    return PipelineAssociationResult(
        matched=not issues,
        issue_codes=tuple(issues),
    )


def normalize_pipeline_job_name(value: object) -> str:
    text = str(value).strip()
    parts = text.split("/")
    if (
        not text
        or len(text) > 256
        or re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", text) is None
        or any(part in {".", ".."} for part in parts)
    ):
        raise ValueError("invalid fixed Jenkins Job full name")
    return text


def normalize_pipeline_build_number(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("build number must be a positive canonical integer")
    text = str(value).strip()
    if re.fullmatch(r"[1-9][0-9]*", text) is None:
        raise ValueError("build number must be a positive canonical integer")
    return int(text)


def normalize_pipeline_branch(value: object) -> str:
    text = str(value).strip().replace("\\", "/")
    for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    parts = text.split("/")
    if (
        not text
        or len(text) > 200
        or text.casefold() == "head"
        or text.casefold().startswith("refs/")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", text) is None
        or ".." in text
        or "@{" in text
        or any(
            not part
            or part.startswith(".")
            or part.endswith(".")
            or part.endswith(".lock")
            for part in parts
        )
    ):
        raise ValueError("invalid pipeline branch")
    return text


def normalize_pipeline_commit_sha(value: object) -> str:
    text = str(value).strip().casefold()
    if re.fullmatch(r"[0-9a-f]{40}", text) is None:
        raise ValueError("commit SHA must contain 40 hexadecimal characters")
    return text


def _require_aware_time(value: datetime | None, name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must include timezone information")
    return value


def _validate_pipeline_status_combination(
    activity_status: PipelineActivityStatus,
    result_status: PipelineResultStatus,
    freshness_status: PipelineFreshnessStatus,
) -> None:
    if (
        activity_status
        in {PipelineActivityStatus.QUEUED, PipelineActivityStatus.RUNNING}
        and result_status is not PipelineResultStatus.UNKNOWN
    ):
        raise ValueError("active pipeline builds cannot have a final result")
    if (
        freshness_status is PipelineFreshnessStatus.UNAVAILABLE
        and activity_status is not PipelineActivityStatus.UNKNOWN
    ):
        raise ValueError("unavailable pipeline observations must have UNKNOWN activity")


__all__ = (
    "PIPELINE_CURRENT_SCHEMA_VERSION",
    "PIPELINE_RUN_DETAIL_SCHEMA_VERSION",
    "PIPELINE_RUN_SUMMARY_SCHEMA_VERSION",
    "PIPELINE_RUNS_SCHEMA_VERSION",
    "PipelineActivityStatus",
    "PipelineAssociationResult",
    "PipelineBuildIdentity",
    "PipelineCurrent",
    "PipelineFlakyDelta",
    "PipelineFreshnessStatus",
    "PipelineIssue",
    "PipelineLinks",
    "PipelineQualityStatus",
    "PipelineResultStatus",
    "PipelineRunDetail",
    "PipelineRunSummary",
    "PipelineRuns",
    "PipelineStage",
    "PipelineTestSummary",
    "associate_normal_run",
    "normalize_pipeline_branch",
    "normalize_pipeline_build_number",
    "normalize_pipeline_commit_sha",
    "normalize_pipeline_job_name",
)
