"""执行原语白名单与网关契约（阶段三 B16，`architecture-phase3.md` §3.2 / D-16）。

**为什么插件层只依赖 Protocol**：`plugins/**` 不允许 `import app.services.*`
（§3.1 分层铁律）。原语的真实实现（M1 生成 / R7 检索 / T5 工具执行）在
`services/action_gateway.py`，由 `api/v1/plugin_deps.py` 注入。
依赖方向因此恒为 `api → plugins → business → db`，插件层永远拿不到
`ToolService` / `httpx` / 数据库会话——这是"插件无代码执行"（D-14）在依赖图上的保证。

**两层分离**（D-15）：

- `ACTION_WHITELIST`：平台内部怎么做事（用户**永不**看到这 6 个词）；
- `CAPABILITY_KEYS`：用户要同意的数据/写入边界（授权抽屉逐项展示"为什么需要"）。

`CAPABILITY_REQUIREMENTS` 把两者连起来，`G-30` 用它做双向校验
（"要了没用"与"用了没要"都报错）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from app.plugins.renderer import RENDER_FORMATS

if TYPE_CHECKING:  # 仅类型检查期引入，避免 actions ↔ schema 的运行期耦合
    from app.plugins.schema import PluginManifest, StepSpec

#: 执行原语白名单（6 项，封闭；新增即能力扩张，须改平台）
ACTION_WHITELIST: Final[tuple[str, ...]] = (
    "understand",
    "compose",
    "retrieve",
    "tool",
    "render_file",
    "summarize",
)

#: 原语的中文别名（**仅内部日志用**，绝不进任何响应与界面——AC-06）
ACTION_INTERNAL_LABELS: Final[dict[str, str]] = {
    "understand": "理解抽取",
    "compose": "内容撰写",
    "retrieve": "知识检索",
    "tool": "办公工具调用",
    "render_file": "文件导出",
    "summarize": "结果汇总",
}

#: 需要额外授权能力的原语（`understand`/`compose`/`summarize` 用平台内置推理，无需授权）
ACTION_CAPABILITY_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "retrieve": ("personal.knowledge.read", "personal.file.read"),
    "tool": ("personal.tool.use",),
}

#: 授权能力枚举（6 项，封闭；PRD P0-P3-03）
CAPABILITY_KEYS: Final[tuple[str, ...]] = (
    "personal.knowledge.read",
    "personal.tool.use",
    "personal.file.read",
    "org.directory.read",
    "org.repo.write",
    "org.external.publish",
)

#: 个人能力（恒开，企业不可关闭；PL13 收到这些键一律 `20001`）
PERSONAL_CAPABILITIES: Final[tuple[str, ...]] = (
    "personal.knowledge.read",
    "personal.tool.use",
    "personal.file.read",
)

#: 企业能力（需管理员在团队设置开启总开关；`plugin_capability_policy` 只存这三项）
ORG_CAPABILITIES: Final[tuple[str, ...]] = (
    "org.directory.read",
    "org.repo.write",
    "org.external.publish",
)

#: 能力展示信息（名称 / 为什么需要 / 不同意的后果）——授权抽屉三要素
CAPABILITY_CATALOG: Final[dict[str, dict[str, str]]] = {
    "personal.knowledge.read": {
        "display_name": "读取知识库",
        "reason": "需要读取你所在企业的知识库内容，用于检索与引用制度、规范等素材",
        "denial_effect": "不同意将无法引用知识库素材，生成的稿件可能缺少企业事实依据",
    },
    "personal.tool.use": {
        "display_name": "使用办公工具",
        "reason": "需要调用你已连接的办公工具，把结果直接写入邮箱、仓库或企微",
        "denial_effect": "不同意将无法自动发送或提交，只能下载内容后手工处理",
    },
    "personal.file.read": {
        "display_name": "读取你上传的文件",
        "reason": "需要读取你在本次任务中上传的文件（如简历、参考文档）",
        "denial_effect": "不同意将无法基于上传文件给出结果",
    },
    "org.directory.read": {
        "display_name": "读取通讯录",
        "reason": "需要读取企业通讯录与组织架构，用于填写所属部门、确定收件人",
        "denial_effect": "不同意将需要你手工填写部门与收件人",
    },
    "org.repo.write": {
        "display_name": "写入代码仓库",
        "reason": "需要把生成的代码提交到指定仓库并创建合并请求",
        "denial_effect": "不同意将只生成代码文件，由你手工提交",
    },
    "org.external.publish": {
        "display_name": "对外发布内容",
        "reason": "需要把内容发布到对外平台（公众号、邮件等），发布前都会先请你确认",
        "denial_effect": "不同意将只生成稿件与排期表，由你手工发布",
    },
}

#: 工具写操作需要的能力（原语 `tool` 的二级校验）
TOOL_WRITE_CAPABILITY: Final[dict[str, tuple[str, ...]]] = {
    "github": ("org.repo.write",),
    "wechat_mp": ("org.external.publish",),
    "email": ("org.external.publish",),
    "wecom": (),
    "feishu": (),
}

#: 通讯录读取类操作（`org.directory.read` 的使用位置，G-30 反向校验用）
DIRECTORY_TOOL_OPERATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("wecom", "list_members"),
    ("wecom", "list_departments"),
    ("feishu", "list_members"),
)

#: `retrieve` 原语要求的读取能力（二者之一）
RETRIEVE_CAPABILITIES: Final[tuple[str, ...]] = (
    "personal.knowledge.read",
    "personal.file.read",
)

#: 允许"两选一"的能力对（G-30 判定 `retrieve` 时使用）
CAPABILITY_ALTERNATIVES: Final[tuple[tuple[str, ...], ...]] = (
    ("personal.knowledge.read", "personal.file.read"),
)

#: 会对外发送内容、必须人工确认的工具操作（`confirm` 缺失即加载失败）
#:
#: PRD 硬要求：对外可见的动作前必须确认。这里用白名单把"哪些动作算对外"
#: 固化下来，避免声明作者遗漏 `confirm`。
OUTBOUND_TOOL_OPERATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("email", "send_message"),
    ("wecom", "send_message"),
    ("feishu", "send_message"),
    ("wechat_mp", "publish_article"),
    ("github", "create_pull_request"),
)

#: 产物最大内联长度（超出写对象存储）
MAX_INLINE_ARTIFACT_BYTES: Final[int] = 32 * 1024

#: 原语 → 是否可自动重试（写外部动作不重试）
ACTION_RETRYABLE: Final[dict[str, bool]] = {
    "understand": True,
    "compose": True,
    "retrieve": True,
    "tool": False,
    "render_file": False,
    "summarize": False,
}

#: 原语默认超时（秒）
ACTION_DEFAULT_TIMEOUT: Final[dict[str, int]] = {
    "understand": 240,
    "compose": 240,
    "retrieve": 120,
    "tool": 60,
    "render_file": 60,
    "summarize": 30,
}

#: 原语执行结果状态
ActionStatus = Literal["ok", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class RunContext:
    """单步执行上下文（**只携带发起人身份与已授能力，不含任何凭据**）。

    §3.6 安全红线：`RunContext` 里没有令牌、没有连接串、没有工具凭据——
    工具凭据由 T5 内部按 `auth` 解析，插件只能拿到结果。

    Attributes:
        tenant_id: 租户 ID。
        task_id: 任务 ID。
        step_seq: 当前步骤序号。
        plugin_id: 插件标识。
        installation_id: 安装记录标识（`plugin_installation` 主键的字符串形态）。
        initiator_id: 发起人用户 ID。
        trace_id: 链路 ID（只进日志）。
        granted: 本次任务已获授的能力集合（每步入口复检后传入）。
        settings: 插件配置（键 ⊆ 声明 `settings_schema`）。
        inputs: 任务输入（键 ⊆ 声明 `inputs`）。
        outputs: 已完成步骤的产物（`key → 内容`），供 `needs` 装配。
        auth: 发起人身份（`AuthContext`，**只有用户/租户/角色，不含任何凭据**）。
            任务以发起人身份调用适配层（§2.5），插件永远拿不到工具连接串。
    """

    tenant_id: str = ""
    task_id: str = ""
    step_seq: int = 0
    plugin_id: str = ""
    installation_id: str = ""
    initiator_id: str = ""
    trace_id: str = ""
    granted: frozenset[str] = frozenset()
    settings: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    auth: Any = None

    def has(self, capability: str) -> bool:
        """判断本次执行是否已获授某能力。

        Args:
            capability: 能力键。

        Returns:
            bool: 已获授返回 True。
        """
        return capability in self.granted

    def needs(self, keys: tuple[str, ...] | list[str]) -> dict[str, Any]:
        """按 `needs` 抽取前序产物（参数装配用）。

        Args:
            keys: 产物键列表。

        Returns:
            dict[str, Any]: 已产出的键值对（缺失的键不出现）。
        """
        return {key: self.outputs[key] for key in keys if key in self.outputs}


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    """待落库的产物草稿（由原语实现产出，尚未脱敏落盘）。

    Attributes:
        output_key: 产物键（⊆ 声明 `outputs[].key`）。
        type: 产物类型（`text` / `link` / `file` / `table`）。
        name: 产物名称（用户可见）。
        inline_text: 内联文本（≤32KB，`table` 为 CSV）。
        external_url: 外链地址（`link` 类型）。
        storage_key: 对象存储键（`file` 类型；本阶段以内存文件句柄代替）。
        content: 二进制内容（`file` 类型，落存储用）。
        preview_text: 表格前 5 行预览。
        size_bytes: 内容大小。
    """

    output_key: str = ""
    type: str = "text"
    name: str = ""
    inline_text: str = ""
    external_url: str = ""
    storage_key: str = ""
    content: bytes = b""
    preview_text: str = ""
    size_bytes: int = 0


@dataclass(frozen=True, slots=True)
class ActionResult:
    """原语执行结果（错误一律翻译成既有业务码，不透出下游细节）。

    Attributes:
        status: `ok` / `failed` / `skipped`。
        artifacts: 产物草稿列表。
        data: 供后续步骤 `needs` 使用的结果（已脱敏）。
        error_code: 失败时的业务错误码（None 表示成功）。
        reason_text: 失败原因（**业务语言**，直接展示给用户）。
        operator_action: 建议动作（业务语言，如"补充后重试本步"）。
        retryable: 是否属于可重试失败。
        usage_units: 本次消耗的额度（用于任务汇总）。
    """

    status: ActionStatus = "ok"
    artifacts: tuple[ArtifactDraft, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_code: Any = None
    reason_text: str = ""
    operator_action: str = ""
    retryable: bool = False
    usage_units: int = 0

    @property
    def ok(self) -> bool:
        """是否成功。

        Returns:
            bool: 成功返回 True。
        """
        return self.status == "ok"

    @property
    def skipped(self) -> bool:
        """是否被跳过（条件不满足）。

        Returns:
            bool: 跳过返回 True。
        """
        return self.status == "skipped"


def capability_display_name(capability: str) -> str:
    """取能力的用户可见名称（未知能力回落原键，不抛错）。

    Args:
        capability: 能力键。

    Returns:
        str: 展示名。
    """
    return CAPABILITY_CATALOG.get(capability, {}).get("display_name", capability)


def capabilities_for_tool(tool_type: str, operation: str) -> tuple[str, ...]:
    """计算某次工具调用需要的额外能力。

    Args:
        tool_type: 工具类型。
        operation: 操作标识。

    Returns:
        tuple[str, ...]: 额外需要的能力键（不含基础的 `personal.tool.use`）。
    """
    required: list[str] = list(TOOL_WRITE_CAPABILITY.get(tool_type, ()))
    if (tool_type, operation) in DIRECTORY_TOOL_OPERATIONS:
        required.append("org.directory.read")
    return tuple(dict.fromkeys(required))


def is_outbound_operation(tool_type: str, operation: str) -> bool:
    """判断工具操作是否对外可见（对外动作必须有 `confirm` 人工接管点）。

    Args:
        tool_type: 工具类型。
        operation: 操作标识。

    Returns:
        bool: 对外可见返回 True。
    """
    return (tool_type, operation) in OUTBOUND_TOOL_OPERATIONS


def declared_tool_paths(step: StepSpec) -> tuple[tuple[str, str], ...]:
    """取某步**声明清单**内的全部可执行路径（主工具 + 等价替代）。

    这是「执行路径 ⊆ 声明清单」断言的判据来源：运行期选中的工具必须落在这个
    集合里。声明了 `wechat_mp.publish_article` 就只能发公众号或走已声明的邮件
    替代；**不允许**运行期凭输入临时换成声明外的第 7 种路径——那等于声明失效。

    Args:
        step: 步骤声明。

    Returns:
        tuple[tuple[str, str], ...]: `(tool_type, operation)` 清单（保持声明顺序）。
    """
    if step.action != "tool":
        return ()
    paths: list[tuple[str, str]] = [(step.tool_type or "", step.operation or "")]
    paths.extend((alt.tool_type, alt.operation) for alt in step.tool_alternatives)
    return tuple(paths)


def assert_execution_path(step: StepSpec, tool_type: str = "", operation: str = "") -> None:
    """运行期复核「执行路径 ⊆ 声明清单」（G-28 的运行期半段）。

    加载期已查过一遍（`loader._check_actions`），这里再查一次是防运行期篡改：
    声明快照落库后若被改动、或网关按输入动态换了工具，都必须在这里被拦住。

    `tool_type` / `operation` **都不传**时只复核原语白名单（即"这一步的声明本身
    是否还成立"）；要复核具体某次调用时再传这两个参数。

    Args:
        step: 步骤声明。
        tool_type: 实际要用的工具类型（不传表示不做路径复核）。
        operation: 实际操作标识（不传表示不做路径复核）。

    Raises:
        ValueError: 原语不在白名单，或工具路径不在声明清单内。
    """
    if step.action not in ACTION_WHITELIST:
        raise ValueError(f"原语不在白名单内：{step.action}")
    if step.action != "tool":
        return
    if not tool_type and not operation:
        return
    if (tool_type, operation) not in declared_tool_paths(step):
        raise ValueError(f"步骤 {step.seq} 的执行路径不在声明清单内：{tool_type}.{operation}")


def render_file_violations(manifest: PluginManifest) -> list[str]:
    """校验 `render_file` 的两条附加约束（§2.2 / §11 冲突 12）。

    **① 导出格式必须由声明给出**：格式是产物类型契约（公文必须 docx），
    缺声明即视为错误，不允许"运行期挑一个"。

    **② 不得与 `personal.file.read` 耦合**：导出是"写产物"，不需要"读用户文件"的
    授权。反过来，声明了"读个人文件"就必须有真正的读文件支撑
    （`inputs[].type=file` 或 `retrieve` 步骤）——否则会出现"没读任何文件却要了
    读文件权限"的越权外观，也会让授权抽屉里的理由说不通。

    Args:
        manifest: 插件声明。

    Returns:
        list[str]: 违规描述（空表示通过）。
    """
    problems: list[str] = []
    render_steps = [step for step in manifest.steps if step.action == "render_file"]
    for step in render_steps:
        if step.render_format not in RENDER_FORMATS:
            problems.append(f"步骤 {step.seq} 导出文件但未声明有效的导出格式")
    if not render_steps:
        return problems
    if "personal.file.read" not in manifest.capability_keys:
        return problems
    reads_input_files = any(item.type == "file" for item in manifest.inputs)
    has_retrieve = any(step.action == "retrieve" for step in manifest.steps)
    if not reads_input_files and not has_retrieve:
        problems.append(
            "声明了读取个人文件的能力，但既没有上传文件的输入项也没有检索步骤"
            "（导出文件不需要读文件授权）"
        )
    return problems


class ActionGateway(Protocol):
    """原语门面（实现见 `services/action_gateway.py`，由依赖注入提供）。

    插件层**只**依赖这个 Protocol：它不含 `httpx`、不含 SQL、不含下游 SDK，
    因此插件"想直连底座"在类型层面就做不到（D-16）。
    """

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """执行一个原语。

        Args:
            ctx: 执行上下文（身份 + 已授能力 + 输入/前序产物）。
            action: 原语名（⊆ `ACTION_WHITELIST`）。
            params: 原语参数（由引擎按 `needs` 装配）。

        Returns:
            ActionResult: 执行结果。
        """
        ...

    def resolve(self, action: str) -> str:
        """把原语解析为平台落点标识（运行期再校验一次白名单）。

        Args:
            action: 原语名。

        Returns:
            str: 平台落点标识（如 `model.generate`）。

        Raises:
            ValueError: 原语不在白名单内。
        """
        ...


__all__: list[str] = [
    "ACTION_CAPABILITY_HINTS",
    "ACTION_DEFAULT_TIMEOUT",
    "ACTION_INTERNAL_LABELS",
    "ACTION_RETRYABLE",
    "ACTION_WHITELIST",
    "CAPABILITY_ALTERNATIVES",
    "CAPABILITY_CATALOG",
    "CAPABILITY_KEYS",
    "DIRECTORY_TOOL_OPERATIONS",
    "MAX_INLINE_ARTIFACT_BYTES",
    "ORG_CAPABILITIES",
    "OUTBOUND_TOOL_OPERATIONS",
    "PERSONAL_CAPABILITIES",
    "RENDER_FORMATS",
    "RETRIEVE_CAPABILITIES",
    "TOOL_WRITE_CAPABILITY",
    "ActionGateway",
    "ActionResult",
    "ActionStatus",
    "ArtifactDraft",
    "RunContext",
    "assert_execution_path",
    "capabilities_for_tool",
    "capability_display_name",
    "declared_tool_paths",
    "is_outbound_operation",
    "render_file_violations",
]
