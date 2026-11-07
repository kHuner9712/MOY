"""会话级能力集与四层授权的基础模型（B6）。

**分层定义（判定顺序固定）**：

1. 租户层：请求生效租户必须等于令牌租户，`__platform__` 额外要求运营权限点；
2. 角色/权限点层：操作所需权限点必须由调用者角色展开得到；
3. 会话能力层：本次会话/任务声明的工具、资源类型、产物格式，必须是
   「第 2 层派生上界」的**子集**——只能收窄，永不可放大；
4. 资源层：具体对象的租户、归属人、状态校验。

四层是**交集**：任一层拒绝即整体拒绝，首拒即停；下层不得绕过上层。

本模块只放纯数据与判定，不 import 其他 `app` 模块（`core` 必须是最底层）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

#: 四层判定的固定顺序（任何调用方不得调整）。
AUTHZ_LAYER_ORDER: Final[tuple[str, ...]] = ("tenant", "permission", "capability", "resource")

#: 能力声明的三个维度。
CAPABILITY_DIMENSIONS: Final[tuple[str, ...]] = ("tools", "resource_types", "artifact_formats")

#: 已知工具类型（与 `app/registry/tool_registry.yaml` + MCP 工具类型保持同步）。
#: 新增工具类型必须先登记注册表，再扩展本集合；测试 `test_capability_catalog`
#: 会拦住两处漂移。
KNOWN_TOOL_TYPES: Final[frozenset[str]] = frozenset(
    {"email", "github", "wecom", "feishu", "wechat_mp", "mcp"}
)

#: 已知资源类型。个人资源（conversation/memory/task/artifact）恒可声明，
#: 但仍要在资源层校验归属；企业资源按权限点收窄。
KNOWN_RESOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "knowledge_base",
        "document",
        "conversation",
        "memory",
        "task",
        "artifact",
        "plugin",
        "tool_connection",
        "team",
        "billing",
        "audit_log",
        "ops",
    }
)

#: 已知产物格式（插件 `render_format` 四项 + 导出 pdf）。
KNOWN_ARTIFACT_FORMATS: Final[frozenset[str]] = frozenset({"docx", "xlsx", "md", "csv", "pdf"})

#: 单个维度允许的声明条目上限（防止超长请求体把审计与比较拖慢）。
MAX_CAPABILITY_ITEMS: Final[int] = 32

#: 单条能力标识的最大长度。
MAX_CAPABILITY_ITEM_LENGTH: Final[int] = 64


class AuthzLayer(StrEnum):
    """四层授权中的层标识（取值即审计里记录的 `layer`）。"""

    TENANT = "tenant"
    PERMISSION = "permission"
    CAPABILITY = "capability"
    RESOURCE = "resource"


@dataclass(frozen=True, slots=True)
class CapabilityViolation:
    """声明能力越过上界的一项。

    Attributes:
        dimension: 维度名（`tools` / `resource_types` / `artifact_formats`）。
        value: 越界的声明值（只记录能力标识，不是用户输入正文）。
    """

    dimension: str
    value: str

    def as_dict(self) -> dict[str, str]:
        """序列化为审计可落库的字典。

        Returns:
            dict[str, str]: 仅含维度与值，不含任何请求参数明文。
        """
        return {"dimension": self.dimension, "value": self.value}


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """会话/任务的能力声明。

    **`None` 的语义**：某个维度为 `None` 表示「未声明该维度」，按调用者权限
    继承；一旦声明对象存在，schema 层要求三个维度全部给出（空数组表示该维度
    全部拒绝），避免「省略即继承」被用来绕过收窄。

    Attributes:
        tools: 允许调用的工具类型集合。
        resource_types: 允许访问的资源类型集合。
        artifact_formats: 允许产出的产物格式集合。
    """

    tools: frozenset[str] | None = None
    resource_types: frozenset[str] | None = None
    artifact_formats: frozenset[str] | None = None

    def __post_init__(self) -> None:
        """把可迭代输入统一为 frozenset（保留 `None` 的「未声明」语义）。"""
        for dimension in CAPABILITY_DIMENSIONS:
            value = getattr(self, dimension)
            if value is not None and not isinstance(value, frozenset):
                object.__setattr__(self, dimension, frozenset(value))

    @property
    def declared(self) -> bool:
        """是否至少声明了一个维度。

        Returns:
            bool: 三个维度都不是 `None` 时为 True。
        """
        return any(getattr(self, dimension) is not None for dimension in CAPABILITY_DIMENSIONS)

    @property
    def fully_declared(self) -> bool:
        """三个维度是否全部声明（schema 提交的声明必须满足）。

        Returns:
            bool: 三维度均非 `None` 时为 True。
        """
        return all(getattr(self, dimension) is not None for dimension in CAPABILITY_DIMENSIONS)

    def get(self, dimension: str) -> frozenset[str] | None:
        """读取某个维度的声明值。

        Args:
            dimension: `tools` / `resource_types` / `artifact_formats`。

        Returns:
            frozenset[str] | None: 声明值；维度未声明时为 None。

        Raises:
            ValueError: 维度名不在目录内（调用方写错，属编程错误）。
        """
        if dimension not in CAPABILITY_DIMENSIONS:
            raise ValueError(f"unknown capability dimension: {dimension}")
        value: frozenset[str] | None = getattr(self, dimension)
        return value

    def violations(self, upper: CapabilitySet) -> tuple[CapabilityViolation, ...]:
        """列出声明越过上界的所有项。

        **不裁剪、不吞掉**：调用方必须据此拒绝请求（业务码 14009），而不是把
        声明与上界求交后继续执行——静默夹紧会让客户端以为「声明生效了」，
        实际执行范围与它提交的不一致。

        Args:
            upper: 上层（权限点）派生出的能力上界，三个维度均为集合。

        Returns:
            tuple[CapabilityViolation, ...]: 越界项；空元组表示声明是上界子集。
        """
        violations: list[CapabilityViolation] = []
        for dimension in CAPABILITY_DIMENSIONS:
            declared = getattr(self, dimension)
            if declared is None:
                continue
            allowed = getattr(upper, dimension)
            allowed_values = allowed if allowed is not None else frozenset()
            for value in sorted(declared - allowed_values):
                violations.append(CapabilityViolation(dimension=dimension, value=value))
        return tuple(violations)

    def allows(self, dimension: str, value: str) -> bool:
        """判断某个具体值是否被本次声明允许。

        未声明该维度时返回 True（继承调用者权限，由上层判定决定），
        已声明时严格按集合判断（空集合表示全部拒绝）。

        Args:
            dimension: 能力维度。
            value: 待判定的工具类型 / 资源类型 / 产物格式。

        Returns:
            bool: 允许为 True。
        """
        declared = self.get(dimension)
        return declared is None or value in declared

    def as_dict(self) -> dict[str, list[str]]:
        """序列化为可落库 JSONB 的字典（仅保留已声明的维度）。

        Returns:
            dict[str, list[str]]: 维度名 → 排序后的值列表。
        """
        payload: dict[str, list[str]] = {}
        for dimension in CAPABILITY_DIMENSIONS:
            declared = getattr(self, dimension)
            if declared is not None:
                payload[dimension] = sorted(declared)
        return payload

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> CapabilitySet:
        """从数据库 JSONB / 请求字典构造能力集。

        Args:
            payload: 形如 `{"tools": [...], "resource_types": [...], ...}` 的字典；
                空字典表示未声明（继承）。

        Returns:
            CapabilitySet: 能力集实例。

        Raises:
            ValueError: 维度值不是数组，或维度名不在目录内。
        """
        if not payload:
            return cls()
        unknown = set(payload) - set(CAPABILITY_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown capability dimension: {sorted(unknown)}")
        values: dict[str, frozenset[str] | None] = {}
        for dimension in CAPABILITY_DIMENSIONS:
            raw = payload.get(dimension)
            if raw is None:
                values[dimension] = None
                continue
            if not isinstance(raw, (list, tuple, set, frozenset)):
                raise ValueError(f"capability dimension {dimension} must be an array")
            values[dimension] = frozenset(str(item) for item in raw)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """资源层校验所需的对象引用。

    Attributes:
        resource_type: 资源类型（∈ `KNOWN_RESOURCE_TYPES`）。
        resource_id: 对象标识（仅用于审计定位，不记录对象内容）。
        tenant_id: 对象所属租户。
        owner_id: 对象归属人（个人资源必填；企业共享资源可空）。
        state: 对象当前状态（如插件安装 `enabled`）。
        required_state: 操作要求的状态；为空表示不校验状态。
        owner_only: 是否仅允许归属人本人访问。
    """

    resource_type: str
    resource_id: str = ""
    tenant_id: str = ""
    owner_id: str = ""
    state: str = ""
    required_state: str = ""
    owner_only: bool = False

    def as_audit_fields(self) -> dict[str, str]:
        """审计只记录类型与标识，不记录对象内容或业务参数。

        Returns:
            dict[str, str]: 资源类型与对象标识。
        """
        return {"resource_type": self.resource_type, "resource_id": self.resource_id}


@dataclass(frozen=True, slots=True)
class AuthzRequest:
    """一次四层授权判定的输入（不含任何请求正文）。

    Attributes:
        tenant_id: 本次操作生效的租户（通常等于令牌租户）。
        permission: 操作所需权限点；空串表示本层不额外要求。
        declared: 会话能力声明；None 表示继承调用者权限。
        required_tools: 本次操作实际会用到的工具类型。
        required_resource_types: 本次操作实际会访问的资源类型。
        required_artifact_formats: 本次操作实际会产出的产物格式。
        resource: 具体资源引用；None 表示无资源层对象。
        operation: 业务操作名（审计用，如 `agent.task.create`）。
    """

    tenant_id: str = ""
    permission: str = ""
    declared: CapabilitySet | None = None
    required_tools: frozenset[str] = field(default_factory=frozenset)
    required_resource_types: frozenset[str] = field(default_factory=frozenset)
    required_artifact_formats: frozenset[str] = field(default_factory=frozenset)
    resource: ResourceRef | None = None
    operation: str = ""


__all__: list[str] = [
    "AUTHZ_LAYER_ORDER",
    "CAPABILITY_DIMENSIONS",
    "KNOWN_ARTIFACT_FORMATS",
    "KNOWN_RESOURCE_TYPES",
    "KNOWN_TOOL_TYPES",
    "MAX_CAPABILITY_ITEMS",
    "MAX_CAPABILITY_ITEM_LENGTH",
    "AuthzLayer",
    "AuthzRequest",
    "CapabilitySet",
    "CapabilityViolation",
    "ResourceRef",
]
