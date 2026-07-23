# API Test Framework

基于 `pytest`、`requests` 和 `allure-pytest` 的代码式接口测试框架。

当前框架已经按“通用基类 + 模型个性化继承 + pytest_nodeid 用例池执行”的方式组织：

- `common` 只存放最通用的基类能力。
- `util` 存放日志、媒体下载等工具能力。
- `module/<model_name>` 存放具体模型的请求类、断言类、装饰器类、本模块独有业务封装类和测试用例。
- `main.py` 负责收集测试用例，返回 `list[str]` 类型的 `case_pool`，列表元素为 `pytest_nodeid`。
- `run_main.py` 是框架执行入口，读取 `case_pool` 后调用 `pytest.main()` 执行。

## 目录结构

```text
common/
  base_request.py       # BaseRequest：通用 HTTP 请求、header、poll_get
  base_assertions.py    # BaseAssertions：通用断言基类
  base_decorators.py    # BaseDecorators：通用 Allure step、模型结果附件能力
  base_task.py          # BaseTask：通用创建、轮询、创建并轮询业务封装
  __init__.py           # 导出通用基类和兼容函数

util/
  api_call_logger.py   # 通用请求/响应日志写入 Allure
  media_resources.py    # POST 前 input.media.url 异步下载与 Allure 步骤输出
  __init__.py

module/
  conftest.py           # pytest fixture、Allure 结果清理与 HTML 报告生成
  image_model/
    request.py          # ImageRequest(BaseRequest)
    assertions.py       # ImageAssertions(BaseAssertions)
    decorators.py       # ImageDecorators(BaseDecorators)
    task.py             # ImageTask：封装本模块独有的业务方法
    test_wan2_7_image.py
    test_wan2_7_image_pro.py
  video_model/
    request.py          # VideoRequest(BaseRequest)
    assertions.py       # VideoAssertions(BaseAssertions)
    decorators.py       # VideoDecorators(BaseDecorators)
    task.py             # VideoTask：封装本模块独有的业务方法
    test_wan2_7_videoedit.py

main.py                 # 收集 pytest_nodeid 到 case_pool(list)
run_main.py             # 框架执行入口
config.py               # 环境配置
pytest.ini              # pytest 默认配置
requirements.txt        # Python 依赖
package.json            # Allure CLI 本地依赖
.env.example            # 环境变量示例
```

## 安装依赖

安装 Python 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装 Allure CLI 本地依赖：

```powershell
npm install
```

说明：

- `allure-pytest` 负责生成 Allure 原始结果。
- `allure-commandline` 负责把原始结果生成 HTML 报告。
- `pytest-xdist` 支持用例级并发执行。
- Allure CLI 依赖 Java。框架会优先使用系统 `java`，找不到时会尝试常见 IDE 自带的 JBR/JRE。

## 环境配置

创建或修改 `.env`，按 `config.py` 当前读取的变量配置：

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

`config.py` 会根据开关选择对应的 `base_url` 和 `api_key`。

注意：旧的 `BASE_URL`、`API_KEY` 变量当前不会被 `config.py` 读取。

## common 分层规范

`common` 只放所有模型都能复用的通用能力，且核心能力必须用类包裹，供 `module` 中的模型类继承。

### BaseRequest

`BaseRequest` 负责：

- session 生命周期
- 默认请求头
- `get/post/put/patch/delete`
- `poll_get`
- URL 拼接
- 请求日志接入

具体模型路径不要写在 `BaseRequest` 中，应写在模型自己的 `request.py` 中。

### BaseAssertions

`BaseAssertions` 负责通用断言：

```python
assert_status_code(response, expected)
assert_json_value(response, json_path, expected)
async_assert_status_code(response, expected)
async_assert_json_value(response, json_path, expected)
```

模型个性化断言写在对应模型目录的 `assertions.py` 中。

### BaseDecorators

`BaseDecorators` 负责：

- 通用 `allure_step`
- `poll_get` 模型结果 URL 提取、下载与收集
- 模型响应结果附件类型识别与文件挂载

模型个性化装饰器拓展写在对应模型目录的 `decorators.py` 中，并由该文件中的类继承 `BaseDecorators`。

`task.py` 不直接继承 `BaseDecorators`。它的职责是封装当前模型目录下独有的业务方法，例如模块专属流程、payload 构造和差异化封装。通用创建、轮询、创建并轮询等能力应沉淀到 `BaseTask`。每个模型目录的 `task.py` 彼此独立，不允许跨模型目录相互引用；如果存在所有模型都可复用的能力，应下沉到 `common` 或 `util`。

### BaseTask

`BaseTask` 负责媒体生成类任务的通用业务封装：

```python
create_image_generation(request_client, payload)
create_chat_completion(request_client, payload)
create_media_generation(request_client, payload)
poll_media_generation_result(request_client, task_id, ...)
create_and_poll_media_generation(request_client, payload, ...)
query_account_balance_for_billing(request_client)
query_usage_records_for_billing(request_client, model_response=..., request_id=...)
query_usage_records_by_model_response_for_billing(request_client, model_response)
query_usage_records_by_request_id_for_billing(request_client, request_id)
extract_task_id(create_response)
```

其中：

- `create_image_generation` 通过 `BaseRequest.post()` 调用 `POST /v1/images/generations`。
- `create_chat_completion` 通过 `BaseRequest.post()` 调用 `POST /v1/chat/completions`。
- `create_media_generation` 通过 `BaseRequest.post()` 调用 `POST /v1/media/generations`，用于创建异步媒体生成任务。
- `poll_media_generation_result` 通过 `BaseRequest.poll_get()` 轮询异步媒体生成结果。
- `create_and_poll_media_generation` 是复合封装，按 `POST /v1/media/generations -> 提取 task_id -> 轮询异步媒体结果` 执行。
- `query_account_balance_for_billing` 使用控制台密钥查询账户余额。
- `query_usage_records_for_billing` 按已有模型响应或指定 request id 查询模型用量记录，不在 `BaseTask` 中构造真实业务 payload。
- `query_usage_records_by_model_response_for_billing`、`query_usage_records_by_request_id_for_billing` 用于按已有模型响应或指定 request id 查询用量记录。
- `BaseTask` 可直接使用 `BaseRequest` 的请求方法；模型 `request.py` 不需要为这些通用路径重复封装同名方法。
- `BaseTask` 的业务步骤使用 `common/base_decorators.py` 中的 `allure_step` 装饰器实现，步骤文案直接写在装饰器中。

## 模型目录规范

每个模型目录建议固定包含：

```text
request.py
assertions.py
decorators.py
task.py
test_*.py
__init__.py
```

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

`decorators.py` 是 `BaseDecorators` 的模型侧继承点，用于承接当前模块的装饰器拓展。

`task.py` 只服务当前目录下的测试用例，用于封装本模块独有的业务方法。通用创建、轮询和业务组合骨架应优先沉淀到 `BaseTask`。新增模型时应创建本目录自己的 `task.py`，不要引用其它模型目录下的 `task.py`。

以 `image_model` 为例：

```python
from common import BaseAssertions, BaseDecorators, BaseRequest, BaseTask


class ImageRequest(BaseRequest):
    pass


class ImageAssertions(BaseAssertions):
    pass


class ImageDecorators(BaseDecorators):
    pass


class ImageTask(BaseTask):
    pass
```

`__init__.py` 负责导出主要对象类：

```python
from module.image_model.assertions import ImageAssertions
from module.image_model.decorators import ImageDecorators
from module.image_model.request import ImageRequest
from module.image_model.task import ImageTask

__all__ = ["ImageAssertions", "ImageDecorators", "ImageRequest", "ImageTask"]
```

## 测试类写法

测试类中不要定义 `__init__`。pytest 会跳过带自定义 `__init__` 的测试类。

使用 `setup_method` 初始化三个对象，使用 `teardown_method` 关闭 request session：

```python
from module.image_model import ImageAssertions, ImageRequest, ImageTask


class TestImageGenerations:
    def setup_method(self):
        self.image_request = ImageRequest()
        self.image_assertions = ImageAssertions()
        self.image_task = ImageTask()

    def teardown_method(self):
        self.image_request.close()

    def test_create_image_generation(self):
        self.image_task.create_and_poll_media_generation(self.image_request, payload)
```

用例中统一使用 `对象.方法()` 形式调用。

## 用例收集与执行入口

### main.py

`main.py` 负责收集测试用例，输出 `pytest_nodeid` 形式的用例池。

`case_pool` 是普通 `list[str]`，不是属性类。

后续职责规划：`main.py` 可作为 pytest 命令行参数注册入口，用于承接 pytest 插件级参数、收集规则或执行参数扩展；当前实现仍是独立收集脚本。

收集全部用例：

```powershell
.\.venv\Scripts\python.exe main.py
```

`pytest_nodeid` 示例：

```text
module/image_model/test_wan2_7_image.py::TestImageGenerations::test_pos_case_1
module/image_model/test_wan2_7_image_pro.py::TestImageGenerations::test_create_image_generation
module/video_model/test_wan2_7_videoedit.py::TestVideo::test_pos_case_1
```

### run_main.py

`run_main.py` 是框架启动入口。它会调用 `main.py` 收集用例池，然后把 nodeid list 传给 `pytest.main()`。

后续职责规划：`run_main.py` 可继续扩展为外部自定义命令行入口，用于封装业务侧参数、环境选择、模型选择、用例筛选等框架外部参数，再转换为 pytest 执行参数。当前已支持测试路径、`pytest-xdist` 参数和未知 pytest 参数透传。

执行全部用例：

```powershell
.\.venv\Scripts\python.exe run_main.py
```

执行指定目录：

```powershell
.\.venv\Scripts\python.exe run_main.py module/image_model
```

传递额外 pytest 参数：

```powershell
.\.venv\Scripts\python.exe run_main.py module/image_model -n 2
```

只传 pytest-xdist 参数时，默认收集 `module` 下全部用例：

```powershell
.\.venv\Scripts\python.exe run_main.py -n auto
```

也可以使用显式参数：

```powershell
.\.venv\Scripts\python.exe run_main.py --test-path module/video_model --numprocesses 2 --dist loadscope
```

只验证收集，不执行接口：

```powershell
.\.venv\Scripts\python.exe run_main.py module/image_model --collect-only -q
```

## 直接使用 pytest

仍然可以直接使用 pytest：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

并发执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -n auto
```

指定并发数：

```powershell
.\.venv\Scripts\python.exe -m pytest -n 4
```

只收集用例：

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

## Allure 报告

`pytest.ini` 默认配置：

```ini
addopts =
    --alluredir=allure-results
    --clean-alluredir
testpaths = module
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

执行 pytest 后：

- `allure-results/` 保存 Allure 原始结果。
- `module/conftest.py` 在 pytest 结束后自动执行 `allure generate`。
- `allure-report/` 保存 HTML 报告。

打开报告：

```powershell
node_modules\.bin\allure.cmd open allure-report
```

如果报告没有生成，优先检查：

- 是否执行过 `npm install`
- 是否存在可用 Java：`java -version`
- pytest 输出中是否出现 `Allure HTML report generation failed`

## Allure 请求步骤规则

通过 `BaseRequest` 发出的请求会写入 Allure 测试步骤。

步骤分类：

- 非 `poll_get` 请求会拆分为两个一级测试步骤：
  - `接口请求`：展开后包含 `请求行`、`请求头`、`请求体`
  - `接口响应`：展开后包含 `响应行`、`响应头`、`响应体`
- `poll_get` 最终一次轮询会拆分为两个一级测试步骤：
  - `轮询结果请求`：展开后包含 `请求行`、`请求头`、`请求体`
  - `轮询结果响应`：展开后包含 `响应行`、`响应头`、`响应体`

典型媒体生成用例的 Allure 一级步骤顺序为：

```text
接口请求
接口响应
轮询结果请求
轮询结果响应
模型响应结果
```

`poll_get` 中间轮询请求不会全部写入报告，只保留最终一次请求和响应日志。

## Allure 业务封装步骤规则

通过 `BaseTask` 发起的任务操作会在 Allure 中增加业务层步骤，业务层步骤由 `BaseDecorators.allure_step` 装饰器生成，业务层步骤内再记录 `BaseRequest` 产生的请求和响应步骤。

图片生成任务当前步骤文案为：

```text
POST /v1/images/generations
POST /v1/chat/completions
POST /v1/media/generations
轮询媒体生成结果: {task_id}
```

单步图片生成调用：

```python
self.image_task.create_image_generation(self.image_request, payload)
```

Allure 步骤结构为：

```text
POST /v1/images/generations
  接口请求
  接口响应
```

单步对话补全调用：

```python
self.image_task.create_chat_completion(self.image_request, payload)
```

Allure 步骤结构为：

```text
POST /v1/chat/completions
  接口请求
  接口响应
```

单步媒体异步任务调用：

```python
self.image_task.create_media_generation(self.image_request, payload)
```

Allure 步骤结构为：

```text
POST /v1/media/generations
  接口请求
  接口响应
```

单步轮询媒体生成结果调用：

```python
self.image_task.poll_media_generation_result(self.image_request, task_id)
```

Allure 步骤结构为：

```text
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

复合操作调用：

```python
self.image_task.create_and_poll_media_generation(self.image_request, payload)
```

Allure 步骤结构为：

```text
POST /v1/media/generations
  接口请求
  接口响应
轮询媒体生成结果: task_id
  轮询结果请求
  轮询结果响应
模型响应结果
```

如果 POST 请求体中包含 `input.media.url`，用例结束时还会额外挂载 `前置资源` 步骤。

## 模型响应结果测试步骤

`poll_get` 成功后，如果 `success_json_path` 对应值中包含 `http` 或 `https` 链接，框架会下载资源到 `data/` 目录。

下载动作发生在 `poll_get` 成功返回前；下载后的模型响应结果会在当前用例的 Allure 测试步骤中输出，步骤名和附件名均为：

```text
模型响应结果
```

其中，`BaseDecorators.download_links_from_poll_get` 负责从 `poll_get` 成功响应中提取 URL、下载文件并记录结果；`module/conftest.py` 在用例结束阶段统一挂载为 Allure 测试步骤，不展示在 Allure 后置栏。

## POST 前媒体资源下载

当 POST 请求体存在 `input.media`，且 media 项包含 `url` 字段时，框架会在 POST 请求发送前触发媒体资源下载。

示例请求体：

```python
payload = {
    "input": {
        "media": [
            {
                "type": "video",
                "url": "https://example.com/source.mp4",
            }
        ],
        "prompt": "视频编辑提示词",
    },
    "model": "wan2.7-videoedit",
}
```

规则：

- 下载逻辑位于 `util/media_resources.py`。
- 下载后的资源存储在 `data/pre_data/` 目录。
- 下载在 POST 请求发送前启动。
- 下载使用后台 daemon 线程，不阻塞接口请求。
- Allure 中在用例结束时统一输出到测试步骤 `前置资源`，不展示在后置栏。
- `前置资源` 步骤下的附件名使用 `media.type`，例如 `video`、`image`、`audio`。
- 兜底机制在整个用例结束时生效；如果到挂载 `前置资源` 步骤时仍未下载完成，或下载失败，会输出文本兜底附件，并保留 `media.type` 和 `media.url`。
- 下载失败不会影响用例执行。

兜底文本示例：

```text
media.type: video
media.url: https://example.com/source.mp4
状态: 资源下载未完成
```

## 请求头

默认请求头由 `BaseRequest` 配置：

```python
{
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "api-v1_chat_completions-framework",
    "Authorization": "Bearer <api_key>",
}
```

用例或模型 request 类中可修改 session 请求头：

```python
client.set_header("X-Test-Source", "module-test")
client.update_headers({"X-Test-Source": "updated"})
client.remove_header("X-Test-Source")
client.reset_headers()
```

单次请求也可以传入请求头：

```python
response = client.get("/v1/models", headers={"X-Request-Id": "case-001"})
```

## poll_get

`poll_get()` 用于轮询任务状态：

```python
response = client.poll_get(
    "/v1/media/tasks/task_id",
    poll_interval=3,
    poll_timeout=900,
    success_json_path="$.result.urls",
    failure_json_path="$.error.category",
)
```

参数说明：

- `poll_interval`：每次 GET 的间隔秒数。
- `poll_timeout`：总轮询超时时间；不传时使用 `API_TIMEOUT`。
- `success_json_path`：只要提取到非空值，就返回最终响应。
- `failure_json_path`：只要提取到非空值，就让用例失败；不需要失败判断时可不传。

## 用例编写规范

- 用例文件放在 `module/<model_name>/` 下。
- 文件名使用 `test_*.py`。
- 测试类名以 `Test` 开头。
- 测试函数名以 `test_` 开头。
- 模型目录保留 `request.py`、`assertions.py`、`decorators.py`、`task.py` 四类文件。
- 测试类中使用 `setup_method` 初始化三个对象。
- 测试方法中使用 `self.对象.方法()` 调用。
- `module/` 下每一个新创建的测试模块中，每个独立文件的类都必须分别继承 `common` 中对应的公共基类。
- 当前 `video_model` 示例用例只执行创建和轮询流程，不做断言。
- 不在用例中硬编码完整域名。
- 不在用例中硬编码 API Key。
- 请求体直接使用 Python 字典，不使用 YAML 用例。
- 新增用例后先执行 `pytest --collect-only -q` 确认可收集。

## 生成目录和忽略规则

以下目录为本地生成产物，不应提交到仓库：

```text
allure-results/
allure-report/
node_modules/
.pytest_cache/
__pycache__/
data/
```
