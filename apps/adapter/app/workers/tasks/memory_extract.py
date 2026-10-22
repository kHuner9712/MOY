"""`memory_extract` 任务处理器（阶段四 T4-03.3，架构 §5.3）。

**这个函数是 D-1 降级判据的唯一落点**：抽取失败 → 记 1 条
`memory_extract_degraded` → 异常继续上抛给 `JobRunner`，由它按
`max_attempts=1` 把任务收口为 `failed`（不重试，下个 10 轮周期再来）。

为什么降级日志放在**这里**而不是 `MemoryService` 里：服务层可能被
手动触发（提交 4 的"立即整理我的记忆"）复用，那时不该按"后台任务失败"
计数；一处只记一次，判据才可断言（PRD §4.7 D-1：`memory_extract_degraded` +1）。
"""

from __future__ import annotations

from app.business.memory_service import MemoryService
from app.core.logging import get_logger
from app.db.repositories.job_repo import JobRecord

logger = get_logger(__name__)


async def handle_memory_extract(job: JobRecord, *, service: MemoryService) -> None:
    """执行一次事实抽取。

    Args:
        job: 任务记录（`payload` 里带 `conversation_id` 与 `user_id`）。
        service: 记忆服务。

    Raises:
        Exception: 抽取失败的原始异常（由 `JobRunner` 按 `max_attempts` 收口）。
            抛出前已记一条 `memory_extract_degraded`，**不含任何用户正文**。
    """
    conversation_id = str(job.payload.get("conversation_id") or job.dedupe_key or "")
    user_id = str(job.payload.get("user_id") or "")
    if not conversation_id or not user_id:
        # 任务参数缺失属于编码/数据缺陷：直接判失败并留痕，别静默"成功"
        logger.warning(
            "memory_extract_bad_payload",
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            has_conversation=bool(conversation_id),
            has_user=bool(user_id),
        )
        raise ValueError("memory_extract payload requires conversation_id and user_id")

    try:
        result = await service.extract_and_merge(job.tenant_id, user_id, conversation_id)
    except Exception as exc:
        logger.warning(
            "memory_extract_degraded",
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            conversation_id=conversation_id,
            attempts=job.attempts,
            detail=type(exc).__name__,
        )
        raise
    logger.info(
        "memory_extract_job_done",
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        conversation_id=conversation_id,
        extracted=result.extracted,
        merged=result.merged,
        superseded=result.superseded,
        evicted=result.evicted,
        dropped_sensitive=result.dropped_sensitive,
        skipped=result.skipped,
        write_failed=result.write_failed,
    )


__all__: list[str] = ["handle_memory_extract"]
