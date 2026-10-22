# 阶段三 B17 批次验收报告（插件域数据模型与市场/安装：PL1-PL5 / PL13）

| 项 | 内容 |
| --- | --- |
| 报告编号 | QA-P3-B17-R01 |
| 被测提交 | **`010db32`**（`feat(plugin): B17 插件域数据模型与市场/安装（PL1-PL5 / PL13）`） |
| 复核方式 | `git archive 010db32` 导出到 `/tmp/qa-b17` 独立复核（**未在主工作区跑**） |
| 归档身份 | `ARCHIVE_SHA=010db324e2c435b9ff5e1f1bed542c8e853c10d4`；`ARCHIVE_TREE=/tmp/qa-b17` |
| 验证日期 | 2026-10-22 |
| **判定** | **FAIL**（2 个 P1 代码缺陷 + 1 个 P1 门禁缺陷） |
| 路由 | **Engineer**（P3-B17-01 / -02）；**Engineer（门禁脚本）**（P3-B17-03）；QA 自查项 P3-B17-04 |

---

## 1. 实跑结果

| 项 | 命令 | 结果 | 判定 |
| --- | --- | --- | --- |
| 契约骨架 | `pytest tests/integration/test_plugin_contract_skeleton.py -q` | **3 failed / 4 passed / 3 xfailed / 0 skipped** | ❌ |
| B17 自带测试 | `pytest test_plugin_market.py test_plugin_install.py test_plugin_loader.py -q` | **46 passed** | ✅ |
| G-26 声明门禁（正常） | `validate_plugins.py --dir "$TREE/apps/adapter/app/plugins/manifests"` | exit **0**，6 份声明 | ✅ |
| G-26 自检 | 同上 `--self-test` | exit **1**（6 条负样本全拦截 + 干净声明通过） | ✅ |
| G-29 端点基线 | `validate_openapi.py --allow-missing-phase2 --allow-missing-phase3` | exit **0**；`core=30 phase2=38/38 phase3=6/14（未实现 0）`；G1-G5 全过 | ✅ |
| G-30 错误码三处一致 | `check_error_catalog.py --allow-pending b08,b09` | **exit 1**：`[FAIL] errors.py → business-errors.ts: 前端兜底表缺少以下码：17002` | ❌ 门禁缺陷（见 P3-B17-03） |

**扫描路径留证**（防假隔离）：`{"directory": "C:\\Users\\15864\\AppData\\Local\\Temp\\qa-b17\\apps\\adapter\\app\\plugins\\manifests", "registered": 6}` —— 指向**导出树**，非主工作区。

### 1.1 xfail 归属口径核对（B16=6 → B17=3）

| 延后用例 | TC | 归属 | 本次结果 |
| --- | --- | --- | --- |
| PL5 `already_installed` 常驻 | TC-P3-MK08-01 | B17 | 已实现 → **FAILED**（缺陷 P3-B17-01） |
| PL5 重复安装零增量 | TC-P3-MK08-06 | B17 | 已实现 → **FAILED**（同根因） |
| PL13 幂等 + 全量回读 | TC-P3-PL03-07 ⑥ | B17 | 已实现 → **FAILED**（缺陷 P3-B17-02） |
| PL14 未安装 → `16003` | TC-P3-PL03-11 | **B18**（PL14 挪批） | 仍 xfail ✅ |
| WF2 重渲染 / 只读拒绝 | TC-P3-WF12-05/06 | B19 | 仍 xfail ✅ |

`EXPECTED_XFAIL` 已由 6 下调为 **3**（`{"B18": 1, "B19": 2}`），与实际 `3 xfailed` 一致 —— 归属机制生效。

---

## 2. 缺陷清单

### P3-B17-01（P1）PL5 安装响应缺 `already_installed` 常驻字段

- **判据**：PRD 勘误 #10 ②「`already_installed` 为常驻字段（首次 `false`、重复 `true`），**不允许只在重复安装时出现**」；TC-P3-MK08-01 / MK08-06。
- **实际**：`InstallData.model_fields` = `{plugin_id, status, version, granted_capabilities, denied_capabilities}`，**无该字段**；重复安装也只返回同一结构。
- **影响**：前端 `if (data.already_installed)` 恒为 `undefined` → 重复安装无法收敛提示，用户会陷入"以为没装上→反复点"的循环（这正是 PRD 把它定为常驻字段的原因）。
- **证据**：`test_b1` / `test_c1` 均在此断言处失败：
  `assert 'already_installed' in {'denied_capabilities', 'granted_capabilities', 'plugin_id', 'status', 'version'}`
- **修复**：`InstallData` 增加 `already_installed: bool = False`；`PluginService.install()` 首次 `False`、重复 `True`。

### P3-B17-02（P1）PL13 写后回读只返回 3 项，非全量 6 项

- **判据**：架构 §5.2「PL13 三条配套约束 ②：**写后回读全量 6 项**（不是只回改的那一项），前端整体刷新，禁止局部拼状态」；TC-P3-PL03-07 ⑥。
- **实际**：`PUT /plugins/capability-policy` 首次写入返回 `data.policies` 长度 **3**（仅 `org.*`），而契约要求 **6**（个人 3 + 企业 3）。
- **影响**：前端按"整体刷新"实现时会把个人能力项丢掉 → 授权抽屉少 3 行；两处读源（PL4 返 6 项 / PL13 返 3 项）不一致，正是架构要避免的漂移。
- **证据**：`assert len(policies_first) == 6` → `assert 3 == 6`。

### P3-B17-03（P1，门禁）G-13 与架构口径冲突，导致**所有批次假红**

- **判据**：架构 §5.4 硬约束 ②「`17002` 是内部审计码：A3/A4 响应体、**`business-errors.ts`**、任何 `message` 中 **0 命中**（门禁 G-13 扩展 `--forbid-internal 17002`）」。
- **实际**：`check_error_catalog.py` 的子集校验要求「`errors.py` 的**每个**码都出现在 `business-errors.ts`」，因此把"应当 0 命中"的 `17002` 判成缺失 → `exit 1`；且脚本**没有**实现 `--forbid-internal 17002`。
- **实测**：`grep -c 17002 apps/web/src/lib/business-errors.ts` = **0**（实现是对的）。
- **性质**：**门禁比契约更严**——与 `404` 那次同类：判据写错方向，把正确实现判红。它会让 B17/B18/B19 每一批的 §0.1 通用门禁块都红。
- **修复**：脚本把 `INTERNAL_CODES = {17002}` 从"前端必须覆盖"集合中排除，并新增 `--forbid-internal 17002`：断言前端/响应体/`message` 中 0 命中。

### P3-B17-04（P3，流程）检查单 §0.1 的 G-29 命令缺 `--allow-missing-phase3`

- **实际**：按检查单原命令（仅 `--allow-missing-phase2`）跑 → `exit 1`（`phase3=6/14`）；补 `--allow-missing-phase3` 后 `exit 0`。
- **影响**：B17-B20 期间每批都会因此红，且红的原因与实现无关。
- **修复**：检查单 §0.1 的 G-29 行补 `--allow-missing-phase3`，并在注释标明「B21 收口后必须去掉」。

---

## 3. QA 自身缺陷（诚实记录）

**骨架前 4 条延后用例是"占位"（`pytest.fail(说明)`），不是真实断言** —— 这导致「xfail 6→2/3」在任何批次都不可能被验证：占位永远失败，标记永远是 xfail。**这是我设计骨架时的错误**，本次已修正：

- `test_b1` / `test_c1` / `test_c2` 改为**真实断言**（schema 字段 + 服务级重复安装审计/授权增量 + HTTP 幂等与回读）；
- `test_b2`（PL14，B18）/ `test_c3` / `test_c4`（WF2，B19）仍为占位，但已在登记表标注归属批次。

修正后本次才有意义地测出 P3-B17-01 / -02 两个缺陷 —— 这印证了一条判据设计原则：**占位的"延后用例"只能证明"还没做"，不能证明"做对没有"**。

---

## 4. 结论与路由

- **B17：FAIL**。功能面（市场/安装/授权/套餐拦截）自带 46 条测试全绿，但两条**契约级**判据未达标：
  1. PL5 缺 `already_installed`（P1）；
  2. PL13 回读只 3 项（P1）。
- **门禁侧**：G-30 假红（P1，门禁缺陷，需工程修脚本）；G-29 命令口径需补参数（P3，文档）。
- **路由**：P3-B17-01 / -02 → **Engineer**；P3-B17-03 → **Engineer（门禁脚本，B18 一并修）**；P3-B17-04 → **QA（检查单）**，已自行修正。

**复验条件**（下一轮最小确认）：修 `InstallData.already_installed` + PL13 回读 6 项后，骨架应为 **7 passed / 0 failed / 3 xfailed / 0 skipped**，且 G-30 `exit 0`。
