# Jenkins 可迁移配置模板

## 1. 目标

这份模板用于把当前 `llm-api-case` Jenkins CI 配置迁移到新的 Jenkins 实例、同一 Jenkins 的新 Job，或新的 Windows Agent。

迁移的核心不是复制某台机器的状态，而是复建下面这条链路：

```text
GitHub 仓库
-> Jenkins Pipeline Job
-> Windows Agent
-> Python 虚拟环境
-> 框架单测 / Smoke 收集 / 可选真实 Smoke
-> JUnit / Allure / 构建产物归档
-> 失败、不稳定、恢复成功邮件通知
```

本模板不包含 Jenkins API Token、SMTP 授权码、GitHub 密码、API Key 或 `.env` 内容。

## 2. 当前配置快照

| 配置项 | 当前值 | 迁移说明 |
| --- | --- | --- |
| Jenkins URL | `http://localhost:8080` | 新环境按实际地址替换 |
| Job 名称 | `llm-api-case` | 建议保持一致，便于报告和邮件识别 |
| Job 类型 | `Pipeline` | 使用 `Pipeline script from SCM` |
| SCM | `Git` | 从仓库读取 `Jenkinsfile` |
| Repository URL | `https://github.com/wjy801/llm_api_case.git` | 新仓库地址按需替换 |
| Branch Specifier | `*/dev2` | 当前 CI 分支 |
| Script Path | `Jenkinsfile` | 固定为仓库根目录 Jenkinsfile |
| Git 凭据 ID | `Github` | 迁移后需要在 Jenkins 凭据中创建同名或修改 Job 配置 |
| Agent 节点名 | `Windows` | 当前在线节点 |
| Agent 标签 | `Windows`, `windows` | Jenkinsfile 当前使用 `agent { label 'Windows' }` |
| Agent 工作目录 | `D:\JenkinsAgent` | 新机器按实际目录替换 |
| 本地 `.env` 来源 | `D:/Code/Form/llm_api_case/.env` | 强环境绑定项，迁移时必须调整 |
| 收件人 | `3239682586@qq.com` | Jenkinsfile 中 `CI_MAIL_TO` |
| 发件邮箱 | `18617962759@163.com` | Jenkinsfile 中 `CI_MAIL_FROM`，SMTP 全局配置也要一致 |
| SMTP Server | `smtp.163.com` | 在 Jenkins 全局配置中设置 |
| SMTP 授权码 | 不入库 | 只填在 Jenkins 全局邮件配置或 Jenkins 凭据中 |

## 3. 必装插件模板

当前已验证插件：

| 插件 | 当前版本 | 用途 |
| --- | --- | --- |
| `workflow-aggregator` | `608.v67378e9d3db_1` | Pipeline 基础能力 |
| `git` | `5.10.1` | 拉取 Git 仓库 |
| `credentials-binding` | `725.ve52b_2328a_fde` | 凭据绑定能力 |
| `junit` | `1403.vd9d1413fd205` | 展示 pytest JUnit XML |
| `allure-jenkins-plugin` | `2.33.0` | 生成 Allure 报告入口 |
| `email-ext` | `2038.v7b_8817a_499d9` | HTML 邮件通知 |
| `ws-cleanup` | `0.49` | 工作区清理能力，当前可选 |

迁移时不强制版本完全一致，但必须确认这些插件已安装并启用。

## 4. Windows Agent 模板

推荐配置：

```text
Node name: Windows
Remote root directory: D:\JenkinsAgent
Labels: Windows windows
Usage: Only build jobs with label expressions matching this node
Launch method: 按实际 Jenkins Agent 连接方式配置
```

Agent 机器必须具备：

```text
PowerShell
Git
Python
Java
Node.js / npm
可访问 GitHub
可访问 Python 包源
可访问待测 API 环境
```

如果 Jenkins Agent 使用 Java 启动，并且系统盘临时目录空间不足，可使用独立临时目录：

```powershell
java -Djava.io.tmpdir=D:\JenkinsAgent\tmp -jar agent.jar ...
```

## 5. Jenkins Job 模板

### 5.1 General

```text
Job name: llm-api-case
Description: API test framework CI job managed by Jenkinsfile from GitHub SCM.
This project is parameterized: 由 Jenkinsfile 自动同步参数
Disable concurrent builds: true
```

### 5.2 Pipeline

```text
Definition: Pipeline script from SCM
SCM: Git
Repository URL: https://github.com/wjy801/llm_api_case.git
Credentials: Github
Branch Specifier: */dev2
Script Path: Jenkinsfile
Lightweight checkout: true
```

### 5.3 当前参数

这些参数由 `Jenkinsfile` 管理，Job 首次运行后会自动同步：

| 参数 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `RUN_FRAMEWORK_TESTS` | Boolean | `true` | 执行 `tests` 框架测试 |
| `RUN_COLLECT_ONLY` | Boolean | `true` | 只收集 `module/smoke` 用例，不真实调用接口 |
| `RUN_REAL_SMOKE` | Boolean | `false` | 执行真实 Smoke 用例 |
| `USE_CHINA_ENVIRONMENT` | Choice | `TRUE` | 选择国内或默认环境 |
| `SMOKE_TARGET` | String | `module/smoke` | 真实 Smoke 执行范围 |
| `TEST_PARALLEL_WORKERS` | Choice | `off` | `off/auto/2/4/8`，控制并发执行 |

## 6. Runtime `.env` 模板

当前 Jenkinsfile 的策略是：

```text
如果 workspace 没有 .env
-> 从 D:/Code/Form/llm_api_case/.env 复制
-> 如果来源也不存在，则构建失败
```

迁移时必须二选一：

### 方案 A：保持文件方式

在新 Agent 上准备 `.env` 文件，并修改 Jenkinsfile 中的来源路径：

```groovy
$sourceEnv = 'D:/Code/Form/llm_api_case/.env'
```

替换为新机器真实路径，例如：

```groovy
$sourceEnv = 'D:/JenkinsSecrets/llm_api_case/.env'
```

注意：

```text
.env 不提交 Git
B 账号、账单账号、zero 账号仍按现有项目文件或用例范围管理
特殊账号默认不通过 Jenkins 全局参数注入
```

### 方案 B：改为 Jenkins 凭据生成 `.env`

当前没有采用该方式。若后续迁移到多人共享 Jenkins，建议再单独设计，不要直接把账号、账单、zero 账号做成全局统一配置。

## 7. 邮件通知模板

### 7.1 Jenkins 全局 SMTP

路径：

```text
Manage Jenkins
-> System
-> Extended E-mail Notification
```

推荐配置：

```text
SMTP server: smtp.163.com
SMTP username: 18617962759@163.com
SMTP password: <163 邮箱 SMTP 授权码，不写入仓库>
Default Content Type: HTML
Default Recipients: 3239682586@qq.com
```

端口和加密方式按 163 邮箱实际设置：

```text
常见 SSL: 465
常见 TLS/STARTTLS: 587
```

配置后必须先使用 Jenkins 的测试邮件功能验证 SMTP 可用。

### 7.2 Jenkinsfile 邮件行为

当前触发规则：

```text
FAILURE  -> 发送 [FAILED]
UNSTABLE -> 发送 [UNSTABLE]
SUCCESS 且上一轮为 FAILURE/UNSTABLE -> 发送 [FIXED]
连续 SUCCESS -> 不发送
```

邮件正文只包含聚合摘要和链接：

```text
Job / Build / Duration / Branch / Commit
JUnit total/failures/errors/skipped
Smoke total/parallel/serial
构建参数
Build / Console / Allure / JUnit 链接
```

邮件正文不应包含：

```text
Jenkins API Token
SMTP 授权码
API Key
.env 内容
Authorization header
完整请求体
完整响应体
账号余额明细
```

## 8. Job config.xml 模板

如需通过 Jenkins API 创建 Job，可用下面结构作为模板。尖括号中的内容按环境替换。

```xml
<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>API test framework CI job managed by Jenkinsfile from GitHub SCM.</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
    <scm class="hudson.plugins.git.GitSCM" plugin="git">
      <configVersion>2</configVersion>
      <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
          <url>https://github.com/wjy801/llm_api_case.git</url>
          <credentialsId>Github</credentialsId>
        </hudson.plugins.git.UserRemoteConfig>
      </userRemoteConfigs>
      <branches>
        <hudson.plugins.git.BranchSpec>
          <name>*/dev2</name>
        </hudson.plugins.git.BranchSpec>
      </branches>
      <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
      <submoduleCfg class="empty-list"/>
      <extensions/>
    </scm>
    <scriptPath>Jenkinsfile</scriptPath>
    <lightweight>true</lightweight>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>
```

创建 Job 的 API 示例：

```powershell
$jenkinsUrl = '<JENKINS_URL>'
$user = '<JENKINS_USER>'
$apiToken = '<JENKINS_API_TOKEN>'
$jobName = 'llm-api-case'
$configXmlPath = '<CONFIG_XML_PATH>'

$pair = "${user}:${apiToken}"
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{ Authorization = "Basic $auth" }
$crumb = Invoke-RestMethod -Uri "$jenkinsUrl/crumbIssuer/api/json" -Headers $headers -Method Get
$headers[$crumb.crumbRequestField] = $crumb.crumb

$configXml = Get-Content -LiteralPath $configXmlPath -Raw
Invoke-WebRequest `
  -Uri "$jenkinsUrl/createItem?name=$jobName" `
  -Headers $headers `
  -Method Post `
  -ContentType 'application/xml' `
  -Body $configXml `
  -UseBasicParsing
```

## 9. Jenkinsfile 迁移检查点

迁移后重点检查这些强绑定点：

```text
agent label 是否存在：Windows
Git credentialsId 是否存在：Github
Branch 是否正确：*/dev2
.env 来源路径是否存在
SMTP 是否能发送测试邮件
Allure 插件是否能识别 allure-results
Python 命令是否可用
requirements.txt 是否安装成功
中文文件名在 Console Output 中是否正常显示
reports/smoke-collect.txt 是否为 UTF-8
```

当前 Jenkinsfile 已处理 Windows 控制台编码：

```text
chcp 65001
PYTHONIOENCODING=utf-8
PYTHONUTF8=1
Smoke 收集文件 UTF-8 写入
```

## 10. 迁移验收流程

### 10.1 最小验证构建

使用参数：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
USE_CHINA_ENVIRONMENT=TRUE
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=off
```

预期：

```text
构建结果 SUCCESS
JUnit 报告生成
Allure 入口生成
reports/unit-tests.xml 归档
reports/smoke-collect.txt 归档
Smoke 收集输出包含 total/parallel/serial
连续成功不发送邮件
```

当前已验证参考结果：

```text
Framework tests: 203 tests, 0 failures, 0 errors, 1 skipped
Smoke collect: 42 total, 16 parallel, 26 serial
```

### 10.2 并发验证构建

使用参数：

```text
TEST_PARALLEL_WORKERS=2
RUN_REAL_SMOKE=false
```

预期：

```text
Framework Unit Tests 使用 pytest-xdist 并发
Smoke collect 仍只做收集
构建成功
```

### 10.3 真实 Smoke 验证

仅在账号、余额、网络、模型服务状态确认可用后执行：

```text
RUN_REAL_SMOKE=true
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=off 或 2
```

更稳妥的方式是先缩小范围：

```text
SMOKE_TARGET=module/smoke/test_response_body_validation.py
```

### 10.4 邮件验证

失败通知验证：

```text
RUN_REAL_SMOKE=true
SMOKE_TARGET=module/not_exists
```

预期：

```text
构建失败
收到 [FAILED] 邮件
邮件包含 HTML 摘要
邮件不包含敏感信息
```

恢复通知验证：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
SMOKE_TARGET=module/smoke
```

预期：

```text
构建成功
如果上一轮失败或不稳定，收到 [FIXED] 邮件
后续连续成功不再发送邮件
```

## 11. 迁移后的常见问题

### 11.1 节点不上线

检查：

```text
Agent Java 进程是否启动
Node name / secret 是否正确
Remote root directory 是否存在
D:\JenkinsAgent\tmp 是否存在并可写
```

### 11.2 找不到 `.env`

检查：

```text
Jenkinsfile 中 $sourceEnv 是否指向新机器真实路径
Jenkins 运行用户是否有权限读取该文件
workspace 中是否已经存在 .env
```

### 11.3 邮件不发送

检查：

```text
Email Extension Plugin 是否启用
Jenkins 全局 SMTP 是否测试通过
CI_MAIL_TO 是否为空
当前构建是否符合 FAILED/UNSTABLE/FIXED 触发条件
连续 SUCCESS 本来就不会发送
```

### 11.4 Smoke 摘要为空

检查：

```text
reports/smoke-collect.txt 是否存在
文件是否为 UTF-8
文件内容是否包含：
  Collected test cases
  Parallel pool cases
  Serial pool cases
```

### 11.5 Allure 无入口

检查：

```text
Allure Jenkins Plugin 是否安装
Jenkins Tools 中 Allure Commandline 是否配置
allure-results 是否存在
post 阶段 allure path 是否为 allure-results
```

## 12. 不迁移的内容

以下内容不应进入模板或仓库：

```text
Jenkins admin 密码
Jenkins API Token
GitHub token/password
SMTP 授权码
.env 原文
API Key
真实账号余额
请求/响应完整日志
```

这些内容只能在目标 Jenkins、目标 Agent 或受控凭据系统中单独配置。
