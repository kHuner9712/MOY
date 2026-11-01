# Moy（麦子）V1.0 全仓审计报告 · 2026-11-02（第三轮）

> **审计范围**：架构与目录结构、代码质量与规范一致性、安全漏洞、性能隐患、依赖版本与许可、测试覆盖。
> **审计对象**：`main` 分支 HEAD `e3734a0`，工作区干净（`git status --porcelain` 为空）。
> **方法**：三线并行独立审计 + 主理人抽验复核。三线各自实跑命令、各自落册，本报告只做合并、去重、重新分级与裁决，不改写成员的实测数据。
> **分册**（含完整证据链与逐条行号）：
> - `docs/audit-2026-11-02-arch-perf-deps.md`（架构 / 性能 / 依赖许可 / 部署安全，A-01…A-18）
> - `docs/audit-2026-11-02-quality-security.md`（代码质量 / 安全漏洞，Q-01…Q-12、S-01…S-15）
> - `docs/audit-2026-11-02-test-coverage.md`（测试覆盖 / 有效性 / 门禁实跑，T-01…T-11）
> **前序基线**：`docs/audit-2026-10-30-full-repo-audit.md`、`docs/audit-2026-10-31-full-repo-audit.md`。
> **数据纪律**：所有数字来自 2026-11-02 本机实跑（Git Bash + `apps/adapter/.venv` Python 3.13.14 + 本机 PostgreSQL）；无法实测的一律进 §六 盲区，不臆断。密钥类发现只记位置与类型，不复述值。

---

## 一、总体评价（TL;DR）

**一句话**：架构骨架与安全基本面**经得起复核**，但**交付态是假的**——测试有 12 条红灯、两处门禁在 HEAD 上是红的或裸跑恒红，而 README 仍写着「0 failed / 门禁全绿」。

三个必须立刻知道的结论：

1. **README 的质量数字已经失真**。声称「2211 passed / 212 skipped / 0 failed / 85.87%」，实测为 **2222 passed / 8 failed / 235 skipped / 85.72%**（无 PG）与 **2453 passed / 12 failed / 0 skipped / 92.31%**（含 PG）。8 条红灯不是偶发，是**时间炸弹**——用例硬编码 `2026-11-01T09:00Z`，墙钟越过该时刻后永久红，根因在**源码**（`InMemoryJobStore` 写侧用墙钟、读侧用注入时钟）。
2. **门禁体系出现两处「假绿」**，直接推翻前轮「16 个门禁全 exit 0」的结论：`validate_deps.py` 在 HEAD 上 **真红**（PyJWT 迁移漏改白名单，CI 裸调用必挂），`validate_import_directions.py` **裸跑恒红**（`--baseline` 默认值为 None，23 条已登记违规全被判为"未登记"）。而「门禁接线」门禁本身是绿的——它只校验脚本被引用，校验不了脚本是否为绿。
3. **部署面是最大短板**：基础设施端口已按 ARC-01 收口（这是真的），但**两套编排都没有 TLS 终止**，`adapter:8000`/`web:3000` 仍绑全网卡；`deploy/k8s/` 只有一份自认"manifests 均未创建"的占位 README。SaaS 生产形态在仓库里**没有实体**。

**同时要肯定的**（避免只读问题清单造成误判）：五层分层红线**全部成立**（`dify_client.py` 唯一底座出口、`api-client.ts` 唯一前端出口、`services/` 唯一协议转换）；96 端点与冻结基线 `--strict` 全通过；SQL/命令注入 0 命中；租户隔离三重（`tenant_clause` + RLS 40/41 表 + `assert_tenant_membership`）；凭据 AES-256-GCM + `os.urandom` + 轮转；`pip-audit` 0 漏洞、`npm audit` 0 漏洞、无 GPL/AGPL/SSPL；`TODO/FIXME` 真实技术债标记 **0 处**（债务以 60+ 处文档串显式登记）。**前端覆盖率从上一轮的 13.44% 拉到 86.75%，H1 是本轮闭环最彻底的一项。**

**放行判断**：**不建议按"可发布"放行**。判定 **QA 阻塞（P0×2：12 红 + `validate_deps` 红）+ 上线阻塞（P0×1：无 TLS 终止）**。代码质量本身可放行，问题在"验证链没跑"与"部署形态没成型"。

---

## 二、问题清单（合并去重后统一分级）

> **计数说明**：三线原始输出 **57 条**（A 19 + Q 12 + S 15 + T 11），其中 6 条为正面对照结论（Q-01/02/04/05、S-15、T-07）不计入问题。跨线重复 5 处已合并：A-01≡T-03、A-08≡T-08、Q-03≡A-12、Q-11≡S-09、Q-06≡S-08。**合并后 46 条真问题：高 7 / 中 21 / 低 18。**
> 编号规则：`P0-x` 高、`P1-x` 中、`P2-x` 低；「来源」列指向分册原始编号，可回溯完整证据。

### 2.1 高（7 项）— 阻断交付

| 编号 | 维度 | 问题 | 关键证据 | 来源 |
|---|---|---|---|---|
| **P0-1** | 测试 / 交付态 | **12 条测试红灯，README「0 failed」失真**。8 条为确定性失败且构成时间炸弹：用例硬编码 `2026-11-01T09:00Z`，`runner.tick()` 恒返 0；根因是**源码契约不一致**——`InMemoryJobStore` 内部混用两个时间源。实测墙钟调用点 **4 处**：`:303`（enqueue 的 `created_at`/`updated_at`/`run_after`）、`:355`（claim 的 `updated_at`）、`:368`（mark_succeeded）、`:391`（mark_failed 的 `updated_at`/`finished_at`）。**更严重的是有两个方法在方法内部就混源**：`claim` 的 `locked_until` 走注入时钟（`:352`）而 `updated_at` 走墙钟（`:355`）；`mark_failed` 的 `run_after` 走注入（`:390`）而 `updated_at` 走墙钟（`:391`）。后果：同一条记录上 `updated_at` 与 `run_after`/`locked_until` 相差 ≈7h40m 且随时间放大——只修 `enqueue` 能让 8 条转绿，但会留下错实现，将来任何按 `updated_at` 排序/比较的用例会以"取决于运行时刻"的方式再炸一次。另 4 条为 Windows 下 PG 夹具的 `Event loop is closed`（属另一条问题，不随本次修复消失，勿算回归）。**修复口径见 §四 P0 第 1 项；逐行根因证据见 `docs/audit-2026-11-02-test-coverage.md` T-01** | 实跑：`pytest tests/unit/test_job_runner.py -q` → **8 failed, 7 passed**；全量无 PG → 8 failed / 235 skipped / 85.72%；含 PG → 12 failed / 92.31%。主理人抽验：`sed -n '283,292p' security.py` 与 `ruff format --check` 均复现 | T-01、T-11 |
| **P0-2** | 门禁 / 依赖治理 | **`validate_deps.py` 在 HEAD 实红，CI 一跑必挂**。2026-10-30 的 PyJWT 迁移改了两处真源（`pyproject.toml:48`、`architecture-v1.0.md:1398`），漏改第三处门禁白名单（`validate_deps.py:55-79` 仍留 `python-jose`、无 `pyjwt`）。反证：CI 从未实际运行 | 实跑：`[FAIL] pyjwt：未登记进 §8.1 清单或未加入 DOCUMENTED_PACKAGES`，**EXIT=1**（主理人已独立复现） | A-01 ≡ T-03 |
| **P0-3** | 门禁 / 工具可信度 | **`validate_import_directions.py` 裸跑恒红**：`--baseline` 的 `default=None`，`DEFAULT_BASELINE` 常量定义了却从未被使用 → 23 条**已登记**违规全被报成"未登记"，exit 1。长期"红灯免疫"，真新增违规会被一起忽略。同型：`validate_batch_clock.py` 缺 `OA_BATCH` 即红（属设计口径，但缺一键入口） | 实跑裸跑：`违规 23 条（基线内 0 / 基线外 23）` **EXIT=1**；带 `--baseline deploy/import_direction_baseline.txt` → `基线内 23 / 基线外 0` EXIT=0 | A-08 ≡ T-08 |
| **P0-4** | 部署 / 配置安全 | **无 TLS 终止 + 应用端口全网卡 + k8s 空壳**。nginx 只存在于 lite 版；`docker-compose.yml` 与 `docker-compose.saas.yml` 的 adapter/web 端口均省略 host IP（= 绑全部网卡）、明文对外；`deploy/k8s/` 仅 README（securityContext / NetworkPolicy / resources 全缺）。`next.config.js` 下发的 HSTS 在明文 HTTP 下浏览器不予采纳 | `docker-compose.yml:589,638`；`docker-compose.saas.yml:396,424`；`ls deploy/k8s/` → 仅 `README.md`；三套 compose `grep mem_limit\|resources:` → **0 命中** | A-02、A-17、A-10 |
| **P0-5** | 安全 / 前端链路 | **令牌窃取链三件套齐全，只差一个注入点**：①访问令牌存 `localStorage`；②CSP `script-src 'self' 'unsafe-inline'`（未 nonce 化）；③`connect-src 'self' http: https:` 等于不限制出站目标。三者叠加 → 任意 DOM XSS 即可执行内联脚本、读令牌、外传到任意域。**当前未发现注入点**（`dangerouslySetInnerHTML` 全仓 0 命中），故不是"已被打穿"，而是"一旦有注入点即全线失守"。前轮 M7 未闭环、代码零改动 | `session-store.ts:72`；`next.config.js:51`、`:55` | S-01 |
| **P0-6** | 功能 / 部署 | **产物下载路径在唯一交付编排中未路由，生产必 404**。`/files/artifacts/{token}` 故意不带 `/api/v1` 前缀，但 `lite.conf` / `lite-tls.conf` / `nginx.conf` 只有 `/healthz`、`/api/`、`/readyz`、`/metrics`、`/` 五类 location，**无 `/files/`**；请求落到 `location /` 被转给 Next.js → 404。阶段三"产物下载"在唯一交付形态下**完全不可用**。这是确定性缺陷，不是理论风险 | `deploy/nginx/lite.conf:55,72,106,122,142`；`deploy/` 全域 `grep "/files/"` → **0 命中**；端点定义 `api/v1/artifact_files.py:26` | S-03 |
| **P0-7** | 测试 / 覆盖率口径 | **默认口径「擦线过」，且红线会被口径而非质量触发**：无 PG 余量仅 **+164 单位（+0.72pp）**，含 PG 余量 **+1661 单位（+9.6pp）**，相差 10 倍——差别全在"默认不跑 235 条 PG 用例"。后果：任何人新增 200 单位 PG 代码而未同步用例，默认口径立刻破 85%，诱导团队写低价值用例喂分母。CI 5b 的 `postgres.py` 下限 50% 未经校准（实测 90.66%） | 覆盖率报告 `coverage-audit.json` / `coverage-pg.json`（205 文件）；`ci-adapter.yml:591-599` | T-02 |

### 2.2 中（21 项）

| 编号 | 维度 | 问题 | 关键证据 | 来源 |
|---|---|---|---|---|
| **P1-01** | 性能 / 可用性 | **租户全量导出把整个 zip 常驻内存 + 无并发闸门 + 容器零内存限制**：`BytesIO` 拼整包并返回 `bytes`，上限 200k 行（≈200MB 峰值），无信号量；三套 compose 零 `mem_limit` → 并发导出拖垮的是**宿主**而非本服务 | `tenant_data_service.py:84,688,705`；`grep semaphore` → 0 | A-03 |
| **P1-02** | 性能 | **运营端租户列表仍是「全量拉取 + 内存切页 + 每行 3 查」**——与上一轮已修的成员列表**同型缺陷换入口复发**。`page_size` 上限 200 时单请求 **1+600** 次查询 | `ops_service.py:216,232,233,235,238,240,247`；`api/v1/ops.py:104` | A-04 |
| **P1-03** | 性能 | `list_queued` 仍无 `LIMIT`（前轮 M2 只闭环了 `next_queued` 与 `_queue_rank`）；仓库层 `SELECT *` **23 处 / 6 文件**，索引-only 扫描不可用且表结构变更时静默扩大载荷 | `task/postgres.py:438,455-466` | A-05 |
| **P1-04** | 性能 | **批量写仍是逐条事务**：`add_artifacts` 在循环体内 `tenant_scope`，N 条 = N 次 commit+RTT。`create_steps` 的同类写法**有注释说明属"短事务硬约定"的有意保留**，不计缺陷 | `artifact_repo.py:372,385,388` | A-06 |
| **P1-05** | 供应链 / 依赖 | **基础设施镜像"补丁级浮动" + compose 不校验 digest**：`pgvector:0.8.7-pg18-bookworm`（只锁到 PG 主版本）、`valkey:9.2-alpine`、`nginx:1.28-alpine`、`ubuntu/squid:6.13-25.04_beta`（**唯一 beta 通道**）；`images.lock` 有 20 条 sha256 但运行时按 tag 引用。自研镜像 Dockerfile 已锁 digest（这点做对了） | `docker-compose.yml:42,93,415`；`validate_images.py` 只校验 tag 一致 | A-07 |
| **P1-06** | 前端架构 / 性能 | `'use client'` 覆盖 **81/93 非测试 tsx（87%）**，RSC/SSR 收益基本放弃；**零列表虚拟化**；三个页面 3s/5s 轮询，多标签页叠加成稳态压力 | `grep "'use client'"` → 81；`grep virtual` → 0；`knowledge/[baseId]/page.tsx:30` 等 | A-09 |
| **P1-07** | 技术黑盒化（产品红线） | **about 页面向终端用户直接讲明技术选型**：`OPEN_SOURCE_COMPONENTS` 把 `Dify` 标为「工作流底座」、`pgvector` 标为「记忆检索所需的数据库扩展」——已超出"开源合规署名"的必要范围。且禁词表 58 条**不含底座品牌名**，所以 `check-banned-terms.mjs` 的"0 命中"**不能**用来证明"前端不出现底座字样" | `apps/web/src/app/(main)/about/page.tsx:31-48`；`check-banned-terms.mjs:49-110` | Q-03 ≡ A-12 |
| **P1-08** | 安全 / 纵深 | **产物下载 `Content-Disposition: inline` + 接受任意声明 MIME + 适配层不发 `nosniff`** → 同源存储型 XSS 面。当前因 P0-6 而**不可达**（"被 bug 挡住的风险"），**修 P0-6 时必须同批修本条，否则打开新攻击面** | `artifact_files.py:84-92,120-138`；`artifact_repo.py:96-101`；`grep nosniff app/` → 0 | S-02 |
| **P1-09** | 安全 / 配置 | **`OA_PAY_CHANNEL=fake` 无生产守卫**：配上即关掉回调验签、可用伪造回调开通订阅。对比 `auth_mode=stub` 有 30004 拦死——**同一类"测试开关"两套待遇**。默认值与两份交付模板均为 `wechat_pay`（安全），风险在运维复制粘贴 | `config.py:433-445`；`security.py:865-968` 不看 `pay_channel` | S-04 |
| **P1-10** | 安全 / 配置 | **`OA_JWT_SECRET` 用模板占位值可通过启动校验**：只判"非空"，不判"是否仍是 `CHANGE_ME_*`"。走官方 `install.sh` 安全（有残留检查），但"手抄模板"路径会留下仓库公开常量作签名密钥 → 任何人可伪造任意租户/角色 JWT | `security.py:909-917`；`deploy/.env.example:160`、`.env.lite.example:119` | S-05 |
| **P1-11** | 安全 / 纵深 | **`require_permission` 空 permission 仍直接 `return`**（`if not permission: return`）——与上一轮定位**行号完全相同、代码一字未改**。核实当前不可利用（2 个调用点均有前置校验），属纵深缺口而非现存越权；应改为抛 `CONFIG_ERROR`（编码错误应在测试期炸） | `security.py:287-288`（主理人抽验已复现） | S-06 |
| **P1-12** | 安全 / 业务 | **前端每次写请求都现场生成新幂等键** → 后端 52 个写端点的 `IdempotencyManager` 在 UI 侧被架空。双击提交、网络重试、回退再提交拿到的是不同键，**无法去重** → 重复下单/重复扣额度是真实业务损失。兜底 RNG 用 `Math.random()` | `api-client.ts:296-299,260-268` | Q-11 ≡ S-09 |
| **P1-13** | 测试 / 前端 | **`components/workbench` 整目录 0 覆盖**（5 文件 676 行），而宿主页 92.63% 是靠 `vi.mock` 把 4 个子组件整体打桩换来的——页面↔子组件的**接缝（props 名/回调签名）在单测中完全不可见**，替身的 props 类型是重新声明的，`tsc` 也拦不住改名。同类：`artifact-card.tsx`(280 行) 0%、`category-bar.tsx` 0% | `workbench/page.test.tsx:62,96,122,144`；前端覆盖率报告 | T-04 |
| **P1-14** | 测试 / 有效性 | **`tests/e2e` 在默认命令下完全不执行**，"e2e" 名不副实（需真起栈，本轮亦未运行）；`tests/security` 同理集中在契约桩。这使 P1-13 的子组件缺口失去了兜底 | QA 报告 §四、附录 B | T-05 |
| **P1-15** | 测试 / 门禁 | **覆盖率棘轮阈值 = 实测 − 0.3pp**（86.4/80.4/81.1/87.6 vs 实测 86.75/80.72/81.45/87.91）——阈值贴着实测走，只能防倒退、不能促改进；且近期"补测批次"同时夹带真实缺陷修复，覆盖数字与质量改善无法归因 | `vitest.config.mts:302-307`；最近 30 提交全为 `test` | T-06 |
| **P1-16** | 测试 / 口径 | **9.5%（235/2465）用例默认不执行，且全部集中在同一维度（PG）**。单一原因 skip 本身**合理**（不是"永久 skip 掩盖问题"），但它意味着**生产主路径默认不在本地防线上**，与 P0-7 是同一枚硬币两面 | `conftest.py` PG 门槛 | T-10 |
| **P1-17** | 部署 / 配置 | `.env.example` 的 `OA_POSTGRES_USER` / `OA_KEYCLOAK_ADMIN` / `OA_OBJECT_STORE_ACCESS_KEY` 取**弱默认用户名**（口令仍随机，故只列中）。`install.sh` 会 `openssl rand` 重生成，但"手抄模板"路径会留下 | `deploy/.env.example:42,68,138`；`install.sh:346-357` | A-13 |
| **P1-18** | 部署 / 回滚 | 11 条迁移中**只有 7 条有 down SQL**（`0006`-`0012`），`0002`-`0005` 无回滚脚本 → `rollback.sh` 无法整链回退 | `deploy/migrations/rollback/` 仅 7 个 | A-14 |
| **P1-19** | 部署 / 灾备 | 备份恢复的**代码级校验很扎实**（dump 魔数 + sha256 + 行数基线 + 样本行比对），但**仓库内没有演练记录/证据**；`rollback.sh` 无 digest 校验（按 tag 回退）。灾备"有实现、无证据" | `backup.sh:271,288,166`；`restore.sh:125-136,414-415` | A-15 |
| **P1-20** | 性能（小项） | 三处小 N+1：分群回填 `_run_id_of` 逐条查；知识库**内存后端**列表逐条 `count_documents`（PG 后端已 JOIN，两后端一快一慢会掩盖真实性能）；插件前置依赖逐项查 | `segment_service.py:241,259`；`knowledge_base_repo.py:296-297`；`plugin_service.py:1287` | A-16 |
| **P1-21** | 依赖版本 | **前端 12 项落后，其中 8 个大版本跨越**（typescript 5.9→7.0、tailwindcss 3.4→4.3、zustand 4.5→5.0、date-fns 3.6→4.4、lucide-react 0.454→1.66、sonner 1.7→2.0、tailwind-merge 2.6→3.7、@types/node 20→26）。后端 10 项全为补丁级（无破坏性风险）。与上一轮 L7 结论一致且**未推进** | `npm outdated` / `pip list --outdated` 实测 | A-18 |

### 2.3 低（17 项）

| 编号 | 维度 | 问题 | 证据 | 来源 |
|---|---|---|---|---|
| **P2-01** | 架构债 | 超大文件 **>800 行 20 个**、>1500 行 1 个（`model_service.py` 1734、`plugin_service.py` 1452、`tenant_data_service.py` 1352、`task/runner.py` 1312、`usage_service.py` 1177、`billing/postgres.py` 1127、`rag_service.py` 1089）；前端 `lib/types.ts` 1751、`api-client.ts` 1593；门禁脚本自身 `validate_repository_parity.py` 3342 | `wc -l` 实测 | A-11 |
| **P2-02** | 安全 / 日志 | **`redact()` 已实现但未接入 structlog processors 链**（死代码）→ 敏感字段自动掩码这层防护**实际不存在**。当前逐条抽查无实际泄露，属"待生效防护" | `logging.py:71-87` 定义、`:115-129` 链中无它；全仓 0 引用 | Q-06 ≡ S-08 |
| **P2-03** | 代码质量 / 披露 | `mypy` 205 文件 0 error 是在 **`strict=false` + 全局 `ignore_missing_imports`** 下达成：`disallow_any_explicit`/`disallow_untyped_calls`/`strict_equality` 均未开启；`Any` 451 处。`type: ignore` 18 处**分布集中且未滥用**（17 在 `db/migrate.py`），`warn_unused_ignores` 已开 | `pyproject.toml:159,168` | Q-07 |
| **P2-04** | 契约一致性 | 前端 `KnowledgeBase` 把后端 `KnowledgeBaseItem` 的 **3 个必填字段声明为可选**（`chunk_policy`/`my_role`/`updated_at`）→ `tsc` 无法拦住漏用。当前 UI 有 `?? 'default'` 兜底故未产生功能错误，但契约校验能力被削弱 | `types.ts:164-171` vs `schemas/rag.py:134-161` | Q-08 |
| **P2-05** | 门禁 / 文档失真 | **`ruff format --check` 实测 4 文件红灯**（`member_service.py:194`、`identity_service.py:253`、`test_org_repo_pg.py:115`、`test_endpoint_permissions.py:81`），而 README 两处声明"全绿" | 实跑 `4 files would be reformatted, 384 files already formatted` **EXIT=1**（主理人已复现） | Q-09 |
| **P2-06** | 文档一致性 | `main.py:13` 写"30 个核心业务端点"，同文件 `:28` 又写"99 个操作"，前后自相矛盾；实测 96 个路由装饰器 / 99 个 OpenAPI 操作 | `app/main.py:13,28` | Q-10 |
| **P2-07** | 前端 / 错误透传 | `toBusinessMessage()` 对非 `BusinessError` 的任意 `Error` 直接 `return error.message` → `TypeError: Failed to fetch`、TLS 证书错误等**英文技术文案原样进 toast**，违反"只展示业务语言" | `business-errors.ts:225-227` | Q-12 |
| **P2-08** | 安全 / 上传 | 上传**只校验扩展名**，无魔数、无文件名净化。核实：内容先落 `TemporaryFile` 且不按文件名拼路径 → **无路径穿越、无本地 RCE**；仅"扩展名伪造 → 下游解析器吃到非预期格式"的间接风险。大小限制真实生效 | `rag_service.py:594-601,454`；`sanitize_filename` 0 命中 | S-07 |
| **P2-09** | 安全 / 可用性 | 产物下载限流按 `request.client.host`，反代后所有用户**共享一个令牌桶**（60/min）→ 任一大客户打满即全员 429。**不信任 XFF 反而是对的**（防伪造绕过），问题是口径没跟鉴权上下文对齐 | `artifact_files.py:65-77`；`lite.conf:72-90` | S-10 |
| **P2-10** | 安全 / 配置 | `jwt_algorithm: str = "HS256"` 未用 `Literal` 约束（对比 `AuthMode`/`DeployMode`/`PayChannel` 都是 `Literal`），环境变量可改成任意值。核实：PyJWT 对 `none` 会抛错、算法列表单元素不存在 HS/RS 混用 | `config.py:71`；`security.py:166-172` | S-12 |
| **P2-11** | 安全 / 信息暴露 | `pg_dump` 把含明文库密码的 DSN 作为命令行参数传递，同机低权用户可读 `/proc/*/cmdline`。核实：**列表参数 + 无 `shell=True`，不存在命令注入** | `db/migrate.py:830-836` | S-13 |
| **P2-12** | 配置语义 | `deploy_mode` 默认 `saas`，而全部交付预设都是 `private`。`saas` 形态**更严**（走跨租户成员校验），故不构成放宽；但会让"`private` 忽略 X-Tenant-Id"的设计在漏配时静默失效 | `config.py:52`；三份 `.env*.example` 均 `private` | S-14 |
| **P2-13** | 测试 / 前端 | 加载态/骨架屏（`SkeletonLoader`）**从未被测**，`ui/skeleton.tsx` 6.25%；三态（加载/空/错）门禁只查"有没有"，不查"对不对" | QA 报告 §三 | T-09 |
| **P2-14** | 工程 / 版本库 | **`.git` 3.3GB，其中 3.2GB 是垃圾**：41 个 `tmp_obj_*`（每个 85-90MB，疑似某次误 `git add` 大文件中途中断）+ 2001 个悬空 blob + 9 个 stash 残留提交；实际 pack 仅 **66MB**。克隆/备份成本被放大 50 倍 | `git count-objects -vH`：`size-pack 65.96 MiB` / `size-garbage 3.15 GiB`；主理人实测 | M-01 |
| **P2-15** | 工程 / 分支策略 | 远端仅 `main`，无 `develop` / `feature/*` / `release/*`，与 README §四声明的 Git Flow 不符（历史可见 `develop`、`feat/*`、`hotfix/*` 曾存在，应已合并删除） | `git branch -a` 实测 | M-02 |
| **P2-16** | 供应链 / CI | 4 个 workflow 权限均为最小 `contents: read`（做得好），但 **33 处 `uses:` 全部用 tag（`@v4`/`@v5`）未 pin SHA** → action 被重推即可污染构建 | `grep "uses:" .github/workflows/` | M-03 |
| **P2-17** | 门禁 / 多租户 | RLS 覆盖 **40/41 张表**（唯一例外 `schema_migration` 为迁移版本表、无 tenant_id，合理），但**没有任何门禁校验"新增含 tenant_id 的表必须启用 RLS"**——靠人工维护 `strict_tables` 数组，未来新增表静默漏掉时无人发现 | `init-schema-phase2.sql:631-641` 数组；`grep -i rls deploy/validate_*.py` 无覆盖检查；主理人实测 | M-04 |
| **P2-18** | 代码结构 / 审计可观测性 | **`now_iso()` 被复制 7 份**（`artifact_repo:99`、`audit_recorder:31`、`billing/records:143`、`conversation/records:42`、`job_repo:75`、`plugin/records:69`、`task/records:88`），函数体逐字相同。**不只是重复代码**：它让任何基于函数名的统计（死代码检测、影响面分析、重构安全评估）失真——本轮审计中主理人与工程师两人据此误判出"10+ 调用方"，实际回溯到任一单份的调用均为 0。建议下沉到 `base.py`（已核实该文件仅在 docstring 提及 `now_iso`、未定义未导入，且被 25 个模块引用，是合适落点），下沉后先补"同名函数禁止新增"的门禁 | `grep -rn "^def now_iso" app/` → **7 命中**；导入链逐跳回溯（QA）→ 回溯到 `job_repo` 者 **0** | T-12 |

---

## 三、前两轮整改闭环核实（三线合并，共 20 项）

| 前轮编号 | 结论 | 证据要点 |
|---|---|---|
| H2 成员列表 N+1 / 内存分页 | ✅ **已修复** | `member_service.py:165,264,216,192-196` 新增 `list_members_page` + 单批 IN；`identity_service.py:255` 改委托，内存 `paginate` 消失 |
| H1 前端覆盖率极薄（13.44% / Funcs 6.60%） | ✅ **已彻底闭环** | 实测 **86.75/80.72/81.45/87.91**，930 用例 59 文件全绿；8 个大文件从 0 升到 83–95%，`api-client.ts` 40%→97.59% |
| H3 SaaS compose 端口暴露 | ⚠️ **部分闭环** | milvus/对象存储/admin 口已绑 `127.0.0.1`（`saas.yml:157,177,178`）✅；**adapter/web 仍全网卡 → 升为 P0-4** |
| M1 Redis 限流窗口不滚动 | ✅ **已修复** | `ratelimit.py:163` 锚点只在窗口首命中写入、`:165` 取真实 TTL，两后端行为对齐 |
| M2 排队查询 | ⚠️ **部分修复** | `next_queued` 已 LIMIT 1、`_queue_rank` 改单条 COUNT ✅；`list_queued` 仍无 LIMIT → P1-03 |
| M3 55 个文件未提交 | ✅ **已闭环** | `git status --porcelain` 空 |
| M4 PG 覆盖盲区 | ⚠️ **改判** | 带 `--run-pg` 后 db 层 72.21%→90.49%、`postgres.py` 27.93%→90.66%。问题应重述为"默认口径不含 PG"而非"PG 无测试" → P0-7 / P1-16 |
| M5/M6 口径漂移 | ✅ **已修复** | `ci-web.yml:87-88` 与实际阈值逐项一致；`pyproject.toml` 已含 `fail_under` |
| M7 令牌链路纵深 | ❌ **未闭环，代码零改动** | 仅新增解释性注释并落档评估 → P0-5 |
| M8 许可成分口径 | ✅ **已对齐** | 实测无 GPL/AGPL/SSPL；灰名单仅 MPL-2.0（certifi/pathspec）、PSF-2.0、MIT-0、BSD-2 |
| L1 超大文件 | ❌ **未修复** | >800 行 20 个，与前轮同量级 → P2-01 |
| L2 循环依赖 3 组 | ⏸ **未修复（无新增）** | 环 3 个全部在基线内、`OA_BATCH=B21` 下未到期（B23-B27） |
| L3 `require_permission` 空放行 | ❌ **未闭环，行号完全相同** | `security.py:287-288` 一字未改 → P1-11（经核实当前不可利用，降级为纵深） |
| L4 validate_secrets 覆盖面 | ✅ **已闭环** | `SCAN_SUFFIXES` 扩到 `.yml/.yaml/.sh/.example`，`.env*` 前缀纳入 |
| L5 镜像 digest 强校验 | ❌ **未修复** | compose 仍按 `name:tag`；新增量化见 P1-05 |
| L6-a/b/c 三处性能 | ✅ **全部已修复** | `rag.py:176,190,192` to_thread；`segment_service.py:221-227` 强引用 + 回调；`workflows/[taskId]/page.tsx:74-90` 节流 + 清理 |
| L7 依赖大版本落后 | ❌ **未修复** | `npm outdated` 仍 12 项（8 个大版本） → P1-21 |
| L8 node_modules extraneous | ✅ **已清理** | React 全树 `19.3.1 deduped` 单实例 |
| L9 裸跑 1 红 | ⚠️ **扩大为 2 个脚本** | `validate_import_directions` 裸跑恒红（P0-3）+ `batch_clock` 缺 `OA_BATCH` 即红（设计口径） |
| **前轮「16 个门禁全 exit 0」** | ❌ **本轮推翻** | `validate_deps.py` EXIT=1（P0-2）；`validate_import_directions.py` 裸跑 EXIT=1（P0-3） |

**闭环率**：20 项中 **✅ 完全闭环 9 项 / ⚠️ 部分 4 项 / ❌ 未闭环 6 项 / ⏸ 未到期 1 项**。未闭环的 6 项里，M7（令牌链路）、L3（权限函数）属"定位行号与前轮完全相同、代码一字未改"——这两条不是"修不动"，是没有进入整改队列。

### 3.1 三线交叉裁决记录（本轮可信度证据）

三线独立作业产生了 3 处结论冲突，均由主理人实证裁决，不留"两个说法并存"：

| # | 冲突 | 裁决 | 依据 |
|---|---|---|---|
| 1 | **`now_iso()` 是谁的函数**：工程师称"`audit_service.py:71`、`ops_service.py:50` 在用（job_repo 的）"，QA 称"回溯到 job_repo = 0"；**主理人首轮裁决站错边，判 QA 错** | **主理人与工程师均误判，QA 正确**。`now_iso` 在仓内有 **7 份独立副本**（`artifact_repo:99`、`audit_recorder:31`、`billing/records:143`、`conversation/records:42`、`job_repo:75`、`plugin/records:69`、`task/records:88`），函数体逐字相同；那 10 处调用点**全部指向各自的副本**，回溯到 `job_repo` 的条数 = **0** | `sed -n '64,72p' audit_service.py` → 来源是 `audit_recorder`；`sed -n '44,50p' ops_service.py` → 来源是 `billing`；`grep -rn "^def now_iso" app/` → 7 命中 |
| 2 | **T-01 修复范围**：QA 初版"3 处"，工程师"4 处"，QA 修订后"4 处 + 2 处方法内混源" | **取最大集**：4 处墙钟调用点 + `claim`（`:352` vs `:355`）与 `mark_failed`（`:390` vs `:391`）两处**方法内部混源** | 逐行核读 `job_repo.py`；混源后果可量化（`updated_at` 与 `run_after` 差 ≈7h40m 并放大） |
| 3 | **测试夹具是否要改**：主理人初判"把硬编码 `2026-11-01T09:00Z` 改相对时间"，QA 反向纠正 | **撤回主理人初判，采纳 QA**：源码修好后 `_Clock` 硬编码初值应保留（判据是"该时间是否与未注入的墙钟比较"，而非"是否硬编码"） | QA 给出判据；过度处置会让确定性测试变成时间相关测试 |

> 记这一节的理由：三线并行最容易出现的失败模式不是"漏报"，而是"三个说法互相矛盾却都不改"。
>
> **第 1 条是这份报告最该记住的一课**：主理人、工程师、QA 三方里，有**两方都栽在同一个错误**上——用 `grep now_iso` 看到 10 处命中，就断定"这个函数被广泛使用"，没有一处去追 `from … import` 的**来源模块**。真相是一个 2 行函数被复制了 7 份，每份只服务自己那个域。
>
> QA 在复核时给出了更准确的表述，值得作为本仓后续审计的方法论：
> 1. 对**同名多定义**的符号，名字级 grep **不能**作为"依赖面"证据，必须按「模块 → 定义/再导出」逐跳回溯到终点；
> 2. 反过来，**"是否被复制/散落"这种结论只能靠名字级统计得出**。同一份数据、两种问法需要两种口径，混用会得出相反结论——本轮正好各踩一次：回溯得"7 份副本"、名字得"10+ 调用方"，**两者都对，但回答的不是同一个问题**；
> 3. 任何新增 `validate_*.py` 若以符号名做一致性校验，必须先把"多定义"纳入判据，否则会产生假红/假绿。
>
> 这个结构不仅让本次误判，它会让**任何基于名字的统计**（死代码检测、影响面分析、重构安全评估）都失真——这才是 `now_iso` 该单列为一条问题（P2-18）的真正理由，而不是"重复代码不好看"。
>
> 第 3 条同样值得记：**成员主动收回主理人的过度处置建议**（把"改测试时钟"改成"保留硬编码初值"），比多报一条问题更有价值。审计报告的可信度不来自"从没判错"，而来自"判错之后有实证把它翻过来"。

---

---

## 四、改进建议清单

### P0 — 立即（本迭代内，阻断交付）

| 序 | 动作 | 对应问题 | 完成判据 |
|---|---|---|---|
| 1 | 修 `InMemoryJobStore` 多时间源：新增 `_stamp()` 走 `self._now()`，替换 `:303`/`:355`/`:368`/`:391` **四处**（漏一处即"记录内部仍不一致"）。红线：`now_iso()` 模块函数**不可删改**——它在 `__all__` 里，且有 **10+ 处真实调用方**（`audit_service.py:71`、`ops_service.py:50`、`order_service.py:62`、`plugin_service.py:51`、`subscription_service.py:46`、`task/runner.py:49`、`billing/*`、`conversation/memory.py:26`）。测试夹具**无需改动**：`_Clock` 的硬编码初值应保留（判据是"是否与未注入的墙钟比较"，源码修好后它就是确定性的优势） | P0-1 | 复验四条：`pytest tests/unit/test_job_runner.py` → **0 failed**；全量 pytest（无 PG）→ 0 failed；`ruff check` → 0；`mypy app` → 0。判定标准加一条：**把系统时钟拨后一年重跑仍 0 failed** |
| 2 | 处理 4 条 Windows PG 夹具红（`Event loop is closed`）：改函数级 engine + 显式 `dispose`，或统一 `SelectorEventLoop` | P0-1 | 含 `--run-pg` 口径 → 0 failed |
| 3 | `validate_deps.py` 白名单 `python-jose` → `pyjwt`，并补一条**反向检查**（白名单项必须能在 `pyproject.toml` 解析到，否则报 stale） | P0-2 | 裸跑 EXIT=0；故意删除一个依赖时门禁能红 |
| 4 | `validate_import_directions.py` 的 `--baseline` 默认值改为 `DEFAULT_BASELINE`（与 `--cycle-baseline` 对称）；README §六 命令清单同步 | P0-3 | 裸跑 EXIT=0；新增一条违规时能红 |
| 5 | 产物下载三件套**同批**做：①nginx 加 `location ^~ /files/artifacts/` 并就地重复 `nosniff`；②`resolve_content_type` 加白名单、未知降级 `octet-stream`；③`_content_disposition` 改 `attachment` + 处理 CR/LF | P0-6 + P1-08 | 走 nginx 入口的 e2e 下载用例通过；`text/html` 声明的产物被降级为下载 |
| 6 | 令牌链路决策并落地（三选一：httpOnly Cookie / CSP nonce + `connect-src` 收敛到 `OA_ADAPTER_ORIGIN` / 内存令牌 + refresh 换发） | P0-5 | CSP 不含 `unsafe-inline` 且 `connect-src` 无裸 `http:`；令牌不在 `localStorage` |
| 7 | 两套 compose 加 nginx 服务（复用 `lite-tls.conf`），adapter/web 改 `127.0.0.1:` 或 `expose:`；至少给 adapter 配 `mem_limit` 与 healthcheck | P0-4 | `grep -c "0.0.0.0\|^\s*- \"8000:"` = 0；TLS 握手可达 |
| 8 | README 数字单一真源：把「2211/212/0 failed/85.87%」「门禁全绿」「ruff format 全绿」改为链路引用或移除，禁止手抄快照 | P0-1、P0-2、P2-05 | README 不再出现任何硬编码测试/门禁数字 |

### P1 — 近期（两周内，SaaS 上线前）

| 序 | 动作 | 对应 |
|---|---|---|
| 9 | 导出链路改 `spooled tempfile` + `StreamingResponse`，加 `asyncio.Semaphore` 与同租户去重 | P1-01 |
| 10 | 仓储层把「分页下推 + 批查询」沉淀为**编码规则**（含 review checklist），而不是逐点修：`ops_service` 租户列表、`list_queued` 加 LIMIT、`add_artifacts` 单事务批量写 | P1-02/03/04 |
| 11 | `pay_channel=fake` 增加 30004 启动判据；`validate_auth_startup` 增加占位值黑名单（`CHANGE_ME`/长度<32） | P1-09/10 |
| 12 | `require_permission` 空权限改抛 `CONFIG_ERROR`，并补用例 `require_permission(auth, "")` 必须抛错 | P1-11 |
| 13 | 幂等键与「用户动作」绑定（表单实例化时生成一次），兜底 RNG 改 `crypto.getRandomValues` | P1-12 |
| 14 | 前端补 `components/workbench/*` 6 个子组件级用例（不要继续加页面用例）；`tests/e2e` 接入 CI 或在 README 明确"非默认执行" | P1-13/14 |
| 15 | 覆盖率门禁校准：CI 5b 的 `postgres.py` 下限 50% → **88%**；本地提供一条「含 PG 一键命令」（脚本或 make target） | P0-7 / P1-16 |
| 16 | about 页技术选型改业务语言（「流程执行引擎」「数据存储与检索引擎」）或移入交付包不渲染；禁词表补品牌名 + 显式白名单例外 | P1-07 |
| 17 | 补 `0002`-`0005` down SQL，或显式声明"最低可回退点 = 0006"并写进部署文档 | P1-18 |
| 18 | 跑一次 clean-room 备份恢复演练，输出落 `docs/drills/` | P1-19 |
| 19 | nginx 补 `ssl_ciphers` 显式套件 + OCSP；把 `lite-tls.conf` 提升为全形态共用模板 | P0-4 配套 |
| 20 | 镜像收到补丁位（`nginx:1.28.x-alpine` 等）；squid 的 `_beta` 通道写书面风险接受；评估 compose 用 digest 引用 | P1-05 |

### P2 — 持续（技术债批次）

| 序 | 动作 | 对应 |
|---|---|---|
| 21 | `git prune --expire=now && git gc --prune=now` 清 3.2GB 垃圾（`prune-packable=0`，安全）；清理 9 个 stash 残留 | P2-14 |
| 22 | 拆 `model_service.py`（模型目录/生成/用量三段）与 `plugin_service.py`（清单/安装/授权三簇） | P2-01 |
| 23 | 把 `redact()` 插进 structlog processors 链（3 行）；`is_sensitive` 一并接入或删除 | P2-02 |
| 24 | 为「含 tenant_id 的新表必须启用 RLS」加门禁（扫 `CREATE TABLE` 与 `ENABLE ROW LEVEL SECURITY` 做集合差） | P2-17 |
| 25 | 前端大版本升级单独立项（tailwind 4 / typescript 7 / zustand 5），不与交付同批；后端补丁批随锁重建 | P1-21 |
| 26 | CI actions 逐步 pin SHA；恢复 CI 额度后先跑一次全绿作为基线 | P2-16 |
| 27 | 把 60+ 处文档串里的"已接受权衡"抽成 `docs/tech-debt.md` 索引（当前只在注释里，工具无法聚合） | Q-05（正向结论的引申） |

---

## 五、本轮确认「做对了」的控制（对照用，不要再改）

| 维度 | 事实 | 证据 |
|---|---|---|
| 架构红线 | `dify_client.py` 全仓唯一底座出口（`httpx`/`requests` 仅现于 `dify_client.py`、`keycloak_client.py`）；`api/`、`business/` 对 `app.clients` 导入 **0 处** | grep 复核 |
| 前端唯一出口 | `api-client.ts` 是全仓唯一网络出口（`fetch(` 仅 2 处，均在 `api-client.ts:310,600`） | grep 复核 |
| 端点契约 | 96 个路由装饰器（GET 43/POST 27/DELETE 12/PATCH 8/PUT 6），`validate_openapi.py --strict` 五组全通过 | 实跑 |
| 多租户隔离 | 三重：`tenant_clause()` + PG RLS `FORCE`（40/41 表）+ `assert_tenant_membership`；RLS 会话变量 `set_config(..., TRUE)` 为事务级（单测已固化） | `init-schema-phase2.sql:631`；`session.py:42` |
| 注入面 | SQL 注入 0（全参数化）、命令注入 0（`subprocess` 列表参数 + 无 `shell=True`） | 源码走查 |
| 凭据保护 | AES-256-GCM + `os.urandom` + 密钥轮转；`credential_service` 日志只打事件名 | 源码走查 |
| 支付安全 | 回调**验签先于占幂等键**（顺序正确） | 源码走查 |
| 错误处理 | 错误码注册制 100% 引用 `ErrorCode.*`（硬编码 0 处）；`internal_detail` 只进日志不进响应 | grep + G6 |
| 技术债卫生 | `TODO/FIXME/HACK` 真实计数 **0**；无 `pass`/`...` 占位 | grep |
| 依赖安全 | `pip-audit` 0 漏洞（51 包）/ `npm audit --omit=dev` 0 漏洞 / 无 GPL·AGPL·SSPL | 实跑 |
| 测试质量 | 0 处 `assert True`、0 永久 skip、`xfail` 白名单为空、mock 极少、安全用例带锁文件三重钉 | QA 走查 |
| 备份恢复 | dump 魔数 + sha256 对 meta.json + 行数基线 + 样本行比对，sha256 不一致即 abort | `backup.sh:271,288`；`restore.sh:125-136` |

---

## 六、未验证盲区（需真库 / 压测 / Linux 才能定论，本轮**不下结论**）

1. **后台任务认领执行计划**：`job_repo.py:558-575` 的 OR 条件 + `ORDER BY`，第二支 `locked_until` 不在 `idx_job_due` 内，可能退化为索引扫 + 过滤；需 `EXPLAIN (ANALYZE, BUFFERS)`。
2. **队列索引缺口**：`plugin_task` 现有索引无一条同时覆盖 `(tenant_id, plugin_id, status, started_at, task_id)`；`list_queued` / `_queue_rank` 的实际计划未验证。
3. **导出真实内存峰值**：200k 行导出的实测 RSS、`_ZIP_CHUNK_ROWS=500` 是否最优、`to_thread` 线程池竞争。
4. **大表聚合与向量检索**：`usage_event` 跨期聚合、`plugin_task` FILTER 统计、`memory_embedding` HNSW 检索表现。
5. **多副本语义**：内存限流/幂等在多副本下按副本数放大（V1.0 单副本下行为正确）；`deploy/k8s` 无 HPA，扩容前须切后端。
6. **SSE 长连接资源**：SSE 只计 1 次限流（按握手），连接上限由 `worker_connections 1024` 与连接池共同决定，未压测。
7. **前端 bundle 体积**：`apps/web/.next` 不存在，`'use client' 87%`（P1-06）的影响需一次 `next build --analyze` 量化。
8. **Playwright e2e**：3 个 spec / 163 行未运行，前端子组件缺口的兜底强度未实测。
9. **`nginx -t`**：需 Linux，两轮审计均未执行。
10. **QA 稳定性**：未做同命令两次复跑比对；T-01 的 8 红已用注入时钟探针证明为确定性，4 条 PG 红未验证 Linux 是否复现。

---

## 七、结论与放行判断

| 维度 | 判定 | 一句话理由 |
|---|---|---|
| 架构与目录结构 | **通过** | 五层分层与红线经实跑复核全部成立，96 端点与冻结基线一致，目录树 24 条声明无虚构 |
| 代码质量与规范 | **通过（附 2 项披露）** | ruff check / mypy / eslint / tsc 全绿；错误码与黑盒化基本到位；需披露 `ruff format` 4 文件红、mypy 非 strict |
| 安全 | **有条件通过** | 核心控制（注入/租户隔离/凭据/支付顺序）扎实；但 P0-5 令牌链路必须在上公网前决策，P1-08/P1-09/P1-10/P1-11 需同批修 |
| 性能 | **通过（附整改项）** | 无崩溃级缺陷；P1-01 是唯一可能"拖垮宿主"的可用性风险，P1-02/03/04 属同型复发需规则化 |
| 依赖与许可 | **通过** | 0 漏洞、无 GPL/AGPL/SSPL、镜像版本与基线吻合；缺口在补丁级浮动与 digest 未强制 |
| 测试覆盖 | **不通过（阻塞）** | 12 条红灯 + 默认口径擦线 + e2e/security 不执行 + 子组件 0 覆盖 |
| 交付态 | **不通过（阻塞）** | 两处门禁假绿/真红、README 数字失真、CI 从未运行、k8s 空壳 |

**总判定：`main` 当前不具备「可发布」条件。** 阻塞项集中在两处：**验证链没跑通**（P0-1/2/3/7）与**部署形态没成型**（P0-4/6）。这两组之外，代码本身的工程质量处于**高于行业平均**的水平——问题不在"写得差"，在"没跑、没验、没交付成型"。

**建议的 48 小时行动顺序**：`P0-1 → P0-2 → P0-3 → P2-05`（四条都是一两行改动，先把"红变绿"恢复可信）→ 再动 `P0-6+P1-08`（同批，避免开新面）→ 再决策 `P0-5` → `P0-4` 与 `P0-7` 排入下一迭代。

---

*本报告由主理人汇编三线分册而成，未改写成员实测数据；所有抽验复现记录见 §二 各条「主理人已复现」标注。分册保留完整证据链与逐条行号，可直接作为整改工单使用。*
