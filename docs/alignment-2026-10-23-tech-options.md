# Moy（麦子）总纲对齐 · 技术决策可行性评估

| 项 | 内容 |
| --- | --- |
| 报告编号 | OA-ALIGN-TECH-001 |
| 日期 | 2026-10-23 |
| 作者 | 高见远（架构师） |
| 输入 | `docs/moy-project-charter-v1.0.md`、`docs/audit-2026-10-23-arch-code-review.md`（AR-001~023）、`docs/audit-2026-10-23-moy-charter-consolidated.md`、`docs/decisions/0005-charter-first-alignment.md`（ADR-0005） |
| 范围 | **仅技术侧**：T1~T6 六项技术决策的可行性、成本、推荐方案 |
| 约束 | 本轮只评估与设计；**本人未修改任何代码**，仅新增本文件 |
| 基线 | `apps/adapter` 实测 **1802 passed / 177 skipped（需 `--run-pg`）/ 0 failed**，见 §1.3 |

> ⚠️ **评估期间工作区发生了并行落地**（`security.py` 2026-10-23 18:18、`test_tenant_spoofing.py` 18:19、`login/page.tsx` 18:21），因此本报告分两部分读：
> - **§2.1 / §2.2 = 已落地实现的评审 + 剩余缺口**（不是"从零建议"）；
> - **§2.3~§2.6 = 未落地项的方案评估**。
> 详见 §1.4 的时间线与取证方式。

---

## 0. 结论速览

| 决策 | 我的独立结论 | 与 ADR-0005 的关系 | 当前状态 |
| --- | --- | --- | --- |
| **T1** 租户伪造 | 修：请求头只能在**成员关系内**生效，越界 20003；成员口径 = `department_member ∪ member_role` | **与 D-25 完全一致**（独立得出） | 🟡 已落地，**剩 3 个缺口**（§2.1.5） |
| **T2** 登录真实化 | 本轮**诚实降级 + OIDC 脚手架**，完整授权码流程排下一批 | **与 D-26 一致** | 🟡 已落地，**剩 1 个阻断级缺口**（回跳路由不存在，§2.2.5） |
| **T3** 连接器 | 本轮**诚实降级**（实现自述模式）+ 首个真实化选**邮箱 SMTP**（唯一可离线端到端验证） | **与 D-19 一致**（D-19 指定邮箱+GitHub） | ⚪ 未落地 |
| **T4** 核心指标 | 4 项现有表可算（**零迁移**）；采纳率必须新建 adoption 事件表 | **与 D-27 一致** | ⚪ 未落地（已有《指标定义文档》） |
| **T5** Open WebUI | 事实="曾评估未采用"；**只改总纲 §7.2**，不补实现 | **与 D-13 一致**（D-13 已落地到 plan/PRD/架构） | 🟡 文档 3 处已改，**总纲 §7.2 仍待改** |
| **T6** 统一任务入口 | 用现有声明资产做规则推荐 MVP，**不引入模型** | **与 D-17 一致**（D-17 分两步） | ⚪ 未落地 |

**剩余待办**：5 项（其中 1 项为阻断级：`/auth/callback` 缺失会导致"跳转后 404"）。详见 §3。

---

## 1. 评估方法与代码事实核对

### 1.1 方法

1. **不采信文档自述**：每条"已实现"回到代码/编排/测试三处交叉核验。
2. **证据到「文件:行号 + 函数名」**；无法用本仓证据判定的标「待验证」。
3. **成本口径**：S ≤ 0.5 人日；M = 1~2 人日；L ≥ 3 人日（含联调与回归）。
4. **兼容优先**：任何改动先问"会不会打破既有 1802 条测试与三种部署形态"。
5. **区分 HEAD 与工作区**：本仓存在大量未提交改动，两者结论不同时必须写明（见 §1.4）。

### 1.2 部署形态事实（决定改动爆炸半径）

| 编排 | `OA_DEPLOY_MODE` | `OA_AUTH_MODE` | 证据 |
| --- | --- | --- | --- |
| `deploy/docker-compose.yml` | `private` | `oidc` | `:471`、`:470` |
| `deploy/docker-compose.lite.yml` | `private`（写死） | `oidc`（写死） | `:420-421` |
| `deploy/docker-compose.saas.yml` | `saas` | `oidc` | `:373`、`:372` |
| 单测/集成默认 `Settings` | `private` | `stub` | `tests/conftest.py:255-262` |

**关键推论**：私有化两条栈在 `security.py` 的 `resolve_tenant_id` 中**提前返回 default、根本不读 `X-Tenant-Id`**，所以 T1 的影响面只有 SaaS 形态；已交付的轻量版/私有化版不受影响。

### 1.3 测试基线（实测）

```
# 修复落地后，全量非 PG 口径（本人 2026-10-23 18:2x~18:3x 实跑）
cd apps/adapter
OA_BATCH=B21 ./.venv/Scripts/python.exe -m pytest tests/unit tests/security -q  → 1310 passed, 1 skipped
OA_BATCH=B21 ./.venv/Scripts/python.exe -m pytest tests/integration  -q         →  492 passed, 176 skipped
合计：1802 passed / 177 skipped / 0 failed
```

- 修复前基线为 1792 passed；差值 **+10** 正是新增的 `tests/security/test_tenant_spoofing.py`（10 条），**无回归**。
- ⚠️ 必须带 `OA_BATCH=B21`（CI 口径 `.github/workflows/ci-adapter.yml:75`），否则 `test_plugin_contract_skeleton.py::test_d1_deferred_registry_matches_markers` 会因 `tests/expected_xfail.py:243-256`「缺失即红」而失败——**看起来像改坏了，其实是环境变量口径**。
- 前端口径（实测）：`npx tsc --noEmit` 通过；`npm run scan:banned` / `scan:placeholders` 各「扫描 100 个文件，0 命中」。

### 1.4 并行落地观测（评估期间发生的事）

| 时间 | 文件 | 变化 | 取证方式 |
| --- | --- | --- | --- |
| 18:18:51 | `apps/adapter/app/core/security.py` | +84 行：新增 `_has_cross_tenant_privilege` / `assert_tenant_membership`，`get_request_context` 内调用 | `git diff`、`grep -n`（`assert_tenant_membership` 现位于 `:461`，调用点 `:551`） |
| 18:19:39 | `apps/adapter/tests/security/test_tenant_spoofing.py` | 新增 10 条 AR-017 用例 | 文件读取 |
| 18:21:46 | `apps/web/src/app/(auth)/login/page.tsx` | +174/-? 行：生产构建只保留「统一登录」，开发构建才显示直进/贴凭证 | `git diff` |
| 同期 | `docs/{plan-v1.0,prd-v1.0,architecture-v1.0}.md`、`README.md`、`apps/web/package.json` | Open WebUI 表述修正（引 D-13）、标题改 Moy | `git diff` |

**说明**：本人在 18:1x 之前读取 `security.py` 时该文件仍是 `HEAD` 版本（无成员校验）；18:2x 复读时修复已存在。因此本报告对 T1/T2 的定位从"建议方案"调整为"评审已落地实现 + 列出剩余缺口"。**HEAD 基线的漏洞事实仍然成立**（`git show HEAD:apps/adapter/app/core/security.py` 可见无校验版本），可用于审计留痕。

---

## 2. T1~T6 逐项评估

### 2.1 T1 · AR-017 租户伪造修复

#### 2.1.1 现状证据（HEAD 基线 = 漏洞成立）

| 事实 | 证据 |
| --- | --- |
| `X-Tenant-Id` 无条件优先，直接返回 | HEAD `core/security.py:217-219`（`resolve_tenant_id`） |
| 解析结果被 `replace()` 直接覆写进 `auth.tenant_id` | HEAD `core/security.py:466-470`（`get_request_context`） |
| 覆写后的租户即下游 RLS 会话变量 | `db/session.py:41-42`（`SET_TENANT_SQL`）、`:308-354`（`tenant_scope`） |
| 94 个端点共用同一依赖 → 单点修复覆盖面 100% | `grep -c "Depends(get_request_context)"` = 94 |
| 既有"跨租户"安全用例只验证 RLS 层 | `tests/security/test_attack_cross_tenant.py`（全文直连 PG 切 `app.tenant_id`，不经过 HTTP） |

#### 2.1.2 关键前置事实：成员关系模型已经存在（无需迁移）

| 表 | 语义 | 证据 |
| --- | --- | --- |
| `member_role` | `PRIMARY KEY (tenant_id, org_id, user_id)` | `deploy/init-schema-phase2.sql:547-556` |
| `department_member` | `(tenant_id, department_id, user_id)` | `init-schema-phase2.sql:490-510` |

仓储层现成能力：`RoleStore.role_ids_of_user`（`role_repo.py:285-291` 契约、`:845-864` PG）、`OrgStore.links_of_user`（`org_repo.py:176-178` 契约、`:849-870` PG），**双后端齐全**（内存实现不依赖 PG）。

⚠️ 唯一坑：组织成员的事实来源是 `department_member ∪ member_role`（`services/identity_service.py:228` 明写）——存在"只在部门、无角色"的成员，只查 `member_role` 会误杀。

#### 2.1.3 候选方案（决策留痕）

| 方案 | 逻辑 | 利 | 弊 | 裁定 |
| --- | --- | --- | --- | --- |
| A 保留请求头优先 + 加成员校验 | 头有值即查成员 | 语义改动最小；天然兼容多租户 | 令牌声明被头降级；审计里"以为在 A、实际在 B" | 采纳（现行实现即为 A 的加固形态） |
| B 令牌声明为基线、头仅在成员关系内选择 | 头≠令牌 → 必须验成员 | 语义最正；常规请求零开销 | 需多租户切换 UX（当前前端无此需求） | 与 A **在拒绝判据上等价**，差异仅在"谁优先"；本仓前端只有全局设置 `X-Tenant-Id`（`api-client.ts:184/231-232/393-395`），故 A 可接受 |
| C 完全忽略请求头 | 一行改完 | 最安全 | SaaS 多租户不可用；破坏 94 端点契约 | 不采纳 |

#### 2.1.4 推荐（已与 D-25 一致）

拒绝判据三选一必须成立，否则 `20003`：
1. 生效租户 == 令牌租户（同租户，零查询）；
2. 平台运维令牌（租户为 `__platform__` 或角色含 `*`）；
3. 用户在目标租户有 `department_member` 或 `member_role`。

错误码用 **`20003 FORBIDDEN`**（`core/errors.py:35`），不新增码——新增要同步 `docs/api/`、前端 `business-errors.ts`、`validate_openapi.py` 三处，收益为零。

#### 2.1.5 对已落地实现的评审

**做得对的地方（我认可，不建议返工）**：

| 点 | 证据 | 为什么对 |
| --- | --- | --- |
| 拒绝判据与成员口径（`department_member ∪ member_role`） | `security.py:461-508`（`assert_tenant_membership`） | 与 `identity_service` 的组织口径一致，不产生第二套真相 |
| 把校验放在 `get_request_context` 而非 `resolve_tenant_id` | `:548-551` | `resolve_tenant_id` 保持同步纯函数，`tests/unit/test_security.py:122-149` 5 条同步用例零改动 |
| 同租户请求不触发查询（性能红线） | `:549`（`if tenant_id != auth.tenant_id`） | 94 个端点的常态路径零额外查询 |
| 私有化模式跳过 | `:549`（`not settings.is_private_mode`） | 不误伤已交付的私有化/轻量版 |
| 平台令牌放行口径与 `is_platform_scope` 对齐 | `:443-458`（`_has_cross_tenant_privilege`） | 运营后台跨租户能力不被破坏 |
| 用例覆盖"条件写反"（只在自己租户有归属时仍拒绝） | `test_tenant_spoofing.py::test_membership_in_own_tenant_does_not_authorize_other_tenant` | 这是最容易写错的点，专门有防回归 |
| 用例覆盖"同租户不得查询成员"（monkeypatch 断言零调用） | 同上 `::test_same_tenant_request_passes_without_membership_lookup` | 性能约束变成可执行断言，不只是注释 |

**剩余缺口（3 项，按严重度）**：

| # | 缺口 | 证据/推演 | 影响 | 建议修法 | 工作量 |
| --- | --- | --- | --- | --- | --- |
| G1 | **`X-Tenant-Id: __platform__` 在 PG 后端会变成 500** | 伪造头 → `tenant_id="__platform__"` ≠ 令牌租户 → `_has_cross_tenant_privilege` 为 False（令牌租户不是哨兵、无 `*`）→ `assert_tenant_membership` → `get_org_store().links_of_user("__platform__", …)` → `tenant_scope()` → `db/tenant.py:assert_tenant_allowed(..., allow_platform=False)` → `INTERNAL_ERROR 30003` → HTTP **500**（`core/errors.py:512-513`） | 仍是 fail-closed（**不是漏洞**），但①返回 500 而非 403，②`error_handler.py:105` 把 30003 当内部异常，会污染错误率告警（`deploy/alert.rules.yml`），③内存模式下是 403、PG 模式下 500，**双后端行为不一致**（现有用例 `test_ops_isolation.py:176-190` 只在内存模式跑，测不出） | 在 `get_request_context` 的校验前加 3 行：`if tenant_id == OPS_TENANT_ID and auth.tenant_id != OPS_TENANT_ID → 20003`；同一断言补 `--run-pg` 用例 | S（0.2d） |
| G2 | **没有回退开关** | `grep "tenant_membership_check"` 无命中；校验无条件生效（除私有化/平台令牌） | 若 SaaS 实库成员关系为空（见 §4 待确认 1），上线即"全员 403"，只能改代码回滚 | 加 `Settings.tenant_membership_check: Literal["auto","always","off"]="auto"`（`auto` = `deploy_mode=="saas" and auth_mode!="stub"`），让第 549 行读它 | S（0.3d） |
| G3 | **跨租户切换无缓存 → 每次 2 次查询** | `assert_tenant_membership` 内 `links_of_user` + `role_ids_of_user`，各自独立 `tenant_scope`（= 2 个短事务） | 只影响"多租户用户切租户"的低频路径；当前无切换器，影响可忽略 | 若未来上切换器：进程内 TTL 60s + maxsize（同 `credential_service.py:288-291` 体例）。**本轮可不做**，但需在 docstring 标注已知性能特征 | — |

补充说明（信任边界，非缺口）：若令牌**声明**本身就是租户 B（由 IdP 签发），则"头==令牌"直接放行、不查库。这是正确的——令牌是签发方的承诺。**前提是 IdP 不会给非成员签发 B 租户令牌**。当前 SaaS 场景下租户由谁写进令牌，见 §4 待确认 2。

#### 2.1.6 测试用例清单（对照已落地用例，标出缺口）

| # | 场景 | 已落地？ |
| --- | --- | --- |
| 1 | 令牌 A + 头 B（非成员）→ 403/20003 且文案不回显内情 | ✅ |
| 2 | 用户只在自己租户有归属（防条件写反） | ✅ |
| 3 | 令牌 A + 头 A → 200 且**零成员查询** | ✅（monkeypatch 断言） |
| 4 | 令牌 A + 无头 → 按令牌声明，零查询 | ✅ |
| 5 | 平台令牌（哨兵租户 / 通配角色）可跨租户 | ✅（参数化 2 条） |
| 6 | 私有化 + 任意头 → `default`，不拒 | ✅ |
| 7 | 私有化**不触发**成员查询 | ✅ |
| 8 | 目标租户有 `department_member` 归属 → 放行 | ✅ |
| 9 | 目标租户有 `member_role` 归属 → 放行 | ✅ |
| 10 | **`__platform__` 伪造头 → 403（不是 30003/500），且 PG 后端同样 403** | ❌ **缺（G1）** |
| 11 | `tenant_membership_check=off` → 不校验（回退通道可用） | ❌ 缺（G2 配套） |
| 12 | PG 后端（`--run-pg`）跑 1/3/6/10 | ❌ 缺（现有全在内存模式） |

#### 2.1.7 工作量与风险

- 已落地部分：**M（1.5d）**（已完成）。
- 剩余缺口：**S（0.5d）**（G1 + G2 + 用例 10/11/12）。
- 风险：
  1. **误杀合法用户（最高）**：SaaS 实库 `member_role`/`department_member` 双空时开启校验 → 全员 403。**这是 G2 必须补的唯一理由**。缓解：上线前盘点（§4）+ `off` 开关。
  2. 双后端行为不一致（G1）会让"内存绿、PG 红"只在真实部署暴露。
  3. 60s 级撤权延迟：当前无缓存，不存在；若加缓存需接受。

#### 2.1.8 T1 结论速答（五个必答问题）

**① `resolve_tenant_id` 是 sync，成员校验放哪一层？**
放 **`get_request_context`（async 依赖层）**，不放 `resolve_tenant_id`。理由：
- `resolve_tenant_id` 是同步纯函数，`tests/unit/test_security.py:122-149` 有 5 条同步用例直接调它；改成 async 会波及全部调用点与既有用例；
- 校验需要查库（仓储是 async），天然属于依赖层；
- 现行落地实现正是这个分层（`security.py:512-560`，校验在 `:551`），**与我的建议一致，不需要返工**。

**② `department_member ∪ member_role` 可行吗？有现成 repo 方法吗？**
可行，**且不需要新建表/迁移**。现成方法：
- `OrgStore.links_of_user(tenant_id, user_id)` — 契约 `org_repo.py:176-178`，PG 实现 `:849-870`，内存实现 `:418-435`；
- `RoleStore.role_ids_of_user(tenant_id, user_id)` — 契约 `role_repo.py:285-291`，PG `:845-864`，内存 `:468-481`。
  **选 `role_ids_of_user` 而非 `role_key_of_user`**：前者 docstring 明确"只读，不触发内置角色播种"，后者会顺带写库（`role_repo.py:286` vs `:282`）。
- 必须取**并集**：`identity_service.py:228` 明写组织成员口径是 `department_member ∪ member_role`，只查 `member_role` 会误杀"只在部门、无角色"的成员。

**③ 私有化模式是否豁免？**
**豁免**，两处都已豁免：
- `resolve_tenant_id` 在 `is_private_mode` 时**提前返回 `default`、根本不读请求头**（HEAD `:214-215`）；
- 依赖层再加一道 `not settings.is_private_mode`（现行 `:549`），因此私有化下连成员查询都不会发生。
实证：`test_tenant_spoofing.py::test_private_mode_does_not_trigger_membership_lookup`（monkeypatch 断言零调用）。

**④ 需要改哪些文件、加哪些测试？**
已落地：`app/core/security.py`、`tests/security/test_tenant_spoofing.py`（10 条）。
仍需：`app/core/config.py`（回退开关 G2）、`app/core/security.py`（`__platform__` 提前拒绝 G1）、`tests/security/test_tenant_spoofing.py`（补用例 10/11/12）。完整 12 条用例矩阵见 §2.1.6。

**⑤ 是否存在依赖"任意 header 直接放行"的既有测试？**
**没有断言层面的依赖**，实测证据：`OA_BATCH=B21` 下 **1802 passed / 177 skipped / 0 failed**（§1.3）。
原因：31 个测试文件用到 `X-Tenant-Id`，但**令牌租户与请求头取同一个值**（`tests/conftest.py:296` 的 `auth_headers` 用 `TEST_TENANT_ID`，令牌也用同值），因此 `tenant_id == auth.tenant_id`，不触发校验。
**唯一的边界用例**是 `tests/integration/test_ops_isolation.py:176-190`（企业令牌伪造 `X-Tenant-Id: __platform__`，期望 403）——它在内存模式通过，但**在 PG 后端会因 G1 返回 500**，PG CI 一跑就会红。这就是 G1 必须同批修的直接原因。
另注：`qa_probe/probe_runtime.py:294` 也构造了"令牌租户 ≠ 请求头"的请求，但它属于手工探针脚本、不在 pytest 门禁内。

#### 2.1.9 ⚠️ 必须先讲的漂移：D-25 写了"短 TTL 缓存"，代码里没有

`grep -n "缓存\|cache\|_ttl\|TTL" apps/adapter/app/core/security.py` → **仅 1 处命中，且是无关的 `request._body` 注释（`:664`）**。

即：D-25 的决策文本含"短 TTL 缓存"，而落地实现**每次跨租户请求都做 2 次查询**（`links_of_user` + `role_ids_of_user`，各自一个短事务）。
处理建议（三选一，需主理人裁定）：
- **(a) 改决策文本**：把"短 TTL 缓存"降级为"后续优化项（当前无切换器，影响可忽略）"——**推荐**，因为当前前端只有全局设置一次 `X-Tenant-Id`（`api-client.ts:231-232`），跨租户请求几乎不存在；
- (b) 补实现：进程内 TTL 60s + maxsize（同 `credential_service.py:288-291` 体例），S（0.3d）；
- (c) 保留文本、标注"计划中"。

**风险提示**：若按 (b) 实现，会出现"撤权延迟 ≤ 60s"，必须在 D-25 里同时写明，否则又是一处文档超前于事实。

#### 2.1.10 复核进展（2026-10-23 18:44 后，G1/G2 已落地）

| 缺口 | 代码状态 | 测试状态 | 复核结论 |
| --- | --- | --- | --- |
| G1 `__platform__` 提前拒绝 | ✅ 已落地：`security.py:588-600` 把哨兵判断放在**任何仓储查询之前**（正是 PG 500 的根因点），`privileged` 复用同一 `_has_cross_tenant_privilege`，不误伤运营令牌 | ❌ 缺"企业令牌伪造 → 403"用例（现有仅参数化覆盖"平台令牌应放行"） | **实现可收，测试待补** |
| G2 回退开关 | ✅ 已落地：`config.py:83` `tenant_membership_check: auto/always/off`；`security.py:512-545` `_should_check_membership`（`off` 打 WARNING，`auto` = saas 且非 stub） | ❌ **零测试**（`grep -rn "tenant_membership_check" apps/adapter/tests` 为空） | **"回退通道"目前只是纸面能力**；必须补反向断言（`off` 下伪造头 → **200 放行**，而非"不报错"） |
| G3 无缓存 | 未做（D-25 已改为 W1 优化） | — | 可接受 |

**新发现的既有缺陷（非本轮引入）：`tests/security/test_attack_object_scope.py` 存在测试状态泄漏。**
四分定位证据（单进程 `pytest tests/unit tests/security tests/integration`）：

| 组合 | 结果 |
| --- | --- |
| `test_endpoints.py::TestAgentEndpoints::test_a2_list_tasks` 单跑 | ✅ pass |
| `test_endpoints.py` 整文件 | ✅ 47 passed |
| `tests/unit` + 该用例 | ✅ 1283 passed |
| `tests/security` + 该用例 | ❌ fail（`16002 … office_doc manifest missing`） |
| 逐个 security 文件二分 | 元凶 = **`test_attack_object_scope.py`** |
| `test_endpoints.py` **先于** `test_attack_object_scope.py` | ✅ 52 passed |

即**顺序敏感**：CI 的 `pytest tests`（`ci-adapter.yml:248`）按字母序为 integration → security → unit，不会踩到；但 `pytest tests/security tests/integration` 这种自然写法必红。修法轻（补 `reset_registry()`，该文件用 class 级 fixture 绕过了 `tests/conftest.py` 的 autouse 重置），但**不修不应认"全量绿"**。

**基线口径的自我更正**：§1.3 的"1802 passed"是**分两个进程**跑出的（unit+security 一次、integration 一次）；单进程合并后暴露上述顺序敏感失败。今后报基线必须连带写明命令，否则"我跑绿了"与"CI 跑绿了"会被混为一谈。

---

### 2.2 T2 · 登录真实化路径

#### 2.2.1 现状证据（HEAD 基线）

| 事实 | 证据 |
| --- | --- |
| 登录页「进入工作台」直接 `router.push('/chat')`，无任何认证 | HEAD `login/page.tsx:30-40` |
| 另一条路是手贴令牌存 `localStorage('officeai_access_token')` | HEAD `:42-59`（`:51`）；AR-018 已记录 XSS 风险 |
| 后端只做令牌校验，不做登录/签发 | `core/security.py:2-4`、`:127-170` |
| Keycloak 容器已部署（lite 与 saas） | `docker-compose.lite.yml:144-169`、`-saas.yml:327-340` |
| KC 客户端配置项存在，默认密钥为空 | `config.py:113-118`（`keycloak_client_secret=""`） |
| 后端已有 KC 客户端（仅 introspect / get_user / group members / create_role） | `clients/keycloak_client.py:26`、`:146-168` |
| **无 realm 导入文件 / 无 client 引导 / 无前端 client** | `find deploy -iname "*realm*"` 为空；`grep -rn "realm" docker-compose.lite.yml` 仅注释 |
| **签名算法错配（关键）**：后端固定 `jwt_secret` + `HS256`，无 JWKS/RS256 | `config.py:69-70`、`security.py:148-161`；架构已留判断 `architecture-v1.0.md:1414` |
| `.env.lite.example` 写死 `OA_JWT_ALGORITHM=HS256` | `.env.lite.example:105-110` |
| 前端无测试框架（AR-010） | `apps/web/package.json`（scripts 无 test） |

**由此得到的关键判断**：Keycloak 默认 RS256，后端只认 HS256 共享密钥 → **今天即便前端跳成功、拿到真令牌，后端也验不过**。"登录真实化"= 认证链路引导 + 签名算法对齐 + 用户标识映射 三件事，不是"接个页面"。

**第四件事（审计未提）**：`AuthContext.resolve` 取 `claims["sub"]` 作用户 ID（`security.py:76`），而 Keycloak `sub` 是 UUID；本仓全链路（`member_role.user_id`、`plugin_task.initiator_id`、`audit_log.actor_id`、测试 `usr_test_user`）都是 `usr_*`。不做映射 ⇒ "登录成功但看不到自己的数据"。代码里已有 `claims.get("user_id")` 兜底分支，但 `sub` 优先，需改为"有 `user_id` 声明时优先"。

#### 2.2.2 方案对比

**A 本轮实现完整 OIDC 授权码流程**，实际包含 8 个子任务：

| # | 子任务 | 基础 |
| --- | --- | --- |
| A1 | realm/client/用户/回跳地址引导 | ❌ 全无 |
| A2 | 签名算法对齐（HS256 配 secret **或** JWKS/RS256） | ❌ 需决策 + 代码 |
| A3 | 前端 PKCE 跳转/回调/续期（无 next-auth） | ❌ 无依赖、无测试框架 |
| A4 | 用户标识映射（`user_id` 声明优先 or 映射表） | 🟡 一行 + realm mapper + 存量补齐 |
| A5 | 首登建租户/建成员关系 | ❌ 且属产品决策 |
| A6 | 令牌存储改造（httpOnly Cookie，同时解 AR-018） | ❌ 需改 `api-client.ts` 全局取 token |
| A7 | 登出 / 静默续期 / 401 重定向 | ❌ |
| A8 | 联调（真起 KC + 浏览器 E2E） | 🟡 无浏览器测试框架 |

→ **L（5~8 人日）**，且 A2/A5 是决策依赖。

**B 本轮"诚实降级 + OIDC 脚手架"**（= D-26）：生产构建只留「统一登录」入口、未配置时只显示提示；开发构建保留直进/贴凭证但显式标注；完整授权码流程排下一批。→ **S（0.5d）**。

**推荐 B**。理由：`charter` §10.3 的核心承诺是"诚实"；在 A1/A2/A5 未定时硬做 A，最可能的结局是"跳转了但进不去"，比显式提示更糟。方案 B 让能力缺口变成**已登记、已排期、已验证的已知项**，这也是 AR-021 的正确关闭方式。

#### 2.2.3 对已落地实现的评审

**做得对的地方**：

| 点 | 证据 |
| --- | --- |
| 生产构建**只有一条路径**，未配置时只提示不给按钮（真降级，不是假入口） | `login/page.tsx:148-160`（`IS_PRODUCTION_BUILD` / `IS_UNIFIED_LOGIN_READY` 双分支） |
| 开发态入口被 `NODE_ENV` 静态替换隔离，生产 bundle 里不存在该分支 | `:24`、`:161-199` |
| 开发入口有显式黄条警示「仅开发环境：以下两种方式都绕过身份校验」 | `:163-165` |
| 界面统一称「统一登录」，不出现身份服务品牌名/技术术语 | `:151`、`:158` |
| 门禁实测通过（禁用词 0 命中、占位词 0 命中、`tsc` 通过） | `npm run scan:banned` / `scan:placeholders` / `npx tsc --noEmit` |

**剩余缺口（1 项阻断级 + 2 项加固）**：

| # | 缺口 | 证据 | 影响 | 建议修法 | 工作量 |
| --- | --- | --- | --- | --- | --- |
| **G4（阻断）** | **回跳地址 `/auth/callback` 路由不存在** | `login/page.tsx:50`（`CALLBACK_PATH='/auth/callback'`）、`:97` 用它拼 `redirect_uri`；但 `apps/web/src/app/(auth)/` 下只有 `layout.tsx` 与 `login/`，`find apps/web/src/app -path "*auth*"` 仅 2 个文件 | 一旦配置 `NEXT_PUBLIC_OIDC_ISSUER/CLIENT_ID`（即"准备上线"那一刻），用户会被送去 KC 登录、成功后回跳 **404**。**比现在"按钮不出现"更糟**——这正是 D-26 想避免的"假入口"，只是从登录页挪到了回跳处 | 二选一：**(a) 本轮最小**：`IS_UNIFIED_LOGIN_READY` 增加第三个条件 `CALLBACK_IMPLEMENTED=false`，未实现回跳前一律不渲染按钮（1 行 + 注释）；**(b) 补** `/auth/callback/page.tsx`：收到 `code` 后**不兑换**，只展示"登录流程改造中，请联系管理员"并清空 URL 参数（诚实、可测、不落任何令牌） | (a) 0.1d / (b) 0.3d |
| G5 | 授权请求**缺 `state`**（也无 PKCE） | `login/page.tsx:56-64`（`buildUnifiedLoginUrl` 只有 `client_id/redirect_uri/response_type/scope`） | 回跳时不校验 `state` ⇒ 登录 CSRF（攻击者诱导用户用攻击者账号登录）。当前因 G4 无法真正走通，故未暴露，但补回跳时必须同时补 | 与 G4(b) 同批：`sessionStorage` 存 `state`+`code_verifier`，回跳校验后清除 | 含在 G4(b) |
| G6 | `scope` 用字符串拼接规避词表（`'pro' + 'file'`） | `login/page.tsx:41-50` | 门禁能过，但**可读性差、且后人复制会传染**；`check-banned-terms.mjs` 已有 `ALLOWED_SAMPLES` 机制（`:121`）本可用于协议参数白名单 | 改为在 `ALLOWED_SAMPLES` 登记该协议参数，恢复常量可读性 | S（0.1d） |

#### 2.2.4 下一批（W1）的排期骨架

| 步骤 | 内容 | 前置 | 估算 |
| --- | --- | --- | --- |
| B-1 | realm/client/用户/回跳地址引导脚本 + `.env` 口径 | 无 | M |
| B-2 | 签名算法决策与实现（**推荐 HS256 配 secret** + 门禁断言"KC 侧算法 == 后端算法"） | 架构决策 | S（HS256）/ M（JWKS） |
| B-3 | 前端 PKCE（`state`+`code_verifier`）+ 令牌存储改造（合并 AR-018） | B-1 | M |
| B-4 | 用户标识映射（`user_id` 声明优先）+ 首登建成员关系 | B-1、产品口径 | M |
| B-5 | 401 重定向 / 续期 / 登出 + 冒烟 | B-3 | S |

#### 2.2.5 涉及文件（本轮剩余）

- `apps/web/src/app/(auth)/login/page.tsx`（G4 的 (a) 或 (b)、G5、G6）
- 新增 `apps/web/src/app/(auth)/auth/callback/page.tsx`（若选 (b)）
- 新增 `docs/auth-readiness.md`（上线前置条件 + 验收清单；**尚未创建**，`ls docs` 无此文件）
- `README.md`（当前认证状态小节）

---

### 2.3 T3 · 连接器「真实化 vs 诚实降级」

#### 2.3.1 现状证据

| 事实 | 证据 |
| --- | --- |
| 5 个连接器全部桩实现，自述"不发起任何真实第三方调用" | `tools/email_connector.py:6-9`、`github_connector.py:6`、`wecom_connector.py:13`、`wechat_mp_connector.py:13`、`feishu_connector.py` 头部 |
| `connect()` 返回 `connection_id=""`，由服务层补 ID ⇒ 当前无真实凭据落库 | `email_connector.py:96-100`、`services/tool_service.py:327` |
| 凭据存储**已具备**（无需迁移）：`tool_connection.secret_cipher/nonce/key_version` | `init-schema-phase2.sql:235-258` |
| 注册表 5 款全 `status: available`（**"可用"但实现是桩，这就是 AR-012 的语义错位**） | `registry/tool_registry.yaml` |
| 前端"办公工具"标签页工具卡片**内联渲染** | `apps/web/src/app/(main)/plugins/page.tsx:301-317` |
| 后端工具条目无"实现状态"字段 | `schemas/tool.py:86-105`（`ToolItem`） |
| 依赖里**没有** SMTP 库（仅 httpx） | `apps/adapter/pyproject.toml` |
| 前端禁用词（"凭据/oauth/租户/密钥/回调地址/redirect"…） | `apps/web/scripts/check-banned-terms.mjs:49-110` |
| 前端占位词（"暂不支持/开发中/即将上线"…） | `apps/web/scripts/check-no-placeholders.mjs:44-55` |

#### 2.3.2 单款真实化：选谁

| 候选 | 依赖 | 凭据 | **沙箱内能否端到端验证** | 外部副作用 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **邮箱 `send_message`（SMTP）** | 标准库 `smtplib`（**零新依赖**） | 应用专用密码 | ✅ 测试内起本地 SMTP sink（`127.0.0.1:1025`），真实 TCP + 真实 SMTP 会话，断言收件人/主题/正文 | 发到自己可控的 sink | **最优** |
| 邮箱 `list_messages`（IMAP） | 标准库 `imaplib` | 同上 | ⚠️ 需 IMAP 服务端（额外容器） | 只读 | 次选 |
| GitHub `create_issue` | httpx（已有） | **需真实 PAT + 外网** | ❌ 只能 `MockTransport` 回放（≠ 端到端） | 会真建 issue | 不适合"可离线验证" |
| 企业微信 / 飞书 | httpx | 需企业管理员建应用、开权限、可信域名 | ❌ | 会真发消息 | 不建议首个 |
| 微信公众号 `publish_article` | httpx | 需认证公众号 | ❌ | **不可逆对外发布** | 最不该首个 |

**推荐首个：邮箱 `send_message`（SMTP）**。理由按优先级：①零新依赖（`smtplib` 标准库；若坚持全异步，`aiosmtplib` 为 MIT 但要过 `validate_deps.py` + 许可证清单，+0.5d）；②**可离线端到端验证**——这是"代码存在 ≠ 功能可运行 ≠ 集成已验证"（AR-021）唯一的破法；③副作用可关在自己手里；④已有 `wechat_mp_connector.py` 的 `confirmed` 二次确认体例可复用。

> 与 D-19 的差异说明：D-19 写"邮箱 + GitHub 排 W1-W4"。我的评估是**GitHub 不具备离线验证条件**，建议 W1 只做邮箱，GitHub 排到"有受控测试账号 + 出站白名单"之后。这是可讨论的偏差，不影响方向。

#### 2.3.3 真实化最小改动（M，2~3 人日，含护栏）

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `app/tools/email_connector.py` | `connect` 把 `{host,port,username,password,use_tls}` 加密存 `tool_connection.secret_cipher`（走 `credential_service`）；`send_message` 用 `smtplib` + `asyncio.to_thread`；错误映射沿用 `13003/13004/13006` |
| 2 | `app/services/tool_service.py` | 解密凭据并传给连接器（当前 `execute` 只传 `connection_id`） |
| 3 | `app/schemas/tool.py` | `CreateConnectionRequest` 增加 `host/port/username`（可选） |
| 4 | `app/tools/base.py` | `IMPLEMENTATION_MODE` + `NETWORK_EGRESS` 声明（见 2.3.4） |
| 5 | 新增 `tests/unit/test_email_connector_smtp.py` | 本地 sink 断言成功路径 + 超时/认证失败/连接拒绝三条错误路径 |
| 6 | `apps/web/.../plugins/page.tsx` | 连接抽屉补"服务器地址/端口"表单 |

**出站护栏必须同批交付**（否则不许上线）：`OA_TOOL_EGRESS_ALLOW_PRIVATE`（私有化默认 true / SaaS false）+ 拒 link-local（`169.254.0.0/16`、云 metadata 域名）+ 端口白名单（25/465/587/1025）+ `timeout_ms` 强制生效。

#### 2.3.4 诚实降级：最小改动清单（本轮，S）

原则：**实现状态必须由实现自述（代码常量），不能由 YAML 声明**——否则重演"配置说 live、实现是桩"（AR-012 的成因）。

| # | 文件 | 改动 | 为什么这里最有效 |
| --- | --- | --- | --- |
| 1 | `app/tools/base.py` | `ToolConnector.IMPLEMENTATION_MODE: ClassVar[str] = "demo"`（谁写真实实现谁改这一行） | 单一事实源；`ToolService` 构造期可校验 |
| 2 | `app/schemas/tool.py:86-105` | `ToolItem` 增 `implementation_mode: Literal["demo","live"]` | T1 注册表是前端唯一数据源 |
| 3 | `app/services/tool_service.py` | 组装时读连接器常量 | 5 个 connector 的 `execute` 零改动 |
| 4 | `apps/web/src/lib/types.ts:301-310` | 类型加 `implementation_mode` | 类型先行 |
| 5 | `apps/web/src/app/(main)/plugins/page.tsx:301-317` | 卡片加 `<Badge>演示</Badge>`；标签页顶部一行说明 | 用户实际看到的那一屏 |
| 6 | `README.md` | 「能力实现状态」表 | 交付/验收口径 |
| 7 | 新增 `tests/integration/test_tool_registry_mode.py` | 断言全 `demo`；新增 live 时测试变红（强制同步） | 防回归 |

**文案红线（先看门禁词表，否则一定红）**：禁用 `凭据/oauth/租户/密钥/回调地址/redirect`（`check-banned-terms.mjs:49-110`）、`暂不支持/开发中/即将上线/敬请期待`（`check-no-placeholders.mjs:44-55`）；可用 `演示/演示模式/只走流程/不会真的发出`。

**推荐组合（= D-19）**：本轮诚实降级（S，0.5d）+ W1 邮箱发送真实化（M，2~3d，含出站护栏）。

---

### 2.4 T4 · 核心指标最小落地（总纲 §十四）

#### 2.4.1 现状证据

指标定义：`charter:488-508`；已有细化文档 `docs/metrics-definition-v1.0.md`（M1~M8，含阈值留白说明）。

| 指标 | 现有数据能否直接算 | 证据 |
| --- | --- | --- |
| 任务完成率 | ✅ `plugin_task.status`（含 `completed/failed/cancelled` 与 `queued/pending/running/waiting_confirm`） | `init-schema-phase3.sql:170-215` |
| 任务完成时间 | ✅ `started_at` / `finished_at` | 同上 `:203-204` |
| 重复使用率 | ✅ `initiator_id` + `plugin_id` + `started_at`（`idx_task_initiator` 支撑） | 同上 |
| 单位成功任务成本 | ✅ `plugin_task.usage_units` 已在任务上累计 | `:200` |
| 故障恢复能力 | 🟡 部分：`retry_of` / `trigger_type='retry'` / `error_message` | `:181-190` |
| **成果采纳率** | ❌ **不能**：`plugin_artifact` 只有 `version/version_history`，无任何"被采用/下载/复制"事件 | `:250-278`；`audit_service.py:56-166` 动作清单亦无 artifact 采纳动作 |
| 满意度 / 付费意愿 | 🟡 付费意愿可由 `billing_*` 近似；满意度无数据源 | `init-schema-phase2.sql:337-460` |
| 能力维护效率 | ❌ 无数据源（需工时/变更统计，非产品数据） | — |

**结论**：4 项零 schema 变更可算；1 项部分可算；采纳率**必须有埋点**；满意度/维护效率本批不做。

#### 2.4.2 采纳率：最小 schema 变更

| 方案 | 内容 | 利 | 弊 |
| --- | --- | --- | --- |
| A：`plugin_artifact` 加列（`adopted_at`/`adopt_count`） | 两列 | 改动最小 | 丢失"谁/何时/几次"；无法算采纳用户数；与多版本语义纠缠 |
| **B（推荐）：新表 `artifact_adoption`** | `(tenant_id, adopt_id, artifact_id, task_id, user_id, action, created_at)`，`action ∈ download\|copy\|insert` | 可算次数/用户数/分母对齐；append-only 易审计；不动已冻结的 artifact 表 | 需新建表 + RLS + N-1 兼容 |

**埋点位置（3 处，不改既有契约）**：①产物下载成功写 `download`；②"复制正文"按钮 → `POST /agent/tasks/{id}/artifacts/{aid}/adopt`；③"回填知识库/工作台" → 同上 `action='insert'`。**幂等**：带 `Idempotency-Key`，按 (user, artifact, action, 日) 去重。

**N-1 兼容（照抄本仓体例）**：老库无新表时写采纳**不得** 500 —— 复用 `db/schema_guard.py:1-40` 的"探测一次 + 缓存 + 降级"模式（`USAGE_DIMENSION_COLUMNS` 即同型）。

**迁移需三处同步**（本仓体例，否则新装库与升级库不一致）：`deploy/migrations/0012_artifact_adoption.sql` + `deploy/migrations/rollback/0012_*.down.sql` + `deploy/init-schema-phase4.sql`（参照 `tenant_budget`：基线 `:276-293` 与迁移 `0011` 双写）。

#### 2.4.3 本轮最小切片（4 项指标，零迁移）

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `app/db/repositories/task_repo.py` | `TaskStore.task_stats(tenant_id, since, until)` + 内存/PG 双实现。PG 一条聚合 SQL：`COUNT(*) FILTER (WHERE status='completed')`、`AVG/percentile_cont(EXTRACT(EPOCH FROM finished_at-started_at))`、`COUNT(DISTINCT initiator_id)`、`SUM(usage_units) FILTER (WHERE status='completed')` |
| 2 | 新增 `app/schemas/stats.py` | `TaskStatsData`：`total/completed/failed/cancelled`、`completion_rate`、`avg_duration_seconds`、`p50_duration_seconds`、`active_users`、`repeat_user_rate`、`units_per_success` |
| 3 | 新增 `app/api/v1/stats.py` | `GET /api/v1/stats/tasks?range=7d\|30d`，`Depends(get_request_context)`；权限点**复用 `task:read`**，不扩张权限矩阵 |
| 4 | `app/api/v1/router.py` | 挂载（与其它域一致的 `business_dependencies`，含限流） |
| 5 | `apps/web/src/lib/api-client.ts` / `types.ts` | `statsApi.tasks(range)` + 类型 |
| 6 | `apps/web/src/app/(main)/workflows/page.tsx` | 表格上方 4 个指标卡（复用 `Card`/`LoadingState`/`ErrorState`） |
| 7 | 新增 `tests/integration/test_stats_tasks.py` | 造 6 个任务断言分子分母；无数据时返回 0 而非 NaN；`range` 非法 → 20001 |

**工作量：M（1.5d）**；采纳率一批：**M（1.5~2d）**。

**风险**：
1. **口径边界**：`plugin_task` 只覆盖插件任务，纯对话（`conversation`/`message`）不在内 → 看板标题必须写"工作流任务"，并与 `M4 /model/usage`（`api/v1/model.py:133-140`）并列说明"两者口径不同"，否则会被读成"整体使用量"。
2. **小样本噪声**：早期租户任务极少，完成率只有 0%/100%。建议同时返回 `total`，`total < 10` 时前端显示"样本还少，仅供参考"（**避开占位词表**）。
3. PG 聚合走 `idx_task_status_time`（`init-schema-phase3.sql:211-212`），单租户十万级无压力。

---

### 2.5 T5 · Open WebUI 的事实核对

#### 2.5.1 全仓检索（本人重跑）

```
grep -rni "open.webui|openwebui|open_webui" 全仓（排除 node_modules/.git）
→ 命中文件：docs/adr/0003-design-token-baseline.md、docs/moy-project-charter-v1.0.md、
            docs/plan-v1.0.md、docs/prd-v1.0.md
```
（注：`plan-v1.0.md` / `prd-v1.0.md` 的命中已被并行改动移除，现仅剩历史引用语义；`charter` 未改。）

| 维度 | 事实 |
| --- | --- |
| `deploy/` 编排 | **零引用**（9 个常驻容器无它，`docker-compose.lite.yml:7`） |
| 前端依赖 | **零引用**（`apps/web/package.json` 无相关包；栈为 Next.js 16 + React 19 + Radix + Tailwind） |
| 前端源码 | **零引用**（`apps/web/src` 无任何标识） |
| 文档原语义 | `plan-v1.0.md` 原「前端交互层（基于 Open WebUI 改造）」+「去掉 Open WebUI 原生样式」；`prd-v1.0.md` 原「Open WebUI 前端参考」；`adr/0003:17` 引用同一句 |
| 总纲语义 | `charter:231-241` §7.2 与 Dify/Milvus 并列写作「**当前技术基础**」 |

#### 2.5.2 事实结论

**"曾计划参考、最终未采用"，当前实现与其无任何代码/依赖/部署关系。**

依据链：①`plan-v1.0.md` 原文里它是"要**去掉**的样式来源"，本身不是"使用的组件"；②`prd-v1.0.md` P0-19 要求"去掉 Open WebUI 原生样式与全部技术向功能"，立项时即定位为短期跳板；③三处编排 + 全部依赖零引用；④实际实现是 Next.js + React，与"基于它改造"不可能平滑演进。**"是否曾把它的代码拷进来"无法从当前工作区证明 → 标「待验证」**（`git log -S "open-webui"` 可判，本轮未执行 git 历史检索）。

#### 2.5.3 建议：修表述，不补实现（= D-13）

**已在 `plan-v1.0.md` / `prd-v1.0.md` / `architecture-v1.0.md` / `README.md` 落地**（并行改动，`git diff` 可见，引 ADR-0005 D-13）。**唯一剩余项 = 总纲 §7.2**：

> 建议改为（与 D-13 一致的口径）：
> - **Dify：** AI 应用与工作流相关基础能力（当前实际使用）；
> - **Milvus：** 向量存储与检索相关能力（当前实际使用）；
> - 前端交互层：自研（Next.js + React + 统一组件库）；曾评估过的第三方聊天前端方案（如 Open WebUI）**已评估、未采用**，理由是技术栈与自研统一体验要求不匹配；
> - **Moy 自身的平台代码**：承担业务编排、工具接入、任务管理、结果交付及平台层面的统一控制。

**理由**：①总纲是给合伙人/投资人/客户看的（§15），把零引用项目列为"当前技术基础"，一旦尽调即击穿全部技术叙述（与 AR-021 同类：文档说 A、代码是 B）；②不补实现——把 Open WebUI（前端为 SvelteKit 系）引入已成型的 React/Next 应用等于重写前端，而 §7.4「模块化与可替换性」已由 `api-client.ts` 单出口 + 适配层达成；③保留历史：`adr/0003:17` 引用的是"总方案原文"，属历史决策，**不需改**。

**工作量：S（0.25d，纯文档）**。**注**：D-13 标注"待创始人确认"，因此**我不单方修改总纲措辞**，只提交建议文本（见 §4 待确认 7）。

#### 2.5.4 附：Milvus 的表述是**另一类问题**（已核实，勿一并删除）

§2.5.3 曾建议"同批复核 Milvus"。**已核实结论：Milvus 与 Open WebUI 不同类，不能一起删。** 证据：

| 事实 | 证据 |
| --- | --- |
| SaaS 确实部署 Milvus standalone + etcd + object-store | `docker-compose.saas.yml:139-156`、`:100`、`:115` |
| **接它的是 Dify，不是 Moy** | `:213-214`、`:253-254`：Dify 服务 `VECTOR_STORE: milvus` + `MILVUS_URI: http://milvus:19530` |
| 适配层**零直连** | `grep -rni "milvus" apps/adapter/app --include=*.py` → 仅 `core/config.py:125` 一个**从未被读取**的 `milvus_uri` 字段（compose `:382` 仍以 `OA_MILVUS_URI` 传入，属死配置） |
| 检索链路全程经底座 | `services/rag_service.py:67-69`（`/v1/datasets`、`/v1/datasets/{id}/retrieve`） |
| 轻量版用 pgvector | `docker-compose.lite.yml:237,304`（`VECTOR_STORE: pgvector`）、`:62`（须 pgvector 官方镜像，迁移 0009 要 `CREATE EXTENSION vector`） |

**因此建议**：总纲 §7.2 的 Milvus 条目改为「**由底座承载**」口径（并入 Dify 条或单列注明"Moy 不经手"），而不是删除——底层在跑是事实，"我们掌握了 Milvus"不是事实，尽调追问的恰是后者。

**并据此重新定位 D-05**（原表述「Milvus 是否接受共享实例按 partition/collection 前缀隔离」）：既然适配层不直连，**风险面不在隔离机制**（Dify 已按 dataset 隔离，租户边界 = `dataset_id`，映射由 `knowledge_base_meta(tenant_id, base_id)` 保证不串），而在**可核查性**——建数据集的 payload 是 `rag_service.py:267-275`（`self._dify.post(_DIFY_DATASETS_PATH, {...})`，其中 `"name": request.name` 在 `:269`；**注意 `:258` 只是 logger 字段，不是 payload**，别查错行），**不带租户前缀**，跨租户同名不串数据但无法从名称判断归属。建议 D-05 收敛为「共享向量实例的租户可核查性」（命名/标签带租户标识），改动面 `rag_service` 一处 + 存量回填，S 级，不紧急但不应以"隔离待决"长期挂账（已由 PM 登记为 LED-044）。另建议顺手清理 `config.py:125` 的死配置（或加注释说明仅作容器透传）。

---

### 2.6 T6 · 统一任务入口 MVP（总纲 §4.1）

#### 2.6.1 现状证据（比预期乐观）

| 能力资产 | 证据 | 对 MVP 的意义 |
| --- | --- | --- |
| 6 个插件声明，含 `name/summary/description/category/inputs[].label` | `app/plugins/manifests/plg_*.json`（如 `plg_weekly_report.json:2-5,47-66`） | **天然关键词语料**（全业务语言，无技术术语） |
| 声明启动时加载进内存注册表 | `plugins/loader.py:160-192`、`:219-284` | 目录端点**不查库** |
| 5 款工具含 `display_name/category/operations[].display_name` | `registry/tool_registry.yaml` | 工具也参与推荐 |
| 建任务请求**已有 `goal` 自由文本** + `plugin_id` + `inputs` | `schemas/agent.py:65-89`（`goal` `:80`、`plugin_id` `:85-89`） | 推荐 → 一键建任务**无需改契约** |
| 建任务端点已支持插件分支 | `api/v1/agent.py:99-140` | 直接复用 |
| 首页当前 `redirect('/chat')` | `apps/web/src/app/page.tsx:1-13` | 有明确落点 |

**结论：MVP 不需要新模型、不需要向量检索、不需要新表。**

#### 2.6.2 最小实现路径（= D-17 的"W1 对话页目标式发起"）

| # | 层 | 文件 | 内容 |
| --- | --- | --- | --- |
| 1 | 后端 | 新增 `app/services/capability_catalog.py` | 从 `PluginRegistry` + `tool_registry.yaml` 组装 `{id, kind, name, summary, category, inputs[]}` |
| 2 | 后端 | 新增 `app/services/goal_router.py` | **规则打分**：`goal` 中文 2-gram + 关键词表 → 命中 `name(×3)/summary(×2)/category(×1)/inputs.label(×1)` → Top3 + 命中理由 |
| 3 | 后端 | 新增 `app/api/v1/capability.py` + `router.py` | `GET /api/v1/capabilities/recommend?goal=…&limit=3`；空目标返回目录而非报错 |
| 4 | 前端 | `apps/web/src/app/(main)/chat/page.tsx` | 顶部目标输入框 + 推荐卡（"开始"→ `/workbench/{pluginId}?goal=…`） |
| 5 | 前端 | `workbench/[pluginId]/page.tsx` | 读 `?goal=` 预填首个文本输入，并作为 `goal` 提交 |
| 6 | 测试 | 新增 `tests/integration/test_capability_recommend.py` | 12 条：各类目标命中正确插件、易混词不串、空目标、超长（4000 上限）、无命中兜底、`limit` 越界 |

**关键设计决策**：①**规则而非模型**——引入模型会带来"推荐不可解释 + 模型不可用即入口不可用"两个新故障点；②**推荐理由必须回填**，否则等于换了个搜索框；③**不新建"能力"表**，目录实时组装，避免第三份真相。

**工作量：S（1 人日）**。**是否本轮做**：值得，但排在 T1 补口、T3 降级、T5 之后——它是体验增强，T1 是数据安全，优先级不能倒置。

**风险**：①规则召回率天花板（6 个插件够用，20+ 会退化，届时换语义检索而接口不变）；②中文分词不引入 jieba（依赖门禁），用关键词表 + 2-gram 覆盖 6 个插件的领域词；③改 `chat` 页需跑 `npm run scan:pages`（`check-page-states.mjs` 要求加载/空/错误态齐全）。

---

## 3. 剩余待办清单（按依赖排序）

| 序 | 项 | 依赖 | 改动文件 | 验证方式 | 估算 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **T2-G4 补回跳或禁用按钮**（阻断级） | 无 | `apps/web/src/app/(auth)/login/page.tsx`（+ 可选新增 `(auth)/auth/callback/page.tsx`） | `npx tsc --noEmit` + `npm run scan:banned && scan:placeholders`；人工：配置 issuer 后点按钮 → 不再 404 | 0.1~0.3d | **P0** |
| 2 | **T1-G1 `__platform__` 提前拒绝 + PG 用例** | 无 | `app/core/security.py`、`tests/security/test_tenant_spoofing.py` | 新增用例 403（内存 + `--run-pg` 双跑）；全量 pytest 1802+ | 0.2d | **P0** |
| 3 | **T1-G2 回退开关** | 依赖 2（同文件） | `app/core/config.py`、`app/core/security.py`、新增用例 | 开关 `off` 时伪造头不再 403（证明回退通道可用） | 0.3d | **P0** |
| 4 | **T5 总纲 §7.2 表述** | 待创始人确认（D-13） | `docs/moy-project-charter-v1.0.md` §7.2 | 与 `plan/prd/架构` 已改口径一致；`grep -rni "open.webui"` 结果与表述相符 | 0.25d | P1 |
| 5 | **T3 诚实降级** | 无 | `app/tools/base.py`、`app/schemas/tool.py`、`app/services/tool_service.py`、`apps/web/src/lib/types.ts`、`apps/web/.../plugins/page.tsx`、`README.md`、新增 `tests/integration/test_tool_registry_mode.py` | pytest + 前端三扫 | 0.5d | P1 |
| 6 | **T4 任务指标切片** | 无（与 5 无冲突） | `app/db/repositories/task_repo.py`、新增 `app/schemas/stats.py`、`app/api/v1/stats.py`、`app/api/v1/router.py`、`apps/web/{types,api-client}.ts`、`workflows/page.tsx`、新增 `tests/integration/test_stats_tasks.py` | pytest + `python deploy/validate_openapi.py` + `npm run scan:pages` | 1.5d | P1 |
| 7 | **T2-G5/G6 state+PKCE 与协议参数白名单** | 依赖 1 | 同上 + `apps/web/scripts/check-banned-terms.mjs`（`ALLOWED_SAMPLES`） | 三扫 | 0.2d | P2 |
| 8 | **T6 统一入口 MVP** | 依赖 6（同改 `types.ts`/`api-client.ts`） | §2.6.2 六个文件 | pytest + `scan:pages`/`scan:capability` | 1.0d | P2 |
| 9 | **T4 采纳率（0012 迁移 + 埋点）** | 依赖 6 | `deploy/migrations/0012_*` + `rollback/0012_*.down.sql` + `init-schema-phase4.sql` + 后端端点 + 前端埋点 + `schema_guard` 降级 | `python deploy/validate_migrations.py` + pytest + PG 跑 `--run-pg` | 1.5~2d | P2 |
| 10 | **T3 邮箱 SMTP 真实化 + 出站护栏** | 依赖 5 | §2.3.3 六个文件 + `config.py` 出站开关 | 本地 SMTP sink 端到端用例；`.env*.example` 补开关 | 2~3d | P3 |

**合计剩余**：P0 约 0.6d、P1 约 2.25d、P2 约 2.7~3.2d、P3 约 2~3d。

**统一收尾命令**（本仓门禁习惯）：
```
cd apps/adapter && OA_BATCH=B21 ./.venv/Scripts/python.exe -m pytest tests -q
cd apps/adapter && ./.venv/Scripts/ruff.exe check app tests
python deploy/validate_openapi.py && python deploy/validate_migrations.py
cd apps/web && npx tsc --noEmit && npm run lint && npm run scan:banned && npm run scan:placeholders && npm run scan:pages
```

---

## 4. 待确认事项

| # | 事项 | 为什么必须确认 | 建议口径 |
| --- | --- | --- | --- |
| 1 | **SaaS 实库"用户↔租户"关系是否齐备** | 决定 T1 能否直接生效。若 `member_role`/`department_member` 双空（例如身份改用 KC group 直连、从未调成员管理），开启校验即"全员 403" | 上线前盘点：`SELECT count(DISTINCT tenant_id) FROM member_role;`、`SELECT count(*) FROM department_member;`。为 0 则先上 G2 开关（`off`），补数据后再切 `auto` |
| 2 | **租户由谁写进令牌**（IdP mapper？） | 若令牌 `tenant_id` 由用户自选（登录时选企业），则"头==令牌"直接放行意味着 IdP 承担成员校验责任，应用层的 `is_member` 就成了第二道闸；若 IdP 只写主租户，则多租户用户切租户必须靠 `is_member` | 需与身份底座方案一起定；本轮不影响 P0 项 |
| 3 | **一个用户是否允许同属多租户** | 决定是否保留"合法切换"分支与是否做切换器 | 建议允许（模型天然支持）；前端本批不做切换器 |
| 4 | **任务指标口径边界** | `plugin_task` ≠ 全部使用量 | 看板标题写"工作流任务"，并与 `M4 /model/usage` 并列说明口径差异 |
| 5 | **采纳率的"采纳"定义与权重** | 下载/复制/回填三种行为不等价 | 建议：分母 = 有产物的成功任务数；分子 = 至少发生一次 `download\|copy\|insert` 的任务数；三行为先等权，后续按数据调整 |
| 6 | **T2 签名算法路线（HS256 vs JWKS/RS256）** | 影响工作量（S vs M）与后续接第三方 IdP 的能力 | 建议 HS256 + 门禁断言"KC 侧算法 == 后端算法"；JWKS 列入"接外部 IdP 时再做" |
| 7 | **总纲 §7.2 措辞**（D-13 标注"待创始人确认"） | 总纲是 L0 北极星文档，措辞权不在架构侧 | 建议采用 §2.5.3 的建议文本；同时复核 §7.2 中 Milvus 的表述（`architecture-v1.0.md` 有 D-05「共享实例按 collection 前缀隔离」待决） |
| 8 | **"演示模式"文案是否可上商用界面** | 涉及产品定位（内部演示 vs 对外交付） | 由主理人/PM 裁定；技术上 `implementation_mode` 字段与文案解耦，可只留字段不下发文案 |
| 9 | **D-19 是否调整"GitHub 同批真实化"** | 我评估 GitHub 无法离线验证 | 建议 W1 只做邮箱 SMTP，GitHub 排到有受控测试账号 + 出站白名单之后 |

---

## 5. 附：证据索引（便于复核）

```
# T1
apps/adapter/app/core/security.py（HEAD:217-219,466-470；工作区:443-508,512-560）
apps/adapter/tests/security/test_tenant_spoofing.py（新增 10 条）
apps/adapter/app/core/errors.py:35,166,512-513；app/middleware/error_handler.py:105
apps/adapter/app/db/{session.py:41-42,308-354;tenant.py:31-40}
apps/adapter/app/db/repositories/{role_repo.py:285-291,845-864;org_repo.py:176-178,849-870}
deploy/init-schema-phase2.sql:490-510,547-556

# T2
apps/web/src/app/(auth)/login/page.tsx:24-50,56-64,80-199,201
apps/adapter/app/core/{security.py:76,127-170;config.py:68-73,113-118}
apps/adapter/app/clients/keycloak_client.py:26,146-168
deploy/docker-compose.lite.yml:144-169,417-421；-saas.yml:327-340,372-376；.env.lite.example:34,105-110
docs/architecture-v1.0.md:1414

# T3
apps/adapter/app/tools/*.py（头部自述）；registry/tool_registry.yaml
apps/adapter/app/schemas/tool.py:86-105；services/tool_service.py:327
apps/web/src/app/(main)/plugins/page.tsx:301-317
apps/web/scripts/{check-banned-terms.mjs:49-110,check-no-placeholders.mjs:44-55}
deploy/init-schema-phase2.sql:215-258

# T4
deploy/init-schema-phase3.sql:170-215,250-278；apps/adapter/app/db/repositories/task_repo.py:126-215,248-309
apps/adapter/app/business/audit_service.py:56-166；app/db/schema_guard.py:1-40
docs/moy-project-charter-v1.0.md:488-508；docs/metrics-definition-v1.0.md
deploy/init-schema-phase4.sql:276-293；deploy/migrations/0011_tenant_budget.sql

# T5
docs/moy-project-charter-v1.0.md:231-241；docs/decisions/0005-charter-first-alignment.md:22,101
docs/plan-v1.0.md、docs/prd-v1.0.md、docs/architecture-v1.0.md（并行改动 git diff）
docs/adr/0003-design-token-baseline.md:17

# T6
apps/adapter/app/plugins/manifests/plg_*.json；app/plugins/loader.py:160-192,219-284
apps/adapter/app/schemas/agent.py:65-89；app/api/v1/agent.py:99-140
apps/web/src/app/page.tsx
```

---

## 6. 跨角色接口确认（供 PM / Engineer / QA 对齐）

| # | 来自 | 事项 | 我的答复 |
| --- | --- | --- | --- |
| 1 | PM | `docs/api/openapi*.json` 与 `app/main.py:54,202` 仍有「企业级跨生态办公 AI 平台」 | **事实核实无误**：`API_DESCRIPTION` 首行（`:54`）+ `title=`（`:202`），两个快照各 2 处命中。**但这是代码改动 + 快照再生成，超出本人"本轮不改代码"的授权**，需主理人放行后由我或 Engineer 执行；且必须连带跑 `deploy/validate_openapi.py`，否则快照与代码不一致会让门禁失真 |
| 2 | PM | `architecture-v1.0.md` 头部按 D-09/D-13 修改、署名"许清楚（代架构师登记）" | **接受**。措辞"不改动任何接口路径/字段名/错误码/门禁判据"准确（`git diff` 仅 9 行、全在头部与修订记录）。建议补一句"L2 与 L0/L1 冲突以上游为准"已含，无需再改 |
| 3 | PM | 采纳率四动作（下载/复制/导出/编辑后保存）+ 7 天窗口未触发记 `none` | **动作定义可行，`none` 记法建议否决**：①`artifact_adoption` 是 append-only 事件表，写入"未发生"违背事件语义；②需要常驻清理任务（新故障点 + 新调度面）。**替代方案（零清理任务）**：采纳率分母 = "完成满 7 天的成功任务数"，在查询期用 `finished_at <= now() - interval '7 days'` 过滤即可，分子照旧查事件。**动作侧**：下载（后端已有 `/files/artifacts` 路由 `artifact_files.py:26,70`）、编辑后保存（`PUT /agent/tasks/{id}/artifacts/{aid}`，`api-client.ts:777-782`）都有现成钩子；"复制"是纯前端需新端点；**"导出"与"下载"在实现上重合，建议合并为一个动作**，否则两处埋点会重复计数 |
| 4 | PM | 修复完成后需"已合并 + 测试全绿"信号以回填 P0-11/P0-21 | 可以给：W0 修复工作区已落地且全量 **1802 绿**；但**建议回填话术写"W0 已修复（G1/G2 补口中）"**，等 G1（PG 后端 500）与 G2（回退开关）落地后再写"已完成"，否则又是一处"文档说完成、PG CI 未跑"的超述 |
| 5 | Lead | T3 优先级里含 GitHub 同批真实化 | 建议 **W1 只做邮箱 SMTP**：GitHub 需真实 PAT + 外网，沙箱内只能 `MockTransport` 回放，不构成"集成已验证"。见 §2.3.2 |

---

**本人本轮未修改任何代码或配置**，仅新增本文件。


---

## 7. 复核更正记录（QA 独立复测后）

| # | 原文表述 | 更正 | 依据 |
| --- | --- | --- | --- |
| 1 | 「带 --run-pg 跑 test_ops_isolation.py::test_forged_platform_header_is_rejected 会红」 | **机制写错了**：--run-pg 只解 pg 标记的 skip，**不会**把全局 business_store 切成 postgres；该用例的 saas_settings 未设 business_store（默认 memory）且未标 pg，所以"带 --run-pg 跑这条"永远绿、暴露不了红灯 | QA 实跑：--run-pg 版仍 1 passed；改用真实 PG + business_store=postgres 构造 app 才复现 **memory 403/20003 vs postgres 500/30003** |
| 2 | 判据①「PG 下 403 而非 500」 | **语义成立、且已被修复**：security.py:569-598 的 early-reject 生效后，QA 复测 postgres 与 memory **均 403/20003**。但**覆盖仍未闭合**：spoofing 用例是 memory 形态，一旦删掉那段 early-reject，memory 用例仍会绿（membership 兜底 403），**只有 PG 会红** → 需补一条 pg 标记的 HTTP 用例 | QA 用真实 PG（moy-qa-pg）复测 |
| 3 | 基线「1802 passed / 177 skipped」 | QA 单进程全量 OA_BATCH=B21 pytest tests -q = **1810 passed / 201 skipped / 0 failed（357s）**；我此前的 1802/177 是**分进程 + 时点更早**，差值来自新增用例（test_tenant_spoofing.py / test_tool_registry_mode.py），非回归 | 两者结论一致：0 failed |
| 4 | CALLBACK_PATH 在 login/page.tsx:36、redirectUri 在 :97-98 | 实为 **:50**（CALLBACK_PATH）与 **:97**（redirectUri）；回跳页已按方案 (b) 落地为诚实占位 | QA 静态核实；本报告已实测前端三门禁 0 命中 |
| 5 | 本机全量跑的命令 | 补执行坑：全量跑时 pytest 清理临时目录会触发沙箱批量删除拦截（[safe-delete] … count>50），表现为 **exit 1 且不打印汇总行**，看起来像"挂了"；加 --basetemp=../../.pytest_tmp（跑完删目录）即可拿到汇总 | QA 实测 |

**保留未消项**：单进程 pytest tests/unit tests/security tests/integration 曾出现 1 红（test_endpoints.py::TestAgentEndpoints::test_a2_list_tasks，根因 tests/security/test_attack_object_scope.py 状态泄漏，顺序敏感）。截至本报告最后一次实测仍未修；若已修复以最新为准。**顺序敏感本身就是"跑绿了 ≠ CI 会绿"的证据，不应被当作可忽略项**。
