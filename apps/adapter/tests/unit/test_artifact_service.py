"""产物查询入口的直接用例（H-03，2026-11-07）。

`api/v1/artifact_files.py` 通过 `business/artifact_service.fetch_artifact`
取产物记录，租户来自**已验签的下载令牌**。审计按模块名检索判为"零测试"，
实际它被下载链路的集成用例覆盖；本文件补充这层委派的两个硬约束：

1. 查询必须带令牌里的租户，不能让请求方自由指定（跨租户读取防线）；
2. 后端实例来自传入的 Settings（测试/生产选择不同存储），不是进程默认单例。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.business import artifact_service
from app.core.config import Settings


@dataclass
class _Record:
    """最小产物记录替身（只关心取回与租户隔离）。"""

    tenant_id: str
    artifact_id: str
    content: bytes = b"payload"


class _SpyStore:
    """记录 `get()` 入参的存储替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.items: dict[tuple[str, str], _Record] = {}

    async def get(self, tenant_id: str, artifact_id: str) -> _Record | None:
        self.calls.append((tenant_id, artifact_id))
        return self.items.get((tenant_id, artifact_id))


@dataclass
class _SpyFactory:
    """替换 `get_artifact_store`，同时记录传入的 Settings。"""

    store: _SpyStore
    seen_settings: list[Settings]

    def __call__(self, settings: Settings) -> _SpyStore:
        self.seen_settings.append(settings)
        return self.store


def _settings() -> Settings:
    """构造测试配置。

    Returns:
        Settings: 配置。
    """
    return Settings(env="test", business_store="memory")


async def test_fetch_artifact_passes_token_tenant_and_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按令牌租户 + 产物 ID 精确查询，命中时原样返回记录。"""
    store = _SpyStore()
    record = _Record(tenant_id="tnt_a", artifact_id="art_001")
    store.items[("tnt_a", "art_001")] = record
    factory = _SpyFactory(store=store, seen_settings=[])
    monkeypatch.setattr(artifact_service, "get_artifact_store", factory)

    found = await artifact_service.fetch_artifact(_settings(), "tnt_a", "art_001")

    assert found is record
    assert store.calls == [("tnt_a", "art_001")]
    # 存储实例按传入 Settings 组装，而不是进程默认单例
    assert len(factory.seen_settings) == 1
    assert factory.seen_settings[0].business_store == "memory"


async def test_fetch_artifact_is_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名产物 ID 在别的租户下取不到（跨租户返回 None，不是他人记录）。"""
    store = _SpyStore()
    store.items[("tnt_a", "art_001")] = _Record(tenant_id="tnt_a", artifact_id="art_001")
    monkeypatch.setattr(
        artifact_service,
        "get_artifact_store",
        _SpyFactory(store=store, seen_settings=[]),
    )

    missed: Any = await artifact_service.fetch_artifact(_settings(), "tnt_b", "art_001")

    assert missed is None
    assert store.calls == [("tnt_b", "art_001")]


async def test_fetch_artifact_missing_returns_none_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """记录不存在时返回 None（由下载链路按 404 处理），不抛异常。"""
    store = _SpyStore()
    monkeypatch.setattr(
        artifact_service,
        "get_artifact_store",
        _SpyFactory(store=store, seen_settings=[]),
    )

    assert await artifact_service.fetch_artifact(_settings(), "tnt_a", "art_missing") is None
    assert store.calls == [("tnt_a", "art_missing")]
