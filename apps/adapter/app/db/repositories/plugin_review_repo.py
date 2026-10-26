# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""插件评价仓储（阶段三 B18，`architecture-phase3.md` §4.2 `plugin_review`）。

**为什么单独一个仓储**：`plugin_repo.py` 已承载 5 张表、1300+ 行；评价是**独立聚合**
（一人一插件一条、重复提交即覆盖、评分均值需重算），读写模式与目录/安装完全不同。
继续往里塞会让那个文件既难读又难 review。表结构与口径（`uq_review_user`、
`匿名不落邮箱/手机/部门路径`）仍与架构一致。

**两条口径**：

- **一人一插件一条**（PRD P0-P3-09 验收①）：`(tenant_id, plugin_id, user_id)` 唯一，
  重复提交是**覆盖更新**（保留 `review_id` 与 `created_at`，只改 `rating/content/anonymous`）；
- **匿名只要展示匿名**：库里**不存** `user_email` / `user_phone` / `department_path`
  （§4.5 字段级不存在），匿名的"某位同事"由服务层渲染，不是存一个假名字。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.repositories.plugin import now_iso
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 评价表名
REVIEW_TABLE: Final[str] = "plugin_review"


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """评价行。

    Attributes:
        tenant_id: 企业 ID。
        review_id: 评价 ID（`rev_`）。
        plugin_id: 插件标识。
        user_id: 评价人。
        rating: 评分（1-5）。
        content: 评价内容（≤200 字）。
        anonymous: 是否匿名展示。
        created_at: 首次提交时间。
        updated_at: 最近更新时间。
    """

    tenant_id: str = ""
    review_id: str = ""
    plugin_id: str = ""
    user_id: str = ""
    rating: int = 5
    content: str = ""
    anonymous: bool = False
    created_at: str = ""
    updated_at: str = ""


class ReviewStore(Protocol):
    """评价存储契约（内存 / PostgreSQL 双实现）。"""

    async def get_review(self, tenant_id: str, plugin_id: str, user_id: str) -> ReviewRecord | None:
        """读取某成员在某插件上的评价。"""
        ...

    async def upsert_review(self, record: ReviewRecord) -> ReviewRecord:
        """写入或覆盖评价。"""
        ...

    async def list_reviews(
        self, tenant_id: str, plugin_id: str, *, offset: int = 0, limit: int = 10
    ) -> tuple[list[ReviewRecord], int]:
        """分页列出评价与总条数。"""
        ...

    async def aggregate(self, tenant_id: str, plugin_id: str) -> tuple[float, int]:
        """计算（评分均值, 条数）。"""
        ...


@dataclass
class InMemoryReviewStore:
    """进程内评价存储（development/test）。

    Attributes:
        _rows: `(tenant_id, review_id) → 评价`。
    """

    _rows: dict[tuple[str, str], ReviewRecord] = field(default_factory=dict)

    async def get_review(self, tenant_id: str, plugin_id: str, user_id: str) -> ReviewRecord | None:
        """读取某成员在某插件上的评价。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。

        Returns:
            ReviewRecord | None: 评价；未提交返回 None。
        """
        for record in self._rows.values():
            if (
                record.tenant_id == tenant_id
                and record.plugin_id == plugin_id
                and record.user_id == user_id
            ):
                return record
        return None

    async def upsert_review(self, record: ReviewRecord) -> ReviewRecord:
        """写入或覆盖评价（保留 `review_id` 与 `created_at`）。

        Args:
            record: 评价行。

        Returns:
            ReviewRecord: 落库后的评价。
        """
        stamp = now_iso()
        existing = await self.get_review(record.tenant_id, record.plugin_id, record.user_id)
        payload = ReviewRecord(
            tenant_id=record.tenant_id,
            review_id=existing.review_id if existing is not None else record.review_id,
            plugin_id=record.plugin_id,
            user_id=record.user_id,
            rating=record.rating,
            content=record.content,
            anonymous=record.anonymous,
            created_at=existing.created_at if existing is not None else stamp,
            updated_at=stamp,
        )
        self._rows[(payload.tenant_id, payload.review_id)] = payload
        return payload

    async def list_reviews(
        self, tenant_id: str, plugin_id: str, *, offset: int = 0, limit: int = 10
    ) -> tuple[list[ReviewRecord], int]:
        """分页列出评价（新→旧）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[ReviewRecord], int]: 当前页与总条数。
        """
        items = [
            record
            for record in self._rows.values()
            if record.tenant_id == tenant_id and record.plugin_id == plugin_id
        ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        start = max(offset, 0)
        return items[start : start + limit], len(items)

    async def aggregate(self, tenant_id: str, plugin_id: str) -> tuple[float, int]:
        """计算评分均值与条数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            tuple[float, int]: （均值, 条数）。
        """
        ratings = [
            record.rating
            for record in self._rows.values()
            if record.tenant_id == tenant_id and record.plugin_id == plugin_id
        ]
        if not ratings:
            return 0.0, 0
        return round(sum(ratings) / len(ratings), 1), len(ratings)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._rows.clear()


class _ReviewTableRepository(BaseRepository):
    """`plugin_review` 表仓储。"""

    table = REVIEW_TABLE


class PostgresReviewStore:
    """PostgreSQL 评价存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def get_review(self, tenant_id: str, plugin_id: str, user_id: str) -> ReviewRecord | None:
        """读取某成员在某插件上的评价。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。

        Returns:
            ReviewRecord | None: 评价；未提交返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ReviewTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, review_id, plugin_id, user_id, rating, content,
                       anonymous, created_at, updated_at
                FROM {REVIEW_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id AND user_id = :user_id
                """,
                repo.params(plugin_id=plugin_id, user_id=user_id),
            )
        return _to_review(row) if row is not None else None

    async def upsert_review(self, record: ReviewRecord) -> ReviewRecord:
        """写入或覆盖评价（`uq_review_user` 兜底）。

        Args:
            record: 评价行。

        Returns:
            ReviewRecord: 落库后的评价。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _ReviewTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {REVIEW_TABLE} (
                    tenant_id, review_id, plugin_id, user_id, rating, content, anonymous
                ) VALUES (
                    :tenant_id, :review_id, :plugin_id, :user_id, :rating, :content, :anonymous
                )
                ON CONFLICT (tenant_id, plugin_id, user_id) DO UPDATE SET
                    rating = EXCLUDED.rating,
                    content = EXCLUDED.content,
                    anonymous = EXCLUDED.anonymous,
                    updated_at = now()
                RETURNING tenant_id, review_id, plugin_id, user_id, rating, content,
                          anonymous, created_at, updated_at
                """,
                repo.params(
                    review_id=record.review_id,
                    plugin_id=record.plugin_id,
                    user_id=record.user_id,
                    rating=record.rating,
                    content=record.content,
                    anonymous=record.anonymous,
                ),
            )
        if row is None:  # pragma: no cover - RETURNING 缺失属数据库异常
            from app.core.errors import ApiError, ErrorCode

            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="review upsert lost")
        return _to_review(row)

    async def list_reviews(
        self, tenant_id: str, plugin_id: str, *, offset: int = 0, limit: int = 10
    ) -> tuple[list[ReviewRecord], int]:
        """分页列出评价。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[ReviewRecord], int]: 当前页与总条数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ReviewTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, review_id, plugin_id, user_id, rating, content,
                       anonymous, created_at, updated_at
                FROM {REVIEW_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(plugin_id=plugin_id, limit=limit, offset=max(offset, 0)),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {REVIEW_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                """,
                repo.params(plugin_id=plugin_id),
            )
        return [_to_review(row) for row in rows], int(total or 0)

    async def aggregate(self, tenant_id: str, plugin_id: str) -> tuple[float, int]:
        """计算评分均值与条数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            tuple[float, int]: （均值, 条数）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ReviewTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT COALESCE(AVG(rating), 0) AS avg_rating, COUNT(*) AS total
                FROM {REVIEW_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                """,
                repo.params(plugin_id=plugin_id),
            )
        if row is None:  # pragma: no cover - 聚合查询必有行
            return 0.0, 0
        return round(float(row.get("avg_rating", 0) or 0), 1), int(row.get("total", 0) or 0)


def _to_review(row: Mapping[str, Any]) -> ReviewRecord:
    """把数据库行映射为评价记录。

    Args:
        row: 行映射。

    Returns:
        ReviewRecord: 评价记录。
    """
    from app.db.repositories.plugin.postgres import _timestamp

    return ReviewRecord(
        tenant_id=str(row.get("tenant_id", "")),
        review_id=str(row.get("review_id", "")),
        plugin_id=str(row.get("plugin_id", "")),
        user_id=str(row.get("user_id", "")),
        rating=int(row.get("rating", 5) or 5),
        content=str(row.get("content", "") or ""),
        anonymous=bool(row.get("anonymous", False)),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
    )


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryReviewStore | None = None


def get_review_store(settings: Settings | None = None) -> ReviewStore:
    """获取评价存储。

    Args:
        settings: 可选配置实例。

    Returns:
        ReviewStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresReviewStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryReviewStore()
    return _memory_store


def reset_review_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "REVIEW_TABLE",
    "InMemoryReviewStore",
    "PostgresReviewStore",
    "ReviewRecord",
    "ReviewStore",
    "get_review_store",
    "reset_review_store",
]
