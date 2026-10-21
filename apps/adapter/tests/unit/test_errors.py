"""错误码注册表与统一响应工厂单元测试（架构文档 3.0.3 / 9.2）。"""

from __future__ import annotations

import pytest

from app.core.errors import (
    ERROR_REGISTRY,
    ApiError,
    ErrorCode,
    ErrorMapper,
    error_response,
    http_status_of,
)
from app.schemas.common import CODE_SUCCESS, MESSAGE_SUCCESS, ApiResponse

pytestmark = pytest.mark.unit


def test_registry_covers_every_error_code() -> None:
    """注册表必须覆盖枚举中定义的全部错误码。"""
    assert set(ERROR_REGISTRY) == set(ErrorCode)


@pytest.mark.parametrize(
    ("code", "expected_http"),
    [
        (ErrorCode.PARAM_INVALID, 400),
        (ErrorCode.UNAUTHORIZED, 401),
        (ErrorCode.FORBIDDEN, 403),
        (ErrorCode.RESOURCE_NOT_FOUND, 404),
        (ErrorCode.CONFLICT, 409),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.IDEMPOTENT_DUPLICATE, 400),
        (ErrorCode.PAYLOAD_TOO_LARGE, 413),
        (ErrorCode.MODEL_GENERATE_FAILED, 200),
        (ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, 404),
        (ErrorCode.TOOL_TYPE_UNSUPPORTED, 400),
        (ErrorCode.USER_NOT_FOUND, 404),
        (ErrorCode.DOWNSTREAM_UNAVAILABLE, 503),
        (ErrorCode.DOWNSTREAM_TIMEOUT, 504),
        (ErrorCode.INTERNAL_ERROR, 500),
        (ErrorCode.CONFIG_ERROR, 500),
    ],
)
def test_http_status_matches_spec(code: ErrorCode, expected_http: int) -> None:
    """HTTP 状态码必须与架构文档 3.0.3 一致。"""
    assert http_status_of(code) == expected_http


def test_code_segments_are_strictly_ranged() -> None:
    """错误码分段必须落在架构文档规定的区间内。"""
    for code in ErrorCode:
        value = int(code)
        assert value == 0 or (
            10001 <= value <= 14999
            or 15001 <= value <= 15999
            or 20001 <= value <= 20999
            or 30001 <= value <= 30999
        ), f"错误码 {value} 超出注册分段"


def test_messages_are_business_language() -> None:
    """message 必须是业务语言，禁止包含技术术语。"""
    forbidden = ("error", "exception", "traceback", "stack", "token", "sql", "http", "500")
    for spec in ERROR_REGISTRY.values():
        lowered = spec.message.lower()
        for word in forbidden:
            assert word not in lowered, f"{int(spec.code)} 的 message 含技术术语: {spec.message}"


def test_api_response_ok_and_fail() -> None:
    """统一响应工厂的成功/失败分支。"""
    ok = ApiResponse[str].ok("payload")
    assert ok.code == CODE_SUCCESS
    assert ok.message == MESSAGE_SUCCESS
    assert ok.data == "payload"
    assert ok.is_success

    fail = ApiResponse[str].fail(code=20001, message="请求内容不完整，请检查后重试")
    assert fail.code == 20001
    assert fail.data is None
    assert not fail.is_success


def test_error_response_uses_registered_message() -> None:
    """按错误码构造响应时应自动取注册文案。"""
    response = error_response(ErrorCode.MODEL_GENERATE_FAILED)
    assert response.code == 10001
    assert response.message == "AI 暂时没有给出回答，请重试"
    assert response.data is None


def test_api_error_to_response_and_log_message() -> None:
    """业务异常的响应转换与日志描述。"""
    exc = ApiError(ErrorCode.RESOURCE_NOT_FOUND, internal_detail="kb_x not found")
    payload = exc.to_response()
    assert payload.code == 20004
    assert payload.message == "你访问的内容不存在或已被删除"
    assert "kb_x not found" in exc.log_message
    assert str(exc).startswith("[20004]")


def test_api_error_message_override() -> None:
    """允许同码不同场景覆盖业务文案。"""
    exc = ApiError(ErrorCode.CONFIG_ERROR, message="知识库配置缺失，请联系管理员")
    assert exc.to_response().message == "知识库配置缺失，请联系管理员"


def test_api_error_rejects_unregistered_code() -> None:
    """未注册错误码必须直接失败（注册制约束）。"""
    with pytest.raises(KeyError):
        ApiError.__init__(ApiError, 99999)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, None, ErrorCode.MODEL_GENERATE_FAILED),
        (401, None, ErrorCode.DOWNSTREAM_UNAVAILABLE),
        (404, None, ErrorCode.RESOURCE_NOT_FOUND),
        (413, None, ErrorCode.PAYLOAD_TOO_LARGE),
        (429, None, ErrorCode.RATE_LIMITED),
        (500, None, ErrorCode.DOWNSTREAM_UNAVAILABLE),
        (504, None, ErrorCode.DOWNSTREAM_TIMEOUT),
        (503, None, ErrorCode.DOWNSTREAM_UNAVAILABLE),
        (400, "context_length_exceeded", ErrorCode.MODEL_CONTEXT_OVERFLOW),
        (400, "sensitive content detected", ErrorCode.MODEL_CONTENT_BLOCKED),
        (400, "insufficient quota", ErrorCode.MODEL_QUOTA_EXHAUSTED),
        (400, "no_model_available", ErrorCode.MODEL_PROFILE_UNAVAILABLE),
        (404, "dataset not found", ErrorCode.KNOWLEDGE_BASE_NOT_FOUND),
    ],
)
def test_error_mapper_maps_downstream_status(
    status: int, body: str | None, expected: ErrorCode
) -> None:
    """下游错误必须被重写为自研错误码（形态防火墙）。"""
    code, message = ErrorMapper.to_business_error(status, body)
    assert code == expected
    assert message == ERROR_REGISTRY[expected].message


def test_error_mapper_never_leaks_downstream_body() -> None:
    """映射结果中不得出现下游原始报文。"""
    secret_body = '{"error":"upstream dify internal error","code":"500"}'
    _, message = ErrorMapper.to_business_error(500, secret_body)
    assert "dify" not in message.lower()
    assert "500" not in message


def test_error_mapper_to_api_error_keeps_status_in_diagnostics() -> None:
    """下游状态码只进内部诊断信息，不进响应。"""
    exc = ErrorMapper.to_api_error(503, "service unavailable", internal_detail="path=/v1/x")
    assert exc.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    assert "503" in (exc.internal_detail or "")
    assert "path=/v1/x" in (exc.internal_detail or "")
    assert exc.to_response().message == "服务暂时不可用，请稍后再试"
