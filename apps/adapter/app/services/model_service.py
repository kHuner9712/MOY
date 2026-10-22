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

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Final

from app.business.context_assembler import (
    AssembledContext,
    ContextAssembler,
    get_context_assembler,
)
from app.business.conversation_service import (
    ConversationService,
    StartedGeneration,
    get_conversation_service,
)
from app.business.policy_service import PolicyService, get_policy_service, normalize_base_id
from app.business.segment_service import SegmentHit, SegmentService, build_key
from app.business.usage_service import UsageService, get_usage_service
from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseStore,
    get_knowledge_base_store,
)
from app.db.tenant import resolve_runtime_tenant
from app.registry.loader import ProfileRoute, ProfileRouteTable, get_profile_routes
from app.schemas.common import sse_frame
from app.schemas.model import (
    CatalogData,
    ChatMessage,
    Citation,
    FinishReason,
    GenerateData,
    GenerateRequest,
    ModelProfile,
    ProfileItem,
    ProfileStatus,
    Quota,
    StreamCitationData,
    StreamDeltaData,
    StreamDoneData,
    StreamStartData,
    Usage,
    UsageData,
)
from app.schemas.rag import SegmentPosition

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


def _base_id_of_native(native_dataset_id: Any) -> str:
    """把底座数据集 ID 映射为自研知识库 ID。

    Args:
        native_dataset_id: 底座数据集 ID（可能为 None）。

    Returns:
        str: 自研知识库 ID；入参为空时返回空串。
    """
    text = str(native_dataset_id or "").strip()
    return f"kb_{text}" if text else ""


def _position_of_native(raw: Any) -> SegmentPosition | None:
    """把底座引用位置映射为自研位置对象。

    Args:
        raw: 底座位置（整型序号或 `{page, paragraph}` 映射）。

    Returns:
        SegmentPosition | None: 自研位置；无法解析时返回 None。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    page = raw.get("page")
    paragraph = raw.get("paragraph")
    if page is None and paragraph is None:
        return None
    return SegmentPosition(
        page=int(page) if page not in (None, "") else None,
        paragraph=int(paragraph) if paragraph not in (None, "") else None,
    )


def _delta_text(frame: str) -> str:
    """取一帧 `delta` 事件的正文。

    与 `_delta_text_of` 的区别：那个只给**长度**（中断折算用），这个给**内容**
    （落库用）。中断时要把已下发的内容写进 assistant 行，否则刷新后"话说到一半"
    会变成空白——用户看到的与历史里存的必须一致。

    Args:
        frame: 自研 SSE 文本帧。

    Returns:
        str: 正文；非 `delta` 帧返回空串。
    """
    if not frame.startswith("event: delta"):
        return ""
    _, _, raw = frame.partition("data: ")
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("text", ""))


def _delta_text_of(frame: str) -> int:
    """取一帧 `delta` 事件的正文长度（流式中断折算用）。

    Args:
        frame: 自研 SSE 文本帧。

    Returns:
        int: 正文字符数；非 `delta` 帧返回 0。
    """
    if not frame.startswith("event: delta"):
        return 0
    _, _, raw = frame.partition("data: ")
    try:
        payload = json.loads(raw.strip() or "{}")
    except ValueError:  # pragma: no cover - 帧由本模块序列化，解析失败属编码缺陷
        return 0
    if not isinstance(payload, dict):
        return 0
    return len(str(payload.get("text", "")))


def _total_units_of(event: dict[str, Any]) -> int:
    """取底座 `message_end` 事件的结算额度。

    Args:
        event: 底座原生 `message_end` 事件。

    Returns:
        int: `total_tokens`；缺失时退化为 `prompt + completion`，全缺返回 0。
    """
    metadata = event.get("metadata", {})
    usage = metadata.get("usage", {}) if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        return 0
    total = int(usage.get("total_tokens", 0) or 0)
    if total:
        return total
    return int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)


def _last_user_text(messages: list[ChatMessage]) -> str:
    """取最后一条用户消息的正文。

    Args:
        messages: 请求里的消息列表。

    Returns:
        str: 最后一条 `role=user` 的正文；没有则返回空串。
    """
    for message in reversed(messages):
        if message.role.value == "user":
            return message.content
    return ""


def _with_conversation(
    data: GenerateData, started: StartedGeneration, assembled: AssembledContext
) -> GenerateData:
    """给生成结果补上会话与来源字段（阶段四加性字段）。

    Args:
        data: 生成结果。
        started: 本轮两条消息句柄。
        assembled: 组装结果。

    Returns:
        GenerateData: 补全后的结果（`model_copy`，不改原对象）。
    """
    return data.model_copy(
        update={
            "conversation_id": started.conversation.conversation_id,
            "history_source": assembled.history_source,
            "ignored_client_history": assembled.ignored_client_history,
        }
    )


def _with_notice(data: GenerateData, notice: tuple[int, str]) -> GenerateData:
    """给生成结果补上非阻断额度提示（B27 `15006`）。

    为什么是加性字段而不是错误码：前端 `unwrap()` 把非 0 `code` 当异常抛，
    提示一旦走 `code` 就变成弹错误框 —— 与 PRD 5.3「软限制、不停服、不弹错」
    直接冲突。`notice` 为 `(0, "")` 时原样返回（不改动对象，零开销）。

    Args:
        data: 生成结果。
        notice: `(提示码, 提示文案)`；`(0, "")` 表示无提示。

    Returns:
        GenerateData: 补上提示字段的生成结果。
    """
    code, message = notice
    if not code:
        return data
    return data.model_copy(update={"notice_code": code, "notice_message": message})


@dataclass(slots=True)
class CitationContext:
    """流式引用回链的上下文（M2 专用）。

    Attributes:
        tenant_id: 租户 ID。
        base_ids: 本次请求**已授权**的知识库 ID 列表（未授权的已在生成前剔除）。
        base_names: `base_id → 知识库名称`。
        hits: 回填 `segment_map` 的命中集合（流结束前统一异步写入）。
        index: 片段序号计数器（底座未给序号时按到达顺序兜底）。
    """

    tenant_id: str
    base_ids: tuple[str, ...] = ()
    base_names: dict[str, str] = field(default_factory=dict)
    hits: list[SegmentHit] = field(default_factory=list)
    index: int = 0

    def resolve_base(self, native_dataset_id: str) -> str:
        """把底座数据集 ID 映射为自研知识库 ID。

        Args:
            native_dataset_id: 底座数据集 ID（可为空）。

        Returns:
            str: 自研知识库 ID；无法确定时返回空串。
        """
        if native_dataset_id:
            return f"kb_{native_dataset_id}"
        if len(self.base_ids) == 1:
            return self.base_ids[0]
        return ""


class ModelService:
    """模型服务。

    Attributes:
        _settings: 配置实例。
        _routes: 能力档位路由表。
        _dify: 底座客户端。
        _policy: 可见性判定服务（未授权库静默跳过）。
        _segments: 片段映射服务（引用回链旁路回填）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        routes: ProfileRouteTable | None = None,
        dify: DifyClient | None = None,
        policy: PolicyService | None = None,
        segments: SegmentService | None = None,
        bases: KnowledgeBaseStore | None = None,
        usage: UsageService | None = None,
        conversations: ConversationService | None = None,
        assembler: ContextAssembler | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            routes: 可选档位路由表，缺省从注册表加载。
            dify: 可选底座客户端，缺省取全局单例。
            policy: 可选可见性判定服务（测试注入）。
            segments: 可选片段映射服务（测试注入）。
            bases: 可选知识库元数据存储（引用回链的名称回填）。
            usage: 可选计量服务（测试注入；D-12 唯一写入口）。
            conversations: 可选会话服务（阶段四落库；测试注入）。
            assembler: 可选上下文组装器（阶段四自研组装；测试注入）。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()
        self._dify = dify or get_dify_client()
        self._policy: PolicyService = policy or get_policy_service(self._settings)
        self._segments: SegmentService = segments or SegmentService(self._settings)
        self._bases: KnowledgeBaseStore = bases or get_knowledge_base_store(self._settings)
        self._usage: UsageService = usage or get_usage_service(self._settings)
        # 阶段四：会话落库与自研上下文组装（历史真源从客户端改为 message 表）
        self._conversations: ConversationService = conversations or get_conversation_service(
            self._settings
        )
        self._assembler: ContextAssembler = assembler or get_context_assembler(self._settings)

    # ------------------------------------------------------------------
    # 租户与授权库解析（B05）
    # ------------------------------------------------------------------
    def _tenant_of(self, auth: AuthContext | None) -> str:
        """解析本次生成所属租户。

        Args:
            auth: 鉴权上下文（服务层单测可为 None）。

        Returns:
            str: 租户 ID。
        """
        if auth is not None and auth.tenant_id:
            return auth.tenant_id
        return resolve_runtime_tenant(self._settings)

    async def _authorized_base_ids(
        self, request: GenerateRequest, auth: AuthContext | None
    ) -> list[str]:
        """过滤出已授权的知识库（未授权库在对话中**静默跳过**）。

        `skipped` 统计口径与 R7 / 列表一致：**先按规范 ID 去重再比较**，
        避免「同一对象两种写法」被算成两次剔除（QA B05-P0-01 附带口径统一）。

        Args:
            request: 生成请求。
            auth: 鉴权上下文。

        Returns:
            list[str]: 允许参与本次生成的知识库 ID（规范形态）。
        """
        requested = {
            normalized
            for normalized in (normalize_base_id(item) for item in request.knowledge_base_ids)
            if normalized
        }
        authorized = await self._policy.filter_authorized(auth, request.knowledge_base_ids)
        skipped = len(requested) - len(authorized)
        if skipped:
            logger.info(
                "model_unauthorized_bases_skipped",
                requested=len(requested),
                allowed=len(authorized),
                skipped=skipped,
            )
        return authorized

    async def _base_names(self, tenant_id: str, base_ids: tuple[str, ...]) -> dict[str, str]:
        """批量取知识库展示名（未登记库不出现在结果中）。

        Args:
            tenant_id: 租户 ID。
            base_ids: 知识库 ID 列表。

        Returns:
            dict[str, str]: `base_id → 名称`。
        """
        names: dict[str, str] = {}
        for base_id in base_ids:
            record = await self._bases.get(tenant_id, base_id)
            if record is not None and record.name:
                names[base_id] = record.name
        return names

    async def _attach_citation_backlinks(
        self,
        data: GenerateData,
        base_ids: list[str],
        tenant_id: str,
        *,
        schedule: bool = True,
    ) -> GenerateData:
        """为引用补齐回链字段并回填片段映射（D-11）。

        Args:
            data: 生成结果。
            base_ids: 本次请求已授权的知识库 ID。
            tenant_id: 租户 ID。
            schedule: 是否异步写库（流式路径 True，避免阻塞）。

        Returns:
            GenerateData: 补齐回链字段后的生成结果。
        """
        fallback = base_ids[0] if len(base_ids) == 1 else ""
        names = await self._base_names(tenant_id, tuple(dict.fromkeys([*base_ids, fallback])))
        hits: list[SegmentHit] = []
        enriched: list[Citation] = []
        for index, citation in enumerate(data.citations):
            base_id = citation.base_id or fallback
            segment_key = (
                build_key(citation.document_id, index, citation.segment_content)
                if citation.document_id
                else ""
            )
            enriched.append(
                citation.model_copy(
                    update={
                        "base_id": base_id,
                        "base_name": names.get(base_id, ""),
                        "segment_id": segment_key,
                    }
                )
            )
            if segment_key and base_id:
                hits.append(
                    SegmentHit(
                        base_id=base_id,
                        document_id=citation.document_id,
                        segment_index=index,
                        content=citation.segment_content,
                        position_page=(
                            citation.position.page if citation.position is not None else None
                        ),
                        position_paragraph=(
                            citation.position.paragraph if citation.position is not None else None
                        ),
                    )
                )
        await self._segments.record_hits(tenant_id, hits, schedule=schedule)
        return data.model_copy(update={"citations": enriched})

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
    async def generate(
        self, request: GenerateRequest, user_id: str = "", auth: AuthContext | None = None
    ) -> GenerateData:
        """执行非流式生成。

        Args:
            request: 生成请求。
            user_id: 终端用户标识（底座 user 字段必填，见 T02 联调修复）。
            auth: 鉴权上下文（用于未授权库静默跳过与引用回链的租户解析）。

        Returns:
            GenerateData: 自研生成结果（`content` / `usage.*_units` / `citations`）。

        Raises:
            ApiError: 档位不可用（10004）、上下文超窗（10002）、生成失败（10001）。
        """
        self.resolve_route(request.profile)
        authorized = await self._authorized_base_ids(request, auth)
        logger.info(
            "model_generate_requested",
            conversation_id=request.conversation_id,
            profile=request.profile.value,
            message_count=len(request.messages),
            knowledge_base_count=len(authorized),
        )
        # 非阻断额度提示（B27 `15006`）：判据 = 与 M4/BL5 同源的 `over_quota_warning`。
        # 只挂在 `data.notice_*` 上，外层 `code` 恒为 0——超额**不拦停**（PRD 5.3）。
        notice = await self._quota_notice(auth)

        # ---- 阶段四落库与自研组装（架构 §5.1，顺序不可调换）---------------
        # 校验 → **落库 user 消息** → 组装 → 调底座 → 回写助手消息。
        # 用户消息必须先落库：反过来（先调上游）一旦上游超时或进程被杀，
        # 用户那句话就永久丢了，而他看到的是"消息发出去了"。
        tenant_id = self._tenant_of(auth)
        current_text = _last_user_text(request.messages)
        started = await self._conversations.start_generation(
            tenant_id, user_id, request.conversation_id, user_text=current_text
        )
        assembled = await self._assembler.build(
            tenant_id,
            user_id,
            started.conversation.conversation_id,
            current_user_text=current_text,
            client_messages=request.messages,
            current_message_id=started.user_message.message_id,
            history_window=request.context.history_window,
            profile=request.profile.value,
            assemble_from=request.context.assemble_from,
        )

        if self._settings.mock_mode:
            stub = self._stub_generate(request)
            await self._settle_generation(
                generation_id=stub.generation_id,
                units=stub.usage.total_units,
                has_authorized_bases=bool(authorized),
                auth=auth,
                source="settled",
            )
            await self._finalize_assistant(
                tenant_id, started, content=stub.content, status="settled"
            )
            return _with_notice(_with_conversation(stub, started, assembled), notice)

        try:
            response = await self._dify.post(
                _DIFY_CHAT_PATH,
                self._build_downstream_payload(
                    request, user_id, query_override=assembled.query_text
                ),
            )
        except Exception:
            # 上游失败：把助手行收口为 failed（**不给用户看码与堆栈**），再原样抛出
            await self._finalize_assistant(
                tenant_id, started, content="", status="failed", error_reason="回答生成失败，请重试"
            )
            raise
        # 非流式同样剥离推理标记（INT-06）
        payload = response.payload
        if isinstance(payload.get("answer"), str):
            flt = _ReasoningFilter()
            payload = {**payload, "answer": flt.feed(payload["answer"]) + flt.flush()}
        data = self._transform_generate_response(payload)
        settled = await self._attach_citation_backlinks(
            data, authorized, self._tenant_of(auth), schedule=False
        )
        # 结算时点：下游已返回 message_end（blocking 模式即响应体），
        # 用下游 usage.total_units 结算，source='settled'（D-12 第 3 条）
        await self._settle_generation(
            generation_id=settled.generation_id,
            units=settled.usage.total_units,
            has_authorized_bases=bool(authorized),
            auth=auth,
            source="settled",
        )
        await self._finalize_assistant(
            tenant_id, started, content=settled.content, status="settled"
        )
        return _with_notice(_with_conversation(settled, started, assembled), notice)

    async def _finalize_assistant(
        self,
        tenant_id: str,
        started: StartedGeneration,
        *,
        content: str,
        status: str,
        error_reason: str = "",
        truncated: bool = False,
    ) -> None:
        """写助手消息终态（**失败不影响已返回给用户的回答**）。

        落库失败只记 WARN：用户已经拿到答案，此时抛错等于把"能用的回答"变成
        "500 错误页"。偏差由对账脚本与 `message` 表的孤儿扫描兜住。

        Args:
            tenant_id: 企业 ID。
            started: 本轮两条消息句柄。
            content: 终态正文。
            status: `settled|aborted|failed`。
            error_reason: 业务化失败原因。
            truncated: 是否截断。
        """
        try:
            await self._conversations.finalize_assistant_message(
                tenant_id,
                started.assistant_message.message_id,
                content=content,
                status=status,
                error_reason=error_reason,
                truncated=truncated,
            )
        except Exception as exc:
            logger.warning(
                "assistant_message_finalize_failed",
                conversation_id=started.conversation.conversation_id,
                message_id=started.assistant_message.message_id,
                detail=type(exc).__name__,
            )

    async def _settle_generation(
        self,
        *,
        generation_id: str,
        units: int,
        has_authorized_bases: bool,
        auth: AuthContext | None,
        source: str,
    ) -> bool:
        """结算一次生成（D-12 唯一写入口的调用点）。

        结算失败**不得**影响用户已得到的回答：异常只记 WARN，
        由 `scripts/reconcile_usage.py` 在对账时发现偏差（架构文档 2.5 第 7 条）。

        Args:
            generation_id: 生成记录 ID（幂等键，重放不产生第二条事件）。
            units: 结算额度。
            has_authorized_bases: 本次是否使用知识库（决定 `chat` / `doc_qa`）。
            auth: 鉴权上下文。
            source: 结算来源（`settled` / `estimated`）。

        Returns:
            bool: 首次写入返回 True。
        """
        user_id = auth.user_id if auth is not None else ""
        if not user_id:
            return False
        try:
            return await self._usage.record_generation(
                tenant_id=self._tenant_of(auth),
                user_id=user_id,
                generation_id=generation_id,
                units=units,
                source=source,
                has_authorized_bases=has_authorized_bases,
            )
        except Exception as exc:  # 计量失败不得中断用户请求（对账脚本负责发现偏差）
            logger.warning(
                "usage_settlement_failed",
                generation_id=generation_id,
                source=source,
                detail=type(exc).__name__,
            )
            return False

    async def _quota_notice(self, auth: AuthContext | None) -> tuple[int, str]:
        """取本次请求的非阻断额度提示（B27 `15006`）。

        单一真源：委托 `UsageService.quota_notice()`，其判据就是 M4/BL5 的同一份
        `snapshot().over_quota_warning`——本类**不重算**额度公式（两套算法必然漂移）。

        Args:
            auth: 鉴权上下文（`None` 或缺失用户时无提示）。

        Returns:
            tuple[int, str]: `(提示码, 提示文案)`；无提示时为 `(0, "")`。
        """
        user_id = auth.user_id if auth is not None else ""
        if not user_id:
            return 0, ""
        return await self._usage.quota_notice(tenant_id=self._tenant_of(auth), user_id=user_id)

    def _build_downstream_payload(
        self, request: GenerateRequest, user_id: str = "", *, query_override: str = ""
    ) -> dict[str, Any]:
        """构造底座原生请求体。

        注意：此处出现底座原生字段名是**允许且必需**的——
        本方法是形态防火墙的内侧，输出绝不外泄（架构文档 3.1.6 关键点）。

        Args:
            request: 自研生成请求。
            user_id: 终端用户标识。
            query_override: 已组装好的 query（阶段四 server 模式）。

                给了它就用它：历史来自自研库（`ContextAssembler`），本方法退化为
                "只做字段搬运"，不再自己拼 transcript。**两条路径不许同时在跑**——
                否则同一句话既在组装段里、又被本方法拼一遍，模型会看到重复历史。

        Returns:
            dict[str, Any]: 底座原生请求体。
        """
        last_user_message = ""
        for message in reversed(request.messages):
            if message.role.value == "user":
                last_user_message = message.content
                break

        # 多轮历史转写为中立对话转录（Dify chat 接口仅接受单条 query；
        # 适配层无状态不拼装语义提示词，只做机械格式转换 —— D-03 会话由上层自持，
        # T02 实联调修复 INT-04a）
        history_lines: list[str] = []
        for message in request.messages[:-1]:
            role_label = "用户" if message.role.value == "user" else "助手"
            history_lines.append(f"{role_label}: {message.content}")
        query = last_user_message
        if history_lines:
            query = (
                "以下是此前的对话记录：\n"
                + "\n".join(history_lines)
                + "\n\n请基于以上对话回答最后这个问题："
                + last_user_message
            )
        if query_override:
            query = query_override

        return {
            "inputs": {},
            "query": query,
            "response_mode": "blocking",
            # 内部会话 ID 严禁透传：Dify 要求 null 或其签发的 UUID，
            # 透传 conv_xxx 会直接 400（T02 实联调修复 INT-04a）
            "conversation_id": None,
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
                        base_id=_base_id_of_native(item.get("dataset_id")),
                        position=_position_of_native(item.get("position")),
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
        self, request: GenerateRequest, user_id: str = "", auth: AuthContext | None = None
    ) -> AsyncIterator[str]:
        """执行流式生成，产出自研 SSE 帧序列。

        事件类型完全自研：`start` / `delta` / `citation` / `done` / `error`，
        绝不复用底座原生事件名（架构文档 3.1.5，附录 A-4/A-6）。

        Args:
            request: 生成请求（同 M1）。
            user_id: 终端用户标识。
            auth: 鉴权上下文（未授权库静默跳过 + 引用回链租户解析）。

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

        authorized = await self._authorized_base_ids(request, auth)
        tenant_id = self._tenant_of(auth)
        citation_ctx = CitationContext(
            tenant_id=tenant_id,
            base_ids=tuple(authorized),
            base_names=await self._base_names(tenant_id, tuple(authorized)),
        )
        generation_id = new_generation_id()
        # 非阻断额度提示（B27 `15006`）：**在流开始前算好**，只挂在终帧 `done` 上。
        # 为什么不中途插帧：流式期间插入非 delta 帧会打断前端渲染与自动滚动；
        # 为什么不改 `error` 帧：超额不是错误，PRD 5.3 要求"提醒而非拦停"。
        notice = await self._quota_notice(auth)
        # 阶段四：与 M1 同序——先落库，再组装，再开流。
        current_text = _last_user_text(request.messages)
        started = await self._conversations.start_generation(
            tenant_id, user_id, request.conversation_id, user_text=current_text
        )
        assembled = await self._assembler.build(
            tenant_id,
            user_id,
            started.conversation.conversation_id,
            current_user_text=current_text,
            client_messages=request.messages,
            current_message_id=started.user_message.message_id,
            history_window=request.context.history_window,
            profile=request.profile.value,
            assemble_from=request.context.assemble_from,
        )
        logger.info(
            "model_stream_started",
            conversation_id=started.conversation.conversation_id,
            profile=request.profile.value,
            knowledge_base_count=len(authorized),
            history_source=assembled.history_source,
        )
        yield sse_frame(
            "start",
            StreamStartData(
                generation_id=generation_id,
                conversation_id=started.conversation.conversation_id,
            ).model_dump(),
        )

        # 结算状态：`produced_chars` 累计已下发正文（中断折算依据），
        # `settled` 标记是否已按下游 usage 精确结算（D-12 第 3 条）
        produced_chars = 0
        #: 已下发的可见正文（中断时写进 assistant 行的 `content`——用户看到的
        #: 与历史里存的必须一致，否则刷新后"话说到一半"变成空白）
        produced_parts: list[str] = []
        settled = False
        downstream_total: int | None = None

        if self._settings.mock_mode:
            completed = False
            try:
                async for frame in self._stub_stream(generation_id, request, notice=notice):
                    text = _delta_text(frame)
                    produced_chars += len(text)
                    if text:
                        produced_parts.append(text)
                    yield frame
                await self._settle_generation(
                    generation_id=generation_id,
                    units=self._stub_units(request),
                    has_authorized_bases=bool(authorized),
                    auth=auth,
                    source="settled",
                )
                completed = True
            finally:
                # 客户端中途断开时 `async for` 不会跑完，这里必须自己收口，
                # 否则那一行永远停在 streaming（下次打开会看到"正在输入"）
                await self._finalize_assistant(
                    tenant_id,
                    started,
                    content="".join(produced_parts),
                    status="settled" if completed else "aborted",
                    error_reason="" if completed else "回答中断，可重新发送",
                    truncated=not completed,
                )
            return

        try:
            streaming_payload = self._build_downstream_payload(
                request, user_id, query_override=assembled.query_text
            ) | {"response_mode": "streaming"}
            reasoning_filter = _ReasoningFilter()
            async for event in self._dify.post_stream(_DIFY_CHAT_PATH, streaming_payload):
                native = str(event.get("event", ""))
                if native in {"message", "agent_message"}:
                    # 推理模型（DeepSeek V4 等）会把思考过程混在 answer 中下发，
                    # 由状态机剥离后再发 delta（INT-06，技术黑盒化红线）
                    visible = reasoning_filter.feed(str(event.get("answer", "")))
                    if not visible:
                        continue
                    produced_chars += len(visible)
                    produced_parts.append(visible)
                    event = {**event, "answer": visible}
                elif native == "message_end":
                    # 流结束前冲洗过滤器缓冲（短回答可能整体滞留在缓冲中）
                    rest = reasoning_filter.flush()
                    if rest:
                        produced_chars += len(rest)
                        produced_parts.append(rest)
                        yield sse_frame("delta", StreamDeltaData(text=rest).model_dump())
                    downstream_total = _total_units_of(event)
                mapped_frame = self._transform_stream_event(event, citation_ctx, notice=notice)
                if mapped_frame is not None:
                    yield mapped_frame
        except ApiError as exc:
            logger.warning("model_stream_failed", detail=exc.log_message)
            yield sse_frame("error", {"code": int(exc.code), "message": exc.message})
        finally:
            # 引用回链旁路回填：不阻塞流，写失败只记 WARN
            await self._segments.record_hits(tenant_id, citation_ctx.hits, schedule=True)
            if downstream_total is not None:
                await self._settle_generation(
                    generation_id=generation_id,
                    units=downstream_total,
                    has_authorized_bases=bool(authorized),
                    auth=auth,
                    source="settled",
                )
                settled = True
            if not settled:
                # 中断/超时/客户端断开：按已产出内容折算（source='estimated'）
                estimated = self._usage.estimate_interrupted_units(produced_chars=produced_chars)
                if estimated > 0:
                    await self._settle_generation(
                        generation_id=generation_id,
                        units=estimated,
                        has_authorized_bases=bool(authorized),
                        auth=auth,
                        source="estimated",
                    )
            # 落库终态：完整走完 → settled；中断/断开 → aborted + truncated=true，
            # **已收到的部分内容保留**（用户看到的与历史里存的必须一致）
            if settled:
                await self._finalize_assistant(
                    tenant_id, started, content="".join(produced_parts), status="settled"
                )
            else:
                await self._finalize_assistant(
                    tenant_id,
                    started,
                    content="".join(produced_parts),
                    status="aborted",
                    error_reason="回答中断，可重新发送",
                    truncated=True,
                )

    def _transform_stream_event(
        self,
        event: dict[str, Any],
        citation_ctx: CitationContext | None = None,
        *,
        notice: tuple[int, str] = (0, ""),
    ) -> str | None:
        """将底座原生流式事件转换为自研 SSE 帧。

        映射关系（架构文档 3.1.6）：
        `message` → `delta`；`message_file` → `citation`；
        `message_end` → `done`；`error` → `error`（重写 code 与 message）。

        Args:
            event: 底座原生事件字典。
            citation_ctx: 引用回链上下文（缺省时引用帧只带既有字段）。
            notice: 非阻断额度提示 `(码, 文案)`，只挂在 `done` 帧（B27 `15006`）。

        Returns:
            str | None: 自研 SSE 帧；无需下发的事件返回 `None`。
        """
        native_event = str(event.get("event", ""))

        if native_event in {"message", "agent_message"}:
            return sse_frame(
                "delta", StreamDeltaData(text=str(event.get("answer", ""))).model_dump()
            )

        if native_event == "message_file":
            return sse_frame("citation", self._build_citation_payload(event, citation_ctx))

        if native_event == "message_end":
            usage_native = event.get("metadata", {})
            usage_raw = usage_native.get("usage", {}) if isinstance(usage_native, dict) else {}
            usage = Usage(
                prompt_units=int(usage_raw.get("prompt_tokens", 0)),
                completion_units=int(usage_raw.get("completion_tokens", 0)),
                total_units=int(usage_raw.get("total_tokens", 0)),
            )
            done = StreamDoneData(
                finish_reason=FinishReason.STOP,
                usage=usage,
                notice_code=notice[0],
                notice_message=notice[1],
            )
            return sse_frame("done", done.model_dump())

        if native_event == "error":
            mapped = self._map_downstream_event_error(event)
            return sse_frame("error", {"code": int(mapped.code), "message": mapped.message})

        return None

    def _build_citation_payload(
        self, event: dict[str, Any], citation_ctx: CitationContext | None
    ) -> dict[str, Any]:
        """构造 `citation` 事件载荷（含回链字段，到达即可点击）。

        Args:
            event: 底座原生 `message_file` 事件。
            citation_ctx: 引用回链上下文。

        Returns:
            dict[str, Any]: 自研 `citation` 载荷。
        """
        document_name = str(event.get("filename", ""))
        content = str(event.get("content", ""))
        document_id = str(event.get("document_id", ""))
        payload = StreamCitationData(document_name=document_name, segment_content=content)
        if citation_ctx is None:
            return payload.model_dump()
        base_id = citation_ctx.resolve_base(str(event.get("dataset_id", "")))
        segment_key = build_key(document_id, citation_ctx.index, content) if document_id else ""
        position = _position_of_native(event.get("position"))
        enriched = StreamCitationData(
            document_name=document_name,
            segment_content=content,
            document_id=document_id,
            base_id=base_id,
            base_name=citation_ctx.base_names.get(base_id, ""),
            segment_id=segment_key,
            position=position,
        )
        if segment_key and base_id:
            citation_ctx.hits.append(
                SegmentHit(
                    base_id=base_id,
                    document_id=document_id,
                    segment_index=citation_ctx.index,
                    content=content,
                    position_page=position.page if position is not None else None,
                    position_paragraph=position.paragraph if position is not None else None,
                )
            )
        citation_ctx.index += 1
        return enriched.model_dump()

    #: 桩模式流式分片（T01）
    _STUB_CHUNKS: Final[tuple[str, ...]] = (
        "（演示环境）",
        "已收到你的问题，",
        "智能助手将在正式接入后返回回答。",
    )

    def _stub_units(self, request: GenerateRequest) -> int:
        """构造桩模式结算额度（与非流式桩口径一致）。

        Args:
            request: 生成请求。

        Returns:
            int: `输入字符数 + 桩输出字符数`。
        """
        prompt_units = sum(len(message.content) for message in request.messages)
        completion_units = sum(len(chunk) for chunk in self._STUB_CHUNKS)
        return prompt_units + completion_units

    async def _stub_stream(
        self,
        generation_id: str,
        request: GenerateRequest,
        *,
        notice: tuple[int, str] = (0, ""),
    ) -> AsyncIterator[str]:
        """构造桩模式流式序列（T01）。

        Args:
            generation_id: 生成记录 ID。
            request: 生成请求。
            notice: 非阻断额度提示 `(码, 文案)`，只挂在终帧 `done`（B27 `15006`）。

        Yields:
            str: SSE 文本帧序列。
        """
        chunks = self._STUB_CHUNKS
        for chunk in chunks:
            yield sse_frame("delta", StreamDeltaData(text=chunk).model_dump())

        prompt_units = sum(len(message.content) for message in request.messages)
        done = StreamDoneData(
            finish_reason=FinishReason.STOP,
            usage=Usage(
                prompt_units=prompt_units,
                completion_units=sum(len(chunk) for chunk in chunks),
                total_units=self._stub_units(request),
            ),
            notice_code=notice[0],
            notice_message=notice[1],
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
    async def get_usage(self, auth: AuthContext, period: str = "month") -> UsageData:
        """查询用量统计（业务化口径：次数/额度，附录 A-7）。

        **与 BL5 同源**（D-12 第 5 条）：额度数字来自 `UsageService.snapshot()`，
        与 `GET /billing/usage` 是同一函数、同一张聚合表、同一段计算，
        因此同刻 `used_units == quota_units_used`。

        Args:
            auth: 鉴权上下文。
            period: 统计周期，`month` / `day`。

        Returns:
            UsageData: 业务化用量数据（新增 `quota_unit`，加性字段）。

        Raises:
            ApiError: 周期参数非法（20001）。
        """
        if period not in {"day", "month"}:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail=f"unsupported usage period: {period}"
            )
        snapshot = await self._usage.snapshot(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        logger.info(
            "model_usage_queried",
            period=period,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            used_units=snapshot.used_units,
        )
        return UsageData(
            period=period,
            conversation_count=snapshot.event_counts.get("chat", 0),
            document_qa_count=snapshot.event_counts.get("doc_qa", 0),
            quota=Quota(
                plan_name=snapshot.plan_name,
                total_units=snapshot.total_units,
                used_units=snapshot.used_units,
                remaining_units=snapshot.remaining_units,
            ),
            quota_unit=snapshot.quota_unit,
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


class _ReasoningFilter:
    """剥离推理模型思考过程的流式状态机（INT-06）。

    DeepSeek V4 等推理模型经 Dify 下发时，answer 中混有：
      - `<think> ... </think>`（或未闭合的 `<think>` 前缀）
      - `<!--dify-deepseek-reasoning-->` 分隔标记
    产品红线（总方案 1.2 技术黑盒化）要求用户不可见任何模型内部形态，
    本过滤器跨 delta 维护状态：思考段丢弃，思考结束后的正文原样输出。
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"
    _MARKER = "<!--dify-deepseek-reasoning-->"

    def __init__(self) -> None:
        """初始化过滤器状态。"""
        self._in_think = False
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """喂入 delta 文本，返回应展示给用户的可见文本。"""
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            if self._in_think:
                idx = self._buf.find(self._CLOSE)
                if idx >= 0:
                    self._buf = self._buf[idx + len(self._CLOSE) :]
                    self._in_think = False
                    continue
                # 未闭合：保留可能是半个结束标记的尾部，其余丢弃
                self._buf = self._buf[-len(self._CLOSE) :]
                break
            self._buf = self._buf.replace(self._MARKER, "")
            idx = self._buf.find(self._OPEN)
            if idx >= 0:
                out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self._OPEN) :]
                self._in_think = True
                continue
            # 无思考标记：但尾部可能是半个 "<think>"，暂存
            keep = len(self._OPEN) - 1
            if len(self._buf) > keep:
                out.append(self._buf[:-keep])
                self._buf = self._buf[-keep:]
            break
        return "".join(out)

    def flush(self) -> str:
        """流结束：若不存在未闭合思考段，输出剩余缓冲。"""
        if self._in_think:
            self._buf = ""
            return ""
        rest, self._buf = self._buf, ""
        return rest.replace(self._MARKER, "")
