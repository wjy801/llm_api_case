pipeline {
    agent { label 'Windows' }

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(name: 'RUN_FRAMEWORK_TESTS', defaultValue: true, description: 'Run framework tests under tests directory.')
        booleanParam(name: 'RUN_COLLECT_ONLY', defaultValue: true, description: 'Collect smoke cases without real execution.')
        booleanParam(name: 'RUN_REAL_SMOKE', defaultValue: false, description: 'Run real smoke cases. Keep disabled by default.')
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
                if (previousResult in ['FAILURE', 'UNSTABLE']) {
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
    def buildUrl = env.BUILD_URL ?: ''
    def branchName = env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'dev2'
    def gitCommit = env.GIT_COMMIT ?: '-'

    return """
    <div style="font-family: Arial, sans-serif; color: #222; line-height: 1.4;">
      <h2 style="margin: 0 0 12px;">${htmlEscape(env.JOB_NAME)} #${htmlEscape(env.BUILD_NUMBER)} - ${htmlEscape(status)}</h2>
      <table style="border-collapse: collapse; min-width: 680px;">
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Status</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd; color: ${statusColor};"><b>${htmlEscape(status)}</b></td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Job</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(env.JOB_NAME)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Build</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">#${htmlEscape(env.BUILD_NUMBER)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Duration</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(currentBuild.durationString)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Branch</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(branchName)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Commit</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(gitCommit)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">JUnit</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            ${junit.tests} total, ${junit.failures} failures, ${junit.errors} errors, ${junit.skipped} skipped
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Smoke Collect</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            ${htmlEscape(smoke.total)} total, ${htmlEscape(smoke.parallel)} parallel, ${htmlEscape(smoke.serial)} serial
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">RUN_FRAMEWORK_TESTS</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.RUN_FRAMEWORK_TESTS)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">RUN_COLLECT_ONLY</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.RUN_COLLECT_ONLY)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">RUN_REAL_SMOKE</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.RUN_REAL_SMOKE)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">USE_CHINA_ENVIRONMENT</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.USE_CHINA_ENVIRONMENT)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">SMOKE_TARGET</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.SMOKE_TARGET)}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">TEST_PARALLEL_WORKERS</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${htmlEscape(params.TEST_PARALLEL_WORKERS)}</td>
        </tr>
      </table>

      <p style="margin-top: 14px;">
        <a href="${htmlEscape(buildUrl)}">Build</a> |
        <a href="${htmlEscape(buildUrl)}console">Console</a> |
        <a href="${htmlEscape(buildUrl)}allure/">Allure</a> |
        <a href="${htmlEscape(buildUrl)}testReport/">JUnit</a>
      </p>
    </div>
    """
}

void notifyByEmail(String status) {
    if (!env.CI_MAIL_TO?.trim()) {
        echo 'CI email recipient is empty. Skip email notification.'
        return
    }

    try {
        emailext(
            subject: "[${status}] ${env.JOB_NAME} #${env.BUILD_NUMBER}",
            mimeType: 'text/html',
            to: env.CI_MAIL_TO,
            from: env.CI_MAIL_FROM,
            body: buildResultSummaryHtml(status)
        )
    } catch (error) {
        echo "CI email notification failed: ${error.getMessage()}"
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
