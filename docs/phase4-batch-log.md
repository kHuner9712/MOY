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

---

# 阶段四 T4-04 档位路由、成本护栏与安全红线

## 17. 提交（按合入顺序）

| SHA | 主题 | 文件数 | 内容 |
| --- | --- | --- | --- |
| `3699589` | `test(cov): 修正 async 覆盖率口径（greenlet+thread）` | 2 | `[tool.coverage.run] concurrency = ["greenlet","thread"]` + 注释说明两种口径各自的漏追踪机理；batch-log 标注旧口径不可比 |
| `8e9f43f` | `feat(cost): 档位路由隔离、成本硬止损与租户预算` | 17 | T4-04.1 ~ .3：`ProfileRouter`（4 档 4 密钥 / fallback / 运行期 status / `/readyz`）、`check_generation_allowed`（402 + 上游 0 次）、`usage_event` 四列 + `schema_guard`、`0011_tenant_budget` + `budget_repo`（80% 提示 / 100% 拒绝） |
| （本次） | `feat(security): 单步超时、权限 fail-closed 与五类攻击用例` | 12 | T4-04.4 ~ .6：单步超时 + 推进锁、`require_permission` fail-closed + 2 条白名单 + 锁文件、`tests/security/` 五类攻击用例 |

## 18. 全量与覆盖率（greenlet+thread 口径）

| 项 | 值 |
| --- | --- |
| 命令 | `OA_TEST_DATABASE_URL='postgresql://officeai_app:***@127.0.0.1:55434/officeai' OA_BATCH=B21 pytest tests --run-pg -q --cov=app --cov-branch` |
| 结果 | **1962 passed / 0 failed / 0 skipped** |
| 耗时 | **1111.26s（18m31s）** |
| 含 PG | **是**（`moy-qa-pg-vec` 55434，41 表：0011 已应用） |
| 终验（跑后又补 5 条诊断/探测用例，不带 cov） | **1967 passed / 0 failed / 0 skipped，483.05s** |

| 维度 | 实测 | 门槛（阶段三旧口径） | 判定 |
| --- | --- | --- | --- |
| 合并 | **91.40%** | ≥90.78% | 通过（口径不同，见 §13） |
| `business+db` | **92.74%**（8875/9570，54 文件） | ≥90.26% | 通过 |
| 分支 | **86.62%**（2992/3454） | ≥80.89% | 通过 |

### 本轮新增文件的 diff 覆盖

| 文件 | 覆盖 | 说明 |
| --- | --- | --- |
| `app/business/usage_service.py` | **98.94%** | 预算判定 / 提示 / 维度透传 |
| `app/business/task_service.py` | **91.25%** | 单步超时 + 推进锁 |
| `app/core/security.py` | **93.80%** | fail-closed + 白名单 + 审计 |
| `app/db/repositories/budget_repo.py` | **94.34%** | 内存/PG 双实现 |
| `app/db/repositories/usage_repo.py` | **94.26%** | 静态双 SQL（带/不带四列） |
| `app/api/v1/router.py` | **92.08%** | `/readyz` 档位检查项 |
| `app/db/advisory_lock.py` | **92.00%** | 进程内锁 + `pg_try_advisory_lock` |
| `app/services/profile_routing.py` | **95.31%**（补诊断用例后） | 4 档密钥 / fallback / 可用性 |
| `app/db/schema_guard.py` | **86.11%** | 未覆盖 3 行为 PG **探测成功**分支（真库用例 `test_schema_guard_detects_columns` 覆盖，合并口径下归属不稳定） |

## 19. T4-04 后半的五组结果

| 组 | 命令 | 结果 |
| --- | --- | --- |
| 单步超时 | `pytest tests/unit/test_task_step_lock.py -q` | **6 passed**：慢步骤 → `failed` + 「超过 0.2 秒，已停止等待」，耗时 < 3s；第 1 步产物在后续步骤超时后仍可取 |
| 防重复推进 | 同上 | 并发两次推进 → **同一步只执行一次**（`calls` 无重复）、步骤序号不跳变；锁用完可重入获取；占用时第二次**立刻** False |
| fail-closed + 白名单 | `pytest tests/security/test_permission_fail_closed.py -q` | **9 passed**：3 个写端点全 403 + 每次拒绝 1 条 `security.permission_denied`；白名单恰好 2 条、仅 GET、锁文件一致、`permission_check_deferred` 全仓仅 1 处 |
| 五类攻击 | `pytest -m security -q --run-pg` | **21 passed**（伪造/过期令牌、跨租户读、越权对象、额度滥用 + 泄漏扫描） |
| 迁移 | `validate_migrations.py --run-pg` | 三路径通过（**10** 个迁移，含 0011 往返） |

### 19.1 反向验证（故意放开一处 → 只对应用例红）

把 `require_permission` 的无角色分支临时改成**恒放行**（复现 F-1 的缺陷形态），跑 `-m security`：

```
mutated → 4 failed, 16 passed, 1 skipped
FAILED test_write_endpoint_denied[PATCH kb] / [DELETE kb] / [DELETE grant]
FAILED test_denials_are_audited
恢复后 → 20 passed, 1 skipped
```

**只有** fail-closed 那 4 条红，其余安全用例（伪造令牌 / 跨租户 / 越权对象 / 额度）**全绿**——
说明这组用例彼此独立、定位精确，不会"一改就整片红"从而逼人放宽断言。

## 20. 两处口径与偏离（需要下游知道）

| # | 项 | 处理 | 理由 |
| --- | --- | --- | --- |
| 1 | token 估算**不**置 `source='estimated'` | 只写四列 + `usage_tokens_estimated` 日志 | `source` 表达**额度**来源（D-12）；复用它标 token 会把"中断折算"与"token 估算"混为一谈，直接打破 `test_b07_4`。**V1.1 成本分析债**：要区分就加独立列 `tokens_estimated` |
| 2 | 越权对象：任务 403，**会话/记忆 404** | 保持现状并在用例里写明 | 个人数据不能靠 `403 vs 404` 泄漏"ID 是否存在"（否则可枚举别人的会话数与记忆条目）。两者都**拒绝访问**，对外表现不同是**防探测**的既有决策 |
| 3 | `test_quota_notice` 两条用例改写 | 超额从"200 + 提示"→"**402 + 15006 + `data is None`**"；流式从"提示挂 `done`"→"**仅 `error` 帧**" | 阶段四 E-3/E-4 的产品变更（软限制→硬止损）；新断言更严，80% 预警提示由新增用例覆盖 |
| 4 | 白名单 `W-2` 以"权限点 + GET"表达"仅 TL1" | 该权限点全仓只用于 TL1 一个调用点（`tools_ext.py`） | 方法级收窄已足够；若将来 `tool:connection:manage` 被别处复用，必须回来加"路由级"约束（已在白名单注释里写明） |

## 21. 技术债（T4-04 新增）

| # | 项 | 建议 |
| --- | --- | --- |
| 1 | `tokens_estimated` 独立列 | V1.1 与 usage 维度一起做（需再动 `0007` 列集，须评审） |
| 2 | `schema_guard` 探测成功分支的覆盖归属不稳定 | 把「列能力探测」做成可注入的 `CapabilityProbe` 端口，便于单测直接覆盖两条分支 |
| 3 | 白名单的"路由级"约束 | 见 §20 第 4 条 |
| 4 | `get_model_service()` 无参构造（T4-03 遗留） | 仍是 V1.1 第一批 |

---

# T4-05 第二批：数据权利（保留清理 + TD1 导出 + TD2 删除）

## 22. 提交

| SHA | 说明 |
| --- | --- |
| `76f7794` | `test(obs): 告警规则自证` —— `deploy/alert.rules.test.yml`，promtool 合成序列钉住 5 条规则的时间语义 |
| `f30c95e` | `feat(ops): 数据保留清理与租户导出/删除（TD1/TD2）` —— `phase4_present` 8→10，端点预算清零 |

## 23. 告警规则自证（promtool，`76f7794`）

不用"打 30 次 5xx 再等 4 分钟"（慢且不可复现）：`promtool test rules` 用合成序列把
5 条规则的时间语义钉成离线可复跑的断言。跑法（Windows/Git-Bash **必须**
`MSYS_NO_PATHCONV=1`，镜像入口是 `prometheus`，须 `--entrypoint promtool`）：

```
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint promtool \
  -v "$PWD/deploy:/etc/prometheus" prom/prometheus:v3.15.0 \
  test rules /etc/prometheus/alert.rules.test.yml
→ SUCCESS
```

三条**实测踩出来**的语义写进了文件头（都是"写错了会假绿"的类型）：

| # | 语义 | 症状 |
| --- | --- | --- |
| 1 | `rate()` 只需窗口内 **2 个样本**，不等窗口填满 | 序列从第 0 分钟就有流量时，`rate(x[2m])` 第 1 分钟就算得出来 → pending 比真实场景早 1 分钟。故序列**前 2 分钟置 0**，才能验证"约 4 分钟 firing" |
| 2 | 常量计数器的 `rate()` **恒为 0** | `values: '80+0x8'` 得到全 0 分布，`histogram_quantile` 返回 NaN，`NaN > 3` 为假——"永不告警"的用例会**假装通过**。故延迟桶一律用递增序列 |
| 3 | `for` 边界（恰好满 `for`）**算 firing** | 用例刻意避开边界时刻（用 +1m 断言），只钉"之前不响、之后响" |

覆盖：`ApiErrorRateHigh`（10% 第 2/3 分钟不响应、第 5 分钟 firing；1% 永不）、
`TaskQueueBacklog`（51 第 4 分钟 pending、第 6 分钟 firing；50 不告警）、
`TenantBudgetNearlyLimit`（0.85 第 9 分钟 pending、第 12 分钟 firing；0.8 不告警）、
`TargetDown`（up=0 第 1 分钟 pending、第 3 分钟 firing）、
`ApiLatencyP95High`（非流式 P95≈4.7s 第 5 分钟 pending、第 7 分钟 firing；
只有流式路由慢时因 `route!~".*stream.*"` 保持 inactive）。

## 24. 保留期清理（`kind=retention_cleanup`）

| 类别 | 表 | 保留期（配置项） | 判据 |
| --- | --- | --- | --- |
| 审计 | `audit_log` | 180 天 `retention_audit_days` | **应用角色无 DELETE 权限** → 交 DBA（`sql/retention-audit.sql`） |
| 用量 | `usage_event` / `usage_monthly` | 13 个月 `retention_usage_months` | 按**自然月**回退（不是 30×N 天） |
| 任务与产物 | `plugin_artifact` → `plugin_task` | 180 天 `retention_task_days` | 产物先删（FK 是 `ON DELETE CASCADE`，先删任务会让"产物删了几行"变成 0） |
| 会话与消息 | `message` → `conversation` | 365 天 `retention_conversation_days`；软删 30 天后提前硬删 | `conversation_soft_delete_recover_days` |
| 记忆 | `user_memory` / `memory_embedding` / `user_memory_setting` | **永久** | 规则标记 `skip_permanent`，只由用户本人在「我的记忆」里删 |
| 运维流水 | `integration_outbox` / `payment_callback_log` | 前者 180 天、后者 13 个月 | 与任务 / 账单同口径 |

**三条设计决策**（写进代码注释，这里记结论）：

1. **每租户一个任务，而不是一个平台级任务**。平台级任务必须走 `platform_scope()` 的
   **写**路径，而那条通道的契约写的是"仅供运营后台"；每租户一个任务让每次删除都落在
   `tenant_scope(job.tenant_id)` 里，**RLS 天然兜底**（就算 `tenant_id` 传错也删不到别人的行）。
   代价是要枚举租户——枚举用一次**只读**的 `platform_scope()`（只 `SELECT tenant_id`），
   发生在 `schedule_all_tenants()`，与删除路径完全分离。
2. **`dedupe_key` 是固定串 `retention-daily`（不带日期）**。带日期会在"当天任务已跑完 +
   进程重启"时再排一个，于是同一时刻躺着两个待执行任务；固定键表达"该租户已有一次清理
   在排队"，重启多少次都只有一次。真实执行时刻记在 `payload.scheduled_for`。
3. **`audit_log` 仍然尝试删一次**，把权限判定交给数据库而不是在代码里写死"这表删不了"。
   这样一旦运维真的授予了权限（例如将来的 `officeai_maint` 角色），清理会自动生效，
   代码不用跟着改；失败即按预期记 `audit_log=skipped(no privilege)`。

日志与告警：每表一行 `retention_table_cleaned`（`table` / `deleted` / `duration_ms`），
失败一行 `retention_table_failed`（含 `error`）。**权限跳过不计失败**；
`job.attempts >= 2` 时把失败日志升到 ERROR（`job.attempts` 就是连续失败序号）。

## 25. TD1 导出：**同步生成**（本批的核心决策）

> **PRD P4-05 ③ 写的是"异步任务产出压缩包"。本批改为同步生成。**

| # | 理由 |
| --- | --- |
| 1 | **端点预算里没有"查导出进度"的位置**。阶段四只允许 10 个新端点，10 条已被 MC1-MC4 / MM1-MM4 / TD1 / TD2 占满。异步导出必须配 `GET /ops/tenant-data/export/{id}` 才能拿到结果，加它就是第 11 条，门禁 G1/G3 当场判红 |
| 2 | **没有进度端点时，异步比同步更糟**：任务排进 `background_job`、包也生成了，但用户没有任何合法手段拿到 `download_url`（下载令牌是短期凭证，不能靠"通知"送达）。那就是"排了队但取不到"，等于数据权利没兑现 |
| 3 | **量级可接受**：中小企业/私有化场景 ≤200,000 行，内存里拼 zip 是秒级操作 |
| 4 | **超限明确报错，不静默截断**：`数据量较大，请联系管理员分批导出`（复用 `20001`，不新造码）。截断的导出包会让客户以为"我的数据只有这些"，在合规场景里比报错严重得多 |

实现要点：5 类 JSONL（`audit` / `usage` / `chat` / `memory` / `artifact`）+
`manifest.json`（每文件行数 + sha256 + 字节数）。取数用**键集分页**
（`(ts, id) > (cursor_ts, cursor_id)`），不用 `OFFSET`——页数越多 `OFFSET` 越慢。
写 zip 用 `ZipFile.open(name, "w")` **流式**写，不把整包先拼进内存。
包落产物库（`application/zip`，`expires_at = now + 令牌 TTL`），
`download_url` 复用既有 `/files/artifacts/{token}`（S9 短期令牌）——
**不新增端点、不新增基线条目**。

`kind=tenant_export` 仍留在迁移 `0010` 的 CHECK 里，但**没有注册处理器**：
枚举是冻结的，删一个取值等于改契约；队列里不会出现该类任务，将来若要转回异步，
处理器位置现成。已在此处披露。

## 26. TD2 删除：先停用 → 分批硬删 → 出证明

1. **逐字确认**企业名称（`18003`）。**不 strip、不忽略大小写、全半角括号不同都拒绝**——
   二次确认的意义是"你必须真的知道自己在删哪家企业"，容错会让误删变成可能；
2. **先置 `tenant.status='suspended'`** 阻断后续写入。不先阻断就会"边删边写"，
   删完还剩新数据，客户拿到的"已删除"结论是假的；
3. 分批（每批 ≤1000）硬删 8 类：审计 / 用量 / outbox / 回调流水 / 凭据 / 会话消息 /
   记忆 / 产物（含「始终删除」的 `background_job` 与 `credential_store`：
   前者是纯运行态，留着会让处理器去处理已删除的会话；后者是密钥材料，留着既无用又是风险）；
4. **删除证明**（各表删除行数 + 时间戳 + 请求人 + 两段式 pending）作为产物落库，
   `proof_download_url` 同样复用 `/files/artifacts/{token}`；
5. **审计行删不掉，且不许合并进总数**：响应与证明里单列 `audit_pending_dba: N`
   + DBA SQL 提示（PRD P4-05 ④"分两段呈现、不得合并成一个数字"）。
   顺序上**先写审计动作、再数审计行**——反了会把"删除动作本身"漏在统计之外。

幂等：第二次删除不报错，业务数据仍为 0；**上一次的证明会被本次替换**
（证明表达"当前状态"，历史动作留在审计里，审计永不被应用侧删除）。
用例断言"二次 `deleted_rows` 不全为 0"，这是真实的：证明要重新落一份。

## 27. 五处口径偏离（下游必须知道）

| # | 项 | 架构 / PRD 原文 | 实际 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | TD1 状态 | `status="preparing"` + `estimated_rows` | `status="ready"` + `rows`（实测行数）+ `download_url` / `expires_at` | 同步生成完毕；返回 `preparing` 会让前端去轮询一个**不存在**的端点。包已经生成了，报估计值没有意义 |
| 2 | TD2 时间字段 | `scheduled_at` | `deleted_at` | 同步删除没有"计划执行时间"；返回未来时间戳会让客户以为"还没删" |
| 3 | `tenant` 的"标记删除" | 置 `deleted_at` | 置 `status='suspended'` + `suspended_reason='数据删除请求处理中'` | `tenant` 表**没有** `deleted_at` 列（阶段一定义的列集：tenant_id/name/status/deploy_mode/plan_code/suspended_reason/created_at/updated_at）。新增该列需要一次迁移 + 基线脚本同步，超出本批范围；`status='suspended'` 是既有的"阻断使用"机制，语义等价。**V1.1 若要 `deleted_at`，须走迁移** |
| 4 | TD2 删完的表残留 | "删除后逐表 count=0" | `plugin_task` / `plugin_artifact` **各剩 1 行**（删除证明本身） | `plugin_artifact.task_id` 是 **NOT NULL + 外键**（`fk_artifact_task`），产物必须挂在一条真实任务下才能落库。证明是"已删除"这一事实的唯一载体，连它一起删掉客户就再也拿不到证据了。用例对这两张表单独断言"只剩证明" |
| 5 | 删除范围外的表 | "8 类数据" | `document_meta` / `knowledge_base_meta` / `knowledge_base_grant` / `segment_map` **不纳入在线删除**，在响应 `retained_tables` 里显式列出 | 知识与文档正文属大对象，在线清空的风险远高于收益（一次误删就是整库知识资产）。退租需全量清空时由 DBA 执行 `sql/full-purge.sql`。**不静默跳过**：响应与证明都写明 |

## 28. 门禁与用例结果

| 组 | 命令 | 结果 |
| --- | --- | --- |
| 端点基线 | `python deploy/validate_openapi.py --strict` | `core=30 phase2=38 phase3=16 phase4_present=10`，**EXIT 0** |
| 全量 | `OA_BATCH=B21 pytest tests --run-pg -q` | **1993 passed / 0 failed**（511s） |
| 数据权利 | `pytest tests/integration/test_retention_export_delete.py --run-pg -q` | **15 passed** |
| 静态 | `ruff check .` / `ruff format --check .` / `mypy app` | 全绿（144 source files） |
| 告警自证 | `promtool test rules` | **SUCCESS** |
| 部署门禁 | `validate_deploy` / `validate_migrations` / `validate_schema_retention` / `validate_images` / `validate_packaging` / `validate_plugins` / `validate_secrets` | 全部 EXIT 0 |
| 交付 SQL | `sql/retention-audit.sql`（`-v days=36500`）、`sql/tenant-export-manual.sql`、`sql/full-export.sql`（41 表全导出）、`sql/full-purge.sql`（守卫 + `BEGIN/ROLLBACK` 验 TRUNCATE） | 均在测试库实跑通过；`full-purge` 的 TRUNCATE 已回滚、库内 342 个企业行完好 |

15 条用例逐条对应判据：

| 判据 | 用例 |
| --- | --- |
| ① 过期行清零 + 日志含行数 + `audit_log=skipped(no privilege)` | `test_expired_rows_are_purged_and_audit_is_skipped` |
| ② 记忆永久不动 | 同上（`user_memory` 仍 2 行 + `retention_table_skipped(reason=permanent)`） |
| 自续期 + 总行数日志 | `test_cleanup_job_reschedules_and_reports` |
| ③ 5 个文件都在 + manifest 行数/sha256 一致 + 可下载解压 | `test_export_zip_has_five_files_and_matching_manifest` |
| ④ 超限报业务错误、不截断 | `test_export_over_limit_returns_business_error` |
| ⑤ 逐表归零 + `audit_pending_dba>0` + 证明可解析 + 行数一致 | `test_delete_wipes_tables_and_proves_it` |
| ⑤ 二次删除幂等 | `test_delete_is_idempotent_on_second_call` |
| 逐字确认（多空格/少字/全半角） | `test_delete_requires_exact_tenant_name` |
| owner 专用 | `test_delete_requires_owner_role` |
| ⑥ 私有化不可用 | `test_private_mode_rejects_export_and_delete` |
| 守卫方向（成员 / 平台令牌都拒） | `test_export_requires_tenant_admin` / `test_export_rejects_platform_token` |

**日志断言为什么用 LoggerSpy 而不是 `capsys`**：`structlog.PrintLoggerFactory(file=sys.stdout)`
在 `configure()` 时就把当时的 `sys.stdout` 对象绑死了，而 `capsys` 替换的是**之后**的
`sys.stdout`——两者可能不是同一个对象，抓不到就是"假绿"。替换模块级 `logger` 才能确定性
断言"代码确实发出了这句话"，同时顺带断言了事件里的 `deleted` 行数。

## 29. 复现踩到的坑（留给下一个人）

| # | 坑 | 正确做法 |
| --- | --- | --- |
| 1 | `promtool` 在 Git-Bash 下报 `path 'D:/.../etc/prometheus/...' does not exist` | `MSYS_NO_PATHCONV=1`；镜像入口是 `prometheus`，要 `--entrypoint promtool` |
| 2 | `psql` 的 `\copy` **不展开 `:变量`** | 先用 `SELECT set_config('moy.tenant', :'tenant', false)` 把变量搬进会话参数，`\copy` 的查询读 `current_setting(...)` |
| 3 | 服务端 `COPY ... TO '<路径>'` 在 QA 容器里打不开目录 | 用客户端 `\copy`（`\cd :out` 可以正常展开变量） |
| 4 | `all docker exec` 的参数都会被 MSYS 改写（`/tmp` → Windows 路径） | 容器内路径一律 `MSYS_NO_PATHCONV=1`；`docker cp` 的**主机侧**要给 Windows 绝对路径 |
| 5 | 清库时用应用角色删 `audit_log` → `permission denied` | 夹具里的**清理**用超级用户（口令相同、只换用户名），**断言**一律走应用角色 |
| 6 | `plugin_task.status` / `tool_execution.status` / `usage_event.source` / `integration_outbox.kind` 都有 CHECK | 造数据前先查约束：`completed` / `success` / `settled` / `kc_group_upsert` |
| 7 | `message.seq` 是 `GENERATED ALWAYS` 标识列 | 插入时**不要**写它 |
| 8 | `plugin_artifact.task_id` 是 NOT NULL **外键** | 数据权利产物也要建一条宿主 `plugin_task`（`plg_system_data_rights`） |
| 9 | `get_artifact_store()` 是**进程级单例**，首次调用时按当时配置定型 | 用例前后 `reset_artifact_store()`，否则"单独跑绿、全量跑红" |
| 10 | `ruff` 未固定版本（`ruff>=0.5`，本地 0.17.1） | 本批把 `tests/unit/test_metrics_contract.py` 按新版本重排（纯格式）+ 修一处 `RUF012`；**建议把 ruff 钉版本**，否则每次升级都会带出无关重排 |

## 30. 本批发现的遗留问题（不属本批，交 QA / 架构）

| # | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| 1 | `deploy/validate_deps.py` **不通过**：`prometheus-client` 未登记进架构文档 §8.1 清单 | `python deploy/validate_deps.py` → `[FAIL] prometheus-client：未登记进架构文档 §8.1 清单` | 由 T4-05.1 的交付方补文档登记（本批零新增依赖，是**继承**的失败） |
| 2 | `tests/unit/test_metrics_contract.py` 里有一句**假断言** | `assert "/metrics" in MetricsMiddleware.__module__ or True` —— `or True` 让它恒真 | 同用例下一行 `assert "/metrics" in EXCLUDED_PATHS` 才是真判据；建议删掉前半句（本批未改动，避免掩盖问题） |
| 3 | `tenant` 表缺 `deleted_at` | 见 §27 第 3 条 | V1.1 若要"标记删除 + 冷静期"，需一次加列迁移 |
| 4 | `kind=tenant_export` 无处理器 | 见 §25 末段 | 若转异步，同时要新增"查导出进度"端点 → 必须走端点预算审批（第 11 条） |

## 31. 终验前收口（主理人指令，两处，本次提交）

### 31.1 移除恒真假断言（`tests/unit/test_metrics_contract.py`）

原 `assert "/metrics" in MetricsMiddleware.__module__ or True` —— `or True` 让它**恒真**，
是一句"挂上去永远绿"的断言，比没有断言更危险（有人会以为这条已被覆盖）。

- 删掉 `or True` 后**真实判据仍然通过**（11 passed）——说明前面的实现没问题，
  这也正是必须删的理由：它掩盖了"这条到底测没测"；
- 顺手加固一条**反向**断言：`EXCLUDED_PATHS` 里不得出现 `/api/v1` 开头的业务路径。
  防的是另一种自伤——为了让曲线干净，把业务路径也排除掉；
- 因此 `MetricsMiddleware` 变为未使用导入，一并移除（不留"为了用而用"的假引用）。

### 31.2 删除证明不得留下业务数据（**合规问题**）

**问题**：TD2 删完 `plugin_task` / `plugin_artifact` 各留 1 行承载证明
（`plugin_artifact.task_id` 是 NOT NULL 外键）。若这 1 行带任何业务字段，
等于"删了又留了一份数据"，这份"已删除"的结论不成立。

**处理**（`tenant_data_service.py`）：

| # | 改动 | 说明 |
| --- | --- | --- |
| 1 | 宿主任务**显式置空**，不靠列默认值 | `conversation_id=''`、`inputs='{}'`、`checklist='[]'`、`warnings='[]'`、`error_message=NULL`、`retry_of=NULL`。默认值某天被改（比如 `inputs` 加了模板初值），这里就会悄悄带数据，而没人会想到去看宿主任务 |
| 2 | 只留用途标记 | `result_summary` = `proof_of_deletion` / `tenant_data_export`（新增常量 `PURPOSE_*`），同时作为产物 `output_key`。它表达"这条行是什么"，不是"谁的数据" |
| 3 | 产物正文类字段一律空 | `inline_text=''`、`preview_text=''`、`external_url/storage_key=NULL`；只有 `content` 一份字节。**没有第二处副本，也就没有"忘了脱敏"的第二个入口** |
| 4 | 证明 JSON 自证 + 声明内容边界 | 新增 `proof_of_deletion: true` 与 `content_policy`（"只含行数与时间戳，不含任何被删数据的内容或摘要"） |

**顺带修掉一个会让证明失效的问题**：证明原先的 `expires_at = now + 下载令牌 TTL`（**15 分钟**），
也就是"客户 15 分钟后再也拿不到自己的删除证明"，而且下一次 `retention_cleanup`
会把这一行当作过期产物删掉。现在**两种产物分两种期限**：

| 产物 | 下载链接 | 产物留存 | 理由 |
| --- | --- | --- | --- |
| 导出包 | 15 分钟短期令牌 | `now + TTL`（15 分钟） | 它是**凭证**：客户要的是"现在取走"，过期即失效最安全 |
| 删除证明 | 15 分钟短期令牌 | `now + plugin_artifact_retention_days`（180 天） | 它是**证据**：客户可能几个月后才来核查"你到底删没删"。链接短、证据长，这两件事本来就不该共用一个期限 |

**用例新增断言**（挑 3 个不同来源的原值：消息正文 / 记忆正文 / 产物文件名）：

- 删除后宿主任务 `inputs` 为空对象、`checklist`/`warnings` 为空数组、`conversation_id=''`、
  `error_message`/`retry_of` 为 NULL、`result_summary == 'proof_of_deletion'`；
- 产物行整行（含 `content` 的 JSON）与**下载到的证明字节**里，3 个原值任意一个都 `not in`；
- 证明留存期 > 1 天（防退化成 15 分钟）；导出侧对称断言 `output_key=tenant_data_export`
  且留存期 < 1 天（防导出包被误设成长期留存）。

结果：`test_retention_export_delete.py` **15 passed**；全量 `pytest tests --run-pg`
**1993 passed / 0 failed**（519s）；`ruff check` / `format --check` / `mypy app` 全绿。

---

## 32. T4-05 第三批 3a：轻量版九容器编排与起栈校验（2026-10-24）

> 本批只做**编排 + 起栈校验**。离线包与 `install.sh` / `upgrade.sh` / `rollback.sh` 不属本批（3b 及后续）。

### 32.1 交付物（5 个文件，全部新增）

| 文件 | 作用 | 关键点 |
| --- | --- | --- |
| `deploy/docker-compose.lite.yml` | 轻量版编排，**常驻 9 个**容器 | `dify-web` 在 `profiles:[admin]`、`migrate` 在 `profiles:[migrate]`；只有 `proxy` 映射宿主端口 |
| `deploy/nginx/nginx.conf` | 主配置（日志格式 / 压缩 / 上传上限 / 长连接超时） | `client_max_body_size 50m` 与 `OA_MAX_UPLOAD_BYTES=52428800` **必须一致** |
| `deploy/nginx/lite.conf` | 站点配置（唯一入口的四条路由） | SSE 三件套（`proxy_buffering off` + `X-Accel-Buffering no` + HTTP/1.1）、`resolver 127.0.0.11` 跟随容器重建、`/readyz` 与 `/metrics` 限内网 |
| `deploy/.env.lite.example` | 轻量版环境样例（**无 Milvus / 对象存储 / sandbox 键**） | 口令一律 `openssl rand -hex 24`（URL 安全：口令要嵌进 DSN） |
| `deploy/validate_lite_up.sh` | 静态断言 + 真起栈校验 | 36 条 compose 断言 + 10 条 nginx 断言；起栈后断言 9 容器 / 全 healthy / < 6 GiB |
| `deploy/images.lock` | 补登 `nginx:1.29-alpine`（BSD-2） | 门禁要求「compose 引用 ⊆ lock 登记」；不登 CI 必红 |

### 32.2 实测结果（**口径必须一起引用**）

| 项 | 值 | 口径 |
| --- | --- | --- |
| 起栈命令 | `bash deploy/validate_lite_up.sh` | 退出码 **0** |
| 运行容器数 | **9** | `docker compose ps --status running` 计数 |
| 健康状态 | **9/9 healthy** | 各服务 healthcheck（PG 断言 `schema_migration`；Keycloak 探 9000 `/health/ready`） |
| 总内存 | **1875.5 MiB (1.83 GiB)** | `docker stats --no-stream`，9 个 `oal-*` 容器工作集合计，起栈后 ~30 s 采样一次 |
| 就绪耗时 | **ready in 59s**（判据 ≤180 s） | `up -d` → `GET http://127.0.0.1:80/readyz` 首次 200 的墙钟差 |
| `compose config -q` | **EXIT 0** | 用 `.env.lite.example` 的占位值（QA 静态验收口径） |

**ready in 59s 的口径警告（不得简写成"轻量版 59 秒起栈"）**：

1. 该数字**不含** postgres initdb——脚本在迁移前就先把 postgres 拉起（并断言 `healthy`），
   计时从 `up -d` 开始，此时 initdb 已完成；
2. 镜像**全在本地**（无拉取耗时）；
3. 宿主上同时跑着标准栈 17 个容器（Docker Desktop / WSL2），比 baseline 的独占环境更苛刻；
4. baseline §3.1 的 **95.7 s** 是"空卷首次启动 + 含 initdb"的完整口径，两者不可互换。

逐容器内存（同一次采样，MiB）：

| keycloak | dify-api | dify-worker | postgres | adapter | web | proxy | plugin_daemon | valkey | 合计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 667.3 | 465.9 | 455.2 | 105.9 | 85.5 | 35.6 | 29.3 | 24.6 | 6.2 | **1875.5** |

与 baseline §2.3（1927.2 MiB）相差 −51.7 MiB（−2.7%），差异来自 keycloak 尚未被访问 + 采样时点不同。

### 32.3 端到端证据（不是只看容器"跑起来了"）

| 证据 | 命令 | 结果 |
| --- | --- | --- |
| 经 proxy 的适配层就绪 | `curl http://127.0.0.1:80/readyz` | `{"code":0,...,"status":"ready"}`，5 项检查全 `ready:true`（含 `db_privilege`） |
| nginx 自身 | `curl http://127.0.0.1:80/healthz` | `ok` |
| 前端同源链路 | `curl -L http://127.0.0.1:80/` | 307 → `/chat` → **200** |
| Dify 自身 | `docker exec oal-dify-api curl -s localhost:5001/health` | `{"status":"ok","version":"1.17.1"}` |
| Dify 库建表 | `psql -d dify -tAc "select count(*) from information_schema.tables where table_schema='public'"` | **144**（`MIGRATION_ENABLED=true` 生效） |
| Keycloak 管理端口 | `docker exec oal-keycloak bash -c '.../dev/tcp/127.0.0.1/9000...'` | `"status": "UP"` |
| pgvector 可装 | `select name,default_version from pg_available_extensions where name='vector'` | `vector\|0.8.7`（**文件在镜像里**） |
| 迁移 | `--profile migrate run --rm migrate` | `applied_now=9 / already_applied=2`，退出 0 |

### 32.4 从镜像里核过的事实（不是文档推测）

本批所有"为什么这么写"都在容器内验过一遍，避免抄错键名：

| 结论 | 证据（镜像内路径） |
| --- | --- |
| `STORAGE_TYPE=local` 可用且等价于 OpenDAL fs | `api/extensions/ext_storage.py:32-35` `case StorageType.LOCAL → OpenDALStorage(scheme="fs", root=STORAGE_LOCAL_PATH)`；`configs/middleware/__init__.py:80` 声明 `STORAGE_LOCAL_PATH`（标记 deprecated） |
| `CODE_EXECUTION_ENDPOINT` 空串会让进程退出 | `configs/feature/__init__.py:110` `CODE_EXECUTION_ENDPOINT: HttpUrl`（空串 → `ValidationError`） |
| `SSRF_PROXY_HTTP_URL` 是 `str \| None`，空串不报校验但会让 httpx 拿不到代理 | `configs/feature/__init__.py:691` |
| 7 个 `PGVECTOR_*` 是硬要求且 MIN < MAX | `dify_vdb_pgvector/pgvector.py:39-53`（逐条 `ValueError: config PGVECTOR_XXX is required`） |
| Dify 会自己 `CREATE EXTENSION vector` | `dify_vdb_pgvector/pgvector.py:269`（本栈 Dify 用超级用户 `officeai`，能建） |
| dify-api 有 `/health` 且镜像内有 curl | `api/extensions/ext_app_metrics.py:20`；`command -v curl` → `/usr/bin/curl` |
| plugin_daemon 有 `/health/check` 且有 curl | 二进制内含 `internal/server/controllers/health_check.go` 与字符串 `/health/check` |
| dify-worker **没有 ps**，PID 1 就是 celery | entrypoint.sh: `exec celery -A celery_entrypoint.celery worker ...`；`command -v ps` 为空 → 探针改读 `/proc/1/cmdline` |
| Keycloak 镜像有 bash/grep、**没有 curl** | `command -v bash /usr/bin/bash`、`command -v curl` 为空 → 探针用 bash `/dev/tcp` 手写 HTTP |
| web 镜像（alpine）有 wget、没有 curl | `command -v wget /usr/bin/wget` |
| adapter 镜像内**不含** `deploy/` | `ls /app` → 只有 `app / pyproject.toml / README.md` → migrate Job 必须挂 `./:/app/deploy:ro` 并设 `OA_DB_SQL_DIR` |

### 32.5 断言不是恒真的（负样本自证）

| 负样本 | 改动 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 删掉 `JAVA_OPTS_APPEND` 一行（**注释里仍留着这串字**） | 静态断言红 | `[FAIL] Keycloak 带 JVM 限堆…实际 None`，EXIT 1 |
| 2 | `CODE_EXECUTION_ENDPOINT: ""` | 静态断言红 | `[FAIL] 未设置 CODE_EXECUTION_ENDPOINT / SSRF_PROXY_*`，EXIT 1 |
| 复位 | 还原编排 | 全绿 | 36 条 compose + 10 条 nginx 全 `OK`，EXIT 0 |

负样本 1 是关键：证明断言走的是 `compose config` 的**解析结果**而不是 grep 全文——
注释里也写着 `-Xms64m -Xmx384m`，grep 会假通过。

### 32.6 三处与架构文档不一致（需要下游知道）

| # | 架构 §9.1 写法 | 本批实现 | 依据 |
| --- | --- | --- | --- |
| 1 | 资源限制只给 4 个关键容器（postgres 1G / keycloak 768M / dify-api 1G / adapter 768M），"其余不设（留弹性）" | **9 个全设**：postgres 512M / valkey 384M / keycloak **1024M** / dify-api 1024M / dify-worker 768M / plugin_daemon 512M / adapter 512M / web 384M / proxy 128M（合计 5248M） | ① `keycloak 768M` 与 baseline §2.3 实测 **cgroup 909.1 MiB** 冲突，按 768M 会被 OOM killer 杀掉（表现为"无日志反复重启"）——数字以实测为准；② "其余不设"在 8G 单机上等于没有兜底，一个失控容器就能压死整机 |
| 2 | 启动顺序写 `dify-api(healthcheck) → … → adapter(healthy /readyz)` | `adapter` 对 `dify-api` 用 `condition: service_started`（postgres/valkey/keycloak 仍用 `service_healthy`） | dify-api 首启固有 78-85 s（baseline §3.2 证明与迁移无关），等它 healthy 只会把链路串长；适配层启动期**不依赖**底座在线（`/readyz` 的 5 项检查里不含下游）。**改这条前先看 baseline §3.2**：那不是可以优化掉的耗时 |
| 3 | 对象存储 `STORAGE_TYPE: local` + `STORAGE_LOCAL_PATH: /app/api/storage` | 同架构，另加：`dify-api` 与 `dify-worker` **共用同一个卷** | worker 写入的分段文件 api 侧要能读到；分卷会让知识库"索引完成但预览 404" |

另有一处**新增**（架构未写）：`validate_lite_up.sh` 在迁移前会幂等地把库里 `officeai_app` 的口令
对齐到 `OA_DATABASE_URL`。原因：`init-app-role.sql` 用占位口令建角色（SQL 无变量），
全新环境不先对齐，第一次 `migrate --apply` 必然 `28P01`，而报错只出现在 migrate 日志里，
现场极易误判成"编排写错了"。**install.sh（3b）必须保留这一步**。

### 32.7 遗留（不属本批，交 3b / QA / 架构）

| # | 事项 | 影响 | 建议 |
| --- | --- | --- | --- |
| 1 | `langgenius/dify-web:1.17.1` 的 **digest 未核验**（本轮未拉取该镜像） | `--profile admin` 首次用时会现拉 | 3b 做离线包时 `docker manifest inspect` 补 digest 进 `images.lock` 与 `OFFLINE_IMAGES` |
| 2 | `vector` 扩展在 **dify 库**是**惰性创建**（首次建知识库时由 Dify 自己执行） | 起栈校验看不到它，属正常 | 存量升级仍需 DBA 手工 `CREATE EXTENSION`（架构 §9.3.1），两件事别混 |
| 3 | 架构 §9.1 提到的 `--profile with-ssrf` **未实现** | 需要抓 URL 的知识库同步不可用 | 3b 或 V1.1 补；本批在 compose 末尾「已知限制」里如实写明 |
| 4 | `dify-web` 只发布到 `127.0.0.1` | 现场要 Remote 进机器才能配 provider | 运维手册（T4-05.6）写清 `ssh -L 3001:127.0.0.1:3001` |
| 5 | 本批**未**覆盖"知识库检索 + 记忆向量"的真实链路 | 向量通路只到"扩展可装" | 属交付流程（装 deepseek 插件 + 填 Key），baseline §2.4 已用标准栈同镜像测过增量（+10 MiB） |

---

## 33. 暂停点（2026-10-23 11:57，按用户要求暂停开发）

### 33.1 当前状态

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `main` / `5d1f29b`（本文件提交后为新的 docs 提交） |
| 端点基线 | `phase4_present=10`，`validate_openapi.py --strict` **EXIT 0**（G1-G6 全过） |
| 镜像门禁 | `images.lock` 17 引用 / 17 登记，digest 全量登记，**EXIT 0** |
| 最后一次全量 | **1993 passed / 0 failed**（`--run-pg`，511s；此后仅改 deploy 脚本与文档，未动应用代码） |
| 工作区 | 仅 6 个 CRLF 假 M（`git diff HEAD --numstat` 为空）+ `scripts/bench/` 未跟踪 |

### 33.2 已交付（阶段四范围内）

- T4-01 ~ T4-04 全批（基座整改、会话落库、记忆、任务引擎加固、成本护栏、安全收口）。
- T4-05.1/.2 可观测：15+2 指标、两个 exporter、5 条告警；**真栈 4/4 target UP**，`promtool test rules` **SUCCESS**（5 条规则成对断言）。
- T4-05.3 数据权利：保留清理（审计 skipped 为预期）、TD1 同步导出（20 万行上限、复用产物令牌）、TD2 删除（`pending DBA` 证明、业务字段清空、证明留存 180 天）。
- 3a 轻量版九容器：**9/9 healthy / 1875.5 MiB / ready in 59s**（口径：不含 initdb、镜像已在本地）。
- 3b-1 `install.sh`（1004 行）：实跑安装 + 幂等 + 三组负样本（端口占用/checksums/容器名冲突）。
- 3b-2 前半 `backup.sh` / `restore.sh`：静态校验与参数用例通过；**restore 未实跑**。
  - 修掉一个 source 变量名冲突：`backup/restore/install` 共用 `_INSTALL_SAVED_ARGS` 会被后者覆盖为空，表现为 `--help`/`--out-dir` 静默失效、仍按默认路径备份；已改为三者互不相同的变量并验证。

### 33.3 未完成（恢复开发按此顺序）

| # | 事项 | 关键口径 |
| --- | --- | --- |
| 1 | `upgrade.sh` | 顺序不可颠倒：vector 预检 **exit 4** → init 指纹 **exit 6** → 备份 → migrate（DBA **exit 5**）→ 冒烟 → 失败自动回滚；目标版本 <0006 **exit 7** |
| 2 | `rollback.sh` | 薄封装 `migrate.py --rollback`（已内置回滚前强制备份）；`OA_MAINTENANCE=1` + 停 proxy/web |
| 3 | 离线包 `deploy/offline/` | 17 镜像 digest 已登记；**9 个 tar 连续 load 未实测**（只验了 2 个） |
| 4 | T4-05.6 | SBOM 四层 + `.difypkg` 扫描 + 合规三件套 + About 页 |
| 5 | T4-05.7 | 性能基线（首屏/首字/并发）+ 金标任务集 20 条 + 部署/使用手册 |
| 6 | QA 终验 | 全量回归 + 五类攻击 + 弱网/断网部署 + 阶段四验收报告 |

### 33.4 恢复开发的第一步（可直接执行）

1. `bash deploy/validate_lite_up.sh` 起轻量栈（约 60s；**单机只能跑一套**）。
2. `bash deploy/backup.sh` → 手工删 20 条 `message` + 5 条 `user_memory` → `OA_MAINTENANCE=1 bash deploy/restore.sh` → 逐表行数与抽查一致。
3. 再实现 `upgrade.sh` / `rollback.sh`，先跑两个负样本（`DROP EXTENSION vector` → exit 4 且迁移 0 行；篡改安装台账 init sha256 → exit 6）。

### 33.5 环境事实（接续者必读）

- 轻量栈已 `down -v`；`oa-*` 标准栈未动；测试库 `moy-qa-pg-vec`（55434）。
- **Git Bash 的 `mktemp` 返回 `C:\...`，`rm` 会被安全删除钩子拒绝** → 脚本不要落临时文件，输出收进变量。
- **单机只能跑一套轻量版**：`container_name: oal-*` 写死，换项目名仍撞名。
- `OA_MAINTENANCE` 全仓无消费点，只是脚本侧意图；真拦流量靠 `stop proxy web`。
- 回滚 down SQL 只有 `0006–0011`；`0002–0005` 没有，且不补（补写等于给客户库准备删除脚本）。

---

## 34. Batch 2：契约防线与边界收口（W-01 / W-02 / W-03 / W-04 / W-05 / W-06 / W-07 / W-08）

> 分支 `feat/batch2-contract-and-boundaries`。八项各一笔提交（T01–T08），
> 另加一笔 QA 回归修复（§34.6 的 A-7 请求侧），共 9 笔；每笔可独立 revert。
> 方案与任务分解见 `docs/phase4-batch2-design-2026-10-25.md`。

### 34.1 W-01 插件域契约改由 `app.openapi()` 派生

| 项 | 结果 |
| --- | --- |
| 真相源 | `create_app().openapi()`（生成器 `deploy/export_plugin_contract.py` 用 `importlib` 加载 `deploy/validate_openapi.py` 的 `build_schema()`，**不复制**它的 `sys.path` 处理） |
| 生成物 | `apps/web/contracts/plugin-domain.gen.json`：23 个 TS 接口 / 133 键 / 3 处属性级枚举（入库） |
| 手写快照 | `contracts/phase3.pl.json` **降为比对对象**（不再是真相源），仍负责「可选性 + 枚举」——后端插件域模型全部字段带默认值，Pydantic v2 不输出 `required`，可选性无法由生成契约承担（口径 A） |
| 门禁 | `scan:contract:gen`（gen ↔ `types.ts` ↔ 手写快照，**只比键集合**，双向严格）+ `--generated --self-test`；gen 缺失退 2、不静默回退 |
| CI 落点 | `.github/workflows/ci-adapter.yml` 的 openapi-export job（`export… --check` + `scan:contract:gen` + 自检）；**`ci-web.yml` 不加** |
| 实测 | 换源首跑 18 处差异（附录 A 的 8 类漂移 × 双侧），收敛后 0；负样本：`plugin.py` 的 `MarketData.total → total_count` → 重生成 → `scan:contract:gen` **EXIT 1**，改回 0 |

**已知代价（接受）**：后端把某字段从「有默认值」改成「必填」，本批抓不到——那需要 `model_fields` 派生 + 手写例外表，登记 V1.1。

### 34.2 W-03 演示支付口径与回调 fail-open 收口

- 本版为**演示支付**，未接真实渠道；对外材料不得写「支持在线支付」。
- 回调 fail-open **共 4 处**（此前记录只有 3 处），第 4 处且最先命中的在 schema 层：
  `app/schemas/billing.py` 的 `PaymentCallbackRequest.trade_state` 默认 `"SUCCESS"`。
  路由 `api/v1/billing.py` 先 `model_dump()`，于是「渠道没传状态」被补成成功。
  4 处一起改后：缺字段 / `NOTPAY` / `CLOSED` / 空串 → 订单仍 `pending_payment`，
  回调流水留 `reason=trade_not_success` 取证行；`is_success` 改 `PAID_TRADE_STATES`
  白名单 + `.strip().upper()`。
- **未进白名单的取值**：微信 v2 用词 `TRADE_SUCCESS` 不认（真接 v3 时再补词表）；
  本批接受，记录于此。

### 34.3 W-04 `plg_hr_admin` 能力声明收紧（4 → 3 项）

- 删除 `org.directory.read`：6 个步骤 / 7 个输入均不消费它，属「多要权限」。
- `ci-web.yml` 的 G-30 由 `scan:capability-warn`（`--warn-only`）改**严格** `scan:capability`
  —— 只删声明不改 CI 等于没修（历史上 CI 一直是绿的）。
- `docs/plugin-dev-guide.md` 订正两处口径：④ 行「什么场景该申请」、§3.2「所属部门来自通讯录」。
- 结果：`多余能力 0 处`、`越权 0 处`；6 份声明一次跑通，无第二个「多要权限」插件。
- `scan:capability-warn` **保留**为本地排查命令（CI 不再引用，不构成绕过）。

### 34.4 W-07 网关 413 / 429 复用已注册错误码（含文案偏差）

- `api-client` 在 401 判定之后、`unwrap()` 之前新增 `throwIfGatewayStatusRejected()`：
  413 → `20008`（`PAYLOAD_TOO_LARGE`）、429 → `20006`（`RATE_LIMITED`），
  `BusinessError(code, '')` 让 UI 按 code 取既有文案，**不新造码、不复制字符串**。
- **`request()` 与 `upload()` 都接**：大文件上传正是 413 的主战场，只改前者等于漏掉真实场景。
- **已知偏差**：`20008` 的注册文案写死「上限 50MB」，而网关 `client_max_body_size`
  可能更小（轻量版 50m / 其他环境 1–10MB），413 时这句可能不准。本批**不改注册文案**
  （错误码文案是登记制），登记为观察项。

### 34.5 W-08 空候选输入的 `format` 约束（四处联动）

`InputSpec.format`（manifest 加载期校验）→ `InputBriefItem.format`（下发）→
`plugin_service.detail()` 显式复制 → 前端 `PluginInputSpec.format?` + `validateField`
形状校验。`repo` = 四类地址（`https?://` / `ssh://` / `git@host:path` / `owner/repo`，
容忍 `#branch`、`?ref=`）；`department` = 1–32 字且不含换行；**未知 format 直接跳过**
（后端将来新增 format 不会让旧前端把所有输入判成不合法）。
负样本：manifest 写 `"format": "bogus"` → `validate_plugins.py` **EXIT 1**。

### 34.6 A-7 `CapabilityPolicyItem` 双向映射（QA 回归修复）

- 背景：后端 `CapabilityPolicyItem` / 请求项都叫 `enabled`，前端业务名是
  `org_switch_enabled`。裁决「不改后端字段名」，映射放前端一处入口。
- **缺陷**：第一版只做了**响应**映射，请求仍发 `org_switch_enabled`；后端请求模型
  `extra="forbid"` → `PUT /plugins/capability-policy` 恒 400/20001（QA 实测：同 body
  换成 `enabled` 即 200，说明后端一直是对的）。属预存在缺陷，本批 A-7 首版只修了一半。
- 修复：`setCapabilityPolicy` 发请求前把 `policies` 映射为
  `{ capability_key, enabled }`，响应仍映射回 `org_switch_enabled`；补 1 条单测断言
  **请求体 JSON 逐字**为 `{"policies":[{"capability_key":"org.repo.write","enabled":true}]}`
  且不含 `org_switch_enabled`。

### 34.7 本批实测（口径：分支 HEAD，未 `--run-pg`、未跑 `build:web`）

| 项 | 值 |
| --- | --- |
| 适配层全量 | `pytest tests -q`（`OA_BATCH=B21`）**2055 passed / 0 failed**，203 skipped 全部为 `--run-pg` 门控 |
| 静态 | `ruff check .` / `ruff format --check .` / `mypy app` 均 0；`validate_openapi.py --strict` 0；`validate_plugins.py` 0 |
| 前端单测 | **53 passed**（18 → 53，含 W-02 行为网 12 条、分页 8 条、表单 14 条、能力开关映射 1 条） |
| 前端静态 | `typecheck:web` / `lint:web` 0；8 道扫描（banned/pages/workbench/placeholders/contract/contract:gen/capability/oidc）全 0；7 道自检全 1 |
| e2e | `playwright test` **3 passed**（新增 `/chat → /login?next=%2Fchat`） |

### 34.8 遗留（不属本批）

| # | 事项 | 处置 |
| --- | --- | --- |
| 1 | `scripts/gen_delivery_manifest.py --check` 红：清单里 `deploy/.env.lite.example`、`deploy/docker-compose.lite.yml` 尺寸失真 | **预存在**（清单最后刷新 `5a72604`，两文件其后被 `f33b22b` / `ed199f4` 改过），非本批引入；Batch 3 的 docs 批次统一重跑生成器 |
| 2 | `20008` 文案写死 50MB | 见 §34.4，观察项 |
| 3 | 微信 v2 `TRADE_SUCCESS` 未进白名单 | 见 §34.2，真接 v3 时再补 |

