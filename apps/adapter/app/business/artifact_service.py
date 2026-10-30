"""产物内容查询入口（审计 2026-10-30 P1-8，`import_direction_baseline.txt` R-API-DB 收口）。

`api/v1/artifact_files.py` 是内容代理（无 JSON 信封、无业务语义），此前直接
import `app.db.repositories.artifact_repo` 取产物记录，绕过业务层唯一入口
（基线 R-API-DB，due_batch=B27）。本模块是业务层为下载链路暴露的最小查询口：

- 只读、按租户过滤（租户来自**已验签**的下载令牌，不可由请求方自由指定）；
- 不做业务裁决——留存期（`expires_at`）判定留在下载链路，与既有语义一致。

api → business → db 的方向在 `deploy/validate_import_directions.py` 的
ALLOWED 矩阵内；本模块就位后基线对应行已删除（修好即删，不留账）。
"""

from __future__ import annotations

from app.core.config import Settings
from app.db.repositories.artifact_repo import ArtifactRecord, get_artifact_store


async def fetch_artifact(
    settings: Settings, tenant_id: str, artifact_id: str
) -> ArtifactRecord | None:
    """按租户获取产物记录。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID（来自已验签的下载令牌）。
        artifact_id: 产物 ID。

    Returns:
        ArtifactRecord | None: 产物记录；不存在时返回 None。
    """
    return await get_artifact_store(settings).get(tenant_id, artifact_id)
