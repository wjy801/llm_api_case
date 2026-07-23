# 框架目录结构与测试用例编写规范

## 目录结构

```text
D:\API_CASE
├─ common/                 # 通用基础能力
│  ├─ base_request.py      # HTTP 请求、Header、poll_get、请求日志
│  ├─ base_assertions.py   # 通用断言
│  ├─ base_decorators.py   # Allure step、结果附件等装饰器能力
│  ├─ base_task.py         # 通用业务骨架：创建、轮询、账单/用量查询
│  └─ __init__.py
├─ util/                   # 工具能力：日志、媒体资源下载等
├─ module/                 # 业务/模型用例目录
│  ├─ image_model/
│  ├─ video_model/
│  ├─ smoke/
│  └─ protocol_testing/
│     ├─ request.py        # 当前模块请求类
│     ├─ assertions.py     # 当前模块断言类
│     ├─ decorators.py     # 当前模块的装饰器拓展
│     ├─ task.py           # 封装本模块独有的业务方法，继承 BaseTask
│     ├─ test_*.py         # 测试用例
│     └─ __init__.py
├─ tests/                  # 框架基础能力单测
├─ config.py               # 环境配置读取
├─ pytest.ini              # pytest 收集与 Allure 配置
├─ run_master.py           # 框架执行入口
├─ requirements.txt        # Python 依赖
└─ package.json            # Allure CLI 依赖
```

以下目录为本地生成产物，不应提交到代码仓库：

```text
allure-results/
allure-report/
data/
.pytest_cache/
__pycache__/
node_modules/
```

## 分层职责

`common/` 只放所有模块都可复用的公共基础能力。

`BaseRequest` 负责 HTTP 请求、默认请求头、Session 生命周期、URL 拼接、请求日志和 `poll_get()`。

`BaseAssertions` 负责通用断言，例如状态码断言、JSONPath 断言。

`BaseDecorators` 负责通用 Allure step、模型结果附件、媒体资源下载结果挂载等能力。

`BaseTask` 负责通用业务骨架，例如：

```python
create_image_generation(request_client, payload)
create_chat_completion(request_client, payload)
create_media_generation(request_client, payload)
poll_media_generation_result(request_client, task_id, ...)
create_and_poll_media_generation(request_client, payload, ...)
query_account_balance_for_billing(request_client)
query_usage_records_for_billing(request_client, model_response=..., request_id=...)
```

`module/<模块名>/` 放当前模块自己的请求类、断言类、装饰器拓展、业务封装和测试用例。

`module/` 下每一个新创建的测试模块中，每个独立文件的类都必须分别继承 `common` 中对应的公共基类：

```python
from common import BaseAssertions, BaseDecorators, BaseRequest, BaseTask


class XxxRequest(BaseRequest):
    pass


class XxxAssertions(BaseAssertions):
    pass


class XxxDecorators(BaseDecorators):
    pass


class XxxTask(BaseTask):
    pass
```

当前模块的 `task.py` 应继承 `BaseTask`，用于封装本模块独有的业务方法，例如模块专属流程、payload 构造和差异化封装。已沉淀到 `BaseTask` 的公共能力不要在模块 `task.py` 中重复实现。

## 用例编写规范

1. 用例统一放在 `module/<模块名>/test_*.py`。
2. 文件名使用 `test_*.py`，测试类使用 `Test*`，测试方法使用 `test_*`。
3. 测试类不要定义 `__init__`，否则 pytest 不会收集该测试类。
4. 使用 `setup_method` 初始化 request、assertions、task 对象。
5. 使用 `teardown_method` 关闭 request session。
6. 每个新测试模块中，每个独立文件的类都必须分别继承 `common` 公共基类：`request.py -> BaseRequest`、`assertions.py -> BaseAssertions`、`decorators.py -> BaseDecorators`、`task.py -> BaseTask`。
7. 用例中通过模块继承类调用公共能力，例如 `self.smoke_task.create_chat_completion(...)`，不要直接实例化或调用 `BaseTask`。
8. `common/base_task.py` 只放公共业务骨架，不放真实业务 payload 数据。
9. 真实测试数据、模型 ID、payload builder 放在对应模块的 `task.py` 或用例中。
10. `request.py` 负责模块私有路径或特殊请求方法；通用路径优先使用 `BaseTask + BaseRequest.post/get/poll_get`。
11. `task.py` 负责封装本模块独有的业务方法，继承 `BaseTask`，只保留模块特有流程。
12. `assertions.py` 负责模块特有断言，通用断言使用 `BaseAssertions`。
13. `decorators.py` 负责当前模块的装饰器拓展。
14. 当一次测试动作需要“发起请求并获取响应”作为一组业务操作时，必须在 `task.py` 中封装该组动作，并使用 `allure_step` 写明业务步骤名。
15. 用例中禁止直接调用 request 方法形成裸露的顶层 `接口请求` / `接口响应` 步骤；这些日志步骤必须被包裹在明确的业务步骤下。
16. payload 使用 Python 字典，不使用 YAML。
17. JSON 中的 `true/false/null` 应改为 Python 的 `True/False/None`。
18. 用例中不要硬编码完整环境域名和 API Key，统一通过 `.env` 与 `config.py` 管理。
19. 新增或修改用例后，先执行 `--collect-only -q` 确认可收集。

## Task 调用规范

同步图片生成：

```python
response = self.image_task.create_image_generation(
    self.image_request,
    payload,
)
```

文本对话补全：

```python
response = self.smoke_task.create_chat_completion(
    self.smoke_request,
    payload,
)
```

异步媒体任务创建：

```python
create_response = self.image_task.create_media_generation(
    self.image_request,
    payload,
)
```

异步媒体任务轮询：

```python
poll_response = self.image_task.poll_media_generation_result(
    self.image_request,
    task_id,
)
```

异步媒体完整流程统一使用：

```python
poll_response = self.image_task.create_and_poll_media_generation(
    self.image_request,
    payload,
)
```

账单用量查询应先产生一次模型调用响应，再通过继承自 `BaseTask` 的公共方法查询：

```python
chat_response = self.smoke_task.create_chat_completion(
    self.smoke_request,
    self.smoke_task.build_chat_completions_payload(),
)

usage_response = self.smoke_task.query_usage_records_for_billing(
    self.smoke_request,
    model_response=chat_response,
)
```

如果某个用例需要“创建任务后立即查询任务状态”这类成组操作，应在当前模块的 `task.py` 中封装，并写明 Allure 业务步骤名：

```python
from common import allure_step


class SmokeTask(BaseTask):
    @allure_step("查询异步媒体任务状态: {task_id}")
    def get_media_generation_task(
        self,
        smoke_request: SmokeRequest,
        task_id: str,
    ) -> requests.Response:
        return smoke_request.get_media_generation_task(task_id)
```

用例只能调用封装后的 task 方法：

```python
task_response = self.smoke_task.get_media_generation_task(self.smoke_request, task_id)
```

禁止在用例中直接调用：

```python
task_response = self.smoke_request.get_media_generation_task(task_id)
```

## 标准用例模板

```python
from __future__ import annotations

from module.smoke import SmokeAssertions, SmokeRequest, SmokeTask


class TestDemo:
    def setup_method(self):
        self.smoke_request = SmokeRequest()
        self.smoke_assertions = SmokeAssertions()
        self.smoke_task = SmokeTask()

    def teardown_method(self):
        self.smoke_request.close()

    def test_chat_completion(self):
        payload = self.smoke_task.build_chat_completions_payload()

        response = self.smoke_task.create_chat_completion(
            self.smoke_request,
            payload,
        )

        self.smoke_assertions.assert_status_code(response, 200)
```

## Allure 步骤规范

业务层步骤由 `common/base_decorators.py` 中的 `allure_step` 装饰器生成，步骤标题直接写入装饰器。

Allure 报告中不允许出现裸露的顶层 `接口请求` / `接口响应`。非轮询 HTTP 日志必须作为某个明确业务步骤的子步骤出现，例如 `异步媒体任务创建：/v1/media/generations`、`查询异步媒体任务状态: task_id`、`查询账户余额`。

典型步骤包括：

```text
POST /v1/images/generations
POST /v1/chat/completions
POST /v1/media/generations
轮询媒体生成结果: {task_id}
接口请求
接口响应
轮询结果请求
轮询结果响应
前置资源
模型响应结果
```

单步同步图片生成的 Allure 结构：

```text
POST /v1/images/generations
  接口请求
  接口响应
```

异步媒体复合调用的 Allure 结构：

```text
POST /v1/media/generations
  接口请求
  接口响应
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

当 POST payload 中存在 `input.media.url` 时，框架会在 Allure 中额外挂载 `前置资源` 步骤。

## 执行命令

只收集用例，不执行接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke --collect-only -q
```

执行指定模块：

```powershell
.\.venv\Scripts\python.exe run_master.py module/smoke
```

执行框架基础单测：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_base_task.py -q
```

打开 Allure 报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

## 新增用例检查清单

```text
文件名是否为 test_*.py
测试类是否以 Test 开头
测试方法是否以 test_ 开头
测试类是否没有 __init__
是否使用 setup_method 初始化对象
是否使用 teardown_method 关闭 request session
新测试模块的 request/assertions/decorators/task 文件是否分别继承 common 公共基类
payload 是否为 Python 字典
true/false/null 是否改为 True/False/None
是否没有硬编码 API Key
是否没有硬编码完整环境域名
公共能力是否通过模块 Task 继承调用
成组请求是否已在 task.py 中封装并写明 Allure 业务步骤名
是否没有裸露的顶层 接口请求/接口响应
是否没有在模块 task.py 重复实现 BaseTask 已具备的方法
长耗时任务是否设置 poll_timeout
新增后是否执行 --collect-only -q
```
