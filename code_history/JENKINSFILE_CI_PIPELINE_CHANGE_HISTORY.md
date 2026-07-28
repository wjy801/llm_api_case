# Jenkinsfile CI Pipeline Change History

## 变更目标

- 将 Jenkins Pipeline 从 Jenkins Job 内嵌脚本迁移为仓库根目录 `Jenkinsfile`，使 CI 配置进入 Git 版本管理。
- 修复 Windows Jenkins Agent 控制台输出中文用例名乱码的问题。

## 代码变更

- 新增 `Jenkinsfile`
  - 保留单 Job 参数化流水线：
    - `RUN_FRAMEWORK_TESTS`
    - `RUN_COLLECT_ONLY`
    - `RUN_REAL_SMOKE`
    - `USE_CHINA_ENVIRONMENT`
    - `SMOKE_TARGET`
  - 保留阶段：
    - `Checkout`
    - `Check Runtime Env`
    - `Prepare Python Env`
    - `Framework Unit Tests`
    - `Collect Smoke Cases`
    - `Real Smoke`
  - 保留 JUnit、Allure、artifact 归档。
  - 新增 `ciPowerShell` 包装函数，统一初始化 Windows 控制台与 Python UTF-8 输出环境。
  - 使用 `checkout scm`，由 Jenkins Job 的 SCM 配置负责仓库、分支、凭据。

## 编码修复

- 在流水线环境变量中增加：
  - `PYTHONIOENCODING=utf-8`
  - `PYTHONUTF8=1`
- 在每个 PowerShell 阶段执行前统一设置：
  - `chcp 65001`
  - `[Console]::InputEncoding = UTF8`
  - `[Console]::OutputEncoding = UTF8`
  - `$OutputEncoding = UTF8`

## 后续操作

- Jenkins Job 需要改为 `Pipeline script from SCM`，指向：
  - 仓库：`https://github.com/wjy801/llm_api_case.git`
  - 分支：`dev2`
  - 凭据：`Github`
  - 脚本路径：`Jenkinsfile`
