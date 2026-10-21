"""底座（Dify）HTTP 客户端 —— 全仓库唯一底座调用出口。

职责（架构文档 6.2）：
- 集中管理连接池、超时、重试、SSE 解析；
- 把底座原生错误形态（HTTP 状态码 + 报文）翻译为自研错误码
  （经 `ErrorMapper`），实现「形态防火墙」；
- 转发 `X-Trace-Id`，实现端到端链路透传。

T01 阶段说明：
本客户端已按最终形态实现调用代码（httpx + 超时 + 重试 + SSE），
但由 `Settings.downstream_enabled` 控制短路：T01 默认关闭，
调用方在桩模式下拿到的是本模块提供的 mock 载荷，**不产生任何真实底座请求**。

底座原生字段名（`answer` / `metadata.usage` / `retriever_resources` 等）
只允许出现在本文件与 `services/` 内，**禁止**出现在任何对外响应中。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

import httpx
import structlog.contextvars

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode, ErrorMapper
from app.core.logging import get_logger
from app.core.security import TRACE_HEADER

logger = get_logger(__name__)

#: 底座默认应用密钥对应的环境变量名（T02 由路由表按档位覆盖）
DEFAULT_APP_KEY_ENV: Final[str] = "OA_DIFY_DEFAULT_APP_KEY"


@dataclass(frozen=True, slots=True)
class DifyResponse:
    """底座响应包装。

    Attributes:
        status_code: 底座 HTTP 状态码（仅内部使用，不外泄）。
        payload: 底座响应体（仅内部使用，需经协议转换后才可外发）。
    """

    status_code: int
    payload: dict[str, Any]


class DifyClient:
    """底座 HTTP 客户端。

    Attributes:
        base_url: 底座地址（仅本类可见）。
        api_key: 底座应用密钥（仅本类可见，严禁出现在日志与响应中）。
        _client: httpx 异步客户端（连接池）。
        _settings: 配置实例。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: 底座地址。
            api_key: 底座应用密钥。
            settings: 可选配置实例。
            client: 可选的外部 httpx 客户端（测试注入用）。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self._build_timeout(),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def _build_timeout(self) -> httpx.Timeout:
        """构造 httpx 超时配置。

        Returns:
            httpx.Timeout: 连接 3s / 读取 60s（架构文档 3.0.4）。
        """
        return httpx.Timeout(
            timeout=self._settings.dify_read_timeout_seconds,
            connect=self._settings.dify_connect_timeout_seconds,
        )

    def _headers(self, *, stream: bool = False, api_key: str | None = None) -> dict[str, str]:
        """构造请求头（含底座鉴权与链路透传）。

        阶段二 D-10：支持按知识库覆盖密钥（per-kb 凭据）。传 `None` 时沿用
        客户端自身的全局密钥——既有调用方**无需改动**，行为完全不变。

        Args:
            stream: 是否为流式请求。
            api_key: 库级密钥（`None` 表示使用全局密钥）。

        Returns:
            dict[str, str]: 请求头。
        """
        effective_key = api_key if api_key is not None else self.api_key
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
            TRACE_HEADER: structlog.contextvars.get_contextvars().get("trace_id", "-"),
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    async def aclose(self) -> None:
        """关闭底层连接池（仅在本类自建客户端时生效）。"""
        if self._owns_client:
            await self._client.aclose()

    @asynccontextmanager
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> AsyncIterator[httpx.Response]:
        """发起一次底座请求，统一处理超时、重试与错误映射。

        重试策略（架构文档 3.0.4）：仅对幂等 GET 与下游 5xx/连接错误重试，
        最多 2 次，指数退避 200ms → 800ms，抖动 ±20%；写操作与流式不重试。

        Args:
            method: HTTP 方法。
            path: 底座路径（以 `/` 开头）。
            json_body: JSON 请求体。
            params: 查询参数。
            stream: 是否使用流式响应。
            idempotent: 是否为幂等请求（决定是否允许重试）。
            api_key: 库级密钥（D-10；`None` 表示使用全局密钥）。

        Yields:
            httpx.Response: 原始响应对象。

        Raises:
            ApiError: 超时（30002）、连接失败（30001）、下游业务错误（经 ErrorMapper 映射）。
        """
        attempts = self._settings.dify_max_retries + 1 if idempotent else 1
        last_error: ApiError | None = None

        for attempt in range(attempts):
            try:
                request = self._client.build_request(
                    method,
                    path,
                    json=json_body,
                    params=params,
                    headers=self._headers(stream=stream, api_key=api_key),
                    timeout=self._client.timeout,
                )
                response = await self._client.send(request, stream=stream)
            except httpx.TimeoutException as exc:
                last_error = ApiError(
                    ErrorCode.DOWNSTREAM_TIMEOUT,
                    internal_detail=f"path={path} attempt={attempt + 1}",
                )
                logger.warning("downstream_timeout", path=path, attempt=attempt + 1)
                if attempt + 1 < attempts:
                    await self._sleep_backoff(attempt)
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = ApiError(
                    ErrorCode.DOWNSTREAM_UNAVAILABLE,
                    internal_detail=f"path={path} attempt={attempt + 1}",
                )
                logger.warning("downstream_connect_failed", path=path, attempt=attempt + 1)
                if attempt + 1 < attempts:
                    await self._sleep_backoff(attempt)
                    continue
                raise last_error from exc

            if response.status_code >= 500 and attempt + 1 < attempts:
                logger.warning(
                    "downstream_server_error",
                    path=path,
                    status_code=response.status_code,
                    attempt=attempt + 1,
                )
                await response.aclose()
                await self._sleep_backoff(attempt)
                continue

            if response.status_code >= 400:
                body = await self._safe_text(response)
                logger.warning(
                    "downstream_business_error",
                    path=path,
                    status_code=response.status_code,
                )
                raise ErrorMapper.to_api_error(
                    response.status_code, body, internal_detail=f"path={path}"
                )

            yield response
            return

        raise last_error or ApiError(ErrorCode.DOWNSTREAM_UNAVAILABLE)

    async def _sleep_backoff(self, attempt: int) -> None:
        """按指数退避 + 抖动休眠。

        Args:
            attempt: 当前重试轮次（从 0 开始）。
        """
        import asyncio
        import random

        base = self._settings.dify_retry_backoff_ms * (4**attempt)
        jitter = base * 0.2 * (random.random() * 2 - 1)  # noqa: S311 - 抖动非安全用途
        await asyncio.sleep(max(base + jitter, 0) / 1000)

    async def _safe_text(self, response: httpx.Response) -> str:
        """安全读取响应文本（异常时返回空串，绝不抛出）。

        Args:
            response: httpx 响应对象。

        Returns:
            str: 响应文本，读取失败时为空串。
        """
        try:
            return (await response.aread() and response.text) or ""
        except httpx.HTTPError:  # pragma: no cover - 防御性分支
            return ""

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """发起 POST 请求（不重试）。

        Args:
            path: 底座路径。
            payload: JSON 请求体。
            idempotent: 是否允许重试（默认写操作不重试）。
            api_key: 库级密钥（D-10；`None` 表示使用全局密钥）。

        Returns:
            DifyResponse: 底座响应包装。
        """
        async with self._request(
            "POST", path, json_body=payload, idempotent=idempotent, api_key=api_key
        ) as response:
            return DifyResponse(response.status_code, await self._json(response))

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
    ) -> DifyResponse:
        """发起 GET 请求（幂等，失败自动重试）。

        Args:
            path: 底座路径。
            params: 查询参数。
            api_key: 库级密钥（D-10；`None` 表示使用全局密钥）。

        Returns:
            DifyResponse: 底座响应包装。
        """
        async with self._request(
            "GET", path, params=params, idempotent=True, api_key=api_key
        ) as response:
            return DifyResponse(response.status_code, await self._json(response))

    async def delete(self, path: str, *, api_key: str | None = None) -> DifyResponse:
        """发起 DELETE 请求（不重试）。

        Args:
            path: 底座路径。
            api_key: 库级密钥（D-10；`None` 表示使用全局密钥）。

        Returns:
            DifyResponse: 底座响应包装。
        """
        async with self._request("DELETE", path, api_key=api_key) as response:
            return DifyResponse(response.status_code, await self._json(response))

    async def post_stream(
        self, path: str, payload: dict[str, Any], *, api_key: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """发起流式 POST 请求，逐事件产出底座原生事件字典。

        流式请求**不重试**（架构文档 3.0.4），避免重复生成。

        Args:
            path: 底座路径。
            payload: JSON 请求体。
            api_key: 库级密钥（D-10；`None` 表示使用全局密钥）。

        Yields:
            dict[str, Any]: 底座原生事件字典（仅在 `services/` 内消费）。

        Raises:
            ApiError: 连接失败（30001）；底座 error 事件由调用方翻译为自研 `error` 事件。
        """
        try:
            request = self._client.build_request(
                "POST",
                path,
                json=payload,
                headers=self._headers(stream=True, api_key=api_key),
                timeout=httpx.Timeout(
                    timeout=None,
                    connect=self._settings.dify_connect_timeout_seconds,
                    read=self._settings.dify_stream_first_byte_timeout_seconds,
                ),
            )
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise ApiError(
                ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail=f"stream path={path}"
            ) from exc

        if response.status_code >= 400:
            body = await self._safe_text(response)
            await response.aclose()
            raise ErrorMapper.to_api_error(
                response.status_code, body, internal_detail=f"path={path}"
            )

        async for event in self._iter_sse(response):
            yield event
        await response.aclose()

    async def _iter_sse(self, response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
        """解析 SSE 字节流为事件字典。

        Args:
            response: 流式响应对象。

        Yields:
            dict[str, Any]: 解析后的事件字典。
        """
        event_name: str = ""
        data_lines: list[str] = []

        async for raw_line in response.aiter_lines():
            line = raw_line.rstrip("\r")
            if not line:
                if data_lines:
                    yield self._build_event(event_name, data_lines)
                event_name = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())

        if data_lines:
            yield self._build_event(event_name, data_lines)

    @staticmethod
    def _build_event(event_name: str, data_lines: list[str]) -> dict[str, Any]:
        """将 SSE 帧组装为事件字典。

        Args:
            event_name: 事件名。
            data_lines: `data:` 行内容列表。

        Returns:
            dict[str, Any]: 含 `event` 与解析后 `data` 的事件字典。
        """
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"text": raw}
        if not isinstance(parsed, dict):
            parsed = {"data": parsed}
        # 事件名来源优先级：JSON 自身 event 字段 > SSE event: 行。
        # Dify 的 chat-messages 流只在 data JSON 内携带 event 字段（无 event: 行），
        # 早期实现无条件用（可能为空的）event_name 覆盖，导致所有事件被置空、
        # 转换器全部丢弃（INT-05，2026-10-21 升级验证时定位）。
        if event_name:
            parsed.setdefault("event", event_name)
        parsed.setdefault("event", "")
        return parsed

    async def _json(self, response: httpx.Response) -> dict[str, Any]:
        """读取并解析 JSON 响应体。

        Args:
            response: httpx 响应对象。

        Returns:
            dict[str, Any]: 解析结果；非对象或解析失败时返回空字典。
        """
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {"data": data}


_client: DifyClient | None = None


def get_dify_client() -> DifyClient:
    """获取底座客户端单例。

    Returns:
        DifyClient: 进程内唯一实例。
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = DifyClient(
            base_url=settings.dify_base_url,
            api_key=settings.dify_default_app_key,
            settings=settings,
        )
    return _client


def reset_dify_client() -> None:
    """重置底座客户端单例，供测试隔离用例。"""
    global _client
    _client = None
