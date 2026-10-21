"""知识库元数据业务能力（阶段二 B04/B05，架构文档 5 类图 / PRD P2-KB-01）。

职责边界：

- 本层是**知识库库级设置的权威**与判权出口（改名 / 改可见范围 / 改内容切分精细度 / 删除）；
- `services/rag_service.py` 只做协议转换，调用本层落库；
- `db/repositories/knowledge_base_repo.py` 只有 SQL 与内存存储；
- 可见性判定统一交给 `business/policy_service.py`（列表 / 检索 / 问答同一套）。

三条业务规则（本批最容易做错的地方）：

1. **删除判权**（PRD v1.1 冻结口径）：`owner` / `org_admin` 可删任意库；
   `member` 仅当 `base.owner_user_id == auth.user_id`（自己创建的库）时可删；
   `guest` 一律 `20003`。删除为**软删除**：列表立即不可见、操作记录保留。
2. **`my_role` 不写死**：由 `owner_user_id`、授权表（`grant_permission`）与令牌角色推导——
   所有者 → `owner`，授权 `editor` / 组织管理员 → `editor`，其余 → `viewer`。
3. **改内容切分精细度只影响之后上传的文档**：已处理文档需在文档行点
   「按新设置重新处理」（R5），本层不改存量文档的 `chunk_policy`。
"""

from __future__ import annotations

from typing import Any, Final

from app.business.audit_service import (
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_KB_UPDATE,
    RESOURCE_KNOWLEDGE_BASE,
    AuditService,
    get_audit_recorder,
)
from app.business.policy_service import (
    GUEST_ROLES,
    ORG_ADMIN_ROLES,
    WILDCARD_ROLE,
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
from app.db.repositories.document_repo import (
    DocumentStoreProtocol,
    get_document_store,
)
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBasePatch,
    KnowledgeBaseRecord,
    KnowledgeBaseStore,
    KnowledgeBaseViewer,
    get_knowledge_base_store,
)
from app.schemas.common import PageResult, build_page
from app.schemas.knowledge_ext import DeleteBaseData, UpdateBaseData, UpdateBaseRequest
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseData,
    CreateBaseRequest,
    KnowledgeBaseItem,
    KnowledgeBaseRole,
    ListBasesQuery,
    Visibility,
)

logger = get_logger(__name__)

#: 知识库 ID 前缀（与 `services/rag_service.py` 的 `new_base_id()` 保持一致）
BASE_ID_PREFIX: Final[str] = "kb_"

#: 名称最大长度（与数据库 CHECK 一致）
NAME_MAX_LENGTH: Final[int] = 128

#: 描述最大长度（与数据库 CHECK 一致）
DESCRIPTION_MAX_LENGTH: Final[int] = 1024


def new_base_id_value() -> str:
    """生成知识库 ID。

    Returns:
        str: 形如 `kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID。
    """
    return new_resource_id(BASE_ID_PREFIX)


def is_org_admin(auth: AuthContext) -> bool:
    """判断是否为组织管理员（规则唯一实现在 `PolicyService`）。

    Args:
        auth: 鉴权上下文。

    Returns:
        bool: 令牌角色命中管理员集合（或通配）时返回 True。
    """
    return has_admin_role(auth)


def is_guest(auth: AuthContext) -> bool:
    """判断是否为访客（只读角色）。

    Args:
        auth: 鉴权上下文。

    Returns:
        bool: 命中访客角色且非管理员时返回 True。
    """
    return is_guest_role(auth)


class KnowledgeBaseService:
    """知识库元数据业务服务。

    Attributes:
        _settings: 配置实例。
        _store: 知识库元数据存储（内存 / PostgreSQL）。
        _documents: 文档元数据存储（删除时级联统计）。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: KnowledgeBaseStore | None = None,
        documents: DocumentStoreProtocol | None = None,
        audit: AuditService | None = None,
        policy: PolicyService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选知识库存储（测试注入）。
            documents: 可选文档存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            policy: 可选可见性判定服务（测试注入；缺省取进程单例）。
        """
        self._settings = settings or get_settings()
        self._store: KnowledgeBaseStore = store or get_knowledge_base_store(self._settings)
        self._documents: DocumentStoreProtocol = documents or get_document_store(self._settings)
        self._audit: AuditService = audit or AuditService(
            self._settings, recorder=get_audit_recorder(self._settings)
        )
        self._policy: PolicyService = policy or get_policy_service(self._settings)

    @property
    def policy(self) -> PolicyService:
        """可见性判定服务（授权变更后由调用方失效其缓存）。"""
        return self._policy

    @property
    def store(self) -> KnowledgeBaseStore:
        """知识库元数据存储（测试可据此准备数据）。"""
        return self._store

    @property
    def documents(self) -> DocumentStoreProtocol:
        """文档元数据存储。"""
        return self._documents

    # ------------------------------------------------------------------
    # 判权（owner / org_admin / member / guest 四档）
    # ------------------------------------------------------------------
    def resolve_role(
        self, record: KnowledgeBaseRecord, auth: AuthContext, *, grant_permission: str | None = None
    ) -> KnowledgeBaseRole:
        """推导当前用户在该知识库上的角色（`my_role`）。

        判定顺序（**未写死为常量**：全部来自数据 + 令牌声明）：

        1. `record.owner_user_id == auth.user_id` → `owner`；
        2. 授权表命中 `editor`（B05 起由 `grant_permission` 传入）→ `editor`；
        3. 组织管理员（令牌角色）→ `editor`；
        4. 其余 → `viewer`。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。
            grant_permission: 该用户在该库上的授权级别（`viewer` / `editor`）。

        Returns:
            KnowledgeBaseRole: 角色枚举。
        """
        if record.owner_user_id and auth.user_id and record.owner_user_id == auth.user_id:
            return KnowledgeBaseRole.OWNER
        if grant_permission == "editor":
            return KnowledgeBaseRole.EDITOR
        if is_org_admin(auth):
            return KnowledgeBaseRole.EDITOR
        return KnowledgeBaseRole.VIEWER

    def is_visible(self, record: KnowledgeBaseRecord, auth: AuthContext) -> bool:
        """判断该知识库对当前用户是否可见（同步版，仅静态规则）。

        需要授权表判定时请用 `require_readable()`（异步，带 5s 缓存快照）。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。

        Returns:
            bool: 可见返回 True。
        """
        return self._policy.is_visible(record, auth)

    def _viewer_of(
        self, auth: AuthContext, allowed_base_ids: tuple[str, ...] = ()
    ) -> KnowledgeBaseViewer:
        """构造观察者上下文。

        Args:
            auth: 鉴权上下文。
            allowed_base_ids: 授权可见的知识库 ID（由 `PolicyService` 计算）。

        Returns:
            KnowledgeBaseViewer: 观察者上下文。
        """
        return KnowledgeBaseViewer(
            user_id=auth.user_id,
            is_admin=is_org_admin(auth),
            allowed_base_ids=tuple(sorted(allowed_base_ids)),
        )

    def assert_can_update(self, record: KnowledgeBaseRecord, auth: AuthContext) -> None:
        """校验「修改知识库设置」对象级权限。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。

        Raises:
            ApiError: 访客或既非所有者也非组织管理员（20003）。
        """
        if is_guest(auth):
            raise ApiError(
                ErrorCode.FORBIDDEN, internal_detail=f"guest cannot update base_id={record.base_id}"
            )
        if record.owner_user_id and record.owner_user_id == auth.user_id:
            return
        if is_org_admin(auth):
            return
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=(
                f"user_id={auth.user_id} roles={auth.roles} cannot update base_id={record.base_id}"
            ),
        )

    def assert_can_delete(self, record: KnowledgeBaseRecord, auth: AuthContext) -> None:
        """校验「删除知识库」对象级权限（PRD 验收 5 的四条边界）。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。

        Raises:
            ApiError: 访客（20003）；成员删除他人创建的库（20003）。
        """
        if is_guest(auth):
            raise ApiError(
                ErrorCode.FORBIDDEN, internal_detail=f"guest cannot delete base_id={record.base_id}"
            )
        if record.owner_user_id and record.owner_user_id == auth.user_id:
            return
        if is_org_admin(auth):
            return
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=(
                f"user_id={auth.user_id} is not owner of base_id={record.base_id} "
                f"(owner={record.owner_user_id})"
            ),
        )

    # ------------------------------------------------------------------
    # R1 创建（供 RagService 复用）
    # ------------------------------------------------------------------
    async def create(
        self, request: CreateBaseRequest, auth: AuthContext, *, base_id: str | None = None
    ) -> CreateBaseData:
        """创建知识库并落库。

        Args:
            request: 创建请求。
            auth: 鉴权上下文。
            base_id: 已由协议转换层生成的知识库 ID；缺省时本层生成。

        Returns:
            CreateBaseData: 创建结果（含库级内容切分精细度）。

        Raises:
            ApiError: 参数非法（20001）。
        """
        name = request.name.strip()
        if not name:
            raise ApiError(ErrorCode.PARAM_INVALID, internal_detail="knowledge base name is empty")
        if len(name) > NAME_MAX_LENGTH:
            raise ApiError(ErrorCode.PARAM_INVALID, internal_detail=f"name too long: {len(name)}")
        if len(request.description) > DESCRIPTION_MAX_LENGTH:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"description too long: {len(request.description)}",
            )

        identifier = base_id or new_base_id_value()
        record = KnowledgeBaseRecord(
            tenant_id=auth.tenant_id,
            base_id=identifier,
            name=name,
            description=request.description,
            visibility=str(request.visibility.value),
            chunk_policy=str(request.chunk_policy.value),
            owner_user_id=auth.user_id,
            created_by=auth.user_id,
        )
        stored = await self._store.create(record)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_KB_CREATE,
            resource_type=RESOURCE_KNOWLEDGE_BASE,
            resource_id=identifier,
            actor_role=_first_role(auth),
            change_summary={"name": name, "visibility": stored.visibility},
        )
        logger.info(
            "knowledge_base_created",
            base_id=identifier,
            visibility=stored.visibility,
            chunk_policy=stored.chunk_policy,
            tenant_id=auth.tenant_id,
        )
        return CreateBaseData(
            base_id=identifier,
            name=stored.name,
            chunk_policy=ChunkPolicy(stored.chunk_policy),
            created_at=stored.created_at,
        )

    # ------------------------------------------------------------------
    # R2 列表
    # ------------------------------------------------------------------
    async def list(self, query: ListBasesQuery, auth: AuthContext) -> PageResult[KnowledgeBaseItem]:
        """查询知识库列表（分页 + 关键字 + 可见范围过滤）。

        `restricted` 库通过授权表判定可见：先取 5s 快照（含授权可见库集合），
        再把该集合交给存储层做 SQL 过滤——保证 `total` 与分页一致（无 N+1）。

        Args:
            query: 分页与关键字参数。
            auth: 鉴权上下文。

        Returns:
            PageResult[KnowledgeBaseItem]: 分页结果。
        """
        snapshot = await self._policy.snapshot(auth)
        records, total = await self._store.list(
            auth.tenant_id,
            keyword=query.keyword,
            viewer=self._viewer_of(auth, tuple(snapshot.granted_base_ids)),
            limit=query.page_size,
            offset=query.offset,
        )
        items = [
            await self._to_item(record, auth, snapshot.permission_of(record.base_id))
            for record in records
        ]
        logger.info(
            "knowledge_base_list_loaded",
            keyword=query.keyword,
            page=query.page,
            total=total,
            tenant_id=auth.tenant_id,
        )
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    async def _to_item(
        self, record: KnowledgeBaseRecord, auth: AuthContext, grant_permission: str | None = None
    ) -> KnowledgeBaseItem:
        """把记录转换为对外条目。

        Args:
            record: 知识库记录。
            auth: 鉴权上下文。
            grant_permission: 该用户在该库上的授权级别（`viewer` / `editor`）。

        Returns:
            KnowledgeBaseItem: 对外条目。
        """
        # 文档数由存储层带出（postgres 为 JOIN 子查询，内存为逐条计数），
        # 上层不再逐条查询，避免 N+1 短事务。
        return KnowledgeBaseItem(
            base_id=record.base_id,
            name=record.name,
            description=record.description,
            visibility=_visibility_of(record.visibility),
            chunk_policy=_chunk_policy_of(record.chunk_policy),
            my_role=self.resolve_role(record, auth, grant_permission=grant_permission),
            document_count=record.document_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    # ------------------------------------------------------------------
    # KB1 更新知识库设置
    # ------------------------------------------------------------------
    async def update(
        self, base_id: str, request: UpdateBaseRequest, auth: AuthContext
    ) -> UpdateBaseData:
        """更新知识库设置（名称 / 描述 / 内容切分精细度 / 可见范围）。

        Args:
            base_id: 知识库 ID。
            request: 更新请求（至少一项）。
            auth: 鉴权上下文。

        Returns:
            UpdateBaseData: 更新后的库级设置。

        Raises:
            ApiError: 库不存在（11001）、无权限（20003）、参数非法（20001）。
        """
        record = await self._require_base(base_id, auth)
        self.assert_can_update(record, auth)

        if not request.has_change():
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="update request has no change field"
            )

        name = request.name
        if name is not None:
            name = name.strip()
            if not name:
                raise ApiError(
                    ErrorCode.PARAM_INVALID, internal_detail="knowledge base name is empty"
                )
            if len(name) > NAME_MAX_LENGTH:
                raise ApiError(
                    ErrorCode.PARAM_INVALID, internal_detail=f"name too long: {len(name)}"
                )
        description = request.description
        if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"description too long: {len(description)}",
            )

        patch = KnowledgeBasePatch(
            name=name,
            description=description,
            chunk_policy=request.chunk_policy.value if request.chunk_policy is not None else None,
            visibility=request.visibility.value if request.visibility is not None else None,
        )
        updated = await self._store.update(auth.tenant_id, base_id, patch)
        if updated is None:
            raise ApiError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, internal_detail=f"base_id={base_id}")

        changes: dict[str, Any] = {}
        if patch.name is not None:
            changes["name"] = patch.name
        if patch.description is not None:
            changes["description"] = patch.description
        if patch.chunk_policy is not None:
            changes["chunk_policy"] = patch.chunk_policy
        if patch.visibility is not None:
            changes["visibility"] = patch.visibility

        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_KB_UPDATE,
            resource_type=RESOURCE_KNOWLEDGE_BASE,
            resource_id=base_id,
            actor_role=_first_role(auth),
            change_summary=changes,
        )
        if patch.chunk_policy is not None:
            logger.info(
                "knowledge_base_policy_changed",
                base_id=base_id,
                chunk_policy=patch.chunk_policy,
                note="仅对之后上传的文档生效，已处理文档需在文档行重新处理",
            )
        logger.info(
            "knowledge_base_updated",
            base_id=base_id,
            fields=sorted(changes),
            tenant_id=auth.tenant_id,
        )
        return UpdateBaseData(
            base_id=updated.base_id,
            name=updated.name,
            description=updated.description,
            chunk_policy=_chunk_policy_of(updated.chunk_policy),
            visibility=_visibility_of(updated.visibility),
            updated_at=updated.updated_at,
        )

    # ------------------------------------------------------------------
    # KB2 删除知识库（软删除）
    # ------------------------------------------------------------------
    async def soft_delete(self, base_id: str, auth: AuthContext) -> DeleteBaseData:
        """软删除知识库及其下文档。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            DeleteBaseData: 删除结果（含一并移除的文档数）。

        Raises:
            ApiError: 库不存在（11001）、无权限（20003）、并发重复删除（20005）。
        """
        record = await self._require_base(base_id, auth)
        self.assert_can_delete(record, auth)

        deleted_document_count = await self._documents.soft_delete_by_base(auth.tenant_id, base_id)
        removed = await self._store.soft_delete(auth.tenant_id, base_id)
        if not removed:
            raise ApiError(
                ErrorCode.CONFLICT,
                internal_detail=f"base already deleted base_id={base_id}",
            )

        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_KB_DELETE,
            resource_type=RESOURCE_KNOWLEDGE_BASE,
            resource_id=base_id,
            actor_role=_first_role(auth),
            change_summary={
                "name": record.name,
                "deleted_document_count": deleted_document_count,
            },
        )
        logger.info(
            "knowledge_base_deleted",
            base_id=base_id,
            deleted_document_count=deleted_document_count,
            tenant_id=auth.tenant_id,
        )
        return DeleteBaseData(
            base_id=base_id,
            deleted=True,
            deleted_document_count=deleted_document_count,
        )

    # ------------------------------------------------------------------
    # 读取（KB3 文档清单 / R3 上传前置校验共用）
    # ------------------------------------------------------------------
    async def require_readable(self, base_id: str, auth: AuthContext) -> KnowledgeBaseRecord:
        """读取可见的知识库（不存在 11001，不可见 20003）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            KnowledgeBaseRecord: 知识库记录。

        Raises:
            ApiError: 库不存在（11001）或不可见（20003）。
        """
        record = await self._store.get(auth.tenant_id, base_id)
        if record is None:
            raise ApiError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, internal_detail=f"base_id={base_id}")
        if not await self._policy.can_read(auth, record):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                internal_detail=f"user_id={auth.user_id} cannot read base_id={base_id}",
            )
        return record

    async def _require_base(self, base_id: str, auth: AuthContext) -> KnowledgeBaseRecord:
        """读取知识库（不存在即 11001，不判可见性）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            KnowledgeBaseRecord: 知识库记录。

        Raises:
            ApiError: 库不存在（11001）。
        """
        record = await self._store.get(auth.tenant_id, base_id)
        if record is None:
            raise ApiError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, internal_detail=f"base_id={base_id}")
        return record

    async def base_exists_for_delete_check(
        self, base_id: str, auth: AuthContext
    ) -> KnowledgeBaseRecord | None:
        """读取**含已删除**的知识库记录（用于「库已删除」的精确报错）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            KnowledgeBaseRecord | None: 记录（含已删除）；不存在返回 None。
        """
        return await self._store.get(auth.tenant_id, base_id, include_deleted=True)


def _first_role(auth: AuthContext) -> str:
    """取操作人的首个角色（操作记录展示用）。

    Args:
        auth: 鉴权上下文。

    Returns:
        str: 首个角色；无角色返回空串。
    """
    return str(auth.roles[0]) if auth.roles else ""


def _visibility_of(value: str) -> Visibility:
    """把存储值转换为可见范围枚举（未知值回落为 `organization`）。

    Args:
        value: 存储值。

    Returns:
        Visibility: 枚举值。
    """
    try:
        return Visibility(str(value))
    except ValueError:
        return Visibility.ORGANIZATION


def _chunk_policy_of(value: str) -> ChunkPolicy:
    """把存储值转换为内容切分精细度枚举（未知值回落为 `default`）。

    Args:
        value: 存储值。

    Returns:
        ChunkPolicy: 枚举值。
    """
    try:
        return ChunkPolicy(str(value))
    except ValueError:
        return ChunkPolicy.DEFAULT


__all__: list[str] = [
    "BASE_ID_PREFIX",
    "DESCRIPTION_MAX_LENGTH",
    "GUEST_ROLES",
    "NAME_MAX_LENGTH",
    "ORG_ADMIN_ROLES",
    "WILDCARD_ROLE",
    "KnowledgeBaseService",
    "is_guest",
    "is_org_admin",
    "new_base_id_value",
]
