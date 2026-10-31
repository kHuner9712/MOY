"""org 仓储 PG 覆盖补测（审计 2026-10-31 M4）。

`org/postgres.py` 的 PG 实现此前无独立用例（仅散见 `test_org_endpoints.py`）。
本文件覆盖部门 CRUD、成员关联、批量关联查询（`links_of_users`）与租户隔离。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.org import DepartmentRecord, MemberLink
from app.db.repositories.org.postgres import PostgresOrgStore
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

TENANT_ID = f"tnt_pg_org_{uuid.uuid4().hex[:8]}"
OTHER_TENANT = f"tnt_pg_org_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-test-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresOrgStore]:
    instance = PostgresOrgStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    async with platform_scope(settings=settings) as uow:
        await uow.execute(
            "DELETE FROM department_member WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )
        await uow.execute(
            "DELETE FROM department WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )


def _dept(tenant: str, dept_id: str, *, org: str = "org_1") -> DepartmentRecord:
    return DepartmentRecord(
        tenant_id=tenant,
        department_id=dept_id,
        org_id=org,
        parent_id="",
        name=f"部门-{dept_id}",
        path=f"/{dept_id}/",
        level=1,
    )


def _link(tenant: str, dept_id: str, user_id: str, *, primary: bool = True) -> MemberLink:
    return MemberLink(
        tenant_id=tenant,
        department_id=dept_id,
        user_id=user_id,
        is_primary=primary,
    )


class TestDepartmentCrud:
    async def test_create_and_get_department(self, store: PostgresOrgStore) -> None:
        created = await store.create_department(_dept(TENANT_ID, "dept_a"))
        assert created.department_id == "dept_a"
        fetched = await store.get_department(TENANT_ID, "dept_a")
        assert fetched is not None
        assert fetched.name == "部门-dept_a"

    async def test_update_department(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_b"))
        updated = await store.update_department(TENANT_ID, "dept_b", name="改名后")
        assert updated is not None
        assert updated.name == "改名后"

    async def test_soft_delete_department(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_c"))
        deleted = await store.soft_delete_department(TENANT_ID, "dept_c")
        assert deleted is True
        fetched = await store.get_department(TENANT_ID, "dept_c")
        assert fetched is None

    async def test_list_departments_excludes_deleted(
        self, store: PostgresOrgStore
    ) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_live"))
        await store.create_department(_dept(TENANT_ID, "dept_dead"))
        await store.soft_delete_department(TENANT_ID, "dept_dead")
        departments = await store.list_departments(TENANT_ID, "org_1")
        ids = [d.department_id for d in departments]
        assert "dept_live" in ids
        assert "dept_dead" not in ids


class TestMemberLinks:
    async def test_link_and_unlink_member(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_m"))
        await store.link_member(_link(TENANT_ID, "dept_m", "usr_1"))
        links = await store.links_of_user(TENANT_ID, "usr_1")
        assert len(links) == 1
        assert links[0].department_id == "dept_m"
        removed = await store.unlink_member(TENANT_ID, "dept_m", "usr_1")
        assert removed is True
        assert await store.links_of_user(TENANT_ID, "usr_1") == []

    async def test_links_of_user_sorted_primary_first(
        self, store: PostgresOrgStore
    ) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_x"))
        await store.create_department(_dept(TENANT_ID, "dept_y"))
        await store.link_member(_link(TENANT_ID, "dept_x", "usr_s", primary=False))
        await store.link_member(_link(TENANT_ID, "dept_y", "usr_s", primary=True))
        links = await store.links_of_user(TENANT_ID, "usr_s")
        assert links[0].is_primary is True

    async def test_links_of_users_batch(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_batch"))
        await store.link_member(_link(TENANT_ID, "dept_batch", "usr_b1"))
        await store.link_member(_link(TENANT_ID, "dept_batch", "usr_b2"))
        result = await store.links_of_users(TENANT_ID, ["usr_b1", "usr_b2", "usr_none"])
        assert set(result.keys()) == {"usr_b1", "usr_b2"}
        assert len(result["usr_b1"]) == 1
        assert len(result["usr_b2"]) == 1

    async def test_links_of_users_empty_list(self, store: PostgresOrgStore) -> None:
        result = await store.links_of_users(TENANT_ID, [])
        assert result == {}

    async def test_user_ids(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_u"))
        await store.link_member(_link(TENANT_ID, "dept_u", "usr_u1"))
        await store.link_member(_link(TENANT_ID, "dept_u", "usr_u2"))
        users = await store.user_ids(TENANT_ID, "org_1")
        assert set(users) >= {"usr_u1", "usr_u2"}


class TestTenantIsolation:
    async def test_cross_tenant_isolation(self, store: PostgresOrgStore) -> None:
        await store.create_department(_dept(TENANT_ID, "dept_iso"))
        await store.link_member(_link(TENANT_ID, "dept_iso", "usr_iso"))
        # 另一租户看不到
        assert await store.get_department(OTHER_TENANT, "dept_iso") is None
        assert await store.links_of_user(OTHER_TENANT, "usr_iso") == []
        result = await store.links_of_users(OTHER_TENANT, ["usr_iso"])
        assert result == {}
