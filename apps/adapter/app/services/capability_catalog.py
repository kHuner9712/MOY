"""能力目录实时组装（W1 切片 B，`architecture-w1-metrics-entry.md` §1.5.2）。

**唯一真相源（关键决策）**：

- 插件声明唯一权威 = `app/plugins/manifests/plg_*.json`，启动时经
  `PluginRegistry` 六重校验加载进进程内单例（`get_registry()`）；
- 工具注册唯一权威 = `app/registry/tool_registry.yaml`（`get_tool_registry()`）；
- `PluginService.market()` 的 DB 读数只是注册表同步下去的**版本记录**，
  不是独立真相源——所以本服务**不查库、不建表、不复制常量**
  （PRD §2.2「目录实时组装，避免第三份真相」；评审以 grep 断言本文件内
  无 `tenant_scope` / SQL / 第二份名称表）。

目录每次请求实时组装（6 插件 + 5 工具，微秒级）。目录**不带**
`integration_status`：推荐卡是否标注「演示」徽标留待 V1.1 连接器真实化
批次统一（设计 §9-Q4），MVP 不引入耦合。

工具没有「所需输入」概念：`inputs = ()`，不把 `params_schema` 的技术结构
透给终端用户；其 `summary` 为操作展示名「、」拼接（派生，非编造）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.plugins.loader import PluginRegistry, get_registry
from app.registry.loader import ToolRegistry, get_tool_registry

#: 工具在目录稳定序里的排序号：插件按声明 `ui_extension.order`（1..99）排，
#: 工具恒排在其后（同分再按 id 升序）。
_TOOL_SORT_ORDER: Final[int] = 999


@dataclass(frozen=True, slots=True)
class CapabilityInputBrief:
    """所需输入提示（D-17「所需输入提示」的料源）。

    Attributes:
        key: 输入键（插件声明 `inputs[].key`）。
        label: 用户可见名称（插件声明 `inputs[].label`）。
        type: 控件类型（`text` / `textarea` / `select` / `email` / `file` 等）。
        required: 是否必填。
    """

    key: str
    label: str
    type: str
    required: bool


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    """一条能力目录项（插件或工具；实时组装，不落库）。

    Attributes:
        id: 能力标识（插件为 `plugin_id`；工具为 `tool:{tool_type}`）。
        kind: 能力类型（`plugin` / `tool`）。
        name: 能力名称（插件声明 / 工具注册表原值）。
        summary: 一句话说明（插件摘要；工具为操作展示名拼接）。
        category: 业务分类（声明原值）。
        inputs: 所需输入提示（插件 `inputs[]`；工具为 `()`）。
        match_texts: 打分料源（字段名 → 文本；键为契约字段名
            `name` / `summary` / `category` / `inputs.label` / `operations`）。
        sort_key: 目录稳定序（插件 `(ui_extension.order, id)`；工具 `(999, id)`）。
    """

    id: str
    kind: str
    name: str
    summary: str
    category: str
    inputs: tuple[CapabilityInputBrief, ...]
    match_texts: dict[str, str]
    sort_key: tuple[int, str]


def _join_display_names(operations: Sequence[Mapping[str, Any]]) -> str:
    """把操作展示名「、」拼接（工具摘要与 operations 打分料源共用）。

    Args:
        operations: 工具能力清单（注册表原值）。

    Returns:
        str: 拼接文本；无操作时为空串。
    """
    return "、".join(
        str(operation.get("display_name", ""))
        for operation in operations
        if operation.get("display_name")
    )


def build_catalog(
    registry: PluginRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
) -> list[CapabilityEntry]:
    """实时组装能力目录（只读两个进程内注册表单例，零 IO 零 DB）。

    目录稳定序：插件按声明 `ui_extension.order` 再按 `plugin_id`；
    工具按 `(999, id)`（即注册表声明序之外按标识升序，保证确定性）。

    Args:
        registry: 插件注册表（None 时取进程内单例 `get_registry()`）。
        tool_registry: 工具注册表（None 时取进程内单例 `get_tool_registry()`）。

    Returns:
        list[CapabilityEntry]: 目录条目（稳定序；典型规模 6 插件 + 5 工具）。
    """
    plugin_registry = registry if registry is not None else get_registry()
    tools = tool_registry if tool_registry is not None else get_tool_registry()

    entries: list[CapabilityEntry] = []
    for manifest in plugin_registry.all_manifests():
        inputs = tuple(
            CapabilityInputBrief(
                key=item.key,
                label=item.label,
                type=item.type,
                required=item.required,
            )
            for item in manifest.inputs
        )
        entries.append(
            CapabilityEntry(
                id=manifest.plugin_id,
                kind="plugin",
                name=manifest.name,
                summary=manifest.summary,
                category=manifest.category,
                inputs=inputs,
                match_texts={
                    "name": manifest.name,
                    "summary": manifest.summary,
                    "category": manifest.category,
                    "inputs.label": "、".join(item.label for item in manifest.inputs),
                },
                sort_key=(manifest.ui_extension.order, manifest.plugin_id),
            )
        )
    for spec in tools.tools:
        operation_text = _join_display_names(spec.operations)
        entry_id = f"tool:{spec.tool_type}"
        entries.append(
            CapabilityEntry(
                id=entry_id,
                kind="tool",
                name=spec.display_name,
                summary=operation_text,
                category=spec.category,
                inputs=(),
                match_texts={
                    "name": spec.display_name,
                    "summary": operation_text,
                    "category": spec.category,
                    "operations": operation_text,
                },
                sort_key=(_TOOL_SORT_ORDER, entry_id),
            )
        )
    entries.sort(key=lambda entry: entry.sort_key)
    return entries


__all__: list[str] = [
    "CapabilityEntry",
    "CapabilityInputBrief",
    "build_catalog",
]
