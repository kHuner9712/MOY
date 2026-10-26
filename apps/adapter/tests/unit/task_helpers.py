"""插件任务域单测的公共脚手架（B19，非测试文件）。

为什么单独一个模块：`test_task_flow` 与 `test_artifact_edit` 都需要「一个已安装、
可推进的插件任务」，而 HTTP 全栈夹具（租户 / 套餐 / 安装 / 授权）在单测里既慢又脆。
这里用**鸭子类型的替身**只提供 `TaskService` 真正用到的两个方法：

- `prepare_task()` → `(安装态, 声明, 企业能力开关)`：服务只用后两个；
- `snapshot()` → 已授能力集合。

替身只替代**外部依赖**，不替代被测逻辑（状态机、脱敏、版本化全部走真实实现）。
"""

from __future__ import annotations

from typing import Any

from app.business.audit_service import AuditService
from app.business.task_service import TaskService
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.artifact_repo import InMemoryArtifactStore
from app.db.repositories.task import InMemoryTaskStore
from app.plugins.actions import ActionResult, ArtifactDraft, RunContext
from app.plugins.engine import PluginEngine
from app.plugins.loader import PluginRegistry, default_manifests_dir
from app.plugins.schema import PluginManifest

#: 夹具租户与用户
TENANT: str = "tnt_test"
USER: str = "usr_test"

#: 默认按哪些能力已授权（覆盖三个标杆插件的必需项）
GRANTED: frozenset[str] = frozenset(
    {
        "personal.knowledge.read",
        "personal.tool.use",
        "personal.file.read",
        "org.directory.read",
        "org.repo.write",
        "org.external.publish",
    }
)


class FakeGrantService:
    """授权服务替身（只实现 `snapshot`）。

    Attributes:
        granted: 已授能力集合。
    """

    def __init__(self, granted: frozenset[str] = GRANTED) -> None:
        """初始化。

        Args:
            granted: 已授能力集合。
        """
        self.granted = granted

    async def snapshot(self, tenant_id: str, plugin_id: str, user_id: str) -> set[str]:
        """返回已授能力集合。

        Args:
            tenant_id: 企业 ID（未使用）。
            plugin_id: 插件标识（未使用）。
            user_id: 用户 ID（未使用）。

        Returns:
            set[str]: 已授能力。
        """
        del tenant_id, plugin_id, user_id
        return set(self.granted)


class FakePluginService:
    """插件服务替身（只实现 `prepare_task`）。

    Attributes:
        manifest: 声明。
        policies: 企业能力开关（缺省全开）。
    """

    def __init__(self, manifest: PluginManifest, policies: dict[str, bool] | None = None) -> None:
        """初始化。

        Args:
            manifest: 声明。
            policies: 企业能力开关。
        """
        self.manifest = manifest
        self.policies = policies or {
            "org.directory.read": True,
            "org.repo.write": True,
            "org.external.publish": True,
        }

    async def prepare_task(
        self, *, plugin_id: str, auth: AuthContext
    ) -> tuple[None, PluginManifest, dict[str, bool]]:
        """替身：返回声明与企业能力开关（安装态被替身略过）。

        Args:
            plugin_id: 插件标识（未使用）。
            auth: 鉴权上下文（未使用）。

        Returns:
            tuple: `(None, 声明, 企业开关)`。
        """
        del plugin_id, auth
        return None, self.manifest, self.policies


class FakeUsageService:
    """计量服务替身（记录调用次数）。"""

    def __init__(self) -> None:
        """初始化。"""
        self.calls: list[tuple[str, int]] = []

    async def record_automation(
        self, *, tenant_id: str, user_id: str, task_id: str, units: int, source: str = ""
    ) -> bool:
        """记录一次自动化计量。

        Args:
            tenant_id: 企业 ID（未使用）。
            user_id: 用户 ID（未使用）。
            task_id: 任务 ID。
            units: 额度。
            source: 来源（未使用）。

        Returns:
            bool: 恒 True。
        """
        del tenant_id, user_id, source
        self.calls.append((task_id, units))
        return True

    async def quota_notice(self, *, tenant_id: str, user_id: str) -> tuple[int, str]:
        """替身：非阻断额度提示（B27 `15006` 的接线点）。

        默认返回 `(0, "")`：绝大多数任务用例不关心额度提示，
        关心超额的用例请注入真实 `UsageService`（单一真源在那边）。

        Args:
            tenant_id: 企业 ID（未使用）。
            user_id: 用户 ID（未使用）。

        Returns:
            tuple[int, str]: 恒 `(0, "")`（未超额，无提示）。
        """
        del tenant_id, user_id
        return 0, ""


class EchoGateway:
    """按 `produces` 产出文本产物的原语替身（用于推进流程）。

    Attributes:
        calls: 每次调用的 `(action, step_seq)` 记录。
        fail_at: 指定步骤序号返回失败（0 表示都成功）。
    """

    def __init__(self, *, fail_at: int = 0) -> None:
        """初始化。

        Args:
            fail_at: 指定失败步骤序号。
        """
        self.calls: list[tuple[str, int]] = []
        self.fail_at = fail_at

    def resolve(self, action: str) -> str:
        """解析原语（测试恒返回标识）。

        Args:
            action: 原语名。

        Returns:
            str: 固定标识。
        """
        return f"test.{action}"

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """执行原语替身。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 成功产物或失败结果。
        """
        self.calls.append((action, ctx.step_seq))
        if self.fail_at and ctx.step_seq == self.fail_at:
            return ActionResult(
                status="failed",
                error_code=30001,
                reason_text="模拟失败，可以重试",
                operator_action="可以重试这一步",
                retryable=True,
            )
        drafts = tuple(
            ArtifactDraft(
                output_key=str(key),
                type="text",
                name=str(key),
                inline_text=f"第 {ctx.step_seq} 步产出 {key}",
                preview_text=f"{key} 预览",
            )
            for key in params.get("produces") or []
        )
        return ActionResult(status="ok", artifacts=drafts, usage_units=1)


def load_manifest(plugin_id: str) -> PluginManifest:
    """从磁盘声明加载一份 manifest。

    Args:
        plugin_id: 插件标识。

    Returns:
        PluginManifest: 声明。

    Raises:
        AssertionError: 声明不存在（测试环境问题，直接失败更好）。
    """
    registry = PluginRegistry()
    failures = registry.load_all(default_manifests_dir())
    assert not failures, f"声明加载失败：{[item.render() for item in failures]}"
    latest = registry.latest(plugin_id)
    manifest = registry.get(plugin_id, latest)
    assert manifest is not None, f"声明不存在：{plugin_id}"
    return manifest


def fixture_manifest(**overrides: object) -> PluginManifest:
    """构造**最小夹具声明**（失败分支专用，与产品声明解耦）。

    为什么需要：产品声明（`app/plugins/manifests/*.json`）属于 **B20 的交付面**，
    会随装配调整（补 `needs`、改 `on_failure`、调步骤数）。把失败分支的用例钉在
    产品声明上，等于**把"契约判定"降级成"声明文案判定"**——声明一改测试就红，
    而契约其实没变。夹具声明只为这三类判定服务：非最终步失败、`needs` 缺失、产物缺失。

    **成功路径仍必须用真实声明跑**（"三插件能按声明装配"这条不能没人验）。

    Args:
        **overrides: 覆盖字段（如 `steps` / `checklist`）。

    Returns:
        PluginManifest: 夹具声明。
    """
    payload: dict[str, object] = {
        "plugin_id": "plg_fixture_case",
        "name": "夹具插件",
        "summary": "仅用于单测的最小声明",
        "description": "这是一份只用于单元测试的夹具声明。" * 8,
        "category": "dev",
        "icon_key": "code",
        "version": "1.0.0",
        "min_platform_version": "1.0.0",
        "capabilities": [{"key": "personal.tool.use", "required": True}],
        "inputs": [{"key": "topic", "label": "主题", "type": "text", "required": True}],
        "outputs": [
            {"key": "draft", "label": "稿件", "type": "text"},
            {"key": "summary", "label": "小结", "type": "text"},
        ],
        "ui_extension": {
            "layout": "task_workspace",
            "nav_label": "夹具",
            "icon_key": "code",
            "order": 9,
        },
        "steps": [
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft"],
                "on_failure": "continue",
            },
            {
                "seq": 2,
                "title": "提交发布",
                "action": "tool",
                "tool_type": "github",
                "operation": "create_pull_request",
                "produces": [],
                "needs": ["draft"],
                "on_failure": "abort",
            },
        ],
        "checklist": [
            {"key": "draft", "name": "稿件", "source_step": 1},
            {"key": "summary", "name": "小结", "source_step": 1},
        ],
    }
    payload.update(overrides)
    return PluginManifest.model_validate(payload)


def build_service(
    manifest: PluginManifest,
    *,
    gateway: EchoGateway | None = None,
    granted: frozenset[str] = GRANTED,
    concurrency: int = 3,
) -> tuple[TaskService, InMemoryTaskStore, InMemoryArtifactStore, FakeUsageService]:
    """构造一个只依赖内存与替身的 `TaskService`。

    Args:
        manifest: 声明。
        gateway: 原语替身（缺省新建）。
        granted: 已授能力。
        concurrency: 同插件并发上限。

    Returns:
        tuple: `(服务, 任务仓储, 产物仓储, 计量替身)`。
    """
    settings = Settings(
        env="test",
        mock_mode=True,
        plugin_task_concurrency=concurrency,
        plugin_retry_backoff_ms=0,
        # S9（2026-10-23）：产物下载令牌**不再回落硬编码开发密钥**，空 `jwt_secret`
        # 直接拒绝签发。任务详情/步骤响应会为每个产物签发下载地址，因此这里必须
        # 配一个测试密钥——修夹具，不是削弱校验（生产由 `validate_auth_startup`
        # 在启动时保证非空）。
        jwt_secret="task-helper-test-secret",
    )
    tasks = InMemoryTaskStore()
    artifacts = InMemoryArtifactStore()
    usage = FakeUsageService()
    registry = PluginRegistry()
    registry.load_all(default_manifests_dir())
    # 夹具声明不在磁盘上：显式注册进注册表（`_manifest_of` 按 (id, version) 取声明）
    if registry.get(manifest.plugin_id, manifest.version) is None:
        registry.load_manifest(manifest.model_dump(mode="json", by_alias=True), source="fixture")
    service = TaskService(
        settings,
        tasks=tasks,
        artifacts=artifacts,
        registry=registry,
        grants=FakeGrantService(granted),  # type: ignore[arg-type]
        plugins=FakePluginService(manifest),  # type: ignore[arg-type]
        engine=PluginEngine(gateway or EchoGateway(), retry_backoff_ms=0),
        audit=AuditService(settings),
        usage=usage,  # type: ignore[arg-type]
    )
    return service, tasks, artifacts, usage


def auth(user_id: str = USER) -> AuthContext:
    """构造鉴权上下文。

    Args:
        user_id: 用户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=TENANT)


__all__: list[str] = [
    "GRANTED",
    "TENANT",
    "USER",
    "EchoGateway",
    "FakeGrantService",
    "FakePluginService",
    "FakeUsageService",
    "auth",
    "build_service",
    "fixture_manifest",
    "load_manifest",
]
