# 第 26 课：从一次 LLM 调用走到长期治理

## 本课在事实链中的位置

第 25 课已经把 Runner、pytest/Worker、Runtime Hooks、P0 Aggregator、Semantic/Metrics 与 Flaky 的事实所有权分开：一个组件只能根据自己实际拥有并通过门禁的证据作结论，不能替相邻组件担保。本课不再增加框架组件，而是把前 25 课的结论放回同一次异步图像生成调用，验证整条链能否首尾闭合。

我们继续使用 Case C：

```text
case_id = module/smoke/test_图片生成异步调用.py::TestAsyncImageGeneration::test_f8_09_async_image_generation_task_succeeds_with_result
param_hash = 74234e98afe7498f
environment = overseas
execution_profile = serial
state_epoch = 1
```

本课把新一轮 Run 110 简称为 `R6`，把由上述五个可比维度形成的长期对象简称为 `K1`，把成功导入时产生的新观察简称为 `O110`。这些是讲解用简称：真实模型保存的是完整 `run_id`、`flaky-v1-<hash>` 和 `observation-v1-<hash>`。

先校准一个会影响全课的事实。仓库中的 Case C 确实调用 `create_and_poll_media_generation()`，但只传了 payload、`poll_interval` 和 `poll_timeout`。当前默认创建函数执行一次 POST，不接收 Retry 策略；组合入口的 `retry_policy` 只传给轮询 GET。因此，“POST 首次失败后重试”不能写成 Case C 当前已经启用的行为。本课为覆盖课程要求，将使用能力层已有 `create=` 注入点构造一个离线受控变体，并在每次推导中标明它不是当前标准业务链。

本课的产出不是一个笼统的“成功”结论，而是一套可复用的判断方法：面对下一次真实 Run，能够沿时间、身份和数据流三张视图，回答某个结论来自哪里、通过了什么门禁、在哪个范围内成立。

---

## 核心问题

> 怎样把一次异步 LLM 调用的业务结果、观察事实、归并可信度、语义指标和跨 Run 治理连接起来，同时避免把任意一层的结论越权解释成整条链都成功？

---

## 从一个具体现象开始

### 受控输入不是生产实测

本课固定一组可重复推导的输入。它描述框架在这些响应和故障条件下应怎样运行，不宣称外部媒体服务曾在生产环境中返回这些值。

```text
run_id        = image-smoke-110-20260831T010000Z-f6a7b8c9
execution_id  = serial-pool
worker_id     = master
case_id       = module/smoke/test_图片生成异步调用.py::TestAsyncImageGeneration::test_f8_09_async_image_generation_task_succeeds_with_result
param_hash    = 74234e98afe7498f
invocation_id = inv-a95ca2cecd3db070b9f59a8e

测试目标/选择条件 = 仅 Case C
numprocesses   = 2

Quality       = enabled
Semantic      = enabled
Metrics       = enabled
Flaky history = enabled
Flaky state   = enabled
数据库          = 可用
```

Runner 的权威收集结果只包含 Case C。文件级 `serial` marker 使它进入 `serial-pool`；没有 xdist Worker 时，插件把 `worker_id` 记为 `master`。无参数值按当前标识算法得到上面的 `param_hash`，再由 `run_id + case_id + param_hash` 得到本轮 `invocation_id`。

受控创建回调给 POST 显式传入 `RetryPolicy(max_attempts=2, base_delay=0.2, max_delay=0.2, jitter=False)`，并让两次尝试携带同一个 `Idempotency-Key`。固定的首次 503 响应不含有效 `Retry-After` 响应头，服务响应如下：

```text
POST attempt 1  → HTTP 503
等待 0.2 秒      → POST 方法资格、503 结果资格、剩余次数与局部时间预算均允许继续
POST attempt 2  → HTTP 202，task_id=job-110

GET poll 1      → HTTP 200，raw status=pending
等待 2 秒       → pending 属于等待态
GET poll 2      → HTTP 200，raw status=running
等待 2 秒       → running 也属于等待态
GET poll 3      → HTTP 200，raw status=succeeded，result.url 非空
停止             → succeeded 属于成功终态
```

这里同时给定一个受控外部前提：服务端把带同一键的两次 POST 视为同一逻辑创建，只返回 `job-110`。客户端源码能够证明请求头和重发资格，不能证明生产服务一定按该键去重。首次 503 也不能证明服务端当时一定没有创建任务。

最终响应只提供单数 `$.result.url`。Case C 的断言接受这个路径；默认 Polling 装饰器寻找的是复数 `$.result.urls`，所以本例不会触发额外的原生下载请求。这样，业务物理发送与 Quality 事件可以一一对齐。

### 正常基线的最终快照

在 Hook、上下文、分片、JUnit、哈希和数据库都正常的基线中，结果是：

```text
物理 HTTP 发送                  = 5
P0 RequestMetric               = 5
Semantic Request Group         = 4
Semantic Polling Session       = 1
Semantic ASYNC_TASK Operation  = 1

Case C call.final_status       = passed
serial-pool.raw_pytest_exit_code = 0
runner.final_exit_code         = 0
P0 status / integrity          = complete / complete
Semantic status / integrity    = complete / complete
Metrics status                 = aggregated
Flaky import                   = IMPORTED，新增 O110=pass
K1 current_state               = QUARANTINED
```

最后两行可以同时成立。`O110=pass` 是 Run 110 新增的执行观察；`QUARANTINED` 是 K1 上仍处于 ACTIVE 的人工治理投影。新一次通过不会自动关闭治理，治理状态也不会倒流改写本轮执行结果。

---

## 为什么原有解释不够

### 静态责任表没有展示变化发生的时点

只知道“P0 负责归并、Semantic 负责语义、Flaky 负责历史”还不够。若文件在 P0 manifest 提交前变化，它属于 P0 本轮输入；若在提交后变化，它是下游读取前的字节漂移。两个动作可能落在同一个文件上，却触发不同门禁。必须把事实放回时间轴。

### 同一个 Case 名称不能代替完整身份

Case C 的稳定定义跨 Run 不变，但 `invocation_id` 随 `run_id` 改变；Request Event、Group、Polling Session 和 Operation 又只属于本次 Invocation。另一方面，K1 故意跨 Run 稳定，却还要纳入参数、环境、执行画像和 epoch。若只写“Case C 成功”，既无法说明某条请求属于哪一轮，也无法说明为什么 O110 应与旧观察比较。

### 编排先后不等于单链依赖

质量收尾的调用顺序是 P0、Semantic、Metrics、Flaky history、Flaky state，但数据依赖不是 `P0 → Semantic → Metrics → Flaky`。Metrics 消费 P0 请求事实与 Semantic 事实；Flaky 则直接消费 P0 Case、Failure、Integrity 和 run.json。于是，只改 RequestMetric 可以让 Semantic/Metrics 拒绝，同时让 Flaky 仍能导入。若把执行顺序误画成证据依赖，这个结果就无法解释。

### “完成”“成功”“可信”“可计算”是不同判断

- HTTP 202 只说明创建请求得到一个 2xx 响应。
- Polling 成功终态说明受控状态机停止在 success。
- Case passed 说明测试断言通过。
- manifest `status=complete` 说明该组件完成了提交，不等于 `integrity_status=complete`。
- Metrics `aggregated` 说明其来源门禁通过并形成指标。
- Flaky `IMPORTED` 说明本轮 Case 生命周期进入历史，不等于 K1 已解除隔离。

因此，端到端解释必须保留每个谓词的主语，不能只留下一个脱离对象的“成功”。

---

## 核心概念

### 1. 端到端证据闭环（End-to-end Evidence Closure）

端到端证据闭环，是把一个最终结论完整追溯为：

```text
来源事实
→ 事实身份
→ 消费门禁
→ 派生结论
→ 成立前提
→ 禁止外推的范围
```

它解决的问题是“为什么这个结论在本轮成立”。它的范围可以小到“E1 是否可重试”，也可以大到“K1 为什么仍是 QUARANTINED”。它与调用链不同：调用链描述代码先调用谁；证据闭环还要求说明下游究竟读了哪些产物、拒绝了哪些输入，以及没有资格推导什么。

例如，“O110 可以导入”不能只引用 Case C passed。它还需要完整的 setup/call/teardown、finished Run、受支持环境、P0 manifest 与指定输出哈希、可接受的完整性问题以及可用数据库。少一项，最强结论可能变成 FAILED、NO_DATA 或排除，而不是 pass。

### 2. 单变量门禁追踪（Single-change Gate Tracing）

单变量门禁追踪，是从同一正常基线出发，每次只改变一个输入，然后顺序记录各层门禁的通过、降级、拒绝与旁路结果。它解决的是复杂故障中的因果归属。

本课会分别加入三项变化：额外混入 Run 109 记录、漏记首个 POST 的响应事实、P0 提交后改动 RequestMetric 输出。它们不能一开始就混在一起：更早的哈希门禁可能阻止后续读取，使另一个问题不再以原来的错误码出现。先分别推导，最后才能安全汇总叠加结果。

“时间视图、身份视图、数据流视图”是观察证据闭环的三个角度，不是仓库新增的数据模型：时间视图回答何时变化；身份视图回答事实属于谁；数据流视图回答谁生产、校验和消费。

---

## 完整运行过程

### 视图一：按时间排列的调用与状态变化

下图先画正常基线，再标出三条独立故障分支的注入点。`T` 值是受控顺序标记，不是线上耗时测量。

```text
时间    Runner / pytest          HTTP 与业务状态                 观察与质量状态
────    ───────────────          ──────────────                 ──────────────
T-1     尚未开始 Run 110         无新请求                        K1=QUARANTINED，governance=ACTIVE
T0      权威收集 Case C          无新请求                        建立 Run/Execution/Worker/Case/Invocation
        并分入 serial-pool                                       Runner 不读取 K1 状态

T1      进入 Case call           POST attempt 1 开始              Group G1 开始，configured_max_attempts=2
T2                              POST attempt 1 → 503              正常基线写 E1：failed、retryable=true
        继续条件：POST 有客户端资格 ∧ 503 可重试 ∧ 尚有次数 ∧ 可安排等待
T3                              等待 0.2 秒                       G1 累积 retry_wait
T4                              POST attempt 2 → 202/job-110      写 E2：success；G1 结束

T5                              建立 Polling deadline=D            Session P1 开始
T6                              GET 1 → 200/pending                写 E3；状态线进入 PENDING
T7                              poll sleep 2 秒                    仍未到终态
T8                              GET 2 → 200/running                写 E4；状态线仍为 PENDING
T9                              poll sleep 2 秒                    仍未到终态
T10                             GET 3 → 200/succeeded              写 E5；状态线进入 SUCCESS 并停止
T11     输出断言通过             返回 result.url                  P1、OP1 收尾；call=passed

T12     pytest 返回 raw=0        业务路径结束                     Worker 已写 Case/Request/Semantic 分片
T13     Runner final=0           无业务调用                        P0 merge → run.json → Semantic → Metrics
T14     无再次选例                无业务调用                        Flaky 导入 O110；ACTIVE 治理使 K1 仍为 Q

独立分支 A：在 T13 的 P0 扫描前，某个 shard 额外含一条 Run 109 记录。
独立分支 B：在 T2，E1 的 response capture 抛异常，业务响应仍交给 Retry。
独立分支 C：在 P0 manifest 提交后、Semantic 读取前，request-metrics.jsonl 被追加换行。
```

时间线上有三个不同预算。每个物理请求有自己的 request timeout；POST Retry 的 `max_attempts/max_elapsed` 只约束该 POST Group 是否继续；Polling 在创建成功以后才建立 `D = polling_started + 600s`，三轮 GET、GET 内可能发生的 Retry 和 poll sleep 共用这个 deadline。创建 POST 不在这个 600 秒窗口内，所以整次 `ASYNC_TASK` 没有自动获得统一 deadline。

T2 的 503 是“收到一个 HTTP 响应”，不是 transport exception。因此正常 E1 的 transport outcome 是 response，而 `status_code=503`、`business_status=failed`、`retryable=true` 分别表达 HTTP/业务和 Retry 判断。T6、T8 的 HTTP 都成功，但业务状态尚未终结，所以对应 RequestMetric 的 `business_status=unknown`；只有 T10 的成功终态让 Polling 停止。

### 视图二：按层级排列的身份与事实归属

```text
Run：image-smoke-110-20260831T010000Z-f6a7b8c9
└─ Execution：serial-pool
   └─ Worker：master
      └─ Case：稳定 case_id；param_hash=74234e98afe7498f
         └─ Invocation：inv-a95ca2cecd3db070b9f59a8e
            ├─ CaseResult：setup / call / teardown
            └─ Operation OP1：ASYNC_TASK / media_generation
               ├─ Request Group G1：POST，configured_max_attempts=2
               │  ├─ Request Event E1：attempt_index=1，503
               │  └─ Request Event E2：attempt_index=2，202
               └─ Polling Session P1：poll_count=3
                  ├─ Request Group G2 → Event E3：GET，pending
                  ├─ Request Group G3 → Event E4：GET，running
                  └─ Request Group G4 → Event E5：GET，succeeded

跨 Run 可比身份（不属于上面的父子树）：
case_id + param_hash + environment + execution_profile + state_epoch
└─ K1 = flaky-v1-<hash>
   ├─ O104=P  O105=F_A  O106=P  O107=F_A  O109=P
   └─ O110=P，仅在 Run 110 通过 Flaky 导入门禁后存在
```

Run、Execution、Worker、Case、Invocation 是当前事实归属的五级身份。图中的 `OP1/G1～G4/P1/E1～E5` 也只是教学短名；真实语义 ID 由框架生成。Operation、Group、Session、Event 是 Invocation 内的业务语义身份，它们不是第六到第九级运行身份。K1 则是为了跨 Run 比较而构造的另一条键：Run 110 改变了 Invocation，却不改变 K1 的五个比较维度。

若 Run 109 的一条记录混入 Run 110 shard，它会在最外层 `run_id` 门禁被识别为外来记录，不能挂到这棵 Run 110 身份树下。若 Hook 漏掉 E1，物理请求仍发生，但持久化身份树里 G1 只剩 attempt 2；这正是“现实发生过”与“观察系统拥有证据”之间的差别。

### 视图三：从原始事实到质量结论的数据流

```text
执行选择链（控制流）
目标/CLI → pytest 权威收集 → marker 分池 → pytest 执行 → raw exit code → Runner final exit code
                                                        ↑
                                  QUARANTINED 没有回连到这条链

事实生产链
pytest TestReport ──原始执行事实──→ cases-<execution>-<worker>.jsonl
BaseRequest + Hooks ──请求观察───→ requests-<execution>-<worker>.jsonl
Hook 捕获异常 ───────诊断事实───→ integrity-<execution>-<worker>.jsonl
Semantic Context ────语义原始事实→ semantic/shards/*.jsonl

校验与派生链
Case / Request / Integrity shards
        │  当前 Run、Schema、重复冲突、Case/JUnit 完整性
        ▼
P0 merged 四文件 + manifest + 源分片摘要 + output 摘要
        ├── RequestMetric + Semantic shards ──关系校验──→ Semantic merged
        │                                               │
        │       run.json + P0 RequestMetric + Semantic ─┴─指标派生──→ Metrics
        │
        └── run.json + P0 CaseResult / Failure / Integrity
                    ──历史准入──→ Flaky DB ──历史重放──→ detected_state
                                                └─治理覆盖──→ current_state

提交后改动点：P0 manifest ──[request-metrics.jsonl 字节被改变]──→ Semantic/Metrics 复验失败
```

图中最重要的是两条分支。Metrics 需要 RequestMetric 和 Semantic；Flaky 不经过 Metrics，也不读取 RequestMetric。`QUARANTINED` 位于 Flaky 的治理投影端，当前没有一条实现边把它接回 Runner 选例链。质量收尾又处于 Runner 的 `finally`，其异常被 fail-open 处理，因此这些派生状态不会覆盖 pytest 已经形成的原始退出码。

---

## 正常路径

### 第一步：先固定权威 Case 集合和五级身份

Runner 先用 pytest 收集一次权威 Case 集合，再按 marker 分池。由于 Case C 带文件级 `serial` 标记，本轮把它交给 `serial-pool`；在非 xdist 子进程中，Worker 是 `master`。插件在测试协议开始时建立 CaseContext，并让 setup、call、teardown 三份 CaseResult 和 JUnit properties 共用同一个 `case_id/invocation_id`。

这一步的输出是“本轮准备执行谁”与“后续事实应归到谁”。它尚未证明网络调用成功，也没有查询 K1 的治理状态。K1 在 T-1 已经是 QUARANTINED，但 Runner 的收集、分池和 pytest 参数构造没有消费这个字段，所以 Case C 仍被执行。

### 第二步：一个 POST Group 从失败响应走到成功响应

受控回调把非空 RetryPolicy、同一 Idempotency-Key 和 payload 交给 BaseRequest。方法资格通过后，BaseRequest 为整个逻辑 POST 建立 G1。第一次真实发送形成 E1：服务器返回 503，因此 transport 层有 response，HTTP 不成功，业务状态为 failed，且该响应满足 Retry 结果资格。

Retry 执行器还要检查 `attempt_index < max_attempts`，以及 0.2 秒等待能否落在 POST policy 的局部预算内。全部满足才执行等待并开始第二次发送。E2 返回 202 和 `job-110` 后，503 不再是 G1 的最终结果；G1 记录两次 attempt、一次实际等待、first status 503 与 final status 202。

这可以形成两个不同的正常指标：四个 Group 中只有 G1 重试，因此 Group 层 `retry_rate=1/4`；在“实际重试过的 Group”这个分母内，首次 HTTP/业务失败而最终成功，所以 HTTP retry rescue 与 business retry rescue 都是 `1/1`。不能用 `1/4` 代替 `1/1`，因为它们回答的问题和分母不同。

再用相同的五次 HTTP 发送做一次语义对照。本例是“两次 POST attempt + 三轮 GET”，所以得到四个 Group、一个 retried Group、三次 poll；若另一受控输入是“一次 POST + 四轮 GET”，物理发送仍为五次，却会得到五个 Group、零个 retried Group、四次 poll。请求总数相同，重试率和轮询次数不同，说明 Request Event 数不能替代 Group 与 Session 层的业务结构。

### 第三步：Polling 查询已有任务，而不是再次创建任务

创建成功并提取 `job-110` 后，Polling 才建立自己的 600 秒 deadline。每一轮 GET 是一个新的 Request Group，各只有一个 attempt。原始状态 `pending` 与 `running` 都映射到 PENDING，循环继续并各发生一次 2 秒 poll sleep；第三轮 `succeeded` 映射到 SUCCESS，状态机停止并返回该响应。

这三轮 GET 都是 HTTP 200，但前两轮的业务状态是 unknown，不能算作两次业务成功。Polling Session 的 `poll_count=3`，终态为 success；外层 Operation 的 outcome 是 success。最终 `result.url` 还让 E5 的 `media_count=1`，而响应没有给出的 input/output token 仍保持 `None/unknown`，不能补成 0。

### 第四步：Hooks 把五次发送变成可归属事实

每次发送前，Runtime Hook 为 RequestContext 放入新的 `request_event_id` 和计时起点；收到响应后，Quality adapter 构造 RequestMetric，先写 P0 request shard，再把同一个 metric 交给 Semantic Collector。Retry 的两个 context 都绑定 G1；三轮 Polling 分别绑定 G2、G3、G4，并同时关联 P1；所有组最终关联 OP1。

正常基线因此具有严格数量关系：五次物理发送对应五条 P0 RequestMetric；它们组成四个 Group；三个 GET Group组成一个 Session；四个 Group和一个 Session组成一个 `ASYNC_TASK` Operation。数量相等来自本例的标准入口、完整上下文和无 Hook 故障，不是对所有网络调用的普遍保证。

### 第五步：Worker 分片经过 P0 提交

Worker 只追加自己的 cases、requests 与 integrity 分片，Semantic Collector 追加自己的语义分片。测试结束后，P0 按当前 `run_id`、Schema 和去重键扫描事实，再用预期 Execution、预期 Case 数和 JUnit identity 核对 Case 生命周期。

正常输入没有 issue。P0 先原子写四份 merged JSONL，再对这些输出文件计算 SHA256，最后写 `manifest.status=complete`。此时 `integrity_status=complete`，两个 complete 恰好相同，但含义不同：前者是提交状态，后者是本次门禁汇总结果。

### 第六步：Semantic 与 Metrics 恢复业务含义

Semantic 读取自身 shards，并在读取 P0 RequestMetric 前检查 P0 manifest 的 `run_id/status` 和 request-metrics output hash。它随后核对 Event、Group、Session、Operation 的引用、身份和 attempt 顺序，写出四份 Semantic merged 文件与新 hashes。正常结果为 `status=complete, integrity_status=complete`。

Metrics 不因 Semantic 已经校验过就省略自己的门禁。它校验 run.json、P0 与 Semantic 的版本/提交/完整性、P0 RequestMetric hash、Semantic 对 P0 的证据引用、四份 Semantic output hash及各层关系，然后才生成 `run-metrics.json`。本例有一个 workload Operation，来源无降级原因，所以状态为 `aggregated`。

### 第七步：Case 历史进入治理投影

Flaky 走另一条分支。它读取 finished run.json、P0 manifest、CaseResult、Failure 和 IntegrityIssue，复验自己关心的三份 P0 output hash，并把 setup/call/teardown 折叠成一次 pass 候选。五个可比维度没有改变，因此新 observation 仍属于 K1；Run ID 改变使它获得新的 O110 身份。

导入 O110 后，系统重放 K1 的全部历史：

```text
O104=P → O105=F_A → O106=P → O107=F_A → O109=P → O110=P
```

自动检测结果与人工治理随后分层处理。K1 已有 ACTIVE governance，投影逻辑把 `current_state` 保持为 QUARANTINED，并保留 `detected_state=CONFIRMED`。要进入恢复判断，需要显式启动 recovery；一次新 pass 本身不会关闭治理。

### 正常结论的追溯单元

| 结论 | 来源事实 | 必须通过的门禁 | 不能推出 |
| --- | --- | --- | --- |
| 创建最终返回 202 | E2 响应 | POST Retry 资格、次数和等待预算 | 服务端天然幂等、首次 503 未创建对象 |
| 异步任务成功 | 三轮状态与 SUCCESS 终态 | Polling policy、共享 deadline | 每个 HTTP 200 都是业务成功 |
| Case C passed | pytest call report 与断言 | 测试完整执行 | 观察事实必然完整 |
| Metrics aggregated | P0 + Semantic 可信来源 | Metrics 自己的版本、哈希、完整性和关系门禁 | Flaky 一定导入 |
| O110=pass | 完整 P0 Case lifecycle | Flaky 自身准入与数据库事务 | K1 自动解除 QUARANTINED |

---

## 复杂路径

以下分支都从上节正常基线重新开始；它们不是先后修改同一份数据库。每次只改变一个主要条件，先确认该变化在哪道门禁产生影响，再在最后汇总叠加结果。

### 分支 A：额外混入一条 Run 109 记录

变化发生在 P0 扫描前：Run 110 的某个 request shard 除五条正确记录外，又多了一条结构合法的 Run 109 记录。

P0 先解析这一行是 JSON object，再比较 `run_id`。发现不等于 Run 110 后，它增加该 `source_shards[]` 项的 `foreign_run_records` 并跳过该行；不会把外来记录送进本轮模型校验、去重或 merged 输出，也不会仅因这一行生成 IntegrityIssue。源分片 SHA256 对完整物理文件计算，所以覆盖这条外来行；merged output SHA256 则覆盖过滤后的 Run 110 文件。这两个摘要属于不同对象和时点。

由于本轮五条 RequestMetric 和 Case 生命周期仍齐全，P0 可以保持 `status=complete, integrity_status=complete`，下游正常运行，O110 仍可导入。这个结论不能推广到“跨 Run 污染永不失败”：如果外来记录替换了本轮必需 Case，过滤后可能触发 `no_case_results` 或 `expected_case_count_mismatch`，P0 就会 failed。

### 分支 B：首个 POST 503 的观察事实缺失

变化点固定在 E1：`request_started` 已成功，503 响应也真实返回；标准 Quality adapter 调用 `request_metrics.record_response()` 时发生异常。中性 Runtime Hook 把观察异常隔离，adapter 尝试追加 WARN `request_capture_failed`。Retry 执行器仍拥有真实 503 响应，继续资格判断、等待和第二次 POST；业务最终成功，Case 和 pytest 仍可 passed/0。

观察链的结果不同：

```text
物理发送：E1, E2, E3, E4, E5                         共 5 次
P0 RequestMetric：E2, E3, E4, E5                    只有 4 条
G1 已观察 attempts：[2]                              attempt_count=1
G1 completeness：incomplete                          缺少连续的 attempt 1
OP1 outcome / completeness：success / incomplete     业务终态与证据完整性并存
```

P0 没有“期望请求数为 5”的门禁。它能够校验实际写下来的四条记录及 WARN，完成文件提交，因此 manifest `status=complete`；WARN 使 `integrity_status=degraded`。所有 hashes 都与这份缺一条记录的文件一致，说明哈希一致不能补回未写下来的事实。

Semantic Collector 收尾 G1 时看到索引 `[2]` 而不是从 1 开始的连续序列，把 Group 和 Operation 标成 incomplete。Semantic Aggregator 又用 P0 E2 复验该序列，产生 ERROR `attempt_index_sequence_invalid`，并为 Operation 产生 WARN `operation_incomplete`。它仍提交自己的输出和 hashes，因此是 `status=complete, integrity_status=failed`。

Metrics 允许读取 degraded 的 P0，但不接受 failed 的 Semantic，于是返回 `status=failed`，三个汇总计数为 0、`metrics=None`，并写 `write_status=failed` 且无 output hash 的 manifest。这里的 0 是失败结果包装中的“未生成汇总计数”，不能解释成观测到零次请求或零用量。

Flaky 不读取 Semantic/Metrics，却会读取 P0 IntegrityIssue。`request_capture_failed` 不在它的安全 WARN 白名单，因此导入以 `FAILED / blocking_integrity_warning` 停止，不新增 O110。随后 state stage 写 `flaky_history_import_not_ready`，不会用这个 Run 重新评估 K1；数据库中原有历史和 QUARANTINED 状态保持不变。

这个结论依赖具体故障点。若使用 Noop 而根本没有观察与诊断，或缺失发生在另一个写入阶段，留下的引用和错误码会不同；不能宣称任意漏记都一定由 attempt gap 发现。

### 分支 C：P0 提交后改动 RequestMetric 输出

变化点固定在 P0 已写完 manifest、Semantic 尚未读取时：只给 `merged/request-metrics.jsonl` 追加一个换行。JSONL 的有效记录没有增加，但 SHA256 对原始字节计算，因此文件实际摘要与 manifest 中保存的旧摘要不同。

P0 不会自动重跑，所以它留下的 manifest 仍显示 `status=complete, integrity_status=complete`。这不是“改动不存在”，而是 P0 对提交时点的陈述。Semantic 保存当前 P0 manifest hash，复算 RequestMetric 文件后产生 ERROR `p0_request_metrics_hash_mismatch`，并停止加载该文件中的事件。随后，四个 Group 引用的五个事件在其 P0 事件表中都不存在，因此还产生 `request_event_missing`。Semantic 仍能把已有语义 shards 和完整性问题写成四份输出并计算 hashes，最终是 `status=complete, integrity_status=failed`。

Metrics 也独立复验 P0 RequestMetric hash，并且这一步发生在读取 Semantic manifest 之前。它以 `p0_request_metrics_hash_mismatch` 返回 failed manifest，不生成新的 `run-metrics.json` payload。

Flaky 的结果不同。它不读取 RequestMetric，也不读取 Semantic/Metrics；它复验的是 P0 CaseResult、Failure 与 IntegrityIssue。只改 request-metrics 时，这些证据未变，所以 Flaky 仍可导入 O110，K1 仍因 ACTIVE governance 保持 QUARANTINED。若改的是 CaseResult、Failure 或 IntegrityIssue，Flaky 才会在自己的哈希门禁以 `artifact_hash_mismatch` 拒绝。

### `QUARANTINED` 为什么没有阻止上述任何执行

三个分支开始前，K1 都已是 QUARANTINED。Runner 的输入是目标、pytest 参数和权威收集结果；标准选例、分池与执行代码没有读取 Flaky state。因此 Case C 仍被收集和执行。只有显式增加“治理状态 → 选例策略”的实现边，QUARANTINED 才可能改变后续执行；当前仓库没有这条边。

### 三个单变量分支对照

| 变化 | P0 | Semantic | Metrics | Flaky | pytest/Runner |
| --- | --- | --- | --- | --- | --- |
| 无变化 | complete / complete | complete / complete | aggregated | 导入 O110；K1 仍 Q | 0 / 0 |
| 额外 Run 109 行 | complete / complete；foreign+1 | complete | aggregated | 仍导入 O110；K1 仍 Q | 0 / 0 |
| E1 response capture 失败 | complete / degraded；4 条 metric | complete / failed | failed，无 payload | `blocking_integrity_warning`；无 O110 | 仍可 0 / 0 |
| P0 后只改 request-metrics 字节 | 旧 manifest 仍 complete / complete | complete / failed | failed，无新 payload | 仍可导入 O110；K1 仍 Q | 仍为 0 / 0 |

表中的 Q 是 `QUARANTINED` 简写。每行都是独立分支，所以能看出：跨 Run 行由 P0 过滤；Hook 缺失通过 P0 IntegrityIssue 影响 Flaky；RequestMetric 改动只击中读取它的 Semantic/Metrics；任何一项都不回写 pytest 原始退出事实。

### 四项条件叠加时的最终快照

现在才把“已 QUARANTINED”“额外 Run 109 行”“E1 观察缺失”“P0 后追加换行”放进同一受控 Run：

```text
业务与执行：5 次物理发送，任务到 succeeded，Case passed，raw/final exit code 可为 0
P0：过滤外来行；仅 4 条 RequestMetric；status=complete，integrity_status=degraded
Semantic：先遇到 P0 request hash mismatch，不加载事件；status=complete，integrity_status=failed
Metrics：在自己的 P0 hash 门禁失败；status=failed，无新 payload
Flaky：因 P0 的 request_capture_failed WARN 被阻断；不新增 O110
State stage：flaky_history_import_not_ready；数据库中 K1 仍为 QUARANTINED
```

叠加后不能再说 Semantic 一定报告 `attempt_index_sequence_invalid`：RequestMetric 哈希门禁先阻止了事件加载，后续主要看到的是 hash mismatch 与缺失引用。Flaky 的阻断原因也不是 Semantic/Metrics 失败或 RequestMetric 被改动，而是它自己读取到的 P0 `request_capture_failed` WARN。最终快照只有在前述单变量推导完成后才不会混淆因果。

---

## 对应的框架实现

### 1. 当前 Case C 与受控 Retry 变体必须分开

Case C 当前调用没有传 RetryPolicy：

```python
result_response = self.smoke_task.create_and_poll_media_generation(
    self.smoke_request,
    self.smoke_task.build_async_image_generation_payload(),
    poll_interval=ASYNC_IMAGE_POLL_INTERVAL_SECONDS,
    poll_timeout=ASYNC_IMAGE_POLL_TIMEOUT_SECONDS,
)
```

能力层组合入口先调用可注入的 `create_call`，再把 `retry_policy` 传给 `poll_call`：

```python
create_call = create or self.create_media_generation
create_response = create_call(request_client, payload)
task_id = extract_call(create_response)
return poll_call(
    request_client,
    task_id,
    poll_interval=poll_interval,
    poll_timeout=poll_timeout,
    polling_policy=polling_policy,
    retry_policy=retry_policy,
)
```

而默认创建函数只是：

```python
return request_client.post(self.media_generations_path, json=payload)
```

这些片段来自 `module/smoke/test_图片生成异步调用.py:57-70`、`common/base_task.py:73-79,101-121` 与 `common/task_capabilities/media_generation.py:57-68,93-124`。真实调用还经过 BaseTask 包装层：它固定注入 `self.create_media_generation`、`self.extract_task_id` 和 `self.poll_media_generation_result`，并只把 `retry_policy` 交给 Polling。输入是当前 Case 参数；输出是创建响应再到最终 Polling 响应；异常沿业务调用抛回测试。关键边界是：组合方法存在 `retry_policy` 参数，不等于创建 POST 使用它。

本课受控变体使用同一个能力层 seam，调用方可以写成：

```python
post_policy = RetryPolicy(
    max_attempts=2,
    base_delay=0.2,
    max_delay=0.2,
    jitter=False,
)

def create_with_retry(client, payload):
    return client.post(
        "/v1/media/generations",
        json=payload,
        headers={"Idempotency-Key": invocation_id},
        retry_policy=post_policy,
    )

result_response = capability.create_and_poll_media_generation(
    request_client,
    payload,
    poll_interval=2,
    poll_timeout=600,
    retry_policy=None,
    create=create_with_retry,
)
```

这是基于现有注入点的教学调用代码，不是仓库当前 Case C 的源码。`retry_policy=None` 明确表示每轮 GET 不再嵌套 Retry，避免把 POST Retry 与 Polling GET Retry 混成同一机制。

### 2. POST 客户端资格是独立判断

`common/retry.py:90-103` 的核心判断是：

```python
if normalized_method in {name.upper() for name in policy.allowed_methods}:
    return True
if normalized_method != "POST":
    return False
if policy.allow_post:
    return True
headers = kwargs.get("headers") or {}
return policy.idempotency_header.lower() in {
    str(name).lower() for name in dict(headers).keys()
}
```

输入是 method、实际请求 kwargs 和 policy；输出只是“客户端是否允许进入多次 attempt”。本例由请求头分支返回 true，503 再通过结果资格，次数和局部时间判断再决定是否等待。该函数没有访问服务端存储，因此不可能提供服务端幂等保证。

### 3. Hook 异常为什么既不打断业务又留下警告

`common/runtime_hooks/lifecycle.py:168-181,260-271` 通过安全调用包装观察者；标准 adapter 在 `quality/runtime_adapter.py:135-148` 内再次隔离 RequestMetric 构造/写入异常：

```python
try:
    function(*args)
except Exception as error:
    collector = get_collector()
    if collector is None:
        return
    collector.capture_integrity(
        source="request_metrics",
        code="request_capture_failed",
        message=f"{type(error).__name__}: {error}",
        related_id=context.attributes.get(REQUEST_EVENT_ID_ATTR),
    )
```

输入是一次已经发生的请求上下文与响应；正常输出是 RequestMetric。异常时业务响应仍返回给 Retry/Polling，观察输出改成一条默认 WARN。若连 Collector 都不可用，函数直接返回，连这条诊断也可能没有；所以 fail-open 保证的是业务隔离，不是“总能留下失败记录”。

### 4. 外来 Run 过滤与两种哈希发生在不同位置

P0 扫描 `quality/aggregator.py:221-272` 时先对整个源文件求摘要，再逐行处理：

```python
stats = _SourceStats(path=path, kind=kind, sha256=_file_sha256(path))
...
if payload.get("run_id") != state.request.run_id:
    stats.foreign_run_records += 1
    continue
record = model.model_validate(payload)
```

外来行影响 source shard SHA256 和审计计数，但不进入本轮 record 集合。归并结束后，P0 对四份 merged 文件另算 output SHA256，再写 complete manifest。输入、时点和保护对象都不同，不能把二者统称为“同一个哈希”。

### 5. 下游必须复验提交后的实际字节

Semantic 在 `quality/semantic_aggregator.py:291-342` 读取 P0 manifest 后重新计算 RequestMetric 文件摘要：

```python
actual_hash = _file_sha256(requests_path)
state.p0_request_metrics_sha256 = actual_hash
expected_hash = (manifest.get("output_hashes") or {}).get("request-metrics")
if expected_hash != actual_hash:
    state.issue(
        severity=IssueSeverity.ERROR,
        source="semantic_aggregator",
        code="p0_request_metrics_hash_mismatch",
        message="P0 request metrics hash differs from manifest",
    )
    return
```

输入是 manifest 中的提交摘要和消费时文件的实际摘要；不等时，状态增加 ERROR，P0 events 不进入 Semantic 的关系表。函数随后仍可提交“本轮语义归并已经完成，但完整性失败”的产物，所以 `status=complete` 与 `integrity_status=failed` 不冲突。

Metrics 在 `quality/metrics/sources.py:62-110` 独立复验 P0 RequestMetric，并拒绝 failed 的 Semantic。来源异常由 `quality/metrics/service.py:92-118` 包装为 failed manifest，而不是伪造一份零指标结果。

### 6. Flaky 的输入边决定它会忽略什么

`quality/flaky_importer.py:227-267` 构造的必需输入只有：

```python
paths = {
    "run_record": output_dir / "run.json",
    "manifest": output_dir / "merged" / "manifest.json",
    "case_results": output_dir / "merged" / "case-results.jsonl",
    "failures": output_dir / "merged" / "failures.jsonl",
    "integrity_issues": output_dir / "merged" / "integrity-issues.jsonl",
}
```

它不列 RequestMetric、Semantic 或 Metrics。随后 `quality/flaky_importer.py:854-880` 拒绝 ERROR 和不在安全白名单内的 WARN；`request_capture_failed` 因此产生 `blocking_integrity_warning`。这解释了两个看似相反的结果：仅改 RequestMetric 时 Flaky 可继续；留下该 P0 WARN 时，即使 Case passed，Flaky 也拒绝导入。

### 7. 治理投影与执行入口没有回边

Runner 在 `run_orchestration/runner.py:34-74,99-193` 依据 pytest 收集结果分池并执行；Quality 收尾在 `finally` 中进行。Flaky 的 ACTIVE governance 由 `quality/flaky_store/projection.py:227-277` 把 `current_state` 保持为 QUARANTINED，但 Runner 没有读取这张表，也没有据此生成 deselect、skip 或 xfail。源码中存在治理命令和状态机，不等于执行器已经消费治理状态。

---

## 能够保证什么

在本课明确给定的输入和前提下，当前实现能够作出以下有限保证：

1. 受控回调显式传入合格 POST policy 与 Idempotency-Key 后，首次 503、剩余次数和等待预算允许时，客户端会执行第二次 POST；两次 attempt 属于同一个 Request Group。
2. Polling 的三轮 GET 查询同一个 `job-110`，状态按 `PENDING → PENDING → SUCCESS` 迁移，并在成功终态停止；三轮查询属于一个 Polling Session。
3. 标准 Hook 工作正常时，五次 BaseRequest 物理发送各生成一条可归属的 P0 RequestMetric，并由 Semantic 组成四 Group、一 Session、一 Operation。
4. P0 会把额外的异 Run 行排除在当前 merged 之外，同时在 source shard 统计中保留 `foreign_run_records` 证据。
5. 下游在消费前复验规定的 output SHA256；P0 提交后改变 RequestMetric 字节会被 Semantic 和 Metrics 各自拒绝。
6. 标准 adapter 的普通请求观察异常不会取代业务响应；若 P0 WARN 成功落盘，`request_capture_failed` 会使 Flaky 拒绝本轮历史导入。
7. Flaky 正常导入 O110 后，ACTIVE governance 仍把 K1 的 `current_state` 保持为 QUARANTINED；若导入被拒绝，既有 K1 记录不会被当作本轮失败观察重写。
8. pytest 的 raw exit code 先由 pytest 形成，Runner final exit code由池结果合成；后续质量结论不会把一个已经为 0 的原始结果改写成另一项事实。

这些保证分别属于客户端、状态机、观察器、归并器、消费者或治理存储。它们可以在同一 Run 中组合，但不能删除各自的主语和条件后升级为“系统保证一切正确”。

---

## 保证成立的前提

### 前提一：能力、启用、经过与外部履约逐级成立

```text
框架存在能力：BaseRequest 支持 POST Retry，能力层支持 create 注入
本课受控启用：create_with_retry 显式传 policy 与 Idempotency-Key
本次真实经过：固定调用确实从该 callback 进入 BaseRequest.post
外部服务履约：fixture 约定同键只形成 job-110；生产环境仍需独立证据
```

当前仓库 Case C 只满足“框架存在能力”，不满足后三项。若改回标准 Case C，并继续沿用本文 `pending → running → succeeded` 三轮固定响应，就应得到一次 POST 与三次无 Retry GET，而不是本课受控基线的五次发送；真实 GET 次数仍由服务状态序列和 Polling 终止条件决定。

### 前提二：身份和上下文完整传播

Quality/Semantic 必须已启用；插件必须成功建立 Run、Execution、Worker、Case 和 Invocation context；请求必须经过 BaseRequest 与绑定的标准 Hooks；语义 scope 必须在 Case 生命周期内正常收尾。否则“物理发送数 = RequestMetric 数”不成立，缺失必须保留为缺失或完整性问题。

Flaky 的可比性还要求 case_id、param_hash、environment、execution_profile 和 state_epoch 与旧历史一致。任一维度变化都可能产生另一个 key，不能仍叫 K1。

### 前提三：提交顺序和改动时点准确

P0 的 source hash、merged output hash和 manifest 各有明确生成时点。篡改分支必须发生在 P0 complete manifest 写完之后、Semantic/Metrics 读取之前；若 P0 随后重跑，manifest 会记录新摘要，结论就不同。正常路径则要求下游读到的字节与提交时相同。

### 前提四：各消费者只按自己的契约作判断

Semantic 需要 P0 RequestMetric；Metrics 需要 run、P0 和 Semantic；Flaky 需要 run 与 P0 Case/Failure/Integrity。三个消费者必须分别启用并执行自己的门禁。数据库必须可写，Run 必须 finished，Case lifecycle 必须能折叠，ACTIVE governance 必须仍开放，才能得到本课描述的 O110 与 QUARANTINED 结果。

### 前提五：旁路网络动作没有混入计数

本课用单数 `result.url`，没有触发默认复数结果下载。若响应含 `$.result.urls` 且 capture output 开启，装饰器会在 Polling 成功后使用原生 `requests.get()` 下载；这些发送不进入 BaseRequest/Hooks，因此“物理网络请求总数”和“五条 Quality Event”将不再相等，下载时间也不在 Polling deadline 内。

---

## 不能保证什么

下面把全书十二条边界落到 Run 110 的具体证据上。每一行同时给出可以成立的结论和禁止外推的范围。

| # | Run 110 中可以成立的结论 | 不能由此推出 |
| --- | --- | --- |
| 1 | BaseRequest 存在 POST Retry；本课受控 callback 显式启用并经过它 | 当前仓库 Case C 已启用该能力，或某次生产 Job 已走过该路径 |
| 2 | 同一 Idempotency-Key 让本例 POST 获得客户端重发资格 | 服务端一定幂等、首次 503 一定未产生任务、生产服务一定只创建一次 |
| 3 | 每次请求有 timeout，POST policy 有局部 `max_elapsed`，Polling 有从创建后开始的 600 秒 deadline | 三者自动合成覆盖整个 ASYNC_TASK 的统一硬 deadline |
| 4 | E1 capture 异常时业务 503 仍交给 Retry，最终 Case 可通过 | fail-open 同时保证五条观察事实完整；复杂分支实际只留下四条 metric |
| 5 | P0 能验证记录 Schema，也能为实际输出生成匹配摘要 | 被观察到的事实一定完整、来源一定正确、业务结论一定正确；缺失 E1 时摘要仍可匹配 |
| 6 | P0 能检查预期 Execution 是否有 Case shard及本轮 Invocation 数 | 它能发现每个 Worker 的所有 Case、Request、Integrity 分片都没有缺失 |
| 7 | 外来 Run 行参与源 shard SHA256；过滤后的 merged 文件另有 output SHA256 | 一个摘要可以同时代表两个阶段，或旧 output 摘要能保证文件以后从未改变 |
| 8 | 标准入口和上下文完整时，Semantic/Metrics 能恢复四层结构与指标 | 缺失 attempt、token 或来源失败可以填成 0；`metrics=None` 也不是零请求 |
| 9 | Flaky 直接从可信 P0 Case 历史形成 O110，不读取 Metrics | Metrics aggregated 能证明 Flaky 已导入；反过来，Metrics failed 也不会自动阻止 Flaky |
| 10 | 历史重放形成自动 detected_state，ACTIVE governance 形成 current_state，Runner 单独负责执行 | 自动检测、人工治理与实际执行是一个状态，或任一层能隐式替另一层决策 |
| 11 | K1 在 T-1 已是 QUARANTINED，Runner 在 T0 仍收集并执行 Case C | QUARANTINED 当前会自动 deselect、skip、xfail，或预测下一轮必然失败 |
| 12 | Case 断言通过可形成 pytest raw=0，Runner可合成为 final=0 | P0/Semantic/Metrics/Flaky 都成功；附属组件也无权覆盖已经形成的原始退出事实 |

还有四个必须保留为限制或未知的点：

- 本课固定响应只能证明在受控输入下的控制流，不能证明生产服务可用性、幂等实现或真实延迟。
- `foreign_run_records=1` 证明扫描到一条外来记录，不证明所有预期 Worker 都提交了所有类型的分片。
- `Operation outcome=success, completeness=incomplete` 只表示业务终态成功而观察覆盖不足；不能择一字段抹掉另一字段。
- K1 仍为 QUARANTINED 只描述当前数据库投影。是否继续隔离、何时开始 recovery，以及未来是否把治理状态接入调度，都需要显式人工或实现决策。

因此，整条链最强的结论始终是带限定语的：谁根据哪些事实、在哪个时点、通过哪些门禁、对哪个身份作出了什么判断。凡是缺少其中一项的“全局成功”或“全局失败”，都超出了当前证据。

---

## 与下一课的关系

第 26 课是本课程的最后一课，没有第 27 课需要预告。我们已经从一次受控异步调用出发，走过权威收集、POST Retry、Polling、Runtime Hooks、Worker 分片、P0 归并、Semantic/Metrics、Flaky 历史与治理，并用跨 Run、观察缺失和提交后改动检验了每道边界。

下一步不是记住一条固定的“成功流水线”，而是在每个真实 Run 中重复同一个闭环：

```text
新执行
→ 保存带身份的原始事实
→ 各消费者独立验证
→ 形成有限且可追溯的结论
→ 由人工治理决定是否改变长期状态
→ 若要影响下一次执行，再显式实现并审计那条控制边
```

当业务结果、质量完整性、指标状态和治理状态互不相同时，不需要强行把它们压成一个答案。把结论放回正确的时间、身份、数据来源和能力边界，正是这 26 课共同建立的长期治理方法。
