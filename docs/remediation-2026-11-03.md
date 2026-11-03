# 整改方案（2026-11-03 审计）

依据：`docs/audit-2026-11-03-full-repo-audit.md`（QA-01~QA-22）。
原则：先方案后动手；每批独立提交、全量门禁通过；CI 以同一 SHA 双绿为准；不静默跳过阻塞项。

## 批次与优先级

### 批次 1：流水线转绿（阻断项）

| 编号 | 问题 | 动作 | 验收口径 |
|---|---|---|---|
| QA-01 | CI Adapter/Web 在 HEAD 全红，修复未提交 | 拆 3 个提交：① 注册表校验 `safe_load_all`；② 4 条 PG 用例事件循环/主键隔离；③ e2e `/sso/session` 路由替身 | 推送同一 SHA 触发两条 workflow：CI Adapter `conclusion=success`（0 failed，预期 2767 passed）；CI Web `conclusion=success`（e2e 0 failed）；记录 run 编号 |
| 文档 | 11-02 报告与 round3 归档未提交 | 单独提交两份文档 | `git status` 不再出现该报告/归档的未提交项 |

### 批次 2：中危（QA-02~QA-06）

| 编号 | 问题 | 动作 | 验收口径 |
|---|---|---|---|
| QA-02 | k8s ConfigMap 的 `OA_WEB_*` 与 Next 构建期 `NEXT_PUBLIC_*` 错位 | 采用"构建期语义"：清单与 README 明确公共地址在镜像构建时注入；删除/标注无消费方的运行期键，保证 validate_config_names 自洽 | k8s README 可按文档产出可用镜像；`validate_config_names.py` 通过；ConfigMap 无"看似可改、实际无效"的键 |
| QA-03 | `list_queued` 无 LIMIT / 全列读取 | 增加 `limit` 参数（默认有界）并下推 `LIMIT`；调用方按批大小传参；列裁剪 | 新增仓储用例验证 limit；积压场景 SQL 只返回 limit 行 |
| QA-04 | ops 租户列表全量拉取 + 内存切片 | 仓储新增分页方法（`limit/offset` + `count`），服务层直接下推 | 分页用例：第 N 页只取 page_size 行；内存不随租户总量增长 |
| QA-05 | `add_artifacts` 逐条记录独立事务 | 单事务批量写入（批次上限），失败整体回滚 | 用例断言 1 个事务；批次超过上限被拒绝/切分 |
| QA-06 | 租户导出全量 `BytesIO` | 改为临时文件/流式写出，限制单次导出行数 | 导出用例：内存峰值不随数据量线性增长；接口契约不变 |

### 批次 3：其余中/低危

| 编号 | 问题 | 动作 | 验收口径 |
|---|---|---|---|
| QA-13/14 | 前端行覆盖余量 +0.96pp；6 个 workbench 组件 0% | 补 6 个组件交互用例，行覆盖目标 ≥89 并抬棘轮 | `npm run test -- --coverage` 行 ≥89；阈值同步上调 |
| QA-07/19 | 235 条 PG 用例本地跳过；裸跑缺 `OA_BATCH` 失败 | 明确 `--run-pg` 文档；为 `OA_BATCH` 提供自洽默认 | 裸跑 `pytest tests` 0 failed；README/Makefile 写明 PG 门槛 |
| QA-11 | 无 LICENSE；后端 42/53 许可证元数据缺失 | 补 `LICENSE`（私有分发保留权利声明，待所有者确认）；新增许可证采集脚本与门禁 | 根目录有 LICENSE；采集输出覆盖 53 包并标注 UNKNOWN 数量；门禁进 CI |
| QA-15 | npm 16 个 moderate（开发链） | 尝试升级；不能升则记录理由与基线 | `npm audit` 结果记录在文档；升级后 lint/tsc/test 全过 |
| QA-12 | 分支保护 API 403 | 记录阻塞与所需权限，不假装已确认 | 文档写明所需套餐/权限与影响 |
| QA-16/17/18/20/21/22 | digest、README 数字、stash、use client/大文件、资源上限、包体预算 | 能低成本闭环的做；其余登记为待办 | 文档状态列更新 |

### 分支收口

- 实测：`git remote -v` = `origin https://github.com/kHuner9712/MOY.git`；`git branch -a` 仅 `main`；`git ls-remote --heads origin` 仅 `refs/heads/main`。**本地与远端已只有 main，无需删除分支。**
- 3 条 stash 保留、不 apply、不 drop：

| stash | 来源分支 | 说明 |
|---|---|---|
| `stash@{0}` | refactor/wave3-deploy-lint | B22-01 半成品，SyntaxError:505 ruff errors，勿 apply |
| `stash@{1}` | develop | s2-wip |
| `stash@{2}` | develop | pre-b20-dirty |

## 执行记录

（随批次完成更新：提交 SHA、CI run 编号、门禁结果、遗留阻塞。）
