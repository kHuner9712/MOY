"""记忆业务编排（阶段四 T4-03.3，架构 §4.5 / §5.3 / PRD §4.2-4.7）。

本模块负责三件事，且**只有**这三件：

1. `extract_and_merge`：把一次会话里的用户消息抽成事实，去掉敏感内容，按
   `normalized_hash` 合并、按相似度写 `superseded_by` 链，最后按 200 条上限淘汰；
2. `maybe_enqueue_extract`：达到"每 10 轮"阈值时**入队**一个 `memory_extract` 任务；
3. `get_setting`：把"全局开关 + 分类开关"读出来给上面两步用。

**四条降级（PRD §4.7）在这里逐条落地**：

| # | 故障 | 本模块行为 |
| --- | --- | --- |
| D-1 | 抽取失败 | 抛出 → Runner 收口为 failed（1 次），记 `memory_extract_degraded` |
| D-3 | 向量不可用 | 跳过向量写入，只存事实；去重退化为 `difflib` 相似度 |
| D-4 | DB 写入失败 | **不上抛、不重试**；连续 3 次打 ERROR 告警 |
| — | 入队失败 | 只记 WARN，对话完全不受影响 |

**统一原则**：记忆是增强，不是前置依赖。这里的任何异常都不允许冒泡到 HTTP 请求——
唯一例外是 D-1 的模型失败，而它发生在后台任务里，本来就不在请求路径上。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.business.audit_service import (
    ACTION_MEMORY_DELETE,
    ACTION_MEMORY_DELETE_ALL,
    ACTION_MEMORY_DELETE_CATEGORY,
    ACTION_MEMORY_SETTINGS,
    AuditService,
    get_audit_service,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.metrics import (
    MEMORY_EXTRACT_TOTAL,
    MEMORY_INJECTED_TOTAL,
    MEMORY_RECALL_DEGRADED_TOTAL,
    MEMORY_RECALL_DURATION_SECONDS,
)
from app.db.repositories.conversation_repo import (
    MessageStore,
    get_message_store,
)
from app.db.repositories.job_repo import JobRecord, JobStore, get_job_store
from app.db.repositories.memory import (
    MEMORY_CATEGORIES,
    MemoryRecord,
    MemorySettingRecord,
    MemoryStore,
    get_memory_store,
    new_memory_id,
)
from app.services.memory_embedding import (
    EmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)
from app.services.memory_extractor import (
    SIMILARITY_THRESHOLD,
    DifyChatCompletion,
    FactCandidate,
    MemoryExtractor,
    content_similarity,
    normalize_content,
    normalized_hash,
    warn_missing_memory_app_key_once,
)

logger = get_logger(__name__)

#: `memory_extract` 任务的最大尝试次数。
#:
#: **必须是 1**（架构 §2.3 第 3 条 / §5.3 / PRD §4.7 D-1）：抽取失败就"这次不记，
#: 下个周期再来"。重试只会把一条注定失败的上游调用重复烧额度，而且下一个
#: 10 轮触发点本来就会重抽——重试是净损失。
MEMORY_EXTRACT_MAX_ATTEMPTS: int = 1

#: 单次抽取回看的用户消息条数（足够覆盖 10 轮，又不至于把提示词撑爆）
EXTRACT_MESSAGE_WINDOW: int = 20

#: 连续多少次 DB 写入失败后打 ERROR 告警（PRD §4.7 D-4 的"连续 3 次"）
WRITE_FAILURE_ALERT_THRESHOLD: int = 3

#: 相似度比较时只看最相近的候选，避免把整个类别都算一遍余弦
SIMILARITY_CANDIDATE_LIMIT: int = 50

#: 「向量能力未配置」是否已告警（进程级，只打一次）。
#:
#: 默认部署就没有 embedding 提供方（架构 A-1），而召回**每一轮对话**都会跑一次——
#: 逐轮打 WARNING 会把真正的降级日志淹掉，也让"日志 1 条"这条判据无法断言。
#: 意外异常（`vector_error`）与超时仍逐次打：那些才是需要被看见的事件。
_vector_unavailable_warned: bool = False


def reset_recall_warning() -> None:
    """重置"已告警"标记（测试隔离用）。"""
    global _vector_unavailable_warned
    _vector_unavailable_warned = False


@dataclass(slots=True)
class ExtractResult:
    """一次抽取的统计结果（日志、任务返回值与测试断言共用）。

    Attributes:
        extracted: 新增的活跃事实条数。
        merged: 命中同义去重键、合并进已有行的条数。
        superseded: 触发 `superseded_by` 链的条数（旧值被新值取代）。
        evicted: 因超过上限被硬删的条数。
        dropped_sensitive: 因敏感信息被丢弃的条数（**只有计数，无原文**）。
        dropped_invalid: 因类别非法/正文为空被丢弃的条数。
        dropped_overflow: 因超出单次上限被丢弃的条数。
        embedding_written: 实际写入的向量行数。
        skipped: 是否因开关关闭/无用户消息而整体跳过。
        write_failed: DB 写入是否发生过失败（失败不重试，只告警）。
    """

    extracted: int = 0
    merged: int = 0
    superseded: int = 0
    evicted: int = 0
    dropped_sensitive: int = 0
    dropped_invalid: int = 0
    dropped_overflow: int = 0
    embedding_written: int = 0
    skipped: bool = False
    write_failed: bool = False
    categories: list[str] = field(default_factory=list)


class MemoryService:
    """记忆服务（抽取 + 合并 + 开关读取）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        memories: MemoryStore | None = None,
        messages: MessageStore | None = None,
        jobs: JobStore | None = None,
        extractor: MemoryExtractor | None = None,
        embedding: EmbeddingProvider | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            memories: 记忆存储。
            messages: 消息存储（只读，用于取 `role=user` 的消息）。
            jobs: 后台任务存储（入队 `memory_extract`）。
            extractor: 抽取器（缺省用底座专用应用，**绝不复用 `ModelService`**）。
            embedding: 向量端口（缺省按配置取，通常不可用）。
            audit: 操作记录服务（**只记动作与条数，不记正文**，PRD §4.8）。
        """
        self._settings = settings or get_settings()
        self._memories: MemoryStore = memories or get_memory_store(self._settings)
        self._messages: MessageStore = messages or get_message_store(self._settings)
        self._jobs: JobStore = jobs or get_job_store(self._settings)
        self._extractor = extractor or MemoryExtractor(DifyChatCompletion(self._settings))
        self._embedding: EmbeddingProvider = embedding or get_embedding_provider(self._settings)
        self._audit: AuditService = audit or get_audit_service(self._settings)
        self._write_failures = 0

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    async def get_setting(self, tenant_id: str, user_id: str) -> MemorySettingRecord:
        """读取记忆开关（无行 = 全开）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            MemorySettingRecord: 开关记录。
        """
        return await self._memories.get_setting(tenant_id, user_id)

    async def update_setting(
        self,
        tenant_id: str,
        user_id: str,
        *,
        enabled: bool | None = None,
        category_switches: dict[str, bool] | None = None,
    ) -> MemorySettingRecord:
        """更新开关（MM4 的唯一写入口）。

        语义：`None` 表示"不改这一项"；`category_switches` 只覆盖**传进来的键**，
        不传就等于保留原值（否则前端只关一类会把其它类一起重置成开启）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            enabled: 全局开关（None 不改）。
            category_switches: 分类开关增量（None 不改）。

        Returns:
            MemorySettingRecord: 落库后的开关。

        Raises:
            ApiError: 分类键非法（20001）——静默忽略非法键会让用户以为关掉了。
        """
        current = await self._memories.get_setting(tenant_id, user_id)
        merged = dict(current.category_switches)
        if category_switches is not None:
            unknown = sorted(set(category_switches) - MEMORY_CATEGORIES)
            if unknown:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    internal_detail=f"unknown memory category: {','.join(unknown)}",
                )
            merged.update({key: bool(value) for key, value in category_switches.items()})
        target = MemorySettingRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            enabled=current.enabled if enabled is None else bool(enabled),
            category_switches=merged,
        )
        saved = await self._memories.upsert_setting(target)
        await self._record_audit(
            ACTION_MEMORY_SETTINGS,
            tenant_id,
            user_id,
            "settings",
            {
                "enabled": saved.enabled,
                "closed_categories": sorted(key for key, value in merged.items() if not value),
            },
        )
        logger.info(
            "memory_setting_updated",
            user_id=user_id,
            enabled=saved.enabled,
            closed_categories=sorted(key for key, value in merged.items() if not value),
        )
        return saved

    async def _record_audit(
        self,
        action: str,
        tenant_id: str,
        user_id: str,
        resource_id: str,
        summary: dict[str, Any],
    ) -> None:
        """写一条操作记录（**只记动作与条数/开关，不记正文**）。

        审计失败不得让用户的操作失败：隐私要求的是"用户能删掉"，
        而不是"删除必须连带审计成功"——所以这里吞异常只记 WARN。

        Args:
            action: 操作编码。
            tenant_id: 企业 ID。
            user_id: 操作人。
            resource_id: 资源标识（记忆 ID / 类别 / `settings`）。
            summary: 变更摘要（**不得含记忆正文**）。
        """
        try:
            await self._audit.record(
                tenant_id=tenant_id,
                actor_id=user_id,
                action=action,
                resource_type="memory",
                resource_id=resource_id,
                change_summary=summary,
            )
        except Exception as exc:
            logger.warning(
                "memory_audit_failed",
                action=action,
                detail=type(exc).__name__,
            )

    async def list_memories(
        self,
        tenant_id: str,
        user_id: str,
        *,
        category: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MemoryRecord], int, MemorySettingRecord]:
        """分页列出生效的记忆（MM1）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 可选类别过滤。
            page: 页码。
            page_size: 每页条数。

        Returns:
            tuple[list[MemoryRecord], int, MemorySettingRecord]:
                `(本页行, 总数, 开关状态)`——`enabled` 与列表同包下发，前端才能一次渲染
                "空列表 + 已关闭"与"空列表 + 还没记到东西"两种不同空态。

        Raises:
            ApiError: 类别非法（20001）。
        """
        if category and category not in MEMORY_CATEGORIES:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"unknown memory category: {category}",
            )
        rows, total = await self._memories.list_page(
            tenant_id, user_id, category=category, page=page, page_size=page_size
        )
        setting = await self._memories.get_setting(tenant_id, user_id)
        return rows, total, setting

    async def delete_memory(self, tenant_id: str, user_id: str, memory_id: str) -> bool:
        """硬删单条记忆（含向量行）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人（防越权：只能删自己的）。
            memory_id: 记忆 ID。

        Returns:
            bool: 确实删掉了返回 True；不存在或不属于本人返回 False（接口层据此报 18002）。
        """
        record = await self._memories.get(tenant_id, memory_id)
        if record is None or record.user_id != user_id:
            return False
        removed = await self._memories.delete(tenant_id, user_id, [memory_id])
        if removed:
            await self._record_audit(
                ACTION_MEMORY_DELETE, tenant_id, user_id, memory_id, {"deleted_count": removed}
            )
            logger.info("memory_deleted", user_id=user_id, memory_id=memory_id, count=removed)
        return removed > 0

    async def delete_memories(self, tenant_id: str, user_id: str, *, category: str = "") -> int:
        """按类别硬删（`category` 为空 = 全删），含历史值与向量行。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 类别（空表示全部）。

        Returns:
            int: 删除的事实条数。

        Raises:
            ApiError: 类别非法（20001）。
        """
        if category and category not in MEMORY_CATEGORIES:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"unknown memory category: {category}",
            )
        removed = await self._memories.delete_by_category(tenant_id, user_id, category=category)
        if removed:
            await self._record_audit(
                ACTION_MEMORY_DELETE_CATEGORY if category else ACTION_MEMORY_DELETE_ALL,
                tenant_id,
                user_id,
                category or "all",
                {"deleted_count": removed, "category": category},
            )
        logger.info(
            "memory_deleted_by_category",
            user_id=user_id,
            category=category or "all",
            count=removed,
        )
        return removed

    async def recall(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        *,
        top_k: int | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - 预算由架构 §4.5 固定为入参名
    ) -> list[MemoryRecord]:
        """召回可注入的记忆（L3），**绝不抛异常**。

        两条降级路径的差别是刻意的（PRD §4.7）：

        - **D-2 超时**：超过预算就**不注入**——用户已经等在那儿了，多等 200ms 换
          一条"可能有用"的记忆不划算；
        - **D-3 向量不可用**：换成"按类别 + `updated_at` 取前 N"**照常注入**——
          没有向量不等于没有记忆，这条路径是本地查询，不会拖慢对话。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            query: 本轮用户输入（用于向量检索）。
            top_k: 条数上限（缺省取配置 `memory_recall_top_k`）。
            timeout: 硬超时秒数（缺省取配置 `memory_recall_timeout_seconds`）。

        Returns:
            list[MemoryRecord]: 可注入的当前值（**低置信度已被剔除**）；不可注入时为空。
        """
        recall_started = time.perf_counter()
        try:
            return await self._recall_impl(tenant_id, user_id, query, top_k=top_k, timeout=timeout)
        finally:
            # 阶段四 T4-05.1：召回耗时（超时那一次也记，便于看 300ms 预算打满的比例）
            MEMORY_RECALL_DURATION_SECONDS.observe(max(time.perf_counter() - recall_started, 0.0))

    async def _recall_impl(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        *,
        top_k: int | None,
        timeout: float | None,  # noqa: ASYNC109 - 预算由架构 §4.5 固定为入参名
    ) -> list[MemoryRecord]:
        """召回实现（`recall` 负责计时，这里只管逻辑）。"""
        limit = int(top_k if top_k is not None else self._settings.memory_recall_top_k)
        budget = float(
            timeout if timeout is not None else self._settings.memory_recall_timeout_seconds
        )
        setting = await self._memories.get_setting(tenant_id, user_id)
        allowed = [item for item in MEMORY_CATEGORIES if setting.category_enabled(item)]
        if not setting.enabled or not allowed:
            # 关了就不注入（PRD §4.4"不允许关了就偷偷还用"）
            MEMORY_INJECTED_TOTAL.inc(result="empty")
            return []

        degraded_reason = ""
        records: list[MemoryRecord] = []
        if self._embedding.available:
            try:
                records = await asyncio.wait_for(
                    self._recall_vector(tenant_id, user_id, query, limit, allowed),
                    timeout=budget,
                )
            except TimeoutError:
                degraded_reason = "timeout"
            except Exception as exc:  # 向量表/扩展不可用 → D-3
                degraded_reason = "vector_unavailable"
                logger.warning(
                    "memory_recall_vector_failed",
                    user_id=user_id,
                    detail=type(exc).__name__,
                )
        else:
            degraded_reason = "vector_unavailable"

        if degraded_reason == "vector_unavailable":
            try:
                records = await asyncio.wait_for(
                    self._recall_by_category(tenant_id, user_id, limit, allowed),
                    timeout=budget,
                )
            except TimeoutError:
                degraded_reason = "timeout"
                records = []
            except Exception as exc:
                logger.warning(
                    "memory_recall_fallback_failed",
                    user_id=user_id,
                    detail=type(exc).__name__,
                )
                records = []

        if degraded_reason:
            MEMORY_RECALL_DEGRADED_TOTAL.inc(reason=degraded_reason)
            _log_recall_degraded(
                reason=degraded_reason,
                user_id=user_id,
                timeout_seconds=budget,
                injected=0 if degraded_reason == "timeout" else len(records),
            )

        # 低置信度不注入（PRD §4.3：仅在管理页显示为"待确认"）
        injectable = [
            item
            for item in records
            if item.confidence != "low" and setting.category_enabled(item.category)
        ][:limit]
        if degraded_reason:
            MEMORY_INJECTED_TOTAL.inc(result="degraded")
        elif injectable:
            MEMORY_INJECTED_TOTAL.inc(result="ok")
        else:
            MEMORY_INJECTED_TOTAL.inc(result="empty")
        return injectable

    async def _recall_vector(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        limit: int,
        categories: list[str],
    ) -> list[MemoryRecord]:
        """向量路径（可能抛异常，由 `recall` 降级）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            query: 查询文本。
            limit: 条数上限。
            categories: 允许的类别。

        Returns:
            list[MemoryRecord]: 最相近的当前值。
        """
        text = (query or "").strip()
        if not text:
            return []
        vectors = await self._embedding.embed([text])
        if not vectors:
            return []
        return await self._memories.search_similar(
            tenant_id, user_id, vectors[0], limit=limit, categories=categories
        )

    async def _recall_by_category(
        self,
        tenant_id: str,
        user_id: str,
        limit: int,
        categories: list[str],
    ) -> list[MemoryRecord]:
        """降级路径：按类别 + `updated_at` 取前 N（本地查询，无向量依赖）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            limit: 条数上限。
            categories: 允许的类别。

        Returns:
            list[MemoryRecord]: 最近更新的当前值。
        """
        rows = await self._memories.list_active(tenant_id, user_id)
        allowed = set(categories)
        return [item for item in rows if item.category in allowed][:limit]

    # ------------------------------------------------------------------
    # 触发：每 N 轮用户消息 → 入队（**绝不阻塞对话**）
    # ------------------------------------------------------------------
    async def maybe_enqueue_extract(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        user_turns: int | None = None,
    ) -> bool:
        """本轮助手消息定稿后调用：达到阈值则入队一个抽取任务。

        为什么是"入队"而不是"直接抽"：抽取要调一次模型（秒级），放在请求路径上
        会把用户这一轮的响应时间翻倍；而且抽取失败绝不能影响对话（PRD §4.7）。

        **本方法永不抛异常**：入队失败只记一条 WARN。队列坏掉是运维问题，
        不该让用户看到"发送失败"。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            conversation_id: 会话 ID（同时作为去重键，避免同一会话被反复排队）。
            user_turns: 该会话累计用户消息数（缺省由仓储统计，测试可注入）。

        Returns:
            bool: 真正入队返回 True；未达阈值/开关关闭/去重命中/失败均返回 False。
        """
        try:
            every = int(self._settings.memory_extract_every_turns)
            if every <= 0:  # 0 = 关闭抽取（只保留手动与历史数据）
                return False
            if not self._extractor.available:
                # 未配置抽取专用应用：**不抽取、不入队**，只打一次告警
                warn_missing_memory_app_key_once(self._settings)
                return False
            setting = await self._memories.get_setting(tenant_id, user_id)
            if not setting.enabled:
                return False
            if not any(setting.category_enabled(category) for category in MEMORY_CATEGORIES):
                # 全部分类都关着：排了也是空跑一次（还多一条任务行），直接不排
                return False
            turns = (
                user_turns
                if user_turns is not None
                else await self._messages.count_role(tenant_id, conversation_id, role="user")
            )
            if turns <= 0 or turns % every != 0:
                return False
            job = await self._jobs.enqueue(
                JobRecord(
                    tenant_id=tenant_id,
                    kind="memory_extract",
                    dedupe_key=conversation_id,
                    payload={"conversation_id": conversation_id, "user_id": user_id},
                    max_attempts=MEMORY_EXTRACT_MAX_ATTEMPTS,
                )
            )
            if job is None:
                logger.info(
                    "memory_extract_already_queued",
                    conversation_id=conversation_id,
                    user_turns=turns,
                )
                return False
            logger.info(
                "memory_extract_enqueued",
                conversation_id=conversation_id,
                job_id=job.job_id,
                user_turns=turns,
            )
            return True
        except Exception as exc:  # 入队失败只记日志，不影响对话（PRD §4.7 统一原则）
            logger.warning(
                "memory_extract_enqueue_failed",
                conversation_id=conversation_id,
                detail=type(exc).__name__,
            )
            return False

    # ------------------------------------------------------------------
    # 抽取 + 合并
    # ------------------------------------------------------------------
    async def extract_and_merge(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ExtractResult:
        """抽取并合并本次会话里的事实（内部事务：抽取 → 丢弃 → 去重 → 写入）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            conversation_id: 会话 ID。

        Returns:
            ExtractResult: 统计结果。

        Raises:
            ChatCompletionError: 模型调用失败/输出不可解析（D-1，由任务层收口）。
            ApiError: 存储不可用等配置错误（同样由任务层收口）。
        """
        result = ExtractResult()
        if not self._extractor.available:
            warn_missing_memory_app_key_once(self._settings)
            result.skipped = True
            return result
        setting = await self._memories.get_setting(tenant_id, user_id)
        if not setting.enabled:
            logger.info("memory_extract_skipped_disabled", user_id=user_id)
            result.skipped = True
            return result
        allowed = {category for category in MEMORY_CATEGORIES if setting.category_enabled(category)}
        if not allowed:
            logger.info("memory_extract_skipped_all_categories_off", user_id=user_id)
            result.skipped = True
            return result

        texts = await self._recent_user_texts(tenant_id, conversation_id)
        if not texts:
            logger.info("memory_extract_skipped_no_user_message", conversation_id=conversation_id)
            result.skipped = True
            return result

        # D-1：模型失败在此上抛（后台任务栈），日志与重试由 JobRunner 负责
        outcome = await self._extractor.extract(texts, categories=allowed)
        result.dropped_sensitive = outcome.dropped_sensitive
        result.dropped_invalid = outcome.dropped_invalid
        result.dropped_overflow = outcome.dropped_overflow
        if outcome.dropped_sensitive:
            # 只记条数，**绝不记原文**（PRD §4.2 第 1 条）
            logger.warning(
                "memory_extract_sensitive_dropped",
                conversation_id=conversation_id,
                dropped_sensitive=outcome.dropped_sensitive,
            )

        for fact in outcome.facts:
            try:
                await self._merge_one(tenant_id, user_id, conversation_id, fact, result)
            except Exception as exc:  # D-4：写入失败不重试、不阻断
                self._note_write_failure(exc, conversation_id)
                result.write_failed = True
                break

        # 阶段四 T4-05.1：抽取次数按结果计数（ok / degraded；skipped 在上面已提前返回）
        MEMORY_EXTRACT_TOTAL.labels(result="degraded" if result.write_failed else "ok").inc()
        if not result.write_failed:
            try:
                result.evicted = await self._evict_overflow(tenant_id, user_id)
            except Exception as exc:
                self._note_write_failure(exc, conversation_id)
                result.write_failed = True

        result.categories = sorted({fact.category for fact in outcome.facts})
        logger.info(
            "memory_extract_done",
            conversation_id=conversation_id,
            extracted=result.extracted,
            merged=result.merged,
            superseded=result.superseded,
            evicted=result.evicted,
            dropped_sensitive=result.dropped_sensitive,
            dropped_invalid=result.dropped_invalid,
            dropped_overflow=result.dropped_overflow,
            embedding_written=result.embedding_written,
            write_failed=result.write_failed,
        )
        return result

    async def _recent_user_texts(self, tenant_id: str, conversation_id: str) -> list[str]:
        """取该会话最近 N 条 `role=user` 的消息正文（**只取 user**）。

        PRD §4.2 第 3 条：不抽取助手自己的话——助手说"我记住了"不是事实来源。
        这里在服务端再过滤一次，而不是信任上游只传 user：过滤漏掉一次，
        "助手认为用户喜欢表格"就会被记成"用户说过喜欢表格"。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。

        Returns:
            list[str]: 用户消息正文（按时间先后）。
        """
        rows = await self._messages.list_before(
            tenant_id,
            conversation_id,
            before_seq=None,
            limit=EXTRACT_MESSAGE_WINDOW * 2,
        )
        # `list_before` 返回 `seq` 降序（最新在前）；先按"最近 N 条用户消息"截断，
        # 再翻回时间正序——反过来会取到窗口里**最旧**的 N 条（长会话里抽到的是开场白）。
        texts = [
            row.content
            for row in rows
            if row.role == "user" and row.content.strip() and row.status != "failed"
        ][:EXTRACT_MESSAGE_WINDOW]
        texts.reverse()
        return texts

    async def _merge_one(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        fact: FactCandidate,
        result: ExtractResult,
    ) -> None:
        """合并一条候选事实（同 hash 合并 / 相似则取代 / 否则新增）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            conversation_id: 来源会话。
            fact: 候选事实。
            result: 统计结果（原地累加）。
        """
        digest = normalized_hash(fact.category, fact.content)
        existing = await self._memories.find_by_hash(tenant_id, user_id, fact.category, digest)
        if existing is not None and existing.active:
            # 同一条事实重复出现 → 合并（**不新增行**，PRD §4.3）
            await self._memories.touch(
                existing,
                hit_count=existing.hit_count + 1,
                confidence=_stronger_confidence(existing.confidence, fact.confidence),
            )
            result.merged += 1
            return

        replacement = await self._find_similar(tenant_id, user_id, fact)
        created = await self._memories.insert(
            MemoryRecord(
                tenant_id=tenant_id,
                memory_id=new_memory_id(),
                user_id=user_id,
                category=fact.category,
                content=fact.content,
                confidence=fact.confidence,
                source_conversation_id=conversation_id,
                normalized_hash=digest,
            )
        )
        if created is None:  # 并发下同 hash 已被写进去 → 退化为合并
            current = await self._memories.find_by_hash(tenant_id, user_id, fact.category, digest)
            if current is not None:
                await self._memories.touch(
                    current,
                    hit_count=current.hit_count + 1,
                    confidence=_stronger_confidence(current.confidence, fact.confidence),
                )
                result.merged += 1
            return

        if replacement is not None:
            # 事实变更 → 旧值写 superseded_by（保留可查，不参与召回），新值成为当前值
            await self._memories.supersede(tenant_id, replacement.memory_id, created.memory_id)
            result.superseded += 1

        await self._write_embedding(tenant_id, created, result)
        result.extracted += 1

    async def _find_similar(
        self, tenant_id: str, user_id: str, fact: FactCandidate
    ) -> MemoryRecord | None:
        """在同类别活跃事实里找语义最近的一条（≥0.9 才算"同一件事"）。

        向量可用时用余弦；不可用时用 `difflib`（见 `content_similarity` 的说明）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            fact: 候选事实。

        Returns:
            MemoryRecord | None: 最相近且达阈值的行；没有返回 None。
        """
        candidates = await self._memories.list_active(tenant_id, user_id, category=fact.category)
        if not candidates:
            return None
        candidates = candidates[:SIMILARITY_CANDIDATE_LIMIT]
        target = normalize_content(fact.content)
        target_vector = await self._embed_query(fact.content)
        vectors: dict[str, list[float]] = {}
        if target_vector:
            try:
                vectors = await self._memories.list_embeddings(
                    tenant_id, [item.memory_id for item in candidates]
                )
            except Exception as exc:  # 向量读失败 → D-3，退化为字符串相似度
                logger.warning("memory_similarity_vector_unavailable", detail=type(exc).__name__)
                vectors = {}
        best: MemoryRecord | None = None
        best_score = 0.0
        for candidate in candidates:
            vector = vectors.get(candidate.memory_id) if vectors else None
            if vector:
                score = cosine_similarity(target_vector, vector)
            else:
                score = content_similarity(target, normalize_content(candidate.content))
            if score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score >= SIMILARITY_THRESHOLD:
            return best
        return None

    async def _embed_query(self, content: str) -> list[float]:
        """给"待比较的新事实"算向量；不可用或失败一律返回空列表（D-3）。

        Args:
            content: 事实正文。

        Returns:
            list[float]: 向量；不可用时为空列表。
        """
        if not self._embedding.available:
            return []
        try:
            vectors = await self._embedding.embed([content])
        except Exception as exc:
            logger.warning("memory_embedding_query_skipped", detail=type(exc).__name__)
            return []
        return list(vectors[0]) if vectors else []

    async def _write_embedding(
        self, tenant_id: str, record: MemoryRecord, result: ExtractResult
    ) -> None:
        """写向量行（不可用则跳过，**事实照存**；PRD §4.7 D-3）。

        Args:
            tenant_id: 企业 ID。
            record: 刚写入的事实。
            result: 统计结果（原地累加）。
        """
        if not self._embedding.available:
            return
        try:
            vectors = await self._embedding.embed([record.content])
        except Exception as exc:
            logger.warning(
                "memory_embedding_write_skipped",
                memory_id=record.memory_id,
                detail=type(exc).__name__,
            )
            return
        if not vectors:
            return
        try:
            await self._memories.upsert_embedding(
                tenant_id,
                record.memory_id,
                model=self._embedding.model,
                dim=self._embedding.dim,
                embedding=vectors[0],
            )
            result.embedding_written += 1
        except Exception as exc:
            logger.warning(
                "memory_embedding_write_failed",
                memory_id=record.memory_id,
                detail=type(exc).__name__,
            )

    async def _evict_overflow(self, tenant_id: str, user_id: str) -> int:
        """超过单用户上限时硬删最该淘汰的行（PRD §4.3）。

        淘汰顺序：`confidence` 升序（低置信度先走）、`updated_at` 升序（最久未更新先走）、
        `memory_id` 兜底保证确定性。淘汰即**物理删除**（含向量行）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            int: 被淘汰的条数。
        """
        limit = int(self._settings.memory_max_per_user)
        active = await self._memories.list_active(tenant_id, user_id)
        if len(active) <= limit:
            return 0
        ordered = sorted(
            active,
            key=lambda item: (
                item.confidence == "high",  # False（low）排前面 → 先淘汰
                item.updated_at,
                item.memory_id,
            ),
        )
        excess = len(active) - limit
        victims = [item.memory_id for item in ordered[:excess]]
        removed = await self._memories.delete(tenant_id, user_id, victims)
        if removed:
            logger.warning(
                "memory_evicted_overflow",
                user_id=user_id,
                removed=removed,
                limit=limit,
            )
        return removed

    def _note_write_failure(self, exc: BaseException, conversation_id: str) -> None:
        """记录一次写入失败；连续 3 次升级为 ERROR 告警（PRD §4.7 D-4）。

        Args:
            exc: 异常。
            conversation_id: 会话 ID（排障定位）。
        """
        self._write_failures += 1
        if self._write_failures >= WRITE_FAILURE_ALERT_THRESHOLD:
            logger.error(
                "memory_write_failure_alert",
                consecutive_failures=self._write_failures,
                detail=type(exc).__name__,
                conversation_id=conversation_id,
            )
            return
        logger.warning(
            "memory_write_degraded",
            consecutive_failures=self._write_failures,
            detail=type(exc).__name__,
            conversation_id=conversation_id,
        )

    @property
    def consecutive_write_failures(self) -> int:
        """当前连续写入失败次数（测试与监控用）。

        Returns:
            int: 连续失败次数。
        """
        return self._write_failures


def _log_recall_degraded(
    *, reason: str, user_id: str, timeout_seconds: float, injected: int
) -> None:
    """记录一次召回降级（**配置性不可用只打一次**，见 `_vector_unavailable_warned`）。

    Args:
        reason: 降级原因（`timeout` / `vector_unavailable`）。
        user_id: 归属个人（排障用）。
        timeout_seconds: 本次预算（秒）。
        injected: 本次实际注入条数（超时路径恒为 0）。
    """
    global _vector_unavailable_warned
    if reason == "timeout":
        logger.warning(
            "memory_recall_degraded",
            user_id=user_id,
            reason=reason,
            timeout_seconds=timeout_seconds,
            injected=injected,
        )
        return
    if _vector_unavailable_warned:
        logger.debug("memory_recall_degraded", user_id=user_id, reason=reason, injected=injected)
        return
    _vector_unavailable_warned = True
    logger.warning(
        "memory_recall_degraded",
        user_id=user_id,
        reason=reason,
        timeout_seconds=timeout_seconds,
        injected=injected,
        note="向量能力不可用：已降级为按类别 + 更新时间召回（本次只告警一次）",
    )


def _stronger_confidence(left: str, right: str) -> str:
    """取更强的置信度（明确陈述可以抬高推测，反之不行）。

    Args:
        left: 原置信度。
        right: 新置信度。

    Returns:
        str: `high` 或 `low`。
    """
    return "high" if "high" in {left, right} else "low"


#: 记忆服务单例
_memory_service: MemoryService | None = None


def get_memory_service(settings: Settings | None = None) -> MemoryService:
    """取记忆服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        MemoryService: 服务实例。
    """
    global _memory_service
    conf = settings or get_settings()
    if _memory_service is None:
        _memory_service = MemoryService(conf)
    return _memory_service


def reset_memory_service() -> None:
    """重置记忆服务单例（测试隔离用）。"""
    global _memory_service
    _memory_service = None


__all__: list[str] = [
    "EXTRACT_MESSAGE_WINDOW",
    "MEMORY_EXTRACT_MAX_ATTEMPTS",
    "WRITE_FAILURE_ALERT_THRESHOLD",
    "ExtractResult",
    "MemoryService",
    "get_memory_service",
    "reset_memory_service",
    "reset_recall_warning",
]
