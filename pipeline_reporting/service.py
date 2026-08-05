from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from html import escape
import json
import os
from pathlib import Path
import tempfile

from pipeline_reporting.builder import build_pipeline_report
from pipeline_reporting.config import load_pipeline_report_config
from pipeline_reporting.contracts import PipelineContext, PipelineReport
from pipeline_reporting.renderer import render_pipeline_summary
from pipeline_reporting.sources import load_pipeline_sources


def context_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    quality_enabled: bool | None = None,
) -> PipelineContext:
    values = environment if environment is not None else os.environ
    return PipelineContext(
        job_name=_value(values, "JOB_NAME", "local"),
        build_number=_value(values, "BUILD_NUMBER", "-"),
        build_url=_value(values, "BUILD_URL", ""),
        build_result=_value(values, "PIPELINE_BUILD_RESULT", "SUCCESS").upper(),
        branch=_value(values, "BRANCH_NAME", _value(values, "GIT_BRANCH", "-")),
        commit_sha=_value(values, "GIT_COMMIT", "-"),
        environment_name=(
            "china"
            if _bool_value(values.get("USE_CHINA_ENVIRONMENT"), default=True)
            else "overseas"
        ),
        duration_ms=_nonnegative_int(values.get("PIPELINE_DURATION_MS")),
        framework_tests_enabled=_bool_value(values.get("RUN_FRAMEWORK_TESTS")),
        smoke_collect_enabled=_bool_value(values.get("RUN_COLLECT_ONLY")),
        real_smoke_enabled=_bool_value(values.get("RUN_REAL_SMOKE")),
        quality_enabled=(
            _bool_value(values.get("QUALITY_ENABLE"))
            if quality_enabled is None
            else quality_enabled
        ),
        smoke_target=_value(values, "SMOKE_TARGET", "module/smoke"),
        parallel_workers=_value(values, "TEST_PARALLEL_WORKERS", "off"),
    )


def generate_pipeline_summary(
    workspace: str | Path,
    output_path: str | Path = "reports/pipeline-summary.md",
    *,
    environment: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    machine_output_path: str | Path | None = None,
    email_subject_path: str | Path | None = None,
    email_html_path: str | Path | None = None,
) -> PipelineReport | None:
    root = Path(workspace)
    config = load_pipeline_report_config(
        environment,
        dotenv_path=dotenv_path or root / ".env",
    )
    target = root / output_path if not Path(output_path).is_absolute() else Path(output_path)
    if not config.enabled:
        target.unlink(missing_ok=True)
        for optional_path in (
            machine_output_path,
            email_subject_path,
            email_html_path,
        ):
            if optional_path is not None:
                _resolve_output_path(root, optional_path).unlink(missing_ok=True)
        return None
    context = context_from_environment(
        environment,
        quality_enabled=config.quality_enabled,
    )
    report = build_pipeline_report(
        context,
        load_pipeline_sources(
            root,
            include_quality=(
                context.real_smoke_enabled and context.quality_enabled
            ),
        ),
    )
    _write_text_atomic(target, render_pipeline_summary(report))
    if machine_output_path is not None:
        _write_text_atomic(
            _resolve_output_path(root, machine_output_path),
            json.dumps(
                _jsonable(report),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    if email_subject_path is not None:
        _write_text_atomic(
            _resolve_output_path(root, email_subject_path),
            _render_email_subject(report) + "\n",
        )
    if email_html_path is not None:
        _write_text_atomic(
            _resolve_output_path(root, email_html_path),
            _render_email_html(report),
        )
    return report


def _resolve_output_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            key: _jsonable(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _render_email_subject(report: PipelineReport) -> str:
    failed = (
        report.unit_tests.failed
        + report.unit_tests.errors
        + report.smoke_tests.failed
        + report.smoke_tests.errors
    )
    total = report.unit_tests.total + report.smoke_tests.total
    label = {
        "PASS": "构建成功",
        "WARN": "构建需关注",
        "FAIL": "构建失败",
        "NO_DATA": "构建无数据",
    }[report.conclusion.value]
    result_text = f"{failed} 失败 / {total} 项" if total else "测试报告未生成"
    return (
        f"【{label}】{report.context.job_name} "
        f"#{report.context.build_number}｜{result_text}"
    )


def _render_email_html(report: PipelineReport) -> str:
    context = report.context
    failed = (
        report.unit_tests.failed
        + report.unit_tests.errors
        + report.smoke_tests.failed
        + report.smoke_tests.errors
    )
    total = report.unit_tests.total + report.smoke_tests.total
    skipped = report.unit_tests.skipped + report.smoke_tests.skipped
    passed = max(total - failed - skipped, 0)
    collect = report.smoke_collect
    build_url = context.build_url.rstrip("/") + "/" if context.build_url else ""
    links = []
    if build_url:
        links = [
            ("流水线执行摘要", f"{build_url}artifact/reports/pipeline-summary.md"),
            ("Allure 报告", f"{build_url}allure/"),
            ("JUnit 报告", f"{build_url}testReport/"),
            ("构建产物", f"{build_url}artifact/"),
        ]
    link_html = "　".join(
        f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
        for label, url in links
    ) or "构建链接不可用"
    collect_text = "未执行或无数据"
    if collect.available:
        collect_text = f"{collect.total} 项"
        if collect.parallel is not None and collect.serial is not None:
            collect_text += f"（并发 {collect.parallel} / 串行 {collect.serial}）"
    failed_cases = (
        *report.unit_tests.failed_cases,
        *report.smoke_tests.failed_cases,
    )
    failed_cases_html = ""
    if failed_cases:
        items = "".join(
            f"<li>{escape(item.name)}</li>" for item in failed_cases[:5]
        )
        failed_cases_html = f"<h3>失败用例（最多 5 项）</h3><ul>{items}</ul>"
    return (
        "<html><body>"
        f"<h2>{escape(report.conclusion_text)}</h2>"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\">"
        f"<tr><td>构建</td><td>{escape(context.job_name)} #{escape(context.build_number)}</td></tr>"
        f"<tr><td>Jenkins 结果</td><td>{escape(context.build_result)}</td></tr>"
        f"<tr><td>用例结果</td><td>{total} 总计 / {passed} 通过 / {failed} 失败 / {skipped} 跳过</td></tr>"
        f"<tr><td>用例收集</td><td>{escape(collect_text)}</td></tr>"
        "</table>"
        f"{failed_cases_html}"
        f"<p>{link_html}</p>"
        "<p>详细执行与质量数据请在构建产物中查看。</p>"
        "</body></html>"
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _value(values: Mapping[str, str], name: str, default: str) -> str:
    value = values.get(name)
    text = str(value).strip() if value is not None else ""
    return text or default


def _bool_value(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"true", "1", "yes", "on"}


def _nonnegative_int(value: str | None) -> int:
    try:
        return max(int(str(value)), 0) if value is not None else 0
    except ValueError:
        return 0
