#!/usr/bin/env python
"""插件任务场景基准：端到端墙钟（建任务 → 跑完 → 拿到产物）。

对应口径：`docs/architecture-phase4.md` §9.6 的「并发」行与 §8.2 的插件任务链路。
本脚本量的是**用户真实等待**：从点下确认到产物可下载，中间跨了
A1 建任务、声明式步骤推进、（可选）人工接管、A3 轮询。
只看「建任务接口返回了多少毫秒」会漏掉 95% 的时间——任务主体是异步跑的。

| 本脚本指标 | 含义 | 披露要点 |
| --- | --- | --- |
| `e2e_ms` | 单次任务从创建到终态的墙钟 | 必须写明插件 ID、步骤总数、是否经过人工确认 |
| `create_ms` | A1 建任务接口耗时 | 小到几乎无意义，单列是为了区分「接口慢」与「步骤慢」 |
| `step_poll_count` | 轮询次数 | 轮询是测量成本，披露时必须说明，否则别人复测对不上数 |

**并发语义提醒**：`--concurrency > 1` 时多个任务会抢同一个插件执行通道，
量到的是「排队 + 执行」而不是「执行」。§9.6 的并发阶梯（100/200/500）
是**平台级**压测，不是本脚本的默认用法；默认 `--concurrency 1` 是有意的。

用法：

    # 单任务端到端
    python scripts/bench/bench_plugin.py --plugin-id plg_content_ops \
        --inputs ./inputs-content.json \
        --base-url http://127.0.0.1:8000 --token "$TOKEN" \
        --concurrency 1 --requests 5 --auto-confirm --out out/plugin-e2e.json

`inputs` 文件就是这个插件的 `inputs` 字段（见 `app/plugins/manifests/<plugin_id>.json`
的 `inputs` 定义）；缺省时用内置的最小合法输入（仅 `plg_dev_workbench` 有内置样例，
其他插件必须显式给 `--inputs`，否则退出 2——**不猜输入**）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import _common as common

#: 终态：到这四种状态即停止轮询
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "aborted")

#: 成功终态
SUCCESS_STATES = ("succeeded",)

#: 需要人工接管（确认）的状态
AWAITING_STATES = ("awaiting_confirm", "waiting_confirm", "paused")

DEFAULT_POLL_INTERVAL_S = 2.0
DEFAULT_MAX_WAIT_S = 900.0

#: 内置样例输入（只给研发工作台；别的插件不猜）
SAMPLE_INPUTS: dict[str, dict[str, Any]] = {
    "plg_dev_workbench": {
        "title": "基准样例：列表页加一个复制按钮",
        "requirement": "在列表页每行加一个复制按钮，点击后复制该行编号，成功后提示。",
        "repo": "platform/admin-web",
        "branch_policy": "新建分支",
        "stack": "React 18 + TypeScript",
    },
}


def build_parser() -> argparse.ArgumentParser:
    """构造解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="bench_plugin.py",
        description=(
            "插件任务端到端基准。"
            "退出码：0 正常 / 1 有失败任务 / 2 用法 / 3 blocked 未执行 / 4 数据不足"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plugin-id", required=True, help="插件 ID（如 plg_content_ops）")
    parser.add_argument("--inputs", default="", help="插件 inputs 的 JSON 文件路径")
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="遇到人工确认点时自动继续（WF1 continue）；不开则记录为未完成",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
        help="A3 轮询间隔秒（默认 2）",
    )
    parser.add_argument(
        "--max-wait", type=float, default=DEFAULT_MAX_WAIT_S, help="单任务最长等待秒（默认 900）"
    )
    common.add_common_args(parser)
    return parser


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    """读取插件输入。

    Args:
        args: 命令行参数。

    Returns:
        dict[str, Any]: 插件 inputs。

    Raises:
        SystemExit: 缺输入（退出 2）——**不猜**。
    """
    if args.inputs:
        path = Path(args.inputs)
        if not path.is_file():
            common.fail(f"--inputs 文件不存在：{path}", common.EXIT_USAGE)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            common.fail("--inputs 必须是非空 JSON 对象（即插件的 inputs 字段）", common.EXIT_USAGE)
        return data
    sample = SAMPLE_INPUTS.get(args.plugin_id)
    if sample is None:
        common.fail(
            f"插件 `{args.plugin_id}` 没有内置样例输入，请用 --inputs 显式提供。"
            "**本脚本不猜插件输入**——猜出来的输入会让测出的耗时不代表真实任务",
            common.EXIT_USAGE,
        )
    return dict(sample)


async def _one_task(
    client: Any,
    args: argparse.Namespace,
    inputs: dict[str, Any],
    e2e: common.Measurement,
    create: common.Measurement,
    counters: dict[str, int],
) -> None:
    """创建并跟踪一次插件任务。

    Args:
        client: httpx 异步客户端。
        args: 命令行参数。
        inputs: 插件输入。
        e2e: 端到端指标。
        create: 建任务接口指标。
        counters: 计数器（轮询次数、确认次数）。
    """
    payload = {
        "conversation_id": "",
        "goal": str(inputs.get("requirement", ""))[:4000],
        "profile": "general",
        "tool_grants": [],
        "inputs": inputs,
        "options": {},
        "plugin_id": args.plugin_id,
    }
    started = time.perf_counter()
    try:
        response = await client.post(
            "/api/v1/agent/tasks", json=payload, headers={"Idempotency-Key": common.idem_key()}
        )
    except Exception as exc:
        e2e.errors.append(f"建任务异常 {type(exc).__name__}: {exc}")
        return
    create.samples.append((time.perf_counter() - started) * 1000.0)
    if response.status_code >= 400:
        e2e.errors.append(f"建任务 HTTP {response.status_code}: {response.text[:160]}")
        return
    body = response.json()
    if int(body.get("code", -1)) != 0:
        e2e.errors.append(f"建任务 code={body.get('code')} msg={body.get('message')}")
        return
    task_id = str((body.get("data") or {}).get("task_id", ""))
    if not task_id:
        e2e.errors.append("建任务未返回 task_id")
        return

    deadline = time.monotonic() + args.max_wait
    last_status = "pending"
    while time.monotonic() < deadline:
        await asyncio.sleep(args.poll_interval)
        counters["step_poll_count"] += 1
        try:
            status_response = await client.get(f"/api/v1/agent/tasks/{task_id}")
        except Exception as exc:
            e2e.errors.append(f"轮询异常 {type(exc).__name__}: {exc}")
            return
        if status_response.status_code >= 400:
            e2e.errors.append(f"轮询 HTTP {status_response.status_code}")
            return
        detail = dict(status_response.json().get("data") or {})
        last_status = str(detail.get("status", ""))
        if last_status in TERMINAL_STATES:
            break
        if last_status in AWAITING_STATES:
            if not args.auto_confirm:
                e2e.errors.append(f"任务停在人工确认点（{last_status}），未开 --auto-confirm")
                return
            counters["confirm_count"] += 1
            try:
                await client.post(
                    f"/api/v1/agent/tasks/{task_id}/retry",
                    json={"decision": "continue", "reason": "bench auto-confirm"},
                    headers={"Idempotency-Key": common.idem_key()},
                )
            except Exception as exc:
                e2e.errors.append(f"自动确认异常 {type(exc).__name__}: {exc}")
                return

    elapsed = (time.perf_counter() - started) * 1000.0
    if last_status not in SUCCESS_STATES:
        e2e.errors.append(f"任务终态为 {last_status or 'unknown'}（期望 succeeded）")
        return
    e2e.samples.append(elapsed)


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
    inputs = load_inputs(args)
    try:
        base_url, token, tenant_id = common.resolve_target(args)
    except common.BlockedError as exc:
        common.fail(f"BLOCKED: 未执行 —— {exc}（不生成任何结果文件）", common.EXIT_BLOCKED)

    e2e = common.Measurement("e2e_ms", "ms")
    create = common.Measurement("create_ms", "ms")
    counters = {"step_poll_count": 0, "confirm_count": 0}

    async def runner() -> None:
        client = common.build_client(
            base_url, token, tenant_id, args.timeout, trust_env=args.trust_env
        )
        try:
            await common.require_healthy(client)
            coros = [
                _one_task(client, args, inputs, e2e, create, counters) for _ in range(args.requests)
            ]
            await _drive(args, coros)
        finally:
            await client.aclose()

    try:
        common.run_coroutine(runner())
    except common.BlockedError as exc:
        common.fail(f"BLOCKED: 未执行 —— {exc}（不生成任何结果文件）", common.EXIT_BLOCKED)

    metrics = [e2e, create]
    extra = {
        "plugin_id": args.plugin_id,
        "auto_confirm": args.auto_confirm,
        "confirm_count": counters["confirm_count"],
        "step_poll_count": counters["step_poll_count"],
        "params_note": {
            "poll_interval_s": args.poll_interval,
            "max_wait_s": args.max_wait,
            "inputs_keys": sorted(inputs),
        },
        "disclosure_requirement": (
            "端到端耗时必须同时写明插件 ID、步骤数、是否经过人工确认、轮询间隔；"
            "本脚本默认并发 1 —— 并发 >1 时量到的是排队+执行，不是单任务执行"
        ),
    }
    payload = common.build_payload("plugin", base_url, args, metrics, args.requests, extra)
    exit_code = common.finish(args, "plugin", payload, metrics, args.requests)
    #: 插件任务的「数据不足」通常意味着全部任务失败，比退出码 4 更该看错误明细
    for metric in metrics:
        if metric.errors:
            print(f"  失败样例（{metric.name}）：{metric.errors[:3]}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
