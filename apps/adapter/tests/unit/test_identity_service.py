"""用户权限服务单元测试（架构文档 3.5 / 3.5.4）。"""

from __future__ import annotations

import pytest

from app.clients.keycloak_client import KeycloakClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.common import ResourceRef
from app.schemas.identity import (
    AuditLogQueryRequest,
    ListMembersQuery,
    OrgRole,
    UpsertRoleRequest,
    VerifyRequest,
)
from app.services.identity_service import IdentityService, map_role

pytestmark = pytest.mark.unit

_AUTH = AuthContext(user_id="usr_1", tenant_id="tnt_1", roles=["member"], email="u@example.com")


def _service() -> IdentityService:
    """构造桩模式身份服务。

    Returns:
        IdentityService: 服务实例。
    """
    return IdentityService(
        settings=Settings(downstream_enabled=False),
        keycloak=KeycloakClient("http://example.invalid", "officeai"),
    )


def test_map_role_falls_back_to_member() -> None:
    """未识别角色按 member 处理，不透出底座原始角色名。"""
    assert map_role("realm-admin") == OrgRole.MEMBER
    assert map_role("owner") == OrgRole.OWNER
    assert map_role("org_admin") == OrgRole.ADMIN
    assert map_role("admin") == OrgRole.ADMIN


def test_verify_allows_granted_permission() -> None:
    """角色命中权限点时允许。"""
    auth = AuthContext(user_id="usr_1", tenant_id="tnt_1", roles=["knowledge:document:upload"])
    data = _service().verify(VerifyRequest(permission="knowledge:document:upload"), auth)
    assert data.allowed is True
    assert data.tenant_id == "tnt_1"


def test_verify_denies_missing_permission() -> None:
    """角色不命中权限点时拒绝但仍返回结论（不抛异常）。"""
    data = _service().verify(VerifyRequest(permission="knowledge:document:upload"), _AUTH)
    assert data.allowed is False
    assert data.user_id == "usr_1"


@pytest.mark.parametrize("bad", ["knowledge", "knowledge:", ":upload", "a:b:c:d", "  :  "])
def test_verify_rejects_malformed_permission_point(bad: str) -> None:
    """权限点命名不规范视为未注册，返回 14004。"""
    with pytest.raises(ApiError) as exc_info:
        _service().verify(VerifyRequest(permission=bad), _AUTH)
    assert exc_info.value.code == ErrorCode.PERMISSION_NOT_DEFINED


def test_verify_accepts_resource_context() -> None:
    """校验请求可携带资源上下文。"""
    request = VerifyRequest(
        permission="knowledge:document:upload",
        resource=ResourceRef(type="knowledge_base", id="kb_1"),
    )
    data = _service().verify(request, _AUTH)
    assert isinstance(data.allowed, bool)


async def test_get_profile_stub() -> None:
    """桩模式资料查询返回自研字段，不含底座原生字段。"""
    data = await _service().get_profile(_AUTH)
    assert data.user_id == "usr_1"
    assert data.tenant.tenant_id == "tnt_1"
    serialized = data.model_dump_json()
    for leaked in ("firstName", "lastName", "realm_access", "attributes"):
        assert leaked not in serialized


async def test_list_members_requires_org_id() -> None:
    """空组织 ID 返回 14002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().list_members("", ListMembersQuery(), _AUTH)
    assert exc_info.value.code == ErrorCode.ORGANIZATION_NOT_FOUND


async def test_list_members_stub_returns_page() -> None:
    """桩模式成员列表返回空分页。"""
    result = await _service().list_members("org_1", ListMembersQuery(), _AUTH)
    assert result.items == []
    assert result.meta.total == 0


async def test_invite_member_requires_org_id() -> None:
    """空组织 ID 邀请返回 14002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().invite_member("", "a@example.com", OrgRole.MEMBER, "", _AUTH)
    assert exc_info.value.code == ErrorCode.ORGANIZATION_NOT_FOUND


async def test_invite_member_stub() -> None:
    """桩模式邀请返回 pending 记录。"""
    data = await _service().invite_member("org_1", "a@example.com", OrgRole.MEMBER, "欢迎", _AUTH)
    assert data.status == "pending"
    assert data.role == OrgRole.MEMBER
    assert data.email == "a@example.com"


async def test_upsert_role_requires_org_id() -> None:
    """空组织 ID 角色操作返回 14002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().upsert_role("", UpsertRoleRequest(role_key="dev"), _AUTH)
    assert exc_info.value.code == ErrorCode.ORGANIZATION_NOT_FOUND


async def test_upsert_role_validates_permissions() -> None:
    """角色权限点命名不规范返回 14004。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().upsert_role(
            "org_1", UpsertRoleRequest(role_key="dev", permissions=["bad"]), _AUTH
        )
    assert exc_info.value.code == ErrorCode.PERMISSION_NOT_DEFINED


async def test_upsert_role_stub() -> None:
    """桩模式角色操作返回自研角色信息。"""
    data = await _service().upsert_role(
        "org_1",
        UpsertRoleRequest(
            role_key="dev",
            display_name="研发",
            permissions=["knowledge:document:upload"],
            members=["usr_2"],
        ),
        _AUTH,
    )
    assert data.role_key == "dev"
    assert data.member_count == 1
    assert data.permissions == ["knowledge:document:upload"]


def test_query_audit_logs_returns_page() -> None:
    """审计查询返回空分页且元信息正确。"""
    request = AuditLogQueryRequest(page=2, page_size=10, resource_type="knowledge_base")
    result = _service().query_audit_logs(request, _AUTH)
    assert result.meta.page == 2
    assert result.meta.page_size == 10
    assert result.items == []


def test_transform_profile_maps_native_fields() -> None:
    """底座原生用户字段转换为自研字段。"""
    data = _service()._transform_profile(  # noqa: SLF001 - 验证转换函数
        _AUTH,
        {
            "id": "usr_9",
            "username": "wangqian",
            "firstName": "王",
            "lastName": "倩",
            "email": "wangqian@example.com",
            "attributes": {"tenant_name": ["桐鸣科技"], "plan": ["professional"]},
        },
    )
    assert data.user_id == "usr_9"
    assert data.display_name == "王倩"
    assert data.email == "wangqian@example.com"
    assert data.tenant.name == "桐鸣科技"
    assert data.tenant.plan == "professional"


def test_transform_profile_rejects_missing_id() -> None:
    """底座返回空用户 ID 时返回 14001。"""
    with pytest.raises(ApiError) as exc_info:
        _service()._transform_profile(_AUTH, {"username": "x"})  # noqa: SLF001
    assert exc_info.value.code == ErrorCode.USER_NOT_FOUND


def test_transform_member_maps_attributes() -> None:
    """底座成员对象转换为自研成员条目。"""
    member = _service()._transform_member(  # noqa: SLF001 - 验证转换函数
        {
            "id": "usr_5",
            "firstName": "李",
            "lastName": "明",
            "email": "liming@example.com",
            "attributes": {"org_role": ["org_admin"]},
        }
    )
    assert member.user_id == "usr_5"
    assert member.display_name == "李明"
    assert member.role == OrgRole.ADMIN


def test_build_organizations_maps_groups() -> None:
    """底座 group 列表转换为自研组织列表。"""
    organizations = _service().build_organizations(
        [{"id": "org_1", "name": "研发部", "attributes": {"org_role": ["owner"]}}]
    )
    assert organizations[0].org_id == "org_1"
    assert organizations[0].role == OrgRole.OWNER
