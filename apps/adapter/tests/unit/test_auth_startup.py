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
