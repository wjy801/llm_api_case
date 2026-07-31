from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import sys

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.config import (
    QUALITY_HTTP_5XX_WARN_RATE_ENV,
    QUALITY_MIN_REQUEST_SAMPLES_ENV,
    QUALITY_SHADOW_GATE_ENV,
    QUALITY_TIMEOUT_WARN_RATE_ENV,
    load_quality_report_config,
)
from quality.models import IntegrityStatus
from quality.report import QualityReportRequest, generate_quality_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quality fact merge and report tools.")
    subparsers = parser.add_subparsers(dest="command")
    merge_parser = subparsers.add_parser("merge", help="merge one quality run")
    merge_parser.add_argument("--run-id", required=True)
    merge_parser.add_argument("--output-dir", default="reports/quality")
    merge_parser.add_argument("--expected-execution", action="append", default=[])
    merge_parser.add_argument("--expected-case-count", type=int)
    merge_parser.add_argument("--junit", action="append", default=[])
    report_parser = subparsers.add_parser("report", help="generate a quality report")
    report_parser.add_argument("--run-id", required=True)
    report_parser.add_argument("--output-dir", default="reports/quality")
    report_parser.add_argument("--min-request-samples", type=int)
    report_parser.add_argument("--http-5xx-warn-rate", type=float)
    report_parser.add_argument("--timeout-warn-rate", type=float)
    report_parser.add_argument(
        "--no-shadow-gate",
        action="store_false",
        dest="shadow_gate",
        default=None,
    )
    try:
        parsed = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if parsed.command is None:
        parser.print_help()
        return 2
    if parsed.command == "report":
        return _report(parsed)
    if parsed.expected_case_count is not None and parsed.expected_case_count < 0:
        print("--expected-case-count must be greater than or equal to 0", file=sys.stderr)
        return 2

    result = merge_quality_run(
        QualityMergeRequest(
            run_id=parsed.run_id,
            output_dir=Path(parsed.output_dir),
            expected_execution_ids=tuple(parsed.expected_execution),
            expected_case_count=parsed.expected_case_count,
            junit_files=tuple(Path(path) for path in parsed.junit),
            run_start_time=datetime.now(UTC),
        )
    )
    print(
        "quality merge completed: "
        f"integrity={result.integrity_status.value}, "
        f"cases={result.case_results}, "
        f"requests={result.request_metrics}, "
        f"failures={result.failure_occurrences}"
    )
    return 2 if result.integrity_status is IntegrityStatus.FAILED and result.case_results == 0 else 0


def _report(parsed: argparse.Namespace) -> int:
    try:
        environment = dict(os.environ)
        cli_overrides = (
            ("min_request_samples", QUALITY_MIN_REQUEST_SAMPLES_ENV),
            ("http_5xx_warn_rate", QUALITY_HTTP_5XX_WARN_RATE_ENV),
            ("timeout_warn_rate", QUALITY_TIMEOUT_WARN_RATE_ENV),
            ("shadow_gate", QUALITY_SHADOW_GATE_ENV),
        )
        for attribute, environment_name in cli_overrides:
            if getattr(parsed, attribute) is not None:
                environment.pop(environment_name, None)
        configured = load_quality_report_config(environment)
        min_request_samples = (
            configured.min_request_samples
            if parsed.min_request_samples is None
            else parsed.min_request_samples
        )
        http_5xx_warn_rate = (
            configured.http_5xx_warn_rate
            if parsed.http_5xx_warn_rate is None
            else parsed.http_5xx_warn_rate
        )
        timeout_warn_rate = (
            configured.timeout_warn_rate
            if parsed.timeout_warn_rate is None
            else parsed.timeout_warn_rate
        )
        shadow_gate = configured.shadow_gate if parsed.shadow_gate is None else parsed.shadow_gate
        result = generate_quality_report(
            QualityReportRequest(
                run_id=parsed.run_id,
                output_dir=Path(parsed.output_dir),
                shadow_gate=shadow_gate,
                min_request_samples=min_request_samples,
                http_5xx_warn_rate=http_5xx_warn_rate,
                timeout_warn_rate=timeout_warn_rate,
            )
        )
    except Exception as error:
        print(f"quality report failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(
        "quality report completed: "
        f"overall={result.overall.value}, integrity={result.integrity_status.value}, "
        f"path={result.gate_report_md_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
