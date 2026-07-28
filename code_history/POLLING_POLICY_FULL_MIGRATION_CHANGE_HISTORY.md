# 轮询状态机全面迁移变更历史

## 变更时间

2026-07-26

## 变更范围

- `common/polling.py`
- `common/base_request.py`
- `common/base_decorators.py`
- `common/base_task.py`
- `module/smoke/request.py`
- `tests/test_base_task.py`
- `tests/test_base_request_middleware.py`
- `tests/test_base_request_retry_polling.py`
- `README.md`
- `FRAMEWORK_TEST_SPEC.md`
- `TEST_CASE_GUIDE.md`

## 变更内容

1. `BaseRequest.poll_get()` 删除 `success_json_path` / `failure_json_path` 旧参数，必须显式传入 `polling_policy`。
2. `BaseRequest.poll_get()` 删除旧版 JSONPath 成功/失败判断分支，统一走 `PollingPolicy` 状态机。
3. 新增 `DEFAULT_MEDIA_POLLING_POLICY`，用于媒体生成类任务默认轮询。
4. `BaseTask.poll_media_generation_result()` 和 `BaseTask.create_and_poll_media_generation()` 默认传入 `DEFAULT_MEDIA_POLLING_POLICY`。
5. `module/smoke/request.py` 的轮询方法迁移为 `polling_policy` 参数。
6. `download_links_from_poll_get` 改为读取 `polling_policy.result_json_path` 提取模型结果链接。
7. 更新相关单测，覆盖直接调用 `poll_get()` 缺少 `polling_policy` 的失败行为。
8. 更新 README、测试规范和用例指南，删除旧参数说明。

## 兼容性说明

- 业务侧通过 `BaseTask.create_and_poll_media_generation()` 的默认调用方式不变。
- 直接调用 `BaseRequest.poll_get()` 的代码必须显式传入 `PollingPolicy`。
- 旧版 `success_json_path` / `failure_json_path` 已删除，不再作为兼容入口保留。

## 验证

- `.\.venv\Scripts\python.exe -m pytest tests/test_polling_state_machine.py -q`：14 passed
- `.\.venv\Scripts\python.exe -m pytest tests/test_base_request_retry_polling.py -q`：16 passed
- `.\.venv\Scripts\python.exe -m pytest tests/test_base_request_middleware.py -q`：14 passed
- `.\.venv\Scripts\python.exe -m pytest tests/test_base_task.py -q`：14 passed
- `.\.venv\Scripts\python.exe -m pytest tests/test_base_request_retry_polling.py tests/test_base_task.py tests/test_polling_state_machine.py -q`：44 passed
- `.\.venv\Scripts\python.exe -m pytest tests -q`：182 passed, 1 skipped
- `.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q`：42 tests collected
