"""公共数据契约：统一响应包装与分页模型。

统一响应格式（架构文档 3.0.1）：
```json
{"code": 0, "message": "success", "data": {}}
```
- `code=0` 表示成功，非 0 为业务/系统错误码；
- `message` 是**面向终端用户的业务语言文案**，前端直接展示；
- `data` 失败时为 `null`。

本模块只做数据结构定义，不含任何业务逻辑，也不依赖 `app.core`，避免循环引用。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

#: 成功码
CODE_SUCCESS: int = 0

#: 成功时的固定文案
MESSAGE_SUCCESS: str = "success"


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装。

    类型参数 `T` 为 `data` 字段的业务模型类型；无数据场景可使用 `ApiResponse[None]`。

    Attributes:
        code: 业务码，0 表示成功。
        message: 业务语言文案，成功时固定为 `success`。
        data: 业务数据，失败时为 `None`。
    """

    code: int = CODE_SUCCESS
    message: str = MESSAGE_SUCCESS
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = MESSAGE_SUCCESS) -> ApiResponse[T]:
        """构造成功响应。

        Args:
            data: 业务数据。
            message: 业务语言文案，默认 `success`。

        Returns:
            ApiResponse[T]: `code=0` 的成功响应。
        """
        return cls(code=CODE_SUCCESS, message=message, data=data)

    @classmethod
    def fail(cls, code: int, message: str, data: T | None = None) -> ApiResponse[T]:
        """构造失败响应。

        Args:
            code: 业务错误码（注册制，见架构文档 3.0.3）。
            message: 业务语言文案，**禁止**透传技术细节。
            data: 附加数据，默认 `None`。

        Returns:
            ApiResponse[T]: 非 0 code 的失败响应。
        """
        return cls(code=code, message=message, data=data)

    @property
    def is_success(self) -> bool:
        """是否成功响应。"""
        return self.code == CODE_SUCCESS


class PageMeta(BaseModel):
    """分页元信息。

    Attributes:
        page: 当前页码，从 1 开始。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    page: int = Field(default=1, ge=1, description="当前页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=200, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class PageResult(BaseModel, Generic[T]):
    """分页数据包装。

    Attributes:
        items: 当前页数据列表。
        meta: 分页元信息。
    """

    items: list[T] = Field(default_factory=list, description="当前页数据")
    meta: PageMeta = Field(default_factory=PageMeta, description="分页元信息")


class PageQuery(BaseModel):
    """分页查询入参基类。

    Attributes:
        page: 页码，从 1 开始。
        page_size: 每页条数，最大 200。
    """

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=200, description="每页条数")

    @property
    def offset(self) -> int:
        """SQL/存储层偏移量。"""
        return (self.page - 1) * self.page_size


class ResourceRef(BaseModel):
    """资源引用（权限校验场景）。

    Attributes:
        type: 资源类型，如 `knowledge_base`。
        id: 资源 ID。
    """

    type: str = Field(default="", description="资源类型")
    id: str = Field(default="", description="资源 ID")


class HealthData(BaseModel):
    """健康检查数据。

    Attributes:
        status: 服务状态。
        app: 应用名。
        version: 应用版本。
        env: 运行环境。
        deploy_mode: 部署模式。
        downstream_enabled: 是否已开启真实下游调用。
    """

    status: str = Field(default="ok", description="服务状态")
    app: str = Field(default="", description="应用名")
    version: str = Field(default="", description="应用版本")
    env: str = Field(default="", description="运行环境")
    deploy_mode: str = Field(default="", description="部署模式")
    downstream_enabled: bool = Field(default=False, description="是否已开启真实下游调用")


class ReadyCheck(BaseModel):
    """就绪探针单项检查结果。

    Attributes:
        name: 检查项名称。
        ready: 是否就绪。
        detail: 业务语言描述。
    """

    name: str = Field(description="检查项名称")
    ready: bool = Field(description="是否就绪")
    detail: str = Field(default="", description="业务语言描述")


class ReadyData(BaseModel):
    """就绪探针数据。

    Attributes:
        status: 整体状态，`ready` / `not_ready`。
        checks: 各检查项明细。
    """

    status: str = Field(default="ready", description="整体状态")
    checks: list[ReadyCheck] = Field(default_factory=list, description="检查项明细")


class DeletedData(BaseModel):
    """删除操作结果。

    Attributes:
        id: 被删除资源 ID。
        deleted: 是否删除成功。
    """

    id: str = Field(description="被删除的资源 ID")
    deleted: bool = Field(default=True, description="是否删除成功")


class AckData(BaseModel):
    """通用确认结果（取消、触发等无返回体操作）。

    Attributes:
        id: 关联资源 ID。
        status: 当前状态。
        updated_at: 更新时间（ISO 8601）。
    """

    id: str = Field(description="关联资源 ID")
    status: str = Field(default="accepted", description="当前状态")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")


def build_page(items: list[T], total: int, page: int, page_size: int) -> PageResult[T]:
    """构造分页结果并自动计算总页数。

    Args:
        items: 当前页数据。
        total: 总条数。
        page: 当前页码。
        page_size: 每页条数。

    Returns:
        PageResult[T]: 分页结果。
    """
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return PageResult[T](
        items=items,
        meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
    )


def sse_frame(event: str, data: Any) -> str:
    """构造 SSE 文本帧。

    自研事件名，绝不复用底座原生 event 名称（架构文档 3.1.5 / 附录 A-4、A-6）。

    Args:
        event: 自研事件名（`start` / `delta` / `citation` / `done` / `error` 等）。
        data: 事件载荷，将被序列化为单行 JSON。

    Returns:
        str: 符合 SSE 协议的一帧文本。
    """
    import json

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def sse_frame_with_id(event: str, data: Any, event_id: int) -> str:
    """构造带 `id:` 的 SSE 文本帧（断线续传的唯一依据）。

    `id` 是**回放游标**（`Event.cursor`），不是会话内序号：fork 会话的回放流里
    父流与本流都有 `seq=1`，只有游标全局单调。客户端把最后一次收到的 `id`
    通过 `Last-Event-ID` 请求头带回，服务端只补 `cursor > id` 的增量。

    字段顺序：`event:` → `id:` → `data:`。SSE 规范不要求同一帧内字段的先后，
    而现有客户端与用例都按"第一行是 `event:`"解析，因此 `id:` 排在中间——
    这是向后兼容的增量，不是新协议。

    Args:
        event: 自研事件名。
        data: 事件载荷，将被序列化为单行 JSON。
        event_id: 回放游标（`Event.cursor`）。

    Returns:
        str: 符合 SSE 协议的一帧文本。
    """
    import json

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\nid: {event_id}\ndata: {payload}\n\n"


#: SSE 响应媒体类型
SSE_MEDIA_TYPE: str = "text/event-stream"

#: SSE 响应头（禁用代理缓冲，保证逐字下发）
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
