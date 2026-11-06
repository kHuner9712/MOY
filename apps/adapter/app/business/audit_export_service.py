"""操作记录导出服务（矩阵第 4 项，2026-11-06）。

**边界设计（逐条对应本项的验收要求）**：

1. **按租户 + 时间范围导出**：租户恒取令牌里的 `tenant_id`（不接受请求参数），
   起止时间**必填**。不带范围的全量导出等于把"漏写过滤条件"的代价放大成
   "整表外带"，因此这里直接拒绝（`20001`）。
2. **CSV + JSON Lines 两种格式**：字段集与既有 U6 查询一致（`AuditLogItem` 的
   口径），不新增字段、不做二次脱敏。既有口径见下方"脱敏"说明。
3. **不全量进内存**：`db.repositories.audit_repo.iter_audit_export` 用键集分页
   逐页取数，每页产出一小段字节即交给 `StreamingResponse` 写走；进程里同时
   存在的只有"一页记录 + 一页字节"，与总行数无关。
4. **上限与限流**：`audit_log_export_max_rows` 在**开始流式之前**用 `COUNT(*)`
   判定，超过即报错（**不截断**）；`audit_log_export_rate_limit` 由 API 层按
   「租户 + 用户 + 路径」执行（见 `api/v1/identity.py`）。
5. **导出自身留痕且不递归**：`prepare()` 在流式开始**前**写一条
   `audit_log.export`（谁、范围、格式、条数），写失败即请求失败（不吞异常，
   与 TD1 的口径一致）。该动作被 `EXPORT_EXCLUDED_ACTIONS` 排除在导出结果之外，
   所以导出文件里永远不含"导出记录"，不会递归膨胀。
6. **权限与隔离**：权限点 `audit:log:read` 由 API 层判权；平台令牌被拒绝
   （平台不是租户，`__platform__` 行不属于任何客户）；数据访问全部经
   `tenant_scope(..., read_only=True)`，SQL 里同时显式带 `tenant_id` 条件，
   RLS 与应用层过滤**双重生效**。

**脱敏口径（取证结论，不自创规则）**：

- `schemas/identity.py::AuditLogItem` 的类注释明确"字段透传，脱敏由业务层写入时
  完成"——即敏感字段在**写入**时就已被约束，读取侧不再二次加工。
- `audit_recorder.AuditEntry.change_summary` 的契约是"只放业务字段，禁止放
  密钥/正文"。抽查既有写入点（成员/部门/知识库/插件/订单）确认都是角色、
  状态、条数、名称一类业务字段，没有手机号/邮箱/证件号。
- U6 的 SQL 投影**不含** `ip_masked`（该列在库里是已掩码列，但从未对外暴露），
  导出沿用同一投影，因此没有任何原始 IP 进入文件。
- 因此本模块**不另加掩码规则**。唯一的格式级防护是 CSV 公式注入前缀
  （`_csv_cell`），它防止电子表格把单元格当公式执行，不是字段脱敏。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from app.business.audit_service import get_audit_service
from app.core.audit_actions import ACTION_AUDIT_LOG_EXPORT
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.audit_repo import (
    AuditQuery,
    count_audit_logs,
    iter_audit_export,
    span_exceeds_limit,
)

logger = get_logger(__name__)

#: 支持的导出格式
AuditExportFormat = Literal["csv", "jsonl"]

#: 合法格式集合（顺序即报错文案里的枚举顺序）
SUPPORTED_FORMATS: Final[tuple[str, ...]] = ("csv", "jsonl")

#: 导出字段（**顺序即 CSV 列顺序**；JSONL 的键集合与之完全相同）
#:
#: 与 `schemas/identity.py::AuditLogItem` 对齐：`log_id` / `occurred_at` / `actor_id`
#: / `actor_role` / `action` / `action_display_name` / `resource_type` / `resource_id`
#: / `result` / `change_summary`，另加 `trace_id`（U6 的 SQL 已投影该列，便于排障）。
EXPORT_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("log_id", "记录 ID"),
    ("occurred_at", "发生时间"),
    ("actor_id", "操作人"),
    ("actor_role", "操作人角色"),
    ("action", "操作编码"),
    ("action_display_name", "操作名称"),
    ("resource_type", "资源类型"),
    ("resource_id", "资源 ID"),
    ("result", "结果"),
    ("change_summary", "变更摘要"),
    ("trace_id", "链路 ID"),
)

#: 时间跨度超限文案（与 U6 保持同一句话）
SPAN_EXCEEDED_MESSAGE: Final[str] = "时间范围跨度超过 90 天，请缩小范围后重试"

#: 导出超过行数上限文案（与 TD1 企业数据导出口径一致：报错、不截断）
EXPORT_TOO_LARGE_MESSAGE: Final[str] = "数据量较大，请缩小时间范围或分批导出"

#: 未提供完整时间范围文案
RANGE_REQUIRED_MESSAGE: Final[str] = "请提供完整的起止时间（start_at 与 end_at）"

#: 时间格式非法文案
RANGE_INVALID_MESSAGE: Final[str] = "起止时间格式不正确，应为 ISO 8601"

#: 功能关闭文案（内部诊断信息会写明由哪个开关控制）
EXPORT_DISABLED_MESSAGE: Final[str] = "操作记录导出未开启，请联系管理员"

#: 格式非法文案
FORMAT_INVALID_MESSAGE: Final[str] = "导出格式仅支持 csv 或 jsonl"

#: CSV 公式注入前缀：以这些字符开头的单元格前加单引号，电子表格会按文本处理
_FORMULA_PREFIXES: Final[tuple[str, ...]] = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class AuditExportRequest:
    """导出请求（由 API 层的查询参数构造）。

    Attributes:
        tenant_id: 租户 ID（**来自令牌**，不是请求参数）。
        actor_id: 导出人用户 ID。
        actor_role: 导出人角色（留痕展示用）。
        export_format: `csv` / `jsonl`。
        start_at: 起始时间（ISO 8601，含）。
        end_at: 结束时间（ISO 8601，含）。
        actor_filter: 操作人精确过滤。
        action: 动作精确过滤。
        action_group: 动作域过滤。
        resource_type: 资源类型过滤。
        result: 结果过滤。
        trace_id: 链路 ID（透传给留痕记录）。
    """

    tenant_id: str
    actor_id: str
    actor_role: str = ""
    export_format: AuditExportFormat = "csv"
    start_at: str = ""
    end_at: str = ""
    actor_filter: str = ""
    action: str = ""
    action_group: str = ""
    resource_type: str = ""
    result: str = ""
    trace_id: str = ""


@dataclass(frozen=True, slots=True)
class AuditExportPlan:
    """已通过校验并已留痕的导出计划。

    Attributes:
        export_id: 本次导出的资源 ID（留痕记录的 `resource_id`）。
        request: 原始请求。
        query: 数据库查询条件（键集分页用）。
        rows: 预计导出行数（`COUNT(*)`，上限判定与留痕条数共用）。
        max_rows: 本次允许的最大行数（流式过程再校验一次）。
    """

    export_id: str
    request: AuditExportRequest
    query: AuditQuery
    rows: int
    max_rows: int


class AuditExportService:
    """操作记录导出服务。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 校验与留痕
    # ------------------------------------------------------------------
    async def prepare(self, request: AuditExportRequest) -> AuditExportPlan:
        """校验开关/格式/时间范围/行数上限，并写入导出留痕。

        留痕必须在**流式开始之前**写好：流式一旦开始，响应头与部分内容已经发出，
        再失败就无法改成业务错误；而"导出成功但没留痕"在审计场景里等于没发生。

        Args:
            request: 导出请求。

        Returns:
            AuditExportPlan: 导出计划。

        Raises:
            ApiError: 功能关闭（20003）、参数非法（20001）、超上限（20001）。
        """
        if not self._settings.audit_log_export_enabled:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                message=EXPORT_DISABLED_MESSAGE,
                internal_detail="audit_log_export_disabled (OA_AUDIT_LOG_EXPORT_ENABLED=false)",
            )
        if request.export_format not in SUPPORTED_FORMATS:
            raise ApiError(ErrorCode.PARAM_INVALID, message=FORMAT_INVALID_MESSAGE)
        start_at = _parse_bound(request.start_at, "start_at")
        end_at = _parse_bound(request.end_at, "end_at")
        if not start_at or not end_at:
            raise ApiError(ErrorCode.PARAM_INVALID, message=RANGE_REQUIRED_MESSAGE)
        if _later_than(start_at, end_at):
            raise ApiError(ErrorCode.PARAM_INVALID, message=RANGE_INVALID_MESSAGE)
        if span_exceeds_limit(start_at, end_at):
            raise ApiError(ErrorCode.PARAM_INVALID, message=SPAN_EXCEEDED_MESSAGE)

        query = AuditQuery(
            actor_id=request.actor_filter,
            action=request.action,
            action_group=request.action_group,
            resource_type=request.resource_type,
            result=request.result,
            start_at=start_at,
            end_at=end_at,
        )
        rows = await count_audit_logs(self._settings, request.tenant_id, query)
        if rows > self._settings.audit_log_export_max_rows:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=EXPORT_TOO_LARGE_MESSAGE,
                data={"rows": rows, "max_rows": self._settings.audit_log_export_max_rows},
            )

        export_id = new_resource_id("audit_export")
        await get_audit_service(self._settings).record(
            tenant_id=request.tenant_id,
            actor_id=request.actor_id,
            action=ACTION_AUDIT_LOG_EXPORT,
            resource_type="audit_log",
            resource_id=export_id,
            actor_role=request.actor_role,
            change_summary={
                "format": request.export_format,
                "start_at": start_at,
                "end_at": end_at,
                "rows": rows,
                "filters": _filters(request),
            },
            trace_id=request.trace_id,
        )
        return AuditExportPlan(
            export_id=export_id,
            request=request,
            query=query,
            rows=rows,
            max_rows=self._settings.audit_log_export_max_rows,
        )

    # ------------------------------------------------------------------
    # 流式写出
    # ------------------------------------------------------------------
    async def stream(self, plan: AuditExportPlan) -> AsyncIterator[bytes]:
        """按页产出导出内容（CSV 或 JSONL）。

        Args:
            plan: 已校验并已留痕的导出计划。

        Yields:
            bytes: 一段可直接写进响应的字节。

        Raises:
            RuntimeError: 流式期间记录数超过上限（连接会中断，**不静默截断**）。
        """
        if plan.request.export_format == "jsonl":
            async for chunk in self._stream_jsonl(plan):
                yield chunk
            return
        async for chunk in self._stream_csv(plan):
            yield chunk

    async def _stream_csv(self, plan: AuditExportPlan) -> AsyncIterator[bytes]:
        """产出 CSV（每页一次编码，`change_summary` 序列化为紧凑 JSON 文本）。"""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([key for key, _ in EXPORT_COLUMNS])
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
        written = 0
        async for row in self._rows(plan):
            written += 1
            _guard_cap(written, plan)
            writer.writerow([_csv_cell(_csv_value(key, row)) for key, _ in EXPORT_COLUMNS])
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)
        logger.info(
            "audit_export_streamed",
            export_id=plan.export_id,
            tenant_id=plan.request.tenant_id,
            format="csv",
            rows=written,
        )

    async def _stream_jsonl(self, plan: AuditExportPlan) -> AsyncIterator[bytes]:
        """产出 JSON Lines（每行一个完整对象，键集合与 CSV 列相同）。"""
        written = 0
        async for row in self._rows(plan):
            written += 1
            _guard_cap(written, plan)
            payload = {key: _json_value(key, row) for key, _ in EXPORT_COLUMNS}
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            yield (line + "\n").encode("utf-8")
        logger.info(
            "audit_export_streamed",
            export_id=plan.export_id,
            tenant_id=plan.request.tenant_id,
            format="jsonl",
            rows=written,
        )

    async def _rows(self, plan: AuditExportPlan) -> AsyncIterator[Mapping[str, Any]]:
        """按页迭代导出记录（键集分页，每页一个只读短事务）。"""
        async for row in iter_audit_export(
            self._settings,
            plan.request.tenant_id,
            plan.query,
            page_size=self._settings.audit_log_export_page_size,
        ):
            yield row


def get_audit_export_service(settings: Settings | None = None) -> AuditExportService:
    """获取导出服务实例（无状态，按需构造）。

    Args:
        settings: 可选配置实例。

    Returns:
        AuditExportService: 服务实例。
    """
    return AuditExportService(settings)


# ---------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------
def _guard_cap(written: int, plan: AuditExportPlan) -> None:
    """流式过程中的行数兜底（防止"统计后又有新记录写入"导致超限外带）。"""
    if written > plan.max_rows:
        logger.error(
            "audit_export_cap_exceeded",
            export_id=plan.export_id,
            tenant_id=plan.request.tenant_id,
            written=written,
            max_rows=plan.max_rows,
        )
        raise RuntimeError("audit_export_cap_exceeded")


def _parse_bound(value: str, label: str) -> str:
    """校验并规范化时间边界（统一成 UTC 的 `...Z`）。

    Args:
        value: 原始时间字符串。
        label: 参数名（报错定位用）。

    Returns:
        str: 规范化后的 ISO 8601 字符串；空串原样返回。

    Raises:
        ApiError: 格式非法（20001）。
    """
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message=RANGE_INVALID_MESSAGE,
            internal_detail=f"{label}={value!r}",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _later_than(start_at: str, end_at: str) -> bool:
    """判断起点是否晚于终点。"""
    try:
        start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return start > end


def _filters(request: AuditExportRequest) -> dict[str, str]:
    """收集非空过滤条件（留痕用；空条件不入库，避免摘要里一堆空串）。"""
    filters = {
        "actor_id": request.actor_filter,
        "action": request.action,
        "action_group": request.action_group,
        "resource_type": request.resource_type,
        "result": request.result,
    }
    return {key: value for key, value in filters.items() if value}


def _csv_value(key: str, row: Mapping[str, Any]) -> str:
    """把字段值转成 CSV 文本（`change_summary` 用紧凑 JSON）。"""
    value = row.get(key, "")
    if key == "change_summary":
        summary = value if isinstance(value, Mapping) else {}
        return json.dumps(dict(summary), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if key == "log_id":
        return str(row.get("id", ""))
    return str(value or "")


def _csv_cell(value: str) -> str:
    """阻止电子表格把文本当公式执行（CSV 公式注入）。"""
    if value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _json_value(key: str, row: Mapping[str, Any]) -> Any:
    """把字段值转成 JSON 值（`change_summary` 保持对象结构）。"""
    if key == "log_id":
        return str(row.get("id", ""))
    if key == "change_summary":
        summary = row.get(key, {})
        return dict(summary) if isinstance(summary, Mapping) else {}
    return row.get(key, "")


__all__: list[str] = [
    "EXPORT_COLUMNS",
    "SUPPORTED_FORMATS",
    "AuditExportFormat",
    "AuditExportPlan",
    "AuditExportRequest",
    "AuditExportService",
    "get_audit_export_service",
]
