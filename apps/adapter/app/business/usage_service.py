"""计量服务（阶段二 B07，架构文档 2.5 / D-12 / §6.2）。

**单一写入源**：本服务是 `usage_event` 与 `usage_monthly` 的**唯一**写入口
（D-12 第 1 条）。任何模块（M1/M2/R3/R5/A1）都不得直接操作这两张表。

**M4 与 BL5 同源**（D-12 第 5 条）：两个端点都调用 `snapshot()`——
同一函数、同一张聚合表、同一段计算，因此**同刻数字必然相等**
（`used_units == quota_units_used`），不是靠对账凑出来的。

**结算时点**（D-12 第 3 条）：流式在 `message_end` 结算（不逐 delta）；
中断/超时在 SSE 生成器 `finally` 中按已产出文本折算，`source='estimated'`，
折算公式 `units = prompt_units + ceil(len(已产出文本) / OA_USAGE_CHARS_PER_UNIT)`，
未知 prompt 用量按 `OA_USAGE_ESTIMATE_PROMPT_UNITS` 兜底。

**回滚**：`OA_USAGE_WRITE_ENABLED=false` → 不再写新事件与聚合，
只读历史聚合（M4/BL5 仍可返回历史数字）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.usage_repo import (
    CAPABILITY_AUTOMATION,
    CAPABILITY_CHAT,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_DOC_QA,
    SCOPE_DEPARTMENT,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    SOURCE_SETTLED,
    RangeEventStats,
    UsageEventRecord,
    UsageStore,
    current_period,
    get_usage_store,
)

logger = get_logger(__name__)

#: 用量事件 ID 前缀（架构文档 11.1）
USAGE_EVENT_PREFIX: Final[str] = "use_"

#: 能力 → 用户可见名称（BL5 `group_by=capability` 的展示名）
CAPABILITY_DISPLAY_NAMES: Final[dict[str, str]] = {
    CAPABILITY_CHAT: "智能对话",
    CAPABILITY_DOC_QA: "文档问答",
    CAPABILITY_DOC_PROCESS: "文档处理",
    CAPABILITY_AUTOMATION: "自动化任务",
}

#: 资源类型
RESOURCE_GENERATION: Final[str] = "generation"
RESOURCE_DOCUMENT: Final[str] = "document"
RESOURCE_TASK: Final[str] = "task"

#: 中断折算的分母下限（防止 0 除）
_MIN_CHARS_PER_UNIT: Final[int] = 1

#: UTF-8 中文平均字节数（用于按文件大小估算字符数，仅在缺少精确字符数时使用）
_UTF8_BYTES_PER_CJK_CHAR: Final[int] = 3


class UsageDirectory(Protocol):
    """计量所需的组织信息解析器（B08 组织域落地后注入真实实现）。"""

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        """取用户的主部门 ID（无部门返回空串）。"""
        ...

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        """取用户展示名（未登记返回空串）。"""
        ...


class NullUsageDirectory:
    """缺省组织信息解析器（B07 阶段无组织域，部门维度按空处理）。"""

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        """取用户主部门（缺省实现恒为空）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 空串。
        """
        del tenant_id, user_id
        return ""

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        """取用户展示名（缺省实现返回空串，由前端展示占位）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 空串。
        """
        del tenant_id, user_id
        return ""


@dataclass(frozen=True, slots=True)
class UsageBreakdownItem:
    """用量分项（BL5 `breakdown` 元素 / M4 的额度说明）。

    Attributes:
        key: 分项键（用户 ID / 部门 ID / 能力名）。
        display_name: 用户可见名称。
        units: 用量。
        percent: 占总额度百分比（总额度为 0 时为 0）。
    """

    key: str = ""
    display_name: str = ""
    units: int = 0
    percent: float = 0.0


@dataclass(frozen=True, slots=True)
class UsageDailyPoint:
    """按自然日的用量点（BL5 `daily_trend` 元素）。

    Attributes:
        date: `YYYY-MM-DD`。
        units: 当日用量。
    """

    date: str = ""
    units: int = 0


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """用量快照（M4 与 BL5 **共用**的唯一计算产物）。

    Attributes:
        tenant_id: 租户 ID。
        period: 计量周期 `YYYY-MM`。
        used_units: 已用额度（个人视图为该成员行，管理视图为租户行）。
        total_units: 额度总量（套餐席位 × 每席位额度；无订阅为 0）。
        remaining_units: 剩余额度（不小于 0）。
        usage_percent: 使用百分比（保留两位小数）。
        over_quota_warning: 是否已达/超额（软提示，不停服）。
        quota_unit: 额度单位文案。
        plan_name: 套餐展示名。
        plan_code: 套餐标识。
        breakdown: 分项明细。
        daily_trend: 按日趋势。
        event_counts: 事件条数（`能力 → 次数`）。
    """

    tenant_id: str = ""
    period: str = ""
    used_units: int = 0
    total_units: int = 0
    remaining_units: int = 0
    usage_percent: float = 0.0
    over_quota_warning: bool = False
    quota_unit: str = ""
    plan_name: str = ""
    plan_code: str = ""
    breakdown: tuple[UsageBreakdownItem, ...] = ()
    daily_trend: tuple[UsageDailyPoint, ...] = ()
    event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def quota_units_used(self) -> int:
        """BL5 口径的已用额度（与 `used_units` **恒等**，D-12 第 5 条）。"""
        return self.used_units


def _percent(used: int, total: int) -> float:
    """计算使用百分比。

    Args:
        used: 已用额度。
        total: 额度总量。

    Returns:
        float: 百分比（两位小数）；总量为 0 时返回 0.0。
    """
    if total <= 0:
        return 0.0
    return round(used / total * 100, 2)


class UsageService:
    """计量服务（D-12 唯一写入口 + M4/BL5 同源读）。

    Attributes:
        _settings: 配置实例。
        _store: 计量存储（内存 / PostgreSQL）。
        _directory: 组织信息解析器（部门归属与展示名）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: UsageStore | None = None,
        directory: UsageDirectory | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选计量存储（测试注入）。
            directory: 可选组织信息解析器（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: UsageStore = store or get_usage_store(self._settings)
        self._directory: UsageDirectory = directory or _default_directory(self._settings)

    @property
    def settings(self) -> Settings:
        """当前配置（供调用方读取折算参数）。"""
        return self._settings

    @property
    def store(self) -> UsageStore:
        """当前计量存储（测试可据此断言事件条数）。"""
        return self._store

    # ------------------------------------------------------------------
    # 结算（唯一写入口）
    # ------------------------------------------------------------------
    async def record_event(self, event: UsageEventRecord) -> bool:
        """写入一条用量事件（幂等）并累加三行聚合。

        `OA_USAGE_WRITE_ENABLED=false` 时**不写任何行**并返回 False（D-12 回滚口径）。

        Args:
            event: 用量事件。

        Returns:
            bool: 首次写入返回 True；重放或写入开关关闭返回 False。
        """
        if not self._settings.usage_write_enabled:
            logger.info(
                "usage_write_disabled",
                capability=event.capability,
                idem_key=event.idem_key,
            )
            return False
        period = event.period or current_period()
        department_id = event.department_id
        if not department_id:
            department_id = await self._directory.primary_department(event.tenant_id, event.user_id)
        record = UsageEventRecord(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            capability=event.capability,
            idem_key=event.idem_key,
            units=event.normalized_units,
            period=period,
            source=event.source,
            org_id=event.org_id,
            department_id=department_id,
            resource_type=event.resource_type,
            resource_id=event.resource_id or event.idem_key,
            event_id=event.event_id or new_resource_id(USAGE_EVENT_PREFIX),
            occurred_at=event.occurred_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        inserted = await self._store.append(record)
        logger.info(
            "usage_recorded",
            capability=record.capability,
            units=record.units,
            source=record.source,
            period=record.period,
            inserted=inserted,
            tenant_id=record.tenant_id,
        )
        return inserted

    async def record_generation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        generation_id: str,
        units: int,
        source: str = SOURCE_SETTLED,
        has_authorized_bases: bool = False,
    ) -> bool:
        """结算一次对话生成（M1/M2）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            generation_id: 生成记录 ID（幂等键）。
            units: 结算额度。
            source: 结算来源（`settled` / `estimated`）。
            has_authorized_bases: 本次是否使用知识库（决定 `doc_qa` 与 `chat` 口径）。

        Returns:
            bool: 首次写入返回 True。
        """
        return await self.record_event(
            UsageEventRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                capability=CAPABILITY_DOC_QA if has_authorized_bases else CAPABILITY_CHAT,
                idem_key=generation_id,
                units=units,
                source=source,
                resource_type=RESOURCE_GENERATION,
                resource_id=generation_id,
            )
        )

    async def record_document_processed(
        self,
        *,
        tenant_id: str,
        user_id: str,
        document_id: str,
        run_id: str,
        units: int,
    ) -> bool:
        """结算一次文档处理（R3/R5 完成路径，`doc_process` 事件）。

        幂等键 = `document_id:run_id`（D-12 第 2 条），R5 重新处理换新批次才产生新事件。

        Args:
            tenant_id: 租户 ID。
            user_id: 上传者用户 ID。
            document_id: 文档 ID。
            run_id: 处理批次号。
            units: 结算额度（按文档字符数折算）。

        Returns:
            bool: 首次写入返回 True。
        """
        return await self.record_event(
            UsageEventRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                capability=CAPABILITY_DOC_PROCESS,
                idem_key=f"{document_id}:{run_id or 'run0'}",
                units=units,
                source=SOURCE_SETTLED,
                resource_type=RESOURCE_DOCUMENT,
                resource_id=document_id,
            )
        )

    async def record_automation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        units: int,
        source: str = SOURCE_SETTLED,
    ) -> bool:
        """结算一次自动化任务（A1 完成路径，`automation` 事件）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            task_id: 任务 ID（幂等键）。
            units: 结算额度。
            source: 结算来源。

        Returns:
            bool: 首次写入返回 True。
        """
        return await self.record_event(
            UsageEventRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                capability=CAPABILITY_AUTOMATION,
                idem_key=task_id,
                units=units,
                source=source,
                resource_type=RESOURCE_TASK,
                resource_id=task_id,
            )
        )

    # ------------------------------------------------------------------
    # 折算（D-12 第 3 条）
    # ------------------------------------------------------------------
    def estimate_document_units(self, *, file_size: int, segment_count: int = 0) -> int:
        """按文档大小折算 `doc_process` 额度。

        文档正文的精确字符数只有解析完成后才有，本阶段按 UTF-8 字节数估算
        （中文约 3 字节/字），换算为「字数额度」；文件大小缺失时退化为片段数。

        Args:
            file_size: 文档大小（字节）。
            segment_count: 内容片段数（兜底口径）。

        Returns:
            int: 折算额度（`>= 0`）。
        """
        chars_per_unit = max(int(self._settings.usage_chars_per_unit), _MIN_CHARS_PER_UNIT)
        if file_size > 0:
            estimated_chars = math.ceil(file_size / _UTF8_BYTES_PER_CJK_CHAR)
            return math.ceil(estimated_chars / chars_per_unit)
        return max(int(segment_count), 0)

    def estimate_interrupted_units(
        self,
        *,
        produced_text: str = "",
        produced_chars: int | None = None,
        prompt_units: int | None = None,
    ) -> int:
        """按已产出内容折算中断时的额度。

        公式（架构文档 2.5 第 3 条）：
        `units = prompt_units + ceil(len(已产出文本) / OA_USAGE_CHARS_PER_UNIT)`；
        `prompt_units` 未知时按 `OA_USAGE_ESTIMATE_PROMPT_UNITS` 兜底。

        Args:
            produced_text: 已下发给用户的正文（与 `produced_chars` 二选一）。
            produced_chars: 已产出字符数（流式路径按增量累计，避免拼接长文本）。
            prompt_units: 已知的输入额度（None 表示未知）。

        Returns:
            int: 折算额度（`>= 0`；无产出且无 prompt 用量时为 0，调用方据此跳过结算）。
        """
        chars_per_unit = max(int(self._settings.usage_chars_per_unit), _MIN_CHARS_PER_UNIT)
        produced = (
            len(produced_text or "") if produced_chars is None else max(int(produced_chars), 0)
        )
        completion = math.ceil(produced / chars_per_unit) if produced else 0
        if prompt_units is None:
            prompt = int(self._settings.usage_estimate_prompt_units) if produced else 0
        else:
            prompt = max(int(prompt_units), 0)
        return prompt + completion

    # ------------------------------------------------------------------
    # 快照（M4 / BL5 同一段计算）
    # ------------------------------------------------------------------
    async def snapshot(self, *, tenant_id: str, user_id: str, period: str = "") -> UsageSnapshot:
        """取个人用量快照（M4 与 BL5 个人视图的**唯一**数据来源）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            period: 计量周期 `YYYY-MM`；空串取当前 UTC 月。

        Returns:
            UsageSnapshot: 用量快照（`used_units` 与 `quota_units_used` 恒等）。
        """
        effective_period = period or current_period()
        rows = await self._store.monthly_rows(tenant_id, effective_period)
        used = sum(
            row.units for row in rows if row.scope_type == SCOPE_MEMBER and row.scope_id == user_id
        )
        quota = await self._store.quota(tenant_id)
        breakdown = self._capability_breakdown(rows=rows, scope_id=user_id, total=quota.total_units)
        trend = await self._daily_trend(
            tenant_id, effective_period, scope_type=SCOPE_MEMBER, scope_id=user_id
        )
        counts = await self._store.event_counts(tenant_id, user_id, effective_period)
        return UsageSnapshot(
            tenant_id=tenant_id,
            period=effective_period,
            used_units=used,
            total_units=quota.total_units,
            remaining_units=max(quota.total_units - used, 0),
            usage_percent=_percent(used, quota.total_units),
            over_quota_warning=bool(quota.total_units) and used >= quota.total_units,
            quota_unit=quota.quota_unit_label,
            plan_name=quota.plan_name,
            plan_code=quota.plan_code,
            breakdown=tuple(breakdown),
            daily_trend=tuple(trend),
            event_counts=counts,
        )

    async def tenant_snapshot(
        self, *, tenant_id: str, period: str = "", group_by: str = ""
    ) -> UsageSnapshot:
        """取企业维度用量快照（BL5 管理视图）。

        管理视图的 `used_units` 读 `usage_monthly(scope_type='tenant')` 行，
        而该行与成员行**由同一事件同一事务写入**，故「按成员求和 == 企业总量」结构成立。

        Args:
            tenant_id: 租户 ID。
            period: 计量周期。
            group_by: 分组维度（`member` / `department` / `capability`；空串不分项）。

        Returns:
            UsageSnapshot: 企业维度用量快照。
        """
        effective_period = period or current_period()
        rows = await self._store.monthly_rows(tenant_id, effective_period)
        used = sum(
            row.units
            for row in rows
            if row.scope_type == SCOPE_TENANT and row.scope_id == tenant_id
        )
        quota = await self._store.quota(tenant_id)
        breakdown = await self._group_breakdown(
            tenant_id=tenant_id, rows=rows, group_by=group_by, total=quota.total_units
        )
        trend = await self._daily_trend(tenant_id, effective_period, scope_type=SCOPE_TENANT)
        counts = await self._store.event_counts(tenant_id, "", effective_period)
        return UsageSnapshot(
            tenant_id=tenant_id,
            period=effective_period,
            used_units=used,
            total_units=quota.total_units,
            remaining_units=max(quota.total_units - used, 0),
            usage_percent=_percent(used, quota.total_units),
            over_quota_warning=bool(quota.total_units) and used >= quota.total_units,
            quota_unit=quota.quota_unit_label,
            plan_name=quota.plan_name,
            plan_code=quota.plan_code,
            breakdown=tuple(breakdown),
            daily_trend=tuple(trend),
            event_counts=counts,
        )

    async def member_total(self, *, tenant_id: str, period: str = "") -> int:
        """按成员行求和（对账用：应与企业总量**严格相等**）。

        Args:
            tenant_id: 租户 ID。
            period: 计量周期。

        Returns:
            int: 全部成员行之和。
        """
        rows = await self._store.monthly_rows(tenant_id, period or current_period())
        return sum(row.units for row in rows if row.scope_type == SCOPE_MEMBER)

    async def tenant_total(self, *, tenant_id: str, period: str = "") -> int:
        """企业维度总量（对账用）。

        Args:
            tenant_id: 租户 ID。
            period: 计量周期。

        Returns:
            int: `scope_type='tenant'` 行之和。
        """
        rows = await self._store.monthly_rows(tenant_id, period or current_period())
        return sum(
            row.units
            for row in rows
            if row.scope_type == SCOPE_TENANT and row.scope_id == tenant_id
        )

    async def event_total(self, *, tenant_id: str, period: str = "") -> int:
        """事件表额度总和（对账基准）。

        Args:
            tenant_id: 租户 ID。
            period: 计量周期。

        Returns:
            int: 事件表额度总和。
        """
        return await self._store.event_units_total(tenant_id, period or current_period())

    async def range_stats(self, *, tenant_id: str, start_at: str, end_at: str) -> RangeEventStats:
        """统计时间区间内的事件级指标（运营后台 OPS6，B10）。

        **不另设口径**：额度仍来自同一张 `usage_event` 表，
        因此「各企业区间额度求和」与「BL5 企业维度按账期求和」在整月区间上误差 0。

        Args:
            tenant_id: 租户 ID。
            start_at: 起始时间（ISO 8601，含）。
            end_at: 结束时间（ISO 8601，不含）。

        Returns:
            RangeEventStats: 区间统计。
        """
        return await self._store.range_stats(tenant_id, start_at, end_at)

    # ------------------------------------------------------------------
    # 内部分项计算
    # ------------------------------------------------------------------
    def _capability_breakdown(
        self, *, rows: list[Any], scope_id: str, total: int
    ) -> list[UsageBreakdownItem]:
        """按能力分组构造分项（个人视图）。

        Args:
            rows: 聚合行。
            scope_id: 用户 ID。
            total: 额度总量。

        Returns:
            list[UsageBreakdownItem]: 分项列表（用量倒序）。
        """
        grouped: dict[str, int] = {}
        for row in rows:
            if row.scope_type != SCOPE_MEMBER or row.scope_id != scope_id:
                continue
            grouped[row.capability] = grouped.get(row.capability, 0) + row.units
        items = [
            UsageBreakdownItem(
                key=capability,
                display_name=CAPABILITY_DISPLAY_NAMES.get(capability, capability),
                units=units,
                percent=_percent(units, total),
            )
            for capability, units in grouped.items()
        ]
        items.sort(key=lambda item: (-item.units, item.key))
        return items

    async def _group_breakdown(
        self, *, tenant_id: str, rows: list[Any], group_by: str, total: int
    ) -> list[UsageBreakdownItem]:
        """按维度分组构造分项（管理视图）。

        Args:
            tenant_id: 租户 ID。
            rows: 聚合行。
            group_by: 维度（`member` / `department` / `capability`）。
            total: 额度总量。

        Returns:
            list[UsageBreakdownItem]: 分项列表（用量倒序）。
        """
        if not group_by:
            return []
        if group_by == "member":
            grouped: dict[str, int] = {}
            for row in rows:
                if row.scope_type != SCOPE_MEMBER:
                    continue
                grouped[row.scope_id] = grouped.get(row.scope_id, 0) + row.units
            items: list[UsageBreakdownItem] = []
            for member_id, units in grouped.items():
                name = await self._directory.display_name(tenant_id, member_id)
                items.append(
                    UsageBreakdownItem(
                        key=member_id,
                        display_name=name,
                        units=units,
                        percent=_percent(units, total),
                    )
                )
        elif group_by == "department":
            department_rows: dict[str, int] = {}
            for row in rows:
                if row.scope_type != SCOPE_DEPARTMENT:
                    continue
                department_rows[row.scope_id] = department_rows.get(row.scope_id, 0) + row.units
            items = [
                UsageBreakdownItem(
                    key=department_id,
                    display_name="",
                    units=units,
                    percent=_percent(units, total),
                )
                for department_id, units in department_rows.items()
            ]
        else:
            grouped = {}
            for row in rows:
                if row.scope_type != SCOPE_TENANT:
                    continue
                grouped[row.capability] = grouped.get(row.capability, 0) + row.units
            items = [
                UsageBreakdownItem(
                    key=capability,
                    display_name=CAPABILITY_DISPLAY_NAMES.get(capability, capability),
                    units=units,
                    percent=_percent(units, total),
                )
                for capability, units in grouped.items()
            ]
        items.sort(key=lambda item: (-item.units, item.key))
        return items

    async def _daily_trend(
        self, tenant_id: str, period: str, *, scope_type: str, scope_id: str = ""
    ) -> list[UsageDailyPoint]:
        """构造按自然日的趋势点。

        Args:
            tenant_id: 租户 ID。
            period: 计量周期。
            scope_type: 维度。
            scope_id: 用户 ID（成员维度）。

        Returns:
            list[UsageDailyPoint]: 按日期升序的趋势点。
        """
        totals = await self._store.daily_totals(
            tenant_id, period, scope_type=scope_type, scope_id=scope_id
        )
        return [
            UsageDailyPoint(date=day, units=units) for day, units in sorted(totals.items()) if day
        ]


def _default_directory(settings: Settings) -> UsageDirectory:
    """构造缺省组织信息解析器。

    延迟导入组织仓储（B08 落地后自动接管部门归属与成员展示名），
    导入不可用时回落到空实现——B07 与 B08 之间不需要改动调用点。

    Args:
        settings: 配置实例。

    Returns:
        UsageDirectory: 组织信息解析器。
    """
    try:
        from app.db.repositories.org_repo import get_org_directory

        directory: UsageDirectory = get_org_directory(settings)
        return directory
    except Exception:  # pragma: no cover - 组织域未落地时走空实现
        return NullUsageDirectory()


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: UsageService | None = None


def get_usage_service(settings: Settings | None = None) -> UsageService:
    """获取计量服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        UsageService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = UsageService(settings)
    return _service


def reset_usage_service() -> None:
    """重置计量服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "CAPABILITY_DISPLAY_NAMES",
    "RESOURCE_DOCUMENT",
    "RESOURCE_GENERATION",
    "RESOURCE_TASK",
    "USAGE_EVENT_PREFIX",
    "NullUsageDirectory",
    "RangeEventStats",
    "UsageBreakdownItem",
    "UsageDailyPoint",
    "UsageDirectory",
    "UsageService",
    "UsageSnapshot",
    "get_usage_service",
    "reset_usage_service",
]
