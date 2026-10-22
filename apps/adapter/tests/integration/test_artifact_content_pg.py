"""产物二进制内容在 **PG 后端** 的真实落库与下载（缺陷 P3-B22-03 的回归用例）。

**这个文件存在的唯一理由**：`plugin_artifact` 曾经只有 `inline_text`（文本预览），
文件类产物（docx / xlsx / zip）的真实字节只活在内存后端的 `ArtifactRecord.content` 里。
于是下载端点 `api/v1/artifact_files.py` 在 PG（私有化）模式下恒走回落分支：

```python
content = record.content          # 恒为 b""
if not content:
    content = record.inline_text.encode("utf-8")   # ← 拿到的是文本预览字节
```

后果是"导出 Word 公文"给出的是一个**能被 Word 打开才怪**的文本文件，而所有单测
（全是内存后端）与验收报告里的"真 docx（36636B、可解压 word/document.xml）"都成立
——**典型的「内存全绿、生产全废」**。

所以本文件的判据必须**只对 PG 后端成立**才算数，且必须走到"下载端点"这一层：

1. 渲染真 docx → `put` 入库 → `get` 回来 → 断言 `PK` magic / 字节数 / 可解压；
2. 用真的下载端点（ASGI + HMAC 令牌）再打一次 → 断言响应体是 zip、头里带
   RFC 5987 的 `filename*`；
3. `content_sha256` 与 `content` 的重算值一致（摘要没被"存了内容"这件事带偏）。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.artifact_token import make_token
from app.core.config import Settings, reset_settings_cache
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    InMemoryArtifactStore,
    PostgresArtifactStore,
    now_iso,
    reset_artifact_store,
)
from app.db.repositories.task_repo import PostgresTaskStore, TaskRecord
from app.db.session import platform_scope
from app.main import create_app
from app.plugins.renderer import DOCX_DOCUMENT_PART, FileRenderer

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 本次运行专用租户（用例结束清理）
TENANT_ID = f"tnt_pg_artcontent_{uuid.uuid4().hex[:8]}"

#: 另一个租户（证明内容也受租户隔离）
OTHER_TENANT = f"tnt_pg_artcontent_other_{uuid.uuid4().hex[:8]}"

#: 真 docx 的字节下限（python-docx 出的公文壳 + 正文稳定在 30KB 以上；
#: 设 10000 是为了"绝不会因为正文短而误判"，同时能抓住"把 100 字节文本当文件"）
MIN_DOCX_BYTES = 10000

#: 公文正文（四段式，贴近 HR 行政公文；只为让 docx 有实际段落）
DOC_PAYLOAD: dict[str, Any] = {
    "title": "关于做好二〇二六年设备采购工作的通知",
    "content": "为规范公司设备采购流程，经研究决定，现将有关事项通知如下。",
    "sections": [
        {
            "heading": "一、适用范围",
            "body": "本通知适用于公司各部门及所属分支机构的办公设备、生产设备与信息化设备采购。",
        },
        {
            "heading": "二、申报流程",
            "body": "各部门应于每季度首月十日前提交采购申请，经部门负责人审核后报行政部汇总，"
            "由行政部统一组织询价比价，形成比价记录留档备查。",
        },
        {
            "heading": "三、审批权限",
            "body": "单笔金额在人民币五万元以下的，由行政部负责人审批；五万元以上至二十万元的，"
            "由分管副总审批；二十万元以上的，报总经理办公会审议后执行。",
        },
        {
            "heading": "四、执行与验收",
            "body": "采购完成后应由申请部门与行政部共同验收，验收合格后办理入库与报销手续，"
            "验收记录归档保存三年，作为后续审计依据。",
        },
    ],
}


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎（pytest-asyncio 每个用例一个新事件循环）。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()
    reset_artifact_store()
    reset_settings_cache()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PostgreSQL 后端的测试配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-artifact-content-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresArtifactStore]:
    """PG 产物存储（用例结束清理本次租户的行）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresArtifactStore]: 产物存储。
    """
    instance = PostgresArtifactStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    """清理本次运行写入的行（平台作用域绕 RLS，只删自己的租户）。

    Args:
        settings: 配置实例。
    """
    async with platform_scope(settings) as uow:
        for table in ("plugin_artifact", "plugin_task_step", "plugin_task"):
            for tenant in (TENANT_ID, OTHER_TENANT):
                await uow.execute(
                    f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                    {"tenant_id": tenant},
                )


async def _seed_task(settings: Settings, *, tenant_id: str = TENANT_ID) -> str:
    """先落一个真实任务（`plugin_artifact.task_id` 有外键）。

    Args:
        settings: 配置实例。
        tenant_id: 企业 ID。

    Returns:
        str: 任务 ID。
    """
    task_store = PostgresTaskStore(settings)
    record = TaskRecord(
        tenant_id=tenant_id,
        task_id=f"task_{uuid.uuid4().hex[:16]}",
        plugin_id="plg_hr_admin",
        plugin_version="1.0.0",
        initiator_id="usr_artcontent",
        status="running",
        started_at=now_iso(),
        idem_key=f"idem-{uuid.uuid4().hex[:12]}",
    )
    await task_store.create_task(record)
    return record.task_id


def _docx_blob(name: str = "行政公文") -> Any:
    """渲染一份真 docx（`ArtifactBlob`）。

    **必须走生产渲染器**：测试自己 `zipfile` 现造一个 zip 只能证明"ZG 列能存字节"，
    证明不了"用户导出的公文能打开"。

    Args:
        name: 产物名称（用作文件名主体）。

    Returns:
        Any: `ArtifactBlob`（含 `content` / `content_type` / `filename`）。
    """
    return FileRenderer().render(render_format="docx", name=name, payload=DOC_PAYLOAD)


def _record(task_id: str, *, blob: Any = None, **overrides: Any) -> ArtifactRecord:
    """构造产物行（默认是一份 `file` 类的 docx）。

    摘要与字节都取自**同一个** blob：`python-docx` 会把创建时间写进 docx，
    两次渲染的字节必然不同，若摘要和字节来自两次渲染，用例会以"摘要不一致"的
    假象红掉——那不是被测代码的问题。

    Args:
        task_id: 所属任务。
        blob: 已渲染的 `ArtifactBlob`；缺省现场渲染一份。
        **overrides: 其它字段覆盖。

    Returns:
        ArtifactRecord: 产物行。
    """
    rendered = blob if blob is not None else _docx_blob()
    payload: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "artifact_id": f"art_{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "step_id": f"step_{uuid.uuid4().hex[:8]}",
        "output_key": "document",
        "type": "file",
        "name": "关于做好二〇二六年设备采购工作的通知",
        "inline_text": "关于做好二〇二六年设备采购工作的通知（正文预览）",
        "preview_text": "关于做好二〇二六年设备采购工作的通知",
        "storage_key": f"{task_id}/6/{rendered.filename}",
        "size_bytes": len(rendered.content),
        # 摘要按**真实字节**算（与 `task_service` 的 `sanitizer.digest(content)` 同源口径）
        "content_sha256": _digest(rendered.content),
        "content": rendered.content,
        "content_type": rendered.content_type,
    }
    payload.update(overrides)
    return ArtifactRecord(**payload)


def _digest(data: bytes) -> str:
    """内容摘要（SHA-256 hex）。

    Args:
        data: 字节。

    Returns:
        str: 64 位小写 hex。
    """
    return hashlib.sha256(data).hexdigest()


# ===========================================================================
# 1. 核心判据：PG 后端能拿回**真 zip**
# ===========================================================================
async def test_pg_backend_roundtrips_real_docx_bytes(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """渲染 docx → `put` 入库 → `get` 回来 → 仍是可解压的真 docx。

    这条用例在**修复前必然失败**（`get()` 返回 `content == b""`），
    这就是它存在的意义：它钉的是"字节有没有落库"，不是"链路有没有报错"。
    """
    blob = _docx_blob()
    task_id = await _seed_task(pg_settings)
    record = _record(task_id, blob=blob)
    await store.add_artifacts([record])

    fetched = await store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    content = fetched.content
    assert content, "PG 后端读回的 content 为空——字节没有落库（P3-B22-03 复发）"
    assert content[:2] == b"PK", f"不是 zip magic，前两字节={content[:2]!r}"
    assert len(content) > MIN_DOCX_BYTES, f"只有 {len(content)} 字节，像是文本预览而不是 docx"
    assert content == blob.content, "读回的字节与写进去的不一致（落库过程被截断/改写）"

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        assert DOCX_DOCUMENT_PART in names, f"docx 缺少正文部件，namelist={names}"
        assert len(archive.read(DOCX_DOCUMENT_PART)) > 0, "正文部件为空"

    # 摘要与字节必须自洽：`content_sha256` 是"内容有没有变"的唯一依据
    assert fetched.content_sha256 == _digest(content)


async def test_pg_backend_content_is_tenant_isolated(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """另一个租户读不到这份产物（内容同样受 RLS 隔离，不是只隔离元数据）。"""
    task_id = await _seed_task(pg_settings)
    record = _record(task_id)
    await store.add_artifacts([record])

    assert await store.get(OTHER_TENANT, record.artifact_id) is None


async def test_pg_backend_save_version_persists_new_bytes(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """WF2 改版：新字节入库，摘要按新内容重算，旧版本摘要进历史。

    「保存成功但下载还是旧内容」是最难被用户描述清楚的一类 bug，
    所以这里断言的是 `get()` 拿到的字节**就是新版字节**。
    """
    task_id = await _seed_task(pg_settings)
    record = _record(task_id)
    await store.add_artifacts([record])

    old_blob = _docx_blob("旧版公文")
    new_blob = FileRenderer().render(
        render_format="docx",
        name="新版公文",
        payload={**DOC_PAYLOAD, "content": "本次修订调整了审批权限与验收要求，其余内容不变。"},
    )
    assert new_blob.content != old_blob.content, "两份 docx 字节相同，用例前提不成立"

    updated = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="本次修订调整了审批权限与验收要求，其余内容不变。",
        preview_text="本次修订",
        content_sha256=_digest(new_blob.content),
        content=new_blob.content,
        content_type=new_blob.content_type,
    )
    assert updated is not None
    assert updated.version == 2

    fetched = await store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    assert fetched.content == new_blob.content, "改版后读回的仍是旧字节"
    assert fetched.content[:2] == b"PK"
    assert fetched.content_sha256 == _digest(fetched.content), "摘要必须与新内容一致"
    assert fetched.size_bytes == len(new_blob.content)
    # 历史里的旧版本摘要仍是**旧内容**的摘要（版本只增不删，旧摘要不许被覆盖）
    assert fetched.version_history[-1]["content_sha256"] == record.content_sha256


async def test_pg_backend_save_version_without_bytes_mirrors_memory_semantics(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """不传字节时按内存后端同语义回落为 `inline_text` 的 UTF-8 字节。

    两套实现的语义漂移是最难查的一类缺陷：内存后端是
    `content or inline_text.encode("utf-8")`，PG 后端必须一致。
    """
    task_id = await _seed_task(pg_settings)
    record = _record(task_id, type="text", content=b"", size_bytes=0)
    await store.add_artifacts([record])

    text = "改过的正文"
    updated = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text=text,
        preview_text=text,
        content_sha256=_digest(text.encode("utf-8")),
    )
    assert updated is not None
    fetched = await store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    assert fetched.content == text.encode("utf-8")
    assert fetched.size_bytes == len(text.encode("utf-8"))


async def test_pg_backend_text_artifact_stores_no_binary(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """纯文本产物不写二进制内容（`content` 落 NULL，读回 `b""`）。

    这一条防的是"为了修下载而把所有产物都塞字节"：`text` 类产物本来就该走
    `inline_text`，落一份重复字节只是把库撑大。
    """
    task_id = await _seed_task(pg_settings)
    record = _record(
        task_id,
        type="text",
        content=b"",
        content_sha256=_digest("方案正文".encode()),
        inline_text="方案正文",
        size_bytes=len("方案正文".encode()),
    )
    await store.add_artifacts([record])

    fetched = await store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    assert fetched.content == b""
    assert fetched.inline_text == "方案正文"


async def test_pg_backend_digest_is_not_cached_across_saves(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """每次保存都写**当次传入**的摘要（硬约束第 2 条：不许沿用旧摘要）。

    摘要若被缓存，前端会拿到旧文件——"保存成功但下载还是旧内容"。
    """
    task_id = await _seed_task(pg_settings)
    record = _record(task_id)
    await store.add_artifacts([record])

    digests: list[str] = []
    for index in range(2):
        payload = b"PK\x03\x04" + f"第 {index} 版内容".encode() * 64
        digest = _digest(payload)
        digests.append(digest)
        updated = await store.save_version(
            TENANT_ID,
            record.artifact_id,
            inline_text=f"第 {index} 版",
            preview_text=f"第 {index} 版",
            content_sha256=digest,
            content=payload,
        )
        assert updated is not None
        assert updated.content_sha256 == digest
        fetched = await store.get(TENANT_ID, record.artifact_id)
        assert fetched is not None
        assert fetched.content == payload
        assert fetched.content_sha256 == _digest(fetched.content)

    assert len(set(digests)) == 2, "两次保存的摘要不该相同（内容不同）"


# ===========================================================================
# 2. 下载端点：用户真正拿到的那一手
# ===========================================================================
def _asgi_client(settings: Settings) -> AsyncClient:
    """构造 ASGI 测试客户端（不触发 lifespan，避免启动期连库）。

    Args:
        settings: 配置实例。

    Returns:
        AsyncClient: 异步客户端。
    """
    return AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    )


async def test_download_endpoint_returns_real_zip_in_pg_mode(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """`GET /files/artifacts/{token}` 在 PG 模式下返回**真 docx 字节**。

    这是缺陷的现场：修复前这里拿到的 body 是 `inline_text` 的 UTF-8 字节
    （前两字节不是 `PK`），浏览器落成一个打不开的 .docx。
    """
    blob = _docx_blob()
    task_id = await _seed_task(pg_settings)
    record = _record(task_id, blob=blob)
    await store.add_artifacts([record])

    # 端点通过 `get_request_settings(request)` 取注入配置（app.state.settings），
    # 不再读模块级 `get_settings()` 单例——这正是 S9 修复顺带纠掉的配置注入问题。
    reset_artifact_store()

    token = make_token(
        tenant_id=TENANT_ID,
        artifact_id=record.artifact_id,
        version=1,
        settings=pg_settings,
    )
    async with _asgi_client(pg_settings) as client:
        response = await client.get(f"/files/artifacts/{token}")

    assert response.status_code == 200
    body = response.content
    assert body[:2] == b"PK", f"下载回来的不是 zip，前两字节={body[:2]!r}"
    assert len(body) > MIN_DOCX_BYTES
    assert body == blob.content, "下载字节与渲染字节不一致"

    disposition = response.headers["content-disposition"]
    assert "filename*=" in disposition, "中文文件名必须走 RFC 5987 的 filename*"
    assert "UTF-8''" in disposition

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert DOCX_DOCUMENT_PART in archive.namelist()


async def test_download_endpoint_still_falls_back_to_inline_text(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """纯文本产物仍按 `inline_text` 出字节（回落分支不许被这次修复删掉）。

    回落本身是**对的**：`text` / `table` 类产物只有正文，没有文件。
    要修的是"文件类产物也掉进回落"，不是"把回落删了"。
    """
    task_id = await _seed_task(pg_settings)
    record = _record(
        task_id,
        type="text",
        content=b"",
        content_sha256=_digest("周报正文".encode()),
        inline_text="周报正文",
        name="本周周报",
        size_bytes=len("周报正文".encode()),
    )
    await store.add_artifacts([record])

    reset_artifact_store()

    token = make_token(
        tenant_id=TENANT_ID,
        artifact_id=record.artifact_id,
        version=1,
        settings=pg_settings,
    )
    async with _asgi_client(pg_settings) as client:
        response = await client.get(f"/files/artifacts/{token}")

    assert response.status_code == 200
    assert response.content == "周报正文".encode()


# ===========================================================================
# 3. 内存后端对照（修复不许改坏它）
# ===========================================================================
async def test_memory_backend_keeps_content_unchanged() -> None:
    """内存后端行为保持不变（它本来就存 `content`）。"""
    memory = InMemoryArtifactStore()
    blob = _docx_blob()
    record = ArtifactRecord(
        tenant_id=TENANT_ID,
        artifact_id="art_mem_docx",
        task_id="task_mem",
        step_id="step_mem",
        output_key="document",
        type="file",
        name="行政公文",
        inline_text="预览",
        content_sha256=_digest(blob.content),
        content=blob.content,
        size_bytes=blob.size_bytes,
    )
    await memory.add_artifacts([record])
    fetched = await memory.get(TENANT_ID, "art_mem_docx")
    assert fetched is not None
    assert fetched.content == blob.content
