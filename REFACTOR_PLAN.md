# 大模型接口自动化测试框架重构计划

本文档从“大模型接口测试框架”的定位出发，按阶段规划重构路径。整体原则是先修稳定性和安全问题，再逐步抽象大模型异步任务、模型注册、用例数据化、产物评估和执行治理能力。

## 阶段 0：稳定性修补

### 目标

解决当前框架中会影响安全、稳定性和失败诊断的问题，为后续重构建立可靠基线。

### 改动范围

- `config.py`
  - 增加环境变量显式校验。
  - 缺少 `base_url`、`api_key`、`timeout` 等配置时，输出明确错误信息。
  - 避免 `os.getenv(...).rstrip()` / `.strip()` 在变量缺失时直接触发 `AttributeError`。
- `.env.example`
  - 与当前实际读取的环境变量保持一致。
  - 明确国内、海外环境切换方式。
- `util/api_call_logger.py`
  - 对请求头、请求体、URL query 中的敏感字段脱敏。
  - 至少覆盖 `Authorization`、`api_key`、`token`、`secret`、`password` 等字段。
- `common/base_request.py`
  - 修正 `poll_get` 成功判定。
  - `None`、空字符串、空列表、空 dict 不应被视为成功结果。
  - 明确 `success_json_path` 是否允许为空；不允许则收紧类型和校验，允许则定义对应轮询语义。
- 现有测试用例
  - 增加创建任务状态码断言。
  - 增加 `task_id` 存在性断言。
  - 增加最终响应关键字段断言。

### 验收标准

- `pytest --collect-only -q` 正常通过。
- Allure 报告中不泄露 API Key 或 token。
- 配置缺失时失败信息可直接定位缺失变量。
- 轮询不会把空结果误判为成功。
- 接口异常时能区分配置失败、创建失败、轮询失败和结果失败。

## 阶段 1：抽象大模型异步任务工作流

### 目标

将 `create + poll + download + attach` 从测试用例中抽出，形成框架核心能力。测试用例只表达要测试的场景，不直接处理任务生命周期细节。

### 建议新增结构

```text
core/
  workflow.py        # GenerationWorkflow，统一编排生成任务
  task_runner.py     # submit / poll / task lifecycle
  artifact.py        # 产物下载、识别、挂载、元信息提取
```

### 设计方向

将当前用例中的流程：

```python
response = steps.create_generation(request_client, payload)
task_id = response.json()["task_id"]
steps.poll_generation_result(request_client, task_id)
```

演进为：

```python
result = workflow.run_generation(payload)
assertions.assert_generation_succeeded(result)
```

`GenerationWorkflow` 统一负责：

- 创建任务。
- 提取 `task_id`。
- 轮询任务状态。
- 判断成功、失败、超时。
- 下载最终产物。
- 挂载 Allure 附件。
- 记录任务耗时、轮询次数、产物信息。

### 验收标准

- 图片和视频用例都走同一套工作流。
- 用例不再直接解析 `task_id`。
- 轮询、下载、Allure 挂载逻辑集中维护。
- 工作流返回统一结果对象，便于后续断言和报告扩展。

## 阶段 2：建立模型注册表

### 目标

减少 `image_model`、`video_model` 中重复的 request/task 代码，使新增同类模型时主要通过配置完成。

### 建议新增结构

```text
models/
  registry.py
  definitions.py
```

### 模型定义建议字段

```text
model_id
endpoint
task_path
modality
task_type
default_parameters
success_json_path
failure_json_path
poll_interval
poll_timeout
artifact_type
```

### 示例

```python
ModelSpec(
    model_id="wan2.7-image",
    modality="image",
    task_type="text_to_image",
    endpoint="/v1/media/generations",
    task_path="/v1/media/tasks/{task_id}",
    success_json_path="$.result.urls",
    failure_json_path="$.error",
    poll_interval=2,
    poll_timeout=600,
    artifact_type="image",
)
```

### 验收标准

- 新增同类型模型不需要复制一套 `request.py` / `task.py`。
- 不同模型的 timeout、JSONPath、产物类型由 registry 控制。
- 模型目录只保留必要的特化断言或场景用例。

## 阶段 3：用例数据化与参数化

### 目标

将 payload 和预期结果从测试函数中拆出。测试函数只负责加载 case、执行 workflow、调用 evaluator。

### 建议结构

```text
cases/
  image_generation/
    smoke.py
    boundary.py
    negative.py
  video_generation/
    smoke.py
    reference_media.py
    long_duration.py
```

### Case 定义建议字段

```text
case_id
model_id
scene
payload
expected
marks
timeout
```

### 测试函数示例

```python
@pytest.mark.parametrize("case", load_cases("image_generation/smoke"))
def test_generation(case):
    result = workflow.run(case)
    evaluator.evaluate(result, case.expected)
```

### 验收标准

- 用例函数数量减少，case 数量通过数据扩展。
- 支持 `smoke`、`regression`、`nightly`、`expensive`、`slow`、`negative` 等标记。
- 正向、反向、边界用例结构统一。
- payload 复用、审阅和批量调整更容易。

## 阶段 4：建设多层断言与产物评估

### 目标

从“接口跑通”升级为“大模型产物可用性验证”。大模型接口测试应同时覆盖接口契约、任务状态、产物可用性和基础质量规则。

### 断言分层

```text
ContractAssertions     # HTTP 状态、JSON schema、task_id、错误结构
TaskAssertions         # 状态流转、失败原因、耗时、轮询次数
ArtifactAssertions     # URL、content-type、文件大小、图片/视频可解析
QualityAssertions      # 黑屏、空图、分辨率、时长、帧数、OCR/语义相关性
```

### 优先实现的低成本规则

- 图片
  - 可被 PIL 打开。
  - 尺寸符合请求参数。
  - 文件大小不低于阈值。
  - 可选：检测纯色图、空白图。
- 视频
  - 可被 `ffprobe` 解析。
  - duration 与请求参数接近。
  - 分辨率符合请求参数。
  - 文件大小不低于阈值。
  - 可选：检测黑屏、空帧。
- URL
  - 可访问。
  - `content-type` 合法。
  - 下载文件扩展名和内容类型匹配。
- 结果数量
  - 与 `n` 或业务预期一致。

### 验收标准

- 最终结果不只是“有 URL”，而是“产物可访问、可解析、符合参数”。
- 失败能定位到接口、任务、下载、产物结构或产物质量中的具体层级。
- Allure 中展示每个评估项及其结果。

## 阶段 5：执行策略与成本治理

### 目标

让框架适合长期 CI、夜间回归和多人使用，避免慢用例、高成本用例、限流和网络波动影响日常验证。

### 建议新增结构

```text
execution/
  policy.py
  rate_limit.py
  retry.py
```

### 能力范围

- 并发上限。
- 429、5xx、网络错误重试。
- 指数退避或固定退避轮询策略。
- 单 case 超时。
- 按 marker 控制执行集。
- `expensive`、`slow` 默认不进入普通 CI。
- 记录创建耗时、轮询耗时、下载耗时、总耗时。

### pytest marker 建议

```ini
markers =
    smoke: 快速冒烟用例
    regression: 常规回归用例
    nightly: 夜间回归用例
    expensive: 高成本模型调用用例
    slow: 长耗时用例
    negative: 参数校验或错误路径用例
```

### 验收标准

- 日常 CI 只跑 smoke 或指定轻量集合。
- 夜间任务可跑 regression/nightly。
- expensive/slow 用例不会误进普通流水线。
- 报告能看到每个模型、每类场景的耗时分布和失败分类。

## 阶段 6：报告与追踪升级

### 目标

将 Allure 从请求响应附件集合升级为模型调用审计报告，支持失败定位、趋势分析和服务端协同排查。

### 每条用例建议输出

```text
环境信息
模型信息
case_id
scene
payload 摘要
request_id / trace_id / task_id
创建任务响应
轮询摘要
最终响应
产物附件
产物元信息
评估结果
耗时统计
失败分类
```

### 验收标准

- 失败报告可以直接用于定位服务端问题。
- 每条用例有稳定 `case_id`。
- 支持按模型、场景、失败类型查看趋势。
- 报告中能区分接口失败、任务失败、超时失败、下载失败、产物质量失败。

## 推荐落地顺序

1. 先完成阶段 0，解决安全和误判问题。
2. 再完成阶段 1，将大模型异步任务生命周期收口。
3. 接着完成阶段 2 和阶段 3，解决模型扩展和用例扩展问题。
4. 然后完成阶段 4，把框架能力从“跑接口”升级到“验证大模型输出”。
5. 最后完成阶段 5 和阶段 6，支撑 CI、回归、趋势分析和服务端协同排查。

## 最小可行重构目标

前三个阶段是最小可行重构范围：

- 配置和日志安全。
- 统一异步任务工作流。
- 模型注册表。

完成这三项后，框架边界会明显清晰，后续增加图像、视频、音频、多模态模型时，不需要继续复制目录和样板代码。
