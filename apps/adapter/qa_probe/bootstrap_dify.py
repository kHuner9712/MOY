"""Dify 底座引导脚本（私有化/SaaS 部署后初始化）。

把 2026-10-21 实跑验证过的完整引导流程固化为可重复执行脚本：
1. 等待底座就绪（/health）
2. 初始化管理员（setup，已初始化则跳过）
3. 登录拿 console 令牌
4. 创建聊天应用与工作流应用（含 API 密钥）
5. 安装模型提供方插件（deepseek，本地 .difypkg 上传 + 安装）
6. 配置模型凭据并绑定到聊天应用

设计原则（与适配层红线一致）：
- 本脚本是**部署运维工具**，不属于产品运行时；仅部署人员执行一次；
- 模型 API 密钥经环境变量注入，不落盘到任何提交文件；
-  marketplace 不可达时（DNS 污染等），使用 --pkg 指定本地 .difypkg 离线安装。

用法：
    python bootstrap_dify.py --base-url http://dify-api:5001 \\
        --email admin@example.com --password '***' \\
        --deepseek-key sk-xxx [--pkg /path/deepseek.difypkg]

容器内执行（无 curl 的 dify-api 镜像不适用，应在同网络辅助容器或宿主机执行）：
    docker run --rm --network <net> -v $(pwd):/probe python:3.12-slim \\
        python /probe/bootstrap_dify.py --base-url http://dify-api:5001 ...
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool, str]] = []

#: 1.14+ 控制台认证改为 Cookie 会话（响应体不再返回 access_token），
#: 统一用带 cookie 罐的 opener 维持登录态
_COOKIE_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIE_JAR))


def _record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{PASS if ok else FAIL} {name}: {detail}")


def _req(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
    token: str = "",
    timeout: int = 60,
    raw: bytes | None = None,
    multipart: tuple[str, bytes, str] | None = None,
) -> tuple[int, dict | bytes]:
    """发起 HTTP 请求，返回 (状态码, 解析后 body)。"""
    url = f"{base.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # 1.17 起控制台写接口校验 CSRF：请求头须与 csrf_token Cookie 一致
    if method.upper() != "GET":
        for cookie in _COOKIE_JAR:
            if cookie.name.endswith("csrf_token"):
                headers["X-CSRF-Token"] = cookie.value
                break
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()
    elif raw is not None:
        data = raw
    elif multipart is not None:
        field, content, ctype = multipart
        boundary = "----oa-bootstrap-boundary"
        data = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{field}.difypkg\"\r\nContent-Type: {ctype}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            payload = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        status = exc.code
    except urllib.error.URLError as exc:
        return 0, str(exc).encode()
    try:
        return status, json.loads(payload)
    except Exception:
        return status, payload


def _wait_ready(base: str, attempts: int = 60) -> bool:
    for _ in range(attempts):
        status, _body = _req(base, "GET", "/health", timeout=10)
        if status == 200:
            return True
        time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Dify 底座引导（部署后初始化）")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--admin-name", default="管理员")
    parser.add_argument("--deepseek-key", default="", help="DeepSeek API 密钥（经环境注入）")
    parser.add_argument("--pkg", default="", help="deepseek 插件 .difypkg 本地路径（市场不可达时）")
    parser.add_argument("--identifier", default="", help="插件唯一标识符（缺省从市场批量接口获取）")
    args = parser.parse_args()

    base = args.base_url

    # 1. 就绪等待
    _record("底座就绪", _wait_ready(base), f"{base}/health")

    # 2. 初始化管理员（已初始化时返回 400，视为已通过）
    status, _ = _req(
        base, "POST", "/console/api/setup",
        body={"email": args.email, "name": args.admin_name, "password": args.password},
    )
    # 201=新建；400/403=已初始化（1.17 对重复初始化返回 403），均视为通过
    ok = status in (201, 400, 403)
    _record("管理员初始化", ok, f"HTTP {status}（400/403=已初始化，跳过）")

    # 3. 登录（1.14+：密码需 Base64 传输；认证态走 Cookie 会话）
    pwd_b64 = base64.b64encode(args.password.encode()).decode()
    status, body = _req(
        base, "POST", "/console/api/login",
        body={"email": args.email, "password": pwd_b64},
    )
    logged_in = status == 200 and isinstance(body, dict) and body.get("result") == "success"
    cookie_names = [c.name for c in _COOKIE_JAR]
    _record("控制台登录", logged_in and bool(cookie_names), f"HTTP {status} cookies={cookie_names}")
    if not logged_in:
        return 1
    token = ""  # Cookie 会话下不再使用 Bearer

    # 4. 聊天应用 + 密钥
    status, body = _req(
        base, "POST", "/console/api/apps", token=token,
        body={"name": "麦子对话助手", "mode": "chat", "icon_type": "emoji",
              "icon": "🌾", "icon_background": "#DDF1E6"},
    )
    chat_app = str((body or {}).get("id", "")) if isinstance(body, dict) else ""
    _record("创建聊天应用", bool(chat_app), chat_app)
    status, body = _req(
        base, "POST", f"/console/api/apps/{chat_app}/api-keys", token=token, body={},
    )
    chat_key = str((body or {}).get("token", "")) if isinstance(body, dict) else ""
    _record("聊天应用密钥", bool(chat_key), chat_key)

    # 5. 工作流应用（Answer 节点）+ 密钥 + 发布
    status, body = _req(
        base, "POST", "/console/api/apps", token=token,
        body={"name": "麦子任务工作流", "mode": "workflow", "icon_type": "emoji",
              "icon": "⚙️", "icon_background": "#DDF1E6"},
    )
    wf_app = str((body or {}).get("id", "")) if isinstance(body, dict) else ""
    _record("创建工作流应用", bool(wf_app), wf_app)
    graph = {
        "graph": {
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 80, "y": 180},
                 "data": {"title": "开始", "type": "start", "variables": []}},
                {"id": "answer", "type": "answer", "position": {"x": 400, "y": 180},
                 "data": {"title": "直接回复", "type": "answer",
                          "answer": "任务已完成（底座联调默认回复）。"}},
                {"id": "end", "type": "end", "position": {"x": 720, "y": 180},
                 "data": {"title": "结束", "type": "end", "outputs": []}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "answer",
                 "sourceHandle": "source", "targetHandle": "target"},
                {"id": "e2", "source": "answer", "target": "end",
                 "sourceHandle": "source", "targetHandle": "target"},
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        "features": {},
        # 1.17 起 environment_variables 已迁至独立 API，草稿载荷禁止携带
    }
    status, _ = _req(base, "POST", f"/console/api/apps/{wf_app}/workflows/draft", token=token, body=graph)
    _record("工作流草稿", status == 200, f"HTTP {status}")
    status, _ = _req(base, "POST", f"/console/api/apps/{wf_app}/workflows/publish", token=token, body={})
    _record("工作流发布", status == 200, f"HTTP {status}")
    status, body = _req(base, "POST", f"/console/api/apps/{wf_app}/api-keys", token=token, body={})
    wf_key = str((body or {}).get("token", "")) if isinstance(body, dict) else ""
    _record("工作流应用密钥", bool(wf_key), wf_key)

    # 6. 模型提供方插件（deepseek）
    identifier = args.identifier
    if args.pkg and not identifier:
        # 本地包安装：先上传拿标识符，再安装
        with open(args.pkg, "rb") as fh:
            content = fh.read()
        status, body = _req(
            base, "POST", "/console/api/workspaces/current/plugin/upload/pkg",
            token=token, multipart=("pkg", content, "application/octet-stream"), timeout=120,
        )
        identifier = str((body or {}).get("unique_identifier", "")) if isinstance(body, dict) else ""
        _record("插件包上传", bool(identifier), identifier)
    if not identifier:
        # 市场批量接口取最新标识符
        status, body = _req(
            base, "POST", "/console/api/workspaces/current/plugin/../../marketplace-stub",
            body={},
        ) if False else (0, {})
        # 市场接口在底座容器外调用（宿主经代理 + --resolve），此处仅提示
        print("⚠️ 未提供 --pkg 且市场不可直连时，请先下载 .difypkg 再传 --pkg")
        print("   下载：curl --resolve marketplace.dify.ai:443:<IP> "
              "\"https://marketplace.dify.ai/api/v1/plugins/download?unique_identifier=<ID>\"")
    if identifier:
        status, _ = _req(
            base, "POST", "/console/api/workspaces/current/plugin/install/marketplace",
            token=token, body={"plugin_unique_identifiers": [identifier]}, timeout=120,
        )
        _record("插件安装", status == 200, f"HTTP {status}")

        # 7. 配置模型凭据（deepseek；1.17 起 POST 到 /credentials 子路径）
        if args.deepseek_key:
            status, _ = _req(
                base, "POST",
                "/console/api/workspaces/current/model-providers/langgenius/deepseek/deepseek/credentials",
                token=token, body={"credentials": {"api_key": args.deepseek_key}},
            )
            _record("模型凭据配置", status in (200, 201), f"HTTP {status}")

            # 8. 绑定模型到聊天应用
            status, _ = _req(
                base, "POST", f"/console/api/apps/{chat_app}/model-config", token=token,
                body={
                    "model": {"provider": "langgenius/deepseek/deepseek",
                              "name": "deepseek-v4-flash", "mode": "chat",
                              "completion_params": {"temperature": 0.7}},
                    "user_input_form": [],
                    "pre_prompt": "你是麦子，用户的办公伙伴。回答简洁友好。",
                    "opening_statement": "", "suggested_questions": [],
                    "suggested_questions_after_answer": {"enabled": False},
                    "file_upload": {"image": {"enabled": False}},
                    "retriever_resource": {"enabled": False},
                },
            )
            _record("聊天应用绑定模型", status == 200, f"HTTP {status}")

    # 汇总 + 输出适配层所需环境变量
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== 引导汇总：{len(results) - len(failed)}/{len(results)} 通过 =====")
    print("\n# 适配层环境变量（写入 deploy/.env 或编排 environment）：")
    print(f"OA_DIFY_DEFAULT_APP_KEY={chat_key}")
    print(f"OA_DIFY_AGENT_APP_KEY={wf_key}")
    if failed:
        print("未通过：", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
