# 测试上下文与变量传递第一版实现变更历史

## 2026-07-26

### 测试上下文与变量传递第一版实现

根据 `dev/test_context_design.md` 落地 P1「测试上下文与变量传递」第一版。

变更内容：

- 新增 `common/test_context.py`
  - 定义用例级 `TestContext`，不使用全局单例或共享状态。
  - 支持 `set()`、`get()`、`require()`、`has()`、`delete()`、`clear()`、`snapshot()`。
  - 支持 `extract()` 从响应 JSONPath、Header、Cookie、Regex 提取变量。
  - 支持 `extract_first()` 按候选来源顺序取第一个有效值。
  - 支持 `required`、`default`、`expected_type`、`transform`、`allow_none`、`multiple`。
  - 区分 JSONPath 命中 `null` 和路径未匹配；`None` 只有在 `allow_none=True` 时可保存。
  - 定义 `TestContextError`、`ContextVariableError`、`ContextVariableNotFound`、`ContextVariableTypeError`、`ContextExtractionError`、`ContextCleanupError`。
  - 实现 LIFO 清理回调栈，清理失败后继续执行剩余回调并聚合错误。
  - 错误中的响应摘要、异常文本和敏感变量值复用脱敏能力，避免泄露 API Key、Authorization、Cookie 等敏感信息。

- 更新 `common/__init__.py`
  - 导出 `TestContext` 及上下文相关异常。
  - 保持延迟导入模式，避免扩大 import 阶段副作用。

- 更新 `module/conftest.py`
  - 新增非 autouse、function scope 的 `test_context` fixture。
  - fixture teardown 阶段调用 `context.cleanup()`。
  - 不绑定业务账号，不读取额外全局配置，不隐式修改 `BaseRequest`。

- 新增 `tests/test_test_context.py`
  - 覆盖变量读写、缺失变量、非法变量名、类型校验和敏感值脱敏。
  - 覆盖 JSONPath、Header、Cookie、Regex 提取。
  - 覆盖 `extract_first()` 多候选兜底。
  - 覆盖默认值、非必填、类型转换、`allow_none` 和 `multiple`。
  - 覆盖清理回调 LIFO、幂等 cleanup、失败聚合和错误脱敏。
  - 覆盖多实例和多线程隔离。
  - 覆盖 `common` 模块导出和 `module.conftest.test_context` fixture teardown。

验证记录：

目标测试首次运行结果：

```text
1 failed, 27 passed, 1 warning
```

失败原因：

- `ContextVariableTypeError` 在变量名为 `api_key` 时没有按变量名脱敏实际值。
- pytest 尝试收集 `TestContext` 类并产生 collection warning。

修复内容：

- 类型错误格式化时按变量名执行敏感值脱敏。
- `TestContext.__test__ = False`，避免 pytest 误收集框架类。
- 补充 JSONPath `null` 与未匹配路径的语义区分，并增加 `allow_none=True` 覆盖。

修复后执行目标测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_test_context.py -q
```

结果：

```text
30 passed
```

执行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_task.py tests/test_base_request_retry_polling.py tests/test_config_validation.py -q
```

结果：

```text
42 passed
```

执行全量单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
158 passed, 1 skipped
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

- 本阶段未追加 `code_history/CODE_CHANGE_HISTORY.md` 总历史。
- B 账号、zero 账号仍限定在具体用例中，本阶段没有纳入全局上下文或全局配置。
