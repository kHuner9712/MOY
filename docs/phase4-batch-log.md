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
| 结果 | **1679 passed / 0 failed / 0 skipped** |
| 耗时 | **651.91s（10m51s）** |
| 含 PG | **是**（`moy-qa-pg`，`--run-pg`） |
| `OA_BATCH` | **B21** |
| 解释器 | `apps/adapter/.venv/Scripts/python.exe`（3.13.14） |

### 三档覆盖率（coverage.py 7.x，`--cov-branch`）

> 首轮实测合并 **90.36%**（低于门槛 0.42pp），按主理人指示**先补测再判**（见 §2.1），
> 下表是补测后的**最终值**。

| 维度 | 本轮实测 | 门槛 | 判定 | 口径 |
| --- | --- | --- | --- | --- |
| 合并 | **90.85%** | ≥90.78%（`max(90, 阶段三实测 90.78%)`） | **通过（+0.07pp）** | `totals.percent_covered`＝(13232 行 + 2433 分支)/(14248 语句 + 2994 分支) |
| `business+db` | **92.09%** | ≥90.26% | 通过（+1.83pp） | `app/business/*` + `app/db/*` 共 45 文件：行覆盖 |
| 分支 | **86.01%** | ≥80.89% | 通过（+5.12pp） | 2433 / 2994 个分支 |

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
| `apps/web/src/lib/business-errors.ts` | **待改**：`15006` 文案仍是旧版（「…常规模式，升级套餐可恢复完整能力」），且缺 402 分支。属 T4-02.6 前端范围 |
| 合并覆盖率 | 已补测达标（90.85% / 92.09% / 86.01%），见 §2.1 |
