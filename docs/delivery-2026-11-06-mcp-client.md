# 交付说明：MCP 客户端（矩阵第 3 项，2026-11-06）

对应 [gap-matrix-2026-11-03.md](file:///c:/Users/15864/Desktop/麦子/docs/gap-matrix-2026-11-03.md) §3.1 的准入基线第 3 项（B3）。
实施计划：[plan-b3-mcp-client.md](file:///c:/Users/15864/Desktop/麦子/.trae/documents/plan-b3-mcp-client.md)。

**矩阵第 3 项状态：已完成（2026-11-06，默认关闭，需显式开启）。**

Moy 现在可以作为 MCP client 连接外部 MCP server，把白名单内的工具暴露给插件步骤与任务引擎；外部服务由运营后台声明式登记，凭据只存引用。

---

## 1. 交付内容

| 能力 | 行为 | 开关 |
| --- | --- | --- |
| 外部服务登记 | `POST /api/v1/ops/mcp-servers`：名称 / 传输 / 地址 / 凭据引用 / 工具白名单 / 启用态 / 超时 / 输出上限 | `OA_MCP_ENABLED=false`（默认关） |
| 登记列表 | `GET /api/v1/ops/mcp-servers?tenant_id=`，**不返回凭据引用**，只给 `credential_configured` | 同上 |
| 删除登记 | `DELETE /api/v1/ops/mcp-servers/{server_id}?tenant_id=` | 同上 |
| 连通性探测 | `POST .../{server_id}/probe`：真实 `initialize → tools/list`，只返回白名单交集与耗时 | 同上 |
| 插件步骤调用 | `tool_type="mcp"` + `operation="<server>.<tool>"`；加载期强制 `confirm`，运行期复核 `personal.tool.use` | 同上 |

**明确不支持**：legacy SSE 传输（登记即拒）；第三方工具市场/签名（V1.1）；stdio 在生产/预发使用。

---

## 2. 传输方式取舍

| 传输 | 档位 | 实现 | 为什么 |
| --- | --- | --- | --- |
| streamable HTTP | **A（生产默认）** | POST JSON-RPC；`Mcp-Session-Id` 维持会话；响应支持 `application/json` 与 `text/event-stream` 两种帧 | 唯一能穿过现有网关/WAF、可审计、可超时、可限流的形态；不要求 Moy 与外部服务同机 |
| stdio | **B（本地/测试，默认关）** | `asyncio.create_subprocess_exec`，stdin/stdout 按行 JSON-RPC；stderr 只排空计字节 | 外部代码=外部进程，风险最高；仅允许测试与本地可信环境，且必须命中命令白名单；staging/production 即使打开也会被服务层拒绝 |
| legacy SSE | **0（不支持）** | 登记时直接拒绝 | 2025-03-26 起已被 streamable HTTP 取代；长连接语义与现有超时/审计模型冲突，支持它是净负担 |

---

## 3. 安全边界（逐条对应需求）

| 边界 | 落地位置 | 行为 |
| --- | --- | --- |
| 权限声明 | `app/api/v1/ops_mcp.py` | 列表 `ops:console:access`；登记/删除/探测 `ops:settings:manage`；企业令牌一律 `20003` |
| 不绕过授权 | `app/plugins/loader.py`、`app/services/mcp_tool_bridge.py` | MCP 只能从插件步骤进入；加载期 `mcp` 视为对外动作，必须有 `confirm`；运行期再复核 `ctx.has("personal.tool.use")` |
| 不绕过 `tenant_scope` | `app/db/repositories/mcp_repo.py` | 所有 SQL 带 `tenant_clause()`，读写都在 `tenant_scope(tenant_id)` 内；`__platform__` 不允许登记 |
| 超时 | `app/clients/mcp_client.py` | 建连 `OA_MCP_CONNECT_TIMEOUT_MS`（默认 5s）+ 总超时 `OA_MCP_DEFAULT_TIMEOUT_MS`（默认 15s），超时归一为 `timeout` |
| 输出上限 | 同上 | 逐块累计超过 `OA_MCP_MAX_OUTPUT_BYTES`（默认 64KB）立即中止，判 `output_too_large`；**不截断后继续使用** |
| 错误隔离 | `app/services/mcp_runner.py` | 外部异常全部收敛为 `McpOutcome(ok=False)`；`retryable=False`，步骤失败不影响同一任务的其它步骤 |
| 失败可观测 | `probe` + 结构化日志 | 返回 `ok/reason/duration_ms/tool_count/tools`；日志只记 `server_id/name/tool/reason/duration_ms/size_bytes`，**不含地址与凭据** |
| 凭据不落明文 | `app/business/mcp_service.py`、`credential_service.resolve_ref` | 只接受 `cred_xxx` 引用；`sk-...` 之类明文直接 20001；解密值只存在 `McpTarget.credential`（`repr=False`），不进响应/日志 |
| URL 形态 | 同上 | 拒绝 URL 内 userinfo；拒绝 `token/api_key/secret/...` 等敏感 query；非 loopback 明文 http 需 `OA_MCP_ALLOW_INSECURE_HTTP=true` |
| 响应不回引用 | `app/schemas/mcp.py` | `McpServerItem` 只有 `credential_configured`，没有 `credential_ref` 字段 |

---

## 4. 接口与数据模型变更（只增不改）

| 变更 | 位置 | 兼容性 |
| --- | --- | --- |
| 4 个运营端点 | `app/api/v1/ops_mcp.py` | 全部新增；未改任何既有路径/字段/响应码 |
| `mcp_server` 表 | `deploy/migrations/0015_mcp_server.sql` | 新增表；幂等守卫；启用并强制 `p_tenant_isolation` |
| 回滚 | `deploy/migrations/rollback/0015_mcp_server.down.sql` | 删除策略 → 索引 → 表 |
| 新库零 DDL | `deploy/init-schema-phase3.sql` | 同步建表与索引，`strict_tables` 加 `mcp_server` |
| 插件步骤窄例外 | `app/plugins/actions.py`、`loader.py` | `tool_type="mcp"` 走 `MCP_OPERATION_PATTERN`，不查静态 `tool_registry.yaml`；其余 `tool_type` 行为不变 |
| OpenAPI 基线 | `docs/api/baseline-b3-4.json` + `deploy/validate_openapi.py` | 新增 G9（精确 4 条、双向严格）；G1/G2/G4/G8 口径不变 |

**RLS**：`mcp_server` 启用 RLS，策略表达式含 `current_setting('app.tenant_id', TRUE)` 与 `'__platform__'` 分支；`validate_schema_retention.py` 无保留期列，口径不变。

**配置**（`OA_MCP_*`，全部写入 `deploy/.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OA_MCP_ENABLED` | `false` | 总开关；关闭时登记端点 20003，任务步骤直接失败，不发任何外部连接 |
| `OA_MCP_ALLOW_STDIO` | `false` | stdio 总开关；生产/预发仍拒绝 |
| `OA_MCP_STDIO_ALLOWED_COMMANDS` | 空 | stdio 可执行文件白名单（basename） |
| `OA_MCP_ALLOW_INSECURE_HTTP` | `false` | 非 loopback 明文 http（联调用） |
| `OA_MCP_DEFAULT_TIMEOUT_MS` | `15000` | 单次调用总超时 |
| `OA_MCP_CONNECT_TIMEOUT_MS` | `5000` | 建连超时 |
| `OA_MCP_MAX_OUTPUT_BYTES` | `65536` | 响应体上限 |
| `OA_MCP_MAX_SERVERS_PER_TENANT` | `10` | 单租户登记上限 |

---

## 5. 验证边界（**必读，不许把 CI 绿当功能已验证**）

| 环节 | 验证程度 | 说明 |
| --- | --- | --- |
| 协议客户端 | 已覆盖 | `tests/unit/test_mcp_client.py`：真实本地 HTTP + **真实 stdio 子进程**；含 SSE 帧、鉴权、超大、超时、连接失败 |
| 登记校验 / 凭据 / 白名单 | 已覆盖 | `tests/unit/test_mcp_service.py`：默认关闭、SSE 拒绝、URL 凭据拒绝、明文凭据拒绝、租户隔离、白名单、真实 probe、凭据失败可观测 |
| 插件步骤全链路 | 已覆盖 | `tests/integration/test_mcp_task_step.py`：`confirm` 强制、非法 operation、真实调用产物、工具自报失败隔离、参数 32KB 上限、server 停掉后 `failed` 不抛 |
| 运营端点权限 | 已覆盖 | `tests/integration/test_ops_mcp_api.py`：企业令牌四端点 20003；平台令牌登记→列表→真实探测→删除；响应不含凭据 |
| PG 仓储 / RLS | **CI 验证（本地未跑）** | `tests/integration/test_mcp_repo_pg.py` + `validate_migrations.py --run-pg`。本地无 PostgreSQL 容器，未执行真实 DDL/回滚/RLS；由 CI Adapter 的 PG 服务校验 |
| **真实第三方 MCP server** | **未验证** | 只用本地最小 server（HTTP + stdio 真连）；未连过任何生产/第三方 MCP 服务（无可用凭据与地址） |
| legacy SSE | 设计不支持 | 登记即拒，无实现 |
| 多副本 / 高并发 | 未验证 | 未做多副本探测与压力测试 |

结论：**协议、安全边界、失败隔离与运营权限已由真实本地 server 锁定；真实第三方服务、生产 PG 与并发需按第 6 节在部署环境验收。**

---

## 6. 5 分钟手工验收

1. 部署环境 `.env` 置 `OA_MCP_ENABLED=true`，重启 adapter。
2. 用平台管理员令牌 `POST /api/v1/ops/mcp-servers`，登记一个内网 MCP server（`transport=http`，地址、`allowed_tools=["echo"]`、`enabled=true`）。
3. `POST .../{server_id}/probe` → 期望 `ok=true`、`tools=["echo"]`、有 `duration_ms`。
4. `GET /api/v1/ops/mcp-servers?tenant_id=...` → 确认响应里**没有** `credential_ref`。
5. 用企业成员令牌重复第 2/3 步 → 期望 403、`code=20003`。
6. 在插件声明里加一个 `tool_type="mcp"`、`operation="<server>.echo"` 且带 `confirm` 的步骤，跑一次任务 → 确认后产出文本；断开外部服务再跑 → 步骤 `failed`，任务其它步骤不受影响。
7. 置 `OA_MCP_ENABLED=false` 重启 → 第 2 步返回 `20003`，任务里的 MCP 步骤直接失败且不发起连接。

---

## 7. 回退方式（出事 1 分钟内可退回）

| 现象 | 处置 |
| --- | --- |
| 外部服务异常拖慢任务 | `.env` 置 `OA_MCP_ENABLED=false` 并重启 adapter；既有插件/任务路径不变 |
| 某台 server 有问题 | 直接 `DELETE` 该登记，或 `POST` 更新 `enabled=false` |
| 需要回滚表结构 | 执行 `deploy/migrations/rollback/0015_mcp_server.down.sql`（**会删除全部登记**，需先导出） |
| OpenAPI 门禁误报 | 不要改基线凑绿；按 §4 登记表核对 `baseline-b3-4.json` 与实现是否一致 |
