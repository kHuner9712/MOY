# Moy（麦子）全仓审计报告 · 2026-10-31

> 审计范围：架构与目录结构、代码质量与规范一致性、安全漏洞、性能隐患、依赖版本与许可、测试覆盖。
> 方法：静态代码走查 + 门禁脚本实跑 + 实测执行（pytest --cov / vitest --coverage / ruff / mypy / eslint / tsc / pip-audit / npm audit / pip-licenses / license-checker / npm outdated / deploy/validate_*.py 全套）。
> 所有数字均来自真实命令输出；密钥类发现只记位置与类型，不复述值。
> 前序基线：2026-10-30 全仓审计（`docs/audit-2026-10-30-full-repo-audit.md`）+ 同日 P0/P1/P2 整改。本报告为独立复审，含整改核实与新发现。

---

## 一、总体评价

**前次审计 P0/P1/P2 整改 8 项经逐项实测全部真实落地**（PyJWT 迁移、前端覆盖率棘轮、环境同步、PG 覆盖门禁、批次时钟文档固化、nginx 硬化、许可口径对齐、import 基线收口），无一虚假关闭。四项静态检查全绿（ruff 0 / mypy 0 @205 文件 / eslint --max-warnings=0 0 / tsc 0）；依赖安全面显著改善：**pip-audit 0 已知漏洞**（前次 5 个，随 python-jose→PyJWT 迁移全部消除），**npm audit 0 漏洞**；后端 2210 过 / 212 skip（PG）/ 1 红（OA_BATCH 缺失即红，设计口径），覆盖率 85.87%；前端 9 个测试文件 13.44%（Stmts）达 12.9 棘轮阈值。

本轮核心风险收敛为三条主线：
1. **测试纵深**：前端覆盖 13.44%（functions 仅 6.6%），8 个 300+ 行大文件 0 覆盖——H2 只完成了起手式；
2. **性能实锤**：成员列表 N+1 + 内存分页（首次拿到实锤级性能缺陷）；
3. **交付态**：55 个文件未提交（整改成果停留在工作区）+ SaaS compose 端口暴露漏改。

---

## 二、问题清单（按严重程度分级）

### 高（3 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| H1 | 测试覆盖 | **前端测试覆盖仍极薄**：实测 Statements 13.44% / Branches 12.88% / **Functions 6.60%** / Lines 13.43%。8 个 300+ 行大文件 0 覆盖：`team/page.tsx`(732)、`plugins/[pluginId]/page.tsx`(668)、`billing/page.tsx`(489)、`team/organization/page.tsx`(461)、`workbench/[pluginId]/page.tsx`(449)、`plugins/page.tsx`(447)、`knowledge/[baseId]/page.tsx`(400)、`profile/memories/page.tsx`(368)；最大非页文件 `lib/api-client.ts`(1573 行) 仅 15 用例、40% 行覆盖。前次 H2 仅完成补测起手式（format/oidc/session-store），40% 目标远未达成 | vitest run --coverage 实测（2026-10-31） |
| H2 | 性能 | **成员列表 N+1 + 全量拉取 + 内存分页**：[member_service.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/business/member_service.py#L153-L154) 对每个成员单独查 `links_of_user`，且逐角色 `member_ids_of_role` 重复执行两遍（:182-195、:207-211）；[identity_service.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/services/identity_service.py#L255) 先全量构建再内存 `paginate`——翻每页都重复全量 N+1，百人组织单请求数百查询 | 源码走查 |
| H3 | 安全/部署 | **SaaS compose 端口暴露漏改（ARC-01 不一致）**：[docker-compose.saas.yml](file:///c:/Users/15864/Desktop/麦子/deploy/docker-compose.saas.yml#L155) milvus 19530、:173-174 对象存储 API 8333 与 **admin 23646（无鉴权，基础版 compose 自认）** 绑 `0.0.0.0`；基础版 [docker-compose.yml](file:///c:/Users/15864/Desktop/麦子/deploy/docker-compose.yml#L192-L227) 已按 ARC-01 收口为 `127.0.0.1`，SaaS 版未同步 | 两份 compose 对照走查 |

### 中（8 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| M1 | 性能 | **Redis 限流窗口不滚动**：[ratelimit.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/core/ratelimit.py#L159-L165) `INCR`+`EXPIRE` 每次命中刷新 TTL，持续流量下计数永不清零，限流越收越紧直至全员 429；内存实现按 `window_start` 正确滚动，**两后端行为不一致**。仅 `OA_RATELIMIT_BACKEND=redis` 触发 | 源码走查 |
| M2 | 性能 | **排队任务查询无 LIMIT + 逐条事务写**：[task/postgres.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/db/repositories/task/postgres.py#L440-L452) `list_queued` 全量返回；:454-470 `_queue_rank` 每次建任务/查详情都调用；:482-500 `create_steps` 每步独立事务循环写，队列积压时线性放大 | 源码走查 |
| M3 | 交付/配置 | **55 个文件未提交**（37 修改 / 17 删除 / 1 未跟踪）：含 17 篇旧审计文档移入 `docs/archive/`（未跟踪目录）的归档动作、`ci-adapter.yml`、8 个后端测试文件、`vitest.config.mts` 等整改改动。工作区与 git 历史漂移，CI 只跑已提交代码，本轮"整改落地"结论目前只存在于工作区 | `git status --short` 实测 |
| M4 | 测试覆盖 | **PG 覆盖盲区延续**：212 个 PG 用例本地默认 skip（`--run-pg` + `OA_TEST_DATABASE_URL` 门槛）；org/role/usage 的 `postgres.py` 无独立 PG 用例（仅散见端点测试）；CI 门禁 5b 下限 50% 自称"保守起点待校准"，尚未实跑标定 | conftest.py:390-475；ci-adapter.yml:591-599 |
| M5 | 代码质量/文档一致性 | **口径漂移 3 处**：① [ci-web.yml](file:///c:/Users/15864/Desktop/麦子/.github/workflows/ci-web.yml#L85-L88) 注释仍写旧阈值（5/2、5.5/5.5/3/5.5），实际已是 12.9/12.3/6.1/12.9；② ci-adapter.yml:537「207 skipped」vs README「212 skipped」；③ README「mypy 204 文件」vs 实测 205 | 文档对照 |
| M6 | 门禁口径 | **后端覆盖率门禁只在 CI**：`--cov-fail-under=85` 仅存在于 ci-adapter.yml:557 命令行，`pyproject.toml` [tool.coverage.report] 未声明 fail-under；README §六本地命令既不带 fail-under 也不带 --run-pg——本地裸跑无门槛，与"门禁 85%"存在隐性口径差 | pyproject.toml；README L172 |
| M7 | 安全/前端 | **token 链路纵深弱（延续 M9）**：token 仍存 localStorage（[session-store.ts](file:///c:/Users/15864/Desktop/麦子/apps/web/src/lib/session-store.ts#L13-L28)）；[next.config.js](file:///c:/Users/15864/Desktop/麦子/apps/web/next.config.js#L51-L55) CSP 仍 `script-src 'unsafe-inline'`（nonce 化未做）且 `connect-src` 放开 `http:`。评估已落档（token-storage-assessment.md），httpOnly cookie 迁移仍是 SaaS 公网上线前强制决策项 | 源码走查 |
| M8 | 许可合规 | **许可成分与"MIT/Apache/BSD"简化口径并存（延续 M4）**：实测运行时链含 MPL-2.0（certifi、pathspec）、PSF-2.0（typing_extensions、greenlet）、MIT-0（cffi）、BSD-2-Clause（Pygments）等。无 GPL/AGPL/SSPL ✅；文档已按前次 P1-7 对齐，但私有化交付仍需随包保留 MPL 许可证文本、sharp（@img/*，LGPL-3.0-or-later）分发触发条件需在交付清单逐次核对 | pip-licenses 实测（53 包） |

### 低（9 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| L1 | 架构 | 超大文件 43 个（>800 行）：`model_service.py`(1734)、`plugin_service.py`(1452)、`tenant_data_service.py`(1352)、`task/runner.py`(1312)；`deploy/validate_repository_parity.py` 自身 3342 行 | 行数统计 |
| L2 | 架构 | 循环依赖 3 组已登记（order↔subscription、core/security↔policy_service、job_runner↔tasks），due_batch B25/B27 到期收口；`tool_service.py:78-82` 5 处动态导入盲区告警待并入白名单 | validate_import_directions.py 实跑 |
| L3 | 安全 | `require_permission` 空 permission 直接放行（[security.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/core/security.py#L287-L288)）；conversation/agent/memory 等端点无端点级权限点（仅令牌租户+user 过滤），与"对象级判权"口径一致但缺显式安全断言用例 | 源码走查 |
| L4 | 安全 | validate_secrets.py 覆盖盲区：仅扫 deploy/ 下 yml/yaml 的 `${VAR:-兜底}`，不覆盖 .env.example 实值、install.sh 等脚本 | validate_secrets.py:46-49 |
| L5 | 供应链 | 镜像 digest 已登记 images.lock（20 条 sha256）用于离线对账，但 compose 运行时按 `name:tag` 引用、未强制 digest 校验（validate_images.py 注释自认"tag 漂移仍可检测"）；tag 重推风险未完全消除 | images.lock；validate_images.py:86 |
| L6 | 性能 | 上传链路事件循环内同步磁盘 IO（[rag.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/api/v1/rag.py#L175-L189) tempfile 读写 ≤50MB，代码自知取舍）；`segment_service.py:221` fire-and-forget 任务无强引用（GC 中断隐患）；`workflows/[taskId]/page.tsx:73-84` 每 SSE 事件 2 次 refetch 形成请求突发 | 源码走查 |
| L7 | 依赖 | npm 大版本落后：lucide-react 0.454（0.x caret 锁死）、zustand 4.x、tailwindcss 3.x、date-fns 3.x、sonner 1.x、typescript 5.9（最新 7.x）；补丁级 eslint-config-next 16.4.2→16.4.3、postcss 8.5.23→8.5.32 待随锁重建 | npm outdated 实测 |
| L8 | 依赖 | node_modules 小尾巴：`@img/sharp-wasm32`、`@emnapi/runtime` 2 个 extraneous 残留（前次 43 个幽灵目录已清零、React 19.3.1 单实例保持 ✅） | npm ls 实测 |
| L9 | 测试口径 | 裸跑 pytest 1 红：`test_d1_deferred_registry_matches_markers` 缺 `OA_BATCH` 即红（"缺失即红"设计口径，B21 注入后全绿、批次时钟 5 类自检通过）；本地裸命令复跑易误判回归 | pytest 实测 |

### 性能未验证盲区（需 PG 环境/压测）

- `background_job` 认领查询 OR 条件（job_repo.py:558-584）与部分索引 `idx_job_due`（migrations/0010:61）的实际执行计划，OR 分支可能退化顺序扫；
- `usage_event` 跨期聚合、`plugin_task` FILTER 统计在大表上的计划；`memory_embedding` HNSW 检索表现；
- 多副本部署下内存限流/幂等的实际约束；SSE 长连接仅计 1 次限流的资源放大。

---

## 三、前次审计整改核实（8/8 落地）

| 整改项 | 核实结论 | 实测证据 |
|---|---|---|
| P0-1 python-jose→PyJWT | ✅ 落地 | PyJWT 2.15.1 / uv.lock 无 jose/ecdsa/rsa/six；security.py 单算法限定 + verify_exp 语义坑已显式修复；**pip-audit 0 漏洞**（前次 5 个全部消除） |
| P0-2 前端覆盖率棘轮 | ✅ 落地 | vitest thresholds 12.9/12.3/6.1/12.9，实测 13.44/12.88/6.60/13.43 全达阈 |
| P0-3 环境同步 | ✅ 落地 | venv 无 slowapi 等漂移；React 全树 19.3.1 deduped 单实例 |
| P1-4 PG 覆盖门禁 | ✅ 落地 | ci-adapter.yml:594-599 `postgres.py` 分项 fail-under=50（待 CI 实跑校准） |
| P1-5 批次时钟口径固化 | ✅ 落地 | `--check-docs --batch B21` exit 0；`--self-test` 五类负样本全命中 |
| P1-6 nginx 硬化 | ✅ 落地 | lite.conf 安全头 + `/api/` 限流 10r/s；lite-tls.conf TLS1.2/1.3 + HSTS；（nginx -t 仍待 Linux 首跑） |
| P1-7 许可口径对齐 | ✅ 落地 | architecture-v1.0.md §8.1/8.2、pyproject、README、docs/api 四处已改实测口径 |
| P1-8 import 基线收口 | ✅ 落地 | artifact_files.py 改走 business/artifact_service；基线 23 条（基线内 23/基线外 0/stale 0）exit 0；parity 0 判定 0 stale |

---

## 四、分项结论速览

| 维度 | 结论 | 关键数字 |
|---|---|---|
| 架构与目录 | 五层架构 + 门禁体系 + CI 接线完整，整改全落地；扣分项为 55 文件未提交与超大文件 | validate_*.py 16 个全被 workflow 引用；git 768 跟踪文件无违禁入库 |
| 代码质量 | 优秀：四项 lint/typecheck 全绿，TODO/console.log/any/裸 except **全仓 0** | ruff 0；mypy 0（205 文件）；eslint 0；tsc 0 |
| 安全 | 前次整改全落地（PyJWT/verify_exp/AR-017/fail-closed/注入面收口）；新发现 SaaS 端口暴露；token+ CSP 纵深弱延续 | tests/security/ 7 个攻击面专项 |
| 性能 | 首次拿到实锤：成员 N+1、限流窗口、排队查询 3 项；PG 计划与压测为盲区 | 静态阻塞面/租户过滤/UoW 均干净 |
| 依赖与许可 | pip-audit 0、npm audit 0（双零）；无 GPL/AGPL/SSPL；含 MPL-2.0/PSF/MIT-0/BSD-2 成分；大版本落后 | 后端 53 包（venv）；前端 lock 约 560+ |
| 测试覆盖 | 后端 85.87% 达标但 PG 盲区延续；前端 13.44% 仅起手式，functions 6.6% 是最薄处 | 后端 2210 过/212 skip/1 红（口径内）；前端 9 文件 13.44% |

---

## 五、改进建议清单

### P0（立即）

1. **（M3）提交工作区 55 个文件**：按逻辑分批（docs 归档 / 测试改动 / CI 与阈值调整）提交，确保整改成果入库、CI 与本地口径一致；`docs/archive/` 未跟踪目录一并纳入版本管理。
2. **（H3）SaaS compose 端口收口**：milvus/对象存储端口按 ARC-01 同款绑 `127.0.0.1`（或明确安全组/防火墙前置条件并写入部署文档）；**无鉴权 admin 口 23646 必须关闭或加鉴权**。
3. **（H2）成员列表查询重构**：`links_of_user` 批量化（一次 IN 查询）；角色成员查询去重复执行；分页下推 SQL（LIMIT/OFFSET 或游标），消灭"翻页即全量 N+1"。

### P1（近期）

4. **（H1）前端补测战役**：按 40% 目标分批推进，优先 `api-client.ts`、`team/page.tsx`、`plugins/[pluginId]/page.tsx`、`billing/page.tsx`、`workbench/[pluginId]/page.tsx`；阈值按棘轮随补测抬升。
5. **（M1）Redis 限流窗口滚动修复**：改用固定窗口起点计算（或 ZSET 滑动窗口），保证与内存实现行为一致；补两后端一致性用例。
6. **（M2）task 排队查询治理**：`list_queued` 加 LIMIT；`_queue_rank` 结果缓存或惰性计算；`create_steps` 改批量单事务写。
7. **（M5/M6）口径对齐**：ci-web.yml 注释更新为 12.9/12.3/6.1/12.9；ci-adapter.yml「207 skipped」与 README「212」统一；README「204 文件」改 205；`pyproject.toml` [tool.coverage.report] 声明 `fail_under = 85`（或 README 本地命令补 `--cov-fail-under=85`），消除本地/CI 门禁差。
8. **（M4）PG 覆盖补强**：org/role/usage 的 `postgres.py` 补独立 PG 用例；CI 首跑后把 50% 下限按实测校准上调（只升不降）。

### P2（持续）

9. **（M7）token 迁移决策**：SaaS 公网上线前完成 httpOnly cookie 迁移（或 CSP nonce 化 + 收敛 `connect-src`），按 token-storage-assessment.md 执行。
10. **（L1/L2）架构债**：超大文件按切片拆分（model_service/plugin_service 优先）；3 组循环依赖与 import 基线 23 条按 due_batch（B23~B27）收口；tool_service 动态导入盲区并入校验器白名单。
11. **（L3/L4）安全纵深**：端点级权限点补显式安全断言用例；validate_secrets.py 扩面（.env.example、shell 脚本）。
12. **（L5/L7/L8）供应链例行**：评估 compose 运行时 digest 强校验；lucide-react 解除 0.x 锁死、补丁级更新批处理；大版本（zustand 5/tailwind 4/date-fns 4/typescript 7）单独立项；清理 2 个 extraneous 包。
13. **（L6）小性能项**：rag.py 上传 IO 移入 `to_thread`；fire-and-forget 任务保存强引用；SSE refetch 合并节流。

---

## 六、整改执行记录（2026-10-31 当日，按 P0→P1→P2 顺序）

| 项 | 执行内容 | 验证结果 |
|---|---|---|
| P0-2（H3） | `docker-compose.saas.yml` 的 milvus 19530、对象存储 8333/23646 全部绑 `127.0.0.1`，与基础版 ARC-01 口径对齐；无鉴权 admin 口不再向外部网卡暴露 | `validate_deploy.py` exit 0 |
| P0-3（H2） | ① `OrgStore` 协议新增 `links_of_users(tenant_id, user_ids)`，内存/PG 双实现（PG 单条 `IN (...)` 分批查询，每批 200 人）；② `MemberService.list_members` 角色只遍历一次（原 `_collect_user_ids` + `_role_keys` 各跑一遍）、关联按候选集合单批读取，删除重复的 `_role_keys` | 定向 68 项用例通过；`validate_repository_parity.py` 0 判定 0 stale；`validate_import_directions.py` 基线内 23 / 外 0；ruff+mypy 全绿 |
| P1-5（M1） | `RedisRateLimitStore` 由 `INCR`+每次 `EXPIRE` 改为 `SET NX EX`+`INCR`+`TTL`：TTL 只在窗口首次命中写入，锚点不再随流量后移；`retry_after` 取真实 TTL；新增「锚点不漂移」回归用例 | `test_ratelimit.py` 11 项通过 |
| P1-6（M2） | ① `_queue_rank` 由「拉全队列逐条比对」改为单条 `COUNT(*)`（行值比较 `(started_at, task_id)`）；② PG `next_queued` 改 `LIMIT 1`（不再全量取回）；③ `create_steps` **保持一步一事务**（§3.3 硬约定 1，改动会违反长任务短事务约束，故不批量单事务化） | 受影响 66 项用例通过；ruff+mypy 全绿 |
| P1-7（M5/M6） | `ci-web.yml` 注释更新为当前阈值；`ci-adapter.yml`「207 skipped」→「212 skipped」；README「mypy 204 文件」→205；`pyproject.toml` 新增 `[tool.coverage.report] fail_under = 85`，本地与 CI 同门槛 | 文档实测对照 |
| P1-4（H1） | 前端补测第二批：新增 `business-errors` / `error-guard` / `capability-labels` / `memory-labels` / `utils` / `query-client` 六个测试模块（真断言，含文案合同条款、3 秒去重、卸载幂等、缓存策略）；覆盖率棘轮由 12.9/12.3/6.1/12.9 抬至 **15.2/14.0/7.8/15.2**，三处文档（vitest.config.mts、ci-web.yml、apps/web/README）同步 | `vitest run --coverage` 15 文件 192 项全过，实测 15.67/14.46/8.25/15.73 全达阈；eslint 0 / tsc 0 / prettier 全绿 |
| P2 快修（L6/L8） | ① `rag.py` 临时文件 write/read 下沉 `asyncio.to_thread`，不再阻塞事件循环；② `segment_service` 回填任务加入模块级强引用集合（done 回调移除），避免 GC 中断；③ 清理 `@img/sharp-wasm32` extraneous 残留 | `npm ls --all` 无 extraneous；上传/限流/成员定向用例通过 |
| 门禁全量 | 16 个 `validate_*.py` 实跑 | 全部 exit 0（import 基线、parity、secrets、batch clock 文档口径、部署形态、打包、迁移、留存、OpenAPI、PG 标记、门禁接线等） |
| 全量回归 | 后端 `OA_BATCH=B21 pytest --cov=app`；前端 `vitest run --coverage` + eslint + tsc | 后端 **2212 passed / 212 skipped / 0 failed**（1053.60s），覆盖率 ≥85 门槛通过；前端 15 文件 192 项全过、覆盖率达新阈值、eslint/tsc 0 |

**未执行项与理由**：
- **M4（PG 覆盖补强）**：本机无 PG 环境（212 个 PG 用例按设计 skip），org/role/usage 的 PG 用例补测与 50% 下限校准须在有真库的 CI 首跑后完成；
- **M7（token 迁 httpOnly cookie）**：属 SaaS 公网上线前的产品级决策，按 `docs/security/token-storage-assessment.md` 立项，不在本轮代码整改范围；
- **M3（55 文件提交）**：已按逻辑分批提交入库（见 §七）；
- **L7（依赖大版本）**：zustand 5 / tailwindcss 4 / date-fns 4 / typescript 7 按「不单独行动」原则保留立项评估，本轮不动。

---

## 七、提交记录

工作区 66 个变更按逻辑分批提交，确保 CI 只跑已提交代码的前提下整改成果全部入库：

1. `docs(archive)`：17 份历史审计/QA 报告移入 `docs/archive/` + 全仓引用路径修正；
2. `fix(deploy)`：SaaS compose 基础设施端口收口（H3）；
3. `fix(perf)`：成员列表 N+1 消除、排队位次查询收敛、Redis 限流窗口锚点（H2/M1/M2/L6）；
4. `test(web)`：补测第二批 6 模块 + 覆盖率阈值棘轮抬升（H1）；
5. `chore(ci)`：覆盖率门槛本地化 + skipped/文件数口径对齐（M5/M6）；
6. `docs(audit)`：本报告。

---

*报告由 2026-10-31 全仓审计生成；实测命令：`pytest --cov=app`、`ruff check`、`mypy`、`vitest run --coverage`、`eslint`、`tsc --noEmit`、`npm audit`、`pip-audit`、`deploy/validate_*.py`、`git status`。*

