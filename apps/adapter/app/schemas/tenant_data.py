"""数据权利的请求/响应模型（阶段四 T4-05.3，架构 §4.2 TD1 / TD2）。

**字段口径直接来自架构 §4.2 的表格**，新增字段只能追加：

| 端点 | 架构要求的响应 `data` |
| --- | --- |
| TD1 | `export_id, status="preparing", estimated_rows` |
| TD2 | `deletion_id, deleted_tables[], scheduled_at` |

两处**刻意的偏离**（都写进 `docs/phase4-batch-log.md`）：

1. TD1 的 `status` 恒为 `ready`、并**追加** `download_url / expires_at / rows`。
   架构写 `preparing` 是因为当时设想异步；本批改为同步生成（理由见
   `app/business/tenant_data_service.py` 模块说明），同步返回 `preparing`
   会让前端以为还要轮询，而**没有轮询端点**。`estimated_rows` 保留为 `rows`
   的别名语义：`rows` 里的数字就是实测行数（不是估算），比 `estimated_rows`
   更有用——导出包已经生成了，报估计值没有意义。
2. TD2 用 `deleted_at` 而不是 `scheduled_at`：同步删除没有"计划执行时间"，
   返回一个未来的时间戳会让客户以为"还没删"。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.business.tenant_data_service import EXPORT_SCOPES


class TenantDataExportRequest(BaseModel):
    """TD1 导出请求。

    Attributes:
        scope: 导出类别；缺省 = 全部 5 类。顺序不敏感，服务端按固定顺序输出。
    """

    scope: list[str] = Field(
        default_factory=lambda: list(EXPORT_SCOPES),
        description="导出范围：audit / usage / chat / memory / artifact",
    )


class TenantDataExportData(BaseModel):
    """TD1 导出结果（同步完成，包已落产物库）。

    Attributes:
        export_id: 导出 ID（同时也是产物 ID，可按此反查产物）。
        status: 恒为 `ready`（同步生成完毕，无需轮询）。
        download_url: 下载地址（短期令牌，复用 `/files/artifacts/{token}`）。
        expires_at: 链接与包的过期时间（ISO 8601）。
        rows: 各类别实测行数 + `total`。
        size_bytes: 压缩包大小（字节）。
    """

    export_id: str
    status: str = "ready"
    download_url: str = ""
    expires_at: str = ""
    rows: dict[str, int] = Field(default_factory=dict)
    size_bytes: int = 0


class TenantDataDeleteRequest(BaseModel):
    """TD2 删除请求（二次确认）。

    Attributes:
        confirm_text: 用户逐字输入的企业名称；与库内名称不一致返回 18003。
        scope: 删除范围；缺省 = 全部 5 类。
    """

    confirm_text: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="请输入完整的企业名称以确认（须与库内名称逐字一致）",
    )
    scope: list[str] = Field(
        default_factory=lambda: list(EXPORT_SCOPES),
        description="删除范围：audit / usage / chat / memory / artifact",
    )


class TenantDataDeleteData(BaseModel):
    """TD2 删除结果（**两段式**：应用侧已删 / 待 DBA 执行）。

    Attributes:
        deletion_id: 删除 ID（同时也是删除证明产物 ID）。
        deleted_at: 删除完成时间（ISO 8601）。
        deleted_tables: 应用侧实际执行删除的表（0 行也算，表达"查过且确认没了"）。
        deleted_rows: 表名 → 删除行数。
        skipped_tables: 因无权限被跳过的表（当前只有 `audit_log`，且这是预期行为）。
        retained_tables: 不纳入在线删除、需 DBA 全库脚本的表。
        audit_pending_dba: 审计表剩余行数（**不与上面的行数合并**）。
        audit_sql_hint: 交给 DBA 执行的 SQL。
        proof_download_url: 删除证明下载地址（短期令牌）。
    """

    deletion_id: str
    deleted_at: str = ""
    deleted_tables: list[str] = Field(default_factory=list)
    deleted_rows: dict[str, int] = Field(default_factory=dict)
    skipped_tables: list[str] = Field(default_factory=list)
    retained_tables: list[str] = Field(default_factory=list)
    audit_pending_dba: int = 0
    audit_sql_hint: str = ""
    proof_download_url: str = ""


__all__: list[str] = [
    "TenantDataDeleteData",
    "TenantDataDeleteRequest",
    "TenantDataExportData",
    "TenantDataExportRequest",
]
