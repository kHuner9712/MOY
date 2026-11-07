# 交付说明：2026-11-07 全仓审计整改（阶段 0–3）

对应 [audit-2026-11-07-full-repo-audit.md](file:///c:/Users/15864/Desktop/麦子/docs/audit-2026-11-07-full-repo-audit.md)（提交 `067db5e`）。
基线：`main @ fea1acd`（审计起点），本文件按阶段记录判定结论、变更与门禁结果。

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| 0 | 工作区整理（28 项未提交改动逐项判定） | 已完成 |
| 1 | 5 条高危（H-01…H-05） | 进行中 |
| 2 | 规范强制化（`engineering-standards` + 门禁） | 待开始 |
| 3 | 气隙离线包（矩阵 B5） | 待开始 |

---

## 阶段 0：工作区整理（2026-11-07）

### 0.1 判定原则

审计起点 `fea1acd` 上共 **25 modified + 3 untracked**。逐文件比对内容与提交历史后，
**全部属于已完成、但未提交的整改工作**，**没有一项是废弃残骸**，因此不做 stash、
不回滚、也不做一次性全量提交；按逻辑单元拆成 3 个提交。

`docs/engineering-standards.md` 文件头自述其中 4 个文件（`db/engine.py`、
`test_db_session.py`、`docker-compose.lite.yml`、`validate_deploy.py`）是其他成员修复
2026-11-06 审计 A-01 的产物，本次判定与之一致。

### 0.2 逐项判定与落点

| # | 文件 | 判定 | 归入提交 |
| --- | --- | --- | --- |
| 1 | `apps/adapter/app/core/config.py` | 完成工作：B-5 `auth_mode=auto` 不再把 stub 视为可用 | `6cb89cb` |
| 2 | `apps/adapter/app/core/security.py` | 完成工作：B-5 stub 不授予跨租户特权 + `_is_loopback_bind()` + 启动前置硬判据 | `6cb89cb` |
| 3 | `apps/adapter/tests/conftest.py` | 完成工作：夹具显式 `host=127.0.0.1`，配合 B-5 | `6cb89cb` |
| 4 | `tests/security/test_tenant_spoofing.py` | 完成工作：B-5 跨租户伪装回归 | `6cb89cb` |
| 5 | `tests/unit/test_auth_startup.py` | 完成工作：B-5 启动拒绝矩阵 | `6cb89cb` |
| 6–10 | `test_l14_shutdown_release.py` / `test_metrics_contract.py` / `test_qa10_content_sniff.py` / `test_rag_upload_streaming.py` / `test_sso_routes.py` | 完成工作：既有用例补 `host`，使 B-5 硬判据不误伤内存传输用例 | `6cb89cb` |
| 11 | `apps/adapter/app/db/engine.py` | 完成工作：A-01 运行期只允许应用角色；通用超级用户黑名单与部署专用属主名解耦 | `1a652f5` |
| 12 | `tests/unit/test_db_session.py` | 完成工作：A-01 回归（`supersuer` 拼写哨兵、应用角色放行、属主名设计边界） | `1a652f5` |
| 13 | `deploy/docker-compose.lite.yml` | 完成工作：A-01 lite 改用 `${OA_DATABASE_URL}`，不再内联属主 DSN | `1a652f5` |
| 14 | `deploy/validate_deploy.py` | 完成工作：A-01 `COMPOSE_FILES` 纳入 lite + 字面超级用户 DSN 判据 + lite 负样本自检 | `1a652f5` |
| 15 | `deploy/install.sh` | 完成工作：A-03 缺真证书/http 基址/CN-SAN 不匹配一律拒绝起栈，自签仅 DEV 开关下允许 | `1a652f5` |
| 16 | `deploy/.env.lite.example` | 完成工作：A-03 新增 `OA_LITE_PROXY_TLS_PORT`、基址改 https | `1a652f5` |
| 17 | `deploy/.env.example` | 完成工作：ADR-0007 租户上下文口径订正 | `1a652f5` |
| 18 | `.gitignore` | 完成工作：排除 Trae 本地计划稿 | `57ee592` |
| 19 | `README.md` | 完成工作：端点口径 103（8 套基线）、分支策略改主干开发 | `57ee592` |
| 20 | `apps/adapter/scripts/reconcile_usage.py` | 完成工作：B-8 执行形态与 CI 边界（docstring） | `57ee592` |
| 21 | `docs/architecture-v1.0.md` | 完成工作：主干开发裁决、覆盖率/端点真源、`OA_KEYCLOAK_BASE_URL` 订正 | `57ee592` |
| 22 | `docs/deployment-guide.md` | 完成工作：A-03 升级前必读、离线包与导出 SQL 现状标注 | `57ee592` |
| 23 | `docs/plan-v1.0.md` | 完成工作：覆盖率门槛以 CI 为真源、主干开发 | `57ee592` |
| 24 | `docs/compliance/dpa-template.md` | 完成工作：保留期改为待双方书面确认 | `57ee592` |
| 25 | `docs/compliance/privacy-policy.md` | 完成工作：未定稿声明与待填清单 | `57ee592` |
| 26 | `CONTRIBUTING.md`（新） | 完成工作：贡献规范，指向 `engineering-standards` | `57ee592` |
| 27 | `docs/adr/0004-open-source-attribution-exemption.md`（新） | 完成工作：已接受 ADR（2026-11-07 裁决） | `57ee592` |
| 28 | `docs/engineering-standards.md`（新） | 完成工作：规范单一真源初版（阶段 2 继续扩展） | `57ee592` |

**废弃残骸判定：0 项。** 本地 3 处 stash（`WIP: B22-01 b批① 软限制改造`、`s2-wip`、
`pre-b20-dirty`）均为历史半成品，与本次工作无关，保持原状、不 apply。

### 0.3 提交与结果

| 提交 | 内容 |
| --- | --- |
| `6cb89cb` | `fix(security)` B-5：stub 暴露与跨租户特权 |
| `1a652f5` | `fix(deploy)` A-01/A-03：lite 应用角色 DSN + 边缘 TLS 强约束 |
| `57ee592` | `docs(governance)` 主干开发裁决、规范单一真源、离线包现状标注 |
| 审计报告 | `067db5e` 随以上提交一并推送 `main` |

**阶段 0 门禁**（提交前实测）：

- 后端 `ruff check` / `ruff format --check` / `mypy`：全绿（443 文件已格式化，229 源文件无问题）。
- `deploy/validate_import_directions.py`：违规 23（基线内 23 / 基线外 0），环 3（基线内 3 / 新增 0）。
- `deploy/validate_deploy.py`：R1–R6 通过（含 lite 覆盖与自检）。
- `deploy/validate_openapi.py --strict`：G1–G10 通过，端点 103。
- `deploy/validate_migrations.py` / `validate_gate_wiring.py` / `validate_repository_parity.py` / `validate_secrets.py`：通过。
- `scripts/gen_delivery_manifest.py`：已刷新（文件尺寸随本次改动变化）。

**阶段 0 结束状态：`git status` 干净；`main` 已包含审计报告与全部整改提交。**

---

## 阶段 1：P0 高危整改（进行中）

### H-01 告警投递：规则从「存在但不可达」变为「可达且可自证」

**变更**

- `deploy/docker-compose.yml`：新增 `alertmanager`（`prom/alertmanager:v0.28.1`）与
  `blackbox-exporter`（`prom/blackbox-exporter:v0.27.0`）；Prometheus 注入
  `OA_DEPLOY_MODE`（顺带关闭审计 U-01 的 external_label 空值疑点），并依赖 Alertmanager。
- `deploy/prometheus.yml`：新增 `alerting.alertmanagers → alertmanager:9093`；
  新增 `edge-tls` 抓取 job（blackbox 探测 `https://edge/healthz`，产出
  `probe_ssl_earliest_cert_expiry`）。
- `deploy/alert.rules.yml`：5 条 → **10 条**。新增 `ArtifactAccessFailureHigh`
  （导出/预览路由 5xx 占比）、`PgConnectionSaturation`、`EdgeProbeFailing`、
  `EdgeCertificateExpiringSoon`（30 天）、`EdgeCertificateExpiringCritical`（7 天）。
- `deploy/alertmanager.yml` + `deploy/alertmanager-entrypoint.sh`：静态基线与启动渲染。
  Alertmanager 不展开环境变量，脚本在启动时把 `OA_ALERT_WEBHOOK_URL` 替换进 critical
  路由；未配置时回落 `officeai-null`（告警仍进 UI/API，缺 URL 也能起栈）。
- `deploy/blackbox.yml`、`deploy/images.lock`（2 个新镜像 digest 已核验）、
  `deploy/.env.example`（`OA_ALERTMANAGER_PORT` / `OA_ALERT_WEBHOOK_URL`）。
- `deploy/validate_config_names.py`：两个新键按「谁在消费」登记白名单。
- `.github/workflows/ci-adapter.yml`：新增 4 道自证 —— `promtool check rules`、
  `promtool test rules`（11 组含负样本）、`promtool check config`（含 alerting 接线）、
  `amtool check-config`（基线 + 脚本渲染的空/非空两态，URL 含 `&` 也验证）。
- 文档：`docs/deployment-guide.md` 修正「5 条告警规则」表述并新增 §10.1 投递与验证清单；
  `docs/architecture-phase4.md` §8.4 更新为 10 条并新增 §8.5 链路说明；
  `docs/engineering-standards.md` S6.9 从「零引用待固化」改为「已挂 CI」。

**对质量的影响**

- 告警首次具备**真实投递路径**（Prometheus → Alertmanager → 可选 webhook）；
  证书到期从「无人知道」变为 30 天预警 + 7 天 critical。
- 规则不再只靠 review：11 组 `promtool test rules` 把阈值边界、`for`、标签保留、
  负样本（80% 不告警 / 非产物路由不计入）全部钉死；规则写错在 CI 直接红灯。
- 假绿被堵：`alert.rules.yml` 与 `prometheus.yml` 此前在 CI **零引用**（M-05），
  现在任何规则/接线改动都必须通过 promtool/amtool，否则 CI Adapter 红。
- 未配置 webhook 时明确标注「仅 UI/API 可见」，不再宣称「已投递」。

**仍未闭环**：无内置 Loki/Tempo/Grafana（M-06，属可观测性纵深，不在 P0 范围）。

**CI 首跑红与修复（d11a142，2026-11-07）**：CI Adapter `42321575875` 的 H-01 自证步骤
以 `Permission denied`（exit 126）失败——`deploy/alertmanager-entrypoint.sh` 在 git 中是
`100644`，Linux runner 上无执行位，而 CI 用 `sh -c "/etc/.../entrypoint.sh"` 直接执行。
修复：CI 改为 `sh /etc/alertmanager/entrypoint.sh`（compose 里本就显式 `/bin/sh` 前缀，
不受影响），并给脚本补执行位。CI Web `42321597212` 的 E2E 在同一 SHA 上因 B-5 硬化
（`auth_mode=stub` + `host=0.0.0.0`）以 `30004` 拒绝起栈——job 里 uvicorn 虽绑
`127.0.0.1`，但 `Settings.host` 仍是默认 `0.0.0.0`；补 `OA_HOST=127.0.0.1` 与绑定位一致。

### H-02 SSRF 阻断（已完成）

**已完成的变更**

- `apps/adapter/app/core/net_safety.py`（新建）：地址判定与解析钉扎的单一实现。
  - 拒绝段 IPv4：`0.0.0.0/8`、`10/8`、`100.64/10`（CGNAT）、`127/8`、`169.254/16`
    （含 `169.254.169.254` 元数据地址）、`172.16/12`、`192.0.0.0/24`、`192.0.2.0/24`、
    `192.168/16`、`198.18/15`、`198.51.100/24`、`203.0.113/24`、`224/4`、`240/4`。
  - 拒绝段 IPv6：`::/128`、`::1/128`、`fc00::/7`（含 `fd00::/8`）、`fe80::/10`、
    `ff00::/8`、`2001:db8::/32`、`64:ff9b::/96`；`::ffff:` 映射地址先拆成 IPv4 再判定，
    `::ffff:169.254.169.254` 与 `::ffff:127.0.0.1` 均被拒。
  - `resolve_host_ip()`：**解析一次**，任一解析结果命中拒绝段则整体拒绝（fail-closed）；
    字面量 IP 直接判定，不经过 DNS。
- `apps/adapter/app/core/config.py`：新增 `mcp_allowed_hosts`（默认空）、
  `mcp_allow_loopback`（默认 false）、`ssrf_proxy_url`（默认空）。
- `apps/adapter/app/business/mcp_service.py`：登记与执行前**两处**校验 —— 先过目标白名单
  （空名单 = 全拒绝，`*.example.com` 支持子域通配且根域不命中），再判内网/保留地址；
  回环仅在 `env∈{test,development}` 且显式 `mcp_allow_loopback` 时放行。错误统一
  `PARAM_INVALID` + 安全文案（不回显解析结果）。
- `apps/adapter/app/clients/mcp_client.py`：`McpConnection` 记录 `pinned_ip` / `host_header` /
  `proxy_url`；请求 URL 换成已判定的 IP（IPv6 加方括号），保留原主机名作 `Host` 头与 TLS
  `sni_hostname` 扩展 —— **DNS rebinding 在解析后无法再改向**。
- `apps/adapter/app/services/mcp_runner.py`：probe 与 execute 共用 `_connection()`；
  HTTP 无代理时先 `resolve_host_ip()` 再钉扎；`unsafe_target:<reason>` 只进内部诊断，
  不把 IP/解析细节透给调用方。
- `deploy/.env.example`：`OA_MCP_ALLOWED_HOSTS=`（默认空 = 全拒绝，附声明示例）、
  `OA_MCP_ALLOW_LOOPBACK=false`；`OA_SSRF_PROXY_URL` 注明适配层已消费。
- `docs/deployment-guide.md` §16.4：三道闸口径 + 现场可复核断言
  （登记 `169.254.169.254` / `[fd00::1]` / `[::ffff:127.0.0.1]` 应得 `400` + `20001`）。

**对质量的影响**

- 新增 `apps/adapter/tests/unit/test_mcp_ssrf.py` **20 个用例**：IP 拒绝参数化（含 mapped /
  CGNAT / ULA）、白名单通配与根域不命中、混合解析 fail-closed、IPv4/IPv6 钉扎与
  `Host`/SNI 回填、空名单全拒绝、内网字面量即便在名单内也拒绝、回环仅在 test + 显式开关、
  代理模式不钉扎。
- 假绿被堵：审计实测 `app/` 内 ssrf/169.254 零命中、`OA_SSRF_PROXY_URL` 零读取（G-9）；
  现在登记内网目标在**登记阶段**就失败，而不是等出网时才暴露。
- 兼容性：既有 4 个 MCP 测试文件补 `mcp_allowed_hosts` / `mcp_allow_loopback` 后全绿；
  默认配置（空白名单）下行为从「任意地址可用」变为「默认拒绝」——这是**有意的安全默认**，
  已在部署手册登记为上线前必配项。

**仍未闭环**：`deploy/squid.conf` 仍是 `http_access allow localnet` 且无 `dstdomain`
（G-11），网络层出站白名单属部署侧工作，不在本条范围。

### H-03 零测试端点（已完成，含审计前提修正）

**审计前提修正**：审计用「模块名/文件名」在 `tests/` 里检索，得出
`ops_mcp.py` / `tools_ext.py` / `artifact_service.py` / `table_parse.py` 零引用。
复核后**四条结论都不成立**：`test_ops_mcp_api.py` 已覆盖 4 个运营端点；
TL1 在 `test_tool_executions.py`；下载链路的集成用例覆盖 `fetch_artifact`；
`table_parse` 被 `schedule_timeline` / `screening_risk` 间接驱动。
真正的缺口是**维度**而非「有没有」：权限拒绝只覆盖了企业令牌、跨租户、
边界输入、能力开关关闭、分页越界都没有用例。

**已完成的变更**

- `tests/integration/test_ops_mcp_api.py`：3 → **11 个用例**（+8）。
  新增：跨租户（A 登记对 B 不可见、探测/删除 404 `20004`、A 记录未被误删）、
  平台哨兵租户 `__platform__` 拒绝（`20001` + 明确文案）、未登记 ID 的
  probe/delete（`20004` 而非 500）、旧版 SSE/未知传输参数化拒绝（3 组）、
  租户 ID/登记 ID 长度越界统一 `400 + 20001`、`mcp_enabled=false` 时
  **写端点 20003 而列表仍可读**（能力关闭不关死运维排查路径）。
- `tests/integration/test_tool_executions.py`：+3 个用例。`page_size∈{0,201}`
  统一 `400 + 20001`；页码越界返回空页且 `meta.total=0`（不回退成全量）。
- `tests/unit/test_table_parse.py`（新建，**11 个用例**）：BOM 不污染首列、
  引号内逗号、空输入、非法 UTF-8 不抛异常、缺列补空串、markdown 表格
  分隔行/对齐标记、乱序缺列补空、非表格文本、全空单元格语义、`column_count`。
- `tests/unit/test_artifact_service.py`（新建，**3 个用例**）：按令牌租户
  + 产物 ID 精确查询、同名产物在别的租户返回 `None`、缺失返回 `None`
  且存储实例由传入 Settings 组装（不是进程默认单例）。
- `tests/unit/test_tool_store_tenant_scope.py`（新建，**2 个用例**）：调用记录
  按租户过滤、租户内分页（offset 不把他人记录翻进来）。

**对质量的影响**

- 净增 **27 个用例**（含参数化展开），四条「零测试」全部由「间接覆盖」升级为
  「直接覆盖 + 对抗维度」。
- 假绿被堵：`mcp_enabled=false` 的写端点此前无断言（关能力后若守卫被绕过不会被发现）；
  TL1 的 `page_size` 上限只有 OpenAPI 声明、无运行时用例；跨租户在运营端点
  此前完全无覆盖。
- 覆盖率：`table_parse` / `artifact_service` / `ops_mcp` 由「仅间接」变为直接命中；
  未删任何断言、未加 `pragma`。

**仍未闭环**：`ops_mcp` 的 PostgreSQL 存储路径（`PostgresMcpServerStore`）只有内存
实现被这些用例驱动；SQL 侧由 `--run-pg` 的仓储用例覆盖，本条不再扩。

### H-04 主旅程 E2E（已完成，**CI 真跑通**，不是 API 级降级）

**结论先行**：主旅程在**浏览器里真跑**——`统一登录 → Keycloak 授权页 → 回调兑换
→ /chat 主应用 → /plugins 安装抽屉`，删除了 `OA_E2E_ALLOW_SKIP` 且不保留任何
等价的永久跳过开关。本地与 CI 使用**同一套**夹具与断言；本地同构复跑
`3 passed`（贯穿段 6.7s、两段匿名前置 1.2/1.3s）。

**已完成的变更**

- `.github/e2e/keycloak-realm-ci.json`（新建）：CI 专用 realm `officeai`。
  public client `officeai-adapter`（PKCE S256、`redirectUris=[http://localhost:3100/auth/callback]`、
  audience + `tenant_id` mapper）、测试用户 `e2e.user` / `e2e-pass-2026`，
  角色 `member` + `org_admin`（前者给对话/知识库基线权限，后者给插件安装）。
  **不含任何生产密钥**，仅 CI 账号。
- `.github/workflows/ci-web.yml` 的 `e2e-gate`：
  - 新增「启动 Keycloak 测试 realm」步骤（`quay.io/keycloak/keycloak:26.8.1`，
    `start-dev --import-realm`，轮询 `.well-known/openid-configuration` 90×2s），
    以及 `if: always()` 的清理步骤。
  - 适配层由 `auth_mode=stub` 改为 **`oidc`**：`OA_PUBLIC_BASE_URL=http://localhost:3100`、
    `OA_KEYCLOAK_BASE_URL=http://127.0.0.1:8080`、realm/client、
    `OA_JWT_ALGORITHM=RS256`、`OA_JWT_ISSUER=http://127.0.0.1:8080/realms/officeai`。
  - 注入真实账号 `OA_E2E_USERNAME/PASSWORD`；**删除 `OA_E2E_ALLOW_SKIP`**；
    浏览器侧 `NEXT_PUBLIC_ADAPTER_BASE_URL=http://localhost:8000`（与页面同站，
    Cookie/CSRF 判定成立），探活用 `127.0.0.1` 规避 Node 优先解析 `::1`。
- `apps/adapter/app/main.py`：development 环境的 CORS 白名单补
  `localhost:3100` / `127.0.0.1:3100`（Playwright webServer 端口；生产同源部署不受影响）。
- `apps/web/e2e/workflow.spec.ts`：
  - 删除 `ALLOW_E2E_SKIP` 分支——缺凭据只保留**必然失败**的一条；
  - 前置探活由 `test.skip` 改为**抛错**（栈不可达即红，错误文案写明怎么起栈）；
  - 主应用落点断言由已下线的首页大标题改为 `navigation[name=主导航]` 地标，
    语义仍是"真的进了主应用"，不是放宽。
- 文档：`apps/web/README.md`（本地/CI 跑法、账号与 OIDC 变量）、
  `docs/remediation-plan-2026-10-29.md`（S-5 标记闭环、删开关）。

**对质量的影响（数据）**

- 假绿被堵：此前 CI 主旅程从未执行（`OA_E2E_ALLOW_SKIP=1` + 静默 skip），
  报告里"绿"的只是匿名段；现在**贯穿段是必跑门禁**。
- 新发现的真实缺陷（本地首跑暴露，均已修）：适配层 OIDC 默认 `HS256` 与
  Keycloak `RS256` 不匹配（`InvalidAlgorithmError`）；`OA_JWT_ISSUER` 必须逐字符
  等于 Keycloak `iss`（`InvalidIssuerError`）；测试用户无角色导致 `/chat` 权限
  提示（`20003`）；`/chat` 文案断言已过期。这些只会在**真跑**里出现——
  这正是删除跳过开关的价值。
- 兼容性：只改了 development CORS 白名单与 CI 夹具，未改任何对外 API 契约。

**仍未闭环 / 口径说明**

- `/workbench` 裸路径本就是 404（动态 `[pluginId]` 路由），工作台落点断言用
  `/chat`；安装抽屉打开即视为安装入口可达，真实安装提交留给插件域用例
  （与既有 `grant-drawer` 单测的分工不变）。
- CI 侧结论以**同 SHA 的 `CI Web` 绿**为准（本文件在推送后回填结果）；不再存在
  "本地绿、CI 跳过"的中间态。

### H-05 CI paths 覆盖（待做）

