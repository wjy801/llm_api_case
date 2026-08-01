from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[2] / "Jenkinsfile"


def test_real_smoke_enables_quality_reports_and_archives_artifacts():
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
    assert "Map readQualitySummary()" not in content
    assert "Map readP1ObservationSummary()" not in content
    assert "Map parseJsonObject(String text)" not in content


def test_email_summary_is_compact_and_excludes_quality_stage_details():
    content = JENKINSFILE.read_text(encoding="utf-8")
    email_content = content[
        content.index("String buildResultSummaryHtml") : content.index("String normalizeBranchName")
    ]

    assert "passed: 0" in content
    assert "failedTests: []" in content
    assert "失败用例（最多 5 项）" in content
    assert "测试报告未生成，构建可能在测试阶段前失败，请查看控制台日志。" in content
    assert "详细质量数据请在构建产物中查看。" in content
    assert "${junit.tests} 总计 / ${junit.passed} 通过 / ${failedCount} 失败 / ${junit.skipped} 跳过" in content
    assert "${smoke.total} 项（并发 ${smoke.parallel} / 串行 ${smoke.serial}）" in content
    assert "buildExecutionSummary()" in email_content
    assert "artifact/reports/quality" not in email_content
    assert "P0" not in email_content
    assert "P1" not in email_content
    assert "normalizeBranchName(env.BRANCH_NAME ?: env.GIT_BRANCH)" in content
    assert "shortGitCommit(env.GIT_COMMIT)" in content
    assert 'subject: "【${statusText}】${env.JOB_NAME} #${env.BUILD_NUMBER}｜${resultText}"' in content
    assert "body: buildResultSummaryHtml(status, junit, smoke)" in content
    assert "'dev2'" not in content
