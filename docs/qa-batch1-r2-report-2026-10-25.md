# 第二轮定向回归报告（D-1 修复复验）

| 项目 | 内容 |
| --- | --- |
| 报告编号 | MOY-QA-2026-10-25-R2 |
| 被测分支 | `fix/batch1-frontend-stability` |
| 被测 HEAD | `cab5c28`（父 `683fe03`）——「fix(web): 声明表单异步初始化与空选项下拉降级」 |
| 改动范围 | `apps/web/src/hooks/use-declaration-form.ts`（+104/−7）、`apps/web/src/components/workbench/declaration-form.tsx`（+30/−4），共 2 个文件 |
| 构建方式 | **重新生产构建**（不复用旧 `.next`）：旧产物先改名移出，`npm run build:web` → 新 `BUILD_ID = V6J-o4iTl3QXzETF6J1F-`（旧为 `CV_phQ-oNoMOr4VzBBa3P`），耗时 29s |
| 运行环境 | 适配层桩 :8000 + `next start` :3200 + 无头 Chrome（CDP）:9222 |
| 执行人 | 严过关（QA） |
| 结论 | **PASS**（定向用例 7/7、静态门禁 5/5；新增 1 条 P3 存量发现、1 条 P2 过渡期观察，均不阻塞本批） |

## 0. 结论速览

| 项目 | 结果 |
| --- | --- |
| R9a / R9b / R9b-neg / R9c / R9d / R10a / R1 | **7 / 7 PASS** |
| `POST /api/v1/agent/tasks` 次数（R9b） | **1**（上一轮为 **0**） |
| `POST /api/v1/agent/tasks` 次数（R9c 从市场进入） | **1** |
| `POST /api/v1/agent/tasks` 次数（R10a 合法邮箱） | **1** |
| `POST /api/v1/agent/tasks` 次数（R9b-neg 目标仓库留空） | **0**（必填仍然生效，未被"降级"放过） |
| `/identity/profile` 次数（R1 硬刷新 `/chat`） | **1** |
| 是否仍有标红字段（R9a 初始态） | **否**（`errors=[]`、`aria-invalid` 计数 0） |
| 静态门禁 | `typecheck:web` 0、`lint:web` 0、`scan:pages` 0、`scan:workbench` 0、`scan:banned` 0 |

**构建产物可信性先验校验**：新 `.next/static` 中存在唯一命中 `暂无可选项，请直接填写` 的 chunk（`chunks/0jl4bfhk0wo61.js`），说明复验跑的是**含修复的新产物**，不是旧构建。

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

## 2. 静态门禁（5/5 通过）

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck:web` | **0** |
| `npm run lint:web`（`--max-warnings=0`） | **0** |
| `node scripts/check-page-states.mjs`（`scan:pages`） | **0**（`/ops/metrics`、`/ops/settings` 等三态判定通过） |
| `node scripts/check-workbench-host.mjs`（`scan:workbench`） | **0**（G-27 三项红线全绿：宿主 1 个、110 个源文件 0 命中、插件目录 17 文件 import 全在白名单） |
| `node scripts/check-banned-terms.mjs`（`scan:banned`） | **0**（110 文件 0 命中） |

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

## 4. 本轮新发现（均不阻塞本批）

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

---

## 6. 结论与残余风险

**PASS。** D-1 的三处改动在真实运行时可全部观察到：

1. 异步补默认值 → `分支策略` 显示「新建分支」且不标红（R9a/R9c），**R9c 证明两条到达时序一致**；
2. 空候选降级 → `目标仓库`/`所属部门` 变文本框 + hint，**必填仍生效**（R9b-neg）；
3. 校验前置 `options.length>0` → 降级后的文本值能通过校验并发起任务，**POST 从 0 → 1**（R9b/R9c/R10a）；
4. 覆盖语义正确 → 详情重取后用户输入原样保留（R9d）。

残余风险：

| # | 风险 | 说明 |
| --- | --- | --- |
| V1 | 声明层未补候选（D-7） | 降级是过渡；候选补齐前，仓库/部门的值真实性无保障 |
| V2 | 邮箱原生校验拦截（D-6） | 用户可能"点了没反应"；建议 `noValidate` 统一到应用文案 |
| V3 | 仅 2 个插件覆盖 | 本次逐页验证 dev_workbench / weekly_report / hr_admin；content_ops / meeting_minutes / mail_draft 未逐一提交验证（机制同源，风险低） |
| V4 | 工作区存在他人未提交改动 | 复验时 `git status` 显示 `.gitignore`、`apps/web/.gitignore`、`apps/web/eslint.config.mjs`、`deploy/.env.example` 有未提交修改（含 ADR-0007 / 租户来源说明，判断为其他成员在处理 D-2 与本轮 lint 忽略规则）。这些改动**不参与前端 bundle**，我未触碰、未回退；如与后续提交冲突请以他们为准 |

## 7. 清理

- 本轮起的 uvicorn（:8000）、`next start`（:3200）、无头 Chrome（:9222）已全部停止；**8000 / 3200 / 9222 均无监听**。
- 停进程方式改为「按端口取 PID → 仅当进程名属于 python/node/chrome 才终止」，避免上一轮误杀同端口的 `wslrelay` / `com.docker.backend`。
- 旧的半成品构建目录已移出仓库（不在 `.next` 内、也不在 eslint 扫描范围内）。
- 产品代码零改动：`git status` 中属于我的只有本报告；`cab5c28` 之后未新增任何源码修改。
