"""仓储基类（架构文档 3.5「应用层双保险」）。

两条硬约束：

1. **租户条件强制注入**：所有查询通过 `tenant_clause()` 拼接
   `tenant_id = :tenant_id`，参数由 `params()` 统一补齐，仓储实现**不得**手写完整
   WHERE 而绕过租户条件。
2. **SQL 只允许常量拼接**：表名 / 列名是类常量，值一律走命名参数绑定；
   `assert_identifier()` 对常量做一次白名单校验，防止后续维护者误把用户输入拼进 SQL。

本类不持有事务控制：事务由 `UnitOfWork` 拥有（一个 HTTP 请求一个事务）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.db.session import UnitOfWork

#: 合法 SQL 标识符（小写字母、数字、下划线，且不以数字开头）
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z_][a-z0-9_]*$")


def assert_identifier(name: str) -> str:
    """校验 SQL 标识符合法性（仅用于表名/列名常量）。

    Args:
        name: 标识符。

    Returns:
        str: 原样返回合法标识符。

    Raises:
        ApiError: 标识符非法（30003，属编码缺陷）。
    """
    if not _IDENTIFIER_PATTERN.match(name):
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            internal_detail=f"illegal sql identifier: {name!r}",
        )
    return name


class BaseRepository:
    """仓储基类。

    Attributes:
        table: 表名（子类以类常量声明，必须为合法标识符）。
        tenant_column: 租户列名，默认 `tenant_id`。
    """

    table: ClassVar[str] = ""
    tenant_column: ClassVar[str] = "tenant_id"

    def __init__(self, uow: UnitOfWork) -> None:
        """初始化仓储。

        Args:
            uow: 已进入的工作单元（事务已开启、租户会话变量已设置）。

        Raises:
            ApiError: 子类未声明合法表名（30003）。
        """
        self._uow = uow
        assert_identifier(self.table)
        assert_identifier(self.tenant_column)

    @property
    def uow(self) -> UnitOfWork:
        """所在工作单元。"""
        return self._uow

    @property
    def tenant_id(self) -> str:
        """当前事务的租户 ID（唯一权威值）。"""
        return self._uow.tenant_id

    def tenant_clause(self, alias: str = "") -> str:
        """构造租户过滤条件（`tenant_id = :tenant_id`）。

        Args:
            alias: 可选表别名（如 `kb`、`d`），用于 JOIN 场景。

        Returns:
            str: 可直接拼进 WHERE 的片段（值仍走参数绑定）。
        """
        prefix = f"{assert_identifier(alias)}." if alias else ""
        return f"{prefix}{self.tenant_column} = :tenant_id"

    def params(self, **extra: Any) -> dict[str, Any]:
        """构造命名参数（自动补齐 `tenant_id`）。

        Args:
            **extra: 额外命名参数。

        Returns:
            dict[str, Any]: 含 `tenant_id` 的参数表。
        """
        merged: dict[str, Any] = {"tenant_id": self.tenant_id}
        merged.update(extra)
        return merged

    async def fetch_all(
        self, sql: str, parameters: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        """执行查询并返回多行。

        Args:
            sql: SQL 语句。
            parameters: 命名参数（应含 `tenant_id`）。

        Returns:
            list[Mapping[str, Any]]: 行列表。
        """
        return await self._uow.fetch_all(sql, parameters or self.params())

    async def fetch_one(
        self, sql: str, parameters: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any] | None:
        """执行查询并返回单行。

        Args:
            sql: SQL 语句。
            parameters: 命名参数。

        Returns:
            Mapping[str, Any] | None: 行映射；无结果返回 None。
        """
        return await self._uow.fetch_one(sql, parameters or self.params())

    async def fetch_scalar(self, sql: str, parameters: Mapping[str, Any] | None = None) -> Any:
        """执行查询并返回标量。

        Args:
            sql: SQL 语句。
            parameters: 命名参数。

        Returns:
            Any: 标量值。
        """
        return await self._uow.fetch_scalar(sql, parameters or self.params())

    async def execute(self, sql: str, parameters: Mapping[str, Any] | None = None) -> Any:
        """执行写语句（不提交，由 `UnitOfWork` 统一提交）。

        Args:
            sql: SQL 语句。
            parameters: 命名参数。

        Returns:
            Any: 执行结果。
        """
        return await self._uow.execute(sql, parameters or self.params())


__all__ = ["BaseRepository", "assert_identifier"]
