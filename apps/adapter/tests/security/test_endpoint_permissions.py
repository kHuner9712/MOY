"""端点级权限点安全断言（审计 2026-10-31 L3）。

`test_permission_fail_closed.py` 已覆盖「无角色令牌→403」的核心红线。
本文件补充**正向**断言：正确角色的令牌能通过权限检查，
以及**常见写端点的权限点逐一验证**（防止权限点串用或遗漏）。

三条判据：
1. 带正确角色的令牌可以通过对应端点的权限检查；
2. 带错误角色的令牌在写端点被 403 + 20003 拒绝；
3. 权限拒绝响应不泄露令牌明文。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.security.conftest import TENANT_A, USER_A, issue_token, write_headers

from app.core.config import Settings
from app.core.security import AuthContext, require_permission

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 常见写端点的权限点映射（方法, 路径, 所需权限点）
WRITE_PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("PATCH", "/api/v1/knowledge/bases/kb_x", "knowledge:base:update"),
    ("DELETE", "/api/v1/knowledge/bases/kb_x", "knowledge:base:delete"),
    ("POST", "/api/v1/knowledge/bases/kb_x/grants", "knowledge:grant:manage"),
    ("DELETE", "/api/v1/knowledge/bases/kb_x/grants/g_x", "knowledge:grant:manage"),
)


class TestRequirePermissionDirect:
    """直接调用 `require_permission` 的权限判定行为。"""

    def test_wildcard_role_passes_any_permission(self) -> None:
        auth = AuthContext(user_id=USER_A, tenant_id=TENANT_A, roles=["*"])
        require_permission(auth, "knowledge:base:delete", method="DELETE")  # 不抛错

    def test_direct_permission_passes(self) -> None:
        auth = AuthContext(
            user_id=USER_A,
            tenant_id=TENANT_A,
            roles=["knowledge:base:delete"],
        )
        require_permission(auth, "knowledge:base:delete", method="DELETE")  # 不抛错

    def test_no_roles_denies_non_whitelisted(self) -> None:
        from app.core.errors import ApiError, ErrorCode

        auth = AuthContext(user_id=USER_A, tenant_id=TENANT_A, roles=[])
        with pytest.raises(ApiError) as exc:
            require_permission(auth, "knowledge:base:delete", method="DELETE")
        assert exc.value.code == ErrorCode.FORBIDDEN

    def test_empty_permission_raises(self) -> None:
        """空权限点是**编码错误**，必须炸，不得静默放行（P1-11）。"""
        from app.core.errors import ApiError, ErrorCode

        auth = AuthContext(user_id=USER_A, tenant_id=TENANT_A, roles=[])
        with pytest.raises(ApiError) as exc:
            require_permission(auth, "", method="GET")
        assert exc.value.code == ErrorCode.CONFIG_ERROR

    def test_whitespace_permission_raises_config_error(self) -> None:
        """纯空白权限点与空串同类（N2）：归因必须是 30004，不是 20003。

        `" "` 若落到 FORBIDDEN 会把"我们漏传了权限点"报成"用户没权限"，
        排障方向直接被带偏。
        """
        from app.core.errors import ApiError, ErrorCode

        auth = AuthContext(user_id=USER_A, tenant_id=TENANT_A, roles=[])
        with pytest.raises(ApiError) as exc:
            require_permission(auth, "  \t ", method="GET")
        assert exc.value.code == ErrorCode.CONFIG_ERROR

    def test_wrong_permission_denied(self) -> None:
        from app.core.errors import ApiError, ErrorCode

        auth = AuthContext(
            user_id=USER_A,
            tenant_id=TENANT_A,
            roles=["knowledge:base:read"],
        )
        with pytest.raises(ApiError) as exc:
            require_permission(auth, "knowledge:base:delete", method="DELETE")
        assert exc.value.code == ErrorCode.FORBIDDEN


class TestEndpointPermissionMatrix:
    """写端点的权限点逐一验证（方法 + 权限点绑定）。"""

    @pytest.mark.parametrize(
        ("method", "path", "permission"),
        WRITE_PERMISSIONS,
        ids=[f"{m}_{p.split('/')[-2] if '/' in p else p}" for m, p, _ in WRITE_PERMISSIONS],
    )
    def test_correct_permission_passes(self, method: str, path: str, permission: str) -> None:
        auth = AuthContext(
            user_id=USER_A,
            tenant_id=TENANT_A,
            roles=[permission],
        )
        require_permission(auth, permission, method=method)  # 不抛错

    @pytest.mark.parametrize(
        ("method", "path", "permission"),
        WRITE_PERMISSIONS,
        ids=[f"{m}_{p.split('/')[-2] if '/' in p else p}" for m, p, _ in WRITE_PERMISSIONS],
    )
    def test_read_only_role_denied_on_write(self, method: str, path: str, permission: str) -> None:
        from app.core.errors import ApiError, ErrorCode

        auth = AuthContext(
            user_id=USER_A,
            tenant_id=TENANT_A,
            roles=["knowledge:base:read"],
        )
        with pytest.raises(ApiError) as exc:
            require_permission(auth, permission, method=method)
        assert exc.value.code == ErrorCode.FORBIDDEN


class TestPermissionAuditTrail:
    """权限拒绝必须产生审计记录。"""

    def test_denied_produces_audit_record(
        self, oidc_client: TestClient, oidc_settings: Settings
    ) -> None:
        token = issue_token(tenant_id=TENANT_A, user_id=USER_A, roles=["knowledge:base:read"])
        headers = write_headers(
            token, tenant=TENANT_A, key="audit-test-0001-0000-0000-000000000000"
        )
        # 只读角色尝试删除 → 403
        response = oidc_client.delete("/api/v1/knowledge/bases/kb_perm_audit", headers=headers)
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == 20003

    def test_denied_does_not_leak_token(
        self, oidc_client: TestClient, oidc_settings: Settings
    ) -> None:
        token = issue_token(tenant_id=TENANT_A, user_id=USER_A, roles=[])
        headers = write_headers(
            token, tenant=TENANT_A, key="audit-test-0002-0000-0000-000000000000"
        )
        response = oidc_client.delete("/api/v1/knowledge/bases/kb_perm_leak", headers=headers)
        assert response.status_code == 403
        # 响应体不含 token 明文
        assert token not in response.text
