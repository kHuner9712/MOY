# Moy（麦子）代码仓库 · 总纲一致性全面审计报告

| 项 | 内容 |
| --- | --- |
| 报告编号 | MOY-AUDIT-2026-10-24 |
| 审计日期 | 2026-10-24 |
| 审计基准 | 《Moy（麦子）项目总纲》V1.0（`docs/moy-project-charter-v1.0.md`）—— 唯一权威对照标准 |
| 代码基线 | `main@51446c2` + **工作区未提交增量**（42 个修改文件、约 40 个新增文件/目录，属 W1「总纲对齐」进行中） |
| 审计类型 | 只读审计：未修改任何业务代码、测试或配置 |
| 分工 | 许清楚（产品一致性）、高见远（架构/代码质量/安全）、严过关（测试/CI/交付治理）、主理人齐活林（独立抽验 + 汇编） |
| 审计结论 | **有条件通过（不具备商用交付条件）** |

---

## 一、执行摘要

### 1.1 总体判断

> **工程底盘依然扎实，但「广度」和「被验证的交付」两处没有跟上总纲。**
>
> 静态质量（ruff / mypy / 297 文件格式 / 9 个部署校验器）实测全绿且门禁可红；测试规模实测 **1867 passed / 203 skipped / 0 failed**，但**覆盖率仅 83.52%，低于自家 85% 门禁**。本轮另发现 **1 项已实测可利用的跨租户越权**（agent 任务域，绕过了上一轮修好的 AR-017），**G-13 错误码门禁当前实测为红**（工作区不可合并），**前端零自动化测试**，**203 条 PG 用例与覆盖率门禁从未被真实执行**，**备份恢复/离线包不闭环**。产品侧「统一任务入口」已从 0 到 1，但权限提示、成果采纳率、个人用户入口三项总纲硬要求仍缺。

### 1.2 问题总览

| 模块 | 条目 | 高 | 中 | 低 |
| --- | --- | --- | --- | --- |
| M1 安全与权限 | 13 | 3 | 8 | 2 |
| M2 架构与分层 | 7 | 0 | 5 | 2 |
| M3 能力体系 | 5 | 2 | 3 | 0 |
| M4 产品与体验 | 9 | 3 | 5 | 1 |
| M5 商业与指标 | 2 | 0 | 2 | 0 |
| M6 质量与交付 | 15 | 6 | 6 | 3 |
| M7 部署运维 | 3 | 0 | 1 | 2 |
| M8 文档一致性 | 3 | 0 | 1 | 2 |
| **合计** | **57** | **14** | **31** | **12** |

### 1.3 最关键的 6 个问题

| # | 问题 | 严重度 | 性质 | 一句话 |
| --- | --- | --- | --- | --- |
| 1 | **SEC-01 Agent 任务跨租户越权** | 高·安全 | 缺陷（已实测复现） | `agent_service._tasks` 是进程级 `dict[task_id]`，`get_task()` 无 auth、`list_tasks()` 直接 `del auth`；持 B 租户令牌可读 A 租户任务详情，并在列表中看到 A 的任务 |
| 2 | **QAL-01 门禁当前为红** | 高·治理 | 违规 | `check_error_catalog.py --strict` 实测 exit 1：前端兜底表缺 18001/18002/18003，按自家 CI 规则工作区不可合并 |
| 3 | **CAP-02 生产登录无法完成** | 高·功能 | 缺口 | 已删除「点一下就进」的绕过入口（进步），但 OIDC 只做了跳转，回调页明确不兑换授权码 → 生产环境无人能登录 |
| 4 | **CAP-01 五连接器全桩** | 高·功能 | 缺口 | `IMPLEMENTATION_MODE="demo"`，5/5 不发起真实第三方调用；推荐卡无「演示」标识 |
| 5 | **PRD-01/02/03 总纲三条硬要求未落地** | 高·产品 | 缺失 | 能力契约无 `permissions` 字段（§4.1/§4.5）；无成果采纳埋点（§4.4/§十四）；个人与独立开发者零入口（§5.1/5.2） |
| 6 | **QAL-04 关键验证不达标** | 高·治理 | 未验证 | CI 从未真实运行；203 条 PG 用例本地被 skip；**实测覆盖率 83.52%，低于 CI 门禁 85%**；README 的 90.78% 无产物可复现 |

---

## 二、审计方法与可复现证据

### 2.1 亲自运行的命令（本机实测）

| 命令 | 退出码 | 结果 | 与文档声称 |
| --- | --- | --- | --- |
| `ruff check .` / `ruff format --check .` | 0 / 0 | 全绿；297 文件已格式化 | 一致 |
| `mypy app` | 0 | Success: no issues found in **150 source files** | 一致 |
| `pytest --collect-only -q` | 0 | **2070** 用例被收集 | 文档 1573+141=1714，不一致 |
| `OA_BATCH=B21 pytest tests -q --no-cov` | 0 | **1867 passed / 203 skipped / 0 failed**（372s） | README「1456 passed / 1 failed / 141 skipped」，不一致 |
| `coverage report`（同一批数据） | 0 | **83.52%**（17651 语句 / 2581 未覆盖；分支 3640 / 427 未覆盖） | ❌ 低于 CI 门禁 85%；README 称 90.78% |
| `python scripts/check_error_catalog.py --strict` | **1** | 缺 18001/18002/18003 | 文档称全绿，**实测红** |
| `python deploy/validate_openapi.py` | 0 | core=30 / phase2=38 / phase3=16 / phase4=10 / w1=2 = **96** | README「84」，不一致 |
| `app.openapi()` 实测 | — | 81 paths / 99 operations（其中 96 个在 `/api/v1`） | 同上 |
| 9 个 `validate_*.py` | 全 0 | 全绿 | 一致 |
| 5 个校验器 `--self-test` | 全 1 | 负样本全部命中（门禁确实可红） | 一致 |
| `npm run typecheck` / `lint` / 7 个前端 scan | 0 | 零错 | 一致 |
| TestClient 冒烟 | — | `/healthz` `/readyz` 200；伪造令牌 401/20002，无堆栈外泄 | — |
| **跨租户越权复现** | — | A 建任务 → B **200 读到详情**；B 列表 `total=1` | — |

### 2.2 明确未验证的范围（不得据此判定通过）

- Docker 全栈未启动：15 容器实跑、备份/恢复实跑、离线安装均未执行
- 真实第三方：5 个连接器、微信支付商户、真实模型密钥均无联调
- 覆盖率口径为「无 PG」子集，**不是** README 声称的 PG 全量口径；该口径已低于 85% 门禁
- 身份服务侧：`tenant_id` / `roles` 声明是否可由用户控制未验证（影响 SEC-02/03 定性）

> 说明：QA 执行全量 pytest 时生成 `apps/adapter/pytest_run.log`（未纳入版本控制），不影响仓库内容。

---

## 三、总纲条款 → 实现状态对照

| 总纲章节 | 要求（摘要） | 判定 | 关键证据 | 对应问题 |
| --- | --- | --- | --- | --- |
| 一/§1.2 定位 | 面向广泛工作需求，不预先限定 | **符合（文档层）** | `prd-v1.0.md:30-45`、`plan-v1.0.md:26`、`README.md:3-5` | — |
| §4.1 统一任务入口 | 围绕目标发起；提示输入与权限 | **部分** | `goal-launcher.tsx` 已上线；无 permissions 字段 | PRD-01/04 |
| §4.2 可扩展能力体系 | 模型/工作流/插件/连接器/知识/流程 | **部分** | 五类 L2；连接器 5/5 桩 | CAP-01/03 |
| §4.3 任务执行与流程 | 计划/编排/状态/失败/重试/确认 | **部分（L2）** | `task_service` 状态机；平台侧无计划生成 | ARC-04、PRD-04 |
| §4.4 结果交付 | 形态/格式/可追溯/可修改 | **部分** | 产物+TTL 令牌+编辑；无采纳度量 | PRD-02 |
| §4.5 用户控制 | 高影响操作授权/确认/审计 | **部分** | 授权清单+两步确认+审计；入口不显示所需权限 | PRD-01、SEC-05 |
| 五、用户覆盖 | 个人/开发者/员工/管理者/企业 | **不均衡** | 企业侧覆盖好；5.1/5.2 零入口 | PRD-03 |
| 六、广泛覆盖战略 | 不预设单一市场 | **符合（文档层）/ 偏离（产品面）** | 定位已改；侧边栏仍 9 个企业向模块 | PRD-03/04 |
| 7.4 模块化可替换 | 换模型/向量库/引擎上层解耦 | **部分** | 出口收口；协议常量散落 4 个 service | ARC-01/07 |
| 7.5 插件生态 | 接入标准/验证/淘汰 | **部分** | 声明+校验+评审；无淘汰流程 | CAP-03/04 |
| 九、商业模式 | 订阅/按量/团队/私有化 | **代码存在，商业未验证** | 四线端点页面齐；支付验签方案错误 | BIZ-01、SEC-10 |
| 10.1 权限与隔离 | 多租户、不得只依赖可改参数 | **有致命缺口** | RLS 双保险优秀；agent 域无租户维度 | **SEC-01** |
| 10.2 外部操作控制 | 写入/发布/删除的授权确认 | **部分** | 确认参数+审计；连接器为桩 | CAP-01、SEC-09 |
| 10.3 失败与可追溯 | 状态/错误/重试/幂等/审计/恢复 | **优秀（单副本）** | trace_id 全链路、审计只增；幂等默认内存 | SEC-11 |
| 10.4 隐私与数据 | 用途/权限/保留/外部模型告知 | **部分** | TD1/TD2 导出删除；无隐私政策/DPA | PRD-07 |
| 10.5 质量验证 | 测试/评估/兼容/人工审核 | **部分** | 2070 用例、9 校验器；**覆盖率实测 83.52% < 85%** | QAL-04/14 |
| §十一 状态分级诚实 | 不得把设想写成事实 | **基本遵守，数字层失真** | 连接器自述 demo；README 数字三套口径 | QAL-03、DOC-01 |
| §十四 核心指标 | 8 项 | **2 项落地 + 2 项部分** | M1/M4 真数据；M2 零落地 | PRD-02/06 |

---

## 四、按模块问题清单

> 严重度：**高** = 动摇总纲定位 / 阻塞可信交付 / 可造成数据越权；**中** = 影响完整性、可扩展性或商业化；**低** = 体验、文档与局部技术债。
> 状态：**确认** = 代码级证据充分（含实测）；**待验证** = 依赖部署架构或外部条件。

### M1 安全与权限（最高优先）

**总纲要求（10.1/10.2/10.4）→ 实现**：RLS 双保险与对象级判权在插件/知识/会话域实现良好；**agent 任务域完全没有租户维度**，构成隔离链断点。

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| **SEC-01** | **Agent 任务跨租户越权（已实测）**：`_tasks` 为进程级 `dict[str, TaskDetail]`，仅按 `task_id` 索引；`get_task(task_id)` 无 `auth` 参数；`list_tasks()` `del auth` 后遍历全量。持 B 租户令牌可读 A 任务详情，列表可见 A 任务。兼有 `_tasks` 无清理的内存增长 | `services/agent_service.py:163,288,310-336`；`api/v1/agent.py:223-249`；实测：A 建任务 → B GET 200、B list total=1 | **高** | 确认 |
| **SEC-02** | **平台哨兵仅凭令牌声明**：令牌 `tenant_id=__platform__` 即获平台视角（`_has_cross_tenant_privilege` / `is_platform_scope` 均不要求 `*` 角色），RLS 对 `__platform__` 全量可见 | `core/security.py:455-458,764-768,588-602`；`deploy/init-schema-phase2.sql:649-654` | **高（条件）** | 待验证：取决于身份服务是否允许用户控制 `tenant_id` 声明 |
| **SEC-03** | **「权限点即角色」兼容口**：任何含 `:` 的角色串直接等价为该权限点，绕过角色矩阵；与 SEC-02 叠加可拿 `ops:console:access` | `business/policy_service.py:160-171`（`:168-170`） | 中高 | 确认 |
| SEC-04 | 成员校验回退开关无环境门禁：`tenant_membership_check=off` 在生产同样生效且仅 WARNING，不拦启动；`auto` 在 `saas + stub` 组合下不校验 | `core/security.py:532-543`；`core/config.py:87` | 中 | 确认（stub 已被启动防呆挡住，窗口限于非 staging/production） |
| SEC-05 | 占位密钥无启动拦截：只校验非空，`.env.example` 的 `CHANGE_ME_STRONG_PASSWORD` 可原样上生产；`jwt_secret` 同时是产物下载令牌 HMAC 密钥 | `core/security.py:867-875`；`deploy/.env.example:151`；`core/artifact_token.py:82-87` | 中 | 确认 |
| SEC-06 | 产物下载令牌为纯 bearer（无登录态/二次授权），HMAC 截断 64bit；链接转发即可外泄 Offer/简历类高敏产物 | `core/artifact_token.py:36,129,154`；`api/v1/artifact_files.py:41-92` | 中 | 确认（TTL 900s 已修，残余风险下降） |
| SEC-07 | **CSV 公式注入**：`render_csv` 原样写单元格，`=`/`+`/`-`/`@` 开头内容在 Excel 打开即执行 | `plugins/renderer.py:354-355` | 中 | 确认 |
| SEC-08 | 插件任务并发配额非原子：先 `count` 再 `insert`，无锁无唯一约束 → 并发可突破并发上限、`queue_position` 重号 | `business/task_service.py:279-300` | 中 | 确认 |
| SEC-09 | 连接器真实化后**无 SSRF 出站守卫**：`tools/base.py` 无域名白名单/私网 IP 拒绝；`oa-ssrf-proxy` 只护 Dify 出口 | `app/tools/base.py`（无 ipaddress/allowlist 命中）；`deploy/docker-compose.yml:367` | 中（窗口开启） | 确认 |
| **SEC-10** | **支付回调验签方案与真实渠道不符**：自研 HMAC-SHA256 四字段（`out_trade_no/transaction_id/amount/notify_id`），非微信 V3 平台证书 RSA-SHA256；无 `timestamp/nonce` 重放窗口。真实回调必然验签失败；持 APIv3 密钥者可伪造成功回调开通套餐 | `business/payment_gateway.py:106-120,279-292,232-254` | **高（商业+安全）** | 确认（`order_service.py:463-600` 的负路径顺序设计正确） |
| SEC-11 | 限流/幂等后端硬编码内存：`RedisRateLimitStore` 为死代码、`Settings` 无 `ratelimit_backend`；`idempotency_backend` 默认 `memory` → 多副本时真实配额 = 配置 × 副本数，重启后幂等键失效 | `core/ratelimit.py:151`；`core/config.py:329` | 中 | 确认（单副本可降为低） |
| SEC-12 | 访问令牌存 `localStorage`（仅开发构建可见入口） | `apps/web/src/app/(auth)/login/page.tsx:131` | 低 | 确认 |
| SEC-13 | `GET /stats/tasks` 仅需登录即可读全租户聚合（任务量/活跃人数），无权限点 | `api/v1/stats.py:110-131` | 低 | 待验证（业务口径需产品确认） |

### M2 架构与分层

**总纲要求（7.4/7.6）→ 实现**：底座出口唯一、错误面不泄内部、插件声明收敛——方向正确；但分层存在**双向环**，换底座仍需改 4 个 service。

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| ARC-01 | 底座协议路径常量散落 **4** 个 service（较上轮 3 处恶化）：`/v1/chat-messages`、`/v1/workflows`、3 条 datasets、`/v1/completion-messages` | `services/model_service.py:79`、`agent_service.py:50`、`rag_service.py:67-69`、`memory_extractor.py:53` | 中 | 确认 |
| ARC-02 | `business → api` 反向依赖（组合根错位） | `business/task_service.py:188` 延迟 import `build_action_gateway` | 中 | 确认 |
| ARC-03 | `services → db/repositories` 直连，且 `business ↔ services` 双向环 | `tool_service.py:33`、`model_service.py:48`、`memory_extractor.py:39`、`identity_service.py:415`；`memory_service.py:60-65` | 中 | 确认 |
| ARC-04 | 上帝文件且继续增长：`billing_repo` 2272、`task_service` 1622、`model_service` 1585、`task_repo` 1549、`plugin_repo` 1529、`engine` 1517（单类 1500 行，护栏/扫描/重试混居） | 各文件头 | 中 | 确认 |
| ARC-05 | 引擎护栏按 `output_key` 硬编码 offer/schedule/screening 三域，非可注册策略 | `plugins/engine.py:738-746` | 低 | 确认 |
| ARC-06 | 新增工具仍需改 3 处（YAML + connector + `_CONNECTOR_MODULES`），与设计意图「数据驱动」不符 | `services/tool_service.py:77-83,140` | 低 | 确认 |
| ARC-07 | 可替换性无任何真实替换演练证据（无「换模型 provider / 换检索后端」灰度记录） | `clients/dify_client.py` | 中 | 确认 |

### M3 能力体系

**总纲要求（4.2/7.5）→ 实现**：插件声明与运行时成熟（8 层校验、6 能力封闭集合、6 份官方声明）；**连接器与登录两项对外能力不可用**。

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| **CAP-01** | 5 个工具连接器全部 demo（不发起真实调用），`params["simulate"]` 是唯一异常注入入口 | `tools/email_connector.py:6`、`github_connector.py:6`、`wecom_connector.py:13`、`feishu_connector.py:13`、`wechat_mp_connector.py:13`；`tools/base.py:73` | **高** | 确认 |
| **CAP-02** | 生产登录不可完成：已移除绕过入口（进步），但 authorize 无 PKCE、`/auth/callback` 明确不兑换授权码 | `login/page.tsx:56,63-70`；`auth/callback/page.tsx:9-22` | **高** | 确认 |
| CAP-03 | 插件能力封闭在 6 能力 × 6 原语 × 1 UI 宿主；V1.0 仅官方 6 份，无第三方/社区轨道 | `plugins/schema.py`；`plugins/manifests/` | 中 | 确认 |
| CAP-04 | 有安装/授权/评审，**无升级-替换-淘汰治理**（下架/吊销/迁移指引缺失） | `business/plugin_service.py` | 中 | 确认 |
| CAP-05 | 模型档位路由已接线，但真实多模型矩阵与密钥接入无证据（`qa_probe/mock_llm.py` 为模拟） | `services/profile_routing.py`、`qa_probe/mock_llm.py` | 中 | 确认 |

### M4 产品与体验

**总纲要求（4.1/4.4/4.5/五/十四）→ 实现**：定位文档已对齐总纲（真实修订，非改标题）；**产品面仍以企业办公为主**，三项硬要求缺。

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| **PRD-01** | **§4.1「提示所需权限」未实现**：能力目录契约只有 `inputs`，无 `permissions`；前端 0 命中「所需权限」。一处改动同时影响 §4.1 与 §4.5 | `schemas/capability.py:26-40`；`components/chat/goal-launcher.tsx:78-107` | **高** | 确认 |
| **PRD-02** | **成果采纳率（M2）零落地**：无 `artifact_adoption` 迁移、无埋点、无端点；总纲 §4.4 的核心判断标准无法度量 | `deploy/migrations/` 仅到 0011；全仓 `grep artifact_adoption` = 0 | **高** | 确认 |
| **PRD-03** | 个人（5.1）与独立开发者（5.2）无可用入口：R0 仅提案并自注「V1.0 无专属入口」；前端 0 命中「个人用户/独立开发者」 | `prd-v1.0.md:88-93`；`prd-v1.1-r0-scenario-pack-proposal.md:43` | **高** | 确认 |
| PRD-04 | 统一入口只做了对话页顶部一个搜索框；侧边栏仍 9 个并列模块，新用户仍需自学结构；缺少平台侧「计划生成与编排」 | `components/layout/sidebar.tsx:41-49`；`chat/page.tsx:83` | 中 | 确认 |
| PRD-05 | 工具推荐卡标为「办公工具」但**不带「演示」徽标**（W1 明确决策不带），用户会把 demo 当可用能力 | `goal-launcher.tsx:101`；`capability_catalog.py:13-15`；`prd-w1-metrics-entry.md:6` | 中 | 确认 |
| PRD-06 | 运营看板未接入任何核心指标：`/ops/metrics` 仍 6 张用量卡（企业数/活跃/对话/文档/用量） | `app/(main)/ops/metrics/page.tsx:45-58` | 中 | 确认 |
| PRD-07 | 隐私政策/DPA/安全白皮书缺失（D-22 已拍板列入 V1.0 但未执行） | `docs/compliance/` 不存在 | 中 | 确认 |
| PRD-08 | 能力承接广度不足：目录仅 6 插件 + 5 工具，示例 3 条全办公向（周报/邮件/纪要） | `goal_router.py:49-61`；`goal-launcher.tsx:39` | 中 | 确认 |
| PRD-09 | 目标推荐为规则 2-gram + 11 条关键词，无语义；同义改写与长句命中率有限（接口已预留） | `goal_router.py:1-24,133-187` | 低 | 确认 |

### M5 商业与指标

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| BIZ-01 | 支付无真实商户联调证据（网关有装配路径与 Fake 开关，无流水记录）；叠加 SEC-10，当前不具备「可付费交付」条件 | `business/payment_gateway.py`；`docs/qa-*`/`phase2-acceptance-report.md` 无联调记录 | 中 | 确认 |
| BIZ-02 | 用量以「字数折算 units」计量，非 token/真实成本；M5 只到「单位成功用量」，单位成功任务**成本**仍不可核算 | `services/usage_service.py`；`stats.py:62-66` | 中 | 确认 |

### M6 质量与交付治理

**总纲要求（10.5/十一）→ 实现**：静态门禁与测试设计优秀、可红证明完备；**执行证据链断裂**，且当前工作区已违规。

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| **QAL-01** | **G-13 门禁当前为红**：`check_error_catalog.py --strict` exit 1，前端兜底表缺 18001/18002/18003（会话与记忆域）。按 ci-adapter 规则该工作区不可合并 | 实测 exit 1；`apps/adapter/app/core/errors.py:157-161`；`apps/web/src/lib/business-errors.ts` 无对应项 | **高** | 确认 |
| **QAL-02** | **前端零自动化测试**：无 test 脚本、无 vitest/jest/playwright、0 个 `*.test.*`；ci-web 只做 lint/tsc/build/文案扫描 | `apps/web/package.json`（scripts 无 test） | **高** | 确认 |
| **QAL-04** | **关键验证不达标**：CI 从未真实运行；203 条 PG 用例（含 RLS 隔离、跨租户）本地全 skip；**实测覆盖率 83.52%，低于 85% 门禁** | `README.md:232`；实测 skipped=203；`coverage report` TOTAL 83.52% | **高** | 确认 |
| **QAL-14** | **覆盖率首次实测即不达标**：无 PG 口径 83.52%（17651 语句，2581 未覆盖），而 README 称 90.78%。差异主要来自 203 条跳过用例，说明「达标」依赖 PG 全量口径，但该口径无任何可复现产物 | `coverage report` 输出；`README.md:24` | **高** | 确认 |
| **QAL-15** | 模块级覆盖严重失衡：`db` 71.70%、`workers` 60.61%（`retention_cleanup.py` 31.86%、`queue_depth.py` 52.38%）；恰是数据隔离与后台任务两条高风险链路 | `coverage report` 分模块输出 | 中 | 确认 |
| **QAL-08** | **备份/恢复无实跑证据**；`upgrade.sh`/`rollback.sh` 未实现；CI 无 backup/restore 步骤 | `docs/phase4-batch-log.md:798`「restore 未实跑」、`:800-805` 未完成清单 | **高** | 确认 |
| **QAL-09** | **离线包不完整**：`deploy/offline/` 仅 README + 1 个 `.difypkg`，无 `images/*.tar`、无 `checksums.sha256`，而 `install.sh:569-587` 硬依赖这两样 | `deploy/offline/`；`install.sh:569-587` | **高** | 确认 |
| QAL-03 | 测试与覆盖率三套口径且全部过期：README 1573/90.78%、CI 注释 1597/89.81%、coverage.xml 89.09%、实测 1867/203 | `README.md:24,215-217`；`ci-adapter.yml:241` | 中 | 确认 |
| QAL-05 | **改 `deploy/migrations/*.sql` 的 PR 不触发迁移门禁**：ci-adapter paths 仅 `apps/adapter/**`；dependency-scan 虽监听 `deploy/**` 但不跑 `validate_migrations.py` | `.github/workflows/ci-adapter.yml:13-19`；`dependency-scan.yml:16-19` | 中 | 确认 |
| QAL-06 | G-30 能力一致性是 warn-only（永不失败），与「可红证明」并列易被误读为已严格化 | `.github/workflows/ci-web.yml:209` | 中 | 确认 |
| QAL-07 | 迁移回滚只覆盖 6/10：`rollback/` 仅 0006-0011，0002-0005 无 down，且校验器不检查 down 覆盖 | `deploy/migrations/rollback/` | 中 | 确认 |
| QAL-10 | 台账可「洗标记」：`EXPECTED_XFAIL_BY_BATCH={}` 使按批归零断言空转；`OPEN_CASES_HISTORY` 注释与 docstring 矛盾 | `tests/expected_xfail.py:17,28,43` | 低 | 确认 |
| QAL-11 | `tests/qa/` 验证脚本永不自动执行（文件名不带 `test_`、不在 CI） | `tests/qa/README.md` | 低 | 确认 |
| QAL-12 | 覆盖率门槛文档不一致：adapter README 80% vs CI 85% | `apps/adapter/README.md:166`；`ci-adapter.yml:254` | 低 | 确认 |
| QAL-13 | 交付基线不干净：42 改 + ~40 新增未提交，门禁从未整体评估该工作区 | `git status` | 中 | 确认 |

### M7 部署与运维

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| OPS-01 | 端口默认绑 `0.0.0.0` 且无 `host_ip`：milvus 19530/9091、对象存储 8333/23646（含 admin 口）、adapter 8000、web 3000、prometheus 9090 | `deploy/docker-compose.yml:181-183,209-211,493,515,574`；`docker-compose.saas.yml:152-171,386,403` | 中 | 确认 |
| OPS-02 | 资源限制只在 lite 版（10 处）；full/saas **0 处** | `grep "resources:" deploy/*.yml` | 低 | 确认 |
| OPS-03 | 仓库根残留空目录 `deploy;D`（Windows 重定向误产物，未跟踪） | 仓库根 | 低 | 确认 |

### M8 文档与事实一致性

| ID | 问题 | 证据 | 严重度 | 状态 |
| --- | --- | --- | --- | --- |
| DOC-01 | 端点数量三处矛盾：README 84 / 校验器 96 / 代码常量 30+38+**14**（该常量零测试引用，是死常量） | `README.md:16,40,214`；`deploy/validate_openapi.py`；`api/v1/router.py:74` | 中 | 确认 |
| DOC-02 | `main` 已提交的 README 仍写「Next.js 14」，实际 `next ^16.4.2`（工作区已改但未提交） | `git show HEAD:README.md:53` vs `apps/web/package.json` | 低 | 确认 |
| DOC-03 | 交付清单未披露「备份恢复未实跑」「离线包缺镜像」两项缺口 | `docs/phase4-delivery-manifest.md` | 低 | 确认 |

---

## 五、成熟度分级总表（总纲第十一章口径）

> 规则：无证据最高 L1；有桩/mock 最高 L1；有自动化测试但外部依赖为桩最高 L2；有真实联调证据才可 L3；无真实用户最高 L4。

| 能力域 | 成熟度 | 依据 |
| --- | --- | --- |
| 统一任务入口（目标→能力推荐） | **L2** | 规则打分纯函数 + 18 条单测；无权限提示、无语义、无编排 |
| 任务执行与流程管理 | **L2-L3** | 状态机/失败/重试/人工确认有测试；无平台侧计划编排 |
| 结果交付 | **L2** | 形态/格式/修改/追溯齐；采纳度量缺 |
| 企业知识库/检索 | **L2** | CRUD/引用/授权完整；无金标验证 |
| 模型档位路由 | **L2（路由）/ L1（矩阵）** | 4 档接线；真实密钥矩阵未验证 |
| 插件体系 | **L2（官方）/ L1（生态）** | 6 声明可装可跑；无淘汰治理 |
| **跨生态连接器** | **L1（全桩）** | 5/5 demo，无真实调用 |
| **身份与登录** | **L1-L2** | 生产入口唯一但无法完成登录 |
| 计费订阅/订单 | **L1-L2** | 端点页面齐；支付验签方案错误、无联调 |
| 多租户隔离（RLS） | **L2** | 双保险 + 自检优秀；**agent 域缺口见 SEC-01** |
| 操作审计 | **L2** | 只增审计 + 不可篡改测试 |
| 隐私/数据权利 | **L2（机制）/ L0（文档）** | TD1/TD2 有；隐私政策/DPA 无 |
| 私有化部署 | **L2** | install/lite/offline 骨架；离线包缺镜像、恢复未实跑 |
| 监控告警 | **L2** | `/metrics` + 5 条告警 + 自证 |
| **核心衡量指标** | **L1-L2** | 4/8 有数据链路（M1/M3/M4/M5），0 项进运营看板 |
| 技术黑盒化 | **L3（门禁级）** | 前端底座字样 0 命中；错误码内部码零外泄 |
| **商业验证** | **L0-L1** | 无真实客户与付费数据 |

---

## 六、优先级排序的改进建议

### P0 —— 阻塞可信交付（建议本周启动，共 6 项）

| # | 行动项 | 归属 | 工作量 | 验收判据 | 对应 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | **修 agent 任务越权**：`AgentService.get_task/list_steps/cancel/retry` 增加 `AuthContext`；`_tasks` 改 `dict[(tenant_id, task_id)]` 或复用 `TaskService._assert_task_visible`；`list_tasks` 删 `del auth`；补「B 租户读 A 任务 → 20003/404」用例 | 工程 | M | 双租户实测：B 读 A 返回业务化拒绝；B 列表不含 A；新增 6 条安全用例 | SEC-01 |
| P0-2 | **关闭 AR-017 绕行路径**：`__platform__` 必须同时具备 `*` 角色；`permissions_for_roles` 的 `":" in role` 改显式白名单；`tenant_membership_check=off` 加环境门禁（仅 development/test 或显式降级开关） | 工程 | M | 伪造 `tenant_id=__platform__` 无 `*` → 20003；非 dev 环境无法 `off` | SEC-02/03/04 |
| P0-3 | **让门禁回到绿并跑通一次真实 CI**：补 `business-errors.ts` 三码；GitHub Actions 跑一次含 PG 的全链路；把结果（junit/coverage）作为交付物 | 工程+QA | M | G-13 exit 0；CI 绿；203 条 PG 用例非 skip 且总覆盖率 ≥85% | QAL-01/04/14 |
| P0-4 | **支付验签改为真实方案**：微信 V3 平台证书 RSA-SHA256 + `timestamp/nonce` 重放窗口；`pay_channel=wechat_pay` 缺证书/序列号时启动硬失败 | 工程 | M | 沙箱回调验签通过；错误签名/重放被拒；有流水记录 | SEC-10 |
| P0-5 | **前端最小测试基线**：引入 vitest + Testing Library，覆盖登录跳转、目标推荐、任务三态、错误重试四条主路径 | 工程 | M | `npm test` 可跑；4 条路径有断言；CI 接入 | QAL-02 |
| P0-6 | **交付链闭环**：备份/恢复实跑一次并留证；补 `upgrade.sh`/`rollback.sh`；离线包补齐镜像 tar + checksums；补 0002-0005 down | 工程+QA | L | 恢复演练记录；离线安装一次成功；回滚覆盖 10/10 | QAL-08/09/07 |

### P1 —— 补齐总纲能力完整性（下一迭代，共 10 项）

| # | 行动项 | 对应 |
| --- | --- | --- |
| P1-1 | 能力契约补 `permissions[]`（取插件 `required_capabilities`），推荐卡与工作台起单前展示「这件事会动什么」 | PRD-01 |
| P1-2 | 落地 M2 成果采纳率：`0012_artifact_adoption` + 三动作埋点（直接采用/修改后采用/弃用）+ 看板卡 | PRD-02 |
| P1-3 | 个人与独立开发者最小场景包（文档模板 5 个，零新模块），作为 §5.1/5.2 的首个可验证落点 | PRD-03 |
| P1-4 | 至少 1-2 款连接器真实化（建议邮箱 + GitHub），其余 UI 明示「演示模式」；连接器出站加域名白名单/私网拒绝 | CAP-01、PRD-05、SEC-09 |
| P1-5 | 完成 OIDC：PKCE + 授权码兑换 + 刷新 + 令牌改 httpOnly Cookie | CAP-02、SEC-12 |
| P1-6 | 核心指标进运营看板（M1/M2/M6），卡片带统计期、样本量与口径版本 | PRD-06 |
| P1-7 | 底座协议常量收拢到 `clients/dify_client.py`（`chat()`/`run_workflow()`/`datasets()`）；厂商标记解析可插拔 | ARC-01 |
| P1-8 | 分层收口：`build_action_gateway` 移到 bootstrap；services 只依赖 business 抽象；CI 加 import 方向门禁 | ARC-02/03 |
| P1-9 | 限流/幂等后端可配，生产默认 Valkey；多副本启动对 memory 后端告警 | SEC-11 |
| P1-10 | 合规三件套：隐私政策、DPA、数据出境说明；离线包 SCA 扫描 `.difypkg` 内 36 依赖 | PRD-07 |

### P2 —— 技术债与体验增强（共 8 项）

| # | 行动项 | 对应 |
| --- | --- | --- |
| P2-1 | 上帝文件拆分（单文件 ≤800 行，先拆 `task_service` / `engine`） | ARC-04 |
| P2-2 | 插件并发配额用 `pg_advisory_xact_lock(tenant_id, plugin_id)` 包住 count+insert | SEC-08 |
| P2-3 | CSV 渲染对 `= + - @` 前缀做转义（防公式注入） | SEC-07 |
| P2-4 | 密钥启动检测：拦截 `CHANGE_ME*`、低熵、长度不足；产物令牌与 JWT 密钥分离 | SEC-05/06 |
| P2-5 | 迁移路径纳入 CI 触发；`validate_migrations` 增加 down 覆盖检查；能力一致性从 warn-only 转硬门禁 | QAL-05/06 |
| P2-6 | 端口绑定收敛到 `127.0.0.1` 或显式 `host_ip`；full/saas 补资源限制 | OPS-01/02 |
| P2-7 | 文档数字单一事实源：端点/测试数/覆盖率由脚本生成，禁止手写；修 `router.py:74` 死常量 | DOC-01、QAL-03 |
| P2-8 | 清理 `deploy;D`、`init-rls.sql` 孤儿文件、`slowapi` 死依赖、`usage_service` 重复块 | OPS-03、ARC-06 |

---

## 七、强项（应保留并推广）

1. **AR-017 修复质量高**：成员判定复用业务权威口径（`department_member ∪ member_role`）、平台头在查库前拒绝（避免 500 变体）、同租户零额外查询、三档开关语义清晰，14 条回归用例含 PG 变体。
2. **RLS 双保险真实到位**：21 张严格表 `ENABLE + FORCE`，`officeai_app` 强制 `NOSUPERUSER NOBYPASSRLS`，`/readyz` 带 `db_privilege` 硬门禁。
3. **错误面不泄内部**：中间件只输出业务 message，`internal_detail` 仅进日志；前端按 `code` 而非 HTTP status 分流，402 额度场景不误判。
4. **门禁「必须能红」可机械证明**：5 个校验器 `--self-test` 实测全部 exit 1 且命中负样本。
5. **静态质量零妥协**：ruff/mypy/format 全绿（150 文件），前端 tsc/eslint 零告警。
6. **测试有效性扎实**：抽查测试 mock 数 0，断言密度 20-50 条/文件；`test_tenant_spoofing.py` 把「off 档确实放过」写成显式断言，安全降级可见。
7. **技术黑盒化到位**：前端域名/底座字样 0 命中，内部错误码 17002 零外泄。
8. **W1 指标实现克制而正确**：只做工作流任务域、双后端一致、空数据全 0、小样本提示、取整点唯一。

---

## 八、假设与不确定性

| # | 假设 | 影响 | 验证方式 |
| --- | --- | --- | --- |
| 1 | SEC-02/03 可利用性取决于身份服务是否允许用户控制 `tenant_id` / `roles` 声明 | 决定「条件高危」是否升为「确认高危」 | 查 Keycloak mapper 与客户端 scope 配置 |
| 2 | SEC-01 已实测，但仅在 `AuthMode` 可签发合法令牌的前提下 | 已确认；生产 `auth_mode=oidc` 同样成立（不依赖 stub） | 已在测试环境用合法 JWT 复现 |
| 3 | 连接器桩判定基于源码自述 `IMPLEMENTATION_MODE="demo"` 且无出站 HTTP | 若存在未合入分支的真实实现需复核 | 对照分支 |
| 4 | 覆盖率已完成「无 PG」子集实测（83.52%）；README 的 PG 全量口径（90.78%）仍未复现 | 无 PG 口径已确认不达标；PG 口径是否达标未知 | 跑一次含 PG 的完整 `--cov` 并提交 junit/coverage 产物 |
| 5 | 备份恢复「未实跑」依据 `phase4-batch-log.md` 自述 | 影响交付成熟度评级 | 现场演练一次并留证 |
| 6 | 支付验签判定基于代码实现，未接真实商户 | 影响商业可信结论 | 沙箱回调实测 |
| 7 | `.difypkg` 内 36 依赖未做 SCA | 供应链风险未知 | SCA 工具解包扫描 |
| 8 | 本报告以工作区当前状态为准；42 改 + 40 新增未提交，正式基线以未来提交为准 | 结论可能随提交变化 | 提交后复跑门禁 |

---

## 九、结论与下一步

**结论**：仓库的**工程治理水平仍显著优于同类项目**（门禁可红、错误面收敛、RLS 双保险、测试设计克制），W1 也让「任务完成」第一次可度量。但对照总纲，当前状态是「**文档已对齐、能力未兑现、交付未验证**」：

- **安全**：上一轮修好的租户隔离，被 agent 任务域从侧面绕过，且已实测可利用 → 任何 SaaS 试点前必须关闭。
- **产品**：总纲 §4.1 的「权限提示」、§4.4 的「成果采纳」、§5.1/5.2 的「个人与开发者」三条硬要求仍未落地；继续横向加模块的边际收益已低于补齐这三项。
- **交付**：CI 从未真实运行、203 条 PG 用例被跳过、备份恢复未实跑、离线包缺镜像 —— 「首版即商用」目前只有静态证据，没有执行证据。

**建议的下一步顺序**：先用 1 周关闭 P0-1~P0-3（越权 + 绕行路径 + 门禁回绿并跑通 CI），再用 2 周补 P0-4~P0-6（支付验签 + 前端测试 + 交付链闭环），最后把 P1-1~P1-3（权限提示 + 采纳率 + 个人场景包）作为下一阶段产品主线。**不建议**在 P0 未关闭前继续扩展插件数量或新增业务模块。

---

*本报告由软件开发团队（主理人齐活林 + 产品经理许清楚 + 架构师高见远 + QA 严过关）协作产出，全程只读未修改业务代码。所有结论附可核验文件路径；冲突处以总纲与代码事实为准。分项依据：`docs/audit-2026-10-23-*`（上轮基线）与本轮三人分项回传。*
