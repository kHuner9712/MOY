"""启动期鉴权防呆（S1，2026-10-23）。

**这些用例存在的理由**：`auth_mode` 的默认值是 `stub`，而 stub 下不校验签名。
如果只在文档里写「生产必须 oidc」，下一个照抄默认值部署的人就会把整个租户数据
暴露给任何能发 HTTP 请求的人。所以判据必须落在代码上：生产/预发 + stub = 启动失败。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import validate_auth_startup


def test_production_stub_is_rejected() -> None:
    """生产环境使用桩鉴权 → 30004（不验签等于关闭鉴权）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(env="production", auth_mode="stub", jwt_secret="a" * 32, host="127.0.0.1")
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_staging_stub_is_rejected() -> None:
    """预发同样按生产口径拦截（预发常被用来做上线前演练，数据是真的）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(env="staging", auth_mode="stub", jwt_secret="a" * 32, host="127.0.0.1")
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_production_oidc_requires_secret() -> None:
    """OIDC 模式但签名为空 → 30004（令牌与产物下载都无法验签）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(Settings(env="production", auth_mode="oidc", jwt_secret="  "))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_production_oidc_with_secret_passes() -> None:
    """生产 + OIDC + 非空密钥 = 合法启动配置。

    P1-10 起密钥长度也参与判定（`MIN_JWT_SECRET_LENGTH=32`），故夹具密钥补齐到
    32 字符以上——**修夹具，不是削弱校验**（原值 `...-32-chars-long` 只有 31 字符，
    名字里的 "32" 本身就不成立）。
    """
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="production-secret-32-chars-long-v2",
            public_base_url="https://moy.example.com",
        )
    )


def test_development_stub_is_allowed() -> None:
    """本地开发允许桩模式，但**仅限 loopback 监听**（B-5 硬化 3）。"""
    validate_auth_startup(
        Settings(env="development", auth_mode="stub", jwt_secret="", host="127.0.0.1")
    )


# ===========================================================================
# P1-09：`OA_PAY_CHANNEL=fake` 的生产守卫
#
# fake 通道**不验支付回调签名**，等于谁都能伪造"支付成功"把订单置为已付。
# 它与 `auth_mode=stub` 属同一类测试开关，必须享受同一套生产守卫。
# ===========================================================================


def test_production_fake_pay_channel_is_rejected() -> None:
    """生产 + `pay_channel=fake` → 30004（回调不验签等于允许伪造支付）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="production",
                auth_mode="oidc",
                jwt_secret="production-secret-32-chars-long!",
                pay_channel="fake",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_PAY_CHANNEL" in excinfo.value.internal_detail


def test_staging_fake_pay_channel_is_rejected() -> None:
    """预发同口径拦截（预发演练用的是真数据）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="staging",
                auth_mode="oidc",
                jwt_secret="production-secret-32-chars-long!",
                pay_channel="fake",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


# ===========================================================================
# P1-10：`OA_JWT_SECRET` 的占位值与最短长度
#
# 只判"非空"挡不住「手抄 .env.example」：模板值 `CHANGE_ME_*` 是仓库公开常量，
# 用它当签名密钥 = 任何人都能自造任意租户/角色的令牌。
#
# 模板侧（N3）已把占位值写到 32 字符以上（`CHANGE_ME_generate_with_openssl_rand_hex_32`），
# 好让照文档走的人看到"命中占位值特征"（准确、可操作），而不是"长度不足"。
# 这里**同时**覆盖老的 `CHANGE_ME_STRONG_PASSWORD`：那份值仍散落在运维自己的
# .env / k8s Secret 里，识别它同样是本判据的职责。
# ===========================================================================


@pytest.mark.parametrize(
    "secret",
    [
        "CHANGE_ME_generate_with_openssl_rand_hex_32",
        "CHANGE_ME_STRONG_PASSWORD",
        "changeme",
        "my-super-secret-password-for-prod",
        "short-key",
    ],
    ids=["current-template", "legacy-template", "changeme", "contains-password", "too-short"],
)
def test_production_placeholder_or_short_secret_is_rejected(secret: str) -> None:
    """占位值 / 过短密钥 → 30004（公开常量当密钥等于不设防）。

    Args:
        secret: 待校验的密钥取值（占位值特征或长度不足）。
    """
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(Settings(env="production", auth_mode="oidc", jwt_secret=secret))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_JWT_SECRET" in excinfo.value.internal_detail


def test_production_strong_secret_still_passes() -> None:
    """32 字符以上的随机密钥照常放行（新判据不能误伤合法配置）。"""
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            public_base_url="https://moy.example.com",
        )
    )


# ===========================================================================
# 安全降级开关门禁（QA-V04）
#
# `tenant_membership_check=off` 会**跳过跨租户成员校验**。它必须能被用（线上
# 卡死时要能止血），但不能「配一行就生效」——否则任何人都能在 .env 里加一行
# 把租户隔离关掉，而系统只打一条 WARNING。所以生产/预发下要它生效，必须
# 「off + allow_security_downgrade=true」两处同时显式声明。
# ===========================================================================


@pytest.mark.parametrize("env_name", ["production", "staging"])
def test_downgrade_switch_off_is_rejected_without_explicit_allow(env_name: str) -> None:
    """生产/预发 + `tenant_membership_check=off` 且未放行 → 30004 启动失败。

    Args:
        env_name: 环境名（生产与预发同口径：预发常用来做上线前演练，数据是真的）。
    """
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env=env_name,  # type: ignore[arg-type]
                auth_mode="oidc",
                jwt_secret="downgrade-secret-32-chars-long-v2",
                # K1 起对外基址是必填项；不补会让本用例先撞上"缺基址"而非被测的
                # "降级开关未放行"，断言就测错了对象。
                public_base_url="https://moy.example.com",
                tenant_membership_check="off",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    # 文案必须说清「怎么改」与「为什么」，而不是只丢一句"配置错误"
    assert "OA_ALLOW_SECURITY_DOWNGRADE" in excinfo.value.internal_detail


def test_downgrade_switch_off_passes_with_explicit_allow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """生产 + `off` + 显式放行 → 启动通过，且**必须留下 WARNING**。

    Args:
        capsys: pytest 输出捕获夹具。本仓的结构化日志走 `PrintLoggerFactory`
            直接写 stdout（不经 stdlib logging），因此必须用 `capsys` 而不是
            `caplog` 才能看到事件名。
    """
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="downgrade-secret-32-chars-long-v2",
            public_base_url="https://moy.example.com",
            tenant_membership_check="off",
            allow_security_downgrade=True,
        )
    )
    captured = capsys.readouterr().out
    assert "security_downgrade_enabled" in captured, "显式放行时也必须留下可见告警"


def test_auto_mode_passes_in_production() -> None:
    """默认 `auto` 档在生产不受新门禁影响（只有 `off` 被拦）。"""
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="downgrade-secret-32-chars-long-v2",
            public_base_url="https://moy.example.com",
        )
    )


def test_downgrade_switch_off_rejected_in_test_env_without_explicit_allow() -> None:
    """B-5 行为变更：`off` 在 test/development 下也**不得静默生效**。

    旧行为是「development/test 因启动期提前 return，off 连 WARNING 都没有就生效」。
    现在所有 env 都要求 `off` + `allow_security_downgrade=true` 双显式声明；
    夹具补 loopback host 是为了避开硬化 3，让用例精确测「降级开关」这一条。
    """
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="test",
                auth_mode="stub",
                jwt_secret="",
                host="127.0.0.1",
                tenant_membership_check="off",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_ALLOW_SECURITY_DOWNGRADE" in excinfo.value.internal_detail


def test_downgrade_switch_off_explicitly_allowed_in_development_logs_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """路径 1：development 下显式放行的降级必须留下**显著 ERROR** 留痕。

    Args:
        capsys: 结构化日志直写 stdout，用 capsys 而非 caplog。
    """
    validate_auth_startup(
        Settings(
            env="development",
            auth_mode="stub",
            jwt_secret="",
            host="127.0.0.1",
            tenant_membership_check="off",
            allow_security_downgrade=True,
        )
    )
    captured = capsys.readouterr().out
    assert "security_downgrade_enabled" in captured
    assert "ERROR" in captured or "error" in captured


# ===========================================================================
# 对外基址必填（K1 / R4）
#
# 空 `OA_PUBLIC_BASE_URL` 不会立刻抛错，而是让两件事**静默**退化：
#   - 会话 Cookie 丢掉 `Secure`（会话 id 走明文，反代后尤其危险）；
#   - realm 渲染出 `redirectUris: ["/auth/callback"]` 这类非法值，
#     登录被 Keycloak 拒绝，报 `Invalid parameter: redirect_uri`（现场极难定位）。
# 所以与"配错了才发现"的其余项同一处置：生产/预发直接启动失败。
# ===========================================================================


def test_production_requires_public_base_url() -> None:
    """生产 + OIDC + 强密钥但缺对外基址 → 30004，且文案点名该配置项。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="production",
                auth_mode="oidc",
                jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_PUBLIC_BASE_URL" in excinfo.value.internal_detail


def test_production_blank_public_base_url_is_rejected() -> None:
    """只有空白字符也按"空"处理（配置文件里写成空格是最常见的误配形态）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="production",
                auth_mode="oidc",
                jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
                public_base_url="   ",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_development_does_not_require_public_base_url() -> None:
    """development 不拦（本地演示没有域名，且该环境不对外）。"""
    validate_auth_startup(
        Settings(
            env="development", auth_mode="stub", jwt_secret="", public_base_url="", host="127.0.0.1"
        )
    )


# ===========================================================================
# JWKS 数据源必填（K6 / R8，定稿判据）
#
# RS* 验签的公钥只能从 JWKS 取，而 JWKS 地址恒由 `OA_KEYCLOAK_BASE_URL` 推导
# （**无回落分支**：外部 IdP 现场该变量本就指向客户 IdP）。为空时若放行，
# 运行期每个请求都 20002「令牌非法」，而真因是配置——排障会被引向令牌。
# 只拦 RS*：HS256 的私有化对称签发现场不依赖它。
# ===========================================================================


def test_production_rs256_requires_keycloak_base_url() -> None:
    """RS256 + 空 `keycloak_base_url` → 30004，且文案点名该项。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env="production",
                auth_mode="oidc",
                jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
                public_base_url="https://moy.example.com",
                jwt_algorithm="RS256",
                keycloak_base_url="",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_KEYCLOAK_BASE_URL" in excinfo.value.internal_detail


def test_production_rs256_with_base_url_passes() -> None:
    """RS256 + 非空 base_url → 放行（不能误伤合法配置）。"""
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            public_base_url="https://moy.example.com",
            jwt_algorithm="RS256",
            keycloak_base_url="http://keycloak:8080",
        )
    )


def test_production_hs256_does_not_require_keycloak_base_url() -> None:
    """HS256 的私有化对称签发现场**不依赖** base_url，不能被这条门禁挡在门外。"""
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            public_base_url="https://moy.example.com",
            jwt_algorithm="HS256",
            keycloak_base_url="",
        )
    )


# ===========================================================================
# P0-5 §2.4.6：会话 Cookie 的 `Secure` 只由对外基址派生，非 https 现场必须告警
# ===========================================================================
def test_http_public_base_url_warns_but_does_not_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """生产 + `http://` 基址 → **启动通过**，但必须留下 `cookie_secure_disabled` 告警。

    为什么不判 fail：私有化纯 HTTP（客户内网、无域名无证书）是**合法现场**，
    判 fail 会把这类部署直接挡在门外。但"能部署"不等于"可以没人知道"——会话
    Cookie 丢掉 `Secure` 后，会话标识在同网段可被嗅探，这条 WARNING 是它唯一的
    可观测痕迹。

    Args:
        capsys: pytest 输出捕获夹具（本仓结构化日志直写 stdout，`caplog` 看不到）。
    """
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            public_base_url="http://192.168.1.50:8080",
            jwt_algorithm="HS256",
        )
    )
    captured = capsys.readouterr().out
    assert "cookie_secure_disabled" in captured, "HTTP 现场必须留下可见告警"
    # 文案要点名后果与处置，而不是只丢一个事件名
    assert "Secure" in captured
    assert "明文" in captured


def test_https_public_base_url_has_no_insecure_cookie_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """生产 + `https://` 基址 → 不应出现该告警（否则告警会被当噪声忽略）。

    Args:
        capsys: pytest 输出捕获夹具。
    """
    validate_auth_startup(
        Settings(
            env="production",
            auth_mode="oidc",
            jwt_secret="9f2c7b1e4d8a6f0c3b5e7d9a1c2f4b6e",
            public_base_url="https://moy.example.com",
            jwt_algorithm="HS256",
        )
    )
    assert "cookie_secure_disabled" not in capsys.readouterr().out


def test_http_public_base_url_does_not_warn_in_development(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本地开发（`OA_ENV=development`）用 http 是常态，不该刷这条告警。

    Args:
        capsys: pytest 输出捕获夹具。
    """
    validate_auth_startup(
        Settings(
            env="development",
            auth_mode="stub",
            public_base_url="http://localhost:3000",
            host="127.0.0.1",
        )
    )
    assert "cookie_secure_disabled" not in capsys.readouterr().out


# ===========================================================================
# B-5 硬化 3：stub + 非 loopback 监听 → 30004（**不受 env 限制**）
# ===========================================================================


@pytest.mark.parametrize("env_name", ["development", "test", "staging", "production"])
def test_stub_non_loopback_bind_is_rejected_in_every_env(env_name: str) -> None:
    """`auth_mode=stub` 且 `OA_HOST` 非回环 → 所有 env 一律 30004。

    这条判据是「`env` 默认 development + `auth_mode` 默认 stub + `host` 默认
    0.0.0.0」这条越权链唯一的机械阻断点：下方 8 条判据在 development/test
    被提前 return，若无本判据，漏注入配置就会得到一个完全不验签的服务。

    Args:
        env_name: 环境名（四种全部覆盖，证明不受 env 限制）。
    """
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(
            Settings(
                env=env_name,  # type: ignore[arg-type]
                auth_mode="stub",
                jwt_secret="a" * 32,
                host="0.0.0.0",  # noqa: S104 - 负样本：刻意绑定全部网卡
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_HOST" in excinfo.value.internal_detail


@pytest.mark.parametrize("loopback", ["127.0.0.1", "localhost", "::1"])
def test_stub_loopback_bind_is_allowed(loopback: str) -> None:
    """显式回环地址仍允许本机联调（判据不能误伤合法开发路径）。

    Args:
        loopback: 回环地址字面量。
    """
    validate_auth_startup(
        Settings(env="development", auth_mode="stub", jwt_secret="", host=loopback)
    )
