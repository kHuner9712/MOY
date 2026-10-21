"""适配层-底座真实联调验证（Phase 1 验收用）。

前置：deploy 栈全部 Up（dify-api/plugin_daemon/milvus 等）、adapter 容器以
OA_DOWNSTREAM_ENABLED=true 运行、Dify 已发布一个含 Answer 节点的工作流应用。

验证点：
1. A1 创建任务（经适配层 → Dify /v1/workflows/run 真实执行）
2. A3 轮询任务状态直至终态（succeeded 为通过）
3. U1 身份校验（stub 模式 JWT）
4. M3 模型目录（本地档位注册表）
5. 幂等重放（同一 Idempotency-Key 二次提交返回 IDEMPOTENT_DUPLICATE）

运行：python integration_real.py [--adapter http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid

import json

import httpx

ADAPTER = os.environ.get("OA_ADAPTER_URL", "http://127.0.0.1:8000")
JWT_SECRET = os.environ.get("OA_JWT_SECRET", "dev-jwt-secret-for-local-integration-testing-only-32b")
APP_KEY = os.environ.get("OA_DIFY_DEFAULT_APP_KEY", "")

PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool, str]] = []


def _token() -> str:
    from jose import jwt

    now = int(time.time())
    return jwt.encode(
        {
            "sub": "usr_integration_test",
            "tenant_id": "default",
            "roles": ["*"],
            "aud": "officeai-adapter",
            "iss": "officeai",
            "iat": now,
            "exp": now + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{PASS if ok else FAIL} {name}: {detail}")


async def main() -> int:
    token = _token()
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    async with httpx.AsyncClient(base_url=ADAPTER, timeout=60.0, trust_env=False) as client:
        # 0. 健康检查（确认 downstream 真实模式）
        r = await client.get("/healthz")
        downstream = r.json().get("data", {}).get("downstream_enabled")
        _record("healthz", r.status_code == 200 and downstream is True, f"downstream_enabled={downstream}")

        # 1. U1 身份校验（stub 模式）
        r = await client.post(
            "/api/v1/identity/verify",
            headers={**headers, "Idempotency-Key": f"idem_{uuid.uuid4().hex[:24]}"},
            json={"permission": "knowledge:document:upload"},
        )
        _record("U1 身份校验", r.status_code == 200 and r.json().get("code") == 0, r.text[:120])

        # 2. M3 模型目录
        r = await client.get("/api/v1/model/catalog", headers=headers)
        _record("M3 模型目录", r.status_code == 200, r.text[:120])

        conv_id = f"conv_{uuid.uuid4().hex[:26]}"

        # 2.5 M1 非流式生成（真实 DeepSeek 模型，经 Dify chat 应用）
        r = await client.post(
            "/api/v1/model/generate",
            headers={**headers, "Idempotency-Key": f"idem_{uuid.uuid4().hex[:24]}"},
            json={
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "你好，请用一句话介绍你自己"}],
                "profile": "general",
                "context": {"history_window": 10, "memory_hints": []},
                "knowledge_base_ids": [],
            },
        )
        body = r.json()
        content = str((body.get("data") or {}).get("content", ""))
        usage = (body.get("data") or {}).get("usage") or {}
        _record(
            "M1 非流式生成（DeepSeek 真实模型）",
            r.status_code == 200 and body.get("code") == 0 and bool(content),
            f"content={content[:40]!r} total_units={usage.get('total_units')}",
        )

        # 2.6 M2 流式生成（SSE 首帧 + 增量帧 + done 帧）
        async with client.stream(
            "POST",
            "/api/v1/model/generate/stream",
            headers=headers,
            json={
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "用一个词回答：天空是什么颜色"}],
                "profile": "general",
                "context": {"history_window": 10, "memory_hints": []},
                "knowledge_base_ids": [],
            },
        ) as resp:
            # SSE 规范：event: <类型> 与 data: <JSON> 成对出现，
            # 事件类型在 event 行（早期版本误从 data JSON 里取，恒为空）
            kinds: list[str] = []
            deltas: list[str] = []
            current_event = ""
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    current_event = line[len("event: "):].strip()
                    kinds.append(current_event)
                elif line.startswith("data: ") and current_event == "delta":
                    try:
                        deltas.append(str(json.loads(line[len("data: "):]).get("text", "")))
                    except Exception:  # noqa: S110 - 单帧解析失败不影响整体流式断言
                        pass
        answer_text = "".join(deltas)
        _record(
            "M2 流式生成（SSE）",
            resp.status_code == 200
            and "start" in kinds
            and "delta" in kinds
            and "done" in kinds
            and bool(answer_text),
            f"events={kinds[:6]} answer={answer_text[:30]!r}",
        )

        # 3. A1 创建任务（真实 Dify 工作流执行）
        idem_key = f"idem_{uuid.uuid4().hex[:24]}"
        r = await client.post(
            "/api/v1/agent/tasks",
            headers={**headers, "Idempotency-Key": idem_key},
            json={
                "conversation_id": conv_id,
                "goal": "验证适配层到 Dify 工作流的真实联调链路",
                "profile": "general",
                "tool_grants": [],
                "inputs": {},
            },
        )
        body = r.json()
        task_id = (body.get("data") or {}).get("task_id", "")
        _record("A1 创建任务", r.status_code == 200 and body.get("code") == 0 and bool(task_id), f"task_id={task_id} body={r.text[:160]}")

        # 4. A3 轮询到终态
        final = ""
        for _ in range(30):
            await asyncio.sleep(1)
            r = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
            data = (r.json().get("data") or {})
            final = str(data.get("status", ""))
            if final in {"completed", "failed", "cancelled"}:
                break
        _record("A3 任务终态", final == "completed", f"status={final}")

        # 5. 幂等重放
        r = await client.post(
            "/api/v1/agent/tasks",
            headers={**headers, "Idempotency-Key": idem_key},
            json={
                "conversation_id": conv_id,
                "goal": "验证适配层到 Dify 工作流的真实联调链路",
                "profile": "general",
                "tool_grants": [],
                "inputs": {},
            },
        )
        _record("幂等重放", r.json().get("code") == 20007, r.text[:120])

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== 汇总：{len(results) - len(failed)}/{len(results)} 通过 =====")
    if failed:
        print("未通过：", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default=ADAPTER)
    args = parser.parse_args()
    ADAPTER = args.adapter
    sys.exit(asyncio.run(main()))
