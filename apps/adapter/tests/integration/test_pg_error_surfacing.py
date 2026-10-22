"""异常路径 A/B（P3-B19-04 收口）：PG 原始错误**对外不可见、对内必须可排障**。

为什么单独一个文件：`9a85de6` 修好 INSERT 之后，`create_task` 在正常路径上能跑通了，
但**异常路径**仍有一条永远查不出来的风险——如果哪天 SQL 再写错列名：

- **对外**：必须只看到 `30003`（不能漏列名 / SQLSTATE，否则等于把 schema 透给用户）；
- **对内**：日志里必须能看到**原始异常类型 + SQLSTATE + 列名**——否则"错误被吞"
  与"错误被正确记录"在测试里长得一模一样（这正是本文件存在的理由）。

三条判据对应主理人 2026-10-23 的 A 档定稿（不引入 `raise ... from exc` 包装，
因为 `base.py::log_exception` 已带 `exc_info`，原始错误本就没丢）。

**为什么用 spy 而不是 `structlog.testing.capture_logs()`**（`P3-B21-01` 的教训）：
`setup_logging()` 里 `cache_logger_on_first_use=True`，而 `error_handler` 的 `logger`
是**模块级对象**——它一旦被用过，就把当时的处理器链**缓存**住了；之后再 `structlog.configure`
（`capture_logs` 就是这么实现的）对**已缓存的 logger 不起作用**。
于是出现"单跑绿、全量红"：单跑时它是首次使用、恰好绑定在捕获配置上；全量跑时早被别的用例用过。
**改成 spy 直接替换模块级 `logger`**，与 structlog 的缓存/配置彻底解耦——
这与 `c5`（用 spy 包住 `sanitize` 断"是否被调用过"）是同一种做法。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.main import create_app
from app.middleware import error_handler as error_handler_module

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

TEST_JWT_SECRET = "unit-test-secret"

#: `plugin_task` 上**不存在**的列——用它制造真实的 `UndefinedColumnError`（SQLSTATE 42703）
#: 该列在 B19 曾被误当存储列（`queue_position`），是本轮 P0 的原始触发点。
MISSING_COLUMN = "queue_position"


def _settings() -> Settings:
    """与 `tests/conftest.py::settings` 同构的桩模式配置。"""
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret=TEST_JWT_SECRET,
        ratelimit_enabled=False,
    )


async def _real_undefined_column_error(dsn: str) -> BaseException:
    """连真库制造一次 `UndefinedColumnError`，返回**原始异常对象**。

    Args:
        dsn: 测试库连接串。

    Returns:
        BaseException: asyncpg 抛出的原始异常（带 `sqlstate` 与列名）。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            f"SELECT {MISSING_COLUMN} FROM plugin_task LIMIT 1"  # noqa: S608 - 固定字面量
        )
    except BaseException as exc:
        return exc
    finally:
        await connection.close()
    raise AssertionError("期望触发 UndefinedColumnError，但语句意外成功了")


class _LoggerSpy:
    """记录 `logger.error(...)` 调用，替代 structlog 的模块级 logger。

    Attributes:
        calls: `(event, kwargs)` 列表。
    """

    def __init__(self) -> None:
        """初始化空记录。"""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def error(self, event: str, **kwargs: Any) -> None:
        """记录一次 error 级日志。

        Args:
            event: structlog 事件名。
            **kwargs: 结构化字段（`exc_info` / `exception_type` / `sqlstate` …）。
        """
        self.calls.append((event, kwargs))

    def __getattr__(self, _name: str) -> Any:
        """其它级别（info/warning…）返回空实现，本用例不关心。"""

        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _noop


def test_pg_error_hidden_from_client_but_traceable_in_logs(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """① 对外 30003 不泄露；② 日志含类型 + SQLSTATE + 列名；③ 只记一次。"""
    err = asyncio.run(_real_undefined_column_error(pg_dsn))

    # 先确认探针拿到的是**真的 PG 错误**（否则后面的断言没有意义）
    assert type(err).__name__ == "UndefinedColumnError"
    assert getattr(err, "sqlstate", None) == "42703"
    assert MISSING_COLUMN in str(err)

    # spy 替换模块级 logger：与 structlog 的 `cache_logger_on_first_use` 解耦
    spy = _LoggerSpy()
    monkeypatch.setattr(error_handler_module, "logger", spy)

    app = create_app(_settings())

    @app.get("/__qa__/pg-boom")
    async def _boom() -> None:
        raise err

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__qa__/pg-boom")

    # ① 对外：统一 30003，且**不得**泄露列名 / SQLSTATE
    assert response.json()["code"] == int(ErrorCode.INTERNAL_ERROR)
    body = response.text
    assert MISSING_COLUMN not in body, "响应体泄露了列名（等于把 schema 透给用户）"
    assert "42703" not in body, "响应体泄露了 SQLSTATE"

    # ②-a **反向控制**：先证明"处理器确实记录了、且带异常本体"——
    #     若这条红，说明红的原因不是缺 SQLSTATE，而是根本没记日志，后面的断言不可归因。
    assert spy.calls, "未捕获异常处理器没有调用 logger.error（错误被吞）"
    event, fields = spy.calls[0]
    assert event == "unhandled_exception"
    assert fields.get("exc_info") is err, "日志未携带原始异常对象（排障时看不到 traceback）"

    # ②-b 排障三要素：原始类型 + 列名 + SQLSTATE
    dumped = json.dumps({k: str(v) for k, v in fields.items()}, ensure_ascii=False)
    assert fields.get("exception_type") == "UndefinedColumnError", "日志缺原始异常类型"
    assert MISSING_COLUMN in dumped, "日志缺列名——现场无法定位是哪一列写错了"
    assert fields.get("sqlstate") == "42703", (
        "日志缺 SQLSTATE——无法区分「列不存在 42703」与「权限不足 42501」/「连接异常 08006」"
    )

    # ③ 只记一次（没有重复告警式重入）
    assert len(spy.calls) == 1, f"同一次异常被记录了 {len(spy.calls)} 次"
