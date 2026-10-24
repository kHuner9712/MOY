"""配置项环境变量名与编排注入名的对齐（配置名漂移防复发，G-35）。

**为什么要有这条用例**：三套编排曾把 `Settings.keycloak_base_url` 对应的环境变量
写成 `OA_KEYCLOAK_URL`——pydantic-settings 的 `extra="ignore"` 让它**静默失效**，
而默认值恰好与注入值相同，所以没有任何可观察症状。门禁
（`deploy/validate_config_names.py`）在编排侧拦这件事，本用例在**配置侧**再钉一颗钉子：
只要 `OA_KEYCLOAK_BASE_URL` 还能覆盖字段，键名就不可能再"改了却没生效"。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_keycloak_base_url_env_overrides_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OA_KEYCLOAK_BASE_URL` 必须覆盖 `keycloak_base_url`（编排用的就是这个名字）。"""
    monkeypatch.setenv("OA_KEYCLOAK_BASE_URL", "http://iam.internal.example:8443")

    # `_env_file=None` 让用例不依赖工作目录下的 .env：否则本机跑与 CI 跑结论可能不同
    settings = Settings(_env_file=None)

    assert settings.keycloak_base_url == "http://iam.internal.example:8443"
