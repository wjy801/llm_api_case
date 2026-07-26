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
        GENERATE_ALLURE_REPORT = 'FALSE'
        GENERATE_HISTORY_REPORT = 'FALSE'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
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
                $sourceEnv = 'D:/Code/Form/llm_api_case/.env'
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
                New-Item -ItemType Directory -Force reports | Out-Null
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
                ./.venv/Scripts/python.exe run_master.py module/smoke --collect-only -q
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
                    Write-Host "Parallel test execution enabled: workers=$env:TEST_PARALLEL_WORKERS"
                } else {
                    Write-Host 'Parallel test execution disabled.'
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
