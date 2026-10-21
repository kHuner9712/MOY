# 阶段二 W3 独立验证报告 —— B09 计费订阅与订单支付 + B10 运营后台接口

> 验证人：严过关（QA 工程师）· 日期：2026-10-22 · 冻结提交：**`837f19b`**（B09 `5776d4d` + B10 `837f19b`）
> **R2 已闭环**：`20b8c45` 上 0 P0 / 0 P1、11/11 验收点、门禁全绿 ⇒ **PASS**，结论与数字见 **`docs/qa-phase2-w3-r2-report.md`**；本文件保留第 1 轮判据原貌以便回溯。
> 判据来源：`docs/architecture-phase2.md` §9.2 B09/B10「完成后可验证」+ §4.5/§4.6 域契约 + §7 错误码；
> `docs/qa-phase2-test-plan.md` §2.1 门禁表、§2.5 分批 DoD、§4.4/§4.5 用例矩阵、§7.1 缺陷分级；
> `docs/qa-phase2-w2-report.md` §5 缺陷模式（判权 fail-open / 审计缺失 / 门禁空跑）。

---

## 0. 结论速览

| 项 | 结果 |
| --- | --- |
| 后端 38/38 端点 | ✅ `phase2=38/38`（未实现 0），`core=30` 未被破坏（G1-G5 全过） |
| 门禁实跑（G-01~G-24 后端部分） | ✅ **`837f19b` 上全绿**（逐条见 §2，均为退出码 0 + 关键输出留痕）；⚠️ R1 修复提交 `686b387` 上 **G-01 ruff check 转红 1 处**（`W3-06`，见 §8.1） |
| 11 条验收点 | ⚠️ **10 条通过 / 1 条不通过**（B09-④「席位用满 `15003`」在 2 条子路径上失效，见 §3.4） |
| 缺陷 | **0 P0（1 个 P0 已在 `686b387` 关闭并经 QA 复验）/ 2 P1（席位，未修）/ 2 P2 / 1 观察**（见 §5、§3.13） |
| 覆盖率两项 | 总 **88.65%**（W-01 修正后硬地板 85%，达标；B15 目标 ≥88%，已过线）/ business+db **86.84%**（阈值 85%，达标）；`686b387` 上为 **88.77% / 87.05%** |
| P0 用例 | **151 条**（阈值 ≥130 / ≥60），`-m p0 --run-pg` → `151 passed / 0 failed / 0 skipped`；`686b387` 上为 **155 条全绿** |
| **准出判定** | ❌ **FAIL（不准出）** —— 存在 2 个 P1（席位判据），按测试方案 §7.1「P1 阻断该批次准出」；**B10 无 P0/P1，单批可放行** |
| **路由** | → **Engineer**（2×P1 + 2×P2，含 G-01 门禁红）；→ 主理人（编号对账 §5.2）；→ NoOne 不适用 |

**一句话**：B09 的支付/订单主干（幂等三道防线、金额单位、脱敏、超时关闭、审计）与 B10 的最小可见原则、平台守卫、统计同源**实测全部成立且质量高**；阻断项集中在**席位判据**——BL4 正确、BL3 与邀请路径未生效。

---

## 1. 验证基线与环境

| 项 | 内容 |
| --- | --- |
| 被测树 | `git archive 837f19b \| tar -x -C /tmp/qaw3_837f19b`（**不读活工作区**，避免 B11-b/B12 前端改动污染） |
| 解释器 | `apps/adapter/.venv/Scripts/python.exe`（Python 3.13.14，主仓 venv，仅作解释器使用） |
| 数据库 | PostgreSQL `127.0.0.1:55433/officeai`，应用角色 `officeai_app`（`NOSUPERUSER NOBYPASSRLS`，夹具自带 `rolbypassrls` 自检） |
| 覆盖率文件 | `COVERAGE_FILE=/tmp/qaw3_837f19b/.coverage*`（不落主树，避免与前端工程师并跑互相覆盖） |
| 业务存储 | 无 DSN 轮 = `OA_BUSINESS_STORE=memory`；带 `--run-pg` 轮 = 默认（内存）+ PG 专测并存 |
| 交付树与冻结树的差异 | 仅新增 `tests/integration/test_qa_b09_b10_verification.py`（**18 条**，QA 独立编写；其中 1 条为 P0 回归、2 条为缺陷钉住）；`app/**`、`deploy/**` **零改动** |

### 1.1 三组基线数字（区分「批次自带」「QA 用例增量」「R1 修复增量」）

| 指标 | 冻结树 `837f19b`（工程师用例） | 交付树（+ W3 用例） | R1 修复后 `686b387`（+ W3 用例） |
| --- | --- | --- | --- |
| 用例收集 | 1044 | 1061 | **1072** |
| `pytest -q`（无 PG） | 1000 passed / 44 skipped | 1015 passed / 44 skipped / 2 xfailed | **16 passed + 2 xfailed**（仅 W3 文件）；全量见下行 |
| `pytest -q --run-pg` | 1044 passed / 0 skipped | 1059 passed / 0 skipped / 2 xfailed | **1070 passed / 0 skipped / 2 xfailed / 0 failed** |
| 总覆盖率 | 88.34% | 88.65% | **88.77%** |
| `app/business+db` 覆盖率 | 86.39% | 86.84% | **87.05%** |
| p0 用例 | 142（工程师） | 151 | **155 passed / 0 failed / 0 skipped** |

> 「xfailed」是 QA **显式钉住缺陷**的 `xfail(strict=True)` 用例：缺陷未修时不计为红（不污染他人回归），**修好后自动 XPASS→hard error**，强制摘除标记。
> `837f19b` 上是 2 条（2×P1 席位）；`686b387` 上仍为同样的 2 条——回调 P0 修复后，第 3 条（`test_extra_4`）如预期转 XPASS，QA 已按约定**摘除标记并转常绿 p0 回归**（见 §3.13）。

---

## 2. 门禁实跑（逐条：命令 / 退出码 / 关键输出）

工作目录：`/tmp/qaw3_837f19b/apps/adapter`（除 G-07/22 在仓库根 `/tmp/qaw3_837f19b`）。

| 编号 | 命令 | 退出码 | 关键输出（实跑留痕） | 判定 |
| --- | --- | --- | --- | --- |
| G-01 | `python -m ruff check .` | 0 | `All checks passed!` | ✅ |
| G-02 | `python -m ruff format --check .` | 0 | `155 files already formatted` | ✅ |
| G-03 | `python -m mypy app` | 0 | `Success: no issues found in 95 source files` | ✅ |
| G-04a | `python -m pytest -q` | 0 | `1015 passed, 44 skipped, 2 xfailed`（44 skip 均为 PG 用例，属口径内） | ✅ |
| G-04b | `OA_TEST_DATABASE_URL=… python -m pytest -q --run-pg` | 0 | `1059 passed, 2 xfailed`，**skipped = 0** | ✅ |
| G-05a | 同上 `--cov=app --cov-report=term` | 0 | `TOTAL 10079 900 1890 308 **88.65%**` | ✅（W-01：硬地板 85%，B15 目标 88%） |
| G-05b | `python -m coverage report --include="app/business/*,app/db/*"` | 0 | `TOTAL 5871 635 1198 193 **86.84%**`（阈值 85%） | ✅ |
| G-06 | `pytest --collect-only -q \| tail -1` | 0 | `1061 tests collected`（基线 1044 + QA 17） | ✅ |
| G-07/08/09 | `python deploy/validate_openapi.py`（**不带** `--allow-missing-phase2`） | 0 | `端点统计: core=30 phase2=38/38 （未实现 0）` / `G1-G5 全部通过` | ✅ |
| G-13 | `python scripts/check_error_catalog.py --strict` | 0 | `架构 §7.1 = 14 码；errors.py = 52 码；business-errors.ts = 52 码；放行域 = 无（严格）`；`14/14 已落地`；`52 个码全部覆盖`；`门禁通过` | ✅（0 PENDING） |
| G-22a | `python deploy/validate_deps.py` | 0 | `运行时依赖 15 条` / `依赖门禁通过` | ✅ |
| G-22b | `python deploy/validate_images.py` | 0 | `compose 引用 14 个镜像，images.lock 登记 14 个` / `镜像清单校验通过` | ✅ |
| G-24 | `OA_TEST_DATABASE_URL=… python -m pytest -q -m p0 --run-pg` | 0 | **`151 passed, 910 deselected`**，`failed=0`、`skipped=0` | ✅（阈值 ≥130，实测 151） |

> 说明：`pytest -q -m p0`（不带 `--run-pg`）为 `121 passed + 21 skipped`，**不计为通过**——§2.5 规则 3 要求 `skipped=0`，故上表采用 `--run-pg` 口径。
> 未执行项：本期无（镜像可拉取性由 `validate_images.py` 的登记一致性覆盖，实际拉取依赖网络，按 §2.3「例外」不计入通过）。

---

## 3. 11 条验收点逐条判定

### 3.1 B09-① 免费版开通 `status=active`、`seats_total=3` —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| `GET /billing/subscription`（未订阅，member 角色） | `plan_code=free`、`status=active`、`seats_total=3`、`subscription_id=""`，**非 404** |
| `POST /billing/subscription {plan_code:free seats:1 action:subscribe}` | `code=0`、`status=active`、`order_id=""`（不产生订单）；BL2 回读 `seats_total=3`（前端传 1 也按 3 开通） |
| 落库校验 | `get_active_subscription` 返回真实行（`status=active`、`seats_total=3`），**不是只靠合成视图** |
| 用例 | `test_b09_1_free_plan_subscribe_is_active_with_three_seats`（p0） |

### 3.2 B09-② 升级生成 `order_id` 且功能开关立即变化 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| `POST … {professional, seats:10, action:upgrade}` | `order_id` 形如 `ord_01M5…`；`status=active` |
| 功能开关权威落点 `tenant.plan_code` | 升级前后：`free` → **`professional`（同一次调用内翻转）** |
| BL2 回读 | `plan_code=professional`、`seats_total=10`、`quota_units_total=3,000,000×10` |
| 套餐目录能力位 | `professional.features[org_structure].included=True`、`free` 为 `False` |
| 订单 | `meta.total=1`、`amount_cents=3900×10`、`status=pending_payment` |
| 用例 | `test_b09_2_upgrade_returns_order_and_flips_tenant_plan_immediately`（p0） |

### 3.3 B09-③ 降级 `effective_at` = 当前周期末、周期末前仍是原套餐 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| `POST … {free, downgrade}` | HTTP 200 + `code=0`（**非 15005 非 0 码**），`effective_at == 升级后 BL2 的 current_period_end` |
| 提示码 | `notice_code=15005`，`order_id=""`（降级不生成订单） |
| 回读 BL2 | `plan_code` 仍 `professional`、`seats_total` 不变、`pending_change={plan_code:free, effective_at:周期末}` |
| `tenant.plan_code` | 仍 `professional`（功能开关**不得提前**降级） |
| 用例 | `test_b09_3_downgrade_effective_at_is_period_end_plan_unchanged` |

### 3.4 B09-④ 席位用满 `15003` —— ❌ **不通过（2 个 P1）**

| 子路径 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- |
| BL4 `PATCH /billing/subscription/seats`（8 在册成员 → 申请 5） | `15003` + 文案「成员席位已用满，请先增加席位」+ `seats_total` 不变 | 400 / `15003` / 文案一致 / `seats_total=8` 未变 | ✅ |
| BL3 `POST /billing/subscription`（`action=upgrade`，目标席位 5，已用 8） | `15003`（服务自身文档规则 4：`seats < seats_used → 15003`） | **200 / `code=0`**，订阅落成 `seats_total=5` 且 `seats_used=8`（`seats_used > seats_total`） | ❌ **QA-P2-W3-01** |
| U4 `POST /identity/organizations/{org}/invitations`（免费版 3/3 已满） | `15003`（PRD `P2-BL-02` 验收 4、测试方案 P1 例⑦） | **200**，正常返回 `invitation_id`（无任何席位校验） | ❌ **QA-P2-W3-02** |

复现（BL3，最小化）：

```bash
# ① 专业版 8 席位 → ② 造 8 名在册成员 → ③ 支付掉升级单（排除 15004 干扰）
# ④ 再次 POST /billing/subscription {plan_code:professional, seats:5, action:upgrade}
POST /api/v1/billing/subscription  →  200 {"code":0, ...}
GET  /api/v1/billing/subscription  →  seats_total=5, seats_used=8   # 自相矛盾
```

根因定位（只定位，不改源码）：`app/business/subscription_service.py:508` `_resolve_seats()` 用**落库快照** `record.seats_used`（开通时为 0，仅在 `_apply_plan` 时刷新），而 `update_seats()`（同文件 :540）用**实时** `await self.seats_used(tenant_id)`。两条路径判据不同源。

钉住用例：`test_b09_4_seats_exhausted_returns_15003_on_bl4_path`（p0，通过）、`test_b09_4b_bl3_seat_guard_must_use_live_seats_used`（xfail strict）、`test_b09_4c_invite_member_blocked_when_seats_full`（xfail strict）。

### 3.5 B09-⑤ 回调幂等：连发 3 次只生效一次、不重复开票 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| 3 次 `POST /billing/payments/callback`（同一 payload，**不带 Authorization**） | 3×HTTP 200 + `data.code="SUCCESS"`（渠道语义要求重复也返回成功） |
| 订阅生效次数 | 第 1 次后与第 3 次后：`subscription_id` / `started_at` / `current_period_end` **三元组完全一致** |
| 开票 | BL9 列表 1 条、`store.counts()["invoices"]==1`、`invoice_status=issued` |
| 留痕 | `store.counts()["callbacks"]==1`（`uq_callback_external` 生效） |
| 其他 | 订单 `status=paid`；`payment_no_masked` 以 `****` 开头（流水号脱敏） |
| 用例 | `test_b09_5_callback_three_times_activates_once_and_invoices_once`（p0） |

### 3.6 B09-⑥ 待支付订单存在时再下单 `15004` 且不产生第二单 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| 首次 `POST /billing/orders` | `status=pending_payment`、`pay_url` 非空、`expires_at − now = 30min ±10s` |
| 二次同参下单 | HTTP 409 / `code=15004` / 文案含「请先完成支付」；响应无技术词 |
| 不产生第二单 | `store.counts()["orders"]==1`、BL7 `meta.total==1`、条目仍是首单 `order_id` |
| 用例 | `test_b09_6_pending_order_blocks_second_order_without_creating_one`（p0） |

### 3.7 B09-⑦ 30 分钟未支付自动 `closed` —— ✅ 通过（两条路径均有可复现证据）

| 路径 | 复现方式（不等待、不改系统时钟） | 结果 |
| --- | --- | --- |
| 懒过期 | 下单后把 `expires_at` 回填到 31 分钟前 → `GET /billing/orders/{id}`（读接口触发 `expire_orders`） | `status=closed`；随后**重新下单成功**（证明闸门释放而非绕过） |
| 补偿任务 | 再下单 → 回填 45 分钟前 → `OrderService.close_expired_orders()` | 返回关闭条数 ≥1（可观测）；BL8 回读 `closed` |
| 副作用校验 | 对已关闭订单发回调 | HTTP 409 / `15008`（不得把关闭单改成 paid） |
| 用例 | `test_b09_7_unpaid_order_closed_after_thirty_minutes`（p0） |

### 3.8 B10-① 企业 token 调任一 `/ops/**` 全 403 且不泄露客户数据 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| 8 端点 × 3 角色（owner / org_admin / member）= **24 次请求** | 全部 HTTP **403** / `code=20003` / `data=null` / 文案「当前账号没有该操作的权限」；**无一次 404**（不暴露端点存在性） |
| 数据零泄露 | 24 个响应体全文均**不含**：企业名（`星海绝密科技-W3-客户名`）、`tenant_id`、真实 `order_id`、`ord_` 前缀 |
| 伪造平台视角 | 企业令牌 + `X-Tenant-Id: __platform__` → 403；`ops_operator` 角色但仍为企业租户 → 403（租户层先于权限点层） |
| 判权 fail-closed | `require_ops_access` 先校验平台视角再校验权限点，两处均为**抛错**分支（无 fail-open 路径） |
| 用例 | `test_b10_1_tenant_token_forbidden_on_all_ops_endpoints_without_leak`（p0）、`test_b10_1b_forged_platform_header_cannot_open_ops` |

### 3.9 B10-② 核销 → `paid` + 订阅生效 + `audit_log` 含运营人与备注 —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| 前置 | 企业侧 `POST /billing/orders {offline_transfer}` → `pending_payment` + `bank_info` 非空 |
| `POST /ops/orders/{id}/confirm {paid_amount_cents, remark}` | `code=0`、`status=paid`、`subscription_status=active` |
| 订阅真正生效 | `get_active_subscription` → `plan_code=professional`、`status=active`；`tenant.plan_code=professional` |
| 订单可追溯 | `confirmed_by='usr_w3_ops'`、`confirm_remark` = 提交的备注原文 |
| 审计 | `audit_log` 动作 `ops.order_confirm`：`actor_id=usr_w3_ops`（运营人）、`tenant_id=<被操作企业>`、`change_summary.operator/remark/status/paid_amount_cents` 齐备 |
| 错误码 | 重复核销 → 409/`15008`；金额少 1 分 → 400/`15009` 且文案「订单金额与应付金额不一致，请核对后重试」 |
| 用例 | `test_b10_2_confirm_order_pays_activates_and_audits_operator_and_remark`（p0） |

### 3.10 B10-③ `metrics?range=30d` 与 BL5 企业维度求和**误差 0** —— ✅ 通过

| 证据 | 结果 |
| --- | --- |
| 样本 | 三企业真实用量 10 / 25 / 7（合计 42，**非零**，避免 `0==0` 的伪证） |
| BL5 企业维度（`group_by=capability`，管理员） | 逐家 `quota_units_used` == 10 / 25 / 7（与造数**逐一对齐**） |
| `metrics.quota_units_total` | **42 − 42 = 0**（严格等号，不是 ≤1%） |
| 同源旁证 | `top_tenants` 每家用量 == BL5 逐家值；`tenant_count=3`；`plan_distribution[free]=3` |
| 参数校验 | `range=90d` → 400 / `20001` |
| 用例 | `test_b10_3_metrics_30d_equals_bl5_tenant_sum_exactly`（p0） |

### 3.11 B10-④ 响应无对话内容/文档原文/成员邮箱明文/凭据 —— ✅ 通过

先造**真实样本**再用全文扫描（不是空库扫空气）：

| 样本 | 落点 |
| --- | --- |
| 对话标记 `W3-CONV-MARKER-…` | `usage_event.idem_key`（真实业务列） |
| 文档原文标记 / 知识库名标记 | `knowledge_base.name/description`、`document.file_name`（真实业务表） |
| 成员邮箱明文 `secret.person.w3@corp-example.com` | `document.uploader_name` + 身份侧邀请记录 |
| 凭据明文 `sk-w3-SECRET-…` | `credential_store` 的 `cipher_text` |

扫描 5 个响应（OPS1/2/4/6/7）：**0 命中**全部标记；邮箱正则 `[\w.+-]+@[\w-]+\.[\w.]{2,}` **0 命中**；字段名黑名单（`email/conversation_content/document_content/segment_content/cipher/secret/credential_ref/password`）**0 命中**；技术词（`traceback/asyncpg/dify/milvus` 等）**0 命中**。
正例仍在：企业名可见、`usage.quota_units_used=13`、`member_count=2`、`tenant_count≥1`（说明是「最小可见」而非「全遮」）。
用例：`test_b10_4_ops_responses_contain_no_content_email_or_credentials`（p0）。

> 结构性证据补强：`app/schemas/ops.py` 的响应模型**没有**邮箱/内容字段（不是「查出来再过滤」）；`ops_service.py` 全文不读 `credential_store` / 文档内容 / 对话表。

### 3.13 R1 增量：回调拒绝路径的幂等键污染（QA-P2-W3-05，P0 → 已在 `686b387` 关闭）

> 触发来源：团队曾以「`P0-07`」与「裁定 #6（验签失败改判 `20001`）」提出该口径（**这两条的编号与表述事后已由主理人撤回**，见 §5.2）。QA 按该口径补测时发现「幂等键零变化」当时**并不成立**，且后果比原表述更严重；本节保留的是 **QA 独立复现 → 修复 → 独立复验关闭** 的完整证据链。

**修复前（`837f19b` 实测，内存后端，逐步骤）**

| 步骤 | 请求 | 实测 |
| --- | --- | --- |
| ① | 同订单 + **流水号 `txn_legit_0001`** + **错误签名** | HTTP 400 / `20001`（这部分正确） |
| ② | 同一订单 + **同一流水号** + **正确签名**（渠道方真通知） | HTTP 200 / `SUCCESS`（渠道收到 200 不再重试）→ 但订单仍 `pending_payment`、**订阅未开通**、**0 张发票** |
| ③ | 幂等键索引快照 | `fake|txn_legit_0001` **已被脏回调占用** |

**业务后果**：用户已付款、订阅永不生效、渠道已收到成功应答不再重试，且只有一条 `payment_callback_duplicated` 的 info 日志——**静默资损且无法自愈**（需人工对账）。触发条件不需要攻击者：只要「先有一次验签失败（例如密钥配错/轮换窗口）→ 修好配置后渠道用同一 `transaction_id` 重试」即成立。

**根因**：`order_service.handle_callback` 先 `append_callback` 领键、后校验；拒绝路径把**未受信**的渠道流水号写进了 `(channel, external_txn_id)` 唯一键空间。

**修复后（`686b387`，QA 独立复验，非采信 commit message）**

| 校验项 | 结果 |
| --- | --- |
| 脏回调（错签名） | 400 / `20001`；订阅 none、发票 0；**真实 txn 未被占用** |
| 同流水号真通知 | 200 / `SUCCESS`；订单 `paid`、订阅生效、发票 1 张 |
| 真通知再重发 2 次 | 仍 `paid`、发票仍 1 张、订阅号与 `started_at` 不变；留痕 **2 条**（1 条 `rejected:` + 1 条成功）——**修复没有削弱真幂等** |
| 拒绝审计 | 存在 `billing.payment_callback_rejected`，含 `payload_sha256`/`reason`/`signature_ok` |
| 全量回归 | `--run-pg` 1070 passed / 0 failed / 0 skipped；p0 155 全绿；`validate_openapi` 仍 38/38 |

**用例处置**：`test_extra_4_rejected_callback_must_not_consume_idempotency_key` 原先以 `xfail(strict=True)` 钉住该 P0，`686b387` 上自动 XPASS → QA 按约定**摘除标记并升级为 `p0` 常绿回归**（并补强「修复不得削弱幂等」的两条断言）。**缺陷状态：已关闭。**

### 3.12 11 条汇总

| # | 验收点 | 判定 |
| --- | --- | --- |
| B09-① | 免费版 `active` / `seats_total=3` | ✅ |
| B09-② | 升级生成订单 + 功能开关立即变化 | ✅ |
| B09-③ | 降级 `effective_at`=周期末、周期末前仍原套餐 | ✅ |
| B09-④ | 席位用满 `15003` | ❌（BL4 通过；BL3 / 邀请路径失效） |
| B09-⑤ | 回调 3 次只生效一次、不重复开票 | ✅ |
| B09-⑥ | 待支付再下单 `15004` 且不产生第二单 | ✅ |
| B09-⑦ | 30 分钟未支付自动 `closed` | ✅ |
| B10-① | 企业 token 全 403 且不泄露 | ✅ |
| B10-② | 核销 paid + 订阅生效 + 审计含运营人/备注 | ✅ |
| B10-③ | 统计与 BL5 求和误差 0 | ✅ |
| B10-④ | 响应无内容/邮箱/凭据 | ✅ |
| — | **合计** | **10 / 11 通过** |

---

## 4. 附加必查 4 条

| # | 项 | 结果 |
| --- | --- | --- |
| 4.1 | 支付回调是**唯一**免鉴权白名单 | ✅ `AUTH_EXEMPT_PATHS = {/healthz, /readyz, /api/v1/billing/payments/callback}`；对 BL1-BL9 + 全部 `/ops/**`（共 17 条探测）无 token 一律 **401 / `20002`**；回调无 token 可达业务层（返回验签结论 `20001` 而非 401）。用例 `test_extra_1_*`；既有 `test_only_callback_is_auth_exempt` 对**全路由**做机械扫描，同样为绿。 |
| 4.2 | 支付密钥缺失：服务能启动、调用返业务错误（不得 500 堆栈） | ✅ `pay_channel=wechat_pay` 且 `mch_id/api_v3_key` 为空、关闭桩模式 → 应用正常启动、`GET /billing/plans` 200；`POST /billing/orders` → HTTP 500 + `{"code":30004,"message":"服务配置异常，请联系管理员"}`，响应全文无 `Traceback` / `File "` / 异常类名 / `OA_PAY` 配置键。用例 `test_extra_2_*`。 |
| 4.3 | `15001-15009` 文案为业务语言、前端 `business-errors.ts` 覆盖 | ✅ 后端 9 码文案均为业务语言（如 15003「成员席位已用满，请先增加席位」、15004「当前账户有未完成订单，请先完成支付」）；前端 9 码**逐字一致**；G-13 strict 实跑 `14/14 + 52/52` 通过（0 PENDING）。 |
| 4.4 | BL 回归不破坏 M4/BL5 同源（B07） | ✅ 升级到 10 席位后写 1 笔 321 字用量：`M4.used_units − BL5.quota_units_used = 0`；两者增量**同为 321**；`BL5.quota_units_total == M4.total_units == 3,000,000×10`。用例 `test_extra_3_*`。 |

---

## 5. 缺陷清单

| 编号 | 等级 | 标题 | 复现 | 归属 / 状态 |
| --- | --- | --- | --- | --- |
| **QA-P2-W3-05** | **P0** | 验签失败的回调占用 `(channel, external_txn_id)` 幂等键 → 同流水号的真实通知被静默吞掉（应答 `SUCCESS` 但订单永远 `pending_payment`、无订阅无发票，渠道不再重试），且无拒绝审计 | 见 §3.13 三步表：① 同流水号+错签名 → `20001`；② 同流水号+正确签名 → `SUCCESS` 但未开通；③ `fake|txn_legit_0001` 已被占 | Engineer · **✅ 已关闭（`686b387`，QA 独立复验）**；按主理人裁定，其**编号与表述撤回**、行为修复记入「回调拒绝路径加固」，**不计入新增缺陷统计**（见 §5.2） |
| **QA-P2-W3-01** | **P1** | BL3 席位判据用落库快照，绕过 `15003` 并落成 `seats_used > seats_total` | 见 §3.4 复现块；`subscription_service.py:508` 用 `record.seats_used`（恒为上次 `_apply_plan` 时的值，开通时为 0）而非 `await self.seats_used()` | Engineer · ⏳ 待修（B09 阻断项） |
| **QA-P2-W3-02** | **P1** | 邀请路径无席位校验：席位已满仍可邀请（PRD `P2-BL-02` 验收 4 / 测试方案 §7.1 P1 例⑦） | 免费版 3 席位被 3 名成员占满 → `POST /identity/organizations/org_x/invitations` → 200 + `invitation_id`（期望 `15003`）。`app/services/identity_service.py::invite_member` 无任何席位判定 | Engineer · ⏳ 待修（裁定：B09 收尾补，含「待接受邀请占位」口径） |
| **QA-P2-W3-03** | P2（测试夹具） | `tests/conftest.py::_reset_all()` **未重置组织存储** | `app/db/repositories/org_repo.py:1052` 提供 `reset_org_store()`，但 `_reset_all()` 未登记 → 同一 session 内上一用例的在册成员会顶高下一用例的 `seats_used`（W3 实测：用例单跑 XFAIL、整跑变 XPASS 的根因） | Engineer · ⏳ 随 W3-01/02 同 commit 修 |
| **QA-P2-W3-06** | P2（门禁） | **G-01 ruff lint 在 `686b387` 上为红** | `tests/unit/test_callback_hardening.py:21` `I001 Import block is un-sorted or un-formatted`（`from app.business.order_service import OrderService` 与 `... import REJECTED_TXN_PREFIX` 两行应合并）。`ruff check --fix` 可自动修 | Engineer · ⏳ 待修（合并进席位修复 commit 即可） |
| **QA-P2-W3-04** | 观察（不阻断本批） | W-01 补偿控制项未全达标 | B15 收尾前要求 `policy/grant/subscription/order/usage/db.session` 单模块行覆盖 ≥90%；实测 `order_service 87.79%`、`usage_service 88.48%`（其余达标：policy 99.18 / grant 98.29 / subscription 90.50 / db.session 95.90） | Engineer（B15 前补齐） |

### 5.1 判级理由（对齐 §7.1）

- **W3-05 定 P0**：不是「看到别人的数据」，但它让**已付款用户的订阅永久不生效**且**无告警、无自愈路径**（渠道收到 200 不再重试），属「数据/契约不可接受缺陷」；且触发不需要攻击者（密钥配错窗口 → 修好后渠道用同一 `transaction_id` 重试即成立）。同一根因的「脏载荷占位」与「真实通知丢失」计 **1 条**。
- W3-01：不是「看到别人的数据」⇒ 非 P0；但它让**计费核心不变量**（`seats_used ≤ seats_total`）可被单端点写坏，且 `15003` 判据在 BL3 路径不成立 ⇒ **P1**。同一根因下「BL3 绕过」与「订阅超卖」按最高级别计 **1 条**。
- W3-02：与 W3-01 **根因不同**（前者快照失真、后者完全缺失校验），且 §7.1 P1 例⑦ 明确点名「`15003` 未在邀请路径触发」⇒ 单独计 1 条 P1。
- W3-03 / W3-06 / W3-04：不阻断功能，属夹具健壮性、门禁与覆盖率补偿控制 ⇒ P2 / 观察。其中 W3-06 虽为 P2，但门禁口径是「任一项红即阻断合入」，需在下一 commit 内消掉。

### 5.2 编号对账（重要，避免按不存在的缺陷施工）

W3 期间收到「`P0-07` 回调解密失败仍走开通」「`P1-03` 缺唯一约束 `billing_callback(...)`」两条提问（称由 team-lead 批准）。**这两条不是本报告提出的**，本报告编号只有 `QA-P2-W3-01`~`W3-06`。

**来源已澄清（主理人 2026-10-22 更正）**：`P0-07` / `P1-03` 系工程师 `software-engineer-4` **自审时自行编号**提出，team-lead 误当作 QA 结论转达；主理人已**撤回这两条判定**（「不是缺陷」），并把工程师对应改动重新定性为**「回调拒绝路径加固」**、`0002` 改为**「升级路径自检」**，均不计入缺陷统计；同时**撤回「`20001` 口径变更」**（验签失败本就是 `20001`，无需改判，`15009` 仍是 OPS5 核销金额不符专用）。

核对结论（保留以备回溯）：

| 外部编号 | 与本报告的关系 | 事实 |
| --- | --- | --- |
| `P0-07` | **同一根因，但原始表述不准** | 「解密/验签失败仍走开通」在 `837f19b` 上**不可复现**（4 条拒绝路径实测订单均保持 `pending_payment`、0 发票、0 订阅，见 §3.13 前表）。真实缺陷是**幂等键被脏载荷占用**导致「真通知被吞」——即本报告 `QA-P2-W3-05`。工程师已以 `686b387` 修复，QA 复验通过、缺陷关闭。 |
| `P1-03` | **不成立（约束本已存在）** | `uq_callback_external (channel, external_txn_id)` 在基线 `0001_phase2_init` / `init-schema-phase2.sql` 第 9 节已存在，`append_callback` 依赖它做 `ON CONFLICT DO NOTHING`；QA 的 p0 用例实测「同 payload 3 次 → 订阅三元组不变、`invoices==1`、`callbacks==1`」⇒ 幂等成立。**不需要新增 `(tenant_id, external_txn_id)` 索引**（全局唯一更严格；改租户维度等于放宽约束，且需评估存量重名回填）。工程师最终以 `0002_callback_idempotency_selfcheck.sql`（原名 `0002_billing_callback_unique.sql`）做**只读对账**（存在且 UNIQUE 否则报错），与 QA 建议一致。 |

### 5.3 已核销的「看起来像缺陷、判定为正确」的 4 处（避免误报）

1. `15006` 未作为 BL3/BL4 的错误码返回 → **正确**：架构裁决要求「降级/减席导致额度超额」用 `HTTP 200 + code=0 + over_quota_warning=true`，实测一致；
2. BL10 对**已支付订单**的新流水号回调返回 `SUCCESS`（而非 `15008`）→ **正确**：渠道语义要求 200，且 `order.is_paid` 分支只 `mark_callback_processed`，**不再开通、不再开票**（实测订阅三元组不变）；
3. `GET /ops/metrics` 的 `quota_units_total` 语义是「区间内已用额度合计」而非「总额度配额」→ **正确**：与 BL5 求和比对的口径正是验收 ③ 的要求，字段命名沿用架构 §4.6 契约；
4. `686b387` 起引入的 `0002` 迁移（后更名为 `0002_callback_idempotency_selfcheck.sql`）**不写** `CREATE UNIQUE INDEX IF NOT EXISTS` → **正确**：应用角色不是表属主，已初始化库上执行 DDL 会 `InsufficientPrivilegeError`（工程师本地实测），只读对账 + 失败即响是当前权限模型下的正确取舍（迁移专用属主的议题已由工程师另报 team-lead）。

---

## 6. 覆盖率两项（含趋势与豁免口径）

| 项 | 阈值 | 冻结树 `837f19b` | 交付树 | R1 修复后 `686b387` | 判定 |
| --- | --- | --- | --- | --- | --- |
| 总覆盖率 | W-01 修正后：硬地板 **85%**，B15 收尾 **≥88%** | 88.34% | 88.65% | **88.77%** | ✅（较 W2 的 87.95% +0.82pp，且已过 B15 的 88% 线） |
| `app/business/*` + `app/db/*` | **≥85%**（W-02 未豁免，低于即红） | 86.39% | 86.84% | **87.05%** | ✅（较 W2 的 85.67% +1.38pp） |

- 本批新增模块覆盖：`billing_repo 88.48%`、`subscription_service 90.50%`、`order_service 87.79%`、`ops_service 95.61%`、`ops_repo 94.74%`、`payment_gateway 68.55%`（低覆盖集中在 `WechatPayGateway` 的真实下单/验签分支——本阶段不做真实调用，属设计内空转，非漏测）。
- W-01 的「B15 收尾 ≥90.5%」已由主理人裁定下调为 **≥88%** + 6 个关键模块 ≥90% 的补偿控制；当前 88.65% 已过线，补偿控制项见缺陷 **QA-P2-W3-04**。

---

## 7. 准出结论与路由

### 7.1 结论

> **FAIL（不准出）。** B09 的订单/支付主干与 B10 的运营后台**实现质量高**：幂等三道防线（部分唯一索引 + 回调流水号唯一 + 一单一票）、金额分级、流水号脱敏、超时双路径关闭、审计含运营人与备注、平台守卫 fail-closed、统计与 BL5 结构同源（误差严格 0）**全部经独立用例实测成立**；G-01~G-24 后端门禁、11 条验收点、4 条附加必查均无一因契约破坏 / 安全放开 / 门禁空跑而红。
> R1 期间新增的 **P0（`QA-P2-W3-05` 回调幂等键被脏载荷占用）已在 `686b387` 修复并经 QA 独立复验关闭**（§3.13）。
> 但仍有 **2 个 P1**：`15003` 判据在 BL3 与邀请两条路径失效，其中 BL3 会把订阅写成 `seats_total < seats_used` 的自相矛盾状态。按 `qa-phase2-test-plan.md` §7.1，P1 阻断该批次准出 ⇒ **B09 不放行**；B10 无 P0/P1，**B10 单批可放行**。

### 7.2 路由

| 对象 | 内容 |
| --- | --- |
| **→ Engineer** | ① **QA-P2-W3-01（P1）**：`_resolve_seats()` 的 `record.seats_used` 改为实时 `await self.seats_used(tenant_id)`（与 `update_seats()` 同源），并补「BL3 目标席位 < 已用 ⇒ `15003`」用例；② **QA-P2-W3-02（P1）**：邀请/新增成员路径接入席位校验（`ensure_seat_available` 单一出口，供 U4/ID10/BL3/BL4 复用；按主理人裁定含「在册成员数 + 待接受邀请数 ≥ seats_total」口径）；③ **QA-P2-W3-03（P2）**：`tests/conftest.py::_reset_all()` 增加 `reset_org_store()`；④ **QA-P2-W3-06（P2，门禁红）**：`tests/unit/test_callback_hardening.py:21` 的 `I001` 导入未排序，`ruff check --fix` 一行修掉（G-01 当前为红，必须先消）；⑤ **QA-P2-W3-04（观察）**：B15 前把 `order_service` / `usage_service` 补到 ≥90%。 |
| **→ 主理人** | ① QA-P2-W3-02 归属：已裁定 **B09 收尾补**，另有「待接受邀请占位」口径因**当前无邀请持久化（无表/无 repo，`invite_member` 为纯桩）**存在实现前置，已提请重裁（建议本轮只判在册成员数，占位随 B13 邀请表落地）；② `P0-07` / `P1-03`：**主理人已撤回**（来源为工程师自审编号，非 QA 结论），本报告 §3.13 保留的是 QA **独立复现并复验关闭**的同一行为，已按裁定记入「加固」而非新增缺陷。 |
| **→ QA（自动收口）** | 2 条 P1 已用 `xfail(strict=True)` 钉住（`test_b09_4b` / `test_b09_4c`），修好后自动 XPASS→hard error 强制摘标记。P0 那条已按此机制完成「XPASS→摘标记→转 `p0` 常绿回归」的完整闭环，可作为后续缺陷处置模板。 |
| **→ NoOne** | 不适用（存在阻断缺陷）。 |

### 7.3 第 2 轮（R2）复验范围（按主理人裁定，上限 2 轮）

工程师 fix commit 落地后，QA **只跑**：

1. `pytest tests/integration/test_qa_b09_b10_verification.py -q -rxX`：`test_b09_4b` / `test_b09_4c` 是否由 XFAIL 转 **XPASS**（转 XPASS 后摘除标记，复跑必须全绿）；
2. `pytest -q --run-pg` 全量 + 覆盖率两项（`0 failed / 0 skipped`、两率不降；R1 基线：1070 passed / 88.77% / 87.05%）；
3. `pytest -q -m p0 --run-pg`（R1 基线 **155 条全绿**，不得回退）；
4. `deploy/validate_openapi.py`（仍 `phase2=38/38`）；
5. **新增（裁定 #5）**：`pytest tests/integration/test_org_endpoints.py tests/integration/test_permission_matrix.py -q --run-pg`（U4 改动可能影响组织域；R1 基线 **49 passed**）；
6. **新增（R1 发现）**：`ruff check .` 必须回到 0 error（当前因 `W3-06` 为红）。

> 只测代码相关提交；纯测试提交（如 D14-1）只跑全量回归。

---

## 8. 交付物

| # | 文件 | 说明 |
| --- | --- | --- |
| 1 | `docs/qa-phase2-w3-report.md` | 本报告（含 R1 增补：§3.13 P0 取证与关闭、§5.2 编号对账） |
| 2 | `apps/adapter/tests/integration/test_qa_b09_b10_verification.py` | **18 条**独立验证用例（**10 条 p0**；2 条 `xfail(strict=True)` 钉住未修的 P1），同步回主仓同一路径供后续回归 |

### 8.1 门禁附录（R1 修复提交 `686b387` 实跑）

| 门禁 | 结果 |
| --- | --- |
| `pytest -q --run-pg --cov=app` | 1070 passed / **0 skipped** / 2 xfailed / **0 failed**；`TOTAL 88.77%` |
| `coverage report --include="app/business/*,app/db/*"` | `TOTAL 87.05%` |
| `pytest -q -m p0 --run-pg` | **155 passed / 0 failed / 0 skipped** |
| `pytest tests/integration/test_org_endpoints.py tests/integration/test_permission_matrix.py -q --run-pg` | **49 passed** |
| `deploy/validate_openapi.py` | `core=30`、`phase2=38/38`、`G1-G5 全部通过` |
| `ruff format --check .` | `157 files already formatted` |
| `mypy app` | `Success: no issues found in 95 source files` |
| `ruff check .` | ❌ **1 error**（`W3-06` `I001` @ `tests/unit/test_callback_hardening.py:21`，`--fix` 可自动修） |
| G-12 `test_db_session + test_tenant_isolation -m pg --run-pg` | **15 passed**（RLS fail-closed 未被加固改动破坏） |
| G-13 `scripts/check_error_catalog.py --strict` | `EXIT=0`，`错误码三处一致门禁通过`（放行域 = 无，0 PENDING） |
| G-23 `scripts/reconcile_usage.py --period 2026-10 --threshold 0.01` | `EXIT=0`、`[PASS] 偏差=0.0000%`；**注**：该账期无数据（成员求和=企业总量=事件总额=0），属「空数据恒等」，负例判据由 `test_b07_5c`（偏差超限 → exit 1 + `billing.reconcile_mismatch` 审计）覆盖 |

---

*报告人：严过关（QA 工程师）· 2026-10-22 · 主判据以冻结提交 `837f19b` 为准，R1 增补以 `686b387` 为准，R2 关闭以 `20b8c45` 为准（见 `qa-phase2-w3-r2-report.md`）；所有数字均为本机实跑，未引用他人口径。*
