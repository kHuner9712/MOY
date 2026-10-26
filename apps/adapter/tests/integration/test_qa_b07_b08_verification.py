"""QA 独立验证用例（W2 / B07 计量同源 + B08 组织权限）。

**本文件的定位**：不重复工程师已有用例的断言，而是对
`docs/architecture-phase2.md` §9.2 B07/B08「完成后可验证」13 条与
`docs/qa-phase2-test-plan.md` TC-P2-USG-* / TC-P2-PERM-* 做**第三方复算**。
文件名与用例前缀 `b07_` / `b08_` / `perm_` 与报告 §4 的 13 条逐条判定一一对应。

**本文件不修改任何源码或既有用例**（W1 §5.6 的教训：用例口径由 QA 维护）。

编号映射（报告 §4）：

| 用例 | 验收点 |
| --- | --- |
| `test_b07_1_*` | ① 同一 `generation_id` 重放不产生第二条事件（三类能力全覆盖） |
| `test_b07_2_*` | ② 同刻 M4 `used_units` == BL5 `quota_units_used`（误差 0 + 同增量） |
| `test_b07_3_*` | ③ 按成员 breakdown 求和 == 企业总量（diff 严格 0） |
| `test_b07_4_*` | ④ 流式中途断开 `units ∈ (0, 完整值)`（两端开区间，真实断开） |
| `test_b07_5_*` | ⑤ 对账脚本**退出码**语义 + 偏差超限写审计（G-23 实跑） |
| `test_b07_6_*` | ⑥ `doc_process` / `automation` 事件端到端落库 |
| `test_b08_1_*` | ① 5 层可建 / 第 6 层 `14006` |
| `test_b08_2_*` | ② 移动到自身子树 `14006` |
| `test_b08_3_*` | ③ 删除有**成员**部门 `14006` |
| `test_b08_4_*` | ④ owner `14007` / 同级 `14008` / 角色被引用 `20005` |
| `test_b08_5_*` | ⑤ 改角色后 ≤5s 判权生效（升降两个方向） |
| `test_b08_6_*` | ⑥ 所有变更写 `audit_log` 且含 before/after |
| `test_b08_7_*` | ⑦ D-13 投影失败不回滚业务事务 |
| `test_perm_*` | 权限矩阵补充：8×3 独立复算 / ID 形态 / 拒后零下游调用 |
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.business.audit_service import AuditService
from app.business.kc_projection import OutboxEntry
from app.business.policy_service import PolicyService, reset_policy_service
from app.business.usage_service import UsageService, reset_usage_service
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    get_knowledge_base_store,
    reset_knowledge_base_store,
)
from app.db.repositories.usage import (
    current_period,
    get_usage_store,
    reset_usage_store,
)
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下令牌/请求头里的租户会被收敛为该值（造数与断言必须一致）
TENANT: Final[str] = "default"

#: 组织 ID
ORG: Final[str] = "org_qa_w2"

#: 适配层根目录（真实跑对账脚本用）
ADAPTER_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: 计量端点
M4_PATH: Final[str] = "/api/v1/model/usage"
BL5_PATH: Final[str] = "/api/v1/billing/usage"


# ===========================================================================
# 公共工具
# ===========================================================================
def _headers(
    settings: Settings,
    *,
    user_id: str = "usr_qa_admin",
    roles: tuple[str, ...] = ("org_admin",),
    tenant_id: str = TENANT,
    idem: str = "",
) -> dict[str, str]:
    """构造鉴权（与可选幂等）请求头。

    Args:
        settings: 配置实例。
        user_id: 用户 ID。
        roles: 令牌角色。
        tenant_id: 租户 ID。
        idem: 幂等键（空串表示不带）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, roles=list(roles), tenant_id=tenant_id)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
    if idem:
        headers["Idempotency-Key"] = idem
    return headers


def _key(tag: str) -> str:
    """由标签派生稳定幂等键（UUID v4 形态，避免同键复用触发 20005）。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[12:15]}-8{digest[15:18]}-{digest[18:30]}"


def _auth(user_id: str = "usr_qa_admin", roles: tuple[str, ...] = ("org_admin",)) -> AuthContext:
    """构造鉴权上下文（绕过 HTTP 层的服务级用例使用）。

    Args:
        user_id: 用户 ID。
        roles: 角色列表。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=TENANT, roles=list(roles), email="qa@example.com")


def _usage(settings: Settings) -> UsageService:
    """构造与进程内单例共享同一存储的计量服务。

    Args:
        settings: 配置实例。

    Returns:
        UsageService: 计量服务。
    """
    return UsageService(settings, store=get_usage_store(settings))


def _event_records(settings: Settings) -> list[Any]:
    """读取进程内全部用量事件（**测试内省**，只用于断言条数与字段）。

    内存实现把事件存在 `_events` 字典（键 `(tenant, capability, idem_key)`）里，
    没有公开的全量读取接口；生产口径的条数/聚合断言另走 `event_total` /
    `monthly_rows` 等公开方法（见各用例），本函数只用于检查 `source`、`units`、
    `idem_key` 这类逐条字段。

    Args:
        settings: 配置实例。

    Returns:
        list[Any]: 事件列表。
    """
    store = get_usage_store(settings)
    raw = getattr(store, "_events", None)
    if isinstance(raw, dict):
        return [item for (tenant, _, _), item in raw.items() if tenant == TENANT]
    return []


def _monthly_total(settings: Settings, scope_type: str, scope_id: str) -> int:
    """取某维度当月聚合额度。

    Args:
        settings: 配置实例。
        scope_type: 维度（`tenant` / `member` / `department`）。
        scope_id: 维度 ID。

    Returns:
        int: 额度。
    """
    store = get_usage_store(settings)
    period = current_period()
    rows = asyncio.run(store.monthly_rows(TENANT, period))
    return sum(
        row.units for row in rows if row.scope_type == scope_type and row.scope_id == scope_id
    )


@pytest.fixture(autouse=True)
def _reset_state(app: FastAPI) -> Any:
    """每个用例前后隔离全部相关单例与内存存储。

    Args:
        app: 应用实例（保证夹具顺序，使配置与单例一致）。

    Yields:
        None: 无。
    """
    from app.business.kc_projection import reset_outbox_store
    from app.db.repositories.grant_repo import reset_grant_store
    from app.db.repositories.org import reset_org_store
    from app.db.repositories.role_repo import reset_role_store

    def _clean() -> None:
        reset_policy_service()
        reset_usage_service()
        reset_usage_store()
        reset_knowledge_base_store()
        reset_grant_store()
        reset_org_store()
        reset_role_store()
        reset_outbox_store()

    _clean()
    yield None
    _clean()


# ===========================================================================
# B07 ①：同一幂等键重放不产生第二条事件（三类能力全覆盖）
# ===========================================================================
async def test_b07_1_replay_creates_no_second_event_for_all_capabilities(
    settings: Settings,
) -> None:
    """chat / doc_process / automation 三类幂等键重放都不得新增事件、不得累加聚合。

    工程师的单测只覆盖了 chat 与 doc_process 的幂等键**形态**；本用例对三类能力
    逐类做「写两次」并同时校验事件条数与 tenant/member 聚合值。
    """
    service = _usage(settings)
    cases = (
        ("chat", "gen_qa_replay", "usr_qa_r1"),
        ("doc_process", "doc_qa_replay:run_1", "usr_qa_r1"),
        ("automation", "task_qa_replay", "usr_qa_r1"),
    )
    for capability, idem_key, user_id in cases:
        first = await service.record_event(
            _event(capability=capability, idem_key=idem_key, user_id=user_id, units=17)
        )
        second = await service.record_event(
            _event(capability=capability, idem_key=idem_key, user_id=user_id, units=17)
        )
        assert first is True, f"{capability} 首次写入应为 True"
        assert second is False, f"{capability} 重放必须返回 False（ON CONFLICT DO NOTHING）"

    store = get_usage_store(settings)
    events = _event_records(settings)
    counts: dict[str, int] = {}
    for event in events:
        counts[event.capability] = counts.get(event.capability, 0) + 1
    assert counts == {"chat": 1, "doc_process": 1, "automation": 1}, counts

    rows = await store.monthly_rows(TENANT, current_period())
    tenant_units = sum(
        row.units for row in rows if row.scope_type == "tenant" and row.scope_id == TENANT
    )
    member_units = sum(row.units for row in rows if row.scope_type == "member")
    assert tenant_units == 51, f"重放不得累加：tenant={tenant_units}"
    assert member_units == 51, f"重放不得累加：member={member_units}"
    # 公开口径的事件条数必须与逐条计数一致（防止只写事件不写聚合或反之）
    stored_counts = await store.event_counts(TENANT, "", current_period())
    assert stored_counts == {"chat": 1, "doc_process": 1, "automation": 1}, stored_counts


def _event(*, capability: str, idem_key: str, user_id: str, units: int) -> Any:
    """构造一条用量事件记录。

    Args:
        capability: 能力（`chat` / `doc_qa` / `doc_process` / `automation`）。
        idem_key: 幂等键。
        user_id: 用户 ID。
        units: 额度。

    Returns:
        UsageEventRecord: 事件记录。
    """
    from app.db.repositories.usage import UsageEventRecord

    return UsageEventRecord(
        tenant_id=TENANT,
        user_id=user_id,
        capability=capability,
        idem_key=idem_key,
        units=units,
        period=current_period(),
        source="settled",
        resource_type="generation",
        resource_id=idem_key,
    )


# ===========================================================================
# B07 ②：M4 与 BL5 同刻同值 + 同增量（误差必须为 0）
# ===========================================================================
async def test_b07_2_m4_equals_bl5_exactly_and_moves_together(
    client: TestClient, settings: Settings
) -> None:
    """同刻 `used_units == quota_units_used`，且一次结算后两者**增量相等**。"""
    headers = _headers(settings)

    before_m4 = client.get(M4_PATH, params={"period": "month"}, headers=headers).json()
    before_bl5 = client.get(BL5_PATH, headers=headers).json()
    assert before_m4["code"] == 0 and before_bl5["code"] == 0
    assert before_m4["data"]["quota"]["used_units"] == before_bl5["data"]["quota_units_used"]

    service = _usage(settings)
    await service.record_generation(
        tenant_id=TENANT, user_id="usr_qa_admin", generation_id="gen_qa_same_source", units=137
    )

    after_m4 = client.get(M4_PATH, params={"period": "month"}, headers=headers).json()
    after_bl5 = client.get(BL5_PATH, headers=headers).json()
    m4_used = after_m4["data"]["quota"]["used_units"]
    bl5_used = after_bl5["data"]["quota_units_used"]

    # 误差必须严格为 0（不接受「约等于」）
    assert m4_used - bl5_used == 0, f"M4={m4_used} BL5={bl5_used} 不同源"
    assert m4_used - before_m4["data"]["quota"]["used_units"] == 137
    assert bl5_used - before_bl5["data"]["quota_units_used"] == 137
    # 剩余额度也必须同步（同一快照内的派生字段）
    assert (
        after_m4["data"]["quota"]["remaining_units"] == after_bl5["data"]["quota_units_remaining"]
    )


# ===========================================================================
# B07 ③：按成员求和 == 企业总量（diff 严格 0）
# ===========================================================================
async def test_b07_3_member_breakdown_sum_equals_tenant_total_exactly(
    client: TestClient, settings: Settings
) -> None:
    """管理视图 `group_by=member` 的 breakdown 求和与企业总量差值严格为 0。"""
    service = _usage(settings)
    for index, units in enumerate((11, 23, 47)):
        await service.record_generation(
            tenant_id=TENANT,
            user_id=f"usr_qa_m{index}",
            generation_id=f"gen_qa_bd_{index}",
            units=units,
        )

    headers = _headers(settings)
    body = client.get(BL5_PATH, params={"group_by": "member"}, headers=headers).json()
    assert body["code"] == 0, body
    data = body["data"]
    breakdown_sum = sum(int(item["units"]) for item in data["breakdown"])
    assert data["quota_units_used"] - breakdown_sum == 0, (
        f"企业总量={data['quota_units_used']} 成员求和={breakdown_sum} 差值非 0"
    )
    # breakdown 覆盖率：每个成员都被计入（11+23+47）
    assert breakdown_sum == 81


# ===========================================================================
# B07 ④：流式中途断开，units 严格落在 (0, 完整值)
# ===========================================================================
class _InterruptingDify:
    """流式下游桩：吐出两段正文后抛错（模拟网络中断/客户端断开）。"""

    def __init__(self, *, fail_after: int, total_units: int | None = None) -> None:
        """初始化桩。

        Args:
            fail_after: 吐出多少段正文后中断；`0` 表示不中断（正常结束）。
            total_units: 正常结束时的下游 usage 总额度。
        """
        self._fail_after = fail_after
        self._total_units = total_units
        # 首段必须长于推理过滤器的保留窗口（`len("<think>")-1 = 6` 字符），
        # 否则 `feed()` 会把整段留在缓冲区，`produced_chars` 仍为 0（测不出中断折算）
        self.chunks = ("会议要点一：预算复核通过，参会五人。", "会议要点二：下周复盘。")

    async def post_stream(self, path: str, payload: dict[str, Any], **_: Any) -> AsyncIterator[Any]:
        """产出底座原生事件。

        Args:
            path: 下游路径。
            payload: 请求体。
            **_: 其它参数。

        Yields:
            dict[str, Any]: 底座原生事件。
        """
        for index, chunk in enumerate(self.chunks):
            if self._fail_after and index >= self._fail_after:
                raise ApiError(
                    ErrorCode.DOWNSTREAM_UNAVAILABLE,
                    internal_detail="simulated break",
                )
            yield {"event": "message", "answer": chunk}
        if self._total_units is not None:
            # 底座真实形态：`metadata.usage.total_tokens`（见 `_total_units_of`）
            yield {
                "event": "message_end",
                "metadata": {"usage": {"total_tokens": self._total_units}},
            }


async def test_b07_4_interrupted_stream_units_strictly_inside_open_interval() -> None:
    """真实驱动 SSE 生成器中途抛错，折算值必须 `0 < units < 完整值`。

    「完整值」用同一份正文正常结束（`message_end` 带 usage）时的结算值作基准，
    因此这是一条**两端都是实测值**的开区间断言，而不是拿常量对常量。

    必须用 `downstream_enabled=True`：桩模式（`mock_mode`）走的是 `_stub_stream`
    分支，先 `yield` 再结算、没有 `finally` 折算逻辑，测不出中断语义。
    """
    from app.schemas.model import GenerateRequest
    from app.services.model_service import ModelService

    online = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
        dify_base_url="http://dify.test",
        dify_default_app_key="test-key",
    )
    request = GenerateRequest(
        conversation_id="conv_qa_interrupt",
        profile="general",
        messages=[{"role": "user", "content": "整理会议记录"}],
    )

    # 基准：正常结束（下游给出 usage.total_units=1000）
    reset_usage_store()
    reset_usage_service()
    full_service = UsageService(online, store=get_usage_store(online))
    full_model = ModelService(
        online,
        dify=_InterruptingDify(fail_after=0, total_units=1000),  # type: ignore[arg-type]
        usage=full_service,
    )
    async for _ in full_model.generate_stream(request, "usr_qa_stream", _auth("usr_qa_stream")):
        pass
    settled = [item for item in _event_records(online) if item.source == "settled"]
    assert len(settled) == 1, settled
    full_units = settled[0].units

    # 中断：产出 1 段正文后抛错（finally 折算）
    reset_usage_store()
    reset_usage_service()
    broken_service = UsageService(online, store=get_usage_store(online))
    broken_model = ModelService(
        online,
        dify=_InterruptingDify(fail_after=1),  # type: ignore[arg-type]
        usage=broken_service,
    )
    async for _ in broken_model.generate_stream(request, "usr_qa_stream", _auth("usr_qa_stream")):
        pass

    broken_events = _event_records(online)
    estimated = [item for item in broken_events if item.source == "estimated"]
    assert len(estimated) == 1, f"中断必须恰好结算 1 条 estimated 事件：{broken_events}"
    units = estimated[0].units
    assert 0 < units < full_units, f"折算值 {units} 必须落在 (0, {full_units}) 开区间"
    # 按公式折算：prompt 兜底 800 + 已下发可见字符数（首段 18 字 - 过滤器保留 6 字）
    assert units == 800 + 12, f"折算公式不符：units={units}"
    # 中断事件必须是 estimated 而非 settled（口径可审计）
    assert estimated[0].source == "estimated"


# ===========================================================================
# B07 ⑤：对账脚本退出码语义 + 偏差超限写审计（G-23 真实子进程）
# ===========================================================================
def test_b07_5a_reconcile_exits_2_without_postgres() -> None:
    """非 PostgreSQL 环境退出码 2（环境不可用，不得伪装成 PASS=0）。"""
    env = {**os.environ, "OA_BUSINESS_STORE": "memory"}
    process = _run_reconcile(env=env)
    assert process.returncode == 2, process.stdout + process.stderr
    assert "SKIP" in process.stdout


@pytest.mark.pg
async def test_b07_5b_reconcile_exits_0_when_deviation_within_threshold(
    pg_dsn: str,
) -> None:
    """一致数据 → 退出码 0（G-23 正例，真实子进程 + 真实 PostgreSQL）。"""
    tenant = "tnt_qa_reconcile_ok"
    await _reset_pg_engine()
    conf = _pg_settings(pg_dsn)
    period = current_period()
    await _clean_reconcile_rows(conf, tenant, period)
    try:
        service = UsageService(conf, store=get_usage_store(conf))
        await asyncio.gather(
            *(
                service.record_generation(
                    tenant_id=tenant,
                    user_id=f"usr_rec_{index}",
                    generation_id=f"gen_rec_{index}",
                    units=100,
                )
                for index in range(3)
            )
        )
        process = _run_reconcile(env=_pg_env(pg_dsn), tenant=tenant, period=period)
        assert process.returncode == 0, process.stdout + process.stderr
        assert "[PASS]" in process.stdout
    finally:
        await _clean_reconcile_rows(conf, tenant, period)
        await _reset_pg_engine()


@pytest.mark.pg
async def test_b07_5c_reconcile_exits_1_and_writes_audit_on_mismatch(
    pg_dsn: str,
) -> None:
    """事件表与聚合偏差 >1% → 退出码非 0 且落 `audit_log(billing.reconcile_mismatch)`。

    构造方式：只在 `usage_event` 里补一条事件（不动聚合），使事件总额比企业总量多，
    从而**结构误差仍为 0**、但「事件 vs 聚合」偏差超阈值——这正是阈值判据的靶心。
    """
    tenant = "tnt_qa_reconcile_bad"
    await _reset_pg_engine()
    conf = _pg_settings(pg_dsn)
    period = current_period()
    await _clean_reconcile_rows(conf, tenant, period)
    audit = AuditService(conf)
    before_count = len(await audit.recent(tenant, action="billing.reconcile_mismatch"))
    try:
        service = UsageService(conf, store=get_usage_store(conf))
        await service.record_generation(
            tenant_id=tenant, user_id="usr_rec_bad", generation_id="gen_rec_bad", units=100
        )
        # 直接往**事件表**插一条（绕过 UsageService，不触碰聚合），制造 >1% 偏差。
        # 这样「成员求和 == 企业总量」仍为 0（结构 OK），只有事件↔聚合偏差超阈值，
        # 正好命中 G-23 阈值判据的靶心。
        from app.db.session import tenant_scope

        async with tenant_scope(tenant, settings=conf) as uow:
            await uow.execute(
                """
                INSERT INTO usage_event (
                    tenant_id, event_id, org_id, department_id, user_id, capability,
                    resource_type, resource_id, idem_key, units, period, source
                ) VALUES (
                    :tid, 'use_qa_phantom_001', '', '', 'usr_rec_bad', 'chat',
                    'generation', 'gen_rec_bad', 'gen_rec_bad_phantom', 100, :period, 'settled'
                )
                """,
                {"tid": tenant, "period": period},
            )

        process = _run_reconcile(env=_pg_env(pg_dsn), tenant=tenant, period=period)
        assert process.returncode == 1, process.stdout + process.stderr
        assert "[FAIL]" in process.stdout

        entries = await audit.recent(tenant, action="billing.reconcile_mismatch")
        assert len(entries) == before_count + 1, (
            "偏差超限必须新增一条 audit_log(billing.reconcile_mismatch)："
            f"before={before_count} after={len(entries)}"
        )
        summary = entries[0].change_summary
        assert "before" in summary and "after" in summary
        assert summary["threshold"] == pytest.approx(0.01)
    finally:
        await _clean_reconcile_rows(conf, tenant, period)
        await _reset_pg_engine()


def _pg_settings(pg_dsn: str) -> Settings:
    """构造指向真实库的配置。

    Args:
        pg_dsn: 连接串。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        business_store="postgres",
        database_url=pg_dsn,
    )


def _pg_env(pg_dsn: str) -> dict[str, str]:
    """构造子进程环境（让脚本走 PostgreSQL）。

    Args:
        pg_dsn: 连接串。

    Returns:
        dict[str, str]: 环境变量。
    """
    return {
        **os.environ,
        "OA_BUSINESS_STORE": "postgres",
        "OA_DATABASE_URL": pg_dsn,
        "OA_DEPLOY_MODE": "private",
    }


def _run_reconcile(
    *, env: dict[str, str], tenant: str = "default", period: str = "2026-11"
) -> subprocess.CompletedProcess[str]:
    """以子进程真实执行对账脚本。

    Args:
        env: 环境变量。
        tenant: 租户。
        period: 账期。

    Returns:
        subprocess.CompletedProcess[str]: 执行结果。
    """
    return subprocess.run(  # noqa: S603 - 固定参数、无 shell
        [
            sys.executable,
            str(ADAPTER_ROOT / "scripts" / "reconcile_usage.py"),
            "--period",
            period,
            "--threshold",
            "0.01",
            "--tenant",
            tenant,
        ],
        cwd=str(ADAPTER_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


async def _clean_reconcile_rows(conf: Settings, tenant: str, period: str) -> None:
    """清理对账用例的哨兵数据。

    注意：`audit_log` 是**追加不可删**的（应用角色无 DELETE 权限，属实设计），
    因此这里只清计量表；审计断言改用「运行前后条数差」而非绝对条数。

    Args:
        conf: 配置实例。
        tenant: 租户。
        period: 账期。
    """
    from app.db.session import tenant_scope

    async with tenant_scope(tenant, settings=conf) as uow:
        await uow.execute("DELETE FROM usage_event WHERE tenant_id = :tid", {"tid": tenant})
        await uow.execute("DELETE FROM usage_monthly WHERE tenant_id = :tid", {"tid": tenant})


async def _reset_pg_engine() -> None:
    """释放进程内数据库引擎单例。

    引擎是**进程级单例**且绑定创建它的事件循环；pytest-asyncio 每个用例一个新循环，
    跨用例复用会得到「Event loop is closed」。每个 PG 用例前后都释放一次即可。
    """
    from app.db.engine import reset_engine_for_test

    await reset_engine_for_test()


# ===========================================================================
# B07 ⑥：doc_process / automation 事件端到端落库
# ===========================================================================
async def test_b07_6_document_process_event_reaches_store_via_service(
    settings: Settings,
) -> None:
    """R4 轮询到 `completed` 跃迁时，`doc_process` 事件真的落库（不是只在代码里写）。

    走 `RagService._record_doc_process_if_completed` 的真实调用路径：同一 `run_id`
    重复轮询不得产生第二条事件（幂等键 = `document_id:run_id`）。
    """
    from app.services.rag_service import RagService

    service = RagService(settings)
    usage = _usage(settings)
    service._usage = usage  # 注入共享存储，便于断言事件  # noqa: SLF001 - QA 验证需要

    @dataclass
    class _Doc:
        """文档记录替身（只需计量相关字段）。"""

        document_id: str = "doc_qa_b07"
        run_id: str = "run_qa_b07"
        file_size: int = 3000
        segment_count: int = 4
        uploader_user_id: str = "usr_qa_uploader"
        stage: str = "completed"

    auth = _auth("usr_qa_uploader")
    await service._record_doc_process_if_completed(  # noqa: SLF001 - QA 验证需要
        tenant_id=TENANT, record=_Doc(), previous_stage="indexing", auth=auth
    )
    await service._record_doc_process_if_completed(  # noqa: SLF001 - 重放验证
        tenant_id=TENANT, record=_Doc(), previous_stage="completed", auth=auth
    )

    events = _event_records(settings)
    doc_events = [item for item in events if item.capability == "doc_process"]
    assert len(doc_events) == 1, f"doc_process 事件应恰好 1 条：{events}"
    assert doc_events[0].idem_key == "doc_qa_b07:run_qa_b07"
    assert doc_events[0].units > 0
    # 落库的聚合也要有（不是只写事件表）
    rows = await get_usage_store(settings).monthly_rows(TENANT, current_period())
    tenant_units = sum(
        row.units for row in rows if row.scope_type == "tenant" and row.scope_id == TENANT
    )
    assert tenant_units == doc_events[0].units
    counts = await get_usage_store(settings).event_counts(TENANT, "", current_period())
    assert counts == {"doc_process": 1}, counts


async def test_b07_6b_automation_event_reaches_store_when_task_succeeds(
    settings: Settings,
) -> None:
    """A1 任务成功完成时 `automation` 事件落库，且幂等键为 `task_id`。"""
    from app.services.agent_service import AgentService

    usage = _usage(settings)
    service = AgentService(settings, usage=usage)
    await service._record_automation(  # noqa: SLF001 - QA 直接验证落库点
        task_id="task_qa_b07", units=64, auth=_auth("usr_qa_agent")
    )
    await service._record_automation(  # noqa: SLF001 - 重放验证
        task_id="task_qa_b07", units=64, auth=_auth("usr_qa_agent")
    )

    events = _event_records(settings)
    automation = [item for item in events if item.capability == "automation"]
    assert len(automation) == 1, f"automation 事件应恰好 1 条：{events}"
    assert automation[0].idem_key == "task_qa_b07"
    assert automation[0].units == 64


# ===========================================================================
# B08 ①：5 层可建、第 6 层 14006
# ===========================================================================
def test_b08_1_sixth_level_returns_14006_with_level_message(
    client: TestClient, settings: Settings
) -> None:
    """5 层成功且 `level` 递增；第 6 层 `14006` 且文案为层级上限（不是删除文案）。"""
    parent = ""
    for depth in range(1, 6):
        body = _create_dept(client, settings, f"QA层{depth}", parent, tag=f"qa-depth-{depth}")
        assert body["code"] == 0, body
        assert body["data"]["level"] == depth
        parent = str(body["data"]["department_id"])

    sixth = _create_dept(client, settings, "QA层6", parent, tag="qa-depth-6")
    assert sixth["code"] == 14006
    assert sixth["message"] == "组织层级最多 5 层，请调整后重试"


# ===========================================================================
# B08 ②：移动到自身子树 14006
# ===========================================================================
def test_b08_2_move_into_own_subtree_returns_14006(client: TestClient, settings: Settings) -> None:
    """把父部门移动到自己的孙部门下 → `14006`（子树文案），且树结构不变。"""
    root = _create_dept(client, settings, "QA根", tag="qa-mv-root")
    child = _create_dept(
        client, settings, "QA子", str(root["data"]["department_id"]), tag="qa-mv-child"
    )
    grand = _create_dept(
        client, settings, "QA孙", str(child["data"]["department_id"]), tag="qa-mv-grand"
    )

    moved = client.patch(
        f"/api/v1/identity/departments/{root['data']['department_id']}",
        json={"parent_id": grand["data"]["department_id"]},
        headers=_headers(settings, idem=_key("qa-mv-bad")),
    ).json()
    assert moved["code"] == 14006
    assert moved["message"] == "不能把部门移动到自己的下级部门"

    # 结构未被破坏：孙部门仍在第 3 层
    flat = client.get(
        f"/api/v1/identity/organizations/{ORG}/departments",
        params={"flat": "true"},
        headers=_headers(settings),
    ).json()
    levels = {item["department_id"]: item["level"] for item in flat["data"]["departments"]}
    assert levels[str(grand["data"]["department_id"])] == 3


# ===========================================================================
# B08 ③：删除有「成员」的部门 14006
# ===========================================================================
def test_b08_3_delete_department_with_members_returns_14006(
    client: TestClient, settings: Settings
) -> None:
    """部门下有成员（无子部门）→ `14006` + 删除文案；移出成员后可删。

    工程师的 API 用例只覆盖了「有子部门」分支，本用例补「有成员」分支
    （两者走同一个错误码但不同判定路径：`links_of_department`）。
    """
    from app.db.repositories.org import MemberLink, get_org_store

    dept = _create_dept(client, settings, "QA有成员", tag="qa-del-members")
    dept_id = str(dept["data"]["department_id"])
    asyncio.run(
        get_org_store(settings).link_member(
            MemberLink(tenant_id=TENANT, department_id=dept_id, user_id="usr_qa_in_dept")
        )
    )

    blocked = client.delete(
        f"/api/v1/identity/departments/{dept_id}",
        headers=_headers(settings, idem=_key("qa-del-blocked")),
    ).json()
    assert blocked["code"] == 14006
    assert blocked["message"] == "该部门下仍有成员或子部门，请先迁移后再删除"

    # 反证：移出成员后删除成功（证明拦截原因确实是「有成员」）
    asyncio.run(get_org_store(settings).unlink_member(TENANT, dept_id, "usr_qa_in_dept"))
    ok = client.delete(
        f"/api/v1/identity/departments/{dept_id}",
        headers=_headers(settings, idem=_key("qa-del-ok")),
    ).json()
    assert ok["code"] == 0, ok


# ===========================================================================
# B08 ④：owner 14007 / 同级 14008 / 角色被引用 20005
# ===========================================================================
def test_b08_4_owner_and_peer_protection_and_role_in_use(
    client: TestClient, settings: Settings
) -> None:
    """`14007`/`14008`/`20005` 三码逐条命中，且 `20005` 文案带成员人数。"""
    asyncio.run(_seed_role("owner", "usr_qa_owner"))
    asyncio.run(_seed_role("org_admin", "usr_qa_peer"))

    # ① 移除 owner → 14007
    owner_hit = client.delete(
        f"/api/v1/identity/organizations/{ORG}/members/usr_qa_owner",
        headers=_headers(settings, idem=_key("qa-rm-owner")),
    ).json()
    assert owner_hit["code"] == 14007
    assert owner_hit["message"] == "不能移除企业所有者，请先转移所有权"

    # ② 修改同级成员 → 14008
    peer_hit = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/usr_qa_peer",
        json={"role": "member"},
        headers=_headers(settings, idem=_key("qa-chg-peer")),
    ).json()
    assert peer_hit["code"] == 14008

    # ③ 角色被引用 → 20005（本批只有内置角色，走「内置不可删」文案；
    #    架构 §4.4 的「该角色下还有 N 名成员」文案属于自定义角色分支，
    #    B08 尚无创建自定义角色的入口 → 该分支在 B08 内不可达，见报告 P3）
    roles = client.get(
        f"/api/v1/identity/organizations/{ORG}/roles", headers=_headers(settings)
    ).json()
    admin_role = next(item for item in roles["data"]["items"] if item["role_key"] == "org_admin")
    in_use = client.delete(
        f"/api/v1/identity/organizations/{ORG}/roles/{admin_role['role_id']}",
        headers=_headers(settings, idem=_key("qa-del-role")),
    ).json()
    assert in_use["code"] == 20005
    assert in_use["data"] is None


async def _seed_role(role_key: str, user_id: str) -> None:
    """直接播种成员角色（绕过邀请链路）。

    Args:
        role_key: 角色标识。
        user_id: 用户 ID。
    """
    from app.db.repositories.role_repo import get_role_store

    store = get_role_store()
    await store.ensure_builtin_roles(TENANT, ORG)
    roles = await store.list_roles(TENANT, ORG)
    role_id = next(item.role_id for item in roles if item.role_key == role_key)
    await store.assign_role(TENANT, ORG, user_id, role_id, assigned_by="usr_qa_admin")


# ===========================================================================
# B08 ⑤：改角色后 ≤5s 判权生效（升 / 降两个方向）
# ===========================================================================
def test_b08_5a_role_upgrade_effective_within_five_seconds(
    client: TestClient, settings: Settings
) -> None:
    """改角色（升级）后 ≤5s 内权限点生效（`team:structure:manage` 为探针）。

    探针选 ID3 而不是 BL5 管理视图：ID3 走 `require_point`（权限点层），
    BL5 管理视图先走 `has_admin_role(auth)`（令牌角色层）——后者属于另一条判径，
    单独由 `test_b08_5b_*` 覆盖。
    """
    import time

    assert settings.permission_cache_ttl_seconds <= 5.0, "缓存 TTL 上限必须是 5s"

    dept = _create_dept(client, settings, "QA角色探针", tag="qa-role-probe")
    dept_id = str(dept["data"]["department_id"])

    asyncio.run(_seed_role("member", "usr_qa_up"))
    member_headers = _headers(settings, user_id="usr_qa_up", roles=("member",))
    before = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"name": "升级前改名"},
        headers={**member_headers, "Idempotency-Key": _key("qa-up-before")},
    ).json()
    assert before["code"] == 20003, f"member 不应有 team:structure:manage：{before}"

    _set_role(client, settings, user_id="usr_qa_up", role="org_admin", tag="qa-role-up")
    started = time.monotonic()
    after = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"name": "升级后改名"},
        headers={**member_headers, "Idempotency-Key": _key("qa-up-after")},
    ).json()
    elapsed = time.monotonic() - started
    assert after["code"] == 0, f"升级为 org_admin 后 {elapsed:.2f}s 内应生效：{after}"
    assert elapsed <= 5.0, f"生效耗时 {elapsed:.2f}s 超过 5s"


def test_b08_5b_role_downgrade_must_follow_business_store(
    client: TestClient, settings: Settings
) -> None:
    """改角色（**降级**）后判权必须收紧：业务库为权威源（D-13）。

    判定口径：令牌角色为 `org_admin`、业务库角色被改为 `member`。
    期望：权限点 `team:structure:manage`、管理员判定、受限库可见性都应收紧（`20003`）。
    若仍放行 → 业务库只能「加权」不能「减权」，撤权在令牌有效期内完全不生效。
    """
    import time

    dept = _create_dept(client, settings, "QA降级探针", tag="qa-down-probe")
    dept_id = str(dept["data"]["department_id"])

    # 造一个「只有管理员可见」的受限库（member 不在授权名单里）
    restricted = "kb_qa_w2_downgrade"
    asyncio.run(
        get_knowledge_base_store().create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=restricted,
                name="QA 降级受限库",
                visibility="restricted",
                owner_user_id="usr_q_owner",
            )
        )
    )

    asyncio.run(_seed_role("org_admin", "usr_qa_down"))
    admin_headers = _headers(settings, user_id="usr_qa_down", roles=("org_admin",))
    warm = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"name": "降级前改名"},
        headers={**admin_headers, "Idempotency-Key": _key("qa-down-warm")},
    ).json()
    assert warm["code"] == 0, f"降级前 org_admin 应可改名：{warm}"
    client.get(BL5_PATH, params={"group_by": "member"}, headers=admin_headers)
    warm_list = client.get("/api/v1/knowledge/bases", headers=admin_headers).json()
    assert restricted in {item["base_id"] for item in warm_list["data"]["items"]}, (
        f"降级前管理员应可见受限库：{warm_list}"
    )

    _set_role(client, settings, user_id="usr_qa_down", role="member", tag="qa-role-down")
    time.sleep(min(settings.permission_cache_ttl_seconds, 5.0) + 0.1)

    # ① 权限点层：team:structure:manage 应收紧
    point_after = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"name": "降级后改名"},
        headers={**admin_headers, "Idempotency-Key": _key("qa-down-after")},
    ).json()
    # ② 管理员判定层：企业级管理视图应收紧
    admin_after = client.get(BL5_PATH, params={"group_by": "member"}, headers=admin_headers).json()
    # ③ 对象级可见性：受限库（本人不在授权名单）不得再出现在列表/详情里
    list_after = client.get("/api/v1/knowledge/bases", headers=admin_headers).json()
    detail_after = client.get(
        f"/api/v1/knowledge/bases/{restricted}/documents", headers=admin_headers
    ).json()

    # 三条判径分别取证（不早退，便于缺陷报告一次给全证据）
    leaks: list[str] = []
    if point_after["code"] != 20003:
        leaks.append(f"① 仍可改组织架构：code={point_after['code']}")
    if admin_after["code"] != 20003:
        leaks.append(f"② 仍可看企业级管理视图：code={admin_after['code']}")
    listed_after = {item["base_id"] for item in list_after.get("data", {}).get("items", [])}
    if restricted in listed_after:
        leaks.append("③ 列表仍可见不受权的受限库")
    if detail_after["code"] != 20003:
        leaks.append(f"④ 详情仍可读不受权的受限库：code={detail_after['code']}")

    assert not leaks, (
        "业务库把该成员从 org_admin 降级为 member 后，判权未收紧"
        f"（令牌角色兜底覆盖了业务库降级）：{leaks}。"
        "架构 §2.6 D-13 要求「业务库为唯一权威源」，而 `_resolve_points` 用**并集**"
        "（令牌角色 ∪ 业务库），`has_admin_role` 只读令牌角色 → 业务库只能加权不能减权。"
    )


def _set_role(
    client: TestClient,
    settings: Settings,
    *,
    user_id: str,
    role: str,
    tag: str,
    actor_roles: tuple[str, ...] = ("owner",),
) -> None:
    """通过 ID8 修改成员角色。

    默认用 `owner` 作为操作人：同级保护（`14008`）会拦住「org_admin 改 org_admin」，
    而降级用例的靶子本身就是 org_admin，必须由更高一级角色发起。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        user_id: 成员 ID。
        role: 新角色标识。
        tag: 幂等键标签。
        actor_roles: 操作人角色。
    """
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/{user_id}",
        json={"role": role},
        headers=_headers(settings, user_id="usr_qa_owner_actor", roles=actor_roles, idem=_key(tag)),
    ).json()
    assert body["code"] == 0, body


# ===========================================================================
# B08 ⑥：所有变更写 audit_log 且含 before/after
# ===========================================================================
def test_b08_6_every_mutation_writes_before_after_audit(
    client: TestClient, settings: Settings
) -> None:
    """建/改名/移动/删部门、改角色/移出成员——每类变更都留前后值。"""
    created = _create_dept(client, settings, "QA审计部门", tag="qa-audit-create")
    dept_id = str(created["data"]["department_id"])

    renamed = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"name": "QA审计部门改名"},
        headers=_headers(settings, idem=_key("qa-audit-rename")),
    ).json()
    assert renamed["code"] == 0, renamed

    # 移动也是「变更」，同样必须有 before/after（before.parent_id 反映旧上级）
    target = _create_dept(client, settings, "QA审计新上级", tag="qa-audit-target")
    moved = client.patch(
        f"/api/v1/identity/departments/{dept_id}",
        json={"parent_id": target["data"]["department_id"]},
        headers=_headers(settings, idem=_key("qa-audit-move")),
    ).json()
    assert moved["code"] == 0, moved
    assert moved["data"]["level"] == 2

    asyncio.run(_seed_role("member", "usr_qa_audit_member"))
    role_change = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/usr_qa_audit_member",
        json={"role": "guest"},
        headers=_headers(settings, idem=_key("qa-audit-member")),
    ).json()
    assert role_change["code"] == 0, role_change

    removed = client.delete(
        f"/api/v1/identity/organizations/{ORG}/members/usr_qa_audit_member",
        headers=_headers(settings, idem=_key("qa-audit-remove")),
    ).json()
    assert removed["code"] == 0, removed

    audit = AuditService(settings)
    for action in (
        "department.create",
        "department.update",
        "member.update",
        "member.remove",
    ):
        entries = asyncio.run(audit.recent(TENANT, action=action))
        assert entries, f"{action} 未写审计"
        summary = entries[0].change_summary
        assert "before" in summary and "after" in summary, f"{action} 缺 before/after：{summary}"
    # 改名/改角色属于「有前值」的变更：before 必须非空且带具体字段
    for action, field in (
        ("department.update", "name"),
        ("member.update", "role"),
        ("member.remove", "role"),
    ):
        entries = asyncio.run(audit.recent(TENANT, action=action))
        summary = entries[0].change_summary
        assert summary["before"], f"{action} 的 before 不应为空：{summary}"
        assert field in summary["before"], f"{action} before 缺 {field}：{summary}"
    # 新建/移出属于「一端为空」的变更：after 与 before 至少各有一端有值
    created_audit = asyncio.run(audit.recent(TENANT, action="department.create"))[0]
    assert created_audit.change_summary["after"], "新建部门必须记录 after 值"
    assert created_audit.change_summary["before"] == {}, "新建部门之前不存在，before 应为空"

    # 移动后的审计：before.parent_id 与 after.parent_id 必须能看出「从哪移到哪」
    move_audit = asyncio.run(audit.recent(TENANT, action="department.update"))[0]
    move_before = move_audit.change_summary["before"]
    move_after = move_audit.change_summary["after"]
    assert move_before.get("parent_id", "") != move_after.get("parent_id"), (
        f"移动的审计前后值无法区分新旧上级：before={move_before} after={move_after}"
    )
    assert move_after.get("level") == 2


# ===========================================================================
# B08 ⑦：D-13 投影失败不回滚业务事务
# ===========================================================================
async def test_b08_7_projection_delivery_failure_never_rolls_back_business(
    client: TestClient, settings: Settings
) -> None:
    """KC 侧投递一直失败 → 出箱退避/置 failed，但业务数据完整可读（单向投影）。"""
    from app.business.kc_projection import (
        MAX_ATTEMPTS,
        KcProjectionService,
        get_outbox_store,
    )
    from app.db.repositories.org import get_org_store

    created = _create_dept(client, settings, "QA投影部门", tag="qa-proj")
    dept_id = str(created["data"]["department_id"])

    class _FailingProjector:
        """投递必失败的 KC 投影器。"""

        def __init__(self) -> None:
            """初始化计数器。"""
            self.calls = 0

        async def upsert_group(self, *, group_id: str, name: str, parent_id: str) -> None:
            """投递失败。"""
            self.calls += 1
            raise RuntimeError("kc down")

        async def delete_group(self, group_id: str) -> None:
            """投递失败。"""
            self.calls += 1
            raise RuntimeError("kc down")

        async def sync_user_groups(self, *, user_id: str, group_ids: list[str]) -> None:
            """投递失败。"""
            self.calls += 1
            raise RuntimeError("kc down")

    store = get_outbox_store(settings)
    projector = _FailingProjector()
    service = KcProjectionService(settings, store=store, projector=projector)

    delivered = 0
    for _ in range(MAX_ATTEMPTS):
        delivered = await service.run_once(TENANT)
        # 让退避到期的条目立即可重试（内存存储直接改 next_attempt_at）
        entries = await store.all_entries(TENANT)  # type: ignore[attr-defined]
        for entry in entries:
            if entry.status == "pending":
                await _force_due(store, entry)

    assert delivered == 0
    assert projector.calls >= MAX_ATTEMPTS
    entries = await store.all_entries(TENANT)  # type: ignore[attr-defined]
    assert entries[0].status == "failed", entries[0]

    # 业务数据必须完好（投影失败不回滚业务库）
    still_there = await get_org_store(settings).get_department(TENANT, dept_id)
    assert still_there is not None and still_there.name == "QA投影部门"
    tree = client.get(
        f"/api/v1/identity/organizations/{ORG}/departments", headers=_headers(settings)
    ).json()
    assert dept_id in {item["department_id"] for item in tree["data"]["departments"]}, (
        "投影失败后业务树不得回滚"
    )


async def _force_due(store: Any, entry: OutboxEntry) -> None:
    """把出箱条目的下次重试时间提前到过去（仅内存实现可用）。

    Args:
        store: 出箱存储。
        entry: 出箱条目。
    """
    entries = getattr(store, "_entries", None)
    if entries is None:  # pragma: no cover - PostgreSQL 实现不用于本用例
        return
    entries[(entry.tenant_id, entry.outbox_id)] = __import__("dataclasses").replace(
        entry, next_attempt_at="2000-01-01T00:00:00+00:00"
    )


# ===========================================================================
# 权限矩阵补充：8 类账号 × 3 入口（QA 独立复算）
#
# 与工程师 `test_permission_matrix.py` 的差异（互补，不是重复）：
# 1. 造数走另一套语义（两个部门 + 直授 + 部门授），账号集合含「跨部门」「无角色」；
# 2. 每条断言都额外要求「拒绝时不下发下游调用」；
# 3. 增补 `dept_<id>` / `<id>` 两种 ID 形态的一致性。
# ===========================================================================
ACCOUNTS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("owner", "usr_q_owner", ("owner",)),
    ("admin", "usr_q_admin", ("org_admin",)),
    ("manager", "usr_q_manager", ("member",)),
    ("member", "usr_q_member", ("member",)),
    ("guest", "usr_q_guest", ("guest",)),
    ("cross_dept", "usr_q_cross", ("member",)),
    ("cross_tenant", "usr_q_evil", ("member",)),
    ("no_role", "usr_q_norole", ()),
)

#: 被授权的账号（owner/admin/manager/member）
ALLOWED: Final[frozenset[str]] = frozenset({"owner", "admin", "manager", "member"})


def _seed_matrix() -> str:
    """造数：1 个受限库（直授 1 人 + 部门授 1 部门）+ 2 个部门 + 内置角色。

    Returns:
        str: 受限库 ID。
    """
    from app.db.repositories.grant_repo import GrantRecord, get_grant_store
    from app.db.repositories.org import MemberLink, get_org_store
    from app.db.repositories.role_repo import get_role_store

    base_id = "kb_qa_w2_restricted"
    asyncio.run(
        get_knowledge_base_store().create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=base_id,
                name="QA 受限库",
                visibility="restricted",
                owner_user_id="usr_q_owner",
            )
        )
    )
    grants = get_grant_store()
    asyncio.run(
        grants.create(
            GrantRecord(
                tenant_id=TENANT,
                grant_id="grt_qa_user",
                base_id=base_id,
                subject_type="user",
                subject_id="usr_q_member",
                permission="viewer",
                granted_by="usr_q_owner",
            )
        )
    )
    asyncio.run(
        grants.create(
            GrantRecord(
                tenant_id=TENANT,
                grant_id="grt_qa_dept",
                base_id=base_id,
                subject_type="department",
                subject_id="dept_qa_rd",
                permission="viewer",
                granted_by="usr_q_owner",
            )
        )
    )
    org = get_org_store()
    asyncio.run(
        org.link_member(
            MemberLink(tenant_id=TENANT, department_id="dept_qa_rd", user_id="usr_q_manager")
        )
    )
    asyncio.run(
        org.link_member(
            MemberLink(tenant_id=TENANT, department_id="dept_qa_sales", user_id="usr_q_cross")
        )
    )
    asyncio.run(get_role_store().ensure_builtin_roles(TENANT, ORG))
    return base_id


def _list_ids(client: TestClient, headers: dict[str, str]) -> list[str]:
    """取 R2 列表 ID。

    Args:
        client: 测试客户端。
        headers: 请求头。

    Returns:
        list[str]: 知识库 ID 列表。
    """
    body = client.get("/api/v1/knowledge/bases", headers=headers).json()
    assert body["code"] == 0, body
    return [str(item["base_id"]) for item in body["data"]["items"]]


def _detail_code(client: TestClient, headers: dict[str, str], base_id: str) -> tuple[int, int]:
    """调用详情入口（KB3）。

    Args:
        client: 测试客户端。
        headers: 请求头。
        base_id: 知识库 ID。

    Returns:
        tuple[int, int]: `(HTTP 状态, 业务码)`。
    """
    response = client.get(f"/api/v1/knowledge/bases/{base_id}/documents", headers=headers)
    return response.status_code, int(response.json()["code"])


def _search_code(client: TestClient, headers: dict[str, str], base_id: str) -> tuple[int, int]:
    """调用检索入口（R7）。

    Args:
        client: 测试客户端。
        headers: 请求头。
        base_id: 知识库 ID。

    Returns:
        tuple[int, int]: `(HTTP 状态, 业务码)`。
    """
    response = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "制度"},
        headers={
            **headers,
            "Idempotency-Key": _key(f"qa-search-{base_id}-{headers['X-Tenant-Id']}"),
        },
    )
    return response.status_code, int(response.json()["code"])


@pytest.mark.parametrize(("account", "user_id", "roles"), ACCOUNTS)
def test_perm_1_eight_accounts_three_entries(
    account: str,
    user_id: str,
    roles: tuple[str, ...],
    client: TestClient,
    settings: Settings,
) -> None:
    """8 类账号 × 3 入口（列表 / 详情 / 检索）逐格断言，拒绝一律 `20003` 且非 404。"""
    base_id = _seed_matrix()
    headers = _headers(settings, user_id=user_id, roles=roles)
    allowed = account in ALLOWED

    listed = base_id in _list_ids(client, headers)
    assert listed is allowed, f"{account} 列表可见性不符预期（期望 {allowed}）"

    status, detail_code = _detail_code(client, headers, base_id)
    if allowed:
        assert detail_code == 0, f"{account} 详情应可读，实际 {detail_code}"
    else:
        assert detail_code == 20003, f"{account} 详情必须 20003，实际 {detail_code}"
        assert status != 404, f"{account} 详情不得用 404 掩盖无权"

    status, search_code = _search_code(client, headers, base_id)
    if allowed:
        assert search_code == 0, f"{account} 检索应可用，实际 {search_code}"
    else:
        assert search_code == 20003, f"{account} 检索必须 20003，实际 {search_code}"
        assert status != 404, f"{account} 检索不得用 404 掩盖无权"


def test_perm_2_department_id_form_is_consistent(settings: Settings) -> None:
    """`dept_<id>` 与 `<id>` 两种部门 ID 写法，判权结果必须一致。

    判定链路：`_user_departments()` 返回**规范形态**（`dept_...`）→ 与授权表的
    `subject_id` 做字符串相等匹配。若授权写入侧未归一化，则「裸 ID 授权」会
    静默失效（授权建成功但没人拿到权限）——与 W1 同一类「一种写法一种结果」缺陷。
    """
    from app.db.repositories.grant_repo import GrantRecord, get_grant_store

    base_id = "kb_qa_w2_form"
    asyncio.run(
        get_knowledge_base_store().create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=base_id,
                name="QA 形态库",
                visibility="restricted",
                owner_user_id="usr_q_owner",
            )
        )
    )
    grants = get_grant_store()
    # 同一物理部门的两种写法各授一次（dept_qa_form 是规范形态）
    for grant_id, subject_id in (
        ("grt_form_prefixed", "dept_qa_form"),
        ("grt_form_bare", "qa_form"),
    ):
        asyncio.run(
            grants.create(
                GrantRecord(
                    tenant_id=TENANT,
                    grant_id=grant_id,
                    base_id=base_id,
                    subject_type="department",
                    subject_id=subject_id,
                    permission="viewer",
                    granted_by="usr_q_owner",
                )
            )
        )

    from app.db.repositories.org import MemberLink, get_org_store

    org = get_org_store()
    for user_id, dept in (("usr_q_p1", "dept_qa_form"), ("usr_q_p2", "dept_qa_form")):
        asyncio.run(
            org.link_member(MemberLink(tenant_id=TENANT, department_id=dept, user_id=user_id))
        )

    policy = PolicyService(settings)
    # 同一部门的成员，不管授权写的是哪种形态，都应命中同一条授权
    p1 = asyncio.run(policy.has_point(_auth("usr_q_p1", ("member",)), "knowledge:base:read"))
    p2 = asyncio.run(policy.has_point(_auth("usr_q_p2", ("member",)), "knowledge:base:read"))
    assert p1 is p2, "同一部门的两个成员判权结果不一致（授权形态导致）"

    record = asyncio.run(get_knowledge_base_store().get(TENANT, base_id))
    assert record is not None
    readable = asyncio.run(policy.can_read(_auth("usr_q_p1", ("member",)), record))
    assert readable is True, "规范形态（dept_qa_form）授权必须命中"

    # 反向：仅「裸 ID 授权」时，规范形态的部门成员是否也能命中（跨写法等价性）
    base2 = "kb_qa_w2_form_bare"
    asyncio.run(
        get_knowledge_base_store().create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=base2,
                name="QA 形态库2",
                visibility="restricted",
                owner_user_id="usr_q_owner",
            )
        )
    )
    asyncio.run(
        grants.create(
            GrantRecord(
                tenant_id=TENANT,
                grant_id="grt_form_bare_only",
                base_id=base2,
                subject_type="department",
                subject_id="qa_form",  # 只有裸形态
                permission="viewer",
                granted_by="usr_q_owner",
            )
        )
    )
    reset_policy_service()
    policy2 = PolicyService(settings)
    record2 = asyncio.run(get_knowledge_base_store().get(TENANT, base2))
    assert record2 is not None
    assert asyncio.run(policy2.can_read(_auth("usr_q_p1", ("member",)), record2)) is True, (
        "裸部门 ID 授权对同一部门成员无效：授权静默失效（两种 ID 形态结果不一致）"
    )


class _CountingDify:
    """记录下游调用次数的检索桩（用于证明「拒绝发生在调用之前」）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化桩。

        Args:
            payload: 下游返回体。
        """
        self.payload = payload
        self.calls = 0

    async def post(self, path: str, body: dict[str, Any], **_: Any) -> Any:
        """记录并返回桩响应。

        Args:
            path: 下游路径。
            body: 请求体。
            **_: 其它参数。

        Returns:
            Any: 带 `payload` 属性的响应对象。
        """
        self.calls += 1

        class _Resp:
            """响应替身。"""

            def __init__(self, payload: dict[str, Any]) -> None:
                """保存载荷。"""
                self.payload = payload

        return _Resp(self.payload)


def test_perm_3_denied_request_makes_zero_downstream_calls(settings: Settings) -> None:
    """未授权账号在检索入口必须**先拒后不调**（下游调用计数严格为 0）。

    W1 的教训是「先调后拒」等于数据已经出去了——错误码对但下游被调用过，
    与「没有越权」不是一回事。本用例用计数桩把这一点钉死。
    """
    from app.schemas.rag import SearchRequest
    from app.services.rag_service import RagService

    online = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret=settings.jwt_secret,
        ratelimit_enabled=False,
        dify_base_url="http://dify.test",
        dify_default_app_key="test-key",
    )
    base_id = "kb_qa_w2_denied"
    asyncio.run(
        get_knowledge_base_store(online).create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=base_id,
                name="QA 拒绝库",
                visibility="restricted",
                owner_user_id="usr_q_owner",
            )
        )
    )

    fake = _CountingDify({"records": []})
    service = RagService(online, dify=fake, policy=PolicyService(online))  # type: ignore[arg-type]
    guest = _auth("usr_q_guest", ("guest",))

    for attempt in (base_id, base_id.removeprefix("kb_")):
        with pytest.raises(ApiError) as excinfo:
            asyncio.run(service.search(SearchRequest(base_id=attempt, query="制度"), auth=guest))
        assert int(excinfo.value.code) == 20003, excinfo.value
    assert fake.calls == 0, f"拒绝路径不得调用下游，实际调用 {fake.calls} 次"


# ===========================================================================
# 回归：QA-P3-07（`filter_authorized` 归一化前重复）—— 已在 `aefa7a6` 修复
# ===========================================================================
async def test_qa_p307_filter_authorized_normalizes_before_dedup(settings: Settings) -> None:
    """`["kb_x", "x"]` 是同一对象的两种写法，归一化后必须**去重且保序**。

    若先按原始串去重再归一化，返回值会出现两个 `kb_x` → M1/M2 的
    `knowledge_base_ids` 重复 → 重复检索 / 引用条重复 / **单次对话用量虚高**
    （与 D-12 计量口径直接冲突）。因此这是计量正确性的前置条件，不只是代码洁癖。
    """
    for base_id in ("kb_x_p307", "kb_y_p307"):
        await get_knowledge_base_store(settings).create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=base_id,
                name=f"P3-07 {base_id}",
                visibility="organization",
                owner_user_id="usr_q_owner",
            )
        )
    policy = PolicyService(settings)
    auth = _auth("usr_q_member", ("member",))

    # 打乱顺序 + 混入裸形态 + 混入同一对象的第三次重复
    result = await policy.filter_authorized(auth, ["kb_y_p307", "kb_x_p307", "x_p307", "kb_y_p307"])

    assert result == ["kb_y_p307", "kb_x_p307"], f"归一化/去重/保序不符：{result}"
    assert len(result) == len(set(result)), f"结果内仍有重复：{result}"


# ===========================================================================
# W-03 判据：跨租户详情「不返回任何可用于反推存在性的信息」
# ===========================================================================
def test_perm_06_cross_tenant_detail_leaks_nothing(settings: Settings) -> None:
    """跨租户访问 KB3 详情：码值可为 `11001`（W-03 裁定），但**不得泄露任何资源信息**。

    判据（W-03）：`非 0 且不返回任何数据`。此外还必须证明「跨租户」与「真不存在」
    在响应上**不可区分**（否则可通过响应差异反推资源存在性）。
    """
    from app.main import create_app

    sentinel_base = "kb_qa_cross_sentinel"
    sentinel_name = "SENTINEL_CROSS_TENANT_BASE_NAME"
    asyncio.run(
        get_knowledge_base_store(settings).create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id=sentinel_base,
                name=sentinel_name,
                visibility="organization",
                owner_user_id="usr_q_owner",
            )
        )
    )
    asyncio.run(_seed_role_rows_only())

    saas = Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret=settings.jwt_secret,
        ratelimit_enabled=False,
    )
    evil = _headers(saas, user_id="usr_q_evil", roles=("member",), tenant_id="tnt_other_w2")
    with TestClient(create_app(saas)) as saas_client:
        cross = saas_client.get(f"/api/v1/knowledge/bases/{sentinel_base}/documents", headers=evil)
        missing = saas_client.get(
            "/api/v1/knowledge/bases/kb_qa_never_exists/documents", headers=evil
        )

    cross_body = cross.json()
    missing_body = missing.json()
    # ① 非 0 且不返回任何数据
    assert cross_body["code"] != 0
    assert cross_body["data"] is None, f"跨租户详情返回了数据：{cross_body}"
    # 说明：此处**不断言** HTTP != 404。W-03 裁定允许跨租户返 `11001`（不存在），
    # 而 `11001` 的 HTTP 映射本就是 404；PERM-07「拒绝不得是 404」针对的是
    # 「无权限但资源存在」的 `20003` 形态（避免用 404 掩盖 20003），两者不冲突。
    # ② 不得含任何可识别信息（库名 / 文档名 / 数量差异 / base_id 回显）
    text = cross.text
    for leaked in (sentinel_name, sentinel_base, "SENTINEL", "documents", "segment"):
        assert leaked not in text, f"跨租户响应泄露了 {leaked!r}：{text}"
    # ③ 与「真不存在」不可区分（码值 + 文案一致）
    assert cross_body["code"] == missing_body["code"], (
        f"跨租户与真不存在可用码值区分：{cross_body['code']} vs {missing_body['code']}"
    )
    assert cross_body["message"] == missing_body["message"], (
        f"跨租户与真不存在可用文案区分：{cross_body['message']} vs {missing_body['message']}"
    )


async def _seed_role_rows_only() -> None:
    """只播种内置角色行（不分配成员角色），供跨租户用例使用。"""
    from app.db.repositories.role_repo import get_role_store

    await get_role_store().ensure_builtin_roles(TENANT, ORG)


# ===========================================================================
# 辅助：部门创建
# ===========================================================================
def _create_dept(
    client: TestClient, settings: Settings, name: str, parent_id: str = "", tag: str = ""
) -> dict[str, Any]:
    """调用 ID2 建部门。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        name: 部门名。
        parent_id: 上级部门 ID。
        tag: 幂等键标签。

    Returns:
        dict[str, Any]: 响应体。
    """
    return client.post(
        f"/api/v1/identity/organizations/{ORG}/departments",
        json={"name": name, "parent_id": parent_id},
        headers=_headers(settings, idem=_key(tag or name)),
    ).json()
