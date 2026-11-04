# Moy（麦子）V1.0 · 测试覆盖率与测试有效性审计

- 审计日期：2026-11-02
- 审计角色：QA（严过关）
- 仓库：`C:\Users\15864\Desktop\麦子`，分支 `main`，HEAD `e3734a0`
- 方式：**只读审计**（未修改任何源码/配置/测试；仅写本报告与本轮覆盖率报告产物）
- 口径纪律：本文所有数字**均为本次实测**，命令与耗时逐条记录；README 的 2211 / 212 / 85.87% 仅作对比基准，不作结论依据。

---

## 一、实测结果总览

### 1.1 后端（`apps/adapter`）双口径

| 口径 | 命令 | collected | passed | failed | skipped | 合并覆盖率 |
| --- | --- | --- | --- | --- | --- | --- |
| **无 PG（本机默认）** | `OA_BATCH=B21 pytest tests -q --cov=app --cov-report=json:coverage-audit.json` | **2465** | **2222** | **8** | **235** | **85.72%**（余量 +164 单位） |
| **含 PG（`--run-pg`）** | `OA_BATCH=B21 OA_TEST_DATABASE_URL=…officeai_app… pytest tests -q --run-pg --cov=app --cov-report=json:coverage-pg.json` | **2465** | **2453** | **12** | **0** | **92.31%**（余量 +1661 单位） |

- 无 PG 口径耗时 **1210.13s（20 分 10 秒）**，与 `pyproject.toml` 的 `[tool.coverage.run] concurrency=["greenlet","thread"]` 口径一致。
- 覆盖率明细（无 PG）：语句 **16479/18826（87.53%）**，分支 **3004/3902（76.99%）**，合并 **19483/22728 = 85.72%**；`fail_under = 85` **勉强达标**。
- **绝对余量：`19483 − ceil(85% × 22728) = 19483 − 19319 = +164 单位（约 +0.72pp）`。** 这是"1 次小规模回退即可破线"的余量，不能用百分比读作"稳过"。

### 1.2 8 条红灯（本次实测新发现，README 声称 0 failed）

```
FAILED tests/unit/test_job_runner.py::TestEnqueueDedupe::test_dedupe_released_after_task_finishes
FAILED tests/unit/test_job_runner.py::TestClaimAndLease::test_tick_claims_and_marks_succeeded
FAILED tests/unit/test_job_runner.py::TestClaimAndLease::test_expired_lease_is_reclaimed
FAILED tests/unit/test_job_runner.py::TestRetryPolicy::test_max_attempts_one_means_no_retry
FAILED tests/unit/test_job_runner.py::TestRetryPolicy::test_max_attempts_retries_until_exhausted
FAILED tests/unit/test_job_runner.py::TestRetryPolicy::test_registry_policy_caps_database_value
FAILED tests/unit/test_job_runner.py::TestRetryPolicy::test_unregistered_kind_fails_loudly
FAILED tests/unit/test_job_runner.py::TestStartStop::test_start_then_stop_leaves_no_residual_task
```

8 条全部同因：`await runner.tick()` 返回 `0`，期望 `1`。根因已定位到**被测实现**（详见 §3 T-01），不是测试写错。

### 1.3 含 PG 口径（本轮实跑完成）

| 项 | 值 |
| --- | --- |
| PG 可用性 | **可用**。`docker ps` 见 `moy-qa-pg`（pgvector 0.8.7-pg18，宿主 55433）；`officeai_app` 角色实测 `rolbypassrls = false`，满足 `NOSUPERUSER NOBYPASSRLS` 前置断言 |
| DSN | `postgresql://officeai_app:***@127.0.0.1:55433/officeai` |
| 结果 | **2465 collected / 2453 passed / 12 failed / 0 skipped**，耗时 1466.54s（24 分 26 秒） |
| 覆盖率 | **92.31%**（语句 17713/18826 = 94.09%，分支 3267/3902 = 83.73%，合并 20980/22728） |
| 余量 | `20980 − ceil(85% × 22728) = +1661 单位`（无 PG 口径只有 +164，**相差 10 倍**） |
| skipped | **0**，符合 QA 口径"带 `--run-pg` 时 skipped 即红" |

**12 条红灯 = 8 条（T-01，实现缺陷，两口径同红）+ 4 条 PG 专属：**

```
FAILED tests/integration/test_org_repo_pg.py::TestDepartmentCrud::test_create_and_get_department
FAILED tests/integration/test_usage_repo_pg.py::TestUsageEvents::test_append_event
FAILED tests/integration/test_usage_repo_pg.py::TestUsageEvents::test_monthly_rows
FAILED tests/integration/test_usage_repo_pg.py::TestUsageEvents::test_event_counts
```

这 4 条的报错全部是 `RuntimeError: Event loop is closed`（`ProactorEventLoop running=False closed=True`），堆栈落在 `asyncio/base_events.py::_check_closed` → `app/db/session.py::tenant_scope`。

**定性（不夸大）**：这是 **Windows 事件循环 + asyncpg 连接池的夹具生命周期问题**，不是从断言看出的产品缺陷——即"测试基础设施在 Windows 本机不可靠"。同一套用例在 Linux CI 上很可能不出现。但在修复前，**"PG 口径全绿"这条判据在 Windows 本机是不成立的**，评审不能拿本机 PG 结果当绿灯。

### 1.3.1 PG 仓储覆盖率（CI 门禁 5b 的校准实测）

CI 门禁 5b 的口径是 `coverage report --include="*/db/repositories/*/postgres.py" --fail-under=50`，注释自述"下限 50 为保守起点，首次实跑后按实测校准"。

| 口径 | `postgres.py` 合并覆盖率 | 行为 |
| --- | --- | --- |
| 无 PG（默认） | **27.93%**（行 30.98%） | 低于 50% → 若 CI 漏带 `--run-pg`，5b 会红（这是设计意图） |
| **含 PG（`--run-pg`）** | **90.66%**（行 92.46%） | 远超 50% |

**分文件（含 PG）**：usage 87.76 / role 89.19 / conversation 83.02 / org 89.03 / memory 96.79 / plugin 95.94 / task 91.70 / billing 87.28。

> **结论**：带 `--run-pg` 时 PG 仓储实现是**测得到的**（90.66%）。M4 描述的不是"没写测试"，而是"默认命令不跑这些测试"。CI 5b 的下限 **50% 应校准到 ≥ 85%**（建议 88，留 ~2.7pp 余量），否则它只能证明"PG 用例跑过"，证明不了"PG 实现被覆盖"。


### 1.4 前端（`apps/web`，Vitest + v8）

| 指标 | 实测 | 阈值（`vitest.config.mts` 棘轮） | 达标 |
| --- | --- | --- | --- |
| % Stmts | **86.75** | 86.4 | ✅ +0.35pp |
| % Branches | **80.72** | 80.4 | ✅ +0.32pp |
| % Functions | **81.45** | 81.1 | ✅ +0.35pp |
| % Lines | **87.91** | 87.6 | ✅ +0.31pp |

- 用例规模：**59 个测试文件 / 930 条用例全部通过**，耗时 27.85s。
- 断言密度：测试文件内 `expect(` 合计 **2165 处**（≈2.3 条/用例）。
- 分母：`all: true` + `include: src/**/*.{ts,tsx}`，实测纳入统计 **114 个源文件**——分母口径是真实全量源码，不是"只算被 import 的文件"（该项历史缺陷已于 R2-QA-03 修正，本次复核成立）。
- 命令注记：`npx vitest run --coverage` 本机**无法完成报告落盘**（工具层 `safe-delete` 沙箱策略拦截清理 `coverage/.tmp` 下 60 个文件，退出码 1，用例本身 930/930 全绿）。改用 `--coverage.reportsDirectory=coverage-qa2 --coverage.reporter=json` 取得 `coverage-final.json`（114 文件）后自行聚合，四指标与官方 text reporter 完全一致（86.75 / 80.72 / 81.45 / 87.91），**非项目缺陷**，如实记录。

### 1.5 与历史基线对比（漂移判定）

| 指标 | README 声称（2026-10-30 / `cedd3e9`） | 本次实测（`e3734a0`） | 判定 |
| --- | --- | --- | --- |
| 无 PG passed | 2211 | **2222** | +11，正常增量 |
| 无 PG skipped | 212 | **235** | **+23**（新增 PG 用例，非退化） |
| 无 PG failed | 0 | **8** | **失真：README 已过期，当前有红** |
| 无 PG 覆盖率 | 85.87% | **85.72%** | −0.15pp；本次合并单位 22728（历史树分母未记录，不可直接换算） |
| 含 PG | 2343 passed / 0 skipped / 91.94% | **2453 passed / 0 skipped / 12 failed / 92.31%** | passed +110、覆盖率 +0.37pp，但**多出 4 条 Windows 事件循环红灯** |
| 前端四指标 | 13.44 / 12.88 / 6.60 / 13.43 | **86.75 / 80.72 / 81.45 / 87.91** | 完全不同于历史，H1 补测战役成效显著（见 §5） |

> 结论：README §一 的"2211 passed / 212 skipped / **0 failed**"**已不成立**——收集数 2423 → 2465（+42），其中 23 条转 skip、19 条转执行，而 19 条里有 8 条红。README 未同步，属陈数。

---

## 二、覆盖率分布分析（后端，无 PG 口径）

### 2.1 按层聚合（合并单位 = 语句 + 分支，按"无 PG"覆盖率升序）

| 层 | 文件 | 合并单位 | 无 PG 覆盖 | 含 PG 覆盖 | 差 |
| --- | --- | --- | --- | --- | --- |
| **db** | 64 | 7111 | **72.21%** ← 最薄弱 | **90.49%** | +18.28pp |
| main.py | 1 | 208 | 74.52% | 74.52% | 0（两口径同为最弱点） |
| middleware | 4 | 142 | 83.10% | 94.37% | +11.27pp |
| plugins | 14 | 1888 | 85.70% | 86.76% | +1.06pp |
| tools | 7 | 511 | 85.91% | 85.91% | 0 |
| api | 21 | 851 | 86.84% | 95.53% | +8.69pp |
| clients | 3 | 287 | 88.50% | 88.50% | 0 |
| services | 13 | 2394 | 91.60% | 91.60% | 0 |
| registry | 2 | 187 | 92.51% | 92.51% | 0 |
| core | 15 | 1556 | 93.38% | 93.51% | +0.13pp |
| business | 34 | 5685 | 93.67% | 95.16% | +1.49pp |
| workers | 6 | 429 | 95.57% | 95.57% | 0 |
| schemas | 20 | 1477 | 99.46% | 99.46% | 0 |
| **合计** | 205 | 22728 | **85.72%** | **92.31%** | **+6.59pp** |

**db 内部再拆：`db/repositories` 57 文件 / 6202 单位 / 无 PG 69.27% → 含 PG 89.63%**，`db/` 其余 7 文件 92.30%。

**两条结论：**
1. **无 PG 口径下 85.72% 完全由 db 层独力拉低**：除 db / main.py / middleware 外，全部 10 个层都在 83% 以上，business/core/services 已在 91–94%。85% 门禁的成败押在 PG 仓储实现上。
2. **但这不是"PG 没测试"**：带上 `--run-pg` 后 db 层从 72.21% 跳升至 90.49%，`postgres.py` 从 27.93% 跳到 90.66%。**差距全部来自"默认命令不跑 PG 用例"**，所以问题定性应从"PG 覆盖盲区"改写为"默认口径不含 PG、且本地无一键入口"（T-02 修正表述）。

### 2.2 低覆盖模块清单（**无 PG 口径**下 <40%，共 9 个，全部为 PG 实现）

| 文件 | 无 PG | 合并单位 | 含 PG |
| --- | --- | --- | --- |
| `app/db/repositories/billing/postgres.py` | 22.61% | 283 | **87.28%** |
| `app/db/repositories/task/postgres.py` | **16.25%** | 277 | **91.70%** |
| `app/db/repositories/plugin/postgres.py` | 25.38% | 197 | **95.94%** |
| `app/db/repositories/org/postgres.py` | 24.52% | 155 | **89.03%** |
| `app/db/repositories/role/postgres.py` | 30.63% | 111 | **89.19%** |
| `app/db/repositories/conversation/postgres.py` | 33.02% | 106 | **83.02%** |
| `app/db/repositories/usage/postgres.py` | 30.61% | 98 | **87.76%** |
| `app/api/v1/memory.py` | 34.38% | 64 | **90.62%** |
| `app/api/v1/tenant_data.py` | 38.00% | 50 | **96.00%** |

> 7 个 `postgres.py` 合计 **1227 单位**，无 PG 口径 16%–33%；**带 `--run-pg` 后全部跳到 83%–96%，两个 api 文件也升到 90.62% / 96.00%**。也就是说这 9 个"低覆盖模块"**不是质量缺口，而是口径缺口**——它们是本次审计里最容易误判的一处（看默认覆盖率会得出"PG 实现裸奔"的错误结论）。
>
> 带上 PG 后全仓仍有 **22 个文件 <85%**，最低 5 个是：`workers/queue_depth.py` 52.38%（21 单位）、`tools/wechat_mp_connector.py` 69.60%、`db/repositories/audit_repo.py` 74.16%、`core/logging.py` 74.24%、`main.py` 74.52%。规模都在 21–208 单位，属"边角持久化/连接器"，不构成系统性风险。

### 2.3 0% 模块清单

**后端：0 个。** 205 个源文件在无 PG 口径下**全部至少被执行过一次**（这与 README 的历史描述不同，属正向结果）。

**前端：10 个文件行覆盖为 0（按源码行数排序）**

| 文件 | 源码行数 | 说明 |
| --- | --- | --- |
| `src/components/workflow/artifact-card.tsx` | 280 | 产物卡片（下载/预览入口） |
| `src/components/workbench/declaration-form.tsx` | 235 | 声明驱动表单（6 个插件共用） |
| `src/components/workbench/file-picker.tsx` | 226 | 上传/选择器 |
| `src/components/workbench/help-panel.tsx` | 110 | 帮助面板 |
| `src/components/workbench/submit-bar.tsx` | 98 | 提交栏 |
| `src/components/workbench/confirm-bar.tsx` | 95 | 确认栏 |
| `src/components/plugin/category-bar.tsx` | 96 | 分类筛选条 |
| `src/app/layout.tsx` | 42 | 根布局 |
| `src/app/(main)/workbench/[pluginId]/loading.tsx` | — | 骨架屏 |
| `src/app/(main)/workflows/[taskId]/loading.tsx` | — | 骨架屏 |

合计约 **1182 行源码零覆盖**，其中 `components/workbench/*` 全目录 5 文件 **0%**（见 §3 T-04）。

### 2.4 "代码量大但覆盖低" TOP15（按 `单位 × (1−覆盖率)` 降序）

| # | 文件 | 覆盖率 | 合并单位 | 未覆盖≈ |
| --- | --- | --- | --- | --- |
| 1 | `db/repositories/task/postgres.py` | 16.25% | 277 | 232 |
| 2 | `db/repositories/billing/postgres.py` | 22.61% | 283 | 218 |
| 3 | `db/repositories/plugin/postgres.py` | 25.38% | 197 | 147 |
| 4 | `db/repositories/org/postgres.py` | 24.52% | 155 | 117 |
| 5 | `db/repositories/memory/postgres.py` | 52.94% | 187 | 88 |
| 6 | `db/repositories/audit_repo.py` | 52.25% | 178 | 84 |
| 7 | `db/repositories/job_repo.py` | 67.19% | 256 | 84 |
| 8 | `services/model_service.py` | 87.54% | 666 | 83 |
| 9 | `db/repositories/knowledge_base_repo.py` | 71.73% | 283 | 80 |
| 10 | `plugins/engine.py` | 78.18% | 362 | 79 |
| 11 | `db/repositories/role/postgres.py` | 30.63% | 111 | 77 |
| 12 | `db/repositories/document_repo.py` | 71.97% | 264 | 74 |
| 13 | `db/repositories/conversation/postgres.py` | 33.02% | 106 | 71 |
| 14 | `db/repositories/grant_repo.py` | 68.18% | 220 | 69 |
| 15 | `db/repositories/usage/postgres.py` | 30.61% | 98 | 68 |

> 15 条里有 **13 条在 `db/repositories`**。其中 `job_repo.py` 未覆盖 84 单位尤其值得注意——因为本轮 8 条红灯正落在它的 `InMemoryJobStore` 语义上（T-01），说明"这两个模块既红又薄"。

### 2.5 前端目录聚合（v8 报告原文，114 文件全量分母）

| 目录 | Stmts | Branch | Funcs | Lines | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/components/workbench` | **0** | **0** | **0** | **0** | 5 文件 676 行，整目录零覆盖（T-04） |
| `src/components/workflow` | 54.90 | 42.85 | 58.97 | 54.08 | `artifact-card.tsx` 0% 拉低 |
| `src/app`（根） | 70 | 100 | 80 | 70 | `layout.tsx` 0% |
| `src/components/ui` | 71.96 | 68.64 | 71.77 | 74.59 | skeleton 6.25 / date-range-picker 37.78 / pagination 54 / tabs 69.76 |
| `src/components/chat` | 78.83 | 71.91 | 72.91 | 78.78 | chat-window 64.7 行 |
| `src/components/plugin` | 85.00 | 90.05 | 77.77 | 90.09 | category-bar 0% |
| `src/components/knowledge` | 88.38 | 77.11 | 74.46 | 89.86 | |
| `src/components/layout` | 89.07 | 79.72 | 78.57 | 90.09 | sidebar 54.16 行 |
| `src/components/billing` | 90.56 | 88.60 | 85.00 | 94.00 | |
| `src/components/org` | 90.22 | 80.48 | 90.69 | 92.43 | |
| `src/components/ops` | 94.73 | 91.66 | 90.00 | 97.22 | |
| `src/hooks` | 98.88 | 91.26 | 100 | 99.69 | 8 个 Hook 全部 ≥96 |
| `src/lib` | 98.63 | 93.59 | 98.94 | 98.88 | api-client 97.59 |
| `src/components/{common,memory,brand}` | 100 | 100/100/71.42 | 100 | 100 | |
| **全部** | **86.75** | **80.72** | **81.45** | **87.91** | 930 用例 / 59 文件 |

**前端唯一的结构性缺口就是 `components/workbench`（0%）与 `artifact-card`（0%）**——其余 11 个目录全部 ≥70%，`lib`/`hooks` 已接近满分。这与后端"唯一拖累是 db 层"是同一类形态：**整体已高，缺口集中且可指名**。

---

## 三、测试有效性问题（分级）

### 高

**T-01｜HEAD 上有 8 条确定性红灯，且是"时间炸弹"，README 声称 0 failed —【高】**

- 证据：§1.2 的 8 条 `test_job_runner.py` 全红，`tick()` 返回 0。
- 根因（实测探针，非推测）：

```
enqueue 后 run_after= 2026-11-01T16:37:19Z   ← 墙钟（datetime.now(UTC)）
测试注入时钟 now  = 2026-11-01T09:00:00Z   ← 用例硬编码
claim(worker_id='w') → claimed = 0
把注入时钟推进到 2026-11-02T09:00Z → claimed = 1
```

- 定位：`app/db/repositories/job_repo.py::InMemoryJobStore.enqueue` 用 `now_iso()`（墙钟）写 `run_after`/`created_at`，而 `claim()` 读的是注入时钟 `self._now()`。**同一存储类里两个时间源不一致**，类注释声称的"时钟可注入"契约在写侧失效。
- 测试侧无过错：断言（`tick()` 应认领到 1 条）符合设计语义。
- **危害**：该用例硬编码 `2026-11-01T09:00Z`，只要墙钟越过该时刻，红灯**永久成立**（不是偶发抖动）；今天正好越过，因此表现为"突然 8 红"。这也解释了 README 的 0 failed 为何"曾经是真的"。

**修复范围（已与 Engineer 交叉复验后修正，勿只改 `enqueue`）**

墙钟 `now_iso()` 在 `InMemoryJobStore` 内共 **4 处**调用点，全部需改为走注入时钟：

| 行 | 位置 | 写入字段 |
| --- | --- | --- |
| `:303` | `enqueue` | `run_after` / `created_at` / `updated_at`（**唯一直接致病点**） |
| `:355` | `claim` | `item.updated_at` |
| `:368` | `mark_succeeded` | `updated_at` / `finished_at` |
| `:391` | `mark_failed` | `updated_at` / `finished_at` |

**关键补充：有两个方法在"单个方法内部"就已经混用两个时间源**（这是本次复验新发现的，比原判断更严重）：

- `mark_failed`：`:390 current = (now or self._now())`（注入时钟）算 `run_after`，`:391 stamp = now_iso()`（墙钟）算 `updated_at`/`finished_at` → 同一条记录上 `updated_at` 比 `run_after` 晚一年（本地实测差 ≈7h40m，随时间放大）。
- `claim`：`locked_until` 由注入的 `current` 派生（`:352`），但 `updated_at` 走墙钟（`:355`） → 同一行内 `updated_at` 与 `locked_until` 不同源。

因此"只修 enqueue 让 8 条转绿"是**能过测试但留错实现**：任何将来按 `updated_at` 排序/比较的用例会再炸一次。建议口径（Engineer 已提出，本审计认可）：加一个私有 `_stamp()` 统一出口，4 处全替换。

**两个实现约束（本审计已逐条核实）**

1. 模块级 `now_iso()` **只能绕开、不能动**——但约束来源要说准（本轮已用导入链解析复核，避免误判）：

   - `job_repo.now_iso` 本身**没有任何导入方**：AST 解析全仓 `from … import now_iso` 共 **17 处导入**，逐条回溯终点模块，**回溯到 `app.db.repositories.job_repo` 的条数 = 0**。
   - 之所以"看起来有很多调用方"，是因为**同一个 2 行函数在 7 个模块各有一份副本**（见 T-12）：`artifact_repo` / `audit_recorder` / `billing.records` / `conversation.records` / `plugin.records` / `task.records` / `job_repo`。逐个来源核对如下：

     | 导入方 | 直接来源 | 终点 | 是否 job_repo |
     | --- | --- | --- | --- |
     | `business/audit_service` | `audit_recorder` | 自定义 | ❌ |
     | `business/ops_service` | `billing` → `billing.records` | 自定义 | ❌ |
     | `business/order_service` | `billing` → `billing.records` | 自定义 | ❌ |
     | `business/subscription_service` | `billing` → `billing.records` | 自定义 | ❌ |
     | `business/plugin_service` | `plugin` → `plugin.records` | 自定义 | ❌ |
     | `business/task/runner` | `task` → `task.records` | 自定义 | ❌ |
     | `billing/{__init__,memory,postgres}`、`ops_repo` | `billing.records` | 自定义 | ❌ |
     | `conversation/{__init__,memory}` | `conversation.records` | 自定义 | ❌ |
     | `plugin/{__init__,memory}`、`plugin_review_repo` | `plugin.records` | 自定义 | ❌ |
     | `task/{__init__,memory}` | `task.records` | 自定义 | ❌ |

   - **修复结论不变**：既不要删、也不要改语义。两条独立理由——① `job_repo.now_iso` 在 `__all__`（`:748`）里，属声明式公共 API；② 仓内有 7 份同名副本，任何"改 `now_iso`"的动作都需先确认改的是哪一份，改错会静默影响其他域。**正确做法是新增私有 `_stamp()`，完全不触碰模块级函数。**
2. **不存在同型缺陷的其他仓储**（结论附扫描口径，便于下轮复用）：
   - 口径 A：`grep -rn "self\._now = " app/ --include="*.py"` → **1 命中**（`app/db/repositories/job_repo.py:279`）。
   - 口径 B：`grep -rn "now: Callable" app/db/repositories/ --include="*.py"` → **1 命中**（`job_repo.py:272` 的 `def __init__(self, *, now: Callable[[], datetime] | None = None)`）。
   - 口径 C（AST 反向确认，排除"换了变量名"）：解析 `app/db/repositories/**/*.py` 全部 `ClassDef.__init__` 的形参，凡名字含 `now` / `clock` / `time` 的类 → **仅 1 个**：`InMemoryJobStore(self, *, now)`。
   - 三口径一致，故"可注入时钟"模式**只在这一个类落地**；其他内存仓储（billing / conversation / task / plugin）一律走墙钟且**没有**注入能力，因此**不可能**出现"注入时钟 vs 墙钟不同源"的同型缺陷。覆盖面很窄，非普遍缺陷。

**测试侧建议（已修正）**：**不要**把测试里硬编码的 `datetime(2026…)` 一刀切改成相对时间——`tests/` 里共 **35 处 / 13 个文件**，绝大多数当"固定数据时间戳"用（保留期基准、创建时间），改成相对时间反而降低断言确定性。真正的判据只有一条：**该时间是否被拿去和未注入的墙钟比较**。只有这类才会 rot；源码修好后 `test_job_runner._Clock` 的硬编码初值反而应当保留（确定性更好）。

**T-02｜默认口径余量仅 +164 单位，而它把 db 层 28% 的单位按未覆盖计入分母 —【高】**

- 证据：无 PG 余量 **+164 单位（+0.72pp）**；带 PG 余量 **+1661 单位（+9.6pp）**——**相差 10 倍**。
- 含义：仓库默认的本地命令（不设 `OA_TEST_DATABASE_URL`）跑出来的"85.72% 刚好过线"，是**把 db 层 28% 的单位按未覆盖计入分母**的结果；一旦按正确口径跑，真正的覆盖是 92.31%。两个数字都真实，但**"擦线过"与"稳过"是两种质量信号**，对外只报前者会严重低估，只报后者会掩盖"本地默认跑不全"。
- 更危险的是反方向：因为余量只有 164 单位，任何人新增 200 单位 PG 代码却没同步 PG 用例，默认口径立刻破 85% 红线——**红线会被"口径不足"而不是"质量下降"触发**，长期会诱导团队写低价值用例来喂分母。
- CI 已有针对该盲区的门禁 5b（`--include="*/db/repositories/*/postgres.py" --fail-under=50`），但**下限 50% 未经校准**：实测带 PG 为 **90.66%**，50% 这个数只能证明"PG 用例跑过"，证明不了"PG 实现被覆盖"。

**T-03｜门禁脚本 `validate_deps.py` 在 HEAD 是红的（pyjwt 未进白名单），而"门禁接线"门禁自己是绿的 —【高】**

- 证据（本次实跑）：
```
[FAIL] pyjwt：未登记进 §8.1 清单或未加入 DOCUMENTED_PACKAGES（须先补架构文档，再加进本脚本白名单）
[FAIL] 依赖门禁不通过（1 项）        EXIT=1
```
- 根因：`docs/architecture-v1.0.md:1398` 已登记 `pyjwt`（2026-10-30 由 python-jose 迁入），`pyproject.toml` 也已声明；但 `deploy/validate_deps.py` 的硬编码集合 `DOCUMENTED_PACKAGES` 仍是 `python-jose`，未同步。
- 性质：**P0 级安全整改（JWT 库迁移）动了 pyproject + 架构文档，却没动门禁白名单**——三处真源，只改了两处。而 `validate_gate_wiring.py` 只校验"脚本是否被 workflow 引用"，校验不了"脚本当前是否为绿"，于是体系自证健康。
- 该红在 CI 中会直接失败（`.github/workflows/ci-adapter.yml:758`），说明 CI 确实没跑过（与 README §六.6 自述"Actions 额度已用尽"一致）。

### 中

**T-04｜前端 `components/workbench` 整目录 0 覆盖，而宿主页 92% 是靠 `vi.mock` 打桩子组件换来的 —【中】**

- 证据：
  - `workbench/[pluginId]/page.tsx` 行覆盖 **92.63%**；
  - 同目录 5 个子组件 `declaration-form`(235 行)、`file-picker`(226)、`help-panel`(110)、`submit-bar`(98)、`confirm-bar`(95) **全部 0%**；
  - `page.test.tsx` 第 62/96/122/144 行把上述 4 个组件整体 `vi.mock` 掉，替身以"手写 props 类型"回显。
- 危害：页面 ↔ 子组件的**接缝（props 名/回调签名）在单测中完全不可见**；替身的 props 类型是重新声明的，不与真组件共享类型，因此 `tsc` 也拦不住改名。`file-picker` 连替身都没有，属纯粹 0。
- 同类：`workflow/artifact-card.tsx`（280 行，产物下载入口）0%；`plugin/category-bar.tsx`（96 行）0%；`ui/skeleton.tsx` 6.25%。
- 缓解事实（不改变结论）：子组件多为"展示 + 回调"型，Playwright E2E 可兜底——但 `apps/web/e2e` 仅 3 个 spec / 163 行，且需真起栈（本轮未运行）。

**T-05｜`tests/e2e` 在默认命令下完全不执行，"e2e" 名不副实 —【中】**

- 证据：`tests/e2e` **10 文件 / 27 个用例**，`pytestmark = [e2e, pg]`；无 PG 口径下 **24 条 skip**（仅 3 条非 pg 用例执行）。skipped 分布：integration 209 / e2e 24 / security 2 = 235。
- 补跑核实：带 `--run-pg` 时 **27 条全部执行且全部通过**，覆盖 content/ dev/ hr/ market/ memory/ failure-retry 六类岗位工作台流。也就是说 e2e 目录**质量没问题，问题在"默认跑不到"**。
- 语义核实：e2e 用 `httpx.AsyncClient` 打进程内 ASGI + 真 PG，下游按 `OA_DOWNSTREAM_ENABLED=false` 全桩 → 与 README 承认的"沙箱/契约桩"一致，**没有任何真实下游回执验证**。目录名会让读者误以为有跨进程端到端，建议改名（如 `tests/scenario`）或另补一条真起栈的 e2e。

**T-06｜覆盖率棘轮阈值 = 实测 − 0.3pp，且"补测批次"同时修真实缺陷（双刃） —【中】**

- `vitest.config.mts` 阈值 86.4/80.4/81.1/87.6 对实测 86.75/80.72/81.45/87.91，各留 ~0.3pp——纪律（只升不降）成立，但**防回退粒度只有 0.3pp**，一次普通重构即可吃掉。
- 反向正面证据（值得记录）：36 批补测的 commit 注释里至少 3 次记明"补测发现并修复真实缺陷"——SSE 事件分发 `handlerKeyFor` 映射失配（第十批）、工作流 Timeline 非失败步骤「详情」永远展不开（第十三批）、`billing/chat` 页 `quotaWarning` 空体取 `usage_percentage` 白屏（第二十三批）。**说明这批补测不是刷数字**，覆盖率爬坡伴随真实缺陷发现。
- 需注意：这些修复的回归保护依赖补测用例本身，而补测用例大量依赖 `vi.mock`（T-04），防线强度需打折。

**T-11｜带 PG 口径在 Windows 本机多出 4 条"事件循环已关闭"红灯 —【中】**

- 证据：`test_org_repo_pg`（1 条）、`test_usage_repo_pg`（3 条）全红，报错统一为 `RuntimeError: Event loop is closed`（`ProactorEventLoop running=False closed=True`），堆栈经 `app/db/session.py::tenant_scope`。
- 定性：**测试基础设施缺陷**（Windows 事件循环 + asyncpg 连接池 + pytest-asyncio 生命周期），不是从断言看出的产品缺陷；Linux CI 上大概率不复现。
- 危害：修好之前，"PG 口径 0 failed"这条判据在 Windows 本机不可用。团队若以本机 PG 结果放行，会长期看到 4 条噪声；若因此关掉 `--run-pg`，则退回 T-02 的盲区。建议：PG 夹具改函数级 engine + 显式 `dispose`，或 Windows 下统一 `SelectorEventLoop`。

### 低

**T-07｜断言质量总体良好（正向结论）**

- 全仓 `grep`：`assert True` = **0**；`pytest.mark.xfail` = **0**（`EXPECTED_XFAIL = 0`，白名单已清空，无长期挂账）；`pytest.mark.skip` = **0**（唯一 skip 机制是 `pg` 标记 → 环境性 skip）。
- 断言强度：`status_code` 断言 275 处，`json()["…"]` 字段断言 430 处，`.json()` 调用 683 处——**状态码断言大多成对配 body 断言**，"只看 200"不是主流。
- mock 用量：`patch(`/`monkeypatch.setattr` 合计 **160 处**（测试总量 32692 + 26764 行）——被测逻辑基本走真实的 DI 容器与内存实现，**不存在"把被测逻辑 mock 掉"的系统性问题**。
- 抽样 10 个文件（unit 4 / integration 3 / e2e 2 / security 1）结论：`tests/security/test_permission_fail_closed.py`、`tests/integration/test_endpoint_baseline.py`、`tests/unit/test_config_env_names.py` 等均为"断言 + 反例 + 锁文件"三重钉，质量高于一般商用项目水平。

**T-08｜裸跑陷阱：2 个门禁脚本裸跑必红（与 L9 同类）**

- `deploy/validate_batch_clock.py` 裸跑 EXIT=2（缺 `OA_BATCH`，"缺失即红"设计）；`deploy/validate_import_directions.py` 裸跑 EXIT=1（缺 `--baseline`，报"基线外 23 条"，带参数后 EXIT=0）。两者都会让"本地随手一跑"得出假红。

**T-09｜加载态/骨架屏从未被测**

- 前端 `app/layout.tsx`（42 行）、两个 `loading.tsx`、`ui/skeleton.tsx`（6.25%）零/近零覆盖；`ui/date-range-picker.tsx` 37.78%。属"首屏体验"类缺口，不影响主流程正确性。

**T-10｜235 / 2465（9.5%）用例默认不执行，且全部集中在同一维度**

- 单一原因（`需要真实 PostgreSQL`）本身是**合理的环境性 skip**，不是"永久 skip 掩盖问题"——这点值得肯定。但它意味着**生产主路径（PG）默认不在本地防线上**，与 T-02 是同一枚硬币的两面。

**T-12｜`now_iso()` 这个 2 行函数在仓储层被复制了 7 份 —【低】**

- 证据（`grep -rn "^def now_iso" app/` + AST 双口径）：`artifact_repo.py:99`、`audit_recorder.py:31`、`billing/records.py:143`、`conversation/records.py:42`、`job_repo.py:75`、`plugin/records.py:69`、`task/records.py:88` —— **7 个独立定义，函数体逐字相同**（`return datetime.now(UTC).isoformat().replace("+00:00","Z")`）。
- 影响①（重复代码）：任何"改时间语义"的整改（改用可注入时钟、改精度、加单调性）都要改 7 个地方，漏一处就出现"两个域时间口径不一致"。
- **影响②（统计陷阱——本条真正的价值，不只是重复代码）**：`now_iso` 在仓内是**「同名 7 个定义 + 17 处导入」**的结构，因此**任何以名字为键的统计都会给出错误结论**。本轮就发生过一次真实误判：用 `grep now_iso` 得到"10+ 处调用方"，据此推断"`job_repo.now_iso` 有大量依赖、不能动"，而逐跳回溯后**指向 `job_repo` 的依赖为 0**。
  推论（供后续审计/工具复用）：
  - 对同名多定义符号，**名字级 grep 不能作为"依赖面"的证据**，必须按 `模块 → 定义/再导出` 逐跳回溯到终点（本次做法：解析 `from X import now_iso`，若 X 未 `def` 则继续回溯 X 的来源，直到终点模块）；
  - 反过来，名字级统计**可以**用来发现"是否被复制/散落"——即"7 份"这个结论恰恰只能靠名字级统计得出。**同一份数据，两种问法要用两种口径**，混用就会得出相反结论；
  - 任何新增的 `validate_*.py` 门禁若以符号名做一致性校验，必须先把"多定义"情形纳入判据，否则会产生假红/假绿。
- 建议：下沉到 `app/db/repositories/base.py` 统一导出一份（该文件已存在、被 25 个模块以 `from app.db.repositories.base import …` 引用，是本层天然的公共落点；当前它只在文档字符串里提到 `now_iso()`，**既未定义也未导入**，正好可以补上），其余 6 处改为 `from .base import now_iso`。属技术债，不阻塞交付。
- 注：本条与 T-01 的修复**无耦合**——T-01 建议新增私有 `_stamp()`，不需要动这 7 份副本。

---

## 四、门禁脚本实跑结果（`deploy/validate_*`，17 项）

| # | 脚本 | 裸跑 EXIT | 正确用法 EXIT | 结论 |
| --- | --- | --- | --- | --- |
| 1 | validate_batch_clock.py | 2 | **0**（`OA_BATCH=B21`） | 裸跑必红（设计使然）；带 env 通过，台账 18 条未修复 0 |
| 2 | validate_config_names.py | 0 | 0 | 通过（G-35/G-36） |
| 3 | validate_deploy.py | 0 | 0 | 通过（G-34） |
| 4 | **validate_deps.py** | **1** | **1** | **FAIL：pyjwt 未登记 DOCUMENTED_PACKAGES**（T-03） |
| 5 | validate_gate_wiring.py | 0 | 0 | 通过：16 个脚本均被 workflow 引用 |
| 6 | validate_images.py | 0 | 0 | 通过 |
| 7 | validate_import_directions.py | **1** | **0**（`--baseline …`） | 裸跑报"基线外 23 条"；带基线后 23 条全在基线内、3 环均在基线 |
| 8 | validate_migrations.py | 0 | 0 | 通过（11 条迁移） |
| 9 | validate_offline_pkgs.py | 0 | 0 | 通过（1 包 / 38 依赖 / 失败 0） |
| 10 | validate_openapi.py | 0 | 0 | 通过（G1–G7；core 30 / phase2 38 / phase3 16 / phase4 10 / w1 2） |
| 11 | validate_packaging.py | 0 | 0 | 通过（26 包全收录） |
| 12 | validate_pg_markers.py | 0 | 0 | 通过（PG 夹具用例全带 `pg` 标记） |
| 13 | validate_plugins.py | 0 | 0 | 通过（6 份声明） |
| 14 | validate_repository_parity.py | 0 | 0 | 通过（20 单元 / 0 FAIL） |
| 15 | validate_schema_retention.py | 0 | 0 | 通过（180 天口径） |
| 16 | validate_secrets.py | 0 | 0 | 通过（无明文兜底） |
| 17 | validate_lite_up.sh `--static-only` | 0 | 0 | 通过（36 条静态断言）；未起栈 |

**统计：17 项中 15 项通过，1 项真失败（validate_deps），1 项裸跑必红（batch_clock，设计使然）；另有 1 项（import_directions）裸跑假红。**
与任务书预期的"约 27 个"不符：仓库内实际只有 **16 个 `validate_*.py` + 1 个 `validate_lite_up.sh`**（`find` 全仓确认，已排除 `node_modules` / `.venv`）。`validate_gate_wiring.py` 自报"16 个 validate_*.py 全部被 workflow 引用"，与本次清点一致。

---

## 五、前轮问题闭环核实

| 前轮编号 | 问题 | 本轮实测 | 判定 |
| --- | --- | --- | --- |
| **H1** | 前端覆盖极薄：13.44 / 12.88 / 6.60 / 13.43，8 个 300+ 行文件 0 覆盖 | 见下表逐项 | **已闭环（超额）** |
| **M4** | PG 覆盖盲区：212 条 PG 用例默认 skip，org/role/usage 的 `postgres.py` 无独立 PG 用例；CI 5b 下限 50% 未校准 | 默认 skip 现为 **235** 条（+23）；`org/role/usage` 均有独立 PG 用例文件；**带 `--run-pg` 实跑：0 skip、`postgres.py` 90.66%、db 层 90.49%**，5b 的 50% 下限对应实测 90.66% | **用例侧已闭环**（覆盖率证明 PG 实现在测）；**但口径侧未闭环**：本地无默认入口、5b 下限仍写 50%、且 Windows 上多 4 条事件循环红灯 |
| **M6** | `--cov-fail-under=85` 只在 CI，pyproject 无 `fail_under`，本地裸跑无门槛 | `pyproject.toml` `[tool.coverage.report] fail_under = 85` **已声明**；本次裸跑即按 85% 判定（输出 `Required test coverage of 85.0% reached`） | **已闭环** |
| **L9** | 裸跑 pytest 缺 `OA_BATCH` 即 1 红 | 仍在，且本次发现同类 **3 处**：pytest 缺 `OA_BATCH`、`validate_batch_clock.py` 裸跑 EXIT 2、`validate_import_directions.py` 裸跑 EXIT 1 | **未闭环（范围扩大）** |

### H1 逐项核实（8 个大文件 + api-client，本次实测）

| 文件 | 源码行数 | Stmts | Branch | Funcs | Lines | 前轮状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `app/(main)/team/page.tsx` | 732 | 92.59 | 90.65 | 83.67 | **94.53** | 0 → 94.53 |
| `app/(main)/plugins/[pluginId]/page.tsx` | 668 | 88.55 | 88.89 | 75.44 | **90.08** | 0 → 90.08 |
| `app/(main)/billing/page.tsx` | 492 | 89.09 | 85.57 | 78.26 | **90.00** | 0 → 90.00 |
| `app/(main)/team/organization/page.tsx` | 461 | 91.35 | 90.77 | 82.05 | **92.71** | 0 → 92.71 |
| `app/(main)/workbench/[pluginId]/page.tsx` | 449 | 92.45 | 94.90 | 80.00 | **92.63** | 0 → 92.63 |
| `app/(main)/plugins/page.tsx` | 447 | 83.12 | 76.47 | 66.67 | **83.33** | 0 → 83.33 |
| `app/(main)/knowledge/[baseId]/page.tsx` | 400 | 82.52 | 75.93 | 70.97 | **84.04** | 0 → 84.04 |
| `app/(main)/profile/memories/page.tsx` | 368 | 87.64 | 90.00 | 73.53 | **88.61** | 0 → 88.61 |
| `lib/api-client.ts` | 1593 | 97.61 | 88.10 | 99.15 | **97.59** | 40% → 97.59 |

**H1 判定：闭环。** 8 个文件从 0 覆盖升到 83–95%，`api-client.ts` 从 40% 升到 97.59%。四指标从 13/12/6/13 抬到 86/80/81/87，36 批补测合计 930 条用例。

**但 H1 的闭环留下了新缺口**：`components/workbench` 整目录 0%、`artifact-card` 0%（T-04）——这恰是"按页面维度补测、用 `vi.mock` 打掉子组件"的必然副产物。补齐方式是给这 6 个子组件补组件级用例，而不是继续加页面用例。

---

## 六、该维度总体结论

**结论：测试体系"厚度够、口径缺、门禁虚"；测试本身写得比大多数商用项目扎实，但当前 HEAD 挂着 12 条红灯、门禁有 1 项真红从未被发现——不能支撑"商用无阻塞"的表述。**

1. **不能宣称质量绿灯**：HEAD 上**无 PG 口径 8 红 / 含 PG 口径 12 红**，README 的"0 failed"已过期。8 条是**实现缺陷**（`InMemoryJobStore.enqueue` 用墙钟、`claim` 用注入时钟），且是"时间炸弹"型——越过 2026-11-01T09:00Z 后永久成立；另 4 条是 Windows 事件循环夹具缺陷。两者都必须修，否则任何后续真回归都会被这 12 条噪声掩盖。
2. **覆盖率本身其实是好的，坏在"默认口径"**：带 `--run-pg` 实测 **92.31%**（余量 +1661 单位），`postgres.py` 90.66%，db 层 90.49%——**PG 主路径并不裸奔**。但仓库默认命令跑出来只有 85.72%（余量 +164 单位），因为它把 28% 的 PG 单位按未覆盖计入分母。也就是说：**对外报 85.72% 会严重低估质量，而只看 85.72% 又会让团队误以为"PG 实现没测"**。真实缺陷是**"没有一键带 PG 的本地入口"**，不是测试写得少。
3. **前端是全国最亮的一块**：四指标 86.75 / 80.72 / 81.45 / 87.91，930 条用例，H1 的 8 个大文件从 0 覆盖升到 83–95%，`api-client.ts` 40% → 97.59%。唯一结构性缺口是 `components/workbench` 整目录 0%（page 靠 `vi.mock` 打桩换来 92%），属"页面维度补测"的副产物，补齐方式明确。
4. **测试自身的有效性是好的（正向结论）**：0 处 `assert True`、0 处永久 skip、xfail 已清零（`EXPECTED_XFAIL = 0`）、mock 用量极低（160 处 / 5.9 万行测试）、安全与权限用例用"锁文件 + 反例 + 审计留痕"三重钉。**问题不在"测试写得假"，而在"跑不全 + 门禁没人跑"。**
5. **最大系统性风险是"门禁存在 ≠ 门禁在跑"**：17 项门禁本机实跑 1 项真红（`validate_deps` pyjwt 未登记）、1 项裸跑假红（`import_directions` 缺 `--baseline`），而 `validate_gate_wiring` 仍自证健康——它只查"接线"，不查"结论"。CI 从未实跑（README 自述额度用尽），意味着**所有绿灯都是本机口径的一次性产物**，没有持续防线的证据。

**放行建议（按优先级）**
- P0：修 `job_repo.InMemoryJobStore` 的时间源——**4 处墙钟调用点统一走 `_stamp()`**（`:303`/`:355`/`:368`/`:391`，只改 `enqueue` 会让 8 条转绿但留下一行内 `updated_at` 与 `locked_until` 不同源的错实现）；清掉 8 红。修 `validate_deps.py` 白名单加 `pyjwt`。
- P1：把 `OA_TEST_DATABASE_URL` + `--run-pg` 纳入本地默认入口（一条 `make test` / npm script 即可），并在 README 里以 **92.31%** 为对外口径、85.72% 标注为"无库降级口径"。
- P1：CI 5b 下限由 50% 校准到 **88%**（实测 90.66%）；修 Windows 事件循环夹具（4 条）。
- P2：补 `components/workbench` 6 个组件级用例（约 1182 行 0 覆盖）；`main.py`（74.52%）与 `queue_depth.py`（52.38%）补边角用例。

**结论一句话**：**测试可支撑商用——前提是先清掉 HEAD 上的 12 红、修掉门禁假象，并把 PG 口径变成默认而非可选。** 当前状态应视为 **QA 阻塞（P0×2）**。

---

## 附录 A：命令与产物

| 用途 | 命令 | 产物 |
| --- | --- | --- |
| 后端无 PG | `OA_BATCH=B21 .venv/Scripts/python.exe -m pytest tests -q --cov=app --cov-report=term-missing --cov-report=json:coverage-audit.json` | `apps/adapter/coverage-audit.json`（205 文件）；耗时 1210s |
| 后端含 PG | `OA_BATCH=B21 OA_TEST_DATABASE_URL=postgresql://officeai_app:***@127.0.0.1:55433/officeai … -m pytest tests -q --run-pg --cov=app --cov-report=json:coverage-pg.json` | `apps/adapter/coverage-pg.json`（205 文件）；耗时 1467s |
| 前端 | `node ../node_modules/vitest/vitest.mjs run --coverage --coverage.reporter=json --coverage.reportsDirectory=coverage-qa2` | `apps/web/coverage-qa2/coverage-final.json`（114 文件） |
| 门禁 | `python deploy/validate_*.py`（按 §四 的用法列） | 逐项 EXIT 记录见 §四 |
| PG 连通性探针 | `asyncpg.connect(...)` 查 `current_user / rolbypassrls` | `officeai_app / false` |
| 注入时钟探针（T-01） | `InMemoryJobStore(now=固定2026-11-01T09:00Z)` → `enqueue` → `claim` | `run_after=2026-11-01T16:37Z`，`claimed=0`；推进注入时钟 +1d 后 `claimed=1` |

## 附录 B：本轮未做/无法做的

1. `apps/web/e2e`（Playwright，3 个 spec / 163 行）未运行——需起 Next 服务与浏览器，超出只读审计的时间盒；因此前端"子组件 0 覆盖"的兜底强度**未经实测**（这也是 T-04 定级停在"中"而未升"高"的原因）。
2. `validate_lite_up.sh` 只跑 `--static-only`（36 条静态断言），未真起 9 容器栈。
3. 覆盖率"行级未覆盖行号"仅取 top 模块，未逐文件列全缺失行（无 PG 2347 条 / 含 PG 1113 条）。
4. 未做多轮稳定性复跑（同一命令跑 2 次比对）。但 T-01 的 8 红已用探针证明是**确定性**而非偶发；T-11 的 4 红为夹具级，未验证 Linux 上是否复现。
