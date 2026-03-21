# Docs Alignment Summary v1

更新时间：2026-03-22

关联文档：
- `README.md`
- `docs/README.md`
- `docs/github-publish-summary-v1.md`

## 1. 本次 README / docs 对齐了哪些内容

本次对齐聚焦“GitHub 首页叙事”和“文档入口导航”一致性，完成了以下收敛：

1. README 项目定位从“功能清单叠加”改为“销售推进系统 + 配置治理平台能力”。
2. README 明确 MOY 不是通用聊天工具，强调模板、企业定制、权限、审计、回滚、运维入口。
3. README 增补 settings/config/runtime 相关页面入口，覆盖 `templates / org-config / runtime-debug / config-ops / config-timeline`。
4. README 明确测试入口：`npm run test -> tests/run-tests.ts`。
5. README 增补“当前限制”与“阶段说明”，避免夸大。
6. `docs/README.md` 重构为分层索引：must-read、recommended reading order、active/docs archive 边界、governance chain、settings surfaces、operations surfaces。
7. `docs/README.md` 明确 archive 仅历史参考，不作为当前实现主依据。

## 2. README 现在反映的当前能力

README 已明确表达以下已落地能力：

- 核心销售工作台与管理视角
- 行业模板与企业定制框架
- 角色与权限模型
- runtime explain/debug
- override hardening 与写路径治理
- persisted audit 与版本基础
- optimistic concurrency guard
- rollback preview + guarded execution（分域最小闭环）
- org config editor
- config operations hub
- config timeline & diff viewer（摘要级）

## 3. 已实现但有限制的能力

以下能力已实现，但 README 已标注其限制边界：

1. Diff 查看：当前是结构化摘要 diff，不是字段级 rich diff。
2. 回滚：当前为分域最小闭环，不支持批量与跨类型事务回滚。
3. 冲突处理：当前是 expectedVersion 漂移拒绝 + 刷新重试，不含自动 merge。
4. 配置中心：当前是分页面最小可用入口，不是完整统一配置中心。
5. 时间线：当前是最近窗口摘要，不是完整历史检索与回放系统。

## 4. 当前仍未实现的能力

1. 字段级 rich diff（可视化逐项）
2. 批量回滚
3. 自动 merge 冲突处理
4. 跨配置类型事务回滚
5. 高级时间线筛选与统一历史回放

## 5. 后续文档治理建议（避免 README 再次落后）

1. 设立“文档同步门禁”：涉及 settings/config/runtime/governance 的功能变更，PR 同步更新 README + docs/README。
2. 每次发布前执行一次“README capability checklist”核对：页面入口、权限边界、测试入口、限制项是否仍准确。
3. 在 `docs/README.md` 维持 active/archive 双层标记，新增文档默认标注 `status: active` 或 `status: archive`。
4. 将 `docs/github-publish-summary-v1.md` 作为发布叙事源，README 仅保留稳定能力摘要，不堆叠阶段性细节。
5. 对“已实现但有限制”能力保持显式清单，避免后续叙事漂移成“已完全完成”。
