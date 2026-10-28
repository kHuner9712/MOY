# 自研统一适配层（apps/adapter）

> Moy（麦子）· 统一适配层
> **全仓库唯一允许接触底座（Dify）与身份底座（Keycloak）的应用。**

---

## 一、职责与红线

### 1.1 做什么

| 职责 | 说明 |
| --- | --- |
| 协议转换 | 底座原生请求/响应 → 自研标准契约 |
| 参数校验 | Pydantic v2 强校验，失败统一收敛为 20001 |
| 鉴权转发 | 校验 Bearer 令牌与租户声明（**不登录、不签发令牌**） |
| 错误统一封装 | 下游错误按注册制映射为自研错误码 + 业务语言文案 |
| 超时/重试/幂等 | 按架构文档 3.0.4 总策略执行 |

### 1.2 不做什么（违反即打回）

- ❌ 会话归属判断、权限策略、套餐额度计算
- ❌ 提示词拼装、结果后处理与格式化
- ❌ 任何业务编排逻辑
- ❌ 向上层泄露底座形态（URL、参数、事件名、错误码）
- ❌ 在 `app/` 与 `app/core/` 之外出现底座客户端依赖或底座 URL

## 二、目录结构

```
apps/adapter/
├── app/
│   ├── main.py                  # FastAPI 入口：中间件、异常处理器、路由挂载
│   ├── core/
│   │   ├── config.py            # 环境变量与配置模型（Pydantic Settings，OA_ 前缀）
│   │   ├── errors.py            # 错误码注册表与 ApiError / ErrorMapper
│   │   ├── idempotency.py       # 幂等键管理（内存 / Redis 双后端）
│   │   ├── logging.py           # 结构化日志（JSON + trace_id + 脱敏）
│   │   ├── ratelimit.py         # 限流（内存 / Redis 双后端）
│   │   └── security.py          # 令牌解析、租户上下文、FastAPI 鉴权依赖
│   ├── middleware/
│   │   ├── trace.py             # trace_id 注入与透传、访问日志
│   │   └── error_handler.py     # 统一异常 → {code, message, data} 兜底
│   ├── schemas/                 # 请求响应契约（common/model/rag/agent/tool/identity）
│   ├── api/v1/                  # 30 个业务端点（router/model/rag/agent/tool/identity）
│   ├── services/                # 协议转换服务（唯一允许写下游调用的位置）
│   ├── clients/                 # dify_client / keycloak_client（唯一调用出口）
│   ├── registry/                # profile_routes.yaml / tool_registry.yaml（数据驱动）
│   └── tools/                   # 工具连接器（新增工具只加这里）
├── tests/{unit,integration}/    # 单元测试与契约测试
├── Dockerfile
└── pyproject.toml
```

## 三、接口清单（30 个业务端点 + 2 个免鉴权探针）

| 域 | 端点 | 方法 | 编号 |
| --- | --- | --- | --- |
| 模型服务 | `/api/v1/model/generate` | POST | M1 |
| | `/api/v1/model/generate/stream` | POST | M2 |
| | `/api/v1/model/catalog` | GET | M3 |
| | `/api/v1/model/usage` | GET | M4 |
| RAG 服务 | `/api/v1/knowledge/bases` | POST | R1 |
| | `/api/v1/knowledge/bases` | GET | R2 |
| | `/api/v1/knowledge/bases/{base_id}/documents` | POST | R3 |
| | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | GET | R4 |
| | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex` | POST | R5 |
| | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | DELETE | R6 |
| | `/api/v1/knowledge/search` | POST | R7 |
| Agent 服务 | `/api/v1/agent/tasks` | POST | A1 |
| | `/api/v1/agent/tasks` | GET | A2 |
| | `/api/v1/agent/tasks/{task_id}` | GET | A3 |
| | `/api/v1/agent/tasks/{task_id}/steps` | GET | A4 |
| | `/api/v1/agent/tasks/{task_id}/cancel` | POST | A5 |
| | `/api/v1/agent/tasks/{task_id}/events` | GET | A6 |
| 工具服务 | `/api/v1/tools/registry` | GET | T1 |
| | `/api/v1/tools/connections` | POST | T2 |
| | `/api/v1/tools/connections` | GET | T3 |
| | `/api/v1/tools/connections/{conn_id}` | DELETE | T4 |
| | `/api/v1/tools/execute` | POST | T5 |
| | `/api/v1/tools/sync-jobs` | POST | T6 |
| | `/api/v1/tools/sync-jobs/{job_id}` | GET | T7 |
| 用户权限 | `/api/v1/identity/verify` | POST | U1 |
| | `/api/v1/identity/profile` | GET | U2 |
| | `/api/v1/identity/organizations/{org_id}/members` | GET | U3 |
| | `/api/v1/identity/organizations/{org_id}/invitations` | POST | U4 |
| | `/api/v1/identity/organizations/{org_id}/roles` | POST | U5 |
| | `/api/v1/identity/audit-logs` | POST | U6 |
| 健康检查 | `/healthz` `/readyz` | GET | 免鉴权 |

在线接口文档：`/docs`（Swagger）、`/redoc`、`/openapi.json`。

## 四、错误码分段

| 分段 | 含义 | 示例 |
| --- | --- | --- |
| `0` | 成功 | `{"code":0,"message":"success"}` |
| `10001-14999` | 领域业务错误 | 10001 AI 暂时没有给出回答，请重试 |
| `20001-20999` | 通用业务错误 | 20001 请求内容不完整，请检查后重试 |
| `30001-30999` | 系统错误 | 30003 系统繁忙，请稍后再试 |

**注册制**：新增错误码必须先在 `docs/architecture-v1.0.md` 3.0.3 登记，
再在 `app/core/errors.py` 的 `ERROR_REGISTRY` 注册，最后同步前端
`apps/web/src/lib/business-errors.ts` 兜底映射。

## 五、本地开发

```bash
# 1) 创建虚拟环境
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2) 安装依赖
pip install -e ".[dev]"

# 3) 准备环境变量
cp ../../deploy/.env.example .env

# 4) 启动（默认桩模式，无需底座即可运行）
uvicorn app.main:app --reload --port 8000

# 5) 健康检查
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

### 5.1 开发态令牌

`OA_AUTH_MODE=stub` 时仅解析 JWT 载荷不验签，可用如下脚本生成测试令牌：

```python
import time
from jose import jwt

now = int(time.time())
print(jwt.encode(
    {
        "sub": "usr_dev_user",
        "tenant_id": "default",
        "roles": ["*"],
        "aud": "officeai-adapter",
        "iss": "officeai",
        "iat": now,
        "exp": now + 3600,
    },
    "",                       # OA_JWT_SECRET
    algorithm="HS256",
))
```

```bash
curl -H "Authorization: Bearer <token>" -H "X-Tenant-Id: default" \
     http://127.0.0.1:8000/api/v1/model/catalog
```

> 生产环境必须设置 `OA_AUTH_MODE=oidc` 并配置 `OA_JWT_SECRET`，严禁使用 stub 模式。

## 六、质量门禁

```bash
ruff check . && ruff format --check .   # Lint + 格式
mypy app                                # 类型检查
pytest tests -q --cov=app --cov-report=term-missing
```

覆盖率门禁：**≥ 85%**（CI 硬地板 `--cov-fail-under=85`，与 `ci-adapter.yml` 门禁 5 一致；
历史 80% 为阶段一口径已废止）。

> 覆盖率 / 用例数的**唯一权威来源 = CI junit.xml**（`ci-adapter.yml` 门禁 5 产物，
> 带 `--run-pg` 且 skipped=0）；本地跑测试产生的 `coverage.xml` / `junit.xml` /
> `pytest_run.log` 是临时工件，不入库、不作为对账口径（QA-03，审计 2026-10-28）。

## 七、扩展指引

### 7.1 新增工具（不改任何接口）

1. 在 `app/tools/` 新增继承 `ToolConnector` 的连接器，用 `@register_connector` 装饰；
2. 在 `app/registry/tool_registry.yaml` 追加一条 `tools` 条目；
3. 在 `app/services/tool_service.py` 的 `_CONNECTOR_MODULES` 登记模块路径；
4. 完成。**T1-T7 端点、请求响应结构、错误码零改动。**

### 7.2 新增能力档位（不改契约）

1. 在 `app/registry/profile_routes.yaml` 的 `profiles` 追加条目；
2. 若确需新枚举值，先更新架构文档 3.1.1 再改 `app/schemas/model.py` 的 `ModelProfile`。

### 7.3 新增错误码

见上文「错误码分段」的注册制流程。

## 八、T01 已知限制

1. **桩模式**：`OA_DOWNSTREAM_ENABLED=false`（默认），所有服务返回业务化 mock 结果，
   不产生任何真实底座请求。真实协议转换在 T02 落地（转换函数已按最终形态实现）。
2. **内存幂等/限流**：多副本部署必须切换 `OA_IDEMPOTENCY_BACKEND=redis`（后端已实现）。
3. **Agent 状态在内存**：T02 起迁 Redis，适配层保持无状态（架构文档 D-03）。
4. **工具凭据**：本期连接器不持久化凭据，D-02 待决后确定归属。
