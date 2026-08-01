from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[2] / "Jenkinsfile"


def test_real_smoke_enables_quality_report_and_email_links_artifact():
    content = JENKINSFILE.read_text(encoding="utf-8")

    assert "deleteDir()\n                checkout scm" in content
    assert "$env:QUALITY_ENABLE = '1'" in content
    assert "$env:QUALITY_SEMANTIC_ENABLE = '1'" in content
    assert "$env:QUALITY_METRICS_ENABLE = '1'" in content
    assert "$env:QUALITY_P1_REPORT_ENABLE = '1'" in content
    assert "$env:QUALITY_FLAKY_HISTORY_ENABLE = '0'" in content
    assert "$env:QUALITY_FLAKY_HISTORY_ENABLE = '1'" in content
    assert "$env:QUALITY_FLAKY_STATE_ENABLE = '0'" in content
    assert "$env:QUALITY_FLAKY_STATE_ENABLE = '1'" in content
    assert "archiveArtifacts artifacts: 'allure-results/**, reports/**'" in content
    assert "$flakyEnvFiles = @('.env', 'D:/API_CASE/.env')" in content
    assert "Get-Content -LiteralPath $flakyEnvFile" in content
    assert "Where-Object { $_ -match '^\\\\s*QUALITY_FLAKY_DB_PATH\\\\s*=' }" in content
    assert "IsNullOrWhiteSpace($env:QUALITY_FLAKY_DB_PATH) -and" in content
    assert "IsNullOrWhiteSpace($env:QUALITY_FLAKY_DB_PATH)" in content
    assert "D:/API_CASE_DATA" not in content
    assert "D:\\API_CASE_DATA" not in content
    assert "$env:QUALITY_OUTPUT_DIR = 'reports/quality'" in content
    assert "$env:QUALITY_SHADOW_GATE = '1'" in content
    assert "Map readQualitySummary()" in content
    assert "catch (error)" in content
    assert "artifact/reports/quality/gate-report.md" in content
    assert "readP1ObservationSummary()" in content
    assert "p1-observation-manifest.json" in content
    assert "artifact/reports/quality/p1-observation.md" in content
