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
    """

    plugin_id: str = ""
    status: str = "enabled"
    version: str = "1.0.0"
    granted_capabilities: list[str] = Field(default_factory=list, description="已授权能力")
    denied_capabilities: list[str] = Field(default_factory=list, description="未授权能力")


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
    "SettingBriefItem",
    "VersionBriefItem",
]
