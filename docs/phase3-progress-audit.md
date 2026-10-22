# 阶段三进度审计（B28 第 9 项）

> **文档性质**：批次进度与门禁实跑的对照记录，供验收复核与阶段四接手。
> **与验收报告的分工**：验收报告回答"能不能放行"，本文回答"每一批到底交了什么、跑了什么、还差什么"。
> **基线**：`develop` @ `4923e1f`（收口进行中，最终基线见 §6）

---

## 1. 批次进度（14 批，对照 `docs/architecture-phase3.md` §9.1）

| 批次 | 名称 | 冻结提交 | 状态 | 备注 |
| --- | --- | --- | --- | --- |
| B16 | 插件声明模型、加载器、6 份声明与 G-26 | `046c432` | ✅ | 8 层校验 + `--self-test` 负样本可红 |
| B17 | 数据模型与市场/安装（PL1-PL5, PL13） | `010db32` | ✅ | 9 表 + RLS + `0003` 迁移 |
| B18 | 生命周期、设置、授权变更（PL6-PL12 + PL14）与评价 | `79f868c` | ✅ | 含 `ed3bb09` 收尾、`c340e70` visibility |
| B19 | 任务引擎、A 域扩展、WF1/WF2 | `b6effcf` | ✅ | 含 B19c `ae69a4a` |
| B20 | 三插件 6 步装配与降级矩阵 | `5aa20ff` | ✅ | docx 渲染（D-20） |
| B21 | 端点基线与门禁扩展 | `81bc499` | ✅ | `phase3=16/16`；含 B21c 四个 PG 用例提交 |
| B22 | 前端请求层与基础组件 | `41310ba` | ✅ | — |
| B23 | 市场/详情/授权抽屉/评价 | `96cb8fd` `66b98f3` | ✅ | 16 端点契约补充 |
| B24 | 工作台单宿主 | `075b529` | ✅ | G-27 宿主唯一 |
| B25 | 任务列表与详情、WF2 产物编辑 | `68e8e19` | ✅ | — |
| **B26** | **端到端装配、演示数据与 e2e** | — | **⚠ 补交付中** | 收口阶段才启动，见 §3 |
| B27 | G-27/G-28/G-30 门禁与「可红」证明 | `53d0292` `e5e6b97` `24decb5` | ✅ | — |
| B28 | 覆盖率、门禁扩写与全量回归 | 分散在 QA 提交 | ⚠ 部分 | 覆盖率达成，但**10 个文件中 4 个未按名单交付**，见 §3 |
| B29 | 验收材料与文档同步 | 本批 | ⚠ 部分 | 验收报告已有；**6 个文件中 1 个（P0-P3-05 插件开发文档）收口期才补** |

**结论**：14 批中 **11 批完整交付、3 批（B26/B28/B29）存在缺项**。缺项都不是"写了但没写好"，而是**整份文件/整批产物没出现**。

---

## 2. 门禁实跑（`4923e1f`，本机单次）

| 门禁 | 命令 | 实测 | 判 |
| --- | --- | --- | --- |
| G-01/02 | `ruff check .` / `ruff format --check .` | All checks passed / 已格式化 | ✅ |
| G-03 | `mypy app` | 117 source files，0 error | ✅ |
| G-06 | `python deploy/validate_openapi.py` | `core=30 phase2=38/38 phase3=16/16`，G1-G5 全过 | ✅ |
| G-11 | `python deploy/validate_migrations.py --run-pg` | EXIT 0（空库 apply×2 + 已初始化 check+apply） | ✅ |
| G-13 | `python apps/adapter/scripts/check_error_catalog.py --strict` | EXIT 0；`[RESERVED] 12004` 恰 1 条；WARN 清单见 §4 | ✅ |
| G-26/28 | `python deploy/validate_plugins.py` / `--self-test` | 主检 0 / 自检 1（负样本全拦） | ✅ |
| G-29 | 端点基线严格模式 | 同上，无 `--allow-missing-phase3` 也能过 | ✅ |
| G-31 | `python deploy/validate_schema_retention.py` | **收口期新增**，见 §3 | ⏳ |
| 台账 ⑯ | `OA_BATCH=B21 pytest tests/integration/test_plugin_contract_skeleton.py` | 12 passed（open 缺陷均在期内） | ✅ |
| 前端 6 道 | `node apps/web/scripts/check-*.mjs` | 禁词/占位词/三态/契约/能力一致性/宿主唯一 全 0 命中 | ✅ |

---

## 3. 缺项清单（对照架构文档逐文件核）

### 3.1 收口阶段正在补（不计入遗留）

| 路径 | 归属 | 状态 |
| --- | --- | --- |
| `apps/adapter/tests/e2e/`（conftest + 各流程用例） | B26 | 补交付中 |
| `scripts/demo_phase3.sh`、`docs/phase3-demo-script.md` | B26 | 补交付中 |
| `deploy/validate_schema_retention.py`（G-31） | B28 第 7 项 | 补交付中 |
| `docs/plugin-dev-guide.md`（P0-P3-05） | B29 第 4 项 | ✅ 已交付（`2eca02c`） |

### 3.2 未按名单交付、判据已由其他载体承担（建议下阶段"改文档登记合并"而非补文件）

| 架构文档列的路径 | 实际承载 | 说明 |
| --- | --- | --- |
| `tests/integration/test_rls_phase3.py` | `tests/integration/pg_fixture.py` + `test_migrate_phase3.py` | 9 表 fail-closed 已验，但**没有按"逐表一条断言"的独立文件落地** |
| `tests/integration/test_task_errors.py` | `test_plugin_contract_skeleton.py`（G-13）+ `tests/unit/test_errors.py` | 17001/17002 覆盖有，A3/A4 响应体不含 17002 的断言在骨架里 |
| `tests/integration/test_plugin_errors.py` | 同上 | 插件域码集合相等断言在骨架 G-13 ③ |
| `tests/integration/test_plugin_reviews.py` | `tests/unit/test_plugin_lifecycle.py` + `test_plugin_assembly.py` | 评价读写与汇总有覆盖 |
| `tests/integration/test_degradation_matrix.py` | `tests/unit/test_plugin_guardrails.py` | 降级四格有覆盖 |
| `tests/integration/test_three_plugins_e2e.py` | **无** | 三插件端到端此前**只有装配级单测，无跨插件 e2e**——这正是 B26 缺失造成的空洞 |
| `deploy/validate_capability_usage.py`（G-30） | `deploy/validate_plugins.py` + 前端 `check-capability-consistency.mjs` | 判据有，脚本未按名单建 |
| `deploy/validate_plugin_actions.py` | `deploy/validate_plugins.py` | 同上 |
| `apps/web/docs/qa-manual-checklist-phase3.md` | **无** | 人工走查清单未落文档 |
| `apps/web/tests/api-client.plugin.spec.ts` | `apps/web/scripts/check-plugin-contract.mjs` | 契约快照替代单测 |

> **判据**：**"判据有承载"不等于"按名单交付"**。上表前 5 行属前者——判据真在跑，只是文件形态与架构文档不同；
> 但 `test_three_plugins_e2e.py` 与人工走查清单属**真的没有**，不能靠"判据有承载"解释。

---

## 4. 错误码收口进度（收口期新增工作）

5 个"注册但从未被端点引用"的码，处置规格由架构侧定稿，实施分四步：

| 码 | 处置 | 状态 |
| --- | --- | --- |
| `12004 TASK_TIMEOUT` | 预留（走状态机，不接线） | ✅ `a7fec29`，G-13 打印 `[RESERVED]` |
| `12002 TASK_EXECUTION_FAILED` | 注销（与 `17002` 语义重复，无干净接线点） | ✅ S2（架构文档先删行 + 注销记录表，再删枚举/注册项/前端键） |
| `12005 TASK_TOOL_NOT_GRANTED` | 注销（`13002` 管连接、`16005` 管授权，无第三语义） | ✅ S2（同上；`BUSINESS_ERROR_SEGMENTS.task` 改 `[12001, 12004]`） |
| `11005 RETRIEVAL_FAILED` | 接线（R7 检索下游故障，`503+30001/30002` → `200+11005`） | ✅ S3（只包 `search()` 内那一次 retrieve 下发；判权/凭据在 try 外） |
| `15006 QUOTA_EXHAUSTED` | 接线为**非阻断提示字段**（`data.notice_code`，`code` 恒 0） | ✅ S4（`UsageService.quota_notice()` 单一真源 + 4 个响应加性字段 + OpenAPI 快照） |

### 4.1 收口过程中翻出的两个真缺陷（已入台账）

| 编号 | 级别 | 内容 | 证据 |
| --- | --- | --- | --- |
| `P3-B22-01` | P1 | **PRD 5.3「额度用尽软限制」四项行为完全未实现**（对话限长 / 文档暂停 / 自动化排队不启动 / 管理员通知） | `over_quota_warning` 仅被计费域 `billing.py:412`、`subscription_service.py:484/628` 消费；M1/M2/R3/A1 四条路径**零接入**。超额租户可无限跑文档与自动化 |
| `P3-B22-02` | P2 | **任务级超时无强制，且有一个死配置** | `config.py:211 plugin_task_timeout_seconds`（默认 900）全文**零读取**；`engine.py:960` 写入的 `params["timeout_seconds"]` **无消费方**；PRD P0-P3-02③ 无实现可测 |

> 两条都在 `tests/expected_xfail.py` 台账登记，`due_batch=B30`，由门禁 ⑯ 在到达该批次时强制变红。

---

## 5. 收口期修复的判据缺陷（自查）

| 缺陷 | 性质 | 修复 |
| --- | --- | --- |
| 台账到期检查用字符串比较（`"B21" < "阶段四"` 恒真） | **永久逃检**：任何非批次号写法都不会过期 | `4923e1f`：加 `_BATCH_RE` 格式校验 + `_batch_num()` 数值比较；非批次号直接判红 |
| 文档引用的 43 个仓库路径不存在 | 引用失真 | `b618a3c`：`architecture-phase3.md` §12 逐条登记（笔误就地改、改名/移目录登记、真缺失移交本文 §3） |
| 验收报告引用两份不存在的文档名 | 引用失真 | `b618a3c`：改为实际存在的 `qa-phase3-b20-report.md` 与 `prd-phase3-traceability.md` |

**共性**：三条都不是"写错了字"，是**判据或引用指向了一个不存在/不可判的对象，而系统仍然报绿**。
这与阶段三反复出现的 `PG 建任务 100% 失败却全绿`、`lookaround 静默失效` 是同一族——
**"看起来在检查"比"没有检查"更危险，因为它会让人停止怀疑。**

---

## 6. 最终基线

本文件随收口更新。最终收口基线以 `docs/phase3-acceptance-report.md` 的"代码基线"字段为准。
