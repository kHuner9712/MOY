"""注册表加载与配置校验单元测试（架构文档 6.1 `registry/`）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ApiError, ErrorCode
from app.registry.loader import (
    PROFILE_ROUTES_FILE,
    TOOL_REGISTRY_FILE,
    VALID_PROFILES,
    ProfileRouteTable,
    ToolRegistry,
    _load_yaml,
    _parse_profile_routes,
    _parse_tool_registry,
    get_profile_routes,
    get_tool_registry,
)

pytestmark = pytest.mark.unit


def test_profile_routes_registers_all_contract_profiles() -> None:
    """档位路由表必须覆盖契约枚举的全部值。"""
    table = get_profile_routes()
    assert set(table.profiles) == VALID_PROFILES


def test_profile_routes_get_returns_route() -> None:
    """按档位查询路由配置。"""
    table = get_profile_routes()
    route = table.get("general")
    assert route is not None
    assert route.display_name == "通用助手"
    assert route.max_context_turns == 20
    assert table.get("nonexistent") is None


def test_profile_routes_resolve_app_key_prefers_default() -> None:
    """应用密钥解析优先主路由。"""
    table = get_profile_routes()
    route = table.get("general")
    assert route is not None
    env = {"OA_DIFY_DEFAULT_APP_KEY": "app-main", "OA_DIFY_FALLBACK_APP_KEY": "app-fb"}
    assert route.resolve_app_key(env) == "app-main"


def test_profile_routes_resolve_app_key_falls_back() -> None:
    """主路由未配置时走降级链。"""
    table = get_profile_routes()
    route = table.get("code")
    assert route is not None
    env = {"OA_DIFY_DEFAULT_APP_KEY": "app-fb"}
    assert route.resolve_app_key(env) == "app-fb"


def test_profile_routes_resolve_app_key_returns_empty() -> None:
    """全部未配置时返回空串（桩模式）。"""
    table = get_profile_routes()
    route = table.get("general")
    assert route is not None
    assert route.resolve_app_key({}) == ""


def test_profile_routes_exposes_retrieval_defaults() -> None:
    """检索默认策略已封装在适配层，不对外暴露。"""
    defaults = get_profile_routes().retrieval_defaults
    assert defaults.top_k == 5
    assert defaults.score_threshold == 0.5


def test_profile_routes_lists_allowed_extensions() -> None:
    """文档格式白名单来自配置。"""
    extensions = get_profile_routes().allowed_document_extensions
    assert set(extensions) == {"pdf", "docx", "xlsx", "md", "txt"}


def test_tool_registry_has_both_example_connectors() -> None:
    """工具注册表必须登记两个示例连接器。"""
    registry = get_tool_registry()
    assert set(registry.tool_types) == {"email", "github"}
    assert registry.get("email") is not None
    assert registry.get("nonexistent") is None


def test_tool_registry_exposes_operations_and_timeout() -> None:
    """注册表暴露能力清单与超时声明。"""
    email = get_tool_registry().get("email")
    assert email is not None
    assert set(email.operation_names()) == {"send_message", "list_messages", "sync"}
    assert email.has_operation("send_message")
    assert not email.has_operation("delete_message")
    assert email.timeout_ms == 10_000
    assert email.connector_impl == "email_connector"


def test_github_timeout_matches_spec() -> None:
    """代码仓库工具超时声明为 15000ms（架构文档 3.4.2）。"""
    github = get_tool_registry().get("github")
    assert github is not None
    assert github.timeout_ms == 15_000


def test_registry_files_contain_no_secrets() -> None:
    """注册表只登记环境变量名，禁止写入真实密钥。"""
    from app.registry.loader import REGISTRY_DIR

    for filename in (PROFILE_ROUTES_FILE, TOOL_REGISTRY_FILE):
        content = (REGISTRY_DIR / filename).read_text(encoding="utf-8")
        assert "app-" not in content
        assert "secret" not in content.lower()


def test_load_yaml_missing_file_raises_config_error(tmp_path: Path) -> None:
    """注册表文件缺失时返回 30004。"""
    with pytest.raises(ApiError) as exc_info:
        _load_yaml(tmp_path / "missing.yaml")
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_profile_routes_rejects_unknown_profile() -> None:
    """未登记的档位必须被拒绝（防止真实模型名混入契约）。"""
    with pytest.raises(ApiError) as exc_info:
        _parse_profile_routes({"profiles": [{"profile": "gpt-4o"}]})
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_profile_routes_rejects_duplicate_profile() -> None:
    """重复档位必须被拒绝。"""
    payload = {"profiles": [{"profile": "general"}, {"profile": "general"}]}
    with pytest.raises(ApiError) as exc_info:
        _parse_profile_routes(payload)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_profile_routes_rejects_empty_list() -> None:
    """空档位列表必须被拒绝。"""
    with pytest.raises(ApiError) as exc_info:
        _parse_profile_routes({"profiles": []})
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_tool_registry_rejects_bad_category() -> None:
    """非法工具分类必须被拒绝。"""
    payload = {
        "tools": [
            {
                "tool_type": "email",
                "category": "unknown",
                "operations": [{"operation": "send_message"}],
            }
        ]
    }
    with pytest.raises(ApiError) as exc_info:
        _parse_tool_registry(payload)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_tool_registry_rejects_bad_auth_type() -> None:
    """非法授权方式必须被拒绝。"""
    payload = {
        "tools": [
            {
                "tool_type": "email",
                "auth_type": "magic",
                "operations": [{"operation": "send_message"}],
            }
        ]
    }
    with pytest.raises(ApiError) as exc_info:
        _parse_tool_registry(payload)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_tool_registry_requires_operations() -> None:
    """空能力清单必须被拒绝。"""
    with pytest.raises(ApiError) as exc_info:
        _parse_tool_registry({"tools": [{"tool_type": "email", "operations": []}]})
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parse_tool_registry_rejects_duplicate_tool_type() -> None:
    """重复工具类型必须被拒绝。"""
    payload = {
        "tools": [
            {"tool_type": "email", "operations": [{"operation": "a"}]},
            {"tool_type": "email", "operations": [{"operation": "b"}]},
        ]
    }
    with pytest.raises(ApiError) as exc_info:
        _parse_tool_registry(payload)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_parsed_tables_are_immutable() -> None:
    """注册表对象为冻结数据类，运行期不可被篡改。"""
    table = get_profile_routes()
    assert isinstance(table, ProfileRouteTable)
    registry = get_tool_registry()
    assert isinstance(registry, ToolRegistry)
    with pytest.raises(AttributeError):
        table.version = 2  # type: ignore[misc]
