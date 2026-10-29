# 麦子（MOY）V1.0 第二轮全仓库审计 · 汇总报告

- **审计日期**：2026-10-28（第二轮，独立复审）
- **审计基线**：`main` @ `19c43a7`（工作区干净）
- **组织方式**：主理人齐活林编排；架构师高见远（架构/依赖许可）、工程师寇豆码（代码质量/安全/性能）、QA 严过关（测试覆盖）独立产出；主理人交叉复核、去重合并、定级裁决
- **审计性质**：**复审 + 新发现**。第一轮 29 条台账已于当日修复入库，本轮任务是验证修复是否到位、是否引入回归，并独立发现新问题
- **方法**：全程只读静态审读 + 仓库自带门禁实跑 + 最小复现 + 部分测试实跑。未修改任何源码/配置/锁文件
- **编号**：`R2-ARC-xx`（架构/依赖）、`R2-ENG-xx`（代码/安全/性能）、`R2-QA-xx`（测试）；与第一轮 `H-x`/`ARC-xx`/`ENG-xx`/`QA-xx` 严格区分

## 局限（必读）

1. **CI 本月未实跑**（GitHub Actions 额度耗尽，用户侧已知）。所有「CI 会红/不会红」结论属据规则推演，已落到可复核的正则与退出码上。
2. **测试维度已完成双路实跑**：QA 带 PG 全量（2366 passed / 0 skipped / 92.21%，含 210 条 PG 红线）为主口径，主理人无 PG 分段实跑为对照。命令、耗时、证据工件路径全部记录在 §7。
3. 未启动容器、未做全栈重建冒烟。镜像内行为属静态推断 + 最小复现（临时目录，未改动仓库）。
4. Python 三方包许可证未逐项机读（`uv.lock` 不含 license 元数据），未取证处标「待验证」。

---

## 一、TL;DR

**这个仓库的工程质量显著高于同类项目，但「验证」这一环存在结构性空洞，导致 3 条高危在修复后 7.5 小时内零人发现。**

三条主线结论：

1. **纪律是真的，不是写在文档里的**。适配层唯一出口（`httpx` 仅 2 处 import）、41 张业务表 40 张 RLS 覆盖、三方契约 0 差异、分层门禁 24 条违规全在基线内且基线今日零改动——这些都有实跑证据。**没有 SQL 注入、命令注入、路径穿越、SSRF 暴露面**，多租户三道防线完整（`set_config(...,TRUE)` 事务级，连接归还池不串租户）。

2. **「构建」和「验证」两条链都是断的**。
   - **构建链**：CI 从不构建镜像、`validate_deploy.py` 无 named-context 规则、正在跑的容器用的是 6 天前旧镜像 → H-3 的修复**引入了回归**（三套编排的 web 镜像全部构建不出来），而**所有观测面都显示正常**（`oa-web`/`oal-web` 都 healthy）。
   - **验证链**：适配层 CI 在 `uv sync` 后调用裸 `ruff`/`mypy`/`pytest`，`.venv` 未进 PATH → **额度恢复后第一次 CI 必然在第 1 个 step 全红**；主线 e2e 明写"暂不进 CI"；前端覆盖率分母只有 12%。
   → 结论：**2366 条测试一条都跑不到，而所有本地指标都好看。**

3. **第一轮修复有 5 条未修净、1 条引入回归、1 条形式交付**。最典型的是「注释宣称已修，代码没有」这一形态（R2-ENG-01/04/05/18），比"没写"更危险——它会让下一轮审计直接跳过。

**分级**：高危 **6** / 中危 **22** / 低危 **25** = **53 条**（去重后）。
**今日修复复审**：已修好 **10** / 未修净 **5** / 部分 **2** / 引入回归 **1** / 未达成 **1**。

**测试实跑权威数字**（QA 单次运行同时产出 junit + coverage，23:09:58–23:33:52）：
```
OA_BATCH=B21 OA_TEST_DATABASE_URL=…moy-qa-pg:55433
pytest tests --run-pg -q --cov=app --cov-branch
→ 2366 collected = 2366 passed / 0 failed / 0 errors / 0 skipped
→ exit 0，1424.06s（23m44s），覆盖率合计 92.21%（20715/22466）
→ 210 条 PG 用例全部真跑（含 tenant_isolation 15 / conversation_rls_pg 10 / attack_cross_tenant 1）
```
**这是本仓库最强的一处资产**：全绿、零 skip、红线真跑、变异 3/3 命中。问题不在测试能力，在**验证链没接上**。

**本轮的三个「最」**：
- 最严重：H-1 三套编排的 web 镜像全部构建不出来，且已坏 7.5 小时（旧镜像掩盖）
- 最隐蔽：H-4 适配层 CI 在 `uv sync` 后调用裸 `ruff`/`mypy`/`pytest`，`.venv` 未进 PATH → **额度恢复后第一次 CI 必然全红**
- 最正面：变异测试 **3/3 命中**——测试本身真能抓回归，问题不在能力而在「刹车没接上」

---

## 二、问题总览

### 2.1 按分报告来源（可逐条核对）

| 来源 | 覆盖维度 | 高 | 中 | 低 | 小计 |
|---|---|---|---|---|---|
| 架构师（R2-ARC） | ①架构目录 ⑤依赖许可 | 3 | 6 | 7 | **16** |
| 工程师（R2-ENG） | ②代码质量 ③安全 ④性能 | 0 | 11 | 10 | **21** |
| QA（R2-QA） | ⑥测试覆盖 验证体系 | 3 | 5 | 8 | **16** |
| **合计** | | **6** | **22** | **25** | **53** |

> 架构师原始 17 条，其中 R2-ARC-05（限流空接线）与工程师 R2-ENG-01 为同一根因，已合并入后者，故计 16 条。

### 2.2 按用户要求的六维度（主维度归属，跨维度条目计入首个标注维度）

| 维度 | 高 | 中 | 低 | 小计 |
|---|---|---|---|---|
| ① 架构与目录结构 | 0 | 3 | 4 | 7 |
| ② 代码质量与规范一致性 | 0 | 4 | 6 | 10 |
| ③ 安全漏洞 | 0 | 5 | 3 | 8 |
| ④ 性能隐患 | 0 | 4 | 2 | 6 |
| ⑤ 依赖版本与许可 | 3 | 3 | 4 | 10 |
| ⑥ 测试覆盖情况 | 3 | 3 | 6 | 12 |
| **合计** | **6** | **22** | **25** | **53** |

> 两条口径的小计差异来自跨维度条目的重复归属（如 R2-ARC-12 同时属①⑥、R2-ENG-01 同时属③④）——**总数 53 是一致且不重复的**，分维度为概览口径。

### 2.3 本轮三个「最」

- **最严重**：H-1 三套编排的 web 镜像全部构建不出来，且已坏 7.5 小时（旧镜像掩盖）
- **最隐蔽**：H-4 适配层 CI 在 `uv sync` 后调用裸 `ruff`/`mypy`/`pytest`，`.venv` 未进 PATH → **额度恢复后第一次 CI 必然全红**
- **最正面**：变异测试 **3/3 命中**——测试本身真能抓回归，问题不在能力而在「刹车没接上」

---

## 三、高危问题（6 条，建议本周内处理）

### H-1 · R2-ARC-01：三套编排的 web 镜像**全部构建不出来**，且已坏 7.5 小时零人发现

**这是本轮最严重的一条，也是唯一一条「修复引入的回归」。**

失败分三个阶段，三套编排各不相同：

| 编排 | 失败位置 | 表现 |
|---|---|---|
| 全量 `docker-compose.yml:616-628` | `npm ci`（Dockerfile:25） | `Conflicting peer dependency: @types/node@26.6.5 / peerOptional from vitest@5.0.5`，exit 1 |
| lite `docker-compose.lite.yml:472-475` | Dockerfile **解析阶段**（:24） | `invalid from flag value lockroot: context "lockroot" not found` |
| saas `docker-compose.saas.yml:397-400` | 同 lite | 同 lite |

**根因**：H-3 的修复方向正确（`additional_contexts: lockroot` + `COPY --from=lockroot` + 删 `npm install` 回退），但
1. **只补了一套编排**——lite/saas 的 `build` 段完全没有 `additional_contexts`，而 Dockerfile 硬依赖 `--from=lockroot`；
2. 全量栈的 context 组织与 workspaces 锁不匹配：`COPY package.json` 取的是 workspace 子包 `@officeai/web`，`package-lock.json` 取的是仓库根（根条目 `officeai` + `workspaces:["apps/*"]`），`npm ci` 校验失败。

**为什么 7.5 小时没人发现（四重静默叠加）**：

```
officeai/web:1.0.0      Created: 2026-10-22T14:52:13Z   ← 6 天前的镜像
officeai/adapter:1.0.0  Created: 2026-10-22T14:50:54Z
H-3 修复提交 e22edf8    = 2026-10-28 15:51:22 +0800
成稿时刻                = 2026-10-28 23:xx
```

1. 旧镜像存在且 healthy → `docker compose up -d` **不触发 rebuild**
2. CI 零镜像构建（R2-ARC-12）→ 无自动发现
3. `validate_deploy.py` 无 named-context 规则 → 静态门禁发现不了
4. 执行记录明写「全栈重建冒烟未执行」→ 人工验证被明确跳过

**真实定性**：不是「构建可能失败」，是「**构建已经坏了，而所有观测面都显示正常**」。当前三套栈之所以健康，是因为跑的是 10-22 的旧镜像。**任何新环境部署、任何 `--build`、任何客户交付都会立即失败。**

**修复要点（三条缺一不可）**：
1. 三套编排统一补 `additional_contexts.lockroot: ..`，并做成「漏配即红」的门禁（见 R2-ARC-12 的 R4 规则）
2. `npm ci` 冲突必须从 **context 组织**上解决：`context: ..` + `dockerfile: apps/web/Dockerfile`（让 workspaces 结构对 npm ci 可见），或在 deps 阶段同时 COPY 根 `package.json` 并还原 `/app/apps/web/` 布局。**只补 lock 文件无用**
3. **逐套**做真实 `docker build` 冒烟——只冒全量栈发现不了 lite/saas 的缺失

**验收标准**：`docker image inspect officeai/web` 的 `Created` 时间必须**晚于修复提交时间**。用产物时间戳证明真的重建过，而不是靠"我改完了"。

---

### H-2 · R2-ARC-02：适配层生产镜像不消费 `uv.lock`，H-2 只修了测试侧

**证据**：`apps/adapter/Dockerfile:30-35`——只 `COPY pyproject.toml README.md` + `app/`，**未 COPY `uv.lock`**；`pip install --upgrade pip` + `pip install .`（现场解析）；`pyproject.toml:2` 的 `requires=["setuptools>=68","wheel"]` 无上界。

**对照**：CI 用 `uv sync --frozen`（`ci-adapter.yml:112`）、SCA 用 `uv export --frozen`（`dependency-scan.yml:216-221`）——**CI 验的是 lock 树，交付的是"构建当天 PyPI 最新匹配版本"的树**。

**影响**：H-2 的目标是「测试与生产同一套依赖组合」，现在只完成了测试侧。`sqlalchemy>=2.0,<3.0` 今天解析 2.1.6、明天可能是 2.2.x；`pip`/`setuptools` 同样浮动。**第一轮认定的"可复现性链条断裂"在生产侧仍然成立。**

**修复**：镜像构建改 `uv sync --frozen --no-dev --no-install-project` + `uv pip install --no-deps .`（或 `uv export --frozen > constraints.txt` 后 `pip install -c constraints.txt .`）；删 `--upgrade pip`；`build-system.requires` 加上界。

**验收**：镜像内 `pip freeze` 与 `uv.lock` 逐包版本一致；`docker image inspect officeai/adapter` 的 `Created` 晚于修复提交。

---

### H-3 · R2-ARC-03：LGPL-3.0 的 libvips 随 sharp 进入前端**生产**依赖树，与自有许可政策冲突

**硬证据**：`package-lock.json:1544` 明写 `"license": "LGPL-3.0-or-later"`；`@img/sharp-libvips-*` 共 10 个平台包，均为 `optional: true`；`sharp@0.35.5` 是 `next@16.4.2` 的 optionalDependency。

**为什么本机没看见**：本机是 Windows，只装了 `sharp-win32-x64`；但生产镜像是 `node:22-alpine`，构建时会拉 `sharp-libvips-linuxmusl-*`——**LGPL 二进制会真实进入生产镜像**，且以 Node 原生模块形式**进程内加载**。

**为什么这是问题**：项目自己的政策是「LGPL 灰名单仅可独立进程」（`docs/architecture-v1.0.md:1460` 甚至声称「全量 MIT/Apache/ISC，无传染性协议」——**这句话是假的**）；`dependency-scan.yml:266` 的黑名单 `\bL?GPL` 会命中 LGPL，CI 一恢复很可能直接判黑 exit 1。

**低成本修复**：全仓 `next/image` **零使用**（`grep -rn "next/image" apps/web/src` 无命中），`npm ci --omit=optional` 即可消除。同步修正文档 §8.2 的「全量」表述。

**替代方案**：保留但登记灰名单豁免（动态链接、未修改、可替换、随镜像提供 LGPL 文本与源码获取指引）；或将来真用 image 优化时改用独立进程的图像服务。

---

### H-4 · R2-QA-01：适配层 CI 在 `uv sync` 后调用裸 `ruff`/`mypy`/`pytest`，`.venv` 未进 PATH → **首次实跑必红**

**证据链**：
```yaml
# ci-adapter.yml:106-112
- uses: astral-sh/setup-uv@v5
  with: { enable-cache: true, cache-dependency-glob: apps/adapter/uv.lock }
- run: uv sync --frozen --extra dev
# :172  ruff check . --output-format=github      ← 裸命令
# :207  mypy app                                 ← 裸命令
# :460  pytest tests -q --run-pg ...             ← 裸命令
```

`astral-sh/setup-uv` 的官方 README 明确：该 action **只把 `uv` 本身加入 PATH**，`activate-environment` **默认 `false`**。`uv sync` 把依赖装进 `apps/adapter/.venv`，**不会**把 `.venv/Scripts|bin` 加进 PATH。

**影响**：`ruff` / `mypy` / `pytest` 三条裸命令在 GitHub runner 上会 `command not found` → **适配层门禁在第 1 个 step 就红**，后面 20 余道门禁 + 2366 条用例 + 210 条 PG 红线**一步都跑不到**。

**为什么这条定高**：它与「CI 恒绿」在红线保护上是**等价失效**。第一轮 H-4 说「测试红线停摆」，修复动作是「恢复 CI 实跑」；但如果 PATH 问题不修，恢复额度也只会得到一条永远红的流水线——运维直觉会去调低门槛或加 `--ignore`，**这正好拆掉红线**。

**修复（三选一，一行）**：
1. 全部改 `uv run --frozen ruff/mypy/pytest ...`
2. `setup-uv` 加 `activate-environment: true`
3. `echo "$PWD/.venv/bin" >> "$GITHUB_PATH"`

**验收**：`workflow_dispatch` 实跑一次，`ruff` step 必须真的执行到（而非 `command not found`）。**标注：静态判断，待远端验证**（本月无额度）。

---

### H-5 · R2-QA-02：贯穿旅程 e2e 在任何自动化里都不执行，且缺凭据时静默 skip

**证据**：
- `ci-web.yml:86` 明写「E2E 需要浏览器与运行中的适配层，**暂不进 CI**」
- 本轮 `npx playwright test` 实跑 = **5 passed / 1 skipped**，被 skip 的正是 `workflow.spec.ts:45`（`describe` 级 `test.skip(!OA_E2E_USERNAME || !OA_E2E_PASSWORD)`）
- 5 条通过的全是「匿名跳转 / 品牌文案」

**影响**：`登录 → 对话 → 知识库 → 工作台 → 插件安装` 这条主链路**零浏览器级回归防线**。第一轮 QA-04 的诉求（补贯穿旅程）**形式上交付、实质上未生效**——而且没人会知道，因为它不在 CI 里、本地缺凭据时静默 skip。

**修复**：
1. 把 e2e 挂进 CI（`next dev` + 适配层 stub 或真栈，用 Playwright 的 `webServer` 数组同时起两个）
2. **无凭据时改为 fail 而不是 skip**（或在 CI 里强制要求 `OA_E2E_*`）——"缺凭据自动 skip"正是"等于没测"的成因
3. 若暂时拿不到真实账号，用本地已有的 `oa-keycloak` 起 mock IdP，不要让 skip 继续冒充通过

---

### H-6 · R2-QA-03：前端覆盖率门禁是「分母受限」的假高，44% 对整站实际约 5%

**证据**：
- `vitest.config.mts` 未设 `coverage.all`（v8 默认 `all: false`，**只统计被 import 到的文件**）
- 实测报告只含 **11 个文件 / 2882 行**；而 `src` 下非测试 `.ts/.tsx` 共 **113 个文件 / 23240 行**
- 其中 **25 个 page/layout 一个都没进分母**
- `44.08%` 对整站有效行覆盖约 **5%**
- 阈值只卡 `lines: 44`，实测 44.08 → **余量 0.08pp**
- `Statements 42.82 / Branches 49.57 / Functions 22.47` 全不设卡

**影响**：门禁给人「前端有 44% 覆盖」的错觉；`Functions 22.47%` 说明大部分函数从没被调用过。任何一次小改动都可能把 44.08 压到 43.9 而**假红**，或通过 `/* v8 ignore */` 轻易抬高而**假绿**。

**修复**：
1. `coverage.all: true` + `include: ['src/**/*.{ts,tsx}']`，让分母等于全部源码
2. 阈值按实测重设，并同步卡 `functions`
3. 分母扩大后真实值会大幅下降——**应作为整改起点，不是退回旧阈值**

---

## 四、中危问题（22 条）

| 编号 | 维度 | 标题 | 关键证据 | 影响与建议 |
|---|---|---|---|---|
| **R2-ENG-01** | ③④ | **幂等/限流 Redis 后端零装配点，配置说谎且告警被绕过** | `IdempotencyManager()` 无参（`core/idempotency.py:563`）；`RedisIdempotencyStore(`/`RedisRateLimitStore(` 全仓**只在 tests/ 出现 7 次**；`config.py:356` 有 `idempotency_backend` 但无 `ratelimit_backend`；`deploy/.env.example:185` 写 `=redis`；`main.py:307` 告警仅在 `=memory` 时触发 | 单副本下功能有效；**扩容即静默失效**（幂等失效、限流放大 N 倍、零日志）。且 `deploy/k8s/README.md:6,18-19` 明写 HPA 下限 2 副本、`config.py:172` 容量公式按副本数推导——多副本是**被设计过只是没交付**的形态。修复见 §5.1，**必须先补 Redis 超时再接后端** |
| **R2-ENG-02** | ③ | 支付回调验签未覆盖 `trade_state`，可重放为已付款 | `payment_gateway.py:120-134` 的 `signing_payload` 只签 `(order, txn, amount, notify_id)`；而「是否已付」由 `is_success = trade_state in PAID_TRADE_STATES`（`:114-117`）决定 | 拿到一次合法回调即可改写 `trade_state` 重放。且实为自研 HMAC 非微信 APIv3。建议把 `trade_state` 纳入签名载荷（并考虑时间戳/nonce 防重放） |
| **R2-ENG-03** | ③ | squid 出站白名单**未实现**：`http_access allow localnet` 放行全部公网 | `deploy/squid.conf:47-49`：`deny deny_localnet` → `allow localnet` → `deny all`。`localnet` 是**源地址** ACL，不是目标白名单；另缺 `::ffff:0:0/96` | 防 SSRF 打内网成立（目标内网黑名单有效），但「私有化禁外网」的设计意图**没有实现**。需补目标域名白名单（`.allowed_domains` + `dstdomain` ACL） |
| **R2-ENG-04** | ②③ | 日志脱敏 `redact()/is_sensitive()` 是死代码 | `core/logging.py:58-87` 未进 structlog processor 链、零调用、零测试 | 当前无实际泄漏（9 处敏感词 logger 调用均只打键名），但**防线存在感是假的**。要么接入 processor 链并补测试，要么删除 |
| **R2-ENG-05** | ③ | 判权双入口，`PolicyService.require_point` 不写拒绝审计 | 19 处用 `require_point`（覆盖 billing）vs `core/security.require_permission`，两套实现；前者不写拒绝审计、不参与延迟判权白名单 | 安全事件审计有盲区。建议统一到单一入口，或让 `require_point` 委托给 `require_permission` |
| **R2-ENG-06** | ③ | 无 CSP / HSTS / Permissions-Policy | `next.config.js` 仅 3 个安全头；`nginx` 无 `add_header` | 叠加 QA-08 的 localStorage token，XSS 后果被放大。建议至少补 CSP（`script-src 'self'`）+ HSTS |
| **R2-ENG-07** | ④ | docx/md/csv 渲染在事件循环上同步跑 | `task/runner.py:1170`（async def 内）、`action_gateway.py:331` | 单次大文档渲染阻塞**全进程**，含 `/healthz` → 健康检查超时 → 容器被杀。建议 `asyncio.to_thread` 或 `run_in_executor` |
| **R2-ENG-08** | ④ | 每次生成把额度快照算两遍 | `model_service.py:603` + `:614`；`usage_service.py:610/826` | 首 token 前约 10 次多余 DB 往返，直接影响对话首字延迟。建议单次请求内复用快照 |
| **R2-ENG-09** | ④ | 两处进程内缓存无上限 | `policy_service._cache`（键含租户×用户）、`plugin_grant_service._cache` | 常驻内存随用户数线性增长，长跑进程会 OOM。建议加 LRU + 上限 |
| **R2-ENG-10** | ④ | 任务批次串行执行但租约一次性下发，跨副本可重复执行 | `job_runner.py:222` vs `job_repo.py:574`；`batch5×60s > lease120s` | 导出/抽取类任务可能被两个副本同时执行。建议租约按单条任务申请，或续租 |
| **R2-ENG-11** | ③ | adapter `:8000` 绑 `0.0.0.0` + `/metrics` 免鉴权 | `error_handler.py:46-59` | 绕过 nginx 直读业务指标（标签低基数，不泄租户，故定中而非高）。建议绑内网或给 `/metrics` 加内网限制 |
| **R2-ARC-04** | ① | 架构基线文档版本口径漂移 | `architecture-v1.0.md:186,187,1438,1700` 仍写 Valkey **7.2** / SeaweedFS **3.80**；实际 `images.lock` 是 **9.2** / **4.48** | 基线文档被声明为「架构与接口契约基线」，实现已越 2 个大版本却无记录。建议抽成单一真源表格，由 `validate_images.py` 反向校验文档 |
| **R2-ARC-06** | ⑤ | SCA 黑名单缺 fair-code 类许可 | `dependency-scan.yml:266` 只覆盖 GPL 族 + SSPL；BUSL / PolyForm / Commons Clause / Elastic / RSAL / CC-BY-NC **零覆盖** | 产业界近三年「源码可用」转让的组件（HashiCorp→BUSL、Elastic→Elastic-2.0）漏检即放行。建议补正则 + 样本自检 |
| **R2-ARC-07** | ① | 分层门禁只查跨层、不查同层环；实测 3 组文件级循环 | AST 实测：`core.security↔policy_service`（登记债的真实闭环）、`subscription↔order`、`job_runner↔tasks`，后两组靠函数内 import 掩盖 | 同层环对 import 顺序敏感，本地全绿、特定启动顺序炸。建议门禁加 SCC 检测（Tarjan 约 10 行）+ 基线只许减不许增 |
| **R2-ARC-12** | ①⑥ | **CI 零镜像构建 + 无 named-context 跨文件规则（两道缺失防线的合并根因）** | `grep "docker build\|buildx" .github/workflows/*.yml` → 无命中；`validate_deploy.py` 只有 R1/R2/R3 三条判据，`grep "additional_contexts\|COPY --from"` → 零命中 | 本轮两条高危（H-1/H-2）与旧 H-3 全是 Dockerfile 层，**CI 永远绿**。修复：① 新增 `docker-build-smoke` job（`buildx build --target deps apps/web` + `docker build apps/adapter`）；② 给 `validate_deploy.py` 补 R4：解析 `COPY --from=<name>` ↔ 各编排 `additional_contexts` 键集合比对。**现成范式就在隔壁**：`validate_config_names.py` 的 G-36 已在做同类比对（遍历 4 个编排 × 解析 Dockerfile ARG），抄它的遍历逻辑即可，工作量以小时计 |
| **R2-ARC-14** | ⑤ | 「精确标签」策略只写在注释，门禁只拦 `latest/main/master` | `validate_images.py:347`：`if tag in {"latest","main","master"}`；实际 `pgvector:pg18`、`nginx:1.29-alpine`（1.29=**mainline 奇数分支**）全部通过 | `pg18` 随 PG 18.x 滚动 → 锁文件记的 digest 与 `compose up` 拉到的实物不再对应。现成答案在 `lite-container-baseline.md:263 (L-2)`：`0.8.7-pg18-bookworm`。建议门禁加精确标签正则 |
| **R2-ARC-15** | ⑤ | `ubuntu/squid:6.13-25.04_beta` 把 beta + Ubuntu interim 底座用于生产 SSRF 出站口 | `images.lock` squid 条目；`docker-compose.yml:413-414`；被 dify-api/sandbox/plugin_daemon 经 `SSRF_PROXY_HTTP(S)_URL` 统一使用 | Ubuntu 25.04 是 interim（标准支持约 9 个月）+ `_beta` 标签不承诺修复 → **Dify 全家的出站口跑在无补丁承诺的底座上**。此条 2026-10-22 审计 `P2-7` 已登记、至今未闭环。建议自建 3 行 Dockerfile（`FROM ubuntu:24.04` LTS + `apt install squid`） |
| **R2-QA-04** | ⑥ | 执行记录对 2349→2366 的归因**事实错误** | 逐 nodeid 对账：差 17 = `test_credential_token_failure_branches.py`(14) + `test_rag_upload_streaming.py`(3)，两文件 `mtime=17:39`、来自 `b538a64`/`26220ee`；全仓 `collect_ignore/importorskip` **0 命中**（不存在条件收集机制）；`--run-pg` 不改变收集数（实测 2366=2366） | 执行记录写的「环境条件性收集」是**可机械证伪的错误归因**。以它解释会让后续对账永远对不上，并掩盖真实原因（用例在增长）。这类文档是下一轮审计的输入，错归因会传染。建议改为「15:20 后新增 2 文件 17 条」，并要求用例数变动必须给 nodeid diff |
| **R2-QA-05** | ⑥ | `ci-adapter` 的 `timeout-minutes: 30` 与真实耗时冲突 | `ci-adapter.yml:48` = 30min；junit 实测全量 **1427.552s = 23.8min**（无干扰）；本机实测覆盖率开销 **2.6x**（`test_endpoint_baseline` 35 条：30s→79s）；pytest 前还有 20 余道门禁 step | CI 修好 PATH 后，pytest 步骤会撞 30min 上限 → **又一次「永远红」**。而这会驱使人去调低覆盖率门槛或加 `--ignore`，正好拆掉红线。建议 pytest 拆独立 job（`timeout-minutes: 60`），或先测 Linux 真实耗时 |
| **R2-QA-06** | ③⑥ | SSRF 既无防护也无对抗测试，而 `OA_SSRF_PROXY_URL` 是**死配置** | `grep -rniE "ssrf\|169\.254\|内网" tests/` → **0 命中**；`grep -rni "ssrf" app/` → **0 命中**（`deploy/.env` 有该变量，`core/config.py` 无消费方） | 适配层**根本没读**这个配置——`oa-ssrf-proxy` 容器在跑，但业务流量不经过它。第一轮「SSRF 暂无用户可控暴露面」成立，但「暂无暴露面 ≠ 有防护」；配置项存在会让人误以为已接代理。要么删掉并登记「V1.0 无出站管控」，要么真接上 + 补对抗用例（`127.0.0.1`/`169.254.169.254`/`file://`/重定向到内网） |
| **R2-QA-07** | ③⑥ | 上传只按扩展名/声明 MIME 判型，无 magic 校验、无类型伪造用例 | `app/api/v1/rag.py:163-190` 分块限长（好）→ 判型走 `11002`；`tests/` 内无 `content_type 伪造 / magic bytes` 用例；路径穿越仅 `test_plugin_renderer.py:173` 一条 | 用户可把 `.exe` 改名 `.docx` 上传并送底座解析（下游风险，非本进程 RCE，故定中）。建议补 magic 白名单（pdf `%PDF`、docx `PK\x03\x04`+`word/`）+ 2 条对抗用例；上传文件名已不透传本地路径，穿越风险低，补 1 条断言钉住即可 |
| **R2-QA-16** | ⑥ | CI 的 `OA_BATCH: B21` 是**硬编码批次号**，与缺陷台账的 `due_batch` 无任何机械同步 | `ci-adapter.yml:88`（注释自认"批次推进时必须同步上调"）；台账 15 条里 **3 条带 `due_batch: B30`**、2 条 `B21`/`B22` 系 | **忘记上调 = 缺陷到期检查永远基于旧批次 = 到期缺陷永不报红**。这是**静默放行**型失效：门禁仍在跑、仍然绿，没人会注意。且「缺失即红」只防了"忘设"，**没防"设成旧值"**——设成 B21 永远合法。已有个现成反例：台账文件自己就在警告"字符串比较会让它永久逃检"。建议加 `validate_batch_clock.py`：`OA_BATCH` 必须 ≥ 台账 open 条目最大 `due_batch`，或把当前批次写成常量双向校验 |

---

## 五、重点条目展开

### 5.1 R2-ENG-01 的定级裁决与修复时序（本轮唯一由主理人裁决的分歧）

**分歧**：工程师定「高」（重复下单/重复扣费）、架构师定「低」（B-08 已披露单副本口径）。

**裁决：中。** 依据：

1. **三套编排零 `replicas` 配置** → compose 默认单副本；`deploy/k8s/` 仅有 README 占位（「状态：占位，S4 阶段实现」），无 Deployment/HPA manifest。→ **单副本下 `InMemoryIdempotencyStore` 能正确拦截重复 key，「重复扣费」不成立**。工程师已接受并主动撤回该影响描述。
2. **但不能定低**，因为三条文档互相矛盾：
   - `deploy/k8s/README.md:6,18-19` 明写 SaaS 生产目标「adapter Pod ×N、HPA 下限 **2** 副本」
   - `core/config.py:172` 容量公式按「副本数 ×(pool_size+max_overflow) ≤ PG max_connections×0.6」推导
   - `deploy/.env.example:185` 写 `OA_IDEMPOTENCY_BACKEND=redis`
   → **多副本是被设计过、只是没交付的形态**；而 `.env.example` 给客户的默认值声称"redis 已就绪"。文档内部互相矛盾 ≠ 已披露。
3. **告警被幻觉绕过**：`main.py:307` 的告警条件恰好是 `idempotency_backend == "memory"` → 配成 `redis` 反而让告警永久沉默。这是「配置幻觉 + 告警被幻觉绕过」的双重失效。

**修复时序（关键，必须先做第 0 步）**：

| 步 | 动作 | 理由 |
|---|---|---|
| **0** | **先给 Redis 客户端补超时**：`credential_service.py:214` 的 `redis_asyncio.from_url()` 未传 `socket_timeout`/`socket_connect_timeout` → redis-py 默认 `None` = **永久阻塞** | 接入后同一个无超时客户端会被**每次写请求**触碰。Valkey 一 hang，故障从「多副本静默失效」升级为「**全线写请求挂死**」——这是"修 01 修出来的"新风险。加 `socket_timeout=1.0, socket_connect_timeout=1.0, health_check_interval=30` + 调用点 `asyncio.wait_for` |
| 1 | 补 `ratelimit_backend` 配置项（别复用 `idempotency_backend`，它已被幂等/限流/凭据缓存三方共用、语义超载） | 建议收成单一 `OA_CACHE_BACKEND` |
| 2 | 在 `create_app` 装配点按配置实例化 `RedisIdempotencyStore`/`RedisRateLimitStore` | 当前 `IdempotencyManager()` 无参、`RateLimiter` 走默认内存 |
| 3 | **可红用例写成装配断言，不要写成 Store 单测** | `tests/unit/test_idempotency.py:174+` 已直接构造 `RedisIdempotencyStore(_FakeRedis())`——「Store 能工作」早验证过了，**漏的正是装配**。用例应为：`create_app(Settings(idempotency_backend="redis", ...))` 后断言 `app.state.rate_limiter._store` 是 `RedisRateLimitStore`。当前必红 |
| 4 | 告警改为校验**装配结果**（打印实际 store 类名），不再比配置字符串 | 未来任何装配漂移自动暴露 |
| 5 | `backend=redis` 但不可用 → **fail-fast**，不静默回落 | `credential_service.py:248-265` 现在两个 `except` 都落回进程内；幂等若照抄就是同类 bug 换个文件重演 |
| 6 | （可选）加可达性门禁：所有 `OA_*_BACKEND` 取值必须在代码中有装配读点 | 把本轮教训机械化，防复发 |

**上线门槛**：不阻塞单副本私有化交付；**阻塞任何扩容动作**（SaaS GA、`compose --scale`、k8s Deployment）。
**最低成本止血（1 行注释，今天可做）**：在 `.env.example:185` 上方加「⚠ 当前版本不生效，见 R2-ENG-01；扩容前必须先落地装配」——让默认值说真话。

### 5.2 关于「千行文件该不该拆」——本轮纠正了一个判断标准错误

第一轮把「超 1000 行」当作拆分裂据。AST 量化后（总行 / 有效代码行）：

| 文件 | 总行 | 有效代码 | docstring 占比 |
|---|---|---|---|
| `model_service.py` | 1669 | **984** | ~41% |
| `plugin_service.py` | 1452 | **901** | ~38% |
| `tenant_data_service.py` | 1352 | **816** | ~40% |
| `task/runner.py` | 1305 | **887** | ~32% |
| `usage_service.py` | 1137 | **590** | ~48% |
| `billing/postgres.py` | 1127 | **733** | — |
| `rag_service.py` | 1089 | **597** | — |
| `memory_service.py` | 1059 | **665** | — |
| `db/migrate.py` | 1054 | **560** | — |
| `core/security.py` | 956 | **432** | — |

**结论：没有该拆的上帝类。** 除 `model_service` 外均未越 1000 有效代码行，45%–68% 是「Args/Returns/Raises」式中文文档，内聚度高。**为行数而拆会拆错对象。**

真正的坏味道是 `core/security.py`——432 行里塞了 **6 类横切**（token 解析、租户解析、权限矩阵、成员校验、幂等、限流、启动自检，22 个模块级函数），且它的反向依赖制造了 **4 条分层基线债 + 1 组真实环**。

**建议拆分（P1）**：`security.py` → `authn.py`（令牌/上下文）+ `authz.py`（权限矩阵/白名单）+ `tenant_guard.py`（租户解析/成员校验），保留 `security.py` 作兼容 re-export——**一举消掉 4 条基线债与 1 组循环依赖**。

---

## 六、今日修复复审（三态判定）

| 条目 | 判定 | 可复核证据 |
|---|---|---|
| **H-1** Dify 客户端生命周期 | ✅ **已修好** | 缓存 `model_service.py:343,565-568`；流式 `try/finally: await response.aclose()` `dify_client.py:450-456`；lifespan 真调用 `main.py:359`；全仓已无「每请求新建连接池」路径。回归用例齐全 |
| **ARC-01** 双网拆分 | ✅ 已修好 | `docker-compose.yml:720-729`；实跑解析 19 服务，web 仅 frontend、adapter 桥接双网 |
| **ARC-04** 孤儿 etcd | ✅ 已修好 | 仅存 `milvus-etcd`（`:158`） |
| **ARC-07** tenacity 零引用 | ✅ 已修好 | pyproject 与 uv.lock 均无 |
| **ARC-14** Valkey 淘汰策略 | ⚠️ **部分** | 全量栈改 `volatile-lru`（`:104`）；**saas:75、lite:123 仍 `allkeys-lru`** |
| **ENG-03** SCA 正则 | ✅ 已修好（GPL 变体维度） | 前缀锚定 + 正负样本自检；**fair-code 仍缺** → R2-ARC-06 |
| **ENG-04** 镜像 digest | ✅ 已修好（构建期镜像） | 5 个 `FROM` 全 `@sha256`；`validate_images.py` 已纳 Dockerfile FROM |
| **ENG-05** 版本口径 | ✅ 已修好（文档侧） | `architecture-v1.0.md:1482` 登记 eslint ^10.13.0 |
| **QA-05** npm 漏洞扫描 | ✅ 已修好 | `dependency-scan.yml:231` `npm audit --audit-level=high`（仓库根，读 workspaces 锁，正确） |
| **QA-09** 覆盖率门禁 | ✅ 已修好 | `pyproject.toml:160-163` ≥85% 硬地板 |
| **H-2** Python 依赖锁定 | ❌ **未修净** | `uv.lock` 已入库、`uv lock --check` 通过、CI 已 `--frozen`；但**生产镜像不消费 lock**（H-2 本条）＋ 注释「7 项 ==」实际只有 6 项（ARC-07 删 tenacity 后未跟改）＋ **仍有 9 项运行时区间约束**（"全部收敛为 ==" 不属实） |
| **ARC-02** 资源限额 | ⚠️ **未修净（8/19）** | 承诺集已兑现；`object-store`、`milvus-etcd`、`prometheus`、`web` 等 11 个仍无限额 |
| **ARC-03** healthcheck | ⚠️ **未修净** | **`dify-worker-beat`、`dify-sandbox`、`ssrf_proxy` 仍无**（方案明确点名） |
| **ARC-10** 上传流式化 | ❌ **未修净** | 已做流式落盘 + 大小限制，但 `rag.py:189` 仍 `tmp.seek(0); payload = tmp.read()` **全量入内存**，峰值与修复前相同。其注释写着"内存峰值回到原水平"——**等于自己承认没降** |
| **ARC-12** OPS 分页 | ⚠️ **部分** | 硬上限 200，无真分页；已自述登记 |
| **ARC-13** 幂等/限流后端 | ❌ **未修净** | 见 R2-ENG-01 |
| **QA-03** 陈旧工件 | ✅ **已修好（口径层面）** | `git check-ignore -v` 三条全命中（`.gitignore:50/51/55`），`git ls-files` 不含。**但实体仍在盘上**（2.6MB，mtime 今日），是本次运行产物而非陈旧件。建议台账改记「已 gitignore；实体为运行产物，以 CI junit 为唯一口径」，因为盘上现在同时躺着第三套覆盖率数字 |
| **QA-04** 前端 e2e | 🔴 **未达成（形式交付，实质未生效）** | ① `ci-web.yml:86` 明写「E2E 需要浏览器与运行中的适配层，**暂不进 CI**」——不在任何自动化里跑；② 实跑 `npx playwright test` = **5 passed / 1 skipped**，被 skip 的正是唯一贯穿旅程 `workflow.spec.ts:45`（`test.skip(!OA_E2E_USERNAME \|\| !OA_E2E_PASSWORD)`）；③ 覆盖率门禁分母只含 11/113 文件（→ H-6）。即「登录→对话→知识库→工作台→插件」主链路**零浏览器级回归防线** |
| **H-3** web 镜像锁 | 🔴 **引入回归** | 见 H-1 |

**统计：已修好 10 / 未修净 5 / 部分 2 / 引入回归 1 / 未达成 1（共 19 条，覆盖第一轮台账核心项）。**

---

## 七、测试覆盖情况

> 本节以 QA 的带 PG 全量实跑为主口径（`OA_BATCH=B21` + `OA_TEST_DATABASE_URL` → `moy-qa-pg` / `pgvector/pgvector:pg18` @ `127.0.0.1:55433`，`officeai_app` 角色 `rolbypassrls=f`，迁移到 `0012_quota_hold`），并以主理人的无 PG 分段实跑作为对照。两轮独立执行，收集数一致。

### 7.0 全量实跑（本轮权威数字）

单次运行同时产出 junit + coverage（`23:09:58–23:33:52`）：

```
OA_BATCH=B21 OA_TEST_DATABASE_URL=…moy-qa-pg:55433 \
  python -m pytest tests --run-pg -q --cov=app --cov-branch

→ 2366 collected = 2366 passed / 0 failed / 0 errors / 0 skipped
→ exit 0，1424.06s（23m44s）
→ 覆盖率：合计 92.21%（20715/22466）· 语句 93.98% · 分支 83.66%
```

**关键点**：`-m pg` = **210 条全部真跑**，含 `tenant_isolation`(15) / `conversation_rls_pg`(10) / `attack_cross_tenant`(1)，**无一条静默退化**。

> 证据工件：`%LOCALAPPDATA%\qa_r2\`（`junit_r2.xml` / `coverage_r2.xml` / `pytest_r2.log`），仓库零改动。

### 7.0.1 租户隔离红线：26/26 全 PASSED（本轮实证）

从 `junit_r2.xml` 逐条抽取，不是汇总数：

| 用例组 | 条数 | 结果 | 关键证据（含耗时） |
|---|---|---|---|
| `test_tenant_isolation.py` | 15 | **全 PASSED** | `sec03_concurrent_twenty_requests_five_rounds` **0.96s**（20 并发×5 轮真跑）；`sec07_readyz_not_ready_with_superuser_dsn` **1.79s** / `..._ready_with_app_role_dsn` **1.69s**（真建连并区分 BYPASSRLS）；`sec02_cross_tenant_insert_is_rejected_by_with_check` |
| `test_conversation_rls_pg.py` | 10 | **全 PASSED** | — |
| `test_attack_cross_tenant.py` | 1 | **PASSED**（0.58s） | `test_cross_tenant_read_returns_zero_rows` |

**关于「默认环境整文件 skip」与「26/26 全过」的关系**（两者不矛盾，需准确表述）：
- **事实一**：无 PG 环境下，跨租户攻击测试**一行都不跑**（主理人实跑 security 目录，56 passed / 2 skipped，skip 的正是整文件）
- **事实二**：带 `--run-pg` + 可用 DSN 时，26/26 **全跑全过**（QA 本轮实证）
→ 结论：**红线测试本身是有效的，但它只在"配了真库"时才存在**。开发者跑默认单测得 1561 全绿，看不到任何租户隔离证据。

### 7.1 收集数与「2349 vs 2366」的根因（**执行记录的归因是错的**）

| 口径 | 数字 | 说明 |
|---|---|---|
| `--collect-only -q` | **2366** | 主理人与 QA 两轮独立实测一致 |
| `--collect-only -q --run-pg` | **2366** | **完全相同**——`--run-pg` 只加 skip 标记，不改变收集数 |
| 工程师 junit.xml | 2349 | `timestamp=2026-10-28T15:20:06` |
| 执行记录 §五 F-2 | 2366 | 记为「环境条件性收集」 |

**逐 nodeid 对账结果**：`only in current: 17 / only in junit: 0`

```
tests/unit/test_credential_token_failure_branches.py   14 条
tests/unit/test_rag_upload_streaming.py                 3 条
```

两文件 `mtime = 2026-10-28 17:39`，来自审计修复批次 `b538a64` / `26220ee`——**晚于** 15:20 那次全量运行。

**结论：差 17 = 15:20 之后新增了 2 个测试文件，与"环境"无关。** 且全仓 `grep pytest_ignore_collect / collect_ignore / importorskip` → **0 命中**，`--run-pg` 实测不改变收集数——即执行记录里描述的「环境条件性收集机制」**在代码里不存在**。

这条本身不是代码缺陷，但它是验证体系可信度的直接证据：**一个可被机械证伪的归因被写进了执行记录，而没有任何门禁校验文档与工件的对账。**

### 7.2 主理人分段实跑（无 PG，作对照基线）

| 层 | 结果 | 耗时 |
|---|---|---|
| 单元 `tests/unit` | **1561 passed / 0 failed / 0 skipped** | 59.4s |
| 安全 `tests/security` | **56 passed / 2 skipped** | 28.2s |
| 集成 `tests/integration` | **538 passed / 184 skipped / 1 failed** | 348.6s |

**关于集成层那 1 failed**：`test_plugin_contract_skeleton.py::test_d1_deferred_registry_matches_markers` → `RuntimeError: 未设置 OA_BATCH`（`tests/expected_xfail.py:303`）。**这是主理人未设环境变量导致的操作失误，不是代码缺陷**。CI 已设 `OA_BATCH: B21`（`ci-adapter.yml:88`），README:23-26 也明确说明该设计。**不计入问题台账。**

**关于 security 的 2 skipped**：`test_attack_cross_tenant.py` **整个文件**、`test_tenant_spoofing.py:455` 均需 `--run-pg` + DSN。QA 在带 PG 口径下确认这两组**是真实执行的**（210 条 pg 用例均在内）。所以准确表述是：**默认无 PG 环境下跨租户红线一条不跑，只有带 DSN 才跑**——这构成开发者"1561 全绿"的错觉来源。

### 7.3 SKIP 结构：静态跳过 0 条，全部为运行时条件

全仓库 `@pytest.mark.skip` / `skipif` / `xfail` = **0 处**。所有跳过都来自运行时条件：

| 来源 | 条件 | 合理性 |
|---|---|---|
| `conftest.py:404` | 未加 `--run-pg` 时给 210 条 `pg` 用例打 skip | 合理（显式开关，CI 与门禁都带 `--run-pg`） |
| `conftest.py:468` | `OA_TEST_DATABASE_URL` 缺失 | 合理 |
| `pg_fixture.py:174/194` | 应用 DSN 缺失 / 超管 DSN 无法派生 | 合理 |

**`expected_xfail` 审计：没有「把已知 bug 永久 xfail 当通过」**。`EXPECTED_XFAIL=0` / `EXPECTED_RED=0` / `EXPECTED_OPEN_CASES=0`，缺陷台账 15 条**全部 `status="fixed"`**，无 `open`。
**残余风险（低）**：`EXPECTED_XFAIL=0` 是**人工维护常量**（文件头明写"人工，禁止自动推导"）。若有人把 `xfail` 改成 `pytest.skip` 或直接删断言，`EXPECTED_XFAIL` 仍是 0 且门禁全绿——`EXPECTED_OPEN_CASES` 只堵了「xfail→red」，**没堵「xfail→删除」**。

### 7.4 变异测试：3/3 命中（本轮最强的正面证据）

在代码副本上改坏三处，跑对应目标文件：

| 变异 | 目标 | 结果 |
|---|---|---|
| M1 删掉 `dify_client.post_stream` 的 `try/finally: aclose()` | `test_model_service_client_cache.py` | 🔴 **2 failed** |
| M2 `InMemoryIdempotencyStore.get()` 恒返回 `None` | `test_idempotency.py` | 🔴 **4 failed** |
| M3 `_decode_token` 加 `options["verify_exp"]=False` | `test_attack_token.py` | 🔴 **1 failed** |

**结论：在"已写了的"路径上，这套用例是真能红的，不是纸面绿色。** H-1（客户端生命周期）、幂等、令牌过期三条红线的修复都被测试真正钉住了。

### 7.5 断言强度：这是本仓库最扎实的一处

AST 扫描 2050 个测试函数：`assert` 语句 **6241** 条（≈3.0 条/用例）、`pytest.raises` **350** 处。
**仅 1 条 `status_code` 断言**的用例只有 **7 条（0.34%）**；零 `assert` 用例 50 条，逐条抽查均为 `pytest.raises` 期望异常，**不是空断言**。

另有 `conftest.py:167` 的 `autouse` 每用例前后重置约 50 个进程级单例（含注释记录历史踩坑：`test_migrate_cli` 污染 stdout 导致其后 51 条成片红）——**顺序依赖被真正消灭了**。

### 7.6 覆盖率口径澄清（同一棵树能算出 6 个数）

| 数字 | 性质 | 可对账性 |
|---|---|---|
| **92.21%** | **本轮合计覆盖率** = `20715/22466` | ✅ **当前最新权威值**（本轮全量实跑，含 PG） |
| **93.98%** | 本轮**行/语句**覆盖率 | ✅ 同一份 XML 的 `line-rate`，**不是合计口径** |
| **83.66%** | 本轮**分支**覆盖率 | ✅ 同一份 XML 的 `branch-rate`，**无任何门槛**（README:239-241 已澄清全仓只有 `--cov-fail-under=85` 合并口径） |
| **85.55%** | 无 PG 覆盖率 | ⚠️ **本轮未测**，仅引 README §223 的 `cedd3e9` 历史值 |
| 91.95% | 上一时点合计 = `(17424+3206)/(18591+3846)` | ✅ 该时点也正确。与 92.21% 差在分母 `22437→22466`（+19 语句 / +10 分支），**两个数各自都对，不是矛盾** |
| 91.28 / 91.91 / 86.35 / 91% | 别的树或别的口径 | 均非当前树 |

**机制性根因**：① 同一份 XML 里同时有 `line-rate` / `branch-rate` / 合计三个百分比，文档随手引其中一个；② 覆盖率不是任何一次运行的"必产出口"（`--cov` 是本地手加参数，只有 CI 有 `--cov-fail-under=85`）；③ 引入 `concurrency=["greenlet","thread"]` 后历史数字**跨口径不可比**（`pyproject.toml:191-205` 已明写），但引用时几乎无人带口径。

**建议**：在 `pyproject.toml` 注释与 CI 输出固定一句「本项目覆盖率口径 =（语句覆盖 + 分支覆盖）/（语句总数 + 分支总数）」，并让 CI 同时打印三个数。

### 7.7 CI 是否真的会跑：设计合格，但执行链是断的

**设计层面无可指摘**：`OA_TEST_DATABASE_URL` 在 job env（:81）、pytest 带 `--run-pg`（:460）、`--cov-fail-under=85`（:466）、**有「带 `--run-pg` 时 skipped 即红」的断言**（:468-489）、`:128-166` 建应用角色并自检 `bypassrls=f`。

**但执行链有两处断裂**：
1. **H-4（R2-QA-01）**：`uv sync` 后的裸命令找不到可执行文件 → 第 1 个 step 就红
2. **R2-QA-05**：`timeout-minutes: 30` vs 实测 23.8min（且覆盖率有 2.6x 开销）

**失败掩盖扫描：没有人为掩盖**。`continue-on-error` **0 命中**；`|| true` 3 处全部是"先收集再判空"的 grep 与 `npm ls`，不影响判定；`if: always()` 仅用于两个 `upload-artifact`。"期望退出码 1"的自检步骤全部用 `set +e; ...; if [ "$code" -ne 1 ]; then exit 1` 显式判据——**这是正确写法**。

### 7.8 门禁脚本复验：未发现恒真门禁

14 个 `validate_*.py` + 7 个前端扫描器全部实跑：**13/14 带 `--self-test`**（注入负样本、期望 exit 1），CI 逐个断言退出码。唯一无自检的 `validate_openapi.py` **非恒真**——它读的是 `create_app().openapi()` 的真实 schema（不是 `docs/api/openapi.json`），且 G1 是精确集合相等。

**但有一条"名义存在"**：`validate_import_directions.py` 的 24 条违规 **100% 落在基线白名单内**——这条门禁今天对存量 24 处分层违规零约束力，只防新增（见 R2-QA-10）。

---

## 八、低危问题（25 条）

| 编号 | 标题 | 一句话证据 |
|---|---|---|
| R2-ARC-08 | `images.lock` digest 只被 `install.sh` 消费 | 三套编排 `grep -c "@sha256"` → **0/0/0**；走 `compose up` 的路径不受 digest 保护 |
| R2-ARC-09 | 「千行文件」无该拆的上帝类，真问题是 `security.py` 职责数 | 见 §5.2 |
| R2-ARC-10 | 3 个承诺服务仍无 healthcheck | `dify-worker-beat`/`dify-sandbox`/`ssrf_proxy`（上游镜像无健康端点，允许不补但须登记） |
| R2-ARC-11 | business 层 5 文件不带 `_service` 后缀；audit 同域两文件 | `kc_projection.py`/`answer_cap.py` 等 vs 21 个 `*_service.py` |
| R2-ARC-13 | 门禁文案与事实不符 | `dependency-scan.yml:355` 恒打印「D-06 待决」，而 D-06 已于 10-21 批准；`:241` license 清单用 `npm install` 非锁树 |
| R2-ARC-16 | `-local` 变体未记录选择理由 | 经核实**是上游官方变体**（有实测 RepoDigest 34412a22 + 2.28GB 佐证），只差 `images.lock` 补一行 note |
| R2-ARC-17 | `ignore::DeprecationWarning:httpx.*` 是 **no-op 空转配置** | 告警发出模块是 `fastapi.testclient`，正则 `httpx.*` 根本不匹配（三个实验验证）。**不是掩盖，更不是降噪——它从来不起作用，唯一效果是暗示"已处理"**。附带发现：仓库只有漏洞**扫描**，无升级**巡检**（`images.lock:16` 引用的架构文档 9.6 条款**实际不存在**，也无 dependabot/renovate） |
| R2-ENG-12 | 上传峰值未降 | 与 ARC-10 同源，`rag.py:189` |
| R2-ENG-13 | 三栈 Valkey 策略不一致 | saas:75 / lite:123 仍 `allkeys-lru` |
| R2-ENG-14 | OPS 列表静默截断 200 | 无分页、不提示 |
| R2-ENG-15 | `usage_service.py:616-621` 整段重复 | 复制粘贴痕迹 |
| R2-ENG-16 | `type: ignore` 18 处中 17 处集中在 `migrate.py` 同根因 | 不是散落债务，是单一根因 |
| R2-ENG-17 | H-1 关停路径惰性构造且异常会跳过 `dispose_engine` | H-1 的次生问题 |
| R2-ENG-18 | `ops_service._tenant_names` 注释称"批量"实为 N+1 | 注释与实现不符 |
| R2-ENG-19 | `Idempotency-Key` 无租户命名空间 | 跨租户同 key 可能互相影响 |
| R2-ENG-20 | eslint 无 type-aware 规则 | 类型相关缺陷 lint 层抓不到 |
| R2-ENG-21 | 除 Dify 外的客户端从不 `aclose` | keycloak_client 等 |
| R2-QA-08 | `--frozen` 不能证锁文件与 pyproject 同步，CI 无 `uv lock --check` | `ci-adapter.yml:112`、`dependency-scan.yml:218` 均为 `--frozen`；`grep "uv lock\|--locked" .github/workflows` → **0 命中**。`--frozen` 只保证"不更新锁"，不校验一致性（那是 `--locked` 的职责）→「改了 pyproject 忘了 lock」静默放行。H-2 的"锁定链条"少了最后一扣，成本 3ms |
| R2-QA-09 | 14 个门禁里 `validate_openapi.py` 是唯一没有 `--self-test` 的 | 其余 13 个均 ≥4 处自检。它是最重要的契约门禁，却是唯一不能机械证明"会红"的。建议注入「删 core 端点/改 method/收窄 enum」三类负样本 |
| R2-QA-10 | 分层依赖方向 24 条违规 100% 在基线白名单内 | 门禁输出「违规 24 条（基线内 24 / 基线外 0 / stale 0）」→ `[OK]`。**不是假绿**（设计就是"只许减不许增"），但对存量 24 处零约束力，读报告的人会以为"分层是干净的"。建议基线加 `due_batch` 到期机制（同 `expected_xfail.DEFECT_LEDGER`） |
| R2-QA-11 | QA-03 只做到 gitignore，实体工件仍在盘上且会再被引用 | `coverage.xml` 825,641B / `junit.xml` 300,568B / `.coverage` 1,503,232B（mtime 15:43/16:46）。不污染 git，但**任何本地脚本或后续 agent 读文件都会再引一次**——第一轮因此得 86.35%，本轮架构师又读到 93.72%。建议删除实体而非仅忽略，并约定引用覆盖率必须带口径与运行 SHA |
| R2-QA-12 | 测试串行、无并行、无超时插件 | 未装 `pytest-xdist` / `pytest-timeout`（实测空）。全量 ~24min 是单进程结果；一个挂死的 `await` 会把 CI 拖到 job 超时，而不是告诉你"哪个用例挂了"。建议 `-n auto` + `--timeout=120`（PG 用例按文件分组避免互踩） |
| R2-QA-13 | 覆盖率三口径长期混用，无一处声明 | `coverage.xml`：`line-rate=0.9372` / `branch-rate=0.8336` / 合计 91.95%。同一棵树能被"算出" 6 个不同百分比（93.72/91.95/91.91/91.28/86.35/91），**全部技术上都对、口径上都不同**。建议在 `pyproject.toml` 与 CI 输出固定一句口径定义，并让 CI 打印三个数 |
| R2-QA-14 | 双实现覆盖率不对称，PG 仓储层是洼地 | `business` / `api` 层多数 95–100%，而 PG 仓储实现仅 **66–88%**。即"内存实现测得细、数据库实现测得粗"——而生产走的是 PG 实现。建议对 `db/repositories/**/postgres.py` 单列覆盖率目标 |
| R2-QA-15 | 6 条 `asyncpg Connection._cancel` 未 await 告警长期无人治理 | 全量实跑日志中的 warning，未进任何红线。虽不致失败，但长期堆积会淹没真正的新告警（与 R2-ARC-17 的「噪音掩盖信号」同源）。建议批量修或加 `filterwarnings` 精确登记 + 跟踪项 |

---

## 九、汇总与改进建议清单

### 9.1 总体评级

| 维度 | 评级 | 一句话判断 |
|---|---|---|
| 架构与目录结构 | **B+** | 分层纪律真执行（24 条违规全在基线内、基线未腐坏）；盲区在同层循环与门禁对角线 |
| 代码质量与规范一致性 | **A-** | `raise HTTPException`=0、裸 `except`=0、`eval/exec/shell=True`=0、前端 `any`=0、`@ts-ignore`=0、`dangerouslySetInnerHTML`=0。扣分在「注释宣称已优化，代码没有」4 条 |
| 安全漏洞 | **B** | 无注入面、无路径穿越、多租户三道防线完整、`validate_auth_startup` fail-closed 闭环；扣分在支付验签范围、squid 白名单未实现、SSRF 配置是死的、无 CSP |
| 性能隐患 | **B** | 连接池/超时/索引/分页全部到位，N+1 仅 1 处且被上限兜住；扣分在热路径做重活（同步渲染、额度双算） |
| 依赖版本与许可 | **C+** | CI 侧闭环、生产侧开环；许可侧有 LGPL 红线未决策 |
| 测试覆盖情况 | **B**（能力 A、验证链 F） | 用例质量是本仓库最强资产（2366 条、变异 3/3 命中、断言得分 3.0/用例）；但**验证链三处断裂**：CI 必红、主线 e2e 不跑、前端覆盖率分母只有 12% |
| **交付就绪度** | **不可交付** | **三套编排的 web 镜像全部构建不出来**——硬阻断 |

> 「能力 A、验证链 F」是本轮最需要传达的一句话：**测试写得很好，但刹车没接上**。这比"测试写得差"更难发现，因为所有本地指标都好看。

### 9.2 P0（部署阻断 + 验证链断裂，本周内，约 3.5 人日）

> **排序原则**：先让「门禁真的跑起来」，再修会被门禁发现的问题。否则修完仍然没人验证——本轮 H-1 之死正是这个原因。

| # | 动作 | 验收标准 | 预估 |
|---|---|---|---|
| **0** | **修 H-4（R2-QA-01）+ R2-QA-05**：`uv run --frozen` 或 `activate-environment: true`；pytest 拆独立 job 并把 `timeout-minutes` 抬到 60 | `workflow_dispatch` 实跑一次，`ruff` step 必须真执行（非 `command not found`）；pytest 步骤不撞超时 | 0.5d |
| 1 | **修 H-1**：三套编排补 `additional_contexts.lockroot`；`npm ci` 冲突从 context 组织上解决（`context: ..` + `dockerfile: apps/web/Dockerfile`，或还原 workspaces 布局） | **三套编排各 `docker build` 成功一次**；`docker image inspect` 的 `Created` 晚于修复提交 | 1d |
| 2 | **修 H-3**：`npm ci --omit=optional` 消除 LGPL；同步修 `architecture-v1.0.md:1460` 的「全量 MIT/Apache/ISC」表述 | `npm ls @img/sharp-libvips-*` 为空；文档表述与实际一致 | 0.25d |
| 3 | **修 H-2**：adapter 镜像改 `uv sync --frozen`，删 `--upgrade pip`，`build-system` 加上界 | 镜像内 `pip freeze` 与 `uv.lock` 逐包一致 | 0.5d |
| 4 | **加 R2-ARC-12 防复发机制**：CI 加 `docker-build-smoke` job；`validate_deploy.py` 补 R4（`COPY --from` ↔ `additional_contexts` 比对，抄 G-36 范式） | 人为删掉某套编排的 context，门禁必须变红 | 0.5d |
| 5 | **修 H-5（R2-QA-02）**：e2e 挂进 CI；无凭据改为 **fail 而非 skip**（或用 `oa-keycloak` 起 mock IdP） | CI 里 `workflow.spec.ts` 的贯穿段必须真实执行并通过 | 0.5d |
| 6 | **修 H-6（R2-QA-03）**：`coverage.all: true` + `include: ['src/**/*.{ts,tsx}']`，阈值按新分母重设 | 分母 = 113 个源文件；报告里能看到真实（会大幅下降的）数字 | 0.25d |
| 7 | **密钥轮换 + 全栈重建冒烟**（用户侧） | `deploy/.env` / `.env.lite` 真实密钥全部轮换；补齐 5 项缺失密钥后 `up -d` | 用户执行 |

### 9.3 P1（本 Sprint，约 5 人日）

| # | 动作 | 验收标准 |
|---|---|---|
| 6 | **R2-ENG-01**：先补 Redis 超时 → 补 `ratelimit_backend` → 装配 → **装配断言用例** → 告警校验装配结果 → 不可用即 fail-fast；`.env.example:185` 加警示注释 | 断言用例当前必红、修复后必绿；`app.state.rate_limiter._store` 类型符合配置 |
| 7 | **R2-ENG-02**：`trade_state` 纳入签名载荷 + 时间戳/nonce 防重放 | 构造「合法三元组 + 篡改 trade_state」的回调必须验签失败 |
| 8 | **R2-ARC-15**：squid 换 `FROM ubuntu:24.04` + `apt install squid`（或非 beta 稳定标签） | `images.lock` tag 不含 `beta`/interim 版本号 |
| 9 | **R2-ARC-14**：`pgvector` 改 `0.8.7-pg18-bookworm`；nginx 改 `1.28-alpine`（stable）；**门禁加精确标签正则** | 把 tag 改成 `pg18` 门禁必须红 |
| 10 | **R2-ENG-03**：squid 补目标域名白名单 | 私有化模式下访问任意外网必须被拒 |
| 11 | **R2-ENG-07/08**：渲染移出事件循环；额度快照单请求内复用 | 大文档渲染期间 `/healthz` 仍 <100ms；首 token 前 DB 往返次数下降 |
| 12 | **R2-ARC-06**：SCA 补 fair-code 正则 + 正负样本 | 构造 `BUSL-1.1` 依赖必须判黑 |
| 13 | **拆分 `core/security.py`** → authn/authz/tenant_guard | 分层门禁违规数下降 4 条；`core.security↔policy_service` 环消失 |
| 14 | **R2-QA-08**：CI 加 `uv lock --check`（或把 `--frozen` 换 `--locked`）；**R2-QA-06**：`OA_SSRF_PROXY_URL` 要么删掉并登记「V1.0 无出站管控」，要么真接 squid + 补对抗用例（`127.0.0.1`/`169.254.169.254`/`file://`/重定向）；**R2-QA-07**：补 magic bytes 白名单 + 2 条类型伪造用例 | `uv lock --check` 进 CI；SSRF 配置项有消费方或已删除；改名 `.exe→.docx` 上传被拒 | 0.5d |

### 9.4 P2（排期）

- R2-ARC-04（基线文档版本同步，建议机制化：由 `validate_images.py` 反向校验文档）
- R2-ARC-07（门禁同层 SCC 检测 + 基线只许减）
- R2-ARC-08（digest 落到编排，或收敛为 `install.sh` 单一入口）
- R2-ARC-17（删空转配置 + 建 `dependency-upgrade-watchlist.md` 登记 4 条到期判据）
- R2-ENG-05（判权双入口统一）、R2-ENG-09（缓存加 LRU）、R2-ENG-10（租约粒度）、R2-ENG-11（`:8000` + `/metrics`）
- R2-QA-09（给 `validate_openapi.py` 补 `--self-test`）
- R2-QA-10（分层基线加 `due_batch` 到期机制）
- R2-QA-11（删除盘上工件实体，而非仅 gitignore）
- R2-QA-12（`pytest-xdist` + `pytest-timeout`）
- R2-QA-13（固定覆盖率口径声明）
- `OA_BATCH` 硬编码批次号（`ci-adapter.yml:88` = B21）：建议从 git tag/日期派生，或加「批次落后即红」校验——**忘记推进批次号会让缺陷到期检查默默基于旧批次**
- 其余低危按域批量清理

### 9.5 三条方法论建议（比单条修复更重要）

1. **「配置改动的完成定义不是 diff 落地，而是产物时间戳更新」**。本轮 H-1 的死因就是这条：改了 Dockerfile + compose，但镜像没重建，无人发现。任何涉及镜像/构建/产物的改动，验收必须是「产物时间戳晚于提交时间」。

2. **「类存在 ≠ 能力存在」**。凡"某项配置声称已生效"的结论，必须做**可达性验证**——grep 该配置项的读点/装配点，只有注释、日志或死代码即视为未实现。ARC-13 被第一轮误判为「已实现」、R2-ENG-01/04/05 都是同一形态。

3. **「注释宣称已优化」是最危险的债**。本轮 4 条（R2-ENG-01/04/05/18）的共同特征是注释写着"已优化/已防护/批量"，代码没有。这类债会让下一轮审计直接跳过。建议在 code review 清单里加一条：**涉及"已优化/已修复/已防护"字样的注释，必须有对应测试或证据行号。**

4. **「能力存在 ≠ 能力在生效」**——这是本轮最重要的一条，也是 QA 维度的一句话结论：**能力在，刹车没接上。**
   测试写得极好（2366 条、变异 3/3 命中、断言 3.0 条/用例、单例复位彻底），但：
   - CI 的 PATH 没接（H-4）→ 2366 条一条都跑不到
   - e2e 没进任何自动化（H-5）→ 主线回归防线等于不存在
   - 覆盖率分母只有 12%（H-6）→ 44% 是假高
   **所有本地指标都好看，而它们都不在生效状态。** 这类失效比"测试写得差"危险得多，因为没有任何信号提示你去看。检验方式只有一个：**问「这条防线最后一次真正拦住问题是什么时候」**，答不上来就是没接上。

---

## 十、未验证项（诚实登记）

| 项 | 原因 | 影响 |
|---|---|---|
| CI 三份 workflow 是否会红 | 本月额度耗尽未实跑 | H-2/ARC-06 的 CI 改动、LGPL 是否被 SCA 判黑、H-4 的 PATH 结论，均属**静态推演**（条目标注「待远端验证」） |
| 三套编排真实 `docker build` | 需拉镜像、耗时，且现场有容器在跑 | H-1 的失败形态由**最小复现 + 静态解析**确认，非真实构建输出 |
| 前端 e2e 贯穿旅程 | 缺真实统一登录测试账号 | 实跑 5 passed / **1 skipped（正是贯穿段）**——即这条防线实际从未跑通 |

| Adobe/Python 三方包逐项许可 | `uv.lock` 无 license 元数据 | 依赖许可结论基于白名单比对，非机读全量 |

---

**审计自证**：本报告所有门禁类结论均有实跑输出（`validate_import_directions.py` exit 0、`validate_images.py` exit 0、`validate_openapi.py --strict` exit 0、`validate_deps.py` exit 0 + `--self-test` 全绿、`validate_gate_wiring.py` exit 0、`export_plugin_contract.py --check` exit 0、`check-plugin-contract.mjs --generated` 0 差异、`uv lock --check` resolved 58）；H-1 为临时目录最小复现（`npm ci` exit 1）；镜像时间戳由 `docker image inspect` 取得；测试数字由 `pytest` 实跑（两人独立两轮）；变异测试 3/3 在代码副本上进行、仓库零改动。审计全程未修改任何源码/配置/锁文件，`git status` 仅新增 4 份审计报告。

**分报告**：
- `docs/audit-2026-10-28-r2-arch.md`（架构/依赖许可，17 条）
- `docs/audit-2026-10-28-r2-eng.md`（代码/安全/性能，21 条）
- `docs/audit-2026-10-28-r2-qa.md`（测试覆盖与验证体系，13 条）
