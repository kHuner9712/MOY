"""会话能力声明的请求模型（B6，只增不改对外 API）。

`capabilities` 是 `CreateTaskRequest` 的可选字段：

- 不传：旧行为不变，按调用者权限继承（登记为待收敛路径）；
- 传了：三个维度必须全部给出，空数组表示该维度全部拒绝；
- 未知值一律由校验器拒绝，经过 FastAPI 校验异常处理器映射为 `20001`。

**为什么不放在 `schemas/capability.py`**：那个模块是 W1 能力推荐的响应契约
（`CapabilityCard` 等），与会话能力集（授权输入）是两套语义；混在一起会让
「推荐卡」与「授权声明」的演化互相牵连。本模块只承载 B6 的授权声明。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.capability import (
    CAPABILITY_DIMENSIONS,
    KNOWN_ARTIFACT_FORMATS,
    KNOWN_RESOURCE_TYPES,
    KNOWN_TOOL_TYPES,
    MAX_CAPABILITY_ITEM_LENGTH,
    MAX_CAPABILITY_ITEMS,
    CapabilitySet,
)

_CAPABILITY_CATALOGS: dict[str, frozenset[str]] = {
    "tools": KNOWN_TOOL_TYPES,
    "resource_types": KNOWN_RESOURCE_TYPES,
    "artifact_formats": KNOWN_ARTIFACT_FORMATS,
}


def _validate_items(values: list[str], dimension: str) -> list[str]:
    """校验某一维度的声明值。

    Args:
        values: 请求提交的字符串列表。
        dimension: 维度名（用于错误定位）。

    Returns:
        list[str]: 去重后的规范值（保持顺序）。

    Raises:
        ValueError: 条目为空、超长，或不在已知目录内。
    """
    catalog = _CAPABILITY_CATALOGS[dimension]
    normalized: list[str] = []
    for item in values:
        value = str(item).strip()
        if not value:
            raise ValueError(f"{dimension} 包含空值")
        if len(value) > MAX_CAPABILITY_ITEM_LENGTH:
            raise ValueError(f"{dimension} 条目过长：{value[:8]}…")
        if value not in catalog:
            # 只回显能力标识（不是用户业务输入），便于调用方定位拼写错误。
            raise ValueError(f"{dimension} 不支持：{value}")
        if value not in normalized:
            normalized.append(value)
    if len(normalized) > MAX_CAPABILITY_ITEMS:
        raise ValueError(f"{dimension} 条目数超限：{MAX_CAPABILITY_ITEMS}")
    return normalized


class CapabilityDeclaration(BaseModel):
    """会话/任务能力声明（三个维度必填，空数组合法）。

    Attributes:
        tools: 允许调用的工具类型。
        resource_types: 允许访问的资源类型。
        artifact_formats: 允许产出的产物格式。
    """

    tools: list[str] = Field(
        max_length=MAX_CAPABILITY_ITEMS,
        description="允许调用的工具类型（∈ 工具注册表；空数组表示不允许任何工具）",
    )
    resource_types: list[str] = Field(
        max_length=MAX_CAPABILITY_ITEMS,
        description="允许访问的资源类型（空数组表示不允许任何资源）",
    )
    artifact_formats: list[str] = Field(
        max_length=MAX_CAPABILITY_ITEMS,
        description="允许产出的产物格式（空数组表示不允许产出任何产物）",
    )

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, values: list[str]) -> list[str]:
        """校验工具维度。

        Args:
            values: 请求值。

        Returns:
            list[str]: 规范化后的值。
        """
        return _validate_items(values, "tools")

    @field_validator("resource_types")
    @classmethod
    def _validate_resource_types(cls, values: list[str]) -> list[str]:
        """校验资源类型维度。

        Args:
            values: 请求值。

        Returns:
            list[str]: 规范化后的值。
        """
        return _validate_items(values, "resource_types")

    @field_validator("artifact_formats")
    @classmethod
    def _validate_artifact_formats(cls, values: list[str]) -> list[str]:
        """校验产物格式维度。

        Args:
            values: 请求值。

        Returns:
            list[str]: 规范化后的值。
        """
        return _validate_items(values, "artifact_formats")

    def to_capability_set(self) -> CapabilitySet:
        """转换为内核能力集。

        Returns:
            CapabilitySet: 三个维度均已声明的能力集。
        """
        return CapabilitySet(
            tools=frozenset(self.tools),
            resource_types=frozenset(self.resource_types),
            artifact_formats=frozenset(self.artifact_formats),
        )

    @classmethod
    def from_capability_set(cls, value: CapabilitySet) -> CapabilityDeclaration:
        """从内核能力集反向构造（重试复制声明时使用）。

        Args:
            value: 能力集。

        Returns:
            CapabilityDeclaration: 请求模型。

        Raises:
            ValueError: 能力集没有完整声明三个维度（无法作为客户端声明重放）。
        """
        if not value.fully_declared:
            raise ValueError("capability set is not fully declared")
        return cls(
            tools=sorted(value.tools or frozenset()),
            resource_types=sorted(value.resource_types or frozenset()),
            artifact_formats=sorted(value.artifact_formats or frozenset()),
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CapabilityDeclaration:
        """从数据库 JSONB 恢复声明。

        Args:
            payload: 形如 `{"tools": [...], ...}` 的字典。

        Returns:
            CapabilityDeclaration: 请求模型。

        Raises:
            ValueError: 维度缺失（说明存量快照不完整，不能当作完整声明重放）。
        """
        missing = set(CAPABILITY_DIMENSIONS) - set(payload)
        if missing:
            raise ValueError(f"capability snapshot missing dimensions: {sorted(missing)}")
        return cls(
            tools=list(payload["tools"]),
            resource_types=list(payload["resource_types"]),
            artifact_formats=list(payload["artifact_formats"]),
        )


__all__: list[str] = ["CapabilityDeclaration"]
