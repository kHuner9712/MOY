"""额度用尽**非阻断提示**（B27 `15006`）的端点级验收。

判据（PRD 5.3 + 架构 §7.1 + 冻结契约表）：

* 超额**不停服、不弹错**：HTTP 200、外层 `code == 0`、成功路径行为一字不变；
* 提示挂在 `data.notice_code` / `data.notice_message` 上（`15006` + 注册表文案）；
* 未超额时两个字段为 `0` / `""`（加性字段默认值，老前端无感）；
* 流式只在**终帧 `done`** 里带提示，**不得中途插帧**（会打断前端渲染与滚动）；
* 单一真源：判据就是 M4/BL5 的 `snapshot().over_quota_warning`，测试造数也走真实
  订阅服务 + 真实计量写入口，**不伪造存储字段**（否则测的是夹具而不是实现）。

为什么必须显式断言 `code == 0`：前端 `unwrap()` 把非 0 `code` 当异常抛，提示一旦
挪到 `code` 上就会变成弹错误框——"提醒"被做成"停服"。这条断言是本组的核心红线。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.subscription_service import SubscriptionService
from app.business.usage_service import UsageService, get_usage_service
from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode
from app.db.repositories.billing_repo import get_billing_store
from app.db.repositories.usage_repo import get_usage_store
from app.schemas.billing import ChangeSubscriptionRequest
from tests.conftest import TEST_USER_ID

pytestmark = [pytest.mark.integration]

#: 私有化部署下令牌里的租户会被收敛为 `default`（单租户），造数与断言必须用它
TENANT = "default"

#: 提示文案的唯一来源（注册表）——不得在测试里另抄一份，否则文案漂移测不出来
NOTICE_MESSAGE = ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].message

GENERATE_PATH = "/api/v1/model/generate"
STREAM_PATH = "/api/v1/model/generate/stream"
TASK_PATH = "/api/v1/agent/tasks"
UPLOAD_PATH = "/api/v1/knowledge/bases/kb_quota_notice/documents"


class _QuotaState:
    """造数结果（便于断言"确实超额了"，避免用例在额度为 0 时空转仍绿）。

    Attributes:
        total_units: 套餐额度总量。
        used_units: 已用额度。
    """

    def __init__(self, total_units: int, used_units: int) -> None:
        """初始化。

        Args:
            total_units: 套餐额度总量。
            used_units: 已用额度。
        """
        self.total_units = total_units
        self.used_units = used_units

    @property
    def over_quota(self) -> bool:
        """是否已达/超过上限（与 `over_quota_warning` 同口径）。"""
        return self.total_units > 0 and self.used_units >= self.total_units


async def _seed_usage(settings: Settings, *, over: bool, tenant_id: str = TENANT) -> _QuotaState:
    """给租户造出"未超额 / 已超额"两种计量状态。

    Args:
        settings: 配置实例。
        over: True 表示把已用额度顶到上限。
        tenant_id: 目标租户（端点走 `default`；服务层用例用任务域租户）。

    Returns:
        _QuotaState: 造数结果。
    """
    subscriptions = SubscriptionService(settings, get_billing_store(settings))
    await subscriptions.change(
        tenant_id=tenant_id,
        actor_id=TEST_USER_ID,
        # 专业版最小 5 席位（套餐规则），额度 = 3,000,000 × 5 = 15,000,000
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )

    usage = UsageService(settings, store=get_usage_store())
    quota = await usage.store.quota(tenant_id)
    assert quota.total_units > 0, "专业版必须给出额度总量，否则本组用例失去意义"
    # 未超额时用"半额"而不是"差 1 就满"：A1 会在建任务时写 automation 计量，
    # 差 1 的造数会被这一笔自身用量顶到上限，用例就会在"未超额"里看到提示（假红）。
    used = quota.total_units if over else quota.total_units // 2
    if used > 0:
        # 走**真实写入口**（D-12 唯一写入口），不直接往聚合表塞行
        await usage.record_generation(
            tenant_id=tenant_id,
            user_id=TEST_USER_ID,
            generation_id="gen_quota_notice_seed",
            units=used,
            has_authorized_bases=False,
        )
    snapshot_used = (await usage.snapshot(tenant_id=tenant_id, user_id=TEST_USER_ID)).used_units
    return _QuotaState(total_units=quota.total_units, used_units=snapshot_used)


def _generate_payload() -> dict[str, Any]:
    """构造 M1/M2 请求体。

    Returns:
        dict[str, Any]: 生成请求体。
    """
    return {
        "conversation_id": "conv_quota_notice_01",
        "profile": "general",
        "messages": [{"role": "user", "content": "帮我写一段周报"}],
    }


def _write_headers(auth_headers: dict[str, str], key: str) -> dict[str, str]:
    """构造写接口请求头（带幂等键，避免 20007 掩盖真实断言）。

    Args:
        auth_headers: 鉴权请求头。
        key: 幂等键。

    Returns:
        dict[str, str]: 请求头。
    """
    return {**auth_headers, "Idempotency-Key": key}


def _sse_frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    """解析 SSE 文本为 `(事件名, 载荷)` 列表。

    Args:
        text: SSE 响应正文。

    Returns:
        list[tuple[str, dict[str, Any]]]: 帧列表。
    """
    frames: list[tuple[str, dict[str, Any]]] = []
    event_name = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            raw = line[len("data: ") :].strip()
            try:
                payload = json.loads(raw or "{}")
            except ValueError:  # pragma: no cover - 帧由服务端序列化
                payload = {}
            frames.append((event_name, payload if isinstance(payload, dict) else {}))
    return frames


# ===========================================================================
# M1 非流式
# ===========================================================================
async def test_m1_generate_over_quota_carries_notice_and_keeps_code_zero(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """M1 超额：`code==0` + `data.notice_code==15006`，且正文照常生成。"""
    state = await _seed_usage(settings, over=True)
    assert state.over_quota, "造数失败：已用额度未达到上限"

    response = client.post(
        GENERATE_PATH,
        json=_generate_payload(),
        headers=_write_headers(auth_headers, "quota-m1-over"),
    )
    assert response.status_code == 200
    body = response.json()
    # 红线①：提示绝不占用 code —— 超额仍是一次成功响应
    assert body["code"] == 0
    # 红线②：成功路径行为不变（正文照常返回）
    assert body["data"]["content"]
    # 提示字段
    assert body["data"]["notice_code"] == int(ErrorCode.QUOTA_EXHAUSTED)
    assert body["data"]["notice_code"] == 15006
    assert body["data"]["notice_message"] == NOTICE_MESSAGE


async def test_m1_generate_under_quota_has_no_notice(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """M1 未超额：`notice_code==0` / `notice_message==""`（加性字段默认值）。"""
    state = await _seed_usage(settings, over=False)
    assert not state.over_quota

    response = client.post(
        GENERATE_PATH,
        json=_generate_payload(),
        headers=_write_headers(auth_headers, "quota-m1-under"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["notice_code"] == 0
    assert body["data"]["notice_message"] == ""


async def test_m1_generate_without_subscription_has_no_notice(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """无订阅租户（额度总量 0）天然不提示——避免用 `used >= 0` 误伤免费壳。"""
    response = client.post(
        GENERATE_PATH,
        json=_generate_payload(),
        headers=_write_headers(auth_headers, "quota-m1-nosub"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["notice_code"] == 0
    assert body["data"]["notice_message"] == ""


# ===========================================================================
# M2 流式（提示只挂终帧 `done`）
# ===========================================================================
async def test_m2_stream_over_quota_notice_only_on_done_frame(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """M2 超额：提示只在 `done` 帧，`start` / `delta` 帧里不得出现提示字段。"""
    state = await _seed_usage(settings, over=True)
    assert state.over_quota

    response = client.post(
        STREAM_PATH,
        json=_generate_payload(),
        headers=_write_headers(auth_headers, "quota-m2-over"),
    )
    assert response.status_code == 200
    frames = _sse_frames(response.text)
    names = [name for name, _ in frames]
    assert names[0] == "start"
    assert names[-1] == "done", f"最后一帧必须是 done，实际：{names}"
    assert "error" not in names, "超额不得降级为 error 帧"

    # 中途帧不带提示（不插帧、不提前暴露）
    for name, payload in frames[:-1]:
        assert payload.get("notice_code", 0) == 0, f"{name} 帧不应携带额度提示"
        assert "notice_message" not in payload or payload["notice_message"] == ""

    done_payload = frames[-1][1]
    assert done_payload["notice_code"] == 15006
    assert done_payload["notice_message"] == NOTICE_MESSAGE


async def test_m2_stream_under_quota_has_no_notice(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """M2 未超额：`done` 帧的提示字段为默认值。"""
    state = await _seed_usage(settings, over=False)
    assert not state.over_quota

    response = client.post(
        STREAM_PATH,
        json=_generate_payload(),
        headers=_write_headers(auth_headers, "quota-m2-under"),
    )
    assert response.status_code == 200
    frames = _sse_frames(response.text)
    assert frames[-1][0] == "done"
    assert frames[-1][1]["notice_code"] == 0
    assert frames[-1][1]["notice_message"] == ""


# ===========================================================================
# R3 上传
# ===========================================================================
def _upload(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    """调用 R3 上传一份小文本文件。

    Args:
        client: 测试客户端。
        headers: 请求头（须含幂等键）。

    Returns:
        dict[str, Any]: 响应体。
    """
    response = client.post(
        UPLOAD_PATH,
        headers=headers,
        files={"file": ("制度.txt", "年假需提前三个工作日申请。".encode(), "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_r3_upload_over_quota_carries_notice_and_keeps_code_zero(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """R3 超额：上传**照样成功**（`code==0`），提示挂 `data.notice_code`。"""
    state = await _seed_usage(settings, over=True)
    assert state.over_quota

    body = _upload(client, _write_headers(auth_headers, "quota-r3-over"))
    assert body["code"] == 0
    assert body["data"]["document_id"].startswith("doc_")
    assert body["data"]["notice_code"] == 15006
    assert body["data"]["notice_message"] == NOTICE_MESSAGE


async def test_r3_upload_under_quota_has_no_notice(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """R3 未超额：提示字段为默认值。"""
    state = await _seed_usage(settings, over=False)
    assert not state.over_quota

    body = _upload(client, _write_headers(auth_headers, "quota-r3-under"))
    assert body["code"] == 0
    assert body["data"]["notice_code"] == 0
    assert body["data"]["notice_message"] == ""


# ===========================================================================
# A1 创建插件任务
#
# A1 的提示落在 `TaskService.create_plugin_task()` 的成功返回上（插件分支）。
# 端点级造数需要「套餐 + 企业能力 + 安装 + 授权」四步装配，属于插件域的既有
# 测试前提（见 `tests/unit/test_plugin_install.py`），与本码无关；因此此处
# 用 `task_helpers.build_service()` 直接驱动**同一个** `create_plugin_task()`，
# 断言完全相同的字段与同样的 `code==0` 语义（`ApiResponse.ok()` 恒 code=0）。
# ===========================================================================
def _task_manifest() -> Any:
    """构造 A1 用例的最小插件声明。

    为什么不用 `fixture_manifest()` 的默认步骤：默认第 2 步是 `github` 写操作，
    按加载规则必须声明人工接管点（`confirm`），否则整份声明被判非法、注册表里取不到，
    `create_plugin_task()` 会先撞 `16002`——那是插件域的前提，与本码无关。
    这里只保留一步 `compose`，把用例钉在"额度提示"这一件事上。

    Returns:
        Any: 合法的最小声明。
    """
    from tests.unit import task_helpers

    return task_helpers.fixture_manifest(
        steps=[
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft", "summary"],
                "on_failure": "abort",
            }
        ]
    )


async def test_a1_plugin_task_over_quota_carries_notice(settings: Settings) -> None:
    """A1 超额：任务**照样创建**（`notice_code==15006`），不拦停、不弹错。"""
    from app.schemas.agent import CreateTaskRequest
    from tests.unit import task_helpers

    state = await _seed_usage(settings, over=True, tenant_id=task_helpers.TENANT)
    assert state.over_quota

    manifest = _task_manifest()
    service, _tasks, _artifacts, _usage = task_helpers.build_service(manifest)
    # 换成**真实**计量服务：单一真源在 `UsageService.quota_notice()`，
    # 替身恒返 (0,"") 就测不到接线；共享同一份内存计量存储才能看到造数。
    service._usage = get_usage_service(settings)  # noqa: SLF001 - 注入真实唯一真源
    data = await service.create_plugin_task(
        CreateTaskRequest(
            conversation_id="conv_quota_a1",
            plugin_id=manifest.plugin_id,
            inputs={"topic": "发布排期"},
        ),
        auth=task_helpers.auth(TEST_USER_ID),
    )
    assert data.task_id.startswith("task_")
    assert data.notice_code == 15006
    assert data.notice_message == NOTICE_MESSAGE


async def test_a1_plugin_task_under_quota_has_no_notice(settings: Settings) -> None:
    """A1 未超额：提示字段为默认值。"""
    from app.schemas.agent import CreateTaskRequest
    from tests.unit import task_helpers

    state = await _seed_usage(settings, over=False, tenant_id=task_helpers.TENANT)
    assert not state.over_quota

    manifest = _task_manifest()
    service, _tasks, _artifacts, _usage = task_helpers.build_service(manifest)
    service._usage = get_usage_service(settings)  # noqa: SLF001 - 注入真实唯一真源
    data = await service.create_plugin_task(
        CreateTaskRequest(
            conversation_id="conv_quota_a1_under",
            plugin_id=manifest.plugin_id,
            inputs={"topic": "发布排期"},
        ),
        auth=task_helpers.auth(TEST_USER_ID),
    )
    assert data.notice_code == 0
    assert data.notice_message == ""
