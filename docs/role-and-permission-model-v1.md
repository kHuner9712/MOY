# MOY 角色与权限模型 v1（最小统一版）

更新时间：2026-03-21  
适用范围：MOY 当前代码库（Next.js + TypeScript + Supabase）中的角色语义、权限判断、页面准入、设置页可见性、模板/企业配置接入边界

关联文档：
- `docs/product-architecture-principles-v1.md`
- `docs/sales-primitives-model-v1.md`
- `docs/enterprise-customization-framework-v1.md`

---

## 1. 当前角色模型现状

当前仓库存在两套角色语义并行：

1. 展示角色（UI 角色）
- 定义位置：`types/auth.ts`
- 当前枚举：`sales | manager`
- 作用：前端通用页面展示、旧导航准入兼容

2. 组织权限角色（Membership 角色）
- 定义位置：`types/productization.ts`
- 当前枚举：`owner | admin | manager | sales | viewer`
- 作用：组织级权限与设置写操作（尤其是 settings / templates / team / ai / onboarding / executive）

服务端与客户端历史上做过“压缩映射”（owner/admin/manager -> manager，其他 -> sales），这保证了旧流程可运行，但也导致权限表达过于粗粒度。

---

## 2. 当前不一致点与风险

1. 语义压缩风险
- `User.role` 只有 `sales/manager`，不足以表达 owner/admin 与 manager 的差异。

2. 判断分散风险
- 代码中存在大量散落判断：`user.role === "manager"`、`isManager(profile)`。
- 影响：模板管理、企业配置、使用量可见性等边界容易漂移。

3. 页面准入与功能写权限混淆
- 某些页面可进入，但写操作由 API 再拦截。
- 用户侧体验是“进得去但做不了”，边界不清晰。

4. 文档与实现容易错位
- 文档说 owner/admin 有更高权限，但 UI 层若只看 `user.role`，无法稳定体现这一点。

---

## 3. 推荐的最小统一方案

目标：不推翻现有架构，不做 ACL 大重构，先统一“角色来源”和“能力判断入口”。

方案：

1. 保留展示角色兼容
- 继续保留 `User.role: sales | manager`，避免大面积破坏现有页面逻辑。

2. 在 User 增加组织角色上下文
- 新增 `User.orgRole?: OrgMemberRole`
- 新增 `User.orgSeatStatus?: OrgSeatStatus`
- 来源：`auth-service` 读取 `org_memberships`，无 membership 时回退到 profile role 映射。

3. 引入统一能力 helper（单一入口）
- 新增：`lib/role-capability.ts`
- 统一函数：
  - `canAccessExecutive()`
  - `canManageTemplates()`
  - `canManageOrgCustomization()`
  - `canViewManagerWorkspace()`
  - `canViewOrgUsage()`
  - `isOrgAdminLike()`
- 底层角色规则集中在：`lib/org-membership-utils.ts`

4. 高风险页面与 API 优先替换
- `executive`、`settings hub`、`settings/usage`、`settings/templates`、`reports`、`(app)/layout` 入口优先改为 helper。
- `reports` 相关 API 改为基于 helper 的统一判定，不再直接依赖 `isManager(profile)`。

---

## 4. “展示角色”与“组织权限角色”的关系

| 维度 | 展示角色（User.role） | 组织权限角色（orgRole） |
|---|---|---|
| 枚举 | `sales` / `manager` | `owner` / `admin` / `manager` / `sales` / `viewer` |
| 主要用途 | 旧页面兼容、基础 UI 分流 | 权限边界、设置写操作、管理与高权限功能 |
| 数据来源 | profile + membership 压缩映射 | `org_memberships.role`（优先） |
| 精度 | 粗粒度 | 细粒度 |
| 未来方向 | 兼容层 | 主权限语义层 |

---

## 5. 页面准入、功能可见、操作权限、数据可见范围的区分

1. 页面准入（Page Access）
- 是否能进入页面路由（例如 `/executive`、`/settings/usage`）。
- 统一由 `canAccessPathForUser()` + capability helper 决定。

2. 功能可见（Feature Visibility）
- 页面内是否展示某模块/入口（例如 settings 卡片是否显示）。

3. 操作权限（Action Permission）
- 是否允许执行写操作（如模板应用、企业配置变更、团队成员角色变更）。
- 通常 owner/admin 才有。

4. 数据可见范围（Data Scope）
- 个人/团队/组织范围的数据读取与生成（例如 reports 的 self/team scope）。

---

## 6. 角色能力边界建议（owner/admin/manager/sales/viewer）

### 角色矩阵表

| 角色 | manager 工作区 | executive | org usage | 模板管理（写） | 企业配置（写） | 备注 |
|---|---|---|---|---|---|---|
| owner | 是 | 是 | 是 | 是 | 是 | 组织最高权限 |
| admin | 是 | 是 | 是 | 是 | 是 | 组织管理员权限 |
| manager | 是 | 是 | 是 | 否（可读） | 否（可读） | 管理运营权限，不含组织治理写权限 |
| sales | 否 | 否 | 否 | 否 | 否 | 个人执行范围 |
| viewer | 否 | 否 | 否 | 否 | 否 | 当前按低权限只读角色处理 |

### 权限能力矩阵表（helper 对应）

| helper | owner | admin | manager | sales | viewer |
|---|---|---|---|---|---|
| `isOrgAdminLike` | true | true | false | false | false |
| `canViewManagerWorkspace` | true | true | true | false | false |
| `canAccessExecutive` | true | true | true | false | false |
| `canViewOrgUsage` | true | true | true | false | false |
| `canManageTemplates` | true | true | false | false | false |
| `canManageOrgCustomization` | true | true | false | false | false |

---

## 7. 模板、企业配置、feature flags、onboarding、executive、reports 如何消费权限能力

1. templates
- 页面可见：`canViewManagerWorkspace`
- 写操作（apply/override）：`canManageTemplates`

2. enterprise customization（后续设置入口）
- 页面可见：`canViewManagerWorkspace`
- 写操作：`canManageOrgCustomization`

3. feature flags / org settings / ai / team / automation
- 页面可见：`canViewManagerWorkspace`
- 写操作：owner/admin（API 继续强校验）

4. onboarding
- 查看：`canViewManagerWorkspace`
- 执行高权限工具（run/demo seed）：owner/admin

5. executive
- 页面准入：`canAccessExecutive`

6. reports
- 团队 scope：`canViewManagerWorkspace`
- 个人 scope：全员（受自身权限边界约束）

---

## 8. 由 helper 统一判断的能力（避免散落 if/else）

统一入口：
- 组织角色规则：`lib/org-membership-utils.ts`
- 用户能力映射：`lib/role-capability.ts`
- 路由准入：`lib/auth.ts` 中 `canAccessPathForUser()`

原则：
- 业务代码不再把 `user.role === "manager"` 当作高权限唯一判定。
- 先 resolve effective org role，再做 capability 判定。

---

## 9. 当前代码映射表（旧判断与本次收敛）

| 文件 | 现状 | 处理状态 |
|---|---|---|
| `app/(app)/executive/page.tsx` | 直接 `user.role === "manager"` | 已改为 `canAccessExecutive(user)` |
| `app/(app)/settings/page.tsx` | settings 卡片无细粒度能力控制 | 已改为 capability 过滤可见性 |
| `app/(app)/settings/usage/page.tsx` | 页面内无显式能力前置判断 | 已加 `canViewOrgUsage(user)` |
| `app/(app)/settings/templates/page.tsx` | 写权限由 `state.role` 局部判断 | 已接入 helper（读写分离） |
| `app/(app)/reports/page.tsx` | 多处 `user.role === "manager"` | 已改为 `canViewManagerWorkspace(user)` |
| `app/(app)/layout.tsx` | 仅 `canAccessPath(user.role, path)` | 已改为 `canAccessPathForUser(user, path)` |
| `app/api/reports/generate/route.ts` | `isManager(auth.profile)` | 已改为 helper（带 membership role） |
| `app/api/reports/coaching-generate/route.ts` | `isManager(auth.profile)` | 已改为 helper（带 membership role） |
| `app/api/*` 其他历史路由 | 仍有大量 `isManager(profile)` | 本次未全量替换，列入后续迁移 |

---

## 10. 迁移建议表

| 阶段 | 范围 | 动作 | 风险控制 |
|---|---|---|---|
| Phase 1（本次） | 高风险入口 | 建立 helper，覆盖 executive/settings/reports 准入与关键 API | 保留 `User.role` 兼容，避免主流程中断 |
| Phase 2 | manager/ai/capture/deals 等 API | 将 `isManager(profile)` 逐步迁移到 capability helper | 每批次补回归测试，避免权限回归 |
| Phase 3 | 领域服务层 | 收敛服务层角色判断，减少页面和 API 的重复逻辑 | 提供 service 级 capability 参数对象 |
| Phase 4 | 权限可观察性 | 增加权限拒绝日志与权限快照调试信息 | 便于线上排查“看不到/不能操作”问题 |

---

## 11. 后续演进建议

1. 逐步淘汰“manager 承担所有高权限语义”
- 将“团队运营权限”和“组织治理写权限”稳定分离。

2. capability-first 代码规范
- 新增页面/API 默认先写 capability 判定，不再写裸 `role` 比较。

3. viewer 角色边界细化
- 当前 viewer 作为低权限角色处理；后续需定义更细只读页面集合。

4. 文档与代码同步机制
- 权限变更必须同步更新 README 与模型文档，避免“实现已改、文档未改”。

5. 为模板/企业配置预留稳定授权接口
- 模板层消费 `canManageTemplates`，企业配置消费 `canManageOrgCustomization`，保持边界清晰。

