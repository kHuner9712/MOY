# 麦子（MOY）V1.0 全仓库审计报告

- **审计日期**：2026-10-28
- **执行方式**：`software-officeai-audit` 团队只读静态审计（主理人齐活林编排；架构师高见远、工程师寇豆码、QA 严过关独立产出，主理人交叉复核汇编）
- **审计范围**：`apps/adapter`（FastAPI 适配层）、`apps/web`（Next.js 16）、`deploy/`、`.github/workflows/`、`docs/`、`sql/`、根配置
- **方法与局限**：全程只读（代码审读 / grep / git ls-files / `pytest --collect-only` 实跑收集），未安装依赖、未实跑全量测试套件；无法静态验证处均标注「待验证」
- **去重说明**：三份成员报告共 32 条原始发现，交叉复核合并同根因 3 组（ARC-05=ENG-02、ARC-11=ENG-01、ARC-09 并入 ARC-08），净 **29 条独立问题：高 4 / 中 11 / 低 14**

---

## TL;DR

仓库工程化水平显著高于同类项目：规范约定（错误码注册制、统一响应、鉴权/幂等/ULID、前端技术黑盒化）均有「CI 门禁 + 可红自检」双重落地，抽查端点链路逐条兑现；无 SQL 注入 / 命令注入 / XSS 注入点，未复刻 Dify 生态典型漏洞模式。**当前最大的系统性风险不在代码本身，而在「可复现性链条」与「验证体系」两处**：Python 依赖无任何锁文件、web 镜像构建静默丢失 package-lock.json、GitHub Actions 额度耗尽导致 207 条 PG 红线用例（含全部租户隔离用例）本地全量 SKIP 且 CI 本月未实跑——整条最高价值测试线处于停摆状态。另有唯一一处代码级高危：M1/M2 每请求新建 Dify 客户端且流式中断不关闭，存在连接泄漏。

---

## 一、问题总览

| 维度 | 高 | 中 | 低 | 小计 |
|---|---|---|---|---|
| 架构与目录结构 | 0 | 3 | 3 | 6 |
| 代码质量与规范一致性 | 0 | 1 | 3 | 4 |
| 安全漏洞 | 1 | 1 | 3 | 5 |
| 性能隐患 | 1 | 1 | 3 | 5 |
| 依赖版本与许可 | 2 | 2 | 1 | 5 |
| 测试覆盖情况 | 0 | 2 | 1 | 3 |
| **合计** | **4** | **11** | **14** | **29** |

> 归属说明：安全维度含 ARC-01（网络暴露面）；依赖维度含 QA-05（npm 无漏洞扫描）、ENG-01（Python 无锁，兼涉测试可信度）；测试维度含 QA-01 的流程面（CI 停摆）。

---

## 二、高危问题（4 条，建议立即处理）

### H-1 Dify 客户端生命周期缺陷：每请求新建连接池且流式中断不关闭（原 ARC-08 + ARC-09）

- **证据**：`apps/adapter/app/services/model_service.py:536-559、648、1250`（generate / generate_stream 每次调用 `DifyClient.for_app_key()`）；`apps/adapter/app/clients/dify_client.py:88-92`（每次实例化自建 `httpx.AsyncClient`）、`:425-452`（`post_stream` 无 try/finally，GeneratorExit 跳过 `response.aclose()`）
- **影响**：每个 M1/M2 请求完整 TCP 建连（延迟 +50-100ms）并对底座形成连接风暴；用户中断流式对话时响应永不关闭，socket/fd 随请求量累积泄漏，两路径叠加加速泄漏
- **建议**：ModelService 按 app_key 维护客户端缓存（或在 lifespan 统一持有、关闭时逐个 aclose）；`post_stream` 的 `async for ... yield` 包进 `try/finally: await response.aclose()`。改动面小（两个文件），收益明确

### H-2 Python 依赖无任何锁文件，8 项依赖无上界浮动（原 ENG-01 + ARC-11）

- **证据**：`apps/adapter/pyproject.toml:22-47`（uvicorn>=0.30、redis>=5.0、asyncpg>=0.29、tenacity>=8.2、structlog>=24.1、slowapi>=0.1.9、python-multipart>=0.0.9、pyyaml>=6.0 共 8 项无上界）；仓库无 uv.lock / requirements 锁定；CI 每次 `pip install -e .[dev]` 现场解析（ci-adapter.yml:106-109）
- **影响**：同一 pyproject 不同时间解析出不同版本，测试与生产不可复现；违反 2026-10-21「精确标签」版本策略；starlette multipart DoS 防线（`starlette>=1.3.1`，PYSEC-2026-1941 直击 R3 上传接口）只靠版本约束字符串，现场安装可能落入含洞版本
- **建议**：pyproject 已配置 `[tool.uv]`，补 `uv.lock` 入库，CI 改 `uv sync --frozen`；全部依赖收敛为 `==精确版本`

### H-3 web 镜像构建上下文丢失 package-lock.json，静默回退无锁 npm install（原 ENG-02 + ARC-05）

- **证据**：`apps/web/Dockerfile:24-25`（`COPY package.json package-lock.json* ./` 在 context 内匹配不到 lock）；`deploy/docker-compose.yml:516-517`（build context 为 `../apps/web`，而 lock 文件在仓库根）
- **影响**：`npm ci` 分支为死代码，镜像内依赖与锁文件漂移；CI 门禁验证过的依赖组合 ≠ 线上实际运行的组合，不可复现
- **建议**：构建 context 改为仓库根（dockerfile 指向 apps/web/Dockerfile），或把根 lock 文件复制进 context；删除 npm install 回退分支，lock 缺失时直接 fail

### H-4 CI 本月未实跑，207 条 PG 红线用例本地全量 SKIP，测试红线停摆（QA-01）

- **证据**：pytest_run.log 尾部 `1867 passed, 203 skipped`；SKIP 明细含 `tests/integration/test_tenant_isolation.py`（15 条）、`test_conversation_rls_pg.py`（10 条）、`tests/security/test_attack_cross_tenant.py` 等全部真库用例
- **影响**：租户隔离 / RLS / artifact / billing 红线回归完全依赖历史记录，任何近期改动破坏隔离都不会被发现；2343 条用例与 20+ 门禁的当前真实状态未知
- **建议**：恢复 CI 实跑（或自建 self-hosted runner 绕开额度限制），立即补跑一次带 `--run-pg` 的全量门禁，以 junit.xml 重建唯一权威数字

---

## 三、中危问题（11 条）

| 编号 | 维度 | 问题 | 证据（文件:行） | 建议 |
|---|---|---|---|---|
| ARC-01 | 架构/安全 | 全量 compose 单一扁平网络 + milvus/object-store/prometheus 端口映射到宿主机，与 router.py「compose 仅内网暴露」口径不符 | docker-compose.yml:183-185、210-212、588-589、604-606；router.py:119-127 | 拆 frontend/backend 双网络（仅 adapter 跨网）；移除或 127.0.0.1 绑定宿主机端口 |
| ARC-02 | 架构 | 全量 compose 无任何资源限制（lite 版却有完整 memory limits） | docker-compose.yml 全文 vs docker-compose.lite.yml:106-110、133-137 | 将 lite 版 limits 口径移植全量版（至少 postgres/valkey/milvus/dify-api/adapter） |
| ARC-03 | 架构 | dify-api/worker/beat、sandbox、plugin_daemon、ssrf_proxy、keycloak、prometheus 等均无 healthcheck；adapter depends_on 未覆盖 postgres/milvus/object-store | docker-compose.yml:231-341、356-374、388-441、503-508 | 补齐 healthcheck；adapter 加 `postgres: condition: service_healthy` |
| ARC-10 | 性能 | R3 上传把整个文件一次性读进内存（上限 50MB+1），无流式落盘/直传 | rag.py:163-172；config.py:357 | 分块写临时文件或直传 SeaweedFS；超大文件走预签名 URL |
| ENG-03 | 依赖 | SCA 协议黑名单正则词界盲区：`\b(GPL|...)\b` 匹配不到 GPLv2/GPLv3/AGPLv3 写法，GPL 依赖会漏检放行 | dependency-scan.yml:239-241 | 补 `\w*GPL\w*` 类模式并加负样本自检（GPLv3 / AGPL-3.0-only / 全称） |
| ENG-04 | 依赖 | 应用镜像基础镜像浮动标签（python:3.13-slim、node:22-alpine），未纳入 images.lock 同等校验（infra 镜像已锁定） | adapter/Dockerfile:17；web/Dockerfile:12,20；对照 deploy/images.lock | 应用镜像 digest 锁定或精确小版本标签（python:3.13.12-slim），纳入 validate_images |
| ENG-05 | 依赖 | 版本口径漂移：eslint ^10.13.0（基线「ESLint 9」）；mypy python_version=3.11 / ruff py311 与运行时 3.13 口径不一 | web/package.json:66；pyproject.toml:10,85,137 | 与基线文档对齐，或在代码处显式登记偏离理由 |
| QA-02 | 安全 | 本地 `deploy/.env*` 含疑似真实凭据明文：DeepSeek key、Dify app key ×2、CREDENTIAL_ENCRYPTION_KEY、全套口令，且有含旧密钥的备份副本（.env.bak.2255、.env.lite.pre-3b2）。已确认未入 git | deploy/.env:91-92,103,184-185；.env.lite:74-77,134 | 轮换全部真实密钥（尤其加密主密钥）；删除 .bak/.pre 副本；改 docker secrets 或 sops |
| QA-03 | 测试 | 宣称指标无法对账：「417 用例 / 91.28%」与仓库工件矛盾（collect 实测 2343；coverage.xml 实际 86.35%、分支 73.84%，且类结构对应已不存在的旧代码——陈旧产物） | coverage.xml 头部；coverage.xml class 清单 vs 现行 repositories 目录 | 以 CI junit.xml 为唯一权威口径；删除 / gitignore 陈旧 coverage.xml、pytest_run.log |
| QA-04 | 测试 | 前端测试薄弱：vitest 仅 55 条纯函数级、e2e 仅 2 条冒烟；OIDC 兑换/工作台/插件安装无浏览器级覆盖；ci-web 无 vitest 覆盖率门禁 | e2e/ 仅 brand.spec.ts、session-redirect.spec.ts；ci-web.yml:86-87 | 优先补「登录兑换→工作台→插件安装」贯穿性 e2e；加 vitest --coverage 门禁（即遗留 C-3） |
| QA-05 | 依赖 | npm 侧无任何漏洞扫描（Python 有 pip-audit，npm 只有协议扫描） | dependency-scan.yml 全文；ci-web.yml 无 audit 步骤 | dependency-scan.yml 增加 `npm audit --audit-level=high` 或 osv-scanner |

---

## 四、低危问题（14 条）

| 编号 | 维度 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| ARC-04 | 架构 | 无人引用的孤儿 `etcd` 服务（Milvus 实际用 milvus-etcd），白占容器与卷 | docker-compose.yml:105-120 | 删除该服务与对应卷 |
| ARC-06 | 架构 | 25 个页面/布局全部首行 'use client'，整站客户端渲染 | src/app 下 25 个文件 | 渐进式改造：静态壳改 Server Components |
| ARC-07 | 架构 | tenacity 声明为依赖但全仓库零引用（重试为手写实现） | pyproject.toml:34 | 移除依赖或在文档标注口径 |
| ARC-12 | 性能 | OPS 企业列表无分页全量加载，`ILIKE '%kw%'` 无法走 btree | billing/postgres.py:276-307 | 加 limit/offset + 总数（list_orders :251-267 为正确示范）；模糊搜索上 pg_trgm |
| ARC-13 | 性能 | 幂等/限流后端默认 `memory`（进程内），多副本部署即失效 | config.py:349；.env.example:180 | 默认改 `redis`（Valkey 已在栈内，代码已实现） |
| ARC-14 | 性能 | Valkey `allkeys-lru`：限流窗口/幂等键与 Dify 队列共库共淘汰策略 | docker-compose.yml:89-94、253-255、485 | 业务 key 迁独立 db 或 `volatile-lru`，显式 TTL |
| ENG-06 | 质量 | eslint `react-hooks/set-state-in-effect` 整体关闭，豁免未闭环 | eslint.config.mjs:28-31 | 豁免加到期判据 / 回归 ticket |
| ENG-07 | 质量 | 错误码实际注册范围（11007、14005-14008、15001-15009、16002-16012、17001-17002、18001-18003）超出审计基准分段表（受控扩展、有三处一致门禁背书，但文档不同步） | core/errors.py:29-167 | 更新架构文档 §3.0.3 分段表 |
| ENG-08 | 依赖 | pyproject「全部 MIT/Apache/BSD」声明无逐项对账留档；python-jose 上游维护活跃度偏弱（待验证） | pyproject.toml:13-14 | license-scan 报告 artifact 留档对账；评估迁移 pyjwt |
| QA-06 | 安全 | 开发环境 CORS `allow_origins=["*"]` + `allow_credentials=True` | app/main.py:381-387 | dev 也改显式白名单（localhost:3000） |
| QA-07 | 安全 | 全局安全降级开关 `tenant_membership_check=off` 仅 warning 日志，忘改回即持续 IDOR 窗口 | security.py:511-533 | off 档加启动横幅告警 + 最长生效时长 |
| QA-08 | 安全 | 会话令牌存 localStorage（V1.0 已知权衡，注释自认） | src/lib/session-store.ts | 后续版本迁移 httpOnly cookie 或短期 token + 刷新 |
| QA-09 | 测试 | 覆盖率门禁口径文档漂移：pyproject 注释写 ≥80%，CI 实际硬门禁 ≥85% | pyproject 注释 vs ci-adapter.yml:435-463 | 更新 pyproject 注释对齐 85% |
| QA-10 | 测试 | 覆盖薄弱模块（陈旧 coverage.xml 口径）：repositories/base.py 0.51、task_repo 0.52、artifact_token 0.54、credential_service 0.72（加密路径） | coverage.xml 各 class | credential_service 与 artifact_token 补分支级单测 |

---

## 五、各维度小结

**① 架构与目录结构**：适配层分层（api/business/db/clients/plugins/registry/workers）职责清晰，「底座调用唯一出口」（clients/dify_client.py）有门禁背书，30 个核心端点与架构文档第 3 章契约抽查全部一致；构建产物零入库（git ls-files 核实 706 个跟踪文件全部干净）。短板集中在 deploy 全量 compose 的拓扑质量（网络/资源/健康检查），且与 lite 版质量不对等——lite 已是可直接照抄的样板。

**② 代码质量与规范一致性**：错误码注册制（唯一登记入口 + G-13 三处一致门禁）、统一响应与业务文案重写（internal 只进日志）、鉴权/幂等/ULID、OA_ 前缀、trace 中间件逐条兑现；`type: ignore`（18 处）/`# noqa`（11 处）全部带理由，前端 0 处 `any`，check-banned-terms.mjs（1111 行）词表带漂移校验与自检，实测 src/ 零术语泄漏。规范层面未发现实质违规，仅存在文档同步类小问题。

**③ 安全漏洞**：自研适配层未复刻 Dify 生态典型漏洞模式——无 SQL 拼接、无 shell=True、无 XSS 注入点，租户伪造有成员校验 + `__platform__` 预拒 + 白名单 lock 快照防线，SSRF 当前无用户可控 URL 暴露面（但「暂无暴露面」≠「有防护」，未来自定义 webhook 功能上线前必须先建内网黑名单）。主要风险为本地明文密钥卫生（QA-02）与网络暴露面（ARC-01）。

**④ 性能隐患**：核心缺陷是 H-1 客户端生命周期（每请求建连 + 流式不关闭）；其余为上传全量进内存（ARC-10）与多副本演进方向的配置默认值问题（ARC-13/14）。正面项：KeycloakClient 进程单例复用、超时/重试齐备、热路径无阻塞调用、39 个索引与查询模式匹配、Redis key 均带 TTL、zip 导出已用 asyncio.to_thread。

**⑤ 依赖版本与许可**：两条高危均属「锁定链条断裂」（H-2/H-3）；SCA 门禁存在 GPL 写法漏检缺口（ENG-03）；应用镜像未纳入 images.lock 同等治理（ENG-04）。正面项：infra 镜像已精确锁定并带远程存在性校验、CI 覆盖率门禁实际 ≥85% 严于基线 80%、仓库卫生全部合格。

**⑥ 测试覆盖情况**：适配层测试体系质量高（2343 条用例、8 个安全攻击测试文件、20+ 自研门禁多数带「可红证明」自检，抽样断言为行为级而非 happy-path）；但当前处于「纸面绿」状态——CI 停摆 + PG 用例全 SKIP（H-4）+ 权威数字缺失（QA-03）。前端是明显短板（QA-04，与遗留 C-3 互证）。

---

## 六、改进建议清单（按优先级）

### P0 · 立即（本周内）
1. **修复 DifyClient 生命周期**（H-1 = ARC-08+09）：按 app_key 缓存客户端 + post_stream try/finally。改动面最小、收益最直接的代码级修复。
2. **补齐 Python 依赖锁定**（H-2 = ENG-01+11）：uv.lock 入库、CI 改 `uv sync --frozen`、依赖收敛精确版本。pyproject 已有 [tool.uv]，成本极低。
3. **修复 web 镜像构建**（H-3 = ENG-02/05）：build context 提到仓库根，恢复 npm ci，删 npm install 回退分支。
4. **恢复测试红线**（H-4 = QA-01）：self-hosted runner 或等额度恢复，跑一次 `pytest --run-pg --cov-fail-under=85`，以 junit.xml 重建权威数字。
5. **凭据治理专项**（QA-02）：轮换 .env 全部真实密钥、删除 .bak/.pre 副本、评估 sops/docker secrets。

### P1 · 本 Sprint 内
6. **compose 对齐 lite 基线**（ARC-01/02/03）：资源限制、healthcheck、前后台双网络、宿主机端口收敛。
7. **封堵扫描缺口**（QA-05 + ENG-03）：npm audit/osv-scanner 入 dependency-scan；SCA 正则补 GPL 变体 + 负样本自检。
8. **上传流式化**（ARC-10）：分块落盘或直传 SeaweedFS。
9. **前端防线**（QA-04）：一条贯穿性 e2e 进 ci-web + vitest 覆盖率门禁（一并关闭遗留 C-3）。
10. **清理陈旧工件**（QA-03）：删除/忽略 coverage.xml、pytest_run.log，确立「CI 产物为唯一口径」。

### P2 · 排期处理
11. 版本口径统一：应用镜像精确标签入 images.lock（ENG-04）；ESLint/Python 口径与基线文档对齐（ENG-05、QA-09）；pyproject 注释更新（QA-09）。
12. 部署默认值演进：幂等/限流后端默认 redis（ARC-13）；Valkey 淘汰策略隔离（ARC-14）。
13. 安全加固：dev CORS 白名单（QA-06）；`tenant_membership_check=off` 加告警与时效（QA-07）；token 存储迁移 httpOnly cookie（QA-08，下版本）。
14. 代码与文档卫生：删孤儿 etcd（ARC-04）；移除 tenacity 或标注（ARC-07）；OPS 列表分页 + pg_trgm（ARC-12）；错误码分段表更新（ENG-07）；eslint 豁免闭环（ENG-06）；license 对账留档 + python-jose 维护度评估（ENG-08）；RSC 渐进改造（ARC-06）；credential_service/artifact_token 补分支单测（QA-10）。

---

## 七、审计覆盖说明

- 本报告基于静态审计，测试套件未实跑（仅 collect-only 收集 2343 条）；「待验证」项：ENG-08（python-jose 维护活跃度）、QA-10 数字口径（依赖陈旧 coverage.xml，仅供趋势参考）。
- 与既有遗留问题的关系：QA-04 即遗留 C-3（前端浏览器级走查）；H-2 与 C-1（模型提供方密钥）无直接关联但同属外部依赖可用性问题族。
- 建议下一次审计在 P0 项全部落地 + CI 恢复实跑后进行，以验证修复有效性与权威指标重建情况。
