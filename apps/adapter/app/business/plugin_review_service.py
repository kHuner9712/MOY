"""插件评价服务（阶段三 B18，`architecture-phase3.md` §5.2 PL11 / PL12）。

三条硬口径：

1. **用过才能评价**（`16011`）：未添加的插件不允许评价——否则市场会被未使用者的评分
   拉偏，且"评价"会变成匿名的公开留言板；
2. **一人一插件一条，重复提交覆盖**（PRD P0-P3-09 验收①）：保留 `review_id` 与
   `created_at`，只改 `rating/content/anonymous`；`uq_review_user` 兜底；
3. **均分服务端重算**：每次提交后把 `rating_avg/rating_count` 写回 `plugin_catalog`，
   列表与详情读的是同一份聚合值（不各自算，避免"卡片 4.6、详情 4.4"）。

**匿名只影响展示**：库里不存邮箱/手机/部门路径（§4.5 字段级不存在），
匿名的「某位同事」是渲染时替换，不是存一个假名字。
"""

from __future__ import annotations

from typing import Final

from app.business.audit_service import AuditService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.plugin_repo import PluginStore, get_plugin_store
from app.db.repositories.plugin_review_repo import (
    ReviewRecord,
    ReviewStore,
    get_review_store,
)
from app.schemas.plugin import ReviewData, ReviewItem, ReviewListData, ReviewRequest

logger = get_logger(__name__)

#: 评价 ID 前缀
REVIEW_ID_PREFIX: Final[str] = "rev_"

#: 匿名评价的展示名（PRD P0-P3-09：不暴露身份，也不编造姓名）
ANONYMOUS_DISPLAY_NAME: Final[str] = "某位同事"


class PluginReviewService:
    """插件评价服务（PL11 列表 + PL12 提交）。

    Attributes:
        _settings: 配置实例。
        _store: 插件存储（目录/安装，用于聚合写回与"用过才能评"判定）。
        _reviews: 评价存储。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: PluginStore | None = None,
        reviews: ReviewStore | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选插件存储（测试注入）。
            reviews: 可选评价存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: PluginStore = store or get_plugin_store(self._settings)
        self._reviews: ReviewStore = reviews or get_review_store(self._settings)
        self._audit = audit or AuditService(self._settings)

    async def list_reviews(
        self, *, plugin_id: str, auth: AuthContext, page: int = 1, page_size: int = 10
    ) -> ReviewListData:
        """评价列表（PL11，按租户隔离）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文（评价按企业隔离，不同企业互不可见）。
            page: 页码。
            page_size: 每页条数。

        Returns:
            ReviewListData: 评价列表与聚合分。
        """
        offset = max(page - 1, 0) * page_size
        records, total = await self._reviews.list_reviews(
            auth.tenant_id, plugin_id, offset=offset, limit=page_size
        )
        catalog = await self._store.get_catalog(plugin_id)
        items = [
            ReviewItem(
                review_id=record.review_id,
                rating=record.rating,
                content=record.content,
                author_name=(
                    ANONYMOUS_DISPLAY_NAME if record.anonymous else await self._display_name(record)
                ),
                department_name="",  # 部门路径不落库（§4.5），此处恒为空串
                created_at=record.created_at,
            )
            for record in records
        ]
        return ReviewListData(
            items=items,
            rating_avg=catalog.rating_avg if catalog is not None else 0.0,
            rating_count=catalog.rating_count if catalog is not None else 0,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if page_size else 0,
        )

    async def submit(
        self, *, plugin_id: str, request: ReviewRequest, auth: AuthContext
    ) -> ReviewData:
        """提交评价（PL12，重复提交覆盖）。

        Args:
            plugin_id: 插件标识。
            request: 评价内容。
            auth: 鉴权上下文。

        Returns:
            ReviewData: 提交结果与更新后的聚合分。

        Raises:
            ApiError: 未添加该插件（16011）、插件不存在（16002）。
        """
        catalog = await self._store.get_catalog(plugin_id)
        if catalog is None or not catalog.installable:
            raise ApiError(ErrorCode.PLUGIN_NOT_FOUND, internal_detail=f"plugin_id={plugin_id}")
        installation = await self._store.get_installation(auth.tenant_id, plugin_id)
        if installation is None or not installation.is_live:
            raise ApiError(
                ErrorCode.PLUGIN_REVIEW_NOT_ALLOWED,
                internal_detail=f"not installed: {plugin_id}",
            )
        record = await self._reviews.upsert_review(
            ReviewRecord(
                tenant_id=auth.tenant_id,
                review_id=new_resource_id(REVIEW_ID_PREFIX),
                plugin_id=plugin_id,
                user_id=auth.user_id,
                rating=request.rating,
                content=request.content,
                anonymous=request.anonymous,
            )
        )
        rating_avg, rating_count = await self._reviews.aggregate(auth.tenant_id, plugin_id)
        await self._store.set_catalog_rating(
            plugin_id, rating_avg=rating_avg, rating_count=rating_count
        )
        logger.info(
            "plugin_review_submitted",
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            rating=record.rating,
            rating_avg=rating_avg,
            rating_count=rating_count,
        )
        return ReviewData(
            review_id=record.review_id,
            rating=record.rating,
            created_at=record.created_at,
            rating_avg=rating_avg,
            rating_count=rating_count,
        )

    async def _display_name(self, record: ReviewRecord) -> str:
        """取评价人展示名（以用户 ID 兜底，不虚构姓名）。

        Args:
            record: 评价记录。

        Returns:
            str: 展示名。
        """
        return record.user_id


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: PluginReviewService | None = None


def get_plugin_review_service(settings: Settings | None = None) -> PluginReviewService:
    """获取评价服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        PluginReviewService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = PluginReviewService(settings)
    return _service


def reset_plugin_review_service() -> None:
    """重置评价服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "ANONYMOUS_DISPLAY_NAME",
    "REVIEW_ID_PREFIX",
    "PluginReviewService",
    "get_plugin_review_service",
    "reset_plugin_review_service",
]
