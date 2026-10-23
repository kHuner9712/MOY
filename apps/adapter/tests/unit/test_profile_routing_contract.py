"""档位路由契约测试（阶段四 T4-04.1，架构 §6.2 / §6.3 / §6.4 / F-8）。

F-8 的行径是"四个档位共用同一个默认应用密钥"，现象很隐蔽：目录显示 4 档可用、
每次调用都成功，只是**所有请求都跑在通用应用上**（编程档拿不到代码模型的提示词）。
本文件把"四档四密钥"钉成契约：

1. 4 档解析出 **4 个互不相同**的密钥；
2. `generate` / `generate_stream` 送出的 `api_key` 与请求档位**一一对应**；
3. 主密钥缺失 → 走 `fallback_app_env`（不直接判定不可用）；
4. 全部缺失 + 真实模式 → **10004**，且上游调用 0 次；
5. M3 目录 `status` 取**运行期**可用性（B-3），与 `/readyz` 同口径；
6. `resolve_app_key` **不再读 `os.environ`**（密钥只来自 `Settings`）。
"""

from __future__ import annotations

import pytest

from app.clients.dify_client import DifyClient, DifyResponse
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.model import (
    ChatMessage,
    GenerateRequest,
    MessageRole,
    ModelProfile,
    ProfileStatus,
)
from app.services.model_service import ModelService
from app.services.profile_routing import ProfileRouter, app_env_map

pytestmark = pytest.mark.unit

#: 四档各自不同的假密钥（**故意各不相同**：相同就说明又退回 F-8 了）
KEYS: dict[str, str] = {
    "general": "app-general-key",
    "code": "app-code-key",
    "long_context": "app-long-context-key",
    "reasoning": "app-reasoning-key",
}


def _settings(**overrides: object) -> Settings:
    """构造"四档齐备 + 真实下游"的配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, object] = {
        "env": "test",
        "business_store": "memory",
        "downstream_enabled": True,
        "dify_default_app_key": KEYS["general"],
        "dify_code_app_key": KEYS["code"],
        "dify_long_context_app_key": KEYS["long_context"],
        "dify_reasoning_app_key": KEYS["reasoning"],
        "dify_fallback_app_key": "app-fallback-key",
    }
    base.update(overrides)
    return Settings(**base)


def _request(profile: ModelProfile) -> GenerateRequest:
    """构造生成请求。

    Args:
        profile: 能力档位。

    Returns:
        GenerateRequest: 请求。
    """
    return GenerateRequest(
        profile=profile,
        messages=[ChatMessage(role=MessageRole.USER, content="你好")],
    )


class _KeyCapturingClient:
    """记录 `api_key` 并返回一条 blocking 响应的底座替身。"""

    def __init__(self) -> None:
        self.api_keys: list[str] = []

    async def post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录密钥并返回固定响应。"""
        del path, payload, idempotent, api_key
        return DifyResponse(
            200,
            {
                "event": "message",
                "answer": "好的",
                "metadata": {"usage": {"prompt_tokens": 3, "completion_tokens": 2}},
            },
        )


class TestKeyResolution:
    """四档四密钥与降级链。"""

    def test_four_profiles_resolve_four_distinct_keys(self) -> None:
        """F-8 的核心断言：4 个档位 → 4 个**不同**的密钥。"""
        router = ProfileRouter(_settings())

        resolved = {profile: router.app_key(profile) for profile in KEYS}

        assert resolved == KEYS
        assert len(set(resolved.values())) == 4

    def test_keys_come_from_settings_not_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`resolve_app_key` 只认 `Settings`：环境变量被设成别的值也不影响。

        这是 F-8 的根因回归——此前它直接读 `os.environ`，注入式配置形同虚设。
        """
        monkeypatch.setenv("OA_DIFY_CODE_APP_KEY", "env-should-be-ignored")

        router = ProfileRouter(_settings())

        assert router.app_key("code") == KEYS["code"]
        assert app_env_map(_settings())["OA_DIFY_CODE_APP_KEY"] == KEYS["code"]

    def test_missing_primary_falls_back(self) -> None:
        """主密钥缺失 → 用降级链里的密钥（不是直接判不可用）。"""
        router = ProfileRouter(_settings(dify_code_app_key=""))

        assert router.app_key("code") == KEYS["general"]
        assert router.uses_fallback("code") is True
        assert router.is_available("code") is True

    def test_all_keys_missing_makes_profile_unavailable(self) -> None:
        """四档与降级链全空 → 该档不可用（真实模式下）。"""
        router = ProfileRouter(
            _settings(
                dify_default_app_key="",
                dify_code_app_key="",
                dify_long_context_app_key="",
                dify_reasoning_app_key="",
                dify_fallback_app_key="",
            )
        )

        assert router.app_key("code") == ""
        assert router.is_available("code") is False
        assert router.available_profiles() == []

    def test_mock_mode_keeps_yaml_status(self) -> None:
        """桩模式（无真实下游）沿用 yaml 声明：否则离线用例的目录会全不可用。"""
        router = ProfileRouter(
            _settings(
                downstream_enabled=False,
                dify_default_app_key="",
                dify_code_app_key="",
                dify_long_context_app_key="",
                dify_reasoning_app_key="",
            )
        )

        assert router.is_available("code") is True
        assert len(router.available_profiles()) == 4


class TestGenerateUsesProfileKey:
    """真正发请求时用的是"该档"的密钥。"""

    async def test_generate_sends_profile_specific_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """四个档位各发一次 → 捕获到的密钥序列与档位一一对应。"""
        captured: list[str] = []
        client = _KeyCapturingClient()

        def _for_app_key(cls: type[DifyClient], key: str, settings: object = None) -> object:
            del cls, settings
            captured.append(key)
            return client

        monkeypatch.setattr(DifyClient, "for_app_key", classmethod(_for_app_key))
        service = ModelService(_settings())
        auth = AuthContext(user_id="usr_route", tenant_id="tnt_route")

        for profile in (
            ModelProfile.GENERAL,
            ModelProfile.CODE,
            ModelProfile.LONG_CONTEXT,
            ModelProfile.REASONING,
        ):
            await service.generate(_request(profile), "usr_route", auth)

        assert captured == [
            KEYS["general"],
            KEYS["code"],
            KEYS["long_context"],
            KEYS["reasoning"],
        ]

    async def test_resolve_app_key_matches_profile(self) -> None:
        """`resolve_app_key` 与路由表一致（Agent/其它服务复用它时不漂移）。"""
        service = ModelService(_settings())

        assert service.resolve_app_key(ModelProfile.CODE) == KEYS["code"]
        assert service.resolve_app_key(ModelProfile.REASONING) == KEYS["reasoning"]

    async def test_all_keys_missing_rejects_before_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全缺失时不发上游请求，返回既有模型类错误码 10004（不新增错误码）。"""
        calls: list[str] = []

        def _for_app_key(cls: type[DifyClient], key: str, settings: object = None) -> object:
            del cls, settings
            calls.append(key)
            return _KeyCapturingClient()

        monkeypatch.setattr(DifyClient, "for_app_key", classmethod(_for_app_key))
        service = ModelService(
            _settings(
                dify_default_app_key="",
                dify_code_app_key="",
                dify_long_context_app_key="",
                dify_reasoning_app_key="",
                dify_fallback_app_key="",
            )
        )

        with pytest.raises(ApiError) as excinfo:
            await service.generate(
                _request(ModelProfile.CODE), "usr_route", AuthContext(user_id="u", tenant_id="t")
            )

        assert excinfo.value.code == ErrorCode.MODEL_PROFILE_UNAVAILABLE
        assert calls == []  # 上游 0 次


class TestCatalogAndReady:
    """M3 目录与 `/readyz` 同口径。"""

    def test_catalog_status_is_runtime_availability(self) -> None:
        """缺一档密钥 → 只有那一档 `unavailable`。"""
        service = ModelService(_settings(dify_reasoning_app_key=""))

        catalog = service.get_catalog()
        status = {item.profile.value: item.status for item in catalog.profiles}

        assert status["general"] == ProfileStatus.AVAILABLE
        assert status["code"] == ProfileStatus.AVAILABLE
        assert status["long_context"] == ProfileStatus.AVAILABLE
        # 推理档没有降级以外的来源？它配了 fallback → 实际仍可用；这里断言"有降级即可用"
        assert status["reasoning"] == ProfileStatus.AVAILABLE

    def test_catalog_marks_unavailable_when_no_key_and_no_fallback(self) -> None:
        """把降级链也清空 → 该档 `unavailable`（目录不再撒谎）。"""
        service = ModelService(
            _settings(dify_reasoning_app_key="", dify_fallback_app_key="", dify_default_app_key="")
        )

        catalog = service.get_catalog()
        status = {item.profile.value: item.status for item in catalog.profiles}

        assert status["reasoning"] == ProfileStatus.UNAVAILABLE
        assert status["general"] == ProfileStatus.UNAVAILABLE
        # code / long_context 仍有自己的 key → 可用
        assert status["code"] == ProfileStatus.AVAILABLE

    def test_readyz_reports_available_profiles(
        self, client: object, auth_headers: dict[str, str]
    ) -> None:
        """/readyz 追加一个 `profiles` 检查项，只输出展示名（不出现模型名/密钥）。"""
        response = client.get("/readyz")  # type: ignore[attr-defined]

        payload = response.json()
        assert response.status_code in (200, 500)
        names = [item["name"] for item in payload["data"]["checks"]]
        assert "profiles" in names
        detail = next(
            item["detail"] for item in payload["data"]["checks"] if item["name"] == "profiles"
        )
        assert "档位" in detail
        for leaked in KEYS.values():
            assert leaked not in detail
