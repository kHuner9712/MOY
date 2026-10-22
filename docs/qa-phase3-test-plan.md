# 第三阶段 QA 测试方案（插件体系与标杆场景插件）

> 文档性质：内部交付文档（供交付总监、架构师、研发、QA 消费）
> 上游输入：`docs/prd-phase3-increment.md` v1.0（25 条 P0 / 第 5 章 AC-02 / 附录 A 端点与错误码 / 附录 C 未验证项）、
> `docs/qa-phase2-test-plan.md`（体例与门禁口径）、`docs/qa-phase2-w3-r2-report.md`（W3 闭环基线 1119 passed / 89.99% / 89.10%）
> 下游消费：`docs/architecture-phase3.md`（端点编号、错误码分配、插件目录约定）、B16~B22 批次研发
> 作者：严过关（QA 工程师）· 日期：2026-10-23 · 版本：**v1.0-rc1（ADJ-1~10 已由主理人裁定；G-26~G-30 定稿，G-31 移出本阶段）** · 状态：待架构落稿后补路径与错误码数值

---

## 0. 判据基准与已裁决口径（先看这一节，否则用例会写错）

### 0.1 上游依据与效力顺序

`prd-phase3-increment.md`（需求与业务判据）→ `architecture-phase3.md`（端点/错误码/插件目录，**未落稿**）→ 本方案（用例与门禁）。
三者冲突时的效力顺序：**架构裁决 > PRD 原文 > 本方案**。架构已定稿（v3.2.0）：**插件域 14 端点（PL1-PL14）+ 任务域 2（WF1、WF2）= phase3 16**、三基线 **30 / 38 / 16 = 84**；本方案的用例一律用端点编号（PLn/WF1）与语义错误码（`E-n`）表述，架构落库后一次性替换为实际路径与数值码（见 §12）。

### 0.2 视为「已裁决」的口径（PRD 已给默认决策，不再逐条评审）

| # | 口径 | 来源 | 用例影响 |
| --- | --- | --- | --- |
| 1 | Q3-1 免费版不可用插件；市场页整页提示升级（非错误态） | PRD §10 | TC-P3-MK06-03 断言整页提示 + 升级入口，不断言错误码 |
| 2 | Q3-2 成员可自行安装；企业级能力（④⑤⑥）需管理员总开关 | PRD §10 + §6 | TC-P3-PL03-05 断言「需管理员开启」+ 复制申请说明 |
| 3 | Q3-3 **安装即启用** | PRD §10 | TC-P3-MK08-01 断言 `status=enabled`；不对 `installed` 中间态断言（见 ADJ-9） |
| 4 | Q3-4 评分仅展示不参与排序；允许匿名，匿名为「某位同事」 | PRD §10 | TC-P3-MK09-04 断言 `author_name=="某位同事"`，且排序结果与评分无关 |
| 5 | Q3-5 任务记录与产物 **180 天**过期 | PRD §10 | TC-P3-WF12-03 / TC-P3-WF14-04 用**注入时钟**跨 181 天断言，禁止 `sleep` |
| 6 | Q3-6 真实联调范围：研发=GitHub 沙箱真调；内容=公众号/邮件契约桩；人事=邮箱/企微契约桩 | PRD §10 | E2E 组按「三层判据」分级断言（§3.3） |
| 7 | Q3-7 单任务步骤 ≤8、单插件并发 ≤3 | PRD §10 | TC-P3-PL02-04/05、TC-P3-WF11-01 |
| 8 | B-1~B-8 **明确不做**（第三方插件、任意代码执行、直连底座、自定义 UI、自带依赖、角色 CRUD、邀请占位/15003 细分、小红书抖音真发布） | PRD §7 | **不写用例**；以「负面断言」证明未越界：无开发者入口 / 无自定义 UI（G-27）/ 无直连（G-28） |
| 9 | E-9 / E-10 **走状态不走错误码**（步骤失败与人工接管是任务内状态） | PRD 附录 A.3 硬要求 | TC-P3-WF13-01/03 断言 `steps[].failure.retryable` 与 `status=waiting_confirm`，**不断言非 0 `code`**，并额外断言「前端未弹全屏错误态」 |
| 10 | 阶段二 38 端点 + 核心 30 端点**零改动**，A1-A4 只做加性扩展 | PRD 附录 A.2 | G-29 + §6.2 加性断言逐条锁死 |

### 0.3 需主理人裁决的 10 条（**不可机械验证 / PRD 内部矛盾**，不要自行放宽）

> 这 10 条我没有直接放宽判据，也没有写成「表现正常即可」。每条都给「问题→建议判据→影响用例」，请裁决后我再落地。

| # | PRD 原文 | 问题 | 我的建议判据 | 影响用例 |
| --- | --- | --- | --- | --- |
| **ADJ-1**（**已裁定 2026-10-23**） | P0-P3-02 ③「并发 ≤3，**超出排队**并提示『前面还有 N 个任务在跑，稍后自动开始』」 vs 附录 A.3 E-12「**复用限流码 20006**（429）」 | **互斥**：返回 429 就不是「排队」，且「最终 4 个全部完成不丢」在 429 下不成立 | **按「排队」实现（主理人裁定）**：第 4 个返回 **HTTP 200 + `status=queued` + `queue_position=1`**；并发释放后**必须自动转 `running`**（不得让用户白等）；**E-12 的 `20006` 在插件域废弃**（v3.2.0：并发超限一律走排队状态，不再用限流码） | TC-P3-PL02-04、TC-P3-PL02-07 |
| **ADJ-2** | P0-P3-13 ①「前序步骤不重跑（**耗时显著小于全量**）」 | 「显著」无阈值，且耗时在 CI 上抖动（阶段二曾因此出现 flaky） | **改用结构性断言**：新任务 `retry_of`=原任务；前序步骤 `status=skipped` 且 `artifacts[].artifact_id` 与原任务**逐个相同**（复用而非重算）；耗时只记录不判定 | TC-P3-WF13-01 |
| **ADJ-3** | P0-P3-23 ②「正则 `\d+\s*(元|万|k|K)` **除日期与福利数字外** 0 命中」 | 「日期与福利数字」是开放集合，无法机械判定；留白会让用例既可能漏判也可能误判 | **固定语料 + 白名单**：语料为 3 份固定 JD 摘要 + 4 类公文要素；正则 `\d+(\.\d+)?\s*(元|万|k|K)` 对 **Offer 全文 + 4 类公文全篇**断言 0 命中；**不设「福利数字」例外**（福利一律用文字，如「五险一金」「带薪年假」）；日期用 `\d{4}-\d{2}-\d{2}` 单独断言存在且被上条正则排除（日期不含元/万/k/K，天然不冲突） | TC-P3-HR23-02、TC-P3-E2E-HR-03 |
| **ADJ-4** | P0-P3-18 ③「稿件中『据…』『数据显示』**类**事实表述 0 处」 | 「类」是开放集合 | 固定 8 条正则清单：`据[^，。]{2,20}(显示|表明|指出)`、`数据显示`、`研究表明`、`报告显示`、`据统计`、`专家指出`、`业内普遍`、`资料显示`；断言 **0 命中**，并在方案里声明这是**启发式守卫**（不证明「无编造」） | TC-P3-CNT18-03 |
| **ADJ-5** | P0-P3-19/21 「字数达标 ±20%」「理由 ≥30 字」 | 「字数」未定义（是否含标点/空白、中英如何计），同一实现两种算法可差 15% | **统一口径**：`len(re.sub(r"\s", "", text))`（去空白后的 Unicode 字符数，标点计入）；断言写入 §0.4 并全案复用 | TC-P3-CNT19-02、TC-P3-HR21-01 |
| **ADJ-6** | P0-P3-11 ①「执行中 **5 秒内**至少刷新一次」 | 墙钟依赖，CI 抖动必然 flaky | 桩模式下事件由桩即时产生；断言「首个 `step` 事件到达 < 5s」且**连跑 3 次稳定**；CI 抖动时判据升级为「< 15s 且 3 次均到达」而不是放宽为「有事件即可」 | TC-P3-WF11-01 |
| **ADJ-7** | P0-P3-17 ②「2 个链接产物**可打开**」（合并请求） | 离线环境无外网，无法真打开；若只断言「字段非空」就是假绿 | **离线**：断言 `pr_url` 与 `commit_sha` 一一关联（桩收到 1 次 `create_pr` 且 `head==commit_sha`）+ 提交对象在**本地 bare 仓库**中真实存在（`git cat-file -e`）；**真打开**留客户环境（V-3） | TC-P3-DEV17-02、TC-P3-E2E-DEV-03 |
| **ADJ-8** | P0-P3-05 ①「QA 按文档可复跑样例插件（**不额外口头指导**）」 | 「无需口头指导」是人的判断，无法机械判定 | 拆为 3 条机械判据（文档 6 节齐全 / 「允许调用的接口」清单与代码端点集合**相等** / 样例插件跑通 1 次任务）+ 1 条**人工留证**（由非作者工程师按文档复跑，记录卡点，不计入自动化 P0） | TC-P3-PL05-01~03 + 手工项 `#plugin-dev-doc` |
| **ADJ-9** | P0-P3-02 ① 状态机含 `installed` 态，但 Q3-3 是「安装即启用」 | `installed` 是瞬时态还是可观察态未定义 | **定义为 PL5 事务内瞬时态**：外部只可观测 `enabled`/`disabled`；用例**不对** `installed` 断言，改为断言「PL5 成功后立即 PL3 读到 `enabled`」 | TC-P3-PL02-01、TC-P3-MK08-01 |
| **ADJ-10**（**已裁定 2026-10-23**） | P0-P3-06 「市场内 V1.0 共 **6 个**内置插件（3 标杆 + 3 轻量工具型）」，但 P0-P3-24 写「**3 个插件**共用同一宿主」 | 轻量工具型是否也有工作台/宿主页未定义，用例无法断言数量与路由 | 题干接受：判据**与声明集合对齐**（不硬编码 6）——`market.items` 的 `plugin_id` 集合 == 扫描 manifests 得到的集合 S；只对「声明了 steps 的插件」断言 `/workbench/{id}` 可访问。**但不得以此放宽**：所有需人工抽验的断言必须按 **§4.11 的固定抽样规则**执行（抽哪个对象、什么顺序、几次内必须一致），**验收现场不得自由裁量** | TC-P3-MK06-01、TC-P3-UI24-04 + §4.11（8 条 / 35 条断言） |

### 0.4 实现口径锁死（不是分歧，但极易做错；用例一律按此断言）

| 项 | 判据 | 锁死方式 |
| --- | --- | --- |
| 产物与任务 | `artifact_id` 与 `task_id` **一对一**，不允许跨任务串 | TC-P3-WF12-01 连跑 3 次后按 `artifact_id` 反查 `task_id`，集合一致 |
| 重试语义 | retry **生成新任务**且 `retry_of` 指向原任务；原任务**状态不改** | TC-P3-WF13-01 前后两次读原任务，`status`/`updated_at` 不变 |
| 核查清单项数 | 研发 **4**、内容 **3**、人事 **4**，名称与 PRD §3.2.3 ②**逐字一致** | TC-P3-WF14-02 用 `==` 比对名称列表（不是 `in`） |
| 金额字段 | Offer 金额字段为**空字符串** `""`（不是 `null`、不是 `0`、不是「面议」、不是「待定」；允许「按公司标准执行」） | TC-P3-HR23-01 三字段 `== ""` 且页面含「请人事填写」；出现「面议/待定」判失败 |
| 金额双规则 | **R1 带单位**正则 + **R2 上下文窗口**（薪资类关键词 ±20 字符内 `\d{3,7}`）；**豁免项固化在用例数据**：日期 `YYYY-MM-DD`、工号 `EMP\d{4}`、百分比 `\d+%`、试用期「3 个月」 | TC-P3-HR23-02 / E2E-HR-03；豁免清单写成 fixture 常量，**验收时不得临场解释** |
| 企业能力开关 | 开关端点返回 `false` 时安装必须失败（`16006`），返回 `true` 时成功；两次表现必须不同 | TC-P3-PL03-07（防前端硬编码 `true`） |
| **报错顺序** | 三种前置同时不满足时按 **`15001`（套餐不支持）→ `16005`（成员未授权）→ `16006`（企业策略关闭）** 返回；三者 CTA 不同（升级套餐 / 重新授权 / 找管理员） | TC-P3-PL03-06（扩展断言：连续三次请求分别只破一个前置，码序必须按此顺序） |
| **金额扫描范围** | 只扫 **`output_key=offer`** 的产物；简历筛选结论里的「待定」是**合法值**（表示判断未定），不得误判 | TC-P3-HR23-02 / E2E-HR-03 / HV-4；扫描器入参必须显式带 `output_key=offer` |
| **步骤状态枚举（DDL 权威）** | 步骤状态只能是 **`pending` / `running` / `completed` / `failed` / `skipped`**；**`waiting_confirm` 是任务级状态，不是步骤级** | TC-P3-WF11-02 / WF13-03；断言步骤状态 `in {...}` 五元集合，不得出现 `succeeded`/`waiting_confirm` |
| **企业开关字段位置（两处都要断言）** | ①**PL1 顶层必须含 `org_capabilities`，且恰好 3 项**（只含 3 个 `org.*` 能力，每项 `{capability_key, org_switch_enabled}`）；②**PL4 仍逐插件返回 6 项**，每项含 `org_switch_enabled`。两处同时断言，正好证明「列表页用 PL1、授权抽屉用 PL4」 | TC-P3-PL03-07 |
| **PL14 授权写接口** | `PUT /plugins/{plugin_id}/grants` 必须带 `Idempotency-Key`；**只能改本人**；`required=true` 不可撤 | TC-P3-PL03-08/09/10 |
| **WF2 产物编辑** | 仅**可编辑类型**（稿件/JD/公文）允许；`type=link`/`type=file` 必须拒；保存 → `version+1` + 下载拿新版本；**编辑后必须重跑脱敏**（一票否决） | TC-P3-WF12-05~08 |
| **不装/停用/重复装 三个码值** | **`16003`＝该插件还没有添加**（未安装却调 PL6/PL7/PL8/PL9/PL10）；**`16004`＝该插件已停用**（`disabled` 时执行任务）；**PL5 重复安装 = `200` + `already_installed=true`**（两个码都不是）。依据 `errors.py:115/117`（`PLUGIN_NOT_INSTALLED` / `PLUGIN_DISABLED`）| TC-P3-PL02-01（16003）、TC-P3-PL02-05（16004）、TC-P3-MK08-06（200+flag） |
| 字数 | 去空白后的 Unicode 字符数（见 ADJ-5） | 全案统一 helper，禁止各用例自算 |
| 进度百分比 | `已完成步骤数 / 总步骤数`，6 步完成 3 步 = **50%**（含浮点误差 <1e-6） | TC-P3-WF11-03 |
| 失败与接管 | **不返回非 0 `code`**；用 `steps[].failure.retryable` 与 `status=waiting_confirm` | TC-P3-WF13-02/03 同时断言 `code==0` |
| 离线可跑 | 所有 P0 用例在**无外网**下可跑：GitHub→本地 bare 仓库 + `git` CLI；公众号/邮件/企微→契约桩 | 见 §8.1；CI 用 `--offline` 模式一次性验证「无外网也能绿」 |

---

## 1. 测试范围与不测范围

### 1.1 在范围内（必须由用例证明）

| 阶段目标 | 必须证明的内容 | 主判据 |
| --- | --- | --- |
| G3-1 可安装/可运行/可卸载 | 生命周期 6 动作（安装/启用/禁用/卸载/升级/恢复）全部可用，卸载后历史仍可查 | §4.2、§4.3 |
| G3-2 授权边界可机械判定 | 能力逐项可见可拒；拒必需→安装阻断；无授权调用→业务错误；撤销后 ≤5s 生效 | §3.1、§4.1 TC-P3-PL03-* |
| G3-3 三插件端到端（AC-02） | **离开界面也成立**的结果物：仓库提交+合并请求 / 稿件+排期表+回执 / 评分表+提纲+Offer+公文 | §4.8 E2E 组（12 条） |
| G3-4 工作流可视化（FLOW-04） | 步骤级进度/产物/失败原因/重试/接管/历史筛选/刷新不丢 | §4.3 |
| G3-5 文案与 UI 一致性 | 禁词 0、占位词 0、插件资产 0 自有样式/脚本、宿主 0 插件分支 | G-27、G-14/G-16/G-17（扫描范围扩到 manifest 文案）、§4.7 |

### 1.2 不在范围内（并有替代证明）

| 不做项 | 原因 | 替代证明 |
| --- | --- | --- |
| 第三方插件注册/提交/审核（B-1） | V1.0 不开放 | 断言「无开发者中心入口」+ 市场只返回官方声明集合（TC-P3-MK06-01） |
| 任意第三方代码执行/沙箱（B-2） | 不纳入 | G-28 断言插件只能调用白名单端点；无 shell/网络出口调用 |
| 插件直连底座/数据库（B-3） | 禁止 | G-28 + TC-P3-PL02-06（构造直连尝试 → 拒绝） |
| 插件自定义 UI（B-4） | 禁止 | G-27 静态扫描 + TC-P3-UI24-01 |
| 插件自带依赖安装（B-5） | 不纳入 | G-22 依赖门禁沿用 + TC-P3-PL01-04（manifest 含依赖段 → 拒绝加载） |
| 自定义角色 CRUD（B-6） | 不纳入 | 无用例；P2 台账登记 |
| 邀请占位 / `15003` 文案细分（B-7） | 不纳入 | 沿用阶段二口径；W3 已登记知限 |
| 小红书/抖音真发布（B-8） | 无授权 | 断言「待人工发布」+ 下载入口（TC-P3-CNT20-03） |
| 浏览器级 E2E 自动化（V-5） | 选型/额度 | 静态断言 + 4 条人工走查留证（`#p3-manual`） |
| 产物保留期满的**真实清理行为**（原 G-31） | 需注入时钟，实现成本高于收益（主理人裁定） | 本阶段只断言「保留期字段存在且默认 180 天」「过期文案常量存在」；真实清理留阶段四压测（V-8，见 §10） |

### 1.3 用例总量与分层分布

| 维度 | 数量 | 说明 |
| --- | --- | --- |
| 用例总数 | **138** | P0 **116**（104 条按 PRD 条目 + 12 条按 AC-02 结果物）+ 非 P0 22 |
| P0 映射 | 25 条 P0 → **每条 ≥2 条**（最少 2 条、最多 12 条） | §4.9 逐条核对表（合计 116，每条数已核对） |
| P0 分层：单元层（`apps/adapter/tests/unit/**`） | 30 | manifest 校验、能力映射、状态机、字数/金额正则、进度计算、错误映射 |
| P0 分层：集成层（`apps/adapter/tests/integration/**`） | 74 | 端点契约、授权、幂等、并发、历史筛选、E2E（内存 + bare 仓库 + 桩） |
| 其中需 `--run-pg` | **18**（⊂74） | 插件安装/状态/卸载落库、版本快照与回滚、评价唯一性、RLS 跨租户、并发计数、审计 |
| P0 分层：前端静态（`apps/web/scripts/**`） | 8 | 禁词/占位/三态/错误码正则/插件资产/宿主分支扫描（以脚本断言实现，仍计入用例） |
| P0 分层：手工抽验（`apps/web/docs/qa-manual-checklist-phase3.md`） | 4 | 授权抽屉交互、失败态动作组、刷新补齐、跨端一致（抽样规则见 §4.11） |
| 非 P0 22 条分布 | 单元 10 / 集成 10 / 手工 2 | P1 需求 10 + 跨域风险 6 + 前端手工 4 + 兼容性 2 |
| 覆盖率要求 | 总 **≥85%**、`business+db` **≥88%**、插件域新模块 **≥90%** | §7.3 |

---

## 2. 质量门禁（G-26 起；与架构对齐后定稿）

### 2.1 门禁总表（新增 6 道 + 沿用阶段二相关 20 道）

| 编号 | 门禁 | 命令（草案，路径随架构定） | 判据 | 生效批次 |
| --- | --- | --- | --- | --- |
| **G-26** | 插件声明 schema 校验 | `python apps/plugins/tools/validate_manifests.py --strict` | 退出码 0；3 份内置 manifest + 1 份样例全通过；非法声明**加载失败且市场不展示** | B16 |
| **G-27** | 插件界面仅用平台组件库 | `node apps/web/scripts/check-plugin-ui-assets.mjs` | 插件目录 0 个 `.css/.scss/.less/.jsx/.tsx`（UI 组件）、0 字体/图标包；宿主页 0 处 `pluginId` 布局分支 | B16（资产）/ B24（宿主） |
| **G-28** | 插件能力白名单 | `python apps/plugins/tools/check_capability_whitelist.py` | 插件代码只调用「已声明能力 → 允许端点」映射内的端点；白名单外/未声明能力 → 违规 | B17 |
| **G-29** | 端点三基线 | `python deploy/validate_openapi.py`（扩展 G6） | `core=30`、**`phase2=38` 不回改**、**`phase3=16`**（PL1-PL14 + WF1/WF2，合计 84）全实现；G4 加性 diff 0 violation | **B21** |
| **G-30** | 错误码三处一致（含阶段三新码）——**与阶段二 G-13 合并为同一脚本，不新建第二个** | `python apps/adapter/scripts/check_error_catalog.py --strict`（同一脚本扩段） | 新码不与既有段冲突、**不复用 `16001`**、`business-errors.ts` 覆盖、同码文案 ≤2 种、0 PENDING；阶段二 52 码仍全绿 | B17 |
| **G3-10** | docx 依赖与插件纯净（PM 追溯表；并入 G-22 + G-27 + 单测，不新建扫描器） | `python deploy/validate_deps.py` + `node apps/web/scripts/check-plugin-ui-assets.mjs` + `pytest tests/unit/test_docx_artifact.py` | ①依赖登记 **16** 项且含 `python-docx`；②插件目录 `import docx` **0 命中**；③产物 `.docx` 可解压出 `word/document.xml` | B21 |
| **G3-11** | `17002` 不外泄（PM 追溯表；并入 G-30 同一脚本扩段） | `python apps/adapter/scripts/check_error_catalog.py --strict --forbid-public 17002` | ①`17002` 仅允许出现在后端内部定义与单测；**A3/A4 响应、前端兜底表、任何 `message` 中 0 命中**；②**脚本内置内部码豁免名单 `{17002}`**——内部码**不要求前端覆盖**（理由：G3-11 禁止其外泄，若仍要求 `business-errors.ts` 覆盖则 G-13 永远红），且豁免必须**显式登记**（新增内部码也要登记，不得默认豁免）；③反例：从豁免名单删掉 `17002` 后 `--strict` 必须失败（证明豁免是白名单而非关闭检查） | B21 |
| ~~G-31~~ | ~~产物留存与过期~~ → **移出本阶段**（主理人裁定：需注入时钟，成本高于收益） | 本阶段只做**静态断言**：`pytest tests/unit/test_artifact_retention.py` | ①产物表/模型存在「保留期」字段且默认值 == 180（天）；②过期文案常量存在于代码（`产物已过期，可重新发起任务`）；③**真实清理留阶段四压测**，本阶段不断言清理行为 | B19 |
| 沿用 | G-01~G-03（ruff/format/mypy）、G-04（全量 0 failed/0 skip）、G-05（覆盖率）、G-07/08/09（并入 G-29）、G-12（RLS）、G-13（并入 G-30）、G-14~G-18（禁词/占位/三态）、G-19~G-21（tsc/lint/build）、G-22（依赖/镜像）、G-24（p0） | 同阶段二 | 同阶段二 | — |

### 2.2 四条核心门禁的判据草案 + 「为什么不会永远绿」

> 统一约定（沿用阶段二 G-15）：**正常模式退出码 0 = 无违规**；`--self-test` 模式**退出码 1 = 所有负样本都被判为违规**（即「脚本确实能发现错误」）。`--self-test` 返回 0 视为**漏检（红）**，返回 2 视为**脚本异常（红）**。每道门禁另需 1 个「正样本必须判绿」，防止脚本把所有输入都判红。

#### G-26 插件声明 schema 校验

- **正常模式**：扫描插件目录下全部 `manifest.json` → 校验 6 段（基本信息 / 能力 / 输入 / 输出 / 界面描述 / 步骤定义）与字段类型、枚举、版本号形态（`^\d+\.\d+\.\d+$`）、步骤序号连续、文案禁词；0 违规 → exit 0。
- **负样本自检（12 条，`--self-test` → exit 1）**：① 缺 `capabilities` ② 缺 `steps` ③ 缺 `ui` ④ 版本 `abc` ⑤ 步骤序号 `1,3,4` ⑥ 未知顶层字段 `script` ⑦ 能力 key 不在 6 项枚举 ⑧ 名称含禁词「RAG 助手」⑨ `plugin_id` 无 `plg_` 前缀 ⑩ `required` 非布尔 ⑪ 步骤标题含英文技术词（`LLM`/`workflow`）⑫ 缺图标。
- **正样本**：3 份内置 manifest + 1 份样例插件必须判绿（否则脚本过度严格）。
- **为什么不会永远绿**：负样本里 ④⑤⑥⑫ 是「结构合法但语义非法」，只有真正做了 schema + 语义两层校验才会全部命中；⑧ 证明文案门禁接入到了**声明期**而非只在渲染期。

#### G-27 插件界面仅用平台组件库

- **正常模式**：① 插件目录（`apps/plugins/**`）中 `.css/.scss/.less/.jsx/.tsx` 计数 **= 0**；② 字体/图标包（`.woff/.ttf/.svg` sprite）**= 0**；③ 宿主页（`apps/web/src/app/(main)/workbench/[pluginId]/**` 与共享宿主组件）匹配禁止模式 **= 0**：`pluginId ===`、`pluginId==`、`switch (pluginId)`、`case 'plg_`、`pluginId ? ... : ...` 三元布局、`layoutMap[pluginId]`、`components/plugins/`（按插件分目录的布局组件）。
- **负样本自检（4 条 → exit 1）**：注入 1 个 `.css`、1 个按插件分目录的 `DevLayout.tsx`、1 处 `if (pluginId === 'plg_dev_workbench')`、1 处 `layoutMap[pluginId]`。
- **正样本**：允许的用法必须不误报——`useParams()` 取 `pluginId`、`api.get(pluginId)`、埋点 `plugin_id` 字段。
- **为什么不会永远绿**：负样本里第 2、4 条只有实现了「宿主唯一 + 声明驱动」才可能不出现；仅扫 `.css` 的粗扫描会漏掉 `layoutMap` 分支。

#### G-28 插件能力白名单

- **正常模式**：由 manifest 的 `capabilities` 推导允许端点集合（能力→端点映射表由架构给定，6 项能力 → 允许调用的适配层端点），再静态解析插件目录的调用清单（`endpoints[]` 或代码内的 `api.` 调用常量）→ 差集必须为空；且**不得**出现直连地址（`http://`、`https://`、`localhost`、`:55433`、`dify`、`postgres`）。
- **负样本自检（5 条 → exit 1）**：① 未声明能力⑤却调用 T3（推代码）② 调用 `/api/v1/ops/**`（越域）③ 直连 `http://127.0.0.1:5001` ④ 调用白名单外的 `M2`（流式生成）⑤ manifest 声明能力④但调用 `R2`（读他人库）。
- **运行时对偶断言**（不是门禁，但同一根因）：未授权能力调用 → `E-4` 业务错误；企业未开总开关 → `E-5`（TC-P3-PL03-04/06）。
- **为什么不会永远绿**：静态扫描与运行时断言是**两个独立判据**；只做静态会被「动态拼接 URL」绕过，只做运行时会被「未走到的分支」放过。两者都写进 §3.1。

#### G-29 端点三基线（含 phase2 不回改）

- **正常模式**：`core=30`（逐路径集合相等）、`phase2=38`（逐路径集合相等）、**`phase3=16`（PL1-PL14 + WF1/WF2，逐路径集合相等）**；G4 加性 diff：0 处删字段/改类型/改字段名/收窄枚举/新增必填请求字段/删既有错误码响应。
- **负样本自检（5 条 → exit 1）**：① 从基线 JSON 删 1 个 phase3 端点（含新增 **WF2**）→ 应报「未实现」② 把 A4 的 `steps[].artifacts` 类型从 `array` 改 `object` → 应报加性违规 ③ 把 phase2 某端点路径改名 → 应报 phase2 集合不等 ④ 给 A1 请求加一个**必填**字段 → 应报加性违规。
- **为什么不会永远绿**：第 ②④ 条只有真正做了「加性 diff 语义比较」才会命中；单纯数端点个数会全绿。
- **额外：phase2 不回改的行为证据**（不只靠契约文件）：抽取阶段二 6 个代表性用例（BL2/BL5/BL10/OPS1/OPS5/KB9 各 1）在阶段三树上必须继续绿，且§6.3 的基线数字只增不减。

### 2.3 执行顺序

```
G-26 声明 → G-27 UI 资产 → G-28 能力白名单 → 后端单测
→ 集成（-m "not pg"） → 集成（-m pg --run-pg） → 覆盖率
→ G-29 端点三基线 → G-30 错误码 → G-22 依赖/镜像
→ 前端：tsc → lint → build → 禁词(源码/自检/产物) → 占位词 → 三态 → G-27 宿主分支
→ 各门禁 --self-test 全跑（负样本自检）
```

任一项红即阻断合入。`pg` 组要求「不带 `--run-pg` 时跳过、带时 **0 skip / 0 fail**」。

### 2.4 豁免流程（受控）

| 规则 | 内容 |
| --- | --- |
| 可申请豁免 | 覆盖率为「总量」项、前端手工走查项、依赖网络的可拉取性 |
| **不可豁免** | G-27（插件自定义 UI）、G-28（能力白名单）、G-29（phase2 不回改）、禁词/占位三面（AC-06 一票否决）、E2E 结果物（AC-02 一票否决） |
| 豁免单 | 沿用 `docs/qa-phase2-waivers.md` 体例，新增段落记录：编号 / 阈值 / 实测 / 理由 / **补偿控制** / 到期批次 / 签字 |
| 硬地板 | 总覆盖率 85%、`business+db` 88%、插件域 90%；低于即红，不允许再申请下调 |

### 2.5 分批 DoD：p0 标记随批次落地（沿用阶段二 §2.5 的强规则）

| # | 规则 |
| --- | --- |
| 1 | 每个批次（B16~B22）落地时，把该批涉及的 P0 映射到**具体测试函数**并加 `@pytest.mark.p0`；只加在**已实现端点**的真实断言上 |
| 2 | **禁止空标记**：不得用 `p0` + `pass` / `assert True` / `xfail` / 无条件 `skip` 凑数（视为伪造门禁，P0 级流程缺陷） |
| 3 | 批次准出前 QA 实跑 `pytest -q -m p0 --run-pg`，要求 `failed=0 且 skipped=0`，并在批次报告贴出 p0 用例数 |
| 4 | 缺陷钉住统一用 `xfail(strict=True)`；修复后**自动 XPASS → hard error**，强制摘标记（W3 已验证该机制有效，见 `qa-phase2-w3-r2-report.md` §1） |
| 5 | 阶段门之前 p0 总数 ≥60（本方案目标 **116**），且 25 条 P0 **每条 ≥2 条用例**（§4.9 核对） |

---

## 3. 风险驱动的测试策略（7 类）

> 每类回答三问：**为什么常规测试抓不到** / **怎么证明（可执行）** / **判据是什么（可断言）**。

### 3.1 授权与能力边界（最高危，G3-2）

- **为什么抓不到**：插件以「发起人身份」调用能力，越权的表现是「功能正常返回了不该返回的数据」，不是报错；且能力是 6 项枚举 × 3 层（本人/企业总开关/插件授权），笛卡尔积大。
- **怎么证明**：① 能力条数 1:1（PL4 vs manifest）；② 逐项拒绝：取消必需 → 安装失败（`E-4`）；③ 只授权部分 → 对应步骤「未完成：未授权…」+「去授权」；④ 撤销后 ≤5s 生效：撤销 → 立即调用 → `E-4`，再用注入时钟 TTL 边界验证 5s 内缓存失效；⑤ **横向越权**：A 成员的授权不能给 B 成员用（同企业不同成员各装一次，互不影响）；⑥ **纵向越权**：普通成员调用企业级能力且管理员未开总开关 → `E-5`（不是 `E-4`）。
- **判据**：`E-4`/`E-5` 两个码必须分开断言（出路不同：自己去授权 vs 找管理员）；`capability_key` 不得出现在 `message` 里（前端按 key 映射文案）。

### 3.2 插件 UI 一致性可机械判定（AC-04）

- **为什么抓不到**：插件 UI 越界通常表现为「多了一段样式」，人工走查看不出来，且会随迭代回归。
- **怎么证明**：G-27 三层扫描（资产 / 分目录布局组件 / 宿主 `pluginId` 分支）+ 1 条「宿主组件唯一」断言（构建产物中引用 `<WorkbenchHost>` 的页面数 = 插件数，但宿主组件文件数 = 1）。
- **判据**：0 命中；且**负样本自检必须 exit 1**（见 §2.2）。

### 3.3 结果物可核查（AC-02，本阶段一票否决）

- **为什么抓不到**：页面「看起来有链接」不等于仓库里有提交；三个插件里两个只能靠契约桩，最容易做成「假回执」。
- **怎么证明（三层判据，按环境降级但**不允许降级成字段存在性**）**：

| 层 | 研发工作台 | 内容运营 | 行政人事 |
| --- | --- | --- | --- |
| L1 真实对象（离线可跑） | **本地 bare 仓库**：`git cat-file -e <sha>`、`git show <sha>:<path>` 的 sha256 == 产物 preview 的 sha256 | 稿件文件落盘，`sha256(文件) == 产物 sha256` | 导出的 `.docx` 可被 `python-docx` 打开且段落数 >0 |
| L2 契约回执（离线可跑） | 桩收到 `create_pr(head=sha)`，返回 `pr_url`；断言 `pr_url` 含 sha | 公众号桩返回 `receipt_id`；未接入平台无回执且状态「待人工发布」 | 企微/邮件桩返回 `message_id`/`event_id` |
| L3 客户环境补验（V-2/V-3） | 真实 GitHub 私有仓：链接可打开、PR 指向同一 commit | 真实公众号发布回执 | 真实企微日程/邮件送达 |

- **判据**：L1 + L2 必须全绿；L3 登记为未验证项（§10）。**「刷新、换设备、次日再查」用「重新登录 + 重新 GET」等价模拟**（阶段二已用同法）。

### 3.4 版本与历史一致性（PRD §9 R2）

- **为什么抓不到**：升级后历史任务若按**当前**模板渲染，步骤标题会变，页面上看着「正常」，但 AC-02 复核时会发现与当时不一致。
- **怎么证明**：① 升级前跑一个任务并记录 `step_titles`；② 发新版本（改步骤标题）→ 升级；③ 打开**升级前**的任务 → 步骤标题与②记录的逐项相等（`==` 列表比较）；④ 回滚后再打开该任务 → 仍相等。
- **判据**：`step_titles` 逐项相等；版本快照只保留 1 个（第二次回滚 → `E-7`/20005 语义）。

### 3.5 工作流状态机与重试幂等（FLOW-04）

- **为什么抓不到**：失败与接管被实现成「错误码」时，前端会整屏报错（PRD 明确禁止），但接口测试只看到 `code != 0` 还以为「符合预期」。
- **怎么证明**：① 注入「工具未连接」→ `code==0`、`steps[].failure.retryable==true`、3 个动作齐全；② retry → 新任务 + `retry_of` + 前序步骤 `skipped` + 产物 `artifact_id` 复用；③ 未失败任务 retry → `E-11`；④ 接管态 15 分钟不自动继续（注入时钟推进 15min，状态仍 `waiting_confirm`）；⑤ 状态流转只前进（记录 10 次轮询的 `status_index` 序列非递减）。
- **判据**：状态与产物都可断言；**不出现** `Traceback`、`\b[1-3]\d{4}\b`（复制诊断说明的文本）。

### 3.6 文案与术语红线（AC-06）

- **为什么抓不到**：插件声明文案是**新的一层**文案来源，阶段二的扫描只覆盖 `apps/web/src` 与后端 `message`，不覆盖 `manifest.json`。
- **怎么证明**：G-14/G-16/G-17 的扫描范围扩到「插件目录 `manifest.json` + 产物预览文本 + 步骤标题」；术语表 19-30（plugin/manifest/runtime/scope/workflow/commit/PR/rating/cron/artifact 等）全部纳入黑名单。
- **判据**：0 命中；且**声明期**命中即「加载失败」（G-26 负样本 ⑧）。

### 3.7 超时 / 取消 / 断线重连

- **为什么抓不到**：超时与取消最容易「把已完成步骤的产物一起丢掉」，而这正是用户最在意的（PRD P0-P3-02 ③）。
- **怎么证明**：① 超时压到 5s 跑长任务 → 状态「未完成（超时）」且**已完成步骤 `artifacts` 仍可取**；② 取消（A5）→ 任务变为已取消，产物保留；③ 事件流中断 30s 后重连（A3+A4 全量补齐）→ 步骤数与服务端一致、无重复无缺失、以服务端为准。
- **判据**：产物保留（而不是「任务失败即全部清空」）；重连后 `len(steps)` 与 A4 相等且 `step_id` 集合相等。

---

## 4. 用例矩阵

### 4.0 阅读约定

| 项 | 约定 |
| --- | --- |
| 编号 | `TC-P3-<模块><需求号>-<序号>`；模块为 `PL/MK/WF/DEV/CNT/HR/UI/E2E`；跨域风险用例 `TC-P3-RISK-<序号>` |
| 「期望」列 | 一律可断言（状态码 / `code` 语义 / 字段值 / 集合相等 / 计数 / 字节数 / 时间注入后的状态）；**不出现**「正常」「可用」「体验良好」 |
| 「落点」列 | 文件 + 函数名（架构落稿后补全）；`#anchor` 为手工走查单章节 |
| 标记 | `p0`（阶段门）、`pg`（需 PostgreSQL）、`slow`（>10s）、`e2e`（结果物组） |
| 错误码 | 架构落稿后统一映射；本表用 `E-1`~`E-16`（PRD 附录 A.3 语义编号）占位，**不写具体数值** |
| P1/P2 | 单独标注，不计入 P0 统计 |

### 4.1 插件标准与运行时（PL，13 条，映射 P0-P3-01~05）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-PL01-01 | 01 | 3 份非法声明就位 | 加载 → `GET market` | 3 份均不在 `items`；后台失败原因记录 **3 条**，每条含 `plugin_id` 与原因码 | `test_manifest_validation.py::test_illegal_manifests_rejected_with_reasons` |
| TC-P3-PL01-02 | 01 | 合法声明 | `GET market/{id}` | `step_titles[]` 与 manifest `steps[].title` **逐项相等**（`==`） | 同上 `::test_step_titles_match_manifest` |
| TC-P3-PL01-03 | 01 | 同名声明重复注册 | 重复注册 | 第二次失败；原条目字段**逐字段不变**（快照 diff 为空） | 同上 `::test_duplicate_plugin_id_keeps_original` |
| TC-P3-PL01-04 | 01 | 声明含自带依赖段 | 加载 | 拒绝加载 + 原因「不支持自带依赖」（B-5） | 同上 `::test_manifest_with_dependencies_rejected` |
| TC-P3-PL02-01 | 02 | 未安装 | 直接 `PATCH state` | `E-2`；`GET installed` 不含该项（不得隐式创建） | `test_plugin_lifecycle.py::test_state_before_install_rejected` |
| TC-P3-PL02-02 | 02 | 已启用 | 执行任务 → `GET tasks?plugin_id=X` | 能筛出且 `plugin_id` 非空、与入参相等 | 同上 `::test_task_carries_plugin_id` |
| TC-P3-PL02-03 | 02 | 长任务 + 超时 5s | 执行 → 等超时 | 状态「未完成（超时）」；已完成步骤 `artifacts.length ≥1` **仍可取** | 同上 `::test_timeout_keeps_finished_artifacts` |
| TC-P3-PL02-04 | 02 | 并发上限 3 | 同时提交 4 个 | 第 4 个 **HTTP 200** + `status=queued` + `queue_position≥1`；**不是 4xx/429**（ADJ-1 裁定） | 同上 `::test_concurrency_queue_slot` |
| TC-P3-PL02-05 | 02 | 已禁用 | 调用执行 | **`16004`**（`PLUGIN_DISABLED`＝已停用；**不要与 `16003` 混用**）；入口置灰（前端断言）+ 文案「该插件已停用，请联系管理员启用」 | 同上 `::test_disabled_plugin_blocks_execution` |
| TC-P3-PL02-06 | 02 | 已安装 | 插件内构造直连底座调用 | 拒绝（G-28 运行时对偶）+ 不产生任何底座请求（桩计数 0） | `test_capability_whitelist_runtime.py::test_direct_base_call_rejected` |
| TC-P3-PL02-07 | 02 | 1 个在跑 + 3 个排队 | 让在跑任务完成 | 排队任务**自动转 `running`**（无需用户重新提交）；`queue_position` 递减；最终 4 个全部 completed（ADJ-1 追加判据） | `test_plugin_lifecycle.py::test_queued_task_auto_starts` |
| TC-P3-PL03-01 | 03 | 研发工作台 | `GET capabilities` | 条数 == manifest 声明（**3**：②③⑤）；每项含 `display_name/reason/denial_effect/category/required` | `test_plugin_authz.py::test_capability_list_matches_manifest` |
| TC-P3-PL03-02 | 03 | 同上 | 取消必需能力②后安装 | 安装失败；`message` 含「该插件需要「使用你已连接的办公工具」才能提交代码」；按钮置灰 | 同上 `::test_required_capability_denied_blocks_install` |
| TC-P3-PL03-03 | 03 | 同上 | 只授权②③、拒⑤ → 安装 | 安装成功；`denied_capabilities==[⑤]`；执行到步骤 5 → 「未完成：未授权写入代码仓库」+「去授权」 | 同上 `::test_partial_grant_degrades_step5` |
| TC-P3-PL03-04 | 03 | 已授权② | 撤销②→立即执行 | `E-4`；审计有 1 条授权变更；恢复授权后 retry 成功 | 同上 `::test_revoke_takes_effect_within_ttl` |
| TC-P3-PL03-05 | 03 | 普通成员 + 管理员未开⑤总开关 | 安装研发工作台 | 第 2 步提示「需要管理员开启「写入代码仓库」能力」+「复制申请说明」（前端复制，无新端点） | 同上 `::test_org_switch_off_shows_apply_hint` |
| TC-P3-PL03-06 | 03 | 三个前置可控 | 分别只破一个前置（套餐不支持 / 成员未授权 / 企业策略关闭） | 码值分别为 `15001` / `16005` / `16006`（**不是** `16005`/`16006` 混用）；三者 `message` 与 CTA 不同（升级套餐 / 重新授权 / 找管理员）；同时破多个时**按 `15001 → 16005 → 16006` 顺序返回** | 同上 `::test_error_order_plan_then_grant_then_policy` |
| TC-P3-PL03-07 | 03 | 企业开关可控 | 依次置 `org_switch_enabled` = `false` / `true` 后安装 | ①**PL1 顶层含 `org_capabilities` 且恰好 3 项**（3 个 `org.*` 能力，每项 `{capability_key, org_switch_enabled}`）；②**PL4 逐插件返回 6 项**且每项含 `org_switch_enabled`；③`false` → 安装失败 + `16006` 文案 + 按钮置灰；④`true` → 安装成功且 `granted_capabilities` 含⑤；⑤**两次表现必须不同**（证明前端未硬编码） | 同上 `::test_org_switch_flips_install_outcome` |
| TC-P3-PL03-08 | 03 | 已安装、已授权②③⑤ | `PUT /plugins/{id}/grants` 改本人授权（撤③加④） | 请求 `grants[{capability_key, granted}]`；响应含 `granted_capabilities[]` / `denied_capabilities[]` / `required_capabilities[]`；撤③后数组相应变化；**同 `Idempotency-Key` 重复提交 → 结果收敛**（授权状态与审计条数不变，仅 1 条） | `test_plugin_authz.py::test_update_own_grants_and_idempotent` |
| TC-P3-PL03-09 | 03 | 同上 | 尝试撤 `required=true` 的能力（②） | 失败（`16005` 语义）+ 文案说明必需能力不可撤销；授权状态**不变**（读回仍含②） | 同上 `::test_cannot_revoke_required_capability` |
| TC-P3-PL03-10 | 03 | A、B 两名成员各已安装 | 用 A 的 token 改 B 的授权 | 越权拒绝（`20003` 语义）；**A 与 B 的授权均不变**（分别读回比对） | 同上 `::test_cannot_update_others_grants` |
| TC-P3-PL04-01 | 04 | 已装 1.0.0 | 发布 1.1.0 → 升级 | `installed_version==1.1.0`；`has_update==false`；任务可执行 | `test_plugin_version.py::test_upgrade_updates_version` |
| TC-P3-PL04-02 | 04 | 同上 | 连点两次升级 | 版本记录**仅 1 条**（幂等）；`previous_version` 不被覆盖成 1.1.0 | 同上 `::test_upgrade_idempotent` |
| TC-P3-PL04-03 | 04 | 已升级 | 回滚 → 再回滚 | 首次回滚到 `1.0.0`；第二次 `E-7`（或 20005）且版本不变 | 同上 `::test_rollback_once_only` |
| TC-P3-PL04-04 | 04 | 升级前有历史任务 | 升级后打开该任务 | `step_titles` 与升级前**逐项相等**（§3.4） | 同上 `::test_history_uses_snapshot_steps` |
| TC-P3-PL05-01 | 05 | 开发文档 | 结构断言 | 6 节齐全（声明字段表/能力枚举/输入规范/输出类型/界面规范/允许接口清单） | `test_plugin_dev_doc.py::test_doc_sections_present` |
| TC-P3-PL05-02 | 05 | 同上 | 文档「允许调用的接口」vs 代码端点集合 | **集合相等**（双向差集为空） | 同上 `::test_allowed_endpoints_match_code` |
| TC-P3-PL05-03 | 05 | 样例插件 | 跑通 1 次任务 | 任务 completed；产物 ≥1；全程无人工参数注入（固定输入） | 同上 `::test_sample_plugin_runs_end_to_end` |

### 4.2 插件市场（MK，14 条，映射 P0-P3-06~10）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-MK06-01 | 06 | 扫描插件目录得集合 S | `GET market?page_size=50` | `plugin_id` 集合 **== S**（不硬编码 6，ADJ-10）；`categories[]` 覆盖 6 个分类 | `test_plugin_market.py::test_market_items_match_manifest_set` |
| TC-P3-MK06-02 | 06 | — | `category=dev` / `keyword` 大小写与空格 / 排序 | ①过滤项 `category` 全为 `dev`；②`keyword` 大小写与首尾空格差异结果**集合相等**；③三种排序的 `plugin_id` 序列两两不同且评分排序与 `rating_avg` 单调 | 同上 `::test_category_keyword_sort` |
| TC-P3-MK06-03 | 06 | 分页 12/页 | 取第 1、2 页 | `plugin_id` **无交集**；`total` == 两页累计（或 `len(S)`）；无匹配时返回空列表 + 空态文案（非错误码） | 同上 `::test_pagination_no_overlap_and_empty_state` |
| TC-P3-MK06-04 | 06 | 免费版企业 | 打开市场 | 整页提示「当前套餐不含插件能力，升级后即可使用」+ 升级入口；**无错误码**（Q3-1） | 同上 `::test_free_plan_shows_upgrade_not_error` |
| TC-P3-MK07-01 | 07 | 已安装未评价 | `GET market/{id}` | `capabilities[]` 条数 == `GET capabilities` 条数；`step_titles[]` 顺序与工作台一致 | `test_plugin_detail.py::test_detail_matches_capabilities_and_steps` |
| TC-P3-MK07-02 | 07 | 无评价 | 详情页评价区 | 文案「还没有人评价，用过后可以来说说」；**不是**空表格（DOM 断言有该文本节点） | 同上 `::test_empty_review_guidance` |
| TC-P3-MK07-03 | 07 | 4 种状态各一 | 详情页主按钮 | 未安装→「安装」；已安装启用→「打开工作台」；已禁用→「启用」；有更新→「更新」（4 断言） | 同上 `::test_primary_button_by_status` |
| TC-P3-MK07-04 | 07 | 不存在/已下架 | `GET market/{id}` | `E-1` + 文案「该插件已下架，去市场看看其他插件」；页面给「返回市场」 | 同上 `::test_unlisted_plugin_detail` |
| TC-P3-MK08-01 | 08 | 未安装 | 安装 → `GET installed` | `status=enabled`（Q3-3/ADJ-9）；导航出现入口 | `test_plugin_install.py::test_install_enables` |
| TC-P3-MK08-02 | 08 | 已启用 | 禁用 → 再启用 | `disabled` → `enabled`；禁用后宿主页置灰 + 文案「该插件已停用，请联系管理员启用」 | 同上 `::test_disable_then_enable` |
| TC-P3-MK08-03 | 08 | 有 2 个历史任务 | 卸载 | `GET installed` 不含；`GET tasks?plugin_id=X` 仍返回 2 条且详情可打开（只读） | `test_plugin_uninstall.py::test_uninstall_keeps_history` |
| TC-P3-MK08-04 | 08 | 有 1 个执行中任务 | 卸载 | 允许卸载 + `running_task_count==1`；该任务最终 completed（不被打断） | 同上 `::test_uninstall_with_running_task` |
| TC-P3-MK08-06 | 08 | 已安装（已授权②③⑤、有 settings） | 重复调 `POST /plugins/{id}/install` | **HTTP 200 + `already_installed=true`**（**不是 `16003`**）；`granted_capabilities[]` 与 `settings` **均不变**；审计仅 **1 条** `plugin.install`（证明「装」与「改授权」是两个动作） | `test_plugin_install.py::test_reinstall_is_idempotent_no_grant_change` |
| TC-P3-MK08-05 | 08 | 弹窗 | 禁用/卸载二次确认 | 禁用文案含「停用后工作台入口将不可用，已发起的任务会继续完成」；卸载含「历史任务记录会保留但不能再发起新任务」+「已生成的产物 180 天后清理」（3 条字符串断言） | 同上 `::test_confirm_dialog_consequences` |
| TC-P3-MK09-01 | 09 | 已安装 | 同成员同插件提交 2 次 | 评价总数 **1** 条且内容为第二次；`rating_avg` 随更新重算 | `test_plugin_review.py::test_review_upsert` |
| TC-P3-MK09-02 | 09 | 未安装用户 | 提交评价 | `E-13` + 文案「使用过这个插件才能评价」 | 同上 `::test_review_requires_installed` |
| TC-P3-MK09-03 | 09 | 3 条评分 5/4/3 | 读列表与汇总 | `rating_avg` 与 `sum/3` 差 **≤0.1**；`rating=6` → 参数错误 +「评分请选择 1 到 5 星」 | 同上 `::test_rating_avg_and_range_validation` |
| TC-P3-MK09-04 | 09 | 匿名提交 | 读列表 | `author_name=="某位同事"`；响应中**不含**真实姓名与邮箱（正则 0 命中） | 同上 `::test_anonymous_review_masks_identity` |
| TC-P3-MK10-01 | 10 | 已装 1.0.0 | 后台发 1.1.0 → `GET installed` | 该行 `has_update=true`、`latest_version=1.1.0`；更新后 `has_update=false`、`installed_version=1.1.0` | `test_plugin_installed_list.py::test_update_badge_and_clear` |
| TC-P3-MK10-02 | 10 | 有 1 个任务 | 读 `last_used_at` | == 该插件最近一次任务的 `created_at`；无任务时 == `null` 且页面「尚未使用」 | 同上 `::test_last_used_at_from_task` |

### 4.3 工作流执行可视化（WF，12 条，映射 P0-P3-11~14）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-WF11-01 | 11 | 6 步任务执行中 | 订阅事件（A6） | 首个 `step` 事件 < 5s 到达；连跑 3 次均满足（ADJ-6） | `test_workflow_progress.py::test_first_step_event_within_5s` |
| TC-P3-WF11-02 | 11 | 同上 | 轮询 10 次 | `status_index` 序列**非递减**；不出现回退（如 completed → running） | 同上 `::test_status_monotonic` |
| TC-P3-WF11-03 | 11 | 6 步完成 3 步 | 读进度 | `progress == 50.0`（误差 <1e-6）；`steps.length == 6` | 同上 `::test_progress_ratio` |
| TC-P3-WF11-04 | 11 | 执行中 | 断网 30s（停桩）后恢复 | 步骤数与 A4 相等、`step_id` 集合相等、无重复无缺失；冲突以服务端为准 | `::test_reconnect_full_sync` |
| TC-P3-WF11-05 | 11 | 执行中 | DOM 扫描 | `node`/`step_id`/`workflow`/`prompt`/`LLM` **0 命中**；步骤标题无英文技术词 | `#p3-manual` + `check-banned-terms.mjs --p3-workflow` |
| TC-P3-WF12-01 | 12 | 连跑 3 次任务 | 产物归属 | 每个 `artifact_id` 反查 `task_id` 唯一（无一跨任务串）；已完成步骤 `artifacts.length ≥1` | `test_workflow_artifacts.py::test_artifact_belongs_to_one_task` |
| TC-P3-WF12-02 | 12 | 6 步任务 | 读 `steps[].artifacts[]` | 类型覆盖 file/link/table/稿件/文档 时字段齐备（`download_url` 或 `url` 或 `preview_text` 至少 1）；表格给前 5 行预览 | 同上 `::test_artifact_payload_by_type` |
| TC-P3-WF12-03 | 12 | 产物过期（时钟 +181d） | 点下载 | 文案「产物已过期，可重新发起任务」；**不出现**错误码/堆栈；任务记录仍可打开 | 同上 `::test_expired_artifact_message`（`pg`） |
| TC-P3-WF12-04 | 12 | 已完成任务 | 产物卡片 DOM | 已完成任务的**提交记录/代码文件**无编辑入口；可编辑类型（稿件/JD/公文）有「编辑」入口（AC-02 与 P0-19/21 口径并存） | `#p3-manual` |
| TC-P3-WF12-05 | 12 | 稿件/JD（可编辑类型） | `PUT /agent/tasks/{id}/artifacts/{aid}` 保存编辑 | `version` **+1**；`version_history[]` 含旧版本且内容**逐字一致**；`download_url` 下载到**新版本**（sha256 == 保存内容 sha256，**不得「存了但下载还是旧的」**） | `test_workflow_artifact_edit.py::test_edit_bumps_version_and_download_new` |
| TC-P3-WF12-06 | 12 | `type=link` / `type=file` 产物 | 调 WF2 编辑 | **必须被拒**（代码文件与提交记录不可编辑，否则 AC-02 结果物核查作废）；拒绝后产物内容与 sha256 均不变 | 同上 `::test_non_editable_types_rejected` |
| TC-P3-WF12-07 | 12 | docx 产物 | 改字段后**重新导出** | 新 docx 可解压出 `word/document.xml` 且段落含新字段值；`content_sha256` **已重算**（≠ 旧 sha）；`version_history` 追加 1 条 | 同上 `::test_reexport_docx_and_sha_recomputed` |
| TC-P3-WF12-08 | 12 | content 含敏感词 | 保存编辑后读产物 | **一票否决**：编辑后**必须重跑脱敏**——产物中该敏感词被消解（**不得因「用户改过」跳过 sanitize**）；审计留 1 条编辑记录 | 同上 `::test_edit_reruns_sanitize` |
| TC-P3-WF13-01 | 13 | 注入「工具未连接」 | 读失败步骤 → 点重试本步 | `code==0`；`failure.retryable==true`；3 个动作齐；新任务 `retry_of`=原任务；前序步骤 `skipped` 且 `artifact_id` 复用（ADJ-2） | `test_workflow_retry.py::test_retry_step_reuses_prior_artifacts` |
| TC-P3-WF13-02 | 13 | 未失败任务 | 调 `POST retry` | `E-11`；界面不出现重试按钮（DOM 断言） | 同上 `::test_retry_on_success_rejected` |
| TC-P3-WF13-03 | 13 | 接管态（offer 金额 / 提交前确认） | 推进时钟 15min | 仍 `waiting_confirm`；**不自动继续**；给确认/放弃两个动作 | 同上 `::test_manual_gate_does_not_auto_continue` |
| TC-P3-WF13-04 | 13 | 失败态 | 点「联系管理员」 | 复制文本 `\b[1-3]\d{4}\b` **0 命中**、无 `Traceback`/`at 0x`/堆栈特征 | 同上 `::test_diagnostic_copy_has_no_codes_or_stack` |
| TC-P3-WF14-01 | 14 | 5 个不同插件/状态/发起人/时间的任务 | 四维筛选 + 1 条组合 | 4 条独立断言 + 1 条组合断言，返回集合精确相等（不是子集） | `test_workflow_history.py::test_filters_four_dimensions` |
| TC-P3-WF14-02 | 14 | 三插件各完成 1 次 | 读 `result.checklist[]` | 项数 **4 / 3 / 4**，名称与 PRD §3.2.3 ②**逐字相等**；每项 `status ∈ {verified, pending_manual}` | 同上 `::test_checklist_items_exact`（`e2e`） |
| TC-P3-WF14-03 | 14 | 非发起人且无管理权 | 打开他人任务 | 权限错误 + 文案「你没有权限查看该任务」；**不返回**任务内容（响应体不含任务名/产物名） | 同上 `::test_cross_user_task_detail_denied` |

### 4.4 研发工作台（DEV，8 条，映射 P0-P3-15~17）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-DEV15-01 | 15 | 表单 | 分别缺标题/描述/仓库提交 | 按钮置灰 + 精确指出缺项（3 条断言） | `test_dev_workbench.py::test_required_fields` |
| TC-P3-DEV15-02 | 15 | 未连接仓库 | 打开表单 | 下拉显示「还没有连接代码仓库」+ 连接入口（非空列表、非报错） | 同上 `::test_no_repo_connected_empty_state` |
| TC-P3-DEV15-03 | 15 | 步骤 1、2 完成 | 读状态 → 重做方案 | `waiting_confirm`；重做后步骤 1 的 `artifact_id` **不变**（未重跑）、方案 `version+1` | 同上 `::test_redo_plan_keeps_step1` |
| TC-P3-DEV16-01 | 16 | 方案已确认 | 步骤 3、4 产物 | 代码文件路径集合 **⊆** 方案清单；测试文件 ≥1 且命名含被测文件主干 | 同上 `::test_code_paths_subset_of_plan` |
| TC-P3-DEV16-02 | 16 | 注入空产出 | 步骤 4 | 该步「未完成」+ 可重试；重试后 completed（不得静默通过） | 同上 `::test_empty_output_fails_and_retries` |
| TC-P3-DEV16-03 | 16 | 产物界面 | DOM 扫描 | 0 处 `diff`/`patch`/`sha`；以「改动内容」呈现（新增/删除行有区分标记） | `#p3-manual` |
| TC-P3-DEV17-01 | 17 | 到步骤 5 未确认 | 查 bare 仓库 `git rev-list --count` | **不变**（未确认不得提交） | `test_dev_commit_e2e.py::test_no_commit_before_confirm`（`e2e`） |
| TC-P3-DEV17-02 | 17 | 已确认 | 查提交 + 抽 1 文件 | `git cat-file -e <sha>` 成功；`git show <sha>:<path>` 的 sha256 == 产物 preview sha256；`pr` 桩收到 `head==sha`（ADJ-7） | 同上 `::test_commit_exists_and_content_identical`（`e2e`） |
| TC-P3-DEV17-03 | 17 | 授权过期（桩返回 401） | 执行步骤 5 | 原因「代码仓库的授权已过期」+「重新连接」；重连后 retry 成功 | 同上 `::test_repo_auth_expired_recoverable` |

### 4.5 内容运营（CNT，9 条，映射 P0-P3-18~20）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-CNT18-01 | 18 | 表单 | 未选平台提交 | 置灰 +「请至少选择一个发布平台」 | `test_content_workbench.py::test_platform_required` |
| TC-P3-CNT18-02 | 18 | 勾 3 平台 | 步骤 1、2 产物 | 素材要点 ≥3 且每条带来源；3 组选题、每组标题 ≥3；标题字数 公众号 ≤64 / 其他 ≤40（ADJ-5 字数法） | 同上 `::test_topic_per_platform` |
| TC-P3-CNT18-03 | 18 | 空素材 | 步骤 1、2 产物 | 标「未找到可靠素材」；稿件中 8 条事实表述正则 **0 命中**（ADJ-4） | 同上 `::test_no_fabrication_without_sources` |
| TC-P3-CNT18-04 | 18 | 勾 5 平台 | 提交 | 提示「单次最多 4 个平台」+ 拒绝（N-4） | 同上 `::test_platform_limit_four` |
| TC-P3-CNT19-01 | 19 | 勾 3 平台 | 步骤 3 产物 | 稿件 3 篇、`platform` 互不相同、每篇含 title/body/tags | 同上 `::test_three_drafts_distinct_platforms` |
| TC-P3-CNT19-02 | 19 | 目标 800 字 | 字数核对 | 640 ≤ `len(去空白)` ≤ 960；越界时「自动重生成 1 次」，仍越界 → 「待人工调整」 | 同上 `::test_word_count_within_20_percent` |
| TC-P3-CNT19-03 | 19 | 稿件已生成 | 编辑保存 → 回看 | 版本 +1；切回上一版本内容**逐字相等**（sha256 相同） | 同上 `::test_draft_version_roundtrip` |
| TC-P3-CNT20-01 | 20 | 3 篇稿件 | 步骤 5 排期表 | 4 列齐（平台/标题/计划时间/状态）；行数 == 3；导出 CSV 逐行与页面一致 | `test_content_schedule.py::test_schedule_table_and_csv`（`e2e`） |
| TC-P3-CNT20-02 | 20 | 计划时间设为过去 | 步骤 5 | 失败 + 指出冲突行；改时间后 retry 成功（不重跑步骤 3） | 同上 `::test_past_schedule_rejected` |
| TC-P3-CNT20-03 | 20 | 公众号+小红书 | 步骤 6 | 公众号 `status=已发布` 且 `receipt_id` 非空；小红书 `status=待人工发布` 且下载链接 HTTP 200 + 非空 | 同上 `::test_publish_receipt_and_manual_pending`（`e2e`） |

### 4.6 行政人事（HR，9 条，映射 P0-P3-21~23）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-HR21-01 | 21 | 3 份 PDF 简历 | 步骤 2 评分表 | 3 行；每行 匹配分∈[0,100]、亮点 ≥2、**风险非空**、结论∈枚举、理由 ≥30 字（ADJ-5）；「风险」为空则该行判失败重跑 | `test_hr_screening.py::test_scoring_table_fields`（`e2e`） |
| TC-P3-HR21-02 | 21 | 21 份简历 | 上传 | 「一次最多 20 份，请分批上传」；不产生部分结果 | 同上 `::test_resume_limit_twenty` |
| TC-P3-HR21-03 | 21 | 1 份加密 PDF + 2 份正常 | 步骤 2 | 加密行标「无法读取，请解除保护后重新上传」，其余 2 行正常（**部分失败不拖垮整体**） | 同上 `::test_encrypted_resume_partial_failure` |
| TC-P3-HR22-01 | 22 | 3 人「建议面试」 | 步骤 3 | 提纲 3 份；每人 ≥5 问；每题含考察点（非空）；含参考追问 ≥1 | `test_hr_interview.py::test_outline_five_questions`（`e2e`） |
| TC-P3-HR22-02 | 22 | 渠道未连接 | 步骤 4 渠道选择 | 该渠道置灰 +「还没有连接企业微信」+ 连接入口 | 同上 `::test_channel_not_connected` |
| TC-P3-HR22-03 | 22 | 时间冲突（桩返回 busy） | 发送邀约 | 提示「该时间段面试官已有安排」+ **2 个**可选时间；改选后成功返回 `event_id` | 同上 `::test_schedule_conflict_gives_options` |
| TC-P3-HR23-01 | 23 | 步骤 5 | Offer 草稿 | 3 个金额字段 **`==""`**；页面含「请人事填写」（ADJ-3/§0.4） | `test_hr_offer_docs.py::test_amount_fields_empty`（`e2e`） |
| TC-P3-HR23-02 | 23 | 固定语料（3 JD + 4 公文），**扫描范围 = `output_key=offer` 的产物** | 双规则扫描 | 仅扫 offer 产物（简历筛选的「待定」合法，**不得**纳入扫描）：**R1 带单位**正则 **0 命中**；**R2 上下文窗口**（薪资词 ±20 字符内 `\d{3,7}`）**0 命中**；**豁免项固化**（日期/工号/百分比/「3 个月」）不计入；offer 内出现「面议/待定」判失败 | 同上 `::test_no_fabricated_amount`（`e2e`） |
| TC-P3-HR23-03 | 23 | 未填金额 | 点导出 | 「请先填写薪资信息」；不产生文件 | 同上 `::test_export_blocked_without_salary` |
| TC-P3-HR23-04 | 23 | 4 类公文要素齐 | 生成 + 导出 | 4 篇正文非空；`.docx` 均可 `python-docx` 打开、段落数 >0、文件大小 >0 | 同上 `::test_four_doc_types_exportable`（`e2e`） |

### 4.7 页面与合规（UI，6 条，映射 P0-P3-24~25）

| 编号 | 需求 | 前置 | 步骤 | 期望（可断言） | 落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P3-UI24-01 | 24 | 构建产物 | 静态扫描 | 宿主组件文件数 **==1**；`pluginId` 布局分支 0 处（G-27 同源） | `check-plugin-ui-assets.mjs` |
| TC-P3-UI24-02 | 24 | 构建产物 | 路由清单 | `/plugins/{id}`、`/workbench/{id}`、`/workflows/{taskId}` 三个路由存在 | `check-page-states.mjs --routes` |
| TC-P3-UI24-03 | 24 | 新页面 | 三态静态断言 | 每个新页面同时引用 `LoadingState`/`EmptyState`/`ErrorState` 且 `ErrorState` 传 `onRetry` | 同上 `--p3-pages` |
| TC-P3-UI24-04 | 24 | 未安装插件 | 访问 `/workbench/{id}` | 空态「该插件还没有添加」+「去市场看看」；**不是**错误页 | `#p3-manual` |
| TC-P3-UI25-01 | 25 | 全站 | 禁词/占位/错误码正则三面扫描（含 manifest 文案） | 0 命中；`\b[1-3]\d{4}\b` 0 命中 | `check-banned-terms.mjs` / `check-no-placeholders.mjs` |
| TC-P3-UI25-02 | 25 | 插件资产目录 | 扫描 | `.css/.scss/.less/.jsx/.tsx` **0 个**；字体/图标包 0 个；`--self-test` 注入 1 个 `.css` → **exit 1** | `check-plugin-ui-assets.mjs --self-test` |
| TC-P3-UI25-03 | 25 | 界面 | DOM 扫描 | 0 处 `Error`/`Exception`/模型名/向量库/RAG/Agent/token/scope/permission/grant | `#p3-manual` + 静态扫描 |

### 4.8 三插件端到端「结果物核查」专组（12 条，`e2e` + `p0`）

> 原则（PRD 第 5 章）：**页面演示不作为达标依据**，每个插件必须有「离开界面也成立」的结果物。本节用例一律**绕过 UI**，直接对产物/仓库/文件断言。

#### 4.8.1 研发工作台（4 条）

| 编号 | 断言（可机械执行） | 判据 |
| --- | --- | --- |
| TC-P3-E2E-DEV-01 | 本地 bare 仓库真实提交 | `git -C <bare> rev-list --count HEAD` 增加 1；`git show --stat <sha>` 的文件数 == 产物文件数；`git cat-file -e <sha>` 成功 |
| TC-P3-E2E-DEV-02 | **抽 1 个文件逐字比对** | `sha256(git show <sha>:<path>) == sha256(产物 preview 文本)`，且逐行 `==` 比对无差异（不是「包含」） |
| TC-P3-E2E-DEV-03 | 合并请求 | 桩收到 **1 次** `create_pr(head=<sha>, base=<branch>)`；`pr_url` 含 `<sha>`；客户环境补验可打开（V-3） |
| TC-P3-E2E-DEV-04 | 核查清单 4 项 | 名称逐字 == [需求要点, 代码文件, 用例文件, 提交记录与合并请求]；`status` 全为 `verified`（无一 `pending_manual`）；重新 GET 结果一致（「刷新/换端」等价） |

#### 4.8.2 内容运营（4 条）

| 编号 | 断言 | 判据 |
| --- | --- | --- |
| TC-P3-E2E-CNT-01 | **3 篇稿件** | 勾 3 平台 → 稿件数 3、`platform` 互不相同、每篇含 title/body/tags；字数在目标 ±20% |
| TC-P3-E2E-CNT-02 | **排期表 4 列** | 表头 == [平台, 标题, 计划时间, 状态]（逐字）；行数 == 3；CSV 导出逐行与页面一致 |
| TC-P3-E2E-CNT-03 | **已接入有回执 / 未接入标待人工** | 公众号 `receipt_id` 非空 + `status=已发布`；小红书 `status=待人工发布` + 稿件下载 200 + 发布指引非空 |
| TC-P3-E2E-CNT-04 | 24h 后可追溯（时钟 +24h） | 稿件版本号与 `receipt_id` 仍可读；产物 sha256 与 24h 前一致（未被静默改写） |

#### 4.8.3 行政人事（4 条）

| 编号 | 断言 | 判据 |
| --- | --- | --- |
| TC-P3-E2E-HR-01 | **评分表每行风险非空** | 3 行 × 5 字段齐；`risk` 逐行 `!= ""`；结论 ∈ {建议面试, 待定, 不通过}；理由 ≥30 字 |
| TC-P3-E2E-HR-02 | **提纲 ≥5 问** | 份数 == 建议面试人数；每份 `questions.length >= 5`；每题 `assessment_point != ""` |
| TC-P3-E2E-HR-03 | **Offer 金额永久留空 + 0 处金额臆造（双规则，仅扫 `output_key=offer`）** | ①3 个金额字段 `== ""`（`null`/`0`/「面议」/「待定」均判失败）；②R1 带单位正则 **0 命中**；③R2 上下文窗口 **0 命中**；④豁免项固化且单独断言存在；⑤**扫描范围不含简历筛选产物**（该处「待定」合法） |
| TC-P3-E2E-HR-04 | 公文可导出 | 4 类各 1 篇；`.docx` 可打开、段落数 >0；核查清单 4 项齐且名称逐字相等 |

### 4.9 P0 覆盖度自检（25 条 → 用例映射，每条 ≥2）

| P0 | 用例 | 条数 | P0 | 用例 | 条数 |
| --- | --- | --- | --- | --- | --- |
| 01 PL 声明标准 | PL01-01~04 | **4** | 14 WF 历史与核查 | WF14-01~03 + E2E-DEV-04/CNT-04 | **5** |
| 02 PL 运行时 | PL02-01~07 | **7** | 15 DEV 需求与方案 | DEV15-01~03 | **3** |
| 03 PL 能力授权 | PL03-01~10 | **10** | 16 DEV 代码与用例 | DEV16-01~03 | **3** |
| 04 PL 版本管理 | PL04-01~04 | **4** | 17 DEV 提交可核查 | DEV17-01~03 + E2E-DEV-01~03 | **6** |
| 05 PL 开发文档 | PL05-01~03 | **3** | 18 CNT 素材与策划 | CNT18-01~04 | **4** |
| 06 MK 市场浏览 | MK06-01~04 | **4** | 19 CNT 稿件与配图 | CNT19-01~03 + E2E-CNT-01 + WF12-05/06 | **6** |
| 07 MK 详情页 | MK07-01~04 | **4** | 20 CNT 排期与发布 | CNT20-01~03 + E2E-CNT-02/03 | **5** |
| 08 MK 生命周期 | MK08-01~**06** | **6** | 21 HR 筛选 | HR21-01~03 + E2E-HR-01 | **4** |
| 09 MK 评价 | MK09-01~04 | **4** | 22 HR 面试与邀约 | HR22-01~03 + E2E-HR-02 | **4** |
| 10 MK 已安装管理 | MK10-01~02 | **2** | 23 HR Offer 与公文 | HR23-01~04 + E2E-HR-03/04 + WF12-07 | **7** |
| 11 WF 进度时间线 | WF11-01~05 | **5** | 24 UI 宿主与入口 | UI24-01~04 | **4** |
| 12 WF 产物展示 | WF12-01~04 | **4** | 25 UI 文案与合规 | UI25-01~03 + WF12-08 | **4** |
| 13 WF 失败与重试 | WF13-01~04 | **4** | — | **合计（去重后）** | **116** |

> 去重说明：E2E 组 12 条在本表中**按服务对象归属到对应 P0**（每条只计一次），因此合计 = 矩阵中 4.1~4.7 的 104 条 + E2E 组 12 条 = **116**，与 §4.1~§4.8 的行数逐一对应。25 条 P0 全部 ≥2 条 ✓，P0 总数 **116 ≥ 60** ✓。

### 4.10 非 P0 用例（22 条，不计入阶段门）

| 组 | 条数 | 内容 |
| --- | --- | --- |
| P1 需求（6 条） | 10 | 开发者文档站、插件评分排序实验、工作台帮助栏、跨插件复用产物、批量安装、插件使用统计 |
| 跨域风险 | 6 | 并发取消、产物大文件（10MB）、`artifact` 路径穿越、manifest 超大（1MB）、任务步骤数 >8（N-1）、多租户同插件并发 |
| 前端手工走查 | 4 | 授权抽屉可键盘操作、失败态三动作可点、刷新补齐、跨端一致 |
| 兼容性 | 2 | 阶段二页面零回归（10 页快照对比）、A1-A4 旧客户端调用（不带新参数） |

### 4.11 人工抽验清单（ADJ-10 裁定：8 条 / 35 条断言，抽样规则固化）

> **为什么要有这一节**：AC-02 的「结果物核查」与 AC-04 的「界面一致性」里有少量必须人眼确认的断言（逐字比对、键盘可达、跨端一致）。若把「抽哪个文件、看几眼」留给验收现场自由裁量，同一份产物两次验收可能得出不同结论——这等同于判据失效。
> 因此本节把**抽样对象、抽样顺序、重复次数、留证方式**全部写死；执行人只按表操作，不做判断。

**通用规则（8 条共用）**：
1. **抽样对象由规则决定，不由执行人挑选**（如「路径字典序最小」而不是「随便挑一个」）。
2. **每个抽样点必须做 3 次重复**（同一对象取 3 次 / 同页面刷新 3 次 / 同断点 3 轮），**3 次结果必须完全一致**；任意一次不一致即判该条失败（不允许「取多数」）。
3. 留证：截图 + 对应 DOM 片段 + 被执行命令原文，落到 `apps/web/docs/qa-walkthrough-phase3/`，命名 `HV-<n>-<yyyymmdd>-<序号>`。
4. 抽验失败按 §7.1 判级：涉及「结果物不可核查 / 金额 / 权限」判 P0，其余判 P1/P2。

| # | 抽验项 | 抽样对象（固定） | 抽样顺序 | 断言数 | 判定 |
| --- | --- | --- | --- | --- | --- |
| **HV-1** | 研发提交内容逐字一致（服务 E2E-DEV-02） | 按**方案影响文件清单的声明顺序**取第 1 个**产物文件**中**第一个**符合扩展名的路径（不是最后一个、也不是中间；不用路径字典序——随机目录会让抽样每次不同） | ①`git show <sha>:<path>` 取原文 → ②与仓库文件**逐字节**比对（sha256 相等）→ ③逐行 `==` → ④行数一致 → ⑤失败时输出**前 200 字符 diff** | **5** | 5 条全过；任一不等判 P0 |
| **HV-2** | 排期表与 CSV 一致（服务 E2E-CNT-02） | 排期表**计划时间最早**的 1 行 + 全表 | ①表头 4 列逐字 → ②该行 4 个单元格 → ③CSV 对应行逐字段 → ④行数与稿件数相等 | **4** | 4 条全过；列名/行数不符判 P0 |
| **HV-3** | 评分表风险列（服务 E2E-HR-01） | **匹配分最高**与**最低**两行 + 全表 | ①两行 `risk` 非空且不等于占位词 → ②两行结论 ∈ 枚举 → ③全表 `risk` 逐行非空 → ④全表行数 == 简历数 | **4** | 4 条全过；任一 `risk` 为空判 P0 |
| **HV-4** | 金额双规则与豁免（服务 HR23-02 / E2E-HR-03） | Offer 全文 + 4 类公文全篇（固定语料） | ①R1 带单位正则 0 命中 → ②R2 上下文窗口 0 命中 → ③豁免项（日期/工号/百分比/「3 个月」）存在且未被 R1/R2 命中 → ④金额字段 == `""`（非「面议/待定」）→ ⑤「请人事填写」提示存在 → ⑥未填金额导出被拒 | **6** | 6 条全过；出现金额臆造或「面议」判 P0 |
| **HV-5** | 授权抽屉键盘与置灰（服务 P0-P3-03） | **研发工作台**（固定，不轮换） | ①Tab 顺序遍历 3 项能力 → ②取消必需能力②后按钮置灰 → ③置灰态 Tab 不可聚焦 → ④后果说明文案在抽屉内可视 | **4** | 4 条全过；按钮未置灰判 P1 |
| **HV-6** | 失败态三动作（服务 P0-P3-13） | 注入「工具未连接」的**步骤 5**（固定） | ①默认展开并高亮 → ②3 个动作按钮存在 → ③「重试本步」可点且触发新任务 → ④「联系管理员」复制文本无 4-5 位码/堆栈 | **4** | 4 条全过；缺任一动作判 P1 |
| **HV-7** | 刷新/换端补齐（服务 P0-P3-11 ④） | 6 步任务执行中**第 3 步完成后**（固定时机） | ①刷新后步骤数与服务端一致 → ②无重复无缺失（`step_id` 集合相等） → ③进度不回退 → ④换端（另一浏览器视口）结果一致 | **4** | 4 条全过；步骤丢失判 P0 |
| **HV-8** | 跨端一致（服务 AC-04） | `/workbench/{id}` 与 `/workflows/{taskId}` 两页 × 1280 / 768 两个断点（固定） | ①布局无溢出（无横向滚动条） → ②主按钮可见且可点 → ③步骤标题换行不截断 → ④两页在同一数据下文案一致 | **4** | 4 条全过；溢出/截断判 P2 |

> 合计：**8 条抽验项 / 35 条断言**（5+4+4+6+4+4+4+4 = 35）。任一条失败即该批次不得准出；抽验记录是批次验收报告的必备附件。

---

## 5. 分层测试落点

### 5.1 单元层（30 条，`apps/adapter/tests/unit/**`）

manifest 校验（8）、能力→端点映射与白名单（5）、状态机合法/非法跳转（4）、字数与金额正则（3）、进度与步骤序号计算（3）、错误映射（E-1~E-13，3）、版本比较语义（2）、清理任务 `expired_at` 判定（2）。

### 5.2 集成层（55 条，`apps/adapter/tests/integration/**`）

插件域端点（PL1-PL14）、任务域扩展（A1-A4 + WF1）、E2E 三插件（12）、跨租户与权限（5）、并发与超时（4）。

### 5.3 必须 `--run-pg` 的用例（18 条）

| 场景 | 条数 | 为什么必须真库 |
| --- | --- | --- |
| 插件安装/状态/卸载落库（PL5/PL6/PL7） | 4 | 需要唯一约束（同企业同插件仅 1 条安装记录）与事务回滚 |
| 版本快照与回滚（PL8/PL9） | 3 | 需要保留 1 个版本的部分唯一索引 |
| 评价 upsert（PL12） | 1 | 需要 `(tenant, plugin, member)` 唯一键 |
| RLS 跨租户（安装、任务、评价、产物） | 4 | 内存后端无法证明 RLS |
| 并发上限与排队（P0-P3-02 ③） | 2 | 需要行锁/计数一致性 |
| 产物过期清理（时钟 +181d） | 2 | 需要 `expired_at` 与批量 UPDATE |
| 审计（安装/授权/启停/卸载/升级/回退/执行） | 2 | 需要 `audit_log` 真实写入与不可变约束 |

### 5.4 前端静态断言与手工走查

静态：禁词（源码/产物/声明文案）、占位词、三态、错误码正则、插件资产、宿主分支（6 个脚本）。
手工（4 条，`apps/web/docs/qa-manual-checklist-phase3.md`）：授权抽屉键盘可达、失败态三动作、刷新补齐、跨端一致；每条留截图 + DOM 片段。

### 5.5 夹具清单（新增，不改阶段二夹具）

| 夹具 | 作用 | 关键约束 |
| --- | --- | --- |
| `local_bare_repo` | 离线「真实仓库」：`git init --bare` + 预置 1 个 commit + 1 个分支 | 用 `git` CLI 真实对象，不用 mock；测试后清理到临时目录 |
| `github_stub` | 合并请求/授权过期/分支冲突的契约桩 | 记录调用次数与入参（`head/base`），可注入 401/409 |
| `wechat_mp_stub` / `mail_stub` / `wecom_stub` | 发布回执 / 邮件 / 企微日程 | 返回固定 `receipt_id`/`message_id`/`event_id`；可注入失败 |
| `clock` | 时间注入（产物过期、15 分钟接管、24h 回看、TTL 5s） | **全局禁用 `sleep`**；所有时间类判据必须走该夹具 |
| `fail_injector` | 步骤级故障注入（工具未连接/空产出/加密文件/时间冲突） | 按 `step_no` 注入，只影响目标步骤 |
| `manifest_factory` | 生成合法/非法 manifest（G-26 与单测共用） | 12 个负样本与 4 个正样本固定化 |
| `plugin_installer` | 一键安装 + 授权 + 配置（E2E 前置） | 必须走真实端点，不直接写库 |

### 5.6 文件总览（新建文件，架构定稿后补全路径）

| 文件 | 条数 |
| --- | --- |
| `tests/unit/test_manifest_validation.py` | 8 |
| `tests/unit/test_capability_mapping.py` | 5 |
| `tests/unit/test_plugin_state_machine.py` | 4 |
| `tests/unit/test_text_metrics.py`（字数/金额正则） | 5 |
| `tests/unit/test_plugin_error_mapping.py` | 4 |
| `tests/unit/test_version_semver.py` | 2 |
| `tests/unit/test_artifact_retention.py` | 2 |
| `tests/integration/test_plugin_market.py` | 8 |
| `tests/integration/test_plugin_install.py` | 12 |
| `tests/integration/test_plugin_authz.py` | 10 |
| `tests/integration/test_plugin_version.py` | 4 |
| `tests/integration/test_plugin_review.py` | 4 |
| `tests/integration/test_workflow_progress.py` | 5 |
| `tests/integration/test_workflow_artifacts.py` | 4 |
| `tests/integration/test_workflow_artifact_edit.py` | 4 |
| `tests/integration/test_workflow_retry.py` | 4 |
| `tests/integration/test_workflow_history.py` | 3 |
| `tests/integration/test_plugin_e2e_dev.py` | 4 |
| `tests/integration/test_plugin_e2e_content.py` | 4 |
| `tests/integration/test_plugin_e2e_hr.py` | 4 |
| `tests/integration/test_plugin_isolation.py`（`pg`） | 4 |
| `apps/plugins/tools/validate_manifests.py` | 门禁 G-26 |
| `apps/plugins/tools/check_capability_whitelist.py` | 门禁 G-28 |
| `apps/web/scripts/check-plugin-ui-assets.mjs` | 门禁 G-27 |
| `apps/web/docs/qa-manual-checklist-phase3.md` | 手工 4 条 |

> 合计核对：P0 单元 **30** + P0 集成 **74**（含 `pg` 18）= **104** 条 pytest（新增 `test_workflow_artifact_edit.py` 4 条、`test_plugin_install.py` +1）；另有前端静态断言 **8** 与手工抽验 **4**（脚本/走查单实现，同样计入 §1.3 的 **116** 条 P0）。另 22 条非 P0 散布于上述文件（单元 10 / 集成 10 / 手工 2）。原 `test_plugin_artifact_retention.py` 按裁定降级为 `tests/unit/test_artifact_retention.py` 的 2 条静态断言，已计入单元 30。

---

## 6. 回归策略（阶段二不得回退）

### 6.1 既有用例保护

阶段一 421 + 阶段二（W3 闭环 1119 passed）为**保护基线**：阶段三任何批次不得使其转红或转 skip；新增代码若需改既有断言，必须在批次报告里说明「为什么旧断言本身错了」并经 QA 复核。

### 6.2 4 处加性扩展的兼容性断言（逐端点）

| 端点 | 新增（只增不删） | 断言 |
| --- | --- | --- |
| A1 `POST /agent/tasks` | 可选 `plugin_id` | 不带该字段 → 行为与阶段二完全一致（响应字段集合相等）；带非法 `plugin_id` → `E-1` 而非 500 |
| A2 `GET /agent/tasks` | 5 个筛选参数 | 不传任何新参 → 结果与阶段二一致；只传旧参 → 不受影响 |
| A3 `GET /agent/tasks/{id}` | `result.checklist[]` | 旧字段逐字段不变；`checklist` 缺失或为 `[]` 时前端不报错 |
| A4 `GET .../steps` | `steps[].artifacts[]`、`steps[].failure` | 旧字段（`step_id`/`status`/`title`）名称与类型不变；新字段可缺省 |

### 6.3 基线数字（只增不减）

| 指标 | 阶段二终值（`3b82df2`） | 阶段三判据 |
| --- | --- | --- |
| 全量用例 | 1119 | 只增；0 failed / 0 skipped / 0 xfail / 0 xpass |
| p0 用例 | 178 | 只增；`-m p0 --run-pg` 0 failed/0 skipped |
| 总覆盖率 | 89.99% | ≥85%（硬地板）；不得低于阶段二终值 -1pp |
| `business+db` | 89.10% | ≥88% |
| 插件域新模块 | — | **≥90%** |
| 端点 | core 30 / phase2 38 | 均不变；**phase3 = 16**（合计 84） |

### 6.4 flaky 与重试

同一用例连续 3 次运行结果不一致 → 标记 `@pytest.mark.flaky` 并在 48h 内定位；**禁止**用 `--reruns` 掩盖。时间类判据一律走 `clock` 夹具（§5.5），禁止 `sleep`。

### 6.5 执行节奏

每批次：静态门禁 → 单测 → 集成（not pg）→ 集成（pg）→ 覆盖率 → 三基线 → 前端门禁 → 该批 P0 实跑；里程碑：全量 + 五道前端门禁 + 手工走查。

---

## 7. 缺陷分级与准入准出

### 7.1 分级（在阶段二基础上补插件域典型例子）

| 级别 | 定义 | 本阶段典型例子 |
| --- | --- | --- |
| **P0** | 数据/安全/契约/合规不可接受 | ①跨租户可见插件安装或任务 ②未授权能力可调用（G3-2 失守）③插件携带自定义 UI/脚本（AC-04 一票否决）④结果物不可核查（仓库无提交 / 排期表缺列 / Offer 出现金额）⑤phase2 端点数或字段被改（G-29 红）⑥禁词/占位命中 ⑦服务启动失败或迁移不可重跑 |
| **P1** | 单端点功能/错误码/文案错，或前端缺状态 | ①`E-4`/`E-5` 混用 ②步骤失败被实现成非 0 `code`（前端整屏报错）③重试后前序步骤仍重跑 ④历史任务按新版本模板渲染 ⑤卸载后历史打不开 ⑥「最近使用时间」口径错 ⑦三态缺失 |
| **P2** | 边界/分页/排序/审计字段/UI 细节 | ①第 2 页与第 1 页有重复 ②评分排序未按官方推荐 ③`last_used_at` 显示格式 ④进度百分比小数位 ⑤空态文案与筛选无结果文案相同 |
| **P3** | 建议与优化 | 图标尺寸、动画时长、帮助栏文案顺序 |

**判级原则**：①「用户看到不属于自己的数据」一律 P0；②「用户看到错误信息/技术词」至少 P1；③同一根因多现象按最高级别计 1 条；④实现与文档冲突先看架构裁决；架构未落稿期间以 PRD 为准并登记待裁决（§0.3）。

### 7.2 批次准入（DoR）

① 该批端点已在 `docs/api/README.md` 与基线 JSON 同步；② 新增错误码三处同步（G-30）；③ 该批测试文件已建、用例数 ≥ 本方案第 9 章要求；④ 插件 manifest 与声明文案已过 G-26；⑤ 已知未完成项清单。

### 7.3 里程碑准出与交付门槛

| 项 | 门槛 |
| --- | --- |
| P0 用例 | **全绿**（**116** 条，0 failed / 0 skipped） |
| 覆盖率 | 总 **≥85%**、`business+db` **≥88%**、插件域新模块 **≥90%** |
| 门禁 | **G-26~G-30** 全绿（含负样本自检），阶段二 G-01~G-25 相关项不回退；G-31 已移出本阶段（只留保留期静态断言） |
| 回归 | 阶段二 1119 用例 0 failed / 0 skipped；core 30 + phase2 38 不变 |
| 结果物 | 三插件 E2E 12 条全绿；L1+L2 判据成立（L3 登记未验证） |
| 人工 | 4 条手工走查留证；开发文档按 ADJ-8 完成人工复跑留证 |

### 7.4 阶段门 checklist

① `pytest -q -m p0 --run-pg` → 0 failed / 0 skipped，p0 **≥116**；② 25 条 P0 各有 ≥2 条用例且全绿；③ **G-26~G-30** 全绿 + 各自 `--self-test` exit 1（G-31 已移出，改为保留期静态断言）；④ 三插件 E2E 12/12；⑤ 禁词/占位/插件资产三面 0 命中；⑥ 阶段二 1119 用例零回退；⑦ 覆盖率三项达标；⑧ 未验证项 V-1~V-7 已按 §10 披露。

---

## 8. 测试数据与构造

### 8.1 原则

1. **离线可跑**：所有 P0 用例在无外网环境可执行（CI 用 `--offline` 跑一遍验收）。
2. **真实优先**：能用真实对象就不用桩——GitHub 用本地 bare 仓库（真实 git 对象、真实 SHA）；文件产物落盘算 sha256；docx 真解析。
3. **可复现**：LLM 类输出用**固定 seed + 固定语料 + 契约桩**，断言只看结构与可机械判定的量（条数/字数/正则/集合关系），不比对生成文本内容。
4. **时间可注入**：所有时间判据走 `clock` 夹具。
5. **失败可注入**：按步骤注入故障（`fail_injector`），避免「为了测失败而改生产代码」。

### 8.2 场景构造表

| 场景 | 构造方式 | 用途 |
| --- | --- | --- |
| 沙箱仓库 | `git init --bare` + 预置 `main` 与 `feature` 分支 + 1 个初始 commit | DEV E2E、提交核查 |
| 分支冲突 | 提交前在 bare 仓库推 1 个新 commit 到目标分支 | TC-P3-DEV17-03 变体 |
| 公众号/邮件/企微桩 | 返回固定 `receipt_id/message_id/event_id`；可注入 401/409/busy | CNT20、HR22 |
| 简历样本 | 3 份正常 PDF + 1 份加密 PDF + 1 份损坏 docx | HR21 |
| 长任务 | 桩步骤 sleep 由测试注入（不经真实等待） | 超时/取消/断线 |
| 过期产物 | `clock.advance(days=181)` | WF12-03（真实清理留给阶段四 V-8） |
| 空素材 | 知识库空 + 不传文件 | CNT18-03 |
| 21 份简历 | 复制模板生成 | HR21-02 |

### 8.3 造数脚本

`apps/plugins/tools/make_fixtures.py`（生成 manifest 负样本、简历样本、公文要素语料）+ `tests/fixtures/plugin_e2e/*`（固定 JSON 输入）。所有造数必须可重复执行（幂等），且不写入仓库（落 `tmp_path`）。

---

## 9. 排期与批次配套（**编号已与架构 §9 对齐**）

> **每批「怎么做算过」与 QA 实跑命令见 `docs/qa-phase3-batch-acceptance-checklist.md`**（同一套编号）。本节只给用例归属与 p0 累计数。
> 编号口径（架构 §9）：B16 声明模型+加载器+G-26 ｜ B17 市场与安装（PL1-PL5 + PL13）｜ B18 生命周期（PL6-PL12）｜ B19 任务引擎 + A 域扩展 + 9 表 ｜ B20 三插件装配与降级矩阵 ｜ B21 端点基线与门禁。
> **B20 按验收粒度拆 B20-1 / B20-2，但代码编号仍是 B20 一个批次一个 commit**（不改架构编号）。

| 批次 | 范围 | 本方案用例 | 出口判据（p0 累计） |
| --- | --- | --- | --- |
| **B16** | 声明模型 + 加载器 + 6 份 manifest + G-26 schema | PL01-01~04、PL05-01~03（**7**） | G-26 `--strict` 0 / `--self-test` 1；累计 **≥7** |
| **B17** | 市场与安装（PL1-PL5 + PL13/**PL14**） | MK06-01~04、MK07-01~04、PL03-01~**10**、MK10-02（**19**） | G-28/G-30 绿；报错顺序 15001→16005→16006；PL14 改本人授权 + `required` 撤不掉 + 他人越权 + 幂等；累计 **≥26** |
| **B18** | 生命周期（PL6-PL12） | PL02-01、MK08-01~**06**、PL04-01~04、MK09-01~04、MK10-01（**16**，含 `pg`） | 迁移幂等 + PL5 重复安装幂等；累计 **≥42** |
| **B19** | 任务引擎 + A 域扩展 + 9 表（超时/排队/续跑/产物编辑/核查清单） | PL02-02~07、WF11-01~05、**WF12-01~08**、WF13-01~04、WF14-01~03（**26**） | 超时不丢产物；排队 `code=0`+`queued` 并自动转 running；**WF2 编辑版本+1/下载新版本/编辑后重跑脱敏**；累计 **≥68** |
| **B20** | 三插件装配与降级矩阵（验收粒度拆两屏） | B20-1：DEV15~17 + E2E-DEV（**13**）<br>B20-2：CNT18~20 + HR21~23 + E2E-CNT/HR（**28**） | B20-1 → 累计 **≥81**；B20-2 → 累计 **≥109** |
| **B21** | 端点基线与门禁（收口批次，不新增 p0） | —（G-29/G-30 + G3-10/G3-11 + 阶段二零回退） | 三基线 **30/38/16**（合计 84）；累计仍 **109** |
| **B23** | 授权抽屉文案与 `16006` 提示（UI25 界面层复核） | **0**（复核 B17 的 MK06-04/PL03-05/PL03-07 界面层） | 前端门禁绿 + `16006` 文案与后端一致；累计仍 **109** |
| **B24** | 宿主页唯一与三态（UI24/UI25） | UI24-01~04 + UI25-01~03（**7**） | 宿主组件唯一 + 三态齐备；**p0 116 全绿** |

> 每批结束都要跑「阶段二 1119 用例零回退」，不是只在 B21 跑。
> **串号提醒**：超时/并发排队/停用拦截/直连拒绝属 **B19（任务引擎）**，不得写进 B16 的验收项——这正是主理人 2026-10-23 指出的协调缺陷，已在检查单 §5 第 9 条列为退回情形。

---

## 10. 未验证项落地（V-1~V-7）

| 编号 | 事项 | 本环境如何模拟（可执行） | 必须客户环境补验 |
| --- | --- | --- | --- |
| **V-1** | 小红书/抖音真实发布 | 断言「待人工发布」+ 稿件下载 200 + 发布指引非空；**不造回执**（B-8 不做假回执） | 无需补验（V1.0 明确不接入）；客户若要求，另立需求 |
| **V-2** | 公众号/邮件真实回执 | 契约桩返回 `receipt_id`；同时覆盖真实 4xx 映射（401 未授权 / 429 限流 / 5xx 重试） | **是**：客户环境完成 1 次真实发布并留存回执截图 |
| **V-3** | GitHub 真实仓库提交 | 本地 bare 仓库真实提交 + `git show` 逐字比对 + `create_pr` 桩断言 `head==sha` | **是**：客户环境 1 次真实推送 + PR 打开 |
| **V-4** | 第三方插件开放 | 不验证；以「无开发者入口」负面断言覆盖 | 不验证（B-1） |
| **V-5** | 浏览器级 E2E 自动化 | 静态断言（G-27/G-30 前端部分）+ 4 条人工走查留证 | 阶段四前关闭；届时可用 Playwright 补全 |
| **V-6** | 评分防刷与排序效果 | 只验证「同成员覆盖更新」与「排序不依赖评分」（Q3-4）；不承诺防刷 | 样本量足够后另评估 |
| **V-7** | 多副本并发上限与重试幂等 | 单副本下验证计数与排队语义；**不断言**多副本一致性 | **是**：多副本部署前改为集中式计数/锁，届时补验 |
| **V-8** | 产物保留期满的**真实清理**（原 G-31） | 本阶段只做静态断言：保留期字段存在且默认 **180**、过期文案常量存在；**不断言清理行为** | 阶段四压测时用注入时钟验证（主理人裁定：时钟注入成本高于收益） |

---

## 11. 遗留风险与自检声明

### 11.1 遗留风险（本方案已知）

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| 1 | LLM 生成质量不可机械判定 | 「素材要点是否真有价值」「方案是否合理」无法自动判定 | 只断言**结构与数量**（≥3 条、≥5 问、字数区间、正则 0 命中），质量由 4 条人工走查抽样 |
| 2 | 契约桩与真实平台的语义差 | 桩绿不代表真实平台绿 | V-2/V-3 客户环境补验；桩同时覆盖真实错误码映射 |
| 3 | ADJ-1~ADJ-10 未裁决前用例无法定稿 | 相应 10 条用例可能返工 | 先按「建议判据」写，裁决后一次性替换；已在 §0.3 标注影响用例 |
| 4 | 架构未落稿导致路径/错误码全为占位 | 落点与断言需二次替换 | 用例表只写 `E-n` 语义与端点编号，替换成本可控（§12） |

### 11.2 自检声明（三条）

1. **本方案不含「表现正常/体验良好」类不可断言判据**；每条期望都落到状态码/字段值/集合相等/计数/正则/字节数/注入时钟后的状态。
2. **每道新门禁都给了负样本自检**（G-26 12 条、G-27 4 条、G-28 5 条、G-29 4 条），并明确「自检 exit 1 = 通过」的返回码约定；同时给正样本防「全判红」。
3. **没有为了凑数把 P0 拆成多条**：116 条 P0 中 104 条按 PRD 条目（每条 P0 ≥2，最多 12）、12 条按 AC-02 结果物；§4.9 的合计与 §4.1~§4.8 的表格行数**逐条对得上**（104 + 12 = 116）。

---

## 12. 待架构落稿后的一次性替换清单（避免二次返工）

| 项 | 现在（占位） | 落稿后替换为 |
| --- | --- | --- |
| 端点路径 | PL1~**PL14** / **WF1-WF2** 编号（**phase3 = 16**） | 实际路径（`/api/v1/plugins/**`、`/api/v1/agent/tasks/{id}/retry`、**`PUT /api/v1/agent/tasks/{id}/artifacts/{artifact_id}`**；新增 `PUT /plugins/{id}/grants`） |
| 错误码 | `E-1`~`E-16` 语义编号；已确认 **`16005`=成员未授权、`16006`=企业策略关闭**（`16001` 废弃不复用） | 架构分配的完整数值表（须过 G-30 冲突校验）；报错顺序 `15001 → 16005 → 16006` 已在 §0.4 锁死 |
| 插件目录 | `apps/plugins/<id>/manifest.json` | 架构约定的目录与文件命名 |
| 插件域模块清单 | `plugin_service / plugin_runtime / plugin_market_service / plugin_repo` | 架构实际模块 → 用于「插件域 ≥90%」的覆盖率统计口径 |
| 能力→端点映射表 | 6 项能力 × 允许端点（PRD §3.2.1 语义） | 架构给定的映射表文件（G-28 的输入） |
| 版本快照表/产物表 | 推断存在 | 实际表名与列名 → 更新 §5.3 的 `pg` 用例 |

---

*作者：严过关（QA 工程师）· 2026-10-23 · 本方案只写判据与落点，**不含用例代码**（B16 批次落地后按本方案实现）；待主理人裁决 §0.3 的 10 条后定稿。*
