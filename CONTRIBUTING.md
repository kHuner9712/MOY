# 贡献指南（CONTRIBUTING）

> **适用范围**：本仓库所有开发变更（代码、配置、编排、文档、交付资产）。
> **上位规范**：`docs/engineering-standards.md`（工程化规范单一真源）。本文件是它的
> 「日常执行版」——只写**提交前做什么、改某类东西必须同步什么**；规范的完整条款、
> 权威顺序与「规范 → 机械门禁」映射表都在上位文件中，两处冲突时以上位文件为准。
>
> **基线**：`main @ fea1acd`（958 个跟踪文件）。所有引用为**文件相对路径 + 行号**，
> 行号以本文件写入时仓库状态为准；后续提交导致漂移时以符号名 / step 名为锚。
> 本文件不改动任何代码 / 配置 / CI，只描述既有约束。

---

## 1. 提交规范

格式（依据 `docs/architecture-v1.0.md:1560` §9.4）：

```
type(scope): 说明
```

**type**（该节枚举）：`feat` / `fix` / `refactor` / `test` / `docs` / `chore`。

**scope**（按本仓库实际目录，取最贴近的一层）：

| scope | 覆盖范围 |
| --- | --- |
| `adapter` | `apps/adapter/app/**`、`apps/adapter/tests/**` |
| `web` | `apps/web/**` |
| `deploy` | `deploy/**`（compose / nginx / k8s / 运维脚本 / 门禁脚本 / env 模板 / images.lock） |
| `migrations` | `deploy/migrations/**`、`deploy/init-*.sql` |
| `api` | `docs/api/**`、OpenAPI 契约与端点基线 |
| `docs` | `docs/**`（不含 `docs/api/**`） |
| `qa` | `qa/**` |
| `ci` | `.github/workflows/**` |
| `scripts` | `scripts/**` |
| `audit` | 审计报告 / 整改台账 |
| `deps` | 依赖声明与锁（`pyproject.toml` / `uv.lock` / `package*.json`） |

**说明**：写「改了什么 + 为什么」，不写「改了哪些文件」（`git diff` 已有）。
涉及门禁基线变更（`deploy/*_baseline.txt`、`docs/api/baseline-*.json`）时，说明中必须
写清**基线为什么变**与**对应审计编号**——这些文件「只许减不许增」或「冻结」
（`deploy/import_direction_baseline.txt:1-14`、`docs/api/baseline-core-30.json` 受
`deploy/validate_openapi.py` G1 保护，见 `ci-adapter.yml:831-842` 注释）。

> **【规范待固化】** 提交格式目前**没有** commit-msg hook 或 CI 断言；
> 建议脚本/工作流：`deploy/validate_commit_msgs.py` 或 `.github/workflows/commit-lint.yml`
> （见 `docs/engineering-standards.md` S2.9 / 附录 A.4）。在此之前靠 Review 人工把关。

---

## 2. 分支（主干开发 · 主理人 2026-11-07 裁决）

| 项 | 规范 | 仓库现状 | 结论 |
| --- | --- | --- | --- |
| 主干 | `main` **唯一主干**、生产分支；所有变更经 PR + Code Review + CI 全绿合入，打 tag 发版 | `main` 为唯一主干，全部交付提交直落 `main` | 一致 |
| 功能分支 | `feature/*` **短生命周期**，合入 `main` 后即删除 | 存在 `feature/event-log` | 一致 |
| 紧急修复 | `hotfix/*`，**仍须 PR + 同样门禁**方可合入 `main` | 未见 | 按需创建 |
| 集成分支 | **不设 `develop`** | `git branch -a` 无 `develop` | 已裁决不恢复 |
| 发布分支 | **不设 `release/*`** | 未见 | 已裁决 |

> **裁决记录（2026-11-07，主理人齐活林；附录 B-1 已裁决）**
> `docs/architecture-v1.0.md §9.4` 原写 Git Flow（`develop` / `release/*` / `hotfix/*`）
> 并在「现状说明」中自承「实际只有 `main` 一个分支」，形成文档与事实长期分叉。
> 按「项目总目标与执行标准不得降格、不得偏移」原则，正式采用**主干开发**：
> `main` 唯一主干 + 短生命周期 `feature/*` + PR 合入 + tag 发版，保留 `hotfix/*`（仍须 PR）。
> **不恢复 `develop`**。架构文档 §9.4 已同步修订（见该节「裁决记录」段）。
> **不得**在 PR 描述中宣称走了 `develop` / `release/*` 流程。

---

## 3. 变更清单（PR 必填）

每次 PR 描述必须包含以下 6 项，缺项视为未完成：

1. **改了什么 / 为什么**（关联任务或审计编号，如 `A-01`、`ARCH-16`、`矩阵第 4 项`）。
2. **影响面**：是否触及 ① 新增/删除端点 ② 新增/改表 ③ 新增编排形态 ④ 新增环境变量
   ⑤ 新增依赖或镜像 ⑥ 新增错误码 ⑦ 门禁基线 —— 命中任一项必须填第 5 节对应清单。
3. **机械门禁**：本次变更由哪条门禁保证（写脚本名 + CI 步骤，或明确写
   「【规范待固化】无门禁，人工验证方式：…」）。**禁止**写「已有门禁保证」却没有
   `docs/engineering-standards.md` 附录 A 中的对应条目。
4. **本地实跑结果**：第 4 节命令的退出码 / 输出摘要；带 PG 的须注明 `--run-pg`。
5. **回滚路径**：本次变更如何退（`deploy/rollback.sh` / 迁移 `*.down.sql` /
   配置回滚 / 镜像 tag 回退），以及是否需要备份（`deploy/backup.sh`）。
6. **证据留存**：若属交付变更，是否需刷新 `docs/phase4-delivery-manifest.md` 的
   「证据」列（该列是**唯一允许手改**的列，见 `docs/phase4-delivery-manifest.md:14-16`），
   以及 CI 双绿 run 编号。

---

## 4. 必须跑的门禁命令

### 4.1 本地最小集（提交前，按改动范围选跑）

**适配层 / `deploy/` / `scripts/`**（工作目录 `apps/adapter`，配置见
`apps/adapter/pyproject.toml:108-158,188-223,250-256`；与 `ci-adapter.yml:237-298` 同源）：

```bash
cd apps/adapter
ruff check . --output-format=github                              # ci-adapter.yml:237
ruff format --check .                                           # ci-adapter.yml:243
ruff check ../../deploy --config pyproject.toml                 # ci-adapter.yml:263
ruff format --check ../../deploy --config pyproject.toml        # ci-adapter.yml:266
ruff check ../../scripts --config pyproject.toml                # ci-adapter.yml:289
ruff format --check ../../scripts --config pyproject.toml       # ci-adapter.yml:292
mypy app                                                        # ci-adapter.yml:298
pytest tests -q --run-pg --cov=app --cov-fail-under=85          # ci-adapter.yml:549-557
coverage report --include="*/db/repositories/*/postgres.py" \
  --show-missing --fail-under=88                                # ci-adapter.yml:598-601
python scripts/check_error_catalog.py --strict                  # ci-adapter.yml:308
```

> 带 `--run-pg` 时 `skipped` 必须为 0（`ci-adapter.yml:560-581` 会解析 `junit.xml` 断言）。
> **覆盖率门槛（2026-11-07 附录 B-2 裁决，以 CI 为真源、只升不降）**：
> 适配层总 85%（`ci-adapter.yml:557`）、PG 仓储 88%（`:601`）、前端 vitest 四阈值
> `statements ≥ 88.8 / branches ≥ 84.0 / functions ≥ 83.9 / lines ≥ 90.0`
> （`apps/web/vitest.config.mts:305-310`）。**不得**用参数临时放宽任何一档。

**前端**（工作目录 `apps/web`，脚本见 `apps/web/package.json:8-33`；与 `ci-web.yml:73-98` 同源）：

```bash
npm run lint:web          # ci-web.yml:73（eslint --max-warnings=0）
npm run typecheck:web     # ci-web.yml:79（tsc --noEmit）
npm run test --workspace apps/web -- --coverage   # ci-web.yml:92
npm run build:web         # ci-web.yml:98
cd apps/web && npm run scan:banned && npm run scan:words \
  && npm run scan:pages && npm run scan:oidc \
  && npm run scan:contract && npm run scan:workbench \
  && npm run scan:capability                       # ci-web.yml:121/127/171/191/211/231/251
```

> 术语/页面/契约类门禁都有 `--self-test` 负样本（CI 期望退出码 1），改动扫描器本身时
> 必须一并跑（`ci-web.yml:133,155,175,195,215,233,255`）。

**配置 / 编排 / 依赖 / 镜像**（仓库根执行，全部为只读校验）：

```bash
python deploy/validate_config_names.py            # ci-adapter.yml:320
python deploy/validate_deploy.py                 # ci-adapter.yml:770
python deploy/validate_images.py                 # ci-adapter.yml:758
python deploy/validate_deps.py                   # dependency-scan.yml:539
python deploy/validate_secrets.py                # dependency-scan.yml:173
python deploy/validate_import_directions.py --baseline deploy/import_direction_baseline.txt  # :352
python deploy/validate_repository_parity.py --baseline deploy/repository_parity_baseline.txt # :375
python deploy/validate_openapi.py --strict       # ci-adapter.yml:842
python deploy/validate_packaging.py              # ci-adapter.yml:514
python deploy/validate_plugins.py                # ci-adapter.yml:404
python deploy/validate_batch_clock.py --check-docs   # ci-adapter.yml:218
python deploy/validate_gate_wiring.py           # ci-adapter.yml:479（判据存在 ≠ 判据在跑）
```

**交付 / 终验**（仓库根）：

```bash
python scripts/gen_delivery_manifest.py --check  # qa/phase4-verify.sh:291
bash qa/phase4-verify.sh                        # 9 项终验，qa/phase4-verify.sh:1-13
python qa/goldset/run.py --validate             # qa/phase4-verify.sh:374
```

> **只读纪律**：本仓库审计/复核场景下禁止运行会写入仓库的命令（`pytest` 默认缓存、
> `npm test`、`ruff format` 写回、`mypy` 默认缓存、`git stash/prune/checkout`、迁移
> `--apply`、`docker compose up`、格式化写回等）。确需本地校验时，把缓存/输出
> 重定向到系统临时目录，不得在仓库内产生新文件。

### 4.2 CI 全量（合并前必须双绿）

4 条 workflow，触发条件见各自文件头：
`ci-adapter.yml:16-31`、`ci-web.yml:11-31`、`dependency-scan.yml:15-26`、`sbom.yml:30-45`。

| workflow | 关键 job | 强制项 |
| --- | --- | --- |
| `ci-adapter.yml` | `quality-gate:45`、`registry-validation:619`、`openapi-export:802` | ruff/mypy/pytest 85% + PG 88% + skipped=0 + 17 门禁接线 + OpenAPI `--strict` |
| `ci-web.yml` | `quality-gate:48`、`terminology-gate:103`、`built-artifact-gate:267`、`format-check:309`、`e2e-gate:346` | eslint/tsc/vitest/build + G-14~G-18/G-27/G-30/G-31 + G-16 + prettier + Playwright |
| `dependency-scan.yml` | `base-layer-isolation:40`、`image-tag-existence:132`、`license-scan:190`、`dependency-manifest:503` | 底座直连扫描、镜像精确标签、SCA 黑名单、`validate_deps.py` |
| `sbom.yml` | `generate:61`、`license-scan:118`、`notice:151` | 离线包合规、SBOM、镜像清单 |

> CI 首轮 lint 口径（`ci-adapter.yml:237/243/263/266/289/292`）：工作流级
> `defaults.run.working-directory` 是 `apps/adapter`，因此 `ruff check .` 只扫适配层；
> `deploy/` 与 `scripts/` 必须显式点名并用同一份 `pyproject.toml` 配置
> （`ci-adapter.yml:246-292` 注释说明「守门的脚本自己无人看守」的成因）。
> **禁止**用 `--exit-zero`、放宽规则或扩大 `exclude` 把红变绿（`:256-258`、`:283-286`）。

---

## 5. 三类变更的强制同步清单

> **A-01 教训（必读）**：`deploy/validate_deploy.py` 的 `COMPOSE_FILES` 曾只列
> `docker-compose.yml` 与 `docker-compose.saas.yml`，**漏了真正的主交付形态
> `docker-compose.lite.yml`**，于是 R1/R2/R3 门禁对 lite 恒绿，lite 的 adapter 内联
> initdb 超级用户 DSN 长期未被发现。工作区已补入 lite 并新增 `LITERAL_SUPERUSER_DSN`
> 判据（`git diff deploy/validate_deploy.py`，**待提交**，见 `docs/engineering-standards.md` 附录 B-10）。
> **结论：新增任何编排形态，第一步是把它加进所有「编排文件集合」，否则所有门禁对新形态失效。**

### 5.1 新增编排形态（如第四套 compose / 新部署模式）

| # | 必须同步 | 位置 / 证据 | 对应门禁 |
| --- | --- | --- | --- |
| 1 | `COMPOSE_FILES` 加入新文件（R1/R2/R3 扫描集合） | `deploy/validate_deploy.py:62`（工作区已含三套） | `ci-adapter.yml:770` |
| 2 | `NAMED_CONTEXT_COMPOSE_FILES` 加入新文件（R4 lockroot） | `deploy/validate_deploy.py:99` | 同上 |
| 3 | `--self-test` 补一条该形态的 R1 负样本 | `deploy/validate_deploy.py:559` 注释（lite 是合规夹具） | `ci-adapter.yml:772` |
| 4 | `COMPOSE_GLOBS` 能匹配到新文件 | `deploy/validate_config_names.py:223-234`（自动 glob） | `ci-adapter.yml:320` |
| 5 | 新编排的 `image:` 全部登记 `deploy/images.lock` 精确 `name:tag` | `deploy/images.lock:1-19` | `ci-adapter.yml:758`、`dependency-scan.yml:149` |
| 6 | env 模板（`.env.example` / `.env.lite.example`）与 `install.sh` 的生成链对齐 | `deploy/install.sh:66-68,395-432,1006-1065` | `deploy/validate_config_names.py`、`deploy/validate_secrets.py` |
| 7 | 密钥类占位值有生成命令或 `install.sh` 的 `env_set` 路径 | `deploy/validate_secrets.py:1-14` | `dependency-scan.yml:173` |
| 8 | k8s 侧 `app.env.keys` / `configmap.yaml` / `kustomization.yaml` 同步（若 k8s 需覆盖该形态） | `deploy/k8s/base/kustomization.yaml:18-29` | `deploy/validate_deploy.py` R6（`:431-482`，**单向子集**，见 `docs/engineering-standards.md` S4.1-e） |
| 9 | 新增形态纳入 `qa/phase4-verify.sh` 终验项 | `qa/phase4-verify.sh:252-291` | 人工终验（**【规范待固化】**） |
| 10 | TLS/端口/健康检查口径与 `nginx/edge.conf` 一致 | `deploy/nginx/edge.conf:35-69`、`deploy/docker-compose.yml:595-625` | `deploy/validate_deploy.py` R5、`deploy/validate_lite_up.sh` |

### 5.2 新增端点

| # | 必须同步 | 位置 / 证据 | 对应门禁 |
| --- | --- | --- | --- |
| 1 | 路由挂进 `include_all_routers()`，带 `business_dependencies`（除免鉴权探针） | `apps/adapter/app/api/v1/router.py:294-359` | `deploy/validate_openapi.py --strict` |
| 2 | 端点基线 JSON 同步（**冻结文件，改前先评审**） | `docs/api/baseline-*.json`（8 份），`apps/adapter/tests/integration/test_endpoint_baseline.py:40-47,108-114` | `ci-adapter.yml:842` |
| 3 | OpenAPI 快照 / 前端契约快照重生成 | `docs/api/openapi.snapshot.json`、`apps/web/contracts/`、`apps/web/scripts/check-plugin-contract.mjs` | `ci-adapter.yml:846-852`（`--check` / `--generated`）、`ci-web.yml:211` |
| 4 | 新增错误码先在架构文档 §3.0.3 登记，再进注册表 + 前端兜底映射 | `docs/architecture-v1.0.md:249`、`apps/adapter/app/core/errors.py:1-29`、`apps/web/src/lib/business-errors.ts` | `ci-adapter.yml:308`（`check_error_catalog.py --strict` 校验三处码集合一致） |
| 5 | 统一响应 `{code, message, data}`，错误只回业务语言、不透堆栈 | `apps/adapter/app/schemas/common.py:29-64`、`apps/adapter/app/middleware/error_handler.py:1-4,133-137,237-242` | `deploy/validate_openapi.py` G4（`:760`）+ 前端 G-14 禁词 |
| 6 | 写接口 `Idempotency-Key`；Bearer + 租户上下文 | `apps/adapter/app/core/security.py:32-40,435-436,486-515` | `ci-web.yml:191`（登录闭环）；**【规范待固化】** 幂等头全量覆盖无独立门禁 |
| 7 | 资源 ID 用 `类型缩写_ + ULID` | `apps/adapter/app/core/ids.py:61-101` | **【规范待固化】** S2.2 |
| 8 | 埋点指标 / 日志 6 必填字段 | `apps/adapter/app/core/metrics.py:126-221`、`apps/adapter/app/core/logging.py:73-75,144-149` | **【规范待固化】** S2.7 |
| 9 | 单元 + 集成测试；需真实 PG 的用例带 `pg` 标记 | `apps/adapter/tests/`、`apps/adapter/pyproject.toml:188-223` | `ci-adapter.yml:335`（`validate_pg_markers.py`）、`:549` |
| 10 | 前端页面三态 / 能力一致性 / 工作台宿主（若新增页面） | `apps/web/scripts/check-page-states.mjs`、`check-capability-consistency.mjs`、`check-workbench-host.mjs` | `ci-web.yml:171/251/231` |

### 5.3 新增表

| # | 必须同步 | 位置 / 证据 | 对应门禁 |
| --- | --- | --- | --- |
| 1 | 增量迁移 `deploy/migrations/NNNN_*.sql`（编号连续） | `deploy/migrations/`（当前 0002–0016，共 15 个） | `ci-adapter.yml:429`（`validate_migrations.py --run-pg` 双路径实跑） |
| 2 | 配对回滚 `deploy/migrations/rollback/NNNN_*.down.sql` | `deploy/migrations/rollback/`（当前 0006–0016，共 11 个；0002–0005 无 down 且**不补**，见 `deploy/migrations/README.md:22`） | 同上 + `deploy/rollback.sh:64-65,189-200` |
| 3 | initdb 全量脚本同步建表 / RLS / 索引（**双口径**） | `deploy/init-schema-phase2.sql`、`init-schema-phase3.sql`、`init-schema-phase4.sql`；挂载顺序由字典序决定（`deploy/docker-compose.yml:56-76`） | `deploy/validate_deploy.py` R3、`deploy/validate_migrations.py`、`ci-adapter.yml:449`（`validate_schema_retention.py`） |
| 4 | 表数 / schema 基线测试同步 | `apps/adapter/tests/integration/test_schema_baseline.py:25-28,159-169`；`deploy/validate_schema_retention.py` | `ci-adapter.yml:429`、`:449` |
| 5 | 迁移台账 README 同步（当前停在 0014，**已知漂移**） | `deploy/migrations/README.md:6,13,24-33` | **【规范待固化】** 建议 `deploy/validate_migrations.py` 增加「README 编号集合 = 磁盘编号集合」断言 |
| 6 | `backup.sh` 的 `SNAPSHOT_TABLES` 补入新表 | `deploy/backup.sh:68-77`（当前 8 张，缺 `mcp_server` 等） | **【规范待固化】** 建议 `deploy/validate_snapshot_tables.py`（`docs/engineering-standards.md` S7.4） |
| 7 | 仓储双实现（内存 / PG）对称 | `apps/adapter/app/db/repositories/**` | `ci-adapter.yml:375`（`validate_repository_parity.py`） |
| 8 | PG 仓储覆盖 ≥ 88% | `ci-adapter.yml:598-601` | `ci-adapter.yml:598` |
| 9 | 交付清单证据 SHA 刷新 | `docs/phase4-delivery-manifest.md:4-8,14-16` | `qa/phase4-verify.sh:291`（`gen_delivery_manifest.py --check`） |
| 10 | 若新增了 `OA_*` 配置键 → 同时走 5.4 | `deploy/.env.example`、`deploy/k8s/base/app.env.keys` | `deploy/validate_config_names.py`、`deploy/validate_deploy.py` R6 |

### 5.4 附加：其它常见变更的同步点

| 变更 | 必须同步 | 门禁 |
| --- | --- | --- |
| 新增 `OA_*` 环境变量 | `deploy/.env.example`（键集合唯一真源，`deploy/validate_deploy.py:431-482` 的 `allowed` 取自此文件）、`.env.lite.example`（若轻量版消费）、`deploy/k8s/base/app.env.keys` + `configmap.yaml` / `secret.yaml`（按含 `PASSWORD`/`SECRET`/`_KEY`/`TOKEN` 判据分流，`deploy/k8s/base/configmap.yaml:10-20`）、`Settings` 字段（`apps/adapter/app/core/config.py`） | `ci-adapter.yml:320`（`validate_config_names.py` R1）、`:770`（R6）、`dependency-scan.yml:173`（`validate_secrets.py`） |
| 改镜像 tag / digest | `deploy/images.lock`（`name:tag` + `digest` + `license`）、三套 compose 的 `image:`、两份 `Dockerfile` 的 `FROM` digest、`deploy/k8s/base/{adapter,web}/deployment.yaml` 的 `image:`、架构文档 §2.1/§8.1/§8.2/§10.3 的版本字面量 | `ci-adapter.yml:758`、`dependency-scan.yml:149/153`、`sbom.yml:146`；`docs/architecture-v1.0.md:1603-1609` |
| 新增依赖 | 先补架构文档 §8.1 / §8.2 清单，再改 `apps/adapter/pyproject.toml` / `apps/web/package.json`，再刷 `uv.lock` / 根 `package-lock.json`（workspaces 根锁，`apps/web/Dockerfile:19-43`） | `dependency-scan.yml:539`（`validate_deps.py`）、`:219/258/277`（SCA）、`ci-adapter.yml:514`（`validate_packaging.py`） |
| 新增/改错误码 | `docs/architecture-v1.0.md:249`（§3.0.3 先登记）→ `apps/adapter/app/core/errors.py:29` → `apps/web/src/lib/business-errors.ts` | `ci-adapter.yml:308` |
| 改门禁脚本/基线 | 脚本本身过 `ruff check ../../deploy`（`ci-adapter.yml:263`）；`--self-test` 负样本必须仍能拦（各 CI 自检步骤）；确认 `validate_gate_wiring.py` 仍能找到挂载点 | `ci-adapter.yml:479` |
| 新增/改前端可见文案 | 禁词扫描（源码 + 构建产物）、占位词、页面三态 | `ci-web.yml:121/127/294/149` |

---

## 6. 禁止事项（一票否决）

1. **禁止降格项目总目标与执行标准**：首版即商用、技术黑盒化、适配层唯一、合规优先、
   体验一致性、最新稳定版精确标签（`docs/moy-project-charter-v1.0.md`；
   `docs/adr/0001` / `0002` / `0003`）。任何「先放宽、后补」的提案必须先经主理人书面裁决。
2. **禁止上层/插件直连 Dify 原生 API**（`docs/architecture-v1.0.md:148-163` §1.4）；
   唯一例外是 `apps/adapter/qa_probe/bootstrap_dify.py`，边界见 §1.4 第 6 条，
   被 CI / 镜像 / `app` 引用即自动失效。
3. **禁止**为过门禁而删除端点、删基线行、加 `--exit-zero`、放宽 ruff 规则、
   扩大 `exclude`、把 `latest`/滚动标签写进编排（`ci-adapter.yml:256-258,283-286,831-842`；
   `deploy/validate_images.py:1-19`）。
4. **禁止**把只有注释/文档支撑的规则写成「已有门禁保证」；无门禁必须标
   **【规范待固化】** 并给建议脚本名（`docs/engineering-standards.md` 维护纪律）。
5. **禁止**手写 `docs/phase4-delivery-manifest.md`（`docs/phase4-delivery-manifest.md:4-8`）；
   「证据」列是唯一允许手改的列。
6. **禁止**把密钥/口令、`.env`、`.pem`/`.key`、备份 dump、`__pycache__`、
   构建产物提交入库（`.gitignore`）。
7. **禁止**在只读审计/复核场景运行会写入仓库的命令（见 4.1 末「只读纪律」）。
8. **禁止**在未同步 §5.1 第 1/2 项的情况下新增编排形态（A-01 复发的直接条件）。

---

## 7. 裁决台账（同步 `docs/engineering-standards.md` 附录 B）

> 原 10 项「待确认」已由**主理人齐活林于 2026-11-07 正式裁决**；完整证据 A/B 与
> 「裁决与执行归属」列见 `docs/engineering-standards.md` 附录 B。**B-5 / B-8 已于第 4 轮补充最终核查
> 结论，状态改「已裁决」**：B-5 = P1 高、V1.0 交付前闭环、saas 形态前置硬约束；
> B-8 = 不挂 PR CI、改部署侧每日 02:00 调度。其余 8 项裁决不变。本表只登记状态与对本文件的影响。

| 编号 | 事项 | 裁决状态与对本文件的影响 |
| --- | --- | --- |
| B-1 | 分支模型 | **已裁决**：主干开发，`main` + 短生命周期 `feature/*` + PR + tag，保留 `hotfix/*`（仍须 PR），不恢复 `develop` → §2 已改写、去掉「待确认」；`docs/architecture-v1.0.md §9.4` 已订正 |
| B-2 | 覆盖率门槛 | **已裁决**：以 CI 为真源，总 85% / PG 88% / 前端 vitest 四阈值，只升不降 → §4.1 已补三档口径；`docs/architecture-v1.0.md §9.6` 已订正、80% 作废 |
| B-3 | 环境变量示例旧键 | **已裁决**：§9.5 示例订正为 `OA_KEYCLOAK_BASE_URL`，并新增规范「文档示例中的 `OA_*` 键必须是 `Settings` 实际字段」→ §5.4 沿用该口径；文档示例键门禁属【规范待固化】 |
| B-4 | 前端黑盒化两条门禁口径 | **已裁决**：开源声明页属合规必需豁免（边界：仅组件名称/版本/许可），两条门禁须共用同一张路径清单 → 见 `docs/adr/0004-open-source-attribution-exemption.md`；§5.4 / §6.2 沿用；共用清单属【规范待固化】 |
| B-5 | 租户上下文优先级 | **已裁决（第 4 轮）：P1 高，V1.0 交付前闭环；saas 形态前置硬约束**。工程师核查确认 `security.py:489` 注释**没有写反**，与 `:503-517` 实现一致，ADR-0007 §4（`:47-49`）是**目标态建议**、结论字段 `:10` 为「P2 非当前可复现越权」→ **不是「实现违反已生效 ADR」**，注释不改；但 3 条条件性越权路径真实存在（`OA_TENANT_MEMBERSHIP_CHECK=off` / stub 跳过校验且不验签 / 配置漏注入使启动期判据失效），硬化交工程师。**不阻断本轮提交**（三套 compose + k8s 显式注入三键、private 忽略请求头）。**硬约束**：saas 形态对外交付前 ADR §4 必须升格为已落地安全基线（ADR `:21` 前置条件）→ 已写入 `engineering-standards.md` S3.1-g / S6.10 / A.4（建议 `deploy/validate_tenant_context.py`）；§5.2 第 6 项的 Bearer + 租户上下文口径不变 |
| B-6 | k8s 与 compose 配置等价性 | **已裁决**：V1.0 不要求等价（k8s 为 V1.1 准备物）；差异集中登记到决策/矩阵层 + `deploy/k8s/README.md` 头部标注（执行归属：工程师/PM，第 3 轮冻结未改）；V1.1 启用前补门禁 → §5.1 第 8 项按「登记差异」执行 |
| B-7 | `.trae/` 归属 | **已裁决**：加 `.gitignore` 规则不入库，设计要点脱敏归档 `docs/decisions/`；配置改动由工程师、归档由 PM → §6.6 禁令不变，状态已裁决 |
| B-8 | `reconcile_usage.py` 是否挂门禁 | **已裁决（第 4 轮）：不挂 PR CI，改部署侧每日 02:00 调度**。脚本会写 `audit_log(billing.reconcile_mismatch)`、需真实 PG、CI 空账期三项计量全 0 会「恒等假绿」且污染审计表；正确性由 `test_usage_endpoints.py` / `test_qa_b07_b08_verification.py` 覆盖，文件头准确口径已由工程师补写 → **§4.2 不列入必跑命令**（且不得被 `validate_gate_wiring.py` C-1 判为门禁缺失）；`engineering-standards.md` S6.7 / S7.10 / A.3 / A.4 已同步。最小样本量门槛与告警机械校验属【规范待固化】，建议 `deploy/validate_reconcile_schedule.py`（「当前 3 条」口径待确认） |
| B-9 | 端点基线数字口径 | **已裁决**：以 `validate_openapi.py` + 8 套 baseline（合计 **103**）为真源；`docs/architecture-v1.0.md §9.6` 已订正；「文档端点数 = 基线合计」列【规范待固化】，建议 `deploy/validate_endpoint_doc_parity.py` → §5.2 第 2 项沿用 8 套基线清单 |
| B-10 | A-01 修复提交状态 | **已裁决**：主理人在 PM 文档整改回传后统一提交；**提交后由架构师复核**两份规范文档与附录 A.1 的行号是否漂移并给出复核结论 → §5.1 / 附录 A.1 引用暂按工作区状态标注「待提交」 |

---

*本文件与 `docs/engineering-standards.md` 同为规范资产；修改任一文件须同步检查另一文件的
引用与编号。规范条款的「机械保证」状态以 `docs/engineering-standards.md` 附录 A 为准。*
