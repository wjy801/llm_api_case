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
    assert "测试报告未生成，构建可能在测试阶段前失败。" in content
    assert "请查看控制台日志" not in email_content
    assert "详细执行与质量数据请在构建产物中查看。" in content
    assert "${junit.tests} 总计 / ${junit.passed} 通过 / ${failedCount} 失败 / ${junit.skipped} 跳过" in content
    assert "${smoke.total} 项（并发 ${smoke.parallel} / 串行 ${smoke.serial}）" in content
    assert '>用例收集</td>' in email_content
    assert "parts << '用例收集'" in email_content
    assert 'parts << "接口测试（${params.SMOKE_TARGET}）"' in email_content
    assert '>Smoke</td>' not in email_content
    assert "buildExecutionSummary()" in email_content
    assert ">构建详情</a>" not in email_content
    assert ">控制台日志</a>" not in email_content
    assert "fileExists('reports/pipeline-summary.md')" in email_content
    assert "artifact/reports/pipeline-summary.md" in email_content
    assert ">流水线执行摘要</a>" in email_content
    assert "fileExists('reports/quality/gate-report.md')" in email_content
    assert "artifact/reports/quality/gate-report.md" in email_content
    assert ">P0 质量门禁报告</a>" in email_content
    assert "fileExists('reports/quality/p1-observation.md')" in email_content
    assert "artifact/reports/quality/p1-observation.md" in email_content
    assert ">P1 观察报告</a>" in email_content
    assert "normalizeBranchName(env.BRANCH_NAME ?: env.GIT_BRANCH)" in content
    assert "shortGitCommit(env.GIT_COMMIT)" in content
    assert 'subject: "【${statusText}】${env.JOB_NAME} #${env.BUILD_NUMBER}｜${resultText}"' in content
    assert "body: buildResultSummaryHtml(status, junit, smoke)" in content
    assert "'dev2'" not in content


def test_pipeline_summary_is_parameterized_generated_before_archive_and_has_fallback():
    content = JENKINSFILE.read_text(encoding="utf-8")

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
    assert content.index("generatePipelineSummary()") < content.index("archiveArtifacts artifacts:")


def test_jenkins_build_parameters_have_chinese_descriptions():
    content = JENKINSFILE.read_text(encoding="utf-8")
    parameter_block = content[content.index("parameters {") : content.index("environment {")]

    assert parameter_block.count("description:") == 8
    assert "执行 tests 目录下的离线框架测试。" in parameter_block
    assert "仅收集 Smoke 用例并统计分池，不调用真实接口。" in parameter_block
    assert "执行真实 Smoke；会产生外部调用和费用，默认关闭。" in parameter_block
    assert "为本轮构建生成 reports/pipeline-summary.md 执行摘要。" in parameter_block
    assert "TRUE 使用中国环境，FALSE 使用海外环境。" in parameter_block
