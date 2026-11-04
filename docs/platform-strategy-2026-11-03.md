# Moy 平台战略 → 工程路线（2026-11-03）

> **既定前提（竞品研究结论，不在本文重新论证）**
> 1. Moy 做通用工作台必输：分发、资金、模型成本都拼不过大厂。
> 2. 可站的位置是「20–100 席 + 数据不出内网 + 一个深垂直工作流」的私有化交付。
> 3. 插件市场 / 开放生态是第二阶段，**不进 V1.0**。
>
> 本文只回答三件事：现有代码里哪些已经是「积木骨架」、哪些只是壳；离 V1.0 交付形态还缺什么；以及 V1.0 / V1.1 / 明确不做 的三档取舍。
> 代码事实一律带 `路径:行号`；代码里看不出设计意图的地方标「**待用户确认**」，不补脑。

---

## 0. 结论先行

1. **插件骨架是真的，但只支撑「官方声明式插件」**：插件没有任何可执行载荷字段，平台用 6 个封闭原语按 JSON 声明编排，同进程执行（`apps/adapter/app/plugins/schema.py:3-6`、`app/plugins/actions.py:28-36`、`app/plugins/engine.py:1-23`）。这对 V1.0 的「深垂直」是好事——不需要沙箱，也不存在第三方代码风险。
2. **生态所需的七件套基本是壳**：签名/审核、对外 SDK、弃用策略、按插件计量与分账均无实现；版本兼容只有 semver + 最低平台版本，没有弃用通道。
3. **V1.0 的四块硬缺口是交付形态，不是功能**：离线镜像包构建脚本未实现、客户自带模型 Key 无注入契约、许可证/节点授权不存在、审计日志无导出。（`OA_OFFLINE_MODE` 死配置已于 2026-11-04 清理：主模板与门禁早前删除，仅剩的 `.env.lite.example` 残留也已删除。）
4. **不建议在 V1.0 补插件市场**：现有市场页与安装链路是给「官方 6 个插件」用的，扩到第三方会同时引爆隔离、签名、分账、治理四条线；V1.1 再做更划算。

---

## 1. 现状盘点：哪些是积木骨架，哪些只是壳

### 1.1 插件模型与执行引擎

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 插件只有 JSON 声明，**没有** `entry_point/module/handler/script/image/command/deps` 等可执行字段；模型 `extra="forbid"`，多写即加载失败 | `apps/adapter/app/plugins/schema.py:3-6`、`schema.py:122-140` | 骨架（设计明确） |
| 声明文件只从 `app/plugins/manifests/*.json` 加载，失败逐条留档、不中断整批 | `apps/adapter/app/plugins/loader.py:50-54`、`loader.py:162-194` | 骨架 |
| 执行原语封闭为 6 项：`understand/compose/retrieve/tool/render_file/summarize` | `apps/adapter/app/plugins/actions.py:28-36` | 骨架（也是天花板） |
| 引擎只负责推进一步，不碰数据库；失败走状态不走错误码 | `apps/adapter/app/plugins/engine.py:1-23` | 骨架 |
| 单步异常被收敛成 `StepOutcome`（`plugin_step_crashed` → 失败步骤），不会把异常抛穿主流程 | `apps/adapter/app/plugins/engine.py:378-421` | 骨架 |
| 重试最多 1 次，仅可重试错误码 | `apps/adapter/app/plugins/engine.py:330-347` | 骨架 |
| 护栏是声明式分派到 `app/plugins/guards/`，判据不写在引擎里 | `apps/adapter/app/plugins/engine.py:20-23` | 骨架 |

**边界**：这套引擎是「平台内置能力的编排器」，不是「第三方代码运行时」。V1.0 不走生态路线时，这正好；V1.1 要开放第三方，必须另做执行隔离（见 §2.1）。

### 1.2 安装、启停与授权

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 安装即启用：`not_installed → enabled` 一步到位 | `apps/adapter/app/business/plugin_service.py:12-16`、`plugin_service.py:487-504` | 骨架 |
| 安装校验顺序固定：套餐 → 本人授权 → 企业开关 → 前置插件 → 参数 | `apps/adapter/app/business/plugin_service.py:9-10` | 骨架 |
| 授权写 `plugin_grant`（成员×插件×能力），不新增 `role_permission`；权限点总数仍为 20 | `apps/adapter/app/business/plugin_service.py:15-16` | 骨架 |
| 停用不打断在跑任务；启用时重查授权与企业开关 | `apps/adapter/app/business/plugin_service.py:804-810` | 骨架 |
| 企业能力开关仅 3 项 `org.*`，存 `plugin_capability_policy`，需 `team:role:manage` | `apps/adapter/app/plugins/actions.py:71`、`app/db/repositories/plugin/postgres.py:62`、`app/api/v1/plugins.py:64-65` | 部分骨架 |
| 成员可自行安装（无需管理员审批） | `apps/adapter/app/api/v1/plugins.py:11` | 骨架（与生态审核冲突，见 §2.4） |

### 1.3 前端市场页

- 页面真实调用后端接口，不是静态占位：`apps/web/src/app/(main)/plugins/page.tsx:86/91/137/140`（市场、已装、工具、执行记录各自的 `useApiQuery`），重试在 `page.tsx:202`。
- 契约键由 `apps/web/src/hooks/use-api.ts:168-181` 定义（market / detail / capabilities / installed）。
- 判定：**骨架**。但它只覆盖官方插件目录，没有「发布方主页、上架状态、审核中、被下架原因、结算面板」这些第三方必需视图。

### 1.4 版本与依赖

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 声明含 `version` / `min_platform_version` / `requires_plugins` | `apps/adapter/app/plugins/schema.py:571-575` | 骨架 |
| 升级时校验目标版本存在与最低平台版本 | `apps/adapter/app/business/plugin_service.py:962-993` | 骨架 |
| 市场列表用 `version_tuple` 比对是否有更新 | `apps/adapter/app/business/plugin_service.py:286-290` | 骨架 |
| 前置插件缺失返回 `16009` | `apps/adapter/app/business/plugin_service.py:500-503` | 骨架 |
| 无「弃用/下线」字段、无远程版本源、无灰度/回滚通道（仅 PL9 恢复上一版本） | `apps/adapter/app/api/v1/plugins.py:542-614`；`schema.py:571-575` 无 deprecation 字段 | 壳 |

### 1.5 骨架 / 壳 总表

| 能力 | 判定 | 一句话理由 |
| --- | --- | --- |
| 声明式插件模型 | 骨架 | 字段级禁执行载荷，机械保证 |
| 执行引擎与原语 | 骨架 | 6 原语 + 状态机 + 声明式护栏 |
| 安装/启停/授权 | 骨架 | 安装即启用，授权独立于角色 |
| 市场页 | 骨架（仅官方目录） | 真实 API，但无第三方生命周期 |
| 版本与依赖 | 半骨架 | semver + 前置插件；无弃用与远程源 |
| 发布审核与签名 | 壳 | `vendor` 固定 `official`，无签名/来源校验（`schema.py:571`） |
| 对外 SDK 与契约文档 | 壳 | 仅有内部契约生成器 `deploy/export_plugin_contract.py:1-14` |
| 执行隔离 | 不存在（当前也不需要） | 无第三方代码 |
| 计量与分账 | 壳 | `usage_event` 无 `plugin_id` 列（`deploy/init-schema-phase2.sql:287-305`） |
| 企业治理 | 部分 | 只有 3 个企业能力开关，无允许/禁止清单 |
| 可观测 | 部分 | 指标带 `plugin_id` 标签（`apps/adapter/app/core/metrics.py:54/181`），但无插件级熔断 |

---

## 2. 生态可行性缺口：第三方敢不敢押注

> 判据不是「我们做没做」，而是「一个独立开发者把商业押在这里，会遇到什么」。

### 2.1 执行隔离

- 现状：插件是声明，不是代码；引擎在适配层同进程内调用平台原语（`app/plugins/engine.py:378-421`）。
- 若 V1.1 允许第三方代码：当前**没有任何**子进程/容器/沙箱路径；底座虽有 Dify Sandbox 镜像（`deploy/offline/README.md:29-32`），但不用于插件。
- 缺口：第三方无法确认「写坏一个插件会不会拖垮整个租户进程」。同进程只做了异常收敛，没有 CPU/内存/文件系统隔离。
- **判断**：V1.0 不开放代码，此缺口可以不解；V1.1 必须先把「声明式插件」作为唯一形态写进对外契约，再决定是否开放代码。

### 2.2 SDK 与契约文档

- 现状：`deploy/export_plugin_contract.py` 只是把后端 OpenAPI 的插件域模型导成前端契约（`export_plugin_contract.py:1-14`），不是给第三方的 SDK。
- 缺失：能力声明格式的外部文档、输入/输出 JSON Schema、错误语义（哪些错误可重试、谁能看到哪段文案）、本地调试与校验 CLI。
- 风险：第三方只能读本仓库源码和 `app/plugins/schema.py`，版本一变就断。

### 2.3 版本兼容与弃用

- 已有：semver、`min_platform_version`、`requires_plugins`（`schema.py:571-575`），升级/回退端点（`plugins.py:542-614`）。
- 缺失：平台版本升档时插件如何被标记「不兼容」、弃用公告期、强制下线策略、快照回放（`plugin_task.plugin_version` 存声明快照，见 `engine.py:352-353`）。
- 风险：平台一次升级可能让第三方插件静默失效，且没有迁移窗口。

### 2.4 发布审核与签名

- 现状：`vendor` 只能是 `official`（`schema.py:571`）；无发布者身份、无签名、无来源哈希；`app/business/plugin_review_service.py` 是**用户评价**，不是上架审核（PL11/PL12，`plugins.py:707-760`）。
- 缺失：提交、审核、驳回原因、签名验证、下架与召回。
- 风险：无法证明「这个插件确实由某发布者提交且未被篡改」；企业客户不会接受。

### 2.5 计量与分账

- 现状：`usage_event` 的唯一能力枚举是 `chat/doc_qa/doc_process/automation`，列里**没有** `plugin_id`（`deploy/init-schema-phase2.sql:287-305`）；月度聚合按租户/部门/成员（同文件 `usage_monthly`）。
- 已有：套餐、订单、配额（`app/business/plan_service.py`、`order_service.py`、`usage_service.py`）。
- 缺口：无法按插件归因，更没有价格表、结算单、分成比例、退款与对账。按插件计费不是加一个字段，而是加一条计量维度 + 结算周期 + 对账口径。
- 风险：第三方拿不到可信账单，平台也算不清该分多少。

### 2.6 可观测与故障隔离

- 已有：`plugin_id` 是指标允许标签（`app/core/metrics.py:54/181`）；单步异常收敛为失败步骤（`engine.py:378-421`）；步骤超时与重试由原语超时/`on_failure` 控制（`engine.py:330-347`）。
- 缺失：插件级熔断（连续失败自动停用）、资源配额、跨租户影响面评估、插件执行链路追踪导出。
- 风险：一个插件慢/崩，当前只会让**该任务**失败，不会拖垮进程；但同进程的 CPU/内存耗尽仍会影响同实例其他租户——这是 V1.1 开放生态前必须回答的问题。

### 2.7 企业侧治理

- 已有：逐能力企业开关（`org.directory.read` / `org.repo.write` / `org.external.publish`），存 `plugin_capability_policy`（`apps/adapter/app/plugins/actions.py:71`、`app/db/repositories/plugin/postgres.py:62`）。
- 缺失：租户级「允许插件清单/禁止插件清单」、插件级数据出域约束（例如某插件不得把知识库内容发到外部）、按部门可见性。
- 风险：企业只能「逐能力开总闸」，不能「只允许这三个插件、禁止那两个」。

---

## 3. V1.0 交付形态还缺什么（以现有代码为准）

> 目标形态：**气隙私有化一体机 + 客户自带模型与 Key + 按节点年费 + 核心可审计**。
> 下面只写「缺什么」，不重复建设已有能力。

### 3.1 气隙/离线安装

- 已有：
  - `deploy/install.sh` 已实现 `--offline`：校验 `checksums.sha256` → `docker load` → 按 `images.lock` 核对 digest（`deploy/install.sh:657-709`）；
  - 离线 Dify 模型插件包与安装引导（`deploy/offline/README.md:67-77`）；
  - 轻量 9 容器栈与 `images.lock`（`deploy/docker-compose.lite.yml`、`deploy/images.lock`）。
- 缺：
  1. **离线包构建侧没有实现**：`build-images.sh / load-images.sh / verify-images.sh / manifests/` 均为占位，README 明说「当前一个都不存在」（`deploy/offline/README.md:1-22`）；
  2. **`OA_OFFLINE_MODE` 死配置已清理（2026-11-04）**：`install.sh` 写入与门禁登记已于早前删除，最后残留的 `deploy/.env.lite.example` 一行也已删除；离线模式若实现需重新引入并补消费方；
  3. 无介质/容量/CPU 架构决策（README 阻塞项，`deploy/offline/README.md:62-65`）。
- V1.0 验收口径：在一台**无外网**干净机器上，`bash deploy/install.sh --offline --images-dir <介质>` 一次装成；篡改任一 tar 后必须退出码 3；安装完成后无任何外联请求（可用客户侧防火墙证明）。

### 3.2 客户自带模型与 Key（BYOK）

- 现状：
  - 适配层只持底座**应用 Key**（`dify_default_app_key`，`apps/adapter/app/core/config.py:136-137`），知识库可绑定库级应用 Key，密文存 `credential_store`（`app/business/credential_service.py:344-396`）；
  - 模型供应商（DeepSeek 等）的 Key 属于底座 Dify 的配置域，仓库里没有注入、校验或展示契约；离线交付靠 `deepseek-0.0.25.difypkg`（`deploy/offline/README.md:67-77`）。
- 缺：客户 Key 的**注入契约**（装机器时写哪里、如何校验、如何轮换、失败时给什么提示），以及「平台不代持供应商 Key」的产品承诺如何被安装脚本证明。
- **待用户确认**：客户 Key 是在 Dify 管理台自行配置，还是由 Moy 交付包提供配置向导/接口？两种方案改动面完全不同。
- V1.0 验收口径：交付物中不含任何供应商 Key；安装后一次真实模型调用成功；Key 不出现在日志、审计、导出与错误响应中。

### 3.3 许可证 / 节点授权

- 现状：全仓检索 `license / entitlement / activation / machine_id / node_id`，只命中依赖声明与前端文案，**没有校验实现**。
- 缺：离线许可证文件（节点指纹 + 席位数 + 到期日 + 签名）、启动/安装时校验、过期与时钟回拨处理、错误码与运维提示。
- **待用户确认**：按节点年费的计量单位（CPU 核/主机/并发席位）、是否允许离线续期、许可证是否与客户企业名绑定。
- V1.0 验收口径：错节点/过期/篡改许可证必须拒绝启动且提示可执行；正常许可证在时钟回拨 24h 内容忍。

### 3.4 审计日志导出

- 现状：只有查询端点 `POST /api/v1/identity/audit-logs`（`apps/adapter/app/api/v1/identity.py:236-266`）与动作枚举（`identity_ext.py:632-667`），**没有导出**（CSV/JSON/对象存储）实现。
- 缺：按时间/动作/成员导出、导出任务化（大批量不阻塞请求）、导出文件脱敏与下载审计。
- V1.0 验收口径：企业管理员可导出指定区间审计日志；导出行为本身写审计；导出文件不含密钥与内部路径。

### 3.5 单垂直工作流的「深」

- 现状：内置 6 个声明均为 **2–6 步**浅工作流：
  - `plg_content_ops.json`（6 步）、`plg_dev_workbench.json`（6 步）、`plg_hr_admin.json`（6 步）、`plg_mail_draft.json`（2 步）、`plg_meeting_minutes.json`（3 步）、`plg_weekly_report.json`（3 步），均在 `apps/adapter/app/plugins/manifests/`。
- 缺：领域数据、领域护栏、失败恢复、可交付产物质量证据；当前更像「流程演示」，不是「一个能签合同的垂直方案」。
- **待用户确认**：V1.0 选哪一条垂直（行政人事 / 内容运营 / 研发工作台），以及该垂直的「完成定义」（哪些产物、哪些校验、哪些人工接管点）。

---

## 4. 三档取舍

### 4.1 V1.0 必须做

| # | 事项 | 改动面（文件/模块） | 验收口径 | 决策 |
| --- | --- | --- | --- | --- |
| A1 | 选定并做深**一个**垂直工作流 | `app/plugins/manifests/plg_*.json`、`app/plugins/guards/`、`app/plugins/actions.py`（仅扩声明与护栏，不新增原语）、对应测试 | 该垂直端到端样例 ≥20 条；每步成功/接管/失败路径有断言；产物可下载且质量抽检通过；无新增原语 | 选哪条垂直：**待用户确认** |
| A2 | 气隙一体机交付 | `deploy/offline/`（新增构建/校验脚本）、`deploy/install.sh`、`deploy/images.lock`、`deploy/sbom/generate.py`、文档 | 无外网一次装成；篡改包退出码 3；安装后台账 digest 全对；交付介质与架构已定 | 介质/架构：**待用户确认** |
| A3 | 处置 `OA_OFFLINE_MODE` | `deploy/validate_config_names.py`、`.env` 模板、`app/core/config.py`（二选一：接消费方或删除） | 门禁不再把它当死配置；文档与清单自洽 | ✅ **已删除（2026-11-04）**：主模板、`install.sh`、门禁登记与 `.env.lite.example` 残留均清理完毕 |
| A4 | 客户自带模型 Key 契约 | `deploy/.env*.example`、`deploy/install.sh` 预检、文档；**不改** `credential_service` 的库级 Key 逻辑 | 交付物零供应商 Key；安装后真实调用成功；Key 不入日志/审计/响应 | 配置位置：**待用户确认** |
| A5 | 离线许可证/节点授权 | 新增 `app/core/licensing.py`（或等价模块）、启动校验、`install.sh` 预检、文档 | 错节点/过期/篡改拒启；正常许可证可用；时钟回拨有容差 | 计量单位/续期方式：**待用户确认** |
| A6 | 审计日志导出 | `app/api/v1/identity.py`、`app/business/audit_service.py`、`app/db/repositories/audit_repo.py`、导出任务 worker | 管理员可导出区间日志；导出本身写审计；大批量不阻塞 | 导出格式（CSV/JSON）：**待用户确认** |
| A7 | 透明计量展示 | 现有 `usage_service` / 配额页；`usage_event` 增维度需迁移 | 企业可看到本月 token/文档处理量与来源；与账单口径一致 | 是否按插件归因：V1.0 不需要 |
| A8 | 核心可审计（开源范围） | 根 `LICENSE`、`deploy/sbom/generate.py`、`about` 页清单、第三方许可证随包 | 交付包含 SBOM + 第三方许可证；开源范围清单对外可查 | 现有 `LICENSE` 是**私有版权声明**（`LICENSE:1-8`），与「开源范围」承诺冲突；开哪些、换什么许可：**待用户确认** |

**V1.0 不自研、直接接第三方更划算**：
- 镜像导出/搬运：`skopeo` / `crane`（不要自己写 `docker save` 编排）；
- SBOM：`syft`（`deploy/sbom/generate.py` 已存在，补齐调用即可）；
- 签名与验签：`cosign` / `sigstore`（许可证签名可复用同一套密钥与流程）；
- 审计外送：客户 SIEM / Filebeat 采集，平台只保证导出格式；
- 模型推理：客户自有 Dify + 模型服务，平台不做推理网关。

### 4.2 V1.1 补（不进 V1.0）

| # | 事项 | 改动面 | 验收口径 | 前置 |
| --- | --- | --- | --- | --- |
| B1 | 插件市场对外开放 | `plugin_service` / `catalog` / 前端市场页 | 第三方可提交、企业可搜索安装、下架可追溯 | A1 稳定 |
| B2 | 发布审核与签名 | 新增审核域 + 签名校验；`schema.vendor` 放开 | 未签名包拒绝加载；审核状态可查；驳回有原因 | 密钥与 CA 方案 |
| B3 | 对外 SDK 与契约 | 新增独立 SDK 包 + JSON Schema + 示例 | 第三方按文档 30 分钟内跑通本地校验 | B1 |
| B4 | 版本兼容与弃用策略 | `schema` 增弃用字段、加载器告警、平台升档清单 | 升档前给出不兼容清单；弃用有公告期 | B1 |
| B5 | 按插件计量与分账 | `usage_event` 增 `plugin_id`（迁移）、结算表、对账任务 | 账单可追溯到事件；分成可对账；退款有路径 | 商业条款 |
| B6 | 执行隔离（若开放代码） | 独立进程/容器 + 资源配额 + 熔断 | 单插件故障不影响其他租户；有资源上限 | B2 |
| B7 | 租户级允许/禁止清单 | 治理表 + 管理端点 + 前端 | 企业可只允许指定插件；越权安装被拒 | B1 |
| B8 | 插件级可观测 | 指标补失败率/耗时；熔断与告警 | 连续失败自动停用；有租户级看板 | B6 |

### 4.3 V1.0 明确不做

1. **通用助手铺量**（对话/写作/问答全场景扩张）；
2. **技能数量 KPI**（以插件个数当增长指标）；
3. **抽成收入预期**（不在 V1.0 财报里承诺 marketplace take rate）；
4. **大企业直销**（不做超大型客户的定制交付与直销团队）；
5. **消费级分发**（不做 C 端应用商店/个人版裂变）；
6. **编码 IDE**（不与 Codex/Claude Code 正面竞争）；
7. **跨境电商**（不做跨境场景与合规链路）。

理由：这七项都要求「分发 + 资金 + 模型成本」三项中的至少两项，正是竞品研究判定 Moy 必输的战场。

---

## 5. 与大厂的现实差距（只写工程相关）

| 维度 | 大厂现状 | Moy 现状（代码事实） | 工程含义 |
| --- | --- | --- | --- |
| 分发渠道 | 应用商店/系统入口/企业预装 | 无远程插件源，声明只从本地 `app/plugins/manifests/` 加载（`loader.py:50-54`） | V1.0 不拼分发，拼「交付到客户机房」 |
| 模型成本 | 自建推理、集中采购、补贴 | 客户自带 Key，平台不持供应商 Key（`config.py:136-137`） | 没有成本优势，也没有价格战空间；卖「数据不出内网」 |
| 基础设施 | 多区域弹性、托管运维 | 单机 9 容器 + 基础 k8s 清单（`deploy/docker-compose.lite.yml`、`deploy/k8s/base/`）；离线包构建未实现（`deploy/offline/README.md:1-22`） | 交付工程是核心竞争力，必须把离线安装做成产品 |
| 生态供给 | 海量第三方插件与开发者 | 6 个官方声明、无签名/审核/分账 | V1.0 不承诺生态；V1.1 再开 |
| 运维可观测 | 全链路追踪与集中告警 | Prometheus 规则与 exporter 已有（`deploy/prometheus.yml`、`deploy/alert.rules.yml`） | 私有化场景要能「客户自己看得懂」，不是云端大盘 |

---

## 6. 待用户确认清单（拍板项）

1. **垂直选择**：V1.0 做深哪一条（行政人事 / 内容运营 / 研发工作台），以及完成定义。
2. **交付介质与架构**：离线包走物理介质/内网 FTP/对象存储？x86_64 还是也要 arm64？
3. **`OA_OFFLINE_MODE`**：✅ 已拍板**删除**（2026-11-04），最后残留的 `.env.lite.example` 已清理；若未来实现离线模式再连同消费方一起引入。
4. **BYOK 边界**：客户模型 Key 在 Dify 管理台配置，还是 Moy 提供配置向导/接口？
5. **许可证口径**：按主机/核/席位计费？是否允许离线续期？是否绑定企业名？
6. **审计导出格式与脱敏范围**：CSV/JSON？是否允许包含成员标识？
7. **开源范围与许可**：现有 `LICENSE:1-8` 是「All rights reserved」私有声明；「核心可审计」若指开源，需要明确开放模块、双许可/分仓边界与对外承诺。
8. **计费展示口径**：V1.0 只需透明展示用量，不承诺按插件分账——是否确认？

---

## 附：本文引用核对说明

- 所有 `路径:行号` 均来自当前工作树（main `6e15f3e` 之后的任务 A 提交前状态），可直接打开核对。
- 本文不改任何业务代码；任务 A（QA-10 内容嗅探）的改动与验证结果另见提交记录。
