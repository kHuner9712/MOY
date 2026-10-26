"""会话与消息仓储（阶段四 T4-02.2，架构 §3.1 / §3.2 / §4.4）——公开出口。

本包是 `app/db/repositories/conversation_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。**本包有两个协议、四条实现**，因此
`__init__` 里有两个独立的进程级单例：

```
records.py    数据记录 + 状态/长度常量 + ID 生成 + recoverable_until
   ↑
protocol.py   ConversationStore / MessageStore 契约（唯一，双实现都必须实现）
   ↑
shared.py     两侧共用的游标编解码（encode_cursor / decode_cursor）
   ↑
memory.py     InMemory{Conversation,Message}Store（development/test，含 clear）
postgres.py   Postgres{Conversation,Message}Store（生产，表名 + 行映射器 + 表仓储）
   ↑
__init__.py   两个单例（get_conversation_store / get_message_store）+ reset_conversation_stores
```

`protocol.py` / `shared.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得
import 对侧，`memory ↔ postgres` 立刻成环。单向依赖由
`deploy/validate_repository_parity.py`（G-38，两条三元组分别核对）与
`deploy/validate_import_directions.py`（G-37）双重把关。

**`reset_conversation_stores()` 一次清两个单例**（原样保留）：只重置会话不重置消息，
会让"清库后仍读到上一用例的消息"这类跨用例污染变成间歇性失败。

**本文件不反向依赖兼容层**：`tests/integration/test_conversation_endpoints.py` 的白盒
夹具按**本包路径**注入单例（`conversation._conversation_store = store`），因此这里只需要
自己的模块全局——不在本层回读旧 shim 的同名全局。包里反向引用 `*_repo.py` 会把兼容层
的生命周期绑进生产代码（倒置依赖），也会让"测试白盒注入"这种实现细节固化成生产耦合；
兼容层只是给调用点留的过渡壳，不该被真源反过来依赖。

**`__all__` 与原 `conversation_repo.py` 完全一致（22 项，顺序不变）**：拆分不改变
公开面，19 处调用点无需改动。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.conversation.memory import (
    InMemoryConversationStore,
    InMemoryMessageStore,
)
from app.db.repositories.conversation.postgres import (
    CONVERSATION_TABLE,
    MESSAGE_TABLE,
    PostgresConversationStore,
    PostgresMessageStore,
)
from app.db.repositories.conversation.protocol import ConversationStore, MessageStore
from app.db.repositories.conversation.records import (
    MAX_TITLE_LENGTH,
    MESSAGE_STATUSES,
    TERMINAL_STATUSES,
    ConversationRecord,
    MessageRecord,
    new_conversation_id,
    new_message_id,
    now_iso,
    recoverable_until,
)
from app.db.repositories.conversation.shared import decode_cursor, encode_cursor

#: 会话存储单例（按配置选中实现）
_conversation_store: ConversationStore | None = None

#: 消息存储单例
_message_store: MessageStore | None = None


def get_conversation_store(settings: Settings | None = None) -> ConversationStore:
    """取会话存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        ConversationStore: 存储实现。
    """
    global _conversation_store
    conf = settings or get_settings()
    if _conversation_store is None:
        _conversation_store = (
            PostgresConversationStore(conf) if conf.uses_postgres else InMemoryConversationStore()
        )
    return _conversation_store


def get_message_store(settings: Settings | None = None) -> MessageStore:
    """取消息存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        MessageStore: 存储实现。
    """
    global _message_store
    conf = settings or get_settings()
    if _message_store is None:
        _message_store = (
            PostgresMessageStore(conf) if conf.uses_postgres else InMemoryMessageStore()
        )
    return _message_store


def reset_conversation_stores() -> None:
    """重置两个存储单例（测试隔离用）。"""
    global _conversation_store, _message_store
    _conversation_store = None
    _message_store = None


__all__: list[str] = [
    "CONVERSATION_TABLE",
    "MAX_TITLE_LENGTH",
    "MESSAGE_STATUSES",
    "MESSAGE_TABLE",
    "TERMINAL_STATUSES",
    "ConversationRecord",
    "ConversationStore",
    "InMemoryConversationStore",
    "InMemoryMessageStore",
    "MessageRecord",
    "MessageStore",
    "PostgresConversationStore",
    "PostgresMessageStore",
    "decode_cursor",
    "encode_cursor",
    "get_conversation_store",
    "get_message_store",
    "new_conversation_id",
    "new_message_id",
    "now_iso",
    "recoverable_until",
    "reset_conversation_stores",
]
