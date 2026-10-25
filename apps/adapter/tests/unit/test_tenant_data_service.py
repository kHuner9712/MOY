"""租户数据权利服务单元测试（架构 §4.2 TD1/TD2、PRD P4-05 ③④）。

## 这一组用例在防什么风险

1. **导出漏列 / 多列**：客户拿导出包去复算账单或做合规审计，少一列等于证据缺失，
   多一列（比如把记忆向量带出去）等于超范围披露。用例对 JSONL 的**键集逐字比对**
   `EXPORT_SOURCES`，任何一侧偏移都会红。
2. **静默截断**：超上限必须是业务错误。用例同时断言"没有产物落地"——只断言报错码
   不足以证明没留下一份缺数据的包。
3. **误删**：二次确认必须逐字比对；不匹配时**一行都不能删**（只断言 18003 不够）。
4. **删除证明变成第二份数据副本**：证明里只能有行数与时间戳。用例把种子里的业务
   正文整串搜一遍。
5. **证明活不过下载链接**：证明的留存期必须长于下载令牌 TTL，否则"我删过了"这件事
   在客户回头核查时已经无从举证。

## 口径说明

`tenant_scope` 由 `tests/unit/fake_tenant_scope.py` 换成内存替身（本机无测试库）。
替身不解释 `WHERE` 谓词，因此"哪一行过期 / 哪一行可见"这类 SQL 层事实仍由
`tests/integration/test_retention_export_delete.py` 在真库上覆盖（本机 skip）。
本文件断言的是**编排行为**：语句打到哪张表、分页推进、zip 内容、返回值、副作用。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.business import tenant_data_service
from app.business.audit_service import get_audit_recorder
from app.business.tenant_data_service import (
    ALWAYS_DELETE_TABLES,
    EXPORT_FILE_NAMES,
    EXPORT_SCOPES,
    EXPORT_SOURCES,
    MANIFEST_NAME,
    PAGE_SIZE,
    PURPOSE_DELETION_PROOF,
    PURPOSE_EXPORT,
    RETAINED_TABLES,
    ExportSource,
    TenantDataService,
    dump_line,
    get_tenant_data_service,
    iso,
    normalize_scopes,
)
from app.core.artifact_token import parse_token
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.tenant_data import EXPORT_SCOPES as CORE_EXPORT_SCOPES
from app.db.repositories.artifact_repo import InMemoryArtifactStore
from tests.unit.fake_tenant_scope import FakeDatabase, install_tenant_scope

pytestmark = pytest.mark.unit

TENANT = "tnt_td_unit"
TENANT_NAME = "麦子科技（单测）有限公司"
ACTOR = "usr_owner"

#: 种子里的业务正文（删除证明里**一处都不许出现**）
SEED_MESSAGE_TEXT = "帮我整理季度汇报"
SEED_MEMORY_TEXT = "汇报要表格式"

_CRED_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

#: 单测用配置（`business_store=memory`：审计落内存记录器，不连库）
_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "tenant-data-unit-secret",
    "credential_encryption_key": _CRED_KEY,
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


def _privilege_error() -> BaseException:
    """构造一个"应用角色无 DELETE 权限"的异常（SQLSTATE 42501）。

    Returns:
        BaseException: 带 `pgcode` 的异常（沿异常链也认，见 `is_insufficient_privilege`）。
    """

    class InsufficientPrivilegeError(Exception):
        pgcode = "42501"

    return InsufficientPrivilegeError("permission denied for table audit_log")


# ===========================================================================
# 常量口径（审计 P1-5 下沉后的防腐断言）
# ===========================================================================
def test_export_file_names_match_export_scopes() -> None:
    """`EXPORT_FILE_NAMES` 与 `EXPORT_SCOPES` 必须同集合、且与 core 同源。

    为什么把它们钉在一起：`EXPORT_SCOPES`（可导出的类别）已下沉
    `app.core.tenant_data`（DTO 与前端共用的**契约词汇**），而
    `EXPORT_FILE_NAMES`（类别 → zip 内文件名）**故意留在业务层**，因为它是
    打包的**实现细节**。两者一旦漂移，请求里合法的 scope 会在打包时
    `KeyError`，或多出一个永远导不出的类别——这类缺陷只在真正导出时才炸。

    同时钉住 re-export 的**同一性**：业务层与服务层读到的必须是**同一个对象**，
    否则说明有人在业务层又复制了一份常量，`schemas` 与 `service` 会各读一份。
    """
    assert set(EXPORT_FILE_NAMES) == set(EXPORT_SCOPES)
    assert EXPORT_SCOPES is CORE_EXPORT_SCOPES
    assert tenant_data_service.EXPORT_SCOPES is CORE_EXPORT_SCOPES
    # 顺序也是契约：请求缺省时按固定顺序输出，清单顺序便于人工核对。
    assert tuple(EXPORT_SCOPES) == tuple(EXPORT_FILE_NAMES)


# ===========================================================================
# 造数据
# ===========================================================================
def _row(source: ExportSource, index: int, *, tenant_id: str = TENANT) -> dict[str, Any]:
    """按取数定义造一行（列集与 `source.columns` 完全一致）。

    Args:
        source: 取数定义。
        index: 行号（决定时间与 ID，保证键集有序）。
        tenant_id: 租户 ID。

    Returns:
        dict[str, Any]: 行。
    """
    base = datetime(2026, 1, 1, tzinfo=UTC)
    row: dict[str, Any] = {column: f"{source.table}.{column}.{index}" for column in source.columns}
    row["tenant_id"] = tenant_id
    row[source.timestamp_column] = base + timedelta(seconds=index)
    row[source.id_column] = index if source.id_cast == "bigint" else f"id_{index:05d}"
    return row


def _seed_scope(database: FakeDatabase, scope: str, count: int) -> int:
    """给某类别造 `count` 行（多表类别按表均分）。

    Args:
        database: 内存库。
        scope: 类别。
        count: 期望总行数（平摊到该类别下的每张表）。

    Returns:
        int: 实际写入的总行数。
    """
    sources = [item for item in EXPORT_SOURCES if item.scope == scope]
    written = 0
    for source in sources:
        database.seed(source.table, [_row(source, index) for index in range(count)])
        written += count
    return written


def _seed_tenant(database: FakeDatabase, *, status: str = "active") -> None:
    """写入企业行（TD2 二次确认的比对基准）。

    Args:
        database: 内存库。
        status: 状态。
    """
    database.seed(
        "tenant",
        [{"tenant_id": TENANT, "name": TENANT_NAME, "status": status}],
    )


def _seed_delete_targets(database: FakeDatabase) -> dict[str, int]:
    """写入 TD2 各表的数据，返回"表 → 行数"（供逐表断言）。

    Args:
        database: 内存库。

    Returns:
        dict[str, int]: 表名 → 行数。
    """
    rows: dict[str, list[dict[str, Any]]] = {
        "message": [{"tenant_id": TENANT, "content": SEED_MESSAGE_TEXT}],
        "conversation": [{"tenant_id": TENANT, "conversation_id": "conv_1"}],
        "user_memory": [{"tenant_id": TENANT, "content": SEED_MEMORY_TEXT}],
        "memory_embedding": [{"tenant_id": TENANT, "memory_id": "mem_1"}],
        "user_memory_setting": [{"tenant_id": TENANT, "user_id": ACTOR}],
        "plugin_artifact": [{"tenant_id": TENANT, "artifact_id": "art_old"}],
        "plugin_task_step": [{"tenant_id": TENANT, "step_id": "step_old"}],
        "plugin_task": [{"tenant_id": TENANT, "task_id": "task_old"}],
        "usage_event": [{"tenant_id": TENANT, "event_id": "evt_1"}],
        "usage_monthly": [{"tenant_id": TENANT, "period": "2026-01"}],
        "tool_execution": [{"tenant_id": TENANT, "execution_id": "exec_1"}],
        "payment_callback_log": [{"tenant_id": TENANT, "callback_id": "cb_1"}],
        "integration_outbox": [{"tenant_id": TENANT, "outbox_id": "obx_1"}],
        "background_job": [{"tenant_id": TENANT, "job_id": "job_1"}],
        "credential_store": [{"tenant_id": TENANT, "credential_ref": "cred_1"}],
        "audit_log": [
            {"tenant_id": TENANT, "action": "tenant_data.export"},
            {"tenant_id": TENANT, "action": "knowledge_base.create"},
        ],
    }
    for table, items in rows.items():
        database.seed(table, items)
    return {table: len(items) for table, items in rows.items()}


# ===========================================================================
# 纯函数与常量（不需要库）
# ===========================================================================
class TestScopeNormalization:
    """`scope[]` 的规范化（TD1/TD2 共用）。"""

    def test_missing_scope_means_all_in_fixed_order(self) -> None:
        """缺省全选，且顺序稳定（响应里的 rows 键序因此可预期）。"""
        assert normalize_scopes(None) == EXPORT_SCOPES
        assert normalize_scopes([]) == EXPORT_SCOPES

    def test_unknown_scope_is_business_error(self) -> None:
        """未知类别返回 20001，而不是被静默忽略（静默忽略 = 导出包少一块）。"""
        with pytest.raises(ApiError) as exc_info:
            normalize_scopes(["usage", "everything"])
        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        assert exc_info.value.internal_detail is not None
        assert "everything" in exc_info.value.internal_detail

    def test_duplicate_scope_is_deduped_and_ordered(self) -> None:
        """重复项去重，且按 `EXPORT_SCOPES` 的固定顺序返回（不随入参顺序漂移）。"""
        assert normalize_scopes(["memory", "audit", "memory"]) == ("audit", "memory")


class TestSerialization:
    """`dump_line` 与 `iso`（导出包的字面格式）。"""

    def test_dump_line_serializes_non_native_types(self) -> None:
        """时间/日期/Decimal/二进制都要能进 JSONL，且各有确定形态。"""
        line = dump_line(
            {
                "at": datetime(2026, 3, 1, 8, 30, tzinfo=UTC),
                "day": date(2026, 3, 1),
                "amount": Decimal("12.30"),
                "blob": b"secret-bytes",
            }
        )
        payload = json.loads(line)
        assert payload["at"] == "2026-03-01T08:30:00Z"
        assert payload["day"] == "2026-03-01"
        assert payload["amount"] == "12.30"
        # 二进制**不进包**：只留摘要，且原文不可还原
        assert payload["blob"] == hashlib.sha256(b"secret-bytes").hexdigest()
        assert "secret-bytes" not in line

    def test_dump_line_sorts_keys_for_stable_hash(self) -> None:
        """键排序让同一份数据每次产出同一条字节串（sha256 可复算）。"""
        left = dump_line({"b": 1, "a": 2})
        right = dump_line({"a": 2, "b": 1})
        assert left == right == '{"a":2,"b":1}'

    def test_dump_line_falls_back_to_str_for_unknown_types(self) -> None:
        """未建模的类型退化为字符串，而不是让整包导出失败（单行坏值不拖垮 200k 行）。"""
        payload = json.loads(dump_line({"tags": {"a", "b"}}))
        assert isinstance(payload["tags"], str)
        assert "a" in payload["tags"]

    def test_iso_is_utc_with_z_suffix(self) -> None:
        """时间统一写成 UTC + `Z`（跨时区客户按同一口径核对）。"""
        moment = datetime(2026, 3, 1, 16, 0, tzinfo=UTC)
        assert iso(moment) == "2026-03-01T16:00:00Z"

    def test_export_source_file_name_follows_scope(self) -> None:
        """文件名由类别映射，同一类别下的多张表进同一个文件。"""
        sources = {source.table: source for source in EXPORT_SOURCES}
        assert sources["conversation"].file_name == "chat.jsonl"
        assert sources["message"].file_name == "chat.jsonl"
        assert sources["audit_log"].file_name == "audit.jsonl"
        assert EXPORT_FILE_NAMES["memory"] == "memory.jsonl"


class TestPrivateDeployment:
    """私有化形态下在线导出/删除对终端用户关闭（PRD P4-05 ⑤）。"""

    @pytest.mark.parametrize("action", ["export", "delete"])
    def test_private_mode_rejects_online_operations(self, action: str) -> None:
        """私有化模式返回 20001，并给出"联系运维执行全库脚本"的可操作文案。"""
        service = TenantDataService(_settings(deploy_mode="private"))
        with pytest.raises(ApiError) as exc_info:
            service.ensure_online_allowed(action)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        assert "运维" in exc_info.value.message

    async def test_private_mode_export_does_not_touch_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """私有化模式导出必须**在取数之前**就失败（一次 count 都不该发）。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(deploy_mode="private"), artifacts=store)

        with pytest.raises(ApiError) as exc_info:
            await service.export(TENANT)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        assert database.statements == []
        assert await store.get(TENANT, "exp_any") is None


# ===========================================================================
# TD1 导出
# ===========================================================================
class TestExport:
    """同步导出：zip + 清单 + 短期下载地址。"""

    async def test_export_builds_five_files_with_faithful_manifest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """五类文件齐全；清单的行数/字节/sha256 与文件实际内容逐项一致。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        expected: dict[str, int] = {}
        for scope in EXPORT_SCOPES:
            expected[scope] = _seed_scope(database, scope, 2)
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(), artifacts=store)

        result = await service.export(TENANT, actor_id=ACTOR)

        assert result.status == "ready"
        assert result.export_id.startswith("exp_")
        assert set(result.rows) == {*EXPORT_SCOPES, "total"}
        for scope in EXPORT_SCOPES:
            assert result.rows[scope] == expected[scope], f"{scope} 行数不符"
        assert result.rows["total"] == sum(expected.values())

        artifact = await store.get(TENANT, result.export_id)
        assert artifact is not None
        assert artifact.content_type == "application/zip"
        assert artifact.size_bytes == len(artifact.content) == result.size_bytes
        assert artifact.output_key == PURPOSE_EXPORT

        archive = zipfile.ZipFile(io.BytesIO(artifact.content))
        assert set(archive.namelist()) == {*EXPORT_FILE_NAMES.values(), MANIFEST_NAME}

        manifest = json.loads(archive.read(MANIFEST_NAME))
        assert manifest["export_id"] == result.export_id
        assert manifest["tenant_id"] == TENANT
        assert manifest["scopes"] == list(EXPORT_SCOPES)
        assert manifest["total_rows"] == result.rows["total"]
        for name in EXPORT_FILE_NAMES.values():
            raw = archive.read(name)
            lines = [line for line in raw.decode("utf-8").splitlines() if line]
            assert manifest["rows"][name] == len(lines)
            assert manifest["files"][name]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert manifest["files"][name]["bytes"] == len(raw)

    async def test_export_lines_expose_exactly_declared_columns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """每行的键集 == `EXPORT_SOURCES` 声明的列（+ chat 的 record）。

        少一列是证据缺失，多一列是超范围披露（比如把记忆向量带出去），
        因此这里用集合**相等**而不是"包含"。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        for scope in EXPORT_SCOPES:
            _seed_scope(database, scope, 1)
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(), artifacts=store)

        result = await service.export(TENANT, actor_id=ACTOR)
        artifact = await store.get(TENANT, result.export_id)
        assert artifact is not None
        archive = zipfile.ZipFile(io.BytesIO(artifact.content))

        for scope in EXPORT_SCOPES:
            declared: dict[str, set[str]] = {}
            for source in EXPORT_SOURCES:
                if source.scope != scope:
                    continue
                declared[source.record] = set(source.columns) | (
                    {"record"} if source.record else set()
                )
            payloads = [
                json.loads(line)
                for line in archive.read(EXPORT_FILE_NAMES[scope]).decode("utf-8").splitlines()
                if line
            ]
            assert payloads, f"{scope}.jsonl 不应为空"
            for payload in payloads:
                owner = payload.get("record", "")
                assert owner in declared, f"{scope}.jsonl 出现未定义的 record={owner!r}"
                assert set(payload) == declared[owner], f"{scope}.jsonl 的键集与取数定义不符"
        # chat 一个文件里混两张表：必须靠 record 区分，且两类都在
        chat_records = {
            json.loads(line)["record"]
            for line in archive.read("chat.jsonl").decode("utf-8").splitlines()
            if line
        }
        assert chat_records == {"conversation", "message"}

    async def test_export_pages_through_more_than_one_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """超过单页行数时按 keyset 游标继续取下一页（游标真的推进了）。

        `PAGE_SIZE + 2` 行必须产出 `PAGE_SIZE + 2` 行，而不是被静默截断到一页。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        total_rows = PAGE_SIZE + 2
        source = next(item for item in EXPORT_SOURCES if item.scope == "usage")
        database.seed(source.table, [_row(source, index) for index in range(total_rows)])
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(), artifacts=store)

        result = await service.export(TENANT, ["usage"], actor_id=ACTOR)

        assert result.rows["usage"] == total_rows
        pages = [
            params
            for statement, params in database.statements
            if "ORDER BY t0.occurred_at, t0.event_id" in statement
        ]
        assert len(pages) == 2, "应恰好取两页（第二页不足一页即结束）"
        # 第一页的游标是纪元起点，第二页的游标必须已经推进到第一页最后一行
        assert pages[1]["cursor_id"] == f"id_{PAGE_SIZE - 1:05d}"
        assert pages[1]["cursor_ts"] > pages[0]["cursor_ts"]
        assert pages[1]["page_size"] == PAGE_SIZE

    async def test_zip_writes_go_through_thread_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """zip 写入必须经 `asyncio.to_thread`（审计 P2-5：不许卡事件循环）。

        为什么这条用例不可省：`ZipFile.open()` 句柄上的 `write()` 是**同步阻塞**
        调用（deflate 压缩 + 落盘）。写在 `async for` 循环体里，20 万行导出期间
        事件循环整体停摆，同进程的健康检查/其它请求一起超时——**功能测试全绿**，
        故障只在线上并发时暴露。

        本用例只钉「写入路径经过了线程池」这一机械事实（不验证真实并发性能）：
        把 `asyncio.to_thread` 换成同步直调的同签名间谍，断言

        1. 被调用次数 == `ceil(行数 / _ZIP_CHUNK_ROWS)`（攒批粒度生效）；
        2. 每批不超过 `_ZIP_CHUNK_ROWS` 行；
        3. zip 内容与清单 sha256 仍与实际字节一致（换成异步写不能改变产物）。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        total_rows = 1200  # > 2 × _ZIP_CHUNK_ROWS，能覆盖「整批 + 尾批」两条路径
        source = next(item for item in EXPORT_SOURCES if item.scope == "usage")
        database.seed(source.table, [_row(source, index) for index in range(total_rows)])

        batches: list[int] = []
        real_to_thread = asyncio.to_thread

        async def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
            """记录每批字节数后**同步**执行（本用例不验证并发，只验证路径）。"""
            batches.append(len(args[0]) if args else 0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        try:
            store = InMemoryArtifactStore()
            service = TenantDataService(_settings(), artifacts=store)
            result = await service.export(TENANT, ["usage"], actor_id=ACTOR)
        finally:
            monkeypatch.setattr(asyncio, "to_thread", real_to_thread)

        assert result.rows["usage"] == total_rows
        # 攒批粒度是本用例的被测事实，必须读实现里的那个常量（SLF001 显式豁免）。
        chunk = tenant_data_service._ZIP_CHUNK_ROWS  # noqa: SLF001 - 断言攒批粒度
        assert chunk > 0
        assert len(batches) == -(-total_rows // chunk), "写入批次应与攒批粒度一致"
        assert all(size > 0 for size in batches), batches

        artifact = await store.get(TENANT, result.export_id)
        assert artifact is not None
        archive = zipfile.ZipFile(io.BytesIO(artifact.content))
        raw = archive.read(EXPORT_FILE_NAMES["usage"])
        assert len([line for line in raw.decode("utf-8").splitlines() if line]) == total_rows
        manifest = json.loads(archive.read(MANIFEST_NAME))
        assert manifest["files"][EXPORT_FILE_NAMES["usage"]]["sha256"] == (
            hashlib.sha256(raw).hexdigest()
        )

    async def test_export_over_limit_is_business_error_without_artifact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """超上限 → 20001 + 指定文案，且**没有产物落地**（绝不静默截断）。"""
        monkeypatch.setattr(tenant_data_service, "MAX_EXPORT_ROWS", 2)
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        source = next(item for item in EXPORT_SOURCES if item.scope == "usage")
        database.seed(source.table, [_row(source, index) for index in range(3)])
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        with pytest.raises(ApiError) as exc_info:
            await service.export(TENANT, ["usage"], actor_id=ACTOR)

        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        assert "数据量较大" in exc_info.value.message
        # 宿主任务是"落产物"的第一步：它没被建，说明整条写入路径没启动
        assert database.rows("plugin_task", TENANT) == [], "超限时不得留下任何一个导出包"
        assert not [item for item in database.statements if "INSERT" in item[0]]

    async def test_export_detects_rows_appearing_during_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """统计之后又写入的新行导致流式超限时，仍然报错而不是产出残缺包。

        这条覆盖"边导边写"：`count(*)` 说 2 行（未超限），取数时却拿到 5 行。
        若实现只靠前置 count 判定，这里就会产出一个超限的包。
        """
        monkeypatch.setattr(tenant_data_service, "MAX_EXPORT_ROWS", 2)
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        source = next(item for item in EXPORT_SOURCES if item.scope == "usage")
        database.seed(source.table, [_row(source, index) for index in range(2)])
        database.racing_rows[source.table] = [_row(source, index) for index in range(2, 5)]
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        with pytest.raises(ApiError) as exc_info:
            await service.export(TENANT, ["usage"], actor_id=ACTOR)

        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        assert "streaming" in (exc_info.value.internal_detail or "")
        assert database.rows("plugin_task", TENANT) == [], "流式中途失败不得留下残缺包"

    async def test_export_download_url_carries_tenant_and_artifact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """下载地址是**签名令牌**，解出来必须是本租户 + 本次导出 ID + 版本 1。

        这条断言绑在"地址能不能用"上：前缀写错（例如照抄 `plugin_artifact_download_prefix`）
        会让响应里的地址 404，而只断言 `startswith` 抓不到版本/租户错位。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        _seed_scope(database, "usage", 1)
        settings = _settings()
        service = TenantDataService(settings, artifacts=InMemoryArtifactStore())

        result = await service.export(TENANT, ["usage"], actor_id=ACTOR)

        assert result.download_url.startswith("/files/artifacts/")
        token = result.download_url.rsplit("/", 1)[-1]
        payload = parse_token(token, settings)
        assert payload.tenant_id == TENANT
        assert payload.artifact_id == result.export_id
        assert payload.version == 1

    async def test_export_expiry_is_short_lived_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导出包按**下载令牌 TTL** 过期（默认 15 分钟），不是 180 天。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        _seed_scope(database, "usage", 1)
        store = InMemoryArtifactStore()
        settings = _settings(artifact_token_ttl_seconds=60)
        service = TenantDataService(settings, artifacts=store)

        result = await service.export(TENANT, ["usage"], actor_id=ACTOR)

        artifact = await store.get(TENANT, result.export_id)
        assert artifact is not None
        expires = datetime.fromisoformat(artifact.expires_at.replace("Z", "+00:00"))
        assert expires < datetime.now(UTC) + timedelta(minutes=5)

    async def test_export_records_audit_without_business_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导出必须留痕，且摘要里**只有条数与范围**（正文一律不进审计）。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        _seed_scope(database, "usage", 2)
        settings = _settings()
        service = TenantDataService(settings, artifacts=InMemoryArtifactStore())

        result = await service.export(TENANT, ["usage"], actor_id=ACTOR)

        entries = await get_audit_recorder(settings).entries_for(TENANT)
        exports = [item for item in entries if item.action == "tenant_data.export"]
        assert len(exports) == 1
        entry = exports[0]
        assert entry.resource_id == result.export_id
        assert entry.actor_id == ACTOR
        assert set(entry.change_summary) == {"scopes", "rows", "size_bytes"}
        assert entry.change_summary["scopes"] == ["usage"]
        assert entry.change_summary["size_bytes"] == result.size_bytes
        assert SEED_MESSAGE_TEXT not in json.dumps(entry.change_summary, ensure_ascii=False)

    async def test_store_is_lazily_resolved_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未注入产物存储时按配置取单例（延迟取值，构造期不建引擎）。"""
        install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        lazy_store = InMemoryArtifactStore()
        monkeypatch.setattr(tenant_data_service, "get_artifact_store", lambda _settings: lazy_store)
        service = TenantDataService(_settings())

        assert service.settings.env == "test"
        assert service._store() is lazy_store  # noqa: SLF001

    def test_factory_keeps_given_settings(self) -> None:
        """工厂把配置带进服务（否则端点用 A 配置、服务用全局单例）。"""
        settings = _settings()
        service = get_tenant_data_service(settings)
        assert service.settings is settings


# ===========================================================================
# TD2 删除
# ===========================================================================
class TestDelete:
    """二次确认 + 分批硬删 + 两段式删除证明。"""

    async def test_delete_wipes_tables_and_reports_two_stages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """逐表归零；应用侧行数与"待 DBA"的审计行数**分开呈现**。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        seeded = _seed_delete_targets(database)
        database.failures["audit_log"] = _privilege_error()
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(), artifacts=store)

        result = await service.delete(TENANT, TENANT_NAME, actor_id=ACTOR)

        assert result.deletion_id.startswith("del_")
        # 应用侧：除 audit_log 与"证明的宿主任务"外全部归零
        for table in seeded:
            if table in {"audit_log", "plugin_task"}:
                continue
            assert database.row_count(table, TENANT) == 0, f"{table} 未清空"
        # `plugin_task` 只剩 1 行 —— 它就是删除证明的宿主任务本身（不是残留数据）
        host_rows = database.rows("plugin_task", TENANT)
        assert len(host_rows) == 1
        assert host_rows[0]["task_id"] == result.deletion_id
        assert result.skipped_tables == ["audit_log"]
        assert "audit_log" not in result.deleted_tables
        assert result.deleted_rows == {
            table: count for table, count in seeded.items() if table != "audit_log"
        }
        # 两段式：审计行单列，且不等于应用侧总数
        assert result.audit_pending_dba == seeded["audit_log"]
        assert result.audit_pending_dba > 0
        assert sum(result.deleted_rows.values()) > 0
        assert result.audit_sql_hint.startswith("DELETE FROM audit_log")
        assert result.retained_tables == list(RETAINED_TABLES)

    async def test_delete_batches_never_exceed_page_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """单张表超过一批时按批硬删（每批 ≤ PAGE_SIZE），总和等于原行数。

        一条 `DELETE` 删几十万行会长时间持锁并把 WAL 撑爆，所以分批是硬约束。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        total = PAGE_SIZE * 2 + 7
        database.seed("conversation", [{"tenant_id": TENANT, "n": index} for index in range(total)])
        database.failures["audit_log"] = _privilege_error()
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        result = await service.delete(TENANT, TENANT_NAME, ["chat"], actor_id=ACTOR)

        batches = database.delete_batches("conversation")
        assert batches == [PAGE_SIZE, PAGE_SIZE, 7]
        assert all(count <= PAGE_SIZE for count in batches)
        assert result.deleted_rows["conversation"] == total
        assert database.row_count("conversation", TENANT) == 0

    async def test_delete_scope_controls_touched_tables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`scope=["chat"]` 只删会话/消息 + 必须无条件删的两张表，用量数据不动。

        顺序也钉住：先子后父（message 在 conversation 之前），最后是 always 项。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        seeded = _seed_delete_targets(database)
        database.failures["audit_log"] = _privilege_error()
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        await service.delete(TENANT, TENANT_NAME, ["chat"], actor_id=ACTOR)

        assert database.touched_tables() == [
            "message",
            "conversation",
            *ALWAYS_DELETE_TABLES,
        ]
        assert database.row_count("usage_event", TENANT) == seeded["usage_event"]
        assert database.row_count("user_memory", TENANT) == seeded["user_memory"]

    async def test_delete_suspends_tenant_to_block_further_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """先停用再删：否则"边删边写"会让"已删除"的结论是假的。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        database.failures["audit_log"] = _privilege_error()
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        await service.delete(TENANT, TENANT_NAME, ["chat"], actor_id=ACTOR)

        row = database.rows("tenant", TENANT)[0]
        assert row["status"] == "suspended"
        assert row["suspended_reason"] == "数据删除请求处理中"

    async def test_confirm_text_mismatch_deletes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """确认文本不逐字一致 → 18003，且**一行都没删、企业也没被停用**。

        "少一个字 / 多一个空格"都是负样本：容错比对会让误删成为可能。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        seeded = _seed_delete_targets(database)
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        wrongs = [
            TENANT_NAME + " ",
            TENANT_NAME[:-1],
            TENANT_NAME.replace("（", "("),
            "  " + TENANT_NAME,
        ]
        for wrong in wrongs:
            assert wrong != TENANT_NAME, "负样本不能等于正样本（否则这条断言是假的）"
            with pytest.raises(ApiError) as exc_info:
                await service.delete(TENANT, wrong, actor_id=ACTOR)
            assert exc_info.value.code == ErrorCode.DELETE_CONFIRM_MISMATCH

        # 空串同样被拒（不允许"空即等于空"这种兜底）
        with pytest.raises(ApiError) as exc_info:
            await service.delete(TENANT, "", actor_id=ACTOR)
        assert exc_info.value.code == ErrorCode.DELETE_CONFIRM_MISMATCH

        for table, count in seeded.items():
            assert database.row_count(table, TENANT) == count, f"{table} 被误删"
        assert database.rows("tenant", TENANT)[0]["status"] == "active"
        assert database.delete_batches("conversation") == []

    async def test_unknown_tenant_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """企业不存在 → 20004（不能拿确认文本去"猜"一个不存在的企业）。"""
        install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        with pytest.raises(ApiError) as exc_info:
            await service.delete(TENANT, TENANT_NAME, actor_id=ACTOR)
        assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND

    async def test_non_privilege_failure_aborts_instead_of_being_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连接类错误**不得**被当成"跳过"——只有权限不足才是预期跳过。

        把任意异常都当"跳过"，会让真故障静默变成"清理完成"。
        """
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        _seed_delete_targets(database)
        database.failures["message"] = RuntimeError("connection reset by peer")
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        with pytest.raises(RuntimeError):
            await service.delete(TENANT, TENANT_NAME, ["chat"], actor_id=ACTOR)
        # 失败发生在删消息时：会话还活着，说明没有半途"跳过并继续"
        assert database.row_count("conversation", TENANT) == 1

    async def test_proof_is_the_only_remaining_artifact_and_holds_no_business_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """删除证明：可解析、两段式、留存期长、且不含任何被删数据的正文。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        seeded = _seed_delete_targets(database)
        database.failures["audit_log"] = _privilege_error()
        store = InMemoryArtifactStore()
        service = TenantDataService(_settings(), artifacts=store)

        result = await service.delete(TENANT, TENANT_NAME, actor_id=ACTOR)

        proof_row = await store.get(TENANT, result.deletion_id)
        assert proof_row is not None
        assert proof_row.content_type == "application/json"
        assert proof_row.output_key == PURPOSE_DELETION_PROOF
        proof = json.loads(proof_row.content)
        assert proof["proof_of_deletion"] is True
        assert proof["deletion_id"] == result.deletion_id
        assert proof["tenant_name"] == TENANT_NAME
        assert proof["requested_by"] == ACTOR
        assert proof["application_side"]["total_rows"] == sum(result.deleted_rows.values())
        assert proof["application_side"]["skipped_tables"] == ["audit_log"]
        assert proof["pending_dba"]["audit_log_rows"] == seeded["audit_log"]
        assert proof["retained_tables"] == list(RETAINED_TABLES)

        # 合规：证明里一处都不许出现原业务正文
        text = proof_row.content.decode("utf-8")
        for leaked in (SEED_MESSAGE_TEXT, SEED_MEMORY_TEXT):
            assert leaked not in text

        # 证明必须活得比下载链接久（默认 180 天 vs 15 分钟）
        expires = datetime.fromisoformat(proof_row.expires_at.replace("Z", "+00:00"))
        assert expires > datetime.now(UTC) + timedelta(days=1)

        # 宿主任务行：外键载体 + 用途标记，不含业务内容
        host = database.rows("plugin_task", TENANT)
        assert len(host) == 1
        assert host[0]["task_id"] == result.deletion_id
        assert host[0]["result_summary"] == PURPOSE_DELETION_PROOF
        assert host[0]["idem_key"] == result.deletion_id

    async def test_delete_twice_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """重复删除不报错；第二次只清掉上一次的证明宿主任务，业务表仍为 0。"""
        database = install_tenant_scope(monkeypatch, tenant_data_service, FakeDatabase())
        _seed_tenant(database)
        _seed_delete_targets(database)
        database.failures["audit_log"] = _privilege_error()
        service = TenantDataService(_settings(), artifacts=InMemoryArtifactStore())

        first = await service.delete(TENANT, TENANT_NAME, actor_id=ACTOR)
        second = await service.delete(TENANT, TENANT_NAME, actor_id=ACTOR)

        assert first.deletion_id != second.deletion_id
        # 上一次的证明宿主任务被清掉（证明表达"当前状态"，历史动作在审计里）
        assert second.deleted_rows["plugin_task"] == 1
        business_rows = {
            table: count for table, count in second.deleted_rows.items() if table != "plugin_task"
        }
        assert business_rows and all(count == 0 for count in business_rows.values())
        assert database.row_count("plugin_task", TENANT) == 1
        # 审计行仍在（应用角色删不掉），数量不变
        assert second.audit_pending_dba == first.audit_pending_dba
