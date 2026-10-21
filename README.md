# 企业级跨生态办公 AI 平台（OfficeAI）

> 面向企业与团队用户的跨生态、全场景、可私有化办公 AI 平台。
> 本仓库为 Monorepo，采用「底座层 + 自研统一适配层 + 业务层 + 插件层 + 前端层」五层解耦架构。

---

## 一、项目简介

平台以成熟开源底座（Dify，Apache-2.0）提供 AI 原子能力，通过**自研统一适配层**屏蔽底座差异，
向上层（前端交互层、场景插件层、业务能力层）输出自研标准接口，实现「替换底座仅改适配层，上层零改动」。

第一阶段（第 1-8 周）目标：搭稳底层架构、固化标准接口。本期（T01）交付**项目脚手架与代码规范**。

## 二、目录结构

```
officeai/                              # Monorepo 根目录
├── apps/
│   ├── adapter/                       # 🔴 自研统一适配层（唯一允许接触底座的应用）
│   │   ├── app/
│   │   │   ├── main.py                # FastAPI 入口：中间件、异常处理器、路由挂载
│   │   │   ├── core/                  # 配置、错误码、日志、鉴权、幂等、限流
│   │   │   ├── middleware/            # trace_id 注入、统一异常兜底
│   │   │   ├── api/v1/                # 五大标准接口端点（M/R/A/T/U 共 30 个）
│   │   │   ├── schemas/               # 请求响应模型（契约同步）
│   │   │   ├── services/              # 协议转换服务（唯一允许写下游调用的位置）
│   │   │   ├── clients/               # 底座 HTTP 客户端（唯一调用出口）
│   │   │   ├── registry/              # 档位路由表、工具类型注册表（数据驱动）
│   │   │   └── tools/                 # 工具连接器实现（新增工具只加这里）
│   │   ├── tests/{unit,integration}/  # 单元测试与契约测试
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   └── web/                           # 前端交互层（Next.js 14 App Router）
│       ├── src/app/                   # 页面（App Router）
│       ├── src/components/ui/         # 统一组件库（shadcn/ui 风格封装）
│       ├── src/lib/api-client.ts      # 前端唯一适配层调用出口
│       ├── src/hooks/                 # 自定义 Hook
│       └── src/styles/globals.css     # 设计令牌
├── deploy/                            # 部署编排（双模式切换）
│   ├── docker-compose.yml             # 私有化一键部署
│   ├── docker-compose.saas.yml        # SaaS 本地联调
│   └── .env.example                   # OA_* 全量环境变量样例
├── docs/
│   ├── plan-v1.0.md                   # 开发执行总方案
│   ├── prd-v1.0.md                    # 产品需求文档
│   ├── architecture-v1.0.md           # 系统架构设计文档
│   ├── api/                           # 接口规范（OpenAPI，从适配层生成）
│   └── adr/                           # 架构决策记录
├── .github/workflows/                 # CI 门禁
├── .editorconfig
├── .gitignore
├── package.json                       # npm workspaces 根声明
└── README.md
```

## 三、本地启动步骤

### 3.1 前置条件

| 组件 | 版本要求 | 说明 |
| --- | --- | --- |
| Python | 3.11+（推荐 3.13） | 适配层运行时 |
| Node.js | 20.11+（推荐 22.x） | 前端构建 |
| Docker + Docker Compose | v2 | 底座与基础设施 |

### 3.2 适配层（apps/adapter）

```bash
cd apps/adapter

# 1) 创建虚拟环境
python -m venv .venv

# 2) 安装依赖（Windows: .venv\Scripts\activate）
pip install -e ".[dev]"

# 3) 准备环境变量
cp ../../deploy/.env.example .env

# 4) 本地启动（默认桩模式，无需底座即可运行）
uvicorn app.main:app --reload --port 8000

# 5) 健康检查
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz

# 6) 接口文档
#    http://127.0.0.1:8000/docs
```

### 3.3 前端（apps/web）

```bash
cd apps/web

# 1) 安装依赖（需网络）
npm install

# 2) 配置适配层地址
#    在项目根目录创建 .env.local，写入 NEXT_PUBLIC_ADAPTER_BASE_URL=http://127.0.0.1:8000

# 3) 开发模式
npm run dev

# 4) 生产构建
npm run build && npm run start
```

### 3.4 一体化部署（deploy）

```bash
cd deploy

cp .env.example .env
# 按需修改 DEPLOY_MODE=saas|private 与各服务连接串

docker compose -f docker-compose.yml up -d        # 私有化
docker compose -f docker-compose.saas.yml up -d   # SaaS 本地联调
```

## 四、开发规范速查（强制）

| 维度 | 规范 |
| --- | --- |
| API 路径 | `/api/v1/{domain}/{resource}`，小写连字符 |
| 资源 ID | `类型缩写_` + ULID，如 `kb_01HF8...`、`task_01HF8...` |
| JSON 字段 | snake_case |
| 枚举值 | 小写下划线，如 `long_context`、`processing` |
| 环境变量 | 大写 + `OA_` 前缀 |
| 统一响应 | `{code, message, data}`，`code=0` 成功 |
| 错误提示 | 仅使用业务语言，严禁透传 code、堆栈、下游原始错误 |
| 提交信息 | `type(scope): 说明`（feat/fix/refactor/test/docs/chore） |
| 分支策略 | `main` / `develop` / `feature/*` / `release/*` / `hotfix/*` |

### 架构红线（违反即打回）

1. 上层**只能**调用适配层 `/api/v1/...`，严禁直连底座原生 API / SDK / 源码引用。
2. 底座 API 形态（URL、参数、事件名、错误码）严禁向上层泄露。
3. `apps/adapter/app/clients/dify_client.py` 是全仓库唯一底座调用出口。
4. `apps/adapter/app/services/*` 是唯一允许写协议转换的位置。
5. `apps/web` 中禁止出现底座相关字样（CI 有扫描门禁）。

## 五、质量门禁

| 门禁 | 工具 | 阈值 |
| --- | --- | --- |
| Python Lint / Format | ruff | 零告警 |
| Python 类型检查 | mypy | 零 error |
| Python 测试 | pytest | 核心接口覆盖率 ≥ 80% |
| 前端 Lint | ESLint | 零 error |
| 前端类型检查 | tsc | 零 error |
| 开源协议扫描 | CI `dependency-scan.yml` | 无 GPL/AGPL/SSPL |
| 底座直连扫描 | CI `dependency-scan.yml` | `apps/web` 命中即失败 |

本地执行：

```bash
cd apps/adapter
ruff check . && ruff format --check .
mypy app
pytest tests -q --cov=app --cov-report=term-missing
```

## 六、当前进度与已知限制

### 6.1 当前进度

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| T01 项目脚手架与代码规范 | ✅ 已完成 | Monorepo 骨架、30 个端点契约、CI 门禁、双模式部署编排 |
| T02 适配层核心框架与五大接口开发 | ⏳ 待启动 | 在本期骨架上填充真实协议转换逻辑 |
| T03 前端基础框架与统一组件库 | ⏳ 待启动 | 在本期骨架上填充页面与组件实现 |
| T04 适配层与底座全量联调 | ⏳ 待启动 | 依赖底座环境就绪 |
| T05 全链路 Demo 与阶段验收 | ⏳ 待启动 | — |

### 6.2 已知限制（本期明确不做）

1. **仅骨架与契约，不含真实业务逻辑**：适配层所有下游调用均为桩实现（mock），
   `DifyClient` / `KeycloakClient` 已按最终形态实现调用代码，但由 `OA_DOWNSTREAM_ENABLED=false` 短路，
   不产生任何真实底座请求。真实协议转换在 T02 落地。
2. **状态全部内存化**：幂等键（`IdempotencyManager`）与限流（`RateLimiter`）本期为进程内实现，
   Redis 后端已实现但默认关闭（`OA_IDEMPOTENCY_BACKEND=memory`），T02/T04 切换。
3. **令牌校验为开发态**：默认 `OA_AUTH_MODE=stub`，仅解析 JWT 载荷不做签名校验，
   生产必须切换为 `oidc` 并配置 `OA_JWT_*` / `OA_KEYCLOAK_*`。
4. **底座形态仅在适配层内出现**：底座地址、事件名、字段名只允许出现在 `apps/adapter`，
   `apps/web` 由 CI 扫描门禁强制隔离。
5. **待决事项**：架构文档 D-06（Redis/MinIO 许可证方案）尚未决策，
   `deploy/docker-compose.yml` 暂用 Valkey（BSD-3）+ MinIO 锁定 AGPL 之前版本线并独立容器运行。
6. **前端未执行依赖安装**：本期环境无 npm 网络安装条件，`apps/web` 为完整可安装骨架，
   需在有网环境执行 `npm install` 后方可构建运行。

## 七、许可证与开源声明

平台为企业级商业交付物，内部依赖清单与协议合规审计结果见 `docs/api/README.md` 与 CI 扫描报告。
所有引入依赖均限定于 MIT / Apache-2.0 / BSD-3-Clause / ISC 白名单协议。
