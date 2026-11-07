# 工程化规范单一真源（Engineering Standards · Single Source of Truth）

> **定位**：本文件是「后续所有开发变更的强制约束」。它**不新增架构约束**，只把已经生效的
> 项目总目标、ADR、架构文档章节、CI 门禁与运维脚本里的既有规则收敛成一张可执行清单，
> 并为每条规则标明「由哪个现有 CI 门禁机械保证」。凡只有文档、没有门禁的，一律标
> **【规范待固化】** 并给出建议脚本名（建议脚本**尚未实现**，不得被引用为已生效门禁）。
>
> **基线**：`main @ fea1acd`（2026-11-06 21:27:27 +0800），958 个跟踪文件。
> 引用均为**文件相对路径 + 行号**，行号以本文件写入时的仓库状态为准；后续提交如使行号
> 漂移，请以被引用的**符号名 / step 名**为锚，不删条款。
>
> **基线状态提示**：写入本文件时工作区存在 4 个未提交改动
> （`apps/adapter/app/db/engine.py`、`apps/adapter/tests/unit/test_db_session.py`、
> `deploy/docker-compose.lite.yml`、`deploy/validate_deploy.py`，见 `git status --short`），
> 系其他成员修复 2026-11-06 审计 A-01 的产物。本文件涉及 `deploy/validate_deploy.py` 的
> `COMPOSE_FILES` 时按**工作区当前状态**描述，并显式标注「待提交」；**本文件不改动这 4 个文件**。
>
> **权威顺序（冲突时从上到下）**：
> 1. `docs/moy-project-charter-v1.0.md`（项目总目标，不得降格）
> 2. 已生效 ADR：`docs/adr/0001-adapter-layer-uniqueness.md`（适配层唯一）、
>    `docs/adr/0002-infrastructure-license-compliance.md`（合规）、
>    `docs/adr/0003-design-token-baseline.md`（体验一致性）、
>    `docs/adr/0004-open-source-attribution-exemption.md`（**2026-11-07 新增**：开源声明页
>    技术黑盒化豁免及其边界，承载附录 B-4 裁决）；
>    决策记录 `docs/decisions/0004-phase1-open-decisions-ratification.md`、
>    `docs/decisions/0007-tenant-context-source.md`、`docs/decisions/0009-base-v1.0-ratification.md`
> 3. 本文件（可执行条款 + 机械门禁映射）
> 4. `docs/architecture-v1.0.md` 及各阶段架构文档（§1.4 / §3.0.3 / §6.1 / §8 / §9）
> 5. CI 与 `deploy/validate_*.py` / `apps/web/scripts/*.mjs` / `apps/adapter/scripts/*.py`
>    （**判据的可执行真源**，文档与它冲突时以门禁为准，并把文档订正列为待办）
> 6. 代码注释、README、交付文档（说明性，不构成判据）
>
> **修订记录（第 3 轮，2026-11-07）**：主理人齐活林对附录 B 的 10 项正式裁决，
> 附录 B 已由「待确认清单」改为**裁决台账**（保留证据 A/B，新增「裁决与执行归属」列）；
> 同步订正 `docs/architecture-v1.0.md` §9.4 / §9.5 / §9.6、`CONTRIBUTING.md §2/§7`，
> 并新增 `docs/adr/0004-open-source-attribution-exemption.md`。
> 本轮一并更正 2026-11-06 审计的 ARCH-11 / ARCH-15 结论（见 S3.1-b/c/e）。
> 本文件与架构文档已发现的口径差异统一登记在 **附录 B（裁决台账）**，不再标「待确认」。

---

## 0. 读法与门禁编号

- 每条规范用 `S<n>.<m>` 编号，末尾必有 **「机械保证」** 或 **「【规范待固化】」** 字段。
- 门禁写法：`脚本路径` + `CI 挂载点（workflow:行号）`。「自检」指该脚本的 `--self-test`
  负样本步骤；「可红证明」是本仓库对门禁有效性的叫法，见
  `deploy/validate_gate_wiring.py:1-14`（C-1/C-4：判据存在 ≠ 判据在跑）。
- **17 个 `deploy/validate_*`** = 16 个 `.py` + 1 个 `.sh`（`deploy/validate_lite_up.sh`）；
  `qa/phase4-verify.sh:252` 中的「16 个」只数 `.py`，两种口径都成立，引用时须写明。
- 另有 3 类门禁不在 `deploy/` 下，映射表（附录 A）一并收录：
  `apps/web/scripts/*.mjs`（7 个）、`apps/adapter/scripts/check_error_catalog.py`、
  `.github/workflows/*.yml` 中的内联 `grep` / `promtool` / `pip-licenses` 步骤。

---

## S1 目录结构职责与边界

### S1.1 顶层目录职责表（规模取自 `git ls-files` 实测）

| 目录 | 职责 | 边界（不得越界） | 关键入口 | 规模 |
| --- | --- | --- | --- | --- |
| `apps/adapter/` | 自研 FastAPI 统一适配层 | **唯一允许接触 Dify 底座的应用**（ADR-0001） | `app/main.py:528`、`app/api/v1/router.py:294`、`app/clients/dify_client.py` | 460 文件；`app/` 237、`tests/` 214、`qa_probe/` 3、`scripts/` 2 |
| `apps/web/` | Next.js 16 App Router 前端交互层 | 只经同源 `/api/v1/*`、`/sso/*` 访问适配层；不得出现底座字样/SDK | `src/app/`、`next.config.js`、`Dockerfile:58-65` | 221 文件；`src/` 195 |
| `deploy/` | 三套 compose、4 份 nginx、14 件 k8s、15 迁移 + 11 回滚、17 个门禁、镜像锁、env 模板、5 个运维脚本 | 只放部署与交付资产，**不放业务代码** | `docker-compose.yml`、`nginx/edge.conf`、`install.sh` | 95 文件 |
| `docs/` | 章程/PRD/架构/ADR/决策/审计/API 契约/交付清单 | `docs/api/openapi.json` 由 CI 生成且**不入库**（`.gitignore`），基线 `baseline-*.json` 必须入库 | `docs/phase4-delivery-manifest.md`、`docs/api/README.md` | 133 文件（含 `docs/archive/` 25 份历史审计） |
| `qa/` | 金标任务集 T01–T20 + 复跑工具 + 四阶段终验 | 结果目录 `results/` 为运行产物 | `qa/goldset/run.py`、`qa/phase4-verify.sh` | 23 文件 |
| `scripts/` | 压测 8 件 + 交付清单生成器 | 构建期工具，不随产物分发 | `scripts/gen_delivery_manifest.py` | 10 文件 |
| `sql/` | DBA 手工脚本（清空/导出/保留期/租户导出/向量扩展） | 不进应用启动路径 | `sql/full-purge.sql:1-10` | 5 文件 |
| `.github/workflows/` | 4 条 CI | 门禁**必须真的被 step 调用** | 见附录 A | 4 文件 / 2007 行 |
| 根 | npm workspaces 根 + 锁 + 规范文件 | `package-lock.json` 是 workspaces 根锁，前端 Dockerfile 依赖它 | `package.json:9-16`、`.editorconfig`、`.gitattributes`、`CONTRIBUTING.md` | 7 tracked |

### S1.2 条款

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S1.1-a | 根目录只放 workspaces 根配置与规范文件，业务代码只进 `apps/*` | **【规范待固化】** 无门禁。建议 `deploy/validate_repo_layout.py`（断言顶层目录白名单 + 各层关键入口存在） |
| S1.1-b | `apps/adapter/app` 的二级包即分层，包名不得随意新增 | `deploy/validate_import_directions.py`（层矩阵定义在脚本内 `:1-24` 与允许矩阵），CI `ci-adapter.yml:352`；新增包需同步矩阵，否则矩阵外 import 判红 |
| S1.1-c | 打包必须覆盖 `app/` 全部子包（历史上漏 `app.business`、`app.db`） | `deploy/validate_packaging.py:1-14`，CI `ci-adapter.yml:514` + `--self-test :519` |
| S1.1-d | `deploy/` 与 `scripts/` 的脚本自身必须过 ruff lint/format | `ci-adapter.yml:263`/`:266`（deploy）、`:289`/`:292`（scripts），共用 `apps/adapter/pyproject.toml` 配置（`:108-158`），不另建第二份 pyproject |
| S1.1-e | 前端构建期 `NEXT_PUBLIC_*` 必须在 `apps/web/Dockerfile` 有同名 `ARG` | `deploy/validate_config_names.py` R2（docstring `:1-40`），CI `ci-adapter.yml:320` + `--self-test :324` |
| S1.1-f | 三套编排都必须声明 `additional_contexts.lockroot` | `deploy/validate_deploy.py` R4（`NAMED_CONTEXT_COMPOSE_FILES`），CI `ci-adapter.yml:770` + `--self-test :772` |
| S1.1-g | 构建产物 / 缓存 / 现场密钥不得入库 | `.gitignore`（`__pycache__/`、`*.py[cod]`、`.env`/`.env.*` + `!.env.example`/`!.env.lite.example`、`*.pem`/`*.key`、`deploy/backups/`、`deploy/migrations/rollback/*/`）；**【规范待固化】** 无机械断言，建议 `deploy/validate_repo_clean.py`（`git ls-files` 黑名单断言）。注：`.trae/` 目前**不在** `.gitignore` 中且未跟踪（`git check-ignore` 退出码 1），见附录 B-7 |
| S1.1-h | `docs/api/openapi.json` 不入库，基线与冻结快照入库 | `.gitignore` + `deploy/validate_openapi.py` G1–G5（`:1-30`），CI `ci-adapter.yml:842`（`--strict`）。**【规范待固化】** 「基线文件必须 tracked」无独立断言，建议并入 `validate_openapi.py` |

---

## S2 命名与路径约定

依据 `docs/architecture-v1.0.md:1529-1539`（§9.1）、`:1540-1546`（§9.2）、`:1547-1553`（§9.3）、
`:1573-1584`（§9.5），`.editorconfig:1-30`、`.gitattributes:1-40`。

| 编号 | 对象 | 规范 | 机械保证 / 状态 |
| --- | --- | --- | --- |
| S2.1 | API 路径 | `/api/v1/{domain}/{resource}`，小写连字符 | `deploy/validate_openapi.py`（`API_PREFIX` 于 `:82`） + CI `ci-adapter.yml:842`；路径集合由 8 份 `docs/api/baseline-*.json` 冻结 |
| S2.2 | 资源 ID | `类型缩写_` + ULID（26 位） | `apps/adapter/app/core/ids.py:1-3,61-67,98-101`；**【规范待固化】** 无门禁校验「所有新增资源 ID 走 `new_prefixed_id`」，建议 `deploy/validate_resource_ids.py` |
| S2.3 | JSON 字段 / 枚举 | snake_case / 小写下划线 | **【规范待固化】** 仅靠 `deploy/validate_openapi.py --strict` 与前端契约快照间接约束（`ci-adapter.yml:842`、`ci-web.yml:212`）；建议 `deploy/validate_naming.py` |
| S2.4 | 前端变量/组件 | camelCase / PascalCase | `ci-web.yml:73`（eslint `--max-warnings=0`）、`:79`（tsc）；**【规范待固化】** 命名风格本身无独立规则，建议在 `apps/web/eslint.config.mjs` 显式声明 |
| S2.5 | 环境变量 | 大写 + `OA_` 前缀 | `deploy/validate_config_names.py:78`（`ENV_PREFIX`）+ R1，CI `ci-adapter.yml:320`；`deploy/validate_secrets.py` 管占位值生成说明，CI `dependency-scan.yml:173` |
| S2.6 | 错误码 | 0 成功；10001–14999 领域；15001–15999 计费；16001–16099 插件；17001–17099 任务；18001–18099 会话/记忆；20000–20999 通用；30000–30999 系统 | `apps/adapter/app/core/errors.py:1-29`（注册表，唯一登记入口）；`apps/adapter/scripts/check_error_catalog.py --strict`，CI `ci-adapter.yml:308`（三处码集合一致 + 内部码禁区） |
| S2.7 | 日志 | structlog JSON 必带 `timestamp/level/trace_id/tenant_id/user_id/event`；禁记令牌、凭据、文档正文 | `apps/adapter/app/core/logging.py:4-6,73-75,144-149,153-184`；**【规范待固化】** 无门禁校验「每条日志含 6 字段」，建议 `deploy/validate_log_fields.py` |
| S2.8 | 行尾/编码 | 全文本 LF + UTF-8；`*.bat`/`*.cmd` CRLF；Python 4 空格 / 其余 2 空格 | `.editorconfig:1-30`、`.gitattributes:1-40`；`ruff format --check`（`ci-adapter.yml:243`、`:266`、`:292`）、`ci-web.yml:323`（prettier `--check`）；**【规范待固化】** `.gitattributes` 与实际文件行尾一致性无独立断言，建议 `deploy/validate_line_endings.py` |
| S2.9 | 提交信息 | `type(scope): 说明`（feat/fix/refactor/test/docs/chore） | `docs/architecture-v1.0.md:1560`（§9.4）；`CONTRIBUTING.md §1`；**【规范待固化】** 无 commit-msg hook / CI 断言，建议 `.github/workflows/commit-lint.yml` 或 `deploy/validate_commit_msgs.py` |

---

## S3 模块/分层依赖方向

### S3.1 架构红线（一票否决）

依据 `docs/adr/0001-adapter-layer-uniqueness.md:1-22`、
`docs/architecture-v1.0.md:148-163`（§1.4 第 1–6 条）、`:1598`（§9.6 范围说明）。

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S3.1-a | 上层（前端 / 插件 / 业务）只调自研适配层，**严禁直连 Dify 原生 API** | `dependency-scan.yml:49`（扫 `apps/web` 与未来 `plugins` 的 `dify｜milvus｜litellm｜\b5001\b｜chat-messages｜workflow_run｜retriever_resources｜datasets/｜/v1/`）、`:95`（前端依赖不含底座 SDK）、`:108`（`apps/adapter/app` 中 `dify-api｜dify_base_url` 只允许出现在 `core/config.py` 与 `clients/dify_client.py`） |
| S3.1-b | 例外唯一且逐条登记：`apps/adapter/qa_probe/bootstrap_dify.py` 可用 urllib 直连 `/console/api/...`（部署期一次性、不进镜像、不被 `app` import、不被 pytest 收集；被 CI/镜像引用即自动失效） | **源码级门禁确实存在**（更正 2026-11-06 ARCH-15）：`.github/workflows/dependency-scan.yml:40-131` 的 `base-layer-isolation` job 有三步——`:49` 扫 `apps/web`/未来 `plugins` 的底座字样、`:95` 查前端依赖无底座 SDK、`:108` 查 `apps/adapter/app` 中 `dify-api｜dify_base_url` 只许出现在 `core/config.py` 与 `clients/dify_client.py`。豁免本身由 `docs/architecture-v1.0.md:157`（§1.4 第 6 条）与 `:1598`（§9.6 范围说明）逐条登记，`pyproject.toml` 的 ruff `extend-exclude` / pytest `testpaths` 不含该目录。**【规范待固化】** 「豁免失效条件（被 CI/镜像/app 引用）」未被现有门禁判据覆盖，建议 `deploy/validate_dify_boundary.py` |
| S3.1-c | 运行期底座访问唯一出口 `app/clients/dify_client.py`；原生路径常量只允许出现在 3 个 service + client | `dependency-scan.yml:108` 的判据**收窄**为 `dify-api｜dify_base_url` 两个地址标记，因此 `/v1/chat-messages`、`/v1/workflows`、`/v1/files/upload`、`/console/api/` 等**原生路径常量未被现有门禁判据覆盖**（现由 `apps/adapter/tests/unit/test_model_service.py:292` 单测 `native_markers` 断言兜底）——这是判据范围问题，**不是「没有门禁」**。**【规范待固化】** 建议 `deploy/validate_dify_boundary.py` 统一收口 S3.1-b/c，把原生路径常量与豁免失效条件纳入机械断言 |
| S3.1-d | 对外文档不得泄露底座术语 | `deploy/validate_openapi.py` G5（`:109-114,782-797`，扫 `docs/api/openapi.json`）+ `ci-adapter.yml:868-889` 内联兜底扫描（4 个词） |
| S3.1-e | 前端用户可见文案禁模型名 / 向量库 / RAG / Agent 等术语；错误只回业务语言 | `apps/web/scripts/check-banned-terms.mjs`（词表 `:49-110` 由 `docs/prd-phase2-increment.md` §7 生成），CI `ci-web.yml:121`（G-14 源码）、`:127`（词表一致性）、`:133`（自检）、`:294`（G-16 构建产物）；后端 `apps/adapter/app/middleware/error_handler.py:1-4,133-137,237-242`。**【2026-11-07 裁决 · 附录 B-4 / ADR-0004】开源声明页属合规必需豁免**（Dify 修改版 Apache-2.0 要求署名/版权不可移除，`deploy/images.lock:42-56`），但边界收窄为：**仅允许第三方组件名称、版本与许可信息**；**禁止**把模型名 / 向量库 / RAG / Agent 作为产品能力描述，禁止在署名表之外出现任何底座术语，禁止把 `role` 写成技术类别（现 `apps/web/src/app/(main)/about/page.tsx:35-39` 待 PM/工程复核收窄）。**更正 2026-11-06 ARCH-11**：豁免**已在 CI 登记**（`dependency-scan.yml:80-84` 只对含 `license:` 的署名表行放行），并非「边界未定义/无豁免」；真正问题是 `check-banned-terms.mjs` 词表不含 `dify/pgvector/valkey` 组件名、靠「不命中」隐式放行，与直连门禁的内联正则白名单**两套口径**。**要求**：两处白名单必须共用同一张路径清单（建议 `apps/web/scripts/attribution-allowlist.json`）；**【规范待固化】** 该单一真源与改造尚未实现，建议 `apps/web/scripts/check-attribution-allowlist.mjs` 或并入 `check-banned-terms.mjs`，见附录 A.4 / B-4 |
| S3.1-f | 全产品 UI 走统一组件库；插件不得自带 CSS/JSX 或独立设计规范 | `docs/adr/0003-design-token-baseline.md:1-22`；`apps/web/tailwind.config.ts:1-14`（设计令牌唯一来源）；CI `ci-web.yml:231`（G-27 `check-workbench-host.mjs`）、`:171`（G-18 页面三态）、`:252`（G-30 能力一致性）、`:212`（G-31 插件契约）。**【规范待固化】** 「所有组件必须 import 自 `src/components/ui/`」无独立门禁，建议 `apps/web/scripts/check-ui-library.mjs` |
| S3.1-g | **saas 形态对外交付前置硬约束**：ADR-0007 §4「租户唯一可信来源 = 令牌声明 `tenant_id`」必须**升格为已落地的安全基线**，不得停留在「目标态建议」 | **【2026-11-07 附录 B-5 最终裁决】** 依据 `docs/decisions/0007-tenant-context-source.md:10`（结论字段「P2 配置依赖型缺陷、非当前可复现越权」）、`:21`（「若 V1.1 要交付 saas 多租户，本条在开该形态前必须升 P0 并先修」）、`:47-49`（§4 建议）、`:65`（§5 把后端语义收紧列为「Batch 2，需单独 ADR」）。当前 `apps/adapter/app/core/security.py:489` 注释「`X-Tenant-Id` 请求头 > 令牌声明 > 配置默认值」经工程师核查**没有写反**，与 `:503-517` 的 `resolve_tenant_id()` 实现一致，且 saas 下由 `:937-938` 的 AR-017 成员校验兜底，**不是「实现违反已生效 ADR」**，注释不改。但在 §4 升格前 saas 形态不得对外交付。**【规范待固化】** 建议 `deploy/validate_tenant_context.py`（断言 saas 预设与成员校验开关），见附录 A.4 |

### S3.2 分层依赖方向（G-37）

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S3.2-a | `apps/adapter/app` 二级包按允许矩阵单向 import；同层自引用恒允许；三分类（模块级 / 函数体延迟 / `TYPE_CHECKING`） | `deploy/validate_import_directions.py:1-24`（矩阵定义），CI `ci-adapter.yml:352`（`--baseline deploy/import_direction_baseline.txt`）+ `--self-test :359` |
| S3.2-b | 文件级 import 图不得新增环（Tarjan SCC ≥2 成员即一个环） | 同脚本，环基线 `deploy/same_layer_cycle_baseline.txt`（默认路径），CI 同上 |
| S3.2-c | 基线只许减不许增；违规消失必须删行（stale 判红）；每条豁免必须有 `due_batch` | 脚本 `:1-24` 与 `deploy/import_direction_baseline.txt:1-30`；**【规范待固化】** 到期判定依赖 `OA_BATCH`（`ci-adapter.yml:93` 当前 `B21`），而 `deploy/validate_batch_clock.py:128-168` 的 C2 **只校验** `apps/adapter/tests/expected_xfail.py::DEFECT_LEDGER`，不覆盖两份方向基线的 `due_batch`（当前 B23/B25/B27）。建议扩展 `validate_batch_clock.py` 的 `DOC_SOURCES` 或直接读入两份基线 |
| S3.2-d | 内存/PG 双实现仓储对称（方法名、record 字段、测试钩子分类） | `deploy/validate_repository_parity.py`，CI `ci-adapter.yml:375`（`--baseline deploy/repository_parity_baseline.txt`）+ `--self-test :383`；基线当前 0 行（`deploy/repository_parity_baseline.txt:20-24`） |
| S3.2-e | 插件声明 schema / 能力枚举 / 产物依赖闭合 | `deploy/validate_plugins.py`（G-26/G-28），CI `ci-adapter.yml:404` + `--self-test :409` |

---

## S4 配置：OA_ 单一真源与 env 模板对齐

### S4.1 单一真源链

`deploy/.env.example`（标准栈，118 个 `OA_` 键）与 `deploy/.env.lite.example`（轻量版，87 个键）
是**键集合唯一真源**；`deploy/.env` / `deploy/.env.lite` 是**值唯一真源**（本地生成、不入库，
`.gitignore` 的 `.env` / `.env.*` + `!.env.example` / `!.env.lite.example` 保证）。
三套编排用 `env_file` 吃全量，再用 `environment:` 覆盖显式项；`deploy/k8s/base/` 用
`--from-env-file=deploy/.env` 生成 Secret + ConfigMap 覆盖（见 `deploy/k8s/README.md` §5.1/§5.2）。

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S4.1-a | 编排注入的每个 `OA_*` 必须是 `Settings` 字段（`OA_` + 字段名大写）或在白名单带理由登记 | `deploy/validate_config_names.py` R1（docstring `:1-40`、`:78`），CI `ci-adapter.yml:320` + `--self-test :324`。**该门禁明确不校验 `.env` 模板与编排的对齐关系**（docstring `:1-40` 末段） |
| S4.1-b | 所有密钥/口令类 `CHANGE_ME_*` 必须给出生成方式，且由 `install.sh` 自动生成 | `deploy/validate_secrets.py`，CI `dependency-scan.yml:173` + `--self-test :178`；编排侧缺失即 `${VAR:?}` 拒绝启动（`docker-compose.yml:47,99,141,253` 等） |
| S4.1-c | 编排不得使用明文兜底密钥（`${VAR:-<常量>}`） | `deploy/validate_secrets.py`（G-33，`:1-14`），CI `dependency-scan.yml:172-178` |
| S4.1-d | 三套编排的 adapter DSN 不得用库属主/超级用户；必须显式声明 `OA_ENV`、`OA_AUTH_MODE`；initdb 挂载文件名集合一致 | `deploy/validate_deploy.py` R1/R2/R3（`COMPOSE_FILES` 为扫描集合），CI `ci-adapter.yml:770` + `--self-test :772`。**A-01 教训**：该集合在 `fea1acd` 上曾漏 `docker-compose.lite.yml`，门禁对主交付形态恒绿；工作区已补入 lite 并新增 `LITERAL_SUPERUSER_DSN` 判据（`git diff deploy/validate_deploy.py`，**待提交**）。新增编排形态必须同步 `COMPOSE_FILES` 与 `NAMED_CONTEXT_COMPOSE_FILES` |
| S4.1-e | k8s 清单的 env 键 ⊆ `.env.example` | `deploy/validate_deploy.py` R6（`:431-482`），CI `ci-adapter.yml:770`。**注意是单向子集断言**：只查「k8s 多出的键」，不查「模板多出的键」。**【规范待固化】** 建议在该函数增加 `allowed - declared` 反向断言 |
| S4.1-f | 4 个 compose 内联键（`OA_DIFY_BASE_URL` / `OA_KEYCLOAK_BASE_URL` / `OA_REDIS_URL` / `OA_OBJECT_STORE_ENDPOINT`）在 k8s 必须有等价注入 | 现状：`docker-compose.yml:660,677,678,687`、`docker-compose.saas.yml:473,488,489,495`、`docker-compose.lite.yml:480,498,499`（lite 无 `OA_OBJECT_STORE_ENDPOINT`）；两份 env 模板与 `deploy/k8s/base/app.env.keys:17-22`、`configmap.yaml` 均无。**【规范待固化】** 建议 `deploy/validate_k8s_parity.py`，或把 4 键补进 `.env.example` 后由 R6 覆盖。当前 k8s 会退化到 `apps/adapter/app/core/config.py:138,172,182,190` 的代码默认值，其中 `redis_url="redis://redis:6379/0"`（`:182`）与栈内 `valkey` 主机名/口令不符。**【2026-11-07 裁决 · 附录 B-6】V1.0 不要求 k8s 与 compose 功能等价**——`deploy/k8s/README.md` 自定位「V1.1 准备物，不作 V1.0 验收依据」，故本条在 V1.0 标为**已登记差异**而非阻断；已知差异须集中登记到决策/矩阵层（建议 `docs/decisions/` 或 `docs/gap-matrix-2026-11-03.md`）并在 `deploy/k8s/README.md` 头部显著标注（**执行归属：工程师，受第 3 轮冻结约束本轮未改**）；**V1.1 启用前必须补齐等价性门禁**（`deploy/validate_k8s_parity.py` 或扩展 R6 反向断言）。见附录 B-6 |
| S4.1-g | 端口、TLS、健康检查口径在三套编排一致 | `deploy/validate_deploy.py` R5（对外端口/边缘），CI `ci-adapter.yml:770`；`deploy/validate_lite_up.sh`（`deploy/validate_lite_up.sh:1-14`，真起栈断言 9 容器 / 内存），由 `deploy/install.sh` 调用（`install.sh:50-59` 复用其函数） |
| S4.1-h | 站点相关键（`OA_PUBLIC_BASE_URL` / issuer / Cookie Secure）同源 | `deploy/install.sh:395-432` 由 `OA_LITE_PUBLIC_URL` 单点渲染 `OA_JWT_ISSUER` / `OA_WEB_OIDC_ISSUER` / realm；`apps/adapter/app/core/security.py` 的 `validate_auth_startup` 启动期拦截（`main.py:318`）。**【规范待固化】** k8s ConfigMap 覆盖 Secret 同名键的冲突无门禁，建议 `deploy/validate_k8s_parity.py`。**【2026-11-07 裁决 · 附录 B-6】** 同 S4.1-f：V1.0 只登记差异、不要求等价；V1.1 启用前必须补门禁并把站点相关键冲突收口 |
| S4.1-i | 批次号 `OA_BATCH` 与文档/脚本四处同步 | `deploy/validate_batch_clock.py:68-71`（`DOC_SOURCES` = `README.md`、`apps/adapter/README.md`、`.github/workflows/ci-adapter.yml`、`qa/phase4-verify.sh`），CI `ci-adapter.yml:218`（`--check-docs`） + `:224`（`--self-test`） |
| S4.1-j | **文档示例中的 `OA_*` 键必须是 `Settings` 实际字段**（环境变量 = `OA_` + 字段名大写），不得出现无 alias 承接的历史名 | **【2026-11-07 裁决 · 附录 B-3】** `docs/architecture-v1.0.md:1572-1588`（§9.5）原示例 `OA_KEYCLOAK_URL` 与 `Settings.keycloak_base_url` 不匹配且无 alias，会被 pydantic 静默忽略（`deploy/validate_config_names.py:1-14`），本轮已订正为 `OA_KEYCLOAK_BASE_URL`。机械保证：`deploy/validate_config_names.py` R1（`:78` 的 `ENV_PREFIX` + `Settings.model_fields`），CI `ci-adapter.yml:320` + `--self-test :324`；**【规范待固化】** 该门禁只扫编排注入的键、**不扫文档示例**，建议扩展 `validate_config_names.py` 增加「文档示例键 ⊆ `Settings.model_fields`」断言 |

---

## S5 依赖声明与锁 / 镜像精确标签

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S5.1 | 白名单 MIT / Apache-2.0 / BSD；灰名单 LGPL/MPL/EPL 仅独立进程；黑名单 GPL/AGPL/SSPL 禁入核心库 | `docs/adr/0002-infrastructure-license-compliance.md:14-22`（总方案 5.3 协议表）；`deploy/validate_offline_pkgs.py:57-62`（`COPYLEFT_MARKERS` / `WEAK_COPYLEFT_MARKERS`）；`dependency-scan.yml:277`（协议黑名单判定）、`:219`（Python 协议）、`:258`（Node 协议）、`:250`（`npm audit --audit-level=high`）；`sbom.yml:118`（license-scan） |
| S5.2 | Dify 修改版 Apache-2.0 的附加约束（多租户书面授权 / 不修改代码 / 不展示 LOGO） | `deploy/images.lock:42-56`（三条规避约束）、`docs/decisions/0004-phase1-open-decisions-ratification.md`；**【规范待固化】** 「不展示品牌标识」仅有文字约束（前端 about 页署名表行由 `dependency-scan.yml:80-84` 白名单放行），建议 `apps/web/scripts/check-brand-assets.mjs` |
| S5.3 | 所有开源组件用最新稳定版**精确标签**，禁止 `latest`/滚动别名/合成标签；compose 每个 `image:` 必须在 `images.lock` 有 `name:tag` 精确匹配 | `deploy/validate_images.py:1-19`（五道校验），CI `ci-adapter.yml:758`、`dependency-scan.yml:149`（含 `--check-remote :153`）、`sbom.yml:146`；判据细节见 `docs/architecture-v1.0.md:1603-1609`（§9.6 镜像清单门禁） |
| S5.4 | 文档 §2.1/§8.1/§8.2/§10.3 的组件版本必须与 `images.lock` tag 字面一致 | `deploy/validate_images.py` 的 `check_doc_versions()`（R2-ARC-04），由 `ci-adapter.yml:758` 触发；`images.lock:135` 的 `doc_version: "18.6"` 为文档口径锚点 |
| S5.5 | `pyproject.toml` 运行时依赖的**包名 + 版本约束**必须同时登记在架构文档 §8.1；反向校验白名单 stale | `deploy/validate_deps.py:1-14,55-56,262-275,309`，CI `dependency-scan.yml:539` + `--self-test :546`、CI `ci-adapter.yml:764`；注意 `validate_deps.py` 是四合一门禁（清单一致性 + 最低安全版本 + 加固约束 + 漏洞扫描），见 `docs/architecture-v1.0.md:1610-1625` |
| S5.6 | 生产镜像必须消费 `uv.lock`（`uv export --frozen`），前端必须消费根 `package-lock.json`（`npm ci`） | `apps/adapter/Dockerfile:30-55`、`apps/web/Dockerfile:19-43`；`deploy/validate_packaging.py`（CI `ci-adapter.yml:514`）；`dependency-scan.yml:258-276`（`npm ci` 而非 `npm install` 以免许可扫描对象漂移）。**【规范待固化】** 「Dockerfile 必须 COPY 锁文件」无独立断言，建议并入 `validate_packaging.py` |
| S5.7 | 离线 `.difypkg` 包内依赖协议 / 已知漏洞扫描 | `deploy/validate_offline_pkgs.py`，CI `sbom.yml:136`（`--self-test`）、`:141`、`:165` |
| S5.8 | 依赖漏洞须逐条做「调用面适用性评估」，命中攻击面零容忍；接受风险须在 `ACCEPTED_RISKS` 登记 | `docs/architecture-v1.0.md:1615-1624`（§9.6）、`deploy/validate_deps.py`；**【规范待固化】** 「评估报告随交付留存」无门禁，建议 `deploy/validate_risk_evidence.py` |

---

## S6 测试分层与覆盖率门槛

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S6.1 | 分层：`tests/unit`、`tests/integration`、`tests/security`、前端 `vitest` / `playwright`；需真实 PG 的用例必须带 `pg` 标记 | `deploy/validate_pg_markers.py:1-14`，CI `ci-adapter.yml:335` + `--self-test :339`；`apps/adapter/pyproject.toml:188-223`（pytest `addopts`/`testpaths`） |
| S6.2 | 总覆盖率 ≥ **85%**（G-05 硬地板），必须带 `--run-pg` | `ci-adapter.yml:549-557`（`--cov=app --cov-fail-under=85`）、`apps/adapter/pyproject.toml:250-256`（`fail_under = 85`）；`ci-adapter.yml:533` 禁止用参数临时放宽 |
| S6.3 | 带 PG 口径 `skipped = 0`（防夹具静默退化） | `ci-adapter.yml:560-581`（解析 `junit.xml` 断言）；`ci-adapter.yml:4` 声明三项强制 |
| S6.4 | PG 仓储实现 `db/repositories/*/postgres.py` 覆盖 ≥ **88%** | `ci-adapter.yml:598-601`（`coverage report --fail-under=88`），取值依据见 `:586-594`（实测 90.66% − 2.66pp 余量） |
| S6.5 | 前端 vitest **四阈值** + eslint 零 error + tsc 零 error + `next build` | `ci-web.yml:73`（eslint）、`:79`（tsc）、`:92`（vitest `--coverage`）、`:98`（build）；根 `package.json:17-20` 提供 `lint:web`/`typecheck:web`/`build:web`。**四阈值（2026-11-07 附录 B-2 裁决，以 `apps/web/vitest.config.mts:305-310` 为真源，只升不降）**：`statements ≥ 88.8` / `branches ≥ 84.0` / `functions ≥ 83.9` / `lines ≥ 90.0`；该配置 `:301-304` 注明四列口径不可混用（`% Stmts` 不是 `% Lines`） |
| S6.6 | 端点基线五套 + 阶段四/W1/W2/B3/B4 增量严格相等，不允许「删端点让 CI 变绿」 | `deploy/validate_openapi.py --strict`，CI `ci-adapter.yml:842`；8 份 `docs/api/baseline-*.json`；`apps/adapter/tests/integration/test_endpoint_baseline.py:40-47,108-114` |
| S6.7 | 每个门禁必须挂进 workflow，且 `--self-test` 能证明「可红」；**例外：会写业务库的对账工具不进 PR CI** | `deploy/validate_gate_wiring.py:1-14`（C-1 反向遍历 `deploy/validate_*.py`），CI `ci-adapter.yml:479` + `--self-test :484`。**【2026-11-07 附录 B-8 最终裁决】** `apps/adapter/scripts/reconcile_usage.py` 偏差超限时**会写** `audit_log(billing.reconcile_mismatch)`（`:18,42`）、需真实 PG（`OA_BUSINESS_STORE=postgres`，否则退出码 2 / `[SKIP]`，`:41`）、CI 空账期三项计量全 0 会「恒等假绿」且污染审计表（`:43-44`），故**不挂 PR CI**，改**部署侧每日 02:00 定时调度**（`docs/architecture-phase2.md:417`）；正确性由 `tests/integration/test_usage_endpoints.py` 与 `tests/integration/test_qa_b07_b08_verification.py` 覆盖（`reconcile_usage.py:38-40`）。**不得**把该脚本「未挂 workflow」判为门禁缺失（S6.7 / `validate_gate_wiring.py` 的 C-1 不适用） |
| S6.8 | 金标集 20 个 yaml 合法 + 可复跑 | `qa/phase4-verify.sh:354-374,405`（`run_goldset_yaml` / `run_goldset_validate`）、`qa/goldset/run.py`；**【规范待固化】** 金标集未挂 4 条 workflow（只在 `qa/phase4-verify.sh` 手工终验），建议 `deploy/validate_goldset.py` 或加 CI step |
| S6.9 | 告警规则时间语义可离线复跑 | `deploy/alert.rules.test.yml`（`promtool test rules`，11 组含负样本）**已挂 CI**：`.github/workflows/ci-adapter.yml`「告警规则与投递链路自证（H-01）」同一步还跑 `promtool check rules` / `check config` / `amtool check-config`（含渲染两态）。2026-11-07 审计 H-01 闭环 |
| S6.10 | **saas 形态交付前安全基线验收**：ADR-0007 §4 升格落地 + 3 条条件性越权路径均有负样本用例 | **【2026-11-07 附录 B-5 最终裁决 · 定级 P1 高，V1.0 交付前必须闭环】** 三条条件性越权路径：① `OA_TENANT_MEMBERSHIP_CHECK=off`；② `auth_mode=stub` 跳过成员校验且不验签；③ 配置漏注入时 `config.py` 默认 `development+saas+stub` 使 `validate_auth_startup` 判据整体失效。硬化已交工程师执行，工作区可见 `security.py:772-790`（stub 不得获平台作用域）、`:847-859`（「B-5 起不再排除 stub」，stub 也走 AR-017）、`:1288-1289`（stub + 非回环启动期拒绝）、`deploy/.env.example:45-52` 订正、负样本用例补全。**【规范待固化】** 建议 `deploy/validate_tenant_context.py` 断言 saas 预设与成员校验开关；交付前须有负样本证明三条路径均被拦截。不阻断本轮提交（见附录 B-5） |

> **【2026-11-07 附录 B-2 已裁决并订正】** `docs/architecture-v1.0.md` §9.6 原写
> 「覆盖率 < 80% 禁合并」（修订前 `:1588`，现 `:1592-1597`）与 CI 的 85% / 88% 不一致；按「以 CI 为真源、
> **门槛不得降低、只升不降**」原则，本轮已把该行订正为**总 85% / PG 仓储 88% / 前端四阈值**
> 三档口径，并作废 80% 旧值。S6.2 / S6.4 / S6.5 为可执行真源。

---

## S7 交付流程（安装 / 升级 / 回滚 / 备份 / 证据留存）

| 编号 | 规范 | 机械保证 / 状态 |
| --- | --- | --- |
| S7.1 | 安装走 `deploy/install.sh`（唯一安装器，强制 `--env-file deploy/.env.lite`），生成 `.installed-manifest.json` 台账（版本 + 9 个镜像 digest + 5 份 init 脚本 sha256 + 时间） | `deploy/install.sh:30-32,66-69,1006-1065,1224`；台账不入库（`.gitignore`）；`deploy/validate_lite_up.sh:1-14` 真起栈断言 |
| S7.2 | 升级走 `deploy/upgrade.sh`：先比对安装台账指纹，再由 `--profile migrate` 一次性 Job 执行迁移（advisory lock），不放在 Web 启动路径 | `deploy/upgrade.sh:308-345,377-382,545`、`deploy/docker-compose.lite.yml:693-725`（migrate Job）；`deploy/validate_migrations.py`（双路径实跑），CI `ci-adapter.yml:429`（`--run-pg`）、`:496`（`--self-test`） |
| S7.3 | 回滚走 `deploy/rollback.sh` + `deploy/migrations/rollback/*.down.sql`；回滚前强制 `pg_dump` 备份；只退显式给出的版本（不自动级联） | `deploy/rollback.sh:1-40,64-65,175-200,233-247`；`deploy/validate_migrations.py`；`.gitignore` 忽略 `deploy/migrations/rollback/*/`（备份落点不入库） |
| S7.4 | 备份走 `deploy/backup.sh`（容器内 `pg_dump -Fc` 整库），恢复走 `deploy/restore.sh`（`pg_restore --clean` + 行数核对 + 抽查 md5），核对失败**服务保持停止** | `deploy/backup.sh:203-238,405`、`deploy/restore.sh:15-29,208-239,243-336,409-423`；`deploy/backups/` 不入库（`.gitignore`）。**【规范待固化】** `backup.sh:68-77` 的 `SNAPSHOT_TABLES` 仅 8 张表，未随 0015 新增的 `mcp_server` 等表同步，恢复校验存在盲区；建议 `deploy/validate_snapshot_tables.py` 或改为动态枚举 |
| S7.5 | 交付清单由 `scripts/gen_delivery_manifest.py` 从真源生成，**禁止手写**；`--check` 为硬门禁 | `docs/phase4-delivery-manifest.md:1-30`（维护人/复核人/真源）、`scripts/gen_delivery_manifest.py:1-26,42-46`；`qa/phase4-verify.sh:291`（`run_manifest` 调用 `--check`） |
| S7.6 | 每次交付留存 CI 双绿 run 编号与证据 SHA | `docs/phase4-delivery-manifest.md:4-8,14-16`（「证据」列是唯一允许手改的列）、`docs/delivery-2026-11-06-audit-export.md` 等交付文档；**【规范待固化】** 「证据 SHA ≠ `待补`」无门禁，建议 `deploy/validate_delivery_evidence.py` |
| S7.7 | 交付前跑 `qa/phase4-verify.sh` 9 项终验（含 16 个 `validate_*.py` 门禁本体 + 金标集 + 清单） | `qa/phase4-verify.sh:1-13,252-291,405`；**【规范待固化】** 该脚本未挂 4 条 workflow，属人工终验，建议加 `workflow_dispatch` 触发的 CI step |
| S7.8 | 镜像 tag/digest 与 `images.lock` 同源；离线分发用同一份镜像 tar | `deploy/images.lock:257-280`（自研镜像 `source: local`）、`deploy/install.sh:649-735`（digest 与 checksums 校验）；`deploy/validate_offline_pkgs.py`，CI `sbom.yml:141` |
| S7.9 | TLS 证书外部挂载、不入库；**生产必须真证书** | 三套编排 `docker-compose.yml:604-605` / `docker-compose.saas.yml:421-422` / `docker-compose.lite.yml:619-620`；`.gitignore` 忽略 `*.pem`/`*.key`；`deploy/gen-selfsigned-cert.sh:1-16` 声明「仅限本地验证」。**【规范待固化】** `deploy/install.sh:828-836` 在缺证书时**自动生成自签且只 warn 不阻断**，唯一安装器的默认路径会把自签证书带进交付；建议 `deploy/validate_edge_cert.py`（校验 CA/有效期/SAN 与 `OA_LITE_PUBLIC_URL` 主机名），并在 `install.sh` 加 `--production` 硬分支 |
| S7.10 | 会写业务库的运维对账工具走**部署侧定时调度**，不进 PR CI（与 S6.7 同源） | **【2026-11-07 附录 B-8 最终裁决】** `apps/adapter/scripts/reconcile_usage.py:34-44` 已由工程师补写准确口径：不由 PR CI 触发、部署侧每日 02:00 调度（`docs/architecture-phase2.md:417`）、正确性由 `tests/integration/test_usage_endpoints.py` 与 `test_qa_b07_b08_verification.py` 覆盖、空账期禁止运行。**【规范待固化】** 最小样本量门槛与 `billing.reconcile_mismatch` 告警尚无机械校验，建议 `deploy/validate_reconcile_schedule.py`（附录 A.4；「当前 3 条」口径待确认） |

---

## S8 规范强制化（七类约束与机械判据）

S1–S7 是**条款**，附录 A 是**条款 → 门禁**映射。本节回答评审最常问的一句：
「这条规范现在真的会拦住我吗？」——按七类给出**已生效的机械判据**。判据的
可执行真源是 `deploy/validate_standards.py`（第 20 个 `.py` 门禁，CI
`ci-adapter.yml`「工程规范强制门禁」+ `--self-test`），它**不重造**已有 19 道门禁，
只补三类现有门禁覆盖不到的漂移：豁免基线偷偷加行、文档计数与磁盘不一致、
配置项只声明不消费。

**前端侧同源判据**：STD-3 直接约束 `apps/web/e2e/**`，而 `apps/web` 不在
`ci-adapter` 的 paths 内——只在 adapter 侧接线时，只改 e2e 的 PR 不会触发该判据。
因此 `ci-web.yml` 的 quality-gate 用**同一份脚本**跑子集
`python3 deploy/validate_standards.py --checks STD-3`；判据单源、两侧同真。
`deploy/validate_ci_paths.py` 把该脚本登记为 ci-web 的依赖输入（C-1），
改脚本会同时触发两侧。

### S8.1 七类约束

| 类别 | 强制判据 | 红灯条件 | 复用的既有门禁 |
| --- | --- | --- | --- |
| 分层与依赖方向 | STD-1：两份 `*_baseline.txt` 每条豁免必须带审计编号 + `due_batch=Bn` + 消除计划；条数上限 **方向 23 / 环 3**，只许减不许增 | 缺任一字段、条数超过上限、审计编号不合法 | `validate_import_directions.py`（矩阵 + SCC + stale + 到期）、`validate_batch_clock.py` |
| 命名与模块边界 | STD-2：`apps/adapter/app/` 实际二级包必须全部在 `LAYERS` 内；STD-5 清单逐条覆盖 | 出现矩阵外新包且未同步矩阵 | `validate_import_directions.py`（S1.1-b）、`validate_openapi.py`（API 路径） |
| 测试强制 | STD-3：测试/e2e 禁 `.only(`；禁无条件 `pytest.mark.skip(`；workflow 禁 `ALLOW_SKIP`；新增 `business/`、`services/`、`db/repositories/` 模块必须同 diff 带测试改动（基线 `HEAD~1`） | 任一命中；新增业务模块零测试 | `validate_pg_markers.py`、CI 覆盖率高地板（S6.2/S6.4/S6.5） |
| 配置一致 | STD-4：模板每个 `OA_*` 必须解析到 `Settings` 字段或 `INFRA_ENV_KEYS`（带理由）；每个 `Settings` 字段必须有 app 内消费方或登记到 `CODE_ONLY_FIELDS`（带理由+到期批次）；8 个安全敏感键必须在两份模板中显式出现 | 无主键、死配置、安全键缺失 | `validate_config_names.py` R1/R2、`validate_deploy.py` R2/R6、`validate_secrets.py` |
| 文档一致 | STD-5：附录 A.1 的「N 个：M `.py` + K `.sh`」必须与磁盘一致；附录 A.5 清单必须逐条覆盖全部 `deploy/validate_*`，且四列非空 | 计数不符、清单缺行/多行、列空 | `validate_batch_clock.py --check-docs`（批次号四处同步）、`scripts/gen_delivery_manifest.py --check` |
| 安全默认 | STD-6：`mcp_allowed_hosts` 默认空串（出站默认拒绝）、`mcp_allow_loopback=false`、`mcp_enabled=false`、`ssrf_proxy_url` 空；模板同名键保持同一默认 | 默认放开 / 模板打开开关 | `validate_secrets.py`（禁明文兜底）、H-02 的 20 条 SSRF 对抗用例、`validate_import_directions.py`（分层） |
| 授权不可放大 | STD-7：权限判定必须按「租户 → 权限点 → 会话能力 → 资源」固定顺序执行，任一层拒绝即拒绝；会话能力只能由上层权限点派生收窄，客户端提交声明越界必须返回 `14009` 且**不得**求交后继续；拒绝必须留痕（层 / 权限点 / 请求项），不得记录正文与令牌 | 判定顺序颠倒、越界声明被夹紧后放行、拒绝无留痕或留痕含敏感明文 | `apps/adapter/tests/security/test_authz_four_layers.py`（28 条，`-m security` 真跑）、`check_error_catalog.py --strict`（`14009` 三处登记） |

### S8.2 豁免与到期

`CODE_ONLY_FIELDS` / `INFRA_ENV_KEYS` 是**带理由的白名单**，新增条目必须同时写
理由与到期批次；`validate_standards.py` 会断言两列非空。基线豁免的上限
（23 / 3）是 2026-11-07 审计实测值，**只许减不许增**——要新增一条豁免，先改本节
的上限常量并走评审，而不是往基线文件里加一行。

### S8.3 门禁清单（名字 / 检查什么 / 红灯条件 / 维护者）

> 由 STD-5 逐行校验：本表必须**恰好覆盖** `deploy/validate_*` 全部文件（当前 20 个），
> 四列不得为空。新增门禁时同步本表；行序与附录 A.1 一致。

见附录 **A.5**（下表为机器可校验的权威清单）。

---

## 附录 A：规范 → 机械门禁映射表

### A.1 `deploy/validate_*`（21 个：20 `.py` + 1 `.sh`）

| # | 门禁 | 职责（取自各自文件头） | CI 挂载点 | 自检 | 覆盖规范 |
| --- | --- | --- | --- | --- | --- |
| 1 | `deploy/validate_batch_clock.py` | 批次时钟 vs 台账 `due_batch` + 文档四处同步 | `ci-adapter.yml:218`、`:224` | ✔ | S4.1-i、S3.2-c |
| 2 | `deploy/validate_config_names.py` | 编排注入 `OA_*` 是 Settings 字段/白名单；`NEXT_PUBLIC_*` 有同名 ARG | `ci-adapter.yml:320`、`:324` | ✔ | S1.1-e、S2.5、S4.1-a |
| 3 | `deploy/validate_deploy.py` | R1 超级用户 DSN / R2 显式 `OA_ENV`+`OA_AUTH_MODE` / R3 initdb 挂载 / R4 lockroot / R5 端口 / R6 k8s 键 | `ci-adapter.yml:770`、`:772` | ✔ | S1.1-f、S4.1-d/e/g |
| 4 | `deploy/validate_deps.py` | 依赖清单 ↔ 架构 §8.1/§8.2 + 最低安全版本 + 加固 + 漏洞（四合一） | `dependency-scan.yml:539`、`:546`；`ci-adapter.yml:764` | ✔ | S5.5、S5.8 |
| 5 | `deploy/validate_gate_wiring.py` | C-1/C-4：判据存在 ≠ 判据在跑（反向遍历找未挂载脚本） | `ci-adapter.yml:479`、`:484` | ✔ | S6.7 |
| 6 | `deploy/validate_images.py` | compose `image:` ⊆ `images.lock` 精确匹配 / 禁浮动标签 / 上游存在性 / 文档版本交叉校验 / 禁 `redis-cli` | `ci-adapter.yml:758`；`dependency-scan.yml:149`、`:153`；`sbom.yml:146` | ✔ | S5.3、S5.4 |
| 7 | `deploy/validate_import_directions.py` | G-37 层矩阵 + Tarjan SCC + 基线 stale/到期 | `ci-adapter.yml:352`、`:359` | ✔ | S1.1-b、S3.2-a/b/c |
| 8 | `deploy/validate_lite_up.sh` | 轻量版静态断言 + 真起栈（9 容器 / 内存 / 健康检查） | 由 `deploy/install.sh:50-59` 调用（**非 workflow step**） | 内含 | S4.1-g、S7.1 |
| 9 | `deploy/validate_migrations.py` | 迁移静态自检 + 双路径实跑（空库 / 已装库） | `ci-adapter.yml:429`、`:496` | ✔ | S7.2、S7.3 |
| 10 | `deploy/validate_offline_pkgs.py` | `.difypkg` 包内协议黑名单 / 弱著佐权 / 已知漏洞 / SBOM 生成 | `sbom.yml:136`、`:141`、`:165` | ✔ | S5.1、S5.7、S7.8 |
| 11 | `deploy/validate_openapi.py` | G1–G5 端点基线 + 对外文档禁词泄露 | `ci-adapter.yml:842`（`--strict`） | ✔ | S1.1-h、S2.1、S3.1-d、S6.6 |
| 12 | `deploy/validate_packaging.py` | `app/` 全子包入打包清单 | `ci-adapter.yml:514`、`:519` | ✔ | S1.1-c、S5.6 |
| 13 | `deploy/validate_pg_markers.py` | 需真实 PG 的用例必须带 `pg` 标记 | `ci-adapter.yml:335`、`:339` | ✔ | S6.1 |
| 14 | `deploy/validate_plugins.py` | G-26/G-28 插件声明 schema + 能力/原语白名单 | `ci-adapter.yml:404`、`:409` | ✔ | S3.2-e |
| 15 | `deploy/validate_repository_parity.py` | G-38 内存/PG 双实现对称 | `ci-adapter.yml:375`、`:383` | ✔ | S3.2-d |
| 16 | `deploy/validate_schema_retention.py` | G-31 产物留存列/默认值/类型 | `ci-adapter.yml:449`、`:454` | ✔ | S7.3（产物表结构） |
| 17 | `deploy/validate_secrets.py` | G-33 密钥占位值生成说明 + 禁明文兜底 | `dependency-scan.yml:173`、`:178` | ✔ | S4.1-b、S4.1-c |
| 18 | `deploy/validate_ci_paths.py` | C-1 依赖输入（根锁 / `uv.lock` / `pyproject.toml` / Dockerfile / 迁移 / `deploy/` 配置 / e2e 夹具）必须被对应 workflow 的 push+PR `paths` 命中；C-2 死模式回代真实文件 | `ci-adapter.yml`「CI paths 漂移校验」（H-05） | ✔ | S6.7、H-05 |
| 19 | `deploy/validate_standards.py` | 六类规范机械判据：豁免基线完备性与上限、层矩阵包边界、测试纪律（`.only`/无条件 skip/`ALLOW_SKIP`/新模块带用例）、配置一致（模板键↔Settings↔消费方）、文档一致（A.1 计数 + A.5 清单）、安全默认 | `ci-adapter.yml`「工程规范强制门禁」（阶段 2） | ✔ | S8.1–S8.3 |
| 20 | `deploy/validate_offline_media.py` | 离线介质包（3A）：固定布局/命名（版本+架构）、MANIFEST 与 SHA256SUMS、逐条校验（缺失/哈希/未登记）、签名校验、缺工具 fail-closed | `ci-adapter.yml`「离线介质包门禁自检」（阶段 3A） | ✔ | S5.7（离线交付）、S7.8（SBOM） |
| 21 | `deploy/validate_offline_apply.py` | 离线介质包目标机链路（3B）：预检（架构/磁盘余量/工具）、写前校验、加载幂等与断点续跑、升级版本兼容、回滚安全停点（失败退出码 3） | `ci-adapter.yml`「离线介质目标机链路自检」（阶段 3B） | ✔ | S5.7（离线交付）、S7.2（迁移顺序） |

> `qa/phase4-verify.sh:252-277` 另有一套「20 个 `.py` 门禁本体」的本地复跑清单
> （FAMILY_A / FAMILY_B 两组，按自检退出码语义分），与 CI 挂载互为补充。

### A.5 门禁清单（名字 / 检查什么 / 红灯条件 / 维护者）

> STD-5 逐行校验：必须恰好覆盖磁盘上全部 `deploy/validate_*`（当前 21 个），
> 四列不得为空；新增/删除门禁必须同 diff 增删本表行。

| # | 门禁 | 检查什么 | 红灯条件 | 维护者 |
| --- | --- | --- | --- | --- |
| 1 | `deploy/validate_batch_clock.py` | `OA_BATCH` 与台账 `due_batch`、文档四处同步 | 批次落后 / 文档未同步 / 自检样本不符 | 平台工程（部署） |
| 2 | `deploy/validate_config_names.py` | 编排注入 `OA_*` 是否 `Settings` 字段；`NEXT_PUBLIC_*` 是否有同名 ARG | 无主键且未登记理由 / ARG 缺失 | 平台工程（部署） |
| 3 | `deploy/validate_deploy.py` | R1–R6：DSN 属主、显式 env、initdb 挂载、lockroot、端口、k8s 键 | 任一规则命中 / 编排集合漏加 | 平台工程（部署） |
| 4 | `deploy/validate_deps.py` | 依赖清单 ↔ 架构 §8.1/§8.2 + 最低安全版本 + 漏洞 | 未登记依赖 / 低于安全版本 / 命中黑名单 | 平台工程（后端） |
| 5 | `deploy/validate_gate_wiring.py` | 每个门禁是否真的被 workflow 引用（判据存在 ≠ 判据在跑） | 有脚本未被任何 workflow 引用 | 平台工程（部署） |
| 6 | `deploy/validate_images.py` | compose 镜像 ⊆ `images.lock` 精确标签 + 上游存在性 + 文档版本 | 浮动标签 / 摘要不符 / 文档版本漂移 | 平台工程（部署） |
| 7 | `deploy/validate_import_directions.py` | G-37 层矩阵 + Tarjan 环 + 基线 stale/到期 | 新增违规 / 环 / 基线过期 / 已修未删行 | 平台工程（后端） |
| 8 | `deploy/validate_lite_up.sh` | 轻量版静态断言 + 真起栈（容器数 / 内存 / 健康检查） | 起栈失败 / 容器超限 / 健康检查未就绪 | 平台工程（部署） |
| 9 | `deploy/validate_migrations.py` | 迁移静态自检 + 空库/已装库双路径实跑 | SQL 不幂等 / 双路径结果不一致 | 平台工程（后端） |
| 10 | `deploy/validate_offline_pkgs.py` | `.difypkg` 协议黑名单 / 弱著佐权 / 已知漏洞 / SBOM | 命中黑名单协议 / 高危漏洞 / 无法生成 SBOM | 平台工程（安全） |
| 11 | `deploy/validate_openapi.py` | G1–G5 端点基线 + 对外文档禁词 | 端点集合漂移 / 文档泄露底座术语 | 平台工程（后端） |
| 12 | `deploy/validate_packaging.py` | `app/` 全子包是否进打包清单 | 漏包（历史上漏 `business`/`db`） | 平台工程（后端） |
| 13 | `deploy/validate_pg_markers.py` | 需真实 PG 的用例是否带 `pg` 标记 | 缺标记导致 PG 用例静默跳过 | 平台工程（后端） |
| 14 | `deploy/validate_plugins.py` | 插件声明 schema / 能力枚举 / 产物依赖闭合 | schema 非法 / 能力越界 / 依赖不闭合 | 平台工程（后端） |
| 15 | `deploy/validate_repository_parity.py` | 内存/PG 双实现方法名、record 字段、钩子对称 | 双实现不对称 / 基线 stale | 平台工程（后端） |
| 16 | `deploy/validate_schema_retention.py` | 产物留存列 / 默认值 / 类型 | 缺列 / 类型不符 / 默认值漂移 | 平台工程（后端） |
| 17 | `deploy/validate_secrets.py` | 密钥占位值生成说明 + 禁明文兜底 | 缺生成说明 / `${VAR:-常量}` | 平台工程（安全） |
| 18 | `deploy/validate_ci_paths.py` | 依赖输入是否被对应 workflow 的 push+PR `paths` 命中；死模式回代真实文件 | 根锁/锁文件/夹具变更不触发门禁 / 抄错路径 | 平台工程（部署） |
| 19 | `deploy/validate_standards.py` | 六类规范：豁免基线上限与到期、层矩阵包边界、测试纪律、配置三方一致、文档计数/清单一致、安全默认 | 基线超限或缺到期 / 矩阵外新包 / `.only` 或无理由 skip / 死配置 / 文档与磁盘不一致 / 安全默认被打开 | 平台工程（后端） |
| 20 | `deploy/validate_offline_media.py` | 介质包布局与命名、MANIFEST/SHA256SUMS、逐条哈希与未登记文件、cosign 签名、缺工具 fail-closed | 结构缺失 / 1 字节篡改 / 文件增删 / 签名失败 / 缺工具仍尝试构建 | 平台工程（部署） |
| 21 | `deploy/validate_offline_apply.py` | 预检（架构/磁盘余量/工具）、写前校验 fail-closed、加载幂等与崩溃断点续跑、升级版本兼容、回滚安全停点 | 缺工具/磁盘不足仍写入 / 校验失败仍写入 / 重复加载 / 非法升级 / 回滚半途报成功 | 平台工程（部署） |

### A.2 四条 workflow（job 级）

| workflow | job（行号） | 关键 step | 覆盖规范 |
| --- | --- | --- | --- |
| `ci-adapter.yml` | `quality-gate:45` | ruff `:237`/`:243`、deploy `:263`/`:266`、scripts `:289`/`:292`、mypy `:298`、`check_error_catalog :308`、pytest `:549`、skipped `:560`、PG 覆盖 `:598` | S1.1-d、S2.6、S2.8、S6.2/3/4 |
| | `registry-validation:619` | 批次时钟 `:218`、各 `validate_*`、镜像 `:758`、依赖 `:764`、部署 `:770` | S3.2、S4、S5 |
| | `openapi-export:802` | `validate_openapi.py --strict :842`、对外文档禁词 `:868`、插件契约换源 `:846-852` | S1.1-h、S2.1、S3.1-d、S6.6 |
| `ci-web.yml` | `quality-gate:48` | eslint `:73`、tsc `:79`、vitest `:92`、build `:98` | S6.5 |
| | `terminology-gate:103` | G-14 `:121`/`:127`/`:133`、G-17 `:149`、G-18 `:171`、登录闭环 `:191`、G-31 `:211`、G-27 `:231`、G-30 `:251` | S3.1-e/f、S6.5 |
| | `built-artifact-gate:267` | G-16 `:294`/`:296` | S3.1-e |
| | `format-check:309` | prettier `:323` | S2.8 |
| | `e2e-gate:346` | Playwright `:406` | S6.5 |
| `dependency-scan.yml` | `base-layer-isolation:40` | 前端/插件字样 `:49`、前端依赖 SDK `:95`、适配层唯一出口 `:108` | **S3.1-a/c** |
| | `image-tag-existence:132` | `:146`/`:151`/`:155` | S5.3 |
| | `license-scan:190` | 密钥兜底 `:172`、Python 协议 `:219`、npm audit `:250`、Node 协议 `:258`、黑名单判定 `:277` | S4.1-c、S5.1、S5.5 |
| | `dependency-manifest:503` | `validate_deps.py :539`/`:546` | S5.5 |
| `sbom.yml` | `generate:61`、`license-scan:118`、`notice:151` | `validate_offline_pkgs.py :136/:141/:165`、`validate_images.py :146` | S5.1、S5.7、S7.8 |

### A.3 不在 `deploy/` 下的门禁

| 门禁 | 挂载点 | 覆盖规范 |
| --- | --- | --- |
| `apps/adapter/scripts/check_error_catalog.py` | `ci-adapter.yml:308`（`--strict`） | S2.6（三处码集合一致 + 内部码禁区） |
| `apps/web/scripts/check-banned-terms.mjs` | `ci-web.yml:121`, `:127`, `:133`, `:294` | S3.1-e |
| `apps/web/scripts/check-no-placeholders.mjs` | `ci-web.yml:149` | S3.1-e |
| `apps/web/scripts/check-page-states.mjs` | `ci-web.yml:171` | S3.1-f |
| `apps/web/scripts/check-oidc-auth.mjs` | `ci-web.yml:191` | S3.1-e（登录闭环 PKCE S256） |
| `apps/web/scripts/check-plugin-contract.mjs` | `ci-web.yml:211`；`ci-adapter.yml:852`（`--generated`） | S6.6、S3.1-f |
| `apps/web/scripts/check-workbench-host.mjs` | `ci-web.yml:231` | S3.1-f（G-27） |
| `apps/web/scripts/check-capability-consistency.mjs` | `ci-web.yml:251` | S3.1-f（G-30） |
| `apps/adapter/scripts/reconcile_usage.py` | **不挂 PR CI**（`grep -rn` 零引用，裁决明令不挂） | 部署侧每日 02:00 调度（`docs/architecture-phase2.md:417`）；正确性由 `tests/integration/test_usage_endpoints.py` 与 `test_qa_b07_b08_verification.py` 覆盖 — **已裁决**，见附录 B-8 / S6.7 / S7.10 |

### A.4 只有文档、没有机械门禁的规范（【规范待固化】汇总）

| 规范 | 现状 | 建议脚本名（**尚未实现**） |
| --- | --- | --- |
| S1.1-a 顶层目录白名单 | 仅 `docs/architecture-v1.0.md:1203`（§6.1 目录树） | `deploy/validate_repo_layout.py` |
| S1.1-g 忽略项/脏文件不入库 | `.gitignore` 静态 | `deploy/validate_repo_clean.py` |
| S1.1-h 基线文件必须 tracked | 靠 `.gitignore` + `validate_openapi.py` 间接 | 并入 `deploy/validate_openapi.py` |
| S2.2 ULID 资源 ID 全量走生成器 | `core/ids.py` 代码约定 | `deploy/validate_resource_ids.py` |
| S2.3 JSON 字段/枚举命名 | 靠契约快照间接 | `deploy/validate_naming.py` |
| S2.4 前端命名风格 | eslint 无显式命名规则 | 在 `apps/web/eslint.config.mjs` 加规则 |
| S2.7 日志 6 必填字段 | `core/logging.py` processor 兜底 `unknown`，无静态断言 | `deploy/validate_log_fields.py` |
| S2.8 `.gitattributes` 与实际行尾 | ruff/prettier 只管格式 | `deploy/validate_line_endings.py` |
| S2.9 提交信息格式 | `docs/architecture-v1.0.md:1560` + `CONTRIBUTING.md` | `.github/workflows/commit-lint.yml` 或 `deploy/validate_commit_msgs.py` |
| S3.1-b/c Dify 边界豁免失效条件 + 原生路径常量 | 仅 `dependency-scan.yml:108` 查 2 个标记 + 单测 `test_model_service.py:292` | `deploy/validate_dify_boundary.py` |
| S3.1-f 统一组件库 import 来源 | G-27/G-18/G-30 间接 | `apps/web/scripts/check-ui-library.mjs` |
| S3.2-c 方向基线 `due_batch` 到期 | `validate_batch_clock.py` 不读两份方向基线 | 扩展 `deploy/validate_batch_clock.py` |
| S4.1-e R6 反向断言 | 单向子集 | 扩展 `deploy/validate_deploy.py` |
| S4.1-f/h k8s 与 compose 配置等价 | `app.env.keys:17-22` / k8s README §5.6 文字登记 | `deploy/validate_k8s_parity.py` |
| S5.2 不展示 Dify 品牌标识 | `images.lock:42-56` + `dependency-scan.yml:80-84` 白名单 | `apps/web/scripts/check-brand-assets.mjs` |
| S5.6 Dockerfile 必须消费锁文件 | 靠 `validate_packaging.py` 间接 | 并入 `deploy/validate_packaging.py` |
| S5.8 漏洞适用性评估留痕 | `docs/architecture-v1.0.md:1615-1624` | `deploy/validate_risk_evidence.py` |
| S6.8 金标集挂 CI | 仅 `qa/phase4-verify.sh:354-374` | `deploy/validate_goldset.py` 或加 CI step |
| S6.9 `alert.rules.test.yml` 挂 CI | 已挂 `ci-adapter.yml`（H-01，2026-11-07） | 无需新脚本 |
| S7.4 备份快照表清单随 schema 演进 | `backup.sh:68-77` 硬编码 8 表 | `deploy/validate_snapshot_tables.py` |
| S7.6 交付证据 SHA ≠ `待补` | `docs/phase4-delivery-manifest.md:14-16` 人工 | `deploy/validate_delivery_evidence.py` |
| S7.7 `qa/phase4-verify.sh` 挂 CI | 人工终验 | 加 `workflow_dispatch` step |
| S7.9 生产真证书 | `gen-selfsigned-cert.sh:1-16` 文字 | `deploy/validate_edge_cert.py` |
| **B-3 文档示例中的 `OA_*` 键 ⊆ `Settings.model_fields`**（新增 S4.1-j） | `validate_config_names.py` R1 只扫编排注入键、不扫文档示例（§9.5 历史名 `OA_KEYCLOAK_URL` 即由此漏网） | 扩展 `deploy/validate_config_names.py`，或 `deploy/validate_doc_env_keys.py` |
| **B-4 术语门禁与直连门禁共用同一张豁免路径清单**（新增，ADR-0004 §3.3） | `check-banned-terms.mjs` 靠「词表不含组件名」隐式放行；`dependency-scan.yml:84` 用内联正则硬编码 `about/page.tsx` 路径，两处各自维护 | `apps/web/scripts/check-attribution-allowlist.mjs` 或并入 `check-banned-terms.mjs`；单一真源建议 `apps/web/scripts/attribution-allowlist.json` |
| **B-9 文档端点数 = 8 套 baseline 合计（103）**（新增，§9.6 已登记） | `docs/architecture-v1.0.md` §9.6 表格数字曾停在阶段三；`deploy/validate_openapi.py` 与 8 份 `docs/api/baseline-*.json` 是唯一真源，但**没有**文档↔基线数字等式断言 | `deploy/validate_endpoint_doc_parity.py`（或并入 `deploy/validate_openapi.py`） |
| **B-6 k8s ↔ compose 配置等价性（V1.1 前置）** | 裁决：V1.0 只登记差异、不要求等价；V1.1 启用前必须补门禁 | `deploy/validate_k8s_parity.py` 或扩展 `validate_deploy.py` R6 反向断言 |
| **B-5 saas 租户上下文安全基线（新增 S3.1-g / S6.10）** | ADR-0007 §4 仍是「目标态建议」（`docs/decisions/0007-tenant-context-source.md:47-49,65`），`:21` 要求 saas 形态开形态前升 P0 先修；3 条条件性越权路径无机械断言 | `deploy/validate_tenant_context.py`：断言 saas 预设（`OA_ENV`/`OA_AUTH_MODE`/`OA_DEPLOY_MODE` 显式注入）、`OA_TENANT_MEMBERSHIP_CHECK != off`、stub 不得获平台作用域、stub+非回环启动期拒绝；并校验负样本用例存在 |
| **B-8 对账调度与告警机械校验（新增 S6.7 / S7.10）** | 最小样本量门槛**脚本内尚未实现**（`reconcile_usage.py:44` 仅文字引用 `docs/qa-phase2-test-plan.md` R-5）；`billing.reconcile_mismatch` 写入与部署侧 02:00 调度无机械校验 | `deploy/validate_reconcile_schedule.py`：校验调度配置存在、最小样本量门槛生效、`audit_log` 告警写入路径有负样本覆盖。**【待确认】** 主理人裁决口径「最小样本量当前 3 条」与 `docs/qa-phase2-test-plan.md:1411` R-5「事件数 <100 输出 `INSUFFICIENT_SAMPLE`」不一致；`reconcile_usage.py:11-16` 的「三条检查」是检查项而非样本量。以主理人最终口径为准，本条不预设数字 |

---

## 附录 B：裁决台账（主理人齐活林，2026-11-07）

> **状态**：原 10 项「待确认」已全部由主理人正式裁决，依据为「项目总目标与执行标准
> 不得降格、不得偏移」及用户授权主理人自主决定细分实现。
> **B-5 / B-8 已于第 4 轮补充最终核查结论**，状态由「核查中」改为**「已裁决」**：
> B-5 = **P1 高、V1.0 交付前闭环、saas 形态前置硬约束**；
> B-8 = **不挂 PR CI，改部署侧每日 02:00 调度**。其余 8 项裁决不变。
> 保留原证据 A / B 两列，新增「裁决与执行归属」列；「建议动作」列改为裁决后的落地动作。

| # | 事项 | 证据 A | 证据 B | 裁决与执行归属 | 建议动作（裁决后） |
| --- | --- | --- | --- | --- | --- |
| B-1 | **分支模型**：架构文档写 Git Flow（`develop` / `release/*` / `hotfix/*`） | `docs/architecture-v1.0.md:1554-1570`（§9.4，2026-11-07 已按裁决修订；修订前 Git Flow 口径见该节「裁决记录」） | 该节「现状说明」自承「实际只有 `main` 一个分支」；`git branch -a` 当前为 `main` + `feature/event-log` + 远端同名 | **已裁决**：采用**主干开发**——`main` 唯一主干 + 短生命周期 `feature/*` + PR 合入 + tag 发版，保留 `hotfix/*`（仍须 PR），**不恢复 `develop`**。执行：架构师本轮已修订 `docs/architecture-v1.0.md §9.4` 与 `CONTRIBUTING.md §2`，去掉「待确认」 | 按 `CONTRIBUTING.md §2` 执行；后续不得在 PR 描述中宣称走 `develop` 流程 |
| B-2 | **覆盖率门槛**：文档 80% vs CI 85% / PG 88% | `docs/architecture-v1.0.md:1592`（现覆盖率段；修订前 `:1588` 写「< 80% 禁合并」） | `ci-adapter.yml:557`（85）、`:601`（88）、`apps/adapter/pyproject.toml:256`（85）、`apps/web/vitest.config.mts:305-310`（四阈值 88.8/84.0/83.9/90.0） | **已裁决**：**以 CI 为真源**，总 85% / PG 仓储 88% / 前端 vitest 四阈值，**不得降低门槛、只升不降**。执行：架构师本轮已订正 §9.6，作废 80% 旧值；本文件 S6.2/S6.4/S6.5 同步 | 任何放宽 `--cov-fail-under` / 阈值的提案须主理人书面裁决 |
| B-3 | **环境变量示例含旧键** | `docs/architecture-v1.0.md:1572-1588`（§9.5 原 `OA_KEYCLOAK_URL`） | `deploy/validate_config_names.py:1-14` 说明该键无 alias 会被 pydantic 静默忽略；三套 compose 现用 `OA_KEYCLOAK_BASE_URL` | **已裁决**：订正 §9.5 示例为 `OA_KEYCLOAK_BASE_URL`，并新增规范「**文档示例中的 `OA_*` 键必须是 `Settings` 实际字段**」。执行：架构师本轮已改 §9.5、新增 S4.1-j | `validate_config_names.py` R1 目前只扫编排注入键、不扫文档示例 → 列 **【规范待固化】**（附录 A.4），建议扩展该脚本 |
| B-4 | **前端技术黑盒化的两条门禁口径** | `ci-web.yml:121` 术语词表（`check-banned-terms.mjs:49-110`）不含 `dify/pgvector/valkey` 组件名 | `dependency-scan.yml:80-84` 对 `about/page.tsx` 含 `license:` 的署名表行给显式白名单；`apps/web/src/app/(main)/about/page.tsx:27-42` 确列 Dify/pgvector/Valkey | **已裁决**：**开源声明页属合规必需豁免**（Dify 修改版许可要求署名/版权不可移除），但边界收窄为**仅允许第三方组件名称、版本与许可信息**；禁止模型名/向量库/RAG/Agent 作为产品能力描述；两条门禁白名单**必须共用同一张路径清单**。执行：架构师本轮新建 `docs/adr/0004-open-source-attribution-exemption.md`、改写 S3.1-e；`role` 文案收窄由 PM/工程、清单落地由工程 | **同时更正 ARCH-11**：豁免**已在 CI 登记**，问题是两套门禁口径不一致，不是「边界未定义/无豁免」。共用清单与 `check-attribution-allowlist.mjs` 列 **【规范待固化】**（附录 A.4 / ADR-0004 §3.3） |
| B-5 | **租户上下文优先级** | 工程师核查结论：`apps/adapter/app/core/security.py:489` 注释**没有写反**，与 `:503-517` 的 `resolve_tenant_id()` 实现一致（saas 下 `X-Tenant-Id` 有值即覆盖令牌声明，经 `:937-938` 的 AR-017 成员校验）；`deploy/.env.example:45-52` 已按此订正 | `docs/decisions/0007-tenant-context-source.md:10` 结论字段为「P2 配置依赖型缺陷、非当前可复现越权」；`:47-49` §4「唯一可信来源 = 令牌声明」是**目标态建议**，`:65` §5 把后端语义收紧列为「Batch 2，需单独 ADR」，`:21` 自设前置「若 V1.1 交付 saas 多租户，开该形态前必须升 P0 并先修」——**不是「实现违反已生效 ADR」** | **已裁决（第 4 轮）：P1 高，V1.0 交付前必须闭环；saas 形态前置硬约束**。3 条条件性越权路径真实存在：① `OA_TENANT_MEMBERSHIP_CHECK=off`；② `auth_mode=stub` 跳过校验且不验签；③ 配置漏注入时 `config.py` 默认 `development+saas+stub` 使 `validate_auth_startup` 判据整体失效。**不阻断本轮提交**：三套 compose + k8s 均显式注入 `OA_ENV`/`OA_AUTH_MODE`/`OA_DEPLOY_MODE`，private 模式忽略请求头。硬化已交工程师执行（stub 也走成员校验、stub 不得获平台作用域、stub+非回环启动期拒绝、`.env.example:45-52` 订正、补负样本用例）；**`security.py:489` 注释不改**。**硬约束**：saas 形态对外交付前，ADR §4 必须升格为已落地的安全基线（ADR `:21` 自己的前置条件）——已写入 **S3.1-g / S6.10 / A.4** | 交付前须有负样本证明 3 条路径均被拦截；**【规范待固化】** `deploy/validate_tenant_context.py`（断言 saas 预设与成员校验开关），见附录 A.4 |
| B-6 | **k8s 与 compose 配置等价性** | `deploy/k8s/base/configmap.yaml:29,38,44`（`OA_PUBLIC_BASE_URL=""`、`OA_DOWNSTREAM_ENABLED=false`、`OA_JWT_ISSUER=localhost`）覆盖 Secret 同名键（`configmap.yaml:24-26` 自注） | 三套 compose `OA_DOWNSTREAM_ENABLED: "true"`；`deploy/k8s/README.md §5.2` 只点名 2 个站点相关键；`app.env.keys:17-22` 仅文字登记 4 个内联键 | **已裁决**：**V1.0 不要求 k8s 与 compose 功能等价**（k8s 自定位 V1.1 准备物、不作 V1.0 验收依据）；但已知差异须**集中登记到决策/矩阵层**并在 `deploy/k8s/README.md` 头部显著标注；**V1.1 启用前补齐等价性门禁**。执行：架构师本轮已写入 S4.1-f/h 与本台账；**README 头部标注与差异登记的执行归属为工程师/PM**——受第 3 轮冻结（不得改 `deploy/`）本轮**未改** | V1.1 前置：`deploy/validate_k8s_parity.py` 或扩展 R6 反向断言；登记落点建议 `docs/decisions/` 或 `docs/gap-matrix-2026-11-03.md` |
| B-7 | **`.trae/` 归属** | `git status --porcelain --untracked-files=all` 列 3 个 `?? .trae/documents/…`；`git check-ignore -q` 退出码 1（未被忽略） | `.gitignore` 对 `.workbuddy/`、`.vscode/*`、`.screenshots/`、`apps/adapter/.qa_*` 等均有显式规则，唯独无 `.trae/` | **已裁决**：`.trae/` 为 IDE 本地工作区，**加 `.gitignore` 规则不入库**；3 份计划的设计要点**脱敏后归档进 `docs/decisions/`**。执行归属：**配置改动由工程师、归档由 PM**；架构师本轮只更新本台账状态 | 工程师改 `.gitignore` 后复核 `git status` 不再出现 `?? .trae/`；PM 归档时注意与本仓库已入库交付文档的口径去重 |
| B-8 | **`apps/adapter/scripts/reconcile_usage.py` 是否应挂门禁** | 脚本偏差超限**会写** `audit_log(billing.reconcile_mismatch)`（`apps/adapter/scripts/reconcile_usage.py:18,42`）、需真实 PG（`OA_BUSINESS_STORE=postgres`，否则退出码 2 / `[SKIP]`，`:41`）、CI 空账期三项计量全 0 会「恒等假绿」并污染审计表（`:43-44`） | `.github/workflows/` 与 `deploy/validate_gate_wiring.py` 中零引用；`docs/architecture-phase2.md:417` 已有「每日 02:00 由运维 cron 或 CI 手动跑」口径；正确性由 `tests/integration/test_usage_endpoints.py` 与 `tests/integration/test_qa_b07_b08_verification.py` 覆盖（`reconcile_usage.py:38-40`） | **已裁决（第 4 轮）：不挂 PR CI，改部署侧每日 02:00 调度**。文件头准确口径已由工程师补写（`reconcile_usage.py:34-44`）；「未挂 workflow」**不得**被 S6.7 / `validate_gate_wiring.py` C-1 判为门禁缺失。已写入 **S6.7 / S7.10 / A.3 / A.4** | **【规范待固化】** `deploy/validate_reconcile_schedule.py`：最小样本量门槛 + `billing.reconcile_mismatch` 告警的机械校验。**【待确认】** 主理人裁决口径「最小样本量当前 3 条」与 `docs/qa-phase2-test-plan.md:1411` R-5「事件数 <100 输出 `INSUFFICIENT_SAMPLE`」不一致，且门槛在脚本内**尚未实现**；`reconcile_usage.py:11-16` 的「三条检查」是检查项而非样本量。以主理人最终口径为准，本条不预设数字 |
| B-9 | **端点基线数字口径** | `docs/architecture-v1.0.md:1633`（修订前 `:1625`；原 G-29 写 `core=30 / phase2=38 / phase3=16`） | `ci-adapter.yml:831-842` 注释为五套基线；`docs/api/` 实有 **8 份** `baseline-*.json`，`endpoints` 数组长度实测 30+38+16+10+2+2+4+1 = **103** | **已裁决**：**以 `deploy/validate_openapi.py` + 8 套 baseline（合计 103）为真源**，订正 §9.6 表格；并把「**文档端点数 = 基线合计**」列为 **【规范待固化】**。执行：架构师本轮已改 §9.6 G-29 行、加了待固化注记、A.4 新增条目 | 建议 `deploy/validate_endpoint_doc_parity.py`（或并入 `deploy/validate_openapi.py`）：扫描架构文档端点数并断言等于 8 套 baseline 的 `endpoints` 长度合计 |
| B-10 | **A-01 修复提交状态** | `git diff deploy/validate_deploy.py` 显示 `COMPOSE_FILES` 已补 lite + 新增 `LITERAL_SUPERUSER_DSN`（工作区） | `git status --short` 显示 4 个文件 `M`、尚未提交（第 3 轮取证时已扩展为 6 个 `M`，含 `deploy/.env.lite.example`、`deploy/install.sh`） | **已裁决**：由**主理人在 PM 文档整改回传后统一提交**；**提交后请架构师复核**两份规范文档与附录 A.1 的行号是否漂移并给出复核结论（**待提交后执行**） | 提交后重跑 `git diff --check` + 抽查 `validate_deploy.py` 的 `COMPOSE_FILES` 行号；若漂移，按「以符号名为锚」更新文档引用 |

---

## 附录 C：与既有文档的关系（不另起炉灶）

| 既有资产 | 本文件如何承接 |
| --- | --- |
| `docs/moy-project-charter-v1.0.md`、总方案 1.2/3.5/5.3/6.4 | S3.1、S5.1 的原约束，本文件不改写、不降格 |
| `docs/adr/0001` / `0002` / `0003` | S3.1-a、S5.1、S3.1-f 的权威来源；本文件只补「由哪个门禁保证」 |
| `docs/adr/0004-open-source-attribution-exemption.md`（2026-11-07 新增） | 承载附录 B-4 裁决：开源声明页豁免的正当性、边界（仅名称/版本/许可）、两条门禁共用路径清单要求，以及 ARCH-11 结论更正；对应 S3.1-e |
| `docs/architecture-v1.0.md` §9.1–§9.6 | S2、S4、S5、S6 的条款来源。**第 3 轮按主理人裁决已订正 §9.4（主干开发）/ §9.5（`OA_KEYCLOAK_BASE_URL` + 文档示例键约束）/ §9.6（覆盖率 85/88/前端四阈值、G-29 8 套 103、文档端点数等式待固化）**；其余漂移登记在附录 B |
| `docs/architecture-phase3.md` §10（G-26~G-31 唯一真源） | 本文件不复制判据，只在 A.1/A.2 指向对应脚本与 CI 行号 |
| `deploy/validate_*.py`、`.github/workflows/*`、`apps/web/scripts/*` | **可执行真源**；本文件的条款若与门禁冲突，以门禁为准并登记待办 |
| `docs/phase4-delivery-manifest.md` | S7.5/S7.6 的交付清单真源；本文件不改清单内容 |
| `CONTRIBUTING.md` | 本文件的「日常执行版」：提交、分支、门禁命令、三类变更同步清单 |

> **维护纪律**：新增门禁 → 先挂 CI（S6.7）→ 再更新本文件附录 A；新增规范 → 先标
> **【规范待固化】** 并给建议脚本名，门禁落地后才可改成「机械保证」。
> **禁止**把只有注释/文档支撑的规则写成「已有门禁保证」。
