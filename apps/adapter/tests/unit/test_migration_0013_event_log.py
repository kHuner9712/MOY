"""迁移 `0013_event_log` 的**静态门禁**（不需要数据库的判据层）。

**为什么需要这一层**：真库用例（`tests/integration/test_event_log_pg.py`）只在
`--run-pg` 时跑；CI 默认档、以及"本地没起库"的场景，需要有判据兜住设计文档 §5.1
的三条硬约束。这类缺陷的复发形态不是"代码写错"，而是**有人把取舍改回去**：

- "16 个分区太多了，改成不分区吧"——回放从走分区变成扫全表；
- "UPDATE/DELETE 也授了吧，运维方便"——append-only 当场失效；
- "down 脚本删表就够了，分区会被级联带走"——半回滚状态留下孤儿表；
- "hash 里别用规范化 JSON 了"——客户复算对不上，链的证据价值归零。

判据全部钉在**SQL 文本 + 仓储代码文本**上，与 `deploy/validate_migrations.py`
的守卫/配对规则**同源**（加载同一个模块，不在这里另写一份规则）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Final

from app.db.repositories.event_log.postgres import (
    _CHAIN_READ_SQL_TEMPLATE,
    _HEAD_SQL,
    EVENT_LOG_TABLE,
)
from app.db.repositories.event_log.records import (
    EVENT_TYPES,
    GENESIS_HASH,
    canonical_json,
    compute_hash,
)

#: `apps/adapter/tests/unit/test_x.py` → parents[4] 才是仓库根
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]

#: 迁移脚本
MIGRATION_0013: Final[Path] = REPO_ROOT / "deploy" / "migrations" / "0013_event_log.sql"

#: 回滚脚本
ROLLBACK_0013: Final[Path] = (
    REPO_ROOT / "deploy" / "migrations" / "rollback" / "0013_event_log.down.sql"
)

#: PG 实现（绑定参数写法与 INSERT 形态的判据对象）
POSTGRES_MODULE: Final[Path] = (
    REPO_ROOT / "apps" / "adapter" / "app" / "db" / "repositories" / "event_log" / "postgres.py"
)

#: 迁移门禁脚本（守卫/配对规则的唯一真源）
VALIDATOR: Final[Path] = REPO_ROOT / "deploy" / "validate_migrations.py"

#: 设计文档 §5.1 的 14 类事件
DESIGN_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session.created",
        "turn.started",
        "context.assembled",
        "context.compressed",
        "model.request",
        "model.delta",
        "model.response",
        "tool.requested",
        "tool.completed",
        "approval.requested",
        "approval.resolved",
        "artifact.created",
        "session.forked",
        "turn.finished",
    }
)


def _load_validator() -> Any:
    """按文件路径加载 `deploy/validate_migrations.py`。

    Returns:
        Any: 模块对象。
    """
    spec = importlib.util.spec_from_file_location("_validate_migrations", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationFiles:
    """迁移与回滚脚本本身。"""

    def test_migration_and_rollback_both_exist(self) -> None:
        """迁移与 `rollback/` 下的 down 脚本必须成对（自 0006 起的纪律）。"""
        assert MIGRATION_0013.is_file()
        assert ROLLBACK_0013.is_file()

    def test_dollar_quotes_are_paired(self) -> None:
        """美元引号标签成对（`0004` 的 `END $$` 事故不再重演）。"""
        validator = _load_validator()
        assert validator.check_dollar_quotes(MIGRATION_0013) == []
        assert validator.check_dollar_quotes(ROLLBACK_0013) == []

    def test_guard_and_privilege_handler_present(self) -> None:
        """含 DDL 的迁移必须带存在性守卫，且 `ALTER TABLE` 须处理权限异常。"""
        validator = _load_validator()
        assert validator.check_guard_presence(MIGRATION_0013) == []

    def test_rollback_really_drops_table_and_partitions(self) -> None:
        """down 脚本**真删**：16 个分区逐个 DROP + 父表 DROP（不能空转）。"""
        sql = ROLLBACK_0013.read_text(encoding="utf-8")
        assert "DROP TABLE" in sql
        assert "partition_count" in sql
        assert "event_log_p" in sql
        assert "DROP TABLE event_log CASCADE" in sql


class TestTableDefinition:
    """设计文档 §5.1 的表结构与三条硬约束。"""

    def test_columns_match_design_exactly(self) -> None:
        """九列与设计文档逐字一致（**没有**自行加字段）。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        block = re.search(
            r"CREATE TABLE IF NOT EXISTS event_log \((.*?)\n\) PARTITION BY HASH \(session_id\)",
            sql,
            re.DOTALL,
        )
        assert block is not None
        body = block.group(1)
        for column in (
            "tenant_id   text        NOT NULL",
            "session_id  text        NOT NULL",
            "seq         bigint      NOT NULL",
            "event_id    text        NOT NULL",
            "type        text        NOT NULL",
            "payload     jsonb       NOT NULL",
            "prev_hash   text        NOT NULL",
            "hash        text        NOT NULL",
            "created_at  timestamptz NOT NULL DEFAULT now()",
        ):
            assert column in body, f"缺列定义：{column}"
        assert "PRIMARY KEY (session_id, seq)" in body

    def test_partition_count_is_a_script_constant(self) -> None:
        """分区数写成脚本内常量（不是散落字面量），且为 16。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "partition_count CONSTANT integer := 16" in sql
        assert "MODULUS" in sql and "REMAINDER" in sql
        assert "PARTITION BY HASH (session_id)" in sql

    def test_two_indexes_declared(self) -> None:
        """两条索引：`(tenant_id, session_id, seq)` 与 `(tenant_id, created_at)`。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "idx_event_log_session_seq" in sql
        assert "event_log (tenant_id, session_id, seq)" in sql
        assert "idx_event_log_tenant_created" in sql
        assert "event_log (tenant_id, created_at)" in sql

    def test_rls_is_enabled_forced_and_platform_exception_kept(self) -> None:
        """RLS：`ENABLE` + `FORCE` + `__platform__` 例外（体例照抄 phase2 第 15 节）。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "FORCE ROW LEVEL SECURITY" in sql
        assert "__platform__" in sql
        assert "current_setting(''app.tenant_id'', TRUE)" in sql
        # 分区单独建策略（PG 的分区不继承父表策略）
        assert "PARTITION" in sql or "event_log_p" in sql

    def test_only_select_and_insert_policies(self) -> None:
        """只建 SELECT / INSERT 策略：没有 UPDATE/DELETE 策略 = 恒 0 行。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "FOR SELECT" in sql
        assert "FOR INSERT" in sql
        assert "FOR UPDATE" not in sql
        assert "FOR DELETE" not in sql

    def test_app_role_gets_insert_select_only(self) -> None:
        """应用角色只授 INSERT + SELECT，且显式 REVOKE（默认权限里含四权）。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "REVOKE ALL ON" in sql
        assert "GRANT SELECT, INSERT ON" in sql
        assert "GRANT SELECT, INSERT, UPDATE" not in sql

    def test_event_type_enum_is_closed_in_code_not_in_ddl(self) -> None:
        """14 类事件在代码侧封闭（不在库里写 CHECK：V1.1 加类型不该重写全表）。"""
        assert EVENT_TYPES == DESIGN_EVENT_TYPES
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "CHECK" not in sql or "CHECK (type" not in sql


class TestZeroDdlGuardsAreStrict:
    """ "零 DDL 守卫"必须逐项验事实（缺陷 D5）。

    守卫的失效方式不是"写错"，而是**验得太少**：只看策略名存在就 RETURN，于是
    `relforcerowsecurity=false`、同名 `USING (true)` 策略、分区漏策略三种
    "隔离已降级"的库都会被判成"已就绪"——守卫反而成了静默放行的通道。
    """

    def test_rls_guard_checks_force_flag_not_only_policy_name(self) -> None:
        """守卫必须验 `relforcerowsecurity`（属主能否绕过策略）。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "relforcerowsecurity" in sql
        assert "c.relrowsecurity AND c.relforcerowsecurity" in sql

    def test_rls_guard_rejects_always_true_policies(self) -> None:
        """守卫必须验策略表达式**非恒真**（同名 `USING (true)` 等于没隔离）。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "qual IS NOT NULL" in sql
        assert "with_check IS NOT NULL" in sql
        assert "'(true)'" in sql

    def test_rls_guard_covers_all_16_partitions(self) -> None:
        """守卫按 `partition_count + 1`（父表 + 16 分区）逐张验，一张不合格就补。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "ready = partition_count + 1" in sql
        assert "补齐剩余部分" in sql

    def test_partition_and_index_guards_check_existence_explicitly(self) -> None:
        """分区/索引守卫**显式查存在性**，不依赖 `IF NOT EXISTS` 的跳过语义。

        路径 B 上应用角色不是表属主，而 `CREATE INDEX` 在解析目标表时就校验属主
        （`RangeVarCallbackOwnsTable`）——把"已存在就跳过"交给实现细节，等于拿
        迁移的可重复执行性赌运气。0012 已为同一件事立过规矩，这里照抄。
        """
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "FROM pg_indexes" in sql
        assert "existing = 2 THEN" in sql
        assert "existing = partition_count THEN" in sql
        # 分区守卫按命名模式数，而不是假设"表存在 ⇒ 分区齐"
        assert "c.relname ~ '^event_log_p[0-9]{2}$'" in sql

    def test_grant_guard_checks_every_partition_and_forbids_update_delete(self) -> None:
        """授权守卫逐张验：SELECT+INSERT 有、UPDATE/DELETE **无**。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "NOT has_table_privilege('officeai_app', t, 'UPDATE')" in sql
        assert "NOT has_table_privilege('officeai_app', t, 'DELETE')" in sql
        assert "granted = partition_count + 1" in sql

    def test_guards_give_actionable_dba_sql_instead_of_skipping(self) -> None:
        """不满足时**中止**并给出可照做的 DBA SQL，不静默跳过。"""
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "请由 DBA" in sql
        assert "GRANT SELECT, INSERT ON <表> TO officeai_app" in sql
        assert "本迁移拒绝跳过" in sql


class TestBindParameterStyle:
    """绑定参数的写法（静态可判，不需要数据库）。"""

    def test_no_double_colon_right_after_a_bind_parameter(self) -> None:
        """禁止 `:x::type`：SQLAlchemy 的绑定参数正则带 `(?!:)`，这种写法不会被识别。

        实测形态：`:sid_0::text` 会被当成普通文本发到库里，报的是
        `syntax error at or near ":"`——只有真库才炸，所以判据钉在源码文本上。
        正确写法是 `CAST(:sid_0 AS text)`。
        """
        # 只看代码行：注释里出现 `:x::type` 是**说明**，不是可执行 SQL
        # （注释里的反例如果被判红，修复者只能删掉解释，那是把门禁变成噪声源）。
        offenders: list[str] = []
        for line in POSTGRES_MODULE.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            offenders.extend(re.findall(r":\w+::", code))
        assert not offenders, f"发现 `:x::type` 写法（应用 CAST 替代）：{offenders}"

    def test_chain_read_sql_compiles_with_all_bind_parameters(self) -> None:
        """父链读取 SQL 能被 SQLAlchemy 编译，且**所有**绑定参数都被识别出来。

        比正则更强的一层：SQLAlchemy 的 `text()` 决定"哪些是绑定参数"，
        只要它没认出 `:after_cursor`，运行期就会把它当普通文本发给数据库。
        """
        from sqlalchemy import text
        from sqlalchemy.dialects import postgresql

        values = ", ".join(
            f"(CAST(:sid_{index} AS text), CAST(:limit_{index} AS bigint), "
            f"CAST(:offset_{index} AS bigint))"
            for index in range(2)
        )
        statement = text(_CHAIN_READ_SQL_TEMPLATE.replace("{values}", values))
        statement.compile(dialect=postgresql.asyncpg.dialect())
        expected = {
            "tenant_id",
            "after_cursor",
            "limit",
            "sid_0",
            "limit_0",
            "offset_0",
            "sid_1",
            "limit_1",
            "offset_1",
        }
        assert expected <= set(statement._bindparams)  # noqa: SLF001

    def test_head_sql_compiles_with_tenant_and_session(self) -> None:
        """头部查询（末号 / 末 hash / 游标偏移）的绑定参数同样要被识别。"""
        from sqlalchemy import text

        statement = text(_HEAD_SQL)
        assert {"tenant_id", "session_id"} <= set(statement._bindparams)  # noqa: SLF001


class TestStoreSqlContract:
    """PG 实现的 SQL 契约（表名、规范化 JSON、创世 hash）。"""

    def test_table_name_matches_migration(self) -> None:
        """仓储用的表名与迁移建的表是同一个。"""
        assert EVENT_LOG_TABLE == "event_log"
        sql = MIGRATION_0013.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS event_log" in sql

    def test_chain_read_sql_uses_lineage_cte_and_limit(self) -> None:
        """父链读取在一条 SQL 完成（`lineage` 参数化 + `LIMIT`）。"""
        assert "WITH lineage" in _CHAIN_READ_SQL_TEMPLATE
        assert "{values}" in _CHAIN_READ_SQL_TEMPLATE
        assert "LIMIT :limit" in _CHAIN_READ_SQL_TEMPLATE
        assert "cursor_offset + e.seq > :after_cursor" in _CHAIN_READ_SQL_TEMPLATE

    def test_genesis_hash_is_64_zeros(self) -> None:
        """创世前 hash 是 64 个 `0`（长度与真实 hash 相同）。"""
        assert GENESIS_HASH == "0" * 64
        assert len(GENESIS_HASH) == 64

    def test_canonical_json_is_the_hash_input(self) -> None:
        """hash 的第四个输入就是 `canonical_json(payload)`（键排序、无空格、UTF-8）。"""
        payload = {"b": "中文", "a": 1}
        assert canonical_json(payload) == '{"a":1,"b":"中文"}'
        expected = compute_hash(GENESIS_HASH, "evt_x", "turn.started", payload)
        material = f"{GENESIS_HASH}evt_xturn.started{canonical_json(payload)}"
        import hashlib

        assert expected == hashlib.sha256(material.encode("utf-8")).hexdigest()
