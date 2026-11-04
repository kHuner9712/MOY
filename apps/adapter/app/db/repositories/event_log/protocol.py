"""事件日志仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `EventStore`，
协议若定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres`
的双向依赖。独立成文件后依赖方向是 `records ← protocol ← {memory, postgres}`，
单向且无环；`deploy/validate_repository_parity.py`（G-38）据此判定
「协议方法 ⊆ 两侧实现」且「两侧签名逐字一致」。

**四个方法的语义（规格 §3.1 E1 的取数方式，不是四个机制）**

| 方法 | 语义 | 用途 |
| --- | --- | --- |
| `append` | 追加一条事件，**同步**返回落库记录 | loop 的唯一写入口 |
| `read_after` | 按**回放游标**取增量 | SSE 续传 / 回放 |
| `fork` | 新会话 `seq=1` 写一条 `session.forked`，**不复制历史** | 分叉（O(1)） |
| `verify_chain` | 重算 hash 链 | 审计导出前的自检 |

**游标（cursor）与序号（seq）不是一回事**，这一条必须在这里说清，否则续传会错：

- `seq` 是**会话内**序号，从 1 开始，`(session_id, seq)` 是主键；
- `cursor` 是**回放流内**的位置。根会话 `cursor == seq`；fork 会话的回放流是
  「父流前 `at_seq` 条 + 本会话全部」，本会话事件的位置是 `at_seq + seq`。
  父流里的事件保留它们自己的 `cursor`（父流是本流的前缀）。

为什么必须有 cursor：fork 会话里父流与本流都有 `seq=1`，只按 `seq` 续传会让
「父流的第 5 条」与「本流的第 5 条」拿到同一个游标值 → 断线重连时要么丢要么重。

**为什么签名都带 `tenant_id`**（与设计文档 §5.1 的 `append(session_id, …)` 相比多一个参数）：
规格 §2 铁律写着「内核不接受无租户作用域的调用——缺 `tenant_id` 直接拒绝，不进 loop」，
且 `tenant_scope()` 需要一个租户才能设置 RLS 会话变量。把 `tenant_id` 做成显式入参、
由 `tenant_scope()` 的 `assert_tenant_allowed()` 校验，是唯一不靠调用方自觉的形态；
仓储**从不**从 `session_id` 里反解租户（那会把授权判断挪进字符串解析）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.db.repositories.event_log.records import Event


class EventStore(Protocol):
    """会话事件日志存储契约（内存 / PostgreSQL 双实现）。

    全部方法 `async`：PG 实现要开事务，内存实现也必须是 `async`——否则同一段
    调用代码在两个后端上的协程形状不同，`await` 只在生产上生效，测试永远发现不了。
    """

    async def append(
        self,
        tenant_id: str,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> Event:
        """追加一条事件（append-only，不可改不可删）。

        实现必须保证三件事：
        1. `seq` 在同一事务内取号（`MAX(seq)+1`），并发冲突靠主键重试；
        2. `prev_hash` 取上一条的 `hash`，首条为 `GENESIS_HASH`；
        3. `hash = sha256(prev_hash || event_id || type || canonical_json(payload))`。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            event_type: 事件类型（必须在 `EVENT_TYPES` 封闭枚举内）。
            payload: 事件载荷（`model.delta` 会被采样）。

        Returns:
            Event: 落库后的事件记录（含 `seq` / `hash` / `cursor`）。

        Raises:
            ValueError: 事件类型未登记（V1.0 只有 14 类）。
            ApiError: 持续取号冲突（超过 `MAX_SEQ_ATTEMPTS` 次）。
        """
        ...

    async def read_after(
        self,
        tenant_id: str,
        session_id: str,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[Event]:
        """按回放游标取增量（`cursor > after_seq`，升序）。

        fork 会话会先展开父链（递归深度上限 `FORK_DEPTH_LIMIT`），再接本会话，
        因此"从头回放一个分叉会话"等价于"回放它的完整历史"。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            after_seq: 回放游标（传 0 表示从头）。
            limit: 单次最多返回条数（必须为正）。

        Returns:
            list[Event]: 升序事件列表（可能为空）。

        Raises:
            ValueError: `limit` 非正。
            ApiError: fork 深度超过上限（父链被截断时拒绝给出不完整的回放）。
        """
        ...

    async def fork(
        self,
        tenant_id: str,
        session_id: str,
        at_seq: int | None = None,
    ) -> str:
        """从某个游标处分叉出新会话（**不复制事件**，O(1)）。

        只在新会话的 `seq=1` 写一条 `session.forked {parent_session, at_seq}`；
        回放时先读父会话 `1..at_seq`，再接本会话。100 次分叉不产生 100 份历史。

        Args:
            tenant_id: 租户 ID。
            session_id: 父会话 ID。
            at_seq: 分叉游标；None 表示分叉到父会话当前末尾。

        Returns:
            str: 新会话 ID。

        Raises:
            ApiError: 父会话不存在、`at_seq` 越界，或分叉深度超过
                `FORK_DEPTH_LIMIT`（超限报业务错误，不静默截断）。
        """
        ...

    async def tail_cursor(self, tenant_id: str, session_id: str) -> int:
        """取会话回放流的**末尾游标**（空会话返回 0）。

        为什么需要它（缺陷 D3）：SSE 续传要把客户端带来的 `Last-Event-ID` **钳**在
        `[0, tail]` 区间内。不钳会怎样：客户端传一个越界值（时间戳、别的会话的 id、
        或人为构造），服务端照抄回 `id: <越界值>` 的终态帧，客户端下次仍带同一个越界
        值 → **永久静默无进度**，而且服务端一切正常、日志无异常。越界值一律按
        "从头回放"处理：回放幂等，重复下发不会让状态错。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            int: 末尾游标（根会话 = 最大 `seq`；fork 会话 = 偏移 + 最大 `seq`）；
            会话不存在或没有任何事件时返回 0。
        """
        ...

    async def verify_chain(self, tenant_id: str, session_id: str) -> bool:
        """重算并校验 hash 链（含 fork 父链）。

        审计导出的前置自检：导出前必须证明"这条链是自洽的"，否则客户拿到的
        证据连自己都复算不通。校验内容：`prev_hash` 连接正确、每条 `hash` 可复算。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            bool: 链自洽返回 True；任何一条不匹配、或父链深度超限返回 False。
        """
        ...


__all__ = ["EventStore"]
