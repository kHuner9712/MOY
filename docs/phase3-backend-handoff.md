# 阶段三后端交接包 v2（B16-B19 已完成 / B20-B21 待接手）

> v2 写于 B19 收尾（作者：backend-p3-b，接手 B18 收尾 + B19）。
> **状态**：B16/B17/B18/B19 全部落库并通过门禁；B20/B21 未开始。
> 读法：§1 冻结号 → §2 现有接口 → §3 **偏离与理由（必读）** → §4 **两条教训** →
> §5 B20 落点 → §6 未决事项。§3/§4 是这份文档价值最高的部分。

## 1. 批次现状与冻结号

| 批次 | 冻结号 | 内容 | 状态 |
| --- | --- | --- | --- |
| B16 | `046c432` | 6 段声明模型、加载器（8 层校验）、6 份声明、错误码登记、G-26 门禁 | 完成 |
| B17 | `010db32` | 9 表 DDL + RLS + `0003` 迁移、双后端仓储、PL1-PL5/PL13 | 完成 |
| B18 | `79f868c` | PL6-PL12、PL14、评价（内容落进 QA 的 docs 提交，代码零丢失） | 完成 |
| 0003 自足 | `b5dbd69` | `to_regclass` 守卫 + 幂等建表 + 建完对账 | 完成 |
| D3 | `c340e70` | `ErrorSpec.visibility`、G-13 内部码豁免 + 正向核对 | 完成 |
| B18 收尾 | `ed3bb09` | 基线改名收口、码表**双向核对**、`16010` 两处、G-26 假隔离、hr 能力收敛 | 完成 |
| **B19** | **`b6effcf`** | 任务引擎 + A 域 4 处加性扩展 + WF1 + WF2，**phase3=16/16** | 完成 |
| B19c | `ae69a4a` | 4 份单测（41 条）+ 修「推进静默不执行」缺陷 + 下载代理说明 | 完成 |
| 0004 修复 | `b75f0ab` | `END $guard$` 标签闭合（原 `END $$` 导致空库迁移语法失败） | 完成 |
| 孤儿治理 | `dd28d13` | 台账 `orphan`/`alias` 可见化 + `--prune-orphans` | 完成 |

**门禁基线（接手人以此为零点）**
`ruff check` / `ruff format --check` 0；`mypy app` = 116 files / 0 error；
`pytest tests/unit` = **744 passed**；骨架 = **11 passed / 0 xfailed / 0 skipped**；
`check_error_catalog.py --strict` = **EXIT 0**（阶段三对外码 12/12 被引用，内部码 0 引用）；
`validate_openapi.py`（**不带** `--allow-missing-phase3`）= `core=30 phase2=38/38 phase3=16/16`，G1-G5 全过；
`validate_plugins.py` 0、`--self-test` 1（打印绝对 `manifests_dir`）。

## 2. 现有接口与调用约定（B20 要接的东西）

**声明层** `app/plugins/`
- `loader.get_registry()` → `PluginRegistry`：`get(plugin_id, version)` / `latest()` / `titles()`；加载失败抛 `30004`。
- `actions.RunContext`：`tenant_id / task_id / step_seq / plugin_id / installation_id / initiator_id / trace_id / granted / settings / inputs / outputs / **auth**`。**只带身份与已授能力，零凭据**；`ctx.needs([...])` 取前序产物。`auth` 是 B19 新增（`AuthContext`），用于以发起人身份调 M1/R7/T5。
- `actions.ActionGateway`（Protocol）：`async invoke(ctx, action, params) -> ActionResult`、`resolve(action) -> str`。实现类 `services/action_gateway.py::ServicesActionGateway(settings)`（**类名与构造签名被 `plugin_deps.build_action_gateway()` 硬编码，别改名**）。
- `engine.PluginEngine(gateway, sanitizer, retry_backoff_ms=)`：`preflight()` / `plan()` / `run_step()` / `degrade()` / `progress()` / `checklist()`；**无 DB 依赖**。
- `sanitizer.ArtifactSanitizer`：`sanitize()` / `mask_pii()` / `assert_no_secret()` / `render_file()` / `digest()` / `zip_bundle()`。

**业务层**
- `PluginService`：市场/安装/启停/版本/授权/开关 + **`prepare_task()`**（A1 与 WF1 共用的准入检查：`15001→16002→16003→16004→16009`）。
- `PluginGrantService.snapshot()`（5s 缓存）；`PluginReviewService`。
- **`TaskService`**（`business/task_service.py`）：`create_plugin_task()` / `retry()` / `cancel()` / `get_task()` / `list_tasks()` / `steps()` / `edit_artifact()`；私有 `_advance_record()` 是**唯一推进入口**（换调度只改这一处）。

**仓储**（内存 + PG 双实现，签名一致）
`plugin_repo.PluginStore`（catalog/version/installation/grant/policy，含 `create_installation` / `swap_version` 的并发语义）、
`task_repo.TaskStore`（`plugin_task` + `plugin_task_step`）、
`artifact_repo.ArtifactStore`（`plugin_artifact` + `version` / `version_history`）。

**已落地 DDL**：`deploy/init-schema-phase3.sql`（9 表）+ `deploy/migrations/0003`（建表自足）+ `0004`（B19 增量列：`plugin_task.confirmed_step_seq`、`plugin_artifact.version` / `version_history`）。

## 3. 五条偏离与理由（**已获主理人逐条批准，不要"改回文档写法"**）

| # | 偏离 | 理由 |
| --- | --- | --- |
| 1 | 产物下载代理 `GET /files/artifacts/{token}` **不挂 `/api/v1`** | 它是**内容代理**（无 JSON 信封、无业务语义、不参与版本化契约），挂进去会破坏「phase3=16」这个对 QA 与客户的承诺。令牌自带 tenant+artifact+version 并 HMAC 签名，故无需登录态。已在 `docs/api/README.md` 登记为"非业务端点"。 |
| 2 | 任务推进在**请求内同步执行**（不用 `asyncio.create_task`） | 单副本 + 步骤毫秒级；`TestClient`/同步调用下后台任务会"永远 running"，是典型假象。多副本换 Valkey 时只改 `_advance_record` 的调度，状态机不动。 |
| 3 | 并发额度 = `running` + **`waiting_confirm`** | 等待确认的任务仍占着这次执行；不计入会让"确认后重新排队"，用户感觉"点了继续反而更慢"，且排队用例无法确定性构造。 |
| 4 | B19 **单批验收**（不拆 a/b） | 拆开会出现"WF1/WF2 端点已注册但基线未登记"的中间假红。台账记 `B19 = b6effcf`。 |
| 5 | 确认动作由 **WF1 `decision=continue`** 承担，**没有** `POST /confirm` | 架构 §2.3 裁决：不新增第 15 个端点。幂等由 WF1 自己满足（指纹含 `method+path+tenant+user+body digest`，路径不同即不同空间），无需手工键。 |

## 4. 两条教训（都付过代价）

**① 反查租户的捷径 = 静默不执行。**
`create_plugin_task` 原先调 `_advance(task_id)`，而它靠"从 task_id 反查租户"（返回空串）→ 取不到记录 → **推进从未执行**。HTTP 层表现：`200` + 状态永远 `running` + 步骤永远 `pending` + **不报任何错**。
**这是最坏的一类缺陷：绿着但什么都没发生。**
修法：直接 `_advance_record(record)`，删掉占位实现。
教训：**判据要落在"端点会不会干活"，不是"端点存不存在"** —— `validate_openapi` 的 16/16 全绿也查不出它，只有流程测试（`tests/unit/test_task_flow.py`）能逮。
台账：`P3-B19-01`（P0，fixed `ae69a4a`）。

**② 改 SQL 却没有 PG = 改完没验。**
`0004` 首版写成 `DO $guard$ … END $$;`（标签不闭合）→ PostgreSQL 报 `unterminated dollar-quoted string`，**空库路径整条迁移语法失败**，`test_g11_*` 4 条 p0 全红。
**守卫块写坏了反而比不写更脆**：加了守卫却不闭合，等于把"跳过"变成"语法崩"。
修法：`END $guard$;`（fixed `b75f0ab`）。
纪律：①改动 `deploy/**` 前先确认本地 PG 可用，改完**必须**跑 `--run-pg`（空库路径是唯一能暴露它的场景）；②无 PG 时至少做"美元引号标签成对、首尾一致"的静态自检。

## 5. B20 的落点（下一批，按此顺序）

1. **三插件 6 步装配**：`plg_dev_workbench` / `plg_content_ops` / `plg_hr_admin` 的 `needs` 参数装配、"路径 ⊆ 清单"断言、Offer 金额 R1/R2/R3 三规则（**只扫 `output_key=offer`**，简历结论里的「待定」是合法值）、排期时间校验。
2. **降级矩阵**：`abort` / `continue`（进 `warnings[]`）/ `skip_and_mark`（清单标"待人工确认"）/ `retry(1)` 逐格落到三插件，且**步骤失败走状态不走错误码**（A3/A4 永不返回 `17002`）。
3. **`render_file` 原语**：`plugins/renderer.py`（docx / md / csv / zip），`render_format` **由声明给定、不接受调用方覆盖**；**docx 只由平台生成，插件目录禁 `import docx`**（G-28 会扫）。
4. **`python-docx` 三处登记**（D-20，MIT、版本锁死）：`pyproject.toml`、`deploy/validate_deps.py` 的 `DOCUMENTED_PACKAGES`、`docs/architecture-v1.0.md` §8.1。三处齐了 G3-10 才绿。
5. 现成可改的落点：`sanitizer._render_docx()` 已写好（依赖缺失时**如实降级为 `.md`，不伪造打不开的 docx**）——装上依赖即生效，无需改逻辑。

## 6. 未决事项（交接时仍开放）

1. **`OA_BATCH` 的报错文案**（在 QA 的 `tests/expected_xfail.py::current_batch()`）：现在是 `KeyError: 'OA_BATCH'`，建议改成 `RuntimeError("请设置 OA_BATCH（例如 OA_BATCH=B19）…")`。**后端没权限改该文件，需 QA 动手**。
2. **阶段一/二 5 个历史僵尸码**（`11005` / `12002` / `12004` / `12005` / `15006`）在 G-13 反查里只打 `[WARN]`：需另立批次收口，或由架构明确"登记即可、不要求引用"。
3. **`org_repo` / `role_repo` 的 PG 分支覆盖率**仍 <90%（阶段二遗留），与插件域无关。
4. **多副本调度**：`_advance_record` 的调度点已收敛，换 Valkey 队列（U-06）时只改这一处；`plugin_task_cancel_requested` 的语义（引擎在下一步入口停）需在改调度时一并复验。
