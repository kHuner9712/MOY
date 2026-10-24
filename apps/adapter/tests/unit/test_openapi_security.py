"""对外 OpenAPI 公共层（安全方案 / 逐操作 security / X-Tenant-Id / servers）。

**为什么这些用例值得单独存在**：OpenAPI 文档不是"给人看的附件"，它是对外交付物——
客户与联调方据此生成客户端。文档里缺 `securitySchemes` 时，生成的客户端**不会带
`Authorization` 头**，联调第一步就 401；而缺 `X-Tenant-Id` 时，SaaS 多租户场景下
生成的客户端无法切换生效租户，表现为"永远读写令牌默认租户的数据"。

两个方向的错误都要拦住，所以断言既有"业务端点必须有 security"，也有
"免鉴权端点必须没有 security"——后者写错等于把下载链接与支付回调标成需要令牌，
联调方会以为它们坏了。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import (
    BEARER_SCHEME_NAME,
    TENANT_HEADER_NAME,
    artifact_token_paths,
    auth_exempt_paths,
    create_app,
)
from app.middleware.error_handler import AUTH_EXEMPT_PATHS

#: 冻结快照路径（`<repo>/docs/api/openapi.snapshot.json`，由本文件所在层级上溯 4 层）
SNAPSHOT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[4] / "docs" / "api" / "openapi.snapshot.json"
)

#: OpenAPI 中代表真实操作的键
_METHODS: Final[frozenset[str]] = frozenset(
    {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
)

#: 必须免鉴权的操作（探针 + 支付回调 + 产物下载），与中间件清单同源
_EXEMPT_OPERATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("get", "/healthz"),
    ("get", "/readyz"),
    ("post", "/api/v1/billing/payments/callback"),
    ("get", "/files/artifacts/{token}"),
)

#: 抽样校验的业务操作（覆盖三个不同域，避免只在某个 router 上成立）
_PROTECTED_OPERATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("get", "/api/v1/model/usage"),
    ("get", "/api/v1/billing/plans"),
    ("post", "/api/v1/agent/tasks"),
)


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """对外 OpenAPI 文档（模块内复用，避免每个用例重算 99 个操作）。

    Returns:
        dict[str, Any]: `create_app().openapi()` 的结果。
    """
    settings = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="openapi-security-test-secret-32-chars",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
    )
    app: FastAPI = create_app(settings)
    return app.openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """展开文档中的全部操作。

    Args:
        schema: OpenAPI 文档。

    Returns:
        list[tuple[str, str, dict[str, Any]]]: `(method, path, operation)` 列表。
    """
    result: list[tuple[str, str, dict[str, Any]]] = []
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in _METHODS and isinstance(operation, dict):
                result.append((method.lower(), path, operation))
    return result


def _operation(schema: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    """取出单个操作对象（缺失时让用例直接失败，而不是 KeyError）。

    Args:
        schema: OpenAPI 文档。
        method: HTTP 方法（小写）。
        path: 路径模板。

    Returns:
        dict[str, Any]: 操作对象。
    """
    path_item = schema.get("paths", {}).get(path)
    assert isinstance(path_item, dict), f"文档中不存在路径 {path}"
    operation = path_item.get(method)
    assert isinstance(operation, dict), f"文档中不存在操作 {method.upper()} {path}"
    return operation


def test_bearer_scheme_is_registered(schema: dict[str, Any]) -> None:
    """`components.securitySchemes.BearerAuth` 必须存在且为 HTTP Bearer/JWT。"""
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert BEARER_SCHEME_NAME in schemes, "缺少 BearerAuth 安全方案，生成的客户端不会带令牌"

    scheme = schemes[BEARER_SCHEME_NAME]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert scheme["bearerFormat"] == "JWT"


def test_servers_point_to_same_origin(schema: dict[str, Any]) -> None:
    """`servers` 必须是同源根路径（由编排层反代，客户端不该拼域名）。"""
    assert schema.get("servers") == [{"url": "/", "description": "同源部署（由编排层反向代理）"}]


@pytest.mark.parametrize(("method", "path"), _EXEMPT_OPERATIONS)
def test_exempt_operations_have_no_security(schema: dict[str, Any], method: str, path: str) -> None:
    """免鉴权操作**不得**声明 security（探针 / 支付回调 / 令牌下载）。"""
    operation = _operation(schema, method, path)
    assert "security" not in operation, f"{method.upper()} {path} 不该要求 Bearer 令牌"


@pytest.mark.parametrize(("method", "path"), _PROTECTED_OPERATIONS)
def test_protected_operations_require_bearer(
    schema: dict[str, Any], method: str, path: str
) -> None:
    """业务操作必须声明 `security: [{BearerAuth: []}]`。"""
    operation = _operation(schema, method, path)
    assert operation.get("security") == [{BEARER_SCHEME_NAME: []}]


def test_only_derived_paths_are_untagged(schema: dict[str, Any]) -> None:
    """**没有** security 的操作集合必须恰好等于「推导出的免鉴权集合 ∩ 文档路径」。

    这条用例锁住的是「豁免判定只有一处真源」：只要有人往端点里手工塞一个
    假豁免（或删掉 ``AUTH_EXEMPT_PATHS`` 的登记），这里立刻红。
    """
    documented = set(schema.get("paths", {}))
    expected = auth_exempt_paths() & documented
    actual = {
        path for _method, path, operation in _operations(schema) if "security" not in operation
    }
    assert actual == expected

    # 推导集合本身必须覆盖中间件清单（否则文档与实际鉴权行为分叉）
    assert AUTH_EXEMPT_PATHS <= auth_exempt_paths()
    # 产物下载是「令牌鉴权」，不是「免鉴权」，因此单独登记、不进中间件清单
    assert artifact_token_paths() & AUTH_EXEMPT_PATHS == frozenset()


def test_every_operation_declares_tenant_header_once(schema: dict[str, Any]) -> None:
    """每个操作都必须有且仅有一处 `X-Tenant-Id` 头参数（可省略，非必填）。"""
    for method, path, operation in _operations(schema):
        parameters = operation.get("parameters", [])
        matched = [
            parameter
            for parameter in parameters
            if isinstance(parameter, dict)
            and parameter.get("in") == "header"
            and parameter.get("name") == TENANT_HEADER_NAME
        ]
        assert len(matched) == 1, (
            f"{method.upper()} {path} 的 {TENANT_HEADER_NAME} 头参数数量为 {len(matched)}，应为 1"
        )
        assert matched[0].get("required") is False, "该头在私有化模式下可省略，不能标为必填"


def test_snapshot_is_in_sync_with_generated_schema(schema: dict[str, Any]) -> None:
    """冻结快照必须与**当前代码生成**的文档完全一致。

    **为什么这条用例必须有**：`validate_openapi.py` 的 G4 是「加性 diff」——只禁删除，
    允许新增。于是「代码加了字段但没重生成快照」永远不会被判红，快照可以单方面
    落后于代码，而下游据此生成的客户端就少一截。这条用例把「快照 == 代码」钉成断言，
    让过期在**单测层**变红，而不是靠人记得跑 `--update-snapshot`。

    失败时按提示重生成：`python deploy/validate_openapi.py --update-snapshot`（PR 需贴 diff）。
    """
    assert SNAPSHOT_PATH.is_file(), (
        f"快照文件缺失：{SNAPSHOT_PATH}；"
        "请运行 `python deploy/validate_openapi.py --update-snapshot` 生成"
    )
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # dict 比较与键序无关，list 序有语义（参数/枚举顺序），正是要校验的部分；
    # 这里再做一次 sort_keys 往返，确保与 `--update-snapshot` 的落盘方式无关。
    normalized = json.loads(json.dumps(schema, ensure_ascii=False, sort_keys=True))
    assert normalized == snapshot, (
        "OpenAPI 快照与代码生成结果不一致（快照过期或有人手改了快照）。"
        "请运行 `python deploy/validate_openapi.py --update-snapshot` 重生成并在 PR 贴出 diff"
    )
