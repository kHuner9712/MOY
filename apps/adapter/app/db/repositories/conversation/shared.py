"""会话与消息仓储 · 两侧共用的纯函数（游标编解码）。

**为什么单独一个文件**：分页游标的编解码是**存储无关的分页协议**——内存实现按
`seq` 过滤、PG 实现按 `seq > :cursor` 过滤，但"客户端看到的那串 opaque 游标"
必须两侧完全一致，否则同一份前端代码在两种后端下表现不同。

它与 `records.py` 的关系是**并列**而不是依赖：游标不描述任何一行数据，
只描述"从哪儿往后读"。放进 `records.py` 会让"数据记录层"混入分页协议；
放进任一实现文件，另一侧就得 import 对侧（`memory ↔ postgres` 成环）。
因此按切片口径（"共用纯函数进 records.py 或 shared.py"）独立成 `shared.py`，
依赖方向 `records ← shared`，不反向 import 任何实现。
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Final

#: 游标里允许出现的键（多余键一律视为非法，避免客户端塞私货）
_CURSOR_KEYS: Final[frozenset[str]] = frozenset({"seq"})


def encode_cursor(seq: int) -> str:
    """把 `seq` 编码成 opaque 游标（`base64url({"seq": n})`）。

    为什么编码而不是直接给 `seq`：游标一旦公开，就会有人按 `seq=1,2,3` 手工翻页
    或做算术假设；编码成不透明串是在告诉调用方「顺序语义由服务端定义」。

    Args:
        seq: 消息序号。

    Returns:
        str: base64url 字符串（无 padding，可直接进 query）。
    """
    payload = json.dumps({"seq": int(seq)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int | None:
    """解析游标；非法游标返回 None（调用方据此报 20001，而不是静默从头开始）。

    Args:
        cursor: 游标字符串。

    Returns:
        int | None: `seq`；非法返回 None。
    """
    text = (cursor or "").strip()
    if not text:
        return None
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) - _CURSOR_KEYS:
        return None
    value = payload.get("seq")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


__all__: list[str] = ["decode_cursor", "encode_cursor"]
