"""组织架构仓储 · records 层（`org_repo` 三拆后的数据记录与域常量）。

本文件是 `app/db/repositories/org/` 包拆分后的**最底层**：数据记录（dataclass）、
层级/前缀/根挂载点常量与部门 ID 归一化纯函数，**不依赖**
protocol / memory / postgres / directory，因此可被它们任意引用而不成环
（依赖方向 `records ← {protocol, directory} ← {memory, postgres} ← __init__`）。

**表名不在这里**：`DEPARTMENT_TABLE` / `MEMBER_TABLE` 只服务 PG 的 SQL，
放 `postgres.py`；**`_now_iso()` 也不在这里**——它只有一个实现（内存侧）在用，
按"私有辅助跟随实现文件"的口径留在 `memory.py`。

`MAX_DEPARTMENT_LEVEL` / `ROOT_PARENT_ID` 是两侧共用的**模型语义**（层级上限、
一级部门挂载点），内存侧建/改部门时用它判层级、PG 侧由 DDL 的 CHECK 兜底，
两处必须同值，因此放本层而不是任一实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.core.errors import ApiError, ErrorCode

#: 组织层级上限（架构文档 2.6 第 5 条；DB 侧另有 CHECK 兜底）
MAX_DEPARTMENT_LEVEL: Final[int] = 5


#: 部门 ID 前缀（唯一规范形态）
DEPARTMENT_ID_PREFIX: Final[str] = "dept_"


#: 根部门挂载点：未指定 `parent_id` 时的一级部门
ROOT_PARENT_ID: Final[str] = ""


@dataclass(frozen=True, slots=True)
class DepartmentRecord:
    """部门记录（`department` 行）。

    Attributes:
        tenant_id: 租户 ID。
        department_id: 部门 ID，形如 `dept_01HF8...`。
        org_id: 组织 ID。
        parent_id: 上级部门 ID（一级部门为空串）。
        name: 部门名称（1-64 字）。
        path: 物化路径，形如 `/dept_a/dept_b/`。
        level: 层级（1-5）。
        manager_user_id: 部门负责人用户 ID。
        member_count: 成员数快照。
        sort_order: 排序号。
        deleted_at: 软删除时间（ISO 8601）；未删除为 None。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str
    department_id: str
    org_id: str = ""
    parent_id: str = ROOT_PARENT_ID
    name: str = ""
    path: str = ""
    level: int = 1
    manager_user_id: str = ""
    member_count: int = 0
    sort_order: int = 0
    deleted_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_deleted(self) -> bool:
        """是否已软删除。"""
        return bool(self.deleted_at)


@dataclass(frozen=True, slots=True)
class MemberLink:
    """成员-部门关联（`department_member` 行）。

    Attributes:
        tenant_id: 租户 ID。
        department_id: 部门 ID。
        user_id: 用户 ID。
        is_primary: 是否主部门（全租户唯一，由部分唯一索引强制）。
        joined_at: 加入时间。
    """

    tenant_id: str
    department_id: str
    user_id: str
    is_primary: bool = False
    joined_at: str = ""


def normalize_department_id(raw: str) -> str:
    """把部门 ID 归一化为唯一规范形态（`dept_` 前缀，B08 的「一个对象一个 ID」）。

    Args:
        raw: 原始部门 ID（`dept_...` 或裸 ID）。

    Returns:
        str: 规范形态；入参为空返回空串。

    Raises:
        ApiError: 前缀形态非法（`deptxxx` 这类既非规范也不是裸 ID 的写法一律 20001）。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith(DEPARTMENT_ID_PREFIX):
        return value
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ApiError(ErrorCode.PARAM_INVALID, internal_detail=f"illegal department id: {raw!r}")
    return f"{DEPARTMENT_ID_PREFIX}{value}"


__all__: list[str] = [
    "DEPARTMENT_ID_PREFIX",
    "MAX_DEPARTMENT_LEVEL",
    "ROOT_PARENT_ID",
    "DepartmentRecord",
    "MemberLink",
    "normalize_department_id",
]
