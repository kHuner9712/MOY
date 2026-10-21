"""业务能力层（阶段二 D-02：`app/business/`）。

分层职责（架构文档 2.1 结论第 2 条）：

- `api/v1`：参数装配、响应封装、鉴权与幂等依赖；
- `services/`：协议转换（对下游形态做「形态防火墙」）；
- `business/`：**业务规则与事务编排**（一个 HTTP 请求一个事务）；
- `db/`：只有 SQL 与会话管理。

本阶段（B03）先落地 per-kb 凭据机制（`credential_service`），
后续批次按域追加知识库元数据、授权、计量、组织、计费服务。
"""

from __future__ import annotations

from app.business.credential_service import (
    CredentialResolver,
    ResolvedCredential,
    get_credential_resolver,
    ref_suffix,
    reset_credential_resolver,
)

__all__ = [
    "CredentialResolver",
    "ResolvedCredential",
    "get_credential_resolver",
    "ref_suffix",
    "reset_credential_resolver",
]
