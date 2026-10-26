"""合规规则注册表（P1-10）。

**声明驱动**：插件在 `manifest.guards[]` 里声明「哪个产物键过哪条规则」，
加载器按本注册表解析规则名并强制覆盖（产出受管键就必须声明对应规则）；
引擎在运行期按声明分派，不再在代码里写死 `if output_key == "offer"`。

分界约束：本模块**不得** import `engine` / `actions`——规则只认
`(text, content, params)` 与 `GuardViolation`，把违规翻译成 `StepOutcome`
是引擎的事。这样规则可被门禁（G-26）与单测直接调用，不牵动执行链。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Protocol, runtime_checkable

from app.plugins.guards.types import GuardViolation

#: 规则名形态（小写下划线，2-64 字符）
RULE_NAME_MIN_LEN: Final[int] = 2
RULE_NAME_MAX_LEN: Final[int] = 64


@runtime_checkable
class GuardRule(Protocol):
    """合规规则协议（实现方为 `guards/` 下的模块级单例对象）。

    Attributes:
        name: 规则名（声明 `guards[].rule` 用它解析）。
        required_output_keys: 强制覆盖的产物键——插件产出这些键时
            **必须**声明本规则（加载器拒载漏声明的插件）。
    """

    @property
    def name(self) -> str:
        """规则名（只读——注册后不可改名）。

        Returns:
            str: 规则名。
        """

    @property
    def required_output_keys(self) -> tuple[str, ...]:
        """强制覆盖的产物键（只读）。

        Returns:
            tuple[str, ...]: 产物键元组。
        """

    def validate_params(self, params: Mapping[str, object]) -> None:
        """校验声明参数（不合法抛 `ValueError`）。

        只放行「收紧词表 / 列名 / 文案」类参数；把判据关闭的布尔项
        （如 `forbid_past=false`）一律拒绝——否则改个参数就能关掉合规门。

        Args:
            params: 声明里的 `guards[].params`。

        Raises:
            ValueError: 参数不合法（未知键 / 类型错 / 关闭判据）。
        """

    def check(
        self, *, text: str, content: bytes | None, params: Mapping[str, object]
    ) -> GuardViolation | None:
        """对一份产物跑合规检查。

        Args:
            text: 产物内联文本（无内联文本时传空串）。
            content: 产物字节（CSV 等文件型产物；无则传 None）。
            params: 已通过 `validate_params` 的声明参数。

        Returns:
            GuardViolation | None: 违规返回违规记录；合规返回 None。
        """


#: 规则名 → 规则实现（模块加载时由各规则模块 `register` 进来）
_RULES: dict[str, GuardRule] = {}


def register(rule: GuardRule) -> GuardRule:
    """注册一条合规规则（同名覆盖，允许测试重注册）。

    Args:
        rule: 规则实现（须满足 `GuardRule` 协议）。

    Returns:
        GuardRule: 原规则（便于装饰器式用法）。

    Raises:
        TypeError: 实现不满足 `GuardRule` 协议。
        ValueError: 规则名形态不合法。
    """
    if not isinstance(rule, GuardRule):
        raise TypeError(f"合规规则不满足 GuardRule 协议：{rule!r}")
    name = rule.name
    if not (RULE_NAME_MIN_LEN <= len(name) <= RULE_NAME_MAX_LEN):
        raise ValueError(f"合规规则名不合法：{name!r}")
    _RULES[name] = rule
    return rule


def resolve_guard(name: str) -> GuardRule | None:
    """按规则名取实现。

    Args:
        name: 规则名（声明 `guards[].rule`）。

    Returns:
        GuardRule | None: 规则实现；未注册返回 None。
    """
    return _RULES.get(name)


def required_rules_for(output_key: str) -> tuple[str, ...]:
    """取强制覆盖某个产物键的规则名列表。

    Args:
        output_key: 产物键。

    Returns:
        tuple[str, ...]: 规则名元组（按注册表键序，稳定可断言）。
    """
    return tuple(
        sorted(name for name, rule in _RULES.items() if output_key in rule.required_output_keys)
    )


def all_rule_names() -> tuple[str, ...]:
    """列出全部已注册规则名（升序）。

    Returns:
        tuple[str, ...]: 规则名元组。
    """
    return tuple(sorted(_RULES))


__all__: list[str] = [
    "RULE_NAME_MAX_LEN",
    "RULE_NAME_MIN_LEN",
    "GuardRule",
    "GuardViolation",
    "all_rule_names",
    "register",
    "required_rules_for",
    "resolve_guard",
]
