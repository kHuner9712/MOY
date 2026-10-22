"""执行原语 → 平台能力的门面实现（阶段三 B19，`architecture-phase3.md` §3.2 / D-16）。

**这一层只做映射与错误码翻译，不做任何业务判断**（"能不能用"由 `TaskService`
与 `PluginEngine` 决定）。因此这里没有判权、没有套餐检查、没有安装态检查——
那些检查在引擎与服务层已经做过一遍，重复做会让错误码来源变得不可预测。

映射表（封闭 6 项，新增即能力扩张）：

| 原语 | 落点 | 计量 |
| --- | --- | --- |
| `understand` | `ModelService.generate`（理解/抽取结构化要点） | `automation` |
| `compose` | `ModelService.generate`（按 `produces` 决定输出形态） | `automation` |
| `retrieve` | `RagService.search`（`top_n ≤ 10`） | `automation` |
| `tool` | `ToolService.execute`（T5 全流程：连接校验 → 执行 → 落库） | `automation` |
| `render_file` | `ArtifactSanitizer.render_file`（本地渲染，不计计量） | — |
| `summarize` | 本地聚合核查清单（无下游调用） | — |

**运行期复查白名单**：`resolve()` 每次都查 `ACTION_WHITELIST`，防"运行期篡改声明"
（声明在加载期已校验过，但运行期再查一次的成本是一次集合判断）。
"""

from __future__ import annotations

import time
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.plugins.actions import (
    ACTION_INTERNAL_LABELS,
    ACTION_WHITELIST,
    ActionResult,
    ArtifactDraft,
    RunContext,
)
from app.plugins.sanitizer import ArtifactSanitizer
from app.schemas.model import ChatMessage, GenerateRequest, MessageRole, ModelProfile
from app.schemas.rag import SearchRequest
from app.schemas.tool import ExecuteRequest

logger = get_logger(__name__)

#: 原语 → 平台落点标识（日志与审计用，**不进响应**）
PLATFORM_TARGETS: Final[dict[str, str]] = {
    "understand": "model.generate",
    "compose": "model.generate",
    "retrieve": "knowledge.search",
    "tool": "tools.execute",
    "render_file": "local.render",
    "summarize": "local.aggregate",
}

#: `retrieve` 的召回上限（§3.2：`top_n ≤ 10`，防止把整个库塞进上下文）
RETRIEVE_TOP_N: Final[int] = 10

#: 单步产出文本的落库上限（与 `plugin_artifact.ck_inline_size` 同口径 32KB）
MAX_INLINE_TEXT: Final[int] = 32 * 1024

#: 原语内部使用的会话 ID 前缀（不与用户会话混淆）
_TASK_SESSION_PREFIX: Final[str] = "task_"


class ServicesActionGateway:
    """`ActionGateway` 的平台实现（注入给 `PluginEngine`）。

    Attributes:
        _settings: 配置实例。
        _sanitizer: 产物脱敏器（渲染与摘要用）。
        _model: 生成服务（延迟构造，避免启动期硬依赖）。
        _rag: 检索服务。
        _tools: 工具服务。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化门面（依赖延迟到首次调用时构造）。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()
        self._sanitizer = ArtifactSanitizer()
        self._model: Any = None
        self._rag: Any = None
        self._tools: Any = None

    # ------------------------------------------------------------------
    # 依赖（延迟构造：插件层不 import services，只在这一层接触）
    # ------------------------------------------------------------------
    def _model_service(self) -> Any:
        """取生成服务实例。

        Returns:
            Any: `ModelService` 实例。
        """
        if self._model is None:
            from app.services.model_service import get_model_service

            self._model = get_model_service()
        return self._model

    def _rag_service(self) -> Any:
        """取检索服务实例。

        Returns:
            Any: `RagService` 实例。
        """
        if self._rag is None:
            from app.services.rag_service import get_rag_service

            self._rag = get_rag_service()
        return self._rag

    def _tool_service(self) -> Any:
        """取工具服务实例。

        Returns:
            Any: `ToolService` 实例。
        """
        if self._tools is None:
            from app.services.tool_service import get_tool_service

            self._tools = get_tool_service()
        return self._tools

    # ------------------------------------------------------------------
    # 白名单解析
    # ------------------------------------------------------------------
    def resolve(self, action: str) -> str:
        """把原语解析为平台落点标识（运行期复查白名单）。

        Args:
            action: 原语名。

        Returns:
            str: 平台落点标识（如 `model.generate`）。

        Raises:
            ApiError: 原语不在白名单内（30004，配置异常；说明声明被篡改）。
        """
        if action not in ACTION_WHITELIST:
            logger.error("plugin_action_not_allowed", action=action)
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=f"action={action} not in whitelist",
            )
        return PLATFORM_TARGETS[action]

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """执行一个原语。

        Args:
            ctx: 执行上下文（身份 + 已授能力 + 输入/前序产物）。
            action: 原语名（⊆ `ACTION_WHITELIST`）。
            params: 原语参数（由引擎按 `needs` 装配）。

        Returns:
            ActionResult: 执行结果（失败只填业务语言，不填堆栈）。
        """
        target = self.resolve(action)
        started = time.monotonic()
        try:
            if action in {"understand", "compose"}:
                result = await self._run_generate(ctx, action, params)
            elif action == "retrieve":
                result = await self._run_retrieve(ctx, params)
            elif action == "tool":
                result = await self._run_tool(ctx, params)
            elif action == "render_file":
                result = self._run_render(ctx, params)
            else:  # summarize
                result = self._run_summarize(ctx, params)
        except ApiError as exc:
            logger.info(
                "plugin_action_failed",
                action=action,
                target=target,
                step_seq=ctx.step_seq,
                code=int(exc.code),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        logger.info(
            "plugin_action_done",
            action=action,
            target=target,
            step_seq=ctx.step_seq,
            status=result.status,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return result

    # ------------------------------------------------------------------
    # M1：understand / compose
    # ------------------------------------------------------------------
    async def _run_generate(
        self, ctx: RunContext, action: str, params: dict[str, Any]
    ) -> ActionResult:
        """调用 M1 生成（理解要点 / 撰写内容），产物按 `produces` 落。

        Args:
            ctx: 执行上下文。
            action: `understand` 或 `compose`。
            params: 原语参数。

        Returns:
            ActionResult: 生成结果（每步一个主产物 + 文本）。
        """
        request = GenerateRequest(
            conversation_id=_session_id(ctx),
            profile=ModelProfile.GENERAL,
            messages=[
                ChatMessage(
                    role=MessageRole.USER,
                    content=_prompt_of(action, params),
                )
            ],
        )
        data = await self._model_service().generate(
            request, user_id=ctx.initiator_id, auth=_auth_of(ctx)
        )
        content = _sanitize_text(self._sanitizer, data.content)
        artifacts = _build_text_artifacts(params, content)
        return ActionResult(
            status="ok",
            artifacts=artifacts,
            data={draft.output_key: content for draft in artifacts},
            usage_units=int(getattr(data.usage, "total_units", 0) or 0),
        )

    # ------------------------------------------------------------------
    # R7：retrieve
    # ------------------------------------------------------------------
    async def _run_retrieve(self, ctx: RunContext, params: dict[str, Any]) -> ActionResult:
        """调用 R7 检索（`top_n ≤ 10`），检索结果作为文本产物落。

        Args:
            ctx: 执行上下文。
            params: 原语参数。

        Returns:
            ActionResult: 检索结果。

        Raises:
            ApiError: 知识库不存在（11001）等由 R7 抛出，由引擎翻译。
        """
        inputs = params.get("inputs") or {}
        base_id = str(params.get("base_id") or inputs.get("base_id") or "")
        query = str(inputs.get("topic") or inputs.get("query") or params.get("title") or "")
        if not base_id or not query:
            # 没有可用知识库时不做"假检索"：如实返回跳过，产物由后续步骤标注缺依据
            return ActionResult(
                status="skipped",
                reason_text="没有指定知识库，这一步没有检索素材",
            )
        search = await self._rag_service().search(
            SearchRequest(
                base_id=base_id,
                query=query[:2000],
                top_k=RETRIEVE_TOP_N,
                score_threshold=0.5,
            ),
            _auth_of(ctx),
        )
        lines = [f"- {segment.content}" for segment in search.segments[:RETRIEVE_TOP_N]]
        text = _sanitize_text(self._sanitizer, "\n".join(lines))
        artifacts = _build_text_artifacts(params, text, artifact_type="table")
        return ActionResult(
            status="ok",
            artifacts=artifacts,
            data={draft.output_key: text for draft in artifacts} | {"hits": len(search.segments)},
        )

    # ------------------------------------------------------------------
    # T5：tool
    # ------------------------------------------------------------------
    async def _run_tool(self, ctx: RunContext, params: dict[str, Any]) -> ActionResult:
        """调用 T5 执行办公工具操作（写外部动作**不自动重试**）。

        Args:
            ctx: 执行上下文。
            params: 原语参数。

        Returns:
            ActionResult: 工具执行结果（`link` 产物带外链）。

        Raises:
            ApiError: 工具未连接（13002）、授权过期（13003）等由 T5 抛出。
        """
        tool_type = str(params.get("tool_type") or "")
        operation = str(params.get("operation") or "")
        tool_params = _tool_params(params)
        execution = await self._tool_service().execute(
            ExecuteRequest(tool_type=tool_type, operation=operation, params=tool_params),
            _auth_of(ctx),
        )
        result = execution.result or {}
        url = str(result.get("url") or result.get("html_url") or "")
        self._sanitizer.assert_no_secret(result, where=f"tool:{tool_type}.{operation}")
        artifacts = _build_tool_artifacts(params, result, url=url)
        data = {
            "tool_execution_id": execution.execution_id,
            "tool_type": execution.tool_type,
            "operation": execution.operation,
            "result": result,
        }
        for draft in artifacts:
            data.setdefault(draft.output_key, draft.inline_text or url)
        return ActionResult(status="ok", artifacts=artifacts, data=data)

    # ------------------------------------------------------------------
    # render_file（本地）
    # ------------------------------------------------------------------
    def _run_render(self, ctx: RunContext, params: dict[str, Any]) -> ActionResult:
        """本地渲染文件（`csv` / `md` / `docx`），**不计量、不读用户文件**。

        Args:
            ctx: 执行上下文。
            params: 原语参数。

        Returns:
            ActionResult: 文件产物（`file` 类型，二进制在 `content`）。
        """
        render_format = str(params.get("render_format") or "md")
        payload = _render_payload(params)
        rows = _render_rows(params)
        blob = self._sanitizer.render_file(
            render_format=render_format,
            name=str(params.get("title") or "artifact"),
            payload=payload,
            rows=rows,
        )
        preview = _sanitize_text(
            self._sanitizer, str(payload.get("content") or payload.get("title") or "")
        )
        artifacts: list[ArtifactDraft] = []
        for key in params.get("produces") or []:
            artifacts.append(
                ArtifactDraft(
                    output_key=str(key),
                    type="file",
                    name=str(payload.get("title") or key),
                    inline_text=preview,
                    storage_key=f"{ctx.task_id}/{ctx.step_seq}/{blob.filename}",
                    content=blob.content,
                    preview_text=preview[:500],
                    size_bytes=blob.size_bytes,
                )
            )
        return ActionResult(
            status="ok" if artifacts else "skipped",
            artifacts=tuple(artifacts),
            data={draft.output_key: blob.filename for draft in artifacts},
        )

    # ------------------------------------------------------------------
    # summarize（本地聚合）
    # ------------------------------------------------------------------
    def _run_summarize(self, ctx: RunContext, params: dict[str, Any]) -> ActionResult:
        """本地汇总（把前序产物归成一段业务语言摘要，无下游调用）。

        Args:
            ctx: 执行上下文。
            params: 原语参数。

        Returns:
            ActionResult: 汇总结果（通常不产生产物）。
        """
        prior = params.get("prior") or {}
        titles = [key for key in prior if isinstance(key, str)]
        summary = (
            f"本次共生成 {len(titles)} 项结果：" + "、".join(titles)
            if titles
            else "本次没有生成结果"
        )
        return ActionResult(status="ok", data={"summary": _sanitize_text(self._sanitizer, summary)})


def _auth_of(ctx: RunContext) -> AuthContext | None:
    """从上下文取发起人身份。

    Args:
        ctx: 执行上下文。

    Returns:
        AuthContext | None: 身份（缺失时返回 None，由下游按无身份处理）。
    """
    return ctx.auth if isinstance(ctx.auth, AuthContext) else None


def _session_id(ctx: RunContext) -> str:
    """构造原语内部使用的会话标识。

    Args:
        ctx: 执行上下文。

    Returns:
        str: 形如 `task_01H...` 的会话标识。
    """
    return f"{_TASK_SESSION_PREFIX}{ctx.task_id}"[:128] or "task_session"


def _prompt_of(action: str, params: dict[str, Any]) -> str:
    """把步骤声明与输入拼成给模型的业务语言指令。

    Args:
        action: 原语名。
        params: 原语参数。

    Returns:
        str: 指令文本。
    """
    label = ACTION_INTERNAL_LABELS.get(action, action)
    inputs = params.get("inputs") or {}
    parts = [f"请完成「{params.get('title') or label}」。"]
    for key, value in inputs.items():
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}：{value}")
        elif isinstance(value, list) and value:
            parts.append(f"{key}：{'、'.join(str(item) for item in value)}")
    prior = params.get("prior") or {}
    for key, value in prior.items():
        text = value if isinstance(value, str) else str(value)
        parts.append(f"参考（{key}）：{text[:1000]}")
    return "\n".join(parts)[:4000]


def _sanitize_text(sanitizer: ArtifactSanitizer, text: str) -> str:
    """脱敏并截断到落库上限。

    Args:
        sanitizer: 脱敏器。
        text: 原始文本。

    Returns:
        str: 脱敏并截断后的文本。
    """
    return sanitizer.mask_pii(text or "")[:MAX_INLINE_TEXT]


def _build_text_artifacts(
    params: dict[str, Any], text: str, *, artifact_type: str = "text"
) -> tuple[ArtifactDraft, ...]:
    """按 `produces` 生成文本类产物草稿。

    Args:
        params: 原语参数。
        text: 已脱敏文本。
        artifact_type: 产物类型（`text` / `table`）。

    Returns:
        tuple[ArtifactDraft, ...]: 产物草稿。
    """
    drafts: list[ArtifactDraft] = []
    for key in params.get("produces") or []:
        drafts.append(
            ArtifactDraft(
                output_key=str(key),
                type=artifact_type,
                name=str(params.get("title") or key),
                inline_text=text,
                preview_text=text[:500],
                size_bytes=len(text.encode("utf-8")),
            )
        )
    return tuple(drafts)


def _build_tool_artifacts(
    params: dict[str, Any], result: dict[str, Any], *, url: str
) -> tuple[ArtifactDraft, ...]:
    """按 `produces` 生成工具类产物（有外链则 `link`，否则 `text`）。

    Args:
        params: 原语参数。
        result: 工具返回体（已脱敏校验）。
        url: 外链地址。

    Returns:
        tuple[ArtifactDraft, ...]: 产物草稿。
    """
    drafts: list[ArtifactDraft] = []
    text = str(result.get("summary") or result.get("message") or url or "")
    for key in params.get("produces") or []:
        drafts.append(
            ArtifactDraft(
                output_key=str(key),
                type="link" if url else "text",
                name=str(key),
                inline_text=text,
                external_url=url,
                preview_text=text[:500],
                size_bytes=len(text.encode("utf-8")),
            )
        )
    return tuple(drafts)


def _tool_params(params: dict[str, Any]) -> dict[str, Any]:
    """装配传给 T5 的参数（只透用户输入，不带任何凭据）。

    Args:
        params: 原语参数。

    Returns:
        dict[str, Any]: T5 参数。
    """
    inputs = dict(params.get("inputs") or {})
    inputs.update(params.get("prior") or {})
    inputs.pop("doc_ids", None)
    return inputs


def _render_payload(params: dict[str, Any]) -> dict[str, Any]:
    """装配渲染数据。

    Args:
        params: 原语参数。

    Returns:
        dict[str, Any]: `title` / `content` / `sections`。
    """
    inputs = params.get("inputs") or {}
    prior = params.get("prior") or {}
    title = str(inputs.get("doc_type") or params.get("title") or "")
    body_parts = [str(value) for value in prior.values() if isinstance(value, str)]
    return {
        "title": title,
        "content": "\n\n".join(body_parts)[:MAX_INLINE_TEXT],
        "sections": [],
    }


def _render_rows(params: dict[str, Any]) -> list[dict[str, Any]]:
    """装配表格行（`table` 产物渲染 CSV 用）。

    Args:
        params: 原语参数。

    Returns:
        list[dict[str, Any]]: 表格行。
    """
    prior = params.get("prior") or {}
    rows: list[dict[str, Any]] = []
    for key, value in prior.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    rows.append({str(k): v for k, v in item.items()})
                else:
                    rows.append({"项目": key, "内容": str(item)})
        elif isinstance(value, str) and value:
            rows.append({"项目": str(key), "内容": value[:200]})
    return rows


class InertActionGateway:
    """固定返回给定结果的测试门面（生产不可用，仅供单测注入）。

    Attributes:
        result: 固定返回的结果。
    """

    def __init__(self, result: ActionResult | None = None) -> None:
        """初始化。

        Args:
            result: 固定返回的结果（缺省为成功空结果）。
        """
        self.result = result or ActionResult(status="ok")

    def resolve(self, action: str) -> str:
        """解析原语（与生产实现同判据）。

        Args:
            action: 原语名。

        Returns:
            str: 平台落点标识。

        Raises:
            ApiError: 原语不在白名单。
        """
        if action not in ACTION_WHITELIST:
            raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail=f"action={action}")
        return PLATFORM_TARGETS[action]

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """返回固定结果。

        Args:
            ctx: 执行上下文（未使用）。
            action: 原语名。
            params: 原语参数（未使用）。

        Returns:
            ActionResult: 固定结果。
        """
        del ctx, action, params
        return self.result


__all__: list[str] = [
    "MAX_INLINE_TEXT",
    "PLATFORM_TARGETS",
    "RETRIEVE_TOP_N",
    "InertActionGateway",
    "ServicesActionGateway",
]
