# 全仓审计报告（OA-AUDIT-001）

| 项 | 内容 |
| --- | --- |
| 报告编号 | OA-AUDIT-001 |
| 审计日期 | 2026-10-22 |
| 审计范围 | 全仓 Monorepo（`apps/adapter`、`apps/web`、`deploy`、`docs`、`.github/workflows`） |
| 代码基线 | `main` @ `dc3ff1e`（工作区另有 1 项未提交修改 + 1 个游离文件） |
| 审计方式 | **实测复跑**（非纸面核对）：本地全量执行 17 道 CI 门禁 + 打包构建 + 静态红线扫描 |
| 审计结论 | **有条件通过**：工程治理成熟度显著高于行业同类，但存在 **3 项 P0 + 1 项潜伏 P1**（P0-1 初判过重，已于 2026-10-23 下调），其中 P0-2 会导致**隔离失效**、P0-4 会使**门禁永久红** |

---

## 一、审计方法与覆盖度

本审计不采信任何文档的"自述绿灯"，全部结论来自**本地实跑**：

| 手段 | 说明 |
| --- | --- |
| 门禁复刻 | 逐条复刻 `.github/workflows/*.yml` 中的 shell 判据，在本地同版本环境执行 |
| 打包实测 | 实际 `pip wheel` 构建产物并解包核对（非推断配置） |
| 静态红线扫描 | 底座直连、许可证、密钥硬编码、镜像浮动标签、占位实现、技术术语泄漏 |
| 交叉核对 | 文档声明 ↔ 代码事实 ↔ 部署配置三向比对 |

**规模基线（实测）**

| 维度 | 数值 |
| --- | --- |
| 纳入版本控制文件 | 439（py 236 / tsx 80 / md 47 / ts 16 / sql 9 / yml 6） |
| 适配层生产代码 | 117 个 `.py`，约 54,900 行（`app/` 目录） |
| 适配层测试代码 | 约 42,431 行 |
| 前端源码 | 约 22,522 行；19 个页面 / 53 个组件 |
| 对外端点 | **84**（core 30 + phase2 38 + phase3 16），实测 0 未实现 |
| 错误码 | 63 个（架构登记 27 码全部落地） |
| 插件声明 | 6 份（3 工作台 + 3 轻量） |

---

## 二、门禁实测矩阵（核心证据）

| # | 门禁 | CI 声明 | **实测结果** | 判定 |
| --- | --- | --- | --- | --- |
| 1 | ruff check | 必过 | 227 files，All checks passed | ✅ |
| 2 | ruff format --check | 必过 | 227 files already formatted | ✅ |
| 3 | mypy | 零 error | Success，117 source files | ✅ |
| 4 | G-13 错误码三处一致 `--strict` | 必过 | 27/27 落地、63 码全覆盖、内部码 17002 零外泄 | ✅ |
| 5 | G-26 插件声明 | 必过 | 6 份声明，能力与原语均在白名单 | ✅ |
| 6 | 迁移门禁（静态） | 必过 | 4 条迁移，标签成对、DDL 有守卫 | ✅ |
| 7 | 依赖门禁 | 必过 | 16 条运行时依赖 | ✅ |
| 8 | 镜像清单校验 | 必过 | compose 14 引用 ⊆ lock 14 登记，`latest` 浮动标签 **0 命中** | ✅ |
| 9 | G1-G5 端点基线 | 必过 | core=30 / phase2=38 / phase3=16，未实现 0 | ✅ |
| 10 | Schema 留存门禁 | 必过 | 180 天口径一致 | ✅ |
| 11 | ESLint（零 warning） | 必过 | 通过 | ✅ |
| 12 | tsc --noEmit | 必过 | 通过 | ✅ |
| 13 | 前端术语/占位/页面三态/契约/宿主门禁 | 必过 | 全部通过；能力门禁 1 处 warn（见 P2-2） | ✅ |
| 14 | **pytest + 覆盖率 ≥85%** | 必过 | **84.19%（未达标）；1 failed / 1456 passed / 141 skipped** | ❌ |
| 15 | **底座调用出口唯一性** | 必过 | **命中 1 个违规文件**（`services/agent_service.py`） | ❌ |
| 16 | **前端底座字样扫描** | 必过 | **5 处命中**（2 类规则误报） | ❌ |
| 17 | **打包完整性（wheel）** | 无门禁 | **缺失 `app/business` / `app/db` / `app/plugins` 共 52 个源文件** | ❌ |

> **关键推论**：14/15/16 三道红，意味着 **CI 在现有配置下不可能全绿**。而仓库历史中 GitHub Actions 额度已耗尽、workflow 从未真正执行过——所有"CI 全绿"的表述均为**未在 CI 上验证过的推断**。

---

## 三、P0 级问题（阻塞交付，必须立即整改）

> 条目编号沿用初判（P0-1 仍叫 P0-1），但 P0-1 的**严重度已下调为 P1（潜伏）**，
> 原因见该节"影响"段与文末自我修正说明。其余 3 条维持 P0。

### P0-1 · 适配层 wheel 缺失三个核心包（**影响面已修正：潜伏缺陷，非启动即崩**）

- **证据**：`apps/adapter/pyproject.toml` → `[tool.setuptools] packages` 只声明了 10 个子包，**未包含** `app.business`（22 文件）、`app.db`（23 文件）、`app.plugins`（7 文件）。
- **实测**：执行 `pip wheel . --no-deps` 得到 `officeai_adapter-1.0.0-py3-none-any.whl`（72 个文件），解包核对：

  ```
  wheel 内 app 子包: ['api', 'clients', 'core', 'middleware', 'registry', 'schemas', 'services', 'tools']
    app/api       16 个文件
    app/business   0 个文件   ← 缺失
    app/db         0 个文件   ← 缺失
    app/plugins    0 个文件   ← 缺失
  ```

- **影响（2026-10-23 复核实测修正，原判过重）**：
  缺陷**真实存在于交付镜像中**——对运行中的 `officeai/adapter:1.0.0` 实测，
  `/opt/venv/lib/python3.13/site-packages/app/` 只有 `api/clients/core/main/middleware/registry/schemas/services/tools`，
  **确无 `business` / `db` / `plugins`**；`cd /tmp`（脱离源码目录）后
  `import app.business` 直接 `ModuleNotFoundError`。
  但**容器本身能正常启动**：Dockerfile 运行阶段有 `COPY app ./app` + `WORKDIR /app` +
  `CMD uvicorn app.main:app`，`cwd` 下的源码树**遮蔽**了 site-packages，
  实测容器内 `/app/app/business` 存在、6 份插件声明就位、服务 healthy。
  本地全绿同理（editable 的 `.pth` finder 指向源码树）。
  → 因此这是**潜伏缺陷**，触发条件明确且苛刻程度中等：
  ① 运行方式改为 wheel-only（去掉 `COPY app` 或换 `WORKDIR`）即刻崩；
  ② 任何外部 `pip install officeai-adapter` 的消费方拿不到业务能力；
  ③ 控制台脚本 `officeai-adapter` 在非 `/app` 目录运行同样失败。
  **据此把本条从 P0 下调为 P1（潜伏）**——它不阻塞阶段三交付，但会在交付形态
  任何变化时立即引爆，属"迟早要还"的类型，修复仍然必要。
- **违反原则**：「首版即商用，拒绝 Demo 级产物」（潜伏缺陷同样是债，只是不立即致命）。
- **整改**：
  1. `[tool.setuptools] packages` 补全 `app.business`、`app.db`、`app.plugins`（含各自子包）；
  2. `[tool.setuptools.package-data]` 增补 `"app.plugins" = ["manifests/*.json"]`；
  3. 新增 CI 门禁：构建 wheel 后解包校验「源码子包集合 ⊆ wheel 子包集合」，并对产物执行一次 `python -c "import app.main"` 冒烟。

### P0-2 · 多租户隔离修复未入库；从 HEAD 部署等于关掉隔离

- **证据**：工作区 `deploy/docker-compose.yml` 有一处**未提交**修改（7 增 1 删），把适配层 DSN 改为优先取 `.env` 的 `OA_DATABASE_URL`（`officeai_app`，NOSUPERUSER + NOBYPASSRLS），此前写死 `${OA_POSTGRES_USER}`（**库属主/超级用户**）。
- **影响**：超级用户**永久绕过行级安全策略**（`FORCE ROW LEVEL SECURITY` 同样约束不住）。当前 `main` @ `dc3ff1e` 上仍是旧写法 → **任何人按 HEAD 部署，RLS 全部不生效，多租户隔离形同虚设**；且 `/readyz` 会判 `db_privilege=false`、容器永远不 healthy。
- **整改**：立即提交该修改（建议单列为 `fix(deploy)` hotfix），并在验收报告中标注"隔离生效的最低版本"。

### P0-3 · 两枚内部 API 密钥以明文兜底值入库，且未登记到环境变量模板

- **证据**：`deploy/docker-compose.yml` 共 3 处（`git show HEAD` 已确认入库于 `010db32`）：

  ```
  259:  PLUGIN_DAEMON_KEY: ${OA_PLUGIN_DAEMON_KEY:-lYkiYYT6owG+71oLerGzA7GXCgOT++6ovaezWAjpCjf+Sjc3ZtU+qUEi}
  261:  PLUGIN_DIFY_INNER_API_KEY: ${OA_PLUGIN_INNER_API_KEY:-QaHbTe77CtuXmsfyhR7+vRjI/+XbV1AaFy691iy+kGDv2Jvy0/eAh8Y1}
  392:  SERVER_KEY: ${OA_PLUGIN_DAEMON_KEY:-lYkiYYT6owG+71oLerGzA7GXCgOT++6ovaezWAjpCjf+Sjc3ZtU+qUEi}
  ```

- **对比**：同文件其余 20+ 枚密钥一律使用 `${VAR:?必须在 .env 中设置}`（缺失即拒绝启动）；唯独这两枚给了明文兜底。
- **`.env.example`（77 个变量）中完全没有 `OA_PLUGIN_DAEMON_KEY` / `OA_PLUGIN_INNER_API_KEY`** → 部署者无从知晓要配置。
- **影响**：私有化部署默认使用**已提交进公开/半公开仓库的常量密钥**；plugin_daemon（5002）与 Dify 内部 API 可被任意调用。
- **整改**：
  1. 改为 `:?必须在 .env 中设置`；
  2. 在 `.env.example` 登记两变量并说明用途；
  3. **轮换这两个密钥**（现有值视为已泄露）；
  4. 新增门禁：扫描 compose 中形如 `${VAR:-<长度≥16的高熵串>}` 的兜底，一律判红。

### P0-4 · CI 覆盖率与用例门禁在当前配置下不可能通过

- **实测**（无 PG 环境，与 CI 环境等价）：

  ```
  FAIL Required test coverage of 85% not reached. Total coverage: 84.19%
  1 failed, 1456 passed, 141 skipped in 500.07s
  ```

- **根因 1（覆盖率）**：`ci-adapter.yml` **无 postgres service**（workflow 注释自认"integration 用例一律 SKIP"）。141 条 PG 依赖用例全部跳过，其中含 **`test_tenant_isolation.py` 15 条租户隔离用例、`test_task_repo_pg.py` 28 条、`test_plugin_repo_pg.py` 14 条、`test_schema_baseline.py` 6 条**。分支覆盖率实测仅 **73.84%**（行 86.35%）。
  → **多租户隔离这条最高红线，在 CI 中从未被自动验证过。**
- **根因 2（失败用例）**：`tests/integration/test_plugin_contract_skeleton.py::test_d1_deferred_registry_matches_markers` 依赖环境变量 `OA_BATCH`，缺失即抛 `RuntimeError`。但 `OA_BATCH` 在 CI 中**未注入**、`pyproject.toml` 未声明、`conftest.py` 未提供默认 → **CI 必现 1 红**。
- **整改**：
  1. `ci-adapter.yml` 增加 `services: postgres` + `OA_TEST_DATABASE_URL`，让隔离/迁移类用例真跑；
  2. CI 注入 `OA_BATCH`（当前批次，如 `B30`）或给 `current_batch()` 加显式 skip 条件；
  3. 在 CI 与文档中明确区分「无 PG 口径」与「含 PG 口径」两套覆盖率基线，避免用一套数字覆盖两种环境。

---

## 四、P1 级问题（影响可维护性与合规可追溯）

### P1-1 · 底座出口唯一性门禁自身违规

`apps/adapter/app/services/agent_service.py:156` 直接读 `settings.dify_base_url`，而 `dependency-scan.yml` 门禁 1 规定"底座 URL 只允许出现在 `core/config.py` 与 `clients/dify_client.py`"。**该门禁一旦运行即红**。

**根因是设计缺口**：Agent 域需要用独立的 `dify_agent_app_key` 构造独立客户端，但 `clients` 层未提供按 app_key 建实例的工厂，service 层只好自己拼配置。

**整改**：在 `clients/dify_client.py` 增加 `DifyClient.for_app_key(api_key)` 工厂（内部读 `settings.dify_base_url`），service 层只传 key；或把 agent_service 加入门禁白名单并在架构文档中显式登记理由（不推荐——白名单会稀释红线）。

### P1-2 · 前端底座字样门禁存在 2 类误报，规则过宽

`dependency-scan.yml` 门禁 1a 的 `FORBIDDEN` 规则命中 5 处，均为误报：

| 命中 | 原因 |
| --- | --- |
| `src/lib/api-client.ts:1171`、`business-errors.ts:71/72/167` | 规则 `5001`（本意匹配底座端口）**子串命中计费域错误码 `15001`** |
| `scripts/check-banned-terms.mjs:64` | 扫描脚本**自身的负样本 fixture** 含 `milvus` 字样 |

**整改**：端口规则改为 `\b5001\b` 或 `dify-api:5001`；扫描目标排除 `scripts/`（或 fixture 用拆分写法）。**误报比漏报更危险**——它会让团队习惯性给门禁打绿灯或跳过。

### P1-3 · 验收报告口径与可复现状态不一致

| 项 | `docs/phase3-acceptance-report.md` 声明 | 本次实测 |
| --- | --- | --- |
| 测试结果 | **1573 passed / 0 failed / 0 skipped** | 1456 passed / **1 failed** / **141 skipped** |
| 总覆盖率 | **90.78%**（分支 80.89%） | **84.19%**（分支 73.84%） |
| 前提 | 未标注 | 报告口径需 `--run-pg` + `OA_BATCH`，CI 环境均不满足 |

差异可解释（报告为含 PG 的全量实跑），但**报告未把"必须在含 PG 环境并设置 OA_BATCH 才能复现"写成前置条件**，导致 README 与验收报告共同给出一个 CI 无法复现的数字。

**整改**：验收报告与 README 的数字一律增加"口径脚注"（是否含 PG、是否设 OA_BATCH、耗时）；README 当前写的"1573 passed / 0 failed / 0 skipped"建议改为"1573（含 PG 全量）／CI 无 PG 子集 1456"。

### P1-4 · 文档代码基线不可追溯

两份验收报告的代码基线均写 `` `develop` @ `xxx` ``（`3b82df2` / `d363909`），但**当前仓库只有 `main` 分支**（`git branch -a` 无 `develop`）。commit 本身存在（`d363909` 距 HEAD 6 个提交），但基线分支名已失效。

**整改**：统一改为 `main @ <sha>`，或在仓库恢复 `develop` 分支；Git Flow 约定（文档要求 develop 主线）与实际（仅 main）也需对齐。

---

## 五、P2 级问题（技术债与维护陷阱）

| # | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| P2-1 | **`deploy/init-rls.sql` 已成孤儿文件**：compose 只挂载 `00/20/22/30` 四个脚本，**未挂载它**，但其内容仍含 `audit_log` 建表与旧策略 `tenant_isolation_*`（已被 phase2 的 `p_tenant_isolation` 取代）。compose 第 53 行注释还引用不存在的 `10-init-rls.sql` | `deploy/docker-compose.yml:50-62` | 删除或明确标注为"历史存档，勿改"；修正注释引用 |
| P2-2 | **插件多要权限**：`plg_hr_admin` 声明 `org.directory.read`，但无步骤或输入使用它。能力门禁为 warn-only，**不会拦截** | `npm run scan:capability` 输出 | 按最小权限原则移除；考虑将"多余能力"升级为硬失败 |
| P2-3 | **幂等后端默认 `memory`**：`.env.example` 中 `OA_IDEMPOTENCY_BACKEND=memory`。多副本部署或重启后幂等键即失效，与"写接口必须带 Idempotency-Key"的强约束不匹配 | `deploy/.env.example` | 生产默认改为 Valkey；或在启动时对 `memory` + 多副本发出告警 |
| P2-4 | **`OA_CREDENTIAL_ENCRYPTION_KEY` 默认为空**：`config.py:136` 默认 `""`，需确认是否有启动期校验，否则凭据可能明文落库 | `app/core/config.py:131-137` | 增加启动校验：生产环境为空即拒绝启动（对齐 `/readyz` 的 `db_privilege` 自检风格） |
| P2-5 | **文档版本号自相矛盾**：`architecture-v1.0.md` 标题 `V1.0.5` / 版本字段 `V1.0.1` / 修订记录已到 `V1.0.7`；多篇文档日期为 `2026-10-23`（晚于审计日 10-22） | `docs/architecture-v1.0.md:1-15` | 统一版本号三处取值；日期回填 |
| P2-6 | **根目录游离文件 `.seed.sql`**（未跟踪，含测试租户 `tnt_local_test` 的计费种子数据），未加入 `.gitignore`，有误提交风险 | 仓库根目录 | 移入 `deploy/` 或加入忽略 |
| P2-7 | **镜像标签与"最新稳定版"策略不符**：`ubuntu/squid:6.13-25.04_beta`（**beta** 标签）、`dify-plugin-daemon:0.6.10-local`（`-local` 变体） | `deploy/images.lock` | 评估 squid 是否可换非 beta 稳定标签；记录 `-local` 变体的选择理由 |
| P2-8 | 文档与代码注释中的过期引用：`grant_repo.py:495` 仍写"待 B08 组织域接入后补齐"，但 B08（14005-14008）已落地 | `app/db/repositories/grant_repo.py:495` | 清理过期注释 |

---

## 六、审计亮点（显著优于同类项目，建议保持）

1. **镜像供应链治理扎实**：`images.lock` 为唯一事实来源，`latest` 浮动标签 **0 命中**，并记录了 MinIO 仓库下架、Grafana AGPL 不可获取等**实测结论**（含"合成标签"事故教训），避免了"文档正确、部署拉不动"的假绿。
2. **多租户隔离是真双保险**：RLS（strict 表 21 + shared 表 3 / phase3 另 7+5）覆盖完整，`officeai_app` 强制 `NOSUPERUSER NOBYPASSRLS`，`/readyz` 带 `db_privilege` 自检——**主动识别出"超级用户绕过 RLS"这一极易被忽略的陷阱**，投入度超出常规。
3. **平台模式哨兵有代码级守卫**：`assert_tenant_allowed(..., allow_platform=False)` 默认收紧，`__platform__` 只对运营路由放开，且 `set_config` 为事务级、结束自动复位。
4. **门禁"必须能红"的机械证明**：G-26 / G-18 / G-27 / G-30 / 迁移门禁都配了负样本自检（断言退出码 = 1），杜绝假绿灯。这是很成熟的工程实践。
5. **技术黑盒化落到实处**：前端 95 个源文件 + 63 个构建产物（2188 条可见文本）术语扫描 0 命中；内部错误码 17002 在前端兜底表 / message / OpenAPI 示例三处 0 外泄。
6. **错误码三处一致自动化**：63 个码在架构文档 / `errors.py` / `business-errors.ts` 之间自动核对，并含"内部码外泄终检"。
7. **类型与静态检查零妥协**：mypy 在 `disallow_untyped_defs=true` 下对 117 个文件零 error；ruff 启用 `ANN`/`S`/`SLF`/`ASYNC` 等 12 组规则仍全绿。
8. **占位实现几乎为零**：生产代码仅 4 处 `NotImplementedError` 且全在抽象基类；前端 `TODO/FIXME` 0 处。

---

## 七、整改优先级建议

```
立即（本周内，阻塞任何对外交付）
  P0-1  补全打包清单 + 新增 wheel 校验门禁
  P0-2  提交 OA_DATABASE_URL 隔离修复
  P0-3  移除硬编码密钥兜底 + 轮换 + 登记 .env.example
  P0-4  CI 加 postgres service + 注入 OA_BATCH

阶段四启动前
  P1-1  底座出口工厂化（或显式白名单）
  P1-2  修正前端扫描规则误报
  P1-3  文档数字加口径脚注
  P1-4  统一代码基线分支名

技术债窗口
  P2-1 ~ P2-8
```

**最后一条建议**：本次审计暴露出的共性问题不是"某段代码写错了"，而是**「门禁写在 CI 里，但 CI 从未真正跑过」**——P0-3（密钥兜底）、P0-4（门禁必红）、P1-2（扫描误报）都是典型的"只有真跑才会暴露"的缺陷。恢复 GitHub Actions 额度、让 17 道门禁在每次推送上真跑一次，其价值高于本次列出的任何单条整改。

> **本报告的自我修正（2026-10-23）**：P0-1 原判"容器一启动即崩"**过重**。
> 经对运行中镜像实测，缺陷真实存在（site-packages 确缺三包），但被
> `WORKDIR /app` + `COPY app ./app` 的源码树遮蔽，容器可正常服务。
> 已下调为 P1（潜伏），详见该节"影响"段。留此记录以说明：
> **审计结论也要能被后续实测推翻**，否则报告本身就成了新的"自述绿灯"。

---

*本报告全部结论均基于 2026-10-22 对 `main` @ `dc3ff1e` 的实测复跑，可复现。*

---

## 八、整改状态（附录，2026-10-22 当日闭环）

本轮仅处理 P0 与 P1，P2 及以下按指示暂不处理。

| 编号 | 问题 | 修复提交 | 状态 |
| --- | --- | --- | --- |
| P0-1 | 打包清单缺 `app.business` / `app.db` / `app.plugins`；镜像 site-packages 实测确缺，靠 cwd 源码树遮蔽（**已由 P0 下调为 P1 潜伏**） | `d55a959` | ✅ 已修复 |
| P0-2 | 应用角色 DSN 覆盖修复未入库，HEAD 部署等于关闭 RLS | `a5893dd` | ✅ 已入库 |
| P0-3 | 内部 API 密钥明文兜底（私有化 3 处 + SaaS 4 处） | `fc3df7b` | ✅ 已修复（**密钥待轮换**） |
| P0-4 | CI 无 PG 服务 + `OA_BATCH` 缺失 → 覆盖率门禁必红 | `0eb5df3` | ✅ 已修复 |
| P1-1 | `agent_service` 直读底座地址，违反唯一出口 | `23fc0ff` | ✅ 已修复 |
| P1-2 | 前端底座字样门禁 2 类误报（`15001` / fixture） | `bc5aa37` | ✅ 已修复 |
| P1-3 | 验收数字缺口径脚注 | `9894646` | ✅ 已补 |
| P1-4 | 代码基线写 `develop`，实际只有 `main` | `9894646` | ✅ 已统一 |

### 修复后的门禁实测（最终树）

| 项 | 结果 |
| --- | --- |
| ruff check / format | ✅ 227 files |
| mypy | ✅ 117 source files |
| pytest（`--run-pg` + `OA_BATCH=B21`） | ✅ **1598 passed / 0 failed / 0 skipped**，覆盖率 **90.75%** |
| 底座出口唯一性 | ✅ 0 违规 |
| 前端底座字样扫描 | ✅ 0 命中（真实违规仍可拦截） |
| 部署/契约门禁 8 项 | ✅ 全通过 |
| 前端扫描门禁 8 项 | ✅ 全通过（`scan:capability` 严格版因 P2-2 仍告警，CI 用 warn-only 版） |
| 新增 G-32 打包门禁 | ✅ 正样本通过 + 3 类负样本被拦截 |
| 新增 G-33 密钥兜底门禁 | ✅ 正样本通过 + 负样本行为符合预期 |

### 新增的两道门禁（防复发）

- **G-32 打包完整性**（`deploy/validate_packaging.py`）：包齐全 / 包内资源已声明 / 声明非空，
  接入 `ci-adapter.yml` 门禁 4d。
- **G-33 密钥兜底扫描**（`deploy/validate_secrets.py`）：密钥类变量的 `${VAR:-…}` 兜底
  若像真实密钥即判红，接入 `dependency-scan.yml` 门禁 1c。
  > 该门禁上线即**多查出 4 处**同类问题（`docker-compose.saas.yml`），已一并修复。

### 遗留

1. **密钥轮换**：编排中出现过的两枚常量视为已泄露，任何沿用旧值的部署必须轮换
   （代码侧无法代办，需运维执行）。
2. **P2 及以下 8 项**：见本报告第五章，按指示本轮不处理。
3. `docs/repo-gap-assessment-2026-10-22.md` 为另一份并行评估产物，未纳入本次提交。
