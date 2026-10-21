# 阶段二 W2 第二轮复核报告（B07 / B08 修复验证）

| 项 | 内容 |
| --- | --- |
| 报告编号 | QA-P2-W2-R02 |
| 复核对象 | **`2df2831`**（`fix(adapter): 判权以业务库角色为准 + 对账偏差审计补齐（QA W2 P0/P1）`） |
| 基线对照 | `2ef734d`（W2 第 1 轮判据基线）→ `fdf229f`（HEAD 前移 3 提交）→ `2df2831`（本轮） |
| QA 工程师 | 严过关（Yan） |
| 复核日期 | 2026-10-22 |
| 取样方式 | **两轮 `git archive` 导出到独立目录复核，全程不读活工作区**（B09/B10 已在主工作区开工，避免半成品污染结论）：① `2df2831` → 缺陷关闭与门禁实跑；② `64e0f0a`（p0 标记入库后）→ 覆盖率为最终口径复测。`COVERAGE_FILE` 均落在导出目录，未写回主仓 |
| **最终判定** | **PASS（准出 W2）**：**0 P0 / 0 P1**；13 条验收点 13/13；G-12/G-13/G-23/G-24 全绿；覆盖率按 `94671a3` 修订口径两项达标（总 87.95% / 新模块 85.67%）。残留 1 条 P2 风险（engineer-only 84.99%，§4.3）、1 条已知边界（G-24 的 24 条全覆盖不成立，§3.2）、B15 关键模块待办（`usage_service` 87.44%，§4.4） |
| 路由 | **NoOne**（W2 闭环，转待命）；另有 3 项知会/决策见 §6.3 |

---

## 1. 结论摘要

| 维度 | 第 1 轮（`2ef734d`） | 第 2 轮（`2df2831`） |
| --- | --- | --- |
| 13 条验收点 | 11 ✅ / 2 ❌ | **13 ✅ / 0 ❌** |
| P0 / P1 | 1 / 2 | **0 / 0** |
| 全量 PG 用例 | `3 failed, 960 passed` | **`969 passed, 0 failed, 0 skipped`** |
| 总覆盖率 | 86.55% | **87.95%**（修订基线 86.88%，+1.07pp） |
| business+db 覆盖率 | 84.35%（❌） | **85.67%（✅）** |
| G-24 | 0 条 p0（空跑 ❌） | **130 条，`--run-pg` 0 failed / 0 skipped（✅）** |
| 判定 | FAIL | **PASS** |

---

## 2. 两条阻断缺陷的关闭验证（含复现命令）

### 2.1 B08-P0-01 业务库角色降级不生效 → ✅ 关闭

**修复位置**：`app/business/policy_service.py`（`_user_role_key` / `_resolve_points(auth, business_role)` / `_resolve_admin` / `admin_of`）、`app/api/v1/billing.py:108`（`policy.admin_of()`）、`policy_service.py:550`（`is_visible` 改用 `snapshot.is_admin`）。

**复现命令（QA 实跑，导出副本）**

```bash
cd C:/Users/15864/Desktop/qa-w2-r2-2df2831/apps/adapter
OA_TEST_DATABASE_URL="postgresql://officeai_app:…@127.0.0.1:55433/officeai" \
  <主仓 venv>/python.exe -m pytest tests/integration/test_qa_b07_b08_verification.py \
  -q -p no:randomly -k "b08_5b" --run-pg
```

**结果**：`1 passed`（第 1 轮同一条用例为 `1 failed`）。

**判定链（四道判径同时收紧，逐条实测）**

| 判径 | 修复前（`2ef734d`） | 修复后（`2df2831`） |
| --- | --- | --- |
| ① 权限点 `team:structure:manage`（ID3 改部门） | `code=0`（仍可改） | **`code=20003`** |
| ② 管理员判定（BL5 `group_by=member` 企业级视图） | `code=0`（仍可看） | **`code=20003`** |
| ③ 受限库列表（本人不在授权名单） | 仍出现 | **不出现** |
| ④ 该受限库详情（KB3） | `code=0`（仍可读） | **`code=20003`** |

**代码侧核对（不是只信用例）**

- `_resolve_points()`：`business_role` 非空时 → `BUILTIN_ROLE_PERMISSIONS[business_role] ∪ role_permission 授予点`，**不再并入令牌角色**；`business_role` 为空（业务库未登记）才回落 `permissions_for_roles(auth.roles)`。→ 业务库**可加可减**，符合 D-13「唯一权威源」。
- `_resolve_admin()`：同口径（业务库角色优先，令牌兜底）；`WILDCARD_ROLE` 保留为平台运维逃生口（与令牌约定一致）。
- `is_visible()`：`is_admin = snapshot.is_admin if snapshot is not None else has_admin_role(auth)` —— 有了快照就不再读令牌角色，堵住「列表放行」的旁路。
- **升级方向未被改坏**：`test_b08_5a`（令牌 `member` + 业务库改 `org_admin` → ≤5s 内获得 `team:structure:manage`）仍 `0.01s` 内生效。

### 2.2 B07-P1-01 对账偏差审计丢失 → ✅ 关闭

**修复位置**：`apps/adapter/scripts/reconcile_usage.py`（新增 `_run_once()`：`reconcile` 与 `record_mismatch` 在**同一事件循环**内完成，避免进程级引擎绑定已关闭循环）。

**复现命令（手工端到端，与用例互补）**

```bash
# ① 造数：aggregate=100，事件表再加一条 100 → 结构误差 0、偏差 50%
# ② 实跑脚本
OA_BUSINESS_STORE=postgres OA_DATABASE_URL="…" \
  <venv>/python.exe scripts/reconcile_usage.py --period 2026-10 --threshold 0.01 --tenant tnt_qa_r2_g23
```

**实跑输出（第 2 轮）**

```
EXIT=1
[FAIL] period=2026-10 tenant=tnt_qa_r2_g23
  成员求和=100 企业总量=100 事件总额=200 偏差=50.0000% 阈值=1.00%
  逐能力差值（成员-企业）：chat:0
  - 事件表总额（200）与企业总量（100）偏差 50.00% 超过阈值 1.00%
[FAIL] 计量对账未通过（已写入操作记录 billing.reconcile_mismatch）
--- stderr ---（空，无 traceback）
```

**审计落库实测**（第 1 轮为 `0 条`，本轮 `1 条`）

```json
{"action": "billing.reconcile_mismatch", "result": "failure",
 "change_summary": {"period": "2026-10",
                    "before": {"tenant_total": 100, "event_total": 200},
                    "after": {"member_total": 100},
                    "deviation": 0.5, "threshold": 0.01,
                    "reasons": ["事件表总额（200）与企业总量（100）偏差 50.00% 超过阈值 1.00%"]}}
```

**正例回归**：`test_b07_5b`（偏差 0）仍 `EXIT=0` + `[PASS]`。

> 判据达成：**退出码非 0 且写 `audit_log(billing.reconcile_mismatch)`**（第 1 轮退出码 1 系崩溃副产品，审计缺失 → G-23 不成立；本轮为设计路径）。

### 2.3 附带关闭：B08-02 部门 ID 形态不一致 → ✅ 关闭

第 1 轮 P1（KB6 裸部门 ID 授权静默失效）。`grant_repo` 判权口径统一到 `PolicyService` 后，`test_perm_2`（`dept_<id>` vs `<id>` 等价性）**转绿**。

### 2.4 已关闭项（转入"已关闭"台账）

| 编号 | 标题 | 关闭证据 |
| --- | --- | --- |
| **QA-P3-07** | `filter_authorized` 归一化前重复（`["kb_x","x"]` 会被当成两个对象） | `aefa7a6` 已修（先 `normalize_base_id` 再 `dict.fromkeys` 去重、保序）。本轮新增回归断言 `test_qa_p307_filter_authorized_normalizes_before_dedup`：输入 `["kb_y_p307","kb_x_p307","x_p307","kb_y_p307"]` → 输出 `["kb_y_p307","kb_x_p307"]`，**无重复、保序**，`len(result)==len(set(result))`。实测 **PASS** |
| **W-03** | 跨租户详情返 `11001` 而非 `20003` | 主理人裁定为**判据偏离**（不算缺陷），判据改为「非 0 且不返回任何数据」。本轮按新判据新增 `test_perm_06_cross_tenant_detail_leaks_nothing`：① `code != 0` 且 `data is None`；② 响应全文不含库名/文档名/`base_id` 回显；③ 与「真不存在」的 `code` 与 `message` **完全一致**（不可反推存在性）。实测 **PASS** |

> 说明：`test_perm_06` 源码里已注明「此处不断言 HTTP != 404」——`11001` 的 HTTP 映射本就是 404，PERM-07 的「拒绝不得是 404」针对的是「无权限但资源存在」的 `20003` 形态，二者不冲突。

---

## 3. 门禁实跑（`2df2831` 导出副本）

| 门禁 | 命令 | 关键输出 | 退出码 | 判定 |
| --- | --- | --- | --- | --- |
| G-01 lint | `ruff check .` | `All checks passed!` | 0 | ✅ |
| G-02 format | `ruff format --check .` | `137 files already formatted` | 0 | ✅ |
| G-03 mypy | `mypy app` | `Success: no issues found in 86 source files` | 0 | ✅ |
| G-04 全量（非 PG） | `pytest -q` | `932 passed, 37 skipped, 1 warning in 156.51s` | 0 | ✅ 0 failed |
| G-04' 全量（PG） | `pytest -q --run-pg --cov=app` | **`969 passed, 1 warning in 301.17s`**，**skipped = 0** | 0 | ✅ |
| G-05 总覆盖率 | 同上 | **`TOTAL 8191 780 1616 264 87.95%`**（`64e0f0a` 复测：`8191 781 1616 264 87.94%`） | 0 | ✅ 硬地板 85% 达标；修订基线 86.88%（B15 目标 ≥88%）→ 高于基线 1.07pp |
| G-05 新模块 | `coverage report --include="app/business/*,app/db/*"` | **`TOTAL 4365 519 938 151 85.67%`** | 0 | ✅ ≥85%（W-02 已撤销豁免，本轮**达标**） |
| G-07 core 端点 | `deploy/validate_openapi.py --allow-missing-phase2` | `端点统计: core=30 phase2=21/38 （未实现 0）` | 0 | ✅ |
| G-08 新域端点 | 同上 | `phase2=21/38` | 0 | ✅ |
| G-09 加性 diff | 同上 | `[OK] G1-G5 全部通过` | 0 | ✅ |
| G-12 RLS | `pytest -m p0 --run-pg`（含 tenant_isolation 15 条）+ 单独复跑 | `41 passed`（第 1 轮数字，代码未变）/ p0 全绿 | 0 | ✅ |
| G-13 错误码 | `scripts/check_error_catalog.py --allow-pending b09` | `5/14 已落地（9 个按批次放行）`；`errors.py → business-errors.ts: 43 个码全部覆盖`；`ErrorCode → ERROR_REGISTRY: 43 个码全部注册`；`G-13 通过` | 0 | ✅ |
| G-23 计量对账 | 正例 + 负例手工各一次 | 正例 `EXIT=0 [PASS]`；负例 `EXIT=1` + 审计 1 条（§2.2） | 0 / 1 | ✅ |
| **G-24 P0 用例** | `pytest tests -q -m p0` | **`109 passed, 21 skipped, 0 failed`**，收集 **130 条**（≥60） | 0 | ✅ |
| G-24'（PG） | `pytest tests -q -m p0 --run-pg` | **`130 passed, 0 failed, 0 skipped`** | 0 | ✅ |

### 3.1 G-24 修复明细（QA 域整改，不占工程师）

第 1 轮 G-24 为**空跑**（0 条 p0）。本轮落地：

1. `pyproject.toml` 注册 marker（`--strict-markers` 已开启，未注册会直接报错）：
   ```toml
   "p0: P0 红线用例（测试方案 §7.1，G-24 门禁）",
   ```
2. 8 个文件挂 `pytest.mark.p0`，**收集 130 条**：

| 文件 | 条数 | 映射的 P0 判据 / 需求 |
| --- | --- | --- |
| `tests/integration/test_security_regressions.py` | 22 | 判据①（跨租户/越权红线：OIDC/租户锁定/限流/幂等 body） |
| `tests/integration/test_tenant_isolation.py` | 15 | 判据①（跨租户可见任何一行数据，SEC-02/03） |
| `tests/unit/test_credential_service.py` | 20 | 判据②（已绑定凭据回落全局，P2-KB-05） |
| `tests/integration/test_permission_matrix.py` | 17 | 判据③（无权限账号通过详情/检索拿数据，P2-KB-03） |
| `tests/unit/test_usage_service.py` | 18 | 判据④（按成员求和 ≠ 企业总量，P2-BL-03） |
| `tests/integration/test_usage_endpoints.py` | 13 | 判据④（M4 与 BL5 同源、成员求和） |
| `tests/integration/test_endpoint_baseline.py` | 20 | 判据⑤（既有 30 端点契约被破坏） |
| `tests/integration/test_g11_migration_idempotency.py` | 5 | 判据⑦（迁移不可重复） |
| **合计** | **130** | 5/7 条 P0 判据 + 4 个需求族 |

3. **必须声明的边界（不粉饰）**：测试方案 §4.7 的 **24 条 P0** 中，只有 **8 条需求族**目前有已落地端点可映射（KB-03/KB-05/BL-03 + 判据①②④⑤⑦）。以下 **10 条客观无法映射**，因对应端点尚未实现（`phase2=21/38`）：

| 未映射需求 | 阻塞批次 |
| --- | --- |
| P2-BL-01 / BL-02 / BL-04（套餐、订阅变更、订单支付） | B09 |
| P2-BL-05（运营后台） | B10 |
| P2-UI-01 ~ UI-05（5 个前端 P0 页面） | B11-B14 |
| P2-UX-01（体验兜底与禁词） | B15 |

   → 处理方式：**不挂空标记**（给未实现功能挂 `p0` 只会制造新的假绿）。主理人已确认「不要求凑数」，并将「各批次落地时同步补 p0 标记」写入 B09-B15 任务书。

### 3.2 已知边界：G-24 的「24 条 P0 全覆盖」在本轮**不成立**（如实登记）

| 项 | 状态 |
| --- | --- |
| 已映射需求族 | **8 条**（P2-KB-03、P2-KB-05、P2-BL-03 + 判据①②④⑤⑦） |
| 未映射需求 | **10 条**（见下表），阻塞原因均为**端点尚未实现**（`phase2=21/38`） |
| 本轮 G-24 判据 | `p0 用例 ≥60 且 failed=0` → **满足（130 条 / 0 failed）** |
| 「24 条 P0 100% 映射」（测试方案 §4.7 自检项） | **不满足**，B15 前无法满足 |

**待补清单（各批次落地时必须同步补 `pytest.mark.p0`，否则 G-24 会在该批次重新变成部分空跑）**

| 未映射需求 | 说明 | 待补批次 |
| --- | --- | --- |
| P2-BL-01 | 套餐目录与额度 | **B09** |
| P2-BL-02 | 订阅变更（升降配） | **B09** |
| P2-BL-04 | 订单支付与回调幂等 | **B09** |
| P2-BL-05 | 运营后台（订单确认/租户管理/设置） | **B10** |
| P2-UI-01 ~ UI-05 | 5 个前端 P0 页面（套餐账单 / 运营后台 / 知识库详情 / 组织架构 / 6 页深化） | **B11-B14** |
| P2-UX-01 | 体验兜底与禁词门禁 | **B15** |

> 这条边界不影响 W2 准出（G-24 的本轮判据已满足），但**必须让阶段验收知道**：现在的 130 条只覆盖了已落地的那 8 族，不能说成「24 条 P0 已全通」。

---

## 4. 覆盖率两项（含口径披露）

### 4.1 门禁口径（测试方案 G-05 命令：`pytest tests --cov=app`，含全部用例）

**判据已按 `94671a3`（`docs/qa-phase2-waivers.md` 修订）更新**：W-01 重新基线为 **86.88%**（B08 后），关闭条件改为「硬地板 ≥85% 不变 + **B15 收尾 ≥88%** + 关键模块单模块 ≥90%」；原「≥90.5%」与「每批波动 ≤0.5pp」对本批**不再适用**（书面判定为剩余批次内不可达）。

| 项 | `2ef734d` | `2df2831` | 本轮阈值（修订后） | 判定 |
| --- | --- | --- | --- | --- |
| 总覆盖率 | 86.55% | **87.95%** | 硬地板 ≥85%；基线 86.88%（B15 目标 ≥88%） | ✅ **达标且高于基线 1.07pp** |
| business+db | 84.35% | **85.67%** | ≥85%（W-02 已撤销豁免） | ✅ **达标** |

### 4.2 趋势（按修订口径）

| 项 | W-02 撤销时（`f168e85`） | 基线与目标 | `2ef734d` | `2df2831` |
| --- | --- | --- | --- | --- |
| 总覆盖率 | 88.06% | 基线 86.88% / B15 目标 88% | 86.55% | **87.95%** |
| business+db | 86.09% | ≥85% | 84.35% | **85.67%** |

`2df2831` 已回到基线之上（+1.07pp）且在 B15 目标 88% 的 -0.05pp 处（B15 前补测即可过线）。**W-02 的 0.65pp 缺口已由修复提交自然补齐**（`policy_service.py` 新增分支被 `test_policy_permission_guards.py` +9 条覆盖）。

### 4.3 「非兜底」证据（按要求必须附上）

QA 另测一份**剔除 QA 自有用例**（`--ignore=tests/integration/test_qa_b07_b08_verification.py`，即**只看工程师自有测试能达到什么水平**）：

| 口径 | 总覆盖率 | business+db | 说明 |
| --- | --- | --- | --- |
| 全仓（含 QA 用例，= G-05 命令口径） | **87.95%** | **85.67%** | 门禁判据取此列（QA 用例是已入库的测试资产，与 W1 轮口径一致） |
| 仅工程师自有用例 | 86.89% | **84.99%** | 交付透明度披露：工程师自有覆盖与新模块阈值差 **0.01pp（约 1 行）** |

**如实结论**：门禁按 G-05 命令口径**达标**；但「工程师自有测试单独测算为 84.99%」意味着新模块 85% 是**踩线达标**，余量仅 0.01pp。

**主理人裁定（2026-10-22）：口径不收紧** —— G-05 按测试方案命令口径（`pytest --run-pg --cov=app`，含全部用例）判定，87.95% / 85.67% 有效；**QA-P2-W2-09 保留为 P2 风险项、不阻断 W2**；同时「engineer-only ≥85%」自 B09 起写入工程师自查项，**B15 前必须自然达标**。裁定理由（原文）：*「用『剔除 QA 用例』当门禁口径会激励团队少写独立验证，方向反了」*。QA 认同该理由并已据此定稿，本条不再作为待决项。

### 4.4 关键模块覆盖率预检（对照 W-01 修订后的关闭条件）

`94671a3` 的新关闭条件包含「**`policy_service`、`grant_service`、`subscription_service`、`order_service`、`usage_service`、`db/session` 单模块行覆盖率 ≥90%**，B15 由 QA 逐模块核验」。QA 提前测了当前已存在的模块，作为 B15 起点基线（`64e0f0a`，含 QA 用例）：

| 关键模块 | 当前行覆盖 | ≥90% 目标 | 状态 |
| --- | --- | --- | --- |
| `business/policy_service.py` | **99.18%** | ≥90% | ✅（修复后从 84.26% 大幅提升） |
| `business/grant_service.py` | **98.29%** | ≥90% | ✅ |
| `db/session.py` | **95.76%** | ≥90% | ✅ |
| `business/usage_service.py` | **87.44%** | ≥90% | ❌ **差 2.56pp**（未覆盖：`638-658` 逐能力差值分支等） |
| `business/subscription_service.py` / `business/order_service.py` | — | ≥90% | ⏳ 端点未实现（B09 落地后纳入） |

**关联但不在新关闭条件名单内、仍需关注的低覆盖模块**：`member_service 71.68%`、`org_repo 77.03%`、`role_service 80.00%`（三者合计缺 108 行未覆盖，是 `business+db` 整体只到 85.67% 的主因）。

**结论**：B15 的「关键模块 ≥90%」在 W2 时点**尚不满足**（`usage_service` 87.44%）。此项**不属于 W2 准出判据**（修订口径只把它列为 B15 关闭条件），但已登记为 **B15 待办**，避免阶段收尾时才发现。

---

## 5. 缺陷台账（本轮）

| 编号 | 等级 | 标题 | 状态 |
| --- | --- | --- | --- |
| B08-P0-01 | P0 | 业务库角色降级不生效（撤权后仍可读未授权受限库） | ✅ **已关闭**（`2df2831`，§2.1） |
| B07-P1-01 | P1 | 对账偏差超限审计告警丢失（G-23 判据不成立） | ✅ **已关闭**（`2df2831`，§2.2） |
| B08-02 | P1 | 部门 ID 两种形态判权结果不一致（裸 ID 授权静默失效） | ✅ **已关闭**（`grant_repo` 统一口径，§2.3） |
| QA-P2-W2-04 | P2 | G-24 空跑（0 条 p0 用例） | ✅ **已关闭**（130 条，§3.1） |
| QA-P2-W2-05 | P2 | G-05 新模块覆盖率跌破阈值 | ✅ **已关闭**（85.67%，§4.1） |
| **QA-P2-W2-09** | P2（风险） | 工程师**自有**用例覆盖率 84.99%（< 85%），达标依赖 QA 用例 | ⏳ **登记，B09 自查项**（§4.3） |
| QA-P3-W2-06 | P3 | 「角色被引用」文案分支在 B08 内不可达（无自定义角色入口） | ⏳ 待 U5 批次复验 |
| QA-P3-W2-07 | P3 | `mock_mode` 下 `automation` 事件与中断折算路径不可达 | ⏳ 建议启动日志标注 |
| QA-P3-W2-08 | P3 | 对账口径偏离（Valkey→事件表），需按 W-03 体例登记 | ⏳ 待登记（主理人域） |
| **QA-P2-W2-10** | P2（B15 待办） | 修订后的 W-01 关闭条件要求「关键模块单模块 ≥90%」，当前 `usage_service` **87.44%** 未达标（`member_service 71.68%` / `org_repo 77.03%` / `role_service 80.00%` 亦偏低） | ⏳ **B15 前补测**（§4.4），不阻断 W2 |

**残留 P0 / P1：0 / 0** ✅（P2 风险 2 条、P3 3 条，均不阻断 W2 准出）

---

## 6. 准出结论与路由

### 6.1 准出判据核对

| 判据 | 要求 | 实际 | 结论 |
| --- | --- | --- | --- |
| P0 | 0 | 0 | ✅ |
| P1 | 0 | 0 | ✅ |
| 13 条验收点 | 全过 | 13 / 13 | ✅ |
| G-24 | p0 ≥60 且 0 failed | 130 条；非 PG `109 passed/21 skipped/0 failed`；PG `130 passed` | ✅ |
| G-12 / G-13 / G-23 | 绿 | ✅ / ✅ / ✅（含负例审计） | ✅ |
| 覆盖率两项 | 总 ≥85%（基线 86.88%）/ 新模块 ≥85%（按 `94671a3` 修订口径） | 87.95% / 85.67%（G-05 命令口径） | ✅ |

### 6.2 结论

> **PASS —— W2 准出。** 第 1 轮的 1 个 P0（撤权后仍读未授权受限库）与 2 个 P1（对账审计丢失、部门 ID 形态）均已修复并经 QA 独立复验转绿；`2df2831` 全量 `969 passed / 0 failed / 0 skipped`；G-24 从「空跑 0 条」改为「130 条红线用例且 `--run-pg` 全绿」；覆盖率按修订口径两项达标（总 87.95% 高于基线 1.07pp、新模块 85.67%）。

**已入库情况（如实说明）**：QA 在收到「改完不要 commit」指示**之前**，已把 p0 标记改动与两份报告提交为 **`64e0f0a`**（只含 12 个文件：`pyproject.toml`、8 个测试文件的 `pytestmark`、`test_qa_b07_b08_verification.py`、两份报告；**未触碰任何源码，也未触碰 B09 的 `test_billing_and_callback.py` / `test_order_service.py` / `test_subscription_service.py`**）。该提交为 `git commit -- <paths>` 定向提交，未使用 `git add -A`，B09/B10 的索引未受影响、未发生 `index.lock` 竞争。若主理人希望以自己的提交信息收口，可直接 `git commit --amend` 或在后续提交中覆盖，QA 不做任何破坏性 git 操作。

### 6.3 路由

| 对象 | 内容 |
| --- | --- |
| **→ NoOne** | W2 缺陷闭环完成，无待修项；QA 转待命，等 W3（B09+B10）任务书 |
| **→ team-lead（知会）** | ① `64e0f0a` 已包含 p0 标记入库（如需自有提交信息请 amend）；② QA-P3-W2-08 由主理人补登记为 **W-04**，测试方案 G-23 条目已加「口径以 `docs/qa-phase2-waivers.md` W-04 为准」；③ **DoD 已按指示写入 `docs/qa-phase2-test-plan.md` §2.5**（分批待补清单 + 禁止空标记硬规则 + `-m p0 --run-pg` 须 `0 failed / 0 skipped`），供阶段验收报告引用 |
| **→ Engineer（B09 起自查项，非 W2 阻塞）** | 工程师自有用例覆盖 84.99%（新模块阈值余量仅 0.01pp）；U5 打通自定义角色后复验 `MESSAGE_ROLE_IN_USE` 文案分支；`mock_mode` 下 `automation`/中断折算不可达，建议启动日志标注 |

---

## 7. 证据声明（防「为过门禁而标记未实现功能」）

给阶段验收留证，明确以下事实：

1. **130 条 p0 标记全部打在已实现端点的真实断言上**，逐条可执行、无 `pass`/`assert True` 之类的空断言，无 `xfail`/`skip` 兜底（`-m p0 --run-pg` 实测 `130 passed, 0 skipped`，跳过数为 0 即证明没有被 skip 掩盖的用例）。
2. **未给任何未实现功能挂 p0 标记**：24 条 P0 中 10 条因端点未实现而**空置**（清单见 §3.2），宁可让「24 条全覆盖」这一自检项判红，也不挂空标记制造假绿。
3. **QA 自有用例的独立性**：`test_qa_b07_b08_verification.py` 由 QA 独立编写与维护，断言口径不受工程师调整（W1 §5.6 约定）；本轮该文件中 3 条曾为红灯的缺陷断言在修复后转绿，未做任何放宽（`git diff` 可见断言强度未降）。
4. **可复算**：本报告所有数字均为 QA 本机在 `git archive 2df2831` / `64e0f0a` 导出副本上实跑，命令与关键输出已逐条贴出，任何人可复现。
5. **计量对账的数据源口径**：`reconcile_usage.py` 的「第二次来源」由架构 §2.5 的 Valkey 诊断计数改为 `usage_event` 事件表，**口径以 `docs/qa-phase2-waivers.md` W-04 为准**（主理人 2026-10-22 登记，提交 `c610c7f`）。G-23 的正例/负例判定均按 W-04 口径执行。

---

## 8. 主理人裁定与文档落实（定稿记录）

| # | 裁定 / 指示 | 落实位置 | 状态 |
| --- | --- | --- | --- |
| 1 | 覆盖率口径**不收紧**（G-05 命令口径含全部用例；QA-P2-W2-09 保留 P2 不阻断；B09 起 engineer-only ≥85% 进工程师自查项，B15 前自然达标） | 本报告 §4.3 | ✅ 已按裁定定稿 |
| 2 | 把「分批 DoD：批次落地即补 p0 标记」写进测试方案，列明 `BL-01/02/04 → B09`、`BL-05 → B10`、`UI-01~05/UX-01 → B11-B15`，并注明「禁止给未实现端点挂空标记」 | `docs/qa-phase2-test-plan.md` **§2.5**（新增，1592 行处）+ G-24 条目加 `§2.5` 引用 | ✅ 已落地 |
| 3 | 对账口径偏离（Valkey → `usage_event` 二次来源）由主理人补 W-04；测试方案 G-23 条目加引用 | `docs/qa-phase2-test-plan.md` **G-23 行**（第 118 行）已加「以 `docs/qa-phase2-waivers.md` W-04 为准」 | ✅ 已落地（W-04 由主理人域登记） |

**W2 状态：准出（PASS），QA 转待命，等 W3（B09+B10）冻结提交号。**

---

*报告人：严过关（QA 工程师）· 2026-10-22 · 复核基线 `2df2831` / `64e0f0a`（`git archive` 导出复核，未读活工作区）*
