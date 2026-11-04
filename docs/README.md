# 文档索引（docs/README.md）

> **用途**：本仓库 `docs/` 下文档较多，本索引是**唯一入口**。新增文档请同时在此登记，
> 否则会成为「无入链孤立文档」（2026-11-04 审计发现 4 份）。
>
> **权威顺序**（冲突时以此为准）：`moy-project-charter-v1.0.md`（总纲）
> → `strategy-2026-11-04-company.md`（战略）→ `spec-base-v1.0.md`（规格）
> → `plan-base-*.md` / `design-*.md`（执行）→ 代码与门禁。
> 完整裁决规则见 **§六「文档层级与冲突裁决」**。
> 历史验收报告与审计报告是**快照**，不作为现值引用。

---

## 一、现行规范（写代码/验收前必读）

| 文档 | 说明 |
| --- | --- |
| **`spec-base-v1.0.md`** | **底座规格（规格层 · 唯一真源 · 冻结候选）**：五件套 + 企业层 + 相元模型 + 四条验收 + 非目标 + 取代关系 + 变更流程。任何「这算不算底座的一部分」只依此文件裁决 |
| **`plan-base-2026-11-04.md`** | **底座分阶段推进计划（生效）**：P0–P4、141 人日分配、依赖图、阶段门禁、五条拍板项及默认值、人力情景、风险登记、每周节奏 |
| `moy-project-charter-v1.0.md` | 项目总纲：定位、范围、能力分级 L1-L5（宪法层，原文不改；待修订条款见 `spec-base-v1.0.md` §10） |
| `plan-v1.0.md` | 24 周四阶段开发执行总方案 |
| `design-base-harness-2026-11-04.md` | 底座完整设计（执行层）：12 项对标矩阵、相 JSON Schema 与两份示例装配单、五层架构、四个关键机制、代码映射、T01–T08 与 141 人日估算。**字段级与机制级定义以本文为准，规格文档引用不复述** |
| `prd-v1.0.md` | V1.0 产品需求文档 |
| `prd-phase{2,3,4}-increment.md` | 阶段二/三/四增量 PRD |
| `prd-phase3-traceability.md` | 阶段三 PRD 断言 → 用例可追溯表 |
| `architecture-v1.0.md` | 系统架构 + 30 端点冻结契约 + 错误码注册制 |
| `architecture-phase{2,3,4}.md` | 各阶段增量架构（38 / 16 / 10 端点）与批次分解 |
| `architecture-w1-metrics-entry.md` | W1 切片（任务统计 + 能力推荐） |
| `api/README.md` + `api/baseline-*.json` | 接口规范总览与端点冻结基线（`openapi.json` 由适配层生成，不入库） |
| `deployment-guide.md` | 私有化/SaaS 部署、升级、回滚、TLS、排障 |
| `user-guide.md` | 终端用户使用说明 |
| `plugin-dev-guide.md` | 插件声明规范与样例 |
| `metrics-definition-v1.0.md` | 指标口径定义（与 `/metrics` 对应） |
| `perf-disclosure.md` | 性能披露口径（含 `scripts/bench/` 用法） |
| `lite-container-baseline.md` | 轻量版 9 容器基线 |
| `key-rotation-checklist.md` | 密钥轮换清单 |

## 二、阶段验收与交付（历史快照）

| 文档 | 说明 |
| --- | --- |
| `phase{1,2,3,4}-acceptance-report.md` | 各阶段验收结论与遗留项 |
| `phase2-gate-evidence.md` / `phase3-backend-handoff.md` / `phase3-demo-script.md` | 门禁取证 / 交接 / 演示脚本 |
| `phase4-delivery-manifest.md` | 交付清单（含「等价实现」列） |
| `phase4-batch-log.md` / `phase4-batch2-*.md` | 阶段四批次记录 |
| `qa-*.md` | QA 测试计划、批次报告、豁免、签核 |

## 三、审计与整改

| 文档 | 说明 |
| --- | --- |
| `audit-2026-11-04-cleanup-audit.md` | **本轮**：文档/代码一致性、失效引用、冗余依赖清单 |
| `audit-2026-11-03-full-repo-audit.md` | 第五轮全仓审计（发布条件判定） |
| `audit-2026-11-02-*.md` | 第四轮：总报告 + 架构性能依赖 / 质量安全 / 测试覆盖三份分册 |
| `remediation-ledger-2026-11-02.md` / `remediation-2026-11-03.md` | 整改台账与执行记录 |
| `verify-2026-11-02-fix-batch1.md` | 整改独立验证 |
| `gap-analysis-2026-10-25-commercial-readiness.md` | 商业化就绪度差距 |
| `gap-matrix-2026-11-03.md` | 能力差距矩阵与分阶段路线 |
| `improvement-plan-2026-10-25.md` / `feature-parity-plan-2026-10-25.md` | 改进计划与对标补齐 |
| `archive/` | 更早的审计与 QA 报告（含 2026-10-30、10-31 两轮全仓审计） |

## 四、研究与决策

| 文档 | 说明 |
| --- | --- |
| `adr/0001`~`0003` | 适配层唯一原则、基础设施许可合规、设计令牌 |
| `decisions/0003`~`0009` | 决策确认单（拍板记录，与 ADR 配套）。**`0009` = 底座 v1.0 冻结与战略复审裁定（D-47～D-54），规格与计划的效力来源** |
| `research-2026-10-25-*.md` | AI 工作台对标与 EvoFlow 对比 |
| `platform-strategy-2026-11-03.md` / `project-brief-2026-11-03.md` | 平台战略与工程简报（**平台战略整份已被 `spec-base-v1.0.md` §10 取代**，仅作研究材料） |
| `strategy-2026-11-04-company.md` | 公司战略裁决（**战略层**）：垂直/交付形态/定价/路线/7 条待拍板 D1–D7 |
| `alignment-2026-10-23-*.md` / `tech-path-review-2026-10-23.md` | 总纲对齐与技术路径复核 |
| `dependency-upgrade-watchlist.md` | 依赖升级观察清单（含触发条件） |
| `design-2026-11-02-*.md` | Keycloak Realm 与安全部署设计 |

---

## 五、可执行工具（不在 docs/，在此登记）

| 入口 | 用途 | 备注 |
| --- | --- | --- |
| `qa/phase4-verify.sh` | 四阶段终验 9 项（含 16 道部署门禁 + pytest） | CI 亦引用；`--pytest-mode pg\|both\|skip` |
| `qa/goldset/run.py` | 金标任务集复跑（20 用例） | 需真实服务与凭据；缺凭据返回 `blocked`，**拒绝伪造结果** |
| `scripts/bench/bench_{chat,kb,plugin}.py` | 性能压测 | 用法见 `scripts/bench/README.md` 与 `perf-disclosure.md` |
| `scripts/demo_phase3.sh` | 阶段三三插件端到端演示 | 需 `OA_TEST_DATABASE_URL` |
| `scripts/gen_delivery_manifest.py` | 交付清单生成/校验 | CI 门禁 `--check` |
| `apps/adapter/scripts/check_error_catalog.py` | 错误码三处一致门禁 | `--strict`，CI 门禁 G-13 |
| `apps/adapter/scripts/reconcile_usage.py` | 计量对账（运维） | `--period YYYY-MM --threshold 0.01` |
| `apps/adapter/qa_probe/bootstrap_dify.py` | Dify 引导（13 项自检） | 需 `--deepseek-key`；市场不可达时 `--pkg` 离线安装 |
| `apps/adapter/qa_probe/integration_real.py` | 适配层↔底座真实联调（阶段一手工探针） | 需全栈 Up + `OA_DOWNSTREAM_ENABLED=true` |
| `apps/adapter/qa_probe/mock_llm.py` | OpenAI 兼容 Mock 模型服务 | `uvicorn mock_llm:app --port 8090`，无外网时联调用 |
| `apps/adapter/tests/integration/selftest_plugin_contract.py` | 插件契约自检（**故意不被 pytest 收集**） | `OA_P3_SKELETON_SELFTEST=1 pytest tests/integration/selftest_plugin_contract.py -q`；含 1 条故意红，exit 1 属预期 |

> 说明：`qa_probe/` 三脚本是**阶段一手工探针**，不参与 CI。保留原因是私有化现场排障仍要用；
> 若后续被自动化取代，请连同本节登记一并删除。

---

## 六、文档层级与冲突裁决

文档冲突不再「看谁写得早」或「看谁嗓门大」，按**层级**裁决。层级由文档的**性质**决定，不由文件名决定。

| 层 | 文档 | 谁改 | 冲突时 |
| --- | --- | --- | --- |
| **宪法** | `moy-project-charter-v1.0.md` | 创始人 | **最高** |
| **战略** | `strategy-2026-11-04-company.md` | 创始人 + PM | 服从宪法 |
| **规格** | `spec-base-v1.0.md` | 架构师 + 创始人签 | 服从战略；**但**与战略在「交付形态 / 非目标集合 / 141 人日总量」上冲突时，以规格为准并触发战略复审 |
| **执行** | `plan-base-*.md`、`design-*.md`、`architecture-*.md`、`prd-*.md` | 架构师 | 服从规格 |
| **记录** | `audit-*` / `phase-*` / `qa-*` / `verify-*` | 任何人可写 | 不改结论，只加日期 |

**裁决顺序（命中即止）**

1. 宪法 ↔ 任何层：以宪法为准。宪法自身与新战略冲突的条款，**原文不改**，只标「待修订（依赖 D1）」，替换原文见战略 §2.3。
2. 规格 ↔ 战略：以规格为准，并触发战略复审。**触发条件只有两条**：① 冲突涉及交付形态或非目标集合；② 冲突会导致 141 人日总量变化。复审由创始人 7 日内裁定，裁定前按规格执行。
3. 规格 ↔ 执行：以规格为准；执行层 24 小时内改，改不完则作废重写。
4. 执行 ↔ 记录：记录层不改结论。记录层与执行层冲突时，记录有效（记录的是当时发生的事）。
5. 同层两份文档冲突：以文件头「生效日期」较晚者为准；日期相同视为缺陷，交架构师当日裁定并同步双方。

**三条纪律**

- 新增文档必须在本索引登记（否则成为无入链孤立文档），并标层级与状态（草案 / 冻结候选 / 生效 / 快照）。
- 「快照」类文档（验收报告、审计报告）**不得被引用为现值**；要引用结论，先找到支撑它的现行文档。
- 规格的 major 变更（增删非目标、改裁决规则）须重跑 `spec-base-v1.0.md` §8 四条验收；minor 变更（术语、验收判据）须做一次全文同义替换检查。
