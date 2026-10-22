"""插件任务编排服务（阶段三 B19，`architecture-phase3.md` §3.3 / §5.3 / D-22）。

**一个服务管三件事**：建任务 + 推进 + 读写视图。`TaskService` 是唯一持有
「任务状态机 + 一事务一步」的地方，引擎（`PluginEngine`）只算「这一步怎么做」。

**推进位置的选择（写清楚，别让下一个人以为是漏了后台任务）**：
V1.0 单副本、步骤全是平台内置调用（毫秒级），因此 `_advance()` 在**请求内同步执行**，
不用 `asyncio.create_task`。原因有三：① `TestClient`/同步调用下后台任务不保证跑完，
"创建成功但永远 running"会变成一类无法复现的报错；② 断点续跑（`waiting_confirm`）
本来就要求"停住"，同步推进把这条语义变成**确定性**的；③ 多副本前要换 Valkey 队列
（登记 U-06），届时 `_advance` 仍是唯一入口，只换调度不改状态机。

**并发上限用状态表达，不用错误码**（PRD E-12）：同租户同插件占用额度（`running` +
`waiting_confirm`）达到 3 个时，第 4 个落 `queued` 并返回 `queue_position`；
额度释放时**自动**把最早排队的任务转 `running` 并继续推进。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any, Final

from app.business.audit_service import (
    ACTION_TASK_FINISH,
    ACTION_TASK_RETRY,
    ACTION_TASK_START,
    AuditService,
    get_audit_service,
)
from app.business.plugin_grant_service import PluginGrantService, get_plugin_grant_service
from app.business.plugin_service import PluginService, get_plugin_service
from app.business.usage_service import UsageService, get_usage_service
from app.core.artifact_token import make_token
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    ArtifactStore,
    assert_editable,
    get_artifact_store,
)
from app.db.repositories.task_repo import (
    OCCUPYING_STATUSES,
    StepRecord,
    TaskRecord,
    TaskStore,
    get_task_store,
    now_iso,
)
from app.plugins.actions import RunContext
from app.plugins.engine import (
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    STEP_SKIPPED,
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_WAITING_CONFIRM,
    PluginEngine,
)
from app.plugins.loader import PluginRegistry, get_registry
from app.plugins.sanitizer import ArtifactSanitizer
from app.plugins.schema import PluginManifest
from app.schemas.agent import (
    Artifact,
    ArtifactType,
    CreateTaskData,
    CreateTaskRequest,
    ListTasksQuery,
    StepItem,
    StepsData,
    StepStatus,
    TaskDetailData,
    TaskItem,
    TaskResult,
    TaskStatus,
)
from app.schemas.common import build_page
from app.schemas.model import Usage
from app.schemas.task import (
    ArtifactEditData,
    ArtifactEditRequest,
    ArtifactVersionItem,
    ChecklistItem,
    ConfirmInfo,
    RetryTaskData,
    RetryTaskRequest,
    StepArtifact,
    StepFailure,
)

logger = get_logger(__name__)

#: 资源 ID 前缀（与架构 §9.1 一致）
_TASK_PREFIX: Final[str] = "task_"
_STEP_PREFIX: Final[str] = "step_"
_ARTIFACT_PREFIX: Final[str] = "art_"

#: 可重试的原任务状态（WF1 `retry_step` / `retry_all` 的准入集合）
_RETRYABLE_TASK_STATUSES: Final[tuple[str, ...]] = (TASK_FAILED, TASK_CANCELLED)

#: 资源类型（审计 `resource_type`）
_RESOURCE_TASK: Final[str] = "task"


class TaskService:
    """插件任务服务（建任务 / 推进 / 重试 / 视图 / 产物编辑）。

    Attributes:
        _settings: 配置实例。
        _tasks: 任务仓储。
        _artifacts: 产物仓储。
        _registry: 声明注册表。
        _grants: 授权服务。
        _plugins: 插件服务（安装态 / 套餐 / 声明）。
        _engine: 步骤引擎。
        _audit: 审计服务。
        _usage: 计量服务。
        _sanitizer: 产物脱敏器（WF2 编辑后重跑）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        tasks: TaskStore | None = None,
        artifacts: ArtifactStore | None = None,
        registry: PluginRegistry | None = None,
        grants: PluginGrantService | None = None,
        plugins: PluginService | None = None,
        engine: PluginEngine | None = None,
        audit: AuditService | None = None,
        usage: UsageService | None = None,
        sanitizer: ArtifactSanitizer | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            tasks: 任务仓储（缺省按配置选后端）。
            artifacts: 产物仓储（缺省按配置选后端）。
            registry: 声明注册表。
            grants: 授权服务。
            plugins: 插件服务。
            engine: 步骤引擎（缺省由 `plugin_deps.build_action_gateway` 装配）。
            audit: 审计服务。
            usage: 计量服务。
            sanitizer: 产物脱敏器。
        """
        self._settings = settings or get_settings()
        self._tasks = tasks or get_task_store(self._settings)
        self._artifacts = artifacts or get_artifact_store(self._settings)
        self._registry = registry or get_registry()
        self._grants = grants or get_plugin_grant_service(self._settings)
        self._plugins = plugins or get_plugin_service(self._settings)
        self._audit = audit or get_audit_service(self._settings)
        self._usage = usage or get_usage_service(self._settings)
        self._sanitizer = sanitizer or ArtifactSanitizer()
        self._engine = engine or self._build_engine()

    def _build_engine(self) -> PluginEngine:
        """装配步骤引擎（延迟到真正需要时才构造门面实现）。

        Returns:
            PluginEngine: 引擎实例。
        """
        from app.api.v1.plugin_deps import build_action_gateway

        return PluginEngine(
            build_action_gateway(self._settings),
            self._sanitizer,
            retry_backoff_ms=self._settings.plugin_retry_backoff_ms,
        )

    # ------------------------------------------------------------------
    # A1 创建插件任务
    # ------------------------------------------------------------------
    async def create_plugin_task(
        self,
        request: CreateTaskRequest,
        auth: AuthContext,
        *,
        idem_key: str = "",
        retry_of: str = "",
        from_step: int = 0,
        trigger_type: str = "manual",
        copy_steps_from: TaskRecord | None = None,
    ) -> CreateTaskData:
        """创建插件任务（A1 的插件分支）。

        校验顺序固定（顺序决定用户看到哪条出路，§5.2 要点补充）：
        `15001 套餐 → 16002 插件 → 16003 未添加 → 16004 已停用 →
        16005 个人能力 → 16006 企业开关 → 16009 前置插件 → 20001 输入`。

        Args:
            request: A1 请求（`plugin_id` 非空）。
            auth: 鉴权上下文。
            idem_key: 幂等键（同键同插件只建一个任务）。
            retry_of: 重试来源任务 ID。
            from_step: 重试起点。
            trigger_type: `manual` / `retry`。
            copy_steps_from: 重试时复制前序步骤的原任务。

        Returns:
            CreateTaskData: 任务 ID、状态与排队位次。

        Raises:
            ApiError: 上述任一校验失败。
        """
        plugin_id = request.plugin_id
        _, manifest, policies = await self._plugins.prepare_task(plugin_id=plugin_id, auth=auth)
        declared = set(manifest.capability_keys)
        granted = await self._grants.snapshot(auth.tenant_id, plugin_id, auth.user_id)
        required = set(manifest.required_capability_keys)

        # 16005：必需能力未授（给 data.missing_capabilities，前端映射能力名）
        missing = sorted(required - granted)
        if missing:
            raise ApiError(
                ErrorCode.CAPABILITY_NOT_GRANTED,
                data={"missing_capabilities": missing},
                internal_detail=f"ungranted: {', '.join(missing)}",
            )
        # 16006：企业级能力总开关未开（只对 org.*）
        blocked = sorted(
            capability
            for capability in required & declared
            if capability.startswith("org.") and not policies.get(capability, False)
        )
        if blocked:
            raise ApiError(
                ErrorCode.CAPABILITY_POLICY_BLOCKED,
                data={"blocked_capabilities": blocked},
                internal_detail=f"policy disabled: {', '.join(blocked)}",
            )
        # 20001：声明级输入校验（必填 / 长度 / 选项）
        self._engine.preflight(manifest, request.inputs, granted)

        if idem_key:
            existing = await self._tasks.find_by_idem(auth.tenant_id, plugin_id, idem_key)
            if existing is not None:
                recorded = await self._create_data(existing, manifest)
                return await self._with_quota_notice(recorded, auth)
        else:
            # 没带幂等键 → 给它一个**一次性的唯一键**。
            #
            # 为什么不能留空串（B21c 实测的 P0）：`plugin_task` 上有
            # `UNIQUE (tenant_id, plugin_id, idem_key)` 且 `idem_key NOT NULL`，
            # 空串是一个**真实取值**，会被唯一索引当作同一个键——于是
            # 「同一插件建第二个任务」直接 `UniqueViolationError`（对外表现仍是
            # "系统繁忙，请稍后再试"）。内存后端没有这个唯一约束，所以全绿。
            #
            # 语义上这也是对的：幂等只在"客户端主动给了键"时才生效，没给就是**另一单**。
            idem_key = new_resource_id("idem")

        plans = self._engine.plan(manifest, request.inputs)
        steps = self._build_steps(plans, auth.tenant_id, "", request.inputs)
        occupying = await self._tasks.count_occupying(auth.tenant_id, plugin_id)
        queued = await self._tasks.count_queued(auth.tenant_id, plugin_id)
        limit = self._settings.plugin_task_concurrency
        is_queued = occupying >= limit
        status = TASK_QUEUED if is_queued else TASK_RUNNING

        task_id = new_resource_id(_TASK_PREFIX)
        for step in steps:
            step.task_id = task_id
        record = await self._tasks.create_task(
            TaskRecord(
                tenant_id=auth.tenant_id,
                task_id=task_id,
                plugin_id=plugin_id,
                plugin_version=manifest.version,
                conversation_id=request.conversation_id,
                initiator_id=auth.user_id,
                trigger_type=trigger_type,
                status=status,
                progress=0,
                current_step_seq=0,
                queue_position=(queued + 1) if is_queued else None,
                inputs=dict(request.inputs),
                retry_of=retry_of,
                from_step=from_step,
                idem_key=idem_key,
            )
        )
        await self._tasks.create_steps(steps)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_TASK_START,
            resource_type=_RESOURCE_TASK,
            resource_id=task_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "plugin_id": plugin_id,
                "version": manifest.version,
                "status": status,
                "retry_of": retry_of,
            },
        )
        logger.info(
            "plugin_task_created",
            tenant_id=auth.tenant_id,
            task_id=task_id,
            plugin_id=plugin_id,
            status=status,
            queue_position=record.queue_position,
        )
        if is_queued:
            return await self._with_quota_notice(
                CreateTaskData(
                    task_id=task_id,
                    status=TaskStatus.QUEUED,
                    created_at=record.started_at,
                    estimated_seconds=0,
                    plugin_id=plugin_id,
                    steps_total=len(steps),
                    retry_of=retry_of or None,
                    queue_position=record.queue_position,
                ),
                auth,
            )

        if copy_steps_from is not None and from_step > 1:
            await self._replay_prior_steps(record, copy_steps_from, manifest)
        await self._advance_record(record)
        latest = await self._tasks.get_task(auth.tenant_id, task_id) or record
        return await self._with_quota_notice(await self._create_data(latest, manifest), auth)

    async def _with_quota_notice(self, data: CreateTaskData, auth: AuthContext) -> CreateTaskData:
        """给 A1 成功响应补上非阻断额度提示（B27 `15006`）。

        为什么是 `data.notice_*` 而不是错误码：超额用户**仍然可以建任务**
        （PRD 5.3 软限制），前端 `unwrap()` 把非 0 `code` 当异常抛，
        提示走 `code` 会让"建任务成功"变成弹错误框，等于把提醒做成停服。

        单一真源：委托 `UsageService.quota_notice()`（判据与 M4/BL5 同源的
        `over_quota_warning`），本类不重算额度公式。

        Args:
            data: A1 响应数据（已构造完成）。
            auth: 鉴权上下文。

        Returns:
            CreateTaskData: 带提示字段的响应数据（无提示时原样返回）。
        """
        notice_code, notice_message = await self._usage.quota_notice(
            tenant_id=auth.tenant_id, user_id=auth.user_id
        )
        if not notice_code:
            return data
        return data.model_copy(
            update={"notice_code": notice_code, "notice_message": notice_message}
        )

    async def _create_data(self, record: TaskRecord, manifest: PluginManifest) -> CreateTaskData:
        """把任务行转成 A1 响应。

        Args:
            record: 任务行。
            manifest: 插件声明。

        Returns:
            CreateTaskData: A1 响应数据。
        """
        steps = await self._tasks.list_steps(record.tenant_id, record.task_id)
        return CreateTaskData(
            task_id=record.task_id,
            status=TaskStatus(record.status),
            created_at=record.started_at,
            estimated_seconds=min(len(manifest.steps) * 10, 900),
            plugin_id=record.plugin_id,
            steps_total=len(steps),
            retry_of=record.retry_of or None,
            queue_position=record.queue_position,
        )

    # ------------------------------------------------------------------
    # 推进（唯一状态机入口）
    # ------------------------------------------------------------------
    async def _advance_record(self, record: TaskRecord) -> None:
        """按记录推进任务（内部主流程）。

        Args:
            record: 任务行（会被就地更新）。
        """
        manifest = self._manifest_of(record)
        steps = await self._tasks.list_steps(record.tenant_id, record.task_id)
        if not steps:  # pragma: no cover - 建任务时必写骨架
            return
        artifacts = await self._artifacts.list_by_task(record.tenant_id, record.task_id)
        outputs = {item.output_key: item.inline_text or item.external_url for item in artifacts}
        skipped_keys: set[str] = set()
        await self._tasks.update_task(record.tenant_id, record.task_id, status=TASK_RUNNING)
        record.status = TASK_RUNNING
        if not record.started_at:
            record.started_at = now_iso()

        ctx = RunContext(
            tenant_id=record.tenant_id,
            task_id=record.task_id,
            plugin_id=record.plugin_id,
            installation_id=record.plugin_id,
            initiator_id=record.initiator_id,
            trace_id=record.task_id,
            granted=frozenset(
                await self._grants.snapshot(record.tenant_id, record.plugin_id, record.initiator_id)
            ),
            settings={},
            inputs=dict(record.inputs),
            outputs=outputs,
            auth=self._auth_of(record),
        )

        statuses = [step.status for step in steps]
        aborted = False
        for plan, step in zip(self._engine.plan(manifest, record.inputs), steps, strict=False):
            spec = plan.spec
            if step.status in {STEP_COMPLETED, STEP_FAILED, STEP_SKIPPED}:
                continue
            latest = await self._tasks.get_task(record.tenant_id, record.task_id)
            if latest is not None and latest.cancel_requested:
                await self._finish(record, steps, TASK_CANCELLED, artifacts)
                return
            if spec.confirm is not None and record.confirmed_step_seq < spec.seq:
                await self._pause_for_confirm(record, spec.seq, spec.confirm.reason, outputs)
                return
            if plan.will_skip:
                await self._tasks.update_step(
                    record.tenant_id,
                    step.step_id,
                    status=STEP_SKIPPED,
                    reason_text=plan.skip_reason,
                    operator_action="",
                    finished_at=now_iso(),
                )
                step.status = STEP_SKIPPED
                skipped_keys.add(str(step.seq))
                statuses[step.seq - 1] = STEP_SKIPPED
                await self._update_progress(record, statuses)
                continue

            await self._tasks.update_step(
                record.tenant_id,
                step.step_id,
                status=STEP_RUNNING,
                started_at=now_iso(),
            )
            record.current_step_seq = spec.seq
            step_ctx = replace(ctx, step_seq=spec.seq, outputs=dict(outputs))
            outcome = await self._engine.run_step(step_ctx, plan, record.inputs)
            await self._persist_step(record, step, outcome)
            new_artifacts = await self._save_artifacts(record, step, outcome)
            artifacts.extend(new_artifacts)
            for item in new_artifacts:
                outputs[item.output_key] = item.inline_text or item.external_url
            statuses[step.seq - 1] = outcome.status
            if outcome.usage_units:
                record.usage_units += outcome.usage_units
                await self._tasks.update_task(
                    record.tenant_id, record.task_id, usage_units=record.usage_units
                )
            if outcome.status == STEP_FAILED:
                if self._engine.should_abort(spec, outcome):
                    await self._mark_remaining_skipped(record, steps, spec.seq)
                    await self._finish(
                        record,
                        steps,
                        TASK_FAILED,
                        artifacts,
                        error_message=outcome.reason_text,
                    )
                    aborted = True
                    break
                if self._engine.mark_skipped_on_failure(spec, outcome):
                    skipped_keys.add(str(spec.seq))
                warning = self._engine.warning_of(spec)
                if warning not in record.warnings:
                    record.warnings.append(warning)
                    await self._tasks.update_task(
                        record.tenant_id, record.task_id, warnings=list(record.warnings)
                    )
            await self._update_progress(record, statuses)

        if aborted:
            await self._release_slot(record)
            return
        await self._finish(record, steps, TASK_COMPLETED, artifacts)

    async def _pause_for_confirm(
        self, record: TaskRecord, step_seq: int, reason: str, outputs: dict[str, Any]
    ) -> None:
        """在人工接管点停下（`waiting_confirm`，**永不自动推进**）。

        Args:
            record: 任务行。
            step_seq: 待确认步骤序号。
            reason: 确认原因（业务语言）。
            outputs: 已产出结果（供前端展示核对要点）。
        """
        await self._tasks.update_task(
            record.tenant_id,
            record.task_id,
            status=TASK_WAITING_CONFIRM,
            confirm_step_seq=step_seq,
            current_step_seq=step_seq,
        )
        record.status = TASK_WAITING_CONFIRM
        record.confirm_step_seq = step_seq
        logger.info(
            "plugin_task_waiting_confirm",
            task_id=record.task_id,
            step_seq=step_seq,
            keys=sorted(outputs)[:5],
            reason_len=len(reason),
        )

    async def _persist_step(self, record: TaskRecord, step: StepRecord, outcome: Any) -> None:
        """把步骤结果落库（一步一事务）。

        Args:
            record: 任务行。
            step: 步骤行。
            outcome: 引擎返回的步骤结果。
        """
        summary = self._summary_of(outcome)
        await self._tasks.update_step(
            record.tenant_id,
            step.step_id,
            status=outcome.status,
            attempt=outcome.attempts,
            retryable=bool(outcome.retryable),
            output_summary=summary,
            reason_text=outcome.reason_text,
            operator_action=outcome.operator_action,
            tool_execution_id=str(outcome.data.get("tool_execution_id", "") or ""),
            finished_at=now_iso(),
        )
        step.status = outcome.status
        step.output_summary = summary
        step.reason_text = outcome.reason_text

    async def _save_artifacts(
        self, record: TaskRecord, step: StepRecord, outcome: Any
    ) -> list[ArtifactRecord]:
        """把步骤产物落库（已脱敏；重跑时覆盖同 `output_key` 旧产物）。

        Args:
            record: 任务行。
            step: 步骤行。
            outcome: 步骤结果。

        Returns:
            list[ArtifactRecord]: 落库后的产物。
        """
        if not outcome.artifacts:
            return []
        existing = {
            item.output_key: item
            for item in await self._artifacts.list_by_step(
                record.tenant_id, record.task_id, step.step_id
            )
        }
        fresh: list[ArtifactRecord] = []
        saved: list[ArtifactRecord] = []
        for draft in outcome.artifacts:
            current = existing.get(draft.output_key)
            text = draft.inline_text
            content = draft.content
            digest = self._sanitizer.digest(content if content else text)
            if current is not None:
                updated = await self._artifacts.save_version(
                    record.tenant_id,
                    current.artifact_id,
                    inline_text=text,
                    preview_text=draft.preview_text or text[:500],
                    content_sha256=digest,
                    content=content,
                    content_type="text/plain; charset=utf-8",
                )
                if updated is not None:
                    saved.append(updated)
                continue
            fresh.append(
                ArtifactRecord(
                    tenant_id=record.tenant_id,
                    artifact_id=new_resource_id(_ARTIFACT_PREFIX),
                    task_id=record.task_id,
                    step_id=step.step_id,
                    output_key=draft.output_key,
                    type=draft.type,
                    name=draft.name or draft.output_key,
                    inline_text=text,
                    preview_text=draft.preview_text or text[:500],
                    external_url=draft.external_url,
                    storage_key=draft.storage_key,
                    size_bytes=draft.size_bytes or len(text.encode("utf-8")),
                    content_sha256=digest,
                    version=1,
                    content=content or text.encode("utf-8"),
                )
            )
        created = await self._artifacts.add_artifacts(fresh) if fresh else []
        return created + saved

    async def _mark_remaining_skipped(
        self, record: TaskRecord, steps: list[StepRecord], after_seq: int
    ) -> None:
        """中止后续步骤（`on_failure=abort` 的连带效果）。

        Args:
            record: 任务行。
            steps: 步骤列表。
            after_seq: 失败步骤序号（之后的无条件跳过）。
        """
        for step in steps:
            if step.seq <= after_seq or step.status != STEP_PENDING:
                continue
            await self._tasks.update_step(
                record.tenant_id,
                step.step_id,
                status=STEP_SKIPPED,
                reason_text="上一步没有完成，这一步已跳过",
                finished_at=now_iso(),
            )
            step.status = STEP_SKIPPED

    async def _update_progress(self, record: TaskRecord, statuses: list[str]) -> None:
        """按步骤状态刷新进度（单调不减）。

        Args:
            record: 任务行。
            statuses: 步骤状态列表。
        """
        progress = max(record.progress, self._engine.progress(statuses))
        record.progress = progress
        await self._tasks.update_task(
            record.tenant_id,
            record.task_id,
            progress=progress,
            current_step_seq=record.current_step_seq,
        )

    async def _finish(
        self,
        record: TaskRecord,
        steps: list[StepRecord],
        status: str,
        artifacts: list[ArtifactRecord],
        *,
        error_message: str = "",
    ) -> None:
        """收尾任务（终态 + 核查清单 + 计量 + 审计 + 释放额度）。

        Args:
            record: 任务行。
            steps: 步骤列表。
            status: 终态。
            artifacts: 产物列表。
            error_message: 失败原因（业务语言）。
        """
        produced = {item.output_key for item in artifacts}
        pending_keys = {
            item.key
            for item in self._checklist_items(record, artifacts)
            if item.status == "pending"
        }
        checklist = self._checklist_items(record, artifacts)
        derived = {
            "checked": "已核查",
            "pending": "待人工确认",
            "missing": "没有产出",
        }
        summary = _summary_text(status, checklist, derived)
        await self._tasks.update_task(
            record.tenant_id,
            record.task_id,
            status=status,
            progress=100 if status == TASK_COMPLETED else record.progress,
            checklist=[item.model_dump() for item in checklist],
            result_summary=summary,
            error_message=error_message,
            confirm_step_seq=0,
            finished_at=now_iso(),
        )
        record.status = status
        record.progress = 100 if status == TASK_COMPLETED else record.progress
        record.confirm_step_seq = 0
        await self._audit.record(
            tenant_id=record.tenant_id,
            actor_id=record.initiator_id,
            action=ACTION_TASK_FINISH,
            resource_type=_RESOURCE_TASK,
            resource_id=record.task_id,
            actor_role="",
            change_summary={
                "plugin_id": record.plugin_id,
                "status": status,
                "steps": len(steps),
                "produced": len(produced),
                "pending": len(pending_keys),
            },
        )
        if status == TASK_COMPLETED and record.usage_units > 0:
            try:
                await self._usage.record_automation(
                    tenant_id=record.tenant_id,
                    user_id=record.initiator_id,
                    task_id=record.task_id,
                    units=record.usage_units,
                )
            except Exception as exc:  # 计量失败不得影响任务结果
                logger.warning(
                    "plugin_task_usage_failed", task_id=record.task_id, detail=type(exc).__name__
                )
        logger.info(
            "plugin_task_finished",
            task_id=record.task_id,
            plugin_id=record.plugin_id,
            status=status,
            produced=len(produced),
        )
        await self._release_slot(record)

    async def _release_slot(self, record: TaskRecord) -> None:
        """腾出并发额度：把最早排队的任务转 `running` 并推进（PRD E-12）。

        Args:
            record: 刚结束的任务行。
        """
        queued = await self._tasks.list_queued(record.tenant_id, record.plugin_id)
        if not queued:
            return
        occupying = await self._tasks.count_occupying(record.tenant_id, record.plugin_id)
        if occupying >= self._settings.plugin_task_concurrency:
            return
        promoted = queued[0]
        await self._tasks.update_task(
            promoted.tenant_id,
            promoted.task_id,
            status=TASK_RUNNING,
            queue_position=None,
        )
        for index, item in enumerate(queued[1:], start=1):
            await self._tasks.update_task(item.tenant_id, item.task_id, queue_position=index)
        promoted.status = TASK_RUNNING
        promoted.queue_position = None
        logger.info("plugin_task_promoted", task_id=promoted.task_id)
        await self._advance_record(promoted)

    # ------------------------------------------------------------------
    # WF1 重试 / 继续 / 重做
    # ------------------------------------------------------------------
    async def retry(
        self, task_id: str, request: RetryTaskRequest, auth: AuthContext
    ) -> RetryTaskData:
        """WF1：继续 / 重做 / 重试本步 / 从头重试。

        Args:
            task_id: 任务 ID。
            request: 重试请求（`decision` 四值）。
            auth: 鉴权上下文。

        Returns:
            RetryTaskData: `resumed` 明确区分"同一任务续跑"与"新建任务"。

        Raises:
            ApiError: 任务不存在（12001）、状态不允许（17001）、插件停用（16004）。
        """
        record = await self._tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        self._assert_task_visible(record, auth)
        await self._plugins.prepare_task(plugin_id=record.plugin_id, auth=auth)

        if request.decision in {"continue", "redo"}:
            if record.status != TASK_WAITING_CONFIRM:
                raise ApiError(
                    ErrorCode.TASK_NOT_RETRYABLE,
                    message="该任务当前不需要确认",
                    internal_detail=f"status={record.status} decision={request.decision}",
                )
            step_seq = record.confirm_step_seq or record.current_step_seq or 1
            if request.decision == "redo":
                await self._tasks.reset_steps_from(record.tenant_id, record.task_id, step_seq)
            await self._tasks.update_task(
                record.tenant_id,
                record.task_id,
                status=TASK_RUNNING,
                confirm_step_seq=0,
                confirmed_step_seq=step_seq,
                error_message="",
            )
            record.status = TASK_RUNNING
            record.confirm_step_seq = 0
            record.confirmed_step_seq = step_seq
            await self._advance_record(record)
            latest = await self._tasks.get_task(auth.tenant_id, task_id) or record
            return RetryTaskData(
                task_id=task_id,
                resumed=True,
                retry_of=None,
                status=latest.status,
                from_step=step_seq if request.decision == "redo" else None,
            )

        if record.status not in _RETRYABLE_TASK_STATUSES:
            raise ApiError(
                ErrorCode.TASK_NOT_RETRYABLE,
                internal_detail=f"status={record.status} decision={request.decision}",
            )
        from_step = request.from_step or 1
        if request.decision == "retry_step" and not request.from_step:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请选择从哪一步开始重试",
                internal_detail="retry_step requires from_step",
            )
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_TASK_RETRY,
            resource_type=_RESOURCE_TASK,
            resource_id=task_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={"decision": request.decision, "from_step": from_step},
        )
        created = await self.create_plugin_task(
            CreateTaskRequest(
                conversation_id=record.conversation_id,
                goal="",
                inputs=dict(record.inputs),
                plugin_id=record.plugin_id,
            ),
            auth,
            retry_of=task_id,
            from_step=from_step if request.decision == "retry_step" else 0,
            trigger_type="retry",
            copy_steps_from=record,
        )
        return RetryTaskData(
            task_id=created.task_id,
            resumed=False,
            retry_of=task_id,
            status=str(created.status),
            from_step=from_step if request.decision == "retry_step" else None,
        )

    async def _replay_prior_steps(
        self, record: TaskRecord, source: TaskRecord, manifest: PluginManifest
    ) -> None:
        """重试时复制前序步骤结果与产物（**不重跑**，PRD 验收①）。

        Args:
            record: 新任务行。
            source: 原任务行。
            manifest: 插件声明。
        """
        source_steps = await self._tasks.list_steps(source.tenant_id, source.task_id)
        new_steps = await self._tasks.list_steps(record.tenant_id, record.task_id)
        by_seq = {step.seq: step for step in new_steps}
        source_artifacts = await self._artifacts.list_by_task(source.tenant_id, source.task_id)
        for step in source_steps:
            target = by_seq.get(step.seq)
            if target is None or step.seq >= max(record.from_step, 2):
                continue
            if step.status not in {STEP_COMPLETED, STEP_SKIPPED}:
                continue
            await self._tasks.update_step(
                record.tenant_id,
                target.step_id,
                status=step.status,
                output_summary=step.output_summary,
                attempt=0,
                finished_at=now_iso(),
            )
            copied = [
                ArtifactRecord(
                    tenant_id=record.tenant_id,
                    artifact_id=new_resource_id(_ARTIFACT_PREFIX),
                    task_id=record.task_id,
                    step_id=target.step_id,
                    output_key=item.output_key,
                    type=item.type,
                    name=item.name,
                    inline_text=item.inline_text,
                    preview_text=item.preview_text,
                    external_url=item.external_url,
                    storage_key=item.storage_key,
                    size_bytes=item.size_bytes,
                    content_sha256=item.content_sha256,
                    version=1,
                    content=item.content,
                )
                for item in source_artifacts
                if item.step_id == step.step_id
            ]
            if copied:
                await self._artifacts.add_artifacts(copied)
        del manifest

    # ------------------------------------------------------------------
    # A5 取消（插件任务）
    # ------------------------------------------------------------------
    async def cancel(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """取消插件任务（置 `cancel_requested`，引擎在下一步入口停止）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 取消后的任务详情。

        Raises:
            ApiError: 任务不存在（12001）、已终态（12003）。
        """
        record = await self._tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        self._assert_task_visible(record, auth)
        if record.terminal:
            raise ApiError(ErrorCode.TASK_CANCELLED, internal_detail=f"task_id={task_id} terminal")
        await self._tasks.update_task(
            record.tenant_id,
            record.task_id,
            cancel_requested=True,
            status=TASK_CANCELLED,
            finished_at=now_iso(),
            error_message="任务已取消",
        )
        record.status = TASK_CANCELLED
        await self._release_slot(record)
        return await self.get_task(task_id, auth)

    # ------------------------------------------------------------------
    # A2 / A3 / A4 视图
    # ------------------------------------------------------------------
    async def list_tasks(self, query: ListTasksQuery, auth: AuthContext) -> Any:
        """A2 任务列表（含插件任务；无 `plugin_id` 时行为与阶段一一致）。

        Args:
            query: 查询参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[TaskItem]`。
        """
        records, total = await self._tasks.list_tasks(
            auth.tenant_id,
            plugin_id=query.plugin_id,
            status=query.status.value if query.status is not None else "",
            initiator_id=query.initiator_id,
            created_from=query.created_from,
            created_to=query.created_to,
            page=query.page,
            page_size=query.page_size,
        )
        items = [self._task_item(record, auth) for record in records]
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    def _task_item(self, record: TaskRecord, auth: AuthContext) -> TaskItem:
        """把任务行转成列表项。

        Args:
            record: 任务行。
            auth: 鉴权上下文。

        Returns:
            TaskItem: 列表项。
        """
        manifest = self._manifest_of(record)
        return TaskItem(
            task_id=record.task_id,
            conversation_id=record.conversation_id,
            goal=manifest.name if manifest is not None else "",
            status=TaskStatus(record.status),
            progress=record.progress,
            created_at=record.started_at,
            updated_at=record.finished_at or record.started_at,
            plugin_id=record.plugin_id,
            warning_count=len(record.warnings),
            initiator_id=record.initiator_id,
            initiator_name=self._initiator_name(record, auth),
        )

    async def get_task(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """A3 任务详情（插件任务分支）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 任务详情。

        Raises:
            ApiError: 任务不存在（12001）、无权查看（20003）。
        """
        record = await self._tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        self._assert_task_visible(record, auth)
        manifest = self._manifest_of(record)
        steps = await self._tasks.list_steps(record.tenant_id, task_id)
        artifacts = await self._artifacts.list_by_task(record.tenant_id, task_id)
        checklist = self._checklist_items(record, artifacts)
        confirm = None
        if record.status == TASK_WAITING_CONFIRM and record.confirm_step_seq > 0:
            spec = _step_spec(manifest, record.confirm_step_seq)
            confirm = ConfirmInfo(
                step_seq=record.confirm_step_seq,
                reason=spec.confirm.reason
                if spec is not None and spec.confirm
                else "需要你确认后继续",
                summary={
                    key: value
                    for key, value in record.inputs.items()
                    if key in (spec.confirm.summary_fields if spec and spec.confirm else [])
                },
            )
        return TaskDetailData(
            task_id=record.task_id,
            status=TaskStatus(record.status),
            progress=record.progress,
            current_step=str(record.current_step_seq or "") or None,
            result=TaskResult(
                summary=record.result_summary,
                artifacts=[
                    Artifact(
                        type=_artifact_type(item.type),
                        name=item.name,
                        download_url=self._download_url(item),
                        url=item.external_url or None,
                        output_key=item.output_key,
                        content_sha256=item.content_sha256,
                    )
                    for item in artifacts
                ],
                checklist=[item.model_dump() for item in checklist],
                warnings=list(record.warnings),
            ),
            usage=Usage(total_units=record.usage_units),
            updated_at=record.finished_at or record.started_at,
            plugin_id=record.plugin_id,
            plugin_version=record.plugin_version,
            initiator_name=self._initiator_name(record, auth),
            queue_position=record.queue_position,
            steps_total=len(steps),
            retry_of=record.retry_of or None,
            confirm=confirm.model_dump() if confirm is not None else None,
        )

    async def steps(self, task_id: str, auth: AuthContext) -> StepsData:
        """A4 步骤详情（含产物与失败信息）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            StepsData: 步骤列表。

        Raises:
            ApiError: 任务不存在（12001）、无权查看（20003）。
        """
        record = await self._tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        self._assert_task_visible(record, auth)
        steps = await self._tasks.list_steps(record.tenant_id, task_id)
        artifacts = await self._artifacts.list_by_task(record.tenant_id, task_id)
        by_step: dict[str, list[ArtifactRecord]] = {}
        for item in artifacts:
            by_step.setdefault(item.step_id, []).append(item)
        items = [
            StepItem(
                step_id=step.step_id,
                sequence=step.seq,
                title=step.title,
                status=StepStatus(step.status),
                started_at=step.started_at or None,
                finished_at=step.finished_at or None,
                output_summary=step.output_summary or None,
                artifacts=[self._step_artifact(item) for item in by_step.get(step.step_id, [])],
                failure=(
                    StepFailure(
                        reason_text=step.reason_text,
                        retryable=step.retryable,
                        operator_action=step.operator_action,
                    )
                    if step.status == STEP_FAILED
                    else None
                ),
            )
            for step in steps
        ]
        return StepsData(task_id=task_id, steps=items)

    # ------------------------------------------------------------------
    # WF2 产物编辑
    # ------------------------------------------------------------------
    async def edit_artifact(
        self,
        task_id: str,
        artifact_id: str,
        request: ArtifactEditRequest,
        auth: AuthContext,
    ) -> ArtifactEditData:
        """WF2：编辑产物并重新导出（**不改任务状态、不触发重跑**）。

        Args:
            task_id: 任务 ID。
            artifact_id: 产物 ID。
            request: 编辑请求。
            auth: 鉴权上下文。

        Returns:
            ArtifactEditData: 新版本、下载地址与历史版本。

        Raises:
            ApiError: 任务不存在（12001）、产物不存在（20004）、不可编辑（20001）、
                非本人（20003）。
        """
        record = await self._tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        if record.initiator_id != auth.user_id:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                message="只有发起人可以修改这次任务的结果",
                internal_detail=f"initiator={record.initiator_id} actor={auth.user_id}",
            )
        artifact = await self._artifacts.get(auth.tenant_id, artifact_id)
        if artifact is None or artifact.task_id != task_id:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"artifact_id={artifact_id}"
            )
        assert_editable(artifact)

        # **保存即重跑脱敏**：「用户改过」不是跳过脱敏的理由（B19 一票否决项）
        sanitized = self._sanitizer.mask_pii(request.content)
        self._sanitizer.assert_no_secret({"content": sanitized}, where=f"wf2:{artifact_id}")
        content = sanitized.encode("utf-8")
        content_type = artifact.content_type
        render_format = request.render_format
        if render_format is None and artifact.type in {"doc", "draft"}:
            render_format = "md"
        if render_format is not None:
            blob = self._sanitizer.render_file(
                render_format=render_format,
                name=artifact.name or artifact.output_key,
                payload={"title": artifact.name, "content": sanitized},
            )
            content = blob.content
            content_type = blob.content_type
        digest = self._sanitizer.digest(content)
        updated = await self._artifacts.save_version(
            auth.tenant_id,
            artifact_id,
            inline_text=sanitized,
            preview_text=sanitized[:500],
            content_sha256=digest,
            content=content,
            content_type=content_type,
        )
        if updated is None:  # pragma: no cover - 并发删除
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"artifact_id={artifact_id}"
            )
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_TASK_FINISH,
            resource_type=_RESOURCE_TASK,
            resource_id=task_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "artifact_id": artifact_id,
                "version": updated.version,
                "action": "artifact_edit",
            },
        )
        logger.info(
            "plugin_artifact_edited",
            task_id=task_id,
            artifact_id=artifact_id,
            version=updated.version,
        )
        return ArtifactEditData(
            artifact_id=updated.artifact_id,
            version=updated.version,
            download_url=self._download_url(updated),
            url=updated.external_url or None,
            content_sha256=updated.content_sha256,
            version_history=self._version_history(updated),
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _manifest_of(self, record: TaskRecord) -> PluginManifest:
        """按任务快照版本取声明（历史任务按创建时版本渲染）。

        Args:
            record: 任务行。

        Returns:
            PluginManifest: 插件声明。

        Raises:
            ApiError: 声明缺失（16002）。
        """
        manifest = self._registry.get(record.plugin_id, record.plugin_version)
        if manifest is None:
            latest = self._registry.latest(record.plugin_id)
            manifest = self._registry.get(record.plugin_id, latest) if latest else None
        if manifest is None:
            raise ApiError(
                ErrorCode.PLUGIN_NOT_FOUND, internal_detail=f"{record.plugin_id} manifest missing"
            )
        return manifest

    def _auth_of(self, record: TaskRecord) -> Any:
        """构造以发起人身份执行的鉴权上下文（不含任何凭据）。

        Args:
            record: 任务行。

        Returns:
            AuthContext: 发起人身份。
        """
        return AuthContext(user_id=record.initiator_id, tenant_id=record.tenant_id)

    def _assert_task_visible(self, record: TaskRecord, auth: AuthContext) -> None:
        """对象级判权：发起人本人，或持有 `audit:log:read` 的管理者。

        Args:
            record: 任务行。
            auth: 鉴权上下文。

        Raises:
            ApiError: 无权查看（20003）。
        """
        if record.initiator_id == auth.user_id:
            return
        if "audit:log:read" in auth.roles or "team:member:update" in auth.roles:
            return
        raise ApiError(
            ErrorCode.FORBIDDEN,
            message="你没有权限查看该任务",
            internal_detail=f"actor={auth.user_id} initiator={record.initiator_id}",
        )

    def _initiator_name(self, record: TaskRecord, auth: AuthContext) -> str:
        """取发起人显示名（尽力而为，取不到回落 ID）。

        Args:
            record: 任务行。
            auth: 鉴权上下文。

        Returns:
            str: 显示名。
        """
        if record.initiator_id == auth.user_id:
            for key in ("name", "display_name", "email"):
                value = auth.claims.get(key) if isinstance(auth.claims, dict) else None
                if isinstance(value, str) and value:
                    return value
        return record.initiator_id

    def _build_steps(
        self,
        plans: Iterable[Any],
        tenant_id: str,
        task_id: str,
        inputs: dict[str, Any],
    ) -> list[StepRecord]:
        """构造步骤骨架（`title` 存声明快照）。

        Args:
            plans: 引擎计划。
            tenant_id: 企业 ID。
            task_id: 任务 ID（建表后回填）。
            inputs: 业务输入（未使用，保留签名以对齐声明）。

        Returns:
            list[StepRecord]: 步骤骨架。
        """
        del inputs
        return [
            StepRecord(
                tenant_id=tenant_id,
                step_id=new_resource_id(_STEP_PREFIX),
                task_id=task_id,
                seq=plan.spec.seq,
                title=plan.spec.title,
                action=plan.spec.action,
                status=STEP_PENDING,
            )
            for plan in plans
        ]

    def _checklist_items(
        self, record: TaskRecord, artifacts: list[ArtifactRecord]
    ) -> list[ChecklistItem]:
        """按声明与产物生成核查清单。

        Args:
            record: 任务行。
            artifacts: 产物列表。

        Returns:
            list[ChecklistItem]: 核查清单。
        """
        manifest = self._manifest_of(record)
        by_key = {item.output_key: item for item in artifacts}
        items: list[ChecklistItem] = []
        for entry in manifest.checklist:
            artifact = by_key.get(entry.key)
            if artifact is not None:
                status = "checked"
            elif artifact is None and entry.key in {item.output_key for item in artifacts}:
                status = "pending"
            else:
                status = "missing"
            items.append(
                ChecklistItem(
                    key=entry.key,
                    name=entry.name,
                    type=artifact.type if artifact is not None else "text",
                    status=status,
                    source_step=entry.source_step,
                    label=entry.name,
                    download_url=self._download_url(artifact) if artifact is not None else None,
                )
            )
        return items

    def _download_url(self, artifact: ArtifactRecord | None) -> str | None:
        """生成平台代理下载地址（**不暴露存储地址**）。

        Args:
            artifact: 产物行。

        Returns:
            str | None: 下载地址；无可下载内容返回 None。
        """
        if artifact is None:
            return None
        if artifact.type == "link":
            return artifact.external_url or None
        token = make_token(
            tenant_id=artifact.tenant_id,
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            settings=self._settings,
        )
        return f"/files/artifacts/{token}"

    def _version_history(self, artifact: ArtifactRecord) -> list[ArtifactVersionItem]:
        """把历史版本转成响应结构（旧版本同样可下载）。

        Args:
            artifact: 产物行。

        Returns:
            list[ArtifactVersionItem]: 历史版本列表（旧 → 新）。
        """
        items: list[ArtifactVersionItem] = []
        for entry in artifact.version_history:
            version = int(entry.get("version", 1) or 1)
            token = make_token(
                tenant_id=artifact.tenant_id,
                artifact_id=artifact.artifact_id,
                version=version,
                settings=self._settings,
            )
            items.append(
                ArtifactVersionItem(
                    version=version,
                    created_at=str(entry.get("created_at", "") or ""),
                    download_url=f"/files/artifacts/{token}",
                    content_sha256=str(entry.get("content_sha256", "") or ""),
                )
            )
        return items

    def _step_artifact(self, artifact: ArtifactRecord) -> StepArtifact:
        """把产物行转成 A4 产物结构。

        Args:
            artifact: 产物行。

        Returns:
            StepArtifact: 产物结构。
        """
        return StepArtifact(
            artifact_id=artifact.artifact_id,
            output_key=artifact.output_key,
            type=artifact.type,
            name=artifact.name,
            version=artifact.version,
            download_url=self._download_url(artifact),
            url=artifact.external_url or None,
            preview_text=artifact.preview_text,
            content_sha256=artifact.content_sha256,
            editable=artifact.editable,
            version_history=self._version_history(artifact),
        )

    @staticmethod
    def _summary_of(outcome: Any) -> str:
        """生成步骤输出摘要（业务语言，截断到 200 字）。

        Args:
            outcome: 步骤结果。

        Returns:
            str: 摘要。
        """
        for value in outcome.data.values():
            if isinstance(value, str) and value:
                return value[:200]
        if outcome.artifacts:
            return f"已生成 {len(outcome.artifacts)} 项结果"
        return ""


def _summary_text(status: str, checklist: list[ChecklistItem], labels: dict[str, str]) -> str:
    """生成任务结果摘要（业务语言）。

    Args:
        status: 任务终态。
        checklist: 核查清单。
        labels: 核查状态的中文文案。

    Returns:
        str: 摘要文本。
    """
    if status == TASK_FAILED:
        return "本次任务没有全部完成，可以查看每一步的结果后重试"
    if status == TASK_CANCELLED:
        return "任务已取消，已完成的部分可以查看"
    counted = {state: sum(1 for item in checklist if item.status == state) for state in labels}
    return (
        f"本次共完成 {counted['checked']} 项结果"
        f"，{counted['pending']} 项待人工确认，{counted['missing']} 项没有产出"
    )


def _step_spec(manifest: PluginManifest | None, seq: int) -> Any:
    """按序号取步骤声明。

    Args:
        manifest: 插件声明。
        seq: 步骤序号。

    Returns:
        Any: 步骤声明；不存在返回 None。
    """
    if manifest is None:
        return None
    for spec in manifest.steps:
        if spec.seq == seq:
            return spec
    return None


def _artifact_type(value: str) -> ArtifactType:
    """把产物类型字符串转成枚举（未知值回落 `text`）。

    Args:
        value: 类型字符串。

    Returns:
        ArtifactType: 类型枚举。
    """
    try:
        return ArtifactType(value)
    except ValueError:  # pragma: no cover - 声明已校验，兜底防脏数据
        return ArtifactType.TEXT


_service: TaskService | None = None


def get_task_service(settings: Settings | None = None) -> TaskService:
    """获取任务服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        TaskService: 服务实例。
    """
    global _service
    if _service is None:
        _service = TaskService(settings)
    return _service


def reset_task_service() -> None:
    """重置单例（测试隔离）。"""
    global _service
    _service = None


__all__: list[str] = [
    "OCCUPYING_STATUSES",
    "TaskService",
    "get_task_service",
    "reset_task_service",
]
