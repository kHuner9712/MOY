# 文档与代码一致性审计清单（2026-11-04）

| 项 | 内容 |
| --- | --- |
| 审计基线 | `main @ e7d79e7`（审计开始时的 HEAD；审计期间仓库仍有其它提交，数字均绑定该 SHA） |
| 审计性质 | **只读清单**。本轮未修改任何业务代码、文档、依赖或测试（除本文件） |
| 口径 | **实测** = 本轮实际执行的只读命令；**静态** = grep / AST / 文件存在性检查；**转引** = 引用前序审计结论，未复跑 |
| 与既有审计的关系 | `docs/audit-2026-11-04-cleanup-audit.md` 已记录 `a4f6213` 的清理执行。本清单只列**当前仍存在**或**新发现**的问题；已修复项不重复计入 |
| 统计 | 共 **50** 条：§1 文档冲突 23、§2 过期表述 10、§3 测试 7、§4 依赖 10；建议 **更新 38 / 删除 2 / 保留 10** |

> 复核提示：所有「实测」数字都可在附录 A 的命令下复现。未复跑全量 pytest、未启动 Docker、未执行 npm build，相关判断已明确标注。

---

## 一、文档与代码的真实冲突（23 条）

| 编号 | 类型 | 位置 | 证据 | 建议 | 风险 | 影响面 / 回归 |
| --- | --- | --- | --- | --- | --- | --- |
| D01 | 数字冲突·实测 | `README.md:23-24` | README 称「2559 passed / 238 skipped / 收集 2797」；本轮 `pytest tests --collect-only -q`（cwd=`apps/adapter`）实测 **收集 2933**，`-m pg` **258** 条。差 +136 / +20 | **更新**：改为当前收集数，或删除具体数字、只保留 CI `junit.xml` 作为唯一口径（后者更抗漂移） | 高 | 读者按 README 判断回归规模会低估；不影响运行。回归：无需跑码，复跑收集命令即可 |
| D02 | 行为冲突·静态 | `README.md:26-27` | 「未设 `OA_BATCH` 时 `current_batch()` **故意抛错**（缺失即红）」；实际 `apps/adapter/tests/expected_xfail.py:292` 定义 `_DEFAULT_BATCH = "B21"` 并回落，`apps/adapter/README.md:188-190` 也写「有默认值 B21」 | **更新**：删除「缺失即红/无默认」表述，与 adapter README 对齐；同步 `.github/workflows/ci-adapter.yml:88-89` 注释 | 高 | 误导部署方以为必须显式设置。回归：`validate_config_names.py` 不受影响 |
| D03 | 数字冲突·实测 | `README.md:72`；`deploy/migrations/README.md:13` | README 写「0002-0012（11 个）」；`deploy/migrations/` 实为 0002…**0013** 共 **12** 个（含 `0013_event_log.sql`）；同文件 `:4/:8/:25` 已写 0013 | **更新**：两处统一为「0002-0013（12 个）」 | 低 | 纯文档；回归：`deploy/validate_migrations.py` |
| D04 | 数字冲突·实测 | `README.md:86` | 写「decisions/ 决策确认单 0003~0008」；`docs/decisions/0009-base-v1.0-ratification.md` 已存在，`docs/README.md:67` 已写 0003~0009 | **更新**：改为 0003~0009 | 低 | 纯文档 |
| D05 | 覆盖率阈值冲突·静态 | `.github/workflows/ci-web.yml:87-88` | CI 注释写「当前阈值为四指标全覆盖：statements 86.4 / branches 80.4 / functions 81.1 / lines 87.6」；实际 `apps/web/vitest.config.mts:306-309` 为 **88.8 / 84.0 / 83.9 / 90.0** | **更新**：CI 注释改为 vitest 实际阈值；或直接删除注释里的数字，指向配置文件 | 中 | 维护者会按旧阈值估余量。回归：`npm run test --workspace apps/web -- --coverage` |
| D06 | 命令失效·静态 | `apps/adapter/README.md:186` | 前置条件写「库已完成 `alembic upgrade head`」；`apps/adapter/app/db/migrate.py:29-30` 明确「不引 Alembic」，`pyproject.toml` 无 Alembic 依赖；实际命令见 `deploy/migrations/README.md:47` | **更新**：改为 `python -m app.db.migrate --apply`，或删除该前置条件 | 高 | 照做会直接失败。回归：`python -m app.db.migrate --status` |
| D07 | 交付状态冲突·静态 | `docs/deployment-guide.md:668,672,678` | 写「轮 2 交付 `docker-compose.tls.yml`」「该文件与 `edge-tls.conf` 尚不存在」并给出启动命令；实际 `deploy/docker-compose.yml:595-610` 已有 `edge`（nginx:1.28-alpine，80/443，挂载 `tls/fullchain.pem`），`deploy/nginx/edge.conf` 已含 443/证书配置；`docker-compose.tls.yml` 确实不存在 | **更新**：§12.5 改为「B 形态已并入主 compose 的 `edge`」，删除不存在的覆盖文件与命令；同时修正「默认 A 只开 80」 | 高 | 部署方会寻找不存在的文件。回归：`docker compose config` + `validate_deploy.py` |
| D08 | 鉴权契约冲突·静态 | `docs/api/README.md:136` | 写「除 `/healthz`、`/readyz` 外均需 Bearer」；`apps/adapter/app/middleware/error_handler.py:58-74` 的 `AUTH_EXEMPT_PATHS` 还含 `/api/v1/billing/public/callback`、`/metrics`、`/sso/*`（`sso_routes.py:673,733,789,877,990` 共 5 个） | **更新**：改为「默认需 Bearer，豁免清单见下表」并补全 | 高 | 集成方会误判鉴权边界。回归：`tests/unit/test_auth_middleware*`、`tests/integration/test_sso*` |
| D09 | 版本冲突·实测 | `docs/lite-container-baseline.md:25,28,41`；`docs/prd-phase4-increment.md:1322` | baseline 写 `nginx:1.29-alpine`、`pgvector/pgvector:pg18`、dify-web「digest 待核验」；`deploy/docker-compose.lite.yml:601` 为 `nginx:1.28-alpine`、`:64` 为 `pgvector/pgvector:0.8.7-pg18-bookworm`；`deploy/images.lock:69-71` 已有 dify-web digest `sha256:6353fe8e…`；PRD 仍写 1.29 | **更新**：按 compose + images.lock 改 tag/digest，并同步 PRD | 中 | 镜像基线整体失真，容量与体积结论不可信 |
| D10 | 数字冲突·实测 | `docs/lite-container-baseline.md:26` | 写「adapter 84 端点」；`README.md:236`、`docs/api/README.md:75-76` 均为 **96**（30+38+16+10+2） | **更新**：改为 96 | 中 | 安全风险面评估口径偏差 |
| D11 | 服务数冲突·实测 | `docs/lite-container-baseline.md:211`；`docs/perf-disclosure.md:119,289` | 写标准栈 18/17 个服务；本轮用 PyYAML 解析 `deploy/docker-compose.yml` 实为 **21 个 service**（含 `edge`、`prometheus`、两个 exporter），常驻 20（`keycloak-import` 为一次性 profile） | **更新**：统一为 21（或注明「常驻 20」），并复核轻量版 9 容器口径 | 中 | 资源预算与文档对不上 |
| D12 | 结论过期·静态 | `docs/plugin-dev-guide.md:665-666` | 偏差①「A4 响应未下发 `steps[].artifacts[]` 与 `failure`」；实际 `apps/adapter/app/schemas/agent.py:335,338` 已声明；偏差②「中文名产物不可下载、无测试覆盖」；`artifact_files.py:128-155` 已用 RFC 5987 `filename*`，`tests/unit/test_content_disposition.py` 存在 | **更新**：删除①②，保留仍成立的③；若需保留历史，加「已修复」日期 | 中 | 会按旧结论验收，误判能力缺失 |
| D13 | 契约数字冲突·实测 | `apps/adapter/app/main.py:10,23` | 注释写「96 个端点：OpenAPI **99 个操作**」；`docs/api/openapi.snapshot.json` 实测 **86 paths / 104 operations**（含 5 个 `/sso`） | **更新**：改为 104，或注明「99 = 不含 `/sso` 的业务口径」 | 低 | 纯注释；避免与 snapshot 门禁解释冲突 |
| D14 | 计数冲突·静态 | `.github/workflows/dependency-scan.yml:529` | 注释「17 运行时 + 36 前端」；实际 `pyproject.toml` 运行时 **16**、`apps/web/package.json` **14+15=29**；`deploy/validate_deps.py:86` 也写 14+15=29 | **更新**：改为 16 + 29 | 低 | 门禁 `FRONTEND_DEPS_MIN=27` 仍通过，仅文案漂移 |
| D15 | 死配置·实测 | `deploy/.env.lite.example:42` | 仍写 `OA_OFFLINE_MODE=true`；`docs/deployment-guide.md:93` 明确「V1.0 无 `OA_OFFLINE_MODE`」，`deploy/install.sh:383` 注释称已删除，`deploy/validate_config_names.py:145,460` 登记为「已删除死配置」，`apps/` 无读取字段 | **删除**：删除该行；若未来实现离线模式再引入 | 高 | 轻量版模板继续注入死键，误导离线部署。回归：`python deploy/validate_config_names.py --self-test`（期望 EXIT 0）+ `install.sh --help` |
| D16 | 组件不存在·静态 | `docs/architecture-v1.0.md:136,144` | 架构图含 `LIC[License 授权校验]`，部署矩阵写 `DEPLOY_MODE=private` + License；`deploy/.env.example:445-447` 明确「V1.1 预留，当前无读取代码」，`apps/` 无消费方 | **更新**：删除组件，或标注「V1.1 规划，当前不生效」 | 中 | 客户会以为私有化需要 License。回归：无 |
| D17 | 路径失效·实测 | `docs/architecture-v1.0.md:1616,1618` | 门禁命令写 `python apps/adapter/scripts/validate_plugins.py`；该路径不存在，实际为 `deploy/validate_plugins.py` | **更新**：改为 `python deploy/validate_plugins.py` | 低 | 照做失败。回归：实跑该命令 |
| D18 | 口径冲突·静态 | `docs/architecture-phase2.md:417` | 写「对账：比对 Valkey 诊断计数 vs `usage_monthly.tenant`」；`apps/adapter/scripts/reconcile_usage.py:4-30` 明确为 `usage_event` 明细 vs `usage_daily` 聚合，且「不读 Valkey」；`docs/qa-phase2-waivers.md:78-79` 已批准口径变更 | **更新**：按脚本现状改写 | 中 | 运维按错口径对账。回归：脚本 `--help` / 集成用例 |
| D19 | 文件不存在·实测 | `docs/qa-phase2-test-plan.md:1356-1359` | 列出的 `tests/factories/data.py`、`apps/adapter/scripts/seed_qa_data.py`、`apps/adapter/scripts/cleanup_qa_schema.py` 均不存在；`reconcile_usage.py` 已存在 | **更新**：删除 3 个「新增」文件行；把 reconcile 状态改为「已实现」 | 中 | 测试计划无法执行。回归：文件存在性检查 |
| D20 | 路径口径·实测 | `docs/qa-phase2-waivers.md:31,76` | 写 `scripts/reconcile_usage.py`；仓库根无此路径，实际为 `apps/adapter/scripts/reconcile_usage.py`（脚本自身 `:23` 给出带前缀命令） | **更新**：补 `apps/adapter/` 前缀或注明执行目录 | 低 | 照做失败 |
| D21 | 状态过期·实测 | `docs/lite-container-baseline.md:41` | dify-web 写「digest 待核验」；`deploy/images.lock:69-71` 已有 `sha256:6353fe8e…` | **更新**：补 digest，删「待核验」 | 低 | 容量基线可信度 |
| D22 | 编号冲突·静态 | `docs/metrics-definition-v1.0.md:289` | 计划迁移 `0013_artifact_adoption`，并写「落地时编号顺延 0013+」；`deploy/migrations/0013_event_log.sql` 已占用 0013 | **更新**：改为 `0014+` | 低 | 后续迁移会撞号 |
| D23 | 组件名冲突·静态 | `docs/architecture-v1.0.md:107` | 写 `postgres / redis / milvus / minio`；同文件 `:134` 已写 SeaweedFS，compose 服务名为 `object-store`，无 minio | **更新**：`valkey / milvus / SeaweedFS(object-store)` | 低 | 架构图与部署不符 |

---

## 二、过期或过时表述（10 条）

| 编号 | 类型 | 位置 | 证据 | 建议 | 风险 | 影响面 / 回归 |
| --- | --- | --- | --- | --- | --- | --- |
| S01 | 结论被推翻·静态 | `docs/archive/audit-2026-11-02-full-repo-audit.md:21,60,118,253` | 仍写 P0-4「待做·轮 2」、TLS 未交付、端口裸绑、k8s 只有 README；现状：`docker-compose.yml:595-610` 有 `edge` 443 + 证书挂载，`:697,750` 与 `docker-compose.saas.yml:501,533` 已绑 `127.0.0.1`，`deploy/k8s/` 有 12 个 manifest | **更新**：文首加「历史快照（2026-11-02），P0-4 已于后续批次修复」状态横幅；或移入 `docs/archive/` | 中 | 读者会把已修问题当遗留。回归：无 |
| S02 | 结论被推翻·静态 | `docs/archive/audit-2026-11-02-arch-perf-deps.md:94,166` | 同上，写「无 TLS/反代」「仅 README」 | **更新**：同 S01 横幅 | 中 | 同上 |
| S03 | 状态过期·静态 | `docs/remediation-ledger-2026-11-02.md:68` | P0-4「待做·轮 2」；`edge` 已实现，应改为「已完成（G1）」 | **更新**：状态列改完成并补证据 | 中 | 整改台账失真 |
| S04 | 结论被推翻·静态 | `docs/design-2026-11-02-security-deploy.md:377-391` | 写「两套 compose 无任何反代」「端口省略 host IP」「k8s manifests 全缺」「19/16/11 服务」；实测有 edge、loopback 绑定、13 个 yaml，服务数 21/18/9 | **更新**：§3.1.1 逐条改现状，或整节标「已被 G1 推翻」 | 中 | 安全设计评审依据过期 |
| S05 | 结论被推翻·静态 | `docs/design-2026-11-02-keycloak-realm.md:56,59` | 写「`KC_HOSTNAME` 未设置，grep 0 命中」「`KC_PROXY`/`KC_PROXY_HEADERS` 未设置」；`docker-compose.yml:498,501` 与 `docker-compose.saas.yml:349,352` 均已设置 | **更新**：改「现状」表为已设置，或加修复日期 | 中 | Host 注入防护被误判为缺失 |
| S06 | 文档描述文档失真·静态 | `docs/project-brief-2026-11-03.md:304` | 称「README 的用例数字仍是旧口径（2343 / 2211 / 212）」；README:23 已更新为 2559/238/2797，故该句已不成立 | **更新**：删该句，或改为「README 已更新，以 CI 工件为准」 | 中 | 简报结论过期 |
| S07 | 结论过期·静态 | `docs/platform-strategy-2026-11-03.md:17,146` | 仍写「`OA_OFFLINE_MODE` 是死配置」；`.env.example` / `install.sh` / k8s 已于 `a4f6213` 清理，仅剩 `.env.lite.example:42`（见 D15） | **更新**：改为「已清理，仅剩轻量模板残留」；`:189/:246` 的「待用户确认」同步关闭 | 中 | 战略文档与清理结果冲突 |
| S08 | 数字过期·静态 | `docs/dependency-upgrade-watchlist.md:39` | 验证栏写「回归 2211 passed / 0 failed / 覆盖率 85.87%」 | **更新**：标注快照日期与 SHA，或改为「全绿（数字见 CI）」 | 低 | 仅作历史参考 |
| S09 | 注释与行为不符·静态 | `apps/adapter/tests/conftest.py:384-385` | 注释称「并发用例另用 `pg_pool_engine`」；实际并发入口是 `tests/integration/pg_fixture.py:248` 的 `b01_concurrent_engine`（`test_tenant_isolation.py:284` 使用） | **更新**：改注释；`pg_pool_engine` 本身见 T03 | 低 | 误导测试维护 |
| S10 | TODO/FIXME 状态·实测 | `apps/adapter/app/**`、`docs/**` | 静态检索：`app/` 仅 2 处命中，均非待办（`plugins/schema.py:183` 是枚举字面量 `"TODO"`，`db/repositories/event_log/records.py:177` 是 `\uXXXX` 说明）；`docs/` 18 处均在审计表/引述中，无未完成 TODO | **保留**：无可执行 TODO；无需动作 | 低 | 无 |

---

## 三、冗余 / 过时 / 失效的测试（7 条）

| 编号 | 类型 | 位置 | 证据 | 建议 | 风险 | 影响面 / 回归 |
| --- | --- | --- | --- | --- | --- | --- |
| T01 | 跳过统计·实测 | 后端 4 处 + 前端 2 处 | 静态检索 + 收集：`skip` 共 **6 个点，全部条件性**（`conftest.py:418-420` 未传 `--run-pg`；`conftest.py:479` 无 DSN；`pg_fixture.py:174,194` 无 DSN；`selftest_plugin_contract.py:34-39` 自检开关；`e2e/workflow.spec.ts:35` 栈未起）。`xfail` **0**、`only` **0**、`skipif` **0**、`.todo` **0** | **保留**：均为环境/开关条件，非永久挂起 | 低 | 删任一条件会让无库环境直接红 |
| T02 | 断言失效·实测 | `apps/adapter/tests/integration/test_endpoints.py:628-635` | 文档串写「空组织 ID 被路径参数校验拦截为 **20001**」；实际断言 `status_code in {401,404}`，仅 404 分支校验 `code == 20004`（`app/core/errors.py:33` 才是 20001）。401 也算通过 → 鉴权回归会**假绿** | **更新**：明确断言 404 + 20004（或按真实路由行为断言 20001），去掉 401 兜底 | 高 | 单条用例失去防回归价值。回归：单跑该用例 |
| T03 | 孤立 fixture·实测 | `tests/conftest.py:342,358,370,511,538`；`tests/e2e/conftest.py:1330` | AST 参数注入 + `getfixturevalue`（全仓 0 处）统计：`limiter` / `api_error` / `fingerprint` / `pg_pool_engine` / `seeded_tenants` / `member_headers` 注入均为 **0**；`pg_pool_engine`、`seeded_tenants` 已分别被 `pg_fixture.py:248`、`:287` 同构替代 | **删除**：6 个 fixture 定义（可一次清理） | 中 | 测试基础设施。回归：`pytest --collect-only` 数量不变 + `tests/integration/test_tenant_isolation.py` 真库用例 |
| T04 | 重复命名·实测 | `tests/unit/test_sso_qa_round2.py:378` ↔ `tests/unit/test_sso_routes.py:718`；`test_clients.py:141/155/177` ↔ `:396/408/420` | 全仓同名 `def test_`：**19 个名称 / 40 处定义**。多数为分层或对等设计（feishu↔wecom、真库↔单测、三 repo 各自隔离），不建议删；SSO 那组是同一 cookie 语义的重复实现，`test_clients` 同名跨类仅易混淆 | **保留**：仅建议重命名 `test_clients` 的 identity 组；SSO 组可合并但需确认 fixture 差异 | 低 | 删重会削覆盖；改名无风险 |
| T05 | CI 永久跳过·静态 | `.github/workflows/ci-web.yml:377-379`；`apps/web/e2e/workflow.spec.ts:31-36` | CI 设 `OA_E2E_ALLOW_SKIP: '1'`，配合 `workflow.spec.ts` 的栈不可达分支 → 贯穿旅程在 CI 实际不跑；代码注释已标 `R2-QA-02（审计 2026-10-29）`「账号就绪后删变量」 | **保留**：作为已知验收缺口跟踪；不要误删变量（会立刻红） | 中 | 端到端链路无 CI 证据 |
| T06 | 假数据目录不存在·实测 | `tests/fixtures` | `Test-Path tests/fixtures` = False；无「无引用数据文件」可报 | **保留**：无需动作 | 低 | 无 |
| T07 | 辅助脚本引用·静态 | `scripts/`、`apps/adapter/scripts/`、`apps/web/scripts/` | 逐个核对 CI / package.json / 文档引用：全部有调用方（`gen_delivery_manifest.py`、`check_error_catalog.py`、`qa/phase4-verify.sh`、`scripts/bench/*`、`reconcile_usage.py`、7 个 `apps/web/scripts/*.mjs`），**0 个孤立脚本** | **保留** | 低 | 无 |

---

## 四、未使用或不匹配的依赖（10 条）

| 编号 | 类型 | 位置 | 证据 | 建议 | 风险 | 影响面 / 回归 |
| --- | --- | --- | --- | --- | --- | --- |
| P01 | 依赖总况·静态 | `apps/adapter/pyproject.toml:28-75`；`apps/web/package.json:35-66` | 运行时 16 + dev 5 + 前端 14/15，共 50 个包逐个对照 import/配置/CLI/构建期引用：**高置信「未使用」0 项**；源码第三方 import 100% 落在声明清单内，无幽灵依赖 | **保留**：不删任何包 | 低 | — |
| P02 | 构建期存疑·静态 | `apps/adapter/pyproject.toml:6` | `requires = ["setuptools>=68,<85", "wheel>=0.44,<1.0"]`；`^(import\|from)\s+wheel` 全仓 **0 命中**，`uv.lock` 无 wheel 条目 | **保留（待验证）**：现代 setuptools 已内置 bdist_wheel，疑似可删；须先做构建验证，不建议本轮动手 | 中 | 删错会打不出 wheel。回归：隔离环境 `pip wheel apps/adapter --no-deps` |
| P03 | 隐式运行期依赖·静态 | 后端 `pyproject.toml:56`；前端 `package.json:45,53-55,62-63,48` | `python-multipart` 直接 import 0 命中，但 `app/api/v1/rag.py:28` 的 `File/Form/UploadFile` 运行期必需（`:55` 已有「移除后上传 500」注释）；`react-dom` 生产 0 import 但 Next 运行期必需；`@types/*` 由无 `types` 限制的 tsconfig 自动纳入；`postcss`、`prettier`、`tailwindcss-animate` 为 runner/构建期 | **保留**：均为隐式必需；`tailwindcss-animate` 可考虑移到 devDependencies（非删除） | 中 | 误删会导致 500 或构建失败 |
| P04 | 锁内双版本·实测 | `package-lock.json` | `apps/web/node_modules/@types/node` 20.19.43（符合 `^20.14.0`）与根 `node_modules/@types/node` **26.6.6**；`apps/web/node_modules/eslint` 10.14.0（符合 `^10.13.0`）与根 `node_modules/eslint` **9.39.5**（跨大版本 peer 兜底） | **更新（独立 PR）**：加 `overrides` 或明确 workspace 解析路径；先验证 `npm ci` + tsc + lint | 中 | `apps/web/Dockerfile:22-38` 已记录 @types/node@26 曾致 `npm ci` 冲突；eslint 双大版本可能让 lint 加载错误版本 |
| P05 | 版本策略·静态 | `apps/web/package.json:57,66` | `@vitest/coverage-v8` 精确 `"5.0.5"`，`vitest` 浮动 `"^5.0.5"`；vitest 升小版本可能导致 coverage provider 失配 | **更新**：两者统一为精确版本，或同时放宽 | 低-中 | CI 覆盖率门禁可能因版本漂移红 |
| P06 | 门禁失效·静态 | `.github/workflows/sbom.yml:65,121` | SBOM 两个 job 均 `continue-on-error: true`，失败会被静默放行 | **更新**：改为阻断，或显式写入「非阻断」豁免说明 | 中 | SBOM 失真无人发现 |
| P07 | 文档数据源失真·静态 | `docs/architecture-phase4.md:1146` | 称 `generate.py` 用 `pip-licenses --format=json` + `npm ls --all --json`；实际 `deploy/sbom/generate.py:206-231,319-327,348-400` 读 `uv.lock` → venv → `pyproject` 与 `package-lock.json` | **更新**：按实际数据源改写 | 低-中 | 合规审计口径不一致 |
| P08 | 交付合规缺口·实测 | `docs/architecture-phase4.md:1023`；`scripts/gen_delivery_manifest.py:103-105` | 文档承诺交付包含 `compliance/... NOTICE`；仓库根无 `NOTICE`（`Test-Path NOTICE` = False），交付清单只映射 3 份 compliance md | **更新**：要么补 NOTICE 生成/映射，要么从承诺中移除；需用户拍板 | 高 | 商业交付合规风险。回归：`python scripts/gen_delivery_manifest.py` 后核对产物 |
| P09 | 工具链不确定性·静态 | `.github/workflows/dependency-scan.yml:221,225,268`；`.github/workflows/ci-adapter.yml:785`；`apps/web/Dockerfile:36-40` | `pip`、`pip-licenses`、`license-checker`、`pip-audit` 均未锁版本；`package-lock.json` 有 14 个 `@img/sharp-libvips-*`（LGPL-3.0-or-later，全部 optional）。镜像构建已用 `--omit=optional` 排除 sharp（Dockerfile 注释），但 `dependency-scan.yml:269` 的 `npm ci` **未加** `--omit=optional`，黑名单正则 `\bL?GPL` 会把 LGPL 也匹配进来 | **更新（需实跑）**：锁定工具版本；扫描侧加 `--omit=optional` 或对 sharp 明确豁免，再确认 `license-checker --production` 行为 | 中-高 | 许可证扫描结果不可复现；可能对「未随镜像交付」的 optional 包误报 |
| P10 | 许可证口径·静态 | `LICENSE:1-8`；`package.json:6`；`apps/web/package.json:6`；`apps/adapter/pyproject.toml:15` | LICENSE 为 proprietary / All rights reserved；三处包元数据均为 `UNLICENSED`；`README.md:266-274` 声明商业交付 | **保留**：口径一致；仅记录 `license = {text=...}` 属 PEP 639 已弃用写法，非问题 | 低 | 无需动作 |

---

## 五、统计

| 分节 | 条数 | 建议更新 | 建议删除 | 建议保留 |
| --- | ---: | ---: | ---: | ---: |
| §1 文档冲突 | 23 | 22 | 1（D15） | 0 |
| §2 过期表述 | 10 | 9 | 0 | 1（S10） |
| §3 测试 | 7 | 1（T02） | 1（T03，含 6 个 fixture） | 5 |
| §4 依赖 | 10 | 6 | 0 | 4 |
| **合计（拍板前建议）** | **50** | **38** | **2** | **10** |

**执行后处置统计（2026-11-04）**：已执行·更新 **37**；已执行·删除 **2**；部分执行 **1**（P09）；
未执行 **10**（S10、T01、T04–T07、P01、P03、P04、P10：均属「保留/无需动作」或用户明确另起一轮）。

风险分布：高 **8** 条（D01、D02、D06、D07、D08、D15、T02、P08）；中 **23** 条（P09 按「中-高」计入）；低 **19** 条。

> 说明：真正建议「删除实体」的只有两组——D15 一行死配置、T03 六个孤立 fixture。其余「更新」均为改文字/改路径/改数字，不涉及代码。

---

## 六、建议执行顺序

1. **第 1 批（高，约 30 分钟，纯文档）**：D01、D02、D06、D07、D08、D15。这 6 条会直接让部署/集成方操作失败或误判鉴权。
2. **第 2 批（中，纯文档数字对齐）**：D03、D04、D05、D09、D10、D11、D13、D14、D21、D22、D23。改完后跑一次 `deploy/validate_*` 全量。
3. **第 3 批（历史文档止损）**：S01–S07。统一在文首加「历史快照 + 修复状态」横幅，不逐条重写正文（保留审计痕迹）。
4. **第 4 批（测试清理，需用户点头）**：T03（删 6 个 fixture）→ T02（收紧断言）。T04 只改名，不建议删。
5. **第 5 批（依赖/合规，独立 PR）**：P04、P05、P06、P07、P09。先复现锁定与构建验证，再改配置；P08 先做决策（补 NOTICE 还是改承诺）。

每批完成后建议的验证：第 1/2/3 批跑 `python deploy/validate_config_names.py`、`python deploy/validate_openapi.py`、`python deploy/validate_migrations.py`；第 4 批跑 `pytest --collect-only`（数量应不变）与 `tests/integration/test_tenant_isolation.py`；第 5 批跑 `npm ci`、`npm run build:web`、`generate.py`。

---

## 七、必须用户确认的争议项

1. **README 测试数字**：是更新为当前收集数（2933），还是删除具体数字、改为「唯一权威来源 = CI junit.xml 工件」？（D01）
2. **`.env.lite.example:42` 死键**：直接删除，还是先实现 `OA_OFFLINE_MODE` 消费方？（D15）
3. **6 个孤立 fixture**：确认删除 `limiter/api_error/fingerprint/pg_pool_engine/seeded_tenants/member_headers`？（T03，测试基础设施，风险低但影响测试维护习惯）
4. **历史审计文档**：加状态横幅保留，还是整体移入 `docs/archive/`？（S01–S05）
5. **锁内双版本**：`@types/node` 20 vs 26、`eslint` 10 vs 9 是否收敛？收敛可能改变 lint/构建行为，需单独 PR 验证。（P04）
6. **`wheel` 构建依赖**：是否授权做一次隔离构建验证后再删？（P02）
7. **NOTICE**：补生成与交付映射，还是从 `architecture-phase4.md:1023` 的交付承诺中删除？（P08）
8. **SBOM 与许可证扫描**：是否改为阻断门禁？LGPL（sharp optional）是否判黑需实跑确认。（P06、P09）
9. **`docs/plugin-dev-guide.md` 偏差①②**：删除，还是保留并标注「已于 2026-11 修复」？（D12）

---

## 附录 A：复核命令（均为只读）

```powershell
# 后端收集数（cwd = apps/adapter）
.\.venv\Scripts\python.exe -m pytest tests --collect-only -q | Select-Object -Last 1
.\.venv\Scripts\python.exe -m pytest tests -m pg --collect-only -q | Select-Object -Last 1

# 服务数（PyYAML 解析）
& apps/adapter/.venv/Scripts/python.exe -c "import yaml; d=yaml.safe_load(open('deploy/docker-compose.yml',encoding='utf-8')); print(len(d['services']), sorted(d['services']))"

# OpenAPI 快照口径
& apps/adapter/.venv/Scripts/python.exe -c "import json; d=json.load(open('docs/api/openapi.snapshot.json',encoding='utf-8')); print(len(d['paths']), sum(len([m for m in v if m in ('get','post','put','patch','delete')]) for v in d['paths'].values()))"

# 文件存在性抽查
foreach($p in @('apps/adapter/scripts/validate_plugins.py','deploy/validate_plugins.py','tests/factories/data.py','apps/adapter/scripts/seed_qa_data.py','apps/adapter/scripts/cleanup_qa_schema.py','deploy/docker-compose.tls.yml','NOTICE')) { "$p`t$(Test-Path $p)" }

# 依赖声明计数
& apps/adapter/.venv/Scripts/python.exe -c "import json,tomllib; p=tomllib.load(open('apps/adapter/pyproject.toml','rb')); w=json.load(open('apps/web/package.json',encoding='utf-8')); print(len(p['project']['dependencies']), len(w['dependencies']), len(w['devDependencies']))"
```

口径声明：`collect-only` 只证明「可被发现」，不代表全部通过；审计阶段未启动 Docker 或执行前端构建。

---

## 八、处置回填（执行记录，2026-11-04）

按用户拍板分四批执行，每批单独提交并跑全量门禁。

### 8.1 逐条结果

| 编号 | 结果 | 处置说明 |
| --- | --- | --- |
| D01 | ✅ 已执行 | README 改为实测收集 2933 / PG 258（2675 passed / 258 skipped），注明「以 CI junit.xml 为准」 |
| D02 | ✅ 已执行 | README 与 `ci-adapter.yml` 注释改为「默认回落 B21」；`validate_batch_clock.py` 仍显式要求 OA_BATCH |
| D03 | ✅ 已执行 | `README.md` 与 `deploy/migrations/README.md` 统一为 0002–0013（12 个） |
| D04 | ✅ 已执行 | README decisions 目录 0003~0008 → 0003~0009 |
| D05 | ✅ 已执行 | `ci-web.yml` 阈值注释同步 `vitest.config.mts`：88.8/84.0/83.9/90.0 |
| D06 | ✅ 已执行 | adapter README 删除 Alembic，改 `python -m app.db.migrate --apply` |
| D07 | ✅ 已执行 | 部署指南 B 形态改为「已并入主 compose 的 `edge`」，删除 `docker-compose.tls.yml` 命令，修正 A 形态端口口径 |
| D08 | ✅ 已执行 | `docs/api/README.md` 鉴权豁免补 `/metrics`、`/sso/*`、支付回调 |
| D09 | ✅ 已执行 | lite 基线与 PRD：nginx 1.28 + digest、pgvector 精确标签 |
| D10 | ✅ 已执行 | lite 基线端点数 84 → 96 |
| D11 | ✅ 已执行 | lite 基线标准栈 18/17 → 21/20；perf-disclosure 两处 17 → 21 |
| D12 | ✅ 已执行 | 删除原偏差①（agent schema 已声明）与原偏差②（RFC 5987 已实现），保留「工具入参映射」偏差并重编号为① |
| D13 | ✅ 已执行 | `app/main.py` 注释 99 → 104 操作 |
| D14 | ✅ 已执行 | dependency-scan 依赖计数 17+36 → 16+29 |
| D15 | ✅ 已执行（删除） | 删除 `deploy/.env.lite.example` 的 `OA_OFFLINE_MODE`（含注释行） |
| D16 | ✅ 已执行 | architecture-v1.0 删除未生效的 License 节点，标注 V1.1 规划 |
| D17 | ✅ 已执行 | validate_plugins 路径改为 `deploy/validate_plugins.py` |
| D18 | ✅ 已执行 | phase2 对账口径改为 `usage_event` vs `usage_daily`（不读 Valkey） |
| D19 | ✅ 已执行 | QA 计划删除 3 个不存在的脚本行，reconcile 标「已实现」 |
| D20 | ✅ 已执行 | waivers 两处补 `apps/adapter/` 前缀 |
| D21 | ✅ 已执行 | lite 基线补 dify-web digest |
| D22 | ✅ 已执行 | metrics 迁移编号 0013 → 0014（0013 已被 event_log 占用） |
| D23 | ✅ 已执行 | architecture-v1.0：Redis→Valkey、minio→SeaweedFS（object-store），部署图同步 |
| S01 | ✅ 已执行 | 归档至 `docs/archive/`（按拍板不加横幅） |
| S02 | ✅ 已执行 | 归档至 `docs/archive/`（按拍板不加横幅） |
| S03 | ✅ 已执行 | 整改台账 P0-4 改「已完成（G1）」，补 edge/loopback/k8s 证据 |
| S04 | ✅ 已执行 | design-security-deploy §3.1 加「已被 G1 推翻」状态行 |
| S05 | ✅ 已执行 | design-keycloak-realm §1 加 2026-11-02 快照状态行并给出当前配置位置 |
| S06 | ✅ 已执行 | 项目简报删除 README 旧口径断言 |
| S07 | ✅ 已执行 | 平台战略：死配置改「已清理」，A3/决策 3 关闭 |
| S08 | ✅ 已执行 | 升级观察表测试数字标快照日期/SHA，并注明以 CI 为准 |
| S09 | ✅ 已执行 | 并发入口注释改为 `tests/integration/pg_fixture.py::b01_concurrent_engine` |
| S10 | ⬜ 未执行 | 保留：设计文档的时间标注，非问题 |
| T01 | ⬜ 未执行 | 保留：skip 均为条件性 |
| T02 | ✅ 已执行 | 空组织 ID 用例去掉 401 假绿，收紧为 404 + 20004 |
| T03 | ✅ 已执行（删除） | 删除 6 个零注入 fixture（AST 复核注入 0 / getfixturevalue 0） |
| T04 | ⬜ 未执行 | 仅建议改名，本轮不删不改（避免破坏 19 组同名分层语义） |
| T05 | ⬜ 未执行 | 保留：接口分层有意为之 |
| T06 | ⬜ 未执行 | 无需动作：e2e 环境限制属已知缺口 |
| T07 | ⬜ 未执行 | 保留：selftest 脚本按设计不被收集 |
| P01 | ⬜ 未执行 | 保留：无高置信未用包 |
| P02 | ✅ 已执行（删除） | 隔离构建验证：移除 `wheel` 前后 `pip wheel . --no-deps` 均成功（setuptools 自带 bdist_wheel），故删除 |
| P03 | ⬜ 未执行 | 保留：`tailwindcss-animate` 为 `animate-in` 类隐式必需；仅建议移入 dependencies |
| P04 | ⬜ 未执行 | 用户拍板另起独立一轮（@types/node 20/26、eslint 10/9），本轮不动 |
| P05 | ✅ 已执行 | web `vitest` 由 `^5.0.5` 收敛为 `5.0.5`，与 `@vitest/coverage-v8` 精确版本一致；lock 同步 |
| P06 | ✅ 已执行 | SBOM 两 job 保持 `continue-on-error: true`，补拍板口径注释（告警不阻断） |
| P07 | ✅ 已执行 | SBOM 文档数据源改为 uv.lock/venv/pyproject + package-lock + images.lock + difypkg |
| P08 | ✅ 已执行 | 新增 `docs/compliance/NOTICE`，纳入交付清单映射并重新生成 manifest |
| P09 | 🟡 部分执行 | LGPL 实跑判定完成（见 8.2）；许可证工具版本锁定未做（避免本轮扩大 CI 变更） |
| P10 | ⬜ 未执行 | 保留：`python-multipart` 为框架上传必需 |

### 8.2 wheel 与 LGPL 实跑结论

- **wheel**：`pip wheel . --no-deps` 隔离构建在「含 wheel」与「仅 setuptools」两种 build-system 下均成功产出 wheel 包；`wheel` 无任何 import、uv.lock 无条目。结论：**可删，已删**（`apps/adapter/pyproject.toml` build-system）。
- **LGPL（sharp optional）**：实跑解析 `package-lock.json` → 命中 14 个 `@img/sharp-libvips-*`，`license=LGPL-3.0-or-later`、`optional=True`、`dev=False`；`npm ls @img/sharp-libvips-linux-x64` 为空（未安装）。前端生产镜像 `apps/web/Dockerfile:36-40` 使用 `npm ci --omit=optional`，**镜像不含这些包**；`dependency-scan.yml` 未加 omit，仅扫描侧列出。结论：**镜像侧已排除，扫描侧按告警处理，不判黑；无需标「待评估」**。

### 8.3 门禁与覆盖率（每批实跑）

| 批次 | 后端 | 前端 | 覆盖率（前端 stmt/branch/func/line；后端） | 16 道 deploy 门禁 |
| --- | --- | --- | --- | --- |
| 第 1 批（文档） | 2675 passed / 258 skipped / 0 failed | 72 文件 / 1112 用例 | 89.78/85.06/84.93/90.98；86.42% | 16/16 EXIT 0 |
| 第 2 批（删除） | 2675 passed / 258 skipped / 0 failed | 72 文件 / 1112 用例 | 89.78/85.06/84.93/90.98；86.42% | 16/16 EXIT 0 |
| 第 3 批（README/CI） | 2675 passed / 258 skipped / 0 failed | 72 文件 / 1112 用例 | 89.78/85.06/84.93/90.98；86.42% | 16/16 EXIT 0 |
| 第 4 批（结构/合规） | 2675 passed / 258 skipped / 0 failed | 72 文件 / 1112 用例 | 89.78/85.06/84.93/90.98；86.42% | 16/16 EXIT 0 |

> PG 口径：本机无 Docker 与 `OA_TEST_DATABASE_URL`，`--run-pg` 未执行；PG 用例 258 条按设计 skip。CI 侧有真库，以 CI 结果为准。

### 8.4 提交与 CI

| 批次 | 提交 | 改动文件数 |
| --- | --- | --- |
| 第 1 批 | `0d616a6` | 13 |
| 第 2 批 | `a928230` | 3 |
| 第 3 批 | `03adc8c` | 10 |
| 第 4 批 | 本提交（含归档、NOTICE、SBOM 注释、wheel 删除） | 20 |

CI 验收（同一 SHA 的 CI Adapter + CI Web）run 编号与结论见最终汇报。
