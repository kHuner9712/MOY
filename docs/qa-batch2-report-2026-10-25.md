# 阶段四 · Batch 2 独立验证报告（契约防线与边界收口）

| 项目 | 内容 |
| --- | --- |
| 报告编号 | MOY-QA-2026-10-25-B2 |
| 被测分支 | `feat/batch2-contract-and-boundaries` |
| 代码基线 | **代码 = `8b03383`**、**HEAD = `3a51753`**（`01a9c36` 之后共 **10** 笔：T01–T08 八笔 + 验后修复 `8b03383` + 本报告 `3a51753`；未 push）。主验证轮跑在 `5b60d64`（T01–T08 共 8 笔）；P1-1 / P2-2 的补修提交 `8b03383` 落地后，QA 在**该提交上重做了定向复验**，结果与初检一致（详见 §5 P1-1「复验证据」） |
| 与派单的差异 | 派单写明 HEAD = `29d3649`；主验证轮开始时仓库实际 HEAD = **`5b60d64`**（`29d3649` **不是** HEAD 的祖先——T08 被改写重提交，两者差异仅 `docs/plugin-dev-guide.md` +6/−1 行）。其后工程师按本报告补 P1-1/P2-2，新增第 9 笔提交 `8b03383`；本报告入库为第 10 笔 `3a51753` → **当前 HEAD = `3a51753`**。T01–T07 哈希全程未变 |
| 验证方式 | 全部命令与探测由 QA 独立执行；**未复用工程师的任何一次运行结果**；3 个负样本由 QA 自行构造，验证后**已原样还原**（`git diff` 为空） |
| 上游输入 | `docs/phase4-batch2-plan-2026-10-25.md`、`docs/phase4-batch2-design-2026-10-25.md`、T01~T08 提交 |
| 生产构建 | 新构建一次（旧 `.next` 改名移出后重建），`BUILD_EXIT=0`，耗时 29s，非复用旧产物 |
| 运行环境 | 适配层桩 `:8000`（`env=development` / `deploy_mode=saas` / `business_store=memory` / `auth_mode=stub`）+ `next start :3200`（生产构建）+ 无头 Chrome CDP `:9222`（**只往 localStorage 写令牌，不注入 `Authorization`**） |
| 执行人 | 严过关（QA） |
| **最终结论** | **PASS WITH KNOWN ISSUES** |

---

## 0. 结论速览

| 判据 | 结果 |
| --- | --- |
| 5 条 P0 复现判据（契约门禁不红 / 支付缺字段仍成功 / D-6 无反馈 / D-7 任意值可提交 / 租户 20001 失效） | **0 条复现**（详见 §4） |
| 后端全量 | `2055 passed, 203 skipped`，数量与派单期望**逐字一致** |
| 前端 | `vitest 52 passed`、`e2e 3 passed`、`typecheck 0`、`lint 0` |
| 静态扫描 | **8/8 = EXIT 0**；**7/7 self-test = EXIT 1（= 自检通过）** |
| 浏览器（生产构建） | **14/14 PASS**，console error **0** |
| 负样本（契约 / 支付 / 租户） | **3/3 均按要求变红**，还原后均转绿且工作区干净 |
| 新增缺陷 | **P1 × 1（预存在，本批只修了一半）→ 已补修并复验通过**、P2 × 2（P2-2 已补修）、P3 × 7（详见 §5） |
| 预存在红 | `gen_delivery_manifest.py --check` = EXIT 1（base `8b8fdaa` 同样红，**非本批引入**；根因=两行尺寸失真，**建议 Batch 3 docs 批次统一刷新**） |
| 未跑项 | 无（`mypy` 本机未预装，临时 `pip install mypy==2.4.0` 后补齐运行，未改仓库依赖清单） |

**一句话（2026-10-25 09:36 更新）**：本批 8 项交付在**代码层面全部达标且负样本可重放**，5 条 P0 判据全部证伪。初检发现的 3 条阻塞项已有 2 条闭合：① **P1-1 已补修并通过 QA 定向复验**（请求方向映射后实发 `HTTP 200`，旧形状仍 `400/20001`；单测 53 passed）② **P2-2 已补**（`phase4-batch-log.md` 新增 Batch 2 段）。**仅剩 P2-1（交付清单预存在红，登记 Batch 3）与 P3 清单**，故结论由「PASS WITH KNOWN ISSUES（含 1 条 P1）」降级为 **PASS WITH KNOWN ISSUES（只剩 P2/P3）**——不给无条件 PASS 的唯一实质原因是 P2-1 使「交付清单可核对」这层证据不成立，而它早于本批。

---

## 1. A 组 · 静态与后端

| # | 检查项 | 命令 | 结果 | 证据 / 数字 |
| --- | --- | --- | --- | --- |
| A1 | 工作区与提交 | `git status --short`、`git log --oneline -12`、`git diff --stat 01a9c36..HEAD` | **PASS** | 仅 `?? apps/adapter/pytest_run.log`（**未入库**：`git ls-files` 0 命中；也**未被 `.gitignore` 覆盖**，见 D-3）；8 个提交；diff = 29 files, +2294/−48 |
| A2a | 后端全量 | `OA_BATCH=B21 python -m pytest tests -q` | **PASS** | `2055 passed, 203 skipped, 1 warning in 366.58s`，`EXIT=0`（与派单期望 2055/203 完全一致） |
| A2b | 安全域 | `pytest tests/security -q` | **PASS** | `56 passed, 2 skipped`，`EXIT=0`（2 条 skip 为 `--run-pg` 类） |
| A2c | 回调硬化定向 | `pytest tests/unit/test_callback_hardening.py tests/integration/test_billing_and_callback.py -q` | **PASS** | `38 passed`，`EXIT=0` |
| A3a | ruff lint | `python -m ruff check .` | **PASS** | `All checks passed!`，`EXIT=0` |
| A3b | ruff format | `python -m ruff format --check .` | **PASS** | `305 files already formatted`，`EXIT=0` |
| A3c | mypy | `python -m mypy app` | **PASS**（环境补齐后） | `Success: no issues found in 150 source files`，`EXIT=0`。**注意**：本机原无 mypy，QA 执行 `pip install mypy`（2.4.0）后运行，未改 `pyproject.toml` |
| A3d | OpenAPI 基线 | `python deploy/validate_openapi.py --strict` | **PASS** | `core=30 phase2=38 phase3=16 phase4=10 w1=2`（未实现全 0），`G1-G7 全部通过`，`EXIT=0` |
| A3e | 插件声明 | `python deploy/validate_plugins.py` | **PASS** | `registered 6, failed 0`；`G-26 通过：6 份声明…能力与原语均在白名单内`，`EXIT=0` |
| A3f | 配置注入名 | `python deploy/validate_config_names.py` | **PASS** | `G-35 注入名可消费` / `G-36 构建参数有声明（3 个 NEXT_PUBLIC_*）`，`EXIT=0` |
| A3g | 错误码目录 | `PYTHONPATH=. python scripts/check_error_catalog.py --strict` | **PASS** | `66 个码全部注册`、`12/12 阶段三对外码已被引用`、`内部码 17002 未被引用且 0 外泄`、`G-13 通过`，`EXIT=0` |
| A4 | 快照刷新是否「顺手混入无关漂移」 | `git show 121e4ba -- docs/api/openapi.snapshot.json`、`git show 5b60d64 -- …`、`pytest tests/unit/test_openapi_security.py::test_snapshot_is_in_sync_with_generated_schema` | **PASS（有保留）** | T08 的 diff 只有 `InputBriefItem.format` 一项，干净；**T04 的 diff 混入了 phase3 遗留**：新增 `InputBriefItem` schema + `MarketDetailData.inputs`。**核实结论：这是修预存在红，不是新漂移**——base `01a9c36` 的快照里 `InputBriefItem` 命中 0 次、`MarketDetailData.properties` 无 `inputs`，而 base 代码已有 `inputs`，故该用例在 base 上**必红**；T04 必须一起刷新，否则无法通过。用例现已 `1 passed`。保留意见：遗留修复与 T04 本体应分成两个提交（见 D-5） |
| A5 | 交付清单 | `python scripts/gen_delivery_manifest.py --check`；base `8b8fdaa` 临时 worktree 复跑 | **预存在红（非本批）** | 本机 `EXIT=1`：「清单内容与生成结果不一致…共 1 处不一致」；在 `git worktree add /tmp/qa-base-wt 8b8fdaa` 下复跑 **输出逐字相同、同样 1 处**，故**不是本批引入**。按派单要求**只报告不修**。**根因已核实**（两行尺寸失真）：清单记录 `deploy/.env.lite.example` = 10,217 B、`deploy/docker-compose.lite.yml` = 29,894 B，磁盘实际 **11,028 B / 30,327 B**（+811 / +433）；清单最后刷新在 `5a72604`，而这两个文件其后被 `f33b22b` / `ed199f4` 改过。三次提交 `f33b22b`/`ed199f4`/`5a72604` **均为 `8b8fdaa` 的祖先**（`git merge-base --is-ancestor` 逐一验证），即漂移发生在**本批之前**。**建议：Batch 3 docs 批次统一刷新清单，本批不提交修复** |

---

## 2. B 组 · 前端与契约

| # | 检查项 | 命令 | 结果 | 证据 / 数字 |
| --- | --- | --- | --- | --- |
| B1 | 类型检查 | `npm run typecheck:web` | **PASS** | `EXIT=0`，无输出 |
| B2 | Lint | `npm run lint:web` | **PASS** | `EXIT=0`（`--max-warnings=0`） |
| B3 | 单测 | `npm run test --workspace apps/web` | **PASS** | `52 passed (5 files) / 0 failed`：`api-client.test.ts 14`、`use-declaration-form.test.ts 20`、`pagination.test.ts 8`、`redirect.test.ts 7`、`session-events.test.ts 3` |
| B4 | e2e | `npm run test:e2e --workspace apps/web` | **PASS** | `3 passed (29.7s)`，含 `session-redirect.spec.ts`（`/chat → /login?next=%2Fchat`） |
| B5 | 8 道扫描 | `scan:{pages,workbench,banned,placeholders,contract,contract:gen,capability,oidc}` | **PASS 8/8** | 逐条 `EXIT=0`。关键输出：`scan:contract`「32 个接口 / 181 个字段 / 5 个枚举，0 差异」；`scan:contract:gen`「23 个接口 / 133 个字段，键集合三方一致（0 差异）」；`scan:capability`「越权或多要权限 0 处、多余能力 0 处」；`scan:oidc`「登录闭环 8/8」 |
| B6 | 7 道 self-test | `scan:self-test`、`placeholders-self-test`、`contract-self-test`、`contract:gen-self-test`、`workbench-self-test`、`capability-self-test`、`pages-self-test` | **PASS 7/7** | 全部 `EXIT=1`（= 自检通过）。负样本命中数：banned 12/12、placeholders 6/6、contract 5/5+枚举 3/3、contract:gen 3/3 + 白名单外快照接口 1/1、workbench 12/12、capability 5/5、pages 6/6 |
| B7 | CI 是否真用严格门禁 | `grep` `.github/workflows/ci-web.yml` / `ci-adapter.yml`；`git show e4111aa` | **PASS** | `ci-web.yml:236` = `npm run scan:capability`（严格，`--warn-only` 已删，注释同步改为「严格模式」）；`ci-adapter.yml:552` = `python deploy/export_plugin_contract.py --check`，`:553` = `node apps/web/scripts/check-plugin-contract.mjs --generated`，另加 `--generated --self-test` 期望退出码 1 的步骤；job 内已装 Node 22（脚本零依赖，不需 `npm ci`） |
| B8 | **契约负样本（独立构造）** | 改 `app/schemas/plugin.py:119` `MarketData.total` → `total_count` → `python deploy/export_plugin_contract.py` → `npm --workspace apps/web run scan:contract:gen` | **PASS（门禁会红）** | 门禁 **`EXIT=1`**，逐条差异 4 处：`PluginMarketData.total_count 缺键（types.ts/快照）`、`PluginMarketData.total 多键（types.ts/快照）`。还原 + 重生成后 **`EXIT=0`（0 差异）**，`git diff` **为空** |
| B9 | gen 契约形态核查 | 读 `apps/web/contracts/plugin-domain.gen.json` | **PASS** | **23 个 TS 接口** / `backend_schema` **23 条、去重后 22 个后端 schema**（`PluginUpgradeResult` 与 `PluginRollbackResult` 共用 `VersionData`）；`excluded` 只声明 `task_domain`(8) + `projection`(1: `OrgCapabilitySwitch`)；`rename` 只声明 A-7 一处 `CapabilityPolicyItem.{org_switch_enabled→enabled}`；`optionality_authority="snapshot"`。**未把 96 端点全量塞入** |
| B10 | **支付负样本（独立构造）** | 把 `app/schemas/billing.py` 的 `trade_state` 默认改回 `"SUCCESS"` → 跑 `test_callback_request_defaults_trade_state_unknown` + `test_callback_missing_trade_state_keeps_order_pending` | **PASS（用例会红）** | **2 failed**，`EXIT=1`。日志完整复现 fail-open 形态：缺 `trade_state` 的回调触发了 `order_paid` → `subscription_activated_by_order` → 发票 `inv_…` 开具。还原后 `38 passed`、`git diff` 为空。另核：`PAID_TRADE_STATES = frozenset({"SUCCESS"})` 白名单、比较前 `strip().upper()`；`TRADE_STATE_UNKNOWN=""`；4 处默认值 grep 全 0（`trade_state: str = TRADE_STATE_SUCCESS`=0、`payload.get("trade_state", TRADE_STATE_SUCCESS)`=0、`or TRADE_STATE_SUCCESS`=0、billing 的 `trade_state` 默认 `"SUCCESS"`=0） |
| B11 | **租户负样本（独立构造）** | 把 `app/core/security.py:223` 的 `raise ApiError(...)` 改 `pass` → 跑 `-k "saas_missing_tenant or private_converges"` | **PASS（用例会红）** | **1 failed, 1 passed**，`EXIT=1`：`test_saas_missing_tenant_header_and_claim_is_rejected` 变红（请求变 200 + 落 `tenant_id=default`），私有化收敛用例仍绿。还原后 `56 passed, 2 skipped`、`git diff` 为空 |
| B12 | 413/429 **双通道**（不只查 `request()`） | 读 `api-client.ts` 的 `request()` 与 `upload()` 实现；跑 `api-client.test.ts` 的 #5/#6/#14 | **PASS** | `request()` 在 **401 之后、`unwrap()` 之前**拦 `413/429`（`api-client.ts:398-400`，`BusinessError(20008/20006,'')`），`upload()` 内**另一处独立实现**同样拦（`:469` 附近，注释写明「上传是 413 的主战场，必须与 `request()` 同口径」）。单测 `#5 413→20008`、`#6 429→20006`、`#14 上传未设租户不带标识头（multipart 是独立实现）` 全绿 |

---

## 3. C 组 · 浏览器行为（生产构建）

构建：旧 `.next`（180MB）改名移出 → `npm run build:web` → `BUILD_EXIT=0`，29s，路由 23 条全部生成。适配层 `:8000` + `next start :3200` + 无头 Chrome `:9222`（CDP 连接，令牌只写 `localStorage['moy.session.v1']`，未拦截/注入任何请求头）。

| # | 场景 | 结果 | 证据 |
| --- | --- | --- | --- |
| C1 | 无会话直达 `/chat` | **PASS** | 最终 URL = `http://localhost:3200/login?next=%2Fchat`（`%2F` 已编码） |
| C2 | `/workbench/plg_weekly_report` 收件人 `not-an-email` → 提交 | **PASS** | `POST /api/v1/agent/tasks` = **0 次**；应用内出现 **「请填写有效的邮箱地址」**（`p.text-destructive`）+ 顶部 toast「还有必填项没填好，请检查标红的字段」；`aria-invalid` 计数 = **1**；**不是**浏览器原生气泡（原生 `input.validationMessage` 仍存在但不参与拦截 → `noValidate` 生效） |
| C3 | `/workbench/plg_dev_workbench` 目标仓库 | **PASS** | `repo=abc` → **0 次 POST** + 应用内 **「请填写有效的目标仓库」**；`repo=https://github.com/moy/web` → **1 次 POST** |
| C4 | `分支策略` 默认值与错误态 | **PASS** | 控件 `SELECT`，value = **「新建分支」**，`aria-invalid = null`（**不标红**） |
| C5 | `/workbench/plg_hr_admin` 部门插件同验 | **PASS（机制与设计不同，见 D-6）** | 输入层 `input[type=text][maxlength=32]`；粘贴 40 字 → 实际值 **32 字**；随后可正常提交（1 次 POST）。即「>32 字不会被提交」成立，但机制是**输入层截断**而非「校验拒绝 + 应用内提示」 |
| C6 | 无会话直达 `/chat`（同上，浏览器侧） | **PASS** | 与 C1 同源证据 |
| C7 | 控制台异常 | **PASS** | `console error = 0`、`Runtime.exceptionThrown = 0`（修复环境前曾有 24 条 CORS 报错，根因为 QA 环境缺 `OA_JWT_SECRET`，见 D-7） |
| C8 | `/team` 企业能力开关 | **不可达（记录）** | 页面显示「还没有加入任何企业」→ 能力开关 UI 不可达（演示数据无组织成员身份），故 A-7 的**请求方向**改由 API 层取证（见 D-1） |
| C9 | 分页越界回落 | **未在浏览器复现（明确声明）** | 演示数据每个列表都只有 1 页（6 个插件、2 笔订单），**无法构造 `total_pages` 3→1**；依据改为 ① 单测 `pagination.test.ts` 8 条（`clampPage(3,1)/(0,3)/(5,3)/(1,0)`、`total 30→10 → page 1`、未越界不弹回第 1 页等）② 代码审查：`clampPage`/`resolvePageState` 是唯一实现，`Pagination` 组件内 effect 用 `useRef` 固定 `onPageChange` 且 `currentPage === page` 即 return（无自激循环），**11 处调用点全部复用同一 `<Pagination>`**（`ops/orders`、`ops/tenants`、`plugins`、`plugins/[pluginId]`、`profile/memories`、`team`×2、`workflows`、`knowledge/[baseId]`、`knowledge/document-table`、`knowledge/grant-panel`）。**不以「没测」冒充通过** |

截图（`.workbuddy/screenshots/`）：`batch2-weekly-before-submit.png`、`batch2-weekly-invalid-email.png`、`batch2-workbench-invalid-repo.png`、`batch2-workbench-valid-repo.png`、`batch2-hr-department-truncated.png`、`batch2-team-page.png`。

配套取证（C 组环境自举顺带产生的端到端证据）：演示支付回调 `trade_state=SUCCESS` → 回调返回 `{"code":"SUCCESS"}` → 订阅由 `free` 升为 `professional`、`status=active`、`quota_units_total=30000000`，即 T04 的**正向路径**在真实 HTTP 链路上同样成立。

---

## 4. P0 判据逐条证伪

| P0 判据 | 结论 | 证据 |
| --- | --- | --- |
| 契约门禁不红 | **证伪（会红）** | 改 `MarketData.total` 后 `scan:contract:gen` = **EXIT 1**，4 处差异；还原后 0 |
| 支付缺字段仍成功 | **证伪（不再成功）** | 负样本下用例 2 条红；正常代码下 `order.status = pending_payment`、`invoices.items = []`、套餐未开通 |
| D-6 仍无反馈 | **证伪（有应用内反馈）** | 浏览器：0 次 POST + 「请填写有效的邮箱地址」+ `aria-invalid=1` + toast |
| D-7 任意值可提交 | **证伪（`abc` 被拦）** | 浏览器：`repo=abc` → 0 次 POST + 「请填写有效的目标仓库」；4 类合法形态在 `use-declaration-form.test.ts` 覆盖 |
| 租户 20001 失效 | **证伪（仍生效）** | 负样本 `raise→pass` 后用例红（200 + `default`）；正常代码 400 + `20001` + 「请求内容不完整，请检查后重试」且文案不含 `header/claim/tenant_id` |

---

## 5. 缺陷清单

### P1-1 ~~（预存在，本批只修了一半）~~ **已修复并通过定向复验**（2026-10-25 09:37，提交 `8b03383`）企业能力开关**请求方向**字段错配 → 保存恒失败

> **状态更新**：工程师已按本报告补修——`api-client.ts` 新增 `BackendCapabilityPolicyRequestItem` + `adaptCapabilityPolicyRequest()`，`setCapabilityPolicy` 把请求体 `org_switch_enabled → enabled` 做**对称映射**，并新增 1 条单测。QA 定向复验 4 项全通过（见本节末「复验证据」）。修复已提交为 `8b03383`。

- **原始现象**：前端 `PUT /api/v1/plugins/capability-policy` 的请求体是 `{policies:[{capability_key, org_switch_enabled}]}`，后端 `CapabilityPolicyRequestItem` 要求 `{capability_key, enabled}`（`enabled` 为必填）→ 恒返回 `400 / 20001「请求内容不完整，请检查后重试」`。
- **复现（QA 实测，修复前）**：
  - 前端形态（`org_switch_enabled`）+ 合法 `Idempotency-Key` → `HTTP 400 {"code":20001}`；
  - 正确形态（`enabled`）→ `HTTP 200 {"code":0}`，并回读全量 6 项。
  - 适配层日志 `invalid_fields = [("body","policies",0,"enabled"), …]`。
- **修复前代码位置**：`apps/web/src/lib/types.ts:1433-1435`（`CapabilityPolicyRequest` 声明 `org_switch_enabled`）、`apps/web/src/app/(main)/team/page.tsx:630-636`（按 `org_switch_enabled` 组装）、`apps/web/src/lib/api-client.ts:1418-1427`（**只映射响应**，请求体原样透传）。
- **影响**：`/team`「企业能力开关」的「保存」在任何有组织成员身份的企业里**必然报错**。因演示数据无组织成员身份，`/team` 直接显示「还没有加入任何企业」，该缺陷当前不可达，故不影响本轮演示，但一上真实企业即暴露。
- **归属判定**：**不是本批引入**——base `01a9c36` 的 `types.ts`/`team/page.tsx`/`api-client.ts` 与修复前一致（已验证），本批按裁决 §7-1 只做了「返回体 `enabled → org_switch_enabled`」。
- **门禁为何抓不到**：`CapabilityPolicyRequest`（写请求）**不在**插件域白名单，`plugin-domain.gen.json` 与 `phase3.pl.json` 里都 0 命中；现有 gen 门禁只覆盖 `CapabilityPolicyItem`（响应）。属 T02 契约防线的一处结构性盲区（**仍未闭合**，见「建议」②）。

**复验证据（2026-10-25 09:37，提交 `8b03383`）**

| 复验项 | 命令 | 结果 |
| --- | --- | --- |
| ① 单测 | `npm run test --workspace apps/web` | **53 passed**（原 52 +1）。新用例断言请求体 `toEqual({policies:[{capability_key:'org.repo.write', enabled:true}]})` 且 `not.toContain('org_switch_enabled')`，同时断言响应 `enabled → org_switch_enabled` |
| ② 适配层实发（新形状 = 前端映射后） | `PUT /api/v1/plugins/capability-policy`，body `{policies:[{capability_key, enabled}]}` | **HTTP 200, code=0**，回读 **6 项**策略 → 不再是 400/20001 |
| ③ 适配层实发（旧形状 = 修复前） | 同端点，body `{policies:[{capability_key, org_switch_enabled}]}` | **HTTP 400, code=20001**「请求内容不完整，请检查后重试」→ 证明后端一直是严的、旧形状就是打不正，映射即修复 |
| ④ 静态门禁 | `typecheck:web` / `lint:web` / `scan:banned` / `scan:pages` | 全 **EXIT 0**（`scan:banned` 117 文件 0 命中） |

> 期间 QA 曾发现该修复首版 `typecheck:web` = EXIT 2（`api-client.test.ts(312,44)` `TS2493`：`fetchMock` 为零参 `vi.fn`，`mock.calls[0][1]` 越界；vitest 走 esbuild 不型检故单测全绿）。**已由 `8b03383` 修掉**，本轮复验 `typecheck` 已为 0。
>
> **QA 在 `8b03383` 上额外补跑的回归（超出派单要求的 4 项）**：8 道扫描全 `EXIT 0`（`scan:contract` 32 接口/181 字段/5 枚举 0 差异；`scan:contract:gen` 23 接口/133 字段 0 差异；`scan:capability` 多余能力 0 处）；`playwright test` **3 passed**（32.3s）。即第 9 笔提交**没有**打坏任何既有门禁。

- **后续建议（仍未做）**：② 把 `CapabilityPolicyRequest`/`CapabilityPolicyRequestItem` 纳入 gen 白名单（后端已有同名 schema，实测存在），让这类「请求形状错配」以后**能被门禁抓住**而不是靠人看。

### P2-1（预存在红）`gen_delivery_manifest.py --check` = EXIT 1

- **证据**：本机与 base `8b8fdaa` worktree **输出逐字相同**（「共 1 处不一致」），非本批引入。
- **根因（已核实，不是推测）**：清单在 `5a72604` 最后一次刷新，此后两个轻量版交付物被改过，`AUDIT_NOTES` 的 `{size}` 占位符按磁盘现算 → 两行尺寸失真：

  | 文件 | 清单记录 | 磁盘实际 | 差值 | 改动提交 |
  | --- | --- | --- | --- | --- |
  | `deploy/.env.lite.example` | 10,217 B | **11,028 B** | +811 | `f33b22b` |
  | `deploy/docker-compose.lite.yml` | 29,894 B | **30,327 B** | +433 | `ed199f4` |

  `f33b22b` / `ed199f4` / `5a72604` 三者**均为 `8b8fdaa` 的祖先**（`git merge-base --is-ancestor` 逐条验证）→ 漂移早于本批。
- **处置**：按派单**只报告不修**。**建议 Batch 3 docs 批次统一刷新**（重跑 `gen_delivery_manifest.py` 生成清单，与本批解耦，避免把 8 行无关尺寸变更混进 Batch 2 的 diff）。
- **影响**：交付清单的「证据列」两行数字失真；不影响任何功能门禁。

### P2-2 计划 E7 的留痕未落

- 计划 §8-E7 要求把「W-03 演示支付口径」「W-04 能力声明删除」写入 `docs/phase4-batch-log.md`；设计 §2.3/§2.7 另要求登记「20008 文案写死 50MB」「微信 v2 `TRADE_SUCCESS` 未登记」两条偏差。
- **实测**：`docs/phase4-batch-log.md` 最后章节仍是 §33（2026-10-23 暂停点），**Batch 2 段完全不存在**（`grep 演示支付|Batch 2` = 0 命中）。
- 已落地的部分：`docs/plugin-dev-guide.md:199` 已订正为「需要按部门自动确定收件人的插件（人工填部门的场景不需要申请）」，`:256` 同步补了 `format` 说明；HR 插件对外能力清单已从 4 项降为 3 项（实测 `personal.tool.use / personal.file.read / org.external.publish`）。
- **影响**：口径（本版为演示支付、未接真实渠道）与两处已知偏差无入库记录，下一批接手者可能误判「支付已接入」。

### P3-1 `apps/adapter/pytest_run.log` 未跟踪且未被 `.gitignore` 覆盖

- `git check-ignore` = 未忽略（exit 1），`git ls-files` = 0 命中（**确认未入库**）。但它会持续出现在 `git status`，与计划 §8-E8「工作区仅允许该文件未跟踪」形成长期噪声。建议加进 `.gitignore`。

### P3-2 `department` 的「33 字拒绝」在浏览器不可达（机制与验收口径不同）

- 设计/计划要求「`department` 33 字或含换行 → 不提交」。T08 给 manifest 加了 `max_length: 32`，前端把它渲染成 `input[maxlength=32]` → 33+ 字在**输入层被静默截断为 32 字**，随后正常提交。
- 结果上「>32 字不会被提交」成立（已验证），但用户侧表现为「粘 40 字，悄悄丢 8 字」，不是「拒绝 + 应用内提示」；`validateFormat` 的 `department` 分支在 UI 上因此**不可达**（只在单测/程序化调用生效）。
- 判定：**不算缺陷**（有兜底、比放行严格），但验收表里「33 字 → 不提交」的机制描述**与实现不符**，报告据实修正。

### P3-3 `PluginInstalledStatus` 扩到 5 值后，渲染层对 `installed`/`uninstalled` 会误标「已停用」

- `plugin-card.tsx:107` 把「非 `enabled`」一律显示为「已停用」；`plugins/[pluginId]/page.tsx:255-288` 同理。已确认全仓**无 exhaustive `switch`**（全部等值比较，扩值不改变渲染分支），但若后端真返回 `installed`（PG 行解析兜底值 `plugin_repo.py:1388`），徽标文案会错。
- 判定：**预存在**（此前 TS 类型只是「声明得比后端窄」，运行时同样会错标）；本批让类型更诚实，未改渲染。建议后续把徽标改成三态映射。

### P3-4 「读取通讯录」企业开关仍对管理员可见

- `MarketData.org_capabilities` 仍返回 `org.directory.read`（「读取通讯录」），`/team` 的企业能力开关仍会列出它；但已无任何插件声明该能力（`manifests` grep = 0）。管理员可开关一个无人使用的能力。
- 判定：**可能有意保留**（能力枚举是平台级 6 项，Q2=A 只要求插件侧不声明）。建议在 batch-log 记一句，避免下批误判为漂移。

### P3-5 T04 提交粒度不纯（phase3 遗留快照漂移混入）

- 见 A4：属**必要修复**（否则 base 上快照同步用例必红），但与 T04 本体同提交，`git show` 时难以区分「本批改动」与「补齐历史」。

### P3-6 设计文档「9 处消费点」与实测 11 处不符

- 实测 `onPageChange` 调用点 **11 处 / 10 个文件**（设计漏算 `knowledge/[baseId]` 页与 `team` 的第二个表格）。因全部复用同一 `<Pagination>`，**行为不受影响**；仅文档数字需订正。

### P3-7 未捕获异常响应的 CORS 头缺失（预存在）

- 本次验证中先出现 24 条浏览器 CORS 报错，定位为适配层 `GET /agent/tasks/{id}` 抛 `ArtifactTokenError`（`OA_JWT_SECRET` 为空）→ 500。该 500 由 `ServerErrorMiddleware` 产生，位于 `CORSMiddleware` **之外**，故响应无 `Access-Control-Allow-Origin`，浏览器只报 CORS 错误而非「系统繁忙，请稍后再试」。
- 判定：**预存在**，且只在服务端 500 时发生。**同时登记一条环境事实**：本地/演示环境必须给非空 `OA_JWT_SECRET`，否则「任务详情/产物下载」链路 500（补齐该变量后 C 组 console error 归零）。建议写在部署说明或 batch-log。

### P3-8 团队简报的 HEAD 与仓库实际不一致

- 派单 HEAD = `29d3649`，实际 `5b60d64`，且 `29d3649` 已不在历史链上。内容差异仅 dev-guide +6/−1 行，**不影响结论**；但说明 T08 在派单后被改写重提交，若有人按旧哈希复核会 `git show` 失败。建议后续派单前先 `git rev-parse HEAD` 校准。

---

## 6. 预存在红 / 未跑项 / 已知限制

| 项 | 状态 | 说明 |
| --- | --- | --- |
| `gen_delivery_manifest.py --check` | **预存在红（EXIT 1）** | base `8b8fdaa` 同红；根因 = `deploy/.env.lite.example`(+811B) / `deploy/docker-compose.lite.yml`(+433B) 在清单刷新 `5a72604` 后被 `f33b22b`/`ed199f4` 改过。**建议 Batch 3 docs 批次统一刷新**，本批不提交修复 |
| `OA_BATCH` 环境变量 | **已按 CI 口径使用（QA 复核过误判风险）** | 本次全部后端运行均带 `OA_BATCH=B21`（CI 定义在 `ci-adapter.yml:88`）。**QA 已实测不带该变量的后果**：`test_plugin_contract_skeleton.py::test_d1_deferred_registry_matches_markers` 直接 `KeyError: 'OA_BATCH'` 红（`tests/expected_xfail.py:255`，源码注释写明「故意不设默认值，缺失即红」）→ **不是代码问题**，也不会被误判为本批缺陷 |
| `mypy` | **未预装，已补齐** | `pip install mypy==2.4.0` 后 `Success: 150 files`；未改仓库依赖声明 |
| `--run-pg` 类用例（4 skip） | **未跑** | 需真实 PostgreSQL（`OA_TEST_DATABASE_URL`），本机无可用实例；不影响本批结论（租户 20001 分支已有非 PG 用例覆盖，且负样本已验证） |
| 分页越界浏览器复现 | **未跑（数据不足）** | 以单测 8 条 + 代码审查（11 处共用同一组件）替代，已在 C9 显式声明 |
| 真实渠道支付（微信 v3） | **不在范围** | Q1=A 演示支付；`TRADE_SUCCESS`（v2 用词）不在白名单，设计已明确本批接受 |
| GitHub 只读仓库候选 | **不在范围** | Q3=A；`repo` 只做形态约束，不校验存在性 |

---

## 7. 环境清理

- 已停止：适配层 `:8000`、`next start :3200`、无头 Chrome `:9222`、e2e 用的 `next dev :3100`。
- 复核：`netstat` 显示 **8000 / 3200 / 9222 / 3100 均无 LISTENING**。
- 工作区：`git status --short` 仅 `?? apps/adapter/pytest_run.log`（与验证前一致）；`git diff` 为空；临时 worktree 已 `git worktree remove`；QA 期间改名移出的旧 `.next` 副本已删除。
- QA 辅助脚本（`git` 未跟踪、`.workbuddy/` 已被忽略）：`.workbuddy/qa-batch2-browser.cjs`（C 组 14 项探测）、`.workbuddy/qa-boot-env.py`（环境自举：演示支付升级 + 能力开关 + 装 3 个插件）、`.workbuddy/qa-pay-demo.py`、`.workbuddy/qa-dbg2.cjs`（字段/页面结构探测）。均为可复跑证据，未触碰产品代码。
- **时间戳说明**：以上「工作区干净」的描述对应本报告所有验证命令执行完毕时（`5b60d64`）。P1-1/P2-2 随后由 `8b03383` 闭合；该提交的定向复验结论见 §5 P1-1 与 §8。

---

## 8. 最终结论

**PASS WITH KNOWN ISSUES**

- 8 项交付（T01~T08）**代码层面全部达标**，5 条 P0 判据**全部证伪**，3 个负样本**全部可重放变红并已还原**，A/B/C 三组共 **34 项检查（A 13 / B 12 / C 9）** 无一条 FAIL，另 5 条 P0 判据逐条证伪。
- 仅剩 **P2-1**（`gen_delivery_manifest --check` 预存在红，交付清单两行尺寸失真）与 **P3 清单**；**P1-1/P2-2 已由 `8b03383` 闭合**。
- 建议 **Batch 3 docs 批次**统一刷新交付清单（单独提交重生成，避免与本批混在一起）；P3 清单按原记录保留。

### 版本口径（与派单对齐，2026-10-25 更新）

- **代码基线 = `8b03383`**（主验证轮 `5b60d64` + 补修 1 笔）：T08 曾 amend（把 `docs/plugin-dev-guide.md:255` 的 `format` 说明并入同一笔），旧 `29d3649` 已不在历史链上；T02–T07 哈希不变（`58f30e3`/`6951703`/`121e4ba`/`7064617`/`e3570a4`/`cf05b39`），T01 = `e4111aa`，第 9 笔为 `8b03383`（A-7 请求侧 + batch-log §34）。**构建与浏览器验证在 `5b60d64` 上执行**（该提交仅多一处文档改动，同源同构建产物），**P1-1/P2-2 的定向复验在 `8b03383` 上重做**。
- **E8「本批提交数 ≤ 8」的口径偏差（已裁决）**：`01a9c36..HEAD` 最终 **10 笔** = **8 笔功能提交（T01–T08）** + **1 笔验后修复 `8b03383`** + **1 笔本报告 `3a51753`**。**主理人已裁决：接受现状（方案 ①）**——功能提交仍为 8 笔，E8 按「功能提交 ≤ 8」成立；验后修复是被 QA 抓出的 P1 缺陷，拆到 Batch 3 会让已发现的能力开关保存失败多留一个批次；报告提交属验收留痕，不计入功能笔数。
- **契约换源 CI 落点**：`ci-adapter.yml` 的 `openapi-export` job（装 Node 22 → `export_plugin_contract.py --check` → `check-plugin-contract.mjs --generated` → `--generated --self-test` 期望退出码 1）；`ci-web.yml` 无 gen 步骤属**预期**，不是遗漏。

**报告结束。**
