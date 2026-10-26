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

> **2026-10-26 第 2 波切片 2A 闭环**（`561ce02`）：**P1-8 部分闭环**——新增 **G-38 仓储双实现一致性门禁**（R1 协议⊆双实现 / R2 双实现方法集相等 / R3 签名兼容 / R4 record 构造形状逐点对齐 / R5 协议未声明只提示；fail-closed、stale、14 组自检；实测 **20 单元 / 3 WARN / 0 FAIL**），并把 `role_repo` 拆为 `role/` 包作为样板（944→67 行纯 shim，19 项 `__all__` 顺序不变，**20 处调用点零改动**）。**口径校正**：审计原文「181 vs 177 / PG 侧多 4 个方法」不成立——实测协议 181 个 async 方法被双实现全覆盖，真实不一致只有 3 处内存侧测试注入缝（**P1-8a**，入基线）、1 处 `RoleRecord.member_count` 构造漂移（**P1-8b**，已修：`count_members`/`get_role`/`list_roles`/`ensure_builtin_roles` 四出口同数）、1 处 `OpsStore.billing` 协议未声明（**P1-8d**，R5 只提示）。R4 v2 另抓到 billing/task 各一处历史形状不一致（已做语义等价对齐）。剩余 7 个仓储拆分与 P1-9 留切片 2C。全量 **2085 passed / 207 skipped / 0 failed**；QA 两轮独立验证（含两条洗白攻击复现）IS_PASS: YES。

> **2026-10-26 第 2 波切片 2B 闭环**（`dca9fda`）：**P1-10**（合规规则声明化）+ **P2-13**（关键词进 manifest）已闭环，合并为一次 manifest schema 变更——「声明进、契约不动」。三个场景护栏（offer 金额形态 / schedule 排期窗口 / screening 评分表风险列）从 engine 硬编码迁至 manifest `guards` 声明表 + `app/plugins/guards/` 规则注册表；loader 构造期强制（未注册 rule / params 非法 / 产受保护键未声明对应 rule 一律拒载）；**硬语义不进 params**（识别不了的时间不判失败、缺风险列整表判失败、关闭项被 `validate_params` 拒绝）。engine.py 1543→895 行，`OFFER_/SCHEDULE_/SCREENING_` 常量归零，**新增合规规则只改 registry+manifest 不改 engine**（热插拔边界成立）。P2-13：`_KEYWORD_TERMS` 47 词（含「写信/代码仓库/合并请求」等同义改写）迁入 6 份 manifest + `tool_registry.yaml` 的 `match_hints`，goal_router 改读目录 + 字段权重兜底。验证：全量 **2101 passed / 207 skipped / 0 failed**（+16 防漂移用例）；合规语义 68 样本新旧逐条等价；AC-B1 19 目标新旧 top1 全 SAME；契约三连绿（`PluginManifest`/`GuardSpec` 零出现在 api/schemas/openapi）。**已知语义缺口（既有，非本切片）**：同义词（如「写信」）在 match_hints 里但打分只认字段文本子串，单独作 goal 时全员 0 分走兜底——新旧行为一致，产品侧若要求命中需把同义词并入 match_texts 或调打分逻辑。QA 独立验证 IS_PASS: YES。

> **2026-10-26 第 2 波切片 2C-1 闭环**（`9b499f5`）：P1-8 第二批——`usage`(915)/`memory`(1315)/`task`(1678) 三个仓储拆为 `{records,protocol,memory,postgres,__init__}` 包 + 纯 re-export shim（79/69/61 行），调用点零改动、新旧路径同对象同单例、`__all__` 三仓储逐字一致（27/15/16 项）。G-38 包形态 4 单元（memory/role/task/usage）/ 3 WARN / 0 FAIL。G-37 基线第 63 行随 `InMemoryMemoryStore.search_similar` 搬迁改路径（同 rule/module/mode/审计编号，仅 file 变；负样本证明改回旧路径立刻 23+stale）。**QA 三项观察与裁决**：① **`row_value` 按零引用死代码删除**（`git grep` 全仓 0 命中、不在原 `__all__`、纯转发 `row.get(key, default)`）——口径定为「旧路径公开面 = 原 `__all__` 全集（27/15/16）逐字不变；非 `__all__` 属性面因此少一项，记为死代码清理，不判缺陷」；`usage/task` 的模块级 `logger` 同为从未调用的死代码，一并删除。② memory 两条 warning 的 structlog logger 名从 `memory_repo` 变为 `memory.postgres`——`get_logger(__name__)` 随类搬迁的必然结果（`role` 拆包已有同样先例），事件名/字段不变、无告警规则按 logger 过滤，按「已知且接受的可观测性差异」登记，不修。③ **R4 覆盖口径变化（三方措辞已对齐）**：拆分前「内存区/PG 区扫同一文件 → 形状必然相等」是**假绿**，拆分后 `[INFO] 跳过` 是**诚实边界、非信号损失**；本轮唯一被机械保住的 record 形状比对是 `TaskStats`（空窗口 `TaskStats()` + 非空满字段两形状），证据为 QA 的 **16 用例 × 9 字段对拍表（144 组全等）**。全量 **2101 passed / 207 skipped / 0 failed**。QA 独立验证 IS_PASS: YES。

> **2026-10-26 第 2 波切片 2C-2 闭环**（`b47856d`）：P1-8 第三批——`plugin`(1633)/`conversation`(1294，**双协议同批**)/`org`(1157) 三仓储拆包（plugin 5 文件+shim 86、conversation 6 文件含 `shared.py` 游标协议+shim 69、org 6 文件含 `directory.py`+shim 55），调用点 17/19/26 零改动，`__all__` 27/22/15 逐字同序。**AST 逐符号对拍证明纯搬迁**：plugin 82/82、conversation 68/68、org 69/69 类与函数逐字未变（差异仅 3 处零引用 `logger` 与 1 处 `_` 占位赋值，按死代码删除）。G-38 **包形态 7** / 3 WARN / 0 FAIL；G-37 仍 **24 条**（3 条 `R-DB-PLG` 随 import 搬移仅改 file 字段、说明列追加位置注记，结构字段与审计编号不变；负样本 21+3 stale 证明必须改且未增删）。**测试改动授权**：`test_conversation_endpoints.py` 4 行（L77/L94 import、L80/L97 白盒注入目标）为**主理人裁决选项 (1)**，口径「断言零改动，允许 import 路径与 patch 目标适配」；同裁决删除 `_compat_store()` 回读桥（包内反向引用旧 shim 属倒置依赖）。**过程纪律**：验证窗口内工作树必须冻结、确需改动先告知 QA、授权依据留 commit message 与审计台账（本轮因 docstring 中性化抹掉痕迹导致 QA 一度无法自证，已纠正）。R4 新增 3 条 `[INFO]` 跳过（plugin `InstallationRecord`、org `DepartmentRecord`/`MemberLink`），机理同 2C-1（拆分前同文件两侧各扫一遍的假绿，拆后诚实边界）。全量 **2101 passed / 207 skipped / 0 failed**。

> **2026-10-26 第 2 波切片 2C-3a 闭环**（`392d46d`）：P1-8 收官——`billing`(2278) 拆包（records 546 / protocol 137 / memory 547 / postgres 1113 / __init__ 176 + shim 120），**8 个 1300+ 行巨型仓储全部拆完**。47 项 `__all__` 逐字同序，调用点 23 站点零改动，`ops_repo` 的 6 符号经 shim 同对象（`ops_repo.BillingStore is billing.BillingStore`）。`PLATFORM_TENANT` 归 records（两侧共用：`default_plans` 的 tenant_id、record 默认值、PG 行过滤），6 张表名仍归 postgres。`default_plans()` 与 `init-schema-phase2.sql` 为**有意镜像**（真源在 SQL、代码不覆盖运营改价），QA 独立解析器 **11 列 × 3 行逐字段相等**。AST **107/107** 纯搬迁。R4：`TenantRecord` 继续机械比对（equal），其余 5 个 record 转 `[INFO]` 跳过（内存侧只有 `replace` 派生），累计 **10 个 record 落跳过面**——QA 建议 2C-4 评估口径升级（把 `replace` 的 kw 键集合纳入形状统计，或做「字段面 vs 构造点覆盖」对账）。全量 **2101 passed / 207 skipped / 0 failed**；G-38 包形态 8 / G-37 24 条且基线零改动。QA 另建议把「SQL 种子 ↔ `default_plans()`」做成常驻单测（约 40 行、不需 PG），已纳入 2C-4。

> **2026-10-26 第 2 波切片 2C-3b 闭环**（`215454a`）：**P1-9** 已闭环——`task_service.py` 1765→**89 行**公开入口（`_service` 物理保留 + 2 单例 + 兼容转出 3 私有函数），职责拆入 `business/task/`：`facade`(340)/`runner`(1227)/`query`(371)/`manifest`(108)/`deps`(67)/`access`(38)/`__init__`(41)，合计 **2281 行**。`TaskDeps` 为**共享可变依赖束**（11 字段，非 frozen），门面 11 属性代理（`_usage`/`_step_timeout_seconds` 读写穿透），组件每次现取、不缓存；锁**恰好 2 处**（`runner.py:341/1017`），`finish→release_slot→advance_record` 嵌套保持同一 Runner 对象。**函数体对拍（互斥口径）**：34 条目 = **28 逐字相同**（含 9 个公开化改名）+ **3 仅 docstring** + **3 设计内重写**（access 抽函数 / `__init__` 装配 / `_build_engine` 加参）→ **0 条真残差**；**34 方法签名**：27 逐字 + 6 改名后逐字 + 1 批准变更（`_build_engine` 加 `settings/sanitizer/gateway_factory`，因 engine 必须先于 deps 存在）。**拆分前基线 = `747280c^`（`070201b`，`task_service.py` 1765 行 / md5 `a079bdd5…`）**；勿用 `547ca04`（2A 闭环，1757 行，会因 P1-10 的两处改动假红 `_advance_locked`/`_run_step_with_timeout`）。`TaskService.__init__` **签名未变**（既有构造点零改动）。改名口径：**6 个真改名 + 4 个新增公开名**（旧私有名仍以薄转调存在）。验证：全量 **2101 passed / 207 skipped / 0 failed**；ruff 368 / mypy 210 / G-37 24-24-0 / G-38 20-8-3-0-0 / G-32 26-26；import 图 9 包全绿（`business/task` 18 边）。**跟随 2C-5**：`_summary_text` 类型收回、`assert_task_visible` 不带 grants 的注释、门面属性代理「后续拆包不要复制」的台账提示。**G-39 原型已交付**（`validate_package_imports_draft.py` 739 行、6 判据、15 夹具、`--root` fail-closed），2C-5 接入 `deploy/` 与 CI。

> **2026-10-26 第 2 波切片 2C-4 闭环**（`39a544a`，8 笔 + 2 笔 docs；合并 `f0d57ca`）：P1-8 **shim 收口完成**——8 个兼容层（usage/memory/task/plugin/conversation/org/role/billing，共 606 物理行/35 可执行语句）全部删除，**158 处调用点**迁至包路径（含 5 处非 `__all__` 名字：memory 3 私有 → `.postgres`、task `STATUS_QUEUED` → `.records`、plugin `_timestamp` → `.postgres`）；基线 6 行 `imported_module` 随包改列（`:33` conversation / `:35`+`:53` memory / `:37` task / `:42` org / `:43` role），两条 `deferred` mode 保持原样。**末态**：8 个 shim 消失；G-37 **24/24/0（扫描 208→200）**；八包口径基线旧路径行 **0**（全仓另有 5 行属 artifact/audit/knowledge_base/tool 未迁移单元，不改）；G-38 **20 单元/包形态 8/3 WARN/0 FAIL**；G-32 26/26；交付清单 119/91；全量 **2101/207/0**；ruff 360；mypy 202。**验收口径**：每笔 11 维（P1 包内 md5 / P2 `__all__` / P2b 被导入名字可用 / P2c 指向正确 / P3 旧路径归零+新路径符号集不缩 / P4 shim 已删 / P5 G-37 三态 / H AST 剥 import 零差异 / T 测试函数集合零变化 / G 41/42 全域 md5 / L 台账 119/91）；**G 的 41/42 唯一例外**是 `usage/memory.py:201` 跨包引用 billing 壳的 1 行 import，由 H 证明仅 import 行变化。**[E1]** 脚本宿主：`scripts/demo_phase3.sh:182` 内嵌 Python 改指包，点号正则扫 `*.sh/*.mk/Makefile` 旧模块引用 **0**，内嵌 import 全部可解析；**[E2]** demo 脚本 `exit 2` 属环境受限（无 PG），`exit 1` 才是缺陷。**过程中固化 15 条报告纪律**（快照绑定、度量口径、can-red、可复跑三件套、模块引用点号正则等），详见完整性清单。

> **2026-10-26 第 2 波切片 2C-5a 闭环**（门禁与测试补强，三项，**生产代码零改动**）：① **SQL 种子 ↔ `default_plans()` 等价单测**（`tests/unit/test_billing_plan_seed_parity.py`，1 用例）：把 `init-schema-phase2.sql` 第 15 节当**数据源**解析——按括号/引号状态机切 `VALUES`（终止于 `ON CONFLICT` 或分号）、跨行 JSONB 去 `::jsonb` 后 `json.loads`，得 **11 列 × 3 行 = 33 字段**逐列对账；另**显式断言** `currency`/`quota_unit_label` 两列不在 SQL 里且仍取 dataclass 默认值（`CNY` / `字数额度`），防"SQL 加列而代码没跟上"；can-red 3 条真红（SQL `3900→3901`／代码 `3_000_000→+1`／SQL 复制第 4 行），反向样本 1 条（`ON CONFLICT` 改 `DO UPDATE` 仍绿，证明不是靠字面量锚点）。② **内存侧 record 往返**（`tests/unit/test_record_roundtrip_memory.py`，**11 条真往返 + 1 条 usage 聚合等价**）：`asdict(读回)` 与写入值逐字段比，派生字段（`created_at`/`installed_at`/`expires_at` 等）必须**逐条给出合法性断言**后回填（当前是**存在性/形态**断言——非空字符串；store 生成的时间戳没有外部基准，因此这是"没丢"而非"值正确"的校验，不应当被读成强校验），禁止"不认识的字段一律跳过"；用例表完整性自检让"少写一个 case"必然变红。**已知缺口显式文档化**：`UsageEventRecord` 是纯输入 DTO（两侧都不构造、无 `_to_*`、读侧只出聚合），本文件对它**不做原行往返**，只断言写入值进了 `event_counts`/`event_units_total`。③ **R6 映射器字段覆盖**（`deploy/validate_repository_parity.py`，WARN-only）：按**返回注解**配对 `_to_*`（不按函数名），取函数体内 `record(...)` 构造点 kwarg **并集**，缺失字段报 `R6 <unit> <record>.<field>`；`--strict-r6` 才计入 exit；`history` 走 `(unit,record,field)` 豁免（**INFO 而非消失**，带 `expire_when`）。**实测口径与预判的三处偏差已更正入账**：(a) 映射器不是 19 个而是**33 个候选**（含 `_row_to_*`/`_coerce_settings`/`query_audit_logs` 等非 `_to_` 前缀函数，只要返回 annotation 是 record 就纳入）→ 修复后 **33 个全部进入配对、32 个有构造点产出判定**（`ops.default_ops_settings` 逐个字段拼默认值、无构造点，按"无可判定"跳过），R6 WARN **0**，唯一发现是 `conversation.MessageRecord.history`（豁免）；(b) `task._to_task_stats` 实测有 **2 个构造点且覆盖全部 9 字段**，"仅零值构造"的预判不成立——零值排除路径改由自检夹具 17 单独证明（只做 `WidgetRecord()` 时全字段都报）；(c) **R6 原先写在 `scan()` 的 `if triples:` 分支内，导致无协议单元 `audit` 的 2 个映射器永远不会被检查**（"看着有判据、实际不执行"的假覆盖，独立复核抓到），已把 R6 移出该分支，并**用注入缺口证明可达**：破坏 `audit_repo._query_postgres` 的 `total=` → 立刻报 `R6 audit AuditQueryResult.total`。**R6 的两条已知边界**：① 取并集意味着"某条分支少写字段"恒绿（分支级覆盖由 R4 的形状集合负责）；② `--strict-r6` 下 R6 WARN 才会判红，默认只提示。**另一处判据修正**：can-red 第③条原表述（"删派生断言 + store 不写 → 红"）字面**恒绿**（两侧都是空串），已拆成方向相反的两条——保留断言/不填充 → 红（派生断言有效）、删登记/填充 → 红（严格比对兜住），都真红。**数值**：收集 **2321**（2308+13）、全量 **2114 passed / 207 skipped / 0 failed**；`ruff format` **360→362**（+2 新文件）、`mypy app` **202 不变**（只扫 `app/`）；G-37 **24/24/0（扫 200）**、G-38 **20 单元/8 包/3 WARN/0 FAIL/0 stale/R6 WARN 0**（自检 exit 0）、G-32 26/26、台账 **119/91**。**遗留**：`usage` 写侧 INSERT 列覆盖（R7）属 **2C-5b**，当前为已知缺口；`usage_event.occurred_at` 由 DDL `DEFAULT now()` 生成、与内存侧调用方时钟存在语义差异，按 `(unit,record,field)` 文档化豁免（`expire_when`：出现读回路径时），**不改代码补列**。

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
