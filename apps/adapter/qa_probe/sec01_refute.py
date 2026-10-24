"""QA 独立复验脚本：SEC-01/02/03 攻击复现（不复用工程用例）。

用法：
    .venv/Scripts/python.exe qa_probe/sec01_refute.py [stub|oidc]

只读验证：不改动业务代码，只在内存中构造应用与令牌。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import Settings
from app.main import create_app

SECRET = "qa-independent-secret-32-bytes!!"
TENANT_A = "tnt_qa_a"
TENANT_B = "tnt_qa_b"
USER_A = "usr_qa_a"
USER_B = "usr_qa_b"
OPS_TENANT = "__platform__"
SECRET_GOAL = "QA探针-A公司并购条款-9f2c4d"

TASKS = "/api/v1/agent/tasks"
OPS_SETTINGS = "/api/v1/ops/settings"
AUDIT_LOGS = "/api/v1/identity/audit-logs"

RESULTS: list[dict[str, Any]] = []


def token(tenant: str, user: str, roles: list[str] | None = None) -> str:
    """签发令牌（roles=None 表示不带角色声明）。"""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user,
        "tenant_id": tenant,
        "aud": "officeai-adapter",
        "iss": "officeai",
        "iat": now,
        "exp": now + 3600,
    }
    if roles is not None:
        payload["roles"] = roles
    return jwt.encode(payload, SECRET, algorithm="HS256")


def hdr(
    tenant: str,
    user: str,
    roles: list[str] | None = None,
    *,
    tenant_header: str | None = None,
    idem: str | None = None,
) -> dict[str, str]:
    """构造请求头。tenant_header 可与令牌租户不同（伪造场景）。"""
    headers = {
        "Authorization": f"Bearer {token(tenant, user, roles if roles is not None else ['member'])}",
        "X-Tenant-Id": tenant_header if tenant_header is not None else tenant,
    }
    if idem:
        headers["Idempotency-Key"] = idem
    return headers


def record(pid: str, desc: str, resp: Any, expect: str, extra: str = "") -> None:
    """记录一条探针结果。"""
    try:
        body = resp.json()
        code = body.get("code")
        message = str(body.get("message", ""))[:40]
        data = body.get("data")
        data_summary = "null" if data is None else json.dumps(data, ensure_ascii=False)[:90]
    except Exception:
        code, message, data_summary = "-", resp.text[:40].replace("\n", " "), "-"
    leak = SECRET_GOAL in resp.text or "并购条款" in resp.text or TENANT_A in resp.text
    RESULTS.append(
        {
            "id": pid,
            "desc": desc,
            "status": resp.status_code,
            "code": code,
            "message": message,
            "leak": leak,
            "expect": expect,
            "data": data_summary,
            "extra": extra,
        }
    )


def main(mode: str) -> int:
    """执行全部探针。"""
    settings = Settings(
        env="test",
        deploy_mode="saas",
        business_store="memory",
        downstream_enabled=False,
        auth_mode=mode,  # type: ignore[arg-type]
        jwt_secret=SECRET,
        jwt_algorithm="HS256",
        jwt_audience="officeai-adapter",
        jwt_issuer="officeai",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # --- 准备：A 建任务 ---
        created = client.post(
            TASKS,
            json={"conversation_id": "conv_qa_a", "goal": SECRET_GOAL},
            headers=hdr(TENANT_A, USER_A, idem=str(uuid.uuid4())),
        )
        assert created.status_code == 200, created.text
        task_a = created.json()["data"]["task_id"]
        print(f"[setup] A 任务 = {task_a}\n")

        # 1. B 读 A 详情
        record(
            "P1",
            "B 读 A 任务详情",
            client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_B, USER_B)),
            "拒绝(12001/20003)，无 goal/result/step 泄漏",
        )

        # 1b. 不可区分性（别人的任务 vs 不存在）
        other = client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_B, USER_B))
        missing = client.get(f"{TASKS}/task_zzz_not_exist", headers=hdr(TENANT_B, USER_B))
        record(
            "P1b",
            "「别人的任务」vs「不存在」响应体一致性",
            missing,
            "状态码+code+message 完全一致",
            extra=f"other={other.status_code}/{other.json().get('code')}/"
            f"{other.json().get('message')!r} vs missing={missing.status_code}/"
            f"{missing.json().get('code')}/{missing.json().get('message')!r}",
        )

        # 2. B 列表
        listing = client.get(TASKS, headers=hdr(TENANT_B, USER_B))
        total = listing.json()["data"]["meta"]["total"] if listing.status_code == 200 else "n/a"
        record(
            "P2",
            "B 列表枚举",
            listing,
            "meta.total == 0 且不含 A 的 task_id",
            extra=f"total={total} contains_A_id={task_a in listing.text}",
        )

        # 3. B 读步骤
        record(
            "P3",
            "B 读 A 任务步骤",
            client.get(f"{TASKS}/{task_a}/steps", headers=hdr(TENANT_B, USER_B)),
            "拒绝(12001/20003)",
        )

        # 4. B 取消 A 任务 + A 侧未被改动
        cancel = client.post(
            f"{TASKS}/{task_a}/cancel",
            headers=hdr(TENANT_B, USER_B, idem=str(uuid.uuid4())),
        )
        after = client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_A, USER_A))
        status_after = after.json()["data"]["status"] if after.status_code == 200 else "n/a"
        record(
            "P4",
            "B 取消 A 任务，随后 A 读自己任务",
            cancel,
            "拒绝，且 A 侧 status 仍 running",
            extra=f"A_after={after.status_code}/{status_after}",
        )

        # 5. B 订阅 SSE
        sse = client.get(f"{TASKS}/{task_a}/events", headers=hdr(TENANT_B, USER_B))
        record(
            "P5",
            "B 对 A 任务发 SSE events",
            sse,
            "建立流之前拒绝",
            extra=f"content-type={sse.headers.get('content-type')}",
        )

        # 6. A 自己读/列/取消
        own = client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_A, USER_A))
        own_list = client.get(TASKS, headers=hdr(TENANT_A, USER_A))
        own_total = own_list.json()["data"]["meta"]["total"] if own_list.status_code == 200 else "n/a"
        record(
            "P6",
            "A 读自己任务 / 列自己任务 / 查步骤",
            own,
            "全部 200",
            extra=f"list_total={own_total} steps={client.get(f'{TASKS}/{task_a}/steps', headers=hdr(TENANT_A, USER_A)).status_code}",
        )

        # 7. 平台口径正反例
        ops_ok = client.get(
            OPS_SETTINGS, headers=hdr(OPS_TENANT, USER_B, roles=["ops_operator"])
        )
        record("P7a", "__platform__ + [ops_operator] 调 ops", ops_ok, "非 403")
        record(
            "P7b",
            "__platform__ + [member] 调 ops",
            client.get(OPS_SETTINGS, headers=hdr(OPS_TENANT, USER_B, roles=["member"])),
            "20003",
        )
        record(
            "P7c",
            "__platform__ + [] 调 ops",
            client.get(OPS_SETTINGS, headers=hdr(OPS_TENANT, USER_B, roles=[])),
            "20003",
        )
        record(
            "P7d",
            "__platform__ + [ops:console:access]（旧写法的权限点当角色）",
            client.get(
                OPS_SETTINGS, headers=hdr(OPS_TENANT, USER_B, roles=["ops:console:access"])
            ),
            "按新口径 20003（口径收紧的证据）",
        )

        # 8. 自造权限点（**每个探针用独立用户**：策略快照按 (tenant,user) 缓存，
        #    复用同一用户会让后续探针读到前一个令牌的角色快照，测不到真实判权）
        for idx, roles in enumerate((["x:y"], ["admin:*"], ["knowledge:base:read:extra"])):
            record(
                f"P8.{idx + 1}",
                f"roles={roles} 调 audit-logs（需 audit:log:read）",
                client.post(
                    AUDIT_LOGS,
                    json={},
                    headers=hdr(
                        TENANT_A, f"usr_qa_p8_{idx}", roles=roles, idem=str(uuid.uuid4())
                    ),
                ),
                "20003",
            )
        record(
            "P8.4",
            "roles=['audit:log:read'] 调 audit-logs（目录内权限点当角色）",
            client.post(
                AUDIT_LOGS,
                json={},
                headers=hdr(
                    TENANT_A,
                    "usr_qa_p8_ok",
                    roles=["audit:log:read"],
                    idem=str(uuid.uuid4()),
                ),
            ),
            "200（兼容口保留）",
        )
        # P8.5：同一用户在「低权令牌 → 高权令牌」之间的快照缓存串号（相邻风险探针）
        low = client.post(
            AUDIT_LOGS,
            json={},
            headers=hdr(TENANT_A, "usr_qa_p8_cache", roles=["x:y"], idem=str(uuid.uuid4())),
        )
        high = client.post(
            AUDIT_LOGS,
            json={},
            headers=hdr(
                TENANT_A, "usr_qa_p8_cache", roles=["audit:log:read"], idem=str(uuid.uuid4())
            ),
        )
        record(
            "P8.5",
            "同一用户先低权后高权（快照缓存是否串号）",
            high,
            "口径应为 200；若 403 说明缓存按 (tenant,user) 复用了旧角色快照",
            extra=f"low={low.status_code} high={high.status_code}",
        )

        # 9. 绕过尝试：X-Tenant-Id 伪造（AR-017 旧路径）
        record(
            "P9a",
            "B 令牌 + X-Tenant-Id: A 读 A 任务",
            client.get(
                f"{TASKS}/{task_a}", headers=hdr(TENANT_B, USER_B, tenant_header=TENANT_A)
            ),
            "20003 / 12001（不得返回 A 内容）",
        )
        record(
            "P9b",
            "B 令牌 + X-Tenant-Id: A 列任务",
            client.get(TASKS, headers=hdr(TENANT_B, USER_B, tenant_header=TENANT_A)),
            "20003（不得枚举到 A 任务）",
        )
        record(
            "P9c",
            "企业令牌 + X-Tenant-Id: __platform__ 读 ops",
            client.get(
                OPS_SETTINGS,
                headers=hdr(TENANT_B, USER_B, roles=["owner"], tenant_header=OPS_TENANT),
            ),
            "20003",
        )
        record(
            "P9d",
            "企业令牌(member) + X-Tenant-Id: __platform__ 读 A 任务",
            client.get(
                f"{TASKS}/{task_a}",
                headers=hdr(TENANT_B, USER_B, tenant_header=OPS_TENANT),
            ),
            "20003",
        )

        # 10. 枚举：B 针对 A 的 task_id 反复取消/重试
        for _ in range(3):
            client.post(
                f"{TASKS}/{task_a}/cancel", headers=hdr(TENANT_B, USER_B, idem=str(uuid.uuid4()))
            )
            client.post(
                f"{TASKS}/{task_a}/retry",
                json={"decision": "retry_all", "reason": "枚举探测"},
                headers=hdr(TENANT_B, USER_B, idem=str(uuid.uuid4())),
            )
        a_list = client.get(TASKS, headers=hdr(TENANT_A, USER_A))
        a_detail = client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_A, USER_A))
        a_total = a_list.json()["data"]["meta"]["total"] if a_list.status_code == 200 else "n/a"
        a_status = (
            a_detail.json()["data"]["status"] if a_detail.status_code == 200 else "n/a"
        )
        record(
            "P10",
            "B 3 轮 cancel/retry 后 A 侧现状",
            a_detail,
            "A 任务数仍 1、状态仍 running",
            extra=f"A_total={a_total} A_status={a_status}",
        )

        # 11. 同租户他人任务（我的附加探针）
        record(
            "P11",
            "同租户另一用户 C 读 A 的任务（水平越权）",
            client.get(f"{TASKS}/{task_a}", headers=hdr(TENANT_A, "usr_qa_c")),
            "本次未修复范围，观察现状",
        )
        record(
            "P11b",
            "同租户另一用户 C 取消 A 的任务（水平越权·写）",
            client.post(
                f"{TASKS}/{task_a}/cancel",
                headers=hdr(TENANT_A, "usr_qa_c", idem=str(uuid.uuid4())),
            ),
            "本次未修复范围，观察现状",
        )

        # 12. 幂等键跨租户复用（B 拿 A 的 Idempotency-Key 重放取消）
        a_key = str(uuid.uuid4())
        a_cancel = client.post(
            f"{TASKS}/{task_a}/cancel", headers=hdr(TENANT_A, USER_A, idem=a_key)
        )
        b_replay = client.post(
            f"{TASKS}/{task_a}/cancel", headers=hdr(TENANT_B, USER_B, idem=a_key)
        )
        record(
            "P12",
            "B 复用 A 的幂等键重放取消（幂等回放越权）",
            b_replay,
            "不得回放 A 的结果（20005/12001）",
            extra=f"A_first={a_cancel.status_code}/{a_cancel.json().get('code')}",
        )

        # 13. A 自己的 SSE（确认修复没把功能一起关掉）
        a_sse = client.get(f"{TASKS}/{task_a}/events", headers=hdr(TENANT_A, USER_A))
        record(
            "P13",
            "A 订阅自己任务 SSE",
            a_sse,
            "200 且 text/event-stream",
            extra=f"content-type={a_sse.headers.get('content-type')}",
        )

    # 14. tenant_membership_check=always 下的 X-Tenant-Id 伪造（同一 stub 配置）
    settings_always = Settings(
        env="test",
        deploy_mode="saas",
        business_store="memory",
        downstream_enabled=False,
        auth_mode=mode,  # type: ignore[arg-type]
        jwt_secret=SECRET,
        jwt_algorithm="HS256",
        jwt_audience="officeai-adapter",
        jwt_issuer="officeai",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        tenant_membership_check="always",
    )
    with TestClient(create_app(settings_always)) as client2:
        created2 = client2.post(
            TASKS,
            json={"conversation_id": "conv_qa_a2", "goal": SECRET_GOAL},
            headers=hdr(TENANT_A, USER_A, idem=str(uuid.uuid4())),
        )
        task_a2 = created2.json()["data"]["task_id"]
        record(
            "P14",
            f"membership_check=always({mode}) 下 B+X-Tenant-Id:A 读 A 任务",
            client2.get(
                f"{TASKS}/{task_a2}", headers=hdr(TENANT_B, USER_B, tenant_header=TENANT_A)
            ),
            "20003（开关生效）",
        )

    print(f"{'ID':<7}{'status':<8}{'code':<8}{'leak':<6}{'desc'}")
    for item in RESULTS:
        print(
            f"{item['id']:<7}{str(item['status']):<8}{str(item['code']):<8}"
            f"{str(item['leak']):<6}{item['desc']}"
        )
        if item["extra"]:
            print(f"        └ extra: {item['extra']}")
        print(f"        └ expect: {item['expect']}")
    print("\n=== JSON ===")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "stub"))
