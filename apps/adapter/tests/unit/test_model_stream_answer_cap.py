"""流式「常规模式」整帧截断（判据 ①b，P4-11 ①，2026-10-27 用户裁决）。

三条断言（缺一不可）：

1. `delta` 拼接后长度 **≤ `standard_mode_answer_chars`**（用 `<=`：整帧丢弃可能略小于上限）；
2. 终帧 `done` 带 `notice_code == 15006`；
3. **上游被完整消费**（桩记录"迭代到耗尽"）——防的是"为了截断把流提前 `break`"，
   那会破坏连接复用与计费完整性，而且从响应上看不出来。

为什么不断言"恰好等于 300"：实现是**整帧丢弃**（放不下就整帧不发），
最后一帧被丢掉后长度通常略小于上限。断言 `==` 会把正确实现判红。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.business.usage_service import BudgetStatus, UsageService
from app.core.config import Settings
from app.core.security import AuthContext
from app.schemas.model import GenerateRequest
from app.services.answer_cap import STANDARD_MODE_NOTICE
from app.services.model_service import ModelService

pytestmark = pytest.mark.unit

USER = "usr_cap_stream"
TENANT = "tnt_cap_stream"


class _LongStreamDify:
    """流式桩：连续吐长帧，并记录"是否被迭代到耗尽"。

    Attributes:
        exhausted: 生成器是否被完整迭代（`True` 表示上游被消费完）。
        frames: 已下发的帧数。
    """

    def __init__(self, chunk: str, count: int) -> None:
        """初始化桩。

        Args:
            chunk: 每帧正文。
            count: 帧数。
        """
        self._chunk = chunk
        self._count = count
        self.exhausted = False
        self.frames = 0

    async def post_stream(
        self, path: str, payload: dict[str, Any], **_: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """产出底座原生事件。

        Args:
            path: 下游路径（未使用）。
            payload: 请求体（未使用）。
            **_: 其它参数。

        Yields:
            dict[str, Any]: 底座原生事件。
        """
        del path, payload
        for _ in range(self._count):
            self.frames += 1
            yield {"event": "message", "answer": self._chunk}
        # 走到这里说明上游被消费完（没有 break）
        self.exhausted = True
        yield {"event": "message_end", "metadata": {"usage": {"total_tokens": 500}}}


def _settings() -> Settings:
    """构造非桩模式配置（流式必须真走 `post_stream` 分支）。

    Returns:
        Settings: 配置。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        mock_mode=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
        dify_base_url="http://dify.test",
        dify_default_app_key="test-key",
        standard_mode_answer_chars=300,
    )


@pytest.mark.asyncio
async def test_stream_is_frame_capped_and_consumes_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超额流式：整帧截断到上限内、终帧带 15006、且上游被消费完。"""
    settings = _settings()
    dify = _LongStreamDify(chunk="这是一段很长的回答内容。", count=40)  # 40×11=440 字
    usage = UsageService(settings)

    async def _exhausted(*_: Any, **__: Any) -> BudgetStatus:
        return BudgetStatus(exhausted=True)

    monkeypatch.setattr(usage, "check_capability_allowed", _exhausted)
    monkeypatch.setattr(usage, "quota_notice", lambda **_: _no_notice())
    model = ModelService(settings, dify=dify, usage=usage)  # type: ignore[arg-type]
    request = GenerateRequest(
        conversation_id="conv_cap_stream",
        profile="general",
        messages=[{"role": "user", "content": "写一段长文"}],
    )

    deltas: list[str] = []
    done_payload: dict[str, Any] = {}
    async for frame in model.generate_stream(
        request, USER, AuthContext(user_id=USER, tenant_id=TENANT)
    ):
        event, _, data = frame.partition("\n")
        payload = json.loads(data.removeprefix("data: "))
        if event.startswith("event: delta"):
            deltas.append(str(payload.get("text", "")))
        elif event.startswith("event: done"):
            done_payload = payload

    joined = "".join(deltas)
    assert len(joined) <= settings.standard_mode_answer_chars, (
        f"流式未按整帧收口：拼接长度 {len(joined)} > {settings.standard_mode_answer_chars}"
    )
    assert joined, "整帧截断不能把内容全丢光（至少第一帧应放得下）"
    assert dify.frames == 40, f"桩未被使用（frames={dify.frames}）→ 断言对象不是真流式"
    assert done_payload.get("notice_code") == 15006, (
        f"终帧必须带常规模式提示码，实际 {done_payload.get('notice_code')!r}"
    )
    assert done_payload.get("notice_message") == STANDARD_MODE_NOTICE
    assert dify.exhausted is True, "上游必须被完整消费（提前 break 会破坏连接复用与计费）"


@pytest.mark.asyncio
async def test_stream_not_capped_when_quota_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向断言：额度充足时**不**截断、**不**加提示（防"限长打到全部用户"）。"""
    settings = _settings()
    dify = _LongStreamDify(chunk="这是一段很长的回答内容。", count=40)
    usage = UsageService(settings)

    async def _funded(*_: Any, **__: Any) -> BudgetStatus:
        return BudgetStatus(exhausted=False)

    monkeypatch.setattr(usage, "check_capability_allowed", _funded)
    monkeypatch.setattr(usage, "quota_notice", lambda **_: _no_notice())
    model = ModelService(settings, dify=dify, usage=usage)  # type: ignore[arg-type]
    request = GenerateRequest(
        conversation_id="conv_cap_stream_ok",
        profile="general",
        messages=[{"role": "user", "content": "写一段长文"}],
    )

    deltas: list[str] = []
    done_payload: dict[str, Any] = {}
    async for frame in model.generate_stream(
        request, USER, AuthContext(user_id=USER, tenant_id=TENANT)
    ):
        event, _, data = frame.partition("\n")
        payload = json.loads(data.removeprefix("data: "))
        if event.startswith("event: delta"):
            deltas.append(str(payload.get("text", "")))
        elif event.startswith("event: done"):
            done_payload = payload

    assert dify.frames == 40, f"桩未被使用（frames={dify.frames}）→ 断言对象不是真流式"
    assert len("".join(deltas)) > settings.standard_mode_answer_chars, (
        "未超额却把回答截断了（限长被扩到了全部用户）"
    )
    assert done_payload.get("notice_code", 0) == 0, "未超额不应带 15006 提示"


async def _no_notice() -> tuple[int, str]:
    """替身：无额度提示。

    Returns:
        tuple[int, str]: 恒 `(0, "")`。
    """
    return 0, ""
