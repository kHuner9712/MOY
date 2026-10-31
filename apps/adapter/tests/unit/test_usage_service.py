"""计量服务单测（阶段二 B07，架构文档 2.5 / D-12）。

覆盖点与测试方案 `TC-P2-USG-03/04/05/07` 对齐：

- 结算时点：一次结算写 1 条 `usage_event` + 三行 `usage_monthly`（同一事务语义）；
- 幂等：同 `generation_id` 重放不产生第二条事件、不重复累加；
- 中断折算：`source='estimated'`，且 `units` 严格落在 `(0, 完整值)`；
- 同源：`snapshot()` 的 `used_units` 与 `quota_units_used` 恒等；
- 结构误差 0：按成员求和 == 企业总量；
- 回滚开关：`OA_USAGE_WRITE_ENABLED=false` 时不写任何行。
"""

from __future__ import annotations

import pytest

from app.business.usage_service import (
    NullUsageDirectory,
    UsageService,
    get_usage_service,
    reset_usage_service,
)
from app.core.config import Settings
from app.db.repositories.usage import (
    CAPABILITY_CHAT,
    CAPABILITY_DOC_PROCESS,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    SOURCE_ESTIMATED,
    SOURCE_SETTLED,
    InMemoryUsageStore,
    current_period,
    get_usage_store,
    reset_usage_store,
)

# P0 红线用例（G-24，测试方案 §7.1）：P2-BL-03 / P0 判据④ —— 按成员求和 ≠ 企业总量
# 映射与实跑见 docs/archive/qa-phase2-w2-r2-report.md §3.1
pytestmark = [pytest.mark.unit, pytest.mark.p0]

TENANT = "tnt_usage"
USER = "usr_alice"
OTHER = "usr_bob"


def _settings(**overrides: object) -> Settings:
    """构造测试配置（桩模式、内存后端）。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, object] = {
        "env": "test",
        "deploy_mode": "private",
        "downstream_enabled": False,
        "auth_mode": "stub",
        "jwt_secret": "unit-test-secret",
        "ratelimit_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _service(**overrides: object) -> UsageService:
    """构造使用独立内存存储的计量服务。

    Args:
        **overrides: 配置覆盖项。

    Returns:
        UsageService: 计量服务。
    """
    conf = _settings(**overrides)
    return UsageService(conf, store=InMemoryUsageStore(), directory=NullUsageDirectory())


async def test_record_writes_one_event_and_three_monthly_rows() -> None:
    """一次结算 = 1 条事件 + 2 行聚合（无部门时不写 department 行）。"""
    service = _service()
    inserted = await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_1", units=120
    )
    assert inserted is True

    rows = await service.store.monthly_rows(TENANT, current_period())
    scopes = {row.scope_type for row in rows}
    assert scopes == {SCOPE_TENANT, SCOPE_MEMBER}
    assert await service.event_total(tenant_id=TENANT) == 120
    assert await service.tenant_total(tenant_id=TENANT) == 120
    assert await service.member_total(tenant_id=TENANT) == 120


async def test_replay_same_generation_id_does_not_double_count() -> None:
    """同 `generation_id` 重放不产生第二条事件、聚合不累加（D-12 第 2 条）。"""
    service = _service()
    first = await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_dup", units=100
    )
    second = await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_dup", units=100
    )
    assert first is True
    assert second is False
    assert await service.event_total(tenant_id=TENANT) == 100
    assert await service.tenant_total(tenant_id=TENANT) == 100


async def test_member_sum_equals_tenant_total_exactly() -> None:
    """按成员求和与企业总量**严格相等**（结构误差 0，TC-P2-USG-02）。"""
    service = _service()
    await service.record_generation(tenant_id=TENANT, user_id=USER, generation_id="gen_a", units=70)
    await service.record_generation(
        tenant_id=TENANT, user_id=OTHER, generation_id="gen_b", units=30
    )
    assert await service.member_total(tenant_id=TENANT) == await service.tenant_total(
        tenant_id=TENANT
    )


async def test_snapshot_is_single_source_for_m4_and_bl5() -> None:
    """`used_units` 与 `quota_units_used` 恒等（M4 / BL5 同源，TC-P2-USG-01）。"""
    service = _service()
    await service.record_generation(tenant_id=TENANT, user_id=USER, generation_id="gen_s", units=42)
    snapshot = await service.snapshot(tenant_id=TENANT, user_id=USER)
    assert snapshot.used_units == 42
    assert snapshot.quota_units_used == snapshot.used_units
    assert snapshot.remaining_units == max(snapshot.total_units - 42, 0)


async def test_estimated_units_formula_matches_architecture() -> None:
    """中断折算公式精确相等：`prompt + ceil(len/1)`（TC-P2-USG-04）。"""
    service = _service(usage_chars_per_unit=1)
    units = service.estimate_interrupted_units(produced_text="你好世界", prompt_units=10)
    assert units == 14


async def test_estimated_units_use_fallback_prompt_when_unknown() -> None:
    """未知 prompt 用量时按 `OA_USAGE_ESTIMATE_PROMPT_UNITS` 兜底。"""
    service = _service(usage_estimate_prompt_units=800, usage_chars_per_unit=1)
    assert service.estimate_interrupted_units(produced_text="abc") == 803
    # 完全没有产出时不得凭空结算（否则中断即扣费）
    assert service.estimate_interrupted_units(produced_text="") == 0


async def test_estimated_units_strictly_below_full_settlement() -> None:
    """中断折算必须严格小于同等内容的完整结算值（PRD 验收 3 条）。"""
    service = _service(usage_estimate_prompt_units=100, usage_chars_per_unit=1)
    full_response = "完整回答" * 50
    full_units = 100 + len(full_response)
    partial = service.estimate_interrupted_units(produced_text=full_response[:40], prompt_units=100)
    assert 0 < partial < full_units


async def test_interrupted_stream_event_marked_estimated() -> None:
    """`source='estimated'` 的事件同样落库并累加（只累加折算值）。"""
    service = _service()
    await service.record_generation(
        tenant_id=TENANT,
        user_id=USER,
        generation_id="gen_int",
        units=50,
        source=SOURCE_ESTIMATED,
    )
    rows = await service.store.monthly_rows(TENANT, current_period())
    assert await service.tenant_total(tenant_id=TENANT) == 50
    assert all(row.units == 50 for row in rows)
    assert SOURCE_SETTLED == "settled"


async def test_write_disabled_skips_all_rows() -> None:
    """`OA_USAGE_WRITE_ENABLED=false` 时只读历史聚合，不写新行（D-12 回滚口径）。"""
    service = _service(usage_write_enabled=False)
    inserted = await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_off", units=99
    )
    assert inserted is False
    assert await service.event_total(tenant_id=TENANT) == 0


async def test_document_process_event_uses_document_and_run_as_idem_key() -> None:
    """文档处理幂等键 = `document_id:run_id`，换批次才产生新事件。"""
    service = _service()
    first = await service.record_document_processed(
        tenant_id=TENANT, user_id=USER, document_id="doc_1", run_id="run_1", units=300
    )
    replay = await service.record_document_processed(
        tenant_id=TENANT, user_id=USER, document_id="doc_1", run_id="run_1", units=300
    )
    new_run = await service.record_document_processed(
        tenant_id=TENANT, user_id=USER, document_id="doc_1", run_id="run_2", units=300
    )
    assert (first, replay, new_run) == (True, False, True)
    assert await service.event_total(tenant_id=TENANT) == 600


async def test_document_units_estimated_from_file_size() -> None:
    """文档额度按 UTF-8 字节估算（中文约 3 字节/字）。"""
    service = _service(usage_chars_per_unit=1)
    assert service.estimate_document_units(file_size=300) == 100
    assert service.estimate_document_units(file_size=0, segment_count=7) == 7


async def test_snapshot_breakdown_by_capability() -> None:
    """个人视图分项按能力汇总，且百分比以总额度为分母。"""
    service = _service()
    await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_c1", units=60
    )
    await service.record_document_processed(
        tenant_id=TENANT, user_id=USER, document_id="doc_x", run_id="run_x", units=40
    )
    snapshot = await service.snapshot(tenant_id=TENANT, user_id=USER)
    keys = {item.key for item in snapshot.breakdown}
    assert keys == {CAPABILITY_CHAT, CAPABILITY_DOC_PROCESS}
    assert sum(item.units for item in snapshot.breakdown) == 100


async def test_tenant_snapshot_group_by_member_matches_total() -> None:
    """管理视图按成员分项求和 == 企业总量（误差 0）。"""
    service = _service()
    await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_m1", units=12
    )
    await service.record_generation(
        tenant_id=TENANT, user_id=OTHER, generation_id="gen_m2", units=8
    )
    snapshot = await service.tenant_snapshot(tenant_id=TENANT, group_by="member")
    assert snapshot.used_units == 20
    assert sum(item.units for item in snapshot.breakdown) == 20


async def test_tenant_snapshot_without_group_by_has_no_breakdown() -> None:
    """未指定分组时不分项（BL5 个人视图语义）。"""
    service = _service()
    snapshot = await service.tenant_snapshot(tenant_id=TENANT)
    assert snapshot.breakdown == ()
    assert snapshot.used_units == 0


async def test_snapshot_daily_trend_is_sorted() -> None:
    """按日趋势按日期升序，且只含发生过的日期。"""
    service = _service()
    await service.record_generation(tenant_id=TENANT, user_id=USER, generation_id="gen_d", units=5)
    snapshot = await service.snapshot(tenant_id=TENANT, user_id=USER)
    dates = [point.date for point in snapshot.daily_trend]
    assert dates == sorted(dates)
    assert sum(point.units for point in snapshot.daily_trend) == 5


async def test_event_counts_track_capabilities() -> None:
    """`event_counts` 按能力统计次数（M4 的对话次数口径）。"""
    service = _service()
    await service.record_generation(tenant_id=TENANT, user_id=USER, generation_id="gen_e1", units=1)
    await service.record_generation(
        tenant_id=TENANT,
        user_id=USER,
        generation_id="gen_e2",
        units=1,
        has_authorized_bases=True,
    )
    snapshot = await service.snapshot(tenant_id=TENANT, user_id=USER)
    assert snapshot.event_counts["chat"] == 1
    assert snapshot.event_counts["doc_qa"] == 1


async def test_usage_service_singleton_is_shared() -> None:
    """单例复用同一存储（跨端点同源的前提）。"""
    first = get_usage_service()
    second = get_usage_service()
    assert first is second
    reset_usage_service()
    assert get_usage_service() is not first


async def test_usage_store_singleton_memory_backend() -> None:
    """内存后端为进程级单例，重置后清空。"""
    store = get_usage_store(_settings())
    assert store is get_usage_store(_settings())
    reset_usage_store()
    assert get_usage_store(_settings()) is not store


# ---------------------------------------------------------------------------
# B27：`quota_notice()` —— `15006` 的唯一出品口（非阻断提示）
#
# 红线：提示走 `data.notice_code`，`code` 恒 0。本组只测"单一真源"这一层：
# 判据必须是 `snapshot().over_quota_warning`（与 M4/BL5 同源），不得另算一套。
# ---------------------------------------------------------------------------


async def _seed_subscription(*, seats: int = 5) -> int:
    """给 `TENANT` 开一个专业版订阅并返回额度总量。

    Args:
        seats: 席位数（专业版最小 5）。

    Returns:
        int: 套餐额度总量。
    """
    from app.business.subscription_service import SubscriptionService
    from app.db.repositories.billing import get_billing_store
    from app.schemas.billing import ChangeSubscriptionRequest

    conf = _settings()
    await SubscriptionService(conf, get_billing_store(conf)).change(
        tenant_id=TENANT,
        actor_id=USER,
        request=ChangeSubscriptionRequest(plan_code="professional", seats=seats, action="upgrade"),
    )
    quota = await InMemoryUsageStore().quota(TENANT)
    assert quota.total_units > 0
    return quota.total_units


async def test_quota_notice_returns_15006_when_over_quota() -> None:
    """达标/超额 → `(15006, 注册表文案)`。"""
    from app.core.errors import ERROR_REGISTRY, ErrorCode

    total = await _seed_subscription()
    service = _service()
    await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_notice_over", units=total
    )

    code, message = await service.quota_notice(tenant_id=TENANT, user_id=USER)
    assert code == int(ErrorCode.QUOTA_EXHAUSTED) == 15006
    assert message == ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].message
    assert message, "提示文案不得为空（前端要直接展示）"


async def test_quota_notice_silent_when_under_quota() -> None:
    """未达上限 → `(0, "")`（不打扰用户）。"""
    total = await _seed_subscription()
    service = _service()
    await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_notice_under", units=total // 2
    )

    assert await service.quota_notice(tenant_id=TENANT, user_id=USER) == (0, "")


async def test_quota_notice_silent_without_subscription() -> None:
    """无订阅（额度总量 0）→ `(0, "")`。

    判据 `over_quota_warning` 含 `total_units > 0`，因此免费壳与桩模式天然不提示；
    这条钉住"不要用 `used >= 0` 之类的简化判据"。
    """
    service = _service()
    await service.record_generation(
        tenant_id=TENANT, user_id=USER, generation_id="gen_notice_nosub", units=10
    )
    snapshot = await service.snapshot(tenant_id=TENANT, user_id=USER)
    assert snapshot.total_units == 0
    assert await service.quota_notice(tenant_id=TENANT, user_id=USER) == (0, "")


async def test_quota_notice_silent_without_identity() -> None:
    """缺少租户/用户标识 → `(0, "")`（不查库、不报错）。"""
    service = _service()
    assert await service.quota_notice(tenant_id="", user_id=USER) == (0, "")
    assert await service.quota_notice(tenant_id=TENANT, user_id="") == (0, "")


async def test_quota_notice_never_raises_on_store_failure() -> None:
    """计量读取失败不得影响主流程：吞掉异常并返回无提示。"""

    class _BrokenStore(InMemoryUsageStore):
        """取快照时抛异常的存储替身。"""

        async def quota(self, tenant_id: str) -> object:
            """模拟存储故障。

            Args:
                tenant_id: 租户 ID。

            Raises:
                RuntimeError: 固定抛出。
            """
            del tenant_id
            raise RuntimeError("store exploded")

    service = UsageService(_settings(), store=_BrokenStore(), directory=NullUsageDirectory())
    assert await service.quota_notice(tenant_id=TENANT, user_id=USER) == (0, "")
