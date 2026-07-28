# 轻量 Mock 与故障模拟第一版实现变更历史

## 2026-07-26

### 轻量 Mock 与故障模拟第一版实现

根据 `dev/lightweight_mock_fault_simulation_design.md` 落地 P2「轻量 Mock 与故障模拟」第一版。

变更内容：

- 新增 `tests/mock_helpers.py`
  - 新增 `make_response()`，统一构造真实 `requests.Response` 对象。
  - 新增 `RequestCall` 和 `SequenceTransport`，支持顺序返回响应或抛出异常，并记录请求调用。
  - 新增 `SleepRecorder`，记录 `time.sleep()` 调用，默认不真实等待。
  - 新增 `FakeApiCallLogger` 和 `create_fake_logger()`，记录 success、failure、retry records 和 polling transitions。
  - 新增连接失败、连接超时、读取超时、普通超时异常工厂。
  - 新增 `polling_responses()`，按状态序列生成轮询响应。
  - 新增 `FakeStreamResponse`，支持 `iter_lines()`、`close()`、`closed` 状态和中途断流异常。

- 新增 `tests/test_mock_helpers.py`
  - 覆盖 JSON/text response 构造。
  - 覆盖 `SequenceTransport` 响应序列、异常序列、调用记录和耗尽错误。
  - 覆盖异常工厂、`SleepRecorder`、`FakeApiCallLogger`、`polling_responses()`。
  - 覆盖 `FakeStreamResponse` 正常迭代和中途断流。

- 新增 `tests/test_stream_fault_simulation.py`
  - 离线覆盖 SSE 正常流解析。
  - 覆盖非法 JSON chunk。
  - 覆盖非 `data:` 行。
  - 覆盖缺失 `[DONE]`。
  - 覆盖中途断流时原异常继续抛出且响应被关闭。
  - 覆盖 `interrupt_stream_chat_completion()` 读取 request id 并关闭响应。

- 更新 `tests/test_base_request_retry_polling.py`
  - 将 503 后成功、Timeout 后成功、轮询成功迁移等场景迁移到 `SequenceTransport`、`SleepRecorder`、`polling_responses()` 和 `FakeApiCallLogger`。
  - 删除本文件内重复的 `DummyLogger`、`created_logger()` 和 `make_response()`。
  - 保留部分显式 fake，避免过度抽象影响测试可读性。

- 更新 `tests/test_base_request_middleware.py`
  - 复用 `make_response()`、`FakeApiCallLogger` 和 `create_fake_logger()`。
  - 删除本文件内重复的 `DummyLogger` 和 `make_response()`。

验证记录：

目标测试首次运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mock_helpers.py tests/test_stream_fault_simulation.py -q
```

结果：

```text
12 passed, 8 failed
```

失败原因：

- `ChunkedEncodingError` 错误地从 `requests.ChunkedEncodingError` 引用，实际应使用 `requests.exceptions.ChunkedEncodingError`。

修复后重新执行目标测试：

```text
20 passed
```

执行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_request_retry_polling.py tests/test_base_request_middleware.py tests/test_request_middleware.py tests/test_api_call_logger.py -q
```

结果：

```text
37 passed
```

执行全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
178 passed, 1 skipped
```

执行 smoke 收集：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
42 tests collected
```

说明：

- 本阶段只新增测试工具和测试用例，未修改生产请求主链路。
- 本阶段未新增第三方依赖，未启动独立 Mock Server。
- 本阶段未追加 `code_history/CODE_CHANGE_HISTORY.md` 总历史。
