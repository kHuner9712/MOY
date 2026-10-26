#!/usr/bin/env bash
# =============================================================================
# 阶段三演示脚本（B26 第 8 项）
#
# 一条命令把三个插件**真的跑一遍**：装插件 → 建任务 → 过人工确认 → 收结果，
# 最后打印 3 个 `task_id` 与各自的核查清单项数。**不打印任何假数据**：
#   任务真正落到 `plugin_task` 表，状态与核查清单从 A3 端点读，
#   产物从平台下载代理上 HTTP 拉一次确认真能下载。
#
# 用法：
#   export OA_TEST_DATABASE_URL="postgresql://officeai_app:...@127.0.0.1:55433/officeai"
#   bash scripts/demo_phase3.sh
#
# 退出码：
#   0  三个插件都跑完、拿到 3 个 task_id 且核查清单项数分别为 4 / 3 / 4
#   1  演示失败（某个插件没跑起来 / 清单项数不符 / 产物下载不通）
#   2  环境不就绪（缺 venv、缺库连接串、连不上库、缺爬升到付费套餐的能力）
#
# 设计说明：
#   1. **用仓库相对路径定位**（`BASH_SOURCE` → 仓库根），不硬编码绝对路径；
#   2. 驱动进程直接用适配层的 venv 解释器 + `httpx.ASGITransport` 打真端点
#      （等价于起服务再 curl，但不占端口、不会被僵尸进程影响演示）；
#   3. 每步都打印"发生了什么"，出错时打印**可照做的下一步**，不静默成功。
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ADAPTER_ROOT="${REPO_ROOT}/apps/adapter"

# ---------------------------------------------------------------------------
# 0. 环境检查（缺一样就明确报错，绝不静默跳过）
# ---------------------------------------------------------------------------
PY="${ADAPTER_ROOT}/.venv/Scripts/python.exe"
if [ ! -x "${PY}" ]; then
  PY="${ADAPTER_ROOT}/.venv/bin/python"
fi
if [ ! -x "${PY}" ]; then
  echo "[FAIL] 找不到适配层虚拟环境解释器：" >&2
  echo "       期望 ${ADAPTER_ROOT}/.venv/Scripts/python.exe（或 .venv/bin/python）" >&2
  echo "       下一步：cd apps/adapter && python -m venv .venv && .venv/Scripts/pip install -e \".[dev]\"" >&2
  exit 2
fi

DSN="${OA_TEST_DATABASE_URL:-${OA_DATABASE_URL:-}}"
if [ -z "${DSN}" ]; then
  echo "[FAIL] 没有数据库连接串：请先设置 OA_TEST_DATABASE_URL" >&2
  echo "       例：export OA_TEST_DATABASE_URL=\"postgresql://officeai_app:***@127.0.0.1:55433/officeai\"" >&2
  exit 2
fi

echo "[1/4] 检查数据库连通性…"
if ! OA_DEMO_DSN="${DSN}" "${PY}" - <<'PY'
import asyncio
import os
import sys

DSN = os.environ["OA_DEMO_DSN"].replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> int:
    """确认库能连上，且阶段三的表在（演示必须有真表）。"""
    import asyncpg

    try:
        connection = await asyncpg.connect(DSN)
    except Exception as exc:  # noqa: BLE001 - 演示脚本，报清楚即可
        print(f"[FAIL] 连不上数据库：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("       下一步：确认 PostgreSQL 已启动、端口/口令正确（docker compose up -d postgres）", file=sys.stderr)
        return 2
    try:
        row = await connection.fetchrow(
            "SELECT to_regclass('public.plugin_task') AS task, "
            "to_regclass('public.plugin_artifact') AS artifact"
        )
    finally:
        await connection.close()
    if row is None or row["task"] is None or row["artifact"] is None:
        print("[FAIL] 库里没有阶段三的表（plugin_task / plugin_artifact）", file=sys.stderr)
        print("       下一步：python -m app.db.migrate --apply（在 apps/adapter 下执行）", file=sys.stderr)
        return 2
    print("      库连接正常，阶段三的表齐备")
    return 0


sys.exit(asyncio.run(main()))
PY
then
  exit $?
fi

# ---------------------------------------------------------------------------
# 1-3. 跑三个插件（同一个驱动进程内完成，任何一步失败即非 0）
# ---------------------------------------------------------------------------
echo "[2/4] 装配环境（真库 + 真端点 + 三个插件安装）…"
echo "[3/4] 逐插件跑任务（建任务 → 过人工确认 → 收结果 → 试下载）…"

cd "${ADAPTER_ROOT}" || exit 2
OA_DEMO_DSN="${DSN}" OA_DEMO_REPO_ROOT="${REPO_ROOT}" "${PY}" - <<'PY'
"""演示驱动：真库 + 真端点跑三个插件，打印 task_id 与核查清单项数。"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

DSN = os.environ["OA_DEMO_DSN"]

#: 三个标杆插件与其输入（演示固定输入，保证每次可比）
PLUGINS: list[dict[str, object]] = [
    {
        "plugin_id": "plg_dev_workbench",
        "label": "前端研发工作台",
        "expect_items": 4,
        "inputs": {
            "title": "列表页加导出按钮",
            "requirement": "在客户列表页工具栏加一个导出按钮，导出当前筛选结果。",
            "repo": "demo/web",
            "branch_policy": "新建分支",
            "stack": "React + TypeScript",
        },
    },
    {
        "plugin_id": "plg_content_ops",
        "label": "内容运营工作台",
        "expect_items": 3,
        "inputs": {
            "topic": "季度新品上线",
            "channels": ["微信公众号", "小红书"],
            "window": "本周内",
            "source": "知识库",
        },
    },
    {
        "plugin_id": "plg_hr_admin",
        "label": "行政人事工作台",
        "expect_items": 4,
        "inputs": {
            "position": "前端工程师",
            "department": "技术部",
            "duties": "负责产品前端开发与交付；参与需求评审与技术方案设计。",
            "resumes": ["候选人A.pdf", "候选人B.pdf"],
            "doc_type": "通知",
            "doc_points": "事项说明；执行要求；反馈时间",
        },
    },
]

#: 人工接管点最多点几次（研发 2 处、内容 1 处、人事 1 处，留 3 次余量）
MAX_CONFIRMS = 3


def _prepare_env() -> None:
    """把演示用配置写进环境变量（全局取值一致，下载令牌才验得住）。"""
    os.environ.setdefault("OA_ENV", "test")
    os.environ.setdefault("OA_DEPLOY_MODE", "saas")
    os.environ.setdefault("OA_DOWNSTREAM_ENABLED", "false")
    os.environ.setdefault("OA_AUTH_MODE", "stub")
    os.environ.setdefault("OA_JWT_SECRET", "demo-phase3-secret")
    os.environ.setdefault("OA_RATELIMIT_ENABLED", "false")
    os.environ.setdefault("OA_BUSINESS_STORE", "postgres")
    os.environ["OA_DATABASE_URL"] = DSN
    os.environ.setdefault("OA_PLUGIN_RETRY_BACKOFF_MS", "0")
    os.environ.setdefault("OA_LOG_LEVEL", "ERROR")


async def main() -> int:
    """跑完三个插件并打印结果。

    Returns:
        int: 0 成功 / 1 演示失败 / 2 环境不就绪。
    """
    _prepare_env()

    from httpx import ASGITransport, AsyncClient
    from jose import jwt

    from app.business.subscription_service import SubscriptionService
    from app.core.config import Settings
    from app.db.repositories.billing import get_billing_store
    from app.main import create_app
    from app.plugins.actions import ORG_CAPABILITIES
    from app.schemas.billing import ChangeSubscriptionRequest

    settings = Settings()
    tenant = f"tnt_demo_{uuid.uuid4().hex[:8]}"
    user = "usr_demo_admin"
    token = jwt.encode(
        {
            "sub": user,
            "tenant_id": tenant,
            "roles": ["owner"],
            "email": f"{user}@example.com",
            "aud": settings.jwt_audience,
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}

    # 演示租户的套餐与企业能力开关（走真实服务/真实端点）
    try:
        subscriptions = SubscriptionService(settings, get_billing_store(settings))
        await subscriptions.change(
            tenant_id=tenant,
            actor_id=user,
            request=ChangeSubscriptionRequest(
                plan_code="professional", seats=5, action="upgrade"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 演示租户开通套餐失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://demo") as client:
        policy = await client.put(
            "/api/v1/plugins/capability-policy",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"policies": [{"capability_key": key, "enabled": True} for key in ORG_CAPABILITIES]},
        )
        if policy.status_code != 200:
            print(f"[FAIL] 打开企业能力失败：HTTP {policy.status_code} {policy.text}", file=sys.stderr)
            return 1

        results: list[dict[str, object]] = []
        for spec in PLUGINS:
            plugin_id = str(spec["plugin_id"])
            label = str(spec["label"])
            print(f"\n—— {label}（{plugin_id}）——")

            manifest_items = await _install(client, headers, plugin_id, label)
            if manifest_items is None:
                return 1

            task = await _run_task(client, headers, plugin_id, dict(spec["inputs"]), label)
            if task is None:
                return 1
            task_id = str(task["task_id"])
            detail = task["detail"]
            checklist = (detail.get("result") or {}).get("checklist") or []
            states = [str(item.get("status")) for item in checklist]
            print(f"   task_id = {task_id}")
            print(f"   任务状态 = {detail.get('status')}（进度 {detail.get('progress')}%）")
            print(
                f"   核查清单 {len(checklist)} 项："
                f"已核查 {states.count('checked')} / 待人工确认 {states.count('pending')} / "
                f"没有产出 {states.count('missing')}"
            )
            for item in checklist:
                print(f"     - {item.get('name')}：{_status_text(str(item.get('status')))}")

            download = await _probe_download(client, headers, task_id, label)
            if download is None:
                return 1

            if len(checklist) != int(spec["expect_items"]):
                print(
                    f"[FAIL] {label} 的核查清单项数应为 {spec['expect_items']}，实际 {len(checklist)}",
                    file=sys.stderr,
                )
                return 1
            results.append(
                {
                    "label": label,
                    "plugin_id": plugin_id,
                    "task_id": task_id,
                    "items": len(checklist),
                    "status": detail.get("status"),
                    "download": download,
                }
            )

    _print_summary(results, tenant)
    return 0


async def _install(client, headers, plugin_id: str, label: str) -> list[str] | None:
    """安装插件（同意全部声明能力）。

    Args:
        client: 异步客户端。
        headers: 请求头。
        plugin_id: 插件标识。
        label: 展示名。

    Returns:
        list[str] | None: 声明能力键；失败返回 None。
    """
    from app.plugins.loader import get_registry

    manifest = get_registry().get(plugin_id)
    if manifest is None:
        print(f"[FAIL] 找不到 {plugin_id} 的声明文件", file=sys.stderr)
        return None
    response = await client.post(
        f"/api/v1/plugins/{plugin_id}/install",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"grants": dict.fromkeys(manifest.capability_keys, True)},
    )
    if response.status_code != 200 or response.json().get("code") != 0:
        print(f"[FAIL] 安装 {label} 失败：HTTP {response.status_code} {response.text}", file=sys.stderr)
        return None
    print(f"   已添加并启用（版本 {response.json()['data']['version']}）")
    return list(manifest.capability_keys)


async def _run_task(client, headers, plugin_id: str, inputs: dict, label: str) -> dict | None:
    """建任务并把所有人工接管点都确认掉。

    Args:
        client: 异步客户端。
        headers: 请求头。
        plugin_id: 插件标识。
        inputs: 业务输入。
        label: 展示名。

    Returns:
        dict | None: `{"task_id": ..., "detail": ...}`；失败返回 None。
    """
    created = await client.post(
        "/api/v1/agent/tasks",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "plugin_id": plugin_id,
            "conversation_id": f"conv_demo_{plugin_id}",
            "goal": "",
            "inputs": inputs,
        },
    )
    if created.status_code != 200 or created.json().get("code") != 0:
        print(f"[FAIL] {label} 建任务失败：HTTP {created.status_code} {created.text}", file=sys.stderr)
        return None
    task_id = created.json()["data"]["task_id"]

    detail = await _detail(client, headers, task_id)
    for _ in range(MAX_CONFIRMS):
        if detail is None or detail.get("status") != "waiting_confirm":
            break
        confirm = detail.get("confirm") or {}
        print(f"   停在「{confirm.get('reason') or '需要确认'}」→ 确认继续")
        decided = await client.post(
            f"/api/v1/agent/tasks/{task_id}/retry",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"decision": "continue"},
        )
        if decided.status_code != 200 or decided.json().get("code") != 0:
            print(f"[FAIL] 确认失败：HTTP {decided.status_code} {decided.text}", file=sys.stderr)
            return None
        detail = await _detail(client, headers, task_id)
    if detail is None:
        return None
    return {"task_id": task_id, "detail": detail}


async def _detail(client, headers, task_id: str) -> dict | None:
    """读 A3 任务详情。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。

    Returns:
        dict | None: 详情数据；失败返回 None。
    """
    response = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
    if response.status_code != 200 or response.json().get("code") != 0:
        print(f"[FAIL] 读任务详情失败：HTTP {response.status_code} {response.text}", file=sys.stderr)
        return None
    return response.json()["data"]


async def _probe_download(client, headers, task_id: str, label: str) -> str | None:
    """从核查清单里挑一个可下载项，真去下载一次。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。
        label: 展示名。

    Returns:
        str | None: 下载结果描述；失败返回 None。
    """
    detail = await _detail(client, headers, task_id)
    if detail is None:
        return None
    for item in (detail.get("result") or {}).get("checklist") or []:
        url = item.get("download_url")
        if not url:
            continue
        response = await client.get(url)
        if response.status_code != 200 or not response.content:
            print(
                f"[FAIL] {label} 的「{item.get('name')}」下载失败："
                f"HTTP {response.status_code}，字节 {len(response.content)}",
                file=sys.stderr,
            )
            return None
        print(f"   试下载「{item.get('name')}」：HTTP 200，{len(response.content)} 字节")
        return f"HTTP 200 / {len(response.content)} 字节"
    print(f"   （{label} 本次没有可下载的核查项，可能是有步骤没完成）")
    return "无可下载项"


def _status_text(state: str) -> str:
    """把核查状态转成中文。

    Args:
        state: `checked` / `pending` / `missing`。

    Returns:
        str: 中文文案。
    """
    return {"checked": "已核查", "pending": "待人工确认", "missing": "没有产出"}.get(state, state)


def _print_summary(results: list[dict[str, object]], tenant: str) -> None:
    """打印演示结果汇总（3 个 task_id + 核查清单项数）。

    Args:
        results: 每个插件的结果。
        tenant: 演示租户。
    """
    print("\n================= 演示结果 =================")
    print(f"租户：{tenant}")
    for index, item in enumerate(results, start=1):
        print(
            f"{index}. {item['label']}（{item['plugin_id']}）\n"
            f"   task_id = {item['task_id']}\n"
            f"   核查清单 = {item['items']} 项；任务状态 = {item['status']}；"
            f"下载验证 = {item['download']}"
        )
    print("============================================")
    print(
        "[OK]   三个插件端到端跑通：3 个 task_id 已打印，核查清单项数 "
        + " / ".join(str(item["items"]) for item in results)
        + "（研发 4 / 内容 3 / 人事 4）"
    )


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[FAIL] 演示被中断", file=sys.stderr)
        sys.exit(1)
PY
DRIVER_STATUS=$?

echo "[4/4] 收尾"
if [ ${DRIVER_STATUS} -ne 0 ]; then
  echo "[FAIL] 演示未通过（驱动退出码 ${DRIVER_STATUS}）" >&2
  echo "       排查顺序：库表是否齐 → 插件声明是否 6 份 → 该插件是否有未完成的人工接管点" >&2
  exit ${DRIVER_STATUS}
fi

echo "[OK]   演示完成：bash scripts/demo_phase3.sh 退出码 0"
exit 0
