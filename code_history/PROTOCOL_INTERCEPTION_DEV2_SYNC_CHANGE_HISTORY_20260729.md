# dev2 协议拦截测试补齐与本地同步记录

## 目标

- 修复远程 `dev2` 缺少图片、视频协议拦截测试文件的问题。
- 保持协议测试文件与其请求入口、payload 构造和断言能力完整配套，避免只补测试文件导致导入或运行失败。
- 将当前 Git 可跟踪的本地配置、代码和 Jenkins 邮件参数统一提交到 `dev2`；本地生成报告不推送。

## 根因

图片和视频协议测试已经存在于 `master` 的协议演进提交中，但这些提交没有进入 `dev2`。本地对应目录只剩 Python 缓存，普通 `git add -A` 无法从缓存恢复正式源码。

此外，根目录 `.gitignore` 包含 `/tests`，导致若干未跟踪的旧测试草稿不会出现在普通 Git 状态中。这些草稿引用了当前不存在的旧接口，未纳入本次远程同步。

## 代码改动

- 将以下协议演进提交按原顺序移植到 `dev2`：
  - `22efaec`：补充图片协议请求入口、任务封装、payload 与断言边界。
  - `4fc823f`：修正文本协议测试矩阵边界。
  - `5e7c0a0`：补充图片和视频协议拦截 CSV、用例加载器与测试文件。
- 新增并跟踪：
  - `module/protocol_testing/image_model/`
  - `module/protocol_testing/video_model/`
- 补齐 `ProtocolRequest`、`ProtocolTask` 和 `payloads.py` 中图片、视频协议测试依赖的接口。
- `done_report/` 保留在本地并加入 `.gitignore`，不进入远程 Git 历史。
- 移除已不使用的 `flaky_retry_queues/` 忽略项，与当前课程和代码边界一致。
- Jenkinsfile 新增 `ALWAYS_SEND_REPORT_EMAIL` 参数；开启时，成功构建也发送 `SUCCESS` 报告邮件，关闭时维持只在恢复成功后发送 `FIXED` 的原行为。

## 验证

- 图片、视频协议测试执行 `--collect-only`：共收集 20 项。
- 当前 Git 已跟踪的协议框架单测：`22 passed`。
- 图片、视频 CSV 加载器分别读取 10 个 case。
- 图片、视频 payload 构造离线冒烟检查通过。
- 未执行真实协议接口请求。

## 未纳入范围

- `.env`、IDE 配置、Python 缓存、Allure 临时结果、本地完成报告和媒体测试数据继续按 `.gitignore` 管理。
- `tests/` 下 4 个未跟踪的旧协议草稿引用过期接口，当前会在收集阶段失败，因此不作为有效测试文件推送。
