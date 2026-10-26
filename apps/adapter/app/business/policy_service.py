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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.core.builtin_roles import BUILTIN_ROLE_KEYS as _BUILTIN_ROLE_KEYS
from app.core.builtin_roles import BUILTIN_ROLE_PERMISSIONS as _BUILTIN_ROLE_PERMISSIONS
from app.core.builtin_roles import BUSINESS_PERMISSIONS as _BUSINESS_PERMISSIONS
from app.core.builtin_roles import KNOWN_PERMISSION_POINTS as _KNOWN_PERMISSION_POINTS
from app.core.builtin_roles import OPS_PERMISSIONS as _OPS_PERMISSIONS
from app.core.builtin_roles import ROLE_GUEST as _ROLE_GUEST
from app.core.builtin_roles import ROLE_MEMBER as _ROLE_MEMBER
from app.core.builtin_roles import ROLE_OPS as _ROLE_OPS
from app.core.builtin_roles import ROLE_ORG_ADMIN as _ROLE_ORG_ADMIN
from app.core.builtin_roles import ROLE_OWNER as _ROLE_OWNER
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

#: 部门 ID 前缀（B08 唯一规范形态）
DEPARTMENT_ID_PREFIX: Final[str] = "dept_"

# ===========================================================================
# 角色与权限点矩阵（审计 P1-4 / P1-7：已下沉 `app.core.builtin_roles`）
#
# 为什么下沉：这份矩阵是三处共用的**词汇表**——`core/security.py`（令牌判权）、
# `db/repositories/role/records.py` 与同包 `{memory,postgres}.py`（目录种子与内置角色播种）、
# 本模块（对象级判定）全都要用。
# 留在业务层，鉴权内核与仓储就得反向依赖业务服务（P1-4 / P1-7 实测）。
#
# 为什么继续在这里 re-export：本模块是「可见性/权限判定」的业务入口，
# 既有调用方与测试按 `from app.business.policy_service import ROLE_OWNER ...`
# 取值。原样 re-export 让常量搬家**不触碰任何调用方**（零改动下沉）。
# ===========================================================================
ROLE_OWNER: Final[str] = _ROLE_OWNER
ROLE_ORG_ADMIN: Final[str] = _ROLE_ORG_ADMIN
ROLE_MEMBER: Final[str] = _ROLE_MEMBER
ROLE_GUEST: Final[str] = _ROLE_GUEST
ROLE_OPS: Final[str] = _ROLE_OPS
BUSINESS_PERMISSIONS: Final[frozenset[str]] = _BUSINESS_PERMISSIONS
OPS_PERMISSIONS: Final[frozenset[str]] = _OPS_PERMISSIONS
KNOWN_PERMISSION_POINTS: Final[frozenset[str]] = _KNOWN_PERMISSION_POINTS
BUILTIN_ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = _BUILTIN_ROLE_PERMISSIONS
BUILTIN_ROLE_KEYS: Final[tuple[str, ...]] = _BUILTIN_ROLE_KEYS


def permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    """把令牌角色集合展开为权限点集合（fail-closed：未知角色不贡献任何权限）。

    Args:
        roles: 角色键集合（令牌 `roles`）。

    Returns:
        frozenset[str]: 权限点集合；含通配角色时返回全集。
    """
    normalized = {str(role).strip().lower() for role in roles if str(role).strip()}
    if WILDCARD_ROLE in normalized:
        return BUSINESS_PERMISSIONS | OPS_PERMISSIONS
    granted: set[str] = set()
    for role in normalized:
        mapped = BUILTIN_ROLE_PERMISSIONS.get(role)
        if mapped is not None:
            granted.update(mapped)
        elif role in KNOWN_PERMISSION_POINTS:
            # 兼容以「权限点即角色」形式签发的令牌（阶段一既有形态）。
            # 只认目录内的已知权限点：`admin:*` / `x:y` 这类自造串不再得分。
            granted.add(role)
    return frozenset(granted)


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
        permission_points: 该用户拥有的权限点集合（权限点层，B07/B08 统一出口）。
        business_role: 业务库中的角色标识（空串表示业务库未登记该成员角色）。
        expires_at: 过期时刻（`time.monotonic()` 基准）。
    """

    tenant_id: str
    user_id: str
    is_admin: bool = False
    granted_base_ids: frozenset[str] = field(default_factory=frozenset)
    permissions: Mapping[str, str] = field(default_factory=dict)
    permission_points: frozenset[str] = frozenset()
    business_role: str = ""
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
        # 部门数据范围（D-13）：组织来源是业务库，不再读身份底座 group。
        # 部门授权（KB6 `subject_type=department`）必须拿到用户所属部门才能命中，
        # 否则「部门授权形同虚设」，会出现「成员被授了部门却看不到」的误拒。
        department_ids = await self._user_departments(auth)
        role_ids = await self._user_role_ids(auth)
        business_role = await self._user_role_key(auth)
        admin = self._resolve_admin(auth, business_role)
        permissions = dict(
            await self._grants.resolve_permissions(
                auth.tenant_id, auth.user_id, department_ids=department_ids, role_ids=role_ids
            )
        )
        granted = frozenset(base_id for base_id, permission in permissions.items() if permission)
        ttl = max(float(self._settings.permission_cache_ttl_seconds), 0.0)
        return PolicySnapshot(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            is_admin=admin,
            granted_base_ids=granted,
            permissions=permissions,
            permission_points=await self._resolve_points(auth, business_role),
            business_role=business_role,
            expires_at=now + ttl,
        )

    async def _user_departments(self, auth: AuthContext) -> tuple[str, ...]:
        """取用户所属部门（供部门级授权命中）。

        Args:
            auth: 鉴权上下文。

        Returns:
            tuple[str, ...]: 部门 ID 元组；组织域不可用时为空。
        """
        try:
            from app.db.repositories.org import get_org_store

            links = await get_org_store(self._settings).links_of_user(auth.tenant_id, auth.user_id)
        except Exception:  # pragma: no cover - 组织域不可用时退化为无部门
            logger.debug("policy_departments_unavailable", user_id=auth.user_id)
            return ()
        return tuple(link.department_id for link in links)

    async def _user_role_key(self, auth: AuthContext) -> str:
        """取业务库中的成员角色标识（权威源，D-13）。

        Args:
            auth: 鉴权上下文。

        Returns:
            str: 角色标识；业务库未登记或权限域不可用时为空串。
        """
        try:
            from app.db.repositories.role_repo import get_role_store

            return await get_role_store(self._settings).role_key_of_user(
                auth.tenant_id, auth.user_id
            )
        except Exception:  # pragma: no cover - 权限域不可用时回落令牌角色
            logger.debug("policy_role_key_unavailable", user_id=auth.user_id)
            return ""

    async def _user_role_ids(self, auth: AuthContext) -> tuple[str, ...]:
        """取用户当前角色 ID（供角色级授权命中）。

        Args:
            auth: 鉴权上下文。

        Returns:
            tuple[str, ...]: 角色 ID 元组；无角色或权限域不可用时为空。
        """
        try:
            from app.db.repositories.role_repo import get_role_store

            role_ids = await get_role_store(self._settings).role_ids_of_user(
                auth.tenant_id, auth.user_id
            )
        except Exception:  # pragma: no cover - 权限域不可用时退化为无角色
            logger.debug("policy_roles_unavailable", user_id=auth.user_id)
            return ()
        return tuple(role_ids)

    async def _resolve_points(self, auth: AuthContext, business_role: str = "") -> frozenset[str]:
        """解析用户拥有的权限点。

        **业务库是权威源（D-13），令牌角色只是兜底**——这一点决定了「减权」是否生效：

        | 业务库有该成员角色 | 权限点来源 | 撤权效果 |
        | --- | --- | --- |
        | 有 | 业务库 `role_permission` ∪ 该角色键的内置矩阵 | **立即收紧**（降级生效） |
        | 无 | 令牌角色矩阵（外部账号 / 未播种租户） | 只能靠令牌过期 |

        早期实现取「令牌 ∪ 业务库」并集，导致降级后令牌角色仍撑着旧权限
        （业务库只能加不能减），QA `test_b08_5b` 已按 D-13 判为缺陷。

        Args:
            auth: 鉴权上下文。
            business_role: 业务库中的角色标识（空串表示未登记）。

        Returns:
            frozenset[str]: 权限点集合（未知角色不贡献任何权限，fail-closed）。
        """
        if WILDCARD_ROLE in normalized_roles(auth):
            # 平台运维令牌：与令牌约定一致，不受租户内角色降级影响
            return permissions_for_roles(auth.roles)
        if business_role:
            points = set(BUILTIN_ROLE_PERMISSIONS.get(business_role, frozenset()))
            try:
                from app.db.repositories.role_repo import get_role_store

                points.update(
                    await get_role_store(self._settings).permissions_of_user(
                        auth.tenant_id, auth.user_id
                    )
                )
            except Exception:  # pragma: no cover - 权限域不可用时按角色键矩阵判定
                logger.debug("policy_role_store_unavailable", user_id=auth.user_id)
            return frozenset(points)
        return permissions_for_roles(auth.roles)

    def _resolve_admin(self, auth: AuthContext, business_role: str) -> bool:
        """判定是否组织管理员（业务库角色优先，令牌角色兜底）。

        Args:
            auth: 鉴权上下文。
            business_role: 业务库中的角色标识（空串表示未登记）。

        Returns:
            bool: 管理员返回 True。
        """
        if WILDCARD_ROLE in normalized_roles(auth):
            return True
        if business_role:
            return business_role in ORG_ADMIN_ROLES
        return has_admin_role(auth)

    # ------------------------------------------------------------------
    # 权限点判定（B07 起 BL5/B08 ID* 的统一出口）
    # ------------------------------------------------------------------
    async def admin_of(self, auth: AuthContext) -> bool:
        """判定是否组织管理员（业务库角色优先；管理视图等入口统一调用本方法）。

        Args:
            auth: 鉴权上下文。

        Returns:
            bool: 管理员返回 True。
        """
        return (await self.snapshot(auth)).is_admin

    async def has_point(self, auth: AuthContext, permission: str) -> bool:
        """判断是否拥有某个权限点（**fail-closed**：无角色即无权限）。

        Args:
            auth: 鉴权上下文。
            permission: 权限点，形如 `team:member:update`。

        Returns:
            bool: 拥有返回 True。
        """
        if not permission:
            return True
        snapshot = await self.snapshot(auth)
        return permission in snapshot.permission_points

    async def require_point(self, auth: AuthContext, permission: str) -> None:
        """校验权限点，不足则抛 `20003`（唯一判权出口）。

        Args:
            auth: 鉴权上下文。
            permission: 权限点。

        Raises:
            ApiError: 权限不足（20003）。
        """
        if await self.has_point(auth, permission):
            return
        from app.core.errors import ApiError, ErrorCode

        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=f"permission={permission} roles={auth.roles}",
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
        is_admin = snapshot.is_admin if snapshot is not None else has_admin_role(auth)
        if is_admin:
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

        **归一化必须先去重后判权（QA-P3-07）**：`["kb_x", "x"]` 是**同一个对象**的
        两种写法，若先按原始串去重再归一化，返回值会出现两个 `kb_x` →
        M1/M2 的 `knowledge_base_ids` 出现重复项 → 可能重复检索、引用条重复，
        **单次对话用量虚高**（与 D-12 的计量口径直接冲突）。
        因此：先 `normalize_base_id` 再 `dict.fromkeys` 去重（保持首次出现顺序），
        并在返回前断言结果内无重复。

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
        for base_id in dict.fromkeys(normalize_base_id(raw_id) for raw_id in base_ids):
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
        if len(allowed) != len(set(allowed)):  # pragma: no cover - 防御性自检
            # 理论上不可达（上面已按规范形态去重）；一旦触发说明归一化被改坏，
            # 会让单次对话重复检索并放大用量，属于必须立即暴露的缺陷。
            logger.error(
                "policy_duplicate_base_ids",
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                allowed=allowed,
            )
            allowed = list(dict.fromkeys(allowed))
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
    "BUILTIN_ROLE_KEYS",
    "BUILTIN_ROLE_PERMISSIONS",
    "BUSINESS_PERMISSIONS",
    "DEPARTMENT_ID_PREFIX",
    "GUEST_ROLES",
    "KNOWN_PERMISSION_POINTS",
    "OPS_PERMISSIONS",
    "ORG_ADMIN_ROLES",
    "ROLE_GUEST",
    "ROLE_MEMBER",
    "ROLE_OPS",
    "ROLE_ORG_ADMIN",
    "ROLE_OWNER",
    "VISIBILITY_ORGANIZATION",
    "WILDCARD_ROLE",
    "PolicyService",
    "PolicySnapshot",
    "get_policy_service",
    "has_admin_role",
    "is_guest_role",
    "normalize_base_id",
    "normalized_roles",
    "permissions_for_roles",
    "reset_policy_service",
]
