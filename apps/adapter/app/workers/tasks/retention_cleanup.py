"""`retention_cleanup` 任务处理器（阶段四 T4-05.3，架构 §3.5 / §10.4 / PRD P4-05 ②）。

**这是"存储期限"这条法律义务的唯一执行点**，因此它不只要会删，还要能**自证**
自己删了什么、以及**哪里删不了**：

1. **分级保留**：审计 180 天、用量 13 个月、任务与产物 180 天、会话与消息 365 天、
   记忆**永久**（只由用户自己删）。全部来自 `Settings.retention_*`，代码里没有
   写死的天数——保留期是合同条款，改天数不该重新发版。
2. **`audit_log` 永远删不掉，且这是设计**：`deploy/init-app-role.sql` 第 4 节对
   应用角色执行 `REVOKE UPDATE, DELETE ON audit_log`（阶段二"审计只增不改"的
   正确设计）。因此本任务**捕获权限错误后逐字打印 `audit_log=skipped(no privilege)`**，
   而不是把它当失败、也不是静默跳过。这一行是验收硬判据（架构 §10.4 / F-9）：
   它是"应用侧知道自己没权限"的唯一证据，比"清理任务跑完了"更能证明没漏清。
3. **每批 ≤ 1000 行**：一条 `DELETE` 删掉几十万行会长时间持锁 + 撑爆 WAL，
   在线业务直接感知为"数据库卡了"。分批后单条语句的锁窗口在毫秒级。
   （`DELETE` 本身不 `VACUUM`，空间由 PG 的 autovacuum 回收——这一点写在
   `sql/retention-audit.sql` 的注释里，避免运维以为"删了磁盘就空了"。）

**为什么是"每租户一个任务"而不是"一个平台级任务"**（本批的第二个设计决策）：

- 平台级任务要跨租户删 → 必须走 `platform_scope()` 写路径，而 `platform_scope`
  的契约写的是"**仅供运营后台**"；把后台维护任务塞进去等于开第二条越权通道；
- 每租户一个任务，每次删除都落在 `tenant_scope(job.tenant_id)` 里，RLS 天然兜底：
  就算 `tenant_id` 传错，也删不到别人的行；
- 代价是要枚举租户。枚举用一次**只读**的 `platform_scope()`（只 `SELECT tenant_id`），
  发生在本模块的 `schedule_all_tenants()`，与删除路径完全分离。

**调度语义**（`dedupe_key` 为什么是固定串）：去重键取 `retention-daily`（**不带日期**）。
带日期会在"当天任务已跑完 + 进程重启"时再排一个当天任务，于是同一时刻躺着两个
待执行的清理任务；固定键表达的是"该租户已有一次清理在排队"，重启多少次都只有一次。
真正的执行时刻记在 `payload.scheduled_for`，排障照样看得见。
"""

# ruff: noqa: S608 - 表名/列名均为本模块常量，值一律命名参数绑定
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import assert_identifier
from app.db.repositories.job_repo import JobRecord, JobStore, get_job_store
from app.db.session import platform_scope, tenant_scope
from app.workers.job_runner import JobRunner

logger = get_logger(__name__)

#: 任务类型（与 `background_job.kind` 的 CHECK 一致）
RETENTION_KIND: Final[str] = "retention_cleanup"

#: 去重键（固定串，理由见模块说明）
RETENTION_DEDUPE_KEY: Final[str] = "retention-daily"

#: 该类任务的最大尝试次数（架构 §2.3：`retention_cleanup` → 3 次）
RETENTION_MAX_ATTEMPTS: Final[int] = 3

#: 连续失败多少次后把日志升到 ERROR（PRD P4-05 ②："连续 2 次非权限原因失败才告警"）
ALERT_AFTER_FAILURES: Final[int] = 2

#: 单批删除行数上限（避免长锁 / WAL 尖峰）
DEFAULT_BATCH_SIZE: Final[int] = 1000

#: 每日执行时刻（UTC 小时）。选 03:00 UTC = 北京时间 11:00 前后，
#: 既避开欧美夜间批处理，也避开国内上午的用量高峰。
SCHEDULE_HOUR_UTC: Final[int] = 3

#: `audit_log` 的跳过标记（**逐字**，验收硬判据，不得改写）
AUDIT_SKIP_MARKER: Final[str] = "audit_log=skipped(no privilege)"

#: 交给 DBA 的审计清理 SQL（响应/证明里回显，避免运维去翻文档）
AUDIT_SQL_HINT: Final[str] = (
    "DELETE FROM audit_log WHERE occurred_at < now() - interval '{{days}} days';"
)

#: PG 的 SQLSTATE：权限不足
_INSUFFICIENT_PRIVILEGE: Final[str] = "42501"

#: 规则模式
MODE_DELETE: Final[str] = "delete"
MODE_SKIP_NO_PRIVILEGE: Final[str] = "skip_no_privilege"
MODE_SKIP_PERMANENT: Final[str] = "skip_permanent"


def subtract_months(moment: datetime, months: int) -> datetime:
    """把时间往前推 N 个自然月（**不用** 30 天近似）。

    `30 * months` 天与"13 个月前"在司法口径上是两回事：2 月与 7 月差 2~3 天，
    累积到 13 个月会差出半个多月。因此按年月回退，并把"日"截到目标月的最后一天
    （`3 月 31 日 - 1 个月` → `2 月 28/29 日`）。

    Args:
        moment: 基准时间（应带时区）。
        months: 回退的月数（≥0）。

    Returns:
        datetime: 回退后的时间。
    """
    if months <= 0:
        return moment
    total = moment.year * 12 + (moment.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def next_daily_run_after(now: datetime | None = None) -> datetime:
    """计算下一次每日清理的执行时刻（次日 `SCHEDULE_HOUR_UTC` 点，UTC）。

    Args:
        now: 当前时间（缺省取 `datetime.now(UTC)`）。

    Returns:
        datetime: 严格大于 `now` 的执行时刻。
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    candidate = current.replace(hour=SCHEDULE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def iso(moment: datetime) -> str:
    """把时间写成 ISO 8601（UTC，`Z` 结尾，与全仓口径一致）。

    Args:
        moment: 时间。

    Returns:
        str: ISO 字符串。
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RetentionRule:
    """一条清理规则（"哪张表、按什么判据、删多少天以前的"）。

    Attributes:
        table: 表名（模块常量，进 SQL 前由 `assert_identifier` 校验）。
        label: 中文名（日志与证明文件用，运维看得懂）。
        where_sql: 相对 `t0` 别名的过期判据（值一律命名参数绑定）。
        params: 该判据用到的命名参数（不含 `tenant_id`）。
        mode: `delete` / `skip_no_privilege` / `skip_permanent`。
        reason: `skip_*` 模式的说明（进日志与交付清单）。
    """

    table: str
    label: str
    where_sql: str
    params: dict[str, Any] = field(default_factory=dict)
    mode: str = MODE_DELETE
    reason: str = ""


@dataclass(slots=True)
class TableResult:
    """一张表的清理结果（日志、返回报告、删除证明三处共用同一份事实）。

    Attributes:
        table: 表名。
        label: 中文名。
        deleted: 实际删除行数。
        duration_ms: 耗时（毫秒）。
        status: `ok` / `skipped_no_privilege` / `skipped_permanent` / `failed`。
        error: 失败原因（异常类名，不含用户数据）。
    """

    table: str
    label: str = ""
    deleted: int = 0
    duration_ms: int = 0
    status: str = "ok"
    error: str = ""

    @property
    def skipped(self) -> bool:
        """是否被跳过（权限 / 永久保留）。

        Returns:
            bool: 跳过时返回 True。
        """
        return self.status in {MODE_SKIP_NO_PRIVILEGE, MODE_SKIP_PERMANENT}


@dataclass(slots=True)
class RetentionReport:
    """一次清理的完整结果。

    Attributes:
        tenant_id: 企业 ID。
        started_at: 开始时间（ISO 8601）。
        finished_at: 结束时间（ISO 8601）。
        results: 每张表的结果（顺序即执行顺序）。
        batch_size: 单批行数上限。
    """

    tenant_id: str
    started_at: str = ""
    finished_at: str = ""
    results: list[TableResult] = field(default_factory=list)
    batch_size: int = DEFAULT_BATCH_SIZE

    @property
    def deleted_total(self) -> int:
        """总删除行数。

        Returns:
            int: 各表删除行数之和。
        """
        return sum(item.deleted for item in self.results)

    @property
    def failed_tables(self) -> list[TableResult]:
        """失败的表（**不含**跳过项：权限跳过是预期行为，不是失败）。

        Returns:
            list[TableResult]: 失败项。
        """
        return [item for item in self.results if item.status == "failed"]

    @property
    def audit_skipped(self) -> bool:
        """审计表是否因无权限被跳过（验收硬判据）。

        Returns:
            bool: `audit_log` 落在 `skipped_no_privilege` 时返回 True。
        """
        return any(
            item.table == "audit_log" and item.status == MODE_SKIP_NO_PRIVILEGE
            for item in self.results
        )

    def counts(self) -> dict[str, int]:
        """每表删除行数（证明文件与日志用）。

        Returns:
            dict[str, int]: 表名 → 删除行数。
        """
        return {item.table: item.deleted for item in self.results}


def build_rules(settings: Settings, *, now: datetime | None = None) -> tuple[RetentionRule, ...]:
    """按配置生成全部清理规则（**唯一的保留期口径来源**）。

    Args:
        settings: 配置实例（`retention_*` 全部在此生效）。
        now: 基准时间（缺省取当前 UTC 时间；测试注入以便断言判据）。

    Returns:
        tuple[RetentionRule, ...]: 规则列表（顺序即执行顺序）。
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    day_cutoff = lambda days: current - timedelta(days=days)  # noqa: E731 - 三行内联更清楚
    usage_floor = subtract_months(current, settings.retention_usage_months)
    # 软删 30 天后硬删（`conversation_soft_delete_recover_days` 是"可恢复窗口"）
    soft_delete_cutoff = current - timedelta(days=settings.conversation_soft_delete_recover_days)
    conversation_cutoff = day_cutoff(settings.retention_conversation_days)
    task_cutoff = day_cutoff(settings.retention_task_days)

    return (
        # --- 会话与消息（365 天硬删；软删 30 天后提前硬删） -------------------
        RetentionRule(
            table="message",
            label="消息",
            where_sql=(
                "t0.created_at < CAST(:conversation_cutoff AS timestamptz)"
                " OR t0.conversation_id IN ("
                "SELECT c.conversation_id FROM conversation c"
                " WHERE c.tenant_id = t0.tenant_id"
                "   AND c.deleted_at IS NOT NULL"
                "   AND c.deleted_at < CAST(:soft_delete_cutoff AS timestamptz))"
            ),
            params={
                "conversation_cutoff": conversation_cutoff,
                "soft_delete_cutoff": soft_delete_cutoff,
            },
        ),
        RetentionRule(
            table="conversation",
            label="会话",
            where_sql=(
                "t0.created_at < CAST(:conversation_cutoff AS timestamptz)"
                " OR (t0.deleted_at IS NOT NULL"
                "     AND t0.deleted_at < CAST(:soft_delete_cutoff AS timestamptz))"
            ),
            params={
                "conversation_cutoff": conversation_cutoff,
                "soft_delete_cutoff": soft_delete_cutoff,
            },
        ),
        # --- 任务与产物（180 天） --------------------------------------------
        # 产物规则放在任务规则**之前**：`fk_artifact_task` / `fk_step_task` 都是
        # `ON DELETE CASCADE`，先删任务会连带删掉产物与步骤，于是"产物删了几行"
        # 会变成 0 —— 统计就不再是"这张表过期了多少行"的事实。
        RetentionRule(
            table="plugin_artifact",
            label="产物",
            where_sql=(
                "(t0.expires_at IS NOT NULL AND t0.expires_at < now())"
                " OR t0.created_at < CAST(:task_cutoff AS timestamptz)"
            ),
            params={"task_cutoff": task_cutoff},
        ),
        # `plugin_task_step` 不单列规则：FK 是 `ON DELETE CASCADE`，任务被删时步骤
        # 自动随之消失（写一条永远不会命中 0 行以外的规则只是噪音）。
        RetentionRule(
            table="plugin_task",
            label="插件任务",
            where_sql=(
                "t0.started_at < CAST(:task_cutoff AS timestamptz)"
                " OR (t0.finished_at IS NOT NULL"
                "     AND t0.finished_at < CAST(:task_cutoff AS timestamptz))"
            ),
            params={"task_cutoff": task_cutoff},
        ),
        # --- 用量（13 个月） -------------------------------------------------
        RetentionRule(
            table="usage_event",
            label="用量事件",
            where_sql="t0.occurred_at < CAST(:usage_cutoff AS timestamptz)",
            params={"usage_cutoff": usage_floor},
        ),
        RetentionRule(
            table="usage_monthly",
            label="用量月度汇总",
            where_sql="t0.period::text < CAST(:period_floor AS text)",
            params={"period_floor": f"{usage_floor.year:04d}-{usage_floor.month:02d}"},
        ),
        # --- 运维流水（与任务同口径 180 天） ---------------------------------
        RetentionRule(
            table="integration_outbox",
            label="集成出站队列",
            where_sql="t0.created_at < CAST(:task_cutoff AS timestamptz)",
            params={"task_cutoff": task_cutoff},
        ),
        RetentionRule(
            table="payment_callback_log",
            label="支付回调流水",
            where_sql="t0.received_at < CAST(:usage_cutoff AS timestamptz)",
            params={"usage_cutoff": usage_floor},
        ),
        # --- 审计（180 天，**应用角色无权删**，交给 DBA） ---------------------
        RetentionRule(
            table="audit_log",
            label="操作记录",
            where_sql="t0.occurred_at < CAST(:audit_cutoff AS timestamptz)",
            params={"audit_cutoff": day_cutoff(settings.retention_audit_days)},
            mode=MODE_SKIP_NO_PRIVILEGE,
            reason=(
                "应用角色对 audit_log 无 DELETE 权限（init-app-role.sql 第 4 节），"
                "按架构 §10.4 / F-9 交 DBA 执行 sql/retention-audit.sql"
            ),
        ),
        # --- 记忆（**永久，仅用户可删**） ------------------------------------
        RetentionRule(
            table="user_memory",
            label="记忆",
            where_sql="false",
            mode=MODE_SKIP_PERMANENT,
            reason="记忆永久保留，只能由用户本人在「我的记忆」里删除（PRD Q-4）",
        ),
        RetentionRule(
            table="memory_embedding",
            label="记忆派生数据",
            where_sql="false",
            mode=MODE_SKIP_PERMANENT,
            reason="随记忆一起删除，不单独过期（没有 memory_id 就不该留）",
        ),
        RetentionRule(
            table="user_memory_setting",
            label="记忆开关",
            where_sql="false",
            mode=MODE_SKIP_PERMANENT,
            reason="用户偏好，永久保留",
        ),
    )


def is_insufficient_privilege(exc: BaseException) -> bool:
    """判断异常是否为 PG 的"权限不足"（SQLSTATE 42501）。

    SQLAlchemy 会把 asyncpg 的异常包成 `ProgrammingError`，所以必须**沿异常链**
    找原始异常；同时 `orig` 属性也是常见携带点（它不是 `__cause__`）。

    Args:
        exc: 捕获到的异常。

    Returns:
        bool: 权限不足返回 True。
    """
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if type(current).__name__ == "InsufficientPrivilegeError":
            return True
        for attribute in ("sqlstate", "pgcode"):
            if getattr(current, attribute, None) == _INSUFFICIENT_PRIVILEGE:
                return True
        for attribute in ("__cause__", "__context__", "orig"):
            candidate = getattr(current, attribute, None)
            if isinstance(candidate, BaseException):
                pending.append(candidate)
    return False


async def _purge(
    tenant_id: str,
    rule: RetentionRule,
    *,
    batch_size: int,
    settings: Settings,
) -> int:
    """分批删除一张表的过期行（每批 ≤ `batch_size`，各自独立事务）。

    **每批一个事务**：单条 `DELETE` 的锁窗口因此有界；也让"删到一半进程被杀"
    最多丢掉当前批，已提交的批次不会回滚（保留期清理可重入，重跑即继续）。

    增量分批的判据只有一个：**本批返回的行数 < `batch_size` 即到底**。
    不先 `count(*)`：`count` 与 `delete` 之间还会有新过期行，多一次全表扫描
    换来的"准确总数"本身也不是准的。

    Args:
        tenant_id: 企业 ID。
        rule: 规则。
        batch_size: 单批行数上限。
        settings: 配置实例。

    Returns:
        int: 删除行数。

    Raises:
        Exception: 删除失败（权限 / 连接 / SQL）原样上抛，由调用方分类。
    """
    # 表名一律经 `assert_identifier` 过一遍：规则表是模块常量，但这里是拼 SQL 的
    # 唯一入口，校验放在最靠近 f-string 的位置才拦得住"以后有人从配置拼表名"。
    table = assert_identifier(rule.table)
    statement = f"""
        DELETE FROM {table}
         WHERE tenant_id = :tenant_id
           AND ctid IN (
               SELECT t0.ctid FROM {table} AS t0
                WHERE t0.tenant_id = :tenant_id AND ({rule.where_sql})
                LIMIT CAST(:batch_size AS bigint)
           )
    """
    deleted = 0
    while True:
        async with tenant_scope(tenant_id, settings=settings) as uow:
            result = await uow.execute(
                statement,
                {**rule.params, "tenant_id": tenant_id, "batch_size": batch_size},
            )
            affected = int(getattr(result, "rowcount", 0) or 0)
        deleted += affected
        if affected < batch_size:
            return deleted


async def run_retention(
    tenant_id: str,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> RetentionReport:
    """执行一次某租户的保留期清理，并返回可交付的完整报告。

    失败**不**中断循环：一张表失败不该让其余表也不清（否则一次权限问题会导致
    全库数据永久超期）。失败项进 `report.failed_tables`，由处理器决定是否上报。

    Args:
        tenant_id: 企业 ID。
        settings: 配置实例（缺省取全局单例）。
        now: 基准时间（测试注入）。
        batch_size: 单批行数上限。

    Returns:
        RetentionReport: 清理报告（含跳过项与失败项）。
    """
    conf = settings or get_settings()
    rules = build_rules(conf, now=now)
    report = RetentionReport(
        tenant_id=tenant_id,
        started_at=iso(now or datetime.now(UTC)),
        batch_size=batch_size,
    )

    for rule in rules:
        started = time.perf_counter()
        item = TableResult(table=rule.table, label=rule.label)
        if rule.mode == MODE_SKIP_PERMANENT:
            item.status = MODE_SKIP_PERMANENT
            item.duration_ms = int((time.perf_counter() - started) * 1000)
            report.results.append(item)
            logger.info(
                "retention_table_skipped",
                tenant_id=tenant_id,
                table=rule.table,
                reason="permanent",
                detail=rule.reason,
            )
            continue
        if rule.mode == MODE_SKIP_NO_PRIVILEGE:
            # 仍然**尝试**一次：权限判定交给数据库，而不是靠代码里写死"这表删不了"。
            # 这样一旦运维真的授予了权限（例如改成 officeai_maint 角色），
            # 清理会自动生效，代码不用跟着改。失败即按预期记跳过标记。
            try:
                item.deleted = await _purge(tenant_id, rule, batch_size=batch_size, settings=conf)
                item.duration_ms = int((time.perf_counter() - started) * 1000)
                report.results.append(item)
                logger.info(
                    "retention_table_cleaned",
                    tenant_id=tenant_id,
                    table=rule.table,
                    deleted=item.deleted,
                    duration_ms=item.duration_ms,
                )
                continue
            except Exception as exc:
                item.duration_ms = int((time.perf_counter() - started) * 1000)
                if is_insufficient_privilege(exc):
                    item.status = MODE_SKIP_NO_PRIVILEGE
                    item.error = "insufficient_privilege"
                    report.results.append(item)
                    # ⚠ 逐字输出，验收硬判据（架构 §10.4 / F-9）。事件名就是那句话本身，
                    # 这样 console / JSON 两种 renderer 下都能被 grep 到。
                    logger.warning(
                        AUDIT_SKIP_MARKER,
                        tenant_id=tenant_id,
                        table=rule.table,
                        reason="no privilege",
                        detail=rule.reason,
                    )
                    continue
                item.status = "failed"
                item.error = type(exc).__name__
                report.results.append(item)
                logger.error(
                    "retention_table_failed",
                    tenant_id=tenant_id,
                    table=rule.table,
                    duration_ms=item.duration_ms,
                    error=type(exc).__name__,
                )
                continue
        try:
            item.deleted = await _purge(tenant_id, rule, batch_size=batch_size, settings=conf)
        except Exception as exc:
            item.duration_ms = int((time.perf_counter() - started) * 1000)
            item.status = "failed"
            item.error = type(exc).__name__
            report.results.append(item)
            logger.error(
                "retention_table_failed",
                tenant_id=tenant_id,
                table=rule.table,
                duration_ms=item.duration_ms,
                error=type(exc).__name__,
            )
            continue
        item.duration_ms = int((time.perf_counter() - started) * 1000)
        report.results.append(item)
        logger.info(
            "retention_table_cleaned",
            tenant_id=tenant_id,
            table=rule.table,
            deleted=item.deleted,
            duration_ms=item.duration_ms,
        )

    report.finished_at = iso(datetime.now(UTC))
    return report


async def schedule_tenant(
    tenant_id: str,
    *,
    store: JobStore | None = None,
    settings: Settings | None = None,
    run_after: datetime | None = None,
    now: datetime | None = None,
) -> JobRecord | None:
    """为单个租户排下一次清理（同租户已有在排的任务时自动去重）。

    Args:
        tenant_id: 企业 ID。
        store: 任务存储（缺省按配置取）。
        settings: 配置实例。
        run_after: 期望执行时间（缺省次日 `SCHEDULE_HOUR_UTC` 点）。
        now: 当前时间（测试注入）。

    Returns:
        JobRecord | None: 入队成功返回记录；已有在排任务返回 None。
    """
    conf = settings or get_settings()
    job_store = store or get_job_store(conf)
    target = (run_after or next_daily_run_after(now)).astimezone(UTC)
    return await job_store.enqueue(
        JobRecord(
            tenant_id=tenant_id,
            kind=RETENTION_KIND,
            dedupe_key=RETENTION_DEDUPE_KEY,
            payload={"scheduled_for": iso(target)},
            max_attempts=RETENTION_MAX_ATTEMPTS,
            run_after=iso(target),
        )
    )


async def list_tenants(settings: Settings | None = None) -> list[str]:
    """枚举全部租户（每日排队的输入）。

    **只读**的 `platform_scope()`：只 `SELECT tenant_id`，不写、不删、不返回任何
    业务字段。写路径一律回到 `tenant_scope`（见模块说明第 3 条）。

    Args:
        settings: 配置实例。

    Returns:
        list[str]: 租户 ID 列表（字典序）。
    """
    conf = settings or get_settings()
    async with platform_scope(conf, read_only=True) as uow:
        rows = await uow.fetch_all("SELECT tenant_id FROM tenant ORDER BY tenant_id")
    return [str(row["tenant_id"]) for row in rows]


async def schedule_all_tenants(
    settings: Settings | None = None,
    *,
    store: JobStore | None = None,
    now: datetime | None = None,
) -> list[str]:
    """为全部租户排下一次清理（启动时调用；已排过的不会重复）。

    Args:
        settings: 配置实例。
        store: 任务存储。
        now: 当前时间（测试注入）。

    Returns:
        list[str]: 本次**新入队**的租户 ID 列表。
    """
    conf = settings or get_settings()
    job_store = store or get_job_store(conf)
    target = next_daily_run_after(now)
    created: list[str] = []
    for tenant_id in await list_tenants(conf):
        job = await schedule_tenant(
            tenant_id, store=job_store, settings=conf, run_after=target, now=now
        )
        if job is not None:
            created.append(tenant_id)
    logger.info(
        "retention_schedule_ready",
        scheduled_for=iso(target),
        tenants=len(created),
        dedupe_key=RETENTION_DEDUPE_KEY,
    )
    return created


async def handle_retention_cleanup(
    job: JobRecord,
    *,
    settings: Settings | None = None,
    store: JobStore | None = None,
) -> None:
    """执行一次清理，并把自己排到第二天（自续期）。

    Args:
        job: 任务记录（`tenant_id` 决定清哪个租户）。
        settings: 配置实例。
        store: 任务存储。

    Raises:
        RuntimeError: 存在**非权限原因**的失败表时抛出，让 Runner 记 `failed`
            并按 `RETENTION_MAX_ATTEMPTS` 退避重试；连续
            `ALERT_AFTER_FAILURES` 次失败时同时打 ERROR 级日志（告警口径）。
    """
    conf = settings or get_settings()
    report = await run_retention(job.tenant_id, conf)
    logger.info(
        "retention_cleanup_done",
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        attempts=job.attempts,
        deleted_total=report.deleted_total,
        counts=report.counts(),
        skipped=[item.table for item in report.results if item.skipped],
        audit_skipped=report.audit_skipped,
        started_at=report.started_at,
        finished_at=report.finished_at,
    )

    failures = report.failed_tables
    # 无论成败都排下一天：否则一次失败会让清理永久停在原地（数据继续超期）。
    await schedule_tenant(job.tenant_id, store=store, settings=conf)

    if not failures:
        return
    detail = ", ".join(f"{item.table}:{item.error}" for item in failures)
    # `job.attempts` 是**本次**尝试的序号（认领时 +1）：第 2 次及以后即"连续失败"
    log = logger.error if job.attempts >= ALERT_AFTER_FAILURES else logger.warning
    log(
        "retention_cleanup_failed",
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        attempts=job.attempts,
        failed_tables=[item.table for item in failures],
        detail=detail,
        alert=job.attempts >= ALERT_AFTER_FAILURES,
    )
    raise RuntimeError(f"retention cleanup failed on {len(failures)} table(s)")


def register_retention_cleanup(
    runner: JobRunner,
    settings: Settings | None = None,
    *,
    store: JobStore | None = None,
) -> None:
    """把清理任务接到 Runner 的注册表上（新增 kind 不改主循环）。

    Args:
        runner: 任务 Runner。
        settings: 配置实例。
        store: 任务存储（自续期用）。
    """
    from functools import partial

    conf = settings or get_settings()
    runner.register(
        RETENTION_KIND,
        partial(handle_retention_cleanup, settings=conf, store=store),
        max_attempts=RETENTION_MAX_ATTEMPTS,
    )


__all__: list[str] = [
    "ALERT_AFTER_FAILURES",
    "AUDIT_SKIP_MARKER",
    "AUDIT_SQL_HINT",
    "DEFAULT_BATCH_SIZE",
    "RETENTION_DEDUPE_KEY",
    "RETENTION_KIND",
    "RETENTION_MAX_ATTEMPTS",
    "SCHEDULE_HOUR_UTC",
    "RetentionReport",
    "RetentionRule",
    "TableResult",
    "build_rules",
    "handle_retention_cleanup",
    "is_insufficient_privilege",
    "list_tenants",
    "next_daily_run_after",
    "register_retention_cleanup",
    "run_retention",
    "schedule_all_tenants",
    "schedule_tenant",
    "subtract_months",
]
