"""R2-ENG-08：额度判定与超额提示必须复用同一份用量快照。

## 这一组用例在防什么风险

`UsageService.snapshot()` 单次要打 **4 次**存储往返（月度行 / 套餐 / 日趋势 /
事件计数）。修复前 M1/M2 每条请求都把它算两遍：

1. `_resolve_generation_budget()` → `check_capability_allowed()` → `snapshot()`；
2. `_quota_notice()` → `quota_notice()` → `snapshot()`。

两次都在**首 token 之前**，因此在冷启动或远端库上表现为"第一句话总是慢半拍"。
`usage_service.py` 侧同样如此（`budget_status` 与 `quota_notice` 各查一次）。

这里钉住三件事：

1. 合并路径上 `snapshot()` **只被调用一次**，且同一份快照被传给两个下游；
2. `budget_status()` / `quota_notice()` 收到快照时**不再自取**（否则复用形同虚设）；
3. **语义不变**：判定结果与提示码仍由原方法产出，快照取失败时仍按旧路径兜底
   （"护栏失败不影响可用性"这条纪律不能被这次改动破坏）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.usage_service import (
    BudgetStatus,
    UsageService,
    UsageSnapshot,
)
from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode
from app.core.security import AuthContext
from app.services.model_service import ModelService

pytestmark = pytest.mark.unit

_TENANT = "tnt_eng08"
_USER = "usr_eng08"


def _settings(**overrides: Any) -> Settings:
    """构造离线可用的配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params: dict[str, Any] = {"env": "test", "downstream_enabled": False, "mock_mode": True}
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


def _snapshot(*, total: int = 100, remaining: int = 0, over: bool = True) -> UsageSnapshot:
    """构造用量快照。

    Args:
        total: 额度总量。
        remaining: 剩余额度。
        over: 是否已超额。

    Returns:
        UsageSnapshot: 快照。
    """
    return UsageSnapshot(
        tenant_id=_TENANT,
        period="2026-10",
        used_units=total - remaining,
        total_units=total,
        remaining_units=remaining,
        usage_percent=100.0 if total and over else 0.0,
        over_quota_warning=over,
        quota_unit="次",
        plan_name="专业版",
        plan_code="pro",
    )


# ===========================================================================
# 模型服务：一条请求只取一次快照
# ===========================================================================
class _RecordingUsageService:
    """计量服务替身：记录 `snapshot()` 次数与被传入的快照对象。

    Attributes:
        snapshot_calls: `snapshot()` 被调用次数。
        check_snapshot: 传给 `check_capability_allowed` 的快照。
        notice_snapshot: 传给 `quota_notice` 的快照。
        snapshot_error: 非 None 时 `snapshot()` 抛出该异常。
    """

    def __init__(self, snapshot_error: Exception | None = None) -> None:
        self.snapshot_calls = 0
        self.check_snapshot: UsageSnapshot | None = None
        self.notice_snapshot: UsageSnapshot | None = None
        self.snapshot_error = snapshot_error
        self._snapshot = _snapshot()

    async def snapshot(self, *, tenant_id: str, user_id: str, period: str = "") -> UsageSnapshot:
        """返回固定快照并计数（或按配置抛错）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            period: 计量周期。

        Returns:
            UsageSnapshot: 快照。

        Raises:
            Exception: 构造时指定了 `snapshot_error`。
        """
        del tenant_id, user_id, period
        self.snapshot_calls += 1
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self._snapshot

    async def check_capability_allowed(
        self,
        *,
        capability: str,
        tenant_id: str,
        user_id: str,
        snapshot: UsageSnapshot | None = None,
    ) -> BudgetStatus:
        """记录传入的快照并返回"已超额"。

        Args:
            capability: 作用域。
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            snapshot: 传入的快照。

        Returns:
            BudgetStatus: 已超额的判定结果。
        """
        del capability, tenant_id, user_id
        self.check_snapshot = snapshot
        return BudgetStatus(plan_total_units=100, plan_remaining_units=0, exhausted=True)

    async def quota_notice(
        self, *, tenant_id: str, user_id: str, snapshot: UsageSnapshot | None = None
    ) -> tuple[int, str]:
        """记录传入的快照并返回 15006 提示。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            snapshot: 传入的快照。

        Returns:
            tuple[int, str]: 提示码与文案。
        """
        del tenant_id, user_id
        self.notice_snapshot = snapshot
        return int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code), "本月可用量已用完"


def _model_service(usage: _RecordingUsageService) -> ModelService:
    """构造注入了计量替身的模型服务。

    Args:
        usage: 计量替身。

    Returns:
        ModelService: 模型服务。
    """
    return ModelService(settings=_settings(), usage=usage)  # type: ignore[arg-type]


async def test_budget_and_notice_share_one_snapshot() -> None:
    """合并路径上 `snapshot()` 只调用一次，且两个下游拿到**同一个**快照对象。"""
    usage = _RecordingUsageService()
    service = _model_service(usage)

    budget, notice = await service._generation_budget_and_notice(  # noqa: SLF001 - 被测私有方法
        AuthContext(user_id=_USER, tenant_id=_TENANT)
    )

    assert usage.snapshot_calls == 1, "同一请求内不得重复查询用量快照（R2-ENG-08）"
    assert usage.check_snapshot is not None, "额度判定必须复用该快照"
    assert usage.notice_snapshot is not None, "超额提示必须复用该快照"
    assert usage.check_snapshot is usage.notice_snapshot, "两处必须是同一个快照对象"
    assert budget is not None and budget.exhausted is True, "判定结果不变（仍为已超额）"
    assert notice[0] == int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code)


async def test_budget_and_notice_without_user_skip_everything() -> None:
    """无用户身份时不做任何额度查询（内部/桩调用不计入个人额度）。"""
    usage = _RecordingUsageService()
    service = _model_service(usage)

    budget, notice = await service._generation_budget_and_notice(None)  # noqa: SLF001

    assert usage.snapshot_calls == 0
    assert budget is None
    assert notice == (0, "")


async def test_snapshot_failure_falls_back_without_blocking() -> None:
    """快照取失败时不得阻断请求：两个子调用退回自取快照的旧路径（拿到 None）。"""
    usage = _RecordingUsageService(snapshot_error=RuntimeError("计量库不可用"))
    service = _model_service(usage)

    budget, notice = await service._generation_budget_and_notice(  # noqa: SLF001
        AuthContext(user_id=_USER, tenant_id=_TENANT)
    )

    assert usage.check_snapshot is None, "兜底路径应传 None（由子调用自取）"
    assert usage.notice_snapshot is None
    assert budget is not None and budget.exhausted is True, "护栏失败仍返回判定结果"
    assert notice[0] == int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code)


# ===========================================================================
# 计量服务：收到快照时不得再自取
# ===========================================================================
class _CountingUsageService(UsageService):
    """真实计量服务 + `snapshot()` 调用计数。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.snapshot_calls = 0

    async def snapshot(self, *, tenant_id: str, user_id: str, period: str = "") -> UsageSnapshot:
        """计数后委派真实实现。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            period: 计量周期。

        Returns:
            UsageSnapshot: 快照。
        """
        self.snapshot_calls += 1
        return await super().snapshot(tenant_id=tenant_id, user_id=user_id, period=period)


async def test_budget_status_reuses_provided_snapshot() -> None:
    """`budget_status(snapshot=...)` 不得再查一次快照，判定结果由传入快照决定。"""
    service = _CountingUsageService(settings=_settings())
    provided = _snapshot(total=100, remaining=0, over=True)

    status = await service.budget_status(tenant_id=_TENANT, user_id=_USER, snapshot=provided)

    assert service.snapshot_calls == 0, "传入快照时不得再自取"
    assert status.plan_total_units == 100
    assert status.plan_remaining_units == 0
    assert status.exhausted is True, "剩余额度为 0 必须判定为已用尽"


async def test_quota_notice_reuses_provided_snapshot() -> None:
    """`quota_notice(snapshot=...)` 不得再查一次快照，提示码由传入快照决定。"""
    service = _CountingUsageService(settings=_settings())
    provided = _snapshot(total=100, remaining=0, over=True)

    code, message = await service.quota_notice(tenant_id=_TENANT, user_id=_USER, snapshot=provided)

    assert service.snapshot_calls == 0, "传入快照时不得再自取"
    assert code == int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code)
    assert message


async def test_quota_notice_without_snapshot_still_works() -> None:
    """不传快照时保持修复前的行为（自取一次）。"""
    service = _CountingUsageService(settings=_settings())

    code, _message = await service.quota_notice(tenant_id=_TENANT, user_id=_USER)

    assert service.snapshot_calls == 1
    assert code == 0, "无订阅（额度总量为 0）时天然不提示"


async def test_merged_path_costs_one_snapshot_instead_of_two() -> None:
    """合并路径总成本 = 1 次快照查询；分开调用（旧路径）需要 2 次。"""
    merged = _CountingUsageService(settings=_settings())
    snapshot = await merged.snapshot(tenant_id=_TENANT, user_id=_USER)
    merged.snapshot_calls = 0
    await merged.budget_status(tenant_id=_TENANT, user_id=_USER, snapshot=snapshot)
    await merged.quota_notice(tenant_id=_TENANT, user_id=_USER, snapshot=snapshot)
    assert merged.snapshot_calls == 0

    legacy = _CountingUsageService(settings=_settings())
    await legacy.budget_status(tenant_id=_TENANT, user_id=_USER)
    await legacy.quota_notice(tenant_id=_TENANT, user_id=_USER)
    assert legacy.snapshot_calls == 2, "修复前每条请求要查两次（这正是被消除的重复计算）"
