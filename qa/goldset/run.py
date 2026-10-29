#!/usr/bin/env python
"""麦子（Moy）V1.0 · 金标任务集加载与复跑工具（`docs/architecture-phase4.md` §9.7 / P4-20）。

职责边界（**V1.0 阶段刻意只做三件事**）：

1. 加载 `qa/goldset/T01.yaml` ~ `T20.yaml`，校验用例格式与评分口径一致性；
2. 以真实 HTTP 调用打通 M1（`POST /api/v1/model/generate`）与插件任务
   （`POST /api/v1/agent/tasks`）拿到产物，落盘到 `results/<date>/<case>/`；
3. 产出**可复算**的 `results/<date>/summary.csv`。

不做什么：

- 不做四维人工评分（完整性/准确性/可直接使用/可核查），只在产物旁给出结构自检结果，
  由人按 `scoring.md` 的锚点打 1/3/5 写进 `scores.json`，再由 `--recompute` 合成总表；
- **拒绝伪造结果**：缺凭据、服务不可达、用例未执行时一律 `status=blocked` 并以
  非零码退出，**绝不**写一行假的 `summary.csv`。这是本项目 R2-QA-02 的教训
  （「缺凭据不许静默跳过」）。

退出码（**故意分开，便于 CI 与人工一眼区分「没跑」与「跑挂了」**）：

| 码 | 含义 | 触发情形 |
| --- | --- | --- |
| 0 | 全部执行且自动检查通过 | 正常完成 |
| 1 | 执行了，但有用例自动检查未通过 | 真实失败 |
| 2 | 用法错误 / 环境不满足 | 参数写错、缺 PyYAML |
| 3 | **blocked：未执行** | 缺凭据、服务不可达、编造防护触发 |
| 4 | 用例格式错误 | yaml 结构/评分口径不合规 |

用法：

    python qa/goldset/run.py --list                     # 列 20 条 + 场景分布
    python qa/goldset/run.py --validate                 # 只校验格式（CI 用）
    python qa/goldset/run.py --case T07 --out results/  # 跑单条
    python qa/goldset/run.py --all --out results/       # 跑全部（需凭据）
    python qa/goldset/run.py --recompute --run-dir results/2026-10-30

凭据（缺一即 `blocked`，不做任何「本地假装成功」）：

    export OA_GOLDSET_BASE_URL=http://127.0.0.1:8000
    export OA_GOLDSET_TOKEN=<Bearer 令牌>
    export OA_GOLDSET_TENANT_ID=default     # 可选，SaaS 模式必填
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

#: 期望的用例 ID 全集。少一条即「用例集不完整」，`--validate` 判 4。
EXPECTED_CASE_IDS: tuple[str, ...] = tuple(f"T{i:02d}" for i in range(1, 21))

#: 评分维度：yaml 中允许的两种键名 → 内部代号。
#: 中文键是 `scoring.md` 的**口径名**（报告与人工评分用它），
#: ASCII 键是 yaml 里的**书写名**（便于工具消费与跨编辑器高亮）。
#: 两种都收，是为了让人改 yaml 时不必记「哪个词条该用哪种文字」——
#: 但**只认这十个**，多一个、少一个都判格式错（`--validate` 退出 4）。
DIMENSIONS: dict[str, str] = {
    "完整性": "D1",
    "准确性": "D2",
    "可直接使用": "D3",
    "无技术暴露": "D4",
    "可核查": "D5",
    "completeness": "D1",
    "accuracy": "D2",
    "usable": "D3",
    "no_tech_exposure": "D4",
    "verifiable": "D5",
}

#: 每个内部代号必须**恰好**有一个键命中（中英文任一），缺了即维度缺失。
DIMENSION_KEYS_BY_CODE: dict[str, tuple[str, ...]] = {
    code: tuple(alias for alias, mapped in DIMENSIONS.items() if mapped == code)
    for code in ("D1", "D2", "D3", "D4", "D5")
}

#: 机器自动打分的那一维（`scoring.md` §1 / §2 D4）
AUTO_DIMENSIONS: tuple[str, ...] = ("D4",)

#: 必须由人工打的四维（D4 之外的全部）。`scores.json` 缺任一项即判格式错。
HUMAN_DIMENSIONS: tuple[str, ...] = tuple(
    dim for dim in ("D1", "D2", "D3", "D4", "D5") if dim not in AUTO_DIMENSIONS
)

#: 五维权重（百分比）。与 `scoring.md` §1 **必须逐字一致**，合计必须 100。
WEIGHTS: dict[str, int] = {"D1": 25, "D2": 30, "D3": 25, "D4": 10, "D5": 10}

#: 合格线（`scoring.md` §3）
PASS_LINE: float = 3.5

#: 单维只允许 1/3/5 三档（`scoring.md` §2）
ALLOWED_SCORES: frozenset[int] = frozenset({1, 3, 5})

#: 禁词表：(匹配方式, 词)。`sub`=子串（中文词），`word`=词边界（ASCII 词，
#: 避免 `segment` 命中 `segment_count`）。口径与
#: `apps/web/scripts/check-banned-terms.mjs` 的 `TERM_GROUPS` 对齐。
BANNED_TERMS: tuple[tuple[str, str], ...] = (
    ("sub", "向量"),
    ("sub", "召回"),
    ("sub", "分块"),
    ("sub", "切片"),
    ("sub", "索引"),
    ("sub", "相似度"),
    ("sub", "阈值"),
    ("sub", "模型"),
    ("word", "gpt"),
    ("word", "claude"),
    ("word", "qwen"),
    ("word", "deepseek"),
    ("word", "gemini"),
    ("word", "embedding"),
    ("word", "rag"),
    ("word", "agent"),
    ("word", "prompt"),
    ("word", "token"),
    ("word", "top_k"),
    ("word", "score"),
    ("word", "chunk"),
    ("word", "segment"),
    ("word", "milvus"),
)

#: 「模型」是子串词，会命中「数据模型/商业模型」这类与 AI 无关的复合词。
#: 命中只落在这些复合词里时按豁免处理（`scoring.md` §2 D4 的豁免条款）。
EXEMPT_COMPOUNDS: tuple[str, ...] = (
    "数据模型",
    "商业模型",
    "业务模型",
    "领域模型",
    "原型模型",
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_BAD_CASE = 4

REQUIRED_CASE_KEYS: tuple[str, ...] = (
    "id",
    "title",
    "scenario",
    "deliverable_type",
    "input",
    "expected_artifact",
    "pass_criteria",
)

VALID_CHANNELS: frozenset[str] = frozenset({"chat", "kb", "plugin"})


class BlockedError(RuntimeError):
    """未执行：缺凭据或服务不可达。**必须**以退出码 3 结束，不得降级为成功。"""


class CaseFormatError(RuntimeError):
    """用例格式错误（退出码 4）。"""


# ---------------------------------------------------------------------------
# 用例加载与校验
# ---------------------------------------------------------------------------
def _load_yaml() -> Any:
    """惰性导入 PyYAML。

    Returns:
        Any: `yaml` 模块。

    Raises:
        SystemExit: 缺依赖（退出码 2，且提示用 adapter venv 的 python）。
    """
    try:
        import yaml  # 惰性导入：让 --help 与参数错误不依赖 PyYAML 是否装好
    except ModuleNotFoundError:  # pragma: no cover - 环境缺失路径
        print(
            "用法错误：缺少 PyYAML。请用 apps/adapter/.venv 的解释器，或 pip install pyyaml",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE) from None
    return yaml


def load_cases(case_dir: Path | None = None) -> list[dict[str, Any]]:
    """加载全部用例并按 ID 排序。

    Args:
        case_dir: 用例目录，缺省为 `qa/goldset`。

    Returns:
        list[dict[str, Any]]: 用例字典列表（已填入 `_path`）。

    Raises:
        CaseFormatError: yaml 语法错误，或文件不是映射。
    """
    yaml = _load_yaml()
    root = case_dir or HERE
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("T[0-9][0-9].yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise CaseFormatError(f"{path.name}: YAML 语法错误：{exc}") from exc
        if not isinstance(data, dict):
            raise CaseFormatError(f"{path.name}: 顶层必须是映射（mapping），实际是 {type(data)}")
        data["_path"] = str(path)
        cases.append(data)
    cases.sort(key=lambda c: str(c.get("id", "")))
    return cases


def _banned_hits(text: str) -> list[str]:
    """扫描文本中的禁词命中。

    Args:
        text: 待扫描文本（面向用户可见的产物文本）。

    Returns:
        list[str]: 命中词列表（去重、保序）。
    """
    hits: list[str] = []
    lowered = text.lower()
    for kind, term in BANNED_TERMS:
        if kind == "sub":
            if term in text and not _only_exempt(text, term):
                hits.append(term)
        else:
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"
            if re.search(pattern, lowered):
                hits.append(term)
    return hits


def _only_exempt(text: str, term: str) -> bool:
    """判断「模型」类子串词的命中是否全部落在豁免复合词里。

    Args:
        text: 待扫描文本。
        term: 命中词。

    Returns:
        bool: True 表示全部命中都在豁免复合词内（不计命中）。
    """
    stripped = text
    for compound in EXEMPT_COMPOUNDS:
        stripped = stripped.replace(compound, "")
    return term not in stripped


def validate_case(case: dict[str, Any]) -> list[str]:
    """校验单条用例的格式与评分口径。

    Args:
        case: 用例字典。

    Returns:
        list[str]: 错误信息列表（空列表表示合规）。
    """
    errors: list[str] = []
    name = Path(str(case.get("_path", case.get("id", "<unknown>")))).name

    for key in REQUIRED_CASE_KEYS:
        if key not in case:
            errors.append(f"{name}: 缺少必填键 `{key}`")
    if errors:
        return errors

    case_id = str(case["id"])
    if Path(str(case["_path"])).stem != case_id:
        errors.append(f"{name}: 文件名与 id 不一致（id={case_id}）")
    if case_id not in EXPECTED_CASE_IDS:
        errors.append(f"{name}: id `{case_id}` 不在 T01..T20 范围内")

    inp = case["input"]
    if not isinstance(inp, dict):
        errors.append(f"{name}: `input` 必须是映射")
    else:
        channel = inp.get("channel")
        if channel not in VALID_CHANNELS:
            errors.append(
                f"{name}: `input.channel` 必须是 {sorted(VALID_CHANNELS)} 之一，实际 `{channel}`"
            )
        turns = inp.get("turns")
        has_turns = isinstance(turns, list) and bool(turns)
        if not str(inp.get("user_query", "")).strip() and not has_turns:
            errors.append(f"{name}: `input.user_query` 与 `input.turns` 至少给一个（都为空）")
        if has_turns:
            for index, turn in enumerate(turns, start=1):  # type: ignore[union-attr]
                if not isinstance(turn, dict) or not str(turn.get("content", "")).strip():
                    errors.append(f"{name}: `input.turns[{index}]` 缺 content")
        if channel == "plugin" and not str(case.get("plugin_id") or "").strip():
            errors.append(f"{name}: channel=plugin 时必须有 `plugin_id`")
        if channel in {"chat", "kb"} and not str(inp.get("profile", "")).strip():
            errors.append(f"{name}: channel={channel} 时必须有 `input.profile`")

    art = case["expected_artifact"]
    if not isinstance(art, dict):
        errors.append(f"{name}: `expected_artifact` 必须是映射")
    else:
        if not str(art.get("kind", "")).strip():
            errors.append(f"{name}: `expected_artifact.kind` 不得为空")
        if not str(art.get("description", "")).strip():
            errors.append(f"{name}: `expected_artifact.description` 不得为空")
        for key in ("must_contain", "must_not_contain"):
            value = art.get(key)
            if not isinstance(value, list) or not value:
                errors.append(f"{name}: `expected_artifact.{key}` 必须是非空列表")

    criteria = case["pass_criteria"]
    if not isinstance(criteria, dict):
        errors.append(f"{name}: `pass_criteria` 必须是映射")
        return errors
    for code, aliases in DIMENSION_KEYS_BY_CODE.items():
        present = [alias for alias in aliases if alias in criteria]
        if not present:
            errors.append(f"{name}: `pass_criteria` 缺少维度 {code}（{aliases[0]}）")
        elif len(present) > 1:
            errors.append(f"{name}: `pass_criteria` 维度 {code} 被重复书写 {present}，只留一个")
    unknown = [key for key in criteria if key not in DIMENSIONS]
    if unknown:
        errors.append(
            f"{name}: `pass_criteria` 含未知维度 {unknown}（只允许 {sorted(DIMENSIONS)}）"
        )
    for key in criteria:
        if key not in DIMENSIONS:
            continue
        items = criteria.get(key)
        if not isinstance(items, list) or not items:
            errors.append(f"{name}: `pass_criteria.{key}` 必须是非空列表")
            continue
        for index, item in enumerate(items, start=1):
            if not isinstance(item, str) or len(item.strip()) < 4:
                errors.append(f"{name}: `pass_criteria.{key}` 第 {index} 条过短或非字符串")

    exposure_keys = [alias for alias in DIMENSION_KEYS_BY_CODE["D4"] if alias in criteria]
    exposure = criteria.get(exposure_keys[0]) if exposure_keys else None
    if isinstance(exposure, list) and exposure:
        blob = " ".join(str(item) for item in exposure)
        if not _banned_hits(blob):
            errors.append(
                f"{name}: `pass_criteria.{exposure_keys[0]}` 未引用任何禁词，D4 无法被机器检测"
            )
    return errors


def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
    """校验整个用例集（集合完整性 + 单条格式 + 权重口径）。

    Args:
        cases: 用例字典列表。

    Returns:
        list[str]: 错误信息列表。
    """
    errors: list[str] = []
    if sum(WEIGHTS.values()) != 100:
        errors.append(f"评分口径错误：权重合计 {sum(WEIGHTS.values())} != 100（见 scoring.md §1）")
    if len(ALLOWED_SCORES) != 3:
        errors.append("评分口径错误：ALLOWED_SCORES 必须恰为 {1,3,5}")

    ids = [str(c.get("id", "")) for c in cases]
    duplicated = [cid for cid, count in Counter(ids).items() if count > 1]
    if duplicated:
        errors.append(f"用例 id 重复：{duplicated}")
    missing = [cid for cid in EXPECTED_CASE_IDS if cid not in set(ids)]
    if missing:
        errors.append(f"用例集不完整，缺少 {missing}")
    for case in cases:
        errors.extend(validate_case(case))
    return errors


# ---------------------------------------------------------------------------
# HTTP 客户端（stdlib，不引入新依赖）
# ---------------------------------------------------------------------------
class Client:
    """最小 HTTP 客户端：只负责带凭据发 JSON 与读 JSON。"""

    def __init__(self, base_url: str, token: str, tenant_id: str, timeout: float) -> None:
        """初始化。

        Args:
            base_url: 适配层基址，如 `http://127.0.0.1:8000`。
            token: Bearer 令牌（空串即视为缺凭据）。
            tenant_id: 租户 ID，可空。
            timeout: 单请求超时秒数。
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tenant_id = tenant_id
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        """构造请求头。

        Returns:
            dict[str, str]: 含鉴权、租户、幂等键的请求头。
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": f"goldset-{uuid.uuid4().hex}",
        }
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id
        return headers

    def get(self, path: str) -> dict[str, Any]:
        """发 GET。

        Args:
            path: 以 `/` 开头的路径。

        Returns:
            dict[str, Any]: 解析后的 JSON。

        Raises:
            BlockedError: 连接失败（服务不可达）。
            RuntimeError: 非 2xx 或非 JSON。
        """
        return self._request("GET", path, None)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发 POST。

        Args:
            path: 以 `/` 开头的路径。
            payload: JSON 请求体。

        Returns:
            dict[str, Any]: 解析后的 JSON。
        """
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        """发请求并解析响应。

        Args:
            method: HTTP 方法。
            path: 路径。
            payload: 请求体，可为 None。

        Returns:
            dict[str, Any]: 解析后的 JSON。

        Raises:
            BlockedError: 网络层不可达。
            RuntimeError: HTTP 非 2xx 或响应不是 JSON 对象。
        """
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - base_url 由使用者显式给定，只支持 http/https
            url, data=body, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise BlockedError(f"{method} {url} 无法连接：{exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{method} {path} 响应不是 JSON：{raw[:200]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{method} {path} 响应顶层不是对象：{type(data)}")
        return data

    def probe(self) -> None:
        """探活（`/healthz`，免鉴权）。

        Raises:
            BlockedError: 服务不可达或未就绪。
        """
        url = f"{self.base_url}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=min(self.timeout, 10.0)) as response:  # noqa: S310
                if response.status >= 400:
                    raise BlockedError(f"/healthz 返回 HTTP {response.status}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise BlockedError(f"服务不可达：{url}（{exc}）") from exc


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _collect_text(node: Any) -> list[str]:
    """递归收集 JSON 里所有字符串（用于禁词扫描）。

    Args:
        node: 任意 JSON 节点。

    Returns:
        list[str]: 字符串列表。
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for value in node.values():
            out.extend(_collect_text(value))
        return out
    if isinstance(node, list):
        out = []
        for item in node:
            out.extend(_collect_text(item))
        return out
    return []


def _chat_payload(case: dict[str, Any]) -> dict[str, Any]:
    """把用例的 `input` 转成 M1 请求体。

    Args:
        case: 用例字典。

    Returns:
        dict[str, Any]: `/api/v1/model/generate` 请求体。

    设计说明：多轮用例（`input.turns`）以 `assemble_from=client` 提交，
    用的是服务端为灰度对比保留的「逃生门」（`GenerationContext.assemble_from`），
    这样金标复跑不依赖库内历史，结果可复现。
    """
    inp = case["input"]
    turns = inp.get("turns")
    if isinstance(turns, list) and turns:
        messages = [
            {"role": str(turn.get("role", "user")), "content": str(turn.get("content", ""))}
            for turn in turns
        ]
    else:
        messages = [{"role": "user", "content": str(inp.get("user_query", ""))}]
    context = inp.get("context") if isinstance(inp.get("context"), dict) else {}
    return {
        "conversation_id": "",
        "profile": str(inp.get("profile", "general")),
        "messages": messages,
        "context": {
            "history_window": int(context.get("history_window", 20)),
            "memory_hints": [],
            "assemble_from": "client" if isinstance(turns, list) and turns else "server",
        },
        "knowledge_base_ids": list(inp.get("knowledge_base_ids") or []),
        "options": {},
    }


def _plugin_payload(case: dict[str, Any]) -> dict[str, Any]:
    """把用例的 `input` 转成 A1 请求体。

    Args:
        case: 用例字典。

    Returns:
        dict[str, Any]: `/api/v1/agent/tasks` 请求体。
    """
    inp = case["input"]
    return {
        "conversation_id": "",
        "goal": str(inp.get("user_query", ""))[:4000],
        "profile": str(inp.get("profile", "general")),
        "tool_grants": [],
        "inputs": dict(inp.get("plugin_inputs") or {}),
        "options": {},
        "plugin_id": str(case.get("plugin_id") or ""),
    }


def _poll_task(client: Client, task_id: str, wait_seconds: float) -> dict[str, Any]:
    """轮询插件任务直到终态或超时。

    Args:
        client: HTTP 客户端。
        task_id: 任务 ID。
        wait_seconds: 最长等待秒数。

    Returns:
        dict[str, Any]: 任务详情 `data`。

    Raises:
        RuntimeError: 任务进入失败/取消终态。
    """
    deadline = time.monotonic() + wait_seconds
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/agent/tasks/{task_id}")
        detail = dict(response.get("data") or {})
        status = str(detail.get("status", ""))
        if status in {"succeeded", "failed", "cancelled", "aborted"}:
            break
        time.sleep(2.0)
    return detail


def execute_case(
    client: Client, case: dict[str, Any], case_dir: Path, wait_seconds: float
) -> dict[str, Any]:
    """执行一条用例并把产物落盘。

    Args:
        client: HTTP 客户端。
        case: 用例字典。
        case_dir: 本条用例的产物目录。
        wait_seconds: 插件任务最长等待秒数。

    Returns:
        dict[str, Any]: 自动检查结果（含 `banned_hits`、`artifact_text` 摘要）。

    Raises:
        RuntimeError: 业务调用失败（HTTP 非 2xx 或 `code != 0`）。
    """
    inp = case["input"]
    channel = str(inp.get("channel"))
    case_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    if channel == "plugin":
        created = client.post("/api/v1/agent/tasks", _plugin_payload(case))
        if int(created.get("code", -1)) != 0:
            raise RuntimeError(
                f"A1 创建任务失败：code={created.get('code')} msg={created.get('message')}"
            )
        task_id = str((created.get("data") or {}).get("task_id", ""))
        detail = _poll_task(client, task_id, wait_seconds)
        if str(detail.get("status")) in {"failed", "cancelled", "aborted"}:
            raise RuntimeError(f"插件任务未成功：status={detail.get('status')}")
        artifact = {"task_id": task_id, "detail": detail}
        text_blob = "\n".join(_collect_text(detail))
    else:
        response = client.post("/api/v1/model/generate", _chat_payload(case))
        if int(response.get("code", -1)) != 0:
            raise RuntimeError(
                f"M1 生成失败：code={response.get('code')} msg={response.get('message')}"
            )
        data = dict(response.get("data") or {})
        artifact = data
        text_blob = "\n".join(
            [str(data.get("content", "")), *_collect_text(data.get("citations", []))]
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    (case_dir / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (case_dir / "artifact.txt").write_text(text_blob, encoding="utf-8")

    hits = _banned_hits(text_blob)
    auto = {
        "case": case["id"],
        "channel": channel,
        "elapsed_ms": elapsed_ms,
        "artifact_chars": len(text_blob),
        "banned_hits": hits,
        "D4": 5 if not hits else 1,
        "structure": _structure_check(case, text_blob),
        "executed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (case_dir / "auto.json").write_text(
        json.dumps(auto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return auto


def _structure_check(case: dict[str, Any], text: str) -> dict[str, Any]:
    """轻量结构自检：只做**可机判**的字符串存在性检查，不做语义判断。

    Args:
        case: 用例字典。
        text: 产物文本。

    Returns:
        dict[str, Any]: `{"checked": n, "missing": [...]}`。
    """
    must = case["expected_artifact"].get("must_contain") or []
    #: 只挑出「短且像关键词」的条目做存在性检查；长句是给人看的判定标准，
    #: 机器硬匹配会误判（例如「至少 2 条待确认项」不是一段可出现的文本）。
    keywords = [
        str(item)
        for item in must
        if len(str(item)) <= 12 and not str(item).startswith(("至少", "不得"))
    ]
    missing = [kw for kw in keywords if kw not in text]
    return {"checked": len(keywords), "missing": missing}


# ---------------------------------------------------------------------------
# 汇总与复算
# ---------------------------------------------------------------------------
def _verdict(total: float | None) -> str:
    """按合格线判定。

    Args:
        total: 加权总分，None 表示尚未人工评分。

    Returns:
        str: `pass` / `fail` / `pending_human`。
    """
    if total is None:
        return "pending_human"
    return "pass" if total >= PASS_LINE else "fail"


def _weighted(human: dict[str, Any], auto_scores: dict[str, int]) -> float:
    """按权重合成总分。

    Args:
        human: 人工分 `{"D1": 5, "D2": 3, ...}`（只含人工四维）。
        auto_scores: 机器自动分 `{"D4": 5}`。

    Returns:
        float: 保留两位的加权分。

    Raises:
        CaseFormatError: 五维未齐（说明有维度既没人工打也没自动算）。
    """
    merged: dict[str, int] = {
        **{k: int(v) for k, v in human.items() if k in WEIGHTS},
        **auto_scores,
    }
    missing = [dim for dim in WEIGHTS if dim not in merged]
    if missing:
        raise CaseFormatError(f"评分缺维度 {missing}，无法合成总分")
    total = sum(merged[dim] * weight for dim, weight in WEIGHTS.items()) / 100.0
    return round(total, 2)


SUMMARY_HEADER: tuple[str, ...] = (
    "case",
    "title",
    "scenario",
    "deliverable_type",
    "status",
    "D1_完整性",
    "D2_准确性",
    "D3_可直接使用",
    "D4_无技术暴露",
    "D5_可核查",
    "total",
    "verdict",
    "weights",
    "banned_hits",
    "elapsed_ms",
    "notes",
)


def write_summary(
    run_dir: Path, cases: list[dict[str, Any]], autos: dict[str, dict[str, Any]]
) -> Path:
    """写 `summary.csv`。

    Args:
        run_dir: `results/<date>` 目录。
        cases: 用例列表。
        autos: `{case_id: auto.json}`。

    Returns:
        Path: `summary.csv` 路径。

    说明：人工分从 `<case>/scores.json` 读（不存在即留空，`verdict=pending_human`）。
    `total` 只由 `_weighted` 计算，**不接受**外部传入的现成总分。
    先写 `.tmp` 再原子替换：复算中途报错时不会把上一份可用的 `summary.csv` 截断。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "summary.csv"
    tmp = run_dir / "summary.csv.tmp"
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SUMMARY_HEADER)
        for case in cases:
            case_id = str(case["id"])
            auto = autos.get(case_id)
            if auto is None:
                continue
            scores = _read_scores(run_dir / case_id / "scores.json")
            total = _weighted(scores, {"D4": int(auto.get("D4", 0))}) if scores else None
            writer.writerow(
                [
                    case_id,
                    case["title"],
                    case["scenario"],
                    case["deliverable_type"],
                    "executed",
                    scores.get("D1", ""),
                    scores.get("D2", ""),
                    scores.get("D3", ""),
                    auto.get("D4", ""),
                    scores.get("D5", ""),
                    "" if total is None else f"{total:.2f}",
                    _verdict(total),
                    "25/30/25/10/10",
                    ";".join(auto.get("banned_hits") or []),
                    auto.get("elapsed_ms", ""),
                    (scores or {}).get("notes", ""),
                ]
            )
    tmp.replace(path)
    return path


def _read_scores(path: Path) -> dict[str, Any]:
    """读人工评分文件并校验取值。

    Args:
        path: `scores.json` 路径。

    Returns:
        dict[str, Any]: 含 D1..D5 与 notes；文件不存在时返回空字典。

    Raises:
        CaseFormatError: 分数不在 {1,3,5} 内、四维不全，或手填了自动维度 D4。
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    scores: dict[str, Any] = {}
    for dim in HUMAN_DIMENSIONS:
        value = data.get(dim)
        if value is None:
            raise CaseFormatError(f"{path}: 缺少维度 {dim}（四维 {list(HUMAN_DIMENSIONS)} 必须齐）")
        if value not in ALLOWED_SCORES:
            raise CaseFormatError(f"{path}: {dim}={value} 不合法，只允许 1/3/5（见 scoring.md §2）")
        scores[dim] = int(value)
    for auto_dim in AUTO_DIMENSIONS:
        if auto_dim in data:
            #: 自动维度不接受人工覆盖——否则「无技术暴露」这条合规门槛可以被手填 5 分绕过
            raise CaseFormatError(
                f"{path}: {auto_dim} 由 run.py 自动扫描，不得手填（见 scoring.md §1）"
            )
    scores["notes"] = str(data.get("notes", ""))
    return scores


def recompute(run_dir: Path) -> Path:
    """从已有产物目录重算 `summary.csv`（可复算的落点）。

    Args:
        run_dir: `results/<date>` 目录。

    Returns:
        Path: `summary.csv` 路径。

    Raises:
        CaseFormatError: 该目录下没有 `auto.json`。
    """
    cases = [case for case in load_cases() if (run_dir / str(case["id"]) / "auto.json").exists()]
    if not cases:
        raise CaseFormatError(f"{run_dir} 下没有任何 auto.json，无法复算")
    autos = {
        str(case["id"]): json.loads(
            (run_dir / str(case["id"]) / "auto.json").read_text(encoding="utf-8")
        )
        for case in cases
    }
    return write_summary(run_dir, cases, autos)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_list(cases: list[dict[str, Any]]) -> None:
    """打印用例清单与场景分布。

    Args:
        cases: 用例列表。
    """
    print(f"{'ID':<5}{'场景':<14}{'交付物':<8}{'通道':<8}标题")
    print("-" * 78)
    for case in cases:
        channel = str(case["input"].get("channel", "?"))
        print(
            f"{case['id']:<5}{case['scenario']:<14}{case['deliverable_type']:<8}{channel:<8}{case['title']}"
        )
    print("-" * 78)
    print(f"合计 {len(cases)} 条（期望 {len(EXPECTED_CASE_IDS)} 条）")
    print("\n场景分布：")
    for scenario, count in Counter(str(c["scenario"]) for c in cases).most_common():
        print(f"  {scenario}：{count} 条")
    print("\n交付物类型分布：")
    for kind, count in Counter(str(c["deliverable_type"]) for c in cases).most_common():
        print(f"  {kind}：{count} 条")


def cmd_validate(cases: list[dict[str, Any]]) -> int:
    """校验并打印结果。

    Args:
        cases: 用例列表。

    Returns:
        int: 退出码（0 或 4）。
    """
    errors = validate_cases(cases)
    if errors:
        print(f"用例校验失败：{len(errors)} 项", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_BAD_CASE
    print(
        f"用例校验通过：{len(cases)} 条，五维权重合计 {sum(WEIGHTS.values())}%，合格线 {PASS_LINE}"
    )
    return EXIT_OK


def cmd_run(cases: list[dict[str, Any]], args: argparse.Namespace) -> int:
    """执行用例。

    Args:
        cases: 已筛选的用例列表。
        args: 命令行参数。

    Returns:
        int: 退出码。

    Raises:
        BlockedError: 缺凭据或服务不可达（由 `main` 转成退出码 3）。
    """
    token = args.token or os.environ.get("OA_GOLDSET_TOKEN", "")
    base_url = args.base_url or os.environ.get("OA_GOLDSET_BASE_URL", "")
    tenant_id = args.tenant_id or os.environ.get("OA_GOLDSET_TENANT_ID", "")
    if not token:
        raise BlockedError(
            "缺少凭据 OA_GOLDSET_TOKEN —— 金标执行需要真实模型凭据，"
            "不做本地兜底、不生成任何结果文件"
        )
    if not base_url:
        raise BlockedError("缺少 OA_GOLDSET_BASE_URL（适配层基址，如 http://127.0.0.1:8000）")

    client = Client(base_url, token, tenant_id, args.timeout)
    client.probe()

    run_dir = Path(args.out) / time.strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    autos: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for case in cases:
        case_id = str(case["id"])
        print(f"[run] {case_id} {case['title']} ...", flush=True)
        try:
            autos[case_id] = execute_case(client, case, run_dir / case_id, args.wait)
            print(
                f"       D4={autos[case_id]['D4']} 禁词命中={autos[case_id]['banned_hits']}",
                flush=True,
            )
        except RuntimeError as exc:
            failures.append(f"{case_id}: {exc}")
            print(f"       失败：{exc}", file=sys.stderr, flush=True)

    if not autos:
        raise BlockedError(f"全部用例执行失败，未产生任何产物：{failures[:3]}")

    summary = write_summary(run_dir, cases, autos)
    print(f"\nsummary: {summary}")
    print(f"已执行 {len(autos)}/{len(cases)} 条，失败 {len(failures)} 条")
    if failures:
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return EXIT_FAILED
    blocked = [cid for cid, auto in autos.items() if auto["D4"] < 5]
    if blocked:
        print(f"禁词命中（D4=1）：{blocked} —— 按 scoring.md §4 的 V3 判 fail", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _select_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """按 `--case` / `--all` 选用例。

    Args:
        cases: 全部用例。
        args: 命令行参数。

    Returns:
        list[dict[str, Any]]: 选中的用例。

    Raises:
        CaseFormatError: `--case` 指向不存在的 ID。
    """
    if args.all:
        return cases
    wanted: list[str] = []
    for chunk in args.case or []:
        wanted.extend(part.strip() for part in chunk.split(",") if part.strip())
    known = {str(case["id"]): case for case in cases}
    unknown = [cid for cid in wanted if cid not in known]
    if unknown:
        raise CaseFormatError(f"未知用例 ID：{unknown}（可用 --list 查看）")
    return [known[cid] for cid in wanted]


def _fail(message: str, code: int) -> NoReturn:
    """打印并以指定码退出。

    Args:
        message: 错误信息。
        code: 退出码。
    """
    print(message, file=sys.stderr)
    raise SystemExit(code)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="run.py",
        description=(
            "金标任务集加载/校验/复跑（qa/goldset）。"
            "退出码：0 通过 / 1 失败 / 2 用法 / 3 blocked（未执行）/ 4 用例格式错"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="列出 20 条用例与场景分布")
    parser.add_argument("--validate", action="store_true", help="只校验用例格式与评分口径")
    parser.add_argument(
        "--case", action="append", metavar="T07", help="要执行的用例 ID，可重复或用逗号分隔"
    )
    parser.add_argument("--all", action="store_true", help="执行全部 20 条")
    parser.add_argument("--out", default="results", help="结果根目录（默认 results/）")
    parser.add_argument("--run-dir", metavar="results/2026-10-30", help="--recompute 的目标目录")
    parser.add_argument("--recompute", action="store_true", help="从已有产物重算 summary.csv")
    parser.add_argument("--base-url", default="", help="适配层基址（默认读 OA_GOLDSET_BASE_URL）")
    parser.add_argument("--token", default="", help="Bearer 令牌（默认读 OA_GOLDSET_TOKEN）")
    parser.add_argument("--tenant-id", default="", help="租户 ID（默认读 OA_GOLDSET_TENANT_ID）")
    parser.add_argument("--timeout", type=float, default=60.0, help="单请求超时秒数（默认 60）")
    parser.add_argument(
        "--wait", type=float, default=300.0, help="插件任务最长等待秒数（默认 300）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 命令行参数，缺省取 `sys.argv[1:]`。

    Returns:
        int: 退出码。
    """
    args = build_parser().parse_args(argv)
    try:
        cases = load_cases()
    except CaseFormatError as exc:
        _fail(f"用例加载失败：{exc}", EXIT_BAD_CASE)

    if args.validate or args.list:
        code = cmd_validate(cases) if args.validate else EXIT_OK
        if args.list:
            cmd_list(cases)
        return code

    errors = validate_cases(cases)
    if errors:
        print(f"用例校验失败：{len(errors)} 项（先修用例再执行）", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_BAD_CASE

    if args.recompute:
        if not args.run_dir:
            _fail("--recompute 需要 --run-dir results/<date>", EXIT_USAGE)
        try:
            print(f"复算完成：{recompute(Path(args.run_dir))}")
        except CaseFormatError as exc:
            _fail(f"复算失败：{exc}", EXIT_BAD_CASE)
        return EXIT_OK

    if not args.case and not args.all:
        _fail(
            "需要 --case / --all / --list / --validate / --recompute 之一（-h 看用法）", EXIT_USAGE
        )

    try:
        selected = _select_cases(cases, args)
    except CaseFormatError as exc:
        _fail(str(exc), EXIT_BAD_CASE)
    if not selected:
        _fail("没有选中任何用例", EXIT_USAGE)

    try:
        return cmd_run(selected, args)
    except BlockedError as exc:
        #: 关键：blocked **不写任何结果文件**，避免与真实结果混淆（R2-QA-02 的教训）
        print(f"BLOCKED: 未执行 —— {exc}", file=sys.stderr)
        print("未生成 summary.csv（避免把「没跑」伪装成「跑过」）", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
