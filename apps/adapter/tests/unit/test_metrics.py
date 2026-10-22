"""进程内指标注册表的单元验收（阶段四 T4-03.4 埋点）。

三条判据：

1. **标签是有限枚举**：塞入未登记的标签一律拒绝并计数（防止基数爆炸）；
2. **指标名与架构 §8.1 逐字一致**：改名字等于单方面撕毁监控契约；
3. **快照可直接被 exporter 消费**：`name{label="value"}` 形态。
"""

from __future__ import annotations

import pytest

from app.core.metrics import (
    MEMORY_INJECTED_TOTAL,
    MEMORY_RECALL_DEGRADED_TOTAL,
    Counter,
    counter,
    labels_of,
    reset_counters,
    snapshot,
)

pytestmark = pytest.mark.unit


class TestCounter:
    """计数器本体。"""

    def test_increment_and_read(self) -> None:
        """按标签组合分别累加。"""
        item = Counter("demo_total", ("result",))

        item.inc()
        item.inc(result="ok")
        item.inc(2, result="ok")

        assert item.value(result="ok") == 3
        assert item.value(result="empty") == 0
        assert item.total() == 4

    def test_rejects_unknown_labels(self) -> None:
        """未登记的标签被拒绝并计数（高基数标签是埋点最常见的自伤方式）。"""
        item = Counter("demo_total", ("result",))

        item.inc(result="ok", user_id="usr_1")

        assert item.rejected == 1
        assert item.total() == 0
        assert item.label_names == ("result",)

    def test_non_positive_amount_is_ignored(self) -> None:
        """计数单调：0/负数不改变数值。"""
        item = Counter("demo_total", ("result",))
        item.inc(0, result="ok")
        item.inc(-5, result="ok")
        assert item.total() == 0

    def test_snapshot_renders_prometheus_shape(self) -> None:
        """快照形如 `name{label="value"}`，供 T4-05 的 exporter 直接输出。"""
        item = Counter("demo_total", ("result",))
        item.inc(result="ok")

        assert item.snapshot() == {'demo_total{result="ok"}': 1}

    def test_snapshot_without_labels(self) -> None:
        """无标签计数器的快照就是指标名本身。"""
        item = Counter("plain_total")
        item.inc()
        item.inc()
        assert item.snapshot() == {"plain_total": 2}

    def test_reset_clears_values(self) -> None:
        """清零（测试隔离用）。"""
        item = Counter("demo_total", ("result",))
        item.inc(result="ok")
        item.reset()
        assert item.total() == 0
        assert item.rejected == 0


class TestRegistry:
    """注册表与既有指标契约。"""

    def test_counter_registration_is_idempotent(self) -> None:
        """同名重复登记返回同一实例（否则指标会分裂成两条）。"""
        assert counter("demo_total", ("result",)) is counter("demo_total", ("result",))

    def test_memory_metric_contract(self) -> None:
        """指标名与标签集必须与架构 §8.1 逐字一致（T4-05 只做 exposition）。"""
        assert MEMORY_INJECTED_TOTAL.name == "memory_injected_total"
        assert MEMORY_INJECTED_TOTAL.label_names == ("result",)
        assert MEMORY_RECALL_DEGRADED_TOTAL.name == "memory_recall_degraded_total"
        assert MEMORY_RECALL_DEGRADED_TOTAL.label_names == ("reason",)

    def test_snapshot_and_reset_all(self) -> None:
        """全局快照可见已记录的值，`reset_counters()` 全部清零。"""
        MEMORY_INJECTED_TOTAL.inc(result="ok")
        MEMORY_RECALL_DEGRADED_TOTAL.inc(reason="timeout")

        merged = snapshot()
        assert merged.get('memory_injected_total{result="ok"}') == 1
        assert merged.get('memory_recall_degraded_total{reason="timeout"}') == 1

        reset_counters()
        assert MEMORY_INJECTED_TOTAL.total() == 0
        assert MEMORY_RECALL_DEGRADED_TOTAL.total() == 0

    def test_labels_of_is_stable(self) -> None:
        """标签渲染按字典序，便于日志比对。"""
        assert labels_of({"b": "2", "a": "1"}) == "a=1,b=2"
