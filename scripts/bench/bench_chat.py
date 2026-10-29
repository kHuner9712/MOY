#!/usr/bin/env python
"""对话场景基准：首字延迟 / 完整响应时间 / 会话恢复。

对应口径：`docs/architecture-phase4.md` §9.6

| 本脚本指标 | §9.6 行 | 披露口径 |
| --- | --- | --- |
| `first_token_ms` | 首字 P95 ≤1s | ① mock（须标注「不含上游延迟」）② 真实模型（写明档位与网络） |
| `full_response_ms` | 无（自加） | 一次完整响应的墙钟，看端到端是否退化 |
| `session_restore_ms` | 会话恢复 ≤300ms | `GET .../messages?limit=30` 的 P95（含 PG） |

**为什么「首字」用 SSE 流式量**：非流式接口的响应时间是「整段生成完」，
拿它当首字会高估 10 倍以上；只有 SSE 能测出用户真正感知的「多久看到第一个字」。

用法：

    # 流式首字 + 完整响应（并发 10，共 100 次）
    python scripts/bench/bench_chat.py --mode chat \
        --base-url http://127.0.0.1:8000 --token "$TOKEN" \
        --concurrency 10 --requests 100 --upstream mock --out out/chat-mock.json

    # 会话恢复（需要已有会话 ID）
    python scripts/bench/bench_chat.py --mode session --conversation-id conv_01H... \
        --base-url http://127.0.0.1:8000 --token "$TOKEN" --requests 100

`--upstream` 是**必填的披露标签**，不是技术参数：§9.6 要求 mock 组必须写明
「不含上游模型延迟」。默认 `unspecified`，此时结果文件里 `upstream_label_ok=false`，
提醒你披露前必须补上——不填不许直接抄进对外文档。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

import _common as common

#: §9.6 首字 P95 阈值（毫秒）
FIRST_TOKEN_THRESHOLD_MS = 1000.0

#: §9.6 会话恢复 P95 阈值（毫秒）
SESSION_RESTORE_THRESHOLD_MS = 300.0

UPSTREAM_CHOICES = ("mock", "real", "unspecified")


def build_parser() -> argparse.ArgumentParser:
    """构造解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="bench_chat.py",
        description=(
            "对话场景基准（首字延迟 / 完整响应 / 会话恢复）。"
            "退出码：0 正常 / 1 有失败请求 / 2 用法 / 3 blocked 未执行 / 4 数据不足"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("chat", "session"),
        default="chat",
        help="chat=流式首字与完整响应；session=会话恢复（历史消息拉取）",
    )
    parser.add_argument(
        "--upstream",
        choices=UPSTREAM_CHOICES,
        default="unspecified",
        help=(
            "上游类型披露标签：mock=桩（不含上游模型延迟）/ real=真实模型 / "
            "unspecified=未标注（结果不可直接披露）"
        ),
    )
    parser.add_argument(
        "--profile", default="general", help="能力档位（general/code/long_context/reasoning）"
    )
    parser.add_argument("--prompt", default="", help="测试用提示词；缺省用内置短句")
    parser.add_argument("--conversation-id", default="", help="--mode session 时的会话 ID")
    parser.add_argument(
        "--limit", type=int, default=30, help="--mode session 时每页条数（§9.6 口径为 30）"
    )
    common.add_common_args(parser)
    return parser


async def _one_chat(
    client: Any,
    prompt: str,
    profile: str,
    first_token_ms: common.Measurement,
    first_byte_ms: common.Measurement,
    full_ms: common.Measurement,
) -> None:
    """发起一次流式请求并记录三个时点。

    Args:
        client: httpx 异步客户端。
        prompt: 提示词。
        profile: 能力档位。
        first_token_ms: 首字指标。
        first_byte_ms: 首字节指标。
        full_ms: 完整响应指标。
    """
    payload = {
        "conversation_id": "",
        "profile": profile,
        "messages": [{"role": "user", "content": prompt}],
        "context": {"history_window": 0, "memory_hints": [], "assemble_from": "server"},
        "knowledge_base_ids": [],
        "options": {},
    }
    headers = {"Idempotency-Key": common.idem_key(), "Accept": "text/event-stream"}
    started = time.perf_counter()
    first_byte_at: float | None = None
    first_token_at: float | None = None
    frame_event = ""
    try:
        async with client.stream(
            "POST", "/api/v1/model/generate/stream", json=payload, headers=headers
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")[:200]
                first_token_ms.errors.append(f"HTTP {response.status_code}: {body}")
                return
            async for line in response.aiter_lines():
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                    first_byte_ms.samples.append((first_byte_at - started) * 1000.0)
                if line.startswith("event:"):
                    frame_event = line.partition(":")[2].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                if (
                    frame_event == "delta"
                    and first_token_at is None
                    and line.partition(":")[2].strip()
                    not in (
                        "",
                        "{}",
                    )
                ):
                    first_token_at = time.perf_counter()
                    first_token_ms.samples.append((first_token_at - started) * 1000.0)
                if frame_event == "done":
                    break
                if frame_event == "error":
                    first_token_ms.errors.append(f"SSE error 帧：{line[:160]}")
                    return
    except Exception as exc:
        first_token_ms.errors.append(f"{type(exc).__name__}: {exc}")
        return
    elapsed = (time.perf_counter() - started) * 1000.0
    if first_token_at is None:
        first_token_ms.errors.append("流结束但未收到 delta 帧")
        return
    full_ms.samples.append(elapsed)


async def _one_session(
    client: Any, conversation_id: str, limit: int, restore_ms: common.Measurement
) -> None:
    """发起一次历史消息拉取。

    Args:
        client: httpx 异步客户端。
        conversation_id: 会话 ID。
        limit: 每页条数。
        restore_ms: 会话恢复指标。
    """
    started = time.perf_counter()
    try:
        response = await client.get(
            f"/api/v1/model/conversations/{conversation_id}/messages", params={"limit": limit}
        )
    except Exception as exc:
        restore_ms.errors.append(f"{type(exc).__name__}: {exc}")
        return
    if response.status_code >= 400:
        restore_ms.errors.append(f"HTTP {response.status_code}: {response.text[:160]}")
        return
    restore_ms.samples.append((time.perf_counter() - started) * 1000.0)


async def _drive(args: argparse.Namespace, tasks: list[Any]) -> None:
    """按并发度跑完所有任务。

    Args:
        args: 命令行参数。
        tasks: 可 await 的任务列表。
    """
    semaphore = asyncio.Semaphore(args.concurrency)

    async def guarded(coro: Any) -> None:
        async with semaphore:
            await coro

    await asyncio.gather(*(guarded(task) for task in tasks))


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 退出码。
    """
    args = build_parser().parse_args(argv)
    try:
        base_url, token, tenant_id = common.resolve_target(args)
    except common.BlockedError as exc:
        common.fail(f"BLOCKED: 未执行 —— {exc}（不生成任何结果文件）", common.EXIT_BLOCKED)

    if args.mode == "session" and not args.conversation_id:
        common.fail("--mode session 需要 --conversation-id", common.EXIT_USAGE)

    first_token = common.Measurement("first_token_ms", "ms", threshold_ms=FIRST_TOKEN_THRESHOLD_MS)
    first_byte = common.Measurement("first_byte_ms", "ms")
    full = common.Measurement("full_response_ms", "ms")
    restore = common.Measurement(
        "session_restore_ms", "ms", threshold_ms=SESSION_RESTORE_THRESHOLD_MS
    )

    prompt = args.prompt or "用一句话说明今天适合做什么。"

    async def runner() -> None:
        client = common.build_client(
            base_url, token, tenant_id, args.timeout, trust_env=args.trust_env
        )
        try:
            await common.require_healthy(client)
            if args.mode == "chat":
                tasks = [
                    _one_chat(client, prompt, args.profile, first_token, first_byte, full)
                    for _ in range(args.requests)
                ]
            else:
                tasks = [
                    _one_session(client, args.conversation_id, args.limit, restore)
                    for _ in range(args.requests)
                ]
            await _drive(args, tasks)
        finally:
            await client.aclose()

    try:
        common.run_coroutine(runner())
    except common.BlockedError as exc:
        common.fail(f"BLOCKED: 未执行 —— {exc}（不生成任何结果文件）", common.EXIT_BLOCKED)

    metrics = [first_token, first_byte, full] if args.mode == "chat" else [restore]
    extra = {
        "mode": args.mode,
        "disclosure": common.disclosure_block(args.upstream),
        "upstream_label_ok": args.upstream != "unspecified",
        "threshold_note": {
            "first_token_ms": "§9.6 首字 P95 ≤1s（mock 组须标注不含上游模型延迟）",
            "session_restore_ms": "§9.6 会话恢复 ≤300ms（30 条消息，含 PG）",
        },
    }
    if args.mode == "session":
        extra["params_note"] = {"limit": args.limit, "conversation_id": args.conversation_id}
    else:
        extra["params_note"] = {"profile": args.profile, "prompt": prompt}

    payload = common.build_payload("chat", base_url, args, metrics, args.requests, extra)
    return common.finish(args, "chat", payload, metrics, args.requests)


if __name__ == "__main__":
    sys.exit(main())
