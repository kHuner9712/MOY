"""管理员额度通知 · **端到端**判据（P4-11 ④a）：真实 `budget_status` 连发 5 次。

为什么单独一个文件（而不是塞进 `tests/unit/test_quota_admin_notification.py`）：

- 那个文件测的是 `_notify_admin_threshold` 的**方法契约**（查重分支、阈值分离、
  普通请求零成本、写失败不打断）；
- 本文件测的是**接线**：`budget_status` 真的调到了那个方法。
  接线掉了的话，方法级用例会全绿而功能根本不存在——本轮反复出现的
  "看起来实现了、实际没接上"。两类判据的目标不同，分文件才看得出各自覆盖什么。

判据：真实 `budget_status` 连续 5 次（同一租户同一期）→ 审计里
`action='billing.quota_exhausted_100'` 且 `resource_id=本期` 的条目**恰好 1 条**。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import AuditService
from app.business.usage_service import UsageService
from app.core.audit_actions import ACTION_QUOTA_EXHAUSTED_100
from app.core.config import Settings
from app.db.repositories.budget_repo import InMemoryBudgetStore, TenantBudgetRecord
from app.db.repositories.usage import InMemoryUsageStore
from tests.unit.test_usage_guard import TENANT, USER, _event

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_repeated_budget_checks_write_one_admin_notification() -> None:
    """④a 端到端：连发 5 次 `budget_status` → 审计只留 1 条触顶通知。

    用真实 `UsageService.budget_status`（不是直接调 `_notify_admin_threshold`），
    因此能钉住"接线存在"；用内存审计记录器读回，验证幂等与资源键。
    """
    settings = Settings(env="test")
    store = InMemoryUsageStore()
    budgets = InMemoryBudgetStore()
    await budgets.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=100))
    await store.append(_event(100))

    audit = AuditService(settings)
    service = UsageService(settings, store=store, budgets=budgets, audit=audit)

    for _ in range(5):
        status = await service.budget_status(tenant_id=TENANT, user_id=USER)
        assert status.exhausted is True, "造数失败：本应已触顶"

    entries = await audit.recorder.entries_for(TENANT)
    matched = [item for item in entries if item.action == ACTION_QUOTA_EXHAUSTED_100]

    assert len(matched) == 1, f"连发 5 次却写了 {len(matched)} 条（幂等失效）"
    assert matched[0].resource_type == "tenant_budget", "额度类资源不应写成 billing"
    assert matched[0].resource_id, "resource_id 必须是本期 period（YYYY-MM）"
