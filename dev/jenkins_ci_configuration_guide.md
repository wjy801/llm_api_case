# Jenkins CI 零基础配置操作手册

## 1. 你最终要搭出来什么

最终只创建一个 Jenkins Job：

```text
llm-api-case
```

这个 Job 通过参数控制执行范围：

```text
RUN_FRAMEWORK_TESTS=true    # 跑框架单测 tests
RUN_COLLECT_ONLY=true       # 只收集 smoke 用例，不真实调用接口
RUN_REAL_SMOKE=false        # 是否真实执行 smoke
USE_CHINA_ENVIRONMENT=TRUE  # 使用国内或海外环境
SMOKE_TARGET=module/smoke   # 真实 smoke 执行范围
```

第一版默认只做安全门禁：

```text
pytest tests -q
run_master.py module/smoke --collect-only -q
```

真实接口 smoke 默认不跑，手动把 `RUN_REAL_SMOKE=true` 后再跑。

## 2. 零基础先理解 5 个 Jenkins 概念

### 2.1 Jenkins Controller

你打开网页访问的 Jenkins 服务本身，负责显示页面、保存配置、调度任务。

### 2.2 Agent / Node

真正执行命令的机器。你的项目是 Windows 路径和 PowerShell 命令，因此第一版推荐用 Windows 机器作为 agent。

### 2.3 Job

一个可执行任务。这里我们只建一个 Pipeline Job：`llm-api-case`。

### 2.4 Pipeline

Jenkins 中的一组自动化步骤。例如：

```text
拉代码 -> 安装依赖 -> 写 .env -> 跑测试 -> 生成报告
```

### 2.5 Jenkinsfile

Pipeline 的脚本文件。可以先直接粘贴到 Jenkins 页面里，跑通后再提交为仓库根目录的 `Jenkinsfile`。

## 3. 本项目 CI 分层

### 3.1 框架单测

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=reports\unit-tests.xml
```

作用：

```text
验证 common、util、配置校验、请求中间件、重试、轮询、上下文、mock 等框架能力
不真实调用外部模型服务
适合作为每次提交的硬门禁
```

### 3.2 smoke 收集

命令：

```powershell
.\.venv\Scripts\python.exe run_master.py module\smoke --collect-only -q
```

作用：

```text
验证业务用例能被 pytest 收集
验证 import、类名、方法名、pytest nodeid 没问题
不真实调用接口
适合作为每次提交的硬门禁
```

### 3.3 真实 smoke

命令：

```powershell
.\.venv\Scripts\python.exe run_master.py module\smoke --junitxml=reports\smoke-tests.xml
```

作用：

```text
真实请求 API
可能产生调用成本
依赖网络、账号、余额、模型服务状态
建议手动触发或定时触发
```

## 4. Jenkins 机器准备

以下步骤在 Jenkins Agent 机器上做。如果你现在只有一台 Jenkins 机器，也可以先用 Jenkins Controller 作为执行节点。

### 4.1 确认 Windows PowerShell 可用

打开 PowerShell，执行：

```powershell
powershell -version
```

能输出版本即可。

### 4.2 安装 Git

安装完成后打开 PowerShell：

```powershell
git --version
```

能看到类似输出即可：

```text
git version 2.x.x
```

### 4.3 安装 Python

建议 Python 3.11 或当前项目本地已验证版本。

安装完成后验证：

```powershell
python --version
py --version
```

至少一个命令可用即可。

如果 `py` 不可用，后续 Jenkinsfile 中把：

```powershell
py -m venv .venv
```

改成：

```powershell
python -m venv .venv
```

### 4.4 安装 Node.js

用于安装 `allure-commandline`，虽然第一版推荐 Jenkins Allure 插件生成报告，但保留 `npm install` 更贴近本地环境。

验证：

```powershell
node -v
npm -v
```

### 4.5 安装 Java

Allure 报告生成通常需要 Java。

验证：

```powershell
java -version
```

### 4.6 确认 Jenkins 工作目录权限

Jenkins 执行用户必须能在 workspace 里创建：

```text
.venv/
reports/
allure-results/
.env
```

如果没有权限，后续构建会在 `Prepare Env` 或 `Write Runtime Env` 阶段失败。

## 5. Jenkins 插件安装步骤

### 5.1 进入插件管理页面

1. 打开 Jenkins 首页。
2. 点击左侧或顶部的 `Manage Jenkins`。
3. 点击 `Plugins` 或 `Manage Plugins`。
4. 进入 `Available plugins`。

### 5.2 搜索并安装插件

安装或确认已安装：

```text
Pipeline
Git
Credentials Binding
JUnit
Allure Jenkins Plugin
Workspace Cleanup
```

说明：

```text
Pipeline              支持 Jenkinsfile 和流水线
Git                   拉取 Git 仓库
Credentials Binding   在 Pipeline 中安全读取 Jenkins 凭据
JUnit                 展示 pytest 生成的 JUnit XML
Allure Jenkins Plugin 读取 allure-results 并生成 Allure 报告
Workspace Cleanup     可选，用于清理 workspace
```

5. 勾选插件。
6. 点击 `Install without restart`。
7. 等待安装完成。
8. 如果提示需要重启，选择安全重启 Jenkins。

## 6. Allure Commandline 配置

### 6.1 进入 Tools 页面

1. 点击 `Manage Jenkins`。
2. 点击 `Tools` 或 `Global Tool Configuration`。
3. 找到 `Allure Commandline`。

### 6.2 新增 Allure 安装项

1. 点击 `Add Allure Commandline`。
2. Name 填：

   ```text
   allure-commandline
   ```

3. 如果 Jenkins 能联网，勾选自动安装。
4. 如果 Jenkins 不能联网，先在机器上安装 Allure Commandline，再填写安装路径。
5. 保存。

## 7. 凭据和 .env 的处理方式

你当前说明：账号凭据已经写在项目文件中，不需要 Jenkins 额外管控。

因此第一版推荐两种方式，选一种即可。

### 7.1 方式 A：Jenkins 只使用仓库/工作区已有 .env

适用条件：

```text
Jenkins workspace 中已经存在正确的 .env
或构建前会由其它安全方式放置 .env
```

这种方式 Jenkinsfile 不需要 `withCredentials`，也不需要写 `.env`。

优点：

```text
最贴合当前项目实际
账号、账单、zero 账号等配置完全沿用已有文件
Jenkins 配置更少
```

风险：

```text
要确保 .env 不被提交到 Git
要确保 Jenkins workspace 的 .env 来源可靠
多人维护时要知道 .env 是谁放进去的
```

### 7.2 方式 B：Jenkins 只生成基础环境 .env

适用条件：

```text
你希望 Jenkins 管理基础环境 URL 和主账号 API Key
但账单、B 账号、zero 账号仍由项目文件管理
```

需要 Jenkins Credentials：

```text
llm-china-base-url
llm-china-api-key
llm-overseas-base-url
llm-overseas-api-key
```

如果你确认所有账号凭据都已经由文件管理，可以先用方式 A。下面 Jenkinsfile 会给出方式 A，方式 B 放在附录中。

## 8. 创建 Jenkins Job

### 8.1 新建 Job

1. 打开 Jenkins 首页。
2. 点击左侧 `New Item`。
3. 输入 Job 名称：

   ```text
   llm-api-case
   ```

4. 选择 `Pipeline`。
5. 点击 `OK`。

### 8.2 勾选参数化构建

1. 进入 Job 配置页。
2. 找到 `General`。
3. 勾选：

   ```text
   This project is parameterized
   ```

### 8.3 添加参数 RUN_FRAMEWORK_TESTS

1. 点击 `Add Parameter`。
2. 选择 `Boolean Parameter`。
3. 填写：

   ```text
   Name: RUN_FRAMEWORK_TESTS
   Default Value: 勾选
   Description: 是否执行框架单测 tests
   ```

### 8.4 添加参数 RUN_COLLECT_ONLY

1. 点击 `Add Parameter`。
2. 选择 `Boolean Parameter`。
3. 填写：

   ```text
   Name: RUN_COLLECT_ONLY
   Default Value: 勾选
   Description: 是否执行 module/smoke collect-only
   ```

### 8.5 添加参数 RUN_REAL_SMOKE

1. 点击 `Add Parameter`。
2. 选择 `Boolean Parameter`。
3. 填写：

   ```text
   Name: RUN_REAL_SMOKE
   Default Value: 不勾选
   Description: 是否执行真实环境 smoke 用例
   ```

### 8.6 添加参数 USE_CHINA_ENVIRONMENT

1. 点击 `Add Parameter`。
2. 选择 `Choice Parameter`。
3. 填写：

   ```text
   Name: USE_CHINA_ENVIRONMENT
   Choices:
   TRUE
   FALSE
   Description: TRUE 使用国内环境，FALSE 使用海外环境
   ```

注意：`Choices` 中一行一个值。

### 8.7 添加参数 SMOKE_TARGET

1. 点击 `Add Parameter`。
2. 选择 `String Parameter`。
3. 填写：

   ```text
   Name: SMOKE_TARGET
   Default Value: module/smoke
   Description: 真实 smoke 执行范围
   ```

常见值：

```text
module/smoke
module/smoke/test_response_body_validation.py
module/smoke/test_图片生成异步调用.py
```

## 9. 配置 Pipeline 脚本

零基础第一版建议先用 `Pipeline script`，把脚本直接粘贴进 Jenkins 页面。跑通后再改成仓库里的 `Jenkinsfile`。

### 9.1 选择 Pipeline script

1. 在 Job 配置页往下滚动。
2. 找到 `Pipeline`。
3. `Definition` 选择：

   ```text
   Pipeline script
   ```

4. 在 `Script` 文本框粘贴下面脚本。

### 9.2 Jenkinsfile 方式 A：使用已有 .env

如果 Jenkins workspace 中已经有项目所需 `.env`，使用这个版本：

```groovy
pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(
            name: 'RUN_FRAMEWORK_TESTS',
            defaultValue: true,
            description: '是否执行框架单测 tests'
        )
        booleanParam(
            name: 'RUN_COLLECT_ONLY',
            defaultValue: true,
            description: '是否执行 module/smoke collect-only'
        )
        booleanParam(
            name: 'RUN_REAL_SMOKE',
            defaultValue: false,
            description: '是否执行真实环境 smoke 用例'
        )
        choice(
            name: 'USE_CHINA_ENVIRONMENT',
            choices: ['TRUE', 'FALSE'],
            description: 'TRUE 使用国内环境，FALSE 使用海外环境'
        )
        string(
            name: 'SMOKE_TARGET',
            defaultValue: 'module/smoke',
            description: '真实 smoke 执行范围'
        )
    }

    environment {
        GENERATE_ALLURE_REPORT = 'FALSE'
        GENERATE_HISTORY_REPORT = 'FALSE'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Check Runtime Env') {
            steps {
                powershell '''
                if (!(Test-Path .env)) {
                    Write-Error ".env does not exist in workspace. Please prepare .env before running Jenkins job."
                }
                '''
            }
        }

        stage('Prepare Python Env') {
            steps {
                powershell '''
                if (!(Test-Path .venv)) {
                    py -m venv .venv
                }

                .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip
                .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt

                if (Test-Path package.json) {
                    npm install
                }

                New-Item -ItemType Directory -Force reports | Out-Null
                '''
            }
        }

        stage('Framework Unit Tests') {
            when {
                expression { return params.RUN_FRAMEWORK_TESTS }
            }
            steps {
                powershell '''
                $env:GENERATE_ALLURE_REPORT="FALSE"
                $env:GENERATE_HISTORY_REPORT="FALSE"
                .\\.venv\\Scripts\\python.exe -m pytest tests -q --junitxml=reports\\unit-tests.xml
                '''
            }
            post {
                always {
                    junit allowEmptyResults: false, testResults: 'reports/unit-tests.xml'
                }
            }
        }

        stage('Collect Smoke Cases') {
            when {
                expression { return params.RUN_COLLECT_ONLY }
            }
            steps {
                powershell '''
                .\\.venv\\Scripts\\python.exe run_master.py module\\smoke --collect-only -q
                '''
            }
        }

        stage('Real Smoke') {
            when {
                expression { return params.RUN_REAL_SMOKE }
            }
            steps {
                powershell '''
                $target = "${env:SMOKE_TARGET}"
                .\\.venv\\Scripts\\python.exe run_master.py $target --junitxml=reports\\smoke-tests.xml
                '''
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
```

### 9.3 如果 `py` 命令不可用

把脚本中的：

```powershell
py -m venv .venv
```

改成：

```powershell
python -m venv .venv
```

### 9.4 保存 Job

1. 粘贴脚本后点击页面底部 `Save`。
2. 回到 Job 首页。

## 10. 第一次构建

### 10.1 先跑最安全模式

1. 进入 `llm-api-case` Job。
2. 点击左侧：

   ```text
   Build with Parameters
   ```

3. 参数保持默认：

   ```text
   RUN_FRAMEWORK_TESTS=true
   RUN_COLLECT_ONLY=true
   RUN_REAL_SMOKE=false
   USE_CHINA_ENVIRONMENT=TRUE
   SMOKE_TARGET=module/smoke
   ```

4. 点击 `Build`。

### 10.2 查看执行过程

1. 左侧或页面下方会出现一次构建，例如：

   ```text
   #1
   ```

2. 点击 `#1`。
3. 点击：

   ```text
   Console Output
   ```

4. 按 stage 查看日志。

### 10.3 成功时你应该看到什么

日志中应出现类似：

```text
192 passed, 1 skipped
42 tests collected
```

具体数字以后可能随用例变化而变化，核心是：

```text
Framework Unit Tests 阶段成功
Collect Smoke Cases 阶段成功
构建结果是 Success
```

## 11. 查看测试报告

### 11.1 JUnit 报告

进入构建详情页后，通常会看到：

```text
Test Result
```

点进去可以查看：

```text
测试总数
失败数
跳过数
失败堆栈
历史趋势
```

### 11.2 Allure 报告

构建完成后，页面上应出现：

```text
Allure Report
```

点进去可以查看：

```text
用例列表
失败详情
请求/响应附件
重试记录
轮询状态迁移
```

### 11.3 构建产物

进入构建详情页，找到：

```text
Build Artifacts
```

应能看到：

```text
allure-results/**
reports/unit-tests.xml
```

如果执行了真实 smoke，还应有：

```text
reports/smoke-tests.xml
```

## 12. 第二次构建：真实 smoke

只有当默认构建已经成功后，再跑真实 smoke。

### 12.1 执行完整 smoke

1. 点击 `Build with Parameters`。
2. 设置：

   ```text
   RUN_FRAMEWORK_TESTS=true
   RUN_COLLECT_ONLY=true
   RUN_REAL_SMOKE=true
   SMOKE_TARGET=module/smoke
   ```

3. 点击 `Build`。

### 12.2 只执行一个文件

如果你只想验证某个文件：

```text
SMOKE_TARGET=module/smoke/test_response_body_validation.py
```

异步图片 smoke：

```text
SMOKE_TARGET=module/smoke/test_图片生成异步调用.py
```

注意：真实 smoke 可能产生 API 调用成本，也可能因为外部环境不稳定失败。

## 13. 后续改成 Jenkinsfile 入仓

页面粘贴脚本跑通后，建议把脚本保存为仓库根目录：

```text
Jenkinsfile
```

然后修改 Job 配置：

1. 进入 `llm-api-case`。
2. 点击 `Configure`。
3. 找到 `Pipeline`。
4. `Definition` 改为：

   ```text
   Pipeline script from SCM
   ```

5. SCM 选择：

   ```text
   Git
   ```

6. 填写：

   ```text
   Repository URL: <你的仓库地址>
   Credentials: <Git 拉代码凭据>
   Branch Specifier: */main
   Script Path: Jenkinsfile
   ```

7. 点击 `Save`。

这样 Jenkinsfile 的修改也能进入代码评审。

## 14. 常见失败和处理

### 14.1 找不到 .env

现象：

```text
.env does not exist in workspace
```

处理：

```text
确认 .env 已经放到 Jenkins workspace
或改用附录中的 Jenkins Credentials 生成 .env 方式
```

### 14.2 找不到 py

现象：

```text
py : The term 'py' is not recognized
```

处理：

把 Jenkinsfile 中：

```powershell
py -m venv .venv
```

改成：

```powershell
python -m venv .venv
```

### 14.3 pip install 失败

处理：

```text
确认 Jenkins 机器能访问 Python 包源
确认 requirements.txt 存在
确认 Python 版本正确
如果公司网络需要代理，给 Jenkins 机器配置代理
```

### 14.4 npm install 失败

处理：

```text
确认 Node.js 已安装
确认 npm -v 可用
如果 Jenkins Allure 插件能正常生成报告，第一版可以临时删除 npm install
```

### 14.5 Allure Report 不显示

检查：

```text
Allure Jenkins Plugin 是否安装
Allure Commandline 是否在 Tools 中配置
pytest.ini 是否包含 --alluredir=allure-results
构建后 workspace 是否存在 allure-results
Pipeline post 中 allure path 是否为 allure-results
```

### 14.6 JUnit 报告不显示

检查：

```text
pytest 命令是否带 --junitxml=reports\unit-tests.xml
reports/unit-tests.xml 是否存在
junit testResults 路径是否写成 reports/unit-tests.xml
```

### 14.7 中文文件名 smoke 路径问题

如果执行：

```text
module/smoke/test_图片生成异步调用.py
```

出现编码或路径问题，先确认：

```text
Jenkins agent 使用 PowerShell
workspace 路径不包含异常字符
Git checkout 后文件名显示正常
```

### 14.8 真实 smoke 超时

处理：

```text
先只跑 collect-only
再通过 SMOKE_TARGET 缩小到单个文件
必要时提高 Pipeline timeout
确认 API_TIMEOUT 配置合理
```

## 15. 附录：如果你想让 Jenkins 生成基础 .env

如果后续你不想依赖 workspace 中已有 `.env`，可以改用 Jenkins Credentials。

### 15.1 创建 Credentials

进入：

```text
Manage Jenkins -> Credentials -> System -> Global credentials
```

新增 `Secret text`：

```text
ID: llm-china-base-url
Value: https://pre.juhemoxing.com

ID: llm-china-api-key
Value: <国内环境 API Key>

ID: llm-overseas-base-url
Value: https://pre.tokensave.pro

ID: llm-overseas-api-key
Value: <海外环境 API Key>
```

### 15.2 替换 Check Runtime Env 阶段

把 Jenkinsfile 中的：

```groovy
stage('Check Runtime Env') {
    steps {
        powershell '''
        if (!(Test-Path .env)) {
            Write-Error ".env does not exist in workspace. Please prepare .env before running Jenkins job."
        }
        '''
    }
}
```

替换为：

```groovy
stage('Write Runtime Env') {
    steps {
        withCredentials([
            string(credentialsId: 'llm-china-base-url', variable: 'CHINA_BASE_URL'),
            string(credentialsId: 'llm-china-api-key', variable: 'CHINA_API_KEY_VALUE'),
            string(credentialsId: 'llm-overseas-base-url', variable: 'OVERSEAS_BASE_URL'),
            string(credentialsId: 'llm-overseas-api-key', variable: 'OVERSEAS_API_KEY_VALUE')
        ]) {
            powershell '''
            @"
USE_CHINA_ENVIRONMENT=${env:USE_CHINA_ENVIRONMENT}

CHINA_TEST_ENVIRONMENT_BASE_URL=$env:CHINA_BASE_URL
CHINA_API_KEY=$env:CHINA_API_KEY_VALUE

OVERSEAS_TEST_BASE_URL=$env:OVERSEAS_BASE_URL
OVERSEAS_API_KEY=$env:OVERSEAS_API_KEY_VALUE

API_TIMEOUT=600
GENERATE_ALLURE_REPORT=FALSE
GENERATE_HISTORY_REPORT=FALSE
HISTORY_REPORT_KEEP_LIMIT=30
"@ | Set-Content -Encoding UTF8 .env
            '''
        }
    }
}
```

并在 `post` 中删除 `.env`：

```groovy
powershell '''
if (Test-Path .env) {
    Remove-Item .env -Force
}
'''
```

## 16. 第一版完成标准

你可以按下面清单验收：

```text
Jenkins 能打开 llm-api-case Job
Build with Parameters 能看到 5 个参数
默认参数构建成功
Console Output 中能看到 pytest tests 执行
Console Output 中能看到 smoke collect-only 执行
JUnit Test Result 能打开
Allure Report 能打开
Build Artifacts 能看到 allure-results 和 reports
RUN_REAL_SMOKE=false 时没有真实 API 调用
RUN_REAL_SMOKE=true 时能按 SMOKE_TARGET 执行真实 smoke
```

