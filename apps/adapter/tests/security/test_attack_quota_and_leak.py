"""攻击 5：额度滥用与信息外泄（阶段四 T4-04.6）。

两件事必须同时成立：

1. **额度用尽时不烧钱**：返回 402 且**一次上游调用都没有**（否则"用户被拒绝、账单还在涨"）；
2. **拒绝响应不泄漏实现**：五类拒绝（401/402/403/404/400）的 `message` 里不得出现
   SQL 片段、表名、堆栈、以及 4-5 位业务码——这些是攻击者做侦察最想要的东西。

第二条容易被当成"文案洁癖"：真实风险是 `WHERE tenant_id=...` 这类片段会暴露表结构，
`Traceback` 会暴露框架与版本，业务码会让攻击者能按码试探边界。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from tests.integration.test_quota_notice import _generate_payload, _seed_usage
from tests.security.conftest import TENANT_A, issue_token, write_headers

from app.core.errors import ApiError, ErrorCode

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 生成端点
GENERATE_PATH = "/api/v1/model/generate"

#: 泄漏模式（命中即缺陷）
LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SQL 关键字", re.compile(r"(?i)\b(select|insert|update|delete|where|join|pg_|asyncpg)\b")),
    ("表名", re.compile(r"(tenant_id|user_memory|usage_event|background_job|tenant_budget)")),
    ("堆栈", re.compile(r"(Traceback|File \"|line \d+, in |Error:)")),
    ("4-5 位业务码", re.compile(r"\b[1-3]\d{4}\b")),
)


def _assert_clean(message: str, *, label: str) -> None:
    """断言一条用户可见文案不含任何泄漏模式。

    Args:
        message: 业务文案。
        label: 场景标签（失败时报出是哪一类拒绝）。
    """
    assert message, f"{label}：拒绝响应必须有业务文案"
    for name, pattern in LEAK_PATTERNS:
        matched = pattern.search(message)
        assert matched is None, f"{label}：文案泄漏了{name}（{matched.group(0)!r}）→ {message}"


class TestQuotaAbuse:
    """额度用尽：对话**继续可用但限长**（P4-11 ①，2026-10-27 用户裁决）。

    历史：本用例原为 `test_hard_stop_calls_no_upstream`，断言「402 + 上游 0 次」。
    改判据的原因：PRD 5.3 ① 要求「≥100% 对话继续可用、进入常规模式、单次回答限长」，
    用户据此裁决**撤销对话的硬止损**。本用例因此从「硬止损判据」变成
    「软限制判据」——**没有删除**：删掉就等于对超额路径失去任何断言。

    **代价（如实标注，已进 PRD 5.3 ① 与风险台账）**：超额用户每次对话
    仍会产生一次上游调用（输出限 300 字 ≈ 0.015% 月额度/次，**次数无上限**）。
    R3/A1 的超额仍是硬止损（不调底座），见
    `tests/unit/test_rag_service.py::test_upload_is_held_...` 与
    `tests/unit/test_task_quota_hold.py`——两种处置并存，别混。

    覆盖范围说明（诚实标注，勿当成"全绿"）：本条断言**判定层与限长层**；
    HTTP 层的 `code==0` + 上游恰好 1 次（判据 ①a/①d）尚未补，
    已在 b 批交付说明里标为待补。
    """

    def test_exhausted_is_no_longer_a_hard_stop(
        self,
        client: Any,
        settings: Any,
        auth_headers: dict[str, str],
    ) -> None:
        """超额请求**不得**再返回 402/15006（否则说明有人把软限制改回了硬拦）。

        这是本用例在新口径下的**唯一职责**：防止"把对话硬止损悄悄装回去"。
        正向判据（`code==0` + 上游恰好 1 次 + 限长 300 + `notice_code=15006`）
        需要可用的上游桩，属判据 ①a/①d，**尚未补**——已在 b 批交付说明里标明，
        不在此处假装覆盖。
        """
        import asyncio

        state = asyncio.run(_seed_usage(settings, over=True))
        assert state.over_quota

        response = client.post(
            GENERATE_PATH,
            json=_generate_payload(),
            headers=auth_headers,
        )

        body = response.json()
        assert response.status_code != 402, "超额对话不得再返回 402（PRD 5.3 ① 软限制）"
        assert body.get("code") != 15006, "超额对话不得再以 15006 拦停（应限长后 code=0）"


class TestNoInformationLeak:
    """五类拒绝响应的文案都要干净。"""

    def test_five_rejection_categories_are_clean(
        self, client: Any, auth_headers: dict[str, str], oidc_client: Any
    ) -> None:
        """401 / 402 / 403 / 404 / 400 五类拒绝的 `message` 逐个扫描。"""
        messages: list[tuple[str, str]] = []

        # ① 401：伪造令牌（错误密钥）
        forged = issue_token(secret="attacker-secret", tenant_id=TENANT_A)
        unauthorized = oidc_client.get(
            "/api/v1/model/catalog",
            headers={"Authorization": f"Bearer {forged}", "X-Tenant-Id": TENANT_A},
        )
        messages.append(("401 伪造令牌", unauthorized.json()["message"]))

        # ② 402：额度用尽（真实造数：直接以 ApiError 形态取文案，避免依赖计费夹具）
        messages.append(("402 额度用尽", ApiError(ErrorCode.QUOTA_EXHAUSTED).message))

        # ③ 403：无角色令牌访问受保护写端点
        no_role = issue_token(tenant_id=TENANT_A, roles=None)
        forbidden = client.delete(
            "/api/v1/knowledge/bases/kb_sec_leak",
            headers=write_headers(
                no_role, tenant=TENANT_A, key="33334444-5555-4666-8777-888899990000"
            ),
        )
        messages.append(("403 权限不足", forbidden.json()["message"]))

        # ④ 404：删除不存在的记忆
        not_found = client.delete(
            "/api/v1/model/memories/mem_sec_leak",
            headers={
                **auth_headers,
                **write_headers(issue_token(), key="44445555-6666-4777-8888-999900001111"),
            },
        )
        messages.append(("404 记忆不存在", not_found.json()["message"]))

        # ⑤ 400：参数非法（缺幂等键）
        bad = client.post(GENERATE_PATH, json=_generate_payload(), headers=auth_headers)
        messages.append(("400 参数非法", bad.json()["message"]))

        for label, message in messages:
            _assert_clean(message, label=label)

        # 业务码只能出现在信封的 `code` 字段里，不能混进文案
        for label, message in messages:
            assert "20003" not in message and "15006" not in message, label
