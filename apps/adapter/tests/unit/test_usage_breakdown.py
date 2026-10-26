"""用量聚合与跨月隔离补测（阶段二 B07/B10 + D14-1 补测）。

补齐的分支（此前 0 覆盖）：

- `UsageService.tenant_snapshot(group_by=member|department|capability)` 三条分项路径；
- **跨月不串账**：同一租户在 `2026-10` 与 `2026-11` 的事件必须各自归属自己的账期，
  企业总量 / 成员分项 / 能力条数 / 每日趋势都不能互相渗透；
- `UsageService.settings` / `store` 属性访问器；
- `UsageService.record_event(period=...)` 显式指定账期（缺省取当前 UTC 月）。

> 账期口径是 BL5 / M4 / OPS6 三处数字一致的根基，跨月串账会导致
> 「上个月用量被算进本月」→ 额度提前耗尽或超额未提示。
> 只加用例不改实现（D14-1 约束）。
"""

from __future__ import annotations

import pytest

from app.business.usage_service import UsageService
from app.core.config import Settings
from app.db.repositories.usage import (
    CAPABILITY_CHAT,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_DOC_QA,
    InMemoryUsageStore,
    UsageEventRecord,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_usage_month"

#: 上期与本期（固定字符串，避免「跑在月初/月末」导致的断言漂移）
PREV_PERIOD = "2026-10"
CURR_PERIOD = "2026-11"


class _StubDirectory:
    """固定返回主部门与展示名的组织解析器（隔离组织域依赖）。"""

    def __init__(self, departments: dict[str, str], names: dict[str, str]) -> None:
        """初始化。

        Args:
            departments: `user_id → department_id`。
            names: `user_id → display_name`。
        """
        self._departments = departments
        self._names = names

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        """取主部门。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 部门 ID；未登记返回空串。
        """
        del tenant_id
        return self._departments.get(user_id, "")

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        """取展示名。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 展示名；未登记返回空串。
        """
        del tenant_id
        return self._names.get(user_id, "")


def _settings() -> Settings:
    """构造测试配置。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
        usage_write_enabled=True,
    )


def _service() -> tuple[UsageService, InMemoryUsageStore]:
    """构造使用独立内存存储的计量服务。

    Returns:
        tuple[UsageService, InMemoryUsageStore]: 服务与存储。
    """
    settings = _settings()
    store = InMemoryUsageStore()
    directory = _StubDirectory(
        departments={"usr_1": "dept_rd", "usr_2": "dept_ops"},
        names={"usr_1": "张三", "usr_2": "李四"},
    )
    return UsageService(settings, store=store, directory=directory), store


async def _append(
    store: InMemoryUsageStore,
    *,
    user_id: str,
    capability: str,
    idem_key: str,
    units: int,
    period: str,
    occurred_at: str,
) -> None:
    """直接写一条用量事件（绕过业务链路，构造聚合数据）。

    Args:
        store: 内存计量存储。
        user_id: 用户 ID。
        capability: 能力。
        idem_key: 幂等键。
        units: 额度。
        period: 账期。
        occurred_at: 发生时间（ISO 8601）。
    """
    await store.append(
        UsageEventRecord(
            tenant_id=TENANT,
            user_id=user_id,
            capability=capability,
            idem_key=idem_key,
            units=units,
            period=period,
            event_id=f"use_{idem_key}",
            occurred_at=occurred_at,
        )
    )


@pytest.mark.asyncio
@pytest.mark.p0
async def test_cross_month_usage_is_not_mixed() -> None:
    """跨月不串账：10 月与 11 月的事件各自归属自己的账期（含分项与条数）。"""
    service, store = _service()
    await _append(
        store,
        user_id="usr_1",
        capability=CAPABILITY_CHAT,
        idem_key="oct-1",
        units=1_000,
        period=PREV_PERIOD,
        occurred_at="2026-10-15T09:00:00Z",
    )
    await _append(
        store,
        user_id="usr_1",
        capability=CAPABILITY_CHAT,
        idem_key="nov-1",
        units=300,
        period=CURR_PERIOD,
        occurred_at="2026-11-02T09:00:00Z",
    )
    await _append(
        store,
        user_id="usr_2",
        capability=CAPABILITY_DOC_PROCESS,
        idem_key="nov-2",
        units=50,
        period=CURR_PERIOD,
        occurred_at="2026-11-03T09:00:00Z",
    )

    october = await service.tenant_snapshot(tenant_id=TENANT, period=PREV_PERIOD)
    november = await service.tenant_snapshot(tenant_id=TENANT, period=CURR_PERIOD)
    assert october.used_units == 1_000
    assert november.used_units == 350
    assert await service.tenant_total(tenant_id=TENANT, period=PREV_PERIOD) == 1_000
    assert await service.tenant_total(tenant_id=TENANT, period=CURR_PERIOD) == 350
    assert await service.member_total(tenant_id=TENANT, period=PREV_PERIOD) == 1_000
    assert await service.member_total(tenant_id=TENANT, period=CURR_PERIOD) == 350
    assert await service.event_total(tenant_id=TENANT, period=PREV_PERIOD) == 1_000

    prev_counts = await store.event_counts(TENANT, "", PREV_PERIOD)
    curr_counts = await store.event_counts(TENANT, "", CURR_PERIOD)
    assert prev_counts == {CAPABILITY_CHAT: 1}
    assert curr_counts == {CAPABILITY_CHAT: 1, CAPABILITY_DOC_PROCESS: 1}

    assert [point.date for point in october.daily_trend] == ["2026-10-15"]
    assert [point.date for point in november.daily_trend] == ["2026-11-02", "2026-11-03"]


@pytest.mark.asyncio
async def test_tenant_snapshot_breakdown_by_member() -> None:
    """`group_by=member`：按成员求和等于企业总量，展示名来自组织解析器。"""
    service, store = _service()
    await _append(
        store,
        user_id="usr_1",
        capability=CAPABILITY_CHAT,
        idem_key="m-1",
        units=120,
        period=CURR_PERIOD,
        occurred_at="2026-11-05T09:00:00Z",
    )
    await _append(
        store,
        user_id="usr_2",
        capability=CAPABILITY_CHAT,
        idem_key="m-2",
        units=80,
        period=CURR_PERIOD,
        occurred_at="2026-11-05T10:00:00Z",
    )
    snapshot = await service.tenant_snapshot(
        tenant_id=TENANT, period=CURR_PERIOD, group_by="member"
    )
    assert snapshot.used_units == 200
    assert sum(item.units for item in snapshot.breakdown) == snapshot.used_units
    names = {item.key: item.display_name for item in snapshot.breakdown}
    assert names == {"usr_1": "张三", "usr_2": "李四"}


@pytest.mark.asyncio
async def test_tenant_snapshot_breakdown_by_department() -> None:
    """`group_by=department`：按部门求和同样等于企业总量（部门来自事件写入时的主部门）。"""
    service, _store = _service()
    # 必须经 `record_event` 写入：`department_id` 由目录解析（直接 append 不会写部门行，
    # 这正是「部门维度不是凭空出现」的口径保证）。
    for user_id, idem_key, units in (("usr_1", "d-1", 70), ("usr_2", "d-2", 30)):
        await service.record_event(
            UsageEventRecord(
                tenant_id=TENANT,
                user_id=user_id,
                capability=CAPABILITY_DOC_QA,
                idem_key=idem_key,
                units=units,
                period=CURR_PERIOD,
                occurred_at="2026-11-06T09:00:00Z",
            )
        )
    snapshot = await service.tenant_snapshot(
        tenant_id=TENANT, period=CURR_PERIOD, group_by="department"
    )
    assert snapshot.used_units == 100
    assert {item.key: item.units for item in snapshot.breakdown} == {
        "dept_rd": 70,
        "dept_ops": 30,
    }


@pytest.mark.asyncio
async def test_tenant_snapshot_breakdown_by_capability() -> None:
    """`group_by=capability`：按能力分组，展示名为业务语言。"""
    service, store = _service()
    await _append(
        store,
        user_id="usr_1",
        capability=CAPABILITY_CHAT,
        idem_key="c-1",
        units=40,
        period=CURR_PERIOD,
        occurred_at="2026-11-07T09:00:00Z",
    )
    await _append(
        store,
        user_id="usr_2",
        capability=CAPABILITY_DOC_PROCESS,
        idem_key="c-2",
        units=60,
        period=CURR_PERIOD,
        occurred_at="2026-11-07T10:00:00Z",
    )
    snapshot = await service.tenant_snapshot(
        tenant_id=TENANT, period=CURR_PERIOD, group_by="capability"
    )
    display = {item.display_name: item.units for item in snapshot.breakdown}
    assert display == {"智能对话": 40, "文档处理": 60}


@pytest.mark.asyncio
async def test_tenant_snapshot_without_group_by_has_empty_breakdown() -> None:
    """不传 `group_by` 时只返回总量，分项为空（不虚构分项）。"""
    service, store = _service()
    await _append(
        store,
        user_id="usr_1",
        capability=CAPABILITY_CHAT,
        idem_key="n-1",
        units=10,
        period=CURR_PERIOD,
        occurred_at="2026-11-08T09:00:00Z",
    )
    snapshot = await service.tenant_snapshot(tenant_id=TENANT, period=CURR_PERIOD)
    assert snapshot.used_units == 10
    assert snapshot.breakdown == ()


@pytest.mark.asyncio
async def test_record_event_honours_explicit_period() -> None:
    """`record_event` 显式账期优先于「当前 UTC 月」（补测：此前只覆盖缺省分支）。"""
    service, store = _service()
    inserted = await service.record_event(
        UsageEventRecord(
            tenant_id=TENANT,
            user_id="usr_1",
            capability=CAPABILITY_CHAT,
            idem_key="explicit-period",
            units=25,
            period=PREV_PERIOD,
        )
    )
    assert inserted is True
    assert await service.tenant_total(tenant_id=TENANT, period=PREV_PERIOD) == 25
    assert await store.event_units_total(TENANT, PREV_PERIOD) == 25


def test_service_exposes_settings_and_store() -> None:
    """`settings` / `store` 访问器返回构造时注入的实例。"""
    settings = _settings()
    store = InMemoryUsageStore()
    service = UsageService(settings, store=store)
    assert service.settings is settings
    assert service.store is store
