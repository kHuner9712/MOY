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
        validate_auth_startup(Settings(env="production", auth_mode="stub", jwt_secret="a" * 32))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_staging_stub_is_rejected() -> None:
    """预发同样按生产口径拦截（预发常被用来做上线前演练，数据是真的）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_auth_startup(Settings(env="staging", auth_mode="stub", jwt_secret="a" * 32))
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
            env="production", auth_mode="oidc", jwt_secret="production-secret-32-chars-long-v2"
        )
    )


def test_development_stub_is_allowed() -> None:
    """本地开发允许桩模式（离线联调与演示需要）。"""
    validate_auth_startup(Settings(env="development", auth_mode="stub", jwt_secret=""))


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
        )
    )


def test_downgrade_switch_off_allowed_in_test_env() -> None:
    """development/test 不受新门禁影响（离线单测需要能构造降级态）。"""
    validate_auth_startup(
        Settings(env="test", auth_mode="stub", jwt_secret="", tenant_membership_check="off")
    )
