"""事实抽取器（阶段四 T4-03.3，架构 §5.3 / PRD §4.2）。

**这个模块绝不允许走 `ModelService.generate`**（本提交最硬的一条约束）。原因不是
洁癖，而是自喂循环：`generate` 会写 `message` 行 → 写行会推进"已累计 N 条 user 消息"
的计数 → 又触发一次抽取 → 又写 message……抽取链路会在无人察觉的情况下自我放大，
把用户的额度烧掉。因此这里只依赖一个**极薄的协议**：

```python
class ChatCompletion(Protocol):
    async def complete(self, prompt: str) -> str: ...
```

它不落 message、不计量、不注入 L3 记忆；测试用 Fake 实现即可完全离线。

抽取口径（PRD §4.2 / §4.3）：

- 6 类固定 schema，**不做自由文本记忆**；
- 单次最多 5 条、每条 ≤60 字，超出丢弃（不排队重试）；
- 只从 `role=user` 的消息抽取（助手说"我记住了"不是事实来源）；
- 敏感信息命中即丢弃：不落库、不记原文，只累加 `dropped_sensitive`。

敏感词表刻意用"足够广"而不是"精确"：误杀一条记忆的代价是"少记一件事"，
漏杀一条身份证号的代价是合规事故——两者不对称，所以宁可多杀。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.clients.dify_client import DifyClient
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.memory_repo import MEMORY_CATEGORIES

logger = get_logger(__name__)

#: 单次抽取的事实条数上限（PRD §4.3「单次最多抽 5 条」）
MAX_FACTS_PER_RUN: Final[int] = 5

#: 单条事实的字数上限（PRD §4.3「每条 ≤60 字」）
MAX_FACT_CHARS: Final[int] = 60

#: 语义相似度阈值（PRD §4.3：向量相似度 ≥ 0.9 视为同一件事）
SIMILARITY_THRESHOLD: Final[float] = 0.9

#: 抽取用的底座路径（独立应用，`OA_DIFY_MEMORY_APP_KEY`）
_DIFY_COMPLETION_PATH: Final[str] = "/v1/completion-messages"

#: 敏感信息正则（命中即丢弃；**不记录匹配到的原文**）
#
# ⚠ 数字类模式**不能用 `\b`**：中文是 `\w`，"手机号13800138000" 里
# `号` 与 `1` 之间不存在词边界，`\b1[3-9]\d{9}\b` 会**漏掉每一个真实号码**——
# 而漏杀的代价是合规事故。这里统一用 `(?<!\d)` / `(?!\d)` 只看"前后是不是数字"。
SENSITIVE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # 身份证：18 位（末位可为 X）
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    # 银行卡：16-19 位连续数字
    re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    # 手机号：中国大陆 11 位
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    # 密码 / 密钥 / 令牌类字样
    re.compile(r"(密码|口令|密钥|私钥|凭据|令牌|password|passwd|secret|token|api[_\s-]?key)", re.I),
    # 健康与宗教（PRD §4.2 第 1 条明确列举的两类特殊类别信息）
    re.compile(
        r"(病史|疾病|癌|糖尿病|高血压|抑郁|焦虑症|传染病|残疾|过敏|怀孕|手术|"
        r"信仰|宗教|佛教|道教|基督教|天主教|伊斯兰|穆斯林|基督徒|寺庙|教堂|礼拜)"
    ),
)

#: 模型输出的类别别名（中文/大小写变体都能收，避免因为一个词就整批丢弃）
_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "identity": "identity",
    "身份": "identity",
    "称呼": "identity",
    "org": "org",
    "组织": "org",
    "部门": "org",
    "所在团队": "org",
    "preference": "preference",
    "偏好": "preference",
    "工作习惯": "preference",
    "convention": "convention",
    "口径": "convention",
    "常用说法": "convention",
    "constraint": "constraint",
    "约束": "constraint",
    "注意事项": "constraint",
    "project": "project",
    "项目背景": "project",
    "项目": "project",
}

#: 抽取提示词（要求严格 JSON 输出；模型跑偏由 `_parse_payload` 兜住）
EXTRACT_PROMPT_TEMPLATE: Final[
    str
] = """你是资料整理助手。请从下面的用户消息里，抽出**关于这位用户本人**的、
**跨会话仍然成立**的稳定事实。

规则：
1. 只抽 6 类：identity（身份/称呼）、org（组织/部门/岗位）、preference（偏好/工作习惯）、
   convention（口径/内部叫法）、constraint（约束/禁止/必须）、project（项目背景）。
2. 一条事实一句话，不超过 {max_chars} 字；最多 {max_facts} 条。
3. 不抽一次性的安排（如"今天下午三点开会"）。
4. 不抽身份证号、银行卡号、手机号、密码/密钥、健康与宗教信仰。
5. 明确陈述用 "high"，推测性表述（"我可能是…"）用 "low"。
6. **只输出 JSON 数组**，不要任何解释文字。格式：
[{{"category": "preference", "content": "汇报要用表格", "confidence": "high"}}]
如果没有任何可抽的稳定事实，输出 []。

用户消息（按时间先后）：
{messages}
"""


class ChatCompletionError(RuntimeError):
    """抽取模型调用失败（上游错误/超时/返回不可解析）。

    抽取失败**不得影响对话**：本异常只会在后台任务的执行栈里出现，
    由 `JobRunner` 按 `max_attempts` 收口，绝不上抛到 HTTP 请求。
    """


#: 「未配置抽取应用」告警是否已打过（进程级，保证只打一次，不逐轮刷屏）
_missing_key_warned: bool = False


def warn_missing_memory_app_key_once(settings: Settings | None = None) -> bool:
    """未配置 `OA_DIFY_MEMORY_APP_KEY` 时打**一次** WARNING。

    为什么只打一次：这个判断每 10 轮就会被调用，逐次打日志会把真正有用的
    `memory_extract_degraded` 淹掉，也会让"日志 1 条"这条判据无法断言。

    Args:
        settings: 可选配置实例。

    Returns:
        bool: 本次是否输出了告警（已打过返回 False，便于用例断言"只一次"）。
    """
    global _missing_key_warned
    conf = settings or get_settings()
    if (conf.dify_memory_app_key or "").strip():
        return False
    if _missing_key_warned:
        return False
    _missing_key_warned = True
    logger.warning(
        "memory_extract_disabled_no_app_key",
        note=(
            "OA_DIFY_MEMORY_APP_KEY 未配置：不抽取、不入队。"
            "刻意不回落默认对话应用——那会产出非结构化文本并静默走降级。"
        ),
    )
    return True


def reset_missing_app_key_warning() -> None:
    """重置"已告警"标记（测试隔离用）。"""
    global _missing_key_warned
    _missing_key_warned = False


class ChatCompletion(Protocol):
    """极薄的一次性补全协议（**不落 message、不计量、不注入记忆**）。"""

    async def complete(self, prompt: str) -> str:
        """执行一次补全。

        Args:
            prompt: 完整提示词。

        Returns:
            str: 模型输出的原始文本。

        Raises:
            ChatCompletionError: 调用失败或返回为空。
        """
        ...


class DifyChatCompletion:
    """生产实现：走底座"事实抽取"专用应用（`OA_DIFY_MEMORY_APP_KEY`）。

    与 `ModelService` 的区别只有一点、但很关键：**这里不碰 message 表、不写计量**。
    客户端实例懒加载并复用（每次调用新建 httpx 连接池会造成 TIME_WAIT 堆积）。

    **密钥缺失时不回落到默认对话应用**（2026-10-24 主理人拍板）：默认应用带自己的
    系统提示与 RAG 上下文，拿它做 JSON 抽取大概率产出非结构化文本，然后静默走 D-1——
    现象是"配置看起来做了、实际一条记忆都没有"。所以缺 `OA_DIFY_MEMORY_APP_KEY` 时
    `available=False`：**不抽取、不入队**，只在进程内打一次 WARNING。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()
        self._client: DifyClient | None = None

    @property
    def app_key(self) -> str:
        """抽取专用应用密钥（**不回落到 `dify_default_app_key`**）。

        Returns:
            str: 应用密钥；未配置时为空串。
        """
        return (self._settings.dify_memory_app_key or "").strip()

    @property
    def available(self) -> bool:
        """是否已配置抽取专用应用（未配置则整套抽取链路不启动）。

        Returns:
            bool: 配了 `OA_DIFY_MEMORY_APP_KEY` 时返回 True。
        """
        return bool(self.app_key)

    def _get_client(self) -> DifyClient:
        """取（或惰性创建）底座客户端。

        Returns:
            DifyClient: 底座客户端。

        Raises:
            ChatCompletionError: 未配置任何可用密钥。
        """
        if self._client is None:
            key = self.app_key
            if not key:
                raise ChatCompletionError("memory extraction app key is not configured")
            self._client = DifyClient.for_app_key(key, self._settings)
        return self._client

    async def complete(self, prompt: str) -> str:
        """执行一次补全（blocking 模式）。

        Args:
            prompt: 完整提示词。

        Returns:
            str: 模型输出文本。

        Raises:
            ChatCompletionError: 上游失败或返回为空。
        """
        client = self._get_client()
        try:
            response = await client.post(
                _DIFY_COMPLETION_PATH,
                {
                    "inputs": {},
                    "query": prompt,
                    "response_mode": "blocking",
                    # 底座 user 字段是内部标识，不携带任何终端用户信息
                    "user": "memory-extractor",
                },
            )
        except ChatCompletionError:
            raise
        except Exception as exc:
            raise ChatCompletionError(f"downstream call failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ChatCompletionError(f"downstream status {response.status_code}")
        payload: dict[str, Any] = response.payload
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ChatCompletionError("downstream returned empty answer")
        return answer

    async def aclose(self) -> None:
        """释放底层客户端（应用停机时调用，避免连接泄漏）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def normalize_content(content: str) -> str:
    """归一化事实正文（用于算去重键与相似度比较）。

    归一化口径：NFKC（全角→半角）→ 小写 → 去掉空白与标点符号。
    "汇报要表格式" 与 "汇报要 表格式。" 必须归一成同一个键，否则同一条事实
    每次出现都会被当成新事实，200 条上限几天就被自家重复项填满。

    Args:
        content: 原始正文。

    Returns:
        str: 归一化正文。
    """
    normalized = unicodedata.normalize("NFKC", content or "").lower()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )


def normalized_hash(category: str, content: str) -> str:
    """计算去重键 `sha256(category + 归一化内容)`。

    Args:
        category: 类别。
        content: 事实正文。

    Returns:
        str: 十六进制摘要（空正文返回空串，避免空串落进唯一索引）。
    """
    body = normalize_content(content)
    if not body:
        return ""
    return hashlib.sha256(f"{category}\x00{body}".encode()).hexdigest()


def content_similarity(left: str, right: str) -> float:
    """计算两条事实的文本相似度（0~1）。

    为什么用 `difflib` 而不是向量：默认配置下没有 embedding 提供方
    （架构 §3.3.1 决策 A-1：Dify 不提供 raw embedding），若"没有向量就不去重"，
    那么**默认部署下 PRD §4.3 的"同一事实重复出现要合并"就永远不生效**——
    用户会看到同一条偏好在"我的记忆"里出现五遍。`difflib` 无网络依赖、
    结果确定、可离线单测，正是这个降级位需要的东西。

    Args:
        left: 归一化文本。
        right: 归一化文本。

    Returns:
        float: 相似度比值。
    """
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def contains_sensitive(content: str) -> bool:
    """判断正文是否命中敏感信息（命中即整条丢弃）。

    Args:
        content: 事实正文。

    Returns:
        bool: 命中任一敏感模式时返回 True。
    """
    text = content or ""
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


@dataclass(slots=True)
class FactCandidate:
    """模型抽取出的一条候选事实（尚未落库）。

    Attributes:
        category: 6 类之一。
        content: 正文（已裁剪，≤`MAX_FACT_CHARS`）。
        confidence: `high` / `low`。
    """

    category: str = "identity"
    content: str = ""
    confidence: str = "high"


@dataclass(slots=True)
class ExtractionOutcome:
    """一次抽取的结果统计（供日志与降级判据使用）。

    Attributes:
        facts: 通过校验的候选事实。
        dropped_sensitive: 因敏感信息被丢弃的条数（**只计数，不记原文**）。
        dropped_invalid: 因类别非法/正文为空被丢弃的条数。
        dropped_overflow: 因超出单次条数上限被丢弃的条数。
        raw_count: 模型返回的原始条数。
    """

    facts: list[FactCandidate] = field(default_factory=list)
    dropped_sensitive: int = 0
    dropped_invalid: int = 0
    dropped_overflow: int = 0
    raw_count: int = 0


def _extract_json_array(answer: str) -> list[Any]:
    """从模型输出里取出 JSON 数组（容忍 ```json 围栏与前后闲聊）。

    Args:
        answer: 模型原始输出。

    Returns:
        list[Any]: 数组元素。

    Raises:
        ChatCompletionError: 找不到可解析的 JSON 数组。
    """
    text = (answer or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            for key in ("facts", "items", "data", "memories"):
                value = loaded.get(key)
                if isinstance(value, list):
                    return value
            continue
        if isinstance(loaded, list):
            return loaded
    raise ChatCompletionError("extraction output is not a json array")


def _coerce_category(value: Any) -> str:
    """把模型给的类别规范成 6 类之一。

    Args:
        value: 原始类别值。

    Returns:
        str: 合法类别；无法识别返回空串。
    """
    text = str(value or "").strip().lower()
    if text in MEMORY_CATEGORIES:
        return text
    return _CATEGORY_ALIASES.get(text, "")


def _coerce_confidence(value: Any) -> str:
    """把模型给的置信度规范成 `high` / `low`（缺省 high）。

    Args:
        value: 原始置信度。

    Returns:
        str: `high` 或 `low`。
    """
    text = str(value or "").strip().lower()
    if text in {"low", "低", "推测", "maybe"}:
        return "low"
    return "high"


class MemoryExtractor:
    """把用户消息抽成 6 类事实（无状态、可离线单测）。"""

    def __init__(
        self,
        chat: ChatCompletion,
        *,
        max_facts: int = MAX_FACTS_PER_RUN,
        max_chars: int = MAX_FACT_CHARS,
    ) -> None:
        """初始化。

        Args:
            chat: 补全协议实现（生产用 `DifyChatCompletion`，测试用 Fake）。
            max_facts: 单次最多返回条数。
            max_chars: 单条正文长度上限。
        """
        self._chat = chat
        self._max_facts = max(1, int(max_facts))
        self._max_chars = max(1, int(max_chars))

    @property
    def max_facts(self) -> int:
        """单次最多返回条数。

        Returns:
            int: 条数上限。
        """
        return self._max_facts

    @property
    def available(self) -> bool:
        """抽取链路是否可用（生产实现缺密钥时为 False）。

        读 `getattr(..., "available", True)`：`ChatCompletion` 协议刻意保持极薄
        （只有一个 `complete`），测试替身不必实现该属性，缺省视为可用。

        Returns:
            bool: 可用返回 True。
        """
        return bool(getattr(self._chat, "available", True))

    def build_prompt(self, messages: list[str]) -> str:
        """拼抽取提示词。

        Args:
            messages: 用户消息正文（**只应包含 `role=user` 的消息**）。

        Returns:
            str: 完整提示词。
        """
        joined = "\n".join(f"- {text.strip()}" for text in messages if text.strip())
        return EXTRACT_PROMPT_TEMPLATE.format(
            max_chars=self._max_chars, max_facts=self._max_facts, messages=joined
        )

    async def extract(
        self, messages: list[str], *, categories: set[str] | None = None
    ) -> ExtractionOutcome:
        """抽取事实（含敏感丢弃、类别过滤、条数与字数上限）。

        Args:
            messages: 用户消息正文（**调用方负责只传 `role=user` 的消息**）。
            categories: 允许的类别集合（`None` 表示全部允许；分类开关关闭的类别在此过滤）。

        Returns:
            ExtractionOutcome: 抽取结果与丢弃统计。

        Raises:
            ChatCompletionError: 模型调用失败或输出不可解析（由调用方降级处理）。
        """
        if not [text for text in messages if text.strip()]:
            return ExtractionOutcome()
        answer = await self._chat.complete(self.build_prompt(messages))
        items = _extract_json_array(answer)
        outcome = ExtractionOutcome(raw_count=len(items))
        seen: set[str] = set()
        allowed = categories if categories is not None else set(MEMORY_CATEGORIES)
        for item in items:
            if not isinstance(item, dict):
                outcome.dropped_invalid += 1
                continue
            category = _coerce_category(item.get("category"))
            content = str(item.get("content") or "").strip()
            if not category or not content or category not in allowed:
                outcome.dropped_invalid += 1
                continue
            if contains_sensitive(content):
                # **只计数，不落库、不记原文**（PRD §4.2 第 1 条）
                outcome.dropped_sensitive += 1
                continue
            trimmed = content[: self._max_chars].strip()
            if not trimmed:
                outcome.dropped_invalid += 1
                continue
            digest = normalized_hash(category, trimmed)
            if digest in seen:  # 同批内重复只留一条
                continue
            seen.add(digest)
            if len(outcome.facts) >= self._max_facts:
                outcome.dropped_overflow += 1
                continue
            outcome.facts.append(
                FactCandidate(
                    category=category,
                    content=trimmed,
                    confidence=_coerce_confidence(item.get("confidence")),
                )
            )
        return outcome


__all__: list[str] = [
    "EXTRACT_PROMPT_TEMPLATE",
    "MAX_FACTS_PER_RUN",
    "MAX_FACT_CHARS",
    "SENSITIVE_PATTERNS",
    "SIMILARITY_THRESHOLD",
    "ChatCompletion",
    "ChatCompletionError",
    "DifyChatCompletion",
    "ExtractionOutcome",
    "FactCandidate",
    "MemoryExtractor",
    "contains_sensitive",
    "content_similarity",
    "normalize_content",
    "normalized_hash",
    "reset_missing_app_key_warning",
    "warn_missing_memory_app_key_once",
]
