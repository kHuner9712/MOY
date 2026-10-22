"""异常路径 A/B（P3-B19-04 收口）：PG 原始错误**对外不可见、对内必须可排障**。

为什么单独一个文件：`9a85de6` 修好 INSERT 之后，`create_task` 在正常路径上能跑通了，
但**异常路径**仍有一条永远查不出来的风险——如果哪天 SQL 再写错列名：

- **对外**：必须只看到 `30003`（不能漏列名 / SQLSTATE，否则等于把 schema 透给用户）；
- **对内**：日志里必须能看到**原始异常类型 + SQLSTATE + 列名**——否则"错误被吞"
  与"错误被正确记录"在测试里长得一模一样（这正是本文件存在的理由）。

三条判据对应主理人 2026-10-23 的 A 档定稿（不引入 `raise ... from exc` 包装，
因为 `base.py::log_exception` 已带 `exc_info`，原始错误本就没丢）。
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest
import structlog
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.main import create_app

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
    except BaseException as exc:  # noqa: BLE001 - 探针：需要拿到原始异常本体
        return exc
    finally:
        await connection.close()
    raise AssertionError("期望触发 UndefinedColumnError，但语句意外成功了")


def test_pg_error_hidden_from_client_but_traceable_in_logs(pg_dsn: str) -> None:
    """① 对外 30003 不泄露；② 日志含原始类型 + SQLSTATE + 列名；③ 处理器不中断。"""
    err = asyncio.run(_real_undefined_column_error(pg_dsn))

    # 先确认探针拿到的是**真的 PG 错误**（否则后面的断言没有意义）
    assert type(err).__name__ == "UndefinedColumnError"
    assert getattr(err, "sqlstate", None) == "42703"
    assert MISSING_COLUMN in str(err)

    app = create_app(_settings())

    @app.get("/__qa__/pg-boom")
    async def _boom() -> None:
        raise err

    with structlog.testing.capture_logs() as logs:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/__qa__/pg-boom")

    # ① 对外：统一 30003，且**不得**泄露列名 / SQLSTATE
    assert response.json()["code"] == int(ErrorCode.INTERNAL_ERROR)
    body = response.text
    assert MISSING_COLUMN not in body, "响应体泄露了列名（等于把 schema 透给用户）"
    assert "42703" not in body, "响应体泄露了 SQLSTATE"

    # ② 对内：日志必须能排障——原始异常类型 + SQLSTATE + 列名
    entries = [entry for entry in logs if entry.get("event") == "unhandled_exception"]
    assert entries, "未捕获异常处理器没有记录 unhandled_exception（错误被吞）"
    entry = entries[0]
    dumped = json.dumps(entry, ensure_ascii=False, default=str)

    # ②-a **反向控制**：先断言"日志确实输出了、且含异常消息"——这两条必须绿。
    #     若它们红了，说明"红的原因"不是缺 SQLSTATE（而是日志根本没出/类型不符），
    #     那样下面的红就不可归因——与 P3-B19-04 的"改坏即红 + 改回即绿"同一条纪律。
    assert entry.get("exc_info"), "反向控制失败：日志没有 exc_info（红的原因会是「日志没输出」而非缺 SQLSTATE）"
    assert entry.get("exception_type") == "UndefinedColumnError", "反向控制失败：日志缺原始异常类型"
    assert MISSING_COLUMN in dumped, "反向控制失败：日志里没有异常消息文本"

    # ②-b 待补项：SQLSTATE 必须进日志，否则「列不存在 42703 / 权限不足 42501 / 连接异常 08006」无法区分
    assert "42703" in dumped, "日志缺 SQLSTATE——无法区分「列不存在」与「权限/连接」类错误"

    # ③ 处理器不中断：有响应、且只记录一次（没有重复告警式重入）
    assert len(entries) == 1, f"同一次异常被记录了 {len(entries)} 次"
