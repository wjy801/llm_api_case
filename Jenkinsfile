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

String buildResultSummaryHtml(String status) {
    def junit = readJunitSummary()
    def smoke = readSmokeCollectSummary()
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
        <a href="${htmlEscape(buildUrl)}testReport/">JUnit 报告</a>
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
