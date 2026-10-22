"""安全缺陷回归测试（QA Sprint 0 报告 BUG-01 ~ BUG-05）。

**背景**：QA 独立验证发现 5 个缺陷，其中 BUG-01 / BUG-02 为 P1 安全问题，
根因相同——`create_app(settings=...)` 注入的配置在鉴权/租户链路被忽略，
导致 `auth_mode=oidc` 被降级为 stub（伪造令牌可登录）、
`deploy_mode=private` 的单租户隔离失效。

原测试套件的盲区：所有 `test_security.py` 用例都**直接调用单元函数并显式传 settings**，
不走 FastAPI 依赖链，真实验签路径从未被自动化覆盖
（`grep -rn 'auth_mode="oidc"' tests/` 命中数为 0）。

本文件**只走 FastAPI 依赖链**（`TestClient` 走完整请求路径），
覆盖 QA 报告要求的五个 oidc 场景 + 其余四项缺陷。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import Settings
from app.core.errors import ErrorCode

# P0 红线用例（G-24，测试方案 §7.1）：SEC-01~07 / P0 判据① —— 红线用例：OIDC/租户锁定/限流/幂等 body
# 映射与实跑见 docs/qa-phase2-w2-r2-report.md §3.1
pytestmark = [pytest.mark.integration, pytest.mark.p0]

#: 测试用 OIDC 密钥
OIDC_SECRET = "oidc-mode-correct-secret"

#: 错误密钥（用于伪造令牌）
WRONG_SECRET = "attacker-controlled-wrong-secret"


def _issue_token(
    *,
    secret: str = OIDC_SECRET,
    audience: str = "officeai-adapter",
    issuer: str = "officeai",
    expires_in: int = 3600,
    tenant_id: str = "default",
    user_id: str = "usr_oidc_user",
) -> str:
    """签发测试令牌。

    Args:
        secret: 签名密钥。
        audience: 受众声明。
        issuer: 签发者声明。
        expires_in: 有效期（秒），负数表示已过期。
        tenant_id: 租户声明。
        user_id: 用户 ID 声明。

    Returns:
        str: JWT 令牌。
    """
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "roles": ["*"],
            "aud": audience,
            "iss": issuer,
            "iat": now,
            "exp": now + expires_in,
        },
        secret,
        algorithm="HS256",
    )


def _oidc_app(**overrides: object) -> object:
    """构造 oidc 模式应用（覆盖默认配置）。

    Args:
        **overrides: 覆盖的配置项。

    Returns:
        object: FastAPI 应用实例。
    """
    from app.main import create_app

    defaults: dict[str, object] = {
        "env": "test",
        "deploy_mode": "private",
        "auth_mode": "oidc",
        "jwt_secret": OIDC_SECRET,
        "ratelimit_enabled": False,
    }
    defaults.update(overrides)
    return create_app(Settings(**defaults))  # type: ignore[arg-type]


def _headers(token: str, tenant: str = "default") -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        token: 令牌。
        tenant: 租户标识。

    Returns:
        dict[str, str]: 请求头。
    """
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant,
        "Idempotency-Key": "oidc-0000-4000-8000-000000000001",
    }


# ===========================================================================
# BUG-01：oidc 模式下伪造令牌必须被拒绝
# ===========================================================================


def test_oidc_mode_rejects_token_signed_with_wrong_secret() -> None:
    """【BUG-01】oidc 模式下用错误密钥签名的令牌必须返回 401 / 20002。

    修复前：`create_app(auth_mode="oidc")` 因读取全局配置单例而被降级为 stub，
    伪造令牌被完全接受（HTTP 200 code=0）。
    """
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token(secret=WRONG_SECRET)}"},
        )
    assert response.status_code == 401, "伪造令牌必须被拒绝"
    body = response.json()
    assert body["code"] == int(ErrorCode.UNAUTHORIZED)
    assert body["message"] == "登录状态已失效，请重新登录"
    assert body["data"] is None


def test_oidc_mode_accepts_valid_token() -> None:
    """【BUG-01 正向】oidc 模式下正确签名的令牌必须通过。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token()}"},
        )
    assert response.status_code == 200
    assert response.json()["code"] == 0


def test_oidc_mode_rejects_expired_token() -> None:
    """【BUG-01 场景 3】过期令牌必须返回 401 / 20002。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token(expires_in=-60)}"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_oidc_mode_rejects_wrong_audience() -> None:
    """【BUG-01 场景 4】受众（audience）不匹配的令牌必须被拒绝。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token(audience='other-service')}"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_oidc_mode_rejects_wrong_issuer() -> None:
    """【BUG-01 场景 5】签发者（issuer）不匹配的令牌必须被拒绝。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token(issuer='evil-issuer')}"},
        )
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_oidc_mode_protects_write_endpoints_too() -> None:
    """【BUG-01 覆盖】写接口同样必须验签，不能只保护读接口。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/model/generate",
            json={
                "conversation_id": "conv_oidc_test",
                "profile": "general",
                "messages": [{"role": "user", "content": "你好"}],
            },
            headers={
                "Authorization": f"Bearer {_issue_token(secret=WRONG_SECRET)}",
                "Idempotency-Key": "oidc-0000-4000-8000-000000000002",
            },
        )
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_stub_mode_still_accepts_unsigned_token() -> None:
    """开发态 stub 模式行为不变（不验签），确保修复未破坏开发体验。"""
    from app.main import create_app

    app = create_app(
        Settings(env="test", deploy_mode="private", auth_mode="stub", ratelimit_enabled=False)
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {_issue_token(secret=WRONG_SECRET)}"},
        )
    assert response.status_code == 200


# ===========================================================================
# BUG-02：私有化模式必须强制单租户隔离
# ===========================================================================


def test_private_mode_forces_default_tenant() -> None:
    """【BUG-02】私有化模式下令牌声明的租户必须被忽略，恒为 `default`。

    修复前：租户解析读取全局配置单例，`deploy_mode=private` 判定失效，
    攻击者可携带 `tenant_id=attacker_tenant` 跨租户访问。
    """
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/identity/profile",
            headers=_headers(_issue_token(tenant_id="attacker_tenant"), tenant="attacker_tenant"),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["tenant"]["tenant_id"] == "default", "私有化模式必须强制 default 租户"
    assert body["data"]["tenant"]["tenant_id"] != "attacker_tenant"


def test_private_mode_ignores_tenant_header() -> None:
    """【BUG-02】私有化模式下 `X-Tenant-Id` 请求头也必须被忽略。"""
    app = _oidc_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/identity/profile",
            headers=_headers(_issue_token(tenant_id="default"), tenant="attacker_tenant"),
        )
    assert response.status_code == 200
    assert response.json()["data"]["tenant"]["tenant_id"] == "default"


def test_saas_mode_honors_tenant_header() -> None:
    """【BUG-02 正向】SaaS 模式仍必须支持多租户隔离（租户头生效）。"""
    app = _oidc_app(deploy_mode="saas")
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/identity/profile",
            headers=_headers(_issue_token(tenant_id="tnt_tenant_a"), tenant="tnt_tenant_a"),
        )
    assert response.status_code == 200
    assert response.json()["data"]["tenant"]["tenant_id"] == "tnt_tenant_a"


# ===========================================================================
# BUG-03：限流必须真正接入请求链路
# ===========================================================================


def test_ratelimit_blocks_requests_over_threshold() -> None:
    """【BUG-03】超过阈值后必须返回 429 / 20006。

    修复前：限流器完全未接入请求链路，阈值设 3、连发 6 次全部 HTTP 200 code=0，
    20006 在生产路径上不可达。
    """
    app = _oidc_app(ratelimit_enabled=True, ratelimit_default_per_minute=3)
    with TestClient(app) as client:
        token = _issue_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "default",
        }
        statuses: list[int] = []
        for _ in range(6):
            response = client.get("/api/v1/model/catalog", headers=headers)
            statuses.append(response.status_code)
            if response.status_code == 429:
                break
    assert 429 in statuses, f"超限请求必须被拦截，实际状态码序列: {statuses}"
    assert statuses.count(200) == 3, f"阈值内 3 次应放行，实际: {statuses}"


def test_ratelimit_returns_business_language_message() -> None:
    """【BUG-03】限流响应必须是业务语言文案，不得泄露技术细节。"""
    app = _oidc_app(ratelimit_enabled=True, ratelimit_default_per_minute=1)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_issue_token()}", "X-Tenant-Id": "default"}
        client.get("/api/v1/model/catalog", headers=headers)
        response = client.get("/api/v1/model/catalog", headers=headers)
    assert response.status_code == 429
    body = response.json()
    assert body["code"] == int(ErrorCode.RATE_LIMITED)
    assert body["message"] == "当前使用人数较多，请稍后再试"


def test_health_probes_are_not_ratelimited() -> None:
    """【BUG-03】健康探针不参与限流，避免被业务流量挤占导致误判。"""
    app = _oidc_app(ratelimit_enabled=True, ratelimit_default_per_minute=1)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/healthz").status_code == 200
            assert client.get("/readyz").status_code == 200


def test_ratelimit_isolates_tenants() -> None:
    """【BUG-03】限流按租户隔离，一个租户超限不影响其他租户。"""
    app = _oidc_app(deploy_mode="saas", ratelimit_enabled=True, ratelimit_default_per_minute=2)
    with TestClient(app) as client:
        tenant_a = {
            "Authorization": f"Bearer {_issue_token(tenant_id='tnt_a')}",
            "X-Tenant-Id": "tnt_a",
        }
        tenant_b = {
            "Authorization": f"Bearer {_issue_token(tenant_id='tnt_b')}",
            "X-Tenant-Id": "tnt_b",
        }
        for _ in range(3):
            client.get("/api/v1/model/catalog", headers=tenant_a)
        response = client.get("/api/v1/model/catalog", headers=tenant_b)
    assert response.status_code == 200, "其他租户不受影响"


# ===========================================================================
# BUG-04：上传超限须区分 20008 / 11003
# ===========================================================================


def test_upload_oversize_returns_20008() -> None:
    """【BUG-04】文件字节数超过传输层上限必须返回 20008 / 413。

    修复前：一律返回 11003 / 400，未体现「请求体超限」与「文档内容过多」的语义区分。
    """
    import io

    app = _oidc_app(max_upload_bytes=1024)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("big.pdf", io.BytesIO(b"x" * 4096), "application/pdf")},
            headers=_headers(_issue_token()),
        )
    assert response.status_code == 413
    body = response.json()
    assert body["code"] == int(ErrorCode.PAYLOAD_TOO_LARGE)
    assert body["message"] == "文件超出大小限制（上限 50MB）"


def test_upload_unsupported_format_still_returns_11002() -> None:
    """【BUG-04】格式不支持仍返回 11002，不受错误码调整影响。"""
    import io

    app = _oidc_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers=_headers(_issue_token()),
        )
    assert response.status_code == 400
    assert response.json()["code"] == int(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED)


def test_upload_uses_request_scoped_limit() -> None:
    """【BUG-04】上传上限取自 `app.state.settings`，不得回落到全局默认 50MB。"""
    import io

    app = _oidc_app(max_upload_bytes=1024)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("big.pdf", io.BytesIO(b"x" * 4096), "application/pdf")},
            headers=_headers(_issue_token()),
        )
    assert response.status_code == 413, "注入的 1KB 上限必须生效"


# ===========================================================================
# BUG-05：幂等指纹必须包含请求体
# ===========================================================================


def test_same_idempotency_key_with_different_body_returns_conflict() -> None:
    """【BUG-05】同一幂等键配同一路径但不同请求体必须返回 20005。

    修复前：指纹只含「方法 + 路径 + 租户 + 用户」，不含请求体，
    提交「知识库 B」会静默回放「知识库 A」的成功结果——**静默数据丢失**。
    """
    app = _oidc_app()
    with TestClient(app) as client:
        headers = _headers(_issue_token())
        first = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "知识库 A", "description": "第一个"},
            headers=headers,
        )
        second = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "知识库 B", "description": "第二个"},
            headers=headers,
        )
    assert first.status_code == 200
    assert first.json()["code"] == 0
    assert second.status_code == 409, "同键不同请求体必须识别为键复用冲突"
    assert second.json()["code"] == int(ErrorCode.CONFLICT)
    assert second.json()["message"] == "操作冲突，请刷新后重试"
    # 关键：不能返回首次的知识库 A
    assert second.json()["data"] is None


def test_same_idempotency_key_with_same_body_replays_first_result() -> None:
    """【BUG-05 正向】同键同体仍按幂等语义回放首次结果（20007）。"""
    app = _oidc_app()
    with TestClient(app) as client:
        headers = _headers(_issue_token())
        payload = {"name": "知识库 A", "description": "第一个"}
        first = client.post("/api/v1/knowledge/bases", json=payload, headers=headers)
        second = client.post("/api/v1/knowledge/bases", json=payload, headers=headers)
    assert first.json()["code"] == 0
    assert second.json()["code"] == int(ErrorCode.IDEMPOTENT_DUPLICATE)
    assert second.json()["data"]["base_id"] == first.json()["data"]["base_id"]


def test_different_idempotency_keys_with_different_bodies_both_succeed() -> None:
    """【BUG-05 正向】不同幂等键的正常请求不受影响。"""
    app = _oidc_app()
    with TestClient(app) as client:
        base_headers = {"Authorization": f"Bearer {_issue_token()}", "X-Tenant-Id": "default"}
        first = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "知识库 A"},
            headers={**base_headers, "Idempotency-Key": "key-aaaa-0000-4000-8000-000000000001"},
        )
        second = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "知识库 B"},
            headers={**base_headers, "Idempotency-Key": "key-bbbb-0000-4000-8000-000000000002"},
        )
    assert first.json()["code"] == 0
    assert second.json()["code"] == 0
    assert first.json()["data"]["base_id"] != second.json()["data"]["base_id"]
    assert first.json()["data"]["name"] == "知识库 A"
    assert second.json()["data"]["name"] == "知识库 B"


# ===========================================================================
# 回归：配置注入链路整体生效
# ===========================================================================


def test_injected_settings_are_used_throughout_request_chain() -> None:
    """综合回归：注入的配置必须在请求链路的每个环节生效。"""
    app = _oidc_app(
        env="test",
        deploy_mode="private",
        auth_mode="oidc",
        jwt_secret=OIDC_SECRET,
        ratelimit_enabled=False,
    )
    with TestClient(app) as client:
        health = client.get("/healthz").json()
        assert health["data"]["env"] == "test", "healthz 必须反映注入的 env"
        assert health["data"]["deploy_mode"] == "private", "healthz 必须反映注入的 deploy_mode"
        assert health["data"]["downstream_enabled"] is False

        profile = client.get("/api/v1/identity/profile", headers=_headers(_issue_token())).json()
        assert profile["data"]["tenant"]["tenant_id"] == "default"


def test_healthz_reflects_injected_settings() -> None:
    """`/healthz` 读取注入配置而非全局单例。"""
    from app.main import create_app

    app = create_app(
        Settings(
            env="staging",
            deploy_mode="saas",
            ratelimit_enabled=False,
            # S1：staging/production 必须 oidc + 非空签名密钥，否则启动即 30004。
            # 这条用例只验证「配置注入生效」，不再顺带验证桩模式。
            auth_mode="oidc",
            jwt_secret="staging-secret-for-healthz-injection-test",
        )
    )
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["data"]["env"] == "staging"
    assert body["data"]["deploy_mode"] == "saas"
