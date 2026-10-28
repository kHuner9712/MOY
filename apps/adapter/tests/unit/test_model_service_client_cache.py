"""H-1（审计 2026-10-28）：Dify 客户端生命周期单测。

覆盖两处修复：
1. `ModelService._dify_for` 按 app_key 缓存客户端（同一 key 只构造一次，
   `aclose()` 后可重建）；
2. `DifyClient.post_stream` 在 GeneratorExit（消费方断开）时仍关闭 response。

修前基准：`_dify_for` 每次新建实例；`post_stream` 无 try/finally，中断泄漏连接。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.schemas.model import ModelProfile
from app.services.model_service import ModelService

pytestmark = pytest.mark.unit


class _StubDifyClient:
    """桩客户端：记录 `for_app_key` 构造次数，供缓存断言。"""

    def __init__(self, api_key: str, settings: Any = None) -> None:
        self.api_key = api_key
        self.closed = False

    async def aclose(self) -> None:
        """关闭桩客户端（仅打标记）。"""
        self.closed = True


class _StubResponse:
    """桩 SSE 响应：消费 aclose 即打标记。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.aclose_calls = 0

    @property
    def status_code(self) -> int:
        return 200

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _StubHttpClient:
    """桩 httpx 客户端：`send(stream=True)` 返回预置桩响应。"""

    def __init__(self, response: _StubResponse) -> None:
        self._response = response

    def build_request(self, *args: Any, **kwargs: Any) -> object:
        return object()

    async def send(self, request: object, stream: bool = False) -> _StubResponse:
        return self._response


def _service_with_stub_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """把 `DifyClient.for_app_key` 替换为桩工厂，返回构造顺序记录表。

    Args:
        monkeypatch: pytest 桩工具。

    Returns:
        list[str]: 每次真实构造（未命中缓存）时收到的 app_key。
    """
    constructed: list[str] = []

    def _fake_for_app_key(api_key: str, settings: Any = None) -> _StubDifyClient:
        constructed.append(api_key)
        return _StubDifyClient(api_key)

    monkeypatch.setattr(DifyClient, "for_app_key", staticmethod(_fake_for_app_key))
    return constructed


def _no_injection_service() -> ModelService:
    """构造未注入客户端的 ModelService（走 `_dify_for` 缓存路径）。"""
    return ModelService(settings=Settings(downstream_enabled=False))


def test_same_app_key_reuses_cached_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 app_key 两次 `_dify_for` 返回同一实例，且只构造一次。"""
    constructed = _service_with_stub_factory(monkeypatch)
    service = _no_injection_service()
    monkeypatch.setattr(service, "resolve_app_key", lambda _p: "key-a")

    first = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001
    second = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001

    assert first is second
    assert constructed == ["key-a"]


def test_different_app_keys_get_different_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """不同 app_key 各自缓存、互不串档。"""
    _service_with_stub_factory(monkeypatch)
    service = _no_injection_service()
    keys = {ModelProfile.GENERAL: "key-a", ModelProfile.CODE: "key-b"}
    monkeypatch.setattr(service, "resolve_app_key", lambda p: keys[p])

    client_a = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001
    client_b = service._dify_for(ModelProfile.CODE)  # noqa: SLF001
    client_a2 = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001

    assert client_a is not client_b
    assert client_a is client_a2


@pytest.mark.asyncio
async def test_aclose_then_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """`aclose()` 后缓存清空，再次 `_dify_for` 重建新实例。"""
    _service_with_stub_factory(monkeypatch)
    service = _no_injection_service()
    monkeypatch.setattr(service, "resolve_app_key", lambda _p: "key-a")

    client = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001
    await service.aclose()
    rebuilt = service._dify_for(ModelProfile.GENERAL)  # noqa: SLF001

    assert rebuilt is not client


def test_injected_client_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入路径不受缓存影响：沿用注入实例，桩工厂零构造。"""
    constructed = _service_with_stub_factory(monkeypatch)
    injected = _StubDifyClient("injected")
    service = ModelService(settings=Settings(downstream_enabled=False), dify=injected)  # type: ignore[arg-type]

    assert service._dify_for(ModelProfile.GENERAL) is injected  # noqa: SLF001
    assert constructed == []


@pytest.mark.asyncio
async def test_post_stream_closes_response_on_generator_exit() -> None:
    """消费 1 个事件后关闭生成器（GeneratorExit），response 仍被 aclose。"""
    response = _StubResponse(
        [
            'data: {"event": "message", "answer": "你"}',
            "",
            'data: {"event": "message", "answer": "好"}',
            "",
        ]
    )
    client = DifyClient(
        base_url="http://dify.test",
        api_key="test-key",
        settings=Settings(downstream_enabled=False),
        client=_StubHttpClient(response),  # type: ignore[arg-type]
    )

    gen = client.post_stream("/chat-messages", {"inputs": {}})
    events: list[dict[str, Any]] = []
    async for event in gen:
        events.append(event)
        break  # 消费方中途断开 → 触发生成器 GeneratorExit
    await gen.aclose()

    assert len(events) == 1
    assert events[0]["answer"] == "你"
    assert response.aclose_calls == 1


@pytest.mark.asyncio
async def test_post_stream_closes_response_on_normal_completion() -> None:
    """正常消费完毕，response 恰好被关闭一次。"""
    response = _StubResponse(['data: {"event": "message_end"}', ""])
    client = DifyClient(
        base_url="http://dify.test",
        api_key="test-key",
        settings=Settings(downstream_enabled=False),
        client=_StubHttpClient(response),  # type: ignore[arg-type]
    )

    events = [event async for event in client.post_stream("/chat-messages", {})]

    assert events and all("event" in e for e in events)
    assert response.aclose_calls == 1
