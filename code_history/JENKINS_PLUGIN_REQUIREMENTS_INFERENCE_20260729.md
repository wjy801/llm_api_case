# Jenkins 插件需求推断记录

## 需求复述

根据当前仓库根目录的 `Jenkinsfile`，反向推断 Jenkins 控制器和 Windows Agent 需要准备哪些插件与运行环境。

## 第一性原理判断

Jenkins 插件需求的本质不是“常见 CI 插件装全”，而是让 Jenkinsfile 中出现的 DSL、step、publisher 和 post action 能被 Jenkins 解析并执行。

因果链：

```text
Jenkinsfile 使用某个 step 或声明式语法
-> Jenkins 必须加载提供该 step 或语法的插件
-> 插件缺失会导致编译期找不到方法，或执行期无法发布报告/通知
-> 流水线无法完成代码拉取、测试执行、报告归档、Allure 展示或邮件通知
```

## TOC 约束分析

当前最大约束是流水线能否被 Jenkins 解析和进入可执行阶段。

优先级应按失败影响排序：

```text
声明式 Pipeline / 基础 Pipeline 插件缺失
-> Jenkinsfile 直接无法解析
-> 所有后续测试、报告、通知都没有机会执行

测试报告 / Allure / 邮件插件缺失
-> 流水线可执行到部分阶段
-> 但 post 阶段或报告发布阶段失败
-> CI 反馈闭环不完整

工具链缺失
-> 插件可用但 agent 执行命令失败
-> 需要在 Windows 节点补齐 Python、npm、PowerShell、Allure CLI 等
```

## 硬依赖插件

| 插件 | Jenkinsfile 依据 | 缺失影响 |
| --- | --- | --- |
| Pipeline | `pipeline {}`、`stages`、`post`、`script` | 声明式流水线无法运行 |
| Pipeline: Declarative | `pipeline {}`、`parameters`、`environment`、`when` | 声明式语法无法解析 |
| Pipeline: Groovy / Workflow CPS | Jenkinsfile 中自定义 Groovy 方法、`script {}`、`@NonCPS` | 脚本化逻辑和自定义函数无法执行 |
| Pipeline: Nodes and Processes | `powershell(script: ...)` | Windows 节点上无法执行 PowerShell step |
| Pipeline: Basic Steps | `archiveArtifacts`、`fileExists`、`readFile`、`timeout` | 文件读取、归档、超时控制等基础 step 不可用 |
| Pipeline: SCM Step | `checkout scm` | Pipeline 中无法执行 SCM checkout |
| Git plugin | `checkout scm` 且仓库为 Git 项目 | 无法从 Git 仓库拉取代码 |
| Git client plugin | Git plugin 的底层 Git 客户端依赖 | Git checkout 能力不完整 |
| JUnit plugin | `junit allowEmptyResults...` | pytest 生成的 JUnit XML 无法在 Jenkins 测试报告中展示 |
| Allure Jenkins Plugin | `allure includeProperties...` | `allure-results` 无法发布成 Jenkins Allure 报告 |
| Email Extension Plugin | `emailext(...)` | 失败、不稳定、恢复成功邮件通知不可用 |
| Timestamper | `timestamps()` | 控制台日志时间戳选项不可用 |

## 建议第一批安装或确认

```text
Pipeline
Pipeline: Declarative
Pipeline: Groovy
Pipeline: Nodes and Processes
Pipeline: Basic Steps
Pipeline: SCM Step
Git plugin
Git client plugin
JUnit plugin
Allure Jenkins Plugin
Email Extension Plugin
Timestamper
```

说明：如果 Jenkins 是通过推荐插件或 Pipeline 套件安装，部分 `Pipeline:*` 插件通常会作为依赖被自动带入；但在离线安装、最小化安装或插件被裁剪过的环境中，需要逐项确认。

## 可选但当前 Jenkinsfile 不强依赖

| 插件 | 判断 |
| --- | --- |
| Credentials Binding | 当前 Jenkinsfile 没有 `withCredentials`；如果后续把 `.env` 或密钥迁移到 Jenkins Credentials，则需要 |
| Workspace Cleanup | 当前没有 `cleanWs()`；已有 PowerShell 手动清理 `reports` |
| HTML Publisher | 当前使用 Allure 插件，不需要 HTML Publisher 发布 Allure |
| GitHub / GitLab 插件 | 当前没有 GitHub/GitLab 专用 step；只有 webhook、MR/PR、状态回写时才需要 |
| Slack Notification | 当前没有 `slackSend` |
| SSH Agent | 当前没有 `sshagent` |
| Config File Provider | 当前没有 `configFileProvider` |
| SonarQube Scanner | 当前没有 Sonar 分析 step |

## 非插件前置条件

Windows Agent 还需要准备：

```text
Git
Python
Node.js / npm
Java
PowerShell
Allure Commandline
```

其中 Allure Commandline 需要与 Jenkins 全局工具配置匹配，否则即使安装了 Allure Jenkins Plugin，也可能无法生成报告。

## 本次改动

未修改业务代码和 Jenkinsfile。新增本插件需求推断记录文件。
