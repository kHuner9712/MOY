# 阶段二 W2 批次验证报告（B07 计量落库与 M4 同源 / B08 组织架构与权限点）

| 项 | 内容 |
| --- | --- |
| 报告编号 | QA-P2-W2-R01 |
| 验证批次 | B07（`6eca802`）、B08（`2ef734d`） |
| 冻结 HEAD | **`2ef734d`**（`develop`） |
| QA 工程师 | 严过关（Yan） |
| 验证日期 | 2026-10-22 |
| 判据来源 | `docs/architecture-phase2.md` v2.0 §9.2 B07/B08（1652-1705 行）、§2.5 D-12、§2.6 D-13、§4.4 ID1-ID11、§4.8 权限点矩阵、§7 错误码 14005-14008；`docs/qa-phase2-test-plan.md` v1.3（USG-01~07、PERM-01~08、§2.1 门禁、§7.1 分级）；`docs/qa-phase2-w1-report.md` §5/§7 |
| **最终判定** | **FAIL（不准出）**：13 条验收点 **11 ✅ / 2 ❌**；**P0 = 1、P1 = 2、P2 = 2、P3 = 3**（共 8 条） |
| **路由结论** | **Engineer**（3 个缺陷需修：B07-01 对账审计丢失、B08-01 降级 fail-open、B08-02 部门 ID 形态不一致）；覆盖面补测由 **QA** 已自行完成（本轮 27 条独立用例已入库） |
| 测量口径 | **不接受工程师自报数字**。所有数字为 QA 本机实跑，且**全部在独立干净 worktree `麦子-qa-w2-clean`（detached @ `2ef734d`）上取样**（原因见 §1.1） |

---

## 1. 测量口径与工作区事件

### 1.1 工作区在验证期间被并行修改（已上报主理人）

| 时间（本地） | 事件 |
| --- | --- |
| 起始 | `git status --porcelain` **空**、`HEAD = 2ef734d` → 干净 |
| 03:48–04:00 | 全部门禁与用例在**干净态**实跑完毕（本报告 §2 主体数字） |
| 04:14:51–04:15:45 | 工作区出现 **5 个未提交改动**（非 QA 所为，QA 未清理、未回退）：`app/api/v1/billing.py`、`app/business/knowledge_base_service.py`、`app/business/policy_service.py`、`app/db/repositories/grant_repo.py`、`scripts/reconcile_usage.py` |
| 04:20 起 | QA 另建独立 worktree `麦子-qa-w2-clean`（detached @ `2ef734d`），把 QA 用例拷入后**重跑全部门禁**，以干净副本作为判据 |

说明：上述 5 个改动中，`scripts/reconcile_usage.py` 的注释直接引用了本轮 QA 用例名 `test_b07_5c`，改动内容是把「对账 + 写审计」合并为单事件循环——**即工程师已在按本轮发现同步修复**（对应缺陷 B07-01）。因该批改动**未提交、无哈希**，本报告结论只对 `2ef734d` 有效；若形成新提交，需走第 2 轮回归（§6）。

QA 对源码与既有用例**零修改**：本轮仅新增 `tests/integration/test_qa_b07_b08_verification.py`（属交付项 2）与本报告。

### 1.2 环境

本机 Windows，`apps/adapter/.venv`（Python 3.13.14），PostgreSQL `127.0.0.1:55433/officeai`（应用角色 `officeai_app`，`NOBYPASSRLS`），桩模式 `OA_BUSINESS_STORE=memory` 与 `postgres` 两轮。

---

## 2. 门禁实跑结果（G-01 ~ G-24）

| 门禁 | 命令 | 关键输出 | 退出码 | 判定 |
| --- | --- | --- | --- | --- |
| G-01 ruff lint | `ruff check .` | `All checks passed!` | 0 | ✅ |
| G-02 ruff format | `ruff format --check .` | `137 files already formatted`（含 QA 用例） | 0 | ✅ |
| G-03 mypy | `mypy app` | `Success: no issues found in 86 source files` | 0 | ✅ |
| G-04 全量（非 PG，工程师用例集） | `pytest -q` | `901 passed, 35 skipped, 1 warning in 144.48s` | 0 | ✅ |
| G-04' 全量（PG，工程师用例集） | `pytest -q --run-pg --cov=app` | `936 passed, 1 warning in 268.23s`，**skipped = 0** | 0 | ✅ |
| G-04'' 含 QA 用例（PG，干净副本） | 同上 + `test_qa_b07_b08_verification.py` | `3 failed, 960 passed, 1 warning in 299.58s`，skipped = 0 | 1 | ❌ 3 条失败**全部**是 §4 的缺陷断言 |
| **G-05 覆盖率（总）** | `pytest --run-pg --cov=app --cov-report=term` | `2ef734d`：`TOTAL 8160 869 1602 262 86.55%`；`fdf229f`：`TOTAL 8160 852 1602 255 86.88%` | 0 | ❌ 阈值 **≥90.5%**，差 3.62pp（W-01 观察中） |
| **G-05 覆盖率（新模块）** | `coverage report --include="app/business/*,app/db/*"` | `2ef734d`：`TOTAL 4335 561 924 158 84.35%`；`fdf229f`：`TOTAL 4335 544 924 151 84.96%` | 0 | ❌ 阈值 **≥85%**，差 0.04pp（**W-02 已撤销豁免**，见 §2.1/§2.2） |
| G-06 用例总数 | `pytest --collect-only -q` | 工程师 936 / 含 QA 963 | 0 | ✅ ≥599 |
| G-07 核心端点基线 | `deploy/validate_openapi.py --allow-missing-phase2` | `端点统计: core=30 phase2=21/38 （未实现 0）` | 0 | ✅ core == 30 |
| G-08 新域端点基线 | 同上 | `phase2=21/38` | 0 | ✅ B05 9 + B07 1（BL5）+ B08 11（ID1-ID11） |
| G-09 加性 diff | 同上（G1-G5） | `[OK] G1-G5 全部通过` | 0 | ✅ 0 violation |
| G-10 底座泄漏 | `grep -rniE "dify\|milvus\|litellm\|chat-messages\|5001" app/api app/schemas` | 命中 **0**（`wc -l` = 0） | — | ✅ |
| G-11 迁移幂等 | `pytest tests/integration/test_g11_migration_idempotency.py tests/integration/test_migrate.py --run-pg` | `16 passed` | 0 | ✅ |
| G-12 RLS fail-closed | `pytest tests/unit/test_db_session.py tests/integration/test_tenant_isolation.py --run-pg` | `41 passed` | 0 | ✅ |
| G-13 错误码三处一致 | `scripts/check_error_catalog.py --allow-pending b09` | `[OK] 架构 §7.1 → errors.py: 5/14 已落地（9 个按批次放行）`；`errors.py 43 码全部覆盖`；`ERROR_REGISTRY 43 个码全部注册`；`G-13 通过` | 0 | ✅（14005-14008 已落地） |
| **G-23 计量对账** | `reconcile_usage.py --period … --threshold 0.01` | 正例 `[PASS] … 偏差=0.0000%` exit 0；**负例 `[FAIL] … 偏差=50.00%` 退出码 1 但 `audit_log` 无记录（脚本崩溃）** | 1 | ❌ 见缺陷 B07-01 |
| **G-24 P0 用例** | `pytest tests -q -m p0` | `940 deselected` → **收集 0 条**（`pyproject.toml` 未注册 `p0` 标记，全仓 `pytest.mark.p0` 命中数 = **0**） | 0 | ❌ **门禁空跑**（见缺陷 QA-P2-W2-04） |

### 2.1 覆盖率两项的趋势与判定

| 项 | B05 轮（W-02 撤销时） | 本轮（`2ef734d`，工程师用例集） | 本轮（含 QA 用例） | 阈值 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 总覆盖率 | 88.06% | **86.55%**（**-1.51pp**） | 87.77%（-0.29pp） | ≥90.5%（W-01 观察中，硬地板 85%） | ❌ 未达 90.5%；且 W-01 约束「每批波动 ≤0.5pp」被 **-1.51pp** 突破 |
| 新模块（business+db） | 86.09% | **84.35%**（**-1.74pp**） | 85.33%（-0.76pp） | ≥85%（W-02 **已撤销豁免**，低于即红） | ❌ 差 0.65pp |

结论：**B07/B08 新增 3.2k 行代码把 W-02 从「已达标」拉回阈值以下**，且 W-01 的波动约束同时被突破。含 QA 用例才勉强 85.57%，属「靠 QA 补测兜底」，不构成工程侧达标。

新模块内低于 85% 的文件（`coverage report`，工程师用例集）：`member_service.py 71.68%`、`role_service.py 78.82%`、`org_repo.py 74.59%`、`audit_repo.py 72.28%`、`policy_service.py 84.26%`、`usage_service.py 86.51%`（达标）、`department_service.py 94.01%`（达标）。

### 2.2 HEAD 前移后的复测（`fdf229f`）

验证期间 `develop` 前移 3 个提交（`aefa7a6` / `85f06b2` / `fdf229f`）。QA 在干净 worktree 切到 `fdf229f` 全量复测：

| 项 | `2ef734d`（冻结基线） | `fdf229f`（复测） | 判定变化 |
| --- | --- | --- | --- |
| 工程师用例集全量（PG） | `936 passed`，0 skipped | **`940 passed`**，0 skipped | ✅ 仍绿 |
| 含 QA 用例（PG） | `3 failed, 960 passed` | **`3 failed, 964 passed`** | 红灯**同为** B07-01 / B08-01 / B08-02，未变化 |
| 总覆盖率 | 86.55% | **86.88%** | ❌ 仍 < 90.5%；对 W-02 撤销时 88.06% 为 **-1.18pp**（仍破 W-01 的 ≤0.5pp） |
| 新模块覆盖率 | 84.35% | **84.96%** | ❌ 仍 < 85%，**仅差 0.04pp**（差 2 行左右覆盖） |
| 新模块覆盖率（含 QA 用例） | 85.33% | **85.57%** | 含 QA 才过线 |

> 结论不变：3 个新提交（KB ID 归一化 / W-03 登记 / G-13 收口）**未触及本批任何缺陷**。新模块覆盖率从 84.35% 改善到 84.96%，属「临门一脚」差距——补 `member_service.py:517-548/563-572` 或 `org_repo.py` 中任一分支即可过线，但仍须由工程师侧补测（不得靠 QA 用例兜底过门禁）。

---

## 3. 独立验证用例（交付项 2）

`apps/adapter/tests/integration/test_qa_b07_b08_verification.py`，27 条（1 条参数化展开为 8）：

```
3 failed, 24 passed in 26.62s     # 干净 worktree @ 2ef734d + --run-pg
```

3 条红灯即 §4 的 3 个缺陷断言（**保留为阻断形式，不加 xfail**，与 W1 §5.6 一致）。

与工程师用例的分工（不是重复）：

| QA 用例 | 补的缺口 |
| --- | --- |
| `test_b07_1_*` | 三类能力（chat/doc_process/automation）**逐类**做重放，且同断言 tenant+member 聚合不累加 |
| `test_b07_2_*` | M4 与 BL5 **同增量**（+137）与 `remaining_units` 同步（工程师只比同刻等值） |
| `test_b07_3_*` | 管理视图 breakdown 求和 == 企业总量的 **diff == 0**（非「≤1%」） |
| `test_b07_4_*` | **真实驱动 SSE 生成器**中断（非纯函数），基准值取同正文正常结束的**实测结算值**，开区间两端都是实测值 |
| `test_b07_5a/b/c_*` | 对账脚本**以子进程真实执行**、校验**退出码语义**（2 / 0 / 1）与 `audit_log` 落库（工程师只测纯函数 `reconcile()`） |
| `test_b07_6_*` | `doc_process` / `automation` 走**真实调用点**（`_record_doc_process_if_completed` / `_record_automation`）并校验事件 + 聚合同步 |
| `test_b08_3_*` | 删除「有**成员**」部门（工程师 API 用例只覆盖「有子部门」分支）+ 移出成员后可删的反证 |
| `test_b08_5a/5b_*` | 改角色 **升/降两个方向** 的 ≤5s 生效（工程师无此用例） |
| `test_b08_6_*` | 部门**移动**的审计能看出新旧上级（`before.parent_id != after.parent_id`） |
| `test_b08_7_*` | **KC 投递失败** 8 次 → `failed`，业务树不回滚（工程师注入的是 enqueue 失败，非投递失败） |
| `test_perm_1_*` | 8 账号 × 3 入口**独立造数复算**（另一套种子） |
| `test_perm_2_*` | `dept_<id>` vs `<id>` 两种 ID 形态等价性（工程师只测了 `kb_` 前缀形态） |
| `test_perm_3_*` | 拒绝时**下游调用计数严格为 0**（工程师矩阵未断言） |

---

## 4. 13 条验收点逐条判定（B07 6 条 + B08 7 条）

### 4.1 B07 计量落库与 M4 同源（架构 §9.2，1652-1676 行）

| # | 验收点 | 判定 | 证据（QA 实跑） |
| --- | --- | --- | --- |
| 1 | 同一 `generation_id` 重放不产生第二条 `usage_event` | ✅ | `test_b07_1`：chat/doc_process/automation 三类各写两次 → 首次 `True`、重放 `False`；逐条计数 `{chat:1, doc_process:1, automation:1}`；聚合 `tenant=51 / member=51`（不累加）；`event_counts` 公开口径一致。另 PG 冒烟 `test_postgres_usage_store_smoke` 证明唯一索引 `uq_usage_event_idem` 生效 |
| 2 | 同刻 M4 `used_units` == BL5 `quota_units_used`（误差 **0**） | ✅ | `test_b07_2`：`m4_used - bl5_used == 0`；一次结算 137 后两者增量均 == 137，`remaining_units` 同步。代码层同源已核：两端都调 `UsageService.snapshot()`（`billing.py:114` / `model_service.py:973`），`quota_units_used` 是 `used_units` 的 property（`usage_service.py:176`），**结构上不可能不等** |
| 3 | 按成员 breakdown 求和 == 企业总量（**diff == 0**） | ✅ | `test_b07_3`：3 成员 11+23+47 → `quota_units_used(81) - breakdown_sum(81) == 0`。另做并发复核（5 并发 × 6 轮）→ 6 轮全部 `(member 500, tenant 500)`，`ON CONFLICT DO UPDATE units = units + EXCLUDED.units` 原子累加无丢失 |
| 4 | 流式中途断开 `units ∈ (0, 完整值)`（两端开区间） | ✅ | `test_b07_4`：同正文下「正常结束」实测 `1000`、「产出 1 段后抛错」实测 `812` → `0 < 812 < 1000`；`source='estimated'`；且结算式精确等于 `prompt 兜底 800 + 已下发可见 12 字`。**注意**：该折算只在 `downstream_enabled=True` 路径的 `finally` 存在；`mock_mode` 的 `_stub_stream` 分支无 `finally`（见 P3 观察项） |
| 5 | `reconcile_usage.py --period 2026-11 --threshold 0.01` 偏差 ≤1% 退出码 0；偏差 >1% 退出码非 0 **且写 `audit_log(billing.reconcile_mismatch)`** | ❌ | **正例 ✅**：`test_b07_5b` 真实子进程 + 真实 PG → `[PASS] 成员求和=300 企业总量=300 事件总额=300 偏差=0.0000%`，**exit 0**。**负例 ❌**：`test_b07_5c` 只在事件表插一条（结构误差仍 0、偏差 50%）→ stdout `[FAIL] … 偏差=50.0000% 超过阈值 1.00%`、退出码 1，但 stderr 是 `RuntimeError: Event loop is closed` + `AttributeError: 'NoneType' object has no attribute 'send'`，且 `SELECT action FROM audit_log WHERE tenant_id='tnt_qa_manual2'` → **`[]`（无记录）**。即退出码 1 来自**未捕获异常**，不是设计路径；**审计告警丢失** → G-23 判据不成立。见缺陷 **B07-01** |
| 6 | `doc_process` / `automation` 事件确实落库（不是只在代码里写了） | ✅ | `test_b07_6`：走真实调用点 `RagService._record_doc_process_if_completed`（stage 跃迁 completed）→ 事件 1 条，`idem_key = doc_qa_b07:run_qa_b07`，`units > 0`，tenant 聚合同步，重复轮询不产生第二条；`test_b07_6b`：`AgentService._record_automation` → `automation` 事件 1 条，`idem_key = task_qa_b07`，`units=64` |

### 4.2 B08 组织架构与权限点（架构 §9.2，1678-1705 行）

| # | 验收点 | 判定 | 证据（QA 实跑） |
| --- | --- | --- | --- |
| 1 | 5 层部门树可建、第 6 层 `14006` | ✅ | `test_b08_1`：逐层 `level` 1→5 全 `code=0`；第 6 层 `code=14006` 且文案 == `组织层级最多 5 层，请调整后重试`（同码三文案未串） |
| 2 | 移动到自身子树 `14006` | ✅ | `test_b08_2`：把「根」移动到自己「孙」下 → `14006` + 文案 `不能把部门移动到自己的下级部门`；随后 `flat=true` 复查孙部门仍在 `level=3`（**结构未被破坏**，非「报错了但改了一半」） |
| 3 | 删除有成员部门 `14006` | ✅ | `test_b08_3`：部门下挂 1 成员（无子部门）→ `14006` + 文案 `该部门下仍有成员或子部门，请先迁移后再删除`；**反证**：移出成员后同一部门删除成功 → 拦截原因确为「有成员」而非其他 |
| 4 | 移除 owner `14007`、修改同级成员 `14008`、角色被引用删除 `20005` | ✅ | `test_b08_4`：ID9 移出 owner → `14007` + `不能移除企业所有者，请先转移所有权`；ID8 改同级 org_admin → `14008`；ID7 删角色 → `20005`（`data=null`）。**附注（P3）**：本批**无创建自定义角色入口**（仅 `ensure_builtin_roles`），`MESSAGE_ROLE_IN_USE`（「该角色下还有 N 名成员」）分支在 B08 内**不可达**，实际走 `is_builtin` 分支文案；待 U5 打通后需复验该文案 |
| 5 | 改角色后 ≤5s 判权生效（策略缓存） | ❌ | **升级方向 ✅**：`test_b08_5a` 令牌 `member` + 业务库改 `org_admin` → 下一请求即获 `team:structure:manage`（`0.01s` 内，断言 ≤5s）。**降级方向 ❌**：`test_b08_5b` 令牌 `org_admin` + 业务库改 `member` → 等 TTL+0.1s 后仍：① 可改组织架构 `code=0`；② 可看企业级管理视图 `code=0`；③ 列表仍含**本人不受权**的受限库；④ 该受限库详情 `code=0`。见缺陷 **B08-01（P0）** |
| 6 | 所有变更写 `audit_log` 且含 before/after 值 | ✅ | `test_b08_6`：`department.create`（before={} 合理、after 有值）、`department.update`（before/after 均非空，含 `name`）、**部门移动**（`before.parent_id != after.parent_id`、`after.level=2`）、`member.update`（before.role=member → after.role=guest）、`member.remove`（before 有 role）逐类断言 |
| 7 | D-13：业务库为权威源，Keycloak 投影失败**不回滚**业务事务 | ✅ | `test_b08_7`：注入必失败的 KC 投影器，`run_once` 连续 8 次（`MAX_ATTEMPTS`）→ 投递成功 0 次、`projector.calls ≥ 8`、出箱 `status='failed'`；同时业务库 `get_department()` 仍返回 `QA投影部门`、ID1 树里仍含该部门 → **业务事务未被回滚**。另核代码：`KcProjectionService._enqueue` 对任何异常 `except Exception → WARN`（`kc_projection.py:642-649`），`run_once` 投递失败只退避/置 failed，且**全仓无「读 KC group 改业务库」路径** |

### 4.3 权限矩阵（重点，W1 的 P0 教训）

工程师 `tests/integration/test_permission_matrix.py` 独立复跑：**17 passed**。QA 对其覆盖面判断 + 补充断言：

| 维度 | 工程师覆盖 | QA 判断 | QA 补充 |
| --- | --- | --- | --- |
| 8 类账号 × 3 入口（列表/详情/检索） | ✅ 24 格参数化 | **够**（每格同时断言列表可见性 + 详情码 + 检索码） | 另用**另一套种子**独立复算 8×3（`test_perm_1`，含 `cross_dept` 在未授权部门、`no_role` 空角色） |
| 拒绝不得是 404 | ✅ | 够 | 列表/详情/检索三处都断言 `status != 404` |
| **ID 形态绕过**（`dept_<id>` vs `<id>`） | ⚠️ **只测了 `kb_` 前缀**（`test_id_form_variant_does_not_bypass`） | **不够**：部门维度未测 | `test_perm_2` → **❌ 不通过**：`subject_id="dept_qa_form"` 授权命中，`subject_id="qa_form"`（同一部门裸形态）**完全不命中** → 授权静默失效。见 **B08-02** |
| **未授权下游调用计数 == 0** | ❌ 未断言 | **不够**（W1 教训「先调后拒」无法被发现） | `test_perm_3` → ✅ 计数桩实测 `fake.calls == 0`（两种 ID 形态都拒且都不调下游） |
| 跨租户 | ✅ 列表空 + 详情非 0 | 够（W-03 判据偏离已登记） | — |
| 无角色 fail-closed | ✅ | 够 | — |
| 删库三分支 | ✅ 4 条分支断言 | 够（裁决 1.5 已落实） | — |

结论：矩阵文件**覆盖面基本够用**，但缺「部门 ID 形态」与「下游零调用」两类断言，且前者一测即红。

---

## 5. 缺陷清单

| 编号 | 等级 | 标题 | 复现（已实跑） | 期望 / 实际 | 归属 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| **B08-01** | **P0（阻断）** | 业务库角色**降级不生效**：`_resolve_points` 用并集且 `has_admin_role` 只读令牌角色，导致「已撤权的 org_admin 仍能读未授权受限库」 | `test_b08_5b`：① 令牌 `roles=["org_admin"]`、业务库角色 `org_admin` 的成员 `usr_qa_down` 预热一次请求；② 由 `owner` 经 ID8 把其业务库角色改为 `member`；③ 等 `permission_cache_ttl_seconds + 0.1s`（≤5s）；④ 用**同一令牌**请求 | 期望：改组织架构 `20003`、企业级管理视图 `20003`、受限库列表不含、详情 `20003`。**实际**：① `code=0`（改名成功）② `code=0` ③ 列表**仍含** `kb_qa_w2_downgrade` ④ 详情 `code=0`。根因：`policy_service.py:414` `points = set(permissions_for_roles(auth.roles))` **并集** `permissions_of_user(...)`（业务库只能**加权不能减权**）；`has_admin_role(auth)`（`policy_service.py:208`）只读 `normalized_roles(auth)`（令牌）；`is_visible` 首行 `if has_admin_role(auth): return True` 直接放行全部受限库。同时 `kc_projection` 只投影 group（`KIND_GROUP_UPSERT/DELETE/USER_GROUP_SYNC`），**不投影角色** → 生产环境该令牌会永久保有 `org_admin` | Engineer | ⏳ 待修（工作区已有未提交改动涉及 `policy_service.py`，需提交后复验） |
| **B07-01** | **P1（阻断）** | `reconcile_usage.py` 偏差超限时**审计告警丢失**（脚本崩溃，退出码 1 来自未捕获异常） | `test_b07_5c`：真实 PG 造「事件表 200 / 聚合 100、成员==企业 100」（结构误差 0，偏差 50%）→ 子进程跑 `reconcile_usage.py --threshold 0.01` | 期望：退出码 1 **且** `audit_log` 新增 1 条 `billing.reconcile_mismatch`（含 before/after/threshold）。**实际**：stdout 有 `[FAIL] … 偏差=50.0000%`，退出码 1；stderr `RuntimeError: Event loop is closed` → `AttributeError: 'NoneType' object has no attribute 'send'`；`audit_log` **0 条**。根因：`main()` 连续两次 `asyncio.run()`（`reconcile` 与 `record_mismatch`），而 `app.db.engine` 是**进程级单例且绑定首个事件循环**，第二次运行拿到已关闭循环的引擎 | Engineer | ⏳ 工作区已有未提交修复（`_run_once` 单事件循环），需提交后复验 |
| **B08-02** | P1 | 部门 ID 形态不一致：KB6 授权写**裸部门 ID** 时静默失效（`dept_<id>` vs `<id>` 结果不同） | `test_perm_2`：对同一物理部门分别用 `subject_id="dept_qa_form"` 与 `"qa_form"` 各建一条授权；成员 `usr_q_p1` 属于该部门 | 期望：两种写法判权结果一致。**实际**：`dept_qa_form` 命中（`can_read=True`）；`qa_form` 不命中（`can_read=False`）→ 管理员授权「建成功但没人拿到权限」，且无任何报错。根因：`normalize_department_id()` 已用于 ID2/ID3/ID4/ID8/U3（`department_service.py:236/313/369/450`、`member_service.py:148/517/518/563`），但 **`grant_service`（KB6 `subject_type=department`）未归一化**，而判权侧 `_user_departments()` 返回的是规范形态 | Engineer | ⏳ 待修（B11 前端接入 / 第三方集成前必修） |
| **QA-P2-W2-04** | P2（流程/门禁） | **G-24「P0 用例全通」是空跑**：全仓无 `pytest.mark.p0`，`pytest -m p0` 收集 **0 条** | `pytest tests -q -m p0` → `940 deselected`，无用例；`grep -rn "pytest.mark.p0" tests/` → **0**；`pyproject.toml` `markers` 只注册了 `unit/integration/contract` | 期望：`p0` 标记用例 ≥60 且 `failed=0`（测试方案 §2.1 G-24）。实际：门禁恒绿但**零覆盖**——W1 报告 §2 记该门禁 ✅（`0 failed`）即为假绿，本轮复现 | Engineer + QA | ⏳ 待建标记映射（24 条 P0 判据 → 用例） |
| **QA-P2-W2-05** | P2（门禁） | **G-05 新模块覆盖率跌破阈值**且 W-01 波动约束被突破 | `coverage report --include="app/business/*,app/db/*"`（工程师用例集）→ `2ef734d` **84.35%**、`fdf229f` **84.96%**（阈值 85%）；总覆盖率 `2ef734d` 86.55% / `fdf229f` 86.88%（W-02 撤销时 88.06%，**-1.18pp**；W-01 约束 ≤0.5pp） | 期望：新模块 ≥85%（W-02 已撤销豁免，低于即红）、总覆盖率每批波动 ≤0.5pp。实际：新模块差 **0.04pp**（约 2 行）、总覆盖率 -1.18pp。低覆盖集中：`member_service 71.68%`、`org_repo 74.59%`、`audit_repo 72.28%`、`role_service 78.82%`、`policy_service 84.96%` | Engineer | ⏳ 待补测（差距极小，但不接受「QA 用例兜底」过门禁） |
| **QA-P3-W2-06** | P3 | 「角色被引用删除」文案分支在 B08 内不可达 | 本批只有 `ensure_builtin_roles`，无创建自定义角色入口 → `delete_role` 的 `is_builtin` 判定先命中 | 期望：`20005` + 「该角色下还有 N 名成员」（架构 §4.4 要点 + 裁决 #3）。实际：`20005` + 「内置角色不可删除…」。码值正确，文案分支待 U5（自定义角色）打通后复验 | Engineer（U5 批次） | ⏳ 观察项 |
| **QA-P3-W2-07** | P3 | `mock_mode`（开发/演示默认）下 `automation` 事件与「中断折算」路径不可达 | `agent_service.py:195` `_record_automation` 只在 `if not self._settings.mock_mode:` 内、且 `native_status == "succeeded"` 时调用；`model_service.py:719` 桩分支「先 yield 后结算」，无 `finally` 折算 | 影响：本地/演示环境既不会写 `automation` 事件，也测不出中断 `estimated`。建议在 `/readyz` 或启动日志标注「计量仅在下游开启时生效」，避免验收方误判 | Engineer | ⏳ 建议 |
| **QA-P3-W2-08** | P3 | 对账口径偏离（已自述登记）：架构 §2.5 第 7 条要求「Valkey 诊断计数 vs `usage_monthly.tenant`」，脚本改用「事件表 vs 聚合」 | `reconcile_usage.py` 文件头注释已「诚实登记」：本阶段无 Valkey 客户端 | 裁定：**接受**。事件表是幂等且可审计的第二来源，口径等价且更强；但需在 `docs/qa-phase2-waivers.md` 以「判据偏离说明（W-03 体例）」正式登记，而非仅留代码注释 | QA（登记） | ⏳ 待登记 |

**未计入缺陷的观察项**

- 本次验证早期（工作区尚未出现并行改动、但 QA 用例仍跨用例复用 PG 引擎时）曾出现 **1 次** `member=300 / tenant=200` 的结构误差。修复用例夹具（每个 PG 用例前后 `reset_engine_for_test()`）后，**5 并发 × 6 轮复测全部 `(500, 500)` 一致**，且 `test_b07_5b` 单独重复 3 次均通过 → 判定为**用例夹具缺陷（QA 侧）**，不计产品缺陷。此处保留数字以备回溯。
- `audit_log` 对应用角色**无 DELETE 权限**（本轮 `DELETE FROM audit_log` 被 PG 拒绝）→ 属**正确设计**（追加不可删），非缺陷；对账用例已改为「前后条数差」断言。
- 「写接口必须带 `Idempotency-Key`」在 ID1/ID5/ID6/ID11（读接口）不适用，本轮未见异常。

---

## 6. 准出结论与路由

### 6.1 准出判据核对

| 判据 | 要求 | 实际 | 结论 |
| --- | --- | --- | --- |
| 13 条验收点 | 全过 | **11 ✅ / 2 ❌**（B07⑤、B08⑤） | ❌ |
| P0 | 0 | **1**（B08-01） | ❌ |
| P1 | 0 | **2**（B07-01、B08-02） | ❌ |
| G-13 / G-23 / G-12 / G-24 | 必跑且绿 | G-13 ✅、G-12 ✅、**G-23 ❌**、**G-24 ❌（空跑）** | ❌ |
| 覆盖率两项 | 90.5% / 85% | `2ef734d` 86.55%（-3.95pp）/ 84.35%（-0.65pp）；`fdf229f` 86.88%（-3.62pp）/ 84.96%（-0.04pp） | ❌ |

> HEAD 前移说明：验证期间 `develop` 又前移 3 个提交（`aefa7a6` 知识库 ID 先归一化再去重、`85f06b2` 登记 W-03、`fdf229f` G-13 收口）。QA 已在干净 worktree 切到 `fdf229f` **重跑 QA 27 条用例：结果与 `2ef734d` 完全一致（3 failed / 24 passed，红灯同为 B07-01 / B08-01 / B08-02）**，即该 3 个提交未触及本批缺陷。全量 PG 回归 + 覆盖率在 `fdf229f` 上复测见 §2.2。

### 6.2 结论

> **FAIL（不准出）。** B07 的「计量同源」主干**实现质量高**（同源读、同事务双写、幂等、折算、并发一致性均经实测通过，误差严格为 0），B08 的部门树/成员保护/审计/单向投影亦按判据落地；但存在 **1 个 P0**（业务库降级不生效 → 未授权受限库可读）与 **1 个 P1**（对账告警审计丢失，G-23 判据不成立），且 **G-24 门禁空跑**、**G-05 新模块覆盖率跌破已撤销豁免的阈值**。按测试方案 §7.1，P0 一票否决。

### 6.3 路由

| 对象 | 内容 |
| --- | --- |
| **→ Engineer** | ① **B08-01（P0）**：`_resolve_points` 改「业务库优先、令牌角色仅兜底」语义（业务库有记录时**以其为准**），`has_admin_role` 与 `is_visible` 的管理员判定必须走同一权威源；建议引入「业务库角色解析 + 5s 快照」单一口径，禁止任何判权函数直读 `auth.roles` 做**授权**决策（`auth.roles` 只保留鉴权/审计用途）。② **B07-01（P1）**：对账脚本单事件循环（工作区已有未提交修复，请提交并补一条「偏差超限必有 1 条审计」的用例）。③ **B08-02（P1）**：KB6 的 `subject_type=department` 入参统一 `normalize_department_id()`，并对历史裸 ID 授权做一次归一化回填（或在读取侧兼容两种形态）。④ G-24 补 `p0` 标记映射（≥60 条）；⑤ 新模块覆盖率补到 ≥85%（优先 `member_service` / `org_repo` / `audit_repo`）。 |
| **→ QA（已闭环）** | 覆盖面补测已完成（本文件 27 条），无需工程师代写用例；后续断言口径变更请先与 QA 对齐（W1 §5.6）。 |
| **→ NoOne** | 不适用（存在阻断缺陷）。 |

### 6.4 第 2 轮复验范围（待新提交）

工程师修复提交后，QA 只跑：① `test_qa_b07_b08_verification.py` 三条红灯是否转绿（`test_b07_5c` / `test_b08_5b` / `test_perm_2`）；② 全量 PG 回归 + 覆盖率两项；③ G-23 负例手工实跑一次（确认 `audit_log` 真有记录）；④ B08-01 修复后需额外确认「升级方向」未被改坏（`test_b08_5a` 仍绿）。**两轮上限内闭环，不再进入第 3 轮。**

---

## 7. 补充：已关闭项与判据裁定（R2 回填）

> 本节由第 2 轮复核（`2df2831`）回填，正文 §5 的缺陷台账保留第 1 轮判据原貌以便回溯。完整第 2 轮结论见 `docs/qa-phase2-w2-r2-report.md`。

| 编号 | 原状态 | 现状态 | 关闭证据 |
| --- | --- | --- | --- |
| **B08-01（P0）** | ⏳ 待修 | ✅ **已关闭** | `2df2831` 判权改「业务库角色为权威源」；`test_b08_5b` 四道判径全部 `20003` |
| **B07-01（P1）** | ⏳ 待修 | ✅ **已关闭** | `2df2831` 单事件循环；负例 `EXIT=1` + `audit_log` 1 条 `billing.reconcile_mismatch` |
| **B08-02（P1）** | ⏳ 待修（原 W2 报告编号） | ✅ **已关闭** | `grant_repo` 判权口径统一；`test_perm_2` 转绿 |
| **QA-P2-W2-04（P2）** | ⏳ 待建标记 | ✅ **已关闭** | 8 文件挂 `pytest.mark.p0`，收集 **130 条**，`-m p0` 0 failed |
| **QA-P2-W2-05（P2）** | ⏳ 待补测 | ✅ **已关闭** | business+db **85.67%**（≥85%），总覆盖率 87.95% |
| **QA-P3-07**（`filter_authorized` 归一化前重复，`["kb_x","x"]` 被当成两个对象 → 单次对话用量虚高，与 D-12 计量口径冲突） | ⏳ 已知问题（W1 遗留） | ✅ **已关闭**（`aefa7a6` 修复） | 新增回归断言 `test_qa_p307_filter_authorized_normalizes_before_dedup`：输入 `["kb_y_p307","kb_x_p307","x_p307","kb_y_p307"]` → 输出 `["kb_y_p307","kb_x_p307"]`，无重复、保序，实测 PASS |
| **W-03**（跨租户详情返 `11001` 而非 `20003`） | 待主理人裁定 | ✅ **裁定为「判据偏离」不算缺陷**（已登记 waivers） | 判据改为「非 0 且不返回任何数据」；新增 `test_perm_06_cross_tenant_detail_leaks_nothing`：`code != 0`、`data is None`、响应不含库名/文档名/base_id、与「真不存在」的 code 与 message **完全一致**（不可反推存在性），实测 PASS |

---

*报告人：严过关（QA 工程师）· 2026-10-22 · 判据以冻结 HEAD `2ef734d` 为准（R2 复核基线 `2df2831`，见 r2 报告）*
