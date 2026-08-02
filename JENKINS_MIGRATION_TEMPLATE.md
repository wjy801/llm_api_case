# Jenkins 可迁移配置模板

## 1. 目标与边界

本模板用于在新机器、新 Jenkins 实例或新 Job 中复建 `llm-api-case` CI。目标是恢复以下完整链路：

```text
GitHub dev3
-> Docker Jenkins Controller
-> Windows WebSocket Agent
-> 框架测试 / Smoke 收集 / 真实 Smoke
-> P0/P1/Flaky 质量产物
-> JUnit / Allure / 邮件
```

本模板记录可迁移配置，不包含以下敏感信息：

```text
Jenkins 管理员密码或 API Token
Windows Agent secret
GitHub 私有仓库凭据
SMTP 授权码
.env 原文
任何 API Key、账号余额或完整请求响应
```

## 2. 当前已验证配置快照

| 配置项 | 当前值 | 迁移说明 |
| --- | --- | --- |
| Jenkins Controller | Docker Desktop 容器 | 数据保存在命名卷 `jenkins_home` |
| Jenkins 镜像 | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/jenkins/jenkins:2.568.1-lts-jdk21` | 可换成等价官方 LTS JDK 21 镜像 |
| Jenkins URL | `http://localhost:8080` | 新环境按实际地址替换 |
| Controller 重启策略 | `unless-stopped` | Docker 启动后自动恢复 |
| Controller DNS | `1.1.1.1`、`8.8.8.8` | 绕过 Windows hosts 的本地加速映射 |
| Git HTTP 版本 | `HTTP/1.1` | 降低当前网络下 HTTP/2 兼容问题 |
| Job 名称 | `llm-api-case` | 建议保持一致 |
| Job 类型 | Pipeline | `Pipeline script from SCM` |
| Repository URL | `https://github.com/wjy801/llm_api_case.git` | 当前为公开仓库 |
| Branch Specifier | `*/dev3` | 当前 CI 分支 |
| Script Path | `Jenkinsfile` | 仓库根目录 |
| Lightweight checkout | `true` | Jenkinsfile 由 Controller 预先拉取 |
| Agent 节点名 | `Windows` | Jenkinsfile 使用该标签 |
| Agent 标签 | `Windows windows` | 节点模式为 Exclusive |
| Agent 工作目录 | `D:\JenkinsAgent` | 新机器按实际路径替换 |
| `.env` 来源 | `D:/API_CASE/.env` | 位于 Windows Agent 主机 |
| 收件人 | `wujinyang@qiqikeji.com` | `Jenkinsfile` 中的 `CI_MAIL_TO` |
| 发件人 | `13463214057@163.com` | `Jenkinsfile` 中的 `CI_MAIL_FROM` |
| SMTP | `smtp.163.com:465`，SSL | 授权码仅存 Jenkins |
| 构建产物保留 | 4 天 | 构建记录和控制台历史继续保留 |
| 定时任务 | 每日 `00:00` 真实 Smoke | 会产生真实调用和费用 |

## 3. 部署拓扑

```text
Windows 主机
├─ Docker Desktop
│  └─ jenkins Controller :8080 / :50000
├─ D:\API_CASE
│  ├─ .env
│  └─ 项目源码
└─ D:\JenkinsAgent
   ├─ agent.jar
   ├─ remoting/
   └─ workspace/
```

Controller 和 Agent 是两个运行环境：

- Controller 负责在流水线启动前从 SCM 读取 `Jenkinsfile`。
- Windows Agent 负责执行 PowerShell、Python、npm、pytest 和真实接口测试。
- 本机浏览器能访问 GitHub，不代表 Docker Controller 一定能访问；迁移后必须分别验证。

## 4. 前置条件

Windows 主机需要安装：

```text
Docker Desktop
Git
PowerShell
Python
Java 21（用于 Windows Agent 和 Allure）
Node.js / npm
```

网络需要允许：

```text
Controller 访问 GitHub
Agent 访问 GitHub
Agent 访问 Python/npm 镜像源
Agent 访问待测 API 环境
Controller/Agent 访问 SMTP 服务
```

## 5. Jenkins Controller 复建

### 5.1 创建数据卷

```powershell
docker volume create jenkins_home
```

### 5.2 启动 Controller

在 PowerShell 中执行，项目路径按目标机器调整：

```powershell
docker run -d `
  --name jenkins `
  --restart unless-stopped `
  --dns 1.1.1.1 `
  --dns 8.8.8.8 `
  -p 8080:8080 `
  -p 50000:50000 `
  -v jenkins_home:/var/jenkins_home `
  --mount "type=bind,source=D:\API_CASE,target=/D:/API_CASE" `
  -e JENKINS_UC=https://mirrors.huaweicloud.com/jenkins/update-center.json `
  -e JENKINS_UC_EXPERIMENTAL=https://updates.jenkins.io/experimental `
  -e JENKINS_INCREMENTALS_REPO_MIRROR=https://repo.jenkins-ci.org/incrementals `
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/jenkins/jenkins:2.568.1-lts-jdk21
```

说明：

- `/var/jenkins_home` 必须使用持久卷，Job、插件、凭据和历史都保存在其中。
- `/D:/API_CASE` 是当前同机部署的辅助挂载；Pipeline SCM 本身不依赖该挂载。异机部署时可以删除或替换。
- 当前配置不依赖 Steam++，不要添加 `github.com:host-gateway`。
- 不要通过 `http.sslVerify=false` 绕过 TLS 校验。

### 5.3 配置 Controller Git

```powershell
docker exec jenkins git config --global http.version HTTP/1.1
docker exec jenkins git config --global --add safe.directory /D:/API_CASE
docker exec jenkins git config --global --add safe.directory /D:/API_CASE/.git
```

如果删除了项目目录挂载，可以省略两个 `safe.directory`。

### 5.4 验证 Controller

```powershell
docker ps --filter name=jenkins
docker logs --tail 100 jenkins
docker exec jenkins getent ahostsv4 github.com
docker exec jenkins git ls-remote `
  https://github.com/wjy801/llm_api_case.git `
  refs/heads/dev3
```

预期：

```text
日志包含 Jenkins is fully up and running
github.com 解析为公网 IP，不是 127.0.0.1
git ls-remote 返回 dev3 的 commit SHA
```

## 6. 必装插件

版本不要求逐字一致，但插件必须与 Jenkins LTS 兼容并启用：

| 插件 | 当前已验证版本 | 用途 |
| --- | --- | --- |
| `workflow-aggregator` | `608.v67378e9d3db_1` | Pipeline 基础能力 |
| `git` | `5.10.1` | SCM 拉取 |
| `credentials-binding` | `728.v902a_273b_8947` | 凭据绑定 |
| `junit` | `1416.vd753e036de5e` | JUnit 测试结果 |
| `allure-jenkins-plugin` | `2.35.2` | Allure 入口 |
| `email-ext` | `2038.v7b_8817a_499d9` | HTML 邮件 |
| `parameterized-scheduler` | `379.v95c73f233a_df` | 带参数的定时真实 Smoke |
| `ws-cleanup` | `0.49` | 工作区清理，可选 |

插件安装后重启 Jenkins，并确认没有 Failed Plugins。

## 7. Windows Agent 配置

### 7.1 节点模板

```text
Node name: Windows
Remote root directory: D:\JenkinsAgent
Labels: Windows windows
Usage: Only build jobs with label expressions matching this node
Launch method: Launch agents by connecting it to the controller
```

### 7.2 WebSocket 启动模板

从 Jenkins 节点页面下载 `agent.jar`，使用页面实时生成的 secret：

```powershell
java -jar agent.jar `
  -url http://localhost:8080/ `
  -secret <AGENT_SECRET> `
  -name Windows `
  -webSocket `
  -workDir D:\JenkinsAgent
```

如果系统临时目录空间不足：

```powershell
java -Djava.io.tmpdir=D:\JenkinsAgent\tmp -jar agent.jar ...
```

Agent secret 不得写入仓库或模板实例。

### 7.3 Agent 验证

```powershell
git --version
python --version
java -version
node --version
npm --version
Test-Path D:\JenkinsAgent
Test-Path D:\API_CASE\.env
```

## 8. Pipeline Job 配置

### 8.1 General

```text
Job name: llm-api-case
Description: API test framework CI job managed by Jenkinsfile from GitHub SCM.
This project is parameterized: 首次运行后由 Jenkinsfile 同步
Disable concurrent builds: 由 Jenkinsfile 管理
```

### 8.2 Pipeline

```text
Definition: Pipeline script from SCM
SCM: Git
Repository URL: https://github.com/wjy801/llm_api_case.git
Credentials: 公开仓库留空；私有仓库选择目标环境凭据
Branch Specifier: */dev3
Script Path: Jenkinsfile
Lightweight checkout: true
```

Lightweight checkout 在流水线执行前发生于 Controller。此阶段失败时，Windows Agent 和 Jenkinsfile 中的 `retry` 都尚未运行。

### 8.3 Jenkinsfile 参数

| 参数 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `RUN_FRAMEWORK_TESTS` | Boolean | `true` | 执行 `tests/` 框架测试 |
| `RUN_COLLECT_ONLY` | Boolean | `true` | 只收集 Smoke，不调用真实接口 |
| `RUN_REAL_SMOKE` | Boolean | `false` | 执行真实 Smoke |
| `GENERATE_PIPELINE_SUMMARY` | Boolean | `true` | 为每轮构建生成 Pipeline 执行摘要 |
| `ALWAYS_SEND_REPORT_EMAIL` | Boolean | `false` | 成功构建也发送邮件 |
| `USE_CHINA_ENVIRONMENT` | Choice | `TRUE` | 国内或海外环境 |
| `SMOKE_TARGET` | String | `module/smoke` | 目录、文件或 nodeid |
| `TEST_PARALLEL_WORKERS` | Choice | `off` | `off/auto/2/4/8` |

### 8.4 定时真实 Smoke

当前 Jenkinsfile 配置：

```text
每天 00:00
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=false
RUN_REAL_SMOKE=true
GENERATE_PIPELINE_SUMMARY=true
ALWAYS_SEND_REPORT_EMAIL=true
USE_CHINA_ENVIRONMENT=TRUE
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=off
```

真实 Smoke 会产生模型调用和费用。不需要定时执行时，应删除/注释 Jenkinsfile 的 `parameterizedCron`，或在 Job 的 Build Triggers 中停用后同步代码。

### 8.5 构建产物保留

Jenkinsfile 使用：

```groovy
buildDiscarder(logRotator(
    artifactDaysToKeepStr: '4',
    artifactNumToKeepStr: '-1',
    daysToKeepStr: '-1',
    numToKeepStr: '-1'
))
```

含义：

- 归档产物只保留最近 4 天。
- 不按构建数量限制产物。
- 构建编号、结果、参数和控制台历史持续保留。
- 外部 Flaky SQLite 不属于构建产物，不会被该策略删除。

## 9. Runtime `.env` 与持久数据

### 9.1 `.env` 来源

当前 Jenkinsfile：

```text
workspace 没有 .env
-> 从 D:/API_CASE/.env 复制
-> 两处都不存在则构建失败
```

迁移到新 Agent 时，修改 Jenkinsfile 的 `$sourceEnv` 或在相同路径准备 `.env`。

建议从 `.env.example` 复制，只填写目标环境实际值。至少核对：

```text
USE_CHINA_ENVIRONMENT
CHINA_TEST_ENVIRONMENT_BASE_URL
CHINA_API_KEY
CHINA_CONTROL_API_KEY
OVERSEAS_TEST_BASE_URL
OVERSEAS_API_KEY
OVERSEAS_CONTROL_API_KEY
API_TIMEOUT
```

Smoke 特殊账号按 `.env.example` 的名称配置，不要改成 Jenkins 全局明文参数。

### 9.2 P0/P1 配置

真实 Smoke 阶段由 Jenkinsfile自动设置：

```text
QUALITY_ENABLE=1
QUALITY_SEMANTIC_ENABLE=1
QUALITY_METRICS_ENABLE=1
QUALITY_P1_REPORT_ENABLE=1
QUALITY_OUTPUT_DIR=reports/quality
QUALITY_SHADOW_GATE=1
QUALITY_MIN_REQUEST_SAMPLES=20
QUALITY_HTTP_5XX_WARN_RATE=0.02
QUALITY_TIMEOUT_WARN_RATE=0.05
```

这些配置不需要写入 Jenkins 全局环境。

### 9.3 Flaky SQLite

在 `.env` 中配置：

```text
QUALITY_FLAKY_DB_PATH=D:/JenkinsData/llm-api-case/flaky-history.db
```

要求：

- 必须是绝对持久路径。
- 父目录提前创建并授予 Windows Agent 写权限。
- 不要放在会被 `deleteDir()` 清理的 workspace。
- 一个数据库只由一个 Job 独占写入。
- 不复制数据库时，新环境会重新积累样本；需要延续 Flaky 历史时必须安全迁移数据库文件。

迁移后可检查：

```powershell
.\.venv\Scripts\python.exe -m quality.cli flaky-db-check `
  --db D:\JenkinsData\llm-api-case\flaky-history.db
```

## 10. SMTP 与邮件

### 10.1 Jenkins 全局配置

路径：

```text
Manage Jenkins
-> System
-> Extended E-mail Notification
```

当前模板：

```text
SMTP server: smtp.163.com
SMTP port: 465
Use SSL: true
Use TLS: false
Charset: UTF-8
Default Content Type: text/html
SMTP username: <SMTP_ACCOUNT>
SMTP password: <163 SMTP 授权码，不写入仓库>
```

Jenkinsfile 当前配置：

```text
CI_MAIL_FROM=13463214057@163.com
CI_MAIL_TO=wujinyang@qiqikeji.com
```

迁移到其他账号时，需要同时修改 Jenkinsfile 和 Jenkins 全局 SMTP。

### 10.2 触发规则

```text
FAILURE  -> FAILED 邮件
UNSTABLE -> UNSTABLE 邮件
SUCCESS 且 ALWAYS_SEND_REPORT_EMAIL=true -> SUCCESS 邮件
SUCCESS 且上一轮为 FAILURE/UNSTABLE -> FIXED 邮件
其他连续 SUCCESS -> 不发送
```

### 10.3 邮件内容

邮件包含：

```text
构建状态、分支、提交、耗时
JUnit 汇总和最多 5 个失败用例
Smoke 收集数量和执行参数
Pipeline 执行摘要 / Allure / JUnit / P0 / P1 / 构建产物链接
```

邮件不包含“构建详情”和“控制台日志”链接。Pipeline 摘要、P0/P1 文件不存在时，对应链接自动隐藏。

## 11. P0/P1/Flaky 产物

真实 Smoke 完成后，至少检查：

| 文件 | 作用 |
| --- | --- |
| `reports/pipeline-summary.md` | 每轮 Pipeline 参数、阶段与执行效果的默认人工入口 |
| `reports/quality/gate-report.md` | 中文 P0 影子门禁报告 |
| `reports/quality/gate-report.json` | 机器可读门禁规则与证据 |
| `reports/quality/summary.json` | P0 完整汇总 |
| `reports/quality/metrics/run-metrics.json` | P1 单次运行指标 |
| `reports/quality/p1-observation.md` | 中文 P1 与 Flaky 报告 |
| `reports/quality/p1-observation.json` | P1 机器数据 |
| `reports/quality/flaky-import.json` | Flaky 历史导入结果 |
| `reports/quality/flaky-evaluation.json` | Flaky 状态评估结果 |

P0 仍为影子门禁，不覆盖 pytest/Jenkins 结果。P1 不估算成本，不建立性能基线，缺失 usage 不按零计算。

## 12. 最小 Job `config.xml` 模板

公开仓库可直接使用以下骨架。私有仓库在 `userRemoteConfigs` 中增加目标环境的 `credentialsId`。

```xml
<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>API test framework CI job managed by Jenkinsfile from GitHub SCM.</description>
  <keepDependencies>false</keepDependencies>
  <properties>
    <jenkins.model.BuildDiscarderProperty>
      <strategy class="hudson.tasks.LogRotator">
        <daysToKeep>-1</daysToKeep>
        <numToKeep>-1</numToKeep>
        <artifactDaysToKeep>4</artifactDaysToKeep>
        <artifactNumToKeep>-1</artifactNumToKeep>
      </strategy>
    </jenkins.model.BuildDiscarderProperty>
  </properties>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
    <scm class="hudson.plugins.git.GitSCM" plugin="git">
      <configVersion>2</configVersion>
      <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
          <url>https://github.com/wjy801/llm_api_case.git</url>
        </hudson.plugins.git.UserRemoteConfig>
      </userRemoteConfigs>
      <branches>
        <hudson.plugins.git.BranchSpec>
          <name>*/dev3</name>
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

通过 API 创建 Job：

```powershell
$jenkinsUrl = '<JENKINS_URL>'
$user = '<JENKINS_USER>'
$apiToken = '<JENKINS_API_TOKEN>'
$jobName = 'llm-api-case'
$configXmlPath = '<CONFIG_XML_PATH>'

$pair = "${user}:${apiToken}"
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$headers = @{ Authorization = "Basic $auth" }
$crumb = Invoke-RestMethod -Uri "$jenkinsUrl/crumbIssuer/api/json" -Headers $headers
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

## 13. 推荐迁移顺序

```text
1. 备份旧 jenkins_home 和 Flaky SQLite
2. 创建新 Controller 和数据卷
3. 安装插件并配置 SMTP
4. 创建并连接 Windows Agent
5. 准备 Agent 上的 .env 和 Flaky 持久目录
6. 创建 Pipeline Job，配置 dev3 SCM
7. 执行最小无真实调用构建
8. 验证邮件、JUnit、Allure 和产物归档
9. 获得费用授权后执行小范围真实 Smoke
10. 最后启用完整真实 Smoke 和定时任务
```

备份 Docker 数据卷示例：

```powershell
New-Item -ItemType Directory -Force D:\JenkinsBackup | Out-Null
docker stop jenkins
docker run --rm `
  -v jenkins_home:/source `
  -v "D:\JenkinsBackup:/backup" `
  alpine sh -c "cd /source && tar czf /backup/jenkins_home.tgz ."
docker start jenkins
```

执行备份前确认没有正在运行或排队的构建。

## 14. 迁移验收

### 14.1 最小安全构建

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
ALWAYS_SEND_REPORT_EMAIL=false
USE_CHINA_ENVIRONMENT=TRUE
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=off
```

预期：

```text
构建 SUCCESS
Windows Agent 执行测试
JUnit 与 Allure 入口可用
reports/unit-tests.xml 归档
reports/smoke-collect.txt 归档
没有真实模型调用
```

### 14.2 小范围真实 Smoke

仅在 Key、余额、网络和模型服务确认可用后执行：

```text
RUN_FRAMEWORK_TESTS=false
RUN_COLLECT_ONLY=false
RUN_REAL_SMOKE=true
ALWAYS_SEND_REPORT_EMAIL=true
SMOKE_TARGET=module/smoke/test_response_body_validation.py
TEST_PARALLEL_WORKERS=off
```

预期生成 P0/P1 报告；未配置 Flaky 数据库时，报告会明确显示对应数据源不可用或禁用，不应伪造状态。

### 14.3 完整真实 Smoke

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=false
RUN_REAL_SMOKE=true
ALWAYS_SEND_REPORT_EMAIL=true
SMOKE_TARGET=module/smoke
TEST_PARALLEL_WORKERS=off 或 auto
```

验收：

```text
并发池和 serial 池均生成 JUnit
P0 数据完整性为 complete，或明确列出完整性问题
P1 报告包含逻辑调用、耗时和 usage 覆盖
Flaky import/state quick_check 正常
邮件包含 P0/P1 直达链接
Jenkins Job 显示 4 天产物保留策略
```

## 15. 常见问题

### 15.1 构建在流水线开始前 Git fetch 失败

这是 Controller SCM 问题，不是 Windows Agent 或 Smoke 问题。检查：

```powershell
docker exec jenkins getent ahostsv4 github.com
docker exec jenkins git config --global --get http.version
docker exec jenkins git ls-remote `
  https://github.com/wjy801/llm_api_case.git `
  refs/heads/dev3
```

要求：

```text
github.com 不能解析为容器内 127.0.0.1
http.version 应为 HTTP/1.1
不要关闭 TLS 校验
```

直连 GitHub 持续不稳定时，应配置长期稳定的企业 HTTP/SOCKS 代理；不要依赖临时 hosts 修改。也可以关闭 Lightweight checkout 并配置 SCM 重试，但仍不能替代稳定网络。

### 15.2 Windows Agent 不在线

```text
检查 Agent Java 进程
检查 node name、secret、Controller URL
检查 D:\JenkinsAgent 可写
检查 WebSocket 是否被网络策略阻断
```

### 15.3 找不到 `.env`

```text
检查 D:\API_CASE\.env
检查 Jenkinsfile 中 $sourceEnv
检查 Agent 运行账户的读取权限
确认 .env 没有被提交到仓库
```

### 15.4 P0/P1 报告不存在

```text
确认 RUN_REAL_SMOKE=true
检查 reports/quality/run.json
检查 Console 中 Quality merge/report/metrics 日志
检查 archiveArtifacts 是否归档 reports/**
```

### 15.5 Flaky 没有状态

```text
检查 QUALITY_FLAKY_DB_PATH 是否为绝对路径
检查父目录是否存在且可写
确认本轮 RunStatus 为 FINISHED
确认 P0 integrity 允许导入
执行 quality.cli flaky-db-check
```

### 15.6 邮件不发送

```text
确认 Email Extension Plugin 已启用
确认 smtp.163.com:465 SSL 测试成功
确认 CI_MAIL_TO/CI_MAIL_FROM
确认构建符合 FAILED、UNSTABLE、FIXED 或 ALWAYS_SEND_REPORT_EMAIL=true
```

### 15.7 Allure 无入口

```text
确认 Allure Jenkins Plugin 已安装
确认 Agent 已安装 Java 和 npm 依赖
确认 allure-results 非空
检查 Jenkins Tools 中的 Allure Commandline 配置
```

### 15.8 4 天前产物未立即删除

LogRotator 通常在后续构建轮转时执行。确认 Job 配置中：

```text
artifactDaysToKeep=4
daysToKeep=-1
numToKeep=-1
```

不要手工删除 `jenkins_home/jobs`，避免破坏构建元数据。

## 16. 最终安全检查

迁移完成后确认：

```text
[ ] GitHub 分支为 dev3
[ ] Controller 与 Agent 都能访问 GitHub
[ ] Agent 标签为 Windows
[ ] .env 和特殊账号 Key 未进入仓库
[ ] SMTP 授权码未进入 Jenkinsfile
[ ] Flaky SQLite 位于 workspace 外部
[ ] P0/P1 JSON 和 Markdown 均已归档
[ ] GENERATE_PIPELINE_SUMMARY 默认开启，pipeline-summary.md 已归档
[ ] 邮件没有构建详情和控制台日志链接
[ ] 构建产物保留 4 天，构建记录继续保留
[ ] 定时真实 Smoke 的费用影响已确认
[ ] 真实请求、响应和附件继续经过脱敏
```
