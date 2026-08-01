from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[2] / "Jenkinsfile"


def test_real_smoke_enables_quality_report_and_email_links_artifact():
    content = JENKINSFILE.read_text(encoding="utf-8")

    assert content.index("deleteDir()") < content.index("def scmVars = checkout scm")
    assert "env.GIT_COMMIT = scmVars.GIT_COMMIT" in content
    assert "env.GIT_BRANCH = scmVars.GIT_BRANCH" in content
    assert "$env:QUALITY_ENABLE = '1'" in content
    assert "$env:QUALITY_SEMANTIC_ENABLE = '1'" in content
    assert "$env:QUALITY_METRICS_ENABLE = '1'" in content
    assert "$env:QUALITY_P1_REPORT_ENABLE = '1'" in content
    assert "$env:QUALITY_FLAKY_HISTORY_ENABLE = '0'" in content
    assert "$env:QUALITY_FLAKY_HISTORY_ENABLE = '1'" in content
    assert "$env:QUALITY_FLAKY_STATE_ENABLE = '0'" in content
    assert "$env:QUALITY_FLAKY_STATE_ENABLE = '1'" in content
    assert "archiveArtifacts artifacts: 'allure-results/**, reports/**'" in content
    assert "junit allowEmptyResults: true, testResults: 'reports/smoke-tests*.xml'" in content
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


def test_email_summary_distinguishes_available_missing_and_not_applicable_data():
    content = JENKINSFILE.read_text(encoding="utf-8")

    assert "passed: 0" in content
    assert "casePassed: summary.case_passed ?: 0" in content
    assert "failedTests: []" in content
    assert "失败用例（最多 5 项）" in content
    assert "邮件数据完整性" in content
    assert "本次未执行真实 Smoke，P0 质量结论不适用" in content
    assert "本次未执行真实 Smoke，P1 指标与 Flaky 结论不适用" in content
    assert "realSmokeEnabled && quality.available" in content
    assert "realSmokeEnabled && p1.available" in content
    assert "normalizeBranchName(env.BRANCH_NAME ?: env.GIT_BRANCH)" in content
    assert "shortGitCommit(env.GIT_COMMIT)" in content
    assert "'dev2'" not in content
