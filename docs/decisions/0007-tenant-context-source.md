# ADR-0007：租户上下文的唯一可信来源（D-2 定性）

| 项 | 内容 |
| --- | --- |
| 记录版本 | v1.0 |
| 编制日期 | 2026-10-25 |
| 编制人 | 高见远（架构师） |
| 触发 | QA 回归 D-2：「`profile` 返回前前端带 `X-Tenant-Id: default`，数据放 `default` 才对齐」 |
| 关联 | `docs/improvement-design-2026-10-25.md` §5-D1（会话门）· AR-017 · QA BUG-02 |
| 结论 | **P2（配置依赖型缺陷，非当前可复现越权）**；修法为前端 2 行，纳入 Batch 2 |

---

## 1. 结论

**D-2 是「真实缺陷、当前不发作」。** 不是桩环境专属，也**不是**数据越权。

- **为什么不是越权**：跨租户读写已被 AR-017 修好并有用例锁死（`apps/adapter/app/core/security.py:460-508` 的 `assert_tenant_membership`；用例 `apps/adapter/tests/security/test_tenant_spoofing.py:195-208` 断言「持 A 令牌 + `X-Tenant-Id: B` → 403 / 20003 / 业务语言文案」）。生产/预发还叠加了启动期防呆（`security.py:905-917`：`tenant_membership_check=off` 必须同时 `allow_security_downgrade=true` 才能启动）。
- **为什么当前不发作**：全部部署预设都是 `OA_DEPLOY_MODE=private`（`deploy/.env:26`、`deploy/.env.example:26`、`deploy/.env.lite:31`、`deploy/.env.lite.example:31`、`deploy/docker-compose.lite.yml:420`），而私有化模式**忽略** `X-Tenant-Id`、恒返回 `default`（`security.py:214-215`）。QA 看到的「数据放 `default` 才对齐」正是私有化模式的**设计行为**，不是异常。
- **真实缺陷在哪**：租户在「首个请求」阶段来自**请求头**而非令牌；且 `/identity/profile`（本应回答「我是谁、我属于哪个租户」的端点）把请求头**回显**成租户——形成自举循环。一旦部署 `saas + oidc`（配置项默认值就是 `deploy_mode="saas"`、`auth_mode` 默认 `stub`，见 `app/core/config.py:51,68`），多租户用户的首个 `profile` 请求会 403，整个应用不可用。
- **定级 P2 而非 P0/P1**：① 无数据泄露路径；② 现网/预发/私有化包均不触发；③ 触发条件是「切到 saas+oidc 多租户」，属于尚未交付的部署形态。**若 V1.1 要交付 saas 多租户，本条在开该形态前必须升 P0 并先修。**

## 2. 判权模型（四种组合）

| 组合 | 生效租户来源 | 首个 `profile`（带 `X-Tenant-Id: default`）结果 | 可否伪造租户 |
| --- | --- | --- | --- |
| `private + oidc` | **恒 `default`**，忽略请求头（`security.py:214-215`） | 200，返回 `default`（`identity_service.py:218` 用 `auth.tenant_id`） | **否**（单租户，头被丢弃；用例 `test_tenant_spoofing.py:301-311`） |
| `private + stub` | 同上，恒 `default` | 200，返回 `default` | **否** |
| `saas + oidc` | 头 > 令牌声明（`security.py:217-221`）；跨租户需过成员校验（`:588-600`） | **403 / 20003**（用户非 `default` 成员）→ 前端落到 `error` 态 | **否**（`:502` 拒绝；用例 `:195-208`） |
| `saas + stub` | 头 > 令牌声明，**无成员校验**（`_should_check_membership` 的 `auto` 档排除 stub，`security.py:542`） | 200，回显 `default` | **是**（但 stub 在 staging/production 被 `validate_auth_startup` 拦成启动失败，`security.py:886-895`；用例 `test_tenant_spoofing.py:413`） |

`auto` 档为什么排除 stub：stub 不验签，本地联调常手编令牌 + 任意租户头，强校验会让联调失效；而「stub 不存在于线上」由启动期防呆保证，所以不存在「线上靠 auto 档漏校验」的窗口（`security.py:520-522` 的原话）。

## 3. `profile` 的自举问题（最关键的一条证据）

```
apps/web/src/lib/api-client.ts:220   let currentTenantId: string = DEFAULT_TENANT_ID;   // 'default'
apps/web/src/lib/api-client.ts:276-277  if (!options.tenantId) headers.set(TENANT_HEADER, currentTenantId);
apps/web/src/components/layout/session-gate.tsx:173  setTenantId(DEFAULT_TENANT_ID);
apps/web/src/components/layout/session-gate.tsx:184  const profile = await identityApi.profile();   // ← 此刻头是 default
apps/adapter/app/core/security.py:217-219           header_tenant 优先于令牌声明
apps/adapter/app/services/identity_service.py:181,218   tenant=TenantInfo(tenant_id=auth.tenant_id) ← 回显「生效租户」
```

**链条**：前端恒定发 `X-Tenant-Id: default` → 后端按头解析 → `profile` 把头回显成 `tenant.tenant_id` → 前端据此 `setTenantId(...)` → 后续请求与头一致。**客户端无法从服务端学到自己的租户，因为服务端先问了客户端。** 在私有化模式下这个环的「错误答案」恰好等于正确答案（`default`），所以看不出来；在 saas 下它会 403 或在用户确为 `default` 成员时把会话**永久固定在 `default`**（QA 担心的那一半是对的，只是触发面比想象小）。

## 4. 建议

1. **租户唯一可信来源 = 令牌声明 `tenant_id`（且仅后端可读）**；`X-Tenant-Id` 降级为「**用户显式切换租户的意图声明**」，只允许在已通过成员校验（AR-017）后生效，且**不得反向进入身份自述**（`profile` 必须回答「你属于哪个租户」，不能回答「你要哪个租户」）。
2. **最小修法（前端 2 行，零契约变更）**：`currentTenantId` 初值改为 `null`，`buildHeaders` 仅在非空时设置 `X-Tenant-Id`。效果：首请求不带头 → 后端用令牌声明 → 返回真租户 → 之后头恒等于令牌租户（同租户零额外查询，`test_tenant_spoofing.py:233-251` 已锁）。**不推荐**改 `profile` 语义（会把 `private` 下的 `default` 收敛与展示口径搅在一起，风险更大）。
3. **需补的门禁用例**（当前**缺口**，`test_tenant_spoofing.py` 无此形状）：
   - 正样本：`saas + oidc`，令牌租户 A，**不带** `X-Tenant-Id` → 200 且 `tenant.tenant_id == A`（已有近似用例 `:254-260`，需补一条「前端首请求形状」的显式命名）。
   - **负样本**：`saas + oidc`，令牌租户 A，带 `X-Tenant-Id: default`，用户非 `default` 成员 → 403 / 20003（锁住「自举毒化」这个坑，防止后人把初值改回 `'default'`）。
   - 负样本：`private`，带任意头 → 生效租户恒 `default`（已有 `:301-311`）。
   - 前端门禁：`scan:banned` 之外增设静态断言「`api-client` 不得给 `currentTenantId` 非空初值」（可选，靠 review 也可）。

## 5. 实施范围

| 项 | 文件（行号为**改后**实测值） | 是否属 Batch 1 |
| --- | --- | --- |
| 前端：`currentTenantId` 初值 `'default'` → `null`；`setTenantId` 签名放宽为 `string \| null`；`buildHeaders` 非空判断 | `apps/web/src/lib/api-client.ts:228,254,286-287` | **是**（`20fee16` 已落地） |
| 前端：`upload()` 是**第二个**写租户头的地方，不走 `buildHeaders`，同样要加非空判断 | `apps/web/src/lib/api-client.ts:434-437` | **是**（`20fee16` 已落地，见下方 §5.1） |
| 前端：三处 reset 改 `setTenantId(null)` | `apps/web/src/components/layout/session-gate.tsx:172,218,259` | **是**（`20fee16` 已落地） |
| 后端：补 2 条租户自举用例 | `apps/adapter/tests/security/test_tenant_spoofing.py` | **否 → Batch 2**（不改产品行为，只是加网） |
| 后端：`X-Tenant-Id` 语义收紧（是否只允许「已校验的切换意图」、是否给 `profile` 增 `home_tenant_id`） | `app/core/security.py`、`app/services/identity_service.py`、`app/schemas/identity.py` | **否 → Batch 2**（涉及契约加字段与判权口径，需单独 ADR） |

### 5.1 `upload()` 为什么必须一起改（补记，本 ADR 初版遗漏）

`api-client.ts` 有**两个**写 `X-Tenant-Id` 的地方：`buildHeaders`（:286）与 `upload`（:434）。后者原本写作 `headers.set(TENANT_HEADER, options.tenantId ?? currentTenantId)`——在 `currentTenantId` 放宽为 `string | null` 之后，`Headers.set` 只接受 `string`，`null` 会被**转成字面量 `"null"`** 写进请求头。那比带 `default` 更糟：`default` 至少是一个真实租户 ID，`"null"` 是一个不存在的租户，后端会按它查库并得到空集，表现为「上传成功但列表永远为空」这类查不出原因的故障。凡放宽此类模块级变量的类型，必须逐处 grep 全部写入点，不能只改「主路径」。

### 5.2 部署前置：`saas` 必须配「带 `tenant_id` 声明」的 OIDC

改完后首请求不再带租户头，于是 `security.py:222-226` 这道原本被头遮蔽的守卫开始生效：**`deploy_mode=saas` 且既无头、又无 `tenant_id` 声明 → 20001**。这是**预期的 fail-fast**（比静默把会话钉在 `default` 好），但属可观测的行为变化：

- `private` 模式**不受影响**：`resolve_tenant_id` 在 `:214-215` 就返回 `default`，早于该守卫；
- 现网预设全为 `private`（§1），无影响；
- 已知体验瑕疵：该守卫返回 20001「请求内容不完整，请检查后重试」，会话门会落到 `error` 态而不是跳登录——**用户看到的文案与真实原因（IdP 未下发租户声明）不匹配**。本批接受（属运维配置错误，且不在用户可自我修复的范围）；Batch 2 建议在 `validate_auth_startup` 旁补一条运维提示，或为该场景单独映射文案（需先在错误码注册表登记，不能前端硬编码）。

**结论：`saas` 部署的前提是 OIDC 令牌必须带 `tenant_id` 声明。** 这条应写进 `deploy/.env.example` 的注释，与 `OA_DEPLOY_MODE` 同段。

## 6. 对 F-01 会话门的影响

**当前设计（「`profile` 就绪后才挂载子页面」）继续成立，而且正是它把风险压到最小**：门把「就绪前的请求集合」限制为**恰好 1 个**（`profile`）。若当初选「令牌注入即挂载」，saas+oidc 下会有 N 个页面查询同时带错租户头、各触发一次成员校验并全部 403。

**已落地**（`20fee16`，父 `cab5c28`）：`restoring` 期内 `currentTenantId === null`，`profile` 请求不带租户头；服务端按令牌声明解析 → 返回真租户 → `session-gate.tsx:191 setTenantId(profile.tenant.tenant_id)` 之后才挂载子页面，后续所有请求的租户头恒等于令牌租户。状态机、就绪判据、`AppSidebar` 在门内——均未改动。

不变的部分：`phase` 五态、`QUERY_KEYS.profile()` 单次缓存、`20002` 统一处置、`next` 参数校验、`(main)` 布局唯一父级的覆盖性。

## 7. 门禁卫生（本次连带发现，与租户逻辑无关但会挡住合入）

`apps/web/.next.stale-r1/`（QA 生产构建的改名产物）既不在 `.gitignore` 也不在 `eslint.config.mjs` 的 ignore 列表：

- `.gitignore:12` 是 `.next/`、`apps/web/.gitignore:9` 是 `.next-bak-*/`（**带横线**）；
- `eslint.config.mjs` 的 ignore 是 `.next/**`、`.next-bak*/**`（**不带横线**）——两层忽略规则**当前就已经不一致**，`.next-bak-*` 会被 git 忽略却被 eslint 当源码扫；
- QA 这次用的是 `.next.stale-r1`（**点号**），两层都没覆盖 → 出现在 `git status` 未跟踪列表 + 让全仓 `lint:web` 报 4.2 万条 `no-var/prefer-const`。

**建议**（一行改动，不改逻辑）：把构建产物的目录命名收敛为**一个**约定（`.next-bak-<时间戳>`），并在 `.gitignore` 与 `eslint.config.mjs` 用**同一组**模式 `.next-bak-*/`、`.next.stale-*/`。不要图省事写成 `.next*`：仓库里已有「显式模式」的先例（`.next-bak*/**` 的注释写明是为了规避 Windows 上删不掉临时目录），通配收得太宽会顺带忽略掉将来合法的源码目录。先加 ignore 而不是删目录——QA 可能仍在用那份产物做复验。
