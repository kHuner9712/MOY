# 阶段二门禁证据（Gate Evidence）

> **用途**：阶段验收报告的**唯一证据来源**。本文件只记录**实跑事实**，判断由主理人做出。
>
> | 项 | 值 |
> | --- | --- |
> | 生成时间（UTC） | 2026-10-21T22:22:10Z |
> | 执行 HEAD | `1db2ed9` feat(web): B13 套餐账单页与运营后台页 |
> | 代码冻结点（阶段二收尾） | `f168e85`（此后仅有 B13/B14 前端提交与文档提交） |
> | 执行人 | software-engineer（只读执行，未 `git add` / `git commit`） |
> | 边界 | 未运行 `pytest`（engineer-4 正在改席位校验）；仅跑只读门禁脚本、`psql`/`asyncpg` 查询、`git show` |
>
> 章节：A 门禁实跑 · B 镜像与依赖一致性 · C 文档与实现一致性抽查 · D 阶段一遗留项复核 · 阻断项

---

## A. 门禁实跑（原始输出 + 退出码 + 与基线差异）

### A-1 `deploy/validate_deps.py`

```bash
cd C:/Users/15864/Desktop/麦子
apps/adapter/.venv/Scripts/python.exe deploy/validate_deps.py
```

| 项 | 值 |
| --- | --- |
| 退出码 | **0** |
| 关键输出 | `[INFO] 运行时依赖 15 条` → `\n[OK] 依赖门禁通过` |
| 校验内容（脚本自述） | ① `pyproject.toml` 与架构 §8.1 清单一致（包名+版本约束双校验）② 最低安全版本（starlette ≥1.3.1）③ 无上游修复项加固约束静态检查 |
| 与基线差异 | 阶段一验收报告「五、门禁独立复跑记录」**未单列该门禁**（架构 §8.1.1 记载其为 Sprint 0 BLK-04 实跑项）；本次为阶段二首次纳入验收表 |
| 结论 | ✅ 通过 |

**事实观察**：脚本输出里出现**字面量 `\n`**（`\n[OK] 依赖门禁通过`），即 `print` 的转义未被解析，属输出格式瑕疵，不影响退出码。

### A-2 `deploy/validate_images.py`

```bash
apps/adapter/.venv/Scripts/python.exe deploy/validate_images.py
```

| 项 | 值 |
| --- | --- |
| 退出码 | **0** |
| 关键输出 | `[INFO] compose 引用 14 个镜像，images.lock 登记 14 个` → `[OK] 镜像清单校验通过` |
| 与基线差异 | 阶段一记录 `13 引用 = 13 登记` → 当前 **14 = 14**（+1，`prom/prometheus` 由「lock 有、compose 未引用」转为实际引用） |
| 结论 | ✅ 通过 |

### A-3 `deploy/validate_openapi.py`（**严格模式，不带 `--allow-missing-phase2`**）

```bash
apps/adapter/.venv/Scripts/python.exe deploy/validate_openapi.py
```

| 项 | 值 |
| --- | --- |
| 退出码 | **0** |
| 关键输出 | `[INFO] 端点统计: core=30 phase2=38/38 （未实现 0）` → `[OK] G1-G5 全部通过` |
| 与基线差异 | 阶段二过程中记录：B03 时 `0/38` → B04 `4/38` → B05 `8/38` → B06 `9/38` → 当前 **38/38**（B07-B10 与前端类型层落地后补齐）；阶段一为 30 端点基线（无 phase2 概念） |
| 结论 | ✅ 通过（G1 核心 30 端点精确集合相等；G2 新域端点全部已登记；G3 计数 38；G4 加性 diff 通过；G5 泄漏扫描 0 命中） |

### A-4 `scripts/check_error_catalog.py --strict`（G-13）

```bash
cd apps/adapter && ./.venv/Scripts/python.exe scripts/check_error_catalog.py --strict
```

| 项 | 值 |
| --- | --- |
| 退出码 | **0** |
| 关键输出 | `[INFO] 架构 §7.1 = 14 码；errors.py = 52 码；business-errors.ts = 52 码；放行域 = 无（严格）`<br>`[OK] 架构 §7.1 → errors.py: 14/14 已落地`<br>`[OK] errors.py → business-errors.ts: 52 个码全部覆盖`<br>`[OK] ErrorCode → ERROR_REGISTRY: 52 个码全部注册`<br>`[OK] 错误码三处一致门禁通过（G-13）` |
| 与基线差异 | 阶段一无此门禁（G-13 为阶段二新增）；脚本默认值已由 `b08,b09` 收紧为 **`none`（严格）**，即 14005-14008（B08）与 15001-15009（B09）均已转正、不再按域放行 |
| 结论 | ✅ 通过 |

### A 小节汇总结论

| 门禁 | 命令 | 退出码 | 结论 |
| --- | --- | --- | --- |
| 依赖清单 | `deploy/validate_deps.py` | 0 | ✅ |
| 镜像清单 | `deploy/validate_images.py` | 0 | ✅ |
| 端点基线（严格） | `deploy/validate_openapi.py` | 0 | ✅（core=30 / phase2=38/38） |
| 错误码三处一致 | `scripts/check_error_catalog.py --strict` | 0 | ✅（14/14、52/52、52/52） |

---

## B. 镜像与依赖一致性

### B-1 `images.lock` ↔ 编排文件逐条比对

比对方法：解析 `deploy/images.lock` 的 `name:tag`，与 `deploy/docker-compose.yml` + `deploy/docker-compose.saas.yml` 中所有 `image:` 引用做**精确字符串比对**（CRLF 已归一，`${OA_IMAGE_TAG:-1.0.0}` 模板单独归类）。

- `images.lock` 登记：**14 条**
- compose 去重后引用：**14 条**

| image:tag | 出现于 | 比对结果 |
| --- | --- | --- |
| `langgenius/dify-api:1.17.1` | private + saas | 精确一致 |
| `langgenius/dify-web:1.17.1` | private + saas | 精确一致 |
| `langgenius/dify-sandbox:0.2.15` | private + saas | 精确一致 |
| `langgenius/dify-plugin-daemon:0.6.10-local` | private + saas | 精确一致 |
| `quay.io/keycloak/keycloak:26.8.1` | private + saas | 精确一致 |
| `postgres:18.6-alpine` | private + saas | 精确一致 |
| `valkey/valkey:9.2-alpine` | private + saas | 精确一致 |
| `milvusdb/milvus:v3.0.3` | private + saas | 精确一致 |
| `quay.io/coreos/etcd:v3.7.2` | private + saas | 精确一致 |
| `chrislusf/seaweedfs:4.48` | private + saas | 精确一致 |
| `ubuntu/squid:6.13-25.04_beta` | private + saas | 精确一致 |
| `prom/prometheus:v3.15.0` | 仅 private | 精确一致 |
| `officeai/adapter:${OA_IMAGE_TAG:-1.0.0}` | private + saas | 变量模板 → lock 精确值 `officeai/adapter:1.0.0`，一致 |
| `officeai/web:${OA_IMAGE_TAG:-1.0.0}` | private + saas | 变量模板 → lock 精确值 `officeai/web:1.0.0`，一致 |

**差异表结论**：新增 0 / 缺失 0 / tag 不一致 0；`lock 有、compose 无`（排除变量模板同名）**0 条**。

**与阶段一基线的版本变更**（`c5bfd7d` 阶段一收尾 → 当前 HEAD，来源 `git show c5bfd7d:deploy/images.lock` 对比）：

| 组件 | 阶段一 | 当前 |
| --- | --- | --- |
| Dify（api/web） | `1.0.0` | `1.17.1` |
| dify-sandbox | `0.2.10` | `0.2.15` |
| dify-plugin-daemon | `0.0.3-local` | `0.6.10-local` |
| PostgreSQL | `16-alpine` | `18.6-alpine` |
| Valkey | `7.2-alpine` | `9.2-alpine` |
| Milvus | `v2.4.6` | `v3.0.3` |
| etcd | `v3.5.16` | `v3.7.2` |
| Keycloak | `25.0.6` | `26.8.1` |
| SeaweedFS | `3.80` | `4.48` |
| squid | `5.2-22.04_beta` | `6.13-25.04_beta` |
| Prometheus | `v2.54.0` | `v3.15.0` |

（条目数阶段一 14 → 当前 14，但 12 个 tag 全部上移，属 `c9ce65e feat(deploy)!: 全组件升级至最新稳定版` 的既定动作；**含破坏性变更标记 `!`**。）

### B-2 浮动标签扫描

| 检查项 | 结果 |
| --- | --- |
| `:latest` 标签 | **0 条** |
| 无 tag（隐式 latest） | **0 条** |
| `images.lock` 与 compose 两端一致 | 两端均无浮动标签 |

**事实观察**：`ubuntu/squid:6.13-25.04_beta` 的 tag 带 `_beta` 后缀（精确版本号，非浮动），与阶段一 `5.2-22.04_beta` 属同一命名策略。

### B-3 运行时依赖 15 条与协议白名单

`apps/adapter/pyproject.toml` 运行时依赖 **15 条**，与架构 §8.1 清单逐条对应（`validate_deps.py` 已机械校验通过）：

| # | 包 | 架构 §8.1 协议 | 白名单（MIT/Apache-2.0/BSD） |
| --- | --- | --- | --- |
| 1 | `fastapi>=0.156,<0.157` | MIT | ✅ |
| 2 | `starlette>=1.3.1,<2.0` | BSD-3-Clause | ✅ |
| 3 | `uvicorn[standard]>=0.30` | BSD-3-Clause | ✅ |
| 4 | `pydantic>=2.7,<3.0` | MIT | ✅ |
| 5 | `pydantic-settings>=2.3,<3.0` | MIT | ✅ |
| 6 | `httpx>=0.27,<0.29` | BSD-3-Clause | ✅ |
| 7 | `redis>=5.0` | MIT | ✅ |
| 8 | `sqlalchemy[asyncio]>=2.0,<3.0` | MIT | ✅ |
| 9 | `asyncpg>=0.29` | Apache-2.0 | ✅ |
| 10 | `python-jose[cryptography]>=3.3` | MIT | ✅ |
| 11 | `tenacity>=8.2` | Apache-2.0 | ✅ |
| 12 | `structlog>=24.1` | MIT + Apache-2.0 双许可 | ✅ |
| 13 | `slowapi>=0.1.9` | MIT | ✅ |
| 14 | `python-multipart>=0.0.9` | Apache-2.0 | ✅ |
| 15 | `pyyaml>=6.0` | MIT | ✅ |

无 GPL / AGPL / SSPL；架构 §8.1 结语为「全量 MIT/Apache-2.0/BSD-3-Clause，满足总方案 5.3 白名单」。

### B-4 镜像协议列（`images.lock` 自带的 `license` 字段）

| 镜像 | lock 协议字段 | 与架构 §11 名单的关系 |
| --- | --- | --- |
| dify-api / web / sandbox / plugin-daemon | Apache-2.0（修改版：多租户与商标限制） | 🟢 白名单（附加限制已由 ADR-0004 三条规避约束处置） |
| keycloak / milvus / etcd / seaweedfs / prometheus | Apache-2.0 | 🟢 白名单 |
| postgres | PostgreSQL-License | 🟢 白名单（§11 明列） |
| valkey | BSD-3-Clause | 🟢 白名单 |
| **squid** | **GPL-2.0** | 🔴 §11 黑名单列名 GPL-2.0；该条目**阶段一即已登记并通过验收**，本次无变化 |
| officeai/adapter、officeai/web | UNLICENSED | 自研镜像，非第三方依赖 |

---

## C. 文档与实现一致性抽查（真实测试库 + 源码常量对照）

抽查方法：`pg_get_indexdef` / `pg_constraint` / `count(*)` 查真实测试库（`officeai_app` @ `127.0.0.1:55433/officeai`），并与 `docs/architecture-phase2.md` §2.4/§4/§5 与源码常量对照。

### C-1 `payment_callback_log` 唯一索引 = `(channel, external_txn_id)`

| 证据 | 内容 |
| --- | --- |
| 实测（DB） | `uq_callback_external -> CREATE UNIQUE INDEX ... USING btree (channel, external_txn_id)` |
| 另有 | `payment_callback_log_pkey -> (tenant_id, callback_id)` |
| 文档（§4.5 / §5 第 586-587 行） | `uq_callback_external`「回调幂等（PRD 验收 3）」 |
| 结论 | ✅ 一致 |

### C-2 `usage_event` 唯一键与 `idem_key` 取值

| 证据 | 内容 |
| --- | --- |
| 实测（DB） | `uq_usage_event_idem -> (tenant_id, capability, idem_key)`；主键 `(tenant_id, event_id)` |
| 文档（§2.5 第 2 条 / §5 第 586 行） | 「`idem_key` = `generation_id`（对话）/ `document_id + run_id`（文档处理）/ `task_id`（自动化任务）」 |
| 实测（源码 `business/usage_service.py`） | `:313 idem_key=generation_id`（chat）、`:349 idem_key=f"{document_id}:{run_id or 'run0'}"`（doc_process）、`:383 idem_key=task_id`（automation） |
| 结论 | ✅ 一致（复合键的分隔符 `:` 为实现细节，文档以「+」示意） |

### C-3 `segment_map.segment_key` 生成口径 = `seg_` + sha256[:16]

| 证据 | 内容 |
| --- | --- |
| 实测（DB 列） | `segment_key text`、`document_id text`、`segment_index integer`、`content_sha256 character(64)`；主键 `PRIMARY KEY (tenant_id, segment_key)` |
| 实测（源码 `business/segment_service.py:88-102`） | `raw = f"{document_id}|{int(segment_index)}|{digest}"` → `short = sha256(raw)[:16]` → 返回 `f"seg_{short}"`；`digest = content_sha256(content)`（先空白归一后再 SHA-256） |
| 文档（§2.4 第 338 行） | `segment_key = "seg_" + sha256(f"{document_id}\|{segment_index}\|{content_sha256}").hexdigest()[:16]` |
| 结论 | ✅ 一致（含「先 `strip()` 与空白归一」的前置口径） |

### C-4 `department` 层级约束 = 5 层

| 证据 | 内容 |
| --- | --- |
| 实测（DB CHECK） | `department_level_check -> CHECK (((level >= 1) AND (level <= 5)))` |
| 文档（§5 DDL） | `level SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5)` |
| 结论 | ✅ 一致 |

### C-5 `permission_catalog` 权限点数 = 20

| 证据 | 内容 |
| --- | --- |
| 实测（DB 计数） | `total=20`（business=16、ops=4）；分组 `{audit:1, billing:2, knowledge:7, member:4, ops:4, tool:2}` |
| 文档（§4.8 标题 / §5 第 15 节种子注释） | 「权限点与端点映射矩阵（**16 业务 + 4 运营**）」、「权限点 20 条」 |
| 结论 | ✅ 一致 |

### C-6 `billing_plan` 种子数 = 3

| 证据 | 内容 |
| --- | --- |
| 实测（DB 行） | 3 行：`free 免费版` / `professional 专业版` / `enterprise 企业版` |
| 文档（§5 表清单第 470 行） | `billing_plan` 行数 3 ✔（§5 第 15 节种子亦为 3 条） |
| 结论 | ✅ 一致 |

### C 小节结论

6 处抽查**全部一致**，无文档与实现漂移。

---

## D. 阶段一遗留项复核

### D-1 `/readyz` 的 `db_privilege` 自检仍生效

实测方式：以两种角色分别构造应用并请求 `/readyz`（未跑 pytest，直接 `create_app` + `TestClient`）。

| 角色（DSN 用户名） | `pg_roles` 标志 | `/readyz` 结果 | `db_privilege` 检查项 |
| --- | --- | --- | --- |
| `officeai_app`（应用角色） | `rolsuper=false`、`rolbypassrls=false` | **HTTP 200 / code=0 / success** | `ready=true`「数据库连接权限正常」 |
| `officeai`（超级用户） | `rolsuper=true`、`rolbypassrls=true` | **HTTP 500 / code=30004 / message=not_ready** | `ready=false`「数据库连接权限配置不正确」，并输出日志事件 `db_privilege_superuser_detected`（含 hint「OA_DATABASE_URL 使用了带 BYPASSRLS 的账号，行级安全策略会被绕过」） |

**结论**：✅ 自检仍生效，且为 fail-closed（超级用户 → 整体 `not_ready`，不会带病上线）。

### D-2 阶段一容器清单 ↔ 当前运行态

| 证据 | 内容 |
| --- | --- |
| 阶段一验收报告记录 | 第 9/16/28 行：「**15 个容器全部 Up**（5 healthy）」；第 175 行（升级后）：「**15+ 容器**全栈重建 ✅ 全部 Up，Milvus 3.0.3 healthy」 |
| 主理人转述口径 | 「**16 容器**清单」 |
| 编排文件声明 | `deploy/docker-compose.yml`（私有化）**18 个 service**；`deploy/docker-compose.saas.yml` **16 个 service** |
| 当前实跑 | `docker ps` 中 `oa-*` 容器 **17 个**（7 healthy：adapter / web / object-store / milvus-object-store / postgres / milvus / valkey） |
| 差异 | ① 运行数 17 vs 阶段一记录 15 → **+2**；② 私有化编排声明 18、实际运行 17 → **`dify-web` 已声明但无对应运行容器**；③ 另有 2 个非本栈容器 `moy-qa-pg` / `moy-pg-test2`（QA 临时库）在运行 |

当前 17 个 `oa-*` 容器明细：

```
oa-adapter(healthy) oa-web(healthy) oa-dify-api oa-dify-worker oa-dify-worker-beat
oa-dify-sandbox oa-plugin-daemon oa-keycloak oa-prometheus oa-ssrf-proxy
oa-object-store(healthy) oa-milvus-object-store(healthy) oa-postgres(healthy)
oa-milvus(healthy) oa-etcd oa-milvus-etcd oa-valkey(healthy)
```

**结论**：⚠️ 三处口径不一致（15 / 16 / 17），且 `dify-web` 声明未运行——事实已列，判断由主理人给出。

---

## 阻断项（只列事实，判断由主理人做出）

| # | 事实 | 证据位置 |
| --- | --- | --- |
| 1 | A 节四件门禁（依赖 / 镜像 / 端点严格 / 错误码严格）退出码**全部为 0**；B 节镜像 14↔14、依赖 15 条协议全白名单；C 节 6 处文档↔实现抽查全部一致；D-1 权限自检生效 | 本文件 A / B / C / D-1 |
| 2 | `ubuntu/squid:6.13-25.04_beta` 在 `images.lock` 中登记协议为 **GPL-2.0**，而架构 §11 将 GPL-2.0 列为 🔴 黑名单；该条目阶段一即存在、本次未变化；`validate_images.py`（只查 name:tag）与 `validate_deps.py`（只查 Python 依赖）**均不校验镜像协议**——镜像协议目前无门禁覆盖 | `deploy/images.lock` 的 `license` 字段；两个 validate 脚本 |
| 3 | **容器口径三处不一致**：阶段一报告 15 / 主理人转述 16 / 私有化编排声明 18 / 实际运行 17；且编排声明了 `dify-web` 但无运行容器 | D-2 |
| 4 | `deploy/validate_deps.py` 输出含未解析的字面 `\n`（`\n[OK] 依赖门禁通过`），属输出格式瑕疵，不影响退出码 | A-1 原始输出 |
| 5 | 全组件升级提交 `c9ce65e` 带破坏性变更标记（`feat(deploy)!`），12 个镜像 tag 相对阶段一全部上移；`images.lock` 条目数未变（14 → 14） | B-1 版本变更表 |
