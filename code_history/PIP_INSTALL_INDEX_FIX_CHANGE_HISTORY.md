# pip install 依赖安装超时修复记录

## 需求理解

用户执行：

```powershell
pip install -r .\requirements.txt
```

已安装依赖被识别为满足，但安装流程继续访问 `pypi.org/simple/jsonschema/`，最终因为 `pypi.org` 连接超时卡住。

## 第一性原理分析

`pip install -r requirements.txt` 的本质动作不是“只检查已安装包”，而是逐条解析需求并补齐未满足依赖。当前虚拟环境中 `jsonschema` 未安装，而框架的 `common/base_assertions.py` 运行时依赖 `jsonschema` 做 JSON Schema 断言，因此不能简单删除该依赖。

因果链：

1. `requirements.txt` 声明了 `jsonschema>=4.0.0`。
2. 当前 `.venv` 未安装 `jsonschema`。
3. pip 需要访问包索引解析并下载 `jsonschema`。
4. 默认索引是 `https://pypi.org/simple`。
5. 当前网络访问 `pypi.org` 超时。
6. 安装命令失败或长时间重试。

## TOC 约束判断

当前系统约束不是依赖版本冲突，也不是源码误引用，而是包索引网络链路不可用。最小有效改动应优先解除安装链路约束，让原命令继续可用。

## 改动内容

1. 在 `requirements.txt` 顶部加入项目级 PyPI 镜像索引：

```text
--index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

2. 保留 `jsonschema>=4.0.0`，因为 Schema 断言功能依赖它。
3. 删除重复的 `pydantic>=2.0.0` 声明，降低依赖文件噪音。

## 预期结果

继续使用原命令即可：

```powershell
pip install -r .\requirements.txt
```

pip 会优先从清华 PyPI 镜像解析和下载缺失依赖，避免默认访问 `pypi.org` 导致的超时。

## 验证结果

已执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

结果：

- pip 使用 `https://pypi.tuna.tsinghua.edu.cn/simple`。
- 成功安装 `jsonschema-4.26.0`。
- 同步安装 `jsonschema-specifications-2025.9.1`、`referencing-0.37.0`、`rpds-py-2026.6.3`。
