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
    """生产 + OIDC + 非空密钥 = 合法启动配置。"""
    validate_auth_startup(
        Settings(env="production", auth_mode="oidc", jwt_secret="production-secret-32-chars-long")
    )


def test_development_stub_is_allowed() -> None:
    """本地开发允许桩模式（离线联调与演示需要）。"""
    validate_auth_startup(Settings(env="development", auth_mode="stub", jwt_secret=""))


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
                jwt_secret="downgrade-secret-32-chars-long",
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
            jwt_secret="downgrade-secret-32-chars-long",
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
            jwt_secret="downgrade-secret-32-chars-long",
        )
    )


def test_downgrade_switch_off_allowed_in_test_env() -> None:
    """development/test 不受新门禁影响（离线单测需要能构造降级态）。"""
    validate_auth_startup(
        Settings(env="test", auth_mode="stub", jwt_secret="", tenant_membership_check="off")
    )
