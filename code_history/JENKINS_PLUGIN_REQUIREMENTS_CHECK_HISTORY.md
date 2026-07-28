# Jenkins 插件依赖检查记录

## 需求理解

检查当前仓库的 Jenkins 流水线需要下载或确认安装哪些 Jenkins 插件。

## 检查依据

- `Jenkinsfile`
- `dev/jenkins_ci_configuration_guide.md`
- `dev/jenkins_email_notification_design.md`

## 第一性原理判断

Jenkins 插件需求的本质不是“常用插件装全”，而是让当前流水线里的 DSL、step 和报告入口能被 Jenkins 识别并执行。

因果链：

```text
Jenkinsfile 使用某个 step/功能
-> Jenkins Controller 必须加载提供该 step/功能的插件
-> 插件缺失会在解析或执行阶段报错
-> pipeline 无法完成 checkout、测试报告、Allure 报告或邮件通知
```

## TOC 约束判断

当前约束是 Jenkinsfile 能否被 Jenkins 正确解析和执行。优先级应按“缺失后是否直接阻塞构建”排序。

```text
硬依赖插件缺失
-> pipeline 直接失败
-> 无法进入测试反馈闭环
```

可选增强插件缺失：

```text
pipeline 仍可运行
-> 但凭据管理、workspace 清理或体验能力下降
```

## 必装或必须确认已安装

| 插件 | Jenkinsfile 依据 | 作用 |
| --- | --- | --- |
| Pipeline | `pipeline {}`、`stages`、`post`、`script` | 支持 Jenkinsfile 和声明式流水线 |
| Git plugin | `checkout scm` | 从 Git 仓库拉取代码 |
| JUnit plugin | `junit ... testResults` | 展示 pytest 生成的 JUnit XML |
| Allure Jenkins Plugin | `allure includeProperties...` | 读取 `allure-results` 并生成 Allure 报告 |
| Email Extension Plugin | `emailext(...)` | 支持失败、不稳定、恢复成功邮件通知 |
| Timestamper | `timestamps()` | 支持控制台日志时间戳 |
| Pipeline: Nodes and Processes | `powershell(script: ...)` | 提供节点执行、Shell/PowerShell 等基础执行步骤 |
| Pipeline: SCM Step | `checkout scm` | 提供 Pipeline 中的 SCM checkout 步骤 |

说明：部分 `Pipeline:*` 插件通常会作为 Pipeline 套件依赖自动安装，但在离线安装或插件裁剪过的 Jenkins 上需要单独确认。

## 可选或当前非硬依赖

| 插件 | 判断 |
| --- | --- |
| Credentials Binding | 当前 Jenkinsfile 没有使用 `withCredentials`，只有未来改成 Jenkins Credentials 生成 `.env` 时才需要 |
| Workspace Cleanup | 当前 Jenkinsfile 没有使用 `cleanWs()`，不是硬依赖 |
| HTML Publisher | 当前通过 Allure 插件发布报告，不需要 |
| GitHub/GitLab 插件 | 当前 Jenkinsfile 没有使用 GitHub/GitLab 专用 step；只有 webhook、状态回写、MR/PR 集成时才需要 |
| SonarQube Scanner | 当前没有 Sonar 分析步骤，不需要 |
| Slack Notification | 当前没有 `slackSend`，不需要 |
| SSH Agent | 当前没有 `sshagent`，不需要 |
| Config File Provider | 当前没有 `configFileProvider`，不需要 |

## 安装优先级

第一批安装或确认：

```text
Pipeline
Git plugin
JUnit plugin
Allure Jenkins Plugin
Email Extension Plugin
Timestamper
```

离线或最小化 Jenkins 环境额外确认：

```text
Pipeline: Declarative
Pipeline: Groovy
Pipeline: Nodes and Processes
Pipeline: SCM Step
Pipeline: Basic Steps
Workflow API
Workflow CPS
Workflow Job
Workflow Step API
SCM API
Git client plugin
Credentials plugin
```

## 非插件但必须准备

Jenkins Windows agent 还需要：

```text
Git
Python
Node.js / npm
Java
PowerShell
Allure Commandline
```

其中 Allure Commandline 需要在 `Manage Jenkins -> Tools` 中配置，名称建议与 Jenkins 全局配置保持一致。

## 本次代码改动

无业务代码改动。仅新增本检查记录文件。
