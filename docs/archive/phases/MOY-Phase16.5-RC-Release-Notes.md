# MOY Phase 16.5 Release Candidate Notes

发布日期：2026-03-16  
版本标签：`v0.16.5-rc`

## 1. 版本定位

Phase 16.5 作为发布候选版本（Release Candidate），目标不是新增大功能，而是为后续 Phase 17 提供稳定基线：

- 清理关键风险（P0/P1）
- 固化交付流程
- 提升仓库可维护性与可验证性

## 2. 本次交付内容

### 2.1 发布流程增强

- 新增 GitHub Actions CI 流程（`lint + build`）
- 分支推送与 PR 会自动校验基础质量

对应文件：

- `.github/workflows/ci.yml`

### 2.2 发布文档补充

- 新增本文件，用于记录 RC 目标、范围、验证与发布建议

对应文件：

- `docs/MOY-Phase16.5-RC-Release-Notes.md`

## 3. 建议验证清单（RC）

在 GitHub 与本地建议至少完成以下检查：

1. `npm run lint` 通过
2. `npm run build` 通过
3. GitHub Actions 的 `CI` 工作流在 `main` 分支首跑通过
4. 核心页面可访问：
   - `/today`
   - `/briefings`
   - `/deals`
   - `/touchpoints`
   - `/manager/executive`（若路由为 `/executive` 则检查对应入口）
5. 关键权限链路可用：
   - owner/admin 可访问设置与自动化页面
   - manager 可访问管理视图与经营驾驶舱
   - sales 访问范围受限且不报错

## 4. 发布标签建议

- 推荐将当前提交打标签：`v0.16.5-rc`
- 验证稳定后再发布正式标签（例如 `v0.16.5`）

## 5. 已知范围说明

Phase 16.5 重点是稳定性与发布质量，不包含 Phase 17 新功能开发。
