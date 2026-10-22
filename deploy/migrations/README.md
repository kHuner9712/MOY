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
