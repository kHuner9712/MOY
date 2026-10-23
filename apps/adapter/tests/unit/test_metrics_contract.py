"""指标契约（阶段四 T4-05.1，架构 §8.1）。

四条判据（都是"错了没人立刻发现"的类型）：

1. **15 条指标齐备且名字逐字一致**——看板 / 告警 / runbook 都按这套写死，
   少一条就是"看板永远空白"，多一条是"没人知道该看哪个"；
2. **标签 ⊆ 白名单**，且**不含 ID 类标签**（`tenant_id` / `user_id` / …）——
   基数爆炸是监控最典型的自伤方式，一次抓取就能把 Prometheus 打挂；
3. **`route` 用路由模板**：带真实 ID 的路径会让每条会话变成一条时间序列；
4. **抓取路径零 DB**：`/metrics` 渲染只读内存。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.metrics import (
    ALLOWED_LABELS,
    BACKGROUND_JOB_DURATION_SECONDS,
    BACKGROUND_JOBS_TOTAL,
    BUSINESS_ERRORS_TOTAL,
    FORBIDDEN_LABELS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    MEMORY_EXTRACT_TOTAL,
    MEMORY_RECALL_DURATION_SECONDS,
    MODEL_FIRST_TOKEN_SECONDS,
    MODEL_UPSTREAM_CALLS_TOTAL,
    MODEL_UPSTREAM_DURATION_SECONDS,
    QUOTA_EXHAUSTED_EVENTS_TOTAL,
    REGISTRY,
    STATUS_CLASSES,
    TASK_QUEUE_DEPTH,
    TASK_STEP_DURATION_SECONDS,
    USAGE_UNITS_TOTAL,
    render,
    status_class,
)
from app.main import create_app
from app.middleware.metrics import route_template

pytestmark = pytest.mark.unit

#: 架构 §8.1 的 15 条指标名（逐字）
EXPECTED_METRICS: frozenset[str] = frozenset(
    {
        "http_requests_total",
        "http_request_duration_seconds",
        "business_errors_total",
        "model_upstream_calls_total",
        "model_upstream_duration_seconds",
        "model_first_token_seconds",
        "task_queue_depth",
        "task_step_duration_seconds",
        "usage_units_total",
        "background_jobs_total",
        "background_job_duration_seconds",
        "memory_recall_duration_seconds",
        "memory_extract_total",
        "quota_exhausted_events_total",
    }
)


def _collectors() -> Iterator[Any]:
    """遍历注册表里的所有 collector。

    Yields:
        Any: 单个 collector。
    """
    yield from REGISTRY.collect()


class TestMetricContract:
    """指标集合与标签集合。"""

    def test_all_section_8_1_metrics_present(self) -> None:
        """§8.1 的指标（除私有化专属的 budget gauge）都在注册表里。

        用 `# HELP` 行判定而不是 `collect()`：带标签的计数器在**还没有子序列**时
        `collect()` 返回空，但 exposition 里仍有 HELP/TYPE——判据要看"注册了没有"。
        """
        text = render().decode()
        missing = [name for name in EXPECTED_METRICS if f"# HELP {name} " not in text]
        assert not missing, f"缺失指标：{sorted(missing)}（看板会永远空白）"

    def test_labels_are_whitelisted(self) -> None:
        """所有标签 ⊆ `ALLOWED_LABELS`，且不含任何 ID 类标签。"""
        for metric in _collectors():
            for sample in metric.samples:
                # 跳过 prometheus_client 自带的固定标签（le / quantile 等）
                label_names = set(sample.labels) - {"le", "quantile"}
                assert label_names <= ALLOWED_LABELS, f"{metric.name} 出现未登记标签：{label_names}"
                assert not (label_names & FORBIDDEN_LABELS), (
                    f"{metric.name} 把 ID 当标签（基数会爆）：{label_names & FORBIDDEN_LABELS}"
                )

    def test_forbidden_and_allowed_do_not_overlap(self) -> None:
        """白名单与黑名单不相交（否则白名单形同虚设）。"""
        assert not (ALLOWED_LABELS & FORBIDDEN_LABELS)

    def test_status_buckets(self) -> None:
        """状态码收敛成 6 类。"""
        assert status_class(200) == "2xx"
        assert status_class(404) == "4xx"
        assert status_class(503) == "5xx"
        assert status_class(302) == "3xx"
        assert status_class(100) == "1xx"
        assert status_class(999) == "unknown"
        assert set(STATUS_CLASSES) == {"1xx", "2xx", "3xx", "4xx", "5xx", "unknown"}

    def test_histograms_are_histograms(self) -> None:
        """直方图必须是 `Histogram`（有 `_bucket` / `_sum` / `_count` 三条样本）。"""
        HTTP_REQUEST_DURATION_SECONDS.labels(route="/x").observe(0.05)
        MODEL_UPSTREAM_DURATION_SECONDS.labels(profile="general").observe(0.05)
        MODEL_FIRST_TOKEN_SECONDS.labels(profile="general").observe(0.05)
        TASK_STEP_DURATION_SECONDS.labels(plugin_id="office_doc").observe(0.05)
        BACKGROUND_JOB_DURATION_SECONDS.labels(kind="memory_extract").observe(0.05)
        MEMORY_RECALL_DURATION_SECONDS.observe(0.05)
        text = render().decode()
        assert "http_request_duration_seconds_bucket" in text
        assert "http_request_duration_seconds_sum" in text
        assert "http_request_duration_seconds_count" in text

    def test_counters_and_gauge_types(self) -> None:
        """计数器与 gauge 的 `_total` / 裸值形态正确。"""
        HTTP_REQUESTS_TOTAL.labels(method="GET", route="/x", status="2xx").inc()
        BUSINESS_ERRORS_TOTAL.labels(code="20003").inc()
        MODEL_UPSTREAM_CALLS_TOTAL.labels(profile="general", outcome="ok").inc()
        USAGE_UNITS_TOTAL.labels(capability="chat").inc()
        BACKGROUND_JOBS_TOTAL.labels(kind="memory_extract", status="succeeded").inc()
        MEMORY_EXTRACT_TOTAL.labels(result="ok").inc()
        QUOTA_EXHAUSTED_EVENTS_TOTAL.labels(scope="plan").inc()
        TASK_QUEUE_DEPTH.set(3)

        text = render().decode()
        assert 'http_requests_total{method="GET",route="/x",status="2xx"}' in text
        assert "business_errors_total{" in text
        assert "task_queue_depth 3.0" in text


class TestMetricsEndpoint:
    """`/metrics` 端点本身。"""

    def test_endpoint_returns_prometheus_text_without_auth(self) -> None:
        """免鉴权、返回标准文本、含 `# HELP`。"""
        app = create_app(
            Settings(
                env="test",
                business_store="memory",
                auth_mode="stub",
                jwt_secret="x",
                ratelimit_enabled=False,
            )
        )
        with TestClient(app) as client:
            response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "# HELP http_requests_total" in response.text
        assert "# TYPE http_requests_total counter" in response.text

    def test_endpoint_is_outside_api_prefix(self) -> None:
        """路径是 `/metrics`（不在 `/api/v1` 下）→ 不计入端点基线。

        用"实际请求"判定而不是翻 `app.routes`：`include_router` 后路由挂在内部 router 上，
        直接遍历顶层 `routes` 看不到（也看不到它是否误挂到 `/api/v1`）。
        """
        app = create_app(
            Settings(
                env="test",
                business_store="memory",
                auth_mode="stub",
                jwt_secret="x",
                ratelimit_enabled=False,
            )
        )
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 200
            assert client.get("/api/v1/metrics").status_code == 404
            # OpenAPI 里不应出现（`include_in_schema=False`）→ 不影响端点基线
            spec = client.get("/openapi.json").json()
        assert "/metrics" not in spec["paths"]

    def test_scrape_path_does_not_touch_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """抓取路径零 DB：渲染时不得调用引擎/仓储。"""
        calls: list[str] = []

        def _boom(*_: Any, **__: Any) -> Any:
            calls.append("db")
            raise AssertionError("/metrics 渲染时不应访问数据库")

        monkeypatch.setattr("app.db.engine.get_engine", _boom)
        monkeypatch.setattr("app.db.session.get_session_factory", _boom)

        app = create_app(
            Settings(
                env="test",
                business_store="memory",
                auth_mode="stub",
                jwt_secret="x",
                ratelimit_enabled=False,
            )
        )
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 200

        assert calls == []

    def test_route_template_prefers_route_pattern(self) -> None:
        """`route_template` 取的是路由模板而不是原始路径。"""

        class _Route:
            path = "/api/v1/model/conversations/{conversation_id}"

        class _Request:
            scope: ClassVar[dict[str, Any]] = {"route": _Route()}

        assert route_template(_Request()) == "/api/v1/model/conversations/{conversation_id}"  # type: ignore[arg-type]

    def test_metrics_middleware_skips_self(self) -> None:
        """`/metrics` 自身的抓取不计入指标（否则曲线被自己的噪声主导）。

        ⚠ 这里原来还有一句 `assert "/metrics" in MetricsMiddleware.__module__ or True`——
        `or True` 让它**恒为真**，是"挂上一个永远绿的断言"，比没有断言更危险
        （有人会以为这条已被覆盖）。已删除：真正的判据是下面的 `EXCLUDED_PATHS`，
        且顺手把"必须含 `/metrics`、且**不得**把业务路径也排除掉"一起钉住
        （后者是反向风险：有人为了让曲线干净，把 `/api/v1/**` 也塞进排除集）。
        """
        from app.middleware.metrics import EXCLUDED_PATHS

        assert "/metrics" in EXCLUDED_PATHS
        assert not [path for path in EXCLUDED_PATHS if path.startswith("/api/v1")], (
            "业务路径不得被排除在指标之外"
        )
