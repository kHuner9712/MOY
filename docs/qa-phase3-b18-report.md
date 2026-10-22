# 阶段三 B18 批次验收报告（生命周期 PL6-PL12 + PL14 授权写接口）

| 项 | 内容 |
| --- | --- |
| 报告编号 | QA-P3-B18-R01 |
| 被测提交 | **`79f868c`**（提交信息为 `docs(qa)`，实际含 B18 全量代码 2383 行 → 记为 **P2 流程问题**） |
| 复核方式 | `git archive 79f868c` → `/tmp/qa-b18`，QA 骨架文件取仓库 HEAD 版本覆盖后执行 |
| 归档身份 | `79f868c202ee80a40b2db08f9b966eb22311f470` / `/tmp/qa-b18` |
| 验证日期 | 2026-10-22 |
| **判定** | **FAIL**（D1/D2 已修复 ✅；但 3 项新/遗留问题阻断：G-13 未修、0003 空库路径失败、checksum 漂移） |

---

## 1. 主判据结果

| # | 判据 | 结果 | 判定 |
| --- | --- | --- | --- |
| 1 | `already_installed` 两路径（false/true）+ 重复安装授权不变 + 审计 1 条 | 探针实测：`FIRST_FLAG=False`、`SECOND_FLAG=True`、`AUDIT_INSTALL_COUNT=1`、`GRANTS_UNCHANGED=True` | ✅ **D1 已修复** |
| 2 | PL13 回读 **6 项** | 实测 6 项：`personal.knowledge.read` / `personal.tool.use` / `personal.file.read` / `org.directory.read` / `org.repo.write` / `org.external.publish` | ✅ **D2 已修复** |
| 3 | `0003` 空库路径 `test_g11_*` 两遍全绿 | **4 failed / 5 passed**：`0003 对账失败：缺少阶段三数据表（9 张）` + `migration checksum drift detected: 0003_phase3_plugin_tables` | ❌ **阻断** |
| 4 | `check_error_catalog --strict` exit 0 | **exit 1**：`[FAIL] errors.py → business-errors.ts: 前端兜底表缺少以下码：17002` | ❌ **D3 未修**（遗留） |
| 5 | `phase3` 基线计数 | `core=30 phase2=38/38 **phase3=14/16**（未实现 0）`；带 `--allow-missing-phase3` 后 exit 0、G1-G5 全过 | ⚠️ 见 §4 口径更正 |
| 6 | 骨架 | **8 passed / 2 xfailed / 1 xpassed / 0 skipped** | ⚠️ xpassed 属正常，待登记同步（§3） |

---

## 2. 阻断项

### P3-B18-01（P1）`0003` 仍是「只对账」，空库 `--apply` 必然失败
- **判据**：team-lead 明确「`0003` 空库路径：现在是**幂等建表 + 对账**，不许残留"只对账"分支」。
- **实际**：`deploy/migrations/0003_phase3_plugin_tables.sql` 文件头自述「**本迁移不建表**，只对已初始化库做一次只读对账」，判据 REAISE 缺表。
- **后果**：空库（新环境 / CI scratch DB）执行 `--apply` 必失败：
  `0003 对账失败：缺少阶段三数据表（plugin_catalog … plugin_artifact）`
  → `test_g11_apply_twice_on_empty_database_is_idempotent` 等 **4 条**红。
- **修复**：0003 改为 `CREATE TABLE IF NOT EXISTS`（幂等建表）+ 保留对账断言；或迁移链显式声明"须先执行基线 `init-schema-phase3.sql`"并让空库路径先应用基线。**二者必须选一，不能停在"只对账"。**

### P3-B18-02（P1）`migration checksum drift detected: 0003_phase3_plugin_tables`
- **现象**：CLI 用例 `--apply` 退出码 1，日志 `[FAIL] migration checksum drift detected: 0003_phase3_plugin_tables`（`--check` 通过、`--apply` 失败）。
- **可能根因**（需工程师确认，非我判定）：同一版本号 `0003` 的 checksum 与执行内容来自**两个不同文件**（基线 `init-schema-phase3.sql` vs `migrations/0003_*.sql`），或文件被 CRLF/空白归一化后两侧计算口径不一致。
- **影响**：即使修好 D4，`--apply` 仍会因漂移失败。

### P3-B18-03（P1，遗留自 B17）G-13 要求 `17002` 出现在前端，与架构"0 命中"冲突
- B17 已报，**B18 未修**：`--strict` 仍 `exit 1`，原因仍是 `前端兜底表缺少 17002`；而架构 §5.4 硬约束 ② 要求 `business-errors.ts` **0 命中**（内部审计码），实测前端 `grep -c 17002 = 0`（实现正确）。
- **性质**：门禁方向与契约相反 → B17-B20 每批假红。修复口径：`INTERNAL_CODES={17002}` 从前端覆盖集合排除 + 新增 `--forbid-internal 17002`。

---

## 3. 骨架与登记表（需 `-2-2` 同步，我不再编辑该文件）

| 项 | 实测 | 期望动作 |
| --- | --- | --- |
| `test_b1` / `c1` / `c2` | **PASSED** | 登记表 `status` 改 `landed`、填 `defect` 关闭信息 |
| `test_b2` | **XPASS**（PL14 已落地，标记仍在） | 摘 `xfail` 标记 + `status=landed` |
| `test_c3` / `c4` | XFAIL（B19 未实现） | 保持 |
| 常量 | 当前 `EXPECTED_XFAIL=3 / RED=3 / OPEN=6` | 应改 **2 / 0 / 2**，`OPEN_HISTORY` 追加 2、`RED_HISTORY` 追加 0 |

> 说明：仓库工作区里 `-2-2` 已把 b1/b2/c1/c2 改成 `landed` 并摘了 b2 标记，但 `expected_xfail.py` 常量未同步，导致 `d1` 报 `[骨架汇总 ⑩] 预期 red 3 条，实际 0 条`。**常量与登记表必须同一次提交改完**，否则 d1 必红。

---

## 4. 两处口径更正（判据本身写错，非实现错）

1. **「phase3 基线计数 16」在 B18 不成立**：B18 落地 PL1-PL5 + PL6-PL12 + PL13 + PL14 = **14 条**；WF1/WF2 属 B19。实测 `phase3=14/16`，带 `--allow-missing-phase3` 时 exit 0。**"16" 是 B21 收口口径，不应作为 B18 判据。**
2. **「PL13 与 PL4 的 `capability_key` 集合完全相等」不成立**：两者语义不同——PL13 是**企业级策略全量 6 项**（3 personal + 3 org），PL4 是**该插件声明的能力**（`plg_mail_draft` 只有 2 项）。实测 `PL13=6 项`、`PL4=2 项`，集合必然不等。
   **正确的"双读源不漂"判据是值一致**：对 PL4 中每个 `org.*` 能力，其 `org_switch_enabled` 必须等于 PL13 同键的 `enabled`。本次实测**成立**：
   - `org.repo.write=false` → PL4(dev_workbench) 读出 `False` ✅
   - `org.external.publish=true` → PL4(mail_draft) 读出 `True` ✅
3. **`switchable` 字段不存在**：team-lead 的 D2 口径提到「personal 项 `switchable=false`」，但 `CapabilityPolicyItem` 实际字段为 `{capability_key, display_name, enabled, updated_by_name, updated_at}`，架构 §5.2 的字段清单亦无 `switchable`。**需确认是"应新增"还是"口径口误"**；若新增，前端契约要同步。

---

## 5. 门禁假红排查（检查单 §0.5 必填行）

| 假红源 | 本批是否命中 | 处置 |
| --- | --- | --- |
| G-13 `17002` 方向相反 | **命中**（exit 1） | 已登记 P1，B18 未修，需排入下一批 |
| G-29 缺 `--allow-missing-phase3` | 命中（不加参数时 exit 1） | 检查单已修；本批按带参数口径记 exit 0 |
| G-26 缺省路径扫错树 | 未命中（本批均显式 `--dir`） | 待工程修缺省路径 |
| G3-12 无批次白名单 | 未运行（脚本未实现） | 规格与放行口径已在检查单 §0.1 |

---

## 6. 结论与路由

- **D1 / D2 修复已确认**（对应 B17 的 P3-B17-01 / -02 关闭）（`already_installed` 两路径 + 审计 1 条；PL13 回读 6 项 + 双读源值一致）。
- **B18 判 FAIL**：P3-B18-01（0003 只对账）、P3-B18-02（checksum 漂移）阻断 g11；P3-B18-03（G-13 假红，遗留自 B17）未修。
- 路由：P3-B18-01 / -02 → **Engineer**；P3-B18-03 → **Engineer（门禁脚本）**；骨架常量同步 → **QA(-2-2)**；两处口径更正 → **team-lead / pm**。

**复验最小集（下一轮）**：`test_g11_*` 两遍全绿、`check_error_catalog --strict` exit 0、骨架 `9 passed / 2 xfailed / 0 skipped`、`phase3=14/16`（带容忍参数 exit 0）。


---

## 7. QA 缺陷台账（本批，供 `tests/expected_xfail.py` 的 `defect` 字段引用）

| 编号 | 等级 | 标题 | 归属 | 状态 |
| --- | --- | --- | --- | --- |
| P3-B17-01 | P1 | PL5 响应缺 `already_installed` 常驻字段 | Engineer | ✅ B18 已修（实测 false/true 两路径） |
| P3-B17-02 | P1 | PL13 写后回读只返 3 项 | Engineer | ✅ B18 已修（实测 6 项） |
| P3-B18-01 | P1 | `0003` 仍是「只对账」，空库 `--apply` 必败 | Engineer | ❌ 未修 |
| P3-B18-02 | P1 | `migration checksum drift: 0003` | Engineer | ❌ 未修 |
| P3-B18-03 | P1 | G-13 要求 `17002` 出现在前端（与架构 0 命中相反） | Engineer（门禁） | ❌ 未修（遗留自 B17） |

> **用途**：`red` 条目的 `defect` 字段必须是上表编号之一，且**编号唯一、与 open 状态条目双向相等**——
> 否则"填个编号"只是填字游戏（编造编号台账里没有；复用编号则数量对不上）。
