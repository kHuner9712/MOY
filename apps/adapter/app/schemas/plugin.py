"""插件域接口契约（阶段三 B17，`architecture-phase3.md` §5.2 PL1-PL5 / PL13）。

**用户可见文案一律不出现技术词**（AC-06）：能力项的 `display_name` / `reason` /
`denial_effect` 来自 `plugins/actions.CAPABILITY_CATALOG`，不在这里硬编码；
本文件只定义**形状**。

**`org_switch_enabled` 必须由端点返回**（PRD v1.1 勘误 #2）：前端禁止硬编码
`true` 或按套餐推导企业能力开关状态——它是逐能力字段，不是插件级单一布尔。

B18 会追加 PL6-PL12 的模型（停用/启用/卸载/升级/回退/设置/评价）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: 市场排序方式
MarketSort = Literal["recommended", "newest", "rating"]

#: 安装态（`none` 表示尚未添加）
InstalledStatus = Literal["none", "enabled", "disabled", "installed", "uninstalled"]

#: 能力分层（个人 / 企业）
CapabilityCategory = Literal["personal", "org"]

#: 插件分类
PluginCategory = Literal["dev", "content", "hr", "data", "collab", "other"]


class PluginCapabilityItem(BaseModel):
    """授权抽屉里的一条能力（三要素 + 开关态）。

    Attributes:
        capability_key: 能力键（**不进 DOM**，前端用 map 转名称）。
        display_name: 能力名称。
        reason: 为什么需要。
        denial_effect: 不同意的后果。
        category: 个人 / 企业。
        required: 是否必需。
        org_switch_enabled: 企业是否已开放（个人能力恒 True）。
    """

    capability_key: str = Field(default="", description="能力键")
    display_name: str = Field(default="", description="能力名称")
    reason: str = Field(default="", description="为什么需要")
    denial_effect: str = Field(default="", description="不同意的后果")
    category: CapabilityCategory = Field(default="personal", description="能力分层")
    required: bool = Field(default=True, description="是否必需")
    org_switch_enabled: bool = Field(default=True, description="企业是否已开放")


class MarketItem(BaseModel):
    """市场列表条目（PL1）。

    Attributes:
        plugin_id: 插件标识。
        name: 插件名称。
        summary: 一句话说明。
        category: 分类。
        icon_key: 图标名。
        rating_avg: 平均评分。
        rating_count: 评分人数。
        latest_version: 最新版本。
        installed_status: 当前企业安装态。
        has_update: 是否有可更新版本。
        installable: 是否可安装（套餐 / 上架状态决定；**免费版也返回 false 而不是不展示**）。
        install_block_reason: 不可安装的原因（业务语言，可直接展示）。
    """

    plugin_id: str = ""
    name: str = ""
    summary: str = ""
    category: str = ""
    icon_key: str = ""
    rating_avg: float = 0.0
    rating_count: int = 0
    latest_version: str = "1.0.0"
    installed_status: InstalledStatus = "none"
    has_update: bool = False
    installable: bool = True
    install_block_reason: str = ""


class CategoryCountItem(BaseModel):
    """市场分类计数（横筛用）。

    Attributes:
        key: 分类键。
        name: 分类名称。
        count: 该分类插件数。
    """

    key: str = ""
    name: str = ""
    count: int = 0


class MarketData(BaseModel):
    """市场列表响应（PL1）。

    Attributes:
        items: 条目列表。
        categories: 分类计数（**不随分页变化**，前端横筛常显）。
        page: 页码。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    items: list[MarketItem] = Field(default_factory=list, description="条目列表")
    categories: list[CategoryCountItem] = Field(default_factory=list, description="分类计数")
    org_capabilities: list[PluginCapabilityItem] = Field(
        default_factory=list, description="企业能力开关（单一读源，3 项，含 org_switch_enabled）"
    )
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=12, ge=1, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class OutputBriefItem(BaseModel):
    """详情页展示的产物说明（PL2）。

    Attributes:
        key: 产物键。
        label: 产物名称。
        type: 产物类型。
    """

    key: str = ""
    label: str = ""
    type: str = "text"


class InputBriefItem(BaseModel):
    """详情页展示的输入声明（PL2，驱动宿主表单）。

    Attributes:
        key: 输入键（与任务 inputs 的键集合一致）。
        label: 输入名称。
        type: 控件类型（text/textarea/select/multi_select/email/file）。
        required: 是否必填。
        max_length: 文本类长度上限。
        options: select / multi_select 候选值。
    """

    key: str = ""
    label: str = ""
    type: str = "text"
    required: bool = False
    max_length: int | None = None
    options: list[str] = Field(default_factory=list, description="候选值")


class SettingBriefItem(BaseModel):
    """详情页展示的配置项说明（PL2）。

    Attributes:
        key: 配置键。
        label: 配置名称。
        type: 控件类型。
        options: 候选值。
    """

    key: str = ""
    label: str = ""
    type: str = "text"
    options: list[str] = Field(default_factory=list, description="候选值")


class VersionBriefItem(BaseModel):
    """版本历史条目（PL2）。

    Attributes:
        version: 版本号。
        published_at: 发布时间。
        changelog: 版本说明。
    """

    version: str = ""
    published_at: str = ""
    changelog: str = ""


class MarketDetailData(BaseModel):
    """市场详情（PL2）。

    Attributes:
        plugin_id: 插件标识。
        name: 插件名称。
        summary: 一句话说明。
        description: 详细说明。
        category: 分类。
        icon_key: 图标名。
        latest_version: 最新版本。
        rating_avg: 平均评分。
        rating_count: 评分人数。
        install_count: 安装企业数。
        capabilities: 能力清单（含开关态）。
        inputs: 输入声明（驱动宿主表单）。
        step_titles: 步骤名称清单（用户可见）。
        outputs: 产物说明。
        settings_schema: 配置项说明。
        versions: 版本历史。
        installed_status: 当前安装态。
        installed_version: 已安装版本。
        has_update: 是否有可更新版本。
        installable: 是否可安装。
        install_block_reason: 不可安装原因。
    """

    plugin_id: str = ""
    name: str = ""
    summary: str = ""
    description: str = ""
    category: str = ""
    icon_key: str = ""
    latest_version: str = "1.0.0"
    rating_avg: float = 0.0
    rating_count: int = 0
    install_count: int = 0
    capabilities: list[PluginCapabilityItem] = Field(default_factory=list, description="能力清单")
    inputs: list[InputBriefItem] = Field(default_factory=list, description="输入声明")
    step_titles: list[str] = Field(default_factory=list, description="步骤名称")
    outputs: list[OutputBriefItem] = Field(default_factory=list, description="产物说明")
    settings_schema: list[SettingBriefItem] = Field(default_factory=list, description="配置项")
    versions: list[VersionBriefItem] = Field(default_factory=list, description="版本历史")
    installed_status: InstalledStatus = "none"
    installed_version: str = ""
    has_update: bool = False
    installable: bool = True
    install_block_reason: str = ""


class InstalledItem(BaseModel):
    """已安装列表条目（PL3）。

    Attributes:
        plugin_id: 插件标识。
        name: 插件名称。
        category: 分类。
        icon_key: 图标名。
        version: 当前版本。
        latest_version: 最新版本。
        has_update: 是否有可更新版本。
        status: 安装态。
        last_used_at: 最近使用时间。
        running_task_count: 在跑任务数。
    """

    plugin_id: str = ""
    name: str = ""
    category: str = ""
    icon_key: str = ""
    version: str = "1.0.0"
    latest_version: str = "1.0.0"
    has_update: bool = False
    status: str = "enabled"
    last_used_at: str = ""
    running_task_count: int = 0


class InstalledData(BaseModel):
    """已安装列表响应（PL3）。

    Attributes:
        items: 条目列表。
        page: 页码。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    items: list[InstalledItem] = Field(default_factory=list, description="条目列表")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class CapabilityListData(BaseModel):
    """能力清单（PL4）。

    Attributes:
        items: 能力清单（含 `org_switch_enabled`）。
        granted_capabilities: 当前成员已授权的能力键。
        org_switch_updated_at: 企业开关最近更新时间。
        installable: 是否可安装。
        install_block_reason: 不可安装原因。
    """

    items: list[PluginCapabilityItem] = Field(default_factory=list, description="能力清单")
    granted_capabilities: list[str] = Field(default_factory=list, description="已授权能力")
    org_switch_updated_at: str = Field(default="", description="企业开关更新时间")
    installable: bool = Field(default=True, description="是否可安装")
    install_block_reason: str = Field(default="", description="不可安装原因")


class InstallRequest(BaseModel):
    """安装请求（PL5）。

    Attributes:
        grants: 能力 → 是否同意（必须覆盖全部必需能力）。
        settings: 插件配置（键须 ⊆ `settings_schema`）。
    """

    model_config = ConfigDict(extra="forbid")

    grants: dict[str, bool] = Field(default_factory=dict, description="能力授权意图")
    settings: dict[str, Any] = Field(default_factory=dict, description="插件配置")


class InstallData(BaseModel):
    """安装结果（PL5）。

    Attributes:
        plugin_id: 插件标识。
        status: 安装态（安装即启用）。
        version: 安装版本。
        granted_capabilities: 已授权能力。
        denied_capabilities: 未授权能力。
        already_installed: 是否「本来就已添加」（首次 false / 重复 true，字段恒有）。
    """

    plugin_id: str = ""
    status: str = "enabled"
    version: str = "1.0.0"
    granted_capabilities: list[str] = Field(default_factory=list, description="已授权能力")
    denied_capabilities: list[str] = Field(default_factory=list, description="未授权能力")
    already_installed: bool = Field(default=False, description="是否重复添加")


class CapabilityPolicyItem(BaseModel):
    """企业能力开关条目（PL13）。

    Attributes:
        capability_key: 能力键。
        display_name: 能力名称。
        enabled: 是否开启。
        updated_by_name: 最后修改人。
        updated_at: 最后修改时间。
    """

    capability_key: str = ""
    display_name: str = ""
    enabled: bool = False
    updated_by_name: str = ""
    updated_at: str = ""


class CapabilityPolicyRequestItem(BaseModel):
    """企业能力开关写入项（PL13 请求元素）。

    Attributes:
        capability_key: 能力键（**仅 `org.*`**）。
        enabled: 是否开启。
    """

    capability_key: str = Field(min_length=3, max_length=64, description="能力键")
    enabled: bool = Field(description="是否开启")


class CapabilityPolicyRequest(BaseModel):
    """企业能力开关写入请求（PL13）。

    Attributes:
        policies: 开关列表。
    """

    model_config = ConfigDict(extra="forbid")

    policies: list[CapabilityPolicyRequestItem] = Field(
        min_length=1, max_length=3, description="开关列表"
    )


class CapabilityPolicyData(BaseModel):
    """企业能力开关响应（PL13，写后回读全量）。

    Attributes:
        policies: 全量开关（顺序固定为 ④⑤⑥）。
    """

    policies: list[CapabilityPolicyItem] = Field(default_factory=list, description="全量开关")


__all__: list[str] = [
    "CapabilityCategory",
    "CapabilityListData",
    "CapabilityPolicyData",
    "CapabilityPolicyItem",
    "CapabilityPolicyRequest",
    "CapabilityPolicyRequestItem",
    "CategoryCountItem",
    "GrantToggleItem",
    "InstallData",
    "InstallRequest",
    "InstalledData",
    "InstalledItem",
    "InstalledStatus",
    "MarketData",
    "MarketDetailData",
    "MarketItem",
    "MarketSort",
    "OutputBriefItem",
    "PluginCapabilityItem",
    "PluginCategory",
    "ReviewData",
    "ReviewItem",
    "ReviewListData",
    "ReviewRequest",
    "SettingBriefItem",
    "SettingsData",
    "SettingsRequest",
    "StateData",
    "StateRequest",
    "UninstallData",
    "UpdateGrantsData",
    "UpdateGrantsRequest",
    "UpgradeRequest",
    "VersionBriefItem",
    "VersionData",
]


# ===========================================================================
# PL6-PL12（B18）：生命周期、设置与评价
# ===========================================================================
class StateRequest(BaseModel):
    """启用/停用请求（PL6）。

    Attributes:
        status: 目标状态（`enabled` / `disabled`）。
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["enabled", "disabled"] = Field(description="目标状态")


class StateData(BaseModel):
    """启用/停用结果（PL6）。

    Attributes:
        plugin_id: 插件标识。
        status: 变更后状态。
        running_task_count: 在跑任务数（停用不打断它们）。
    """

    plugin_id: str = ""
    status: str = "enabled"
    running_task_count: int = 0


class UninstallData(BaseModel):
    """卸载结果（PL7）。

    Attributes:
        plugin_id: 插件标识。
        uninstalled: 是否已卸载。
        running_task_count: 仍在跑的任务数（会继续完成）。
        kept_task_count: 保留的历史任务数（确认弹窗展示）。
    """

    plugin_id: str = ""
    uninstalled: bool = True
    running_task_count: int = 0
    kept_task_count: int = 0


class UpgradeRequest(BaseModel):
    """升级请求（PL8）。

    Attributes:
        target_version: 目标版本。
    """

    model_config = ConfigDict(extra="forbid")

    target_version: str = Field(min_length=5, max_length=16, description="目标版本")


class VersionData(BaseModel):
    """版本变更结果（PL8 / PL9）。

    Attributes:
        plugin_id: 插件标识。
        version: 当前版本。
        previous_version: 上一版本。
        has_update: 是否仍有更新可用。
    """

    plugin_id: str = ""
    version: str = "1.0.0"
    previous_version: str = ""
    has_update: bool = False


class SettingsRequest(BaseModel):
    """插件配置写入请求（PL10）。

    Attributes:
        settings: 配置键值（键须 ⊆ 声明 `settings_schema`）。
    """

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict, description="配置键值")


class SettingsData(BaseModel):
    """插件配置结果（PL10）。

    Attributes:
        plugin_id: 插件标识。
        settings: 当前配置。
        updated_at: 更新时间。
    """

    plugin_id: str = ""
    settings: dict[str, Any] = Field(default_factory=dict, description="当前配置")
    updated_at: str = ""


class ReviewItem(BaseModel):
    """评价条目（PL11）。

    Attributes:
        review_id: 评价 ID。
        rating: 评分。
        content: 评价内容。
        author_name: 展示名（匿名时为「某位同事」）。
        department_name: 部门展示名（匿名时为空）。
        created_at: 提交时间。
    """

    review_id: str = ""
    rating: int = 5
    content: str = ""
    author_name: str = ""
    department_name: str = ""
    created_at: str = ""


class ReviewListData(BaseModel):
    """评价列表（PL11）。

    Attributes:
        items: 评价条目。
        rating_avg: 平均分。
        rating_count: 评价条数。
        page: 页码。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    items: list[ReviewItem] = Field(default_factory=list, description="评价条目")
    rating_avg: float = Field(default=0.0, ge=0.0, description="平均分")
    rating_count: int = Field(default=0, ge=0, description="评价条数")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=10, ge=1, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class ReviewRequest(BaseModel):
    """提交评价请求（PL12）。

    Attributes:
        rating: 评分（1-5）。
        content: 评价内容（≤200 字）。
        anonymous: 是否匿名。
    """

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5, description="评分")
    content: str = Field(default="", max_length=200, description="评价内容")
    anonymous: bool = Field(default=False, description="是否匿名")


class ReviewData(BaseModel):
    """提交评价结果（PL12）。

    Attributes:
        review_id: 评价 ID。
        rating: 评分。
        created_at: 提交时间。
        rating_avg: 更新后的平均分。
        rating_count: 更新后的评价条数。
    """

    review_id: str = ""
    rating: int = 5
    created_at: str = ""
    rating_avg: float = 0.0
    rating_count: int = 0


# ===========================================================================
# PL14（架构 v2 增补）：更新本人授权
# ===========================================================================
class GrantToggleItem(BaseModel):
    """单条授权增减意图（PL14 请求元素）。

    Attributes:
        capability_key: 能力键。
        granted: 是否授予。
    """

    capability_key: str = Field(min_length=3, max_length=64, description="能力键")
    granted: bool = Field(description="是否授予")


class UpdateGrantsRequest(BaseModel):
    """更新授权请求（PL14）。

    Attributes:
        grants: 增减意图列表。
    """

    model_config = ConfigDict(extra="forbid")

    grants: list[GrantToggleItem] = Field(min_length=1, max_length=6, description="增减意图")


class UpdateGrantsData(BaseModel):
    """更新授权结果（PL14）。

    Attributes:
        granted_capabilities: 当前已授予。
        denied_capabilities: 当前未授予。
        required_capabilities: 该插件的必需能力（不可撤）。
    """

    granted_capabilities: list[str] = Field(default_factory=list, description="已授予")
    denied_capabilities: list[str] = Field(default_factory=list, description="未授予")
    required_capabilities: list[str] = Field(default_factory=list, description="必需能力")
