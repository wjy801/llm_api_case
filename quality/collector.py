from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from threading import RLock

from quality.models import CaseResult, IntegrityIssue, IssueSeverity, RequestMetric
from quality.redaction import redact_quality_value
from quality.runtime_context import QualityRunContext
from quality.storage import append_jsonl, ensure_quality_dirs


WarningSink = Callable[[str], None]


@dataclass(frozen=True)
class QualityShardPaths:
    cases: Path
    requests: Path
    integrity: Path


class QualityCollector:
    def __init__(
        self,
        run_context: QualityRunContext,
        *,
        warning_sink: WarningSink | None = None,
    ) -> None:
        self.run_context = run_context
        self._warning_sink = warning_sink or _default_warning_sink
        self._write_lock = RLock()

        layout = ensure_quality_dirs(run_context.output_dir)
        suffix = f"{run_context.execution_id}-{run_context.worker_id}.jsonl"
        self.paths = QualityShardPaths(
            cases=layout.shards / f"cases-{suffix}",
            requests=layout.shards / f"requests-{suffix}",
            integrity=layout.shards / f"integrity-{suffix}",
        )
        for path in (self.paths.cases, self.paths.requests, self.paths.integrity):
            path.write_text("", encoding="utf-8")

    def record_case(self, result: CaseResult) -> bool:
        return self._record_primary(
            self.paths.cases,
            result,
            failure_code="case_write_failed",
            related_id=result.invocation_id,
        )

    def record_request(self, metric: RequestMetric) -> bool:
        return self._record_primary(
            self.paths.requests,
            metric,
            failure_code="request_write_failed",
            related_id=metric.request_event_id,
        )

    def record_integrity(self, issue: IntegrityIssue) -> bool:
        try:
            self._append(self.paths.integrity, issue)
            return True
        except Exception as error:
            self._warn(
                "quality integrity write failed: "
                f"{type(error).__name__}: {_safe_message(error)}"
            )
            return False

    def capture_integrity(
        self,
        *,
        source: str,
        code: str,
        message: str,
        related_id: str | None = None,
        severity: IssueSeverity = IssueSeverity.WARN,
    ) -> bool:
        try:
            issue = IntegrityIssue(
                run_id=self.run_context.run_id,
                severity=severity,
                source=source,
                code=code,
                message=_safe_message(message),
                related_id=related_id,
                created_at=datetime.now(UTC),
            )
        except Exception as error:
            self._warn(
                "quality integrity construction failed: "
                f"{type(error).__name__}: {_safe_message(error)}"
            )
            return False
        return self.record_integrity(issue)

    def _record_primary(
        self,
        path: Path,
        record: CaseResult | RequestMetric,
        *,
        failure_code: str,
        related_id: str,
    ) -> bool:
        try:
            self._append(path, record)
            return True
        except Exception as error:
            self.capture_integrity(
                source="collector",
                code=failure_code,
                message=f"{type(error).__name__}: {_safe_message(error)}",
                related_id=related_id,
                severity=IssueSeverity.ERROR,
            )
            return False

    def _append(
        self,
        path: Path,
        record: CaseResult | RequestMetric | IntegrityIssue,
    ) -> None:
        with self._write_lock:
            append_jsonl(path, record)

    def _warn(self, message: str) -> None:
        try:
            self._warning_sink(message)
        except Exception:
            return


_COLLECTOR_LOCK = RLock()
_COLLECTOR: QualityCollector | None = None


def configure_collector(
    run_context: QualityRunContext,
    *,
    warning_sink: WarningSink | None = None,
) -> QualityCollector:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        collector = QualityCollector(run_context, warning_sink=warning_sink)
        _COLLECTOR = collector
        return collector


def get_collector(default: QualityCollector | None = None) -> QualityCollector | None:
    with _COLLECTOR_LOCK:
        return _COLLECTOR or default


def reset_collector() -> None:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        _COLLECTOR = None


def _safe_message(value: object) -> str:
    redacted = redact_quality_value(str(value), remove_url_query=True)
    return str(redacted).strip() or type(value).__name__


def _default_warning_sink(message: str) -> None:
    print(message, file=sys.stderr)
