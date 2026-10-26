"""兼容层（deprecated shim）：`app.db.repositories.role_repo` → `app.db.repositories.role`。

**真源已迁到包**：`app/db/repositories/role/{records,protocol,memory,postgres,__init__}.py`
（审计 P1-8：仓储上帝文件 + 双后端镜像；切片 2A 以本模块为拆分布局样板）。

**本文件只做 re-export**，不得出现任何类/函数定义——包形态与 `*_repo.py` 同名并存时，
`deploy/validate_repository_parity.py`（G-38）要求本文件是**纯壳**：一旦这里重新长出
实现，"薄兼容层"就会慢慢变回第二份真源，拆分等于没做（G-38 判 exit 2）。

**为什么保留而不是直接删**：仍有 20 处调用点（`business/{role,member,policy}_service.py`、
`core/security.py`、`tests/**`）按旧路径 import。本轮只做拆分样板、不动调用点，
把改动面压到"移动 + re-export"，任何一跳都能单独回滚。

**弃用计划（切片 2C）**：
1. 20 处调用点改为 `from app.db.repositories.role import ...`；
2. 删本文件；
3. 同步修改 `app/core/security.py:486` 的延迟导入路径，并**删掉**
   `deploy/import_direction_baseline.txt` 第 43 行
   （`R-CORE-DB app/core/security.py app.db.repositories.role_repo deferred -- P1-7 ...`）——
   路径改名后旧基线行会变 stale，G-37 会正确地判红，逼迫两处同步收口。
"""

from __future__ import annotations

from app.db.repositories.role import (
    BUILTIN_ROLE_DISPLAY_NAMES,
    CATALOG_TABLE,
    MEMBER_ROLE_TABLE,
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUSES,
    PERMISSION_CATALOG_SEED,
    PLATFORM_TENANT,
    ROLE_ID_PREFIX,
    ROLE_PERMISSION_TABLE,
    ROLE_TABLE,
    InMemoryRoleStore,
    PermissionCatalogEntry,
    PostgresRoleStore,
    RoleRecord,
    RoleStore,
    builtin_role_id,
    get_role_store,
    reset_role_store,
    role_display_name,
)

__all__: list[str] = [
    "BUILTIN_ROLE_DISPLAY_NAMES",
    "CATALOG_TABLE",
    "MEMBER_ROLE_TABLE",
    "MEMBER_STATUSES",
    "MEMBER_STATUS_ACTIVE",
    "PERMISSION_CATALOG_SEED",
    "PLATFORM_TENANT",
    "ROLE_ID_PREFIX",
    "ROLE_PERMISSION_TABLE",
    "ROLE_TABLE",
    "InMemoryRoleStore",
    "PermissionCatalogEntry",
    "PostgresRoleStore",
    "RoleRecord",
    "RoleStore",
    "builtin_role_id",
    "get_role_store",
    "reset_role_store",
    "role_display_name",
]
