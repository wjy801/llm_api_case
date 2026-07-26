# Jenkins 邮件通知接入变更历史

## 变更范围

- `Jenkinsfile`

## 变更内容

- 新增 CI 邮件通知收件人配置：
  - `CI_MAIL_TO=3239682586@qq.com`
  - `CI_MAIL_FROM=18617962759@163.com`
- 在构建准备阶段清理并重建 `reports` 目录，避免邮件摘要读取历史构建残留报告。
- `Collect Smoke Cases` 阶段将 `run_master.py --collect-only` 输出落盘到 `reports/smoke-collect.txt`，用于邮件中的 Smoke 收集摘要。
- 新增构建后邮件通知逻辑：
  - 构建失败发送 `FAILED` 通知。
  - 构建不稳定发送 `UNSTABLE` 通知。
  - 上一次失败或不稳定后，本次成功发送 `FIXED` 通知。
  - 普通连续成功不发送邮件。
- 新增 HTML 运行结果摘要：
  - 构建状态、Job、构建号、耗时、分支、提交号。
  - JUnit 汇总：total、failures、errors、skipped。
  - Smoke 收集汇总：total、parallel、serial。
  - 当前构建参数摘要。
  - Build、Console、Allure、JUnit 入口链接。
- 新增安全处理：
  - 邮件正文只包含聚合摘要和跳转链接，不附带 console 原文、`.env`、请求体、响应体、账号凭据或 API Key。
  - SMTP 授权码不写入 `Jenkinsfile`，仍由 Jenkins 全局邮件配置管理。
  - 邮件发送异常只记录错误消息，不影响原始构建结果。

## 验证结果

- 已确认 Jenkins `Email Extension Plugin` 已安装并启用。
- 已通过 Jenkins Pipeline 校验接口验证：
  - `Jenkinsfile successfully validated.`

## 后续前置条件

- Jenkins 全局 `Extended E-mail Notification` 需要配置 SMTP：
  - SMTP server: `smtp.163.com`
  - SMTP username: `18617962759@163.com`
  - SMTP password: 163 邮箱 SMTP 授权码
  - 默认内容类型建议设置为 HTML
- Jenkins 系统配置页需先发送测试邮件，确认 SMTP 链路可用。
- Jenkins Job 使用 SCM Jenkinsfile，因此本次变更需要提交并推送到 `dev2` 后才会被流水线实际使用。
