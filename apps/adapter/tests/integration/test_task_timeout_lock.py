"""单步超时与防重复推进的集成验收（阶段四 T4-04.4，架构 §2.6）。

两条判据对应两类线上事故，**判据的措辞就是事故现场**：

1. **单步超时**：「配 5s 超时 + 步骤内 sleep 10s → 任务判超时**且** `elapsed < 8s`」。
   `elapsed` 那条不是锦上添花——只看"状态是失败"分不清"真中断"与"跑完 10 秒后
   又判定失败"；用户等 10 秒和等 5 秒是两种产品体验。同时要求**前序步骤的产物仍可取**：
   超时按失败收口，但已经产出的东西不能连带消失（用户拿到的部分成果就是那一步的产物）。
2. **防重复推进**：「两个推进方同时进来 → 步骤序号只 +1」。
   没有锁时"读步骤 → 跑一步 → 写结果"会被执行两遍：产物重复、额度重复扣、
   步骤序号跳变。判据用**原语门面的调用流水**证明（同一个 `seq` 不出现两次），
   而不是只看终态——两条路径跑完后的终态长得一模一样。

为什么单步限时而不是整任务限时（架构 §2.6 原话）：8 步的任务外层限 600s，
等于"8 步共享 600s"或"每步都没限"，用户看到的仍然是"进度条永远停在 3/8"。

PG 那一段（`TestAdvanceLockAcrossConnections`）验的是**跨连接**的 advisory lock：
进程内 `asyncio.Lock` 只能证明"同一个事件循环里互斥"，多副本部署时互斥必须由
数据库给（`pg_try_advisory_lock` + 独占连接 + `finally` 解锁）。无 PG 时按既有约定
skip，不破坏"不带 `--run-pg` 也能 30 秒内出结果"的习惯。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.business.task_service import TaskService
from app.core.config import Settings
from app.db.advisory_lock import task_advance_lock
from app.plugins.engine import STEP_COMPLETED, STEP_FAILED, TASK_FAILED
from app.schemas.agent import CreateTaskRequest
from tests.unit import task_helpers

pytestmark = [pytest.mark.integration]

#: 规格给定的量级：单步预算 5s、步骤内 sleep 10s、总耗时要压到 8s 以内
STEP_TIMEOUT_SECONDS = 5.0
SLEEP_SECONDS = 10.0
ELAPSED_LIMIT_SECONDS = 8.0


def _manifest() -> Any:
    """构造两步声明（第 1 步产出产物，第 2 步会被注入的慢调用拖住）。

    用夹具声明而不是产品声明：产品声明的步骤数会随装配调整，
    把"第 2 步超时"钉在产品声明上会让本用例随声明改版而红，而契约其实没变。

    Returns:
        Any: 合法的最小两步声明。
    """
    return task_helpers.fixture_manifest(
        steps=[
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft", "summary"],
                "on_failure": "continue",
            },
            {
                "seq": 2,
                "title": "归档稿件",
                "action": "compose",
                "produces": [],
                "on_failure": "abort",
            },
        ]
    )


def _request(plugin_id: str) -> CreateTaskRequest:
    """构造建任务请求。

    Args:
        plugin_id: 插件标识。

    Returns:
        CreateTaskRequest: 请求。
    """
    return CreateTaskRequest(
        conversation_id="conv_task_timeout", inputs={"topic": "登录页改版"}, plugin_id=plugin_id
    )


async def _create(service: TaskService, plugin_id: str) -> str:
    """建一个任务并返回 `task_id`。

    Args:
        service: 任务服务。
        plugin_id: 插件标识。

    Returns:
        str: 任务 ID。
    """
    created = await service.create_plugin_task(_request(plugin_id), task_helpers.auth())
    return created.task_id


class TestStepTimeout:
    """单步超时：真中断（不是跑完）、按失败收口、前序产物保留。"""

    async def test_step_timeout_interrupts_within_budget_and_keeps_artifacts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """5s 预算 + 10s 步骤 → 超时失败，总耗时 < 8s，第 1 步产物仍可取。"""
        manifest = _manifest()
        service, tasks, _artifacts, _usage = task_helpers.build_service(manifest)
        original = service._engine.run_step  # noqa: SLF001
        seen: list[int] = []

        async def _patched(step_ctx: Any, plan: Any, inputs: Any, **kwargs: Any) -> Any:
            seen.append(plan.spec.seq)
            if plan.spec.seq == 2:
                await asyncio.sleep(SLEEP_SECONDS)  # 远超 5s 预算
            return await original(step_ctx, plan, inputs, **kwargs)

        monkeypatch.setattr(service._engine, "run_step", _patched)  # noqa: SLF001
        service._step_timeout_seconds = STEP_TIMEOUT_SECONDS  # noqa: SLF001

        started = time.perf_counter()
        task_id = await _create(service, manifest.plugin_id)
        elapsed = time.perf_counter() - started

        # ① 真中断：没有等满 10 秒（否则「超时」只是个迟到的结论）
        assert elapsed < ELAPSED_LIMIT_SECONDS, (
            f"单步超时未真正打断执行：耗时 {elapsed:.1f}s ≥ {ELAPSED_LIMIT_SECONDS}s"
        )
        assert seen[:2] == [1, 2], f"应跑到第 2 步才超时，实际执行序列 {seen}"

        # ② 任务与步骤的状态：按失败收口，原因用业务语言（不外泄码与堆栈）
        record = await tasks.get_task(task_helpers.TENANT, task_id)
        assert record is not None
        assert record.status == TASK_FAILED, f"超时应把任务收口为失败，实际 {record.status}"

        steps = await tasks.list_steps(task_helpers.TENANT, task_id)
        assert steps[0].status == STEP_COMPLETED
        assert steps[1].status == STEP_FAILED
        assert "超过" in steps[1].reason_text and "停止等待" in steps[1].reason_text
        assert steps[1].retryable is True, "超时是可重试的：稍后重跑可能就过了"
        assert not any(token.isdigit() and len(token) >= 5 for token in steps[1].reason_text), (
            f"失败原因里不许出现内部错误码：{steps[1].reason_text!r}"
        )

        # ③ 前序产物仍可取（含可下载地址）——超时不得连带清掉已产出内容
        detail = await service.get_task(task_id, task_helpers.auth())
        artifacts = list(detail.result.artifacts) if detail.result is not None else []
        assert artifacts, "第 1 步的产物被超时连带清掉了"
        assert all(item.download_url for item in artifacts), "产物必须带可下载地址"
        assert all(item.download_url.startswith("/files/artifacts/") for item in artifacts), (
            f"下载地址必须复用既有产物代理，实际 {[item.download_url for item in artifacts]}"
        )


class TestConcurrentAdvance:
    """防重复推进：并发推进同一任务时，每个步骤序号只执行一次。"""

    async def test_concurrent_advance_runs_each_step_once(self) -> None:
        """两个并发推进 → 原语门面里每个 `seq` 只出现一次（步骤只 +1，不跳变）。"""
        from app.db.advisory_lock import reset_advance_locks

        reset_advance_locks()
        manifest = _manifest()
        gateway = task_helpers.EchoGateway()
        service, tasks, _artifacts, _usage = task_helpers.build_service(manifest, gateway=gateway)

        # 让每步都真的耗时一点：否则两个推进在内存仓储里天然串行，测不到竞态窗口
        original = service._engine.run_step  # noqa: SLF001

        async def _slow(step_ctx: Any, plan: Any, inputs: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0.05)
            return await original(step_ctx, plan, inputs, **kwargs)

        service._engine.run_step = _slow  # type: ignore[method-assign]  # noqa: SLF001

        task_id = await _create(service, manifest.plugin_id)
        record = await tasks.get_task(task_helpers.TENANT, task_id)
        assert record is not None
        before_seq = record.current_step_seq

        await asyncio.gather(
            service._advance_record(record),  # noqa: SLF001
            service._advance_record(record),  # noqa: SLF001
        )

        executed = [seq for _action, seq in gateway.calls]
        assert executed == sorted(executed), f"步骤执行顺序错乱：{executed}"
        assert len(executed) == len(set(executed)), f"同一步被并发执行了两次：{executed}"
        assert executed == [1, 2], f"两个步骤应各执行一次，实际 {executed}"

        after = await tasks.get_task(task_helpers.TENANT, task_id)
        assert after is not None
        assert after.current_step_seq == 2, (
            f"步骤序号应只推进到 2（不跳变），实际 {after.current_step_seq}（before={before_seq}）"
        )
        assert after.status == "completed"


@pytest.mark.pg
class TestAdvanceLockAcrossConnections:
    """跨连接的 advisory lock：多副本部署时互斥由数据库给，不靠进程内锁。

    **`pg` 标记为什么在类上和方法上各写一次**（不是冗余）：两个消费者认的地方不同——
    pytest 认类级（子类/新增方法自动继承），而门禁
    `deploy/validate_pg_markers.py` 只认「函数装饰器」与「模块级 `pytestmark`」，
    **不解析类级装饰器**。只写类级时 pytest 会正确跳过，但门禁会把这两条报成
    「缺标记」——那种报错看起来像误报，实际是在提醒：不带 `--run-pg` 时它们会走
    `pg_dsn` 夹具的**静默跳过**，既不在 `-m pg` 集合里，也不计入 skipped，
    账面上没人知道它们从没跑过。两处都写，才同时满足两个消费者。
    """

    @pytest.fixture
    def pg_settings(self, pg_dsn: str) -> Settings:
        """指向测试库的配置。

        Args:
            pg_dsn: 测试库连接串。

        Returns:
            Settings: 配置实例。
        """
        return Settings(env="test", business_store="postgres", database_url=pg_dsn)

    @pytest.fixture(autouse=True)
    async def _release_engine(self) -> Any:
        """用例前后释放进程内引擎。

        引擎缓存绑定事件循环，pytest-asyncio 每个用例一个新循环；
        不释放会得到 `RuntimeError: Event loop is closed`（"单独跑绿、一起跑红"的根因）。

        Yields:
            None: 用例运行期间。
        """
        from app.db.engine import dispose_engine

        await dispose_engine()
        yield
        await dispose_engine()

    async def test_second_connection_cannot_acquire_same_task_lock(
        self, pg_settings: Settings
    ) -> None:
        """第一条连接持锁时，第二条连接**立刻**拿不到（不排队、不等待）。

        `pg_try_advisory_lock` 而不是 `pg_advisory_lock`：任务推进由用户请求触发，
        拿不到锁应当立刻返回（另一个推进方在干活），不能把请求线程挂在数据库里等。
        """
        task_id = "task_lock_across_conns"

        async with task_advance_lock(task_id, pg_settings) as first:
            assert first is True, "首条连接必须拿到锁（否则用例前提不成立）"
            started = time.perf_counter()
            async with task_advance_lock(task_id, pg_settings) as second:
                assert second is False, "同一任务的第二条连接不该拿到锁"
            waited = time.perf_counter() - started
            assert waited < 1.0, f"拿不到锁时应立刻返回，实际等待 {waited:.2f}s"

        # 释放之后必须能重新拿到：否则任务会被永久锁死（`finally` 解锁的有效性）
        async with task_advance_lock(task_id, pg_settings) as again:
            assert again is True, "锁释放后无法重新获取：任务会被永久锁死"

    async def test_different_tasks_do_not_block_each_other(self, pg_settings: Settings) -> None:
        """锁粒度是**单个任务**，不是全局：并发推进两个不同任务互不影响。"""
        async with task_advance_lock("task_lock_a", pg_settings) as first:
            assert first is True
            async with task_advance_lock("task_lock_b", pg_settings) as other:
                assert other is True, "不同任务之间不该互斥（否则整个任务域串行）"
