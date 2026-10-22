# 第三阶段 批次验收清单（按架构 §9 编号：B16–B21 + 前端批次）

> 用途：**批次验收的判定书**。team-lead 按本清单逐项勾选即完成批次验收；工程师按本清单自测即知道「怎么算过」。
> 配套：`docs/qa-phase3-test-plan.md`（用例与门禁判据的唯一来源）。
> **编号口径（2026-10-23 对齐架构 §9，以此为准）**：B16 声明模型+加载器+G-26 ｜ B17 市场与安装（PL1-PL5 + PL13）｜ B18 生命周期（PL6-PL12）｜ B19 任务引擎 + A 域扩展 + 9 表 ｜ B20 三插件装配与降级矩阵 ｜ B21 端点基线与门禁。
> **B20 过大 → 按验收粒度拆 B20-1 / B20-2，但代码编号仍是 B20 一个批次一个 commit**（主理人裁定：不破坏与架构/工程的编号一致性）。
> **前端批次沿用架构 B22-B27**（B22 已落库 `41310ba`）：**UI25 → B23**、**UI24 → B24**，不再叫「前端批次」，也不另立编号。
> **端点口径（v3.2.0 终稿）**：插件域 **14**（PL1-PL14，新增 PL14 `PUT /plugins/{id}/grants`）+ 任务域 **2**（WF1 retry、**WF2 产物编辑**）= **16**；三基线 **30 / 38 / 16 = 84**。
> 作者：严过关（QA 工程师）· 日期：2026-10-23 · 版本：**v1.1（重编号）**

---

## 0. 使用方式（5 步，缺一步不算验收）

| 步 | 动作 | 责任人 | 产出 |
| --- | --- | --- | --- |
| 1 | 工程师提交批次 commit，并附**自测证据包**（§0.2） | Engineer | commit SHA + 证据包 |
| 2 | QA **导出该 SHA** 到独立目录复核（不读活工作区） | QA | 导出路径记录 |
| 3 | QA 跑本批「实跑命令」+ 通用门禁块（§0.1） | QA | 命令 + 退出码 + 关键输出 |
| 4 | QA 按「验收项表」逐条判定，输出 `PASS / FAIL(带定位)` | QA | 批次验收结论 |
| 5 | team-lead 勾选本清单并归档 | team-lead | 勾选后的本文件（或批次报告引用） |

### 0.1 通用门禁命令块（每批必跑，复制即用）

```bash
# ── 路径（架构已封版 16 端点，以下为真实路径，不再用占位符）──
REPO="C:/Users/15864/Desktop/麦子"
PY="$REPO/apps/adapter/.venv/Scripts/python.exe"
TREE="$REPO/_qa/p3/<SHA>"          # 导出目录：git archive <SHA> | tar -x -C "$TREE"
LOG="$REPO/_qa/p3/logs/<SHA>"       # 日志目录，先 mkdir -p
DSN="postgresql://officeai_app:CHANGE_ME_APP_PASSWORD@127.0.0.1:55433/officeai"
export OA_BATCH="B19"          # 当前批次（台账到期检查用；**缺失即红**，故意不给默认值）
mkdir -p "$LOG"

# ── 静态 / 测试（每条都重定向后单独取 $?，禁止管道）──
cd "$TREE/apps/adapter"
"$PY" -m ruff check .                                  > "$LOG/g01.log" 2>&1; echo "G-01 EXIT=$?"; tail -1 "$LOG/g01.log"
"$PY" -m ruff format --check .                         > "$LOG/g02.log" 2>&1; echo "G-02 EXIT=$?"; tail -1 "$LOG/g02.log"
"$PY" -m mypy app                                      > "$LOG/g03.log" 2>&1; echo "G-03 EXIT=$?"; tail -1 "$LOG/g03.log"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest -q --run-pg -rxX                > "$LOG/g04.log" 2>&1; echo "G-04 EXIT=$?"; tail -3 "$LOG/g04.log"   # 期望 0 failed / 0 skipped / xfailed == EXPECTED_XFAIL
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest -q -m p0 --run-pg               > "$LOG/g24.log" 2>&1; echo "G-24 EXIT=$?"; tail -2 "$LOG/g24.log"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest -q --run-pg --cov=app --cov-report=term > "$LOG/g05.log" 2>&1; echo "G-05 EXIT=$?"; grep "^TOTAL" "$LOG/g05.log"
"$PY" -m coverage report --include="app/business/*,app/db/*"            > "$LOG/g05b.log" 2>&1; echo "G-05b EXIT=$?"; tail -1 "$LOG/g05b.log"

# ── G3-12 双向码表核对（pm 2026-10-23 提出；**必须按批次放行，否则中批全红**）──
#   ① 对外码：必须被 ≥1 个**已实现端点**引用（反查僵尸码）
#   ② 端点引用：必须在注册表内（正查未定义码）
#   ③ 内部码（可见性=内部，如 17002）：不得被任何端点引用
#   ⚠️ 中批陷阱：B17 只有 PL1-PL5/PL13，16003/16004/16007/16008/16010/16011/16012/17001
#      都是"端点未落地"的正常状态，直接判僵尸会**每批假红**。放行方式与 G-13 同源：
#      按 `--allow-pending bxx` 列出"本批尚未实现的端点所对应的码"，只对已实现端点判僵尸。
#   最小断言：两个方向的差集都为空（不是"数量相等"）。
# ── 占位禁用自检（team-lead 2026-10-23）：pytest.fail 不得当占位 ──
HITS=$(grep -rn "pytest.fail(" "$TREE/apps/adapter/tests/" | grep -icE "未实现|TODO|占位" || true)
echo "G-PLACEHOLDER HITS=$HITS (期望 0)"; test "$HITS" = "0"

# ── 契约 / 错误码 / 插件声明（部署脚本在 REPO 根）──
# B21 起 `--allow-missing-phase3` **已从脚本删除**：再传会被 argparse 拒绝（exit 2）。
# 现在一律**严格模式**（phase3 必须 == 16）；B17-B20 的历史结论引旧参数时请注明「当时参数」（届时 phase3 必须 14/14）
"$PY" "$TREE/deploy/validate_openapi.py" --allow-missing-phase2 \
      > "$LOG/g29.log" 2>&1; echo "G-29 EXIT=$?"; grep -E "端点统计|G1-G5" "$LOG/g29.log"
"$PY" "$TREE/apps/adapter/scripts/check_error_catalog.py" --strict        > "$LOG/g30.log" 2>&1; echo "G-30 EXIT=$?"; tail -1 "$LOG/g30.log"
"$PY" "$TREE/deploy/validate_plugins.py" --dir "$TREE/apps/adapter/app/plugins/manifests" \
      > "$LOG/g26.log" 2>&1; echo "G-26 EXIT=$?"; grep -o '"directory": "[^"]*"' "$LOG/g26.log" | head -1; tail -1 "$LOG/g26.log"
"$PY" "$TREE/deploy/validate_plugins.py" --dir "$TREE/apps/adapter/app/plugins/manifests" --self-test \
      > "$LOG/g26s.log" 2>&1; echo "G-26SELF EXIT=$? (期望 1)"; tail -2 "$LOG/g26s.log"

# ── 归档身份（写进报告固定字段：门禁验的是哪棵树）──
echo "ARCHIVE_SHA=$(git -C "$REPO" rev-parse <SHA>)" | tee "$LOG/archive-identity.txt"
echo "ARCHIVE_TREE=$TREE" | tee -a "$LOG/archive-identity.txt"
```

> **退出码纪律（主理人 2026-10-23 抽验教训，强制）**：`cmd | tail -3; echo $?` 取到的是 **`tail` 的退出码（恒 0）**，会把「自检通过=1」误判成「永远绿」。**所有门禁必须重定向到日志后单独取 `$?`**；前端三个 `.mjs` 扫描器同理。**违反此纪律的输出一律视为无效证据（见 §5 第 10 条）。**

### 0.2 工程师自测证据包（缺项直接退回，不进验收）

① 本批 commit SHA；② 上述命令的**原始输出**（不允许只写「已通过」），且 pytest 汇总行必须给出**四列** `passed / failed / skipped / xfailed` 与**两个数字**（`EXPECTED_XFAIL` vs 实际 `xfailed`）；③ 本批新增/变更端点的**契约 diff**（`docs/api/README.md` + 基线 JSON 同一 commit）；④ 新增错误码三处同步位置；⑤ 已知未完成项清单；⑥ 若动前端：`tsc`/`lint`/`build` 输出与受影响页面清单；⑦ **归档身份**：被验的 commit SHA + 导出目录绝对路径；**所有带目录扫描的门禁（G-26 等）必须显式传 `--dir` 并附日志里的 `"directory": "..."` 行**——不带 `--dir` 时会扫到「已安装 app 包」而非导出树，形成**假隔离**（2026-10-23 QA 实测：在 `/tmp` 导出目录跑，日志显示扫的是主工作区）。

### 0.3 p0 分布与累计（按架构 §9 编号）

| 批次 | 本批 p0 | 累计 p0 | 主要用例前缀 |
| --- | --- | --- | --- |
| B16 | 7 | 7 | PL01、PL05 |
| B17 | **20** | **27** | MK06、MK07、PL03（含 **PL14** 的 4 条，其中 **PL03-11 = 未安装改授权**）、MK10-02 |
| B18 | **16** | **43** | PL02-01、MK08（含 **PL5 重复安装**）、PL04、MK09、MK10-01 |
| B19 | **26** | **69** | PL02-02~07、WF11、**WF12（含 WF2 编辑 4 条）**、WF13、WF14 |
| **B20-1** | 13 | **82** | DEV15~17、E2E-DEV |
| **B20-2** | 28 | **110** | CNT18~20、HR21~23、E2E-CNT/HR |
| B21 | 0（门禁批次） | 110 | —（G-29/G-30 + G3-10/G3-11 + 阶段二零回退） |
| **B23** | **0**（界面复核） | 110 | —（复核 MK06-04 / PL03-05 / PL03-07 的界面层） |
| **B24** | **7** | **117** | UI24、UI25 |

> 说明：B21 是**收口批次**，不新增 p0，验收对象是三基线（30/38/**16**，合计 84）、错误码一致性（含 `17002` 不外泄 + 内部码豁免）与阶段二零回退；**p0 117 全绿在 B24 收口**。B23 只做界面层复核（主理人已确认记 0 条新 p0，避免与 B17 重复计数）。

### 0.4 汇总口径（**xfail/skip 定死**，与测试方案 §2.6 同源）

| 规则 | 判据 |
| --- | --- |
| `skipped` | **任何批次、任何模式都必须为 0**。自检类文件在正常模式下**不收集**（不是 `skipif`）；实现二选一：①文件名不带 `test_` 前缀（pytest 默认不收集，**首选**——零改全局配置）；②`conftest.py` 的 `collect_ignore`。`skip` 没有到期日，`xfail` 有编号 |
| `xfailed` | 正常模式允许 `xfailed == EXPECTED_XFAIL`（`tests/expected_xfail.py` 的模块常量，唯一真源）；**批次完成后按归属表下降**（`EXPECTED_XFAIL_BY_BATCH`：B16=6 → B17=2 → B19=0，阶段终态 0）。**只要求该批归属的 xfail 全部转 pass，不要求一次归 0** |
| 汇总断言 | pytest 汇总必须同时打印 **`EXPECTED_XFAIL`** 与**实际 `xfailed`**；不等即红（多=新增未登记缺陷；少=已修复但常量未减，强制摘标记） |
| 验收报告 | 自动化测试列写**四列** `passed / failed / skipped / xfailed`，不得只写三列 |
| **`red`（已实现但红着）** | 单独计数 `EXPECTED_RED`（`tests/expected_xfail.py`），且必须满足 `EXPECTED_OPEN == EXPECTED_XFAIL + EXPECTED_RED`。**批次签收要求 `EXPECTED_RED == 0`**——否则等于「带着红缺陷签收」。red 随批次上升是正常的（新交付代码暴露缺陷），**但不得用它把 `xfailed` 做小**：把 xfail 改 red 时 `xfailed` 会降，而 `EXPECTED_OPEN` 必须同步不动或下降，改不动就说明只是洗了标记 |

**判定示例**：`4 passed + 6 xfailed + 0 skipped` = 合格；`4 passed + 6 xfailed + 2 skipped` = 红；`10 passed + 0 xfailed` 但常量仍为 6 = 红。

### 0.5 门禁假红排查（每批报告必填，按根因归类）

> **元规则**：假绿的代价是"以为做完了"；**假红更贵**——它消耗团队对门禁的信任，最后所有人开始"绕门禁"。
> 截至 B17 已出现 **4 个假红源，其中 3 个同根因：门禁不知道自己的输入是什么**。

| 假红源 | 形态 | 根因归类 | 状态 |
| --- | --- | --- | --- |
| G-13 要求 `17002` 出现在 `business-errors.ts` | 脚本判据与契约**方向相反**（架构要求 0 命中） | 脚本未跟契约 | 已报 P1，待修（`INTERNAL_CODES` 排除 + `--forbid-internal`） |
| G-29 传了 `--allow-missing-phase3`（B21 已删除该参数） | 门禁依赖外部参数，但缺省不含该参数 | **输入未界定** | 已修（§0.1 命令补参数） |
| G-26 缺省路径扫到"已安装包"而非归档树 | 隔离复跑变成假隔离，验的不是它声称的树 | **输入未界定** | 已报，待修（缺省按 `ADAPTER_ROOT` + 日志打绝对路径 + 断言解析目录==遍历目录） |
| G3-12 无批次白名单 | 把"本批未实现端点"的码当僵尸码 | **输入（应查范围）未界定** | 已修（pm §7 行 201 加 `--allow-pending <批次>`） |

**填报要求**：每批报告在此表后追加本批实测——「假红源 / 是否命中 / 处置」，命中但未修的要写清"已登记 + 到期批次"，不得只写"已知悉"。

---

## B16 声明模型 + 加载器 + 6 份 manifest + G-26 schema

**范围**：manifest 6 段声明、机械校验、加载失败留因、重复标识、拒绝自带依赖、开发文档与样例插件。
**用例**：`TC-P3-PL01-01~04`、`TC-P3-PL05-01~03`（**7 条 p0**）。
**不含**（按架构 §9 归 B19）：超时、并发排队、任务执行、停用后拦截——这些是任务引擎的验收点，不是声明/加载器的。

**准入**：架构已给 manifest 字段表与插件目录约定；G-26 脚本可执行；6 份 manifest 与 1 份样例插件已入库。

| # | 验收项 | 判据（可断言） | 谁跑 |
| --- | --- | --- | --- |
| 1 | 6 份 manifest 全通过 | `validate_manifests.py --strict` exit 0；6 份均加载成功 | QA |
| 2 | 非法声明不加载 | 12 个负样本**均不出现**在加载结果，且留 **12 条**失败原因（`plugin_id` + 原因码） | QA |
| 3 | 声明与展示一致 | `step_titles[]` == manifest `steps[].title`（逐项 `==`） | QA |
| 4 | 重复标识 | 第二次注册失败，原条目字段快照 diff 为空 | QA |
| 5 | 自带依赖被拒 | manifest 含依赖段 → 加载失败 + 原因「不支持自带依赖」 | QA |
| 6 | 文案在声明期拦截 | 名称含「RAG」等禁词 → 加载失败（不是等到渲染期） | QA |
| 7 | 文档 6 节齐全 | 声明字段表/能力枚举/输入规范/输出类型/界面规范/允许接口清单 | QA |
| 8 | 文档与代码一致 | 「允许调用的接口」集合 **==** 代码端点集合（双向差集空） | QA |
| 9 | 样例插件可跑 | 固定输入跑通 1 次任务，产物 ≥1，无人工参数注入 | QA |
| 10 | G-26 可红 | `--self-test` **exit 1**（脚本内置 6 条全命中）+ 单测 6 类（合计 12 类）；**6 份真实 manifest 判绿不误报** | QA |

**实跑命令**

```bash
cd "$TREE/apps/adapter"
"$PY" deploy/validate_plugins.py            > "$LOG/b16-g26.log" 2>&1; echo "G-26 EXIT=$?"; tail -2 "$LOG/b16-g26.log"
"$PY" deploy/validate_plugins.py --self-test > "$LOG/b16-g26s.log" 2>&1; echo "G-26SELF EXIT=$? (期望 1)"; tail -2 "$LOG/b16-g26s.log"
"$PY" -m pytest tests/unit/test_manifest_validation.py -q > "$LOG/b16-unit.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b16-unit.log"
"$PY" -m pytest tests/integration/test_plugin_manifest_load.py -q > "$LOG/b16-int.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b16-int.log"
```
> 说明：`validate_plugins.py` 默认扫 `apps/adapter/app/plugins/manifests`（**6 份**）；其 `--self-test` 内置 **6 条**负样本，另 6 类非法声明由 `tests/unit/test_manifest_validation.py` 覆盖（合计 12 类）。

**通过判据**：10 项全过 + 通用门禁 + 累计 p0 ≥7。
**常见退回原因**：① 只测合法声明，12 负样本漏检；② `--self-test` 返回 0（脚本不会报错）却当绿；③ 禁词只在渲染期拦，声明期放行；④ 文档接口清单写了代码里没有的端点；⑤ **把超时/排队写进本批**（应在 B19）。

---

## B17 市场与安装（PL1 列表 / PL2 详情 / PL3 已安装 / PL4 能力 / PL5 安装 / PL13 配置 / **PL14 改本人授权**）

**范围**：市场浏览与详情、能力清单与逐项授权、安装（含企业开关）、已安装列表的「最近使用时间」。
**用例**：`TC-P3-MK06-01~04`、`TC-P3-MK07-01~04`、`TC-P3-PL03-01~11`、`TC-P3-MK10-02`（**20 条 p0**）。

**准入**：能力→端点映射表已给定（G-28 输入）；错误码 `16001-16006` 已分配并三处同步；市场分类枚举与 6 份 manifest 对齐。

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 市场集合对齐 | `market.items` 的 `plugin_id` 集合 **==** manifest 集合 S（不硬编码 6） | QA |
| 2 | 分类/关键字/排序 | 过滤项分类全为 `dev`；关键字大小写/空格差异结果集合相等；三种排序序列两两不同 | QA |
| 3 | 分页无重叠 | 第 1、2 页 `plugin_id` 交集空；`total` 与累计一致；无匹配时空列表 + 空态文案 | QA |
| 4 | 免费版整页提示 | 「当前套餐不含插件能力，升级后即可使用」+ 升级入口；**无错误码** | QA |
| 5 | 详情页一致 | `capabilities[]` 条数 == capabilities 端点条数；`step_titles[]` 顺序一致 | QA |
| 6 | 主按钮 4 态 | 安装 / 打开工作台 / 启用 / 更新，映射全对 | QA |
| 7 | 无评价空态 | 「还没有人评价，用过后可以来说说」（有该文本节点，非空表格） | QA |
| 8 | 能力条数 1:1 | `GET capabilities` 条数 == manifest 声明（研发 = **3**）；每项含 5 个字段 | QA |
| 9 | 拒必需能力 | 取消②→安装失败 + 文案「该插件需要「使用你已连接的办公工具」才能提交代码」+ 按钮置灰 | QA |
| 10 | 部分授权降级 | 只授权②③→安装成功、`denied_capabilities==[⑤]`；步骤 5「未完成：未授权写入代码仓库」+「去授权」 | QA |
| 11 | 撤销 ≤5s | 撤销②→立即调用 `16005`；恢复后 retry 成功；TTL 边界用注入时钟验证 | QA |
| 12 | **报错顺序（新口径）** | 同时满足「套餐不支持 / 未授权 / 企业策略关闭」时，返回顺序 **`15001` → `16005` → `16006`**；三者 CTA 不同（升级套餐 / 重新授权 / 找管理员） | QA |
| 13 | 企业开关（两处都断言） | ①**PL1 顶层含 `org_capabilities` 且恰好 3 项**（`{capability_key, org_switch_enabled}`）；②**PL4 逐插件 6 项**且每项含 `org_switch_enabled`；③`false` → 安装失败 + `16006`；④`true` → 成功且 `granted_capabilities` 含⑤；⑤两次表现必须不同 | QA |
| 14 | **PL14 改本人授权** | `PUT /plugins/{id}/grants` 改本人（撤③加④）：响应三个数组与请求一致；**审计留 1 条 `plugin.capability_grant`**（未安装直调 `16003` 的口径在 B18 第 1 项） | QA |
| 15 | **PL14 幂等** | 同 `Idempotency-Key` 重复提交 → 结果收敛（授权状态不变、审计仅 1 条） | QA |
| 16 | **PL14 两条反例** | ①撤 `required=true` → 失败（`16005` 语义）且授权不变；②用 A 的 token 改 B 的授权 → 越权拒绝（`20003` 语义）且双方授权均不变 | QA |
| 17 | 最近使用时间 | `last_used_at` == 最近一次任务 `created_at`；无任务为 `null` 且页面「尚未使用」 | QA |
| 18 | G-30 可红 | `--strict` exit 0；删 1 条前端映射 → exit 非 0；`16001` 未被复用 | QA |
| 19 | **PL14 未安装时改授权（补齐 PL14）** | `PUT /plugins/{id}/grants` 对未安装插件 → **`16003`**（不是 `16004`、不是成功）；② `plugin_grant` **无新增行**；③ 审计**无** `plugin.capability_grant` 事件（三条缺一不算覆盖） | QA |

**实跑命令**

```bash
cd "$TREE/apps/adapter"
"$PY" -m pytest tests/integration/test_plugin_market.py tests/integration/test_plugin_detail.py -q > "$LOG/b17-market.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b17-market.log"
"$PY" -m pytest tests/integration/test_plugin_authz.py -q   # 含 PL14 四条（08/09/10/11） > "$LOG/b17-authz.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b17-authz.log"
"$PY" scripts/check_error_catalog.py --strict
```

**通过判据**：**19 项**全过 + 通用门禁 + 累计 p0 **≥27**。
**常见退回原因**：① `16005`/`16006` 合并成一个码；② 报错顺序反了（用户被引到错误出路）；③ 撤销后仍可调用；④ 前端硬编码开关为 `true`（第 13 项必须两次表现不同）；⑤ 市场数量硬编码 6。

---

## B18 生命周期（PL6 状态 / PL7 卸载 / PL8 升级 / PL9 回退 / PL10 配置 / PL11-12 评价）

**范围**：安装即启用、启停、卸载保留历史、版本升级与恢复上一版本、评价 upsert。
**用例**：`TC-P3-PL02-01`（未安装即改状态 PL6 → `16003`；PL14 的未安装直调归 B17 第 19 项）、`TC-P3-MK08-01~06`、`TC-P3-PL04-01~04`、`TC-P3-MK09-01~04`、`TC-P3-MK10-01`（**16 条 p0**）。

**准入**：安装表与版本快照表的唯一约束已落迁移；步骤定义随版本存档方案已定。

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 未安装不可改状态（PL6） | `PATCH state` → **`16003`**（「该插件还没有添加」）；`GET installed` 不含（无隐式创建）。**PL14 的未安装直调口径在 B17 第 19 项**，本项不重复判 | QA |
| 2 | 安装即启用 + `already_installed` 恒有 | 安装后 `status=enabled`（不断言 `installed` 中间态）；**首次安装响应含 `already_installed=false`**（字段常驻，不得只在重复时出现） | QA |
| 3 | 启停往返 | `disabled` → `enabled`；禁用后宿主页置灰 + 文案 | QA |
| 4 | 卸载保留历史 | `GET installed` 不含；历史任务仍可查且详情可打开（只读） | QA |
| 5 | 卸载时有在跑任务 | 允许卸载 + `running_task_count==1`；任务最终 completed | QA |
| 6 | 二次确认后果文案 | 禁用含「…已发起的任务会继续完成」；卸载含「历史任务记录会保留但不能再发起新任务」「已生成的产物 180 天后清理」 | QA |
| 7 | 升级生效 | `installed_version==1.1.0`、`has_update==false`、任务可执行 | QA |
| 8 | 升级幂等 | 连点两次 → 版本记录**仅 1 条**，`previous_version` 未被覆盖 | QA |
| 9 | 只能回退一次 | 首次回 `1.0.0`；第二次 `16007`（或 20005）且版本不变 | QA |
| 10 | **历史任务用快照** | 升级改步骤标题后，升级前任务的 `step_titles` 与升级前逐项相等；回滚后仍相等 | QA |
| 11 | 更新标记 | 发新版本 → 该行出现目标版本号；更新后「已是最新」 | QA |
| 12 | 评价覆盖更新 | 同成员两次 → 仅 1 条且为第二次；`rating_avg` 与均值差 ≤0.1 | QA |
| 13 | 评价权限与参数 | 未安装 → `16013`；`rating=6` → 参数错误 +「评分请选择 1 到 5 星」 | QA |
| 14 | 匿名不含真实身份 | `author_name=="某位同事"`；响应 0 处真实姓名/邮箱 | QA |
| 15 | 唯一约束（`pg`） | 同企业同插件仅 1 条安装记录；并发安装不产生 2 条 | QA |
| 16 | **PL5 重复安装幂等** | 已安装再调 PL5 → **200 + `already_installed=true`**（**不是 `16003`**）；`granted_capabilities[]` 与 `settings` **不变**；审计仅 **1 条** `plugin.install` | QA |

**实跑命令**

```bash
cd "$TREE/apps/adapter"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest tests/integration/test_plugin_lifecycle.py -q --run-pg > "$LOG/b18-life.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b18-life.log"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest tests/integration/test_plugin_version.py tests/integration/test_plugin_review.py -q --run-pg > "$LOG/b18-ver.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b18-ver.log"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest tests/integration/test_plugin_isolation.py -q --run-pg > "$LOG/b18-iso.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b18-iso.log"
```

**通过判据**：**16 项**全过 + 通用门禁 + 累计 p0 **≥43**。
**常见退回原因**：① 卸载级联删历史任务；② 升级后用当前模板渲染历史任务（第 10 项）；③ 连点升级产生两条版本记录；④ `last_used_at` 用 `updated_at`；⑤ 内存后端绿但 `pg` 唯一约束缺失。

---

## B19 任务引擎 + A 域扩展 + 9 表（超时 / 排队 / 续跑 / 步骤产物 / 核查清单）

**范围**：任务执行与状态机、超时与取消、并发排队与自动转运行、步骤级进度与产物、**产物编辑（WF2：版本/重导出/脱敏）**、失败与重试、人工接管、历史筛选与核查清单。
**用例**：`TC-P3-PL02-02~07`（6）、`TC-P3-WF11-01~05`（5）、**`TC-P3-WF12-01~08`（8，含 WF2）**、`TC-P3-WF13-01~04`（4）、`TC-P3-WF14-01~03`（3）= **26 条 p0**。
> **本次从 B16 移入的 6 条**：`PL02-02`（任务带 `plugin_id`）、`PL02-03`（**超时不丢产物**）、`PL02-04`（**3 并发第 4 个排队返 200+queued**）、`PL02-05`（停用后拦截执行）、`PL02-06`（直连底座被拒）、`PL02-07`（**排队自动转 running**）。
> 对应检查单项 **6 项**（下表 #1-#4、#7、#8），其中「超时」1 项、「排队」2 项（不报错 + 自动转运行）。

**准入**：A2/A3/A4 的加性字段已进基线（`checklist[]`/`artifacts[]`/`failure{}`）；9 张表迁移幂等；事件推送 A6 可用。

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 任务带插件标识 | `A2?plugin_id=X` 能筛出，`plugin_id` 与入参相等 | QA |
| 2 | **超时不丢产物** | 5s 超时 → 状态「未完成（超时）」；已完成步骤 `artifacts.length ≥1` **仍可取** | QA |
| 3 | **排队不报错（ADJ-1）** | 第 4 个任务 **HTTP 200 + `status=queued` + `queue_position ≥1`**；**不是 4xx/429** | QA |
| 4 | **排队自动转运行（ADJ-1 追加）** | 在跑任务完成后排队任务自动 `running`（无需重提），`queue_position` 递减，最终 4 个全 completed | QA |
| 5 | 首事件及时 | 桩模式首个 `step` 事件 < 5s，连跑 3 次稳定 | QA |
| 6 | 状态单调 + 进度算法 + **枚举权威** | 10 次轮询 `status_index` 非递减；6 步完成 3 步 → `progress==50.0`（误差 <1e-6）；步骤状态 **⊆ `pending/running/completed/failed/skipped`**（`waiting_confirm` 是任务级，不得出现在步骤上） | QA |
| 7 | 停用后拦截执行 | 禁用后执行 → **`16004`**（`PLUGIN_DISABLED`；**不得写成 `16003`**）+ 文案「该插件已停用，请联系管理员启用」 | QA |
| 8 | 直连底座被拒 | 构造直连调用 → 拒绝且底座桩计数 **0** | QA |
| 9 | 断线补齐 | 断网 30s 恢复后 `step_id` 集合相等、无重复无缺失 | QA |
| 10 | 无技术词 | DOM `node`/`step_id`/`workflow`/`prompt`/`LLM` **0 命中** | QA |
| 11 | 产物归属 | 每个 `artifact_id` 反查 `task_id` 唯一；已完成步骤 `artifacts.length ≥1` | QA |
| 12 | **失败走状态不走码** | 注入失败 → `code==0`、`failure.retryable==true`、3 个动作齐 | QA |
| 13 | 重试语义 | 新任务 `retry_of`=原任务；前序步骤 `skipped` 且 `artifact_id` **复用**；原任务 `status/updated_at` 不变 | QA |
| 14 | 未失败不可重试 | `16011`；界面无重试按钮 | QA |
| 15 | 接管不自动继续 | 时钟 +15min 仍 `waiting_confirm` | QA |
| 16 | 诊断文本干净 | 复制文本 `\b[1-3]\d{4}\b` 0 命中、无 `Traceback` | QA |
| 17 | 四维筛选 | 插件/状态/发起人/时间 4 条独立 + 1 条组合，返回集合**精确相等** | QA |
| 18 | 核查清单 | 项数 4/3/4，名称**逐字相等**，`status ∈ {verified, pending_manual}` | QA |
| 19 | 越权查看 | 非发起人且无管理权 → 权限错误 +「你没有权限查看该任务」，响应体不含任务名/产物名 | QA |
| 20 | 保留期静态断言（原 G-31 降级） | 保留期字段存在且默认 **180**；过期文案常量存在（**不断言真实清理**） | QA |
| 21 | 迁移幂等 | 9 张表连跑两遍迁移均通过；表数符合基线 | QA |
| 22 | **WF2 编辑：版本与下载一致** | 保存后 `version` **+1**、`version_history[]` 含旧版本且内容逐字一致、**下载拿到新版本**（sha256 == 保存内容；不得「存了但下载还是旧的」） | QA |
| 23 | **WF2 非可编辑类型必须拒** | `type=link` / `type=file`（代码文件、提交记录）调 WF2 → 拒绝且产物 sha256 不变（否则 AC-02 结果物核查作废） | QA |
| 24 | **WF2 重导出与 sha 重算** | 改字段后导出的 docx 可解压出 `word/document.xml` 且含新值；`content_sha256` **已重算**（≠ 旧 sha）；`version_history` +1 | QA |
| 25 | **WF2 编辑后必须重跑脱敏（一票否决）** | 用含敏感词的 content 保存 → 产物中该词被消解；**不得因「用户改过」跳过 sanitize**；审计留 1 条编辑记录 | QA |

**实跑命令**

```bash
cd "$TREE/apps/adapter"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest tests/integration/test_plugin_lifecycle.py -q --run-pg -k "queue or timeout or disabled" > "$LOG/b19-life.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b19-life.log"
"$PY" -m pytest tests/integration/test_workflow_progress.py tests/integration/test_workflow_artifacts.py -q > "$LOG/b19-wf1.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b19-wf1.log"
"$PY" -m pytest tests/integration/test_workflow_retry.py tests/integration/test_workflow_history.py -q > "$LOG/b19-wf2.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b19-wf2.log"
"$PY" -m pytest tests/unit/test_artifact_retention.py -q > "$LOG/b19-ret.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b19-ret.log"
OA_TEST_DATABASE_URL="$DSN" "$PY" -m pytest tests/integration/test_migrate.py -q --run-pg > "$LOG/b19-mig.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b19-mig.log"
```

**通过判据**：**25 项**全过 + 通用门禁 + 累计 p0 **≥69**。
**常见退回原因**：① 超时把已完成产物一起清空（第 2 项）；② 第 4 个任务返回 429（违反 ADJ-1）；③ 排队任务不自动启动（第 4 项）；④ 步骤失败返回非 0 `code`（前端整屏报错，违反 FLOW-04）；⑤ 重试把前序步骤重跑；⑥ 产物挂任务级而非步骤级；⑦ 核查清单用 `in` 而不是 `==`。

---

## B20 三插件装配与降级矩阵（验收粒度拆 B20-1 / B20-2，代码编号仍为 B20）

**总范围**：三个标杆插件（研发 / 内容 / 人事）的装配、输入输出、人工接管点、能力降级矩阵与端到端结果物。
**p0 合计 41 条**，按验收粒度拆两屏：
- **B20-1 研发插件端到端**：`TC-P3-DEV15-01~03`、`DEV16-01~03`、`DEV17-01~03`、`E2E-DEV-01~04` = **13 条**
- **B20-2 内容 + 人事插件端到端**：`TC-P3-CNT18-01~04`、`CNT19-01~03`、`CNT20-01~03`、`HR21-01~03`、`HR22-01~03`、`HR23-01~04`、`E2E-CNT-01~04`、`E2E-HR-01~04` = **28 条**

**准入**：本地 bare 仓库夹具与 `github_stub`/`wechat_mp_stub`/`mail_stub`/`wecom_stub` 可用；`fail_injector` 可按步骤注入；金额豁免清单已写成 fixture 常量。

### B20-1 研发插件（13 条 p0，2025 年 13 项）

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 必填校验 | 缺标题/描述/仓库 → 置灰并指出缺项（3 条断言） | QA |
| 2 | 未连仓库空态 | 「还没有连接代码仓库」+ 连接入口（非报错） | QA |
| 3 | 方案确认门 | 步骤 1、2 后 `waiting_confirm`；重做方案时步骤 1 `artifact_id` 不变、方案 `version+1` | QA |
| 4 | 路径 ⊆ 清单 | 代码文件路径集合 ⊆ 影响文件清单；测试文件 ≥1 且含被测文件主干 | QA |
| 5 | 空产出判失败 | 注入空产出 → 「未完成」+ 可重试；重试后 completed（不得静默通过） | QA |
| 6 | **未确认不提交** | 未点确认 → bare 仓库 `rev-list --count` **不变** | QA |
| 7 | **提交真实存在** | `git cat-file -e <sha>` 成功；`git show --stat` 文件数 == 产物文件数 | QA |
| 8 | **逐字一致（HV-1 新抽样规则）** | 取**方案影响文件清单声明顺序**的第 1 个产物文件的**第一个**符合扩展名的路径 → 与仓库文件**逐字节**比对（sha256 相等 + 逐行 `==`），**3 次重复一致**；失败输出**前 200 字符 diff** | QA（HV-1） |
| 9 | 合并请求关联 | 桩收到 **1 次** `create_pr(head=<sha>)`；`pr_url` 含 `<sha>` | QA |
| 10 | 授权过期可恢复 | 401 → 「代码仓库的授权已过期」+「重新连接」；重连后 retry 成功 | QA |
| 11 | 分支冲突提示 | 目标分支被更新 → 「目标分支已有新的改动，建议新建分支提交」+「改用新分支重试」 | QA |
| 12 | 无技术词 | 界面 0 处 `diff`/`patch`/`sha` | QA |
| 13 | 核查清单 4 项 | 名称逐字 == [需求要点, 代码文件, 用例文件, 提交记录与合并请求]，全 `verified`，重 GET 一致 | QA |

### B20-2 内容 + 人事插件（28 条 p0，18 项）

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 平台必选 / 上限 | 未选 → 置灰 +「请至少选择一个发布平台」；勾 5 → 「单次最多 4 个平台」 | QA |
| 2 | 选题数正确 | 勾 3 → 3 组选题、每组标题 ≥3；标题字数 公众号 ≤64 / 其他 ≤40 | QA |
| 3 | 无素材不编造 | 标「未找到可靠素材」；8 条事实表述正则 **0 命中** | QA |
| 4 | 稿件数=平台数 | 3 篇、`platform` 互不相同、每篇 title/body/tags | QA |
| 5 | 字数区间 | 目标 800 → 640~960（去空白字符数）；越界自动重生成 1 次 → 仍越界标「待人工调整」 | QA |
| 6 | 草稿版本 | 编辑保存版本 +1；切回上一版本 sha256 相同 | QA |
| 7 | **排期表 4 列（HV-2）** | 表头逐字 [平台, 标题, 计划时间, 状态]；行数 == 稿件数；CSV 逐行一致，3 次重复一致 | QA（HV-2） |
| 8 | 过去时间被拒 | 计划时间早于当前 → 失败并指出冲突行；改时间后 retry 成功（不重跑步骤 3） | QA |
| 9 | 回执 vs 待人工 | 公众号 `receipt_id` 非空 + `已发布`；小红书 `待人工发布` + 下载 200 + 指引非空 | QA |
| 10 | **评分表风险非空（HV-3）** | 3 行 × 5 字段；`risk` 逐行非空；结论 ∈ 枚举；理由 ≥30 字；3 次重复一致 | QA（HV-3） |
| 11 | 简历上限与部分失败 | 第 21 份 → 「一次最多 20 份」；加密文件行标「无法读取…」，其余行正常 | QA |
| 12 | 提纲 ≥5 问 | 份数 == 建议面试人数；每题 `assessment_point` 非空 | QA |
| 13 | 渠道与冲突 | 未连接渠道置灰 +「还没有连接企业微信」；冲突 → 提示 + **2 个**可选时间 | QA |
| 14 | **金额判据只扫 offer 产物（新口径）** | 扫描范围**仅 `output_key=offer` 的产物**（简历筛选结论里的「待定」是合法值，不得误判）；`offer` 内 3 个金额字段 `==""`；R1 带单位 + R2 上下文窗口（薪资词 ±20 字符内 `\d{3,7}`）**0 命中**；豁免项固化（日期/工号/百分比/「3 个月」）；**`offer` 内出现「面议/待定」判失败**；未填金额导出被拒 | QA（HV-4） |
| 15 | 公文导出 | 4 类各 1 篇；`.docx` 可 `python-docx` 打开、段落数 >0、大小 >0 | QA |
| 16 | 24h 可追溯 | 时钟 +24h 后稿件版本与 `receipt_id` 仍可读，产物 sha256 不变 | QA |
| 17 | 降级矩阵 | 缺任一能力时对应步骤「未完成：未授权…」，其余步骤正常完成（不得整体失败） | QA |
| 18 | 门禁可红 | G-27 插件资产 `--self-test` **exit 1**（注入 1 个 `.css`） | QA |

**实跑命令**

```bash
cd "$TREE/apps/adapter"
# B20-1
"$PY" -m pytest tests/integration/test_dev_workbench.py -q > "$LOG/b20-dev.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b20-dev.log"
"$PY" -m pytest tests/integration/test_dev_commit_e2e.py -q -m e2e > "$LOG/b20-dev-e2e.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b20-dev-e2e.log"
# B20-2
"$PY" -m pytest tests/integration/test_content_workbench.py tests/integration/test_content_schedule.py -q > "$LOG/b20-cnt.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b20-cnt.log"
"$PY" -m pytest tests/integration/test_hr_screening.py tests/integration/test_hr_interview.py tests/integration/test_hr_offer_docs.py -q > "$LOG/b20-hr.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b20-hr.log"
"$PY" -m pytest tests/integration/test_plugin_e2e_content.py tests/integration/test_plugin_e2e_hr.py -q -m e2e > "$LOG/b20-e2e.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b20-e2e.log"
cd "$TREE" && node apps/web/scripts/check-plugin-ui-assets.mjs --self-test   # 期望 exit 1
```

**通过判据**：B20-1 13 项（累计 **≥77**）+ B20-2 18 项（累计 **≥105**）全过 + 通用门禁。
**常见退回原因**：① 只在桩里「假装提交」，bare 仓库无对象；② 抽样只断言「非空」而非逐字节；③ 未确认就能提交（接管点形同虚设）；④ 未接入平台造假回执；⑤ **金额扫描扫到简历筛选产物**，把合法的「待定」误判为违规；⑥ 缺能力时整个任务失败而非单步降级。

---

## B21 端点基线与门禁（收口批次，不新增 p0）

**范围**：三段基线（core 30 / phase2 38 / **phase3 16**）、错误码三处一致（含 `17002` 不外泄 + 内部码豁免）、docx 依赖与插件纯净、前端五门禁、阶段二零回退。
**p0**：0（本批只做门禁与回归；**p0 117 全绿在 B24 收口**）。

**准入**：所有后端批次已合入；`docs/api/README.md` 与基线 JSON 已同步；前端已构建。

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 三基线 | `core=30`、`phase2=38`（**不回改**）、**`phase3=16`**（PL1-PL14 + WF1/**WF2**，合计 **84**）全实现；G4 加性 diff 0 violation | QA |
| 2 | G-29 可红 | 删 1 个 phase3 端点（含 **WF2**）/ A4 类型 array→object / 改 phase2 路径名 / A1 加必填字段 —— 5 个负样本均 exit 非 0 | QA |
| 3 | 错误码一致 | `check_error_catalog.py --strict` exit 0（0 PENDING）；新码不撞既有段、**不复用 `16001`**、同码文案 ≤2 种 | QA |
| 4 | 阶段二行为证据 | 抽 BL2/BL5/BL10/OPS1/OPS5/KB9 六个用例，在阶段三树上继续绿 | QA |
| 5 | 阶段二零回退 | 阶段二 1119 用例 0 failed / 0 skipped；p0 178 全绿 | QA |
| 6 | 前端五门禁 | 禁词（源码/自检/产物）、占位词、三态、错误码正则、插件资产全绿 | QA |
| 7 | 覆盖率（**2026-10-23 终裁·四档；全部锚在复算值上、零松弛；此后不再改动**） | ① **总 `app/*` 合并 ≥88%**（复算 88.06%）② **`business+db` 合并 ≥86%**（复算 **86.09%**；取 86 是「不回归」，**写 85 会低于实测 = 悄悄下调**）③ **分支底线 ≥76.6%**（复算 **76.60%**；现 74.19%）④ **核心三 repo**（`task_repo`/`artifact_repo`/`plugin_repo`）**语句 ≥85% 且 分支 ≥60%**——**新设、无历史基线（须在报告标注）**。三指标（语句/分支/合并 + 分母）**必须展示**，门禁只认上述四档。**表述三条（不得省）**：(a) 「CI 从未开分支」**不成立**——`apps/adapter/pyproject.toml:175 branch = true`，`num_branches = 1218` 为证（本轮最易被后人重复的错误结论）；(b) `89.99 / 89.10 / 93.37` **复算不出、不得再引用**，能对上的是 `88.06%`(总)/`86.09%`(business+db)；(c) `88%` 对 `business+db` **从未达成**（历史 86.09%），门槛锚 86% 是「不回归」，不是「上调」也不是「维持」。**报告强制行**：`coverage: num_branches=N | 语句 x% | 分支 y% | 合并 z% | 分母 … | 门槛版本=2026-10-23 终裁`。**禁用**：不可复算的基准数不得作门槛依据（复算不出即出局） | QA |
| 8 | **G3-10 docx 依赖与插件纯净** | 依赖登记 **16** 项且含 `python-docx`；插件目录 **`import docx` 库 0 命中**（声明里的 `"render_format": "docx"` 是**契约要求、保留**，不属违规——G-28 管的是「不得 import 库」，不是「不得出现 docx 字样」）；产物 `.docx` 可解压出 `word/document.xml` | QA |
| 10 | **「最终态一致性」门禁三态证据（B21 唯一把关点）** | 对端点基线/码表/声明 schema 三类门禁，必须给**三态**：①**缺项→红**（严格模式 `EXIT=1` + 明确"N<16"）②**补齐→绿**（`EXIT=0`）③**再删一项→红**（临时删 1 个端点后仍能红，再恢复）。只证"能绿"不够——要证"绿是补齐换来的，不是参数放行"。基线：`49cc9f7` 严格=1/容差=0/`phase3=14/16` | QA |
| 9 | **G3-11 `17002` 不外泄 + 内部码豁免** | ①`17002` 仅后端内部定义与单测允许；**A3/A4 响应、前端兜底表、任何 `message` 0 命中**；②脚本内置豁免名单 **`{17002}`**（内部码不要求前端覆盖，否则 G-13 永远红）；③反例：从名单删 `17002` → `--strict` 必须失败（证明是白名单而非关检查） | QA |

**实跑命令**

```bash
"$PY" "$TREE/deploy/validate_openapi.py"                           > "$LOG/b21-openapi.log" 2>&1; echo "G-29 EXIT=$?"; tail -2 "$LOG/b21-openapi.log"
"$PY" "$TREE/apps/adapter/scripts/check_error_catalog.py" --strict > "$LOG/b21-catalog.log" 2>&1; echo "G-30 EXIT=$?"; tail -1 "$LOG/b21-catalog.log"
"$PY" "$TREE/deploy/validate_plugins.py" --self-test               > "$LOG/b21-g26s.log" 2>&1; echo "G-26SELF EXIT=$? (期望 1)"; tail -1 "$LOG/b21-g26s.log"
cd "$TREE/apps/web"
npm run typecheck        > "$LOG/b21-tsc.log" 2>&1;   echo "TSC EXIT=$?";   tail -2 "$LOG/b21-tsc.log"
npm run lint             > "$LOG/b21-lint.log" 2>&1;  echo "LINT EXIT=$?";  tail -2 "$LOG/b21-lint.log"
npm run build            > "$LOG/b21-build.log" 2>&1; echo "BUILD EXIT=$?"; tail -2 "$LOG/b21-build.log"
node scripts/check-banned-terms.mjs                > "$LOG/b21-banned.log" 2>&1;   echo "BANNED EXIT=$?";   tail -2 "$LOG/b21-banned.log"
node scripts/check-banned-terms.mjs --self-test    > "$LOG/b21-banneds.log" 2>&1;  echo "BANNED_SELF EXIT=$? (期望 1)"; tail -2 "$LOG/b21-banneds.log"
node scripts/check-no-placeholders.mjs             > "$LOG/b21-ph.log" 2>&1;      echo "PLACEHOLDER EXIT=$?"; tail -2 "$LOG/b21-ph.log"
node scripts/check-page-states.mjs                 > "$LOG/b21-states.log" 2>&1;  echo "STATES EXIT=$?"; tail -2 "$LOG/b21-states.log"
node scripts/check-plugin-contract.mjs             > "$LOG/b21-contract.log" 2>&1; echo "CONTRACT EXIT=$?"; tail -2 "$LOG/b21-contract.log"
node scripts/check-plugin-contract.mjs --self-test > "$LOG/b21-contracts.log" 2>&1; echo "CONTRACT_SELF EXIT=$? (期望 1)"; tail -2 "$LOG/b21-contracts.log"
```

**通过判据**：**9 项**全过；任一红即 B21 不通过（本批没有「部分通过」）。
**常见退回原因**：① phase2 变成 37/39（改了老契约）；② phase3 写成 14/15（漏 PL14 或 WF2）；③ 错误码新增但 `business-errors.ts` 没补；④ `17002` 出现在 `message` 或前端兜底表；⑤ 只跑契约文件不跑阶段二用例（缺行为证据）；⑥ 前端门禁 `--self-test` 返 0。

---

## B23 前端·市场与授权文案（3 条**界面复核**，不新增 p0）

**范围**：免费版市场的降级表现、授权抽屉文案、`16006` 提示与后端一致性。
**用例**：**0 条新 p0** —— 这三条是对 B17 已过用例的**界面层复核**（后端断言在 B17，界面文案在 B23），**不重复计 p0**，累计仍 104。
> 口径说明（主理人 2026-10-23 已确认）：B23 三项分别对应 `MK06-04`、`PL03-05`、`PL03-07` 的**界面层**断言，按「复核」处理，**记 0 条新 p0**（后端断言属 B17），累计从 110 起算。

| # | 验收项 | 判据 | 谁跑 |
| --- | --- | --- | --- |
| 1 | 免费版市场不降级 | 免费版打开 `/plugins` → 整页「当前套餐不含插件能力，升级后即可使用」+ 升级入口；**不是错误页、不出现错误码**（Q3-1） | QA |
| 2 | 授权抽屉文案 | 能力项全部业务语言（0 处 `scope`/`permission`/`grant`）；按钮「同意并安装」；取消必需项的后果说明可见（W-3） | QA |
| 3 | `16006` 提示一致 | 界面文案与后端 `message` **逐字一致**；且与 `16005` 的文案/CTA 可区分（自己授权 vs 找管理员） | QA |

**实跑命令**

```bash
cd "$TREE/apps/web"
node scripts/check-banned-terms.mjs --p3-authz-drawer  > "$LOG/b23-drawer.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b23-drawer.log"
node scripts/check-page-states.mjs --p3-plugins-page   > "$LOG/b23-freeplan.log" 2>&1; echo "EXIT=$?"; tail -2 "$LOG/b23-freeplan.log"
```

**通过判据**：3 项全过；p0 累计**仍 109**（本批不计 p0）。

---

## B24 前端·宿主页与合规（7 条 p0）

**范围**：宿主唯一、路由、三态、未安装空态、禁词三面、插件资产 0、手工抽验。
**用例**：`TC-P3-UI24-01~04`、`TC-P3-UI25-01~03`（**7 条 p0**）。

| # | 验收项 | 判据 |
| --- | --- | --- |
| 1 | 宿主唯一 | 宿主组件文件数 **==1**；`pluginId` 布局分支 **0** 处 |
| 2 | 路由齐备 | `/plugins/{id}`、`/workbench/{id}`、`/workflows/{taskId}` 存在于构建产物 |
| 3 | 三态齐备 | 每个新页面引用 `LoadingState/EmptyState/ErrorState` 且 `ErrorState` 传 `onRetry` |
| 4 | 未安装空态 | `/workbench/{id}` → 「该插件还没有添加」+「去市场看看」（非错误页） |
| 5 | 禁词三面 | 源码/产物/声明文案 0 命中；`\b[1-3]\d{4}\b` 0；占位词 0 |
| 6 | 插件资产 0 | `.css/.scss/.less/.jsx/.tsx` = 0；字体图标包 = 0；`--self-test` exit 1 |
| 7 | 手工抽验 HV-5~8 | 各 3 次重复一致，留证齐全（抽样规则见测试方案 §4.11） |

**通过判据**：7 项全过 + **p0 117 全绿** + 阶段二 1119 零回退。

---

## 5. 退回规则（一票否决，不进入讨论）

| # | 情形 | 处理 |
| --- | --- | --- |
| 1 | `--run-pg` 出现 **skip** | 直接退回，不接受「环境问题」解释 |
| 2 | 门禁 `--self-test` 返回 0（漏检）却当绿 | 退回；该门禁视为未实现 |
| 3 | 用 `skip` / `xfail` / `assert True` 凑 p0 数 | 退回，按「伪造门禁」记 P0 流程缺陷 |
| 4 | 契约改了但 `docs/api/README.md` 未同步，或 `phase2` 数字变化（38→37/39） | 退回（G-29 红） |
| 5 | 结果物只有「字段存在」而无真实对象（无 git 对象 / 无文件 / 无回执 id） | 退回（AC-02 一票否决） |
| 6 | 插件目录出现 `.css`/自有 `.jsx`，或宿主出现 `pluginId` 布局分支 | 退回（AC-04 一票否决） |
| 7 | 验收项只写「已实现/正常」，无「可断言证据」 | 该项按未通过计 |
| 8 | 自测证据包缺项（§0.2 六项之一） | 退回补证，合规后重新排期 |
| 9 | **批次内容串号**（如把 B19 的超时/排队写进 B16） | 退回重报，避免「我说过了、你说没过」的扯皮 |
| 10 | **用管道取门禁退出码**（`cmd \| tail -n; echo $?`）或未附「EXIT=」原始行 | 该证据视为无效，按未跑处理（易把「自检=1」误判成「恒绿」） |
| 11 | **`skipped > 0`**，或用 `skipif` 代替 `collect_ignore` 掩盖自检文件；或 `xfailed` 与 `EXPECTED_XFAIL` 不等 | 退回；skip 视为「无到期日的假绿」，xfail 不等视为「缺陷台账与实际脱节」 |

---

## 6. 用例 → 批次映射速查（与架构 §9 对齐；前端批次 B22-B27）

| 批次 | 用例前缀 | 条数 | 累计 |
| --- | --- | --- | --- |
| B16 | PL01、PL05 | 7 | 7 |
| B17 | MK06、MK07、PL03（含 PL14 含 PL03-11）、MK10-02 | 20 | 27 |
| B18 | PL02-01、MK08（含 PL5 重复安装）、PL04、MK09、MK10-01 | 16 | 43 |
| B19 | PL02-02~07、WF11、WF12（含 WF2）、WF13、WF14 | 26 | 69 |
| B20-1 | DEV15~17、E2E-DEV | 13 | 82 |
| B20-2 | CNT18~20、HR21~23、E2E-CNT/HR | 28 | 110 |
| B21 | —（门禁批次，含 G3-10/G3-11） | 0 | 110 |
| B23 | —（界面复核，不新增 p0） | 0 | 110 |
| B24 | UI24、UI25 | 7 | **117** |

---

*编制：严过关（QA 工程师）· 2026-10-23 · v1.1 按架构 §9 重编号；判据来源 `docs/qa-phase3-test-plan.md`。*
