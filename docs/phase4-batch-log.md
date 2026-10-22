# 阶段四批次一 交付记录（T4-01 基座与在途整改收敛）

> 维护人：寇豆码（工程师）· 口径纪律：任何数字必须带 **SHA + 命令 + 是否含 PG + OA_BATCH + 耗时**（架构 §12 第 17 条），缺一不可引用。

---

## 1. 本轮入库的提交（5 个，按合入顺序）

| SHA | 主题 | 文件数 | 内容 |
| --- | --- | --- | --- |
| `873173c` | `fix(security): 生产环境禁止桩鉴权并校验签名密钥` | 4 | S1：`validate_auth_startup`（`env ∈ {staging,production}` + `stub` → 30004；同环境空 `jwt_secret` 同样拒绝）+ lifespan 接线 + 单测；`test_security_regressions` 的 `/healthz` 注入用例改 `staging+oidc+非空密钥` |
| `4404c72` | `fix(artifact): 下载令牌短 TTL、空密钥拒签与租户限流` | 10 | S9：令牌载荷加 `exp` 并强制校验（默认 900s、封顶 24h、可配）；空密钥拒签（删 `_DEV_KEY`）；端点改注入配置 + 「租户+IP」60 次/分限流 + 留存期判定；测试侧改用「产物身份」比对 |
| `e296a56` | `fix(deploy): 强制应用角色 DSN，禁止回落库属主` | 2 | S6：两份编排 `OA_DATABASE_URL` 改 `${OA_DATABASE_URL:?…}`（缺失即拒绝启动）；显式声明 `OA_ENV`/`OA_AUTH_MODE`；SaaS 停挂历史 `10-init-rls.sql` |
| `dfab8b9` | `ci(compliance): 补 PG 服务、合规门禁三修与部署形态门禁` | 5 | S7/S8：CI 补 PG 服务与 `OA_BATCH`；`pyyaml` 缺失致合规门禁恒红；删 MinIO 死规则、GPL 改认 `distribution: independent-container`；新增 `deploy/validate_deploy.py`（G-34，R1/R2/R3 + `--self-test`）并接进 CI |
| `e1c7abb` | `feat(contract): 阶段四错误码、配置基座与端点基线` | 8 | T4-01.2/.3：18001/18002/18003；`15006` → **402** + 新文案；4 档密钥 + 7 项保留期/记忆 + 5 项向量化配置；`baseline-phase4-10.json`；`validate_openapi` 四路扣除 + G6（登记严格/实现可见）+ `--strict` |
| `2699897` | `feat(db): 迁移锁与台账同事务、回滚命令，0006/0007 加性变更` | 14 | T4-01.4/.5：`pg_advisory_xact_lock(7326017)`；新脚本 DDL+台账同事务、旧脚本会话锁两步；`--rollback`（不级联）；`OA_DB_AUTO_MIGRATE=false`；`0003` 哨兵；`0006 content_type` + `0007 usage_event` 四列 + 两份 down；写入三处真实 MIME；`test_migration_lock.py` |

---

## 2. 全量测试与覆盖率（唯一权威口径）

| 项 | 值 |
| --- | --- |
| SHA | `main @ 2699897` |
| 命令 | `OA_TEST_DATABASE_URL='postgresql://officeai_app:***@127.0.0.1:55433/officeai' OA_BATCH=B21 pytest tests --run-pg -q --cov=app --cov-branch` |
| 结果 | **1749 passed / 0 failed / 0 skipped** |
| 耗时 | **683.86s（11m23s）** |
| 含 PG | **是**（`moy-qa-pg`，`--run-pg`） |
| `OA_BATCH` | **B21** |
| 解释器 | `apps/adapter/.venv/Scripts/python.exe`（3.13.14） |

### 三档覆盖率（coverage.py 7.x，`--cov-branch`）

> 首轮实测合并 **90.36%**（低于门槛 0.42pp），按主理人指示**先补测再判**（见 §2.1），
> 下表是补测后的**最终值**。

| 维度 | 本轮实测 | 门槛 | 判定 | 口径 |
| --- | --- | --- | --- | --- |
| 合并 | **90.94%** | ≥90.78% | **通过（+0.16pp）** | `totals.percent_covered`（14806 语句 / 1048 未覆盖 / 3078 分支） |
| `business+db` | **92.27%** | ≥90.26% | 通过（+2.01pp） | `app/business/*` + `app/db/*` 共 48 文件：行覆盖 |
| 分支 | **86.13%** | ≥80.89% | 通过（+5.24pp） | 2505 / 3078 个分支 |

#### 2.1 首轮到最终值：补了什么（`32cf676` + 本次提交）

| 文件 | 首轮 | 补测后 | 补的内容 |
| --- | --- | --- | --- |
| `app/db/migrate.py` | 81.49%（59 行未覆盖） | **97.67%**（7 行） | `--rollback` 的拒绝/跳过/级联分支、`apply_migration` 两条锁路径（xact / session）、`apply_rollback` 的 asyncpg 路径（fake connection）、强制备份的四个失败出口、CLI 的 `--rollback` 三种出口（打印 / `--json` / 退出 1） |
| `app/api/v1/artifact_files.py` | 81.25% | **100%** | `_is_expired` 五个分支（空值 / 无时区 / 不可解析 / 已过期 / 未过期） |
| `app/db/repositories/artifact_repo.py` | 95.71% | 95.71% | 未覆盖的 5 行是历史脏数据兜底分支（`# pragma: no cover` 邻域），不影响判定 |

**门槛没有下调，数字没有改口径。**

### 合并覆盖率未达门槛的原因（不美化、不调门槛）

本轮新增/改动代码集中在 `migrate.py`，而它的**回滚与异步执行分支**只有真库 CLI 路径
（`validate_migrations.py --run-pg`，不在 pytest 内）会走到：

| 文件 | 行覆盖 | 未覆盖行 | 说明 |
| --- | --- | --- | --- |
| `app/db/migrate.py` | **81.49%**（59/348 行未覆盖，分支 94/122） | `477-478, 504-522, 668-707…` | `--rollback` 的「无 down 脚本 / 更晚版本仍应用（拒绝级联）/ 版本未应用」三条分支，以及 `apply_rollback` 的 asyncpg 路径 |
| `app/api/v1/artifact_files.py` | 81.25%（4 行） | `110,113,114,116` | 留存期过期与限流的 404/429 出口（真库用例覆盖了主路径） |
| `app/db/repositories/artifact_repo.py` | 95.71%（5 行） | `620-624, 698, 732` | 老库缺列的回落与脏数据分支 |
| `app/core/errors.py` | 99.21%（1 行） | `208` | — |
| `app/core/config.py` | 97.35%（2 行） | `343, 399` | — |
| `app/core/security.py` | 95.58%（7 行） | — | 与本轮无关的既有分支 |

**已按主理人指示补测**（见 §2.1），合并覆盖率回到 90.85%，三档全部达标。

---

### 2.2 前端禁词门禁（回归验证，非新门禁）

真实门禁一直在 `apps/web/scripts/check-banned-terms.mjs`（架构文档里的
`scripts/check-frontend-terms.mjs` 与 `docs/api/term-blacklist.json` 是**幽灵引用**，
两者都不存在）。本次三条实跑：

| 命令 | 结果 |
| --- | --- |
| `npm run scan:banned` | **扫描 95 个文件，0 命中** |
| `npm run scan:words` | **词表与 PRD 一致（55 条词面）** |
| `node scripts/check-banned-terms.mjs --self-test` | **SELF-TEST PASS**：负样本 12/12 命中、正样本 8 条 0 误报、标识符 5 条 0 误报、模板样本 5 条全符合预期 |

⚠ 口径提示（写给 QA）：`--self-test` 退出码 **1 = 自检通过**（脚本头部注释第 19 行
明文规定，与 `deploy/validate_deploy.py --self-test` 同一约定）。因此
`npm run scan:self-test` 会打印 `npm error ... exit code 1`，**那不是失败**；
判据看输出里的 `SELF-TEST PASS`。若接 CI 应写
`node scripts/check-banned-terms.mjs --self-test; test $? -eq 1`。

记忆域扩词（T4-03.7）**正确顺序**：先改 `docs/prd-phase2-increment.md` §7 表格
（`check-banned-terms.mjs` 的 `PRD_PATH` 指向它，不是 phase4 PRD）→
`node scripts/check-banned-terms.mjs --update-words` → `scan:words` / `scan:banned` /
`--self-test` 三连。**禁止手改 `BANNED_TERMS`**（`--check-words` 会判漂移并退出 3）；
词源若加到 phase4 PRD 会静默生成不含新词的常量（假绿）。

## 3. 收敛过程中修掉的真实缺陷（全部在测试侧，均未削弱断言）

### 3.1 S9 拒签空密钥 → 20 条用例抛 `ArtifactTokenError`
`tests/unit/task_helpers.py::build_service` 的 `Settings(env="test")` 没有 `jwt_secret`，
而任务详情要为每个产物签下载地址。处置：**修夹具**补测试密钥；生产侧仍由
`validate_auth_startup` 保证非空（`test_production_oidc_requires_secret` 用**纯空白**
`"  "` 触发 `strip()` 分支，比空串更严）。

### 3.2 令牌带 `exp` 后「按字符串比 download_url」变成时钟抽奖
3 处 e2e 断言把响应里的 `download_url` 与现场重新签发的令牌做字符串相等。令牌加
`exp` 后同一产物跨秒签发必然不同——同一文件两次全量跑的失败数在 26/21 之间抖动，
就是这个原因。处置：改为按「**产物身份**（租户/产物/版本）+ 令牌仍有效」比对
（新增 `artifact_url_claims` / `assert_download_url_matches` / `checklist_identity`）。

### 3.3 「脚本自带 BEGIN」不能用字符串判（迁移机制的真缺陷）
`DO $guard$ BEGIN … END $guard$` 里的 `BEGIN` 是 PL/pgSQL 块关键字。按
`"BEGIN" in sql.upper()` 判会把 0003 起所有守卫式迁移误判成「旧脚本」，
DDL 与台账退回两步——正好是 T4-01.4 要修的缺陷本身。处置：改为行首语句正则
（`^\s*(BEGIN|COMMIT|ROLLBACK|START TRANSACTION)\s*;`）。

---

## 4. 需要下游知道的三条边界

1. **`usage_event` 四列不能写进 `init-schema-phase2.sql`**：它是迁移链基线的 checksum
   来源，已在每台库接管为真实摘要，改它 = 下次 `--apply` 判 drift（30004 起不来）。
   因此阶段四加性变更的前置落点放在 `init-schema-phase3.sql` 尾部（initdb 以超级用户
   执行、不参与 checksum）；`plugin_artifact.content_type` 直接建在 CREATE TABLE 里。
2. **回滚需要表属主权限**：`DROP COLUMN` 与「缺列补列」都要求 owner，`officeai_app`
   无此权限。`validate_migrations.py` 的路径 C（up→down→up）因此以**属主身份**执行
   ——真实运维就是 DBA 一次性动作（compose 的 `migrate` Job 同此设计）；应用角色的
   零 DDL 路径由路径 B 覆盖。
3. **测试库一次性准备**：`moy-qa-pg` 已按客户 DBA 的等价步骤补齐上述 5 列
   （`ALTER TABLE … ADD COLUMN IF NOT EXISTS`，以 `officeai` 执行），否则路径 B/C
   走不到零 DDL 分支。这是环境准备，不是代码绕过。

---

## 5. 未入库 / 待办

| 项 | 状态 |
| --- | --- |
| `docs/architecture-phase4.md` 等 6 份文档、`scripts/bench/` | 未入库（按主理人分工，文档由其另管） |
| `.seed.sql` | 本地手工造数脚本，无引用；已进 `.gitignore` |
| `apps/web/src/lib/business-errors.ts` | **待改（T4-02 提交 3）**：`15006` 文案仍是旧版（「…常规模式，升级套餐可恢复完整能力」），且缺 402 分支；`api-client` 还需把 HTTP 402 当业务响应解析。⚠ **提交 3 之前不要让任何人手工验收额度场景**——后端已返回 402 + 新文案，前端仍是旧映射，会看到「文案没变」的假象 |
| 合并覆盖率 | 已补测达标（90.85% / 92.09% / 86.01%），见 §2.1 |

---

# 阶段四 T4-03 记忆内核（提交 2 / 提交 3）交付记录

> 维护人：寇豆码（工程师）· 同样的口径纪律：**SHA + 命令 + 是否含 PG + OA_BATCH + 耗时**，缺一不可引用。

## 6. 本轮入库的提交（3 个，按合入顺序）

| SHA | 主题 | 文件数 | 内容 |
| --- | --- | --- | --- |
| `79250db` | `feat(memory): 后台任务引擎与事实抽取（PG 队列 + 6 类事实 + 降级）` | 18 | 提交 2：`background_job` 仓储（`FOR UPDATE SKIP LOCKED` + `locked_until` 租约，认领与租约同事务）+ 注册表式 `JobRunner`（`tick()` 可同步驱动、`stop()` 无残余协程）+ lifespan 接线；`MemoryExtractor` 走极薄 `ChatCompletion`（**不经 `ModelService.generate`**）+ 6 类事实 + 敏感丢弃 + 同 hash 合并 / `superseded_by` 链 / 200 条淘汰 + 四条降级 |
| `8249266` | `test(memory): 真库用例前置释放引擎，防事件循环关闭导致的跨文件假红` | 1 | 提交 2 收尾：`test_memory_job_pg.py` 的 `dispose_engine()` 由「仅用例后」改为「前后都释放」（既有写法只在后释放；前置才是幂等的，`-k` 重排/xdist 下同样成立） |
| `98d726d` | `feat(memory): 召回注入、降级与 MM1-MM4 端点` | 16 | 提交 3：`MemoryService.recall`（top_k=8 + 300ms 硬超时 + D-2/D-3 双路径降级）+ `ContextAssembler` L3 注入（只进上游 prompt）+ MM1-MM4 四端点（硬删含向量行、18002、审计只记动作与条数）+ `phase4_present` 4→8 + 抽取应用改为必填（无 key 不抽取不入队、只告警一次） |

## 7. 全量测试与口径（唯一权威）

| 项 | 提交 2 | 提交 3 |
| --- | --- | --- |
| SHA | `main @ 79250db` | `main @ 98d726d` |
| 命令 | `OA_TEST_DATABASE_URL='postgresql://officeai_app:***@127.0.0.1:55434/officeai' OA_BATCH=B21 pytest tests --run-pg -q`（**不带 cov**） | 同左 |
| 结果 | **1804 passed / 0 failed / 0 skipped** | **1830 passed / 0 failed / 0 skipped** |
| 耗时 | **425.14s（7m05s）** | **444.97s（7m24s）** |
| 含 PG | **是**（`moy-qa-pg-vec`，55434；`vector 0.8.7` / 40 表） | 同左 |
| `OA_BATCH` | B21 | B21 |
| 解释器 | `apps/adapter/.venv/Scripts/python.exe` | 同左 |

门禁：`ruff check .` / `ruff format --check .` / `mypy app` 三次提交后均全绿（`mypy` 覆盖 134 源文件）。

定向验收：

| 命令 | 结果 |
| --- | --- |
| `pytest -m memory -q`（提交 2） | 29 passed |
| `pytest tests/unit/test_job_runner.py -q` | 15 passed |
| `pytest -m memory -q`（提交 3） | **41 passed** |
| `pytest tests/unit/test_memory_recall_degrade.py -q` | 10 passed |
| `pytest tests/integration/test_memory_job_pg.py --run-pg -q` | 6 passed |
| `pytest tests/integration/test_memory_endpoints.py --run-pg -q` | 14 passed |
| `pytest tests/integration --run-pg -q --basetemp=/tmp/pyt-integ` | 632 passed（整目录真实排序，含 G-11 迁移幂等、schema 基线、会话 RLS、计费 PG） |
| `deploy/validate_migrations.py --run-pg` | `[OK]` 三路径实跑通过（空库 `--apply`×2、已初始化库 `--check/--apply`、`0007→0006` 回滚往返） |
| `deploy/validate_openapi.py --no-write-openapi` | `phase4_present=8`（未实现：TD1/TD2），G1-G6 通过 |

## 8. 与文档口径的 6 处偏离（均已获主理人批准，除第 5 条为既有形态）

| # | 文档口径 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | 提交 2 指令：「向量不可用时**跳过**语义去重，只按 hash」 | 保留架构 §3.3.1 的 `difflib ≥0.9` 降级 | 默认部署**没有** embedding 提供方（架构 A-1）；若"没向量就不去重"，PRD §4.3「同一事实重复出现要合并」在默认部署下永不生效，用户会看到同一条偏好出现五遍。`difflib` 无网络、结果确定、可离线单测。**已批准** |
| 2 | 提交 2 指令：「抽取失败 → job 按 `max_attempts` 重试」 | `memory_extract` 的 `max_attempts=1`（Runner 通用按 `max_attempts` 重试，该类策略即 1 次） | PRD §4.7 D-1 / 架构 §2.3 第 3 条 / §5.3 时序图三处一致：抽取失败"丢弃，下个周期再来"。重试只会重复烧注定失败的上游调用。**已批准** |
| 3 | lifespan 仅判 `OA_BACKGROUND_JOBS_ENABLED` ∧ `env != test` | 再加一条 `has_engine` | `business_store=memory` 下没有队列可消费，起轮询只是空转。**已批准** |
| 4 | PRD §4.3 的第三个触发条件（手动「立即整理」） | **不实现端点**，列 V1.1 | 端点预算固定 10 个（PRD §0.3）。**在此披露**，不做"偷偷加第 11 个端点" |
| 5 | 架构 §8.1 的 `memory_injected_total` 走 `/metrics` | 自制 stdlib 计数器注册表（`app/core/metrics.py`），**不新增 `/metrics` 端点** | 加端点会打破 `phase4_present` 与端点基线；导出器属 T4-05 交付物，两批各写一套必然漂移。本批只负责"把数记下来" |
| 6 | ——（实现细节） | 语义去重与召回降级的**告警只打一次**（进程级标记） | 召回每轮都跑，逐轮 WARNING 会淹掉真正的降级日志（「日志 1 条」也就无法断言）。意外异常与超时仍逐次告警 |

## 9. 需要下游（QA / 前端 / 提交 4）知道的四条边界

1. **`get_model_service()` 无参构造，其配置取进程级 `get_settings()`**（阶段二既有形态，非本批引入）：
   `app.state.settings` 的注入只到端点层，`ModelService`/`ContextAssembler` 用进程级配置。
   生产环境"进程级配置 = 部署环境变量"，语义正确；但**测试若要模拟"抽取应用已配置"，必须注入环境变量
   `OA_DIFY_MEMORY_APP_KEY` 并清配置缓存**（见 `test_memory_extract.py::_enable_extract_app`）。
   建议后续批次统一收口为「端点内 `get_model_service(get_request_settings(request))`」，本批未动以免波及既有 1804 用例。
2. **`memory_extract_every_turns` 的计数口径是"用户轮次"**（`count_role(role="user")`），不是
   `conversation.message_count`（后者含 assistant 行）；拿总条数当轮次会让触发频率翻倍。
3. **测试环境的宿主安全删除钩子会污染退出码**：`pytest tests/integration --run-pg -q` 曾出现
   632 个用例全绿但 `exit=1`、日志停在 `[100%]` 无汇总行——根因是钩子拦了 pytest 的临时目录清理
   （`[safe-delete] count=606 > threshold=50`）。加 `--basetemp=/tmp/...` 后同一批 `exit=0`。
   **QA 读结果请看汇总行，不要只看退出码。**
4. **`deploy/validate_migrations.py --run-pg` 必须显式带 `OA_TEST_DATABASE_URL`**，否则只报
   `[FAIL] 环境`；且不要用 `| tail` 读退出码（管道会吞掉非 0）。

## 10. 未入库 / 待办

| 项 | 状态 |
| --- | --- |
| `scripts/bench/` | 未跟踪，按分工不入库 |
| 前端「我的记忆」页（T4-03.6）、禁词门禁扩容（T4-03.7） | 未开始（不在提交 2/3 范围） |
| `MemoryService.recall` 的向量 provider（OpenAI 兼容） | 只有 `NullEmbeddingProvider`；`OA_MEMORY_EMBEDDING_ENABLED=true` 但未配齐时**打 WARNING 并回落不可用**，不静默假装可用 |
| 手动触发抽取、30 分钟闲置触发 | 列 V1.1 / 后续提交（见 §8 第 4 条） |
| `docs/api/openapi.snapshot.json` | 未重写（`--no-write-openapi` 下 G1-G6 全通过，新增端点为加性变更） |

---

# 阶段四 T4-03 提交 4 与记忆内核收口

## 11. 提交 4：「我的记忆」页与禁词扩词

| SHA | 主题 | 文件数 | 内容 |
| --- | --- | --- | --- |
| `3f234fb` | `feat(memory): 「我的记忆」页与禁词扩词` | 19 | 提交 4：`/profile/memories` 页 + 3 个记忆组件 + 文案单一来源 + `memoryApi` / 类型 / 查询键 + 侧栏入口 + 三态门禁登记 + PRD §7 记忆域一行 + 词表重生成（58 条）；MM1 加性补 `category_switches`；`core/metrics.py` 写死 §8.1 命名约束；新增单测 69 条 |
| `（见下条）` | `docs(phase4): …` | 1 | 本节与 §12-§16 |

### 11.1 前端门禁（全绿）

| 命令 | 结果 |
| --- | --- |
| `npx tsc --noEmit` | 0 错误 |
| `npm run lint`（`eslint . --max-warnings=0`） | 0 错误 0 warning |
| `node scripts/check-banned-terms.mjs` | **扫描 100 个文件，0 命中** |
| `node scripts/check-banned-terms.mjs --check-words` | **词表与 PRD 一致（58 条词面）**，退出 0 |
| `node scripts/check-banned-terms.mjs --self-test` | `SELF-TEST PASS`：负样本 12/12 命中、正样本 8 条 0 误报、标识符 5 条 0 误报、模板 5 条符合预期（**退出码 1 = 通过**） |
| `node scripts/check-page-states.mjs` | 18 个业务页三态齐备且错误态可重试（0 问题），含新增 `/profile/memories` |

### 11.2 记忆页的 6 套空态与黑盒化

| 状态 | 触发条件 | 文案（`lib/memory-labels.ts` 单一来源） |
| --- | --- | --- |
| 从未产生 / 尚未开始了解 | `enabled=true` 且列表为空且非"刚清空"且无筛选 | 「我还不太了解你」+「多聊几次，我会在这里记下能帮你省事的那些信息。」+「去看对话」 |
| 已全部删除 | 本页完成"删除全部"且刷新后仍为空 | 「你的记忆已全部删除」+「之后我会重新开始了解你。」 |
| 筛选无结果 | 选了类别且该类为空 | 「这个类别下还没有信息」+「换个类别看看…」 |
| 已关闭 | `enabled=false` | 顶部横幅（PRD §6.1 原文）+「记忆已关闭」+「重新开启」 |
| 暂时取不回 | 接口成功但无 `data`（防御分支） | 「关于你的信息暂时没能取回来」+「稍后重新打开这一页就会再试一次。」 |
| 加载失败 | 请求错误 | `ErrorState`（页面自身渲染，**必带重试**） |

**两处刻意的取舍**（写法进代码注释，此处留证）：

1. **"抽取未配置"不单独暴露**：后端不会把 `OA_DIFY_MEMORY_APP_KEY` 是否配置告诉前端，
   页面用「我还不太了解你」覆盖"尚未产生"与"尚未配置"两种情形——用户看到的是同一件事，
   而技术原因只进后端日志（`memory_extract_disabled_no_app_key`，进程内一次）。
2. **"向量降级"不在本页出现**：PRD §4.5 允许的唯一降级文案是「这次的回答没有用到过往信息」，
   它属于**对话页**的语义（D-2/D-3 影响的是"这一轮回答"），不属于"我的记忆"页——
   本页只展示"我记住了什么"。降级信息只进指标 `memory_recall_degraded_total` 与日志。

## 12. 全量与覆盖率（T4-03 收尾，权威口径）

| 项 | 值 |
| --- | --- |
| SHA | `main @ 3f234fb` |
| 命令 | `COVERAGE_FILE=/tmp/.cov_final2 OA_TEST_DATABASE_URL='postgresql://officeai_app:***@127.0.0.1:55434/officeai' OA_BATCH=B21 pytest tests --run-pg -q --cov=app --cov-branch --cov-config=<greenlet+thread>` |
| 结果 | **1900 passed / 0 failed / 0 skipped** |
| 耗时 | **1071.72s（17m51s）** |
| 含 PG | **是**（`moy-qa-pg-vec` 55434，`vector 0.8.7` / 40 表） |
| `OA_BATCH` | B21 |

### 三档覆盖率

| 维度 | 本轮实测 | 门槛 | 判定 |
| --- | --- | --- | --- |
| 合并 | **91.28%** | ≥90.78% | 通过（+0.50pp） |
| `business+db` | **92.73%**（8695/9377 语句，51 文件） | ≥90.26% | 通过（+2.47pp） |
| 分支 | **86.36%**（3019/3496） | ≥80.89% | 通过（+5.47pp） |

### T4-03 增量文件的 diff 覆盖（逐个 ≥90%）

| 文件 | 行/分支 | 覆盖 | 未覆盖说明 |
| --- | --- | --- | --- |
| `app/business/memory_service.py` | 322 / 94 | **96.15%** | 余 9 行为防御性分支（并发让位、极端空结果） |
| `app/services/memory_extractor.py` | 183 / 56 | **97.91%** | 余 2 行为不可达裁剪分支（`content[:60].strip()` 为空） |
| `app/db/repositories/memory_repo.py` | 323 / 52 | **98.40%** | 余 6 行为向量表缺失时的告警回落 |
| `app/db/repositories/job_repo.py` | 207 / 40 | **91.50%** | 余 15 行为 PG `list_by_status` 与 JSONB 兜底 |
| `app/workers/job_runner.py` | 121 / 16 | **98.54%** | 余 1 行为停机时的分支出口 |
| `app/api/v1/memory.py` | 68 / 14 | **92.68%** | 余 3 行为幂等回放分支 |
| `app/schemas/memory.py` | 32 / 0 | **100%** | — |
| `app/services/memory_embedding.py` | 52 / 10 | **100%** | — |
| `app/core/metrics.py` | 62 / 14 | **100%** | — |

## 13. 覆盖率口径修正（**后续批次必须照此执行**）

本轮发现一个会长期误导人的测量问题：**默认的 `--cov` 在 asyncpg + SQLAlchemy 下会低估覆盖率**。

- 现象：`DELETE /model/memories/{id}` 的工厂函数**确实执行了**（日志有 `memory_deleted`），
  但默认模式下 `memory.py:129-136` 报"未覆盖"；给该文件加 30 行等价用例也不会变绿。
- 根因：SQLAlchemy async 通过 **greenlet** 切换执行栈，默认 `concurrency` 只认 thread，
  greenlet 里跑的帧丢失追踪。
- 单加 `greenlet` 会**反向**出问题：FastAPI 的 async 端点帧反而丢失 → 合并覆盖率从 89.82% 掉到 86.95%。
- **正确口径：`concurrency = greenlet,thread`（两者都要）**。同一份代码：默认 89.82% → 单 greenlet 86.95% → **两者 91.28%**。

> **已按主理人裁定落地**：`pyproject.toml` 的 `[tool.coverage.run]` 已写入
> `concurrency = ["greenlet", "thread"]`（单独提交 `test(cov): 修正 async 覆盖率口径`）。
>
> ⚠ **跨阶段数字不可比**：阶段三记录的 **90.78 / 90.26 / 80.89** 是**旧口径（仅 thread）**，
> 与阶段四起的新口径**不可直接比较**。门槛数值**不下调**——新口径只会更高，
> 属测量修正，不是放水。此后引用覆盖率必须注明"greenlet+thread 口径"。

## 14. 手工验收路径（QA 执行脚本）

> 三条都已在**接口层**有自动化等价断言（下表"自动化等价"列），手工验收的价值在于验证
> 前端交互与文案；若时间紧，可只跑手工第 1 条 + 自动化三条。

| # | 手工步骤 | 预期（用户可见） | 自动化等价 |
| --- | --- | --- | --- |
| 1 | `/profile/memories` 删掉任意一条 → 回 `/chat` **新开**一个会话问同样的问题 | 该条不再出现在新会话的回答里；记忆列表里也不再有它 | `test_delete_removes_fact_and_embedding`（删除后 `memory_embedding` 同 ID 行数=0、`count_active=0`）+ `recall` 只读 `superseded_by IS NULL` 的行 |
| 2 | 打开记忆页 → 关「从新对话里了解我」→ 回 `/chat` 新开会话 | 顶部出现「记忆已关闭」横幅；新会话的回答**不引用**任何已记住的信息 | `test_switches_off_no_injection`（关开关后 `injected_memory_count=0` 且 query 无记忆块） |
| 3 | 记忆页把筛选切到「工作习惯」→「删除某一类的全部」→ 确认 | 该类条目全部消失，其它类别仍在；刷新后依旧 | `test_delete_by_category_keeps_others`（`deleted_count=1`、另一类保留） |

## 15. T4-03 记忆内核小结（5 个提交）

| # | SHA | 内容 |
| --- | --- | --- |
| 1 | `4726264` / `a5575ec` | 迁移 `0009`（三表 + RLS + 向量列）、`0010`（任务表）；40 表基线与 vector 预检 |
| 2 | `79250db` / `8249266` | PG 队列 + `JobRunner`；6 类事实抽取、敏感丢弃、去重/取代/上限、四条降级 |
| 3 | `98d726d` / `116ca4f` | 召回注入（300ms 硬超时 + 三档降级）、MM1-MM4 端点、抽取应用改必填、指标埋点 |
| 4 | `3f234fb` | 「我的记忆」页 + 6 套空态 + 禁词扩词（词表 58 条） |

**PRD 判据对照**：6 类可解析 ✓ / 只抽 `role=user` ✓ / 敏感命中不落库且 `dropped_sensitive>0` ✓ /
同事实合并（同 hash）✓ / 事实变更写 `superseded_by` 链 ✓ / 单用户 200 条淘汰 ✓ /
四条降级（D-1 抽取失败、D-2 召回超时、D-3 向量不可用、D-4 写入失败）✓ /
关开关不注入 ✓ / 删除是硬删且含向量行 ✓ / 审计只记动作与条数 ✓。

**本批未做（对外披露，不藏在"未做"里）**：

| 项 | 归属 |
| --- | --- |
| 手动「立即整理我的记忆」触发 | V1.1（端点预算固定 10 个，PRD §0.3） |
| 会话 30 分钟闲置触发 | V1.1（PRD §4.3 的第三个触发条件） |
| OpenAI 兼容的向量 provider | 只有 `NullEmbeddingProvider`；开启但未配齐时**显式告警并回落不可用**，不静默假装可用 |
| MM 历史值列表接口 | MM1 只返回当前值；`superseded_by` 字段已透出，前端"你之前说过…"为前向兼容位 |
| `/metrics` 导出器 | T4-05.1（本批只落进程内计数器，名称/标签已与 §8.1 逐字对齐） |

## 16. 技术债（T4-03 期间新增，按影响排序）

| # | 项 | 影响 | 建议 |
| --- | --- | --- | --- |
| 1 | `get_model_service()` **无参**构造，配置取进程级 `get_settings()` | 注入式配置（`app.state.settings`）到不了 `ModelService`/`ContextAssembler`；生产正确，但测试必须靠环境变量模拟 | **V1.1 第一批**统一收口为 `get_model_service(get_request_settings(request))`，需一并回归既有 ~1900 用例 |
| 2 | 覆盖率口径未配置化（默认 thread） | 后续批次会继续读到"PG 路径未覆盖"的假象，误判为测试缺口 | 见 §13，`concurrency = ["greenlet","thread"]` 单独提交 |
| 3 | `app/workers/tasks/memory_extract.py` 覆盖 85% | 余 2 行为"任务参数缺失直接判失败"的防御分支 | 下一批补 1 条用例即可 |
| 4 | MM1 只返回当前值 | 历史值只在 `superseded_by` 字段上体现，无法列出"历史条目" | V1.1 随"记忆详情"一起做 |
