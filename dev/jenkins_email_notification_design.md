# Jenkins 邮件通知开发方案

## 1. 需求理解

当前 Jenkins CI 已经可以通过单 Job 执行框架测试、Smoke 收集和可选真实 Smoke。

下一阶段目标是在流水线中加入邮件通知能力，使构建失败、不稳定或从失败恢复成功时，相关人员能及时收到通知。

邮件通知不应替代 Jenkins/Allure 报告，只应提供高质量入口：

```text
构建状态
构建参数
失败入口
报告入口
必要摘要
```

不应在邮件正文中直接输出敏感日志、账号凭据、API Key、请求体或完整响应体。

## 2. 第一性原理分析

邮件通知的本质不是“构建结束发邮件”，而是：

```text
CI 状态发生需要关注的变化
-> 相关人员需要及时知道
-> 邮件内容需要帮助判断是否要行动
-> 通知不能制造噪音
-> 通知不能泄露敏感信息
```

因此，邮件通知设计应满足：

- 只在有行动价值时发送。
- 内容简洁，指向 Jenkins/Allure 详情页。
- 不夹带敏感日志。
- SMTP 密码或授权码不进入仓库。

## 3. TOC 约束分析

当前约束点不是 Jenkins 是否能发邮件，而是通知质量。

如果所有成功构建都发邮件：

```text
邮件数量增加
-> 用户忽略通知
-> 真正失败时也容易被忽略
```

如果失败邮件缺少入口：

```text
收到失败通知
-> 仍需要手动找 Job、找构建号、找报告
-> 排障效率低
```

因此推荐策略：

```text
失败通知
不稳定通知
恢复成功通知
普通成功不通知
```

## 4. 推荐实现方式

优先使用 Jenkins 插件：

```text
Email Extension Plugin
```

原因：

- Pipeline 支持更好。
- 支持 HTML 邮件。
- 支持自定义 subject/body。
- 支持构建失败、恢复等场景。
- 后续可扩展收件人策略。

不推荐在 Python 测试框架里直接发邮件。

原因：

```text
测试框架只负责执行和断言
-> CI 通知属于 Jenkins 编排层职责
-> 放在 Jenkinsfile 更容易拿到构建号、Job URL、Allure URL、构建参数
```

## 5. 通知触发规则

建议初版规则：

```text
failure:
  发送 FAILED 邮件

unstable:
  发送 UNSTABLE 邮件

success:
  如果上一轮构建是 FAILURE 或 UNSTABLE，则发送 FIXED 邮件
  普通成功不发送
```

Declarative Pipeline 示例：

```groovy
post {
    failure {
        notifyByEmail('FAILED')
    }
    unstable {
        notifyByEmail('UNSTABLE')
    }
    success {
        script {
            if (currentBuild.previousBuild?.result in ['FAILURE', 'UNSTABLE']) {
                notifyByEmail('FIXED')
            }
        }
    }
}
```

## 6. 邮件内容设计

邮件正文建议只包含摘要和入口。

推荐字段：

```text
项目名称
构建号
构建状态
分支
提交
触发人
关键参数
Jenkins 构建链接
Console Output 链接
Allure Report 链接
JUnit Test Result 链接
```

推荐参数摘要：

```text
RUN_FRAMEWORK_TESTS
RUN_COLLECT_ONLY
RUN_REAL_SMOKE
USE_CHINA_ENVIRONMENT
SMOKE_TARGET
TEST_PARALLEL_WORKERS
```

不建议放入邮件：

- `.env` 内容。
- API Key。
- Authorization header。
- 账号余额明细。
- 完整请求体和响应体。
- 完整 console log。
- Allure 原始附件。

### 6.1 运行结果摘要 HTML

邮件正文建议嵌入一块“运行结果摘要”HTML，但摘要只放聚合结果，不放完整日志。

摘要应回答三个问题：

```text
本次执行了什么
结果是否通过
失败后应该点哪里排查
```

推荐摘要字段：

```text
构建状态
构建耗时
分支与提交
触发参数
Framework Unit Tests 是否执行
Smoke Collect 是否执行
Real Smoke 是否执行
TEST_PARALLEL_WORKERS
JUnit 总用例数
JUnit failures/errors/skipped
Smoke 收集总数
Smoke 并发池数量
Smoke 串行池数量
Allure Report 链接
Console Output 链接
```

推荐数据来源：

```text
reports/*.xml
  解析 JUnit XML，统计 tests/failures/errors/skipped。

reports/smoke-collect.txt
  Collect Smoke Cases 阶段将 run_master.py collect-only 输出落盘。
  从文本中解析：
    Collected test cases
    Parallel pool cases
    Serial pool cases

Jenkins env/currentBuild/params
  获取构建号、Job 名称、构建 URL、参数、状态、耗时。
```

不建议从完整 console log 中截取大段内容放入邮件。Console 只作为链接入口。

推荐 HTML 样式：

```html
<div style="font-family: Arial, sans-serif; color: #222;">
  <h2 style="margin: 0 0 12px;">llm-api-case #123 - FAILED</h2>
  <table style="border-collapse: collapse; min-width: 620px;">
    <tr>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">Status</td>
      <td style="padding: 6px 10px; border: 1px solid #ddd; color: #b00020;"><b>FAILED</b></td>
    </tr>
    <tr>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">Branch</td>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">dev2</td>
    </tr>
    <tr>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">Tests</td>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">202 total, 0 failed, 1 skipped</td>
    </tr>
    <tr>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">Smoke Collect</td>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">42 total, 16 parallel, 26 serial</td>
    </tr>
    <tr>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">Parallel Workers</td>
      <td style="padding: 6px 10px; border: 1px solid #ddd;">2</td>
    </tr>
  </table>

  <p>
    <a href="BUILD_URL">Build</a> |
    <a href="BUILD_URLconsole">Console</a> |
    <a href="BUILD_URLallure/">Allure</a> |
    <a href="BUILD_URLtestReport/">JUnit</a>
  </p>
</div>
```

实际邮件中应由 Jenkinsfile 动态填充这些值。

## 7. Jenkins 全局配置步骤

### 7.1 安装插件

进入：

```text
Manage Jenkins
-> Plugins
-> Available plugins
```

搜索并安装：

```text
Email Extension Plugin
```

如果已安装，则跳过。

### 7.2 配置 SMTP

进入：

```text
Manage Jenkins
-> System
-> Extended E-mail Notification
```

配置项建议：

```text
SMTP server: 邮箱服务商 SMTP 地址
SMTP Port: 465 或 587
Use SSL/TLS: 按邮箱服务商要求选择
SMTP Username: 发件邮箱
SMTP Password: SMTP 授权码，不是邮箱登录密码
Default Content Type: HTML
Default Subject: Jenkins CI Notification
Default Recipients: 默认收件人
```

常见服务商：

```text
QQ 邮箱: smtp.qq.com
163 邮箱: smtp.163.com
企业邮箱: 使用企业邮箱后台提供的 SMTP 地址
```

注意：

- QQ/163/企业邮箱通常需要 SMTP 授权码。
- 不建议使用个人主力邮箱密码。
- 推荐新建单独 CI 发件邮箱。

### 7.3 测试 SMTP

在 Jenkins 系统配置页使用：

```text
Test configuration by sending test e-mail
```

确认能收到测试邮件后，再修改 Jenkinsfile。

## 8. 凭据管理建议

SMTP 密码或授权码不要写入仓库。

推荐：

```text
Manage Jenkins
-> Credentials
-> System
-> Global credentials
```

新增凭据：

```text
Kind: Username with password
ID: ci-smtp
Username: 发件邮箱
Password: SMTP 授权码
```

如果 Jenkins 的 Extended E-mail Notification 全局配置支持选择凭据，则使用该凭据。

如果不支持选择凭据，只在 Jenkins 系统配置界面填写授权码，仍不要写入 Jenkinsfile。

## 9. Jenkinsfile 改造方案

### 9.1 新增收件人环境变量

建议先用固定环境变量：

```groovy
environment {
    CI_MAIL_TO = 'receiver@example.com'
}
```

后续可扩展为 Jenkins 参数：

```groovy
string(name: 'CI_MAIL_TO', defaultValue: 'receiver@example.com', description: 'CI notification recipients.')
```

初版建议不开放为参数，避免误填或遗漏。

### 9.2 新增通知函数

建议先在流水线中生成两个摘要数据源：

```groovy
stage('Framework Unit Tests') {
    steps {
        ciPowerShell('''
        ./.venv/Scripts/python.exe -m pytest tests @parallelArgs -q --junitxml=reports/unit-tests.xml
        ''')
    }
}

stage('Collect Smoke Cases') {
    steps {
        ciPowerShell('''
        ./.venv/Scripts/python.exe run_master.py module/smoke --collect-only -q | Tee-Object -FilePath reports/smoke-collect.txt
        ''')
    }
}
```

然后在 Jenkinsfile 中增加摘要构造函数。

示例：

```groovy
Map readJunitSummary() {
    def summary = [tests: 0, failures: 0, errors: 0, skipped: 0]
    def files = findFiles(glob: 'reports/*.xml')
    files.each { file ->
        def xml = readFile(file.path)
        def suites = new XmlSlurper().parseText(xml)
        if (suites.name() == 'testsuite') {
            summary.tests += (suites.@tests.text() ?: '0') as int
            summary.failures += (suites.@failures.text() ?: '0') as int
            summary.errors += (suites.@errors.text() ?: '0') as int
            summary.skipped += (suites.@skipped.text() ?: '0') as int
        } else {
            suites.testsuite.each { suite ->
                summary.tests += (suite.@tests.text() ?: '0') as int
                summary.failures += (suite.@failures.text() ?: '0') as int
                summary.errors += (suite.@errors.text() ?: '0') as int
                summary.skipped += (suite.@skipped.text() ?: '0') as int
            }
        }
    }
    return summary
}

Map readSmokeCollectSummary() {
    def summary = [total: '-', parallel: '-', serial: '-']
    if (!fileExists('reports/smoke-collect.txt')) {
        return summary
    }

    def text = readFile('reports/smoke-collect.txt')
    def totalMatch = text =~ /Collected test cases:\s*(\d+)/
    def parallelMatch = text =~ /Parallel pool cases:\s*(\d+)/
    def serialMatch = text =~ /Serial pool cases:\s*(\d+)/

    if (totalMatch.find()) {
        summary.total = totalMatch.group(1)
    }
    if (parallelMatch.find()) {
        summary.parallel = parallelMatch.group(1)
    }
    if (serialMatch.find()) {
        summary.serial = serialMatch.group(1)
    }
    return summary
}

String buildResultSummaryHtml(String status) {
    def junit = readJunitSummary()
    def smoke = readSmokeCollectSummary()
    def statusColor = status == 'FAILED' ? '#b00020' : status == 'UNSTABLE' ? '#b26a00' : '#137333'

    return """
    <div style="font-family: Arial, sans-serif; color: #222;">
      <h2 style="margin: 0 0 12px;">${env.JOB_NAME} #${env.BUILD_NUMBER} - ${status}</h2>
      <table style="border-collapse: collapse; min-width: 680px;">
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Status</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd; color: ${statusColor};"><b>${status}</b></td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Job</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${env.JOB_NAME}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Build</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">#${env.BUILD_NUMBER}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">JUnit</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            ${junit.tests} total, ${junit.failures} failed, ${junit.errors} errors, ${junit.skipped} skipped
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Smoke Collect</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">
            ${smoke.total} total, ${smoke.parallel} parallel, ${smoke.serial} serial
          </td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Parallel Workers</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${params.TEST_PARALLEL_WORKERS}</td>
        </tr>
        <tr>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">Real Smoke</td>
          <td style="padding: 6px 10px; border: 1px solid #ddd;">${params.RUN_REAL_SMOKE}</td>
        </tr>
      </table>

      <p>
        <a href="${env.BUILD_URL}">Build</a> |
        <a href="${env.BUILD_URL}console">Console</a> |
        <a href="${env.BUILD_URL}allure/">Allure</a> |
        <a href="${env.BUILD_URL}testReport/">JUnit</a>
      </p>
    </div>
    """
}
```

示例：

```groovy
void notifyByEmail(String status) {
    def summaryHtml = buildResultSummaryHtml(status)
    emailext(
        subject: "[${status}] ${env.JOB_NAME} #${env.BUILD_NUMBER}",
        mimeType: 'text/html',
        to: env.CI_MAIL_TO,
        body: """
        ${summaryHtml}
        <hr/>
        <h3>Build Parameters</h3>
        <p><b>RUN_FRAMEWORK_TESTS:</b> ${params.RUN_FRAMEWORK_TESTS}</p>
        <p><b>RUN_COLLECT_ONLY:</b> ${params.RUN_COLLECT_ONLY}</p>
        <p><b>RUN_REAL_SMOKE:</b> ${params.RUN_REAL_SMOKE}</p>
        <p><b>USE_CHINA_ENVIRONMENT:</b> ${params.USE_CHINA_ENVIRONMENT}</p>
        <p><b>SMOKE_TARGET:</b> ${params.SMOKE_TARGET}</p>
        <p><b>TEST_PARALLEL_WORKERS:</b> ${params.TEST_PARALLEL_WORKERS}</p>
        """
    )
}
```

### 9.3 post 阶段接入

```groovy
post {
    always {
        allure includeProperties: false, jdk: '', results: [[path: 'allure-results']]
        archiveArtifacts artifacts: 'allure-results/**, reports/**', allowEmptyArchive: true
    }
    failure {
        notifyByEmail('FAILED')
    }
    unstable {
        notifyByEmail('UNSTABLE')
    }
    success {
        script {
            if (currentBuild.previousBuild?.result in ['FAILURE', 'UNSTABLE']) {
                notifyByEmail('FIXED')
            }
        }
    }
}
```

## 10. 安全保护

邮件正文不得包含：

```text
Authorization
api_key
API_KEY
token
secret
password
.env
完整请求体
完整响应体
账号余额详情
```

如果后续需要附带日志，应先做脱敏处理。

初版不建议附加 console log。

## 11. 测试计划

### 11.1 Jenkins SMTP 测试

在 Jenkins 系统配置页发送测试邮件，确认发件链路可用。

### 11.2 失败通知测试

临时触发一个失败构建，例如传入不存在的测试目录：

```text
SMOKE_TARGET=module/not_exists
RUN_REAL_SMOKE=true
```

期望：

```text
构建失败
收到 FAILED 邮件
邮件中包含 Build URL、Console URL、Allure URL
邮件中不包含敏感信息
```

同时检查邮件中的运行结果摘要 HTML：

```text
Status 显示 FAILED
JUnit 汇总能展示 total/failures/errors/skipped
Smoke Collect 汇总能展示 total/parallel/serial
Parallel Workers 显示本次参数
Build/Console/Allure/JUnit 链接可点击
```

### 11.3 恢复通知测试

恢复正常参数再次构建：

```text
RUN_FRAMEWORK_TESTS=true
RUN_COLLECT_ONLY=true
RUN_REAL_SMOKE=false
```

期望：

```text
构建成功
如果上一轮失败，收到 FIXED 邮件
普通连续成功不发邮件
```

同时检查 FIXED 邮件中的运行结果摘要：

```text
Status 显示 FIXED
失败后的恢复构建能展示最新测试统计
邮件正文不包含完整 console log
```

### 11.4 不稳定通知测试

如果后续存在 flaky 或 xfail/xpass 导致 Jenkins 标记 unstable 的场景，再验证 `UNSTABLE` 邮件。

## 12. 实施顺序

建议按以下顺序执行：

1. 安装或确认 `Email Extension Plugin`。
2. 配置 Jenkins 全局 SMTP。
3. 发送 Jenkins 测试邮件。
4. 修改 Jenkinsfile，加入 `CI_MAIL_TO` 和 `notifyByEmail()`。
5. 接入 `failure/unstable/fixed` 通知。
6. 触发失败构建验证 FAILED 邮件。
7. 触发恢复成功构建验证 FIXED 邮件。
8. 确认普通成功不发邮件。

## 13. 验收标准

- Jenkins 能成功发送测试邮件。
- 构建失败时收到 FAILED 邮件。
- 失败后恢复成功时收到 FIXED 邮件。
- 普通成功构建不发送邮件。
- 邮件包含关键链接和参数摘要。
- 邮件包含运行结果摘要 HTML。
- HTML 摘要包含 JUnit 汇总、Smoke Collect 汇总、并发 worker 参数。
- HTML 摘要链接可以跳转到 Build、Console、Allure、JUnit 页面。
- 邮件不包含密钥、账号凭据、请求体、响应体等敏感信息。
- Jenkinsfile 中不出现 SMTP 密码或授权码。
