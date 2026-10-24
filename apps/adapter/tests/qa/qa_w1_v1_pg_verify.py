"""W1 第一批 V1-9 独立 PG 验证（QA 严过关 / Yan）——不复用工程师造数。

自建 5 条造数（3 + retry + 窗口外）+ 重复用户场景，同批数据分别灌入
PostgresTaskStore 与 InMemoryTaskStore：
1. 仓储层：PG 聚合 SQL == 内存实现 == QA 手工复算期望值（防止"两边一起错"）；
2. 端点层：business_store=postgres 起真实应用，TestClient 打
   GET /api/v1/stats/tasks，响应与 PRD §1.2 公式手工复算一致（AC-A7）。
结束清理 QA 库自建租户数据。

跑法：cd apps/adapter && OA_TEST_DATABASE_URL=... ./.venv/Scripts/python.exe qa_w1_v1_pg_verify.py
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.task_repo import (
    InMemoryTaskStore,
    PostgresTaskStore,
    TaskRecord,
    TaskStats,
)
from app.main import create_app

PG_DSN = os.environ.get("OA_TEST_DATABASE_URL", "")
PASS = 0
FAIL = 0
LINES: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        LINES.append(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        LINES.append(f"[FAIL] {name} — {detail}")


def pg_settings() -> Settings:
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="qa-w1-pg-secret",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        business_store="postgres",
        database_url=PG_DSN,
        credential_encryption_key=base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(),
    )


def make_token(settings: Settings, tenant_id: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": "usr_qa_pg",
        "tenant_id": tenant_id,
        "roles": ["member"],
        "email": "qa@example.com",
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def iso(base: datetime, **offset: float) -> str:
    return (base + timedelta(**offset)).isoformat().replace("+00:00", "Z")


def rec(tenant: str, task_id: str, **overrides: object) -> TaskRecord:
    payload: dict[str, object] = {
        "tenant_id": tenant,
        "task_id": task_id,
        "plugin_id": "plg_weekly_report",
        "plugin_version": "1.0.0",
        "initiator_id": "usr_pg_a",
        "trigger_type": "manual",
        "status": "completed",
        "usage_units": 0,
        "idem_key": f"idem-{uuid.uuid4().hex[:12]}",
    }
    payload.update(overrides)
    return TaskRecord(**payload)  # type: ignore[arg-type]


def dataset_main(tenant: str, now: datetime) -> list[TaskRecord]:
    """与内存验证 V1-1 完全相同的 5 条造数。"""
    return [
        rec(tenant, "qa_pg_c1", initiator_id="usr_a", status="completed",
            started_at=iso(now, days=-5), finished_at=iso(now, days=-5, seconds=200), usage_units=10),
        rec(tenant, "qa_pg_f1", initiator_id="usr_b", status="failed",
            started_at=iso(now, days=-4), finished_at=iso(now, days=-4, seconds=50), usage_units=2),
        rec(tenant, "qa_pg_x1", initiator_id="usr_c", plugin_id="plg_mail_draft",
            status="cancelled", cancel_requested=True, started_at=iso(now, days=-3)),
        rec(tenant, "qa_pg_r1", initiator_id="usr_a", trigger_type="retry", retry_of="qa_pg_c1",
            status="completed", started_at=iso(now, days=-2), finished_at=iso(now, days=-2, seconds=150), usage_units=8),
        rec(tenant, "qa_pg_o1", initiator_id="usr_d", status="completed",
            started_at=iso(now, days=-8), finished_at=iso(now, days=-8, seconds=100), usage_units=10),
    ]


def dataset_repeat(tenant: str, now: datetime) -> list[TaskRecord]:
    """重复用户场景（同用户同插件 2 次非 retry）。"""
    return [
        rec(tenant, "qa_pg_rr1", initiator_id="usr_rep", status="completed",
            started_at=iso(now, days=-2), finished_at=iso(now, days=-2, seconds=100), usage_units=5),
        rec(tenant, "qa_pg_rr2", initiator_id="usr_rep", status="completed",
            started_at=iso(now, days=-1), finished_at=iso(now, days=-1, seconds=300), usage_units=15),
    ]


async def seed_store(store, records: list[TaskRecord]) -> None:
    """PG INSERT 不含 finished_at，统一 create + update 与内存拿逐字相同数据。"""
    for r in records:
        await store.create_task(r)
        if r.finished_at:
            await store.update_task(r.tenant_id, r.task_id, finished_at=r.finished_at)


def diff_stats(a: TaskStats, b: TaskStats) -> str:
    out = []
    for f in ("total", "completed", "failed", "cancelled", "active_users", "repeat_users",
              "duration_sum_seconds", "duration_p50_seconds", "units_sum"):
        va, vb = getattr(a, f), getattr(b, f)
        if isinstance(va, float) or isinstance(vb, float):
            if abs(float(va) - float(vb)) > 1e-9:
                out.append(f"{f}: {va} != {vb}")
        elif va != vb:
            out.append(f"{f}: {va} != {vb}")
    return "；".join(out)


async def main() -> int:
    if not PG_DSN:
        print("缺少 OA_TEST_DATABASE_URL，PG 验证无法进行（绝不假绿）")
        return 2
    settings = pg_settings()
    now = datetime.now(UTC)
    tenants = [f"tnt_qa_pg_{uuid.uuid4().hex[:8]}" for _ in range(2)]
    memory = InMemoryTaskStore()
    pg_store = PostgresTaskStore(settings)
    try:
        # ---------- 场景一：主造数 ----------
        t1 = tenants[0]
        recs1 = dataset_main(t1, now)
        await seed_store(memory, recs1)
        await seed_store(pg_store, recs1)
        for days, exp in (
            (7, TaskStats(total=4, completed=2, failed=1, cancelled=1, active_users=3,
                          repeat_users=0, duration_sum_seconds=350.0, duration_p50_seconds=175.0, units_sum=18)),
            (30, TaskStats(total=5, completed=3, failed=1, cancelled=1, active_users=4,
                           repeat_users=0, duration_sum_seconds=450.0, duration_p50_seconds=150.0, units_sum=28)),
        ):
            since = now - timedelta(days=days)
            since_s, until_s = since.isoformat(), now.isoformat()
            mem = await memory.task_stats(t1, since_s, until_s)
            pg = await pg_store.task_stats(t1, since_s, until_s)
            d_mp = diff_stats(mem, pg)
            d_pe = diff_stats(pg, exp)
            report(f"V1-9a 主造数 {days}d：PG == 内存", not d_mp, d_mp or f"total={pg.total} completed={pg.completed} cancelled={pg.cancelled} users={pg.active_users} sum={pg.duration_sum_seconds} p50={pg.duration_p50_seconds} units={pg.units_sum}")
            report(f"V1-9b 主造数 {days}d：PG == QA 手工复算期望", not d_pe, d_pe)

        # ---------- 场景二：重复用户 ----------
        t2 = tenants[1]
        recs2 = dataset_repeat(t2, now)
        await seed_store(memory, recs2)
        await seed_store(pg_store, recs2)
        since = now - timedelta(days=7)
        exp2 = TaskStats(total=2, completed=2, failed=0, cancelled=0, active_users=1,
                         repeat_users=1, duration_sum_seconds=400.0, duration_p50_seconds=200.0, units_sum=20)
        mem2 = await memory.task_stats(t2, since.isoformat(), now.isoformat())
        pg2 = await pg_store.task_stats(t2, since.isoformat(), now.isoformat())
        report("V1-9c 重复用户场景：PG == 内存 == 手工期望",
               not diff_stats(mem2, pg2) and not diff_stats(pg2, exp2),
               f"diff(mem,pg)={diff_stats(mem2, pg2) or '无'} diff(pg,exp)={diff_stats(pg2, exp2) or '无'}")

        # ---------- 端点层：真实 PG 后端应用（同一事件循环内用 AsyncClient） ----------
        import httpx

        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://qa-pg-verify") as client:
            headers = {"Authorization": f"Bearer {make_token(settings, t1)}", "X-Tenant-Id": t1}
            r7 = await client.get("/api/v1/stats/tasks?range=7d", headers=headers)
            r30 = await client.get("/api/v1/stats/tasks?range=30d", headers=headers)
        d7 = r7.json().get("data", {})
        d30 = r30.json().get("data", {})
        exp_resp_7 = {"total": 4, "completed": 2, "failed": 1, "cancelled": 1, "active_users": 3,
                      "completion_rate": 66.67, "avg_duration_seconds": 175.0, "p50_duration_seconds": 175.0,
                      "repeat_user_rate": 0.0, "units_per_success": 9.0}
        exp_resp_30 = {"total": 5, "completed": 3, "failed": 1, "cancelled": 1, "active_users": 4,
                       "completion_rate": 75.0, "avg_duration_seconds": 150.0, "p50_duration_seconds": 150.0,
                       "repeat_user_rate": 0.0, "units_per_success": 9.33}
        bad7 = [f"{k}: 期望 {v} 实得 {d7.get(k)}" for k, v in exp_resp_7.items() if d7.get(k) != v]
        bad30 = [f"{k}: 期望 {v} 实得 {d30.get(k)}" for k, v in exp_resp_30.items() if d30.get(k) != v]
        report("V1-9d 端点层（PG 后端）7d 响应 == 手工复算",
               r7.status_code == 200 and not bad7,
               "；".join(bad7) if bad7 else f"HTTP {r7.status_code}, data={d7}")
        report("V1-9e 端点层（PG 后端）30d 响应 == 手工复算",
               r30.status_code == 200 and not bad30,
               "；".join(bad30) if bad30 else f"HTTP {r30.status_code}, data={d30}")
    finally:
        from app.db.engine import reset_engine_for_test
        from app.db.session import platform_scope

        try:
            async with platform_scope(settings) as uow:
                for table in ("plugin_task_step", "plugin_task"):
                    for t in tenants:
                        await uow.execute(
                            f"DELETE FROM {table} WHERE tenant_id = :tenant_id",
                            {"tenant_id": t},
                        )
            LINES.append(f"[INFO] 已清理 QA 库租户数据: {tenants}")
        finally:
            await reset_engine_for_test()

    print("\n".join(LINES))
    print(f"\n==== V1-9 PG 汇总: {PASS} passed / {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
