# 交付说明：操作记录导出（矩阵第 4 项，2026-11-06）

对应 [gap-matrix-2026-11-03.md](file:///c:/Users/15864/Desktop/麦子/docs/gap-matrix-2026-11-03.md) §3.1 的准入基线第 4 项（B4）。

**矩阵第 4 项状态：已完成（2026-11-06，默认关闭，需显式开启）。**

客户现在可以按租户 + 时间范围把操作记录导出为 CSV 或 JSON Lines；导出过程分页流式写出、
有行数上限与限流、自身留痕且留痕不进入导出结果，权限与 RLS 双层生效。

---

## 1. 交付内容

| 能力 | 行为 | 开关 |
| --- | --- | --- |
| CSV 导出 | `GET /api/v1/identity/audit-logs/export?format=csv&start_at=…&end_at=…`，11 列、表头为中文 | `OA_AUDIT_LOG_EXPORT_ENABLED=false`（默认关） |
| JSON Lines 导出 | 同端点 `format=jsonl`，每行一个 JSON 对象（键集合与 CSV 列一一对应） | 同上 |
| 过滤 | `actor_id` / `action` / `action_group` / `resource_type` / `result`（均为可选精确匹配/域过滤） | 同上 |
| 导出留痕 | 每次导出写一条 `audit_log.export`：导出人、角色、格式、起止时间、结果条数、过滤条件 | 同上 |

**明确不做**：导出请求本身作为可下载的"任务/异步打包文件"（实时流式返回）；`ip_masked`
列不导出（沿用 U6 投影，库里虽有已掩码列但从未对外暴露）；不做二次脱敏掩码（见 §3）。

---

## 2. 大数据量：分页 + 流式 + 上限 + 限流

| 关注点 | 落地位置 | 行为 |
| --- | --- | --- |
| 不全量进内存 | `app/db/repositories/audit_repo.py::iter_audit_export` | 键集分页（`(occurred_at, id) > (?, ?)`，`ORDER BY occurred_at ASC, id ASC`，`LIMIT page_size`）逐页取；每页一个新短事务（`tenant_scope(read_only=True)`）；内存后端同样按键排序分页 |
| 流式写出 | `app/business/audit_export_service.py::stream` | `AsyncIterator[bytes]`，CSV 用 `StringIO + csv.writer` 每次写完 `seek/truncate`（单行复用缓冲），JSONL 逐行 `dumps + "\n"`；进程里同时只有"一页记录 + 一页字节" |
| 行数上限 | 同上 `prepare()` | 流式**之前**先 `COUNT(*)`；超过 `OA_AUDIT_LOG_EXPORT_MAX_ROWS`（默认 200000）直接 `20001` 并带 `data={rows, max_rows}`，**不截断** |
| 流中兜底 | 同上 `_guard_cap` | 万一计数与实际漂移，`written > max_rows` 记 `audit_export_cap_exceeded` 并中断连接（宁可失败也不静默截断） |
| 时间范围 | 同上 `prepare()` | 起止**必填**；跨度沿用 U6 的 90 天口径（`span_exceeds_limit`）；归一化为 UTC `...Z` |
| 限流 | `app/api/v1/identity.py` | `RateLimiter.check(tenant, user, "identity:audit-logs:export")`，默认 3 次 / 3600 秒（`OA_AUDIT_LOG_EXPORT_RATE_LIMIT` / `_WINDOW_SECONDS`），超限 `20006` |
| 索引 | `deploy/migrations/0016_audit_export.sql` | 新增 `idx_audit_log_tenant_time_id (tenant_id, occurred_at, id)`；既有索引是 `DESC` 且缺 `id`，同时间戳多行仍需额外排序，本索引让导出退化为索引顺序扫描 |

分页大小由 `OA_AUDIT_LOG_EXPORT_PAGE_SIZE` 控制（默认 500，范围 100–5000）。

---

## 3. 脱敏口径（取证结论，不自创规则）

| 证据 | 结论 |
| --- | --- |
| `app/schemas/identity.py::AuditLogItem` 类注释「字段透传，脱敏由业务层写入时完成」 | 读取侧不二次加工；敏感字段在**写入**时已约束 |
| `app/db/repositories/audit_recorder.py::AuditEntry.change_summary` 契约「只放业务字段，禁止放密钥/正文」 | 摘要里不会有密钥/正文 |
| 抽查既有写入点（成员/部门/知识库/插件/订单） | `change_summary` 只有角色、状态、条数、名称、金额一类字段，无手机号/邮箱/证件号 |
| U6 查询的 SQL 投影 | 不含 `ip_masked`（该列虽在库里，但从未对外暴露） |
| 导出字段 | 与 U6 对齐并保留 `trace_id`；**不含** `tenant_id`（导出永远单租户，多一列只是把租户标识外带） |

因此**不新增掩码规则**。唯一的格式级防护是 **CSV 公式注入前缀**（`_csv_cell`：
以 `=` `+` `-` `@` `\t` `\r` 开头的单元格前加 `'`），避免电子表格把单元格当公式执行——
这是格式安全，不是字段脱敏。

CSV 列（顺序固定）：`log_id, occurred_at, actor_id, actor_role, action,
action_display_name, resource_type, resource_id, result, change_summary, trace_id`。
`change_summary` 在 CSV 中是 JSON 字符串，在 JSONL 中保持对象。

---

## 4. 留痕与"不递归"

- `prepare()` 在**流式开始之前**写一条 `audit_log.export`（`ACTION_AUDIT_LOG_EXPORT`），
  `change_summary` 含 `format / start_at / end_at / rows / filters`，`resource_id` 是本次
  导出的 `export_id`（响应头 `X-Export-Id` 同值）。
- 写留痕失败即整个请求失败（不吞异常，与 TD1 企业数据导出口径一致）——不允许"偷偷导出、没留痕"。
- `audit_repo.EXPORT_EXCLUDED_ACTIONS = {audit_log.export}`：`count_audit_logs` 与
  `iter_audit_export` **都**排除该动作，所以导出文件里永远不含导出记录，也不会因"导出产生的新记录"
  让下一次导出条数膨胀。
- 导出动作已登记进动作目录（`ACTION_CATALOG` 的 `audit_log` 域），`GET /audit-logs/actions`
  可见，前端筛选可识别。

---

## 5. 权限、租户与 RLS

| 边界 | 落地位置 | 行为 |
| --- | --- | --- |
| 权限点 | `app/services/identity_service.py::export_audit_logs` | 复用既有 `audit:log:read`（`get_policy_service().require_point`），不新增权限点 |
| 跨租户 | 同上 | `tenant_id` 恒取令牌（`replace(request, tenant_id=auth.tenant_id)`），请求参数里**没有** `tenant_id` |
| 平台令牌 | 同上 | `is_platform_scope` 命中即 `20003`（平台不是租户，`__platform__` 行不属于任何客户，不能导出） |
| 应用层过滤 | `audit_repo._conditions` | SQL 显式 `tenant_id = :tenant_id`（U6 与导出共用同一条件构造器，不复制粘贴） |
| RLS | `tenant_scope(..., read_only=True)` | 每次分页查询都在租户作用域短事务内，RLS 与应用层条件**双重生效** |
| RLS 留存校验 | `deploy/migrations/0016_audit_export.sql` | `ENABLE` + `FORCE` 后复核 `p_tenant_isolation`；策略缺失则按既有表达式重建（不新增策略、不放宽匹配） |
| 响应头 | `app/api/v1/identity.py` | `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`，避免代理/浏览器缓存审计数据 |

---

## 6. 接口与数据模型变更（只增不改）

| 变更 | 位置 | 兼容性 |
| --- | --- | --- |
| 1 个新端点 | `app/api/v1/identity.py`（`GET /audit-logs/export`） | 新增；未改 U6 查询与任何既有路径/字段/响应码 |
| 1 个新动作码 | `app/core/audit_actions.py`（`audit_log.export` / 展示名「导出操作记录」） | 新增动作，不影响既有动作编码 |
| 1 个新索引 | `deploy/migrations/0016_audit_export.sql` | 幂等 + 权限守卫；不改表结构、不改数据 |
| 回滚 | `deploy/migrations/rollback/0016_audit_export.down.sql` | 只 `DROP INDEX IF EXISTS`，**不触碰 RLS**（RLS 是阶段二既有结构） |
| 新库基线 | `deploy/init-schema-phase2.sql` | 同步 `CREATE INDEX IF NOT EXISTS`，全新库一次到位 |
| OpenAPI 基线 | `docs/api/baseline-b4-1.json` + `deploy/validate_openapi.py` | 新增 G10（精确 1 条、双向严格）；G1–G9 口径不变 |

**配置**（`OA_AUDIT_LOG_EXPORT_*`，全部写入 `deploy/.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OA_AUDIT_LOG_EXPORT_ENABLED` | `false` | 总开关；关闭时端点 `20003`，且**不写留痕** |
| `OA_AUDIT_LOG_EXPORT_MAX_ROWS` | `200000` | 单次导出行数上限（1–1000000） |
| `OA_AUDIT_LOG_EXPORT_PAGE_SIZE` | `500` | 键集分页每页条数（100–5000） |
| `OA_AUDIT_LOG_EXPORT_RATE_LIMIT` | `3` | 窗口内最多导出次数 |
| `OA_AUDIT_LOG_EXPORT_RATE_WINDOW_SECONDS` | `3600` | 限流窗口（60–86400 秒） |

---

## 7. 验证边界（**必读，不许把 CI 绿当功能已验证**）

| 环节 | 验证程度 | 说明 |
| --- | --- | --- |
| 服务逻辑与格式 | 已覆盖 | `tests/unit/test_audit_export_service.py`（9 条）：默认关闭且不留痕、起止必填、90 天跨度、格式非法、超上限不截断且不留痕、CSV 列顺序 + 公式注入 + 摘要 JSON、JSONL 键集合 + 摘要对象、留痕写入且二次导出条数不变/文件不含导出动作、内存租户隔离 |
| 端点与权限 | 已覆盖 | `tests/integration/test_audit_export_api.py`（8 条）：默认关闭、成员无权限、CSV 成功（行数头/`no-store`/留痕条数）、JSONL 排除导出留痕、平台令牌 403、起止必填与 90 天、超上限带 `data`、限流 429 |
| PG 键集分页 / RLS | **CI 验证（本地未跑）** | `tests/integration/test_audit_export_pg.py`：同时间戳多行的分页稳定性与完整性、A/B 租户隔离、`audit_log` 的 ENABLE+FORCE+`p_tenant_isolation`、0016 索引存在。本地无 PostgreSQL，未执行真实 DDL/RLS；由 CI Adapter 的 PG 服务校验 |
| 真实大数据量压测 | **未验证** | 只验证了"分页不重复不丢行"（小样本）与"超上限报错"；未在 20 万行真实数据上测吞吐与内存峰值 |
| 长期导出并发 | **未验证** | 未做多副本/多并发导出压测；限流后端沿用进程内实现（与既有 `ratelimit` 同口径，多副本前需切共享后端） |
| 脱敏 | 沿用既有口径 | 未对线上历史 `change_summary` 做全量抽查；若历史写入曾放入敏感字段，导出会原样带出（见 §3 证据链） |

结论：**服务行为、端点契约、权限与"不递归留痕"已由真实请求锁定；PG 路径与真实数据规模需按 §8 在部署环境验收。**

---

## 8. 5 分钟手工验收

1. `.env` 置 `OA_AUDIT_LOG_EXPORT_ENABLED=true`，重启 adapter。
2. 用组织管理员令牌 `GET /api/v1/identity/audit-logs/export?format=csv&start_at=2026-10-01T00:00:00Z&end_at=2026-11-06T00:00:00Z`
   → 期望 `200`、`content-type: text/csv`、有 `X-Export-Id` / `X-Export-Rows`、`Cache-Control: no-store`。
3. 同一请求换 `format=jsonl` → 每行一个 JSON，且 `x-export-rows` 与 CSV 那次一致（导出留痕不进导出集）。
4. `GET /api/v1/identity/audit-logs/export?format=csv&start_at=2026-10-01T00:00:00Z`（缺 `end_at`）
   → `400` / `20001`；把起点改到 2026-01-01 再试 → `400` / `20001`（90 天口径）。
5. 同一令牌连续第 4 次导出（默认 3 次/小时）→ `429` / `20006`。
6. 用普通成员令牌重复第 2 步 → `403` / `20003`；用平台令牌 → `403` / `20003`。
7. 置 `OA_AUDIT_LOG_EXPORT_ENABLED=false` 重启 → 第 2 步 `403` / `20003`，且 `audit_log` 里不新增 `audit_log.export`。
8. 导出后查 `GET /api/v1/identity/audit-logs`（U6，动作过滤 `audit_log.export`）→ 能看到本次导出留痕（人、范围、条数）。

---

## 9. 回退方式（出事 1 分钟内可退回）

| 现象 | 处置 |
| --- | --- |
| 导出拖慢数据库 | `.env` 置 `OA_AUDIT_LOG_EXPORT_ENABLED=false` 并重启 adapter；端点立刻 `20003`，既有查询不受影响 |
| 单租户导出过频 | 调小 `OA_AUDIT_LOG_EXPORT_RATE_LIMIT` / 调大窗口；无需改代码、无需重启以外的操作 |
| 单次导出过大 | 调小 `OA_AUDIT_LOG_EXPORT_MAX_ROWS`（超限报错，不截断） |
| 需要回滚索引 | 执行 `deploy/migrations/rollback/0016_audit_export.down.sql`（只删索引，DML/RLS 不受影响） |
| OpenAPI 门禁误报 | 不要改基线凑绿；按 §6 核对 `baseline-b4-1.json` 与实现是否一致（G10 双向严格） |
