"""片段稳定标识与引用回链单元测试（阶段二 B05，架构文档 2.4 D-11）。

覆盖点：

1. 稳定键口径：`seg_` + sha256(document_id|segment_index|content_sha256)[:16]；
   **空白归一**后同一内容必须得到同一键（排版差异不得判成新版本）；
2. 回填：`record_hits` 写入 `valid` 行并带当前批次号；回填开关关闭时 0 写入；
   存储异常被吞掉（只记 WARN），不冒泡到检索/对话主流程（降级红线）；
3. 三态机：`mark_stale(keep_run_id)` 只把旧批次转 stale、`mark_deleted` 转 deleted；
4. KB4 详情：`valid` 带上下文；`stale` 保留期内返回旧内容；超保留期 `content=null`；
   映射缺失降级为 `stale`（不报错）；文档未处理完 → `11004`；库无权 → `20003`。

测试运行在内存后端上，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.segment_service import (
    SegmentHit,
    SegmentService,
    build_key,
    content_sha256,
    normalize_content,
)
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.document_repo import (
    DocumentProgressPatch,
    DocumentRecord,
    InMemoryDocumentStore,
)
from app.db.repositories.knowledge_base_repo import (
    InMemoryKnowledgeBaseStore,
    KnowledgeBaseRecord,
)
from app.db.repositories.segment_repo import (
    InMemorySegmentStore,
    SegmentMapRecord,
    SegmentStore,
)
from app.schemas.rag import SegmentPosition
from app.schemas.segment import SegmentStatus

pytestmark = pytest.mark.unit

_TENANT = "tnt_seg"
_OWNER = AuthContext(user_id="usr_owner", tenant_id=_TENANT, roles=["member"])
_OTHER = AuthContext(user_id="usr_other", tenant_id=_TENANT, roles=["member"])
_BASE_ID = "kb_seg_001"
_DOC_ID = "doc_seg_001"
_RUN_1 = "run_001"
_RUN_2 = "run_002"


class _Clock:
    """可控时钟（验证保留期边界）。"""

    def __init__(self, value: datetime | None = None) -> None:
        """初始化时钟。

        Args:
            value: 初始时刻，缺省为当前 UTC 时间。
        """
        self.value = value or datetime.now(UTC)

    def __call__(self) -> datetime:
        """读取当前时刻。

        Returns:
            datetime: 当前时刻。
        """
        return self.value


def _settings(**overrides: object) -> Settings:
    """构造桩模式配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base = {
        "env": "test",
        "deploy_mode": "private",
        "downstream_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _BrokenStore(InMemorySegmentStore):
    """写入必然失败的存储（验证回填降级）。"""

    async def upsert(self, record: SegmentMapRecord) -> SegmentMapRecord:
        """始终抛错。

        Args:
            record: 映射记录。

        Raises:
            RuntimeError: 始终抛出。
        """
        raise RuntimeError("store unavailable")


class _Harness:
    """片段服务测试夹具（内存三存储 + 知识库判权）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        store: SegmentStore | None = None,
    ) -> None:
        """初始化夹具。

        Args:
            settings: 可选配置。
            store: 可选片段存储（测试注入）。
        """
        self.settings = settings or _settings()
        self.segments = store or InMemorySegmentStore()
        self.documents = InMemoryDocumentStore()
        self.bases = InMemoryKnowledgeBaseStore()
        self.clock = _Clock()
        self.bases_service = KnowledgeBaseService(self.settings, store=self.bases)
        self.service = SegmentService(
            self.settings,
            store=self.segments,
            documents=self.documents,
            bases=self.bases_service,
            clock=self.clock,
        )

    async def seed(
        self,
        *,
        visibility: str = "organization",
        index_status: str = "completed",
        run_id: str = _RUN_1,
        owner: str = _OWNER.user_id,
    ) -> None:
        """写入知识库与文档。

        Args:
            visibility: 知识库可见范围。
            index_status: 文档三态进度。
            run_id: 处理批次。
            owner: 知识库所有者。
        """
        await self.bases.create(
            KnowledgeBaseRecord(
                tenant_id=_TENANT,
                base_id=_BASE_ID,
                name="员工手册库",
                visibility=visibility,
                owner_user_id=owner,
            )
        )
        stage = "completed" if index_status == "completed" else "parsing"
        await self.documents.create(
            DocumentRecord(
                tenant_id=_TENANT,
                document_id=_DOC_ID,
                base_id=_BASE_ID,
                file_name="员工手册.pdf",
                index_status=index_status,
                stage=stage,
                uploader_user_id=owner,
                run_id=run_id,
            )
        )


# ===========================================================================
# 1. 稳定键口径
# ===========================================================================
def test_build_key_matches_documented_formula() -> None:
    """稳定键 = `seg_` + sha256(document_id|index|content_sha256)[:16]。"""
    import hashlib

    content = "年假申请需提前三个工作日提交"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    expected = "seg_" + hashlib.sha256(f"{_DOC_ID}|3|{digest}".encode()).hexdigest()[:16]
    assert build_key(_DOC_ID, 3, content) == expected
    assert build_key(_DOC_ID, 3, content).startswith("seg_")
    assert len(build_key(_DOC_ID, 3, content)) == len("seg_") + 16


def test_build_key_normalizes_whitespace() -> None:
    """前后空白与连续空白不影响稳定键（排版差异不产生新键）。"""
    assert normalize_content("  年假   申请\n需提前  ") == "年假 申请 需提前"
    assert build_key(_DOC_ID, 0, "  年假   申请\n需提前  ") == build_key(
        _DOC_ID, 0, "年假 申请 需提前"
    )


def test_build_key_changes_with_content_and_index() -> None:
    """内容或槽位变化必须产生新键（stale 判定的唯一依据）。"""
    base = build_key(_DOC_ID, 0, "旧版本内容")
    assert build_key(_DOC_ID, 0, "新版本内容") != base
    assert build_key(_DOC_ID, 1, "旧版本内容") != base
    assert build_key("doc_other", 0, "旧版本内容") != base
    assert content_sha256("旧版本内容") != content_sha256("新版本内容")


# ===========================================================================
# 2. 回填
# ===========================================================================
async def test_record_hits_writes_valid_rows_with_run_id() -> None:
    """回填写入 `valid` 行，批次号取文档当前批次。"""
    harness = _Harness()
    await harness.seed()

    written = await harness.service.record_hits(
        _TENANT,
        [
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=0,
                content="第一条制度内容",
                position_page=12,
                position_paragraph=3,
            ),
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=1,
                content="第二条制度内容",
            ),
        ],
        schedule=False,
    )
    assert written == 2
    key = build_key(_DOC_ID, 0, "第一条制度内容")
    stored = await harness.segments.get(_TENANT, key)
    assert stored is not None
    assert stored.status == "valid"
    assert stored.run_id == _RUN_1
    assert stored.position_page == 12
    assert stored.position_paragraph == 3
    assert stored.content_excerpt == "第一条制度内容"
    assert stored.content_sha256 == content_sha256("第一条制度内容")


async def test_record_hits_respects_backfill_switch() -> None:
    """`OA_SEGMENT_BACKFILL_ENABLED=false` 时不写库（D-11 回滚开关）。"""
    harness = _Harness(settings=_settings(segment_backfill_enabled=False))
    await harness.seed()

    written = await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="内容")],
        schedule=False,
    )
    assert written == 0
    assert await harness.segments.count(_TENANT, _DOC_ID) == 0


async def test_record_hits_swallows_store_failures() -> None:
    """存储异常被吞掉：回填失败不影响检索/对话主流程。"""
    harness = _Harness(store=_BrokenStore())
    await harness.seed()

    written = await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="内容")],
        schedule=False,
    )
    assert written == 0


async def test_record_hits_schedules_background_task() -> None:
    """`schedule=True` 返回计划条数（不等待写库完成）。"""
    harness = _Harness()
    await harness.seed()

    planned = await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="内容")],
        schedule=True,
    )
    assert planned == 1
    # 让出事件循环，收尾后台任务（避免用例结束后任务仍挂着）

    await asyncio.sleep(0)
    assert await harness.segments.count(_TENANT, _DOC_ID) == 1


# ===========================================================================
# 3. 三态机
# ===========================================================================
async def test_mark_stale_keeps_current_run() -> None:
    """新批次完成：旧批次转 stale，当前批次保持 valid（架构文档 2.4 第 3 条）。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="旧内容")],
        schedule=False,
    )
    await harness.service.record_hits(
        _TENANT,
        [
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=0,
                content="新内容",
                run_id=_RUN_2,
            )
        ],
        schedule=False,
    )

    affected = await harness.service.mark_stale(_TENANT, _DOC_ID, keep_run_id=_RUN_2)
    assert affected == 1

    old = await harness.segments.get(_TENANT, build_key(_DOC_ID, 0, "旧内容"))
    new = await harness.segments.get(_TENANT, build_key(_DOC_ID, 0, "新内容"))
    assert old is not None and old.status == "stale"
    assert old.stale_at
    assert new is not None and new.status == "valid"


async def test_mark_deleted_marks_all_rows() -> None:
    """R6 删除文档：全部映射转 deleted（引用抽屉展示「该引用已不可查看」）。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="内容")],
        schedule=False,
    )

    assert await harness.service.mark_deleted(_TENANT, _DOC_ID) == 1
    stored = await harness.segments.get(_TENANT, build_key(_DOC_ID, 0, "内容"))
    assert stored is not None
    assert stored.status == "deleted"
    assert stored.stale_at


# ===========================================================================
# 4. KB4 详情
# ===========================================================================
async def test_get_detail_returns_valid_with_context() -> None:
    """命中 `valid` 行：返回片段正文、前后文与位置。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=0,
                content="前一段：入职流程",
                position_page=11,
                position_paragraph=2,
            ),
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=1,
                content="目标片段：年假申请",
                position_page=12,
                position_paragraph=3,
            ),
            SegmentHit(
                base_id=_BASE_ID,
                document_id=_DOC_ID,
                segment_index=2,
                content="后一段：请假审批",
                position_page=13,
                position_paragraph=1,
            ),
        ],
        schedule=False,
    )
    key = build_key(_DOC_ID, 1, "目标片段：年假申请")

    data = await harness.service.get_detail(
        base_id=_BASE_ID,
        document_id=_DOC_ID,
        segment_key=key,
        with_context=True,
        auth=_OWNER,
    )
    assert data.status is SegmentStatus.VALID
    assert data.segment_id == key
    assert data.content == "目标片段：年假申请"
    assert data.context_before == "前一段：入职流程"
    assert data.context_after == "后一段：请假审批"
    assert data.position == SegmentPosition(page=12, paragraph=3)
    assert data.base_name == "员工手册库"
    assert data.document_name == "员工手册.pdf"


async def test_get_detail_without_context_returns_null_context() -> None:
    """`with_context=false` 时不返回上下文。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [
            SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="片段 A"),
            SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=1, content="片段 B"),
        ],
        schedule=False,
    )
    data = await harness.service.get_detail(
        base_id=_BASE_ID,
        document_id=_DOC_ID,
        segment_key=build_key(_DOC_ID, 1, "片段 B"),
        with_context=False,
        auth=_OWNER,
    )
    assert data.context_before is None
    assert data.context_after is None


async def test_get_detail_stale_returns_200_semantics() -> None:
    """重处理后旧引用返回 `stale` 且保留期内仍可展示旧内容（PRD 验收 3）。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="旧版本内容")],
        schedule=False,
    )
    await harness.documents.update_progress(
        _TENANT,
        _DOC_ID,
        _completed_patch(_RUN_2),
    )
    await harness.service.mark_stale(_TENANT, _DOC_ID, keep_run_id=_RUN_2)

    data = await harness.service.get_detail(
        base_id=_BASE_ID,
        document_id=_DOC_ID,
        segment_key=build_key(_DOC_ID, 0, "旧版本内容"),
        with_context=True,
        auth=_OWNER,
    )
    assert data.status is SegmentStatus.STALE
    assert data.content == "旧版本内容"


async def test_get_detail_stale_beyond_retention_has_no_content() -> None:
    """超过保留期的 stale 行不再返回内容（抽屉只提示「原文已更新」）。"""
    harness = _Harness()
    await harness.seed()
    await harness.service.record_hits(
        _TENANT,
        [SegmentHit(base_id=_BASE_ID, document_id=_DOC_ID, segment_index=0, content="旧版本内容")],
        schedule=False,
    )
    key = build_key(_DOC_ID, 0, "旧版本内容")
    await harness.service.mark_stale(_TENANT, _DOC_ID, keep_run_id=_RUN_2)

    harness.clock.value = harness.clock.value + timedelta(
        days=harness.settings.segment_map_retention_days + 1
    )
    data = await harness.service.get_detail(
        base_id=_BASE_ID,
        document_id=_DOC_ID,
        segment_key=key,
        with_context=True,
        auth=_OWNER,
    )
    assert data.status is SegmentStatus.STALE
    assert data.content is None


async def test_get_detail_missing_mapping_degrades_to_stale() -> None:
    """映射缺失（保留期外清理 / 从未回填）降级为 `stale`，不报错。"""
    harness = _Harness()
    await harness.seed()

    data = await harness.service.get_detail(
        base_id=_BASE_ID,
        document_id=_DOC_ID,
        segment_key="seg_0000000000000000",
        with_context=True,
        auth=_OWNER,
    )
    assert data.status is SegmentStatus.STALE
    assert data.content is None


async def test_get_detail_document_not_completed_returns_11004() -> None:
    """文档未处理完 → 11004。"""
    harness = _Harness()
    await harness.seed(index_status="processing")

    with pytest.raises(ApiError) as excinfo:
        await harness.service.get_detail(
            base_id=_BASE_ID,
            document_id=_DOC_ID,
            segment_key="seg_0000000000000000",
            with_context=True,
            auth=_OWNER,
        )
    assert excinfo.value.code == ErrorCode.DOCUMENT_INDEXING


async def test_get_detail_unknown_document_returns_11004() -> None:
    """文档不存在 → 11004。"""
    harness = _Harness()
    await harness.seed()

    with pytest.raises(ApiError) as excinfo:
        await harness.service.get_detail(
            base_id=_BASE_ID,
            document_id="doc_missing",
            segment_key="seg_0000000000000000",
            with_context=True,
            auth=_OWNER,
        )
    assert excinfo.value.code == ErrorCode.DOCUMENT_INDEXING


async def test_get_detail_forbidden_base_returns_20003() -> None:
    """`restricted` 库未授权成员 → 20003。"""
    harness = _Harness()
    await harness.seed(visibility="restricted")

    with pytest.raises(ApiError) as excinfo:
        await harness.service.get_detail(
            base_id=_BASE_ID,
            document_id=_DOC_ID,
            segment_key="seg_0000000000000000",
            with_context=True,
            auth=_OTHER,
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN


async def test_get_detail_unknown_base_returns_11001() -> None:
    """库不存在 → 11001。"""
    harness = _Harness()
    with pytest.raises(ApiError) as excinfo:
        await harness.service.get_detail(
            base_id="kb_missing",
            document_id=_DOC_ID,
            segment_key="seg_0000000000000000",
            with_context=True,
            auth=_OWNER,
        )
    assert excinfo.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


def _completed_patch(run_id: str) -> DocumentProgressPatch:
    """构造「处理完成 + 新批次」的进度补丁。

    Args:
        run_id: 新批次号。

    Returns:
        DocumentProgressPatch: 进度补丁。
    """
    return DocumentProgressPatch(
        stage="completed",
        index_status="completed",
        progress=100,
        run_id=run_id,
    )
