"""插件任务编排 · 推进与状态机（`TaskRunner`，切片 2C-3b）。

**本文件是唯一的「写路径」**：建任务、推进（一事务一步）、重试、取消、产物编辑。
`TaskQuery` 只读、`TaskManifestResolver` 只解析声明、`TaskService`（门面）只做装配与
转发——因此「谁能改任务状态」只有一处，不会出现两条并行写路径。

**四条与契约绑定的口径**（与三拆前逐字一致，搬迁不改语义）：

1. **推进位置**：`advance_record()` 在**请求内同步执行**，不用 `asyncio.create_task`。
   `TestClient` 下后台任务不保证跑完，"创建成功但永远 running"会变成无法复现的报错；
   断点续跑（`waiting_confirm`）本来就要"停住"，同步推进把这条语义变成确定性。
2. **锁边界**：`advance_record` 取 `task_advance_lock` + `_advance_locked` 持锁执行；
   `cancel` 的锁只包"条件写"，出锁后才 `release_slot`；`finish → release_slot →
   advance_record` 的嵌套始终落在同一个 `TaskRunner` 实例上（不可拆到别的组件）。
3. **并发上限用状态表达**（PRD E-12）：`settings.plugin_task_concurrency` 是唯一上限
   来源（历史上注释写死"3 个"，与实际配置可能不符）；排队任务在额度释放时被自动转
   `running` 并继续推进。
4. **终态只写一次**：`finish` 与 `cancel` 都用 `expected_status=_LIVE_TASK_STATUSES`
   的条件写，写成功的那一方才 `release_slot`（`transitioned` 判据），否则额度双释放。

**`deps.usage` / `deps.step_timeout_seconds` 必须每次现取**：测试在构造后写回这两个
属性（`test_quota_notice.py:367` / `test_task_step_lock.py:96`），缓存副本会让写入失效。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any, Final

from app.business.audit_service import ACTION_TASK_FINISH, ACTION_TASK_RETRY, ACTION_TASK_START
from app.business.task.access import assert_task_visible
from app.business.task.deps import TaskDeps
from app.business.task.manifest import TaskManifestResolver
from app.business.task.query import TaskQuery
from app.business.usage_service import CAP_AUTOMATION, HOLD_REASON_QUOTA
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.metrics import TASK_STEP_DURATION_SECONDS
from app.core.security import AuthContext
from app.db.advisory_lock import task_advance_lock
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    assert_editable,
)
from app.db.repositories.task import StepRecord, TaskRecord, now_iso
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
    TASK_PENDING,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_WAITING_CONFIRM,
    StepOutcome,
)
from app.plugins.schema import PluginManifest
from app.schemas.agent import (
    CreateTaskData,
    CreateTaskRequest,
    TaskDetailData,
    TaskStatus,
)
from app.schemas.task import ArtifactEditData, ArtifactEditRequest, RetryTaskData, RetryTaskRequest

logger = get_logger(__name__)

#: 资源 ID 前缀（与架构 §9.1 一致）
_TASK_PREFIX: Final[str] = "task_"
_STEP_PREFIX: Final[str] = "step_"
_ARTIFACT_PREFIX: Final[str] = "art_"

#: 可重试的原任务状态（WF1 `retry_step` / `retry_all` 的准入集合）
_RETRYABLE_TASK_STATUSES: Final[tuple[str, ...]] = (TASK_FAILED, TASK_CANCELLED)

#: 「仍在推进」的状态集合 —— 终态写入（收尾 / 取消）的**乐观并发条件**。
#:
#: P1-2 的判据就是它：终态只能从这些状态出发写一次。双方都拿旧快照时，
#: 先写成功的那一方赢，后写的那一方 `update_task` 返回 `None` 并放弃覆写——
#: 这样"取消被 COMPLETED 覆盖"与"额度被释放两次"同时消失。
_LIVE_TASK_STATUSES: Final[tuple[str, ...]] = (
    TASK_PENDING,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_WAITING_CONFIRM,
)

#: 资源类型（审计 `resource_type`）
_RESOURCE_TASK: Final[str] = "task"


class TaskRunner:
    """插件任务写路径（建任务 / 推进 / 重试 / 取消 / 产物编辑）。"""

    def __init__(
        self,
        deps: TaskDeps,
        resolver: TaskManifestResolver,
        query: TaskQuery,
    ) -> None:
        """初始化。

        **只存引用，不存快照**：`deps.usage` 与 `deps.step_timeout_seconds` 一律在
        调用点现取（测试会在构造后写回这两个值）。

        Args:
            deps: 共享依赖容器。
            resolver: 声明解析器（取 manifest 的唯一入口）。
            query: 只读视图（`cancel` 收尾要返回详情、`finish` 要用核查清单）。
        """
        self._deps = deps
        self._resolver = resolver
        self._query = query

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
        _, manifest, policies = await self._deps.plugins.prepare_task(
            plugin_id=plugin_id, auth=auth
        )
        declared = set(manifest.capability_keys)
        granted = await self._deps.grants.snapshot(auth.tenant_id, plugin_id, auth.user_id)
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
        self._deps.engine.preflight(manifest, request.inputs, granted)

        if idem_key:
            existing = await self._deps.tasks.find_by_idem(auth.tenant_id, plugin_id, idem_key)
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

        plans = self._deps.engine.plan(manifest, request.inputs)
        steps = self._build_steps(plans, auth.tenant_id, "", request.inputs)
        occupying = await self._deps.tasks.count_occupying(auth.tenant_id, plugin_id)
        queued = await self._deps.tasks.count_queued(auth.tenant_id, plugin_id)
        limit = self._deps.settings.plugin_task_concurrency
        # P4-11 ③：额度挂起**并入排队**，不新增状态枚举值。
        # 语义上两者都是"没开始跑"，因此复用 `queued`：用户看到的是
        # 「已排队 / 额度恢复后自动开始」，而 `TaskStatus` 不多出一个
        # 库里永不存在的筛选值（那种值会让前端筛出无法解释的空列表）。
        quota_held = await self._quota_held(auth)
        is_queued = occupying >= limit or quota_held
        status = TASK_QUEUED if is_queued else TASK_RUNNING

        task_id = new_resource_id(_TASK_PREFIX)
        for step in steps:
            step.task_id = task_id
        record = await self._deps.tasks.create_task(
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
        await self._deps.tasks.create_steps(steps)
        if quota_held:
            # **挂起标记只在 UPDATE 里写，不进 INSERT 列名列表**：
            # DDL 的 `NOT NULL DEFAULT ''` 让它成为 R7 的 omittable，
            # 也让 G-SQL 的「列数 == 值数」对账完全不受影响。
            # 先建行再改挂起（而不是建行时就带上）是为了不动那条 INSERT。
            await self._deps.tasks.update_task(
                auth.tenant_id, task_id, hold_reason=HOLD_REASON_QUOTA
            )
            logger.info(
                "plugin_task_quota_held",
                tenant_id=auth.tenant_id,
                task_id=task_id,
                plugin_id=plugin_id,
            )
        await self._deps.audit.record(
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
        await self.advance_record(record)
        latest = await self._deps.tasks.get_task(auth.tenant_id, task_id) or record
        return await self._with_quota_notice(await self._create_data(latest, manifest), auth)

    async def _quota_held(self, auth: AuthContext) -> bool:
        """判定本次建任务是否应**挂起**（P4-11 ③，上游调用之前用）。

        与 `RagService._quota_exhausted` 同一口径（都委托 `UsageService`）：
        判定本身出错时**放行**并记 WARN —— 护栏不该把可用性押在计量库上。

        Args:
            auth: 鉴权上下文。

        Returns:
            bool: 已超额返回 True（任务照建、但不执行）。
        """
        if not auth.user_id:
            return False
        try:
            return await self._deps.usage.is_capability_exhausted(
                capability=CAP_AUTOMATION,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
            )
        except Exception as exc:  # 判定失败不阻断
            logger.warning("task_quota_check_failed", detail=type(exc).__name__)
            return False

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
        notice_code, notice_message = await self._deps.usage.quota_notice(
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
        steps = await self._deps.tasks.list_steps(record.tenant_id, record.task_id)
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
    async def advance_record(self, record: TaskRecord) -> None:
        """按记录推进任务（**带推进锁**，阶段四 T4-04.4）。

        两个进程/两个请求同时推进同一任务时，只有拿到锁的那个继续；另一个**立刻返回**
        （不排队、不等待）——否则"读步骤 → 跑一步 → 写结果"会被执行两遍：
        产物重复、额度重复扣、步骤序号跳变。

        Args:
            record: 任务行（会被就地更新）。
        """
        async with task_advance_lock(record.task_id, self._deps.settings) as acquired:
            if not acquired:
                logger.info("task_advance_skipped_locked", task_id=record.task_id)
                return
            await self._advance_locked(record)

    async def _advance_locked(self, record: TaskRecord) -> None:
        """真正的推进主流程（调用方已持有推进锁）。

        Args:
            record: 任务行（会被就地更新）。
        """
        manifest = self._resolver.manifest_of(record)
        steps = await self._deps.tasks.list_steps(record.tenant_id, record.task_id)
        if not steps:  # pragma: no cover - 建任务时必写骨架
            return
        artifacts = await self._deps.artifacts.list_by_task(record.tenant_id, record.task_id)
        outputs = {item.output_key: item.inline_text or item.external_url for item in artifacts}
        skipped_keys: set[str] = set()
        # P1-2：推进的第一步（`running`）也必须是**条件写**。否则"取消已写终态、
        # 推进方拿着旧快照进来"会先把状态改回 running，再跑完后续步骤——
        # 取消被复活，且收尾时又能成功释放一次额度（双释放的第二个入口）。
        claimed = await self._deps.tasks.update_task(
            record.tenant_id,
            record.task_id,
            status=TASK_RUNNING,
            expected_status=_LIVE_TASK_STATUSES,
        )
        if claimed is None:
            logger.info(
                "task_advance_skipped_finished",
                task_id=record.task_id,
                status=record.status,
            )
            return
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
                await self._deps.grants.snapshot(
                    record.tenant_id, record.plugin_id, record.initiator_id
                )
            ),
            settings={},
            inputs=dict(record.inputs),
            outputs=outputs,
            auth=self._auth_of(record),
        )

        statuses = [step.status for step in steps]
        aborted = False
        for plan, step in zip(self._deps.engine.plan(manifest, record.inputs), steps, strict=False):
            spec = plan.spec
            if step.status in {STEP_COMPLETED, STEP_FAILED, STEP_SKIPPED}:
                continue
            latest = await self._deps.tasks.get_task(record.tenant_id, record.task_id)
            if latest is not None and latest.cancel_requested:
                await self.finish(record, steps, TASK_CANCELLED, artifacts)
                return
            if spec.confirm is not None and record.confirmed_step_seq < spec.seq:
                await self._pause_for_confirm(record, spec.seq, spec.confirm.reason, outputs)
                return
            if plan.will_skip:
                await self._deps.tasks.update_step(
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

            await self._deps.tasks.update_step(
                record.tenant_id,
                step.step_id,
                status=STEP_RUNNING,
                started_at=now_iso(),
            )
            record.current_step_seq = spec.seq
            step_ctx = replace(ctx, step_seq=spec.seq, outputs=dict(outputs))
            outcome = await self._run_step_with_timeout(
                step_ctx, plan, record.inputs, manifest.guards
            )
            await self._persist_step(record, step, outcome)
            new_artifacts = await self._save_artifacts(record, step, outcome)
            artifacts.extend(new_artifacts)
            for item in new_artifacts:
                outputs[item.output_key] = item.inline_text or item.external_url
            statuses[step.seq - 1] = outcome.status
            if outcome.usage_units:
                record.usage_units += outcome.usage_units
                await self._deps.tasks.update_task(
                    record.tenant_id, record.task_id, usage_units=record.usage_units
                )
            if outcome.status == STEP_FAILED:
                if self._deps.engine.should_abort(spec, outcome):
                    await self._mark_remaining_skipped(record, steps, spec.seq)
                    await self.finish(
                        record,
                        steps,
                        TASK_FAILED,
                        artifacts,
                        error_message=outcome.reason_text,
                    )
                    aborted = True
                    break
                if self._deps.engine.mark_skipped_on_failure(spec, outcome):
                    skipped_keys.add(str(spec.seq))
                warning = self._deps.engine.warning_of(spec)
                if warning not in record.warnings:
                    record.warnings.append(warning)
                    await self._deps.tasks.update_task(
                        record.tenant_id, record.task_id, warnings=list(record.warnings)
                    )
            await self._update_progress(record, statuses)

        if aborted:
            # `finish(TASK_FAILED)` 已经完成终态写入并释放过额度；
            # 这里再释放一次会多拉起一个排队任务（P1-2 的"双释放"入口之一）。
            await self.release_slot(record, transitioned=False)
            return
        await self.finish(record, steps, TASK_COMPLETED, artifacts)

    async def _run_step_with_timeout(
        self,
        step_ctx: RunContext,
        plan: Any,
        inputs: dict[str, Any],
        guard_specs: Sequence[Any] = (),
    ) -> StepOutcome:
        """单步执行，**每步**都有独立超时（阶段四 T4-04.4，架构 §2.6）。

        为什么必须按**步**限时而不是按任务：一个任务可能有 8 步、每步各自等下游，
        整任务外层 `wait_for(600s)` 等于"8 步共享 600s"或"每步都没限"——用户看到的
        仍然是"卡住不动"。按步限时才能保证单步异常不会拖垮整个任务。

        超时按**失败**处理（`STEP_FAILED` + 业务语言原因），已产出的产物保留：
        已落库的 artifact 不因后续步骤超时被删。

        **超时值每次现取** `deps.step_timeout_seconds`（测试构造后会写回）。

        Args:
            step_ctx: 本步上下文。
            plan: 本步计划（`StepPlan`）。
            inputs: 任务入参。
            guard_specs: 合规规则声明（`manifest.guards`，P1-10 声明式分派）。

        Returns:
            StepOutcome: 步骤结果（超时也返回一个 outcome，不抛异常）。
        """
        timeout = float(self._deps.step_timeout_seconds)
        started = time.perf_counter()
        try:
            return await asyncio.wait_for(
                self._deps.engine.run_step(step_ctx, plan, inputs, guard_specs=guard_specs),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "task_step_timeout",
                task_id=step_ctx.task_id,
                step_seq=step_ctx.step_seq,
                timeout_seconds=timeout,
            )
            return StepOutcome(
                status=STEP_FAILED,
                error_code=0,
                reason_text=f"这一步执行超过 {int(timeout)} 秒，已停止等待",
                operator_action="可稍后重试；若反复超时请联系管理员",
                retryable=True,
            )
        finally:
            # 阶段四 T4-05.1：单步耗时按插件统计（超时那一步也记，便于看分位）
            TASK_STEP_DURATION_SECONDS.labels(
                plugin_id=getattr(plan, "plugin_id", "") or "unknown"
            ).observe(max(time.perf_counter() - started, 0.0))

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
        await self._deps.tasks.update_task(
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
        await self._deps.tasks.update_step(
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
            for item in await self._deps.artifacts.list_by_step(
                record.tenant_id, record.task_id, step.step_id
            )
        }
        fresh: list[ArtifactRecord] = []
        saved: list[ArtifactRecord] = []
        for draft in outcome.artifacts:
            current = existing.get(draft.output_key)
            text = draft.inline_text
            content = draft.content
            digest = self._deps.sanitizer.digest(content if content else text)
            if current is not None:
                updated = await self._deps.artifacts.save_version(
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
        created = await self._deps.artifacts.add_artifacts(fresh) if fresh else []
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
            await self._deps.tasks.update_step(
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
        progress = max(record.progress, self._deps.engine.progress(statuses))
        record.progress = progress
        await self._deps.tasks.update_task(
            record.tenant_id,
            record.task_id,
            progress=progress,
            current_step_seq=record.current_step_seq,
        )

    async def finish(
        self,
        record: TaskRecord,
        steps: list[StepRecord],
        status: str,
        artifacts: list[ArtifactRecord],
        *,
        error_message: str = "",
    ) -> None:
        """收尾任务（终态 + 核查清单 + 计量 + 审计 + 释放额度）。

        P1-2（取消竞态）两处加固：

        1. **写终态前校验 `cancel_requested`**：用户已请求取消时，"跑完了"不是
           `completed` 而是 `cancelled`——否则用户点了取消、任务却显示"已完成"
           （取消丢失，且统计里还算成一次成功）。
        2. **终态写带 `expected_status=_LIVE_TASK_STATUSES`**：并发方（取消）已经
           写了终态时，本次写入**一行都不改**（拿到 `None`），于是既不会覆盖取消，
           也不会二次释放额度。失败归因不在降级范围内：步骤真失败就如实记 `failed`，
           `cancel_requested` 仍留在行上，用户能看到"取消请求已登记"。

        Args:
            record: 任务行。
            steps: 步骤列表。
            status: 终态。
            artifacts: 产物列表。
            error_message: 失败原因（业务语言）。
        """
        latest = await self._deps.tasks.get_task(record.tenant_id, record.task_id)
        if latest is not None and latest.cancel_requested and status == TASK_COMPLETED:
            logger.info("plugin_task_finish_downgraded_to_cancelled", task_id=record.task_id)
            status = TASK_CANCELLED
            error_message = error_message or "任务已取消"
        produced = {item.output_key for item in artifacts}
        pending_keys = {
            item.key
            for item in self._query.checklist_items(record, artifacts)
            if item.status == "pending"
        }
        checklist = self._query.checklist_items(record, artifacts)
        derived = {
            "checked": "已核查",
            "pending": "待人工确认",
            "missing": "没有产出",
        }
        summary = _summary_text(status, checklist, derived)
        updated = await self._deps.tasks.update_task(
            record.tenant_id,
            record.task_id,
            status=status,
            progress=100 if status == TASK_COMPLETED else record.progress,
            checklist=[item.model_dump() for item in checklist],
            result_summary=summary,
            error_message=error_message,
            confirm_step_seq=0,
            finished_at=now_iso(),
            expected_status=_LIVE_TASK_STATUSES,
        )
        if updated is None:
            # 并发方已经收口（典型是用户取消）：终态、审计与额度都归它，
            # 本方法**不得**再写一次，否则取消被覆盖、额度被双释放。
            logger.info(
                "plugin_task_finish_skipped_terminal",
                task_id=record.task_id,
                planned_status=status,
            )
            return
        record.status = status
        record.progress = 100 if status == TASK_COMPLETED else record.progress
        record.confirm_step_seq = 0
        await self._deps.audit.record(
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
                await self._deps.usage.record_automation(
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
        await self.release_slot(record)

    async def release_slot(self, record: TaskRecord, *, transitioned: bool = True) -> None:
        """腾出并发额度：把最早排队的任务转 `running` 并推进（PRD E-12）。

        P1-2（幂等）：释放额度**只能由"把任务写成终态的那一方"发起**（`transitioned=True`）。
        推进收尾与用户取消是两条并行路径，双方都调一次就会各拉起一个排队任务，
        并发上限被击穿。调用方用 `transitioned` 声明自己是否真的完成了
        「仍在推进 → 终态」的那一次写入，没写成功的一方传 `False`，这里直接返回。

        Args:
            record: 刚结束的任务行。
            transitioned: 本次调用是否**由自己完成了终态写入**（幂等判据）。
        """
        if not transitioned:
            logger.info("plugin_task_slot_release_skipped", task_id=record.task_id)
            return
        queued = await self._deps.tasks.list_queued(record.tenant_id, record.plugin_id)
        if not queued:
            return
        occupying = await self._deps.tasks.count_occupying(record.tenant_id, record.plugin_id)
        if occupying >= self._deps.settings.plugin_task_concurrency:
            return
        promoted = queued[0]
        # 拉起排队任务同样是状态转换：带 `expected_status=queued` 才算"抢到"。
        # 没有这个条件时，两个并发释放方会各拉起一个任务，并发上限被击穿。
        claimed = await self._deps.tasks.update_task(
            promoted.tenant_id,
            promoted.task_id,
            status=TASK_RUNNING,
            queue_position=None,
            expected_status=TASK_QUEUED,
        )
        if claimed is None:
            logger.info("plugin_task_promote_skipped", task_id=promoted.task_id)
            return
        for index, item in enumerate(queued[1:], start=1):
            await self._deps.tasks.update_task(item.tenant_id, item.task_id, queue_position=index)
        promoted.status = TASK_RUNNING
        promoted.queue_position = None
        logger.info("plugin_task_promoted", task_id=promoted.task_id)
        await self.advance_record(promoted)

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
        record = await self._deps.tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        assert_task_visible(record, auth)
        await self._deps.plugins.prepare_task(plugin_id=record.plugin_id, auth=auth)

        if request.decision in {"continue", "redo"}:
            if record.status != TASK_WAITING_CONFIRM:
                raise ApiError(
                    ErrorCode.TASK_NOT_RETRYABLE,
                    message="该任务当前不需要确认",
                    internal_detail=f"status={record.status} decision={request.decision}",
                )
            step_seq = record.confirm_step_seq or record.current_step_seq or 1
            if request.decision == "redo":
                await self._deps.tasks.reset_steps_from(record.tenant_id, record.task_id, step_seq)
            await self._deps.tasks.update_task(
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
            await self.advance_record(record)
            latest = await self._deps.tasks.get_task(auth.tenant_id, task_id) or record
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
        await self._deps.audit.record(
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
        source_steps = await self._deps.tasks.list_steps(source.tenant_id, source.task_id)
        new_steps = await self._deps.tasks.list_steps(record.tenant_id, record.task_id)
        by_seq = {step.seq: step for step in new_steps}
        source_artifacts = await self._deps.artifacts.list_by_task(source.tenant_id, source.task_id)
        for step in source_steps:
            target = by_seq.get(step.seq)
            if target is None or step.seq >= max(record.from_step, 2):
                continue
            if step.status not in {STEP_COMPLETED, STEP_SKIPPED}:
                continue
            await self._deps.tasks.update_step(
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
                await self._deps.artifacts.add_artifacts(copied)
        del manifest

    # ------------------------------------------------------------------
    # A5 取消（插件任务）
    # ------------------------------------------------------------------
    async def cancel(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """取消插件任务（置 `cancel_requested` + 终态，引擎在下一步入口停止）。

        P1-2（取消竞态）：取消是**状态转换**，不是普通字段更新。修复前三处漏洞：

        1. 取消不持推进锁直接写终态 → 与推进并发时，`finish` 可以拿旧快照把它覆盖；
        2. `update_task` 没有乐观条件 → 覆盖是**无条件**的，覆盖后状态变成 `completed`；
        3. 两条路径各调一次 `release_slot` → 额度被释放两次，排队任务被多拉起一个。

        现在的写法：终态写入带 `expected_status=仍在推进`（双后端同语义的原子条件写），
        **写成功的那一方**才释放额度。拿不到推进锁时**不排队等待**——取消是用户请求，
        把它挂在"当前这一步跑完"上等于让用户点不动；互斥由条件写保证，
        推进方即使手里是旧快照也写不进终态。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 取消后的任务详情。

        Raises:
            ApiError: 任务不存在（12001）、已终态（12003）。
        """
        record = await self._deps.tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        assert_task_visible(record, auth)
        if record.terminal:
            raise ApiError(ErrorCode.TASK_CANCELLED, internal_detail=f"task_id={task_id} terminal")

        async with task_advance_lock(record.task_id, self._deps.settings) as acquired:
            if not acquired:
                logger.info("plugin_task_cancel_contended", task_id=record.task_id)
            updated = await self._deps.tasks.update_task(
                record.tenant_id,
                record.task_id,
                cancel_requested=True,
                status=TASK_CANCELLED,
                finished_at=now_iso(),
                error_message="任务已取消",
                expected_status=_LIVE_TASK_STATUSES,
            )
        if updated is None:
            # 竞态输给了推进方（对方已把终态写进去）：如实返回 12003，
            # 不假装"取消成功"——否则用户会以为取消生效，实际任务已完成。
            raise ApiError(
                ErrorCode.TASK_CANCELLED,
                internal_detail=f"task_id={task_id} terminal by peer",
            )
        record.status = TASK_CANCELLED
        record.cancel_requested = True
        # 终态由本次调用写入 → 由本次调用释放额度（`transitioned=True`）
        await self.release_slot(record, transitioned=True)
        return await self._query.get_task(task_id, auth)

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
        record = await self._deps.tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        if record.initiator_id != auth.user_id:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                message="只有发起人可以修改这次任务的结果",
                internal_detail=f"initiator={record.initiator_id} actor={auth.user_id}",
            )
        artifact = await self._deps.artifacts.get(auth.tenant_id, artifact_id)
        if artifact is None or artifact.task_id != task_id:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"artifact_id={artifact_id}"
            )
        assert_editable(artifact)

        # **保存即重跑脱敏**：「用户改过」不是跳过脱敏的理由（B19 一票否决项）
        sanitized = self._deps.sanitizer.mask_pii(request.content)
        self._deps.sanitizer.assert_no_secret({"content": sanitized}, where=f"wf2:{artifact_id}")
        content = sanitized.encode("utf-8")
        content_type = artifact.content_type
        render_format = request.render_format
        if render_format is None and artifact.type in {"doc", "draft"}:
            render_format = "md"
        if render_format is not None:
            blob = self._deps.sanitizer.render_file(
                render_format=render_format,
                name=artifact.name or artifact.output_key,
                payload={"title": artifact.name, "content": sanitized},
            )
            content = blob.content
            content_type = blob.content_type
        digest = self._deps.sanitizer.digest(content)
        updated = await self._deps.artifacts.save_version(
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
        await self._deps.audit.record(
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
            download_url=self._query.download_url(updated),
            url=updated.external_url or None,
            content_sha256=updated.content_sha256,
            version_history=self._query.version_history(updated),
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _auth_of(self, record: TaskRecord) -> Any:
        """构造以发起人身份执行的鉴权上下文（不含任何凭据）。

        Args:
            record: 任务行。

        Returns:
            AuthContext: 发起人身份。
        """
        return AuthContext(user_id=record.initiator_id, tenant_id=record.tenant_id)

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


def _summary_text(status: str, checklist: list[Any], labels: dict[str, str]) -> str:
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


__all__: list[str] = ["TaskRunner", "_summary_text"]
