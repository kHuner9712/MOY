"""插件域仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `PluginStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from app.db.repositories.plugin.records import (
    CatalogRecord,
    InstallationRecord,
    VersionRecord,
)


class PluginStore(Protocol):
    """插件域存储契约（内存 / PostgreSQL 双实现）。"""

    async def ensure_versions(self, records: Sequence[VersionRecord]) -> int:
        """幂等登记声明快照，返回新插入条数。"""
        ...

    async def get_version(self, plugin_id: str, version: str) -> VersionRecord | None:
        """按版本读取声明快照。"""
        ...

    async def list_versions(self, plugin_id: str) -> list[VersionRecord]:
        """列出某插件的全部版本（新→旧）。"""
        ...

    async def list_catalog(self, *, category: str = "", keyword: str = "") -> list[CatalogRecord]:
        """列出可售目录（`status='available'`，按排序号）。"""
        ...

    async def get_catalog(self, plugin_id: str) -> CatalogRecord | None:
        """读取目录行（含下架行，由服务层判 `16002`）。"""
        ...

    async def catalogs_of(self, plugin_ids: Sequence[str]) -> dict[str, CatalogRecord]:
        """批量读取目录行（**含下架行**；缺失的 id 不进字典）。

        为什么要批量版（审计 P2-6）：已安装列表按插件逐条 `get_catalog()`，
        N 个插件就是 N 次查询（N+1）。批量版一次 `IN (...) / ANY(...)` 取回，
        缺失与下架两种情形由「字典里有没有这个键」「`status` 是什么」分别表达，
        语义与逐条调用完全一致。
        """
        ...

    async def count_catalog_by_category(self) -> dict[str, int]:
        """按分类统计**可售**目录条数（口径同 `list_catalog()` 无参调用）。

        为什么不在服务层用 `list_catalog()` 数（审计 P2-6）：那会把整张目录
        （含 description 正文）读进内存只为计数。`GROUP BY` 只回 6 行。
        """
        ...

    async def set_catalog_rating(
        self, plugin_id: str, *, rating_avg: float, rating_count: int
    ) -> None:
        """写回评分聚合（评价提交后重算）。"""
        ...

    async def adjust_install_count(self, plugin_id: str, delta: int) -> None:
        """调整安装企业数（安装 +1 / 卸载 -1，不为负）。"""
        ...

    async def get_installation(self, tenant_id: str, plugin_id: str) -> InstallationRecord | None:
        """读取安装行（含已卸载行）。"""
        ...

    async def list_installations(
        self, tenant_id: str, *, status: str = ""
    ) -> list[InstallationRecord]:
        """列出企业的安装行。"""
        ...

    async def save_installation(self, record: InstallationRecord) -> InstallationRecord:
        """写入（或按 `(tenant_id, plugin_id)` 更新）安装行。"""
        ...

    async def create_installation(self, record: InstallationRecord) -> InstallationRecord:
        """**仅插入**安装行；行已存在时抛 `InstallationConflictError`（唯一键语义）。"""
        ...

    async def swap_version(
        self,
        tenant_id: str,
        plugin_id: str,
        *,
        expected_version: str,
        target_version: str,
    ) -> InstallationRecord | None:
        """乐观锁切换版本指针；`version` 已被他人改动时返回 `None`（指针竞争）。"""
        ...

    async def get_grants(self, tenant_id: str, plugin_id: str, user_id: str) -> set[str]:
        """读取某成员在某插件上的**有效**能力集合。"""
        ...

    async def set_grants(
        self,
        tenant_id: str,
        plugin_id: str,
        user_id: str,
        capabilities: Iterable[str],
    ) -> set[str]:
        """覆盖写入成员能力（新增授权 / 撤销未列出的能力），返回有效集合。"""
        ...

    async def list_policies(self, tenant_id: str) -> dict[str, bool]:
        """读取企业能力开关（未登记视为关闭）。"""
        ...

    async def save_policies(
        self, tenant_id: str, values: Mapping[str, bool], actor_id: str
    ) -> dict[str, bool]:
        """写入企业能力开关（仅 `org.*`）。"""
        ...


__all__: list[str] = ["PluginStore"]
