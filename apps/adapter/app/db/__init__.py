"""业务库访问层（阶段二 D-02，架构文档 2.1 / 3.5）。

分层铁律（架构文档 2.1 结论第 2 条）：

- `app/api/v1 → app/services → app/business → app/db`；
- **本包只允许出现 SQL 与会话管理**，禁止业务编排、禁止协议转换；
- 租户隔离双保险：① 仓储 SQL 必带 `tenant_id = :tenant_id`；
  ② 表启用 `FORCE ROW LEVEL SECURITY`，会话变量由 `db/session.py` 在
  事务首条语句用 `SELECT set_config('app.tenant_id', :tenant_id, TRUE)` 设置。

模块职责：

| 模块 | 职责 |
| --- | --- |
| `engine.py` | 异步引擎工厂 + 池参数 + `dispose`；`memory` 模式返回 `None` |
| `session.py` | `UnitOfWork` / `tenant_scope` 事务边界与租户会话变量 |
| `migrate.py` | `--check` / `--apply` 迁移执行器（checksum 校验，不引入 Alembic） |
| `tenant.py` | `PLATFORM_TENANT` 常量与 `allow_platform` 守卫 |
| `repositories/` | 仓储层（`BaseRepository` 强制注入租户条件） |
"""

from __future__ import annotations

from app.db.engine import (
    check_engine,
    create_engine,
    dispose_engine,
    get_engine,
    normalize_database_url,
    reset_engine_for_test,
    validate_business_store,
)
from app.db.session import (
    SET_TENANT_SQL,
    TENANT_SETTING_NAME,
    UnitOfWork,
    get_session_factory,
    tenant_scope,
)
from app.db.tenant import PLATFORM_TENANT, assert_tenant_allowed, is_platform_tenant

__all__ = [
    "PLATFORM_TENANT",
    "SET_TENANT_SQL",
    "TENANT_SETTING_NAME",
    "UnitOfWork",
    "assert_tenant_allowed",
    "check_engine",
    "create_engine",
    "dispose_engine",
    "get_engine",
    "get_session_factory",
    "is_platform_tenant",
    "normalize_database_url",
    "reset_engine_for_test",
    "tenant_scope",
    "validate_business_store",
]
