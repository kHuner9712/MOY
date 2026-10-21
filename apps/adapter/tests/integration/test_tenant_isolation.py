"""真实 PostgreSQL 下的多租户隔离与连接权限验证（阶段二 B01）。

对应 `docs/qa-phase2-test-plan.md` §3.1 的 TC-P2-SEC-01/02/03/04/05/06/07。

为什么必须用真实库：`FakeRlsDatabase`（`tests/unit/test_db_session.py`）只能验证
**适配层实现**是否按契约下发 SQL；策略本身是否生效、`FORCE ROW LEVEL SECURITY`
是否真的约束了表所有者、连接池复用下会话变量是否复位，只有真实 PostgreSQL 能证明。

前置条件（缺一不可）：

1. `OA_TEST_DATABASE_URL` 必须指向 **NOSUPERUSER NOBYPASSRLS** 的角色
   （如 `officeai_app`）；未设置时本文件全部 skip。
2. **绝不能用 `officeai`（initdb 建的超级用户）跑本文件**：它 `BYPASSRLS`，
   用它跑所有隔离断言都会"通过"，但那是假阳性。
   夹具 `pg_fixture.assert_rls_not_bypassed` 会直接 `pytest.fail` 拦截。

夹具来源：`tests/integration/pg_fixture.py`（**独立文件，不改 conftest.py**）。
模块级导入的夹具优先级高于 conftest 同名夹具，因此 B04 批次改 conftest 不影响本文件。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import ApiError, ErrorCode
from app.db.engine import reset_engine_for_test
from app.db.session import SET_TENANT_SQL, tenant_scope
from app.db.tenant import PLATFORM_TENANT
from tests.integration.pg_fixture import (
    APP_ROLE,
    BUSINESS_POLICY,
    CATALOG_POLICY,
    fetch_scalar,
    list_policy_tables,
)

# 夹具由独立文件提供：避免与 B04 批次的 conftest 改动互相踩踏
pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: `apps/adapter/app` 源码根目录（SEC-05 静态扫描范围）
APP_SOURCE_ROOT: Path = Path(__file__).resolve().parents[2] / "app"

#: 精确锁定两个哨兵库（不用 LIKE 前缀，避免与真实数据混淆）
_SELECT_SENTINEL_KBS = (
    "SELECT tenant_id, base_id, name FROM knowledge_base_meta WHERE name IN (:name_a, :name_b)"
)

#: 哨兵库行数（不带租户作用域时，业务表必须一律 0 行）
_SELECT_SENTINEL_ANY = "SELECT count(*) FROM knowledge_base_meta WHERE name IN (:name_a, :name_b)"


def _make_factory(engine: Any) -> Any:
    """构造会话工厂。

    Args:
        engine: 异步引擎。

    Returns:
        Any: `async_sessionmaker` 实例。
    """
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def _readyz_body(dsn: str) -> dict[str, Any]:
    """用真实 DSN 启动应用并取一次 `/readyz` 响应体。

    Args:
        dsn: 业务库连接串。

    Returns:
        dict[str, Any]: 响应体的 `data` 部分。
    """
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        env="test",
        deploy_mode="private",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=dsn,
        db_auto_migrate=False,
        credential_encryption_key=base64.b64encode(os.urandom(32)).decode("ascii"),
    )
    application = create_app(settings)
    with TestClient(application) as client:
        response = client.get("/readyz")
    return {"status_code": response.status_code, **response.json()["data"]}


def _check_map(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 `checks` 数组按 name 建索引（**契约要求 checks 必须是数组**）。

    Args:
        body: `/readyz` 响应体 `data`。

    Returns:
        dict[str, dict[str, Any]]: name → 检查项。
    """
    checks = body["checks"]
    assert isinstance(checks, list), f"`checks` 必须是数组，实际为 {type(checks).__name__}"
    return {str(item["name"]): dict(item) for item in checks}


# ===========================================================================
# TC-P2-SEC-01 事务级 set_config（is_local=TRUE）
# ===========================================================================
async def test_sec01_set_config_is_transaction_local(b01_engine: Any) -> None:
    """事务提交后 `current_setting('app.tenant_id', TRUE)` 必须复位。

    这是「连接池串租户」的根因：写成 `FALSE`（会话级）时值会粘在连接上，
    下一个请求（同一连接）会带着上一租户的身份查询。
    """
    async with b01_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(sql_text(SET_TENANT_SQL), {"tenant_id": "tnt_b01_reset"})
            inside = (
                await connection.execute(sql_text("SELECT current_setting('app.tenant_id', TRUE)"))
            ).scalar()
        after = (
            await connection.execute(sql_text("SELECT current_setting('app.tenant_id', TRUE)"))
        ).scalar()

    assert inside == "tnt_b01_reset", "事务内未生效，set_config 参数或语句被改动"
    assert after in ("", None), f"事务级变量未复位，残留值为 {after!r}（疑似写成 FALSE）"


async def test_sec01_platform_scope_does_not_leak_into_next_tenant(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """平台作用域（运营后台）之后接普通租户作用域，普通租户**不得**看到别人家数据。

    这是主理人手工未覆盖的口径：他只验证了「原始 `set_config` 会复位」与
    「普通租户 A→B 不串」，没验证过**平台模式→普通租户**这条最危险的相邻序列。
    一旦 `set_config` 被写成会话级，运营后台查完全量数据后，
    下一个普通租户请求会带着 `__platform__` 看到全库数据。
    """
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]

    # ① 运营后台：平台模式可以读到两个租户（这是设计内行为）
    async with tenant_scope(PLATFORM_TENANT, allow_platform=True, session_factory=factory) as uow:
        platform_rows = await uow.fetch_all(
            _SELECT_SENTINEL_KBS, {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
        )
    assert len(platform_rows) == 2, "平台模式应能读到两个租户的哨兵库"

    # ② 紧接其后的普通租户 B：只能看到自己那一条
    async with tenant_scope(tenant_b["tenant_id"], session_factory=factory) as uow:
        rows_b = await uow.fetch_all(
            _SELECT_SENTINEL_KBS, {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
        )
    assert [row["name"] for row in rows_b] == [tenant_b["name"]], (
        f"平台作用域泄漏到了普通租户：租户 B 看到了 {[row['name'] for row in rows_b]}"
    )


# ===========================================================================
# TC-P2-SEC-02 池=1：同一连接交替服务两个租户
# ===========================================================================
async def test_sec02_pool_one_alternating_tenants_never_leak(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """`pool_size=1` 强制复用同一物理连接，A→B→A→B 四轮全隔离。

    断言四件事：本租户哨兵必现、对端哨兵必不现、两次落在同一后端 PID、
    结果行的 `tenant_id` 恒等于本租户。
    """
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]
    params = {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
    pids: set[int] = set()

    for expected in (tenant_a, tenant_b, tenant_a, tenant_b):
        async with tenant_scope(expected["tenant_id"], session_factory=factory) as uow:
            rows = await uow.fetch_all(_SELECT_SENTINEL_KBS, params)
            pid = await uow.fetch_scalar("SELECT pg_backend_pid()")
        pids.add(int(pid))
        names = {row["name"] for row in rows}
        tenant_ids = {row["tenant_id"] for row in rows}
        assert names == {expected["name"]}, (
            f"租户 {expected['tenant_id']} 的查询结果应为自己的哨兵库，实际 {sorted(names)}"
        )
        assert tenant_ids == {expected["tenant_id"]}

    assert len(pids) == 1, f"四次查询未落在同一物理连接上（PID={sorted(pids)}），池=1 未生效"


async def test_sec02_cross_tenant_update_and_delete_affect_zero_rows(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """跨租户 UPDATE / DELETE 必须只影响 0 行（主理人只手测了 UPDATE）。

    RLS 的 `USING` 表达式对不可见行直接过滤，因此不会报错而是静默 0 行——
    这正是「双保险」应有的行为。
    """
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]

    # 租户 A 试图改写/删除租户 B 的知识库
    async with tenant_scope(tenant_a["tenant_id"], session_factory=factory) as uow:
        updated = await uow.execute(
            "UPDATE knowledge_base_meta SET name = :hacked "
            "WHERE tenant_id = :victim AND base_id = :bid",
            {
                "hacked": f"{tenant_a['name']}_HACKED",
                "victim": tenant_b["tenant_id"],
                "bid": tenant_b["base_id"],
            },
        )
        deleted = await uow.execute(
            "DELETE FROM knowledge_base_meta WHERE tenant_id = :victim AND base_id = :bid",
            {"victim": tenant_b["tenant_id"], "bid": tenant_b["base_id"]},
        )

    assert updated.rowcount == 0, f"跨租户 UPDATE 竟然改了 {updated.rowcount} 行"
    assert deleted.rowcount == 0, f"跨租户 DELETE 竟然删了 {deleted.rowcount} 行"

    # 受害者数据必须完好无损
    async with tenant_scope(tenant_b["tenant_id"], session_factory=factory) as uow:
        survivor = await uow.fetch_one(
            "SELECT name FROM knowledge_base_meta WHERE tenant_id = :tid AND base_id = :bid",
            {"tid": tenant_b["tenant_id"], "bid": tenant_b["base_id"]},
        )
    assert survivor is not None, "租户 B 的知识库被跨租户删除"
    assert survivor["name"] == tenant_b["name"]


async def test_sec02_cross_tenant_insert_is_rejected_by_with_check(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """以租户 A 身份插入「租户 B 的行」必须被 `WITH CHECK` 拒绝。

    没有这条，攻击者可以往别人家租户里塞数据（污染、越权授权的前置动作）。
    """
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]

    async with tenant_scope(tenant_a["tenant_id"], session_factory=factory) as uow:
        with pytest.raises(Exception) as excinfo:
            await uow.execute(
                "INSERT INTO knowledge_base_meta "
                "(tenant_id, base_id, name, owner_user_id, created_by) "
                "VALUES (:victim, :bid, :name, :uid, :uid)",
                {
                    "victim": tenant_b["tenant_id"],
                    "bid": "kb_b01_forged",
                    "name": "forged_by_a",
                    "uid": tenant_a["user_id"],
                },
            )
            await uow.session.commit()

    assert "row-level security" in str(excinfo.value).lower(), (
        f"跨租户 INSERT 未按 RLS 拒绝，实际异常：{excinfo.value!r}"
    )


# ===========================================================================
# TC-P2-SEC-03 并发 20 × 5 轮交替租户
# ===========================================================================
async def test_sec03_concurrent_twenty_requests_five_rounds_never_leak(
    b01_concurrent_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """20 个并发任务交替使用两个租户，连续 5 轮，任何一行跨租户即 P0 失败。

    断言维度：每条结果行的 `tenant_id` 等于本任务租户、本租户哨兵必现、
    对端哨兵必不现、且确实用了多个后端连接（否则并发是假的）。
    """
    factory = _make_factory(b01_concurrent_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]
    params = {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
    expected = {tenant_a["tenant_id"]: tenant_a["name"], tenant_b["tenant_id"]: tenant_b["name"]}
    forbidden = {
        tenant_a["tenant_id"]: tenant_b["name"],
        tenant_b["tenant_id"]: tenant_a["name"],
    }

    async def one(index: int) -> tuple[str, list[dict[str, Any]], int]:
        """执行一次租户作用域查询。

        Args:
            index: 任务序号（偶数走 A，奇数走 B）。

        Returns:
            tuple[str, list[dict[str, Any]], int]: 租户 ID、结果行、后端连接 PID。
        """
        tenant_id = tenant_a["tenant_id"] if index % 2 == 0 else tenant_b["tenant_id"]
        async with tenant_scope(tenant_id, session_factory=factory) as uow:
            rows = await uow.fetch_all(_SELECT_SENTINEL_KBS, params)
            pid = await uow.fetch_scalar("SELECT pg_backend_pid()")
        return tenant_id, [dict(row) for row in rows], int(pid)

    for round_no in range(5):
        results = await asyncio.gather(*(one(index) for index in range(20)))
        pids: set[int] = set()
        for tenant_id, rows, pid in results:
            pids.add(pid)
            assert len(rows) == 1, (
                f"第 {round_no} 轮：租户 {tenant_id} 应只看到 1 条哨兵库，实际 {len(rows)} 条"
            )
            for row in rows:
                assert row["tenant_id"] == tenant_id, (
                    f"第 {round_no} 轮发生跨租户泄漏：租户 {tenant_id} 查到 {row}"
                )
            names = {row["name"] for row in rows}
            assert expected[tenant_id] in names
            assert forbidden[tenant_id] not in names
        assert len(pids) >= 2, "并发用例未使用多个后端连接，pool 配置可能失效"


# ===========================================================================
# TC-P2-SEC-04 未设租户变量 → fail-closed
# ===========================================================================
async def test_sec04_every_business_table_is_fail_closed(b01_engine: Any) -> None:
    """**全部**业务表（挂 `p_tenant_isolation` 的表）未设租户时必须 0 行。

    主理人只手测了 5 张表；本用例按策略名动态枚举全部业务表，
    任何一张新表漏挂策略/漏开 `FORCE` 都会在这里红。
    """
    tables = await list_policy_tables(b01_engine, BUSINESS_POLICY)
    assert len(tables) >= 20, f"业务表数量异常（{len(tables)}），策略名可能已改名"

    async with b01_engine.connect() as connection:
        for table in tables:
            count = (
                await connection.execute(
                    # 表名来自 pg_policy 系统表枚举，不是外部输入
                    sql_text(f"SELECT count(*) FROM {table}")  # noqa: S608
                )
            ).scalar()
            assert count == 0, f"fail-closed 失效：{table} 在无租户作用域时返回 {count} 行"


async def test_sec04_seeded_catalog_tables_are_readable_without_tenant(
    b01_engine: Any,
) -> None:
    """共享目录表（套餐 / 权限点）无租户作用域时**应能**读到平台种子数据。

    这是 SEC-04 的**反向口径**：`p_catalog_shared` 刻意放行
    `tenant_id = '__platform__'` 的行，否则企业开通时读不到套餐目录。
    把它误判成"泄漏"会导致有人去"修"一个设计内行为，反而把目录表读空。

    只断言**有种子数据**的两张目录表：`ops_settings` 同属共享策略，
    但初始化脚本不预置任何行（`DEFAULT '__platform__'` 待运营后台写入），
    对空表断言 `count > 0` 是把"还没写数据"误报成"读不到数据"。
    """
    tables = await list_policy_tables(b01_engine, CATALOG_POLICY)
    assert {"billing_plan", "permission_catalog", "ops_settings"} <= set(tables), (
        f"共享目录表清单变化：{sorted(tables)}"
    )

    plans = await fetch_scalar(b01_engine, "SELECT count(*) FROM billing_plan")
    permissions = await fetch_scalar(b01_engine, "SELECT count(*) FROM permission_catalog")
    assert plans == 3, f"无租户作用域时套餐目录应有 3 条，实际 {plans}"
    assert permissions == 20, f"无租户作用域时权限点目录应有 20 条，实际 {permissions}"


# ===========================================================================
# TC-P2-SEC-05 源码静态守卫
# ===========================================================================
def _iter_app_sources() -> list[Path]:
    """列出 `app/` 下全部 Python 源文件。

    Returns:
        list[Path]: 源文件路径清单。
    """
    return sorted(APP_SOURCE_ROOT.rglob("*.py"))


def _session_level_set_config_hits() -> list[str]:
    """扫描全量源码中「会话级 `set_config(..., FALSE)`」的命中。

    Returns:
        list[str]: 命中文本清单（应为空）。
    """
    pattern = re.compile(r"set_config\s*\([^)]*?\b(FALSE|False|false)\b")
    hits: list[str] = []
    for path in _iter_app_sources():
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line) and "is_local" not in line:
                hits.append(f"{path.name}:{number}: {line.strip()}")
    return hits


def _non_docstring_string_constants(source: str) -> list[str]:
    """取源码中**非文档字符串**的字符串常量（注释天然不在 AST 内）。

    Args:
        source: Python 源码文本。

    Returns:
        list[str]: 字符串常量清单。
    """
    import ast

    tree = ast.parse(source)
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_ids.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_ids
    ]


def test_sec05_no_session_level_set_config_anywhere() -> None:
    """全量源码中不得出现会话级 `set_config(..., FALSE)`。

    会话级写法会让连接池把 A 租户的变量带给 B 租户，且只在并发下偶发，
    静态拦截是唯一可靠的手段。
    """
    hits = _session_level_set_config_hits()
    assert hits == [], f"发现会话级 set_config（会把租户变量粘在连接上）：{hits}"


def test_sec05_set_config_appears_exactly_once_in_session_module() -> None:
    """`app/db/session.py` 中 `set_config` 只出现 1 次（即 `SET_TENANT_SQL` 常量）。

    按「非文档字符串常量」计数：避免把讲解用的 docstring/注释算进去，
    同时又能挡住任何人在别处再写一条 `set_config` 绕过唯一入口。
    """
    source = (APP_SOURCE_ROOT / "db" / "session.py").read_text(encoding="utf-8")
    mentions = [text for text in _non_docstring_string_constants(source) if "set_config" in text]
    assert len(mentions) == 1, (
        f"app/db/session.py 中 set_config 出现 {len(mentions)} 次（期望 1）：{mentions}"
    )
    assert "TRUE" in mentions[0], f"set_config 第三参不是事务级（TRUE）：{mentions[0]}"


# ===========================================================================
# TC-P2-SEC-06 平台哨兵守卫
# ===========================================================================
async def test_sec06_platform_sentinel_rejected_without_explicit_allow(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """非运营后台路径传入 `__platform__` 必须被 30003 拒掉，且查询仍 0 行。

    阶段二 B01 尚无 `/ops/**` 路由，守卫的唯一入口就是 `tenant_scope`，
    故本用例直接打在守卫层；后续 B 批次补上台路由后应再补 HTTP 层用例。
    """
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]

    with pytest.raises(ApiError) as excinfo:
        async with tenant_scope(PLATFORM_TENANT, session_factory=factory):
            pass
    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR, "平台哨兵未按 30003 拦截"

    # 被拒之后：不开租户作用域直接查，仍是 0 行（fail-closed 依然生效，未泄漏）
    leaked = await fetch_scalar(
        b01_engine, _SELECT_SENTINEL_ANY, {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
    )
    assert leaked == 0, f"平台哨兵被拒后仍然读到了 {leaked} 行哨兵数据"


async def test_sec06_allow_platform_requires_explicit_flag_not_default(
    b01_engine: Any,
    b01_tenants: dict[str, dict[str, str]],
) -> None:
    """运营后台显式 `allow_platform=True` 时可跨租户读取（正向行为必须保留）。"""
    factory = _make_factory(b01_engine)
    tenant_a = b01_tenants["a"]
    tenant_b = b01_tenants["b"]

    async with tenant_scope(PLATFORM_TENANT, allow_platform=True, session_factory=factory) as uow:
        rows = await uow.fetch_all(
            _SELECT_SENTINEL_KBS, {"name_a": tenant_a["name"], "name_b": tenant_b["name"]}
        )

    visible = {row["tenant_id"] for row in rows}
    assert {tenant_a["tenant_id"], tenant_b["tenant_id"]} <= visible, (
        "平台模式下应能读到全部租户数据（运营后台依赖该行为）"
    )


# ===========================================================================
# TC-P2-SEC-07 / G-25  `/readyz` 的 `db_privilege` 自检（真实 DSN 反向验证）
# ===========================================================================
async def test_sec07_readyz_not_ready_with_superuser_dsn(b01_superuser_dsn: str) -> None:
    """超级用户 DSN 启动 → `not_ready`，`db_privilege` 检查项为「数据库连接权限配置不正确」。

    与 `tests/integration/test_health.py` 的替身引擎用例互补：那里证明的是
    **分支逻辑**，这里证明的是**真实超级用户连接确实会被判死**。
    """
    body = _readyz_body(b01_superuser_dsn)
    try:
        assert body["status_code"] == 500
        assert body["status"] == "not_ready"
        checks = _check_map(body)
        assert checks["db_privilege"]["ready"] is False
        assert checks["db_privilege"]["detail"] == "数据库连接权限配置不正确"
        # 数组契约：`db` 项必须仍在，新增项只能追加
        assert checks["db"]["ready"] is True
        assert len(body["checks"]) >= 5
    finally:
        await reset_engine_for_test()


async def test_sec07_readyz_ready_with_app_role_dsn(b01_app_dsn: str) -> None:
    """应用角色（`officeai_app`）DSN 启动 → `ready`，`db_privilege` 为「数据库连接权限正常」。

    反向验证：换成受限角色后自检必须转绿，否则运维按提示改完后仍会一直 not_ready。
    """
    body = _readyz_body(b01_app_dsn)
    try:
        assert body["status_code"] == 200
        assert body["status"] == "ready"
        checks = _check_map(body)
        assert checks["db_privilege"]["ready"] is True
        assert checks["db_privilege"]["detail"] == "数据库连接权限正常"
        assert checks["db"]["ready"] is True
    finally:
        await reset_engine_for_test()


async def test_sec07_app_role_identity_is_nosuperuser_nobypassrls(b01_engine: Any) -> None:
    """连接身份自检：`current_user` 必须是 `officeai_app` 且不带 BYPASSRLS。

    这是 SEC-02/03/04 全部断言成立的前提；一旦有人把 DSN 换回 `officeai`，
    夹具会先 fail，这里再落一道明示断言便于报告定位。
    """
    async with b01_engine.connect() as connection:
        role, bypass = (
            await connection.execute(
                sql_text(
                    "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()

    assert role == APP_ROLE, f"测试连接角色应为 {APP_ROLE}，实际 {role}"
    assert bypass is False, f"连接角色 {role} 带 BYPASSRLS，全部隔离断言均无效"
