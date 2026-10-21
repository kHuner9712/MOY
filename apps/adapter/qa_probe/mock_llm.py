"""Mock LLM 服务（OpenAI 兼容）—— 适配层-底座真实联调用。

用途（QA 报告 R-03 要求的「Sprint 1 引入 Dify mock 服务做契约测试」）：
- 私有化联调环境没有外部模型 API 密钥，本服务提供 OpenAI 兼容的
  /v1/models 与 /v1/chat/completions（流式 + 非流式），
  使 Dify → 模型提供方 → 适配层 的全链路可以在纯本地闭环验证；
- 响应内容确定性（含固定 token 用量），便于断言。

运行：uvicorn mock_llm:app --host 0.0.0.0 --port 8090
Dify 容器经 host.docker.internal:8090 访问本服务。
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock LLM (OpenAI compatible)")

MODEL_ID = "mock-qwen-plus"
API_KEY = "mock-key-not-checked"


def _chat_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _completion(messages: list[dict]) -> str:
    """根据最后一条用户消息生成确定性回复。"""
    last = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    content = (last or {}).get("content") or ""
    if isinstance(content, list):  # 多模态消息取文本部分
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return f"[MOCK-LLM] 已收到你的消息：「{str(content)[:80]}」。这是本地模拟模型的确定性回复。"


def _usage(prompt_text: str, completion_text: str) -> dict:
    return {
        "prompt_tokens": max(1, len(prompt_text) // 4),
        "completion_tokens": max(1, len(completion_text) // 4),
        "total_tokens": max(2, (len(prompt_text) + len(completion_text)) // 4),
    }


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "mock",
                }
            ],
        }
    )


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    messages = body.get("messages", [])
    stream = bool(body.get("stream"))
    answer = _completion(messages)
    prompt_text = " ".join(str(m.get("content", "")) for m in messages)
    usage = _usage(prompt_text, answer)
    model = body.get("model") or MODEL_ID

    if not stream:
        return JSONResponse(
            {
                "id": _chat_id(),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
        )

    # 流式：按词切分，逐段推送 SSE
    async def _gen():
        chunks = [answer[i : i + 12] for i in range(0, len(answer), 12)] or [answer]
        for chunk in chunks:
            payload = {
                "id": _chat_id(),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            time.sleep(0.02)
        final = {
            "id": _chat_id(),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
