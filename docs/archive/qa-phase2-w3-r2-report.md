# 阶段二 W3 第二轮复核报告（B09 席位修复验证）

| 项 | 内容 |
| --- | --- |
| 报告编号 | **QA-P2-W3-R02** |
| 复核对象 | **`20b8c45`**（`fix(adapter): 席位校验以实时用量为准并接入邀请入口（QA W3-01/02/03）`，4 文件 / +147 −13） |
| 基线对照 | `837f19b`（W3 第 1 轮判据基线）→ `686b387`（回调拒绝路径加固，R1 复验）→ **`20b8c45`**（本轮；其父提交 `fa78817` 为纯测试提交 D14-1） |
| QA 工程师 | 严过关（Yan） |
| 复核日期 | 2026-10-22 |
| 取样方式 | **`git archive 20b8c45` 导出到独立目录复核，全程不读活工作区**（主树其后另落 `8304a80` 迁移定性改名 `0002_callback_idempotency_selfcheck.sql` 与 `4c832c3` R2 `base_id` 加性提交，均不含行为变更，第二步在最终 HEAD 补跑 4 项，见 §8）；`COVERAGE_FILE` 落在导出目录，未写回主仓 |
| **最终判定** | **PASS（准出 B09，B10 已于 W3 单批放行）** —— 最终冻结点 `3b82df2` 复核结果见 **§9**（1119 passed / p0 178 / 89.99% / 89.10% / order_service 100%）：**0 P0 / 0 P1**；11 条验收点 **11/11**；门禁 9 项全绿（`0 failed / 0 skipped / 0 xfail / 0 xpass`）；覆盖率 **89.78% / 88.74%**（均较基线上升）；W-01 补偿控制的 **6 个关键模块全部 ≥90%** |
| 路由 | → **NoOne**（W3 闭环，转待命）；3 条知会见 §7 |

---

## 0. 结论速览

| 项 | R1（`686b387`） | R2（`20b8c45`） | 判定 |
| --- | --- | --- | --- |
| P0 | 0（1 条已关闭） | **0** | ✅ |
| P1 | **2**（席位判据） | **0** | ✅ 全部关闭 |
| P2 | 2（conftest 夹具、ruff I001） | **0** | ✅ 全部关闭 |
| 11 条验收点 | 10/11（B09-④ 不通过） | **11/11** | ✅ |
| 我的验证用例 | 18（2 xfail） | **19 全绿**（13 条 p0） | ✅ |
| 全量 `--run-pg` | 1070 passed / 2 xfailed | **1108 passed / 0 skipped / 0 xfail / 0 xpass** | ✅ |
| 总覆盖率 | 88.77% | **89.78%** | ✅ |
| `business+db` | 87.05% | **88.74%** | ✅ |
| p0 用例 | 155 | **173 全绿** | ✅ |
| `validate_openapi` | 38/38 | **38/38** | ✅ |

---

## 1. 缺陷关闭验证

### 1.1 QA-P2-W3-01（P1）BL3 席位判据用落库快照 → ✅ 关闭

| 项 | 内容 |
| --- | --- |
| 修复实现 | 抽出单一出口 `SubscriptionService.ensure_seat_available(tenant_id, target_seats, extra_reserved=0)`；BL3/BL4 传 `extra_reserved=0`，判据为 `used + reserved > target`；`_resolve_seats()` 不再读 `record.seats_used` |
| 关闭证据 | `test_b09_4b_bl3_seat_guard_must_use_live_seats_used`：升级 8 席 → 造 8 名在册成员 → 支付掉升级单（排除 15004）→ BL3 传 `seats=5` ⇒ **400 / `15003`**，且订阅未落成「已用 > 总数」 |
| 附带校验 | `test_b09_4d_reduce_seats_to_exactly_used_is_allowed`：10 席减到 8 席、正好 8 人在册 ⇒ **200**、`seats_total=8`、`seats_used=8`、`order_id=""`；再减到 7 ⇒ `15003` |

> 主理人裁定的两处「未按原话但正确」的语义，我用**双向断言**验证：
> ① 判据是 `>`（`used + reserved > target`）而非 `>=`——「正好用满」必须放行（4d 与 4c 的反面各钉一次）；
> ② `15003` 文案保持架构 §7.1 冻结原文「成员席位已用满，请先增加席位」（4c 精确等值断言，实测一致）。

### 1.2 QA-P2-W3-02（P1）邀请路径无席位校验 → ✅ 关闭

| 项 | 内容 |
| --- | --- |
| 修复实现 | `identity_service.invite_member` 接入 `ensure_seat_available(target_seats=当前 seats_total, extra_reserved=1)`；待接受邀请数当前恒为 0（无邀请持久化），实现注释已按「邀请表落地后加和」预留 |
| 关闭证据 | `test_b09_4c_invite_member_blocked_when_seats_full` **两侧都断言**：免费版 3 席、在册 2 ⇒ 邀请 **200 放行**（2+1 == 3）；在册 3 ⇒ 邀请 **400 / `15003`** + 文案精确等值 |

### 1.3 QA-P2-W3-03（P2）conftest 未重置组织存储 → ✅ 关闭

`tests/conftest.py::_reset_all()` 已加 `reset_org_store()`（+4 行）。验证方式：我的 4 条席位用例在**整文件连跑**下结果与单跑一致（此前会因上一个用例的在册成员顶高 `seats_used` 而漂移）。

### 1.4 QA-P2-W3-06（P2）ruff `I001` 门禁红 → ✅ 关闭

`ruff check .` → **`All checks passed!`**（`tests/unit/test_callback_hardening.py` 的导入已合并）；`ruff format --check .` → `161 files already formatted`。

### 1.5 QA-P2-W3-05（P0，R1 已关闭）回顾

回调拒绝路径的幂等键污染已在 `686b387` 修复、R1 复验关闭；本轮**回归未回退**：

| 断言 | 结果 |
| --- | --- |
| 脏回调（同流水号 + 错签名） | 400 / `20001`；订阅 none、发票 0；**真实 txn 未被占用** |
| 同流水号 + 正确签名 | 200 / `SUCCESS`、订单 `paid`、订阅生效、发票 1 张 |
| 真通知再发 2 次 | 发票仍 1、订阅 `(subscription_id, started_at)` 不变 |
| 拒绝留痕 | `payment_callback_log` 有且仅 1 条 `signature_ok=False` 且 `processed=False`（TC-P2-BL04-04） |
| 拒绝审计 | `billing.payment_callback_rejected` 存在 |

### 1.6 QA-P2-W3-04（观察）W-01 补偿控制 → ✅ 提前达标（可关闭）

| 关键模块 | B15 要求 | R1 | R2 |
| --- | --- | --- | --- |
| `policy_service` | ≥90% | 99.18% | **99.18%** |
| `grant_service` | ≥90% | 98.29% | **98.29%** |
| `subscription_service` | ≥90% | 90.50% | **91.89%** |
| `order_service` | ≥90% | 87.79% ❌ | **90.91%** ✅ |
| `usage_service` | ≥90% | 88.48% ❌ | **99.54%** ✅ |
| `db/session` | ≥90% | 95.90% | **95.90%** |

6/6 全部达标 ⇒ 该项在 B09 收尾即已满足，**建议从 B15 待办中移除**（B15 只需复核不回退）。

---

## 2. 门禁实跑（`20b8c45` 导出副本，逐条退出码 + 关键输出）

| 编号 | 命令 | 结果 |
| --- | --- | --- |
| G-01 | `python -m ruff check .` | `All checks passed!` |
| G-02 | `python -m ruff format --check .` | `161 files already formatted` |
| G-03 | `python -m mypy app` | `Success: no issues found in 95 source files` |
| G-04 | `OA_TEST_DATABASE_URL=… pytest -q --run-pg -rxX` | **`1108 passed`，0 failed / 0 skipped / 0 xfail / 0 xpass** |
| G-05a | 同上 `--cov=app` | `TOTAL 10112 819 1892 296 **89.78%**` |
| G-05b | `coverage report --include="app/business/*,app/db/*"` | `TOTAL 5898 554 1200 181 **88.74%**` |
| G-07/08/09 | `python deploy/validate_openapi.py`（严格，无 `--allow-missing-phase2`） | `core=30 phase2=38/38 （未实现 0）` / `G1-G5 全部通过` |
| G-12 | `test_db_session + test_tenant_isolation -m pg --run-pg` | **15 passed** |
| G-13 | `python scripts/check_error_catalog.py --strict` | `EXIT=0`，`错误码三处一致门禁通过`（0 PENDING） |
| G-23 | `reconcile_usage.py --period 2026-10 --threshold 0.01` | `EXIT=0`、`[PASS] 偏差=0.0000%`（**空账期，见 §6.2**） |
| G-24 | `pytest -q -m p0 --run-pg` | **173 passed / 0 failed / 0 skipped** |
| 回归组 | `test_org_endpoints + test_permission_matrix --run-pg` | **49 passed**（U4 改动影响面） |
| 回归组 | `test_tool_executions --run-pg` | **14 passed** |

---

## 3. 覆盖率两项（口径与趋势）

| 项 | 阈值 | `837f19b` | `686b387` | **`20b8c45`** | 判定 |
| --- | --- | --- | --- | --- | --- |
| 总覆盖率 | W-01 修订：硬地板 85% / B15 收尾 ≥88% | 88.34% | 88.77% | **89.78%** | ✅（已过 B15 的 88% 线） |
| `app/business/*` + `app/db/*` | ≥85%（W-02 未豁免） | 86.39% | 87.05% | **88.74%** | ✅ |

> 口径说明：以上为 QA 本轮 `pytest -q --run-pg --cov=app --cov-report=term` 单次实跑值，`COVERAGE_FILE` 位于导出目录。**不引用任何他方自测数字**（过程稿曾出现 89.75% / 85.36% 等口径，来源为不同提交的他人自测，已作废）。
> 分段增长原因：`fa78817`（D14-1，纯测试提交，+~36 条分支覆盖用例）与我的 19 条验证用例共同贡献；`order_service` 从 87.79%→90.91%、`usage_service` 88.48%→99.54% 是本轮 W-01 补偿控制达标的主因。

### 3.1 用例总数分解（可审计，解释与他人自测数字的差异）

| 口径 | 收集数 | 执行结果 |
| --- | --- | --- |
| `20b8c45` 树（**不含** QA 验证文件） | **1089** | — |
| + QA 验证文件 **19 条** | **1108** | `1108 passed / 0 skipped / 0 xfail / 0 xpass` |

> 过程稿中出现过「1103 passed + 2 XPASS、p0 170」的参考值，对应的是一份 **18 条**（两条仍带 `xfail`）的 QA 文件；
> 本报告交付的版本为 **19 条**（4b/4c 摘标记升 p0、新增 4d 边界用例），因此执行数为 **1108**、p0 为 **173**。
> 差异可由上表逐项核对：`+1`（新增 4d）、`+2`（两条 xfail 转 passed）。若他方执行数比 1107 少 2，说明其运行环境里有 2 条用例被 skip，**需单独确认**（`--run-pg` 口径要求 0 skip）。

---

## 4. 11 条验收点复核（R2 全绿）

| # | 验收点 | R1 | R2 | 证据用例 |
| --- | --- | --- | --- | --- |
| B09-① | 免费版 `active` / `seats_total=3` | ✅ | ✅ | `test_b09_1_*` |
| B09-② | 升级生成订单 + 功能开关立即变化 | ✅ | ✅ | `test_b09_2_*` |
| B09-③ | 降级 `effective_at`=周期末、周期末前仍原套餐 | ✅ | ✅ | `test_b09_3_*` |
| B09-④ | 席位用满 `15003` | ❌ | **✅** | `test_b09_4_*`（BL4）、`test_b09_4b_*`（BL3）、`test_b09_4c_*`（邀请，两侧）、`test_b09_4d_*`（边界等于） |
| B09-⑤ | 回调 3 次只生效一次、不重复开票 | ✅ | ✅ | `test_b09_5_*`、`test_extra_4_*` |
| B09-⑥ | 待支付再下单 `15004` 且不产生第二单 | ✅ | ✅ | `test_b09_6_*` |
| B09-⑦ | 30 分钟未支付自动 `closed` | ✅ | ✅ | `test_b09_7_*` |
| B10-① | 企业 token 全 403 且不泄露 | ✅ | ✅ | `test_b10_1_*`、`test_b10_1b_*` |
| B10-② | 核销 paid + 订阅生效 + 审计含运营人/备注 | ✅ | ✅ | `test_b10_2_*` |
| B10-③ | 统计与 BL5 求和误差 0 | ✅ | ✅ | `test_b10_3_*` |
| B10-④ | 响应无内容/邮箱/凭据 | ✅ | ✅ | `test_b10_4_*` |

附加 4 条（唯一免鉴权路径、支付密钥缺失返业务错误、`15001-15009` 文案与前端覆盖、M4/BL5 同源）同样全绿（`test_extra_1/2/3_*`）。

---

## 5. 用例文件变更（本轮）

| 项 | 变更 |
| --- | --- |
| 文件 | `apps/adapter/tests/integration/test_qa_b09_b10_verification.py` |
| 条数 | 18 → **19**（13 条 p0） |
| xfail | **2 → 0**（4b / 4c 按约定摘标记并升 `p0`，docstring 记录缺陷编号与关闭提交） |
| 新增 | `test_b09_4d_reduce_seats_to_exactly_used_is_allowed`（固定 `used == target` 必须放行的边界） |
| 强化 | `test_b09_4c_*` 改为**两侧断言**（2+1==3 放行 / 3+1>3 拒绝），防止「修 A 引入 B」 |
| 全局 | 全仓 `mark.xfail` 计数 **0**；无新增 `skip` |

---

## 6. 残余风险与未验证项（如实登记）

### 6.1 邀请占位（阶段三前置）

**未验证**：待接受邀请数对席位的占用。原因：当前**无邀请持久化**——`invite_member` 不落库、`deploy/init-schema-phase2.sql` 与 `deploy/migrations/` 均无 invitation 表。实现已按 `extra_reserved` 预留参数并注释说明，但**「在册 + 待接受邀请 ≥ 总席位」这一完整口径无法在本轮验证**。已按主理人裁定登记为阶段三前置（随邀请表落地启用，调用方签名不变）。

### 6.2 G-23 的「绿」在空账期上成立

`reconcile_usage.py` 本轮 `EXIT=0 / 偏差 0.0000%`，但该账期 `成员求和 = 企业总量 = 事件总额 = 0`，属**空数据恒等**，不能作为对账能力已充分验证的证据。真实负例判据仍由 `test_b07_5c`（偏差超限 → `EXIT=1` + `billing.reconcile_mismatch` 审计）覆盖。

### 6.3 总覆盖率与原始阈值的关系

原始 G-05 阈值为 90.5%，现为 89.78%（差 0.72pp）。该阈值已由 W-01 修订为「硬地板 85% + B15 收尾 ≥88% + 6 个关键模块 ≥90%」，三项本轮全部满足；若后续恢复 90.5% 口径，需在 B15 补测中补齐。

### 6.4 席位判据的并发窗口（观察，不阻断）

`ensure_seat_available` 先读实时在用数再写订阅，属「先检查后写入」；并发邀请/调席时存在理论上的检查-写入间隙。当前 U4 为桩、席位变更仅 owner 可发起，实际并发概率极低；**未构造并发用例**，登记为已知边界。若阶段三引入邀请表与并发邀请，应改为唯一约束或行锁保证。

---

## 7. 证据声明与路由

### 7.1 证据声明（防「为过门禁而标记未实现功能」）

1. 本轮**未新增任何 `skip` / `xfail` / 放宽断言**：19 条用例全部为真实断言，摘标记后连跑稳定；
2. 全仓 `mark.xfail` 计数为 **0**，`-rxX` 输出无 `XFAIL` / `XPASS`；
3. 席位判据的**两侧**（放行 / 拒绝）均有断言，避免「只测拒绝、把边界写错」；
4. 覆盖率为单次实跑，未拼接不同提交的数字；
5. 空账期 G-23 已在 §6.2 明确降级其证据强度。

### 7.2 路由

| 对象 | 内容 |
| --- | --- |
| **→ NoOne** | W3 闭环：0 P0 / 0 P1，11/11，门禁 9 项全绿，可出阶段二验收报告。 |
| **→ 主理人（知会 3 条）** | ① `QA-P2-W3-04`（W-01 补偿控制）已提前达标，建议从 B15 待办移除、改为「B15 复核不回退」；② §6.1 邀请占位按裁定登记阶段三前置；③ §6.4 席位判据存在「先检查后写入」的理论并发窗口，建议在邀请表/并发邀请落地时一并处理，本轮不阻断。 |
| **→ Engineer（无需动作）** | 5 条缺陷（1 P0 / 2 P1 / 2 P2）全部关闭；无新增缺陷。 |

---

## 8. 第二步（最终 HEAD 补跑，待 SHA）

`20b8c45` 之后仅两笔非行为变更（`8304a80` 迁移定性改名、`4c832c3` R2 `base_id` 加性）。最终 HEAD 到手后补跑 4 项：

| # | 命令 | 判据 |
| --- | --- | --- |
| 1 | `pytest -q --run-pg` | 0 failed / 0 skipped / 0 xfail / 0 xpass（本步基线 **1108**，`4c832c3` 增 2 条用例后应为 1110 左右，只增不减） |
| 2 | `pytest -q -m p0 --run-pg` | **≥173** 且 0 failed / 0 skipped |
| 3 | `pytest tests/integration/test_qa_b09_b10_verification.py -q` | **19/19 绿**（xfail 已摘除） |
| 4 | `deploy/validate_openapi.py` | `phase2=38/38`、`G1-G5 通过` |

---

## 9. 最终 HEAD 复核（`3b82df2`，第二步实跑）

> 冻结点：**`3b82df2`**（`3659f28` = W3 文档与验证用例入库 → `3b82df2` = W3-04 纯测试提交，仅改 `tests/unit/test_order_service.py` +9 用例）。
> 取样：`git archive 3b82df2` 导出到独立目录；`COVERAGE_FILE` 在导出目录。代码至此封板。

### 9.1 四项确认（全部绿）

| # | 命令 | 结果 | 判据 |
| --- | --- | --- | --- |
| 1 | `pytest -q --run-pg -rxX` | **1119 passed**，0 failed / 0 skipped / **0 xfail / 0 xpass** | ✅（期望 1119） |
| 2 | `pytest -q -m p0 --run-pg` | **178 passed / 0 failed / 0 skipped** | ✅（≥173） |
| 3 | `pytest tests/integration/test_qa_b09_b10_verification.py -q` | **19 passed**（树内版本：19 条 / 13 条 p0 / 0 xfail） | ✅（19/19） |
| 4 | `python deploy/validate_openapi.py` | `core=30`、`phase2=38/38 （未实现 0）`、`G1-G5 全部通过` | ✅（38/38） |

### 9.2 覆盖率明细（供验收报告直接引用）

| 口径 | `20b8c45`（第一步） | **`3b82df2`（最终）** | 阈值 / 说明 |
| --- | --- | --- | --- |
| 总覆盖率（`--cov=app`） | 89.78% | **89.99%** | W-01：硬地板 85%，B15 目标 ≥88% → ✅ |
| `app/business/*` + `app/db/*` | 88.74% | **89.10%** | G-05 新模块阈值 85% → ✅ |
| `app/business/*` | — | **93.37%** | 参考值 |
| `order_service`（单模块） | 90.91% | **100.00%**（187 语句 / 44 分支，0 未覆盖） | W-01 补偿控制 ≥90% → ✅ |
| 迁移目录 | — | 仅 `0002_callback_idempotency_selfcheck.sql` | 与定性一致（不含首建） |

### 9.3 第一步 → 第二步的增量（全部来自纯测试提交，无行为变更）

| 指标 | `20b8c45` | `3b82df2` | 增量 |
| --- | --- | --- | --- |
| 全量用例 | 1108 | **1119** | +11（`4c832c3` 的 2 条 base_id 用例 + `3b82df2` 的 9 条 order_service 分支用例） |
| p0 用例 | 173 | **178** | +5 |
| 总覆盖率 | 89.78% | **89.99%** | +0.21pp |
| `business+db` | 88.74% | **89.10%** | +0.36pp |
| `order_service` | 90.91% | **100.00%** | +9.09pp |

### 9.4 最终判定

> **PASS（W3 正式闭环，B09 准出）**：最终冻结点 `3b82df2` 上 **0 P0 / 0 P1**、11/11 验收点、四项确认全绿、0 failed / 0 skipped / 0 xfail / 0 xpass、覆盖率 **89.99% / 89.10%**（`order_service` 100%）。**B10 已于 W3 第 1 轮单批放行。**
> 三条残余项（邀请占位未验证、G-23 空账期、席位判据理论并发窗口）见 §6，均为「知限」而非缺陷。

---

*报告人：严过关（QA 工程师）· 2026-10-22 · 第一步判据以 `20b8c45` 为准，最终复核以 `3b82df2` 为准（§9）；所有数字均为本机单次实跑，未引用他人口径。*
