# 第四阶段验收报告（第 21-24 周 · 商用打磨与交付准备）

> **文档性质**：阶段门禁验收记录。所有数字为本机实跑产物，命令与目录范围随数字给出；引用时请连命令一起引用，脱离口径引用会得出错误结论。

| 项 | 内容 |
|---|---|
| 验收日期 | 2026-10-29 |
| 验收范围 | 第四阶段全部 31 个任务（T4-01.1 ~ T4-05.7）、119 项交付物 |
| 代码基线 | `6b7fa1a`（三轮审计整改入库）+ 本轮工作区改动（32 项，未提交） |
| 验收人 | 主理人齐活林（编排）+ QA 严过关（执行）；工程师寇豆码、架构师高见远交付 |
| **验收结论** | **✅ 通过（PASS）**：119/119 交付物已建、待建 0；门禁 16/16 exit 0；全量测试 2212 passed / 0 failed |

---

## 一、结论摘要

| 维度 | 目标 | 实测 | 结论 |
|---|---|---|---|
| 交付物清单 | 待建项为 0 | **119 行 / 已建 119（其中等价实现 8）/ 待建 0** | ✅ |
| 任务覆盖 | 31 个任务全覆盖 | **T4-01.1~T4-05.7 全部在清单中有覆盖** | ✅ |
| 端点契约 | core 30 + phase2 38 + phase3 16 + phase4 10 + w1 2 | `validate_openapi.py --strict` **exit 0**；未实现 0/0/0/0 | ✅ |
| 自动化测试 | 0 failed | **2212 passed / 212 skipped / 0 failed**（446.63s，exit 0） | ✅ |
| 类型检查 | 零问题 | `mypy app` **204 源文件零问题** | ✅ |
| Lint / Format | 全绿 | app / deploy / scripts / 全仓 **check 与 format 全 exit 0**（383 文件） | ✅ |
| 门禁脚本 | 16 个全绿 | **16/16 exit 0**；`--self-test` 分族全部符合 | ✅ |
| 部署编排 | 三套可解析 | base / lite / saas `config --quiet` **全 exit 0** | ✅ |
| 前端 | lint/tsc/test 全绿 | lint 0 / tsc 0 / **55 tests passed**；coverage **% Lines 5.97 / % Funcs 3.30**（阈值 5 / 2） | ✅ |
| 新交付物自检 | 可加载可运行 | 20 条金标合法 + `run.py --validate` 0 + 3 个 bench `--help` 0 | ✅ |

**一句话结论**：第四阶段「商用打磨与交付准备」目标达成——119 项交付物全部落地，**53 个验收检查点全 PASS**，带 PG 全量测试首次达到 **0 skipped**，升级/回滚脚本、SBOM、合规三件套、交付文档齐备，可进入 V1.0 发布流程。

> **可复跑性**：全部验收由 `qa/phase4-verify.sh` 驱动（342 行，LF）——`bash qa/phase4-verify.sh --pytest-mode both`，退出码 0=全 PASS / 1=有 FAIL / 2=用法错，53 个检查点逐条落 `summary.txt`（含命令、退出码、期望码、耗时、日志路径）。**验收结论不是一次性人工跑的产物，任何人可复现。**

---

## 二、交付物核对

### 2.1 清单口径

`docs/phase4-delivery-manifest.md` 由 `scripts/gen_delivery_manifest.py` 从**单一真源**生成（`architecture-phase4.md` §11 任务表 + `git ls-files`），状态列不接受手改。

```bash
python scripts/gen_delivery_manifest.py --check
# → exit 0
# [OK] 119 行；已建 119（其中等价 8）；待建 0；清单与磁盘一致
```

> **⚠️ 门禁语义边界（必须连读）**：`--check` exit 0 只证明「清单与磁盘一致」，**不证明每一项都已交付**。判定「已交付」需要另外三件事：① 交付物能被执行/读取；② 有测试或门禁覆盖它；③ 有验收证据。本报告第三章逐项给出。

### 2.2 本轮补齐的交付物（19 项真缺）

| 任务 | 交付物 | 状态 |
|---|---|---|
| T4-03.3 | `tests/e2e/test_memory_recall.py`（3 例） | ✅ 新建。经论证「确实无等价覆盖」：`test_memory_extract.py` 只到「抽→存」、`test_memory_recall_degrade.py` 用 `seed_fact()` 手工造记忆跳过起点、全仓 `grep 跨会话 tests/` 0 命中 |
| T4-04.2 | `tests/integration/test_quota_hardstop.py`（3 例） | ✅ 新建。硬止损钉在**真正成立的两路**（R3 上传 `multipart_calls==[]`、A1 任务 `gateway.calls==[]`） |
| T4-04.3 | `tests/integration/test_tenant_budget.py`（6 例） | ✅ 新建。80% → 恰好 1 条 notice；100% → `exhausted=True` + 上传/任务两路挂起 |
| T4-04.4 | `tests/integration/test_task_timeout_lock.py`（4 例，含 2 条 pg） | ✅ 新建。5s 预算 + 10s 步骤 → `elapsed<8s` 且 `status=failed`；并发推进 → 每 seq 只执行一次 |
| T4-05.5 | `deploy/upgrade.sh` / `deploy/rollback.sh` | ✅ 新建。`vector` 预检 exit 4、无自动回滚 exit 7、未设维护闸门 exit 3，全部实测 |
| T4-05.6 | `deploy/sbom/generate.py`、`deploy/validate_offline_pkgs.py`、`.github/workflows/sbom.yml`、`apps/web/src/app/(main)/about/page.tsx`、合规三件套 | ✅ 新建。SBOM **727 组件**（docker 19 / pypi 61 / npm 608 / difypkg 39，四层各 ≥1） |
| T4-05.7 | `docs/deployment-guide.md`(777 行)、`user-guide.md`(507)、`key-rotation-checklist.md`(558)、`perf-disclosure.md`(343)、`qa/goldset/*`(22 文件)、`scripts/bench/*.py`(4 脚本 + README) | ✅ 新建 |

### 2.3 清单失真校正（7 项「标待建实为已建」）

进场时清单标「待建 28 项」，经逐项核实**其中 7 项实为已建、只是文件名与清单不同**：

| 清单路径 | 实际实现 | 判据 |
|---|---|---|
| `app/core/permission_whitelist.py` | `app/core/security.py:238` `PERMISSION_WHITELIST` | 架构 §2.5 要求「全仓唯一位置」，独立文件反而违背设计 |
| `tests/unit/test_permission_whitelist.py` | `tests/security/test_permission_fail_closed.py`（9 条用例） | 含 `test_lock_file_matches_code` / `test_exactly_two_entries` |
| `tests/security/test_authn_bypass.py` | `tests/security/test_attack_token.py`（4 例） | 攻击 1&2 |
| `tests/unit/test_cross_tenant.py` | `tests/security/test_attack_cross_tenant.py`（1 例） | 攻击 3 |
| `tests/unit/test_object_privilege.py` | `tests/security/test_attack_object_scope.py`（5 例） | 攻击 4 |
| `tests/unit/test_quota_abuse.py`、`test_info_leak.py` | `tests/security/test_attack_quota_and_leak.py`（2 例） | 攻击 5 |
| `tests/unit/test_metrics_labels.py` | `tests/unit/test_metrics.py` + `test_metrics_contract.py` | 含 `test_rejects_unknown_labels` |

**这 7 项以 `EQUIVALENT_IMPLS` 机制登记**（清单生成器新增能力）：等价路径必须真实存在，否则判红——防止该机制被用来掩盖真缺失。已用负样本验证：把一条等价路径改成不存在的文件 → `--check` exit 1，报「等价映射不得用于掩盖缺失」。

**若照清单原样施工，会造出 7 个重复成果，其中 1 个（独立的 `permission_whitelist.py`）还违背「唯一位置」的设计约束。**

### 2.4 设计变更导致的 1 项移除

`app/workers/tasks/tenant_export.py` 从 T4-05.3「涉及文件」中移除，理由链：

- `tasks/__init__.py:12` 明写「`tenant_export` **刻意没有处理器**，TD1 改同步生成」
- TD1 能力已在 `tenant_data_service.py` 完整实现：`:84 MAX_EXPORT_ROWS=200_000`、`:1235` 导出包 TTL、`:973` 删除证明 TTL
- 有测试覆盖：`test_retention_export_delete.py` 15 条 PG 用例 + `test_tenant_data_service.py` 3 处 `MAX_EXPORT_ROWS` monkeypatch

工程侧曾新建 394 行实现该处理器，经核实为**死代码**（`assert_ttl_separation` / `register_tenant_export` 在 `app/`、`tests/` 零外部调用），已删除。判据：**删除前后全量测试数字逐字相同**，证明没有任何测试依赖该模块的行为。

> 处置选择说明：未采用「补薄处理器」（造死代码）、未采用「登记为等价实现」（会把过时设计固化进清单）、未采用「§13 不进 V1.0」（该能力确实交付了，只是路径不同）。采用「从任务表移除 + 行内注明变更原因」，因为它最准确：**这个交付物不存在，且不需要存在**。

---

## 三、门禁实跑证据

### 3.1 测试与类型检查

| 项 | 命令 | 结果 |
|---|---|---|
| 全量测试（无 PG） | `cd apps/adapter && OA_BATCH=B21 pytest tests -q --basetemp=/tmp/oapytest_lead` | **2212 passed / 212 skipped / 0 failed**，446.63s，**exit 0**（收集 2424） |
| **全量测试（带 PG）** | 同上 + `--run-pg` + `OA_TEST_DATABASE_URL` | **2424 passed / 0 skipped / 0 failed**，627.71s，**exit 0** |
| 三目录 | `pytest tests/unit tests/integration tests/security -q` | 2209 passed / 188 skipped / 0 failed，exit 0 |
| e2e 目录 | `pytest tests/e2e -q` | 3 passed / 24 skipped，exit 0 |
| 类型检查 | `mypy app` | **Success: no issues found in 204 source files**，exit 0 |

> 口径自洽：2209 + 3 = 2212、188 + 24 = 212，合计 2424。

> **⭐ 带 PG 那一行的意义**：`2424 = 2212 + 212`——**212 条 skip 全部变成真跑，skip 归零**。这是历次阶段验收都没做到的事（此前一直带着 210 条 skipped 交付）。`step1b.log` 中 `SKIPPED` 计数为 **0**，因此**不存在「原因不明的 skip」需要解释**。与历史基线（2198/210）相比，无 PG 口径 +14 passed / +2 skipped，带 PG 口径首次达到 **0 skipped**。

> **数字来源说明**：本表由主理人独立实跑定案（无 PG 口径），带 PG 口径由 QA 在冻结快照 `6b7fa1a` 上实跑并落盘 `/tmp/p4verify_frozen/step1b.log`。此前工程师报 2212/212、QA 曾推算 2210/214，为免报告出现多版本数字，由主理人重新跑全量定案——实测支持 2212/212，推算作废。

### 3.2 Lint 与格式（CI 口径，四组范围）

| 范围 | `ruff check` | `ruff format --check` |
|---|---|---|
| `apps/adapter`（app + tests，383 文件） | exit 0 | **exit 0**（383 files already formatted） |
| `deploy/`（18 文件） | exit 0 | exit 0 |
| `scripts/`（6 文件） | exit 0 | exit 0 |
| 仓库根全仓 | exit 0 | exit 0 |

### 3.3 门禁脚本（16 个）

```bash
# 全部带 OA_BATCH=B21；import_directions 与 repository_parity 带 --baseline
for s in validate_*.py; do python deploy/$s; done
# → 16/16 exit 0
```

`--self-test` 分族约定（本仓库两族并存，不可一刀切）：

- **族 A（自检成功 = exit 1）** 9 个：`batch_clock` / `deploy` / `gate_wiring` / `migrations` / `openapi` / `packaging` / `plugins` / `schema_retention` / `secrets`
- **族 B（自检成功 = exit 0）** 7 个：`config_names` / `deps` / `images` / `pg_markers` / `repository_parity` / `import_directions` / `offline_pkgs`

实测：族 A 全 exit 1、族 B 全 exit 0，无一 MISMATCH。

### 3.4 端点契约

```bash
python deploy/validate_openapi.py --strict --no-write-openapi
# → exit 0
# [INFO] 端点统计: core=30 phase2=38 phase3=16 phase4_declared=10 phase4_present=10
#        w1_declared=2 w1_present=2（未实现：phase2 0 / phase3 0 / phase4 0 / w1 0）
# [OK]   G1-G7 全部通过
```

### 3.5 部署编排

| 编排 | 命令 | 结果 |
|---|---|---|
| 全量栈 | `docker compose --env-file .env.example -f docker-compose.yml config --quiet` | exit 0 |
| 轻量栈 | `--env-file .env.lite.example -f docker-compose.lite.yml` | exit 0 |
| SaaS 栈 | `--env-file .env.example -f docker-compose.saas.yml` | exit 0 |

### 3.6 前端

| 项 | 命令 | 结果 |
|---|---|---|
| Lint | `npm run lint:web` | exit 0 |
| 类型 | `npm run typecheck:web` | exit 0 |
| 测试 | `rm -rf apps/web/coverage && npm run test --workspace apps/web -- --coverage` | **6 files / 55 tests passed**，exit 0 |
| 覆盖率 | 同上 | **% Stmts 5.82 / % Lines 5.97 / % Funcs 3.30**（阈值 `lines: 5` / `functions: 2`，通过） |

> **⚠️ 列名不可混用（本报告初稿在此处写错过一次）**：vitest 输出四列 `% Stmts | % Branch | % Funcs | % Lines`，阈值 `lines` 对应的是 **`% Lines` = 5.97**，不是 `% Stmts` = 5.82。初稿把 Stmts 当 Lines 引用，还据此得出「从 5.98% 降了 0.16 个点」——实际 `% Lines` 是从 5.98 降到 **5.97**，只动 0.01。**对外文档把 Stmts 写成 Lines 会被技术评审直接抓出来。**

> **覆盖率口径**：分母为全量源码（`coverage.all: true`）。约 6% 是**真实值**，不是退步——旧口径 44% 的分母只算 12.4% 的源码。阶段四补 `/about` 等页面后 `% Lines` 5.98 → 5.97，**降的不是覆盖率，是分母变大了**。门禁当前作用是「防继续下滑」，不是「质量达标」。

### 3.7 新交付物自检

| 项 | 结果 |
|---|---|
| `qa/goldset/` 20 条 yaml | 全部合法、`run.py --validate` exit 0（权重合计 100%） |
| `run.py` 缺凭据行为 | **exit 3** + 打印 `BLOCKED` + **不写任何文件**（连目录都不创建） |
| `run.py` 用例格式错 | exit 4（报「缺少维度」「用例集不完整」） |
| `scripts/bench/` 3 个脚本 | `--help` 全 exit 0；缺凭据 exit 3；坏样本文件 exit 2；0 成功样本 exit 4（**一律不写 JSON**） |

---

## 四、缺陷台账（本轮发现与处置）

### 4.1 门禁自身的缺陷（3 处，均已修）

| # | 缺陷 | 影响 | 处置 |
|---|---|---|---|
| G-1 | `deploy/` 下 14 个校验脚本从未被 lint（CI 的 `ruff check .` 工作目录是 `apps/adapter`） | 「守门的脚本自己是唯一无人看守的死角」 | 上轮已补 CI 门禁 2b |
| G-2 | `scripts/` 成为新的 lint 死角（11 errors） | 同类问题平移到另一目录 | 补 CI 门禁 2c + 清零 |
| G-3 | **`validate_pg_markers.py` 不认类级 `@pytest.mark.pg`**（AST 只判 `FunctionDef/AsyncFunctionDef`） | **pytest 认这个标记会正确跳过，门禁不认 → 合法写法被判红** | 改 `ast.iter_child_nodes` 递归下降实现类级作用域 + self-test 增类级正负样本 |

G-3 的修根因证据：**删掉工程师为过门禁而加的方法级临时标记后，门禁仍 exit 0**。不删的话，永远无法证明它真的支持类级。

### 4.2 本轮新增文件触发的失败（1 处）

`validate_pg_markers.py` 首轮 exit 1，2 条用例缺 pg 标记。归因证据：`git cat-file -e HEAD:…test_task_timeout_lock.py` → **not in HEAD**，确认是本轮新增文件而非既有债。修复过程中牵出上表 G-3。

### 4.3 一处诊断争议（记入教训）

`ruff format --check .` 报 1 文件待格式化，起初被诊断为「CRLF」。实际是**两个问题并存**：

| 问题 | 证据 | 说明 |
|---|---|---|
| 行尾为 CRLF | QA 实测 `CRLF=270 / LF=270`；`pyproject.toml:137` 要求 `line-ending = "lf"` | 真实存在 |
| **docstring 格式** | `ruff format --check` 报位置 `test_task_timeout_lock.py:1:41` | **列号说明不只是行尾** |

两个问题由**一次 `ruff format` 同时解决**（它既按 `line-ending="lf"` 归一，也调整格式）。修复后 `ruff format --check .` → **383 files already formatted / exit 0**，该文件测试 `2 passed / 2 skipped`（逻辑未变）。

**为什么值得记**：诊断过程中出现过三种说法——「CRLF」（只看到行尾）、「自愈」（看到后来绿了但没查原因）、「不是 CRLF」（只看到 `grep -c $'\r'` = 0，没注意列号）。三种都只说对了一部分。

**可复用的启发式**：`ruff format` 报错带**列号**（`:1:41`）时，行尾问题解释不了它——行尾差异不落在某一列上。列号 + 行尾同时出现 ⇒ 两个问题都要处理。

### 4.4 `.sh` 的 CRLF 会制造假绿（QA 自己踩到，价值很高）

QA 编写 `qa/phase4-verify.sh` 时第一版是 CRLF，bash **不报语法错误，而是静默跳过函数体**（函数直接返回 0）。结果是：**检查项没有真正执行，却被记成 PASS**，差一点让第 9c 项以假绿进入验收报告。

处置：转 LF，并在脚本入口加 CRLF 自检（检测到 `\r` → 打印原因 + **exit 2**）。

> **纪律**：`.py` 的 CRLF 会被 `ruff format --check` 抓到，`.sh` 的 CRLF **没有任何门禁会抓**，它只会让检查变成假绿。凡是新写的 shell 脚本，必须自带行尾自检或纳入检查。

---

## 五、方法与过程教训

### 5.1 清单是派生品，不是目的

进场时「28 项待建」经核实有 **7 项已建、1 项属设计变更**，真缺 19 项。若照清单原样施工，会造出 7 个重复成果 + 1 个死代码，其中「独立的 `permission_whitelist.py`」还违背「全仓唯一位置」的既有设计约束。

**教训**：清单是「从任务表 + 磁盘状态」派生的视图，它的价值在于**发现差异**，不在于**指挥施工**。拿到清单的第一件事是核实，不是执行。

### 5.2 死代码比「待建」更危险

394 行的 `tenant_export.py` 如果留着，半年后有人看到会以为异步导出已实现，然后在真实故障里发现它从未被调用。

**判据**：`app/` 下的模块若被测试间接覆盖，删掉它通常会让测试变红或导入失败。**删除前后测试数字逐字相同 ⇒ 没有任何测试依赖它的行为。**

> 表述修正：这证明的**不是**「它从未被收集」（`app/` 下模块本来就不被 pytest 收集，那是显然的事），而是「它不是任何被验证路径的一部分」。

### 5.3 修根因后必须删掉绕过手段

`validate_pg_markers.py` 修好后，工程师为过门禁加的两行方法级标记必须删掉——否则门禁仍然靠方法级标记过关，**永远证明不了它真的支持类级**。

**通用纪律**：临时绕过是允许的（先解红），但「根因已修」的验证必须包含「移除绕过手段后仍绿」。

### 5.4 记录「当前事实」的该更新，记录「历史事实」的不能动

用户 2026-10-27 裁决把对话超额策略改为「继续可用 + 限长」，架构文档 13 处判据需要回写。但同批出现的 5 处需分别处置：

| 类型 | 例子 | 处置 |
|---|---|---|
| 记录**当前**事实 | 错误码表、T4-04.2 判据 | 更新 + 加现状注 |
| 前提已失效但**代码仍在** | §7.3「402 是唯一业务性 4xx」 | **降级为历史**，不删（前端 `api-client.ts` 的防御代码还在，删了后人会困惑） |
| 记录**历史**事实 | 附录 F-20 审计证据行 | **一个字都不改**（动了就是篡改证据链） |

### 5.5 真事实 + 错因果 = 错结论（工程师寇豆码提出）

`ruff format` 失败被诊断成「CRLF 已自愈」。这里的每一步核对都**通过**——「文件现在没有 CRLF」是真的、「命令现在返回 0」也是真的；错只发生在**「用 A 解释 B」那一跳**：把「现在绿了」归因于「CRLF 自己好了」，而没查「是谁让它绿的」（实际是有人跑了 `ruff format`）。

**这类错误不会在自查时暴露**：自查只能验证事实，验证不了因果。它只能被外部打红才会发现（本次是 QA 的门禁）。

配套纪律不只是「先读报错坐标」，还有：**当自己的一手观察不足以为自己的结论背书时，主动请人复跑，而不是把「自查通过」当成证据闭环。**

### 5.6 守卫者也要被守卫

本轮三次遇到「守门者自身的盲区」：`deploy/` 脚本从未被 lint → `scripts/` 成为新死角 → 门禁自己判错合法代码。**每补上一个死角，要立刻问「同类死角还有哪里」**，而不是补完就收工。

---

## 六、未验证项与已知限制（交付时需向客户披露）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| U-1 | `upgrade.sh` / `rollback.sh` 端到端 | **未真跑** | 备份/迁移/冒烟/自动回滚需要停 `oal-proxy`/`oal-web`（当前栈 9 容器健康运行），需一个维护窗口。已用 `--dry-run` 打到实时库验证预检与命令拼装，退出码 3/4/7 全部实测 |
| U-2 | CI 全部改动 | **未在远端验证** | GitHub Actions 额度未恢复。本轮新增/修改的 CI 步骤（门禁 2c `scripts/`、`sbom.yml`）仅本地验证 YAML 可解析与命令可跑 |
| U-3 | ~~带 PG 全量测试~~ | **✅ 已完成** | 本机 `moy-qa-pg` 可用，带 `--run-pg` 实测 **2424 passed / 0 skipped / 0 failed**（627.71s）。212 条 skip 全部真跑，skip 归零 |
| U-4 | 前端覆盖率约 6% | **真实值** | `% Lines 5.97 / % Funcs 3.30`（阈值 5 / 2）。门禁作用是「防下滑」而非「达标」。达标路径是补 page/layout 级测试，属长期工作 |
| U-5 | `perf-disclosure.md` 的数字 | **结果表为空** | 按纪律：数字必须来自 `results/` 下实测产物，不得手填。压测方案已给出，需在目标环境执行 |
| U-6 | 第三方渗透测试 | **未做** | 自查覆盖 5 类攻击面 + 2 个补充域（58 条自动化用例），缺外部背书。计划 V1.1 前安排 |
| U-7 | 登录后业务页的 CSP 实际表现 | **未验** | 生产模式 `next start` 实测 10/10 路由零 CSP 违规、零 pageerror，但本机 OIDC 未配置，所有路由落到登录壳页。覆盖了 SSR + RSC 内联脚本 + Tailwind 这条最易被 CSP 打死的链路，**未覆盖**登录后的表单/轮询/SSE |
| U-8 | `CSP` 的防护强度 | **已知削弱** | `script-src` 含 `'unsafe-inline'`（无 nonce 方案下的必然妥协）；`connect-src 'self' http: https:` 几乎等于不限制，对「数据外发」的阻断价值有限。如实登记为已知取舍，非已解决 |

### 环境约束（不是产品缺陷，但会让人误判为失败）

| # | 现象 | 原因 | 处置 |
|---|---|---|---|
| E-1 | `pytest` 跑到 100% 但无 summary 行、exit 1 | 宿主 `safe-delete` 钩子拦截 pytest 清理临时目录（`count:61 > threshold:50`） | `--basetemp` 指到仓库外（如 `/tmp/...`） |
| E-2 | `npm run test -- --coverage` 首跑 exit 1 | vitest V8CoverageProvider 清理旧 `coverage` 目录被同一钩子拦（`count:162 > threshold:50`） | 先 `rm -rf apps/web/coverage` 再跑 |

> 两条都会让命令以 exit 1 结束，但**用例本身全过**。CI 环境无此钩子，不受影响。**不能把这两条写成「通过」**——把环境坑伪装成绿灯，下一个人会在这里浪费一小时。

---

## 七、放行结论

第四阶段四项核心目标全部达成：

1. **交付物齐备**：119/119 已建、待建 0；31 个任务全覆盖；清单与实际磁盘一致且经等价映射机制校验。
2. **质量门禁全绿**：16 个门禁脚本 exit 0；2212 passed / 0 failed；mypy 204 文件零问题；app/deploy/scripts/全仓 lint+format 全绿。
3. **交付能力就绪**：升级/回滚脚本（含 `vector` 扩展预检、维护闸门、无自动回滚版本的显式拒绝）、SBOM 四层可解析、合规三件套、部署/用户/密钥轮换/性能披露四份文档齐备。
4. **已知限制已披露**：U-1~U-8 逐条列明，E-1/E-2 环境约束与产品缺陷分开登记。

**准予进入 V1.0 发布流程。**

**放行条件**：U-1（升级/回滚端到端）与 U-2（CI 远端验证）必须在发布前各完成一次真实执行；U-5（性能披露数字）在对外披露前必须由目标环境实测填充，不得用估算。

---

## 八、修订记录

| 日期 | 修订 |
|---|---|
| 2026-10-29 | 初版。基于本轮四阶段收尾工作（19 项真缺补齐 + 7 项清单失真校正 + 1 项设计变更移除） |
