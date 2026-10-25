# 麦子（Moy/OfficeAI）全仓库审计汇总报告（团队三线）

- **日期**：2026-10-25
- **审计人**：齐活林（主理人，汇编）· 高见远（架构）· 寇豆码（工程质量）· 严过关（测试体系）
- **范围**：`apps/adapter`（150 py 源文件）+ `apps/web`（91 tsx + 33 ts）+ `deploy/` + `sql/` + `.github/workflows/`
- **方式**：只读审计（AST 静态分析 / 定向 grep 实证 / 工具链实跑 / 覆盖率实测），未修改任何仓库文件
- **基线**：main @ `2bfe6a8`，工作树干净
- **分级口径**：P0 致命（红线违反/资金与数据风险/门禁失效）· P1 高（明确缺陷与结构违规）· P2 中（质量/性能/一致性隐患）· P3 低（规范与建议）

---

## 一、总体评估

**工程质量显著高于常规水平，顶层架构红线基本守住，主要风险集中在「适配层内部结构」「真实模式未完成链路」「测试口径与门禁有效性」三处。**

守住的红线（均有实证）：`apps/web` 底座禁词零真实命中；`app/` 内 httpx 仅存在于 `clients/`；底座 URL/路径/事件名全部收敛在 `clients/dify_client.py` + `services/`；96 端点基线与代码逐条吻合；ruff/mypy/tsc 实测全绿（150 源文件）；六层租户隔离；SQL 注入面为零；无硬编码密钥；验签常量时间比较；脱敏单一真源；工具数据驱动名副其实；1954 个测试函数零重复正文。

核心短板：①文档宣称的 `api → services → business → db` 单向依赖链实际是**网状**（3 组包级双向依赖 + 3 组逻辑环 + 6 处 api 直连 db + business→api/schemas 反向边）；②「双后端镜像」仓储导致 8 个 1300+ 行上帝文件与约 8000–10000 行结构样板；③插件引擎把场景合规规则烧进核心，热插拔名不副实；④**1 个已证实失效的 CI 门禁**（P0）；⑤真实下游模式存在「演示全绿、上线即断」缺口（RAG 上传）。

**发现统计**：P0 × 1 · P1 × 13 · P2 × 22 · P3 × 20+（P3 按摘要列出）。

---

## 二、P0（致命，1 项）

### P0-1 CI「工具注册表 ↔ 连接器」一致性门禁已失效，当前 HEAD 上恒红

> **状态（2026-10-25）：已修复并经 QA 两轮独立复验闭环**——commit `f210432`（分支 `hotfix/p0-1-registry-validation-gate`，本地未推送）。修复内容：① CI 脚本改动态导入 `_CONNECTOR_MODULES`（单一真源）；② 修正 `pip install -e apps/adapter`（Round 1 QA 发现仓库根安装必失败的连带缺陷）与注释事实陈述；③ pytest 删除「连接器缺失容忍」并新增存在性断言。实证：CI 口径（仓库根 + `bash -eo pipefail` + 干净 venv）正向 exit 0（5 工具全 OK）、负样本 exit 1（3 FAIL 精准报出）；全量回归 2056 passed / 203 skipped / 0 failed。剩余外部卡点为 Actions 额度本身（见完整性清单 B-01）。
- **位置**：`.github/workflows/ci-adapter.yml:393-425`（`registry-validation` job Python 内联片段）
- **证据**：本地复现 CI 原文 exit 1：`[FAIL] 注册条目 wecom/feishu/wechat_mp 的连接器未实现`。根因：片段硬编码只 import `email_connector` 与 `github_connector`（ci-adapter.yml:401-402），而 `tool_registry.yaml` 已登记 5 个连接器，`app/tools/__init__.py` 不做自动导入。
- **加重情节**：pytest 侧对应测试 `tests/integration/test_tool_registry_mode.py:64-69` 对「连接器未实现」取容忍态度（`connector_cls is None → 按 DEMO`）→「注册表登记了但连接器不存在」这个属性**在任何会绿的门禁里都没有被断言**。因 GitHub Actions 额度用尽本轮未跑，故无人发现；**额度恢复第一天即阻塞所有 PR**。
- **影响**：工具注册表路径（用户点名关注项）实际无有效守护；该 job 现处于「必须绕过或删注册项才能绿」的状态，诱导破坏性修复。
- **修复**：① CI 片段改为读 `app/services/tool_service.py:_CONNECTOR_MODULES`（或 importlib 扫 `app/tools/*_connector.py`）全量导入后再比对；② 删除 pytest 侧 None 容忍，断言 `ConnectorRegistry.all()` ⊇ 全部 `connector_impl`。

---

## 三、P1（高，13 项）

### P1-1 RAG 真实模式上传链路断裂（文件字节被丢弃）【工程】

> **状态（2026-10-26）：已修复并经 QA 独立全量复验闭环**——commit `52032e5`（合并 `f3dac84`，已推送）。修复：`DifyClient.post_multipart()`（httpx files/form_data，复用鉴权/超时/重试/ErrorMapper；multipart 时不写 Content-Type）；`upload_document(content=)` 透传字节与推断的 content_type，真实模式漏传字节抛 30003 且下游零调用；端点接通 payload；mock 路径零变化。验证：全量 2066 passed / 203 skipped / 0 failed；回滚旧逻辑后 5 条用例失败（行为级证伪）。QA 提示：真实 Dify 联调仍未做（需底座环境 + 密钥，见 B-02/B-03）。
- `services/rag_service.py:359-361`：真实模式下仅向后端 POST `{"filename": ..., "purpose": "document"}`；`clients/dify_client.py:196-204` 的 `_request` 只支持 `json=` 请求体，**全仓无 multipart/文件流能力**；端点读取的文件内容（`api/v1/rag.py:167` 的 `payload`）未传进 service，被丢弃。
- 影响：`downstream_enabled=true` 时 RAG 文档上传必然失败/产生空记录；默认 `mock_mode=True` 掩盖，本机测试全绿 →「上线即爆」。
- 修复：`DifyClient` 增加 `post_multipart()`；`upload_document` 透传字节；补非 mock 路径集成测试。

### P1-2 任务状态机取消竞态（取消可被完成态覆盖）【工程】

> **状态（2026-10-26）：已修复并经 QA 独立全量复验闭环**——commit `52032e5`（合并 `f3dac84`，已推送）。修复：`update_task(expected_status=)` 双后端同步（PG 单条条件 UPDATE + RETURNING，无 TOCTOU）；`_advance_locked`/`_finish` 条件写；`_finish` 校验 `cancel_requested`（completed 降级 cancelled、failed 不降级）；`_release_slot` 仅状态转换所有者执行一次；`cancel()` 入推进锁、条件写失败如实 12003。新增 `TestCancelRace` 4 条并发用例，回滚旧逻辑后 4 failed（取消被覆盖×2、双拉起、缺参数）。
- `business/task_service.py:988-1017`：`cancel()` 不持 `task_advance_lock` 直接写 `status=TASK_CANCELLED`；`db/repositories/task_repo.py:863-910` 的 `update_task` 无 `AND status = :expected` 乐观条件。
- 竞态序列：advance 通过取消检查点跑最后一步 → cancel 置 CANCELLED → advance `_finish(TASK_COMPLETED)` 覆盖状态，用户取消静默丢失；`_release_slot` 存在双重调用风险。
- 修复：`update_task` 增加 `expected_status` 条件；cancel 纳入推进锁或 `_finish` 写终态前校验 `cancel_requested`。

### P1-3 business ↔ services 包级双向模块级依赖【架构】
- `business/memory_service.py:60,65`（→services 模块级）↔ `services/model_service.py:24-36`、`services/rag_service.py:33-38`、`services/agent_service.py:22`（→business 模块级），违反 `architecture-phase2.md:205` 铁律「business 不得反向依赖 services」。
- 修复：`memory_embedding`/`memory_extractor` 下沉为纯能力模块，services 只留协议转换薄层。

### P1-4 db → business 反向依赖【架构】
- `db/repositories/audit_repo.py:28` 模块级 `from app.business.audit_service import ACTION_*`；`role_repo.py:313` 延迟导入 `BUILTIN_ROLE_*`。
- 影响：repositories 不再是纯 SQL 层；与 D-02「business 可整体搬迁」目标冲突（搬走 business，db 立刻 ImportError）。
- 修复：审计动作常量、内置角色定义下沉 `core/` 或 db 种子数据。

### P1-5 api → db 直连 6 处，绕过 business/services【架构】
- `api/v1/artifact_files.py:24`、`conversation.py:27`、`identity_ext.py:40`、`memory.py:29`、`stats.py:45`、`router.py:54`。接口层禁止「任何 SQL」（architecture-phase2.md:154）；租户隔离/审计在这些路径靠各自自觉。
- 修复：stats/artifact_files/conversation 改经 business 门面；router.py 探针可接受或移 core。

### P1-6 business → api 反向依赖（组合根错位，两轮历史审计建议未落地）【架构】
- `business/task_service.py:188`：`from app.api.v1.plugin_deps import build_action_gateway`（延迟导入）。
- 修复：`plugin_deps` 移至 `app/plugins/deps.py` 或新增 `app/bootstrap.py`。

### P1-7 schemas → business 反向依赖【架构】
- `schemas/tenant_data.py:26`：`from app.business.tenant_data_service import EXPORT_SCOPES`。契约层（最应稳定）反向依赖业务层。
- 修复：`EXPORT_SCOPES` 移入 schemas 或 core。

### P1-8 仓储「双后端镜像」是上帝文件的系统性根因【架构+工程】
- 8 个 1300+ 行文件：`billing_repo.py`（2272）、`task_repo.py`（1549）、`plugin_repo.py`（1529）、`memory_repo.py`（1315）、`conversation_repo.py`（1294）、`org_repo.py`（1157）、`role_repo.py`（942）、`usage_repo.py`（915）。
- 量化：InMemory 侧 177 个 async 方法与 PG 侧 181 个同名方法签名逐条一致；约 8000–10000 行结构样板；新增字段需三处同步，漏改即「内存有、PG 无」静默不一致——而 PG 用例本机 skip 203 条，这类漂移本机永远暴露不出来。
- 修复：按 records/内存/PG 拆三文件；「三处同步」从口头铁律变为 CI 断言（协议方法集 ↔ 双实现方法集）。

### P1-9 task_service.py 1622 行 / 34 方法，职责混杂【架构】
- 任务创建、推进循环、确认暂停、产物落库、重试/前序重放、取消、列表/详情投影、核查清单、版本史、下载 URL、Manifest 解析、鉴权判定全集于一身。
- 修复：拆 `TaskRunner` / `TaskQuery` / `TaskManifestResolver`。

### P1-10 插件引擎把场景合规规则烧进核心，热插拔名不副实【架构】
- `plugins/engine.py:785 _guard_offer`、`:819 _guard_schedule`、`:845 _guard_screening`；产物键写死 `OFFER_OUTPUT_KEY`(91)、`SCHEDULE_OUTPUT_KEY`(135)、`SCREENING_OUTPUT_KEY`(165)；markdown 表格解析 1352-1407。
- 影响：新场景插件需要新合规规则时必须改引擎核心——「新增插件只加 manifests/」对合规类插件不成立。
- 修复：合规规则声明化（manifest `guards` 段）+ 规则注册表，引擎只跑已注册 guard。

### P1-11 行覆盖门槛余量仅 0.52pp（约 93 条语句）【QA】
- `ci-adapter.yml:291-299` `--cov-fail-under=85`，实测 85.52%（15468/17729）。任何新增端点/服务不同步带测试 CI 即红，压力诱发「装饰测试」或临时放宽参数。
- 修复：引入 diff-cover / 浮动门槛，把「总体不退」与「增量必须覆盖」解耦。

### P1-12 最弱模块恰是持久化红线路径，且本机永远测不到【QA】
- 无 PG 实测覆盖：`api/v1/memory.py` 34.38%、`api/v1/tenant_data.py` 38.00%、`db/repositories/audit_repo.py` 51.09%、`task_repo.py` 53.64%、`billing_repo.py` 65.17%、`plugin_repo.py` 67.80%、`grant_repo.py` 68.18%、`ops_repo.py` 68.42%。
- 多租户隔离、计费、幂等、审计不可篡改的**真实库行为**（RLS/WITH CHECK/advisory lock）只存在于 186 条 PG 标记用例，本机与（额度耗尽的）本轮 CI 均未执行；85.52% 的数字易被误读为这些路径已验证。
- 修复：恢复 PG 口径复跑；中期提供 testcontainers/一次性 PG 脚本。

### P1-13 任务级超时是死配置，PRD 承诺的能力不存在【QA】
- `expected_xfail.py:141-156`：`core/config.py:211 plugin_task_timeout_seconds` 全文零读取、`engine.py:960` 写入后无消费方；PRD P0-P3-02③「单步超时自动失败」无实现可测（缺陷台账 P3-B22-02 open，due B30）。同类 P3-B22-01（超额软限制四项行为只接计费域）亦 open。
- 修复：提升为迭代看板可见项；或按台账注销配置并在 PRD 标注不做。

---

> **2026-10-26 第 1 波闭环**：P2-1（ops 三个写端点接入 Idempotency-Key，资金操作不再裸奔）、P2-2（幂等/限流/锁表/桩任务四处内存上限与淘汰）、P2-3（in-flight 重放改 20005，不再返回 data=null 冒充成功）、P2-16（17 条暗跳用例补 pg 标记 + `deploy/validate_pg_markers.py` 静态门禁接入 CI）、P2-22（限流隔离断言强化）已修复并推送（`b3a9d7c`）。实证：`-m pg` 收集 186→203，与无库跳过数 203 一致；全量 2075 passed / 203 skipped / 0 failed；ruff/format/mypy 全绿。

> **2026-10-26 第 2 波切片 1 闭环**（`384870c`）：**P1-4**（db→business 归零：审计词表 `core/audit_actions.py`、内置角色 `core/builtin_roles.py`、记录器 `db/repositories/audit_recorder.py`）、**P1-6**（business→api 归零：`TaskService` 改 `gateway_factory` 组合根注入）、**P1-7**（schemas→business 归零：`EXPORT_SCOPES` 下沉 core）、**P2-4**（排期判定 UTC aware，`datetime.now()` 全仓归零）、**P2-5**（zip 500 行分块 `to_thread`）、**P2-6**（插件目录/占用计数批量查询，21→2 次）、**P2-12**（qa_probe 豁免登记）已闭环。新增 **G-37 依赖方向门禁**：层对 12×12 矩阵、module/deferred/type_checking 三分类、基线只许减不许增、fail-closed（app-root 异常/文件数<100 → exit 2）、12 类正负自检；基线 **28→24**（`services→db` 6 条与 `core→schemas` 1 条经门禁判定入基线，编号 P2-10/P1-7）。全量 **2083 passed / 207 skipped / 0 failed**；QA 独立验证 IS_PASS: YES。

### 追加登记（2026-10-26 第 0 波复验，QA 发现，不阻塞第 0 波放行）

- **R-1（P2）** `_pause_for_confirm()` 与 `retry(continue/redo)` 的 `status=` 写仍为无条件写：极窄窗口（取消落在 `cancel_requested` 检查之后、写之前）可把已 `cancelled` 翻回 `waiting_confirm`；可自愈（`cancel_requested` 仍在行上，再点取消即收敛）。建议下一波统一补 `expected_status`。
- **R-2（P3）** `cancel()` 在 peer 已写终态时返回 12003，但对客 message 仍是「任务已取消」，与真实状态（completed）不符；随 P3 文案批次评估。
- **R-3（归入 B-06）** PG 真实库未验证：203 条 PG 用例 skip；`ANY(:expected_status)` 的 list→`text[]` 编码、并发行锁与 `RETURNING` 空行路径需预发 `--run-pg` 验证（含 `test_task_repo_pg.py` 28 条）。
- **R-4（P3）** 0 字节文件先被 `_validate_document` 拦为 11002「暂不支持该格式」（既有行为，本次未改）；如需更准确文案另立。
- **R-5（P3）** 架构文档 R3 仅记 `POST /v1/files/upload`，未记 multipart 文件字段 + `purpose`/`user` 文本字段；建议架构侧补一行，避免被改回 JSON。

---

## 四、P2（中，22 项，按域分组）

### 工程质量（寇豆码）
| # | 问题 | 位置 |
|---|---|---|
| P2-1 | ops 域 3 个写端点无幂等键；前端对所有写请求照发 `Idempotency-Key`（api-client.ts:294-297）形成「伪保护」；`confirm_order` 为资金操作 | `api/v1/ops.py:159/241/351` |
| P2-2 | 进程内字典无界增长（内存泄漏）：`_local_locks`、幂等 `_entries`、限流窗口、agent `_steps` 均只惰性清理，从未被再访问的键永久驻留 | `db/advisory_lock.py:41`、`core/idempotency.py:127`、`core/ratelimit.py`、`services/agent_service.py` |
| P2-3 | 幂等重放 in-flight 请求返回空 data（`code=20007, data=null`），与 `_Entry` docstring 承诺的 20005 不符 | `core/idempotency.py:439-442`、`:130-148` |
| P2-4 | `engine.py:1230` `datetime.now()` naive 本地时间参与排期判定（容器 UTC vs 用户 UTC+8，偏移 8 小时）；全仓唯一例外 | `plugins/engine.py:1230` |
| P2-5 | zip 导出在异步路径同步执行（20 万行级）阻塞事件循环，且全量驻留 BytesIO | `business/tenant_data_service.py:671-700` |
| P2-6 | 已安装插件列表 N+1：每页 20 条 → 21 次查询；市场列表全量加载目录再内存分页 | `business/plugin_service.py:417-418`、`:237-262` |
| P2-7 | SSE 任务事件为进程内 dict 桩，多副本订阅者收空流 | `services/agent_service.py:667-685` |
| P2-8 | 幂等/限流默认内存后端，多副本部署按实例分裂（文档已声明 T02/T04 切 redis，需部署清单显式化） | `core/idempotency.py:114-118`、`core/ratelimit.py` |

### 架构与依赖（高见远）
| # | 问题 | 位置 |
|---|---|---|
| P2-9 | core 被底层穿透：`security.py` 延迟导入 business/db（4 处），OPS_ROLE_KEYS 常量被迫在 core 写字面量并配测试同步——循环代价已实体化 | `core/security.py:283/353/358/815/485-486` |
| P2-10 | services → db.repositories 直连，绕过 business（计量/审计易漏） | `services/memory_extractor.py:39`、`model_service.py:48`、`tool_service.py:33`、`identity_service.py:415` |
| P2-11 | 3 组逻辑环全靠延迟导入拆解：`policy_service ↔ security ↔ role_repo`；`order_service ↔ subscription_service`；`job_runner ↔ tasks ↔ retention_cleanup`；无 import 方向门禁 | 全仓 AST SCC 检测 |
| P2-12 | `qa_probe/bootstrap_dify.py` 用 urllib 直连底座（含 multipart），字面违反「dify_client.py 唯一出口」红线 | `qa_probe/bootstrap_dify.py:34-43/86` |
| P2-13 | `goal_router.py:47-59` 关键词表硬编码 6 插件 + 5 工具 id，新插件不加表就落兜底 | `services/goal_router.py:47-59` |
| P2-14 | `PHASE3_DOMAIN_ENDPOINTS = 14` 与实际 16 不符，且被两处注释引用称「冻结 16 条」自相矛盾（埋雷） | `api/v1/router.py:74` |

### 测试与门禁（严过关）
| # | 问题 | 位置 |
|---|---|---|
| P2-15 | `_reset_all()` 人工登记制（60+ reset 调用），遗漏即顺序依赖——历史上已发生 3 次（B08/T4-03.3/job runner） | `tests/conftest.py:175-256` |
| P2-16 | 17 条 PG 用例无 `pg` 标记「暗跳」（186 标记 vs 203 跳过，差 17）；改进计划 B-13 原定断言未实施 | `test_conversation_rls_pg.py` 等 14 处已定位 |
| P2-17 | 巨型测试文件：`tests/e2e/conftest.py` 1860 行；`test_qa_b07/b09_verification.py` 合计 3000+ 行（批次验证脚本混入回归目录） | `tests/e2e/` |
| P2-18 | 前端 React 组件层 0 测试覆盖（vitest 55 条只测纯函数；Playwright 2 条冒烟不接后端；三态门禁 G-18 仅是静态扫描） | `apps/web` |
| P2-19 | `dependency-manifest` job 只 WARN 不 FAIL：新增白名单外依赖可无声跳过协议审核 | `.github/workflows/dependency-scan.yml:378-434` |
| P2-20 | ci-web 触发路径不含禁词词表真源（只改 PRD 术语表的 PR 不触发扫描） | `.github/workflows/ci-web.yml:12-19` |
| P2-21 | PG 门禁「有机制、无证据」：89.81% 历史基线 1573 条 vs 现 2258 条套件，严重过期；本轮 CI 未跑 | `.github/workflows/ci-adapter.yml:62-88` |
| P2-22 | 22 个无断言用例中 2 个实为弱断言：限流租户隔离只调不断言，隔离失效也绿 | `tests/unit/test_ratelimit.py:92/108` |

---

## 五、P3 摘要（20+ 项，不逐条展开）

- **重复**：`now_iso()` 6 处重复定义（artifact/billing/conversation/job/plugin/task repo）；`_with_quota_notice` 两处复制（task_service.py:351 / rag_service.py:403）
- **一致性**：前端错误兜底表 3 处文案与后端注册表漂移（10003/13001/18003，business-errors.ts:34/54/109）；README 目录树过期（migrations 实为 0002-0011 十个，README 称 0001-0004）；中间件顺序文档漂移（漏 Metrics、Trace/AccessLog 相对顺序与实际执行序相反）；adapter README「≥80%」与 CI 实际 85% 不一致
- **安全加固**：CORS 非 development 全禁 + nginx 缺 HSTS/CSP/X-Frame-Options（main.py:381-389、deploy/nginx/lite.conf）；下载令牌与 JWT 混用同一密钥（core/artifact_token.py:82-87）；Redis 幂等键设计与 docstring 不符且客户端可控字符串原样入 key（idempotency.py:215/241）；令牌存 localStorage（已知接受风险）；SSE 错误透传原始 "Failed to fetch"（api-client.ts:643-646）；下游 400 一律映射成模型域文案（errors.py:610-623）；配额检查 TOCTOU（usage_service.py:611-657，有界可接受）
- **性能/工程**：model_service.py 1585 行七种职责待拆；types.ts 1747 行可按域拆；e2e conftest 拆分；SCA 不含 devDependencies；license-scan 用 `npm install` 应改 `npm ci`；prettier 现拉现用与 devDeps 漂移；陈旧产物入库（coverage.xml 86.35%/73.84%、pytest_run.log 1867 passed，均与现行不符易误引）；dependency-scan push 触发路径缺 feature/**

---

## 六、覆盖率口径核查（严过关实测，重要）

| README 声称 | 实测（本机，无 PG，OA_BATCH=B21） | 判定 |
|---|---|---|
| 2030 passed / 203 skipped / 0 failed | **2055 passed / 203 skipped / 0 failed**（2258 collected） | passed 少 25，文档滞后一个批次 |
| 合并覆盖率 85.51% | **85.52%**（15468/17729） | ✅ 可信 |
| 分支 2176/2840=76.6%「余量为 0」 | **2827/3664=77.16%**，超门槛 +20 条 | ❌ 该叙事已过期失真 |
| 全量（含 PG）1573 passed / 89.81% | 本机无 PG，无法复现；1573 ≪ 2258 | ⚠️ 本轮无证据，基线严重过期 |
| adapter README「核心接口 ≥80%」 | CI 实际 `--cov-fail-under=85` | ❌ 文档与门禁不一致 |

**复现口径**：不加 `-k`、不设 PG 环境变量、设 `OA_BATCH=B21`（`-k "not pg"` 会按名称再剔 216 条，得第三套不可比数字）。标记分布：pg 186 / unit 1176 / integration 765 / e2e 24 / security 58 / p0 222。

---

## 七、优先级修复清单（按波次）

### 第 0 波 · 立即（1–2 天，解除「上线即爆/CI 即堵」）
1. **P0-1** 修复 registry-validation 死门禁（动态全量导入连接器 + pytest 删容忍断言）
2. **P1-1** DifyClient 补 multipart，RAG 真实模式上传透传文件字节 + 集成测试
3. **P1-2** 任务取消乐观并发控制（update_task 条件更新 + cancel 入锁）

### 第 1 波 · 本周（恢复质量证据链）
4. GitHub Actions 额度恢复后立即跑通 ci-adapter 全量（`--run-pg`），重测全量口径（外部阻塞，见《项目完整性清单》B-01）
5. **P1-11** 覆盖门槛改 diff-cover/浮动机制，摆脱 0.52pp 擦线
6. **P2-1** ops 三个写端点补幂等（或文档登记豁免）
7. **P2-2** 内存态字典统一加上限 + 淘汰；**P2-3** 幂等重放语义修正（in-flight 返回 20005）
8. **P2-16** 补齐 17 条 pg 标记 + `skipped == pg 标记数` 断言

### 第 2 波 · 两周内（结构治理）
9. **P1-3/4/5/6/7 + P2-9/10/11** 分层治理：引入 import-linter（或自写 AST 门禁）钉死依赖方向；`plugin_deps` 移出 api 层；审计/角色常量下沉 core；权限解析下沉 core
10. **P1-8** 仓储按 records/内存/PG 拆文件 + 双实现方法集 CI 断言
11. **P1-9/P1-10** task_service 三拆；插件合规规则声明化（manifest guards + 注册表）
12. **P2-4/5/6/13** 时区归一；zip 改 to_thread/流式；插件目录批量查询；goal_router 关键词进 manifest
13. **P2-12** qa_probe 底座调用登记豁免或收口 DifyClient

### 第 3 波 · 阶段四内（收尾与防漂移）
14. **P2-18** 前端 10 个业务页补三态组件测试；**P2-15** `_reset_all` 自登记 + 元测试
15. **P2-19/20** dependency-manifest 对新增依赖判红；ci-web paths 补 PRD 词表真源
16. 文档数字单一真源：README/CI 注释/入库产物（coverage.xml、pytest_run.log）三处对齐；PHASE3 常数校准；README 目录树随批次生成
17. P3 批次清理（now_iso 合一、兜底文案对齐、安全头、独立令牌密钥、SCA devDeps、npm ci 等）
18. **P1-13** 任务级超时：按缺陷台账 due B30 处置（实现或注销配置 + PRD 标注）

---

## 八、附：成员报告与证据索引

- 架构报告（高见远）：包级依赖 AST 分析、红线 grep 实证、96 端点核对、文档一致性抽查
- 工程质量报告（寇豆码）：ruff/mypy/tsc 实跑全绿确认；六层租户隔离/验签/脱敏实证；重复代码量化；安全七面扫描；索引与查询覆盖核对
- 测试体系报告（严过关）：2258 collected / 2055 passed / 203 skipped / 0 failed 实测；coverage json 逐模块；门禁脚本全跑（唯一红：registry-validation 内联片段）；CI 配置逐 job 审阅
- 关联历史审计：`docs/audit-2026-10-23-arch-code-review.md`、`docs/audit-2026-10-24-full-repo-audit.md`（-r2）——本报告 P1-4（business→api）与其结论互证，属两轮建议未落地欠账
