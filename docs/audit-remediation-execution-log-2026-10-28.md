# 审计修复执行记录与暂缓登记（2026-10-28）

- **执行人**：工程师 寇豆码
- **依据**：`docs/remediation-plan-2026-10-28.md`（29 条）
- **分支**：`feature/audit-remediation-2026-10-28`

## 一、暂缓条目登记（方案 §0.1 裁定「暂缓」，本轮只登记不实施）

| 编号 | 条目 | 暂缓理由（方案原文口径） | 前置条件 |
| --- | --- | --- | --- |
| ARC-06 | 整站 RSC 改造 | 大范围结构性重构，须等 QA-04 的 e2e 防线落地后再动（先有回归防线、再动结构）。QA-04 本轮已交付 `apps/web/e2e/workflow.spec.ts`（贯穿旅程），改造前的防线雏形已就位 | e2e 在 CI/真实栈跑稳一个迭代 |
| QA-08 | localStorage → httpOnly cookie | V1.0 已明示接受的权衡；迁移牵动 CSRF/跨域复杂度 | 下版本（V1.1）排期 |
| ENG-08（部分） | jose → pyjwt 评估 | 需上游活跃度调研，不阻塞本轮 | 上游调研完成后出评估结论 |

## 二、降级/部分完成条目

| 编号 | 状态 | 说明 |
| --- | --- | --- |
| H-2 | 依赖收敛完成，CI 改动待远端验证 | `uv lock` 落库、7 项依赖 `==` pin、`uv lock --check` 通过、`uv sync --frozen --extra dev` 本地全绿；`ci-adapter.yml` / `dependency-scan.yml` 的 uv 改动无法本地验证（需远端 CI 跑一次确认） |
| H-4 | 本地全绿，CI 恢复属用户操作 | 本地 `--run-pg` 全量门禁 2349 passed / 0 failed / 0 skipped，覆盖率 91.95%；真实密钥轮换与 CI 修复待用户（方案 §4 清单） |
| QA-02 | 代码侧完成，密钥轮换待用户 | 两个 .env 副本已删除（仓库外 7 天隔离备份：`C:/Users/15864/Desktop/麦子-audit-quarantine-2026-10-28/`）；`deploy/.env`（8165B）与 `deploy/.env.lite`（10435B）仍含真实密钥，**轮换清单见方案 §4，须用户执行** |

## 三、与方案的偏差记录

1. **无 develop 分支**：仓库只有 `main`，修复分支从 `main` 切出（任务书要求从 develop，现场不存在）。
2. **QA-03 的 .gitignore**：方案要求新建 `apps/adapter/.gitignore`——根 `.gitignore` 已覆盖全部目标模式（`coverage.xml` / `.coverage*` / `junit.xml` / `.pytest_cache` / `pytest_run.log`），按最小变更原则未新建冗余文件。
3. **ARC-02 valkey 限额**：全量栈 `--maxmemory` 默认 2gb（lite 为 256mb），直接抄 lite 的 384M 会被 OOM killer 先杀，改为 `3g` 并留注释。
4. **ARC-02 milvus 限额**：lite 栈无 milvus 条目可抄，按官方 standalone 建议 `2g` 预留并登记「无 lite 实测基线，上线后校准」。
5. **compose 全栈冒烟（ARC-01/03 验证步骤 3）未执行**：现场 `oa-*` 全栈正在运行且 `deploy/.env` 缺 4 个必需密钥（`OA_PLUGIN_DAEMON_KEY` 等），原地 `up` 会用错误环境重建生产栈。改为静态验证（`docker compose config` + `validate_deploy.py` 全绿），全栈重建冒烟列入用户操作清单。
6. **e2e 贯穿段未实跑**：`workflow.spec.ts` 贯穿旅程需要真实统一登录测试账号（现场资产），凭据缺失时自动 skip；匿名前置段 2 条本地实跑通过。
7. **第一 commit 附带文档**：`git add -A` 将未跟踪的 `docs/audit-2026-10-28.md`（审计初稿）一并带入首个 commit，无害保留。
8. **ENG-04 digest 时点**：python/node 构建镜像 digest 为 2026-10-28 Hub 实查值，已在 images.lock note 标注时点。
