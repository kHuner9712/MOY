"""后台任务仓储（阶段四 T4-03.2，架构 §2.3 / §3.4）。

为什么是 PG 队列而不是 Valkey：任务状态必须与业务数据**同事务**提交
（"记忆写进 `user_memory`" 与 "job 变 succeeded" 要么一起成立、要么一起不成立），
否则会出现"记忆写了但 job 重试一次 → 重复抽取"或"job 说成功但记忆没落地"。

三条关键约束（写错任何一条都会在生产上出事）：

1. **认领必须跨租户**：`background_job` 是 strict RLS 表，认领走
   `platform_scope()`（`app.tenant_id='__platform__'`，与 `/ops/**` 同一机制）。
   认领与租约更新在**同一个事务**里完成，否则会出现"状态是 running 但
   `locked_until` 没写"的撕裂行——那一行再没人能回收，任务永久卡死。
2. **租约而非锁**：`locked_by` + `locked_until`。进程被 kill 时行停在 `running`，
   由下一次认领回收（`locked_until < now()`）。不引第二套锁语义（Valkey 锁）。
3. **同 kind 去重**：`uq_job_dedupe` 只对"同租户 + 同 kind + 同 dedupe_key 且未终结"
   生效（`dedupe_key <> ''`）。入队冲突**不是错误**——它就是"已经排着了"，
   调用方拿到 `None` 继续走自己的业务，绝不让去重把对话打红。

双后端（内存 / PostgreSQL）行为必须一致：`InMemoryJobStore` 与
`PostgresJobStore` 的语义漂移会让"单测全绿、生产丢任务"。
"""

# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.session import platform_scope, tenant_scope

logger = get_logger(__name__)

JOB_TABLE: Final[str] = "background_job"

#: 允许的任务类型（与 `0010_background_job.sql` 的 CHECK 一致）
JOB_KINDS: Final[frozenset[str]] = frozenset(
    {"memory_extract", "tenant_export", "retention_cleanup"}
)

#: 任务状态（与 CHECK 一致）
JOB_STATUSES: Final[frozenset[str]] = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled"}
)

#: 未终结状态（去重索引的生效区间）
OPEN_STATUSES: Final[frozenset[str]] = frozenset({"queued", "running"})

#: `last_error` 入库长度上限：错误信息只用于排障，**绝不落原文正文**
MAX_ERROR_LENGTH: Final[int] = 200

#: 重试退避的基数（秒）；第 n 次重试等 `base * 2**(n-1)`，上限 60s
RETRY_BACKOFF_BASE_SECONDS: Final[float] = 5.0

#: 重试退避上限（秒）
RETRY_BACKOFF_MAX_SECONDS: Final[float] = 60.0


def new_job_id() -> str:
    """生成任务 ID（`job_` + ULID）。

    Returns:
        str: 任务 ID。
    """
    return new_resource_id("job")


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601，`Z` 结尾）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> str:
    """把时间值规范成 ISO 8601（UTC，`Z` 结尾）。

    Args:
        value: 时间值（`datetime` / 字符串 / None）。

    Returns:
        str: ISO 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text.replace("+00:00", "Z") if text else ""


def _json_object(value: Any) -> dict[str, Any]:
    """把 JSONB 列读成字典（asyncpg 可能返回 `dict` 或 JSON 字符串）。

    Args:
        value: 列值。

    Returns:
        dict[str, Any]: 字典；无法解析时返回空字典（宁可少字段，不炸整条链路）。
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        return {str(key): item for key, item in loaded.items()} if isinstance(loaded, dict) else {}
    return {}


def retry_delay_seconds(attempts: int) -> float:
    """计算第 `attempts` 次失败后的退避时长（指数退避，封顶 60s）。

    Args:
        attempts: 已尝试次数（≥1）。

    Returns:
        float: 退避秒数。
    """
    exponent = max(0, attempts - 1)
    delay: float = RETRY_BACKOFF_BASE_SECONDS * float(2**exponent)
    return min(delay, RETRY_BACKOFF_MAX_SECONDS)


def sanitize_error(detail: str) -> str:
    """把异常信息裁剪成可入库的短串（去换行、截断、不含正文）。

    **隐私约束**：`last_error` 只允许放"异常类型 + 位置"，绝不放用户内容；
    调用方只应传 `f"{type(exc).__name__}: 上下文"`。

    Args:
        detail: 原始错误描述。

    Returns:
        str: 单行、≤`MAX_ERROR_LENGTH` 的描述。
    """
    text = " ".join((detail or "").split())
    return text[:MAX_ERROR_LENGTH]


@dataclass(slots=True)
class JobRecord:
    """后台任务行。

    Attributes:
        tenant_id: 企业 ID（任务归属租户，认领后切回该租户干活）。
        job_id: 任务 ID（`job_` + ULID）。
        kind: 任务类型（`memory_extract` / `tenant_export` / `retention_cleanup`）。
        dedupe_key: 去重键（如会话 ID）；空串表示不参与去重。
        payload: 任务参数（JSON 对象）。
        status: `queued|running|succeeded|failed|cancelled`。
        attempts: 已尝试次数（**认领时 +1**，因此首次执行时恒为 1）。
        max_attempts: 最大尝试次数；`attempts >= max_attempts` 即终结。
        run_after: 最早可执行时间（延迟任务 / 退避重试）。
        locked_by: 持有者标识（`<host>:<pid>:<uuid>`，便于排障定位进程）。
        locked_until: 租约到期时间；过期即可被其他进程回收。
        last_error: 最近一次失败原因（已裁剪，无正文）。
        created_at: 创建时间。
        updated_at: 更新时间。
        finished_at: 终结时间。
    """

    tenant_id: str = ""
    job_id: str = ""
    kind: str = ""
    dedupe_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    run_after: str = ""
    locked_by: str = ""
    locked_until: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""
    finished_at: str = ""

    @property
    def exhausted(self) -> bool:
        """本次尝试是否已用尽重试次数。

        Returns:
            bool: `attempts >= max_attempts` 时返回 True（该失败即终结）。
        """
        return self.attempts >= self.max_attempts


def _to_job(row: Mapping[str, Any]) -> JobRecord:
    """把数据库行转成任务记录。

    Args:
        row: 行映射。

    Returns:
        JobRecord: 任务记录。
    """
    return JobRecord(
        tenant_id=str(row.get("tenant_id", "")),
        job_id=str(row.get("job_id", "")),
        kind=str(row.get("kind", "")),
        dedupe_key=str(row.get("dedupe_key", "") or ""),
        payload=_json_object(row.get("payload")),
        status=str(row.get("status", "queued")),
        attempts=int(row.get("attempts", 0) or 0),
        max_attempts=int(row.get("max_attempts", 3) or 3),
        run_after=_timestamp(row.get("run_after")),
        locked_by=str(row.get("locked_by", "") or ""),
        locked_until=_timestamp(row.get("locked_until")),
        last_error=str(row.get("last_error", "") or ""),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
        finished_at=_timestamp(row.get("finished_at")),
    )


class JobStore(Protocol):
    """任务存储契约（内存 / PostgreSQL 双实现）。"""

    async def enqueue(self, record: JobRecord) -> JobRecord | None:
        """入队；同 `dedupe_key` 的未终结任务已存在时返回 None（去重命中）。"""
        ...

    async def claim(self, *, worker_id: str, limit: int, lease_seconds: int) -> list[JobRecord]:
        """认领到期任务（跨租户，含租约过期的回收）；无任务返回空列表。"""
        ...

    async def mark_succeeded(self, job: JobRecord) -> None:
        """标记成功（终态）。"""
        ...

    async def mark_failed(self, job: JobRecord, *, error: str, now: datetime | None = None) -> str:
        """标记失败：还有余量则退回 `queued` 退避重试，否则置 `failed`。

        Returns:
            str: 落库后的状态（`queued` 或 `failed`）。
        """
        ...

    async def get(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """按 ID 读取（排障与测试用）。"""
        ...

    async def list_by_status(
        self, tenant_id: str, status: str, *, limit: int = 100
    ) -> list[JobRecord]:
        """列出某状态的任务（排障与测试用）。"""
        ...


class InMemoryJobStore:
    """进程内任务存储（development / test）。

    语义与 PostgreSQL 实现逐条对齐：去重索引、租约回收、`attempts` 认领时递增。
    时钟可注入（`now`），否则"租约过期回收"这类用例只能靠 sleep——慢且不稳定。
    """

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        """初始化空存储。

        Args:
            now: 可选时钟（返回带时区的当前时间），测试注入用。
        """
        self._items: dict[tuple[str, str], JobRecord] = {}
        self._now = now or (lambda: datetime.now(UTC))

    async def enqueue(self, record: JobRecord) -> JobRecord | None:
        """入队（去重键冲突时返回 None）。

        Args:
            record: 任务记录。

        Returns:
            JobRecord | None: 落库后的记录；去重命中返回 None。

        Raises:
            ValueError: 任务类型或状态非法（内存实现无 CHECK 约束，需自己拦）。
        """
        if record.kind not in JOB_KINDS:
            raise ValueError(f"illegal job kind: {record.kind!r}")
        if record.dedupe_key and self._find_open(record) is not None:
            logger.info(
                "job_enqueue_deduped",
                tenant_id=record.tenant_id,
                kind=record.kind,
                dedupe_key=record.dedupe_key,
            )
            return None
        stamp = now_iso()
        stored = JobRecord(
            tenant_id=record.tenant_id,
            job_id=record.job_id or new_job_id(),
            kind=record.kind,
            dedupe_key=record.dedupe_key,
            payload=dict(record.payload),
            status="queued",
            attempts=0,
            max_attempts=max(1, record.max_attempts),
            run_after=record.run_after or stamp,
            locked_by="",
            locked_until="",
            last_error="",
            created_at=record.created_at or stamp,
            updated_at=record.updated_at or stamp,
            finished_at="",
        )
        self._items[(stored.tenant_id, stored.job_id)] = stored
        return stored

    async def claim(self, *, worker_id: str, limit: int, lease_seconds: int) -> list[JobRecord]:
        """认领到期任务（`queued` 且到点，或 `running` 且租约过期）。

        Args:
            worker_id: 持有者标识。
            limit: 本次最多认领条数。
            lease_seconds: 租约时长（秒）。

        Returns:
            list[JobRecord]: 已认领的记录（`status='running'`、`attempts` 已 +1）。
        """
        current = self._now().astimezone(UTC)
        due: list[JobRecord] = []
        for item in self._items.values():
            if item.status == "queued":
                run_after = as_datetime(item.run_after)
                if run_after is None or run_after <= current:
                    due.append(item)
            elif item.status == "running":
                locked_until = as_datetime(item.locked_until)
                if locked_until is None or locked_until < current:
                    due.append(item)
        due.sort(key=lambda job: (job.run_after or "", job.created_at or "", job.job_id))
        claimed: list[JobRecord] = []
        for item in due[: max(0, limit)]:
            item.status = "running"
            item.attempts += 1
            item.locked_by = worker_id
            item.locked_until = (
                (current + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
            )
            item.updated_at = now_iso()
            claimed.append(item)
        return claimed

    async def mark_succeeded(self, job: JobRecord) -> None:
        """标记成功。

        Args:
            job: 任务记录。
        """
        stored = self._items.get((job.tenant_id, job.job_id))
        if stored is None:
            return
        stamp = now_iso()
        stored.status = "succeeded"
        stored.locked_by = ""
        stored.locked_until = ""
        stored.last_error = ""
        stored.updated_at = stamp
        stored.finished_at = stamp

    async def mark_failed(self, job: JobRecord, *, error: str, now: datetime | None = None) -> str:
        """标记失败（余量足够则退回队列并退避）。

        Args:
            job: 任务记录。
            error: 错误描述（调用方已裁剪）。
            now: 当前时间（缺省用注入的时钟）。

        Returns:
            str: 落库后的状态。
        """
        stored = self._items.get((job.tenant_id, job.job_id))
        if stored is None:
            return "failed"
        current = (now or self._now()).astimezone(UTC)
        stamp = now_iso()
        stored.last_error = sanitize_error(error)
        stored.locked_by = ""
        stored.locked_until = ""
        stored.updated_at = stamp
        # 注册表的重试策略是**上限**（Runner 传进来的 `job` 已按策略收敛），
        # 因此这里以传入值为准：库里写 3 也不能突破 `memory_extract` 的 1。
        stored.max_attempts = max(1, min(stored.max_attempts, job.max_attempts))
        if stored.attempts >= stored.max_attempts:
            stored.status = "failed"
            stored.finished_at = stamp
            return stored.status
        stored.status = "queued"
        stored.finished_at = ""
        stored.run_after = (
            (current + timedelta(seconds=retry_delay_seconds(stored.attempts)))
            .isoformat()
            .replace("+00:00", "Z")
        )
        return stored.status

    async def get(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 企业 ID。
            job_id: 任务 ID。

        Returns:
            JobRecord | None: 任务记录。
        """
        return self._items.get((tenant_id, job_id))

    async def list_by_status(
        self, tenant_id: str, status: str, *, limit: int = 100
    ) -> list[JobRecord]:
        """列出某状态的任务。

        Args:
            tenant_id: 企业 ID。
            status: 任务状态。
            limit: 条数上限。

        Returns:
            list[JobRecord]: 任务列表（按创建时间升序）。
        """
        rows = [
            item
            for item in self._items.values()
            if item.tenant_id == tenant_id and item.status == status
        ]
        rows.sort(key=lambda job: (job.created_at, job.job_id))
        return rows[:limit]

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._items.clear()

    def _find_open(self, record: JobRecord) -> JobRecord | None:
        """查找同（租户 + kind + dedupe_key）的未终结任务。

        Args:
            record: 待入队记录。

        Returns:
            JobRecord | None: 命中的行；没有返回 None。
        """
        for item in self._items.values():
            if (
                item.tenant_id == record.tenant_id
                and item.kind == record.kind
                and item.dedupe_key == record.dedupe_key
                and item.status in OPEN_STATUSES
            ):
                return item
        return None


class _JobTableRepository(BaseRepository):
    """`background_job` 表仓储。"""

    table = JOB_TABLE


class PostgresJobStore:
    """PostgreSQL 任务存储（`FOR UPDATE SKIP LOCKED` 队列）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def enqueue(self, record: JobRecord) -> JobRecord | None:
        """入队（`ON CONFLICT DO NOTHING` 吸收去重冲突）。

        Args:
            record: 任务记录。

        Returns:
            JobRecord | None: 落库后的记录；去重命中返回 None。

        Raises:
            ValueError: 任务类型非法。
        """
        if record.kind not in JOB_KINDS:
            raise ValueError(f"illegal job kind: {record.kind!r}")
        job_id = record.job_id or new_job_id()
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {JOB_TABLE} (
                    tenant_id, job_id, kind, dedupe_key, payload, status,
                    attempts, max_attempts, run_after
                ) VALUES (
                    :tenant_id, :job_id, :kind, :dedupe_key, CAST(:payload AS jsonb), 'queued',
                    0, :max_attempts, COALESCE(CAST(:run_after AS timestamptz), now())
                )
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                repo.params(
                    job_id=job_id,
                    kind=record.kind,
                    dedupe_key=record.dedupe_key,
                    payload=json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                    max_attempts=max(1, record.max_attempts),
                    run_after=as_datetime(record.run_after),
                ),
            )
        if row is None:
            logger.info(
                "job_enqueue_deduped",
                tenant_id=record.tenant_id,
                kind=record.kind,
                dedupe_key=record.dedupe_key,
            )
            return None
        return _to_job(row)

    async def claim(self, *, worker_id: str, limit: int, lease_seconds: int) -> list[JobRecord]:
        """认领到期任务（平台作用域 + `SKIP LOCKED` + 租约，同一事务完成）。

        `status='running' AND locked_until < now()` 的行来自崩溃进程：它们**没有**
        终结，因此必须可被回收，否则任务永久卡在 running（用户看到"记忆一直是空的"）。

        Args:
            worker_id: 持有者标识。
            limit: 本次最多认领条数。
            lease_seconds: 租约时长（秒）。

        Returns:
            list[JobRecord]: 已认领的记录。
        """
        async with platform_scope(settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                WITH candidate AS (
                    SELECT tenant_id, job_id
                      FROM {JOB_TABLE}
                     WHERE (status = 'queued' AND run_after <= now())
                        OR (status = 'running'
                            AND (locked_until IS NULL OR locked_until < now()))
                     ORDER BY run_after ASC, created_at ASC
                     LIMIT :claim_limit
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE {JOB_TABLE} AS job
                   SET status = 'running',
                       attempts = job.attempts + 1,
                       locked_by = :locked_by,
                       locked_until = now() + make_interval(
                           secs => CAST(:lease_seconds AS double precision)
                       ),
                       updated_at = now()
                  FROM candidate
                 WHERE job.tenant_id = candidate.tenant_id
                   AND job.job_id = candidate.job_id
                RETURNING job.*
                """,
                {"locked_by": worker_id, "lease_seconds": lease_seconds, "claim_limit": limit},
            )
        return [_to_job(row) for row in rows]

    async def mark_succeeded(self, job: JobRecord) -> None:
        """标记成功（终态）。

        Args:
            job: 任务记录。
        """
        async with tenant_scope(job.tenant_id, settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {JOB_TABLE}
                   SET status = 'succeeded',
                       locked_by = '',
                       locked_until = NULL,
                       last_error = '',
                       updated_at = now(),
                       finished_at = now()
                 WHERE {repo.tenant_clause()} AND job_id = :job_id
                """,
                repo.params(job_id=job.job_id),
            )

    async def mark_failed(self, job: JobRecord, *, error: str, now: datetime | None = None) -> str:
        """标记失败（余量足够则退回队列并退避）。

        Args:
            job: 任务记录。
            error: 错误描述（已裁剪）。
            now: 当前时间（缺省取数据库时间；此处仅用于计算退避基准）。

        Returns:
            str: 落库后的状态（`queued` 或 `failed`）。
        """
        exhausted = job.attempts >= job.max_attempts
        status = "failed" if exhausted else "queued"
        delay = 0.0 if exhausted else retry_delay_seconds(job.attempts)
        async with tenant_scope(job.tenant_id, settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {JOB_TABLE}
                   SET status = :status,
                       locked_by = '',
                       locked_until = NULL,
                       last_error = :last_error,
                       max_attempts = LEAST(max_attempts, :effective_max_attempts),
                       updated_at = now(),
                       finished_at = CASE WHEN :status = 'failed' THEN now() ELSE NULL END,
                       run_after = CASE WHEN :status = 'failed' THEN run_after
                                        ELSE now() + make_interval(
                                            secs => CAST(:delay AS double precision)
                                        ) END
                 WHERE {repo.tenant_clause()} AND job_id = :job_id
                """,
                repo.params(
                    job_id=job.job_id,
                    status=status,
                    last_error=sanitize_error(error),
                    delay=delay,
                    effective_max_attempts=max(1, job.max_attempts),
                ),
            )
        return status

    async def get(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 企业 ID。
            job_id: 任务 ID。

        Returns:
            JobRecord | None: 任务记录。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {JOB_TABLE} WHERE {repo.tenant_clause()} AND job_id = :job_id",
                repo.params(job_id=job_id),
            )
        return _to_job(row) if row is not None else None

    async def list_by_status(
        self, tenant_id: str, status: str, *, limit: int = 100
    ) -> list[JobRecord]:
        """列出某状态的任务（按创建时间升序）。

        Args:
            tenant_id: 企业 ID。
            status: 任务状态。
            limit: 条数上限。

        Returns:
            list[JobRecord]: 任务列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _JobTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {JOB_TABLE}
                 WHERE {repo.tenant_clause()} AND status = :status
                 ORDER BY created_at ASC, job_id ASC
                 LIMIT :limit
                """,
                repo.params(status=status, limit=limit),
            )
        return [_to_job(row) for row in rows]


#: 任务存储单例（按配置选中实现）
_job_store: JobStore | None = None


def get_job_store(settings: Settings | None = None) -> JobStore:
    """取任务存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        JobStore: 存储实现。
    """
    global _job_store
    conf = settings or get_settings()
    if _job_store is None:
        _job_store = PostgresJobStore(conf) if conf.uses_postgres else InMemoryJobStore()
    return _job_store


def reset_job_store() -> None:
    """重置任务存储单例（测试隔离用）。"""
    global _job_store
    _job_store = None


__all__: list[str] = [
    "JOB_KINDS",
    "JOB_STATUSES",
    "JOB_TABLE",
    "MAX_ERROR_LENGTH",
    "OPEN_STATUSES",
    "InMemoryJobStore",
    "JobRecord",
    "JobStore",
    "PostgresJobStore",
    "get_job_store",
    "new_job_id",
    "now_iso",
    "reset_job_store",
    "retry_delay_seconds",
    "sanitize_error",
]
