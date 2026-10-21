"""资源 ID 生成单元测试（架构文档 9.1：类型缩写 + ULID）。"""

from __future__ import annotations

import pytest

from app.core.ids import is_resource_id, is_ulid, new_resource_id, new_ulid

pytestmark = pytest.mark.unit


def test_ulid_length_and_alphabet() -> None:
    """ULID 为 26 位 Crockford Base32（剔除 I/L/O/U）。"""
    value = new_ulid()
    assert len(value) == 26
    assert is_ulid(value)
    for char in value:
        assert char not in {"I", "L", "O", "U"}


def test_ulid_is_unique_across_many_calls() -> None:
    """大量调用不重复。"""
    values = {new_ulid() for _ in range(5000)}
    assert len(values) == 5000


def test_ulid_is_monotonic_within_same_millisecond() -> None:
    """同毫秒内生成的 ULID 严格单调递增（保证按 ID 排序等价于按时间排序）。"""
    values = [new_ulid() for _ in range(200)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_ulid_embeds_timestamp_prefix() -> None:
    """前 10 位为 48 位毫秒时间戳，解析后接近当前时间。"""
    import time

    value = new_ulid()
    timestamp_part = value[:10]
    decoded_ms = 0
    for char in timestamp_part:
        decoded_ms = decoded_ms * 32 + "0123456789ABCDEFGHJKMNPQRSTVWXYZ".index(char)
    now_ms = int(time.time() * 1000)
    assert abs(now_ms - decoded_ms) < 5000, "时间戳部分应与当前时间接近"


def test_new_resource_id_prefixes() -> None:
    """资源 ID 形如 `kb_01HF8...`。"""
    value = new_resource_id("kb")
    assert value.startswith("kb_")
    assert is_resource_id(value, "kb")
    assert is_resource_id(value, "kb_"), "带不带尾随下划线都应可校验"


def test_new_resource_id_accepts_prefix_with_underscore() -> None:
    """前缀带尾随下划线时不重复添加。"""
    value = new_resource_id("task_")
    assert value.startswith("task_")
    assert not value.startswith("task__")


@pytest.mark.parametrize(
    ("value", "prefix", "expected"),
    [
        ("kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4", "kb", True),
        ("kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4", "doc", False),
        ("kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4", "kb_", True),
        ("kb_TOOSHORT", "kb", False),
        ("kb_01HF8YQZ3K7M9X2P4R6T8V0I", "kb", False),
        ("", "kb", False),
    ],
)
def test_is_resource_id(value: str, prefix: str, expected: bool) -> None:
    """资源 ID 校验。"""
    assert is_resource_id(value, prefix) is expected


def test_is_ulid_rejects_invalid() -> None:
    """非法 ULID 被拒绝。"""
    assert not is_ulid("")
    assert not is_ulid("too-short")
    assert not is_ulid("01K4Q8Z2X7M9B3N5P6R8T0V2W4!")
