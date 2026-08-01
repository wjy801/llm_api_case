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
                    junit allowEmptyResults: true, testResults: 'reports/smoke-tests*.xml'
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
    def summary = [
        available: false,
        reportCount: 0,
        tests: 0,
        passed: 0,
        failures: 0,
        errors: 0,
        skipped: 0,
        failedTests: [],
    ]
    def reportFiles = [
        'reports/unit-tests.xml',
        'reports/smoke-tests.xml',
        'reports/smoke-tests-parallel.xml',
        'reports/smoke-tests-serial.xml',
    ]

    reportFiles.each { reportFile ->
        if (fileExists(reportFile)) {
            def parsed = parseJunitXmlText(readFile(reportFile))
            summary.available = true
            summary.reportCount += 1
            summary.tests += parsed.tests
            summary.failures += parsed.failures
            summary.errors += parsed.errors
            summary.skipped += parsed.skipped
            parsed.failedTests.each { failedTest ->
                if (summary.failedTests.size() < 5 && !summary.failedTests.contains(failedTest)) {
                    summary.failedTests << failedTest
                }
            }
        }
    }

    summary.passed = summary.tests - summary.failures - summary.errors - summary.skipped
    if (summary.passed < 0) {
        summary.passed = 0
    }
    return summary
}

Map readSmokeCollectSummary() {
    if (!fileExists('reports/smoke-collect.txt')) {
        return [available: false, total: '-', parallel: '-', serial: '-']
    }

    def summary = parseSmokeCollectText(readFile('reports/smoke-collect.txt'))
    summary.available = true
    return summary
}

String buildResultSummaryHtml(String status, Map junit, Map smoke) {
    def statusColor = status == 'FAILED' ? '#b00020' : status == 'UNSTABLE' ? '#b26a00' : '#137333'
    def statusText = buildStatusText(status)
    def buildUrl = env.BUILD_URL ?: ''
    def branchName = normalizeBranchName(env.BRANCH_NAME ?: env.GIT_BRANCH)
    def gitCommit = shortGitCommit(env.GIT_COMMIT)
    def failedCount = junit.failures + junit.errors
    def junitText = junit.available
        ? "${junit.tests} 总计 / ${junit.passed} 通过 / ${failedCount} 失败 / ${junit.skipped} 跳过"
        : '测试报告未生成，构建可能在测试阶段前失败，请查看控制台日志。'
    def smokeText = smoke.available
        ? "${smoke.total} 项（并发 ${smoke.parallel} / 串行 ${smoke.serial}）"
        : '未生成收集清单'
    def failedTestsHtml = ''
    if (junit.available && failedCount > 0) {
        def failedItems = junit.failedTests.collect { failedTest ->
            "<li style=\"margin: 4px 0;\">${htmlEscape(failedTest)}</li>"
        }.join('')
        if (!failedItems) {
            failedItems = '<li style="margin: 4px 0;">未能从 JUnit 报告提取失败用例名称</li>'
        }
        failedTestsHtml = """
        <div style="margin-top: 16px; padding: 12px 16px; background: #fff5f5; border-left: 4px solid #b00020;">
          <div style="font-weight: 600; color: #b00020;">失败用例（最多 5 项）</div>
          <ul style="margin: 8px 0 0; padding-left: 20px;">${failedItems}</ul>
        </div>
        """
    }
    def reportLinks = [
        "<a href=\"${htmlEscape(buildUrl)}\">构建详情</a>",
        "<a href=\"${htmlEscape(buildUrl)}console\">控制台日志</a>",
        "<a href=\"${htmlEscape(buildUrl)}allure/\">Allure 报告</a>",
    ]
    if (junit.available) {
        reportLinks << "<a href=\"${htmlEscape(buildUrl)}testReport/\">JUnit 报告</a>"
    }
    reportLinks << "<a href=\"${htmlEscape(buildUrl)}artifact/\">构建产物</a>"

    return """
    <div style="max-width: 720px; font-family: 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif; color: #222; line-height: 1.5;">
      <h2 style="margin: 0; color: ${statusColor};">${htmlEscape(statusText)}</h2>
      <p style="margin: 4px 0 0; color: #555;">${htmlEscape(env.JOB_NAME)} #${htmlEscape(env.BUILD_NUMBER)}</p>
      <p style="margin: 2px 0 16px; color: #777;">${htmlEscape(branchName)} · ${htmlEscape(gitCommit)} · ${htmlEscape(formatDurationChinese(currentBuild.duration))}</p>
      <table style="width: 100%; border-collapse: collapse;">
        <tr>
          <td style="width: 72px; padding: 9px 10px; border: 1px solid #ddd; color: #666;">测试</td>
          <td style="padding: 9px 10px; border: 1px solid #ddd;">${htmlEscape(junitText)}</td>
        </tr>
        <tr>
          <td style="padding: 9px 10px; border: 1px solid #ddd; color: #666;">Smoke</td>
          <td style="padding: 9px 10px; border: 1px solid #ddd;">${htmlEscape(smokeText)}</td>
        </tr>
        <tr>
          <td style="padding: 9px 10px; border: 1px solid #ddd; color: #666;">执行</td>
          <td style="padding: 9px 10px; border: 1px solid #ddd;">${htmlEscape(buildExecutionSummary())}</td>
        </tr>
      </table>
      ${failedTestsHtml}
      <p style="margin-top: 16px;">
        ${reportLinks.join(' | ')}
      </p>
      <p style="margin-top: 8px; color: #888; font-size: 12px;">详细质量数据请在构建产物中查看。</p>
    </div>
    """
}

String buildExecutionSummary() {
    def parts = []
    if (String.valueOf(params.RUN_FRAMEWORK_TESTS).equalsIgnoreCase('true')) {
        parts << '框架测试'
    }
    if (String.valueOf(params.RUN_COLLECT_ONLY).equalsIgnoreCase('true')) {
        parts << 'Smoke 收集'
    }
    if (String.valueOf(params.RUN_REAL_SMOKE).equalsIgnoreCase('true')) {
        parts << "真实 Smoke（${params.SMOKE_TARGET}）"
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
        def junit = readJunitSummary()
        def smoke = readSmokeCollectSummary()
        def resultText = junit.available
            ? "${junit.failures + junit.errors} 失败 / ${junit.tests} 项"
            : '测试报告未生成'
        emailext(
            subject: "【${statusText}】${env.JOB_NAME} #${env.BUILD_NUMBER}｜${resultText}",
            mimeType: 'text/html',
            to: env.CI_MAIL_TO,
            from: env.CI_MAIL_FROM,
            body: buildResultSummaryHtml(status, junit, smoke)
        )
    } catch (error) {
        echo "CI 邮件通知发送失败：${error.getMessage()}"
    }
}

@NonCPS
Map parseJunitXmlText(String xmlText) {
    def summary = [tests: 0, failures: 0, errors: 0, skipped: 0, failedTests: []]
    def suiteMatcher = xmlText =~ /<testsuite\b[^>]*>/

    suiteMatcher.each { suiteTag ->
        summary.tests += extractIntAttribute(suiteTag, 'tests')
        summary.failures += extractIntAttribute(suiteTag, 'failures')
        summary.errors += extractIntAttribute(suiteTag, 'errors')
        summary.skipped += extractIntAttribute(suiteTag, 'skipped')
    }

    def testCaseMatcher = xmlText =~ /(?s)<testcase\b([^>]*)>(.*?)<\/testcase>/
    while (testCaseMatcher.find() && summary.failedTests.size() < 5) {
        def body = testCaseMatcher.group(2)
        if (!(body =~ /<(?:failure|error)\b/).find()) {
            continue
        }
        def attributes = testCaseMatcher.group(1)
        def className = extractStringAttribute(attributes, 'classname')
        def testName = extractStringAttribute(attributes, 'name')
        def displayName = className && testName ? "${className}::${testName}" : testName ?: className ?: '未知失败用例'
        summary.failedTests << displayName
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
int extractIntAttribute(String text, String attributeName) {
    def matcher = text =~ /(?:^|\s)${attributeName}="(\d+)"/
    if (!matcher.find()) {
        return 0
    }
    return matcher.group(1) as int
}

@NonCPS
String extractStringAttribute(String text, String attributeName) {
    def matcher = text =~ /(?:^|\s)${attributeName}="([^"]*)"/
    if (!matcher.find()) {
        return ''
    }
    return matcher.group(1)
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
