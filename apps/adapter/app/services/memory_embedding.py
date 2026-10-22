"""记忆向量化端口（阶段四 T4-03.3，架构 §3.3.1 / 决策 A-1）。

默认**没有**可用的 embedding 提供方：Dify 1.17 的 Service API 不提供 raw embedding，
DeepSeek 系模型也没有 embeddings 端点。因此本模块的默认实现是
`NullEmbeddingProvider`（`available=False`），调用方据此：

- 去重退化为 `difflib` 字符串相似度（PRD §4.3 的"同一事实要合并"仍然生效）；
- 向量写入跳过（`memory_embedding` 无行），**事实照存**（PRD §4.7 D-3）。

客户自带 OpenAI 兼容 embeddings 时开 `OA_MEMORY_EMBEDDING_ENABLED=true` 并配齐
`base_url` / `api_key` / `model` / `dim`；维度固定进索引（`vector(N)`），换模型必须
重建该列——这条写进运维手册。本提交（抽取）只用到"可用/不可用"与余弦计算；
真正发起 HTTP 的 provider 随召回（提交 3）落地，故此处不引入额外网络客户端。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认向量维度（与 `memory_embedding.embedding vector(1024)` 一致）
DEFAULT_DIM: Final[int] = 1024


class EmbeddingUnavailableError(RuntimeError):
    """向量能力不可用（未配置 / 已关闭 / 调用失败）。

    调用方**必须**捕获它并降级（跳过向量、只用字符串相似度），
    绝不允许它让抽取或对话失败（PRD §4.7 D-3）。
    """


class EmbeddingProvider(Protocol):
    """向量化端口。"""

    @property
    def available(self) -> bool:
        """当前是否可用（不可用时调用方跳过一切向量操作）。

        Returns:
            bool: 可用返回 True。
        """
        ...

    @property
    def model(self) -> str:
        """向量模型标识（写入 `memory_embedding.model`，换模型需重算）。

        Returns:
            str: 模型标识；不可用时为空串。
        """
        ...

    @property
    def dim(self) -> int:
        """向量维度。

        Returns:
            int: 维度。
        """
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化。

        Args:
            texts: 待向量化文本。

        Returns:
            list[list[float]]: 与输入等长的向量列表。

        Raises:
            EmbeddingUnavailableError: 能力不可用或调用失败。
        """
        ...


class NullEmbeddingProvider:
    """不可用实现（默认）。

    故意**抛异常**而不是返回零向量：零向量的余弦相似度恒为 0，
    会把"没有向量能力"伪装成"两条事实不相似"，让去重静默失效。
    """

    @property
    def available(self) -> bool:
        """恒不可用。

        Returns:
            bool: 恒为 False。
        """
        return False

    @property
    def model(self) -> str:
        """不可用时无模型标识。

        Returns:
            str: 空串。
        """
        return ""

    @property
    def dim(self) -> int:
        """默认维度（仅用于建表口径说明）。

        Returns:
            int: `DEFAULT_DIM`。
        """
        return DEFAULT_DIM

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """始终失败。

        Args:
            texts: 待向量化文本（忽略）。

        Returns:
            list[list[float]]: 不会返回。

        Raises:
            EmbeddingUnavailableError: 恒抛出。
        """
        raise EmbeddingUnavailableError("embedding provider is not configured")


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """计算余弦相似度。

    Args:
        left: 向量 A。
        right: 向量 B。

    Returns:
        float: 余弦相似度（0~1 的常见区间；维度不一致或含零向量返回 0.0）。
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_left = 0.0
    norm_right = 0.0
    for a, b in zip(left, right, strict=True):
        dot += float(a) * float(b)
        norm_left += float(a) * float(a)
        norm_right += float(b) * float(b)
    if norm_left <= 0.0 or norm_right <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """取向量化实现（当前仅"不可用"一种；配置不全会显式告警）。

    Args:
        settings: 可选配置实例。

    Returns:
        EmbeddingProvider: 端口实现。

    说明：
        `memory_embedding_enabled=true` 但缺少 `base_url`/`api_key`/`model` 属于
        配置事故，此时**不静默降级**：打 WARNING 并返回不可用实现，
        以免运维以为"开了就是好的"。
    """
    conf = settings or get_settings()
    if not conf.memory_embedding_enabled:
        return NullEmbeddingProvider()
    if not (
        conf.memory_embedding_base_url
        and conf.memory_embedding_api_key
        and conf.memory_embedding_model
    ):
        logger.warning(
            "memory_embedding_misconfigured",
            note="enabled without base_url/api_key/model; falling back to no-vector mode",
        )
        return NullEmbeddingProvider()
    logger.warning(
        "memory_embedding_provider_pending",
        model=conf.memory_embedding_model,
        note="openai-compatible embedder lands with recall; extraction uses no-vector mode",
    )
    return NullEmbeddingProvider()


__all__: list[str] = [
    "DEFAULT_DIM",
    "EmbeddingProvider",
    "EmbeddingUnavailableError",
    "NullEmbeddingProvider",
    "cosine_similarity",
    "get_embedding_provider",
]
