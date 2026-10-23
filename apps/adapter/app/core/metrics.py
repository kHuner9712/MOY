"""指标注册表（阶段四 T4-05.1，架构 §8.1）。

**为什么用 `prometheus_client`（Apache-2.0）而不是手写**：直方图的分桶、累积、`+Inf` 桶、
文本 exposition 格式都有明确规范（`# HELP` / `# TYPE` / `_bucket{le=}` / `_sum` / `_count`），
手写极易出现"自测能过、Prometheus 抓了算不出来"的偏差——那种偏差在告警侧表现为
"规则永远不 firing"，排查成本极高。（T4-03 只埋了两个计数器，那时不引依赖是对的；
本批要上 15 条含直方图，再手写就是给自己埋雷。）

**15 条指标严格按 §8.1 命名与标签**（看板 / 告警 / runbook 都按这套写死）。

**基数纪律**（本文件最重要的一条）：

- 标签只能是**有限枚举**（见 `ALLOWED_LABELS`）；
- **禁止**把 `tenant_id` / `user_id` / `conversation_id` / `message_id` 等 ID 当标签
  （见 `FORBIDDEN_LABELS`）——取值随用户增长，一次抓取就能把 Prometheus 内存打爆；
- `route` 必须是**路由模板**（`/api/v1/model/conversations/{conversation_id}`），
  不能用原始路径（否则每个会话 ID 都是一条时间序列）；
- `status` 只保留 6 类（见 `STATUS_CLASSES`）。

**抓取路径零 DB**：`/metrics` 只做内存渲染；`task_queue_depth` 由
`app/workers/queue_depth.py` 每 10s 刷新一次，**不在渲染时查库**（否则 15s 的抓取
会把数据库持续打醒）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from prometheus_client import (
    CollectorRegistry,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client import (
    Counter as PromCounter,
)

from app.core.config import Settings, get_settings

#: 指标注册表（独立于 prometheus_client 的默认 registry：测试可控、不串味）
REGISTRY: Final[CollectorRegistry] = CollectorRegistry()

#: 允许的标签名（CI 断言：所有指标的标签必须 ⊆ 本集合）
ALLOWED_LABELS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "route",
        "status",
        "code",
        "profile",
        "outcome",
        "plugin_id",
        "capability",
        "kind",
        "result",
        "reason",
        "scope",
    }
)

#: 禁止的标签名（任何一条出现在指标定义里都是缺陷）
FORBIDDEN_LABELS: Final[frozenset[str]] = frozenset(
    {
        "tenant_id",
        "user_id",
        "conversation_id",
        "message_id",
        "task_id",
        "memory_id",
        "job_id",
    }
)

#: HTTP 状态码只保留 6 类（避免 404/405/... 每种都变成一条序列）
STATUS_CLASSES: Final[tuple[str, ...]] = ("1xx", "2xx", "3xx", "4xx", "5xx", "unknown")

#: 延迟直方图分桶（秒）：覆盖 5ms ~ 10s
_LATENCY_BUCKETS: Final[tuple[float, ...]] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

#: 首 token 分桶（秒）：首字节通常 0.1~30s，单独一套
_FIRST_TOKEN_BUCKETS: Final[tuple[float, ...]] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)


def status_class(status_code: int) -> str:
    """把 HTTP 状态码收敛成 6 类之一。

    Args:
        status_code: 状态码。

    Returns:
        str: `1xx` / `2xx` / `3xx` / `4xx` / `5xx` / `unknown`。
    """
    hundred = status_code // 100
    if 1 <= hundred <= 5:
        return f"{hundred}xx"
    return "unknown"


# ---------------------------------------------------------------------------
# 一、HTTP 与业务（中间件 / 错误处理器写入）
# ---------------------------------------------------------------------------
HTTP_REQUESTS_TOTAL = PromCounter(
    "http_requests_total",
    "HTTP 请求总数（方法 / 路由模板 / 状态类）",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒，按路由模板）",
    ("route",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
BUSINESS_ERRORS_TOTAL = PromCounter(
    "business_errors_total",
    "业务错误数（按业务码；码是有限枚举，可安全作标签）",
    ("code",),
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# 二、模型上游（M1/M2）
# ---------------------------------------------------------------------------
MODEL_UPSTREAM_CALLS_TOTAL = PromCounter(
    "model_upstream_calls_total",
    "模型上游调用数（档位 / 结果）",
    ("profile", "outcome"),
    registry=REGISTRY,
)
MODEL_UPSTREAM_DURATION_SECONDS = Histogram(
    "model_upstream_duration_seconds",
    "模型上游调用耗时（秒，按档位）",
    ("profile",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
MODEL_FIRST_TOKEN_SECONDS = Histogram(
    "model_first_token_seconds",
    "流式首 token 延迟（秒，按档位）",
    ("profile",),
    buckets=_FIRST_TOKEN_BUCKETS,
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# 三、插件任务与后台任务
# ---------------------------------------------------------------------------
TASK_QUEUE_DEPTH = Gauge(
    "task_queue_depth",
    "插件任务排队深度（10s 定时刷新；抓取时不查库）",
    registry=REGISTRY,
)
TASK_STEP_DURATION_SECONDS = Histogram(
    "task_step_duration_seconds",
    "任务单步耗时（秒，按插件）",
    ("plugin_id",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
BACKGROUND_JOBS_TOTAL = PromCounter(
    "background_jobs_total",
    "后台任务数（类型 / 结果）",
    ("kind", "status"),
    registry=REGISTRY,
)
BACKGROUND_JOB_DURATION_SECONDS = Histogram(
    "background_job_duration_seconds",
    "后台任务执行耗时（秒，按类型）",
    ("kind",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# 四、计量、记忆与成本护栏
# ---------------------------------------------------------------------------
USAGE_UNITS_TOTAL = PromCounter(
    "usage_units_total",
    "计量额度累计（按能力）",
    ("capability",),
    registry=REGISTRY,
)
MEMORY_RECALL_DURATION_SECONDS = Histogram(
    "memory_recall_duration_seconds",
    "记忆召回耗时（秒；超过 300ms 预算即降级）",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
MEMORY_EXTRACT_TOTAL = PromCounter(
    "memory_extract_total",
    "记忆抽取次数（按结果）",
    ("result",),
    registry=REGISTRY,
)
QUOTA_EXHAUSTED_EVENTS_TOTAL = PromCounter(
    "quota_exhausted_events_total",
    "额度用尽拒绝数（scope=plan|budget）",
    ("scope",),
    registry=REGISTRY,
)

#: 租户预算使用比例——**仅私有化模式注册**。
#:
#: SaaS 多租户下这个 gauge 的取值取决于"当前请求是哪个租户"，而标签又不允许放
#: `tenant_id`，注册出来就是一条**含义不明**的曲线；索性不注册（宁缺毋滥）。
TENANT_BUDGET_USED_RATIO: Gauge | None = None


def configure_deploy_mode_metrics(settings: Settings | None = None) -> Gauge | None:
    """按部署模式注册"仅私有化"的指标（幂等）。

    Args:
        settings: 可选配置实例。

    Returns:
        Gauge | None: 私有化模式返回 `tenant_budget_used_ratio`；SaaS 返回 None。
    """
    global TENANT_BUDGET_USED_RATIO
    conf = settings or get_settings()
    if conf.deploy_mode != "private":
        return None
    if TENANT_BUDGET_USED_RATIO is None:
        TENANT_BUDGET_USED_RATIO = Gauge(
            "tenant_budget_used_ratio",
            "租户月度预算使用比例（仅私有化模式）",
            registry=REGISTRY,
        )
    return TENANT_BUDGET_USED_RATIO


# ---------------------------------------------------------------------------
# 五、兼容层：T4-03 引入的轻量计数器 API
#
# 保留 `inc()/value()/total()/snapshot()` 是为了不打断 T4-03 的用例与调用点；
# 底层同时写 prometheus_client，文本 exposition 由 `render()` 统一产出。
# ---------------------------------------------------------------------------
class Counter:
    """带固定标签集的单调计数器（prometheus_client 的薄封装）。

    Attributes:
        name: 指标名。
    """

    __slots__ = ("_label_names", "_prom", "_rejected", "_values", "name")

    def __init__(self, name: str, label_names: tuple[str, ...] = ()) -> None:
        """初始化。

        Args:
            name: 指标名。
            label_names: 允许的标签名。
        """
        self.name = name
        self._label_names = frozenset(label_names)
        self._values: dict[tuple[tuple[str, str], ...], int] = {}
        self._rejected = 0
        try:
            # prometheus_client 要求"无标签"时**不传** labelnames（传空元组会报错）
            self._prom: PromCounter | None = (
                PromCounter(name, f"{name}（记忆域）", tuple(label_names), registry=REGISTRY)
                if label_names
                else PromCounter(name, f"{name}（记忆域）", registry=REGISTRY)
            )
        except ValueError:
            # 同名重复注册（测试里会反复构造同一个计数器）：只保留内存计数，
            # 文本 exposition 由首次注册的那个实例负责——避免"测试一跑就 ValueError"。
            self._prom = None

    @property
    def label_names(self) -> tuple[str, ...]:
        """允许的标签名（按字典序）。

        Returns:
            tuple[str, ...]: 标签名。
        """
        return tuple(sorted(self._label_names))

    @property
    def rejected(self) -> int:
        """被拒绝的标签组合次数。

        Returns:
            int: 次数。
        """
        return self._rejected

    def inc(self, amount: int = 1, **labels: str) -> None:
        """自增。

        Args:
            amount: 增量（必须 > 0）。
            **labels: 标签键值（只允许 `label_names` 里的键）。
        """
        if amount <= 0:
            return
        if not set(labels).issubset(self._label_names):
            self._rejected += 1
            return
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        self._values[key] = self._values.get(key, 0) + int(amount)
        # prometheus_client 的约束：有标签的计数器必须**传全**标签，无标签的不能调
        # `.labels()`。缺标签的组合只落内存计数（兼容层只服务记忆域的两个计数器，
        # 它们的调用点永远传全标签）。
        if self._prom is None:
            return
        if not self._label_names:
            self._prom.inc(amount)
            return
        if set(labels) == set(self._label_names):
            self._prom.labels(**{str(k): str(v) for k, v in labels.items()}).inc(amount)

    def value(self, **labels: str) -> int:
        """读取某个标签组合的当前值。

        Args:
            **labels: 标签键值。

        Returns:
            int: 当前值。
        """
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        return self._values.get(key, 0)

    def total(self) -> int:
        """所有标签组合的合计。

        Returns:
            int: 合计值。
        """
        return sum(self._values.values())

    def snapshot(self) -> dict[str, int]:
        """导出快照（兼容旧调用；文本 exposition 请用 `render()`）。

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


#: 注册表：指标名 → 兼容计数器
_registry: Final[dict[str, Counter]] = {}


def counter(name: str, label_names: tuple[str, ...] = ()) -> Counter:
    """取（或登记）一个兼容计数器（同名幂等）。

    Args:
        name: 指标名。
        label_names: 允许的标签名。

    Returns:
        Counter: 计数器。
    """
    existing = _registry.get(name)
    if existing is not None:
        return existing
    created = Counter(name, label_names)
    _registry[name] = created
    return created


#: 记忆注入次数（标签 `result=ok|empty|degraded`，**不要**加 user/tenant 标签）
MEMORY_INJECTED_TOTAL: Final[Counter] = counter("memory_injected_total", ("result",))

#: 记忆召回降级次数（标签 `reason=timeout|vector_unavailable`）
MEMORY_RECALL_DEGRADED_TOTAL: Final[Counter] = counter("memory_recall_degraded_total", ("reason",))


def snapshot() -> dict[str, int]:
    """导出全部兼容计数器的快照。

    Returns:
        dict[str, int]: 合并后的映射。
    """
    merged: dict[str, int] = {}
    for item in _registry.values():
        merged.update(item.snapshot())
    return merged


def reset_counters() -> None:
    """清零全部兼容计数器（测试隔离用）。"""
    for item in _registry.values():
        item.reset()


def render() -> bytes:
    """渲染 Prometheus 文本格式（`/metrics` 响应体）。

    **只读内存**：不查库、不调上游。

    Returns:
        bytes: exposition 文本。
    """
    return generate_latest(REGISTRY)


def labels_of(value: Mapping[str, str]) -> str:
    """把标签映射渲染成稳定字符串（日志与断言用）。

    Args:
        value: 标签映射。

    Returns:
        str: 形如 `a=1,b=2` 的串。
    """
    return ",".join(f"{key}={item}" for key, item in sorted(value.items()))


__all__: list[str] = [
    "ALLOWED_LABELS",
    "BACKGROUND_JOBS_TOTAL",
    "BACKGROUND_JOB_DURATION_SECONDS",
    "BUSINESS_ERRORS_TOTAL",
    "FORBIDDEN_LABELS",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUEST_DURATION_SECONDS",
    "MEMORY_EXTRACT_TOTAL",
    "MEMORY_INJECTED_TOTAL",
    "MEMORY_RECALL_DEGRADED_TOTAL",
    "MEMORY_RECALL_DURATION_SECONDS",
    "MODEL_FIRST_TOKEN_SECONDS",
    "MODEL_UPSTREAM_CALLS_TOTAL",
    "MODEL_UPSTREAM_DURATION_SECONDS",
    "QUOTA_EXHAUSTED_EVENTS_TOTAL",
    "REGISTRY",
    "STATUS_CLASSES",
    "TASK_QUEUE_DEPTH",
    "TASK_STEP_DURATION_SECONDS",
    "TENANT_BUDGET_USED_RATIO",
    "USAGE_UNITS_TOTAL",
    "Counter",
    "configure_deploy_mode_metrics",
    "counter",
    "labels_of",
    "render",
    "reset_counters",
    "snapshot",
    "status_class",
]
