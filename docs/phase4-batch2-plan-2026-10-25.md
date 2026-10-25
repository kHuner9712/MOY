# 麦子 V1.0 阶段四 · Batch 2 可执行计划（契约防线与边界收口）

| 项 | 内容 |
| --- | --- |
| 文档编号 | MOY-PLAN-2026-10-25-R2 |
| 编制人 | 许清楚（产品经理） |
| 编制时间 | 2026-10-25 |
| 适用阶段 | 阶段四（商用打磨与交付准备）· Batch 2 |
| 代码基线 | `main @ 8b8fdaa`（领先 `origin/main` 20 个提交，未 push；工作区仅 `apps/adapter/pytest_run.log` 未跟踪） |
| 上游输入 | `docs/improvement-plan-2026-10-25.md`（23 条）、`docs/improvement-design-2026-10-25.md`（Batch 1 设计）、`docs/qa-batch1-report-2026-10-25.md`、`docs/qa-batch1-r2-report-2026-10-25.md`、`docs/audit-2026-10-24-full-repo-audit-r2.md`、ADR-0007 / ADR-0008 |
| 决策口径 | 主理人 2026-10-25 拍板：**Q1=A（本版演示支付）/ Q2=A（删除 `org.directory.read`）/ Q3=A（W-08 只做格式约束）**，落点见 §9 |
| 下游消费 | 架构师（技术方案）、工程师（实施）、QA（验证） |
| 口径声明 | 本文只写计划，**不改任何代码**。所有事实均给出 `文件:行号`／提交／本次实测命令；未核实的标「待验证」 |

---

## 0. 一页速读

- **工作项 8 条**：P0 **3** / P1 **4** / P2 **1**，全部为团队内部可闭合（不依赖任何外部账号）。
- **一句话目标**：把「Batch 1 靠人眼看住的东西」变成**门禁与测试能自己抓住**的东西，并补齐 Batch 1 留下的错误处理与边界缺口。
- **为什么是这三条 P0**：Batch 1 修好了 5 个崩溃，但**没有一条门禁能重放它们**——契约快照仍是手抄文档（`apps/web/scripts/check-plugin-contract.mjs:5-8`、`apps/web/contracts/phase3.pl.json:2-4` 自承「来源仍为文档，换源见 F-10」），前端单测只覆盖 3 个纯函数文件（18 条），支付回调仍是 fail-open（`payment_gateway.py:97,293,387` 缺省 `trade_state=SUCCESS`）。**不修这三条，「阶段四已验证」这句话就仍然不成立。**
- **当前门禁真实状态（本次实测）**：`typecheck/lint/pages/workbench/banned/contract/oidc/placeholders/words` = 0；**`scan:capability` = EXIT 1**（`plg_hr_admin` 多要 `org.directory.read`），而它在 CI 里（`.github/workflows/ci-web.yml`）→ **CI 必红**。
- **Q1-Q3 已拍板（全选 A，无外部账号也能闭合）**：本版**演示支付**（不声明「支持在线支付」）；HR 插件**删除** `org.directory.read` 声明；`repo` 候选留 Batch 3、本批只做格式约束。落点与影响见 §9。
- **执行顺序已写死**：**W-04 第一个做**（一行 manifest 删除，先让 CI 转绿）→ W-01 → W-02；W-03 与 W-05 并行；W-06 → W-07 → W-08。

---

## 1. 现状盘点

### 1.1 已完成（有证据，不重复推翻）

| 项 | 证据（提交 / 报告 / 本次实测） | 本批是否再动 |
| --- | --- | --- |
| Batch 1 前端稳定性（F-01~F-05） | 合并 `b60489e`；`afe5693` 会话门上提、`5538a4a` 扁平分页、`70bb3d6` 工作台容错、`42563dc` 后端 `inputs`；QA：`docs/qa-batch1-r2-report-2026-10-25.md` §0（R9a~R10a `7/7 PASS`、`private` 4/4） | 否 |
| 租户自举修复（D-2 代码侧） | `20fee16`；QA R16：首个 `profile` **不带** `X-Tenant-Id` → 200，其后请求头 = `tnt_dev` | 只补**后端用例**（W-05） |
| 声明表单异步默认值与空候选降级（D-1） | `cab5c28`；QA R9a/R9c/R9b-neg（`POST` 从 0 → 1、必填仍生效） | 否（残留 D-6/D-7 见 §1.2） |
| 品牌化「麦子 / Moy」 | `8be2680`；ADR-0008 D-44；`grep -rn "办公 AI 平台" apps/web/src` = 0 | 否 |
| 前端测试接入 | `47edeab`；本次实测 `npx vitest run` = **18 passed / 3 files**；`e2e/brand.spec.ts` 2 条（Playwright 2/2） | 扩（W-02） |
| 8 个前端静态扫描门禁 | 本次实测：`scan:{pages,workbench,banned,contract,oidc,placeholders,words}` 全 0；`scan:capability` **=1** | 修（W-04） |
| 402 硬止损前端落地 | `business-errors.ts:77-79`（新 `15006` 文案）、`api-client.ts:330-340`（按 `code` 不按 status 分流）——`phase4-batch-log.md:139` 登记的待改项**已闭合** | 否 |
| 文档/决策入库 | ADR-0007、ADR-0008；本次 `git status` 干净 | 否 |

### 1.2 待办（本阶段真正要闭合的）

| ID | 问题 | 证据 | 级别 | 归属 |
| --- | --- | --- | --- | --- |
| F-10a | 契约快照**仍手写自架构文档**，G-31 比对的是快照而非真后端 | `check-plugin-contract.mjs:5-8`；`contracts/phase3.pl.json:2-4` | P0 | 本批 W-01 |
| F-10b | 前端行为回归网薄：18 条单测全在纯函数，`api-client`/分页/表单无网 | `src/lib/{redirect,session-events}.test.ts`、`hooks/use-declaration-form.test.ts`；vitest 18/18 | P0 | 本批 W-02 |
| B-04 | 支付回调 fail-open：`trade_state` 缺省 `SUCCESS` | `payment_gateway.py:97`（dataclass 默认）、`:293`、`:387`（解析默认） | P0 | 本批 W-03 |
| G-CAP | `scan:capability` 存量红 → CI 恒红 | 本次 `node scripts/check-capability-consistency.mjs` = EXIT 1；`plg_hr_admin.json:21-24` 声明 `org.directory.read`，6 个步骤/7 个输入均不消费 | P1 | 本批 W-04 |
| D-2 | 租户门禁缺 2 条后端用例（代码已修 `20fee16`） | `security.py:214-226` 的「私有化忽略头」「SaaS 头优先」「两头皆无 → 20001」三支；`tests/security/test_tenant_spoofing.py` 无 `20001` 断言 | P1 | 本批 W-05 |
| D-6 | 非法邮箱被浏览器原生校验拦在 React 之前 → 点提交**无任何应用内反馈** | `declaration-form.tsx:51`（`<form` 无 `noValidate`）+ `:200`（`type="email"`）；QA R2 §4 D-6 | P1（QA 原记 P3，本批上调见 §3） | 本批 W-06 |
| B-EDGE | 分页越界不回落；非信封响应丢真实原因 | 后端 `plugin_service.py:255`（不夹到 `total_pages`）；`api-client.ts:318-325`（解析失败一律 `AdapterUnavailableError`） | P1 | 本批 W-07 |
| D-7 | 空候选 select 降级为文本框后**放行任意字符串**，无格式约束 | `plg_dev_workbench.json:54-58`（`repo` 无 `options`）、`plg_hr_admin.json:51-55`（`department` 无 `options`）；`use-declaration-form.ts:184-190` 仅在 `options.length>0` 时校验 | P2 | 本批 W-08 |

### 1.3 阻塞（外部，不进本批）

| 缺什么 | 卡住谁 | 本阶段降级 | 拿到后谁接 |
| --- | --- | --- | --- |
| 5 款连接器真实账号/出站白名单 | A-03 | 保持 L1 桩；对外只写「成熟度 L1」（ADR-0008 D-46） | 工程师（Batch 3） |
| 微信支付商户号/APIv3 证书 | B-04 真接 | 本批只做 fail-open 收口 + 演示态口径（W-03） | 工程师（外部到位后另批） |
| 离线包目标平台/介质/规格 | A-04、A-05 实跑 | 不排期；`deploy/offline/images/*.tar` 仍缺（`install.sh:71,129,937` 引用） | 交付总监 |
| GitHub Actions 额度 | E-01 | 本地等价复跑，结论标注「未经远端验证」 | 运维 |
| 真实 IdP | V4（QA R2 §6） | 保持 `oidc` 本地回环验证 | 架构师 |
| GitHub 只读仓库列表 | D-7 的 `repo` 候选 | 本批只做格式约束（§5 W-08） | 工程师（Batch 3） |

---

## 2. 本阶段目标

### 2.1 一句话目标

> 让「Batch 1 修好的东西」被**契约门禁 + 行为测试**长期守住，并把 Batch 1 遗留的四处错误处理/边界缺口（原生校验拦截、分页越界、非信封错误、空候选放行）与一处资金侧 fail-open 收干净。

### 2.2 成功标准（每条可判定）

| # | 标准 | 判定方式 |
| --- | --- | --- |
| S1 | 契约漂移能被门禁抓住，且**故意改坏必红** | 把后端 `MarketData.total` 改名 → `npm --workspace apps/web run scan:contract` 退出 ≠ 0 |
| S2 | 前端行为回归网 ≥ 30 条，覆盖 `api-client`/表单/分页 | `npm run test --workspace apps/web` ≥ 30 passed，0 failed |
| S3 | 支付回调无「缺省即成功」路径 | `grep -c "trade_state: str = TRADE_STATE_SUCCESS" apps/adapter/app/business/payment_gateway.py` = 0；无签名回调 → 失败响应 |
| S4 | 8 个前端静态扫描门禁全 0（含 `scan:capability`） | 见 §10 命令块，逐条 EXIT 0 |
| S5 | 后端判权/边界有可复跑证据 | `pytest tests/security -q` 全绿且新增 ≥2 条租户用例；`mypy app` / `ruff` 0 |

### 2.3 本阶段不做什么

| 不做 | 理由 | 登记去处 |
| --- | --- | --- |
| 分页口径统一（后端全量改 `PageResult`） | 96 端点，风险 >> 收益 | V1.1 技术债（`improvement-plan` §9） |
| 连接器 L1 打通、支付真接 v3、离线包/升级回滚、性能/渗透报告、CI 远端实跑 | 全部外部阻塞（§1.3），排期不可控 | Batch 3 |
| 品牌、移动端导航、运营标题 | Batch 1 已闭合（`8be2680`/`6a0547d`/`522ceed`） | — |
| 组织架构目录读取（HR 插件侧） | Q2=A：插件不读目录，部门由用户填写 | 能力声明随 W-04 删除；如 V1.1 要真实候选，按连接器批次重开 |
| 契约快照「一次性全量对齐」（60+ 接口） | 会制造无法 review 的巨型 diff | 本批只覆盖插件域接口（`MarketData`/`MarketDetailData`/`ReviewListData` 等）+ 导出链路（W-01） |
| README 数字重算（C-09） | 与服务端数字绑定，须与 `gen_delivery_manifest.py` 同批改 | 后续 docs 批次 |

---

## 3. 优先级与理由

| ID | 级别 | 为什么现在做 | 不做的后果 |
| --- | --- | --- | --- |
| W-01 契约换源 | **P0** | 它是 Batch 1 五个崩溃的**根因防线**；`improvement-plan` §6.1 明确「快照换源不是可选优化，是 A 方案的前置条件」 | 下次任一后端字段调整，前端再次「照错的抄 → 比对通过 → 线上崩」，且无人能提前发现 |
| W-02 行为回归网 | **P0** | 本批要改的 D-6/分页/错误文案全部是运行时行为，静态门禁抓不到；ADR-0008 D-45 已放行依赖 | 本批每一条修复都只有一次人工走查做证据，回归即复发 |
| W-03 支付 fail-open | **P0** | 真实资金风险：缺省 `trade_state=SUCCESS` 让「无字段/未知状态」的回调被判成功；且它与外部账号**解耦**（只删默认值 + 加白名单就能做） | 任何能构造回调体的一方都可能把订单推成已支付；§6.5 表述无法自洽 |
| W-04 `scan:capability` 红 | **P1（执行序第一）** | CI 恒红 → 所有「门禁全绿」结论不成立；且它是**唯一**的红 | 团队习惯性忽略红门禁，真漂移被淹没 |
| W-05 租户门禁用例 | **P1** | `security.py:222-226` 的 20001 分支**零覆盖**；D-2 修复只靠前端不再预置头，缺后端锁定 | 将来有人把该分支删掉/放宽，无任何用例变红 |
| W-06 邮箱原生校验 | **P1** | 「点了没反应」是 Batch 1 唯一会让用户以为**按钮坏了**的缺陷；改动 <5 行 | 每周报工/招聘两个标杆插件的主路径上留一个静默失败点 |
| W-07 分页越界 + 非信封错误 | **P1** | 两者共同表现是「页面说没数据/说系统繁忙，而真实原因是别的」——直接违反「无技术暴露、可解释」 | 运营/插件市场在删改数据后停在空页；网关 413/502 一律显示「服务暂时不可用」 |
| W-08 空候选格式约束 | **P2（可裁）** | 有 workaround（能填能提交），但用户填的仓库名到执行期才失败，错误更难解释；因 Q3 要求"声明驱动"，成本从「5 行校验」升为「schema+类型+校验三处联动」 | 错误被推迟到任务运行期，客服无法判断是填错还是系统问题 |

> 定级说明：**P0/P1/P2 是价值分级，不是执行顺序**——W-04 是 P1，但它是 1 行删除且解掉唯一的 CI 红，故执行序排第一（§4.1）。
> 另一条定级说明：D-6 由 QA 的 P3 上调 P1，理由两点——① 它属于本批主题「错误处理」且是唯一「控件看起来失效」的形态；② 修复成本 <5 行 + 1 条用例，收益是消除一处静默失败。若主理人认为 P3 更合适，可直接降级而不影响其他工作项。

---

## 4. 模块与顺序

### 4.1 有序实施（第 0 步 + 4 个阶段，顺序已写死）

| 步骤 | 内容 | 可并行 | 说明 |
| --- | --- | --- | --- |
| **0 门禁转绿**（第一步，已写死） | **W-04** | 顺序上先于 W-01，不与其他项并行 | 一行 manifest 删除，让 CI 立刻转绿；用户决策已拍板（§9-Q2=A），**无需等待** |
| **A 契约防线** | W-01 → W-02 | W-03、W-05 可并行（不同栈、不同文件） | W-02 断言的「形状」必须等 W-01 定稿，故 W-02 紧跟 W-01 |
| **B 后端判权/边界** | W-03 ∥ W-05 | 二者互相独立 | W-03 支付链路、W-05 租户链路，互不影响；可插在 A 的任意并行槽 |
| **C 前端错误处理** | W-06 → W-07 → W-08 | 三者互不重叠（表单 / ui+api-client / 声明校验） | W-08 依赖 W-06 的「校验统一走 `validateField`」结论 |
| **D 回归与文档** | 全量门禁复跑 → QA 报告 → 交付清单证据列 | — | 见 §8 退出标准 |

### 4.2 依赖与并行度

| 工作项 | 前置 | 可并行于 | 串行原因 |
| --- | --- | --- | --- |
| W-01 | 无 | W-03、W-04 | — |
| W-02 | W-01 | W-03 | 测试要断言的是换源后的契约形状 |
| W-03 | 无 | 全部 | 后端口径独立 |
| W-04 | 无 | 全部 | — |
| W-05 | 无 | 全部 | — |
| W-06 | 无 | W-07、W-08 | — |
| W-07 | 无 | W-06、W-08 | — |
| W-08 | W-01、W-06 | W-07 | ① 新增 `format` 字段要先进契约与类型（W-01）；② 共用 `validateField` 文案与错误槽位（W-06） |

---

## 5. 逐项定义

### 5.1 P0

| ID | 问题/目标 | 证据 | 方案方向 | 验收标准（可执行） | 测试要求 | 风险/回滚 |
| --- | --- | --- | --- | --- | --- | --- |
| **W-01** | G-31 比对的是手抄快照，不是真后端；换源为 `app.openapi()` 派生 | `check-plugin-contract.mjs:5-8`；`phase3.pl.json:2-4`；`deploy/validate_openapi.py:148`（已有 `create_app().openapi()`）、`:808`（`--update-snapshot`） | ① 用既有导出链路生成插件域契约（`openapi.snapshot.json` → 派生 `contracts/plugin-domain.gen.json`）；② `check-plugin-contract.mjs` 改为**同时**断言「派生契约 == `types.ts`」与「派生契约 == 手写快照（过渡期双向锁定）」；③ 保留 `--self-test` 负样本 | ① `python deploy/validate_openapi.py --strict` EXIT 0；② 新增 `npm --workspace apps/web run scan:contract:gen` EXIT 0；③ **负样本**：把后端 `MarketData.total` 改名为 `total_count` → 上述命令 EXIT ≠ 0，改回后 0；④ `--self-test` 仍抓全部负样本 | 新增派生脚本 1 个 + 契约比对单测 ≥4 条（缺键/多键/可选性翻转/枚举漂移） | 生成脚本与手写快照并存一期，任一不一致即红；回滚=删派生步骤，手写快照仍在。**不刷新 `docs/api/openapi.snapshot.json`**（`sort_keys` 全量重写，diff 噪声大，见 `improvement-design` §9.3） |
| **W-02** | 18 条单测全在纯函数；`api-client`/分页/表单降级无网 | 本次 `npx vitest run` = 18 passed；`e2e/brand.spec.ts` 仅 2 条（登录页） | 扩 vitest：① `api-client` 分支（`20002` 触发 `emitUnauthorized`、HTTP 401、`code=0`、`15006` 走 `BusinessError`、非信封 → `AdapterUnavailableError`）；② 表单（`options=[]` 时 select→text、必填不放宽、邮箱走应用内文案）；③ e2e 增 1 条：登录→`/plugins`→详情→工作台表单可提交（分页越界夹具随 W-07 落地，不在此处） | ① `npm run test --workspace apps/web` ≥ 30 passed；② `npm run test:e2e --workspace apps/web` ≥ 3 passed；③ **负样本**：去掉 W-06 的 `noValidate` → 邮箱用例变红；W-07 的分页回落负样本由 W-07 自带 | 同上（用例即要求） | 不引入 React 渲染器（ADR-0008 §4：避免 React 18/19 混用）；e2e 用本机 Chrome，不进 CI。回滚=单文件 revert |
| **W-03** | 回调 `trade_state` 缺省即 `SUCCESS`（fail-open） | `payment_gateway.py:97`（dataclass 默认）、`:293`、`:387`（解析默认）、`:103`（`is_success` 判等） | ① 默认值改为**未知态**（空串/`UNKNOWN`），`is_success` 改为**白名单**（仅显式 `SUCCESS`）；② 未知/缺失状态 → 回调判失败并留取证行（复用 `order_service.py:494,568` 既有出口）；③ 产品口径（**Q1=A 已拍板**）：本版**演示支付**——对外不声明「支持在线支付」；下单接口对未开通路径显式返回业务错误，UI 保持「演示」语义（不新增词表外词汇）；真接 v3 待商户号/证书到位后另批 | ① `grep -c "trade_state: str = TRADE_STATE_SUCCESS" apps/adapter/app/business/payment_gateway.py` = 0；② `grep -c "payload.get(\"trade_state\", TRADE_STATE_SUCCESS)"` = 0；③ 无 `trade_state` 的回调 → 订单**不**变已支付（用例断言订单状态不变 + 1 条取证记录）；④ `pytest tests/unit/test_callback_hardening.py tests/integration/test_billing_and_callback.py -q` 全绿 | 新增 ≥3 条用例：缺字段、未知值、`SUCCESS` 正常 | 测试侧仅 5 处引用 `trade_state`，影响面小；回滚=单 commit revert（回到 fail-open，仅应急） |

### 5.2 P1 / P2

| ID | 问题/目标 | 证据 | 方案方向 | 验收标准（可执行） | 测试要求 | 风险/回滚 |
| --- | --- | --- | --- | --- | --- | --- |
| **W-04**（**第一个做**） | `scan:capability` 存量红，CI 恒红 | 本次 EXIT 1；`plg_hr_admin.json:12-29` 声明 4 能力，6 步骤/7 输入无一消费 `org.directory.read`；门禁规则 `check-capability-consistency.mjs:165-191` | **已定方案 A（不再有备选）**：从 `plg_hr_admin.json` 删除 `org.directory.read` 声明（插件不读目录，部门由用户填写）→ 授权清单同步少一项。**授权说明/详情文案是否要删词——已逐处检查**：详情页与授权抽屉的能力行由后端按 manifest 渲染（`components/plugin/capability-list.tsx:44`、`grant-drawer.tsx:126`；名称兜底表 `lib/capability-labels.ts:18` 是 6 项白名单兜底，**不按插件裁剪，不得改动**）→ 删声明后该行自动消失，**前端无需删词**；`plg_hr_admin.json` 自身也无「组织架构」字样（`grep 组织架构 plg_hr_admin.json` = 0）。**唯一需改文案的地方是文档**：`docs/plugin-dev-guide.md:199` 用「需要填写所属部门的插件」作为该能力的举例，删后自相矛盾，**需同步订正该行举例** | ① `npm --workspace apps/web run scan:capability` EXIT 0；② `python deploy/validate_plugins.py` EXIT 0；③ `node scripts/check-capability-consistency.mjs` 输出「多余能力 **0 处**」；④ 插件详情页能力清单不再出现「读取企业通讯录与组织架构」；⑤ 反向核查 `grep -rn "org.directory.read" apps/adapter/app/plugins/manifests/` = 0 命中 | 门禁 `--self-test` 仍全抓；`pytest tests/unit/test_plugin_assembly.py -q`（已核对：断言的是 checklist 4 项 / 6 步，**不校验能力条数**，删声明不影响）；`pytest tests/*plugin* -q` 全绿 | 对外能力清单变化需在交付说明留一行；回滚=manifest 单文件 revert |
| **W-05** | 租户门禁 2 条分支零覆盖 | `security.py:214-226`；`tests/security/test_tenant_spoofing.py` 无 `20001` 断言（本次 grep `20001` = 0 命中） | 补 2 条用例：① SaaS + 无头 + 令牌无 `tenant_id` 声明 → `20001`（且文案不含技术词）；② 私有化 + 任意头 + 令牌声明 A → 生效租户恒 `default`（现有用例已覆盖「不加成员查询」，缺「声明冲突时仍收敛」） | `pytest tests/security -q` 全绿 + 新增 2 条可单独点名运行；`pytest --run-pg -k tenant_spoofing -q` 全绿 | 新增即要求；**负样本**：临时把 `:222` 的 `raise` 改成 `pass` → 用例 ① 必红 | 纯测试新增，零产品风险；回滚=删除用例 |
| **W-06** | 非法邮箱点提交「没反应」 | `declaration-form.tsx:51`（`<form` 无 `noValidate`）、`:200`；QA R2 §4 D-6 | `<form noValidate>`，校验统一走 `use-declaration-form.ts:162-190` 的 `validateField`（邮箱规则已在 `:38-39`）；错误文案只经 `toBusinessMessage`/既有文案来源，不新增词表外词汇 | ① `/workbench/plg_weekly_report` 填 `not-an-email` 提交 → `POST /agent/tasks` = 0 次，页面出现**应用内**错误文案且 `aria-invalid=true`；② `grep -c "noValidate" apps/web/src/components/workbench/declaration-form.tsx` ≥ 1；③ `npm run scan:banned` / `scan:pages` 0 | vitest ≥3 条（空/非法/合法邮箱 → 错误文案与 `buildInputs` 行为）；e2e 可并入 W-02 冒烟 | 极低；回滚=1 行 revert |
| **W-07** | 分页越界停在空页；非信封响应丢真实原因 | `plugin_service.py:255`（`start` 不夹到 `total_pages`）；`api-client.ts:318-325`（解析失败一律 `AdapterUnavailableError`） | **范围已定稿：只做「读取侧夹紧 + 413/429 文案」，不做数据强制刷新。** ① `total`/`total_pages` 变化后把 `page` 夹到 `[1, max(total_pages,1)]`，**9 处消费点必须共用一处逻辑**（架构师抽成共用 hook/组件，禁止各页各写一次）；② `api-client` 对网关响应补状态分支：`413` →「文件超出大小限制」、`429` →「请求过于频繁，请稍后再试」，其余保持「服务暂时不可用」 | ① 构造 `total_pages` 从 3 变 1 的场景 → 页面自动回第 1 页且展示数据（不出现空表 + 页码 3）；② `413`/`429` 响应 → 文案为上述业务语言（用例断言）；③ 9 处分页消费点（7 个页面 + `document-table`/`grant-panel` 两个组件）`scan:pages` 0 | vitest ≥4 条（越界回落、413、429、非信封 502 兜底） | 分页回落是共用逻辑（9 处消费点），改动面大于看起来；**先只改「读取侧」不做数据强制刷新**，避免跳页抖动；回滚=单文件 revert |
| **W-08** | 空候选降级后放行任意字符串 | `plg_dev_workbench.json:54-58`、`plg_hr_admin.json:51-55`；`use-declaration-form.ts:184-190` | 降级文本框加**最小格式约束**（**Q3=A 已拍板**）：`repo` 接受 **4 类**——`https?://…`、`ssh://…`、`git@host:path`、`owner/repo`；`department` **1–32 字、不含换行**；错误文案沿用既有 hint 槽位；GitHub 只读账号候选留 Batch 3（见 §1.3）。**硬约束：走声明驱动，不在宿主里硬编码 `repo` / `department` 键名。** 现状核查：后端 `InputSpec`（`app/plugins/schema.py:233-254`）与前端 `PluginInputSpec`（`lib/types.ts:1169-1178`）**均无 `format` 字段**，故 W-08 需三处联动新增 ① 后端 `InputSpec.format`（`Literal["repo_ref","short_text"]`，`_StrictModel` 已拒未知键）② 前端类型加同名可选字段 ③ `validateField` 按 `spec.format` 分支校验；manifest 只在 `repo`/`department` 上加 `format`，宿主不认键名 | ① `/workbench/plg_dev_workbench` 的「目标仓库」分别填 4 类合法值（`https://git.example.com/moy/web`、`ssh://git@git.example.com/moy/web.git`、`git@git.example.com:moy/web.git`、`moy/web`）→ 均能提交（4 次 `POST`）；填 `abc` → `POST` = 0 次 + 应用内提示；② `department` 填 33 字或含换行 → 不提交；③ `scan:workbench`/`scan:banned` 0；④ `python deploy/validate_plugins.py` EXIT 0，且 `npm --workspace apps/web run scan:contract` EXIT 0（新增 `format` 字段必须同步进契约与前端类型）；⑤ `grep -n "format" apps/adapter/app/plugins/schema.py` 命中 `InputSpec` 定义 | vitest ≥5 条（4 类正例 + 1 类反例；`department` 边界 1 字/32 字/33 字）+ 后端 ≥2 条 schema 用例（合法 `format` 通过 / 未知 `format` 拒绝） | 过严风险：真实填法可能带 `#branch`/`?ref=` 后缀 → 校验取「前缀 + `owner/repo` 形态」不做整串严格匹配。**依赖风险：新增声明字段会改契约形状，必须在 W-01 之后做**，否则 G-31 与手写快照打架；回滚=manifest 与三处 `format` 改动同 revert |

---

## 6. 错误处理与边界清单

> 本批只填「有真实缺口」的行；已闭合的写「已验证，不改」，避免把清单当待办。

| 场景 | 现状（证据） | 本批处置 | 验证 |
| --- | --- | --- | --- |
| 空候选 `select` | 降级为文本框 + hint「暂无可选项，请直接填写」（`declaration-form.tsx:116-133`）；校验仅在 `options` 非空时生效（`use-declaration-form.ts:184-190`）→ **放行任意值** | W-08 加格式约束 | 填 `abc` 不提交 |
| 非法邮箱 | 浏览器原生校验拦在 React 前，**无应用内文案**（`declaration-form.tsx:51,200`） | W-06 `noValidate` + 统一 `validateField` | 0 次 POST + 应用内文案 |
| 接口 4xx/5xx 业务文案 | 信封内靠 `code`（含 402/20002）已验证；**非信封**（网关 413/429/502 HTML）一律「服务暂时不可用」 | W-07 补 413/429 分支 | 文案断言 |
| 会话失效 | `api-client.ts:334-340` 单点广播 + 会话门统一清会话跳登录（QA R1/R7/R8 PASS） | 验证不改；W-02 补 `401` 与 `20002` 两条单测 | 单测 + 不出现「登录状态已失效」残留 |
| 租户缺失/不匹配 | 私有化忽略头（`security.py:214`）；SaaS 头优先（`:217-219`）；两头皆无 → `20001`（`:222-226`，**零覆盖**） | W-05 补 2 条用例 | 用例可点名运行 |
| 分页越界 | 后端不夹（`plugin_service.py:255`），前端不回落 → 空页 | W-07 前端夹紧 | `total_pages` 收缩场景 |
| 契约漂移 | 比对对象是手抄快照（W-01 证据） | W-01 换源 + 双向锁定 | 改后端字段 → 门禁红 |
| 文件类输入 | 知识库上传已本地校验扩展名与 50MB，业务文案 + 三态齐（`knowledge/[baseId]/page.tsx:115-136`；`file-picker.tsx:131-161`） | 验证不改；W-07 顺带覆盖服务端 413 | 上传超限 → 业务文案 |

---

## 7. 外部阻塞与降级

| 外部项 | 缺什么 | 没有时本阶段怎么降级 | 拿到后谁接 |
| --- | --- | --- | --- |
| 连接器账号（5 款） | 测试账号/凭据/出站白名单 | 保持 L1 桩；W-04 若选方案 B 则**不做**，走方案 A | 工程师 / Batch 3 |
| 微信支付资质 | 商户号、APIv3 密钥、平台证书、公网回调 | W-03 只做「删 fail-open + 演示态口径」；**不声明已接入支付** | 工程师（另批） |
| 离线包目标平台 | CPU 架构/介质/机器规格 | 不排期；`upgrade.sh`/`rollback.sh`/`offline/images` 仍缺，不进本批 | 交付总监 |
| GitHub Actions 额度 | 额度恢复或自建 runner | 本地等价复跑，报告标注「未经远端验证」 | 运维 |
| 真实 IdP | OIDC 提供方 | 保持本地回环验证，V1.1 前补一次端到端 | 架构师 |
| GitHub 只读账号 | 仓库列表 | W-08 只做格式约束 + 文案说明「执行阶段会校验」 | 工程师 / Batch 3 |

---

## 8. 退出标准（Exit Criteria）

全部满足才算本批闭合：

| # | 条件 | 命令 / 观察 |
| --- | --- | --- |
| E1 | 前端静态扫描 8/8 全 0（含 `scan:capability`） | §10 `for` 循环，逐条 EXIT 0 |
| E2 | `typecheck` + `lint` 0 error 0 warning | `npm run typecheck:web`、`npm run lint:web` |
| E3 | 单测 ≥30、e2e ≥3，且**两条负样本可复现变红** | W-02 负样本：去掉 `noValidate` → 红；改后端 `total` 名 → 契约门禁红 |
| E4 | 后端门禁全绿 | `pytest tests/security -q`（含新增 2 条）、`pytest tests/{unit,integration} -k "plugin or callback or billing" -q`、`ruff check .`、`mypy app` |
| E5 | 契约导出链路可用 | `python deploy/validate_openapi.py --strict` EXIT 0 + `scan:contract:gen` EXIT 0 |
| E6 | 支付无 fail-open | §5 W-03 的两条 `grep` 均为 0 + 缺字段回调用例通过 |
| E7 | 文档留痕 | 新增 `docs/qa-batch2-report-2026-10-25.md`（QA）；W-03（演示支付口径）/W-04（能力声明删除）写入 `docs/phase4-batch-log.md`；订正 `docs/plugin-dev-guide.md:199` 的能力举例；HR 插件对外能力清单同步少一项 |
| E8 | 工作区干净 | `git status` 仅允许 `pytest_run.log` 未跟踪；本批提交数 ≤ 8（一项一提交） |

---

## 9. 已拍板决策

| # | 事项 | 决策 | 理由（一句话） | 落地位置 |
| --- | --- | --- | --- | --- |
| **Q1** | 支付口径 | **已选 A：本版演示支付** | 商户号/APIv3 证书不在手上，真接无法验证也算不上"已就绪"；而 fail-open 必须今天就删，与账号无关 | W-03（§5 W-03 ③：删 fail-open + 演示态口径；真接 v3 移出本批）；§2.3 不做清单；对外材料**不得**写「支持在线支付」 |
| **Q2** | HR 插件 `org.directory.read` | **已选 A：删除该能力声明** | 插件 6 步/7 输入确实不读目录，部门由用户填写；留着即"多要权限"，且是 CI 唯一的红 | W-04（§5 W-04：删 1 行 manifest + 授权说明/文案核查 + 订正 `plugin-dev-guide.md:199` 举例）；`scan:capability` 本批必须转绿；HR 授权清单 4 → 3 项 |
| **Q3** | `repo` 候选来源 | **已选 A：不列入本批** | GitHub 只读账号是外部依赖，等它会把本批从"可闭合"变成"不可闭合"；格式约束已能挡住绝大多数填错 | W-08（§5 W-08：`repo` 四类形态 + `department` 1–32 字，声明驱动不硬编码键名）；GitHub 候选进 Batch 3（§1.3） |

**结论：本批已无待用户确认事项。** 三项均满足「无外部账号也能闭合」，因此 §8 退出标准不含任何外部依赖。若后续要改口径（如改为真接支付），按其影响面走 ADR-0008 追加，不回头改本计划。

---

## 10. 复跑命令速查

```bash
# 0) W-04 验收（本批第一步；改前 = EXIT 1，改后必须 EXIT 0）
cd apps/web
npm run -s scan:capability; echo "EXIT=$?"          # 期望 0，且输出「多余能力 0 处」
node scripts/check-capability-consistency.mjs --self-test > /dev/null; echo "self-test EXIT=$?"  # 1 = 自检通过
cd ../.. && python deploy/validate_plugins.py; echo "validate_plugins EXIT=$?"                   # 期望 0
grep -rn "org.directory.read" apps/adapter/app/plugins/manifests/ || echo "manifests 0 命中（符合 Q2=A）"

# 1) 前端静态门禁（本批 E1）
cd apps/web
for s in scan:pages scan:workbench scan:banned scan:contract scan:oidc scan:placeholders scan:words scan:capability; do
  npm run -s $s > /tmp/$s.log 2>&1; echo "$s EXIT=$?"; done
npm run typecheck:web; npm run lint:web
npx vitest run; npx playwright test          # 期望：≥30 / ≥3

# 2) 契约换源（W-01）
cd ../.. && python deploy/validate_openapi.py --strict
npm --workspace apps/web run scan:contract:gen

# 3) 支付 fail-open（W-03）：两条都应输出 0
grep -c "trade_state: str = TRADE_STATE_SUCCESS" apps/adapter/app/business/payment_gateway.py
grep -c 'payload.get("trade_state", TRADE_STATE_SUCCESS)' apps/adapter/app/business/payment_gateway.py

# 4) 租户门禁（W-05）
cd apps/adapter && pytest tests/security/test_tenant_spoofing.py -q
```

**文档结束。** 本计划事实基线为 `main @ 8b8fdaa` 工作区实况（2026-10-25）。若代码已变更，先按 §10 逐条复跑再据实调整条目状态。
