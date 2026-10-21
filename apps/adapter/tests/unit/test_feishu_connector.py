"""飞书连接器单元测试（阶段二 B06，PRD P2-TL-02）。

覆盖点：

1. 能力清单 4 项且与注册表一致；注册表能力名为中文业务语言；
2. 权限不足（应用未开通）→ `13005` + 文案含「开通对应权限」；
3. **部分成功语义**：日程创建成功但个别参与人未找到 → `status=success`（即不抛错）
   + `result.warnings`「有 N 位参与人未找到，请核对后重试」（PRD 验收 4）；
4. 全部参与人都未找到 → `13006`（不是"部分成功"）；
5. 边界：参与人 > 50 → `20001`；缺标题/开始时间 → `20001`；
6. 异常映射：空授权码 → `13003`；未连接 → `13002`；模拟无响应 → `13004`。
"""

from __future__ import annotations

import pytest

from app.core.errors import ApiError, ErrorCode
from app.registry.loader import get_tool_registry
from app.tools.base import WARNINGS_KEY
from app.tools.feishu_connector import (
    MAX_PARTICIPANTS,
    PERMISSION_REQUIRED_MESSAGE,
    TOO_MANY_PARTICIPANTS_MESSAGE,
    FeishuConnector,
)

pytestmark = pytest.mark.unit


def _connector() -> FeishuConnector:
    """构造连接器实例。

    Returns:
        FeishuConnector: 连接器。
    """
    return FeishuConnector()


async def _execute(operation: str, params: dict | None = None) -> dict:
    """以有效连接执行一次操作。

    Args:
        operation: 操作标识。
        params: 操作参数。

    Returns:
        dict: 执行结果。
    """
    return await _connector().execute(
        connection_id="conn_1",
        operation=operation,
        params=params or {},
        timeout_ms=15_000,
        tenant_id="tnt_1",
        user_id="usr_1",
    )


def test_supported_operations_match_registry() -> None:
    """连接器能力声明与注册表逐项一致。"""
    spec = get_tool_registry().get("feishu")
    assert spec is not None
    assert set(FeishuConnector.declared_operations()) == set(spec.operation_names())
    assert len(FeishuConnector.declared_operations()) == 4
    FeishuConnector.assert_registry_consistency(spec.operation_names())


def test_registry_operations_are_business_language() -> None:
    """注册表能力名全为中文业务语言（PRD 验收 1）。"""
    spec = get_tool_registry().get("feishu")
    assert spec is not None
    display_names = [str(item.get("display_name", "")) for item in spec.operations]
    assert display_names == ["发送消息", "创建日程", "查看成员", "新建云文档"]


async def test_connect_and_disconnect() -> None:
    """授权连接返回过期时间；空授权码 → 13003；空连接断开 → 13002。"""
    result = await _connector().connect(
        auth_code="code-1", redirect_uri="https://x/cb", tenant_id="tnt_1", user_id="usr_1"
    )
    assert result["connection_id"].startswith("conn_")
    assert result["expires_at"]

    with pytest.raises(ApiError) as auth_error:
        await _connector().connect(
            auth_code="", redirect_uri="", tenant_id="tnt_1", user_id="usr_1"
        )
    assert auth_error.value.code == ErrorCode.TOOL_AUTH_EXPIRED

    with pytest.raises(ApiError) as conn_error:
        await _connector().disconnect(connection_id="", tenant_id="tnt_1", user_id="usr_1")
    assert conn_error.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_create_event_success() -> None:
    """创建日程成功返回 event_id 与结束时间。"""
    result = await _execute(
        "create_calendar_event",
        {
            "title": "需求评审",
            "start_time": "2026-11-02T10:00:00Z",
            "duration_minutes": 45,
            "participants": ["a@example.com", "b@example.com"],
        },
    )
    assert result["event_id"].startswith("evt_")
    assert result["title"] == "需求评审"
    assert result["participant_count"] == 2
    assert result["end_time"]
    assert WARNINGS_KEY not in result


async def test_create_event_partial_success_returns_warnings() -> None:
    """个别参与人未找到 → 整单成功 + warnings（PRD 验收 4）。"""
    result = await _execute(
        "create_calendar_event",
        {
            "title": "需求评审",
            "start_time": "2026-11-02T10:00:00Z",
            "participants": ["a@example.com", "b@example.com"],
            "invalid_participants": ["b@example.com"],
        },
    )
    assert result["event_id"].startswith("evt_")
    assert result[WARNINGS_KEY] == ["有 1 位参与人未找到，请核对后重试"]


async def test_create_event_all_participants_missing_raises_13006() -> None:
    """全部参与人都未找到 → 13006（不是部分成功）。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute(
            "create_calendar_event",
            {
                "title": "需求评审",
                "start_time": "2026-11-02T10:00:00Z",
                "participants": ["a@example.com"],
                "invalid_participants": ["a@example.com"],
            },
        )
    assert excinfo.value.code == ErrorCode.TOOL_THIRD_PARTY_ERROR
    assert "未找到" in excinfo.value.message


async def test_create_event_requires_title_and_start_time() -> None:
    """缺标题或开始时间 → 20001。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("create_calendar_event", {"title": "只有标题"})
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


async def test_create_event_over_participant_limit() -> None:
    """参与人超过 50 位 → 20001 + 业务文案。"""
    participants = [f"user{index}@example.com" for index in range(MAX_PARTICIPANTS + 1)]
    with pytest.raises(ApiError) as excinfo:
        await _execute(
            "create_calendar_event",
            {
                "title": "全员会",
                "start_time": "2026-11-02T10:00:00Z",
                "participants": participants,
            },
        )
    assert excinfo.value.code == ErrorCode.PARAM_INVALID
    assert excinfo.value.message == TOO_MANY_PARTICIPANTS_MESSAGE


async def test_permission_not_granted_maps_to_13005_with_hint() -> None:
    """应用未开通权限 → 13005 + 文案含「开通对应权限」（PRD 验收 3）。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("create_doc", {"title": "文档", "simulate": "forbidden"})
    assert excinfo.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED
    assert excinfo.value.message == PERMISSION_REQUIRED_MESSAGE
    assert "开通对应权限" in excinfo.value.message


async def test_unresponsive_maps_to_13004() -> None:
    """模拟第三方无响应 → 13004。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("list_members", {"simulate": "unresponsive"})
    assert excinfo.value.code == ErrorCode.TOOL_UNRESPONSIVE


async def test_send_message_and_create_doc() -> None:
    """发送消息与新建云文档返回业务化结果。"""
    message = await _execute("send_message", {"content": "同步一下", "receivers": ["a@b.com"]})
    assert message["message_id"].startswith("msg_")
    assert message["receiver_count"] == 1

    doc = await _execute("create_doc", {"title": "会议纪要", "content": "结论"})
    assert doc["doc_id"].startswith("fdoc_")
    assert doc["title"] == "会议纪要"


async def test_send_message_without_receiver_raises_20001() -> None:
    """无接收人 → 20001。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("send_message", {"content": "内容"})
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


async def test_execute_without_connection_returns_13002() -> None:
    """未连接 → 13002。"""
    with pytest.raises(ApiError) as excinfo:
        await _connector().execute(
            connection_id="",
            operation="list_members",
            params={},
            timeout_ms=15_000,
            tenant_id="tnt_1",
            user_id="usr_1",
        )
    assert excinfo.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_sync_is_unsupported() -> None:
    """飞书不支持数据同步任务 → 13005。"""
    with pytest.raises(ApiError) as excinfo:
        await _connector().sync(
            connection_id="conn_1",
            scope="all",
            params={},
            timeout_ms=15_000,
            tenant_id="tnt_1",
            user_id="usr_1",
        )
    assert excinfo.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED
