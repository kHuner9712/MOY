"""Keycloak 单向投影单测（阶段二 B08，架构文档 2.6 / D-13 第 3 条）。

覆盖点：

1. **方向不可逆**：只有「业务库 → KC」的出箱登记，没有任何反向写入路径；
2. **失败不回滚业务**：`enqueue()` 异常只记 WARN 并返回 False；
3. **指数退避**：1s → 8s → 64s，第 8 次仍失败置 `failed`；
4. **投递成功**置 `sent` 且不再被 `due()` 取出。
"""

from __future__ import annotations

import pytest

from app.business.kc_projection import (
    BACKOFF_SECONDS,
    KIND_GROUP_DELETE,
    KIND_GROUP_UPSERT,
    KIND_USER_GROUP_SYNC,
    MAX_ATTEMPTS,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
    InMemoryOutboxStore,
    KcProjectionService,
    NoopProjector,
    OutboxEntry,
    next_attempt_at,
)
from app.core.config import Settings

pytestmark = pytest.mark.unit

TENANT = "tnt_proj"


def _settings() -> Settings:
    """构造测试配置。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )


class _FailingProjector(NoopProjector):
    """始终抛错的投影器（验证退避与最终失败）。"""

    def __init__(self) -> None:
        """初始化计数。"""
        self.calls = 0

    async def upsert_group(self, *, group_id: str, name: str, parent_id: str) -> None:
        """模拟下游不可用。

        Args:
            group_id: 分组 ID。
            name: 分组名称。
            parent_id: 上级分组 ID。

        Raises:
            RuntimeError: 恒定失败。
        """
        self.calls += 1
        raise RuntimeError("kc down")


class _RecordingProjector(NoopProjector):
    """记录调用参数的投影器（验证单向投影载荷）。"""

    def __init__(self) -> None:
        """初始化记录容器。"""
        self.groups: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        self.synced: list[tuple[str, list[str]]] = []

    async def upsert_group(self, *, group_id: str, name: str, parent_id: str) -> None:
        """记录分组投影。

        Args:
            group_id: 分组 ID。
            name: 分组名称。
            parent_id: 上级分组 ID。
        """
        self.groups.append((group_id, name, parent_id))

    async def delete_group(self, group_id: str) -> None:
        """记录分组删除。

        Args:
            group_id: 分组 ID。
        """
        self.deleted.append(group_id)

    async def sync_user_groups(self, *, user_id: str, group_ids: list[str]) -> None:
        """记录成员分组同步。

        Args:
            user_id: 用户 ID。
            group_ids: 分组 ID 列表。
        """
        self.synced.append((user_id, list(group_ids)))


def _service(
    projector: NoopProjector | None = None, store: InMemoryOutboxStore | None = None
) -> tuple[KcProjectionService, InMemoryOutboxStore]:
    """构造使用独立内存出箱的投影服务。

    Args:
        projector: 投影执行器。
        store: 出箱存储。

    Returns:
        tuple: (服务, 出箱存储)。
    """
    outbox = store or InMemoryOutboxStore()
    return KcProjectionService(
        _settings(), store=outbox, projector=projector or NoopProjector()
    ), outbox


async def test_enqueue_group_upsert_creates_pending_entry() -> None:
    """建部门登记 `kc_group_upsert` 出箱记录（pending）。"""
    service, store = _service()
    assert (
        await service.enqueue_group_upsert(
            tenant_id=TENANT, group_id="dept_a", name="研发部", parent_id="dept_root"
        )
        is True
    )
    entries = await store.all_entries(TENANT)
    assert len(entries) == 1
    assert entries[0].kind == KIND_GROUP_UPSERT
    assert entries[0].status == STATUS_PENDING
    assert entries[0].payload == {"group_id": "dept_a", "name": "研发部", "parent_id": "dept_root"}


async def test_enqueue_group_delete_and_user_sync() -> None:
    """删除部门与成员分组同步各自登记对应种类。"""
    service, store = _service()
    await service.enqueue_group_delete(tenant_id=TENANT, group_id="dept_a")
    await service.enqueue_user_groups(tenant_id=TENANT, user_id="usr_1", group_ids=["dept_a"])
    kinds = {entry.kind for entry in await store.all_entries(TENANT)}
    assert kinds == {KIND_GROUP_DELETE, KIND_USER_GROUP_SYNC}


async def test_enqueue_failure_is_swallowed() -> None:
    """出箱不可用时只记录失败，不向调用方抛异常（不得回滚业务事务）。"""

    class _BrokenStore(InMemoryOutboxStore):
        async def enqueue(self, entry: OutboxEntry) -> OutboxEntry:
            raise RuntimeError("outbox down")

    service = KcProjectionService(_settings(), store=_BrokenStore())
    assert (
        await service.enqueue_group_upsert(tenant_id=TENANT, group_id="dept_a", name="研发部")
        is False
    )


async def test_run_once_delivers_and_marks_sent() -> None:
    """投递成功后置 `sent`，且 `due()` 不再取出。"""
    projector = _RecordingProjector()
    service, store = _service(projector)
    await service.enqueue_group_upsert(tenant_id=TENANT, group_id="dept_a", name="研发部")
    await service.enqueue_group_delete(tenant_id=TENANT, group_id="dept_b")
    await service.enqueue_user_groups(tenant_id=TENANT, user_id="usr_1", group_ids=["dept_a"])

    delivered = await service.run_once(TENANT)
    assert delivered == 3
    assert projector.groups == [("dept_a", "研发部", "")]
    assert projector.deleted == ["dept_b"]
    assert projector.synced == [("usr_1", ["dept_a"])]
    assert await store.due(TENANT) == []
    assert {entry.status for entry in await store.all_entries(TENANT)} == {STATUS_SENT}


async def test_run_once_retries_with_backoff() -> None:
    """首次失败不置失败态，而是按退避安排重试。"""
    projector = _FailingProjector()
    service, store = _service(projector)
    await service.enqueue_group_upsert(tenant_id=TENANT, group_id="dept_a", name="研发部")

    delivered = await service.run_once(TENANT)
    assert delivered == 0
    entries = await store.all_entries(TENANT)
    assert entries[0].status == STATUS_PENDING
    assert entries[0].attempts == 1
    assert entries[0].last_error == "RuntimeError"
    assert entries[0].next_attempt_at


async def test_run_once_marks_failed_after_max_attempts() -> None:
    """连续失败到上限后置 `failed` 并停止重试。"""
    projector = _FailingProjector()
    service, store = _service(projector)
    await service.enqueue_group_upsert(tenant_id=TENANT, group_id="dept_a", name="研发部")

    # 直接把退避时间清空，逐次触发投递
    entries = await store.all_entries(TENANT)
    outbox_id = entries[0].outbox_id
    for _ in range(MAX_ATTEMPTS):
        await store.mark_retry(
            TENANT, outbox_id, attempts=entries[0].attempts, next_attempt_at="", error=""
        )
        await service.run_once(TENANT)
        entries = await store.all_entries(TENANT)
        if entries[0].status == STATUS_FAILED:
            break
    assert entries[0].status == STATUS_FAILED


def test_next_attempt_at_follows_backoff_sequence() -> None:
    """退避序列 1s → 8s → 64s，之后固定 64s。"""
    from datetime import UTC, datetime

    base = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
    assert next_attempt_at(1, now=base).startswith("2026-11-01T12:00:01")
    assert next_attempt_at(2, now=base).startswith("2026-11-01T12:00:08")
    assert next_attempt_at(3, now=base).startswith("2026-11-01T12:01:04")
    assert next_attempt_at(9, now=base).startswith("2026-11-01T12:01:04")
    assert BACKOFF_SECONDS == (1, 8, 64)


async def test_service_never_reads_from_keycloak() -> None:
    """D-13：投影服务只有写方向（`enqueue_*`）与投递，无任何读取业务数据的路径。"""
    service, _store = _service()
    public = {name for name in dir(service) if not name.startswith("_")}
    assert public == {
        "enqueue_group_upsert",
        "enqueue_group_delete",
        "enqueue_user_groups",
        "run_once",
        "store",
    }
