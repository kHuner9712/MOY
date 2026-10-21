"""操作记录「只增不改」（真实 PostgreSQL，阶段二 B01 第 9 条）。

判据来源：`deploy/init-app-role.sql` 第 4 节对应用角色做了
`REVOKE UPDATE, DELETE ON audit_log`；架构文档与 PRD 均要求「操作记录不可篡改」。

主理人手工未覆盖这一条：他只验证了 RLS 隔离与迁移幂等。
本文件在真实库上证明应用角色**能写入但改写不了**——
若权限没收住，审计就成了可擦拭的记录，合规口径直接失效。

清理说明：应用角色**无法删除**操作记录（这正是被测行为），
因此清理由超级用户引擎代劳；哨兵行自带 uuid 后缀，
即使中途异常退出留下残行也不会让下一次执行误判行数。

夹具来源：`tests/integration/pg_fixture.py`（独立文件，不改 conftest.py）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import tenant_scope

# 夹具由独立文件提供：避免与 B04 批次的 conftest 改动互相踩踏
pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 哨兵操作记录的动作名前缀（便于清理与识别）
SENTINEL_ACTION_PREFIX = "qa_b01_audit_probe"

_INSERT_AUDIT = (
    "INSERT INTO audit_log (tenant_id, actor_id, action, resource_type, resource_id, result) "
    "VALUES (:tid, :actor, :action, 'knowledge_base', :rid, 'success')"
)


def _fresh_probe(label: str) -> tuple[str, str, str]:
    """生成一次探针用的租户 ID / 动作名 / 资源 ID（带 uuid，互不干扰）。

    Args:
        label: 用例标签。

    Returns:
        tuple[str, str, str]: 租户 ID、动作名、资源 ID。
    """
    suffix = uuid.uuid4().hex[:8]
    return (
        f"tnt_b01_audit_{label}_{suffix}",
        f"{SENTINEL_ACTION_PREFIX}_{label}_{suffix}",
        f"kb_b01_audit_{suffix}",
    )


async def _purge_sentinel_rows(engine: Any) -> None:
    """用超级用户清掉全部哨兵行（应用角色删不掉，这是被测行为本身）。

    Args:
        engine: 超级用户引擎。
    """
    async with engine.connect() as connection:
        await connection.execute(
            sql_text("DELETE FROM audit_log WHERE action LIKE :prefix"),
            {"prefix": f"{SENTINEL_ACTION_PREFIX}_%"},
        )
        await connection.commit()


async def _insert_one(factory: Any, tenant_id: str, action: str, resource_id: str) -> None:
    """以租户作用域写入一条哨兵操作记录。

    Args:
        factory: 会话工厂。
        tenant_id: 租户 ID。
        action: 动作名。
        resource_id: 资源 ID。
    """
    async with tenant_scope(tenant_id, session_factory=factory) as uow:
        await uow.execute(
            _INSERT_AUDIT,
            {
                "tid": tenant_id,
                "actor": "usr_b01_audit_probe",
                "action": action,
                "rid": resource_id,
            },
        )


async def test_audit_log_insert_is_allowed_for_app_role(
    b01_engine: Any,
    b01_superuser_engine: Any,
) -> None:
    """应用角色可以写操作记录（否则审计功能本身就废了）。"""
    tenant_id, action, resource_id = _fresh_probe("ins")
    factory = async_sessionmaker(bind=b01_engine, expire_on_commit=False, autoflush=False)
    try:
        await _insert_one(factory, tenant_id, action, resource_id)

        async with b01_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    sql_text("SELECT set_config('app.tenant_id', :tid, TRUE)"),
                    {"tid": tenant_id},
                )
                rows = (
                    await connection.execute(
                        sql_text(
                            "SELECT count(*) FROM audit_log WHERE tenant_id = :tid "
                            "AND action = :action"
                        ),
                        {"tid": tenant_id, "action": action},
                    )
                ).scalar()
    finally:
        await _purge_sentinel_rows(b01_superuser_engine)

    assert rows == 1, f"应用角色写入操作记录失败，查到 {rows} 行"


async def test_audit_log_update_is_denied_for_app_role(
    b01_engine: Any,
    b01_superuser_engine: Any,
) -> None:
    """应用角色改写操作记录必须被拒绝（先写入，再尝试擦改）。"""
    tenant_id, action, resource_id = _fresh_probe("upd")
    factory = async_sessionmaker(bind=b01_engine, expire_on_commit=False, autoflush=False)
    try:
        await _insert_one(factory, tenant_id, action, resource_id)

        async with tenant_scope(tenant_id, session_factory=factory) as uow:
            with pytest.raises(Exception) as excinfo:
                await uow.execute(
                    "UPDATE audit_log SET result = 'success_rewritten' "
                    "WHERE tenant_id = :tid AND action = :action",
                    {"tid": tenant_id, "action": action},
                )
    finally:
        await _purge_sentinel_rows(b01_superuser_engine)

    assert "permission denied" in str(excinfo.value).lower(), (
        f"操作记录改写未被权限拦住，实际异常：{excinfo.value!r}"
    )


async def test_audit_log_delete_is_denied_for_app_role(
    b01_engine: Any,
    b01_superuser_engine: Any,
) -> None:
    """应用角色删除操作记录必须被拒绝。"""
    tenant_id, action, resource_id = _fresh_probe("del")
    factory = async_sessionmaker(bind=b01_engine, expire_on_commit=False, autoflush=False)
    try:
        await _insert_one(factory, tenant_id, action, resource_id)

        async with tenant_scope(tenant_id, session_factory=factory) as uow:
            with pytest.raises(Exception) as excinfo:
                await uow.execute(
                    "DELETE FROM audit_log WHERE tenant_id = :tid AND action = :action",
                    {"tid": tenant_id, "action": action},
                )
    finally:
        await _purge_sentinel_rows(b01_superuser_engine)

    assert "permission denied" in str(excinfo.value).lower(), (
        f"操作记录删除未被权限拦住，实际异常：{excinfo.value!r}"
    )


async def test_audit_privileges_are_granted_as_expected(b01_engine: Any) -> None:
    """权限矩阵自检：应用角色对 `audit_log` 只有 INSERT/SELECT，没有 UPDATE/DELETE。

    直接查 `information_schema.role_table_grants` 而不是靠"试一下报错"，
    这样即使某天有人误发 `GRANT` 也会第一时间红。
    """
    async with b01_engine.connect() as connection:
        rows = (
            await connection.execute(
                sql_text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = current_user AND table_name = 'audit_log'"
                )
            )
        ).scalars()

    privileges = {str(item) for item in rows}
    assert {"INSERT", "SELECT"} <= privileges, f"应用角色缺少审计读写权限：{sorted(privileges)}"
    assert "UPDATE" not in privileges, "应用角色被授予了 audit_log 的 UPDATE 权限"
    assert "DELETE" not in privileges, "应用角色被授予了 audit_log 的 DELETE 权限"
