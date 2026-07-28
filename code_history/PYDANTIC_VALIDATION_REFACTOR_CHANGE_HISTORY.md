# Pydantic 校验模型重构变更历史

## 变更时间

2026-07-26

## 变更范围

- `config.py`
- `util/config_validation.py`
- `common/retry.py`
- `common/polling.py`
- `requirements.txt`
- `tests/test_config_validation.py`
- `tests/test_retry_policy.py`
- `tests/test_polling_state_machine.py`
- `README.md`
- `FRAMEWORK_TEST_SPEC.md`

## 变更内容

1. 引入 `pydantic>=2.0.0` 作为结构化校验依赖。
2. 将 `Settings` 从 dataclass 改为 Pydantic frozen model。
3. 新增内部 `_EnvironmentSettingsInput`，用 Pydantic 承接 `.env` 原始字段校验，再输出公开 `Settings`。
4. 保留 `ConfigValidationError` 对外错误类型，并将 Pydantic 校验错误转换回原有聚合错误文案。
5. 将 `RetryPolicy`、`RetryAttemptRecord` 改为 Pydantic frozen model，保留原构造参数、默认值和校验语义。
6. 将 `PollingPolicy`、`PollingEvaluation`、`PollingTransition` 改为 Pydantic frozen model。
7. 为 `PollingTransition` 保留旧的位置参数构造兼容性。
8. 补充 Pydantic frozen model 回归测试，避免后续重新退回手写属性类。
9. 更新 README 与测试规范，声明配置、重试策略、轮询策略已使用 Pydantic 进行结构化校验。
10. 补充文档中的 Pydantic 模型边界和兼容性说明：
    - README 中补充已迁移模型清单、frozen 行为和错误兼容边界
    - `FRAMEWORK_TEST_SPEC.md` 中新增 Pydantic 校验模型规范章节
    - 明确纯内部记录结构和测试 helper 不强制迁移

## 兼容性说明

- `load_settings()` 仍返回 `Settings`，字段名和默认值保持不变。
- 配置错误仍通过 `ConfigValidationError` 暴露，并保留变量名。
- `RetryPolicy(...)`、`PollingPolicy(...)` 的公开构造方式保持兼容。
- `PollingTransition(1, 0.0, state, status, 200)` 的位置参数构造继续可用。
- Pydantic frozen model 用于防止策略/配置对象运行期被意外改写。

## 验证

- `.\.venv\Scripts\python.exe -m pytest tests/test_config_validation.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests/test_retry_policy.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests/test_polling_state_machine.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests/test_base_request_retry_polling.py -q`
