# 迁移台账与孤儿清理（阶段三 B19 起）

> **阶段三迁移尚未发布**：`0003` / `0004` 在本阶段内仍有改动。任何跑过旧版的开发库、
> 测试库，`schema_migration` 里的 checksum 会与磁盘不一致——**这是检测机制在正常工作**，
> 不是缺陷。验证一律用**全新库**；开发库改动后重建即可。

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
| 新装（initdb 首启） | initdb（超级用户） | `deploy/init-schema-phase4.sql` 开头的 `CREATE EXTENSION IF NOT EXISTS vector` 自动执行 |
| 存量库升级 | **DBA** | `psql -U officeai -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"` 后重跑 `--apply` |
| 空库验证路径（`validate_migrations --run-pg` / G-11 临时库） | 超级用户 | 门禁/夹具在建库后、`--apply` 前以属主身份预建扩展 |

若扩展缺失，0009 会**中止并给出上面那条 DBA SQL**，不会静默跳过——
"迁移说成功、表却不存在"比报错难查得多。

扩展未安装时 `/readyz` 不报错（它只查 5 项既有依赖），记忆功能会在抽取/召回时降级；
`test_memory_schema_pg.py` 里有 `pg_extension` 版本断言（≥0.8）作为机械钉子。
