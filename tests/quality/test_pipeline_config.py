from __future__ import annotations

from pathlib import Path

import pytest

from quality.flaky_probe import ProbeRuntimeConfig, load_probe_runtime_config
from quality.pipeline_config import (
    DEFAULT_ACTIVE_POLL_SECONDS,
    DEFAULT_IDLE_POLL_SECONDS,
    DEFAULT_QUALITY_GRACE_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    PipelineMonitorConfig,
    load_pipeline_monitor_config,
    read_jenkins_read_credentials,
    validate_dashboard_jenkins_origin,
)


def _valid_environment(credential_file: Path) -> dict[str, str]:
    return {
        "QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE": "1",
        "QUALITY_DASHBOARD_JENKINS_ORIGIN": "https://jenkins.example.internal",
        "QUALITY_DASHBOARD_JENKINS_JOB": "folder/api-case-main",
        "QUALITY_DASHBOARD_JENKINS_CREDENTIAL_FILE": str(credential_file),
    }


def _external_credential(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    credential = secrets / "dashboard-main-readonly.txt"
    credential.write_text("reader:api-token\n", encoding="utf-8")
    return repository, credential


def test_pipeline_monitor_is_disabled_by_default_without_filesystem_side_effects(
    tmp_path,
):
    config = load_pipeline_monitor_config({}, repository_root=tmp_path)

    assert config == PipelineMonitorConfig()
    assert tuple(tmp_path.iterdir()) == ()


def test_valid_pipeline_monitor_config_is_independent_and_reads_credentials(tmp_path):
    repository, credential = _external_credential(tmp_path)
    values = {
        **_valid_environment(credential),
        "QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS": "5",
        "QUALITY_DASHBOARD_IDLE_POLL_SECONDS": "30",
        "QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS": "4.5",
        "QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS": "120",
    }

    config = load_pipeline_monitor_config(values, repository_root=repository)
    credentials = read_jenkins_read_credentials(config.credential_file)

    assert config.enabled is True
    assert config.requested_enabled is True
    assert config.warning is None
    assert config.jenkins_origin == "https://jenkins.example.internal"
    assert config.job_full_name == "folder/api-case-main"
    assert config.credential_file == credential.resolve()
    assert config.active_poll_seconds == 5
    assert config.idle_poll_seconds == 30
    assert config.request_timeout_seconds == 4.5
    assert config.quality_grace_seconds == 120
    assert credentials.username == "reader"
    assert credentials.token == "api-token"
    assert "api-token" not in repr(credentials)


def test_complete_configuration_can_remain_kill_switched(tmp_path):
    repository, credential = _external_credential(tmp_path)
    values = _valid_environment(credential)
    values["QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE"] = "0"

    config = load_pipeline_monitor_config(values, repository_root=repository)

    assert config.requested_enabled is False
    assert config.enabled is False
    assert config.warning is None
    assert config.jenkins_origin == "https://jenkins.example.internal"


@pytest.mark.parametrize(
    ("changes", "warning"),
    [
        (
            {"QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE": "maybe"},
            "QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE",
        ),
        (
            {"QUALITY_DASHBOARD_JENKINS_ORIGIN": "http://jenkins.example.internal"},
            "QUALITY_DASHBOARD_JENKINS_ORIGIN",
        ),
        (
            {"QUALITY_DASHBOARD_JENKINS_ORIGIN": "https://user:secret@jenkins.example.internal"},
            "QUALITY_DASHBOARD_JENKINS_ORIGIN",
        ),
        (
            {"QUALITY_DASHBOARD_JENKINS_JOB": "../other-job"},
            "QUALITY_DASHBOARD_JENKINS_JOB",
        ),
        (
            {"QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS": "31"},
            "QUALITY_DASHBOARD_ACTIVE_POLL_SECONDS",
        ),
        (
            {"QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS": "0"},
            "QUALITY_DASHBOARD_REQUEST_TIMEOUT_SECONDS",
        ),
        (
            {"QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS": "NaN"},
            "QUALITY_DASHBOARD_QUALITY_GRACE_SECONDS",
        ),
    ],
)
def test_invalid_configuration_fails_closed_without_raising(tmp_path, changes, warning):
    repository, credential = _external_credential(tmp_path)
    values = {**_valid_environment(credential), **changes}

    config = load_pipeline_monitor_config(values, repository_root=repository)

    assert config.enabled is False
    assert warning in str(config.warning)
    assert "secret" not in str(config.warning)


def test_missing_or_repository_local_credential_disables_only_monitor(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    local_credential = repository / "credential.txt"
    local_credential.write_text("reader:token", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    local = load_pipeline_monitor_config(
        _valid_environment(local_credential),
        repository_root=repository,
    )
    absent = load_pipeline_monitor_config(
        _valid_environment(missing),
        repository_root=repository,
    )

    assert local.enabled is False
    assert "outside the repository" in str(local.warning)
    assert absent.enabled is False
    assert "does not exist" in str(absent.warning)
    assert str(local_credential) not in str(local.warning)
    assert str(missing) not in str(absent.warning)


def test_main_and_probe_credentials_must_be_separate(tmp_path):
    repository, credential = _external_credential(tmp_path)
    values = {
        **_valid_environment(credential),
        "QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE": str(credential),
    }

    config = load_pipeline_monitor_config(values, repository_root=repository)

    assert config.enabled is False
    assert "must be separate" in str(config.warning)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "missing-colon",
        ":token",
        "reader:",
        "reader:token\nsecond:token",
    ],
)
def test_read_only_credential_contract_rejects_malformed_files(tmp_path, content):
    credential = tmp_path / "credential.txt"
    credential.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="username:token"):
        read_jenkins_read_credentials(credential)


@pytest.mark.parametrize(
    "origin",
    [
        "http://jenkins.example.internal",
        "https://jenkins.example.internal/path",
        "https://jenkins.example.internal?token=secret",
        "https://user:secret@jenkins.example.internal",
        "jenkins.example.internal",
    ],
)
def test_jenkins_origin_requires_a_clean_https_origin(origin):
    with pytest.raises(ValueError, match="HTTPS origin"):
        validate_dashboard_jenkins_origin(origin)


def test_default_monitor_values_freeze_the_stage_a_contract(tmp_path):
    repository, credential = _external_credential(tmp_path)

    config = load_pipeline_monitor_config(
        _valid_environment(credential),
        repository_root=repository,
    )

    assert config.active_poll_seconds == DEFAULT_ACTIVE_POLL_SECONDS == 5.0
    assert config.idle_poll_seconds == DEFAULT_IDLE_POLL_SECONDS == 30.0
    assert config.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS == 5.0
    assert config.quality_grace_seconds == DEFAULT_QUALITY_GRACE_SECONDS == 120.0


def test_main_pipeline_environment_does_not_change_probe_configuration():
    values = {
        "QUALITY_DASHBOARD_MAIN_PIPELINE_ENABLE": "1",
        "QUALITY_DASHBOARD_JENKINS_ORIGIN": "invalid",
    }

    assert load_probe_runtime_config(values) == ProbeRuntimeConfig()
