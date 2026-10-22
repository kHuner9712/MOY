# 阶段三后端交接包（B16-B18 已完成 / B19-B21 待接手）

> 写于 B18 收尾时。作者：software-engineer-4（后端 B16-B21）。
> **状态**：B16/B17/B18 功能与门禁已落库；B19/B20/B21 未开始。本文件**未提交**，待主理人核过再定。
> 读法：先看 §1 现状表，再看 §2 约定，最后 §4 未决问题——§3 是踩过的坑，施工前务必过一遍。

## 1. 批次现状与冻结号

| 批次 | 冻结号 | 内容 | 状态 |
| --- | --- | --- | --- |
| B16 | `046c432` | 6 段声明模型、加载器（8 层校验）、6 份声明、13 个错误码、G-26 门禁 | 完成 |
| B17 | `010db32` | 9 表 DDL + RLS + 0003 迁移、双后端仓储、PL1-PL5/PL13、phase3 基线结构 | 完成 |
| B18 | `79f868c`（**归属事故**：内容落在 QA 的 docs 提交里，代码零丢失） | PL6-PL12、PL14、评价、`already_installed`、`org_capabilities`、PL13 六项回读 | 完成 |
| 0003 自足 | `b5dbd69` | `to_regclass` 守卫 + 幂等建表 + 建完对账 | 完成 |
| D3 | `c340e70` | `ErrorSpec.visibility`、G-13 内部码豁免 + 正向核对 | 完成 |

**门禁基线（接手人应以这组数为起点）**
`ruff`/`format`/`mypy` 全绿（107 files）；`pytest -q`（排除 QA 在编辑的骨架）= 1135 passed / 0 failed；
`test_g11_migration_idempotency.py --run-pg` 两遍各 **5 passed**；
`test_plugin_contract_skeleton.py` = **9 passed / 2 xfailed**（余 2 条是 WF2，B19 做）；
`check_error_catalog.py --strict` = **EXIT 0**；
`validate_openapi --allow-missing-phase2 --allow-missing-phase3` → `core=30 phase2=38/38 phase3=14/16`。

## 2. 现有接口与调用约定（B19 要接的东西）

**声明层**（`app/plugins/`）
- `get_registry()` → `PluginRegistry`：`get(plugin_id, version)`、`latest()`、`versions()`、`titles()`、`all_manifests()`；加载失败抛 `30004`（含 8 层校验原因）。
- `actions.RunContext`：`tenant_id / task_id / step_seq / plugin_id / installation_id / initiator_id / trace_id / granted(frozenset) / settings / inputs / outputs`；`ctx.needs([...])` 取前序产物。**只带身份与已授能力，不含任何凭据**。
- `actions.ActionGateway`（Protocol）：`async invoke(ctx, action, params) -> ActionResult`、`resolve(action) -> str`。实现类应落在 `services/action_gateway.py`，由 `api/v1/plugin_deps.py::build_action_gateway()` 注入（该函数已存在，内部延迟导入 `ServicesActionGateway`）。
- `ActionResult`：`status(ok|failed|skipped) / artifacts(tuple[ArtifactDraft]) / data / error_code / reason_text / operator_action / retryable / usage_units`。**失败只填业务语言 `reason_text` + `operator_action`，不填堆栈**。

**业务层**
- `PluginService`：`market/detail/installed/capabilities/install/set_state/uninstall/upgrade/rollback/set_settings/update_grants/set_capability_policy`；`ensure_versions()` 幂等登记声明快照。
- `PluginGrantService`：`snapshot(tenant, plugin, user)`（5s 缓存）/`grant(...)`/`assert_granted(...)`/`invalidate(...)`。
- `PluginReviewService`：`list_reviews(plugin_id, auth, page, page_size)` / `submit(...)`。
- 仓储：`plugin_repo.PluginStore`（catalog/version/installation/grant/policy 五张表）、`plugin_review_repo.ReviewStore`、`billing_repo`（套餐 `features.plugin_market`）。

**已落地的 DDL**（`deploy/init-schema-phase3.sql`，9 张表）：
`plugin_catalog` / `plugin_version`（不可变，`manifest_sha256`）/ `plugin_installation`（`version` + `previous_version` 双指针）/ `plugin_grant`（`revoked_at` 软失效）/ `plugin_capability_policy` / `plugin_review` / `plugin_task`（**已含 `plugin_version` 快照列**、`uq_task_idem`、`fk_task_of`）/ `plugin_task_step`（`uq_step_seq`）/ `plugin_artifact`（`ck_inline_size` 32KB）。

## 3. 踩过的坑（施工前必读）

1. **迁移自足 ≠ `IF NOT EXISTS`**：`CREATE TABLE IF NOT EXISTS` 在表已存在时**仍要求 schema CREATE 权限**（应用角色在 initdb 库上必报 `permission denied for schema public`）。正确做法是 `to_regclass(...)` 守卫 + `EXECUTE $ddl$ ... $ddl$`（0003 即此写法）。同理 `CREATE POLICY` 不能重复执行，必须先 `DROP POLICY IF EXISTS`。
2. **幂等空间**：任务创建与 `POST /.../confirm` 不能共用同一 `Idempotency-Key`（同一用户先"确认继续"再"重做"会因同键不同体被拒）——confirm 用独立命名空间 `confirm:{task_id}:{idem}`。
3. **`16010` 是僵尸码**：声明了但无端点引用。要落到「并发安装唯一键冲突」「`installation.version` 指针竞争」两处；**不要**把所有冲突都归给它。
4. **D2 六项回读**：PL13 必须回**全量 6 项**（个人能力恒 `org_switch_enabled=true`），PL1 顶层 `org_capabilities` 只放 3 个 `org.*`；两处 key 集合必须一致。
5. **G-13 内部码**：`17002` 标 `visibility="内部"`，前端 0 命中；`ErrorSpec.visibility` 是数据而非脚注。
6. **`git add` 后立刻 commit**：B18 就是因为我留了 4 分钟暂存窗口，被别人的提交带走。
7. **`17003` 不得存在**：架构未声明且语义不成立，若出现一律删（当前代码里没有）。

## 4. 未决问题（需主理人裁定或接手人决策）

1. **phase3 基线文件仍是 `baseline-phase3-14.json`**：内容已 14 条、常量已 16，**文件名与 `validate_openapi` 的 `phase3_path` 待改名到 `-16.json`**（B19 补 WF1/WF2 后收口）。
2. **对外码双向核对**：目前只做了「内部码不进前端」的正向检查；「每个对外码必须被 ≥1 个端点引用」（能抓 `16010` 这类僵尸码）尚未实现——建议在 `16010` 落地后一起加，否则一开就红。
3. **`org_repo`/`role_repo` 的 PG 分支覆盖率**仍 <90%（阶段二遗留），与插件域无关。
4. **`plugin_task.plugin_version`**：DDL 已有，B19 建任务时**必须写入**（历史任务按创建时版本渲染，PRD R2 对策）。
5. **A1 双模式**：`plugin_id` 为空时必须与阶段一**逐字等价**（30 端点回归全绿），这是 QA 的一票否决项。
