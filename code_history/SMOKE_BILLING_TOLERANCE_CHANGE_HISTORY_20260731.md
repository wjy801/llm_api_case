# Smoke 扣费断言容差变更记录

## 需求复述

针对 `module/smoke` 中涉及扣费的用例，原先通过调用前后账户余额差值与用量账单金额做精确比较。现在改为范围比较：实际余额扣减与账单用量金额的差值绝对值不超过 `0.01` 时，用例允许通过。

## 第一性原理分析

扣费断言真正要验证的是“账户余额扣减是否与账单记录表达的用量成本一致”。金额字段在服务端计算、账单落库、接口展示时可能存在小数精度、舍入或结算粒度差异，因此精确相等不是这个目标的必要条件。

因果链：

1. smoke 扣费用例先查调用前余额，再发起模型调用。
2. 用例根据模型响应 request id 查询用量账单。
3. 用例再查调用后余额，并计算 `before_balance - after_balance`。
4. 旧逻辑把实际余额扣减和账单用量金额按 `0.01` 精度量化后做相等比较。
5. 一旦接口返回值存在可接受的小额差异，断言就可能把正常计费误判为失败。

## TOC 约束判断

当前约束点不是用例流程，也不是查询接口调用顺序，而是 `SmokeAssertions` 中公共扣费比较策略。把容差集中放在公共断言层，可以一次覆盖文本、同步图片、异步图片以及并发调用的扣费校验，避免在多个测试文件中复制判断逻辑。

## 代码改动

- 修改 `module/smoke/assertions.py`
  - 将 `BILLING_COMPARE_PRECISION = Decimal("0.01")` 调整为 `BILLING_AMOUNT_TOLERANCE = Decimal("0.01")`。
  - `assert_call_billing_deduction_matches()` 从精确比较改为：
    - `abs(actual_deduction - usage_quota) <= Decimal("0.01")`
  - `assert_total_billing_deduction_matches_usage_quota_sum()` 从精确比较改为：
    - `abs(actual_deduction - usage_quota_sum) <= Decimal("0.01")`
  - 断言失败信息增加实际差值 `deduction_delta` 和允许容差说明。
  - 删除不再需要的 `ROUND_HALF_UP` 依赖和旧的 `_to_billing_compare_amount()`。

- 新增 `tests/test_smoke_billing_assertions.py`
  - 覆盖单次扣费差值在容差内通过。
  - 覆盖单次扣费精确相等通过。
  - 覆盖单次扣费差值等于 `0.01` 边界通过。
  - 覆盖单次扣费差值超过 `0.01` 失败。
  - 覆盖并发账单金额求和在容差内通过。
  - 覆盖并发账单金额求和超过容差失败。

## 未改动范围

- 未放宽 `assert_total_balance_unchanged()`。
  - 该断言用于失败调用“不应扣费”的场景，目标是验证余额不变。
  - 如果对失败调用余额不变也放宽到 `0.01`，可能掩盖真实小额误扣。

## 验证结果

已执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_smoke_billing_assertions.py -q
```

结果：

```text
6 passed
```

已执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

结果：

```text
41 tests collected
```
