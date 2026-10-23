# ruff: noqa: S608 - 表名/租户 ID 均为本文件常量（非外部输入），断言用直连 SQL 才看得见真值
"""数据权利验收：保留期清理 + TD1 导出 + TD2 删除（阶段四 T4-05.3）。

**为什么必须连真库**：本批的三件事全部只在 SQL 层成立——

1. `audit_log` 的 `REVOKE DELETE` 是**数据库权限**，内存替身永远"删得掉"；
2. 分批删除用 `ctid IN (SELECT ... LIMIT n)`，这是 PG 特有语法；
3. 删除证明里的行数、`audit_pending_dba` 都是 `count(*)` 的真值。

用应用角色（`NOSUPERUSER NOBYPASSRLS`）连库，否则 RLS 断言全是假绿。

判据（逐条对齐架构 §11 T4-05.3 与 PRD P4-05）：

① 保留期配 1 天并跑 `retention_cleanup` → 目标表过期行 `count=0`、日志含行数、
   **明确出现 `audit_log=skipped(no privilege)`**；
② 记忆**永久**：清理后 `user_memory` 一行不少；
③ 导出包解压后 5 类文件都在，`manifest.json` 的行数与文件实际行数一致；
④ 导出超限 → 业务错误 + 指定文案（**不静默截断**）；
⑤ 删除后应用侧逐表 `count=0`；证明与响应里 `audit_pending_dba > 0` 且
   与应用侧行数**分开呈现**；二次删除幂等；
⑥ 私有化模式下 TD1/TD2 返回业务错误（终端用户不可用）。
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.business import tenant_data_service
from app.business.tenant_data_service import (
    ALWAYS_DELETE_TABLES,
    EXPORT_FILE_NAMES,
    MANIFEST_NAME,
)
from app.core.config import Settings, reset_settings_cache
from app.db.engine import dispose_engine
from app.db.repositories.artifact_repo import reset_artifact_store
from app.db.repositories.job_repo import InMemoryJobStore, JobRecord
from app.main import create_app
from app.workers.tasks import retention_cleanup
from app.workers.tasks.retention_cleanup import (
    AUDIT_SKIP_MARKER,
    RETENTION_DEDUPE_KEY,
    build_rules,
    handle_retention_cleanup,
    next_daily_run_after,
    run_retention,
    subtract_months,
)
from tests.conftest import make_token

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 本次运行专用的企业（避免与其它文件互踩）
TENANT = f"tnt_td_{uuid.uuid4().hex[:8]}"

#: 企业名称（TD2 的二次确认基准，故意带空格与中文，验证"逐字比对"）
TENANT_NAME = "麦子科技（测试）有限公司"

#: 操作人（owner 角色才可删除）
OWNER = "usr_td_owner"

#: 普通管理员（有 `team:role:manage` 但不是 owner）
ORG_ADMIN = "usr_td_org_admin"

#: 32 字节密钥的 base64（凭据加密用，本文件只需要构造合法配置）
_CRED_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

#: 本文件会写入/清理的表
TABLES: tuple[str, ...] = (
    "message",
    "conversation",
    "memory_embedding",
    "user_memory",
    "user_memory_setting",
    "plugin_artifact",
    "plugin_task_step",
    "plugin_task",
    "integration_outbox",
    "payment_callback_log",
    "credential_store",
    "usage_event",
    "usage_monthly",
    "tool_execution",
    "background_job",
    "audit_log",
)

#: 删除范围里"必须归零"的表（含 always 项）。
#:
#: **不含** `plugin_task` / `plugin_artifact`：删除证明本身必须挂在一条宿主任务下
#: （`plugin_artifact.task_id` 是 NOT NULL 外键），因此这两张表删完各剩 1 行，
#: 就是证明自己。用例对它们单独断言"只剩证明"。
EXPECTED_EMPTY_AFTER_DELETE: tuple[str, ...] = (
    "message",
    "conversation",
    "user_memory",
    "memory_embedding",
    "user_memory_setting",
    "plugin_task_step",
    "integration_outbox",
    "payment_callback_log",
    "credential_store",
    "usage_event",
    "usage_monthly",
    "tool_execution",
    *ALWAYS_DELETE_TABLES,
)


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture(autouse=True)
async def _isolate(pg_dsn: str) -> AsyncIterator[None]:
    """用例前后清库 + 重置进程级单例 + 释放进程级引擎。

    引擎**前后都释放**：`get_engine()` 是进程级缓存，而 pytest-asyncio 给每个用例
    一个新事件循环；旧循环里的 asyncpg 连接留在池里，下一个用例取到就是
    `RuntimeError: Event loop is closed`（表现为"单独跑绿、一起跑红"）。

    产物存储单例也必须重置：`get_artifact_store()` 在**首次调用时**按当时的配置
    定型，若本文件之前的用例用 `business_store=memory` 建过它，导出的包就会写进
    内存而不落库，于是"删除后逐表 count=0 / 证明只剩 1 行"这类真值断言会假红
    （单独跑本文件时不会复现，只在全量跑时出现——最费时间的那类失败）。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        None: 用例运行期间。
    """
    reset_settings_cache()
    reset_artifact_store()
    await dispose_engine()
    await _cleanup(pg_dsn)
    yield
    await _cleanup(pg_dsn)
    await dispose_engine()
    reset_artifact_store()
    reset_settings_cache()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """指向测试库的 SaaS 配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置实例。
    """
    return build_settings(pg_dsn)


def build_settings(pg_dsn: str, **overrides: Any) -> Settings:
    """构造指向测试库的配置（保留期统一压到 1 天，便于造数据）。

    Args:
        pg_dsn: 测试库连接串。
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    params: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "saas",
        "business_store": "postgres",
        "database_url": pg_dsn,
        "auth_mode": "stub",
        "ratelimit_enabled": False,
        "credential_encryption_key": _CRED_KEY,
        "downstream_enabled": False,
        "jwt_secret": "tenant-data-secret",
        # 保留期：1 天 / 1 个月，测试里造"过期行"不必等半年
        "retention_audit_days": 1,
        "retention_usage_months": 1,
        "retention_task_days": 1,
        "retention_conversation_days": 1,
        "conversation_soft_delete_recover_days": 1,
        "background_jobs_enabled": False,
    }
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


@pytest.fixture
async def api(pg_settings: Settings) -> AsyncIterator[AsyncClient]:
    """指向真库的异步客户端（**不跑 lifespan**：这里不需要迁移与轮询）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[AsyncClient]: 客户端。
    """
    app = create_app(pg_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def private_api(pg_dsn: str) -> Iterator[TestClient]:
    """私有化模式的客户端（TD1/TD2 必须报业务错误）。

    **不进入 `with`**：`TestClient` 的 `__enter__` 会跑 `lifespan`（建引擎、跑迁移），
    本用例只断言错误码，不需要也不应该顺带初始化整套启动链路。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        Iterator[TestClient]: 同步客户端。
    """
    settings = build_settings(pg_dsn, deploy_mode="private")
    client = TestClient(create_app(settings))
    try:
        yield client
    finally:
        client.close()


# ===========================================================================
# 数据库直连助手（造数据 / 断言真值）
# ===========================================================================
async def _execute(pg_dsn: str, sql: str, *, tenant: str = TENANT) -> None:
    """以指定租户上下文执行一条写语句（绕过服务层，直接造真库数据）。

    Args:
        pg_dsn: 连接串。
        sql: 语句。
        tenant: 会话租户（RLS 上下文）。
    """
    connection = await asyncpg.connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", tenant)
        await connection.execute(sql)
    finally:
        await connection.close()


async def _scalar(pg_dsn: str, sql: str, *, tenant: str = TENANT) -> Any:
    """以指定租户上下文读取单值。

    Args:
        pg_dsn: 连接串。
        sql: 查询。
        tenant: 会话租户。

    Returns:
        Any: 标量值。
    """
    connection = await asyncpg.connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", tenant)
        return await connection.fetchval(sql)
    finally:
        await connection.close()


async def _count(pg_dsn: str, table: str) -> int:
    """数本用例企业在该表的行数（断言真值用）。

    Args:
        pg_dsn: 连接串。
        table: 表名（本文件常量）。

    Returns:
        int: 行数。
    """
    value = await _scalar(pg_dsn, f"SELECT count(*) FROM {table} WHERE tenant_id = '{TENANT}'")
    return int(value or 0)


async def _cleanup(pg_dsn: str) -> None:
    """清理本文件写入的行。

    **必须用超级用户**：应用角色对 `audit_log` 没有 `DELETE` 权限（这正是本批要
    证明的事实），用它清库会在夹具里就抛 `permission denied`。
    超级用户只出现在"清理"这一处，所有**断言**仍走应用角色——否则 RLS 断言是假绿。

    Args:
        pg_dsn: 连接串（应用角色；口令与超级用户相同，见 pg_fixture 的约定）。
    """
    dsn = _superuser_dsn(pg_dsn)
    for table in TABLES:
        await _execute(
            dsn, f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_td_%'", tenant="__platform__"
        )
    await _execute(dsn, "DELETE FROM tenant WHERE tenant_id LIKE 'tnt_td_%'", tenant="__platform__")


def _superuser_dsn(app_dsn: str) -> str:
    """把应用角色连接串换成超级用户连接串（仅用于清理）。

    与 `tests/integration/pg_fixture.py` 的派生约定一致：QA 容器里
    `officeai` 与 `officeai_app` 共用同一口令，只换用户名。

    Args:
        app_dsn: 应用角色连接串。

    Returns:
        str: 超级用户连接串（无法派生时原样返回，让失败显性）。
    """
    return app_dsn.replace("officeai_app:", "officeai:", 1)


async def _seed_tenant(pg_dsn: str) -> None:
    """建企业行（TD2 的确认文本基准）。

    Args:
        pg_dsn: 连接串。
    """
    await _execute(
        pg_dsn,
        "INSERT INTO tenant (tenant_id, name, status, deploy_mode, plan_code)"
        f" VALUES ('{TENANT}', '{TENANT_NAME}', 'active', 'saas', 'professional')"
        " ON CONFLICT (tenant_id) DO NOTHING",
    )


async def _seed_rows(pg_dsn: str, *, age_days: int) -> None:
    """按指定"年龄"写一套数据（5 类 + 运维流水 + 凭据 + 任务）。

    Args:
        pg_dsn: 连接串。
        age_days: 数据年龄（天）；0 表示"刚写入"。
    """
    offset = f"now() - interval '{age_days} days'"
    await _execute(
        pg_dsn,
        "INSERT INTO usage_event (tenant_id, event_id, user_id, capability, idem_key, units,"
        " period, source, occurred_at) VALUES"
        f" ('{TENANT}', 'evt_td_{age_days}_1', '{OWNER}', 'chat', 'idem_td_{age_days}', 3,"
        f" '2026-11', 'settled', {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO usage_monthly (tenant_id, period, scope_type, scope_id, capability, units)"
        f" VALUES ('{TENANT}', '2020-0{age_days % 9 + 1}', 'tenant', '{TENANT}', 'chat', 9)"
        " ON CONFLICT DO NOTHING",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO conversation (tenant_id, conversation_id, user_id, title,"
        " created_at, updated_at)"
        f" VALUES ('{TENANT}', 'conv_td_{age_days}', '{OWNER}', '季度汇报', {offset}, {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO message (tenant_id, message_id, conversation_id, user_id, role, content,"
        f" created_at, updated_at) VALUES ('{TENANT}', 'msg_td_{age_days}',"
        f" 'conv_td_{age_days}', '{OWNER}', 'user', '帮我整理季度汇报', {offset}, {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO user_memory (tenant_id, memory_id, user_id, category, content, confidence,"
        f" created_at, updated_at) VALUES ('{TENANT}', 'mem_td_{age_days}', '{OWNER}',"
        f" 'preference', '汇报要表格式', 'high', {offset}, {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO user_memory_setting (tenant_id, user_id, enabled, category_switches)"
        f" VALUES ('{TENANT}', '{OWNER}', true, '{{}}'::jsonb)"
        " ON CONFLICT (tenant_id, user_id) DO NOTHING",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO plugin_task (tenant_id, task_id, plugin_id, plugin_version, initiator_id,"
        f" trigger_type, status, progress, idem_key, started_at, finished_at, expires_at) VALUES"
        f" ('{TENANT}', 'task_td_{age_days}', 'plg_demo_plugin', '1.0.0', '{OWNER}',"
        f" 'manual', 'completed', 100, 'idem_task_td_{age_days}', {offset}, {offset}, {offset})",
    )
    # `expires_at` 一律给未来：产物的留存期是"任务完成 + 180 天"，
    # 造"刚写入"的数据时如果 expires_at 也是现在，它会被"已过期"规则一并清掉，
    # 于是"新数据不受影响"这条断言就失去意义了。
    await _execute(
        pg_dsn,
        "INSERT INTO plugin_artifact (tenant_id, artifact_id, task_id, step_id, output_key, type,"
        f" name, inline_text, expires_at, created_at) VALUES ('{TENANT}', 'art_td_{age_days}',"
        f" 'task_td_{age_days}', '', 'report', 'text', '季度汇报.md', '正文',"
        f" now() + interval '180 days', {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO integration_outbox (tenant_id, outbox_id, kind, payload,"
        " created_at, updated_at)"
        f" VALUES ('{TENANT}', 'obx_td_{age_days}', 'kc_group_upsert', '{{}}'::jsonb,"
        f" {offset}, {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO payment_callback_log (tenant_id, callback_id, channel, external_txn_id,"
        f" order_id, amount_cents, payload_sha256, received_at) VALUES ('{TENANT}',"
        f" 'cb_td_{age_days}', 'fake', 'txn_{age_days}', 'ord_td', 100,"
        f" '{age_days:064d}', {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO tool_execution (tenant_id, execution_id, conn_id, tool_type, operation,"
        f" status, operator_user_id, executed_at) VALUES ('{TENANT}', 'exec_td_{age_days}',"
        f" 'conn_td', 'http', 'GET', 'success', '{OWNER}', {offset})",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO credential_store (tenant_id, credential_ref, provider, cipher_text, nonce,"
        f" key_version, status, created_at) VALUES ('{TENANT}', 'cred_td_{age_days}', 'feishu',"
        " '\\x00'::bytea, '\\x00'::bytea, 1, 'active', " + offset + ")",
    )
    await _execute(
        pg_dsn,
        "INSERT INTO background_job (tenant_id, job_id, kind, dedupe_key, max_attempts)"
        f" VALUES ('{TENANT}', 'job_td_{age_days}', 'memory_extract', 'conv_td_{age_days}', 1)",
    )


class LoggerSpy:
    """记录 structlog 调用的替身（断言"日志里逐字出现某句话"）。

    为什么不抓 stdout：`structlog.PrintLoggerFactory(file=sys.stdout)` 在
    `configure()` 时就把当时那个 `sys.stdout` 对象绑死了，而 pytest 的 `capsys`
    替换的是**之后**的 `sys.stdout`——两者可能不是同一个对象，抓不到就是"假绿"。
    直接替换模块级 `logger` 才能确定性断言"代码确实发出了这条事件"。
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, str(event), dict(fields)))

    def debug(self, event: str, **fields: Any) -> None:
        self._record("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)

    def named(self, event: str) -> list[dict[str, Any]]:
        """取某事件的全部字段。

        Args:
            event: 事件名。

        Returns:
            list[dict[str, Any]]: 该事件的字段列表。
        """
        return [fields for _level, name, fields in self.events if name == event]


# ===========================================================================
# 纯函数（不需要库，但跟随本文件的 pg 标记）
# ===========================================================================
class TestRetentionRules:
    """保留期规则的纯逻辑（不碰库）。"""

    def test_subtract_months_handles_month_end(self) -> None:
        """按自然月回退，且"日"截到目标月最后一天（不能用 30 天近似）。"""
        assert subtract_months(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(
            2026, 2, 28, tzinfo=UTC
        )
        # 闰年
        assert subtract_months(datetime(2028, 3, 31, tzinfo=UTC), 1) == datetime(
            2028, 2, 29, tzinfo=UTC
        )
        # 跨年
        assert subtract_months(datetime(2026, 1, 15, tzinfo=UTC), 13) == datetime(
            2024, 12, 15, tzinfo=UTC
        )

    def test_next_daily_run_is_strictly_future(self) -> None:
        """次日 03:00 UTC 且严格大于 now（同一天 03:00 之后 → 明天）。"""
        early = next_daily_run_after(datetime(2026, 11, 2, 1, 0, tzinfo=UTC))
        assert early == datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
        late = next_daily_run_after(datetime(2026, 11, 2, 3, 0, tzinfo=UTC))
        assert late == datetime(2026, 11, 3, 3, 0, tzinfo=UTC)

    def test_rules_cover_all_five_tiers(self, pg_settings: Settings) -> None:
        """五档保留期都有规则；记忆标为永久；审计标为无权限。"""
        rules = {rule.table: rule for rule in build_rules(pg_settings)}
        assert rules["usage_event"].mode == "delete"
        assert rules["conversation"].mode == "delete"
        assert rules["plugin_task"].mode == "delete"
        assert rules["audit_log"].mode == "skip_no_privilege"
        assert rules["user_memory"].mode == "skip_permanent"
        assert rules["memory_embedding"].mode == "skip_permanent"

    def test_audit_skip_marker_is_verbatim(self) -> None:
        """跳过标记必须**逐字**等于架构 §10.4 写的那句（验收硬判据）。"""
        assert AUDIT_SKIP_MARKER == "audit_log=skipped(no privilege)"


# ===========================================================================
# ① 保留期清理
# ===========================================================================
class TestRetentionCleanup:
    """`retention_cleanup` 的真库行为。"""

    async def test_expired_rows_are_purged_and_audit_is_skipped(
        self, pg_dsn: str, pg_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判据 ①②：过期行清零、日志含行数、审计打印跳过标记、记忆不动。"""
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=40)
        await _seed_rows(pg_dsn, age_days=0)

        # 审计行由 DBA 负责，这里先插两条证明"应用侧删不掉"
        await _execute(
            pg_dsn,
            "INSERT INTO audit_log (tenant_id, actor_id, action, resource_type, occurred_at)"
            f" VALUES ('{TENANT}', '{OWNER}', 'tenant_data.export', 'tenant_data',"
            " now() - interval '40 days')",
        )

        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        report = await run_retention(TENANT, pg_settings)
        counts = report.counts()

        # ① 过期行清零，且**同表的新数据不受影响**
        assert await _count(pg_dsn, "usage_event") == 1
        assert await _count(pg_dsn, "conversation") == 1
        assert await _count(pg_dsn, "message") == 1
        assert counts["usage_event"] == 1
        assert counts["conversation"] == 1
        assert counts["message"] == 1
        assert counts["plugin_artifact"] == 1

        # ① 日志含行数（不是"跑完了"而已）
        cleaned = spy.named("retention_table_cleaned")
        assert cleaned, "缺少 retention_table_cleaned 事件"
        assert any(item["table"] == "usage_event" and item["deleted"] == 1 for item in cleaned)

        # ① 审计：状态是"跳过"而不是"失败"，且日志逐字出现那句话
        assert report.audit_skipped is True
        skipped_events = spy.named(AUDIT_SKIP_MARKER)
        assert skipped_events, f"日志里必须逐字出现 {AUDIT_SKIP_MARKER}"
        assert skipped_events[0]["table"] == "audit_log"
        # 无权限不算失败：报告里不能有 failed 项
        assert report.failed_tables == []

        # ① 审计行**确实还在**（应用角色删不掉）
        assert await _count(pg_dsn, "audit_log") == 1

        # ② 记忆永久：清理不动它
        assert await _count(pg_dsn, "user_memory") == 2
        permanent = spy.named("retention_table_skipped")
        assert any(
            item["table"] == "user_memory" and item["reason"] == "permanent" for item in permanent
        )

    async def test_cleanup_job_reschedules_and_reports(
        self, pg_dsn: str, pg_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务处理器：跑完把自己排到次日（自续期），并输出可核对的总行数。"""
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=40)
        spy = LoggerSpy()
        monkeypatch.setattr(retention_cleanup, "logger", spy)

        store = InMemoryJobStore()
        job = JobRecord(
            tenant_id=TENANT,
            job_id="job_retention_test",
            kind="retention_cleanup",
            dedupe_key=RETENTION_DEDUPE_KEY,
            attempts=1,
        )
        await handle_retention_cleanup(job, settings=pg_settings, store=store)

        done = spy.named("retention_cleanup_done")
        assert done, "缺少 retention_cleanup_done 事件"
        assert done[0]["deleted_total"] > 0
        assert done[0]["audit_skipped"] is True

        queued = await store.list_by_status(TENANT, "queued")
        assert len(queued) == 1
        assert queued[0].dedupe_key == RETENTION_DEDUPE_KEY
        assert queued[0].run_after > datetime.now(UTC).isoformat().replace("+00:00", "Z")[:19]


# ===========================================================================
# ③ ④ 导出
# ===========================================================================
class TestTenantExport:
    """TD1 导出（同步生成 + 短期下载链接）。"""

    async def test_export_zip_has_five_files_and_matching_manifest(
        self, pg_dsn: str, api: AsyncClient
    ) -> None:
        """判据 ③：5 类文件都在；manifest 行数/sha256 与文件实际内容一致；能下载解压。"""
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=0)

        response = await api.post(
            "/api/v1/ops/tenant-data/export",
            json={"scope": ["audit", "usage", "chat", "memory", "artifact"]},
            headers=_headers("export-1", path="POST"),
        )
        body = response.json()
        assert body["code"] == 0, body
        data = body["data"]
        assert data["status"] == "ready"
        assert data["export_id"].startswith("exp_")
        assert data["rows"]["usage"] == 1
        assert data["rows"]["chat"] == 2  # 1 会话 + 1 消息
        assert data["rows"]["memory"] == 1
        assert data["rows"]["artifact"] == 1
        assert data["rows"]["total"] == 5

        # 下载走既有产物代理（**没有新增端点**）
        download = await api.get(data["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/zip")
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        names = set(archive.namelist())
        assert names == {*EXPORT_FILE_NAMES.values(), MANIFEST_NAME}

        manifest = json.loads(archive.read(MANIFEST_NAME))
        assert manifest["export_id"] == data["export_id"]
        assert manifest["tenant_id"] == TENANT
        assert manifest["total_rows"] == data["rows"]["total"]
        for name in EXPORT_FILE_NAMES.values():
            raw = archive.read(name)
            lines = [line for line in raw.decode("utf-8").splitlines() if line]
            assert manifest["rows"][name] == len(lines), f"{name} 行数与清单不一致"
            assert manifest["files"][name]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert manifest["files"][name]["bytes"] == len(raw)

        # 内容抽查：会话与消息同文件、用 record 区分；记忆只含用户可见正文
        chat_lines = [
            json.loads(line) for line in archive.read("chat.jsonl").decode("utf-8").splitlines()
        ]
        assert {line["record"] for line in chat_lines} == {"conversation", "message"}
        memory_lines = [
            json.loads(line) for line in archive.read("memory.jsonl").decode("utf-8").splitlines()
        ]
        assert memory_lines[0]["content"] == "汇报要表格式"
        assert "embedding" not in memory_lines[0]

    async def test_export_over_limit_returns_business_error(
        self, pg_dsn: str, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判据 ④：超过上限返回业务错误 + 指定文案（**绝不静默截断**）。"""
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=0)
        monkeypatch.setattr(tenant_data_service, "MAX_EXPORT_ROWS", 2)

        response = await api.post(
            "/api/v1/ops/tenant-data/export",
            json={},
            headers=_headers("export-over", path="POST"),
        )
        body = response.json()
        assert body["code"] == 20001, body
        assert "数据量较大" in body["message"]
        assert response.status_code == 400

    async def test_export_requires_tenant_admin(self, pg_dsn: str, api: AsyncClient) -> None:
        """普通成员（无 `team:role:manage`）不能导出 → 20003。"""
        await _seed_tenant(pg_dsn)
        response = await api.post(
            "/api/v1/ops/tenant-data/export",
            json={},
            headers=_headers("export-member", path="POST", roles=["member"]),
        )
        assert response.json()["code"] == 20003
        assert response.status_code == 403

    async def test_export_rejects_platform_token(self, pg_dsn: str, api: AsyncClient) -> None:
        """运营账号（平台令牌）不能代表企业导出 → 20003（方向与 OPS1-OPS8 相反）。"""
        await _seed_tenant(pg_dsn)
        response = await api.post(
            "/api/v1/ops/tenant-data/export",
            json={},
            headers=_headers("export-platform", path="POST", tenant="__platform__", roles=["*"]),
        )
        assert response.json()["code"] == 20003


# ===========================================================================
# ⑤ 删除
# ===========================================================================
class TestTenantDelete:
    """TD2 删除（二次确认 + 两段式证明 + 幂等）。"""

    async def test_delete_wipes_tables_and_proves_it(self, pg_dsn: str, api: AsyncClient) -> None:
        """判据 ⑤：逐表归零、`audit_pending_dba>0`、证明可解析且行数一致。"""
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=0)

        response = await api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": TENANT_NAME},
            headers=_headers("delete-1", path="DELETE"),
        )
        body = response.json()
        assert body["code"] == 0, body
        data = body["data"]
        assert data["deletion_id"].startswith("del_")

        # 应用侧逐表归零
        for table in EXPECTED_EMPTY_AFTER_DELETE:
            remaining = await _count(pg_dsn, table)
            assert remaining == 0, f"{table} 仍有 {remaining} 行"

        # 任务与产物各剩 1 行：就是删除证明自己（它必须活着，否则客户拿不到证据）
        assert await _count(pg_dsn, "plugin_artifact") == 1
        assert await _count(pg_dsn, "plugin_task") == 1
        assert (
            await _scalar(
                pg_dsn,
                f"SELECT artifact_id FROM plugin_artifact WHERE tenant_id = '{TENANT}'",
            )
            == data["deletion_id"]
        )

        # 审计：删不掉，且**单列**在 audit_pending_dba（不与应用侧合并）
        assert data["audit_pending_dba"] > 0
        assert "audit_log" in data["skipped_tables"]
        assert "audit_log" not in data["deleted_tables"]
        assert data["audit_sql_hint"].startswith("DELETE FROM audit_log")
        assert await _count(pg_dsn, "audit_log") == data["audit_pending_dba"]

        # 证明文件可解析，行数与响应一致
        proof_response = await api.get(data["proof_download_url"])
        assert proof_response.status_code == 200
        proof = json.loads(proof_response.content)
        assert proof["deletion_id"] == data["deletion_id"]
        assert proof["tenant_name"] == TENANT_NAME
        assert proof["application_side"]["total_rows"] == sum(data["deleted_rows"].values())
        assert proof["pending_dba"]["audit_log_rows"] == data["audit_pending_dba"]
        # 两段式：应用侧与待 DBA **不合并**
        assert proof["application_side"]["total_rows"] > 0
        assert proof["pending_dba"]["audit_log_rows"] > 0
        assert proof["application_side"]["skipped_tables"] == ["audit_log"]

        # 企业被停用以阻断后续写入
        status = await _scalar(pg_dsn, f"SELECT status FROM tenant WHERE tenant_id = '{TENANT}'")
        assert status == "suspended"

    async def test_delete_is_idempotent_on_second_call(self, pg_dsn: str, api: AsyncClient) -> None:
        """判据 ⑤：二次删除不报错、业务数据仍为 0（幂等）。

        第二次会顺手清掉上一次的证明（证明表达"当前状态"，历史动作在审计里），
        因此只断言"业务数据不再存在"，不断言 `deleted_rows` 全为 0。
        """
        await _seed_tenant(pg_dsn)
        await _seed_rows(pg_dsn, age_days=0)
        first = await api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": TENANT_NAME},
            headers=_headers("delete-a", path="DELETE"),
        )
        assert first.json()["code"] == 0

        second = await api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": TENANT_NAME},
            headers=_headers("delete-b", path="DELETE"),
        )
        body = second.json()
        assert body["code"] == 0, body
        for table in ("conversation", "message", "usage_event", "user_memory", "credential_store"):
            assert await _count(pg_dsn, table) == 0
        assert body["data"]["audit_pending_dba"] >= 2  # 两次删除动作都留在审计里
        # 证明只有一份（本次的）
        assert await _count(pg_dsn, "plugin_artifact") == 1

    async def test_delete_requires_exact_tenant_name(self, pg_dsn: str, api: AsyncClient) -> None:
        """确认文本必须**逐字**一致：多一个空格、少一个字、全半角括号不同都拒绝。"""
        await _seed_tenant(pg_dsn)
        wrongs = (
            TENANT_NAME + " ",  # 尾部多一个空格
            TENANT_NAME[:-1],  # 少一个字
            TENANT_NAME.replace("（", "("),  # 全角括号换成半角
            "  " + TENANT_NAME,
        )
        for index, wrong in enumerate(wrongs):
            assert wrong != TENANT_NAME, "负样本不能等于正样本（否则这条断言是假的）"
            response = await api.request(
                "DELETE",
                "/api/v1/ops/tenant-data",
                json={"confirm_text": wrong},
                headers=_headers(f"delete-wrong-{index}", path="DELETE"),
            )
            body = response.json()
            assert body["code"] == 18003, (wrong, body)
            assert response.status_code == 400
            assert "企业名称" in body["message"]

        # 空串走请求体校验（不进入业务层），是 4xx 而不是静默通过。
        # 本仓统一异常处理器把 pydantic 的 422 归一成 400，故两种都算"被拦下"。
        empty = await api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": ""},
            headers=_headers("delete-empty", path="DELETE"),
        )
        assert empty.status_code in (400, 422)
        assert empty.json()["code"] != 0
        # 数据一条都没删
        assert await _count(pg_dsn, "tenant") == 1

    async def test_delete_requires_owner_role(self, pg_dsn: str, api: AsyncClient) -> None:
        """`org_admin` 有 `team:role:manage` 但**不是 owner** → 20003。"""
        await _seed_tenant(pg_dsn)
        response = await api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": TENANT_NAME},
            headers=_headers(
                "delete-org-admin", path="DELETE", roles=["org_admin"], user=ORG_ADMIN
            ),
        )
        assert response.json()["code"] == 20003
        assert await _count(pg_dsn, "tenant") == 1


# ===========================================================================
# ⑥ 私有化
# ===========================================================================
class TestPrivateDeployment:
    """私有化模式下 TD1/TD2 对终端用户不可用（PRD P4-05 ⑤）。"""

    def test_private_mode_rejects_export_and_delete(
        self, pg_dsn: str, private_api: TestClient
    ) -> None:
        """判据 ⑥：两个端点都返回业务错误，并给出"联系运维执行全库脚本"的文案。"""
        headers = _headers("private", path="POST")
        export = private_api.post("/api/v1/ops/tenant-data/export", json={}, headers=headers)
        body = export.json()
        assert body["code"] == 20001, body
        assert "部署形态" in body["message"]

        delete = private_api.request(
            "DELETE",
            "/api/v1/ops/tenant-data",
            json={"confirm_text": TENANT_NAME},
            headers=_headers("private", path="DELETE"),
        )
        assert delete.json()["code"] == 20001


# ===========================================================================
# 助手
# ===========================================================================
def _headers(
    tag: str,
    *,
    path: str,
    roles: list[str] | None = None,
    tenant: str = TENANT,
    user: str = OWNER,
) -> dict[str, str]:
    """构造鉴权 + 租户 + 幂等头。

    Args:
        tag: 幂等键标签（参与指纹，避免跨端点撞键）。
        path: 业务路径（写操作必须带 `Idempotency-Key`）。
        roles: 角色列表（缺省 owner）。
        tenant: 租户 ID。
        user: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    conf = Settings(env="test", jwt_secret="tenant-data-secret")
    token = make_token(settings=conf, tenant_id=tenant, user_id=user, roles=roles or ["owner"])
    digest = hashlib.sha256(f"{path}:{tag}".encode()).hexdigest()
    key = f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant,
        "Idempotency-Key": key,
    }


__all__: list[str] = []
