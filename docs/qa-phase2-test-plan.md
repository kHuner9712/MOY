# 第二阶段验收测试方案（第 9-16 周）

| 项 | 内容 |
| --- | --- |
| 方案版本 | **v1.3（第二轮裁决落实：门禁提前 B11 + 模板扫描漏检修复）** |
| QA 工程师 | 严过关（Yan） |
| 适用阶段 | 第二阶段（第 9-16 周），批次 B01-B15 / 里程碑 T01-T05 |
| 上游输入 | `docs/prd-phase2-increment.md`（24 条 P0 / 114 条可执行断言）、`docs/architecture-phase2.md`（38 新端点 + 14 新错误码 + 15 批次）、`docs/phase2-progress-audit.md`（基线完成度 22%） |
| 门禁基准 | `docs/qa-report-sprint0.md` v1.0 + `docs/qa-report-sprint0-addendum-v1.1.md` + `docs/qa-blockers-closure.md` v1.0（沿用同一套口径与命令） |
| 代码基线 | `develop` @ `321d4f1`，`apps/adapter/tests/` 421 用例 / 覆盖率 90.95%（阶段一验收值） |
| 本次范围 | **仅方案与用例设计**，不写实现代码、不修改源码、不改动既有用例断言 |
| 状态 | **已按裁决定稿**（裁决落实记录见第 11 章） |

### 修订记录

| 版本 | 日期 | 修订说明 |
| --- | --- | --- |
| v1.0 | 2026-10-21 | 首次发布：范围、25 道门禁（初版 24 道）、7 类风险证明方法、178 条用例矩阵、分层落点、回归策略、分级准出、造数、排期、5 条遗留风险 |
| v1.1 | 2026-10-21 | 按交付总监 5 条裁决定稿：①`11007` 文案改「该知识库暂时无法访问，请联系管理员」；②member 删库改**三分支**；③前端不放行 Playwright + 手工走查改 6 字段留证；④新增 **G-25 `/readyz` db_privilege 自检**（门禁 24→25），`TC-P2-SEC-07` 改写；⑤门禁口径改为**本地复跑为准、CI 就绪待复跑**；⑥人工验收单只出模板、缺资质记「未验证项」；⑦新增第 11 章裁决落实记录。**用例编号与总数（178）不变** |
| v1.2 | 2026-10-21 | ①**纠正硬错误**：`/readyz` 的 `checks` 是 `list[ReadyCheck]` **数组**（`app/schemas/common.py:168-177`），v1.1 误写为对象；G-25、§3.1.3、`TC-P2-SEC-07`、§7.4 全部改为「数组追加元素 + 遍历断言」；②禁词扫描器提前落地并实跑，新增 **第 12 章前置介入记录**（含当前 6 页存量命中 2 处）；③扫描器文件统一命名 `check-banned-terms.mjs`。**用例编号与总数（178）不变** |
| v1.3 | 2026-10-21 | ①**裁决 1**：存量 2 处违规改为「现在修」（工程师 B01-B03 收尾处理，QA 不改代码），§12.3 登记处置；②**裁决 2**：禁词门禁 **G-14/G-15 提前到 B11**（G-16 仍 B15），§2.1/§9.1/§9.2/§12.4 同步；③**校准**：明确「用例总数以 178 为准」；④**校准**：脚本路径保持 `apps/web/scripts/`，补仓库根调用方式；⑤**校准**：模板字符串提取审计，修复 1 处真实漏检（`${}` 内部字面量），自检增至 5 条模板样本；新增 §12.7 记录。**用例编号与总数（178）不变** |

---

## 0. 判据基准与已裁决口径（先看这一节，否则用例会写错）

PRD 与架构存在 5 处判定口径差异，架构第 12.2 章已逐条裁决。**本方案一律以裁决结论为唯一判据**，避免出现「工程师按 PRD 写、QA 按架构判」的扯皮。任何一条若产品侧不认可，必须走契约评审重开，不允许在用例里写"两种都算过"。

| # | 争议点 | PRD 原文 | **本方案采用的判据** | 依据 | 用例影响 |
| --- | --- | --- | --- | --- | --- |
| 1 | 凭据失效错误码 | `11005` | **`11007`**，文案「**该知识库暂时无法访问，请联系管理员**」（交付总监裁决：凭据失效重试无用，文案必须给用户可执行的出路） | 架构 §7.2 裁决 #2 + 交付总监裁决 1.1（PRD 已回写） | TC-P2-CRED-02/04/05 断言 `code=11007` **且 message 逐字相等**，不断言 `11005` |
| 2 | 角色被引用删除 | `20005` | `20005` + 覆盖文案「该角色下还有 N 名成员，请先调整成员角色」 | 架构 §7.1 同码多文案表 | TC-P2-ID02-04 同时断言码值与文案 |
| 3 | 处理中删除文档 | `11006` + 「文档正在处理，请稍后再删除」 | `11006` + 该文案（`ApiError(message=...)` 覆盖） | 裁决 #4 | TC-P2-KB02-06 断言码值 + 精确文案 |
| 4 | 降级生效提示 | 附录 A.3 标注 `15005` 配 HTTP 400 | **HTTP 200 + `code=0`** + `data.pending_change`；`15005` 仅作提示码文本出现 | 裁决 #5 | TC-P2-BL02-03 断言 `status_code=200 且 code=0` |
| 5 | 运营端点鉴权失败 | `16001` | **`20003`**（不新增 `16001`） | 裁决 #6 | TC-P2-OPS-01 断言 `20003` |

另有一条**实现口径**需在用例中显式锁死（不是分歧，但极易做错）：

| 项 | 判据 | 锁死方式 |
| --- | --- | --- |
| `segment_count` 是否出现在文档列表 | **不出现**（裁决 #10）。列表响应无该键；`R4` 详情保留该键 | TC-P2-KB02-07 断言 `"segment_count" not in item` 且 `R4` 中存在 |
| KB3 分页响应体积 | 第 250 页响应体 **< 200KB** | TC-P2-KB02-05 断言 `len(response.content) < 204800` |
| 计量"误差 0" | `sum(按成员) == 企业总量`，**不是 ≤1%**；≤1% 只约束"适配层计数 vs 计量表"的对账 | TC-P2-USG-02 用等号断言；TC-P2-USG-06 用 ≤1% |
| 引用 stale | **HTTP 200 + `code=0` + `data.status=stale`**，不是错误码 | TC-P2-SEG-01 三字段同时断言 |
| 成员能否删除知识库 | **三分支**（交付总监裁决 1.5，覆盖 PRD 6.2 矩阵与架构 4.2 的表述差异）：①member 删**自己创建**的库 → 成功 ②member 删**他人创建**或`organization` 公共库 → `20003` ③`owner`/`org_admin` 删任意库 → 成功 | TC-P2-PERM-02 的三条子断言（`test_permission_matrix.py::test_kb_delete_three_branches`）；不得只测其中一支 |

---

## 1. 测试范围与不测范围

### 1.1 在范围内（必须由用例证明）

| 层 | 范围 | 量化边界 |
| --- | --- | --- |
| 适配层端点 | 38 个新端点（KB 8 / TL 1 / ID 11 / BL 10 / OPS 8）+ 8 处既有端点扩展 | 端点覆盖率 100%（每个端点至少 1 条正向 + 1 条错误路径） |
| 适配层回归 | 冻结的 30 端点（M/R/A/T/U） | 421 条既有用例 0 失败、0 删除 |
| 业务层与数据层 | `app/business/**`、`app/db/**`（本阶段全新） | 行覆盖率 ≥ 85%；25 张表 DDL 与迁移幂等 |
| 隔离与权限 | RLS 事务级语义、三层校验、对象级校验 | 4 角色 × 3 入口矩阵 100% 覆盖 |
| 计量与计费 | `usage_event` / `usage_monthly` 双写、M4 与 BL5 同源、订单/回调幂等、对账 | 结构误差 0；对账偏差 ≤ 1% |
| 前端 | 10 页面（6 深化 + 4 新增）、10 个新基础组件、三态兜底、禁词 | 10/10 页面三态齐备；禁词 0 命中；占位词 0 命中 |
| 工程门禁 | ruff / mypy / pytest / 覆盖率 / OpenAPI 双基线 / `/readyz` 权限自检 / tsc / eslint / build / 依赖 / 镜像 | 见第 2 章 25 道门禁 |

### 1.2 不在范围内（并给出替代证明与残余风险）

| 不测项 | 不测理由 | 替代证明 | 残余风险归属 |
| --- | --- | --- | --- |
| 企业微信 / 飞书 / 微信公众号真实联通 | 无企业侧测试租户、无 admin 授权、公众号绑定不可自动化 | `httpx.MockTransport` 注入真实响应样本（按官方文档字段录制），断言请求头/体/错误映射；真实联通按 `docs/qa-phase2-manual-signoff.md` **模板**执行（本阶段只出模板，资质到位后再跑） | 第 10 章 R-2 |
| 微信支付真实扣款 | 涉及真实资金；测试环境无商户号证书（架构 U-01 已定 `OA_PAY_CHANNEL=fake`） | `FakeGateway`（架构 B09 已规划）实现确定性签名 + 验签；断言金额、幂等、超时、验签失败四类语义；真实扣款**不在验收范围** | 第 10 章 R-2 |
| 真实第三方凭据/密钥到位前的联调 | 无资质 | 阶段验收报告如实写「**未验证项**」，**不得写「通过」**（裁决 5） | 第 10 章 R-2 |
| Dify / Keycloak 真实底座 | 阶段一亦为桩模式；真实底座在本阶段不作为变更对象 | 阶段一 `qa_probe/integration_real.py` 8/8 已验；本阶段只断言"适配层协议转换与错误映射"，不断言底座行为 | 第 10 章 R-5 |
| 检索准确率 / 召回质量 | 无标注数据集，无 ground truth；属算法指标不属验收门禁 | 只断言链路完整性（`status=valid/stale`、字段齐备、引用可回链）与 0 空引用异常 | 明确放弃，不在阶段门考核 |
| 性能压测（首屏 ≤2s、首字 ≤1s） | 无稳定压测环境；架构未把性能列为批次门禁 | 人工实测 3 次取中位数并留 Lighthouse 报告截图归档，**仅作记录不作自动断言** | 第 10 章 R-3 |
| 移动端 / 响应式 / 暗色模式 | PRD 4.1 明确「仅桌面端 1280px 起」「不做暗色」（D-08 已拍板） | 不测 | 无 |
| P1（9 条）/ P2（6 条）需求 | 非阶段门交付 | 仅做冒烟（接口可调用、不 5xx）；不写断言级用例 | 无 |
| 渗透测试 / 等保合规 | 第三阶段范围 | 仅保留阶段一已有的信息泄露金丝雀用例 | 无 |
| 数据迁移（真实客户数据） | 无存量客户库 | 只测「空库 → 全量 → 再跑一次」的幂等性 | 第 10 章 R-4 |

### 1.3 用例总量与分层分布

| 维度 | 数量 | 说明 |
| --- | --- | --- |
| 用例总数 | **178** | 含跨域风险用例 39 条 |
| 单元层（`tests/unit/**`） | 31 | 纯函数、状态映射、加解密、密钥派生、错误映射 |
| 集成层（`tests/integration/**`） | 100 | 端点契约、幂等、鉴权、错误码、并发隔离（其中 **86 条需要 PostgreSQL**，用 `@pytest.mark.pg` 门控） |
| 前端静态与手工（`apps/web/scripts/**` + 走查单） | 47 | 禁词扫描、三态静态断言、DOM 产物扫描、40 格手工走查 |
| 覆盖 PRD 可执行断言 | 114 / 114 | 24 条 P0 全部映射，见 4.7 |

---

## 2. 质量门禁（沿用阶段一 + 本阶段新增，逐条给命令与阈值）

### 2.1 门禁总表（25 道，全部阻断）

| 编号 | 门禁项 | 命令 | 阈值 / 判据 | 来源 | 生效批次 |
| --- | --- | --- | --- | --- | --- |
| G-01 | ruff lint | `cd apps/adapter && ruff check .` | 退出码 0，0 error | 阶段一沿用 | B01 |
| G-02 | ruff format | `cd apps/adapter && ruff format --check .` | 0 files would be reformatted | 阶段一沿用 | B01 |
| G-03 | mypy | `cd apps/adapter && mypy` | `Success: no issues found`，0 error 0 warning | 阶段一沿用 | B01 |
| G-04 | pytest 全量 | `cd apps/adapter && pytest tests -q` | `failed=0 and error=0 and skipped=0` | 阶段一沿用（阶段一为 0 skipped，本阶段维持） | B01 |
| G-05 | 覆盖率 | `pytest tests -q --cov=app --cov-report=term-missing --cov-fail-under=90.5` | 总覆盖率 **≥ 90.5%**（阶段一 90.95%，允许 0.45pp 回落）；`app/business/**` + `app/db/**` **≥ 85%** | 新增 | B01 |
| G-06 | 用例总数 | `pytest tests --collect-only -q \| tail -1` | **≥ 599**（421 基线 + 178 新增），且低于基线即红 | 新增 | B01 |
| G-07 | 核心端点基线 | `python deploy/validate_openapi.py` | G1 + G3：`core == 30`，路径集合精确相等 | 新增（D-09） | B02 |
| G-08 | 新域端点基线 | 同上 | G2 + G3：`phase2 == 38` | 新增（D-09） | B02 |
| G-09 | 加性 diff | 同上（G4） | 0 violation：禁删字段、禁改类型/名、禁收窄枚举、禁新增必填请求字段、禁删既有错误码响应 | 新增（D-09） | B02 |
| G-10 | 底座泄漏 | `grep -rniE "dify\|milvus\|litellm\|chat-messages\|5001" apps/web apps/adapter/app/api apps/adapter/app/schemas` | 0 命中（前端与 api/schemas 两层） | 阶段一沿用 | B02 |
| G-11 | 迁移幂等 | `pytest tests/integration/test_migrate.py -q` | 空库连跑两遍均通过；`tables=25`；`billing_plan=3`；`permission_catalog=20` | 新增 | B01 |
| G-12 | RLS fail-closed | `pytest tests/unit/test_db_session.py tests/integration/test_tenant_isolation.py -q -m pg` | 全通过；未设置 `app.tenant_id` 时业务表 `count(*)==0` | 新增 | B01 |
| G-13 | 错误码三处一致 | `python apps/adapter/scripts/check_error_catalog.py` | `errors.py` 码集合 == 架构 §7 表码集合；`business-errors.ts` 键集合 ⊇ `errors.py` 码集合 | 新增 | B03 |
| G-14 | 禁词（源码） | `cd apps/web && npm run scan:banned`（= `node scripts/check-banned-terms.mjs`） | 退出码 0，命中 0 | 新增（**生效批次按裁决 2 提前到 B11**） | **B11** |
| G-15 | 禁词自检 + 词表防漂移 | `node scripts/check-banned-terms.mjs --self-test` 与 `--check-words` | `--self-test` **退出码 1**（12 负样本全命中 + 8 正样本 0 误报 + 5 标识符 0 误报 + 5 模板样本符合预期）；`--check-words` **退出码 0**（词表与 PRD 一致，漂移即红） | 新增（**生效批次按裁决 2 提前到 B11**） | **B11** |
| G-16 | 禁词（DOM/产物）+ 错误码正则 | `node scripts/check-banned-terms.mjs --built` | 10 个路由产物可见文本 0 命中；`\b[1-3]\d{4}\b` 0 命中 | 新增 | B15 |
| G-17 | 占位词 | `node scripts/check-no-placeholders.mjs` | 「暂不支持」「敬请期待」「敬请期待后续版本」共 0 命中 | 新增（PRD G2-5 一票否决） | B14 |
| G-18 | 前端三态静态断言 | `node scripts/check-page-states.mjs` | 10/10 页面同时引用 `LoadingState`/`EmptyState`/`ErrorState` 且 `ErrorState` 传入 `onRetry` | 新增 | B15 |
| G-19 | tsc | `cd apps/web && npm run typecheck` | 退出码 0 | 阶段一沿用 | B11 |
| G-20 | eslint | `cd apps/web && npm run lint` | 0 error / 0 warning（`--max-warnings=0`） | 阶段一沿用 | B11 |
| G-21 | build | `cd apps/web && npm run build` | 成功；路由数 10（业务页）+ `/login` + `/` | 阶段一沿用 | B12 |
| G-22 | 依赖与镜像 | `python deploy/validate_deps.py && python deploy/validate_images.py` | 0 黑名单协议；0 未登记依赖；镜像仓库可拉取 | 阶段一延续（含 P1-5 修正后） | B01 |
| G-23 | 计量对账 | `python apps/adapter/scripts/reconcile_usage.py --period <YYYY-MM> --threshold 0.01` | 偏差 **≤ 1%**，退出码 0；偏差超限写 `audit_log(billing.reconcile_mismatch)` | 新增 | B07 |
| G-24 | P0 用例全通 | `pytest tests -q -m p0`（24 条 P0 的映射用例打 `p0` 标记） | `failed=0`；p0 用例数 ≥ 60 | 新增 | B01 |
| G-25 | **数据库连接权限自检**（启动级） | 用超级用户 DSN 启动：`OA_DATABASE_URL=postgresql://postgres:***@... python -m uvicorn app.main:app` 后 `curl -s localhost:8000/readyz`（`business_store=postgres` 时生效） | `/readyz` 返回 `status=="not_ready"`，且 `checks` **数组**中存在一项 `{"name":"db_privilege","ready":false,"detail":"数据库连接权限配置不正确"}`（`checks` 为 `list[ReadyCheck]`，**不得改成对象/字典**）；换成 `officeai` 用户 DSN 后 `status=="ready"` 且同名项 `ready==true` | 新增（交付总监裁决 3 + 2026-10-21 结构纠正，纳入 B01 验收） | B01 |

### 2.2 门禁执行顺序（本地门禁脚本，逐条串行）

```
lint/format/mypy → 单元测试 → 迁移幂等 → /readyz 权限自检 → 集成测试(-m "not pg")
→ 集成测试(-m pg --run-pg) → 覆盖率 → OpenAPI 双基线 → 错误码三处一致 → 依赖/镜像 → 对账
前端：typecheck → lint → build → 禁词(源码) → 禁词自检 → 禁词(产物) → 占位词 → 三态静态断言
```

任一项红即阻断合入。`pg` 组的要求是：**默认 `pytest`（不带 `--run-pg`）跳过 PG 用例；带 `--run-pg` 时必须 `0 skip / 0 fail`**（防止"本地跳过、事后也跳过"的假绿）。

### 2.3 门禁执行环境（本轮以本地复跑为准）

> **背景**：本仓库 GitHub Actions 额度已于本月用尽，CI 实际不会运行。为避免阶段验收时出现"没跑 CI 算不算通过"的争议，本轮统一口径如下。

| 项 | 口径 |
| --- | --- |
| 判定依据 | **本地门禁脚本全绿**（`ruff` / `ruff format --check` / `mypy` / `pytest` / `deploy/validate_openapi.py` / `node scripts/*.mjs` / `next build`），由 QA 在本机逐条执行并在验收报告附**命令 + 退出码 + 关键输出片段** |
| CI 的地位 | 「**就绪待额度恢复后可复跑**」：配置文件与步骤不得删除或跳过，作为交付物的一部分交付；恢复额度后按同一顺序复跑一次，结果应与本地一致 |
| 措辞要求 | 验收报告中禁止写「CI 绿」；应写「本地门禁 G-01~G-25 全绿（附输出），CI 配置就绪待复跑」 |
| 例外 | 若本地无法执行的门禁（如镜像可拉取性依赖网络），标注为「未执行 + 原因」，不得写成「通过」 |

### 2.4 豁免流程（受控，且不可豁免项明确）

| 项 | 规则 |
| --- | --- |
| 可豁免范围 | 仅 P2 级门禁项（G-05 单模块覆盖率、G-11 表数量断言、G-23 对账阈值的临时放宽） |
| **不可豁免** | G-07/G-08/G-09（契约基线）、G-12（租户隔离）、G-13（错误码一致性）、G-14/G-15/G-16/G-17（禁词与占位词）、G-24（P0 用例）、**G-25（数据库权限自检，裁决 3）** |
| 形式 | 写入 `docs/qa-phase2-waivers.md`：门禁编号 / 理由 / 补偿措施 / 到期批次 / 主理人签字，缺任一项无效 |
| 有效期 | 单批次，跨批次自动失效 |

---

## 3. 风险驱动的测试策略（7 类）

> 本章每条风险都回答三个问题：**为什么常规测试抓不到** / **怎么证明（可执行）** / **判据是什么（可断言）**。所有证明方法均已排除"再点一遍看是否正常"。

### 3.1 多租户隔离（最高危）

#### 3.1.1 风险描述

RLS 依赖 `SELECT set_config('app.tenant_id', :tid, TRUE)` 的**事务级**语义。若写成 `FALSE`（会话级）或不在事务第一条语句执行，则 `current_setting` 的值会**残留到连接池复用的下一个请求**，造成 A 企业读到 B 企业数据。架构 §3.5 已明确策略为 `FORCE ROW LEVEL SECURITY` + `fail-closed`（未设置时查不到任何行）。

#### 3.1.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 单请求测试天然隔离 | 每个 TestClient 请求独立建连、独立事务，`FALSE` 与 `TRUE` 行为完全一致，用例全绿 |
| 只在并发/复用下暴露 | 需要「池大小压到 1 + 交替租户」才能复现；默认 `pool_size=10` 时请求落在不同连接上，问题被掩盖 |
| 断言方式错 | 只断言"响应 200 且列表非空"永远发现不了串租户；必须断言"返回行里不存在另一租户的哨兵值" |
| 静态检查缺席 | 代码评审会漏 `is_local` 参数；需要用可执行断言把"必须 TRUE"钉死 |

#### 3.1.3 证明方法（4 层，层层可执行）

**证明 A：事务级复位（最小可复现单元）**

```python
# tests/unit/test_db_session.py::test_set_config_is_transaction_local
async with engine.connect() as conn:
    async with conn.begin():
        await conn.execute(text("select set_config('app.tenant_id', :t, TRUE)"), {"t": "tnt_a"})
        assert (await conn.execute(text("select current_setting('app.tenant_id', TRUE)"))).scalar() == "tnt_a"
    # 事务已结束，同一连接上再查：必须为空串（复位），而不是 'tnt_a'
    assert (await conn.execute(text("select current_setting('app.tenant_id', TRUE)"))).scalar() in ("", None)
```

判据：复位后值为 `""` 或 `NULL`；若仍为 `tnt_a` → 该实现用了会话级语义，直接判 P0。

**证明 B：同连接连续服务两个租户（池压到 1）**

用 `create_async_engine(dsn, pool_size=1, max_overflow=0, pool_pre_ping=True)` 建独立引擎，顺序发起「A 建库 → B 列库 → A 列库」：
判据：B 的列表**不含** A 的哨兵库名 `SENTINEL_TNT_A_KB`；A 的列表**必含**该库；且 B 的响应文本中 `SENTINEL_TNT_A_KB` 出现次数为 0。

**证明 C：并发 20 请求交替租户（压测式断言）**

```python
# tests/integration/test_tenant_isolation.py::test_concurrent_interleaved_tenants_no_leak
async def one(i: int) -> tuple[str, list[str], str]:
    tid = "tnt_a" if i % 2 == 0 else "tnt_b"
    r = await client.get("/api/v1/knowledge/bases", headers=headers_for(tid))
    return tid, [x["name"] for x in r.json()["data"]["items"]], r.text

for _ in range(5):                      # 5 轮
    results = await asyncio.gather(*[one(i) for i in range(20)])
    for tid, names, raw in results:
        assert all(n.startswith(SENTINEL[tid]) for n in names)   # 每条只含本租户
        assert SENTINEL[other(tid)] not in raw                   # 响应体无对端哨兵
```

判据：20 请求 × 5 轮 = 100 次采样，跨租户行数 **0**，对端哨兵字符串出现次数 **0**。

**证明 D：静态守卫（防止有人把 TRUE 改成 FALSE）**

```python
# tests/unit/test_db_session.py::test_set_config_must_be_transaction_local
src = Path("app").rglob("*.py")
hits = [p for p in src if re.search(r"set_config\(\s*['\"]app\.tenant_id['\"]\s*,.*?(FALSE|false)",
                                    p.read_text(encoding="utf-8"), re.S)]
assert hits == [], f"发现会话级 set_config：{hits}"
assert len(re.findall(r"set_config\(\s*['\"]app\.tenant_id['\"]", SESSION_SRC)) == 1
```

判据：会话级写法命中 0；`set_config` 在 `app/db/session.py` 中**只出现 1 次**且位于 `tenant_scope` 事务开启后的第一条语句。

**证明 E：fail-closed、平台哨兵守卫与启动级权限自检**

- 不开 `tenant_scope` 直接 `select count(*) from knowledge_base_meta` → 必须 `0`（不是报错、不是全量）。
- 非 `ops` 路由传 `allow_platform=True` → 必须抛 `30003`，且 `select count(*)` 仍为 `0`。
- **连接身份双层校验**（交付总监裁决 3，升级为硬要求）：
  1. **用例内直查**：`select current_user` 必须是 `officeai`；`select rolbypassrls from pg_roles where rolname=current_user` 必须为 `false`。
  2. **启动级自检（真正的防线）**：`business_store=postgres` 时，`/readyz` 必须新增一项 `ReadyCheck`，`name="db_privilege"`，执行 `SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user`；
     - 结果为 `true` → `status="not_ready"`，该检查项 `ready=false`、`detail="数据库连接权限配置不正确"`；
     - 结果为 `false` → `status="ready"`，该检查项 `ready=true`。
     > **结构红线（2026-10-21 纠正）**：`checks` 是 **`list[ReadyCheck]` 数组**（`apps/adapter/app/schemas/common.py:168-177`），**不是字典**。只能在数组里**追加元素**，不得把 `checks` 改成对象——否则破坏阶段一已冻结的 `/readyz` 契约与既有测试。
     > 为什么必须做启动自检：用例只能约束"测试时用的 DSN"，约束不了运维实际部署的 DSN；把校验放进 `/readyz` 才能让错误配置在**部署时**暴露，而不是在客户环境里静默全表可读。契约形态（追加元素，其余不变）：
     ```json
     { "status": "not_ready",
       "checks": [
         { "name": "db",           "ready": true,  "detail": "" },
         { "name": "db_privilege", "ready": false, "detail": "数据库连接权限配置不正确" }
       ] }
     ```
     断言写法（按数组遍历，不按字典取键）：
     ```python
     data = client.get("/readyz").json()["data"]
     assert data["status"] == "not_ready"
     assert any(c["name"] == "db_privilege" and c["ready"] is False
                and c["detail"] == "数据库连接权限配置不正确" for c in data["checks"])
     ```

#### 3.1.4 用例与判据

| 编号 | 覆盖点 | 通过判据（可断言） |
| --- | --- | --- |
| TC-P2-SEC-01 | 事务级复位 | 提交后 `current_setting` 为 `""`/`NULL` |
| TC-P2-SEC-02 | 池=1 交替两租户 | 对端哨兵出现 0 次；本租户哨兵必现 |
| TC-P2-SEC-03 | 并发 20×5 轮交替 | 跨租户行 0 条；失败即 P0 |
| TC-P2-SEC-04 | 未设租户变量 fail-closed | 业务表 `count(*)==0`，不抛未捕获异常 |
| TC-P2-SEC-05 | 静态守卫 | 会话级 `set_config` 命中 0；出现次数 == 1 |
| TC-P2-SEC-06 | 平台哨兵守卫 | 非 ops 传 `__platform__` → `30003` 且查询仍 0 行 |
| TC-P2-SEC-07 | **启动级数据库权限自检**（裁决 3 改写；`checks` 按**数组**断言） | ①以超级用户 DSN 启动 → `/readyz` `status=="not_ready"`，且 `any(c["name"]=="db_privilege" and c["ready"] is False and c["detail"]=="数据库连接权限配置不正确" for c in data["checks"])` 为真；②以 `officeai` DSN 启动 → `status=="ready"` 且同名项 `c["ready"] is True`；③同用例内直查 `rolbypassrls==false` 与 `current_user=='officeai'`（三断言全过）；④`checks` 必须是 JSON 数组（`isinstance(data["checks"], list)`），且既有 `db` 项仍在（向后兼容） |

### 3.2 per-kb 凭据不回落（安全事故级）

#### 3.2.1 风险描述

知识库 A 绑定凭据 `c1` 后，若解析失败（缺失 / `status=revoked` / 解密失败 / 密钥版本不存在）而代码回落到全局 `settings.dify_default_app_key`，则 A 的文档会被写进**全局凭据对应的底座空间**，与所有未绑定库混在一起 → 跨库串用、数据互相可见。架构 D-10 定为「宁可整库不可用，绝不串库」。

#### 3.2.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 只断言状态码不够 | 断言 `code==11007` 时，代码可能"先回落全局调通、再抛出 11007"，状态码一样对，但下游已被污染 |
| 日志与响应另需检查 | 密钥可能出现在 structlog 日志、异常 `internal_detail`、或 `data` 的调试字段里；只看 HTTP body 会漏 |
| 兼容路径掩盖 | 未绑定库本来就走全局凭据（正确行为），测试若只测未绑定库，看起来"凭据机制已生效" |
| 幂等与限流顺序 | 若在限流/幂等前解密，重复请求会重复解密（性能与密钥暴露面），架构 §1.4 明确要求解密在幂等 factory 内 |

#### 3.2.3 证明方法（计数断言 + 金丝雀）

**证明 A：下游调用次数必须为 0（核心）**

```python
# tests/unit/test_credential_service.py::test_bound_failure_never_falls_back
calls = []
def handler(request: httpx.Request) -> httpx.Response:
    calls.append(request)                            # 记录任何一次下游请求
    return httpx.Response(200, json={})

client = DifyClient(transport=httpx.MockTransport(handler), api_key=GLOBAL_CANARY)
monkeypatch.setattr(settings, "dify_default_app_key", GLOBAL_CANARY)   # 全局值设为金丝雀
seed_credential(base_id="kb_a", status="revoked")                      # 已绑定但不可用

with pytest.raises(ApiError) as exc:
    await rag_service.upload_document("kb_a", file=..., auth=auth)
assert exc.value.code == ErrorCode.KB_CREDENTIAL_UNAVAILABLE          # 11007
assert len(calls) == 0                                                # 一次下游都没打
assert GLOBAL_CANARY not in json.dumps(last_response_body)            # 未回落全局
```

判据：`calls == []`、`code == 11007`、`message == "该知识库暂时无法访问，请联系管理员"`（逐字，防文案漂移）、全局金丝雀字符串在响应体中出现 0 次。

**证明 B：明文零泄露（三面扫描）**

对以下三处统一扫描金丝雀明文 `PLAINTEXT-CANARY-7f3a`：① 响应 `response.text`；② 捕获的日志 `caplog.text` / structlog 输出；③ `select cipher_text, nonce from credential_store` 的字节内容。
判据：三处命中均为 **0**；且日志中出现的 `credential_ref` 只能是**后 6 位**（正则 `ref_suffix=[0-9a-z]{6}\b` 命中 1 次，完整 ref 命中 0 次）。

**证明 C：轮换即时生效（不重启）**

`PUT /credential-binding` 从 `c1` 换到 `c2` 后，**下一个请求**的 `Authorization: Bearer` 头必须是 `c2` 的值：
判据：`calls[-1].headers["authorization"] == "Bearer c2_plaintext"`（通过 MockTransport 断言），且 `oa:cred:v1:{tenant}:{ref}` 缓存键已被 `DEL`（用 Valkey 客户端 `exists` 断言为 0）。

**证明 D：故障不扩散**

A 绑 `c1`（置无效）、B 未绑：调 A 的 R3/R7 → 11007；同一时刻调 B 的 R3/R7 → `code=0` 且成功。
判据：A 两次均 11007、B 两次均 0；A 的下游调用计数 0、B 的调用计数 ≥1。

**证明 E：解密时机在幂等之后**

同一 `Idempotency-Key` 连发两次（同 body）：第二次直接回放首次结果。
判据：解密函数被调用 **1 次**（用 `monkeypatch.setattr(CredentialResolver, "resolve", wrapper_with_counter)` 计数）；下游调用 1 次。若为 2 次 → 违反架构 §1.4，判 P1。

**证明 F：接口与前端零暴露**

- `PUT /credential-binding` 用企业管理员 token 调用 → 必须 20003（仅 `ops:console:access` 可调）。
- 前端源码与 10 页面 DOM 中 `credential` / `api_key` / `dify` / `凭据` / `密钥` 命中 0（由 G-14/G-16 覆盖）。

#### 3.2.4 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-CRED-01 | 未绑定走全局（兼容） | 下游 `authorization == Bearer <global>`；`code=0` |
| TC-P2-CRED-02 | 已绑定解析失败 | `code=11007`；`message=="该知识库暂时无法访问，请联系管理员"`（逐字）；下游调用 **0**；全局金丝雀 0 命中 |
| TC-P2-CRED-03 | 明文零泄露 | 响应/日志/DB 三面金丝雀 0 命中；日志只含 ref 后 6 位 |
| TC-P2-CRED-04 | 故障不扩散 | A=11007 且 0 次下游；B=200 且 ≥1 次下游 |
| TC-P2-CRED-05 | 轮换即时生效 + 任务可重试 | 下一请求头为 `c2`；缓存键已删；进行中任务 11007 后重试成功 |
| TC-P2-CRED-06 | 解密在幂等之后 | `resolve` 调用 1 次；下游 1 次（重复请求场景） |
| TC-P2-CRED-07 | KB8 鉴权与零暴露 | 企业 token → 20003；ops token → 200；前端 0 命中 |

### 3.3 计量对账一致性

#### 3.3.1 风险描述

M4（`/model/usage`）与 BL5（`/billing/usage`）若读不同存储，用户会在两个页面看到不同数字；流式 `message_end` 前逐 delta 结算会导致重复累加；中断不结算会导致漏计；幂等重放会重复计费。架构 D-12 的解法是「唯一真相源 `usage_monthly` + 同事务双写（tenant 行 + member 行）」，因此**按成员求和 = 企业总量在结构上必然为 0 误差**。

#### 3.3.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 两个端点分别断言 | 各自断言"数值 > 0"永远发现不了不一致；必须**同一时刻取两次快照比对** |
| 误差因聚合路径不同而出现 | 若 `group_by=member` 走 `usage_event` 聚合、总量走 `usage_monthly`，两者会漂移；必须用**同一时刻、同一张表**比对 |
| 流式边界难构造 | 需要可控 SSE 发生器（正常结束 / 中途断 / 客户端断开）才能覆盖 3 种结算路径 |
| 中断折算未定义 | 若不写死公式，`0 < units < 完整值` 这条断言无法执行；本方案采用架构 §2.5 的折算公式 |

#### 3.3.3 证明方法

**证明 A：同刻同值（M4 与 BL5）**

```python
# tests/integration/test_usage_endpoints.py::test_m4_and_billing_usage_are_same_snapshot
m4 = client.get("/api/v1/model/usage?period=month", headers=h).json()["data"]
bl5 = client.get(f"/api/v1/billing/usage?period={period}", headers=h).json()["data"]
assert m4["used_units"] == bl5["quota_units_used"]          # 完全相等，不是近似
# 再做一次生成，两个端点必须同步增加同一增量
delta_m4 = m4_before_after_diff; delta_bl5 = bl5_before_after_diff
assert delta_m4 == delta_bl5 == expected_units
```

**证明 B：按成员求和 = 企业总量（DB + API 双断言，误差 0）**

```sql
-- tests/integration/test_usage_reconcile.py::test_member_sum_equals_tenant_row
SELECT (SELECT units FROM usage_monthly
         WHERE tenant_id=:t AND period=:p AND scope_type='tenant' AND scope_id=:t AND capability='chat')
     - (SELECT COALESCE(SUM(units),0) FROM usage_monthly
         WHERE tenant_id=:t AND period=:p AND scope_type='member' AND capability='chat') AS diff;
```

判据：`diff == 0`（SQL 断言），且 `sum(x["units"] for x in breakdown) == quota_units_used`（API 断言，等号）。

**证明 C：流式三种边界**

| 边界 | 构造方法 | 判据 |
| --- | --- | --- |
| 正常结束（`message_end`） | MockTransport 返回完整 SSE：3 个 `message` + 1 个 `message_end{usage.total_tokens:900}` | `usage_event` **恰好 1 条**，`units=900`，`source='settled'`；不是 4 条 |
| 中途断开 | SSE 生成器 yield 3 个 delta 后 `raise ConnectionError` | 1 条 `source='estimated'`；`0 < units < 900`；`units == prompt_units + ceil(len(text)/1)` |
| 客户端断开 | 读到第 2 个 delta 后关闭 StreamResponse 的 `receive` | 同上；且 `finally` 块被执行（用事件标记断言） |

**证明 D：幂等重放不重复计**

同一 `generation_id` 连续两次 `record()`（或同一 `Idempotency-Key` 重放 M1）：
判据：`select count(*) from usage_event where tenant_id=:t and capability='chat'` 增加 **1**（不是 2）；`usage_monthly` 的 `units` 增量等于单次值。

**证明 E：同事务原子性**

注入故障：在 `usage_monthly` 第 2 次 upsert 时抛错（monkeypatch 仓储方法）。
判据：`usage_event` 表中**无**该 `idem_key` 的行（整体回滚），`usage_monthly` 无增量 → 不出现"事件写了、聚合没写"的半写状态。

**证明 F：对账任务**

`python apps/adapter/scripts/reconcile_usage.py --period <p> --threshold 0.01`：
判据：退出码 0；输出 `deviation <= 0.01`；人为把 Valkey 计数 +20% 后重跑 → 退出码非 0 且 `audit_log` 出现 `billing.reconcile_mismatch`。

#### 3.3.4 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-USG-01 | M4 与 BL5 同刻同值 | `used_units == quota_units_used`；增量相等 |
| TC-P2-USG-02 | 成员求和 = 企业总量 | SQL `diff == 0`；API 求和等号成立 |
| TC-P2-USG-03 | `message_end` 单次结算 | `usage_event` 恰好 1 条，`source='settled'`，units 等于下游 usage |
| TC-P2-USG-04 | 中断折算 | `source='estimated'`；`0 < units < 完整值`；按公式精确相等 |
| TC-P2-USG-05 | 幂等重放 | 事件数 +1；聚合增量等于单次值 |
| TC-P2-USG-06 | 对账偏差 | ≤ 1%；超限时非 0 退出且写审计 |
| TC-P2-USG-07 | 同事务原子性 | 故障注入后两表均无残留（无半写） |

### 3.4 引用回链的 stale 语义

#### 3.4.1 风险描述

重新处理文档后 `segment_id` 必变，历史引用必然失效。若实现返回 404/11004，用户看到的是错误；PRD 要求"不报错、展示旧版本提示"。架构 D-11 用内容哈希稳定键 + 三态机（`valid/stale/deleted`），且**stale 只在"新 run 处理成功"时标记**。

#### 3.4.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 只测 happy path | 常规用例只查"能取到片段"，不会去重处理再查旧引用 |
| 状态机有三个易错分支 | ①处理**中**不得标 stale；②处理**失败**不得标 stale；③删除才转 deleted。少写一个分支，用户就会在处理中/失败时丢引用 |
| HTTP 语义易写错 | 用 404 或 410 返回 stale 最"自然"，但违反 PRD 明确要求（200 + status） |

#### 3.4.3 证明方法（状态机全覆盖）

| 步骤 | 操作 | 期望 DB 状态 | 期望 API |
| --- | --- | --- | --- |
| S1 | R7 检索命中 → 异步回填 | `segment_map.status='valid'`，`segment_key` 有值 | KB4 → 200，`status='valid'`，`content` 非 null |
| S2 | R5 重新处理（run_id 换新，处理中） | **旧行仍 `valid`** | KB4 → 200，`status='valid'`（处理中不得失效） |
| S3 | 新 run 成功（`document_meta.stage=completed` 同事务提交） | 旧 run 全部 `stale`，`stale_at` 非空 | KB4 → **200 + code=0 + `status='stale'`** |
| S4 | 另建文档，R5 重处理**失败** | 旧行仍 `valid` | KB4 → 200，`status='valid'` |
| S5 | R6 删除文档 | `status='deleted'`（或级联物理删） | KB4 → 200 + `status` 或 11004（文档已删），前端显示「该引用已不可查看」 |
| S6 | 删除知识库 | 级联删除 | KB4 → `11001`；`GET /knowledge/bases` 不含该库 |
| S7 | 超过 90 天保留期（用 `stale_at` 回拨 SQL 模拟） | 清理任务物理删 | KB4 → 200 + `status='stale'` + `content=null` |

同时断言**稳定性口径**：`segment_key == "seg_" + sha256(f"{doc_id}|{index}|{content_sha256}")[:16]`；同内容同槽重复回填 → 键不变（幂等）；内容改一个字 → 键变。用 `tests/unit/test_segment_service.py::test_build_key_is_stable_and_content_sensitive` 断言（含空白归一：前后空格与连续空白不得改变键）。

#### 3.4.4 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-SEG-01 | 重处理成功 → stale | `status_code==200 and code==0 and data.status=='stale'`；`stale_at` 非空 |
| TC-P2-SEG-02 | 重处理中 / 失败不标 stale | 两场景 `status=='valid'`；DB 中 `stale_at is null` |
| TC-P2-SEG-03 | 删除文档 | `status=='deleted'` 或 11004；前端文案「该引用已不可查看」 |
| TC-P2-SEG-04 | 删除知识库 | KB4 → `11001`；列表不含；文档接口 `11001` |
| TC-P2-SEG-05 | 超保留期 | `status=='stale'` 且 `content is None`，HTTP 仍 200 |
| TC-P2-SEG-06 | 稳定键口径 | 同内容同键、内容变更键变；空白归一后键不变 |

### 3.5 权限三层校验（对象级，红线）

#### 3.5.1 风险描述

PRD 6.3 红线原文点名的形态是「**列表过滤了但详情接口没校验**」。这类缺陷在功能测试里表现为"一切正常"，在安全测试里表现为"改个 URL 就拿到别人的数据"。本阶段新增 38 个端点、4 类资源、两层隔离（租户 + 组织/授权），笛卡尔组合达数百格，必须用矩阵穷举。

#### 3.5.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 正向用例天然通过 | 有权限的账号调哪个入口都对，缺陷只在"无权限账号 + 直接入口"暴露 |
| 列表过滤会"看起来正确" | 未授权库不出现在列表 → 人以为权限做完了；漏掉 `GET .../documents` 与 `R7` 两个直达入口 |
| 错误码易被写成 404 | 返回 404 也算"拿不到数据"，但违反契约（应为 `20003`），且会给攻击者"资源是否存在"的探测信号 |
| 缓存让时序错乱 | 授权撤销后 ≤5s 内应失效；若缓存 TTL 写大，手工测（刷新快）看起来已生效，自动化 5s 内复测才暴露 |

#### 3.5.3 证明方法：知识库资源 × 角色 × 入口 笛卡尔矩阵

前置数据：`kb_r`（`visibility=restricted`，owner=U_owner）、`kb_o`（`visibility=organization`）。授权：`usr_member_b` 获 `viewer`、`usr_member_c` 获 `editor`、`usr_guest_d` 获 `viewer`。所有账号同租户。

| 账号（角色 / 授权） | 列表 `GET /knowledge/bases` | 详情 `GET .../documents`（KB3） | 检索 `POST /knowledge/search`（R7） | 问答 `M1` 携带该库 | 上传 `R3` | 删除库 `KB2` | 授权 `KB6` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| owner | ✅ 含 kb_r | ✅ 200 | ✅ 200 | ✅ 有引用 | ✅ 200 | ✅ 200 | ✅ 200 |
| org_admin | ✅ 含 kb_r | ✅ 200 | ✅ 200 | ✅ 有引用 | ✅ 200 | ✅ 200 | ✅ 200 |
| member B / viewer | ✅ 含 kb_r | ✅ 200 | ✅ 200 | ✅ 有引用 | ⛔ 20003 | ✅ 自己创建的 / ⛔ 20003 他人或公共 | ⛔ 20003 |
| member C / editor | ✅ 含 kb_r | ✅ 200 | ✅ 200 | ✅ 有引用 | ✅ 200 | ✅ 自己创建的 / ⛔ 20003 他人或公共 | ⛔ 20003 |
| member 未授权 | ⛔ **不含** kb_r | ⛔ **20003** | ⛔ **20003** | ✅ 200 但**静默跳过该库、引用中无 kb_r** | ⛔ 20003 | ⛔ 20003 | ⛔ 20003 |
| guest D / viewer | ✅ 含 kb_r | ✅ 200 | ✅ 200 | ✅ 有引用 | ⛔ 20003 | ⛔ 20003 | ⛔ 20003 |
| guest 未授权 | ⛔ 不含 | ⛔ 20003 | ⛔ 20003 | ✅ 静默跳过 | ⛔ 20003 | ⛔ 20003 | ⛔ 20003 |
| 他租户 owner（跨租户） | ⛔ 不含（且 20003 亦可） | ⛔ **20003** | ⛔ **20003** | — | ⛔ 20003 | ⛔ 20003 | ⛔ 20003 |

**三条硬判据**（任一不满足即 P0）：
1. 「详情」列在无权限账号下必须是 **`code=20003`**，不得是 `404`、`403`、`200 + 空列表`。
2. 「问答」列必须是 **HTTP 200 + `code=0`**，且 `citations` 中 `base_id` 不含未授权库（静默跳过，不报错）。
3. 「列表」列与「详情」列结论必须一致（列表不可见 ⟺ 详情 20003），这是"列表过滤但详情未校验"的直接检测式。

**「删除库」列的第四项硬判据（交付总监裁决 1.5，三分支必须全部断言）**：
- `member` 删**自己创建**的库 → `code=0`，且库从列表消失、审计有记录（分支 a）；
- `member` 删**他人创建**的库 → `code=20003`（分支 b）；
- `member` 删 `visibility=organization` 的公共库（非自己创建）→ `code=20003`（分支 c，与 b 分开断言，避免用一条 `in (20003)` 掩盖）；
- `owner` / `org_admin` 删任意库 → `code=0`（分支 d，回归）。
> 裁决理由记录：成员若不能清理自己建的库会持续产生废弃库，属产品缺陷；因此 **`knowledge:base:delete` 的对象级判定必须包含 owner_user_id 比较**，不能一律按角色黑名单拒绝。

同一矩阵在**工具连接**与**账单**上再做两遍（资源不同、入口不同）：

| 资源 | 入口 | owner | org_admin | member | guest |
| --- | --- | --- | --- | --- | --- |
| 工具连接 T3/T4 | 列表 / 断开他人连接 / 断开自己连接 | ✅/✅/✅ | ✅/✅/✅ | 仅自己/⛔20003/✅ | 仅被授权/⛔20003/✅（PRD 8 号权限点 🔸） |
| 账单 BL2/BL7/BL8 | 本企业订阅 / 他人订单详情 / `/billing/usage?group_by=member` | ✅/✅/✅ | ✅/✅/✅ | 20003 或个人视图/20003/20003 | 同 member（🔸 仅个人） |
| 运营 `/ops/**` | 8 个端点全部 | 20003 | 20003 | 20003 | 20003（仅 `ops_operator` 通过） |

#### 3.5.4 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-PERM-01 | 知识库 8 账号 × 3 入口 | 矩阵 24 格逐格断言（参数化，每格一条 assert） |
| TC-P2-PERM-02 | 知识库写入口 8 账号 × 4 写操作 + **删库三分支** | 32 格逐格断言 `code` 精确匹配；删库另加 4 条分支断言（member 删自己的→`code=0`；member 删他人的→`20003`；member 删公共库→`20003`；org_admin 删任意→`code=0`），落点 `test_permission_matrix.py::test_kb_delete_three_branches` |
| TC-P2-PERM-03 | 工具连接 4 角色 × 3 入口 | 12 格；"断开他人连接"必须 20003 |
| TC-P2-PERM-04 | 账单 4 角色 × 3 入口 | 12 格；member 看不到企业视图 |
| TC-P2-PERM-05 | 运营 8 端点 × 企业 token | 8 条全 `20003`；HTTP 状态 403 或 200 均可，`code` 必须 20003 |
| TC-P2-PERM-06 | 跨租户直连 | token tenant 与资源 tenant 不一致 → 20003（不得 404） |
| TC-P2-PERM-07 | 对象级反例防护 | 「列表不含」且「详情 20003」同时成立；断言 `response.status_code != 404` |
| TC-P2-PERM-08 | 缓存 5s 上限 | 撤销授权后 5s 内复测 → 已失效；用 `iter_with_deadline(5.0)` 断言首次成功时刻 ≤5s |

### 3.6 弱网 / 异常兜底（体验红线）

#### 3.6.1 风险描述

10 个页面 × 4 类异常（断网 / 500 / 超时 / 401 过期）任何一格出现白屏、英文、错误码、无重试，都是「首版即商用」的硬伤。当前仓库**无前端测试框架**（`package.json` 只有 dev/build/start/lint/typecheck），且 GitHub Actions 额度已用尽（CI 不运行），引入新框架只会增加一条无法自动验证的依赖 → 按**交付总监裁决 2**，本阶段**不放行 Playwright/Vitest**，改用**静态断言 + 结构化留证走查**双轨。

#### 3.6.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| build 通过 ≠ 有错误态 | `next build` 只证明能编译；`/chat` 至今没有 `ErrorState`，build 一直是绿的 |
| 只测接口异常不够 | 接口 500 时前端可能静默 catch 后渲染空列表 → 用户看到"没有数据"而不是"加载失败" |
| 文案扫描漏动态拼接 | 静态扫源码发现不了 `{err.message}` 直接把后端英文/错误码渲染出来 |
| 401 与 20003 处理路径不同 | 401 应跳登录、20003 应展示权限文案；混为一谈会导致"权限不足也把你踢出登录" |

#### 3.6.3 证明方法 A：静态断言（自动，G-18）

`apps/web/scripts/check-page-states.mjs`（Node 内置 `fs`，无新依赖）：对下列 10 个页面文件做 AST 级近似检查（正则 + 括号平衡已足够，因为模式固定）：

| 检查项 | 规则 |
| --- | --- |
| 三态引用 | 文件内必须同时出现 `LoadingState`、`EmptyState`、`ErrorState` 三个标识符 |
| 重试绑定 | `ErrorState` 出现处附近必须出现 `onRetry`（同一 JSX 块内） |
| 无裸渲染错误 | 禁止出现 `{error.message}` / `{err.code}` / `String(e)` 直接渲染模式 |
| 禁止原生 `fetch` | 页面与组件内 `fetch(` 出现次数为 0（唯一出口 `api-client.ts`） |

判据：10/10 通过；任一页面缺失即红。

#### 3.6.4 证明方法 B：逐页手工走查表（40 格，留证）

**执行方式**：`npm run build && npm start -p 3100`，Chrome DevTools 注入（Offline / 响应替换 / 延时 / token 篡改）。走查单落点为 `apps/web/docs/qa-manual-checklist-phase2.md`，**每格必须填 6 个字段**（缺一即视为未执行）：

| 字段 | 填写要求 |
| --- | --- |
| 页面 | 路由原样（如 `/knowledge/kb_x`） |
| 场景 | `断网` / `500` / `超时` / `401 过期` 之一 |
| 操作 | 可复现的最小步骤（如「进入页面 → 等待 3s → 点重试」） |
| 期望 | 从本方案 40 格表复制的判据（文案原文 + 按钮/元素要求） |
| 实际 | **抄录页面出现的文案原文**（不是「正常」「符合预期」），并写是否出现重试按钮 |
| 截图文件名 | `qa-p2-<页面>-<场景>-<序号>.png`，存 `docs/qa-phase2-evidence/` |

**三条留证纪律**：① 不接受「已检查」「已验证」这类无信息结论，`实际` 列为空或写「正常」的格子按**未执行**处理；② 每格必须在 `docs/qa-phase2-evidence/` 有对应截图文件，文件名与表格一致，缺图按未执行处理；③ 触发缺陷时该格 `实际` 列需带缺陷编号（P0/P1/P2 见 §7.1）。

| 页面 | 断网（Offline） | 500（Network → 响应替换） | 超时（delay 30s） | 401 过期（改 token） |
| --- | --- | --- | --- | --- |
| `/chat` | 「网络连接已断开，请检查网络后重试」顶部条 + 输入区禁用 | 「回答暂时没有生成成功，请重试」+ 重试按钮可点 | 同 500，且 15s 内出现（不得无限转圈） | 跳 `/login`，**不显示** 20002 码 |
| `/knowledge` | 固定提示条；卡片区 ErrorState | 「知识库列表加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/knowledge/{baseId}` | 提示条；表格 ErrorState | 「知识库暂时无法加载，请重试」+ 重试 | 同 500 | 跳登录 |
| `/team` | 提示条；成员表 ErrorState | 「成员列表加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/team/organization` | 提示条；树与右侧双 ErrorState | 「组织架构加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/billing` | 提示条；套餐卡 + 图表 ErrorState | 「套餐信息加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/plugins` | 提示条；工具卡 ErrorState | 「办公工具列表加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/workflows` | 提示条；任务表 ErrorState | 「任务列表加载失败，请重试」+ 重试 | 同 500 | 跳登录 |
| `/profile` | 提示条；用量卡「用量信息暂时不可用，请稍后重试」 | 同左 + 重试 | 同左 | 跳登录 |
| `/ops/**` | 提示条 + ErrorState（独立布局不崩） | 「数据加载失败，请重试」+ 重试 | 同左 | 跳登录；**企业账号**访问则「当前账号没有该操作的权限」 |

**每格必须同时满足的 4 条硬判据**：
1. 无白屏：`document.body.innerText.trim().length > 0`；
2. 无错误码：页面文本匹配 `\b[1-3]\d{4}\b` 命中 0；
3. 无英文技术词：`error|exception|traceback|500|undefined|null|NaN` 命中 0（注意：`null` 在正常业务文案中也禁止出现）；
4. 有出路：出现可点击的重试按钮，点击后重新发起**原请求**（Network 面板可见同一 URL 第二次请求，**不是整页刷新**——`performance.navigation.type === 'navigate'` 不成立）。

#### 3.6.5 证明方法 C：连续失败与去重

- 连续 3 次失败：第 3 次文案必须为「多次尝试未成功，请稍后再试或联系管理员」（`use-retryable-request.ts` 有计数器）。
- 同一错误 3s 内出现 3 次：toast 只出现 **1 次**（`error-guard.ts` 去重窗口），3s 后再错再出现 1 次。

#### 3.6.6 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-UX-01 | 10 页断网 | 40 格中「断网」列 10 格全满足 4 条硬判据 |
| TC-P2-UX-02 | 10 页 500 | 同上（500 列） |
| TC-P2-UX-03 | 10 页超时 | 15s 内出错误态；不无限 loading |
| TC-P2-UX-04 | 10 页 401 过期 | 跳登录；页面文本无 `20002` |
| TC-P2-UX-05 | 空态 ≠ 筛选无结果 | 两态文案字符串不同（断言 `!=`） |
| TC-P2-UX-06 | 错误码正则 | UI 文本 `\b[1-3]\d{4}\b` 0 命中 |
| TC-P2-UX-07 | 连续 3 次失败 | 第 3 次文案为指定句子 |
| TC-P2-UX-08 | 3s 去重 + 离线条 | 3 次错误只 1 个 toast；离线条文案精确匹配 |
| TC-P2-UX-09 | 无白屏 | 骨架 → 内容切换期间空白 ≤300ms；`innerText` 从不为空 |

### 3.7 禁词门禁（一票否决项）

#### 3.7.1 风险描述

PRD 第 7 章 18 行术语对照表，任一词出现在**用户可见文案**即缺陷。难点不在"列出禁词"，而在**扫得准**：扫太松 → 满屏误报，团队最终把门禁关掉；扫太紧 → 漏检。因此本方案把「扫描面 × 提取规则 × 豁免口径」写死。

#### 3.7.2 为什么常规测试抓不到

| 盲区 | 说明 |
| --- | --- |
| 后端 message 与前端文案是两套 | 只扫前端会漏 `errors.py` 的 message；只扫后端会漏前端硬编码提示 |
| 代码标识符必然命中 | `chunk_policy`、`segment_count`、`index_status` 是合法字段名，直接按子串扫会 100% 误报 → 必须做词边界与上下文豁免 |
| 动态拼接 | `「第 ${n} 页」` 这类模板静态扫不出运行时结果，需扫构建产物 |
| 正则过宽 | `\b[1-3]\d{4}\b` 会命中 `2026`、`12000`、`19999` 等；不设豁免规则会误报 |

#### 3.7.3 证明方法：三面扫描 + 负样本自检

**面 1：源码字符串字面量（`apps/web/src/**/*.{ts,tsx}`）**

提取：正则 `(['"\x60])((?:[^'"\x60\\]|\\.)*)\1` 取字面量内容。
排除（按优先级）：
1. `import ... from '<path>'`、`require('<path>')`、`import('<path>')` 中的路径；
2. `className=` / `class=` 属性值、CSS 变量、`href`/`src` 的 URL；
3. **标识符形态**：命中位置左右紧邻 `_`、ASCII 字母或数字，且整体构成已知标识符（白名单正则 `^(chunk_policy|index_status|segment_count|segment_id|base_id|document_id|quota_unit|used_units|total_units|permission|role_key)$`）；
4. `lib/types.ts` 中形如 `^\s*\w+\??:` 的字段声明行（类型定义不是文案）。

**面 2：DOM 渲染文本 / 构建产物**

`next build` 后遍历 `.next/server/app/**/*.html` 与 `.next/static/chunks/**/*.js` 中的字符串常量（前者取可见文本、后者取字符串字面量），剔除 `<script>`、`<style>`、`<template>` 与所有属性值。
判据：面 2 独立于面 1 执行，防止"源码里是变量、运行期拼出来"的漏检。

**面 3：后端 message**

- `app/core/errors.py` 注册表全部 `message`；
- 参数化遍历每个错误码，触发真实响应并断言 `body["message"]`（这是唯一能覆盖"运行期覆盖文案"的方式，如 14006 的三种文案）；
- `apps/web/src/lib/business-errors.ts` 全部 value。

**禁词表（按 PRD 第 7 章 18 行展开，分三组）**

| 组 | 词条 | 处理 |
| --- | --- | --- |
| A 强禁（子串命中即违规） | 分块、切片、chunk size、overlap、向量、向量化、Milvus、embedding、RAG、召回、相似度、top_k、score_threshold、prompt、completion、Agent、智能体、编排、workflow run、OAuth、access_token、回调地址、redirect、租户、tenant、审计、audit log、事件流、ACL、权限位、RBAC、SKU、计费周期、proration、seat、索引、入库、命中索引、重建索引、reindex、密钥、凭据、app secret、corp_id | 无豁免，命中即失败 |
| B 词边界 + 豁免 | token、units、chunk、segment、profile、permission string、Dify、dify、api_key | 需词边界；命中标识符白名单（见上）或类型字段声明行时豁免 |
| C 模型名 | GPT-4o / GPT-4 / Qwen / DeepSeek / 豆包 / Claude / Llama / ChatGPT | 大小写不敏感，无豁免 |
| D 错误码 | `\b[1-3]\d{4}\b` | **仅面 2、面 3 的 message 启用**；豁免上下文：匹配串紧邻「年/月/日/元/万/字/席位/号/编号/ID/页/条」或位于 `data-*` 属性 |

**自检（G-15）**：内置 12 条负样本，每类至少 1 条（A 组 5 条、B 组 3 条、C 组 2 条、D 组 2 条），`--self-test` 时必须**全部命中**且退出码为 1；再内置 8 条"应豁免"正样本（`chunk_policy`、`segment_count`、`index_status`、`第 12 页`、`2026 年`、`12000 元`），必须**全部不命中**。任何一个方向错了都说明扫描器失效 → 门禁作废重写。

#### 3.7.4 用例与判据

| 编号 | 覆盖点 | 通过判据 |
| --- | --- | --- |
| TC-P2-WORD-01 | 面 1 源码字面量 | 命中 0（豁免清单生效后） |
| TC-P2-WORD-02 | 面 2 DOM/产物 | 10 页可见文本命中 0；错误码正则 0 |
| TC-P2-WORD-03 | 面 3 后端 message + 错误映射表 | 全码遍历命中 0；`business-errors.ts` 命中 0 |
| TC-P2-WORD-04 | 负样本自检 + 豁免正样本 | 12 条负样本 100% 命中（退出码 1）；8 条正样本 0 命中 |

---

## 4. 用例矩阵

### 4.0 阅读约定

| 项 | 约定 |
| --- | --- |
| 编号 | `TC-P2-<域><需求号>-<序号>`；跨域风险用例为 `TC-P2-<RISK>-<序号>` |
| 「需求」列 | PRD 需求号；`§3.x` 表示判据细节见第 3 章对应小节 |
| 「期望」列 | 一律为可断言项（状态码 / `code` / 字段值 / 计数 / 字节数 / 耗时）。**不出现"表现正常"「体验良好」等表述** |
| 「落点」列 | 具体文件 + 测试函数名；`#anchor` 表示手工走查单中的章节。所有落点文件路径相对仓库根 `C:/Users/15864/Desktop/麦子/` |
| 复用 | 标注 `[复用]` 的用例不重写，只在既有用例上追加断言或参数化；标注 `[新增]` 为本阶段新建文件 |
| 标记 | `@pytest.mark.p0`（阶段门）、`@pytest.mark.pg`（需 PostgreSQL）、`@pytest.mark.slow`（>10s） |

### 4.1 知识库域（27 条 + 凭据 7 条见 §3.2）

#### 4.1.1 P2-KB-01 知识库设置与生命周期（6 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-KB01-01 | P2-KB-01 验收 1 | 登录 member 账号；库不存在 | ①`POST /knowledge/bases {name:"制度库", chunk_policy:"fine"}` ②`GET /knowledge/bases` ③对该库 `POST .../documents` **不带** `chunk_policy` ④`GET .../documents/{id}` | ①`data.chunk_policy=="fine"` ②同库条目 `chunk_policy=="fine"` ③上传成功 `code=0` ④`data` 内含 `chunk_policy=="fine"` 或下游请求体 `chunk_policy=="fine"`（MockTransport 断言） | `apps/adapter/tests/integration/test_kb_meta_endpoints.py::test_create_base_persists_chunk_policy_and_document_inherits` [新增] |
| TC-P2-KB01-02 | P2-KB-01 验收 2 | 库 owner（有 `knowledge:base:update`）；库已存在 | 参数化 `PATCH`：`name=""`、`name` 129 字、`description` 1025 字、`chunk_policy="coarse"`、`visibility="public"`、请求体 `{}` | 6 组全部 `status_code==400 and code==20001`；空名文案含「知识库名称不能为空」；`updated_at` 不变 | `test_kb_meta_endpoints.py::test_patch_base_validation_matrix` [新增] |
| TC-P2-KB01-03 | P2-KB-01 验收 3 | 两个账号：库 owner、无权限 member | ①owner `PATCH` 四字段 ②`updated_at` 对比 ③无权限 `PATCH` ④无权限 `DELETE` | ①`code=0` 且四字段回显为新值 ②`updated_at > 旧值` ③④均 `code=20003`；无权限账号 `status_code in (403,200)` 但 `code==20003` | `test_kb_meta_endpoints.py::test_patch_base_updates_all_fields_and_denies_without_permission` [新增] |
| TC-P2-KB01-04 | P2-KB-01 验收 4 | 库内 12 个文档；有删除权 | ①`DELETE /knowledge/bases/{id}` ②`GET /knowledge/bases` ③`GET .../documents` ④查 `audit_log` | ①`data.deleted is True` 且 `deleted_document_count==12` ②列表不含该 `base_id` ③`code==11001` ④存在 `action='knowledge.base_deleted'` 且 `resource_id==base_id` | `test_kb_meta_endpoints.py::test_delete_base_soft_deletes_and_writes_audit` [新增] |
| TC-P2-KB01-05 | P2-KB-01 功能 3 + 裁决 | 库已有 3 个 `completed` 文档，`chunk_policy=default` | ①`PATCH chunk_policy=fine` ②查 3 个已处理文档的片段数/处理参数 ③再上传第 4 个文档 | ①`code=0` ②3 个旧文档片段数与处理参数**均不变**（逐个断言）③第 4 个文档按 `fine` 处理（下游请求体断言） | `apps/adapter/tests/unit/test_knowledge_base_service.py::test_chunk_policy_change_does_not_touch_processed_documents` [新增] |
| TC-P2-KB01-06 | P2-KB-01 错误码 20005 | 同一库；两个并发删除请求 | `asyncio.gather` 同时发 2 个 `DELETE`（不同 `Idempotency-Key`） | 恰好 1 个 `code=0`，另 1 个 `code in (20005, 11001)`；`knowledge_base_meta` 该行 `deleted_at` 只被写 1 次（非空且单值）；无 500 | `test_kb_meta_endpoints.py::test_concurrent_delete_returns_20005` [新增] |

#### 4.1.2 P2-KB-02 文档清单与处理进度（7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-KB02-01 | P2-KB-02 验收 1 | MockTransport 依次返回 `uploaded→parsing→preparing→completed` | ①上传 ②立即 `GET .../documents` ③每 200ms 轮询直至 `completed`，记录 `(stage, progress)` 序列 | ①上传后 **1s 内** 出现在列表 ②`stage` 序列严格按定义顺序推进，不倒退 ③`progress` 单调不减 ④终态 `stage=="completed" and index_status=="completed"` | `test_kb_meta_endpoints.py::test_document_stage_progression_is_monotonic` [新增] |
| TC-P2-KB02-02 | P2-KB-02 功能 2 | 5 种 `(stage, index_status)` 组合（纯数据构造，无需 DB） | 参数化调用 `map_stage` 与一致性校验函数，逐组合断言；DB 级 CHECK 由 `test_migrate.py::test_stage_index_status_check` 单独验证 | 5 组映射均满足：`completed ⟺ stage=completed`、`failed ⟺ index_status=failed`、`uploaded/parsing/preparing ⟺ index_status=processing`；一致性校验对 2 个非法组合（如 `stage=completed` + `index_status=processing`）返回 False；DB 侧插非法组合抛 `IntegrityError` | `apps/adapter/tests/unit/test_document_service.py::test_stage_and_index_status_are_consistent` [新增] + `tests/integration/test_migrate.py::test_stage_index_status_check` [新增] |
| TC-P2-KB02-03 | P2-KB-02 验收 2 | MockTransport 让处理返回失败（如加密 PDF） | ①上传 → 等 `failed` ②读 `error_message` ③`POST .../reindex` ④再查 | ①`stage=="failed"` ②`error_message` 非空且**不含** `error/exception/traceback/NoneType` 与任何 4-5 位数字码 ③`code=0` ④`stage in ("uploaded","parsing")` 且 `progress` 从 0 重新计数 | `test_kb_meta_endpoints.py::test_failed_document_reindex_returns_to_parsing` [新增] |
| TC-P2-KB02-04 | P2-KB-02 功能 5 | 5 个文档：3 个 `completed`（名字含 `Policy`/`policy`/`合同`）、1 个 `failed`、1 个 `parsing` | 参数化 6 组查询：`stage=failed`、`keyword=policy`、`keyword=POLICY`、`sort=uploaded_at:desc`、`keyword=合同&stage=completed`、`stage=processing`（非法值） | ①`stage=failed` 返回 1 条且 `document_id` 精确匹配 ②③大小写不敏感返回同 2 条（`set` 相等）④`uploaded_at` 严格降序 ⑤交集合正确 ⑥非法枚举 `code==20001` | `test_kb_meta_endpoints.py::test_document_filter_matrix` [新增] |
| TC-P2-KB02-05 | P2-KB-02 验收 4 | 单库 5000 文档（1 条 `generate_series` 插入） | `GET .../documents?page=250&page_size=20`，连续 3 次 | ①`len(items)==20` ②`meta.total==5000 and meta.total_pages==250` ③`len(response.content) < 204800`（**三次均满足**）④20 条 `document_id` 无重复且与第 249/251 页无交集 | `apps/adapter/tests/integration/test_kb_document_scale.py::test_page_250_under_200kb_with_5000_docs` [新增] `@slow` |
| TC-P2-KB02-06 | P2-KB-02 验收 5 / 裁决 #4 | 文档 `stage=parsing` | `DELETE .../documents/{doc_id}` | `status_code==400 and code==11006 and message=="文档正在处理，请稍后再删除"`（**逐字**）；文档仍在列表中 | `test_kb_meta_endpoints.py::test_delete_processing_document_returns_11006` [新增] |
| TC-P2-KB02-07 | P2-KB-02 功能 3 + 裁决 #10 | 两个文档：一个 `estimated_remaining_seconds` 无样本（null）、一个有待值 | ①读 KB3 ②读 R4 | ①KB3 每项 **不含** `segment_count` 键（`"segment_count" not in item`）；`estimated_remaining_seconds` 可为 `null` 且 JSON 中该键存在 ②R4 响应 **含** `segment_count`（整数）与 `file_size`/`uploaded_at` | `test_kb_meta_endpoints.py::test_document_list_contract_excludes_segment_count_and_allows_null_eta` [新增] |

#### 4.1.3 P2-KB-03 可见范围与成员授权（8 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-KB03-01 | P2-KB-03 验收 1（列表） | `kb_r` 为 `restricted`，仅授权 A；B 未授权 | B `GET /knowledge/bases` | `base_id` 集合不含 `kb_r`；响应文本中 `kb_r` 出现 **0 次**；`code=0`（静默过滤，不报错） | `apps/adapter/tests/integration/test_grant_and_backlink.py::test_restricted_base_hidden_from_ungranted_list` [新增] |
| TC-P2-KB03-02 | P2-KB-03 验收 1（详情/检索） | 同上 | B 分别调 `GET .../documents`（KB3）与 `POST /knowledge/search {base_id:kb_r}` | 两者均 `code==20003`；`status_code != 404`（防探测）；响应体不含任何该库文档名 | `test_grant_and_backlink.py::test_restricted_base_detail_and_search_return_20003` [新增] |
| TC-P2-KB03-03 | P2-KB-03 功能 5 | 同一 B，请求携带 `knowledge_base_ids=["kb_r","kb_o"]` | `POST /model/generate`（桩模式） | `status_code==200 and code==0`；`citations` 中 `base_id` 集合 **不含** `kb_r`；响应无 `20003`、无 `kb_r` 字样 | `test_grant_and_backlink.py::test_chat_silently_skips_unauthorized_base` [新增] |
| TC-P2-KB03-04 | P2-KB-03 验收 2 | 部门树 A→A1→A11；成员 M 属 A11；`kb_r` 授权给 A | ①M 访问 `kb_r` ②把 M 移出 A11 ③`t0` 记时，轮询至不可访问 | ①`code=0` ②`code=0` ③**首个 20003 出现的耗时 ≤5.0s**（`time.monotonic()` 断言） | `test_grant_and_backlink.py::test_department_grant_covers_subtree_and_revokes_within_5s` [新增] |
| TC-P2-KB03-05 | P2-KB-03 功能 2 + 4 | `kb_r` 授权给角色 `role_x`，M 属该角色 | ①M 访问 → 200 ②`DELETE .../grants/{grant_id}` ③5s 内 M 再访问 | ①`code=0` ②`code=0 and deleted is True` ③`code==20003` 且列表不含该库；无二次报错 | `test_grant_and_backlink.py::test_role_grant_effective_and_revocable` [新增] |
| TC-P2-KB03-06 | P2-KB-03 验收 3 | 无既有授权 | 同一 `subject_id` 连续 `POST .../grants` 两次 | 第 1 次 `created_count==1 and skipped_count==0`；第 2 次 `created_count==0 and skipped_count==1`；`GET .../grants` 该主体条目**恰好 1 条** | `test_grant_and_backlink.py::test_duplicate_grant_counts_as_skipped` [新增] |
| TC-P2-KB03-07 | P2-KB-03 验收 5 | 目标成员为 `guest` | `POST .../grants {subject_type:"user", permission:"editor"}` | `code==20001` 且 `message=="访客只能查看，不能上传或管理文档"`（逐字）；`knowledge_base_grant` 无新增行 | `test_grant_and_backlink.py::test_guest_editor_grant_rejected_with_20001` [新增] |
| TC-P2-KB03-08 | P2-KB-03 功能 4 + 端点约束 | `kb_r` owner=U_owner | ①对 owner 的 grant 执行 `DELETE` ②`subject_ids` 传 51 个 ③传 50 个 ④查审计 | ①`code in (20001,20003)` 且 owner 仍可访问（`code=0`）②`code==20001` 且文案含「最多」 ③`created_count+skipped_count==50` ④审计含 `knowledge.grant_changed` 与操作人 | `test_grant_and_backlink.py::test_owner_grant_cannot_be_revoked_and_batch_limit` [新增] |

#### 4.1.4 P2-KB-04 引用溯源（6 条；stale 状态机见 §3.4 的 6 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-KB04-01 | P2-KB-04 验收 1 | 含 PDF 库；桩模式返回 2 条 `retriever_resources` | `POST /model/generate` | `citations` 长度 2；每条含 `base_id`/`base_name`/`segment_id`/`position{page,paragraph}` 且 `segment_id` 匹配 `^seg_[0-9a-f]{16}$`；旧字段 `document_name`/`segment_content`/`score` **仍存在** | `test_grant_and_backlink.py::test_m1_citations_include_base_and_segment_fields` [新增] |
| TC-P2-KB04-02 | P2-KB-04 验收 5 | MockTransport 在 `done` 事件**之前**发 `citation` | `POST /model/generate/stream`，逐事件读取 | `citation` 事件出现在 `done` 之前；载荷含 4 个新字段；`event` 名仍为 `citation`（未改名）；同一 `segment_id` 可在收到后立即用 KB4 查到（`status=="valid"`） | `test_grant_and_backlink.py::test_m2_citation_event_arrives_before_done` [新增] |
| TC-P2-KB04-03 | P2-KB-04 功能 1（R7） | 库内 1 文档已处理 | `POST /knowledge/search` | `segments[0]` 含 `base_id`/`base_name`/`segment_id`；`position` 字段**未变**（仍为 `{page,paragraph}`）；`score` 仍存在 | `test_grant_and_backlink.py::test_r7_segments_include_base_and_segment_id` [新增] |
| TC-P2-KB04-04 | P2-KB-04 功能 2 + 端点 | 已回填 1 个片段 | ①`GET .../segments/{sid}?with_context=true` ②`with_context=false` ③对未处理完文档调 | ①`content` 非空、`context_before/after` 各非空、`position.page==12`、`status=="valid"` ②`context_before is None and context_after is None` ③`code==11004` | `test_grant_and_backlink.py::test_segment_detail_returns_context_when_requested` [新增] |
| TC-P2-KB04-05 | P2-KB-04 边界（>20 条） | 构造一次回答 25 条引用 | 打开引用抽屉 | 抽屉按来源分组折叠，分组头显示条数；展开后 25 条全部可见；无横向滚动溢出；`document.body.scrollWidth <= window.innerWidth` | `apps/web/docs/qa-manual-checklist-phase2.md#citation-fold` [手工] |
| TC-P2-KB04-06 | P2-KB-04 功能 3（降级） | 纯文本文件（`position.page=null`） | 点击「在文档中定位」 | 展示片段内容 + 文案「该文档没有页码信息，已定位到相关片段」；**不显示** `null`/`undefined`/`第 null 页`；URL 形如 `/knowledge/{baseId}?doc={docId}&segment={sid}` | `apps/web/docs/qa-manual-checklist-phase2.md#citation-no-page` [手工] |

### 4.2 工具域（22 条）

#### 4.2.1 P2-TL-01 企业微信（6 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-TL01-01 | P2-TL-01 验收 1 | 未连接 `wecom` | `POST /tools/execute {tool_type:"wecom", operation:"send_message"}` | `code==13002` 且 `message=="企业微信尚未连接，请先在设置中完成授权"`（逐字） | `apps/adapter/tests/integration/test_tool_executions.py::test_wecom_execute_without_connection_returns_13002` [新增] |
| TC-P2-TL01-02 | P2-TL-01 验收 2 | 已连接 | `GET /tools/registry` | 含 `tool_type=="wecom"`；`operations` 长度 4，值为 `send_message/list_departments/list_members/create_group`；每个 operation 的 `display_name` 命中中文正则 `^[\u4e00-\u9fa5]+$`；`display_name` 无 `access_token/corp/secret/OAuth` | `test_tool_executions.py::test_registry_wecom_operations` [新增] |
| TC-P2-TL01-03 | P2-TL-01 验收 3 | 已连接；MockTransport 返回 `message_id` | `POST /tools/execute {operation:"send_message"}` | `code=0 and data.status=="success"`；`data.result.message_id` 非空字符串；下游请求体含 `touser` 与 `text.content` 且 `content` 与入参一致 | `apps/adapter/tests/unit/test_wecom_connector.py::test_send_message_returns_message_id` [新增] |
| TC-P2-TL01-04 | P2-TL-01 验收 4 | 连接已过期（MockTransport 返回 `errcode=42001`） | ①`execute` ②记录连接状态 ③重连 ④再 `execute` | ①`code==13003` 且文案含「重新连接」 ②`GET /tools/connections` 该连接 `status=="expired"` ③重连 `code=0` 且**无需重填参数**（请求体为空）④`code=0 and status=="success"` | `test_tool_executions.py::test_wecom_expired_credential_then_reconnect` [新增] |
| TC-P2-TL01-05 | P2-TL-01 功能 3 + 边界 | 已连接 | 参数化：①`to` 列表 201 人 ②下游 502 ③下游 `errcode=60111`（成员不存在） ④下游 `errcode=40013`（corp 非法） | ①`code==20001` 且文案含「200 人」 ②`code==13004` ③`code==13006` 且文案含「通讯录」 ④`code==13003`；四组 `message` 均无英文技术词、无 4-5 位码 | `test_wecom_connector.py::test_wecom_error_mapping_matrix` [新增] |
| TC-P2-TL01-06 | P2-TL-01 验收 5 | 已连接；金丝雀密钥 `WECOM-CANARY-9x` | 执行一次成功后扫描三面：响应体、`caplog`、`audit_log`/`tool_execution` 落库内容 | `WECOM-CANARY-9x` 命中 **0**；`access_token`/`corp_id`/`secret` 命中 **0**；`tool_execution.error_message` 为业务语言 | `test_wecom_connector.py::test_wecom_response_and_log_have_no_secret_words` [新增] |

#### 4.2.2 P2-TL-02 飞书（5 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-TL02-01 | P2-TL-02 验收 1 | 已连接 | `GET /tools/registry` | 含 `feishu` 且 4 项 `operations`（`send_message/create_calendar_event/list_members/create_doc`）；`display_name` 全中文 | `test_tool_executions.py::test_registry_feishu_operations` [新增] |
| TC-P2-TL02-02 | P2-TL-02 验收 2 | 已连接；MockTransport 返回 `event_id` | 创建日程（标题/开始/时长/参与人 2 人/备注） | `code=0 and status=="success"`；下游请求体 `start_time.timezone` 等于企业时区；参与人数组长度 2；`result` 含 `event_id` | `apps/adapter/tests/unit/test_feishu_connector.py::test_create_calendar_event_success` [新增] |
| TC-P2-TL02-03 | P2-TL-02 验收 3 | MockTransport 返回权限不足（`code=99991672`） | 创建日程 | `code==13005` 且 `message` 含「开通对应权限」；不含 `scope`/`tenant_access_token` | `test_feishu_connector.py::test_permission_denied_maps_to_13005` [新增] |
| TC-P2-TL02-04 | P2-TL-02 验收 4 | MockTransport：日程创建成功、邀请 1 人失败 | 创建日程（参与人 2 人，其中 1 人不匹配） | `code=0 and data.status=="success"`；`data.result.warnings` 长度 1 且含该参与人；**不判整单失败**；前端提示文案「已创建日程，1 位参与人未邀请成功」 | `test_feishu_connector.py::test_partial_attendee_failure_returns_warnings` [新增] |
| TC-P2-TL02-05 | P2-TL-02 验收 5 | 企微 + 飞书均已连接 | `GET /tools/connections` | `len(items)==2`；两连接 `conn_id` 不同、`status` 均 `connected`；删除飞书连接后企微仍 `connected` | `test_tool_executions.py::test_two_tools_coexist` [新增] |

#### 4.2.3 P2-TL-03 微信公众号（5 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-TL03-01 | P2-TL-03 验收 1 | 已绑定 | `GET /tools/registry` | 含 `wechat_mp` 且 4 项能力（`upload_material/create_draft/publish_article/list_articles`）；`display_name` 全中文 | `test_tool_executions.py::test_registry_wechat_mp_operations` [新增] |
| TC-P2-TL03-02 | P2-TL-03 验收 2 + 3 | 已绑定 | ①`create_draft` ②**不调** `publish_article` ③查 `list_articles` 与下游 mock 的"已发布"集合 | ①`code=0` 且返回 `draft_id` 非空 ②— ③`list_articles` 中**不含**该 `draft_id`；mock 的发布调用计数为 **0**（证明无自动发布） | `apps/adapter/tests/unit/test_wechat_mp_connector.py::test_draft_not_published_without_confirmation` [新增] |
| TC-P2-TL03-03 | P2-TL-03 验收 4 | 已有草稿 | ①`publish_article {draft_id}` ②`list_articles` | ①`code=0 and status=="success"` ②返回该文且 `read_count/like_count/share_count` 键存在；**缺失数据为 `null` 而非 `0`**（断言类型） | `test_wechat_mp_connector.py::test_publish_then_list_articles` [新增] |
| TC-P2-TL03-04 | P2-TL-03 验收 5 | 已发布 1 次 | 同一 `draft_id` 再 `publish_article` | `code==20007`；下游发布调用计数仍为 **1**；`list_articles` 中该文只有 1 条 | `test_tool_executions.py::test_duplicate_publish_returns_20007` [新增] |
| TC-P2-TL03-05 | P2-TL-03 功能 5 | 已绑定 | 参数化：①素材 20MB（超限）②正文含未授权外链 | ①`code==20008` 且 `message` 含具体限制数值 ②`code==13006` 且文案「正文含未获授权的外部链接，请调整后再发布」；两者均无 `media_id`/`appsecret` | `test_wechat_mp_connector.py::test_material_and_external_link_error_matrix` [新增] |

#### 4.2.4 P2-TL-04 工具中心闭环与零改动回归（6 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-TL04-01 | P2-TL-04 验收 5 | 5 款连接器已装载 | `GET /tools/registry` | `len(tools)==5`；`tool_type` 集合 == `{email, github, wecom, feishu, wechat_mp}`；`len(set)==5`（无重复） | `test_tool_executions.py::test_registry_returns_five_tools` [新增] |
| TC-P2-TL04-02 | P2-TL-04 验收 4 | 冻结快照 `docs/api/openapi.snapshot.json` | `python deploy/validate_openapi.py` | T1-T7 七端点的 path/method/请求字段/错误码集合**完全一致**；`registry` 响应仅 `tools` 数组长度变化（schema 结构不变）；G4 报 0 violation | `apps/adapter/tests/integration/test_endpoint_baseline.py::test_tools_endpoints_schema_only_array_changed` [新增] |
| TC-P2-TL04-03 | P2-TL-04 验收 3 | 造 25 条执行记录（含 5 条失败） | `GET /tools/executions?page=1&page_size=20` | `len(items)==20`；按 `executed_at` 降序；失败项 `error_message` 非空且**不含** `Traceback`/`File "`/`app/`/堆栈/4-5 位码；`operation_display_name` 全中文 | `test_tool_executions.py::test_executions_returns_latest_20_without_stacktrace` [新增] |
| TC-P2-TL04-04 | P2-TL-04 验收 2 | 已连接后断开 | ①`DELETE /tools/connections/{id}` ②`execute` ③`GET /plugins` | ①`code=0` ②`code==13002` ③卡片状态「未连接」且文案「企业微信尚未连接，请先在设置中完成授权」 | `test_tool_executions.py::test_disconnect_then_execute_returns_13002` [新增] |
| TC-P2-TL04-05 | P2-TL-04 功能 5 | 企业共用连接 + 个人连接并存（同 `tool_type`） | ①`execute` ②断开个人连接 ③`execute` | ①走个人连接（下游凭据为个人，MockTransport 断言）②`code=0` ③仍成功（回落企业共用），企业连接 `status` 不变 | `test_tool_executions.py::test_personal_connection_precedence` [新增] |
| TC-P2-TL04-06 | P2-TL-04 功能 5（权限） | member 无 `tool:connection:manage` | ①`GET /tools/registry` ②`GET /tools/executions` ③`POST /tools/connections` ④`DELETE` | ①`code=0`（可查看）②`code=0` ③④`code==20003` | `test_tool_executions.py::test_tool_readonly_without_manage_permission` [新增] |

### 4.3 组织与权限域（28 条）

#### 4.3.1 P2-ID-01 部门 / 组织层级树（7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-ID01-01 | P2-ID-01 验收 1（正例） | 已有根部门 `dept_root`（level=1） | 循环建 4 层（level 2-5） | 4 次均 `code=0`；`data.level` 依次为 2,3,4,5；`GET .../departments?flat=true` 返回 5 条 | `apps/adapter/tests/integration/test_org_endpoints.py::test_create_five_levels` [新增] |
| TC-P2-ID01-02 | P2-ID-01 验收 1（负例） | 已存在 5 层 | 以第 5 层为 `parent_id` 建第 6 层 | `code==14006` 且 `message=="组织层级最多 5 层，请调整后重试"`（逐字）；`department` 表无新增行 | `test_org_endpoints.py::test_sixth_level_rejected_with_14006_message` [新增] |
| TC-P2-ID01-03 | P2-ID-01 验收 2 | 3 层树，部门各有 2/3/1 名成员 | ①`GET .../departments?flat=false` ②`flat=true` | ①`children` 嵌套深度 3；每个节点 `path_names` 与实际路径逐字相等（如 `["桐鸣科技","研发部"]`）；`level` 与深度一致；`member_count` 精确（2/3/1）②平铺数组长度 3 且每项无 `children` | `apps/adapter/tests/unit/test_department_service.py::test_path_names_and_level_assembly` [新增] + `test_org_endpoints.py::test_department_tree_shape` [新增] |
| TC-P2-ID01-04 | P2-ID-01 验收 3 + 功能 6 | 部门 D 有 1 名成员 + 1 个子部门 | ①`DELETE /identity/departments/D` ②迁移成员到别处 ③删子部门 ④再删 D ⑤对根部门 `DELETE` | ①`code==14006` 且文案「该部门下仍有成员或子部门，请先迁移后再删除」②③`code=0` ④`code=0 and deleted is True` ⑤`code==14006`（根不可删） | `test_org_endpoints.py::test_delete_department_with_children_or_members_returns_14006` [新增] |
| TC-P2-ID01-05 | P2-ID-01 验收 4 | 树 A→B→C | `PATCH /identity/departments/A {parent_id: C}` | `code==14006` 且文案「不能把部门移动到自己的下级部门」；A 的 `path` 未变（SQL 断言） | `test_org_endpoints.py::test_move_department_into_own_subtree_returns_14006` [新增] |
| TC-P2-ID01-06 | P2-ID-01 验收 5 | 部门 D 被授权 `kb_r`；成员 M 属 D | ①M 访问 `kb_r` → 200 ②把 D 移到另一父部门（M 随之） ③5s 内 M 再访问 | ①`code=0` ②`code=0` ③仍 `code=0`（授权随对象走）；再把 M 移出 D → 5s 内 `code=20003` | `test_org_endpoints.py::test_move_department_affects_grant_within_5s` [新增] |
| TC-P2-ID01-07 | P2-ID-01 验收 6 | — | ①建部门 ②改名 ③移动 ④删除 ⑤查 `audit_log` | ⑤存在 4 条记录，`action` 分别为 `department.created/renamed/moved/deleted`，`change_summary` 含前后值（移动含 from/to parent） | `test_org_endpoints.py::test_department_actions_written_to_audit` [新增] |

#### 4.3.2 P2-ID-02 权限点目录与角色配置（7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-ID02-01 | P2-ID-02 验收 1 | 平台权限点数据已初始化 | `GET /identity/permissions` | `len(groups)==6`；组名 == 知识库/工具/插件/成员/账单/操作记录；全部 `permissions` 展开后**恰好 16 项**；每项 `display_name` 命中中文正则且非空；`description` 非空 | `test_org_endpoints.py::test_permission_catalog_has_16_items_in_6_groups` [新增] |
| TC-P2-ID02-02 | P2-ID-02 验收 2 | 角色 `member`，成员 M | ①`U5` 给 `member` 加 `knowledge:base:create` ②5s 内 M 建库 ③移除该权限 ④5s 内 M 再建库 | ①`code=0` ②`code=0` ③`code=0` ④`code==20003`；两次切换的生效耗时均 ≤5.0s | `test_org_endpoints.py::test_role_permission_change_takes_effect_within_5s` [新增] |
| TC-P2-ID02-03 | P2-ID-02 验收 3 | 自定义角色 `role_c` | `U5` 给 `role_c` 授 `billing:subscription:manage`，再授 `team:role:manage` | 两次均 `code==20003`；`role_permission` 无新增行 | `test_org_endpoints.py::test_custom_role_cannot_get_privileged_permissions` [新增] |
| TC-P2-ID02-04 | P2-ID-02 验收 4 / 裁决 #3 | 角色 `role_x` 下有 3 名成员 | `DELETE .../roles/{role_x}` | `code==20005` 且 `message=="该角色下还有 3 名成员，请先调整成员角色"`（含具体数字）；角色仍存在 | `test_org_endpoints.py::test_delete_referenced_role_returns_20005_with_count` [新增] |
| TC-P2-ID02-05 | P2-ID-02 功能 3 | 内置角色 owner | ①`U5` 试图改 owner 的权限点 ②删除内置角色 `member` | ①`code in (20003,20001)` 且 owner 权限集合与初始完全一致（集合相等）②`code in (20003,20001)` 且角色仍存在 | `test_org_endpoints.py::test_owner_permissions_immutable` [新增] |
| TC-P2-ID02-06 | P2-ID-02 验收 5 | 前端已构建 | ①扫 `/team/organization` 页面产物文本 ②扫「角色权限」弹窗 DOM | `knowledge:`/`billing:`/`team:` 形式的权限标识字符串命中 **0**；页面只出现中文业务名称 | `apps/web/scripts/check-banned-terms.mjs`（新增规则 `permission-string`）+ `apps/web/docs/qa-manual-checklist-phase2.md#role-permission-dialog` [手工] |
| TC-P2-ID02-07 | P2-ID-02 功能 5 | — | ①`U1` 传未注册权限 `foo:bar:baz` ②建第 51 个角色 | ①`code==14004` ②`code==20001` 且文案含上限数值；角色表仍 50 条 | `test_org_endpoints.py::test_unknown_permission_and_role_limit` [新增] |

#### 4.3.3 P2-ID-03 成员角色变更与移除（8 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-ID03-01 | P2-ID-03 验收 1 | M 为 `member` | ①`PATCH .../members/M {role:"org_admin"}` ②`profile` 查角色 ③`GET /identity/permissions` 的可用集合 ④5s 内调管理员接口 | ①`code=0 and data.role=="org_admin"` ②`role=="org_admin"` ③含管理类权限 ④`code=0`（原为 20003） | `test_org_endpoints.py::test_member_role_change_effective_within_5s` [新增] |
| TC-P2-ID03-02 | P2-ID-03 验收 2（owner） | 目标为 owner | `DELETE .../members/{owner_id}` | `code==14007` 且 `message=="不能移除企业所有者，请先转移所有权"`；成员仍在 | `test_org_endpoints.py::test_remove_owner_returns_14007` [新增] |
| TC-P2-ID03-03 | P2-ID-03 验收 2（自己/同级） | 操作者为 org_admin，目标为自己 / 另一 org_admin | ①删自己 ②删同级 ③把自己降级为 member | 三条均 `code==14008` 且 `message=="你没有权限修改该成员"` | `test_org_endpoints.py::test_remove_self_or_peer_returns_14008` [新增] |
| TC-P2-ID03-04 | P2-ID-03 验收 3（正例） | 100 名普通成员 | `POST .../members/batch {action:"remove", member_ids:[100 个]}` | `data.succeeded==100`；`data.failed==[]`；成员表 `status` 全部非 `active` | `test_org_endpoints.py::test_batch_remove_100_succeeds` [新增] |
| TC-P2-ID03-05 | P2-ID-03 验收 3（隔离） | 100 名成员中含 1 名 owner | 同批量移除 | `succeeded==99`；`failed` 长度 1 且该 `member_id` 为 owner、`reason` 含「所有者」；其余 99 人不受影响（逐个断言） | `test_org_endpoints.py::test_batch_partial_failure_isolated` [新增] |
| TC-P2-ID03-06 | P2-ID-03 验收 4 | 成员 M 创建过 3 个知识库 | ①移出 M ②M 用旧 token 访问 ③查 M 的知识库归属 ④M 重新登录 | ①`data.removed is True and transferred_knowledge_base_count==3` ②`code==20003` ③3 个库 `owner_user_id` 变为企业管理员 ④`profile` 无企业数据 | `test_org_endpoints.py::test_removed_member_gets_20003_and_kb_transferred` [新增] |
| TC-P2-ID03-07 | P2-ID-03 验收 5 | 同上 | 查 `audit_log` | 存在 1 条 `action="member.role_changed"`，`action_display_name=="变更成员角色"`，`change_summary=={"role":{"from":"member","to":"org_admin"}}` | `test_org_endpoints.py::test_role_change_audit_has_change_summary` [新增] |
| TC-P2-ID03-08 | P2-ID-03 端点约束 | — | ①`batch` 传 101 个 id ②`action="delete_all"`（非法枚举）③停用成员 `status=disabled` 后其访问 | ①`code==20001` 且文案含「100」②`code==20001` ③`code==20003`（停用即不可访问） | `test_org_endpoints.py::test_batch_limit_and_invalid_action` [新增] |

#### 4.3.4 P2-ID-04 操作记录查询（6 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-ID04-01 | P2-ID-04 功能 3 | — | `GET /identity/audit-logs/actions` | 分组数 ≥6；展开后 `action` 字符串集合 ⊇ 17 个必需动作（登录/邀请/变更角色/移出/部门增删移/知识库增改删/授权变更/上传删除文档/工具连接断开/套餐变更/席位变更/订单核销/角色权限变更）；每项 `display_name` 全中文 | `test_org_endpoints.py::test_audit_action_catalog_covers_required_actions` [新增] |
| TC-P2-ID04-02 | P2-ID-04 验收 1 | — | 执行一次改角色，`t0` 记时轮询 `POST /identity/audit-logs` | 5.0s 内查到；`action_display_name=="变更成员角色"`；`actor_role` 非空 | `test_org_endpoints.py::test_audit_query_finds_action_within_5s` [新增] |
| TC-P2-ID04-03 | P2-ID-04 验收 2 | 造 4 类记录（2 人 × 2 结果） | 参数化组合筛选：`actor_keyword` 模糊 + `start_at/end_at` + `result=failed` + `action_group` | 每组返回集合与预期 `document_id` 集合**精确相等**；`meta.total` 等于该集合大小 | `test_org_endpoints.py::test_audit_filter_combination` [新增] |
| TC-P2-ID04-04 | P2-ID-04 验收 3 | — | 查询跨度 91 天（`start_at` 到 `end_at`） | `code==20001` 且 `message=="查询时间范围最长 90 天，请缩小范围"`；恰好 90 天时 `code=0` | `test_org_endpoints.py::test_audit_range_over_90_days_returns_20001` [新增] |
| TC-P2-ID04-05 | P2-ID-04 验收 4 | 记录产生于 `10.12.34.56` | 查询并断言 `ip_masked` | 值匹配 `^\d{1,3}\.\d{1,3}\.\*\.\*$`；原始 IP `10.12.34.56` 在响应文本中出现 **0** 次 | `test_org_endpoints.py::test_audit_ip_is_masked` [新增] |
| TC-P2-ID04-06 | P2-ID-04 验收 5 | member 账号（无 `audit:log:read`） | ①`POST /identity/audit-logs` ②`GET /team?tab=audit` | ①`code==20003` ②侧边栏与标签页无「操作记录」入口（DOM 文本命中 0） | `test_org_endpoints.py::test_audit_query_requires_permission` [新增] + `apps/web/docs/qa-manual-checklist-phase2.md#team-audit-tab` [手工] |

### 4.4 计费域（22 条）

#### 4.4.1 P2-BL-01 套餐目录（4 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-BL01-01 | P2-BL-01 验收 1 | 套餐数据已初始化 | `GET /billing/plans` | `len(plans)==3`；`plan_code` 集合 == `{free,professional,enterprise}`；每个套餐含 `price_per_seat_month_cents`(int)/`min_seats`/`quota_units_per_seat_month`/`quota_unit_label=="字数额度"`/`features`(list)/`status`/`trial_days` | `apps/adapter/tests/integration/test_billing_and_callback.py::test_plans_returns_three_plans_with_all_fields` [新增] |
| TC-P2-BL01-02 | P2-BL-01 验收 2 | 同上 | ①读 free 套餐 `price_per_seat_month_cents` ②前端 `/billing` 渲染 | ①`==0` ②展示文本精确为「¥0 / 席位 / 月」（正则 `^¥0 / 席位 / 月$`）；**不出现**「免费试用」 | `test_billing_and_callback.py::test_free_plan_price_is_zero` [新增] + `apps/web/docs/qa-manual-checklist-phase2.md#billing-plan-price` [手工] |
| TC-P2-BL01-03 | P2-BL-01 验收 3 | 专业版已有订阅 | ①`PATCH /ops/settings` 或 SQL 置 `professional.status='offline'` ②`GET /billing/plans` ③新建订单选该套餐 ④查已订阅用户订阅信息 | ②该套餐 `status=="offline"` ③`code` 非 0（`15002`）④订阅的 `plan_name` 仍正常显示（非空、非 `null`） | `test_billing_and_callback.py::test_offline_plan_cannot_be_purchased` [新增] |
| TC-P2-BL01-04 | P2-BL-01 功能 2 | 前端已构建 | ①改 `ops_settings.quota_unit_label` 与某套餐 `quota_units_per_seat_month` ②刷新 `/billing` | 页面额度数字与单位**随配置变化**（变化前后文本 `!=`）；源码中 gzip 后检索 `200000`/`3000000`/`8000000` 命中 **0**（证明无硬编码） | `apps/web/docs/qa-manual-checklist-phase2.md#billing-no-hardcode` [手工] + `apps/web/scripts/check-banned-terms.mjs --hardcoded-quota` |

#### 4.4.2 P2-BL-02 订阅与席位（7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-BL02-01 | P2-BL-02 验收 1 | 新企业无订阅 | ①`GET /billing/subscription` ②`POST /billing/subscription {plan_code:"free", action:"subscribe"}` | ①`code=0`，`plan_code=="free" and status=="active" and seats_total==3`（合成视图，**非 404**）②`code=0 and status=="active"`，`seats_total==3` | `test_billing_and_callback.py::test_subscribe_free_plan_creates_active_subscription` [新增] |
| TC-P2-BL02-02 | P2-BL-02 验收 2 | free 订阅 | `POST {plan_code:"professional", seats:10, action:"upgrade"}` | `code=0 and status=="active"`；`seats_total==10`；`order_id` 匹配 `^ord_`；`features` 中 `org_structure.included is True`（升级前为 False） | `test_billing_and_callback.py::test_upgrade_creates_order_and_flips_features` [新增] |
| TC-P2-BL02-03 | P2-BL-02 验收 3 / 裁决 #5 | professional 生效中 | `POST {plan_code:"free", action:"downgrade"}` | **`status_code==200 and code==0`**（不是 400/`15005`）；`data.pending_change.plan_code=="free"`；`data.pending_change.effective_at == 当前 period_end`；`GET /billing/subscription` 的 `plan_code` 仍为 `professional` | `test_billing_and_callback.py::test_downgrade_sets_pending_change` [新增] |
| TC-P2-BL02-04 | P2-BL-02 验收 4 | 席位 3 已用 3 | ①`U4` 邀请新成员 ②`GET /plugins` 邀请按钮 | ①`code==15003` 且 `message=="成员席位已用满，请先增加席位"` ②按钮 `disabled` 且旁有「增加席位」链接（`a[href="/billing"]` 存在） | `test_billing_and_callback.py::test_invite_when_seats_full_returns_15003` [新增] + `#team-seat-full` [手工] |
| TC-P2-BL02-05 | P2-BL-02 验收 5 | 席位 10 已用 7 | `PATCH /billing/subscription/seats {seats:5}` | `code==15003`；`seats_total` 仍为 10（未变更） | `test_billing_and_callback.py::test_reduce_seats_below_used_returns_15003` [新增] |
| TC-P2-BL02-06 | P2-BL-02 功能 5 | 无订阅 | `asyncio.gather` 同时发 5 个 `subscribe` | 恰好 1 个 `code=0`，其余 `code in (15004,20005)`；`billing_subscription` 中 `status='active'` 的行**恰好 1 条**（部分唯一索引生效） | `test_billing_and_callback.py::test_concurrent_subscribe_keeps_single_active` [新增] |
| TC-P2-BL02-07 | P2-BL-02 功能 5（宽限） | `status=past_due`，`current_period_end = now-8d` | ①推进时间至逾期第 8 天（注入 clock）②调 `GET /billing/subscription` ③尝试上传文档 | ①`status=="expired"` ②页面显示「已到期」+ 保留数据文案 ③文档处理被拒（`code==15001` 或排队），**对话仍可用**（`code=0`，回答受限） | `apps/adapter/tests/unit/test_subscription_service.py::test_past_due_grace_then_expired` [新增]（clock 注入） |

#### 4.4.3 P2-BL-03 用量与额度（4 条；核心一致性见 §3.3 的 7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-BL03-01 | P2-BL-03 验收 1（时延） | 桩模式 | 完成一次对话，`t0` 记时，每 200ms 查 `usage_event` | **≤10.0s** 内出现 1 条 `capability='chat'` 的事件；`units > 0` | `apps/adapter/tests/integration/test_usage_endpoints.py::test_usage_lands_within_10s` [新增] |
| TC-P2-BL03-02 | P2-BL-03 功能 2 | 4 类能力各产生 1 条用量 | `GET /billing/usage?period=<p>&group_by=capability` | `breakdown` 4 项，`display_name` 集合 == `{对话, 文档问答, 文档处理, 自动化任务}`；每项含 `units`/`percent`；`sum(percent)` 与 100 的偏差 < 0.2（浮点） | `test_usage_endpoints.py::test_usage_breakdown_by_capability_uses_business_names` [新增] |
| TC-P2-BL03-03 | P2-BL-03 功能 3 | 3 成员 × 2 部门有量 | ①`group_by=member` ②`group_by=department` ③`group_by=member` 但账号为普通成员 | ①条目数 == 3，`sum(units)==quota_units_used` ②按部门求和 == 企业总量 ③`code==20003`（管理视图需管理权限） | `test_usage_endpoints.py::test_usage_group_by_member_and_department` [新增] |
| TC-P2-BL03-04 | P2-BL-03 功能 4 | — | ①`GET /model/usage` ②`GET /billing/usage` ③前端 `/profile` 与 `/billing` | ①`data.quota_unit=="字数额度"` ②`quota_unit_label` 同值 ③展示为「已用 123.5 万 / 共 300 万」；小于 1 万时展示精确到字（如「已用 8,432 字」）；**`token` 命中 0** | `test_usage_endpoints.py::test_usage_returns_quota_unit_label` [新增] + `#usage-format` [手工] |

#### 4.4.4 P2-BL-04 订单 / 账单 / 支付（7 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-BL04-01 | P2-BL-04 验收 1 | `OA_PAY_CHANNEL=fake` | ①`POST /billing/orders {payment_method:"online"}` ②模拟回调 ③`GET /billing/subscription` ④`GET /billing/orders/{id}` | ①`code=0`，`status=="pending_payment"`，`pay_url` 非空，`expires_at - created_at == 30min ±5s` ②回调 `code=0` ③`status=="active"` ④`status=="paid"` 且 `paid_at` 非空 | `test_billing_and_callback.py::test_online_payment_full_flow` [新增] |
| TC-P2-BL04-02 | P2-BL-04 验收 2 | 企业版 | ①下单 `offline_transfer` ②`GET /billing/orders/{id}` ③`POST /ops/orders/{id}/confirm {paid_amount_cents, remark}` ④查订阅 | ①`bank_info` 非空且含户名/账号/金额（账号为掩码）②`payment_no_masked is None` ③`code=0 and status=="paid"` ④订阅生效 `status=="active"` | `test_billing_and_callback.py::test_offline_transfer_confirm_activates_subscription` [新增] |
| TC-P2-BL04-03 | P2-BL-04 验收 3 | 已有 1 个待支付订单 | 同一回调 payload 连发 3 次 | 3 次均 HTTP 200；`billing_subscription` 只被激活 1 次（`started_at` 不变）；`billing_invoice` 该订单只有 **1** 条；`payment_callback_log` 只有 **1** 条 | `test_billing_and_callback.py::test_callback_three_times_activates_once` [新增] |
| TC-P2-BL04-04 | P2-BL-04 功能 5（验签） | 构造错误签名 | 发送签名错误的回调 | 返回非成功语义（`code!=0`）；`payment_callback_log.signature_ok is False` 留痕 1 条；**订阅未变更**（`status` 与 `started_at` 不变） | `test_billing_and_callback.py::test_callback_with_bad_signature_rejected_and_logged` [新增] |
| TC-P2-BL04-05 | P2-BL-04 验收 4 | 待支付订单；注入 clock 推进 31 分钟 | ①`close_expired()` ②`GET /billing/orders/{id}` | ①返回关闭条数 1 ②`status=="closed"`；前端文案「订单已超时关闭，请重新下单」；再次下单可成功（`code=0`） | `apps/adapter/tests/unit/test_order_service.py::test_expired_order_closed` [新增]（clock 注入） |
| TC-P2-BL04-06 | P2-BL-04 验收 5 | 已有 `pending_payment` 订单 | 再 `POST /billing/orders` | `code==15004` 且文案含「请先完成支付」；`billing_order` 该企业 `pending_payment` 行数仍为 **1** | `test_billing_and_callback.py::test_duplicate_pending_order_returns_15004` [新增] |
| TC-P2-BL04-07 | P2-BL-04 验收 6 | 下单 299 元 10 席位 | ①`amount_cents` ②前端渲染 | ①`==29900`（整数）②页面文本精确含「¥299.00」（正则 `¥\d+\.\d{2}`）；`29900` 原文不出现在 DOM 文本中 | `test_billing_and_callback.py::test_amount_cents_and_currency` [新增] + `#billing-amount-format` [手工] |

### 4.5 运营域（8 条，对应 P2-BL-05）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-OPS-01 | P2-BL-05 验收 1 / 裁决 #6 | 企业 token（owner/org_admin/member 各一） | 对 8 个 `/ops/**` 端点逐个请求 | 24 次（8 端点 × 3 token）全部 `code==20003`；**不得返回 404**（防探测）；响应体不含任何企业数据 | `apps/adapter/tests/integration/test_ops_isolation.py::test_tenant_token_rejected_on_all_ops_endpoints` [新增] |
| TC-P2-OPS-02 | P2-BL-05 验收 2 | 对公转账待支付订单 | ①`POST /ops/orders/{id}/confirm` 带备注 ②查订单/订阅/审计 | ①`code=0 and status=="paid"` ②订阅 `active`；`audit_log` 含 `actor_id`=运营人、`tenant_id`=被操作企业、`change_summary` 含备注原文 | `apps/adapter/tests/integration/test_ops_endpoints.py::test_confirm_order_activates_and_audits` [新增] |
| TC-P2-OPS-03 | P2-BL-05 验收 3 | 3 个企业各有用量 | ①`GET /ops/metrics?range=30d` ②对比各企业 `/billing/usage` | `metrics.quota_units_total == sum(各企业 quota_units_used)`，**误差 0**（等号）；`tenant_count` 等于 `tenant` 表非删除行数 | `test_ops_endpoints.py::test_metrics_matches_billing_usage_sum` [新增] |
| TC-P2-OPS-04 | P2-BL-05 验收 4 | 某企业用量 71% | ①`PATCH /ops/settings {quota_warn_threshold:70}` ②等 5 分钟（或触发通知任务）③查站内提醒 | ①`code=0` ②③该企业管理员收到提醒 1 条，文案含「70%」；阈值 80 时不触发（回归） | `test_ops_endpoints.py::test_quota_threshold_takes_effect` [新增] |
| TC-P2-OPS-05 | P2-BL-05 验收 5 | — | ①`PATCH /ops/tenants/{id} {status:"suspended"}` ②该企业成员登录 ③调任意业务接口 | ①`code=0` ②HTTP 403 且文案「该企业账号已被停用，请联系平台客服」③同上；运营自身访问不受影响 | `test_ops_endpoints.py::test_suspend_tenant_blocks_login` [新增] |
| TC-P2-OPS-06 | P2-BL-05 功能 5（最小可见） | 被查询企业有对话、文档、成员邮箱 | 遍历 OPS1/OPS2/OPS4/OPS6 响应 | 响应 JSON 中不含：对话文本、文档 `content`、成员 `email` 明文（正则 `[\w.]+@[\w.]+` 命中 0）；仅允许计数与 `masked_email`（如 `w***n@example.com`） | `test_ops_isolation.py::test_ops_response_never_contains_content_or_email` [新增] |
| TC-P2-OPS-07 | P2-BL-05 端点错误码 | 待支付订单 299 元 | ①`confirm {paid_amount_cents: 19900}` ②`confirm` 正确金额后再 `confirm` 一次 | ①`code==15009` 且文案「订单金额与应付金额不一致，请核对后重试」②第二次 `code==15008` 且文案「该订单已处理，请勿重复操作」 | `test_ops_endpoints.py::test_confirm_amount_mismatch_and_duplicate` [新增] |
| TC-P2-OPS-08 | P2-BL-05 功能 4 | — | ①`PATCH /ops/settings {trial_days:21, announcement:"..."}` ②`GET /ops/settings` ③查前端公告位（若展示） | ①`code=0` ②回读值 == 提交值；`settings` 含 5 个键（阈值/试用天数/单位文案/公告/套餐状态）③公告文本与提交一致 | `test_ops_endpoints.py::test_patch_settings_effective` [新增] |

### 4.6 前端域（32 条）

> 前端无测试框架（本阶段不放行，裁决 2），落点为两类：`apps/web/scripts/*.mjs` 静态断言（自动、纳入本地门禁）与 `apps/web/docs/qa-manual-checklist-phase2.md` 手工走查（6 字段 + 截图留证）。每格判据仍为可断言的字符串/元素级要求。

#### 4.6.1 P2-UI-01 知识库详情（5 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UI01-01 | P2-UI-01 验收 1（URL） | 库 `kb_x` 存在 | 直接访问 `/knowledge/kb_x?tab=grants`，再刷新，再复制到新标签 | 三标签均可切换；URL 含 `?tab=grants` 时**首屏即停在成员授权**（无需点击）；三态键 `tab` ∈ `{documents,grants,info}`；非法 `tab` 回落到 `documents` | `#kb-detail-tab` [手工] |
| TC-P2-UI01-02 | P2-UI-01 验收 1（进度） | 空库 | 连续上传 3 个文档，观察 30s | 3 行均从「已上传」推进到「可使用」；进度条数值单调不减；轮询在全部完成后停止（Network 面板 5s 内无新 `documents` 请求） | `#kb-upload-progress` [手工] |
| TC-P2-UI01-03 | P2-UI-01 验收 1（删除） | 库内有 3 个文档 | 点删除 → 确认框 → 确认 | 确认框文案含「该库下 3 个文档将一并删除，删除后不可恢复」；确认后跳回 `/knowledge`；toast 文本精确为「知识库已删除」；列表不再出现 | `#kb-delete-flow` [手工] |
| TC-P2-UI01-04 | P2-KB-01 验收 3（置灰） | member 无 `knowledge:base:update` | 打开详情页，hover 设置与删除按钮 | 两按钮 `disabled`（`[disabled]` 属性存在）；hover 出现 tooltip 且文案为原因句（含「权限」，非空、非错误码）；点击无效（无请求发出） | `#kb-disabled-buttons` [手工] + `apps/web/scripts/check-page-states.mjs`（断言存在 `Tooltip` 引用） |
| TC-P2-UI01-05 | P2-UX-01 / P2-KB-02 功能 2 | 注入 500；库内 1 个失败文档 | ①看三态 ②展开失败行 | 表格 8 行骨架 → 内容无抖动；错误态含重试；失败行文案「处理未成功：…」+「重新处理」按钮；展开区显示「内容片段数 12」（列表不显示） | `check-page-states.mjs` + `#kb-detail-states` [手工] |

#### 4.6.2 P2-UI-02 组织架构（4 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UI02-01 | P2-UI-02 验收 1（拖拽） | 3 层树 | 把部门 B 拖到部门 C 下，刷新页面 | 刷新后层级仍为 A→C→B；`PATCH` 请求体 `parent_id` 正确；`path_names` 更新 | `#org-drag` [手工] |
| TC-P2-UI02-02 | P2-UI-02 验收 1（第 6 层） | 已存在 5 层，选中第 5 层 | 点「新建部门」 | 按钮 `disabled`；提示文案含「最多 5 层」；点击后无 `POST` 请求（Network 断言 0 次） | `#org-level-limit` [手工] |
| TC-P2-UI02-03 | P2-UI-02 验收 1（只读） | member 无 `team:structure:manage` | 右键任意部门 | 无右键菜单（`[role="menu"]` 不存在）；顶部「新建部门」`disabled`；树可展开但不可拖拽 | `#org-readonly` [手工] |
| TC-P2-UI02-04 | P2-UI-02 加载性能 + 空态 | 5 层 50 节点 | 首次进入，`performance.now()` 打点 | 树渲染完成 ≤300ms（`PerformanceObserver` 或 `requestAnimationFrame` 打点）；空树时文案「还没有部门」+「新建部门」按钮 | `#org-perf-empty` [手工] |

#### 4.6.3 P2-UI-03 套餐与账单（3 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UI03-01 | P2-UI-03 验收 1（三色） | 用量分别造 79% / 80% / 100% | 三次读取进度条 `aria-valuenow` 与计算样式 | 79% → `background-color` 为 `#2563EB`；80% → `#D97706`；100% → `#DC2626`；`aria-valuenow` 等于百分比整数 | `#billing-progress-color` [手工] |
| TC-P2-UI03-02 | P2-UI-03 验收 1（升级） | 当前 free | 点升级 → 选席位 → 确认金额 → 支付 | 3 步内完成（点击次数 ≤3 次到达支付页/二维码）；抽屉内金额与 `amount_cents/100` 一致；成功后套餐卡即时刷新为 professional | `#billing-upgrade-3-steps` [手工] |
| TC-P2-UI03-03 | P2-UI-03 验收 1（分页空态） | 25 个订单、0 张账单 | ①翻页至第 2 页 ②切到账单标签 | ①每页 20/5 条，页码总数 2，无重复 `order_id` ②账单空态文案「当前账期还没有生成账单」，与订单空态「还没有订单记录」**不同** | `#billing-pagination-empty` [手工] |

#### 4.6.4 P2-UI-04 运营后台（3 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UI04-01 | P2-UI-04 验收 1 | 企业 owner 账号 | 直接访问 `/ops/tenants` | 不渲染运营侧边栏；展示文案「当前账号没有该操作的权限」+ 返回入口；Network 中 `/ops/tenants` 响应 `code==20003`；页面无白屏 | `#ops-blocked` [手工] |
| TC-P2-UI04-02 | P2-UI-04 验收 1（核销） | 1 个待支付对公订单 | 点「确认收款」→ 填金额与备注 → 二次确认 | 弹窗含二次确认句「确认已收到该笔款项？核销后套餐将立即生效」；确认后列表该行状态**即时**变「已支付」（无需手动刷新）；金额不符时提示「订单金额与应付金额不一致…」 | `#ops-confirm-order` [手工] |
| TC-P2-UI04-03 | P2-UI-04 验收 1（空态/反馈） | 时间范围内无数据 | ①看统计看板 ②改配置并保存 | ①4 个指标卡显示 `0` 而非空白，2 张图显示「所选时间范围内没有数据」②保存后 toast 精确为「已保存」，刷新后值仍为新值 | `#ops-empty-and-save` [手工] |

#### 4.6.5 P2-UI-05 既有 6 页深化（8 条）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UI05-01 | P2-UI-05（/chat 错误态） | 注入 500 | 发一条消息 | 消息区展示「回答暂时没有生成成功，请重试」+ 重试按钮；点击重试后 Network 出现**同一** URL 的第二次请求；页面不整页刷新（`performance.navigation.type != 'navigate'`） | `#chat-error-state` [手工] + `check-page-states.mjs`（`ErrorState` 存在于 `chat/page.tsx`） |
| TC-P2-UI05-02 | P2-UI-05 + P2-KB-04 | 回答含 1 条引用 | 点引用条 → 抽屉 → 点「在文档中定位」 | 抽屉宽 480px（`getBoundingClientRect().width` 在 476-484 之间）；展示库名/文档名/片段全文/前后文/「第 12 页 · 第 3 段」；跳转 URL 含 `doc=` 与 `segment=`；目标片段有高亮类 | `#chat-citation-drawer` [手工] |
| TC-P2-UI05-03 | P2-UI-05（/knowledge） | 6 个库、1 个 filtered 无结果 | ①看卡片 ②筛选无结果 | ①每卡显示 3 个徽标（精细度/可见范围/文档数），文案为「标准」「精细」「全企业可见」「指定成员可见」「仅自己可见」②空态两种文案不同：「还没有知识库…」vs「没有找到符合条件的结果」 | `#knowledge-badges-empty` [手工] |
| TC-P2-UI05-04 | P2-UI-05 + P2-TL-04 | 3 款已连接 | 打开 `/plugins` →「办公工具」页签 | 5 张卡片（含 2 张未连接）；每张显示名称/能力数/状态/最近调用时间；状态文案 ∈ `{已连接, 未连接, 授权已过期}`；断开需二次确认「断开后将无法在对话中使用该工具」 | `#plugins-tools-tab` [手工] |
| TC-P2-UI05-05 | P2-UI-05（/workflows） | 1 个失败任务 | 点「重试」 | 任务列表显示中文任务名（非 `workflow_run`/ID）；失败详情给业务语言原因（如「步骤「生成方案」未完成：所用工具尚未连接」）；重试生成**新任务**且原记录保留（列表条数 +1） | `#workflows-retry` [手工] |
| TC-P2-UI05-06 | P2-UI-05（/team） | 席位已满 | ①看三标签 ②点邀请 | ①三标签「成员/组织架构/操作记录」，点组织架构跳 `/team/organization` ②邀请按钮 `disabled` 且旁有「增加席位」链接 | `#team-tabs-seat` [手工] |
| TC-P2-UI05-07 | P2-UI-05（/profile） | 用量 63% | 打开 `/profile` | 「套餐与用量」区块显示「本月字数额度 已用 189 万 / 共 300 万」；点该区块跳 `/billing`；无权限用户不显示企业账单入口（入口元素不存在） | `#profile-usage` [手工] |
| TC-P2-UI05-08 | P2-UI-05 验收（导航/占位） | 全站 | 遍历 10 页 | 侧边栏当前项高亮与页面标题一致（文本相等）；全站文本「暂不支持」「敬请期待」命中 **0**；每页空态文案为业务语言（无英文、无 ID） | `apps/web/scripts/check-no-placeholders.mjs`（自动）+ `#nav-title` [手工] |

#### 4.6.6 P2-UX-01 全页面状态兜底（9 条，对应 §3.6 的 40 格）

| 编号 | 需求 | 前置条件 | 步骤 | 期望（可断言） | 自动化落点 |
| --- | --- | --- | --- | --- | --- |
| TC-P2-UX-01 | P2-UX-01 验收 1（断网） | build 产物已启动 | 10 页逐一 DevTools Offline | 10/10 出现 ErrorState + 顶部固定条「网络连接已断开，请检查网络后重试」；`innerText` 非空；错误码正则 0 命中；恢复网络后点重试可恢复 | `#ux-offline-10-pages` [手工] |
| TC-P2-UX-02 | P2-UX-01 验收 1（500） | 同上 | 10 页逐一拦截响应改 500 | 10/10 出现业务文案 + 重试；文案句式「<发生了什么>，<怎么办>」；无 `500`/`Internal`/堆栈 | `#ux-500-10-pages` [手工] |
| TC-P2-UX-03 | P2-UX-01 验收 1（超时） | 同上 | 10 页逐一 delay 30s | 15s 内出现超时错误态（不无限转圈）；有重试按钮；`performance` 中请求被 abort 或超时标记 | `#ux-timeout-10-pages` [手工] |
| TC-P2-UX-04 | P2-UX-01 验收 1（401） | token 改为过期 | 10 页逐一访问 | 10/10 跳转 `/login`；页面与 toast 中 `20002` 与「token」命中 0；登录后回到原页 | `#ux-401-10-pages` [手工] |
| TC-P2-UX-05 | P2-UX-01 验收 2 | 列表页各 2 种状态 | `/knowledge`、`/team`、`/plugins`、`/workflows`、`/billing` 的「无数据」与「筛选无结果」 | 每页两态文案 `!=`；「无数据」态含行动按钮（新建/邀请/连接/安装），「筛选无结果」态含「清除筛选」 | `#ux-empty-vs-filtered` [手工] |
| TC-P2-UX-06 | P2-UX-01 验收 3 | 构建产物 | 扫 10 页可见文本 | `\b[1-3]\d{4}\b` 命中 **0**；英文技术词（`error/exception/traceback/null/undefined/NaN`）命中 **0** | `apps/web/scripts/check-banned-terms.mjs --built`（自动） |
| TC-P2-UX-07 | P2-UX-01 验收 4 | 注入持续失败 | 点重试 3 次 | 第 3 次展示「多次尝试未成功，请稍后再试或联系管理员」；前 2 次为重试态；计数器在成功后归零（再失败仍从「请重试」开始） | `#ux-retry-3-times` [手工] |
| TC-P2-UX-08 | P2-UX-01 功能 5 | 同一错误连续触发 3 次（间隔 <3s） | 观察 toast 数量 | toast **1 个**；3s 后再触发 → 第 2 个；离线时顶部条只有 1 条（不叠加） | `#ux-toast-dedupe` [手工] |
| TC-P2-UX-09 | P2-UX-01 验收 5 | 4x CPU 降速 | 逐页首屏录制 | 骨架屏 → 内容切换无布局抖动（CLS < 0.1，Lighthouse）；白屏持续 ≤300ms（`PerformanceObserver` 的 `paint` 记录） | `#ux-cls` [手工] + Lighthouse 报告归档 |

### 4.7 覆盖度自检表（24 条 P0 → 用例映射）

| # | 需求号 | PRD 可执行断言数 | 对应用例 | 覆盖 | 缺口说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | P2-KB-01 | 5 | TC-P2-KB01-01 ~ 06 | ✅ 5/5 | — |
| 2 | P2-KB-02 | 5 | TC-P2-KB02-01 ~ 07 | ✅ 5/5 | — |
| 3 | P2-KB-03 | 6 | TC-P2-KB03-01 ~ 08 + TC-P2-PERM-01/02 | ✅ 6/6 | — |
| 4 | P2-KB-04 | 5 | TC-P2-KB04-01 ~ 06 + TC-P2-SEG-01 ~ 06 | ✅ 5/5 | — |
| 5 | P2-KB-05 | 5 | TC-P2-CRED-01 ~ 07 | ✅ 5/5 | 码值 `11007` 与文案「该知识库暂时无法访问，请联系管理员」按交付总监裁决 1.1 锁定（见 §0 与第 11 章） |
| 6 | P2-TL-01 | 6 | TC-P2-TL01-01 ~ 06 | ✅ 6/6 | 真实发送为人工验收单（§1.2） |
| 7 | P2-TL-02 | 5 | TC-P2-TL02-01 ~ 05 | ✅ 5/5 | 同上 |
| 8 | P2-TL-03 | 5 | TC-P2-TL03-01 ~ 05 | ✅ 5/5 | 同上 |
| 9 | P2-TL-04 | 5 | TC-P2-TL04-01 ~ 06 | ✅ 5/5 | — |
| 10 | P2-ID-01 | 6 | TC-P2-ID01-01 ~ 07 | ✅ 6/6 | — |
| 11 | P2-ID-02 | 5 | TC-P2-ID02-01 ~ 07 | ✅ 5/5 | — |
| 12 | P2-ID-03 | 5 | TC-P2-ID03-01 ~ 08 | ✅ 5/5 | — |
| 13 | P2-ID-04 | 5 | TC-P2-ID04-01 ~ 06 | ✅ 5/5 | — |
| 14 | P2-BL-01 | 4 | TC-P2-BL01-01 ~ 04 | ✅ 4/4 | — |
| 15 | P2-BL-02 | 5 | TC-P2-BL02-01 ~ 07 | ✅ 5/5 | — |
| 16 | P2-BL-03 | 6 | TC-P2-BL03-01 ~ 04 + TC-P2-USG-01 ~ 07 | ✅ 6/6 | — |
| 17 | P2-BL-04 | 6 | TC-P2-BL04-01 ~ 07 | ✅ 6/6 | 真实渠道联调为人工单（§1.2） |
| 18 | P2-BL-05 | 5 | TC-P2-OPS-01 ~ 08 + TC-P2-UI04-01 ~ 03 | ✅ 5/5 | — |
| 19 | P2-UI-01 | 3 | TC-P2-UI01-01 ~ 05 | ✅ 3/3 | — |
| 20 | P2-UI-02 | 3 | TC-P2-UI02-01 ~ 04 | ✅ 3/3 | — |
| 21 | P2-UI-03 | 3 | TC-P2-UI03-01 ~ 03 | ✅ 3/3 | — |
| 22 | P2-UI-04 | 3 | TC-P2-UI04-01 ~ 03 | ✅ 3/3 | — |
| 23 | P2-UI-05 | 3 | TC-P2-UI05-01 ~ 08 | ✅ 3/3 | — |
| 24 | P2-UX-01 | 5 | TC-P2-UX-01 ~ 09 + TC-P2-WORD-01 ~ 04 | ✅ 5/5 | — |
| — | **合计** | **114** | 178 条用例 | **100%** | 无缺口 |

**自检结论**：24 条 P0 全部至少 1 条用例，114 条可执行断言全部有对应用例；**无未映射的 P0**。高风险域（多租户/凭据/计量/权限）断言密度为 2.2-3.0 条/用例，高于其他域（1.4）。

---

## 5. 分层测试落点

### 5.1 单元层（`apps/adapter/tests/unit/**`，31 条）

原则：**不碰网络、不碰 DB**，只测纯函数与状态映射；所有外部依赖用 mock 替身。

> 下表「支撑用例」指该文件承担其**核心逻辑**验证；部分用例的主落点在集成层（同一编号可两处出现，不重复计数）。合计 31 条指"主落点为单元层"的用例数。

| 文件 | 状态 | 覆盖用例 | 关键断言点 | 需 DB |
| --- | --- | --- | --- | --- |
| `tests/unit/test_db_session.py` | [新增] | TC-P2-SEC-01/05 | `set_config` 事务级复位；静态守卫（无会话级写法、出现次数 == 1） | 是（1 条） |
| `tests/unit/test_crypto.py` | [新增] | TC-P2-CRED-03（部分） | AES-256-GCM 加解密往返；错误密钥抛异常；`key_version` 选择正确密钥；nonce 12 字节且不重复 | 否 |
| `tests/unit/test_credential_service.py` | [新增] | TC-P2-CRED-01/02/04/06 | **`test_bound_failure_never_falls_back`：下游调用计数 == 0**；`source` 只有 `global/bound`；缓存不存明文 | 否 |
| `tests/unit/test_knowledge_base_service.py` | [新增] | TC-P2-KB01-05 | 改 `chunk_policy` 不触发旧文档操作；名称长度/描述长度校验边界 | 否 |
| `tests/unit/test_document_service.py` | [新增] | TC-P2-KB02-02 | `map_stage` 5 态参数化；`index_status ⟺ stage` 双向一致；非法组合抛错 | 否 |
| `tests/unit/test_grant_service.py` | [新增] | TC-P2-KB03-06 | 同主体幂等 → `skipped_count`；`guest+editor` 前置拦截 → `20001` | 否 |
| `tests/unit/test_segment_service.py` | [新增] | TC-P2-SEG-06 | `build_key` 稳定性（同内容同键）、内容敏感性（改一字变键）、空白归一 | 否 |
| `tests/unit/test_wecom_connector.py` | [新增] | TC-P2-TL01-03/05/06 | `message_id` 透传；错误码映射矩阵（4 组）；响应/日志禁词 | 否 |
| `tests/unit/test_feishu_connector.py` | [新增] | TC-P2-TL02-02/03/04 | 时区字段；`13005` 映射；`warnings` 结构（部分成功不判失败） | 否 |
| `tests/unit/test_wechat_mp_connector.py` | [新增] | TC-P2-TL03-02/03/05 | 未确认不发布（发布调用计数 0）；`null` vs `0`；`20008`/`13006` | 否 |
| `tests/unit/test_usage_service.py` | [新增] | TC-P2-USG-03/04/05 | 结算时点；折算公式 `prompt+ceil(len/1)`；幂等键去重 | 否 |
| `tests/unit/test_department_service.py` | [新增] | TC-P2-ID01-03 | `path_names` 组装（无 N+1）；`level` 计算；超 5 层拦截 | 否 |
| `tests/unit/test_subscription_service.py` | [新增] | TC-P2-BL02-07 | `past_due` 7 天 → `expired`；降级 `pending_change` 时点 | 否（clock 注入） |
| `tests/unit/test_order_service.py` | [新增] | TC-P2-BL04-05 | 30 分钟超时 → `closed`；金额分单位计算 | 否（clock 注入） |
| `tests/unit/test_ops_service.py` | [新增] | TC-P2-OPS-06 | 聚合结果不含敏感字段（白名单式断言键集合） | 否 |
| `tests/unit/test_error_messages_business_language.py` | [新增] | TC-P2-WORD-03 | 遍历 `ERROR_REGISTRY` 全部 message 过禁词表；同码多文案逐条登记一致 | 否 |
| `tests/unit/test_idempotency.py` | **[复用]** | 全部幂等语义 | 现有 28 条用例不改；新增 38 端点复用同一 `build_fingerprint`，只需在集成层断言 body 摘要生效 | 否 |
| `tests/unit/test_errors.py` | **[复用]** | 错误码注册制 | 现有 12 条；新增 14 码的注册校验并入 `test_error_messages_business_language.py` | 否 |
| `tests/unit/test_security.py` | **[复用]** | 鉴权单元 | 现有 16 条不改 | 否 |
| `tests/unit/test_rag_service.py` / `test_model_service.py` / `test_agent_service.py` / `test_tool_service.py` / `test_identity_service.py` / `test_registry.py` / `test_clients.py` / `test_ids.py` / `test_ratelimit.py` | **[复用]** | 阶段一协议转换 | 不改断言；仅当签名变化时改构造参数（见 6.1） | 否 |

### 5.2 集成层（`apps/adapter/tests/integration/**`，100 条）

| 文件 | 状态 | 覆盖用例 | 说明 |
| --- | --- | --- | --- |
| `tests/integration/test_endpoints.py` | **[复用]** | 阶段一 46 条 | 不改断言；新增扩展端点断言放新文件（见下） |
| `tests/integration/test_security_regressions.py` | **[复用]** | 阶段一 22 条 | oidc 5 场景、租户锁定、限流、20008、幂等 body 摘要——**全部保留为阻断项** |
| `tests/integration/test_health.py` | **[复用]** | 15 条 | 新增 `/readyz` 在 DB 不可达时 `not_ready` 断言（追加 1 条） |
| `tests/integration/test_migrate.py` | [新增] | TC-P2-SEC-01（环境）、G-11 | 临时 schema 跑两遍；表数/套餐数/权限点数断言 |
| `tests/integration/test_endpoint_baseline.py` | [新增] | TC-P2-TL04-02、G-07/08/09 | 双基线计数 + 加性 diff + tools 端点 schema 仅数组变化 |
| `tests/integration/test_tenant_isolation.py` | [新增] | TC-P2-SEC-02/03/04/06/07 | 池=1 交替、并发 20×5、fail-closed、平台哨兵、连接身份 |
| `tests/integration/test_credential_binding.py` | [新增] | TC-P2-CRED-01/02/04/05/07 | KB8 鉴权、换绑即时生效、故障不扩散 |
| `tests/integration/test_kb_meta_endpoints.py` | [新增] | TC-P2-KB01-01~04/06、KB02-01/03/04/06/07 | KB1/KB2/KB3 + R4 扩展 |
| `tests/integration/test_kb_document_scale.py` | [新增] | TC-P2-KB02-05 | 5000 文档分页与体积（`@slow`） |
| `tests/integration/test_grant_and_backlink.py` | [新增] | TC-P2-KB03-01~08、KB04-01~04 | 授权闭环 + 三处同一套判定 + 引用字段 |
| `tests/integration/test_segment_stale_lifecycle.py` | [新增] | TC-P2-SEG-01~05 | 三态机 S1-S7 全流程 |
| `tests/integration/test_permission_matrix.py` | [新增] | TC-P2-PERM-01~08 | 笛卡尔矩阵参数化（8 账号 × 入口） |
| `tests/integration/test_compat_extensions.py` | [新增] | 6.2 全部 9 行 | 8 处扩展的向后兼容断言（旧字段在、类型不变、枚举只增） |
| `tests/integration/test_tool_executions.py` | [新增] | TC-P2-TL01-01/02/04、TL02-01/05、TL03-01/04、TL04-01/03/04/05/06 | 注册表 5 款、TL1、连接并存/优先级 |
| `tests/integration/test_usage_endpoints.py` | [新增] | TC-P2-USG-01、BL03-01~04 | M4 与 BL5 同源、breakdown、单位 |
| `tests/integration/test_usage_reconcile.py` | [新增] | TC-P2-USG-02/06/07 | 求和为 0、对账脚本、同事务原子性 |
| `tests/integration/test_org_endpoints.py` | [新增] | TC-P2-ID01-01~07、ID02-01~05/07、ID03-01~08、ID04-01~06 | 组织树/权限点/成员/审计 |
| `tests/integration/test_billing_and_callback.py` | [新增] | TC-P2-BL01-01~03、BL02-01~06、BL04-01~04/06/07 | 套餐/订阅/席/订单/回调 |
| `tests/integration/test_ops_endpoints.py` | [新增] | TC-P2-OPS-02~05/07/08 | 核销、统计、阈值、配置 |
| `tests/integration/test_ops_isolation.py` | [新增] | TC-P2-OPS-01/06、PERM-05 | 企业 token 全拒 + 最小可见 |

### 5.3 数据库用例与 skip 机制（86 条需 PG）

| 项 | 口径 |
| --- | --- |
| 标记 | 需真实 PostgreSQL 的用例统一打 `@pytest.mark.pg`（`pytest.ini` 注册 `pg`/`slow`/`p0`，开启 `--strict-markers`，未注册标记直接报错） |
| 连接 | 读环境变量 `OA_TEST_DATABASE_URL`（本地可用 `docker run -p 5432:5432 postgres:18.6-alpine` 或既有 `oa-postgres` 容器；CI 配置保留 `postgres:18.6-alpine` service 定义待额度恢复）；缺失时**不是静默通过**，而是 `pytest.skip("未提供 OA_TEST_DATABASE_URL")` |
| 本地默认 | `pytest tests -q`（不带 `--run-pg`）→ pg 组 collected but skipped，其余全跑，保证 30s 内出结果 |
| 强制复跑 | `pytest tests -q --run-pg`；**带 `--run-pg` 时 `skipped != 0` 即失败**（解析 `pytest -q` 输出尾行），防止"本地跳过、事后也跳过"的假绿。本轮以此命令的本地输出作为判定依据（裁决 4） |
| 数据隔离 | 每个测试会话创建独立 schema `qa_<runid>`（`--runid` 由执行者/worker id 注入），用例结束 `DROP SCHEMA ... CASCADE`；避免并行执行互撞 |
| 用例内隔离 | 每个用例包在一个事务里，结束 `rollback`（`uow` 夹具）；只有"并发/多连接"用例（`SEC-02/03`）必须用真实提交 + 显式清理 |
| 迁移用例 | `test_migrate.py` 在**临时空 schema** 上跑全量 DDL 两遍，不污染 `public`；断言 `schema_migration` 记录数与 checksum 稳定 |
| 规模用例 | `test_kb_document_scale.py` 打 `@slow`，默认跳过（`-m "not slow"`），阶段末单独执行一次（本地，含 5000 文档造数）；清理用 `TRUNCATE ... RESTART IDENTITY CASCADE` 限定该 schema |

```python
# tests/conftest.py（本阶段新增，追加到既有文件末尾）
def pytest_addoption(parser):
    parser.addoption("--run-pg", action="store_true", default=False, help="运行需要 PostgreSQL 的用例")

def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-pg"):
        return
    skip = pytest.mark.skip(reason="需要 PostgreSQL（加 --run-pg 或设置 OA_TEST_DATABASE_URL）")
    for item in items:
        if "pg" in item.keywords:
            item.add_marker(skip)
```

### 5.4 前端层

| 手段 | 内容 | 命令 | 判据 |
| --- | --- | --- | --- |
| 类型 | `tsc --noEmit`（`strict` + `noUncheckedIndexedAccess` 已开） | `cd apps/web && npm run typecheck` | 退出码 0；新增 38 端点类型与后端字段**逐字段同名**（`types.ts` 与 `app/schemas/billing.py` 等对齐） |
| 规范 | eslint `--max-warnings=0` | `npm run lint` | 0 error / 0 warning |
| 构建 | `next build` | `npm run build` | 10 业务路由 + `/login` + `/` 全部生成 |
| 禁词 | `scripts/check-banned-terms.mjs` 三面扫描 + `--self-test` + `--built` | 见 §3.7 | 0 命中；自检退出码 1 |
| 占位词 | `scripts/check-no-placeholders.mjs` | 同上 | 「暂不支持」「敬请期待」0 命中 |
| 三态静态断言 | `scripts/check-page-states.mjs` | 同上 | 10/10 页面三态 + `onRetry` |
| 手工走查 | 40 格异常矩阵 + 24 条 UI 用例 | `npm run build && npm start -p 3100` | 逐格填 6 字段（页面/场景/操作/期望/实际/截图文件名），实际列抄录文案原文；`实际` 写「正常」或缺失截图 = 未执行（见 §3.6.4） |

**为什么本阶段不引入 Vitest/Playwright（交付总监裁决 2：不放行）**：① **本仓库 GitHub Actions 额度本月已用尽，CI 实际不会运行**，引入新框架等于给交付物加一条"无法在 CI 验证"的依赖；② 会新增 2 个依赖与 1 套配置，触发 G-22 依赖登记流程；③ React 19 + Next 16 的测试工具链兼容性未验证，引入风险高于收益；④ 体验类判据（文案、置灰、二次确认、拖拽）用静态断言 + 留证走查已可满足"可核对"要求。**代价**：运行期动态拼接文案无法自动覆盖 → 已登记为 R-3，第三阶段转 e2e。

**替代证明的强度要求**：手工走查不是"降级为人工感觉"，而是把 40 格变成 40 条**带截图与文案原文的结构化证据**；每格判据仍是字符串/元素级（如「出现 `a[href="/billing"]`」「页面文本 `\b[1-3]\d{4}\b` 命中 0」），因此可复核、可追溯、可在第三阶段直接翻译成 e2e 断言。

### 5.5 新增夹具清单（`tests/conftest.py` 追加，不改既有夹具）

| 夹具 | 作用 | 被哪些用例使用 |
| --- | --- | --- |
| `pg_dsn` | 读 `OA_TEST_DATABASE_URL`，缺失跳过 | 全部 `pg` 用例 |
| `db_schema`（session 级） | 建/删 `qa_<runid>` schema | 同上 |
| `uow` | 租户作用域事务，用例结束回滚 | 绝大多数集成用例 |
| `two_tenants` | 造 `tnt_a` / `tnt_b` 及各自哨兵库名 | SEC-02/03、PERM-06 |
| `mock_dify` | `httpx.MockTransport` + `calls` 计数器 + 可编程响应序列 | CRED-*、KB02-*、USG-03/04、TL-* |
| `clock` | 可注入 `now_fn`（供订单超时/订阅逾期/处理耗时） | BL02-07、BL04-05、KB02-01 |
| `seed_credential` | 用 `crypto` 模块加密金丝雀明文入库 | CRED-* |
| `kb_factory` / `doc_factory` | 直接 SQL 造库/文档（可控 `stage`/`run_id`/`tenant_id`） | KB0*/SEG-*/PERM-* |
| `usage_factory` | 造 `usage_event` + `usage_monthly` 三行 | USG-*、BL03-*、OPS-03 |
| `audit_factory` | 造 17 类动作记录 | ID04-* |
| `assert_no_forbidden` | 传入任意 dict/str，断言无禁词与错误码 | WORD-03、TL01-06、OPS-06 |

### 5.6 本阶段新增/改动文件总览

```
apps/adapter/tests/
├── conftest.py                        [改] 追加 §5.5 夹具（不动既有夹具）
├── unit/  + test_db_session.py, test_crypto.py, test_credential_service.py,
│            test_knowledge_base_service.py, test_document_service.py, test_grant_service.py,
│            test_segment_service.py, test_wecom_connector.py, test_feishu_connector.py,
│            test_wechat_mp_connector.py, test_usage_service.py, test_department_service.py,
│            test_subscription_service.py, test_order_service.py, test_ops_service.py,
│            test_error_messages_business_language.py                     （16 个新文件）
└── integration/ + test_migrate.py, test_endpoint_baseline.py, test_tenant_isolation.py,
                   test_credential_binding.py, test_kb_meta_endpoints.py,
                   test_kb_document_scale.py, test_grant_and_backlink.py,
                   test_segment_stale_lifecycle.py, test_permission_matrix.py,
                   test_compat_extensions.py, test_tool_executions.py,
                   test_usage_endpoints.py, test_usage_reconcile.py, test_org_endpoints.py,
                   test_billing_and_callback.py, test_ops_endpoints.py, test_ops_isolation.py
                                                                          （17 个新文件）

apps/web/
├── scripts/check-banned-terms.mjs   [新增]（B15 已规划，本方案补充 4 组词表 + 豁免规则）
├── scripts/check-no-placeholders.mjs  [新增]
├── scripts/check-page-states.mjs      [新增]
└── docs/qa-manual-checklist-phase2.md [新增] 40 格异常矩阵 + 24 条 UI 用例留证单

deploy/
├── validate_openapi.py                [新增]（架构 B02 已规划，本方案锁定 G-07/08/09 判据）
└── validate_error_catalog.py          → 建议并入 apps/adapter/scripts/check_error_catalog.py
```

---

### 5.9 映射校准（B05/B06 实测用例名，落实 QA-P3-05）

> **背景**：本方案 §4/§5 早期给出的「落点名」是**设计期预测名**，工程师实现时取名不同，导致「按编号找用例」时要多跳一次。依 R02 裁定（见 `docs/qa-phase2-waivers.md` §二 QA-P3-05），此处以**实测名**为准做一次校准；**用例编号与判据不变**，仅同步落点。

| 用例编号 | 方案原预测落点 | **实测落点（以此为准）** |
| --- | --- | --- |
| TC-P2-KB03-01 | `test_grant_and_backlink.py::test_restricted_base_hidden_from_ungranted_list` | `test_grant_and_backlink.py::test_restricted_base_is_visible_only_to_granted_member` |
| TC-P2-KB03-02 | `::test_restricted_base_detail_and_search_return_20003` | 同上（同一用例内含 KB3 直连与 R7 两条断言） |
| TC-P2-KB03-03 | `::test_chat_silently_skips_unauthorized_base` | `::test_conversation_silently_skips_unauthorized_base` |
| TC-P2-KB03-04 | `::test_department_grant_covers_subtree_and_revokes_within_5s` | `::test_department_grant_applies_to_current_members`（≤5s 由 `tests/unit/test_grant_service.py::test_permission_cache_expires_within_five_seconds` 证 TTL） |
| TC-P2-KB03-05 | `::test_role_grant_effective_and_revocable` | `::test_revoke_grant_takes_effect_immediately`（角色授权生效在 `tests/unit/test_grant_service.py`） |
| TC-P2-KB03-06 | `::test_duplicate_grant_counts_as_skipped` | `::test_duplicate_grant_is_skipped`（批内重复见 `tests/unit/test_grant_service.py::test_create_grants_deduplicates_within_single_request`） |
| TC-P2-KB03-07 | `::test_guest_editor_grant_rejected_with_20001` | `tests/unit/test_grant_service.py`（`guest+editor` → `20001`）+ 集成层 `::test_grant_endpoints_require_permission` |
| TC-P2-KB03-08 | `::test_owner_grant_cannot_be_revoked_and_batch_limit` | `::test_grant_endpoints_require_permission` + `::test_grant_records_are_audited` |
| TC-P2-KB04-01~02 | `::test_kb4_*`（M1/M2 citation 字段） | `tests/unit/test_model_service.py`（citation 新字段）+ `::test_kb4_returns_valid_segment_with_context` |
| TC-P2-KB04-03 | `::test_r7_returns_backlink_fields` | `tests/integration/test_endpoints.py::TestRagEndpoints::test_r7_search` + QA `test_qa_b05_b06_verification.py::test_a1_*` |
| TC-P2-SEG-01 | `test_segment_stale_lifecycle.py::test_*_stale` | 方案原计划的独立文件**未建**，实测落 `test_grant_and_backlink.py::test_kb4_returns_stale_after_reprocessing` + QA `test_qa_b05_b06_verification.py::test_a6_*` |
| TC-P2-SEG-02 | `::test_reprocessing_keeps_valid` | `::test_kb4_failed_reprocessing_keeps_segment_valid` |
| TC-P2-SEG-03/04 | `::test_deleted_document/book_*` | `::test_kb4_error_boundaries`（`11004` / `11001` 边界） |
| TC-P2-SEG-05 | `::test_stale_beyond_retention_returns_null_content` | `tests/unit/test_segment_service.py`（保留期外 `content=None`）+ QA `test_a6_*` |
| TC-P2-SEG-06 | `tests/unit/test_segment_service.py::test_build_key_*` | 同名（`build_key` 稳定性/敏感性/空白归一） |
| TC-P2-PERM-01/07 | `test_permission_matrix.py::*` | 方案原计划的矩阵文件**未建**；B05 轮实测落 `test_grant_and_backlink.py::test_restricted_base_is_visible_only_to_granted_member` + QA `test_qa_b05_b06_verification.py::test_a1_/test_a2_/test_a2b_/test_a2c_/test_a3_`（含 ID 形态与跨租户维度） |
| TC-P2-PERM-08 | `::test_permission_cache_ttl` | `tests/unit/test_grant_service.py::test_permission_cache_expires_within_five_seconds` + QA `test_a5_*` |
| TC-P2-TL-01~04 | `test_tool_executions.py::test_t1_*` / `test_tl1_*` | `::test_t1_returns_five_tools_with_unchanged_shape`、`::test_tl1_returns_latest_twenty`、`::test_failed_execution_is_recorded_without_stack_or_code`、`::test_tl1_requires_permission` |
| TC-P2-TL-05 | `::test_t5_execute_*` | `::test_execute_without_connection_returns_13002_with_tool_message`、`::test_successful_execution_is_recorded` |

**两处结构性缺口（B05 轮已由 QA 独立用例补齐，后续批次请按正式文件名补齐）**：

| 方案要求的文件 | 现状 | 补偿 |
| --- | --- | --- |
| `tests/integration/test_segment_stale_lifecycle.py` | 未建（stale 断言并入 `test_grant_and_backlink.py`） | QA `test_qa_b05_b06_verification.py::test_a6_*` 覆盖 200+stale 与无错误码外露 |
| `tests/integration/test_permission_matrix.py`（8 账号 × 3 入口 24 格） | 未建 | **B08 必须补**（ID1-ID11 到位后才有 8 账号语义）；当前由 PERM 相关 4 条 QA 断言 + `test_grant_and_backlink.py` 覆盖 |

> 校准原则：**编号只增不改、判据只紧不松**。若后续批次改名，请在同一 PR 内同步本表，否则按 §6.1 R2 视作追溯缺陷。

---

## 6. 回归策略

### 6.1 既有 421 用例的保护规则（硬约束）

| 规则 | 内容 | 违规判定 |
| --- | --- | --- |
| R1 不删用例 | 421 条一条不得删除、不得 `skip` | 用例数 < 421 即红 |
| R2 不放宽断言 | 禁止把 `assert code == 20001` 改成 `assert code in (...)`；禁止把精确文案断言改成正则模糊匹配 | diff 中出现断言弱化即打回 |
| R3 只允许构造参数变化 | 因签名变化（如 `RagService.upload_document` 增加 `base_id` 解析）可改**调用方式**，不可改**期望值** | 需在 PR 描述列出改动点 |
| R4 默认跑内存实现 | 既有用例全部在 `OA_BUSINESS_STORE=memory` 下运行，保持 30s 内完成、不依赖 PG | 既有用例出现 `pg` 标记即红 |
| R5 双实现一致性 | 新增业务层在 `postgres` 与 `memory` 两实现下**返回结构必须一致**（用同一批契约断言跑两遍） | 结构 diff 非空即红 |
| R6 红线用例升级为阻断 | `test_security_regressions.py` 22 条（oidc 5 场景/租户锁定/限流/20008/幂等 body）由"参考"升级为"阻断"，任何一条失败即为 P0 | 失败即阻断合入 |

**R5 的落地方式**：`tests/integration/test_compat_extensions.py` 用 `@pytest.mark.parametrize("store", ["memory", "postgres"])` 跑同一组断言；`memory` 只验结构/错误码，`postgres` 另加 SQL 级断言。

### 6.2 8 处既有端点扩展的兼容性断言（逐端点）

| # | 端点 | 必须成立的兼容性断言 | 用例 |
| --- | --- | --- | --- |
| 1 | R1 `POST /knowledge/bases` | ①不传 `chunk_policy` 时 `code=0` 且 `data.chunk_policy=="default"` ②传 `fine` 生效 ③旧字段 `base_id/name/created_at` 仍在且类型不变 | TC-P2-KB01-01、`test_compat_extensions.py::test_r1_optional_chunk_policy` |
| 2 | R2 `GET /knowledge/bases` | ①`visibility` 旧值 `organization/private` 仍被接受 ②新增 `restricted` 可返回 ③条目新增 3 字段均可为空（旧库数据）④`meta` 结构不变 | `test_compat_extensions.py::test_r2_new_fields_are_additive` |
| 3 | R3 `POST .../documents` | ①请求字段名与取值域不变 ②响应新增 `stage` 且 `index_status` 仍为三态 ③不带 `chunk_policy` 走库级默认 | TC-P2-KB01-01、`test_compat_extensions.py::test_r3_stage_is_additive` |
| 4 | R4 `GET .../documents/{doc_id}` | ①旧字段全在 ②新增 4 字段可空 ③`segment_count` 仍存在（与 KB3 不同，这是关键差异） | TC-P2-KB02-07 |
| 5 | R7 `POST /knowledge/search` | ①`segments[].position` 结构不变 ②新增 3 字段 ③`score` 仍存在 ④`top_k` 超限仍 `20001` | TC-P2-KB04-03 + 既有 `test_r7_search` |
| 6 | M1 `POST /model/generate` | ①`citations[]` 旧字段 `document_name/segment_content/score` 全在 ②新增 4 字段可缺省（无知识库时为 `[]`）③`content/finish_reason/usage` 结构不变 | TC-P2-KB04-01 + 既有 `test_m1_generate_success` |
| 7 | M2 `POST /model/generate/stream` | ①事件名集合仍为 `delta/citation/done`（不得改名/新增破坏性事件）②`citation` 旧字段在 ③`done` 载荷不变 | TC-P2-KB04-02 + 既有 `test_m2_stream_emits_self_defined_events` |
| 8 | M4 `GET /model/usage` | ①字段名与结构不变（`period/conversation_count/document_qa_count/quota`）②新增 `quota_unit=="字数额度"` ③`period=month` 与非法值行为不变 | TC-P2-BL03-04 + 既有 `test_m4_usage_returns_business_units` |
| 9 | U3 / U6 | ①U3 旧 query 参数仍生效，新参数 `department_id/status` 可选 ②U6 旧请求体字段仍被接受，新筛选可选 ③响应新增字段均可空 ④`>90 天` 的 `20001` 只在 U6 新逻辑生效 | `test_compat_extensions.py::test_u3_u6_filters_are_optional` + TC-P2-ID04-04 |

**统一机械校验**：`python deploy/validate_openapi.py` 的 G4 必须对上述 9 行报 0 violation。人工断言（上表）与机械校验（G4）互为补充：G4 防"结构被改"，人工断言防"语义被改"（例如把 `documents` 的 `stage` 塞进 `index_status`，结构合法但语义错）。

### 6.3 回归基线数字与断言写法

| 指标 | 阶段一基线 | 本阶段目标 | 断言写法（本地复跑，裁决 4） |
| --- | --- | --- | --- |
| 用例总数 | 421 | ≥ 599 | `collected >= 599` |
| 失败/错误 | 0 | 0 | `failed == 0 and error == 0` |
| 跳过 | 0 | 0（带 `--run-pg` 时） | `skipped == 0` |
| 总覆盖率 | 90.95% | ≥ 90.5% | `--cov-fail-under=90.5` |
| 新模块覆盖率 | 无 | ≥ 85% | 单独 `--cov=app/business --cov=app/db --cov-fail-under=85` |
| 端点基线 | core=30 | core=30 且 phase2=38 | `validate_openapi.py` |
| 前端路由 | 6 业务页 + login | 10 业务页 + login | build 输出解析 |
| **CI 状态** | — | **配置就绪、待额度恢复后复跑** | 验收报告写「本地全绿 + CI 待复跑」，不写「CI 绿」 |

### 6.4 flaky 与重试用例规范

| 项 | 规则 |
| --- | --- |
| 允许重试的条件 | 仅"时间敏感"用例（`≤5s 生效`、`≤10s 落库`）允许 `pytest-rerunfailures` 重试 1 次，且重试必须在报告中可见 |
| 不允许重试 | 并发隔离（SEC-03）、幂等、权限矩阵、错误码——这些出现间歇性失败本身就是缺陷信号，重试等于掩盖 |
| 并发用例执行次数 | 每次交付前本地 1 轮；阶段末本地 5 轮（`--count 5`）；5 轮中出现 1 次跨租户行即 P0 |
| 时间断言口径 | 一律用 `time.monotonic()` 且断言上界留 1.5 倍余量（如 ≤5s 断言 `<= 7.5`），但**报告需同时记录实测值**；若实测值贴近上界需复测 3 次取中位数 |
| 环境依赖用例 | 依赖真实网络/真实渠道的用例**不写进测试套件**，写在人工验收单中，避免"偶发红"污染门禁 |

### 6.5 回归执行节奏

| 时点 | 范围 | 命令 | 出口条件 |
| --- | --- | --- | --- |
| 每批提交 | 该批新增 + 既有 421 | `pytest tests -q` + `validate_openapi.py` | 0 failed；G-07/08/09 绿 |
| 每批完成后 | 全量 + pg 组 | `pytest tests -q --run-pg` | skipped=0 |
| 每里程碑（T01-T05） | 全量 + 前端全门禁 + `/readyz` 自检 + 对账 | 第 2 章全部 **25 道** | 25/25 绿（本地输出留档） |
| 阶段末（第 16 周） | 24 条 P0 映射用例 + 全量 + 手工走查 40 格 | `pytest -q -m p0` + 走查单 + 截图归档 | 24 条 P0 全通；0 P0/0 P1 未闭环 |

---

## 7. 缺陷分级与准入准出

### 7.1 分级定义（结合本阶段真实场景）

| 级别 | 定义 | 本阶段典型例子 | 处置时限 | 是否阻断阶段门 |
| --- | --- | --- | --- | --- |
| **P0** | 数据/安全/契约/部署的不可接受缺陷 | ①跨租户可见任何一行数据（SEC-03 失败）②已绑定凭据回落全局（CRED-02 下游计数 >0）③无权限账号通过详情/检索入口拿到数据（PERM-01/07）④按成员求和 ≠ 企业总量（USG-02 `diff != 0`）⑤既有 30 端点任一契约被破坏（G-09 红）⑥禁词命中或出现占位文案（G-14/16/17）⑦服务启动失败或迁移不可重复 | 发现后 **4 小时内**定位、**24 小时内**修复 + 回归 | **是（一票否决）** |
| **P1** | 单端点功能/错误码/文案错误，或前端缺错误态 | ①`stage` 与 `index_status` 不一致 ②stale 返回 404 而非 200+stale ③回调重复执行导致重复开通 ④`11006` 文案不是「请稍后再删除」 ⑤`/chat` 无 ErrorState ⑥订单超时不关闭 ⑦`15003` 未在邀请路径触发 | 24 小时内定位、本批次内修复 | 是（阻断该批次准出） |
| **P2** | 边界/分页/排序/审计字段/UI 细节 | ①5000 文档第 250 页体积超 200KB ②关键字筛选大小写敏感 ③`ip_masked` 未打码 ④空态与筛选无结果文案相同 ⑤进度条阈值色差 1 档 | 本里程碑内修复 | 否（需登记豁免） |
| **P3** | 建议与优化 | 文案可更简洁、图标尺寸、tooltip 延迟 | 排入第三阶段 | 否 |

**判级原则**：① 能导致"用户看到不属于自己的数据"一律 P0；② 能导致"用户看到错误信息/白屏/技术词"至少 P1；③ 同一根因导致的多个现象按最高级别计 1 条，不重复计数（避免缺陷数虚高）；④ 实现与文档不一致时，先判"哪边是契约"——契约以架构裁决为准（§0），若代码与裁决不符即缺陷，若 PRD 与裁决不符则登记为文档缺陷（不计入代码缺陷数）。

### 7.2 批次准入条件（QA 侧 DoR）

一批进入 QA 验证前，工程师必须提供：

| # | 交付项 | 不满足的后果 |
| --- | --- | --- |
| 1 | 该批「验收命令」全绿（架构 9.2 每批已给命令） | 直接退回，不进入测试 |
| 2 | 该批涉及的端点已在 `docs/api/README.md` 与基线 JSON 同步 | G-07/08 必红，退回 |
| 3 | 新增错误码已三处同步（架构 §7 / `errors.py` / `business-errors.ts`） | G-13 必红，退回 |
| 4 | 该批测试文件已建（架构 9.2 已列出文件名）且用例数 ≥ 本方案第 9 章要求 | 缺口 >20% 退回 |
| 5 | 数据库迁移脚本幂等（若该批含 DDL） | G-11 必红，退回 |
| 6 | 已知未完成项清单（明示"本批不做"的边界） | 无清单则 QA 按 PRD 全量判，缺一项即缺陷 |

### 7.3 里程碑准出条件

| 里程碑 | 覆盖批次 | 准出条件（全部满足） |
| --- | --- | --- |
| T01 业务库与契约基线 | B01 B02 | G-01~G-13 全绿；SEC-01~07 全通；`validate_openapi.py` 双基线绿；迁移两遍幂等 |
| T02 知识库商用级 + 工具全通 | B03 B04 B05 B06 | 知识库 27 条 + 凭据 7 条 + 工具 22 条全通；stale 三态机 6 条全通；G2-1/G2-2 人工确认 |
| T03 计量与组织权限 | B07 B08 | USG 7 条 + 组织权限 28 条全通；对账偏差 ≤1%（实跑）；权限矩阵 8 条全通；0 P0/0 P1 |
| T04 计费闭环 + 前端全量 | B09 B10 B11 B12 B13 | 计费 22 条 + 运营 8 条全通；tsc/lint/build 绿；新增 4 页三态齐备；支付全流程（fake）通 |
| T05 深化与体验兜底 | B14 B15 | UI 24 条 + UX 9 条全通；禁词三面 0 命中 + 自检通过；占位词 0；40 格走查单归档；0 P0/0 P1 |

### 7.4 阶段门放行 checklist（逐条打勾，缺一不放）

| # | 条件 | 判据来源 |
| --- | --- | --- |
| 1 | 24 条 P0 的映射用例 100% 通过 | `pytest -q -m p0` |
| 2 | 114 条 PRD 可执行断言 100% 有对应用例且通过 | 4.7 表 |
| 3 | 全站禁词三面扫描 0 命中（含自检通过） | G-14/15/16 |
| 4 | 全站「暂不支持」「敬请期待」0 命中 | G-17 |
| 5 | 10 个页面三态齐备（含 `/chat` 补 ErrorState） | G-18 |
| 6 | 多租户隔离 0 泄漏（100 次并发采样） | TC-P2-SEC-03 |
| 7 | 凭据不回落（下游计数 0） | TC-P2-CRED-02 |
| 8 | 计量结构误差 0、对账偏差 ≤1% | TC-P2-USG-02/06 |
| 9 | stale 语义 200+status，无错误码外露 | TC-P2-SEG-01/05 |
| 10 | 权限矩阵 0 越权（列表/详情/检索三入口一致） | TC-P2-PERM-01/07 |
| 11 | 既有 421 用例 0 失败 0 删除，总数 ≥599 | G-04/G-06 |
| 12 | 端点双基线 30+38，8 处扩展 0 violation | G-07/08/09 |
| 13 | 0 个 P0、0 个 P1 未闭环 | 缺陷清单 |
| 14 | 人工验收单：**模板已就绪**；密钥/商户资质到位后执行并签署，缺资质项如实标注「**未验证项**」（**不得写「通过」**） | `docs/qa-phase2-manual-signoff.md` |
| 15 | `/readyz` 的 `db_privilege` 自检在超级用户 DSN 下 `status=not_ready`，且 `checks` 数组中该项 `ready=false`（`checks` 保持数组，不得改对象） | G-25 |
| 16 | 所有门禁结论附**本地命令 + 退出码 + 输出片段**；CI 表述为「配置就绪待复跑」 | §2.3 |

---

## 8. 测试数据与构造方法

### 8.1 构造原则

| 原则 | 说明 |
| --- | --- |
| 前缀唯一 | 所有造数使用 `QA_<runid>_` 前缀（库名/文档名/邮箱），便于断言与清理 |
| 哨兵可断言 | 跨租户用例必须用**可检索的哨兵字符串**（如 `SENTINEL_TNT_A_KB`），而不是"看数量变化" |
| 直插与 API 混用 | 只读断言用 API 造数（走真实路径）；需要控制 `tenant_id`/`stage`/`stale_at` 等内部字段时用 SQL 直插（`kb_factory`/`doc_factory`） |
| 事务包裹 | 除并发用例外，全部包在事务里回滚，保证用例幂等可重复 |
| 不依赖执行顺序 | 每个用例自带造数，禁止依赖前一个用例的残留数据 |

### 8.2 场景构造表

| # | 数据场景 | 构造方法 | 关键字段 / 数量 | 清理 | 使用用例 |
| --- | --- | --- | --- | --- | --- |
| 1 | 双租户 + 哨兵库 | `two_tenants`：SQL 插 `tenant(tnt_a/tnt_b)` + 各插 1 个库（名含哨兵） | `tenant_id`, `name="SENTINEL_TNT_A_KB"` | schema 级 DROP | SEC-02/03、PERM-06 |
| 2 | restricted 库 + 三类授权 | API 序列：R1 建库 → KB6 授权 user/department/role | `visibility='restricted'`, 3 条 grant | 回滚 | KB03-01~08、PERM-01 |
| 3 | 5 层 / 6 层部门 | 循环 `POST /departments`（root 预置 level=1） | 5 层节点 + 成员分布 2/3/1 | 回滚 | ID01-01/02、UI02-02 |
| 4 | 失败文档 | MockTransport 返回解析失败 → 等 `failed`；或 SQL 直插 `stage='failed'` + `error_message` | `stage='failed'`, `index_status='failed'`, `run_id` | 回滚 | KB02-03/04、UI01-05 |
| 5 | 5000 文档 | 1 条 SQL：`INSERT ... SELECT FROM generate_series(1,5000)` | 5000 行，`uploaded_at` 递增，`base_id` 固定 | `TRUNCATE` 该 schema | KB02-05 |
| 6 | 处理中文档 | SQL 插 `stage='parsing'`, `progress=40` | + `run_id` 非空 | 回滚 | KB02-06、SEG-02（处理中） |
| 7 | 片段映射 + stale | ①R7 命中回填 → ②`PATCH`/R5 重处理（新 `run_id`）→ ③完成 | `segment_map.status`, `stale_at` | 回滚 | SEG-01~05 |
| 8 | 超保留期 stale | SQL 把 `stale_at` 回拨 91 天 + 手动跑清理任务 | `stale_at = now() - interval '91 days'` | 回滚 | SEG-05 |
| 9 | 绑定凭据（正常/失效） | `seed_credential(status='active'/'revoked')`，明文用金丝雀 | `credential_ref`, `cipher_text`, `key_version` | 回滚 | CRED-01~07 |
| 10 | 额度 80% / 100% | `usage_factory(units=...)` 使 `used/total` 达阈值；或改 `billing_subscription.quota_units_total` | `usage_monthly.tenant.units` | 回滚 | BL03-*、OPS-04、UI03-01 |
| 11 | 待支付订单 + 超时 | `POST /billing/orders` 后不回调；`clock` 推进 31 分钟 | `status='pending_payment'`, `expires_at` | 回滚 | BL04-05/06 |
| 12 | 回调幂等 | 同一 payload 循环 3 次；`external_txn_id` 固定 | `payment_callback_log` | 回滚 | BL04-03 |
| 13 | past_due / expired 订阅 | SQL 改 `status='past_due'` + `current_period_end=now()-8d`；`clock` 注入 | `status`, `current_period_end` | 回滚 | BL02-07 |
| 14 | 审计 17 类动作 | `audit_factory` 批量插（每类 1 条 + 2 类各 3 条用于筛选） | `action`, `actor_id`, `result`, `ip_masked` | 回滚 | ID04-01~05 |
| 15 | 大文件（上传超限） | 生成 50MB+1 字节临时文件（`truncate` 稀疏文件，避免占用磁盘） | `files={'file': (name, fh, 'application/pdf')}` | 删临时文件 | 既有 `test_upload_oversize_returns_20008`（复用） |
| 16 | 前端异常注入 | `next start` + DevTools 协议拦截；或 `NEXT_PUBLIC_MOCK=1` 返回预设错误 JSON（仅开发态） | 500 / offline / delay 30s / 401 | 重启服务 | UX-01~09、UI05-01 |
| 17 | 前端权限态 | 用不同角色 token 直连适配层 + 前端本地存储注入 | 4 角色 × 有/无权限 | 清 localStorage | UI01-04、UI02-03、UI05-06 |
| 18 | 前端大数据量 | mock 返回 5000 条文档 / 50 节点树 | 分页与渲染性能 | — | KB02-05（后端）+ UI02-04（前端） |

### 8.3 造数脚本与夹具落点

| 脚本/夹具 | 作用 |
| --- | --- |
| `tests/factories/data.py`（新增） | `make_kb()` / `make_document()` / `make_usage()` / `make_audit()` 纯数据工厂 |
| `apps/adapter/scripts/seed_qa_data.py`（新增） | 手工联调用的批量造数（5 层组织 + 3 库 + 5000 文档开关） |
| `apps/adapter/scripts/cleanup_qa_schema.py`（新增） | 清理 `qa_*` schema，防测试库膨胀 |
| `apps/adapter/scripts/reconcile_usage.py`（架构已规划） | 对账，被 TC-P2-USG-06 调用 |

---

## 9. 排期建议（按 B01-B15 批次）

### 9.1 批次与用例配套（「插入验证」列 = 该批完成后立即执行，不等里程碑）

| 批次 | 必须配套用例 | 用例数 | **立即插入验证** | 配套命令 |
| --- | --- | --- | --- | --- |
| B01 数据库基础层 | TC-P2-SEC-01/02/03/04/05/06/07 + **G-11 + G-25** | 7 | ✅ **必插**（RLS 是全阶段地基，晚发现一周=全部用例返工；`db_privilege` 自检同批验收） | `pytest tests/unit/test_db_session.py tests/integration/test_migrate.py tests/integration/test_tenant_isolation.py -q --run-pg` + 超级用户 DSN 启动后 `curl -s localhost:8000/readyz` |
| B02 端点基线治理 | TC-P2-TL04-02 + G-07/08/09 | 1 | ✅ 必插（基线一旦错，后续每批都红） | `python deploy/validate_openapi.py` |
| B03 per-kb 凭据 | TC-P2-CRED-01~07 | 7 | ✅ **必插（安全事故级）**：不通过不得进入 B04 | `pytest tests/unit/test_crypto.py tests/unit/test_credential_service.py tests/integration/test_credential_binding.py -q --run-pg` |
| B04 知识库元数据与文档 | TC-P2-KB01-01~06、KB02-01~07 | 13 | ✅ 必插（`chunk_policy`/`stage` 是 8 处扩展的核心，语义错会污染前端） | `pytest tests/integration/test_kb_meta_endpoints.py tests/integration/test_endpoints.py -q --run-pg` |
| B05 授权与回链 | TC-P2-KB03-01~08、KB04-01~04、SEG-01~06、PERM-01/02/07/08 | 22 | ✅ **必插**（restricted + stale 是 G2-1 的两条硬验收） | `pytest tests/integration/test_grant_and_backlink.py tests/integration/test_segment_stale_lifecycle.py tests/integration/test_permission_matrix.py -q --run-pg` |
| B06 工具三款 + TL1 | TC-P2-TL01-01~06、TL02-01~05、TL03-01~05、TL04-01/03/04/05/06 | 21 | ✅ 必插（T1-T7 零改动是本阶段回归硬约束） | `pytest tests/unit/test_wecom_connector.py tests/unit/test_feishu_connector.py tests/unit/test_wechat_mp_connector.py tests/integration/test_tool_executions.py -q --run-pg` |
| B07 计量与 M4 同源 | TC-P2-USG-01~07、BL03-01~04 | 11 | ✅ **必插**（计量同源错 = 账单不可信，且会连带 BL09/BL10 返工） | `pytest tests/integration/test_usage_endpoints.py tests/integration/test_usage_reconcile.py -q --run-pg` + `reconcile_usage.py` |
| B08 组织与权限 | TC-P2-ID01-01~07、ID02-01~07、ID03-01~08、ID04-01~06 | 28 | ✅ 必插（4 角色 × 16 权限点是 G2-3 全量） | `pytest tests/integration/test_org_endpoints.py -q --run-pg` |
| B09 计费订阅与支付 | TC-P2-BL01-01~04、BL02-01~07、BL04-01~07 | 18 | ✅ 必插（回调幂等与超时关闭是商用红线） | `pytest tests/integration/test_billing_and_callback.py -q --run-pg` |
| B10 运营后台接口 | TC-P2-OPS-01~08、PERM-05 | 9 | ✅ 必插（企业 token 全拒 + 最小可见是合规事故防线） | `pytest tests/integration/test_ops_isolation.py tests/integration/test_ops_endpoints.py -q --run-pg` |
| B11 前端基础层 | G-19/G-20 + **G-14/G-15（禁词门禁提前接入，裁决 2）** + TC-P2-ID02-06（部分） | — | ✅ **必插**：`scan:banned` + `--self-test` + `--check-words` 接入 npm 脚本与门禁，此后 B12-B14 的 11 个页面文案从第一批起受检 | `npm run typecheck && npm run lint && npm run scan:banned && node scripts/check-banned-terms.mjs --self-test && node scripts/check-banned-terms.mjs --check-words` |
| B12 前端新页（知识库/组织） | TC-P2-UI01-01~05、UI02-01~04 | 9 | ✅ 必插（新增页的三态与权限置灰必须在 B14 之前定型） | 手工走查 + `node scripts/check-page-states.mjs` |
| B13 前端新页（计费/运营） | TC-P2-UI03-01~03、UI04-01~03 | 6 | ✅ 必插（金额格式化与核销二次确认） | 同上 |
| B14 既有 6 页深化 | TC-P2-UI05-01~08、G-17 | 8 | ✅ 必插（`/chat` 补齐 ErrorState 是本阶段唯一的历史缺口） | `node scripts/check-no-placeholders.mjs` + 走查 |
| B15 体验兜底与禁词门禁 | TC-P2-UX-01~09、WORD-01~04、**G-16/G-18**（G-14/G-15 已在 B11 生效） | 13 | ✅ **必插（一票否决项）**：`--built` 产物扫描与三态静态断言补上；不通过则阶段门直接不放行 | `node scripts/check-banned-terms.mjs --built && node scripts/check-page-states.mjs` |

### 9.2 QA 侧前置准备（避免成为关键路径）

| 时点 | QA 交付 | 说明 |
| --- | --- | --- |
| B01 开始前 2 天 | `pg_dsn`/`uow`/`two_tenants` 夹具与 `qa_<runid>` schema 机制跑通 | 否则 B01 当天无法验证 RLS |
| B03 开始前 2 天 | `mock_dify`（带 `calls` 计数器）+ `seed_credential` + 金丝雀常量 | 凭据不回落用例强依赖 |
| B05 开始前 2 天 | `kb_factory`/`doc_factory`（可控 `stage`/`run_id`）+ stale 三态机脚本 | 状态机用例需要直插能力 |
| B07 开始前 2 天 | `usage_factory` + 对账脚本跑通（含 Valkey 计数注入） | 对账必须实跑一次 |
| B11 开始前 | `check-page-states.mjs` 骨架；**禁词扫描器已于 v1.2 提前交付**（`apps/web/scripts/check-banned-terms.mjs`，自检通过），B11 只需接入 npm 门禁 | 接入前须确认存量 2 处（`knowledge/page.tsx:100/152`）已由工程师修掉（裁决 1），否则门禁一接即红；红了直接报主理人，QA 不改产品代码 |
| B14 开始前 | 40 格手工走查单模板 + 截图归档约定 | 留证需要格式统一 |

### 9.3 关键路径提示

- **B01 与 B03 是唯一两个"不通过就必须停线"的批次**：B01 决定全部数据相关用例是否可信，B03 决定是否有安全缺陷流入后续 4 个域。
- **B05 与 B07 是返工代价最高的两批**：B05 的 `PolicyService` 是三处判定共用，改一次影响 KB3/R7/M1/M2；B07 的 `UsageService` 被 M4/BL5/OPS6 三处共用。
- **B15 不可压缩**：禁词扫描器本身需要"自检负样本"才能证明有效，若在最后 2 天才写，一旦扫描器有 bug 就无时间修文案。

---

## 10. 遗留风险与建议（5 条）

| # | 风险 | 影响 | 为什么本方案解决不了 | 建议与缓解 |
| --- | --- | --- | --- | --- |
| **R-1** | **错误码口径已裁定，残余为"文档回写是否完成"**：交付总监裁决 1.1 采纳 `11007` 并把文案改为「该知识库暂时无法访问，请联系管理员」，产品经理已回写 PRD；但回写动作若未在 B03 前完成，工程师仍可能读旧版 PRD | 工程师照旧稿写 `11005` 或旧文案 → CRED-02/04/05 全红 | 文档一致性无法由测试代码保证 | ①本方案已在 §0 锁定码值与**逐字文案**；②B03 准入时要求工程师出示 PRD 附录 A.3 已更新的页面链接（§7.2 交付项 2 的延伸）；③`test_error_messages_business_language.py` 会把该文案纳入注册表断言，文案被改即红 |
| **R-2** | **真实第三方与真实支付不可自动化**：企微/飞书/公众号需企业租户与管理员授权，微信支付需商户号与证书 | 可能出现"协议字段写对了、真实接口仍不通"（如公众号 `media_id` 类型、飞书时区字段格式） | 无测试账号与资质，非技术问题 | ①录制式 `httpx.MockTransport` + 官方文档字段对齐，覆盖协议层；②按裁决 5 **本阶段只交付模板** `docs/qa-phase2-manual-signoff.md`（字段：工具/操作/期望/实际/凭证截图/执行人/日期/结论，**不预填结论**）；③资质到位后按模板执行，缺资质时在验收报告标注「未验证项」，**不得写「通过」** |
| **R-3** | **前端无测试框架（裁决 2 明确不放行），运行期文案与性能无法自动断言**：禁词扫描只能覆盖源码字面量与构建产物字符串常量，动态拼接（如 `第 ${page} 页` 中 `page` 来自错误码映射）可能漏检；首屏 ≤2s / 首字 ≤1s 无稳定环境 | 可能出现"扫描 0 命中但页面仍显示技术词"；性能不达标无法自动拦住 | 引入 Vitest/Playwright 会新增依赖，且**本仓库 CI 额度已用尽、无法自动验证**，风险大于收益 | ①40 格走查按 §3.6.4 的 **6 字段 + 截图** 留证（`实际` 列写「正常」或缺失截图即判未执行）；②性能用 Lighthouse 3 次取中位数记录归档，仅作阶段门附件（不作硬断言）；③第三阶段把 40 格直接翻译成 40 条 e2e |
| **R-4** | **RLS 双保险的最后一公里在运维**：自检已升级为硬要求（裁决 3），但探针配置仍由运维决定 | 若编排层只探 `/healthz`（存活）而不探 `/readyz`（就绪），`db_privilege` 失败不会阻断流量 | QA 能证明"自检逻辑正确"，无法证明"编排层真的用了它" | ①G-25 已列入不可豁免门禁；②交付物中 `deploy/docker-compose*.yml` 的 healthcheck 必须指向 `/readyz`（QA 用 `grep -c "readyz" deploy/docker-compose*.yml` ≥1 断言）；③交付前由运维在真实环境跑一次 `select current_user, rolbypassrls ...` 并截图归档 |
| **R-5** | **对账与"真实流量"耦合**：对账脚本比对 Valkey 诊断计数与 `usage_monthly`；若 Valkey 不可用、或测试环境流量太少（<100 事件），偏差率没有统计意义 | 可能出现"对账通过"但实际口径错；或对账本身跑不起来被跳过 | 无法在单测环境模拟生产流量分布 | ①`reconcile_usage.py` 增加最小样本量门槛（事件数 <100 时输出 `INSUFFICIENT_SAMPLE` 并**不算通过**，记为「未验证」）；②阶段末用 `seed_qa_data.py` 造 ≥1000 条真实路径事件后实跑一次并归档输出；③把对账结果写入 `docs/qa-phase2-acceptance-report.md` 的必填章节 |

### 10.1 关于「24 条 P0 覆盖缺口」的说明

**无缺口**。24 条 P0 全部映射到用例（见 4.7），114 条可执行断言全部有对应用例。P2-KB-05 的码值与文案冲突已由交付总监裁决 1.1 闭环（`11007` + 新文案），不再构成验收争议；R-1 的残余仅为"PRD 回写是否已在 B03 前完成"的流程确认。

### 10.2 本方案的三条自检声明

1. **不写"需验证"**：第 3 章 7 类风险全部给出可执行证明方法（含代码片段、SQL、正则、数量级），第 4 章 178 条用例的「期望」列全部为可断言项。
2. **可追溯**：每条用例标注 PRD 需求号；每条 P0 标注映射用例；每个落点为具体文件路径（手工用例为走查单锚点）。
3. **不重复造轮子**：既有 421 条用例全部标注 `[复用]` 并说明复用方式；8 处扩展的兼容性断言给出"机械校验 G4 + 人工断言"双层，未重写阶段一用例。

---

## 11. 裁决落实记录

> 本节逐条记录交付总监 5 条裁决在方案中的落点，作为评审与验收的对照表。**凡本章未登记的判据，一律以 v1.0 原文为准。**

### 11.1 裁决 → 落点对照

| 裁决 | 内容摘要 | 方案落点（章节 / 用例 / 门禁） | 落实方式 | 状态 |
| --- | --- | --- | --- | --- |
| **1.1** | 凭据失效采纳 `11007`，**文案改为「该知识库暂时无法访问，请联系管理员」** | §0 判据表第 1 行；§3.2.3 证明 A 判据；§3.2.4 `TC-P2-CRED-02`；§4.7 第 5 行；§10 R-1；`tests/unit/test_error_messages_business_language.py` | 用例断言由「只断码」升级为「码 + **逐字文案**」双断言；`11005` 在全文仅作为冲突源出现，不作断言 | ✅ 已落实 |
| **1.2** | `15005` → HTTP 200 + `code=0` | §0 判据表第 2 行；`TC-P2-BL02-03` | 断言 `status_code==200 and code==0`，并断言 `data.pending_change` 存在 | ✅ 已落实（v1.0 已符合，本次仅补登来源） |
| **1.3** | `16001` → 复用 `20003` | §0 判据表第 3 行；`TC-P2-OPS-01` | 8 端点 × 3 企业 token 全部断言 `code==20003`、且 `status_code != 404` | ✅ 已落实 |
| **1.4** | `11006` 同码多文案 | §0 判据表第 4 行；`TC-P2-KB02-06` | 断言码值 + 文案「文档正在处理，请稍后再删除」逐字相等 | ✅ 已落实 |
| **1.5** | **member 删库改三分支**：自己创建的→成功；他人的/公共的→`20003`；owner/org_admin 任意→成功 | §0「实现口径」表（原"不能删"整行改写）；§3.5.3 矩阵 member B/C 两行 + 新增「第四项硬判据」四分支；§3.5.4 `TC-P2-PERM-02` | 用例由 1 条断言扩为 **4 条分支断言**，新增落点 `test_permission_matrix.py::test_kb_delete_three_branches`；并在矩阵下记录裁决理由（防后续被"权限严谨"反向改回） | ✅ 已落实（**判据实质变更**，见 11.2） |
| **2** | 前端允许「静态断言 + 40 格手工走查」，**不放行 Playwright**；走查须可留证 | §3.6.1 理由补充；§3.6.4 执行方式（6 字段表 + 三条留证纪律 + 截图目录 `docs/qa-phase2-evidence/`）；§4.6 引言；§5.4（"不放行"四条理由 + 替代证明强度要求）；§10 R-3 | 走查单字段从"截图 + 观察结论"升级为 **页面/场景/操作/期望/实际/截图文件名**；`实际` 列写「正常」或缺失截图 → 判**未执行** | ✅ 已落实 |
| **3** | `rolbypassrls` 启动自检升级为硬要求 | **§2.1 新增 G-25**（门禁 24→25）；§2.2 执行顺序插入 `/readyz` 自检；§2.4 列入不可豁免；§3.1.3 证明 E（双层校验 + **数组形态**契约 + 断言代码）；§3.1.4 `TC-P2-SEC-07` 改写；§7.4 checklist 新增第 15 项；§10 R-4；§9.1 B01 行 | `TC-P2-SEC-07` 判据改为四断言：超级用户 DSN → `status=not_ready` 且数组内 `db_privilege.ready=false`、`detail==数据库连接权限配置不正确`；`officeai` DSN → `ready` 且 `ready=true`；`checks` 为数组且 `db` 项仍在；直查 `rolbypassrls==false` | ✅ 已落实（**新增门禁 + 用例判据改写 + 结构纠正**，见 §12.1） |
| **4** | 门禁以**本地复跑**为准；CI 表述改"配置就绪待复跑"；pg 门控不变 | **§2.3 新增「门禁执行环境」小节**；§1.1 工程量行；§5.3 连接/强制复跑/数据隔离三行；§6.3 表（新增「CI 状态」行、列名改「断言写法（本地复跑）」）；§6.5 节奏表；§7.4 新增第 16 项 | 全文删除「CI 绿 / CI 必红 / CI 强制」表述；统一为「本地门禁 G-01~G-25 全绿（附命令+退出码+输出片段），CI 配置就绪待复跑」；`--run-pg` 下 `0 skip / 0 fail` 要求不变 | ✅ 已落实 |
| **5** | 人工验收单**只出模板**，不预填结论；缺资质标「未验证项」 | §1.2 不测项两条改写 + 新增第三条（「未验证项」口径）；§7.4 第 14 项；§10 R-2；**新增模板文件 `docs/qa-phase2-manual-signoff.md`** | 模板含 7 字段（工具/操作/期望/实际/凭证截图/执行人/日期）+ 结论列，全部留空；模板顶部写明"不得预填结论""缺资质记未验证" | ✅ 已落实（模板已交付） |

### 11.2 判据实质变更清单（需工程师与产品同步知悉）

| # | 变更前（v1.0） | 变更后（v1.1） | 受影响用例 | 影响面 |
| --- | --- | --- | --- | --- |
| 1 | member 一律不可删库（`20003`） | member 可删**自己创建**的库（`code=0`）；删他人/公共库仍 `20003` | `TC-P2-PERM-02`（断言由 1 条 → 4 条） | **B05 的 `PolicyService.can_delete_base()` 必须比较 `owner_user_id`**，不能只按角色白名单；同时 KB2 的审计与 `deleted_document_count` 逻辑不变 |
| 2 | `11007` 文案「知识库服务暂时不可用，请稍后重试」 | 「该知识库暂时无法访问，请联系管理员」 | `TC-P2-CRED-02/04/05` | 前端 `business-errors.ts` 的 `11007` 兜底文案必须同步；B03 交付时一并改 |
| 3 | `/readyz` 仅检查数据库连通性 | 新增 `db_privilege` 检查项与 `checks` 结构 | `TC-P2-SEC-07`、`G-25` | **`/readyz` 响应契约变更**，需进 OpenAPI 并同步 `deploy/docker-compose*.yml` 的 healthcheck；B01 验收项 |
| 4 | 手工走查"截图 + 观察结论" | 6 字段 + 截图文件命名规范 + 三条留证纪律 | `TC-P2-UI01-01 ~ UI05-08`、`TC-P2-UX-01 ~ 09` | 走查单模板须在 B12 前定稿（§9.2 已列前置） |

### 11.3 未改动项（明确说明，避免"以为改了"）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 用例总数 | **178 条不变** | 裁决 1.5 与裁决 3 均为**改写既有用例的判据**，未新增编号；`TC-P2-PERM-02` 内部由 1 条断言扩为 4 条分支断言（用例数仍计 1） |
| 用例编号与域分布 | 不变 | 知识库 27 / 工具 22 / 组织权限 28 / 计费 22 / 运营 8 / 前端 32 / 跨域 39 |
| 24 条 P0 映射 | 不变（仍 100%） | 4.7 表仅更新第 5 行的说明文字 |
| 分层落点数字 | 单元 31 / 集成 100 / 前端 47 | 不变 |
| 7 类风险的证明方法 | 不变 | 仅 §3.1.3 证明 E 增加启动自检层、§3.6.4 强化留证要求 |
| 门禁总数 | **24 → 25** | 新增 G-25；其余 24 道阈值未变 |
| 章节结构 | **新增第 12 章** | §1-§11 章节号与内容不变（§11 仅补充第 3 行措辞）；§12 为前置介入记录 |

---

## 12. 前置介入记录

> 记录两件事：① 一处**硬错误的结构纠正**（§12.1）；② 禁词扫描器提前落地与存量违规实测结果（§12.2-§12.5）。这两项都不改变用例编号与总数（178）。

### 12.1 `/readyz` 结构纠正（v1.1 硬错误）

| 项 | 内容 |
| --- | --- |
| 错误表述（v1.1） | 「`/readyz` 响应结构调整为 `status + checks{db, db_privilege}`」——把 `checks` 写成了**对象/字典** |
| 代码事实 | `apps/adapter/app/schemas/common.py:168-177`：`ReadyCheck{name, ready, detail}`；`ReadyData{status, checks: list[ReadyCheck]}` → **`checks` 是数组** |
| 若按错误写法实现的后果 | 破坏阶段一已冻结的 `/readyz` 契约，`tests/integration/test_health.py` 既有断言会红；前端与编排层探针解析同时受影响 |
| 正确形态 | **只在数组里追加一个 `ReadyCheck`**：`{"name":"db_privilege","ready":false,"detail":"数据库连接权限配置不正确"}`；`db` 项与整体结构不动 |
| 断言写法 | `any(c["name"]=="db_privilege" and c["ready"] is False and c["detail"]=="数据库连接权限配置不正确" for c in data["checks"])`；并断言 `isinstance(data["checks"], list)` |
| 方案落点 | §2.1 G-25 阈值列；§3.1.3 证明 E（含 JSON 形态与断言代码块）；§3.1.4 `TC-P2-SEC-07`（新增第 ④ 条「`checks` 必须是数组且 `db` 项仍在」）；§7.4 第 15 项；§11.1 裁决 3 行 |
| 是否改变用例 | 否，仅改写 `TC-P2-SEC-07` 的判据（用例数不变） |

### 12.2 禁词扫描器（提前落地）

| 项 | 内容 |
| --- | --- |
| 文件 | `apps/web/scripts/check-banned-terms.mjs`（Node 22 内置能力，**零新增依赖**） |
| 运行方式（在 `apps/web` 下） | `node scripts/check-banned-terms.mjs`（扫 `src` 全部）/ 传文件路径（只扫指定文件）/ `--json` / `--stats`（证明扫到了多少片段）/ `--dump <file>`（逐片段列出，用于核查提取是否漏） |
| 词表来源 | PRD 第 7 章 18 行表格 → 去重后 **55 条词面**（含中文子串词 24 条、ASCII 词边界词 31 条），模型品牌名与 4-5 位错误码单独成组（分别对应 PRD 第 8、17 行） |
| 防漂移 | `--update-words` 从 `docs/prd-phase2-increment.md` 第 7 章重新生成常量（标记块 `// <BANNED_TERMS:BEGIN>` … `END`）；`--check-words` 校验脚本常量与 PRD 是否一致，**漂移退出码 3** |
| 自检 | `--self-test`：12 条负样本必须全部命中、8 条业务语言正样本必须 0 误报、5 个标识符（`segment_count`/`chunk_policy`/`index_status`/`segment_id`/`token_total`）必须 0 误报、**5 条模板样本**（含 `${}` 内部字面量、`${err.code}` 变量引用不得误判）必须全部符合预期。**退出码 1 = 自检通过**（与 G-15 口径一致：能抓到东西才叫可用） |
| 输出格式 | `文件:行号:命中词:上下文片段`，末尾按文件汇总命中数 |
| 代码规范 | 已过 `npx eslint scripts/check-banned-terms.mjs` → **0 error / 0 warning**（不破坏 G-20） |

**排除口径（已写进脚本注释，避免后续争议）**

| 豁免类别 | 具体规则 | 对应代码位置 |
| --- | --- | --- |
| 注释 | `//` 与 `/* */` 全部替换为空格（保留行号） | `blankComments()` |
| 模块路径 | `import/export … from '…'` 整行视为空 | `blankImports()` |
| 不可见属性值 | `className/class/style/id/href/src/htmlFor/name/role/type/key/on*/data-*` 等 | `INVISIBLE_ATTRS` |
| **可见属性不豁免** | `placeholder/title/alt/aria-label` 等属于用户可见文案，**必须扫**（PRD 第 7 章含"提示/按钮"） | `getJsxAttr()` 白名单反向 |
| 键/路径/URL | 含 `_ - / . : @` 的 ASCII 字面量、以 `/` 开头的模板拼接、`http(s)://` | `looksLikeKeyOrPath()` |
| 类型联合字面量 | 行内形如 `export type X = 'a' \| 'token' \| 'none'` 的枚举值属于代码 | `isTypeContext()` |
| 标识符边界 | ASCII 词用 `(?<![A-Za-z0-9_])term(?![A-Za-z0-9_])`，`segment` 不会命中 `segment_count` | `wordRegex()` |
| 错误码上下文 | `\b[1-3]\d{4}\b` 仅在有中文/空格的文案里启用，且前后 6 字符出现「年/月/日/号/元/万/字/的/页/条/席位/个/次」即豁免（避免把金额、页码、年份误判为错误码） | `CODE_EXEMPT_CTX` |
| 例外保留 | 字面量**本身就是**禁词时（如 `'access_token'`）即使形如键名也判命中 | `isExactBannedTerm()` |

### 12.3 当前 6 个页面扫描结果（存量违规）

**覆盖度自证**（先证明"确实扫到了东西"，避免"0 命中=没扫到"的假象）：

| 范围 | 文件数 | 待检片段 | 字符串 | 模板 | JSX 文本 |
| --- | --- | --- | --- | --- | --- |
| 当前 6 个页面（chat / knowledge / plugins / profile / team / workflows） | 6 | **194** | 125 | 4 | 65 |
| `src` 全量（含组件、hooks、lib） | 30 | **725** | 559 | 32 | 134 |

> 数字较 v1.2 首版（192 / 716）各 +2 / +9，来自本轮修复：模板 `${...}` **内部**的字符串字面量现在也被单独提取（此前会随表达式一起被剔除）。

**命中清单**（6 页与全量结果一致：2 处，均在知识库页）

| # | 位置 | 命中词 | 上下文 | 定性 | 建议改法 |
| --- | --- | --- | --- | --- | --- |
| 1 | `src/app/(main)/knowledge/page.tsx:100` | 索引（PRD #3） | `toast.success('文档已上传，正在建立索引')` | P1（体验红线，阶段门一票否决项） | `'文档已上传，正在处理内容'` |
| 2 | `src/app/(main)/knowledge/page.tsx:152` | 索引（PRD #3） | `正在建立索引，请稍候（{upload.progress}%）` | P1 | `正在处理内容，请稍候（{upload.progress}%）` |

**未命中的说明（这些是"看起来像但要放行"的样例，用于证明排除口径有效）**

| 位置 | 原文 | 为什么不判命中 |
| --- | --- | --- |
| `knowledge/page.tsx:24` | `/** 索引状态展示映射（业务语言） */` | 注释，不在用户可见范围（PRD 允许内部使用技术词） |
| `layout.tsx:24` | `href: '/profile'` | 前端路由，不是文案 |
| `lib/types.ts:249` | `export type ToolAuthType = 'oauth2' \| 'token' \| 'none'` | 类型联合字面量，是代码枚举值 |
| `lib/api-client.ts:98` | `'X-Tenant-Id'` | HTTP 头名 |
| `lib/api-client.ts:511` | `` `/knowledge/bases/${baseId}/documents/${docId}/reindex` `` | API 路径（模板拼接） |

**结论**：存量违规 **2 处 / 1 个文件**，其余 24 个源文件（含全部组件）0 命中。数量少说明阶段一的文案基本符合红线，问题集中在知识库上传进度这一条链路的措辞上（同一概念写了两遍，属同源缺陷）。

> **处置（裁决 1，不等 B14）**：这 2 处已由主理人转工程师，在 B01-B03 收尾时顺手改为「正在处理内容」「正在准备内容，请稍候」。**QA 不改产品代码**；改完后我复跑 `scan:banned` 应 0 命中，并在 B11 接入门禁前再确认一次。

### 12.4 与门禁的接入关系（裁决 2：提前到 B11）

| 项 | 现状 | 计划 |
| --- | --- | --- |
| 脚本可用性 | ✅ 已可用（自检通过、eslint 清洁、`npm run lint` 全项目通过） | — |
| 是否已接入 `package.json` / 门禁 | ❌ **尚未接入** | **B11 接入**（原计划 B15，按裁决 2 提前） |
| 提前理由 | B12-B14 会新增 5 个页面 + 深化 6 个页面，是最容易引入违规的窗口；晚接等于让 11 个页面文案裸奔到最后才检查 | — |
| 接入步骤（B11） | ①`package.json` 加 `"scan:banned": "node scripts/check-banned-terms.mjs"`；②G-14 跑 `scan:banned`（须 0 命中）；③G-15 跑 `--self-test`（须退出码 1）与 `--check-words`（须退出码 0）；④`--built` 仍随 B15（G-16） | — |
| 接入前置 | **必须确认存量 2 处已修**（裁决 1）。若接入时仍红：直接报主理人，**QA 不改产品代码**、也不放宽扫描规则 | — |
| 仓库根调用方式 | 保持脚本在 `apps/web/scripts/`（校准 1：`package.json` 与 `eslint .` 都以 `apps/web` 为根）；后续 CI 从仓库根调用即可：`npm --prefix apps/web run scan:banned` | — |

### 12.5 未做的前置项

| 项 | 裁决 | 我的准备状态 |
| --- | --- | --- |
| PG 测试夹具（`pg_dsn` / `uow` / `two_tenants` / schema 隔离） | **暂不搭建**（依赖 B01 的 `app/db/session.py` 与最终表结构，提前做会与工程师同文件冲突） | 方案 §5.5 已列全字段与行为；等 B01 提交后立即接手，需要工程师提供「连接串、迁移命令、测试库初始化方式」三项 |
| 禁词扫描器 `--built`（构建产物 + DOM 文本扫描） | 随 B15 做（依赖 `next build` 产物路径稳定） | 当前已实现源码三面中的「字符串 + 模板 + JSX 文本」；`--built` 复用同一词表与排除口径 |

### 12.6 协作口径更正

| 项 | 更正后 |
| --- | --- |
| 跨成员信息流 | **一律经主理人中转**。此前直接给工程师发过 `PolicyService.can_delete_base()` 与 `/readyz` 的提醒，属流程违规；后续凡需告知工程师/产品/架构的内容，只写在给主理人的汇报里，由主理人转交 |
| 本条影响 | 已发出的信息不追回（内容本身与主理人转交的一致），但流程按要求改正 |

### 12.7 第二轮裁决与校准记录（2026-10-21）

| 编号 | 内容 | 我的落实 |
| --- | --- | --- |
| **口径校准** | **主理人 2026-10-21 校准：用例总数以 178 为准**（此前"178→184"系主理人误读我的表述，不增补 6 条） | 本文件所有版本（v1.0/v1.1/v1.2）均为 **178 条**，编号体系不变；后续引用一律以 178 为准，不再出现 184 |
| **裁决 1** | 存量 2 处违规**现在就修**，不等 B14：`knowledge/page.tsx:100` → 「文档已上传，正在处理内容」；`:152` → 「正在准备内容，请稍候」 | 主理人已转工程师在 B01-B03 收尾处理；**QA 不改产品代码**。我在 §12.3 登记了处置说明与验收动作（B11 接门禁前复跑 `scan:banned` 须 0 命中） |
| **裁决 2** | 禁词门禁**提前到 B11**（G-14/G-15 生效），`--built`（G-16）仍随 B15 | §2.1 G-14/G-15 生效批次改为 **B11** 并补 `--check-words`；§9.1 B11 行改为「必插」并给出完整命令；§9.1 B15 行收窄为 G-16/G-18；§9.2 B11 前置补「须先确认存量已修，红了报主理人」；§12.4 全部改写 |
| **校准 1** | 脚本路径保持 `apps/web/scripts/`，不移到仓库根 | §12.4 补「仓库根调用方式」：`npm --prefix apps/web run scan:banned`；§12.2 路径说明保留原判断 |
| **校准 2** | 排除口径收紧：确认 **Toast / 空态 / 错误提示里的动态拼接（模板字符串）必须被扫到**；抽查 5 个文件确认提取逻辑不漏 | 见下方"模板提取审计结论"：**发现并修复 1 处真实漏检**（`${}` 内部字面量），修复后 5 文件模板计数与源码真值**逐一对齐** |

**模板提取审计结论（对应校准 2）**

| 抽查文件 | 源码真值（去注释后反引号数 / 2） | 修复前提取数 | 修复后提取数 | 结论 |
| --- | --- | --- | --- | --- |
| `lib/api-client.ts` | 21 | 18（看似漏 3，实为 dump 过滤了"静态文本为空"的模板） | **21** | ✅ 不漏 |
| `hooks/use-conversation-store.ts` | 2 | 2 | **2** | ✅ 不漏 |
| `components/chat/chat-window.tsx` | 2 | 2 | **2** | ✅ 不漏 |
| `app/(main)/workflows/page.tsx` | 2 | 2 | **2** | ✅ 不漏 |
| `components/knowledge/document-search.tsx` | 1 | 1 | **1** | ✅ 不漏 |

**审计中修复的 1 处真实漏检**：模板 `` `${ok ? '正在建立索引' : ''}` `` —— 原逻辑把整个 `${...}` 当代码剔除，表达式**内部**的字符串字面量会一起丢失。已改为：静态部分照旧扫描 + 对每个 `${...}` 递归提取其中的单/双引号字面量（`extractQuotedStrings()`），并在 `--self-test` 增加 5 条模板样本（含"`${err.code}` 变量引用不得误判为错误码"的反向样本）。同时修掉 `--dump` 会隐藏"纯表达式模板"的问题（否则审计时会误判为漏扫）。

**另一个已核清的点（Toast / ErrorState）**：当前代码里 `toast.success/error` 的文案全部是**字面量**（已扫）；`<ErrorState message={errorMessage} />` 的文案是**变量**，静态扫不到，但其来源是 `toBusinessMessage(error)` → `lib/business-errors.ts`（在 `src` 扫描范围内）与后端返回的 `message`（由 §3.7 面 3 覆盖）。若将来写成 `<ErrorState message="…" />` 字面量，本扫描器会捕获（`message` 不在 `INVISIBLE_ATTRS` 中）。

---

*方案结束（v1.2）。判据变更必须同步更新 §0、第 11/12 章与本文件版本号；已落实裁决的判据不得在后续评审中单方回退。*
