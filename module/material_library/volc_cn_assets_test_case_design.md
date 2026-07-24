# 国内官key素材库接口测试用例设计

## 1. 测试范围

文档地址：`https://pre.juhemoxing.com/docs/api/volc-cn-assets`

覆盖接口：

| 模块 | 接口 |
| --- | --- |
| 素材组 | `POST /v1/volc/assets/groups` |
| 素材组 | `POST /v1/volc/assets/groups/list` |
| 素材组 | `GET /v1/volc/assets/groups/{group_id}` |
| 素材组 | `POST /v1/volc/assets/groups/{group_id}/update` |
| 素材组 | `POST /v1/volc/assets/groups/{group_id}/delete` |
| 素材 | `POST /v1/volc/assets` |
| 素材 | `POST /v1/volc/assets/list` |
| 素材 | `GET /v1/volc/assets/{asset_id}` |
| 素材 | `POST /v1/volc/assets/{asset_id}/update` |
| 素材 | `POST /v1/volc/assets/{asset_id}/delete` |
| 真人认证 | `POST /v1/volc/assets/visual-validate/sessions` |
| 真人认证 | `GET /v1/volc/assets/visual-validate/sessions/{session_id}` |
| 真人认证 | `GET /v1/volc/assets/visual-validate/results/{session_id}` |
| 视频联动 | `POST /v1/media/generations`，使用 `asset://asset-volc-cn-*` |
| 视频联动 | `GET /v1/media/tasks/{task_id}` |

## 2. 测试数据

| 数据项 | 建议值 | 说明 |
| --- | --- | --- |
| `ProjectName` | `default` | 素材组、素材、认证会话保持一致 |
| AIGC 组名 | `api-case-volc-group-{uuid}` | 每次运行唯一，避免脏数据冲突 |
| 素材名 | `api-case-volc-asset-{uuid}` | 每次运行唯一 |
| 素材图片 URL | `https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg` | 普通 AIGC 素材 |
| 真人图片 URL | `https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg` | 真人素材流程参考 |
| 视频模型 | `doubao-seedance-2-0-fast-260128` | 可补充 `doubao-seedance-2-0-mini-260615` 复用验证 |

## 3. 正向流程用例

| 用例 ID | 用例名称 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| VC-AIGC-001 | 创建 AIGC 素材组 | API Key 有效 | 调用 `POST /v1/volc/assets/groups`，传 `Name`、`Description`、`GroupType=AIGC`、`ProjectName=default` | HTTP 200；`Result.Id` 存在；ID 前缀为 `group-volc-cn-`；不暴露上游 AK/SK |
| VC-AIGC-002 | 上传图片素材 | 已创建 AIGC 素材组 | 调用 `POST /v1/volc/assets`，传 `GroupId`、公网 `URL`、`Name`、`AssetType=Image`、`ProjectName=default` | HTTP 200；`Result.Id` 存在；ID 前缀为 `asset-volc-cn-` |
| VC-AIGC-003 | 轮询素材状态到 Active | 已上传素材 | 每 3-5 秒调用 `GET /v1/volc/assets/{asset_id}?ProjectName=default` | `Result.Status` 最终为 `Active`；若为 `Failed`，响应包含可读失败原因 |
| VC-AIGC-004 | AIGC 素材用于视频生成 | 素材状态为 `Active` | 调用 `POST /v1/media/generations`，`content[].image_url.url` 使用 `asset://{asset_id}` | 创建任务成功；轮询 `/v1/media/tasks/{task_id}` 最终 `succeeded`；结果包含视频 URL |
| VC-AIGC-005 | 素材 ID 跨 fast/mini 复用 | 素材状态为 `Active` | 分别用 fast 与 mini 模型提交相同 `asset://{asset_id}` | 两个模型均能正常提交任务；不要求每次生成都成功，但不能因素材 ID 不兼容失败 |

## 4. 素材组管理用例

| 用例 ID | 用例名称 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| VC-GRP-001 | 按 GroupType 查询素材组列表 | 已创建 AIGC 组 | `POST /v1/volc/assets/groups/list`，`Filter.GroupType=AIGC` | HTTP 200；`Result.Items` 包含目标组；`Result.TotalCount >= 1` |
| VC-GRP-002 | 按 GroupIds 精确查询素材组列表 | 已创建 AIGC 组 | `POST /groups/list`，`Filter.GroupIds=[group_id]` | HTTP 200；返回列表包含目标 `group_id` |
| VC-GRP-003 | 按 Name 模糊查询素材组列表 | 已创建 AIGC 组 | `POST /groups/list`，`Filter.Name` 传组名关键字 | HTTP 200；返回列表包含目标组 |
| VC-GRP-004 | 查询素材组详情 | 已创建 AIGC 组 | `GET /v1/volc/assets/groups/{group_id}?ProjectName=default` | HTTP 200；`Result.Id` 等于路径 ID；`Result.Name`、`Result.GroupType` 正确 |
| VC-GRP-005 | 更新素材组名称和描述 | 已创建 AIGC 组 | `POST /groups/{group_id}/update` 修改 `Name`、`Description` | HTTP 200；再次查询详情，名称和描述已持久化 |
| VC-GRP-006 | 删除空素材组 | 组内素材已删除 | `POST /groups/{group_id}/delete` | HTTP 200；删除后详情查询返回 404 |
| VC-GRP-007 | 删除素材组幂等 | 素材组已删除 | 再次调用 `POST /groups/{group_id}/delete` | 返回 200 空结果，或返回文档约定的资源不存在错误；实际结果需要记录为契约 |

## 5. 素材管理用例

| 用例 ID | 用例名称 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| VC-AST-001 | 按 GroupIds 查询素材列表 | 已上传素材 | `POST /v1/volc/assets/list`，`Filter.GroupIds=[group_id]` | HTTP 200；`Result.Items` 包含目标素材 |
| VC-AST-002 | 按 Status 查询素材列表 | 已上传素材 | `POST /assets/list`，`Filter.Statuses=["Active","Processing"]` | HTTP 200；返回素材状态属于查询条件 |
| VC-AST-003 | 按 Name 查询素材列表 | 已上传素材 | `POST /assets/list`，`Filter.Name` 传素材名关键字 | HTTP 200；返回列表包含目标素材 |
| VC-AST-004 | 查询素材详情 | 已上传素材 | `GET /v1/volc/assets/{asset_id}?ProjectName=default` | HTTP 200；`Result.Id`、`Result.GroupId`、`Result.Name`、`Result.AssetType` 正确 |
| VC-AST-005 | 更新素材名称 | 已上传素材 | `POST /assets/{asset_id}/update` 修改 `Name` | HTTP 200；再次查询详情，`Name` 已持久化 |
| VC-AST-006 | 删除素材 | 已上传素材 | `POST /assets/{asset_id}/delete` | HTTP 200；删除后详情查询返回 404 |
| VC-AST-007 | 删除素材幂等 | 素材已删除 | 再次调用 `POST /assets/{asset_id}/delete` | 返回 200 空结果，或返回文档约定的资源不存在错误；实际结果需要记录为契约 |

## 6. 真人认证用例

| 用例 ID | 用例名称 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| VC-VV-001 | 创建真人认证会话 | API Key 有效，服务端已配置回调地址 | `POST /v1/volc/assets/visual-validate/sessions`，传 `ProjectName=default` | HTTP 200；返回 `Result.SessionId`、`Result.H5Link`；响应不暴露 BytedToken |
| VC-VV-002 | 查询 pending 认证会话 | 已创建会话，用户未完成 H5 | `GET /visual-validate/sessions/{session_id}` | HTTP 200；`Result.Status=pending`；包含 `SessionId`、`ProjectName`、`CreateTime`、`UpdateTime` |
| VC-VV-003 | 用户完成认证后查询会话状态 | 已打开 H5 并完成认证 | 轮询 `GET /visual-validate/sessions/{session_id}` | `Status` 变为 `callback_received` 或 `group_ready` |
| VC-VV-004 | 获取真人素材组 | 认证会话已完成 | `GET /visual-validate/results/{session_id}` | HTTP 200；返回 `Result.GroupId`；组 ID 前缀为 `group-volc-cn-`；组类型为 `LivenessFace` |
| VC-VV-005 | 真人素材上传一致性校验 | 已获得真人素材组 | 使用真人 `GroupId` 上传真人图片素材 | 匹配时素材可进入 `Active`；不匹配时返回可读错误，如 `FaceMismatch` |

## 7. 异常与边界用例

| 用例 ID | 用例名称 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| VC-NEG-001 | 创建素材组缺少 `Name` | `POST /groups` 不传 `Name` | HTTP 400；错误信息明确；不创建资源 |
| VC-NEG-002 | 创建素材组 `Name` 超长 | `Name` 超过 64 字符 | HTTP 400；提示字段长度非法 |
| VC-NEG-003 | 创建素材组 `Description` 超长 | `Description` 超过 300 字符 | HTTP 400；提示字段长度非法 |
| VC-NEG-004 | 非法 `GroupType` | `GroupType=InvalidType` | HTTP 400；不创建资源 |
| VC-NEG-005 | 素材上传缺少 `GroupId` | `POST /assets` 不传 `GroupId` | HTTP 400；错误信息明确 |
| VC-NEG-006 | 素材上传缺少 `URL` | `POST /assets` 不传 `URL` | HTTP 400；错误信息明确 |
| VC-NEG-007 | 素材上传 Base64 | `URL` 传 base64 或 `data:image/...` | HTTP 400；提示官方不支持 Base64 |
| VC-NEG-008 | 素材上传非法 URL | `URL` 传不可访问地址 | HTTP 400/502；错误信息可读 |
| VC-NEG-009 | 非法 `AssetType` | `AssetType=Document` | HTTP 400；错误信息明确 |
| VC-NEG-010 | 查询不存在素材组 | 随机 `group-volc-cn-not-exist-*` 查询详情 | HTTP 404；错误码或消息为资源不存在 |
| VC-NEG-011 | 查询不存在素材 | 随机 `asset-volc-cn-not-exist-*` 查询详情 | HTTP 404；错误码或消息为资源不存在 |
| VC-NEG-012 | 查询不存在认证会话 | 随机 `session-volc-cn-not-exist-*` 查询会话 | HTTP 404；错误码或消息为资源不存在 |
| VC-NEG-013 | 未 Active 素材用于视频 | 上传后立即使用 `asset://{asset_id}` 提交视频 | HTTP 409 或任务失败；提示素材未 Active |
| VC-NEG-014 | 删除非空素材组 | 组内存在素材时直接删除组 | 推荐预期为拒绝并返回错误；若实际上游级联删除，需要记录为产品契约差异 |
| VC-NEG-015 | 跨火山账号素材混用 | 同一视频请求混用不同 channel key 下的素材 | HTTP 409；不回退其他 key |

## 8. 鉴权与隔离用例

| 用例 ID | 用例名称 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| VC-SEC-001 | 无鉴权访问 | 移除 `Authorization` 请求任一素材接口 | HTTP 401 或 403；不返回业务数据 |
| VC-SEC-002 | 无效 API Key | 使用无效 Bearer Token 请求任一接口 | HTTP 401 或 403；不返回业务数据 |
| VC-SEC-003 | 跨账号查询素材组 | A 账号创建组，B 账号查询详情 | HTTP 403 或 404；不泄露资源详情 |
| VC-SEC-004 | 跨账号查询素材 | A 账号创建素材，B 账号查询详情 | HTTP 403 或 404；不泄露资源详情 |
| VC-SEC-005 | 跨账号删除资源 | A 账号创建资源，B 账号删除 | HTTP 403 或 404；A 账号资源仍存在 |

## 9. 错误处理与安全输出用例

| 用例 ID | 用例名称 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| VC-ERR-001 | 国内 AK/SK 未配置 | 在未配置国内素材通道环境调用创建组 | HTTP 503；错误信息说明服务不可用或配置缺失 |
| VC-ERR-002 | 回调地址未配置 | 调用创建真人认证会话 | HTTP 503；错误信息说明回调地址不可用 |
| VC-ERR-003 | 上游限流 | 高频调用创建或查询接口 | HTTP 429；响应可读；建议客户端退避重试 |
| VC-ERR-004 | 上游调用失败 | 构造上游失败场景 | HTTP 502；错误信息可读 |
| VC-ERR-005 | 错误响应不泄露内部信息 | 对所有 4xx/5xx 响应检查文本 | 不包含 `traceback`、`stack trace`、`sql`、`AK`、`SK`、内部 URL、数据库错误 |

## 10. 推荐执行分层

### P0 冒烟

1. 创建 AIGC 素材组。
2. 上传图片素材。
3. 查询素材详情并轮询到 `Active`。
4. 使用 `asset://asset_id` 提交视频任务。
5. 删除素材。
6. 删除素材组。

### P1 管理接口回归

1. 素材组列表、详情、更新、删除。
2. 素材列表、详情、更新、删除。
3. 删除后 404。
4. 删除幂等。
5. 不存在资源查询。

### P2 真人认证

1. 创建真人认证会话。
2. 人工或测试环境模拟完成 H5。
3. 查询会话状态。
4. 获取真人素材组。
5. 上传真人素材并校验 Active 或 FaceMismatch。

### P3 安全与异常

1. 鉴权失败。
2. 跨账号隔离。
3. 参数校验。
4. 上游失败、限流、配置缺失。
5. 错误响应脱敏。

## 11. 清理策略

1. 所有正向用例创建的 `asset_id` 和 `group_id` 都需要登记到清理列表。
2. 清理顺序固定为先删除素材，再删除素材组。
3. 真人认证生成的 `LivenessFace` 组需要单独登记，避免和普通 AIGC 组混淆。
4. 删除非空组观察用例默认不建议在常规回归中执行，避免上游级联删除导致本地映射残留。
5. 清理接口失败时记录资源 ID，便于人工补偿清理。

## 12. 与现有 smoke 脚本的关系

| 现有脚本 | 可参考内容 | 本设计补充点 |
| --- | --- | --- |
| `volc_cn_asset_pipeline_smoke.py` | AIGC 素材组创建、素材上传、素材轮询、视频生成联动、真人认证主流程 | 增加字段契约、异常、隔离、幂等、错误脱敏用例 |
| `volc_cn_asset_mgmt_smoke.py` | 管理接口全链路、列表过滤、更新、删除、删除后 404 | 补充真人认证、跨账号、非法参数、配置缺失、视频素材未 Active 场景 |

