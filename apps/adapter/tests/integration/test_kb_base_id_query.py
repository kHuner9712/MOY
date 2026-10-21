"""R2 按 `base_id` 精确查询（加性参数）测试（阶段二 B09 收尾，前端详情页定位）。

**为什么要这个参数**：前端知识库详情页拿到的是 `base_id`，此前只能拉 R2 列表
再在前端按 ID 扫描——库数量超过一页（`page_size` 上限 200）就定位不到。

**为什么是加性参数而不是新端点**：不新增端点（`phase2` 仍 38/38）、
不传时行为与之前**完全一致**；G-04 只禁「新增必填字段 / 删属性」，
可选 query 属允许范围。

**不可见语义**：传了 `base_id` 但该库不可见（他人私有库 / 不存在 / 已删除）
一律返回**空列表**（`code=0`），不返回 404/403——否则这个参数就成了
「枚举他人知识库是否存在」的探测面。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户
TENANT = "default"

#: R2 路径
BASES_PATH = "/api/v1/knowledge/bases"


def _headers(settings: Settings, *, user_id: str, roles: list[str], idem: str) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        user_id: 用户 ID。
        roles: 角色列表。
        idem: 幂等键标签。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, tenant_id=TENANT, roles=roles)
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": TENANT,
        "Idempotency-Key": _key(idem),
    }


def _key(tag: str) -> str:
    """由标签派生稳定 UUIDv4 幂等键。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


def _create_base(
    client: TestClient,
    settings: Settings,
    *,
    name: str,
    visibility: str,
    tag: str,
    user_id: str = "usr_owner",
) -> str:
    """创建知识库并返回 `base_id`。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        name: 知识库名称。
        visibility: 可见范围（`organization` / `private` / `restricted`）。
        tag: 幂等键标签。
        user_id: 创建人。

    Returns:
        str: 知识库 ID。
    """
    response = client.post(
        BASES_PATH,
        json={"name": name, "description": "制度文档", "visibility": visibility},
        headers=_headers(settings, user_id=user_id, roles=["owner"], idem=f"create-{tag}"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0, response.text
    return str(response.json()["data"]["base_id"])


@pytest.mark.p0
def test_list_bases_by_base_id_returns_single_item(client: TestClient, settings: Settings) -> None:
    """传 `base_id` 只返回该库 1 条；不传时行为不变（仍返回全部可见库）。"""
    first = _create_base(
        client, settings, name="薪酬制度库", visibility="organization", tag="bid-a"
    )
    second = _create_base(
        client, settings, name="报销制度库", visibility="organization", tag="bid-b"
    )

    matched = client.get(
        BASES_PATH,
        params={"base_id": first},
        headers=_headers(settings, user_id="usr_owner", roles=["owner"], idem="bid-q1"),
    )
    assert matched.status_code == 200, matched.text
    body = matched.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["meta"]["total"] == 1, "精确查询只应有 1 条"
    assert [item["base_id"] for item in data["items"]] == [first]
    assert data["items"][0]["name"] == "薪酬制度库"

    # 加性：不传 base_id 时列表行为完全不变
    listed = client.get(
        BASES_PATH,
        headers=_headers(settings, user_id="usr_owner", roles=["owner"], idem="bid-q2"),
    )
    assert listed.status_code == 200
    ids = {item["base_id"] for item in listed.json()["data"]["items"]}
    assert {first, second} <= ids


@pytest.mark.p0
def test_list_bases_by_base_id_hides_invisible_and_unknown(
    client: TestClient, settings: Settings
) -> None:
    """无权限的私有库与不存在的 ID 都返回空列表（不 404/403，不泄露存在性）。"""
    private_base = _create_base(
        client, settings, name="我的私有库", visibility="private", tag="bid-private"
    )

    # 普通成员：有 knowledge:base:read，但看不到他人私有库 → 空列表
    hidden = client.get(
        BASES_PATH,
        params={"base_id": private_base},
        headers=_headers(settings, user_id="usr_member", roles=["member"], idem="bid-q3"),
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["code"] == 0
    assert hidden.json()["data"]["items"] == []
    assert hidden.json()["data"]["meta"]["total"] == 0

    # 不存在的 ID：同样空列表（与「无权限」不可区分）
    unknown = client.get(
        BASES_PATH,
        params={"base_id": "kb_not_exists_000000000000000000"},
        headers=_headers(settings, user_id="usr_owner", roles=["owner"], idem="bid-q4"),
    )
    assert unknown.status_code == 200
    assert unknown.json()["code"] == 0
    assert unknown.json()["data"]["items"] == []

    # 所有者本人：能查到自己的私有库
    owned = client.get(
        BASES_PATH,
        params={"base_id": private_base},
        headers=_headers(settings, user_id="usr_owner", roles=["owner"], idem="bid-q5"),
    )
    assert owned.status_code == 200
    assert [item["base_id"] for item in owned.json()["data"]["items"]] == [private_base]
