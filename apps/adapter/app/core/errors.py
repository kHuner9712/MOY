"""统一错误码注册表与业务异常（架构文档 3.0.3，错误码注册制）。

铁律（架构文档 3.0.4 降级条 + 9.2）：
- 错误码**必须**先在架构文档登记，再进代码；本模块是唯一登记入口；
- `message` 是终端用户唯一可见文本，一律使用业务语言；
- **严禁**把 `code`、堆栈、下游原始错误文本透出给用户——技术细节只进日志；
- 新增错误码时同步更新架构文档 3.0.3 与前端 `business-errors.ts` 兜底映射。

错误码分段：
- 0：成功；
- 10001-14999：领域业务错误（模型 / RAG / Agent / 工具 / 权限）；
- 15001-15999：计费与订阅错误（阶段二 B09，架构文档 §7.1）；
- 20000-20999：通用业务错误；
- 30000-30999：系统错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Final

from app.schemas.common import ApiResponse


class ErrorCode(IntEnum):
    """统一错误码枚举（与架构文档 3.0.3 逐条对应）。"""

    # ---------------- 通用业务错误 20000-20999 ----------------
    PARAM_INVALID = 20001
    UNAUTHORIZED = 20002
    FORBIDDEN = 20003
    RESOURCE_NOT_FOUND = 20004
    CONFLICT = 20005
    RATE_LIMITED = 20006
    IDEMPOTENT_DUPLICATE = 20007
    PAYLOAD_TOO_LARGE = 20008

    # ---------------- 模型服务域 10001-10099 ----------------
    MODEL_GENERATE_FAILED = 10001
    MODEL_CONTEXT_OVERFLOW = 10002
    MODEL_CONTENT_BLOCKED = 10003
    MODEL_PROFILE_UNAVAILABLE = 10004
    MODEL_QUOTA_EXHAUSTED = 10005

    # ---------------- RAG 服务域 11001-11099 ----------------
    KNOWLEDGE_BASE_NOT_FOUND = 11001
    DOCUMENT_FORMAT_UNSUPPORTED = 11002
    DOCUMENT_TOO_LARGE = 11003
    DOCUMENT_INDEXING = 11004
    RETRIEVAL_FAILED = 11005
    DOCUMENT_STATE_CONFLICT = 11006
    #: 库级凭据解析失败（阶段二 D-10）。**严禁**回落全局凭据，
    #: 否则 A 库的请求会打到 B 库的应用上（跨库数据泄露）。
    KB_CREDENTIAL_UNAVAILABLE = 11007

    # ---------------- Agent 服务域 12001-12099 ----------------
    TASK_NOT_FOUND = 12001
    TASK_EXECUTION_FAILED = 12002
    TASK_CANCELLED = 12003
    TASK_TIMEOUT = 12004
    TASK_TOOL_NOT_GRANTED = 12005

    # ---------------- 工具服务域 13001-13099 ----------------
    TOOL_TYPE_UNSUPPORTED = 13001
    TOOL_NOT_CONNECTED = 13002
    TOOL_AUTH_EXPIRED = 13003
    TOOL_UNRESPONSIVE = 13004
    TOOL_OPERATION_UNSUPPORTED = 13005
    TOOL_THIRD_PARTY_ERROR = 13006

    # ---------------- 用户权限服务域 14001-14099 ----------------
    USER_NOT_FOUND = 14001
    ORGANIZATION_NOT_FOUND = 14002
    ROLE_NOT_FOUND = 14003
    PERMISSION_NOT_DEFINED = 14004
    #: 部门不存在（阶段二 B08，架构文档 §7.1）
    DEPARTMENT_NOT_FOUND = 14005
    #: 部门层级/删除冲突（同码多文案：超 5 层 / 移动到自身子树 / 仍有成员或子部门）
    DEPARTMENT_STRUCTURE_CONFLICT = 14006
    #: 企业所有者保护（不得移除/降级 owner）
    OWNER_PROTECTED = 14007
    #: 同级或更高级成员保护 / 操作自己
    MEMBER_MODIFICATION_FORBIDDEN = 14008

    # ---------------- 计费与订阅域 15001-15099（阶段二 B09，架构文档 §7.1） ----------------
    #: 套餐能力未开通（BL3 BL6）
    PLAN_FEATURE_UNAVAILABLE = 15001
    #: 套餐不可购（已下架，BL3 BL6）
    PLAN_NOT_PURCHASABLE = 15002
    #: 席位不足（BL4；U4 复用调用）
    SEATS_EXHAUSTED = 15003
    #: 重复下单：已有待支付订单（BL3 BL6）
    PENDING_ORDER_EXISTS = 15004
    #: 降级将在当前周期结束后生效（**非阻断提示码**，BL3）
    PLAN_CHANGE_PENDING = 15005
    #: 额度用尽（软限制，不停服；M1/M2/R3/A1）
    QUOTA_EXHAUSTED = 15006
    #: 支付未完成，请重新支付（BL6 BL8）
    PAYMENT_INCOMPLETE = 15007
    #: 重复核销 / 重复回调（BL10 OPS5）
    ORDER_ALREADY_PROCESSED = 15008
    #: 核销金额校验失败（OPS5）
    ORDER_AMOUNT_MISMATCH = 15009

    # ---------------- 系统错误 30001-30099 ----------------
    DOWNSTREAM_UNAVAILABLE = 30001
    DOWNSTREAM_TIMEOUT = 30002
    INTERNAL_ERROR = 30003
    CONFIG_ERROR = 30004


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """错误码元信息。

    Attributes:
        code: 错误码。
        http_status: 传输层 HTTP 状态码。
        message: 面向终端用户的业务语言文案。
        internal: 内部含义，仅进日志，禁止外泄。
    """

    code: ErrorCode
    http_status: int
    message: str
    internal: str


# ---------------------------------------------------------------------------
# 错误码注册表：message 一律取自架构文档 3.0.3，不得随意改写为技术语言
# ---------------------------------------------------------------------------
ERROR_REGISTRY: Final[dict[ErrorCode, ErrorSpec]] = {
    # 通用业务错误
    ErrorCode.PARAM_INVALID: ErrorSpec(
        ErrorCode.PARAM_INVALID, 400, "请求内容不完整，请检查后重试", "参数校验失败"
    ),
    ErrorCode.UNAUTHORIZED: ErrorSpec(
        ErrorCode.UNAUTHORIZED, 401, "登录状态已失效，请重新登录", "令牌缺失/过期/签名失败"
    ),
    ErrorCode.FORBIDDEN: ErrorSpec(
        ErrorCode.FORBIDDEN, 403, "当前账号没有该操作的权限", "权限点校验失败"
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorSpec(
        ErrorCode.RESOURCE_NOT_FOUND, 404, "你访问的内容不存在或已被删除", "资源不存在"
    ),
    ErrorCode.CONFLICT: ErrorSpec(
        ErrorCode.CONFLICT, 409, "操作冲突，请刷新后重试", "状态冲突/幂等冲突"
    ),
    ErrorCode.RATE_LIMITED: ErrorSpec(
        ErrorCode.RATE_LIMITED, 429, "当前使用人数较多，请稍后再试", "触发限流"
    ),
    ErrorCode.IDEMPOTENT_DUPLICATE: ErrorSpec(
        ErrorCode.IDEMPOTENT_DUPLICATE, 400, "重复的请求已被忽略", "幂等键命中"
    ),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorSpec(
        ErrorCode.PAYLOAD_TOO_LARGE, 413, "文件超出大小限制（上限 50MB）", "请求体超限"
    ),
    # 模型服务域
    ErrorCode.MODEL_GENERATE_FAILED: ErrorSpec(
        ErrorCode.MODEL_GENERATE_FAILED, 200, "AI 暂时没有给出回答，请重试", "生成失败"
    ),
    ErrorCode.MODEL_CONTEXT_OVERFLOW: ErrorSpec(
        ErrorCode.MODEL_CONTEXT_OVERFLOW,
        200,
        "当前对话内容过长，请新建会话或精简内容",
        "上下文超模型窗口",
    ),
    ErrorCode.MODEL_CONTENT_BLOCKED: ErrorSpec(
        ErrorCode.MODEL_CONTENT_BLOCKED, 200, "该内容暂不支持处理，请调整后重试", "内容安全拦截"
    ),
    ErrorCode.MODEL_PROFILE_UNAVAILABLE: ErrorSpec(
        ErrorCode.MODEL_PROFILE_UNAVAILABLE,
        200,
        "智能助手繁忙，请稍后再试",
        "所选能力档位无可用模型",
    ),
    ErrorCode.MODEL_QUOTA_EXHAUSTED: ErrorSpec(
        ErrorCode.MODEL_QUOTA_EXHAUSTED, 200, "当前套餐可用次数不足", "额度不足（计费，S2）"
    ),
    # RAG 服务域
    ErrorCode.KNOWLEDGE_BASE_NOT_FOUND: ErrorSpec(
        ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, 404, "知识库不存在或已被删除", "知识库不存在"
    ),
    ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED: ErrorSpec(
        ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED, 400, "暂不支持该格式的文档", "文档格式不支持"
    ),
    ErrorCode.DOCUMENT_TOO_LARGE: ErrorSpec(
        ErrorCode.DOCUMENT_TOO_LARGE, 400, "文档内容过多，请拆分后上传", "文档解析/大小超限"
    ),
    ErrorCode.DOCUMENT_INDEXING: ErrorSpec(
        ErrorCode.DOCUMENT_INDEXING, 200, "文档还在处理中，请稍候查看", "索引未完成"
    ),
    ErrorCode.RETRIEVAL_FAILED: ErrorSpec(
        ErrorCode.RETRIEVAL_FAILED, 200, "检索服务暂时不可用，请重试", "检索失败"
    ),
    ErrorCode.DOCUMENT_STATE_CONFLICT: ErrorSpec(
        ErrorCode.DOCUMENT_STATE_CONFLICT, 200, "文档正在处理，无法重复操作", "索引状态冲突"
    ),
    ErrorCode.KB_CREDENTIAL_UNAVAILABLE: ErrorSpec(
        ErrorCode.KB_CREDENTIAL_UNAVAILABLE,
        200,
        "该知识库暂时无法访问，请联系管理员",
        "库级凭据解析失败（缺失/失效/解密失败），已禁止回落全局凭据",
    ),
    # Agent 服务域
    ErrorCode.TASK_NOT_FOUND: ErrorSpec(
        ErrorCode.TASK_NOT_FOUND, 404, "任务不存在或已过期", "任务不存在"
    ),
    ErrorCode.TASK_EXECUTION_FAILED: ErrorSpec(
        ErrorCode.TASK_EXECUTION_FAILED, 200, "任务执行未能完成，可查看详情后重试", "执行失败"
    ),
    ErrorCode.TASK_CANCELLED: ErrorSpec(ErrorCode.TASK_CANCELLED, 200, "任务已取消", "终态冲突"),
    ErrorCode.TASK_TIMEOUT: ErrorSpec(
        ErrorCode.TASK_TIMEOUT, 200, "任务执行超时，请重试", "单步/整体超时"
    ),
    ErrorCode.TASK_TOOL_NOT_GRANTED: ErrorSpec(
        ErrorCode.TASK_TOOL_NOT_GRANTED, 200, "该步骤需要的工具尚未连接", "依赖工具未授权"
    ),
    # 工具服务域
    ErrorCode.TOOL_TYPE_UNSUPPORTED: ErrorSpec(
        ErrorCode.TOOL_TYPE_UNSUPPORTED, 400, "暂不支持该类工具", "工具类型未注册"
    ),
    ErrorCode.TOOL_NOT_CONNECTED: ErrorSpec(
        ErrorCode.TOOL_NOT_CONNECTED,
        200,
        "该工具尚未连接，请先在设置中完成授权",
        "连接不存在/未授权",
    ),
    ErrorCode.TOOL_AUTH_EXPIRED: ErrorSpec(
        ErrorCode.TOOL_AUTH_EXPIRED, 200, "工具授权已过期，请重新连接", "授权过期"
    ),
    ErrorCode.TOOL_UNRESPONSIVE: ErrorSpec(
        ErrorCode.TOOL_UNRESPONSIVE, 200, "工具暂时没有响应，请稍后再试", "下游调用失败"
    ),
    ErrorCode.TOOL_OPERATION_UNSUPPORTED: ErrorSpec(
        ErrorCode.TOOL_OPERATION_UNSUPPORTED, 400, "该工具不支持此操作", "操作不在工具能力清单"
    ),
    ErrorCode.TOOL_THIRD_PARTY_ERROR: ErrorSpec(
        ErrorCode.TOOL_THIRD_PARTY_ERROR, 200, "第三方工具返回了异常结果", "下游业务错误"
    ),
    # 用户权限服务域
    ErrorCode.USER_NOT_FOUND: ErrorSpec(ErrorCode.USER_NOT_FOUND, 404, "用户不存在", "用户不存在"),
    ErrorCode.ORGANIZATION_NOT_FOUND: ErrorSpec(
        ErrorCode.ORGANIZATION_NOT_FOUND, 404, "团队不存在", "组织不存在"
    ),
    ErrorCode.ROLE_NOT_FOUND: ErrorSpec(ErrorCode.ROLE_NOT_FOUND, 404, "角色不存在", "角色不存在"),
    ErrorCode.PERMISSION_NOT_DEFINED: ErrorSpec(
        ErrorCode.PERMISSION_NOT_DEFINED, 400, "权限点未定义", "权限点未注册"
    ),
    ErrorCode.DEPARTMENT_NOT_FOUND: ErrorSpec(
        ErrorCode.DEPARTMENT_NOT_FOUND, 404, "该部门不存在或已被删除", "部门不存在"
    ),
    ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT: ErrorSpec(
        ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT,
        400,
        "该部门下仍有成员或子部门，请先迁移后再删除",
        "部门层级/删除冲突（同码多文案由 ApiError(message=...) 区分）",
    ),
    ErrorCode.OWNER_PROTECTED: ErrorSpec(
        ErrorCode.OWNER_PROTECTED, 400, "不能移除企业所有者，请先转移所有权", "所有者保护"
    ),
    ErrorCode.MEMBER_MODIFICATION_FORBIDDEN: ErrorSpec(
        ErrorCode.MEMBER_MODIFICATION_FORBIDDEN,
        403,
        "你没有权限修改该成员",
        "同级或更高级成员保护 / 操作自己",
    ),
    # 计费与订阅域（文案取自架构文档 §7.1，**禁止**改写为技术语言）
    ErrorCode.PLAN_FEATURE_UNAVAILABLE: ErrorSpec(
        ErrorCode.PLAN_FEATURE_UNAVAILABLE,
        403,
        "当前套餐不支持该功能，升级后即可使用",
        "套餐能力未开通",
    ),
    ErrorCode.PLAN_NOT_PURCHASABLE: ErrorSpec(
        ErrorCode.PLAN_NOT_PURCHASABLE,
        400,
        "该套餐已下架，请选择其他套餐",
        "套餐不可购（状态非 available）",
    ),
    ErrorCode.SEATS_EXHAUSTED: ErrorSpec(
        ErrorCode.SEATS_EXHAUSTED,
        400,
        "成员席位已用满，请先增加席位",
        "席位不足（邀请/加成员/减席越界）",
    ),
    ErrorCode.PENDING_ORDER_EXISTS: ErrorSpec(
        ErrorCode.PENDING_ORDER_EXISTS,
        409,
        "当前账户有未完成订单，请先完成支付",
        "重复下单：同租户已有待支付订单",
    ),
    ErrorCode.PLAN_CHANGE_PENDING: ErrorSpec(
        ErrorCode.PLAN_CHANGE_PENDING,
        200,
        "降级将在当前周期结束后生效",
        "变更生效时点（非阻断提示码，走 data.notice_code 下发）",
    ),
    ErrorCode.QUOTA_EXHAUSTED: ErrorSpec(
        ErrorCode.QUOTA_EXHAUSTED,
        200,
        "本月额度已用完，当前为常规模式，升级套餐可恢复完整能力",
        "额度用尽（软限制，不停服）",
    ),
    ErrorCode.PAYMENT_INCOMPLETE: ErrorSpec(
        ErrorCode.PAYMENT_INCOMPLETE, 200, "支付未完成，请重新支付", "支付失败/超时"
    ),
    ErrorCode.ORDER_ALREADY_PROCESSED: ErrorSpec(
        ErrorCode.ORDER_ALREADY_PROCESSED,
        409,
        "该订单已处理，请勿重复操作",
        "重复核销/重复回调的业务侧冲突",
    ),
    ErrorCode.ORDER_AMOUNT_MISMATCH: ErrorSpec(
        ErrorCode.ORDER_AMOUNT_MISMATCH,
        400,
        "订单金额与应付金额不一致，请核对后重试",
        "核销金额校验失败",
    ),
    # 系统错误
    ErrorCode.DOWNSTREAM_UNAVAILABLE: ErrorSpec(
        ErrorCode.DOWNSTREAM_UNAVAILABLE, 503, "服务暂时不可用，请稍后再试", "下游不可用"
    ),
    ErrorCode.DOWNSTREAM_TIMEOUT: ErrorSpec(
        ErrorCode.DOWNSTREAM_TIMEOUT, 504, "服务响应超时，请重试", "下游超时"
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(
        ErrorCode.INTERNAL_ERROR, 500, "系统繁忙，请稍后再试", "适配层内部异常"
    ),
    ErrorCode.CONFIG_ERROR: ErrorSpec(
        ErrorCode.CONFIG_ERROR, 500, "服务配置异常，请联系管理员", "配置缺失/错误"
    ),
}


class ApiError(Exception):
    """业务异常基类。

    抛出后由统一异常处理器转换为 `{code, message, data}` 响应，
    `internal` 与原始异常仅写入日志，不进入响应体。
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        data: Any = None,
        internal_detail: str | None = None,
    ) -> None:
        """初始化业务异常。

        Args:
            code: 错误码。
            message: 覆盖默认业务文案（仅用于同码不同场景的细分文案）。
            data: 附加业务数据。
            internal_detail: 内部诊断信息，**只进日志**。

        Raises:
            KeyError: 错误码未在注册表中登记（注册制约束）。
        """
        spec = ERROR_REGISTRY.get(code)
        if spec is None:  # pragma: no cover - 注册表缺失属编码错误
            raise KeyError(f"错误码未注册: {code}")
        self.code: ErrorCode = spec.code
        self.http_status: int = spec.http_status
        self.message: str = message or spec.message
        self.internal: str = spec.internal
        self.data: Any = data
        self.internal_detail: str | None = internal_detail
        super().__init__(f"[{int(spec.code)}] {self.message}")

    def to_response(self) -> ApiResponse[Any]:
        """转换为统一失败响应。

        Returns:
            ApiResponse[Any]: `data` 为 `None` 或业务附加数据的失败响应。
        """
        return ApiResponse[Any].fail(code=int(self.code), message=self.message, data=self.data)

    @property
    def log_message(self) -> str:
        """日志用完整描述（含内部含义，不含原始下游报文）。"""
        suffix = f" | {self.internal_detail}" if self.internal_detail else ""
        return f"code={int(self.code)} internal={self.internal} message={self.message}{suffix}"


def error_response(
    code: ErrorCode, *, message: str | None = None, data: Any = None
) -> ApiResponse[Any]:
    """按错误码构造统一失败响应（非异常路径使用）。

    Args:
        code: 错误码。
        message: 覆盖默认业务文案。
        data: 附加业务数据。

    Returns:
        ApiResponse[Any]: 统一失败响应。
    """
    spec = ERROR_REGISTRY[code]
    return ApiResponse[Any].fail(code=int(spec.code), message=message or spec.message, data=data)


def http_status_of(code: ErrorCode) -> int:
    """查询错误码对应的 HTTP 状态码。

    Args:
        code: 错误码。

    Returns:
        int: HTTP 状态码。
    """
    return ERROR_REGISTRY[code].http_status


class ErrorMapper:
    """下游错误 → 自研错误码映射器（架构文档 3.6 类图）。

    这是「形态防火墙」的关键组件：下游原始状态码与报文在此被彻底重写，
    上层永远拿不到底座形态（架构文档 1.4 第 3 条）。
    """

    #: 下游 HTTP 状态码 → 自研错误码
    STATUS_MAP: Final[dict[int, ErrorCode]] = {
        400: ErrorCode.MODEL_GENERATE_FAILED,
        401: ErrorCode.DOWNSTREAM_UNAVAILABLE,
        403: ErrorCode.DOWNSTREAM_UNAVAILABLE,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        408: ErrorCode.DOWNSTREAM_TIMEOUT,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        422: ErrorCode.PARAM_INVALID,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.DOWNSTREAM_UNAVAILABLE,
        502: ErrorCode.DOWNSTREAM_UNAVAILABLE,
        503: ErrorCode.DOWNSTREAM_UNAVAILABLE,
        504: ErrorCode.DOWNSTREAM_TIMEOUT,
    }

    #: 下游错误码字符串 → 自研错误码（用于业务语义细分，如额度不足、内容拦截）
    KEYWORD_MAP: Final[tuple[tuple[str, ErrorCode], ...]] = (
        ("quota", ErrorCode.MODEL_QUOTA_EXHAUSTED),
        ("insufficient", ErrorCode.MODEL_QUOTA_EXHAUSTED),
        ("context_length", ErrorCode.MODEL_CONTEXT_OVERFLOW),
        ("max_tokens", ErrorCode.MODEL_CONTEXT_OVERFLOW),
        ("sensitive", ErrorCode.MODEL_CONTENT_BLOCKED),
        ("moderation", ErrorCode.MODEL_CONTENT_BLOCKED),
        ("risk_control", ErrorCode.MODEL_CONTENT_BLOCKED),
        ("no_model", ErrorCode.MODEL_PROFILE_UNAVAILABLE),
        ("dataset", ErrorCode.KNOWLEDGE_BASE_NOT_FOUND),
        ("not_found", ErrorCode.RESOURCE_NOT_FOUND),
        ("timeout", ErrorCode.DOWNSTREAM_TIMEOUT),
        ("connection", ErrorCode.DOWNSTREAM_UNAVAILABLE),
    )

    @classmethod
    def to_business_error(
        cls, downstream_status: int, downstream_body: str | None = None
    ) -> tuple[ErrorCode, str]:
        """将下游错误映射为自研错误码与业务语言文案。

        Args:
            downstream_status: 下游 HTTP 状态码。
            downstream_body: 下游响应体，**仅用于关键词判定，不会透出**。

        Returns:
            tuple[ErrorCode, str]: 自研错误码与业务语言文案。
        """
        code = cls.STATUS_MAP.get(downstream_status, ErrorCode.DOWNSTREAM_UNAVAILABLE)
        if downstream_body:
            lowered = downstream_body.lower()
            for keyword, mapped in cls.KEYWORD_MAP:
                if keyword in lowered:
                    code = mapped
                    break
        return code, ERROR_REGISTRY[code].message

    @classmethod
    def to_api_error(
        cls,
        downstream_status: int,
        downstream_body: str | None = None,
        *,
        internal_detail: str | None = None,
    ) -> ApiError:
        """将下游错误映射为可直接抛出的业务异常。

        Args:
            downstream_status: 下游 HTTP 状态码。
            downstream_body: 下游响应体，**仅用于关键词判定，不会透出**。
            internal_detail: 内部诊断信息（下游状态码），只进日志。

        Returns:
            ApiError: 自研业务异常。
        """
        code, _message = cls.to_business_error(downstream_status, downstream_body)
        detail = f"downstream_status={downstream_status} {internal_detail or ''}".strip()
        return ApiError(code, internal_detail=detail)
