# Sprint 0 脚手架 QA 验证报告

> ⚠️ **本报告已有复核追加版**：工程师修复提交 `3acb0a5` 后，我于同日完成第二轮独立复核，
> 5 个缺陷全部验证通过，但**新发现 1 个 P0 部署阻断缺陷**（MinIO 镜像标签在上游不存在）。
> 复核结果见 **[`docs/archive/qa-report-sprint0-addendum-v1.1.md`](./docs/archive/qa-report-sprint0-addendum-v1.1.md)**。
> 本文档（v1.0）内容未作删改，仍然有效。

| 项 | 内容 |
| --- | --- |
| 报告版本 | v1.0 |
| QA 工程师 | 严过关（Yan） |
| 验证对象 | Sprint 0 脚手架，git commit `f46544f`，main / develop 分支 |
| 验证日期 | 2026-10-21 |
| 验收基准 | `docs/architecture-v1.0.md`（第 3 / 6 / 8 / 9 章）、`docs/plan-v1.0.md`、`docs/prd-v1.0.md` |
| 验证方式 | 独立重跑（未采信工程师自述结论），自建`qa_probe/` 探针脚本实测运行时行为 |

---

## 一、结论摘要

### 判定：**有条件通过**

Sprint 0 脚手架的**工程质量显著高于基线预期**：ruff / mypy / compileall 三项静态门禁全绿，
374 个用例全部通过、适配层覆盖率 90.95%（门禁要求 ≥80%），30 个业务端点与架构文档
**逐条完全一致**，38 个错误码与文档 3.0.3 **逐字一致**，底座直连红线**零命中**，
信息泄露防护经金丝雀注入验证有效。

但存在 **2 个 P1 缺陷**，均为同一类根因：**`create_app(settings)` 注入的配置在鉴权/租户
链路被忽略**，导致显式注入场景下**OIDC 签名验证被完全跳过**（伪造令牌可被接受）。
另有 3 个 P2 缺陷（限流器未接入请求链路、上传超限未按20008 拦截、幂等指纹不含请求体）。

**核心结论一句话**：静态门禁与接口契约层面可直接放行；鉴权配置注入缺陷必须在Sprint 1
接入真实底座前修复，否则生产切`OA_AUTH_MODE=oidc` 时一旦走到非环境变量路径将出现
**任意伪造令牌可登录**。

---

### 缺陷分级统计

| 级别 | 数量 | 编号 |
| --- | --- | --- |
| P0 | 0 | — |
| P1 | 2 | BUG-01、BUG-02 |
| P2 | 3 | BUG-03、BUG-04、BUG-05 |
| 合计 | **5** | — |

### 路由判定

**源码 Bug → 路由 Engineer（寇豆码）**，需修复 BUG-01 ~ BUG-05。
其中 BUG-01 / BUG-02 为安全相关，建议合并为一次修复提交。

---

## 二、验证项逐条结果

### A. 代码质量 —— 全部通过

| 项 | 命令 | 真实输出 | 判定 |
| --- | --- | --- | --- |
| A-1 ruff lint | `ruff check .` | `All checks passed!`（59 个文件参与检查，`--show-files` 计数 59） | ✅ 通过 |
| A-2 ruff format | `ruff format --check .` | `59 files already formatted` | ✅ 通过 |
| A-3 mypy | `mypy` | `Success: no issues found in 42 source files` | ✅ 零 error / 零 warning |
| A-4 语法编译 | `python -m compileall -q apps/adapter/app apps/adapter/tests` | 退出码 0，无输出（59 个 .py 全部可编译） | ✅ 通过 |

补充核查：`ruff` 版本 0.17.1，`SLF001`（flake8-self）规则真实可用，
非"选择了不存在的规则集而空过"。

### B. 测试与覆盖率 —— 通过，但存在用例设计缺陷

| 项 | 命令 | 真实输出 | 判定 |
| --- | --- | --- | --- |
| B-1 环境 | 复用既有 venv `envs\default`（Python 3.13.14），未全局安装任何包 | pip list 确认 pytest 9.1.1 / pytest-cov 7.1.0 / ruff 0.17.1 / mypy 2.4.0 | ✅ 合规 |
| B-2 pytest | `pytest tests -q --cov=app --cov-report=term-missing --cov-fail-under=80` | **374 passed, 1 warning in 47.63s**，0 failed / 0 skipped / 0 error | ✅ 通过 |
| B-3 覆盖率 | 同上 | **TOTAL 90.95%**（2474 语句 / 150 Miss / 422 Branch / 82 BrPart），门禁 80% | ✅ 达标，超出 10.95 个百分点 |
| B-4 CI 门禁一致性 | 对比 `ci-adapter.yml` | 本地执行的参数与 CI 步骤（第 79-83 行）完全一致 | ✅ 一致 |

**覆盖率分模块明细（低于 90% 的模块）**：

| 模块 | 覆盖率 | 缺口行|
| --- | --- | --- |
| `app/services/rag_service.py` | 75.62% | 156-170, 243-265, 322-326, 384-385, 409, 441-455 |
| `app/core/logging.py` | 74.24% | 67-68, 81-87, 127 |
| `app/api/v1/tool.py` | 81.63% | 99, 207-213, 246 |

三处缺口均为**下游协议转换分支**（`downstream_enabled=true` 路径），桩模式下无法覆盖，
属预期缺口，非缺陷。

**B-5 测试是否流于形式 —— 抽查结论：不是空壳，但有结构性盲区**

正面判断（3 条具体依据）：

1. **响应体契约被真实断言**，非仅状态码。以 `tests/integration/test_endpoints.py:58-66`
   为例，用例同时断言 `status_code == 400`、`body["code"] == 20001`、
   `body["message"] == "请求内容不完整，请检查后重试"` 三项，把业务语言文案也纳入契约。
2. **断言密度健康**。`test_endpoints.py` 46 个用例 / 110 条assert（2.4 条/用例），
   `test_model_service.py` 19 用例 / 48 assert（2.5 条/用例）；
   若为空壳测试，断言密度通常 ≤1。
3. **存在负向与边界用例**，非只测 happy path。如 `test_m1_invalid_profile_returns_20001`
   用 `"profile": "gpt-4o"` 验证枚举白名单，`test_m1_empty_messages_returns_20001`
   验证空数组边界。

**但存在一个结构性盲区（构成 BUG-01/02 未被自身测试发现的原因）**：
`tests/unit/test_security.py` 的 16 个用例**全部直接调用 `AuthContext.resolve()` /
`resolve_tenant_id()` 等单元函数并显式传入 `settings` 参数**，而非通过 FastAPI 依赖链
（`get_auth_context` / `get_request_context`）发起真实请求。
经检索，全测试套件中 `auth_mode="oidc"` 的用例数为 **0**：

```
$ grep -rn "auth_mode=\"oidc\"" tests/
（无输出）
```

这意味着「配置注入是否在依赖链中生效」这一关键路径**从未被测试覆盖**，
因此 BUG-01/02 在374 个用例全绿的情况下依然存在。详见缺陷清单。

### C. 接口契约符合性 —— 通过（本项为本期最关键交付物）

**C-1 端点枚举（应用自身 OpenAPI vs 架构文档清单）**

从 `create_app().openapi()` 实际枚举：

```
=== BUSINESS ENDPOINTS: 30 ===
GET     /api/v1/agent/tasks
POST    /api/v1/agent/tasks
GET     /api/v1/agent/tasks/{task_id}
POST    /api/v1/agent/tasks/{task_id}/cancel
GET     /api/v1/agent/tasks/{task_id}/events
GET     /api/v1/agent/tasks/{task_id}/steps
POST    /api/v1/identity/audit-logs
POST    /api/v1/identity/organizations/{org_id}/invitations
GET     /api/v1/identity/organizations/{org_id}/members
POST    /api/v1/identity/organizations/{org_id}/roles
GET     /api/v1/identity/profile
POST    /api/v1/identity/verify
GET     /api/v1/knowledge/bases
POST    /api/v1/knowledge/bases
POST    /api/v1/knowledge/bases/{base_id}/documents
DELETE  /api/v1/knowledge/bases/{base_id}/documents/{doc_id}
GET     /api/v1/knowledge/bases/{base_id}/documents/{doc_id}
POST    /api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex
POST    /api/v1/knowledge/search
GET     /api/v1/model/catalog
POST    /api/v1/model/generate
POST    /api/v1/model/generate/stream
GET     /api/v1/model/usage
GET     /api/v1/tools/connections
POST    /api/v1/tools/connections
DELETE  /api/v1/tools/connections/{conn_id}
POST    /api/v1/tools/execute
GET     /api/v1/tools/registry
POST    /api/v1/tools/sync-jobs
GET     /api/v1/tools/sync-jobs/{job_id}

=== NON-BUSINESS PATHS: 2 ===
GET     /healthz
GET     /readyz
```

**比对结果：30 个业务端点 + 2 个健康探针，与架构文档端点清单逐条一致，缺失 0、多余 0、
路径不一致 0、方法不一致 0。**

文档侧独立复核（按域统计清单行）：

```
M 域: 4 条 / R 域: 7 条 / A 域: 6 条 / T 域: 7 条 / U 域: 6 条 / 总计: 30
```

**关于「架构师交付摘要称 29 个端点」这一不一致的说明**：
经全文检索 `docs/architecture-v1.0.md`，**文档正文中不存在"29 个端点"的表述**，
其端点清单（M1-M4 / R1-R7 / A1-A6 / T1-T7 / U1-U6）实际合计即为 **30** 个。
`docs/api/README.md` 第 2.1 节亦明确记载"**业务端点合计 30**"。
因此判定为：**"29" 是交付摘要的口头/汇报误差，架构文档本身自洽且为 30**；
**代码实现按 30 落地，与文档一致**，CI 门禁
（`ci-adapter.yml:224` `if len(business) != 30`）亦锁定 30，三方一致。
建议在下一版架构文档中补一句显式的"端点总数：30"，消除歧义。

**C-2 抽查 8 个端点逐字段契约比对（覆盖 5 个接口域）**

自建`qa_probe/probe_contract.py`，共 **34 项断言全部通过**：

| 域 | 端点 | 关键校验点 | 结果 |
| --- | --- | --- | --- |
| 模型 M1 | `POST /model/generate` | 响应含 `generation_id`；字段全 snake_case；缺 `messages`→20001；非法 `profile`→20001；非法 `role`→20001 | ✅ 5/5 |
| 模型 M3 | `GET /model/catalog` | 不泄露底座模型名（扫 gpt/claude/qwen/deepseek/dify/openai/llm/model_name，命中 0） | ✅ |
| 模型 M4 | `GET /model/usage` | 业务化口径 `period/conversation_count/document_qa_count/quota`；非法 `period`→20001 | ✅ |
| RAG R1/R2 | `POST+GET /knowledge/bases` | `base_id` 前缀 `kb_`；含 `name/created_at`；非法 `visibility`→20001；空 name→20001；`page=0`→20001；`page_size=9999`→20001 | ✅ 7/7 |
| RAG R7 | `POST /knowledge/search` | 字段 snake_case；`top_k` 超限→20001 | ✅ |
| Agent A1/A3 | `POST+GET /agent/tasks` | `task_id` 前缀 `task_`；状态为自研三态；方法不匹配→统一信封 | ✅ |
| 工具 T1/T5 | `GET /tools/registry`、`POST /tools/execute` | 未注册类型→13001；不支持操作→13005 | ✅ |
| 权限 U1/U2 | `POST /identity/verify`、`GET /identity/profile` | 含 `allowed` 判定；租户 ID 为 `tnt_` 自研前缀不泄露底座主体 | ✅ |

实测响应抽样（真实 body）：

```json
// POST /api/v1/model/generate
{"code":0,"message":"success","data":{"generation_id":"gen_6184CE2661524FFD98336ED0",
 "content":"（演示环境）已收到你的问题，智能助手将在正式接入后返回回答。",
 "finish_reason":"stop","usage":{"prompt_units":2,"completion_units":24,"total_units":26},
 "citations":[]}}
```

底座形态防火墙有效：`answer`→`content`、`metadata.usage.*_token`→`usage.*_units`、
`retriever_resources`→`citations` 转换正确，对外无底座原生字段。

**C-3 统一响应格式（6 个端点实测）**

| 端点 | HTTP | code | message | data |
| --- | --- | --- | --- | --- |
| `GET /model/catalog` | 200 | 0 | `'success'` | dict |
| `GET /model/usage` | 200 | 0 | `'success'` | dict |
| `GET /knowledge/bases` | 200 | 0 | `'success'` | dict |
| `GET /tools/registry` | 200 | 0 | `'success'` | dict |
| `GET /agent/tasks` | 200 | 0 | `'success'` | dict |
| `GET /identity/profile` | 200 | 0 | `'success'` | dict |

失败响应（含401 / 20001 / 30003）经`keys == ['code','data','message']` 严格校验，
且 `data` 均为 `null`。**判定：严格符合 3.0.1 ✅**

**C-4 链路追踪**

`X-Trace-Id` 入参 `my-trace-123` → 响应头回写 `my-trace-123`，透传正确 ✅

### D. 错误码与业务语言 —— 2 个 P1 缺陷

**D-1 错误码覆盖度（3.0.3 全码段核对）**

程序化逐码比对（文档 3.0.3 表格 vs `ERROR_REGISTRY`）：

```
注册码总数: 38
文档中未找到的码: 无
message/http 不一致: 无
文档码数: 38  代码码数: 38
文档有代码无: 无
代码有文档无: 无
```

7 个码段 **20001-20008(8) / 10001-10005(5) / 11001-11006(6) / 12001-12005(5) /
13001-13006(6) / 14001-14004(4) / 30001-30004(4) = 38**，无缺失、无多余，
且**每个 message 文案与 HTTP 状态码均与文档逐字一致**。判定 ✅

**D-2 错误路径实测**

| 场景 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- |
| 缺 `Authorization` 头 | 401 + 20002 + "登录状态已失效，请重新登录" | `HTTP 401 {"code":20002,"message":"登录状态已失效，请重新登录","data":null}` | ✅ |
| 非 Bearer 前缀（Basic） | 401 + 20002 | `HTTP 401 code=20002` | ✅ |
| 空 Bearer 值 | 401 + 20002 | `HTTP 401 code=20002` | ✅ |
| 垃圾令牌串 | 401 + 20002 | `HTTP 401 code=20002` | ✅ |
| 过期令牌（oidc 验签） | 401 + 20002 | `HTTP 401 code=20002` | ✅ |
| **错误签名令牌（oidc 验签）** | 401 + 20002 | **`HTTP 200 code=0`（!!）** | ❌ **BUG-01** |
| 请求体缺必填字段（6 个域各测） | 400 + 20001 | 全部 `HTTP 400 code=20001` | ✅ |
| 非法 JSON 报文 | 20001 | `HTTP 400 code=20001` | ✅ |
| 资源不存在（5 个场景） | 11001/12001/13002/20004 | 分别命中 20004 / 12001 / 13002 / 20004 | ✅ |
| **文档不存在 `GET .../documents/{doc_id}`** | 11001 | **`HTTP 200 code=0`（桩模式直接返回 PROCESSING）** | ⚠️ 见下|

关于"文档不存在返回 200"：经核`app/services/rag_service.py:313-319`，
`mock_mode` 下有意跳过存在性校验直接返回 `IndexStatus.PROCESSING`，
属**桩模式设计意图**（真实存在性校验需下游启用后由 `_transform_index_status` 承担）。
但存在**文档一致性问题**：`11001`（知识库不存在）目前在 `app/` 下**无任何业务调用点**
（仅在 `errors.py` 注册 + `ErrorMapper` 关键词映射中出现），属事实上的死码。
建议在接入真实下游时补齐调用点，或在文档中标注"T01 桩模式下不可达"。列为 P2 观察项。

**D-3 业务语言检查（架构红线）**

扫描全部 38 条 error message 是否含技术词
（error/exception/traceback/stack/NoneType/Timeout/500/栈/报错/异常/null/http/sql）：

```
命中: (13006, '异常', '第三方工具返回了异常结果')
命中: (30004, '异常', '服务配置异常，请联系管理员')
```

**判定：非缺陷（我的扫描规则过宽，测试脚本 Bug）。** 已程序化复核：

```
13006: 文档='第三方工具返回了异常结果'  代码='第三方工具返回了异常结果'  逐字一致=True
30004: 文档='服务配置异常，请联系管理员'  代码='服务配置异常，请联系管理员'  逐字一致=True
```

中文语境下"异常结果""配置异常"属正常业务表述，且**与架构文档 3.0.3 钦定文案逐字一致**，
改动反而会违反"错误码注册制"。以纯英文技术词（error/exception/traceback/stack/NoneType）
重新扫描，**命中数为 0**，业务语言红线 ✅

**D-4 信息泄露检查（金丝雀注入）**

monkeypatch `RagService.list_bases` 抛出携带金丝雀串的异常：

```python
raise RuntimeError("SECRET-CANARY-abc123 /abs/internal/path.py line 42")
```

实测响应：

```
HTTP 500 body={"code":30003,"message":"系统繁忙，请稍后再试","data":null}
泄露扫描命中: []
```

检查项 `Traceback` / `File "` / `app/` / `site-packages` / `Exception` / `NoneType` /
异常类名 / 内部路径 —— **全部未出现在响应体**；堆栈仅进日志
（`unhandled_exception` 结构化日志 level=error）。另测超长路径参数（5000 字符）、
空 ID、SQL 注入式 ID（`%27%20OR%201=1--`）均无泄露，注入式 ID 返回 405 统一信封。判定 ✅

**D-5 幂等（20007 / 20005）**

| 场景 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- |
| 同 key 同 body 连发两次 | 第2 次 20007 | 第1 次 `HTTP 200 code=0 data={base_id...}`；第2 次 `HTTP 200 code=20007 msg=重复的请求已被忽略` | ✅ |
| 同 key **不同 path** | 20005 冲突 | `HTTP 409 code=20005操作冲突，请刷新后重试` | ✅ |
| 写接口缺 `Idempotency-Key` | 20001 | `HTTP 400 code=20001` | ✅ |
| **同 key 同 path 不同 body** | 应识别为不同请求 | **返回 20007（按重复处理）** | ⚠️ **BUG-05** |

BUG-05 说明：指纹 `build_fingerprint()` 定义为
`method:path:tenant_id:user_id`（`app/core/idempotency.py:485`），**不含请求体**。
故"同 key + 同 path + 不同 body"无法被识别为键复用冲突，会静默回放首次结果。
这是**语义设计缺陷**（`tests/conftest.py:189-190` 的注释也自认"同一幂等键被复用于
不同请求会返回 20005"，但 body 不同的情况无法触发）。列为 P2。

**D-6 租户隔离头（3.0.2）**

| 场景 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- |
| 私有化缺 `X-Tenant-Id` | 回退 `default`，仍 200 | `HTTP 200 code=0` | ✅ |
| SaaS 无租户头且令牌无 claim | 20001 | `HTTP 400 code=20001` | ✅ |
| SaaS 从 claim 推导 | 200 | `HTTP 200 code=0` | ✅ |
| SaaS 头优先于 claim | 200 | `HTTP 200 code=0` | ✅ |
| **私有化模式 + 令牌携带 `tenant_id=attacker_tenant`** | 租户锁定 `default` | **`tenant.tenant_id = attacker_tenant`（隔离被绕过）** | ❌ **BUG-02** |

### E. 架构红线与合规 —— 通过

**E-1 底座直连扫描**

```
$ grep -rniE "dify|milvus|litellm|5001" apps/web/
（无输出，退出码 1）
```

`apps/web` 全目录（package.json、源码、注释、Dockerfile、.env.example）**零命中** ✅

适配层分层约束：

```
$ grep -rniE "dify|milvus|litellm|5001" apps/adapter/app/api/ apps/adapter/app/schemas/
（无输出，退出码 1）
```

`api/` 与 `schemas/` 层**零命中**；底座术语仅存在于 `clients/`、`services/`、
`core/config.py`、`registry/`（配置文件键名）—— 符合"唯一允许接触底座"的红线 ✅

全仓库底座术语分布（按文件计数）：`deploy/docker-compose.yml` 60、
`docker-compose.saas.yml` 44、`clients/dify_client.py` 26、`services/rag_service.py` 21
—— 全部位于编排层或适配层内部，无外泄至前端 ✅

**E-2 依赖白名单核对**

`apps/adapter/pyproject.toml` 14 个运行时依赖与架构文档 8.1 清单**逐项一致**，
dev 依赖 5 项亦一致，**无未登记依赖** ✅

`apps/web/package.json` 19 个dependencies + 10 个 devDependencies，对照 8.2 清单：
全部在册。逐项核对结果：next/react/react-dom/typescript/tailwindcss/@radix-ui/*/
class-variance-authority/clsx/tailwind-merge/lucide-react/zustand/
@tanstack/react-query/react-hook-form/@hookform/resolvers/zod/sonner/date-fns/
eslint/prettier 均已登记。

**未登记项 2 个（均为构建期工具，不影响运行时，无协议风险）**：

| 包 | 版本 | 协议 | 说明 |
| --- | --- | --- | --- |
| `tailwindcss-animate` | ^1.0.7 | MIT | Tailwind 动画插件，8.2 未单列 |
| `autoprefixer` | ^10.4.19 | MIT | PostCSS 前缀补全 |
| `postcss` | ^8.4.38 | MIT | CSS 处理 |

**无 GPL / AGPL / SSPL 风险项** ✅，建议将上述 3 个补登记入 8.2（文档滞后，非代码问题）。

**E-3 部署合规**

| 检查项 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- |
| Redis → Valkey（D-06） | 已改 | `valkey/valkey:7.2-alpine`，容器名 `oa-valkey` | ✅ |
| MinIO 锁 AGPL 前版本线 | RELEASE.2021-04-22 | `minio/minio:RELEASE.2021-04-22T23-11-00Z` | ✅ |
| MinIO 独立容器运行 | 独立进程/容器 | `container_name: oa-milvus-minio`（与 Milvus 分离） | ✅ |
| `DEPLOY_MODE` 双模式切换 | 存在 | 私有化 `docker-compose.yml:327 OA_DEPLOY_MODE: private`；SaaS `docker-compose.saas.yml:257 OA_DEPLOY_MODE: saas` | ✅ |
| `.env.example` 用 `OA_` 前缀、无真实凭据 | 满足 | 9 处敏感项全部为 `CHANGE_ME_*` 占位符 | ✅ |

**E-4 CI 门禁（三份 workflow）**

| Workflow | 门禁项| 判定 |
| --- | --- | --- |
| `ci-adapter.yml` | ruff check（:59）/ ruff format --check（:65）/ mypy（:71）/ pytest+`--cov-fail-under=80`（:79-83）/ YAML+JSON 语法校验 / 注册表一致性 / OpenAPI 导出 + 端点数 30 断言 + 底座术语扫描 | ✅ 完整 |
| `ci-web.yml` | eslint（:57）/ `tsc --noEmit`（:63）/ `next build`（:69）/ Prettier 校验（:89） | ✅ 完整 |
| `dependency-scan.yml` | 底座直连扫描（:50）/ 前端依赖无底座 SDK（:76）/ 适配层唯一出口（:89）/ Python SCA（:131）/ Node SCA（:144）/ 协议黑名单判定（:155）/ 依赖版本约束落在架构文档 8 章（:256） | ✅ 完整 |

**YAML 语法验证（Python yaml 解析全部 8 个 yml/yaml）**：

```
[OK] .github/workflows/ci-adapter.yml    [OK] deploy/docker-compose.saas.yml
[OK] .github/workflows/ci-web.yml        [OK] deploy/prometheus.yml
[OK] .github/workflows/dependency-scan.yml [OK] apps/adapter/app/registry/profile_routes.yaml
[OK] deploy/docker-compose.yml           [OK] apps/adapter/app/registry/tool_registry.yaml
ALL_VALID
```

JSON 4 个（`.eslintrc.json` / `apps/web/package.json` / `tsconfig.json` / 根 `package.json`）
全部 `[OK]` ✅

CI 内嵌脚本实跑（注册表一致性校验）：

```
[OK]   email 能力清单一致 (3 项)
[OK]   github 能力清单一致 (2 项)
退出码=0
```

**E-5 安全抽查**

全仓库扫描 `password=` / `secret=` / `api_key=` / `token=` 后接非占位值
（已排除 `CHANGE_ME` / `placeholder` / `your-` / `xxx` / `dummy` / `test-` 等占位模式）：

```
./apps/adapter/qa_probe/probe_runtime.py:56,65,136   ← 仅命中 QA 自建探针，非业务代码
---END---
```

**业务代码零硬编码凭据** ✅（我的探针脚本已标注，将随报告一并清理或纳入 `.gitignore`）

`.gitignore` 覆盖：`.venv/` COVERED / `node_modules/` COVERED /
`__pycache__/` COVERED / `.env` COVERED ✅

### F. 结构与规范 —— 通过

**F-1 目录结构 vs 架构文档 6.1**：逐条比对，**缺失 0**。重点确认：

| 预期项 | 状态 |
| --- | --- |
| 根 `package.json`（workspace 声明） | ✅ 存在 |
| `docs/api/` | ✅ 存在（含 README.md，契约基线说明完整） |
| `docs/adr/0001` 决策记录 | ✅ 存在（ADR-0001 采纳"适配层唯一原则"，含状态/日期/决策者） |
| `apps/adapter/app/{core,middleware,api/v1,schemas,services,clients,registry,tools}` | ✅ 全部存在且文件名逐字匹配 |
| `apps/web/src/{app,components/ui,lib,hooks,styles}` | ✅ 全部存在 |
| `deploy/{k8s,offline}` | ✅ 存在（含 README 占位，S4 交付） |

`apps/adapter/qa_probe/` 为 QA 本次新增，非多余文件。

**F-2 命名规范抽查（各 3 组实例与反例）**

资源 ID 前缀 —— 实测运行时真实产出：

| 规范 | 实例 |
| --- | --- |
| `kb_` | `kb_21A173966A7743EB8BA09ABD` |
| `task_` | `task_27DB53BA014D4901AAFB0299` |
| `gen_` | `gen_6184CE2661524FFD98336ED0` |

反例检索：全仓库**未发现**任何缺前缀或使用 `uuid`/`id` 裸值的资源 ID ✅
（生成实现：`rag_service.py:66-81`、`agent_service.py:72`、`tool_service.py:59`）

**ULID 格式说明**：实现采用 `uuid.uuid4().hex[:24].upper()`（24 位十六进制），
而 `security.py:42` 注释与 `rag_service.py:70` docstring 示例写作 `kb_01HF8...`（ULID 风格）。
二者**不一致**：实现是 UUID 截断，非真正 ULID（无时间排序性）。当前阶段不构成功能缺陷
（ID 仅作不透明标识），但注释与实现不符会误导后续按时间排序的开发者。列为 P2 观察项。

环境变量：`OA_` 前缀共 13 个唯一变量，**无任何非 `OA_` 前缀的环境变量引用** ✅

枚举小写下划线：实测 `general` / `long_context` / `organization` / `processing` /
`completed` / `communication` 全部符合 ✅

**F-3 git 卫生**

| 项 | 实测 | 判定 |
| --- | --- | --- |
| 提交信息规范 | `feat(scaffold): 初始化 monorepo 脚手架与统一适配层契约基线` | ✅ 符合 `type(scope): 说明` |
| main / develop 分支 | 均存在，当前 `* main` | ✅ |
| 分支内容一致性 | `git log main..develop` 为空（两者同指f46544f） | ✅ |
| 远程配置 | `git remote -v` 无输出 | ✅ 未配置远程，无误配置 |
| 工作区干净度 | 仅 `?? .workbuddy/`、`?? apps/adapter/qa_probe/`（均为本次 QA 产物） | ✅ 业务文件无脏改 |

### G. 前端骨架 —— 通过（受环境限制，静态验证）

**G-1 配置语法**：JSON 3 个 + YAML/TS 配置文件全部可解析 ✅

**G-2 六大页面占位页 + 登录页**：

```
src/app/(auth)/login/page.tsx        ✅
src/app/(main)/chat/page.tsx        ✅
src/app/(main)/knowledge/page.tsx   ✅
src/app/(main)/plugins/page.tsx     ✅
src/app/(main)/workflows/page.tsx   ✅
src/app/(main)/team/page.tsx        ✅
src/app/(main)/profile/page.tsx     ✅
```

六大页面齐备，登录页存在 ✅

**G-3适配层唯一出口**：

```
$ grep -rnE "fetch\(|axios|XMLHttpRequest|https?://[a-z0-9]" src/ | grep -v api-client.ts
（无输出）
```

`src/lib/api-client.ts` 是**唯一**发起请求的文件，地址统一来自
`NEXT_PUBLIC_ADAPTER_BASE_URL`（默认 `http://127.0.0.1:8000`），无第二处硬编码 ✅

**G-4 `business-errors.ts` code→业务语言映射兜底**：

-覆盖码数：**38 个**，与后端 `ERROR_REGISTRY` **完全对齐**
  （10001-10005 / 11001-11006 / 12001-12005 / 13001-13006 / 14001-14004 /
  20001-20008 / 30001-30004）
- 兜底逻辑：`BUSINESS_ERROR_MESSAGES[code] ?? UNKNOWN_ERROR_MESSAGE`（:78）
- 语义：适配层已返回 message 时直接用，缺失时按 code 回落，两者皆无走通用兜底 ✅

**G-5 组件库集中度**：`src/components/ui/` 下 6 个组件（badge/button/card/dialog/
feedback/input/toast），业务组件（`components/chat/`、`components/knowledge/`）
未自定义重复基础样式 ✅

---

## 三、缺陷清单

### P0（阻断上线）

无。

---

### P1（必须修复，建议 Sprint 1 前完成）

#### BUG-01：`create_app(settings)` 注入的鉴权配置被忽略，OIDC 签名验证可被完全绕过

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `apps/adapter/app/core/security.py:309`（根因）<br>`apps/adapter/app/core/security.py:332`（同源） |
| **严重度** | **P1（安全）** |
| **现象** | `get_auth_context()` 调用 `AuthContext.resolve(extract_bearer_token(request))` 时**未传入 settings**，`AuthContext.resolve` 内部 `settings or get_settings()` 回退到 `get_settings()` 全局单例（`lru_cache`）。因此 `create_app(settings)` 显式注入的配置被完全忽略，鉴权始终按**进程启动时的环境变量单例**执行。 |

**复现命令**：

```bash
cd apps/adapter
"C:/Users/15864/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from app.core.config import Settings, get_settings
from app.main import create_app
from jose import jwt; import time
s = Settings(env='test', deploy_mode='private', downstream_enabled=False,
             auth_mode='oidc', jwt_secret='REAL-SECRET', ratelimit_enabled=False)
now = int(time.time())
forged = jwt.encode({'sub':'attacker','tenant_id':'t1','aud':s.jwt_audience,
                     'iss':s.jwt_issuer,'iat':now,'exp':now+3600},
                    'WRONG-KEY', algorithm='HS256')
with TestClient(create_app(s)) as c:
    r = c.get('/api/v1/model/catalog',
              headers={'Authorization': f'Bearer {forged}', 'X-Tenant-Id':'t1'})
    print('HTTP', r.status_code, r.json().get('code'))
"
```

**期望行为**：`HTTP 401`，`code=20002`
**实际行为**：`HTTP 200`，`code=0` —— **用错误密钥签名的伪造令牌被完全接受**

**风险分析**：
生产部署走环境变量路径（`OA_AUTH_MODE=oidc`），此时 `get_settings()` 单例与配置一致，
**当前生产路径安全**（已实测确认返回 401）。但该缺陷会在以下场景暴露为致命漏洞：
① 任何以 `create_app(Settings(...))` 方式构造应用的位置（测试、集成嵌入、后续 SDK 封装）；
② 若未来有人为可测性改造配置获取方式；③ 单元测试无法验证 oidc 真实行为，
使签名校验缺陷长期潜伏。

**修复建议**：为鉴权与租户依赖注入 settings，例如

```python
async def get_auth_context(request: Request) -> AuthContext:
    auth = AuthContext.resolve(extract_bearer_token(request), get_settings())
    ...
```

或更彻底：将 `settings` 存入 `app.state`，由依赖从 `request.app.state` 读取，
消除对全局单例的依赖。

---

#### BUG-02：私有化模式租户隔离失效，令牌可自带任意租户

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `apps/adapter/app/core/security.py:332`（根因）<br>`apps/adapter/app/core/security.py:213-214`（被绕过分支） |
| **严重度** | **P1（安全 / 多租户数据隔离）** |
| **现象** | `get_request_context()` 调用 `resolve_tenant_id(request, auth)` 未传 settings，导致 `conf.is_private_mode` 判断依据错误配置。私有化模式本应恒返回 `default` 租户（`security.py:213-214`），但实际仍采纳令牌中的 `tenant_id` 声明。 |

**复现命令**：

```bash
cd apps/adapter
"C:/Users/15864/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from jose import jwt; import time
sp = Settings(env='test', deploy_mode='private', downstream_enabled=False,
              auth_mode='stub', jwt_secret='q', ratelimit_enabled=False)
now = int(time.time())
tok = jwt.encode({'sub':'u1','tenant_id':'attacker_tenant','aud':sp.jwt_audience,
                  'iss':sp.jwt_issuer,'iat':now,'exp':now+3600},'q',algorithm='HS256')
with TestClient(create_app(sp)) as c:
    r = c.get('/api/v1/identity/profile', headers={'Authorization': f'Bearer {tok}'})
    print('HTTP', r.status_code, r.json()['data']['tenant'])
"
```

**期望行为**：`tenant.tenant_id == "default"`（私有化单租户天然隔离）
**实际行为**：`{'tenant_id': 'attacker_tenant', 'name': '演示租户', 'plan': 'professional'}`

**风险**：私有化交付形态下，任意用户可通过自造令牌声明访问他人租户数据，
**构成跨租户越权**。与 BUG-01 同根因，一并修复即可。

---

### P2（建议修复，可排入 Sprint 1）

#### BUG-03：限流器未接入请求链路，错误码 20006 在生产路径不可达

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `apps/adapter/app/main.py:105-115`（中间件注册处缺失）<br>`apps/adapter/app/core/ratelimit.py:199-208`（单例定义但无调用方） |
| **严重度** | P2 |
| **现象** | `get_rate_limiter()` / `RateLimiter` 在整个 `app/` 目录下**除自身定义外零调用点**；`main.py` 仅注册 `TraceMiddleware`、`AccessLogMiddleware`、`CORSMiddleware`，未挂载任何限流依赖。 |

**复现命令**：

```bash
cd apps/adapter
"C:/Users/15864/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import os
os.environ.update({'OA_AUTH_MODE':'stub','OA_JWT_SECRET':'q','OA_DEPLOY_MODE':'private',
    'OA_RATELIMIT_ENABLED':'true','OA_RATELIMIT_DEFAULT_PER_MINUTE':'3',
    'OA_DOWNSTREAM_ENABLED':'false'})
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from app.main import create_app
from jose import jwt; import time
now=int(time.time())
t=jwt.encode({'sub':'u1','tenant_id':'t1','aud':'officeai-adapter','iss':'officeai',
              'iat':now,'exp':now+3600},'q',algorithm='HS256')
with TestClient(create_app()) as c:
    for i in range(6):
        r=c.get('/api/v1/model/catalog',headers={'Authorization':f'Bearer {t}'})
        print(f'第{i+1}次 HTTP {r.status_code} code={r.json().get(\"code\")}')
"
```

**期望行为**：第 4 次起返回 `HTTP 429`，`code=20006`，message "当前使用人数较多，请稍后再试"
**实际行为**：6 次全部 `HTTP 200 code=0`（已通过环境变量路径确认，排除配置注入因素）

**说明**：`ratelimit.py` 本身实现完整且被单元测试覆盖（9 用例），覆盖率 93.59%，
仅缺"接入请求链路"这一步。属集成遗漏，非功能缺陷。
**修复建议**：在 `get_request_context` 中按租户+用户维度接入限流校验，
或引入 `slowapi`（已在依赖清单中但尚未使用）作为中间件。

---

#### BUG-04：上传超限返回 11003 而非 20008，且未按文档双码语义处理

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `apps/adapter/app/services/rag_service.py:284-288`<br>`apps/adapter/app/api/v1/rag.py:141-146` |
| **严重度** | P2 |
| **现象** | 3.0.3 定义了两个相关码：`20008`（HTTP 413，文件超出大小限制）与 `11003`（HTTP 400，文档内容过多）。`_validate_document()` 对所有超限一律抛 `11003`；`rag.py:145` 的 `await file.read(max_upload_bytes + 1)` 读取上限+1 字节用于探测，**但未对探测结果单独判断 20008**。另 `rag.py:144` 使用全局 `get_settings()` 而非注入配置，同样受 BUG-01 类问题影响。 |

**复现命令**：

```bash
cd apps/adapter
"C:/Users/15864/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import os
os.environ.update({'OA_AUTH_MODE':'stub','OA_JWT_SECRET':'q','OA_DEPLOY_MODE':'private',
    'OA_MAX_UPLOAD_BYTES':'1024','OA_DOWNSTREAM_ENABLED':'false'})
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from app.main import create_app
from jose import jwt; import time
now=int(time.time())
t=jwt.encode({'sub':'u1','tenant_id':'t1','aud':'officeai-adapter','iss':'officeai',
              'iat':now,'exp':now+3600},'q',algorithm='HS256')
with TestClient(create_app()) as c:
    r=c.post('/api/v1/knowledge/bases/kb_1/documents',
             headers={'Authorization':f'Bearer {t}','Idempotency-Key':'aaaaaaaa-bbbb-4ccc-8ddd-000000000009'},
             files={'file':('big.txt',b'x'*5000,'text/plain')})
    print('HTTP',r.status_code,'code=',r.json().get('code'),'msg=',r.json().get('message'))
"
```

**期望行为**：`HTTP 413 code=20008`「文件超出大小限制（上限 50MB）」
**实际行为**：`HTTP 400 code=11003`「文档内容过多，请拆分后上传」

**说明**：拦截**功能本身有效**（超限确实被拒绝，未发生内存击穿），
问题在于错误码语义分层未按文档落地。
**修复建议**：区分「单文件超过 `max_upload_bytes`」→ 20008/413
与「解析后内容超 `max_document_chars`」→ 11003/400。

---

#### BUG-05：幂等指纹不含请求体，同键同路径不同请求体会静默回放首次结果

| 项 | 内容 |
| --- | --- |
| **文件:行号** | `apps/adapter/app/core/idempotency.py:471-485`（`build_fingerprint`） |
| **严重度** | P2 |
| **现象** | 指纹定义为 `f"{method}:{path}:{tenant_id}:{user_id}"`，不含请求体摘要。故「同 `Idempotency-Key` + 同 method/path/tenant/user + 不同 body」无法被 `check_or_set()` 识别为键复用冲突（20005），而是按重复请求返回 20007 并回放首次结果。 |

**复现命令**：

```bash
cd apps/adapter
"C:/Users/15864/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -c "
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from jose import jwt; import time
s=Settings(env='test',deploy_mode='private',downstream_enabled=False,
          auth_mode='stub',jwt_secret='q',ratelimit_enabled=False)
now=int(time.time())
t=jwt.encode({'sub':'usr_qa','tenant_id':'tnt_qa','aud':s.jwt_audience,
              'iss':s.jwt_issuer,'iat':now,'exp':now+3600},'q',algorithm='HS256')
h={'Authorization':f'Bearer {t}'}
k={'Idempotency-Key':'12345678-1234-4123-8123-123456789abc'}
with TestClient(create_app(s)) as c:
    c.post('/api/v1/knowledge/bases', json={'name':'库A'}, headers={**h,**k})
    r=c.post('/api/v1/knowledge/bases', json={'name':'库B'}, headers={**h,**k})
    print('HTTP', r.status_code, 'code=', r.json().get('code'), r.json().get('message'))
"
```

**期望行为**：`HTTP 409 code=20005`「操作冲突，请刷新后重试」（不同请求体视为键复用）
**实际行为**：`HTTP 200 code=20007`「重复的请求已被忽略」（静默回放「库A」的首次结果）

**风险**：客户端 bug 导致复用同一 key 提交不同内容时，用户会看到"成功"但数据未变更，
且无任何提示，**属静默数据丢失**。
**补充验证**：同 key 不同 path 可正确返回 20005（已实测 `HTTP 409 code=20005`），
说明指纹机制本身工作正常，仅缺 body 维度。
**修复建议**：指纹追加请求体规范化哈希（如 `sha256(canonical_json(body))[:16]`）。

---

### P2 观察项（不单独立缺陷，建议 Sprint 1 一并处理）

| 编号 | 观察点 | 位置 | 建议 |
| --- | --- | --- | --- |
| OBS-01 | `11001`（知识库不存在）在 `app/` 下无业务调用点，桩模式下不可达 | `app/core/errors.py:46,150`<br>`app/services/rag_service.py:313-319` | 接入真实下游时补齐，或文档标注 T01 不可达 |
| OBS-02 | docstring 示例 `kb_01HF8...` 为 ULID 风格，实现却是 `uuid4().hex[:24]`，注释与实现不符 | `app/services/rag_service.py:70,79`<br>`app/core/security.py:42` | 统一表述或改用真ULID（若后续需时间排序） |
| OBS-03 | 架构文档未显式声明"端点总数：30"（清单实为 30，与 `docs/api/README.md` 一致） | `docs/architecture-v1.0.md` 第 3 章 | 补一句显式声明，消除"29/30"歧义 |
| OBS-04 | `tailwindcss-animate` / `autoprefixer` / `postcss` 未登记入架构文档 8.2（均为 MIT，无协议风险） | `apps/web/package.json:32,39,42` | 补登记 |

---

## 四、阻塞项（环境原因 + 建议人工验证方式）

| 编号 | 阻塞内容 | 环境原因 | 建议人工验证方式 |
| --- | --- | --- | --- |
| BLK-01 | `docker compose config` 未执行 | 本机 Docker 大概率不可用 | 已用Python yaml 解析两个 compose 文件**语法合法**；建议人工执行①`docker compose -f deploy/docker-compose.yml config` 验证变量插值与依赖关系②`docker compose config --services` 核对服务清单③ 启动后 `docker compose ps` 确认全部 healthy。**重点核对**：adapter 服务对 `valkey`/`postgres`/`milvus`/`minio` 的 `depends_on` 与健康检查条件、`OA_*` 变量是否全部在 `.env` 中有对应项 |
| BLK-02 | eslint / tsc 未实跑 | 无 `node_modules`，且禁止 `npm install` / 全局安装 | 已静态校验 JSON/TS 配置语法可解析、`api-client.ts` 为唯一请求出口；建议人工执行①`cd apps/web && npm ci` ②`npm run lint`（配置 `--max-warnings=0`）③`npm run typecheck` ④`npm run build`。**重点核对**：`tsconfig.json` 的 `strict` 开关、`api-client.ts` 中 `ApiResponse<T>`泛型与后端 schema 的一致性 |
| BLK-03 | Docker 镜像 AGPL 判定未运行时验证 | 同 BLK-01 | 已核对 MinIO 锁定 `RELEASE.2021-04-22T23-11-00Z`（AGPL 之前的最后版本线）且为独立容器；建议人工执行 `docker run --rm minio/minio:RELEASE.2021-04-22T23-11-00Z --version` 确认标签可拉取，并留存 D-06 法务确认记录 |
| BLK-04 | SCA 协议扫描未实跑 | 需 `pip-audit` / `license-checker` 等工具，属CI 依赖 | 已人工核对 pyproject/package.json 与文档 8 章一致、无 GPL/AGPL/SSPL；建议人工执行 CI `dependency-scan.yml` 的 `sca-scan` job 并归档报告 |

---

## 五、覆盖率与通过率统计

### 静态门禁

| 门禁 | 结果 | 门禁要求 | 判定 |
| --- | --- | --- | --- |
| ruff check | `All checks passed!`（59 文件） | 零告警 | ✅ |
| ruff format --check | `59 files already formatted` | 全部已格式化 | ✅ |
| mypy | `Success: no issues found in 42 source files` | 零 error | ✅ |
| compileall | 退出码 0（59 个 .py） | 全部可编译 | ✅ |

### 测试

| 指标 | 数值 |
| --- | --- |
| 用例总数 | **374** |
| 通过 | **374** |
| 失败 | **0** |
| 跳过 | **0** |
| 错误 | **0** |
| 警告 | 1（starlette TestClient 的 `BlockingPortal` 弃用告警，来自第三方库，非本项目代码） |
| 耗时 | 47.63s |
| **通过率** | **100%** |

### 覆盖率

| 指标 | 数值 |
| --- | --- |
| 语句覆盖 | **90.95%**（2474 语句，150 未覆盖） |
| 分支覆盖 | 422 分支，82 部分覆盖 |
| CI 门禁要求 | ≥ 80% |
| **达标情况** | ✅ **超出 10.95 个百分点** |

### QA 自建独立探针（本报告数据来源，未使用工程师测试结果）

| 探针 | 断言数 | 通过 | 失败 |
| --- | --- | --- | --- |
| `qa_probe/probe_runtime.py`（运行时红线：错误码/信封/泄露/幂等/租户） | 44 | 40 | 4 |
| `qa_probe/probe_contract.py`（8 端点逐字段契约） | 34 | 34 | 0 |

4 项失败逐条归因如下（**无一项被误判为代码缺陷**）：

| 探针用例 | 表面现象 | 归因判定 | 依据 |
| --- | --- | --- | --- |
| D-1'' 错误签名（oidc） | 伪造令牌返回 200 | **真实源码缺陷 → BUG-01** | 已用独立命令二次复现，根因定位到 `security.py:309` |
| D-3 文档不存在 | 返回 200 而非 11001 | **桩模式设计意图 → OBS-01** | `rag_service.py:313-319` `mock_mode` 有意跳过存在性校验；连带发现 11001 无业务调用点 |
| D-4' 指纹冲突 | 同 key 不同 body 返回 20007 | **真实源码缺陷 → BUG-05** | 已二次复现，根因 `idempotency.py:485` 指纹不含 body |
| D-7 业务语言扫描 | 13006 / 30004 含"异常" | **探针规则过宽 → 非缺陷** | 程序化复核与文档 3.0.3 **逐字一致**，见 D-3 节 |

即：4 项失败中**发现 2 个真实缺陷**（BUG-01、BUG-05）、1 项设计观察（OBS-01）、
1 项探针误报（已修正规则）。其中 BUG-01 位于工程师 374 个用例**未能覆盖**的盲区
（`grep -rn 'auth_mode="oidc"' tests/` 命中数为 0）。

---

## 六、遗留风险

| 编号 | 风险 | 影响 | 缓解建议 |
| --- | --- | --- | --- |
| R-01 | **鉴权/租户配置注入缺陷潜伏**。BUG-01/02 不在生产环境变量路径上暴露，但代码中"注入配置不生效"的模式已在 3 处出现（`security.py:309`、`security.py:332`、`rag.py:144`），后续新增依赖若沿用同一写法会持续累积 | 高 | Sprint 1 优先修复，并在 code review 中将"依赖函数必须显式接收 settings"列为检查项 |
| R-02 | **oidc 模式零测试覆盖**。374 个用例全部运行在 `auth_mode="stub"` 下，真实验签路径（audience/issuer/exp 校验）从未被自动化验证 | 高 | 补充 oidc 模式集成测试：有效令牌、过期令牌、错误签名、错误 audience、错误 issuer 五个场景 |
| R-03 | **桩模式掩盖真实下游行为**。`downstream_enabled=false` 下 12 个模块合计约 9% 语句未覆盖，且集中在协议转换核心逻辑。接入真实 Dify/Keycloak 时，ErrorMapper、超时重试、SSE 解析等路径可能出现未预期行为 | 中 | Sprint 1 引入 Dify mock 服务做契约测试（架构文档 6.1 已规划 `tests/integration/` 对 Dify mock） |
| R-04 | **`OA_JWT_SECRET` 等关键变量缺启动期校验**。`config.py:64` 的 `jwt_secret` 默认为空字符串，未做非空校验。若生产遗漏该变量且 `auth_mode=stub`，将静默以无验签模式启动 | 中 | 增加 validator：`auth_mode="oidc"` 时 `jwt_secret` 必须非空且长度达标，否则启动即失败（对应错误码 30004） |
| R-05 | **依赖文档滞后**。3 个前端构建依赖未登记（均为 MIT，无协议风险），说明架构文档 8 章与实际依赖之间缺少同步机制 | 低 | CI `dependency-scan.yml` 已有"依赖版本约束落在架构文档 8 章清单内"门禁，建议扩展为"包名也在清单内" |
| R-06 | **D-06 待决项未闭环**。Redis→Valkey 与 MinIO 版本线的最终选型仍标注"需主理人与法务/合规确认" | 中 | Sprint 1 前完成法务确认并更新 ADR；代码侧当前方案（Valkey 7.2-alpine + MinIO 独立容器锁 2021 版本线）已满足灰名单规则 |
| R-07 | **本报告的探针脚本未纳入 CI**。`qa_probe/` 为一次性验证产物，其中的安全断言（如 oidc 验签、泄露金丝雀）具备长期回归价值 | 低 | 建议将 `probe_runtime.py` 中的鉴权与泄露用例改写为 `tests/security/` 正式用例并纳入 CI 门禁 |

---

## 七、给工程师的修复清单（精确到文件行号）

| 优先级 | 缺陷 | 文件:行号 | 修复动作 |
| --- | --- | --- | --- |
| **P1** | BUG-01 | `apps/adapter/app/core/security.py:309` | `AuthContext.resolve(extract_bearer_token(request))` → 显式传入 settings（依赖 `request.app.state` 或 `get_settings()`） |
| **P1** | BUG-02 | `apps/adapter/app/core/security.py:332` | `resolve_tenant_id(request, auth)` → 同上显式传入 settings |
| P2 | BUG-03 | `apps/adapter/app/main.py:105-115` | 在请求依赖链接入 `get_rate_limiter()`，按租户+用户维度限流并抛 20006 |
| P2 | BUG-04 | `apps/adapter/app/services/rag_service.py:284-288`<br>`apps/adapter/app/api/v1/rag.py:141-146` | 区分单文件超限（20008/413）与解析后内容超限（11003/400）；`rag.py:144` 改用注入配置 |
| P2 | BUG-05 | `apps/adapter/app/core/idempotency.py:471-485` | `build_fingerprint` 追加请求体规范化哈希 |
| P2 | OBS-01 | `apps/adapter/app/core/errors.py:46` | 补齐 11001 调用点，或文档标注 T01 桩模式不可达 |
| P2 | OBS-02 | `apps/adapter/app/services/rag_service.py:70,79`<br>`apps/adapter/app/core/security.py:42` | 修正 docstring 中的 ULID 示例以匹配实现（或改用真 ULID） |

**修复后需回归验证的项**：BUG-01/02 必须补充对应自动化用例，
确保 374 个用例不再"全绿但漏掉安全缺陷"。

---

## 八、QA 自检声明

1. **未采信工程师自述结论**：ruff / mypy / pytest / 覆盖率 / 端点枚举全部由 QA 独立执行，
   命令与输出均如实粘贴。
2. **未复用工程师测试结果**：本报告 C、D 两项结论来自 QA 自建
   `qa_probe/probe_runtime.py`（44 断言）与 `qa_probe/probe_contract.py`（34 断言）。
3. **区分源码 Bug 与测试 Bug**：探针 4 项失败中，1 项经程序化复核判定为**我的探针规则过宽**
   （文案与文档逐字一致），已修正判定并在正文中说明依据，未误报为代码缺陷；
   另 1 项为桩模式设计意图（已转为文档观察项 OBS-01），未夸大为缺陷。
   真实成立的源码缺陷为 2 项（BUG-01、BUG-05），均已二次独立复现。
4. **无伪造数据**：本报告所有数字均来自真实命令输出；未执行项（Docker / npm）已在
   「阻塞项」章节明确标注并给出人工验证方式。
5. **样本量诚实**：契约比对覆盖 8 个端点 / 5 个接口域 / 34 项断言；
   错误码覆盖 38/38 全量核对，非抽样。

---

*报告结束。建议 Sprint 0 有条件放行，BUG-01 / BUG-02 在 Sprint 1 启动前修复。*