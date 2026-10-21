# 企业级跨生态办公 AI 平台 系统架构设计文档 V1.0.1（第一阶段：底座与适配层攻坚）

> 文档性质：内部交付文档（供交付总监、工程师、QA 消费）
> 需求来源：《开发执行总方案 V1.0》（`docs/plan-v1.0.md》）、《产品需求文档 PRD V1.0》（`docs/prd-v1.0.md`）
> 作者：高见远（Gao）· 架构师
> 日期：2026-10-21
> 版本：V1.0.1
> 评审人：齐活林（交付总监）、许清楚（产品经理）、寇豆码（工程师）、严过关（QA）
> 覆盖阶段：第一阶段（第 1-8 周，底座与适配层攻坚）；第二至四阶段架构扩展点在各章以「后续阶段」标注

### 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V1.0 | 2026-10-21 | 高见远 | 首次发布：五层架构落地设计、技术选型、适配层五大标准接口规范 v1.0、时序图、monorepo 结构、第一阶段任务、依赖清单、共享约定、待明确事项 |
| V1.0.1 | 2026-10-21 | 高见远 | Sprint 0 QA 反馈修订：① 第 3 章补「端点总数 30 + 2 探针」显式声明（消除 29/30 歧义）② 8.2 补登记 postcss / autoprefixer / tailwindcss-animate 三个构建期依赖 ③ 新增 3.0.5「幂等指纹与冲突判定口径」，明确指纹含请求体 SHA-256、同键异体判 20005 冲突且禁止回放（BUG-05 规范根因堵死）④ 3.0.3 的 20005 内部含义补注幂等键请求体不一致。**本次修订不改动任何接口路径、字段名、错误码取值。** |

---

## 目录

1. 总体架构落地设计
2. 技术选型与理由
3. 🔴 自研统一适配层五大标准接口规范 v1.0（最高优先级）
4. 接口设计对待确认问题的兼容策略（Q1/Q2 解耦）
5. 核心时序图
6. 项目代码结构（Monorepo）
7. 第一阶段任务列表（第 1-8 周）
8. 依赖包清单（Python + Node）
9. 共享知识（跨文件约定）
10. 待明确事项

---

# 1. 总体架构落地设计

## 1.1 五层架构落地视图

总方案第二章的五层解耦架构在本阶段的落地划分如下。每层明确「模块划分、服务边界、部署形态」三要素。

```mermaid
graph TB
    subgraph L1["前端交互层（Next.js 应用，无状态，可水平扩展）"]
        FE1[对话主页 / 多会话]
        FE2[知识库页面]
        FE3[插件市场]
        FE4[工作流可视化]
        FE5[团队管理 / 个人中心]
    end
    subgraph L2["场景插件层（S3 启动，第一阶段仅定义标准）"]
        PL1[插件运行时]
        PL2[插件声明 JSON 规范]
    end
    subgraph L3["业务能力层（自研，第一阶段仅基础权限）"]
        BIZ1[身份与权限服务 - Keycloak 封装]
        BIZ2[计费计量 - S2]
        BIZ3[运营后台 - S2]
    end
    subgraph L4["🔴 自研统一适配层（FastAPI，无状态，水平扩展）"]
        AD1[模型服务接口]
        AD2[RAG 服务接口]
        AD3[Agent 服务接口]
        AD4[工具服务接口]
        AD5[用户权限服务接口]
    end
    subgraph L5["底座能力层（Dify Apache-2.0，仅必要补全）"]
        DF1[LiteLLM 多模型调度]
        DF2[RAG 引擎 + Milvus]
        DF3[Agent / Workflow 编排]
        DF4[工具接入框架]
    end
    subgraph L6["基础设施层"]
        INF1[PostgreSQL]
        INF2[Redis]
        INF3[Milvus]
        INF4[Keycloak]
        INF5[MinIO / 对象存储]
        INF6[监控告警 Prometheus + Grafana]
    end
    L1 -->|仅调用适配层 REST/SSE| L4
    L2 -->|仅调用适配层| L4
    L3 -->|仅调用适配层| L4
    L4 -->|Dify REST API| L5
    L4 -->|OIDC / Admin REST| BIZ1
    L5 --> L6
    BIZ1 --> L6
```

## 1.2 服务边界（第一阶段）

| 服务 | 归属层 | 职责边界（做什么/不做什么） | 有状态性 |
| --- | --- | --- | --- |
| web（Next.js） | 前端交互层 | 渲染 UI、维护会话列表等本地交互状态、调用适配层；**不**直连 Dify/Keycloak 以外的任何底座 | 无 |
| adapter（FastAPI） | 自研统一适配层 | 协议转换、参数校验、鉴权转发、错误统一封装、超时/重试/幂等控制；**禁止**任何业务编排逻辑 | 无（状态全部外置 Redis/PostgreSQL） |
| dify-api / dify-worker | 底座能力层 | 多模型调度、RAG 索引与检索、Agent/Workflow 执行 | 有（依赖 PG/Redis/Milvus） |
| keycloak | 业务能力层/基础设施 | 用户、组织、角色、令牌签发与校验 | 有 |
| postgres / redis / milvus / minio | 基础设施层 | 数据持久化、缓存、向量检索、文件存储 | 有 |

**适配层边界红线（强制）**：适配层代码中禁止出现：会话归属判断、权限策略、套餐额度计算、提示词拼装、结果后处理与格式化（超出协议转换必要范围）等业务逻辑。以上能力一律放业务能力层或插件层。

## 1.3 部署拓扑（双模式，同一套代码配置切换）

```mermaid
graph TB
    subgraph SAAS["云端 SaaS 模式（DEPLOY_MODE=saas）"]
        ING[Ingress / Gateway<br/>TLS + WAF]
        WEBA[web Pod ×N<br/>HPA 2-10]
        ADA[adapter Pod ×N<br/>HPA 2-8]
        DFA[dify-api / worker<br/>可独立伸缩]
        PG[(PostgreSQL<br/>主从 + 行级隔离)]
        RD[(Redis 集群)]
        ML[(Milvus 集群)]
        MIO[(MinIO)]
        KC[Keycloak]
    end
    subgraph PRIV["私有化模式（DEPLOY_MODE=private）"]
        COMPOSE[Docker Compose 单节点<br/>离线镜像包]
        WEBP[web 容器]
        ADP[adapter 容器]
        DFP[dify-api / worker 容器]
        PGP[(PostgreSQL 容器)]
        RDP[(Redis 容器)]
        MLP[(Milvus 单机)]
        MIOP[(MinIO 容器)]
        KCP[Keycloak 容器]
        LIC[License 授权校验<br/>环境变量注入]
    end
```

| 维度 | SaaS 多租户 | 私有化单节点 |
| --- | --- | --- |
| 编排 | K8s + HPA 弹性扩缩容 | Docker Compose 一键部署 |
| 代码 | **同一套代码** | **同一套代码** |
| 区分方式 | `DEPLOY_MODE=saas` 环境变量 | `DEPLOY_MODE=private` + License |
| 租户隔离 | PostgreSQL 行级安全策略（RLS）+ 应用层 tenant_id 双保险 | 单租户天然隔离 |
| 外网依赖 | 允许调用闭源模型 API | 全部剥离，纯本地模型 + 本地服务 |
| 容量基线 | 核心服务可用率 ≥ 99.5% | 中小团队/部门级（并发假设见 Q3） |

## 1.4 架构红线：适配层唯一原则（专项强调）

> **本节为一票否决级架构约束，违反即打回重构。**

1. 上层（前端交互层、场景插件层、业务能力层）**只能**调用自研统一适配层的标准接口（`/api/v1/...`），**严禁**以任何形式（HTTP/SDK/源码引用）直连 Dify 原生 API、Milvus、LiteLLM 或 Dify 内部数据库。
2. 底座层 API 文档仅适配层开发可见（总方案 3.1 输出标准），不进入前端、插件、业务层任何代码库与文档。
3. Dify 原生 API 的 URL、请求参数、响应结构、event 名称、错误码等一切形态特征，**禁止**向上层泄露。适配层是唯一的「形态防火墙」。
4. 代码级防线：monorepo 中仅 `apps/adapter` 允许出现 Dify 客户端依赖与 Dify URL 配置；`apps/web`、插件 SDK 的依赖与代码中禁止出现 dify/milvus/litellm 等字样（CI 增加 import 扫描门禁，见 9.5）。
5. 违反后果：若上层直连底座，底座替换（总方案 7.1 风险预案）时将导致大面积重构，与「替换底座仅改适配层，上层零改动」的核心设计目标直接冲突。

---

# 2. 技术选型与理由

## 2.1 选型总表

| 层 | 组件 | 选型 | 开源协议 | 选择理由 |
| --- | --- | --- | --- | --- |
| 底座 | AI 平台底座 | Dify（锁定版本，最小补丁策略） | Apache-2.0 | 总方案指定；RAG/Agent/多模型调度开箱即用；社区活跃；协议宽松可商用 |
| 底座 | 模型路由 | LiteLLM（随 Dify 内置） | MIT | 统一 100+ 模型 API 协议，新增模型仅配置不改代码 |
| 底座 | 向量库 | Milvus 2.x | Apache-2.0 | Dify 官方支持；支持单机/集群双形态，匹配私有化单节点需求 |
| 底座 | 文档解析 | Unstructured（随 Dify 可选） | Apache-2.0 | PDF/Word/Excel 解析事实标准 |
| 适配层 | 运行时 | Python 3.11+ | PSF（BSD 式） | 与 Dify 生态同栈，团队技能复用最大化；异步生态成熟 |
| 适配层 | Web 框架 | FastAPI | MIT | 原生异步、Pydantic 强校验、自动 OpenAPI、SSE 支持完善 |
| 适配层 | HTTP 客户端 | HTTPX | BSD-3-Clause | 异步 + 连接池 + 超时/重试精细控制，适配 Dify 调用 |
| 适配层 | 参数校验 | Pydantic v2 | MIT | 与 FastAPI 一体，接口入参强校验 |
| 前端 | 框架 | Next.js 14（App Router） | MIT | SSR/SSG、路由与性能优化开箱即用；React 生态 |
| 前端 | 语言 | TypeScript | Apache-2.0 | 全端类型安全，接口契约即类型 |
| 前端 | 样式 | Tailwind CSS | MIT | 原子化 CSS，设计令牌落地快 |
| 前端 | 组件库 | shadcn/ui + Radix UI | MIT / MIT | 组件源码级可控、无供应商锁定；满足「100% 统一组件库」硬约束 |
| 身份 | IAM | Keycloak | Apache-2.0 | 总方案指定；OIDC/OAuth2/SAML 全协议；组织/角色/细粒度权限 |
| 数据 | 关系库 | PostgreSQL 16 | PostgreSQL License（类 BSD/MIT） | RLS 多租户隔离、JSON 支持、生态成熟 |
| 缓存 / 幂等 | Valkey 7.2 | **BSD-3-Clause** | Redis 协议兼容的社区分支，永久宽松许可；**已决策替代 Redis 7**（RSALv2/SSPL 触黑名单），适配层业务代码零改动，仅换镜像与连接地址。详见 ADR-0002 |
| 存储 | 对象存储 | MinIO（锁 `RELEASE.2021-04-22`） | Apache-2.0（该版本线） | S3 兼容，可单机可集群，私有化友好。**必须锁定 AGPL 之前的最后版本线并以独立容器运行**（见 8.2 说明与 ADR-0002） |
| 运维 | 监控 | Prometheus + Grafana | Apache-2.0 | 云原生事实标准 |

## 2.2 关键决策论证

**决策 1：适配层采用 Python 3.11+ / FastAPI**
- 与 Dify（Python）同栈，复用其模型协议理解与调试工具链，联调成本最低；
- FastAPI 原生 async + Pydantic，与「协议转换 + 强校验」的适配层定位天然匹配；
- SSE 流式转发有一等公民支持（`StreamingResponse`），满足第 5 章流式链路需求；
- 团队无强理由（如存量 Node 团队）不更换技术栈——若后续团队技能结构变化，适配层为独立服务，可整体替换而不影响上层。

**决策 2：底座「最小补全」策略**
- 只对 Dify 做：必要功能补全（如私有化外网依赖剥离）、Bug 修复、安全加固；
- 不做编排引擎魔改、不做数据库表结构直连——所有定制诉求优先通过适配层消化，其次才评估底座补丁，补丁以独立 patch 集管理，控制上游升级成本（总方案架构核心说明 2）。

**决策 3：模型能力档位化（对上层屏蔽真实模型）**
- 适配层模型服务不暴露真实模型名，仅暴露 `model_profile` 能力枚举（`general` / `code` / `long_context` / `reasoning`）；
- 真实模型名与路由矩阵仅存在于适配层配置与底座，落实附录 A-1「模型名称对终端用户不可见」，同时使 Q2（模型接入优先级）不影响接口契约（详见第 4 章）。

---

# 3. 🔴 自研统一适配层五大标准接口规范 v1.0（最高优先级）

> 本章为第一阶段第 1-2 周关键交付物「接口规范文档」的正式内容，评审定稿后即为契约基线，后续变更走版本化（URL 前缀 `/api/v1`，不兼容变更升 `/api/v2`）。
> **所有命名、参数、错误码均为自研定义，与 Dify 原生接口无任何形式一致性。**
>
> **端点总数声明**：本规范共 **30 个业务端点**——模型服务 M1-M4（4 个）、RAG 服务 R1-R7（7 个）、Agent 服务 A1-A6（6 个）、工具服务 T1-T7（7 个）、用户权限服务 U1-U6（6 个）；另有 `/healthz`、`/readyz` **2 个免鉴权探针**（不计入业务端点）。该总数与 `docs/api/README.md` 第 2.1 节、CI 门禁 `ci-adapter.yml` 的端点数断言（`if len(business) != 30`）三处一致，构成不可歧义的契约基线；**后续新增端点必须同步更新本声明与另两处**。

## 3.0 通用约定

### 3.0.1 统一响应格式

所有接口（含流式接口的控制帧）统一返回：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | int | 0=成功；10000-14999=业务错误；20000-20999=通用业务错误；30000-30999=系统错误（见 3.0.3） |
| message | string | **面向用户可读的业务语言提示**，前端直接展示；成功时为 "success" |
| data | object \| null | 业务数据，失败时为 null |

HTTP 状态码仅用于传输层语义（200 成功 / 400 请求格式错误 / 401 未认证 / 403 无权限 / 404 资源不存在 / 429 限流 / 5xx 服务端错误），业务语义一律以 body.code 为准。

### 3.0.2 鉴权方式

- 所有接口（除健康检查 `/healthz`、`/readyz`）必须携带 `Authorization: Bearer <access_token>`；
- Token 由 Keycloak 签发（OIDC），适配层负责解析校验（签名、过期、租户声明），**适配层不做登录、不签发令牌**（登录流程属业务能力层，第一阶段由 Keycloak 托管页承担）；
- 请求头必填 `X-Tenant-Id`（SaaS 模式）或由 token `tenant_id` 声明推导（私有化模式默认租户 `default`）；
- 服务间调用（适配层 → Dify）使用 Dify 的 dataset/app API Key，存放于适配层配置中心，不暴露给上层。

### 3.0.3 统一错误码表（自研定义，业务/系统分段）

**通用业务错误 20000-20999**

| code | HTTP | message（示例，前端直接展示） | 内部含义 |
| --- | --- | --- | --- |
| 20001 | 400 | 请求内容不完整，请检查后重试 | 参数校验失败 |
| 20002 | 401 | 登录状态已失效，请重新登录 | 令牌缺失/过期/签名失败 |
| 20003 | 403 | 当前账号没有该操作的权限 | 权限点校验失败 |
| 20004 | 404 | 你访问的内容不存在或已被删除 | 资源不存在 |
| 20005 | 409 | 操作冲突，请刷新后重试 | 状态冲突/幂等冲突（含幂等键请求体不一致） |
| 20006 | 429 | 当前使用人数较多，请稍后再试 | 触发限流 |
| 20007 | 400 | 重复的请求已被忽略 | 幂等键命中 |
| 20008 | 413 | 文件超出大小限制（上限 50MB） | 请求体超限 |

**模型服务域 10001-10099**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 10001 | 200 | AI 暂时没有给出回答，请重试 | 生成失败 |
| 10002 | 200 | 当前对话内容过长，请新建会话或精简内容 | 上下文超模型窗口 |
| 10003 | 200 | 该内容暂不支持处理，请调整后重试 | 内容安全拦截 |
| 10004 | 200 | 智能助手繁忙，请稍后再试 | 所选能力档位无可用模型 |
| 10005 | 200 | 当前套餐可用次数不足 | 额度不足（计费，S2） |

**RAG 服务域 11001-11099**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 11001 | 404 | 知识库不存在或已被删除 | 知识库不存在 |
| 11002 | 400 | 暂不支持该格式的文档 | 文档格式不支持 |
| 11003 | 400 | 文档内容过多，请拆分后上传 | 文档解析/大小超限 |
| 11004 | 200 | 文档还在处理中，请稍候查看 | 索引未完成 |
| 11005 | 200 | 检索服务暂时不可用，请重试 | 检索失败 |
| 11006 | 200 | 文档正在处理，无法重复操作 | 索引状态冲突 |

**Agent 服务域 12001-12099**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 12001 | 404 | 任务不存在或已过期 | 任务不存在 |
| 12002 | 200 | 任务执行未能完成，可查看详情后重试 | 执行失败 |
| 12003 | 200 | 任务已取消 | 终态冲突 |
| 12004 | 200 | 任务执行超时，请重试 | 单步/整体超时 |
| 12005 | 200 | 该步骤需要的工具尚未连接 | 依赖工具未授权 |

**工具服务域 13001-13099**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 13001 | 400 | 暂不支持该类工具 | 工具类型未注册 |
| 13002 | 200 | 该工具尚未连接，请先在设置中完成授权 | 连接不存在/未授权 |
| 13003 | 200 | 工具授权已过期，请重新连接 | 授权过期 |
| 13004 | 200 | 工具暂时没有响应，请稍后再试 | 下游调用失败 |
| 13005 | 400 | 该工具不支持此操作 | 操作不在工具能力清单 |
| 13006 | 200 | 第三方工具返回了异常结果 | 下游业务错误 |

**用户权限服务域 14001-14099**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 14001 | 404 | 用户不存在 | 用户不存在 |
| 14002 | 404 | 团队不存在 | 组织不存在 |
| 14003 | 404 | 角色不存在 | 角色不存在 |
| 14004 | 400 | 权限点未定义 | 权限点未注册 |

**系统错误 30001-30099（前端仍仅展示 message，技术细节只进日志）**

| code | HTTP | message（示例） | 内部含义 |
| --- | --- | --- | --- |
| 30001 | 503 | 服务暂时不可用，请稍后再试 | 下游（Dify/Keycloak 等）不可用 |
| 30002 | 504 | 服务响应超时，请重试 | 下游超时 |
| 30003 | 500 | 系统繁忙，请稍后再试 | 适配层内部异常 |
| 30004 | 500 | 服务配置异常，请联系管理员 | 配置缺失/错误 |

> 附录 A-5 落实：以上 message 即终端用户唯一可见的错误文本；code 仅用于内部日志与监控，严禁出现在 UI。

### 3.0.4 超时、重试、幂等总策略

| 策略 | 规则 |
| --- | --- |
| 超时 | 适配层调用 Dify：连接 3s / 读取 60s（非流式）、流式首包 15s；调用 Keycloak：5s；调用第三方工具：按工具注册表 `timeout_ms` 声明（默认 10s） |
| 重试 | 仅对幂等 GET 与下游 5xx/连接错误重试：最多 2 次，指数退避 200ms→800ms，抖动 ±20%；写操作与流式**不重试**（避免重复生成） |
| 幂等 | 所有写接口（创建会话消息、上传文档、创建任务、工具执行）必须携带 `Idempotency-Key` 头（UUID v4，客户端生成，24h 内有效）；适配层以 Redis 去重，命中返回首次结果并置 `code=20007` 提示已忽略（首包响应仍为业务成功语义时返回原结果）。**幂等指纹与冲突判定口径见 3.0.5** |
| 降级 | 下游不可用时返回对应业务错误码（如 10001/30001），禁止透传下游原始错误文本 |

### 3.0.5 幂等指纹与冲突判定口径（实现强制口径）

幂等去重的**唯一正确实现口径**如下，任何实现偏离本口径即视为契约违规：

**第一步：幂等指纹（必须包含请求体摘要）**

```
fingerprint = SHA256(
    method + "\n" +
    path   + "\n" +
    tenant_id + "\n" +
    user_id + "\n" +
    canonical_body_sha256
)
```

| 组成项 | 取值要求 |
| --- | --- |
| `method` | HTTP 方法大写，如 `POST` |
| `path` | 规范化后的请求路径（去除查询串，路径参数按其原始文本参与，不做重写） |
| `tenant_id` | 解析后的租户 ID（私有化模式恒为 `default`） |
| `user_id` | 解析后的用户 ID |
| `canonical_body_sha256` | **请求体规范化 JSON 的 SHA-256 十六进制摘要**（键按字典序排序、无多余空白、UTF-8 编码） |

> ⚠️ **指纹缺 `canonical_body_sha256` 即为实现缺陷**。若指纹仅由 `method:path:tenant:user` 构成，会导致「同一 `Idempotency-Key` + 同 method/path/tenant/user + 不同请求体」被误判为重复请求并**静默回放首次结果**，造成用户看到「成功」但数据未变更的静默数据丢失（已在 Sprint 0 QA 中实测复现，对应 BUG-05）。请求体为空时，摘要取空体规范化的固定值，不得跳过该项。

**第二步：命中判定（两分支，必须区分）**

设请求携带 `Idempotency-Key = K`，Redis 中已存在键 `K` 的首次记录（含首次指纹与首次结果）：

| 情形 | 判定 | 响应 | 说明 |
| --- | --- | --- | --- |
| **K 命中且指纹一致**（同方法同路径同租户同用户同请求体） | 重复请求 | 返回首次结果，`code=20007`「重复的请求已被忽略」（HTTP 400） | 语义等价于安全重放，**允许回放** |
| **K 命中但指纹不一致**（请求体不同，或方法/路径/租户/用户任一不同） | **键复用冲突** | `code=20005`「操作冲突，请刷新后重试」/ HTTP 409 | **严禁回放首次结果**；不得返回首次结果的部分字段，不得按 20007 处理，不得静默忽略 |

**第三步：实现要求**

1. 指纹计算必须发生在**请求体解析完成之后**（可解析体）与**参数校验之后**（校验失败不占用幂等键）；
2. 文件类上传接口的请求体摘要取「文件名 + 文件大小 + 文件内容 SHA-256」，不得直接读入全文入内存计算；
3. 冲突分支（20005）**不得**将首次结果写入或返回，仅返回冲突错误；
4. 幂等键 TTL 24h，键本身为客户端生成的 UUID v4，服务端不做格式之外的语义约束。

**第四步：回归测试要求（纳入 CI 门禁）**

必须包含以下三条幂等用例并全部通过，否则 CI 失败：

| 用例 | 期望 |
| --- | --- |
| 同 key + 同 path + 同 body 连发两次 | 第二次返回 `20007`，且不产生第二次副作用 |
| 同 key + 同 path + **不同 body** | 返回 `20005` / HTTP 409，**不返回首次结果** |
| 同 key + **不同 path** | 返回 `20005` / HTTP 409 |

---

## 3.1 模型服务接口域（Model Service）

**设计原则**：对上层只暴露「能力档位」`model_profile`，不暴露真实模型名（附录 A-1）；上下文（历史消息）由上层传入，适配层负责裁剪保护与格式转换。

### 3.1.1 端点清单

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| M1 | `/api/v1/model/generate` | POST | 单轮/多轮非流式生成 |
| M2 | `/api/v1/model/generate/stream` | POST | 流式生成（SSE，见 3.1.5） |
| M3 | `/api/v1/model/catalog` | GET | 查询可用能力档位目录 |
| M4 | `/api/v1/model/usage` | GET | 用量统计（业务化口径，见 Q7） |

### 3.1.2 M1 非流式生成

请求：

```json
{
  "conversation_id": "conv_01HF8YQZ3K7M9X2P4R6T8V0W",
  "profile": "general",
  "messages": [
    { "role": "user", "content": "帮我把这段话改写得更专业：……" }
  ],
  "context": {
    "history_window": 20,
    "memory_hints": ["常用称呼：王总"]
  },
  "knowledge_base_ids": ["kb_01HF8YQZ3K7M9X2P4R6T8V0W"],
  "options": {
    "temperature": 0.7,
    "max_tokens": 4096
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| conversation_id | string | 是 | 会话 ID（上层维护，用于上下文关联与日志追踪） |
| profile | enum | 是 | 能力档位：`general` 通用 / `code` 代码 / `long_context` 长上下文 / `reasoning` 深度推理 |
| messages | array | 是 | 当前轮消息（user/assistant/system），content 为纯文本或附件引用 |
| context.history_window | int | 否 | 携带的历史轮数，默认 20 |
| context.memory_hints | array | 否 | 上层记忆注入（P0-02，边界见 Q6） |
| knowledge_base_ids | array | 否 | 关联知识库，触发 RAG 增强 |
| options.temperature / max_tokens | number/int | 否 | 缺省按档位默认策略 |

响应 `data`：

```json
{
  "generation_id": "gen_01HF8YR2M4N6P8Q0R2S4T6V8",
  "content": "以下是改写后的专业版本：……",
  "finish_reason": "stop",
  "usage": {
    "prompt_units": 1280,
    "completion_units": 356,
    "total_units": 1636
  },
  "citations": [
    {
      "document_id": "doc_01HF8YQZ3K7M9X2P4R6T8V0W",
      "document_name": "员工手册.pdf",
      "segment_content": "年假申请需提前三个工作日在系统提交……",
      "score": 0.92
    }
  ]
}
```

> `usage` 字段名业务化（units 而非 token），落实附录 A-7；`citations` 用于前端引用展示（FLOW-02）。

### 3.1.3 M3 能力档位目录

请求：`GET /api/v1/model/catalog`

响应 `data`：

```json
{
  "profiles": [
    {
      "profile": "general",
      "display_name": "通用助手",
      "description": "日常问答、文档写作、信息整理",
      "max_context_turns": 20,
      "status": "available"
    },
    {
      "profile": "code",
      "display_name": "编程助手",
      "description": "代码编写、解释与调试",
      "max_context_turns": 10,
      "status": "available"
    },
    {
      "profile": "long_context",
      "display_name": "长文档助手",
      "description": "超长文档阅读与摘要",
      "max_context_turns": 5,
      "status": "available"
    }
  ]
}
```

> Q2 落实：新增模型仅在适配层配置中扩充档位→模型路由映射，目录内容随配置动态变化，接口契约不变。

### 3.1.4 M4 用量统计

请求：`GET /api/v1/model/usage?period=month`

响应 `data`：

```json
{
  "period": "2026-11",
  "conversation_count": 128,
  "document_qa_count": 34,
  "quota": {
    "plan_name": "专业版",
    "total_units": 5000000,
    "used_units": 1234567,
    "remaining_units": 3765433
  }
}
```

> 仅业务化口径（次数/额度），token 精确值不向普通用户展示（附录 A-7，口径待 Q7）。

### 3.1.5 M2 流式生成（SSE）

- 端点：`POST /api/v1/model/generate/stream`，请求体同 M1；
- 响应 `Content-Type: text/event-stream`；
- 自研事件类型（**不复用 Dify event 名称**，落实 A-4/A-6）：

| 事件 | data 载荷 | 说明 |
| --- | --- | --- |
| `start` | `{ "generation_id": "gen_..." }` | 生成开始 |
| `delta` | `{ "text": "增量文本" }` | 文本增量 |
| `citation` | `{ "document_name": "……", "segment_content": "……" }` | 命中引用（随答案位置穿插下发） |
| `done` | `{ "finish_reason": "stop", "usage": {...} }` | 正常结束 |
| `error` | `{ "code": 10001, "message": "AI 暂时没有给出回答，请重试" }` | 业务错误，前端据 message 展示 |

### 3.1.6 与 Dify 原生 API 的映射转换说明

| 适配层接口 | Dify 原生调用 | 转换内容 |
| --- | --- | --- |
| M1 非流式生成 | `POST {DIFY}/v1/chat-messages`（`response_mode: blocking`，`inputs: {}`） | ① `messages` 历史数组 → Dify 会话重建或 `query`+外部上下文注入；② `profile` → 适配层路由表选定 Dify App（不同档位可绑定不同 Dify 应用）；③ Dify `answer`/`metadata.usage` → `content`/`usage`（token→units 字段重命名）；④ Dify `retriever_resources` → `citations` |
| M2 流式生成 | `POST {DIFY}/v1/chat-messages`（`response_mode: streaming`，SSE） | Dify `message`/`agent_message` 事件 → 自研 `delta`；`message_end` → `done`；`message_file` → 忽略或转 `citation`；Dify `error` 事件 → 自研 `error`（重写 code/message） |
| M3 目录 | 无对应 | 纯适配层配置驱动，不调用 Dify |
| M4 用量 | Dify 无原生统计接口（可按 app 维度聚合日志） | 适配层聚合 Redis 计数器（按租户/会话），不依赖 Dify |

**关键点**：Dify 的 `query`、`inputs`、`response_mode`、`answer`、`metadata.usage.total_tokens`、`retriever_resources` 等原生字段名**禁止**出现在适配层对外响应中；对外一律为 `content`、`usage.*_units`、`citations`。

---

## 3.2 RAG 服务接口域（RAG Service）

**设计原则**：仅暴露「知识库-文档」业务模型；分块、向量化、召回参数（附录 A-3）全部封装在适配层配置与底座内。

### 3.2.1 端点清单

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| R1 | `/api/v1/knowledge/bases` | POST | 创建知识库 |
| R2 | `/api/v1/knowledge/bases` | GET | 知识库列表（分页） |
| R3 | `/api/v1/knowledge/bases/{base_id}/documents` | POST | 上传文档（multipart，异步索引） |
| R4 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | GET | 查询文档索引进度 |
| R5 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex` | POST | 重建索引 |
| R6 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | DELETE | 删除文档 |
| R7 | `/api/v1/knowledge/search` | POST | 独立检索（带引用，对话内增强由 M1 触发） |

### 3.2.2 R1 创建知识库

请求：

```json
{
  "name": "人事制度库",
  "description": "公司人事制度与流程文档",
  "visibility": "organization"
}
```

响应 `data`：`{ "base_id": "kb_01HF8YQZ3K7M9X2P4R6T8V0W", "name": "人事制度库", "created_at": "2026-11-02T09:00:00Z" }`

### 3.2.3 R3 上传文档

请求：`multipart/form-data`，字段 `file`（≤50MB，支持 pdf/docx/xlsx/md/txt）、`chunk_policy`（可选，`default`/`fine`，缺省 `default`）。

响应 `data`：

```json
{
  "document_id": "doc_01HF8YQZ3K7M9X2P4R6T8V0W",
  "file_name": "员工手册.pdf",
  "index_status": "processing",
  "progress": 0
}
```

### 3.2.4 R4 索引进度查询

响应 `data`：

```json
{
  "document_id": "doc_01HF8YQZ3K7M9X2P4R6T8V0W",
  "index_status": "completed",
  "progress": 100,
  "segment_count": 246,
  "error_message": null
}
```

`index_status` 枚举：`processing` 处理中 / `completed` 可用 / `failed` 失败（`error_message` 为业务语言）。

### 3.2.5 R7 检索

请求：

```json
{
  "base_id": "kb_01HF8YQZ3K7M9X2P4R6T8V0W",
  "query": "年假怎么申请",
  "top_k": 5,
  "score_threshold": 0.5
}
```

响应 `data`：

```json
{
  "segments": [
    {
      "document_id": "doc_01HF8YQZ3K7M9X2P4R6T8V0W",
      "document_name": "员工手册.pdf",
      "segment_content": "年假申请需提前三个工作日在系统提交……",
      "score": 0.92,
      "position": { "page": 12, "paragraph": 3 }
    }
  ]
}
```

### 3.2.6 与 Dify 原生 API 的映射转换说明

| 适配层接口 | Dify 原生调用 | 转换内容 |
| --- | --- | --- |
| R1 创建知识库 | `POST {DIFY}/v1/datasets` | `name/description` 透传；`visibility` 由适配层落业务库（Dify 无此概念），Dify `id` → `base_id` |
| R3 上传文档 | ① `POST {DIFY}/v1/files/upload`；② `POST {DIFY}/v1/datasets/{dataset_id}/documents`（data_source=file） | Dify `file_id`/`batch` → `document_id`；索引进度由 Dify `indexing_status`（waiting/parsing/cleaning/splitting/indexing/completed/error）**映射**为自研三态 `processing/completed/failed`（原状态名不暴露，落实 A-3） |
| R4 进度查询 | `GET {DIFY}/v1/datasets/{dataset_id}/documents/{document_id}` | Dify `completed_segments/total_segments` → `progress` 百分比 |
| R5 重建索引 | Dify 无原生重建 API（或删除后重传，按 Dify 版本能力） | 适配层编排「查原文档→重新触发索引」，失败返回 11006 |
| R6 删除文档 | `DELETE {DIFY}/v1/datasets/{dataset_id}/documents/{document_id}` | 透传 |
| R7 检索 | `POST {DIFY}/v1/datasets/{dataset_id}/retrieve` | Dify `records[].segment.content/score/document_name` → `segments[]`；Dify 的 `score_threshold/top_k` 经适配层默认策略校准后下发 |

---

## 3.3 Agent 服务接口域（Agent Service）

**设计原则**：仅暴露「任务-步骤」业务模型；Agent 编排细节、工具调用内部协议（附录 A-6）封装在底座；V1.0 任务执行采用「创建 + 轮询 + SSE 推送」双通道，Webhook 回调预留（见 10 章 D-04）。

### 3.3.1 端点清单

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| A1 | `/api/v1/agent/tasks` | POST | 创建任务（异步执行） |
| A2 | `/api/v1/agent/tasks` | GET | 任务列表（按会话/租户过滤） |
| A3 | `/api/v1/agent/tasks/{task_id}` | GET | 查询任务状态与结果 |
| A4 | `/api/v1/agent/tasks/{task_id}/steps` | GET | 查询步骤详情（工作流可视化） |
| A5 | `/api/v1/agent/tasks/{task_id}/cancel` | POST | 取消任务 |
| A6 | `/api/v1/agent/tasks/{task_id}/events` | GET | SSE 订阅任务进度推送 |

### 3.3.2 A1 创建任务

请求：

```json
{
  "conversation_id": "conv_01HF8YQZ3K7M9X2P4R6T8V0W",
  "goal": "把这份需求拆解为技术方案并生成代码文件",
  "profile": "general",
  "tool_grants": ["tool_github", "tool_knowledge"],
  "inputs": {
    "requirement_text": "……"
  },
  "options": { "max_steps": 12, "timeout_seconds": 600 }
}
```

响应 `data`：

```json
{
  "task_id": "task_01HF8YQZ3K7M9X2P4R6T8V0W",
  "status": "running",
  "created_at": "2026-11-02T09:00:00Z",
  "estimated_seconds": 120
}
```

### 3.3.3 A3 任务状态查询

响应 `data`：

```json
{
  "task_id": "task_01HF8YQZ3K7M9X2P4R6T8V0W",
  "status": "completed",
  "progress": 100,
  "current_step": null,
  "result": {
    "summary": "已生成技术方案与 3 个代码文件，并提交到仓库",
    "artifacts": [
      { "type": "file", "name": "LoginForm.tsx", "download_url": "……" },
      { "type": "link", "name": "Pull Request #42", "url": "https://github.com/..." }
    ]
  },
  "usage": { "total_units": 24310 },
  "updated_at": "2026-11-02T09:03:12Z"
}
```

`status` 枚举：`pending` / `running` / `completed` / `failed` / `cancelled`。

### 3.3.4 A4 步骤详情

响应 `data`：

```json
{
  "task_id": "task_01HF8YQZ3K7M9X2P4R6T8V0W",
  "steps": [
    {
      "step_id": "step_1",
      "sequence": 1,
      "title": "理解需求",
      "status": "completed",
      "started_at": "2026-11-02T09:00:01Z",
      "finished_at": "2026-11-02T09:00:12Z",
      "output_summary": "已提取 5 个功能点"
    },
    {
      "step_id": "step_2",
      "sequence": 2,
      "title": "生成技术方案",
      "status": "running",
      "started_at": "2026-11-02T09:00:13Z",
      "finished_at": null,
      "output_summary": null
    }
  ]
}
```

> `title`/`output_summary` 为业务语言步骤描述，由工作流模板配置注入；底座节点类型名不暴露。

### 3.3.5 与 Dify 原生 API 的映射转换说明

| 适配层接口 | Dify 原生调用 | 转换内容 |
| --- | --- | --- |
| A1 创建任务 | `POST {DIFY}/v1/workflows/{workflow_id}/runs`（`response_mode: streaming` 以获取节点事件）或 blocking + 轮询 | `goal/inputs` → Dify `inputs`；`profile/tool_grants` → 适配层选择 workflow 模板与工具配置；Dify `workflow_run_id` → `task_id` |
| A3 状态查询 | `GET {DIFY}/v1/workflows/{workflow_id}/runs/{run_id}` | Dify `status`（running/succeeded/failed/stopped）→ `running/completed/failed/cancelled`；Dify `outputs` → `result`（按模板 schema 提取 artifacts） |
| A4 步骤详情 | 流式运行期间收集 Dify `node_started/node_finished` 事件并落 Redis | Dify 节点 ID/类型 → 自研 `step_id/sequence/title`（映射表存适配层配置）；`node_finished.outputs` → `output_summary` |
| A5 取消任务 | `POST {DIFY}/v1/workflows/{workflow_id}/runs/{run_id}/stop`（或停止流式连接） | 透传 |
| A6 事件推送 | 复用上述流式连接或 Redis Pub/Sub | 适配层自建 SSE 帧格式（`step_started/step_finished/task_completed`） |

---

## 3.4 工具服务接口域（Tool Service）

**设计原则**：**工具类型注册表模式**（详见第 4 章）——新增工具只向注册表登记（工具类型、能力清单、授权方式、端点模板），接口契约零改动。

### 3.4.1 端点清单

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| T1 | `/api/v1/tools/registry` | GET | 查询工具类型注册表（可用工具与能力清单） |
| T2 | `/api/v1/tools/connections` | POST | 发起工具授权连接（OAuth/令牌） |
| T3 | `/api/v1/tools/connections` | GET | 已连接工具列表 |
| T4 | `/api/v1/tools/connections/{conn_id}` | DELETE | 断开工具连接 |
| T5 | `/api/v1/tools/execute` | POST | 执行工具操作 |
| T6 | `/api/v1/tools/sync-jobs` | POST | 创建数据同步任务（如邮件收件箱同步） |
| T7 | `/api/v1/tools/sync-jobs/{job_id}` | GET | 查询同步任务进度 |

### 3.4.2 T1 工具类型注册表

请求：`GET /api/v1/tools/registry`

响应 `data`：

```json
{
  "tools": [
    {
      "tool_type": "email",
      "display_name": "邮箱",
      "category": "communication",
      "auth_type": "oauth2",
      "status": "available",
      "operations": [
        { "operation": "send_message", "display_name": "发送邮件", "params_schema": { "type": "object" } },
        { "operation": "list_messages", "display_name": "查看邮件", "params_schema": { "type": "object" } }
      ],
      "timeout_ms": 10000
    },
    {
      "tool_type": "github",
      "display_name": "代码仓库",
      "category": "development",
      "auth_type": "oauth2",
      "status": "available",
      "operations": [
        { "operation": "create_pull_request", "display_name": "创建合并请求", "params_schema": { "type": "object" } }
      ],
      "timeout_ms": 15000
    }
  ]
}
```

> Q1 落实：注册表内容由配置驱动，首批 5 款选型（无论最终决策为何）只改变注册表数据，**不改变** T1-T7 任何契约。

### 3.4.3 T2 建立工具连接

请求：

```json
{
  "tool_type": "email",
  "auth_code": "oauth_authorization_code",
  "redirect_uri": "https://app.example.com/settings/tools/callback"
}
```

响应 `data`：`{ "connection_id": "conn_01HF8YQZ3K7M9X2P4R6T8V0W", "tool_type": "email", "status": "connected", "expires_at": "2027-02-01T00:00:00Z" }`

### 3.4.4 T5 执行工具操作

请求：

```json
{
  "tool_type": "email",
  "operation": "send_message",
  "params": {
    "to": ["candidate@example.com"],
    "subject": "面试邀约",
    "body": "……"
  }
}
```

响应 `data`：

```json
{
  "execution_id": "exec_01HF8YQZ3K7M9X2P4R6T8V0W",
  "tool_type": "email",
  "operation": "send_message",
  "status": "success",
  "result": { "message_id": "ext_msg_123", "sent_at": "2026-11-02T09:05:00Z" },
  "error": null
}
```

`status` 枚举：`success` / `failed`（`error` 为业务语言）。

### 3.4.5 与 Dify 原生 API 的映射转换说明

| 适配层接口 | Dify 原生调用 | 转换内容 |
| --- | --- | --- |
| T1 注册表 | `GET {DIFY}/console/api/tools`（内置工具清单）+ 适配层自有扩展注册表 | 仅将 Dify 内置工具作为**候选来源**之一；对外展示形态（display_name/operations）完全自研；Dify 未覆盖的办公工具（如企业微信/飞书/邮箱）由适配层扩展连接器实现，仍走 T1-T7 统一出口 |
| T2 连接 | Dify 无面向终端用户的 OAuth 连接管理 | 由适配层自建连接管理（凭据加密存业务库），Dify 侧以 API Key/凭据注入方式使用 |
| T5 执行 | Dify 工具调用发生在 workflow 节点内部，无独立对外执行 API | 独立执行场景由适配层编排为 Dify workflow 调用或直连工具连接器；工具返回的原始错误 → 13006 |

> 说明：Dify 原生工具框架面向「Agent 编排内调用」，不满足「终端用户自助授权 + 独立调用 + 数据同步」三需求，故工具服务域以适配层扩展为主、Dify 内置工具复用为辅——这正是适配层「补全底座不擅长能力」（总方案 3.1-4）的体现。

---

## 3.5 用户权限服务接口域（Identity & Access Service）

**设计原则**：适配层仅做**令牌校验与请求转发**，用户/组织/角色的权威数据在 Keycloak 与其扩展存储；权限策略判断在业务能力层，适配层提供统一校验出口（FLOW-05：业务代码不得自行判权）。

### 3.5.1 端点清单

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| U1 | `/api/v1/identity/verify` | POST | 令牌与权限点校验（内部高频调用） |
| U2 | `/api/v1/identity/profile` | GET | 当前登录用户信息 |
| U3 | `/api/v1/identity/organizations/{org_id}/members` | GET | 组织成员列表 |
| U4 | `/api/v1/identity/organizations/{org_id}/invitations` | POST | 邀请成员 |
| U5 | `/api/v1/identity/organizations/{org_id}/roles` | POST | 创建/分配角色 |
| U6 | `/api/v1/identity/audit-logs` | POST | 审计日志查询 |

### 3.5.2 U1 权限校验

请求：

```json
{
  "permission": "knowledge:document:upload",
  "resource": { "type": "knowledge_base", "id": "kb_01HF8YQZ3K7M9X2P4R6T8V0W" }
}
```

响应 `data`：

```json
{
  "allowed": true,
  "user_id": "usr_01HF8YQZ3K7M9X2P4R6T8V0W",
  "tenant_id": "tnt_01HF8YQZ3K7M9X2P4R6T8V0W",
  "roles": ["org_admin"]
}
```

### 3.5.3 U2 当前用户

响应 `data`：

```json
{
  "user_id": "usr_01HF8YQZ3K7M9X2P4R6T8V0W",
  "display_name": "王倩",
  "email": "wangqian@example.com",
  "tenant": { "tenant_id": "tnt_01HF8YQZ3K7M9X2P4R6T8V0W", "name": "桐鸣科技", "plan": "professional" },
  "organizations": [
    { "org_id": "org_01HF8YQZ3K7M9X2P4R6T8V0W", "name": "研发部", "role": "member" }
  ]
}
```

### 3.5.4 与 Dify / Keycloak 的映射转换说明

| 适配层接口 | 下游调用 | 转换内容 |
| --- | --- | --- |
| U1 校验 | Keycloak token introspection（`POST /realms/{realm}/protocol/openid-connect/token/introspect`）+ 权限点映射表 | Keycloak realm/client roles → 自研权限点（`resource:action` 命名）；KC `active/exp` → `allowed` |
| U2 资料 | Keycloak Admin REST `GET /admin/realms/{realm}/users/{id}` | KC `firstName/email/attributes` → 自研字段；租户与套餐信息取自业务库（适配层只做组装转发） |
| U3 成员列表 | Keycloak Admin REST groups/users | KC group 成员 → 组织成员 |
| U4 邀请成员 | 适配层扩展（Keycloak 无原生邀请流） | 生成邀请令牌 → Keycloak 建用户 → 业务库落审计 |
| U5 角色管理 | Keycloak Admin REST roles/composites | 透传 + 权限点注册表校验（14004） |
| U6 审计查询 | 业务库审计表（适配层不持有审计逻辑，仅查询出口） | 字段透传，脱敏由业务层写入时完成 |

> 说明：Dify 无用户权限域对应能力，本域以 Keycloak 为权威源，适配层做协议统一。

---

## 3.6 适配层内部类图

```mermaid
classDiagram
    class ApiResponse {
        +int code
        +string message
        +dict data
        +ok(data) ApiResponse$
        +fail(code, message) ApiResponse$
    }
    class IdempotencyManager {
        +RedisClient redis
        +check_or_set(key, ttl) bool
        +get_result(key) dict
        +store_result(key, result) void
    }
    class AuthContext {
        +string user_id
        +string tenant_id
        +list roles
        +resolve(token) AuthContext$
    }
    class DifyClient {
        +str base_url
        +str api_key
        +post(path, payload, timeout) dict
        +post_stream(path, payload) AsyncIterator
        +get(path, params) dict
        +delete(path) dict
    }
    class KeycloakClient {
        +str base_url
        +str realm
        +introspect(token) dict
        +get_user(user_id) dict
        +list_group_members(org_id) list
    }
    class ModelService {
        +DifyClient dify
        +generate(req) dict
        +generate_stream(req) AsyncIterator
        +get_catalog() dict
        +get_usage(ctx) dict
    }
    class RagService {
        +DifyClient dify
        +create_base(req) dict
        +upload_document(base_id, file, policy) dict
        +get_index_status(base_id, doc_id) dict
        +reindex(base_id, doc_id) dict
        +search(req) dict
    }
    class AgentService {
        +DifyClient dify
        +RedisClient redis
        +create_task(req) dict
        +get_task(task_id) dict
        +list_steps(task_id) dict
        +cancel_task(task_id) dict
    }
    class ToolService {
        +dict tool_registry
        +list_registry() dict
        +create_connection(req) dict
        +execute(req) dict
    }
    class IdentityService {
        +KeycloakClient kc
        +verify(token, permission, resource) dict
        +get_profile(ctx) dict
        +query_audit_logs(req) dict
    }
    class ErrorMapper {
        +to_business_error(dify_status, dify_body) tuple$
    }
    ModelService --> DifyClient
    RagService --> DifyClient
    AgentService --> DifyClient
    IdentityService --> KeycloakClient
    ModelService ..> ApiResponse
    RagService ..> ApiResponse
    AgentService ..> ApiResponse
    ToolService ..> ApiResponse
    IdentityService ..> ApiResponse
    ModelService ..> IdempotencyManager
    AgentService ..> IdempotencyManager
    ToolService ..> IdempotencyManager
    ModelService ..> AuthContext
    AgentService ..> AuthContext
    ToolService ..> AuthContext
    IdentityService ..> AuthContext
    ModelService ..> ErrorMapper
    RagService ..> ErrorMapper
    AgentService ..> ErrorMapper
```

---

# 4. 接口设计对待确认问题的兼容策略（Q1/Q2 解耦）

## 4.1 Q1（首批 5 款办公工具选型）→ 工具类型注册表模式

- **注册表数据驱动**：每个工具类型登记 `tool_type / display_name / category / auth_type / operations[] / timeout_ms / connector_impl`；
- **新增工具流程**：实现连接器（connector）→ 注册表登记 → 上线，T1-T7 端点、请求响应结构、错误码**零改动**；
- **前端自描述**：T1 返回注册表全量，插件市场/设置页按注册表动态渲染工具卡片与授权引导，不硬编码工具列表；
- **对 Q1 三个子问题的兼容**：(a) 无论从插件依赖反推还是独立通用清单，均表现为注册表条目；(b) 替换/增补工具仅改注册表；(c) 内容运营发布范围仅影响 `email`/`wechat_official` 等具体工具的 `operations` 清单。

## 4.2 Q2（模型矩阵接入优先级）→ 模型类型枚举 + 能力描述

- **枚举稳定**：`profile`（general/code/long_context/reasoning）为契约枚举，新增真实模型不新增枚举；
- **路由后置**：适配层配置「档位 → 模型/应用」路由表与降级链（如 general 主路由豆包、降级通义千问；本地模型用于 private 模式）；
- **容量与成本后置**：模型成本、token 预算（Q2-b）由适配层聚合计量层消费，不影响对话接口；
- **默认模型后置**：按场景固定默认档位（对话 general、代码场景 code），用户侧不展示模型信息（附录 A-1）；
- **Q2 结论未定时的默认假设**：S1 按 PRD 建议（豆包 + Qwen2.5 + DeepSeek-Coder）配置路由，决策变化仅改路由表配置。

## 4.3 其余待决项与架构的关系

| PRD 待决项 | 架构兼容策略 |
| --- | --- |
| Q3 私有化环境假设 | 部署层配置化（容量参数、GPU 开关、离线包分发），不影响接口契约 |
| Q4 插件依赖与优先级 | 插件层设计（S3），第一阶段仅冻结插件 JSON 规范与运行时接口草案 |
| Q5 计费定价 | 计量字段（units/counts）已在 3.1.4 设计，定价策略属业务配置 |
| Q6 记忆边界 | `context.memory_hints` 字段预留，记忆存储与策略属业务层 |
| Q7 用量展示口径 | 3.1.4 已按「业务化用量」设计，口径确认后仅调整展示层 |

---

# 5. 核心时序图

## 5.1 非流式对话请求全链路

```mermaid
sequenceDiagram
    actor U as 终端用户
    participant W as Web前端
    participant A as 适配层(ModelService)
    participant D as Dify底座
    participant LLM as 大模型

    U->>W: 输入问题并发送
    W->>A: POST /api/v1/model/generate<br/>Authorization: Bearer token<br/>Idempotency-Key
    A->>A: 令牌校验 / 参数校验(Pydantic)<br/>幂等键检查(Redis)
    A->>A: profile → 路由表选择 Dify App
    A->>D: POST /v1/chat-messages<br/>(response_mode=blocking)
    D->>LLM: 调用模型生成
    LLM-->>D: 生成结果 + 用量
    D-->>A: {answer, metadata.usage, retriever_resources}
    A->>A: 字段转换<br/>answer→content / tokens→units / retriever→citations<br/>Dify错误→自研错误码+业务语言message
    A-->>W: {code:0, message:"success", data:{...}}
    W-->>U: 渲染回答与引用来源
```

## 5.2 RAG 文档上传与索引构建链路

```mermaid
sequenceDiagram
    actor U as 终端用户
    participant W as Web前端
    participant A as 适配层(RagService)
    participant S as MinIO
    participant D as Dify底座
    participant V as Milvus

    U->>W: 选择文档上传
    W->>A: POST /api/v1/knowledge/bases/{id}/documents<br/>multipart/form-data
    A->>A: 格式/大小校验<br/>权限校验(知识库归属)
    A->>S: 存储原始文件
    A->>D: POST /v1/files/upload
    A->>D: POST /v1/datasets/{dataset_id}/documents
    A-->>W: {code:0, data:{document_id, index_status:"processing"}}
    W-->>U: 展示索引进度条
    D->>D: 解析 → 清洗 → 分块 → 向量化
    D->>V: 写入向量与元数据
    W->>A: 轮询 GET .../documents/{doc_id}
    A->>D: GET /v1/datasets/{ds}/documents/{doc}
    D-->>A: indexing_status=completed
    A->>A: 状态映射 completed→completed<br/>progress 计算
    A-->>W: {index_status:"completed", progress:100}
    W-->>U: 索引完成，可开始提问
```

## 5.3 流式对话（SSE）链路

```mermaid
sequenceDiagram
    actor U as 终端用户
    participant W as Web前端
    participant A as 适配层(ModelService)
    participant D as Dify底座

    U->>W: 输入问题并发送
    W->>A: POST /api/v1/model/generate/stream<br/>Accept: text/event-stream
    A->>A: 校验 + 幂等检查
    A->>D: POST /v1/chat-messages<br/>(response_mode=streaming)
    A-->>W: HTTP 200 建立 SSE 通道
    A->>W: event: start {generation_id}
    D-->>A: SSE event: message (增量文本)
    A->>W: event: delta {text}
    D-->>A: SSE event: message_file / 引用元数据
    A->>W: event: citation {document_name, segment_content}
    D-->>A: SSE event: message_end {usage}
    A->>W: event: done {finish_reason, usage}
    W-->>U: 逐字渲染回答，引用角标可点击
    Note over A,D: 若 Dify 返回 error 事件<br/>A 转换为 event: error {code, message}<br/>业务语言直接可展示
```

---

# 6. 项目代码结构（Monorepo）

## 6.1 目录树

```
officeai/                              # Monorepo 根目录
├── apps/
│   ├── adapter/                       # 🔴 自研统一适配层（唯一允许接触 Dify 的应用）
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                # FastAPI 入口：中间件、异常处理器、路由挂载
│   │   │   ├── core/
│   │   │   │   ├── config.py          # 环境变量与配置模型（Pydantic Settings）
│   │   │   │   ├── security.py        # 令牌解析、租户上下文
│   │   │   │   ├── errors.py          # 错误码枚举与 ApiResponse 工厂
│   │   │   │   ├── idempotency.py     # Redis 幂等键管理
│   │   │   │   ├── ratelimit.py       # 限流
│   │   │   │   └── logging.py         # 结构化日志（JSON + trace_id）
│   │   │   ├── middleware/
│   │   │   │   ├── trace.py           # trace_id 注入与透传
│   │   │   │   └── error_handler.py   # 统一异常→ApiResponse 兜底
│   │   │   ├── api/
│   │   │   │   └── v1/
│   │   │   │       ├── router.py      # 路由汇总
│   │   │   │       ├── model.py       # 模型服务端点 M1-M4
│   │   │   │       ├── rag.py         # RAG 服务端点 R1-R7
│   │   │   │       ├── agent.py       # Agent 服务端点 A1-A6
│   │   │   │       ├── tool.py        # 工具服务端点 T1-T7
│   │   │   │       └── identity.py    # 用户权限端点 U1-U6
│   │   │   ├── schemas/
│   │   │   │   ├── common.py          # ApiResponse、分页
│   │   │   │   ├── model.py
│   │   │   │   ├── rag.py
│   │   │   │   ├── agent.py
│   │   │   │   ├── tool.py
│   │   │   │   └── identity.py
│   │   │   ├── services/              # 协议转换服务（唯一允许写 Dify/Keycloak 调用）
│   │   │   │   ├── model_service.py
│   │   │   │   ├── rag_service.py
│   │   │   │   ├── agent_service.py
│   │   │   │   ├── tool_service.py
│   │   │   │   └── identity_service.py
│   │   │   ├── clients/
│   │   │   │   ├── dify_client.py     # Dify HTTP 客户端（重试/超时/SSE 解析）
│   │   │   │   └── keycloak_client.py # Keycloak 客户端
│   │   │   ├── registry/
│   │   │   │   ├── profile_routes.yaml  # 能力档位→Dify App 路由表
│   │   │   │   └── tool_registry.yaml   # 工具类型注册表（Q1 数据驱动）
│   │   │   └── tools/                 # 工具连接器实现（Q1 新增工具只加这里）
│   │   │       ├── base.py
│   │   │       ├── email_connector.py
│   │   │       └── github_connector.py
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   └── integration/           # 契约测试（对 Dify mock 服务）
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   └── web/                           # 前端（Next.js）
│       ├── src/
│       │   ├── app/                   # App Router 页面
│       │   │   ├── (auth)/            # 登录/回调
│       │   │   ├── (main)/
│       │   │   │   ├── chat/          # 对话主页
│       │   │   │   ├── knowledge/     # 知识库
│       │   │   │   ├── plugins/       # 插件市场（S3）
│       │   │   │   ├── workflows/     # 工作流可视化（S3）
│       │   │   │   ├── team/          # 团队管理
│       │   │   │   └── profile/       # 个人中心
│       │   │   └── layout.tsx
│       │   ├── components/
│       │   │   ├── ui/                # 统一组件库（shadcn/ui 封装，全产品唯一来源）
│       │   │   ├── chat/              # 对话相关组件
│       │   │   └── knowledge/         # 知识库相关组件
│       │   ├── lib/
│       │   │   ├── api-client.ts      # 适配层 API 客户端（唯一出口）
│       │   │   ├── types.ts            # 接口类型定义（与接口规范同步）
│       │   │   └── business-errors.ts # code→业务语言映射兜底
│       │   ├── hooks/                 # use-chat-stream / use-upload 等
│       │   └── styles/globals.css
│       ├── public/
│       ├── Dockerfile
│       └── package.json
├── deploy/
│   ├── docker-compose.yml             # 私有化一键部署
│   ├── docker-compose.saas.yml        # SaaS 本地联调
│   ├── k8s/                           # SaaS 集群 manifests（S4）
│   └── offline/                       # 离线镜像打包脚本（S4）
├── docs/
│   ├── plan-v1.0.md
│   ├── prd-v1.0.md
│   ├── architecture-v1.0.md           # 本文档
│   ├── api/                           # 接口规范（OpenAPI，从适配层生成）
│   └── adr/                           # 架构决策记录
├── .github/
│   └── workflows/
│       ├── ci-adapter.yml             # lint + test + 覆盖率门禁
│       ├── ci-web.yml
│       └── dependency-scan.yml        # SCA 协议扫描（月度+PR）
├── .editorconfig
├── .gitignore
├── package.json                       # monorepo 根（workspace 声明）
└── README.md
```

## 6.2 关键文件职责说明

| 文件 | 职责 | 约束 |
| --- | --- | --- |
| `apps/adapter/app/services/*` | 协议转换的唯一实现位置 | 仅协议转换/校验/错误封装，禁业务逻辑 |
| `apps/adapter/app/clients/dify_client.py` | 全仓库唯一 Dify 调用出口 | 集中管理超时/重试/连接池 |
| `apps/adapter/app/registry/*.yaml` | 档位路由表、工具注册表 | 数据驱动，改配置不改代码 |
| `apps/web/src/lib/api-client.ts` | 前端唯一适配层调用出口 | 禁止出现任何底座域名/路径 |
| `apps/web/src/components/ui/*` | 全产品统一组件库 | 插件前端组件只允许从这里引用 |
| `deploy/docker-compose.yml` | 双模式部署入口 | `DEPLOY_MODE` 环境变量切换 |

---

# 7. 第一阶段任务列表（第 1-8 周）

## 7.1 任务总表（5 项，按依赖排序）

| 任务 ID | 任务名称 | 周次 | 执行角色 | 交付物 | 依赖 |
| --- | --- | --- | --- | --- | --- |
| T01 | 底座部署验证 + 项目基础设施 + 接口规范定稿 | 第 1-2 周 | 工程师（寇豆码）+ 架构师（高见远）+ 主理人（齐活林）组织评审 | ① 可运行 Dify 底座环境（含首批 3 模型路由假设）② monorepo 脚手架、CI/lint/SCA 门禁 ③ 接口规范 v1.0 评审定稿 | — |
| T02 | 适配层核心框架 + 五大标准接口开发 | 第 3-5 周 | 工程师（寇豆码） | 适配层服务（五大接口域全部端点）、单元测试（核心接口覆盖率 ≥ 80%）、OpenAPI 文档 | T01 |
| T03 | 前端基础框架 + 统一组件库 | 第 3-5 周 | 工程师（寇豆码） | Next.js 框架、shadcn/ui 封装组件库、api-client、SSE 流式渲染组件、设计令牌 | T01 |
| T04 | 适配层与底座全量联调 + 基础权限体系 | 第 5-7 周 | 工程师 + QA（严过关） | 联调测试报告（五大接口域对 Dify/Keycloak）、Keycloak 基础权限（用户/组织/角色/校验出口）、契约测试 | T02 |
| T05 | 前端对话与知识库页面 + 全链路 Demo + 第一阶段验收 | 第 6-8 周 | 工程师 + QA + 主理人 | 可演示 Demo（完整对话 + 文档问答 + 引用溯源）、全链路测试报告、第一阶段验收报告 | T03、T04 |

## 7.2 排期甘特图

```mermaid
gantt
    title 第一阶段任务排期（第 1-8 周，2026-11-02 启动）
    dateFormat YYYY-MM-DD
    axisFormat %m-%d
    section T01 底座与基建
    Dify 底座部署与功能验证 :t01a, 2026-11-02, 5d
    项目脚手架与 CI 门禁 :t01b, 2026-11-02, 7d
    接口规范评审定稿 :milestone, t01c, 2026-11-09, 0d
    section T02 适配层开发
    核心框架与模型/RAG 接口 :t02a, 2026-11-09, 8d
    Agent/工具/权限接口 :t02b, after t02a, 8d
    单元测试与覆盖率补齐 :t02c, after t02b, 4d
    section T03 前端框架
    框架搭建与组件库 :t03a, 2026-11-09, 8d
    api-client 与流式组件 :t03b, after t03a, 8d
    section T04 联调与权限
    五大接口域联调 :t04a, 2026-11-23, 8d
    Keycloak 基础权限 :t04b, 2026-11-23, 8d
    section T05 Demo 与验收
    对话与知识库页面 :t05a, 2026-11-30, 8d
    全链路打通与 Demo :t05b, after t05a, 5d
    第一阶段验收 :milestone, t05c, 2026-12-25, 0d
```

## 7.3 任务依赖图

```mermaid
graph LR
    T01[T01 底座与基础设施<br/>第1-2周] --> T02[T02 适配层开发<br/>第3-5周]
    T01 --> T03[T03 前端框架<br/>第3-5周]
    T02 --> T04[T04 联调与基础权限<br/>第5-7周]
    T03 --> T05[T05 Demo 与验收<br/>第6-8周]
    T04 --> T05
```

## 7.4 阶段验收对照（总方案第一阶段验收标准）

| 总方案验收项 | 对应任务 | 验证方式 |
| --- | --- | --- |
| 底座核心功能跑通 | T01 | 底座验证清单签字 |
| 接口规范全员评审通过 | T01 | 评审会议纪要（本文档第 3 章为评审对象） |
| 所有原子能力均可通过适配层标准接口调用 | T02 + T04 | 联调测试报告：五大接口域用例 100% 通过 |
| 前端到适配层到底座全链路跑通，可完成完整对话与文档问答 | T05 | Demo 演示 + QA 全链路测试报告 |

---

# 8. 依赖包清单

## 8.1 Python（适配层 `apps/adapter`）

| 包 | 版本约束 | 协议 | 用途 |
| --- | --- | --- | --- |
| fastapi | ^0.115 | MIT | Web 框架 |
| uvicorn[standard] | ^0.30 | BSD-3-Clause | ASGI 服务器 |
| pydantic | ^2.7 | MIT | 参数校验与配置 |
| pydantic-settings | ^2.3 | MIT | 环境变量配置管理 |
| httpx | ^0.27 | BSD-3-Clause | Dify/Keycloak 异步调用 |
| redis | ^5.0 | MIT | 幂等键/限流/缓存（Python 客户端，通过 RESP 协议对接 Valkey） |
| sqlalchemy | ^2.0 | MIT | 业务库 ORM（审计等轻量场景） |
| asyncpg | ^0.29 | Apache-2.0 | PostgreSQL 异步驱动 |
| python-jose[cryptography] | ^3.3 | MIT | JWT 解析 |
| tenacity | ^8.2 | Apache-2.0 | 重试策略 |
| structlog | ^24.1 | MIT + Apache-2.0 双许可 | 结构化日志 |
| slowapi | ^0.1.9 | MIT | 限流（基于 limits，MIT） |
| python-multipart | ^0.0.9 | Apache-2.0 | multipart 文件上传 |
| pyyaml | ^6.0 | MIT | 注册表/路由表 YAML 解析 |
| pytest / pytest-asyncio / pytest-cov | ^8.0 / ^0.23 / ^4.1 | MIT | 测试框架 |
| ruff / mypy | ^0.5 / ^1.10 | MIT | Lint 与类型检查 |

> 全量 MIT/Apache-2.0/BSD-3-Clause，无 GPL/AGPL/SSPL，满足总方案 5.3 白名单。
>
> **基础设施组件说明**：`redis` 为 Python 客户端库（MIT），服务端为 **Valkey 7.2（BSD-3-Clause）**，二者通过标准 RESP 协议通信，非代码链接；镜像与协议决策见 ADR-0002。

## 8.2 Node（前端 `apps/web`）

| 包 | 版本约束 | 协议 | 用途 |
| --- | --- | --- | --- |
| next | ^14.2 | MIT | 应用框架 |
| react / react-dom | ^18.2 | MIT | UI 运行时 |
| typescript | ^5.4 | Apache-2.0 | 类型系统 |
| tailwindcss | ^3.4 | MIT | 样式 |
| @radix-ui/* | ^1.x（按需） | MIT | 无样式组件基元 |
| class-variance-authority / clsx / tailwind-merge | ^0.7 / ^2.1 / ^2.5 | MIT / MIT / MIT | 样式变体与类名合并 |
| lucide-react | ^0.4xx | ISC | 图标 |
| zustand | ^4.5 | MIT | 客户端状态 |
| @tanstack/react-query | ^5.5 | MIT | 服务端状态与缓存 |
| react-hook-form + @hookform/resolvers | ^7.5 / ^3.9 | MIT | 表单 |
| zod | ^3.23 | MIT | 前端参数校验 |
| sonner | ^1.5 | MIT | 全局提示 |
| date-fns | ^3.6 | MIT | 日期处理 |
| eslint / prettier | ^8.57 / ^3.3 | MIT | Lint 与格式化 |
| postcss | ^8.4.38 | MIT | CSS 处理（构建期） |
| autoprefixer | ^10.4.19 | MIT | CSS 浏览器前缀补全（构建期） |
| tailwindcss-animate | ^1.0.7 | MIT | Tailwind 动画插件（构建期） |

> 全量 MIT/Apache-2.0/ISC，无传染性协议。
>
> **构建期依赖说明**：`postcss` / `autoprefixer` / `tailwindcss-animate` 三项**仅在构建阶段参与 CSS 编译与前缀补全，不进入运行时产物**（不随 `next build` 输出到客户端 bundle，也不随容器镜像进入运行时依赖），故与运行时依赖分列标注。三者均为 MIT 协议，无 GPL/AGPL/SSPL 风险。
> **MinIO 版本线说明（基础设施，非代码依赖）**：MinIO 自 2021 年起主线为 AGPL-3.0，须锁定 AGPL 之前的最后版本线（RELEASE.2021-04-22 等）或以**独立进程/容器**方式运行（灰名单规则：独立进程调用、不引入核心代码）。二选一方案列入待确认项 D-06。

---

# 9. 共享知识（跨文件约定）

## 9.1 命名规范

| 对象 | 规范 | 示例 |
| --- | --- | --- |
| API 路径 | `/api/v1/{domain}/{resource}`，小写连字符 | `/api/v1/knowledge/bases` |
| 资源 ID | 前缀 `类型缩写_` + ULID | `conv_01HF8YQZ...`、`kb_01HF8...`、`task_01HF8...` |
| JSON 字段 | snake_case | `generation_id`、`index_status` |
| 枚举值 | 小写下划线 | `long_context`、`processing` |
| 前端变量/组件 | camelCase / PascalCase | `useChatStream`、`ChatWindow` |
| 环境变量 | 大写 + `OA_` 前缀 | `OA_DIFY_BASE_URL` |

## 9.2 统一响应与错误码

- 所有接口返回 `{code, message, data}`（见 3.0.1）；
- 错误码分段：0 成功 / 10001-14999 领域业务错误 / 20001-20999 通用业务错误 / 30001-30999 系统错误（见 3.0.3）；
- 前端**只展示 message**，严禁展示 code、堆栈、下游原始错误；
- 新增错误码必须先在本文档登记，后进代码（错误码注册制）。

## 9.3 日志规范

- 结构化 JSON 日志（structlog），必填字段：`timestamp / level / trace_id / tenant_id / user_id / event`；
- `trace_id` 由接入层生成，全链路透传（适配层 → Dify 请求头 `X-Trace-Id`）；
- 日志分级：ERROR=需告警的系统/下游错误；WARN=重试/降级；INFO=关键业务节点；DEBUG=开发调试（生产关闭）；
- **日志中禁止记录**：用户令牌、工具授权凭据、文档正文全文。

## 9.4 Git Flow 分支策略

- `main`：生产分支，仅接受 `release/*` 与 `hotfix/*` 合并，打 tag 发布；
- `develop`：集成分支，功能完成后合入；
- `feature/*`：功能分支（如 `feature/adapter-rag-api`），开发完成提 MR，Code Review 通过后合 develop；
- `release/*`：版本冻结，仅 bugfix；`hotfix/*`：生产热修；
- 提交信息：`type(scope): 说明`（feat/fix/refactor/test/docs/chore）。

## 9.5 环境变量规范

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `OA_ENV` | `development` / `production` | 运行环境 |
| `OA_DEPLOY_MODE` | `saas` / `private` | 部署模式（双模式切换开关） |
| `OA_DIFY_BASE_URL` | `http://dify-api:5001` | 底座地址（仅适配层可见） |
| `OA_DIFY_DEFAULT_APP_KEY` | `app-xxx` | 底座应用密钥 |
| `OA_KEYCLOAK_URL` / `OA_KEYCLOAK_REALM` | ... | IAM 配置 |
| `OA_DATABASE_URL` / `OA_REDIS_URL` / `OA_MILVUS_URI` / `OA_MINIO_*` | ... | 基础设施连接 |
| `OA_LOG_LEVEL` | `INFO` | 日志级别 |

## 9.6 CI 门禁（强制）

- Lint（ruff / eslint）+ 类型检查（mypy / tsc）不过禁合并；
- 适配层核心接口测试覆盖率 < 80% 禁合并；
- **底座直连扫描**：`apps/web` 与未来插件 SDK 代码中出现 `dify/milvus/litellm/5001` 等字样即失败（1.4 红线的代码级防线）；
- SCA 协议扫描：PR 触发 + 每月全量，发现黑名单协议即失败。

---

# 10. 待明确事项

## 10.1 PRD Q1-Q7 中影响架构的项（转主理人向用户确认）

| # | 问题 | 对架构的影响 | 当前默认假设 |
| --- | --- | --- | --- |
| Q1 | 首批 5 款工具选型 | 工具注册表初始数据、连接器开发排期 | 注册表模式已解耦契约；按 PRD 建议（GitHub/企业微信/飞书/邮箱/公众号）启动 |
| Q2 | 模型接入优先级、私有化闭源 API 许可、场景默认模型 | 档位路由表初始配置 | 按 PRD 建议（S1 接入豆包/Qwen2.5/DeepSeek-Coder） |
| Q3 | 私有化环境规模/GPU/合规基线 | 部署容量参数、离线包内容 | 中小团队级，CPU 可行、GPU 可选 |
| Q4 | 三插件依赖与优先级 | S3 排期（第一阶段不阻塞） | 工作流可视化先行 |
| Q5 | 定价与超额策略 | 计量字段已就绪，策略属配置 | 三维计量先行 |
| Q6 | 记忆边界（跨会话/可查看删除/保留期） | `memory_hints` 字段预留 | 单会话长上下文 + 轻量偏好 |
| Q7 | 用量展示口径 | 3.1.4 已按业务化口径设计 | 普通用户看次数/额度 |

## 10.2 架构师新发现的待决项

| # | 待决项 | 说明与建议 | 建议决策方 |
| --- | --- | --- | --- |
| D-01 | SSE 事件集是否作为对外契约冻结 | 已按 3.1.5 自研事件集设计；需确认前端渲染组件对 `citation` 穿插位置的要求（随答案流穿插 vs 结束后聚合） | 产品经理 + 工程师 |
| D-02 | 适配层是否持有业务库（审计/连接凭据/幂等记录） | 当前设计适配层仅用 Redis（幂等/限流）与 Keycloak，业务库（工具连接凭据、审计）建议归业务能力层；第一阶段为跑通 Demo，适配层可能临时持有连接凭据表，需明确临时边界与迁移时点 | 主理人 + 架构师 |
| D-03 | 会话与消息的存储归属 | 会话列表/历史消息由上层维护还是适配层代理 Dify 会话？建议上层自持（业务库），适配层只做无状态生成，避免适配层有状态化 | 架构师 + 工程师 |
| D-04 | Agent 长任务的回调机制 | V1.0 建议「轮询 + SSE 推送」双通道，Webhook 回调留 V1.x；需确认是否接受 | 产品经理 |
| D-05 | 多租户隔离实现细节 | PostgreSQL RLS + 应用层 tenant_id 双保险已定；需确认 SaaS 是否接受共享 Milvus 实例按 partition/collection 前缀隔离 | 架构师 + 主理人 |
| D-06 | Redis 7 许可证与 MinIO 版本线合规方案 | Redis 7 为 RSALv2/SSPL 双许可、MinIO 主线为 AGPL-3.0；建议均以独立进程/容器运行（灰名单规则）或将 Redis 降级 Valkey（BSD-3）、MinIO 锁定 AGPL 前版本线；需合规决策 | 主理人 + 法务/合规 |
| D-07 | 适配层健康检查与就绪探针契约 | `/healthz`、`/readyz` 语义（ready 依赖 Redis/Dify 连通性）需与运维对齐 | 工程师 + 主理人 |
| D-08 | 前端设计令牌（品牌色/字体/圆角）基线 | 第 3 周组件库开发前需确定品牌视觉基线，否则组件库返工风险 | 产品经理 |

### 10.3 待决项状态跟踪（V1.0.1 更新）

| # | 状态 | 承载文档 | 下一动作 | 时限 |
| --- | --- | --- | --- | --- |
| D-01 | 待确认 | — | 产品经理确认 SSE 事件集冻结 | T03 内 |
| D-02 | 待确认 | `docs/api/README.md` 第五节 | Sprint 1 明确业务库归属边界 | Sprint 1 启动前 |
| D-03 | 已定（上层自持） | ADR-0001 推论 | 落地即可 | — |
| D-04 | 待确认 | — | 产品经理确认 V1.0 采用轮询 + SSE | T05 前 |
| D-05 | 待确认 | — | 架构师 + 主理人确认 Milvus 隔离方式 | Sprint 1 |
| **D-06** | **已形成 ADR-0002，待法务/合规签字** | `docs/adr/0002-infrastructure-license-compliance.md` | 主理人协调法务确认 Valkey（BSD-3）+ MinIO `RELEASE.2021-04-22`（Apache-2.0）版本线 | Sprint 1 启动前 |
| D-07 | 待确认 | `docs/api/README.md` 第五节 | 与运维对齐探针语义 | T01 内 |
| **D-08** | **已形成 ADR-0003 占位，待产品经理拍板** | `docs/adr/0003-design-token-baseline.md` | 产品经理在 2.1-2.5 决策项上确认或修改建议默认值 | **第 3 周 T03 开工前（硬时限）** |

---

## 附录：本文档与上游文档的追溯关系

- 第 1 章 ← 总方案第二章五层架构、3.6 部署模式；
- 第 2 章 ← 总方案 3.1-3.5 技术底座、5.3 合规规范；
- 第 3 章 ← 总方案 3.2 五大接口规范（P0-17）、PRD 附录 A 红线 A1-A10；
- 第 4 章 ← PRD 第九章 Q1/Q2；
- 第 5 章 ← PRD FLOW-01/02、总方案 3.5 流式要求；
- 第 6 章 ← 总方案 5.1 代码规范；
- 第 7 章 ← 总方案第四章第一阶段排期；
- 第 8 章 ← 总方案 5.3 协议黑白名单；
- 第 9 章 ← 总方案第五章强制开发规范；
- 第 10 章 ← PRD Q1-Q7 + 架构师新增 D-01~D-08。

（本文档结束）
