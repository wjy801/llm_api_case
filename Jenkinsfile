pipeline {
    agent { label 'Windows' }

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(
            artifactDaysToKeepStr: '4',
            artifactNumToKeepStr: '-1',
            daysToKeepStr: '-1',
            numToKeepStr: '-1'
        ))
    }

    triggers {
        parameterizedCron('''
            0 0 * * * % RUN_FRAMEWORK_TESTS=true;RUN_COLLECT_ONLY=false;RUN_REAL_SMOKE=true;GENERATE_PIPELINE_SUMMARY=true;ALWAYS_SEND_REPORT_EMAIL=true;USE_CHINA_ENVIRONMENT=TRUE;SMOKE_TARGET=module/smoke;TEST_PARALLEL_WORKERS=off
        ''')
    }

    parameters {
        booleanParam(name: 'RUN_FRAMEWORK_TESTS', defaultValue: true, description: '执行 tests 目录下的离线框架测试。')
        booleanParam(name: 'RUN_COLLECT_ONLY', defaultValue: true, description: '仅收集 Smoke 用例并统计分池，不调用真实接口。')
        booleanParam(name: 'RUN_REAL_SMOKE', defaultValue: false, description: '执行真实 Smoke；会产生外部调用和费用，默认关闭。')
        booleanParam(name: 'GENERATE_PIPELINE_SUMMARY', defaultValue: true, description: '为本轮构建生成 reports/pipeline-summary.md 执行摘要。')
        booleanParam(name: 'ALWAYS_SEND_REPORT_EMAIL', defaultValue: false, description: '成功构建也发送报告邮件；失败或不稳定构建始终发送。')
        choice(name: 'USE_CHINA_ENVIRONMENT', choices: ['TRUE', 'FALSE'], description: 'TRUE 使用中国环境，FALSE 使用海外环境。')
        string(name: 'SMOKE_TARGET', defaultValue: 'module/smoke', description: '真实 Smoke 的目标目录、文件或 pytest nodeid。', trim: true)
        choice(name: 'TEST_PARALLEL_WORKERS', choices: ['off', 'auto', '2', '4', '8'], description: 'off 关闭并发；auto/2/4/8 启用 pytest-xdist 并设置 worker 数量。')
    }

    environment {
        CI_MAIL_TO = 'wujinyang@qiqikeji.com'
        CI_MAIL_FROM = '13463214057@163.com'
        GENERATE_ALLURE_REPORT = 'FALSE'
        GENERATE_HISTORY_REPORT = 'FALSE'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        PIP_INDEX_URL = 'https://repo.huaweicloud.com/repository/pypi/simple'
        PIP_TRUSTED_HOST = 'repo.huaweicloud.com'
        PIP_DEFAULT_TIMEOUT = '60'
        PIP_RETRIES = '2'
        NPM_CONFIG_REGISTRY = 'https://registry.npmmirror.com'
        PYTHONIOENCODING = 'utf-8'
        PYTHONUTF8 = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                deleteDir()
                script {
                    def scmVars = checkout scm
                    env.GIT_COMMIT = scmVars.GIT_COMMIT ?: env.GIT_COMMIT ?: ''
                    env.GIT_BRANCH = scmVars.GIT_BRANCH ?: env.GIT_BRANCH ?: ''
                }
            }
        }

        stage('Check Runtime Env') {
            steps {
                ciPowerShell('''
                $sourceEnv = 'D:/API_CASE/.env'
                if (!(Test-Path .env) -and (Test-Path -LiteralPath $sourceEnv)) {
                    Copy-Item -LiteralPath $sourceEnv -Destination .env -Force
                }
                if (!(Test-Path .env)) {
                    Write-Error ".env does not exist in workspace and source path was not found: $sourceEnv"
                }
                ''')
            }
        }

        stage('Prepare Python Env') {
            steps {
                ciPowerShell('''
                python --version
                if (!(Test-Path .venv)) {
                    python -m venv .venv
                }
                ./.venv/Scripts/python.exe --version
                ./.venv/Scripts/python.exe -m pip install --upgrade pip
                ./.venv/Scripts/python.exe -m pip install -r requirements.txt
                if (Test-Path package.json) {
                    npm install
                }
                $reportsPath = Join-Path (Get-Location) 'reports'
                if (Test-Path -LiteralPath $reportsPath) {
                    Remove-Item -LiteralPath $reportsPath -Recurse -Force
                }
                New-Item -ItemType Directory -Force -Path $reportsPath | Out-Null
                ''')
                script {
                    initializePipelineStageStatus()
                }
            }
        }

        stage('Framework Unit Tests') {
            when { expression { return params.RUN_FRAMEWORK_TESTS } }
            steps {
                script {
                    updatePipelineStageStatus('framework_tests', 'NO_DATA')
                }
                ciPowerShell('''
                $env:GENERATE_ALLURE_REPORT = 'FALSE'
                $env:GENERATE_HISTORY_REPORT = 'FALSE'
                $parallelArgs = @()
                if (![string]::IsNullOrWhiteSpace($env:TEST_PARALLEL_WORKERS) -and $env:TEST_PARALLEL_WORKERS -ne 'off' -and $env:TEST_PARALLEL_WORKERS -ne 'null') {
                    $parallelArgs = @('-n', $env:TEST_PARALLEL_WORKERS)
                    Write-Host "Parallel test execution enabled: workers=$env:TEST_PARALLEL_WORKERS"
                } else {
                    Write-Host 'Parallel test execution disabled.'
                }
                ./.venv/Scripts/python.exe -m pytest tests @parallelArgs -q --junitxml=reports/unit-tests.xml
                ''')
            }
            post {
                success {
                    script { updatePipelineStageStatus('framework_tests', 'PASSED') }
                }
                failure {
                    script { updatePipelineStageStatus('framework_tests', 'FAILED') }
                }
                unstable {
                    script { updatePipelineStageStatus('framework_tests', 'FAILED') }
                }
                aborted {
                    script { updatePipelineStageStatus('framework_tests', 'FAILED') }
                }
                always {
                    junit allowEmptyResults: false, testResults: 'reports/unit-tests.xml'
                }
            }
        }

        stage('Collect Smoke Cases') {
            when { expression { return params.RUN_COLLECT_ONLY } }
            steps {
                script {
                    updatePipelineStageStatus('smoke_collect', 'NO_DATA')
                }
                ciPowerShell('''
                $collectOutput = ./.venv/Scripts/python.exe run_master.py module/smoke --collect-only -q 2>&1
                $collectExitCode = $LASTEXITCODE
                $collectOutput | ForEach-Object { Write-Host $_ }
                $collectOutput | Set-Content -LiteralPath reports/smoke-collect.txt -Encoding UTF8
                if ($collectExitCode -ne 0) {
                    exit $collectExitCode
                }
                ''')
            }
            post {
                success {
                    script { updatePipelineStageStatus('smoke_collect', 'PASSED') }
                }
                failure {
                    script { updatePipelineStageStatus('smoke_collect', 'FAILED') }
                }
                unstable {
                    script { updatePipelineStageStatus('smoke_collect', 'FAILED') }
                }
                aborted {
                    script { updatePipelineStageStatus('smoke_collect', 'FAILED') }
                }
            }
        }

        stage('Real Smoke') {
            when { expression { return params.RUN_REAL_SMOKE } }
            steps {
                script {
                    updatePipelineStageStatus('real_smoke', 'NO_DATA')
                }
                ciPowerShell('''
                $target = $env:SMOKE_TARGET
                $env:QUALITY_ENABLE = '1'
                $env:QUALITY_SEMANTIC_ENABLE = '1'
                $env:QUALITY_METRICS_ENABLE = '1'
                $env:QUALITY_FLAKY_HISTORY_ENABLE = '0'
                $env:QUALITY_FLAKY_STATE_ENABLE = '0'
                $flakyEnvFiles = @('.env', 'D:/API_CASE/.env')
                foreach ($flakyEnvFile in $flakyEnvFiles) {
                    if ([string]::IsNullOrWhiteSpace($env:QUALITY_FLAKY_DB_PATH) -and (Test-Path -LiteralPath $flakyEnvFile)) {
                        $flakyDbSetting = Get-Content -LiteralPath $flakyEnvFile |
                            Where-Object { $_ -match '^\\s*QUALITY_FLAKY_DB_PATH\\s*=' } |
                            Select-Object -Last 1
                        if ($null -ne $flakyDbSetting) {
                            $configuredFlakyDbPath = $flakyDbSetting.Substring($flakyDbSetting.IndexOf('=') + 1).Trim()
                            if (![string]::IsNullOrWhiteSpace($configuredFlakyDbPath)) {
                                $env:QUALITY_FLAKY_DB_PATH = $configuredFlakyDbPath
                            }
                        }
                    }
                }
                if (![string]::IsNullOrWhiteSpace($env:QUALITY_FLAKY_DB_PATH)) {
                    $env:QUALITY_FLAKY_HISTORY_ENABLE = '1'
                    $env:QUALITY_FLAKY_STATE_ENABLE = '1'
                    Write-Host 'Flaky history and state evaluation enabled with the externally configured job database path.'
                } else {
                    Write-Host 'Flaky history and state evaluation disabled because QUALITY_FLAKY_DB_PATH is not configured.'
                }
                $env:QUALITY_OUTPUT_DIR = 'reports/quality'
                $parallelArgs = @()
                if (![string]::IsNullOrWhiteSpace($env:TEST_PARALLEL_WORKERS) -and $env:TEST_PARALLEL_WORKERS -ne 'off' -and $env:TEST_PARALLEL_WORKERS -ne 'null') {
                    $parallelArgs = @('-n', $env:TEST_PARALLEL_WORKERS)
                    Write-Host "Parallel-first smoke execution enabled: workers=$env:TEST_PARALLEL_WORKERS"
                } else {
                    Write-Host 'Parallel-first smoke execution disabled.'
                }
                ./.venv/Scripts/python.exe run_master.py $target @parallelArgs --junitxml=reports/smoke-tests.xml
                ''')
            }
            post {
                success {
                    script { updatePipelineStageStatus('real_smoke', 'PASSED') }
                }
                failure {
                    script { updatePipelineStageStatus('real_smoke', 'FAILED') }
                }
                unstable {
                    script { updatePipelineStageStatus('real_smoke', 'FAILED') }
                }
                aborted {
                    script { updatePipelineStageStatus('real_smoke', 'FAILED') }
                }
                always {
                    junit allowEmptyResults: true, testResults: 'reports/smoke-tests*.xml'
                }
            }
        }
    }

    post {
        always {
            script {
                generatePipelineSummary()
            }
            allure includeProperties: false, jdk: '', results: [[path: 'allure-results']]
            archiveArtifacts artifacts: 'allure-results/**, reports/**', allowEmptyArchive: true
        }
        failure {
            script {
                notifyByEmail('FAILED')
            }
        }
        unstable {
            script {
                notifyByEmail('UNSTABLE')
            }
        }
        success {
            script {
                def previousResult = currentBuild.previousBuild?.result
                if (params.ALWAYS_SEND_REPORT_EMAIL) {
                    notifyByEmail('SUCCESS')
                } else if (previousResult in ['FAILURE', 'UNSTABLE']) {
                    notifyByEmail('FIXED')
                }
            }
        }
    }
}

void ciPowerShell(String body) {
    powershell(script: """
    chcp 65001 | Out-Null
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    \$OutputEncoding = [System.Text.Encoding]::UTF8
    \$env:PYTHONIOENCODING = 'utf-8'
    \$env:PYTHONUTF8 = '1'
    \$env:USE_CHINA_ENVIRONMENT = '${params.USE_CHINA_ENVIRONMENT}'
    \$env:TEST_PARALLEL_WORKERS = '${params.TEST_PARALLEL_WORKERS ?: 'off'}'
    \$env:GENERATE_PIPELINE_SUMMARY = '${params.GENERATE_PIPELINE_SUMMARY}'
    ${body}
    """)
}

boolean pipelineSummaryEnabled() {
    if (params.GENERATE_PIPELINE_SUMMARY == null) {
        return true
    }
    return String.valueOf(params.GENERATE_PIPELINE_SUMMARY).equalsIgnoreCase('true')
}

void initializePipelineStageStatus() {
    if (!pipelineSummaryEnabled()) {
        echo 'Pipeline summary generation is disabled; stage status initialization skipped.'
        return
    }
    try {
        ciPowerShell("""
        ./.venv/Scripts/python.exe -m pipeline_reporting initialize-stages `
          --framework-tests ${params.RUN_FRAMEWORK_TESTS} `
          --smoke-collect ${params.RUN_COLLECT_ONLY} `
          --real-smoke ${params.RUN_REAL_SMOKE}
        """)
    } catch (error) {
        echo "Pipeline stage status initialization failed open: ${error.getMessage()}"
    }
}

void updatePipelineStageStatus(String stageName, String status) {
    if (!pipelineSummaryEnabled()) {
        return
    }
    try {
        ciPowerShell("""
        ./.venv/Scripts/python.exe -m pipeline_reporting set-stage `
          --name '${stageName}' `
          --status '${status}'
        """)
    } catch (error) {
        echo "Pipeline stage status update failed open: ${stageName}=${status}: ${error.getMessage()}"
    }
}

void generatePipelineSummary() {
    if (!pipelineSummaryEnabled()) {
        echo 'Pipeline summary generation is disabled by GENERATE_PIPELINE_SUMMARY.'
        return
    }
    try {
        if (!fileExists('.venv/Scripts/python.exe') || !fileExists('pipeline_reporting/__main__.py')) {
            throw new IllegalStateException('Python report generator is unavailable in the workspace.')
        }
        withEnv([
            "PIPELINE_BUILD_RESULT=${currentBuild.currentResult ?: currentBuild.result ?: 'SUCCESS'}",
            "PIPELINE_DURATION_MS=${currentBuild.duration ?: 0}",
        ]) {
            ciPowerShell('''
            ./.venv/Scripts/python.exe -m pipeline_reporting generate `
              --workspace . `
              --output reports/pipeline-summary.md `
              --machine-output reports/pipeline-summary.json `
              --email-subject-output reports/pipeline-email-subject.txt `
              --email-html-output reports/pipeline-email.html `
              --dotenv .env
            ''')
        }
        if (!fileExists('reports/pipeline-summary.md')) {
            throw new IllegalStateException('Pipeline summary generator completed without output.')
        }
    } catch (error) {
        echo "Pipeline summary generation failed open: ${error.getMessage()}"
        writeFallbackPipelineSummary()
    }
}

void writeFallbackPipelineSummary() {
    if (!pipelineSummaryEnabled()) {
        return
    }
    try {
        powershell(script: "New-Item -ItemType Directory -Force -Path reports | Out-Null")
        def result = currentBuild.currentResult ?: currentBuild.result ?: 'UNKNOWN'
        writeFile(
            file: 'reports/pipeline-summary.md',
            encoding: 'UTF-8',
            text: """# Jenkins 流水线执行摘要

## 本次结论

- 构建：`${env.JOB_NAME ?: '-'} #${env.BUILD_NUMBER ?: '-'}`
- Jenkins 结果：`${result}`
- 报告状态：详细摘要生成失败
- 建议：查看本轮 Jenkins 阶段日志，定位环境准备或报告生成问题。

本兜底报告不包含推测的用例数量，也不会改变原始构建结果。
"""
        )
    } catch (fallbackError) {
        echo "Fallback pipeline summary generation also failed: ${fallbackError.getMessage()}"
    }
}

String buildFallbackEmailHtml(String status) {
    def statusColor = status == 'FAILED' ? '#b00020' : status == 'UNSTABLE' ? '#b26a00' : '#137333'
    def statusText = buildStatusText(status)
    def buildUrl = env.BUILD_URL ?: ''
    def branchName = normalizeBranchName(env.BRANCH_NAME ?: env.GIT_BRANCH)
    def gitCommit = shortGitCommit(env.GIT_COMMIT)

    return """
    <div style="max-width: 720px; font-family: 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif; color: #222; line-height: 1.5;">
      <h2 style="margin: 0; color: ${statusColor};">${htmlEscape(statusText)}</h2>
      <p style="margin: 4px 0 0; color: #555;">${htmlEscape(env.JOB_NAME)} #${htmlEscape(env.BUILD_NUMBER)}</p>
      <p style="margin: 2px 0 16px; color: #777;">${htmlEscape(branchName)} · ${htmlEscape(gitCommit)} · ${htmlEscape(formatDurationChinese(currentBuild.duration))}</p>
      <p>统一执行摘要或邮件渲染产物不可用，请以 Jenkins 阶段状态和构建日志为准。</p>
      <p><a href="${htmlEscape(buildUrl)}">打开 Jenkins 构建</a></p>
    </div>
    """
}

String buildExecutionSummary() {
    def parts = []
    if (String.valueOf(params.RUN_FRAMEWORK_TESTS).equalsIgnoreCase('true')) {
        parts << '框架测试'
    }
    if (String.valueOf(params.RUN_COLLECT_ONLY).equalsIgnoreCase('true')) {
        parts << '用例收集'
    }
    if (String.valueOf(params.RUN_REAL_SMOKE).equalsIgnoreCase('true')) {
        parts << "接口测试（${params.SMOKE_TARGET}）"
        parts << "并发 ${workerText(params.TEST_PARALLEL_WORKERS)}"
    }
    if (parts.isEmpty()) {
        parts << '未选择测试任务'
    }
    parts << environmentText(params.USE_CHINA_ENVIRONMENT)
    return parts.join(' · ')
}

String normalizeBranchName(def value) {
    def branchName = String.valueOf(value ?: '').trim()
    if (!branchName) {
        return '-'
    }
    return branchName.replaceFirst('^refs/remotes/', '').replaceFirst('^origin/', '')
}

String shortGitCommit(def value) {
    def commit = String.valueOf(value ?: '').trim()
    if (!commit) {
        return '-'
    }
    return commit.length() > 12 ? commit.substring(0, 12) : commit
}

String buildStatusText(String status) {
    def labels = [
        FAILED: '构建失败',
        UNSTABLE: '构建不稳定',
        SUCCESS: '构建成功',
        FIXED: '构建已恢复',
        ABORTED: '构建已中止',
        NOT_BUILT: '未执行构建',
    ]
    return labels[status] ?: '未知构建状态'
}

String environmentText(def value) {
    return String.valueOf(value).equalsIgnoreCase('true') ? '中国环境' : '默认环境'
}

String workerText(def value) {
    def workerValue = String.valueOf(value ?: 'off')
    if (workerValue == 'off') {
        return '关闭'
    }
    if (workerValue == 'auto') {
        return '自动'
    }
    return "${workerValue} 个"
}

String formatDurationChinese(def durationMillis) {
    long totalSeconds = ((durationMillis ?: 0L) as long).intdiv(1000L)
    long hours = totalSeconds.intdiv(3600)
    long minutes = totalSeconds.mod(3600).intdiv(60)
    long seconds = totalSeconds.mod(60)
    def parts = []
    if (hours > 0) {
        parts << "${hours} 小时"
    }
    if (minutes > 0) {
        parts << "${minutes} 分钟"
    }
    if (seconds > 0 || parts.isEmpty()) {
        parts << "${seconds} 秒"
    }
    return parts.join(' ')
}

void notifyByEmail(String status) {
    if (!env.CI_MAIL_TO?.trim()) {
        echo 'CI 邮件收件人为空，跳过邮件通知。'
        return
    }

    try {
        def statusText = buildStatusText(status)
        def subject = "【${statusText}】${env.JOB_NAME} #${env.BUILD_NUMBER}｜测试报告未生成"
        def body = buildFallbackEmailHtml(status)
        if (fileExists('reports/pipeline-email-subject.txt') && fileExists('reports/pipeline-email.html')) {
            subject = readFile('reports/pipeline-email-subject.txt').trim()
            body = readFile('reports/pipeline-email.html')
        }
        subject = subject.replaceFirst(/^【[^】]+】/, "【${statusText}】")
        emailext(
            subject: subject,
            mimeType: 'text/html',
            to: env.CI_MAIL_TO,
            from: env.CI_MAIL_FROM,
            body: body
        )
    } catch (error) {
        echo "CI 邮件通知发送失败：${error.getMessage()}"
    }
}

@NonCPS
String htmlEscape(Object value) {
    if (value == null) {
        return ''
    }

    return value.toString()
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#39;')
}
