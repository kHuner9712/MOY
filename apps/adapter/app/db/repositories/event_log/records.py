"""会话事件日志仓储 · 记录与纯函数（内存 / PostgreSQL 双实现共用）。

本包是 `event_log` 表的三拆产物，与 `app/db/repositories/task/` 同一体例：

```
records.py    数据记录（dataclass）+ 14 类事件封闭枚举 + hash/规范化/采样纯函数
   ↑
protocol.py   EventStore 契约（唯一，内存 / PG 都必须实现）
   ↑
memory.py     InMemoryEventStore（development / test）
postgres.py   PostgresEventStore（生产，`event_log` 哈希分区 + RLS + 追加写）
   ↑
__init__.py   进程级单例（get_event_store / get_memory_event_store / reset_event_store）
```

`protocol.py` 必须是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环（`deploy/validate_repository_parity.py` G-38 与
`deploy/validate_import_directions.py` G-37 双重把关）。

设计依据：规格 §3.1 原则 E1（事件日志是**状态真源**，不是日志）、
设计文档 §5.1（表设计 / 三条硬约束 / 14 类事件 / fork 语义 / 递归深度上限 8）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from app.core.ids import new_resource_id

#: 事件 ID 前缀（架构文档 9.1 命名规范：类型缩写 + ULID）
EVENT_ID_PREFIX: Final[str] = "evt_"

#: 分叉会话 ID 前缀
SESSION_ID_PREFIX: Final[str] = "ses_"

#: 创世前的 `prev_hash`：首条事件的 `prev_hash` 是 64 个 `0`
#:
#: 为什么是 64 个 `0` 而不是空串：链的"起点"必须是一个**长度与真实 hash 相同**的
#: 固定值，否则 `prev_hash || event_id || type || payload` 的拼接在首条事件上会
#: 出现"少 64 个字符"的可区分前缀，让"拿掉第一条事件"这种篡改在长度上可见。
GENESIS_HASH: Final[str] = "0" * 64

#: fork 递归深度上限（设计文档 §5.1：`WITH RECURSIVE` 一次查完父链，深度上限 8）
#:
#: 上限的意义不是性能而是**可控性**：链深由 fork 次数决定，无上限时一次恶意或
#: 失控的分叉递归会把一条回放变成 N 次 JOIN；限制在 8 层后，最坏回放成本可算。
FORK_DEPTH_LIMIT: Final[int] = 8

#: `seq` 取号的最大尝试次数（撞主键冲突即重试；超过即认定持续冲突）
#:
#: 为什么是 16 而不是 3（缺陷 D4）：`MAX(seq)+1` + 主键冲突重试的失败面是
#: **并发写者数**——N 个写者抢同一格时，最坏情况需要 N-1 次重试才能全部落库。
#: 上限 3 意味着 5 并发预期至少 2 条失败并抛 30003，而**事件是真丢的**
#: （丢的不是日志，是状态真源的一条事实）。16 覆盖到 16 个同会话并发写者；
#: 每次重试带指数退避 + 抖动（见 `postgres.append`），把"同一时刻再次相撞"摊开。
#:
#: 代价：最坏总等待约 1.3s（5ms 起、倍增至 16 次），只在持续冲突时发生；
#: 正常路径零等待。要彻底消除重试可以改用 `pg_advisory_xact_lock` 串行化取号，
#: 但那会把同会话写入的并发度压成 1（loop 的流式 append 与工具回写会互相排队），
#: 在"事件多、写入频繁"的会话里是更贵的代价。
MAX_SEQ_ATTEMPTS: Final[int] = 16

#: 事件类型封闭枚举（V1.0 共 14 类，设计文档 §5.1）
#:
#: **封闭**是刻意的：事件类型是投影函数的唯一分派依据，往流里塞一个未登记的类型，
#: 回放侧只能静默忽略它——而"静默忽略"意味着"这一段历史丢了但没人报错"。
#: `subagent.spawn` 属 V1.1 预留，**不在**本集合内：V1.0 的装载器拒绝产生该事件，
#: 仓储层因此也拒绝写入它（两层拒绝，任一层生效都够）。
EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session.created",
        "turn.started",
        "context.assembled",
        "context.compressed",
        "model.request",
        "model.delta",
        "model.response",
        "tool.requested",
        "tool.completed",
        "approval.requested",
        "approval.resolved",
        "artifact.created",
        "session.forked",
        "turn.finished",
    }
)

#: 常用事件类型的具名常量（避免字符串在 4 个模块里各写一遍）
EVENT_SESSION_CREATED: Final[str] = "session.created"
EVENT_TURN_STARTED: Final[str] = "turn.started"
EVENT_MODEL_DELTA: Final[str] = "model.delta"
EVENT_SESSION_FORKED: Final[str] = "session.forked"
EVENT_TOOL_REQUESTED: Final[str] = "tool.requested"
EVENT_TOOL_COMPLETED: Final[str] = "tool.completed"
EVENT_TURN_FINISHED: Final[str] = "turn.finished"

#: fork 标记 payload 的两个键（`session.forked {parent_session, at_seq}`）
FORK_PARENT_KEY: Final[str] = "parent_session"
FORK_AT_SEQ_KEY: Final[str] = "at_seq"

#: `model.delta` 采样：首部保留字符数
#:
#: 为什么必须采样（R3 风险对冲）：一次 turn 的 token 流会产出几十到上百条
#: `model.delta`，一条一个满文本，行数与字节数都随 turn 长度线性膨胀（实测口径：
#: 行数 ≈ turn 数的 20 倍）。事件日志的定位是"状态真源 + 完整性证据"，不是"原文
#: 存档"——完整文本属于模型调用的返回体，落库的是**首尾 + 摘要**。
DELTA_SAMPLE_HEAD_CHARS: Final[int] = 64

#: `model.delta` 采样：尾部保留字符数
DELTA_SAMPLE_TAIL_CHARS: Final[int] = 64

#: 一个 turn 内最多保留的 `model.delta` 行数（首 + 尾 两条）
MAX_DELTA_ROWS_PER_TURN: Final[int] = 2

#: 采样后 `model.delta` payload 里承载原文的字段（保留键名，值清空）
DELTA_TEXT_KEYS: Final[tuple[str, ...]] = ("delta", "text")

#: 采样摘要键（`{head, tail, chars, sha256, sampled}`）
DELTA_SAMPLE_KEY: Final[str] = "sample"


def validate_event_type(event_type: str) -> str:
    """校验事件类型在封闭枚举内。

    Args:
        event_type: 事件类型（如 `model.delta`）。

    Returns:
        str: 原样返回合法类型。

    Raises:
        ValueError: 类型未登记（V1.0 只允许 14 类；`subagent.spawn` 属 V1.1）。
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"unregistered event type {event_type!r}; "
            f"V1.0 closed enum has {len(EVENT_TYPES)} types (see design §5.1)"
        )
    return event_type


def new_event_id() -> str:
    """生成事件 ID。

    Returns:
        str: 形如 `evt_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的资源 ID。
    """
    return new_resource_id(EVENT_ID_PREFIX)


def now_iso() -> str:
    """当前时间（UTC，毫秒精度 ISO-8601，带 `Z`）。

    与 `task/records.py::now_iso` 同口径：领域模型里时间统一用 ISO 字符串，
    只有落 PG 时才转 `datetime`（asyncpg 对 `timestamptz` 参数只接受 `datetime`，
    见 `db/repositories/base.py::as_datetime` 的注释）。

    Returns:
        str: 形如 `2026-11-04T08:30:00.123+00:00` 的时间串。
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def canonical_json(payload: Mapping[str, Any]) -> str:
    """把 payload 序列化为**规范化 JSON**（hash 链的输入）。

    三个必须同时成立的条件（少一条，同一个 payload 就会算出两个 hash，
    客户复算时对不上——链的完整性证据当场失效）：

    1. **键排序**（`sort_keys=True`）：JSON 对象的键序在语言间不保证一致；
    2. **无多余空格**（`separators=(",", ":")`）：缩进与空格是表示层噪声；
    3. **UTF-8 不转义**（`ensure_ascii=False`）：中文键值转成 `\\uXXXX` 后，
       字符长度与字节长度都不再等于原文，跨语言复算必然漂移。

    Args:
        payload: 事件载荷（JSON 可序列化的映射）。

    Returns:
        str: 规范化后的 JSON 文本。
    """
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_hash(prev_hash: str, event_id: str, event_type: str, payload: Mapping[str, Any]) -> str:
    """计算事件 hash（`sha256(prev_hash || event_id || type || canonical_json(payload))`）。

    **这不是安全机制**（同库内 DBA 可重写整条链），而是给客户的完整性证据：
    审计导出附链头，客户可自行重算（设计文档 §5.1 原文口径）。

    Args:
        prev_hash: 上一条事件的 hash；首条事件传 `GENESIS_HASH`。
        event_id: 事件 ID。
        event_type: 事件类型。
        payload: 事件载荷。

    Returns:
        str: 64 位小写十六进制摘要。
    """
    material = f"{prev_hash}{event_id}{event_type}{canonical_json(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compact_delta_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """对 `model.delta` 做**载荷级采样**：原文只留首尾 + 摘要。

    幂等：已采样过的 payload（带 `sample` 且原文键已清空）原样返回，
    因此重放/重试不会把摘要再采样一次（那会把 `sha256` 算成摘要的摘要）。

    非 `model.delta` 类型**不受影响**（原样返回浅拷贝）：审批结论、工具结果等
    结构化载荷是投影的输入，压缩它们会改变回放结果。

    Args:
        event_type: 事件类型。
        payload: 原始载荷。

    Returns:
        dict[str, Any]: 采样后的载荷（新字典，不修改入参）。
    """
    body = dict(payload)
    if event_type != EVENT_MODEL_DELTA:
        return body

    text = ""
    for key in DELTA_TEXT_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value:
            text = value
            break
    if not text:
        # 已经是采样态（原文键缺失或为空）——保持幂等，不重复摘要
        return body

    head = text[:DELTA_SAMPLE_HEAD_CHARS]
    tail = text[-DELTA_SAMPLE_TAIL_CHARS:] if len(text) > DELTA_SAMPLE_HEAD_CHARS else ""
    body[DELTA_SAMPLE_KEY] = {
        "head": head,
        "tail": tail,
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sampled": True,
    }
    for key in DELTA_TEXT_KEYS:
        if key in body:
            body[key] = ""
    return body


def plan_delta_rows(delta_count: int) -> list[int]:
    """给一个 turn 内的 `model.delta` 序列规划**要落库的行下标**（行级采样）。

    为什么需要行级采样（设计文档 §5.1 的 R3 风险对冲）：一个 turn 的 token 流
    通常几十到上百个片段，一条片段一行会让 `event_log` 的行数变成 turn 数的
    20 倍量级，而回放与审计**不需要**逐片段的原文——首条确定"开始了"、末条确定
    "完整答案的结尾"，中间由 `model.response` 的完整结果承担。

    口径：1 条 → 保留它；≥2 条 → 只保留首、尾两条。

    Args:
        delta_count: 本 turn 的 `model.delta` 条数。

    Returns:
        list[int]: 需要落库的下标（升序）。
    """
    if delta_count <= 0:
        return []
    if delta_count == 1:
        return [0]
    return [0, delta_count - 1]


@dataclass(frozen=True, slots=True)
class Event:
    """一条事件（`event_log` 的行投影）。

    Attributes:
        tenant_id: 租户 ID（全部仓储与事件的必填维度，规格 §2 铁律）。
        session_id: 会话 ID。
        seq: 会话内单调序号（从 1 开始，与 `session_id` 组成主键）。
        event_id: 事件 ID（`evt_` + ULID）。
        type: 事件类型（`EVENT_TYPES` 之一）。
        payload: 事件载荷（JSON 对象）。
        prev_hash: 上一条事件 hash；会话首条为 `GENESIS_HASH`。
        hash: 本条 hash（见 `compute_hash`）。
        created_at: 落库时间（ISO-8601 字符串，UTC）。
        cursor: **回放游标**（派生值，不是表列）。
    """

    tenant_id: str
    session_id: str
    seq: int
    event_id: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    created_at: str
    cursor: int

    def is_fork_marker(self) -> bool:
        """本条是否为 fork 标记（`session.forked`）。

        Returns:
            bool: 是分叉标记返回 True。
        """
        return self.type == EVENT_SESSION_FORKED

    def parent_session(self) -> str:
        """取 fork 标记里的父会话 ID。

        Returns:
            str: 父会话 ID；非 fork 标记或字段缺失返回空串。
        """
        value = self.payload.get(FORK_PARENT_KEY)
        return value if isinstance(value, str) else ""

    def parent_at_seq(self) -> int:
        """取 fork 标记里的父会话切点（游标语义）。

        Returns:
            int: 切点；非 fork 标记或字段缺失返回 0。
        """
        value = self.payload.get(FORK_AT_SEQ_KEY)
        return value if isinstance(value, int) else 0


def build_event(
    *,
    tenant_id: str,
    session_id: str,
    seq: int,
    event_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    prev_hash: str,
    digest: str,
    created_at: str,
    cursor_offset: int = 0,
) -> Event:
    """构造 `Event`（内存 / PG 两个实现的**唯一**构造出口）。

    为什么要有这个工厂函数：`deploy/validate_repository_parity.py` 的 R4 规则
    逐点比对"两侧直接构造 record 时的关键字集合"，某一侧少写一个字段就判红
    （"某个代码路径少写字段"正是漂移发生的地方）。把构造收敛到一处，
    两个后端就不可能构造出形状不同的记录——一致性由**结构**保证，不靠人记得。

    Args:
        tenant_id: 租户 ID。
        session_id: 会话 ID。
        seq: 会话内序号。
        event_id: 事件 ID。
        event_type: 事件类型。
        payload: 事件载荷（应为已采样的载荷）。
        prev_hash: 上一条 hash。
        digest: 本条 hash。
        created_at: 落库时间。
        cursor_offset: 回放游标偏移（fork 会话为 `at_seq`，根会话为 0）。

    Returns:
        Event: 事件记录。
    """
    return Event(
        tenant_id=tenant_id,
        session_id=session_id,
        seq=seq,
        event_id=event_id,
        type=event_type,
        payload=dict(payload),
        prev_hash=prev_hash,
        hash=digest,
        created_at=created_at,
        cursor=cursor_offset + seq,
    )


def new_fork_session_id() -> str:
    """生成分叉会话的会话 ID。

    分叉会话不属于任何一个任务（一次分叉可能被多个任务消费），因此不套用
    `build_session_id(tenant, task)` 的 `租户:任务` 形态，而是独立发一个
    `ses_` 前缀的资源 ID；租户维度由 `tenant_id` 列与 `tenant_scope()` 保证。

    Returns:
        str: 形如 `ses_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的会话 ID。
    """
    return new_resource_id(SESSION_ID_PREFIX)


def build_session_id(tenant_id: str, task_id: str) -> str:
    """把任务映射为会话 ID（`task` 与 `session` 的当前一对一口径）。

    为什么把 `tenant_id` 编进 session_id 而不是只存列：`session_id` 同时是
    **HASH 分区键**与 SSE 的对外游标维度。两个租户用同一个 `task_id`
    （ULID 理论上可能重复，更现实的是导入数据）时，若只按 `task_id` 分区，
    两张不同租户的流会落在同一分区、共享 `seq` 空间——取号互相干扰，而且
    `PRIMARY KEY (session_id, seq)` 会让第二个租户**写不进去**。编码租户后，
    会话标识本身就是全局唯一的，与 `tenant_id` 列构成双保险。

    Args:
        tenant_id: 租户 ID。
        task_id: 任务 ID。

    Returns:
        str: 形如 `tnt_xxx:task_yyy` 的会话 ID。
    """
    return f"{tenant_id}:{task_id}"


__all__ = [
    "DELTA_SAMPLE_HEAD_CHARS",
    "DELTA_SAMPLE_KEY",
    "DELTA_SAMPLE_TAIL_CHARS",
    "EVENT_ID_PREFIX",
    "EVENT_MODEL_DELTA",
    "EVENT_SESSION_CREATED",
    "EVENT_SESSION_FORKED",
    "EVENT_TOOL_COMPLETED",
    "EVENT_TOOL_REQUESTED",
    "EVENT_TURN_FINISHED",
    "EVENT_TURN_STARTED",
    "EVENT_TYPES",
    "FORK_AT_SEQ_KEY",
    "FORK_DEPTH_LIMIT",
    "FORK_PARENT_KEY",
    "GENESIS_HASH",
    "MAX_DELTA_ROWS_PER_TURN",
    "MAX_SEQ_ATTEMPTS",
    "SESSION_ID_PREFIX",
    "Event",
    "build_event",
    "build_session_id",
    "canonical_json",
    "compact_delta_payload",
    "compute_hash",
    "new_event_id",
    "new_fork_session_id",
    "now_iso",
    "plan_delta_rows",
    "validate_event_type",
]
