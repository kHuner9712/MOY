# Moy（麦子）全仓审计报告 · 2026-10-30

> 审计范围：架构与目录结构、代码质量与规范一致性、安全漏洞、性能隐患、依赖版本与许可、测试覆盖。
> 方法：静态代码走查 + 门禁脚本核验 + 实测执行（pytest/vitest/ruff/mypy/eslint/tsc/pip-audit/npm audit/license-checker/SBOM 抽验）。
> 所有数字均来自真实命令输出；密钥类发现只记位置与类型，不复述值。
> 前序基线：2026-10-29 已做过一轮全仓审计清理（commit `34e761a`），本报告为独立复审。

---

## 一、总体评价

仓库治理水平显著高于同类项目常态：ruff / mypy（204 文件）/ eslint（--max-warnings=0）/ tsc **四项静态检查全绿**；后端 2343 用例体系（本地口径 2210 过 / 1 红 / 212 skip）、前端 7 项自研门禁扫描全绿；镜像 digest 锁定、多阶段构建、非 root 运行、SBOM 四层（724 组件）齐备；租户伪造（AR-017）、权限 fail-closed 白名单锁定、日志脱敏等安全红线均有用例看护。

**本次未发现可直接利用的已修复缺陷**。核心风险集中在两条主线：
1. **供应链**：JWT 验签链路上的 python-jose 存在无修复版 CVE；
2. **测试纵深**：前端单测覆盖率约 6%、后端 PG 实现为本地覆盖盲区，回归防线前重后轻。

---

## 二、问题清单（按严重程度分级）

### 高（2 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| H1 | 依赖/安全 | **python-jose 3.5.0 CVE-2026-85394（PYSEC-2026-4298），无修复版本**。HMAC 初始化接受 DER 公钥，攻击者持公钥可伪造 HS256 令牌；且是对 CVE-2024-33663 的不完整修复。命中 JWT 验签核心链路。缓解事实：[security.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/core/security.py#L155) 以 `algorithms=[conf.jwt_algorithm]` 显式限定单算法（默认 HS256 对称密钥），该 CVE 的"算法未限定"前提不成立，实际可利用性低；但上游无版可升，整链（python-jose→ecdsa）低维护 | pip-audit 实测；apps/adapter/pyproject.toml L39 |
| H2 | 测试覆盖 | **前端单测覆盖率约 6%（Statements 5.82% / Branches 5.73% / Functions 3.30%）**，55 个用例只覆盖 lib/hook 少数模块；约 9 成文件 0 覆盖——含 698 行的 `team/page.tsx`、661 行的 `plugins/[pluginId]/page.tsx`、500 行的 `oidc.ts`、304 行的 `session-store.ts` 及几乎全部业务组件。且覆盖率门禁**形同虚设**：[vitest.config.mts](file:///c:/Users/15864/Desktop/麦子/apps/web/vitest.config.mts) 阈值仅 lines 5 / functions 2，贴着实测（5.96 / 3.30）几乎不设防（注：初稿误记为"无 coverage 阈值"，实测配置存在"只升不降"整改起点地板，已在 P0-2 抬升） | vitest run --coverage 实测（2026-10-30） |

### 中（11 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| M1 | 测试覆盖 | 后端覆盖率 85.87%（greenlet+thread 口径）**仅高出门禁 0.87pp**；覆盖最低 5 个模块全部是 PG 仓储实现（task 16.48% / billing 22.61% / plugin 25.38% / org 26.87% / usage 30.61%）。212 个 PG 用例本地默认 skip，**生产主路径恰是本地覆盖盲区**，覆盖率数字由内存实现撑起 | pytest --cov 实测（2026-10-30） |
| M2 | 门禁韧性 | 无 `OA_BATCH` 环境变量时 `test_d1_deferred_registry_matches_markers` 必失败（"缺失即红"是设计意图），但裸命令口径下门禁为红；CI 依赖外部注入，本地复跑易误判为回归 | pytest 实测 1 failed |
| M3 | 依赖 | **本地 venv 与 uv.lock 漂移**：venv 多出 slowapi 0.1.10 / limits 5.8.0 / Deprecated 3.0.0 / wrapt 2.5.1（均不在锁文件、代码不引用），editable 元数据把 slowapi 列为运行时依赖、缺 cryptography。测试实跑环境 ≠ 锁定环境 ≠ 镜像构建环境 | pip list vs uv.lock 对账实测 |
| M4 | 许可合规 | pyproject 注释"全部为 MIT / Apache-2.0 / BSD-3-Clause"**与实测不符**：运行时链存在 MPL-2.0（certifi，经 httpx）、PSF-2.0（typing_extensions、greenlet）、MIT-0（cffi）、BSD-2-Clause（pyasn1）等。"无 GPL/AGPL/SSPL"属实 ✅。MPL-2.0 属文件级弱互惠，私有化交付需随包保留许可证文本 | pip-licenses 实测；pyproject.toml L18 |
| M5 | 前端依赖 | **双 React 实例**：root `node_modules/react@18.3.1`（lock 标 `peer:true` 的历史残留）与 `apps/web/node_modules/react@19.3.1` 并存；提升到 root 的运行时库（next/radix/sonner/lucide/@tanstack）解析的是 18.3.1，应用代码解析 19.3.1。存在 "Invalid hook call"/双实例隐患 | npm ls 实测 |
| M6 | 前端依赖 | **node_modules 安装态不健康**：lock 声明 prettier@3.3.3 磁盘缺失、带外残留 prettier@3.9.10（`npx prettier` 静默用到非锁定版本）；两棵树合计约 43 个 `.包名-哈希` 中断安装残留目录，`@typescript-eslint` 下旧版 6.21.0 与现行 8.72.0 并存。干扰扫描统计与模块解析 | npm ls / 磁盘对账实测 |
| M7 | 依赖/安全 | **ecdsa 0.19.2 CVE-2024-23342（PYSEC-2026-1325），上游声明不修**：P-256 签名 Minerva 时序侧信道可泄漏 nonce。仅 ES* 算法签名触发（当前默认 HS256），但随 python-jose 传递引入、无退出路径 | pip-audit 实测 |
| M8 | 安全/部署 | **nginx 无 TLS、无安全响应头（HSTS/X-Frame-Options/CSP/X-Content-Type-Type）、无限流**：[lite.conf](file:///c:/Users/15864/Desktop/麦子/deploy/nginx/lite.conf) 仅 `listen 80`（`server_tokens off` 有做）。私有化内网可接受，**SaaS 对外前置必补** | deploy/nginx/lite.conf L26-31 |
| M9 | 安全/前端 | token 存 **localStorage**（[session-store.ts](file:///c:/Users/15864/Desktop/麦子/apps/web/src/lib/session-store.ts#L13-L21) 注释自认同源 XSS 可窃取），叠加 [next.config.js](file:///c:/Users/15864/Desktop/麦子/apps/web/next.config.js#L36-L46) CSP 含 `'unsafe-inline'`（RSC 需要，已注释论证）。XSS 面一旦成立即直接丢会话 | 源码走查 |
| M10 | 许可合规 | sharp 平台二进制 `@img/sharp-win32-x64` / `@img/sharp-wasm32` 标注 **LGPL-3.0-or-later**。SaaS 服务端不分发则触发面小；若分发桌面端/自托管安装包需按 LGPL 履行动态链接/开源义务。Dockerfile 已用 `--omit=optional` 排除 Linux 侧 sharp（R2-ARC-03） | license-checker 实测；apps/web/Dockerfile |
| M11 | 架构 | **分层方向违规基线未清零**：[import_direction_baseline.txt](file:///c:/Users/15864/Desktop/麦子/deploy/import_direction_baseline.txt) 登记 24 条（`api→db` 6 条含 artifact_files.py 直引 artifact_repo、`business→services`、`services→db`、`db→上层`（plugin/memory 仓储依赖 plugins/services 实现）、`core→上层` 等），均"已评审+有消除计划+due_batch 到期"且 CI 门禁化，但长期挂账会侵蚀"五层解耦"承诺。（初稿曾列 audit_repo→business 一条，经查该条切片 1 已修复、注释为历史说明，P1-8 中已更正） | 基线文件；validate_import_directions.py |

### 低（9 项）

| 编号 | 维度 | 问题 | 证据 |
|---|---|---|---|
| L1 | 依赖 | pip 26.1.2 CVE-2026-13346（工具链，非交付运行时；26.2 已修） | pip-audit 实测 |
| L2 | 依赖 | six 1.17.0（py2 兼容残留，经 python-jose 链）；asyncpg 的 license URL 字段 UNKNOWN；python-jose/ecdsa 组合事实上低维护 | pip-licenses 实测 |
| L3 | 供应链 | SBOM（CycloneDX 1.5，724 组件，四层齐备且与 uv.lock 一致）。复核（2026-10-30）：`sbom.yml` **已有每周一 03:00 UTC 定时重算**（cron `0 3 * * 1`）+ 四层断言 + license-scan，初稿「无自动重生成」表述已过期；无「提交产物过期检查」——SBOM 产物不 git 入库，故本就不需要 | deploy/sbom/generate.py；.github/workflows/sbom.yml L45-48 |
| L4 | 前端依赖 | 旧大版本：lucide-react 0.454.x（受 0.x caret 锁死，落后一年以上）、zustand 4.x、tailwindcss 3.x（维护模式）、date-fns 3.x、sonner 1.x | npm outdated 实测 |
| L5 | 前端依赖 | 补丁级落后：next 16.4.2→16.4.3、eslint-config-next、postcss、autoprefixer、@tanstack/react-query | npm outdated 实测 |
| L6 | 架构 | 仓储双实现差异基线：[repository_parity_baseline.txt](file:///c:/Users/15864/Desktop/麦子/deploy/repository_parity_baseline.txt) 登记 grant 内存实现多出 `set_user_departments/set_user_roles/set_subject_name` 等——内存/PG 行为漂移风险，已有 parity 门禁看护 | 基线文件 L18-34 |
| L7 | 部署 | `deploy/k8s/` 目录只有一个 README.md，无实际清单（k8s 部署面尚未兑现）。README 已明确标注「阶段四验收范围外、占位目录、manifests 均未创建」，**无文档误导**；能力未兑现属排期问题 | 目录列表；deploy/k8s/README.md L3-11 |
| L8 | 代码质量 | 后端 `app/` 仅 1 处 TODO 类标记（plugins/schema.py）；前端 `src/` 零 TODO/console.log/any 滥用——该项实为质量亮点，登记残留供清零 | grep 统计 |
| L9 | 安全/运维 | `deploy/.env` 明文存有 PG/Valkey/对象存储/Dify/DeepSeek/Keycloak 等凭据（**未入 git**，仅 .example 被跟踪 ✅），本地磁盘明文 + 密钥轮换依赖人工。`validate_secrets.py` 已在 `dependency-scan.yml:162` 接线且带自检（复核 2026-10-30）；剩余风险属运维侧凭据管理 | deploy/.env L34-115；git ls-files 核验；dependency-scan.yml |

### 性能维度（观察项，无实锤问题）

- 静态面干净：async 路径**无同步阻塞调用**（time.sleep/requests 零命中；subprocess 仅 migrate CLI 场景）；仓储/业务查询普遍带 `LIMIT/OFFSET` 分页；有 `enforce_ratelimit` 限流；nginx 的 SSE 300s 超时与上传 50m 上限刻意与后端 413 口径对齐。
- **盲区**：PG 查询计划/索引效率因 212 个 PG 用例 skip 未在本地验证；无压测基线数据；前端大页面组件（team/page.tsx 698 行）渲染性能无实测。建议纳入 PG 环境专项验证，而非当前结论为"有隐患"。

---

## 三、分项结论速览

| 维度 | 结论 | 关键数字 |
|---|---|---|
| 架构与目录 | 五层架构落地良好，门禁/基线/ADR/CI 接线完整；扣分项是违规基线长期挂账 | git 761 文件，无构建产物/.env 入库；validate_*.py 16 个与 README 一致 |
| 代码质量 | 优秀：四项 lint/typecheck 全绿，TODO≈0，命名/注释/错误码体系统一 | ruff 0 告警；mypy 0 错（204 文件）；eslint 0 告警；tsc 0 错 |
| 安全 | 无已修复缺陷逃逸；租户伪造 AR-017 有修复+用例、权限 fail-closed 白名单锁定、日志脱敏、非 root + digest 锁镜像。风险在供应链 CVE 与 XSS→localStorage token 链 | 测试含 tests/security/ 专项（五类攻击用例） |
| 性能 | 静态面无实锤隐患；PG 查询与前端大页为未验证盲区 | 分页/限流/SSE 超时口径已对齐 |
| 依赖与许可 | npm 0 已知漏洞；pip 3 包 5 漏洞（1 高 2 中低）；无 GPL/AGPL/SSPL，但声明措辞与实测不符、含 MPL-2.0/LGPL 成分 | 后端 58 包（uv.lock）；前端 lock 约 564 name@version |
| 测试覆盖 | 后端体系庞大（2343 用例、安全/契约/PG 分层）但门禁余量薄 + PG 盲区；前端覆盖严重不足 | 后端 85.87%（门禁 85%）；前端 5.82%（无门禁） |

---

## 四、改进建议清单

### P0（立即，1 周内）

1. **（对应 H1/M7）JWT 验签链路替换 python-jose**：迁移至 PyJWT（活跃维护、API 近似）或 JWCrypto；若暂不动，书面风险接受 + 在 `dependency-upgrade-watchlist.md` 登记到期判据（当前 `algorithms` 显式限定已构成有效缓解，但不应长期依赖缓解项）。
2. **（对应 H2）前端覆盖率门禁抬升**：vitest `coverage.thresholds` 按"只升不降"棘轮从整改起点（lines 5/functions 2）抬升到四指标全覆盖；40% 一步到位不可行（现状 5.8%，直接设 40% 门禁必红），40% 作为补测战役（P1）目标值；`team/page.tsx`、`plugins/[pluginId]/page.tsx`、`billing/page.tsx`、`workbench/[pluginId]/page.tsx`、`oidc.ts`、`session-store.ts` 列入优先补测清单。
3. **（对应 M3）重做本地环境同步**：`uv sync --frozen --extra dev` 清除 venv 漂移（slowapi 等 4 包）；`rm -rf node_modules` 后 `npm ci` 清除幽灵目录与双 React（或以 `overrides` 收敛 react 19）。

### P1（近期，2–4 周）

4. **（对应 M1）PG 覆盖补强**：CI 加一条含 PG 的覆盖率作业（`OA_TEST_DATABASE_URL` + `--run-pg`），对 `db/repositories/*/postgres.py` 设模块级覆盖下限（如 60%），消掉"内存全绿、PG 盲跑"结构。
5. **（对应 M2）门禁口径固化**：CI 显式注入 `OA_BATCH` 并写入 README 实测口径表；或让缺失时给出 skip 而非 fail（二选一，关键是与"缺失即红"的既有设计决策保持一致并文档化）。
6. **（对应 M8/M9）SaaS 对外前置清单**：nginx 补 TLS + HSTS/X-Frame-Options/X-Content-Type-Options/CSP + limit_req；评估 token 迁 httpOnly cookie（或缩短 access token TTL + 严格 CSP nonce 化，收敛 `'unsafe-inline'`）。
7. **（对应 M4/M10）合规声明修正**：pyproject 与 docs 的许可证口径改为实测口径（列出 MPL-2.0/PSF-2.0/MIT-0/BSD-2）；MPL-2.0（certifi）随交付保留许可证文本；sharp LGPL 触发条件写入交付文档。
8. **（对应 M11/L6）基线清零战役**：按 import_direction_baseline 的既有消除计划先清 `api→db`（artifact_files.py）与 `db→上层`（audit_repo→business 反向依赖）两条最伤架构的；parity 差异给 grant 补齐 PG 侧方法或显式标注不支持。

### P2（持续）

9. **（对应 L3）SBOM 自动化**：CI 定期（或 release 时）重生成并比对 diff，过期即告警。
10. **（对应 L4/L5）依赖例行升级**：lucide-react 解除 0.x 锁死、补丁级更新（next 16.4.3 等）按月批处理；zustand/tailwindcss/date-fns 大版本升级单独立项评估。
11. **（对应 L9）凭据管理**：本地 `.env` 中真实密钥迁入密钥管理（或至少纳入轮换日历）；validate_secrets.py 已有熵值兜底，建议再加 CI 入库扫描（当前只扫工作区）。
12. **（对应 L7）k8s 目录**：要么补清单（含 securityContext/runAsNonRoot/limits），要么从 README 的目录说明中移除，避免"目录在、能力无"的误导。

---

## 五、P0 整改记录（2026-10-30 当日执行，全部完成并验证）

| 项 | 执行内容 | 验证结果 |
|---|---|---|
| P0-1（H1/M7） | python-jose → **PyJWT 2.15.1**（pyproject/uv.lock/security.py/8 处测试与探针/README 同步迁移）。uv.lock 重生成后 ecdsa/rsa/pyasn1/six 一并移除（L2 部分关闭）。**迁移中发现并修正一处语义差异**：PyJWT 在 `verify_signature=False` 时 `verify_exp` 默认放行（jose 相反），已在 [security.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/core/security.py#L150-L163) 显式置回，保住"过期令牌必拒"红线 | ruff 0 / mypy 0（204 文件）；全量回归 **2211 passed / 212 skipped / 0 failed**，覆盖率 85.87% 与迁移前持平；`pip-audit` 高危 CVE 随包移除 |
| P0-2（H2） | 覆盖率阈值按"只升不降"棘轮抬升为四指标：statements 5.5 / branches 5.5 / functions 3 / lines 5.5；40% 调整为 P1 补测战役目标并登记优先补测清单（vitest.config.mts 注释 + ci-web.yml 同步） | vitest 55/55 过，实测 5.82 / 5.73 / 3.30 / 5.96 全部达阈 |
| P0-3（M3/M5/M6） | 后端 `uv sync` 清除 venv 漂移（slowapi/limits/deprecated/wrapt 移除，M3 关闭）；前端 node_modules 全量重装，根 `package.json` 加 `overrides` 收敛 **React 19.3.1 单实例**（M5 关闭），43 个幽灵目录清零、prettier 3.3.3 锁实一致（M6 主体关闭） | `npm ls react` 全树 19.3.1 无 invalid；lock 无 18.x 残留；eslint 0 / tsc 0 / scan:* 7/7 |

**遗留说明**：M6 的 eslint@8 残留（root peer 装）经 dedupe 后已降级为非阻断项；L2 的 asyncpg license URL=UNKNOWN 与泛化标签未动（P2）；H2 的补测本体（40% 目标）属 P1 战役，不在本次范围。

---

## 六、P1 整改记录（2026-10-30 执行）

| 项 | 执行内容 | 验证结果 |
|---|---|---|
| P1-4（M1） | ci-adapter.yml 新增门禁 5b：带 PG 的覆盖率数据上单算 `db/repositories/*/postgres.py` 分项，`coverage report --fail-under=50`（保守起点，首轮 CI 实跑后校准上调，只升不降） | 本地无 PG 口径不可复跑该项；判据写入 CI 注释（与 G-05 同纪律） |
| P1-5（M2） | 口径固化落地：① `validate_batch_clock.py` 新增 C3 `--check-docs`，校验 `DOC_SOURCES` 四处同步点（根 README ×2、apps/adapter README、ci-adapter.yml、qa/phase4-verify.sh ×3）的批次号必须一致，漏改即红并列出文件:行号；② 自检扩为五类负样本（落后/格式/豁免/**文档漂移**），CI 门禁 0 改带 `--check-docs` 并补自检步骤（期望退出码 1）；③ `apps/adapter/README.md` 新增 §6.1 运行口径表（无 PG / 有 PG / 前端三套，标注权威口径与"缺失即红"）；④ 根 README 口径表数字更新为最新实测（本机 2211/212/85.87%，含 PG 行标注待 CI 复跑） | `--check-docs` exit 0（B21 全线一致）；`--self-test` 退出码 1（五类负样本全命中）；deploy/ ruff + format 全绿 |
| P1-6（M8/M9） | nginx 硬化：`nginx.conf` 加 `limit_req_zone`（10r/s），`lite.conf` 加安全响应头（nosniff/X-Frame-Options DENY/Referrer-Policy，含 add_header 不继承陷阱的逐点重复）+ `/api/` 限流（burst 20）；新增 `deploy/nginx/lite-tls.conf` TLS 变体（TLS1.2/1.3、HSTS 半年、80→443 跳转，证书挂载不入库）；CSP 留在 Next.js 单点。token 存储与 CSP 收敛评估落档 [token-storage-assessment.md](file:///c:/Users/15864/Desktop/麦子/docs/security/token-storage-assessment.md)（短期 CSP nonce 化 + TTL；SaaS 公网上线前必须决策迁 httpOnly cookie） | 配置语法人工复核（Windows 无 nginx 二进制，`nginx -t` 待 Linux/CI 首跑） |
| P1-7（M4/M10） | 许可证口径对齐 4 处：`architecture-v1.0.md` §8.1（依赖表 python-jose→pyjwt 行 + 结语改实测口径 + 8.1.1 补 H1/M7 收口注记）、§8.2（补 sharp LGPL 处置说明）、`pyproject.toml` 注释、根 `README.md` §七、`docs/api/README.md`。MPL-2.0 按灰名单政策注明"随交付保留许可证文本"；sharp LGPL 注明 `--omit=optional` 排除与分发触发条件 | 文档修订基于 pip-licenses 实测清单 |
| P1-8（M11/L6） | ① `api→db` 首条收口：新增 [artifact_service.py](file:///c:/Users/15864/Desktop/麦子/apps/adapter/app/business/artifact_service.py) 业务层查询口，`artifact_files.py` 改走 api→business→db 合法链路，import 基线行删除（24→23 条）；② grant parity 3 条收口：核查为测试模式专属写口（app/ 内零调用点），按校验器原生 TEST_HOOKS 机制显式归类，parity 基线清零；③ 审计初稿"db→上层 audit_repo 反向 import business"**已过期**（切片 1 早已修复），报告已更正 | `validate_import_directions.py`：基线内 23 / 基线外 0 / stale 0 全绿；`validate_repository_parity.py`：0 判定 / 0 stale 全绿；ruff/mypy 全绿（205 文件）；产物链路定向 69 过 |
| H2 补测第一批 | 子代理并行完成：`format.ts`/`oidc.ts`/`session-store.ts` 补 93 个用例（三文件行覆盖 98~100%），全局覆盖率 5.82%→**13.44%**（Stmts），阈值按棘轮抬升至 12.9/12.3/6.1/12.9 | vitest 148/148 过（原 55 全保留）；eslint/tsc/prettier 全绿 |

**P1 遗留**：P1-4 的 50% 下限待 CI 首跑校准；nginx 新配置待 `nginx -t`/容器首跑验证；M9 的 httpOnly cookie 迁移为 SaaS 公网上线前的强制决策项（见评估文档）；import 基线余 23 条按 due_batch（B23~B27）排期收口。

---

## 七、P2 核查与整改记录（2026-10-30）

| 项 | 结论 |
|---|---|
| L3 SBOM | **复核关闭**：`sbom.yml` 已有每周一 03:00 UTC 定时重算（`cron: '0 3 * * 1'`）+ 四层 `bom-ref` 断言 + license-scan；SBOM 产物不入 git，故不存在"提交产物过期"问题。初稿表述已更正 |
| L4/L5 前端依赖 | 补丁级已升级：`next 16.4.2→16.4.3`、`@tanstack/react-query 5.104.5→5.105.0`、`autoprefixer 10.6.1→10.6.2`、`eslint 10.13.0→10.14.0`（均在 caret 范围内）。`eslint-config-next` 停留 16.4.2（npm 锁条目未随新范围重解析，仅开发期 lint 预设、不影响运行时，下次锁重建自然上移）。大版本（lucide 1.x / zustand 5 / tailwindcss 4 / date-fns 4）按 W-4「不单独行动」原则保留。**验证**：lint 0 / tsc 0 / vitest 148 过达新阈值 / `next build` 成功（20 条路由正常生成） |
| L7 k8s | README 已明确标注「验收范围外、占位目录、manifests 均未创建」，**无文档误导**；不作代码改动，能力兑现挂排期（属产品决策而非审计缺陷） |
| L9 凭据 | `validate_secrets.py` 已在 `dependency-scan.yml:162` 接线且带自检；本地 `deploy/.env` 明文属运维侧凭据管理事项（文件未入库），不新增机制 |
| L1 pip | 工具链 CVE（CVE-2026-13346），非仓库可修复项；CI 使用 `setup-python` 自带 pip，随 runner 镜像升级自然消解 |
| 观察清单同步 | `docs/dependency-upgrade-watchlist.md`：W-2（python-jose）/ W-3（ecdsa）标记闭环，补两条 2026-10-30 维护记录 |

*报告由 2026-10-30 全仓审计生成；实测命令：`pytest --cov=app`、`ruff check`、`mypy`、`vitest run --coverage`、`eslint`、`tsc --noEmit`、`pip-audit`、`npm audit`、`pip-licenses`、`license-checker`、`npm outdated`、SBOM 抽验。P0 整改已于同日执行完毕（见 §五），P1 整改同日执行（见 §六），P2 核查同日完成（见 §七）。*
