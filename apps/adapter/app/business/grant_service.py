"""知识库授权业务能力（阶段二 B05，架构文档 6.3 / PRD P2-KB-03）。

职责边界：

- 本层是「谁可以看 / 谁可以上传」的**唯一写入口**（KB5 列表、KB6 批量授权、KB7 撤销）；
- 判定本身在 `business/policy_service.py`（列表 / 检索 / 问答同一套）；
- `db/repositories/grant_repo.py` 只有 SQL 与内存存储；
- 撤销只写 `revoked_at`，保留操作痕迹（不物理删除）。

三条硬规则（本批最易做错的地方）：

1. **同一对象重复授权以最新为准（幂等）**：靠 `uq_grant_active` 唯一索引兜底，
   并发下也是「一个成功、另一个 skipped」，不做「先查后插」（PRD 验收 3）。
2. **`guest` 不可被授予 `editor`**：返回 `20001` 且文案为
   「访客只能查看，不能上传或管理文档」（PRD 验收 5）。
3. **授权成功后立即失效可见性缓存**：保证「下一请求即生效」，
   不必等 5s TTL（架构文档 6.3 的 `DEL oa:perm:v1:*` 语义）。
"""

from __future__ import annotations

from typing import Any, Final

from app.business.audit_service import (
    ACTION_GRANT_CREATE,
    ACTION_GRANT_REVOKE,
    RESOURCE_GRANT,
    AuditService,
    get_audit_recorder,
)
from app.business.policy_service import (
    PolicyService,
    get_policy_service,
    has_admin_role,
    is_guest_role,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, GrantStore, get_grant_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBasePatch,
    KnowledgeBaseRecord,
    KnowledgeBaseStore,
    get_knowledge_base_store,
)
from app.schemas.common import build_page
from app.schemas.knowledge_ext import (
    CreateGrantData,
    CreateGrantRequest,
    DeleteGrantData,
    GrantItem,
    GrantPermission,
    GrantSubjectType,
)

logger = get_logger(__name__)

#: 授权记录 ID 前缀（架构文档 11.1）
GRANT_ID_PREFIX: Final[str] = "grnt_"

#: 单次批量授权的对象上限（PRD P2-KB-03 请求约束）
MAX_SUBJECTS_PER_REQUEST: Final[int] = 50

#: 访客角色被授予 `editor` 时的业务文案（PRD 验收 5，原文不得改写）
GUEST_EDITOR_MESSAGE: Final[str] = "访客只能查看，不能上传或管理文档"

#: 授权对象为角色时的「访客」识别口径（身份底座接入前的简化判定）
_GUEST_ROLE_KEYS: Final[frozenset[str]] = frozenset({"guest", "role_guest", "rol_guest"})


def new_grant_id() -> str:
    """生成授权记录 ID。

    Returns:
        str: 形如 `grnt_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID。
    """
    return new_resource_id(GRANT_ID_PREFIX)


def _is_guest_subject(subject_type: str, subject_id: str) -> bool:
    """判断授权对象是否为访客角色。

    Args:
        subject_type: 授权对象类型。
        subject_id: 授权对象 ID。

    Returns:
        bool: 角色类型且指向访客角色时返回 True。
    """
    if subject_type != GrantSubjectType.ROLE.value:
        return False
    normalized = subject_id.strip().lower()
    return normalized in _GUEST_ROLE_KEYS or normalized.endswith("guest")


class GrantService:
    """知识库授权业务服务。

    Attributes:
        _settings: 配置实例。
        _store: 授权存储（内存 / PostgreSQL）。
        _bases: 知识库元数据存储。
        _audit: 操作记录服务。
        _policy: 可见性判定服务（授权变更后失效其缓存）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: GrantStore | None = None,
        bases: KnowledgeBaseStore | None = None,
        audit: AuditService | None = None,
        policy: PolicyService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选授权存储（测试注入）。
            bases: 可选知识库存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            policy: 可选可见性判定服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: GrantStore = store or get_grant_store(self._settings)
        self._bases: KnowledgeBaseStore = bases or get_knowledge_base_store(self._settings)
        self._audit: AuditService = audit or AuditService(
            self._settings, recorder=get_audit_recorder(self._settings)
        )
        self._policy: PolicyService = policy or get_policy_service(self._settings)

    @property
    def store(self) -> GrantStore:
        """授权存储（测试可据此准备数据）。"""
        return self._store

    # ------------------------------------------------------------------
    # KB5 授权清单
    # ------------------------------------------------------------------
    async def list_grants(self, base_id: str, page: int, page_size: int, auth: AuthContext) -> Any:
        """分页查询知识库的授权清单。

        Args:
            base_id: 知识库 ID。
            page: 页码。
            page_size: 每页条数。
            auth: 鉴权上下文。

        Returns:
            PageResult[GrantItem]: 授权清单分页结果。

        Raises:
            ApiError: 库不存在（11001）或无权管理授权（20003）。
        """
        await self._require_manageable(base_id, auth)
        records, total = await self._store.list(
            auth.tenant_id, base_id, limit=page_size, offset=(page - 1) * page_size
        )
        items = await self._to_items(records, auth)
        logger.info(
            "grant_list_loaded",
            base_id=base_id,
            page=page,
            total=total,
            tenant_id=auth.tenant_id,
        )
        return build_page(items=items, total=total, page=page, page_size=page_size)

    async def _to_items(self, records: list[GrantRecord], auth: AuthContext) -> list[GrantItem]:
        """把授权记录转换为对外条目（补齐对象展示名）。

        Args:
            records: 授权记录列表。
            auth: 鉴权上下文。

        Returns:
            list[GrantItem]: 对外条目列表。
        """
        names: dict[tuple[str, str], str] = {}
        grouped: dict[str, list[str]] = {}
        for record in records:
            grouped.setdefault(record.subject_type, []).append(record.subject_id)
        for subject_type, subject_ids in grouped.items():
            resolved = await self._store.resolve_subject_names(
                auth.tenant_id, subject_type, subject_ids
            )
            for subject_id, display_name in resolved.items():
                names[(subject_type, subject_id)] = display_name
        return [
            GrantItem(
                grant_id=record.grant_id,
                subject_type=_subject_type_of(record.subject_type),
                subject_id=record.subject_id,
                subject_name=names.get(
                    (record.subject_type, record.subject_id),
                    _fallback_subject_name(record.subject_type, record.subject_id),
                ),
                permission=_permission_of(record.permission),
                granted_by_name=record.granted_by,
                granted_at=record.granted_at,
            )
            for record in records
        ]

    # ------------------------------------------------------------------
    # KB6 批量授权
    # ------------------------------------------------------------------
    async def create_grants(
        self, base_id: str, request: CreateGrantRequest, auth: AuthContext
    ) -> CreateGrantData:
        """批量新增授权（重复对象计入 `skipped_count`，不报错）。

        Args:
            base_id: 知识库 ID。
            request: 批量授权请求。
            auth: 鉴权上下文。

        Returns:
            CreateGrantData: 新建与跳过条数。

        Raises:
            ApiError: 库不存在（11001）、无权管理（20003）、参数非法（20001）。
        """
        await self._require_manageable(base_id, auth)
        subject_ids = _normalize_subject_ids(request.subject_ids)
        if not subject_ids:
            raise ApiError(ErrorCode.PARAM_INVALID, internal_detail="grant subject_ids empty")
        if len(subject_ids) > MAX_SUBJECTS_PER_REQUEST:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"grant subject_ids too many: {len(subject_ids)}",
            )
        permission = str(request.permission.value)
        subject_type = str(request.subject_type.value)
        if permission == GrantPermission.EDITOR.value:
            for subject_id in subject_ids:
                if _is_guest_subject(subject_type, subject_id):
                    raise ApiError(ErrorCode.PARAM_INVALID, message=GUEST_EDITOR_MESSAGE)

        created = 0
        skipped = 0
        created_ids: list[str] = []
        for subject_id in subject_ids:
            record, is_created = await self._store.create(
                GrantRecord(
                    tenant_id=auth.tenant_id,
                    grant_id=new_grant_id(),
                    base_id=base_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    permission=permission,
                    granted_by=auth.user_id,
                )
            )
            if is_created:
                created += 1
                created_ids.append(record.grant_id)
            else:
                skipped += 1

        await self._ensure_restricted(base_id, auth)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_GRANT_CREATE,
            resource_type=RESOURCE_GRANT,
            resource_id=base_id,
            actor_role=_first_role(auth),
            change_summary={
                "subject_type": subject_type,
                "subject_count": len(subject_ids),
                "permission": permission,
                "created_count": created,
                "skipped_count": skipped,
            },
        )
        self._policy.invalidate(auth.tenant_id)
        logger.info(
            "grant_created",
            base_id=base_id,
            subject_type=subject_type,
            permission=permission,
            created_count=created,
            skipped_count=skipped,
            tenant_id=auth.tenant_id,
        )
        return CreateGrantData(created_count=created, skipped_count=skipped)

    async def _ensure_restricted(self, base_id: str, auth: AuthContext) -> None:
        """授权存在即视为「指定成员可见」：把库可见范围收敛为 `restricted`。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。
        """
        record = await self._bases.get(auth.tenant_id, base_id)
        if record is None or record.visibility == "restricted":
            return
        await self._bases.update(
            auth.tenant_id, base_id, KnowledgeBasePatch(visibility="restricted")
        )
        logger.info("knowledge_base_visibility_restricted", base_id=base_id)

    # ------------------------------------------------------------------
    # KB7 撤销授权
    # ------------------------------------------------------------------
    async def revoke_grant(self, base_id: str, grant_id: str, auth: AuthContext) -> DeleteGrantData:
        """撤销一条授权（撤销立即生效）。

        Args:
            base_id: 知识库 ID。
            grant_id: 授权记录 ID。
            auth: 鉴权上下文。

        Returns:
            DeleteGrantData: 撤销结果。

        Raises:
            ApiError: 库不存在（11001）、授权不存在（20004）、无权管理（20003）。
        """
        await self._require_manageable(base_id, auth)
        record = await self._store.get(auth.tenant_id, grant_id)
        if record is None or record.base_id != base_id:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                internal_detail=f"grant_id={grant_id} base_id={base_id}",
            )
        revoked = await self._store.revoke(auth.tenant_id, grant_id)
        if revoked is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"grant revoked grant_id={grant_id}"
            )
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_GRANT_REVOKE,
            resource_type=RESOURCE_GRANT,
            resource_id=base_id,
            actor_role=_first_role(auth),
            change_summary={
                "grant_id": grant_id,
                "subject_type": record.subject_type,
                "permission": record.permission,
            },
        )
        self._policy.invalidate(auth.tenant_id)
        logger.info(
            "grant_revoked",
            base_id=base_id,
            grant_id=grant_id,
            tenant_id=auth.tenant_id,
        )
        return DeleteGrantData(grant_id=grant_id, deleted=True)

    # ------------------------------------------------------------------
    # 对象级判权
    # ------------------------------------------------------------------
    async def _require_manageable(self, base_id: str, auth: AuthContext) -> KnowledgeBaseRecord:
        """校验「管理授权」的对象级权限（仅所有者与组织管理员）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            KnowledgeBaseRecord: 知识库记录。

        Raises:
            ApiError: 库不存在（11001）或无权管理（20003）。
        """
        record = await self._bases.get(auth.tenant_id, base_id)
        if record is None:
            raise ApiError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, internal_detail=f"base_id={base_id}")
        if is_guest_role(auth):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                internal_detail=f"guest cannot manage grants base_id={base_id}",
            )
        if record.owner_user_id and record.owner_user_id == auth.user_id:
            return record
        if has_admin_role(auth):
            return record
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=(
                f"user_id={auth.user_id} roles={auth.roles} cannot manage grants base_id={base_id}"
            ),
        )


def _normalize_subject_ids(subject_ids: list[str]) -> list[str]:
    """规范化并去重授权对象 ID（保持调用方顺序）。

    Args:
        subject_ids: 原始对象 ID 列表。

    Returns:
        list[str]: 去空白、去重后的列表。
    """
    return list(dict.fromkeys(item.strip() for item in subject_ids if item and item.strip()))


def _subject_type_of(value: str) -> GrantSubjectType:
    """把存储值转换为授权对象类型枚举。

    Args:
        value: 存储值。

    Returns:
        GrantSubjectType: 枚举值；未知值回落为 `user`。
    """
    try:
        return GrantSubjectType(str(value))
    except ValueError:
        return GrantSubjectType.USER


def _permission_of(value: str) -> GrantPermission:
    """把存储值转换为授权级别枚举。

    Args:
        value: 存储值。

    Returns:
        GrantPermission: 枚举值；未知值回落为 `viewer`。
    """
    try:
        return GrantPermission(str(value))
    except ValueError:
        return GrantPermission.VIEWER


def _fallback_subject_name(subject_type: str, subject_id: str) -> str:
    """授权对象展示名的兜底值（无法解析名称时使用）。

    Args:
        subject_type: 授权对象类型。
        subject_id: 授权对象 ID。

    Returns:
        str: 业务语言兜底名。
    """
    if subject_type == GrantSubjectType.DEPARTMENT.value:
        return "部门"
    if subject_type == GrantSubjectType.ROLE.value:
        return "角色"
    return "成员"


def _first_role(auth: AuthContext) -> str:
    """取操作人的首个角色（操作记录展示用）。

    Args:
        auth: 鉴权上下文。

    Returns:
        str: 首个角色；无角色返回空串。
    """
    return str(auth.roles[0]) if auth.roles else ""


__all__: list[str] = [
    "GRANT_ID_PREFIX",
    "GUEST_EDITOR_MESSAGE",
    "MAX_SUBJECTS_PER_REQUEST",
    "GrantService",
    "new_grant_id",
]
