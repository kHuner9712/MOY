"""资源 ID 生成（架构文档 9.1 命名规范：类型缩写 + ULID）。

ULID（Universally Unique Lexicographically Sortable Identifier）规格：
- 26 位 Crockford Base32 字符（`0-9` + `ABCDEFGHJKMNPQRSTVWXYZ`，剔除易混淆的 I/L/O/U）；
- 前 10 位为 48 位毫秒时间戳（保证按时间字典序递增）；
- 后 16 位为 80 位随机数；
- 同一毫秒内生成时递增随机部分，保证严格单调（便于按 ID 排序与分页）。

示例：`kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4`。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Final

#: Crockford Base32 字母表（剔除 I / L / O / U，避免与 1 / 0 混淆）
_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 时间戳部分长度（48 位毫秒）
_TIMESTAMP_LEN: Final[int] = 10

#: 随机部分长度（80 位）
_RANDOM_LEN: Final[int] = 16

#: ULID 总长度
_ULID_LEN: Final[int] = _TIMESTAMP_LEN + _RANDOM_LEN

#: 随机数部分的最大值（用于同毫秒内递增）
_MAX_RANDOM: Final[int] = 32**_RANDOM_LEN - 1

#: 同毫秒递增的锁
_lock: Final[threading.Lock] = threading.Lock()

#: 上一次生成的毫秒时间戳
_last_ms: Final[list[int]] = [-1]

#: 上一次生成的随机部分
_last_random: Final[list[int]] = [0]


def _encode(value: int, length: int) -> str:
    """将整数编码为定长 Crockford Base32 字符串。

    Args:
        value: 非负整数值。
        length: 输出长度。

    Returns:
        str: 定长 Base32 字符串。
    """
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """生成一个新的 ULID。

    同一毫秒内多次调用时，随机部分严格递增，保证 ID 按生成顺序字典序递增。

    Returns:
        str: 26 位 ULID 字符串。
    """
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms[0]:
            # 同毫秒：随机部分 +1（溢出时极罕见，回退到新的随机数）
            if _last_random[0] >= _MAX_RANDOM:
                now_ms += 1
                _last_ms[0] = now_ms
                _last_random[0] = int.from_bytes(os.urandom(10), "big")
            else:
                _last_random[0] += 1
        else:
            _last_ms[0] = now_ms
            _last_random[0] = int.from_bytes(os.urandom(10), "big")
        random_part = _last_random[0]
        timestamp_part = _last_ms[0]

    return _encode(timestamp_part, _TIMESTAMP_LEN) + _encode(random_part, _RANDOM_LEN)


def new_resource_id(prefix: str) -> str:
    """生成带类型前缀的资源 ID。

    Args:
        prefix: 类型缩写（如 `kb` / `doc` / `task`），带不带尾随下划线均可。

    Returns:
        str: 形如 `kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的资源 ID。
    """
    normalized = prefix if prefix.endswith("_") else f"{prefix}_"
    return f"{normalized}{new_ulid()}"


def is_ulid(value: str) -> bool:
    """判断字符串是否为合法 ULID。

    Args:
        value: 待判断的字符串。

    Returns:
        bool: 合法返回 True。
    """
    if len(value) != _ULID_LEN:
        return False
    return all(char in _ALPHABET for char in value)


def is_resource_id(value: str, prefix: str) -> bool:
    """判断字符串是否为指定前缀的合法资源 ID。

    Args:
        value: 待判断的字符串。
        prefix: 期望的类型缩写（含或不含下划线均可）。

    Returns:
        bool: 合法返回 True。
    """
    normalized = prefix if prefix.endswith("_") else f"{prefix}_"
    if not value.startswith(normalized):
        return False
    return is_ulid(value[len(normalized) :])
