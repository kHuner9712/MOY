"""B6 四层授权与会话能力集（security 用例）。

**验收红线**：这组用例覆盖「租户 → 权限点 → 会话能力 → 资源」四层各自的拒绝、
四层交集（上层允许、下层拒绝）、越权放大尝试、跨租户尝试，以及拒绝留痕。

为什么必须标 `security`：任何一条红都意味着「客户端能把自己的执行范围放大到
超出调用者既有权限」，属于发版阻断项。

判定顺序（与 `app/core/capability.py` 的 `AUTHZ_LAYER_ORDER` 逐字一致）：

1. 租户层：拒绝 → `20003`；
2. 权限点层：拒绝 → `20003`；
3. 会话能力层：拒绝 → `14009`（声明越界 / 操作超出声明）；
4. 资源层：拒绝 → `20003`。

四层是**交集**：任一层拒绝即整体拒绝，首拒即停；下层不得绕过上层。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
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

from app.business.authz_service import (
    INHERITED_CAPABILITY_PATHS,
    AuthorizationService,
    reset_authorization_service,
)
from app.business.policy_service import PolicySnapshot, reset_policy_service
from app.core.capability import (
    KNOWN_ARTIFACT_FORMATS,
    KNOWN_TOOL_TYPES,
    AuthzRequest,
    CapabilitySet,
    ResourceRef,
)
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.tenant import PLATFORM_TENANT
from app.services.agent_service import reset_agent_service

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: agent 任务集合端点（A1）
TASKS_PATH = "/api/v1/agent/tasks"

#: 成员权限点（含 `tool:execute`，但**不含** `audit:log:read`）
MEMBER_POINTS = frozenset(
    {
        "knowledge:base:read",
        "tool:connection:manage",
        "tool:execute",
        "billing:subscription:read",
    }
)

#: 访客权限点（**不含** `tool:execute`）
GUEST_POINTS = frozenset(
    {
        "knowledge:base:read",
        "tool:connection:manage",
        "billing:subscription:read",
    }
)


class _FakePolicy:
    """可注入的权限快照服务（不读数据库）。"""

    def __init__(self, points: frozenset[str]) -> None:
        """初始化。

        Args:
            points: 调用者的权限点集合。
        """
        self._points = points

    async def snapshot(self, auth: AuthContext) -> PolicySnapshot:
        """返回固定权限点。

        Args:
            auth: 鉴权上下文。

        Returns:
            PolicySnapshot: 权限点快照。
        """
        return PolicySnapshot(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            permission_points=self._points,
        )


class _FakeAudit:
    """收集拒绝留痕的审计替身。"""

    def __init__(self) -> None:
        """初始化。

        self.calls: 每次 `record` 的 kwargs。
        """
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        """记录一次调用。

        Args:
            **kwargs: 审计服务入参。
        """
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def _isolate_singletons() -> Iterator[None]:
    """每个用例前后重置授权 / 策略 / agent 任务单例。

    Yields:
        None: 用例执行期间不携带上一个用例的权限快照与能力声明。
    """
    reset_authorization_service()
    reset_policy_service()
    reset_agent_service()
    yield
    reset_authorization_service()
    reset_policy_service()
    reset_agent_service()


def _make_service(points: frozenset[str]) -> tuple[AuthorizationService, _FakeAudit]:
    """构造带假依赖的授权服务。

    Args:
        points: 调用者权限点。

    Returns:
        tuple[AuthorizationService, _FakeAudit]: 服务与审计替身。
    """
    audit = _FakeAudit()
    return AuthorizationService(policy=_FakePolicy(points), audit=audit), audit


def _auth(tenant_id: str = TENANT_A, user_id: str = USER_A) -> AuthContext:
    """构造鉴权上下文。

    Args:
        tenant_id: 令牌租户。
        user_id: 令牌用户。

    Returns:
        AuthContext: 上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=tenant_id)


def _declared(
    *,
    tools: Iterable[str] = (),
    resources: Iterable[str] = (),
    formats: Iterable[str] = (),
) -> CapabilitySet:
    """构造三个维度全部声明的能力集。

    Args:
        tools: 工具维度。
        resources: 资源类型维度。
        formats: 产物格式维度。

    Returns:
        CapabilitySet: 完整声明。
    """
    return CapabilitySet(
        tools=frozenset(tools),
        resource_types=frozenset(resources),
        artifact_formats=frozenset(formats),
    )


def _assert_denied(exc: pytest.ExceptionInfo[ApiError], code: ErrorCode) -> None:
    """断言业务异常码。

    Args:
        exc: `pytest.raises` 捕获对象。
        code: 期望业务码。
    """
    assert exc.value.code == code, f"期望 {int(code)}，实际 {int(exc.value.code)}"


def _layer_of(audit: _FakeAudit) -> str:
    """取最后一次留痕的拒绝层。

    Args:
        audit: 审计替身。

    Returns:
        str: 层名。
    """
    assert audit.calls, "拒绝必须留痕"
    return str(audit.calls[-1]["change_summary"]["layer"])


# ===========================================================================
# 第 1 层：租户
# ===========================================================================


async def test_tenant_layer_rejects_cross_tenant_request() -> None:
    """生效租户 ≠ 令牌租户 → 20003，且在租户层被拒。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(tenant_id=TENANT_B, operation="agent.task.create"),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "tenant"


async def test_tenant_layer_rejects_platform_without_ops_point() -> None:
    """平台租户缺少 `ops:console:access` → 20003（平台视角不能只凭租户声明）。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(tenant_id=PLATFORM_TENANT),
            AuthzRequest(tenant_id=PLATFORM_TENANT, operation="ops.settings.read"),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "tenant"


async def test_tenant_layer_allows_platform_with_ops_point() -> None:
    """平台租户 + 运营权限点 → 通过租户层。"""
    service, audit = _make_service(frozenset({"ops:console:access"}))
    declared = _declared(resources=("ops",))

    effective = await service.authorize(
        _auth(tenant_id=PLATFORM_TENANT),
        AuthzRequest(
            tenant_id=PLATFORM_TENANT,
            declared=declared,
            operation="ops.settings.read",
        ),
    )

    assert effective == declared
    assert audit.calls == []


# ===========================================================================
# 第 2 层：角色 / 权限点
# ===========================================================================


async def test_permission_layer_rejects_missing_point() -> None:
    """缺少操作所需权限点 → 20003，且在权限点层被拒。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                permission="audit:log:read",
                operation="audit.log.read",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "permission"


async def test_permission_layer_allows_holder() -> None:
    """持有权限点 → 通过权限点层。"""
    service, audit = _make_service(frozenset({"audit:log:read"}))
    declared = _declared(resources=("audit_log",))

    effective = await service.authorize(
        _auth(),
        AuthzRequest(
            tenant_id=TENANT_A,
            permission="audit:log:read",
            declared=declared,
            operation="audit.log.read",
        ),
    )

    assert effective == declared
    assert audit.calls == []


async def test_permission_layer_runs_before_capability_layer() -> None:
    """权限点缺失 + 声明越界 → 拒绝落在权限点层（20003），不是能力层（14009）。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                permission="audit:log:read",
                declared=_declared(tools=KNOWN_TOOL_TYPES),
                operation="audit.log.read",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "permission", "首拒即停，上层不得被下层跳过"


# ===========================================================================
# 第 3 层：会话能力
# ===========================================================================


async def test_capability_layer_rejects_tool_beyond_upper() -> None:
    """无 `tool:execute` 却声明工具 → 14009（能力层，声明越界）。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(tools=("email",), resources=("artifact",)),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert _layer_of(audit) == "capability"
    assert audit.calls[-1]["change_summary"]["reason"] == "declaration_exceeds_upper"


async def test_capability_layer_rejects_resource_type_beyond_upper() -> None:
    """无 `audit:log:read` 却声明 `audit_log` → 14009。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(resources=("audit_log",)),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert _layer_of(audit) == "capability"


async def test_capability_layer_rejects_operation_outside_declaration() -> None:
    """声明里没有该工具，但本次操作需要它 → 14009（执行期收窄生效）。"""
    service, audit = _make_service(MEMBER_POINTS)
    declared = _declared(resources=("artifact",))

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=declared,
                required_tools=frozenset({"email"}),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert audit.calls[-1]["change_summary"]["reason"] == "operation_exceeds_capability"


async def test_capability_layer_does_not_silently_intersect() -> None:
    """越界声明整体拒绝，**不裁剪后继续**（两个越界项都必须出现在留痕里）。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(
                    tools=("email", "github"),
                    resources=("artifact",),
                    formats=KNOWN_ARTIFACT_FORMATS,
                ),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    violations = audit.calls[-1]["change_summary"]["violations"]
    values = {item["value"] for item in violations}
    assert {"email", "github"} <= values, "越界项必须全部登记，不得只留一项"
    assert "docx" in values, "产物格式同样受上界约束"


# ===========================================================================
# 第 4 层：资源
# ===========================================================================


async def test_resource_layer_rejects_foreign_tenant_object() -> None:
    """对象属于别的租户 → 20003（资源层）。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(resources=("plugin",), tools=("email",)),
                resource=ResourceRef(
                    resource_type="plugin",
                    resource_id="pl_foreign",
                    tenant_id=TENANT_B,
                    state="enabled",
                    required_state="enabled",
                ),
                operation="plugin.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "resource"
    assert audit.calls[-1]["change_summary"]["reason"] == "resource_tenant_mismatch"


async def test_resource_layer_rejects_non_owner_on_owner_only_resource() -> None:
    """个人资源非归属人 → 20003（产物令牌绑定等归属校验）。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(resources=("artifact",)),
                resource=ResourceRef(
                    resource_type="artifact",
                    resource_id="art_1",
                    tenant_id=TENANT_A,
                    owner_id=USER_B,
                    owner_only=True,
                ),
                operation="agent.artifact.edit",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "resource"
    assert audit.calls[-1]["change_summary"]["reason"] == "resource_owner_mismatch"


async def test_resource_layer_rejects_wrong_state() -> None:
    """插件安装未启用却要建任务 → 20003（资源状态校验）。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(resources=("plugin",), tools=("email",)),
                resource=ResourceRef(
                    resource_type="plugin",
                    resource_id="pl_1",
                    tenant_id=TENANT_A,
                    state="disabled",
                    required_state="enabled",
                ),
                operation="plugin.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "resource"
    assert audit.calls[-1]["change_summary"]["reason"] == "resource_state_mismatch"


# ===========================================================================
# 交集语义：上层允许、下层拒绝
# ===========================================================================


async def test_intersection_permission_allows_capability_rejects() -> None:
    """权限点层允许（`tool:execute` 在），但本次声明收窄到不允许工具 → 14009。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                permission="tool:execute",
                declared=_declared(resources=("artifact",)),
                required_tools=frozenset({"email"}),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert _layer_of(audit) == "capability"


async def test_intersection_capability_allows_resource_rejects() -> None:
    """能力层允许（声明含 plugin），资源层仍可拒绝 → 20003（不是 14009）。"""
    service, audit = _make_service(MEMBER_POINTS)
    declared = _declared(resources=("plugin",), tools=("email",))

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=declared,
                required_tools=frozenset({"email"}),
                required_resource_types=frozenset({"plugin"}),
                resource=ResourceRef(
                    resource_type="plugin",
                    resource_id="pl_2",
                    tenant_id=TENANT_A,
                    state="disabled",
                    required_state="enabled",
                ),
                operation="plugin.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "resource", "能力层通过后必须继续走资源层"


async def test_intersection_all_layers_allow_returns_declaration() -> None:
    """四层全通过：有效能力集就是声明本身，且没有拒绝留痕。"""
    service, audit = _make_service(MEMBER_POINTS)
    declared = _declared(
        tools=("email",),
        resources=("plugin", "artifact"),
        formats=("docx",),
    )

    effective = await service.authorize(
        _auth(),
        AuthzRequest(
            tenant_id=TENANT_A,
            permission="tool:execute",
            declared=declared,
            required_tools=frozenset({"email"}),
            required_resource_types=frozenset({"plugin"}),
            required_artifact_formats=frozenset({"docx"}),
            resource=ResourceRef(
                resource_type="plugin",
                resource_id="pl_3",
                tenant_id=TENANT_A,
                state="enabled",
                required_state="enabled",
            ),
            operation="plugin.task.create",
        ),
    )

    assert effective == declared
    assert audit.calls == []


# ===========================================================================
# 越权放大尝试
# ===========================================================================


async def test_escalation_declaring_all_tools_without_permission_is_rejected() -> None:
    """把全部工具与产物格式一次性声明 → 14009（放大尝试整体拒绝）。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(
                    tools=KNOWN_TOOL_TYPES,
                    resources=("artifact",),
                    formats=KNOWN_ARTIFACT_FORMATS,
                ),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    violations = audit.calls[-1]["change_summary"]["violations"]
    assert len(violations) == len(KNOWN_TOOL_TYPES) + len(KNOWN_ARTIFACT_FORMATS)


async def test_escalation_at_execution_time_is_rejected_not_degraded() -> None:
    """执行期想用未声明工具 → 14009，**不得**静默降级成声明内的工具继续跑。"""
    service, audit = _make_service(MEMBER_POINTS)
    declared = _declared(tools=("email",), resources=("artifact",), formats=("docx",))

    with pytest.raises(ApiError) as exc:
        await service.enforce_declaration(
            _auth(),
            declared=declared,
            required_tools=frozenset({"email", "github"}),
            tenant_id=TENANT_A,
            operation="plugin.task.advance",
        )

    _assert_denied(exc, ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert _layer_of(audit) == "capability"
    assert audit.calls[-1]["change_summary"]["requested"] == "tools:github"


# ===========================================================================
# 跨租户
# =========================================================================


async def test_cross_tenant_rejected_before_capability_layer() -> None:
    """跨租户 + 声明越界 → 租户层 20003（不是 14009），下层不得替上层放行。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(tenant_id=TENANT_A),
            AuthzRequest(
                tenant_id=TENANT_B,
                declared=_declared(tools=KNOWN_TOOL_TYPES),
                operation="agent.task.create",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "tenant"


async def test_cross_tenant_resource_rejected_with_valid_declaration() -> None:
    """声明完全合法，但对象在别的租户 → 资源层 20003。"""
    service, audit = _make_service(MEMBER_POINTS)

    with pytest.raises(ApiError) as exc:
        await service.authorize(
            _auth(tenant_id=TENANT_A),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(tools=("email",), resources=("artifact",)),
                resource=ResourceRef(
                    resource_type="artifact",
                    resource_id="art_foreign",
                    tenant_id=TENANT_B,
                ),
                operation="agent.artifact.edit",
            ),
        )

    _assert_denied(exc, ErrorCode.FORBIDDEN)
    assert _layer_of(audit) == "resource"


# ===========================================================================
# 拒绝留痕（可审计、不含敏感内容）
# ===========================================================================


async def test_denial_is_audited_with_layer_and_requested_item() -> None:
    """拒绝留痕含「谁 / 哪一层 / 请求什么能力项 / 什么操作」。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError):
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(tools=("email",)),
                operation="agent.task.create",
            ),
        )

    assert len(audit.calls) == 1
    call = audit.calls[0]
    summary = call["change_summary"]
    assert call["tenant_id"] == TENANT_A
    assert call["actor_id"] == USER_A
    assert call["result"] == "failure"
    assert call["action"] == "security.permission_denied"
    assert summary["layer"] == "capability"
    assert summary["operation"] == "agent.task.create"
    assert summary["requested"] == "tools:email"


async def test_denial_audit_excludes_sensitive_payload() -> None:
    """留痕只含结构化判定字段，不含请求正文、凭据或工具参数。"""
    service, audit = _make_service(GUEST_POINTS)

    with pytest.raises(ApiError):
        await service.authorize(
            _auth(),
            AuthzRequest(
                tenant_id=TENANT_A,
                declared=_declared(tools=("email",)),
                operation="agent.task.create",
            ),
        )

    summary = audit.calls[-1]["change_summary"]
    allowed = {
        "layer",
        "operation",
        "permission",
        "requested",
        "reason",
        "missing",
        "violations",
        "resource_type",
        "resource_id",
    }
    assert set(summary) <= allowed, f"留痕出现未登记字段：{set(summary) - allowed}"
    for forbidden in ("goal", "inputs", "token", "authorization", "body", "content"):
        assert forbidden not in summary, f"留痕不得包含 {forbidden}"


# ===========================================================================
# 兼容：未声明 = 继承调用者权限（登记待收敛）
# ===========================================================================


async def test_undeclared_path_inherits_caller_permissions() -> None:
    """不声明能力 → 有效能力集等于调用者上界（旧路径行为不变）。"""
    service, audit = _make_service(MEMBER_POINTS)

    effective = await service.authorize(
        _auth(),
        AuthzRequest(tenant_id=TENANT_A, operation="agent.task.create"),
    )

    assert effective.fully_declared, "继承结果必须是完整上界，供执行期比较"
    assert effective.allows("tools", "email")
    assert audit.calls == []


def test_inherited_paths_are_registered_for_convergence() -> None:
    """继承路径必须显式登记（不得静默放宽）。"""
    assert len(INHERITED_CAPABILITY_PATHS) == 3
    assert all(item for item in INHERITED_CAPABILITY_PATHS)


# ===========================================================================
# 对外 API：可选字段，旧请求不变（真跑，不 skip）
# ===========================================================================


def _write_headers(tenant_id: str, user_id: str, roles: list[str]) -> dict[str, str]:
    """构造写请求头（唯一幂等键）。

    Args:
        tenant_id: 租户。
        user_id: 用户。
        roles: 角色。

    Returns:
        dict[str, str]: 请求头。
    """
    token = issue_token(tenant_id=tenant_id, user_id=user_id, roles=roles)
    return write_headers(token, tenant=tenant_id, key=f"b6-{uuid.uuid4()}")


def test_api_declaration_within_permission_succeeds(oidc_client: TestClient) -> None:
    """成员提交合法声明 → 建单成功（能力集只收窄，不改变既有成功路径）。"""
    response = oidc_client.post(
        TASKS_PATH,
        json={
            "conversation_id": "conv_b6_ok",
            "goal": "B6 能力声明合法收窄",
            "capabilities": {
                "tools": [],
                "resource_types": ["artifact"],
                "artifact_formats": ["docx"],
            },
        },
        headers=_write_headers(TENANT_A, USER_A, ["member"]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0, response.text


def test_api_declaration_beyond_permission_returns_14009(oidc_client: TestClient) -> None:
    """访客（无 `tool:execute`）声明工具 → 403 + 14009（拒绝而非静默降级）。"""
    response = oidc_client.post(
        TASKS_PATH,
        json={
            "conversation_id": "conv_b6_deny",
            "goal": "B6 越权放大尝试",
            "capabilities": {
                "tools": ["email"],
                "resource_types": ["artifact"],
                "artifact_formats": ["docx"],
            },
        },
        headers=_write_headers(TENANT_A, USER_A, ["guest"]),
    )

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == int(ErrorCode.CAPABILITY_SCOPE_EXCEEDED)
    assert body["data"] is None


def test_api_omitting_capabilities_keeps_legacy_behavior(oidc_client: TestClient) -> None:
    """不提交 `capabilities` → 建单成功（对外 API 只增不改）。"""
    response = oidc_client.post(
        TASKS_PATH,
        json={"conversation_id": "conv_b6_legacy", "goal": "B6 继承权限路径"},
        headers=_write_headers(TENANT_A, USER_A, ["member"]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0, response.text


def test_api_unknown_capability_value_is_input_error(oidc_client: TestClient) -> None:
    """声明里的未知能力值 → 20001（请求校验失败，不进四层判定）。"""
    response = oidc_client.post(
        TASKS_PATH,
        json={
            "conversation_id": "conv_b6_bad",
            "goal": "B6 未知能力值",
            "capabilities": {
                "tools": ["not_a_tool"],
                "resource_types": ["artifact"],
                "artifact_formats": ["docx"],
            },
        },
        headers=_write_headers(TENANT_A, USER_A, ["member"]),
    )

    assert response.json()["code"] == int(ErrorCode.PARAM_INVALID), response.text


__all__: list[str] = []
