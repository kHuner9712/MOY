# 接口规范（API Specification）

> 本目录存放对外接口规范文档，由适配层自动生成，**禁止手工编辑**。
>
> 契约基线：`docs/architecture-v1.0.md` 第 3 章「自研统一适配层五大标准接口规范 v1.0」。

---

## 一、生成方式

```bash
cd apps/adapter
python - <<'PY'
import json
from app.main import create_app

schema = create_app().openapi()
with open('../../docs/api/openapi.json', 'w', encoding='utf-8') as fp:
    json.dump(schema, fp, ensure_ascii=False, indent=2)
PY
```

CI（`.github/workflows/ci-adapter.yml` 的 `openapi-export` 任务）在每次
push / PR 触发时自动生成并作为构件上传，命名 `openapi-spec`。

运行中的服务也可直接访问：

| 地址 | 说明 |
| --- | --- |
| `http://<host>:8000/docs` | Swagger UI |
| `http://<host>:8000/redoc` | ReDoc |
| `http://<host>:8000/openapi.json` | OpenAPI 3.1 原始文档 |

---

## 二、契约总览

### 2.1 端点数量

| 域 | 前缀 | 端点数 | 编号 |
| --- | --- | --- | --- |
| 模型服务 | `/api/v1/model` | 4 | M1-M4 |
| RAG 服务 | `/api/v1/knowledge` | 7 | R1-R7 |
| Agent 服务 | `/api/v1/agent` | 6 | A1-A6 |
| 工具服务 | `/api/v1/tools` | 7 | T1-T7 |
| 用户权限服务 | `/api/v1/identity` | 6 | U1-U6 |
| **核心业务端点合计（冻结）** | | **30** | |
| 健康检查 | `/healthz`、`/readyz` | 2 | 免鉴权 |

**阶段二新域端点（38 个，按 B03-B10 逐批落地；同挂 `/api/v1`，不新增版本）**

| 域 | 前缀 | target | 编号 |
| --- | --- | --- | --- |
| 知识库扩展 | `/api/v1/knowledge` | 8 | KB1-KB8 |
| 工具扩展 | `/api/v1/tools` | 1 | TL1 |
| 组织与权限 | `/api/v1/identity` | 11 | ID1-ID11 |
| 计费订阅 | `/api/v1/billing` | 10 | BL1-BL10 |
| 运营后台 | `/api/v1/ops` | 8 | OPS1-OPS8 |
| **新域端点合计** | | **38** | |

> 阶段小计 **68 个业务端点**（30 冻结 + 38 新域）。基线文件：
> `docs/api/baseline-core-30.json`（经评审流程保护，禁止修改）、
> `docs/api/baseline-phase2-38.json`（只允许登记真实已实现端点）。
> 门禁：`python deploy/validate_openapi.py`（G1-G9，失败退出码非 0）；
> **无任何放宽参数**：阶段二历史用的 `--allow-missing-phase2` 已于 2026-10-29
> 审计清理中从脚本本体一并移除（38/38 已交付），CI 全程 `--strict`。

**阶段三新域端点（16 个，按 B17-B19 逐批落地；同挂 `/api/v1`，不新增版本）**

| 域 | 前缀 | target | 编号 |
| --- | --- | --- | --- |
| 插件域 | `/api/v1/plugins` | 14 | PL1-PL14 |
| 任务域（新增写端点） | `/api/v1/agent` | 2 | WF1-WF2 |
| **新域端点合计** | | **16** | |

> 阶段小计 **84 个业务端点**（30 冻结 + 38 阶段二 + 16 阶段三；阶段四与 W1 见下两节，全量 96）。基线文件：
> `docs/api/baseline-phase3-16.json`（**文件名即域内冻结总数 16**，逐个登记已实现端点；
> B17 登记 6 条、B18 追加 8 条、B19 追加 WF1/WF2 → 16/16 收口）。
> **非业务端点（不计入基线）**：`GET /files/artifacts/{token}` —— 产物内容代理。
> 它**不挂 `/api/v1`**、不参与 `PHASE3_DOMAIN_ENDPOINTS` 计数：无 JSON 信封、无业务
> 语义、无版本化契约，只是把产物字节按签名令牌吐出去（令牌自带租户 + 产物 + 版本并经
> HMAC 签名，所以不需要登录态）。把它挂进 `/api/v1` 会让「16 条冻结端点」这个对 QA 与
> 客户的承诺失去意义——审计看到未登记路径时，请对照这一条。
>
> 另 **4 处 A 域加性扩展**（A1/A2/A3/A4）不新增端点数，只加可选字段与枚举加宽，
> 由 G4 加性 diff 逐项覆盖（`baseline-core-30.json` 与 `baseline-phase2-38.json`
> **一个字节不改**，经评审流程保护，无机械 hash 断言）。

#### 阶段三严格模式（B21 收口）

`deploy/validate_openapi.py` **不再提供** `--allow-missing-phase3`（B21 已删除该参数，
传了会被 argparse 直接拒绝，退出码 2）。判据因此只有一种形态：

| 情形 | 结果 |
| --- | --- |
| `phase3 = 16/16` | 绿（唯一允许的状态） |
| 少一条（端点被删 / 路由没注册） | 红：`G2 新域端点未实现: METHOD path` + `G3 插件域端点数量必须为 16` |
| 多一条（写了端点没登记基线） | 红：`G2 新域端点未登记（禁止先写端点后补基线）` |

> **为什么删掉这个参数**：放宽开关一旦常态化，"绿"就不再说明端点齐了。
> B19 收口后"已登记但未实现"的中间态已经不存在，留着它等于给"删端点让 CI 变绿"
> 留一条合法通道。要改基线必须走评审（`baseline-phase3-16.json` 只允许登记
> **真实已实现**的端点），不允许加参数。
>
> 三态证据（B21 实跑，主理人已验收）：补齐态 EXIT 0 → 追加一条假端点 EXIT 1
> （G2）→ 恢复 EXIT 0 → 删掉一条已实现端点的登记 EXIT 1（G1+G3）→ 恢复 EXIT 0。

**阶段四新域端点（10 个，T4 批次落地；同挂 `/api/v1`，不新增版本）**

| 域 | 前缀 | target | 编号 |
| --- | --- | --- | --- |
| 会话管理 | `/api/v1/model/conversations` | 4 | MC1-MC4 |
| 用户记忆 | `/api/v1/model/memories` | 4 | MM1-MM4 |
| 租户数据 | `/api/v1/ops/tenant-data` | 2 | TD1-TD2 |
| **新域端点合计** | | **10** | |

**W1 增量端点（2 个，门禁基础设施；同挂 `/api/v1`）**

| 域 | 前缀 | target | 编号 |
| --- | --- | --- | --- |
| 任务统计 | `/api/v1/stats` | 1 | W1-A1 |
| 能力推荐 | `/api/v1/capabilities` | 1 | W1-B1 |
| **增量端点合计** | | **2** | |

**矩阵第 2 项增量端点（2 个，通用任务「计划生成 → 确认」；同挂 `/api/v1`）**

| 域 | 前缀 | target | 编号 |
| --- | --- | --- | --- |
| 执行计划 | `/api/v1/agent/tasks/{task_id}/plan` | 2 | P1-P2 |
| **增量端点合计** | | **2** | |

> 全量合计 **98 个业务端点**（30 冻结 + 38 阶段二 + 16 阶段三 + 10 阶段四 + 2 W1 + 2 W2）。
> 基线文件：`docs/api/baseline-phase4-10.json`、`docs/api/baseline-w1-2.json`、
> `docs/api/baseline-w2-2.json`（口径同阶段三：只登记真实已实现端点；`--strict` 下
> missing>0 即红，任何未登记的新端点一律立即判红，这三条都没有放宽开关）。
> P1/P2 为**本期新增且已实现**，不做 `[PENDING]` 过渡（G8 登记侧与实现侧都严格）。

**MCP 增量端点（4 个，矩阵第 3 项；同挂 `/api/v1`）**

| 能力 | 端点 | 条数 | 编号 |
| --- | --- | --- | --- |
| 外部服务登记列表 | `GET /api/v1/ops/mcp-servers` | 1 | MC1 |
| 登记 / 更新外部服务 | `POST /api/v1/ops/mcp-servers` | 1 | MC2 |
| 删除外部服务 | `DELETE /api/v1/ops/mcp-servers/{server_id}` | 1 | MC3 |
| 连通性探测 | `POST /api/v1/ops/mcp-servers/{server_id}/probe` | 1 | MC4 |
| **增量端点合计** | | **4** | |

> 全量合计 **102 个业务端点**（30 冻结 + 38 阶段二 + 16 阶段三 + 10 阶段四 + 2 W1 + 2 W2 + 4 MCP）。
> 基线文件：`docs/api/baseline-phase4-10.json`、`docs/api/baseline-w1-2.json`、
> `docs/api/baseline-w2-2.json`、`docs/api/baseline-b3-4.json`（口径同阶段三：只登记
> 真实已实现端点；`--strict` 下 missing>0 即红，任何未登记的新端点一律立即判红，
> 这四个基线组都没有放宽开关）。
> 矩阵第 3 项的 4 条为**本期新增且已实现**，不做 `[PENDING]` 过渡（G9 登记侧与实现侧都严格）。
> 与业务端点不同，MCP 能力本身有独立开关 `OA_MCP_ENABLED`（默认 `false`）；关闭时
> 登记端点返回 `20003`，插件步骤里的 `tool_type="mcp"` 直接失败，不会发起任何外部连接。

### 2.2 统一约定

| 项 | 约定 |
| --- | --- |
| 响应格式 | `{code, message, data}`，`code=0` 成功 |
| HTTP 状态码 | 仅承载传输层语义，业务语义一律以 `body.code` 为准 |
| 鉴权 | 默认需 `Authorization: Bearer <token>`；豁免清单（`app/middleware/error_handler.py` 的 `AUTH_EXEMPT_PATHS`）：`/healthz`、`/readyz`、`/metrics`、`/api/v1/billing/public/callback`、`/sso/*`（5 个 OIDC 端点） |
| 租户 | `X-Tenant-Id` 请求头；私有化模式默认 `default` |
| 幂等 | 所有写接口必须携带 `Idempotency-Key`（UUID v4，24h 有效）；指纹含请求体摘要，同键异体判冲突（详见架构文档 3.0.5） |
| 链路 | 响应头回写 `X-Trace-Id`，全链路透传 |
| 版本 | 前缀 `/api/v1`；不兼容变更升 `/api/v2`，禁止在 v1 内破坏契约 |

### 2.3 错误码分段

| 分段 | 含义 |
| --- | --- |
| `0` | 成功 |
| `10001-14999` | 领域业务错误（模型 / RAG / Agent / 工具 / 权限） |
| `15001-15999` | 计费与订阅错误（阶段二新增） |
| `16001-16099` | 插件域错误（阶段三新增；`16001` 已注销不复用，自 `16002` 起） |
| `17001-17099` | 任务域错误（阶段三新增，重试语义） |
| `18001-18099` | 会话与记忆域错误（阶段四新增，个人数据对象） |
| `20001-20999` | 通用业务错误 |
| `30001-30999` | 系统错误 |

完整错误码与业务语言文案见架构文档 3.0.3，代码落点为
`apps/adapter/app/core/errors.py` 的 `ERROR_REGISTRY`。

**提示码（非阻断，`code` 仍为 0）**：有些「结果需要告知、但不构成失败」的场景
**不得**返回非 0 `code`——前端 `unwrap()` 会把非 0 码当异常抛出，用户看到报错弹窗。
这类场景统一走 `data` 内的字段下发，前端据此展示**提示条/提示文案**：

| 场景 | 下发方式 | 前端动作 |
| --- | --- | --- |
| 降级将在当前周期结束后生效（`15005`） | `data.notice_code=15005` + `data.notice_message` | 展示提示条，**不弹错误** |
| 变更后额度已超额（`15006` 语义） | `data.over_quota_warning=true` | 展示超额提示，**不弹错误** |

> `15005` / `15006` 仍登记在错误码表中（供日志与文档引用），但在上述端点里
> **不会**作为 `code` 返回。判定口径见架构文档 §7.2 的裁决行。

**注册制流程**：架构文档登记 → `ERROR_REGISTRY` 注册 → 同步
`apps/web/src/lib/business-errors.ts` 兜底映射 → 更新本文档。

---

## 三、底座形态防火墙

对外文档与响应中**不得出现**任何底座原生特征（架构文档 1.4）：

| 底座原生形态 | 自研对外形态 |
| --- | --- |
| `answer` | `content` |
| `metadata.usage.*_token` | `usage.*_units` |
| `retriever_resources` | `citations` / `segments` |
| `indexing_status`（七态） | `index_status`（三态） |
| SSE `message` / `message_end` / `message_file` | `delta` / `done` / `citation` |
| 工作流节点类型名 | 业务语言 `step.title` |
| 真实模型名 | 能力档位 `model_profile` |

CI 门禁（`.github/workflows/dependency-scan.yml` + `ci-adapter.yml`）
会在每次提交时校验：命中即失败。

---

## 四、兼容性策略

| 变更类型 | 是否允许 | 要求 |
| --- | --- | --- |
| 新增端点 | ✅ | 分配新编号（M8/A7…），登记架构文档第 3 章 |
| 新增可选请求字段 | ✅ | 必须有默认值，不得破坏既有调用方 |
| 新增响应字段 | ✅ | 需在本文档同步；前端类型定义同步更新 |
| 修改字段语义 | ❌ | 视为不兼容变更，必须升 `/api/v2` |
| 删除端点/字段 | ❌ | 必须升 `/api/v2` |
| 新增错误码 | ✅ | 走错误码注册制流程 |

---

## 五、待决事项

| 编号 | 问题 | 影响 | 当前处理 |
| --- | --- | --- | --- |
| D-01 | SSE 事件集是否作为对外契约冻结 | 前端引用穿插渲染方式 | 已按「随答案穿插」实现，待产品确认 |
| D-02 | 适配层是否持有业务库 | 工具连接凭据、审计存储归属 | 已决：适配层持有业务库（`app/db/engine` + 8 组 repositories，memory/PG 双后端） |
| D-03 | 会话与消息的存储归属 | 是否由适配层代理会话 | 上层自持，适配层无状态 |
| D-07 | `/healthz`、`/readyz` 语义契约 | 编排层探针配置 | `/healthz` 存活、`/readyz` 就绪，待运维确认 |
| D-06 | 基础设施灰名单组件选型 | Redis/MinIO 协议合规 | 已形成 `docs/adr/0002`，已批准（D-06） |
| D-08 | 前端品牌设计令牌基线 | 组件库返工风险 | 已形成 `docs/adr/0003` 占位，待产品经理拍板 |

---

## 六、第三方组件与协议声明

平台内部依赖清单与协议合规审计结果由 CI 扫描报告归档
（`.github/workflows/dependency-scan.yml` 构件 `license-scan-report`）。

代码级依赖以 **MIT / Apache-2.0 / BSD-2/3-Clause / ISC** 白名单为主，传递链含
MPL-2.0（certifi，灰名单：文件级弱互惠，随交付保留许可证文本）/ PSF-2.0 / MIT-0 等
宽松协议，**无 GPL/AGPL/SSPL**（2026-10-30 审计 M4 实测口径）；
基础设施镜像的灰名单仅 squid（GPL-2.0），以独立进程方式运行，
详见 `deploy/.env.example` 与架构文档 2.1 / 10.2 D-06。
