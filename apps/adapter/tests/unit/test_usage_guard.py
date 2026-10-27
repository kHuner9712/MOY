"""成本护栏与计量维度验收（阶段四 T4-04.2 / T4-04.3，架构 §7.2 / §7.4 / §7.5）。

四条判据：

1. **硬止损在调用之前**：额度用尽 → `15006` / HTTP 402，**上游调用 0 次**；
2. **4 个计量维度真写进事件**：不同档位 → `profile` 不同，token 非空（取不到真实值时
   按字符估算并把 `source` 置 `estimated`）；
3. **列级降级**：`usage_event` 缺维度列（N-1 旧库）时**维度不落库但事件照记**，不 500；
4. **租户月度预算**：80% → 非阻断提示（`code=0`）；100% → 拒绝；跨月自然重置。

**不用 `10005` 做自研止损**：那个码表示"上游回传额度不足"，与"我们的额度用完了"是两件事。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.usage_service import BUDGET_WARN_MESSAGE, BudgetStatus, UsageService
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.core.security import AuthContext
from app.db import schema_guard
from app.db.repositories.budget_repo import InMemoryBudgetStore, TenantBudgetRecord
from app.db.repositories.usage import (
    SOURCE_SETTLED,
    InMemoryUsageStore,
    UsageEventRecord,
)
from app.schemas.model import ChatMessage, GenerateRequest, MessageRole, ModelProfile
from app.services.model_service import ModelService

pytestmark = pytest.mark.unit

TENANT = "tnt_guard"
USER = "usr_guard"


class _QuotaDirectory:
    """组织信息替身（护栏用例不关心部门）。"""

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        return ""

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        return "测试用户"


def _settings(**overrides: Any) -> Settings:
    """构造护栏用例配置（内存存储，无真实下游）。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, Any] = {
        "env": "test",
        "business_store": "memory",
        "dify_default_app_key": "app-general",
        "dify_code_app_key": "app-code",
        "dify_long_context_app_key": "app-long",
        "dify_reasoning_app_key": "app-reason",
        "usage_write_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)


def _service(
    *,
    store: InMemoryUsageStore | None = None,
    budgets: InMemoryBudgetStore | None = None,
    settings: Settings | None = None,
) -> UsageService:
    """构造计量服务。

    Args:
        store: 用量存储。
        budgets: 预算存储。
        settings: 配置。

    Returns:
        UsageService: 服务。
    """
    return UsageService(
        settings or _settings(),
        store=store or InMemoryUsageStore(),
        directory=_QuotaDirectory(),
        budgets=budgets or InMemoryBudgetStore(),
    )


def _event(units: int, *, period: str = "") -> UsageEventRecord:
    """构造一条聊天用量事件。

    Args:
        units: 额度。
        period: 计量周期。

    Returns:
        UsageEventRecord: 事件。
    """
    return UsageEventRecord(
        tenant_id=TENANT,
        user_id=USER,
        capability="chat",
        idem_key=f"gen_{period}_{units}",
        units=units,
        period=period,
        source=SOURCE_SETTLED,
    )


class TestBudgetStatus:
    """预算判定（纯逻辑 + 存储）。"""

    async def test_unset_budget_never_blocks(self) -> None:
        """没设预算且无套餐额度 → 不拦（免费壳/桩模式不能因为护栏全挂）。"""
        status = await _service().budget_status(tenant_id=TENANT, user_id=USER)

        assert status.exhausted is False
        assert status.warn is False
        assert status.budget_enabled is False

    async def test_eighty_percent_warns_without_blocking(self) -> None:
        """80% → 只提示不拦（`exhausted=False`、`warn=True`）。"""
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(
            TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=1000, warn_ratio=0.8)
        )
        await store.append(_event(850))

        status = await _service(store=store, budgets=budgets).budget_status(
            tenant_id=TENANT, user_id=USER
        )

        assert status.budget_units == 1000
        assert status.budget_used_units == 850
        assert status.warn is True
        assert status.exhausted is False

    async def test_hundred_percent_is_reported_not_raised(self) -> None:
        """100% → `exhausted=True`，但**不抛**：对话继续可用、由调用方限长。

        2026-10-27 用户裁决（PRD 5.3 ①）：`≥100%` 对话不再 402，改为"常规模式"。
        判据落点：`model_service` 拿到 `exhausted` → `apply_answer_cap()`。
        若有人把这里改回 `raise`，本用例立即红。
        """
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=100))
        await store.append(_event(100))
        service = _service(store=store, budgets=budgets)

        status = await service.check_generation_allowed(tenant_id=TENANT, user_id=USER)

        assert status.exhausted is True

    async def test_budget_resets_across_months(self) -> None:
        """跨月自然重置：上月用量不计入本月（预算按月判，不需要手工清零）。"""
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=100))
        await store.append(_event(100, period="2020-01"))  # 很久以前的用量

        status = await _service(store=store, budgets=budgets).budget_status(
            tenant_id=TENANT, user_id=USER
        )

        assert status.budget_used_units == 0
        assert status.exhausted is False

    async def test_disabled_budget_does_not_block(self) -> None:
        """`enabled=false` 与"没设额度"都表现为不拦，但语义可区分（`budget_enabled`）。"""
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(
            TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=10, enabled=False)
        )
        await store.append(_event(999))

        status = await _service(store=store, budgets=budgets).budget_status(
            tenant_id=TENANT, user_id=USER
        )

        assert status.exhausted is False
        assert status.budget_enabled is False

    async def test_ratio_and_exhausted_helpers(self) -> None:
        """预算记录的三个纯函数口径（未设额度时恒不预警，除零安全）。"""
        record = TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=100)

        assert record.usage_ratio(0) == 0.0
        assert record.usage_ratio(150) == pytest.approx(1.5)
        assert record.should_warn(80) is True
        assert record.should_warn(100) is False  # 已用尽走上限，不是"即将用尽"
        assert record.exhausted(100) is True
        assert TenantBudgetRecord(tenant_id=TENANT).usage_ratio(50) == 0.0


class TestBudgetNotice:
    """80% 提示：`code=0` 的信封里挂 notice，不阻断。"""

    async def test_notice_carries_warning_message(self) -> None:
        """达预警线 → `(15006, 即将用完文案)`；未达 → `(0, "")`。"""
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=1000))
        await store.append(_event(800))
        service = _service(store=store, budgets=budgets)

        code, message = await service.quota_notice(tenant_id=TENANT, user_id=USER)

        assert code == int(ErrorCode.QUOTA_EXHAUSTED)
        assert message == BUDGET_WARN_MESSAGE

    async def test_no_notice_below_threshold(self) -> None:
        """用量低于预警线 → 无提示。"""
        store = InMemoryUsageStore()
        budgets = InMemoryBudgetStore()
        await budgets.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=1000))
        await store.append(_event(100))

        code, message = await _service(store=store, budgets=budgets).quota_notice(
            tenant_id=TENANT, user_id=USER
        )

        assert (code, message) == (0, "")


class TestUsageDimensions:
    """计量维度四列：档位 3 个值 + token 非空 + 估算标记。"""

    async def test_generation_records_profile_and_tokens(self) -> None:
        """三个不同档位 → 三条事件，`profile` 三个值，token 均非空。"""
        store = InMemoryUsageStore()
        service = _service(store=store)

        for index, profile in enumerate(("general", "code", "reasoning")):
            await service.record_generation(
                tenant_id=TENANT,
                user_id=USER,
                generation_id=f"gen_{index}",
                units=10 + index,
                profile=profile,
                provider="downstream",
                prompt_tokens=12 + index,
                completion_tokens=8,
            )

        events = _all_events(store)
        assert {event.profile for event in events} == {"general", "code", "reasoning"}
        assert all(event.prompt_tokens > 0 for event in events)
        assert all(event.completion_tokens > 0 for event in events)
        assert all(event.provider == "downstream" for event in events)
        assert all(event.source == SOURCE_SETTLED for event in events)

    async def test_estimated_tokens_do_not_change_source(self) -> None:
        """token 估算**不改变** `source`：那个字段表达的是**额度**来源。

        复用同一个字段会让"流式中断按已产出折算"（`estimated`）与"token 是估算的"
        混成一件事——既有判据 `test_b07_4` 正是靠区分这两者成立的。
        """
        store = InMemoryUsageStore()
        service = _service(store=store)

        await service.record_generation(
            tenant_id=TENANT,
            user_id=USER,
            generation_id="gen_estimated",
            units=10,
            profile="general",
            prompt_tokens=5,
            completion_tokens=5,
            estimated=True,
        )

        event = next(iter(_all_events(store)))
        assert event.source == SOURCE_SETTLED  # 额度是按 settled 结算的
        assert (event.prompt_tokens, event.completion_tokens) == (5, 5)

    def test_token_estimate_from_payload(self) -> None:
        """响应里有真实 usage 就用它；没有就按字符数估算并标记 estimated。"""
        real = {"metadata": {"usage": {"prompt_tokens": 21, "completion_tokens": 9}}}
        assert ModelService._token_usage(  # noqa: SLF001 - 纯函数边界验证
            real, "很长的提示词", "回答"
        ) == (21, 9, False)

        assert ModelService._token_usage({}, "提示词", "答案") == (  # noqa: SLF001
            3,
            2,
            True,
        )


def _all_events(store: InMemoryUsageStore) -> list[UsageEventRecord]:
    """取出内存存储里的全部事件（测试内省用）。

    Args:
        store: 用量存储。

    Returns:
        list[UsageEventRecord]: 事件列表。
    """
    events = getattr(store, "_events", {})
    return list(events.values())


class TestSchemaGuardDegrade:
    """列级降级：旧库缺 4 列时事件照记，只是维度为空（不 500）。"""

    async def test_missing_columns_do_not_break_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`usage_dimension_supported=False` → 写入走不带维度的路径（此处以内存替身验证语义）。"""
        monkeypatch.setattr(schema_guard, "usage_dimension_supported", _always_false)
        store = InMemoryUsageStore()
        service = _service(store=store)

        inserted = await service.record_generation(
            tenant_id=TENANT, user_id=USER, generation_id="gen_degraded", units=3
        )

        assert inserted is True
        assert len(_all_events(store)) == 1

    async def test_probe_returns_true_in_memory_mode(self) -> None:
        """内存模式恒支持（进程内事件对象天然有字段）。"""
        from app.db.schema_guard import usage_dimension_supported

        assert await usage_dimension_supported(_settings()) is True


async def _always_false(settings: Any = None) -> bool:
    """替身：列能力恒为不可用。"""
    return False


class TestSoftLimitAfterQuota:
    """额度触顶后：对话**继续可用但限长**（P4-11 ①，2026-10-27 用户裁决）。

    历史：本类原为 `TestHardStopBeforeUpstream`，断言"402 + 上游 0 次"。
    改判据的原因：PRD 5.3 ① 要求「≥100% 对话继续可用、只限长」。
    **代价（如实标注）**：超额用户每次对话仍会产生一次上游调用。
    R3/A1 的超额仍是硬止损（不调底座），由 `test_task_quota_hold.py` 与
    `test_rag_service.py::test_upload_is_held_...` 覆盖——两种处置并存。
    """

    async def test_exhausted_status_is_returned_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_resolve_generation_budget` 返回 exhausted 状态，**不抛**。"""
        service = ModelService(_settings())
        auth = AuthContext(user_id=USER, tenant_id=TENANT)

        async def _exhausted(*_: Any, **__: Any) -> BudgetStatus:
            return BudgetStatus(exhausted=True)

        monkeypatch.setattr(service._usage, "check_capability_allowed", _exhausted)  # noqa: SLF001

        status = await service._resolve_generation_budget(auth)  # noqa: SLF001

        assert status is not None
        assert status.exhausted is True, "超额状态必须原样返回给调用方（由它决定限长）"

    async def test_guard_swallows_unexpected_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """判定本身出错（计量库抖动）时放行：护栏不能把可用性押在计量库上。"""
        service = ModelService(_settings())
        auth = AuthContext(user_id=USER, tenant_id=TENANT)

        async def _boom(*_: Any, **__: Any) -> BudgetStatus:
            raise RuntimeError("usage store down")

        monkeypatch.setattr(service._usage, "check_capability_allowed", _boom)  # noqa: SLF001

        status = await service._resolve_generation_budget(auth)  # noqa: SLF001 - 不抛即通过

        assert status is None, "判定失败要按「未超额」处理（返回 None），不能把请求打挂"


def _token(settings: Settings) -> str:
    """签发测试令牌。

    Args:
        settings: 配置。

    Returns:
        str: JWT。
    """
    from tests.conftest import make_token

    return make_token(settings=settings, tenant_id=TENANT, user_id=USER)


def _request(profile: ModelProfile = ModelProfile.GENERAL) -> GenerateRequest:
    """构造生成请求。

    Args:
        profile: 档位。

    Returns:
        GenerateRequest: 请求。
    """
    return GenerateRequest(
        profile=profile,
        messages=[ChatMessage(role=MessageRole.USER, content="你好")],
    )


class TestSchemaGuardFailure:
    """探测失败必须降级为"不支持"，不能把异常抛给调用方。"""

    async def test_probe_failure_returns_false(self) -> None:
        """连不上库（端口不可达）→ 返回 False，且不抛异常。"""
        from app.db import schema_guard

        schema_guard.reset_schema_guard()
        unreachable = _settings(
            business_store="postgres",
            database_url="postgresql://u:p@127.0.0.1:1/db",
            db_pool_timeout_seconds=0.5,
        )

        assert await schema_guard.usage_dimension_supported(unreachable) is False
        schema_guard.reset_schema_guard()

    async def test_memory_mode_is_always_supported(self) -> None:
        """内存模式恒支持（进程内事件对象天然有字段）。"""
        from app.db import schema_guard

        schema_guard.reset_schema_guard()
        assert await schema_guard.usage_dimension_supported(_settings()) is True
