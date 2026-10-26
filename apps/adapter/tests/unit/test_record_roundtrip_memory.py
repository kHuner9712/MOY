"""内存后端 record 往返验收（切片 2C-5a 第 3 项）。

**覆盖什么**：写侧 `store.<write>(record)` → 读侧 `store.<read>()` 回来后，
`asdict(read)` 必须与写入值**逐字段相等**。R4 只断言"仓储单元有往返测试"，
不检查字段覆盖，所以 `settings` / `inputs` / `bank_info` / `checklist` 这类
嵌套结构整个丢失时，旧用例仍会绿。

**派生字段必须显式登记**：`created_at` / `installed_at` / `expires_at` 这类由
store 补默认值的字段，允许与写入值不同，但**必须通过该字段自己的合法性断言**
（非空 / 正数），且比对时用"读回值回填后再比"。**不允许**用"不认识的字段一律
跳过"的方式通过——那样 store 少写一个字段永远不会被发现。

**一条已知缺口（显式文档化，不假装覆盖）**：`UsageEventRecord` 是纯输入 DTO。
内存与 PG 两侧都不构造它，`usage/postgres.py` 也没有 `_to_*` 映射器，读侧只暴露
聚合（`monthly_rows` / `daily_totals` / `event_counts` / `event_units_total` /
`range_stats`）。因此本文件对它**不做原行往返**，只断言"写入值确实进了聚合"，
**明确不覆盖原行字段**。若将来需要原行级覆盖，正确做法是给读侧加只读出口
（属实现改动，另立切片）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

import pytest

from app.db.repositories.billing import (
    CallbackLogRecord,
    InMemoryBillingStore,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    default_plans,
)
from app.db.repositories.org import DepartmentRecord, InMemoryOrgStore, MemberLink
from app.db.repositories.plugin import InMemoryPluginStore, InstallationRecord
from app.db.repositories.role import (
    PERMISSION_CATALOG_SEED,
    InMemoryRoleStore,
    PermissionCatalogEntry,
)
from app.db.repositories.task import InMemoryTaskStore, StepRecord, TaskRecord
from app.db.repositories.usage import InMemoryUsageStore, UsageEventRecord

pytestmark = pytest.mark.unit

TENANT = "tnt_roundtrip"
USER = "usr_roundtrip"
PLUGIN = "plg_roundtrip"
PERIOD = "2026-10"

#: 派生字段的合法性断言（store 允许改写该字段，但必须落在合法形态里）。
DerivedCheck = Callable[[Any], None]


def _filled(value: Any) -> None:
    """派生时间戳必须是非空字符串。

    Args:
        value: 读回的字段值。
    """
    assert isinstance(value, str) and value, f"派生时间戳未被填充：{value!r}"


@dataclass(frozen=True, slots=True)
class RoundTripCase:
    """一条往返用例：哪个单元的哪个 record、怎么写、怎么读、哪些字段属派生。"""

    unit: str
    record: type
    build: Callable[[], Awaitable[tuple[Any, Any]]]
    derived: Mapping[str, DerivedCheck]


async def _plan() -> tuple[PlanRecord, Any]:
    """套餐：内存种子整体与 `default_plans()` 逐条一致，再取首行做往返。"""
    store = InMemoryBillingStore()
    expected = sorted(default_plans(), key=lambda item: (item.sort_order, item.plan_code))
    read = await store.list_plans()
    assert len(read) == len(expected) == 3, f"套餐条数不一致：读 {len(read)} / 种子 {len(expected)}"
    for seeded, listed in zip(expected, read, strict=True):
        assert asdict(listed) == asdict(seeded), (
            f"{seeded.plan_code} 内存种子与 default_plans 不一致"
        )
    return expected[0], read[0]


async def _subscription() -> tuple[SubscriptionRecord, Any]:
    """订阅：写入后按企业读有效订阅。"""
    store = InMemoryBillingStore()
    record = SubscriptionRecord(
        tenant_id=TENANT,
        subscription_id="sub_roundtrip",
        plan_code="professional",
        seats_total=5,
        seats_used=2,
        current_period_start="2026-10-01",
        current_period_end="2026-11-01",
        auto_renew=False,
        pending_plan_code="enterprise",
        pending_change_at="2026-11-01T00:00:00Z",
        grace_until="2026-11-08T00:00:00Z",
    )
    await store.save_subscription(record)
    return record, await store.get_active_subscription(TENANT)


async def _order() -> tuple[OrderRecord, Any]:
    """订单：写入后按 ID 读回。"""
    store = InMemoryBillingStore()
    record = OrderRecord(
        tenant_id=TENANT,
        order_id="ord_roundtrip",
        order_type="renew",
        plan_code="professional",
        seats=7,
        amount_cents=27300,
        status="pending_payment",
        payment_method="transfer",
        pay_url="https://pay.example.com/ord_roundtrip",
        bank_info={"bank": "ICBC", "account": "6222****1234"},
        payment_no_masked="6222****1234",
        expires_at="2026-10-27T00:00:00Z",
        created_by=USER,
    )
    await store.create_order(record)
    return record, await store.get_order(TENANT, "ord_roundtrip")


async def _invoice() -> tuple[InvoiceRecord, Any]:
    """账单：一单一票，按订单读回。"""
    store = InMemoryBillingStore()
    record = InvoiceRecord(
        tenant_id=TENANT,
        invoice_id="inv_roundtrip",
        order_id="ord_roundtrip",
        period=PERIOD,
        amount_cents=27300,
        status="issued",
        download_path="invoices/inv_roundtrip.pdf",
        issued_at="2026-10-26T10:00:00Z",
    )
    await store.ensure_invoice(record)
    return record, await store.get_invoice_by_order(TENANT, "ord_roundtrip")


async def _callback() -> tuple[CallbackLogRecord, Any]:
    """支付回调留痕：按渠道 + 流水号读回。"""
    store = InMemoryBillingStore()
    record = CallbackLogRecord(
        tenant_id=TENANT,
        callback_id="cb_roundtrip",
        channel="wechat_pay",
        external_txn_id="txn_20261026",
        order_id="ord_roundtrip",
        amount_cents=27300,
        signature_ok=True,
        payload_sha256="a" * 64,
        processed=True,
    )
    assert await store.append_callback(record) is True, "首次写回调应返回 True"
    return record, await store.get_callback("wechat_pay", "txn_20261026")


async def _department() -> tuple[DepartmentRecord, Any]:
    """部门：写入后按 ID 读回。"""
    store = InMemoryOrgStore()
    record = DepartmentRecord(
        tenant_id=TENANT,
        department_id="dep_roundtrip",
        org_id="org_root",
        name="研发中心",
        path="/研发中心",
        level=2,
        manager_user_id=USER,
        member_count=3,
        sort_order=7,
    )
    await store.create_department(record)
    return record, await store.get_department(TENANT, "dep_roundtrip")


async def _member_link() -> tuple[MemberLink, Any]:
    """成员-部门关联：`is_primary` 是易丢字段，必须原样读回。"""
    store = InMemoryOrgStore()
    record = MemberLink(
        tenant_id=TENANT,
        department_id="dep_roundtrip",
        user_id=USER,
        is_primary=True,
    )
    await store.link_member(record)
    links = await store.links_of_user(TENANT, USER)
    assert len(links) == 1, f"关联条数应为 1，实际 {len(links)}"
    return record, links[0]


async def _installation() -> tuple[InstallationRecord, Any]:
    """插件安装：`settings` 是嵌套 dict，整个丢失时旧用例不会报。"""
    store = InMemoryPluginStore()
    record = InstallationRecord(
        tenant_id=TENANT,
        plugin_id=PLUGIN,
        version="2.3.1",
        previous_version="2.2.0",
        settings={"timezone": "Asia/Shanghai", "max_items": 10},
        capability_version=3,
        installed_by=USER,
        enabled_at="2026-10-26T09:00:00Z",
        last_used_at="2026-10-26T09:30:00Z",
    )
    await store.create_installation(record)
    return record, await store.get_installation(TENANT, PLUGIN)


async def _task() -> tuple[TaskRecord, Any]:
    """任务：`inputs` / `checklist` / `warnings` 三个复合字段都要原样读回。"""
    store = InMemoryTaskStore()
    record = TaskRecord(
        tenant_id=TENANT,
        task_id="task_roundtrip",
        plugin_id=PLUGIN,
        plugin_version="2.3.1",
        conversation_id="conv_roundtrip",
        initiator_id=USER,
        trigger_type="schedule",
        status="running",
        progress=40,
        current_step_seq=2,
        queue_position=1,
        inputs={"url": "https://example.com/a", "depth": 2},
        checklist=[{"title": "抓取", "done": True}, {"title": "汇总", "done": False}],
        warnings=["超时重试 1 次"],
        retry_of="task_previous",
        from_step=1,
        confirm_step_seq=2,
        confirmed_step_seq=2,
        usage_units=12,
        idem_key="idem_roundtrip",
    )
    await store.create_task(record)
    return record, await store.get_task(TENANT, "task_roundtrip")


async def _step() -> tuple[StepRecord, Any]:
    """步骤：全部字段由调用方给出，因此不允许任何字段被改写。"""
    store = InMemoryTaskStore()
    record = StepRecord(
        tenant_id=TENANT,
        step_id="stp_roundtrip",
        task_id="task_roundtrip",
        seq=1,
        title="抓取页面",
        action="fetch_page",
        status="done",
        attempt=2,
        retryable=True,
        output_summary="抓到 12 条",
        operator_action="confirm",
        tool_execution_id="toolx_roundtrip",
        duration_ms=880,
        started_at="2026-10-26T09:00:00Z",
        finished_at="2026-10-26T09:01:00Z",
    )
    await store.create_steps([record])
    steps = await store.list_steps(TENANT, "task_roundtrip")
    assert len(steps) == 1, f"步骤条数应为 1，实际 {len(steps)}"
    return record, steps[0]


async def _permission() -> tuple[PermissionCatalogEntry, Any]:
    """权限点目录：整表与种子逐条一致，再取首条做往返。"""
    store = InMemoryRoleStore()
    expected = sorted(PERMISSION_CATALOG_SEED, key=lambda item: item.sort_order)
    read = await store.permission_catalog(TENANT)
    assert [asdict(item) for item in read] == [asdict(item) for item in expected], (
        "权限目录与 PERMISSION_CATALOG_SEED 逐条不相等"
    )
    return expected[0], read[0]


#: 11 条真往返。`usage` 不在此表（无读回路径，见 `test_usage_aggregate_only_and_case_table`）。
CASES: tuple[RoundTripCase, ...] = (
    RoundTripCase("billing", PlanRecord, _plan, {}),
    RoundTripCase(
        "billing", SubscriptionRecord, _subscription, {"created_at": _filled, "updated_at": _filled}
    ),
    RoundTripCase("billing", OrderRecord, _order, {"created_at": _filled, "updated_at": _filled}),
    RoundTripCase("billing", InvoiceRecord, _invoice, {"created_at": _filled}),
    RoundTripCase("billing", CallbackLogRecord, _callback, {"received_at": _filled}),
    RoundTripCase(
        "org", DepartmentRecord, _department, {"created_at": _filled, "updated_at": _filled}
    ),
    RoundTripCase("org", MemberLink, _member_link, {"joined_at": _filled}),
    RoundTripCase("plugin", InstallationRecord, _installation, {"installed_at": _filled}),
    RoundTripCase("task", TaskRecord, _task, {"started_at": _filled, "expires_at": _filled}),
    RoundTripCase("task", StepRecord, _step, {}),
    RoundTripCase("role", PermissionCatalogEntry, _permission, {}),
)

#: 用例表必须覆盖的 record 清单（少一个就会被 `test_usage_aggregate_only_and_case_table` 抓住）。
EXPECTED_RECORDS = frozenset(
    {
        ("billing", "PlanRecord"),
        ("billing", "SubscriptionRecord"),
        ("billing", "OrderRecord"),
        ("billing", "InvoiceRecord"),
        ("billing", "CallbackLogRecord"),
        ("org", "DepartmentRecord"),
        ("org", "MemberLink"),
        ("plugin", "InstallationRecord"),
        ("task", "TaskRecord"),
        ("task", "StepRecord"),
        ("role", "PermissionCatalogEntry"),
    }
)


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{item.unit}-{item.record.__name__}" for item in CASES]
)
async def test_record_roundtrip(case: RoundTripCase) -> None:
    """写后读回必须逐字段一致；派生字段允许改写但必须合法填充。

    Args:
        case: 待验证的往返用例。
    """
    built, read = await case.build()
    assert read is not None, f"{case.record.__name__} 写后读不回"
    assert type(read) is case.record, f"读回类型不符：{type(read).__name__}"

    names = {field.name for field in fields(case.record)}
    unknown = set(case.derived) - names
    assert not unknown, f"派生字段表里写了不存在的字段：{sorted(unknown)}"

    for name, check in case.derived.items():
        check(getattr(read, name))

    # 派生字段用读回值回填成"期望值"，其余字段必须与写入值逐字相等。
    expected = replace(built, **{name: getattr(read, name) for name in case.derived})
    written = asdict(built)
    returned = asdict(read)
    reference = asdict(expected)
    diffs = {
        name: {"写入": written[name], "读回": returned[name]}
        for name in written
        if returned[name] != reference[name]
    }
    assert not diffs, f"{case.record.__name__} 往返丢字段：{diffs}"


async def test_usage_aggregate_only_and_case_table() -> None:
    """用量只覆盖"写入值进了聚合"（原行字段文档化缺口）+ 用例表完整性自检。

    完整性自检放在这里，是让"少写一个 case 就变绿"不可能：表里少一项，本用例会红。
    """
    covered = {(case.unit, case.record.__name__) for case in CASES}
    assert covered == EXPECTED_RECORDS, (
        f"往返用例表与清单不一致：缺 {sorted(EXPECTED_RECORDS - covered)}，"
        f"多 {sorted(covered - EXPECTED_RECORDS)}"
    )

    store = InMemoryUsageStore()
    event = UsageEventRecord(
        tenant_id=TENANT,
        user_id=USER,
        capability="chat.completion",
        idem_key="idem_usage_roundtrip",
        units=7,
        period=PERIOD,
        department_id="dep_roundtrip",
        provider="dashscope",
        prompt_tokens=120,
        completion_tokens=30,
    )
    assert await store.append(event) is True, "首次写事件应返回 True"
    assert await store.append(event) is False, "同幂等键重复写应返回 False"

    # 聚合等价：写入的 1 条事件、7 额度必须体现在聚合读侧。
    assert await store.event_counts(TENANT, USER, PERIOD) == {"chat.completion": 1}
    assert await store.event_units_total(TENANT, PERIOD) == event.normalized_units
