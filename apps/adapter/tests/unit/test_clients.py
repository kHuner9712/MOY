"""下游客户端单元测试（架构文档 3.0.4 超时/重试/降级）。

使用 httpx 内置 `MockTransport` 模拟底座响应，验证：
- 超时映射为 30002、连接失败映射为 30001；
- 下游 4xx/5xx 经 `ErrorMapper` 重写为自研错误码，且不透出原始报文；
- 幂等 GET 失败自动重试，写操作不重试；
- SSE 事件被解析为事件字典。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.clients.dify_client import DifyClient
from app.clients.keycloak_client import KeycloakClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode

pytestmark = pytest.mark.unit


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: Settings | None = None,
    base_url: str = "http://base.internal",
) -> tuple[DifyClient, httpx.AsyncClient]:
    """构造注入 MockTransport 的底座客户端。

    Args:
        handler: 请求处理器。
        settings: 可选配置实例。
        base_url: 底座地址。

    Returns:
        tuple[DifyClient, httpx.AsyncClient]: 客户端与其底层 httpx 客户端。
    """
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport, base_url=base_url)
    client = DifyClient(
        base_url=base_url,
        api_key="app-test-key",
        settings=settings
        or Settings(downstream_enabled=True, dify_max_retries=2, dify_retry_backoff_ms=0),
        client=async_client,
    )
    return client, async_client


async def test_post_returns_native_payload() -> None:
    """POST 成功返回底座原生载荷。"""
    client, raw = _client_with_handler(lambda request: httpx.Response(200, json={"answer": "内容"}))
    response = await client.post("/v1/chat-messages", {"query": "x"})
    assert response.status_code == 200
    assert response.payload == {"answer": "内容"}
    await raw.aclose()


async def test_request_carries_auth_and_trace_headers() -> None:
    """请求头必须携带底座鉴权与 trace_id 透传。"""
    import structlog.contextvars

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={})

    structlog.contextvars.bind_contextvars(trace_id="trace-abc")
    client, raw = _client_with_handler(handler)
    await client.post("/v1/chat-messages", {})
    await raw.aclose()
    assert captured["authorization"] == "Bearer app-test-key"
    assert captured["x-trace-id"] == "trace-abc"


async def test_post_maps_business_error_to_self_code() -> None:
    """底座业务错误被重写为自研错误码，且不透出原始报文。"""
    client, raw = _client_with_handler(
        lambda request: httpx.Response(400, text="quota exceeded for user")
    )
    with pytest.raises(ApiError) as exc_info:
        await client.post("/v1/chat-messages", {})
    assert exc_info.value.code == ErrorCode.MODEL_QUOTA_EXHAUSTED
    assert "quota exceeded" not in exc_info.value.to_response().message
    await raw.aclose()


async def test_get_retries_on_server_error() -> None:
    """幂等 GET 遇 5xx 自动重试并最终成功。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"id": "ds_1"})

    client, raw = _client_with_handler(handler)
    response = await client.get("/v1/datasets/ds_1")
    assert response.payload == {"id": "ds_1"}
    assert len(attempts) == 3
    await raw.aclose()


async def test_get_gives_up_after_max_retries() -> None:
    """重试用尽后返回 30001。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, text="unavailable")

    client, raw = _client_with_handler(handler)
    with pytest.raises(ApiError) as exc_info:
        await client.get("/v1/datasets/ds_1")
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    assert len(attempts) == 3
    await raw.aclose()


async def test_post_does_not_retry() -> None:
    """写操作不重试（避免重复生成）。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, text="boom")

    client, raw = _client_with_handler(handler)
    with pytest.raises(ApiError) as exc_info:
        await client.post("/v1/chat-messages", {})
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    assert len(attempts) == 1
    await raw.aclose()


async def test_timeout_maps_to_30002() -> None:
    """下游超时映射为 30002。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    settings = Settings(downstream_enabled=True, dify_max_retries=0)
    client, raw = _client_with_handler(handler, settings=settings)
    with pytest.raises(ApiError) as exc_info:
        await client.get("/v1/datasets")
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_TIMEOUT
    await raw.aclose()


async def test_connect_error_maps_to_30001() -> None:
    """连接失败映射为 30001。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = Settings(downstream_enabled=True, dify_max_retries=0)
    client, raw = _client_with_handler(handler, settings=settings)
    with pytest.raises(ApiError) as exc_info:
        await client.get("/v1/datasets")
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    await raw.aclose()


async def test_delete_returns_payload() -> None:
    """DELETE 成功返回空载荷。"""
    client, raw = _client_with_handler(lambda request: httpx.Response(200, json={"result": "ok"}))
    response = await client.delete("/v1/datasets/ds_1/documents/doc_1")
    assert response.payload == {"result": "ok"}
    await raw.aclose()


async def test_non_json_response_yields_empty_payload() -> None:
    """非 JSON 响应安全降级为空载荷，不抛异常。"""
    client, raw = _client_with_handler(lambda request: httpx.Response(200, text="not json"))
    response = await client.get("/v1/datasets")
    assert response.payload == {}
    await raw.aclose()


async def test_post_stream_parses_sse_frames() -> None:
    """SSE 字节流被解析为事件字典序列。"""
    body = (
        'event: message\ndata: {"answer":"片段"}\n\n'
        'event: message_end\ndata: {"metadata":{"usage":{"total_tokens":9}}}\n\n'
    ).encode()
    client, raw = _client_with_handler(
        lambda request: httpx.Response(
            200, content=body, headers={"Content-Type": "text/event-stream"}
        )
    )
    events = [event async for event in client.post_stream("/v1/chat-messages", {})]
    assert [event["event"] for event in events] == ["message", "message_end"]
    assert events[0]["answer"] == "片段"
    assert events[1]["metadata"]["usage"]["total_tokens"] == 9
    await raw.aclose()


async def test_post_stream_handles_malformed_data() -> None:
    """SSE data 非 JSON 时降级为文本载荷。"""
    body = "event: message\ndata: 非结构化文本\n\n".encode()
    client, raw = _client_with_handler(
        lambda request: httpx.Response(
            200, content=body, headers={"Content-Type": "text/event-stream"}
        )
    )
    events = [event async for event in client.post_stream("/v1/chat-messages", {})]
    assert events[0]["text"] == "非结构化文本"
    await raw.aclose()


async def test_post_stream_maps_error_status() -> None:
    """SSE 建立失败时映射为自研错误码。"""
    client, raw = _client_with_handler(lambda request: httpx.Response(503, text="unavailable"))
    with pytest.raises(ApiError) as exc_info:
        [event async for event in client.post_stream("/v1/chat-messages", {})]
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    await raw.aclose()


async def test_post_stream_connect_error_maps_to_30001() -> None:
    """SSE 连接失败映射为 30001。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, raw = _client_with_handler(handler)
    with pytest.raises(ApiError) as exc_info:
        [event async for event in client.post_stream("/v1/chat-messages", {})]
    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    await raw.aclose()


class TestKeycloakClient:
    """身份底座客户端。"""

    @staticmethod
    def _client(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> tuple[KeycloakClient, httpx.AsyncClient]:
        """构造注入 MockTransport 的身份底座客户端。

        Args:
            handler: 请求处理器。

        Returns:
            tuple[KeycloakClient, httpx.AsyncClient]: 客户端与其底层 httpx 客户端。
        """
        transport = httpx.MockTransport(handler)
        async_client = httpx.AsyncClient(transport=transport, base_url="http://idp.internal")
        client = KeycloakClient(
            base_url="http://idp.internal",
            realm="officeai",
            settings=Settings(dify_max_retries=0),
            client=async_client,
        )
        return client, async_client

    async def test_introspect_returns_active_flag(self) -> None:
        """令牌自省返回 active 标识。"""
        client, raw = self._client(
            lambda request: httpx.Response(200, json={"active": True, "exp": 999})
        )
        payload = await client.introspect("token-value")
        assert payload["active"] is True
        await raw.aclose()

    async def test_get_user_returns_native_payload(self) -> None:
        """用户查询返回身份底座原生对象（供服务层转换）。"""
        client, raw = self._client(
            lambda request: httpx.Response(200, json={"id": "usr_1", "username": "u"})
        )
        payload = await client.get_user("usr_1")
        assert payload["username"] == "u"
        await raw.aclose()

    async def test_get_user_missing_maps_to_14001(self) -> None:
        """用户不存在映射为 14001。"""
        client, raw = self._client(lambda request: httpx.Response(404, text="not found"))
        with pytest.raises(ApiError) as exc_info:
            await client.get_user("usr_missing")
        assert exc_info.value.code == ErrorCode.USER_NOT_FOUND
        await raw.aclose()

    async def test_list_group_members_returns_list(self) -> None:
        """组织成员查询返回列表。"""
        client, raw = self._client(
            lambda request: httpx.Response(200, json={"members": [{"id": "usr_1"}]})
        )
        members = await client.list_group_members("org_1")
        assert members == [{"id": "usr_1"}]
        await raw.aclose()

    async def test_list_group_members_handles_missing_key(self) -> None:
        """响应缺少 members 字段时返回空列表。"""
        client, raw = self._client(lambda request: httpx.Response(200, json={}))
        assert await client.list_group_members("org_1") == []
        await raw.aclose()

    async def test_create_role_returns_payload(self) -> None:
        """角色创建返回结果。"""
        client, raw = self._client(lambda request: httpx.Response(201, json={"id": "role_1"}))
        payload = await client.create_role("dev", "研发")
        assert payload["id"] == "role_1"
        await raw.aclose()

    async def test_timeout_maps_to_30002(self) -> None:
        """身份底座超时映射为 30002。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        client, raw = self._client(handler)
        with pytest.raises(ApiError) as exc_info:
            await client.introspect("token")
        assert exc_info.value.code == ErrorCode.DOWNSTREAM_TIMEOUT
        await raw.aclose()

    async def test_connect_error_maps_to_30001(self) -> None:
        """身份底座连接失败映射为 30001。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client, raw = self._client(handler)
        with pytest.raises(ApiError) as exc_info:
            await client.introspect("token")
        assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
        await raw.aclose()

    async def test_non_json_response_yields_empty_payload(self) -> None:
        """非 JSON 响应安全降级。"""
        client, raw = self._client(lambda request: httpx.Response(200, text="plain"))
        assert await client.introspect("token") == {}
        await raw.aclose()

    async def test_business_error_is_mapped(self) -> None:
        """身份底座业务错误经映射后不透出原始报文。"""
        client, raw = self._client(
            lambda request: httpx.Response(403, text="forbidden by policy engine")
        )
        with pytest.raises(ApiError) as exc_info:
            await client.get_user("usr_1")
        assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
        assert "policy engine" not in exc_info.value.to_response().message
        await raw.aclose()
