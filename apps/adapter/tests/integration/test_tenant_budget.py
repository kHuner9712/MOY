"""租户月度预算：80% 提示 + 100% 拦停（阶段四 T4-04.3，架构 §3.4 / §7.5）。

**判据（PRD P4-11 / 架构 T4-04.3）**

1. 当月用量到 `monthly_budget_units × warn_ratio`（默认 **80%**）→ 响应带**恰好一条**
   提示，且 `code` 仍为 0（提醒不是拦停）；
2. 到 **100%** → 可挂起的调用面（文档 / 自动化）**不再执行**，对话按 2026-10-27
   裁决继续可用、只限长。

**为什么"预算"要独立成文件**（与套餐额度分开）：

- 套餐额度是**个人**口径（`snapshot().remaining_units`，按成员行求和）；
- 租户预算**与企业当月总用量**比较（`tenant_total`，按 tenant 行求和），
  是"给整个企业设一个天花板"的另一条判据；
- 两条判据写在 `UsageService.budget_status()` 里、合成一个 `exhausted`，
  但**取值来源、阈值语义、修复动作**完全不同（一个找管理员买席位，一个调预算）。
  只测一条会漏掉另一条的接线（例如忘读 `tenant_budget` 表、忘把 `warn_ratio` 用上）。

**判据语义对齐实现，而不是猜**（`app/db/repositories/budget_repo.py`）：

- `monthly_budget_units = 0` → **没设**额度，护栏只看套餐（不拦、不提示）；
- `enabled = false` → **明确关掉**护栏（有额度也不拦）；
- `should_warn(used)` 在 `ratio >= warn_ratio` 且**未用尽**时为真（用尽走另一条）。

错误码取**现有注册表**（`ErrorCode.QUOTA_EXHAUSTED = 15006`），不新造 18xxx——
额度类码段是 `15xxx`，预算与套餐对被拒用户是同一件事（"这个月不能再用"）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.usage_service import (
    BUDGET_WARN_MESSAGE,
    HOLD_REASON_QUOTA,
    UsageService,
    get_usage_service,
)
from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode
from app.db.repositories.budget_repo import (
    TenantBudgetRecord,
    get_budget_store,
    reset_budget_store,
)
from app.db.repositories.usage import get_usage_store
from app.services.answer_cap import STANDARD_MODE_NOTICE
from tests.conftest import TEST_USER_ID

pytestmark = [pytest.mark.integration]

#: 私有化部署下令牌里的租户会被收敛为 `default`
TENANT = "default"

GENERATE_PATH = "/api/v1/model/generate"

#: 预算额度（取值只为让 80% / 100% 都落成整数，不代表任何真实套餐）
BUDGET_UNITS = 1000

#: 注册表里的额度码（**唯一来源**：测试不另抄数字，否则码段漂移测不出来）
QUOTA_CODE = int(ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].code)


@pytest.fixture(autouse=True)
def _clean_budget_store() -> Iterator[None]:
    """用例前后清空预算单例。

    根 `conftest._reset_all()` 重置了用量 / 计费 / 任务等一批单例，但**没有**重置
    预算存储（`reset_budget_store` 目前只被本文件调用）。不在这里清，本文件写入的
    `tenant_budget` 行会顺着进程级单例漏到后面任何"恰好用 `default` 租户"的用例里，
    表现是"单独跑绿、全量跑红"。清两次（前 + 后）比只清一次更稳：
    前置清掉别人的残留，后置不把自己的残留留给别人。

    Yields:
        Iterator[None]: 无值。
    """
    reset_budget_store()
    try:
        yield
    finally:
        reset_budget_store()


async def _set_budget(*, units: int, warn_ratio: float = 0.80, enabled: bool = True) -> None:
    """写一条租户预算（走仓储接口，与运营后台同一条路径）。

    Args:
        units: 月度额度（0 = 没设）。
        warn_ratio: 预警比例。
        enabled: 是否启用。
    """
    store = get_budget_store()
    await store.upsert(
        TenantBudgetRecord(
            tenant_id=TENANT,
            monthly_budget_units=units,
            warn_ratio=warn_ratio,
            enabled=enabled,
        )
    )


async def _consume(units: int, *, idem: str) -> None:
    """记一笔企业用量（**真实写入口**，不直接往聚合表塞行）。

    Args:
        units: 额度。
        idem: 幂等键（同键重放不会重复累加）。
    """
    usage = UsageService(store=get_usage_store())
    await usage.record_generation(
        tenant_id=TENANT,
        user_id=TEST_USER_ID,
        generation_id=idem,
        units=units,
        has_authorized_bases=False,
    )


def _generate(client: TestClient, headers: dict[str, str], *, key: str) -> dict[str, Any]:
    """调用 M1 非流式生成。

    Args:
        client: 测试客户端。
        headers: 鉴权请求头。
        key: 幂等键。

    Returns:
        dict[str, Any]: 响应体。
    """
    response = client.post(
        GENERATE_PATH,
        json={
            "conversation_id": "conv_tenant_budget",
            "profile": "general",
            "messages": [{"role": "user", "content": "帮我整理本周进展"}],
        },
        headers={**headers, "Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ===========================================================================
# 80%：提示（不拦停）
# ===========================================================================
async def test_eighty_percent_budget_adds_exactly_one_notice(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """用量到 80% → 响应带 1 条提示，`code` 仍为 0。

    「恰好一条」的判据不是文字游戏：提示字段只有 `notice_code` / `notice_message`
    两个位置，若实现把预算提示与套餐提示**各写一次**（或把同一件事同时挂到
    `code` 与 `notice_*` 上），用户会收到两条互相矛盾的话——一条说"已用到八成"、
    一条说"已用完"。这里断言文案就是预算预警那条，且外层 `code=0`。
    """
    await _set_budget(units=BUDGET_UNITS)
    await _consume(int(BUDGET_UNITS * 0.8), idem="gen_budget_warn_seed")

    body = _generate(client, auth_headers, key="budget-warn")
    assert body["code"] == 0, "80% 只是提醒，不得变成错误码（前端会把非 0 当异常弹框）"
    data = body["data"]
    assert data["notice_code"] == QUOTA_CODE
    assert data["notice_message"] == BUDGET_WARN_MESSAGE, (
        f"提示文案必须是预算预警那条，实际 {data['notice_message']!r}"
    )
    # 「一条」：提示字段只有一个码 + 一句文案，不得再夹带别的额度提示
    assert sum(1 for key in data if key.startswith("notice_")) == 2
    assert data["notice_message"].count("八成") == 1

    # 判定本身也要能自证（否则"提示恰好出现了"可能只是文案被硬编码在别处）
    usage = get_usage_service()
    status = await usage.budget_status(tenant_id=TENANT, user_id=TEST_USER_ID)
    assert status.budget_enabled is True
    assert status.exhausted is False
    assert status.warn is True
    assert 0.8 <= status.budget_ratio < 1.0, f"预算比例异常：{status.budget_ratio}"


async def test_below_warning_ratio_has_no_notice(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """未到预警线（79%）→ 无提示（避免"天天提醒"把提示变成噪音）。"""
    await _set_budget(units=BUDGET_UNITS)
    await _consume(int(BUDGET_UNITS * 0.79), idem="gen_budget_quiet_seed")

    body = _generate(client, auth_headers, key="budget-quiet")
    assert body["code"] == 0
    assert body["data"]["notice_code"] == 0
    assert body["data"]["notice_message"] == ""


async def test_disabled_budget_never_warns(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """`enabled=false` → 即使已用满额度也不提示、不拦（"关掉"≠"没设"）。"""
    await _set_budget(units=BUDGET_UNITS, enabled=False)
    await _consume(BUDGET_UNITS, idem="gen_budget_disabled_seed")

    body = _generate(client, auth_headers, key="budget-disabled")
    assert body["code"] == 0
    assert body["data"]["notice_code"] == 0
    usage = get_usage_service()
    status = await usage.budget_status(tenant_id=TENANT, user_id=TEST_USER_ID)
    assert status.budget_enabled is False
    assert status.exhausted is False


# ===========================================================================
# 100%：拦停（可挂起的调用面不再执行）
# ===========================================================================
async def test_hundred_percent_budget_marks_capability_exhausted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """用量到 100% → 可挂起能力被判为"已用尽"，对话按现行口径继续可用、只限长。

    这条用例同时钉住两件事，防止下一个人按旧判据改代码/改测试：

    1. **拦停的判定源是 `budget.exhausted`**（不是套餐额度：本用例根本没订阅）；
    2. **对话不返回 402**（2026-10-27 用户裁决，PRD 5.3 ①）。谁把对话改回硬止损，
       这里会红；谁又把文档/自动化改成"照常执行"，`is_capability_exhausted`
       的两条断言会红。
    """
    await _set_budget(units=BUDGET_UNITS)
    await _consume(BUDGET_UNITS, idem="gen_budget_full_seed")

    usage = get_usage_service()
    status = await usage.budget_status(tenant_id=TENANT, user_id=TEST_USER_ID)
    assert status.budget_enabled is True
    assert status.budget_used_units >= BUDGET_UNITS
    assert status.exhausted is True, "预算用尽必须被判为拦停"

    # 可挂起的两路：判定为已用尽（下一跳即"不执行"）
    assert await usage.is_capability_exhausted(
        capability="document", tenant_id=TENANT, user_id=TEST_USER_ID
    )
    assert await usage.is_capability_exhausted(
        capability="automation", tenant_id=TENANT, user_id=TEST_USER_ID
    )

    # 对话：继续可用，但带常规模式提示（不是错误码）
    body = _generate(client, auth_headers, key="budget-full")
    assert body["code"] == 0, "对话超额不得返回 402（2026-10-27 裁决）"
    data = body["data"]
    assert data["content"], "超额仍必须给出回答（常规模式，不是空壳）"
    assert data["notice_code"] == QUOTA_CODE
    assert data["notice_message"] == STANDARD_MODE_NOTICE


async def test_hundred_percent_budget_stops_automation_task_from_running(
    settings: Settings,
) -> None:
    """预算用尽 → A1 任务**照建但不执行**（一步都没跑）。

    为什么用 A1 证明"被拒"：预算/额度护栏没有"拒绝创建"这个语义（PRD 5.3 /
    P4-11 ③ 要求"排队到额度恢复"），所以"被拒"的可观测形态就是
    **`status=queued` + `hold_reason=quota` + 原语门面零调用**。
    只断言 `queued` 不够——并发上限也会让任务 `queued`，那说明不了额度起了作用。
    """
    from app.schemas.agent import CreateTaskRequest
    from tests.unit import task_helpers

    reset_budget_store()
    store = get_budget_store()
    await store.upsert(
        TenantBudgetRecord(
            tenant_id=task_helpers.TENANT, monthly_budget_units=BUDGET_UNITS, warn_ratio=0.80
        )
    )
    usage = UsageService(settings, store=get_usage_store())
    await usage.record_generation(
        tenant_id=task_helpers.TENANT,
        user_id=TEST_USER_ID,
        generation_id="gen_budget_a1_seed",
        units=BUDGET_UNITS,
        has_authorized_bases=False,
    )

    manifest = task_helpers.fixture_manifest(
        steps=[
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft", "summary"],
                "on_failure": "abort",
            }
        ]
    )
    gateway = task_helpers.EchoGateway()
    service, tasks, _artifacts, _usage = task_helpers.build_service(manifest, gateway=gateway)
    # 真实计量服务：预算判据只有一处（`UsageService.budget_status`），替身测不到
    service._usage = get_usage_service(settings)  # noqa: SLF001 - 注入真实唯一真源

    data = await service.create_plugin_task(
        CreateTaskRequest(
            conversation_id="conv_budget_a1",
            plugin_id=manifest.plugin_id,
            inputs={"topic": "季度复盘"},
        ),
        auth=task_helpers.auth(TEST_USER_ID),
    )

    assert gateway.calls == [], f"预算用尽时不许执行任何步骤，实际执行了 {gateway.calls}"
    assert data.status.value == "queued"
    record = await tasks.get_task(task_helpers.TENANT, data.task_id)
    assert record is not None
    assert record.hold_reason == HOLD_REASON_QUOTA


async def test_budget_ratio_and_thresholds_follow_repository_semantics() -> None:
    """预算记录的阈值语义与仓储实现一致（80% 提示 / 100% 用尽 / 关闭即失效）。

    直接测记录对象而不是服务：这三条是"配置语义"，与 HTTP 无关；
    写成端点用例只会让它们更慢、更难定位。真正的接线由上两条用例负责。
    """
    record = TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=BUDGET_UNITS)
    assert record.effective is True
    assert record.should_warn(799) is False
    assert record.should_warn(800) is True
    # 用尽是另一条判据：此时 `should_warn` 必须为假（否则会同时提示"八成"与"用完"）
    assert record.exhausted(1000) is True
    assert record.should_warn(1000) is False
    assert record.usage_ratio(1000) == pytest.approx(1.0)

    unset = TenantBudgetRecord(tenant_id=TENANT)
    assert unset.effective is False
    assert unset.should_warn(10_000) is False
    assert unset.exhausted(10_000) is False
    assert unset.usage_ratio(10_000) == 0.0

    off = TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=BUDGET_UNITS, enabled=False)
    assert off.effective is False
    assert off.exhausted(BUDGET_UNITS) is False
