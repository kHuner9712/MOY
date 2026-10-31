"""role 仓储 PG 覆盖补测（审计 2026-10-31 M4）。

`role/postgres.py` 的 PG 实现此前无独立用例（仅散见 `test_org_endpoints.py`）。
本文件覆盖内置角色播种、角色分配/读取、权限点查询与租户隔离。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.role.postgres import PostgresRoleStore
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

TENANT_ID = f"tnt_pg_role_{uuid.uuid4().hex[:8]}"
OTHER_TENANT = f"tnt_pg_role_other_{uuid.uuid4().hex[:8]}"


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
async def store(pg_settings: Settings) -> AsyncIterator[PostgresRoleStore]:
    instance = PostgresRoleStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    async with platform_scope(settings=settings) as uow:
        await uow.execute(
            "DELETE FROM member_role WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )
        await uow.execute(
            "DELETE FROM role WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )


class TestBuiltinRoles:
    async def test_ensure_builtin_roles_seeds_four(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        assert len(roles) == 4
        keys = {r.role_key for r in roles}
        assert {"owner", "org_admin", "member", "guest"} <= keys

    async def test_ensure_builtin_roles_idempotent(self, store: PostgresRoleStore) -> None:
        first = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        second = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        assert len(first) == len(second)
        assert {r.role_id for r in first} == {r.role_id for r in second}


class TestRoleAssignment:
    async def test_assign_and_read_role(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        member_role = next(r for r in roles if r.role_key == "member")
        await store.assign_role(TENANT_ID, "org_1", "usr_r1", member_role.role_id)
        role_key = await store.role_key_of_user(TENANT_ID, "usr_r1")
        assert role_key == "member"

    async def test_assign_overwrites(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        by_key = {r.role_key: r.role_id for r in roles}
        await store.assign_role(TENANT_ID, "org_1", "usr_r2", by_key["member"])
        await store.assign_role(TENANT_ID, "org_1", "usr_r2", by_key["org_admin"])
        assert await store.role_key_of_user(TENANT_ID, "usr_r2") == "org_admin"

    async def test_member_ids_of_role(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        member_role = next(r for r in roles if r.role_key == "member")
        await store.assign_role(TENANT_ID, "org_1", "usr_m1", member_role.role_id)
        await store.assign_role(TENANT_ID, "org_1", "usr_m2", member_role.role_id)
        members = await store.member_ids_of_role(TENANT_ID, member_role.role_id)
        assert set(members) >= {"usr_m1", "usr_m2"}

    async def test_count_members(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        member_role = next(r for r in roles if r.role_key == "member")
        await store.assign_role(TENANT_ID, "org_1", "usr_c1", member_role.role_id)
        count = await store.count_members(TENANT_ID, member_role.role_id)
        assert count >= 1

    async def test_role_ids_of_user(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        member_role = next(r for r in roles if r.role_key == "member")
        await store.assign_role(TENANT_ID, "org_1", "usr_rids", member_role.role_id)
        ids = await store.role_ids_of_user(TENANT_ID, "usr_rids")
        assert len(ids) >= 1


class TestTenantIsolation:
    async def test_cross_tenant_isolation(self, store: PostgresRoleStore) -> None:
        roles = await store.ensure_builtin_roles(TENANT_ID, "org_1")
        member_role = next(r for r in roles if r.role_key == "member")
        await store.assign_role(TENANT_ID, "org_1", "usr_iso", member_role.role_id)
        # 另一租户看不到
        assert await store.role_key_of_user(OTHER_TENANT, "usr_iso") == ""
        other_members = await store.member_ids_of_role(OTHER_TENANT, member_role.role_id)
        assert "usr_iso" not in other_members
