# ADR-0005：总纲—PRD 对齐裁决与文档层级（L0/L1/L2）

| 项 | 内容 |
| --- | --- |
| 记录版本 | v1.0 |
| 拍板日期 | 2026-10-23 |
| 拍板人 | 桐鸣科技创始人（授权主理人齐活林代拍） |
| 依据 | 《Moy（麦子）项目总纲》V1.0（`docs/moy-project-charter-v1.0.md`，L0 北极星）；`docs/alignment-2026-10-23-charter-vs-prd.md`（20 条差异 + 43 项台账）；`docs/archive/audit-2026-10-23-moy-charter-consolidated.md`；`docs/alignment-2026-10-23-tech-options.md`（技术评估） |
| 关联文档 | `docs/decisions/0004-phase1-open-decisions-ratification.md`（D-01~D-08 已决）、`docs/prd-v1.0.md`、`docs/plan-v1.0.md`、`docs/architecture-v1.0.md` |
| 决策范围 | 总纲 vs PRD/总方案 的 20 条不一致；6 项技术实施路径；W0 开发清单 |

---

## 〇、结论速览

| # | 决策项 | 结论 |
| --- | --- | --- |
| D-09 | 文档层级 | **L0 总纲（北极星）→ L1 plan+PRD（V1.0 范围与需求）→ L2 架构/阶段 PRD**；冲突以上游为准 |
| D-10 | 产品定位（AL-01） | **改为总纲口径**「面向广泛工作需求的模块化 AI 工作平台」；企业办公为 V1.0 首批落地场景，非产品边界 |
| D-11 | 目标用户（AL-03） | PRD 角色表**补 R0（个人/独立开发者）**，标注「V1.1 交付，V1.0 仅保证基础可用」；企业 R1-R5 全保留 |
| D-12 | 非目标第 7 条（AL-04） | 改为「**V1.0 范围约束（非长期边界）**」；章节标题加「仅限 V1.0」 |
| D-13 | Open WebUI（AL-05） | **按事实修正**：PRD/plan/架构/README 删除「前端参考」表述；总纲 §7.2 加编者脚注（待创始人确认） |
| D-14 | 成熟度表述（AL-06/13/14/16） | **全面引入总纲五级状态**（L1-L5），区分「目标」与「已验证状态」；PRD 增成熟度列与成果类验收 |
| D-15 | 核心指标（AL-07） | 本轮出《指标定义文档》；埋点与看板排 W1（先完成率/采纳率，后成本/维护效率） |
| D-16 | 插件生态（AL-08） | **维持 V1.0 官方插件 + 加注阶段性 + 补淘汰/替换流程**（W1） |
| D-17 | 统一任务入口（AL-09） | **分两步**：W1 做对话页目标式发起（M）；侧边栏重构 V1.x |
| D-18 | 商业模式（AL-10/15） | 套餐与定价表述为「**设计假设，未经验证**」；私有化标注 L2（L3 待现场） |
| D-19 | 跨生态工具（AL-11） | 本轮**诚实降级 + 演示模式标注**；真实化（邮箱 + GitHub）排 W1-W4 |
| D-20 | 竞争对标（AL-12） | 维持对标意图，**收紧为 6 项可核验维度** |
| D-21 | 固定排期（AL-17） | **维持 24 周计划基线 + 加注**「非对外承诺，按证据动态调配」 |
| D-22 | 隐私合规（AL-18） | 隐私政策/DPA/安全白皮书**列入 V1.0 交付**（文档类，W1） |
| D-23 | 技术黑盒化（AL-19） | **维持全站禁词门禁**（强项不破坏），仅加注适用范围 |
| D-24 | 阶段 PRD（AL-20） | 4 份阶段 PRD 各加 3 行**继承声明**，正文不改 |
| D-25 | AR-017 租户伪造 | **修复**：跨租户请求必须通过成员关系校验（`department_member ∪ member_role`），默认拒绝；本轮 W0 |
| D-26 | 登录流程（AR-013） | 本轮**诚实降级 + OIDC 脚手架**（env 驱动、生产禁占位）；完整授权码流程排 W1 |
| D-27 | 指标埋点 | 完成率/完成时间/复用率可从现有表计算；采纳率需新增 adoption 事件表（W1） |
| D-28 | 交付清单失真（LED-043） | 修正 `phase4-delivery-manifest.md`：install/backup/restore 标已建；`upgrade.sh`/`rollback.sh` 列入待建 |
| D-29 | 组件归属表述（Milvus）与 D-05 重定位 | 总纲 §7.2 Milvus 行加**编者注**：由 Dify 底座直接使用、适配层不直连；PRD §2.4 记录 SaaS=Milvus / 轻量=pgvector 双后端事实；D-05 从「共享实例隔离机制」重定位为「租户可核查性」（LED-044，S 级，W1） |

---

## 一、背景与裁决原则

### 1.1 背景

2026-10-23 发布的《Moy 项目总纲》V1.0 将产品定位为「面向广泛工作需求的模块化 AI 工作平台」，并明确要求「不得把设想描述为事实」。而现有 `plan-v1.0.md` / `prd-v1.0.md` 仍以「企业级跨生态办公 AI 平台」为定位、自称「项目权威需求来源」，且存在「首版即商用/100%」等与实现状态不符的表述。全面审计（同日）确认：连接器 5/5 全桩、登录占位、支付未联调、核心指标 0 落地。

### 1.2 裁决原则

1. **总纲优先**：总纲是定位、战略、原则与核心指标的唯一基准（L0）。
2. **不推翻已交付成果**：PRD 承载的 V1.0 范围、接口规范、验收方法是有效资产，降级为实现层文档而非重写；阶段一至四的验收结论按「V1.0 范围内交付验证」继续有效。
3. **范围纪律不降**：把「永久边界」改为「V1.0 范围约束」，约束力不变，只去掉与总纲冲突的长期语义。
4. **诚实优先于叙事**：任何能力的状态以其可验证成熟度（L1-L5）为准；未达 L3 不得对外声明可用。
5. **强项不破坏**：技术黑盒化门禁、RLS 双保险、fail-closed 权限、错误码一致性、缺陷台账等已验证明星机制保持不动。
6. **成本可控**：优先选择「零代码/低代码」的文档与表述修正；工程改动聚焦安全与可信交付。

---

## 二、文档层级裁决（D-09）

### 2.1 层级定义

| 层级 | 文档 | 职责 | 冲突处理 |
| --- | --- | --- | --- |
| **L0** | `docs/moy-project-charter-v1.0.md` | 定位、战略、原则、核心指标（北极星） | 最高，其余文档不得与其冲突 |
| **L1** | `docs/plan-v1.0.md`（V1.0 范围基线）+ `docs/prd-v1.0.md`（V1.0 需求规格） | 本期做什么、做到什么程度 | L0 优先；plan 与 PRD 冲突时以 plan 为范围基准、PRD 为需求细节 |
| **L2** | `docs/architecture-v1.0.md`、各阶段 PRD/验收报告 | 怎么实现、如何验证 | 以上游为准 |

### 2.2 决策依据

- 方案 A（总纲 L0、PRD/plan 降 L1）：一次性消除「谁优先」的争论，后续审计/开发/QA 有统一裁判；对已交付成果零影响。
- 方案 B（维持 plan 为 Master Plan）：把矛盾留给下一轮，且总纲 §十五明确要求 AI 协作者以其为背景基准。
- **选定 A**。代价：plan 从「Master Plan」降为「V1.0 范围基线」，需向交付总监说明权责变化（不影响其交付管理职能）。

---

## 三、20 条不一致的逐项裁决（D-10 ~ D-24）

> 每条格式：差异 → 方案 A/B 要点 → 选定 → 依据。完整利弊分析见 `docs/alignment-2026-10-23-charter-vs-prd.md` §3。

### D-10 产品定位（AL-01）

- **差异**：总纲「面向广泛工作需求的模块化 AI 工作平台」 vs PRD/plan「面向企业与团队用户的跨生态、全场景、可私有化的办公 AI 平台」。
- **方案 A（总纲口径）**：改为广泛工作平台，企业办公为 V1.0 首批落地场景。利：解决 12 项下游偏差根因；与投资人口径一致；不缩小 V1.0 商用范围（企业客户照卖）。弊：对外材料需更正，销售话术重构。
- **方案 B（维持 PRD）**：利：文档零改动、话术聚焦。弊：与总纲永久冲突，每次审计重复提出；个人/开发者入口自我关闭。
- **选定：A**。依据：纯文档 S 级成本，零代码，不损害任何已交付物，保留未来市场期权。

### D-11 目标用户（AL-03）

- **差异**：总纲 5.1-5.5 含个人/独立开发者/创业者；PRD 角色表 R1-R5 全为企业内角色。
- **方案 A**：补 R0（个人/独立开发者），标注 V1.1 交付。利：定位有需求锚点。弊：角色表变宽，需防「写了没做」的超述。
- **方案 B**：维持 R1-R5。弊：改定位不改角色 = 仅话术对齐，下轮审计必判。
- **选定：A（带 V1.1 标注）**。依据：定位变更必须有需求锚点；V1.0 不交付 R0 专属入口的事实写进表格，避免新超述。

### D-12 非目标第 7 条（AL-04）

- **差异**：PRD「不覆盖总方案 1.3 之外的能力（含一切未提及的通用办公套件能力）」= 永久边界，与总纲「不预先限定/持续扩展」冲突。
- **方案 A**：改为「V1.0 范围约束（非长期边界）」，章节标题加「仅限 V1.0」。利：约束力不降（仍是 P0 范围基线），解除永久语义。弊：范围纪律从「靠条文」变为「靠评审判断」。
- **方案 B**：维持原条文。弊：与总纲字面冲突；把未来通用能力全部挡在门外。
- **选定：A**。

### D-13 Open WebUI（AL-05）

- **差异**：总纲 §7.2 将其列为「当前技术基础」，PRD §2.4 称「前端参考」，但仓库 `deploy/` 与 `architecture-v1.0.md` 零引用，前端为自研 Next.js 16.4.2 + React 19.3.1。
- **方案 A**：按事实修正（PRD/plan/架构/README 删除参考表述；总纲 §7.2 加脚注）。利：技术路线与代码一致，尽调可自证。弊：需创始人确认总纲修订。
- **方案 B**：保留「规划参考」。弊：违反总纲第十一章状态诚实；审计已 grep 证实零落地。
- **选定：A**。总纲原文不擅改：以**编者脚注**形式标注「评估过，未采用；前端为自研实现」，并在 ADR 记录待创始人确认。

### D-14 成熟度表述（AL-06 / AL-13 / AL-14 / AL-16）

- **差异**：PRD G1「首版即达到可直接付费交付」「100% 统一」「18/18 全覆盖」 vs 实际 L1-L2 状态。
- **方案 A**：引入总纲五级（L1 代码存在 / L2 功能可运行 / L3 集成已验证 / L4 生产可用 / L5 商业已验证），目标表加「当前状态」列；原则 1 改为「面向商用交付为目标，对外表述以已验证状态为准」；「18/18」明确为需求覆盖度而非可用性；六章补成果类验收（AC-17~AC-20）。
- **方案 B**：保留激励性表述。弊：与连接器全桩、登录占位并存，可被一次演示证伪；违反总纲 §十一/§十五。
- **选定：A**。依据：性价比最高——不改功能只改宣称方式，直接消除 3 项高危超述风险。

### D-15 核心衡量指标（AL-07）

- **差异**：总纲第十四章 8 项指标 vs PRD 六章验收无对应项，代码 0 落地。
- **方案 A**：本轮出《指标定义文档》（定义/口径/采集点/看板归属），W1 埋点与看板。利：为后续所有优先级讨论提供共同语言（原则七）。弊：埋点 L 级，短期数字不好看。
- **方案 B**：继续只统计用量。弊：原则七彻底落空；无法回答「Moy 是否帮用户把事做成」。
- **选定：A（文档先行 + 分批埋点）**。阈值不编造：金标集通过率阈值标注「由创始人拍板」。

### D-16 插件生态（AL-08）

- **差异**：总纲 §7.5 生态引入 vs PRD 非目标 5「不开放第三方插件市场」。
- **方案 A**：V1.0 开放第三方。弊：与 schema 级安全模型冲突，需沙箱/审核/吊销/分成，交付风险极高。
- **方案 B**：维持官方插件 + 加注「V1.0 阶段性策略」+ 补淘汰/替换流程。利：安全模型不破；给生态留轨道。
- **选定：B**。依据：开放是架构级决策，不能在交付前夜拍；「不开放」必须写成阶段性而非永久。

### D-17 统一任务入口（AL-09）

- **差异**：总纲 4.1「围绕目标发起、不必理解底层结构」 vs 9 个并列模块。
- **方案 A（分两步）**：W1 对话页加「目标式发起 + 所需输入/权限提示」（M）；侧边栏重构 V1.x（L）。利：以最小闭环兑现核心承诺，避免交付前夜大改版。弊：入口层仍是过渡形态。
- **方案 B**：维持 9 模块。弊：总纲 4.1 未兑现，审计连续两轮判高。
- **选定：A**。

### D-18 商业模式与私有化（AL-10 / AL-15）

- **差异**：PRD「商业化就绪/可对外销售/断网一键部署一次成功」 vs 支付未联调、私有化仅在开发环境验证。
- **方案 A**：套餐与定价标注「设计假设，未经真实付费验证」；私有化标注「交付部署包与文档（L2），L3 需客户现场验证」。利：与总纲 §九「潜在选择、未产生收入」一致；不否定已建代码。弊：销售话术变保守。
- **方案 B**：维持「可对外销售」。弊：一次下单即可证伪。
- **选定：A**。

### D-19 跨生态工具（AL-11）

- **差异**：PRD「首批 5 款深度打通」 vs 5/5 桩实现。
- **方案 A**：表述层改「鉴权-调用-数据回流全链路联调（当前桩实现）」，UI 加「演示模式」标注；事实层 W1-W4 真实化邮箱 + GitHub，其余 3 款维持演示。利：卖点要么兑现要么诚实。弊：短期跨生态故事变弱。
- **方案 B**：维持「深度打通」。弊：任何实测即穿。
- **选定：A**。
- **实施要点**（经架构师 T3 评估细化）：实现状态必须由**实现自述**（`ToolConnector.IMPLEMENTATION_MODE: ClassVar[str] = "demo"`）而非 YAML/配置声明，避免「配置说 live、实现是桩」的静默缺口；`ToolItem` 暴露 `implementation_mode`，前端工具卡片显示「演示」徽标；新增 registry 断言测试，未来出现 `live` 时强制同步。W1 首个真实化对象选**邮箱 `send_message`（SMTP）**——标准库零新依赖、可在本地 SMTP sink 端到端验证、外部副作用可控；真实化必须同时交付 SSRF 护栏（私网开关、metadata 拒绝、端口白名单、超时）。

### D-20 竞争对标（AL-12）

- **方案 A**：收紧为 6 项可核验维度（对话/检索问答/代码辅助/工具调用/协作管理/成果交付）。利：可验收、有证据。弊：叙事不如「对标大厂」响亮。
- **方案 B**：维持全面对标。弊：AC-01 无定义、无法执行。
- **选定：A（维持对标意图、收紧口径）**。

### D-21 固定排期（AL-17）

- **方案 A**：删掉 24 周承诺。弊：交付管理失去基线。
- **方案 B**：维持 + 加注「计划基线，非对外承诺；阶段门禁以证据为准」。利：保留管理价值，不与总纲冲突。
- **选定：B**。

### D-22 隐私合规（AL-18）

- **方案 A**：隐私政策/DPA/安全白皮书列入 V1.0 交付（S 级文档成本）。利：私有化客户采购前置条件；关闭 LED-020。
- **方案 B**：延后 V1.x。弊：DPA 卡采购；总纲 §10.4 为基础能力。
- **选定：A（W1 交付）**。

### D-23 技术黑盒化（AL-19）

- **方案 A**：放宽为「主界面不得出现术语，专业场景另设入口」。弊：破坏全站禁词门禁的机械可判定性（当前是最硬资产）。
- **方案 B**：维持全站门禁 + 加注适用范围（终端 UI；内部文档/运营后台/适配层日志不受限）。利：强项保留。
- **选定：B**。

### D-24 阶段 PRD 继承（AL-20）

- **方案 A**：4 份阶段 PRD 各加 3 行继承声明，正文不改。利：阻断扩散，成本极低。弊：历史正文仍有字面冲突（接受「历史文档 + 头部澄清」）。
- **方案 B**：逐份重写。弊：4000+ 行，风险高收益低。
- **选定：A**。

---

## 四、技术实施决策（D-25 ~ D-28）

### D-25 AR-017 SaaS 租户伪造修复

- **事实**：`app/core/security.py:217-219` `X-Tenant-Id` 无条件覆盖令牌 `tenant_id`；`:467-470` 直接 `replace()` 回写。成员关系数据已存在：`department_member`（tenant_id, department_id, user_id）+ `member_role`（`identity_service.list_members` 文档明确「组织来源 = department_member ∪ member_role」）。
- **方案 A（成员关系校验）**：保留 `X-Tenant-Id` 作为多租户切换入口，但跨租户时必须验明「当前用户 ∈ 目标租户」；不通过返回 20003。支持多租户用户合法切换，语义最正确。
- **方案 B（令牌优先）**：`X-Tenant-Id` 与令牌不一致即拒绝。实现最简单，但多租户用户无法切换（需重新登录），且与现有前端传 header 的契约冲突。
- **选定：A**。依据：既关闭越权，又保留多租户切换能力；成员数据现成，无需新增表。
  > 命名对齐：架构师技术评估中称其为「方案 B（令牌声明为权威基线，请求头只能在成员关系内选择）」。两者**落地行为一致**（同租户零查询、跨租户必验成员、平台令牌豁免），差异仅在框架表述；本 ADR 采用「方案 A」的称呼，实现以本文与 `tech-options` §2.1.4 为准。
- **实施要点**（经架构师 T1 评估细化，见 `docs/alignment-2026-10-23-tech-options.md` §2.1）：
  - 在 `get_request_context`（async）中做成员校验；`resolve_tenant_id` 仅做解析不做放行（保持同步纯函数，既有 5 条单测语义不变）。
  - 校验来源：`department_member ∪ member_role`（复用 `org_repo.links_of_user` / `role_repo.role_ids_of_user`；理想形态收敛为 `RoleStore.is_member()` 单方法）。
  - **配置开关**：`Settings.tenant_membership_check: Literal["auto","always","off"] = "auto"`；`auto` = SaaS 且 `auth_mode != "stub"`。`off` 作为紧急回退。
  - **stub 豁免**：`auto` 模式下 stub 鉴权不校验，保证开发态与既有测试不变。
  - 私有化模式（`OA_DEPLOY_MODE=private`）保持恒 `default`，不触发校验。
  - **`__platform__` 提前拒绝**：非平台令牌携带 `X-Tenant-Id: __platform__` → 直接 20003，避免流入 RLS 变成 30003（PG 后端当前会变 HTTP 500，内存为 403，属双后端不一致，必须同批修）。
  - 无成员记录 → 20003（禁止），记 `tenant_switch_denied` 日志（含 user_id / token_tenant / requested_tenant）。
  - 缓存：**本轮不实现**。当前行为契约是「**无缓存 → 撤权即时生效**」：`member_role` / `department_member` 记录一经删除，下一次请求立即 403，不存在 TTL 窗口。架构师核实代码中并无缓存；前端仅全局设置一次 `X-Tenant-Id`、无切换器，跨租户请求低频，缓存当前零收益。将来若引入缓存，**必须同时把「撤权延迟 ≤ TTL」写入契约**，否则运维会按「即时生效」承诺 SLA 却被缓存打破。
  - **G2 默认值（写死，供审计核对）**：`tenant_membership_check` 缺省为 `auto`，判定式 `deploy_mode == "saas" and auth_mode != "stub"`；`always` 强制校验；`off` 完全不校验（紧急回退通道）。默认值落在「SaaS 且真实验签」一侧，开发/私有化默认不受影响。
  - **开关双向判据（验收红线）**：`auto` 下「非成员 + 伪造跨租户头」→ 403/20003；`off` 下**同一请求 → 200 放行**（不是"不报错"——开关写反成 `always` 也会"不报错"，只有断言放行才能证明通道真通）。两个方向都测才算开关可用。
  - **已知限制（`always` + 私有化）**：`always` 模式在私有化部署下会把「令牌声明被忽略、生效租户恒为 default」判为跨租户，用户若无 `default` 归属会被 20003。默认 `auto` 不受影响；启用 `always` 前须先补私有化全员归属口径（W1）。
  - 测试：`tests/security/test_tenant_spoofing.py` 覆盖 10 类场景（跨租户非成员 → 20003、合法成员 → 200、仅部门成员 → 200、`__platform__` 伪造 → 20003 且非 30003、私有化豁免、stub 豁免、平台运维放行、开关双向等）；**`__platform__` 伪造用例需在 `--run-pg` 下也跑一遍**（内存/PG 双后端一致性）。
- **兼容性**：不改变端点契约、错误码注册制；94 个端点共用同一依赖，改一处全覆盖。既有测试需检查是否依赖「任意 header 直接放行」。

### D-26 登录流程（AR-013）

- **事实**：Keycloak 26.8.1 已在 `docker-compose.yml`；适配层 `keycloak_client.py` 存在；后端 `auth_mode=oidc` 强制验签；前端无任何 OIDC 配置项，登录页 `handleEnter` 直接跳 `/chat`。
- **方案 A（本轮完整 OIDC）**：前端接授权码 + PKCE、回调页、token 交换；需可运行的 Keycloak realm/client 与端到端验证。工作量 M-L，且本环境无运行中的身份服务，无法完成 L3 验证。
- **方案 B（本轮诚实降级 + 脚手架）**：登录页按环境区分——生产构建隐藏「直接进入」与「粘贴凭证」，显示「统一登录未配置」；开发构建保留并明确标注「仅开发」；新增 `NEXT_PUBLIC_OIDC_ISSUER` / `NEXT_PUBLIC_OIDC_CLIENT_ID` 配置位与跳转脚手架。W1 在身份服务可用后完成授权码流程与 L3 验证。
- **选定：B**。依据：不能在无法验证的情况下宣称登录可用（违反总纲第十一章）；但必须先消除「生产可直接进入」这一最危险形态，并铺好 OIDC 轨道。
- **补充（架构师 T2 评估）**：`/auth/callback` 路由当前不存在，一旦配置 issuer/client_id，用户会在统一登录成功后回跳 404——比不显示按钮更糟。本轮必须补一个**不落令牌、只提示「认证改造中」的回跳页**（或条件不渲染统一登录按钮），并在 W1 补 `state`/PKCE。另：后端仅支持 HS256 而 Keycloak 默认 RS256、`sub` 为 UUID 与全链路 `usr_*` 不匹配——这两条是「接 OIDC = L（5-8d）」的真实原因，须在 W1 排期中显式处理。

### D-27 指标埋点

- **可算（现有表，零迁移）**：任务完成率（`plugin_task.status`）、任务完成时间（`started_at`/`finished_at`）、重复使用率（`initiator_id` × `plugin_id` 30 天窗口）、单位成功任务成本（`plugin_task.usage_units`）；故障恢复可由 `retry_of`/`trigger_type='retry'` 近似。
- **需新增**：成果采纳率 → 新增 `artifact_adoption` 事件表（`tenant_id, adopt_id, artifact_id, task_id, user_id, action ∈ download/copy/insert, created_at`），由下载路由与前端「复制/回填」动作埋点；迁移编号 0012，含 RLS 与回滚 SQL，N-1 兼容沿用 `schema_guard.py` 探测降级模式。
- **选定**：本轮只出《指标定义文档》与 AC 条款；W1 先交付「完成率 / 完成时长 / 重复使用率 / 单位成功任务成本」四项统计切片（零迁移）+ 看板卡片；采纳率随 0012 迁移独立一批。
- **口径红线**：看板必须标「工作流任务」，与整体使用量（`/model/usage`）区分；样本量 `total < 10` 时显示「样本还少」，不得用占位词。
- **勘误（2026-10-24，ADR-0006 D-42）**：上文采纳动作枚举 `download/copy/insert` 与 `prd-v1.0.md` AC-18、`metrics-definition-v1.0.md` 的 `download/copy/edit_save` 不一致；**以 `edit_save` 为准**（语义为「编辑后保存」，`insert` 无法覆盖该动作）。0012 迁移的 `action` 枚举按 `download/copy/edit_save` 设计。

### D-28 交付清单失真（LED-043）

- **事实**：`docs/phase4-delivery-manifest.md:131-133` 将 `deploy/install.sh`、`backup.sh`、`restore.sh` 标为「待建」，但三文件实际存在（45KB/19KB/19KB）；`upgrade.sh`/`rollback.sh` 确缺。
- **选定**：按事实修正清单（9 项原「待建」实为已建，已补真实提交 SHA；`upgrade.sh`/`rollback.sh` 确缺，保持「待建」，备注「任务表登记于 T4-05.5（W4），建议 W1 前移评估」）；生成器 `--check` 升级为**失真门禁**（比对清单状态 vs 磁盘实际，不一致退出 1，已验证旧清单可复现红灯）。
- **依据**：阶段四证据链必须真实，否则后续所有「已交付」判断失真。
- **后续**：建议把 `gen_delivery_manifest.py --check` 加入 CI（当前 `.github/workflows` 未调用，清单仍可能再漂）。

### D-29 组件归属表述（Milvus）与 D-05 重定位

- **事实核对**：Milvus 非虚构但归属需精确——它与 etcd（共享）被 Dify 文档明列为**外部 Milvus 模式**的依赖，适配层代码**零 Milvus 调用**（只有 Dify 的 `/v1/datasets` HTTP），`OA_MILVUS_URI` 无任何使用方。
- **方案 A（删 Milvus 行）**：❌ 与「SaaS 由底座使用 Milvus」的事实相反，比原来更不准确。
- **方案 B（保留 + 加编者注）**：✅ 采纳。总纲 §7.2 该行加编者注「由 Dify 底座直接使用；Moy 适配层不直连向量库；轻量部署以 pgvector 替代」。理由：既保留事实（SaaS 拓扑确实用它），又消除「Moy 掌握向量库」的误读；与 Open WebUI 编者注同一处理方式。
- **PRD 补充**：§2.4 增加「向量检索双后端」事实段（SaaS=Dify+Milvus；轻量=pgvector），说明「替换向量库」的边界在底座而非适配层。
- **D-05 重定位（LED-044）**：原 D-05「共享 Milvus 实例的租户隔离机制待决」前提有误（适配层从不指定 collection），改判为「**租户可核查性**」——Dify dataset 无 `tenant_` 前缀、管理面无反向字段，排障/数据权利请求时无法从 dataset 名回推租户。S 级，W1，仅改命名约定与映射表，**不承诺 SQL 级隔离**（上层语义隔离已足够，但必须在架构文档写明边界）。
- **OpenAPI 定位残留**：`main.py` title/description + `docs/api/openapi.json` + `snapshot` 是**一个原子任务**（生成器从 app 对象整体导出，分开做会让契约门禁红），已作为单条任务派给工程师，不由产品侧改。
- **快照重生成（存量补齐，非契约变更）**：`openapi.snapshot.json` 自 T4-03 起从未重生成，缺 8 个已交付端点与 30 个 schema。**选定保留重生成版本**，但以两条断言证明它是「补齐」而非「改契约」：① 旧 paths 集合 ⊆ 新 paths 集合（无端点删除）；② 除 `info.title` 与 `ToolItem.integration_status` 外无既有字段删改。若断言不成立，回退快照并单独立项。

---

## 五、实施计划

### 5.1 W0（本轮，立即执行）

| 序 | 事项 | 归属 | 对应决策 | 完成判据 |
| --- | --- | --- | --- | --- |
| 1 | 文档层级 + 定位修订（PRD/plan/架构/README/阶段 PRD 头部） | 产品 | D-09~D-12、D-24 | 4 份文档 + README 与总纲口径一致 |
| 2 | 超述诚实化（目标表 + 原则 1 + plan 核心目标 + 成熟度列） | 产品 | D-14、D-18 | 无「可直接付费交付/100%」类无条件断言 |
| 3 | Open WebUI 事实修正（PRD/plan/架构/README + 总纲脚注） | 产品 | D-13 | 全仓无「Open WebUI 前端参考」表述（历史决策记录除外） |
| 4 | 非目标第 7 条 + 阶段 PRD 继承声明 | 产品 | D-12、D-24 | 章节标题与条文更新 |
| 5 | AR-017 租户伪造修复 + 安全测试 | 工程 | D-25 | 攻击用例 → 20003；合法切换 → 200；测试全绿 |
| 6 | 登录页诚实降级 + OIDC 脚手架 | 工程 | D-26 | 生产构建无「直接进入」；开发态明确标注 |
| 7 | 连接器演示模式标注（UI + 文档） | 工程+产品 | D-19 | 工具连接相关页面/文案明示演示级 |
| 8 | 指标定义文档（8 项指标口径/采集点/看板归属） | 产品 | D-15 | `docs/metrics-definition-v1.0.md` 出具 |
| 9 | 交付清单修正（LED-043） | QA | D-28 | 清单与实际一致；校验脚本通过 |

### 5.2 W1-W4（下一迭代，按依赖排序）

指标埋点与看板 → 统一任务入口（对话页目标式发起）→ 连接器真实化（邮箱 + GitHub）→ 金标任务集 20 条 → 合规三件套 → 个人场景包 → 支付真实联调 → 插件淘汰流程 → OIDC 完整授权码流程。

### 5.3 明确不做（避免范围蔓延）

侧边栏信息架构重构（V1.x）｜第三方插件市场开放（V1.x）｜技术黑盒化放宽｜24 周排期重构｜上帝文件拆分｜前端测试体系（均进 V1.x 技术债窗口）。

---

## 六、代价与风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| plan 降级引发交付管理权责争议 | 中 | 层级声明明确「plan 仍是 V1.0 范围唯一真源」，只把定位/指标上交 L0 |
| 销售话术变保守，短期成单受影响 | 中 | 提供「能力状态一页纸」，按 L1-L5 标注，销售可如实说明 |
| AR-017 修复引入请求级成员查询 | 低 | 短 TTL 缓存 + 复用 policy_service 缓存模式；压测验证 |
| 登录降级在演示时显得「功能缺失」 | 中 | 演示使用开发构建并明确标注「开发模式」；W1 补齐 OIDC |
| 指标埋点占用迭代资源 | 中 | 分两批，先完成率/采纳率；文档先行不阻塞 |
| 总纲脚注需创始人确认 | 低 | 先以编者注形式记录，创始人确认后转正式修订 |

---

## 七、W0 实施记录（2026-10-23）

| # | 事项 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | 文档层级 + 定位修订（PRD/plan/架构/README/3 份阶段 PRD） | ✅ | `prd-v1.0.md`（L0-L2 层级、§2.1 定位、R0、成熟度列、§6.6）、`plan-v1.0.md`（层级+1.1）、`architecture-v1.0.md`、`README.md`、3 份 phase PRD 继承声明 |
| 2 | 超述诚实化 | ✅ | PRD §2.2 目标表加「当前状态」列；原则 1 改为「以已验证状态为准」；`grep "首版即商用\|可直接付费交付"` = 0 |
| 3 | Open WebUI / Milvus 事实修正 | ✅ | PRD/plan 零残留；总纲 §7.2 两条编者注（原文未改） |
| 4 | 非目标第 7 条 + 继承声明 | ✅ | PRD §八标题与第 7 条；3 份阶段 PRD 头部 |
| 5 | AR-017 租户伪造修复 | ✅ | `security.py` 成员校验 + `tenant_membership_check` 开关 + `__platform__` 提前拒绝；`tests/security/test_tenant_spoofing.py` 10 用例；工程侧全量 1802 passed（架构师复核） |
| 6 | 登录诚实降级 + 回跳页 | ✅ | `login/page.tsx` 生产分支无绕过入口；`(auth)/auth/callback/page.tsx`（不落令牌）；`.env.example` 增 OIDC 占位 |
| 7 | 连接器演示标注 | ✅ | `IntegrationStatus`（默认 demo，来自连接器类声明）+ `ToolItem.integration_status` + 前端「演示」徽标 + `test_tool_registry_mode.py` |
| 8 | 指标定义文档 | ✅ | `docs/metrics-definition-v1.0.md`（M1-M8，294 行，阈值待拍板） |
| 9 | 交付清单修正 | ✅ | `phase4-delivery-manifest.md`（已建 82→91，待建 37→28）+ `gen_delivery_manifest.py --check` 升级为失真门禁，退出 0 |
| 10 | OpenAPI 契约残留清理 | ✅ | `main.py` title/description；快照重生成（旧 71 端点 ⊆ 新 79，无删除；仅 2 处放宽型约束） |
| 11 | 独立开发者场景包提案（V1.1） | ✅ | `docs/prd-v1.1-r0-scenario-pack-proposal.md`（3 场景，不排期、不承诺） |

**工作区说明**：本次改动 31 个文件；另有 6 个文件（`memory_service.py`、`usage_service.py`、`core/metrics.py`、`db/migrate.py`、`tests/integration/pg_fixture.py`、`tests/unit/test_metrics.py`）为本次任务开始前既有的未提交改动，非本团队产出，未纳入本次变更范围。

### 7.1 独立验证结论（QA + 主理人复核，2026-10-23）

> 口径说明：下表严格区分「已执行」与「未执行」。凡未实际执行的项，不得记为 PASS。
>
> 另注：架构师最终复核表中「G1 缺 pg 用例 / G2 零测试」两行基于更新前的快照，已过时——当前 `test_tenant_spoofing.py` 含 14 条 memory 用例 + 1 条 pg 用例（`--run-pg` 实跑 15 passed / 0 skip），并已完成「删 early-reject 必红」的反向验证。

| 验证项 | 结果 | 执行者 | 证据 |
| --- | --- | --- | --- |
| V1-1 跨租户非成员 → 20003 | **PASS** | 主理人实跑 | `test_tenant_spoofing.py` 14 passed（含该用例） |
| V1-3 同租户请求 → 200（不误杀） | **PASS** | 主理人实跑 | 同上 |
| V1-4 `off` 开关 → 200 放行 | **PASS** | 主理人实跑 | `test_membership_check_off_skips_validation` 通过 |
| V1-2 / V1-5 PG 下 `__platform__` → 403（非 500） | **PASS（含反向验证）** | 工程师实跑 `--run-pg` | `test_tenant_spoofing.py --run-pg`：**15 passed / 0 skip**；临时删除 early-reject 后 **1 failed（error_code=30003）**，已还原 |
| V5 全量回归 | **PASS（批标记）/ 1 failed（单进程，既有缺陷未修）** | QA + 架构师 + 主理人复跑 | `OA_BATCH=B21` + PG：**1810 passed / 201 skipped / 0 failed**；单进程无批标记：**1809 passed / 1 failed**，唯一红为 `test_endpoints.py::test_a2_list_tasks`。**归因未证实**：架构师称由 security 用例改全局注册表引起，但 `grep registry apps/adapter/tests/security/*.py` 无命中，且 `test_tenant_spoofing + test_endpoints` 单跑 61 passed 无失败；不做猜测性修复，记 **LED-045（W1 定位）**，复现命令：`pytest tests/security tests/unit tests/integration -q` |
| V2 前端 `tsc` / 禁词 / 占位词 | **PASS** | QA 实跑 | 0 error、两项扫描 0 命中 |
| V2 登录页生产分支无绕过入口 / 回跳页不写存储且清理地址栏 / 演示徽标 | **PASS** | 主理人静态复核 + 门禁实跑 | `login/page.tsx` 条件渲染；`callback/page.tsx` 无 localStorage/sessionStorage，`metadata.referrer='no-referrer'`，`CallbackCleanup` 挂载后 `replaceState` 去参数；`plugins/page.tsx` 有 `integration_status === 'demo'` 分支；`tsc` 0 error、两项扫描各 102 文件 0 命中 |
| V3 文档一致性 | **PASS（1 处生成物例外）** | 主理人复核 | 定位残留仅剩历史审计/决策文档 + 构建产物 `apps/adapter/officeai_adapter.egg-info/PKG-INFO`（非源码，重新安装即刷新）；超述词 0；L0/§6.6/AC-17/M1-M8/两条编者注均存在 |
| V4 交付清单 `--check` | **PASS** | 主理人实跑 | 退出码 0 |
| V2「配置 OIDC 后生产构建显示统一登录按钮」 | **未验证** | — | 需完整前端构建；本轮明确不 build，留 W1 与真实 OIDC 联调一并验证 |

---

### 7.2 本轮未解决的问题（进 W1，不阻塞 W0）

| 编号 | 问题 | 依据 | 归属 |
| --- | --- | --- | --- |
| LED-045 | 单进程全量顺序敏感：`test_endpoints.py::test_a2_list_tasks` 在跑过 `tests/security` + `tests/unit` 后失败。**已修复（W1）**：真实根因 = `test_attack_object_scope.py` 夹具向 PRIVATE_TENANT 泄漏 `plugin_id='office_doc'` 任务，列表装配 `_manifest_of`（`task_service.py:1285-1305`）遇缺失 manifest 抛 16002 致整列表 404。修法双轨：产品级（列表路径容忍缺失 manifest，详情/执行路径仍显性 16002）+ 测试卫生（夹具 teardown 清理，未动任何既有断言）+ 回归用例（含「列表容忍/详情失败」双向断言与反向验证） | 复现：`pytest tests/security tests/integration/test_endpoints.py -q`；修后 **83 passed / 2 skipped**，全量 **1867 passed / 0 failed** | 已关闭 |
| — | `stub` + PG 下伪造 `X-Tenant-Id: __platform__` 可能触发 ops 仓储 `allow_platform` 传参缺陷（30003）。stub 非生产形态（staging/prod 已被启动拦截），不在 AR-017 范围 | 架构师 T1 评估 §2.1 | 工程 W1 单独立项 |
| — | `tenant_membership_check=always` 在私有化模式会误判「令牌声明被忽略」为跨租户；启用前需补私有化全员归属口径 | 架构师 T1 评估 | 产品+工程 W1 |
| — | `integration_status` 为 `demo` 时前端隐藏「发起连接」入口——避免"明知是演示还引导连接"，V1.1 真实化后恢复 | 架构师前端复核 §2.3 | 产品 W1 确认 |

---

## 七·补、W1 第一批实施记录（2026-10-23，QA 验证中）

**范围**：任务统计切片（M1/M3/M4/M5）+ 统一任务入口 MVP。依据 `docs/prd-w1-metrics-entry.md` v1.1 与 `docs/architecture-w1-metrics-entry.md`（T01-T05）。

| 任务 | 内容 | 状态 |
| --- | --- | --- |
| T01 | 契约与门禁基础设施：`schemas/stats.py`、`schemas/capability.py`、前端 types/api-client/use-api、`docs/api/baseline-w1-2.json`（2 条）、`validate_openapi.py` 加性改造（G7）、`test_endpoint_baseline.py` +5 用例 | ✅ 35 passed |
| T02 | 切片 A 后端：`task_repo.py` `task_stats` 三处同步（协议/内存/PG，一条聚合 SQL）、`api/v1/stats.py`、`test_stats_tasks.py`（7 用例，含 pg 标记） | ✅ 内存 6 passed + PG 7 passed |
| T03 | 切片 B 后端：`capability_catalog.py`（只读注册表，无 SQL）、`goal_router.py`（2-gram+关键词表，确定性排序）、`api/v1/capability.py`、2 个测试文件（22+21 用例） | ✅ 43 passed |
| T04 | 切片 A 前端：`formatDurationSeconds`、`task-metric-cards.tsx`（4 卡三态+小样本+统计期切换）、workflows 页挂载 | ✅ 门禁全绿 |
| T05 | 切片 B 前端：`goal-launcher.tsx`（debounce/理由回填/所需输入/兜底）、chat 页挂载、workbench `?goal=` 预填、`use-declaration-form.ts` initial | ✅ 门禁全绿 |

**已验证**：全量 `OA_BATCH=B21 pytest tests -q` → **1865 passed / 203 skipped / 0 failed**；`validate_openapi.py` G1-G7 退出 0（`w1_declared=2 w1_present=2`）；`schemas/agent.py` diff 为空（契约红线）；前端 `tsc` + 三项扫描全过（104 文件 0 命中）；PG 口径 `test_stats_tasks.py --run-pg` 7 passed。

**关键设计决策落实**：零迁移、零新依赖、零新错误码（参数非法走既有 20001）；比率取整只在端点层一次；目录实时组装不建第三份真相；M3 按批准口径交付「任务总时长（含人工确认等待）」，不宣称扣除等待。

**偏差记录**（均有理由，见工程回报）：`test_health.py` 同步扣 W1 基线（机械必需）；tokenize 增加功能字过滤（否则「写一个招聘说明」被「一个」顶前沿，AC-B1 假红）；关键词数断言放宽为非空+≤12（设计示例表自身有 3-4 词）。

### 独立验证结论（QA，2026-10-23）

| 验证项 | 结果 | 证据 |
| --- | --- | --- |
| V1 后端端点（自建 TestClient 脚本独立造数，不复用工程夹具） | **30/30 PASS** | 7d/30d 逐字段手工复算一致（total=4/completed=2/failed=1/cancelled=1/rate=66.67/avg=175.0/units=9.0 等）；`range=90d` → 400 + 20001；空租户全 0 无 NaN；未登录 401；推荐命中/易混词不串/空目标返目录/参数边界/无命中兜底/工具卡形态全部符合；**PG 与内存在 QA 自有造数下逐字一致** |
| V2 契约红线 | **4/4 PASS** | `schemas/agent.py` diff 空；零新增依赖；`baseline-w1-2.json` 恰好 2 条且与挂载逐字一致；`task:read` 仅在 stats.py 文档字符串作说明性出现（权限矩阵零引用） |
| V3 门禁 | **全过** | `validate_openapi.py` exit 0（`w1_declared=2 w1_present=2`，四套旧基线计数不变）；`tsc` 0 error；禁词/占位词各 104 文件 0 命中；`scan:pages` 18 页全过；**`next build` 成功**；`scan:contract` 32 接口/169 字段 0 差异；`scan:workbench` G-27 三项红线通过 |
| V4 前端静态审查 | **7/7 PASS** | 四卡标题逐字无简写；小样本文案合规；无 units/token 用户可见文案；无命中引导卡指向 `/plugins`；`?goal=` 用 `useEffect + window.location.search`（未用 `useSearchParams`）；指标区不混入 `/model/usage` |
| V5 回归 | **PASS** | `OA_BATCH=B21 pytest tests -q` → **1867 passed / 203 skipped / 0 failed**（374s，含 LED-045 修复后新增 2 条）；W1 自有测试 84 passed + 1 PG 门控；`task_repo.py` 纯增量 239 行零删除 |
| V5 补：完整 PG 口径（标准 vec 库 `moy-qa-pg-vec:55434`） | **PASS** | `OA_TEST_DATABASE_URL=…55434/officeai OA_BATCH=B21 pytest tests -q --run-pg` → **2070 passed / 0 failed**（563.88s，退出 0）——全部 203 条 PG 用例实跑通过，含 memory 内核 |

**遗留（QA 证实为既有问题，均不阻塞 W1 第一批交付；LED-045 已在本批修复）**：
1. LED-045（高，已修）：测试隔离 + 端点稳健性双轨修复，见 §7.2；修后全量 **1867 passed / 0 failed**，新增回归用例 2 条。
2. QA PG 库环境（中）：`moy-qa-pg:55433` 使用 `postgres:18.6-alpine`，不带 pgvector，与仓库标准镜像（CI/compose 均为 `pgvector/pgvector:pg18`）不一致，导致 memory 内核 PG 用例 setup ERROR。**处置：采用 `moy-qa-pg-vec:55434` 为标准 PG 口径库**（已全基线 0001-0011 + vector 0.8.7，35 用例实测通过），旧库不重建、口径写入文档；容器镜像统一留作环境任务。
3. `scan:capability` 告警（中）：`plg_hr_admin` 多要 `org.directory.read`（phase3 引入，HEAD 同错复现），不在本批门禁清单。

**PG 口径库定版（环境决策）**：标准库 = `moy-qa-pg-vec:55434`（镜像 `pgvector/pgvector:pg18`，与 CI `.github/workflows/ci-adapter.yml` 及 `deploy/docker-compose.yml` 一致；台账 0001-0011 全应用 + vector 0.8.7）。`moy-qa-pg:55433` 使用 `postgres:18.6-alpine`，不提供 pgvector，**不得用于 memory 内核 PG 用例**；该库已补 0010/0011，0009 因缺扩展阻塞，容器镜像统一留作环境任务（不阻塞开发）。口径已写入 `docs/qa-phase3-batch-acceptance-checklist.md` 与 `apps/adapter/tests/qa/README.md`（后者 2026-10-29 审计清理已删除）。

**验证脚本归位**：QA 的两个独立验证脚本移入 `apps/adapter/tests/qa/`（不被 pytest 收集，`ruff extend-exclude` 同步），附 README 说明跑法；内存口径实跑 23 passed。（脚本 `qa_w1_v1_verify.py` / `qa_w1_v1_pg_verify.py` 及目录 2026-10-29 审计清理已删除，W1 结论留存于本决策单；正式回归以 `tests/integration/test_stats_tasks.py`、`tests/integration/test_capability_recommend.py` 为准）

---

## 八、待创始人确认事项

1. **总纲 §7.2 Open WebUI 脚注**：本 ADR 以编者注形式标注「评估过、未采用」，请确认是否转为正式修订。
2. **金标集通过率阈值**（AC-17）：本 ADR 不编造数字，待拍板后回填。
3. **W1 资源投入**：指标埋点 + 统一入口 + 连接器真实化合计约 2-3 个批次，需确认优先级排序。
4. **对外材料更正**：若已有对外发布/销售材料使用「企业办公 AI 平台」定位，需同步更正。

---

*本 ADR 依据总纲与代码事实作出；所有结论可在仓库中复核。决策执行情况见 W0 交付记录。*
