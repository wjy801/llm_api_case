# Flaky 治理看板运行与迁移配置

## 1. 文档范围

本文用于在新主机或新容器环境中复建以下闭环：

```text
GitLab 修复分支
-> Flaky 看板创建单用例 Probe
-> Jenkinsfile.probe 收集可信验证证据
-> 5 次可信 PASS（相邻计数至少 30 分钟）
-> 看板开放“合并 dev3 并关闭”
-> dev3 非强制 fast-forward 到已验证 SHA
-> 自动执行 flaky-recovery-close
-> 后续新运行不再对该治理记录执行 Skip
```

本文不包含任何真实密码、Token、API Key 或私钥。当前第一版看板没有用户登录和 RBAC，只允许绑定 `127.0.0.1` 或 `::1`，写请求另有同源和 CSRF 校验。不要把端口直接暴露到局域网或公网。

## 2. 参考部署拓扑

当前已验证环境使用以下角色；迁移时名称和路径可以替换，但职责不能合并：

| 角色 | 参考实现 | 职责 |
| --- | --- | --- |
| GitLab | `gitlab` 容器，宿主机 HTTP `8929`、SSH `2224` | 保存修复分支和 dev3 |
| Jenkins Controller | `jenkins` 容器，宿主机 HTTP `8080` | 保存 Job、凭据、队列和构建记录 |
| Jenkins HTTPS 入口 | 参考端口 `8443` | 为 Dashboard 调用 Jenkins 提供可信 HTTPS Origin |
| Probe Controller Agent | 标签 `probe-controller`，受信 OS 身份 | 访问固定控制端代码、SQLite 和证据密钥 |
| Probe Target Agent | 标签 `probe-target-restricted`，独立容器/身份 | 检出待验证 SHA 并执行一个目标用例 |
| Dashboard | 宿主机或受信管理节点 | 查询治理库、触发 Jenkins、fast-forward dev3、调用关闭 CLI |

Probe Controller 与 Probe Target 必须是不同 Jenkins 节点和不同 OS 身份。Target 不得读取治理数据库、控制端工作区、Jenkins 调度凭据或证据 HMAC 密钥。

## 3. 迁移所需文件

目标代码版本至少需要包含：

```text
Jenkinsfile.probe
quality/flaky_dashboard.py
quality/flaky_merge.py
quality/flaky_probe.py
quality/probe_job.py
quality/probe_target.py
quality/flaky_store/**
quality/templates/**
requirements.txt
requirements-dashboard.txt
```

推荐为控制端创建独立、干净且固定版本的 checkout，不要直接使用日常开发工作区：

```powershell
$controllerRoot = 'D:\API_CASE_CONTROLLER'
git clone http://localhost:8929/root/llm_api_case.git $controllerRoot
Set-Location $controllerRoot
git remote rename origin gitlab
git fetch gitlab
git switch --detach <APPROVED_CONTROLLER_COMMIT_SHA>

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dashboard.txt
```

控制端启动后必须保持 HEAD 不变且工作树干净。批准版本和 Probe 文件摘要按以下方式取得：

```powershell
$controllerCommit = (git rev-parse HEAD).Trim()
$jenkinsfileDigest = (Get-FileHash -Algorithm SHA256 Jenkinsfile.probe).Hash.ToLowerInvariant()
```

`QUALITY_FLAKY_CONTROLLER_COMMIT` 和 `QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256` 必须分别使用这两个值。修改 `Jenkinsfile.probe` 后必须重新审批版本并更新摘要。

## 4. GitLab 配置

### 4.1 两种访问地址

容器内外不要共用 `localhost`：

| 调用方 | URL 示例 |
| --- | --- |
| 宿主机 Dashboard/控制端 | `http://localhost:8929/root/llm_api_case.git` |
| 同一 Docker 网络中的 Jenkins/Target | `http://gitlab/root/llm_api_case.git` |

在 Dashboard checkout 中配置固定远端名：

```powershell
git remote add gitlab http://localhost:8929/root/llm_api_case.git
git ls-remote --exit-code gitlab refs/heads/dev3
```

若 `gitlab` 已存在，使用 `git remote set-url gitlab <URL>`。不要把 Token 写进远端 URL 或提交到仓库。

### 4.2 Dashboard Git 身份

运行 Dashboard 的 OS 账号必须能够：

- 精确读取 `refs/heads/<修复分支>` 和 `refs/heads/dev3`；
- 获取这两个提交对象；
- 对 dev3 执行普通、非强制 push；
- 在 GitLab 分支保护中被允许更新 dev3，但不能 force push。

建议使用独立 Project Access Token 或服务账号，并通过 Git Credential Manager/系统凭据存储提供认证。自动合并不调用 GitLab API，也不创建 merge commit；实际执行的是：

```text
git fetch <remote> refs/heads/dev3 refs/heads/<fix-branch>
git merge-base --is-ancestor <dev3-sha> <verified-sha>
git push <remote> <verified-sha>:refs/heads/dev3
git ls-remote <remote> refs/heads/dev3
```

代码不会使用 `--force`。修复分支 HEAD 漂移或 dev3 不能 fast-forward 时会拒绝合并，并要求基于最新 dev3 更新分支后重新验证。

## 5. Jenkins Probe Job

### 5.1 Job 定义

创建专用 Pipeline Job，建议固定全名为 `quality-probe`：

```text
Definition: Pipeline script from SCM
SCM: Git
Repository URL: 使用 Jenkins/Target 可访问的 GitLab 容器网络 URL
Script Path: Jenkinsfile.probe
Controller revision: 固定到已批准提交
```

Job 只允许以下三个参数，不能增加自由 nodeid、pytest 表达式或目标 SHA 参数：

| 参数 | 类型 | 来源 |
| --- | --- | --- |
| `TRIGGER_ID` | String | Dashboard dispatcher |
| `DISPATCH_TOKEN` | Password | Dashboard dispatcher，claim 后立即清除 |
| `PLAN_DIGEST` | String | 数据库中不可变 Probe plan |

`Jenkinsfile.probe` 已设置 `disableConcurrentBuilds`、73 小时总超时和最多 10 轮执行。每轮只选择计划中的一个 `case_id + param_hash`；可信 FAIL 立即结束，非计数结果达到上限后结束为证据不足。

### 5.2 插件与凭据

Jenkins 至少需要：

```text
Pipeline / workflow-aggregator
Git plugin
Credentials Binding
Pipeline Utility Steps（readJSON）
```

创建以下 Jenkins Credentials：

| ID | 类型 | 用途 |
| --- | --- | --- |
| SCM 凭据（名称自定） | Username/Token 或 SSH Key | Jenkins 和 Target 检出 GitLab SHA |
| `probe-target-api-key` | Secret text | Probe Target 调用非生产 API |
| `flaky-probe-evidence-key` | Secret file | Controller 验证并签名/复核 Probe 证据 |

`flaky-probe-evidence-key` 的文件内容必须与 Dashboard 主机上的证据 HMAC 文件完全相同。迁移有活动或 `READY_TO_CLOSE` attempt 的数据库时，必须保留原 key 和 `QUALITY_FLAKY_EVIDENCE_KEY_ID`，否则既有证据不能通过关闭门禁。

为 Dashboard 创建 Jenkins 服务账号，只授予 `quality-probe` 的 Read、Build、Cancel 权限。账号和 API Token 写入仓库外文件，格式严格为单行：

```text
username:api-token
```

### 5.3 节点环境

`probe-controller` 节点至少需要：

```text
QUALITY_FLAKY_CONTROLLER_ROOT=<控制端绝对路径>
QUALITY_FLAKY_DB_PATH=<仓库外 SQLite 绝对路径>
QUALITY_FLAKY_TRIGGER_ENABLE=1
QUALITY_FLAKY_JENKINS_ORIGIN=<Dashboard 可使用的同一 HTTPS Origin>
QUALITY_FLAKY_JENKINS_JOB=quality-probe
QUALITY_FLAKY_CONTROLLER_COMMIT=<40 位小写 SHA>
QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256=<64 位小写 SHA256>
QUALITY_FLAKY_EVIDENCE_KEY_ID=probe-evidence-key-v1
```

`probe-target-restricted` 节点至少需要：

```text
QUALITY_FLAKY_TARGET_PYTHON=<仓库外 Python 可执行文件>
```

Target Python 环境需要安装业务测试依赖。Target 不设置 `QUALITY_FLAKY_DB_PATH`、控制端路径或任何控制端 Secret；Pipeline 也会在执行测试前显式清空这些变量。

## 6. 数据库与密钥

### 6.1 SQLite

数据库必须是仓库和 Jenkins Workspace 之外的本地绝对路径，例如：

```text
D:\API_CASE_DATA\flaky\flaky.sqlite3
```

不要把活动 SQLite 放在 SMB/NFS 等网络共享上。Dashboard 与 Probe Controller 必须访问同一个文件，并依赖仓储层 writer lock 串行写入；Probe Target 永远不能访问该文件。

首次部署：

```powershell
New-Item -ItemType Directory -Force D:\API_CASE_DATA\flaky | Out-Null
.\.venv\Scripts\python.exe -m quality.cli flaky-db-migrate `
  --db D:\API_CASE_DATA\flaky\flaky.sqlite3
.\.venv\Scripts\python.exe -m quality.cli flaky-db-check `
  --db D:\API_CASE_DATA\flaky\flaky.sqlite3
```

迁移既有数据库前，先停止源 Dashboard 和所有写入该库的 Jenkins 构建，再复制数据库及其配套状态。不要在写入期间直接复制 `.sqlite3` 文件。

### 6.2 Dashboard 主机密钥

准备三个相互独立、位于仓库外的文件：

| 文件 | 内容要求 |
| --- | --- |
| Jenkins credential | 单行 `username:api-token` |
| CSRF key | 至少 32 字节随机内容 |
| Evidence HMAC key | 至少 32 字节随机内容，与 Jenkins Secret file 一致 |

示例路径：

```text
D:\JenkinsSecrets\dashboard-jenkins-auth.txt
D:\JenkinsSecrets\dashboard-csrf.key
D:\JenkinsSecrets\flaky-probe-evidence.key
```

可使用 Python 生成两个随机 key；命令只展示生成方式，不要把输出提交到仓库：

```powershell
$secretRoot = 'D:\JenkinsSecrets'
New-Item -ItemType Directory -Force $secretRoot | Out-Null
$csrf = & D:\API_CASE_CONTROLLER\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(48), end='')"
$evidence = & D:\API_CASE_CONTROLLER\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(48), end='')"
Set-Content -LiteralPath "$secretRoot\dashboard-csrf.key" -Value $csrf -NoNewline -Encoding ascii
Set-Content -LiteralPath "$secretRoot\flaky-probe-evidence.key" -Value $evidence -NoNewline -Encoding ascii
```

使用 NTFS ACL 只允许 Dashboard 服务账号和必要管理员读取。三个路径必须不同；配置加载器会拒绝仓库内路径、相对路径、不存在的文件以及复用同一文件。

## 7. Dashboard 环境变量

| 变量 | 必填 | 示例/说明 |
| --- | --- | --- |
| `QUALITY_FLAKY_TRIGGER_ENABLE` | 是 | `1`；允许新建 Probe |
| `QUALITY_FLAKY_JENKINS_ORIGIN` | 是 | `https://localhost:8443`；必须是 HTTPS origin，不含路径或账号 |
| `QUALITY_FLAKY_JENKINS_JOB` | 是 | `quality-probe` 或完整 folder/job 名 |
| `QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE` | 是 | 仓库外 `username:token` 文件 |
| `QUALITY_FLAKY_CONTROLLER_COMMIT` | 是 | 批准的控制端 40 位小写 SHA |
| `QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256` | 是 | 批准的 `Jenkinsfile.probe` SHA-256 |
| `QUALITY_FLAKY_CSRF_SECRET_FILE` | 是 | 仓库外 CSRF key |
| `QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE` | 是 | 仓库外证据 key |
| `QUALITY_FLAKY_EVIDENCE_KEY_ID` | 建议显式设置 | 例如 `probe-evidence-key-v1` |
| `QUALITY_FLAKY_GIT_REMOTE` | 是 | 例如 `gitlab`；只允许简单远端名 |
| `REQUESTS_CA_BUNDLE` | 私有 CA 时必填 | Jenkins HTTPS 入口 CA 文件 |
| `QUALITY_FLAKY_DASHBOARD_HOST` | 否 | 默认且只允许 `127.0.0.1`/`::1` |
| `QUALITY_FLAKY_DASHBOARD_PORT` | 否 | 默认 `8765` |

Jenkins HTTP `8080` 不能直接作为 `QUALITY_FLAKY_JENKINS_ORIGIN`；配置校验要求 HTTPS。使用企业入口或受控反向代理，并让 `REQUESTS_CA_BUNDLE` 指向签发证书的 CA。

PowerShell 启动模板：

```powershell
$controllerRoot = 'D:\API_CASE_CONTROLLER'
$databasePath = 'D:\API_CASE_DATA\flaky\flaky.sqlite3'
$secretRoot = 'D:\JenkinsSecrets'

Set-Location $controllerRoot
$env:QUALITY_FLAKY_TRIGGER_ENABLE = '1'
$env:QUALITY_FLAKY_JENKINS_ORIGIN = 'https://localhost:8443'
$env:QUALITY_FLAKY_JENKINS_JOB = 'quality-probe'
$env:QUALITY_FLAKY_JENKINS_CREDENTIAL_FILE = "$secretRoot\dashboard-jenkins-auth.txt"
$env:QUALITY_FLAKY_CONTROLLER_COMMIT = (git rev-parse HEAD).Trim()
$env:QUALITY_FLAKY_CONTROLLER_JENKINSFILE_SHA256 = (Get-FileHash -Algorithm SHA256 Jenkinsfile.probe).Hash.ToLowerInvariant()
$env:QUALITY_FLAKY_CSRF_SECRET_FILE = "$secretRoot\dashboard-csrf.key"
$env:QUALITY_FLAKY_EVIDENCE_HMAC_KEY_FILE = "$secretRoot\flaky-probe-evidence.key"
$env:QUALITY_FLAKY_EVIDENCE_KEY_ID = 'probe-evidence-key-v1'
$env:QUALITY_FLAKY_GIT_REMOTE = 'gitlab'
$env:REQUESTS_CA_BUNDLE = 'D:\PKI\jenkins-ca.crt'

.\.venv\Scripts\python.exe -m quality.cli flaky-dashboard `
  --db $databasePath `
  --host 127.0.0.1 `
  --port 8765
```

自动关闭通过同一 Python 解释器启动 `python -m quality.cli flaky-recovery-close`，所以 Dashboard 服务进程必须持续继承上述 Git 远端和 Evidence key 配置。

## 8. 主体流水线的 Skip 配置

治理关闭与 Skip 的关系由主体 Smoke 流水线配置决定：

```text
QUALITY_FLAKY_AUTO_SKIP_ENABLE=1
QUALITY_FLAKY_SKIP_MODE=enforce
QUALITY_FLAKY_DB_PATH=<同一治理数据库绝对路径>
```

`shadow` 只记录 would-skip，不会真实跳过；`off` 完全关闭。治理记录关闭后，下一次新运行生成的不可变决策计划不再包含该用例。已经开始的运行不会在中途改变 Skip 决策。

## 9. 启动后检查

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health/live
Invoke-RestMethod http://127.0.0.1:8765/health/ready
git ls-remote --exit-code gitlab refs/heads/dev3
```

预期：

- `/health/live` 返回 `live`；
- `/health/ready` 返回 `ready` 且数据库检查通过；
- 首页活动治理卡片显示“启动流水线验证”；
- `READY_TO_CLOSE` 卡片显示“合并 dev3 并关闭”；
- 页面不展示数据库路径、Secret、Jenkins Token 或原始证据签名。

执行真实验收时使用专门准备的 Flaky 测试分支，并确认该提交允许 fast-forward 到 dev3。点击合并按钮前，记录 dev3 和修复分支 SHA；完成后确认：

```powershell
git ls-remote --exit-code gitlab refs/heads/dev3
.\.venv\Scripts\python.exe -m quality.cli flaky-db-check --db $databasePath
```

页面中的治理记录应转为 `CLOSED` 并从待治理列表移除。

## 10. 失败处理

| 前端提示 | 后端原因 | 处理方式 |
| --- | --- | --- |
| 找不到修复分支 | `target_head_unavailable` | 确认分支已推送到 GitLab，检查远端与凭据 |
| 修复分支已变化 | `verified_branch_head_mismatch` | 对新 SHA 重新发起 Probe |
| dev3 已变化 | `dev3_not_fast_forward` | 基于最新 dev3 更新修复分支并重新验证 |
| GitLab 拒绝合并 | `git_merge_rejected` | 检查 protected branch、服务账号和非 fast-forward 状态 |
| 自动关闭失败 | `automatic_close_failed` | 不回退 dev3；修复密钥/数据库问题后再次点击，流程可识别已合并 SHA并只重试关闭 |

合并与 SQLite 关闭不能组成单个跨系统事务，因此顺序固定为“先确认并更新 dev3，再执行关闭”。如果 dev3 已更新而关闭失败，Skip 仍保持，不会提前放行；禁止通过重置 dev3 处理，应恢复关闭条件后幂等重试。

## 11. 迁移步骤

1. 在源环境停止 Dashboard，并等待/终止所有 Probe 写入；记录 GitLab dev3 SHA、控制端 SHA、Jenkinsfile SHA-256 和数据库路径。
2. 离线备份治理 SQLite；如果存在活动或 `READY_TO_CLOSE` attempt，同时备份原 Evidence key 和 key id。
3. 在目标环境准备独立控制端 checkout、Python 依赖、GitLab 远端和最小权限 Git 凭据。
4. 迁移数据库到仓库外本地磁盘，运行 `flaky-db-migrate`，再运行 `flaky-db-check`。
5. 复建 Jenkins `quality-probe`、两个隔离节点、SCM 凭据、Target API Key 和 Evidence Secret file。
6. 配置 Jenkins HTTPS 入口与 CA，使用 Dashboard 服务账号验证 Job Read/Build/Cancel。
7. 设置 Dashboard 环境变量并以前台方式首次启动，确认 `/health/ready` 后再交给服务管理器。
8. 用隔离治理记录执行一次完整前端验收；不要用未准备的业务分支试验 dev3 合并。
9. 验收通过后再开启主体流水线的 `QUALITY_FLAKY_AUTO_SKIP_ENABLE=1` 与 `QUALITY_FLAKY_SKIP_MODE=enforce`。

## 12. 停用与回退

- 禁止新建 Probe：设置 `QUALITY_FLAKY_TRIGGER_ENABLE=0` 并重启 Dashboard。
- 禁止所有看板写操作：停止 Dashboard；仅关闭 Trigger 不会撤销已经 `READY_TO_CLOSE` 的合并入口。
- 立即停止后续运行应用治理 Skip：设置 `QUALITY_FLAKY_AUTO_SKIP_ENABLE=0` 或 `QUALITY_FLAKY_SKIP_MODE=off`。
- 数据库回退：停止所有写者，只恢复到新路径，执行迁移与检查后再切换；不要覆盖正在使用的数据库。
- 合并成功但关闭失败：保留 dev3，修复配置后重试自动关闭或使用同一 `flaky-recovery-close` 门禁，不要 force push 或人工篡改治理表。

## 13. 迁移验收清单

- [ ] Dashboard 只监听 loopback。
- [ ] 三个 Secret 文件互不复用、位于仓库外且 ACL 受限。
- [ ] Jenkins Origin 使用可信 HTTPS，CA 可验证。
- [ ] GitLab 容器内外 URL 分别可达。
- [ ] Dashboard Git 身份能普通更新 dev3，但不能 force push。
- [ ] Probe Controller 与 Target 节点、OS 身份隔离。
- [ ] Target 无法读取治理数据库和控制端 Secret。
- [ ] 控制端 HEAD、干净工作树和 `Jenkinsfile.probe` SHA-256 匹配配置。
- [ ] SQLite 位于仓库/Workspace 外且 `flaky-db-check` 通过。
- [ ] Jenkins Job 只有三个固定参数，并禁止并发构建。
- [ ] 前端分支验证、失败提示、READY 门禁、合并和自动关闭均已验收。
- [ ] 关闭后仅影响后续新生成的 Skip 决策计划。
