# 阶段二 W1 批次验证报告（B05 知识库授权与引用回链 / B06 工具三款与调用记录）

| 项 | 内容 |
| --- | --- |
| 报告编号 | QA-P2-W1-R01 |
| 验证批次 | B05（`81c8bbb`）、B06（`2edbb78`） |
| 验证分支 | `develop` |
| QA 工程师 | 严过关（Yan） |
| 验证日期 | 2026-10-21 |
| 判据来源 | `docs/qa-phase2-test-plan.md` v1.3（PERM-01/07、KB03-01~08、SEG-01~06、TL、§2.1 门禁、§7.1 分级）、`docs/architecture-phase2.md` v2.0（§2.4 D-11、§4.2 KB4-KB7、§4.3 TL1、§4.8 第 885 行三层校验、§9.2 B05/B06 可验证项） |
| **最终判定** | **有条件通过（PASS with conditions）**：B05/B06 功能准出通过（P0/P1 = 0）；T02 阶段门仍受 G-05 覆盖率阻断（见 §10） |
| **路由结论** | **NoOne（本批次闭环）**：第 1 轮发现的 P0/P2 已在 `5a036be`（修复提交，原 `7c2cf79` 经 amend 后为该哈希）修复并经 QA 独立复验通过；剩余 G-05 覆盖率与 G-13 脚本为**流程/门禁项**（见 §7、§10.5） |
| 复跑口径 | 不接受工程师自报数字，本报告所有数字均为 QA 本机实跑（附命令与关键输出） |

---

## 1. 结论摘要

> **阅读顺序建议**：第 1 轮（提交 `2edbb78`）判定 FAIL 并回退工程师 → 工程师 `7c2cf79` 修复 → 第 2 轮复验通过。**当前有效结论见 §10**，第 3~5 章保留第 1 轮的判据与证据（修复前后对照用）。

### 1.1 两轮结论对照

| 轮次 | 被测提交 | 9 条验收点 | 全量（含 QA 用例） | P0/P1 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 第 1 轮 | `81c8bbb` + `2edbb78` | 7 ✅ / 2 ❌ | non-PG `735 passed / 33 skipped / 5 failed`；PG `768 passed / 5 failed` | **P0=1** / P1=0 | **FAIL（回退）** |
| 第 2 轮 | `5a036be`（修复，首次复验时哈希为 `7c2cf79`） | **9 ✅ / 0 ❌** | non-PG `745 passed / 33 skipped / 0 failed`；PG `778 passed / 0 skipped / 0 failed` | **P0=0** / P1=0 | **有条件通过** |

> 说明：`7c2cf79` 复验通过后，工程师以 amend 方式把 KB3/KB4/M1/M2 的 ID 归一化补强并入同一提交，形成当前 HEAD `5a036be`（`git diff 7c2cf79 5a036be` = `document_service.py` / `knowledge_base_service.py` / `model_service.py` / `rag_service.py` / `test_rag_service.py`）。**本报告结论以 `5a036be` 为准**，两版均经 QA 独立 worktree 复跑（`7c2cf79`：745→744/777；`5a036be`：745/778，多 1 条为工程师新增单测）。

### 1.2 摘要（第 1 轮结论，作为缺陷背景）

| 维度 | 结论 |
| --- | --- |
| 9 条验收点 | **7 条 ✅ 通过**、**2 条 ❌ 不通过**（B05 验收点 1 的「R7 20003」与「对话静默跳过」在换 ID 形态后失效；B05 验收点 3 的批内重复不达标） |
| 门禁 | G-01/G-02/G-03/G-04/G-06/G-07/G-08/G-09/G-10/G-11/G-12/G-22 全绿；**G-05 覆盖率不达标**；G-13 门禁脚本缺失（以等价核对替代，结论：B05/B06 范围内新码齐备） |
| 缺陷 | **P0 = 1**、**P1 = 0**、**P2 = 2**、**P3 = 2** |
| 口径偏差裁定 | 「`restricted` 判定对无本地元数据的库放行」→ **不接受，必须修复（回退工程师）**，理由与复现证据见第 5 章 |
| 测量口径 | 全部数字取自**干净提交态 `2edbb78`**（独立 worktree 复跑）；18:11 起工程师在工作树中的未提交改动期间的测量**不作为判据**（见 §2.0） |

**关键事实**：该放行不是「只影响历史库的兼容妥协」，而是 `restricted` 判权的**整体短路开关**——任何一个底座对象都存在两种可达 ID 形态（`kb_<原生>` 与 `<原生>`），去掉前缀即可让判定完全跳过，未授权成员能取回受限库正文；同一根因下，**跨租户**用对方 `base_id` 直连检索同样返回数据。

---

## 2. 门禁实跑结果（G-01 ~ G-25，与本次相关项）

### 2.0 测量口径与时间线（必须先看，否则数字会对不上）

| 时间（UTC） | 工作树状态 | 说明 |
| --- | --- | --- |
| 17:51–17:57 | **干净**（`2edbb78`） | G-01/02/03 + 工程师用例集 non-PG（725 passed / 33 skipped）与 PG（758 passed / 0 skipped） |
| 17:58–18:08 | 干净 | QA 用例开发与实跑、OpenAPI/依赖/G-10/G-13 等价核对 |
| **18:11 起** | **脏**（工程师开始提交前修改 `rag_service.py` / `policy_service.py` / `grant_service.py` / `segment_service.py`） | 此后的仓库内测量**不作为判据**（18:11–18:15 的 PG 跑出现 18 failed / 4 xpassed，系修复中间态的混合快照；若误采会得出完全错误的结论） |
| 18:18–18:23 | **独立 worktree @ `2edbb78`**（`git worktree`，容器内干净副本 + 拷贝 QA 用例） | 本报告最终数字的采信口径 |

> 结论性提醒：**下列数字全部取自「干净提交态」**；工程师当前未提交的 WIP 需在提交后由 QA 重跑（见 §8.3 第 2 轮）。

执行环境：本机 Windows，`apps/adapter/.venv`（Python 3.13.14），`OA_BUSINESS_STORE=memory`（不传 DSN）与 PG（`127.0.0.1:55433`）两轮。

| 门禁 | 命令 | 关键输出 | 退出码 | 判定 |
| --- | --- | --- | --- | --- |
| G-01 ruff lint | `python -m ruff check .` | `All checks passed!` | 0 | ✅ |
| G-02 ruff format | `python -m ruff format --check .` | `112 files already formatted`（含 QA 用例为 113） | 0 | ✅ |
| G-03 mypy | `python -m mypy app` | `Success: no issues found in 73 source files` | 0 | ✅ |
| G-04 pytest 全量（工程师用例集） | `python -m pytest -q` | `725 passed, 33 skipped` | 0 | ✅ 0 failed / 0 error |
| G-04' pytest 带 PG（工程师用例集） | `pytest -q --run-pg`（PG DSN） | `758 passed`，**skipped=0** | 0 | ✅ 满足「`--run-pg` 时 0 skip / 0 fail」 |
| G-04'' 含 QA 验收用例（worktree） | 同上 + `tests/integration/test_qa_b05_b06_verification.py` | non-PG `735 passed / 33 skipped / 5 failed`；PG `768 passed / 5 failed / 0 skipped` | 1 | ❌ 5 条失败**全部**是 §5 缺陷断言（见下） |
| G-05 覆盖率（总） | `pytest --run-pg --cov=app --cov-report=term` | `TOTAL 6118 632 1208 198 87.22%` | 0 | ❌ 阈值 ≥90.5%，差 3.28pp |
| G-05 覆盖率（新模块） | `coverage report --include="app/business/*,app/db/*"` | `TOTAL 2610 368 562 94 83.98%` | 0 | ❌ 阈值 ≥85%，差 1.02pp |
| G-06 用例总数 | `pytest --collect-only -q \| tail -1` | 工程师用例集 758；含 QA 用例 773 | 0 | ✅ ≥599 |
| G-07 核心端点基线 | `python deploy/validate_openapi.py --allow-missing-phase2` | `端点统计: core=30 phase2=9/38` | 0 | ✅ core==30 |
| G-08 新域端点基线 | 同上 | `phase2=9/38` | 0 | ✅ B05 的 KB4-KB7（8/38）+ B06 的 TL1 = 9/38 |
| G-09 加性 diff | 同上（G4） | `[OK] G1-G5 全部通过` | 0 | ✅ 0 violation |
| G-10 底座泄漏 | `grep -rniE "dify\|milvus\|litellm\|chat-messages\|5001" app/api app/schemas` | 命中 **0**（`wc -l` = 0） | — | ✅ |
| G-11 迁移幂等 | `pytest tests/integration/test_g11_migration_idempotency.py -q --run-pg` | 5 条由 skip 转 pass | 0 | ✅ |
| G-12 RLS fail-closed | `pytest tests/integration/test_tenant_isolation.py -q --run-pg` | 15 条由 skip 转 pass | 0 | ✅ |
| G-13 错误码三处一致 | 方案指定 `apps/adapter/scripts/check_error_catalog.py` | **文件不存在**（`apps/adapter/scripts/` 目录不存在） | — | ⚠️ 见 §6.2 等价核对 |
| G-22 依赖门禁 | `python deploy/validate_deps.py` | `[OK] 依赖门禁通过`（运行时依赖 15 条） | 0 | ✅ |
| G-24 P0 用例 | 全量回归中 p0 标记用例 | 0 failed | 0 | ✅（但见 §5：PERM-07 的判据未被覆盖） |

### 2.1 覆盖率趋势（为什么要单独说明）

| 批次 | 用例数 | 总覆盖率 | 来源 |
| --- | --- | --- | --- |
| 阶段一验收 | 421 | 90.95% | `docs/phase1-acceptance-report.md` |
| B03 | 514 | 85.05% | `docs/phase2-progress-audit.md` |
| B04 | 637 | 85.97% | 同上 |
| **B05+B06（本次）** | **758** | **87.10%**（含 QA 用例为 87.22%） | 本报告实跑（干净提交态） |

结论：G-05 自 B03 起即处于不达标状态，B05/B06 **不是回归**（反而 +1.13pp），但方案 §2.1 将 G-05 列为**阻断项**且 §2.4 要求不达标必须有 `docs/qa-phase2-waivers.md` 登记（门禁编号/理由/补偿措施/到期批次/签字）——该文件**不存在**。故 G-05 判定为「红」而非「默认接受」，登记为 **QA-P2-03**（流程缺陷，见 §7）。

---

## 3. B05 五条验收点逐条判定

| # | 验收点（架构 §9.2 B05） | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | `restricted` 库仅授权成员可见：未授权成员**列表不含**、**直连 `20003`**、**R7 `20003`**、**对话静默跳过** | ❌ **不通过** | 列表 ✅、KB3 ✅、R7（正确 ID）✅、对话（正确 ID）✅；但**换 ID 形态后全部失效，且跨租户直接可读**：`::test_a2_*`、`::test_a2b_*`、`::test_a2c_*`、`::test_a3_*` 共 4 条阻断断言全部失败（证据见 §5.3） |
| 2 | 授权给部门后成员移出部门 ≤5s 失效（策略缓存 5s） | ✅ 通过 | `::test_a5_department_grant_removal_expires_within_five_seconds`（注入单调时钟，TTL 上限断言 `permission_cache_ttl_seconds <= 5.0` 成立，默认 5.0；过期后 `can_read` 为 False） |
| 3 | 同对象重复授权 `skipped_count=1` | ⚠️ **部分通过** | 跨批次重复 ✅（`created=0/skipped=1`）；**同批 `subject_ids` 内重复 ❌**（`created=1/skipped=0`，违反架构 §4.2 要点「`subject_ids` 中重复对象计入 `skipped_count`」）→ **B05-P2-02** |
| 4 | 重处理后旧引用返回 `200 + status=stale`，无控制台报错、无错误码外露 | ✅ 通过 | `::test_a6_kb4_stale_returns_200_without_error_semantics`：`status_code=200`、`code=0`、`data.status=="stale"`；响应全文无 5 位错误码、无禁词；`::test_a7_*` 断言无内部字段 |
| 5 | 既有 30 端点回归全绿 | ✅ 通过 | G-07 `core=30` 精确相等；G-09 `G1-G5 全部通过`；全量 734 passed / 0 failed |

补充：KB4 的对象级校验本身是**正确**的——`KnowledgeBaseService.require_readable()` 对无元数据的库抛 `11001`（`knowledge_base_service.py:571-575`），因此详情入口 fail-closed。问题只出在 R7 与对话两条路径（见 §5.2 根因定位）。

---

## 4. B06 四条验收点逐条判定

| # | 验收点（架构 §9.2 B06） | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | T1 返回 5 款、`operations` 全为中文业务语言（无技术词） | ✅ 通过 | `::test_b1_*`：`tool_type` 集合 == {email, github, wecom, feishu, wechat_mp}；逐条 `display_name` 含中文；禁词（向量/召回/相似度/embedding/chunk/segment/score/dify/milvus/litellm/5001）0 命中 |
| 2 | T1-T7 契约与阶段一一致（G-04 加性 diff / G-09 基线） | ✅ 通过 | `validate_openapi.py`：`[OK] G1-G5 全部通过`、`phase2=9/38`；`test_t1_and_t5_paths_unchanged` 覆盖 7 条路径集合不变 |
| 3 | TL1 返回最近 20 条、失败项无堆栈无错误码 | ✅ 通过 | `::test_b2_*`（22 条调用 → `items=20`、`meta.total=22`、倒序）；`::test_b2b_*`（失败项 `status=failed`、`error_message` 为业务文案，响应全文无 `13006` / `Traceback` / `File "`） |
| 4 | 未连接调用 T5 返回 `13002` | ✅ 通过 | `::test_b3_*`：`code=13002`，文案含「尚未连接」（企业微信专属文案） |

---

## 5. 口径偏差裁定：「无本地元数据的库放行」是否可接受

### 5.1 工程师的表述与代码事实

工程师自报：「`restricted` 判定对『本地无元数据的历史知识库』**放行**（不按 restricted 拦），理由是兼容阶段一通过 R1 直建、未经本地落库的库。」

代码事实（两处 fail-open，均为 B05 新增/改动）：

| 位置 | 代码 | 后果 |
| --- | --- | --- |
| `app/services/rag_service.py:696-702`（`_assert_readable`） | `record = await self._bases.store.get(auth.tenant_id, base_id)`；`if record is None: return` | R7 检索**不判权**直接下发下游 |
| `app/business/policy_service.py:307-311`（`filter_authorized`） | `if record is None: allowed.append(base_id); continue` | M1/M2 对话**不静默跳过**，库照常参与检索 |

### 5.2 根因：判定键选错（按「元数据是否存在」而非「对象归属」）

架构 §4.8 第 885 行规定顺序为 `租户层 → 组织层 → 权限点层 → 对象级`，并明确**禁止**「列表过滤了但详情接口没校验」。当前实现把「本地有没有这条元数据」当成了判权前提，于是：

1. **同一底座对象存在两种可达 ID**：阶段二 `base_id = "kb_" + 底座 dataset id`（`rag_service.py:266`），而 R7 下发下游时执行 `base_id.removeprefix("kb_")`（`rag_service.py:665`）。用 `<原生 id>` 调用时 `store.get()` 必然 `None` → 整体放行 → 下游照样按 `datasets/<原生 id>/retrieve` 取数。**去前缀这一步对任何拿到过 `base_id` 的人都是公开可推导的**（`base_id` 本来就在 R2 列表、详情页 URL、审计日志里）。
2. **租户层也被同一分支跳过**：`store.get()` 是租户作用域的，跨租户查不到 → 返回 `None` → 放行。即**对方租户的 `base_id`（连前缀都不用去）就能读到对方数据**。

### 5.3 复现证据（QA 实跑，非推断）

证据 A：桩模式下错误码不一致（未授权成员 `usr_b`，库 `kb_qaA2B` 为 `restricted`）

```
正确 ID  : POST /api/v1/knowledge/search {"base_id": "kb_...", "query": "年假"} -> code=20003  ✅ 拒绝
去前缀 ID: POST /api/v1/knowledge/search {"base_id": "...",    "query": "年假"} -> code=0     ❌ 放行
```

证据 B：**下游真实返回正文**（注入 `httpx.MockTransport`，`downstream_enabled=True`，未授权成员）

```
AssertionError: 未授权成员通过去前缀 ID 取回了 restricted 库正文：
downstream=http://dify.test/v1/datasets/qaA2B/retrieve
contents=['第十五条 高管薪酬明细（受限库原文）']
```

证据 C：**跨租户**（`tnt_a` 建 `kb_crossA`（restricted），`tnt_b` 的攻击者检索）

```
'kb_crossA'    -> code=0 segments=['A 企业受限资料']
'crossA'       -> code=0 segments=['A 企业受限资料']
```

证据 D：对话路径（M1/M2）同样失效

```
AssertionError: 未授权库未被剔除：['01M5FT0G0NS7MX62CTQTWE0H12']
（filter_authorized(["kb_...", "01M5..."]) 只剔除了带前缀的那一个）
```

复现命令（可重复执行；下述 5 条断言在干净提交态 `2edbb78` 上**全部失败**即为证据）：

```bash
cd apps/adapter
./.venv/Scripts/python.exe -m pytest tests/integration/test_qa_b05_b06_verification.py -q \
  -k "a2 or a2b or a2c or a3 or a4b"
# 干净 worktree 复跑（推荐，避免工作树被并发修改）：
git -C <repo> worktree add /tmp/qa-b05-clean 2edbb78
```

实测：干净 worktree @ `2edbb78` + `--run-pg` → `768 passed, 5 failed, 0 skipped`，5 条失败即 `a2 / a2b / a2c / a3 / a4b`。

### 5.4 逐问裁定

| 问题 | 裁定 |
| --- | --- |
| 是否构成 P0（无权限账号通过详情/检索入口拿到数据）？ | **是。** 命中测试方案 §7.1 P0 定义 ③（PERM-01/07）；证据 B 已实跑取回受限库正文；同一根因下还命中 P0 定义 ①（跨租户可见数据，证据 C）。按 §7.1 判级原则 ③「同一根因多现象按最高级别计 1 条」，合并为 **1 个 P0**。 |
| 是否与阶段二「知识库级权限」目标（G2-1）冲突？ | **是。** G2-1 的验收形态是「未授权成员拿不到数据」，而当前只要换 ID 写法即可拿到；`restricted` 语义**不可依赖**。 |
| 兼容性理由是否成立？ | **不成立，且自相矛盾。** R2 列表完全由本地元数据驱动（`rag_service.list_bases → KnowledgeBaseService.list → store.list`），未经落库的历史库**本来就已从列表消失**；工程师保留 R7 放行并未实现「阶段一行为不变」，而是造成「列表看不到、检索拿得到」这对更糟的组合——正是 §4.8 禁止的形态。若确需兼容历史库，正解是迁移期**回填/合成元数据**或提供显式开关（默认关闭、写入登记），而不是 fail-open。 |

### 5.5 明确结论

**不接受（记 R-x 风险）无效 → 必须修复，回退工程师。**

修复建议（不代替工程师决策，仅给出判据方向）：

1. `_assert_readable`：`record is None` 时**不得 return**——跨租户/未登记库一律 `20003`（不泄露存在性，符合 §8 租户层判据）；不得用 `11001` 之外的新码。
2. `filter_authorized`：`record is None` 时剔除（fail-closed）。
3. `base_id` **规范化**：在入口把 `kb_` 前缀形态与原生形态归一（或拒绝非规范形态 `20001`），保证「一个对象一个 ID」，否则任何按 ID 查表的校验都可被绕过。
4. 如确需兼容阶段一历史库：加 `OA_LEGACY_BASE_PASSTHROUGH`（默认 `false`）、在 `/readyz` 或启动日志提示，并在 `docs/qa-phase2-waivers.md` 登记；不得默认放行。
5. 回归时请保留本报告 §5.3 的 5 条 QA 断言（当前已在用例文件中以阻断形式存在，无需 `xfail`）。

### 5.6 修复落地情况（第 2 轮已复验，见 §10）

工程师在 `5a036be` 中按上述方向完成修复，并把归一化补强推广到 KB3/KB4/M1/M2 入口（`document_service.py` / `knowledge_base_service.py` / `model_service.py`）：

- 新增 `normalize_base_id()`（`BASE_ID_PREFIX` 归一化），R7/KB3/KB4/M1/M2 入口先归一化再判权；
- `_assert_readable` 改为 `record is None or record.is_deleted` → `20003`（fail-closed）；
- `filter_authorized` 改为未登记/已删除库计入 `skipped`（fail-closed）。

QA 第 2 轮在干净 worktree 复跑：`745 passed / 33 skipped`（non-PG）、`778 passed / 0 skipped / 0 failed`（PG），5 条缺陷断言全部转绿（详见 §10.2/§10.4）。

另记录一项事实（不计缺陷）：QA 用例文件中的 `xfail` 标记在第 1 轮期间被外部移除，当前 5 条缺陷断言以**阻断**形式存在并已随修复提交；`test_a2b` 的断言口径被工程师调整为「抛 `20003` + 下游零调用」，QA 已裁定**接受**（比原写法更强，理由见 §10.3）。后续建议「QA 用例由 QA 维护」，调整断言口径请先与 QA 对齐。

---

## 6. 附加必查项（黑盒与合规）

### 6.1 G-10 底座泄漏

`grep -rniE "dify|milvus|litellm|chat-messages|5001" apps/adapter/app/api apps/adapter/app/schemas` → **0 命中** ✅。
（注：`dify` 仅出现在 `app/services/**`、`app/clients/**` 内部实现层，符合阶段一既有口径。）

### 6.2 G-13 错误码三处一致（等价核对）

方案指定脚本 `apps/adapter/scripts/check_error_catalog.py` **在仓库中不存在**（`apps/adapter/scripts/` 目录不存在），门禁无法按方案执行 → 登记 **QA-P3-04**。QA 以等价脚本核对：

| 核对项 | 结果 |
| --- | --- |
| `errors.py` 码数 / `business-errors.ts` 码数 | 39 / 52 |
| `errors.py` 中缺失于 `business-errors.ts` 的码 | **0 条** ✅（前端覆盖后端全集） |
| 架构 §7 14 个新码 | `11007` 已落地；`14005-14008`、`15001-15009`（13 个）**未落地**——属 B08/B09 范围，**不构成本批缺陷** |
| 本批相关码 | `13002/13003/13004/13005/13006`、`20003/20004/20005`、`11001/11004` **两侧均存在** ✅ |

### 6.3 新增响应模型内部字段

| 检查 | 结果 |
| --- | --- |
| `schemas/knowledge_ext.py` / `segment.py` / `tool_ext.py` 是否出现 `tenant_id` / `dify_dataset_id` / `credential_*` / `api_key` | 仅 `KB8` **请求体** `credential_ref`（输入字段，合法）；响应 `CredentialBindingData` 只有 `base_id/bound/updated_at` ✅ |
| 运行时响应键扫描（KB5/KB6/R2） | `tenant_id`、`owner_user_id`、`native_segment_id`、`content_sha256`、`api_key` **0 命中** ✅（`::test_a7_*`） |
| KB4 stale 降级语义 | `content` 允许 `null`；`status=stale` 承担降级语义，且 `STATUS_DISPLAY_HINTS` 给出前端文案映射（「该引用来自旧版本内容，原文已更新」），不进响应体 ✅ |

### 6.4 幂等指纹是否含 `body_digest`

| 接口 | 实现 | 验证 |
| --- | --- | --- |
| KB6 `POST .../grants` | `hash_request_body(body.model_dump_json())` → `body_digest`（`knowledge_ext.py:455`） | ✅ 实跑：同 `Idempotency-Key` 换 `subject_ids` → `code=20005`，且新主体**未被授权**（`::test_a8_*`） |
| KB1 / KB8 | 同上（`knowledge_ext.py:171`、`:324`） | ✅ 代码核对一致 |
| KB2 / KB7（无体） | 走空体固定摘要（显式注释） | ✅ 符合架构 §3.0.5「摘要项不得省略」 |
| TL1（只读） | 不涉及幂等 | — |

---

## 7. 缺陷清单

> 状态：第 1 轮登记的 P0/P2 均已由 `5a036be` 修复并经 QA 第 2 轮复验转绿（见 §10）。下表保留原始复现信息以便回溯。

| 编号 | 等级 | 标题 | 复现步骤 | 期望 / 实际 | 归属 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| **B05-P0-01** | **P0（阻断）** | `restricted` 判权对「无本地元数据」的库 fail-open，导致 R7 / 对话可读到无权限库（含**跨租户**） | ① `T_owner` 建 `restricted` 库 `kb_X`；② 未授权成员 `POST /knowledge/search {"base_id":"X"}`（去掉 `kb_` 前缀）或 `{"knowledge_base_ids":["kb_X"]}` 走 M1；③ 或 `tnt_b` 用户直接用 `tnt_a` 的 `kb_crossA` 检索 | 期望 `20003`（§4.8 租户层+对象级）；实际 `code=0` 且下游 `datasets/<id>/retrieve` 返回受限库正文。定位：`app/services/rag_service.py:697`（`if record is None: return`）、`app/business/policy_service.py:309-311`（`if record is None: allowed.append(...)`） | Engineer | ✅ 已闭环（`5a036be`：归一化 + fail-closed） |
| **B05-P2-02** | P2 | 同批 `subject_ids` 内重复对象未计入 `skipped_count` | `POST .../grants {"subject_ids":["usr_c","usr_c"]}` | 期望 `created=1/skipped=1`（架构 §4.2 要点）；实际 `created=1/skipped=0`（`grant_service.py:228` + `_normalize_subject_ids:394` 先行去重，丢失 skipped 计数） | Engineer | ✅ 已闭环（保留重复项 + `seen` 计 skipped） |
| **QA-P2-03** | P2（流程） | G-05 覆盖率两项均未达标且无豁免登记 | `pytest --run-pg --cov=app`；`coverage report --include="app/business/*,app/db/*"` | 期望 ≥90.5% / ≥85%；第 1 轮实际 **87.22% / 83.98%**、第 2 轮 **87.11% / 83.97%**；`docs/qa-phase2-waivers.md` **不存在**（方案 §2.4 要求登记门禁编号/理由/补偿/到期/签字）。注：非本批回归（B03 85.05% → B04 85.97% → 现在 87.11%，持续改善） | 主理人 + Engineer | ⏳ 待决策 |
| **QA-P3-04** | P3 | G-13 门禁脚本缺失，门禁无法按方案执行 | `python apps/adapter/scripts/check_error_catalog.py` | 期望可执行；实际 `No such file or directory`（已用等价核对替代，见 §6.2）。脚本已写但未提交；建议提交后纳入门禁序列，`--strict` 待前端码补齐（B11-B14）再开 | Engineer | ⏳ 待提交 |
| **QA-P3-05** | P3 | 用例名与测试方案 §5 落点名不一致，追溯困难 | 方案要求 `test_grant_and_backlink.py::test_restricted_base_detail_and_search_return_20003` 等，实际为 `test_restricted_base_is_visible_only_to_granted_member` 等 | 不影响判据成立性；建议用例名对齐或同步更新方案映射表 | Engineer / QA | ⏳ 建议 |
| **QA-P3-06** | P3 | G-11 scratch 库偶发撞锁致假失败 | 见 §10.6 | 期望稳定绿；实际偶发 1 次 `test_g11_cli_check_then_apply_on_empty_database` 失败，清残留后全量 778 passed | QA / Engineer | ⏳ 建议加固 |

**未计入缺陷的观察项**（供决策，不需本批修复）：

- R7 仍要求 `Idempotency-Key`（阶段一冻结契约，检索类接口带幂等头属设计遗留）；本批未改，符合「零改动」要求。
- `KB4` 在「映射缺失」时返回 `status=stale + content=null`（`segment_service.py:366-383`），与 TC-P2-SEG-05「超保留期 → stale + content is None」一致 ✅。

---

## 8. 第 1 轮准出结论与路由记录（已闭环）

### 8.1 第 1 轮判定：**FAIL**（第 2 轮已改判为「有条件通过」，见 §10）

依测试方案 §7.3（T02 里程碑准出：知识库 27 条 + 凭据 7 条 + 工具 22 条全通；0 P0/0 P1）与 §7.4 checklist 第 10、13 条（「权限矩阵 0 越权（列表/详情/检索三入口一致）」「0 个 P0 未闭环」）：

- **B06：可准出**（4/4 验收点通过，门禁全绿，无缺陷）。
- **B05：不准出**（验收点 1 在 ID 形态维度失效，P0 未闭环；P2-02 契约偏差）。

### 8.2 路由：**Engineer（回退）**

1. 修复 B05-P0-01（判权 fail-closed + `base_id` 规范化 + 跨租户 `20003`）；
2. 修复 B05-P2-02（同批重复计入 `skipped_count`）；
3. 回归命令：`pytest tests/integration/test_qa_b05_b06_verification.py -q`（修复后去掉 4 个 `xfail` 标记应为全绿）+ 全量 `pytest -q --run-pg`；
4. G-05 覆盖率与 G-13 脚本按 §7 登记豁免或补齐（不阻断本批功能准出，但阻断 T02 阶段门）。

### 8.3 复验承诺（第 2 轮）

修复提交后 QA 只跑一轮回归，判据为：①`test_qa_b05_b06_verification.py` **15/15 全绿**（当前 5 条阻断断言应转为通过）；②全量 `--run-pg` 0 failed / 0 skipped；③ G-07/08/09/G-10 不变；④ 无新增 P0/P1。若仍有残留，按「已知问题」记入结论，不再开启第 3 轮。

---

## 9. 附：本次新增测试资产与执行命令

| 资产 | 说明 |
| --- | --- |
| `apps/adapter/tests/integration/test_qa_b05_b06_verification.py` | QA 独立验收用例 **15 条**（10 通过 + 5 条编码缺陷的阻断断言），不复用工程师辅助函数；ruff / ruff format 合规 |

用例清单（15 条，与本章判据一一对应）：

| 用例 | 对应验收点 | 干净提交态结果 |
| --- | --- | --- |
| `test_a1_restricted_base_hidden_from_unauthorized_member_three_entries` | B05-1（列表/KB3/R7） | ✅ pass |
| `test_a2_search_denies_unauthorized_member_using_native_base_id` | B05-1 / PERM-07 | ❌ fail（P0） |
| `test_a2b_downstream_call_proves_restricted_data_exposure` | B05-1 / §7.1 P0③ | ❌ fail（P0，附下游取数证据） |
| `test_a2c_cross_tenant_search_is_denied` | §7.1 P0① 跨租户 | ❌ fail（P0） |
| `test_a3_conversation_drops_unauthorized_base_using_native_id` | B05-1 / PERM-07 | ❌ fail（P0） |
| `test_a4_duplicate_grant_increments_skipped_count` | B05-3（跨批） | ✅ pass |
| `test_a4b_intra_batch_duplicate_counts_as_skipped` | B05-3（批内）/ 架构 §4.2 | ❌ fail（P2） |
| `test_a5_department_grant_removal_expires_within_five_seconds` | B05-2 | ✅ pass |
| `test_a6_kb4_stale_returns_200_without_error_semantics` | B05-4 | ✅ pass |
| `test_a7_responses_do_not_expose_internal_fields` | §6.3 合规 | ✅ pass |
| `test_a8_kb6_idempotency_fingerprint_includes_body` | §6.4 幂等指纹 | ✅ pass |
| `test_b1_t1_returns_five_tools_with_chinese_operations` | B06-1 | ✅ pass |
| `test_b2_tl1_returns_latest_twenty_without_stack_or_codes` | B06-3 | ✅ pass |
| `test_b2b_tl1_failed_item_shows_business_reason_only` | B06-3（失败项） | ✅ pass |
| `test_b3_execute_without_connection_returns_13002` | B06-4 | ✅ pass |

```bash
# 门禁（本报告数字来源）
cd apps/adapter
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m pytest -q
OA_TEST_DATABASE_URL="postgresql://officeai_app:***@127.0.0.1:55433/officeai" \
  ./.venv/Scripts/python.exe -m pytest -q --run-pg --cov=app --cov-report=term
cd ../.. && apps/adapter/.venv/Scripts/python.exe deploy/validate_openapi.py --allow-missing-phase2
apps/adapter/.venv/Scripts/python.exe deploy/validate_deps.py

# QA 独立验收
cd apps/adapter && ./.venv/Scripts/python.exe -m pytest \
  tests/integration/test_qa_b05_b06_verification.py -q -rxX
```

> 口径声明：本报告所有门禁结论均为**本地实跑**，未使用 CI；CI 额度恢复后应按方案 §2.2 顺序复跑一次，结果应与本报告一致。

---

## 10. 第 2 轮复验（交付 `5a036be`）

### 10.1 复验口径

被测提交：`5a036be fix(adapter): B05 修复 restricted 判权 fail-open 与授权计数（P0）`（首次复验时的哈希为 `7c2cf79`，其后经 amend 并入 KB3/KB4/M1/M2 归一化补强）。在**独立 worktree**（`git worktree add /tmp/qa-head 5a036be`）上复跑，避免工作树中并行 WIP 干扰测量（第 1 轮已踩过一次，见 §2.0）。

### 10.2 复验结果

| 项 | 命令 | 第 2 轮输出（`5a036be`） | 判定 |
| --- | --- | --- | --- |
| G-01 ruff | `ruff check .` | `All checks passed!` | ✅ |
| G-02 ruff format | `ruff format --check .` | `113 files already formatted` | ✅ |
| G-03 mypy | `mypy app` | `Success: no issues found in 73 source files` | ✅ |
| G-04 pytest（non-PG） | `pytest -q` | `745 passed, 33 skipped` | ✅ 0 failed |
| G-04' pytest（PG） | `pytest -q --run-pg` | `778 passed`，**0 skipped / 0 failed** | ✅ |
| G-05 覆盖率（总） | `--cov=app` | `TOTAL 6153 641 1218 201 87.11%` | ❌ 仍 <90.5%（见 §10.5） |
| G-05 覆盖率（新模块） | `coverage report --include="app/business/*,app/db/*"` | `TOTAL 2637 372 570 96 83.97%` | ❌ 仍 <85%（差 1.03pp） |
| G-07/08/09 | `validate_openapi.py --allow-missing-phase2` | `core=30 phase2=9/38`、`[OK] G1-G5 全部通过` | ✅ |
| G-10 | `grep -rniE "dify\|milvus\|litellm\|chat-messages\|5001" app/api app/schemas` | `0` | ✅ |
| G-22 | `validate_deps.py` | `[OK] 依赖门禁通过` | ✅ |
| **QA 验收用例** | `pytest tests/integration/test_qa_b05_b06_verification.py -q` | **15 passed, 0 failed** | ✅ **缺陷断言全部转绿** |

### 10.3 修复内容 QA 复核（是否真的修对，而不是把断言改松）

| 修复点 | 复核方式 | 结论 |
| --- | --- | --- |
| ID 归一化 | `policy_service.normalize_base_id()`（`kb_` 前缀唯一规范形态），R7 入口先归一化再判权 | ✅ 一个对象一个判定键，前缀形态不再产生「查不到」分支 |
| R7 fail-closed | `_assert_readable`：`record is None or record.is_deleted` → `20003`（不泄露存在性） | ✅ 跨租户/未登记库一律拒绝（`test_a2c` 转绿） |
| 对话 fail-closed | `filter_authorized`：未登记/已删除计入 `skipped` 并剔除 | ✅ `test_a3` 转绿 |
| 批内重复计数 | `_normalize_subject_ids` 改「去空白、**保留重复项**」，主循环用 `seen` 计 `skipped` | ✅ `test_a4b` 转绿（3 项含重复 → `created=1/skipped=2`，与其单测一致） |
| 既有断言是否被放宽（方案 §6.1 R2） | 逐条看 `test_endpoints.py::test_r7_search`、`test_grant_service.py::test_create_grants_deduplicates_within_single_request` 的 diff | ✅ **不是放宽而是收紧**：`test_r7_search` 改为先建库再检索（旧断言固化的正是 fail-open 行为）并新增 `test_r7_unknown_base_returns_20003` 反例；批内重复断言由 `skipped==0` 改为 `skipped==2`（对齐架构 §4.2）。属「判据修正 + 新增反例」，允许 |
| `test_a2b` 断言口径调整（工程师提出，QA 裁定） | 原写法：`leaked = await search(...)` 后断言 `segments == []`；新写法：`pytest.raises(ApiError) → code==20003` **且** `"url" not in captured`（下游一次都没被调用） | ✅ **接受**。修复后未授权/未登记库一律抛 `20003`，与 `a2`/`a2c` 口径一致；新写法比原写法**更强**（不仅「没返回正文」，而且「判权拦截必须发生在发起下游请求之前」）。若保留「返回空列表」语义反而会与 20003 口径冲突，故不改回 |

### 10.6 G-11 偶发失败（测试基础设施，P3 已登记）

第 2 轮 PG 全量曾出现一次 `test_g11_cli_check_then_apply_on_empty_database` 失败，单独跑该文件 5 passed、清掉残留 scratch 库后全量重跑 778 passed。QA 复核 `tests/integration/pg_fixture.py:378-405`：`create_empty_database()` 已做 `pg_terminate_backend` + `DROP DATABASE IF EXISTS`，因此更可能是「上一轮被中断留下仍存活的连接 / 子进程尚未释放」导致 `DROP DATABASE` 撞锁，而非建库逻辑缺失。

登记为 **QA-P3-06**（不阻断）：建议 ① `DROP DATABASE` 失败时重试 2-3 次（递增等待）；② CLI 用例把 `drop_database` 放进 `finally`；③ scratch 库名加 `worker/xdist` 后缀，避免并行撞名。**复现提示**：若看到该失败，先查 `pg_stat_activity` 中的残留连接与 `officeai_scratch_*` 库。

### 10.4 9 条验收点第 2 轮判定（全部通过）

| # | 验收点 | 第 2 轮 | 证据 |
| --- | --- | --- | --- |
| B05-1 | `restricted` 仅授权成员可见（列表 / 直连 / R7 / 对话，含 ID 形态与跨租户） | ✅ | `test_a1`、`test_a2`、`test_a2b`、`test_a2c`、`test_a3` 全绿 |
| B05-2 | 部门移出成员 ≤5s 失效 | ✅ | `test_a5` |
| B05-3 | 同对象重复授权 `skipped_count=1` | ✅ | `test_a4`（跨批）+ `test_a4b`（批内） |
| B05-4 | 重处理后旧引用 `200 + stale`，无错误码外露 | ✅ | `test_a6` |
| B05-5 | 既有 30 端点回归全绿 | ✅ | `core=30` 精确相等 + `G1-G5` 全过 + PG 全量 0 failed |
| B06-1 | T1 5 款、operations 中文业务语言 | ✅ | `test_b1` |
| B06-2 | T1-T7 契约与阶段一一致 | ✅ | `validate_openapi.py` G4 0 violation |
| B06-3 | TL1 最近 20 条、失败项无堆栈无错误码 | ✅ | `test_b2`、`test_b2b` |
| B06-4 | 未连接 T5 → `13002` | ✅ | `test_b3` |

### 10.5 遗留（不阻断本批功能准出，阻断 T02 阶段门）

| 编号 | 项 | 现状 | 处置建议 |
| --- | --- | --- | --- |
| QA-P2-03 | G-05 覆盖率两项未达标（87.11% / 83.97%） | 仍未达标（自 B03 起的存量趋势） | 二选一：①补测至 ≥90.5%/≥85%（优先 `app/db/repositories/*` 与 `app/tools/wechat_mp_connector.py`，后者仅 69.60%）；②按方案 §2.4 写 `docs/qa-phase2-waivers.md` 登记（门禁编号/理由/补偿措施/到期批次/签字），到期批次建议 B07 |
| QA-P3-04 | G-13 门禁脚本缺失 | 工作树中已见 `apps/adapter/scripts/check_error_catalog.py`（**未提交**，`5a036be` 不含） | 随下一批提交并纳入 §2.2 门禁序列复跑；严格模式（`--strict`）需等前端 `business-errors.ts` 在 B11-B14 补齐后开启 |
| QA-P3-06 | G-11 scratch 库偶发撞锁（见 §10.6） | 观测到 1 次 | 重试 + `finally` 清理 + 库名带 worker 后缀 |

**工作区归属说明（避免误认领）**：截至本报告定稿，仓库工作树中仍有未提交/未跟踪内容——`.github/workflows/ci-adapter.yml`（改动）、`apps/adapter/scripts/`（G-13 脚本）、`apps/adapter/tests/integration/test_b05_guard_regressions.py`、`apps/adapter/tests/unit/test_policy_permission_guards.py`、本报告。其中 **`test_policy_permission_guards.py` 与 `test_b05_guard_regressions.py` 非 QA 产出**（QA 本批只新增 `test_qa_b05_b06_verification.py`，已随 `5a036be` 提交），如需纳入准出判据请先提交，QA 再按 §9 命令复跑。
