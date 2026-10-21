"""模型服务协议转换（架构文档 3.1 / 3.6 类图 `ModelService`）。

转换职责（架构文档 3.1.6）：
| 适配层 | 底座原生 | 转换内容 |
| --- | --- | --- |
| content | answer | 字段重命名 |
| usage.*_units | metadata.usage.*_token | token → units 业务化重命名 |
| citations | retriever_resources | 结构映射 |
| profile | 应用路由 | 适配层路由表选定底座应用 |
| SSE start/delta/citation/done/error | message/message_file/message_end/error | 事件重写 |

T01 阶段说明：`Settings.downstream_enabled=false` 时返回业务化 mock 结果，
不产生任何真实底座请求；转换函数本身已按最终形态实现，T02 直接启用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Final

from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.registry.loader import ProfileRoute, ProfileRouteTable, get_profile_routes
from app.schemas.common import sse_frame
from app.schemas.model import (
    CatalogData,
    Citation,
    FinishReason,
    GenerateData,
    GenerateRequest,
    ModelProfile,
    ProfileItem,
    ProfileStatus,
    Quota,
    StreamDeltaData,
    StreamDoneData,
    StreamStartData,
    Usage,
    UsageData,
)

logger = get_logger(__name__)

#: 底座原生路径（仅本文件与 `clients/dify_client.py` 可出现）
_DIFY_CHAT_PATH: Final[str] = "/v1/chat-messages"

#: ULID 风格 ID 前缀（架构文档 9.1 资源 ID 规范）
_GENERATION_PREFIX: Final[str] = "gen_"


def new_generation_id() -> str:
    """生成生成记录 ID。

    Returns:
        str: 形如 `gen_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_GENERATION_PREFIX)


class ModelService:
    """模型服务。

    Attributes:
        _settings: 配置实例。
        _routes: 能力档位路由表。
        _dify: 底座客户端。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        routes: ProfileRouteTable | None = None,
        dify: DifyClient | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            routes: 可选档位路由表，缺省从注册表加载。
            dify: 可选底座客户端，缺省取全局单例。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()
        self._dify = dify or get_dify_client()

    # ------------------------------------------------------------------
    # 能力档位解析
    # ------------------------------------------------------------------
    def resolve_route(self, profile: ModelProfile) -> ProfileRoute:
        """解析能力档位对应的路由配置。

        Args:
            profile: 能力档位。

        Returns:
            ProfileRoute: 档位路由配置。

        Raises:
            ApiError: 档位未注册或不可用（10004）。
        """
        route = self._routes.get(profile.value)
        if route is None:
            raise ApiError(
                ErrorCode.MODEL_PROFILE_UNAVAILABLE,
                internal_detail=f"profile not registered: {profile.value}",
            )
        if route.status != "available":
            raise ApiError(
                ErrorCode.MODEL_PROFILE_UNAVAILABLE,
                internal_detail=f"profile unavailable: {profile.value}",
            )
        return route

    def resolve_app_key(self, profile: ModelProfile) -> str:
        """按档位路由表解析底座应用密钥。

        Args:
            profile: 能力档位。

        Returns:
            str: 应用密钥；未配置时返回空串（桩模式）。

        Raises:
            ApiError: 档位不可用（10004）。
        """
        route = self.resolve_route(profile)
        return str(route.resolve_app_key())

    # ------------------------------------------------------------------
    # M1 非流式生成
    # ------------------------------------------------------------------
    async def generate(self, request: GenerateRequest, user_id: str = "") -> GenerateData:
        """执行非流式生成。

        Args:
            request: 生成请求。
            user_id: 终端用户标识（底座 user 字段必填，见 T02 联调修复）。

        Returns:
            GenerateData: 自研生成结果（`content` / `usage.*_units` / `citations`）。

        Raises:
            ApiError: 档位不可用（10004）、上下文超窗（10002）、生成失败（10001）。
        """
        self.resolve_route(request.profile)
        logger.info(
            "model_generate_requested",
            conversation_id=request.conversation_id,
            profile=request.profile.value,
            message_count=len(request.messages),
            knowledge_base_count=len(request.knowledge_base_ids),
        )

        if self._settings.mock_mode:
            return self._stub_generate(request)

        response = await self._dify.post(
            _DIFY_CHAT_PATH, self._build_downstream_payload(request, user_id)
        )
        return self._transform_generate_response(response.payload)

    def _build_downstream_payload(
        self, request: GenerateRequest, user_id: str = ""
    ) -> dict[str, Any]:
        """构造底座原生请求体。

        注意：此处出现底座原生字段名是**允许且必需**的——
        本方法是形态防火墙的内侧，输出绝不外泄（架构文档 3.1.6 关键点）。

        Args:
            request: 自研生成请求。

        Returns:
            dict[str, Any]: 底座原生请求体。
        """
        last_user_message = ""
        for message in reversed(request.messages):
            if message.role.value == "user":
                last_user_message = message.content
                break

        [
            {"role": message.role.value, "content": message.content}
            for message in request.messages[:-1]
        ]
        return {
            "inputs": {},
            "query": last_user_message,
            "response_mode": "blocking",
            "conversation_id": request.conversation_id,
            # 底座 user 为必填终端用户标识（Dify >= 0.6 强制，T02 实联调修复）
            "user": user_id,
            "files": [],
            "context": {
                "history_window": request.context.history_window,
                "memory_hints": request.context.memory_hints,
            },
        }

    def _transform_generate_response(self, payload: dict[str, Any]) -> GenerateData:
        """将底座原生响应转换为自研生成结果。

        Args:
            payload: 底座原生响应体。

        Returns:
            GenerateData: 自研生成结果。

        Raises:
            ApiError: 底座返回错误事件（按事件语义映射为 10001/10002/10003）。
        """
        event = str(payload.get("event", ""))
        if event == "error":
            raise self._map_downstream_event_error(payload)

        usage_raw = payload.get("metadata", {})
        usage_native = usage_raw.get("usage", {}) if isinstance(usage_raw, dict) else {}
        usage = Usage(
            prompt_units=int(usage_native.get("prompt_tokens", 0)),
            completion_units=int(usage_native.get("completion_tokens", 0)),
            total_units=int(usage_native.get("total_tokens", 0)),
        )

        resources = payload.get("retriever_resources", [])
        citations: list[Citation] = []
        if isinstance(resources, list):
            for item in resources:
                if not isinstance(item, dict):
                    continue
                citations.append(
                    Citation(
                        document_id=str(item.get("document_id", "")),
                        document_name=str(item.get("document_name", "")),
                        segment_content=str(item.get("content", "")),
                        score=float(item.get("score", 0.0)),
                    )
                )

        return GenerateData(
            generation_id=new_generation_id(),
            content=str(payload.get("answer", "")),
            finish_reason=self._map_finish_reason(event),
            usage=usage,
            citations=citations,
        )

    @staticmethod
    def _map_finish_reason(event: str) -> FinishReason:
        """将底座事件映射为自研结束原因。

        底座原生 `stop` 语义统一收敛为自研 `stop`；
        仅在显式截断信号下映射为 `length`。

        Args:
            event: 底座原生事件名。

        Returns:
            FinishReason: 自研结束原因枚举。
        """
        return FinishReason.LENGTH if event == "message_truncated" else FinishReason.STOP

    @staticmethod
    def _map_downstream_event_error(payload: dict[str, Any]) -> ApiError:
        """将底座 error 事件映射为自研业务异常。

        Args:
            payload: 底座原生 error 事件载荷。

        Returns:
            ApiError: 自研业务异常（10001 / 10002 / 10003 / 10004）。
        """
        code_native = str(payload.get("code", ""))
        message_native = str(payload.get("message", "")).lower()
        if "context" in message_native or "token" in message_native:
            return ApiError(ErrorCode.MODEL_CONTEXT_OVERFLOW, internal_detail=code_native)
        if "sensitive" in message_native or "risk" in message_native:
            return ApiError(ErrorCode.MODEL_CONTENT_BLOCKED, internal_detail=code_native)
        if "quota" in message_native or "limit" in message_native:
            return ApiError(ErrorCode.MODEL_QUOTA_EXHAUSTED, internal_detail=code_native)
        return ApiError(ErrorCode.MODEL_GENERATE_FAILED, internal_detail=code_native)

    def _stub_generate(self, request: GenerateRequest) -> GenerateData:
        """构造桩模式生成结果（T01）。

        Args:
            request: 生成请求。

        Returns:
            GenerateData: 业务化 mock 结果。
        """
        prompt_units = sum(len(message.content) for message in request.messages)
        completion_units = 24
        logger.debug("model_generate_stub", profile=request.profile.value)
        return GenerateData(
            generation_id=new_generation_id(),
            content="（演示环境）已收到你的问题，智能助手将在正式接入后返回回答。",
            finish_reason=FinishReason.STOP,
            usage=Usage(
                prompt_units=prompt_units,
                completion_units=completion_units,
                total_units=prompt_units + completion_units,
            ),
            citations=[],
        )

    # ------------------------------------------------------------------
    # M2 流式生成
    # ------------------------------------------------------------------
    def ensure_profile_available(self, profile: ModelProfile) -> None:
        """校验能力档位可用性（SSE 端点必须在建立响应前调用）。

        SSE 响应一旦开始写出便无法再返回统一错误响应体，
        因此校验必须在返回 `StreamingResponse` 之前完成（架构文档 3.0.1）。

        Args:
            profile: 能力档位。

        Raises:
            ApiError: 档位不可用（10004）。
        """
        self.resolve_route(profile)

    async def generate_stream(
        self, request: GenerateRequest, user_id: str = ""
    ) -> AsyncIterator[str]:
        """执行流式生成，产出自研 SSE 帧序列。

        事件类型完全自研：`start` / `delta` / `citation` / `done` / `error`，
        绝不复用底座原生事件名（架构文档 3.1.5，附录 A-4/A-6）。

        Args:
            request: 生成请求（同 M1）。

        Yields:
            str: SSE 文本帧。

        Raises:
            ApiError: 档位不可用（10004）；流式建立失败时以 `error` 帧下发而非抛出。
        """
        try:
            self.resolve_route(request.profile)
        except ApiError as exc:
            yield sse_frame("error", {"code": int(exc.code), "message": exc.message})
            return

        generation_id = new_generation_id()
        logger.info(
            "model_stream_started",
            conversation_id=request.conversation_id,
            profile=request.profile.value,
        )
        yield sse_frame("start", StreamStartData(generation_id=generation_id).model_dump())

        if self._settings.mock_mode:
            async for frame in self._stub_stream(generation_id, request):
                yield frame
            return

        try:
            streaming_payload = self._build_downstream_payload(request, user_id) | {
                "response_mode": "streaming"
            }
            async for event in self._dify.post_stream(_DIFY_CHAT_PATH, streaming_payload):
                mapped_frame = self._transform_stream_event(event)
                if mapped_frame is not None:
                    yield mapped_frame
        except ApiError as exc:
            logger.warning("model_stream_failed", detail=exc.log_message)
            yield sse_frame("error", {"code": int(exc.code), "message": exc.message})

    def _transform_stream_event(self, event: dict[str, Any]) -> str | None:
        """将底座原生流式事件转换为自研 SSE 帧。

        映射关系（架构文档 3.1.6）：
        `message` → `delta`；`message_file` → `citation`；
        `message_end` → `done`；`error` → `error`（重写 code 与 message）。

        Args:
            event: 底座原生事件字典。

        Returns:
            str | None: 自研 SSE 帧；无需下发的事件返回 `None`。
        """
        native_event = str(event.get("event", ""))

        if native_event in {"message", "agent_message"}:
            return sse_frame(
                "delta", StreamDeltaData(text=str(event.get("answer", ""))).model_dump()
            )

        if native_event == "message_file":
            return sse_frame(
                "citation",
                {
                    "document_name": str(event.get("filename", "")),
                    "segment_content": str(event.get("content", "")),
                },
            )

        if native_event == "message_end":
            usage_native = event.get("metadata", {})
            usage_raw = usage_native.get("usage", {}) if isinstance(usage_native, dict) else {}
            usage = Usage(
                prompt_units=int(usage_raw.get("prompt_tokens", 0)),
                completion_units=int(usage_raw.get("completion_tokens", 0)),
                total_units=int(usage_raw.get("total_tokens", 0)),
            )
            done = StreamDoneData(finish_reason=FinishReason.STOP, usage=usage)
            return sse_frame("done", done.model_dump())

        if native_event == "error":
            mapped = self._map_downstream_event_error(event)
            return sse_frame("error", {"code": int(mapped.code), "message": mapped.message})

        return None

    async def _stub_stream(
        self, generation_id: str, request: GenerateRequest
    ) -> AsyncIterator[str]:
        """构造桩模式流式序列（T01）。

        Args:
            generation_id: 生成记录 ID。
            request: 生成请求。

        Yields:
            str: SSE 文本帧序列。
        """
        chunks = ("（演示环境）", "已收到你的问题，", "智能助手将在正式接入后返回回答。")
        for chunk in chunks:
            yield sse_frame("delta", StreamDeltaData(text=chunk).model_dump())

        prompt_units = sum(len(message.content) for message in request.messages)
        done = StreamDoneData(
            finish_reason=FinishReason.STOP,
            usage=Usage(
                prompt_units=prompt_units,
                completion_units=sum(len(chunk) for chunk in chunks),
                total_units=prompt_units + sum(len(chunk) for chunk in chunks),
            ),
        )
        logger.info("model_stream_completed", generation_id=generation_id)
        yield sse_frame("done", done.model_dump())

    # ------------------------------------------------------------------
    # M3 能力档位目录
    # ------------------------------------------------------------------
    def get_catalog(self) -> CatalogData:
        """查询可用能力档位目录（纯配置驱动，不调用底座）。

        Returns:
            CatalogData: 档位列表；真实模型名不外泄（附录 A-1）。

        Raises:
            ApiError: 档位配置异常（30004）。
        """
        items: list[ProfileItem] = []
        for route in self._routes.routes:
            items.append(
                ProfileItem(
                    profile=ModelProfile(route.profile),
                    display_name=route.display_name,
                    description=route.description,
                    max_context_turns=route.max_context_turns,
                    status=(
                        ProfileStatus.AVAILABLE
                        if route.status == "available"
                        else ProfileStatus.UNAVAILABLE
                    ),
                )
            )
        return CatalogData(profiles=items)

    # ------------------------------------------------------------------
    # M4 用量统计
    # ------------------------------------------------------------------
    def get_usage(self, auth: AuthContext, period: str = "month") -> UsageData:
        """查询用量统计（业务化口径：次数/额度，附录 A-7）。

        T01 为桩实现：真实计量在 S2 由业务能力层提供，
        此处只做协议转换与字段占位，不做额度计算（适配层边界红线）。

        Args:
            auth: 鉴权上下文。
            period: 统计周期，`month` / `day`。

        Returns:
            UsageData: 业务化用量数据。

        Raises:
            ApiError: 周期参数非法（20001）。
        """
        if period not in {"day", "month"}:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail=f"unsupported usage period: {period}"
            )
        logger.info(
            "model_usage_queried", period=period, tenant_id=auth.tenant_id, user_id=auth.user_id
        )
        return UsageData(
            period=period,
            conversation_count=0,
            document_qa_count=0,
            quota=Quota(plan_name="", total_units=0, used_units=0, remaining_units=0),
        )


_service: ModelService | None = None


def get_model_service() -> ModelService:
    """获取模型服务单例。

    Returns:
        ModelService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = ModelService()
    return _service


def reset_model_service() -> None:
    """重置模型服务单例，供测试隔离用例。"""
    global _service
    _service = None
