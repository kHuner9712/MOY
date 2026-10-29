#!/usr/bin/env python
"""知识库场景基准：检索耗时 / 文档解析耗时。

对应口径：`docs/architecture-phase4.md` §9.6

| 本脚本指标 | 覆盖的能力 | 披露要点 |
| --- | --- | --- |
| `search_ms` | R7 `/knowledge/search` | 须写明文档数、返回条数与阈值——100 篇与 10 万篇不同 |
| `parse_ms` | R3 上传 → R4 轮询 | 解析是**异步**的，「上传到能搜」整段才是用户感知的等待 |

**不覆盖**：§9.6 的「记忆召回 ≤300ms」不是本脚本的事——它由 M1 组装上下文时
内部注入，接口层没有独立入口；要测只能在**预置 200 条记忆**的环境上跑
`bench_chat.py --mode chat` 并对比开关前后的首字延迟，属另一套压测场景，
不在本脚本虚构一个「记忆召回接口」来凑数。

用法：

    # 检索（并发 10，共 200 次）
    python scripts/bench/bench_kb.py --mode search --base-id kb_001 \
        --base-url http://127.0.0.1:8000 --token "$TOKEN" \
        --concurrency 10 --requests 200 --query "差旅报销标准" --out out/kb-search.json

    # 文档解析（并发 1：解析是串行资源，并发会互相排队，量出的不是单篇耗时）
    python scripts/bench/bench_kb.py --mode parse --base-id kb_001 \
        --base-url http://127.0.0.1:8000 --token "$TOKEN" \
        --concurrency 1 --requests 5 --sample-file ./样例.md --out out/kb-parse.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import _common as common

#: R4 轮询间隔（秒）。解析是异步的，间隔太密会把被测服务自己压出噪声
POLL_INTERVAL_S = 1.0

#: R4 轮询上限（秒）
POLL_TIMEOUT_S = 600.0

#: 视为「已可检索」的 index_status 取值
READY_STATES = ("ready", "indexed", "succeeded", "completed", "done")

#: 视为「处理失败」的 index_status 取值
FAILED_STATES = ("failed", "error", "cancelled")

DEFAULT_SAMPLE_TEXT = (
    "# 差旅报销管理办法（基准样例）\n\n"
    "## 一、住宿标准\n一线城市每晚不超过 600 元，二线城市不超过 450 元。\n\n"
    "## 二、市内交通\n每日不超过 120 元，需提供行程单。\n\n"
    "## 三、超标处理\n超标部分需部门负责人审批，并附书面说明。\n"
)


def build_parser() -> argparse.ArgumentParser:
    """构造解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="bench_kb.py",
        description=(
            "知识库场景基准（检索 / 文档解析）。"
            "退出码：0 正常 / 1 有失败请求 / 2 用法 / 3 blocked 未执行 / 4 数据不足"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("search", "parse"),
        default="search",
        help="search=检索；parse=上传到可检索",
    )
    parser.add_argument("--base-id", required=True, help="知识库 ID（R3/R7 都要）")
    parser.add_argument("--query", default="差旅报销标准", help="--mode search 的查询语句")
    parser.add_argument("--top-k", type=int, default=5, help="--mode search 返回条数（默认 5）")
    parser.add_argument(
        "--score-threshold", type=float, default=0.5, help="--mode search 相关度阈值（默认 0.5）"
    )
    parser.add_argument(
        "--sample-file", default="", help="--mode parse 的样例文件；缺省用内置短文本"
    )
    parser.add_argument(
        "--doc-count", type=int, default=0, help="知识库现有文档数（写进结果便于披露，不参与计算）"
    )
    common.add_common_args(parser)
    return parser


async def _one_search(client: Any, args: argparse.Namespace, metric: common.Measurement) -> None:
    """发起一次检索。

    Args:
        client: httpx 异步客户端。
        args: 命令行参数。
        metric: 检索耗时指标。
    """
    payload = {
        "base_id": args.base_id,
        "query": args.query,
        "top_k": args.top_k,
        "score_threshold": args.score_threshold,
    }
    started = time.perf_counter()
    try:
        response = await client.post(
            "/api/v1/knowledge/search", json=payload, headers={"Idempotency-Key": common.idem_key()}
        )
    except Exception as exc:
        metric.errors.append(f"{type(exc).__name__}: {exc}")
        return
    elapsed = (time.perf_counter() - started) * 1000.0
    if response.status_code >= 400:
        metric.errors.append(f"HTTP {response.status_code}: {response.text[:160]}")
        return
    body = response.json()
    if int(body.get("code", -1)) != 0:
        metric.errors.append(f"code={body.get('code')} msg={body.get('message')}")
        return
    metric.samples.append(elapsed)


def load_sample(args: argparse.Namespace) -> tuple[bytes, str]:
    """读取（或生成）上传用的样例文件内容。

    **在进入事件循环前完成**：文件读取是阻塞 I/O，放在 async 函数里会卡住事件循环，
    让并发测量失去意义（ASYNC240 的实质原因，不只是风格问题）。

    Args:
        args: 命令行参数。

    Returns:
        tuple[bytes, str]: `(内容, 文件名)`。

    Raises:
        SystemExit: 样例文件不存在（退出 2，不进入测量）。
    """
    if not args.sample_file:
        return DEFAULT_SAMPLE_TEXT.encode("utf-8"), f"bench-{uuid.uuid4().hex[:8]}.md"
    source = Path(args.sample_file)
    if not source.is_file():
        common.fail(f"--sample-file 不存在：{source}", common.EXIT_USAGE)
    return source.read_bytes(), source.name


async def _one_parse(
    client: Any, args: argparse.Namespace, metric: common.Measurement, sample: tuple[bytes, str]
) -> None:
    """上传一次样例文件并轮询到可检索，记录整段墙钟。

    Args:
        client: httpx 异步客户端。
        args: 命令行参数。
        metric: 解析耗时指标。
        sample: `(内容, 文件名)`，由 `load_sample` 预先读好。
    """
    content, file_name = sample
    started = time.perf_counter()
    try:
        response = await client.post(
            f"/api/v1/knowledge/bases/{args.base_id}/documents",
            files={"file": (file_name, content, "text/markdown")},
            headers={"Idempotency-Key": common.idem_key()},
        )
    except Exception as exc:
        metric.errors.append(f"上传异常 {type(exc).__name__}: {exc}")
        return
    if response.status_code >= 400:
        metric.errors.append(f"上传失败 HTTP {response.status_code}: {response.text[:160]}")
        return
    body = response.json()
    if int(body.get("code", -1)) != 0:
        metric.errors.append(f"上传 code={body.get('code')} msg={body.get('message')}")
        return
    doc_id = str((body.get("data") or {}).get("document_id", ""))
    if not doc_id:
        metric.errors.append("上传成功但未返回 document_id，无法轮询")
        return

    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            status_response = await client.get(
                f"/api/v1/knowledge/bases/{args.base_id}/documents/{doc_id}"
            )
        except Exception as exc:
            metric.errors.append(f"轮询异常 {type(exc).__name__}: {exc}")
            return
        if status_response.status_code >= 400:
            metric.errors.append(f"轮询失败 HTTP {status_response.status_code}")
            return
        data = dict(status_response.json().get("data") or {})
        state = str(data.get("index_status", "")).lower()
        if state in READY_STATES or int(data.get("progress", 0)) >= 100:
            metric.samples.append((time.perf_counter() - started) * 1000.0)
            return
        if state in FAILED_STATES:
            metric.errors.append(f"文档处理失败：{data.get('error_message', '')}")
            return
    metric.errors.append(f"轮询超时（{POLL_TIMEOUT_S}s）仍未完成")


async def _drive(args: argparse.Namespace, coros: list[Any]) -> None:
    """按并发度跑完所有任务。

    Args:
        args: 命令行参数。
        coros: 协程列表。
    """
    semaphore = asyncio.Semaphore(args.concurrency)

    async def guarded(coro: Any) -> None:
        async with semaphore:
            await coro

    await asyncio.gather(*(guarded(coro) for coro in coros))


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

    metric = (
        common.Measurement("search_ms", "ms")
        if args.mode == "search"
        else common.Measurement("parse_ms", "ms")
    )
    #: 样例文件在进入事件循环前读好（阻塞 I/O 不进 async，见 `load_sample`）
    sample = load_sample(args) if args.mode == "parse" else (b"", "")

    async def runner() -> None:
        client = common.build_client(
            base_url, token, tenant_id, args.timeout, trust_env=args.trust_env
        )
        try:
            await common.require_healthy(client)
            if args.mode == "search":
                coros = [_one_search(client, args, metric) for _ in range(args.requests)]
            else:
                coros = [_one_parse(client, args, metric, sample) for _ in range(args.requests)]
            await _drive(args, coros)
        finally:
            await client.aclose()

    try:
        common.run_coroutine(runner())
    except common.BlockedError as exc:
        common.fail(f"BLOCKED: 未执行 —— {exc}（不生成任何结果文件）", common.EXIT_BLOCKED)

    extra = {
        "mode": args.mode,
        "base_id": args.base_id,
        "kb_doc_count": args.doc_count,
        "params_note": (
            {
                "query": args.query,
                "top_k": args.top_k,
                "score_threshold": args.score_threshold,
                "kb_doc_count": args.doc_count,
            }
            if args.mode == "search"
            else {
                "sample_file": args.sample_file or "<内置短文本>",
                "poll_interval_s": POLL_INTERVAL_S,
            }
        ),
        "disclosure_requirement": (
            "检索耗时必须同时写明知识库文档数、返回条数、相关度阈值；"
            "解析耗时为「上传到可检索」的整段墙钟，不是上传接口耗时"
        ),
    }
    payload = common.build_payload("kb", base_url, args, [metric], args.requests, extra)
    return common.finish(args, "kb", payload, [metric], args.requests)


if __name__ == "__main__":
    sys.exit(main())
