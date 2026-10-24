#!/usr/bin/env python
"""轻量版最小容器集探针（PRD v1.1 P4-35 的测量工具）。

三个动作：

    python probe.py cold            # 冷启动：计时 + 采样，直到全部就绪
    python probe.py idle --sec 60   # 空载：只采样
    python probe.py chain --n 3     # 完整链路：连打 M1，同时采样

测量口径（写进报告 §2 时必须照抄）：
- 内存主口径 = `docker stats --no-stream` 的 MemUsage（Docker 官方工作集口径，
  与客户运维看到的一致）；单次调用实测耗时 ~2.9s，故**有效采样间隔 ≈3s**。
- 上界校验 = cgroup v2 `memory.current`（含 page cache），每阶段末取一次。
- 就绪时间：有 healthcheck 的服务取容器健康状态翻转时刻；keycloak 走宿主机 HTTP；
  plugin_daemon / dify-worker 无就绪语义，只记 Running 时刻。
- 只统计 `oalite-` 前缀容器（与生产栈 oa-* 完全隔离，互不污染）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "oalite"
OUT = Path(__file__).resolve().parent / "out"

#: 服务 → 就绪判定方式
PROBES: dict[str, str] = {
    "postgres": "health",
    "valkey": "health",
    "dify-api": "health",
    "adapter": "health",
    "web": "health",
    "proxy": "health",
    "keycloak": "http:http://127.0.0.1:65081/health/ready",
    "plugin_daemon": "running",
    "dify-worker": "running",
}

_MEM = re.compile(r"([\d.]+)\s*([KMGT]?i?B)")


def merged_env() -> Path:
    """合并出探针专用 env（deploy/.env + 缺失的内部密钥）。

    `deploy/.env` 里没有 `OA_PLUGIN_DAEMON_KEY` / `OA_PLUGIN_INNER_API_KEY`
    （标准栈此前用 compose 里的明文兜底值启动，审计后已从 compose 移除）。
    探针需要一对**自洽**的内部密钥，故本地生成，只写入 `out/probe.env`，
    不落到任何被跟踪的文件里。
    """
    import secrets

    src = (Path(__file__).resolve().parents[3] / "deploy" / ".env").read_text(encoding="utf-8")
    have = {line.split("=", 1)[0].strip() for line in src.splitlines() if "=" in line and not line.startswith("#")}
    extra = []
    for key, value in {
        "OA_PLUGIN_DAEMON_KEY": secrets.token_urlsafe(32),
        "OA_PLUGIN_INNER_API_KEY": secrets.token_urlsafe(32),
        "OA_DIFY_SANDBOX_KEY": secrets.token_urlsafe(16),  # 探针不用 sandbox，仅为插值必填
    }.items():
        if key not in have:
            extra.append(f"{key}={value}")
    OUT.mkdir(exist_ok=True)
    target = OUT / "probe.env"
    target.write_text(src.rstrip() + "\n" + "\n".join(extra) + "\n", encoding="utf-8")
    return target


def sh(*args: str, check: bool = False) -> str:
    """执行命令并返回 stdout。"""
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args} -> {r.returncode}: {r.stderr[:400]}")
    return r.stdout


def to_mib(text: str) -> float:
    """把 `123.4MiB` 归一化为 MiB。"""
    m = _MEM.search(text or "")
    if not m:
        return 0.0
    val, unit = float(m.group(1)), m.group(2).upper()
    factor = {"B": 1 / 1048576, "KB": 1 / 1024, "KIB": 1 / 1024,
              "MB": 1.0, "MIB": 1.0, "GB": 1024.0, "GIB": 1024.0}[unit]
    return round(val * factor, 1)


def containers() -> list[str]:
    """列出探针项目的容器名。"""
    out = sh("docker", "ps", "-a", "--filter", f"name=^{PROJECT}-", "--format", "{{.Names}}")
    return [n for n in out.split() if n]


def sample() -> dict[str, float]:
    """采样一次内存（MiB，按容器）。"""
    names = set(containers())
    out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")
    got: dict[str, float] = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, usage = line.split("\t", 1)
        if name in names:
            got[name] = to_mib(usage.split("/")[0])
    return got


def cgroup_snapshot() -> dict[str, float]:
    """cgroup v2 memory.current 上界快照（MiB）。"""
    got: dict[str, float] = {}
    for name in containers():
        out = sh("docker", "exec", name, "sh", "-c", "cat /sys/fs/cgroup/memory.current 2>/dev/null")
        if out.strip().isdigit():
            got[name] = round(int(out.strip()) / 1048576, 1)
    return got


def ready(name: str) -> bool:
    """单个服务是否就绪。"""
    svc = name[len(PROJECT) + 1:].rsplit("-", 1)[0]
    how = PROBES.get(svc, "running")
    if how == "health":
        out = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name).strip()
        return out == "healthy"
    if how.startswith("http:"):
        try:
            with urllib.request.urlopen(how[5:], timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False
    out = sh("docker", "inspect", "-f", "{{.State.Status}}", name).strip()
    return out == "running"


def started_at(name: str) -> str:
    return sh("docker", "inspect", "-f", "{{.State.StartedAt}}", name).strip()


def cold(up_cmd: list[str]) -> int:
    """冷启动测量：从 `up -d` 到全部就绪。"""
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    # 非阻塞起栈：`up -d` 在 depends_on: service_healthy 下会阻塞到依赖就绪，
    # 若同步等待就拿不到"每个服务各自何时就绪"的真实时序。故后台起、并行采样。
    proc = subprocess.Popen(up_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    up_elapsed = 0.0

    samples: list[tuple[float, dict[str, float]]] = []
    ready_at: dict[str, float] = {}
    deadline = t0 + 480
    while time.time() < deadline:
        s = sample()
        samples.append((round(time.time() - t0, 1), s))
        for name in containers():
            if name not in ready_at and ready(name):
                ready_at[name] = round(time.time() - t0, 1)
        svc_ready = {n[len(PROJECT) + 1:].rsplit("-", 1)[0] for n in ready_at}
        if set(PROBES) <= svc_ready:
            break
        print(f"  t={round(time.time()-t0,1)}s ready={len(svc_ready)}/{len(PROBES)} "
              f"missing={sorted(set(PROBES)-svc_ready)}", flush=True)

    up_elapsed = round(time.time() - t0, 1) if proc.poll() is None else up_elapsed
    if proc.poll() is None:
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            print("  [warn] up 进程未在 120s 内返回，继续按采样结果记录")
    elif proc.returncode != 0:
        print("up 失败：", (proc.stdout.read() if proc.stdout else "")[-1500:])
    total = round(time.time() - t0, 1)
    cg = cgroup_snapshot()
    peaks: dict[str, float] = {}
    for _, s in samples:
        for k, v in s.items():
            peaks[k] = max(peaks.get(k, 0.0), v)

    (OUT / "cold.json").write_text(json.dumps({
        "up_cmd_elapsed_s": round(up_elapsed, 1),
        "all_ready_s": total,
        "sample_count": len(samples),
        "sample_interval_s": round(total / max(1, len(samples)), 1),
        "ready_at_s": dict(sorted(ready_at.items(), key=lambda kv: kv[1])),
        "peak_mib_dockerstats": dict(sorted(peaks.items(), key=lambda kv: -kv[1])),
        "peak_mib_cgroup": dict(sorted(cg.items(), key=lambda kv: -kv[1])),
        "sum_peak_mib": round(sum(peaks.values()), 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(json.loads((OUT / "cold.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


def idle(sec: int) -> int:
    """空载采样。"""
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    samples: list[tuple[float, dict[str, float]]] = []
    while time.time() - t0 < sec:
        samples.append((round(time.time() - t0, 1), sample()))
    peaks: dict[str, float] = {}
    for _, s in samples:
        for k, v in s.items():
            peaks[k] = max(peaks.get(k, 0.0), v)
    cg = cgroup_snapshot()
    (OUT / "idle.json").write_text(json.dumps({
        "window_s": sec,
        "sample_count": len(samples),
        "peak_mib_dockerstats": dict(sorted(peaks.items(), key=lambda kv: -kv[1])),
        "peak_mib_cgroup": dict(sorted(cg.items(), key=lambda kv: -kv[1])),
        "sum_peak_mib": round(sum(peaks.values()), 1),
        "sum_cgroup_mib": round(sum(cg.values()), 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(json.loads((OUT / "idle.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0


def token() -> str:
    """按生产口径（HS256 + aud/iss）现签一枚令牌。"""
    import base64
    import hashlib
    import hmac

    env = (Path(__file__).resolve().parents[3] / "deploy" / ".env").read_text(encoding="utf-8")
    secret = ""
    for line in env.splitlines():
        if line.startswith("OA_JWT_SECRET="):
            secret = line.split("=", 1)[1].strip()
    now = int(time.time())

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=").decode()

    head = b64({"alg": "HS256", "typ": "JWT"})
    body = b64({"sub": "usr_liteprobe0001", "tenant_id": "default", "roles": ["owner"],
                "aud": "officeai-adapter", "iss": "officeai", "exp": now + 3600, "iat": now})
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{head}.{body}.{sig}"


def chain(n: int) -> int:
    """完整链路：M1 生成 n 次，同时采样内存峰值。"""
    OUT.mkdir(exist_ok=True)
    tok = token()
    results = []
    samples: list[tuple[float, dict[str, float]]] = []
    t0 = time.time()
    for i in range(n):
        body = json.dumps({
            "conversation_id": f"conv_liteprobe{i}",
            "profile": "general",
            "messages": [{"role": "user", "content": "用两句话说明周报应该包含哪些要素"}],
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:65000/api/v1/model/generate", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}",
                     "Idempotency-Key": f"22222222-3333-4444-8555-{i:012d}"},
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read())
                results.append({"i": i, "code": payload.get("code"),
                                "latency_s": round(time.time() - started, 2),
                                "units": (payload.get("data") or {}).get("usage", {}).get("total_units")})
        except urllib.error.HTTPError as exc:
            results.append({"i": i, "code": f"HTTP {exc.code}", "body": exc.read()[:200].decode(errors="replace")})
        except Exception as exc:  # noqa: BLE001
            results.append({"i": i, "code": type(exc).__name__, "detail": str(exc)[:200]})
        samples.append((round(time.time() - t0, 1), sample()))
    # 链路后静置一轮再取峰值
    time.sleep(3)
    samples.append((round(time.time() - t0, 1), sample()))
    cg = cgroup_snapshot()

    peaks: dict[str, float] = {}
    for _, s in samples:
        for k, v in s.items():
            peaks[k] = max(peaks.get(k, 0.0), v)
    (OUT / "chain.json").write_text(json.dumps({
        "requests": results,
        "success": sum(1 for r in results if r.get("code") == 0),
        "sample_count": len(samples),
        "peak_mib_dockerstats": dict(sorted(peaks.items(), key=lambda kv: -kv[1])),
        "peak_mib_cgroup": dict(sorted(cg.items(), key=lambda kv: -kv[1])),
        "sum_peak_mib": round(sum(peaks.values()), 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(json.loads((OUT / "chain.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0 if all(r.get("code") == 0 for r in results) else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["cold", "idle", "chain"])
    ap.add_argument("--sec", type=int, default=60)
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args(argv)
    if args.action == "cold":
        env_file = merged_env()
        up = ["docker", "compose", "--env-file", str(env_file), "-p", PROJECT,
              "-f", "docker-compose.probe.yml", "up", "-d"]
        return cold(up)
    if args.action == "idle":
        return idle(args.sec)
    return chain(args.n)


if __name__ == "__main__":
    sys.exit(main())
