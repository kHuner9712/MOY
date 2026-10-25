# 插件开发指南（P0-P3-05）

> **本文档是 V1.0 插件体系的唯一开发说明来源**：一份声明规范 + 一个可运行样例插件
> （`plg_mail_draft`，内部使用、不进市场），覆盖「声明字段表 / 6 项能力枚举与申请口径 /
> 输入定义规范 / 输出产物类型 / 界面描述规范 / 允许调用的端点清单 / 样例 walkthrough」。
>
> - 需求出处：`docs/prd-phase3-increment.md` §3.1 P0-P3-05 与 §3.2.1 验收标准 ① ② ③；
> - 设计出处：`docs/architecture-phase3.md` §2（声明规范）/ §3（运行时）/ §5（契约）/ §11（D-14~D-26）；
> - 代码真源：`apps/adapter/app/plugins/schema.py`（声明模型）、`loader.py`（加载校验）、
>   `actions.py`（原语与能力白名单）、`engine.py`（步骤引擎）、`renderer.py`（文件渲染）、
>   `sanitizer.py`（脱敏）、`app/api/v1/plugins.py` 与 `app/api/v1/agent.py`（端点）。
>
> **术语约定**：插件 = 一份 JSON 声明 + 对平台能力的引用，**不含任何可执行代码**
> （D-14）。文中「端点」指适配层对外地址；「声明」指插件目录里的 JSON 文件
> （代码内标识符为 `manifest`，仅出现在路径与字段名中）。面向用户的文案一律使用业务语言，
> 不出现底层技术词——这份文档同样遵守该红线（见 §8）。

---

## 1. 声明字段表

### 1.1 声明是什么、放在哪里

一份插件 = 一个 JSON 文件，放在 `apps/adapter/app/plugins/manifests/<plugin_id>.json`。
文件名与 `plugin_id` 一致（内置 6 份：`plg_dev_workbench` / `plg_content_ops` /
`plg_hr_admin` / `plg_weekly_report` / `plg_meeting_minutes` / `plg_mail_draft`）。

声明分 **6 段**，每段解决一个问题：

| 段 | 字段 | 回答的问题 |
| --- | --- | --- |
| ① 基本信息 | `plugin_id` `name` `summary` `description` `category` `icon_key` `vendor` `version` `min_platform_version` `changelog` `requires_plugins` | 这是什么插件、给谁看 |
| ② 授权能力 | `capabilities[]` | 它要读/写用户的什么（用户逐项同意） |
| ③ 插件配置 | `settings_schema[]` | 安装时/使用前要固定下来的选项 |
| ④ 输入与产物 | `inputs[]` `outputs[]` | 用户填什么、最后拿到什么 |
| ⑤ 界面描述 | `ui_extension` | 它在工作台里长在哪、叫什么 |
| ⑥ 执行定义 | `steps[]` `checklist[]` | 分几步做、每步产出什么、结果怎么核查 |

**两条铁律**（`schema.py` 全部模型 `extra="forbid"`）：

1. **多写一个字段即加载失败**——未在本文登记的字段一律不存在，没有"自定义扩展位"；
2. **文案字段全部过禁词表**（`name` / `summary` / `description` / `changelog` /
   `steps[].title` / `inputs[].label` / `outputs[].label` / `checklist[].name` /
   `settings_schema[].label` / `ui_extension.nav_label`）——命中即**拒绝加载**，
   不是警告。清单含「编排 / 节点 / 回滚 / 依赖 / 占位 / 待补充 / TODO」等，
   目的是不让内部用语出现在用户界面上。

### 1.2 第①段：基本信息

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `plugin_id` | ✅ | string | 6-44 字符 | 正则 `^plg_[a-z][a-z0-9_]{2,40}$`；**稳定标识，不是资源 ID**；同一标识同一版本重复注册即拒绝（D-18） |
| `name` | ✅ | string | 2-32 字 | 过禁词表 |
| `summary` | ✅ | string | 4-60 字 | 一句话说明；过禁词表 |
| `description` | ✅ | string | **≥120 且 ≤800 字** | 下限是 PRD P0-P3-07 的明确要求（详情页说明区）；过禁词表 |
| `category` | ✅ | enum | 6 选 1 | `dev` \| `content` \| `hr` \| `data` \| `collab` \| `other`（PRD P0-P3-06 六分类，封闭） |
| `icon_key` | ✅ | string | 1-32 字符 | 平台图标名，**不引用任何外部资源** |
| `vendor` | ✅ | enum | 固定 `official` | V1.0 只有官方内置插件，无第三方值 |
| `version` | ✅ | semver | ≤16 字符 | 正则 `^\d+\.\d+\.\d+$`（不接受预发布标签）；`(plugin_id, version)` 全局唯一 |
| `min_platform_version` | ✅ | semver | ≤16 字符 | 平台版本低于它 → 升级被拒（16007） |
| `changelog` | ❌ | string | ≤300 字，缺省 `""` | 版本变更说明（详情页版本历史） |
| `requires_plugins` | ❌ | string[] | ≤3 个，缺省 `[]` | 元素须为合法 `plugin_id` 且去重；前置插件未启用 → 安装被拒（16009） |

### 1.3 第②段：授权能力 `capabilities[]`

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `key` | ✅ | string | 3-64 字符 | **必须 ⊆ 6 项封闭枚举**（§2），未开放的能力 → 拒绝加载 |
| `required` | ❌ | bool | 缺省 `true` | `false` = 用户可以不勾（V1.0 内置 6 份声明均为 `true`） |

- 声明条数 1-6 条，**不可重复**；
- 授权条数与声明条数 **1:1**（PRD P0-P3-03 验收②）：授权抽屉逐项展示，不合并、不拆分。

### 1.4 第③段：插件配置 `settings_schema[]`

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `key` | ✅ | string | 1-64 字符 | 写入安装记录配置的键；PL10 保存时键必须 ⊆ 本段 |
| `label` | ✅ | string | 1-32 字 | 用户可见名称；过禁词表 |
| `type` | ✅ | enum | 2 选 1 | `text` \| `select` |
| `options` | ❌ | string[] | ≤20 个，缺省 `[]` | `select` 必给候选值；`text` **不得**给候选值（两者不匹配即拒绝加载） |

- 整段 ≤8 项，可缺省（无配置项的插件不写本段）。

### 1.5 第④段：输入 `inputs[]` 与产物 `outputs[]`

`inputs[]`（1-12 项，**必填段**）：

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `key` | ✅ | string | 1-64 字符 | 任务输入的键；段内不可重复 |
| `label` | ✅ | string | 1-32 字 | 用户可见名称；过禁词表 |
| `type` | ✅ | enum | 6 选 1 | `text` \| `textarea` \| `select` \| `multi_select` \| `email` \| `file`（§3） |
| `required` | ❌ | bool | 缺省 `false` | 必填项为空 → 建任务即拒（20001，业务文案「请填写「XX」」） |
| `max_length` | ❌ | int \| null | 1-8000，缺省 `null` | 仅文本类有意义；超长 → 20001（「「XX」不能超过 N 个字」） |
| `options` | ❌ | string[] | ≤20 个，缺省 `[]` | `select`/`multi_select` 的候选值；**允许为空**（动态取值，见 §3.2）；文本类给候选值即拒绝加载 |

`outputs[]`（1-12 项，**必填段**）：

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `key` | ✅ | string | 1-64 字符 | 产物键；段内不可重复；步骤 `produces` 必须 ⊆ 本段 |
| `label` | ✅ | string | 1-32 字 | 用户可见名称；过禁词表 |
| `type` | ✅ | enum | 4 选 1 | `text` \| `link` \| `file` \| `table`（§4） |

### 1.6 第⑤段：界面描述 `ui_extension`（必填，单对象）

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `layout` | ✅ | enum | 仅 `task_workspace` | **只有 1 个值**：新增布局 = 能力扩张，是故意的（D-17） |
| `nav_label` | ✅ | string | 2-16 字 | 侧边栏入口名称；过禁词表 |
| `icon_key` | ✅ | string | 1-32 字符 | 平台图标名 |
| `order` | ✅ | int | 1-99 | 侧边栏排序号 |

渲染规则见 §5。

### 1.7 第⑥段：执行定义 `steps[]`（1-8 步，必填）

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `seq` | ✅ | int | 1-8 | **必须从 1 连续递增**（写 1,3,4 即拒绝加载） |
| `title` | ✅ | string | 2-32 字 | 用户可见步骤名；过禁词表 |
| `action` | ✅ | enum | 6 选 1 | `understand` \| `compose` \| `retrieve` \| `tool` \| `render_file` \| `summarize`（平台内部执行原语，**永不进授权页**，D-15） |
| `produces` | ❌ | string[] | ≤8 个，缺省 `[]` | 产物键 ⊆ `outputs[].key` |
| `needs` | ❌ | string[] | ≤8 个，缺省 `[]` | 前序产物键，**必须由更早的步骤产出**（否则拒绝加载） |
| `on_failure` | ❌ | enum | 4 选 1，缺省 `abort` | `abort`（任务失败、后续跳过）/ `continue`（该步失败但任务继续，最终告警）/ `skip_and_mark`（跳过并在核查清单标「待人工确认」）/ `retry`（自动重试 1 次，仅可重试错误） |
| `confirm` | ❌ | object \| null | 缺省 `null` | 人工接管点：`{reason: 4-80 字, summary_fields: ≤8 个输入/产物键}`；**对外发送动作必须声明**（缺了拒绝加载） |
| `when` | ❌ | object \| null | 缺省 `null` | 条件执行：`{input: 输入键, in: 1-20 个取值}`（JSON 里键名是 **`in`**）；条件不满足 → 该步跳过 |
| `tool_type` | ⚠️ | string \| null | ≤32 字符 | `action="tool"` 时**必填**，且必须在工具登记表内（§6.3）；其它原语**不得**带 |
| `operation` | ⚠️ | string \| null | ≤64 字符 | 同上，须 ∈ 该工具的 operations |
| `tool_alternatives` | ❌ | object[] | ≤3 个，缺省 `[]` | 等价替代 `{tool_type, operation}`（按输入里的渠道选择）；同样受白名单校验；不得与主工具重复 |
| `render_format` | ⚠️ | enum \| null | `docx` \| `md` \| `csv` | `action="render_file"` 时**必填**（格式是产物契约，不允许运行期挑）；其它原语**不得**带 |
| `timeout_seconds` | ❌ | int | 10-600，缺省 120 | 单步超时 |

`checklist[]`（2-5 项，**必填段**，结果物核查清单，P0-P3-14）：

| 字段 | 必填 | 类型 | 上限 / 默认 | 校验规则 |
| --- | --- | --- | --- | --- |
| `key` | ✅ | string | 1-64 字符 | 必须是已声明的产物键 |
| `name` | ✅ | string | 2-24 字 | 核查项名称；过禁词表 |
| `source_step` | ✅ | int | 1-8 | 产出该结果物的步骤序号，必须存在 |

### 1.8 结构自洽规则（跨字段，违反即拒绝加载）

1. 步骤序号从 1 连续；产物键、输入键段内不重复；
2. 每步 `produces` ⊆ `outputs[].key`；每步 `needs` 已被更早步骤产出；
3. `when.input` 是已声明的输入键；`confirm.summary_fields` 是已声明的输入或产物键；
4. `checklist[].key` 是已声明的产物键，`source_step` 指向存在的步骤；
5. `tool` 步骤必带 `tool_type` + `operation`，其它原语不得带工具字段；等价替代的工具类型不得重复；
6. `render_file` 步骤必带 `render_format`，其它原语不得带；
7. `on_failure="retry"` 只能用于可重试原语（`understand`/`compose`/`retrieve`）；写外部动作永不自动重试；
8. 声明了 `personal.file.read` 就必须有真实读取支撑（`inputs[].type=file` 或 `retrieve` 步骤）——导出文件不需要读文件授权，不许"要了不用"。

### 1.9 加载器的八层校验（哪些写法会被拒）

加载器（`loader.py`）在**启动期**把一份声明可能出错的地方一次查完，任何一项不通过都
**拒绝加载并留档原因**（CI 与日志可见），绝不"带病注册"：

| # | 层 | 拒绝写法（示例） |
| --- | --- | --- |
| 1 | 禁用键名 | 任何层级出现 `token` / `api_key` / `secret` / `password` / `credential` / `endpoint` / `base_url` / `url` / `host` / `dsn` / `entry_point` / `module` / `handler` / `script` / `image` / `command` / `deps` 等键名（递归扫描，塞进配置值里也会被抓到） |
| 2 | 形态（6 段） | 顶层不是 JSON 对象；缺必填字段；多未声明字段；类型/枚举/长度/序号不合法 |
| 3 | 文案禁词 | 文案字段命中禁词表（含占位词：`待补充` / `TODO` / `xxx` / `敬请期待` 等） |
| 4 | 能力封闭 | `capabilities[].key` 不在 6 项枚举内；能力声明重复 |
| 5 | 原语与工具 | `action` 不在 6 项白名单；`tool_type`/`operation` 不在工具登记表内 |
| 6 | 人工接管 | 对外发送动作（发邮件 / 发消息 / 发公众号 / 建合并请求）未声明 `confirm` |
| 7 | 导出与重试 | `render_file` 缺 `render_format`；`retry` 用在不可重试原语上 |
| 8 | 版本不可变 | 同一 `(plugin_id, version)` 注册两次即拒绝（D-18：已注册条目字段保持不变） |

**加载失败的市场表现**：该插件不出现在市场列表（PL1），详情页按「该插件已下架」处理；
后台日志留档 `source / stage / reason` 三段，可直接定位到字段。

---

## 2. 能力枚举与申请口径（6 项封闭集合）

### 2.1 为什么能力是"数据边界"而不是"功能清单"

插件的能力声明分两层，**彻底分离**（D-15，本阶段最关键的设计决策）：

| 层 | 字段 | 谁看 | 内容 |
| --- | --- | --- | --- |
| 授权能力 | `capabilities[].key` | **用户**（授权抽屉逐项同意） | 6 项数据/写入边界（本节） |
| 执行原语 | `steps[].action` | 平台（永不进授权页） | 6 项平台内部做法（§1.7） |

若把执行原语当授权项，用户看到的是「生成代码」而不是「它要读你什么」——既无法回答
授权边界，也会把技术词汇带上界面。所以：**能力描述"读/写什么"，原语描述"怎么做"。**

### 2.2 六项能力逐项说明

个人能力（3 项）**恒开**，企业不可关闭；企业能力（3 项）需管理员在团队设置开启总开关
（`org_switch_enabled`，由 PL4 逐项返回，前端不得硬编码）。

| # | 能力键 | 名称 | 意味着什么 | 什么场景该申请 | 申请后平台给什么 | 不同意的后果 |
| --- | --- | --- | --- | --- | --- | --- |
| ① | `personal.knowledge.read` | 读取知识库 | 读取你所在企业的知识库内容 | 需要检索与引用制度、规范等素材的插件（如周报归拢） | 运行期按可见范围检索，可见库由平台策略收敛 | 无法引用知识库素材，生成的稿件可能缺少企业事实依据 |
| ② | `personal.tool.use` | 使用办公工具 | 调用你已连接的办公工具 | 需要把结果写入邮箱、仓库、企业微信、飞书、公众号的插件 | 工具凭据由平台代管（T5 内部解析），插件只拿结果 | 无法自动发送或提交，只能下载内容后手工处理 |
| ③ | `personal.file.read` | 读取你上传的文件 | 读取你在本次任务中选择的已有文档 | 基于上传材料做处理的插件（如会议纪要、简历筛选） | 凭文档引用（`doc_ids`）取文本，插件不持有文件字节 | 无法基于上传文件给出结果 |
| ④ | `org.directory.read` | 读取通讯录 | 读取企业通讯录与组织架构 | 需要按部门自动确定收件人的插件（人工填部门的场景不需要申请） | 通讯录查询结果进入本次任务 | 需要你手工填写部门与收件人 |
| ⑤ | `org.repo.write` | 写入代码仓库 | 把生成的代码提交到指定仓库并创建合并请求 | 研发类插件（提交前必确认） | 以你的身份调用代码仓库 | 只生成代码文件，由你手工提交 |
| ⑥ | `org.external.publish` | 对外发布内容 | 把内容发布到对外平台（公众号、邮件等） | 内容运营、周报发送、录用通知发送（发布前必确认） | 以你的身份调用对外渠道 | 只生成稿件与排期表，由你手工发布 |

### 2.3 能力 ↔ 执行原语映射（双向校验的依据）

| 执行原语 | 必需能力（任一满足） | 说明 |
| --- | --- | --- |
| `understand` | — | 平台内置推理，不读外部数据，无需授权 |
| `compose` | — | 同上；只处理任务输入 |
| `retrieve`（知识库） | `personal.knowledge.read` | 可见库由平台策略收敛 |
| `retrieve`（上传文件） | `personal.file.read` | 声明须同时有 `inputs[].type=file` |
| `tool`（代码仓库写） | `personal.tool.use` + `org.repo.write` | 连接权 + 写仓库权，两个都要 |
| `tool`（邮箱 / 公众号对外发送） | `personal.tool.use` + `org.external.publish` | 同上 |
| `tool`（读通讯录） | `personal.tool.use` + `org.directory.read` | 同上 |
| `tool`（其它只读操作） | `personal.tool.use` | — |
| `render_file` | — | 纯本地导出，与读文件授权**无耦合** |
| `summarize` | — | 纯本地聚合 |

**双向校验（G-30，CI 门禁）**：要了能力却没有对应步骤 → 报「声明了未使用的能力」（防多要权限）；
用了步骤却没要对应能力 → 报「使用了未声明的能力」。声明作者按上表自查即可一次通过。

### 2.4 申请口径（用户视角的三步）

1. **安装时逐项同意**（PL5）：`grants` 必须覆盖声明的全部 `required: true` 能力且为 `true`，
   缺项 → 16005（`data.missing_capabilities[]` 指出缺哪几项）；多传未声明能力 → 20001；
2. **企业能力先开总开关**（PL13，管理员）：④⑤⑥ 未开放 → 16006，界面给「需要管理员开启」+
   复制申请说明；个人能力恒开，传 `personal.*` 给 PL13 → 20001；
3. **授权可撤销**（PL14，仅限本人）：必需能力不可撤（撤了插件直接不可用，引导停用/卸载）；
   撤销后**下一次调用立即生效**（缓存 ≤5s），已完成步骤不回滚。

授权抽屉的三要素（名称 / 为什么需要 / 不同意的后果）由平台按 §2.2 表格生成，
声明作者**不需要**也无法自定义——写 `capabilities` 只写键名。

---

## 3. 输入定义规范

### 3.1 控件类型（6 种，封闭）

| `type` | 控件 | 取值形态 | 备注 |
| --- | --- | --- | --- |
| `text` | 单行文本 | string | 可配 `max_length`（≤8000） |
| `textarea` | 多行文本 | string | 可配 `max_length` |
| `select` | 单选 | string | 候选值来自 `options` 或平台动态提供 |
| `multi_select` | 多选 | string[] | 同上 |
| `email` | 邮箱 | string | 收件人类输入的专用形态 |
| `file` | 文件选择 | **`doc_ids: string[]`** | 选**已有文档**，不是当场上传（见 3.3） |

宿主表单由声明驱动（P0-P3-24）：前端拿 PL2 下发的 `inputs[]` 原样渲染，
**不得硬编码控件**——声明加一个输入项，表单就多一项，无需改前端。

### 3.2 候选值：静态与动态

- **静态**：`options` 写死候选（如语气 `["正式","简洁"]`）；
- **动态**：`select` / `multi_select` 允许 `options` 为空——此时取值由平台按身份提供
  （「目标仓库」来自已连接的代码仓库、「所属部门」来自通讯录）。写死静态候选反而会让
  声明与真实数据不一致。

运行期校验（建任务时，业务文案直接可展示）：必填为空 →「请填写「XX」」；超长 →
「「XX」不能超过 N 个字」；取值不在候选范围 →「「XX」的取值不在可选范围内」。

### 3.3 文件类输入口径（V1.0 定稿）

| 项 | 口径 |
| --- | --- |
| 值形态 | `doc_ids: string[]`（已存在文档的 id），**插件不持有文件字节，只持有引用** |
| 文件从哪来 | 用户先在「知识库 / 我的文件」上传，再在插件表单里**选已有文档**（上传是平台既有能力，插件不重复实现） |
| 插件怎么读 | 运行期凭 `personal.file.read` 解析 `doc_ids` 取文本 |
| 是否新增上传端点 | **不新增**：V1.0 端点表已冻结；"先有文件再让插件处理"是真实场景，表单里不当场上传 |
| 空态 | 没有可选文档时，选择器提示「还没有上传文件，先去知识库上传」并给跳转 |
| 校验 | 越权（不属于本人可见范围）→ 20003；不存在/已删除 → 20004；数量超声明上限 → 20001 |

---

## 4. 输出产物类型

### 4.1 四种产物类型（封闭）

| `type` | 语义 | 内容形态 | 结果区渲染 |
| --- | --- | --- | --- |
| `text` | 文本稿件（正文、纪要、回执） | 内联文本，**≤32KB**（超出写对象存储） | 可预览、可复制、可下载、可编辑（WF2） |
| `link` | 外链（提交记录、合并请求、归档地址） | `external_url` | 可跳转；**只读**（不可编辑——它是结果物核查的证据链） |
| `file` | 文件（代码文件、评分表、导出文档） | 对象存储键 + 二进制 | 可下载；**只读** |
| `table` | 表格（议题结论、评分表、排期表） | CSV 文本 + 前 5 行预览 | 表格预览、可下载 CSV、可编辑（WF2） |

### 4.2 产物口径（与运行时对齐）

- **每步 0..n 个产物**；已完成步骤的产物数 ≥1（P0-P3-12 验收②）——空产出判该步失败；
- **落库前统一脱敏**：邮箱/手机/身份证掩码；凭据类键名直接拒绝（`ArtifactSanitizer`）；
  产物编辑（WF2）保存时**重跑脱敏**，"用户改过"不是跳过脱敏的理由；
- **下载走平台代理**：`download_url` 形如 `/files/artifacts/{token}`，令牌自带企业 + 产物 +
  版本并签名，**不暴露存储地址**；产物留存 **180 天**，过期后下载地址为空并提示重新发起；
- **可编辑性**：`text` / `table` 可编辑（版本 +1，旧版可回看可下载）；`link` / `file` 只读
  （仓储层再判一次，不只靠端点层）；
- **特殊护栏**（平台内置，声明无需关心）：录用通知产物（`offer`）跑金额三规则；
  发布排期表（`schedule`）校验计划时间不得过去、同平台同刻不得重复。

---

## 5. 界面描述规范

### 5.1 声明只"描述"，渲染全平台

插件**不携带、也无法携带任何界面实现**。`ui_extension` 只回答三个问题：
入口在哪（`nav_label` + `order` + `icon_key`）、用什么布局（`layout`）、
剩下的全部由**平台工作台宿主**按声明渲染（D-17，一个宿主渲染全部插件）：

| 声明字段 | 宿主渲染结果 |
| --- | --- |
| `inputs[]` | 任务表单（§3.1 控件一一对应） |
| `steps[]` | 步骤时间线（标题 + 状态 + 进度） |
| `outputs[]` / 产物 | 结果区（按类型渲染，§4.1） |
| `checklist[]` | 结果物核查清单（已核查 / 待人工确认 / 没有产出 三态） |
| `confirm` | 人工接管卡片（原因 + 待确认摘要 + 确认按钮） |

### 5.2 硬性约束：必须用平台组件库

1. **必须使用平台组件库**：工作台宿主只用平台组件库渲染；插件侧没有任何"自定义组件"入口
   （声明里没有该字段，加载器 `extra="forbid"` 直接拒）；
2. **禁止自带样式与脚本**：插件目录（`app/plugins/manifests/` 及配套目录）出现
   `.css` / `.scss` / `.jsx` / `.tsx` 文件即被前端门禁（G-27）拦截；声明里出现样式/脚本类
   键名（如 `style` / `script` / `css`）会被第 1 层禁用键名扫描或形态校验拒绝；
3. **不引用外部资源**：`icon_key` 只取平台图标名；不得出现外部地址类字段
   （`url` / `endpoint` / `base_url` 等，§1.9 第 1 层）。

### 5.3 会被拒绝的界面写法（速查）

| 写法 | 结果 |
| --- | --- |
| `ui_extension` 里加未声明字段（如 `theme` / `custom_css`） | 形态校验拒绝（`extra="forbid"`） |
| `layout` 写 `task_workspace_v2` 等新值 | 枚举校验拒绝（只有 `task_workspace`） |
| `nav_label` 超过 16 字或少于 2 字 | 长度校验拒绝 |
| 在插件目录放样式表/脚本文件 | 前端门禁 G-27 拒绝（该目录 0 个 `.css/.scss/.jsx/.tsx`） |
| 想让"某个插件长得不一样" | 做不到：所有工作台长得一样，差异化只允许 `icon_key` / `nav_label` / 声明文案 |

---

## 6. 允许调用的端点清单

> **范围**：一个插件**自身不发起任何调用**（无代码执行，D-14）。本节列的是**插件周边**
> （工作台宿主页面、集成方、验证脚本）被允许调用的适配层端点——除此之外的任何端点、
> 以及任何"直连底层"的写法，都不被允许（§8 边界②）。
> 任务域路由段 `agent` 是历史沿用的路径名，界面与文案中一律称「任务」。

### 6.1 插件域（前缀 `/api/v1/plugins`）

| # | 方法 | 路径 | 用途 | 判权 |
| --- | --- | --- | --- | --- |
| PL1 | GET | `/plugins/market` | 市场列表（分类/关键字/排序/分页；`installable` + 原因逐项返回） | 登录即可 |
| PL2 | GET | `/plugins/market/{plugin_id}` | 详情：说明、能力清单、步骤名、产物说明、配置项、版本历史、**输入定义原样下发** | 登录即可 |
| PL3 | GET | `/plugins/installed` | 已添加列表（当前版本、有无更新、在跑任务数） | 登录即可 |
| PL4 | GET | `/plugins/{plugin_id}/capabilities` | 能力清单（每项含作用/不同意后果/企业开关态）——授权抽屉唯一读源 | 登录即可 |
| PL5 | POST | `/plugins/{plugin_id}/install` | 安装（安装即启用）；`grants` 覆盖全部必需能力 | 登录即可 |
| PL6 | PATCH | `/plugins/{plugin_id}/state` | 停用/启用（不打断在跑任务） | 登录即可 |
| PL7 | DELETE | `/plugins/{plugin_id}` | 移除（历史任务与产物全保留） | 登录即可 |
| PL8 | POST | `/plugins/{plugin_id}/upgrade` | 更新到指定版本 | 登录即可 |
| PL9 | POST | `/plugins/{plugin_id}/rollback` | 恢复上一版本（只保留最近两个版本，只能回退一次） | 登录即可 |
| PL10 | PUT | `/plugins/{plugin_id}/settings` | 保存配置（键必须 ⊆ 声明配置项） | 登录即可 |
| PL11 | GET | `/plugins/{plugin_id}/reviews` | 评价列表与均分 | 登录即可 |
| PL12 | POST | `/plugins/{plugin_id}/reviews` | 提交评价（重复提交覆盖更新） | 登录即可 |
| PL13 | PUT | `/plugins/capability-policy` | 企业能力总开关（仅 3 项企业能力；写后回全量 6 项） | `team:role:manage` |
| PL14 | PUT | `/plugins/{plugin_id}/grants` | 调整**本人**授权（必需能力不可撤） | 登录即可 |

### 6.2 任务域（前缀 `/api/v1/agent`）

| # | 方法 | 路径 | 用途 |
| --- | --- | --- | --- |
| A1 | POST | `/tasks` | 创建任务（带 `plugin_id` 即走插件任务；输入按声明校验） |
| A2 | GET | `/tasks` | 任务列表（可按插件/状态/发起人/时间筛选） |
| A3 | GET | `/tasks/{task_id}` | 任务状态与结果：`status`、`progress`、`result.checklist[]`、`result.artifacts[]`、`result.warnings[]`、`confirm{step_seq,reason,summary}` |
| A4 | GET | `/tasks/{task_id}/steps` | 步骤时间线（标题/状态/摘要） |
| A5 | POST | `/tasks/{task_id}/cancel` | 取消任务（已执行步骤保留） |
| A6 | GET | `/tasks/{task_id}/events` | 进度推送（SSE：`step_started` / `step_finished` / `task_completed`） |
| WF1 | POST | `/tasks/{task_id}/retry` | 继续/重做/重试：`decision` = `continue`（接管点续跑，同任务）/ `redo`（同任务重跑该步及下游）/ `retry_step`（带 `from_step`，新建任务）/ `retry_all`（新建任务） |
| WF2 | PUT | `/tasks/{task_id}/artifacts/{artifact_id}` | 编辑产物并重新导出（仅 `text`/`table`；版本 +1；保存即重新脱敏与渲染） |

### 6.3 产物下载代理（不在 `/api/v1` 下）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/files/artifacts/{token}` | 按 A3/A4 下发的 `download_url` 取内容；令牌自带企业+产物+版本，过期返回 404 |

### 6.4 通用规则

1. **幂等键**：所有写端点（PL5-PL10、PL12-PL14、A1、A5、WF1、WF2）必须携带
   `Idempotency-Key` 请求头（UUID v4，24 小时内同键重放返回首次结果）；缺键 → 20001；
2. **任务状态走状态不走错误码**：步骤失败在 `steps[].failure{reason_text, retryable,
   operator_action}` 与任务 `warnings[]` 里就地展示，接口仍是 `code=0`；
3. **错误码速查**（插件域从 16002 起，16001 已注销不复用）：

| 码 | 场景 | 码 | 场景 |
| --- | --- | --- | --- |
| 16002 | 插件不存在/已下架 | 16008 | 无上一版本可恢复 |
| 16003 | 插件未添加（PL5 不返回它） | 16009 | 前置插件缺失 |
| 16004 | 插件已停用 | 16010 | 状态冲突（并发安装/版本竞争） |
| 16005 | 必需能力未授权（`data.missing_capabilities[]`） | 16011 | 未使用即评价 |
| 16006 | 企业能力未开放 | 16012 | 版本不存在 |
| 16007 | 平台版本过低 | 17001 | 任务不可重试 |

复用既有码：工具未连接 13002 / 授权过期 13003 / 套餐不支持 15001 / 额度软限制 15006 /
无权限 20003 / 参数非法 20001 / 任务不存在 12001。并发超上限（同插件第 4 个任务）
**不用错误码**：返回 `200 + status=queued + queue_position`。

### 6.5 工具调用白名单（`action="tool"` 时可用）

| `tool_type` | 可用 `operation` |
| --- | --- |
| `email` | `send_message` / `list_messages` / `sync` |
| `github` | `create_pull_request` / `create_issue` |
| `wecom` | `send_message` / `list_departments` / `list_members` / `create_group` |
| `feishu` | `send_message` / `create_calendar_event` / `list_members` / `create_doc` |
| `wechat_mp` | `upload_material` / `create_draft` / `publish_article` / `list_articles` |

其中**对外可见**的动作（`email.send_message` / `wecom.send_message` /
`feishu.send_message` / `wechat_mp.publish_article` / `github.create_pull_request`）
必须在声明里带 `confirm`（§1.7）。工具凭据由平台代管，插件与声明都拿不到。

---

## 7. 样例插件 walkthrough：`plg_mail_draft`

### 7.1 选型与理由

三份轻量候选中选 `plg_mail_draft`（邮件起草），因为它是**结构最完整、依赖最少**的一份：

| 维度 | `plg_mail_draft` | `plg_meeting_minutes` | `plg_weekly_report` |
| --- | --- | --- | --- |
| 步骤数 | 2 | 3 | 3 |
| 能力数 | 2 | 2 | 3 |
| 文件输入 | 无 | 有（`doc_ids`） | 无 |
| 检索步骤 | 无 | 无 | 有 |
| 接管点 | 1（发送前确认） | 0 | 1 |
| 导出步骤 | 无 | 无 | 无 |

它覆盖了开发一个插件需要的全部语法点：能力申请、配置项、四种输入控件里的三种、
两种产物类型、工具调用 + 人工接管、核查清单——且没有文件/检索/导出这些需要额外准备的环节。

### 7.2 声明全文（`app/plugins/manifests/plg_mail_draft.json`）

```json
{
  "plugin_id": "plg_mail_draft",
  "name": "邮件起草",
  "summary": "按要点起草一封得体的邮件并发出",
  "description": "告诉工作台收件人、目的与要点，它会起草一封语气得体的邮件，并在发送前把标题、正文与收件人一起给你确认；确认后通过你已连接的邮箱发出，并把发送结果如实告诉你。语气可选正式或简洁，正文会控制在一屏内，方便对方快速读完。草稿会同时留存在任务记录里，需要时可以改完再发一次。",
  "category": "collab",
  "icon_key": "mail",
  "vendor": "official",
  "version": "1.0.0",
  "min_platform_version": "1.0.0",
  "changelog": "首个版本：邮件起草、发送确认、发送结果反馈。",
  "capabilities": [
    { "key": "personal.tool.use", "required": true },
    { "key": "org.external.publish", "required": true }
  ],
  "settings_schema": [
    { "key": "signature", "label": "邮件签名", "type": "text" }
  ],
  "inputs": [
    { "key": "recipient", "label": "收件人", "type": "email", "required": true },
    { "key": "purpose", "label": "邮件目的", "type": "text", "required": true, "max_length": 200 },
    { "key": "key_points", "label": "要说清的要点", "type": "textarea", "required": true, "max_length": 1000 },
    { "key": "tone", "label": "语气", "type": "select", "required": true, "options": ["正式", "简洁"] }
  ],
  "outputs": [
    { "key": "draft", "label": "邮件正文", "type": "text" },
    { "key": "receipt", "label": "发送结果", "type": "text" }
  ],
  "steps": [
    {
      "seq": 1, "title": "起草邮件", "action": "compose",
      "produces": ["draft"], "on_failure": "retry"
    },
    {
      "seq": 2, "title": "确认并发送", "action": "tool",
      "tool_type": "email", "operation": "send_message",
      "produces": ["receipt"], "needs": ["draft"],
      "confirm": {
        "reason": "邮件会发送给收件人，请确认正文与收件人",
        "summary_fields": ["recipient", "purpose"]
      },
      "on_failure": "continue", "timeout_seconds": 60
    }
  ],
  "checklist": [
    { "key": "draft", "name": "邮件正文", "source_step": 1 },
    { "key": "receipt", "name": "发送结果", "source_step": 2 }
  ],
  "ui_extension": {
    "layout": "task_workspace", "nav_label": "邮件起草", "icon_key": "mail", "order": 6
  }
}
```

逐段对照 §1：能力 ②⑥（用办公工具 + 对外发布）；`compose` 步骤产草稿（可重试）；
`tool` 步骤发邮件（对外动作 → 必带 `confirm`；`on_failure=continue` 让发送失败不毁掉整单）；
核查清单 2 项对应 2 个产物。

### 7.3 分步走一遍（从声明到产物）

以下每一步都已实跑（输出见 7.4）。环境：适配层内存后端 + 桩模式（离线，与适配层集成测试同构），
租户 `default`，发起人 `usr_owner`。

**第 0 步 · 环境准备（三件事，缺一不可）**

| 事项 | 为什么 | 做法 |
| --- | --- | --- |
| 套餐含插件能力 | 安装/启用才拦套餐（15001） | 租户订阅升到含 `plugin_market` 的套餐 |
| 企业能力总开关 | `org.external.publish` 属企业能力，需管理员开启 | 团队设置里开启（PL13） |
| 连接办公工具 | 第 2 步要调用邮箱，未连接 → 13002 | 设置里连接邮箱 |

**第 1 步 · 声明过加载器校验（G-26）**

```bash
python deploy/validate_plugins.py
# 期望退出码 0：
# [OK] G-26 通过：6 份声明（plg_content_ops, plg_dev_workbench, plg_hr_admin,
#      plg_mail_draft, plg_meeting_minutes, plg_weekly_report），能力与原语均在白名单内
```

**第 2 步 · 看能力清单（PL4，授权抽屉的数据源）**

```http
GET /api/v1/plugins/plg_mail_draft/capabilities
```

```json
{"items": [
  {"capability_key": "personal.tool.use", "required": true, "org_switch_enabled": true, "...": "..."},
  {"capability_key": "org.external.publish", "required": true, "org_switch_enabled": true, "...": "..."}
]}
```

每项带 `display_name` / `reason` / `denial_effect`（§2.2 由平台生成，声明不写）。

**第 3 步 · 安装（PL5，逐项同意全部必需能力）**

```http
POST /api/v1/plugins/plg_mail_draft/install
Idempotency-Key: 11111111-2222-4333-8444-555555555555

{"grants": {"personal.tool.use": true, "org.external.publish": true}}
```

```json
{"plugin_id": "plg_mail_draft", "status": "enabled", "version": "1.0.0",
 "granted_capabilities": ["org.external.publish", "personal.tool.use"],
 "denied_capabilities": [], "already_installed": false}
```

安装即启用（Q3-3）。重复安装返回 `200 + already_installed=true`，授权与设置不动（D-26）。

**第 4 步 · 发起任务（A1，输入按声明校验）**

```http
POST /api/v1/agent/tasks
Idempotency-Key: 11111111-2222-4333-8444-555555555556

{"conversation_id": "conv_walkthrough", "plugin_id": "plg_mail_draft",
 "inputs": {"recipient": "teammate@example.com", "purpose": "同步本周插件体系进展",
            "key_points": "声明标准已定稿；运行时与市场已上线；开发文档与样例本次交付。",
            "tone": "正式"}}
```

```json
{"task_id": "task_01M5...", "status": "waiting_confirm", "steps_total": 2}
```

第 1 步「起草邮件」在请求内同步执行完成；任务停在**第 2 步的接管点**——
`on_failure` 与 `confirm` 的语义在这里第一次可见：对外发送前必须人看。

**第 5 步 · 确认并继续（WF1，`decision=continue` 同任务续跑）**

```http
POST /api/v1/agent/tasks/{task_id}/retry
Idempotency-Key: 11111111-2222-4333-8444-555555555557

{"decision": "continue"}
```

```json
{"task_id": "task_01M5...", "resumed": true, "retry_of": null, "status": "running"}
```

`resumed=true` + `retry_of=null` 表示"同一任务被暂停后继续"，不是新建任务（D-19）——
核查清单与产物因此永远指向唯一任务。

**第 6 步 · 看结果（A3：状态 + 核查清单 + 产物）**

```http
GET /api/v1/agent/tasks/{task_id}
```

```json
{"status": "completed", "progress": 100,
 "result": {
   "checklist": [
     {"key": "draft", "name": "邮件正文", "status": "checked", "source_step": 1,
      "download_url": "/files/artifacts/..."},
     {"key": "receipt", "name": "发送结果", "status": "checked", "source_step": 2,
      "download_url": "/files/artifacts/..."}
   ],
   "artifacts": [
     {"type": "text", "name": "起草邮件", "download_url": "/files/artifacts/...", "url": null},
     {"type": "text", "name": "receipt", "download_url": "/files/artifacts/...", "url": null}
   ],
   "warnings": []
 }}
```

三态口径（P0-P3-14）：`checked`（已产出）/ `pending`（被跳过，待人工确认）/ `missing`（没有产出）——
不把缺的说成有。

**第 7 步 · 下载产物（平台内容代理）**

```http
GET /files/artifacts/{token}
```

令牌自带企业 + 产物 + 版本并签名，浏览器直接点开、复制到聊天窗口都可用。

**第 8 步（可选）· 改稿并重新导出（WF2）**

`text` / `table` 产物可编辑：保存后版本 +1、重跑脱敏、刷新下载地址，旧版可回看。
`link` / `file` 只读。编辑不改变任务状态、不触发重跑。

### 7.4 验证记录（实跑命令与输出摘要）

以下为本文档定稿前的真实执行记录（内存后端 + 桩模式，离线可复现）。
**每一步都实际跑过，没有"应该可以"**。

| 步骤 | 命令 / 动作 | 实测结果 |
| --- | --- | --- |
| A 加载校验 | `python deploy/validate_plugins.py` | 退出码 **0**；6 份声明全部通过 |
| A' 注册表 | `get_registry()` 后查 `plugin_ids()` / `failures()` | 6 个插件，**加载失败 0 条**；`plg_mail_draft@1.0.0`，步骤 `['起草邮件','确认并发送']` |
| B 能力清单 | `GET /plugins/plg_mail_draft/capabilities` | 2 条：`personal.tool.use(required=true, org_switch=true)`、`org.external.publish(required=true, org_switch=true)` |
| C 安装 | `POST /plugins/plg_mail_draft/install` | `status=enabled`、`version=1.0.0`、`already_installed=false` |
| D 建任务 | `POST /agent/tasks`（按声明 4 项输入） | `status=waiting_confirm`、`progress=50`、`confirm={step_seq:2, reason:'邮件会发送给收件人，请确认正文与收件人', summary:{recipient,purpose}}` |
| E 确认继续 | `POST /agent/tasks/{id}/retry {"decision":"continue"}` | `resumed=true`、`retry_of=null`、`task_id` 不变 |
| F 完成 | `GET /agent/tasks/{id}` | `status=completed`、`progress=100`、`warnings=[]`；核查清单 `draft=checked`、`receipt=checked`；`result.artifacts` 2 个均带 `download_url` |
| G 负样本 1 | 同一 `(plugin_id, version)` 二次注册 | 拒绝，`stage=duplicate`（版本不可变真的会红） |
| H 负样本 2 | 安装时 `grants` 只给 `personal.tool.use` | **16005**，`data.missing_capabilities=["org.external.publish"]`（逐项授权真的会红） |

**离线桩环境的两个附加说明（实测口径，不是设计豁免）**：

1. **工具入参**：平台把任务输入原样交给办公工具连接器，连接器按自己的契约校验入参
   （邮箱连接要求收件人以列表 `to` 给出）。因此验证时在输入里附上 `to: ["teammate@example.com"]`
   即可全绿；**只按声明的 4 项输入执行时，第 2 步按业务文案
   「请求内容不完整，请检查后重试」失败**，`on_failure=continue` 使任务仍以
   `completed` 收尾（`warnings` 多一条「第 2 步「确认并发送」没有完成，结果里会缺少这部分」，
   核查清单 `receipt=missing`）。这条差异已作为待明确项上报（见下方偏差 ③）。
2. **产物正文**：邮箱连接器（桩）只回执发送结果、不回文本摘要，因此 `receipt` 产物
   下载得到 0 字节；草稿产物正文由平台生成能力产出，内容非空。

**实跑中发现的三处实现偏差（均已上报，不影响本文档的字段/端点口径）**：

| # | 偏差 | 影响 | 位置 |
| --- | --- | --- | --- |
| ① | A4 响应未下发 `steps[].artifacts[]` 与 `steps[].failure`：服务层已构造，但端点响应模型（`app/schemas/agent.py` 的步骤条目）未声明这两个字段，被序列化过滤 | 「每步产物」只能经 A3 `result.artifacts[]` / `checklist[].download_url` 获取；`app/schemas/task.py` 已有可复用的产物/失败结构 | `app/api/v1/agent.py` A4 + `app/schemas/agent.py` |
| ② | 产物下载对名称含非拉丁字符的产物返回 500：产物名直接写入 `Content-Disposition` 头，头值按 latin-1 编码，中文名（`compose`/`render_file` 产物必然以步骤标题命名）即崩 | 中文名产物暂不可下载；该端点无测试覆盖 | `app/api/v1/artifact_files.py` |
| ③ | 工具入参原样透传：6 份声明的输入键与各连接器契约键（`email.to` / `feishu.title` / `github.owner,repo,head,base` / `wechat_mp.draft_id+confirmed`）均不一致 | 仅按声明输入执行时，`tool` 步骤在离线环境必然失败；"输入 → 工具入参"的映射职责（平台适配 or 连接器）需要产品侧明确 | `app/services/action_gateway.py` 入参装配 |

> 复跑方式：按 7.3 的 HTTP 顺序执行即可；适配层集成测试（`tests/integration/`）中的
> 插件用例给出了同构的夹具写法（TestClient + 内存后端）。

---

## 8. V1.0 三条边界（红线）

> 出处：PRD `docs/prd-phase3-increment.md` §3.2.1 P0-P3-05 验收标准 ③；设计依据
> `docs/architecture-phase3.md` D-14 / D-17 与 §2.1 硬约束。

1. **不得自定义 UI**：界面一律由平台工作台宿主按声明渲染，必须使用平台组件库，
   **禁止自带样式与脚本**（§5.2）。所有插件工作台"长得一样"，差异化只允许体现在
   `icon_key` / `nav_label` 与声明文案上。
2. **不得直连底层**：插件不持有任何外部地址与凭据，运行期只经平台原语门面接触平台能力；
   声明出现外部地址/凭据/代码类字段即被加载器拒绝（§1.9），运行期走非声明路径会被
   引擎护栏拦下并停步。
3. **V1.0 仅官方内置插件，不开放第三方提交**：`vendor` 只能是 `official`，目录行由平台方
   维护；第三方开放需要重做（隔离运行 + 签名 + 审核），不在 V1.0 范围（D-14）。

### 样例插件"不进市场"的机制

市场可见性由目录行状态控制（`plugin_catalog.status`）：

| 状态 | 市场表现 | 安装 |
| --- | --- | --- |
| `available` | PL1 可见（`installable` 另看套餐） | PL5 可安装 |
| `hidden` / `offline` | PL1 不可见；PL2/PL4/PL5 一律按「该插件已下架」（16002）处理 | 不可经 PL5 安装 |

因此：**内部样例插件**（含你照本文档写的练习插件）把声明文件放进
`app/plugins/manifests/` 即可被加载器校验、被测试与服务层使用；若不进市场，
**不要写 `available` 的目录行**（不写目录行，或写 `status='hidden'`）——它不会出现在
市场列表，也不能经 PL5 安装。V1.0 的安装入口只面向市场上架插件；内部验证按 §7.4 的
复跑方式进行。

---

## 附录 A · 声明速查（一页版）

```
plugin_id        ^plg_[a-z][a-z0-9_]{2,40}$        必填  （与版本一起全局唯一）
name/summary/description   2-32 / 4-60 / 120-800 字  必填  （禁词表）
category         dev|content|hr|data|collab|other   必填
icon_key/vendor  平台图标名 / official              必填
version/min_platform_version   x.y.z                必填
changelog        ≤300 字                            可选
requires_plugins ≤3 个 plugin_id                    可选
capabilities[]   {key: 6 选 1, required: true}      1-6 条，不重复
settings_schema[] {key,label,type: text|select,options≤20}   ≤8 项
inputs[]         {key,label,type: 6 选 1,required,max_length≤8000,options≤20}  1-12 项
outputs[]        {key,label,type: text|link|file|table}  1-12 项
ui_extension     {layout: task_workspace, nav_label 2-16, icon_key, order 1-99}
steps[]          1-8 步，seq 从 1 连续；action 6 选 1；produces/needs ⊆ 产物键；
                 on_failure: abort|continue|skip_and_mark|retry；
                 tool 步必带 tool_type+operation（白名单）；render_file 步必带 render_format；
                 对外动作必带 confirm；timeout_seconds 10-600（缺省 120）
checklist[]      {key: 产物键, name 2-24, source_step}  2-5 项
```

## 附录 B · 字段与端点自查口径

本文档的每一个字段名、枚举值、端点路径均对照下列代码写成；改动这些代码时，
本文档必须同步（否则按"文档有、代码无 / 代码有、文档无"论缺陷）：

| 文档内容 | 代码真源 |
| --- | --- |
| §1 字段表 | `apps/adapter/app/plugins/schema.py`（`PluginManifest` 及各子模型） |
| §1.9 八层校验 | `apps/adapter/app/plugins/loader.py`（`load_manifest`） |
| §2 能力枚举与口径 | `apps/adapter/app/plugins/actions.py`（`CAPABILITY_KEYS` / `CAPABILITY_CATALOG` / 映射表） |
| §3 输入规范 | `schema.py`（`InputSpec`）+ `architecture-phase3.md` §2.1 文件类输入口径 |
| §4 产物类型 | `schema.py`（`OutputSpec`）+ `app/db/repositories/artifact_repo.py` + `renderer.py` |
| §5 界面规范 | `schema.py`（`UiExtension`）+ 前端门禁 G-27（`apps/web/scripts/check-plugin-assets.mjs`） |
| §6 端点清单 | `apps/adapter/app/api/v1/plugins.py`、`app/api/v1/agent.py`、`app/api/v1/artifact_files.py`、`app/api/v1/router.py` |
| §6.5 工具白名单 | `apps/adapter/app/registry/tool_registry.yaml` |
| §7 样例 | `apps/adapter/app/plugins/manifests/plg_mail_draft.json` |
| §8 边界 | `docs/prd-phase3-increment.md` §3.2.1 P0-P3-05 ③ + `docs/architecture-phase3.md` D-14/D-17 |
