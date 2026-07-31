from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.models import IntegrityStatus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge quality shard files for one run.")
    subparsers = parser.add_subparsers(dest="command")
    merge_parser = subparsers.add_parser("merge", help="merge one quality run")
    merge_parser.add_argument("--run-id", required=True)
    merge_parser.add_argument("--output-dir", default="reports/quality")
    merge_parser.add_argument("--expected-execution", action="append", default=[])
    merge_parser.add_argument("--expected-case-count", type=int)
    merge_parser.add_argument("--junit", action="append", default=[])
    parsed = parser.parse_args(argv)

    if parsed.command != "merge":
        parser.print_help()
        return 2
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


if __name__ == "__main__":
    raise SystemExit(main())
