# Moy（麦子）· OfficeAI

> Moy（麦子）—— 面向广泛工作需求的模块化 AI 工作平台。本仓库为 V1.0 实现（首批落地场景：企业办公），
> 采用「底座层 + 自研统一适配层 + 业务层 + 插件层 + 前端层」五层解耦架构。
> 产品定位与战略以《项目总纲》（`docs/moy-project-charter-v1.0.md`）为准；能力可用性以总纲第十一章状态分级（L1-L5）为准，
> 详见 `docs/prd-v1.0.md` §5.1 成熟度列与 `docs/decisions/0005-charter-first-alignment.md`。

---

## 一、项目简介

平台以成熟开源底座（Dify，**修改版 Apache-2.0**；多租户运营须书面授权、前端 LOGO/版权不可移除等附加约束见 `docs/adr/0002-infrastructure-license-compliance.md` §9.2）提供 AI 原子能力，通过**自研统一适配层**屏蔽底座差异，
向上层（前端交互层、场景插件层、业务能力层）输出自研标准接口，实现「替换底座仅改适配层，上层零改动」。

**当前进度**：第一至第四阶段主体已交付，W1（总纲对齐）切片 A/B 已完成。
**103 个端点全部就绪**，以 `docs/api/baseline-*.json` 8 套基线为唯一真源：
`core 30 + phase2 38 + phase3 16 + phase4 10 + W1 2 + W2 2 + B3 4 + B4 1 = 103`（`validate_openapi.py --strict` EXIT 0）。

> **测试数字口径（务必连同数字一起读）**
> 测试与覆盖率存在**两套口径**，脱离前置条件单独引用数字会误导：
>
> | 口径 | 前置条件 | 结果 |
> | --- | --- | --- |
> | **本机（无 PG）** | 不设 `OA_TEST_DATABASE_URL`，`OA_BATCH=B21` | **2675 passed / 258 skipped / 0 failed**（收集 2933，其中 PG 用例 258 条；2026-11-04 实测） |
> | **全量（含 PG）** | 设 DSN 指向 `NOSUPERUSER NOBYPASSRLS` 角色 + `--run-pg` + 设 `OA_BATCH` | **以 CI `ci-adapter` 的 junit.xml 工件为准**（最近一次 success：2026-11-03 run `41641650690`）；本地复跑需真库 |
>
> 未设 `OA_BATCH` 时 `tests/expected_xfail.py::current_batch()` **默认回落 `B21`**（与 CI 同值），
> 裸跑不再报红；`deploy/validate_batch_clock.py` 仍要求显式设置（缺失即退出码 2）。无库口径下
> 258 条 PG 用例按设计 skip，不计入断言有效性。
>
> **数字的唯一权威来源 = CI 工件**（`.github/workflows/ci-adapter.yml` 的 junit.xml /
> coverage 报告）。本机复跑命令：`pytest tests -q --cov=app --cov-report=term-missing`
> （无 PG 口径去掉 `--run-pg` 与 DSN）。**历史快照仅供追溯，勿当现值**：含 PG
> 2343 passed / 合并覆盖 91.94%（`cedd3e9`）；无 PG 2211 passed / 212 skipped /
> 85.87%（2026-10-30）。数字随批次变化，引用必须带 SHA 与命令（PRD §7.5 同款要求）。

## 二、目录结构

```
officeai/                              # Monorepo 根目录
├── apps/
│   ├── adapter/                       # 🔴 自研统一适配层（唯一允许接触底座的应用）
│   │   ├── app/
│   │   │   ├── main.py                # FastAPI 入口：中间件、异常处理器、路由挂载
│   │   │   ├── core/                  # 配置、错误码、日志、鉴权、幂等、限流
│   │   │   ├── middleware/            # trace_id 注入、统一异常兜底
│   │   │   ├── api/v1/                # 标准接口端点（M/R/A/T/U + 插件域 PL/WF + 统计/能力，共 103 个）
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
│   │   ├── tests/{unit,integration,e2e,security}/  # 单元测试 / 集成测试 / 端到端测试 / 安全测试
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   └── web/                           # 前端交互层（Next.js 16 App Router）
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
│   ├── migrations/                    # 0002-0013 迁移（12 个，0001 为 RESERVED 基线别名；幂等 + 自足 + 双路径门禁）
│   └── validate_*.py                  # 门禁脚本 16 个（端点/迁移/依赖/镜像/插件/Schema 保留/批量时钟/配置名/部署/门禁接线/导入方向/离线包/打包/PG 标记/仓库对称/密钥）
├── docs/
│   ├── README.md                      # 文档索引（唯一入口：规范 / 验收 / 审计 / 决策 + 工具脚本登记）
│   ├── plan-v1.0.md                   # 开发执行总方案
│   ├── prd-v1.0.md                    # 产品需求文档
│   ├── prd-phase{2,3,4}-increment.md  # 阶段二/三/四增量 PRD
│   ├── architecture-v1.0.md           # 系统架构设计文档（30 端点冻结基线）
│   ├── architecture-phase{2,3,4}.md   # 阶段二/三/四架构（38 / 16 / 10 端点基线 + 批次分解）
│   ├── architecture-w1-metrics-entry.md  # W1 增量架构（任务统计 + 能力推荐切片）
│   ├── phase{1,2,3,4}-acceptance-report.md  # 各阶段验收报告
│   ├── plugin-dev-guide.md            # 插件开发规范与样例（P0-P3-05）
│   ├── api/                           # 接口规范与端点冻结基线（openapi.json 由适配层生成）
│   ├── adr/                           # 架构决策记录（ADR-0001~0003）
│   ├── decisions/                     # 决策确认单（拍板附件，0003~0009）
│   ├── compliance/                    # 隐私政策 / DPA / 安全白皮书
│   ├── security/                      # 安全评估与权限白名单锁
│   └── archive/                       # 历史审计与 QA 报告（只读快照）
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
#    创建 apps/web/.env.local，写入 NEXT_PUBLIC_ADAPTER_BASE_URL=http://127.0.0.1:8000

# 3) 开发模式
npm run dev

# 4) 生产构建
npm run build && npm run start
```

### 3.4 一体化部署（deploy）

```bash
cd deploy

cp .env.example .env
# OA_JWT_SECRET 必须换成强随机密钥（≥32 字符）：
#   保留模板里的 CHANGE_ME_* 会**启动失败 30004**（占位值是仓库公开常量，
#   拿它当签名密钥等于任何人都能自造令牌）。这一步漏了会卡在起容器阶段。
JWT_SECRET="$(openssl rand -hex 32)"
sed -i.bak "s|^OA_JWT_SECRET=.*|OA_JWT_SECRET=${JWT_SECRET}|" .env && rm -f .env.bak
unset JWT_SECRET
# 按需修改 OA_DEPLOY_MODE=saas|private 与各服务连接串

docker compose -f docker-compose.yml up -d        # 私有化
docker compose -f docker-compose.saas.yml up -d   # SaaS 本地联调
```

> 更省事的一条路径是 `bash deploy/install.sh --online`（或 `--offline`）：
> 它会自动生成全部口令/密钥（含 `OA_JWT_SECRET`）并做前置检查——**生产部署优先走它**，
> 上面的手抄模板路径适合本地联调。

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
| 分支策略 | **主干开发**：`main` 唯一主干 + 短生命周期 `feature/*` + PR 合入 + tag 发版；`hotfix/*` 仍须 PR。不设 `develop` / `release/*`（见 `docs/architecture-v1.0.md` §9.4、`CONTRIBUTING.md` §2） |

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
| Python 测试 | pytest | 核心接口合并覆盖率 ≥ 85%（`--cov-fail-under=85`） |
| 前端 Lint | ESLint | 零 error |
| 前端类型检查 | tsc | 零 error |
| 开源协议扫描 | CI `dependency-scan.yml` | 无 GPL/AGPL/SSPL |
| 底座直连扫描 | CI `dependency-scan.yml` | `apps/web` 命中即失败 |

本地执行：

```bash
cd apps/adapter
ruff check . && ruff format --check .
mypy app
OA_BATCH=B21 pytest tests -q --cov=app --cov-report=term-missing
```

## 六、当前进度与已知限制

### 6.1 当前进度（按 24 周四阶段口径）

| 阶段 | 周期 | 状态 | 验收报告 |
| --- | --- | --- | --- |
| 一：底座与适配层攻坚 | 第 1-8 周 | ✅ 已验收 | `docs/phase1-acceptance-report.md` |
| 二：基础功能全量对齐 | 第 9-16 周 | ✅ 已验收 | `docs/phase2-acceptance-report.md` |
| 三：插件体系与标杆场景 | 第 17-20 周 | ✅ 已验收 | `docs/phase3-acceptance-report.md` |
| 四：商用打磨与交付准备 | 第 21-24 周 | ✅ 已验收 | `docs/phase4-acceptance-report.md` |
| W1：总纲对齐切片（A/B） | — | ✅ 已完成 | `docs/api/baseline-w1-2.json`（2 端点冻结基线） |

第三阶段交付内容：插件声明模型与运行时（6 段声明 + 8 层加载校验 + 6 能力封闭集合）、
插件市场与全生命周期（PL1-PL14）、任务引擎与工作流可视化（WF1/WF2）、
3 个标杆场景插件（前端研发 / 内容运营 / 行政人事，均端到端可用）。

### 6.2 当前门禁与质量基线（`main` 实测）

| 项 | 值 |
| --- | --- |
| 端点契约 | 8 套基线合计 **103**（`core 30 / phase2 38 / phase3 16 / phase4 10 / w1 2 / w2 2 / b3 4 / b4 1`），`validate_openapi.py --strict` EXIT 0 |
| 本机测试（无 PG） | **2559 passed / 238 skipped / 0 failed**（收集 2797，2026-11-04 实测，命令见 §一）；含 PG 口径**以 CI junit.xml 工件为准**（最近 success：2026-11-03 run `41641650690`） |
| 覆盖率 | 门槛 **85%**（CI 硬地板 `--cov-fail-under=85`，卡合并覆盖率）；现值以 CI 工件为准。历史快照：91.94%（含 PG，`cedd3e9`）/ 85.87%（无 PG，2026-10-30）。余量判断一律看**绝对单位差**，不看百分比（见 §6.3 第 5 条） |
| 静态门禁 | `ruff check` / `ruff format` / `mypy` 全绿 |
| 部署门禁 | 端点基线 / 迁移双路径 / 依赖白名单 / 镜像锁定 / 插件声明 / 错误码三处一致 均 EXIT 0 |
| 前端门禁 | 禁词 / 占位词 / 页面三态 / 契约快照 / 能力一致性 / 工作台宿主唯一 均 0 命中；`vitest` 72 文件 / 1112 用例全过（2026-11-04 实测） |

### 6.3 已知限制与未验证项

1. **微信支付真实商户联调未做**：需客户提供商户号与密钥，当前为沙箱 + 契约桩验证。
2. **模型提供方密钥未提供**：`marketplace` 不可达，M1/M2 的 happy path 依赖真实密钥才能跑通。
3. **真实环境回执未验证**：GitHub 仓库写操作、公众号/邮件发布回执均为沙箱或契约桩；
   插件在"工具未连接"时按步骤级失败处理，不会谎称已发布。
4. **多副本调度未验证**：V1.0 按单副本交付，任务队列语义在单实例下验证；换 Valkey 时从
   `task_service._advance_record()` 单入口替换。
5. **不要用百分比判断覆盖率余量**：见 §6.2 的口径说明——百分比受分母影响，
   分母变化时百分比可能下降而绝对余量反而上涨。判断余量一律看**绝对单位差**
   （`实得 − ceil(85% × 合并单位)`），不看小数点。
   （历史遗留：本文曾写「分支覆盖率余量为 0：`2176/2840` 刚好达到 76.6% 门槛」，
   两个问题：① 数字早已失真；② **全仓并不存在分支覆盖率门槛**——CI（`ci-adapter.yml`）
   命令行硬地板 `--cov-fail-under=85`（卡合并覆盖率），pyproject 不设，grep 不到任何分支阈值配置。
   "余量为 0"是在跟一个不存在的门槛比，故整句删除。）
6. **CI 已实跑通过，日常仍以本机门禁复核**：2026-11-03 两个 workflow 均 success
   （CI Adapter run `41641650690` / 18m58s，CI Web run `41641657153` / 3m17s）；
   此前“额度用尽、从未运行”的历史状态已结束。提交前仍建议先跑 §五的本地命令，
   避免把 CI 当唯一反馈环。
7. **历史遗留错误码**：已于 B27 收口——`11005`（RETRIEVAL_FAILED）与 `15006`（QUOTA_EXHAUSTED）
   已接线（R7 检索失败 / 额度用尽软提示走 `data.notice_code`），`12002`/`12005` 已按「注销值不复用」
   删除，`12004` 转预留（`[RESERVED]` 放行）；处置口径见 `apps/adapter/scripts/check_error_catalog.py`。

## 七、许可证与开源声明

平台为企业级商业交付物，内部依赖清单与协议合规审计结果见 `docs/adr/0002-infrastructure-license-compliance.md`
与 CI 扫描报告（`dependency-scan.yml`）。底座 Dify 为**修改版 Apache-2.0**，三条规避约束
（SaaS 不使用多租户特性 / 不修改源码 / 不展示品牌）见 ADR-0002 §9.2 与 `docs/decisions/0004` §三。
依赖协议实测口径（2026-10-30 审计 M4/M10）：白名单 MIT / Apache-2.0 / BSD-2/3-Clause / ISC 为主，
传递链含 MPL-2.0（certifi，灰名单：随交付保留许可证文本）/ PSF-2.0 / MIT-0 等宽松协议；
**无 GPL/AGPL/SSPL**；LGPL 成分（sharp 平台二进制）已从生产镜像排除（`--omit=optional`，
R2-ARC-03），分发桌面端/自托管安装包前须复核 LGPL 义务（详见 `docs/architecture-v1.0.md` §8.1/8.2）。
