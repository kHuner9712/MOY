"""保留期清理任务单元测试（架构 §10.4 / PRD P4-05 ②）。

## 这一组用例在防什么风险

1. **保留期被写死**：天数必须全部来自 `Settings.retention_*`（合同条款，改天数不该发版）。
   用例注入固定 `now`，把每条判据的截止时间**逐字算出来**比对。
2. **审计表被当成"失败"或"静默跳过"**：`audit_log` 删不掉是设计（应用角色无 DELETE
   权限），验收硬判据是日志里**逐字**出现 `audit_log=skipped(no privilege)`。
3. **一次权限问题毁掉整轮清理**：一张表失败不得中断其余表（否则数据永久超期）。
4. **长锁**：单批必须 ≤ batch_size，最后一批不足即停（否则一条 DELETE 锁几十万行）。
5. **失败后清理永久停摆**：无论成败都要把自己排到次日（自续期）。
6. **记忆被误删**：记忆是永久保留，只由用户本人删。

## 口径说明

`tenant_scope` / `platform_scope` 换成 `tests/unit/fake_tenant_scope.py` 的内存替身。
替身不解释 `WHERE` 谓词（"哪一行过期"是 SQL 层事实，由真库用例覆盖，本机 skip）；
本文件断言的是编排行为：命中哪张表、分几批、批大小、事务次数、报告字段、日志与自续期。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.db.repositories.job_repo import InMemoryJobStore, JobRecord
from app.workers.tasks import retention_cleanup
from app.workers.tasks.retention_cleanup import (
    ALERT_AFTER_FAILURES,
    AUDIT_SKIP_MARKER,
    DEFAULT_BATCH_SIZE,
    MODE_SKIP_NO_PRIVILEGE,
    MODE_SKIP_PERMANENT,
    RETENTION_DEDUPE_KEY,
    RETENTION_KIND,
    RETENTION_MAX_ATTEMPTS,
    SCHEDULE_HOUR_UTC,
    RetentionReport,
    TableResult,
    build_rules,
    handle_retention_cleanup,
    is_insufficient_privilege,
    iso,
    list_tenants,
    next_daily_run_after,
    register_retention_cleanup,
    run_retention,
    schedule_all_tenants,
    schedule_tenant,
    subtract_months,
)
from tests.unit.fake_tenant_scope import (
    FakeDatabase,
    install_platform_scope,
    install_tenant_scope,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_retention_unit"
OTHER_TENANT = "tnt_retention_other"

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "retention-unit-secret",
    "downstream_enabled": False,
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


def _settings(**overrides: Any) -> Settings:
    """构造配置实例。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params = dict(_BASE_PARAMS)
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


class LoggerSpy:
    """记录 structlog 调用的替身（断言"日志里逐字出现某句话"）。

    为什么不抓 stdout：`configure_logging()` 在 configure 时就把 renderer 绑到了
    当时的 `sys.stdout`，与 `capsys` 之后替换的不是同一个对象。直接替换模块级
    `logger` 才能确定性断言"代码确实发出了这条事件、且级别正确"。
    """

    def __init__(self) -> None:
        """初始化空记录。"""
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        """记录一次调用。

        Args:
            level: 级别。
            event: 事件名。
            **fields: 附加字段。
        """
        self.events.append((level, str(event), dict(fields)))

    def debug(self, event: str, **fields: Any) -> None:
        """DEBUG 级。"""
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        """INFO 级。"""
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        """WARNING 级。"""
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        """ERROR 级。"""
        self._record("error", event, **fields)

    def named(self, event: str) -> list[tuple[str, dict[str, Any]]]:
        """取某事件的全部 `(级别, 字段)`。

        Args:
            event: 事件名。

        Returns:
            list[tuple[str, dict[str, Any]]]: 匹配项。
        """
        return [(level, fields) for level, name, fields in self.events if name == event]


def _privilege_error() -> BaseException:
    """构造"应用角色无 DELETE 权限"的异常（SQLSTATE 42501）。

    Returns:
        BaseException: 带 `orig` 链的异常（贴近 SQLAlchemy 包装真实异常的样子）。
    """

    class InsufficientPrivilegeError(Exception):
        sqlstate = "42501"

    class WrapperError(Exception):
        orig = InsufficientPrivilegeError("permission denied")

    return WrapperError("(sqlalchemy) permission denied for table audit_log")


def _seed_rules(database: FakeDatabase, settings: Settings, count: int) -> list[str]:
    """给所有 `delete` 模式的表各造 `count` 行。

    Args:
        database: 内存库。
        settings: 配置（决定规则表清单）。
        count: 每表行数。

    Returns:
        list[str]: 被造数据的表名。
    """
    tables: list[str] = []
    for rule in build_rules(settings):
        if rule.mode != "delete":
            continue
        database.seed(rule.table, [{"tenant_id": TENANT, "n": index} for index in range(count)])
        tables.append(rule.table)
    return tables


# ===========================================================================
# 时间口径（纯函数）
# ===========================================================================
class TestTimeMath:
    """保留期的自然月/自然日口径。"""

    def test_subtract_months_clamps_to_month_end(self) -> None:
        """按自然月回退并截到目标月最后一天（30 天近似在司法口径上不成立）。"""
        assert subtract_months(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(
            2026, 2, 28, tzinfo=UTC
        )
        assert subtract_months(datetime(2028, 3, 31, tzinfo=UTC), 1) == datetime(
            2028, 2, 29, tzinfo=UTC
        )
        assert subtract_months(datetime(2026, 1, 15, tzinfo=UTC), 13) == datetime(
            2024, 12, 15, tzinfo=UTC
        )

    def test_subtract_months_zero_is_identity(self) -> None:
        """0 个月不改动时间（避免 `retention_usage_months=0` 这类越界配置造成漂移）。"""
        moment = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
        assert subtract_months(moment, 0) == moment
        assert subtract_months(moment, -3) == moment

    def test_next_daily_run_is_strictly_in_the_future(self) -> None:
        """次日 `SCHEDULE_HOUR_UTC` 点，且严格大于 now（同一天该点之后 → 明天）。"""
        before = next_daily_run_after(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
        assert before == datetime(2026, 11, 2, SCHEDULE_HOUR_UTC, 0, tzinfo=UTC)
        at_point = next_daily_run_after(datetime(2026, 11, 2, SCHEDULE_HOUR_UTC, 0, tzinfo=UTC))
        assert at_point == datetime(2026, 11, 3, SCHEDULE_HOUR_UTC, 0, tzinfo=UTC)
        assert at_point > datetime(2026, 11, 2, SCHEDULE_HOUR_UTC, tzinfo=UTC)


# ===========================================================================
# 规则生成（保留期 = 合同条款）
# ===========================================================================
class TestRules:
    """`build_rules`：天数全部来自配置，模式与顺序固定。"""

    def test_cutoffs_are_derived_from_settings(self) -> None:
        """每条判据的截止时间都由 `Settings.retention_*` 算出（没有写死的天数）。"""
        settings = _settings(
            retention_audit_days=7,
            retention_usage_months=13,
            retention_task_days=30,
            retention_conversation_days=400,
            conversation_soft_delete_recover_days=5,
        )
        now = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
        rules = {rule.table: rule for rule in build_rules(settings, now=now)}

        assert rules["audit_log"].params["audit_cutoff"] == now - timedelta(days=7)
        assert rules["plugin_task"].params["task_cutoff"] == now - timedelta(days=30)
        assert rules["usage_event"].params["usage_cutoff"] == subtract_months(now, 13)
        # 13 个月前 = 2025-02（自然月，不是 390 天）
        assert rules["usage_monthly"].params["period_floor"] == "2025-02"
        assert rules["conversation"].params["conversation_cutoff"] == now - timedelta(days=400)
        assert rules["message"].params["soft_delete_cutoff"] == now - timedelta(days=5)

    def test_changing_settings_changes_rules(self) -> None:
        """改配置必须改判据（若某天有人把天数写死，这条会红）。"""
        now = datetime(2026, 3, 15, tzinfo=UTC)
        loose = {
            rule.table: rule for rule in build_rules(_settings(retention_audit_days=180), now=now)
        }
        tight = {
            rule.table: rule for rule in build_rules(_settings(retention_audit_days=1), now=now)
        }
        assert tight["audit_log"].params["audit_cutoff"] - loose["audit_log"].params[
            "audit_cutoff"
        ] == timedelta(days=179)

    def test_memory_tables_are_permanent(self) -> None:
        """记忆三张表永远是"永久保留"，且判据是 `false`（不是"删 0 行"）。"""
        rules = {rule.table: rule for rule in build_rules(_settings())}
        for table in ("user_memory", "memory_embedding", "user_memory_setting"):
            assert rules[table].mode == MODE_SKIP_PERMANENT
            assert rules[table].where_sql == "false"
            assert rules[table].reason

    def test_audit_rule_is_skip_no_privilege_with_reason(self) -> None:
        """审计规则不是"永久跳过"，而是"尝试后按权限结果跳过"（授权后会自动生效）。"""
        rules = {rule.table: rule for rule in build_rules(_settings())}
        assert rules["audit_log"].mode == MODE_SKIP_NO_PRIVILEGE
        assert "init-app-role" in rules["audit_log"].reason

    def test_artifact_rule_comes_before_task_rule(self) -> None:
        """产物规则必须排在任务规则之前。

        两张表的外键是 `ON DELETE CASCADE`：先删任务会连带删掉产物与步骤，
        "产物过期了几行"就永远统计成 0，报告不再是事实。
        """
        tables = [rule.table for rule in build_rules(_settings())]
        assert tables.index("plugin_artifact") < tables.index("plugin_task")


# ===========================================================================
# 权限异常识别
# ===========================================================================
class TestPrivilegeDetection:
    """`is_insufficient_privilege`：SQLAlchemy 会把 asyncpg 异常包一层。"""

    def test_detects_sqlstate_on_wrapped_chain(self) -> None:
        """沿 `orig` 找到 SQLSTATE 42501（SQLAlchemy `ProgrammingError` 的形态）。"""
        assert is_insufficient_privilege(_privilege_error()) is True

    def test_detects_by_exception_name(self) -> None:
        """按异常类名识别（asyncpg 原生异常名）。"""

        class InsufficientPrivilegeError(Exception):
            pass

        assert is_insufficient_privilege(InsufficientPrivilegeError("nope")) is True

    def test_detects_through_cause_chain(self) -> None:
        """`__cause__` 链同样被检查（raise ... from ... 的形态）。"""

        class RawError(Exception):
            pgcode = "42501"

        try:
            try:
                raise RawError("permission denied")
            except RawError as inner:
                raise RuntimeError("wrapped") from inner
        except RuntimeError as wrapped:
            assert is_insufficient_privilege(wrapped) is True

    def test_does_not_treat_other_errors_as_privilege(self) -> None:
        """连接类/语法类错误不算权限不足（否则真故障会被静默"跳过"）。"""
        assert is_insufficient_privilege(RuntimeError("connection reset")) is False
        assert is_insufficient_privilege(KeyError("42501")) is False

    def test_self_referencing_exception_chain_terminates(self) -> None:
        """异常链自引用时不会死循环（`orig` 指回自己是真实存在的形态）。"""

        class SelfReferencingError(Exception):
            def __init__(self) -> None:
                super().__init__("loop")
                self.orig = self

        assert is_insufficient_privilege(SelfReferencingError()) is False


# ===========================================================================
# 报告对象
# ===========================================================================
class TestReport:
    """`RetentionReport` 的派生字段（日志与证明都读它们）。"""

    def test_derived_fields(self) -> None:
        """总数只算删除行数；跳过项不算失败；审计跳过单独可见。"""
        report = RetentionReport(
            tenant_id=TENANT,
            results=[
                TableResult(table="usage_event", deleted=3),
                TableResult(table="audit_log", status=MODE_SKIP_NO_PRIVILEGE),
                TableResult(table="user_memory", status=MODE_SKIP_PERMANENT),
                TableResult(table="message", status="failed", error="RuntimeError"),
            ],
        )
        assert report.deleted_total == 3
        assert [item.table for item in report.failed_tables] == ["message"]
        assert report.audit_skipped is True
        assert report.counts() == {
            "usage_event": 3,
            "audit_log": 0,
            "user_memory": 0,
            "message": 0,
        }

    def test_skipped_flag_only_for_skip_modes(self) -> None:
        """`skipped` 只对两种 skip 模式为真（失败不是"跳过"）。"""
        assert TableResult(table="a", status=MODE_SKIP_NO_PRIVILEGE).skipped is True
        assert TableResult(table="a", status=MODE_SKIP_PERMANENT).skipped is True
        assert TableResult(table="a", status="failed").skipped is False
        assert TableResult(table="a").skipped is False

    def test_audit_skipped_false_without_audit_row(self) -> None:
        """没有 audit_log 结果项时不算"审计已跳过"（避免误报合规达成）。"""
        assert RetentionReport(tenant_id=TENANT).audit_skipped is False


# ===========================================================================
# 执行一轮清理
# ===========================================================================
class TestRunRetention:
    """`run_retention`：分批、跳过、失败隔离。"""

    async def test_deletes_every_rule_table_and_reports_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """所有 delete 模式的表都被清空，报告里的行数等于实际删除行数。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        tables = _seed_rules(database, settings, 3)
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        report = await run_retention(TENANT, settings, now=datetime(2026, 3, 15, tzinfo=UTC))

        assert report.tenant_id == TENANT
        assert report.batch_size == DEFAULT_BATCH_SIZE
        assert report.started_at == "2026-03-15T00:00:00Z"
        assert report.finished_at.endswith("Z")
        assert report.failed_tables == []
        for table in tables:
            assert database.row_count(table, TENANT) == 0, f"{table} 未清空"
            assert report.counts()[table] == 3
        # 永久保留的表一行都没动
        assert report.counts()["user_memory"] == 0
        assert "retention_table_cleaned" in {name for _level, name, _f in spy.events}

    async def test_batches_are_bounded_by_batch_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单批不超过 batch_size，且"本批不足一批"即停止（不空转也不开长事务）。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        database.seed("usage_event", [{"tenant_id": TENANT, "n": index} for index in range(23)])
        database.failures["audit_log"] = _privilege_error()

        report = await run_retention(TENANT, settings, batch_size=10)

        assert database.delete_batches("usage_event") == [10, 10, 3]
        assert report.counts()["usage_event"] == 23
        # 每批一个事务：DELETE 语句数 == 开启的事务数（否则锁窗口不有界）
        deletes = [sql for sql, _params in database.statements if sql.startswith("DELETE")]
        assert len(deletes) == len(database.scopes)

    async def test_exact_multiple_needs_one_extra_probe_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """恰好整批时多跑一次空批确认到底（终止条件是"本批不足"，不是"等于"）。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.seed("usage_event", [{"tenant_id": TENANT, "n": index} for index in range(20)])
        database.failures["audit_log"] = _privilege_error()

        report = await run_retention(TENANT, _settings(), batch_size=10)

        assert database.delete_batches("usage_event") == [10, 10, 0]
        assert report.counts()["usage_event"] == 20

    async def test_audit_is_reported_as_skipped_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """审计无权限 → 状态是"跳过"、日志逐字出现验收标记、不算失败。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        report = await run_retention(TENANT, _settings())

        assert report.audit_skipped is True
        assert report.failed_tables == []
        marked = spy.named(AUDIT_SKIP_MARKER)
        assert marked, f"日志里必须逐字出现 {AUDIT_SKIP_MARKER}"
        level, fields = marked[0]
        assert level == "warning"
        assert fields["table"] == "audit_log"
        assert fields["reason"] == "no privilege"
        assert AUDIT_SKIP_MARKER == "audit_log=skipped(no privilege)"

    async def test_audit_cleaned_when_database_grants_permission(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """一旦数据库真的授予权限，审计清理自动生效（代码不写死"删不了"）。

        这是"按数据库权限判定"而不是"代码里写死跳过"的可验证后果。
        """
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.seed("audit_log", [{"tenant_id": TENANT, "n": 1}, {"tenant_id": TENANT, "n": 2}])

        report = await run_retention(TENANT, _settings())

        assert report.audit_skipped is False
        assert report.counts()["audit_log"] == 2
        assert database.row_count("audit_log", TENANT) == 0

    async def test_audit_non_privilege_failure_is_reported_as_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """审计表上的**非权限**失败要记 failed（不能一律当"跳过"糊过去）。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.failures["audit_log"] = RuntimeError("connection reset")
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        report = await run_retention(TENANT, _settings())

        assert [item.table for item in report.failed_tables] == ["audit_log"]
        assert report.audit_skipped is False
        assert not spy.named(AUDIT_SKIP_MARKER), "非权限失败不得打「无权限」标记"
        assert spy.named("retention_table_failed")

    async def test_failed_table_does_not_stop_the_rest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """一张表失败（非权限原因）→ 记 failed，其余表照常清理。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        _seed_rules(database, settings, 2)
        database.failures["usage_event"] = RuntimeError("connection reset")
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        report = await run_retention(TENANT, settings)

        failed = report.failed_tables
        assert [item.table for item in failed] == ["usage_event"]
        assert failed[0].error == "RuntimeError"
        # 跳过项 = 审计（无权限）+ 三张记忆表（永久保留），顺序即规则顺序
        assert [item.table for item in report.results if item.skipped] == [
            "audit_log",
            "user_memory",
            "memory_embedding",
            "user_memory_setting",
        ]
        assert report.audit_skipped is True
        # 其余表照常清空
        assert database.row_count("conversation", TENANT) == 0
        assert database.row_count("user_memory", TENANT) == 0
        assert spy.named("retention_table_failed")

    async def test_other_tenant_rows_are_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只清 `job.tenant_id` 的行（RLS 兜底的前提是语句永远带 tenant_id）。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.seed("usage_event", [{"tenant_id": OTHER_TENANT, "n": 1}])
        database.failures["audit_log"] = _privilege_error()

        report = await run_retention(TENANT, _settings())

        assert report.counts()["usage_event"] == 0
        assert database.row_count("usage_event", OTHER_TENANT) == 1


# ===========================================================================
# 调度（排队 / 枚举 / 自续期）
# ===========================================================================
class TestScheduling:
    """`schedule_tenant` / `list_tenants` / `schedule_all_tenants`。"""

    async def test_schedule_tenant_enqueues_fixed_dedupe_key(self) -> None:
        """去重键是固定串（不带日期），执行时刻记在 `payload.scheduled_for`。"""
        store = InMemoryJobStore()
        now = datetime(2026, 3, 15, 1, 0, tzinfo=UTC)

        job = await schedule_tenant(TENANT, store=store, settings=_settings(), now=now)

        assert job is not None
        assert job.kind == RETENTION_KIND
        assert job.dedupe_key == RETENTION_DEDUPE_KEY == "retention-daily"
        assert job.max_attempts == RETENTION_MAX_ATTEMPTS == 3
        assert job.run_after.startswith("2026-03-15T03:00:00")
        assert job.payload["scheduled_for"] == job.run_after

    async def test_schedule_tenant_dedupes_on_second_call(self) -> None:
        """同租户已有在排任务时返回 None（重启多次也只排一次）。"""
        store = InMemoryJobStore()
        settings = _settings()
        assert await schedule_tenant(TENANT, store=store, settings=settings) is not None
        assert await schedule_tenant(TENANT, store=store, settings=settings) is None

    async def test_list_tenants_uses_read_only_platform_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """枚举租户走只读 `platform_scope`（写路径不许跨租户）。"""
        database = FakeDatabase()
        database.seed("tenant", [{"tenant_id": OTHER_TENANT}, {"tenant_id": TENANT}])
        install_platform_scope(monkeypatch, retention_cleanup, database)

        tenants = await list_tenants(_settings())

        assert tenants == sorted([TENANT, OTHER_TENANT])
        assert database.scopes == [("__platform__", True)]

    async def test_schedule_all_tenants_reports_only_new_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """为全部租户排队，返回值只含**本次新入队**的租户；重复调用返回空列表。"""
        database = FakeDatabase()
        database.seed("tenant", [{"tenant_id": TENANT}, {"tenant_id": OTHER_TENANT}])
        install_platform_scope(monkeypatch, retention_cleanup, database)
        store = InMemoryJobStore()
        settings = _settings()

        first = await schedule_all_tenants(settings, store=store)
        second = await schedule_all_tenants(settings, store=store)

        assert sorted(first) == sorted([TENANT, OTHER_TENANT])
        assert second == []
        queued = await store.list_by_status(TENANT, "queued")
        assert len(queued) == 1
        assert queued[0].dedupe_key == RETENTION_DEDUPE_KEY


# ===========================================================================
# 任务处理器（Runner 入口）
# ===========================================================================
class TestHandleJob:
    """`handle_retention_cleanup`：跑完自续期；异常原因失败要抛给 Runner。"""

    async def test_success_reschedules_and_logs_totals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """成功后把下一次排到次日，并在日志里给出可核对的总行数。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        _seed_rules(database, settings, 2)
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)
        store = InMemoryJobStore()
        job = JobRecord(
            tenant_id=TENANT,
            job_id="job_retention_unit",
            kind=RETENTION_KIND,
            dedupe_key=RETENTION_DEDUPE_KEY,
            attempts=1,
        )

        await handle_retention_cleanup(job, settings=settings, store=store)

        done = spy.named("retention_cleanup_done")
        assert done
        _level, fields = done[0]
        assert fields["deleted_total"] > 0
        assert fields["audit_skipped"] is True
        assert fields["skipped"] == [
            "audit_log",
            "user_memory",
            "memory_embedding",
            "user_memory_setting",
        ]
        queued = await store.list_by_status(TENANT, "queued")
        assert len(queued) == 1
        assert queued[0].dedupe_key == RETENTION_DEDUPE_KEY

    async def test_failure_raises_but_still_reschedules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非权限原因失败 → 抛 RuntimeError（让 Runner 退避重试），但仍排次日。

        不排次日会让一次失败把清理永久停在原地，数据继续超期。
        """
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        _seed_rules(database, settings, 1)
        database.failures["usage_event"] = RuntimeError("disk full")
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)
        store = InMemoryJobStore()
        job = JobRecord(
            tenant_id=TENANT,
            job_id="job_retention_unit",
            kind=RETENTION_KIND,
            dedupe_key=RETENTION_DEDUPE_KEY,
            attempts=1,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await handle_retention_cleanup(job, settings=settings, store=store)
        assert "failed on 1 table" in str(exc_info.value)

        queued = await store.list_by_status(TENANT, "queued")
        assert len(queued) == 1, "失败后也必须自续期"
        failed = spy.named("retention_cleanup_failed")
        assert failed
        level, fields = failed[0]
        assert level == "warning", "第一次失败是 WARNING，不是 ERROR"
        assert fields["alert"] is False
        assert fields["failed_tables"] == ["usage_event"]

    async def test_repeated_failure_escalates_to_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连续失败（`attempts >= 2`）升级为 ERROR 并置 `alert=True`（告警口径）。"""
        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        settings = _settings()
        database.failures["usage_event"] = RuntimeError("disk full")
        database.failures["audit_log"] = _privilege_error()
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)
        job = JobRecord(
            tenant_id=TENANT,
            job_id="job_retention_unit",
            kind=RETENTION_KIND,
            dedupe_key=RETENTION_DEDUPE_KEY,
            attempts=ALERT_AFTER_FAILURES,
        )

        with pytest.raises(RuntimeError):
            await handle_retention_cleanup(job, settings=settings, store=InMemoryJobStore())

        level, fields = spy.named("retention_cleanup_failed")[0]
        assert level == "error"
        assert fields["alert"] is True
        assert fields["attempts"] == ALERT_AFTER_FAILURES

    async def test_register_actually_dispatches_through_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """注册后 Runner 能真的跑起来：tick 处理 1 条、任务成功、并自续期一条新任务。

        只断言"注册表里有这个 kind"抓不到 `partial(...)` 参数绑错（例如 store 传丢，
        自续期会排到默认存储里，生产上表现为"清理只跑一次就再也不跑了"）。
        """
        from app.workers.job_runner import JobRunner

        database = install_tenant_scope(monkeypatch, retention_cleanup, FakeDatabase())
        database.failures["audit_log"] = _privilege_error()
        settings = _settings()
        store = InMemoryJobStore()
        runner = JobRunner(settings=settings, store=store)
        register_retention_cleanup(runner, settings, store=store)

        assert runner.registered_kinds() == [RETENTION_KIND]
        past = datetime.now(UTC) - timedelta(minutes=1)
        job = await store.enqueue(
            JobRecord(
                tenant_id=TENANT,
                kind=RETENTION_KIND,
                dedupe_key="probe-once",
                max_attempts=RETENTION_MAX_ATTEMPTS,
                run_after=iso(past),
            )
        )
        assert job is not None

        assert await runner.tick() == 1

        finished = await store.get(TENANT, job.job_id)
        assert finished is not None
        assert finished.status == "succeeded"
        queued = await store.list_by_status(TENANT, "queued")
        assert [item.dedupe_key for item in queued] == [RETENTION_DEDUPE_KEY]
