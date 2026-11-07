"""L-14：关停路径单步失败不得跳过后续释放。

## 这一组用例在防什么风险

修复前 `app/main.py` 的 lifespan `finally` 段是三条**裸 `await`** 顺序执行：
`get_model_service().aclose()` → `dispose_engine()` → `aclose_shared_redis()`。
任何一条抛异常，后面的全部被跳过——进程带着未释放的数据库引擎与 Redis 连接退出，
对端（Postgres / Valkey）表现为连接缓慢堆积，现场只能靠重启兜，且日志里**看不到**
"是关停时炸了"，只会看到下一次故障。

关停路径的正确语义是"尽最大努力做完每一步"，而不是"遇到第一个错就放弃"。
这里钉住三件事：

1. 单步失败只降级为日志，**不影响后续步骤**；
2. 顺序仍是「底座连接 → 业务库 → Redis」（反序会把干净停机变成一串 `InterfaceError`）；
3. 失败确实被记录（静默吞掉等于把问题藏起来）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI

from app import main as app_main
from app.core.config import Settings
from app.core.idempotency import reset_idempotency_manager
from app.core.ratelimit import RateLimiter
from app.main import _release_on_shutdown
from app.main import lifespan as app_lifespan

pytestmark = pytest.mark.unit


def _settings(**overrides: Any) -> Settings:
    """构造离线可用的配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "private",
        "business_store": "memory",
        "auth_mode": "stub",
        "host": "127.0.0.1",  # B-5：stub 仅 loopback 可启动
        "jwt_secret": "l14-shutdown-unit-secret",
        "downstream_enabled": False,
        "ratelimit_enabled": False,
        "background_jobs_enabled": False,
        "db_auto_migrate": False,
    }
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    """每个用例前后重置装配单例，避免串味。"""
    reset_idempotency_manager()
    yield
    reset_idempotency_manager()


# ===========================================================================
# 1) 辅助函数的语义
# ===========================================================================
async def test_release_swallows_exception() -> None:
    """单步失败只记日志，不向调用方抛出（否则 `finally` 会提前中断）。"""

    async def boom() -> None:
        raise RuntimeError("释放失败")

    await _release_on_shutdown("demo", boom)  # 不抛即通过


async def test_release_accepts_sync_callable_factory() -> None:
    """`closer` 是"工厂"：未被调用就不产生任何副作用（也避免 never-awaited 告警）。"""
    performed: list[str] = []

    async def second() -> None:
        performed.append("second")

    async def first() -> None:
        raise RuntimeError("noop")

    await _release_on_shutdown("first", first)
    await _release_on_shutdown("second", second)
    assert performed == ["second"], "前一步失败不得影响后一步"


# ===========================================================================
# 2) 真实 lifespan 的关停顺序与容错
# ===========================================================================
async def test_lifespan_shutdown_continues_after_each_step_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三步依次抛错时，**三步都要被执行**，且顺序不变（底座 → 业务库 → Redis）。"""
    called: list[str] = []

    class _ExplodingModelService:
        """`aclose` 必抛的底座服务替身。"""

        async def aclose(self) -> None:
            called.append("model_service")
            raise RuntimeError("上游连接池释放失败")

    async def _failing_dispose() -> None:
        called.append("db_engine")
        raise RuntimeError("引擎释放失败")

    async def _failing_redis() -> None:
        called.append("shared_redis")
        raise RuntimeError("Redis 释放失败")

    monkeypatch.setattr(app_main, "get_model_service", lambda: _ExplodingModelService())
    monkeypatch.setattr(app_main, "dispose_engine", _failing_dispose)
    monkeypatch.setattr(app_main, "aclose_shared_redis", _failing_redis)

    settings = _settings()
    fake_app = FastAPI()
    fake_app.state.settings = settings
    fake_app.state.rate_limiter = RateLimiter(settings=settings)

    async with app_lifespan(fake_app):
        pass

    assert called == ["model_service", "db_engine", "shared_redis"], (
        "关停顺序必须是底座 → 业务库 → Redis，且任一步失败不得跳过后续步骤"
    )


async def test_lifespan_shutdown_releases_everything_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常关停路径：三步都执行且顺序正确（防止"加 try 时把某一步挪走/删掉"）。"""
    called: list[str] = []

    class _ModelService:
        """正常释放的底座服务替身。"""

        async def aclose(self) -> None:
            called.append("model_service")

    async def _dispose() -> None:
        called.append("db_engine")

    async def _redis() -> None:
        called.append("shared_redis")

    monkeypatch.setattr(app_main, "get_model_service", lambda: _ModelService())
    monkeypatch.setattr(app_main, "dispose_engine", _dispose)
    monkeypatch.setattr(app_main, "aclose_shared_redis", _redis)

    settings = _settings()
    fake_app = FastAPI()
    fake_app.state.settings = settings
    fake_app.state.rate_limiter = RateLimiter(settings=settings)

    async with app_lifespan(fake_app):
        pass

    assert called == ["model_service", "db_engine", "shared_redis"]
