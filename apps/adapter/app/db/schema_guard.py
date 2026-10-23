"""列级能力探测（阶段四 T4-04.2，架构 §7.4）。

**为什么需要它**：`usage_event` 的 4 个计量维度列（`profile` / `provider` /
`prompt_tokens` / `completion_tokens`）是迁移 `0007` 加的。N-1 兼容要求"旧库 +
新应用"能起来——此时列不存在，写这 4 列会直接抛 `UndefinedColumn`，而计量失败
**不得让对话失败**（D-12）。

所以四列的唯一读取口径是这里：探测一次、按连接串缓存；列不存在时**降级为不写**
（事件照记，只是维度为空），而不是 500、也不是静默写 NULL 到不存在的列。

**缓存口径**：按 `database_url + 库内标识` 缓存，避免每个事件都查
`information_schema`（那是每轮对话一次的开销）。测试用 `reset_schema_guard()` 隔离。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 计量维度四列（与 `0007` 加性变更逐字一致）
USAGE_DIMENSION_COLUMNS: Final[tuple[str, ...]] = (
    "profile",
    "provider",
    "prompt_tokens",
    "completion_tokens",
)

#: 缓存：连接串 → 是否具备四列
_capability_cache: dict[str, bool] = {}


def reset_schema_guard() -> None:
    """清空探测缓存（测试隔离与配置热切换用）。"""
    _capability_cache.clear()


async def usage_dimension_supported(settings: Settings | None = None) -> bool:
    """`usage_event` 是否已具备计量维度四列。

    内存模式恒为 True（进程内事件对象天然有字段）；真实库探测失败（连接异常等）
    一律按**不支持**处理——宁可少写维度，不可让计量写入炸掉。

    Args:
        settings: 可选配置实例。

    Returns:
        bool: 四列齐全返回 True。
    """
    conf = settings or get_settings()
    if not conf.uses_postgres:
        return True
    key = conf.database_url.strip()
    cached = _capability_cache.get(key)
    if cached is not None:
        return cached

    supported = False
    try:
        from app.db.session import tenant_scope

        async with tenant_scope(
            "__platform__", allow_platform=True, read_only=True, settings=conf
        ) as uow:
            found = await uow.fetch_scalar(
                """
                SELECT count(*) FROM information_schema.columns
                 WHERE table_name = 'usage_event'
                   AND column_name = ANY(:columns)
                """,
                {"columns": list(USAGE_DIMENSION_COLUMNS)},
            )
        supported = int(found or 0) == len(USAGE_DIMENSION_COLUMNS)
    except Exception as exc:  # 探测失败 → 降级（不抛、不阻断）
        logger.warning("usage_dimension_probe_failed", detail=type(exc).__name__)
        supported = False

    if not supported:
        logger.warning(
            "usage_dimension_columns_missing",
            note="usage_event 缺少计量维度列：维度字段不落库（事件与额度照常记录）",
            columns=list(USAGE_DIMENSION_COLUMNS),
        )
    _capability_cache[key] = supported
    return supported


__all__: list[str] = [
    "USAGE_DIMENSION_COLUMNS",
    "reset_schema_guard",
    "usage_dimension_supported",
]
