from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[2] / "Jenkinsfile"


def test_real_smoke_enables_quality_reports_and_archives_artifacts():
    content = JENKINSFILE.read_text(encoding="utf-8")

    assert "buildDiscarder(logRotator(" in content
    assert "artifactDaysToKeepStr: '4'" in content
    assert "artifactNumToKeepStr: '-1'" in content
    assert "daysToKeepStr: '-1'" in content
    assert "numToKeepStr: '-1'" in content
    assert content.index("deleteDir()") < content.index("def scmVars = checkout scm")
    assert "env.GIT_COMMIT = scmVars.GIT_COMMIT" in content
    assert "env.GIT_BRANCH = scmVars.GIT_BRANCH" in content
    assert "$env:QUALITY_ENABLE = '1'" in content
    assert "$env:QUALITY_SEMANTIC_ENABLE = '1'" in content
    assert "$env:QUALITY_METRICS_ENABLE = '1'" in content
    assert "QUALITY_P1_REPORT_ENABLE" not in content
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
    assert "QUALITY_SHADOW_GATE" not in content
    assert "QUALITY_MIN_REQUEST_SAMPLES" not in content
    assert "QUALITY_HTTP_5XX_WARN_RATE" not in content
    assert "QUALITY_TIMEOUT_WARN_RATE" not in content
    assert "Map readQualitySummary()" not in content
    assert "Map readP1ObservationSummary()" not in content
    assert "Map parseJsonObject(String text)" not in content
    assert "$reportsPath = Join-Path (Get-Location) 'reports'" in content
    assert "Remove-Item -LiteralPath $reportsPath -Recurse -Force" in content
    assert content.index("Remove-Item -LiteralPath $reportsPath -Recurse -Force") < content.index("New-Item -ItemType Directory -Force -Path $reportsPath")


def test_email_consumes_the_shared_python_report_without_reparsing_junit():
    content = JENKINSFILE.read_text(encoding="utf-8")
    email_content = content[
        content.index("void notifyByEmail") : content.index("@NonCPS\nString htmlEscape")
    ]

    assert "Map readJunitSummary()" not in content
    assert "Map readSmokeCollectSummary()" not in content
    assert "parseJunitXmlText" not in content
    assert "parseSmokeCollectText" not in content
    assert "reports/pipeline-summary.json" in content
    assert "reports/pipeline-email-subject.txt" in content
    assert "reports/pipeline-email.html" in content
    assert "readFile('reports/pipeline-email-subject.txt').trim()" in email_content
    assert "readFile('reports/pipeline-email.html')" in email_content
    assert "buildFallbackEmailHtml(status)" in email_content
    assert "emailext(" in email_content
    assert "body: body" in email_content
    assert "gate-report" not in email_content
    assert "P0 质量门禁报告" not in email_content
    assert "p1-observation" not in email_content
    assert "P1 观察报告" not in email_content
    assert "'dev2'" not in content


def test_pipeline_summary_is_parameterized_generated_before_archive_and_has_fallback():
    content = JENKINSFILE.read_text(encoding="utf-8")
    generator = content[
        content.index("void generatePipelineSummary()") : content.index("void writeFallbackPipelineSummary()")
    ]

    assert "booleanParam(name: 'GENERATE_PIPELINE_SUMMARY', defaultValue: true" in content
    assert "GENERATE_PIPELINE_SUMMARY=true" in content
    assert "\\$env:GENERATE_PIPELINE_SUMMARY = '${params.GENERATE_PIPELINE_SUMMARY}'" in content
    assert "initializePipelineStageStatus()" in content
    assert "updatePipelineStageStatus('framework_tests', 'PASSED')" in content
    assert "updatePipelineStageStatus('smoke_collect', 'PASSED')" in content
    assert "updatePipelineStageStatus('real_smoke', 'PASSED')" in content
    assert "void generatePipelineSummary()" in content
    assert "void writeFallbackPipelineSummary()" in content
    assert "Pipeline summary generation is disabled by GENERATE_PIPELINE_SUMMARY." in content
    assert generator.index("if (!pipelineSummaryEnabled())") < generator.index("try {")
    assert generator.index("return") < generator.index("writeFallbackPipelineSummary")
    assert "currentBuild.result =" not in generator
    assert "currentBuild.currentResult =" not in generator
    assert content.index("generatePipelineSummary()") < content.index("allure includeProperties:")
    assert content.index("generatePipelineSummary()") < content.index("archiveArtifacts artifacts:")
    assert content.index("generatePipelineSummary()") < content.index("notifyByEmail('FAILED')")


def test_jenkins_build_parameters_have_chinese_descriptions():
    content = JENKINSFILE.read_text(encoding="utf-8")
    parameter_block = content[content.index("parameters {") : content.index("environment {")]

    assert parameter_block.count("description:") == 8
    assert "执行 tests 目录下的离线框架测试。" in parameter_block
    assert "仅收集 Smoke 用例并统计分池，不调用真实接口。" in parameter_block
    assert "执行真实 Smoke；会产生外部调用和费用，默认关闭。" in parameter_block
    assert "为本轮构建生成 reports/pipeline-summary.md 执行摘要。" in parameter_block
    assert "TRUE 使用中国环境，FALSE 使用海外环境。" in parameter_block
