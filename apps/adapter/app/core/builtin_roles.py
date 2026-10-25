"""内置角色与权限点目录（架构文档 §4.8 矩阵的**唯一**代码实现）。

**为什么要下沉到 `core`**（审计 P1-4 / P1-7）：这份常量原先定义在
`business/policy_service.py`，导致

- `db/repositories/role_repo.py`（内置角色播种）与 `core/security.py`（令牌判权）
  必须**反向依赖业务层**才能拿到矩阵——仓储和内核跟着业务服务一起漂移；
- 「判权用一套、播种用另一套」的双口径风险随每次改动放大。

角色矩阵是**跨层共享的词汇表**（鉴权内核 / 角色播种 / 业务判权都用它），
不是某个业务服务的实现细节，因此归 `core`。`business/policy_service.py`
从这里 import 后原样 re-export，既有调用方 import 路径零改动。

本模块只放**常量**，不 import 任何其他 `app` 模块（`core` 必须是最底层）。
"""

from __future__ import annotations

from typing import Final

#: 角色键：企业所有者
ROLE_OWNER: Final[str] = "owner"

#: 角色键：组织管理员
ROLE_ORG_ADMIN: Final[str] = "org_admin"

#: 角色键：普通成员
ROLE_MEMBER: Final[str] = "member"

#: 角色键：访客
ROLE_GUEST: Final[str] = "guest"

#: 角色键：平台运营（仅运营后台）
ROLE_OPS: Final[str] = "ops_operator"

#: 16 个业务权限点（架构文档 §4.8）
BUSINESS_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "knowledge:base:create",
        "knowledge:base:update",
        "knowledge:base:delete",
        "knowledge:document:upload",
        "knowledge:document:delete",
        "knowledge:grant:manage",
        "knowledge:base:read",
        "tool:connection:manage",
        "tool:execute",
        "team:member:invite",
        "team:member:update",
        "team:structure:manage",
        "team:role:manage",
        "billing:subscription:read",
        "billing:subscription:manage",
        "audit:log:read",
    }
)

#: 4 个运营权限点（架构文档 §4.8）
OPS_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "ops:console:access",
        "ops:order:confirm",
        "ops:tenant:manage",
        "ops:settings:manage",
    }
)

#: 已知权限点目录（业务 + 运营，共 20 个）。
#:
#: 用途**只有一个**：判断「把权限点当角色签发」的兼容形态是否合法。
#: 为什么必须是白名单而不是 `":" in role`：后者只要令牌里塞一个任意
#: `x:y` 形态的字符串就能凭空白拿到一个权限点——若该字符串恰好等于某个
#: 端点要求的权限点（如 `tool:execute`），等于签名方之外的人也能自造权限。
#: 白名单把口径收敛到目录内，新增权限点必须先落 `BUSINESS_PERMISSIONS` /
#: `OPS_PERMISSIONS`，与架构 §4.8 矩阵保持单一真源。
KNOWN_PERMISSION_POINTS: Final[frozenset[str]] = BUSINESS_PERMISSIONS | OPS_PERMISSIONS

#: 内置角色 → 权限点（架构文档 §4.8 矩阵的**唯一**代码实现）。
#:
#: 矩阵中的 🔸 表示「**拥有权限点**，但对象级仍受限（仅被授权库 / 个人数据）」，
#: 因此 🔸 计入本表；真正的对象级判定由 `is_visible()` 与各业务服务负责。
#: 这是「权限点层 → 对象级」两层分离的口径，缺一层都会造成越权或误拒。
BUILTIN_ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    ROLE_OWNER: BUSINESS_PERMISSIONS,
    ROLE_ORG_ADMIN: BUSINESS_PERMISSIONS,
    ROLE_MEMBER: frozenset(
        {
            "knowledge:base:create",
            "knowledge:base:update",
            "knowledge:base:delete",
            "knowledge:document:upload",
            "knowledge:document:delete",
            "knowledge:base:read",
            "tool:connection:manage",
            "tool:execute",
            "billing:subscription:read",
        }
    ),
    ROLE_GUEST: frozenset(
        {
            "knowledge:base:read",
            "tool:connection:manage",
            "billing:subscription:read",
        }
    ),
    ROLE_OPS: OPS_PERMISSIONS,
}

#: 内置角色键（B08 建 4 个内置角色，不可删除）
BUILTIN_ROLE_KEYS: Final[tuple[str, ...]] = (
    ROLE_OWNER,
    ROLE_ORG_ADMIN,
    ROLE_MEMBER,
    ROLE_GUEST,
)

__all__: list[str] = [
    "BUILTIN_ROLE_KEYS",
    "BUILTIN_ROLE_PERMISSIONS",
    "BUSINESS_PERMISSIONS",
    "KNOWN_PERMISSION_POINTS",
    "OPS_PERMISSIONS",
    "ROLE_GUEST",
    "ROLE_MEMBER",
    "ROLE_OPS",
    "ROLE_ORG_ADMIN",
    "ROLE_OWNER",
]
