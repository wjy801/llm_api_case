# Flaky 治理 MVP 阶段 0：v2 数据审计报告

## 1. 审计范围

- 审计日期：2026-09-01。
- 输入：当前 `.env` 配置且实际存在的 Flaky SQLite v2 数据库。
- 方式：源数据库仅以 SQLite `mode=ro` 打开，使用 SQLite backup API 创建临时一致性快照，再由 `quality.flaky_v2_audit` 以 `mode=ro` 审计快照。
- 脱敏：报告不包含数据库绝对路径和原始 Jenkins Job 名；Job 名只保留不可逆短哈希标识。
- 临时快照：审计结束后自动删除，不进入 Git 或 Jenkins artifact。

快照信息：

| 项目 | 结果 |
| --- | --- |
| 大小 | 3,121,152 bytes |
| SHA-256 | `00b84d0b6b85c6fdc12314930e27c52237498772a9b1d78b6c0e8dfdec3b8da2` |
| SQLite quick_check | `ok` |
| foreign_key_check | 0 条违规 |
| Schema migration | `0001`、`0002`，名称和 checksum 均与当前仓库一致 |

## 2. 数据概览

| 数据 | 数量 |
| --- | ---: |
| v2 Run | 44 |
| Case observation | 1,717 |
| Flaky state | 132 |
| Transition | 281 |
| Governance | 0 |

Run 分布：

- 来源：Jenkins 44。
- 分支：`origin/dev3` 19，`origin/main` 25。
- 环境：`china` 43，`overseas` 1。
- 状态：finished 44；P0 完整性均为 complete。
- Job：2 个脱敏 Job，其中 `job-sha256-cedf9ab09afd` 19，`job-sha256-bae15a4be2d1` 25。

Observation 分布：

- 执行画像：parallel 793，serial 924。
- 结果：pass 1,514，fail 203。
- 失败分类：PRODUCT_DEFECT 72、TEST_DEFECT 3、CONFIGURATION 38、ENVIRONMENT 1、TRANSIENT 1、UNKNOWN 88。
- 其中基础设施类 observation 40 条、未知分类 observation 88 条；它们证明现有 v2 准入边界不足，迁移后不得直接进入 v3 检测窗口。

状态分布：

- OBSERVING 62。
- STABLE 35。
- SUSPECTED 9。
- CONFIRMED 26。
- 全部 132 条投影均为 CURRENT；current state 与 detected state 分布一致。

只读审计器同时输出逐条脱敏明细：每条 state 包含 `flaky_key`、自动/当前状态、projection 状态以及最近 observation 的稳定 ID、Run ID、结果、失败分类和时间；每条 governance 包含稳定 ID、状态、脱敏 owner/actor、到期时间、resolution 和 recovery anchor。当前快照没有 governance，因此该明细集合为空。

## 3. 一致性检查

以下检查结果均为 0：

- 缺失 projection。
- stale projection。
- 孤儿 transition。
- 孤儿 governance。
- 重复 ACTIVE/RECOVERING governance。
- state 最新 observation 关联错误。
- CLOSED governance 缺少 resolution 或 closed_at。

当前没有 governance 记录，因此不存在需要 owner 逐条确认的 RECOVERING、ACTIVE 或 CLOSED 历史记录。

## 4. 迁移处置结论

1. 44 个 v2 Run 及其 1,717 条 observation 全部按 `LEGACY_UNKNOWN` 保留，仅用于审计，不进入 v3 detection projection。
2. 现有 132 条 detected state 仅保存为 legacy 状态；v3 活动投影从首条合格 NORMAL 按 comparability fingerprint 重新建立。
3. 现有 26 条 CONFIRMED 不等同于人工隔离，不能据此产生 Skip。
4. 数据库中没有活动 governance，无需执行 RECOVERING 回退或人工 occurrence 处置。
5. 数据完整性允许进入阶段 1 的迁移实现和合成迁移测试，但本报告本身不授权生产迁移。

## 5. Go/No-Go

- **Go**：阶段 0 契约验收；阶段 1 本地 v3 Schema、状态机和 CLI 开发。
- **No-Go**：当前数据库生产迁移。必须先完成阶段 1 的 `0003`、显式迁移、跨进程单写者、备份恢复和迁移测试。
- **No-Go**：Shadow、Enforce Skip 和 Jenkins Probe；分别等待阶段 2、3、4 的独立门禁。
