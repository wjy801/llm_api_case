from __future__ import annotations

import ast
from dataclasses import MISSING, fields
import inspect
from pathlib import Path

import quality
import quality.observation_report as observation_report
from quality.observation_models import SourceExpectation


EXPECTED_PUBLIC_SYMBOLS = (
    "P1ObservationGenerationResult",
    "P1ObservationRequest",
    "generate_p1_observation_report",
    "render_p1_observation_markdown",
)


def test_observation_report_is_a_package_with_the_frozen_public_api():
    package_dir = Path(observation_report.__file__).resolve().parent

    assert observation_report.__spec__.submodule_search_locations is not None
    assert package_dir.name == "observation_report"
    assert not (package_dir.parent / "observation_report.py").exists()
    assert observation_report.__all__ == EXPECTED_PUBLIC_SYMBOLS
    assert all(
        getattr(quality, name) is getattr(observation_report, name)
        for name in EXPECTED_PUBLIC_SYMBOLS
    )


def test_observation_public_dataclass_fields_and_defaults_are_compatible():
    request_fields = fields(observation_report.P1ObservationRequest)
    assert tuple(field.name for field in request_fields) == (
        "run_id",
        "output_dir",
        "metrics_expectation",
        "flaky_import_expectation",
        "flaky_evaluation_expectation",
    )
    assert request_fields[0].default is MISSING
    assert request_fields[1].default is MISSING
    assert tuple(field.default for field in request_fields[2:]) == (
        SourceExpectation.REQUIRED,
        SourceExpectation.REQUIRED,
        SourceExpectation.REQUIRED,
    )

    result_fields = fields(observation_report.P1ObservationGenerationResult)
    assert tuple(field.name for field in result_fields) == (
        "run_id",
        "output_dir",
        "manifest_path",
        "json_path",
        "markdown_path",
        "write_status",
        "report_status",
        "issue_codes",
        "report",
    )
    assert result_fields[-1].default is None


def test_observation_public_function_parameters_are_compatible():
    assert tuple(
        inspect.signature(observation_report.generate_p1_observation_report).parameters
    ) == ("request",)
    assert tuple(
        inspect.signature(observation_report.render_p1_observation_markdown).parameters
    ) == ("report",)


def test_observation_package_init_contains_no_business_implementation():
    init_path = Path(observation_report.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    forbidden = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    assert not any(isinstance(node, forbidden) for node in ast.walk(tree))
