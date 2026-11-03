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

### 批次 1（流水线转绿）—— 已验收

| 提交 | 内容 |
|---|---|
| `57f0429` | 注册表配置校验改 `safe_load_all`，逐文档解析 ingress/networkpolicy 等 k8s 清单 |
| `ce1b14c` | 4 条 PG 用例：用例内独立 engine + 显式 dispose；usage 用例唯一 `event_id` |
| `f37d091` | e2e `/sso/session` 路由替身（恒 401，断言口径不变） |
| `585d35f` | 落盘 11-02 第四轮审计 + round3 归档（报告自身可溯源） |
| `e122537` | 落盘 11-03 第五轮审计与本整改方案 |

**CI 同 SHA 双绿（`e122537`）**：

| workflow | run | 结论 |
|---|---|---|
| CI Adapter | [41609501419](https://github.com/kHuner9712/MOY/actions/runs/41609501419) | **success**（注册表校验；Lint/类型/测试 0 failed） |
| CI Web | [41609507565](https://github.com/kHuner9712/MOY/actions/runs/41609507565) | **success**（Prettier/术语门禁/Lint/构建/E2E/产物扫描全绿） |

### 批次 2（中危 QA-02~QA-06）—— 已完成

| 提交 | 内容 | 证据 |
|---|---|---|
| `3c91218` | QA-02：ConfigMap 移除 4 个无消费方的 `OA_WEB_*` 运行期键；README §5.7 写明 build-arg 口径 | `validate_config_names` / `validate_deploy` 通过 |
| `d616022` | QA-03：`list_queued(limit=200)` 下推 `LIMIT`；`next_queued` 取 1；内存计数独立 | 内存用例 `test_list_queued_respects_scan_limit_without_breaking_count`；PG 用例 `test_list_queued_respects_scan_limit`（`--run-pg`） |
| `8f02a3b` | QA-04：`BillingStore/OpsStore.page_tenants`（PG `LIMIT/OFFSET` + `COUNT(*)`），OPS 服务直接下推 | `test_tenant_list_paginates_without_full_scan`；PG `test_page_tenants_pushes_down_pagination_and_count` |
| `41b0e2b` | QA-05：`add_artifacts` 按租户分组、**同一事务**批量写入，保持入参顺序 | PG 用例 `test_artifact_add_batch_is_single_tenant_roundtrip` |
| `0538a95` | QA-06：ZIP 构建改 `SpooledTemporaryFile`（>8MB 溢写磁盘），峰值由"两份"降到"一份" | 用例 `test_export_spools_zip_beyond_memory_cap`（把上限压到 1 字节强制 rollover） |

### 批次 3（其余中/低危）—— 已完成 / 已登记

| 提交 | 内容 |
|---|---|
| `51af723` | QA-13/14：workbench 6 组件补 19 条用例；实测 **89.78/85.06/84.93/90.98**，阈值抬到 **88.8/84.0/83.9/90.0** |
| `9cb6140` | QA-07/19：`OA_BATCH` 默认 `B21`（与 CI 同值），裸跑 0 failed；README 运行口径表写明 `--run-pg` 四前置条件与「238 skipped ≠ 通过」 |
| `94df114` | QA-11/18：根目录 `LICENSE`（私有保留权利，与 `UNLICENSED` 一致）；Dependency Scan 把 UNKNOWN 许可证显式列入灰名单，基线 42 / 上限 45 |
| `277c042` | 审计报告第 10 节整改状态列 + 本执行记录 + 分支收口附录 |
| `0975a9d` | 修正 `tests/expected_xfail.py` 的多余空行（CI Adapter 在 `277c042` 上 ruff format 报红后定位修复） |

**门禁结果（本地，批次 2+3 合并验收）**：

| 门禁 | 结果 |
|---|---|
| 后端 `pytest tests`（无 PG，裸跑） | **0 failed / 2535 passed / 238 skipped**（PG 用例按设计跳过） |
| 后端 `ruff check app tests` / `mypy app` | 通过 |
| 前端 `vitest run --coverage` | 72 文件全绿；89.78/85.06/84.93/90.98（阈值 88.8/84/83.9/90） |
| 前端 `tsc --noEmit` / `eslint` / `prettier --check` | 通过 |
| `deploy/validate_*.py` 16 项 | 全部 EXIT=0（`validate_batch_clock.py` 需 `OA_BATCH=B21`，与 CI 一致） |

**CI 同 SHA 双绿（批次 2/3 代码基线 `0975a9d`）**：

| workflow | run | 触发 | 结论 |
|---|---|---|---|
| CI Adapter | [41617426516](https://github.com/kHuner9712/MOY/actions/runs/41617426516) | push | **success**（ruff/mypy + PG 容器全量 pytest + 覆盖率下限） |
| CI Web | [41618757526](https://github.com/kHuner9712/MOY/actions/runs/41618757526) | workflow_dispatch（同 SHA） | **success**（Prettier/术语门禁/Lint/tsc/vitest/构建/E2E/产物扫描） |
| Dependency Scan | 41617426595 | push | success |

- 首次推送 `277c042` 时 CI Adapter 在 `ruff format --check` 报红：`tests/expected_xfail.py` 多一个空行（CI 的 ruff 退出码 1，本地此前只按目录抽查未覆盖到）。定位后由 `0975a9d` 修复并复跑，**不是以「已修复」结案，而是拿到新的同 SHA 双绿**。
- CI Web 的 `on.push.paths` 不含 `apps/adapter/tests/**`，因此 `0975a9d` 不会自动触发；为满足「同一 SHA 双绿」，用 `gh workflow run ci-web.yml --ref main` 在 `0975a9d` 上手动触发并验收。

### 未闭环/阻塞项（不假装已确认）

| 编号 | 状态 | 说明 / 需要谁 |
|---|---|---|
| QA-12 分支保护 | **阻塞（平台套餐，非权限）** | 见下方「QA-12 403 成因取证」。结论：当前账号对仓库是 `ADMIN`、令牌含 `repo`，**不是令牌或角色不足**；403 原文为 `Upgrade to GitHub Pro or make this repository public to enable this feature.`，私有仓库在 Free 计划不支持分支保护/rulesets。已试 `GET protection`、`GET branches/main`、`GET rulesets`，三者口径一致（前两者 403，分支 `protected=false`）。**需用户本人处置**：升级 GitHub Pro/Team，或把仓库改为 public，或在网页端开通；免费替代仅有「CI 硬失败 + 流程约束」，无法阻止直接 push |
| QA-15 npm moderate | **已评估，不升** | 16 个 moderate 全部来自同一条 `minimatch < 11.0.1`（GHSA-g3hv-75wg-m249），传播链只经过 eslint/`@typescript-eslint`/`eslint-config-next` 等**开发依赖**；`eslint@10.14.0`（当前最新）仍依赖 `minimatch ^10.2.6`，`npm audit fix` 给出的 `eslint@4` 是 registry 误报。风险面：仅 lint 时对仓库内固定 glob 求值，不接受用户输入。处置：不降级、不强制 override 到 11（会破坏 eslint 内部 API）；保留 `npm audit --audit-level=high` 门禁，moderate 不阻断 |
| QA-06 残留 | **口径已写明** | `plugin_artifact.content` 是 `bytea`，最终写库仍需一份完整 `bytes`；端到端流式需把产物二进制迁到对象存储并改仓储契约，属独立存储变更 |
| QA-16/17/20/21/22 | **部分闭环 / 登记** | 16 digest pin 已闭环；README 中与本次相关的 `72→68` 已改；`'use client'` 89/93 未动（属重构级）；`.env.example` 弱默认仍由部署门禁阻断；包体预算未做（无基线） |

### QA-12 403 成因取证（2026-11-03 复核）

**结论：不是令牌权限问题，也不是账号角色问题；是「私有仓库 + GitHub Free 计划」不支持分支保护/rulesets。**

| 取证项 | 命令 | 返回原文/要点 |
|---|---|---|
| 登录态与 scopes | `gh auth status` | `Logged in to github.com account kHuner9712 (keyring)`；`Token: gho_****`（gh OAuth 登录态，非 classic/fine-grained PAT）；`Token scopes: 'gist', 'read:org', 'repo'` |
| 可见性/角色 | `gh repo view kHuner9712/MOY --json visibility,isPrivate,viewerPermission,defaultBranchRef,owner` | `"visibility":"PRIVATE"`、`"isPrivate":true`、`"viewerPermission":"ADMIN"`、`"defaultBranchRef":{"name":"main"}`、`"owner":{"login":"kHuner9712"}` |
| 仓库 permissions | `gh api /repos/kHuner9712/MOY` | `"permissions":{"admin":true,"maintain":true,"pull":true,"push":true,"triage":true}`、`"private":true` |
| 分支保护读 | `gh api /repos/kHuner9712/MOY/branches/main/protection` | `{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","documentation_url":"https://docs.github.com/rest/branches/branch-protection#get-branch-protection","status":"403"}` |
| 分支状态 | `gh api /repos/kHuner9712/MOY/branches/main` | `{"name":"main","protected":false}` |
| rulesets 交叉验证 | `gh api /repos/kHuner9712/MOY/rulesets` | `{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","documentation_url":"https://docs.github.com/rest/repos/rules#get-all-repository-rulesets","status":"403"}` |

- 两个独立端点（branch protection / rulesets）返回**同一条**套餐提示，排除「调用方式不对（缺 PUT body）」或「token 缺 scope」的解释：本账号是该私有仓库的 `ADMIN`，`repo` scope 已具备管理写入能力，若套餐支持即可直接 `PUT`。
- 因套餐不支持，**未尝试写入**（写入同样会被 403 拒绝），也未改动任何仓库设置/分支/stash。
- 免费可行替代：CI 已把 16 道门禁与 `CI Adapter`/`CI Web`/`Dependency Scan` 做成硬失败，但 Free 私有仓库无法把「必需检查」绑到合并动作上，**仍不能阻止直接 push 到 `main`**；要真正强制 PR + 必需检查，只能由用户完成下方任一动作。

**用户必须亲自完成的最小动作（当前可选两项，选项 3 不可用）**：

1. **升级 GitHub Pro**（个人账号，私有仓库即可用分支保护/rulesets）：GitHub → Settings → Billing → Upgrade；完成后我可用 `PUT` 写保护并 `GET` 回读验证。
2. **把仓库转为 public**（Free 计划下公开仓库支持分支保护）：`gh repo edit kHuner9712/MOY --visibility public --accept-visibility-change-consequences`（会公开全部代码与历史，需你确认后再执行）。
3. 若已有 Team 计划（组织）：把仓库迁到该组织下，或用组织账号在 Settings → Rules 中配置 ruleset。

补充：`gh api /user/orgs` 实测当前账号**不属于任何组织**（空列表），因此选项 3 目前不可用。

## 附录：分支收口取证（2026-11-03 执行时实测）

```
$ git remote -v
origin  https://github.com/kHuner9712/MOY.git (fetch)
origin  https://github.com/kHuner9712/MOY.git (push)

$ git branch -a
* main
  remotes/origin/HEAD -> origin/main
  remotes/origin/main

$ git ls-remote --heads origin
e12253789aecdd304102ebfb82588a8740d8d510  refs/heads/main
```

推送批次 2/3 后复验（`0975a9d` 时点）：

```
$ git ls-remote --heads origin
0975a9d0b2931c790782ab4ab07b23c155bac2af  refs/heads/main

$ git branch -a
* main
  remotes/origin/HEAD -> origin/main
  remotes/origin/main
```

- **本地与远端均只有 `main`**（本轮开始时远端为 `e122537`；收口阶段实测无其它本地/远端分支）。
  审计报告 §1.1 的「无 origin」是上一轮取证口径错误，本轮以 `git remote -v` 实测为准并已修正。
- 需删除的分支：**0 个**；未合并实质工作：**0 条**（不存在非 main 分支）。
- `git branch --merged main` / `--no-merged main` 在仅有 main 时无输出差异，不另列 SHA。
- 后续只有文档/代码提交前移 `main`，不新增分支；最终以 `git ls-remote --heads origin` 复核（见最终汇报）。

### 保留的 3 条 stash（不 apply、不 drop）

| stash | 来源分支 | 说明 | SHA |
|---|---|---|---|
| `stash@{0}` | `refactor/wave3-deploy-lint` | B22-01 半成品；**SyntaxError:505 ruff errors，勿 apply** | 保留 |
| `stash@{1}` | `develop` | s2-wip | 保留 |
| `stash@{2}` | `develop` | pre-b20-dirty | 保留 |

对应分支已不存在（历史遗留），stash 仍可从 `refs/stash` 取回；按要求**保留原样**。

