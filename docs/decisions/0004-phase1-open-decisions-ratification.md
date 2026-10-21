# 第一阶段遗留决策项拍板记录（D-06 / Grafana / Dify / Q1 / Q2 / D-08）

| 项 | 内容 |
| --- | --- |
| 记录版本 | v1.0 |
| 拍板日期 | 2026-10-21 |
| 拍板人 | 桐鸣科技创始人（授权主理人齐活林代拍） |
| 依据 | 《开发执行总方案 V1.0》5.3 / 3.4 / 3.1；`docs/plan-v1.0.md`；PRD V1.0 Q1-Q7 |
| 关联文档 | `docs/adr/0002-infrastructure-license-compliance.md`（D-06）、`docs/adr/0003-design-token-baseline.md` + `docs/decisions/0003-design-token-baseline-decision-sheet.md`（D-08）、`docs/qa-blockers-closure-appendix-a.md`（P1-1 / P1-2 / P2-4 来源） |

---

## 〇、结论速览

| # | 决策项 | 结论 |
| --- | --- | --- |
| 1 | D-06（Redis→Valkey、MinIO→SeaweedFS） | **正式批准**，ADR-0002 v1.3 生效 |
| 2 | Grafana 协议更正（AGPL-3.0） | **V1.0 移除 Grafana**，监控栈保留 Prometheus；看板列 V1.1 |
| 3 | Dify 修改版 Apache-2.0（多租户 + 商标限制） | **SaaS 不使用 Dify 多租户特性**，租户隔离由自研适配层 + Postgres RLS 实现 |
| 4 | Q1 首批 5 款办公工具 | **邮箱（SMTP/IMAP）、GitHub、企业微信、飞书、微信公众号** |
| 5 | Q2 模型矩阵优先级 | SaaS 首批：通义千问 / 豆包 / DeepSeek；私有化主力：Qwen2.5 系列 + DeepSeek-Coder；GPT-4o / Claude 3.5 列 V1.1 |
| 6 | D-08 前端品牌设计令牌 | Q1 选 A（#2563EB）；Q2 同意不做深色；Q3 同意内置开源中文字体（方案 A） |

---

## 一、D-06 正式批准（ADR-0002 v1.3 生效）

**结论：批准。** Valkey 7.2（BSD-3-Clause，白名单）替代 Redis 7（RSALv2/SSPL 黑名单）；
SeaweedFS 3.80（Apache-2.0，白名单）替代 MinIO（AGPL-3.0 黑名单，且 Docker Hub 仓库已整体下架、
制品不可获取，ADR-0002 §2.2 实测）。

法务/合规签字前提（ADR-0002 §2.2.2 作业规范第 4 条「签字对象必须是可拉取制品」）已满足：
两个镜像的 Registry API 存在性核验通过（valkey 200 / seaweedfs 200），
且 **Docker daemon 已于本机可用（2026-10-21，29.9.1），真实 `docker pull` 实跑通过**。

**本拍板生效后**：ADR-0002 状态由「已接受 —— 待签字」转为「**已签字生效**」；
`deploy/images.lock`、`deploy/.env.example`、两份 compose 中残留的「⚠️ D-06 待决」标注全部清除。

---

## 二、Grafana 裁定：V1.0 移除（QA 附录 A P1-1）

### 2.1 事实

- QA 附录 A 实测：`grafana/grafana` 自 **8.0 起为 AGPL-3.0**，`images.lock` 原标注
  `Apache-2.0` 为**错误**；当前引用版本 `11.1.0` 实为 AGPL-3.0。
- 架构师复核（2026-10-21，Docker 实跑 + Registry API 双通道）：**AGPL 前的最后版本线
  （7.5.x）在 Docker Hub 已不存在** —— `7.5.33 / 7.5.32 / 7.5.31` 均返回 HTTP 404
  （对照组 `11.1.0` 返回 200，证实验证方法有效）。「锁定 AGPL 前版本线」的处置路径
  （即 MinIO 曾采用的范式）**在 Grafana 上已不可行**。

### 2.2 方案比较

| 方案 | 结论 |
| --- | --- |
| A. 锁 7.5.x（Apache-2.0 最后版本线） | ❌ **不可行**：Docker Hub 已无 7.5.x 任何标签（实测 404），仅剩源码分发 |
| B. 保留 11.1.0（AGPL-3.0）作独立进程 | ⚠️ 可行但否：虽不违反「禁止引入核心代码库」的字面规则（独立容器、不修改、不进核心库），但与项目对 AGPL 的一贯保守立场不一致；且私有化离线包需分发该镜像，客户现场的法务解释成本高 |
| C. 换 Apache-2.0 替代看板（如 Perses） | ⏳ V1.1 评估：Perses（CNCF Sandbox，Apache-2.0）方向正确但成熟度对 V1.0 商用监控不足 |
| **D. V1.0 移除 Grafana** | ✅ **采纳** |

### 2.3 决策与理由

**V1.0 交付栈中移除 Grafana**，监控体系保留 **Prometheus（Apache-2.0）**：指标采集 +
告警规则 + 适配层 `/metrics` 端点，足以支撑总方案 6.3「核心服务可用率 ≥ 99.5%」的度量与告警；
可视化看板不属 V1.0 任何验收项（AC-01~AC-16 均不涉及），移除后：

1. 合规零争议（全栈无 AGPL/GPL 进入交付物，squid 灰名单项维持既有独立进程处置不变）；
2. 私有化离线镜像包体积下降约 150MB+；
3. 符合总方案 7.2「底座能力够用即可，不追求底层技术炫技」。

**V1.1 候选**：评估 Perses（Apache-2.0）或按需对 Grafana 走「AGPL 独立进程 + 法务专项评估」。

### 2.4 落点

- `deploy/images.lock`：删除 grafana 条目；
- `deploy/docker-compose.yml`：删除 grafana 服务与 `grafana-data` 卷，注释改为「监控告警（Prometheus，Apache-2.0）」；
- `deploy/.env.example`：删除 `OA_GRAFANA_*` 四项；
- `deploy/offline/README.md`：镜像表删除 grafana 行；
- `docs/architecture-v1.0.md`：§2.1 选型表与 INF6 图同步为「Prometheus」，并在 §8.2 记录 Grafana 移除结论。

---

## 三、Dify 修改版 Apache-2.0 裁定（QA 附录 A P1-2）

### 3.1 事实

QA 附录 A 实测：Dify 的 LICENSE 为 **Apache-2.0 的修改版**，附带两类限制——
**多租户特性限制**（多租户模式不在开源授权范围内）与 **商标/LOGO 限制**。
`images.lock` 原标注裸 `Apache-2.0` 不完整。

### 3.2 决策

**三条规避约束，全部与既有架构原则同向，零架构改动：**

1. **SaaS 版禁用 Dify 多租户特性** —— 租户隔离完全由自研适配层实现（`X-Tenant-Id` 上下文 +
   Postgres RLS，`deploy/init-rls.sql` 已落地）。这同时强化 ADR-0001「适配层唯一原则」：
   Dify 以**单租户形态**被适配层独占调用，多租户语义不出现在适配层以下。
2. **不修改 Dify 任何代码** —— 维持总方案 2.2「仅做必要功能补全与 Bug 修复，不做深度魔改」，
   修改版协议的附加限制只对「修改后分发」与「多租户使用」生效，两者均不命中。
3. **不展示 Dify 品牌标识** —— 前端技术黑盒化（总方案 1.2 第 2 条）已保证用户不可见底座形态；
   产品 About 页与开源声明文件中按总方案 5.3-4 如实列明 Dify 及其协议。

私有化版本为单租户部署，天然不命中多租户限制。

### 3.3 落点

- `deploy/images.lock`：dify-api / dify-web / dify-sandbox 三条目的 `license` 更正为
  `Apache-2.0（修改版：多租户与商标限制，见 ADR-0002 §3）`，note 补充三条规避约束索引；
- ADR-0002 新增第三章记录本裁定；
- `docs/architecture-v1.0.md` §8.2 同步标注。

---

## 四、Q1：首批 5 款办公工具选型（第 9-11 周接入）

**结论：邮箱（SMTP/IMAP）、GitHub、企业微信、飞书、微信公众号。**

| 工具 | 覆盖场景 | 选型理由 |
| --- | --- | --- |
| 邮箱（SMTP/IMAP） | 行政人事工作台（面试邀约、Offer）、研发工作台（通知） | 协议标准无厂商锁定；三插件中两个强依赖；私有化可对接客户自有邮件服务器 |
| GitHub | 前端研发工作台（代码提交、PR） | 总方案 3.4 指定的研发插件核心链路终点；API 成熟度最高 |
| 企业微信 | 行政人事（入职通知）、内容运营（分发） | 国内企业办公事实标准之一；API 完备（通讯录/消息/应用） |
| 飞书 | 行政人事、内容运营（备选分发通道） | 与企微形成双通道覆盖；开放平台 API 完备 |
| 微信公众号 | 内容运营工作台（稿件发布） | 总方案 3.4 指定的内容运营核心发布渠道；API 稳定 |

**未入选及理由**：小红书 / 抖音（内容运营插件 V1.1 目标，发布 API 需企业资质且能力受限，
V1.0 内容运营插件先以公众号 + 企微/飞书分发闭环）；招聘平台（行政人事插件 V1.0 以
「JD 生成 + 简历初评 + 流程生成」辅助为主，平台直连同步列 V1.1）；钉钉（与企微/飞书能力重叠）。

**落地方式**：按既有「工具类型注册表」数据驱动模式扩展（`app/registry/tool_registry.yaml` +
`app/tools/` 新增连接器），T1-T7 端点与契约零改动（架构文档 6.2 / adapter README 7.1）。

---

## 五、Q2：模型矩阵接入优先级（第 9-11 周接入）

**结论：三梯队。**

| 梯队 | 模型 | 部署形态 | 用途 |
| --- | --- | --- | --- |
| **第一梯队（V1.0 SaaS 首批）** | 通义千问（qwen-plus） | 闭源 API | 主对话模型；国内合规、性价比、长上下文稳定 |
| | 豆包（Doubao-pro-32k） | 闭源 API | 主对话模型（负载分担 + 火山引擎价格优势） |
| | DeepSeek（deepseek-chat） | 闭源 API | 长文档/知识库问答主力（长上下文性价比） |
| **第二梯队（V1.0 私有化主力）** | Qwen2.5 系列（72B/32B 按客户硬件） | 开源权重，vLLM 部署 | 私有化离线主模型；与通义千问同家族，提示词与效果基线一致 |
| | DeepSeek-Coder（V2-Lite / 33B） | 开源权重 | 代码辅助场景（P0-05 通用代码辅助） |
| **第三梯队（V1.1 可选）** | GPT-4o、Claude 3.5 | 闭源 API | 出海场景；默认关闭，开通前需数据出境合规评估 |

**原则**：① SaaS 首批全国产（合规 + 成本 + 延迟）；② 私有化与 SaaS 共享 Qwen 家族基线，
同一提示词体系双形态生效；③ 海外模型不做 V1.0 默认能力，避免数据出境合规风险（总方案 5.4）。

**落地方式**：`app/registry/profile_routes.yaml` 能力档位登记（M3 模型目录接口已就绪），
密钥经适配层统一管理，前端只见业务化模型档位名称（如「标准 / 深度思考 / 代码专家」），
不见真实模型名（总方案 1.2 第 2 条）。

---

## 六、D-08 拍板回执（ADR-0003 决策确认单）

创始人授权主理人代拍，对 `docs/decisions/0003-design-token-baseline-decision-sheet.md`
第 5 章三问的回复：

| 问题 | 回复 | 说明 |
| --- | --- | --- |
| Q1 品牌主色 | **选 A：`#2563EB` 沉稳蓝** | 零返工（脚手架已落地此值），企业级最不出错 |
| Q2 深色模式 | **同意 V1.0 不做** | 配套两条硬约束：令牌保留深色命名空间备用；流水线拦截 `dark:` 变体类 |
| Q3 私有化内置中文字体 | **同意方案 A** | 仅私有化镜像内置思源黑体 / Noto Sans SC 同族字体（SIL OFL 1.1，约 8-10MB），按总方案 5.3 走一次引入审核并留档、开源声明列明；SaaS 镜像不内置 |

第 1 章 30 余项决策项（C1-C6 / F1-F5 / S1-S5 / D1-D7 / I1-I5）**全部按推荐动作执行**，
其中唯一对 ADR 建议值的修改（C5 焦点环改实色）随 Q1 选 A 一并生效。
第 3 章缺口清单（G1-G7）纳入组件库深化任务（T03/S2），时限不变。

---

## 七、执行清单（拍板后落地）

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | 本记录落库 + ADR-0002 状态转「已签字生效」并补第三章 | 本次 |
| 2 | ADR-0003 决策单回填拍板回执，状态转「已拍板」 | 本次 |
| 3 | Grafana 移除（images.lock / compose / .env.example / offline README / 架构文档） | 本次 |
| 4 | Dify 协议标注更正（images.lock / ADR-0002 / 架构文档 §8.2） | 本次 |
| 5 | P2-4 适配层 `minio_*` 配置项改名 `object_store_*`（config.py / compose / .env.example / validate_images.py / 架构文档） | 本次 |
| 6 | 工具注册表扩展 5 款连接器骨架（email / github / wecom / feishu / wechat-mp） | Sprint 1 第 9-11 周 |
| 7 | 模型档位登记通义千问 / 豆包 / DeepSeek / Qwen2.5 / DeepSeek-Coder | Sprint 1 第 9-11 周 |
| 8 | 设计令牌 `tokens.css` 落码 + 组件库 G1/G2/G3/G5 硬缺口补齐 | T03 组件库深化 |
| 9 | 字体引入审核（SIL OFL 1.1）+ 开源声明登记 | S4 部署包制作前 |

---

*记录结束。后续任何对上述结论的修订须走 ADR 修订流程，禁止在代码与编排文件中静默变更。*

---

## 八、追认（2026-10-21 晚）：全组件最新版策略

创始人补充拍板：**「所有我们要用到的开源组件，全都要使用最新版」**——
项目目标是快速搭建成熟商业产品、后续在新基线上自研升级，
停留在旧稳定版会持续累积升级债务（双重成本：先还债、再自研）。

**执行口径**（已落 `docs/adr/0002` v1.5 + `deploy/images.lock` version 2）：
- 「最新版」= 当前**最新稳定版的精确标签**（禁止 `latest` 浮动标签，保持可复现）；
- 升级范围与落点：

| 组件 | 原版本 | 升级至 |
| --- | --- | --- |
| Dify api/worker/web | 1.0.0 | **1.17.1**（+ 新增 worker_beat；sandbox 0.2.15；plugin_daemon 0.6.10；SaaS 编排补齐 plugin_daemon/sandbox） |
| Keycloak | 25.0.6 | **26.8.1**（KEYCLOAK_ADMIN → KC_BOOTSTRAP_ADMIN_*） |
| PostgreSQL | 16 | **18.6**（18+ 卷挂载路径改为 /var/lib/postgresql） |
| Valkey | 7.2 | **9.2** |
| Milvus | v2.4.6 | **v3.0.3**（大版本；仍 etcd + S3 接线，补 MINIO_REGION） |
| etcd | v3.5.16 | **v3.7.2**（旧数据目录不兼容，需清卷） |
| SeaweedFS | 3.80 | **4.48**（S3 根路径匿名 403，探针改 master /cluster/status） |
| Prometheus | v2.54.0 | **v3.15.0** |
| squid | 5.2-22.04 | **6.13-25.04** |
| 适配层 Python 依赖 | fastapi 0.146 | **0.156.8** / starlette 1.9.0 / httpx 0.28.1（约束不变） |
| 前端 | Next 14.2 + React 18 + ESLint 8 | **Next 16.4 + React 19.3 + ESLint 9（扁平配置）** |

**升级实跑结论**：全栈重建后适配层真实联调 **8/8 通过**（含 M1/M2 真实 DeepSeek 链路）；
升级过程发现并修复 **6 个新缺陷**（INF-09~13 + INT-05/06，见第一阶段验收报告附录 B）。
