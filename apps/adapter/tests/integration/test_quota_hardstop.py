"""额度硬止损的**上游零调用**验收（阶段四 T4-04.2，架构 §7.2 / PRD P4-11）。

**口径先行（不读这段会照旧判据写出一批必然变红的用例）**

架构 §7.2 与 PRD P4-11 的原始判据是「额度 0 → `code=15006` + 上游 0 次」。
2026-10-27 用户裁决（PRD `prd-phase2-increment.md` §5.3 ① 修订留痕、
`app/business/usage_service.py:711-792` 的实现说明、`tests/expected_xfail.py`
的 `P3-B22-01` 结清记录）把**对话（M1/M2）**这一路改成了「≥100% 继续可用 +
单次回答限长」，因此对话路径的上游调用次数**恒为 1**（代价已如实登记）。

硬止损（= 上游 0 次）现在成立的是另外两路，本文件测的就是这两路：

| 调用面 | 超额后行为 | 上游调用次数 | 判据落点 |
| --- | --- | --- | --- |
| R3 文档上传 | 收下 + 挂起（`stage=quota_held`） | **0** | 本文件用例 ① |
| A1 自动化任务 | 照建 + 不执行（`status=queued` / `hold_reason=quota`） | **0** | 本文件用例 ② |
| M1/M2 对话 | 继续可用 + 限长（`notice_code=15006`） | 1（**刻意**） | `test_quota_notice.py` |

**与既有文件的分工**（三处都已存在，本文件不重复它们）：

- `tests/unit/test_rag_service.py` 的
  `test_upload_is_held_without_calling_downstream_when_quota_exhausted`
  ——服务内部（`is_capability_exhausted` 被 monkeypatch）的零调用证明；
- `tests/unit/test_task_quota_hold.py` ——A1 挂起与恢复（用**替身**计量服务）；
- `tests/unit/test_usage_guard.py::TestUsageDimensions` ——`UsageService.record_generation`
  存下三个档位（**存储层**）。

本文件补的是**接线**：真的走 HTTP 端点 / 真的 `TaskService.create_plugin_task()`，
用**真实** `UsageService`（造数也走真实订阅 + 真实计量写入口），证明
「端点 → 业务层 → 额度判定 → 上游」这条链上，判定发生在**上游之前**，
且档位与 token 真的从 `generate` 端点一路写进了 `usage_event`。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.subscription_service import SubscriptionService
from app.business.usage_service import (
    HOLD_REASON_QUOTA,
    QUOTA_HOLD_NOTICE,
    UsageService,
    get_usage_service,
)
from app.clients.dify_client import DifyResponse
from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode
from app.db.repositories.billing import get_billing_store
from app.db.repositories.usage import get_usage_store
from app.schemas.billing import ChangeSubscriptionRequest
from app.schemas.rag import DocumentStage
from app.services.rag_service import RagService
from tests.conftest import TEST_JWT_SECRET, TEST_USER_ID

pytestmark = [pytest.mark.integration]

#: 私有化部署下令牌里的租户会被收敛为 `default`（单租户），端点造数必须用它
TENANT = "default"

#: 提示文案/码的唯一来源（注册表）——测试里不另抄一份，否则文案漂移测不出来
QUOTA_SPEC = ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED]

GENERATE_PATH = "/api/v1/model/generate"
UPLOAD_PATH = "/api/v1/knowledge/bases/kb_hardstop/documents"

#: 三个**互不相同**的档位（契约枚举只有 4 个值，取其中 3 个即可证明维度真的在写）
PROFILES = ("general", "code", "long_context")


def _write_headers(auth_headers: dict[str, str], key: str) -> dict[str, str]:
    """构造写接口请求头（带幂等键，避免 20007 掩盖真实断言）。

    Args:
        auth_headers: 鉴权请求头。
        key: 幂等键。

    Returns:
        dict[str, str]: 请求头。
    """
    return {**auth_headers, "Idempotency-Key": key}


async def _exhaust_quota(settings: Settings, *, tenant_id: str, user_id: str) -> None:
    """把某租户某成员的额度顶到上限（全部走真实写入口，不伪造存储字段）。

    为什么不用 `monkeypatch` 把 `is_capability_exhausted` 打成 `True`：
    那样测的是夹具的返回值，而不是"订阅 + 计量 → 判定"这条真实链路。
    这里是集成层，造数的可信度直接决定结论的可信度。

    Args:
        settings: 配置实例。
        tenant_id: 目标租户。
        user_id: 目标成员（判定按**个人**额度口径）。
    """
    subscriptions = SubscriptionService(settings, get_billing_store(settings))
    await subscriptions.change(
        tenant_id=tenant_id,
        actor_id=user_id,
        # 专业版最小 5 席位，额度 = 3,000,000 × 5 = 15,000,000
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )
    usage = UsageService(settings, store=get_usage_store())
    quota = await usage.store.quota(tenant_id)
    assert quota.total_units > 0, "专业版必须给出额度总量，否则用例前提不成立"
    await usage.record_generation(
        tenant_id=tenant_id,
        user_id=user_id,
        generation_id=f"gen_hardstop_seed_{tenant_id}",
        units=quota.total_units,
        has_authorized_bases=False,
    )
    snapshot = await usage.snapshot(tenant_id=tenant_id, user_id=user_id)
    assert snapshot.remaining_units <= 0, "造数失败：额度未真正用尽"


class _RecordingDify:
    """记录调用次数的假底座（只实现上传路径用到的两个方法）。

    Attributes:
        multipart_calls: `post_multipart` 的调用入参。
        post_calls: `post` 的调用入参。
    """

    def __init__(self) -> None:
        """初始化（零调用）。"""
        self.multipart_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    async def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, tuple[str, bytes, str]],
        form_data: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录一次 multipart 上传（超额时**不该被调到**）。

        Args:
            path: 下游路径。
            files: 文件字段。
            form_data: 文本字段。
            api_key: 下游密钥。

        Returns:
            DifyResponse: 底座载荷（仅在未被拦截时才会产生）。
        """
        self.multipart_calls.append(
            {"path": path, "files": files, "form_data": form_data, "api_key": api_key}
        )
        return DifyResponse(200, {"id": "file-hardstop", "name": "制度.txt"})

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录一次 JSON POST（超额时**不该被调到**）。

        Args:
            path: 下游路径。
            payload: 请求体。
            idempotent: 是否幂等（替身不使用）。
            api_key: 下游密钥。

        Returns:
            DifyResponse: 底座载荷（仅在未被拦截时才会产生）。
        """
        del idempotent
        self.post_calls.append({"path": path, "payload": payload, "api_key": api_key})
        return DifyResponse(200, {"id": "doc-hardstop", "indexing_status": "waiting"})


def _live_settings() -> Settings:
    """构造**真实模式**（非桩）的配置：只有这样上传路径才会真去调底座。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret=TEST_JWT_SECRET,
        ratelimit_enabled=False,
    )


# ===========================================================================
# ① R3 文档：硬止损在底座调用之前
# ===========================================================================
async def test_document_processing_is_hard_stopped_before_any_downstream_call(
    client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> None:
    """额度用尽 → 上传**收下但零底座调用**（判据核心：上游 0 次）。

    「零上游」口径是**零底座调用**，不是零落库（PRD 5.3「已上传的保留」）：
    记录照落、`hold_reason='quota'`、`stage='quota_held'`，但没有一次
    `post_multipart` / `post`。这条区分是 P4-11 ② 的全部意义——
    拒收会让"额度恢复后再处理"没有对象可处理。
    """
    await _exhaust_quota(settings, tenant_id=TENANT, user_id=TEST_USER_ID)

    live = _live_settings()
    dify = _RecordingDify()
    service = RagService(
        live,
        dify=dify,  # type: ignore[arg-type] - 只用到 post_multipart / post
        usage=UsageService(live, store=get_usage_store()),
    )
    # 端点通过 `get_rag_service()` 取单例，这里把它换成"真实模式 + 可计数底座"的实例
    import app.services.rag_service as rag_module

    original = rag_module._service  # noqa: SLF001 - 注入受测装配
    rag_module._service = service  # noqa: SLF001
    try:
        response = client.post(
            UPLOAD_PATH,
            headers=_write_headers(auth_headers, "hardstop-upload"),
            files={"file": ("制度.txt", "年假需提前三个工作日申请。".encode(), "text/plain")},
        )
    finally:
        rag_module._service = original  # noqa: SLF001

    assert response.status_code == 200, response.text
    body = response.json()
    # 收下（不是拒收）：外层 code=0，文档 ID 已生成
    assert body["code"] == 0, f"超额不得拒收上传：{body}"
    data = body["data"]
    assert data["document_id"].startswith("doc_")

    # **核心判据**：一次底座调用都没有
    assert dify.multipart_calls == [], "超额时不许向底座上传文件字节"
    assert dify.post_calls == [], "超额时不许向底座创建文档"

    # 挂起标记与未处理状态（恢复后据此自动处理）
    assert data["stage"] == DocumentStage.QUOTA_HELD.value
    assert data["progress"] == 0
    assert data["notice_code"] == int(QUOTA_SPEC.code)
    # 文档这一路的文案与对话不同：必须给出**可执行出路**（PRD 5.3 ② 红线）——
    # V1.0 不存输入字节，恢复后无法重放解析，所以是「请重新上传」而不是「自动处理」。
    assert data["notice_message"] == QUOTA_HOLD_NOTICE
    assert "重新上传" in data["notice_message"]

    record = await service._documents.store.get(TENANT, data["document_id"])  # noqa: SLF001
    assert record is not None, "PRD 5.3「已上传的保留」：记录必须落库"
    assert record.hold_reason == HOLD_REASON_QUOTA
    assert record.file_size > 0


# ===========================================================================
# ② A1 自动化任务：硬止损在步骤执行之前
# ===========================================================================
async def test_automation_task_is_held_without_running_any_step(settings: Settings) -> None:
    """额度用尽 → 任务**照建但不执行**：`status=queued`、一步都没跑。

    「上游 0 次」在这里的等价表述是 **`EchoGateway.calls == []`**：
    A1 的每一次上游调用都发生在步骤原语里，门面一次都没被调到，
    就说明一个插件步骤都没启动。只断言 `hold_reason` 不够——
    那正是"看着有判据、实际不执行"的假绿（`test_task_quota_hold.py` 模块说明同一句）。
    """
    from app.schemas.agent import CreateTaskRequest
    from tests.unit import task_helpers

    await _exhaust_quota(settings, tenant_id=task_helpers.TENANT, user_id=TEST_USER_ID)

    manifest = task_helpers.fixture_manifest(
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
    gateway = task_helpers.EchoGateway()
    service, tasks, _artifacts, _usage = task_helpers.build_service(manifest, gateway=gateway)
    # 换成**真实**计量服务：单一真源在 `UsageService`，替身恒返 False 就测不到接线
    service._usage = get_usage_service(settings)  # noqa: SLF001 - 注入真实唯一真源

    data = await service.create_plugin_task(
        CreateTaskRequest(
            conversation_id="conv_hardstop_a1",
            plugin_id=manifest.plugin_id,
            inputs={"topic": "发布排期"},
        ),
        auth=task_helpers.auth(TEST_USER_ID),
    )

    assert gateway.calls == [], f"超额时不许执行任何步骤，实际执行了 {gateway.calls}"
    assert data.status.value == "queued", "超额任务必须排队而不是 running"
    assert data.notice_code == int(QUOTA_SPEC.code)

    record = await tasks.get_task(task_helpers.TENANT, data.task_id)
    assert record is not None
    assert record.hold_reason == HOLD_REASON_QUOTA, "挂起标记必须落库（前端据此显示原因）"


# ===========================================================================
# ③ 计量维度：三个档位真的从端点写进了事件
# ===========================================================================
async def test_three_profiles_write_three_dimension_rows(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """3 次不同档位调用 → `usage_event.profile` 出现 3 个值，且 token 均非空。

    `test_usage_guard.py::TestUsageDimensions` 已在**存储层**验过这四列；
    本条验的是**接线**：`GenerateRequest.profile` 与两个 token 计数真的从
    `POST /model/generate` 一路传到了 `usage_event`。少接一根线时，
    存储层用例照样绿，而看板会整列为空——那正是本判据要防的事。
    """
    for index, profile in enumerate(PROFILES):
        response = client.post(
            GENERATE_PATH,
            json={
                "conversation_id": f"conv_hardstop_dim_{index}",
                "profile": profile,
                "messages": [{"role": "user", "content": f"第 {index} 次提问"}],
            },
            headers=_write_headers(auth_headers, f"hardstop-dim-{index}"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["code"] == 0

    store = get_usage_store()
    events = list(getattr(store, "_events", {}).values())
    assert len(events) == len(PROFILES), f"应产生 {len(PROFILES)} 条事件，实际 {len(events)}"
    assert {event.profile for event in events} == set(PROFILES), (
        f"档位维度未落库：{[event.profile for event in events]}"
    )
    assert all(event.prompt_tokens > 0 for event in events), "输入 token 必须非空"
    assert all(event.completion_tokens > 0 for event in events), "输出 token 必须非空"
    # 每条事件的租户/成员身份都必须可归因（看板按这两个维度切分）
    assert {event.tenant_id for event in events} == {TENANT}
    assert {event.user_id for event in events} == {TEST_USER_ID}
