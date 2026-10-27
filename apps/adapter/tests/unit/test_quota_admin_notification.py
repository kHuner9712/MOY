"""管理员额度通知（P4-11 ④）：阈值 → 审计留痕，且**按 (租户, 动作, 本期) 幂等**。

三条判据（缺一不可）：

- ④a **幂等**：连发多次只留 1 条 `billing.quota_exhausted_100`；
- ④b **阈值分离**：≥80% 未超额只留 `_80`，不留 `_100`；
- ④c **普通请求零写**：未达预警线时两个 action 都没有。

**为什么必须用 fake 断言"走了查重分支"**：只靠"连发 5 次只有 1 行"反推，
证明不了那行查重代码被执行过（也可能压根没触发写入）。所以这里让 fake
分别在 `exists=True` / `exists=False` 两种情况下断言"不写 / 写一次"。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.usage_service import BudgetStatus, UsageService
from app.core.audit_actions import ACTION_QUOTA_EXHAUSTED_100, ACTION_QUOTA_WARN_80

pytestmark = pytest.mark.unit

TENANT = "tnt_notify"
USER = "usr_notify"


class _FakeAudit:
    """审计替身：记录写入，并可指定 `exists` 的返回值。"""

    def __init__(self, *, existing: bool = False) -> None:
        """初始化。

        Args:
            existing: `exists()` 的返回值（True 表示本期已有记录）。
        """
        self.existing = existing
        self.records: list[dict[str, Any]] = []
        self.exists_calls: list[tuple[str, str]] = []

    async def exists(self, *, tenant_id: str, action: str, resource_id: str) -> bool:
        """替身：查重。

        Args:
            tenant_id: 租户 ID。
            action: 动作编码。
            resource_id: 资源 ID（本期）。

        Returns:
            bool: 由 `existing` 决定。
        """
        del tenant_id
        self.exists_calls.append((action, resource_id))
        return self.existing

    async def record(self, **kwargs: Any) -> None:
        """替身：写入。

        Args:
            **kwargs: 审计字段。
        """
        self.records.append(kwargs)


def _service(audit: _FakeAudit | None, *, exhausted: bool, warn: bool) -> UsageService:
    """构造只用于通知判据的服务（`budget_status` 被替换成固定结果）。

    Args:
        audit: 审计替身。
        exhausted: 是否触顶。
        warn: 是否达预警线。

    Returns:
        UsageService: 服务实例。
    """
    service = UsageService()
    service._audit = audit  # type: ignore[assignment]  # noqa: SLF001
    status = BudgetStatus(exhausted=exhausted, warn=warn)

    async def _fixed(**_kwargs: Any) -> BudgetStatus:
        return status

    service.budget_status = _fixed  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_exhausted_notification_is_idempotent() -> None:
    """④a：本期已有记录 → 一次都不写；没有记录 → 只写一次。"""
    audit = _FakeAudit(existing=True)
    service = _service(audit, exhausted=True, warn=True)

    for _ in range(5):
        await service._notify_admin_threshold(  # noqa: SLF001
            status=BudgetStatus(exhausted=True, warn=True), tenant_id=TENANT, user_id=USER
        )

    assert audit.exists_calls, "必须走查重分支（否则测不出幂等是否真的生效）"
    assert audit.records == [], "本期已有记录时不得再写（幂等）"
    assert all(action == ACTION_QUOTA_EXHAUSTED_100 for action, _ in audit.exists_calls)

    audit2 = _FakeAudit(existing=False)
    service2 = _service(audit2, exhausted=True, warn=True)
    await service2._notify_admin_threshold(  # noqa: SLF001
        status=BudgetStatus(exhausted=True, warn=True), tenant_id=TENANT, user_id=USER
    )

    assert len(audit2.records) == 1, "没有记录时必须恰好写一条"
    written = audit2.records[0]
    assert written["action"] == ACTION_QUOTA_EXHAUSTED_100
    assert written["resource_type"] == "tenant_budget", "额度类资源，别写成 billing"
    assert written["resource_id"], "resource_id 必须是本期 period"


@pytest.mark.asyncio
async def test_warn_only_writes_warn_action() -> None:
    """④b：≥80% 未超额 → 只写 `_80`，不写 `_100`。"""
    audit = _FakeAudit(existing=False)
    service = _service(audit, exhausted=False, warn=True)

    await service._notify_admin_threshold(  # noqa: SLF001
        status=BudgetStatus(exhausted=False, warn=True), tenant_id=TENANT, user_id=USER
    )

    assert [item["action"] for item in audit.records] == [ACTION_QUOTA_WARN_80]


@pytest.mark.asyncio
async def test_below_threshold_writes_nothing() -> None:
    """④c：未达预警线 → **两个 action 都没有**（证明普通请求零写、零查）。"""
    audit = _FakeAudit(existing=False)
    service = _service(audit, exhausted=False, warn=False)

    await service._notify_admin_threshold(  # noqa: SLF001
        status=BudgetStatus(exhausted=False, warn=False), tenant_id=TENANT, user_id=USER
    )

    assert audit.records == []
    assert audit.exists_calls == [], "未达阈值时连查重都不该发生（普通请求零成本）"


@pytest.mark.asyncio
async def test_notification_failure_does_not_break_budget_status() -> None:
    """审计写失败只记 WARN，不能把额度判定带崩（读路径的副产品）。"""

    class _Broken(_FakeAudit):
        async def exists(self, **_kwargs: Any) -> bool:
            raise RuntimeError("audit store down")

    audit = _Broken()
    service = _service(audit, exhausted=True, warn=True)

    await service._notify_admin_threshold(  # noqa: SLF001 - 不抛即通过
        status=BudgetStatus(exhausted=True, warn=True), tenant_id=TENANT, user_id=USER
    )
