pipeline {
    agent { label 'Windows' }

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    triggers {
        parameterizedCron('''
            0 0 * * * % RUN_FRAMEWORK_TESTS=true;RUN_COLLECT_ONLY=false;RUN_REAL_SMOKE=true;ALWAYS_SEND_REPORT_EMAIL=true;USE_CHINA_ENVIRONMENT=TRUE;SMOKE_TARGET=module/smoke;TEST_PARALLEL_WORKERS=off
        ''')
    }

    parameters {
        booleanParam(name: 'RUN_FRAMEWORK_TESTS', defaultValue: true, description: 'Run framework tests under tests directory.')
        booleanParam(name: 'RUN_COLLECT_ONLY', defaultValue: true, description: 'Collect smoke cases without real execution.')
        booleanParam(name: 'RUN_REAL_SMOKE', defaultValue: false, description: 'Run real smoke cases. Keep disabled by default.')
        booleanParam(name: 'ALWAYS_SEND_REPORT_EMAIL', defaultValue: false, description: 'Send report email for every build result.')
        choice(name: 'USE_CHINA_ENVIRONMENT', choices: ['TRUE', 'FALSE'], description: 'TRUE uses China environment, FALSE uses default environment.')
        string(name: 'SMOKE_TARGET', defaultValue: 'module/smoke', description: 'Smoke test target path.', trim: true)
        choice(name: 'TEST_PARALLEL_WORKERS', choices: ['off', 'auto', '2', '4', '8'], description: 'off disables pytest-xdist; auto/2/4/8 enables parallel test execution.')
    }

    environment {
        CI_MAIL_TO = '3239682586@qq.com'
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
                checkout scm
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
            }
        }

        stage('Framework Unit Tests') {
            when { expression { return params.RUN_FRAMEWORK_TESTS } }
            steps {
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
                always {
                    junit allowEmptyResults: false, testResults: 'reports/unit-tests.xml'
                }
            }
        }

        stage('Collect Smoke Cases') {
            when { expression { return params.RUN_COLLECT_ONLY } }
            steps {
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
        }

        stage('Real Smoke') {
            when { expression { return params.RUN_REAL_SMOKE } }
            steps {
                ciPowerShell('''
                $target = $env:SMOKE_TARGET
                $env:QUALITY_ENABLE = '1'
                $env:QUALITY_SEMANTIC_ENABLE = '1'
                $env:QUALITY_METRICS_ENABLE = '1'
                $env:QUALITY_P1_REPORT_ENABLE = '1'
                $env:QUALITY_FLAKY_HISTORY_ENABLE = '0'
                $env:QUALITY_FLAKY_STATE_ENABLE = '0'
                $flakyEnvFiles = @('.env', 'D:/API_CASE/.env')
                foreach ($flakyEnvFile in $flakyEnvFiles) {
                    if ([string]::IsNullOrWhiteSpace($env:QUALITY_FLAKY_DB_PATH) -and (Test-Path -LiteralPath $flakyEnvFile)) {
                        $flakyDbSetting = Get-Content -LiteralPath $flakyEnvFile |
                            Where-Object { $_ -match '^\s*QUALITY_FLAKY_DB_PATH\s*=' } |
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
                $env:QUALITY_SHADOW_GATE = '1'
                $env:QUALITY_MIN_REQUEST_SAMPLES = '20'
                $env:QUALITY_HTTP_5XX_WARN_RATE = '0.02'
                $env:QUALITY_TIMEOUT_WARN_RATE = '0.05'
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
                always {
                    junit allowEmptyResults: true, testResults: 'reports/smoke-tests.xml'
                }
            }
        }
    }

    post {
        always {
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
    ${body}
    """)
}

Map readJunitSummary() {
    def summary = [tests: 0, failures: 0, errors: 0, skipped: 0]
    def reportFiles = [
        'reports/unit-tests.xml',
        'reports/smoke-tests.xml',
        'reports/smoke-tests-parallel.xml',
        'reports/smoke-tests-serial.xml',
    ]

    reportFiles.each { reportFile ->
        if (fileExists(reportFile)) {
            def parsed = parseJunitXmlText(readFile(reportFile))
            summary.tests += parsed.tests
            summary.failures += parsed.failures
            summary.errors += parsed.errors
            summary.skipped += parsed.skipped
        }
    }

    return summary
}

Map readSmokeCollectSummary() {
    if (!fileExists('reports/smoke-collect.txt')) {
        return [total: '-', parallel: '-', serial: '-']
    }

    return parseSmokeCollectText(readFile('reports/smoke-collect.txt'))
}

Map readQualitySummary() {
    def unavailable = [
        available: false,
        overall: '质量摘要未生成',
        integrity: '-',
        caseTotal: '-',
        caseFailed: '-',
        caseError: '-',
        caseSkipped: '-',
        productDefect: '-',
        configuration: '-',
        frameworkDefect: '-',
        unknown: '-',
        requestTotal: '-',
        http5xx: '-',
        timeout: '-',
    ]
    try {
        def summaryPath = 'reports/quality/summary.json'
        def gatePath = 'reports/quality/gate-report.json'
        if (!fileExists(summaryPath) || !fileExists(gatePath)) {
            return unavailable
        }
        def summaryPayload = parseJsonObject(readFile(summaryPath))
        def gatePayload = parseJsonObject(readFile(gatePath))
        def summary = summaryPayload.summary instanceof Map ? summaryPayload.summary : [:]
        def categories = summaryPayload.failure_categories instanceof Map ? summaryPayload.failure_categories : [:]
        if (!summary || !gatePayload.overall) {
            return unavailable
        }
        return [
            available: true,
            overall: gatePayload.overall,
            integrity: summary.integrity_status ?: '-',
            caseTotal: summary.case_total ?: 0,
            caseFailed: summary.case_failed ?: 0,
            caseError: summary.case_error ?: 0,
            caseSkipped: summary.case_skipped ?: 0,
            productDefect: categories.PRODUCT_DEFECT ?: 0,
            configuration: categories.CONFIGURATION ?: 0,
            frameworkDefect: categories.FRAMEWORK_DEFECT ?: 0,
            unknown: categories.UNKNOWN ?: 0,
            requestTotal: summary.request_total ?: 0,
            http5xx: summary.http_5xx_count ?: 0,
            timeout: summary.timeout_count ?: 0,
        ]
    } catch (error) {
        echo "质量摘要读取失败：${error.getMessage()}"
        return unavailable
    }
}

Map readP1ObservationSummary() {
    def unavailable = [
        available: false,
        reportStatus: 'P1 观察报告未生成',
        operationCount: '-',
        workloadOperationCount: '-',
        operationSuccess: '-',
        operationFailed: '-',
        operationTimeout: '-',
        usageComplete: '-',
        usagePartial: '-',
        usageMissing: '-',
        newlySuspected: '-',
        newlyConfirmed: '-',
        quarantined: '-',
        recovering: '-',
        recovered: '-',
        overdue: '-',
        requiredSourceFailures: '-',
    ]
    try {
        def manifestPath = 'reports/quality/p1-observation-manifest.json'
        def reportPath = 'reports/quality/p1-observation.json'
        if (!fileExists(manifestPath) || !fileExists(reportPath)) {
            return unavailable
        }
        def manifest = parseJsonObject(readFile(manifestPath))
        if (manifest.write_status != 'complete' || !manifest.output_hashes?.json) {
            return unavailable
        }
        def payload = parseJsonObject(readFile(reportPath))
        def overview = payload.overview instanceof Map ? payload.overview : [:]
        if (!overview || payload.run_id != manifest.run_id) {
            return unavailable
        }
        return [
            available: true,
            reportStatus: payload.report_status ?: '-',
            operationCount: overview.operation_count ?: 0,
            workloadOperationCount: overview.workload_operation_count ?: 0,
            operationSuccess: overview.operation_success_count ?: 0,
            operationFailed: overview.operation_failed_count ?: 0,
            operationTimeout: overview.operation_timeout_count ?: 0,
            usageComplete: overview.usage_complete_count ?: 0,
            usagePartial: overview.usage_partial_count ?: 0,
            usageMissing: overview.usage_missing_count ?: 0,
            newlySuspected: overview.newly_suspected_count ?: 0,
            newlyConfirmed: overview.newly_confirmed_count ?: 0,
            quarantined: overview.quarantined_count ?: 0,
            recovering: overview.recovering_count ?: 0,
            recovered: overview.recovered_count ?: 0,
            overdue: overview.overdue_count ?: 0,
            requiredSourceFailures: overview.required_source_failure_count ?: 0,
        ]
    } catch (error) {
        echo "P1 观察报告读取失败：${error.getMessage()}"
        return unavailable
    }
}

String buildResultSummaryHtml(String status) {
    def junit = readJunitSummary()
    def smoke = readSmokeCollectSummary()
    def quality = readQualitySummary()
    def p1 = readP1ObservationSummary()
    def statusColor = status == 'FAILED' ? '#b00020' : status == 'UNSTABLE' ? '#b26a00' : '#137333'
    def statusText = buildStatusText(status)
    def buildUrl = env.BUILD_URL ?: ''
    def branchName = env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'dev2'
    def gitCommit = env.GIT_COMMIT ?: '-'

    return """
    <div style="font-family: 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif; color: #222; line-height: 1.4;">
      <h2 style="margin: 0 0 12px;">${htmlEscape(env.JOB_NAME)} #${htmlEscape(env.BUILD_NUMBER)} 构建报告：${htmlEscape(statusText)}</h2>
      <table style="border-collapse: collapse; min-width: 680px;">
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">构建状态</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd; color: ${statusColor};"><b>${htmlEscape(statusText)}</b></td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">任务名称</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(env.JOB_NAME)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">构建编号</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">#${htmlEscape(env.BUILD_NUMBER)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">构建耗时</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(formatDurationChinese(currentBuild.duration))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">分支</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(branchName)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">提交版本</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(gitCommit)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">JUnit 测试结果</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            共 ${junit.tests} 项，失败 ${junit.failures} 项，错误 ${junit.errors} 项，跳过 ${junit.skipped} 项
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">冒烟用例收集</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            共 ${htmlEscape(smoke.total)} 项，可并发 ${htmlEscape(smoke.parallel)} 项，需串行 ${htmlEscape(smoke.serial)} 项
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">质量影子门禁</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            ${htmlEscape(quality.overall)}，完整性 ${htmlEscape(quality.integrity)}
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">质量用例摘要</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            共 ${htmlEscape(quality.caseTotal)} 项，失败 ${htmlEscape(quality.caseFailed)} 项，错误 ${htmlEscape(quality.caseError)} 项，跳过 ${htmlEscape(quality.caseSkipped)} 项
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">质量失败分类</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            产品缺陷 ${htmlEscape(quality.productDefect)}，配置问题 ${htmlEscape(quality.configuration)}，框架缺陷 ${htmlEscape(quality.frameworkDefect)}，未知 ${htmlEscape(quality.unknown)}
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">质量请求摘要</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            共 ${htmlEscape(quality.requestTotal)} 次，HTTP 5xx ${htmlEscape(quality.http5xx)} 次，超时 ${htmlEscape(quality.timeout)} 次
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">P1 单次观察</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            状态 ${htmlEscape(p1.reportStatus)}，逻辑调用 ${htmlEscape(p1.operationCount)}，workload ${htmlEscape(p1.workloadOperationCount)}，成功/失败/超时 ${htmlEscape(p1.operationSuccess)}/${htmlEscape(p1.operationFailed)}/${htmlEscape(p1.operationTimeout)}
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">P1 用量与 Flaky</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            usage complete/partial/missing ${htmlEscape(p1.usageComplete)}/${htmlEscape(p1.usagePartial)}/${htmlEscape(p1.usageMissing)}；suspected ${htmlEscape(p1.newlySuspected)}，confirmed ${htmlEscape(p1.newlyConfirmed)}，quarantined ${htmlEscape(p1.quarantined)}，recovering ${htmlEscape(p1.recovering)}，recovered ${htmlEscape(p1.recovered)}，overdue ${htmlEscape(p1.overdue)}；必需数据源故障 ${htmlEscape(p1.requiredSourceFailures)}
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">运行框架单元测试</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(booleanText(params.RUN_FRAMEWORK_TESTS))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">仅收集冒烟用例</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(booleanText(params.RUN_COLLECT_ONLY))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">运行真实冒烟测试</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(booleanText(params.RUN_REAL_SMOKE))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">运行环境</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(environmentText(params.USE_CHINA_ENVIRONMENT))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">冒烟测试目标</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.SMOKE_TARGET)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">并发进程数</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(workerText(params.TEST_PARALLEL_WORKERS))}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">每次构建均发送邮件</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(booleanText(params.ALWAYS_SEND_REPORT_EMAIL))}</td>
        </tr>
      </table>

      <p style="margin-top: 14px;">
        <a href="${htmlEscape(buildUrl)}">构建详情</a> |
        <a href="${htmlEscape(buildUrl)}console">控制台日志</a> |
        <a href="${htmlEscape(buildUrl)}allure/">Allure 报告</a> |
        <a href="${htmlEscape(buildUrl)}testReport/">JUnit 报告</a> |
        <a href="${htmlEscape(buildUrl)}artifact/reports/quality/gate-report.md">质量影子门禁报告</a> |
        <a href="${htmlEscape(buildUrl)}artifact/reports/quality/p1-observation.md">P1 单次观察与 Flaky 报告</a>
      </p>
    </div>
    """
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

String booleanText(def value) {
    return String.valueOf(value).equalsIgnoreCase('true') ? '是' : '否'
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
        emailext(
            subject: "【${statusText}】${env.JOB_NAME} #${env.BUILD_NUMBER} 构建报告",
            mimeType: 'text/html',
            to: env.CI_MAIL_TO,
            from: env.CI_MAIL_FROM,
            body: buildResultSummaryHtml(status)
        )
    } catch (error) {
        echo "CI 邮件通知发送失败：${error.getMessage()}"
    }
}

@NonCPS
Map parseJunitXmlText(String xmlText) {
    def summary = [tests: 0, failures: 0, errors: 0, skipped: 0]
    def suiteMatcher = xmlText =~ /<testsuite\b[^>]*>/

    suiteMatcher.each { suiteTag ->
        summary.tests += extractIntAttribute(suiteTag, 'tests')
        summary.failures += extractIntAttribute(suiteTag, 'failures')
        summary.errors += extractIntAttribute(suiteTag, 'errors')
        summary.skipped += extractIntAttribute(suiteTag, 'skipped')
    }

    return summary
}

@NonCPS
Map parseSmokeCollectText(String text) {
    return [
        total: extractFirstGroup(text, /Collected test cases:\s*(\d+)/, '-'),
        parallel: extractFirstGroup(text, /Parallel pool cases:\s*(\d+)/, '-'),
        serial: extractFirstGroup(text, /Serial pool cases:\s*(\d+)/, '-'),
    ]
}

@NonCPS
Map parseJsonObject(String text) {
    def parsed = new groovy.json.JsonSlurperClassic().parseText(text)
    return parsed instanceof Map ? parsed : [:]
}

@NonCPS
int extractIntAttribute(String text, String attributeName) {
    def matcher = text =~ /(?:^|\s)${attributeName}="(\d+)"/
    if (!matcher.find()) {
        return 0
    }
    return matcher.group(1) as int
}

@NonCPS
String extractFirstGroup(String text, String pattern, String defaultValue) {
    def matcher = text =~ pattern
    if (!matcher.find()) {
        return defaultValue
    }
    return matcher.group(1)
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
