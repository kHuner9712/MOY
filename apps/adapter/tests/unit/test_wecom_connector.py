"""企业微信连接器单元测试（阶段二 B06，PRD P2-TL-01）。

覆盖点：

1. 能力清单 4 项且与注册表一致（`assert_registry_consistency`）；
2. `send_message`：正常返回 `message_id` 与接收人数；超 200 人 → `20001` +
   PRD 原文文案「一次最多发送给 200 人，请分批发送」；
3. `create_group`：缺参数 → `20001`；部分成员找不到 → 整单成功 + `warnings`；
4. 异常映射：空授权码 → `13003`；未连接 → `13002`；模拟异常 → `13003` / `13004`；
   `member_missing` → `13006` + 文案「未找到该成员，请确认后在通讯录中核对」；
5. 响应与日志中不出现第三方术语（`access_token` / `corp_id` / `secret`）。
"""

from __future__ import annotations

import pytest

from app.core.errors import ApiError, ErrorCode
from app.registry.loader import get_tool_registry
from app.tools.base import WARNINGS_KEY
from app.tools.wecom_connector import (
    MAX_RECIPIENTS,
    MEMBER_MISSING_MESSAGE,
    TOO_MANY_RECIPIENTS_MESSAGE,
    WecomConnector,
)

pytestmark = pytest.mark.unit

_FORBIDDEN_TERMS = ("access_token", "corp_id", "appsecret", "secret")


def _connector() -> WecomConnector:
    """构造连接器实例。

    Returns:
        WecomConnector: 连接器。
    """
    return WecomConnector()


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
        timeout_ms=10_000,
        tenant_id="tnt_1",
        user_id="usr_1",
    )


def test_supported_operations_match_registry() -> None:
    """连接器能力声明与注册表逐项一致。"""
    spec = get_tool_registry().get("wecom")
    assert spec is not None
    assert set(WecomConnector.declared_operations()) == set(spec.operation_names())
    assert len(WecomConnector.declared_operations()) == 4
    WecomConnector.assert_registry_consistency(spec.operation_names())


def test_registry_operations_are_business_language() -> None:
    """注册表能力名全为中文业务语言（PRD 验收 2）。"""
    spec = get_tool_registry().get("wecom")
    assert spec is not None
    display_names = [str(item.get("display_name", "")) for item in spec.operations]
    assert display_names == ["发送消息", "查看通讯录部门", "查看通讯录成员", "创建群聊"]


async def test_connect_returns_expiry_and_empty_code_raises_13003() -> None:
    """空授权码 → 13003；正常授权返回过期时间。"""
    with pytest.raises(ApiError) as excinfo:
        await _connector().connect(
            auth_code=" ", redirect_uri="https://x/cb", tenant_id="tnt_1", user_id="usr_1"
        )
    assert excinfo.value.code == ErrorCode.TOOL_AUTH_EXPIRED

    result = await _connector().connect(
        auth_code="code-1", redirect_uri="https://x/cb", tenant_id="tnt_1", user_id="usr_1"
    )
    assert result["connection_id"].startswith("conn_")
    assert result["expires_at"]


async def test_send_message_returns_message_id() -> None:
    """正常发送返回 message_id 与接收人数。"""
    result = await _execute(
        "send_message",
        {"content": "下午三点面试", "members": ["usr_a", "usr_b"], "departments": ["dept_x"]},
    )
    assert result["message_id"].startswith("msg_")
    assert result["recipient_count"] == 3


async def test_send_message_without_recipient_raises_20001() -> None:
    """无收件人 → 20001。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("send_message", {"content": "内容"})
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


async def test_send_message_over_limit_returns_prd_message() -> None:
    """单次超过 200 人 → 20001 + PRD 原文文案（PRD 验收 3）。"""
    members = [f"usr_{index}" for index in range(MAX_RECIPIENTS + 1)]
    with pytest.raises(ApiError) as excinfo:
        await _execute("send_message", {"content": "内容", "members": members})
    assert excinfo.value.code == ErrorCode.PARAM_INVALID
    assert excinfo.value.message == TOO_MANY_RECIPIENTS_MESSAGE


async def test_member_missing_maps_to_13006() -> None:
    """企业微信业务错误（成员不存在）→ 13006 + PRD 原文文案。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute(
            "send_message", {"content": "内容", "members": ["usr_a"], "simulate": "member_missing"}
        )
    assert excinfo.value.code == ErrorCode.TOOL_THIRD_PARTY_ERROR
    assert excinfo.value.message == MEMBER_MISSING_MESSAGE


async def test_list_departments_and_members_return_business_shape() -> None:
    """通讯录两类查询返回业务化结构。"""
    departments = await _execute("list_departments")
    assert departments == {"departments": [], "total": 0}
    members = await _execute("list_members", {"keyword": "张"})
    assert members == {"members": [], "total": 0}


async def test_create_group_requires_name_and_members() -> None:
    """缺少群名或成员 → 20001。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("create_group", {"name": "面试群"})
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


async def test_create_group_partial_success_returns_warnings() -> None:
    """部分成员找不到 → 整单成功 + warnings（不判整单失败）。"""
    result = await _execute(
        "create_group",
        {
            "name": "面试群",
            "member_ids": ["usr_a", "usr_b"],
            "invalid_member_ids": ["usr_b"],
        },
    )
    assert result["chat_id"].startswith("chat_")
    assert result[WARNINGS_KEY] == ["有 1 位成员未找到，请核对后重试"]


async def test_execute_without_connection_returns_13002() -> None:
    """未连接 → 13002。"""
    with pytest.raises(ApiError) as excinfo:
        await _connector().execute(
            connection_id="",
            operation="send_message",
            params={"content": "x", "members": ["usr_a"]},
            timeout_ms=10_000,
            tenant_id="tnt_1",
            user_id="usr_1",
        )
    assert excinfo.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_unsupported_operation_returns_13005() -> None:
    """操作不在能力清单 → 13005。"""
    with pytest.raises(ApiError) as excinfo:
        await _execute("delete_everything")
    assert excinfo.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED


async def test_simulated_auth_expired_and_unresponsive() -> None:
    """模拟授权过期 → 13003；模拟无响应 → 13004。"""
    with pytest.raises(ApiError) as expired:
        await _execute("list_members", {"simulate": "auth_expired"})
    assert expired.value.code == ErrorCode.TOOL_AUTH_EXPIRED

    with pytest.raises(ApiError) as unresponsive:
        await _execute("list_members", {"simulate": "unresponsive"})
    assert unresponsive.value.code == ErrorCode.TOOL_UNRESPONSIVE


async def test_sync_is_unsupported() -> None:
    """企业微信不支持数据同步任务 → 13005。"""
    with pytest.raises(ApiError) as excinfo:
        await _connector().sync(
            connection_id="conn_1",
            scope="all",
            params={},
            timeout_ms=10_000,
            tenant_id="tnt_1",
            user_id="usr_1",
        )
    assert excinfo.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED


async def test_results_contain_no_forbidden_terms() -> None:
    """返回结果不含第三方术语（技术黑盒化红线）。"""
    result = await _execute("send_message", {"content": "内容", "members": ["usr_a"]})
    text = str(result).lower()
    for term in _FORBIDDEN_TERMS:
        assert term not in text
