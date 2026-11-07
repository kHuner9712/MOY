"""四层授权判定服务（B6）。

判定顺序**固定**：`tenant → permission → capability → resource`。

- 任一层拒绝即整体拒绝，首拒即停；
- 会话能力层只做「声明 ⊆ 上界」判定，**不夹紧**：越界直接 `14009`；
- 拒绝一律留痕（层 / 权限点 / 请求项 / 操作名），不记录请求正文与凭据。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from app.core.audit_actions import ACTION_PERMISSION_DENIED
from app.core.capability import (
    AUTHZ_LAYER_ORDER,
    KNOWN_ARTIFACT_FORMATS,
    KNOWN_RESOURCE_TYPES,
    KNOWN_TOOL_TYPES,
    AuthzLayer,
    AuthzRequest,
    CapabilitySet,
    ResourceRef,
)
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.tenant import PLATFORM_TENANT

logger = get_logger(__name__)

#: 未声明能力的历史路径清单（**待收敛项**）。
#:
#: 这些路径在本版本按「继承调用者权限」运行，不因 B6 改变行为；后续版本要
#: 逐条收敛到显式能力声明，收敛前不得把它们当作已受能力集约束。
INHERITED_CAPABILITY_PATHS: Final[tuple[str, ...]] = (
    "agent.task.create（未提交 capabilities 字段）",
    "plugin.task.create（未提交 capabilities 字段）",
    "dify.task.execute（异步执行期无能力快照，仅建单期可校验）",
)

#: 运营权限点。
OPS_CONSOLE_POINT: Final[str] = "ops:console:access"

#: 工具执行权限点（声明任何工具都要求它）。
TOOL_EXECUTE_POINT: Final[str] = "tool:execute"

#: 个人资源类型：任何已鉴权用户都可以声明，归属由资源层校验。
PERSONAL_RESOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {"conversation", "memory", "task", "artifact"}
)


def upper_capabilities(permission_points: Iterable[str]) -> CapabilitySet:
    """由权限点派生能力上界（第 2 层 → 第 3 层的唯一出口）。

    上界只取决于**调用者实际拥有的权限点**，不读取任何客户端输入。
    这样客户端声明的任何值都只能与上界求交，无法凭空放大。

    Args:
        permission_points: 调用者在当前租户下的权限点集合。

    Returns:
        CapabilitySet: 三个维度均为集合的上界。
    """
    points = frozenset(permission_points)
    can_execute_tool = TOOL_EXECUTE_POINT in points

    tools = KNOWN_TOOL_TYPES if can_execute_tool else frozenset()
    formats = KNOWN_ARTIFACT_FORMATS if can_execute_tool else frozenset()

    resource_types: set[str] = set(PERSONAL_RESOURCE_TYPES)
    if "knowledge:base:read" in points:
        resource_types.update({"knowledge_base", "document"})
    if can_execute_tool or "tool:connection:manage" in points:
        resource_types.update({"plugin", "tool_connection"})
    if any(point.startswith("team:") for point in points):
        resource_types.add("team")
    if any(point.startswith("billing:") for point in points):
        resource_types.add("billing")
    if "audit:log:read" in points:
        resource_types.add("audit_log")
    if OPS_CONSOLE_POINT in points:
        resource_types.add("ops")

    return CapabilitySet(
        tools=tools,
        resource_types=frozenset(resource_types),
        artifact_formats=formats,
    )


class AuthorizationService:
    """四层授权判定服务。

    Attributes:
        _policy: 权限快照服务（测试可注入）。
        _audit: 审计服务（测试可注入）。
    """

    def __init__(self, *, policy: Any | None = None, audit: Any | None = None) -> None:
        """初始化服务。

        Args:
            policy: 可选权限快照服务；缺省取全局单例。
            audit: 可选审计服务；缺省取全局单例。
        """
        self._policy = policy
        self._audit = audit

    async def permission_points(self, auth: AuthContext) -> frozenset[str]:
        """取调用者在当前租户下的权限点集合。

        Args:
            auth: 鉴权上下文。

        Returns:
            frozenset[str]: 权限点集合（权威来源为 `PolicySnapshot`）。
        """
        policy = self._policy
        if policy is None:
            from app.business.policy_service import get_policy_service

            policy = get_policy_service()
        snapshot = await policy.snapshot(auth)
        return frozenset(snapshot.permission_points)

    def upper_capabilities(self, permission_points: Iterable[str]) -> CapabilitySet:
        """由权限点派生上界（实例方法便于测试替换）。

        Args:
            permission_points: 权限点集合。

        Returns:
            CapabilitySet: 能力上界。
        """
        return upper_capabilities(permission_points)

    async def enforce_declaration(
        self,
        auth: AuthContext,
        *,
        declared: CapabilitySet,
        required_tools: Iterable[str] = (),
        required_resource_types: Iterable[str] = (),
        required_artifact_formats: Iterable[str] = (),
        tenant_id: str = "",
        operation: str = "",
    ) -> None:
        """执行期按已持久化的能力快照做准入（不再重算上界）。

        上界已在建单期校验并持久化；执行期只验证「本次操作所需项是否仍在声明
        内」，因此不会出现「用执行期的权限重算把已收敛的声明放大」的情况。

        Args:
            auth: 发起人上下文。
            declared: 已持久化的能力快照（必须完整声明）。
            required_tools: 本次操作实际会用到的工具。
            required_resource_types: 本次操作实际会访问的资源类型。
            required_artifact_formats: 本次操作实际会产出的产物格式。
            tenant_id: 任务所属租户（默认等于 `auth.tenant_id`）。
            operation: 业务操作名（审计用）。

        Raises:
            ApiError: 租户不一致（20003）或操作超出声明（14009）。
        """
        request = AuthzRequest(
            tenant_id=tenant_id or auth.tenant_id,
            declared=declared,
            required_tools=frozenset(required_tools),
            required_resource_types=frozenset(required_resource_types),
            required_artifact_formats=frozenset(required_artifact_formats),
            operation=operation,
        )
        await self._check_tenant(auth, request, frozenset())
        missing = self._missing_requirements(declared, request)
        if missing:
            await self._deny(
                auth,
                AuthzLayer.CAPABILITY,
                request,
                reason="operation_exceeds_capability",
                requested="; ".join(missing),
                extra={"missing": missing},
            )

    @staticmethod
    def _missing_requirements(declared: CapabilitySet, request: AuthzRequest) -> list[str]:
        """列出本次操作所需、但未被声明允许的项。

        Args:
            declared: 有效能力集。
            request: 判定输入。

        Returns:
            list[str]: 形如 `tools:email` 的缺失项；空列表表示全部允许。
        """
        missing: list[str] = []
        for value in sorted(request.required_tools):
            if not declared.allows("tools", value):
                missing.append(f"tools:{value}")
        for value in sorted(request.required_resource_types):
            if not declared.allows("resource_types", value):
                missing.append(f"resource_types:{value}")
        for value in sorted(request.required_artifact_formats):
            if not declared.allows("artifact_formats", value):
                missing.append(f"artifact_formats:{value}")
        return missing

    async def authorize(self, auth: AuthContext, request: AuthzRequest) -> CapabilitySet:
        """执行四层判定并返回本次会话的有效能力集。

        Args:
            auth: 鉴权上下文（用户/租户由令牌解析，不接受客户端覆盖）。
            request: 判定输入（不含请求正文）。

        Returns:
            CapabilitySet: 有效能力集：声明存在时为声明本身（已证明是上界子集）；
                未声明时为调用者上界（继承）。

        Raises:
            ApiError: 任一层拒绝。租户/权限/资源层 → `20003`；
                能力层 → `14009`（声明越界或操作超出声明）。
        """
        points = await self.permission_points(auth)
        upper = self.upper_capabilities(points)
        self._check_layer_order()

        await self._check_tenant(auth, request, points)
        await self._check_permission(auth, request, points)
        effective = await self._check_capability(auth, request, upper)
        await self._check_resource(auth, request)
        return effective

    @staticmethod
    def _check_layer_order() -> None:
        """防御性自检：判定顺序必须与公开契约一致。

        Raises:
            RuntimeError: 代码中的顺序常量被改坏（属编程错误，启动即暴露）。
        """
        if AUTHZ_LAYER_ORDER != ("tenant", "permission", "capability", "resource"):
            raise RuntimeError("四层授权顺序被破坏")

    # ------------------------------------------------------------------
    # 第 1 层：租户
    # ------------------------------------------------------------------
    async def _check_tenant(
        self, auth: AuthContext, request: AuthzRequest, points: frozenset[str]
    ) -> None:
        """租户层：生效租户必须等于令牌租户；平台租户额外要求运营权限点。

        Args:
            auth: 鉴权上下文。
            request: 判定输入。
            points: 权限点集合。

        Raises:
            ApiError: 跨租户或平台视角缺失（20003）。
        """
        effective_tenant = request.tenant_id or auth.tenant_id
        if effective_tenant != auth.tenant_id:
            await self._deny(
                auth,
                AuthzLayer.TENANT,
                request,
                reason="tenant_mismatch",
                requested=effective_tenant,
            )
        if effective_tenant == PLATFORM_TENANT and OPS_CONSOLE_POINT not in points:
            await self._deny(
                auth,
                AuthzLayer.TENANT,
                request,
                reason="platform_requires_ops_point",
                requested=PLATFORM_TENANT,
            )

    # ------------------------------------------------------------------
    # 第 2 层：角色/权限点
    # ------------------------------------------------------------------
    async def _check_permission(
        self, auth: AuthContext, request: AuthzRequest, points: frozenset[str]
    ) -> None:
        """权限点层：要求的权限点必须在调用者权限点集合内。

        Args:
            auth: 鉴权上下文。
            request: 判定输入（取 `permission` 与操作名）。
            points: 权限点集合。

        Raises:
            ApiError: 缺少权限点（20003）。
        """
        permission = request.permission
        if not permission:
            return
        if permission not in points:
            await self._deny(
                auth,
                AuthzLayer.PERMISSION,
                request,
                reason="missing_permission_point",
                requested=permission,
            )

    # ------------------------------------------------------------------
    # 第 3 层：会话能力
    # ------------------------------------------------------------------
    async def _check_capability(
        self, auth: AuthContext, request: AuthzRequest, upper: CapabilitySet
    ) -> CapabilitySet:
        """能力层：声明必须是上界子集，且本次操作所需项必须被声明允许。

        Args:
            auth: 鉴权上下文。
            request: 判定输入。
            upper: 权限点上界。

        Returns:
            CapabilitySet: 有效能力集。

        Raises:
            ApiError: 声明越界或操作超出声明（14009）。
        """
        declared = request.declared
        if declared is None:
            # 未声明 = 继承调用者权限（旧路径行为不变），但登记待收敛。
            logger.info(
                "authz_capability_inherited",
                operation=request.operation or "unspecified",
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
            )
            effective = upper
        else:
            violations = declared.violations(upper)
            if violations:
                await self._deny(
                    auth,
                    AuthzLayer.CAPABILITY,
                    request,
                    reason="declaration_exceeds_upper",
                    requested="; ".join(f"{v.dimension}:{v.value}" for v in violations),
                    extra={"violations": [v.as_dict() for v in violations]},
                )
            effective = declared

        missing = self._missing_requirements(effective, request)
        if missing:
            await self._deny(
                auth,
                AuthzLayer.CAPABILITY,
                request,
                reason="operation_exceeds_capability",
                requested="; ".join(missing),
                extra={"missing": missing},
            )
        return effective

    # ------------------------------------------------------------------
    # 第 4 层：资源
    # ------------------------------------------------------------------
    async def _check_resource(self, auth: AuthContext, request: AuthzRequest) -> None:
        """资源层：对象租户、归属人、状态必须满足要求。

        Args:
            auth: 鉴权上下文。
            request: 判定输入。

        Raises:
            ApiError: 对象不属于当前租户 / 非归属人 / 状态不符（20003）。
        """
        resource = request.resource
        if resource is None:
            return
        effective_tenant = request.tenant_id or auth.tenant_id
        if resource.resource_type not in KNOWN_RESOURCE_TYPES:
            await self._deny(
                auth,
                AuthzLayer.RESOURCE,
                request,
                reason="unknown_resource_type",
                requested=resource.resource_type,
                resource=resource,
            )
        if resource.tenant_id and resource.tenant_id != effective_tenant:
            await self._deny(
                auth,
                AuthzLayer.RESOURCE,
                request,
                reason="resource_tenant_mismatch",
                requested=resource.resource_type,
                resource=resource,
            )
        if resource.owner_only and resource.owner_id != auth.user_id:
            await self._deny(
                auth,
                AuthzLayer.RESOURCE,
                request,
                reason="resource_owner_mismatch",
                requested=resource.resource_type,
                resource=resource,
            )
        if resource.required_state and resource.state != resource.required_state:
            await self._deny(
                auth,
                AuthzLayer.RESOURCE,
                request,
                reason="resource_state_mismatch",
                requested=resource.resource_type,
                resource=resource,
            )

    # ------------------------------------------------------------------
    # 拒绝与留痕
    # ------------------------------------------------------------------
    async def _deny(
        self,
        auth: AuthContext,
        layer: AuthzLayer,
        request: AuthzRequest,
        *,
        reason: str,
        requested: str,
        resource: ResourceRef | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """记录拒绝并抛出对应业务码（不返回）。

        Args:
            auth: 鉴权上下文。
            layer: 拒绝发生的层。
            request: 判定输入。
            reason: 内部拒绝原因（只进审计/日志，不进用户文案）。
            requested: 被拒绝的请求项（能力名/资源类型/权限点），不含业务正文。
            resource: 资源引用（资源层拒绝时提供）。
            extra: 额外审计字段（仅结构化的能力项，不含敏感参数）。

        Raises:
            ApiError: 能力层 → `14009`；其余层 → `20003`。
        """
        await self._record_denied(
            auth,
            layer,
            request,
            reason=reason,
            requested=requested,
            resource=resource,
            extra=extra,
        )
        if layer is AuthzLayer.CAPABILITY:
            raise ApiError(
                ErrorCode.CAPABILITY_SCOPE_EXCEEDED,
                internal_detail=f"layer=capability reason={reason} requested={requested}",
            )
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=f"layer={layer.value} reason={reason} requested={requested}",
        )

    async def _record_denied(
        self,
        auth: AuthContext,
        layer: AuthzLayer,
        request: AuthzRequest,
        *,
        reason: str,
        requested: str,
        resource: ResourceRef | None,
        extra: dict[str, Any] | None,
    ) -> None:
        """写拒绝留痕（失败不阻断拒绝）。

        **日志/审计禁记**：请求正文、令牌、工具参数、文件内容。这里只写
        「谁 / 哪一层 / 请求什么能力项 / 什么操作」。

        Args:
            auth: 鉴权上下文。
            layer: 拒绝层。
            request: 判定输入。
            reason: 内部原因。
            requested: 请求项。
            resource: 资源引用。
            extra: 额外结构化字段。
        """
        change_summary: dict[str, Any] = {
            "layer": layer.value,
            "operation": request.operation or "unspecified",
            "permission": request.permission,
            "requested": requested,
            "reason": reason,
        }
        if resource is not None:
            change_summary.update(resource.as_audit_fields())
        if extra:
            change_summary.update(extra)
        try:
            service: Any = self._audit
            if service is None:
                from app.business.audit_service import get_audit_service

                service = get_audit_service()
            if service is None:
                return
            await service.record(
                tenant_id=auth.tenant_id or "",
                actor_id=auth.user_id or "",
                action=ACTION_PERMISSION_DENIED,
                resource_type=(
                    resource.resource_type
                    if layer is AuthzLayer.RESOURCE and resource is not None
                    else f"authz_{layer.value}"
                ),
                resource_id=resource.resource_id if resource is not None else requested,
                result="failure",
                change_summary=change_summary,
            )
        except Exception as exc:  # 审计失败不改变「已拒绝」这个事实
            logger.warning("authz_audit_failed", layer=layer.value, detail=type(exc).__name__)


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_authorization_singleton: AuthorizationService | None = None


def get_authorization_service(
    *, policy: Any | None = None, audit: Any | None = None
) -> AuthorizationService:
    """获取授权服务单例。

    Args:
        policy: 可选权限服务（仅首次构造时使用）。
        audit: 可选审计服务（仅首次构造时使用）。

    Returns:
        AuthorizationService: 进程内唯一实例。
    """
    global _authorization_singleton
    if _authorization_singleton is None:
        _authorization_singleton = AuthorizationService(policy=policy, audit=audit)
    return _authorization_singleton


def reset_authorization_service() -> None:
    """重置单例（测试隔离用）。"""
    global _authorization_singleton
    _authorization_singleton = None


__all__: list[str] = [
    "INHERITED_CAPABILITY_PATHS",
    "AuthorizationService",
    "get_authorization_service",
    "reset_authorization_service",
    "upper_capabilities",
]
