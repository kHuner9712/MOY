"""租户哨兵常量与平台模式守卫（架构文档 3.5 / D-02 第 5 条）。

RLS 判定表达式（`deploy/init-schema-phase2.sql` 第 15 节）：

```sql
tenant_id = current_setting('app.tenant_id', TRUE)
OR current_setting('app.tenant_id', TRUE) = '__platform__'
```

`__platform__` 是**字符串哨兵**而非数据库角色降权：一旦代码层守卫被绕过，
RLS 的双保险即失效。因此本模块把「谁能进平台模式」收敛为唯一入口
`assert_tenant_allowed(..., allow_platform=True)`，仅运营后台路由
（通过 `ops:console:access` 校验后）允许传入。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 平台租户哨兵（平台级目录表行：套餐 / 权限点 / 系统配置）
PLATFORM_TENANT: Final[str] = "__platform__"

#: 私有化模式的固定租户 ID（单租户天然隔离）
PRIVATE_TENANT: Final[str] = "default"


def is_platform_tenant(tenant_id: str) -> bool:
    """判断是否为平台租户哨兵。

    Args:
        tenant_id: 租户 ID。

    Returns:
        bool: 等于 `__platform__` 时返回 True。
    """
    return tenant_id.strip() == PLATFORM_TENANT


def assert_tenant_allowed(tenant_id: str, *, allow_platform: bool = False) -> str:
    """校验租户 ID 可用于开启事务级租户会话变量。

    规则（架构文档 3.5「平台模式守卫」）：

    - 租户 ID 不得为空；
    - 非 `__platform__` 的正常租户一律放行；
    - 传入 `__platform__` 时，调用方必须显式声明 `allow_platform=True`，
      否则抛 `30003`（内部错误：越权进入平台模式属编码缺陷，不是用户错误）。

    Args:
        tenant_id: 租户 ID。
        allow_platform: 是否允许进入平台模式（仅运营后台路由可置 True）。

    Returns:
        str: 规范化后的租户 ID。

    Raises:
        ApiError: 租户 ID 为空（20001）或越权进入平台模式（30003）。
    """
    normalized = (tenant_id or "").strip()
    if not normalized:
        raise ApiError(ErrorCode.PARAM_INVALID, internal_detail="empty tenant id")
    if is_platform_tenant(normalized) and not allow_platform:
        logger.error("platform_scope_denied", tenant_id=normalized)
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            internal_detail="platform tenant requires allow_platform=True",
        )
    return normalized


def resolve_runtime_tenant(settings: Settings) -> str:
    """解析运行期默认租户 ID。

    私有化模式恒为 `default`（架构文档 3.5「私有化模式」行）；
    SaaS 模式回落到 `settings.default_tenant_id`。

    Args:
        settings: 配置实例。

    Returns:
        str: 当前生效的默认租户 ID。
    """
    if settings.is_private_mode:
        return PRIVATE_TENANT
    return settings.default_tenant_id or PRIVATE_TENANT


__all__ = [
    "PLATFORM_TENANT",
    "PRIVATE_TENANT",
    "assert_tenant_allowed",
    "is_platform_tenant",
    "resolve_runtime_tenant",
]
