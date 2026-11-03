"""租户数据权利服务（阶段四 T4-05.3，架构 §4.2 TD1/TD2 / PRD P4-05 ③④）。

**为什么导出是同步的**（本批最关键的一个决策，PRD 原写"异步任务"）：

1. **端点预算里没有"查导出进度"的位置**。阶段四只允许 10 个新端点（架构 §4.1 第 3 条），
   10 条里 MC1-MC4、MM1-MM4、TD1、TD2 已经占满。异步导出必须配一个
   `GET /ops/tenant-data/export/{id}` 才能拿到结果——加它就是第 11 条，
   门禁 G1/G3 会当场判红；
2. **没有查询端点时的异步导出比同步更糟**：任务排进 `background_job`、包也生成了，
   但用户没有任何合法手段拿到 `download_url`（下载令牌是短期凭证，不能靠"通知"送达）。
   那就是"排了队但取不到"，等于数据权利没兑现；
3. **量级可接受**：私有化与中小企业场景（单租户 ≤ 200,000 行）在内存里拼 zip 是
   秒级操作。超过 200,000 行**明确报错**（`数据量较大，请联系管理员分批导出`），
   而不是静默截断——截断的导出包会让客户以为"我的数据只有这些"，
   在合规场景里比报错严重得多。

> 因此 `kind=tenant_export` 保留在 `background_job.kind` 的 CHECK 里（迁移 0010 已冻结），
> 但**没有任何注册的处理器**。这是刻意的：队列里不会出现该类任务，
> 而将来若要改成异步，处理器位置现成。已在 `docs/phase4-batch-log.md` 披露。

**删除为什么要"先停用、再分批硬删、最后出证明"**：

- 先 `tenant.status='suspended'`：阻断后续写入。不先阻断就会"边删边写"，
  删完还剩新数据，客户拿到的"已删除"结论是假的；
- 分批（每批 ≤ 1000 行）避免长锁，理由同 `retention_cleanup`；
- 出**删除证明**（各表行数 + 时间戳，作为产物落库）：客户退租时需要能自证
  "已删除"，口头说明/邮件回复不具备可核查性；
- **审计行删不掉，且不许合并进总数**：应用角色对 `audit_log` 无 `DELETE` 权限
  （`init-app-role.sql` 第 4 节）。响应与证明里单列 `audit_pending_dba: N`
  + DBA SQL 提示，这正是 PRD P4-05 ④"分两段呈现、不得合并成一个数字"的要求。

**私有化口径**（PRD P4-05 ⑤）：单租户部署下"导出/删除"实质是"全库导出/清空"，
由 DBA 执行脚本完成，**不对终端用户开放**（终端用户点一下就把整个库清了，
这是事故不是功能）。服务层直接返回业务错误，脚本见 `sql/full-export.sql` /
`sql/full-purge.sql`。
"""

# ruff: noqa: S608 - 表名/列名均为模块常量，值一律命名参数绑定
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from app.core.artifact_token import make_token
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.tenant_data import EXPORT_SCOPES as _EXPORT_SCOPES
from app.db.repositories.artifact_repo import ArtifactRecord, ArtifactStore, get_artifact_store
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 导出可选的类别（架构 §4.2 TD1：`scope[]{audit,usage,chat,memory,artifact}`）
#:
#: 定义已下沉 `app.core.tenant_data`（审计 P1-5）：请求模型
#: `schemas/tenant_data.py` 的默认值要用它，留在业务层会让 DTO 反向依赖服务实现。
#: 这里**原样 re-export**（连同 `__all__`），既有调用方 import 路径零改动。
EXPORT_SCOPES: Final[tuple[str, ...]] = _EXPORT_SCOPES

#: 导出包内各文件的固定名（**顺序即清单顺序**，便于人工核对）
EXPORT_FILE_NAMES: Final[dict[str, str]] = {
    "audit": "audit.jsonl",
    "usage": "usage.jsonl",
    "chat": "chat.jsonl",
    "memory": "memory.jsonl",
    "artifact": "artifact.jsonl",
}

#: 清单文件名
MANIFEST_NAME: Final[str] = "manifest.json"

#: 导出总行数上限（超过即报错，**不截断**）
MAX_EXPORT_ROWS: Final[int] = 200_000

#: 单页读取行数（同时是单批删除行数）
PAGE_SIZE: Final[int] = 1000

#: zip 写入的**攒批行数**（审计 P2-5）。
#:
#: 为什么攒批：`ZipFile.open()` 返回的句柄上，`write()` 是**同步阻塞**调用
#: （deflate 压缩 + 落盘），在 `async for` 的循环体里直接调它会把事件循环卡住
#: ——20 万行的导出期间，同一进程的所有请求（含健康检查）一起停摆。
#: 现在攒够 `_ZIP_CHUNK_ROWS` 行就 `await asyncio.to_thread(handle.write, ...)`
#: 交给线程池：单次跨线程开销被摊到 500 行上（每次约几十 KB），既不让循环
#: 每行 switch 一次，也不让循环被压缩卡住。
_ZIP_CHUNK_ROWS: Final[int] = 500

#: ZIP 打包缓冲在内存里的上限（审计 2026-11-03 QA-06）。
#:
#: 超过这个大小就由 `SpooledTemporaryFile` 自动溢写到磁盘，避免"压缩过程"
#: 随导出包体积线性吃内存。最终写库仍需一份完整 `bytes`（存储契约是 `bytea`），
#: 但峰值从"两份"降到"一份"。
_ZIP_SPOOL_MAX_BYTES: Final[int] = 8 * 1024 * 1024

#: 导出超过上限时的业务文案（架构 §4.2：复用既有码，不新造 18xxx）
EXPORT_TOO_LARGE_MESSAGE: Final[str] = "数据量较大，请联系管理员分批导出"

#: 私有化模式下的文案（PRD P4-05 ⑤）
PRIVATE_MODE_MESSAGE: Final[str] = "当前部署形态不支持在线导出或删除，请联系运维执行全库脚本"

#: 产物下载地址前缀（与 `api/v1/artifact_files.py` 的路由前缀**逐字一致**）。
#:
#: 不用 `Settings.plugin_artifact_download_prefix`：那一项是阶段三给插件产物准备的
#: `/api/v1/agent/artifacts`（历史值，当前全仓无读取点）。照抄会让响应里的地址 404。
ARTIFACT_DOWNLOAD_PREFIX: Final[str] = "/files/artifacts"

#: 导出包与删除证明的"宿主任务"标识。
#:
#: `plugin_artifact` 上有 `fk_artifact_task (tenant_id, task_id) → plugin_task`，
#: **且 `task_id` 是 NOT NULL** —— 产物必须挂在一条真实任务下才能落库。
#: 因此数据权利的产物也建一条 `plugin_task` 记录（`plugin_id` 用一个通用系统值，
#: 状态直接 `completed`）。这样做的副作用要说清楚：
#: **TD2 删完之后 `plugin_task` / `plugin_artifact` 各剩 1 行，就是删除证明本身**——
#: 证明是"已删除"这一事实的唯一载体，连它一起删掉客户就再也拿不到证据了。
HOST_PLUGIN_ID: Final[str] = "plg_system_data_rights"
HOST_PLUGIN_VERSION: Final[str] = "1.0.0"

#: 产物用途标记（同时写进产物 `output_key` 与宿主任务的 `result_summary`）。
#:
#: 为什么需要它：宿主任务行与产物行都是"系统为了满足外键而留下的最小记录"，
#: 光看 `plugin_id` 分不出这次是导出还是删除证明；有了用途标记，
#: 运维查库时能一眼看出"这条行是证明，不是残留业务数据"。
PURPOSE_EXPORT: Final[str] = "tenant_data_export"
PURPOSE_DELETION_PROOF: Final[str] = "proof_of_deletion"

#: 删除范围 → 表清单（顺序即删除顺序：先删"子"，再删"父"）
DELETE_SCOPE_TABLES: Final[dict[str, tuple[str, ...]]] = {
    "audit": ("audit_log",),
    "usage": (
        "usage_event",
        "usage_monthly",
        "tool_execution",
        "payment_callback_log",
        "integration_outbox",
    ),
    "chat": ("message", "conversation"),
    "memory": ("memory_embedding", "user_memory", "user_memory_setting"),
    "artifact": ("plugin_artifact", "plugin_task_step", "plugin_task"),
}

#: **无论 `scope` 选什么都要删**的表：凭据是密钥材料（留着既无用也是风险），
#: `background_job` 是纯运行态（留着可能让处理器去处理已删除的会话）。
ALWAYS_DELETE_TABLES: Final[tuple[str, ...]] = ("background_job", "credential_store")

#: **不纳入在线删除**的表（知识库与文档正文属大对象存储，需 DBA 全库脚本）
RETAINED_TABLES: Final[tuple[str, ...]] = (
    "document_meta",
    "knowledge_base_meta",
    "knowledge_base_grant",
    "segment_map",
)

#: 表的中文名（证明文件与日志用）
TABLE_LABELS: Final[dict[str, str]] = {
    "audit_log": "操作记录",
    "usage_event": "用量事件",
    "usage_monthly": "用量月度汇总",
    "tool_execution": "工具调用记录",
    "payment_callback_log": "支付回调流水",
    "integration_outbox": "集成出站队列",
    "message": "消息",
    "conversation": "会话",
    "memory_embedding": "记忆派生数据",
    "user_memory": "记忆",
    "user_memory_setting": "记忆开关",
    "plugin_artifact": "产物",
    "plugin_task_step": "任务步骤",
    "plugin_task": "插件任务",
    "background_job": "后台任务",
    "credential_store": "外部系统凭据",
}

#: 删除证明的 SQL 提示（响应与证明文件回显，运维不必翻文档）
AUDIT_SQL_HINT: Final[str] = (
    "DELETE FROM audit_log WHERE tenant_id = '<tenant_id>';"
    "  -- 以库属主（officeai）执行，见 sql/retention-audit.sql"
)


def _json_default(value: Any) -> Any:
    """把非 JSON 原生类型转成可序列化形式（时间统一 ISO 8601 UTC）。

    Args:
        value: 任意值。

    Returns:
        Any: 可 JSON 序列化的值。
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # 二进制**不进导出包**（体积不可控且无法审阅）；只留摘要
        return hashlib.sha256(bytes(value)).hexdigest()
    return str(value)


def dump_line(payload: Mapping[str, Any]) -> str:
    """把一行记录序列化成 JSONL 的一行（键排序，便于 diff 与 sha256 复算）。

    Args:
        payload: 记录。

    Returns:
        str: 单行 JSON（不含换行符）。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=_json_default,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ExportSource:
    """一个 JSONL 文件的取数定义（键集分页）。

    Attributes:
        scope: 所属类别。
        table: 表名。
        timestamp_column: 排序/游标的时间列（**必须 NOT NULL**）。
        id_column: 次序键（同秒多条时保证稳定顺序）。
        id_cast: 游标键的 SQL 类型（`text` / `bigint`）。
        columns: 导出列。
        record: 行标签（同一文件里混多表时用于区分）；空串表示不写该字段。
    """

    scope: str
    table: str
    timestamp_column: str
    id_column: str
    columns: tuple[str, ...]
    id_cast: str = "text"
    record: str = ""

    @property
    def file_name(self) -> str:
        """文件名。

        Returns:
            str: 以类别映射出的 JSONL 文件名。
        """
        return EXPORT_FILE_NAMES[self.scope]


#: 导出取数清单（顺序 = 写入顺序 = 清单顺序）。
#:
#: 逐表说明为什么是这些列：
#: - `audit`：只给"谁在什么时候做了什么"，`change_summary` 一起导出（客户有权知道
#:   自己那条操作记录里到底写了什么）；
#: - `usage`：四列 token 维度一起导出（阶段四 0007 加列），否则客户无法复算账单；
#: - `chat`：`conversation` 与 `message` 进同一个文件，行首 `record` 区分；
#: - `memory`：`content` 是用户可见的那部分；派生数据（向量）与开关不导出，
#:   理由见 `manifest.json` 的 `notes`；
#: - `artifact`：**只导出清单**（名称/摘要/大小/过期时间），不含二进制：
#:   二进制走既有 `/files/artifacts/{token}` 单件下载，塞进 zip 会让包体积不可控。
EXPORT_SOURCES: Final[tuple[ExportSource, ...]] = (
    ExportSource(
        scope="audit",
        table="audit_log",
        timestamp_column="occurred_at",
        id_column="id",
        id_cast="bigint",
        columns=(
            "id",
            "actor_id",
            "actor_role",
            "action",
            "action_display_name",
            "resource_type",
            "resource_id",
            "result",
            "change_summary",
            "ip_masked",
            "trace_id",
            "occurred_at",
        ),
    ),
    ExportSource(
        scope="usage",
        table="usage_event",
        timestamp_column="occurred_at",
        id_column="event_id",
        columns=(
            "event_id",
            "org_id",
            "department_id",
            "user_id",
            "capability",
            "resource_type",
            "resource_id",
            "units",
            "period",
            "source",
            "profile",
            "provider",
            "prompt_tokens",
            "completion_tokens",
            "occurred_at",
        ),
    ),
    ExportSource(
        scope="chat",
        table="conversation",
        timestamp_column="created_at",
        id_column="conversation_id",
        columns=(
            "conversation_id",
            "user_id",
            "title",
            "archived",
            "message_count",
            "last_message_at",
            "created_at",
            "updated_at",
            "deleted_at",
        ),
        record="conversation",
    ),
    ExportSource(
        scope="chat",
        table="message",
        timestamp_column="created_at",
        id_column="message_id",
        columns=(
            "message_id",
            "conversation_id",
            "user_id",
            "role",
            "content",
            "status",
            "truncated",
            "profile",
            "created_at",
        ),
        record="message",
    ),
    ExportSource(
        scope="memory",
        table="user_memory",
        timestamp_column="created_at",
        id_column="memory_id",
        columns=(
            "memory_id",
            "user_id",
            "category",
            "content",
            "confidence",
            "source_conversation_id",
            "superseded_by",
            "hit_count",
            "created_at",
            "updated_at",
        ),
    ),
    ExportSource(
        scope="artifact",
        table="plugin_artifact",
        timestamp_column="created_at",
        id_column="artifact_id",
        columns=(
            "artifact_id",
            "task_id",
            "step_id",
            "output_key",
            "type",
            "name",
            "size_bytes",
            "content_sha256",
            "content_type",
            "version",
            "expires_at",
            "created_at",
        ),
    ),
)


@dataclass(slots=True)
class ExportResult:
    """导出结果（直接映射为 TD1 的响应 `data`）。

    Attributes:
        export_id: 导出 ID（同时是产物 ID，便于按 ID 反查产物）。
        status: 状态。同步实现下恒为 `ready`。
        download_url: 下载地址（短期令牌，复用 `/files/artifacts/{token}`）。
        expires_at: 令牌过期时间（ISO 8601）。
        rows: 各文件的实测行数 + 总数。
        artifact_id: 产物 ID。
        size_bytes: 包大小。
    """

    export_id: str = ""
    status: str = "ready"
    download_url: str = ""
    expires_at: str = ""
    rows: dict[str, int] = field(default_factory=dict)
    artifact_id: str = ""
    size_bytes: int = 0


@dataclass(slots=True)
class DeletionResult:
    """删除结果（直接映射为 TD2 的响应 `data`）。

    Attributes:
        deletion_id: 删除 ID（同时是证明产物 ID）。
        deleted_at: 删除完成时间（ISO 8601）。
        deleted_tables: 实际执行删除的表（**含** 0 行的表，表达"查过且确认没了"）。
        deleted_rows: 表名 → 删除行数。
        skipped_tables: 因权限被跳过的表（当前只有 `audit_log`）。
        retained_tables: 不纳入在线删除、需 DBA 全库脚本的表。
        audit_pending_dba: 审计表**剩余**行数（待 DBA 执行，PRD 要求单列不合并）。
        audit_sql_hint: 交给 DBA 的 SQL。
        proof_download_url: 删除证明下载地址。
    """

    deletion_id: str = ""
    deleted_at: str = ""
    deleted_tables: list[str] = field(default_factory=list)
    deleted_rows: dict[str, int] = field(default_factory=dict)
    skipped_tables: list[str] = field(default_factory=list)
    retained_tables: list[str] = field(default_factory=list)
    audit_pending_dba: int = 0
    audit_sql_hint: str = AUDIT_SQL_HINT
    proof_download_url: str = ""


def iso(moment: datetime | None = None) -> str:
    """把时间写成 ISO 8601（UTC，`Z` 结尾）。

    Args:
        moment: 时间（缺省当前时间）。

    Returns:
        str: ISO 字符串。
    """
    current = moment or datetime.now(UTC)
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """校验并规范化 `scope[]`（缺省 = 全选，顺序与 `EXPORT_SCOPES` 一致）。

    Args:
        scopes: 请求里的类别列表。

    Returns:
        tuple[str, ...]: 规范化后的类别元组。

    Raises:
        ApiError: 出现未知类别（20001）。
    """
    if not scopes:
        return EXPORT_SCOPES
    unknown = [item for item in scopes if item not in EXPORT_SCOPES]
    if unknown:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message="导出范围只能从 操作记录 / 用量 / 会话 / 记忆 / 产物 中选择",
            internal_detail=f"unknown tenant-data scope: {unknown}",
        )
    return tuple(scope for scope in EXPORT_SCOPES if scope in set(scopes))


class TenantDataService:
    """租户数据导出与删除。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 配置实例。
            artifacts: 产物存储（缺省按配置取单例；测试可注入）。
        """
        self._settings = settings or get_settings()
        self._artifacts: ArtifactStore | None = artifacts

    @property
    def settings(self) -> Settings:
        """配置实例。

        Returns:
            Settings: 配置。
        """
        return self._settings

    def _store(self) -> ArtifactStore:
        """取产物存储（延迟取单例，避免构造期就建引擎）。

        Returns:
            ArtifactStore: 产物存储。
        """
        if self._artifacts is None:
            self._artifacts = get_artifact_store(self._settings)
        return self._artifacts

    # ------------------------------------------------------------------
    # 公共校验
    # ------------------------------------------------------------------
    def ensure_online_allowed(self, action: str) -> None:
        """私有化部署下拒绝在线导出/删除（PRD P4-05 ⑤）。

        Args:
            action: `export` / `delete`（只用于日志与内部诊断）。

        Raises:
            ApiError: 私有化模式（20001，带可操作文案）。
        """
        if not self._settings.is_private_mode:
            return
        logger.warning("tenant_data_online_denied", action=action, deploy_mode="private")
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message=PRIVATE_MODE_MESSAGE,
            internal_detail=(
                "deploy_mode=private: 在线导出/删除关闭，请执行 sql/full-export.sql "
                "或 sql/full-purge.sql"
            ),
        )

    # ------------------------------------------------------------------
    # TD1 导出
    # ------------------------------------------------------------------
    async def export(
        self,
        tenant_id: str,
        scopes: Sequence[str] | None = None,
        *,
        actor_id: str = "",
    ) -> ExportResult:
        """同步生成导出包并落产物库。

        Args:
            tenant_id: 企业 ID。
            scopes: 类别列表（缺省全选）。
            actor_id: 操作人（写日志用，不落包）。

        Returns:
            ExportResult: 导出结果（含短期下载地址）。

        Raises:
            ApiError: 私有化模式（20001）或数据量超上限（20001）。
        """
        self.ensure_online_allowed("export")
        selected = normalize_scopes(scopes)
        started = time.perf_counter()

        counts: dict[str, int] = dict.fromkeys(EXPORT_FILE_NAMES, 0)
        for scope in selected:
            counts[scope] = await self._count_scope(tenant_id, scope)
        total = sum(counts.values())
        if total > MAX_EXPORT_ROWS:
            logger.warning(
                "tenant_export_too_large",
                tenant_id=tenant_id,
                total=total,
                limit=MAX_EXPORT_ROWS,
                scopes=list(selected),
            )
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=EXPORT_TOO_LARGE_MESSAGE,
                internal_detail=(
                    f"tenant export exceeds row limit: total={total} limit={MAX_EXPORT_ROWS}"
                ),
            )

        export_id = new_resource_id("exp")
        payload, manifest = await self._build_archive(tenant_id, export_id, selected, counts)
        artifact = await self._store_artifact(
            tenant_id=tenant_id,
            artifact_id=export_id,
            name=f"tenants-{export_id}.zip",
            content=payload,
            content_type="application/zip",
            purpose=PURPOSE_EXPORT,
            # 导出包是"短期凭证"：与下载令牌同 TTL，过期即失效
            expires_in_days=None,
        )
        download_url, expires_at = self._download_url(tenant_id, artifact)
        rows = {scope: int(manifest["rows"].get(EXPORT_FILE_NAMES[scope], 0)) for scope in selected}
        rows["total"] = int(manifest["total_rows"])
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="tenant_data.export",
            resource_type="tenant_data",
            resource_id=export_id,
            change_summary={
                "scopes": list(selected),
                "rows": rows,
                "size_bytes": len(payload),
            },
        )
        logger.info(
            "tenant_export_ready",
            tenant_id=tenant_id,
            actor_id=actor_id,
            export_id=export_id,
            scopes=list(selected),
            rows=rows,
            total_rows=total,
            size_bytes=len(payload),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return ExportResult(
            export_id=export_id,
            status="ready",
            download_url=download_url,
            expires_at=expires_at,
            rows=rows,
            artifact_id=artifact.artifact_id,
            size_bytes=len(payload),
        )

    async def _count_scope(self, tenant_id: str, scope: str) -> int:
        """统计某类别的总行数（超限的快速失败路径）。

        Args:
            tenant_id: 企业 ID。
            scope: 类别。

        Returns:
            int: 行数。
        """
        total = 0
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            for source in self._sources(scope):
                table = _identifier(source.table)
                value = await uow.fetch_scalar(
                    f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id",
                    {"tenant_id": tenant_id},
                )
                total += int(value or 0)
        return total

    @staticmethod
    def _sources(scope: str) -> tuple[ExportSource, ...]:
        """取某类别下的全部取数定义。

        Args:
            scope: 类别。

        Returns:
            tuple[ExportSource, ...]: 取数定义。
        """
        return tuple(source for source in EXPORT_SOURCES if source.scope == scope)

    async def _build_archive(
        self,
        tenant_id: str,
        export_id: str,
        scopes: Sequence[str],
        counts: Mapping[str, int],
    ) -> tuple[bytes, dict[str, Any]]:
        """生成 zip 与清单。

        **流式写 zip**（`ZipFile.open(name, "w")`）而不是"先把整包拼进内存"：
        200,000 行的包可能上百 MB，`writestr` 会把同一份数据在内存里存两遍。

        **打包落盘而非全量 BytesIO**（审计 2026-11-03 QA-06）：写入目标是
        `SpooledTemporaryFile`——前 `_ZIP_SPOOL_MAX_BYTES` 在内存，超过就自动
        溢写到临时文件。这样"压缩过程"的内存峰值有上界，不会被整包大小牵着走。

        **残留口径**：最终仍要把整包读成一次 `bytes` 交给产物仓储（
        `plugin_artifact.content` 是 `bytea`，asyncpg 只接受完整 `bytes`）。
        因此峰值约为"一份压缩包"，而不是"两份"；要做到端到端流式，需把产物
        二进制迁到对象存储并把插入接口改成文件句柄，属独立存储契约变更。

        **写入不占事件循环**（审计 P2-5）：`handle.write` 是同步阻塞调用
        （deflate + 落盘），每 `_ZIP_CHUNK_ROWS` 行攒成一批后
        `await asyncio.to_thread(handle.write, b"".join(pending))`。
        `ZipFile` 的创建与关闭仍在事件循环线程（它们要读写同一个文件句柄，
        跨线程反而要加锁）；只有「压缩+写」这一段挪到线程池。

        Args:
            tenant_id: 企业 ID。
            export_id: 导出 ID。
            scopes: 类别列表。
            counts: 各类别行数（已由 `_count_scope` 统计）。

        Returns:
            tuple[bytes, dict[str, Any]]: zip 字节与清单内容。

        Raises:
            ApiError: 生成过程中行数超过上限（并发写入导致的超限）。
        """
        buffer = tempfile.SpooledTemporaryFile(max_size=_ZIP_SPOOL_MAX_BYTES)
        files: dict[str, dict[str, Any]] = {}
        actual: dict[str, int] = {}
        written = 0

        try:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for scope in scopes:
                    name = EXPORT_FILE_NAMES[scope]
                    digest = hashlib.sha256()
                    lines = 0
                    size_bytes = 0
                    with archive.open(name, "w") as handle:
                        # 攒批写（见 `_ZIP_CHUNK_ROWS`）：`handle.write` 是同步阻塞调用，
                        # 直接写在循环体里会卡住事件循环；攒够一批再丢线程池。
                        pending: list[bytes] = []
                        async for line in self._iter_scope(tenant_id, scope):
                            encoded = (line + "\n").encode("utf-8")
                            digest.update(encoded)
                            pending.append(encoded)
                            lines += 1
                            size_bytes += len(encoded)
                            written += 1
                            if written > MAX_EXPORT_ROWS:
                                raise ApiError(
                                    ErrorCode.PARAM_INVALID,
                                    message=EXPORT_TOO_LARGE_MESSAGE,
                                    internal_detail=(
                                        "tenant export exceeded row limit while streaming: "
                                        f"written={written} limit={MAX_EXPORT_ROWS}"
                                    ),
                                )
                            if len(pending) >= _ZIP_CHUNK_ROWS:
                                await asyncio.to_thread(handle.write, b"".join(pending))
                                pending.clear()
                        if pending:
                            await asyncio.to_thread(handle.write, b"".join(pending))
                            pending.clear()
                    files[name] = {
                        "scope": scope,
                        "rows": lines,
                        "declared_rows": int(counts.get(scope, 0)),
                        "bytes": size_bytes,
                        "sha256": digest.hexdigest(),
                    }
                    actual[scope] = lines

                manifest: dict[str, Any] = {
                    "export_id": export_id,
                    "tenant_id": tenant_id,
                    "generated_at": iso(),
                    "format": "jsonl",
                    "scopes": list(scopes),
                    "rows": {EXPORT_FILE_NAMES[scope]: actual.get(scope, 0) for scope in scopes},
                    "total_rows": written,
                    "files": files,
                    "notes": [
                        "每行一条记录，UTF-8 编码，JSON 键已排序。",
                        "会话与消息同放 chat.jsonl，行内 record 字段区分 conversation / message。",
                        "产物只导出清单（名称、摘要、大小），不含二进制正文；二进制可按单件下载获取。",
                        "记忆仅导出用户可见内容；派生向量与开关设置不在导出范围内。",
                        "操作记录（audit.jsonl）按保留期由运维侧维护，导出不受其影响。",
                    ],
                }
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                        default=_json_default,
                    ),
                )
            buffer.seek(0)
            payload = buffer.read()
        finally:
            buffer.close()
        return payload, manifest

    async def _iter_scope(self, tenant_id: str, scope: str) -> AsyncIterator[str]:
        """按类别逐页产出 JSONL 行（键集分页，逐页一个新事务）。

        为什么用键集（`(ts, id) > (cursor_ts, cursor_id)`）而不是 `OFFSET`：
        `OFFSET 199000` 要求数据库先扫过前 199000 行再丢掉，页数越多越慢；
        键集分页每页都走索引区间扫描，代价恒定。

        Args:
            tenant_id: 企业 ID。
            scope: 类别。

        Yields:
            str: 一行 JSON。
        """
        for source in self._sources(scope):
            cursor_ts = datetime(1970, 1, 1, tzinfo=UTC)
            cursor_id: Any = 0 if source.id_cast == "bigint" else ""
            while True:
                rows = await self._fetch_page(tenant_id, source, cursor_ts, cursor_id)
                for row in rows:
                    payload = dict(row)
                    if source.record:
                        payload["record"] = source.record
                    yield dump_line(payload)
                if len(rows) < PAGE_SIZE:
                    break
                last = rows[-1]
                cursor_ts = last[source.timestamp_column]
                cursor_id = last[source.id_column]

    async def _fetch_page(
        self,
        tenant_id: str,
        source: ExportSource,
        cursor_ts: datetime,
        cursor_id: Any,
    ) -> list[dict[str, Any]]:
        """取一页数据。

        Args:
            tenant_id: 企业 ID。
            source: 取数定义。
            cursor_ts: 上一页最后一行的时间。
            cursor_id: 上一页最后一行的 ID。

        Returns:
            list[dict[str, Any]]: 行（已转成 dict）。
        """
        table = _identifier(source.table)
        columns = ", ".join(_identifier(column) for column in source.columns)
        ts_column = _identifier(source.timestamp_column)
        id_column = _identifier(source.id_column)
        sql = (
            f"SELECT {columns} FROM {table} AS t0"
            " WHERE t0.tenant_id = :tenant_id"
            f"   AND (t0.{ts_column}, t0.{id_column})"
            f"       > (CAST(:cursor_ts AS timestamptz), CAST(:cursor_id AS {source.id_cast}))"
            f" ORDER BY t0.{ts_column}, t0.{id_column}"
            " LIMIT CAST(:page_size AS bigint)"
        )
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            rows = await uow.fetch_all(
                sql,
                {
                    "tenant_id": tenant_id,
                    "cursor_ts": cursor_ts,
                    "cursor_id": cursor_id,
                    "page_size": PAGE_SIZE,
                },
            )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # TD2 删除
    # ------------------------------------------------------------------
    async def delete(
        self,
        tenant_id: str,
        confirm_text: str,
        scopes: Sequence[str] | None = None,
        *,
        actor_id: str = "",
    ) -> DeletionResult:
        """二次确认后硬删租户数据，并产出删除证明。

        Args:
            tenant_id: 企业 ID。
            confirm_text: 用户输入的企业名称（须与库内名称**逐字**一致）。
            scopes: 类别列表（缺省全选）。
            actor_id: 操作人。

        Returns:
            DeletionResult: 删除结果（含两段式统计与证明下载地址）。

        Raises:
            ApiError: 企业不存在（20004）、确认文本不匹配（18003）、
                私有化模式（20001）、未知类别（20001）。
        """
        self.ensure_online_allowed("delete")
        selected = normalize_scopes(scopes)
        name = await self._tenant_name(tenant_id)
        # **逐字比对**：不 strip、不忽略大小写、不做模糊匹配。
        # 二次确认的意义就是"你必须真的知道自己在删哪家企业"，容错会让误删变成可能。
        if (confirm_text or "") != name:
            logger.warning(
                "tenant_delete_confirm_mismatch",
                tenant_id=tenant_id,
                actor_id=actor_id,
                expected_length=len(name),
                actual_length=len(confirm_text or ""),
            )
            raise ApiError(
                ErrorCode.DELETE_CONFIRM_MISMATCH,
                internal_detail="confirm_text does not match tenant name exactly",
            )

        started = time.perf_counter()
        await self._suspend(tenant_id)

        deleted_rows: dict[str, int] = {}
        skipped: list[str] = []
        for table in self._delete_order(selected):
            try:
                deleted_rows[table] = await self._purge_table(tenant_id, table)
            except Exception as exc:
                if _is_insufficient_privilege(exc):
                    skipped.append(table)
                    deleted_rows[table] = 0
                    logger.warning(
                        "tenant_delete_table_skipped",
                        tenant_id=tenant_id,
                        table=table,
                        reason="no privilege",
                    )
                    continue
                raise

        # 顺序很重要：**先写审计动作、再数审计行**。
        # 删除动作本身也落在 audit_log 里，反序会让"待 DBA 执行的行数"
        # 恰好少掉自己这一条——而 PRD P4-05 ④ 要求这个数字覆盖全部待清行。
        deletion_id = new_resource_id("del")
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="tenant_data.delete",
            resource_type="tenant_data",
            resource_id=deletion_id,
            change_summary={
                "scopes": list(selected),
                "deleted_rows": deleted_rows,
                "skipped_tables": skipped,
            },
        )
        audit_pending = await self._count_audit(tenant_id)
        deleted_at = iso()

        proof = {
            # 自证标记：这份文件"是什么"，与"谁的数据"无关
            "proof_of_deletion": True,
            "deletion_id": deletion_id,
            "tenant_id": tenant_id,
            "tenant_name": name,
            "deleted_at": deleted_at,
            "requested_by": actor_id,
            "scopes": list(selected),
            # 两段式呈现（PRD P4-05 ④）：应用侧 / 待 DBA，**不合并成一个数字**
            "application_side": {
                "tables": {
                    table: rows for table, rows in deleted_rows.items() if table not in skipped
                },
                "total_rows": sum(
                    rows for table, rows in deleted_rows.items() if table not in skipped
                ),
                "skipped_tables": skipped,
            },
            "pending_dba": {
                "audit_log_rows": audit_pending,
                "sql": AUDIT_SQL_HINT,
                "note": (
                    "应用角色对 audit_log 无 DELETE 权限（init-app-role.sql 第 4 节），"
                    "审计行需由库属主执行上面的 SQL 清理。"
                ),
            },
            "retained_tables": list(RETAINED_TABLES),
            "retained_note": (
                "知识库与文档正文不纳入在线删除；退租需要全量清空时由 DBA 执行 sql/full-purge.sql。"
            ),
            "content_policy": (
                "本证明只含各表行数与时间戳，不含任何被删数据的内容或摘要；"
                "它自身是删除后唯一保留的产物，留存期取 plugin_artifact_retention_days。"
            ),
        }
        proof_bytes = json.dumps(
            proof, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default
        ).encode("utf-8")
        artifact = await self._store_artifact(
            tenant_id=tenant_id,
            artifact_id=deletion_id,
            name=f"deletion-proof-{deletion_id}.json",
            content=proof_bytes,
            content_type="application/json",
            purpose=PURPOSE_DELETION_PROOF,
            # 证明要**活得比下载链接久**：链接 15 分钟就失效，而"我已经删了"这件事
            # 客户可能要过几个月才来核查。按产物留存期（默认 180 天）保存。
            expires_in_days=int(self._settings.plugin_artifact_retention_days),
        )
        proof_url, _expires = self._download_url(tenant_id, artifact)
        logger.info(
            "tenant_delete_done",
            tenant_id=tenant_id,
            actor_id=actor_id,
            deletion_id=deletion_id,
            deleted_rows={t: r for t, r in deleted_rows.items() if t not in skipped},
            total_rows=sum(r for t, r in deleted_rows.items() if t not in skipped),
            skipped_tables=skipped,
            audit_pending_dba=audit_pending,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        applied = {table: rows for table, rows in deleted_rows.items() if table not in skipped}
        return DeletionResult(
            deletion_id=deletion_id,
            deleted_at=deleted_at,
            deleted_tables=list(applied),
            deleted_rows=applied,
            skipped_tables=skipped,
            retained_tables=list(RETAINED_TABLES),
            audit_pending_dba=audit_pending,
            audit_sql_hint=AUDIT_SQL_HINT,
            proof_download_url=proof_url,
        )

    @staticmethod
    def _delete_order(scopes: Iterable[str]) -> tuple[str, ...]:
        """按"先子后父"的顺序拼出要删的表（去重，保持稳定顺序）。

        Args:
            scopes: 类别列表。

        Returns:
            tuple[str, ...]: 表名序列。
        """
        ordered: list[str] = []
        for scope in scopes:
            for table in DELETE_SCOPE_TABLES.get(scope, ()):
                if table not in ordered:
                    ordered.append(table)
        for table in ALWAYS_DELETE_TABLES:
            if table not in ordered:
                ordered.append(table)
        return tuple(ordered)

    async def _tenant_name(self, tenant_id: str) -> str:
        """读企业名称（二次确认的比对基准）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            str: 企业名称。

        Raises:
            ApiError: 企业不存在（20004）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            row = await uow.fetch_one(
                "SELECT name FROM tenant WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant_id},
            )
        if row is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                internal_detail=f"tenant not found: {tenant_id}",
            )
        return str(row["name"] or "")

    async def _suspend(self, tenant_id: str) -> None:
        """把企业置为停用（**阻断后续写**，替代 `deleted_at`）。

        `tenant` 表没有 `deleted_at` 列（阶段一定义的列集：`tenant_id/name/status/
        deploy_mode/plan_code/suspended_reason/created_at/updated_at`）。新增该列
        需要一次迁移 + 基线脚本同步，超出本批范围，因此复用既有的
        `status='suspended'` + `suspended_reason` 表达"因数据删除请求而停用"。

        Args:
            tenant_id: 企业 ID。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            await uow.execute(
                "UPDATE tenant SET status = 'suspended',"
                " suspended_reason = :reason, updated_at = now()"
                " WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant_id, "reason": "数据删除请求处理中"},
            )

    async def _purge_table(self, tenant_id: str, table_name: str) -> int:
        """分批硬删一张表的全部行。

        Args:
            tenant_id: 企业 ID。
            table_name: 表名。

        Returns:
            int: 删除行数。

        Raises:
            Exception: SQL / 权限错误原样上抛（由调用方区分"预期跳过"与"真失败"）。
        """
        table = _identifier(table_name)
        statement = (
            f"DELETE FROM {table}"
            " WHERE tenant_id = :tenant_id"
            "   AND ctid IN ("
            f"       SELECT t0.ctid FROM {table} AS t0"
            "         WHERE t0.tenant_id = :tenant_id"
            "         LIMIT CAST(:page_size AS bigint)"
            "   )"
        )
        removed = 0
        while True:
            async with tenant_scope(tenant_id, settings=self._settings) as uow:
                result = await uow.execute(
                    statement, {"tenant_id": tenant_id, "page_size": PAGE_SIZE}
                )
                affected = int(getattr(result, "rowcount", 0) or 0)
            removed += affected
            if affected < PAGE_SIZE:
                return removed

    async def _count_audit(self, tenant_id: str) -> int:
        """统计该租户 `audit_log` 剩余行数（待 DBA 执行的数量）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            int: 行数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            value = await uow.fetch_scalar(
                "SELECT count(*) FROM audit_log WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant_id},
            )
        return int(value or 0)

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    async def _audit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        change_summary: Mapping[str, Any],
    ) -> None:
        """写一条数据权利动作的操作记录。

        **不吞异常**：导出/删除是"客户会拿去证明我们已经履约"的动作，
        没留下记录就等于没发生。审计写失败必须让请求失败，而不是静默成功——
        这与"权限被拒时审计失败不阻断拒绝"的口径正好相反，因为那次拒绝本身
        已经生效，而这里没有别的证据来源。

        Args:
            tenant_id: 企业 ID。
            actor_id: 操作人。
            action: 操作编码。
            resource_type: 资源类型。
            resource_id: 资源 ID。
            change_summary: 变更摘要（只放条数与范围，禁止正文）。
        """
        from app.business.audit_service import AuditService

        await AuditService(self._settings).record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            change_summary=dict(change_summary),
        )

    # ------------------------------------------------------------------
    # 产物与下载地址
    # ------------------------------------------------------------------
    async def _ensure_host_task(self, tenant_id: str, task_id: str, purpose: str) -> None:
        """为数据权利产物建一条宿主任务（`plugin_artifact.task_id` 是 NOT NULL 外键）。

        幂等：`ON CONFLICT (tenant_id, task_id) DO NOTHING`，重放/重试都不会重复建。

        **这条任务行里不允许有任何客户数据**（合规要求，不是洁癖）：宿主任务只是
        为了满足外键而存在，因此除最小标识外**显式置空**——`conversation_id=''`、
        `inputs='{}'`、`checklist='[]'`、`warnings='[]'`、`error_message=NULL`。
        不用"靠列默认值兜住"：默认值某天被改（比如 `inputs` 加个模板初值），
        这里就会悄悄带上数据，而没人会想到去看一眼宿主任务。
        `result_summary` 只放一个用途标记（`proof_of_deletion` / `tenant_data_export`），
        它是"这条任务是什么"，不是"谁的数据"。

        Args:
            tenant_id: 企业 ID。
            task_id: 宿主任务 ID（与导出/删除 ID 相同，便于反查）。
            purpose: 用途标记（写进 `result_summary`，也是产物的 `output_key`）。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            await uow.execute(
                "INSERT INTO plugin_task ("
                " tenant_id, task_id, plugin_id, plugin_version, initiator_id,"
                " conversation_id, trigger_type, status, progress, idem_key,"
                " inputs, result_summary, checklist, warnings, error_message,"
                " started_at, finished_at"
                ") VALUES ("
                " :tenant_id, :task_id, :plugin_id, :plugin_version, '',"
                " '', 'manual', 'completed', 100, :idem_key,"
                " '{}'::jsonb, :result_summary, '[]'::jsonb, '[]'::jsonb, NULL,"
                " now(), now()"
                ") ON CONFLICT (tenant_id, task_id) DO NOTHING",
                {
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "plugin_id": HOST_PLUGIN_ID,
                    "plugin_version": HOST_PLUGIN_VERSION,
                    # `idem_key` 是 NOT NULL：用任务 ID 本身，天然唯一且可反查
                    "idem_key": task_id,
                    "result_summary": purpose,
                },
            )

    async def _store_artifact(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        name: str,
        content: bytes,
        content_type: str,
        purpose: str,
        expires_in_days: int | None = None,
    ) -> ArtifactRecord:
        """把包写入产物库（复用 B19 的产物代理，**不新增下载端点**）。

        **有效期分两种，不能混**：

        - 导出包：`expires_at = now + 下载令牌 TTL`（默认 15 分钟）。它是"短期凭证"，
          客户要的是"现在取走"，过期即失效最安全；
        - 删除证明：`expires_at = now + plugin_artifact_retention_days`（默认 180 天）。
          证明要用来说"我已经删了"，只在 15 分钟内可取等于没给。**下载链接本身仍是
          短期令牌**（15 分钟），需要新链接时重新调一次 TD2 之外的路径即可——
          链接短、证据长，这两件事本来就不该共用一个期限。

        产物行里**只有证明 JSON 本身**：`inline_text` / `preview_text` 一律空，
        二进制只走 `content`。不允许把被删数据的任何内容或摘要塞进来。

        Args:
            tenant_id: 企业 ID。
            artifact_id: 产物 ID（与导出/删除 ID 相同）。
            name: 文件名（扩展名决定 MIME 归一化结果）。
            content: 字节。
            content_type: MIME。
            purpose: 用途标记（同时作为 `output_key` 与宿主任务的 `result_summary`）。
            expires_in_days: 留存天数；`None` 表示按下载令牌 TTL 过期。

        Returns:
            ArtifactRecord: 落库后的产物行。
        """
        if expires_in_days is None:
            ttl = int(self._settings.artifact_token_ttl_seconds)
            expires_at = iso(datetime.now(UTC) + timedelta(seconds=ttl))
        else:
            expires_at = iso(datetime.now(UTC) + timedelta(days=max(1, expires_in_days)))
        await self._ensure_host_task(tenant_id, artifact_id, purpose)
        created = await self._store().add_artifacts(
            [
                ArtifactRecord(
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    # `task_id` 必须指向真实任务（NOT NULL + FK），所以宿主任务用同一个
                    # ID：一条数据权利产物 = 一条宿主任务 + 一条产物，反查也最直接。
                    task_id=artifact_id,
                    step_id="",
                    output_key=purpose,
                    type="file",
                    name=name,
                    # 正文摘要字段一律空：产物里**只有** content 这一份字节，
                    # 没有第二处副本，也就没有"忘了脱敏"的第二个入口
                    inline_text="",
                    preview_text="",
                    content=content,
                    content_type=content_type,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    expires_at=expires_at,
                )
            ]
        )
        return created[0]

    def _download_url(self, tenant_id: str, artifact: ArtifactRecord) -> tuple[str, str]:
        """生成短期下载地址与过期时间。

        Args:
            tenant_id: 企业 ID。
            artifact: 产物行。

        Returns:
            tuple[str, str]: `(download_url, expires_at)`。
        """
        ttl = int(self._settings.artifact_token_ttl_seconds)
        expires_at = iso(datetime.now(UTC) + timedelta(seconds=ttl))
        token = make_token(
            tenant_id=tenant_id,
            artifact_id=artifact.artifact_id,
            version=max(1, artifact.version),
            settings=self._settings,
            ttl_seconds=ttl,
        )
        return f"{ARTIFACT_DOWNLOAD_PREFIX}/{token}", expires_at


def _identifier(name: str) -> str:
    """校验表名/列名（拼进 SQL 前必须过一次白名单）。

    Args:
        name: 标识符。

    Returns:
        str: 原样返回。
    """
    from app.db.repositories.base import assert_identifier

    return assert_identifier(name)


def _is_insufficient_privilege(exc: BaseException) -> bool:
    """判断异常是否为 PG 权限不足（SQLSTATE 42501，沿异常链查找）。

    Args:
        exc: 捕获到的异常。

    Returns:
        bool: 权限不足返回 True。
    """
    from app.workers.tasks.retention_cleanup import is_insufficient_privilege

    return is_insufficient_privilege(exc)


def get_tenant_data_service(settings: Settings | None = None) -> TenantDataService:
    """构造租户数据服务。

    Args:
        settings: 配置实例（缺省取全局单例）。

    Returns:
        TenantDataService: 服务实例。
    """
    return TenantDataService(settings or get_settings())


__all__: list[str] = [
    "ALWAYS_DELETE_TABLES",
    "ARTIFACT_DOWNLOAD_PREFIX",
    "DELETE_SCOPE_TABLES",
    "EXPORT_FILE_NAMES",
    "EXPORT_SCOPES",
    "EXPORT_SOURCES",
    "EXPORT_TOO_LARGE_MESSAGE",
    "MANIFEST_NAME",
    "MAX_EXPORT_ROWS",
    "PAGE_SIZE",
    "PRIVATE_MODE_MESSAGE",
    "PURPOSE_DELETION_PROOF",
    "PURPOSE_EXPORT",
    "RETAINED_TABLES",
    "TABLE_LABELS",
    "DeletionResult",
    "ExportResult",
    "ExportSource",
    "TenantDataService",
    "dump_line",
    "get_tenant_data_service",
    "iso",
    "normalize_scopes",
]
