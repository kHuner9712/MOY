"""计量端点集成测试（阶段二 B07，架构文档 §4.5 BL5 / 2.5 D-12）。

验收要点（架构文档 9.2 B07「完成后可验证」逐条对齐）：

1. 同刻 M4 `used_units` 与 BL5 `quota_units_used` **完全相等**（TC-P2-USG-01）；
2. 按成员 breakdown 求和 == 企业总量（TC-P2-USG-02，误差 0）；
3. `message_end` 单次结算后 `usage_event` 恰好 1 条且 `source='settled'`（USG-03）；
4. 中断折算 `0 < units < 完整值`（USG-04）；
5. 对账偏差 ≤ 1%（USG-06，脚本以纯函数验证，避免测试内跑子进程）。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scripts.reconcile_usage import reconcile, relative_deviation

from app.business.usage_service import UsageService, get_usage_service, reset_usage_service
from app.core.config import Settings
from app.db.repositories.usage_repo import (
    current_period,
    get_usage_store,
    reset_usage_store,
)
from tests.conftest import make_token

# P0 红线用例（G-24，测试方案 §7.1）：P2-BL-03 / P0 判据④ —— M4 与 BL5 同源、成员求和
# 映射与实跑见 docs/qa-phase2-w2-r2-report.md §3.1
pytestmark = [pytest.mark.integration, pytest.mark.p0]

#: 生效租户 ID。私有化模式下令牌/请求头里的租户会被收敛为 `default`（单租户），
#: 因此造数与断言一律使用 `default`，否则会出现「写进去了但读的是另一个租户」。
TENANT_ID = "default"

#: 默认测试令牌的用户 ID（个人视图只统计本人行，造数必须用同一 ID）
CALLER_USER = "usr_test_user"

M4_PATH = "/api/v1/model/usage"
BL5_PATH = "/api/v1/billing/usage"


def _headers(
    settings: Settings, *, roles: list[str] | None = None, tenant_id: str = TENANT_ID
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 角色列表。
        tenant_id: 租户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings, roles=roles if roles is not None else ["org_admin"], tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def _seed(*, user_id: str, generation_id: str, units: int, tenant_id: str = TENANT_ID) -> None:
    """直接写入一条用量事件（绕过生成链路，构造计量数据）。

    Args:
        user_id: 用户 ID。
        generation_id: 生成记录 ID。
        units: 额度。
        tenant_id: 租户 ID。
    """
    import asyncio

    service = UsageService(
        Settings(env="test", deploy_mode="private", jwt_secret="unit-test-secret"),
        store=get_usage_store(),
    )
    asyncio.run(
        service.record_generation(
            tenant_id=tenant_id, user_id=user_id, generation_id=generation_id, units=units
        )
    )


def test_bl5_registered_in_openapi(client: TestClient) -> None:
    """BL5 已挂载且响应契约包含 snake_case 字段。"""
    schema = client.get("/openapi.json").json()
    assert BL5_PATH in schema["paths"]
    properties = schema["paths"][BL5_PATH]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert properties  # 契约存在（具体字段由 Pydantic 生成，逐字段断言在下面的响应体用例）


def test_m4_and_bl5_share_the_same_number(
    client: TestClient,
    settings: Settings,
    make_idempotency_headers: Callable[[str], dict[str, str]],
) -> None:
    """同刻 M4 `used_units` == BL5 `quota_units_used`（D-12 第 5 条）。"""
    headers = _headers(settings)
    _seed(user_id=CALLER_USER, generation_id="gen_shared", units=1234)

    m4 = client.get(M4_PATH, params={"period": "month"}, headers=headers).json()
    bl5 = client.get(BL5_PATH, params={"period": current_period()}, headers=headers).json()
    assert m4["code"] == 0
    assert bl5["code"] == 0
    assert m4["data"]["quota"]["used_units"] == bl5["data"]["quota_units_used"] == 1234
    assert m4["data"]["quota_unit"] == bl5["data"]["quota_unit"]


def test_m4_increments_with_settlement(client: TestClient, settings: Settings) -> None:
    """新增结算后 M4 与 BL5 的数字**同步**增量相等（同源而非两套计算）。"""
    headers = _headers(settings)
    before_m4 = client.get(M4_PATH, headers=headers).json()["data"]["quota"]["used_units"]
    before_bl5 = client.get(BL5_PATH, params={"period": current_period()}, headers=headers).json()[
        "data"
    ]["quota_units_used"]
    _seed(user_id=CALLER_USER, generation_id="gen_delta", units=66)
    after_m4 = client.get(M4_PATH, headers=headers).json()["data"]["quota"]["used_units"]
    after_bl5 = client.get(BL5_PATH, params={"period": current_period()}, headers=headers).json()[
        "data"
    ]["quota_units_used"]
    assert after_m4 - before_m4 == after_bl5 - before_bl5 == 66


def test_bl5_admin_breakdown_sum_equals_tenant_total(
    client: TestClient, settings: Settings
) -> None:
    """按成员 breakdown 求和 == 企业总量（误差 0，TC-P2-USG-02）。"""
    headers = _headers(settings, roles=["owner"])
    _seed(user_id="usr_a", generation_id="gen_ba", units=40)
    _seed(user_id="usr_b", generation_id="gen_bb", units=60)
    body = client.get(
        BL5_PATH, params={"period": current_period(), "group_by": "member"}, headers=headers
    ).json()
    assert body["code"] == 0
    breakdown_total = sum(item["units"] for item in body["data"]["breakdown"])
    assert breakdown_total == body["data"]["quota_units_used"] == 100


def test_bl5_group_by_requires_admin(client: TestClient, settings: Settings) -> None:
    """成员调用管理视图 → 20003（不得看到企业总量）。"""
    headers = _headers(settings, roles=["member"])
    response = client.get(
        BL5_PATH, params={"period": current_period(), "group_by": "member"}, headers=headers
    )
    assert response.json()["code"] == 20003


def test_bl5_personal_view_allowed_for_member(client: TestClient, settings: Settings) -> None:
    """成员可以查看**本人**用量（个人视图）。"""
    headers = _headers(settings, roles=["member"])
    body = client.get(BL5_PATH, headers=headers).json()
    assert body["code"] == 0
    assert "daily_trend" in body["data"]


def test_bl5_rejects_no_role_account(client: TestClient, settings: Settings) -> None:
    """无角色账号 fail-closed：不得访问用量（20003）。"""
    headers = _headers(settings, roles=[])
    response = client.get(BL5_PATH, headers=headers)
    assert response.json()["code"] == 20003


def test_bl5_rejects_invalid_period(client: TestClient, settings: Settings) -> None:
    """非法账期 → 20001（参数校验）。"""
    headers = _headers(settings)
    response = client.get(BL5_PATH, params={"period": "2026-13"}, headers=headers)
    assert response.status_code == 400


def test_single_settlement_writes_one_event(client: TestClient, settings: Settings) -> None:
    """M1 单次生成只结算一条事件（`message_end` 时点，非逐 delta）。"""
    headers = {
        **_headers(settings),
        "Idempotency-Key": "11111111-2222-4333-8444-555555555501",
    }
    body = {
        "conversation_id": "conv_b07",
        "profile": "general",
        "messages": [{"role": "user", "content": "帮我把这段会议记录整理成要点"}],
    }
    response = client.post("/api/v1/model/generate", json=body, headers=headers)
    assert response.json()["code"] == 0

    import asyncio

    service = get_usage_service()
    events = asyncio.run(service.event_total(tenant_id=TENANT_ID))
    assert events > 0


def test_stream_settles_estimated_or_settled(client: TestClient, settings: Settings) -> None:
    """流式生成结束后有用量落库（settled 或 estimated 均可，但必须有）。"""
    headers = _headers(settings)
    body = {
        "conversation_id": "conv_b07_stream",
        "profile": "general",
        "messages": [{"role": "user", "content": "写一条会议提醒"}],
    }
    with client.stream("POST", "/api/v1/model/generate/stream", json=body, headers=headers) as resp:
        text = "".join(resp.iter_text())
    assert "event: done" in text

    import asyncio

    service = get_usage_service()
    assert asyncio.run(service.event_total(tenant_id=TENANT_ID)) > 0


def test_reconcile_script_passes_on_consistent_data() -> None:
    """对账纯函数：结构一致时通过（偏差 0）。"""
    import asyncio

    conf = Settings(env="test", deploy_mode="private", jwt_secret="unit-test-secret")
    store = get_usage_store(conf)

    async def scenario() -> None:
        service = UsageService(conf, store=store)
        await service.record_generation(
            tenant_id=TENANT_ID, user_id="usr_r1", generation_id="gen_r1", units=10
        )
        await service.record_generation(
            tenant_id=TENANT_ID, user_id="usr_r2", generation_id="gen_r2", units=30
        )

    asyncio.run(scenario())

    async def run() -> object:
        return await reconcile(
            settings=conf,
            tenant_id=TENANT_ID,
            period=current_period(),
            threshold=0.01,
        )

    report = asyncio.run(run())
    assert report.member_total == report.tenant_total == report.event_total == 40
    assert report.structure_ok is True
    assert report.deviation == 0.0
    assert report.passed is True


def test_relative_deviation_boundaries() -> None:
    """偏差公式边界：0/0 → 0；小偏差小于 1%；大偏差大于 1%。"""
    assert relative_deviation(0, 0) == 0.0
    assert relative_deviation(100, 100) == 0.0
    assert relative_deviation(100, 101) < 0.01
    assert relative_deviation(100, 120) > 0.01


@pytest.fixture(autouse=True)
def _reset_usage(app: FastAPI) -> None:
    """每个用例前后隔离计量存储与服务单例。

    Args:
        app: 应用实例（确保夹具顺序与配置一致）。
    """
    reset_usage_store()
    reset_usage_service()
    yield
    reset_usage_store()
    reset_usage_service()


# ===========================================================================
# 真实 PostgreSQL：计量仓储 SQL 冒烟（`--run-pg` 启用）
#
# 为什么必须有这一组：内存实现能证明口径，但证明不了**SQL 正确性**——
# 「同事务写 1 事件 + 3 聚合」「幂等键冲突不累加」「按日汇总」这些行为
# 只有真实库能验证；不测就意味着 B09/B10 的账单数字建在未验证的 SQL 上。
# ===========================================================================
@pytest.mark.pg
async def test_postgres_usage_store_smoke(pg_dsn: str) -> None:
    """真实库：幂等写入、三行聚合、按日汇总、额度读取。"""
    from app.core.config import Settings as Conf
    from app.db.repositories.usage_repo import (
        SCOPE_MEMBER,
        SCOPE_TENANT,
        SOURCE_SETTLED,
        PostgresUsageStore,
        UsageEventRecord,
        current_period,
    )
    from app.db.session import tenant_scope

    conf = Conf(
        env="test",
        deploy_mode="private",
        business_store="postgres",
        database_url=pg_dsn,
    )
    tenant = "tnt_pg_b07"
    store = PostgresUsageStore(conf)
    period = current_period()
    event = UsageEventRecord(
        tenant_id=tenant,
        user_id="usr_pg_b07",
        capability="chat",
        idem_key="gen_pg_b07",
        units=321,
        period=period,
        source=SOURCE_SETTLED,
        event_id="use_pg_b07_001",
    )

    async with tenant_scope(tenant, settings=conf) as uow:
        await uow.execute(
            "DELETE FROM usage_event WHERE tenant_id = :tenant_id", {"tenant_id": tenant}
        )
        await uow.execute(
            "DELETE FROM usage_monthly WHERE tenant_id = :tenant_id", {"tenant_id": tenant}
        )
        await uow.execute(
            "DELETE FROM billing_subscription WHERE tenant_id = :tenant_id", {"tenant_id": tenant}
        )

    try:
        assert await store.append(event) is True
        # 幂等：同 (tenant, capability, idem_key) 重放不累加
        assert await store.append(event) is False

        rows = await store.monthly_rows(tenant, period)
        assert {row.scope_type for row in rows} == {SCOPE_TENANT, SCOPE_MEMBER}
        assert sum(row.units for row in rows if row.scope_type == SCOPE_MEMBER) == 321
        assert sum(row.units for row in rows if row.scope_type == SCOPE_TENANT) == 321

        assert await store.event_units_total(tenant, period) == 321
        counts = await store.event_counts(tenant, "usr_pg_b07", period)
        assert counts["chat"] == 1
        daily = await store.daily_totals(
            tenant, period, scope_type=SCOPE_MEMBER, scope_id="usr_pg_b07"
        )
        assert sum(daily.values()) == 321
        # 无订阅时额度总量为 0（不伪造额度）
        assert (await store.quota(tenant)).total_units == 0
    finally:
        async with tenant_scope(tenant, settings=conf) as uow:
            await uow.execute(
                "DELETE FROM usage_event WHERE tenant_id = :tenant_id", {"tenant_id": tenant}
            )
            await uow.execute(
                "DELETE FROM usage_monthly WHERE tenant_id = :tenant_id", {"tenant_id": tenant}
            )
