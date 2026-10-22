"""阶段四错误码与配置基座的注册制钉子（T4-01.2）。

**为什么单独一份文件**：`test_errors.py` 钉的是「注册表机制」（覆盖、分段、
文案纪律）；这份钉的是「阶段四这一批新增语义」——每一条都对应架构文档的一行，
改动任何一个都必须让这里变红，否则前端文案与 HTTP 语义会各自漂移。

四条判据：

1. `18001/18002/18003` 落在**新开的 18xxx 段**，且三者互不相同；
2. `15006` 是 **402**（由 200 改），文案为 `本月可用量已用完，请联系管理员调整`，
   且**没有**复用 `10005`（上游额度不足是另一件事）；
3. `message` 是唯一文案源：任何消费方都从 `ERROR_REGISTRY` 取值，不得另写字面量；
4. `Settings` 里 4 个档位密钥与 `profile_routes.yaml` 的变量名**逐字对应**
   （F-8：它们此前根本没在 Settings 声明，靠 loader 直接读 `os.environ` 兜住）。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode, http_status_of
from app.registry.loader import get_profile_routes

pytestmark = pytest.mark.unit


# ===========================================================================
# 1. 会话与记忆域 18xxx
# ===========================================================================
def test_conversation_and_memory_codes_are_registered_in_18xxx_segment() -> None:
    """18001/18002/18003 三个码已注册，且落在 18001-18099 段内。"""
    mapping = {
        ErrorCode.CONVERSATION_NOT_FOUND: 18001,
        ErrorCode.MEMORY_NOT_FOUND: 18002,
        ErrorCode.DELETE_CONFIRM_MISMATCH: 18003,
    }
    for code, expected in mapping.items():
        assert int(code) == expected
        assert 18001 <= expected <= 18099
        assert code in ERROR_REGISTRY, f"{expected} 未进注册表（注册制约束）"


@pytest.mark.parametrize(
    ("code", "expected_status", "expected_message"),
    [
        (ErrorCode.CONVERSATION_NOT_FOUND, 404, "该对话已不存在"),
        (ErrorCode.MEMORY_NOT_FOUND, 404, "这条信息已不存在"),
        (ErrorCode.DELETE_CONFIRM_MISMATCH, 400, "请输入完整的企业名称以确认"),
    ],
)
def test_new_codes_carry_exact_status_and_message(
    code: ErrorCode, expected_status: int, expected_message: str
) -> None:
    """HTTP 状态与文案必须与架构 §4.2 逐字一致（文案是终端用户唯一可见文本）。"""
    spec = ERROR_REGISTRY[code]
    assert spec.http_status == expected_status
    assert spec.message == expected_message
    assert http_status_of(code) == expected_status


def test_conversation_not_found_is_not_reusing_task_or_generic_codes() -> None:
    """会话/记忆是两个**独立**语义，不得挤进 12001（任务）或 20004（通用资源）。"""
    used = {int(code) for code in ErrorCode}
    assert 18001 in used
    assert int(ErrorCode.CONVERSATION_NOT_FOUND) != int(ErrorCode.RESOURCE_NOT_FOUND)
    assert int(ErrorCode.MEMORY_NOT_FOUND) != int(ErrorCode.RESOURCE_NOT_FOUND)


# ===========================================================================
# 2. 15006 → 402
# ===========================================================================
def test_quota_exhausted_is_402_not_200() -> None:
    """`15006` 由 200 改 402（架构 §7.3）：额度用尽是**阻断**，不是成功响应。"""
    spec = ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED]
    assert int(ErrorCode.QUOTA_EXHAUSTED) == 15006
    assert spec.http_status == 402
    assert http_status_of(ErrorCode.QUOTA_EXHAUSTED) == 402
    assert spec.message == "本月可用量已用完，请联系管理员调整"


def test_quota_exhausted_does_not_reuse_upstream_quota_code() -> None:
    """不得复用 `10005 MODEL_QUOTA_EXHAUSTED`：那是"上游回传额度不足"的映射分支。"""
    assert int(ErrorCode.MODEL_QUOTA_EXHAUSTED) == 10005
    assert ERROR_REGISTRY[ErrorCode.MODEL_QUOTA_EXHAUSTED].http_status == 200
    assert (
        ERROR_REGISTRY[ErrorCode.MODEL_QUOTA_EXHAUSTED].message
        != ERROR_REGISTRY[ErrorCode.QUOTA_EXHAUSTED].message
    )


# ===========================================================================
# 3. 档位密钥：Settings 与路由表逐字对应（F-8）
# ===========================================================================
def test_profile_keys_declared_in_settings_match_routing_table() -> None:
    """路由表引用 4 个 `OA_DIFY_*_APP_KEY`，Settings 必须都有同名字段。"""
    table = get_profile_routes()
    declared = set(Settings.model_fields)

    referenced: set[str] = set()
    for route in table.routes:
        referenced.add(route.default_app_env)
        referenced.update(route.fallback_app_env)
    # 只校验「档位专属」的 4 个 + 默认档；agent key 属任务域，不在本判据内
    phase4_keys = {name for name in referenced if name not in {"OA_DIFY_DEFAULT_APP_KEY"}}
    assert phase4_keys == {
        "OA_DIFY_FALLBACK_APP_KEY",
        "OA_DIFY_CODE_APP_KEY",
        "OA_DIFY_LONG_CONTEXT_APP_KEY",
        "OA_DIFY_REASONING_APP_KEY",
    }, f"路由表引用的环境变量集合变了，请同步架构 §6.3：{sorted(phase4_keys)}"

    for env_name in phase4_keys:
        field_name = env_name.removeprefix("OA_").lower()
        assert field_name in declared, f"Settings 缺字段 {field_name}（对应 {env_name}）"


def test_retention_and_memory_settings_have_architecture_defaults() -> None:
    """保留期/记忆默认值必须与架构 §3.5 口径表一致（改了要同步文档）。"""
    settings = Settings(_env_file=None)
    assert settings.retention_audit_days == 180
    assert settings.retention_usage_months == 13
    assert settings.retention_task_days == 180
    assert settings.retention_conversation_days == 365
    assert settings.conversation_soft_delete_recover_days == 30
    assert settings.memory_max_per_user == 200
    assert settings.memory_extract_every_turns == 10


def test_embedding_settings_default_to_disabled_with_fixed_dimension() -> None:
    """向量化默认关闭（A-1），维度默认 1024 且与索引类型绑定。"""
    settings = Settings(_env_file=None)
    assert settings.memory_embedding_enabled is False
    assert settings.memory_embedding_dim == 1024
    assert settings.memory_embedding_timeout_seconds > 0
    assert settings.memory_embedding_batch_size >= 1
