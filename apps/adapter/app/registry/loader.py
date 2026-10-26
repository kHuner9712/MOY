"""注册表加载器：把 YAML 配置读成强类型对象并做启动期校验。

职责：
- 加载 `profile_routes.yaml`（能力档位 → 底座应用路由表）；
- 加载 `tool_registry.yaml`（工具类型注册表，Q1 数据驱动）；
- 校验配置合法性，缺失或非法时抛 30004（配置异常），**启动即暴露**。

安全约束：YAML 中只登记环境变量名，禁止写入任何真实密钥。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 注册表目录
REGISTRY_DIR: Final[Path] = Path(__file__).resolve().parent

#: 能力档位路由表文件名
PROFILE_ROUTES_FILE: Final[str] = "profile_routes.yaml"

#: 工具类型注册表文件名
TOOL_REGISTRY_FILE: Final[str] = "tool_registry.yaml"

#: 合法的能力档位枚举（与架构文档 3.1.1 契约一致）
VALID_PROFILES: Final[frozenset[str]] = frozenset({"general", "code", "long_context", "reasoning"})

#: 合法的工具分类枚举
VALID_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"communication", "development", "document", "schedule", "data"}
)

#: 合法的授权方式枚举
VALID_AUTH_TYPES: Final[frozenset[str]] = frozenset({"oauth2", "token", "none"})

#: 匹配提示的字数范围与条数上限。
#:
#: 与 `app.plugins.schema.MATCH_HINT_*` **同口径**（P2-13）。这里用字面量
#: 而不是 import：G-37 允许矩阵里 `registry` 只能依赖 `core`，反向依赖
#: `plugins` 会制造一条新的方向倒挂；三处整数的口径由
#: `tests/unit/test_match_hints.py` 统一钉住，漂移会立刻变红。
MATCH_HINT_MIN_LEN: Final[int] = 2
MATCH_HINT_MAX_LEN: Final[int] = 8
MATCH_HINT_MAX_COUNT: Final[int] = 12


def _load_yaml(path: Path) -> dict[str, Any]:
    """读取并解析 YAML 文件。

    Args:
        path: YAML 文件路径。

    Returns:
        dict[str, Any]: 解析后的顶层映射。

    Raises:
        ApiError: 文件缺失或解析失败（30004 配置异常）。
    """
    if not path.is_file():
        raise ApiError(
            ErrorCode.CONFIG_ERROR, internal_detail=f"registry file missing: {path.name}"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ApiError(
            ErrorCode.CONFIG_ERROR, internal_detail=f"registry yaml invalid: {path.name}"
        ) from exc
    if not isinstance(raw, dict):
        raise ApiError(
            ErrorCode.CONFIG_ERROR, internal_detail=f"registry yaml root not a mapping: {path.name}"
        )
    return raw


@dataclass(frozen=True, slots=True)
class ProfileRoute:
    """单个能力档位路由配置。

    Attributes:
        profile: 能力档位枚举值。
        display_name: 业务化展示名。
        description: 业务化描述。
        max_context_turns: 最大上下文轮数。
        status: 档位状态。
        default_app_env: 主路由对应的环境变量名。
        fallback_app_env: 降级链环境变量名列表。
    """

    profile: str
    display_name: str
    description: str
    max_context_turns: int
    status: str
    default_app_env: str
    fallback_app_env: tuple[str, ...] = ()

    def resolve_app_key(self, env: dict[str, str] | None = None) -> str:
        """按主路由 → 降级链顺序解析应用密钥。

        Args:
            env: 环境变量映射，缺省读取 `os.environ`。

        Returns:
            str: 首个可用的应用密钥；均未配置时返回空串。
        """
        import os

        source = env if env is not None else dict(os.environ)
        for name in (self.default_app_env, *self.fallback_app_env):
            value = source.get(name, "")
            if value:
                return value
        return ""


@dataclass(frozen=True, slots=True)
class RetrievalDefaults:
    """检索默认策略（附录 A-3：不对外暴露）。"""

    top_k: int = 5
    score_threshold: float = 0.5
    search_method: str = "hybrid_search"


@dataclass(frozen=True, slots=True)
class ProfileRouteTable:
    """能力档位路由表。

    Attributes:
        version: 配置版本号。
        routes: 档位路由配置列表。
        retrieval_defaults: 检索默认策略。
        chunk_policy_defaults: 分块策略默认值。
        allowed_document_extensions: 允许上传的文档扩展名白名单。
    """

    version: int
    routes: tuple[ProfileRoute, ...]
    retrieval_defaults: RetrievalDefaults = field(default_factory=RetrievalDefaults)
    chunk_policy_defaults: dict[str, dict[str, int]] = field(default_factory=dict)
    allowed_document_extensions: tuple[str, ...] = ()

    def get(self, profile: str) -> ProfileRoute | None:
        """按档位查询路由配置。

        Args:
            profile: 能力档位枚举值。

        Returns:
            ProfileRoute | None: 命中的配置；未命中返回 `None`。
        """
        for route in self.routes:
            if route.profile == profile:
                return route
        return None

    @property
    def profiles(self) -> tuple[str, ...]:
        """全部已登记档位。"""
        return tuple(route.profile for route in self.routes)


@dataclass(frozen=True, slots=True)
class ToolSpecModel:
    """工具类型注册条目。

    Attributes:
        tool_type: 工具类型标识。
        display_name: 业务化展示名。
        category: 业务化分类。
        auth_type: 授权方式。
        status: 可用状态。
        timeout_ms: 超时声明（毫秒）。
        connector_impl: 连接器实现标识。
        operations: 能力清单（operation → params_schema）。
        match_hints: 目标匹配提示（goal_router 打分用的领域词，可缺省）。
    """

    tool_type: str
    display_name: str
    category: str
    auth_type: str
    status: str
    timeout_ms: int
    connector_impl: str
    operations: tuple[dict[str, Any], ...]
    match_hints: tuple[str, ...] = ()

    def operation_names(self) -> tuple[str, ...]:
        """全部操作标识。"""
        return tuple(str(op.get("operation", "")) for op in self.operations)

    def has_operation(self, operation: str) -> bool:
        """判断操作是否在能力清单中。

        Args:
            operation: 操作标识。

        Returns:
            bool: 存在返回 True。
        """
        return operation in self.operation_names()


@dataclass(frozen=True, slots=True)
class ToolRegistry:
    """工具类型注册表。

    Attributes:
        version: 配置版本号。
        tools: 工具条目列表。
    """

    version: int
    tools: tuple[ToolSpecModel, ...]

    def get(self, tool_type: str) -> ToolSpecModel | None:
        """按工具类型查询注册条目。

        Args:
            tool_type: 工具类型标识。

        Returns:
            ToolSpecModel | None: 命中的条目；未命中返回 `None`。
        """
        for spec in self.tools:
            if spec.tool_type == tool_type:
                return spec
        return None

    @property
    def tool_types(self) -> tuple[str, ...]:
        """全部已登记工具类型。"""
        return tuple(spec.tool_type for spec in self.tools)


def _parse_profile_routes(raw: dict[str, Any]) -> ProfileRouteTable:
    """解析档位路由表 YAML。

    Args:
        raw: YAML 顶层映射。

    Returns:
        ProfileRouteTable: 强类型路由表。

    Raises:
        ApiError: 配置非法（30004）。
    """
    entries = raw.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="profile_routes.profiles empty")

    routes: list[ProfileRoute] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="profile entry not a mapping")
        profile = str(entry.get("profile", "")).strip()
        if profile not in VALID_PROFILES:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"unknown model_profile: {profile}"
            )
        if profile in seen:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"duplicated model_profile: {profile}"
            )
        seen.add(profile)
        fallback_raw = entry.get("fallback_app_env") or []
        routes.append(
            ProfileRoute(
                profile=profile,
                display_name=str(entry.get("display_name", "")),
                description=str(entry.get("description", "")),
                max_context_turns=int(entry.get("max_context_turns", 20)),
                status=str(entry.get("status", "available")),
                default_app_env=str(entry.get("default_app_env", "")),
                fallback_app_env=tuple(str(item) for item in fallback_raw),
            )
        )

    retrieval_raw = raw.get("retrieval_defaults") or {}
    chunk_raw = raw.get("chunk_policy_defaults") or {}
    extensions_raw = raw.get("allowed_document_extensions") or []

    return ProfileRouteTable(
        version=int(raw.get("version", 1)),
        routes=tuple(routes),
        retrieval_defaults=RetrievalDefaults(
            top_k=int(retrieval_raw.get("top_k", 5)),
            score_threshold=float(retrieval_raw.get("score_threshold", 0.5)),
            search_method=str(retrieval_raw.get("search_method", "hybrid_search")),
        ),
        chunk_policy_defaults={
            str(name): {str(k): int(v) for k, v in (params or {}).items()}
            for name, params in chunk_raw.items()
        },
        allowed_document_extensions=tuple(str(item).lower() for item in extensions_raw),
    )


def _parse_match_hints(raw: object, *, owner: str) -> tuple[str, ...]:
    """解析并校验匹配提示（2-8 字、去重、≤12 条）。

    Args:
        raw: YAML 里的 `match_hints` 值（缺失/None 视为空元组）。
        owner: 归属标识（报错定位用，如工具类型）。

    Returns:
        tuple[str, ...]: 匹配提示元组。

    Raises:
        ApiError: 形态不合法（30004）。
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail=f"match_hints not a list: {owner}")
    if len(raw) > MATCH_HINT_MAX_COUNT:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=f"match_hints too many ({len(raw)}>{MATCH_HINT_MAX_COUNT}): {owner}",
        )
    hints: list[str] = []
    for item in raw:
        hint = str(item).strip()
        if not (MATCH_HINT_MIN_LEN <= len(hint) <= MATCH_HINT_MAX_LEN):
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    f"match_hint length {len(hint)} "
                    f"not in {MATCH_HINT_MIN_LEN}..{MATCH_HINT_MAX_LEN}: {owner}"
                ),
            )
        if hint in hints:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"duplicated match_hint: {owner}"
            )
        hints.append(hint)
    return tuple(hints)


def _parse_tool_registry(raw: dict[str, Any]) -> ToolRegistry:
    """解析工具类型注册表 YAML。

    Args:
        raw: YAML 顶层映射。

    Returns:
        ToolRegistry: 强类型注册表。

    Raises:
        ApiError: 配置非法（30004）。
    """
    entries = raw.get("tools")
    if not isinstance(entries, list) or not entries:
        raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="tool_registry.tools empty")

    tools: list[ToolSpecModel] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="tool entry not a mapping")
        tool_type = str(entry.get("tool_type", "")).strip()
        if not tool_type:
            raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="tool_type missing")
        if tool_type in seen:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"duplicated tool_type: {tool_type}"
            )
        seen.add(tool_type)
        category = str(entry.get("category", "data"))
        if category not in VALID_CATEGORIES:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"unknown tool category: {category}"
            )
        auth_type = str(entry.get("auth_type", "oauth2"))
        if auth_type not in VALID_AUTH_TYPES:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"unknown tool auth_type: {auth_type}"
            )
        operations_raw = entry.get("operations") or []
        if not isinstance(operations_raw, list) or not operations_raw:
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"tool operations empty: {tool_type}"
            )
        operations = tuple(
            op for op in operations_raw if isinstance(op, dict) and op.get("operation")
        )
        if len(operations) != len(operations_raw):
            raise ApiError(
                ErrorCode.CONFIG_ERROR, internal_detail=f"invalid tool operation: {tool_type}"
            )
        tools.append(
            ToolSpecModel(
                tool_type=tool_type,
                display_name=str(entry.get("display_name", "")),
                category=category,
                auth_type=auth_type,
                status=str(entry.get("status", "available")),
                timeout_ms=int(entry.get("timeout_ms", 10_000)),
                connector_impl=str(entry.get("connector_impl", "")),
                operations=operations,
                match_hints=_parse_match_hints(entry.get("match_hints"), owner=tool_type),
            )
        )

    return ToolRegistry(version=int(raw.get("version", 1)), tools=tuple(tools))


@lru_cache(maxsize=1)
def get_profile_routes() -> ProfileRouteTable:
    """加载能力档位路由表（进程内缓存）。

    Returns:
        ProfileRouteTable: 强类型路由表。

    Raises:
        ApiError: 配置缺失或非法（30004）。
    """
    table = _parse_profile_routes(_load_yaml(REGISTRY_DIR / PROFILE_ROUTES_FILE))
    logger.info("profile_routes_loaded", profiles=list(table.profiles), version=table.version)
    return table


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """加载工具类型注册表（进程内缓存）。

    Returns:
        ToolRegistry: 强类型注册表。

    Raises:
        ApiError: 配置缺失或非法（30004）。
    """
    registry = _parse_tool_registry(_load_yaml(REGISTRY_DIR / TOOL_REGISTRY_FILE))
    logger.info(
        "tool_registry_loaded", tool_types=list(registry.tool_types), version=registry.version
    )
    return registry


def reset_registry_cache() -> None:
    """清空注册表缓存，供测试与配置热加载使用。"""
    get_profile_routes.cache_clear()
    get_tool_registry.cache_clear()
