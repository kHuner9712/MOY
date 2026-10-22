"""进程内指标计数器（阶段四 T4-03.4 埋点用；**不引入第三方依赖**）。

⚠ **指标名与标签必须与架构 §8.1 逐字一致**（T4-05.1 只做 exposition、不改名）：

- `memory_injected_total{result="ok"|"empty"|"degraded"}`
- `memory_recall_degraded_total{reason="timeout"|"vector_unavailable"}`

改这里的名字等于单方面撕毁监控契约：看板、告警规则与 runbook 会同时失效，
而且失效方式是"新指标 0 增长"（没人会立刻发现）。要改先改架构 §8.1。

为什么是这样一个极小的自制注册表，而不是 `prometheus_client`：

1. 本阶段**端点预算固定 10 个**（PRD §0.3），加 `/metrics` 端点会直接打破
   `phase4_present` 与 `test_endpoint_baseline.py` 的断言；
2. `/metrics` 导出器本身是 T4-05（可观测）的交付物，这里只负责"把数记下来"，
   导出格式由那一批统一决定——两批各写一套 exporter 必然漂移；
3. 不新增依赖（本阶段策略）。

用法：

```python
MEMORY_INJECTED_TOTAL.inc(result="ok")    # 标签值只用固定枚举，别放 user/tenant
...
MEMORY_INJECTED_TOTAL.snapshot()          # {"memory_injected_total{result=ok}": 3}
```

**基数纪律**：标签值必须是**有限枚举**（`ok|empty|degraded`）。把 `user_id` / `tenant_id`
当标签会让指标基数随用户数线性膨胀，最后把监控打挂——这是埋点最常见的自伤方式。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: 全局计数器注册表（键：指标名）
_registry: Final[dict[str, Counter]] = {}


class Counter:
    """带固定标签集的单调计数器。

    Attributes:
        name: 指标名（如 `memory_injected_total`）。
        label_names: 允许的标签名（顺序无关；不在其中的标签会被忽略并记入 `rejected`）。
    """

    __slots__ = ("_label_names", "_rejected", "_values", "name")

    def __init__(self, name: str, label_names: tuple[str, ...] = ()) -> None:
        """初始化计数器。

        Args:
            name: 指标名。
            label_names: 允许的标签名。
        """
        self.name = name
        self._label_names = frozenset(label_names)
        self._values: dict[tuple[tuple[str, str], ...], int] = {}
        self._rejected = 0

    @property
    def label_names(self) -> tuple[str, ...]:
        """允许的标签名（按字典序，便于断言）。

        Returns:
            tuple[str, ...]: 标签名。
        """
        return tuple(sorted(self._label_names))

    @property
    def rejected(self) -> int:
        """被拒绝的标签组合次数（非 0 说明有人往标签里塞了高基数值）。

        Returns:
            int: 次数。
        """
        return self._rejected

    def inc(self, amount: int = 1, **labels: str) -> None:
        """自增。

        Args:
            amount: 增量（必须 > 0；计数器单调）。
            **labels: 标签键值（只允许 `label_names` 里的键）。
        """
        if amount <= 0:
            return
        if not set(labels).issubset(self._label_names):
            self._rejected += 1
            return
        key = tuple(sorted((str(key), str(value)) for key, value in labels.items()))
        self._values[key] = self._values.get(key, 0) + int(amount)

    def value(self, **labels: str) -> int:
        """读取某个标签组合的当前值（未出现过返回 0）。

        Args:
            **labels: 标签键值。

        Returns:
            int: 当前值。
        """
        key = tuple(sorted((str(key), str(value)) for key, value in labels.items()))
        return self._values.get(key, 0)

    def total(self) -> int:
        """所有标签组合的合计（自检与用例用）。

        Returns:
            int: 合计值。
        """
        return sum(self._values.values())

    def snapshot(self) -> dict[str, int]:
        """导出快照（供 T4-05 的 exporter 直接消费）。

        Returns:
            dict[str, int]: 形如 `{"name{label=value}": 3}` 的映射。
        """
        result: dict[str, int] = {}
        for key, value in self._values.items():
            if not key:
                result[self.name] = value
                continue
            rendered = ",".join(f'{label}="{label_value}"' for label, label_value in key)
            result[f"{self.name}{{{rendered}}}"] = value
        return result

    def reset(self) -> None:
        """清零（测试隔离用）。"""
        self._values.clear()
        self._rejected = 0


def counter(name: str, label_names: tuple[str, ...] = ()) -> Counter:
    """取（或登记）一个计数器（同名幂等）。

    Args:
        name: 指标名。
        label_names: 允许的标签名。

    Returns:
        Counter: 计数器实例。
    """
    existing = _registry.get(name)
    if existing is not None:
        return existing
    created = Counter(name, label_names)
    _registry[name] = created
    return created


def snapshot() -> dict[str, int]:
    """导出全部计数器的快照。

    Returns:
        dict[str, int]: 合并后的指标映射。
    """
    merged: dict[str, int] = {}
    for item in _registry.values():
        merged.update(item.snapshot())
    return merged


def reset_counters() -> None:
    """清零全部计数器（测试隔离用；不清注册表，避免单例被换掉）。"""
    for item in _registry.values():
        item.reset()


#: 记忆注入次数（标签 `result=ok|empty|degraded`，**不要**加 user/tenant 标签）
MEMORY_INJECTED_TOTAL: Final[Counter] = counter("memory_injected_total", ("result",))

#: 记忆召回降级次数（标签 `reason=timeout|vector_unavailable`）
MEMORY_RECALL_DEGRADED_TOTAL: Final[Counter] = counter("memory_recall_degraded_total", ("reason",))


def labels_of(value: Mapping[str, str]) -> str:
    """把标签映射渲染成稳定字符串（便于日志与断言，避免各处手写格式）。

    Args:
        value: 标签映射。

    Returns:
        str: 形如 `ok` / `reason=timeout` 的串。
    """
    return ",".join(f"{key}={item}" for key, item in sorted(value.items()))


__all__: list[str] = [
    "MEMORY_INJECTED_TOTAL",
    "MEMORY_RECALL_DEGRADED_TOTAL",
    "Counter",
    "counter",
    "labels_of",
    "reset_counters",
    "snapshot",
]
