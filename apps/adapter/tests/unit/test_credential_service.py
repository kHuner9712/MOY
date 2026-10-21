"""凭据解析测试（阶段二 B03，架构文档 2.3 结论第 2-5 条）。

**本文件的核心是「绝不回落」**：已绑定的知识库解析失败必须 11007，
且下游调用次数必须为 0——只断言「返回了 11007」是不够的，
因为实现可能在抛错前先偷偷用全局凭据打了一次下游（用户看到 11007，
但数据已经打到别人的库上了）。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import pytest

from app.business import credential_service
from app.business.credential_service import (
    CredentialResolver,
    InMemoryCredentialCache,
    ResolvedCredential,
    get_credential_resolver,
    ref_suffix,
    reset_credential_resolver,
)
from app.clients.dify_client import DifyResponse
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.credential_repo import (
    STATUS_REVOKED,
    CredentialRecord,
    InMemoryCredentialBindingStore,
    InMemoryCredentialStore,
    reset_credential_stores,
)
from app.schemas.rag import ChunkPolicy, SearchRequest
from app.services.rag_service import RagService

# P0 红线用例（G-24，测试方案 §7.1）：P2-KB-05 / P0 判据② —— 已绑定凭证回落全局
# 映射与实跑见 docs/qa-phase2-w2-r2-report.md §3.1
pytestmark = [pytest.mark.unit, pytest.mark.p0]

_TENANT = "tnt_cred_tenant"
_BASE_ID = "kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4"
_CRED_REF = "cred_01K4Q8Z2X7M9B3N5P6R8T0V2W5"
_OTHER_REF = "cred_01K4Q8Z2X7M9B3N5P6R8T0V2W6"
_BOUND_KEY = "app-bound-key-0001"
_OTHER_KEY = "app-bound-key-0002"
_GLOBAL_KEY = "app-global-key-0001"


def make_key() -> str:
    """生成合法 base64 密钥。"""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def make_settings(**overrides: Any) -> Settings:
    """构造测试配置（默认开启下游调用，便于统计调用次数）。"""
    payload: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "saas",
        "downstream_enabled": True,
        "dify_default_app_key": _GLOBAL_KEY,
        "credential_encryption_key": make_key(),
        "credential_mode": "bound",
        "credential_cache_ttl_seconds": 300,
        "ratelimit_enabled": False,
    }
    payload.update(overrides)
    return Settings(**payload)


class FakeDifyClient:
    """底座客户端替身（记录调用次数与下发的 api_key）。"""

    def __init__(self) -> None:
        """初始化。"""
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    @property
    def total_calls(self) -> int:
        """下游调用总数。"""
        return len(self.post_calls) + len(self.get_calls) + len(self.delete_calls)

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录 POST 调用。"""
        self.post_calls.append({"path": path, "api_key": api_key, "idempotent": idempotent})
        return DifyResponse(200, {"records": [], "id": "file-1", "batch": "b1"})

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录 GET 调用。"""
        self.get_calls.append({"path": path, "api_key": api_key, "params": params})
        return DifyResponse(200, {"indexing_status": "completed"})

    async def delete(self, path: str, *, api_key: str | None = None) -> DifyResponse:
        """记录 DELETE 调用。"""
        self.delete_calls.append({"path": path, "api_key": api_key})
        return DifyResponse(204, {})


class BrokenStore(InMemoryCredentialStore):
    """读取即抛错的凭据存储（模拟数据库不可用）。"""

    async def get(self, tenant_id: str, credential_ref: str) -> CredentialRecord | None:
        """抛错。"""
        raise RuntimeError("credential store unavailable")


def build_resolver(
    settings: Settings | None = None,
    *,
    store: InMemoryCredentialStore | None = None,
    cache: InMemoryCredentialCache | None = None,
    bindings: InMemoryCredentialBindingStore | None = None,
) -> CredentialResolver:
    """构造解析器（存储与绑定均用内存实现，便于断言）。

    Args:
        settings: 配置。
        store: 凭据存储。
        cache: 缓存实现。
        bindings: 绑定存储。

    Returns:
        CredentialResolver: 解析器。
    """
    return CredentialResolver(
        settings=settings or make_settings(),
        store=store or InMemoryCredentialStore(),
        binding_store=bindings or InMemoryCredentialBindingStore(),
        cache=cache or InMemoryCredentialCache(),
    )


async def setup_bound_credential(
    resolver: CredentialResolver,
    *,
    base_id: str = _BASE_ID,
    credential_ref: str = _CRED_REF,
    api_key: str = _BOUND_KEY,
    tenant_id: str = _TENANT,
) -> None:
    """写入凭据并完成绑定。

    Args:
        resolver: 解析器。
        base_id: 知识库 ID。
        credential_ref: 凭据引用。
        api_key: 明文密钥。
        tenant_id: 租户 ID。
    """
    await resolver.store_credential(credential_ref, api_key, tenant_id=tenant_id)
    await resolver.bind(base_id, credential_ref, tenant_id=tenant_id)


def build_service(
    resolver: CredentialResolver, settings: Settings | None = None
) -> tuple[RagService, FakeDifyClient]:
    """构造带替身客户端的 RAG 服务。

    Args:
        resolver: 凭据解析器。
        settings: 配置（必须与解析器一致，缺省用同一份测试配置）。

    Returns:
        tuple[RagService, FakeDifyClient]: 服务与替身客户端。
    """
    dify = FakeDifyClient()
    service = RagService(settings=settings or make_settings(), dify=dify, resolver=resolver)
    return service, dify


def auth_context(tenant_id: str = _TENANT) -> AuthContext:
    """构造鉴权上下文。

    Args:
        tenant_id: 租户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id="usr_cred_test", tenant_id=tenant_id)


async def seed_base(
    settings: Settings | None = None, *, base_id: str = _BASE_ID, tenant_id: str = _TENANT
) -> None:
    """登记知识库元数据（B05 起 R7 判权 fail-closed，检索前必须先有元数据）。

    Args:
        settings: 配置（与解析器保持一致，保证取到同一份内存单例）。
        base_id: 知识库 ID。
        tenant_id: 租户 ID。
    """
    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        get_knowledge_base_store,
    )

    store = get_knowledge_base_store(settings or make_settings())
    await store.create(
        KnowledgeBaseRecord(
            tenant_id=tenant_id,
            base_id=base_id,
            name="凭据联调库",
            visibility="organization",
            owner_user_id="usr_cred_test",
        )
    )


# ===========================================================================
# 1. 未绑定 → 全局凭据（现状行为不变）
# ===========================================================================
async def test_unbound_base_uses_global_credential() -> None:
    """未绑定凭据的知识库继续走全局凭据。"""
    resolver = build_resolver()
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.source == "global"
    assert resolved.api_key == _GLOBAL_KEY
    assert resolved.credential_ref is None


async def test_unbound_base_does_not_need_encryption_key() -> None:
    """未绑定库在密钥缺失时也能正常解析（不因运维漏配而全站不可用）。"""
    resolver = build_resolver(make_settings(credential_encryption_key=""))
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.source == "global"


# ===========================================================================
# 2. 已绑定 → 解密得到库级密钥
# ===========================================================================
async def test_bound_base_uses_decrypted_credential() -> None:
    """已绑定库返回解密后的密钥，来源标记为 bound。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.source == "bound"
    assert resolved.api_key == _BOUND_KEY
    assert resolved.credential_ref == _CRED_REF


async def test_bound_base_is_tenant_isolated() -> None:
    """其他租户看不到该绑定（同一 base_id 在别的租户仍未绑定）。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    other = await resolver.resolve(_BASE_ID, tenant_id="tnt_other")
    assert other.source == "global"


# ===========================================================================
# 3. 已绑定但解析失败：11007 且下游调用次数为 0（绝不回落）
# ===========================================================================
async def test_bound_failure_never_falls_back() -> None:
    """已绑定库解析失败 → 11007，且**下游调用次数必须为 0**。"""
    binding_store = InMemoryCredentialBindingStore()
    await binding_store.set_credential_ref(_TENANT, _BASE_ID, _CRED_REF)
    resolver = CredentialResolver(
        settings=make_settings(),
        store=BrokenStore(),
        binding_store=binding_store,
        cache=InMemoryCredentialCache(),
    )
    service, dify = build_service(resolver)
    await seed_base()

    with pytest.raises(ApiError) as excinfo:
        await service.search(SearchRequest(base_id=_BASE_ID, query="报销制度"), auth=auth_context())

    assert excinfo.value.code == ErrorCode.KB_CREDENTIAL_UNAVAILABLE
    assert excinfo.value.http_status == 200
    assert excinfo.value.message == "该知识库暂时无法访问，请联系管理员"
    assert dify.total_calls == 0, "解析失败后不得退回全局凭据调用下游"
    # 诊断信息只进日志，且不得包含任何密钥
    assert _GLOBAL_KEY not in (excinfo.value.internal_detail or "")
    assert _BOUND_KEY not in (excinfo.value.internal_detail or "")


async def test_credential_plaintext_absent_from_raised_detail() -> None:
    """异常详情里不允许出现明文密钥（只允许 ref 后缀）。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    # 破坏缓存中的密文，制造解密失败
    await resolver.invalidate(_TENANT, credential_ref=_CRED_REF)
    store = resolver._store  # noqa: SLF001 - 测试直接篡改存储
    original = await store.get(_TENANT, _CRED_REF)
    assert original is not None
    await store.put(
        CredentialRecord(
            tenant_id=_TENANT,
            credential_ref=_CRED_REF,
            provider="knowledge_base",
            cipher_text=b"broken-cipher-text",
            nonce=b"0" * 12,
            key_version=original.key_version,
        )
    )
    with pytest.raises(ApiError) as excinfo:
        await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    detail = excinfo.value.internal_detail or ""
    assert _BOUND_KEY not in detail
    assert ref_suffix(_CRED_REF) in detail


def test_global_key_is_referenced_only_before_bound_branch() -> None:
    """结构防线：`dify_default_app_key` 不得出现在「已绑定」分支之后。

    `resolve()` 的 except 分支一旦访问全局凭据，就存在回落可能；
    该断言用源码扫描固化评审检查点（比人工评审更可靠）。
    """
    source = Path(credential_service.__file__).read_text(encoding="utf-8")
    marker = "以下为「已绑定」分支"
    assert marker in source, "解析链路的分支注释丢失，请同步更新本断言"
    head, _, tail = source.partition(marker)
    assert "dify_default_app_key" in head
    assert "dify_default_app_key" not in tail, "已绑定分支内禁止访问全局凭据（回落风险）"


def test_resolved_credential_repr_redacts_api_key() -> None:
    """解析结果的 repr 不得包含明文（防异常栈/日志外泄）。"""
    resolved = ResolvedCredential(
        api_key="app-plain-secret", source="bound", credential_ref=_CRED_REF
    )
    assert "app-plain-secret" not in repr(resolved)
    assert ref_suffix(_CRED_REF) in repr(resolved)


# ===========================================================================
# 4. 缓存：只存密文、不做负缓存、换绑即失效
# ===========================================================================
async def test_cache_contains_ciphertext_only() -> None:
    """缓存里只能有密文三要素，绝不能出现明文。"""
    cache = InMemoryCredentialCache()
    resolver = build_resolver(cache=cache)
    await setup_bound_credential(resolver)
    await resolver.resolve(_BASE_ID, tenant_id=_TENANT)

    cached = await cache.get(f"{credential_service.CREDENTIAL_CACHE_PREFIX}:{_TENANT}:{_CRED_REF}")
    assert cached is not None
    assert set(cached) == {"c", "n", "v"}
    assert _BOUND_KEY not in str(cached)


async def test_no_negative_cache_after_failure() -> None:
    """解析失败不写缓存：运维修好后下一个请求即恢复。"""
    store = InMemoryCredentialStore()
    binding_store = InMemoryCredentialBindingStore()
    await binding_store.set_credential_ref(_TENANT, _BASE_ID, _CRED_REF)
    resolver = CredentialResolver(
        settings=make_settings(),
        store=store,
        binding_store=binding_store,
        cache=InMemoryCredentialCache(),
    )

    with pytest.raises(ApiError):
        await resolver.resolve(_BASE_ID, tenant_id=_TENANT)

    await setup_bound_credential(resolver)
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.api_key == _BOUND_KEY


async def test_rebind_takes_effect_on_next_request() -> None:
    """换绑后下一请求即生效（无需重启）：缓存被精确删除。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    assert (await resolver.resolve(_BASE_ID, tenant_id=_TENANT)).api_key == _BOUND_KEY

    await resolver.store_credential(_OTHER_REF, _OTHER_KEY, tenant_id=_TENANT)
    await resolver.bind(_BASE_ID, _OTHER_REF, tenant_id=_TENANT)
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.api_key == _OTHER_KEY
    assert resolved.credential_ref == _OTHER_REF


async def test_revoked_credential_raises_11007() -> None:
    """已吊销凭据 → 11007（不回落）。"""
    store = InMemoryCredentialStore()
    resolver = CredentialResolver(
        settings=make_settings(),
        store=store,
        binding_store=InMemoryCredentialBindingStore(),
        cache=InMemoryCredentialCache(),
    )
    await setup_bound_credential(resolver)
    await store.revoke(_TENANT, _CRED_REF)
    await resolver.invalidate(_TENANT, credential_ref=_CRED_REF)
    with pytest.raises(ApiError) as excinfo:
        await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert excinfo.value.code == ErrorCode.KB_CREDENTIAL_UNAVAILABLE


async def test_revoked_record_status_constant_matches_schema() -> None:
    """吊销状态字面量与数据库 CHECK 约束一致。"""
    assert STATUS_REVOKED == "revoked"


# ===========================================================================
# 5. 应急模式：credential_mode=global
# ===========================================================================
async def test_global_mode_forces_global_credential() -> None:
    """应急 `OA_CREDENTIAL_MODE=global` 时全部走全局凭据（并打 WARN）。"""
    resolver = build_resolver(make_settings(credential_mode="global"))
    await setup_bound_credential(resolver)
    resolved = await resolver.resolve(_BASE_ID, tenant_id=_TENANT)
    assert resolved.source == "global"
    assert resolved.api_key == _GLOBAL_KEY


# ===========================================================================
# 6. 下游拿到的是库级密钥（R3/R4/R7 全链路）
# ===========================================================================
async def test_search_passes_bound_key_to_downstream() -> None:
    """R7 检索把库级密钥透传给下游客户端。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    await seed_base()
    service, dify = build_service(resolver)
    await service.search(SearchRequest(base_id=_BASE_ID, query="制度"), auth=auth_context())
    assert dify.post_calls
    assert dify.post_calls[0]["api_key"] == _BOUND_KEY


async def test_unbound_search_uses_global_key() -> None:
    """未绑定库的检索仍使用全局密钥（行为与现状一致）。"""
    resolver = build_resolver()
    await seed_base()
    service, dify = build_service(resolver)
    await service.search(SearchRequest(base_id=_BASE_ID, query="制度"), auth=auth_context())
    assert dify.post_calls[0]["api_key"] == _GLOBAL_KEY


async def test_upload_and_index_status_pass_bound_key() -> None:
    """R3 上传与 R4 进度查询均使用库级密钥。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    service, dify = build_service(resolver)

    await service.upload_document(
        base_id=_BASE_ID,
        file_name="handbook.pdf",
        file_size=1024,
        chunk_policy=ChunkPolicy.DEFAULT,
        auth=auth_context(),
    )
    assert {call["api_key"] for call in dify.post_calls} == {_BOUND_KEY}

    await service.get_index_status(
        base_id=_BASE_ID, doc_id="doc_01K4Q8Z2X7M9B3N5P6R8T0V2W7", auth=auth_context()
    )
    assert dify.get_calls[0]["api_key"] == _BOUND_KEY


async def test_delete_document_uses_bound_key() -> None:
    """R6 删除文档使用库级密钥。"""
    resolver = build_resolver()
    await setup_bound_credential(resolver)
    service, dify = build_service(resolver)
    await service.delete_document(
        base_id=_BASE_ID, doc_id="doc_01K4Q8Z2X7M9B3N5P6R8T0V2W7", auth=auth_context()
    )
    assert dify.delete_calls[0]["api_key"] == _BOUND_KEY


# ===========================================================================
# 7. 单例与配置变化
# ===========================================================================
def test_singleton_reuses_resolver_for_same_config() -> None:
    """同一份配置复用同一解析器实例（避免每次请求重建密钥环）。"""
    reset_credential_resolver()
    reset_credential_stores()
    key = make_key()
    conf = make_settings(credential_encryption_key=key)
    assert get_credential_resolver(conf) is get_credential_resolver(conf)


def test_singleton_rebuilds_when_key_changes() -> None:
    """密钥变化时解析器单例必须重建，避免用旧密钥解新密文。"""
    reset_credential_resolver()
    reset_credential_stores()
    first = get_credential_resolver(make_settings(credential_encryption_key=make_key()))
    second = get_credential_resolver(make_settings(credential_encryption_key=make_key()))
    assert first is not second
