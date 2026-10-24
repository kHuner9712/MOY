"""SEC-01 攻击用例：agent 任务跨租户越权（D-40 / AL-30 判据⑥⑦）。

**缺陷形态（已实测）**：`AgentService` 的 `_tasks` / `_steps` 是**进程级**
`dict[task_id]`，`get_task()` 不接收鉴权上下文，`list_tasks()` 第一行就是
`del auth`。于是持租户 B 合法令牌的人：

- 只要知道（或枚举到）A 的任务 ID，就能读到 A 的任务目标、结果与逐步产物；
- 打开「我的任务」列表就能看到 A 的任务 ID 与状态（比直取更容易被批量利用）。

**修复口径**（ADR-0006 §5.2 C1/C2）：索引改 `(tenant_id, task_id)` 复合键；
A3/A4/A5/A6 全部透传 `context.auth`；「本地没有就回落底座」的旁路删除，
未命中一律 `12001`（`TASK_NOT_FOUND`）。

**判据**（§5.3）：

1. 跨租户读/列/查步骤/取消/重试 一律业务化拒绝，且**响应体不含 A 的任何内容**；
2. 拒绝形态与「任务不存在」完全一致（同码同文案），不给攻击者探测接口；
3. A 自身的行为**一字不变**（行为不变的守卫用例，防止修复把功能一起关掉）。

平台口径两条（SEC-02 / SEC-03）：

4. `__platform__` + 运营角色 → 可进运营后台；
5. `__platform__` + `member` / 空角色 → `20003`（平台视角不再只凭租户声明）。

为什么这些用例必须标 `security`：它们是**验收红线**。任何一条红都意味着
「租户 B 能读租户 A 的数据」，属于发版阻断项，不能带着上线。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.security.conftest import (
    TENANT_A,
    TENANT_B,
    USER_A,
    USER_B,
    issue_token,
    write_headers,
)

from app.business import policy_service
from app.core import security as security_module
from app.core.errors import ErrorCode
from app.services.agent_service import reset_agent_service

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: agent 任务集合端点（A1 创建 / A2 列表）
TASKS_PATH = "/api/v1/agent/tasks"

#: 运营后台入口（B10 平台哨兵）
OPS_SETTINGS_PATH = "/api/v1/ops/settings"

#: 审计日志查询（U6，要求目录内权限点 `audit:log:read`）
AUDIT_LOGS_PATH = "/api/v1/identity/audit-logs"

#: 平台租户哨兵（与 `app.core.security.OPS_TENANT_ID` 同值）
OPS_TENANT = "__platform__"

#: A 的任务目标：一旦出现在 B 的响应体里就是**内容泄漏**（不只是状态码问题）
SECRET_GOAL = "把 A 公司的并购条款整理成清单-7f3a91"

#: 跨租户读写被拒时允许的业务码。
#:
#: `12001`（任务不存在）是当前实现的主路径：不区分「不存在」与「不属于你」，
#: 避免让攻击者把拒绝当成目录探测。`20003` 留给「未来若在端点层先做一次
#: 权限点判定」的情形——两者都属**拒绝**，绝不允许出现 `code == 0`。
DENIED_CODES = {int(ErrorCode.TASK_NOT_FOUND), int(ErrorCode.FORBIDDEN)}


@pytest.fixture(autouse=True)
def _isolate_agent_tasks() -> Iterator[None]:
    """每个用例前后清空 agent 任务单例（进程内存字典是全局的）。

    Yields:
        None: 用例执行期间的任务表是干净的。
    """
    reset_agent_service()
    yield
    reset_agent_service()


def _idem(tag: str) -> str:
    """生成一个唯一的幂等键（避免跨用例/跨请求的重放判定干扰断言）。

    Args:
        tag: 语义标签（便于失败时定位哪一步重放）。

    Returns:
        str: UUID v4 形态的幂等键。
    """
    return f"{tag}-{uuid.uuid4()}"


def _headers(tenant_id: str, user_id: str, *, roles: list[str] | None = None) -> dict[str, str]:
    """构造写请求头（合法令牌 + 幂等键）。

    Args:
        tenant_id: 令牌租户。
        user_id: 用户 ID。
        roles: 角色列表（None 表示使用默认 `member`）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = issue_token(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles if roles is not None else ["member"],
    )
    return write_headers(token, tenant=tenant_id, key=_idem(tenant_id))


def _read_headers(
    tenant_id: str, user_id: str, *, roles: list[str] | None = None
) -> dict[str, str]:
    """构造读请求头（GET 不需要幂等键）。

    Args:
        tenant_id: 令牌租户。
        user_id: 用户 ID。
        roles: 角色列表。

    Returns:
        dict[str, str]: 请求头。
    """
    token = issue_token(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles if roles is not None else ["member"],
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def _create_tenant_a_task(client: TestClient) -> dict[str, Any]:
    """以租户 A 身份创建一个 agent 任务（走 A1 真实端点，不直接改字典）。

    Args:
        client: 测试客户端。

    Returns:
        dict[str, Any]: `data` 体（含 `task_id`）。
    """
    response = client.post(
        TASKS_PATH,
        json={"conversation_id": "conv_sec_a", "goal": SECRET_GOAL},
        headers=_headers(TENANT_A, USER_A),
    )
    assert response.status_code == 200, f"A1 创建任务失败：{response.text}"
    body = response.json()
    assert body["code"] == 0, f"A1 返回业务失败：{body}"
    return body["data"]


def _assert_no_leak(response: Any) -> None:
    """断言拒绝响应**没有带出任何数据**，且不含 A 的任务目标文本。

    **为什么不只看状态码**：越权的危害是「内容被读到」。若实现改成了
    「先返回 200 再在字段里带出 A 的任务」，或者「返回 404 但 `data` 里塞了残留
    记录」，只看状态码会全绿而泄漏照样发生。因此两条一起断：

    1. `data` 必须是 `null`（拒绝响应不带业务数据——A 的任务 ID、状态、产物
       都只能从这里出去）；
    2. 响应体里不得出现 A 的 goal 文本（逐字比对，不做片段推断）。

    Args:
        response: `httpx.Response`（用鸭子类型标注，避免为一条辅助函数引入依赖）。
    """
    body = response.json()
    assert body["data"] is None, "拒绝响应不得带出任何任务数据"
    assert SECRET_GOAL not in response.text, "拒绝响应体不得回显 A 的任务目标"


# ===========================================================================
# 判据 1：跨租户读详情 —— 必须拒绝，且不泄漏内容
# ===========================================================================


def test_tenant_b_cannot_read_tenant_a_task_detail(oidc_client: TestClient) -> None:
    """B 持合法 JWT 读 A 的任务详情 → 拒绝，且响应体不含 A 的 goal/result。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.get(
        f"{TASKS_PATH}/{created['task_id']}",
        headers=_read_headers(TENANT_B, USER_B),
    )

    _assert_no_leak(response)
    assert response.status_code in {403, 404}, (
        f"跨租户直取任务详情必须被拒绝，实际 {response.status_code}：{response.text[:200]}"
    )
    body = response.json()
    assert body["code"] in DENIED_CODES, f"拒绝必须走业务码，实际 {body['code']}"
    assert body["data"] is None, "拒绝响应不得带出任何任务数据"


def test_denied_response_is_indistinguishable_from_missing(oidc_client: TestClient) -> None:
    """「别人的任务」与「不存在的任务」返回**完全一致**（不给探测接口）。

    若两者的码/文案有差别，攻击者就能用「哪句报错」判断某个 task_id 是否真实存在，
    进而配合其他缺口做定向攻击。
    """
    created = _create_tenant_a_task(oidc_client)
    headers_b = _read_headers(TENANT_B, USER_B)

    other = oidc_client.get(f"{TASKS_PATH}/{created['task_id']}", headers=headers_b)
    missing = oidc_client.get(f"{TASKS_PATH}/task_doesnotexist", headers=headers_b)

    assert other.status_code == missing.status_code
    assert other.json()["code"] == missing.json()["code"]
    assert other.json()["message"] == missing.json()["message"]


# ===========================================================================
# 判据 2：列表枚举 —— B 的列表里不许出现 A 的任务
# ===========================================================================


def test_tenant_b_list_excludes_tenant_a_tasks(oidc_client: TestClient) -> None:
    """B 查任务列表 → `meta.total == 0`，且不含 A 的任务 ID。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.get(TASKS_PATH, headers=_read_headers(TENANT_B, USER_B))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["meta"]["total"] == 0, "B 的列表不得包含 A 的任务（枚举面越权）"
    assert created["task_id"] not in response.text


# ===========================================================================
# 判据 3：步骤详情 —— 逐步产物信息量更大，同样必须拒绝
# ===========================================================================


def test_tenant_b_cannot_read_tenant_a_task_steps(oidc_client: TestClient) -> None:
    """B 查 A 的任务步骤 → 拒绝（12001/20003），不返回任何步骤。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.get(
        f"{TASKS_PATH}/{created['task_id']}/steps",
        headers=_read_headers(TENANT_B, USER_B),
    )

    _assert_no_leak(response)
    assert response.status_code in {403, 404}, f"跨租户查步骤必须被拒绝：{response.text[:200]}"
    body = response.json()
    assert body["code"] in DENIED_CODES
    assert body["data"] is None


# ===========================================================================
# 判据 4：取消 —— 越权「写」比越权「读」更严重（会让别人的任务无故中止）
# ===========================================================================


def test_tenant_b_cannot_cancel_tenant_a_task(oidc_client: TestClient) -> None:
    """B 取消 A 的任务 → 拒绝，且 A 侧任务状态**仍为 running**。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.post(
        f"{TASKS_PATH}/{created['task_id']}/cancel",
        headers=_headers(TENANT_B, USER_B),
    )

    _assert_no_leak(response)
    assert response.status_code in {403, 404}, f"跨租户取消必须被拒绝：{response.text[:200]}"
    body = response.json()
    assert body["code"] in DENIED_CODES

    # 关键断言：目标资源**没有被改动**（只看状态码会漏掉"拒绝了但仍执行了副作用"）
    check = oidc_client.get(
        f"{TASKS_PATH}/{created['task_id']}", headers=_read_headers(TENANT_A, USER_A)
    )
    assert check.status_code == 200, check.text
    assert check.json()["data"]["status"] == "running", "A 的任务不得被 B 取消"


# ===========================================================================
# 判据 5：重试/继续 —— 会创建新任务或推进别人的任务，必须拒绝
# ===========================================================================


def test_tenant_b_cannot_retry_tenant_a_task(oidc_client: TestClient) -> None:
    """B 重试 A 的任务 → 拒绝（12001），且不会为 A 造出第二个任务。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.post(
        f"{TASKS_PATH}/{created['task_id']}/retry",
        json={"decision": "retry_all", "reason": "跨租户探测"},
        headers=_headers(TENANT_B, USER_B),
    )

    _assert_no_leak(response)
    assert response.status_code in {403, 404}, f"跨租户重试必须被拒绝：{response.text[:200]}"
    assert response.json()["code"] in DENIED_CODES

    # A 的列表里仍然只有自己那一个任务（没有被 B 的重试"催生"出副本）
    listing = oidc_client.get(TASKS_PATH, headers=_read_headers(TENANT_A, USER_A))
    assert listing.status_code == 200
    assert listing.json()["data"]["meta"]["total"] == 1


# ===========================================================================
# 判据 5b：A6 SSE 事件流 —— 拒绝必须发生在**建流之前**
#
# 为什么单独一条：SSE 一旦开始写出，响应头就已经发出去了，**再也无法**改成一个
# 统一错误响应体（架构 §3.0.1）。也就是说 A6 的归属校验只要晚一步，就会变成
# 「200 + 一个空流」，客户端看起来"订阅成功"却永远收不到内容，而越权与否
# 只能靠"流里有什么"来判——那已经是泄漏之后的事了。
# ===========================================================================


def test_tenant_b_cannot_stream_tenant_a_task_events(oidc_client: TestClient) -> None:
    """B 订阅 A 的任务事件 → 12001，且 `content-type` **不是** `text/event-stream`。"""
    created = _create_tenant_a_task(oidc_client)

    response = oidc_client.get(
        f"{TASKS_PATH}/{created['task_id']}/events",
        headers=_read_headers(TENANT_B, USER_B),
    )

    assert response.status_code in {403, 404}, (
        f"跨租户订阅事件必须在建流前被拒绝，实际 {response.status_code}：{response.text[:200]}"
    )
    # 最强的判据：证明根本没有建立 SSE 流
    content_type = response.headers.get("content-type", "")
    assert "text/event-stream" not in content_type, "拒绝时必须没有建立 SSE 流"
    body = response.json()
    assert body["code"] in DENIED_CODES
    _assert_no_leak(response)


# ===========================================================================
# 判据 6：A 自己的行为不变（防止修复把功能一起关掉）
# ===========================================================================


def test_owner_can_still_read_list_and_cancel_own_task(oidc_client: TestClient) -> None:
    """A 读自己的任务 / 列自己的任务 / 取消自己的任务 → 全部正常。"""
    created = _create_tenant_a_task(oidc_client)
    headers_a = _read_headers(TENANT_A, USER_A)

    detail = oidc_client.get(f"{TASKS_PATH}/{created['task_id']}", headers=headers_a)
    assert detail.status_code == 200, detail.text
    assert detail.json()["code"] == 0
    assert detail.json()["data"]["task_id"] == created["task_id"]
    assert detail.json()["data"]["status"] == "running"

    listing = oidc_client.get(TASKS_PATH, headers=headers_a)
    assert listing.status_code == 200
    assert listing.json()["data"]["meta"]["total"] == 1
    assert listing.json()["data"]["items"][0]["task_id"] == created["task_id"]

    steps = oidc_client.get(f"{TASKS_PATH}/{created['task_id']}/steps", headers=headers_a)
    assert steps.status_code == 200, steps.text
    assert steps.json()["code"] == 0

    cancelled = oidc_client.post(
        f"{TASKS_PATH}/{created['task_id']}/cancel", headers=_headers(TENANT_A, USER_A)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_two_tenants_keep_separate_task_tables(oidc_client: TestClient) -> None:
    """A、B 各建一个任务 → 各自的列表里只有自己的那一个（复合键生效）。"""
    task_a = _create_tenant_a_task(oidc_client)
    response = oidc_client.post(
        TASKS_PATH,
        json={"conversation_id": "conv_sec_b", "goal": "整理 B 公司的周报"},
        headers=_headers(TENANT_B, USER_B),
    )
    assert response.status_code == 200, response.text
    task_b = response.json()["data"]

    listing_a = oidc_client.get(TASKS_PATH, headers=_read_headers(TENANT_A, USER_A))
    listing_b = oidc_client.get(TASKS_PATH, headers=_read_headers(TENANT_B, USER_B))
    ids_a = {item["task_id"] for item in listing_a.json()["data"]["items"]}
    ids_b = {item["task_id"] for item in listing_b.json()["data"]["items"]}

    assert ids_a == {task_a["task_id"]}
    assert ids_b == {task_b["task_id"]}


# ===========================================================================
# 平台口径 ①：`__platform__` + 运营角色 → 正常
# ===========================================================================


def test_platform_ops_role_can_reach_ops_console(oidc_client: TestClient) -> None:
    """`__platform__` + `["ops_operator"]` 调运营端点 → **非 403**（SEC-02 正例）。"""
    response = oidc_client.get(
        OPS_SETTINGS_PATH,
        headers=_read_headers(OPS_TENANT, USER_B, roles=["ops_operator"]),
    )
    assert response.status_code != 403, (
        f"带运营角色的平台令牌必须能进运营后台，实际 {response.status_code}：{response.text[:200]}"
    )


def test_wildcard_role_can_reach_ops_console(oidc_client: TestClient) -> None:
    """`*` 通配角色（任一租户）→ 运营端点放行（既有口径不变的守卫）。"""
    response = oidc_client.get(
        OPS_SETTINGS_PATH,
        headers=_read_headers(TENANT_A, USER_A, roles=["*"]),
    )
    assert response.status_code != 403, response.text[:200]


# ===========================================================================
# 平台口径 ②：只有租户声明不算平台视角（SEC-02 核心判据）
# ===========================================================================


@pytest.mark.parametrize(
    ("roles", "label"),
    [
        (["member"], "普通成员角色"),
        ([], "无角色声明"),
        (["owner"], "企业所有者角色"),
    ],
)
def test_platform_tenant_without_ops_role_is_denied(
    oidc_client: TestClient, roles: list[str], label: str
) -> None:
    """`__platform__` + 非运营角色 → `20003`（标签：{label}）。

    Args:
        oidc_client: 测试客户端。
        roles: 令牌角色。
        label: 用例标签（失败时定位是哪一种角色形态被放行）。
    """
    response = oidc_client.get(
        OPS_SETTINGS_PATH,
        headers=_read_headers(OPS_TENANT, USER_B, roles=roles),
    )
    assert response.status_code == 403, (
        f"「{label}」不得凭租户声明取得平台视角：{response.text[:200]}"
    )
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)
    assert response.json()["data"] is None


# ===========================================================================
# 平台口径 ③：自造权限点不再得分（SEC-03）
# ===========================================================================


@pytest.mark.parametrize(
    "roles",
    [
        ["x:y"],
        ["admin:*"],
        ["knowledge:base:read:extra"],
    ],
)
def test_invented_permission_role_grants_nothing(oidc_client: TestClient, roles: list[str]) -> None:
    """`x:y` / `admin:*` 这类自造串 → 调需目录内权限点的端点一律 `20003`。

    端点选 U6（`audit:log:read`）：它走 `PolicyService.require_point`，
    判据完全来自「角色展开出的权限点集合」，不存在端点层的其他短路。

    Args:
        oidc_client: 测试客户端。
        roles: 令牌角色（自造串）。
    """
    response = oidc_client.post(
        AUDIT_LOGS_PATH,
        json={},
        headers=_headers(TENANT_A, USER_A, roles=roles),
    )
    assert response.status_code == 403, (
        f"自造角色 {roles} 不得获得任何权限点：{response.text[:200]}"
    )
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_directory_permission_point_as_role_still_works(oidc_client: TestClient) -> None:
    """目录内权限点当角色签发（阶段一兼容形态）→ 仍生效（不得误伤）。

    `audit:log:read` 在 `BUSINESS_PERMISSIONS` 里，因此兼容口保留；
    这正是「白名单」与「一律拒绝」的区别：收紧的是**目录外**的自造串。
    """
    response = oidc_client.post(
        AUDIT_LOGS_PATH,
        json={},
        headers=_headers(TENANT_A, USER_A, roles=["audit:log:read"]),
    )
    assert response.status_code == 200, f"目录内权限点当角色必须继续生效：{response.text[:200]}"
    assert response.json()["code"] == 0


def test_permission_whitelist_is_the_directory() -> None:
    """`permissions_for_roles` 只认目录内权限点（单点口径，防端点层漏判）。"""
    assert policy_service.permissions_for_roles(["x:y"]) == frozenset()
    assert policy_service.permissions_for_roles(["admin:*"]) == frozenset()
    assert policy_service.permissions_for_roles(["audit:log:read"]) == frozenset({"audit:log:read"})
    # 内置角色矩阵不受影响
    assert "team:member:invite" in policy_service.permissions_for_roles(["owner"])


def test_ops_role_keys_stay_in_sync_with_policy_service() -> None:
    """`core.security.OPS_ROLE_KEYS` 必须**恰好等于**「通配 + 运营角色」。

    两处是**字面量重复**（core 不能 import business，会成环）。这条用例是
    唯一能拦住「改了一边忘了另一边」的地方，因此判据必须是**集合相等**而不是
    `in`：`in` 只保证「运营角色在里面」，往 `OPS_ROLE_KEYS` 里顺手加个 `member`
    仍然全绿——那正是把平台视角扩散给普通成员的路子（`__platform__` + `member`
    即可跨租户），必须当场变红。
    """
    assert security_module.OPS_ROLE_KEYS == frozenset(
        {security_module.OPS_WILDCARD_ROLE, policy_service.ROLE_OPS}
    )
