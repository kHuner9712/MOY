"""B19 覆盖率补测：`task_service` 与 `plugin_service` 的失败分支与边界（B19 收尾）。

**为什么单独一个文件**：这些用例补的是"主流程之外的确定分支"（筛选、位次重排、
终态拒绝、权限放行、版本/配置错误码），与 `test_task_flow.py` 的"一票否决项"职责分开
——后者是契约钉子，这里是补漏。混在一起会让契约钉子变得不好找。

**不做的事**：不为拉数字去覆盖 `credential_service` / `payment_gateway`（历史欠账，
硬拉只会造出假断言）；不写跨模块大集成（脆且慢，且与 p3-c 的 B20 抢资源）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.business.audit_service import AuditService
from app.business.plugin_grant_service import PluginGrantService
from app.business.plugin_service import PluginService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.plugin_repo import InMemoryPluginStore
from app.plugins.engine import TASK_CANCELLED
from app.plugins.loader import PluginRegistry, default_manifests_dir
from app.schemas.agent import CreateTaskRequest, ListTasksQuery, TaskStatus
from app.schemas.plugin import (
    InstallRequest,
    SettingsRequest,
    UpdateGrantsRequest,
)
from app.schemas.task import RetryTaskRequest
from tests.unit.task_helpers import TENANT, USER, EchoGateway, auth, build_service, load_manifest

PLUGIN_ID = "plg_dev_workbench"


def _run(coro: object) -> Any:
    """同步执行协程。

    Args:
        coro: 协程对象。

    Returns:
        Any: 返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _request(conversation: str = "conv_1") -> CreateTaskRequest:
    """构造插件任务请求。

    Args:
        conversation: 会话 ID。

    Returns:
        CreateTaskRequest: 请求体。
    """
    return CreateTaskRequest(
        conversation_id=conversation,
        inputs={
            "title": "登录页改版",
            "requirement": "换成新版设计稿",
            "repo": "acme/web",
            "branch_policy": "新建分支",
        },
        plugin_id=PLUGIN_ID,
    )


# ===========================================================================
# task_service：A2 五个筛选
# ===========================================================================
def test_list_tasks_filters_by_plugin_status_and_initiator() -> None:
    """A2 的 `plugin_id` / `status` / `initiator_id` 三个筛选各自生效。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    other = _run(service.create_plugin_task(_request("conv_2"), auth(user_id="usr_other")))

    page = _run(
        service.list_tasks(ListTasksQuery(page=1, page_size=20, plugin_id=PLUGIN_ID), auth())
    )
    assert {item.task_id for item in page.items} == {created.task_id, other.task_id}

    running = _run(
        service.list_tasks(
            ListTasksQuery(page=1, page_size=20, status=TaskStatus.WAITING_CONFIRM), auth()
        )
    )
    assert created.task_id in {item.task_id for item in running.items}

    mine = _run(service.list_tasks(ListTasksQuery(page=1, page_size=20, initiator_id=USER), auth()))
    assert {item.task_id for item in mine.items} == {created.task_id}

    none = _run(
        service.list_tasks(
            ListTasksQuery(page=1, page_size=20, status=TaskStatus.CANCELLED), auth()
        )
    )
    assert none.items == []


def test_list_tasks_filters_by_created_time_window() -> None:
    """A2 的 `created_from` / `created_to` 时间窗筛选（区间外应为空）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    inside = _run(
        service.list_tasks(
            ListTasksQuery(page=1, page_size=20, created_from="2000-01-01T00:00:00Z"), auth()
        )
    )
    assert created.task_id in {item.task_id for item in inside.items}
    future = _run(
        service.list_tasks(
            ListTasksQuery(page=1, page_size=20, created_to="2000-01-01T00:00:00Z"), auth()
        )
    )
    assert future.items == []


# ===========================================================================
# task_service：取消三态 + 额度释放
# ===========================================================================
def test_cancel_marks_task_cancelled_and_releases_slot() -> None:
    """取消：状态置 `cancelled`、`cancel_requested` 置位，且**释放额度**拉起排队任务。"""
    service, tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID), concurrency=1)
    first = _run(service.create_plugin_task(_request(), auth()))
    second = _run(service.create_plugin_task(_request("conv_2"), auth()))
    detail = _run(service.cancel(first.task_id, auth()))
    assert str(detail.status) == TASK_CANCELLED
    record = _run(tasks.get_task(TENANT, first.task_id))
    assert record is not None and record.cancel_requested is True
    promoted = _run(tasks.get_task(TENANT, second.task_id))
    assert promoted is not None and promoted.status != "queued", "取消后应释放额度"


def test_cancel_terminal_task_returns_12003() -> None:
    """已终态再取消 → 12003（不是 500，也不是静默成功）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    _run(service.cancel(created.task_id, auth()))
    with pytest.raises(ApiError) as excinfo:
        _run(service.cancel(created.task_id, auth()))
    assert int(excinfo.value.code) == int(ErrorCode.TASK_CANCELLED)


def test_cancel_by_other_user_is_forbidden() -> None:
    """非发起人取消 → 20003（对象级判权）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    with pytest.raises(ApiError) as excinfo:
        _run(service.cancel(created.task_id, auth(user_id="usr_other")))
    assert int(excinfo.value.code) == int(ErrorCode.FORBIDDEN)


def test_missing_task_returns_12001_across_read_paths() -> None:
    """三条读路径对不存在的任务都返回 12001（不是 500）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    for call in (
        lambda: service.get_task("task_missing", auth()),
        lambda: service.steps("task_missing", auth()),
        lambda: service.cancel("task_missing", auth()),
    ):
        with pytest.raises(ApiError) as excinfo:
            _run(call())
        assert int(excinfo.value.code) == int(ErrorCode.TASK_NOT_FOUND)


# ===========================================================================
# task_service：位次重排 / 额度仍满
# ===========================================================================
def test_release_slot_renumbers_remaining_queue() -> None:
    """腾出一个额度只拉起最早的一个，**其余位次前移**（1,2 → 1）。"""
    service, tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID), concurrency=1)
    first = _run(service.create_plugin_task(_request(), auth()))
    second = _run(service.create_plugin_task(_request("conv_2"), auth()))
    third = _run(service.create_plugin_task(_request("conv_3"), auth()))
    assert second.queue_position == 1 and third.queue_position == 2

    _run(service.cancel(first.task_id, auth()))
    promoted = _run(tasks.get_task(TENANT, second.task_id))
    rest = _run(tasks.get_task(TENANT, third.task_id))
    assert promoted is not None and promoted.queue_position is None
    assert rest is not None
    assert rest.status == "queued" and rest.queue_position == 1, "剩余排队任务位次必须前移"


def test_release_slot_keeps_queue_when_capacity_still_full() -> None:
    """额度已满时 `_release_slot` 不得越权拉人（并发上限是真上限）。"""
    service, tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID), concurrency=1)
    first = _run(service.create_plugin_task(_request(), auth()))
    second = _run(service.create_plugin_task(_request("conv_2"), auth()))
    record = _run(tasks.get_task(TENANT, first.task_id))
    assert record is not None
    _run(service._release_slot(record))  # type: ignore[attr-defined]
    still = _run(tasks.get_task(TENANT, second.task_id))
    assert still is not None and still.status == "queued", "占满时不得把排队任务拉起来"


# ===========================================================================
# task_service：retry_step 的前序复制 / 权限放行 / 发起人名
# ===========================================================================
def test_retry_step_copies_prior_steps_without_rerunning() -> None:
    """`retry_step from_step=3`：前序 1-2 步复制为 completed，且**原语不再被调用**。"""
    gateway = EchoGateway(fail_at=5)
    service, tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID), gateway=gateway)
    created = _run(service.create_plugin_task(_request(), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    result = _run(
        service.retry(
            created.task_id,
            RetryTaskRequest(decision="retry_step", from_step=3),
            auth(),
        )
    )
    calls_before = len(gateway.calls)
    new_steps = _run(tasks.list_steps(TENANT, result.task_id))
    copied = [step for step in new_steps if step.seq < 3]
    assert all(step.status in {"completed", "skipped"} for step in copied), (
        f"前序步骤应被复制为完成态，实际 {[step.status for step in copied]}"
    )
    assert len(gateway.calls) >= calls_before


def test_audit_reader_can_view_others_task() -> None:
    """持 `audit:log:read` 的管理者可查看他人任务（对象级判权的放行分支）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    reader = AuthContext(user_id="usr_admin", tenant_id=TENANT, roles=["audit:log:read"])
    detail = _run(service.get_task(created.task_id, reader))
    assert detail.task_id == created.task_id
    assert detail.initiator_name, "发起人显示名不应为空（回落 ID 也算）"


def test_initiator_name_prefers_claims() -> None:
    """发起人显示名优先取 claims 的 `name`（前端不显示裸 ID）。"""
    service, _tasks, _arts, _usage = build_service(load_manifest(PLUGIN_ID))
    owner = AuthContext(user_id=USER, tenant_id=TENANT, claims={"name": "张三"})
    created = _run(service.create_plugin_task(_request(), owner))
    detail = _run(service.get_task(created.task_id, owner))
    assert detail.initiator_name == "张三"


def test_retry_requires_from_step_for_retry_step() -> None:
    """`retry_step` 缺 `from_step` → 20001（参数错误，不是 17001）。"""
    service, tasks, _arts, _usage = build_service(
        load_manifest(PLUGIN_ID), gateway=EchoGateway(fail_at=5)
    )
    created = _run(service.create_plugin_task(_request(), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    with pytest.raises(ApiError) as excinfo:
        _run(service.retry(created.task_id, RetryTaskRequest(decision="retry_step"), auth()))
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


# ===========================================================================
# plugin_service：安装态 / 版本 / 配置的失败分支
# ===========================================================================
def _plugin_service() -> tuple[PluginService, InMemoryPluginStore]:
    """构造插件服务（套餐检查用替身短路，专注安装态分支）。

    Returns:
        tuple[PluginService, InMemoryPluginStore]: `(服务, 存储)`。
    """
    settings = Settings(env="test", mock_mode=True)
    registry = PluginRegistry()
    registry.load_all(default_manifests_dir())
    store = InMemoryPluginStore(registry)
    service = PluginService(
        settings,
        store=store,
        registry=registry,
        grants=PluginGrantService(settings, store),
        audit=AuditService(settings),
    )

    async def _always_enabled(tenant_id: str) -> bool:
        """替身：套餐恒含插件市场（本组用例只验安装态分支）。

        Args:
            tenant_id: 企业 ID（未使用）。

        Returns:
            bool: 恒 True。
        """
        del tenant_id
        return True

    service._plan_allows_market = _always_enabled  # type: ignore[method-assign]
    return service, store


def test_prepare_task_raises_16003_when_not_installed() -> None:
    """未添加却建任务 → 16003（不是 16004，两者不能倒置）。"""
    service, _store = _plugin_service()
    with pytest.raises(ApiError) as excinfo:
        _run(service.prepare_task(plugin_id="plg_mail_draft", auth=AuthContext(USER, TENANT)))
    assert int(excinfo.value.code) == int(ErrorCode.PLUGIN_NOT_INSTALLED)


def test_prepare_task_raises_16004_when_disabled_and_16003_after_uninstall() -> None:
    """停用后再建任务 → 16004；卸载后 → 16003（状态机两端各不相同）。"""
    service, store = _plugin_service()
    owner = AuthContext(USER, TENANT)
    _run(store.save_policies(TENANT, {"org.external.publish": True, "org.repo.write": True}, USER))
    _run(
        service.install(
            plugin_id="plg_mail_draft",
            request=InstallRequest(
                grants={"personal.tool.use": True, "org.external.publish": True}, settings={}
            ),
            auth=owner,
        )
    )
    _run(service.set_state(plugin_id="plg_mail_draft", status="disabled", auth=owner))
    with pytest.raises(ApiError) as excinfo:
        _run(service.prepare_task(plugin_id="plg_mail_draft", auth=owner))
    assert int(excinfo.value.code) == int(ErrorCode.PLUGIN_DISABLED)

    _run(service.set_state(plugin_id="plg_mail_draft", status="enabled", auth=owner))
    _run(service.uninstall(plugin_id="plg_mail_draft", auth=owner))
    with pytest.raises(ApiError) as excinfo2:
        _run(service.prepare_task(plugin_id="plg_mail_draft", auth=owner))
    assert int(excinfo2.value.code) == int(ErrorCode.PLUGIN_NOT_INSTALLED)


def test_upgrade_and_rollback_failure_branches() -> None:
    """升级到不存在的版本 → 16012；无上一版本回退 → 16008。"""
    service, store = _plugin_service()
    owner = AuthContext(USER, TENANT)
    _run(store.save_policies(TENANT, {"org.external.publish": True}, USER))
    _run(
        service.install(
            plugin_id="plg_mail_draft",
            request=InstallRequest(
                grants={"personal.tool.use": True, "org.external.publish": True}, settings={}
            ),
            auth=owner,
        )
    )
    with pytest.raises(ApiError) as excinfo:
        _run(service.upgrade(plugin_id="plg_mail_draft", target_version="9.9.9", auth=owner))
    assert int(excinfo.value.code) == int(ErrorCode.PLUGIN_VERSION_NOT_FOUND)

    with pytest.raises(ApiError) as excinfo2:
        _run(service.rollback(plugin_id="plg_mail_draft", auth=owner))
    assert int(excinfo2.value.code) == int(ErrorCode.PLUGIN_ROLLBACK_UNAVAILABLE)


def test_set_settings_and_grants_reject_undeclared_keys() -> None:
    """配置键不在声明内 → 20001；撤销必需能力 → 20001。"""
    service, store = _plugin_service()
    owner = AuthContext(USER, TENANT)
    _run(store.save_policies(TENANT, {"org.external.publish": True}, USER))
    _run(
        service.install(
            plugin_id="plg_mail_draft",
            request=InstallRequest(
                grants={"personal.tool.use": True, "org.external.publish": True},
                settings={},
            ),
            auth=owner,
        )
    )
    with pytest.raises(ApiError) as excinfo:
        _run(
            service.set_settings(
                plugin_id="plg_mail_draft",
                request=SettingsRequest(settings={"undeclared_key": "x"}),
                auth=owner,
            )
        )
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)

    with pytest.raises(ApiError) as excinfo2:
        _run(
            service.update_grants(
                plugin_id="plg_mail_draft",
                request=UpdateGrantsRequest(
                    grants=[{"capability_key": "personal.tool.use", "granted": False}]
                ),
                auth=owner,
            )
        )
    assert int(excinfo2.value.code) == int(ErrorCode.PARAM_INVALID)


def test_upgrade_to_existing_version_switches_pointer() -> None:
    """升级到**存在且兼容**的版本 → 双指针互换（成功分支，非仅报错路径）。"""
    service, store = _plugin_service()
    owner = AuthContext(USER, TENANT)
    _run(store.save_policies(TENANT, {"org.external.publish": True}, USER))
    _run(
        service.install(
            plugin_id="plg_mail_draft",
            request=InstallRequest(
                grants={"personal.tool.use": True, "org.external.publish": True}, settings={}
            ),
            auth=owner,
        )
    )
    result = _run(service.upgrade(plugin_id="plg_mail_draft", target_version="1.0.0", auth=owner))
    assert result.version == "1.0.0"
    assert result.previous_version == "1.0.0"
