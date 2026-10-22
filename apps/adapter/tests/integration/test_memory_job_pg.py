"""记忆与任务仓库的真库验收（阶段四 T4-03.2 / T4-03.3，架构 §3.3 / §3.4）。

**为什么必须连真库**：本提交的仓储层几乎全是 SQL——`FOR UPDATE SKIP LOCKED` 认领、
租约回收、`ON CONFLICT DO NOTHING` 去重、`superseded_by` 更新、`CAST(:embedding AS vector)`。
内存替身一条都验不到，而"SQL 只在生产被验证"是本项目反复踩过的坑
（阶段二 B21c 的 `timestamptz` 参数类型问题即此类）。

五条判据：

1. **同 kind 去重**：`uq_job_dedupe` 生效，重复入队返回 `None`（不是抛错）；
2. **认领**：`status='running'` + `locked_by` + `locked_until` + `attempts+1`；
3. **租约回收**：把 `locked_until` 改到过去，下一次认领能拿回同一行（`attempts=2`）；
4. **失败收口**：`max_attempts=1` 时 `mark_failed` 直接置 `failed` 并留 `last_error`；
5. **记忆生命周期**：插入 / 同键查询 / 合并 / `superseded_by` / 硬删（含向量行）。

⚠ 用**应用角色**（`NOSUPERUSER NOBYPASSRLS`）连库，否则 RLS 断言全是假绿。
"""

# ruff: noqa: S608 - 表名来自本文件常量（非外部输入），值走参数绑定
from __future__ import annotations

import asyncpg
import pytest

from app.core.config import Settings
from app.db.engine import dispose_engine
from app.db.repositories.job_repo import JobRecord, PostgresJobStore
from app.db.repositories.memory_repo import (
    MemoryRecord,
    MemorySettingRecord,
    PostgresMemoryStore,
    new_memory_id,
)
from app.services.memory_extractor import normalized_hash

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT = "tnt_mem_job"
OTHER_TENANT = "tnt_mem_job_2"
USER = "usr_mem_job"

#: 本文件会写入的表（清理用；与 `DELETE ... WHERE tenant_id LIKE 'tnt_mem_job%'` 配套）
TABLES = ("background_job", "user_memory", "memory_embedding", "user_memory_setting")


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


async def _cleanup(dsn: str) -> None:
    """清理本文件写入的行。

    Args:
        dsn: 连接串。
    """
    for table in TABLES:
        await _platform_execute(dsn, f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_mem_job%'")


@pytest.fixture(autouse=True)
async def _clean(pg_dsn: str) -> None:
    """每个用例前后清理。

    Args:
        pg_dsn: 测试库连接串。
    """
    await _cleanup(pg_dsn)
    yield
    await _cleanup(pg_dsn)


@pytest.fixture(autouse=True)
async def _release_engine() -> None:
    """用例**前后**都释放进程内引擎。

    `get_engine()` 是进程级缓存，而 pytest-asyncio 给每个用例一个新事件循环；
    旧循环里建的 asyncpg 连接留在池里，下一个用例取到就是
    `RuntimeError: Event loop is closed`（表现为"单独跑绿、一起跑红"）。
    既有写法（`test_conversation_rls_pg.py`）只在用例后释放；这里**前置也释放**，
    因为本文件可能被排在"某个建了引擎但没释放"的文件之后（`-k`/重排/xdist 都会触发）。

    Yields:
        None: 用例运行期间。
    """
    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """指向测试库的配置实例。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: `business_store=postgres` 的配置。
    """
    return Settings(env="test", business_store="postgres", database_url=pg_dsn)


class TestJobQueue:
    """后台任务队列的 SQL 语义。"""

    async def test_enqueue_dedupes_claim_and_succeed(
        self, pg_dsn: str, pg_settings: Settings
    ) -> None:
        """同 kind 去重 + 认领 + 成功终结。"""
        store = PostgresJobStore(pg_settings)

        first = await store.enqueue(
            JobRecord(
                tenant_id=TENANT,
                kind="memory_extract",
                dedupe_key="conv_pg_1",
                payload={"conversation_id": "conv_pg_1", "user_id": USER},
                max_attempts=1,
            )
        )
        assert first is not None
        # 去重命中**返回 None**（不是抛 IntegrityError：入队冲突不是错误）
        second = await store.enqueue(
            JobRecord(tenant_id=TENANT, kind="memory_extract", dedupe_key="conv_pg_1")
        )
        assert second is None
        # 不同租户不算重复
        other = await store.enqueue(
            JobRecord(tenant_id=OTHER_TENANT, kind="memory_extract", dedupe_key="conv_pg_1")
        )
        assert other is not None

        claimed = await store.claim(worker_id="w-test", limit=10, lease_seconds=60)
        mine = [item for item in claimed if item.tenant_id == TENANT]
        assert len(mine) == 1
        assert mine[0].status == "running"
        assert mine[0].locked_by == "w-test"
        assert mine[0].locked_until
        assert mine[0].attempts == 1
        assert mine[0].payload["conversation_id"] == "conv_pg_1"

        await store.mark_succeeded(mine[0])
        done = await store.get(TENANT, mine[0].job_id)
        assert done is not None
        assert done.status == "succeeded"
        assert done.locked_until == ""
        assert done.finished_at

        # 终结后去重键释放，可再次入队（下一个 10 轮周期还要用）
        again = await store.enqueue(
            JobRecord(tenant_id=TENANT, kind="memory_extract", dedupe_key="conv_pg_1")
        )
        assert again is not None

    async def test_expired_lease_is_reclaimed(self, pg_dsn: str, pg_settings: Settings) -> None:
        """崩溃留下的 `running` 行在租约过期后被回收（attempts 累加）。"""
        store = PostgresJobStore(pg_settings)
        job = await store.enqueue(
            JobRecord(
                tenant_id=TENANT,
                kind="memory_extract",
                dedupe_key="conv_pg_lease",
                max_attempts=3,
            )
        )
        assert job is not None
        first = await store.claim(worker_id="dead-worker", limit=1, lease_seconds=60)
        assert [item.job_id for item in first] == [job.job_id]

        # 租约未过期 → 认领不到
        assert await store.claim(worker_id="w2", limit=10, lease_seconds=60) == []

        # 把租约改到过去（模拟进程被 kill 后时间流逝）
        await _platform_execute(
            pg_dsn,
            "UPDATE background_job SET locked_until = now() - interval '5 minutes' "
            f"WHERE tenant_id = '{TENANT}' AND job_id = '{job.job_id}'",
        )
        reclaimed = await store.claim(worker_id="w2", limit=10, lease_seconds=60)
        assert [item.job_id for item in reclaimed] == [job.job_id]
        assert reclaimed[0].attempts == 2
        assert reclaimed[0].locked_by == "w2"

    async def test_failure_is_terminal_when_attempts_exhausted(self, pg_settings: Settings) -> None:
        """`max_attempts=1` → 失败即终结，`last_error` 有记录。"""
        store = PostgresJobStore(pg_settings)
        job = await store.enqueue(
            JobRecord(
                tenant_id=TENANT,
                kind="memory_extract",
                dedupe_key="conv_pg_fail",
                max_attempts=1,
            )
        )
        assert job is not None
        claimed = await store.claim(worker_id="w-test", limit=10, lease_seconds=60)
        target = next(item for item in claimed if item.job_id == job.job_id)

        status = await store.mark_failed(target, error="ChatCompletionError")
        assert status == "failed"
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.last_error == "ChatCompletionError"
        assert stored.finished_at
        # 已终结 → 不再被认领
        assert await store.claim(worker_id="w-test", limit=10, lease_seconds=60) == []


class TestMemoryRepository:
    """记忆事实的 SQL 语义（列名、唯一索引、向量列）。"""

    async def test_fact_lifecycle(self, pg_settings: Settings) -> None:
        """插入 → 同键查询 → 合并 → 取代 → 硬删。"""
        store = PostgresMemoryStore(pg_settings)
        digest = normalized_hash("preference", "汇报要表格式")
        created = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式",
                confidence="high",
                source_conversation_id="conv_pg_1",
                normalized_hash=digest,
            )
        )
        assert created is not None
        assert created.hit_count == 1

        found = await store.find_by_hash(TENANT, USER, "preference", digest)
        assert found is not None
        assert found.memory_id == created.memory_id

        # 同 hash 再插 → 唯一索引挡住（返回 None，由服务层退化为合并）
        duplicate = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式",
                normalized_hash=digest,
            )
        )
        assert duplicate is None

        await store.touch(found, hit_count=2, confidence="high")
        touched = await store.get(TENANT, created.memory_id)
        assert touched is not None
        assert touched.hit_count == 2

        # 新值取代旧值
        replacement = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式的",
                normalized_hash=normalized_hash("preference", "汇报要表格式的"),
            )
        )
        assert replacement is not None
        await store.supersede(TENANT, created.memory_id, replacement.memory_id)

        old = await store.get(TENANT, created.memory_id)
        assert old is not None
        assert old.superseded_by == replacement.memory_id
        active = await store.list_active(TENANT, USER, category="preference")
        assert [item.memory_id for item in active] == [replacement.memory_id]
        assert await store.count_active(TENANT, USER) == 1

        # 硬删（含向量行）
        await store.upsert_embedding(
            TENANT,
            replacement.memory_id,
            model="fake",
            dim=1024,
            embedding=[0.0] * 1024,
        )
        vectors = await store.list_embeddings(TENANT, [replacement.memory_id])
        assert len(vectors[replacement.memory_id]) == 1024
        removed = await store.delete(TENANT, USER, [replacement.memory_id, created.memory_id])
        assert removed == 2
        assert await store.list_embeddings(TENANT, [replacement.memory_id]) == {}
        assert await store.count_active(TENANT, USER) == 0

    async def test_personal_boundary_cannot_read_other_user(self, pg_settings: Settings) -> None:
        """个人边界：同租户的另一个用户读不到（RLS 只管租户，这里是仓储层的职责）。"""
        store = PostgresMemoryStore(pg_settings)
        await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="identity",
                content="我叫赵工",
                normalized_hash=normalized_hash("identity", "我叫赵工"),
            )
        )
        assert await store.count_active(TENANT, "usr_other") == 0
        assert await store.list_active(TENANT, "usr_other") == []

    async def test_setting_default_and_upsert(self, pg_settings: Settings) -> None:
        """无行 = 全开；写入后能读回（`category_switches` 是 JSONB）。"""
        store = PostgresMemoryStore(pg_settings)
        default = await store.get_setting(TENANT, USER)
        assert default.enabled is True
        assert default.category_switches == {}

        saved = await store.upsert_setting(
            MemorySettingRecord(
                tenant_id=TENANT,
                user_id=USER,
                enabled=True,
                category_switches={"preference": False},
            )
        )
        assert saved.enabled is True
        reloaded = await store.get_setting(TENANT, USER)
        assert reloaded.enabled is True
        assert reloaded.category_enabled("preference") is False  # 分类开关只关这一类
        assert reloaded.category_enabled("identity") is True

        # 关全局 → 所有分类一律禁止（"关了就偷偷还用"是明确不允许的设计）
        await store.upsert_setting(
            MemorySettingRecord(
                tenant_id=TENANT,
                user_id=USER,
                enabled=False,
                category_switches={"preference": False},
            )
        )
        closed = await store.get_setting(TENANT, USER)
        assert closed.enabled is False
        assert closed.category_enabled("identity") is False
