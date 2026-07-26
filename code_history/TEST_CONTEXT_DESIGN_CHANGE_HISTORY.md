# 测试上下文与变量传递开发方案变更历史

## 2026-07-26

### 测试上下文与变量传递开发方案

根据 `FRAMEWORK_CAPABILITY_ROADMAP.md` 中 P1「测试上下文与变量传递」，在当前代码基础上输出开发方案。

变更内容：

- 新增 `dev/test_context_design.md`
  - 梳理当前 `BaseTask.extract_task_id()`、`BaseTask.get_request_id_from_response()`、异步图片用例私有提取函数和并发计费用例手动变量传递的重复点。
  - 明确第一版目标：用例级 `TestContext`、变量读写、响应提取、类型校验、清理回调和并发隔离。
  - 明确第一版只支持 function / test-case scope，不做 class/session/global 上下文，不做跨用例持久化。
  - 设计 JSONPath、Header、Cookie、Regex 四类提取规则。
  - 设计 `extract()`、`extract_first()`、`get()`、`require()`、`set()`、`add_cleanup()`、`cleanup()` 等 API。
  - 设计变量缺失、类型不匹配、提取失败和清理失败异常。
  - 明确上下文不保存 API Key、Authorization、Cookie 等敏感值，错误摘要必须复用脱敏能力。
  - 明确 B 账号、zero 账号仍限定在具体用例中，不纳入全局测试上下文配置。
  - 规划 `common/test_context.py`、`tests/test_test_context.py` 和可选 pytest fixture 的职责边界。
  - 补充单元测试设计、实施顺序、验证命令、风险处理和第一版完成标准。

验证记录：

- 本次为开发方案输出，只新增设计文档和变更历史，未改动运行时代码，未执行测试。
