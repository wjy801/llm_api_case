from __future__ import annotations

import argparse
from pathlib import Path
import sys

from pipeline_reporting.config import GENERATE_PIPELINE_SUMMARY_ENV
from pipeline_reporting.contracts import StageStatus
from pipeline_reporting.service import generate_pipeline_summary
from pipeline_reporting.sources import (
    initialize_stage_status_file,
    update_stage_status_file,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "initialize-stages":
        initialize_stage_status_file(
            args.path,
            framework_tests_enabled=args.framework_tests,
            smoke_collect_enabled=args.smoke_collect,
            real_smoke_enabled=args.real_smoke,
        )
        return 0
    if args.command == "set-stage":
        update_stage_status_file(
            args.path,
            stage_name=args.name,
            status=StageStatus(args.status),
        )
        return 0
    if args.command == "generate":
        report = generate_pipeline_summary(
            args.workspace,
            args.output,
            dotenv_path=args.dotenv,
        )
        if report is None:
            print(f"Pipeline summary generation is disabled by {GENERATE_PIPELINE_SUMMARY_ENV}.")
        else:
            print(
                "Pipeline summary generated: "
                f"conclusion={report.conclusion.value}, output={args.output}"
            )
        return 0
    parser.error("unsupported command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Jenkins pipeline execution summary.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize-stages")
    initialize.add_argument("--path", default="reports/pipeline-stage-status.json")
    initialize.add_argument("--framework-tests", type=_parse_bool, required=True)
    initialize.add_argument("--smoke-collect", type=_parse_bool, required=True)
    initialize.add_argument("--real-smoke", type=_parse_bool, required=True)

    set_stage = subparsers.add_parser("set-stage")
    set_stage.add_argument("--path", default="reports/pipeline-stage-status.json")
    set_stage.add_argument("--name", required=True)
    set_stage.add_argument(
        "--status",
        choices=[item.value for item in StageStatus],
        required=True,
    )

    generate = subparsers.add_parser("generate")
    generate.add_argument("--workspace", default=".")
    generate.add_argument("--output", default="reports/pipeline-summary.md")
    generate.add_argument("--dotenv", default=".env")
    return parser


def _parse_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


if __name__ == "__main__":
    sys.exit(main())
