"""K6（R8）:RS256 + JWKS 验签回归网。

**这项为什么是阻断级**（`docs/design-2026-11-02-keycloak-realm.md` §2.1）：

适配层此前只支持 `HS256` + 共享密钥（`jwt.decode(token, jwt_secret, ...)`），而
Keycloak 默认签 **RS256**。症状是「登录看起来成功了，但每个业务接口 401」——
与「Cookie 没带上」的表现完全一样，排障时会被引向错误方向。

本文件锁死五件事：

1. **JWKS 走内网**（`keycloak_base_url`），`iss` 校验用公网 `jwt_issuer`：两个基准不合并。
   私有化现场内网 DNS 通常没有公网域名记录，走公网必挂，且是每请求热路径。
2. 外部 IdP（issuer 是绝对 URL 且非 Keycloak realm 形态）回落为从 issuer 派生，
   **纯配置态判定**，不存在「拉取失败就换数据源」的运行期回落。
3. `kid` 轮换：未命中时强制刷新**一次**（只缓存不刷新 → 轮换后永久 401）。
4. JWKS 不可达 → 20002 fail-closed（绝不退化成不验签）。
5. HS256 路径行为不变（私有化对称签发现场）。

测试用**真实** RSA 密钥与真实 JWKS JSON 解析，只把 HTTP 换成假 opener——
否则「解析 JWKS、按 kid 配键」这段逻辑等于没测。
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import (
    AuthContext,
    reset_jwks_cache,
    resolve_jwks_url,
    uses_asymmetric_algorithm,
)
from tests.conftest import TEST_TENANT_ID, TEST_USER_ID

pytestmark = pytest.mark.unit

#: 内部 Keycloak 地址（编排里无条件注入的那个）
INTERNAL_BASE = "http://keycloak:8080"
#: 公网 issuer（`OA_JWT_ISSUER`，令牌里的 iss 就是它）
PUBLIC_ISSUER = "https://moy.example.com/realms/officeai"


def _b64url_uint(value: int) -> str:
    """把 RSA 大整数编码成 JWK 的 base64url（无填充）。

    Args:
        value: 整数值。

    Returns:
        str: base64url 字符串。
    """
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwks_document(*keys: tuple[str, rsa.RSAPrivateKey]) -> dict[str, Any]:
    """构造 JWKS 文档。

    Args:
        *keys: `(kid, 私钥)` 对。

    Returns:
        dict: JWKS JSON 结构。
    """
    entries: list[dict[str, Any]] = []
    for kid, private_key in keys:
        numbers = private_key.public_key().public_numbers()
        entries.append(
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        )
    return {"keys": entries}


class _FakeResponse:
    """`json.load()` 可消费的最小响应对象。"""

    def __init__(self, payload: Any) -> None:
        """初始化。

        Args:
            payload: 待序列化成 JSON 的对象。
        """
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *args: Any) -> bytes:
        """读取内容（`json.load` 只调这一个方法）。

        Args:
            *args: 透传给 `BytesIO.read`。

        Returns:
            bytes: 内容字节。
        """
        return self._buffer.read(*args)

    def __enter__(self) -> _FakeResponse:
        """进入上下文。

        Returns:
            _FakeResponse: 自身。
        """
        return self

    def __exit__(self, *args: Any) -> bool:
        """退出上下文。

        Args:
            *args: 忽略。

        Returns:
            bool: False（不吞异常）。
        """
        return False


class _FakeOpener:
    """替换 `urllib.request.build_opener()` 的假 opener。"""

    def __init__(self, handler: Any, recorder: list[str]) -> None:
        """初始化。

        Args:
            handler: 接收请求 URL 并返回响应体或抛异常的回调。
            recorder: 记录每次请求的 URL（用于断言"到底打了哪个地址"）。
        """
        self._handler = handler
        self._recorder = recorder

    def open(self, request: Any, timeout: float | None = None) -> Any:
        """执行一次"HTTP 请求"。

        Args:
            request: `urllib.request.Request`。
            timeout: 超时（断言其被传入）。

        Returns:
            Any: 假响应对象。

        Raises:
            Exception: 由 `handler` 决定（用于模拟不可达）。
        """
        url = getattr(request, "full_url", str(request))
        self._recorder.append(url)
        _ = timeout
        return self._handler(url)


def _install_fake_jwks(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[str]:
    """把 urllib 的 HTTP 出口换成假实现。

    Args:
        monkeypatch: pytest monkeypatch。
        handler: 接收 URL、返回响应体（或抛异常）的回调。

    Returns:
        list[str]: 请求记录（断言访问了哪个地址、访问了几次）。
    """
    recorder: list[str] = []

    def _build_opener(*args: Any, **kwargs: Any) -> _FakeOpener:
        _ = (args, kwargs)
        return _FakeOpener(handler, recorder)

    monkeypatch.setattr(urllib.request, "build_opener", _build_opener)
    return recorder


def _rs256_settings(**overrides: Any) -> Settings:
    """构造 RS256 现场配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "private",
        "downstream_enabled": False,
        "auth_mode": "oidc",
        "jwt_algorithm": "RS256",
        "jwt_issuer": PUBLIC_ISSUER,
        "jwt_audience": "officeai-adapter",
        # RS256 现场 `jwt_secret` 仍必须存在：产物下载令牌用它做 HMAC
        "jwt_secret": "s" * 64,
        "keycloak_base_url": INTERNAL_BASE,
        "keycloak_realm": "officeai",
        "ratelimit_enabled": False,
        "background_jobs_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _sign(private_key: rsa.RSAPrivateKey, kid: str, conf: Settings, **claims: Any) -> str:
    """用 RSA 私钥签发测试令牌。

    Args:
        private_key: 私钥。
        kid: 密钥 ID（写进 JOSE 头，JWKS 按它配键）。
        conf: 配置实例（取 aud / iss）。
        **claims: 覆盖默认声明。

    Returns:
        str: JWT 令牌。
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": TEST_USER_ID,
        "tenant_id": TEST_TENANT_ID,
        "aud": conf.jwt_audience,
        "iss": conf.jwt_issuer,
        "exp": now + 600,
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(scope="module")
def key_a() -> rsa.RSAPrivateKey:
    """签名密钥 A。

    Returns:
        rsa.RSAPrivateKey: 2048 位私钥。
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def key_b() -> rsa.RSAPrivateKey:
    """签名密钥 B（模拟 Keycloak 轮换出的新密钥）。

    Returns:
        rsa.RSAPrivateKey: 2048 位私钥。
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _clear_jwks_cache() -> None:
    """每个用例前清空进程内 JWKS 缓存，避免用例之间串味。"""
    reset_jwks_cache()


# ---------------------------------------------------------------------------
# 地址推导（纯函数，不需要网络）
# ---------------------------------------------------------------------------


def test_uses_asymmetric_algorithm() -> None:
    """`RS*` 走 JWKS，`HS*` 仍走共享密钥。"""
    assert uses_asymmetric_algorithm("RS256") is True
    assert uses_asymmetric_algorithm("rs256") is True
    assert uses_asymmetric_algorithm("  RS512 ") is True
    assert uses_asymmetric_algorithm("HS256") is False
    assert uses_asymmetric_algorithm("") is False


def test_jwks_url_uses_internal_keycloak_not_public_issuer() -> None:
    """JWKS 走内网：issuer 是公网 realm 形态时仍取 `keycloak_base_url`。"""
    conf = _rs256_settings()
    assert resolve_jwks_url(conf) == (
        f"{INTERNAL_BASE}/realms/officeai/protocol/openid-connect/certs"
    )


def test_jwks_url_is_independent_of_jwt_issuer() -> None:
    """**JWKS 地址与 `jwt_issuer` 完全无关**（定稿：无回落分支）。

    这是刻意设计的结果，不是实现细节：`keycloak_base_url` 的语义是
    「适配层视角的 IdP 地址」，客户自带外部 IdP 时它必须指向客户 IdP
    （否则令牌自省与用户资料接口先就挂了）。所以外部 IdP 场景走的是
    **同一条**路径，不存在「从公网 issuer 派生」的回落分支。

    本用例用三种 issuer 形态（realm URL / 外部 IdP URL / 非 URL 的旧占位值）
    断言推导结果**逐字相同**——一旦有人加回回落分支，这里立刻红。
    """
    expected = f"{INTERNAL_BASE}/realms/officeai/protocol/openid-connect/certs"
    for issuer in (
        PUBLIC_ISSUER,
        "https://login.example.com/tenant-a/v2.0",
        "officeai",
        "",
    ):
        assert resolve_jwks_url(_rs256_settings(jwt_issuer=issuer)) == expected


def test_jwks_url_empty_base_returns_empty_string() -> None:
    """`keycloak_base_url` 为空 → 返回空串，由启动校验/调用方 fail-closed。

    绝不能拼出 `/realms/officeai/...` 这种**相对**地址：那会被当成同源路径去打
    适配层自己，得到一个与鉴权无关的 404，而错误码仍是 20002（令牌非法）——
    排查方向直接被带偏。
    """
    assert resolve_jwks_url(_rs256_settings(keycloak_base_url="   ")) == ""


def test_jwks_url_normalises_trailing_slash() -> None:
    """尾斜杠不能让地址双斜杠化（Keycloak 侧会 404）。"""
    conf = _rs256_settings(
        keycloak_base_url="http://keycloak:8080/",
        keycloak_realm="/officeai/",
        jwt_issuer="https://moy.example.com/realms/officeai/",
    )
    assert "//realms" not in resolve_jwks_url(conf)
    assert resolve_jwks_url(conf) == (
        f"{INTERNAL_BASE}/realms/officeai/protocol/openid-connect/certs"
    )


# ---------------------------------------------------------------------------
# 验签主路径
# ---------------------------------------------------------------------------


def test_rs256_valid_token_verifies_and_hits_internal_jwks(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """RS256 有效令牌验签通过，且 JWKS 请求打的是**内网**地址。"""
    conf = _rs256_settings()
    recorder = _install_fake_jwks(
        monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a)))
    )

    auth = AuthContext.resolve(_sign(key_a, "kid-a", conf), conf)

    assert auth.user_id == TEST_USER_ID
    assert auth.tenant_id == TEST_TENANT_ID
    assert recorder == [f"{INTERNAL_BASE}/realms/officeai/protocol/openid-connect/certs"]
    assert not any("moy.example.com" in url for url in recorder)


def test_rs256_external_idp_still_hits_configured_idp_base(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """外部 IdP 现场：JWKS 仍打 `keycloak_base_url`（客户 IdP 地址），不打公网 issuer。

    端到端证明「无回落分支」：把 issuer 换成客户 IdP 的公网域名，请求地址不变。
    """
    conf = _rs256_settings(
        jwt_issuer="https://login.example.com/tenant-a/v2.0",
        keycloak_base_url="http://idp.internal:8080",
    )
    recorder = _install_fake_jwks(
        monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a)))
    )

    auth = AuthContext.resolve(_sign(key_a, "kid-a", conf), conf)

    assert auth.user_id == TEST_USER_ID
    assert recorder == ["http://idp.internal:8080/realms/officeai/protocol/openid-connect/certs"]


def test_rs256_empty_base_url_fails_closed(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """RS256 但 `keycloak_base_url` 为空 → 20002，且**不发任何网络请求**。

    启动校验（`validate_auth_startup`）本应拦住这种配置；本用例覆盖"绕过启动
    校验直接构造 Settings"的路径，确保它也是 fail-closed 而不是拼相对 URL。
    """

    def _boom(url: str) -> Any:  # pragma: no cover - 走到这里就是缺陷
        raise AssertionError(f"base_url 为空时不应发起 JWKS 请求：{url}")

    recorder = _install_fake_jwks(monkeypatch, _boom)
    conf = _rs256_settings(keycloak_base_url="")

    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(_sign(key_a, "kid-a", conf), conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED
    assert "unconfigured" in str(excinfo.value.internal_detail)
    assert recorder == []


def test_rs256_kid_rotation_triggers_exactly_one_refresh(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey, key_b: rsa.RSAPrivateKey
) -> None:
    """Keycloak 轮换密钥后：`kid` 未命中 → 刷新一次 → 新令牌可验。

    只缓存不刷新会让轮换后**永久 401**（缓存里永远没有新 kid），本用例锁死这个坑。
    """
    conf = _rs256_settings()
    documents = [
        _jwks_document(("kid-a", key_a)),  # 第一次拉取：只有旧密钥
        _jwks_document(("kid-a", key_a), ("kid-b", key_b)),  # 刷新后：新旧并存
    ]
    calls: list[int] = []

    def _handler(url: str) -> _FakeResponse:
        index = min(len(calls), len(documents) - 1)
        calls.append(1)
        return _FakeResponse(documents[index])

    recorder = _install_fake_jwks(monkeypatch, _handler)

    token = _sign(key_b, "kid-b", conf)
    auth = AuthContext.resolve(token, conf)

    assert auth.user_id == TEST_USER_ID
    assert len(recorder) == 2, f"应当恰好拉取两次（首次 + 刷新一次），实际 {recorder}"


def test_rs256_cache_avoids_refetch(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """同 kid 连续两次验签只拉一次 JWKS（热路径不能每请求打 Keycloak）。"""
    conf = _rs256_settings()
    recorder = _install_fake_jwks(
        monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a)))
    )

    token = _sign(key_a, "kid-a", conf)
    AuthContext.resolve(token, conf)
    AuthContext.resolve(token, conf)

    assert len(recorder) == 1


# ---------------------------------------------------------------------------
# fail-closed
# ---------------------------------------------------------------------------


def test_rs256_jwks_unreachable_fails_closed(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """JWKS 拉不到 → 20002，**绝不**退化成不验签。"""

    def _boom(url: str) -> Any:
        raise urllib.error.URLError("connection refused")

    _install_fake_jwks(monkeypatch, _boom)
    conf = _rs256_settings()

    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(_sign(key_a, "kid-a", conf), conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED
    assert "jwks unreachable" in str(excinfo.value.internal_detail)


def test_rs256_unknown_kid_after_refresh_fails(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey, key_b: rsa.RSAPrivateKey
) -> None:
    """刷新后仍找不到该 kid（伪造令牌/未知密钥）→ 20002。"""
    conf = _rs256_settings()
    _install_fake_jwks(monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a))))

    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(_sign(key_b, "kid-unknown", conf), conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED


def test_rs256_wrong_signature_rejected(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey, key_b: rsa.RSAPrivateKey
) -> None:
    """签名的私钥与 JWKS 里的公钥不配对 → 20002。"""
    conf = _rs256_settings()
    _install_fake_jwks(monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a))))

    forged = _sign(key_b, "kid-a", conf)  # 借 kid-a 的名义，用 B 签
    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(forged, conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED


def test_rs256_expired_token_rejected(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """过期拒绝在 RS256 路径同样生效（不能因为换算法放宽时效）。"""
    conf = _rs256_settings()
    _install_fake_jwks(monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a))))

    token = _sign(key_a, "kid-a", conf, exp=int(time.time()) - 60)
    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(token, conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED


def test_rs256_wrong_audience_rejected(
    monkeypatch: pytest.MonkeyPatch, key_a: rsa.RSAPrivateKey
) -> None:
    """`aud` 不匹配必须拒绝——Keycloak 默认 `aud=account`，这正是 K5 要配 mapper 的原因。"""
    conf = _rs256_settings()
    _install_fake_jwks(monkeypatch, lambda url: _FakeResponse(_jwks_document(("kid-a", key_a))))

    with pytest.raises(ApiError) as excinfo:
        AuthContext.resolve(_sign(key_a, "kid-a", conf, aud="account"), conf)

    assert excinfo.value.code == ErrorCode.UNAUTHORIZED


# ---------------------------------------------------------------------------
# 回归：HS256 不能被分流改坏
# ---------------------------------------------------------------------------


def test_hs256_still_uses_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """`HS256` 现场行为不变，且**不产生任何 JWKS 网络请求**。"""

    def _boom(url: str) -> Any:  # pragma: no cover - 走到这里就是缺陷
        raise AssertionError(f"HS256 路径不应请求 JWKS：{url}")

    recorder = _install_fake_jwks(monkeypatch, _boom)
    conf = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="oidc",
        jwt_algorithm="HS256",
        jwt_secret="k" * 64,
        jwt_issuer=PUBLIC_ISSUER,
        ratelimit_enabled=False,
        background_jobs_enabled=False,
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": TEST_USER_ID,
            "tenant_id": TEST_TENANT_ID,
            "aud": conf.jwt_audience,
            "iss": conf.jwt_issuer,
            "exp": now + 600,
        },
        conf.jwt_secret,
        algorithm="HS256",
    )

    auth = AuthContext.resolve(token, conf)
    assert auth.user_id == TEST_USER_ID
    assert recorder == []


def test_stub_mode_does_not_touch_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """`auth_mode=stub` 不验签、也不该去拉 JWKS（开发态无网络）。"""

    def _boom(url: str) -> Any:  # pragma: no cover - 走到这里就是缺陷
        raise AssertionError(f"stub 模式不应请求 JWKS：{url}")

    recorder = _install_fake_jwks(monkeypatch, _boom)
    conf = _rs256_settings(auth_mode="stub")
    now = int(time.time())
    token = jwt.encode(
        {"sub": TEST_USER_ID, "tenant_id": TEST_TENANT_ID, "exp": now + 600},
        conf.jwt_secret,
        algorithm="HS256",
    )

    auth = AuthContext.resolve(token, conf)
    assert auth.user_id == TEST_USER_ID
    assert recorder == []
