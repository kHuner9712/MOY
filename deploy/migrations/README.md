# 迁移台账与孤儿清理（阶段三 B19 起）

> **发布状态**：阶段三早已验收，`0003` / `0004` 等阶段三迁移已冻结、不再改动；
> 迁移线已推进到 `0014_task_plan`（矩阵第 2 项计划确认，清单见下表）。
> 任何跑过未发布期旧版的开发库、测试库，`schema_migration` 里的 checksum 会与磁盘
> 不一致——**这是检测机制在正常工作**，不是缺陷。验证一律用**全新库**；开发库重建即可。

## 迁移清单（0005–0014，按执行顺序）

> 更早的 `0002`–`0004` 已冻结，用途见各 SQL 文件头注释：
> `0002_callback_idempotency_selfcheck`（回调幂等自检，原 `0002_billing_callback_unique` 改名，见 §4）、
> `0003_phase3_plugin_tables`（插件域 9 表）、`0004_phase3_task_versions`（任务版本表）。
> 全量迁移线为 0002–0014 共 13 个（0001 为 RESERVED 基线别名）；0014 为 `0014_task_plan.sql`。

| 迁移脚本 | 用途（摘自各 SQL 文件头注释） |
| --- | --- |
| `0005_phase3_artifact_content.sql` | `plugin_artifact.content` BYTEA 列：文件类产物（docx/xlsx/zip）字节落库，导出的是真 docx 而非文本预览 |
| `0006_plugin_artifact_content_type.sql` | `plugin_artifact.content_type` 列：下载产物声明真实 MIME，不再恒回落 `text/plain` |
| `0007_usage_event_dimensions.sql` | `usage_event` 增 `profile` / `provider` / `prompt_tokens` / `completion_tokens` 四个计量维度 |
| `0008_conversation_message.sql` | 会话与消息落库（`conversation` / `message`）：对话历史刷新可恢复，自研上下文组装的前提 |
| `0009_user_memory.sql` | 记忆内核三表（`user_memory` / `memory_embedding` / `user_memory_setting`），依赖 `vector` 扩展（见下文「pgvector 扩展由谁安装」） |
| `0010_background_job.sql` | 后台任务表：PG 队列 + `FOR UPDATE SKIP LOCKED`，承载记忆抽取、租户导出、保留期清理 |
| `0011_tenant_budget.sql` | 租户月度预算表（`tenant_budget`）：成本护栏读取，计费表零改动 |
| `0012_quota_hold.sql` | `document_meta` / `plugin_task` 增 `hold_reason` 额度挂起标记，超额挂起状态落库、重启/多副本不丢 |
| `0013_event_log.sql` | 会话事件日志 `event_log`（底座 T02，设计文档 §5.1）：16 个 HASH 分区、RLS 按租户隔离、只授 INSERT+SELECT（append-only）、`(tenant_id, session_id, seq)` + `(tenant_id, created_at)` 两个索引。**这是状态真源**（规格 §3.1 E1），删表等于删掉全部会话历史与审计证据——回滚前必须先导出 |
| `0014_task_plan.sql` | 通用任务「计划生成 → 确认」（矩阵第 2 项）：`plugin_task` 增 `plan_version` / `plan_expires_at` / `plan_confirmed_at` 三列，状态 CHECK 扩 `waiting_plan`，并建部分索引 `idx_task_plan_expiry`（清理器按到期时间扫描）。不新增表，RLS 沿用既有 `p_tenant_isolation`；回滚见 `rollback/0014_task_plan.down.sql` |

`rollback/` 目录自 `0006` 起为每个迁移配对 `*.down.sql` 回退脚本（`0005` 及更早无 down 文件）。

## 1. 台账三种状态

| 状态 | 含义 | 处置 |
| --- | --- | --- |
| `pending` | 磁盘有脚本、台账无记录 | `--apply` 执行 |
| `drifted` | 台账有记录、脚本内容已变 | 中止（重建库或补反向迁移），**不静默通过** |
| `orphan` | 台账有记录、**磁盘已无脚本** | 每次运行以 `[WARN]` 列出；确认后 `--prune-orphans --yes` 清理 |

## 2. 为什么孤儿必须可见

孤儿行现在会「沉默通过」，但换一台已初始化的库部署时两边台账就不一致，
排查成本极高（现象是"某台机器上少跑了一个迁移"，而不是报错）。
因此孤儿**每次运行都打印**，且只能通过显式入口清理。

## 3. 命令

```bash
# 只校验（会打印孤儿与别名信息）
python -m app.db.migrate --check

# 清理孤儿：默认 dry-run，看清清单后再加 --yes
python -m app.db.migrate --check --prune-orphans
python -m app.db.migrate --check --prune-orphans --yes
```

## 4. 版本改名

`0002_billing_callback_unique` 已改名 `0002_callback_idempotency_selfcheck`。
旧库无需手工改台账：`MIGRATION_ALIASES`（`app/db/migrate.py`）会把旧版本号认领到新脚本，
运行时打印 `[INFO] 版本别名 … → …`，旧行可用 `--prune-orphans` 清掉。
**只允许删孤儿**，其他台账行任何情况下都不动。

## 阶段四：pgvector 扩展由谁安装（**必读**）

`0009_user_memory.sql` 需要 `vector` 扩展。实测 pgvector 0.8.7 的
`pg_available_extension_versions.trusted = false` —— **应用角色即使拥有库属主权限也
建不了 `CREATE EXTENSION vector`**（`permission denied to create extension`）。

因此分工固定为：

| 场景 | 谁装扩展 | 怎么做 |
| --- | --- | --- |
| 新装（initdb 首启） | initdb（超级用户） | `deploy/init-schema-phase4.sql` 建表段之前的 `CREATE EXTENSION IF NOT EXISTS vector`（约第 153 行）自动执行 |
| 存量库升级 | **DBA** | `psql -U officeai -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"` 后重跑 `--apply` |
| 空库验证路径（`validate_migrations --run-pg` / G-11 临时库） | 超级用户 | 门禁/夹具在建库后、`--apply` 前以属主身份预建扩展 |

若扩展缺失，0009 会**中止并给出上面那条 DBA SQL**，不会静默跳过——
"迁移说成功、表却不存在"比报错难查得多。

扩展未安装时 `/readyz` 不报错（它只查 5 项既有依赖），记忆功能会在抽取/召回时降级；
`test_memory_schema_pg.py` 里有 `pg_extension` 版本断言（≥0.8）作为机械钉子。
