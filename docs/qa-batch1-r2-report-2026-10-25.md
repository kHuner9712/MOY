# 第二轮定向回归报告（D-1 修复复验）

| 项目 | 内容 |
| --- | --- |
| 报告编号 | MOY-QA-2026-10-25-R2（含 T03b 复验，第 2 版） |
| 被测分支 | `fix/batch1-frontend-stability` |
| 代码基线 | **`20fee16`**（`cab5c28` D-1 → `20fee16` T03b；其后仅有文档/忽略规则提交 `39b0403`、`d1a6a86`、`9fe6666`） |
| 文档 HEAD | `d1a6a86`（ADR-0007）→ 本报告追加时仓库 HEAD 为 `9fe6666` |
| 改动范围 | 代码两批：①D-1 `use-declaration-form.ts`（+104/−7）+ `declaration-form.tsx`（+30/−4）；②T03b `api-client.ts`（+32/−16 量级）+ `session-gate.tsx`（三处 reset 改 `null`） |
| 构建方式 | **重新生产构建，只构建一次**（不复用旧 `.next`）：旧产物改名移出 → `npm run build:web` → 新 `BUILD_ID = OGb5ZdY3TwtU0IxCvbUc-`（`cab5c28` 版为 `V6J-o4iTl3QXzETF6J1F-`），耗时 28s |
| 运行环境 | 适配层桩 :8000 + `next start` :3200 + 无头 Chrome（CDP）:9222；**两种部署模式都验**：`saas`（默认，真多租户路径）与 `OA_DEPLOY_MODE=private`（生产预设） |
| 执行人 | 严过关（QA） |
| 结论 | **PASS**（`saas` 模式 9/9、`private` 模式 4/4、静态门禁 7/7；另登记 1 条 P3 存量发现、1 条 P2 过渡期观察，均不阻塞本批） |

## 0. 结论速览

| 项目 | 结果 |
| --- | --- |
| R9a / R9b / R9b-neg / R9c / R9d / R10a / R1 | **7 / 7 PASS** |
| **R16（新增 · T03b 判据）** | **PASS**：首个 `profile` **不带** `X-Tenant-Id`；其后请求头 = 响应体里的真实租户 |
| 首个 `/identity/profile` 的租户头快照 | **(absent)**，HTTP **200**，响应体 `data.tenant.tenant_id = tnt_dev` |
| profile 之后业务请求的 `X-Tenant-Id` | **`tnt_dev`**（= 响应体租户，非字面量 `null`） |
| `private` 模式复验（R16/R9a/R9b/R1） | **4 / 4 PASS**（`default` 全程稳定） |
| `POST /api/v1/agent/tasks` 次数（R9b） | **1**（上一轮为 **0**） |
| `POST /api/v1/agent/tasks` 次数（R9c 从市场进入） | **1** |
| `POST /api/v1/agent/tasks` 次数（R10a 合法邮箱） | **1** |
| `POST /api/v1/agent/tasks` 次数（R9b-neg 目标仓库留空） | **0**（必填仍然生效，未被"降级"放过） |
| `/identity/profile` 次数（R1 硬刷新 `/chat`） | **1** |
| 是否仍有标红字段（R9a 初始态） | **否**（`errors=[]`、`aria-invalid` 计数 0） |
| 静态门禁 | `typecheck:web` 0、`lint:web` 0、`scan:pages` 0、`scan:workbench` 0、`scan:banned` 0、`scan:oidc` 0、`scan:contract` 0（**7/7**） |

**构建产物可信性先验校验**：新 `.next/static` 中命中 `暂无可选项，请直接填写` 的 chunk 恰好 1 个（D-1 已进产物），
且 `DEFAULT_TENANT_ID` 字面量 **0 命中**（T03b 已删除该常量，说明不是旧产物）。`BUILD_ID` 也与上一轮不同。

---

## 1. R9a-R1 逐条结果

### R9a 硬刷新 `/workbench/plg_dev_workbench`（PASS）

| 断言 | 实测 |
| --- | --- |
| 表单字段数 | **6**：需求标题\*/需求描述\*/目标仓库\*/分支策略\*/技术栈偏好/参考文档 |
| `分支策略` 默认值 | `select`，value = **「新建分支」**，`aria-invalid = null`，候选 `[新建分支, 指定分支]` |
| `目标仓库` 控件形态 | **`input[type=text]`**（非 select），value 空、`aria-invalid = null` |
| 降级提示 | **「暂无可选项，请直接填写」** 出现（hint 槽位） |
| 错误态 | `p.text-destructive` 数量 **0**；`[aria-invalid="true"]` 计数 **0** |
| 控制台 | `Runtime.exceptionThrown = 0`；console error 0 条 |

截图：`.workbuddy/screenshots/after2-workbench.png`（可肉眼看到「目标仓库」是输入框 + 灰字提示、「分支策略」下拉显示「新建分支」、无红框）。

### R9b-neg 目标仓库留空提交（PASS，反向用例）

| 断言 | 实测 |
| --- | --- |
| `POST /agent/tasks` | **0 次** |
| 标红文案 | **「请填写目标仓库」**（`text-destructive`），`aria-invalid` 计数 1 |

作用：证明「空候选降级为文本框」只放宽了**候选值校验**，没有把**必填**一起放宽。

### R9b 填好三项后提交（PASS）

| 断言 | 实测 |
| --- | --- |
| 填入 | 需求标题、需求描述、目标仓库 = `https://git.example.com/moy/web`（任意文本） |
| `POST /api/v1/agent/tasks` | **1 次**（URL 精确为 `http://127.0.0.1:8000/api/v1/agent/tasks`） |
| 提交后残留错误 | `errors = []` |
| 适配层侧 | `{"method":"POST","path":"/api/v1/agent/tasks","status_code":200}`（下游未启用但接口接受入参，任务进入待确认态） |
| 异常 | 0 |

对比：**上一轮同一操作为 0 次 POST** —— 这是本轮修复生效的核心证据。

### R9c 从市场点进工作台（inputs 异步到达路径，PASS）

| 断言 | 实测 |
| --- | --- |
| 入口 | `/plugins` → 卡片「前端研发工作台」→「打开工作台」按钮（`h3.closest('div[class*=rounded]')` 精确定位卡片） |
| 表单形状 | 6 字段，与 R9a **完全一致**（label 序列逐字相同） |
| `分支策略` | 「新建分支」，`aria-invalid = null` |
| `目标仓库` | `input[type=text]` |
| 提交 | **1 次 POST**，提交前无残留错误 |
| 异常 | 0 |

结论：**两条时序（硬刷新 / 客户端路由进入）结果一致**，异步补默认值生效。

截图：`after2-workbench-from-market.png`。

### R9d 已填值不被异步重申明覆盖（PASS）

| 断言 | 实测 |
| --- | --- |
| 触发方式 | 等查询进入 stale（全局 `staleTime=30s`，等待 31s）→ `Network.emulateNetworkConditions` 断网 → 恢复 → 触发 `refetchOnReconnect` |
| 断网确实生效 | 断网期间 `navigator.onLine = **false**`，`online/offline` 事件均收到 |
| 详情重取次数 | `/plugins/market/plg_dev_workbench` GET：**1 → 2**（真的重新取了一次，inputs 换引用） |
| 用户输入 | 需求标题「已填内容不应被清空」、需求描述、目标仓库 `…/keep` **全部原样保留** |
| select 默认值 | `分支策略` 仍为「新建分支」，未被重置或清空 |
| 异常 | 0 |

> 说明：`query-client.tsx` 里全局 `refetchOnWindowFocus: false`，**focus 不会触发重取**，所以本轮按你的第二个选项（详情 refetch）走 `refetchOnReconnect` 这条真实重取路径，而非伪造一次 focus。

### R10a `/workbench/plg_weekly_report`（PASS）

| 断言 | 实测 |
| --- | --- |
| 字段数 | **3**：汇报周期\*（text）/本周重点（text）/收件人\*（type=email） |
| 非法邮箱 `not-an-email` | `POST = **0**`；`submit` 事件 **未触发**（原生约束校验拦在 React 之前）；`#declaration-receiver:invalid = true` |
| 合法邮箱 `qa@example.com` | `POST = **1**` |
| 异常 | 0 |

截图：`after2-weekly.png`。

### R1 冒烟（PASS）

| 断言 | 实测 |
| --- | --- |
| 硬刷新 `/chat` 后 URL | `http://127.0.0.1:3200/chat`（未跳登录） |
| `/identity/profile` 次数 | **1** |
| 业务请求带 Authorization | **3/3** |
| `登录状态已失效` 文案 | 未出现 |
| 异常 | 0 |

截图：`after2-chat.png`。

### 附加（超出要求，覆盖第二个受影响插件）

`/workbench/plg_hr_admin` 的「所属部门」同样为 required + 空候选 → 实测降级为 `input[type=text]` + 提示「暂无可选项，请直接填写」，填值后 **1 次 POST**、无标红、异常 0（`after2-workbench-hr.png`）。**D-1 的两个受影响插件均已解除阻断。**

---

## 1.5 R16 首个 `profile` 的租户头（本轮新增 · T03b 判据，PASS）

按工程师的要求**拆成两句验**，避免"改之前恒带 default、改之后也像对的"这种自我欺骗：

| 断言 | 实测 |
| --- | --- |
| ① 首个 `GET /api/v1/identity/profile` 的请求头 | **没有 `X-Tenant-Id`**（`(absent)`），HTTP **200** |
| ① 该响应的 `data.tenant.tenant_id` | **`tnt_dev`**（= 令牌声明的租户，服务端解析下发） |
| ② profile 之后任意业务请求的 `X-Tenant-Id` | **`tnt_dev`**，与①响应体逐字相同（样本：`/plugins/installed`、`/plugins/market?…`；共 2 条，取值集合只有 1 个） |
| ② 是否出现字面量 `"null"` / `"undefined"` | **没有**（全局不变量 INV：本轮共 3 条业务请求，`literalNullTenant = 0`） |
| 提交请求上的租户头 | `POST /agent/tasks` 携带 `X-Tenant-Id: tnt_dev` |

**关键点：这条是在 `saas` 模式下验的**（我启动适配层时未设 `OA_DEPLOY_MODE`，实际 `deploy_mode=saas`，见 `adapter-r3.log` 启动行）。
`saas` 正是"请求头优先于令牌声明"、自举毒化会真实发生的那条路径；`private` 模式另做了 4/4 回归（见 §1.6）。

> **一个必须如实说明的环境耦合**：T03b 生效后，`saas` 模式下前端不再预置租户头，服务端按令牌声明解析出 **`tnt_dev`**，
> 于是"生效租户"从上一轮的 `default` 变成了 `tnt_dev`。第一轮我按设计把夹具放在 `default`（私有化口径），
> 本轮按 `tnt_dev` 重新预置后才跑出结果——**这是修复生效后的正确表现，不是测试妥协**：
> 数据必须落在令牌声明的租户里，这本身正是 T03b 想要的行为。

## 1.6 `private` 模式回归（生产预设，4/4 PASS）

用 `OA_DEPLOY_MODE=private` 重启适配层（启动行 `"deploy_mode": "private"`），不带租户头请求 `profile` → 返回 `default`：

| 用例 | 结果 | 关键数字 |
| --- | --- | --- |
| R16-private | **PASS** | 首个 profile `(absent)` + HTTP 200；响应体租户 `default`；之后请求头全部 `default` |
| R9a-private | **PASS** | 6 字段、`分支策略`=「新建分支」、`目标仓库`=`input`+hint、`errors=[]` |
| R9b-private | **PASS** | `POST /agent/tasks` **1 次**，携带 `X-Tenant-Id: default` |
| R1-private | **PASS** | 硬刷新 `/chat` 不掉登录，`profile` **1 次**，异常 0 |

截图：`after3-private-workbench.png`。**结论：T03b 在私有化模式下没有引入回归，前端表现与改前一致。**

## 2. 静态门禁（7/7 通过）

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck:web` | **0** |
| `npm run lint:web`（`--max-warnings=0`） | **0** |
| `node scripts/check-page-states.mjs`（`scan:pages`） | **0**（`/ops/metrics`、`/ops/settings` 等三态判定通过） |
| `node scripts/check-workbench-host.mjs`（`scan:workbench`） | **0**（G-27 三项红线全绿：宿主 1 个、110 个源文件 0 命中、插件目录 17 文件 import 全在白名单） |
| `node scripts/check-banned-terms.mjs`（`scan:banned`） | **0**（110 文件 0 命中） |
| `node scripts/check-plugin-contract.mjs`（`scan:contract`） | **0** |
| `node scripts/check-oidc-auth.mjs`（`scan:oidc`） | **0** |

**HEAD 变动后复跑**：报告定稿时仓库 HEAD 已推进到 `f33b22b`（只改 `.gitignore` / `eslint.config.mjs` / `deploy/.env.lite.example`，
`git show --name-only` 确认 `apps/web/src` 与 `apps/adapter/app` 零改动），因此在 `f33b22b` 上重跑了 `lint:web` 与 `typecheck:web`，
仍为 **0 / 0**。构建产物（`20fee16` 代码）与新 HEAD 的运行时行为一致。

**`lint:web` 假红已复位**：上一轮 42742 条 error 全部来自 `.next.stale-r1/`（我改名的产物目录，不在 eslint ignores 内）。
该目录已由工程师删除（其报告：删除前确认 10 分钟无写入、内容 100% 为构建产物），本轮 `lint:web` 在**未修改任何 eslint 配置**的前提下回到 **0**。
治本项（ignore 模式 `.next-bak-*/**` + `.next.stale-*/**`）已由 `39b0403` 落地。

---

## 3. 测试侧问题（已修脚本，非产品缺陷）

如实记录，避免把脚本缺陷当成产品问题回传：

| # | 现象 | 根因 | 处置 |
| --- | --- | --- | --- |
| T1 | R9a 首轮 FAIL，`errorTexts` 里出现「最多 80 个字」 | `Field` 的 hint 与 error 复用同一个 `<p>` 槽位，脚本按文案关键字分类，把 hint 误当 error | 改用 `p.text-destructive` 与 `[aria-invalid="true"]` 判定（v2） |
| T2 | R9d 首轮 FAIL，详情 GET 1→1（没重取） | 全局 `staleTime=30s`，刚取过的详情在 30s 内是 fresh，`refetchOnReconnect` 不会重取；且 `refetchOnWindowFocus=false`，focus 方案无效 | 先等 31s 进入 stale 再断网→恢复（v2） |
| T3 | R10a 首轮 FAIL，看不到「请填写有效的邮箱地址」 | `<form>` 未加 `noValidate` + `<input type="email">`：非法邮箱被**浏览器原生约束校验**拦在 `submit` 之前，React 的 `onSubmit` 根本不执行，因此自定义文案永不出现 | 改为断言「是否被拦住」（0 POST + `:invalid`），并把该现象单列为发现 D-6（v2） |
| T4 | `lint:web` 一度报 42742 条 error | 我把旧产物目录改名为 `.next.stale-r1`，而 `eslint.config.mjs` 的 ignores 只覆盖 `.next/**` 与 `.next-bak*/**`；目录移出后 lint 恢复 0 | 移出/按仓库约定命名，**未改动 eslint 配置** |

> 其中 T4 属于我自己制造的假红，已在报告中保留痕迹，方便复核时不被误导。

---

## 4. 定性更正与本轮新发现（均不阻塞本批）

### D-2【P2 · 已按 ADR-0007 更正定性】前端首个 `profile` 预置 `X-Tenant-Id: default` → `saas + oidc` 租户自举毒化

初版（`docs/qa-batch1-report-2026-10-25.md`）把它写成"多租户会串数据""数据放 default 才对齐是异常"，**两处都不准确**，按架构核实结果更正如下：

| 初版说法（错） | 更正后（ADR-0007） |
| --- | --- |
| 私有化下恒返回 `default` 是被前端带偏的异常 | **是设计行为**：全部部署预设都是 `OA_DEPLOY_MODE=private`（`deploy/.env:26`、`.env.example:26`、`.env.lite:31`、`docker-compose.lite.yml:420`），该模式**忽略** `X-Tenant-Id`、恒返回 `default`（`apps/adapter/app/core/security.py:214-215`），单租户天然隔离 |
| 多租户会串数据 | **不成立**：AR-017 已修好并锁死——持 A 租户令牌 + `X-Tenant-Id: B` → 403 / 20003 / 业务语言文案（`apps/adapter/tests/security/test_tenant_spoofing.py:195-208`）；生产另有启动期防呆（`security.py:905-917`） |

- **真实缺陷（P2，触发面更窄）**：仅 `saas + oidc` 下成立。前端拿 profile 前恒发 `X-Tenant-Id: default`，
  后端让请求头**优先于**令牌声明（`security.py:217-219`），`profile` 又把生效租户回显成 `tenant.tenant_id`
  （`identity_service.py:181,218`）→ 客户端与服务端互相自举。表现：首个 `profile` **403/20003**（会话门落到 error 态
  显示「当前账号没有该操作的权限」而**不是**跳登录，排障时极易看错方向），或用户本身是 `default` 成员时把会话永久钉住。
- **定级 P2**：不进 Batch 2；V1.1 上 `saas` 多租户前必须升 P0。修法（5 行）已并入 T03b `20fee16`，本轮 R16 已验证。
- **ADR**：`docs/decisions/0007-tenant-context-source.md`。

### D-6【P3 · 存量】非法邮箱时点「开始执行」没有任何应用内反馈

- **现象**：`/workbench/plg_weekly_report` 收件人填 `not-an-email` 后点提交：POST 0 次、**无错误文案、无 toast**；`submit` 事件根本没到 React。用户看到的是「按钮点了没反应」（真实浏览器会弹原生校验气泡，但它不可定制、不进无障碍错误区，且与应用内 `validateField` 的邮箱规则**重复且两套**）。
- **根因**：`declaration-form.tsx` 的 `<form>` 未加 `noValidate`，`type="email"` 触发浏览器原生约束校验，先于 `onSubmit` 拦截。
- **归属**：**存量**，`683fe03`（修复前）版本即如此（`type={spec.type === 'email' ? 'email' : 'text'}` + 无 `noValidate`），非本次 D-1 改动引入。
- **建议**：`<form noValidate>`（让校验统一走 `validateField`，文案一致），或改为 `type="text" inputMode="email"`。**本批未改产品代码**。

### D-7【P2 · 过渡期观察】降级文本框放行任意字符串，后端未校验其真实性

- **现象**：目标仓库填 `https://git.example.com/moy/web`（任意文本）即被接受，`POST /agent/tasks` 返回 200。
- **含义**：D-1 的降级是「先把路走通」的过渡方案，**根治仍是声明层补 `options`**；否则下游真实接入后，用户填的仓库名不存在会在执行阶段失败（错误被推迟到任务运行期，更难解释）。
- **建议**：`manifests/plg_dev_workbench.json`（`repo`）与 `plg_hr_admin.json`（`department`）补候选；在此之前，产品需明确「自由填写 + 运行期校验」的兜底文案。

---

## 5. 证据索引

| 类型 | 路径 |
| --- | --- |
| 定向脚本（v1，含三处脚本缺陷的原始输出） | `.workbuddy/suite-r2-round.mjs`、`.workbuddy/suite-r2-round.out` |
| 定向脚本（v2，修正取证口径后） | `.workbuddy/suite-r2-round-v2.mjs`、`.workbuddy/suite-r2-round-v2.out` |
| 结果 JSON | `.workbuddy/result-r2-round.json`（v1）、`.workbuddy/result-r2-round-v2.json`（v2） |
| 排查脚本 | `.workbuddy/{debug-weekly,debug-weekly2,debug-weekly3,debug-weekly4,debug-weekly5,probe-online,probe-hr}.mjs` |
| 截图 | `after2-workbench.png`、`after2-workbench-submitted.png`、`after2-workbench-from-market.png`、`after2-weekly.png`、`after2-chat.png`、`after2-workbench-hr.png` |
| 构建日志 | `.workbuddy/build-r2.log`（`BUILD_ID=V6J-o4iTl3QXzETF6J1F-`） |
| 服务日志 | `.workbuddy/adapter-r2.log`（POST 200 记录；本轮 `401=0`、`missing Authorization header=0`）、`.workbuddy/next-r2.log` |
| 门禁日志 | `.workbuddy/r2-{tc,lint,check-page-states,check-workbench-host,check-banned-terms}.log` |
| **T03b 定向脚本** | `.workbuddy/suite-r3.mjs`（saas，含 R16/INV）、`.workbuddy/suite-r3-private.mjs`（private） |
| **T03b 结果 JSON** | `.workbuddy/result-r3.json`、`.workbuddy/result-r3-private.json`、`.workbuddy/suite-r3.out` |
| **T03b 截图** | `after3-workbench.png`、`after3-workbench-submitted.png`、`after3-workbench-from-market.png`、`after3-weekly.png`、`after3-chat.png`、`after3-private-workbench.png` |
| **T03b 构建/服务日志** | `.workbuddy/build-r3.log`（`BUILD_ID=OGb5ZdY3TwtU0IxCvbUc-`）、`.workbuddy/adapter-r3.log`（`deploy_mode=saas`）、`.workbuddy/adapter-r3-private.log`（`deploy_mode=private`）、`.workbuddy/next-r3.log` |
| **T03b 门禁日志** | `.workbuddy/r3-{tc,lint,check-page-states,check-workbench-host,check-banned-terms,check-oidc-auth,check-plugin-contract}.log` |

---

## 6. 结论与残余风险

**PASS（代码基线 `20fee16`）。** 两批改动在真实运行时都可观察到：

**T03b（租户头自举修复）**：
1. 首个 `profile` **不带** `X-Tenant-Id`，HTTP 200（R16①）——这是 `saas` 模式下最关键的一步，改前恒为 `default`；
2. 服务端按令牌声明解析出 `tnt_dev`，profile 之后的业务请求头**逐字等于该租户**（R16②），且从不出现字面量 `"null"`（INV）；
3. `private` 模式 4/4 无回归，`default` 全程稳定（§1.6）；
4. 提交链路（`POST /agent/tasks`）携带正确租户头；`upload()` 的"未知不带"口径由 INV 兜住（本轮未触发 multipart 上传，只验证了"不发 null"这条不变量，见 V5）。

**D-1（声明表单）**：

1. 异步补默认值 → `分支策略` 显示「新建分支」且不标红（R9a/R9c），**R9c 证明两条到达时序一致**；
2. 空候选降级 → `目标仓库`/`所属部门` 变文本框 + hint，**必填仍生效**（R9b-neg）；
3. 校验前置 `options.length>0` → 降级后的文本值能通过校验并发起任务，**POST 从 0 → 1**（R9b/R9c/R10a）；
4. 覆盖语义正确 → 详情重取后用户输入原样保留（R9d）。

残余风险：

| # | 风险 | 说明 |
| --- | --- | --- |
| V1 | 声明层未补候选（D-7） | 降级是过渡；候选补齐前，仓库/部门的值真实性无保障 |
| V2 | 邮箱原生校验拦截（D-6） | 用户可能"点了没反应"；建议 `noValidate` 统一到应用文案 |
| V3 | 仅 3 个插件覆盖提交链路 | 逐页验证 dev_workbench / weekly_report / hr_admin；content_ops / meeting_minutes / mail_draft 未逐一提交验证（机制同源，风险低） |
| V4 | 未接真实 IdP | R16 验的是"前端不再预置租户头 + 服务端按令牌解析"，**没有**接真实 OIDC 提供方跑完整登录兑换；`saas + oidc` 端到端需在 V1.1 前补一次 |
| V5 | `upload()` multipart 未实跑 | 仅通过 INV 证明"任何请求都不带字面量 null/undefined 租户头"，未跑真实上传（夹具无文件来源） |
| V6 | 工作区曾有他人未提交改动 | 复验时 `git status` 显示 `.gitignore`、`apps/web/.gitignore`、`apps/web/eslint.config.mjs`、`deploy/.env.example` 有未提交修改（含 ADR-0007 / 租户来源说明，判断为其他成员在处理 D-2 与本轮 lint 忽略规则）。这些改动**不参与前端 bundle**，我未触碰、未回退；如与后续提交冲突请以他们为准 |

## 7. 清理

- 本轮起的 uvicorn（:8000）、`next start`（:3200）、无头 Chrome（:9222）已全部停止；**8000 / 3200 / 9222 均无监听**。
- 停进程方式改为「按端口取 PID → 仅当进程名属于 python/node/chrome 才终止」，避免上一轮误杀同端口的 `wslrelay` / `com.docker.backend`。
- 旧的半成品构建目录已移出仓库（不在 `.next` 内、也不在 eslint 扫描范围内）。
- 产品代码零改动：`git status` 中属于我的只有本报告；`cab5c28` 之后未新增任何源码修改。
