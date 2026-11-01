# Moy（麦子）V1.0 · 架构 / 性能 / 依赖许可 / 部署配置安全 审计报告

> 审计日期：2026-11-02　|　分支 `main`　|　HEAD `e3734a0`（工作区干净，`git status --porcelain` 空）
> 审计角色：架构师（Architect）　|　维度：①架构与目录结构 ②性能隐患 ③依赖版本与许可 ④部署与配置安全
> 方法：**全部结论来自实跑命令或源码行号实证**，无一条凭印象；命令失败如实记录失败原因与原样输出。
> 只读约束：本报告是本次审计唯一写入的文件，未修改任何源码 / 配置 / 依赖 / 锁文件；临时脚本一律落 `/tmp`。
> 前序基线：`docs/audit-2026-10-30-full-repo-audit.md`、`docs/audit-2026-10-31-full-repo-audit.md`（本报告含逐条闭环核实）。

---

## 一、审计范围与方法（实跑清单）

### 1.1 环境

| 项 | 实测值 |
|---|---|
| Shell | Windows Git Bash（路径 `/c/Users/15864/Desktop/麦子`） |
| 后端解释器 | `apps/adapter/.venv/Scripts/python.exe` → **Python 3.13.14**（存在，已使用；非 README 所述最低版本 3.11） |
| 后端包数 | `pip list` **53 个**（含项目自身 `officeai-adapter==1.0.0`） |
| 后端 py 文件 | `app/` 下 210 个非测试 .py；`tests/` 下 181 个（含 `test_*.py`） |
| 前端 ts/tsx | `apps/web/src` 174 个（其中 34 个 `*.test.tsx`，非测试 93 个 .tsx） |
| 前端构建产物 | `apps/web/.next` **不存在** → bundle 体积无法实测（见 §四盲区） |

### 1.2 实跑命令与结果（原样摘录）

```
# 仓库状态
git log --oneline -3            → e3734a0 test(web): 补测第三十六批…；0c6ab08…；e0bbcb9…
git status --porcelain           → （空）

# 门禁：依赖方向
python deploy/validate_import_directions.py
  → 结论：违规 23 条（基线内 0 / 基线外 23 / stale 0），环 3 个（基线内 3 / 新增 0 / stale 0），
     扫描 203 个文件，批次时钟 未启用
  → REAL_EXIT=1                      ★ 红
OA_BATCH=B21 python deploy/validate_import_directions.py
  → 违规 23 条（基线内 0 / 基线外 23 / stale 0）… EXIT=1     ★ 仍红
python deploy/validate_import_directions.py --baseline deploy/import_direction_baseline.txt
  → 违规 23 条（基线内 23 / 基线外 0 / stale 0），环 3 个（基线内 3）
  → [OK] 分层依赖方向合规 … EXIT=0                            （见 A-08：仅带参才绿）

# 门禁：其余 16 个
validate_repository_parity.py   EXIT=0  20 单元 / 0 判定 / 0 WARN / 0 FAIL / 0 stale
validate_images.py              EXIT=0  [OK] 镜像清单校验通过
validate_secrets.py             EXIT=0  [OK] 密钥兜底扫描通过
validate_openapi.py             EXIT=0  core=30 phase2=38 phase3=16 phase4=10 w1=2（未实现 0）
validate_openapi.py --strict    EXIT=0  [OK] G1-G7 全部通过
validate_deploy.py              EXIT=0  部署形态一致
validate_gate_wiring.py         EXIT=0  16 个 validate_*.py 全部被 workflow 引用
validate_config_names.py        EXIT=0
validate_packaging.py           EXIT=0
validate_plugins.py             EXIT=0  6 份声明
validate_migrations.py          EXIT=0
validate_schema_retention.py    EXIT=0
validate_pg_markers.py          EXIT=0
validate_offline_pkgs.py        EXIT=0
OA_BATCH=B21 validate_batch_clock.py EXIT=0  台账 18 条（未修复 0）
validate_deps.py                EXIT=1  ★ 红：[FAIL] pyjwt：未登记进 §8.1 清单或未加入 DOCUMENTED_PACKAGES

# 前端门禁
node scripts/check-banned-terms.mjs      → 扫描 115 个文件，0 命中（EXIT=0）
grep -i dify apps/web/src               → about/page.tsx:39 命中 1 处（见 A-12）

# 依赖安全与许可
uvx pip-audit -r /tmp/req_audit.txt --no-deps  → No known vulnerabilities found（51 包）
npm audit --omit=dev                            → found 0 vulnerabilities
npm outdated                                    → 12 项落后（8 个大版本）
pip list --outdated                             → 10 项，全为补丁级
（许可）importlib.metadata 读 53 个已装包的 License-Expression/Classifier 字段
（因 pip-licenses 不在 venv 内、且不允许写入 venv，改用标准库只读读取同一事实源）

# 结构 / 体量 / 端口 / 镜像
find … | xargs wc -l                            → >800 行 20 个；>1500 行 1 个
自写解析器读 3 份 compose 的 ports: 块          → 见 §二 A-02
grep "image: " deploy/docker-compose*.yml       → 与记忆基线逐项比对（见 §3.5）
```

**失败/受限记录（如实）**：
- `pip-audit` 无法直接审计 `officeai-adapter==1.0.0`（本地包不在 PyPI）→ 剔除该项后审 51 包；`pip==26.1.2`（包管理器自身）一并剔除。
- `pip-licenses` 未安装于 venv，且审计红线不允许改动 venv → 改以 `importlib.metadata` 读 dist-info 的许可证字段（同一事实源，只读）。
- `nginx -t` 需 Linux 环境，本轮无法执行（延续前轮未决项）。
- 无 PostgreSQL 实例 → 212 个 PG 用例按设计 skip；执行计划类结论一律进 §四盲区，不臆断。
- `apps/web/.next` 不存在 → 不做 bundle 数字结论。

---

## 二、问题清单

### 2.1 高（3 项）

| 编号 | 维度 | 问题描述 | 证据（文件:行 / 命令输出） | 影响 | 改进建议 |
|---|---|---|---|---|---|
| **A-01** | 依赖治理 / 门禁 | **`validate_deps.py` 在 HEAD 上实红**：`pyproject.toml` 已按 2026-10-30 P0-1 迁到 `pyjwt>=2.8,<3.0`，架构文档 §8.1 也已登记 `pyjwt`，但脚本的 `DOCUMENTED_PACKAGES` 白名单**仍留着 `python-jose`、没有 `pyjwt`**。CI 对该门禁是裸调用 → **main 分支 CI 必红** | 命令：`python deploy/validate_deps.py` → `[FAIL] pyjwt：未登记进 §8.1 清单或未加入 DOCUMENTED_PACKAGES`，`EXIT=1`<br>白名单：`deploy/validate_deps.py:55-79`（含 `"python-jose"`，无 `"pyjwt"`）<br>依赖声明：`apps/adapter/pyproject.toml:48`<br>文档已登记：`docs/architecture-v1.0.md:1398`<br>CI 裸调用：`.github/workflows/ci-adapter.yml:758` | 门禁红即交付阻断；更坏的是"白名单留着已删依赖"说明**该门禁自 P0-1 迁移起就没再被人工跑过**，10-31 审计"16 个门禁全 exit 0"的结论对这一项不成立 | ① 把 `python-jose` 换成 `pyjwt`；② 给 `validate_deps.py` 补一条**反向检查**（白名单项必须能在 pyproject 中解析到，否则报 stale），杜绝"删依赖不删白名单"；③ 本地 README §六 命令清单补上 `validate_deps.py` |
| **A-02** | 部署 / 配置安全 | **全量私有化（`docker-compose.yml`）与 SaaS（`docker-compose.saas.yml`）两套编排都没有 TLS 终止点**：nginx 只存在于 lite 版（`deploy/nginx/lite.conf`、`lite-tls.conf`，且被 `docker-compose.lite.yml:528 proxy` 引用）。两套 compose 里 `adapter`/`web` 端口均为**省略 host IP = 绑全部网卡**，明文对外；`deploy/k8s/` 只有 `README.md`（自述"manifests 均未创建"），因此 **SaaS 生产部署形态在仓库内没有实体** | 端口：`deploy/docker-compose.yml:589`（`"${OA_ADAPTER_PORT:-8000}:8000"`）、`:638`（web 3000）；`deploy/docker-compose.saas.yml:396`、`:424`<br>服务清单：base/saas **均无 nginx / 入口反代服务**（base 的 `ssrf_proxy:414` 是底座出站 squid，非入口）<br>k8s：`ls deploy/k8s/` → 仅 `README.md`（20-32 行自认 manifests 全缺）<br>对照（已修）：`docker-compose.saas.yml:157,177,178` 已绑 `127.0.0.1` | 前轮 H3 只收了"基础设施端口"，**应用端口 + 边缘 TLS 从未收口**：SaaS 版把 8000/3000 直接摊在全网卡上，无 HSTS/CSP/限流边缘层（`next.config.js` 下发的 HSTS 在明文 HTTP 下浏览器**不予采纳**）；私有化全量版同理。SaaS 上线路径目前只有"自己再搭一层反代"，无编排保证 | ① 两套 compose 增加 nginx 服务（复用 `lite-tls.conf`），app 端口改 `127.0.0.1:…` 或 `expose:`；② 若坚持由云 LB 终止 TLS，必须在 `docs/deployment-guide.md` 明确前置条件 + 安全组最小开放，并把 `X-Forwarded-Proto` 信任链写清；③ k8s manifests 作为 SaaS 交付物立项（当前是"占位即交付"） |
| **A-03** | 性能 / 可用性 | **租户全量导出把整个 zip 常驻内存，且无并发闸门、容器无内存上限**：`_build_archive` 用 `io.BytesIO()` 拼整包并返回 `bytes`，行数上限 200,000；压缩本身已 `to_thread`（这点做得对），但**内存峰值 = 包体大小**，同时没有信号量/单飞限制；三套 compose **零内存限制**（`mem_limit`/`deploy.resources` 全仓 0 处） | `apps/adapter/app/business/tenant_data_service.py:84`（`MAX_EXPORT_ROWS = 200_000`）、`:688`（`buffer = io.BytesIO()`）、`:705`（`archive.open`）、`:725-726`（分块 `to_thread`）<br>无并发控制：同文件 `grep -i semaphore` → 0 命中<br>端点：`apps/adapter/app/api/v1/tenant_data.py:148`（`POST /api/v1/ops/tenant-data/export`）<br>资源限制：`grep -n "mem_limit\|deploy:\|resources:" deploy/docker-compose*.yml` → **0 命中** | 200k 行的包按 1KB/行估算即 ~200MB，两个并发导出就能打死 1-2GB 的 adapter 容器；容器无内存上限时**拖垮的是宿主**，不只是本服务。当前只有 owner/org_admin 能触发，非外部攻击面，但属单点可用性缺陷 | ① 用 `spooled tempfile` + `StreamingResponse` 边生成边下发，不返回 `bytes`；② 加进程级 `asyncio.Semaphore(1~2)` + 同租户去重；③ 三套 compose 至少给 adapter 配 `mem_limit`/`cpus`（也补 saas 的健康检查，当前仅 5 处 healthcheck） |

### 2.2 中（8 项）

| 编号 | 维度 | 问题描述 | 证据 | 影响 | 改进建议 |
|---|---|---|---|---|---|
| **A-04** | 性能 | **运营端租户列表：全量拉取后内存切页 + 每行 3 次查询（N+1×3）**。与前轮 H2 完全同型，但当时只修了成员列表。代码注释自认"用量只对当前页逐个聚合"，即已刻意收窄到页内，但**页内仍是 3×page_size 次往返** | `apps/adapter/app/business/ops_service.py:216`（`page_size: int = 20`）、`:232`（`tenants = await self._store.list_tenants(...)` 全量）、`:233`（`total = len(tenants)`）、`:235`（内存切片）、`:238`（`get_active_subscription`）、`:240`（`tenant_total`）、`:247`（`seats_used`）→ 单页 20 条即 **1+60** 次查询；`api/v1/ops.py:104` 允许 `page_size` 到 **200** → 1+600 | 平台侧租户数增长后线性恶化；运营后台首屏变慢，PG 连接被短查询刷屏 | 分页下推 SQL（`LIMIT/OFFSET` + `count(*) over()`）；三个聚合改一条 `LEFT JOIN LATERAL` 或 `IN (...)` 批查；与 `member_service.list_members_page` 的修法同构 |
| **A-05** | 性能 | **`list_queued` 仍无 `LIMIT`**（前轮 M2 只闭环了 `next_queued` 与 `_queue_rank`）；同时仓库层仍广泛使用 `SELECT *` | `apps/adapter/app/db/repositories/task/postgres.py:438`（`def list_queued`）、`:455-466`（SQL 无 `LIMIT`）<br>已修对照：`:428`（`next_queued … LIMIT 1`）、`:469-500`（`_queue_rank` 改单条 `COUNT(*)`）<br>`SELECT *` 23 处 / 6 文件：`grep -rn "SELECT \*" apps/adapter/app` → artifact_repo、budget_repo、conversation/postgres、job_repo、memory/postgres、task/postgres | 队列积压（如插件被限流/长时间失败）时一次查询把整队列拉进内存；`SELECT *` 让索引-only 扫描不可用，且表结构变更时静默扩大载荷 | `list_queued` 加 `LIMIT :n`（前端本就只展示前 N 位）；`SELECT *` 改列显式列出（顺带消除 `tenant_id` 之外的冗余大字段） |
| **A-06** | 性能 | **批量写仍是"逐条一个事务"**：`artifact_repo.add_artifacts` 与 `task.create_steps` 都在循环体内 `async with tenant_scope(...)`，N 条 = N 个事务 = N 次 commit+RTT | `apps/adapter/app/db/repositories/artifact_repo.py:372`（`add_artifacts`）→ `:385`（`for record in records`）→ `:388`（循环内 `tenant_scope`）<br>`apps/adapter/app/db/repositories/task/postgres.py:501`（`create_steps`）→ `:511`（循环内 `tenant_scope`）<br>后者的取舍已在注释里写明（"§3.3 硬约定 1：长任务短事务"） | 一次任务产出 10 个产物/步骤即 10 次事务；PG 上 `fsync` 放大明显，压测时是第一批瓶颈 | `create_steps` 保持现约定可接受；`add_artifacts` **没有**同类约束（产物是任务末尾一次性落），改单事务批量 `executemany`/`VALUES (...),(...)` 即可 |
| **A-07** | 依赖 / 供应链 | **基础设施镜像存在"补丁级浮动"**：`pgvector/pgvector:0.8.7-pg18-bookworm` 只锁到 PG **主版本 18**（18.6/18.7 由上游漂移）、`valkey/valkey:9.2-alpine`、`nginx:1.28-alpine`、`ubuntu/squid:6.13-25.04_beta`（**beta 通道**）同理；`images.lock` 登记了 20 条 sha256，但 **compose 运行时按 `name:tag` 引用、不校验 digest**（自研镜像的 Dockerfile `FROM` 已锁 digest，基础设施侧未锁） | `deploy/docker-compose.yml:42`（pgvector-pg18）、`:93`（valkey 9.2-alpine）、`:415`（squid `6.13-25.04_beta`）；`deploy/docker-compose.saas.yml:26,68,325`；`deploy/docker-compose.lite.yml:531`（`nginx:1.28-alpine`）<br>`validate_images.py` EXIT=0（仅校验"清单↔编排 tag 一致"，不校验 digest）<br>对照（已锁）：`apps/adapter/Dockerfile:17,58`、`apps/web/Dockerfile:15,46,73` 均 `FROM …@sha256:…` | 与项目"最新稳定版**精确标签**"策略存在口径差：文档说精确，编排实际是"主/次版本浮动"；离线交付可复现性依赖 images.lock，但运行时无强制 | ① `nginx`/`valkey` 收到补丁位（如 `1.28.3-alpine`）；② squid 的 `_beta` 通道需书面风险接受（这是唯一走 beta 的基础设施组件）；③ 评估 compose 运行时 `@sha256` 引用（与 images.lock 对账脚本已有，改引用即可复用） |
| **A-08** | 架构 / 工具可信度 | **依赖方向门禁的默认参数失效**：`--baseline` 的 `default=None`，而 `DEFAULT_BASELINE` 常量定义了却**从未被使用**（`if baseline_path is not None` 之后才加载）→ 裸跑时基线为空，23 条**已登记**违规全部被报成"未登记"，**exit 1**。基线文件头与 `--cycle-baseline` 的写法（"默认即此文件"）让人以为两者对称 | `deploy/validate_import_directions.py:210`（`DEFAULT_BASELINE` 定义）、`:1152`（`--baseline` `default=None`）、`:1202-1209`（`if baseline_path is not None:` 才加载）<br>实测：无参 EXIT=1 / `--baseline …` EXIT=0，两次"违规 23 条"但"基线内"N 分别为 0 与 23 | 本地裸跑必红 → 长期"红灯免疫"（alarm fatigue），真新增违规时会一起被忽略；两类门禁参数风格不一致，接手的工程师会误判 | `--baseline` 默认改 `DEFAULT_BASELINE`（与 `--cycle-baseline` 对称）；`README §六` 去掉多余的 `--baseline` 显式参数或反之，二者取一 |
| **A-09** | 前端性能 / 架构 | **`'use client'` 覆盖 81/93 非测试 tsx（87%）**，RSC/SSR 收益基本放弃；**无任何列表虚拟化**（无 `react-virtual`/`tanstack-virtual` 依赖）；三个页面用 3s 轮询（knowledge/workbench） | `grep -rl "'use client'" src --include=*.tsx` → 81；`find src -name "*.tsx" -not -name "*.test.tsx"` → 93<br>虚拟化：`grep -rn "virtual" src package.json` → 0 命中<br>轮询常量：`knowledge/[baseId]/page.tsx:30`、`workbench/[pluginId]/page.tsx:30`（3000ms）、`workflows/[taskId]/page.tsx:27`（5000ms）+ `:30`（节流 300ms） | 首屏 JS 体积与 hydration 成本整体偏高（`next.config.js` 只对 `lucide-react`/`date-fns` 做 `optimizePackageImports`）；3s 轮询 ×N 个打开标签页 = 对 adapter 的稳态压力 | 列表页/容器布局页改 Server Component（`(main)/layout.tsx` 与纯展示页优先）；长列表（team/ops-tenant/plugins）接虚拟化；轮询改 SSE 推送优先、或 `refetchIntervalInBackground: false` + 退避 |
| **A-10** | 部署 / 安全 | **nginx 硬化只覆盖 lite 版且不完整**：有 TLS1.2/1.3 + HSTS + X-Frame-Options + nosniff + `/api/` `limit_req 10r/s` + 真实 IP 透传，但**无 `ssl_ciphers` / `ssl_dhparam` / OCSP stapling**（`ssl_prefer_server_ciphers off` 后套件完全交给客户端偏好） | `deploy/nginx/lite-tls.conf:53`（`ssl_protocols TLSv1.2 TLSv1.3`）、`:54-56`（`prefer off`/session 配置）、`:60-63`（安全头）、`:79`（`limit_req zone=api_limit burst=20 nodelay`）<br>`grep "ssl_ciphers" deploy/nginx/*.conf` → **0 命中**<br>限流域定义：`deploy/nginx/nginx.conf:59`（10r/s、10m） | TLS 套件不可控（客户端可协商到较弱套件）；无 OCSP 时吊销状态靠客户端 CRL 回源；限流只作用于 lite | 补 `ssl_ciphers 'ECDHE-…:!aNULL:!MD5'` 显式套件 + OCSP；把 `lite-tls.conf` 提升为"所有部署形态共用"的边缘模板（与 A-02 一并解决） |

### 2.3 低（8 项）

| 编号 | 维度 | 问题描述 | 证据 | 影响 | 改进建议 |
|---|---|---|---|---|---|
| **A-11** | 架构债 | 超大文件仍在：**>800 行 20 个**（`model_service.py` 1734、`plugin_service.py` 1452、`tenant_data_service.py` 1352、`task/runner.py` 1312、`usage_service.py` 1177、`billing/postgres.py` 1127、`rag_service.py` 1089、`memory_service.py` 1059…），**>1500 行 1 个**；前端 `lib/types.ts` 1751、`lib/api-client.ts` 1593；门禁脚本自身 `validate_repository_parity.py` 3342 | `find apps/adapter/app -name "*.py" | xargs wc -l | sort -rn`；`find apps/web/src …` | 单文件认知负荷高、评审粒度粗；`model_service` 已跨 1700 行，改动冲突概率高 | 先拆 `model_service`（模型目录/生成/用量三段）与 `plugin_service`（清单/安装/授权三簇）；`types.ts` 按域拆分（前端类型是编译期成本，优先级低） |
| **A-12** | 架构红线（前端禁词） | 前端"底座字样"红线**实际覆盖窄于表述**：禁词表 58 条全是技术概念词（chunk/segment/milvus/rag/embedding…），**没有"底座品牌名"**；`about/page.tsx:39` 存在 1 处 `Dify`（第三方组件许可证署名）。因此 `check-banned-terms.mjs` 的"0 命中"**不能**用来证明"前端不出现底座字样" | `apps/web/scripts/check-banned-terms.mjs:49-110`（词表全文，无 Dify/底座）<br>`grep -rn -i dify apps/web/src` → `src/app/(main)/about/page.tsx:39`<br>脚本自检 EXIT=0"扫描 115 个文件，0 命中" | 红线证据链有缺口：审计若只引用"0 命中"会得出过强结论（本次据此**不下**"前端无底座字样"的结论） | 要么把品牌名加入词表并对 about 页做白名单例外（显式豁免比不覆盖好），要么在红线表述里把"底座字样"限定为"底座接口/URL/概念术语"，两者取一 |
| **A-13** | 配置安全 | `.env.example` / `.env.lite.example` 里 `OA_POSTGRES_USER`、`OA_KEYCLOAK_ADMIN`、`OA_OBJECT_STORE_ACCESS_KEY` 取的是**弱默认值**（与常见默认口令同集合；按红线条例不复述值）。`install.sh` 路径会用 `openssl rand` 重生成，但"手抄模板"路径（README 允许 `cp .env.example .env`）会留下弱值 | `deploy/.env.example:42,68,138`；`deploy/.env.lite.example:67,85`（行号+字段名；值不记录）<br>`deploy/install.sh:346-357`（openssl rand 生成 6 项口令 + 4 项密钥）<br>`validate_secrets.py` EXIT=0（只拦"密钥类变量带明文兜底"，不管"用户名是默认值"） | 私有化现场若不走 install.sh，PG/Keycloak 管理员名即公开默认值（口令仍是随机的，故只列低） | `.env.example` 把这三个也写成 `<请替换>` 形态；`validate_secrets.py` 增加"用户类变量等于已知默认值"提示项（WARN 级） |
| **A-14** | 部署 / 回滚 | 11 条迁移中**只有 7 条有 down SQL**（`0006`-`0012`），`0002`-`0005` 无回滚脚本 → `rollback.sh` 无法整链回退（只能回退后半程） | `ls deploy/migrations/*.sql` → 0002…0012（11 个）；`ls deploy/migrations/rollback/` → `0006…0012.down.sql`（7 个）<br>`deploy/rollback.sh:242`（DRY_RUN 的 `migrate --rollback` 调用） | 升级失败回退到 0005 之前不可自动化，现场只能"重装+恢复备份" | 补 0002-0005 的 down SQL，或在 `rollback.sh` 里显式声明"最低可回退点=0006"并在文档标注 |
| **A-15** | 部署 / 灾备 | 备份恢复的**代码级校验很扎实**（dump 魔数 + sha256 与 meta.json 比对 + 行数基线 + 样本行比对），但**仓库内没有演练记录/证据文件**；`rollback.sh` 也无镜像 digest 校验（回退按 tag） | `deploy/backup.sh:271,288`（`dump_sha256` 落 meta.json）、`:166`（`db_verify_rows`）<br>`deploy/restore.sh:125-136`（sha256 不一致即 abort）、`:414-415`（`verify_row_counts` + `verify_sample_rows`）<br>`grep -rn "演练\|drill" docs/*.md` → 仅见历史 gap 文档的"未完成"清单，无演练产物 | 灾备能力"有实现、无证据"，验收（PRD AC-13/AC-15）缺可引用材料 | 跑一次 clean-room 演练并把输出（六步 + 行数校验 + 回滚点）落 `docs/drills/`，纳入交付证据 |
| **A-16** | 性能（小项） | 三处背景/关联路径的小 N+1：① 分群回填 `_run_id_of` 每条命中查一次；② 知识库**内存后端**列表对当前页记录逐条 `count_documents`（PG 后端已用 JOIN，两条路径口径不一致）；③ 插件前置依赖 `get_installation` 逐项查 | ①`apps/adapter/app/business/segment_service.py:241`（循环 `_write_one`）、`:259`（`run_id = hit.run_id or await self._run_id_of(...)`）<br>②`apps/adapter/app/db/repositories/knowledge_base_repo.py:296-297`<br>③`apps/adapter/app/business/plugin_service.py:1287` | 均在后台/小集合路径（生产 `OA_BUSINESS_STORE=postgres`，②仅影响 dev/测试）；不构成线上瓶颈，但②的"两后端一快一慢"会掩盖真实性能 | ①`run_id` 批查；②内存后端也做一次预分组计数；③`requires_plugins` 一次 `IN` 查 |
| **A-17** | 部署（SaaS） | `deploy/k8s/` **只有 README**：`securityContext` / `runAsNonRoot` / `readOnlyRootFilesystem` / `resources.limits` / `NetworkPolicy` / `Secret` 引用**全部不存在**（README 自认"阶段四验收范围外"）。好消息是**没有虚假声明**（`docs/deployment-guide.md` 未提 k8s） | `ls -la deploy/k8s/` → 仅 `README.md`；`grep -rn "securityContext\|NetworkPolicy\|kind:" deploy/k8s/` → 0 命中<br>`deploy/k8s/README.md:3,11`（明确"manifests 均未创建"） | SaaS 多租户的"底座仅内网 Service + NetworkPolicy 只允许 adapter 访问"这条设计**没有实现载体**；生产安全基线（非 root/只读根/资源限制）在编排层缺位（Dockerfile 层已做非 root，可部分对冲） | 立项产出 `base/` + `adapter/` + `web/` 最小三件套（Deployment+Service+NetworkPolicy+resources），HPA/PDB 随后 |
| **A-18** | 依赖版本 | 版本落后分为两类：**前端 12 项**（8 个大版本跨越：typescript 5.9.3→7.0.2、tailwindcss 3.4.19→4.3.3、zustand 4.5.7→5.0.16、date-fns 3.6.0→4.4.0、lucide-react 0.454.0→1.66.0、sonner 1.7.4→2.0.8、tailwind-merge 2.6.1→3.7.0、@types/node 20→26；补丁级：@tanstack/react-query 5.105.0→5.105.1、eslint-config-next 16.4.2→16.4.3、postcss 8.5.23→8.5.32、prettier 3.3.3→3.9.10）；**后端 10 项全为补丁/次版本**（fastapi 0.156.8→0.162.0、pydantic 2.14.1→2.14.2、anyio/websockets/python-multipart/mypy/opentelemetry-api 各 1 补丁位） | `npm outdated`（12 行原样）<br>`pip list --outdated --format=json`（10 项原样）<br>`package.json` 依赖用 `^` 区间而非精确 pin（可复现性由 `package-lock.json` 承担） | 后端落后无破坏性风险；前端 8 个大版本一次性升级风险高，不宜与交付同批 | 后端补丁批随锁重建；前端按"tailwind 4 / typescript 7 / zustand 5"单独立项（与 10-31 审计 L7 结论一致，**未推进**） |

> 注：另有若干**非缺陷**验证结果（避免误读为问题）：`apps/adapter/app/clients/dify_client.py` 仍是全仓唯一底座调用出口（`grep -rln "httpx\.|requests\.(get|post)" app/` → 仅 `dify_client.py`、`keycloak_client.py`；`api/`、`business/` 对 `app.clients` 的导入 **0 处**）；前端唯一网络出口 `api-client.ts`（全仓 `fetch(` 仅 2 处，均在 `api-client.ts:310,600`）；`business/` 与 `services/` **未发现重复实现**（同名公开方法逐处核对：`list_documents` 是 `rag_service` 委托 `DocumentService`，仅 1 份实现）；React 全树 `19.3.1 deduped` 单实例。

---

## 三、前轮问题闭环核实表

| 前轮编号 | 结论 | 证据 |
|---|---|---|
| **H2** 成员列表 N+1 / 内存分页 | **已修复** | `business/member_service.py:165`（新增 `list_members_page`）、`:264`（`links_of_users` 单批 IN 查询）、`:216`（角色只遍历一次）；分页下推：`:192-196`（无部门过滤时**先切片再取关联**）；`services/identity_service.py:255` 已改为委托 `list_members_page`，内存 `paginate` 消失。残余：`member_ids_of_role` 仍按角色数 N 次调用（`:216`），量级 = 角色数（个位数），可接受 |
| **M1** Redis 限流窗口不滚动 | **已修复** | `core/ratelimit.py:147`（`RedisRateLimitStore.hit`）、`:163`（`pipeline.set(redis_key, 0, nx=True, ex=window_seconds)` → 锚点只在窗口首命中写入）、`:165`（`ttl` 取真实剩余）；内存实现 `:103-125` 按 `window_start` 滚动 + `_prune_locked` 容量保护。两后端行为已对齐（差异仅剩边界取整的 ±1s） |
| **M2** 排队查询无 LIMIT / `_queue_rank` 重复 / `create_steps` 逐条事务 | **部分修复** | `next_queued` 已 `LIMIT 1`（`task/postgres.py:428`）✅；`_queue_rank` 已改单条 `COUNT(*)` 行值比较（`:469-500`）✅；**`list_queued` 仍无 LIMIT**（`:438`,`:455-466`）❌；`create_steps` 仍一步一事务（`:501`,`:511`）——注释写明系"§3.3 短事务硬约定"的**有意保留**，非遗漏（本报告 A-06 只对 `add_artifacts` 提改进） |
| **L6-a** rag 事件循环内同步 tempfile | **已修复** | `api/v1/rag.py:176`（`with tempfile.TemporaryFile()`）、`:190`（`await asyncio.to_thread(tmp.write, chunk)`）、`:192`（`await asyncio.to_thread(tmp.read)`） |
| **L6-b** segment fire-and-forget 无强引用 | **已修复** | `business/segment_service.py:221`（`create_task`）、`:224-227`（`_BACKGROUND_TASKS.add(task)` + `add_done_callback(_BACKGROUND_TASKS.discard)` + 失败日志回调） |
| **L6-c** SSE 每事件 2 次 refetch | **已修复** | `app/(main)/workflows/[taskId]/page.tsx:74-84`（`refetchTimer` + `throttledRefetch`，`REFETCH_THROTTLE_MS=300`）、`:86-90`（卸载清理定时器）；SSE 回调改为 `throttledRefetch`（`:102-112`） |
| **H3** SaaS compose 端口暴露 | **已修复（基础设施面）**，应用面见 A-02 | `docker-compose.saas.yml:157`（milvus `127.0.0.1:${OA_MILVUS_PORT:-19530}`）、`:177`（对象存储 8333）、`:178`（**admin 23646** 同样绑回环）；对照 base 版 `:192,226,227` 口径一致。**但** adapter/web 仍是全网卡（`:396`,`:424`），且两套编排无 TLS 终止 → A-02 |
| **L1** 超大文件 | **未修复** | >800 行 20 个、>1500 行 1 个（`model_service.py:1734`）与前轮同量级（前轮口径含 tests 为 43） |
| **L2** 循环依赖 3 组 | **未修复（无新增）** | `validate_import_directions.py` 输出：`环 3 个（基线内 3 / 新增 0 / stale 0）`；`same_layer_cycle_baseline.txt` 仍登记 order↔subscription（B25）、policy↔security（B27）、job_runner↔tasks（B25）；方向违规 23 条全部在基线内、`批次时钟 B21` 下**未到期**（B23-B27） |
| **L4** validate_secrets 覆盖面 | **已修复** | `deploy/validate_secrets.py:52`（`SCAN_SUFFIXES = {".yml",".yaml",".sh",".example"}`）、`:56`（`.env` 前缀文件参与扫描）；实跑覆盖 `deploy` 下 `.example/.sh/.yaml/.yml`，EXIT=0 |
| **L5** 镜像 digest 强校验 | **未修复** | compose 仍按 `name:tag` 引用（`docker-compose.yml:42,93,177,247…`），`images.lock` 仅作对账；新增量化见 A-07（含 patch 级浮动与 beta 通道） |
| **L7** 依赖大版本落后 | **未修复** | `npm outdated` 仍 12 项（8 个大版本）；后端 10 项全补丁级（前轮未列后端，本轮补） |
| **M7** token + CSP 纵深 | **未修复（有据决策）** | `apps/web/next.config.js:48-60`：`script-src 'self' 'unsafe-inline'`、`connect-src 'self' http: https:`；注释说明 nonce 化需 middleware 改造。token 存储未在本轮复查范围（属代码/安全维度） |
| **M5/M6** 口径漂移 | **已修复** | `ci-web.yml:87-88` 注释（86.4/80.4/81.1/87.6）与 `apps/web/vitest.config.mts:302-307` 实际阈值**逐项一致**；`pyproject.toml` 已含 `[tool.coverage.report] fail_under`（前轮 P1-7） |
| **前轮"16 门禁全 exit 0"** | **不成立（本轮推翻）** | `validate_deps.py` EXIT=1（A-01）；`validate_import_directions.py` 裸跑 EXIT=1（A-08） |

---

## 四、性能未验证盲区（需真库 / 压测才能定论）

1. **后台任务认领计划**：`job_repo.py:558-575` 的 `(status='queued' AND run_after<=now()) OR (status='running' AND locked_until<now())` + `ORDER BY run_after, created_at`，是否走 `idx_job_due`（部分索引 `WHERE status IN ('queued','running')`，`migrations/0010:61-62`）。OR 的第二支含 `locked_until` 判断不在索引内，**可能退化为索引扫 + 过滤**；需 `EXPLAIN (ANALYZE, BUFFERS)`。
2. **排队位次与队列拉取**：`plugin_task` 现有索引为 `(tenant_id, plugin_id, started_at DESC)` / `(tenant_id, status, started_at DESC)`（`migrations/0003:195-198`），而 `list_queued`/`_queue_rank` 同时按 `tenant_id + plugin_id + status` 过滤并 `ORDER BY (started_at, task_id)`。**没有一条索引同时覆盖 plugin_id 与 status**，且排序键含 `task_id`（行值比较）。三种写法在同一份数据上的实际计划未验证；候选改进是 `(tenant_id, plugin_id, status, started_at, task_id)`。
3. **导出链路**：200k 行导出的真实内存峰值、`_ZIP_CHUNK_ROWS=500` 的分块大小是否最优、zip 压缩在 `to_thread` 下的线程池竞争（默认 executor 与导出并发）。
4. **`usage_event` 跨期聚合 / `plugin_task` FILTER 统计 / `memory_embedding` HNSW 检索**：均为大表 + 聚合或向量检索，无真库无法给结论（延续前轮盲区）。
5. **多副本语义**：内存限流（`InMemoryRateLimitStore`）与进程内单例（`get_*_store` 的 `_memory_store`）在多副本下的实际约束；本机只有单进程验证条件。
6. **SSE 长连接的资源占用**：SSE 计 1 次限流（`nginx limit_req` 按 `$binary_remote_addr`，长连接只算握手），连接数上限由 `worker_connections 1024`（`nginx.conf:17`）与 adapter 的异步连接池共同决定，未压测。
7. **前端 bundle 体积**：`apps/web/.next` 不存在，无法给首屏 JS 数字；`'use client'` 87% 的推断性影响（A-09）需一次真实 `next build` + `--analyze` 才能量化。
8. **记忆/分群写入放大**：`memory_embedding` 的 HNSW 建索成本、`segment_map` 回填（`segment_service._write_all` 逐条 `upsert`）的写入放大比，均未测。

---

## 五、该维度总体结论（3-5 句）

1. **架构骨架仍然可信**：五层分层 + 红线（`dify_client.py` 唯一底座出口、`api-client.ts` 唯一前端出口、`services/` 唯一协议转换）经实跑 grep 复核**全部成立**，20 个仓储单元双实现一致性 0 判定、6 份插件声明、96 端点 `--strict` 全通过，README §二 目录树 24 条声明路径**逐一存在**、无虚构。
2. **但门禁体系出现两处"绿灯假象"**：`validate_deps.py` 在 HEAD 上**实红**（PyJWT 迁移后白名单未更新，CI 裸调用必红），`validate_import_directions.py` 裸跑**恒红**（`--baseline` 默认值为 None 导致 23 条已登记违规全被判为未登记）——这直接推翻前轮"16 门禁全 exit 0"的结论，是本次最该先修的两条。
3. **前轮性能整改 6 条里 5 条真实落地**（成员 N+1 与分页下推、Redis 限流锚点、排队位次、rag 上传 IO、segment 强引用、SSE 节流），但**同型缺陷换了个入口复发**：运营端租户列表仍是"全量 + 内存切页 + 每行 3 查"，`list_queued` 仍无 LIMIT——说明修的是"点"，没有沉淀成"仓储层分页/批查询"的规则。
4. **部署面是当前最大短板**：基础设施端口已按 ARC-01 收口（SaaS milvus/对象存储/无鉴权 admin 口均绑回环，H3 闭环），但**两套编排都没有 TLS 终止**、应用端口全网卡暴露、`deploy/k8s/` 只有一份占位 README（securityContext/NetworkPolicy/资源限制全缺）、三套 compose **零内存限制**——SaaS 生产形态实质上还没有交付物，这才是私有化/SaaS 上线前必须先解决的。
5. **依赖面干净但不"最新"**：`pip-audit` 0 漏洞（51 包）、`npm audit --omit=dev` 0 漏洞、无 GPL/AGPL/SSPL（灰名单成分仅 MPL-2.0 certifi/pathspec、PSF-2.0 typing_extensions/greenlet、MIT-0 cffi、BSD-2 Pygments），与记忆基线镜像版本逐项吻合（Dify 1.17.1 / Keycloak 26.8.1 / PG 18 / Valkey 9.2 / Milvus 3.0.3 / etcd 3.7.2 / SeaweedFS 4.48 / Prometheus 3.15）；缺口在**补丁级浮动 + 无 digest 强制**（A-07）与前端 8 个大版本跨越（A-18）。

### 建议修复顺序

| 顺序 | 项 | 理由 |
|---|---|---|
| 1 | **A-01** 依赖门禁红 | 一行白名单改动即可解除 CI 阻断，且要补"白名单 stale"反向检查 |
| 2 | **A-08** 门禁默认参数 | 同一类问题：让本地裸跑与 CI 同口径，否则后面所有整改都缺少可信红灯 |
| 3 | **A-02 / A-10 / A-17** 部署形态（TLS 终止 + 端口收口 + k8s 最小三件套） | SaaS 上线硬前置 |
| 4 | **A-03** 导出内存与并发闸门 + compose 资源限制 | 唯一可能"拖垮宿主"的可用性缺陷 |
| 5 | **A-04 / A-05 / A-06** 仓储层分页与批写规则化 | 把已修过的经验沉淀成规则，防止第三次复发 |
| 6 | A-07 / A-09 / A-11 / A-12~A-18 | 例行/立项链路 |

---

*报告范围：架构与目录结构、性能隐患、依赖版本与许可、部署与配置安全。测试覆盖率、代码级安全漏洞（注入/越权/XSS 等）由 engineer / QA 维度出具，本报告不重复结论。*
*本报告为本次审计唯一写入的文件；所有命令输出均为 2026-11-02 在本机 Git Bash + `apps/adapter/.venv`（Python 3.13.14）实跑结果。*
