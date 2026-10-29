#!/usr/bin/env python
"""`scripts/bench/` 三个基准脚本的公共部分（参数、统计、输出、退出码）。

设计约束（与 `docs/architecture-phase4.md` §9.6 口径一致）：

1. **只用 httpx + asyncio**，不引 locust 等新依赖（`httpx` 本就是适配层依赖，
   不扩大依赖面与 license 面）；
2. **绝不输出假数据**：没有凭据、服务不可达、样本数为 0 时一律非零退出且**不写 JSON**。
   一条 `"p95": 0.0` 的假数据比没有数据危险得多——它会被当成实测值写进对外披露文档；
3. 每个输出文件都带 `disclaimer`，提醒「本机结果不代表生产」；
4. 样本数不足 100 时 `p99` 置 `null` 并标 `p99_reliable: false`——
   50 个样本算出来的 p99 就是最大值本身，标出来比藏起来诚实。

退出码（三个脚本统一，便于 CI 与人工判断「没跑」与「跑挂了」）：

| 码 | 含义 |
| --- | --- |
| 0 | 跑完，无失败请求 |
| 1 | 跑完，但有请求失败（`error_rate > 0`） |
| 2 | 用法错误 / 缺 httpx |
| 3 | **blocked：未执行**（缺凭据、服务不可达、目标资源不存在）；不写 JSON |
| 4 | 数据不足（成功样本为 0 或低于 `--min-samples`）；不写 JSON |
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

EXIT_OK = 0
EXIT_HAD_ERRORS = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_NO_DATA = 4

SCHEMA = "moy-bench/1"

DISCLAIMER = (
    "本机结果不代表生产：仅在当前主机、当前网络、当前数据量下有效；"
    "对外披露前必须在目标环境复测，并按 docs/architecture-phase4.md §9.6 的口径"
    "（并发阶梯、含 PG、冷缓存等）逐项标注测量条件。"
)

#: p99 至少需要这么多成功样本才有统计意义（见模块 docstring 第 4 条）
P99_MIN_SAMPLES = 100


class BlockedError(RuntimeError):
    """未执行：缺凭据 / 服务不可达 / 目标资源不存在。必须以退出码 3 结束。"""


def _import_httpx() -> Any:
    """惰性导入 httpx。

    Returns:
        Any: httpx 模块。

    Raises:
        SystemExit: 缺依赖（退出码 2）。
    """
    try:
        import httpx
    except ModuleNotFoundError:  # pragma: no cover - 环境缺失路径
        print(
            "用法错误：缺少 httpx。请用 apps/adapter/.venv/Scripts/python.exe 运行，"
            "或 pip install 'httpx>=0.27,<0.29'（与适配层依赖一致）",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE) from None
    return httpx


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """给解析器加公共参数。

    Args:
        parser: 子命令或根解析器。
    """
    parser.add_argument(
        "--base-url",
        default="",
        help="适配层基址，如 http://127.0.0.1:8000（默认读 OA_BENCH_BASE_URL）",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Bearer 令牌（默认读 OA_BENCH_TOKEN）。**没有令牌即退出 3，不做好坏兜底**",
    )
    parser.add_argument(
        "--tenant-id",
        default="",
        help="租户 ID（默认读 OA_BENCH_TENANT_ID）；SaaS 模式必填",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="并发数（默认 10；§9.6 的 100/200/500 阶梯由外部多次调用实现）",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=100,
        help="总请求数（默认 100；p99 需 >=100 才标记为可信）",
    )
    parser.add_argument(
        "--out",
        default="",
        help="结果 JSON 输出路径（默认 scripts/bench/out/<scenario>-<时间戳>.json）",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="单请求超时秒数（默认 120）")
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="成功样本下限，低于即退出 4（避免用 1 个样本宣称 p95）",
    )
    parser.add_argument(
        "--label", default="", help="本次运行的标签，写进 JSON 便于对比（如 ci / local-mock）"
    )
    parser.add_argument(
        "--trust-env",
        action="store_true",
        help=(
            "信任 HTTP_PROXY/HTTPS_PROXY 等环境变量（默认**不信**）。"
            "默认不信是测量正确性要求：走了代理量到的是代理的耗时，不是服务的；"
            "目标确实需要代理时再显式打开"
        ),
    )


def resolve_target(args: argparse.Namespace) -> tuple[str, str, str]:
    """解析并校验连接目标。

    Args:
        args: 已解析参数。

    Returns:
        tuple[str, str, str]: `(base_url, token, tenant_id)`。

    Raises:
        BlockedError: 缺凭据或缺基址（**不允许任何本地兜底**）。
    """
    base_url = (args.base_url or os.environ.get("OA_BENCH_BASE_URL", "")).rstrip("/")
    token = args.token or os.environ.get("OA_BENCH_TOKEN", "")
    tenant_id = args.tenant_id or os.environ.get("OA_BENCH_TENANT_ID", "")
    if not base_url:
        raise BlockedError("缺少 --base-url（或 OA_BENCH_BASE_URL）")
    if not token:
        raise BlockedError(
            "缺少 --token（或 OA_BENCH_TOKEN）：基准测试必须打真实服务，"
            "无凭据一律判未执行，绝不生成占位数据"
        )
    if args.concurrency < 1 or args.requests < 1:
        raise BlockedError("--concurrency 与 --requests 必须 >= 1")
    return base_url, token, tenant_id


def build_client(
    base_url: str, token: str, tenant_id: str, timeout: float, trust_env: bool = False
) -> Any:
    """构造 httpx 异步客户端。

    Args:
        base_url: 基址。
        token: Bearer 令牌。
        tenant_id: 租户 ID，可空。
        timeout: 超时秒数。
        trust_env: 是否读 `HTTP_PROXY` 等环境变量。**默认 False**：
            基准测试若悄悄走了代理，量到的就是代理的耗时而不是被测服务的，
            这种数据比没有数据更糟（会被当成实测值披露）。需要代理时用
            `--trust-env` 显式打开并在报告里注明。

    Returns:
        Any: `httpx.AsyncClient`。
    """
    httpx = _import_httpx()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return httpx.AsyncClient(
        base_url=base_url, headers=headers, timeout=timeout, trust_env=trust_env
    )


async def require_healthy(client: Any) -> None:
    """探活。

    Args:
        client: httpx 异步客户端。

    Raises:
        BlockedError: 服务不可达。
    """
    httpx = _import_httpx()
    try:
        response = await client.get("/healthz", timeout=10.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPError) as exc:
        raise BlockedError(
            f"服务不可达：{client.base_url}/healthz（{type(exc).__name__}: {exc}）"
        ) from exc
    if response.status_code >= 400:
        raise BlockedError(f"/healthz 返回 HTTP {response.status_code}")


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def percentile(samples: list[float], pct: float) -> float:
    """线性插值分位数（与 numpy 默认口径一致，便于交叉核对）。

    Args:
        samples: 样本（升序或乱序皆可，函数内部排序）。
        pct: 百分位，取值 0-100。

    Returns:
        float: 分位数值；样本为空时返回 0.0。

    Raises:
        ValueError: `pct` 越界。
    """
    if not 0 <= pct <= 100:
        raise ValueError(f"pct 必须在 0-100，实际 {pct}")
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def summarise(samples: list[float]) -> dict[str, Any]:
    """计算 P50/P95/P99 等统计量。

    Args:
        samples: 成功样本（毫秒）。

    Returns:
        dict[str, Any]: 统计结果；样本不足 `P99_MIN_SAMPLES` 时 `p99=None`。
    """
    if not samples:
        return {
            "count": 0,
            "p50": None,
            "p95": None,
            "p99": None,
            "p99_reliable": False,
            "min": None,
            "max": None,
            "mean": None,
        }
    reliable = len(samples) >= P99_MIN_SAMPLES
    return {
        "count": len(samples),
        "p50": round(percentile(samples, 50), 2),
        "p95": round(percentile(samples, 95), 2),
        "p99": round(percentile(samples, 99), 2) if reliable else None,
        "p99_reliable": reliable,
        "min": round(min(samples), 2),
        "max": round(max(samples), 2),
        "mean": round(sum(samples) / len(samples), 2),
    }


@dataclass
class Measurement:
    """一个指标的测量结果。"""

    name: str
    unit: str
    samples: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    threshold_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """导出为 JSON 友好结构。

        Returns:
            dict[str, Any]: 含 stats、threshold 判定与**原始样本**。

        `raw_samples_ms` 是硬要求（`docs/perf-disclosure.md` §3.2）：
        **客户必须能拿着原始数据自己算出同样的 P95**，而不是只接受一个结论。
        只给汇总数字的产物无法被复算，按文档纪律属于不可引用。
        """
        stats = summarise(self.samples)
        verdict = "not_evaluated"
        if self.threshold_ms is not None and stats["p95"] is not None:
            verdict = "pass" if stats["p95"] <= self.threshold_ms else "fail"
        return {
            "name": self.name,
            "unit": self.unit,
            "stats": stats,
            "threshold_ms": self.threshold_ms,
            "verdict": verdict,
            "error_count": len(self.errors),
            "error_samples": self.errors[:5],
            "raw_samples_ms": [round(value, 3) for value in self.samples],
        }


def error_rate(success: int, total: int) -> float:
    """计算错误率。

    Args:
        success: 成功数。
        total: 总数。

    Returns:
        float: 0.0-1.0。
    """
    if total <= 0:
        return 0.0
    return round((total - success) / total, 4)


def default_out_path(scenario: str) -> Path:
    """默认输出路径（`scripts/bench/out/<scenario>-<时间戳>.json`）。

    Args:
        scenario: 场景名。

    Returns:
        Path: 输出路径。
    """
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return Path(__file__).resolve().parent / "out" / f"{scenario}-{stamp}.json"


def run_coroutine(coro: Any) -> Any:
    """在同步入口里跑协程。

    Args:
        coro: 协程对象。

    Returns:
        Any: 协程返回值。
    """
    return asyncio.run(coro)


def build_payload(
    scenario: str,
    client_target: str,
    args: argparse.Namespace,
    metrics: list[Measurement],
    total: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装输出 JSON。

    Args:
        scenario: 场景名。
        client_target: 目标基址。
        args: 命令行参数。
        metrics: 指标列表。
        total: 总请求数。
        extra: 场景专属字段。

    Returns:
        dict[str, Any]: 可直接落盘的字典。
    """
    success = max((len(m.samples) for m in metrics), default=0)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "scenario": scenario,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "label": args.label,
        "target": {"base_url": client_target},
        "params": {
            "concurrency": args.concurrency,
            "requests": args.requests,
            "timeout_s": args.timeout,
            "min_samples": args.min_samples,
        },
        "samples": {
            "total": total,
            "success": success,
            "error_rate": error_rate(success, total),
        },
        "metrics": {m.name: m.to_dict() for m in metrics},
        "disclaimer": DISCLAIMER,
        "recompute_hint": (
            "分位数可用 raw_samples_ms 复算：线性插值，rank=(n-1)*p/100，"
            "在相邻两点间按小数部分加权（与 numpy.percentile 默认口径一致）。"
            "口径定义见 docs/perf-disclosure.md §2.1"
        ),
    }
    if extra:
        payload.update(extra)
    return payload


def write_output(path: Path, payload: dict[str, Any]) -> Path:
    """原子写输出文件。

    Args:
        path: 目标路径。
        payload: 内容。

    Returns:
        Path: 实际写入路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def finish(
    args: argparse.Namespace,
    scenario: str,
    payload: dict[str, Any],
    metrics: list[Measurement],
    total: int,
) -> int:
    """统一收尾：判数据是否足够、写盘、打印摘要、返回退出码。

    Args:
        args: 命令行参数。
        scenario: 场景名。
        payload: 输出内容。
        metrics: 指标列表。
        total: 总请求数。

    Returns:
        int: 退出码。
    """
    success = max((len(m.samples) for m in metrics), default=0)
    if success == 0:
        print(
            f"数据不足：0 个成功样本（共发起 {total} 次）。不写 {args.out or 'JSON'}——"
            "没有数据就不产出文件，避免被当成实测值",
            file=sys.stderr,
        )
        return EXIT_NO_DATA
    if success < args.min_samples:
        print(
            f"数据不足：成功样本 {success} < --min-samples {args.min_samples}，不写输出",
            file=sys.stderr,
        )
        return EXIT_NO_DATA

    path = Path(args.out) if args.out else default_out_path(scenario)
    write_output(path, payload)
    print(f"输出：{path}")
    for metric in metrics:
        item = payload["metrics"][metric.name]
        stats = item["stats"]
        p99 = f"{stats['p99']}ms" if stats["p99"] is not None else "null(样本<100，不报 p99)"
        print(
            f"  {metric.name}: n={stats['count']} "
            f"p50={stats['p50']}ms p95={stats['p95']}ms p99={p99} "
            f"阈值={item['threshold_ms']} 判定={item['verdict']}"
        )
    if payload["samples"]["error_rate"] > 0:
        print(f"有失败请求：错误率 {payload['samples']['error_rate']}", file=sys.stderr)
        return EXIT_HAD_ERRORS
    return EXIT_OK


def fail(message: str, code: int) -> NoReturn:
    """打印错误并以指定码退出。

    Args:
        message: 错误信息。
        code: 退出码。
    """
    print(message, file=sys.stderr)
    raise SystemExit(code)


def idem_key() -> str:
    """生成幂等键（写接口必需）。

    Returns:
        str: `bench-<uuid4 hex>`。
    """
    return f"bench-{uuid.uuid4().hex}"


def disclosure_block(upstream: str) -> dict[str, Any]:
    """披露标签校验（`docs/perf-disclosure.md` §3.1「披露标签校验」的统一实现）。

    Args:
        upstream: `mock` / `real` / `unspecified`。

    Returns:
        dict[str, Any]: `{"upstream", "disclosable", "blockers", "requirement"}`。

    `unspecified` 一律 `disclosable=False`：§3.1 明确「填 unspecified（未标注）的结果
    不允许直接对外披露」。把这条做进产物文件，而不是靠写报告的人记得——
    否则总会有人把一份没标上游的 JSON 抄进对客文档。
    """
    blockers: list[str] = []
    if upstream == "unspecified":
        blockers.append("未标注上游类型（--upstream），结果不可对外披露")
    return {
        "upstream": upstream,
        "disclosable": not blockers,
        "blockers": blockers,
        "requirement": (
            "mock 组必须逐字标注「不含上游模型延迟」；"
            "real 组必须写明模型档位与网络环境；"
            "另需按 §3.3 记录硬件/软件/编排/数据库/网络五项"
        ),
    }
