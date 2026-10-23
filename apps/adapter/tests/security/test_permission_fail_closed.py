"""权限 fail-closed 与白名单锁（阶段四 T4-04.5，架构 §2.5）。

F-1 的缺陷形态：`require_permission` 遇到"令牌里没有角色声明"时**静默放行**，
把判权推给业务层。结果是「不带 roles 的令牌 = 绕过全部端点级权限」——
攻击者只要拿到任意一个能签出合法 token 的通道，就能构造"无角色"令牌打所有端点。

三条判据：

1. **无角色 → 3 个写端点全部 403**，且每次拒绝都留一条操作记录；
2. **白名单恰好 2 条**（W-1/W-2），且**按方法收窄**（GET 之外的写方法不放行）；
3. **白名单锁文件未扩张**：`docs/security/permission-whitelist.lock` 与代码集合逐字一致，
   任何"顺手加一条"都会让本文件变红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from tests.security.conftest import TENANT_A, USER_A, issue_token, write_headers

from app.business.audit_service import ACTION_PERMISSION_DENIED, get_audit_recorder
from app.core.config import Settings, get_settings
from app.core.security import PERMISSION_WHITELIST

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 锁文件路径（仓库根 → docs/security）
LOCK_PATH = Path(__file__).resolve().parents[4] / "docs" / "security" / "permission-whitelist.lock"

#: 三个写端点（都要求端点级权限点；权限校验是端点体的第一句）
WRITE_ENDPOINTS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    # 三个**写**端点，权限校验都是端点体的第一句（所以 403 一定先于业务逻辑）：
    # 改知识库 / 删知识库 / 撤销授权。选它们而不是插件安装/成员新增，
    # 是因为那两者的请求体需要一整套前置数据（插件已安装、组织已有成员），
    # 校验会先撞 422/404，测不到鉴权分支——判据本身（写操作 fail-closed）不受影响。
    ("PATCH", "/api/v1/knowledge/bases/kb_sec_placeholder", {"name": "改个名"}),
    ("DELETE", "/api/v1/knowledge/bases/kb_sec_placeholder", {}),
    ("DELETE", "/api/v1/knowledge/bases/kb_sec_placeholder/grants/grant_sec_placeholder", {}),
)


def _no_role_headers(tag: str) -> dict[str, str]:
    """构造**不带角色声明**的合法令牌请求头。

    Args:
        tag: 幂等键标签（每个端点各不相同）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = issue_token(tenant_id=TENANT_A, user_id=USER_A, roles=None)
    digest = re.sub(r"\W", "", tag)[:8].ljust(8, "0")
    key = f"{digest}-1111-4222-8333-444455556666"
    return write_headers(token, tenant=TENANT_A, key=key)


class TestFailClosed:
    """无角色令牌访问写端点 → 403 + 审计一条。"""

    @pytest.mark.parametrize(("method", "path", "body"), WRITE_ENDPOINTS)
    def test_write_endpoint_denied(
        self,
        client: Any,
        oidc_settings: Settings,
        method: str,
        path: str,
        body: dict[str, Any],
    ) -> None:
        """3 个写端点都必须 403（20003），而不是"交给业务层"。"""
        headers = _no_role_headers(path)

        response = client.request(method, path, json=body, headers=headers)

        assert response.status_code == 403, f"{method} {path} 未按 fail-closed 拒绝"
        assert response.json()["code"] == 20003
        assert response.json()["data"] is None

    def test_denials_are_audited(self, client: Any, oidc_settings: Settings) -> None:
        """每次拒绝都留一条 `security.permission_denied`（只记动作，不记令牌）。"""
        recorder = get_audit_recorder(get_settings())
        for method, path, body in WRITE_ENDPOINTS:
            client.request(method, path, json=body, headers=_no_role_headers(f"{path}-audit"))

        entries = [
            entry
            for entry in getattr(recorder, "_entries", [])
            if entry.action == ACTION_PERMISSION_DENIED
        ]

        assert len(entries) >= len(WRITE_ENDPOINTS)
        for entry in entries:
            assert entry.result == "failure"
            assert "token" not in json.dumps(entry.change_summary, ensure_ascii=False).lower()


class TestWhitelistLock:
    """白名单：恰好 2 条、按方法收窄、锁文件未扩张。"""

    def test_exactly_two_entries(self) -> None:
        """架构 §2.5.2：白名单恰好 2 条（≤5 是上限，不是目标）。"""
        assert len(PERMISSION_WHITELIST) == 2
        assert set(PERMISSION_WHITELIST) == {
            ("knowledge:base:read", "GET"),
            ("tool:connection:manage", "GET"),
        }

    def test_every_entry_has_a_reason(self) -> None:
        """每条都必须写清"为什么必须委派"（没有理由的白名单等于后门）。"""
        for key, reason in PERMISSION_WHITELIST.items():
            assert len(reason) >= 30, f"{key} 的理由太短，无法判断是否必要"

    def test_only_get_is_whitelisted(self) -> None:
        """白名单只放行读方法：写方法一律走角色矩阵（否则等于顺手放开写）。"""
        assert all(method == "GET" for _permission, method in PERMISSION_WHITELIST)

    def test_lock_file_matches_code(self) -> None:
        """锁文件与代码集合逐字一致（新增/删除任何一条都必须同时改锁文件）。"""
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        locked = {
            (entry["permission"], method)
            for entry in lock["entries"]
            for method in entry["methods"]
        }

        assert locked == set(PERMISSION_WHITELIST), (
            "白名单与锁文件不一致：改白名单必须同时更新 "
            "docs/security/permission-whitelist.lock 并在评审中说明理由"
        )
        assert lock["version"] == 1

    def test_deferred_logging_only_whitelisted(self) -> None:
        """`permission_check_deferred` 只允许在 `core/security.py` 出现一次（白名单分支）。"""
        source = (Path(__file__).resolve().parents[2] / "app" / "core" / "security.py").read_text(
            encoding="utf-8"
        )
        app_root = Path(__file__).resolve().parents[2] / "app"

        # 只数"被当成事件名用"的那一处（注释里提到它不算）
        security_hits = source.count('"permission_check_deferred"')
        assert security_hits == 1, "白名单分支之外不得再出现延迟判权日志"

        others = [
            path
            for path in app_root.rglob("*.py")
            if path.name != "security.py"
            and '"permission_check_deferred"' in path.read_text(encoding="utf-8")
        ]
        assert others == [], f"其他模块不得记录延迟判权：{others}"
