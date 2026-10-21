"""接口数据契约包（与架构文档第 3 章接口规范 v1.0 同步）。

命名规范（架构文档 9.1）：
- JSON 字段一律 snake_case；
- 枚举值一律小写下划线；
- 资源 ID 一律 `类型缩写_` + ULID。
"""

from app.schemas.common import (
    CODE_SUCCESS,
    MESSAGE_SUCCESS,
    AckData,
    ApiResponse,
    DeletedData,
    HealthData,
    PageMeta,
    PageQuery,
    PageResult,
    ReadyCheck,
    ReadyData,
    ResourceRef,
    build_page,
    sse_frame,
)

__all__ = [
    "CODE_SUCCESS",
    "MESSAGE_SUCCESS",
    "AckData",
    "ApiResponse",
    "DeletedData",
    "HealthData",
    "PageMeta",
    "PageQuery",
    "PageResult",
    "ReadyCheck",
    "ReadyData",
    "ResourceRef",
    "build_page",
    "sse_frame",
]
