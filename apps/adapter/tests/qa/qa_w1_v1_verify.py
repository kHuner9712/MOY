"""W1 第一批 V1 独立验证脚本（QA 严过关 / Yan，自建请求与数据，不复用工程师用例）。

验证对象：
- GET /api/v1/stats/tasks?range=7d|30d
- GET /api/v1/capabilities/recommend?goal=…&limit=N

判据来源：docs/prd-w1-metrics-entry.md v1.1（R1-R4、AC-A1~A3、AC-B1~B5）、
docs/architecture-w1-metrics-entry.md §3/§7。

跑法：cd apps/adapter && ./.venv/Scripts/python.exe qa_w1_v1_verify.py
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.task import (
    InMemoryTaskStore,
    TaskRecord,
    get_task_store,
    reset_task_store,
)
from app.main import create_app

# jose 直接签令牌（不 import tests.conftest，保持独立）
from jose import jwt

PASS = 0
FAIL = 0
LINES: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    """记录单项结果。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        LINES.append(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        LINES.append(f"[FAIL] {name} — {detail}")


def make_token(settings: Settings, *, user_id: str = "usr_qa", tenant_id: str = "default") -> str:
    """现场签发 JWT（stub 模式不验签，但格式必须合法）。"""
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": ["member"],
        "email": "qa@example.com",
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def private_settings() -> Settings:
    """私有化模式配置（单租户 default）。"""
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="qa-w1-secret",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        dify_memory_app_key="qa-memory-key",
    )


def saas_settings() -> Settings:
    """SaaS 模式配置（多租户，租户头生效）。"""
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="qa-w1-secret",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        default_tenant_id="default",
    )


def iso(base: datetime, **offset: float) -> str:
    """相对基准时间生成 ISO 8601 UTC（Z 后缀，与 now_iso() 同格式）。"""
    return (base + timedelta(**offset)).isoformat().replace("+00:00", "Z")


def rec(**overrides: object) -> TaskRecord:
    """构造任务行。"""
    payload: dict[str, object] = {
        "tenant_id": "default",
        "task_id": f"task_{uuid.uuid4().hex[:16]}",
        "plugin_id": "plg_weekly_report",
        "plugin_version": "1.0.0",
        "initiator_id": "usr_qa_a",
        "trigger_type": "manual",
        "status": "completed",
        "usage_units": 0,
        "idem_key": f"idem-{uuid.uuid4().hex[:12]}",
    }
    payload.update(overrides)
    return TaskRecord(**payload)  # type: ignore[arg-type]


def seed(records: list[TaskRecord]) -> InMemoryTaskStore:
    """重置并写入内存存储（返回存储实例）。"""
    reset_task_store()
    store = get_task_store(Settings(env="test", deploy_mode="private", downstream_enabled=False))
    assert isinstance(store, InMemoryTaskStore)
    store.clear()
    for r in records:
        store._tasks[(r.tenant_id, r.task_id)] = r  # noqa: SLF001 — 直写造数，不依赖业务路径
    return store


def check_no_nan_null(node: object, path: str = "data") -> list[str]:
    """递归遍历，收集 NaN/null/Infinity 出现的位置。"""
    bad: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            bad += check_no_nan_null(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad += check_no_nan_null(v, f"{path}[{i}]")
    elif node is None:
        bad.append(f"{path}=null")
    elif isinstance(node, float) and (math.isnan(node) or math.isinf(node)):
        bad.append(f"{path}={node}")
    return bad


# ============================================================================
# V1-1：AC-A2 分子分母可复算（5 条造数：3 + retry + 窗口外）
# ============================================================================
def v1_1_recompute(client: TestClient, headers: dict[str, str]) -> None:
    """按 PRD §1.2 公式手工复算 7d 窗口每一个字段。"""
    now = datetime.now(UTC)
    records = [
        # t1：usr_a 手动 completed（200s，用量 10）
        rec(
            task_id="qa_c1",
            initiator_id="usr_a",
            status="completed",
            started_at=iso(now, days=-5),
            finished_at=iso(now, days=-5, seconds=200),
            usage_units=10,
        ),
        # t2：usr_b 手动 failed（50s，用量 2）
        rec(
            task_id="qa_f1",
            initiator_id="usr_b",
            status="failed",
            started_at=iso(now, days=-4),
            finished_at=iso(now, days=-4, seconds=50),
            usage_units=2,
        ),
        # t3：usr_c 用户主动取消（cancel_requested=true）
        rec(
            task_id="qa_x1",
            initiator_id="usr_c",
            plugin_id="plg_mail_draft",
            status="cancelled",
            cancel_requested=True,
            started_at=iso(now, days=-3),
        ),
        # t4：usr_a trigger_type=retry completed（150s，用量 8）→ 进 total/completed，剔出用户口径
        rec(
            task_id="qa_r1",
            initiator_id="usr_a",
            trigger_type="retry",
            retry_of="qa_c1",
            status="completed",
            started_at=iso(now, days=-2),
            finished_at=iso(now, days=-2, seconds=150),
            usage_units=8,
        ),
        # t5：窗口外（now-8d）completed → 7d 排除
        rec(
            task_id="qa_o1",
            initiator_id="usr_d",
            status="completed",
            started_at=iso(now, days=-8),
            finished_at=iso(now, days=-8, seconds=100),
            usage_units=10,
        ),
    ]
    seed(records)

    # ---- 手工复算（PRD §1.2 公式）----
    exp = {
        "total": 4,
        "completed": 2,  # t1 + retry t4
        "failed": 1,
        "cancelled": 1,  # 用户主动取消
        "active_users": 3,  # usr_a/usr_b/usr_c（retry 剔出）
        "completion_rate": round(2 / (4 - 1) * 100, 2),  # 66.67
        "avg_duration_seconds": round((200 + 150) / 2, 1),  # 175.0
        "p50_duration_seconds": 175.0,  # [150,200] 线性插值 = 175.0
        "repeat_user_rate": 0.0,  # 无用户同插件 ≥2 次（retry 不计）
        "units_per_success": round((10 + 8) / 2, 2),  # 9.0
    }

    resp = client.get("/api/v1/stats/tasks?range=7d", headers=headers)
    if resp.status_code != 200:
        report("V1-1a 7d 请求 200", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return
    body = resp.json()
    data = body.get("data", {})
    diffs = []
    for key, want in exp.items():
        got = data.get(key)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 1e-9:
                diffs.append(f"{key}: 期望 {want} 实得 {got}")
        elif got != want:
            diffs.append(f"{key}: 期望 {want} 实得 {got}")
    report(
        "V1-1a 7d 十字段手工复算一致（AC-A2）",
        not diffs,
        "；".join(diffs) if diffs else f"total={data.get('total')} rate={data.get('completion_rate')} avg={data.get('avg_duration_seconds')} p50={data.get('p50_duration_seconds')} units={data.get('units_per_success')}",
    )
    report(
        "V1-1b range 回显 = 7d",
        data.get("range") == "7d",
        f"实得 {data.get('range')!r}",
    )

    # ---- range=30d：窗口外任务入窗 ----
    exp30 = {
        "total": 5,
        "completed": 3,
        "failed": 1,
        "cancelled": 1,
        "active_users": 4,  # usr_a/usr_b/usr_c/usr_d
        "completion_rate": round(3 / (5 - 1) * 100, 2),  # 75.0
        "avg_duration_seconds": round((200 + 150 + 100) / 3, 1),  # 150.0
        "p50_duration_seconds": 150.0,  # [100,150,200]
        "repeat_user_rate": 0.0,
        "units_per_success": round((10 + 8 + 10) / 3, 2),  # 9.33
    }
    resp30 = client.get("/api/v1/stats/tasks?range=30d", headers=headers)
    data30 = resp30.json().get("data", {}) if resp30.status_code == 200 else {}
    diffs30 = []
    for key, want in exp30.items():
        got = data30.get(key)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 1e-9:
                diffs30.append(f"{key}: 期望 {want} 实得 {got}")
        elif got != want:
            diffs30.append(f"{key}: 期望 {want} 实得 {got}")
    report(
        "V1-1c 30d 十字段手工复算一致（AC-A6）",
        resp30.status_code == 200 and not diffs30,
        "；".join(diffs30) if diffs30 else f"total={data30.get('total')} rate={data30.get('completion_rate')} avg={data30.get('avg_duration_seconds')} units={data30.get('units_per_success')}",
    )


# ============================================================================
# V1-2：M4 重复使用率非零路径（同用户同插件 2 次非 retry）
# ============================================================================
def v1_2_repeat_user(client: TestClient, headers: dict[str, str]) -> None:
    """重复用户场景：repeat_user_rate 应为 100.0。"""
    now = datetime.now(UTC)
    records = [
        rec(
            task_id="qa_rr1",
            initiator_id="usr_rep",
            status="completed",
            started_at=iso(now, days=-2),
            finished_at=iso(now, days=-2, seconds=100),
            usage_units=5,
        ),
        rec(
            task_id="qa_rr2",
            initiator_id="usr_rep",
            status="completed",
            started_at=iso(now, days=-1),
            finished_at=iso(now, days=-1, seconds=300),
            usage_units=15,
        ),
    ]
    seed(records)
    resp = client.get("/api/v1/stats/tasks?range=7d", headers=headers)
    data = resp.json().get("data", {})
    exp = {
        "total": 2,
        "completed": 2,
        "active_users": 1,
        "repeat_user_rate": 100.0,
        "completion_rate": 100.0,
        "avg_duration_seconds": 200.0,
        "p50_duration_seconds": 200.0,
        "units_per_success": 10.0,
    }
    diffs = [f"{k}: 期望 {v} 实得 {data.get(k)}" for k, v in exp.items() if data.get(k) != v]
    report(
        "V1-2 M4 重复使用率非零路径（同用户同插件 2 次）",
        resp.status_code == 200 and not diffs,
        "；".join(diffs) if diffs else f"repeat_user_rate={data.get('repeat_user_rate')} avg={data.get('avg_duration_seconds')}",
    )


# ============================================================================
# V1-3：range=90d → 20001 且 HTTP 400（AC-A1）
# ============================================================================
def v1_3_invalid_range(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.get("/api/v1/stats/tasks?range=90d", headers=headers)
    ok = resp.status_code == 400 and resp.json().get("code") == 20001
    report(
        "V1-3 range=90d → HTTP 400 + code=20001（AC-A1，不得 500）",
        ok,
        f"HTTP {resp.status_code}, code={resp.json().get('code')}, body={resp.text[:160]}",
    )
    resp2 = client.get("/api/v1/stats/tasks", headers=headers)
    report(
        "V1-3b range 缺省 = 7d（200）",
        resp2.status_code == 200 and resp2.json().get("data", {}).get("range") == "7d",
        f"HTTP {resp2.status_code}, range={resp2.json().get('data', {}).get('range')!r}",
    )


# ============================================================================
# V1-4：空租户全 0，无 NaN/null（AC-A3/R4）——SaaS 模式 + 全新租户
# ============================================================================
def v1_4_empty_tenant() -> None:
    settings = saas_settings()
    app: FastAPI = create_app(settings)
    tenant = f"tnt_qa_empty_{uuid.uuid4().hex[:8]}"
    headers = {
        "Authorization": f"Bearer {make_token(settings, tenant_id=tenant)}",
        "X-Tenant-Id": tenant,
    }
    with TestClient(app) as c:
        resp = c.get("/api/v1/stats/tasks?range=7d", headers=headers)
        ok_status = resp.status_code == 200
        data = resp.json().get("data", {}) if ok_status else {}
        all_zero = all(
            data.get(k) == 0 for k in ("total", "completed", "failed", "cancelled", "active_users")
        ) and all(
            data.get(k) == 0.0
            for k in (
                "completion_rate",
                "avg_duration_seconds",
                "p50_duration_seconds",
                "repeat_user_rate",
                "units_per_success",
            )
        )
        bad = check_no_nan_null(resp.json())
        report(
            "V1-4 空租户全字段 0 且无 NaN/null（AC-A3/R4）",
            ok_status and all_zero and not bad,
            f"HTTP {resp.status_code}, data={data}, 异常节点={bad}",
        )


# ============================================================================
# V1-5：未登录 → 401
# ============================================================================
def v1_5_unauth() -> None:
    settings = private_settings()
    app: FastAPI = create_app(settings)
    with TestClient(app) as c:
        r1 = c.get("/api/v1/stats/tasks")
        r2 = c.get("/api/v1/capabilities/recommend?goal=整理本周周报")
    report(
        "V1-5 stats 未登录 → 401",
        r1.status_code == 401,
        f"HTTP {r1.status_code}",
    )
    report(
        "V1-5b recommend 未登录 → 401",
        r2.status_code == 401,
        f"HTTP {r2.status_code}",
    )


# ============================================================================
# V1-6~V1-10：能力推荐（AC-B1~B5/B7 + 易混词 + 无命中兜底）
# ============================================================================
PLACEHOLDER_WORDS = ["暂不支持", "暂未支持", "尚未支持", "敬请期待", "稍后开放", "即将上线", "即将开放", "正在开发", "开发中", "后续版本提供"]


def v1_6_weekly_report(client: TestClient, headers: dict[str, str]) -> None:
    """goal=整理本周周报 → matched、理由非空、周报卡 inputs 含汇报周期/收件人（AC-B1/B2/B7）。"""
    resp = client.get("/api/v1/capabilities/recommend", params={"goal": "整理本周周报", "limit": 3}, headers=headers)
    body = resp.json()
    data = body.get("data", {})
    items = data.get("items", [])
    reasons_ok = all(it.get("matched_terms") and it.get("matched_fields") for it in items)
    wr = next((it for it in items if it.get("id") == "plg_weekly_report"), None)
    wr_ok = wr is not None and items[0].get("id") == "plg_weekly_report"
    input_labels = [i.get("label") for i in (wr or {}).get("inputs", [])]
    inputs_ok = "汇报周期" in input_labels and "收件人" in input_labels
    report(
        "V1-6a goal=整理本周周报 → matched=true 且周报卡居首",
        data.get("matched") is True and items and wr_ok,
        f"matched={data.get('matched')}, 首卡={items[0].get('id') if items else None}",
    )
    report(
        "V1-6b 每项 matched_terms/matched_fields 非空（AC-B2 禁空理由）",
        bool(items) and reasons_ok,
        f"items={[(it.get('id'), it.get('matched_terms'), it.get('matched_fields')) for it in items]}",
    )
    report(
        "V1-6c 周报卡 inputs 含「汇报周期」「收件人」（AC-B7）",
        inputs_ok,
        f"labels={input_labels}",
    )
    report(
        "V1-6d total_catalog = 11（6 插件 + 5 工具）",
        data.get("total_catalog") == 11,
        f"实得 {data.get('total_catalog')}",
    )


def v1_7_confusable(client: TestClient, headers: dict[str, str]) -> None:
    """易混词：goal=写一封邮件 不得把周报插件排在邮件相关能力前面（AC-B1）。"""
    resp = client.get("/api/v1/capabilities/recommend", params={"goal": "写一封邮件", "limit": 3}, headers=headers)
    data = resp.json().get("data", {})
    items = data.get("items", [])
    ids = [it.get("id") for it in items]
    mail_ids = {"plg_mail_draft", "tool:email"}
    wr_idx = ids.index("plg_weekly_report") if "plg_weekly_report" in ids else None
    mail_idx = [i for i, x in enumerate(ids) if x in mail_ids]
    ok = bool(mail_idx) and (wr_idx is None or wr_idx > min(mail_idx))
    report(
        "V1-7 易混词「写一封邮件」不串到周报（邮件相关能力在前/周报不在列）",
        ok,
        f"排序={ids}, scores={[it.get('score') for it in items]}",
    )


def v1_8_empty_goal(client: TestClient, headers: dict[str, str]) -> None:
    """空 goal → 返回目录不报错（AC-B4）；limit 缺省 3。"""
    resp = client.get("/api/v1/capabilities/recommend", params={"goal": ""}, headers=headers)
    data = resp.json().get("data", {})
    ok = (
        resp.status_code == 200
        and data.get("matched") is False
        and data.get("fallback") is False
        and len(data.get("items", [])) == 3
    )
    report(
        "V1-8a goal 为空 → 目录前 3 条、matched/fallback=false（AC-B4）",
        ok,
        f"HTTP {resp.status_code}, items={[it.get('id') for it in data.get('items', [])]}, matched={data.get('matched')}, fallback={data.get('fallback')}",
    )
    resp2 = client.get("/api/v1/capabilities/recommend", headers=headers)
    data2 = resp2.json().get("data", {})
    report(
        "V1-8b goal 参数缺省 → 目录前 3 条（limit 缺省 3）",
        resp2.status_code == 200 and len(data2.get("items", [])) == 3,
        f"HTTP {resp2.status_code}, items={[it.get('id') for it in data2.get('items', [])]}",
    )


def v1_9_param_bounds(client: TestClient, headers: dict[str, str]) -> None:
    """goal 4001 字 / limit=0 / limit=11 → 20001（AC-B5）。"""
    long_goal = "整" * 4001
    r1 = client.get("/api/v1/capabilities/recommend", params={"goal": long_goal}, headers=headers)
    r2 = client.get("/api/v1/capabilities/recommend", params={"goal": "整理本周周报", "limit": 0}, headers=headers)
    r3 = client.get("/api/v1/capabilities/recommend", params={"goal": "整理本周周报", "limit": 11}, headers=headers)
    r4 = client.get("/api/v1/capabilities/recommend", params={"goal": "整理本周周报", "limit": 10}, headers=headers)
    report(
        "V1-9a goal 4001 字 → HTTP 400 + 20001",
        r1.status_code == 400 and r1.json().get("code") == 20001,
        f"HTTP {r1.status_code}, code={r1.json().get('code')}",
    )
    report(
        "V1-9b limit=0 → HTTP 400 + 20001",
        r2.status_code == 400 and r2.json().get("code") == 20001,
        f"HTTP {r2.status_code}, code={r2.json().get('code')}",
    )
    report(
        "V1-9c limit=11 → HTTP 400 + 20001",
        r3.status_code == 400 and r3.json().get("code") == 20001,
        f"HTTP {r3.status_code}, code={r3.json().get('code')}",
    )
    report(
        "V1-9d limit=10 → 200（上限内放行）",
        r4.status_code == 200,
        f"HTTP {r4.status_code}",
    )
    r5 = client.get("/api/v1/capabilities/recommend", params={"goal": "整" * 4000}, headers=headers)
    report(
        "V1-9e goal 恰 4000 字 → 200（边界内）",
        r5.status_code == 200,
        f"HTTP {r5.status_code}",
    )


def v1_10_no_hit(client: TestClient, headers: dict[str, str]) -> None:
    """无命中 → fallback=true、items 非空、hint 无占位词（AC-B3）。"""
    resp = client.get("/api/v1/capabilities/recommend", params={"goal": "zzz 不存在的词", "limit": 3}, headers=headers)
    data = resp.json().get("data", {})
    hint = data.get("fallback_hint", "")
    items = data.get("items", [])
    placeholder_hit = [w for w in PLACEHOLDER_WORDS if w in hint]
    ok = (
        resp.status_code == 200
        and data.get("matched") is False
        and data.get("fallback") is True
        and len(items) > 0
        and hint
        and not placeholder_hit
        and all(it.get("score") == 0 for it in items)
    )
    report(
        "V1-10 无命中兜底：fallback=true + items 非空 + hint 无占位词（AC-B3）",
        ok,
        f"items={[it.get('id') for it in items]}, hint={hint!r}, 占位词={placeholder_hit}",
    )


def v1_11_tool_card(client: TestClient, headers: dict[str, str]) -> None:
    """工具卡：kind=tool、inputs=[]（§3.2）。"""
    resp = client.get("/api/v1/capabilities/recommend", params={"goal": "给团队发一封邮件通知"}, headers=headers)
    data = resp.json().get("data", {})
    tools = [it for it in data.get("items", []) if it.get("kind") == "tool"]
    ok = bool(tools) and all(it.get("inputs") == [] for it in tools) and all(it.get("id", "").startswith("tool:") for it in tools)
    report(
        "V1-11 工具卡 kind=tool/id=tool:*/inputs=[]",
        ok,
        f"items={[(it.get('id'), it.get('kind'), it.get('inputs')) for it in data.get('items', [])]}",
    )


def main() -> int:
    """入口：按序执行全部 V1 检查。"""
    settings = private_settings()
    app: FastAPI = create_app(settings)
    headers = {"Authorization": f"Bearer {make_token(settings)}", "X-Tenant-Id": "default"}
    with TestClient(app) as client:
        v1_1_recompute(client, headers)
        v1_2_repeat_user(client, headers)
        v1_3_invalid_range(client, headers)
        v1_6_weekly_report(client, headers)
        v1_7_confusable(client, headers)
        v1_8_empty_goal(client, headers)
        v1_9_param_bounds(client, headers)
        v1_10_no_hit(client, headers)
        v1_11_tool_card(client, headers)
    v1_4_empty_tenant()
    v1_5_unauth()

    print("\n".join(LINES))
    print(f"\n==== V1 汇总: {PASS} passed / {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
