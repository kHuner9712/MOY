"""WF2 产物编辑单测（阶段三 B19，`architecture-phase3.md` §5.3 / D-25）。

一票否决项（QA 会卡这三条，这里逐条钉住）：

1. **保存即重渲染**：`version + 1`、`content_sha256` 变化、下载地址刷新；
2. **编辑后必须重跑脱敏**：「用户改过」不是跳过脱敏的理由——用户贴进手机号，
   落库后必须是掩码；
3. **只读类型被拒**：`link` / `file` → `20001`，且 `version` / 摘要**都不变**
   （防"先写后校验"）；
4. **副作用边界**：不改任务状态、不重跑流程（步骤数与状态集合均不变）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import ApiError, ErrorCode
from app.db.repositories.artifact_repo import EXTENSION_CONTENT_TYPES, ArtifactRecord
from app.plugins.engine import TASK_WAITING_CONFIRM
from app.plugins.renderer import FileRenderer
from app.schemas.agent import CreateTaskRequest
from app.schemas.task import ArtifactEditRequest
from tests.unit.task_helpers import TENANT, auth, build_service, load_manifest

#: 带人工接管点的插件（用它跑出一个真正带产物的任务）
PLUGIN_ID = "plg_dev_workbench"


def _run(coro: object) -> object:
    """同步执行协程。

    Args:
        coro: 协程对象。

    Returns:
        object: 返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _prepare_task() -> tuple[object, object, object, str]:
    """建一个"已跑到接管点"的任务并返回其首个可编辑产物。

    Returns:
        tuple: `(服务, 任务仓储, 产物仓储, artifact_id)`。
    """
    service, tasks, artifacts, _ = build_service(load_manifest(PLUGIN_ID))
    created = service.create_plugin_task(  # type: ignore[attr-defined]
        CreateTaskRequest(
            conversation_id="conv_1",
            inputs={
                "title": "登录页改版",
                "requirement": "换成新版设计稿",
                "repo": "acme/web",
                "branch_policy": "新建分支",
            },
            plugin_id=PLUGIN_ID,
        ),
        auth(),
    )
    data = _run(created)
    items = _run(artifacts.list_by_task(TENANT, data.task_id))
    assert items, "第 1 步应产出至少一个产物"
    return service, tasks, artifacts, (data.task_id, items[0].artifact_id)


def test_edit_with_xlsx_render_format_stores_real_xlsx() -> None:
    """WF2 选择 Excel：落库 MIME 与字节都必须是 xlsx（表格重新导出路径）。"""
    service, _tasks, artifacts, (task_id, artifact_id) = _prepare_task()
    edited = _run(
        service.edit_artifact(  # type: ignore[attr-defined]
            task_id,
            artifact_id,
            ArtifactEditRequest(content="姓名,得分\n张三,88\n李四,90", render_format="xlsx"),
            auth(),
        )
    )
    assert edited.version >= 2
    stored = _run(artifacts.get(TENANT, artifact_id))
    assert stored is not None
    assert stored.content_type == EXTENSION_CONTENT_TYPES["xlsx"]
    assert FileRenderer.is_openable_xlsx(stored.content) is True


def test_edit_bumps_version_rerenders_and_resanitizes() -> None:
    """保存 → 版本 +1、摘要变化、下载地址刷新，且**手机号被掩码**。"""
    service, _tasks, artifacts, (task_id, artifact_id) = _prepare_task()
    before = _run(artifacts.get(TENANT, artifact_id))
    assert before is not None

    edited = _run(
        service.edit_artifact(  # type: ignore[attr-defined]
            task_id,
            artifact_id,
            ArtifactEditRequest(content="改后的正文，联系电话 13912345678"),
            auth(),
        )
    )
    assert edited.version == before.version + 1, "版本必须只 +1"
    assert edited.content_sha256 != before.content_sha256, "保存后必须重算摘要"
    assert edited.download_url, "保存即重新渲染并刷新下载地址"
    assert edited.version_history, "旧版本必须可在版本历史里回看/下载"

    stored = _run(artifacts.get(TENANT, artifact_id))
    assert stored is not None
    assert "13912345678" not in stored.inline_text, (
        "**编辑后必须重跑脱敏**：用户贴进的手机号不能原样落库（一票否决）"
    )
    assert stored.version == edited.version


def test_edit_does_not_touch_task_or_steps() -> None:
    """编辑产物不改任务状态、不重跑流程（步骤数与状态集合均不变）。"""
    service, tasks, _, (task_id, artifact_id) = _prepare_task()
    before_task = _run(tasks.get_task(TENANT, task_id))
    before_steps = _run(tasks.list_steps(TENANT, task_id))
    assert before_task is not None
    assert before_task.status == TASK_WAITING_CONFIRM

    _run(
        service.edit_artifact(  # type: ignore[attr-defined]
            task_id, artifact_id, ArtifactEditRequest(content="只改正文"), auth()
        )
    )
    after_task = _run(tasks.get_task(TENANT, task_id))
    after_steps = _run(tasks.list_steps(TENANT, task_id))
    assert after_task is not None
    assert after_task.status == before_task.status, "编辑是事后修订，不是再执行"
    assert len(after_steps) == len(before_steps), "不得增删步骤行"
    assert [item.status for item in after_steps] == [item.status for item in before_steps]


def test_edit_old_version_is_downloadable() -> None:
    """旧版本在 `version_history` 里带下载地址（只增不删，可回看）。"""
    service, _tasks, _artifacts, (task_id, artifact_id) = _prepare_task()
    first = _run(
        service.edit_artifact(  # type: ignore[attr-defined]
            task_id, artifact_id, ArtifactEditRequest(content="第一版修改"), auth()
        )
    )
    second = _run(
        service.edit_artifact(  # type: ignore[attr-defined]
            task_id, artifact_id, ArtifactEditRequest(content="第二版修改"), auth()
        )
    )
    assert second.version == first.version + 1
    versions = [item.version for item in second.version_history]
    assert len(versions) == len(set(versions)), "历史版本号不得重复"
    assert all(item.download_url for item in second.version_history)


@pytest.mark.parametrize("readonly_type", ["link", "file"])
def test_readonly_types_are_rejected_and_unchanged(readonly_type: str) -> None:
    """`link` / `file` → 20001，且 `version` 与摘要**都不变**（防先写后校验）。"""
    service, _, artifacts, (task_id, _artifact_id) = _prepare_task()
    record = ArtifactRecord(
        tenant_id=TENANT,
        artifact_id="art_readonly001",
        task_id=task_id,
        step_id="step_readonly",
        output_key="commit",
        type=readonly_type,
        name="提交记录",
        inline_text="",
        external_url="https://example.com/commit/1" if readonly_type == "link" else "",
        content_sha256="a" * 64,
        version=1,
    )
    _run(artifacts.add_artifacts([record]))
    with pytest.raises(ApiError) as excinfo:
        _run(
            service.edit_artifact(  # type: ignore[attr-defined]
                task_id, record.artifact_id, ArtifactEditRequest(content="想改提交记录"), auth()
            )
        )
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)
    after = _run(artifacts.get(TENANT, record.artifact_id))
    assert after is not None
    assert after.version == 1, "被拒后版本不得变（先写后校验会留下脏版本）"
    assert after.content_sha256 == record.content_sha256


def test_only_initiator_can_edit() -> None:
    """编辑是发起人本人的权限（他人 → 20003）。"""
    service, _, _, (task_id, artifact_id) = _prepare_task()
    with pytest.raises(ApiError) as excinfo:
        _run(
            service.edit_artifact(  # type: ignore[attr-defined]
                task_id, artifact_id, ArtifactEditRequest(content="改"), auth(user_id="usr_other")
            )
        )
    assert int(excinfo.value.code) == int(ErrorCode.FORBIDDEN)


def test_unknown_artifact_returns_20004() -> None:
    """产物不存在 → 20004（不是 500，也不静默新建）。"""
    service, _, _, (task_id, _artifact_id) = _prepare_task()
    with pytest.raises(ApiError) as excinfo:
        _run(
            service.edit_artifact(  # type: ignore[attr-defined]
                task_id, "art_missing001", ArtifactEditRequest(content="改"), auth()
            )
        )
    assert int(excinfo.value.code) == int(ErrorCode.RESOURCE_NOT_FOUND)
