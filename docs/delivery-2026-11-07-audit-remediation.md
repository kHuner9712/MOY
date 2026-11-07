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

### H-02 SSRF 阻断（待做）

### H-03 零测试端点（待做）

### H-04 主旅程 E2E（待做）

### H-05 CI paths 覆盖（待做）

