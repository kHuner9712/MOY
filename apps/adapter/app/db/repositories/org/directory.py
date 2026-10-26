"""组织架构仓储 · 计量目录适配器（`OrgUsageDirectory`）。

**为什么单独一个文件**：`OrgUsageDirectory` 是"给 `UsageService` 用的
`UsageDirectory` 协议实现"——它**只依赖 `OrgStore` 协议**（构造注入），
不依赖任何具体后端，也不是"存储契约"的一部分（不参与 G-38 的三元组判定）。

放 `records.py` 会成环（records 在协议下层，却要 import 上层的 `OrgStore`）；
放 `memory.py` / `postgres.py` 会让另一个后端或使用方被迫 import 具体实现。
独立成 `directory.py` 后依赖方向是 `records ← protocol ← directory`，
与两个实现并列，谁也不依赖谁。

`get_org_directory()` 这个工厂**不在这里**：它要 `get_org_store()`（单例选择器），
与其余 `get_*_store` / `reset_*_store` 一起放 `__init__.py`，可避免
`__init__ ↔ directory` 成环，也不需要函数内延迟导入。
"""

from __future__ import annotations

from app.db.repositories.org.protocol import OrgStore


class OrgUsageDirectory:
    """计量服务所需的组织信息解析器（D-12 部门维度归属）。

    实现 `UsageService` 期望的 `UsageDirectory` 协议：
    主部门用于写 `usage_monthly(scope_type='department')`，展示名用于 BL5 分项。
    """

    def __init__(self, store: OrgStore) -> None:
        """初始化。

        Args:
            store: 组织架构存储。
        """
        self._store = store

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        """取用户的主部门 ID。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 主部门 ID；无关联返回空串。
        """
        links = await self._store.links_of_user(tenant_id, user_id)
        for link in links:
            if link.is_primary:
                return link.department_id
        return links[0].department_id if links else ""

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        """取用户展示名。

        成员展示名由身份底座提供，本阶段以用户 ID 兜底（不虚构姓名）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 展示名。
        """
        del tenant_id
        return user_id


__all__: list[str] = ["OrgUsageDirectory"]
