# 企业级跨生态办公 AI 平台（OfficeAI）

> 面向企业与团队用户的跨生态、全场景、可私有化办公 AI 平台。
> 本仓库为 Monorepo，采用「底座层 + 自研统一适配层 + 业务层 + 插件层 + 前端层」五层解耦架构。

---

## 一、项目简介

平台以成熟开源底座（Dify，Apache-2.0）提供 AI 原子能力，通过**自研统一适配层**屏蔽底座差异，
向上层（前端交互层、场景插件层、业务能力层）输出自研标准接口，实现「替换底座仅改适配层，上层零改动」。

**当前进度**：已完成第一至第三阶段（第 1-20 周，共 24 周计划中的前 20 周）。
第三阶段交付插件体系与 3 个标杆场景插件，**84 个端点全部就绪**（core 30 + phase2 38 + phase3 16）。
第四阶段（商用打磨与交付准备）待启动。

> **测试数字口径（审计 OA-AUDIT-001 / P1-3 补充，务必连同数字一起读）**
> 测试与覆盖率存在**两套口径**，脱离前置条件单独引用数字会误导：
>
> | 口径 | 前置条件 | 实测结果 |
> | --- | --- | --- |
> | **全量（含 PG）** | 设 `OA_TEST_DATABASE_URL` 指向 `NOSUPERUSER NOBYPASSRLS` 角色 + `--run-pg` + 设 `OA_BATCH` | **1573 passed / 0 failed / 0 skipped**，合并覆盖率 90.78% |
> | **CI 子集（无 PG）** | 不设上述变量 | 1456 passed / 1 failed（缺 `OA_BATCH`）/ 141 skipped，覆盖率 84.19% |
>
> 未设 `OA_BATCH` 时 `tests/expected_xfail.py::current_batch()` **故意抛错**（缺失即红，
> 不给静默默认）。CI 若不加 Postgres 服务，覆盖率门禁（≥85%）**必然不达标**。

## 二、目录结构

```
officeai/                              # Monorepo 根目录
├── apps/
│   ├── adapter/                       # 🔴 自研统一适配层（唯一允许接触底座的应用）
│   │   ├── app/
│   │   │   ├── main.py                # FastAPI 入口：中间件、异常处理器、路由挂载
│   │   │   ├── core/                  # 配置、错误码、日志、鉴权、幂等、限流
│   │   │   ├── middleware/            # trace_id 注入、统一异常兜底
│   │   │   ├── api/v1/                # 标准接口端点（M/R/A/T/U + 插件域 PL/WF，共 84 个）
│   │   │   ├── schemas/               # 请求响应模型（契约同步）
│   │   │   ├── services/              # 协议转换服务（唯一允许写下游调用的位置）
│   │   │   ├── clients/               # 底座 HTTP 客户端（唯一调用出口）
│   │   │   ├── business/              # 业务服务层（用户/知识库/任务/插件/计费等）
│   │   │   ├── db/                    # 数据层：session、migrate、repositories（双后端）
│   │   │   ├── plugins/               # 场景插件层：声明模型、加载器、引擎、渲染、脱敏
│   │   │   │   ├── manifests/         # 6 份插件声明（3 工作台 + 3 轻量）
│   │   │   │   └── ...
│   │   │   ├── registry/              # 档位路由表、工具类型注册表（数据驱动）
│   │   │   └── tools/                 # 工具连接器实现（新增工具只加这里）
│   │   ├── tests/{unit,integration,e2e}/  # 单元测试 / 集成测试 / 端到端测试
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   └── web/                           # 前端交互层（Next.js 14 App Router）
│       ├── src/app/                   # 页面（App Router）：含 /plugins、/workbench/{id}、/workflows/{taskId}
│       ├── src/components/ui/         # 统一组件库（shadcn/ui 风格封装）
│       ├── src/lib/api-client.ts      # 前端唯一适配层调用出口
│       ├── src/hooks/                 # 自定义 Hook
│       ├── scripts/                   # 前端门禁脚本（禁词/占位词/三态/契约/能力一致性）
│       └── src/styles/globals.css     # 设计令牌
├── deploy/                            # 部署编排（双模式切换）
│   ├── docker-compose.yml             # 私有化一键部署
│   ├── docker-compose.saas.yml        # SaaS 本地联调
│   ├── .env.example                   # OA_* 全量环境变量样例
│   ├── init-schema-phase3.sql         # 阶段三 Schema 初值（9 张插件/任务表）
│   ├── migrations/                    # 0001-0004 迁移（幂等 + 自足 + 双路径门禁）
│   └── validate_*.py                  # 门禁脚本：端点/迁移/依赖/镜像/插件声明/Schema 保留
├── docs/
│   ├── plan-v1.0.md                   # 开发执行总方案
│   ├── prd-v1.0.md                    # 产品需求文档
│   ├── prd-phase{2,3}-increment.md    # 阶段二/三增量 PRD
│   ├── architecture-v1.0.md           # 系统架构设计文档（30 端点冻结基线）
│   ├── architecture-phase{2,3}.md     # 阶段二/三架构（38 / 16 端点基线 + 批次分解）
│   ├── phase{1,2,3}-acceptance-report.md  # 各阶段验收报告
│   ├── plugin-dev-guide.md            # 插件开发规范与样例（P0-P3-05）
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

# 4) 本地启动（默认桩模式：OA_DOWNSTREAM_ENABLED=false、OA_AUTH_MODE=stub，
#    无需底座即可运行；接入真实底座需同时切这两个开关并配置连接串）
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

### 6.1 当前进度（按 24 周四阶段口径）

| 阶段 | 周期 | 状态 | 验收报告 |
| --- | --- | --- | --- |
| 一：底座与适配层攻坚 | 第 1-8 周 | ✅ 已验收 | `docs/phase1-acceptance-report.md` |
| 二：基础功能全量对齐 | 第 9-16 周 | ✅ 已验收 | `docs/phase2-acceptance-report.md` |
| 三：插件体系与标杆场景 | 第 17-20 周 | ✅ 已验收 | `docs/phase3-acceptance-report.md` |
| 四：商用打磨与交付准备 | 第 21-24 周 | ⬜ 未启动 | — |

第三阶段交付内容：插件声明模型与运行时（6 段声明 + 8 层加载校验 + 6 能力封闭集合）、
插件市场与全生命周期（PL1-PL14）、任务引擎与工作流可视化（WF1/WF2）、
3 个标杆场景插件（前端研发 / 内容运营 / 行政人事，均端到端可用）。

### 6.2 当前门禁与质量基线（`develop` 实测）

| 项 | 值 |
| --- | --- |
| 端点契约 | `core=30 / phase2=38/38 / phase3=16/16`，合计 **84**，严格模式 EXIT 0 |
| 全量测试 | **1573 passed / 0 failed / 0 skipped**（含 PostgreSQL 实跑） |
| P0 用例 | `pytest -m p0` → **184/184** |
| 覆盖率 | 总 `app/*` 合并 **90.78%**；`business+db` 合并 **90.26%**；分支 **2315/2862 = 80.89%** |
| 静态门禁 | `ruff check` / `ruff format` / `mypy`（117 文件）全绿 |
| 部署门禁 | 端点基线 / 迁移双路径 / 依赖白名单 / 镜像锁定 / 插件声明 / 错误码三处一致 均 EXIT 0 |
| 前端门禁 | 禁词 / 占位词 / 页面三态 / 契约快照 / 能力一致性 / 工作台宿主唯一 均 0 命中 |

### 6.3 已知限制与未验证项

1. **微信支付真实商户联调未做**：需客户提供商户号与密钥，当前为沙箱 + 契约桩验证。
2. **模型提供方密钥未提供**：`marketplace` 不可达，M1/M2 的 happy path 依赖真实密钥才能跑通。
3. **真实环境回执未验证**：GitHub 仓库写操作、公众号/邮件发布回执均为沙箱或契约桩；
   插件在"工具未连接"时按步骤级失败处理，不会谎称已发布。
4. **多副本调度未验证**：V1.0 按单副本交付，任务队列语义在单实例下验证；换 Valkey 时从
   `task_service._advance_record()` 单入口替换。
5. **分支覆盖率余量为 0**：`2176/2840` 刚好达到 76.6% 门槛，删除或跳过任一用例即回落，
   已挂阶段四首周补齐（目标余量 ≥50 条）。
6. **CI 未实际运行**：GitHub Actions 额度已用尽，上述门禁均为本机实跑（QA 独立复核 + 主理人抽验）；
   CI 配置已就绪，额度恢复后需复跑一次。
7. **历史遗留错误码**：5 个阶段一/二注册但从未被端点引用的码（`11005/12002/12004/12005/15006`）
   在错误码目录检查里只打 `[WARN]`，处置方案见架构侧评审记录。

## 七、许可证与开源声明

平台为企业级商业交付物，内部依赖清单与协议合规审计结果见 `docs/api/README.md` 与 CI 扫描报告。
所有引入依赖均限定于 MIT / Apache-2.0 / BSD-3-Clause / ISC 白名单协议。
