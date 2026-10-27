"""「常规模式」回答限长（PRD 5.3 ①，P4-11 b 批）。

**命名说明（勿改）**：这里刻意不用 `truncate` / `truncated` —— 那组词在
`conversation_service` / `conversation.py:213` / 租户导出列里已表示
「**流式断连或超时后保留已收内容**」。两者语义完全不同（一个是产品策略限长，
一个是传输中断的残留），混用会让排障时分不清"用户是被限长了"还是"连接断了"。

**为什么按字符而不按 token**：Dify chat 不接受 per-request 的 `max_tokens`
（只能改底座应用配置 = 跨应用副作用），而"降历史条数"会让回答从"变短"退化成
"变傻"（丢上下文），踩 PRD 红线。因此只能在**已生成的正文**上做字符级收口。
"""

from __future__ import annotations

from typing import Final

#: 常规模式提示语（PRD 5.3 ① 原文；业务语言、零技术词）
STANDARD_MODE_NOTICE: Final[str] = "额度已用尽，当前为常规模式，升级套餐可恢复完整能力"


def apply_answer_cap(
    content: str, *, limit: int, notice: str = STANDARD_MODE_NOTICE
) -> tuple[str, bool]:
    """按字符上限收口回答，并追加「常规模式」提示。

    按**码点**计数（`len(str)`）：中文一个字算一个，符合用户"字变短了"的感知；
    按字节判断会在多字节字符上误判长度。

    幂等：已带提示语的正文原样返回——避免同一响应被两处（非流式 + 结算前）
    各收一次，把提示语自己截掉。

    Args:
        content: 原始回答正文。
        limit: 字符上限（`Settings.standard_mode_answer_chars`）。
        notice: 追加的提示语；传空串表示只截断不加提示。

    Returns:
        tuple[str, bool]: (处理后的正文, 是否发生了收口)。
    """
    body = content.strip()
    tail = notice.strip()
    if tail and body.endswith(tail):
        return content, True
    if len(body) <= limit:
        return content, False
    capped = body[:limit].rstrip()
    if not tail:
        return capped, True
    return f"{capped}\n\n{tail}", True
