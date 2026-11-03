# 全仓审计与清理候选清单（第六轮）

- **审计对象**：`C:\Users\15864\Desktop\麦子`
- **审计日期**：2026-11-04（Asia/Shanghai）
- **代码基线**：`main` HEAD `83c6548`；工作区干净（`git status --porcelain` 空）
- **本轮侧重**：①代码实现与文档描述不一致 ②过期/不匹配文档 ③冗余失效测试脚本 ④冗余或版本不匹配的依赖
- **本轮性质**：审计阶段**只读取证**；清理执行记录见文末 §8（2026-11-04，已授权执行）
- **规模**：跟踪文件 888 个；`docs/` 下 Markdown 102 份；适配层 405 个 `.py`；前端 128 个 `.tsx`

---

## 0. 取证与基线（实跑，可复核）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 端点契约 | `python deploy/validate_openapi.py` | `core=30 phase2=38 phase3=16 phase4=10 w1=2`，未实现 0，**EXIT 0** |
| 后端测试（无 PG） | `OA_BATCH=B21 pytest tests -q` | **收集 2797 / 2559 passed / 238 skipped / 0 failed**，523s |
| 前端测试 | `npx vitest run` | **72 文件 / 1112 用例全过**，28.5s |
| 前端依赖树 | `npm ls --depth=0` | 无 extraneous / 无 missing |
| 后端依赖 | `pip check` + `uv lock --check` | 无冲突；锁文件与 `pyproject.toml` 一致 |
| 配置门禁 | `python deploy/validate_config_names.py` | EXIT 0（说明：该门禁**扫不到** `install.sh` 写入的死键，见 A6） |
| CI 实跑 | `gh run list` | 2026-11-03 两次 `success`：CI Adapter `41641650690`（18m58s）、CI Web `41641657153`（3m17s） |
| 文档引用 | 脚本扫描 `docs/**/*.md` 中反引号路径 | 见第 2 节（按文档分档统计） |

> 结论先行：**代码与门禁本身健康**（测试全绿、端点齐、依赖无冗余）。问题集中在**文档事实层**：
> 4 处对外口径与代码冲突（Dify 许可、测试数字、CI 状态、Milvus 版本）、1 组死配置自相矛盾、
> 以及历史设计文档中约 30 处指向已重命名文件的失效引用。**未发现需要删除的生产代码或依赖。**

---

## 1. 代码 ↔ 文档事实冲突（须更新，优先级最高）

| 编号 | 位置 | 问题 | 证据 | 建议 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| **A1** | `README.md:12`；`docs/architecture-v1.0.md:76,173` | 把 Dify 许可写成 **Apache-2.0** | Dify 现行为 **修改版 Apache-2.0**（Dify Open Source License）：多租户运营须书面授权、前端 LOGO/版权不可移除、外观专利条款。ADR-0002 §9.2、`docs/decisions/0004` §三 均已按修改版记录，且已拍板三条规避约束 | 改为「Dify（修改版 Apache-2.0，三条规避约束见 ADR-0002 §9.2）」。**对外文档的许可表述属合规口径，必须与 ADR 一致** | **P1** |
| **A2** | `README.md:23-24,235-236` | 测试数字停更在 2026-10-30（收集 2423 / 2211 passed / 212 skipped / 覆盖率 91.94%·85.87%） | 本轮实测 **收集 2797 / 2559 passed / 238 skipped / 0 failed**；前端 1112 用例。偏差 +346 条，且 README 自称「最新」 | ① 更新为本轮实测值并标注命令与日期；② **建议同时改为「唯一权威来源 = CI junit.xml 工件」**（`apps/adapter/README.md:194-201` 已是此写法），从机制上消除再次漂移 | **P1** |
| **A3** | `README.md:256`（§6.3 第 6 条） | 「**CI 未实际运行**：GitHub Actions 额度已用尽」 | `gh run list` 显示 2026-11-03 13:08 两个 workflow 均 `success`；本轮 HEAD 之前已有多次实跑 | 改为「CI 已于 2026-11-03 实跑通过（run 41641650690 / 41641657153）；额度已恢复，后续以 CI 为准」。若仍有限制，改为写明「额度恢复后曾实跑，日常以本机门禁为准」 | **P1** |
| **A4** | `docs/architecture-v1.0.md:175` | 选型表写「向量库 **Milvus 2.x**」 | `deploy/images.lock:147-148` = `milvusdb/milvus:v3.0.3`；`deploy/docker-compose.yml:177` = `v3.0.3`；**同一文档** §版本史 v1.5 行已记「Milvus v2.4.6→v3.0.3」 | 改为「Milvus 3.0.3」；并把 Milvus（及 Keycloak/Dify）纳入 `deploy/validate_images.py` 的 `DOC_VERSION_FIELDS` 交叉校验——当前只校验 PostgreSQL，故此类漂移门禁抓不到 | **P1** |
| **A5** | `deploy/.env.example:105`、`deploy/install.sh:385-386`、`deploy/validate_config_names.py:145,459` | 死配置 `OA_OFFLINE_MODE` 仍在生成与写入，但**无任何消费方**；门禁注释却声称「已从白名单与注入里双双删除」 | 全仓只出现在 `.env.example` 赋值、`install.sh:385-386` 写入、以及门禁自身的注释与自检样本（`:145/:459/:528`）；`app/` 无对应字段，三套 compose 均不注入 | 二选一并保持三处一致：①**删除**（推荐，等离线包真正实现时再加，注释同步改为「曾删除，待实现」）；②实现消费方并纳入白名单。现状属「注释说已删、脚本仍在写」的自相矛盾 | **P1** |
| **A6** | `deploy/.env.example:449-450` | `OA_LICENSE_KEY` / `OA_LICENSE_SEAT_COUNT` 无任何消费方（全仓零引用） | `grep` 仅命中 `.env.example`；代码无 license 校验，`docs/platform-strategy-2026-11-03.md:17` 亦自陈「许可证/节点授权不存在」 | 标注「V1.1 预留，**当前不生效**」或直接删除；不得让运维误以为填了即受授权保护 | **P2** |
| **A7** | `docs/prd-phase4-increment.md:245` | 「风险面随端点数线性增长（**当前 84 个端点**）」 | 现为 **96 个**（`validate_openapi.py` 实跑） | 改为「当时 84 个，现 96 个」或直接 96 | **P2** |
| **A8** | `docs/architecture-v1.0.md:1423` | 「回归 **2211 过** / 0 败」 | 本轮 2559 passed | 去掉具体数字，改为「回归全绿（数字见 CI 工件）」或标注快照日期 | **P3** |
| **A9** | `deploy/validate_packaging.py:11` | 注释「**84 个端点**绝大多数起不来」 | 现为 96 | 改 96。仅注释，无功能影响 | **P3** |

---

## 2. 文档引用失真（指向不存在或已改名的文件）

统计口径：反引号包裹、以 `apps/`、`deploy/`、`docs/` 等仓库根路径开头、带文件扩展名的引用。

| 编号 | 位置 | 问题 | 建议 | 优先级 |
| --- | --- | --- | --- | --- |
| **B1** | `docs/architecture-phase3.md` | **95 条代码/测试路径引用中 26 条不存在**：`test_plugin_grants.py`、`test_plugin_version.py`、`test_three_plugins_e2e.py`、`test_dev_workbench_flow.py`、`repositories/task_repo.py`（已拆为 `repositories/task/`）等。文档 1553 行有「并入 `test_plugin_lifecycle.py` / `test_plugin_assembly.py`」映射表，但正文 1099/1144/1229 行仍按旧名罗列 | 在「测试清单」章节首部加一行**实现命名以 §映射表为准**的提示；或按映射表批量改名（26 处，成本中等）。**不建议删除这些段落**——它们承载验收口径 | **P2** |
| **B2** | `docs/architecture-phase2.md` | 4 条失效：`repositories/{billing,org,usage}_repo.py`（已拆子包）、`apps/web/scripts/scan-forbidden-words.mjs`（现为 `check-banned-terms.mjs`） | 更新为现路径 | **P3** |
| **B3** | `docs/architecture-phase4.md` | 4 条失效：`test_authn_bypass.py`、`test_metrics_labels.py`、`test_permission_whitelist.py`（已并入 `tests/security/`），`tests/unit/x.py` 为示例占位 | 前 3 条更新为等价实现路径；`x.py` 保留（占位示例） | **P3** |
| **B4** | `docs/architecture-phase3.md:1280` | 同一文档内路径写法不一致：1257/1284 行写 `apps/adapter/scripts/check_error_catalog.py`，1280 行写 `scripts/check_error_catalog.py` | 统一为 `apps/adapter/scripts/...` | **P3** |
| **B5** | `docs/qa-phase2-test-plan.md:110-113` | G-15~G-18 命令缺 `cd apps/web` 前缀（G-14 有），读者按根目录执行会报「文件不存在」 | 补 `cd apps/web &&` 前缀 | **P3** |
| **B6** | `docs/prd-phase4-increment.md:1333`；`docs/qa-phase2-test-plan.md:1412` | 引用从未存在的产物：`docs/qa-phase4-test-plan.md`、`docs/qa-phase2-acceptance-report.md`（`git log --all` 均无记录） | 改为「未产出（V1.0 不适用）」或删去引用；不要保留「应该有」的文件名 | **P3** |

> 已核实**不是**问题的引用（勿误删）：`docs/phase4-delivery-manifest.md` 的 8 条「不存在路径」均在同行注明
> **「已建（等价）」并给出等价实现路径**；`docs/phase3-acceptance-report.md:234` 已自陈两处引用失真并给出正确文件。

---

## 3. 过期 / 冗余文档（建议归档或建索引）

| 编号 | 对象 | 现状 | 建议 | 优先级 |
| --- | --- | --- | --- | --- |
| **C1** | `docs/audit-2026-10-30-full-repo-audit.md`、`docs/audit-2026-10-31-full-repo-audit.md` | 两轮历史审计，**无任何入链引用**（除自身） | 移入 `docs/archive/`（与既有 18 份归档同规格） | **P2** |
| **C2** | 4 份无入链文档：`gap-matrix-2026-11-03.md`、`project-brief-2026-11-03.md`、`remediation-plan-2026-10-29.md`、`research-2026-10-25-evoflow-workbench-comparison.md` | 内容有效但无人引用，新人不可发现 | **新建 `docs/README.md` 文档索引**（按「现行规范 / 阶段验收 / 审计与整改 / 研究与决策」四类登记）。比逐个补链接更可持续 | **P2** |
| **C3** | `README.md` 目录树 | 只列到 `docs/adr/`，未列 `docs/decisions`、`docs/archive`、`docs/compliance`、`docs/security`、`docs/api` | 补全，或改为「文档全貌见 `docs/README.md`」 | **P3** |
| **C4** | `docs/audit-2026-11-02-*.md`（1 份总报告 + 3 份分册） | 同批次、互有引用，非冗余 | **保留**，仅在索引中标注「总报告 + 三份维度分册」 | — |

---

## 4. 测试脚本与用例（结论：无需删除，仅需登记）

| 编号 | 对象 | 核查结果 | 建议 | 优先级 |
| --- | --- | --- | --- | --- |
| **D1** | `qa/phase4-verify.sh`、`qa/goldset/`（20 用例 + `run.py`）、`scripts/bench/`、`scripts/demo_phase3.sh`、`scripts/gen_delivery_manifest.py` | 全部**有活跃引用**：前两者被 `ci-adapter.yml`、`architecture-phase4.md` §9.7 引用；bench 被 `docs/perf-disclosure.md` 引用；demo 被阶段三验收引用 | **保留**，不属冗余 | — |
| **D2** | `apps/adapter/qa_probe/{bootstrap_dify,integration_real,mock_llm}.py`（590 行） | 阶段一手工联调探针，仍可运行，但**无自动化入口**，易被误判为失效脚本 | **保留**，在 `docs/README.md` 或 `apps/adapter/README.md` 登记「前置条件 + 运行方式 + 适用阶段」 | **P3** |
| **D3** | `tests/integration/selftest_plugin_contract.py` | 文件名不带 `test_`，**故意不被收集**（避免污染「0 skipped」口径），文件头已写明 | **保留**；建议在 QA 清单登记按路径运行命令 | **P3** |
| **D4** | 全部后端用例 | 扫描结果：**0 个空断言/`pass` 测试**；无永久 `skip`；238 skip **全部**为「无 PG 时按设计跳过」 | **无需清理** | — |
| **D5** | 3 条历史 stash（`stash@{0}` 自带「SyntaxError:505，勿直接 apply」警告） | 与当前主线无关 | **保留**，建议在台账登记或打 tag，防止误 apply | **P3** |

---

## 5. 依赖与环境（结论：无冗余包，仅 3 处小项）

| 编号 | 对象 | 核查结果 | 建议 | 优先级 |
| --- | --- | --- | --- | --- |
| **E1** | 前端 14 个运行时依赖 | 全部有实际 import：`@tanstack/react-query`(28 文件)、`lucide-react`(50)、`zustand`(2)、`sonner`、`date-fns`、`cva`、`clsx`、`tailwind-merge`、Radix×2 等；`tailwindcss-animate` 由 `tailwind.config.ts:83` 引用（`animate-in` 类依赖它） | **全部保留，无未使用包** | — |
| **E2** | 后端 16 个直接依赖 | 全部有 import；唯一无直接 import 的是 `python-multipart`（FastAPI 表单/文件上传的**运行时**依赖，删了上传即 500） | 保留；在 `pyproject.toml` 该行补一句「无直接 import，供框架解析 multipart」 | **P3** |
| **E3** | 锁与版本 | `uv lock --check` 通过；`pip check` 0 冲突；`npm ls` 无 extraneous；声明与实际安装一致（fastapi 0.156.8 / starlette 1.10.0 / pydantic 2.14.1 / PyJWT 2.15.1） | **保留**，无需动版本 | — |
| **E4** | `@types/node ^20` vs Node 22（README 推荐、CI 用 22） | 类型定义落后运行时 2 个大版本 | 对齐为 `^22`（低风险；需 `npm install` 更新锁） | **P3** |
| **E5** | 本地残留产物：`.coverage`(106KB)、`apps/adapter/coverage-audit.json`(2.5MB)、`coverage-pg.json`(2.5MB)、`deploy/backups/*.log` | 均已被 `.gitignore` 覆盖，不入库 | **删除本地文件**（释放约 5.1MB，不影响仓库与构建） | **P3** |
| **E6** | `deploy/offline/plugins/deepseek-0.0.25.difypkg`（185KB，已入库） | 无路径引用，但为 `bootstrap_dify.py --pkg` 的离线物料 | **保留**，在 `deploy/offline/README.md` 登记用途与来源 | **P3** |

---

## 6. 建议执行批次

| 批次 | 内容 | 涉及文件 | 风险 | 验证 |
| --- | --- | --- | --- | --- |
| **批次 1（必做）** | A1~A4：Dify 许可、测试数字、CI 状态、Milvus 版本 | `README.md`、`architecture-v1.0.md` | 极低（纯文本） | 改后全文检索确认无残留旧表述 |
| **批次 2（必做）** | A5：`OA_OFFLINE_MODE` 三处对齐（推荐删除） | `.env.example`、`install.sh`、`validate_config_names.py` | 低（死键，删除不影响运行） | `validate_config_names.py` 与 `--self-test` 均 EXIT 0 |
| **批次 3（建议）** | B1~B6：失效引用与路径写法 | 4 份文档 | 低（纯文本） | 复跑第 2 节扫描脚本，缺失数归零 |
| **批次 4（结构）** | C1~C3：归档 2 份审计、新建 `docs/README.md` 索引、README 目录树 | `docs/`、`README.md` | 低（移动文件需同步入链） | 全仓 `grep` 旧路径为 0；`git status` 仅预期变更 |
| **批次 5（可选）** | A6~A9、D2/D3/D5、E2/E4/E5/E6 | 多处 | 低 | 后端 2559 / 前端 1112 / 门禁 16 项全绿 |

**不执行的动作（明确排除）**：删除任何生产代码、测试用例、门禁脚本、`docs/` 中的阶段验收报告与历史审计、`deploy/offline` 物料、3 条 stash。

---

## 7. 清理后验证清单（硬门槛）

```bash
# 后端
cd apps/adapter && ruff check . && ruff format --check . && mypy app
OA_BATCH=B21 pytest tests -q            # 期望 2559 passed / 238 skipped / 0 failed

# 前端
cd apps/web && npm run lint && npm run typecheck && npx vitest run   # 期望 1112 passed

# 门禁（16 项）
bash qa/phase4-verify.sh --pytest-mode skip
```

> 只有在上述四项全绿、且 `git diff` 仅含本文档列明的变更时，本次清理才算完成。

---

## 附录：本轮扫描方法

1. 文档路径有效性：正则提取反引号内仓库根路径 → `os.path.exists` 逐一核对；按文档统计缺失率。
2. 文档入链率：把 `docs/*.md` 的文件名在全仓 `.md/.py/.yml` 文本中反查，零命中记为孤立文档。
3. 依赖使用率：Python 用 AST 遍历 `app/`、`tests/` 的 `Import/ImportFrom`；前端用全仓 `grep -F` 命中包名。
4. 死配置：提取 `.env.example` 的 `OA_*` 键 → 在 `deploy/**`、`apps/**`、`scripts/**`（排除样例文件）反查消费方。
5. 测试有效性：AST 扫描 `test_*` 函数是否只有 `pass` / 无断言无调用；统计 `skip` 标记并逐条核对原因。
6. 审计报告只采信**本轮实跑输出**；历史报告结论仅作线索，不直接引用。

---

## 8. 执行记录（2026-11-04，已授权清理）

### 8.1 已执行

| 批次 | 条目 | 实际动作 |
| --- | --- | --- |
| 1 | A1~A4 | `README.md`：Dify 许可改「修改版 Apache-2.0」+ 指向 ADR-0002 §9.2；测试数字改为本轮实测 + 「CI 工件为唯一权威」；CI 状态改为「已实跑」并附 run id。`architecture-v1.0.md`：Dify 许可（3 处）、Milvus 3.0.3、删除固化测试数字 |
| 2 | A5~A9 | 删除死配置 `OA_OFFLINE_MODE`（`.env.example` / `install.sh` / `k8s app.env.keys` / `k8s configmap.yaml` 四处同步）；`OA_LICENSE_*` 标注「V1.1 预留、当前不生效」；`84 个端点` → 96（PRD + 门禁注释）；`architecture-v1.0.md` 去掉 2211 快照 |
| 3 | B1~B6 | `architecture-phase3.md` 增加「设计期命名 + 偏差登记表」阅读提醒；`architecture-phase4.md` 增加等价实现指引；阶段二文档 4 处旧路径修正；`check_error_catalog` 路径统一；QA 门禁命令补 `cd apps/web`；两处从未存在的产物引用改为事实描述 |
| 4 | C1~C3 | 2026-10-30 / 10-31 两轮审计 `git mv` 至 `docs/archive/`，并同步 11-02 报告中的引用路径；新建 `docs/README.md` 索引（规范/验收/审计/决策 + 工具脚本登记）；README 目录树补全 `decisions/`、`compliance/`、`security/`、`archive/` |
| 5 | D2/D3、E2、E5、E6 | `qa_probe/` 三脚本与 `selftest_plugin_contract.py` 在索引中登记用途与运行命令；`python-multipart` 补「不可删」注释；删除本地 5.1MB 残留产物与缓存；`deploy/offline/README.md` 同步死配置已删除的说明 |
| 连带 | 审计未列但同源 | `docs/deployment-guide.md` 移除 `OA_OFFLINE_MODE=true` 的操作指引；`docs/adr/0001`、`docs/decisions/0006`、`docs/gap-matrix-2026-11-03.md` 的 Dify 许可与死配置口径同步 |

### 8.2 未执行（附理由）

| 条目 | 理由 |
| --- | --- |
| E4 `@types/node ^20 → ^22` | 会触发 lock 重解析，属**独立依赖变更**，不应混在文档清理提交里；需单独 PR + CI 验证 |
| D5 三条历史 stash | 含明确「勿直接 apply」的半成品，删除不可逆；保留并在索引/台账可见 |
| C4 2026-11-02 四份审计 | 互有引用且是第四轮唯一完整证据链，保留 |
| 设计期路径（phase3 26 处等） | 文档已有《路径偏差登记表》与 §12.3 说明，**批量改名会制造新的失实**；改为显著提示，验收以代码为准 |

### 8.3 验证结果（全绿）

| 项 | 结果 |
| --- | --- |
| `ruff check` / `ruff format --check` | 0 告警 / 402 文件已格式化 |
| `mypy app` | Success，210 个源文件 |
| 后端全量（无 PG） | **2559 passed / 238 skipped / 0 failed**（收集 2797，513.8s） |
| 前端 `eslint` / `tsc` | 0 error |
| 前端 `vitest` | **72 文件 / 1112 用例全过**；stmts 89.78% / branch 85.06% / funcs 84.93% / lines 90.98% |
| `qa/phase4-verify.sh --pytest-mode skip` | **52 项 PASS / 0 FAIL** |
| `validate_config_names.py`（含 `--self-test`） | EXIT 0 / 自检通过 |
| 交付清单 `--check` | 119 行一致（因 `install.sh` 尺寸变化已重新生成） |

### 8.4 清理边界

- 未删除任何生产代码、测试用例、门禁脚本、阶段验收报告与离线物料。
- 本地删除项均为 `.gitignore` 覆盖的产物：`.coverage`、`coverage-audit.json`、`coverage-pg.json`、
  `deploy/backups/*.log`、`.pytest_cache` / `.mypy_cache` / `.ruff_cache`（约 5.1MB）。
- 清理后工作区仅含本次预期变更；构建产物（`node_modules` / `.venv`）未触碰。
