"""插件声明模型（阶段三 B16，`architecture-phase3.md` §2.1 / §2.3）。

**为什么是"声明"而不是"代码"**（D-14 / PRD B-2）：插件只含 JSON 声明 + 平台能力引用，
没有 `entry_point` / `module` / `image` / `command` / `deps` 任何可执行载荷字段。
因此本文件的每个模型都 `extra="forbid"`——多写一个字段即加载失败，
这是"字段级不存在"（§4.5）在代码层的机械保证，而不是靠评审自觉。

**两层分离**（D-15，本阶段最关键的设计决策）：

| 层 | 字段 | 谁看 | 取值 |
| --- | --- | --- | --- |
| 授权能力 | `capabilities[].key` | 用户（授权抽屉逐项同意） | 6 项封闭枚举（个人 3 + 企业 3） |
| 执行原语 | `steps[].action` | 平台（永不进授权页） | 6 项封闭白名单 |

若把执行原语当授权项，用户看到的是「生成代码」而不是「它要读你什么」，
既无法回答授权边界，也把技术词汇带上界面（违反 AC-06）。

**校验分层**：本文件的 `field_validator` / `model_validator` 只做**形态**校验
（格式、枚举、长度、序号连续、产物引用闭合）；跨字段的**业务一致性**
（能力 ↔ 原语的"要了必须用 / 用了必须被授"）由 `loader.py` 与 G-30 门禁负责，
这样单元测试可以只构造形态非法的样本，不必牵动能力映射表。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 插件标识正则（`plg_` + 小写字母开头 + 2-40 位小写字母/数字/下划线）
PLUGIN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^plg_[a-z][a-z0-9_]{2,40}$")

#: 语义化版本正则（本阶段只接受 `x.y.z`，不接受预发布标签）
SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+\.\d+\.\d+$")

#: 市场分类（PRD P0-P3-06 六分类，封闭）
CATEGORIES: Final[tuple[str, ...]] = ("dev", "content", "hr", "data", "collab", "other")

#: 输入控件类型（驱动宿主表单，PRD P0-P3-24）
INPUT_TYPES: Final[tuple[str, ...]] = (
    "text",
    "textarea",
    "select",
    "multi_select",
    "email",
    "file",
)

#: 产物类型（驱动结果区渲染）
OUTPUT_TYPES: Final[tuple[str, ...]] = ("text", "link", "file", "table")

#: 配置项类型
SETTING_TYPES: Final[tuple[str, ...]] = ("text", "select")

#: 界面布局（仅 1 个值：新增布局=能力扩张，是故意的）
UI_LAYOUTS: Final[tuple[str, ...]] = ("task_workspace",)

#: 失败处置策略
ON_FAILURE_VALUES: Final[tuple[str, ...]] = (
    "abort",
    "continue",
    "skip_and_mark",
    "retry",
)

#: 步骤数上限（PRD Q3-7）
MAX_STEPS: Final[int] = 8

#: 核查清单项数范围（P0-P3-14）
MIN_CHECKLIST: Final[int] = 2
MAX_CHECKLIST: Final[int] = 5

#: 步骤标题/文案最大长度
MAX_TITLE_LENGTH: Final[int] = 32

#: 插件描述长度下限（PRD P0-P3-07 要求 ≥120 字）与上限
MIN_DESCRIPTION_LENGTH: Final[int] = 120
MAX_DESCRIPTION_LENGTH: Final[int] = 800

#: 中文功能字（虚词 / 量词 / 代词）：**两字均为功能字**的 2-gram 不携带
#: 领域信号（「一个」「什么」「我们」），goal 切分时直接丢弃；
#: `match_hints` 也不接受纯功能字提示（那不携带任何领域信号）。
#:
#: 为什么住在 schema 而不是 `goal_router`：功能字表同时约束**声明层**
#: （`match_hints` 形态校验）与**推荐层**（tokenize 过滤），两处各存一份
#: 必然漂移——声明放进来的提示词推荐层却过滤掉，是最难发现的一类假护栏。
#: 保守口径：只在**全部字符都是功能字**时丢弃——「本周」「整理」
#: 「报告」这类领域词两字不都在表里，不受影响。
FUNCTION_CHARS: Final[frozenset[str]] = frozenset(
    "的一了是把被让向从到和与及或在是有没不也就都还再又着过吗呢吧啊"
    "你我他她它们这那哪谁什么怎样请帮想只要能会可以给为于按据跟同比"
    "起来去得地如若虽但而且因所之则等各每该此其"
    "个些"
)

#: 匹配提示（`match_hints`）单条字数范围与条数上限（插件声明与工具注册表同口径）
MATCH_HINT_MIN_LEN: Final[int] = 2
MATCH_HINT_MAX_LEN: Final[int] = 8
MATCH_HINT_MAX_COUNT: Final[int] = 12

#: 合规规则声明数上限（P1-10：一插件最多 8 条护栏声明）
MAX_GUARDS: Final[int] = 8

#: 禁止出现的字段名（**字段级不存在**清单 §4.5 的机械兜底）。
#:
#: 即便有人绕过 `extra="forbid"`（例如把载荷塞进 `settings` 值的 JSON 里），
#: 加载器仍会对整份原始 JSON 做一次键名扫描；这里列出的是"只要出现就一定是错的"。
FORBIDDEN_KEYS: Final[tuple[str, ...]] = (
    # 凭据类（安全红线 §3.6）
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "credential",
    "credential_ref",
    "access_token",
    "bearer",
    # 外部地址类（插件不得直连任何底座 / 外部端点）
    "endpoint",
    "base_url",
    "url",
    "host",
    "dsn",
    # 代码执行类（PRD B-2）
    "entry",
    "entry_point",
    "module",
    "module_path",
    "handler",
    "script",
    "image",
    "command",
    "deps",
    "dependencies",
    "bundle_url",
    "source_url",
    "download_url",
)

#: 禁词/占位词（PRD §8 第 19-30 行；与 `deploy/validate_plugins.py` 共用同一份）。
#:
#: 命中即**拒绝加载**（不是警告）：这些词一旦进声明文案，就会出现在用户界面上，
#: 违反 AC-06「无技术暴露」。清单刻意包含「编排 / 节点 / 回滚 / 依赖」这类
#: 工程师觉得自然、用户看不懂的词。
BANNED_TERMS: Final[tuple[str, ...]] = (
    "编排",
    "节点",
    "manifest",
    "回滚",
    "依赖",
    "sha",
    "sha256",
    "PR",
    "prompt",
    "向量",
    "召回",
    "相似度",
    "embedding",
    "chunk",
    "segment",
    "token",
    "RAG",
    "LLM",
    "大模型",
    "api",
    "API",
    "json",
    "JSON",
    "schema",
    "schema 校验",
    "工作流引擎",
    "沙箱",
    "插件运行时",
    "调用链",
    "底层",
    "接口",
    "参数",
    "字段",
    "配置项",
    "待补充",
    "TODO",
    "TBD",
    "占位",
    "示例文案",
    "xxx",
    "XX",
    "敬请期待",
)

#: 文案字段（用户可见）——禁词表只扫这些，技术字段（`action`/`tool_type`）不扫
TEXT_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "summary",
    "description",
    "changelog",
    "steps[].title",
    "inputs[].label",
    "outputs[].label",
    "checklist[].name",
    "settings_schema[].label",
    "ui_extension.nav_label",
)


class _StrictModel(BaseModel):
    """所有声明模型的基类：禁止未声明字段（`extra="forbid"`）。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CapabilityClaim(_StrictModel):
    """授权能力声明（用户要同意的数据/写入边界）。

    Attributes:
        key: 能力键，必须 ⊆ 6 项封闭枚举（由 `loader` 与 `actions.CAPABILITY_KEYS` 校验）。
        required: 是否必需；`false` 表示用户可以不勾（本阶段 6 项声明均为必需）。
    """

    key: str = Field(min_length=3, max_length=64, description="能力键")
    required: bool = Field(default=True, description="是否必需")


class SettingSpec(_StrictModel):
    """插件配置项声明（默认仓库/分支/渠道等）。

    Attributes:
        key: 配置键（写入 `plugin_installation.settings` 的键集合）。
        label: 用户可见名称。
        type: 控件类型（`text` / `select`）。
        options: `select` 的可选值。
    """

    key: str = Field(min_length=1, max_length=64, description="配置键")
    label: str = Field(min_length=1, max_length=32, description="配置名称")
    type: Literal["text", "select"] = Field(description="控件类型")
    options: list[str] = Field(default_factory=list, max_length=20, description="可选值")

    @model_validator(mode="after")
    def _select_needs_options(self) -> SettingSpec:
        """`select` 必须给出候选值，`text` 不得给。

        Returns:
            SettingSpec: 自身。

        Raises:
            ValueError: 类型与候选值不匹配。
        """
        if self.type == "select" and not self.options:
            raise ValueError(f"配置项 {self.key} 为下拉选择但未给出可选值")
        if self.type == "text" and self.options:
            raise ValueError(f"配置项 {self.key} 为文本输入但给出了可选值")
        return self


class InputSpec(_StrictModel):
    """输入项声明（驱动宿主表单）。

    Attributes:
        key: 输入键（任务 `inputs` 的键集合与之一致）。
        label: 用户可见名称。
        type: 控件类型。
        required: 是否必填。
        max_length: 文本类输入的长度上限。
        options: `select` / `multi_select` 的候选值。
        max_items: 多值输入（`file` / `multi_select`）的数量上限。
    """

    key: str = Field(min_length=1, max_length=64, description="输入键")
    label: str = Field(min_length=1, max_length=32, description="输入名称")
    type: Literal["text", "textarea", "select", "multi_select", "email", "file"] = Field(
        description="控件类型"
    )
    required: bool = Field(default=False, description="是否必填")
    max_length: int | None = Field(default=None, ge=1, le=8000, description="长度上限")
    options: list[str] = Field(default_factory=list, max_length=20, description="候选值")
    max_items: int | None = Field(
        default=None, ge=1, le=200, description="数量上限（file / multi_select）"
    )
    format: Literal["", "repo", "department"] = Field(
        default="",
        description="候选值的格式约束（空候选输入用：平台按身份给值，插件声明它的形状）",
    )

    @model_validator(mode="after")
    def _format_only_for_scalar_inputs(self) -> InputSpec:
        """`format` 只允许用在**单值**输入上（`file` / `multi_select` 禁止）。

        为什么写在形态校验里：`format` 约束的是「一个值长什么样」。多值输入里
        每一份的形状可能不同（多个文件、多个选项），声明一个整体 format 表达不出
        这层意思，放过去只会让人误以为已经校验过了——于是「声明了格式、运行期不校验」
        变成第二条假护栏（与 `max_items` 那条同源）。

        Returns:
            InputSpec: 自身（Pydantic v2 `mode="after"` 约定）。

        Raises:
            ValueError: 在多值输入上声明了 `format`。
        """
        if self.format and self.type in {"file", "multi_select"}:
            raise ValueError(
                f"输入项 {self.key} 是多值输入（{self.type}），不能声明 format={self.format!r}"
            )
        return self

    @model_validator(mode="after")
    def _item_limit_only_for_multi_value(self) -> InputSpec:
        """`max_items` 只对**多值输入**有意义（`file` / `multi_select`）。

        为什么写在形态校验里：单值输入声明数量上限，说明声明作者把"份数"
        理解错了；这种声明放过去，运行期永远算不出超限，等于给了一条
        "看上去限制了、其实没限"的假护栏。宁可加载期就报错。

        Returns:
            InputSpec: 自身。

        Raises:
            ValueError: 单值输入声明了 `max_items`。
        """
        if self.max_items is not None and self.type not in {"file", "multi_select"}:
            raise ValueError(
                f"输入项 {self.key} 是单值输入（{self.type}），不能声明数量上限 max_items"
            )
        return self

    @model_validator(mode="after")
    def _text_only_constraints(self) -> InputSpec:
        """文本类输入不该带候选值（避免表单把选择项渲染成输入框）。

        `select` / `multi_select` **允许** `options` 为空：此时取值由平台动态提供
        （例如「目标仓库」来自已连接的代码仓库、「所属部门」来自通讯录），
        写死静态候选反而会让声明与真实数据不一致。

        Returns:
            InputSpec: 自身。

        Raises:
            ValueError: 文本类输入给了候选值。
        """
        if self.type in {"text", "textarea", "email", "file"} and self.options:
            raise ValueError(f"输入项 {self.key} 不是选择类却给出了候选值")
        return self


class OutputSpec(_StrictModel):
    """产物项声明（驱动结果区渲染）。

    Attributes:
        key: 产物键（步骤 `produces` 的键集合 ⊆ 它）。
        label: 用户可见名称。
        type: 产物类型（`text` / `link` / `file` / `table`）。
    """

    key: str = Field(min_length=1, max_length=64, description="产物键")
    label: str = Field(min_length=1, max_length=32, description="产物名称")
    type: Literal["text", "link", "file", "table"] = Field(description="产物类型")


class UiExtension(_StrictModel):
    """界面扩展声明（一个宿主渲染全部插件，D-17）。

    Attributes:
        layout: 布局标识（本阶段仅 `task_workspace`）。
        nav_label: 侧边栏入口名称。
        icon_key: 平台图标名（不引用任何外部资源）。
        order: 排序号。
    """

    layout: Literal["task_workspace"] = Field(description="布局标识")
    nav_label: str = Field(min_length=2, max_length=16, description="入口名称")
    icon_key: str = Field(min_length=1, max_length=32, description="图标名")
    order: int = Field(ge=1, le=99, description="排序号")


class ConfirmSpec(_StrictModel):
    """人工接管点声明（PRD 三处 + Offer 金额确认）。

    Attributes:
        reason: 为什么需要确认（用户可见）。
        summary_fields: 确认时展示哪些输入/产物键。
    """

    reason: str = Field(min_length=4, max_length=80, description="确认原因")
    summary_fields: list[str] = Field(default_factory=list, max_length=8, description="展示项")


class WhenSpec(_StrictModel):
    """条件执行声明（HR 场景互斥：公文与 Offer 二选一）。

    Attributes:
        input: 依据哪个输入项。
        in_values: 命中哪些取值时执行该步。
    """

    input: str = Field(min_length=1, max_length=64, description="输入键")
    in_values: Annotated[list[str], Field(alias="in", min_length=1, max_length=20)] = Field(
        description="命中取值"
    )

    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, populate_by_name=True
    )


class ToolAlternative(_StrictModel):
    """工具的等价替代（按输入里的渠道/平台选择）。

    Attributes:
        tool_type: 工具类型（必须 ∈ `tool_registry.yaml`）。
        operation: 操作标识（必须 ∈ 该工具的 operations）。
    """

    tool_type: str = Field(min_length=2, max_length=32, description="工具类型")
    operation: str = Field(min_length=2, max_length=64, description="操作标识")


class StepSpec(_StrictModel):
    """步骤定义（§2.3）。

    Attributes:
        seq: 序号（1..8 且连续）。
        title: 用户可见步骤名。
        action: 执行原语（⊆ 6 项白名单）。
        produces: 该步产物键（⊆ `outputs[].key`）。
        needs: 依赖的前序产物键（必须由更早的步骤产生）。
        on_failure: 失败处置策略。
        confirm: 人工接管点（缺省表示不需确认）。
        when: 条件执行（缺省表示总是执行）。
        tool_type: `action="tool"` 时必填。
        operation: `action="tool"` 时必填。
        tool_alternatives: `tool` 步骤的等价替代。
        render_format: `action="render_file"` 时**必填**（`docx` / `md` / `csv`）。
        timeout_seconds: 单步超时（缺省 120，上限 600）。
    """

    seq: int = Field(ge=1, le=MAX_STEPS, description="序号")
    title: str = Field(min_length=2, max_length=MAX_TITLE_LENGTH, description="步骤名称")
    action: str = Field(min_length=2, max_length=32, description="执行原语")
    produces: list[str] = Field(default_factory=list, max_length=8, description="产物键")
    needs: list[str] = Field(default_factory=list, max_length=8, description="依赖产物键")
    on_failure: Literal["abort", "continue", "skip_and_mark", "retry"] = Field(
        default="abort", description="失败处置"
    )
    confirm: ConfirmSpec | None = Field(default=None, description="人工接管点")
    when: WhenSpec | None = Field(default=None, description="条件执行")
    tool_type: str | None = Field(default=None, max_length=32, description="工具类型")
    operation: str | None = Field(default=None, max_length=64, description="操作标识")
    tool_alternatives: list[ToolAlternative] = Field(
        default_factory=list, max_length=3, description="等价替代"
    )
    render_format: Literal["docx", "md", "csv"] | None = Field(
        default=None, description="导出格式（render_file 必填）"
    )
    timeout_seconds: int = Field(default=120, ge=10, le=600, description="单步超时秒数")

    @model_validator(mode="after")
    def _tool_fields_consistent(self) -> StepSpec:
        """`tool` 原语必须带 `tool_type`+`operation`，其它原语不得带。

        Returns:
            StepSpec: 自身。

        Raises:
            ValueError: 工具字段与原语不匹配。
        """
        if self.action == "tool":
            if not self.tool_type or not self.operation:
                raise ValueError(f"步骤 {self.seq} 使用工具调用但缺少工具类型或操作")
        elif self.tool_type or self.operation or self.tool_alternatives:
            raise ValueError(f"步骤 {self.seq} 不是工具调用却声明了工具字段")
        titles = [self.tool_type or ""]
        titles.extend(item.tool_type for item in self.tool_alternatives)
        if len(set(titles)) != len(titles):
            raise ValueError(f"步骤 {self.seq} 的工具类型重复")
        return self

    @model_validator(mode="after")
    def _render_format_consistent(self) -> StepSpec:
        """`render_file` 必须声明导出格式，其它原语不得声明。

        与 `_tool_fields_consistent` 同一体例：**字段存在即必须正确**。
        缺声明的导出步骤会在运行期退化成"随便挑一个格式"，而产物格式是
        用户能感知的交付物（公文必须是 docx），不能靠兜底猜。

        Returns:
            StepSpec: 自身。

        Raises:
            ValueError: 导出步骤缺格式，或非导出步骤带了格式。
        """
        if self.action == "render_file":
            if self.render_format is None:
                raise ValueError(f"步骤 {self.seq} 导出文件但未声明导出格式")
        elif self.render_format is not None:
            raise ValueError(f"步骤 {self.seq} 不是导出文件却声明了导出格式")
        return self


class ChecklistItem(_StrictModel):
    """结果物核查清单项（P0-P3-14）。

    Attributes:
        key: 对应产物键。
        name: 用户可见名称。
        source_step: 产出该结果物的步骤序号。
    """

    key: str = Field(min_length=1, max_length=64, description="产物键")
    name: str = Field(min_length=2, max_length=24, description="核查项名称")
    source_step: int = Field(ge=1, le=MAX_STEPS, description="来源步骤序号")


class GuardSpec(_StrictModel):
    """合规规则声明（P1-10：把「产出什么键要过哪道门」从代码挪进声明）。

    **只声明"跑哪条规则"，不声明"判据是什么"**：语义硬约束（排期认不出的
    时间不判失败、评分表缺「风险」列整表判失败）写死在规则模块里，
    `params` 只允许收紧词表 / 列名 / 文案，`validate_params` 拒绝把判据
    关闭的布尔项——否则"改个参数就能把合规门关掉"会变成一条后门。

    Attributes:
        output_key: 产物键（必须 ∈ `outputs[].key`，由 `_check_structure` 校验）。
        rule: 规则名（必须已由 `app.plugins.guards` 注册，加载器校验）。
        params: 规则参数（缺省 `{}` = 规则内置基线取值）。
    """

    output_key: str = Field(min_length=1, max_length=64, description="产物键")
    rule: str = Field(min_length=2, max_length=64, description="规则名")
    params: dict[str, Any] = Field(default_factory=dict, description="规则参数")


class PluginManifest(_StrictModel):
    """插件完整声明（6 段，`extra="forbid"`）。

    Attributes:
        plugin_id: 稳定标识（`plg_` 前缀，**不是**资源 ID）。
        name: 插件名称（2-32 字）。
        summary: 一句话说明（≤60 字）。
        description: 详细说明（≥120 字且 ≤800 字）。
        category: 市场分类。
        icon_key: 平台图标名。
        vendor: 发布方（本阶段固定 `official`）。
        version: 版本（semver）。
        min_platform_version: 最低平台版本（semver）。
        changelog: 版本变更说明（可选）。
        requires_plugins: 前置插件。
        capabilities: 授权能力声明。
        settings_schema: 配置项声明。
        inputs: 输入声明。
        outputs: 产物声明。
        ui_extension: 界面扩展。
        steps: 步骤定义（1-8 步）。
        checklist: 核查清单（2-5 项）。
        match_hints: 目标匹配提示（goal_router 打分用的领域词，可缺省）。
        guards: 合规规则声明（可缺省；产出受管产物键时由加载器强制覆盖）。
    """

    plugin_id: str = Field(min_length=6, max_length=44, description="插件标识")
    name: str = Field(min_length=2, max_length=32, description="插件名称")
    summary: str = Field(min_length=4, max_length=60, description="一句话说明")
    description: str = Field(
        min_length=MIN_DESCRIPTION_LENGTH,
        max_length=MAX_DESCRIPTION_LENGTH,
        description="详细说明",
    )
    category: Literal["dev", "content", "hr", "data", "collab", "other"] = Field(
        description="市场分类"
    )
    icon_key: str = Field(min_length=1, max_length=32, description="图标名")
    vendor: Literal["official"] = Field(default="official", description="发布方")
    version: str = Field(max_length=16, description="版本")
    min_platform_version: str = Field(max_length=16, description="最低平台版本")
    changelog: str = Field(default="", max_length=300, description="版本说明")
    requires_plugins: list[str] = Field(default_factory=list, max_length=3, description="前置插件")
    capabilities: list[CapabilityClaim] = Field(min_length=1, max_length=6, description="能力声明")
    settings_schema: list[SettingSpec] = Field(
        default_factory=list, max_length=8, description="配置项"
    )
    inputs: list[InputSpec] = Field(min_length=1, max_length=12, description="输入声明")
    outputs: list[OutputSpec] = Field(min_length=1, max_length=12, description="产物声明")
    ui_extension: UiExtension = Field(description="界面扩展")
    steps: list[StepSpec] = Field(min_length=1, max_length=MAX_STEPS, description="步骤定义")
    checklist: list[ChecklistItem] = Field(
        min_length=MIN_CHECKLIST, max_length=MAX_CHECKLIST, description="核查清单"
    )
    match_hints: list[str] = Field(
        default_factory=list, max_length=MATCH_HINT_MAX_COUNT, description="匹配提示"
    )
    guards: list[GuardSpec] = Field(
        default_factory=list, max_length=MAX_GUARDS, description="合规规则声明"
    )

    # ------------------------------------------------------------------
    # 形态校验
    # ------------------------------------------------------------------
    @field_validator("plugin_id")
    @classmethod
    def _check_plugin_id(cls, value: str) -> str:
        """校验插件标识格式。

        Args:
            value: 插件标识。

        Returns:
            str: 原值。

        Raises:
            ValueError: 不符合 `plg_` 规范。
        """
        if PLUGIN_ID_PATTERN.match(value) is None:
            raise ValueError(f"插件标识不合法：{value}（应形如 plg_dev_workbench）")
        return value

    @field_validator("version", "min_platform_version")
    @classmethod
    def _check_semver(cls, value: str) -> str:
        """校验版本号格式。

        Args:
            value: 版本号。

        Returns:
            str: 原值。

        Raises:
            ValueError: 非 `x.y.z` 形态。
        """
        if SEMVER_PATTERN.match(value) is None:
            raise ValueError(f"版本号不合法：{value}（应形如 1.0.0）")
        return value

    @field_validator("requires_plugins")
    @classmethod
    def _check_requires(cls, value: list[str]) -> list[str]:
        """校验前置插件标识格式并去重。

        Args:
            value: 前置插件列表。

        Returns:
            list[str]: 原值。

        Raises:
            ValueError: 含非法标识或重复项。
        """
        for item in value:
            if PLUGIN_ID_PATTERN.match(item) is None:
                raise ValueError(f"前置插件标识不合法：{item}")
        if len(set(value)) != len(value):
            raise ValueError("前置插件重复")
        return value

    @model_validator(mode="after")
    def _check_structure(self) -> PluginManifest:
        """校验步骤序号连续、产物/依赖引用闭合、清单指向合法步骤。

        追加（P1-10 / P2-13）：`match_hints` 形态与 `guards` 引用闭合。

        Returns:
            PluginManifest: 自身。

        Raises:
            ValueError: 结构不自洽。
        """
        seqs = [step.seq for step in self.steps]
        expected = list(range(1, len(seqs) + 1))
        if seqs != expected:
            raise ValueError(f"步骤序号必须从 1 连续递增，实际 {seqs}（期望 {expected}）")

        output_keys = {item.key for item in self.outputs}
        if len(output_keys) != len(self.outputs):
            raise ValueError("产物键重复")

        produced_before: set[str] = set()
        for step in self.steps:
            missing = [key for key in step.produces if key not in output_keys]
            if missing:
                raise ValueError(f"步骤 {step.seq} 产出了未声明的产物：{missing}")
            unresolved = [key for key in step.needs if key not in produced_before]
            if unresolved:
                raise ValueError(f"步骤 {step.seq} 依赖了前序未产出的内容：{unresolved}")
            produced_before.update(step.produces)

        input_keys = {item.key for item in self.inputs}
        if len(input_keys) != len(self.inputs):
            raise ValueError("输入键重复")
        for step in self.steps:
            if step.when is not None and step.when.input not in input_keys:
                raise ValueError(f"步骤 {step.seq} 的条件依赖未声明的输入：{step.when.input}")
            for key in step.confirm.summary_fields if step.confirm else []:
                if key not in output_keys and key not in input_keys:
                    raise ValueError(f"步骤 {step.seq} 的确认展示项未声明：{key}")

        known_steps = set(seqs)
        for item in self.checklist:
            if item.source_step not in known_steps:
                raise ValueError(f"核查项 {item.key} 指向不存在的步骤 {item.source_step}")
            if item.key not in output_keys:
                raise ValueError(f"核查项 {item.key} 不是已声明的产物")

        self._check_match_hints()
        self._check_guards(output_keys)
        return self

    def _check_match_hints(self) -> None:
        """校验匹配提示形态：逐条 2–8 字、去重、非纯功能字。

        Raises:
            ValueError: 提示词形态不合规。
        """
        seen: set[str] = set()
        for hint in self.match_hints:
            if not (MATCH_HINT_MIN_LEN <= len(hint) <= MATCH_HINT_MAX_LEN):
                raise ValueError(
                    f"匹配提示「{hint}」长度为 {len(hint)}，应在 "
                    f"{MATCH_HINT_MIN_LEN}-{MATCH_HINT_MAX_LEN} 字之间"
                )
            if hint in seen:
                raise ValueError(f"匹配提示重复：{hint}")
            seen.add(hint)
            # 纯功能字（「一个」「什么」）不携带领域信号，进不了打分也换不来召回
            if not any(char not in FUNCTION_CHARS for char in hint):
                raise ValueError(f"匹配提示「{hint}」全是功能字，不携带领域信号")

    def _check_guards(self, output_keys: set[str]) -> None:
        """校验合规规则声明：产物键闭合、`(output_key, rule)` 不重复。

        Args:
            output_keys: 已声明的产物键集合。

        Raises:
            ValueError: 规则声明不自洽。
        """
        seen: set[tuple[str, str]] = set()
        for guard in self.guards:
            if guard.output_key not in output_keys:
                raise ValueError(f"合规规则指向未声明的产物：{guard.output_key}")
            pair = (guard.output_key, guard.rule)
            if pair in seen:
                raise ValueError(f"合规规则重复声明：{guard.output_key} × {guard.rule}")
            seen.add(pair)

    # ------------------------------------------------------------------
    # 便捷访问
    # ------------------------------------------------------------------
    @property
    def capability_keys(self) -> tuple[str, ...]:
        """声明的能力键（按声明顺序）。

        Returns:
            tuple[str, ...]: 能力键元组。
        """
        return tuple(item.key for item in self.capabilities)

    @property
    def required_capability_keys(self) -> tuple[str, ...]:
        """必需的能力键。

        Returns:
            tuple[str, ...]: 必需能力键元组。
        """
        return tuple(item.key for item in self.capabilities if item.required)

    @property
    def action_keys(self) -> tuple[str, ...]:
        """使用到的执行原语（去重、按出现顺序）。

        Returns:
            tuple[str, ...]: 原语元组。
        """
        seen: list[str] = []
        for step in self.steps:
            if step.action not in seen:
                seen.append(step.action)
        return tuple(seen)

    def step_titles(self) -> list[str]:
        """步骤标题列表（入库快照用）。

        Returns:
            list[str]: 标题列表。
        """
        return [step.title for step in self.steps]

    def text_fields(self) -> list[tuple[str, str]]:
        """收集全部用户可见文案（禁词门禁扫这些）。

        Returns:
            list[tuple[str, str]]: `(字段路径, 文案)` 列表。
        """
        collected: list[tuple[str, str]] = [
            ("name", self.name),
            ("summary", self.summary),
            ("description", self.description),
            ("ui_extension.nav_label", self.ui_extension.nav_label),
        ]
        if self.changelog:
            collected.append(("changelog", self.changelog))
        collected.extend((f"steps[{step.seq}].title", step.title) for step in self.steps)
        collected.extend((f"inputs[{item.key}].label", item.label) for item in self.inputs)
        collected.extend((f"outputs[{item.key}].label", item.label) for item in self.outputs)
        collected.extend((f"checklist[{item.key}].name", item.name) for item in self.checklist)
        collected.extend(
            (f"settings_schema[{item.key}].label", item.label) for item in self.settings_schema
        )
        return collected


def _term_pattern(term: str) -> re.Pattern[str]:
    """把禁词编译为匹配模式（ASCII 词按词边界，中文按子串）。

    为什么按词边界：`PR` / `sha` / `api` 这类短 ASCII 词若按子串匹配，
    会把 `spring`、`share`、`rapid` 全部误判成违规（阶段二禁词门禁踩过同样的坑）。
    中文没有词边界概念，直接子串匹配即可。

    Args:
        term: 禁词。

    Returns:
        re.Pattern[str]: 编译后的模式（大小写不敏感）。
    """
    if term.isascii():
        return re.compile(rf"\b{re.escape(term)}s?\b", re.IGNORECASE)
    return re.compile(re.escape(term))


#: 禁词 → 匹配模式（模块加载时编译一次，避免每份声明重复编译）
_BANNED_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (term, _term_pattern(term)) for term in BANNED_TERMS
)


def find_banned_terms(text: str) -> list[str]:
    """返回文案中命中的禁词（ASCII 按词边界、中文按子串）。

    Args:
        text: 待检文案。

    Returns:
        list[str]: 命中的禁词（去重、保持清单顺序）。
    """
    if not text:
        return []
    return [term for term, pattern in _BANNED_PATTERNS if pattern.search(text)]


def find_forbidden_keys(payload: object, *, path: str = "") -> list[str]:
    """递归扫描原始声明中的禁用键名（凭据 / 代码 / 外部地址）。

    这一层是 `extra="forbid"` 的**兜底**：即使有人把敏感字段塞进 `settings` 的取值、
    或塞进某个 `dict` 型扩展位，也会在这里被抓到。

    Args:
        payload: 已解析的 JSON 载荷（dict / list / 标量）。
        path: 当前路径（用于报错定位）。

    Returns:
        list[str]: 命中路径清单，形如 `steps[0].token`。
    """
    hits: list[str] = []
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key)
            child = f"{path}.{key}" if path else key
            if key.lower() in FORBIDDEN_KEYS:
                hits.append(child)
            hits.extend(find_forbidden_keys(value, path=child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            hits.extend(find_forbidden_keys(value, path=f"{path}[{index}]"))
    return hits


__all__: list[str] = [
    "BANNED_TERMS",
    "CATEGORIES",
    "FORBIDDEN_KEYS",
    "FUNCTION_CHARS",
    "INPUT_TYPES",
    "MATCH_HINT_MAX_COUNT",
    "MATCH_HINT_MAX_LEN",
    "MATCH_HINT_MIN_LEN",
    "MAX_CHECKLIST",
    "MAX_GUARDS",
    "MAX_STEPS",
    "MIN_CHECKLIST",
    "MIN_DESCRIPTION_LENGTH",
    "ON_FAILURE_VALUES",
    "OUTPUT_TYPES",
    "PLUGIN_ID_PATTERN",
    "SEMVER_PATTERN",
    "SETTING_TYPES",
    "TEXT_FIELDS",
    "UI_LAYOUTS",
    "CapabilityClaim",
    "ChecklistItem",
    "ConfirmSpec",
    "GuardSpec",
    "InputSpec",
    "OutputSpec",
    "PluginManifest",
    "SettingSpec",
    "StepSpec",
    "ToolAlternative",
    "UiExtension",
    "WhenSpec",
    "find_banned_terms",
    "find_forbidden_keys",
]
