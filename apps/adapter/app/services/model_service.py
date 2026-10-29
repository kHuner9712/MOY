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
import time
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
from app.business.usage_service import (
    CAP_CHAT,
    BudgetStatus,
    UsageService,
    UsageSnapshot,
    get_usage_service,
)
from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ERROR_REGISTRY, ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.metrics import (
    MODEL_FIRST_TOKEN_SECONDS,
    MODEL_UPSTREAM_CALLS_TOTAL,
    MODEL_UPSTREAM_DURATION_SECONDS,
)
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
from app.services.answer_cap import STANDARD_MODE_NOTICE, apply_answer_cap
from app.services.profile_routing import ProfileRouter

logger = get_logger(__name__)

#: 底座原生路径（仅本文件与 `clients/dify_client.py` 可出现）
_DIFY_CHAT_PATH: Final[str] = "/v1/chat-messages"

#: 计量维度里的生成方标识（内部值，只进 usage_event，不进任何响应）
GENERATION_PROVIDER: Final[str] = "downstream"

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
        memory: Any | None = None,
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
            memory: 可选记忆服务（阶段四抽取触发；测试注入）。

                **类型留作 `Any` 是刻意的**：`app/business/memory_service.py`
                会反向 import `app/services/*`，在模块顶层互相 import 会在
                `app.services` 包初始化期间命中"部分初始化的模块"。
                真正取用的地方（`_memory_service()`）才做惰性 import。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()
        # 阶段四 T4-04.1（F-8）：档位 → 密钥的唯一判定处（Settings 驱动，不读 os.environ）
        self._profile_router = ProfileRouter(self._settings, self._routes)
        # 注入了客户端就用它（测试/Agent 复用同一连接池）；否则**按档位**建客户端。
        # 注意不能无条件 `dify or get_dify_client()`：那会把"未注入"和"注入"抹平，
        # 于是四档又全跑回同一个默认应用——正是 F-8 的行径。
        self._dify_injected = dify is not None
        self._dify = dify if dify is not None else get_dify_client()
        # H-1（审计 2026-10-28）：按 app_key 缓存底座客户端，避免每请求新建
        # 连接池。缓存 key 是 `resolve_app_key` 的返回值；轮换 Dify app key 后
        # 必须重启进程才生效（与 settings 启动固化的现状一致，非新增约束）。
        self._dify_clients: dict[str, DifyClient] = {}
        self._policy: PolicyService = policy or get_policy_service(self._settings)
        self._segments: SegmentService = segments or SegmentService(self._settings)
        self._bases: KnowledgeBaseStore = bases or get_knowledge_base_store(self._settings)
        self._usage: UsageService = usage or get_usage_service(self._settings)
        # 阶段四：会话落库与自研上下文组装（历史真源从客户端改为 message 表）
        self._conversations: ConversationService = conversations or get_conversation_service(
            self._settings
        )
        self._assembler: ContextAssembler = assembler or get_context_assembler(self._settings)
        # 阶段四 T4-03.3：记忆抽取触发（助手消息 settled 后入队，不阻塞对话）
        self._memory_cached: Any | None = memory

    def _memory_service(self) -> Any | None:
        """取记忆服务（惰性 import，见 `__init__` 的 `memory` 参数说明）。

        Returns:
            Any | None: 记忆服务；构造失败时返回 None（记忆是增强，不是依赖）。
        """
        if self._memory_cached is not None:
            return self._memory_cached
        try:
            from app.business.memory_service import get_memory_service

            self._memory_cached = get_memory_service(self._settings)
        except Exception as exc:  # 记忆不可用不得影响对话
            logger.warning("memory_service_unavailable", detail=type(exc).__name__)
            return None
        return self._memory_cached

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
        route = self._profile_router.route(profile.value)
        if route is None:
            raise ApiError(
                ErrorCode.MODEL_PROFILE_UNAVAILABLE,
                internal_detail=f"profile not registered: {profile.value}",
            )
        # 静态声明（yaml）在这里拦；**密钥级可用性**由 `_dify_for()` 在真正发请求前拦
        # ——那里才知道客户端是否被注入（测试注入客户端时不应再要求密钥）。
        # 目录与 `/readyz` 用的是同一个 `ProfileRouter.is_available()`，口径一致。
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
        self.resolve_route(profile)
        return self._profile_router.app_key(profile.value)

    def _dify_for(self, profile: ModelProfile) -> DifyClient:
        """取该档位对应的底座客户端（**按档位隔离**，F-8）。

        注入了客户端（测试）时沿用注入实例；否则按档位密钥新建
        ——`DifyClient.for_app_key` 是"底座地址唯一出口"的既有约定（OA-AUDIT-001）。

        Args:
            profile: 能力档位。

        Returns:
            DifyClient: 该档位的客户端。

        Raises:
            ApiError: 档位不可用或未配置密钥（10004）。
        """
        if self._dify_injected:
            return self._dify
        key = self.resolve_app_key(profile)
        if not key:
            raise ApiError(
                ErrorCode.MODEL_PROFILE_UNAVAILABLE,
                internal_detail=f"profile app key missing: {profile.value}",
            )
        # H-1（审计 2026-10-28）：查缓存 → 建实例 → 写缓存。临界区内没有任何
        # await（resolve_app_key 是同步方法），单事件循环内不会被打断，无需加锁。
        client = self._dify_clients.get(key)
        if client is None:
            client = DifyClient.for_app_key(key, self._settings)
            self._dify_clients[key] = client
        return client

    async def aclose(self) -> None:
        """关闭缓存的底座客户端（进程退出与测试清理用）。

        注入路径（`_dify_injected`）不受影响：注入客户端 `_owns_client=False`，
        其 `aclose` 本就是 no-op。
        """
        for client in self._dify_clients.values():
            await client.aclose()
        self._dify_clients.clear()

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
        # P4-11 ①（2026-10-27 用户裁决）：额度判定在**任何上游调用之前**做，
        # 但**不再拦截**——超额时只把回答限长（常规模式），`code` 仍为 0。
        #
        # R2-ENG-08（本轮）：判定与提示**共用同一份用量快照**
        # （`_generation_budget_and_notice`）。修复前两者各查一遍库，`snapshot()`
        # 单次 4 次存储往返，首 token 之前白白多付 4 次。合并后仍发生在任何
        # 下游请求之前，"先判定后花钱"的顺序纪律不变。
        budget, notice = await self._generation_budget_and_notice(auth)
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
        # R2-ENG-08：`notice` 已在上一步与 `budget` 一起取回（共用同一份用量快照），
        # 这里**不再**单独调用 `_quota_notice`——重复查库正是本次修复要消除的。
        # 超额时提示**必须**是常规模式文案（PRD 5.3 ①）：`_quota_notice` 给的是
        # 「本月可用量已用完，请联系管理员调整」——那是"要停服"的口径，
        # 与本条"继续可用、只限长"矛盾。两者都指向 15006，但文案必须说清出路。
        if budget is not None and budget.exhausted:
            notice = (
                int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code),
                STANDARD_MODE_NOTICE,
            )

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
                profile=request.profile.value,
                prompt_tokens=len(assembled.query_text),
                completion_tokens=len(stub.content),
                estimated=True,
            )
            await self._finalize_assistant(
                tenant_id, started, content=stub.content, status="settled"
            )
            return _with_notice(_with_conversation(stub, started, assembled), notice)

        # 阶段四 T4-05.1：上游调用计数与耗时（`outcome` 在 except 里置 error，
        # 计数放在 finally 里，成功与失败两条路径都能覆盖且不重复）
        upstream_started = time.perf_counter()
        upstream_outcome = "ok"
        try:
            response = await self._dify_for(request.profile).post(
                _DIFY_CHAT_PATH,
                self._build_downstream_payload(
                    request, user_id, query_override=assembled.query_text
                ),
            )
        except Exception:
            upstream_outcome = "error"
            # 上游失败：把助手行收口为 failed（**不给用户看码与堆栈**），再原样抛出
            await self._finalize_assistant(
                tenant_id, started, content="", status="failed", error_reason="回答生成失败，请重试"
            )
            raise
        finally:
            MODEL_UPSTREAM_CALLS_TOTAL.labels(
                profile=request.profile.value, outcome=upstream_outcome
            ).inc()
            MODEL_UPSTREAM_DURATION_SECONDS.labels(profile=request.profile.value).observe(
                max(time.perf_counter() - upstream_started, 0.0)
            )
        # 非流式同样剥离推理标记（INT-06）
        payload = response.payload
        if isinstance(payload.get("answer"), str):
            flt = _ReasoningFilter()
            payload = {**payload, "answer": flt.feed(payload["answer"]) + flt.flush()}
        data = self._transform_generate_response(payload)
        settled = await self._attach_citation_backlinks(
            data, authorized, self._tenant_of(auth), schedule=False
        )
        # P4-11 ①「常规模式」：超额不拦停，只把**已生成**的正文按字收口，
        # 末尾追加业务语并挂 `notice_code=15006`（PRD 5.3 ①，用户可感知但不停服）。
        # 位置放在引用回链之后：引用是正文的一部分，先截断会把引用标记切坏。
        if budget is not None and budget.exhausted:
            capped, _ = apply_answer_cap(
                settled.content, limit=self._settings.standard_mode_answer_chars
            )
            settled = settled.model_copy(
                update={
                    "content": capped,
                    "notice_code": int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code),
                    "notice_message": STANDARD_MODE_NOTICE,
                }
            )
        # 结算时点：下游已返回 message_end（blocking 模式即响应体），
        # 用下游 usage.total_units 结算，source='settled'（D-12 第 3 条）
        prompt_tokens, completion_tokens, tokens_estimated = self._token_usage(
            payload, assembled.query_text, settled.content
        )
        await self._settle_generation(
            generation_id=settled.generation_id,
            units=settled.usage.total_units,
            has_authorized_bases=bool(authorized),
            auth=auth,
            source="settled",
            profile=request.profile.value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated=tokens_estimated,
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
            return
        # 阶段四 T4-03.3：只有**正常定稿**才触发抽取。
        # `aborted`/`failed` 轮次不抽：那一轮助手没给出完整回答，用户那句话往往
        # 只是半句（"我想说的是…"），抽出来的"事实"比不抽更糟。
        if status == "settled":
            await self._maybe_enqueue_memory_extract(tenant_id, started)

    async def _maybe_enqueue_memory_extract(
        self, tenant_id: str, started: StartedGeneration
    ) -> None:
        """达到阈值时入队一次记忆抽取（**任何异常都不影响对话**）。

        Args:
            tenant_id: 企业 ID。
            started: 本轮两条消息句柄（取 `user_id` 与 `conversation_id`）。
        """
        memory = self._memory_service()
        if memory is None:
            return
        try:
            await memory.maybe_enqueue_extract(
                tenant_id,
                started.user_message.user_id,
                started.conversation.conversation_id,
            )
        except Exception as exc:  # `maybe_enqueue_extract` 自身不抛，这里是最后一道保险
            logger.warning(
                "memory_extract_trigger_failed",
                conversation_id=started.conversation.conversation_id,
                detail=type(exc).__name__,
            )

    async def _resolve_generation_budget(
        self, auth: AuthContext | None, snapshot: UsageSnapshot | None = None
    ) -> BudgetStatus | None:
        """取本次生成可用的额度判定结果（**不再拦截**，P4-11 ① 软限制）。

        为什么改名：它曾叫 `_enforce_generation_budget`，在超额时抛 `15006` 拦停。
        2026-10-27 用户裁决按 PRD 5.3 ① 改为「≥100% 对话继续可用，只是单次回答限长」，
        于是这里**只取状态、不抛**——留着旧名会让下一个人以为它还会拦，
        再在别处补一层拦截，两条口径就开始漂移了。

        两条纪律仍然保留：

        1. **必须在任何下游请求之前**取（限长要用它的结果，晚取就是先花钱后限长）；
        2. **判定本身出错时放行并记 WARN**——护栏不该把可用性押在计量库上。

        Args:
            auth: 鉴权上下文（无用户时跳过判定：内部/桩调用不计入个人额度）。
            snapshot: 同一请求内已取到的用量快照（R2-ENG-08，避免重复查库）。

        Returns:
            BudgetStatus | None: 判定结果；无用户或判定失败时返回 None（按"未超额"处理）。
        """
        user_id = auth.user_id if auth is not None else ""
        if not user_id:
            return None
        try:
            return await self._usage.check_capability_allowed(
                capability=CAP_CHAT,
                tenant_id=self._tenant_of(auth),
                user_id=user_id,
                snapshot=snapshot,
            )
        except Exception as exc:  # 判定失败不阻断（与 `_quota_notice` 同口径）
            logger.warning(
                "generation_budget_check_failed",
                user_id=user_id,
                detail=type(exc).__name__,
            )
            return None

    @staticmethod
    def _token_usage(
        payload: dict[str, Any], prompt_text: str, completion_text: str
    ) -> tuple[int, int, bool]:
        """从下游响应里取 token 用量；取不到时按字符数估算。

        为什么允许估算：`usage_event` 的 4 个维度是**看板口径**，不是账单口径
        （账单用 `units`）。真实 token 缺失时写估算值并把 `source` 置 `estimated`，
        看板就能把"实测"与"折算"分开统计，而不是整列为空。

        Args:
            payload: 下游响应体。
            prompt_text: 本轮完整 query（含历史与记忆）。
            completion_text: 助手回答正文。

        Returns:
            tuple[int, int, bool]: `(prompt_tokens, completion_tokens, 是否估算)`。
        """
        metadata = payload.get("metadata")
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if isinstance(prompt, int) and isinstance(completion, int) and (prompt or completion):
            return prompt, completion, False
        return len(prompt_text), len(completion_text), True

    async def _settle_generation(
        self,
        *,
        generation_id: str,
        units: int,
        has_authorized_bases: bool,
        auth: AuthContext | None,
        source: str,
        profile: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        estimated: bool = False,
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
            profile: 能力档位（M11 计量维度）。
            prompt_tokens: 输入 token（真实或估算）。
            completion_tokens: 输出 token（真实或估算）。
            estimated: token 是否为估算值（true 时事件 `source` 置 `estimated`）。

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
                profile=profile,
                provider=GENERATION_PROVIDER,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated=estimated,
            )
        except Exception as exc:  # 计量失败不得中断用户请求（对账脚本负责发现偏差）
            logger.warning(
                "usage_settlement_failed",
                generation_id=generation_id,
                source=source,
                detail=type(exc).__name__,
            )
            return False

    async def _quota_notice(
        self, auth: AuthContext | None, snapshot: UsageSnapshot | None = None
    ) -> tuple[int, str]:
        """取本次请求的非阻断额度提示（B27 `15006`）。

        单一真源：委托 `UsageService.quota_notice()`，其判据就是 M4/BL5 的同一份
        `snapshot().over_quota_warning`——本类**不重算**额度公式（两套算法必然漂移）。

        Args:
            auth: 鉴权上下文（`None` 或缺失用户时无提示）。
            snapshot: 同一请求内已取到的用量快照（R2-ENG-08，避免重复查库）。

        Returns:
            tuple[int, str]: `(提示码, 提示文案)`；无提示时为 `(0, "")`。
        """
        user_id = auth.user_id if auth is not None else ""
        if not user_id:
            return 0, ""
        return await self._usage.quota_notice(
            tenant_id=self._tenant_of(auth), user_id=user_id, snapshot=snapshot
        )

    async def _generation_budget_and_notice(
        self, auth: AuthContext | None
    ) -> tuple[BudgetStatus | None, tuple[int, str]]:
        """一次取齐「额度判定」与「非阻断额度提示」（R2-ENG-08）。

        为什么合并：这两件事的**输入是同一份用量快照**，修复前却各自查了一遍库
        （`_resolve_generation_budget` → `UsageService.snapshot()`，
        `_quota_notice` → `UsageService.snapshot()`）。`snapshot()` 单次要打
        4 次存储往返（月度行 / 套餐 / 日趋势 / 事件计数），于是**首 token 之前**
        白白多出 4 次往返——在冷启动或远端库上，这正是"感觉慢半拍"的来源。

        语义**完全不变**：
        - 快照取失败时两个子调用各自回落到"自取快照"的旧路径，判定仍按未超额、
          提示仍为空（护栏失败不影响可用性这条纪律原样保留）；
        - 判定结果与提示码仍由原方法产出，本方法只负责共享输入。

        Args:
            auth: 鉴权上下文（无用户时返回 `(None, (0, ""))`）。

        Returns:
            tuple[BudgetStatus | None, tuple[int, str]]:
                `(额度判定结果, (提示码, 提示文案))`。
        """
        user_id = auth.user_id if auth is not None else ""
        if not user_id:
            return None, (0, "")
        tenant_id = self._tenant_of(auth)
        snapshot: UsageSnapshot | None = None
        try:
            snapshot = await self._usage.snapshot(tenant_id=tenant_id, user_id=user_id)
        except Exception as exc:
            # 快照取不到：留 None，让两个子调用各自按旧路径兜底（行为与修复前一致）
            logger.warning(
                "generation_usage_snapshot_failed",
                user_id=user_id,
                detail=type(exc).__name__,
            )
        budget = await self._resolve_generation_budget(auth, snapshot=snapshot)
        notice = await self._quota_notice(auth, snapshot=snapshot)
        return budget, notice

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

        # P4-11 ①（2026-10-27 用户裁决）：流式与 M1 同口径，但**不再下发 402 error 帧**——
        # PRD 5.3 ① 要求「≥100% 对话继续可用，只是单次回答限长」。
        # 这里取一次判定结果；超额提示仍走 `done` 帧的 `notice_*`（既有 `notice` 通道）。
        #
        # **已知边界（不要误以为是漏实现）**：流式**不做帧级截断**——
        # 在 SSE 上按字截断要跨帧缓冲、还要避免切断多字节字符与引用标记，
        # 处理不当会让前端拿到半句话或坏帧（比"答案长一点"严重得多）。
        # 故降级为"只加终帧提示、不截断"，该边界已写进 PRD 5.3 ① 的注。
        # R2-ENG-08（本轮）：流式与 M1 同口径，判定与提示共用同一份用量快照。
        budget, notice = await self._generation_budget_and_notice(auth)

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
        # R2-ENG-08：`notice` 已随 `budget` 一起取回（共用同一份用量快照），这里不再查库。
        # 超额时**必须**带 15006 提示（即使 `_quota_notice` 因套餐口径没算出提示）：
        # 流式不截断是已知边界，这条 `notice` 就是用户唯一能感知"进入常规模式"的地方。
        if notice[0] == 0 and budget is not None and budget.exhausted:
            notice = (
                int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code),
                STANDARD_MODE_NOTICE,
            )
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
            first_token_at: float | None = None
            stream_started = time.perf_counter()
            # P4-11 ①「常规模式」：流式按**整帧**收口（主理人 2026-10-27 定的方案）。
            # 规则：只有在"这一帧整帧放得下"时才转发；放不下就**整帧丢弃**并置
            # `cap_reached`，此后继续消费上游但不再 yield。
            # 为什么不是"跨帧缓冲切半句"：切半句会切断多字节字符与引用标记，
            # 前端拿到的是坏帧或半句话——比"答案略短"严重得多。
            # 为什么达上限后仍消费上游：提前 close 会破坏连接复用与计费完整性。
            # **只在超额时收口**：`answer_cap=None` 表示不设限。
            # 这里必须判 `budget.exhausted`——漏判会把限长打到全部用户，
            # 且从响应上完全看不出来（测试用反向断言钉住）。
            answer_cap: int | None = (
                self._settings.standard_mode_answer_chars
                if budget is not None and budget.exhausted
                else None
            )
            capped_chars = 0
            cap_reached = False
            async for event in self._dify_for(request.profile).post_stream(
                _DIFY_CHAT_PATH, streaming_payload
            ):
                native = str(event.get("event", ""))
                if native in {"message", "agent_message"}:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                        MODEL_FIRST_TOKEN_SECONDS.labels(profile=request.profile.value).observe(
                            max(first_token_at - stream_started, 0.0)
                        )
                    # 推理模型（DeepSeek V4 等）会把思考过程混在 answer 中下发，
                    # 由状态机剥离后再发 delta（INT-06，技术黑盒化红线）
                    visible = reasoning_filter.feed(str(event.get("answer", "")))
                    if not visible:
                        continue
                    # `produced_chars` 记**上游产出**（中断时按它折算费用），
                    # `produced_parts` 记**用户实际看到的**（落库历史必须与所见一致）。
                    produced_chars += len(visible)
                    if answer_cap is not None and (
                        cap_reached or capped_chars + len(visible) > answer_cap
                    ):
                        cap_reached = True
                        continue
                    capped_chars += len(visible)
                    produced_parts.append(visible)
                    event = {**event, "answer": visible}
                elif native == "message_end":
                    # 流结束前冲洗过滤器缓冲（短回答可能整体滞留在缓冲中）
                    rest = reasoning_filter.flush()
                    if rest:
                        produced_chars += len(rest)
                        # 冲洗出的尾巴同样按整帧判断：放不下就不发（不切半句）
                        if answer_cap is None or (
                            not cap_reached and capped_chars + len(rest) <= answer_cap
                        ):
                            capped_chars += len(rest)
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
                    profile=request.profile.value,
                    prompt_tokens=len(assembled.query_text),
                    completion_tokens=produced_chars,
                    estimated=True,
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
                        profile=request.profile.value,
                        prompt_tokens=len(assembled.query_text),
                        completion_tokens=produced_chars,
                        estimated=True,
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
                    # B-3：目录 `status` 取**运行期**可用性（该档能否解析出密钥），
                    # 不再是 yaml 里的静态值——否则会出现"目录写着可用、一调就 10004"。
                    status=(
                        ProfileStatus.AVAILABLE
                        if self._profile_router.is_available(route.profile)
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
