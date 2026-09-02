# Flaky 治理 MVP 阶段 3：看板一键触发、Probe Job 与恢复证据

## 1. 阶段结论

阶段 3 交付一条有界的恢复验证链：看板以唯一 POST 创建验证尝试，轻量 dispatcher 将不可变 Probe 计划投递到固定 Jenkins Job，受信控制端认领构建、校验目标工作区产物并导入签名证据，最终只产生 `READY_TO_CLOSE` 人工关闭资格或明确的失败/不确定终态。

本阶段接受 SQLite 与 Jenkins 无法原子提交的事实，投递语义为“可能至少一次”：一次性 token 和构建开始前的原子 claim 保证最多一个构建取得执行权，仅作用于 `effect_status=APPLIED` evidence 的 `(attempt_id, round_no)` 部分唯一约束保证每轮最多计数一次。实际治理 Skip 继续保持 `off/shadow`，不得在本阶段启用 `enforce`。

当前状态：`PROBE_VALIDATED / ENFORCE_NOT_AUTHORIZED`。阶段 2 继续保持 `SHADOW_VALIDATED / ENFORCE_NOT_AUTHORIZED`；阶段 3 代码、fake Jenkins 测试、容器化 GitLab/Jenkins 非生产真实演练和人工关闭门禁均已通过。真实演练期间未启用治理 Skip，完成后 Probe trigger 开关已恢复为关闭，正式最小间隔已恢复为 30 分钟。

## 2. 目标、范围与非目标

### 2.1 目标

- 只增加 `POST /api/v1/governances/{governance_id}/probe-attempts`，从活动治理记录创建一个固定目标版本的 verification attempt。
- 通过 request id、规范 payload hash 和事务内唯一约束吸收双击、刷新与网络重试。
- 使用持久 trigger ledger、dispatcher 和 reconciler 跨越数据库/Jenkins 双写边界。
- 新增独立 `Jenkinsfile.probe`；每轮单独申请受限 Agent，30 分钟等待期间不占 executor。
- 将控制代码、数据库、签名密钥与目标测试代码隔离，目标工作区只生成 P0 执行事实。
- 对每轮 P0 生成可验证的 evidence envelope，并严格按阶段 0 规则导入 Probe evidence。
- 覆盖 PASS、可信 FAIL、非计数、过期、构建丢失、取消和迟到结果的确定性编排。
- 达到 5 次计数 PASS 时只进入 `READY_TO_CLOSE`，继续由 CLI 人工关闭。

### 2.2 明确不做

- 不开启治理驱动的实际 Skip，不修改阶段 2 的 `WOULD_SKIP` 语义。
- 不提供通用 Jenkins 代理、任意 Job、任意分支/SHA、nodeid 或 pytest 参数入口。
- 不允许人工直接执行 `Build with Parameters` 绕过 trigger、claim 或 evidence 门禁。
- 不新增 Web 登录、用户身份识别、RBAC、多租户或审批流；唯一 Web 写入的 actor 固定为 `dashboard-anonymous`。
- 不在页面提供隔离、取消隔离、关闭治理或强制修正状态的按钮。
- 不自动关闭 governance，不把 Probe PASS 写成 NORMAL observation。
- 不引入 Celery、Redis、消息队列、分布式锁、HA dispatcher、通用工作流或自动扩缩容。
- 不支持未合入 `origin/dev3` 的个人 Commit，也不复用主 `Jenkinsfile` 的自由 `SMOKE_TARGET`。
- 不承诺抵御已获 Jenkins 管理权限或受保护分支写权限的恶意人员；MVP 只防止参数篡改、误投递、重放和工作区越权取密钥。

## 3. 前置门槛与现状约束

### 3.1 必须满足的门槛

1. 阶段 1 的 v3 Schema、单写者、Probe 准入和 attempt 重算已通过全部本地测试。
2. 阶段 2 已完成至少 10 个正式 Smoke Run 的 Shadow 核对，达到 `SHADOW_VALIDATED`。
3. 阶段 3 的真实演练只使用非生产数据库、非生产 API 凭据和专用 Jenkins Probe Job。
4. Jenkins 服务端已固定 HTTPS origin、证书信任、Job 全名和最小权限 service account。
5. 受保护 `origin/dev3` 可由服务端 fresh fetch；无法确认远端 HEAD 时拒绝创建 attempt。
6. `Jenkinsfile.probe` 的受信 controller commit 和文件 digest 已由人工登记；Job 不从目标分支加载 Pipeline 脚本。
7. `QUALITY_FLAKY_TRIGGER_ENABLE` 默认保持 `0`，只有演练窗口内显式开启。

真实 v2 数据审计若仍未完成，生产迁移继续 No-Go；这不阻止使用新建的非生产 v3 数据库完成阶段 3 演练。

### 3.2 当前 Jenkins 边界

现有 `Jenkinsfile` 顶层固定 Windows Agent、总 timeout 为 60 分钟，并允许调用者自由填写 `SMOKE_TARGET`。它不能满足最长 72 小时、轮次间释放 executor 和参数不可篡改三个条件，因此不得复用或条件分支改造；阶段 3 必须新增独立 Job 和 `Jenkinsfile.probe`。

## 4. 冻结的端到端链路

```text
Dashboard POST
  -> 事务创建 attempt + Probe plan + PENDING trigger + 全局容量槽
  -> dispatcher CAS 到 DISPATCHING
  -> 固定 Jenkins Job（可能收到重复投递）
  -> 受信 controller 在 checkout 前原子 claim
  -> 受限 target 执行精确 Case 并生成 P0
  -> controller 校验 P0、生成 HMAC evidence envelope
  -> Probe importer 幂等写 evidence 并重算 attempt
  -> 下一轮等待（无 executor）或结束编排
  -> reconciler 核对 Jenkins 终态
  -> READY_TO_CLOSE 后由人工 CLI close
```

不可跨越的边界：

- 浏览器不决定目标 Commit、测试选择器、环境、画像、Job 或执行次数。
- Jenkins 参数不是计划事实源；数据库中的不可变 Probe plan 才是事实源。
- target 不能访问治理数据库、dispatch token、Jenkins service account 或 envelope 签名密钥。
- Probe evidence 不进入 `flaky_normal_observation`，不触发 detection reprojection。
- trigger/build 完成不等于恢复成功；只有合格 evidence 决定 attempt 状态。

## 5. `0004` 最小 Schema

阶段 3 新增不可变 migration `0004_probe_dispatch`。它只补足外部投递和在途轮次所需结构，不重做阶段 1 的领域表。

| 表或改造 | 最小职责与约束 |
| --- | --- |
| `flaky_probe_plan` | 每个 attempt 恰好一条；保存规范 JSON、`plan_digest`、计划版本和创建时间；数据库 trigger 禁止 UPDATE/DELETE |
| `flaky_probe_trigger` | 扩充状态、dispatch attempt、token hash、固定 Job 身份、queue/build 数字 ID、错误码、CAS row version 和各状态时间；`request_id` 唯一 |
| `flaky_probe_round` | 保存 `(attempt_id, round_no)` 的授权、开始、导入或放弃状态及唯一 run_id；同一 attempt 同一 round 唯一 |
| `flaky_probe_capacity_slot` | 只允许 `slot_id=1` 的单行容量所有权；指向当前占槽 trigger，不使用自动过期 |
| `flaky_governance_event` | 追加 attempt 创建、trigger/取消/终态、READY 与人工 close 的 causal event，不修改既有事件 |

### 5.1 Probe plan

计划采用 `flaky-probe-plan.v1` 规范 JSON，至少冻结：

```text
attempt_id / governance_id / flaky_key
case_id / param_hash / environment / execution_profile / state_epoch
target_branch=dev3 / target_commit_sha / controller_commit_sha
policy_revision / probe_evidence_rule_version / fact_schema_version
required_consecutive_passes=5 / min_interval_minutes=30
max_attempt_age_hours=72 / max_non_counting_runs=3
max_dispatch_attempts=3 / max_orchestration_rounds=10
allowed_job_full_name
```

- 目标 SHA 只能由服务端 fresh fetch 后解析 `refs/remotes/origin/dev3` 得到，必须为 40 位小写十六进制。
- `max_dispatch_attempts` 和 `max_orchestration_rounds` 是防止无限外部调用的固定 MVP 预算；前者默认 3，后者默认 10，不改变“最多 3 次消耗配额的 NON_COUNTING”规则。
- 字段按键排序、UTF-8、无额外空白、拒绝 NaN，`plan_digest=sha256:<64 lowercase hex>`。
- plan 创建后不可变；策略变化只影响新 attempt，不能改写进行中的计划。

### 5.2 Trigger、round 与容量不变量

- 一个 attempt 恰好对应一个 trigger 和一个 plan。
- `request_id` 全局唯一；相同 request id 只能对应同一规范 payload hash。
- 数据库只保存 `sha256(dispatch_token)`；禁止保存、事件化或日志化 raw token。
- queue ID、build number 只保存数字身份；展示 URL 必须由固定 Jenkins origin 和 Job 全名生成。
- 一个 trigger 最多绑定一个取得 claim 的 Jenkins build；同一 build 的重复 claim 幂等成功，其他 build 被拒绝。
- 一个 attempt 同时最多一个 `AUTHORIZED/STARTED` round；IMPORTED/ABANDONED 后才可授权下一 round。
- 容量槽覆盖 `PENDING`、`DISPATCHING`、`QUEUED`、`RUNNING`、`DISPATCH_UNKNOWN` 和 `CANCEL_REQUESTED`。任何进入这些状态的事务必须同时持有 slot。
- `FAILED` 只有标记为“可安全重试”时可再次争抢 slot；抢不到就保持 FAILED，不绕过全局容量。
- slot 没有 TTL。进程失联或时间经过不能证明 Jenkins 未接收请求，禁止靠超时自动释放。

应用 `0004` 前必须不存在阶段 1 遗留的活动 attempt/PENDING 本地 trigger；发现时先通过阶段 1 命令显式取消。迁移不得猜测它们是否已经投递。

### 5.3 `flaky-db-check` 增量

数据库检查新增：

- plan digest 重算、attempt/plan/trigger 一对一及不可变 trigger 是否存在。
- 活动 transport 状态与 capacity slot 双向一致。
- RUNNING 必须有唯一 claimed build；QUEUED 不得伪造 build number。
- token 只能存在 hash，格式固定；终态或 token 轮换后的旧 hash 不得重新有效。
- round 序号、run id、状态时间和 evidence 引用一致，无两个在途 round。
- trigger、attempt 与 governance 状态组合合法；终态释放 slot，取消不确定时仍占槽。

## 6. 看板 POST、CSRF 与幂等

### 6.1 唯一写路由

```http
POST /api/v1/governances/{governance_id}/probe-attempts
Content-Type: application/json
Origin: <exact-dashboard-origin>
X-CSRF-Token: <signed-token>

{
  "reason": "...",
  "row_version": 7,
  "request_id": "uuid-v4"
}
```

成功创建返回 `201 Created`，只包含 attempt、trigger、target SHA、plan digest 和当前状态的安全 DTO。服务端不在 POST 请求内调用 Jenkins；外部调用只能发生在事务提交之后。

### 6.2 请求校验

- 请求体上限 4 KiB；只接受三个已知字段，拒绝额外字段。
- reason 经 Unicode NFC 和首尾空白规范化后长度为 1～500；控制字符直接拒绝，不静默删除。
- row version 必须为非负整数，request id 必须是规范 UUIDv4。
- governance 必须为 ACTIVE、属于阶段 2 已验证范围、row version 匹配且没有活动 attempt。
- trigger 开关必须开启，target fetch 必须成功，全局 slot 必须可在创建事务内取得。
- 非 ACTIVE、过期 row version 或活动 attempt 返回 `409`；容量已占用返回 `429`；开关关闭或目标 HEAD 无法验证返回 `503`。
- actor 固定为 `dashboard-anonymous`。截断后的来源 IP/User-Agent 只能标为 `untrusted_client_metadata`，不得展示为真实操作者。

### 6.3 CSRF 与最小 Web 边界

- 延续阶段 2 的 loopback/隔离管理网部署；管理网不是用户授权，页面必须持续显示匿名触发风险。
- 页面 GET 生成短期签名 CSRF token，将同一 token 写入不可缓存页面和 `HttpOnly; SameSite=Strict` Cookie；页面脚本从 DOM 读取 token 并放入 header。POST 同时校验 Cookie、header token、签名、时效和 exact Origin。
- 非 loopback 部署必须使用 HTTPS 并设置 Secure Cookie；HTTP 只允许明确的 loopback 开发演练。
- 拒绝缺失/null Origin、跨域请求、非 JSON 内容和浏览器简单表单提交。
- CSRF 只防跨站请求，不证明调用者身份；本阶段不把它包装成登录或权限控制。

### 6.4 request id 幂等

payload hash 由服务端对以下规范值计算：

```text
schema_version + governance_id + row_version + normalized_reason
```

处理顺序：

1. 完成语法、CSRF 和 Origin 校验后，先按 request id 查询已有结果。
2. request id 与 payload hash 均相同，直接返回原 attempt/trigger，不重新 fetch、不重新占槽。
3. request id 相同而 hash 不同，返回 `409 idempotency_conflict`。
4. 首次请求 fresh fetch `origin/dev3`，随后在单写者事务内再次检查 request id、governance CAS 和 slot。
5. 同一事务创建 attempt、plan、PENDING trigger，治理改为 RECOVERING，并追加事件。

目标 SHA 不进入浏览器 payload hash，因为它由服务端决定并已固化在首次请求结果中。分支在网络重试期间推进时，同 request id 仍返回原计划；新 HEAD 必须使用新的 request id 和新 attempt。

## 7. Trigger 状态机与 dispatcher

### 7.1 状态定义

| 状态 | 含义 | 是否占全局 slot |
| --- | --- | --- |
| PENDING | 本地事务已提交，尚未开始外部投递 | 是 |
| DISPATCHING | 一个 dispatcher 已 CAS 取得本次投递权 | 是 |
| QUEUED | Jenkins 明确返回固定 Job 的 queue ID | 是 |
| DISPATCH_UNKNOWN | 请求可能已接收，但响应不足以证明结果 | 是 |
| RUNNING | 某个 Jenkins build 已原子 claim | 是 |
| CANCEL_REQUESTED | 已要求取消，但外部终态或无有效执行尚未确认 | 是 |
| COMPLETED | 已确认 build 终态且本地轮次/evidence 已收敛 | 否 |
| FAILED | 明确未接收的可重试失败，或已收敛的终端失败 | 否 |
| CANCELLED | 已证明不会再产生有效执行，取消已收敛 | 否 |

`FAILED` 必须另存 `failure_disposition=RETRYABLE|TERMINAL`。只有 `RETRYABLE` 且能证明请求未被 Jenkins 接收时才允许再派发；到达投递预算后转为终端 FAILED，同时 attempt -> INCONCLUSIVE、governance -> ACTIVE。

Trigger 的 COMPLETED 只表示编排协议正常收敛，不表示测试通过；此时 attempt 可以是 READY_TO_CLOSE、FAILED、INCONCLUSIVE 或 EXPIRED。终端 FAILED 表示投递/构建协议未正常收敛，也不能被展示成 TRUSTED_FAIL。

### 7.2 允许的核心转换

```text
PENDING -> DISPATCHING
RETRYABLE FAILED -> DISPATCHING        # 重新取得 slot 后
DISPATCHING -> QUEUED
DISPATCHING -> DISPATCH_UNKNOWN
DISPATCHING -> RETRYABLE FAILED        # 仅明确未发送/明确拒绝
DISPATCH_UNKNOWN -> RETRYABLE FAILED   # 仅取得可审计的未接收证明
DISPATCHING|QUEUED|DISPATCH_UNKNOWN -> RUNNING
QUEUED|RUNNING|DISPATCH_UNKNOWN -> CANCEL_REQUESTED
PENDING|RETRYABLE FAILED -> CANCELLED
QUEUED -> TERMINAL FAILED              # 已证明 queue 非正常终止且无 build
RUNNING -> COMPLETED|FAILED
CANCEL_REQUESTED -> CANCELLED
```

- Dispatcher 在短事务中 CAS 到 DISPATCHING、增加 dispatch attempt、生成 128-bit CSPRNG token 并只存 hash；事务提交后才携带 raw token 调用 Jenkins。
- token 在每次“已证明未接收”的安全重试时轮换。旧 token 即使迟到到达，也无法通过 claim。
- raw token 只存在于当前 dispatcher 内存和 Jenkins masked password parameter 中；不得进入 URL、异常文本或结构化日志。
- dispatcher 结果写回必须使用 `WHERE state=DISPATCHING AND dispatch_attempt_no=?`。若 build 已抢先 claim 为 RUNNING/COMPLETED，响应处理只能补写已验证的 queue ID，绝不把状态回退为 QUEUED。
- HTTP 超时、连接在发送后中断、5xx 或无法验证响应来源均进入 DISPATCH_UNKNOWN。不能因为“通常没收到”而改成 FAILED。
- 只有本地校验失败、请求字节确定未发送，或 Jenkins 明确返回未排队的拒绝响应，才能成为 RETRYABLE FAILED。

### 7.3 固定 Jenkins client

- origin、Job full name 和 API path 来自服务端只读配置，不接受请求参数覆盖。
- 只允许 HTTPS、校验证书、拒绝跨 origin 重定向；响应 Location 必须同 origin、同固定 Job 且 queue ID 为十进制整数。
- 显式设置连接与总 deadline；MVP 默认连接 3 秒、单次总预算 10 秒，超限按是否能证明未发送分类。
- Jenkins 凭据来自权限受限的本地 secret/Jenkins credential，只授予固定 folder/job 的 Read/Build/Cancel；不授予 Configure、Delete 或 Script Console。
- Job 只接收 `TRIGGER_ID`、masked `DISPATCH_TOKEN` 和 `PLAN_DIGEST`。表单中出现其他参数即由 Job 自身拒绝。
- 日志按字段 allowlist 输出；token、Authorization、Cookie、CSRF、测试 API secret 永远替换为固定占位符。

## 8. 原子 claim 与最多一次有效执行

固定 Probe Job 的第一项外部副作用不是 checkout，而是由受信 controller 调用 claim 服务：

```text
claim(trigger_id, raw_dispatch_token, plan_digest,
      fixed_job_full_name, jenkins_build_number)
```

同一单写者事务内必须验证：

- trigger、attempt 和 governance 仍匹配且计划 digest 完全一致。
- raw token 的 hash 以常量时间比较等于当前 dispatch attempt 的 hash。
- Job full name 等于计划固定值，build number 为正整数。
- trigger 状态为 DISPATCHING、DISPATCH_UNKNOWN 或 QUEUED，且没有其他 claimed build。
- trigger 开关仍开启，attempt 未过期、未取消且仍允许开始执行。

成功后写 claimed build 并转为 RUNNING。相同 trigger/build/token 的重试幂等成功；不同 build、旧 token 或 `CANCEL_REQUESTED` 一律拒绝，并在 checkout 前正常退出且不产生 P0/evidence。

这允许 Jenkins 在 dispatcher 保存 queue ID 前启动：build 可以从 DISPATCHING 或 DISPATCH_UNKNOWN 直接进入 RUNNING。Dispatcher 的迟到响应使用 CAS，不能覆盖更靠后的状态。

`disableConcurrentBuilds(abortPrevious: false)` 只减少同一 Job 的并发，不是正确性边界。即使重复 queue item 先后启动，也只有一个 build 能取得数据库 claim。

## 9. Reconciler 与崩溃恢复

### 9.1 运行方式

- Dashboard 单进程内运行小型 dispatcher/reconciler 定时循环；Uvicorn 仍只允许一个 worker。
- 同时提供 `flaky-probe-dispatch-once` 和 `flaky-probe-reconcile-once`，供计划任务、测试和故障恢复复用同一 application service。
- 误启动多个循环时，SQLite 单写者和状态 CAS 决定唯一赢家；不依赖进程内 mutex 保证正确性。
- 每轮只扫描固定上限记录，所有 Jenkins HTTP 调用发生在数据库事务和 OS 写锁之外。

### 9.2 对账规则

- stale DISPATCHING 不能按时间直接回到 PENDING。先查询固定 Job 的当前 queue/build，并用 trigger id、plan digest、Job 身份和时间窗核对；仍无法确定时前进到 DISPATCH_UNKNOWN，而不是假定请求未发送。
- DISPATCH_UNKNOWN 找到匹配 queue/build 时补齐身份并前进；查不到时保持 UNKNOWN，除非 Jenkins 提供可审计证据证明请求未接收。
- 无法证明未接收的 UNKNOWN 禁止自动重试。人工取消也必须经过 `CANCEL_REQUESTED` 和最终核对，不能仅删除本地记录。
- QUEUED 对账 queue item；若转为 build，则等待 claim。queue 消失但没有 build/取消证据时不能猜成 CANCELLED。
- RUNNING 对账固定 build。build 已终止但回调缺失时，根据已持久化 rounds/evidence 确定 attempt；证据不足则 trigger -> FAILED、attempt -> INCONCLUSIVE、governance -> ACTIVE。
- 对账只允许前进或补充缺失外部 ID，不能从 RUNNING/终态回退到 QUEUED。
- Jenkins 不可达只记录安全错误码和下次检查时间，不修改领域结论。

MVP 不建设 Jenkins 侧通用 receipt ledger。极端情况下，响应丢失且 queue item 在启动前又被外部删除，可能长期保持 DISPATCH_UNKNOWN；这是已接受的人工处置风险，不能用重复投递掩盖。

## 10. 独立 `Jenkinsfile.probe` 与双工作区

### 10.1 Job 固定配置

`Jenkinsfile.probe` 必须满足：

- 顶层 `agent none`、`skipDefaultCheckout(true)`、`disableConcurrentBuilds(abortPrevious: false)`。
- 总 timeout 覆盖 72 小时 attempt 上限并留终态收尾时间，MVP 固定为 73 小时。
- 无 SCM/webhook/cron 自动触发，只允许固定 service account 调用。
- 参数只有 trigger id、masked dispatch token、plan digest；没有 target SHA、nodeid、环境、worker 数或 pytest 表达式。
- claim 成功后立即清除 raw token；后续 stage 和 target 环境不可读取。
- 每轮结束释放 Agent，在 Pipeline flyweight 上等待到下一允许时间；不得用占用 `node` 的 sleep。
- `post` 只做受信控制端收尾和清理，不能根据 Jenkins result 伪造 Probe PASS/FAIL。

### 10.2 可信 controller 边界

- Jenkins Pipeline 定义来自人工批准的 controller commit/file digest，不能从 `origin/dev3` 或待验证 target checkout 加载。
- controller 负责 claim、读取 plan、记录可信开始时间、校验 P0、签 envelope、导入 evidence 和状态收尾。
- controller 可访问数据库和 HMAC key，但这些 secret 只在对应受信 stage 的凭据作用域内出现。
- controller 调用 Git、pytest 和所有子进程都使用参数数组；禁止把 reason、nodeid、SHA 或路径拼接为 shell 字符串。
- controller 把 target 实际 detached checkout 的 SHA 作为可信运行事实登记；阶段 3 不接入通用部署平台，“实际部署 revision”仅指本次受控 Probe 工作区实际执行的该 SHA。

### 10.3 受限 target 边界

- target 在独立受限 Agent/OS 身份和独立 workspace 中运行，不只是同一用户下换一个目录。
- 由受信 Pipeline SCM step 在受限 Agent fresh fetch 并 detached checkout 计划中的 40 位 SHA，返回值由 controller 登记后才启动目标进程；不得让目标代码自行选择 revision 或跟随移动分支。
- 只注入该环境测试必需的最小 API 凭据，并限制其可访问目标、运行时长与资源；不注入数据库、Jenkins service account、dispatch token 或 HMAC key。
- collect 使用阶段 2 的统一 item identity，把 `case_id + param_hash` 唯一映射到一个 nodeid。零匹配、多匹配或身份不一致均产生 NON_COUNTING，不扩大到文件/类执行。
- 按计划复现 environment、execution profile、numprocesses/dist/serial 约束；无法复现时不执行近似画像。
- Probe 只绕过治理产生的 Skip mark；业务自身 skip/xfail/xpass 仍保留并分类为 NON_COUNTING。
- target 只生成版本化 P0 文件。controller 从固定交接目录读取、限制路径和大小、重算 hash，完成后清理 workspace。

受保护分支中的测试代码仍能伪造自身输出，因此 HMAC 不是对恶意 target 的远程证明。MVP 的信任前提是 `dev3` 受代码评审保护；controller 只负责防混用、篡改和重放。

## 11. Evidence envelope 与导入

### 11.1 Envelope 契约

controller 对每个已授权 round 生成 `flaky-probe-envelope.v2`。v2 在原始契约上增加环境与执行画像绑定；阶段 3 尚未进行真实演练，因此旧实现期的 v1 evidence 不迁移并统一 fail-closed。收到合法 P0 时绑定其 hashes；目标进程已结束但 P0 缺失、超限、路径非法或结构损坏时，生成 controller-origin 的不合格 envelope，并固定分类为消耗配额的 `NON_COUNTING/probe_evidence_untrusted`。只有 controller/build 在生成 envelope 前失联时，才由 reconciler 将 round 标为 ABANDONED 并使 attempt 进入 INCONCLUSIVE：

```text
schema_version / key_id
attempt_id / trigger_id / plan_digest / round_no / run_id
target_commit_sha / controller_commit_sha
environment / execution_profile
jenkins_origin_id / job_full_name / build_number
trusted_started_at / trusted_finished_at
p0_bundle_status / p0_manifest_sha256? / p0_file_hashes
signature = HMAC-SHA256(canonical_payload)
```

- 使用独立的 evidence HMAC secret；不得与 CSRF secret 或 Jenkins token 复用。
- 数据库只保存 key id、envelope、签名和 P0 hash 引用，不保存 HMAC secret。
- MVP 只支持一个活动 key；更换 key 时禁止存在活动 attempt，不实现在线多 key 轮换平台。
- target 不能签名。签名只证明 controller 观察到的计划、Job/build、时间和 P0 bundle 状态；当 P0 不合格时，envelope 必须携带稳定诊断码，不能伪造 manifest hash。

### 11.2 每轮协议

1. controller 调用 round authorize，原子检查 trigger RUNNING、开关、deadline、计划和总轮次预算，分配 round_no/run_id。
2. controller 记录可信开始时间后，才让 target 执行精确 Case。
3. target 返回 P0；controller 校验并生成 envelope。
4. Probe importer 先验 HMAC 与所有绑定字段，再调用阶段 1 的版本化 Probe 分类。
5. evidence 与 round=IMPORTED 在同一事务写入，并从全部 evidence 确定性重算 attempt。
6. 若还可继续，controller 在无 Agent 状态等待；否则停止创建新 round。

controller/build 崩溃留下 STARTED round 时，round 不可复用。reconciler 只有在 Jenkins 终态已确认且不可能再导入时才将其标为 ABANDONED，并同时使 attempt 进入 INCONCLUSIVE；不得补造 envelope 或继续积累 PASS。

### 11.3 结果编排

| 导入结果 | 编排动作 | attempt/governance |
| --- | --- | --- |
| COUNT_PASS，累计少于 5 | 等待距上次计数 PASS 满 30 分钟后下一轮 | ACTIVE / RECOVERING |
| COUNT_PASS，累计达到 5 | 停止新 round，等待 build/trigger 收敛 | READY_TO_CLOSE / RECOVERING |
| TRUSTED_FAIL | 立即停止后续 round | FAILED / ACTIVE |
| 消耗配额的 NON_COUNTING 未达 3 | 若总轮次和 72 小时预算允许则下一轮 | ACTIVE / RECOVERING |
| 消耗配额的 NON_COUNTING 达 3 | 立即停止 | INCONCLUSIVE / ACTIVE |
| 间隔不足、重复、越界或迟到 | 只审计；按计划和总预算决定是否继续 | 不直接推进 |
| 超过 72 小时 | 停止并收尾 | EXPIRED / ACTIVE |
| build 丢失/总轮次耗尽仍无结论 | 停止并收尾 | INCONCLUSIVE / ACTIVE |

PASS 进度继续按 `round_no ASC, trusted_started_at ASC, run_id ASC` 从全部 evidence 重算。任意 NORMAL PASS/FAIL、Jenkins 构建结果或控制台文本都不能改变该计数。

## 12. 关闭、取消与 kill switch

### 12.1 人工关闭

页面不新增 close 按钮。继续使用阶段 1 的 `flaky-recovery-close`，但 close 事务必须额外验证：

- attempt 为 READY_TO_CLOSE，governance 为 RECOVERING 且 row version 匹配。
- trigger 已是 COMPLETED，所有已授权 round 均为 IMPORTED，不存在 AUTHORIZED、STARTED 或 ABANDONED。
- 全部 evidence 在事务内重新验签、重算仍为 5 次合格连续 PASS。
- fresh fetch 的 `origin/dev3` HEAD、plan target SHA 与 controller 登记的 target 实际 checkout SHA 三者相等。
- 没有未收敛的 DISPATCH_UNKNOWN、CANCEL_REQUESTED 或在途 import。

成功时同一事务完成 attempt -> CLOSED、governance -> CLOSED、递增该 flaky key 的 detection generation 并追加事件。分支已推进时拒绝关闭，必须取消旧 attempt 后针对新 HEAD 创建新 attempt；ancestor 关系不足以关闭。

### 12.2 取消协议

- PENDING 或明确从未发送的 RETRYABLE FAILED 可在单事务内取消并释放 slot。
- DISPATCHING、QUEUED、DISPATCH_UNKNOWN 或 RUNNING 先转为 CANCEL_REQUESTED；同时使尚未 claim 的 token 失效，并在事务外调用固定 Jenkins cancel API。
- 外部取消成功响应也不立即假设 build 已终止；reconciler 确认 queue 消失且无可 claim build，或确认 claimed build 已终止后，才转 CANCELLED、attempt -> CANCELLED、governance -> ACTIVE 并释放 slot。
- 取消响应丢失、Jenkins 不可达或 build 仍在结束中时保持 CANCEL_REQUESTED 和 RECOVERING，不提前释放容量。
- 迟到的未 claim build 因 token 已失效在 checkout 前退出；迟到 evidence 只写 AUDIT_ONLY。
- 提供 `flaky-recovery-cancel` 的安全升级和专用 reconcile 命令，不提供“强制删 trigger/释放 slot”命令。

### 12.3 触发开关

```text
QUALITY_FLAKY_TRIGGER_ENABLE=0|1
```

- 默认 `0`；未知值一律视为关闭并告警。
- 关闭后隐藏/禁用按钮、拒绝新 POST，PENDING/RETRYABLE FAILED 不再派发。
- dispatcher 每次 HTTP 前、Job claim 和每轮 authorize 都重新检查。
- 对 QUEUED/RUNNING/DISPATCH_UNKNOWN，reconciler 发起与人工取消相同的 CANCEL_REQUESTED 流程；外部结果未确认前不释放治理或容量。
- 开关关闭与 HTTP 发送之间仍存在极小竞态；即使请求刚好入队，claim/round gate 仍阻止新的有效 target 执行。
- 本开关只控制新 Probe 副作用，不改变 Shadow/Skip 开关，也不自动关闭 governance。

## 13. 配置与秘密

```text
QUALITY_FLAKY_TRIGGER_ENABLE=0
QUALITY_FLAKY_JENKINS_ORIGIN=https://<fixed-origin>
QUALITY_FLAKY_JENKINS_JOB=<fixed-folder/job>
QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE=<local-protected-file>
QUALITY_FLAKY_DB_PATH=<host-fixed-shared-absolute-path>
QUALITY_FLAKY_CONTROLLER_ROOT=<approved-controller-checkout-absolute-path>
QUALITY_FLAKY_CONTROLLER_COMMIT=<40-hex-sha>
QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256=<64-hex>
QUALITY_FLAKY_TARGET_PYTHON=<restricted-agent-python-absolute-path>
QUALITY_FLAKY_CSRF_SECRET_FILE=<local-protected-file>
QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE=<local-protected-file>
QUALITY_FLAKY_EVIDENCE_KEY_ID=probe-evidence-key-v1
```

- 配置在服务启动时严格解析；origin/Job/controller 身份错误时 trigger 功能 fail-closed，只读看板仍可用。
- secret 文件必须位于仓库和 Jenkins artifact 目录之外并限制 OS ACL；不得写入 `.env.pipeline`、Git、数据库、报告或备份。
- Probe controller 只能从固定 `QUALITY_FLAKY_CONTROLLER_ROOT` 执行；共享 SQLite 路径必须位于该 checkout 之外，不能配置为 Jenkins file credential 临时副本。
- 受限 target 使用固定的仓库外 Python 解释器，且在目标进程环境中显式清除数据库、controller 根目录、dispatch token 和 evidence/Jenkins 凭据变量。
- 不建设 secret manager 或自动轮换平台；使用现有 Jenkins Credentials 与本地主机受限文件满足 MVP。
- 所有重试、扫描批量、请求长度、总轮次和时间预算都有固定上限，调用者不能通过 HTTP 覆盖。

## 14. 实施工作包

| 顺序 | 工作包 | 交付物 | 完成条件 |
| --- | --- | --- | --- |
| S3-01 | Schema 与纯状态机 | `0004`、plan/trigger/round/slot 模型、转换函数 | migration、约束、状态/slot 属性测试通过 |
| S3-02 | Web 写入口 | POST、CSRF、Origin、幂等 application service | 双击/重放/跨域/row version/容量测试通过 |
| S3-03 | Jenkins client | 固定 URL client、token 处理、dispatcher-once | 确定失败与 UNKNOWN 分类、无锁 HTTP 测试通过 |
| S3-04 | 对账与取消 | reconciler-once、CANCEL_REQUESTED、kill-switch 收敛 | 崩溃/响应丢失/queue/build 取消测试通过 |
| S3-05 | Probe Job | 独立 `Jenkinsfile.probe`、`flaky治理MVP阶段3JenkinsProbeJob配置清单.md` | 顶层 agent none、参数 allowlist、claim-before-checkout、controller/target 节点与 OS 身份隔离校验通过 |
| S3-06 | 双工作区 | controller/target 交接、精确 collect 与凭据隔离 | target 无法读取 DB/token/HMAC，零/多匹配不执行 |
| S3-07 | Evidence | envelope、HMAC、Probe import、round ledger | 篡改/重放/乱序/迟到与 NORMAL 隔离测试通过 |
| S3-08 | 编排与 close | 5 PASS、可信 FAIL、NON_COUNTING、人工 close | 全部状态在事务内一致，绝不自动 close |
| S3-09 | 非生产演练 | 成功、可信 FAIL、UNKNOWN、取消四条记录 | 证据链与看板/CLI/数据库一致 |

工作包按顺序合入；S3-09 完成前 `QUALITY_FLAKY_TRIGGER_ENABLE` 在默认部署配置中保持 `0`。阶段 4 的 enforce 分支不得混入任何工作包。

## 15. 测试与验收

### 15.1 自动化测试

- plan 规范化/digest、不可变约束、request id 同 payload 重放与异 payload 冲突。
- 同一 governance 双击、不同 governance 并发、过期 row version 和全局 slot 竞争。
- exact Origin、CSRF 双提交、Cookie 属性、恶意 reason、额外字段和请求体上限。
- 两个 dispatcher 同时扫描只有一个 CAS 成功；进程在 CAS 前、提交后、发送前、发送后和响应写回前崩溃。
- Jenkins 明确拒绝、连接前失败、请求后超时、5xx、非法重定向和伪造 Location 的分类。
- build 早于 queue ID 写回、重复 queue item、旧 token、新 token 和不同 build 的 claim 竞态。
- DISPATCH_UNKNOWN 不盲重试，reconcile 找到 queue/build 后单调前进。
- PENDING、DISPATCHING、QUEUED、RUNNING、DISPATCH_UNKNOWN、CANCEL_REQUESTED 全部占用容量。
- kill switch 在 POST、dispatch、claim、等待和每轮开始时关闭；运行中取消结果不确定时仍持有 slot。
- target 零匹配、多匹配、错误 SHA、错误画像、业务 skip/xfail、P0 缺失/损坏/超限和越界路径。
- envelope 任一字段/hash/signature 篡改、跨 attempt/build/round 重放均不计数。
- 5 次 PASS 的间隔边界、可信 FAIL、3 次消耗配额的 NON_COUNTING、总轮次和 72 小时边界。
- build 终态与最后一轮 import 乱序、controller 崩溃留下 STARTED round、迟到 evidence。
- Probe 前后对同一批 NORMAL history 重放，projection、transition ID 和 detected state 完全相同。
- close 时分支推进、实际 checkout SHA 不同、trigger 未终态、round 在途/ABANDONED 和并发迟到 FAIL 均拒绝。

外部系统测试使用受控 fake Jenkins；状态机测试使用注入时间，不以 sleep 制造竞态。

### 15.2 非生产真实演练

必须保存脱敏的 attempt/trigger/round/evidence/event ID 和 Jenkins queue/build 身份：

1. 5 次合格 PASS，跨度满足策略，进入 READY_TO_CLOSE；人工 close 后 generation 加一。
2. 已有计数 PASS 后出现可信测试 FAIL，attempt -> FAILED、governance -> ACTIVE。
3. Jenkins 已接收但 dispatcher 响应丢失，build 从 DISPATCH_UNKNOWN 取得 claim，且没有第二个有效执行。
4. 运行中关闭 trigger switch，进入 CANCEL_REQUESTED；只有 Jenkins 终态确认后才 CANCELLED 并回到 ACTIVE。

真实演练只允许非生产 API 凭据和数据库。不得为了完成验收在生产治理记录上制造失败或取消。

#### 15.2.1 2026-09-02 容器化环境演练记录

演练环境使用容器内 GitLab 与 Jenkins、独立的 `probe-controller` 和 `probe-target-restricted` 节点、仓库外非生产 SQLite 数据库以及最小权限 `probe-dispatcher`。target 没有宿主机目录挂载，无法读取 controller checkout、数据库、HMAC key 或 Jenkins service credential。以下标识均为脱敏后的持久审计标识：

| 场景 | 关键身份 | 结果 |
| --- | --- | --- |
| 5 次 PASS 与人工关闭 | attempt `attempt-v1-15ab5a230185f7857b890fc90b3f8ee4eb20c8508381a9d54a29b90658af11d8`；trigger `trigger-v1-821d446da255acc6173d61f7ac1de40e2dcf98a339894d2b705dcf3a99811268`；queue `293`；build `6` | 5 个 round 均为 `COUNT_PASS/APPLIED`，状态先到 `READY_TO_CLOSE`，trigger 为 `COMPLETED`；人工 close 后 governance 变为 `CLOSED`，detection generation 从 1 增加到 2 |
| 已有 PASS 后可信 FAIL | attempt `attempt-v1-92c6f74fa8c157b57e641ac981b239c63d6c715f4c122e523235635dc4d6d729`；trigger `trigger-v1-c3376b359711c5f36cf5cb3d5450c55aea96de34b7fddb80ab4ecb770a12a4fc`；queue `321`；build `7` | round 1 为 `COUNT_PASS/APPLIED`；等待期内将非生产 target API credential 替换为随机无效值，round 2 为 `TRUSTED_FAIL/APPLIED`；attempt 变为 `FAILED`、governance 回到 `ACTIVE`，随后恢复有效 credential |
| 响应丢失与重复 queue | attempt `attempt-v1-b6c690b81e73d9d8befbdd5949ada3626707043e7079a564ea7bdae0fed780c0`；trigger `trigger-v1-29a7ed44c68be9b3703f73401b1bde9d81713e6cc0a3dddea6899550c9082f2c`；queue `342`、`344`；build `9`、`10` | 首次请求已被 Jenkins 接受但响应被丢弃，本地先进入 `DISPATCH_UNKNOWN`；build 9 唯一 claim 并仅导入一条 APPLIED evidence，build 10 以 `probe_claim_not_allowed` 退出；dispatch attempt 仍为 1 |
| kill-switch 运行中取消 | attempt `attempt-v1-40a8bb392e2f67d3c4e8ad3bcd94a241880ed3d25aa8691cdf1860edfd7353d9`；trigger `trigger-v1-ab854796b10e951c9f2e59ad722b55f07ca302e07b46625182a61cbae276f7bb`；queue `274`；build `4` | 关闭 Dashboard 与 controller trigger switch 后，disabled-runtime reconcile 先写入 `CANCEL_REQUESTED`；只有 Jenkins build 进入终态后才收敛为 `CANCELLED` 并释放容量 |

为缩短非生产演练窗口，经明确授权将现场轮次间隔临时改为 5 分钟；5 次 PASS 的相邻 `started_at` 间隔均超过 5 分钟。正式代码、GitLab `dev3` 和 Jenkins Job 在验收后均恢复 30 分钟默认值；30 分钟边界继续由注入时间的状态机测试覆盖。一次 controller/target 版本未对齐的预备试跑被正确拒绝为 `NON_COUNTING/probe_p0_file_missing`，未误计 PASS；对齐到同一临时提交后才重新执行上述有效演练。

收尾检查结果：`flaky-db-check` 返回 schema v4、`issue_codes=[]`；`tests/quality` 为 `461 passed, 44 warnings`；数据库、受信/受限工作区、Jenkins build 3～10 控制台与归档产物的明文敏感值扫描为 0 命中；等待窗口内 controller 与 target 的普通 executor 占用均为 0。Windows controller agent 已更换节点身份并轮换连接 secret，新进程仅通过受限 secret 文件读取凭据，不再把明文 secret 放入命令行。

### 15.3 退出门槛

- 阶段 1、2 的全部门槛继续通过，Shadow 仍零身份扩大、零范围外候选。
- 一次有效点击只创建一个 attempt/plan/trigger；重放不会新增外部有效执行。
- 即使至少一次投递产生重复 queue item，也最多一个 build claim、每个 round 最多一条 APPLIED evidence。
- Jenkins 不可用或响应不确定时显示准确状态，DISPATCH_UNKNOWN 没有盲重试路径。
- raw dispatch token、HMAC key、Jenkins/API 凭据未出现在数据库、日志、产物或目标工作区。
- 成功、可信 FAIL、UNKNOWN 和取消四次非生产演练全部完成并可从看板追溯。
- 达到 PASS 门槛不会自动关闭；人工关闭前的全部复验门禁实际生效。
- 阶段 2 的实际治理 Skip 数仍为 0，配置要求 enforce 时仍拒绝/降级。

全部满足后阶段状态为 `PROBE_VALIDATED / ENFORCE_NOT_AUTHORIZED`。只完成代码和 fake Jenkins 测试时最多标记 `PROBE_READY / REAL_REHEARSAL_PENDING`。

## 16. 对抗式审计记录

| 发现 | 风险 | 修正或处置 |
| --- | --- | --- |
| SQLite 与 Jenkins 不能原子提交 | 崩溃或响应丢失会造成重复投递或未知状态 | 明确采用至少一次投递；外部调用在提交后，靠 trigger ledger、claim 和仅约束 APPLIED evidence 的部分唯一索引实现最多一次有效效果，同时保留迟到/重复审计记录 |
| 事务内生成 raw token 但数据库只存 hash | dispatcher 崩溃后无法恢复 token，PENDING 也无法投递 | token 改为每次 DISPATCHING CAS 时生成，只存 hash；安全重试轮换 token，旧投递无法 claim |
| DISPATCH_UNKNOWN 按超时重试 | 原请求可能已排队，造成两个有效构建 | UNKNOWN 保持占槽并先对账；无法证明未接收时禁止重发 |
| build 可在 queue ID 保存前启动 | dispatcher 的 QUEUED 写回可能覆盖 RUNNING | claim 允许从 DISPATCHING/UNKNOWN/QUEUED 前进；所有响应写回使用状态 CAS，只补 ID 不回退 |
| 多 dispatcher 或崩溃后直接回收 DISPATCHING | 两个进程可能并行投递 | CAS 决定单次赢家；stale 状态必须先查 Jenkins，时间本身不构成安全重试证据 |
| 只限制 QUEUED/RUNNING | PENDING 或 UNKNOWN 堆积仍能塞满 Jenkins/治理状态 | 单行 capacity slot 覆盖全部有外部副作用或不确定性的状态 |
| raw token 作为普通字符串参数 | Jenkins 参数页、进程参数或日志可能泄露并被重放 | masked password parameter、hash-only 存储、受保护输入、claim 后清除、全链路脱敏 |
| `disableConcurrentBuilds()` 被当作唯一去重 | 重复 queue item仍会依次启动 | 每个 build 在 checkout 前数据库 claim；未 claim 构建不执行目标代码 |
| 取消 Jenkins 与更新数据库分两步 | 取消响应丢失时提前回 ACTIVE，旧 build 仍可能产证据 | 增加 CANCEL_REQUESTED；外部终态未确认时维持 RECOVERING 和 slot，迟到证据只审计 |
| Probe Jenkinsfile 从 target 分支加载 | 待验证代码可窃取数据库和签名密钥或绕过 claim | Pipeline 定义固定在批准的 controller commit/digest，target 只在 claim 后由受限 Agent checkout |
| “双目录”被误认为安全隔离 | 同一 OS 身份可跨目录读取 controller secret | 要求独立受限 Agent/OS 身份和凭据作用域；只换 workspace 不算通过验收 |
| HMAC 被描述为目标代码可信证明 | 受保护分支代码仍可伪造自己的 P0 行为 | 明确信任前提；controller HMAC 只证明校验与绑定，不宣称远程执行可信 |
| P0 缺失或损坏时不生成 envelope | 基础设施失败会从证据链消失，无法执行非计数配额 | controller 为已结束目标进程生成不合格 envelope；controller/build 丢失则 ABANDONED 并使 attempt INCONCLUSIVE |
| READY attempt 仍允许 ABANDONED round | 未知结果可能在人工 close 后迟到成为可信 FAIL | ABANDONED 必然令 attempt INCONCLUSIVE；close 要求全部 round 已 IMPORTED |
| kill switch 只阻止新 POST | 已排队/运行 attempt 继续外部调用并长期 RECOVERING | dispatch、claim、每轮均复查；关闭后走 CANCEL_REQUESTED，对账确认后才回 ACTIVE |
| 只限制非计数配额，不限制总轮次 | 间隔不足/重复等不消耗配额的结果可造成无限调用 | plan 增加固定总轮次预算，耗尽时 INCONCLUSIVE |
| target SHA 由浏览器提交或只验 ancestor | 可验证任意历史/未执行代码并错误关闭 | 服务端 fresh fetch 固定 dev3 HEAD；close 再次要求 HEAD、计划 SHA 与 controller 登记的实际 checkout SHA 相等 |
| CSRF 被当成匿名用户权限 | 管理网内调用者仍可占用唯一 Probe 配额 | 页面明确匿名风险；CSRF 只防跨站，权限体系继续作为非目标 |
| 为可靠投递引入通用队列/HA | 超出单机 SQLite MVP，增加无法验证的组件 | 使用单进程轻量循环、once CLI、OS 锁和 CAS；不引入 Celery/Redis/分布式协调 |
| 阶段 3 顺手开启 enforce | 触发和执行决策同时变化，故障无法隔离 | 阶段 3 始终保持 Shadow；实际 Skip 仅可由阶段 4 独立门禁授权 |

审计后的剩余已接受风险只有两项：匿名管理网调用者可占用 Probe 配额；极端的“响应丢失且未启动 queue item 又被外部删除”可能需要人工处置 DISPATCH_UNKNOWN。两项都必须显式展示，不能以自动重试或虚构 actor 掩盖。
