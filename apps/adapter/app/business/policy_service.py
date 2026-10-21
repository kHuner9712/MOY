"""可见性判定（阶段二 B05，架构文档 6.3 / PRD P2-KB-03 验收 1-2）。

**同一套判定，三处复用**（权限隔离红线）：

| 调用点 | 方法 |
| --- | --- |
| 知识库列表（R2） | `visible_base_ids()` → 传给存储层的观察者上下文 |
| 独立检索（R7 / KB4） | `can_read()` → 不可见抛 `20003` |
| 对话问答（M1/M2） | `filter_authorized()` → 未授权库**静默跳过**（不报错、不引用） |

判定规则（架构文档 4.8 权限点矩阵 + 2.2 第 4 条）：

- `organization`（全企业可见）：租户内全员可见；
- `private`（仅自己可见）：仅所有者与管理可见；
- `restricted`（指定成员可见）：所有者、管理员，或授权表命中（用户直授 /
  所在部门及子部门 / 角色）可见；
- 组织管理员（`owner` / `org_admin` / `admin` / 平台通配 `*`）可见本租户全部知识库。

**缓存**：按「租户 + 用户」缓存 5 秒（`OA_PERMISSION_CACHE_TTL_SECONDS`），
保证「成员移出部门 ≤5s 失效」而无需跨进程广播；授权写入方（KB6/KB7、成员变更）
可调用 `invalidate_tenant()` 提前失效，做到「下一请求即生效」。

> 本阶段可见库集合由「读租户内知识库元数据 + 一次批量授权查询」组装（无 N+1）；
> B08 组织域落地后改为 SQL 侧 UNION + 共享缓存，对外语义不变。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantStore, get_grant_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    KnowledgeBaseStore,
    get_knowledge_base_store,
)

logger = get_logger(__name__)

#: 组织管理员角色（令牌 `roles` 中出现任一即视为组织管理员）
ORG_ADMIN_ROLES: Final[frozenset[str]] = frozenset({"owner", "org_admin", "admin"})

#: 访客角色（只读，任何写操作一律拒绝）
GUEST_ROLES: Final[frozenset[str]] = frozenset({"guest"})

#: 通配角色（平台运维令牌）
WILDCARD_ROLE: Final[str] = "*"

#: 全企业可见
VISIBILITY_ORGANIZATION: Final[str] = "organization"

#: 知识库 ID 前缀（唯一规范形态）
BASE_ID_PREFIX: Final[str] = "kb_"


def normalize_base_id(raw: str) -> str:
    """把知识库 ID 归一化为唯一规范形态（`kb_` 前缀）。

    **一个对象只能有一个判定键**（QA B05-P0-01）：底座原生 ID 与 `kb_<原生>`
    指向同一个底座对象，若判权按拼写分别处理，未授权成员只要换一种写法就能
    让「本地元数据不存在」的分支短路，从而绕过 `restricted` 判定。
    因此所有入口（R7 / M1 / M2 / KB4）先归一化，再按同一键判权。

    Args:
        raw: 原始知识库 ID（`kb_...` 或底座原生 ID）。

    Returns:
        str: 规范形态；入参为空返回空串。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith(BASE_ID_PREFIX):
        return value
    return f"{BASE_ID_PREFIX}{value}"


def normalized_roles(auth: AuthContext) -> set[str]:
    """取归一化后的角色集合（小写去空白）。

    Args:
        auth: 鉴权上下文。

    Returns:
        set[str]: 角色集合。
    """
    return {str(role).strip().lower() for role in auth.roles}


def has_admin_role(auth: AuthContext) -> bool:
    """判断是否为组织管理员（含平台通配角色）。

    Args:
        auth: 鉴权上下文。

    Returns:
        bool: 命中管理员角色集合时返回 True。
    """
    roles = normalized_roles(auth)
    if WILDCARD_ROLE in roles:
        return True
    return bool(roles & ORG_ADMIN_ROLES)


def is_guest_role(auth: AuthContext) -> bool:
    """判断是否为访客（只读角色，管理员不算）。

    Args:
        auth: 鉴权上下文。

    Returns:
        bool: 命中访客角色且非管理员时返回 True。
    """
    if has_admin_role(auth):
        return False
    return bool(normalized_roles(auth) & GUEST_ROLES)


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """某个用户在当前租户下的可见性与授权快照。

    Attributes:
        tenant_id: 租户 ID。
        user_id: 用户 ID。
        is_admin: 是否组织管理员。
        granted_base_ids: 通过授权表可见的知识库 ID 集合（仅非 `organization` 库有意义）。
        permissions: `base_id → viewer|editor`（授权级别，用于 `my_role`）。
        expires_at: 过期时刻（`time.monotonic()` 基准）。
    """

    tenant_id: str
    user_id: str
    is_admin: bool = False
    granted_base_ids: frozenset[str] = field(default_factory=frozenset)
    permissions: Mapping[str, str] = field(default_factory=dict)
    expires_at: float = 0.0

    def permission_of(self, base_id: str) -> str | None:
        """取该库上的授权级别。

        Args:
            base_id: 知识库 ID。

        Returns:
            str | None: `viewer` / `editor`；无授权返回 None。
        """
        return self.permissions.get(base_id)


class PolicyService:
    """可见性判定服务（列表 / 检索 / 问答同一套）。

    Attributes:
        _settings: 配置实例。
        _bases: 知识库元数据存储。
        _grants: 授权存储。
        _clock: 单调时钟（测试可注入）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        bases: KnowledgeBaseStore | None = None,
        grants: GrantStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            bases: 可选知识库存储（测试注入）。
            grants: 可选授权存储（测试注入）。
            clock: 可选单调时钟（测试注入，用于验证 5s 缓存失效）。
        """
        self._settings = settings or get_settings()
        self._bases: KnowledgeBaseStore = bases or get_knowledge_base_store(self._settings)
        self._grants: GrantStore = grants or get_grant_store(self._settings)
        self._clock: Callable[[], float] = clock or time.monotonic
        self._cache: dict[tuple[str, str], PolicySnapshot] = {}

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    async def snapshot(self, auth: AuthContext) -> PolicySnapshot:
        """取（或按 TTL 重建）该用户的可见性快照。

        Args:
            auth: 鉴权上下文。

        Returns:
            PolicySnapshot: 可见性与授权快照。
        """
        key = (auth.tenant_id, auth.user_id)
        cached = self._cache.get(key)
        now = self._clock()
        if cached is not None and cached.expires_at > now:
            return cached

        snapshot = await self._load(auth, now)
        self._cache[key] = snapshot
        logger.debug(
            "policy_snapshot_loaded",
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            granted=len(snapshot.granted_base_ids),
            is_admin=snapshot.is_admin,
        )
        return snapshot

    async def _load(self, auth: AuthContext, now: float) -> PolicySnapshot:
        """从存储组装快照（一次批量授权查询，无 N+1）。

        Args:
            auth: 鉴权上下文。
            now: 当前单调时钟值。

        Returns:
            PolicySnapshot: 新建快照。
        """
        admin = has_admin_role(auth)
        permissions = dict(await self._grants.resolve_permissions(auth.tenant_id, auth.user_id))
        granted = frozenset(base_id for base_id, permission in permissions.items() if permission)
        ttl = max(float(self._settings.permission_cache_ttl_seconds), 0.0)
        return PolicySnapshot(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            is_admin=admin,
            granted_base_ids=granted,
            permissions=permissions,
            expires_at=now + ttl,
        )

    # ------------------------------------------------------------------
    # 单一判定
    # ------------------------------------------------------------------
    def is_visible(
        self,
        record: KnowledgeBaseRecord,
        auth: AuthContext,
        snapshot: PolicySnapshot | None = None,
    ) -> bool:
        """判断单个知识库对当前用户是否可见（同步版，仅静态规则 + 快照）。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。
            snapshot: 快照（缺省时只用静态规则：全企业可见 / 所有者 / 管理员）。

        Returns:
            bool: 可见返回 True。
        """
        if record.is_deleted:
            return False
        if record.visibility == VISIBILITY_ORGANIZATION:
            return True
        if has_admin_role(auth):
            return True
        if record.owner_user_id and record.owner_user_id == auth.user_id:
            return True
        if snapshot is None:
            return False
        return record.base_id in snapshot.granted_base_ids

    async def can_read(self, auth: AuthContext, record: KnowledgeBaseRecord) -> bool:
        """判断单个知识库是否可读（异步版，带快照）。

        Args:
            auth: 鉴权上下文。
            record: 知识库记录。

        Returns:
            bool: 可读返回 True。
        """
        snapshot = await self.snapshot(auth)
        return self.is_visible(record, auth, snapshot)

    async def grant_permission(self, auth: AuthContext, base_id: str) -> str | None:
        """取当前用户在该库上的授权级别（用于 `my_role`）。

        Args:
            auth: 鉴权上下文。
            base_id: 知识库 ID。

        Returns:
            str | None: `viewer` / `editor`；无授权返回 None。
        """
        snapshot = await self.snapshot(auth)
        return snapshot.permission_of(base_id)

    async def visible_base_ids(self, auth: AuthContext) -> frozenset[str]:
        """取通过授权表额外可见的知识库 ID 集合。

        Args:
            auth: 鉴权上下文。

        Returns:
            frozenset[str]: 授权可见的知识库 ID 集合。
        """
        snapshot = await self.snapshot(auth)
        return snapshot.granted_base_ids

    # ------------------------------------------------------------------
    # 批量过滤（M1/M2 静默跳过）
    # ------------------------------------------------------------------
    async def filter_authorized(
        self, auth: AuthContext | None, base_ids: Sequence[str]
    ) -> list[str]:
        """过滤出当前用户有权使用的知识库（未授权库静默剔除）。

        **fail-closed（QA B05-P0-01）**：本地查不到元数据、或元数据已软删除的知识库
        一律剔除，不再「无法判定就放行」——R2 列表本来就只返回元数据内的库，
        放行只会造成「列表看不到、检索拿得到」的越权形态（架构 §4.8）。
        历史库若确需继续检索，必须走元数据回填，不接受按 ID 形态开洞。

        Args:
            auth: 鉴权上下文（None 表示无鉴权信息，原样返回）。
            base_ids: 请求携带的知识库 ID 列表。

        Returns:
            list[str]: 过滤后的知识库 ID 列表（规范形态、去重、保持原顺序）。
        """
        if auth is None or not base_ids:
            return list(dict.fromkeys(base_ids))
        snapshot = await self.snapshot(auth)
        allowed: list[str] = []
        skipped = 0
        for raw_id in dict.fromkeys(base_ids):
            base_id = normalize_base_id(raw_id)
            if not base_id:
                skipped += 1
                continue
            record = await self._bases.get(auth.tenant_id, base_id)
            if record is None or record.is_deleted:
                skipped += 1
                continue
            if self.is_visible(record, auth, snapshot):
                allowed.append(base_id)
            else:
                skipped += 1
        if skipped:
            logger.info(
                "policy_bases_silently_skipped",
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                skipped=skipped,
                allowed=len(allowed),
            )
        return allowed

    # ------------------------------------------------------------------
    # 失效
    # ------------------------------------------------------------------
    def invalidate(self, tenant_id: str, user_id: str = "") -> None:
        """失效指定租户（或某个用户）的快照。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID；为空时失效该租户全部用户。
        """
        if user_id:
            self._cache.pop((tenant_id, user_id), None)
            return
        for key in [item for item in self._cache if item[0] == tenant_id]:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """清空全部缓存（测试隔离用）。"""
        self._cache.clear()


# ===========================================================================
# 进程级单例
#
# 必须是单例：授权写入方（KB6/KB7、成员变更）调用 `invalidate_tenant()` 后，
# 列表 / 检索 / 问答三处必须**同时**看到新结果，否则出现「列表已隐藏但检索仍可读」。
# ===========================================================================
_policy_singleton: PolicyService | None = None


def get_policy_service(settings: Settings | None = None) -> PolicyService:
    """获取可见性判定服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        PolicyService: 进程内唯一实例。
    """
    global _policy_singleton
    if _policy_singleton is None:
        _policy_singleton = PolicyService(settings)
    return _policy_singleton


def reset_policy_service() -> None:
    """重置单例（测试隔离用）。"""
    global _policy_singleton
    if _policy_singleton is not None:
        _policy_singleton.clear()
    _policy_singleton = None


__all__: list[str] = [
    "BASE_ID_PREFIX",
    "GUEST_ROLES",
    "ORG_ADMIN_ROLES",
    "VISIBILITY_ORGANIZATION",
    "WILDCARD_ROLE",
    "PolicyService",
    "PolicySnapshot",
    "get_policy_service",
    "has_admin_role",
    "is_guest_role",
    "normalize_base_id",
    "normalized_roles",
    "reset_policy_service",
]
