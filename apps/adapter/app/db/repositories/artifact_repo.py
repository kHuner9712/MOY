# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""产物仓储与版本化写入（阶段三 B19，`architecture-phase3.md` §3.5 / WF2 / D-25）。

**WF2 版本化语义（三条硬约束）**：

1. **版本只增不删**：每次保存 `version + 1`，旧版本进 `version_history[]`；
   不设回退端点——"回退 = 再编辑一次"，少一个端点也少一条"回退是否改任务状态"的歧义。
2. **`content_sha256` 每次保存都重算**：否则前端缓存会拿到旧文件，而"保存成功但
   下载还是旧内容"是最难被用户描述清楚的一类 bug。
3. **`link` / `file` 只读**：允许改提交记录会直接毁掉 AC-02 的结果物核查客观性，
   所以可编辑类型在**仓储层**也再判一次（不只靠端点层）。

**二进制内容的取舍**（V1.0 明说）：对象存储在私有化路径上是可选项，因此
文件类产物的字节在内存后端保留、在 PG 后端只留 `storage_key` 与摘要；
`download_url` 一律由平台代理（`/files/artifacts/...`），**不暴露存储地址**。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.session import tenant_scope

logger = get_logger(__name__)

ARTIFACT_TABLE: Final[str] = "plugin_artifact"

#: 可编辑的产物类型（`link` / `file` 只读，D-25）
EDITABLE_TYPES: Final[frozenset[str]] = frozenset({"draft", "doc", "table", "text"})

#: 明确只读的产物类型（用于错误文案与负向断言）
READONLY_TYPES: Final[frozenset[str]] = frozenset({"link", "file"})

#: 版本历史上限（防单产物历史无限膨胀；超出后丢最旧的，只保留最近 N 版）
MAX_VERSION_HISTORY: Final[int] = 20


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ArtifactRecord:
    """产物行（`plugin_artifact` + WF2 版本字段）。

    Attributes:
        tenant_id: 企业 ID。
        artifact_id: 产物 ID（`art_` 前缀）。
        task_id: 任务 ID。
        step_id: 步骤 ID。
        output_key: 产物键（⊆ 声明 `outputs[].key`）。
        type: `text|link|file|table`。
        name: 产物名称（用户可见）。
        inline_text: 内联文本（≤32KB，已脱敏；`table` 为 CSV）。
        preview_text: 表格前 5 行预览。
        external_url: 外链（`link` 类型）。
        storage_key: 存储键（`file` 类型）。
        size_bytes: 大小。
        content_sha256: 内容摘要（每次保存重算）。
        version: 版本号（单调 +1，从 1 开始）。
        version_history: 历史版本（`[{version, created_at, content_sha256, inline_text}]`）。
        expires_at: 过期时间（180 天）。
        created_at: 创建时间。
        updated_at: 最近更新时间。
        content: 二进制内容（**仅内存后端**保留，PG 后端为 b""）。
        content_type: MIME 类型（下载响应用）。
    """

    tenant_id: str = ""
    artifact_id: str = ""
    task_id: str = ""
    step_id: str = ""
    output_key: str = ""
    type: str = "text"
    name: str = ""
    inline_text: str = ""
    preview_text: str = ""
    external_url: str = ""
    storage_key: str = ""
    size_bytes: int = 0
    content_sha256: str = ""
    version: int = 1
    version_history: list[dict[str, Any]] = field(default_factory=list)
    expires_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    content: bytes = b""
    content_type: str = "text/plain; charset=utf-8"

    @property
    def editable(self) -> bool:
        """是否可编辑（`draft` / `doc` / `table` / `text`）。

        Returns:
            bool: 可编辑返回 True。
        """
        return self.type in EDITABLE_TYPES


class ArtifactStore(Protocol):
    """产物存储契约（内存 / PostgreSQL 双实现）。"""

    async def add_artifacts(self, records: list[ArtifactRecord]) -> list[ArtifactRecord]:
        """批量插入产物。"""
        ...

    async def list_by_task(self, tenant_id: str, task_id: str) -> list[ArtifactRecord]:
        """列出任务全部产物。"""
        ...

    async def list_by_step(
        self, tenant_id: str, task_id: str, step_id: str
    ) -> list[ArtifactRecord]:
        """列出某步产物。"""
        ...

    async def get(self, tenant_id: str, artifact_id: str) -> ArtifactRecord | None:
        """读取单个产物。"""
        ...

    async def save_version(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        inline_text: str,
        preview_text: str,
        content_sha256: str,
        content: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRecord | None:
        """保存新版本（`version + 1`，旧版本进 `version_history`）。"""
        ...


class InMemoryArtifactStore:
    """进程内产物存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._items: dict[tuple[str, str], ArtifactRecord] = {}

    async def add_artifacts(self, records: list[ArtifactRecord]) -> list[ArtifactRecord]:
        """批量插入产物。

        Args:
            records: 产物行列表。

        Returns:
            list[ArtifactRecord]: 落库后的产物行。
        """
        created: list[ArtifactRecord] = []
        for record in records:
            payload = replace(
                record,
                created_at=record.created_at or now_iso(),
                updated_at=record.updated_at or now_iso(),
                expires_at=record.expires_at or _default_expires_at(),
            )
            self._items[(record.tenant_id, record.artifact_id)] = payload
            created.append(payload)
        return created

    async def list_by_task(self, tenant_id: str, task_id: str) -> list[ArtifactRecord]:
        """列出任务全部产物。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            list[ArtifactRecord]: 产物列表（按步骤与创建时间）。
        """
        items = [
            record
            for (tid, _), record in self._items.items()
            if tid == tenant_id and record.task_id == task_id
        ]
        items.sort(key=lambda item: (item.step_id, item.created_at, item.output_key))
        return items

    async def list_by_step(
        self, tenant_id: str, task_id: str, step_id: str
    ) -> list[ArtifactRecord]:
        """列出某步产物。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            step_id: 步骤 ID。

        Returns:
            list[ArtifactRecord]: 产物列表。
        """
        return [
            record
            for record in await self.list_by_task(tenant_id, task_id)
            if record.step_id == step_id
        ]

    async def get(self, tenant_id: str, artifact_id: str) -> ArtifactRecord | None:
        """读取单个产物。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID。

        Returns:
            ArtifactRecord | None: 产物行。
        """
        return self._items.get((tenant_id, artifact_id))

    async def save_version(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        inline_text: str,
        preview_text: str,
        content_sha256: str,
        content: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRecord | None:
        """保存新版本。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID。
            inline_text: 新版本正文（**已脱敏**）。
            preview_text: 新预览。
            content_sha256: 内容摘要（已重算）。
            content: 新文件字节。
            content_type: MIME 类型。

        Returns:
            ArtifactRecord | None: 更新后的产物行。
        """
        current = self._items.get((tenant_id, artifact_id))
        if current is None:
            return None
        history = list(current.version_history)
        history.append(
            {
                "version": current.version,
                "created_at": current.updated_at or current.created_at,
                "content_sha256": current.content_sha256,
                "inline_text": current.inline_text,
                "content_type": current.content_type,
            }
        )
        history = history[-MAX_VERSION_HISTORY:]
        updated = replace(
            current,
            inline_text=inline_text,
            preview_text=preview_text,
            content_sha256=content_sha256,
            content=content or inline_text.encode("utf-8"),
            content_type=content_type,
            size_bytes=len(content) if content else len(inline_text.encode("utf-8")),
            version=current.version + 1,
            version_history=history,
            updated_at=now_iso(),
        )
        self._items[(tenant_id, artifact_id)] = updated
        return updated

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._items.clear()


class _ArtifactRepository(BaseRepository):
    """产物表仓储基类。

    `table` **必须显式声明**：`BaseRepository.__init__` 在进入事务后第一件事就是
    `assert_identifier(table)`，空表名直接抛 `30003`——而这发生在任何 SQL 之前，
    所以漏声明时 `PostgresArtifactStore` 的**每个方法**都失败，现场只看到
    "系统繁忙，请稍后再试"（B21c 实测）。同 `plugin_repo` 的四个子类必须一致。
    """

    table = ARTIFACT_TABLE


class PostgresArtifactStore:
    """PostgreSQL 产物存储（生产实现）。

    `version` 与 `version_history` 两列为 WF2 新增（迁移 `0004`），
    老库未跑迁移时读取会回落默认值（`1` / `[]`），不会崩。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def add_artifacts(self, records: list[ArtifactRecord]) -> list[ArtifactRecord]:
        """批量插入产物。

        Args:
            records: 产物行列表。

        Returns:
            list[ArtifactRecord]: 落库后的产物行。
        """
        created: list[ArtifactRecord] = []
        for record in records:
            async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
                repo = _ArtifactRepository(uow)
                row = await repo.fetch_one(
                    f"""
                    INSERT INTO {ARTIFACT_TABLE} (
                        tenant_id, artifact_id, task_id, step_id, output_key, type, name,
                        inline_text, preview_text, external_url, storage_key, size_bytes,
                        content_sha256, expires_at
                    ) VALUES (
                        :tenant_id, :artifact_id, :task_id, :step_id, :output_key, :type, :name,
                        :inline_text, :preview_text, NULLIF(:external_url, ''),
                        NULLIF(:storage_key, ''), :size_bytes, NULLIF(:content_sha256, ''),
                        :expires_at
                    )
                    RETURNING *
                    """,
                    repo.params(
                        artifact_id=record.artifact_id,
                        task_id=record.task_id,
                        step_id=record.step_id,
                        output_key=record.output_key,
                        type=record.type,
                        name=record.name or record.output_key,
                        inline_text=record.inline_text,
                        preview_text=record.preview_text,
                        external_url=record.external_url,
                        storage_key=record.storage_key,
                        size_bytes=record.size_bytes,
                        content_sha256=record.content_sha256,
                        expires_at=as_datetime(record.expires_at)
                        or as_datetime(_default_expires_at()),
                    ),
                )
            if row is not None:
                created.append(_to_artifact(row))
        return created

    async def list_by_task(self, tenant_id: str, task_id: str) -> list[ArtifactRecord]:
        """列出任务全部产物。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            list[ArtifactRecord]: 产物列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ArtifactRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {ARTIFACT_TABLE}
                 WHERE tenant_id = :tenant_id AND task_id = :task_id
                 ORDER BY step_id, created_at, output_key
                """,
                repo.params(task_id=task_id),
            )
        return [_to_artifact(row) for row in rows]

    async def list_by_step(
        self, tenant_id: str, task_id: str, step_id: str
    ) -> list[ArtifactRecord]:
        """列出某步产物。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            step_id: 步骤 ID。

        Returns:
            list[ArtifactRecord]: 产物列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ArtifactRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {ARTIFACT_TABLE}
                 WHERE tenant_id = :tenant_id AND task_id = :task_id AND step_id = :step_id
                 ORDER BY created_at, output_key
                """,
                repo.params(task_id=task_id, step_id=step_id),
            )
        return [_to_artifact(row) for row in rows]

    async def get(self, tenant_id: str, artifact_id: str) -> ArtifactRecord | None:
        """读取单个产物。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID。

        Returns:
            ArtifactRecord | None: 产物行。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ArtifactRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT * FROM {ARTIFACT_TABLE}
                 WHERE tenant_id = :tenant_id AND artifact_id = :artifact_id
                """,
                repo.params(artifact_id=artifact_id),
            )
        return _to_artifact(row) if row is not None else None

    async def save_version(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        inline_text: str,
        preview_text: str,
        content_sha256: str,
        content: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRecord | None:
        """保存新版本（`version + 1`，旧版本压入 `version_history`）。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID。
            inline_text: 新版本正文（已脱敏）。
            preview_text: 新预览。
            content_sha256: 内容摘要。
            content: 新文件字节（PG 后端不落库，仅用于摘要与大小）。
            content_type: MIME 类型。

        Returns:
            ArtifactRecord | None: 更新后的产物行。
        """
        current = await self.get(tenant_id, artifact_id)
        if current is None:
            return None
        history = list(current.version_history)
        history.append(
            {
                "version": current.version,
                "created_at": current.updated_at or current.created_at,
                "content_sha256": current.content_sha256,
                "inline_text": current.inline_text,
                "content_type": current.content_type,
            }
        )
        history = history[-MAX_VERSION_HISTORY:]
        size = len(content) if content else len(inline_text.encode("utf-8"))
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ArtifactRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {ARTIFACT_TABLE}
                   SET inline_text = :inline_text,
                       preview_text = :preview_text,
                       content_sha256 = :content_sha256,
                       size_bytes = :size_bytes,
                       version = COALESCE(version, 1) + 1,
                       version_history = CAST(:version_history AS jsonb)
                 WHERE tenant_id = :tenant_id AND artifact_id = :artifact_id
                RETURNING *
                """,
                repo.params(
                    artifact_id=artifact_id,
                    inline_text=inline_text,
                    preview_text=preview_text,
                    content_sha256=content_sha256,
                    size_bytes=size,
                    version_history=json.dumps(history, ensure_ascii=False),
                ),
            )
        return _to_artifact(row) if row is not None else None


def assert_editable(record: ArtifactRecord) -> None:
    """校验产物可编辑（`link` / `file` 一律只读）。

    Args:
        record: 产物行。

    Raises:
        ApiError: 类型不可编辑（20001），文案说明"这类产物只能查看或下载"。
    """
    if record.editable:
        return
    raise ApiError(
        ErrorCode.PARAM_INVALID,
        message="这类产物只能查看或下载，不能修改",
        internal_detail=f"artifact type={record.type} is read-only",
    )


def _default_expires_at() -> str:
    """产物过期时间（180 天，Q3-5）。

    Returns:
        str: ISO 8601 时间戳。
    """
    return (datetime.now(UTC) + timedelta(days=180)).isoformat().replace("+00:00", "Z")


def _to_artifact(row: Mapping[str, Any]) -> ArtifactRecord:
    """把数据库行转成产物行。

    Args:
        row: 数据库行。

    Returns:
        ArtifactRecord: 产物行。
    """
    history = row.get("version_history")
    if isinstance(history, str):
        try:
            loaded = json.loads(history)
        except json.JSONDecodeError:  # pragma: no cover - 脏数据兜底
            loaded = []
        history_list = loaded if isinstance(loaded, list) else []
    elif isinstance(history, list):
        history_list = list(history)
    else:
        history_list = []
    return ArtifactRecord(
        tenant_id=str(row.get("tenant_id", "")),
        artifact_id=str(row.get("artifact_id", "")),
        task_id=str(row.get("task_id", "")),
        step_id=str(row.get("step_id", "")),
        output_key=str(row.get("output_key", "")),
        type=str(row.get("type", "text")),
        name=str(row.get("name", "") or ""),
        inline_text=str(row.get("inline_text", "") or ""),
        preview_text=str(row.get("preview_text", "") or ""),
        external_url=str(row.get("external_url", "") or ""),
        storage_key=str(row.get("storage_key", "") or ""),
        size_bytes=int(row.get("size_bytes", 0) or 0),
        content_sha256=str(row.get("content_sha256", "") or ""),
        version=int(row.get("version", 1) or 1),
        version_history=[item for item in history_list if isinstance(item, dict)],
        expires_at=_timestamp(row.get("expires_at")),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")) or _timestamp(row.get("created_at")),
        content_type=str(row.get("content_type", "") or "text/plain; charset=utf-8"),
    )


def _timestamp(value: Any) -> str:
    """把时间值转成 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 字符串（空值返回空串）。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


_store: ArtifactStore | None = None


def get_artifact_store(settings: Settings | None = None) -> ArtifactStore:
    """获取产物存储单例。

    Args:
        settings: 可选配置实例。

    Returns:
        ArtifactStore: 存储实现。
    """
    global _store
    effective = settings or get_settings()
    if _store is None:
        _store = (
            PostgresArtifactStore(effective)
            if effective.business_store == "postgres"
            else InMemoryArtifactStore()
        )
    return _store


def get_memory_artifact_store() -> InMemoryArtifactStore | None:
    """获取内存存储（测试清库用）。

    Returns:
        InMemoryArtifactStore | None: 内存实现；当前为 PG 时返回 None。
    """
    global _store
    if _store is None:
        _store = InMemoryArtifactStore()
    return _store if isinstance(_store, InMemoryArtifactStore) else None


def reset_artifact_store() -> None:
    """重置单例（测试隔离）。"""
    global _store
    _store = None


__all__: list[str] = [
    "ARTIFACT_TABLE",
    "EDITABLE_TYPES",
    "MAX_VERSION_HISTORY",
    "READONLY_TYPES",
    "ArtifactRecord",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "PostgresArtifactStore",
    "assert_editable",
    "get_artifact_store",
    "get_memory_artifact_store",
    "now_iso",
    "reset_artifact_store",
]
