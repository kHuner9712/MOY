# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""产物仓储与版本化写入（阶段三 B19，`architecture-phase3.md` §3.5 / WF2 / D-25）。

**WF2 版本化语义（三条硬约束）**：

1. **版本只增不删**：每次保存 `version + 1`，旧版本进 `version_history[]`；
   不设回退端点——"回退 = 再编辑一次"，少一个端点也少一条"回退是否改任务状态"的歧义。
2. **`content_sha256` 每次保存都重算**：否则前端缓存会拿到旧文件，而"保存成功但
   下载还是旧内容"是最难被用户描述清楚的一类 bug。
3. **`link` / `file` 只读**：允许改提交记录会直接毁掉 AC-02 的结果物核查客观性，
   所以可编辑类型在**仓储层**也再判一次（不只靠端点层）。

**二进制内容的落点**（B22 修正，原设计见下）：
文件类产物（docx / xlsx / zip）的字节**两种后端都保留**——内存后端存在
`ArtifactRecord.content`，PG 后端存进 `plugin_artifact.content`（BYTEA，迁移 `0005`）。
对象存储在私有化路径上是可选项，因此不能把"二进制能否落库"挂在它上面；
`download_url` 仍一律由平台代理（`/files/artifacts/...`），**不暴露存储地址**。

> 原设计（已废弃）：字节只在内存后端保留、PG 后端只留 `storage_key` 与摘要。
> 后果是 PG（私有化）模式下下载任何文件类产物拿到的都是 `inline_text` 的文本预览，
> 而不是真文件——"内存全绿、生产全废"（缺陷 P3-B22-03）。**不要把这个取舍改回去。**
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

#: 扩展名 → MIME（与 `plugins/renderer.py::RENDER_CONTENT_TYPES` **必须一致**）。
#:
#: 为什么在仓储层再写一份：渲染器只管"它自己产出的文件"，而仓储层收到的
#: `ArtifactRecord` 还可能来自插件声明（`link`/`file`）与历史行。落库前统一
#: 归一化，才能保证"库里存的 MIME 与下载响应声明的 MIME"永远同源（P3-B22-04）。
EXTENSION_CONTENT_TYPES: Final[dict[str, str]] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "json": "application/json",
    "zip": "application/zip",
}

#: 兜底 MIME（无法判定扩展名时）
DEFAULT_CONTENT_TYPE: Final[str] = "text/plain; charset=utf-8"

#: 允许采信调用方**声明**的 MIME 白名单（仅在扩展名与产物类型都判不出时生效）。
#:
#: 为什么需要白名单（2026-11-02 审计 S-02）：原实现是「`declared` 非
#: `application/octet-stream` 就原样采用」，等于把 MIME 决定权交给插件声明。
#: 插件（租户管理员安装，非完全可信）若声明 `text/html` 并把产物内容写成 HTML/JS，
#: 用户点下载就能在**平台同源**下执行脚本——配合令牌存 localStorage 构成完整的
#: 令牌窃取链。下载链路的 MIME 必须由服务端收口，不能透传声明值。
#:
#: 白名单只收「浏览器不会当脚本执行」的类型。**刻意排除**：
#: `text/html`、`application/xhtml+xml`、`image/svg+xml`（SVG 可内嵌 `<script>`）、
#: `text/javascript`、`application/javascript`——这些一律降级为
#: `DEFAULT_CONTENT_TYPE`，配合下载端点下发的 `nosniff` 与 `sandbox` 兜住。
SAFE_DECLARED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/zip",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/csv",
        "text/markdown",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)


def is_safe_declared_content_type(declared: str) -> bool:
    """判断调用方声明的 MIME 是否可被服务端采信。

    按**基础类型**（分号前的部分）比对，因此 `text/csv; charset=utf-8`
    这类带参数的写法同样通过；大小写不敏感。

    Args:
        declared: 调用方声明的 MIME（可为空）。

    Returns:
        bool: 在白名单内返回 True。
    """
    base = declared.split(";", 1)[0].strip().lower()
    return bool(base) and base in SAFE_DECLARED_CONTENT_TYPES


def resolve_content_type(name: str, artifact_type: str, declared: str) -> str:
    """归一化产物的 MIME（P3-B22-04）。

    优先级：**扩展名 > 产物类型 > 调用方声明（白名单）> 兜底**。

    为什么扩展名优先：调用方（任务服务）在"用户编辑后重新渲染"路径上会把
    上个版本的 `content_type` 带过来，而本次渲染可能换了格式（md → docx）。
    以最终文件名后缀为准，才能保证「库里的 MIME 与真实字节的格式一致」——
    这正是当初下载 docx 拿到 `text/plain` 的根因。

    为什么声明值要过白名单：见 `SAFE_DECLARED_CONTENT_TYPES` 的说明——
    MIME 由服务端收口，不能把 `text/html` / `image/svg+xml` 这类可执行类型
    透传给浏览器。

    Args:
        name: 产物名称（含扩展名）。
        artifact_type: 产物类型（`text|link|file|table`）。
        declared: 调用方声明的 MIME（可为空）。

    Returns:
        str: 归一化后的 MIME。
    """
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in EXTENSION_CONTENT_TYPES:
        return EXTENSION_CONTENT_TYPES[suffix]
    if artifact_type == "table":
        # 表格统一以 CSV 落盘（`inline_text` 就是 CSV 文本）
        return EXTENSION_CONTENT_TYPES["csv"]
    text = (declared or "").strip()
    if is_safe_declared_content_type(text):
        return text
    return DEFAULT_CONTENT_TYPE


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
        content: 二进制内容（`file` 类产物的真实字节；PG 后端落 `plugin_artifact.content`）。
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
                # 与 PG 后端同一套归一化规则：两个后端产出的 MIME 必须一致，
                # 否则"内存全绿、生产全废"会从字节问题变成类型问题（0006）
                content_type=resolve_content_type(
                    record.name or record.output_key, record.type, record.content_type
                ),
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
            content_type=resolve_content_type(
                current.name or current.output_key, current.type, content_type
            ),
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

        `content` 与 `content_sha256` 一起落库：摘要负责"内容变没变"，
        `content` 负责"下载拿得到什么"。两者**不得**互相替代（B22）。

        Args:
            records: 产物行列表。

        Returns:
            list[ArtifactRecord]: 落库后的产物行。
        """
        created: dict[int, ArtifactRecord] = {}
        groups: dict[str, list[tuple[int, ArtifactRecord]]] = {}
        for index, record in enumerate(records):
            groups.setdefault(record.tenant_id, []).append((index, record))
        # 审计 2026-11-03 QA-05：同一租户的产物在**一个事务**里写完。
        # 旧实现每条产物都进出一次 `tenant_scope`（各自提交），一次工具执行产出
        # 十几个文件就是十几个事务；中途失败还会留下"前几个已落库、后面没有"的
        # 半批产物。租户维度分组是因为 `tenant_scope` 与 RLS 会话变量绑定到单个租户。
        for tenant_id, entries in groups.items():
            async with tenant_scope(tenant_id, settings=self._settings) as uow:
                repo = _ArtifactRepository(uow)
                for index, record in entries:
                    # 落库前归一化 MIME：库里存的类型必须与真实字节的格式一致（0006）
                    content_type = resolve_content_type(
                        record.name or record.output_key, record.type, record.content_type
                    )
                    row = await repo.fetch_one(
                        f"""
                        INSERT INTO {ARTIFACT_TABLE} (
                            tenant_id, artifact_id, task_id, step_id, output_key, type, name,
                            inline_text, preview_text, external_url, storage_key, size_bytes,
                            content_sha256, content, content_type, expires_at
                        ) VALUES (
                            :tenant_id, :artifact_id, :task_id, :step_id, :output_key, :type, :name,
                            :inline_text, :preview_text, NULLIF(:external_url, ''),
                            NULLIF(:storage_key, ''), :size_bytes, NULLIF(:content_sha256, ''),
                            :content, :content_type, :expires_at
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
                            # 空字节写 NULL 而不是 `''::bytea`：`text` / `link` 类产物
                            # 本来就没有二进制内容，"没有"与"零字节文件"要能区分开。
                            content=record.content or None,
                            content_type=content_type,
                            expires_at=as_datetime(record.expires_at)
                            or as_datetime(_default_expires_at()),
                        ),
                    )
                    if row is not None:
                        created[index] = _to_artifact(row)
        # 按入参顺序返回（跨租户分组不改变调用方看到的顺序）
        return [created[index] for index in sorted(created)]

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

        **`content_sha256` 每次保存都重算**（硬约束第 2 条）：本方法只负责存值，
        不负责算摘要——调用方（`task_service`）用 `sanitizer.digest()` 重算后传入。
        这里绝不允许"内容没变就沿用旧摘要"的优化，否则前端缓存会拿到旧文件。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID。
            inline_text: 新版本正文（已脱敏）。
            preview_text: 新预览。
            content_sha256: 内容摘要（调用方已重算）。
            content: 新文件字节；空则按内存后端同语义回落到 `inline_text` 的 UTF-8 字节。
            content_type: MIME 类型（当前 PG 表未持久化该列，见模块说明）。

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
        # 与内存实现 `content or inline_text.encode("utf-8")` **严格同语义**：
        # 调用方没给字节时，新版本的内容就是新正文的 UTF-8 字节。
        # 在 Python 侧算好再绑定，不在 SQL 里写 COALESCE/convert_to——
        # 参数类型推断交给 asyncpg 的 bytea 上下文，比让 PG 去解 COALESCE 更稳。
        effective_content = content or inline_text.encode("utf-8")
        size = len(effective_content)
        # 同一产物换格式重渲染时，MIME 必须跟着变（名称后缀优先）
        effective_type = resolve_content_type(
            current.name or current.output_key, current.type, content_type
        )
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ArtifactRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {ARTIFACT_TABLE}
                   SET inline_text = :inline_text,
                       preview_text = :preview_text,
                       content_sha256 = :content_sha256,
                       content = :content,
                       content_type = :content_type,
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
                    content=effective_content,
                    content_type=effective_type,
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
        content=_as_bytes(row.get("content")),
        # 老库（未跑 0006）读回来是 None：按名称/类型归一化，
        # 而不是无条件声明 text/plain——那正是 P3-B22-04 的现场
        content_type=resolve_content_type(
            str(row.get("name", "") or row.get("output_key", "")),
            str(row.get("type", "text")),
            str(row.get("content_type", "") or ""),
        ),
    )


def _as_bytes(value: Any) -> bytes:
    """把 `bytea` 列读回来的值规范成 `bytes`。

    为什么单独一个函数（而不是 `bytes(row.get("content") or b"")`）：

    - 列可能是 `NULL`（`text` / `link` 类产物本来就没有二进制内容）→ 返回 `b""`，
      调用方 `if not content` 的回落分支才成立；
    - asyncpg 在某些版本/驱动组合下把 `bytea` 读成 `memoryview`，
      而 `memoryview or b""` 的真值判断与后续 `len()` / `startswith` 行为不一致；
    - 老库未跑 `0005` 时行里**没有** `content` 这个键 → `row.get` 返回 `None`，
      同样落到 `b""`，不会因为缺列把整条读取链路炸掉（降级行为与 B22 之前一致）。
    - `bytearray` 一并收掉：`bytes(bytearray)` 是拷贝，不会与行对象共享可变缓冲。

    Args:
        value: 数据库行的 `content` 值。

    Returns:
        bytes: 二进制内容；空值返回 `b""`。
    """
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return b""


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
