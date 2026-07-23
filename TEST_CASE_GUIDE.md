# 测试用例编写指南

本文档说明如何基于当前 API 测试框架编写、收集、执行和排查测试用例。

当前框架基于 `pytest`、`requests` 和 `allure-pytest`，用例以 Python 代码形式编写，统一放在 `module/<model_name>/` 目录下。

## 1. 用例目录规范

所有模型用例放在对应模型目录下：

```text
module/<model_name>/test_*.py
```

例如视频模型：

```text
module/video_model/test_wan2_7_t2v.py
module/video_model/test_wan2_7_i2v.py
module/video_model/test_wan2_7_r2v.py
module/video_model/test_wan2_7_videoedit.py
```

pytest 收集规则：

```text
文件名：test_*.py
测试类：Test*
测试方法：test_*
```

示例：

```python
class TestVideoT2V:
    def test_pos_case_1(self):
        ...
```

## 2. 标准用例模板

测试类中不要定义 `__init__`。pytest 会跳过带自定义 `__init__` 的测试类。

推荐使用 `setup_method` 初始化对象，使用 `teardown_method` 关闭 request session。

```python
from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoT2V:
    def setup_method(self):
        self.video_request = VideoRequest()
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "prompt": "这里填写提示词"
            },
            "model": "wan2.7-t2v",
            "parameters": {
                "duration": 10,
                "prompt_extend": True,
                "ratio": "16:9",
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_generation(
            self.video_request,
            payload,
            poll_timeout=1500,
        )
```

## 3. Payload 编写规范

payload 直接使用 Python 字典，不使用 YAML。

JSON 中的值需要转换为 Python 写法：

```text
true  -> True
false -> False
null  -> None
```

示例：

```python
payload = {
    "input": {
        "prompt": "一段电影级视频描述"
    },
    "model": "wan2.7-t2v",
    "parameters": {
        "duration": 15,
        "prompt_extend": True,
        "ratio": "16:9",
        "resolution": "720P",
        "watermark": True,
    },
}
```

不要在用例中硬编码完整域名或 API Key。环境地址和密钥通过 `.env` 配置，由 `config.py` 读取。

## 4. 视频生成类用例

视频模型一般通过 `VideoTask.create_and_poll_generation()` 完成创建任务和轮询结果。

```python
self.video_task.create_and_poll_generation(
    self.video_request,
    payload,
    poll_timeout=1500,
)
```

该方法会完成：

```text
创建任务 -> 获取 task_id -> 轮询任务结果 -> 下载模型响应结果 -> 写入 Allure
```

默认轮询成功判断：

```python
success_json_path="$.result.urls"
```

默认轮询失败判断：

```python
failure_json_path="$.error.category"
```

如果任务耗时较长，建议显式设置 `poll_timeout`，例如：

```python
poll_timeout=1500
```

## 5. 文生视频 T2V 用例示例

适用于无输入媒体、仅使用 prompt 生成视频的场景。

```python
from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoT2V:
    def setup_method(self):
        self.video_request = VideoRequest()
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "prompt": "一段紧张刺激的侦探追查故事，展现电影级叙事能力。"
            },
            "model": "wan2.7-t2v",
            "parameters": {
                "duration": 15,
                "prompt_extend": True,
                "ratio": "16:9",
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_generation(
            self.video_request,
            payload,
            poll_timeout=1500,
        )
```

## 6. 图生视频 I2V 用例示例

当 payload 中存在 `input.media.url` 时，框架会在 POST 请求前自动触发媒体资源下载，并在 Allure 中挂载到 `前置资源` 步骤。

```python
from __future__ import annotations

from module.video_model import VideoAssertions, VideoRequest, VideoTask


class TestVideoI2V:
    def setup_method(self):
        self.video_request = VideoRequest()
        self.video_assertions = VideoAssertions()
        self.video_task = VideoTask()

    def teardown_method(self):
        self.video_request.close()

    def test_pos_case_1(self):
        payload = {
            "input": {
                "media": [
                    {
                        "type": "first_frame",
                        "url": "https://example.com/image.png",
                    }
                ],
                "prompt": "让图片中的主体自然运动，生成电影感视频。",
            },
            "model": "wan2.7-i2v",
            "parameters": {
                "duration": 10,
                "prompt_extend": True,
                "resolution": "720P",
                "watermark": True,
            },
        }

        self.video_task.create_and_poll_generation(
            self.video_request,
            payload,
            poll_timeout=1500,
        )
```

常见 `media.type`：

```text
first_frame
driving_audio
reference_video
reference_image
video
image
audio
```

## 7. 断言写法

当前视频示例用例主要验证创建和轮询流程是否跑通，不强制增加断言。

如果需要断言创建任务响应，可以拆开调用：

```python
response = self.video_task.create_generation(self.video_request, payload)
self.video_assertions.assert_status_code(response, 200)
```

断言 JSONPath：

```python
self.video_assertions.assert_json_value(response, "$.model", "wan2.7-t2v")
```

通用断言能力来自 `BaseAssertions`：

```python
assert_status_code(response, expected)
assert_json_value(response, json_path, expected)
async_assert_status_code(response, expected)
async_assert_json_value(response, json_path, expected)
```

## 8. Task 单步和复合调用

媒体生成类任务的通用业务封装位于 `common/base_task.py`。

单步图片生成：

```python
image_response = self.image_task.create_image_generation(self.image_request, payload)
```

单步对话补全：

```python
chat_response = self.image_task.create_chat_completion(self.image_request, payload)
```

单步创建媒体异步任务：

```python
create_response = self.image_task.create_media_generation(self.image_request, payload)
```

单步轮询媒体生成结果：

```python
poll_response = self.image_task.poll_media_generation_result(self.image_request, task_id)
```

复合创建并轮询：

```python
poll_response = self.image_task.create_and_poll_media_generation(self.image_request, payload)
```

图片模型的 `ImageTask` 继承 `BaseTask`，因此支持以上调用方式。媒体异步完整流程统一使用 `create_and_poll_media_generation()`。

## 9. Request 和 Task 的职责

`request.py` 负责接口路径和 HTTP 方法封装。

示例：

```python
class VideoRequest(BaseRequest):
    generation_path = "/v1/media/generations"
    task_path_template = "/v1/media/tasks/{task_id}"

    def create_generation(self, payload):
        return self.post(self.generation_path, json=payload)
```

`task.py` 负责封装本模块独有的业务方法，例如模块专属流程、payload 构造和差异化封装。通用创建、轮询、创建并轮询逻辑应沉淀到 `BaseTask`；账户余额查询、模型用量查询等 billing 通用流程也应沉淀到 `BaseTask`。模型目录的 `task.py` 只保留模型专属流程差异。`BaseTask` 可直接使用 `BaseRequest.post()`、`BaseRequest.get()` 和 `BaseRequest.poll_get()`，模型 `request.py` 不需要为通用路径重复封装同名方法。业务步骤统一使用 `common/base_decorators.py` 中的 `allure_step` 装饰器生成，基础步骤文案直接写在 `BaseTask` 的装饰器中。

用例文件中只保留测试数据和调用流程，不建议在 `test_*.py` 中重复拼接接口路径、处理轮询细节。

## 10. 新增模型目录规范

新增模型时建议保持固定结构：

```text
module/new_model/
  request.py
  assertions.py
  decorators.py
  task.py
  test_xxx.py
  __init__.py
```

`module/` 下每一个新创建的测试模块中，每个独立文件的类都必须分别继承 `common` 中对应的公共基类：

```python
from common import BaseAssertions, BaseDecorators, BaseRequest, BaseTask


class NewModelRequest(BaseRequest):
    pass


class NewModelAssertions(BaseAssertions):
    pass


class NewModelDecorators(BaseDecorators):
    pass


class NewModelTask(BaseTask):
    pass
```

职责划分：

```text
request.py      写接口路径和 HTTP 方法封装
task.py         写本模块独有的业务方法
assertions.py   写模型专属断言
decorators.py   写当前模块的装饰器拓展
test_*.py       写具体测试数据和调用流程
```

每个模型目录的 `task.py` 彼此独立，不要跨模型目录引用其它模型的 `task.py`。

如果某个能力所有模型都能复用，应下沉到 `common` 或 `util`。

## 11. 环境配置

创建或修改根目录 `.env`：

```text
USE_CHINA_ENVIRONMENT=TRUE

CHINA_TEST_ENVIRONMENT_BASE_URL=https://pre.juhemoxing.com
CHINA_API_KEY=your-china-api-key

OVERSEAS_TEST_BASE_URL=https://pre.tokensave.pro
OVERSEAS_API_KEY=your-overseas-api-key

API_TIMEOUT=600
```

环境开关：

```text
USE_CHINA_ENVIRONMENT=TRUE   # 国内环境
USE_CHINA_ENVIRONMENT=FALSE  # 海外环境
```

旧变量 `BASE_URL`、`API_KEY` 当前不会被 `config.py` 读取。

## 12. 用例收集和执行

当前仓库实际执行入口是 `run_master.py`。

只验证收集，不执行接口：

```powershell
.\.venv\Scripts\python.exe run_master.py module/video_model --collect-only -q
```

执行某个模型目录：

```powershell
.\.venv\Scripts\python.exe run_master.py module/video_model
```

执行指定文件：

```powershell
.\.venv\Scripts\python.exe run_master.py module/video_model/test_wan2_7_t2v.py
```

并发执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/video_model -n 2
```

也可以直接使用 pytest：

```powershell
.\.venv\Scripts\python.exe -m pytest module/video_model --collect-only -q
.\.venv\Scripts\python.exe -m pytest module/video_model/test_wan2_7_t2v.py
```

## 13. Allure 报告

pytest 默认会输出 Allure 原始结果：

```text
allure-results/
```

测试结束后，`module/conftest.py` 会自动生成 HTML 报告：

```text
allure-report/
```

打开报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

Allure 中会记录：

```text
POST /v1/images/generations
POST /v1/chat/completions
POST /v1/media/generations
接口请求
接口响应
轮询媒体生成结果: task_id
轮询结果请求
轮询结果响应
前置资源
模型响应结果
```

说明：

- `POST /v1/images/generations`、`POST /v1/chat/completions`、`POST /v1/media/generations`、`轮询媒体生成结果: task_id` 是 `BaseTask` 通过 `allure_step` 装饰器生成的业务层步骤，步骤文案直接写在装饰器中。
- `接口请求`、`接口响应` 是非轮询 HTTP 请求的日志步骤。
- `轮询结果请求`、`轮询结果响应` 是 `poll_get` 最终一次轮询的日志步骤。
- `模型响应结果` 是轮询成功后下载的结果附件步骤。
- `前置资源` 只在 POST payload 存在 `input.media.url` 时出现。

单步图片生成的 Allure 结构：

```text
POST /v1/images/generations
  接口请求
  接口响应
```

单步对话补全的 Allure 结构：

```text
POST /v1/chat/completions
  接口请求
  接口响应
```

单步媒体异步任务的 Allure 结构：

```text
POST /v1/media/generations
  接口请求
  接口响应
```

单步轮询媒体生成结果的 Allure 结构：

```text
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

复合调用 `create_and_poll_generation` 的 Allure 结构：

```text
POST /v1/media/generations
  接口请求
  接口响应
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

如果报告没有生成，优先检查：

```powershell
npm install
java -version
```

以及 pytest 输出中是否出现：

```text
Allure HTML report generation failed
```

## 14. 新增用例检查清单

新增或修改用例后，按以下清单检查：

```text
文件名是否是 test_*.py
测试类是否以 Test 开头
测试方法是否以 test_ 开头
测试类是否没有 __init__
是否使用 setup_method 初始化对象
是否使用 teardown_method 关闭 request session
新测试模块的 request/assertions/decorators/task 文件是否分别继承 common 公共基类
payload 是否是 Python 字典
true/false/null 是否改成 True/False/None
是否没有硬编码 API Key
是否没有硬编码完整环境域名
媒体资源是否放在 input.media.url
长耗时任务是否设置 poll_timeout
新增用例后是否执行 --collect-only -q
```

推荐新增后先执行：

```powershell
.\.venv\Scripts\python.exe run_master.py module/video_model --collect-only -q
```
