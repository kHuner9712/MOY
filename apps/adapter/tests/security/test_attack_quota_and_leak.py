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
from tests.integration.test_quota_notice import _generate_payload, _seed_usage, _write_headers
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
    """额度用尽：402 且上游 0 次。"""

    def test_hard_stop_calls_no_upstream(
        self,
        client: Any,
        settings: Any,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超额请求 → 402，且 `DifyClient.for_app_key` 一次都没被调用。"""
        # 造"已超额"的计费状态（复用 B27 的造数器，避免另写一套额度口径）
        import asyncio

        state = asyncio.run(_seed_usage(settings, over=True))
        assert state.over_quota

        created: list[str] = []

        def _for_app_key(cls: Any, key: str, settings: Any = None) -> Any:
            created.append(key)
            raise AssertionError("额度用尽时不应创建上游客户端")

        monkeypatch.setattr(
            "app.clients.dify_client.DifyClient.for_app_key", classmethod(_for_app_key)
        )

        response = client.post(
            GENERATE_PATH,
            json=_generate_payload(),
            headers=_write_headers(auth_headers, "sec-quota"),
        )

        assert response.status_code == 402
        assert response.json()["code"] == 15006
        assert created == []
        _assert_clean(response.json()["message"], label="402 额度用尽")


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
