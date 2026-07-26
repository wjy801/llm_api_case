# 轻量 Mock 与故障模拟开发方案变更历史

## 2026-07-26

### 轻量 Mock 与故障模拟开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P2「轻量 Mock 与故障模拟」，在已有框架代码基础上输出开发方案。

变更内容：

- 新增 `dev/lightweight_mock_fault_simulation_design.md`
  - 梳理当前 `BaseRequest`、请求中间件、`RetryPolicy`、`PollingPolicy` 和 SSE 解析的 mock 切入点。
  - 明确第一版目标：使用 pytest `monkeypatch` 和 fake 对象沉淀可复用离线故障模拟能力。
  - 设计测试专用 `tests/mock_helpers.py`，不进入生产运行时代码。
  - 设计 `make_response()`、`SequenceTransport`、`RequestCall`、故障异常工厂、`SleepRecorder`、`FakeApiCallLogger`、`polling_responses()` 和 `FakeStreamResponse`。
  - 规划覆盖连接失败、超时、429、5xx、非法 JSON、字段类型错误、缺少请求 ID、轮询状态迁移和 SSE 故障。
  - 明确第一版不新增第三方依赖，不启动独立 Mock Server，不引入 YAML / JSON 场景 DSL。
  - 明确后续只有在 URL / method / body 匹配复杂化时再考虑 `requests-mock` 或 `responses`。
  - 补充单元测试设计、实施顺序、验证命令、风险处理和第一版完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档和本阶段独立变更历史，未改动运行时代码，未执行测试。
- 按最新要求，本次未追加 `code_history/CODE_CHANGE_HISTORY.md` 总历史。
