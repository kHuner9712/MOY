"""记忆端点 MM1-MM4 的真库验收（阶段四 T4-03.5，架构 §4.2 / PRD §4.4）。

**为什么必须连真库**：MM2/MM3 的核心约束是"删除必须是硬删，且 `memory_embedding`
里同 `memory_id` 的行同时消失"——只有真库能证明"同一事务里两表都删掉了"。
内存替身删了就是删了，看不出事务边界。

六条判据：

1. **MM1 分页 + 类别筛选 + 同包 `enabled`**（前端三态空态靠它区分）；
2. **MM2 硬删**：接口返回 `deleted=true`，且 `memory_embedding` 同 ID 行数为 0；
3. **MM2 不存在 → 18002**（不静默 200）；
4. **MM3 按类删 / 全删**：只删指定类别，另一类不动；
5. **MM4 开关**：写入后 MM1 的 `enabled` 随之变化，分类开关只覆盖传入的键；
6. **审计只记动作与条数**：`change_summary` 里搜不到任何记忆正文。
"""

# ruff: noqa: S608 - 表名来自本文件常量（非外部输入），值走参数绑定
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.business.audit_service import get_audit_recorder
from app.core.config import Settings
from app.db.engine import dispose_engine
from app.db.repositories.memory_repo import (
    MemoryRecord,
    get_memory_store,
    new_memory_id,
)
from app.main import create_app
from app.services.memory_extractor import normalized_hash
from tests.conftest import make_token

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT = "tnt_mem_api"
USER = "usr_mem_api"
FACT_TEXT = "汇报要表格式"

#: `credential_encryption_key`：postgres 模式下启动校验要求它存在（32 字节 base64）
_CRED_KEY = base64.b64encode(b"0" * 32).decode()


async def _platform_execute(dsn: str, sql: str) -> None:
    """以平台模式执行一条管理语句（跨租户清理用）。

    Args:
        dsn: 连接串。
        sql: 语句。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute(sql)
    finally:
        await connection.close()


async def _count_embeddings(dsn: str, memory_id: str) -> int:
    """统计某条记忆的向量行数（判据 2 的核心断言）。

    Args:
        dsn: 连接串。
        memory_id: 记忆 ID。

    Returns:
        int: 行数。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM memory_embedding WHERE memory_id = $1", memory_id
            )
        )
    finally:
        await connection.close()


def _bind(pg_dsn: str) -> None:
    """把记忆存储与审计记录器绑定到真库（都是进程级单例）。

    Args:
        pg_dsn: 测试库连接串。
    """
    conf = _settings(pg_dsn)
    get_memory_store(conf)
    get_audit_recorder(conf)


@pytest.fixture(autouse=True)
async def _clean(pg_dsn: str) -> AsyncIterator[None]:
    """每个用例前后清理本文件写入的行，并释放引擎。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        None: 用例运行期间。
    """
    for table in ("memory_embedding", "user_memory", "user_memory_setting", "background_job"):
        await _platform_execute(dsn=pg_dsn, sql=f"DELETE FROM {table} WHERE tenant_id = '{TENANT}'")
    await dispose_engine()
    # 把两个进程级单例绑定到真库（conftest 每个用例前刚重置过它们）
    _bind(pg_dsn)
    yield
    for table in ("memory_embedding", "user_memory", "user_memory_setting", "background_job"):
        await _platform_execute(dsn=pg_dsn, sql=f"DELETE FROM {table} WHERE tenant_id = '{TENANT}'")
    await dispose_engine()


def _settings(pg_dsn: str) -> Settings:
    """构造指向测试库的应用配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="saas",
        business_store="postgres",
        database_url=pg_dsn,
        auth_mode="stub",
        ratelimit_enabled=False,
        credential_encryption_key=_CRED_KEY,
        downstream_enabled=False,
    )


@pytest.fixture
async def api(pg_dsn: str) -> AsyncIterator[AsyncClient]:
    """指向真库的异步客户端（**不跑 lifespan**：这里不需要迁移与轮询）。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        AsyncIterator[AsyncClient]: 客户端。
    """
    _bind(pg_dsn)
    app = create_app(_settings(pg_dsn))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _headers(tag: str = "", *, path: str = "POST") -> dict[str, str]:
    """构造鉴权 + 幂等头。

    Args:
        tag: 幂等键标签（空表示不带幂等键）。
        path: 业务路径（参与幂等指纹，避免跨端点撞键）。

    Returns:
        dict[str, str]: 请求头。
    """
    conf = Settings(env="test", jwt_secret="unit-test-secret")
    token = make_token(settings=conf, tenant_id=TENANT, user_id=USER)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}
    if tag:
        digest = normalized_hash(tag, f"{path}:{tag}")[:32]
        key = f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
        headers["Idempotency-Key"] = key
    return headers


async def _seed(
    *facts: tuple[str, str], vectors: Sequence[Sequence[float]] | None = None
) -> list[MemoryRecord]:
    """写入若干记忆（可选写向量）。

    存储取自**进程级单例**，由 `_clean` 夹具在用例开始时绑定到真库
    （`get_memory_store()` 不传配置会先建成内存实现，接口读到的就不是真库了）。

    Args:
        *facts: `(category, content)` 序列。
        vectors: 与 facts 等长的向量列表。

    Returns:
        list[MemoryRecord]: 落库后的记录。
    """
    store = get_memory_store()
    created: list[MemoryRecord] = []
    for index, (category, content) in enumerate(facts):
        record = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category=category,
                content=content,
                source_conversation_id="conv_api",
                normalized_hash=normalized_hash(category, content),
            )
        )
        assert record is not None
        if vectors is not None and index < len(vectors):
            await store.upsert_embedding(
                TENANT,
                record.memory_id,
                model="fake",
                dim=len(vectors[index]),
                embedding=vectors[index],
            )
        created.append(record)
    return created


class TestMM1List:
    """MM1 列表。"""

    async def test_empty_list_reports_enabled(self, api: AsyncClient) -> None:
        """空列表 + `enabled=true`（前端据此渲染"还没记到东西"而不是"已关闭"）。"""
        response = await api.get("/api/v1/model/memories", headers=_headers())

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
        assert body["data"]["enabled"] is True
        assert body["data"]["meta"]["total"] == 0

    async def test_lists_items_with_required_fields(self, api: AsyncClient) -> None:
        """列表项字段与架构 §4.2 逐字对齐。"""
        await _seed(("preference", FACT_TEXT), ("identity", "我叫赵工"))

        response = await api.get("/api/v1/model/memories", headers=_headers())

        data = response.json()["data"]
        assert data["meta"]["total"] == 2
        item = data["items"][0]
        assert set(item) == {
            "memory_id",
            "category",
            "content",
            "confidence",
            "source_conversation_id",
            "updated_at",
            "superseded_by",
        }

    async def test_category_filter(self, api: AsyncClient) -> None:
        """`category` 只返回该类。"""
        await _seed(("preference", FACT_TEXT), ("identity", "我叫赵工"))

        response = await api.get(
            "/api/v1/model/memories", params={"category": "identity"}, headers=_headers()
        )

        items = response.json()["data"]["items"]
        assert [item["category"] for item in items] == ["identity"]

    async def test_unknown_category_returns_20001(self, api: AsyncClient) -> None:
        """非法类别 → 20001（不静默当成"全部"）。"""
        response = await api.get(
            "/api/v1/model/memories", params={"category": "salary"}, headers=_headers()
        )

        assert response.status_code == 400
        assert response.json()["code"] == 20001


class TestMM2Delete:
    """MM2 单条硬删。"""

    async def test_delete_removes_fact_and_embedding(self, api: AsyncClient, pg_dsn: str) -> None:
        """硬删：事实行与向量行同时消失。"""
        created = await _seed(("preference", FACT_TEXT), vectors=[[0.1] * 1024])
        memory_id = created[0].memory_id
        assert await _count_embeddings(pg_dsn, memory_id) == 1

        response = await api.delete(f"/api/v1/model/memories/{memory_id}", headers=_headers("del"))

        body = response.json()
        assert response.status_code == 200
        assert body["code"] == 0
        assert body["data"] == {"memory_id": memory_id, "deleted": True}
        assert await _count_embeddings(pg_dsn, memory_id) == 0
        listing = await api.get("/api/v1/model/memories", headers=_headers())
        assert listing.json()["data"]["items"] == []

    async def test_delete_unknown_returns_18002(self, api: AsyncClient) -> None:
        """删不存在的记忆 → 404 / 18002（不静默 200）。"""
        response = await api.delete(
            "/api/v1/model/memories/mem_does_not_exist", headers=_headers("del404")
        )

        assert response.status_code == 404
        assert response.json()["code"] == 18002
        assert response.json()["message"] == "这条信息已不存在"

    async def test_delete_is_idempotent_by_key(self, api: AsyncClient) -> None:
        """同一 `Idempotency-Key` 重复删除 → 20007 并回放首次结果。"""
        created = await _seed(("preference", FACT_TEXT))
        path = f"/api/v1/model/memories/{created[0].memory_id}"
        headers = _headers("idem", path=path)

        first = await api.delete(path, headers=headers)
        second = await api.delete(path, headers=headers)

        assert first.json()["code"] == 0
        assert second.json()["code"] == 20007
        assert second.json()["data"] == first.json()["data"]


class TestMM3BatchDelete:
    """MM3 按类删 / 全删。"""

    async def test_delete_by_category_keeps_others(self, api: AsyncClient) -> None:
        """只删指定类别，另一类不动。"""
        await _seed(("preference", FACT_TEXT), ("identity", "我叫赵工"))

        response = await api.delete(
            "/api/v1/model/memories", params={"category": "preference"}, headers=_headers("cat")
        )

        body = response.json()
        assert body["code"] == 0
        assert body["data"] == {"deleted_count": 1, "category": "preference"}
        listing = await api.get("/api/v1/model/memories", headers=_headers())
        assert [item["category"] for item in listing.json()["data"]["items"]] == ["identity"]

    async def test_delete_all_without_category(self, api: AsyncClient) -> None:
        """不传类别 = 清空全部（含向量行）。"""
        await _seed(("preference", FACT_TEXT), ("identity", "我叫赵工"), vectors=None)

        response = await api.delete("/api/v1/model/memories", headers=_headers("all"))

        assert response.json()["data"] == {"deleted_count": 2, "category": ""}
        listing = await api.get("/api/v1/model/memories", headers=_headers())
        assert listing.json()["data"]["meta"]["total"] == 0


class TestMM4Settings:
    """MM4 开关。"""

    async def test_close_global_switch(self, api: AsyncClient) -> None:
        """关全局后 MM1 的 `enabled=false`（已有条目保留）。"""
        await _seed(("preference", FACT_TEXT))

        response = await api.put(
            "/api/v1/model/memories/settings",
            json={"enabled": False},
            headers=_headers("close"),
        )

        body = response.json()
        assert body["code"] == 0
        assert body["data"]["enabled"] is False
        assert body["data"]["updated_at"]
        listing = await api.get("/api/v1/model/memories", headers=_headers())
        assert listing.json()["data"]["enabled"] is False
        assert listing.json()["data"]["meta"]["total"] == 1  # 已有条目不被清掉

    async def test_category_switches_only_override_given_keys(self, api: AsyncClient) -> None:
        """分类开关是增量覆盖：没传的类别保持原样。"""
        headers = _headers("sw1")
        await api.put(
            "/api/v1/model/memories/settings",
            json={"category_switches": {"preference": False, "org": False}},
            headers=headers,
        )
        response = await api.put(
            "/api/v1/model/memories/settings",
            json={"category_switches": {"preference": True}},
            headers=_headers("sw2"),
        )

        switches = response.json()["data"]["category_switches"]
        assert switches == {"preference": True, "org": False}

    async def test_empty_settings_body_returns_20001(self, api: AsyncClient) -> None:
        """两项都没传 → 20001。"""
        response = await api.put(
            "/api/v1/model/memories/settings", json={}, headers=_headers("empty")
        )

        assert response.status_code == 400
        assert response.json()["code"] == 20001


class TestAudit:
    """审计：只记动作与条数，不记正文（PRD §4.8）。"""

    async def test_delete_audit_has_no_content(self, api: AsyncClient, pg_dsn: str) -> None:
        """删除后审计条目里搜不到记忆正文。"""
        created = await _seed(("preference", FACT_TEXT))
        memory_id = created[0].memory_id
        await api.delete(f"/api/v1/model/memories/{memory_id}", headers=_headers("audit"))

        recorder = get_audit_recorder(_settings(pg_dsn))
        entries = await recorder.recent(
            TENANT, action="memory.delete", resource_id=memory_id, limit=10
        )

        assert len(entries) == 1
        assert entries[0].change_summary.get("deleted_count") == 1
        assert FACT_TEXT not in json.dumps(entries[0].change_summary, ensure_ascii=False)

    async def test_settings_audit_records_switches_only(
        self, api: AsyncClient, pg_dsn: str
    ) -> None:
        """开关变更审计只留 `enabled` 与关掉的类别名。"""
        await api.put(
            "/api/v1/model/memories/settings",
            json={"enabled": True, "category_switches": {"preference": False}},
            headers=_headers("audit-sw"),
        )

        recorder = get_audit_recorder(_settings(pg_dsn))
        entries: list[Any] = await recorder.recent(
            TENANT, action="memory.settings_update", resource_id="settings", limit=10
        )

        assert entries, "开关变更必须留下操作记录"
        latest = entries[0]
        assert latest.change_summary["enabled"] is True
        assert latest.change_summary["closed_categories"] == ["preference"]
        assert FACT_TEXT not in json.dumps(latest.change_summary, ensure_ascii=False)
