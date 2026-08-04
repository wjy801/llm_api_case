# 阶段 0：恢复 clean checkout 可信基线详细重构方案

> 版本：2026-08-04 V1
>
> 状态：已执行并通过隔离 clean checkout 验收
>
> 设计依据：`dev/当前框架代码过度设计审查_20260804.md`
>
> 总体实施基线：`dev/CI接口框架设计缺陷优先级评审_20260803.md`
>
> 阶段定位：只恢复可信基线、建立兼容合同，不提前实施 P0/P1 架构重构

## 1. 需求理解

阶段 0 不是重写框架，而是先回答一个基础问题：

> 在干净检出、没有本地临时文件和开发机缓存的情况下，这个仓库究竟包含哪些测试，能够稳定证明哪些行为？

本阶段必须解决：

1. `.gitignore` 测试白名单导致本地与 clean checkout 文件集合不一致；
2. 已跟踪、未跟踪、被忽略和用户工作区修改混在同一测试结果中；
3. Windows 自动换行导致带 SHA256 的报告夹具失真；
4. 当前离线测试结果不能作为后续 P0/P1 的可信回归基线；
5. 公共 API、CLI、类型身份、Schema、报告和产物路径尚未形成明确兼容清单；
6. 已知 P0/P1 缺陷缺少可重复的行为刻画入口。

## 2. 第一性原理与 TOC 约束

### 2.1 阶段 0 的本质

任何重构都需要一个可信测量系统：

```text
确定仓库真实文件集合
-> 确定 pytest 真实测试集合
-> 排除工作区和平台差异
-> 固化必须兼容的行为合同
-> 才能判断后续修改是否回归
```

如果基线本身不可信，增加更多测试、重构更多模块只会放大噪声。

### 2.2 当前 TOC 约束

```text
tests 白名单式忽略
-> 新测试可能只存在本地
-> 本地 pytest 与 clean checkout 执行集合不同
-> 本地失败无法判断是仓库缺陷还是个人文件
-> P0/P1 没有可信回归起点
```

当前首要约束不是 Runner、Quality 或 BaseRequest 的实现，而是“仓库测试集合没有唯一事实源”。

### 2.3 阶段 0 决策原则

1. 先确定文件归属，再修测试；
2. 先恢复跨平台基线，再记录数量；
3. 只修复测试、夹具和版本控制边界，不提前改生产架构；
4. 保留已确认的公共合同，已知错误行为不伪装成正确合同；
5. 每类变化独立提交，便于回滚和定位。

## 3. 已确认范围

### 3.1 必须保留

- Runner 统一先收集再执行；
- 串行、并行和未来分布式共享同一 nodeid 计划语义；
- `run_master.py`、`master_service.py` 和现有 CLI 入口；
- Request、Task、Assertions、Decorators 四件套强制创建规范；
- Quality、Metrics、Flaky、JUnit、Allure、Pipeline Summary 的现有公开入口与产物路径；
- `tests/` 作为离线框架回归目录，`module/` 作为业务用例目录。

### 3.2 不纳入阶段 0

- 不修改 pytest 退出码聚合算法；
- 不实现最终权威选择集合；
- 不重构 BaseRequest、BaseTask、Runtime Hooks 或 Quality Lifecycle；
- 不合并下载器、配置校验器或 Artifact 校验器；
- 不调整 Jenkins 阶段和报告展示；
- 不处理流式输出/SSE 用例和相关实现；
- 不处理密钥泄露、凭证轮换或 Secret 扫描；
- 不执行真实接口，不产生付费调用；
- 不建立 `runs/<run_id>`、插件平台或分布式执行器。

### 3.3 工作区保护

当前工作区包含用户已有修改和未跟踪文件。执行阶段 0 时必须：

- 不使用 `git reset --hard`、`git checkout --` 或批量清理；
- 不覆盖 `module/smoke/test_call_billing_correctness.py` 中用户删除流式账单用例的修改；
- 不批量执行 `git add tests module`；
- 不把 `module/test/`、响应样本或本地实验文件自动纳入仓库；
- 每个待纳管文件先审查内容和职责，再单独加入。

## 4. 当前审计快照

> 以下数据只描述 2026-08-04 当前工作区，不是永久数量契约。

### 4.1 Git 与测试文件边界

- `.gitignore` 使用 `/tests/*` 后逐文件放行；
- 已跟踪的根级测试即使匹配忽略规则仍会留在 Git，但新增文件默认可能被忽略；
- 当前发现以下未跟踪或被忽略文件：

| 文件 | 当前状态 | 阶段 0 默认处理 |
| --- | --- | --- |
| `tests/test_protocol_interception_cases.py` | 未跟踪、被显式放行 | 审查独有覆盖后决定纳管 |
| `tests/test_protocol_interception_payloads.py` | 未跟踪、被显式放行 | 审查独有覆盖后决定纳管 |
| `tests/test_protocol_request.py` | 未跟踪且被忽略 | 不恢复旧常量；重写到当前公共 builder 或删除本地副本 |
| `tests/test_smoke_billing_assertions.py` | 未跟踪且被忽略 | 与已跟踪账单测试比对，合并独有覆盖或删除重复文件 |
| `module/test/` | 未跟踪 | 默认视为本地实验目录，不自动纳管 |

### 4.2 当前测试结果

直接执行当前工作区 `python -m pytest tests -q` 会先被未跟踪且被忽略的 `tests/test_protocol_request.py` 阻断：它仍导入已不存在的 `PROTOCOL_PROBE_PROMPT`。这个错误不能直接认定为 clean checkout 缺陷。

只显式执行 Git 已跟踪测试文件时，当前工作区结果为：

```text
541 passed
10 failed
```

失败来源分为两类：

1. 九项 Pipeline Summary 测试：报告夹具清单中的 SHA256 与 Windows 工作区文件字节不一致；
2. 一项账单结构测试：用户已删除一个流式账单用例，结构测试仍固定断言存在 5 个查询点。

第二项属于用户工作区范围变更，不应通过恢复流式用例解决。

### 4.3 跨平台换行事实

以下夹具在 Git 索引中为 LF，在当前 Windows 工作区被转换为 CRLF：

```text
tests/quality/fixtures/pipeline_report_cleanup/merged/manifest.json
tests/quality/fixtures/pipeline_report_cleanup/merged/request-metrics.jsonl
tests/quality/fixtures/pipeline_report_cleanup/metrics/manifest.json
tests/quality/fixtures/pipeline_report_cleanup/metrics/run-metrics.json
```

`git ls-files --eol` 当前显示 `i/lf w/crlf`。Manifest 保存的是 LF 文件 SHA256，因此不能通过把清单改成当前 CRLF Hash 解决，否则会制造平台相关夹具。

### 4.4 当前业务收集

当前工作区 Smoke collect-only 为：

```text
总数 40
并行池 15
串行池 25
```

数量只作审计记录，不成为永久断言。当前集合中仍存在 `test_stream_chat_completions_chunk_fields`，与“流式输出不纳入本轮范围”的声明需要在范围冻结时确认归属；阶段 0 不为其增加或修改流式实现。

## 5. 阶段 0 交付物

阶段结束时应产生：

1. 修正后的 `.gitignore`；
2. 最小 `.gitattributes` 跨平台夹具规则；
3. 测试文件归属清单；
4. clean checkout 测试清单和环境记录；
5. 公共兼容合同清单；
6. 测试文件跟踪边界回归；
7. 已知 P0/P1 缺陷的行为刻画测试或测试骨架；
8. 全绿离线回归结果；
9. Smoke collect-only 集合守恒结果；
10. 独立 `code_history` 变更记录。

建议输出文件：

```text
dev/阶段0测试文件归属清单_20260804.md
dev/阶段0公共兼容合同清单_20260804.md
code_history/阶段0恢复clean_checkout可信基线代码变更记录_20260804.md
```

## 6. 工作包 0A：冻结输入和用户工作区边界

### 6.1 目标

保证后续结果可以明确区分：仓库基线、用户有意修改和本地实验文件。

### 6.2 执行动作

1. 记录当前分支、HEAD SHA、Python、pytest、pytest-xdist、Allure 插件和 Pydantic 版本；
2. 保存 `git status --short`，只作为审计输入，不自动处理文件；
3. 将用户已确认的范围变化与阶段 0 变更分开提交；
4. 对所有未跟踪测试生成归属表；
5. 明确流式输出不进入本轮改造，密钥泄露不进入本轮评审；
6. 确认四件套规范和统一预收集设计不变。

### 6.3 验收

- 每个当前修改/未跟踪文件都有 owner、用途和处理决定；
- 阶段 0 提交不夹带用户业务用例修改；
- 没有文件因自动清理或批量 Git 操作丢失。

## 7. 工作包 0B：修正测试版本控制边界

### 7.1 修改范围

修改 `.gitignore`：

- 删除 `/tests/*`；
- 删除 `!/tests/...` 逐文件放行；
- 保留 `__pycache__`、pytest 缓存、报告、下载文件和其他运行产物忽略规则；
- 不忽略任何 `tests/**/test_*.py`。

### 7.2 文件归属处理

删除白名单后，逐一处理新暴露文件：

- 有独立框架回归价值：修正后单独纳管；
- 与已跟踪测试重复：合并独有覆盖后删除本地副本；
- 本机实验：移动到仓库外或保持为明确忽略的本地实验目录；
- 运行数据：转换为脱离真实环境的固定 fixture，或不纳管。

`tests/test_protocol_request.py` 不应仅为通过旧测试而恢复 `PROTOCOL_PROBE_PROMPT`。如果决定纳管，应改用当前公开 payload builder 或测试内稳定合成值，并证明它提供现有测试没有的覆盖。

### 7.3 自动保护

新增仓库边界测试或校验脚本，验证：

```text
tests 下所有 test_*.py 均不被 gitignore 忽略
当前工作区被 pytest 收集的测试文件均受 Git 管理
新增 tests/test_example.py 默认可被 Git 发现
```

CI clean checkout 无法发现开发机上的未跟踪文件，因此本地检查和 CI 检查都要保留：本地负责发现未纳管文件，CI 负责证明仓库文件集合可复现。

### 7.4 验收

- `git check-ignore tests/test_example.py` 返回未忽略；
- `git ls-files tests` 覆盖所有确认纳入的框架测试；
- clean checkout 与当前已提交树收集到同一测试文件集合；
- 没有批量纳入本机实验目录。

## 8. 工作包 0C：修复跨平台报告夹具

### 8.1 根因

报告夹具使用文件字节 SHA256 作为信任边界，但 Git 在 Windows 上把 LF 转为 CRLF，导致清单与工作区文件天然不一致。

### 8.2 处理原则

- 不把 Manifest Hash 更新为 Windows CRLF Hash；
- 不降低或跳过 Hash 校验；
- 不在读取端偷偷统一换行后再计算 Hash；
- 固定被 Hash 保护的 fixture 文件字节表示。

### 8.3 修改动作

1. 新增最小 `.gitattributes`，至少对 `tests/quality/fixtures/**` 固定 `eol=lf`；
2. 刷新当前工作区夹具，使其与索引 LF 字节一致；
3. 使用 `git ls-files --eol` 验证 `i/lf w/lf`；
4. 重新计算 `request-metrics.jsonl` 和 `run-metrics.json` SHA256；
5. 只有在 LF 文件内容本身发生语义变化时才更新 Manifest；
6. 增加跨平台夹具 Hash 合同测试。

### 8.4 验收

- Pipeline Summary 清理夹具不再报告基础文件 Hash 不一致；
- 九项相关测试恢复通过；
- Windows 与 clean checkout 使用同一夹具字节；
- 没有关闭 Manifest 完整性校验。

## 9. 工作包 0D：处理当前测试漂移

### 9.1 账单结构测试

用户已经删除流式账单用例，不得为满足旧断言恢复该用例。

将 `tests/quality/test_smoke_billing_settlement.py` 从“必须正好存在 5 个赋值点”改为保护真实不变量：

```text
至少存在一个调用后余额查询
且所有 after_balance_response 均调用
query_account_balance_after_settlement_for_billing
```

这避免测试锁死业务用例数量，同时继续防止绕过结算等待。

### 9.2 本地协议测试

- `tests/test_protocol_request.py` 当前不是 Git 基线的一部分；
- 不把其缺失导入视为仓库生产代码必须恢复的 API；
- 若纳管，先改为当前公共 builder/Task 契约，再运行独立回归；
- 若覆盖已被其他 tracked 测试提供，删除本地副本；
- 不允许它继续以“被忽略但会被本地 pytest 收集”的状态存在。

### 9.3 两个协议拦截测试

对 `tests/test_protocol_interception_cases.py` 和 `tests/test_protocol_interception_payloads.py`：

1. 对比已跟踪协议测试的覆盖；
2. 验证完全离线、无真实等待；
3. 有独有覆盖则单独纳管；
4. 无独有覆盖则合并或删除；
5. 不因为 `.gitignore` 已放行就默认纳管。

### 9.4 验收

- 默认离线回归没有收集错误；
- 已确认纳入的测试全部通过；
- 本地实验文件不会影响默认 pytest 结果；
- 业务用例数量变化不再导致无关结构断言失败。

## 10. 工作包 0E：建立离线配置隔离

### 10.1 保持兼容

阶段 0 不改变以下入口及异常时机：

- `Settings`；
- 模块级 `settings`；
- `load_settings()`；
- 公开解析函数和异常类型；
- 真实业务入口现有配置行为。

### 10.2 离线测试规则

- 测试显式注入 Fake/Test Settings；
- 不依赖仓库根目录 `.env`；
- 不创建真实 Request Client 后再尝试阻止网络；
- 使用现有 Mock、SequenceTransport、fake clock 和临时目录；
- collect-only 不创建 run_id、Quality 产物或真实客户端；
- 测试结束恢复环境变量和 ContextVar。

### 10.3 验收

- 临时移除 `.env` 后，离线测试仍可收集和执行；
- 网络调用在测试环境中被显式阻断或替换；
- 没有真实 sleep 和付费调用；
- collect-only 前后无新增 Quality/Allure/JUnit 运行产物。

## 11. 工作包 0F：建立兼容合同清单

### 11.1 公共 API

记录并测试：

- `common`、`quality`、`pipeline_reporting` 的公共导出；
- BaseRequest/BaseTask/BaseAssertions/BaseDecorators 的公开签名；
- 四件套真实类、模块、MRO、继承和 `__init__.py` 导出；
- `run_master.py`、Quality CLI、Reporting CLI 的命令和退出码；
- 默认配置、开关及可观察的异常时机。

### 11.2 产物合同

记录：

- JUnit 路径和文件命名；
- Allure results、HTML、history 和附件路径；
- Pipeline Summary 路径与章节；
- Quality、Metrics、Flaky Schema 和机器产物路径；
- Jenkins 当前归档和邮件入口。

### 11.3 边界

- 不为流式输出增加专项合同；
- 不把测试数量写成永久合同；
- 不把内部文件数量和函数所在文件写成公共合同；
- 不把当前错误退出码行为声明为兼容要求。

### 11.4 验收

- 后续 P0/P1 每项修改都能映射到对应合同；
- 强兼容项和允许内部重构项有明确区分；
- 四件套规则与 `FRAMEWORK_TEST_SPEC.md` 一致。

## 12. 工作包 0G：增加行为刻画测试

### 12.1 已有正确行为

直接增加正常合同测试：

- collect-only 无副作用；
- 并串行分池集合互斥且并集等于总集合；
- nodeid 唯一；
- 公共入口可导入；
- 下载/附件失败不覆盖原始响应；
- BaseTask 旧入口和 skip 条件保持；
- 报告显式阶段失败优先于可解析 JUnit。

### 12.2 已知待修缺陷

对 P0/P1 尚未修复的行为使用显式、严格的临时预期失败：

```python
@pytest.mark.xfail(strict=True, reason="P0: authoritative selection plan")
```

适用场景：

- `-k`、`-m`、`--ignore` 导致预收集与正式执行集合不同；
- 计划非空但执行返回 exit 5；
- pytest 2/3/4 被压缩；
- Polling 请求、重试和 sleep 越过总预算；
- Header 临时修改在共享客户端并发时污染。

规则：

- xfail 必须标明目标阶段和明确原因；
- 不允许用普通 passing 测试把错误行为冻结成正确合同；
- P0/P1 修复完成后，XPASS 必须转为正常通过测试；
- 不为流式输出新增刻画测试。

### 12.3 验收

- 默认测试没有意外失败；
- 已知缺陷有可重复证据但不伪装成正确行为；
- P0/P1 修复后会由 strict XPASS 强制更新测试状态。

## 13. 工作包 0H：验证 clean checkout

### 13.1 验证环境

在阶段 0 变更形成提交后，使用新的临时目录完成本地 clone 或 Jenkins clean workspace 验证。不得用当前脏工作区结果代替。

记录：

- commit SHA；
- OS 与 Python minor；
- pytest、xdist、Allure、Pydantic 版本；
- 测试文件清单和 nodeid 清单；
- 并行池、串行池集合；
- 离线测试结果；
- collect-only 结果。

### 13.2 必跑命令

```powershell
python -m pytest tests -q
python run_master.py module/smoke --collect-only -q
```

如阶段 0 增加仓库边界测试，还应单独运行对应测试文件，以便快速定位失败。

### 13.3 集合验收

```text
总集合 = 并行池 ∪ 串行池
并行池 ∩ 串行池 = 空集
每个 nodeid 唯一
实际收集文件均受 Git 管理
```

数量只写入本次 `code_history` 审计记录，不写成长期硬编码断言。

## 14. 阶段 0 验收矩阵

| 维度 | 通过条件 | 证据 |
| --- | --- | --- |
| Git 边界 | 新增 `tests/test_example.py` 默认不被忽略 | `git check-ignore` |
| 文件归属 | 所有本地测试都有纳管/合并/删除/移出决定 | 归属清单 |
| clean checkout | 新目录检出后测试集合可复现 | SHA、文件清单、nodeid 清单 |
| 离线回归 | `python -m pytest tests -q` 无失败和收集错误 | pytest 输出 |
| 跨平台夹具 | Hash 夹具在 Windows 使用 LF 且 Manifest 校验通过 | `git ls-files --eol`、Hash 测试 |
| 业务收集 | collect-only 成功且集合守恒 | 总/并行/串行集合 |
| 无副作用 | collect-only 不调用真实接口、不创建 Quality run | 产物前后对比 |
| 配置隔离 | 离线回归不依赖 `.env` 和外部业务配置 | 临时环境测试 |
| 四件套 | 新模块结构规范保持 | 结构/类型合同测试 |
| 公共 API | 稳定入口、签名、类型身份可复核 | 兼容合同清单与测试 |
| 产物合同 | JUnit/Allure/Quality/摘要路径有记录 | 合同清单 |
| 已知缺陷 | P0/P1 缺陷有 strict xfail 或独立复现证据 | 刻画测试 |
| 范围 | 未实现 P0/P1，未处理 SSE 和密钥泄露 | diff 审查 |

## 15. 风险与控制

### 15.1 把本地文件误当仓库缺陷

控制：所有结论同时标注 tracked/untracked/ignored/modified 状态；最终以 clean checkout 为准。

### 15.2 批量纳管测试

控制：禁止 `git add tests module`；逐文件审查并独立暂存。

### 15.3 用更新 Hash 掩盖换行问题

控制：先固定 fixture 为 LF，再验证原 Manifest；不接受 Windows 专用 Hash。

### 15.4 为通过测试恢复已删除业务用例

控制：账单结构测试改测不变量，不恢复流式账单用例。

### 15.5 阶段 0 偷跑架构重构

控制：生产模块原则上不改；如发现必须修改，停止当前工作包并重新判定是否属于 P0/P1。

### 15.6 xfail 永久化

控制：每个 xfail 标注目标阶段；P0/P1 验收时扫描并清零对应 xfail。

## 16. 建议提交拆分

阶段 0 建议拆为以下独立提交：

1. `chore: 修正测试文件跟踪边界`
   - `.gitignore`
   - 测试文件归属清单
   - Git 边界测试
2. `test: 固定跨平台质量报告夹具`
   - `.gitattributes`
   - fixture EOL/Hash 合同测试
3. `test: 收敛本地测试归属与账单结构断言`
   - 协议测试归属处理
   - 账单结构不变量测试
4. `test: 建立阶段0兼容与缺陷刻画`
   - 公共合同测试
   - P0/P1 strict xfail
5. `docs: 记录clean checkout可信基线`
   - 兼容合同清单
   - README/FRAMEWORK_TEST_SPEC 基线说明
   - code_history 记录

禁止把以上提交与 Runner、Quality、BaseRequest 或 Jenkins 重构混合。

## 17. 回滚策略

- `.gitignore` 变更可独立回滚，不影响生产代码；
- `.gitattributes` 变更如引起异常大面积文本 diff，立即停止并缩小到 Hash fixture 目录；
- 测试归属决策逐文件回滚，不批量删除；
- 结构断言修改回滚时不得恢复用户已删除的流式业务用例；
- 兼容合同和刻画测试可独立回滚，但 P0 开始前必须重新建立等价证据；
- 任一工作包失败时保留前序已验收工作包，不通过硬重置撤销用户改动。

## 18. 阶段 0 完成定义

只有同时满足以下条件，才能进入 P0：

```text
clean checkout 测试文件集合唯一且全部受 Git 管理
离线回归全绿
跨平台报告夹具 Hash 可信
Smoke collect-only 集合守恒
离线测试不依赖外部业务配置和真实网络
公共 API、CLI、类型身份、Schema 和产物路径已有兼容清单
已知 P0/P1 缺陷有可重复证据
用户工作区改动未被覆盖或混入
```

阶段 0 完成后仍不代表执行事实问题已经修复；它只意味着后续 P0 的每个行为变化都可以被可靠测量。
