"""片段稳定标识与引用回链业务能力（阶段二 B05，架构文档 2.4 D-11）。

**稳定键（唯一口径）**：

```
segment_key = "seg_" + sha256(f"{document_id}|{segment_index}|{content_sha256}").hexdigest()[:16]
```

- `document_id` 用**自研** `doc_` ID（底座重传后 ID 会变，自研 ID 首次创建即固定）；
- `segment_index` 是该文档内的片段序号（同内容不同位置不得混淆）；
- `content_sha256` 是片段正文**先 `strip()` 与空白归一**后的 SHA-256（内容变化必须产生新键）。

写库时机 = **旁路异步回填**：R7 检索命中与 M1/M2 `citation` 事件到达时
`record_hits(...)` 以 `asyncio.create_task` 写库，**写失败只记 WARN，不影响主流程**
（引用回链是增强能力，不能让检索或对话因为写映射失败而报错）。

回滚开关：`OA_SEGMENT_BACKFILL_ENABLED=false` 时不写库，接口返回空 `segment_id`。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.policy_service import normalize_base_id
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.document_repo import (
    INDEX_STATUS_COMPLETED,
    DocumentStoreProtocol,
    get_document_store,
)
from app.db.repositories.segment_repo import (
    EXCERPT_MAX_CHARS,
    STATUS_DELETED,
    STATUS_STALE,
    STATUS_VALID,
    SegmentMapRecord,
    SegmentStore,
    build_excerpt,
    get_segment_store,
)
from app.schemas.rag import SegmentPosition
from app.schemas.segment import SegmentDetailData, SegmentStatus

logger = get_logger(__name__)

#: 片段稳定标识前缀（架构文档 11.1）
SEGMENT_KEY_PREFIX: Final[str] = "seg_"

#: 稳定键哈希截取长度
_KEY_HASH_LENGTH: Final[int] = 16


def normalize_content(content: str) -> str:
    """归一化片段正文（去首尾空白 + 空白折叠）。

    空白差异不应产生新的稳定键，否则同一段落在不同排版下会被判成「新版本」。

    Args:
        content: 片段正文。

    Returns:
        str: 归一化后的正文。
    """
    return " ".join(str(content).split())


def content_sha256(content: str) -> str:
    """计算归一化正文的 SHA-256。

    Args:
        content: 片段正文。

    Returns:
        str: 64 位十六进制摘要。
    """
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


def build_key(document_id: str, segment_index: int, content: str) -> str:
    """构造片段稳定标识。

    Args:
        document_id: 自研文档 ID。
        segment_index: 文档内片段序号（0 起）。
        content: 片段正文。

    Returns:
        str: 形如 `seg_9f2c1a4b7d0e3f56` 的稳定标识。
    """
    digest = content_sha256(content)
    raw = f"{document_id}|{int(segment_index)}|{digest}"
    short = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_KEY_HASH_LENGTH]
    return f"{SEGMENT_KEY_PREFIX}{short}"


@dataclass(frozen=True, slots=True)
class SegmentHit:
    """一次检索 / 引用命中（回填映射的输入）。

    Attributes:
        base_id: 知识库 ID。
        document_id: 自研文档 ID。
        segment_index: 文档内片段序号（0 起）。
        content: 片段正文。
        native_segment_id: 底座原生片段 ID（仅内部二次取片段用）。
        position_page: 页码。
        position_paragraph: 段落序号。
        run_id: 处理批次（为空时按文档当前批次补齐）。
    """

    base_id: str
    document_id: str
    segment_index: int
    content: str
    native_segment_id: str = ""
    position_page: int | None = None
    position_paragraph: int | None = None
    run_id: str = ""


def _now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(UTC)


def _default_clock() -> datetime:
    """默认时钟（当前 UTC 时间）。

    Returns:
        datetime: 当前 UTC 时间。
    """
    return _now()


def _parse_iso(value: str) -> datetime | None:
    """解析 ISO 8601 时间串。

    Args:
        value: 时间字符串。

    Returns:
        datetime | None: 解析成功返回时间；失败返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class SegmentService:
    """片段映射业务服务。

    Attributes:
        _settings: 配置实例。
        _store: 片段映射存储。
        _documents: 文档元数据存储（用于补齐批次号与文档状态判定）。
        _bases: 知识库服务（KB4 的对象级判权复用同一套可见性判定）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: SegmentStore | None = None,
        documents: DocumentStoreProtocol | None = None,
        bases: KnowledgeBaseService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选片段映射存储（测试注入）。
            documents: 可选文档存储（测试注入）。
            bases: 可选知识库服务（测试注入）。
            clock: 可选时钟（测试注入，用于验证保留期边界）。
        """
        self._settings = settings or get_settings()
        self._store: SegmentStore = store or get_segment_store(self._settings)
        self._documents: DocumentStoreProtocol = documents or get_document_store(self._settings)
        self._bases: KnowledgeBaseService = bases or KnowledgeBaseService(self._settings)
        self._clock: Callable[[], datetime] = clock or _default_clock

    @property
    def store(self) -> SegmentStore:
        """片段映射存储（测试可据此断言回填结果）。"""
        return self._store

    # ------------------------------------------------------------------
    # 回填（R7 / M1 / M2 共用）
    # ------------------------------------------------------------------
    async def record_hits(
        self, tenant_id: str, hits: Sequence[SegmentHit], *, schedule: bool = True
    ) -> int:
        """回填引用命中的片段映射（旁路异步，写失败不影响主流程）。

        Args:
            tenant_id: 租户 ID。
            hits: 命中片段列表。
            schedule: 是否以 `asyncio.create_task` 异步写库（生产路径为 True）。

        Returns:
            int: 计划写入（或已写入）的条数；回填开关关闭时返回 0。
        """
        if not self._settings.segment_backfill_enabled or not hits:
            return 0
        payload = list(hits)
        if not schedule:
            return await self._write_all(tenant_id, payload)
        task = asyncio.create_task(self._write_all(tenant_id, payload))
        # 强引用任务：`create_task` 的返回值只被事件循环弱引用，不保存引用时
        # 任务可能在执行途中被 GC 回收（审计 2026-10-31 L6）。
        _BACKGROUND_TASKS.add(task)
        # 任务异常必须在任务内被吞掉（回填是增强能力），此处只挂一个兜底回调
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        task.add_done_callback(_log_backfill_failure)
        return len(payload)

    async def _write_all(self, tenant_id: str, hits: Sequence[SegmentHit]) -> int:
        """逐条写入映射（异常内部消化，只记 WARN）。

        Args:
            tenant_id: 租户 ID。
            hits: 命中片段列表。

        Returns:
            int: 成功写入条数。
        """
        written = 0
        for hit in hits:
            try:
                await self._write_one(tenant_id, hit)
                written += 1
            except Exception as exc:
                logger.warning(
                    "segment_backfill_failed",
                    tenant_id=tenant_id,
                    document_id=hit.document_id,
                    reason=type(exc).__name__,
                )
        return written

    async def _write_one(self, tenant_id: str, hit: SegmentHit) -> None:
        """写入单条映射（槽位唯一，内容变化换键）。

        Args:
            tenant_id: 租户 ID。
            hit: 命中片段。
        """
        run_id = hit.run_id or await self._run_id_of(tenant_id, hit.document_id)
        await self._store.upsert(
            SegmentMapRecord(
                tenant_id=tenant_id,
                segment_key=build_key(hit.document_id, hit.segment_index, hit.content),
                base_id=hit.base_id,
                document_id=hit.document_id,
                run_id=run_id,
                segment_index=max(int(hit.segment_index), 0),
                native_segment_id=hit.native_segment_id,
                content_sha256=content_sha256(hit.content),
                content_excerpt=build_excerpt(hit.content),
                position_page=hit.position_page,
                position_paragraph=hit.position_paragraph,
                status=STATUS_VALID,
            )
        )

    async def _run_id_of(self, tenant_id: str, document_id: str) -> str:
        """取文档当前处理批次号（未登记文档返回空串）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            str: 批次号。
        """
        record = await self._documents.get(tenant_id, document_id)
        return record.run_id if record is not None else ""

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def fetch(
        self,
        tenant_id: str,
        segment_key: str,
        *,
        base_id: str = "",
        document_id: str = "",
    ) -> SegmentMapRecord | None:
        """按稳定标识读取映射（带对象归属校验）。

        Args:
            tenant_id: 租户 ID。
            segment_key: 稳定标识。
            base_id: 期望所属知识库（非空时校验归属）。
            document_id: 期望所属文档（非空时校验归属）。

        Returns:
            SegmentMapRecord | None: 映射；不存在或归属不符返回 None。
        """
        record = await self._store.get(tenant_id, segment_key)
        if record is None:
            return None
        if base_id and record.base_id and record.base_id != base_id:
            logger.info("segment_object_mismatch", field="base_id", segment_key=segment_key)
            return None
        if document_id and record.document_id and record.document_id != document_id:
            logger.info("segment_object_mismatch", field="document_id", segment_key=segment_key)
            return None
        return record

    async def get_detail(
        self,
        *,
        base_id: str,
        document_id: str,
        segment_key: str,
        with_context: bool,
        auth: AuthContext,
    ) -> SegmentDetailData:
        """KB4 引用详情（回链抽屉数据）。

        `status=stale` 一律返回 HTTP 200 + `status="stale"`（PRD P2-KB-04 第 4 条）：
        引用失效不是错误，前端据 `status` 展示「该引用来自旧版本内容，原文已更新」。

        Args:
            base_id: 知识库 ID。
            document_id: 文档 ID。
            segment_key: 片段稳定标识。
            with_context: 是否返回前后各一段上下文。
            auth: 鉴权上下文。

        Returns:
            SegmentDetailData: 引用详情。

        Raises:
            ApiError: 库不存在（11001）、文档不可定位（11004）、无权读取（20003）。
        """
        normalized_base_id = normalize_base_id(base_id)
        base = await self._bases.require_readable(normalized_base_id, auth)
        base_id = normalized_base_id
        document = await self._documents.get(auth.tenant_id, document_id)
        if document is None or (document.base_id and document.base_id != base_id):
            raise ApiError(
                ErrorCode.DOCUMENT_INDEXING,
                internal_detail=f"document not found doc_id={document_id} base_id={base_id}",
            )
        if document.index_status != INDEX_STATUS_COMPLETED:
            raise ApiError(
                ErrorCode.DOCUMENT_INDEXING,
                internal_detail=(
                    f"document stage={document.stage} status={document.index_status} "
                    f"doc_id={document_id}"
                ),
            )

        record = await self.fetch(
            auth.tenant_id, segment_key, base_id=base_id, document_id=document_id
        )
        if record is None:
            # 映射缺失（保留期外清理或从未回填）：按「旧版本引用」降级，不报错
            logger.info(
                "segment_mapping_missing",
                base_id=base_id,
                document_id=document_id,
                segment_key=segment_key,
            )
            return SegmentDetailData(
                segment_id=segment_key,
                document_id=document_id,
                document_name=document.file_name,
                base_id=base_id,
                base_name=base.name,
                content=None,
                position=SegmentPosition(),
                status=SegmentStatus.STALE,
            )

        status = SegmentStatus.VALID if record.status == STATUS_VALID else SegmentStatus.STALE
        content = self._content_of(record)
        context_before: str | None = None
        context_after: str | None = None
        if with_context and status is SegmentStatus.VALID:
            context_before, context_after = await self._context_of(record)
        logger.info(
            "segment_detail_loaded",
            base_id=base_id,
            document_id=document_id,
            segment_key=segment_key,
            status=str(status.value),
            tenant_id=auth.tenant_id,
        )
        return SegmentDetailData(
            segment_id=record.segment_key,
            document_id=document_id,
            document_name=document.file_name,
            base_id=base_id,
            base_name=base.name,
            content=content,
            context_before=context_before,
            context_after=context_after,
            position=SegmentPosition(
                page=record.position_page, paragraph=record.position_paragraph
            ),
            status=status,
        )

    def _content_of(self, record: SegmentMapRecord) -> str | None:
        """按保留期规则决定是否返回片段内容。

        Args:
            record: 片段映射。

        Returns:
            str | None: 可展示的内容摘要；已过保留期为 None。
        """
        if record.status == STATUS_DELETED:
            return None
        if record.status == STATUS_STALE and not self._within_retention(record):
            return None
        return record.content_excerpt[:EXCERPT_MAX_CHARS]

    def _within_retention(self, record: SegmentMapRecord) -> bool:
        """判断 stale 行是否仍在保留期内（保留期内可返回旧内容）。

        Args:
            record: 片段映射。

        Returns:
            bool: 保留期内返回 True。
        """
        stale_at = _parse_iso(record.stale_at or "")
        if stale_at is None:
            return True
        deadline = stale_at + timedelta(days=self._settings.segment_map_retention_days)
        return self._clock() <= deadline

    async def _context_of(self, record: SegmentMapRecord) -> tuple[str | None, str | None]:
        """取前后各一段上下文。

        Args:
            record: 片段映射。

        Returns:
            tuple[str | None, str | None]: `(前一段, 后一段)`。
        """
        neighbors = await self._store.neighbors(
            record.tenant_id, record.document_id, record.run_id, record.segment_index
        )
        before = next(
            (item for item in neighbors if item.segment_index < record.segment_index), None
        )
        after = next(
            (item for item in neighbors if item.segment_index > record.segment_index), None
        )
        return (
            before.content_excerpt if before is not None else None,
            after.content_excerpt if after is not None else None,
        )

    # ------------------------------------------------------------------
    # 状态迁移（R5 成功完成 / R6 删除）
    # ------------------------------------------------------------------
    async def mark_stale(self, tenant_id: str, document_id: str, keep_run_id: str = "") -> int:
        """把旧批次的映射标记为 stale（新批次成功完成后调用）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            keep_run_id: 新批次号（该批次的行保持 valid）。

        Returns:
            int: 影响行数。
        """
        affected = await self._store.mark_stale(tenant_id, document_id, keep_run_id)
        logger.info(
            "segment_marked_stale",
            document_id=document_id,
            affected=affected,
            tenant_id=tenant_id,
        )
        return affected

    async def mark_deleted(self, tenant_id: str, document_id: str) -> int:
        """把文档全部映射标记为 deleted（R6 删除文档时调用）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            int: 影响行数。
        """
        affected = await self._store.mark_deleted(tenant_id, document_id)
        logger.info(
            "segment_marked_deleted",
            document_id=document_id,
            affected=affected,
            tenant_id=tenant_id,
        )
        return affected


def _log_backfill_failure(task: asyncio.Task[int]) -> None:
    """回收回填任务的异常，避免 "Task exception was never retrieved"。

    Args:
        task: 已完成的回填任务。
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("segment_backfill_task_failed", reason=type(error).__name__)


#: 在途回填任务的强引用集合（任务完成后由 done 回调移除）
_BACKGROUND_TASKS: set[asyncio.Task[int]] = set()


_service: SegmentService | None = None


def get_segment_service(settings: Settings | None = None) -> SegmentService:
    """获取片段映射服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        SegmentService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = SegmentService(settings)
    return _service


def reset_segment_service() -> None:
    """重置片段映射服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "SEGMENT_KEY_PREFIX",
    "SegmentHit",
    "SegmentService",
    "build_key",
    "content_sha256",
    "get_segment_service",
    "normalize_content",
    "reset_segment_service",
]
