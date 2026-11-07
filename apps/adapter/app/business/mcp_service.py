"""MCP server 业务服务（矩阵第 3 项，2026-11-06）。

职责只有两件：**登记校验、启用/白名单判定**。真正的网络/进程调用在
`app/services/mcp_runner.py`（`business` 层按依赖矩阵不得直接依赖 `clients`）。

安全边界在这里收口：

- 登记只允许运营控制台（`api/v1/ops_mcp.py` 做权限点校验），业务层再校验
  `OA_MCP_ENABLED`、租户（拒绝 `__platform__`）、传输档位、URL 形态与凭据引用格式；
- 执行只接受「插件步骤」传入的 `tenant_id + operation`，按 `tenant_scope` 读取，
  **不存在跨租户查询入口**；
- 凭据只以引用形式落库，解析走 `CredentialResolver.resolve_ref()`，明文只交给
  `McpTarget`（`repr=False`），不返回、不写日志。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit

from app.business.credential_service import CredentialResolver, get_credential_resolver
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.net_safety import UnsafeTargetError, ensure_allowlisted, is_blocked_ip, parse_ip
from app.core.security import OPS_TENANT_ID
from app.db.repositories.mcp_repo import (
    McpServerRecord,
    McpServerStore,
    get_mcp_server_store,
)

logger = get_logger(__name__)

#: 传输（只登记这两种；legacy SSE 在校验层拒绝）
TRANSPORT_HTTP: Final[str] = "http"
TRANSPORT_STDIO: Final[str] = "stdio"

#: 服务器名（插件 `operation` 的前半段）
SERVER_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

#: 工具名（MCP 允许 `_` / `-` / `.`）
TOOL_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: 凭据引用（只接受引用形态；这同时机械阻止把明文密钥写进这一列）
CREDENTIAL_REF_PATTERN: Final[re.Pattern[str]] = re.compile(r"^cred_[A-Za-z0-9]{6,64}$")

#: URL 中禁止出现的敏感查询参数（防止把凭据塞进 endpoint）
SENSITIVE_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"token", "api_key", "apikey", "key", "secret", "password", "credential", "sig", "signature"}
)

#: 单台服务器允许的工具数上限
MAX_ALLOWED_TOOLS: Final[int] = 50

#: stdio 启动参数个数与单参数长度上限
MAX_ARGS: Final[int] = 16
MAX_ARG_LENGTH: Final[int] = 256


@dataclass(frozen=True, slots=True)
class McpTarget:
    """一次受控调用需要的连接参数（凭据明文只活在这里）。

    Attributes:
        tenant_id: 租户 ID。
        server_id: 登记 ID。
        name: 服务器名。
        transport: `http` / `stdio`。
        endpoint: 地址或可执行文件路径。
        args: stdio 启动参数。
        credential: 已解密的凭据（**不进 repr / 日志 / 响应**）。
        timeout_ms: 单次总超时。
        connect_timeout_ms: 建连超时。
        max_output_bytes: 响应体上限。
        allow_insecure_http: 是否允许非 loopback 明文 http。
        allow_loopback: 是否放行回环（仅 development/test 且显式开关）。
        proxy_url: 出站代理（空 = 直连并做 DNS 钉扎）。
        allowed_tools: 工具白名单（运行期已复核）。
    """

    tenant_id: str
    server_id: str
    name: str
    transport: str
    endpoint: str
    args: tuple[str, ...] = ()
    credential: str = field(default="", repr=False)
    timeout_ms: int = 15_000
    connect_timeout_ms: int = 5_000
    max_output_bytes: int = 64 * 1024
    allow_insecure_http: bool = False
    allow_loopback: bool = False
    proxy_url: str = ""
    allowed_tools: tuple[str, ...] = ()


class McpServerService:
    """MCP server 登记与调用服务。"""

    def __init__(
        self,
        settings: Settings | None = None,
        store: McpServerStore | None = None,
        credentials: CredentialResolver | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 配置实例。
            store: 存储（缺省按 `OA_BUSINESS_STORE` 选择）。
            credentials: 凭据解析器。
        """
        self._settings = settings or get_settings()
        self._store = store or get_mcp_server_store(self._settings)
        self._credentials = credentials or get_credential_resolver(self._settings)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    async def list_servers(self, tenant_id: str) -> list[McpServerRecord]:
        """列出租户内登记。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[McpServerRecord]: 登记列表。
        """
        self._assert_tenant(tenant_id)
        return await self._store.list_servers(tenant_id)

    async def get_server(self, tenant_id: str, server_id: str) -> McpServerRecord:
        """读取单条登记。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            McpServerRecord: 登记。

        Raises:
            ApiError: 资源不存在（20004）。
        """
        self._assert_tenant(tenant_id)
        record = await self._store.get_server(tenant_id, server_id)
        if record is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                internal_detail=f"mcp server not found id={server_id}",
            )
        return record

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    async def register_server(self, tenant_id: str, payload: dict[str, Any]) -> McpServerRecord:
        """登记或更新一台 MCP server。

        Args:
            tenant_id: 目标租户（运营侧显式指定）。
            payload: `UpsertMcpServerRequest` 的字典形态。

        Returns:
            McpServerRecord: 落库后的登记。

        Raises:
            ApiError: 能力未开启（20003）或登记不合法（20001）。
        """
        self._require_enabled()
        self._assert_tenant(tenant_id)

        name = str(payload.get("name", "")).strip()
        if not SERVER_NAME_PATTERN.match(name):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="服务器名称只能用小写字母、数字、下划线或短横线，且以字母开头",
                internal_detail=f"invalid server name={name!r}",
            )

        transport = str(payload.get("transport", "")).strip()
        if transport == "sse":
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="暂不支持旧版 SSE 连接，请改用 streamable HTTP",
                internal_detail="legacy sse rejected",
            )
        if transport not in {TRANSPORT_HTTP, TRANSPORT_STDIO}:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="连接方式只能是 streamable HTTP 或本地连接",
                internal_detail=f"transport={transport!r}",
            )

        endpoint = str(payload.get("endpoint", "")).strip()
        args = self._validate_args(payload.get("args") or [])
        if transport == TRANSPORT_HTTP:
            self._validate_url(endpoint)
            args = ()
        else:
            self._validate_stdio(endpoint)

        credential_ref = str(payload.get("credential_ref", "")).strip()
        if credential_ref and not CREDENTIAL_REF_PATTERN.match(credential_ref):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="凭据必须使用平台登记的凭据编号，不能直接填写密钥",
                internal_detail="credential_ref format rejected",
            )

        allowed_tools = self._validate_allowed_tools(payload.get("allowed_tools") or [])

        enabled = bool(payload.get("enabled", False))
        timeout_ms = int(payload.get("timeout_ms") or self._settings.mcp_default_timeout_ms)
        if not 1_000 <= timeout_ms <= 120_000:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="单次超时必须在 1 秒到 120 秒之间",
                internal_detail=f"timeout_ms={timeout_ms}",
            )
        max_output_bytes = int(
            payload.get("max_output_bytes") or self._settings.mcp_max_output_bytes
        )
        if not 1_024 <= max_output_bytes <= 1_048_576:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="返回内容上限必须在 1KB 到 1MB 之间",
                internal_detail=f"max_output_bytes={max_output_bytes}",
            )

        requested_id = str(payload.get("server_id", "")).strip()
        existing = await self._store.get_server(tenant_id, requested_id) if requested_id else None
        if requested_id and existing is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                internal_detail=f"mcp server not found id={requested_id}",
            )
        if not requested_id:
            total = await self._store.count_servers(tenant_id)
            if total >= self._settings.mcp_max_servers_per_tenant:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message=(
                        f"每个企业最多登记 {self._settings.mcp_max_servers_per_tenant} 个外部服务"
                    ),
                    internal_detail=f"count={total}",
                )
            requested_id = f"mcp_{uuid.uuid4().hex[:24]}"

        by_name = await self._store.get_by_name(tenant_id, name)
        if by_name is not None and by_name.server_id != requested_id:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="这个名称已经被另一台外部服务使用",
                internal_detail=f"duplicate name={name}",
            )

        record = McpServerRecord(
            tenant_id=tenant_id,
            server_id=requested_id,
            name=name,
            transport=transport,
            endpoint=endpoint,
            args=args,
            credential_ref=credential_ref,
            allowed_tools=allowed_tools,
            enabled=enabled,
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
        )
        saved = await self._store.upsert_server(record)
        logger.info(
            "mcp_server_registered",
            server_id=saved.server_id,
            name=saved.name,
            transport=saved.transport,
            enabled=saved.enabled,
            tool_count=len(saved.allowed_tools),
            credential_configured=bool(saved.credential_ref),
        )
        return saved

    async def delete_server(self, tenant_id: str, server_id: str) -> bool:
        """删除登记。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            bool: 删除成功返回 True。

        Raises:
            ApiError: 资源不存在（20004）。
        """
        self._require_enabled()
        self._assert_tenant(tenant_id)
        deleted = await self._store.delete_server(tenant_id, server_id)
        if not deleted:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                internal_detail=f"mcp server not found id={server_id}",
            )
        logger.info("mcp_server_deleted", server_id=server_id)
        return True

    # ------------------------------------------------------------------
    # 探测 / 执行前的受控解析（调用方是 `services/mcp_runner.py`）
    # ------------------------------------------------------------------
    async def prepare_probe(self, tenant_id: str, server_id: str) -> McpTarget:
        """探测前的校验与凭据解析。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            McpTarget: 连接参数（含已解密的凭据）。

        Raises:
            ApiError: 能力未开启（20003）或资源不存在（20004）。
        """
        self._require_enabled()
        record = await self.get_server(tenant_id, server_id)
        return await self._target(record)

    async def prepare_execute(self, tenant_id: str, operation: str) -> tuple[McpTarget, str]:
        """执行前的启用态/白名单复核与凭据解析。

        Args:
            tenant_id: 租户 ID（来自已鉴权任务上下文）。
            operation: `<server_name>.<tool_name>`。

        Returns:
            tuple[McpTarget, str]: 连接参数与工具名。

        Raises:
            ApiError: 能力未开启 / 服务器未登记或未启用 / 工具不在白名单 / 凭据不可用。
        """
        self._require_enabled()
        self._assert_tenant(tenant_id)
        server_name, tool_name = self._split_operation(operation)
        record = await self._store.get_by_name(tenant_id, server_name)
        if record is None or not record.enabled:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED,
                message="这个外部工具还没有准备好，请稍后再试",
                internal_detail=f"server={server_name} enabled=false",
            )
        if tool_name not in record.allowed_tools:
            raise ApiError(
                ErrorCode.TOOL_OPERATION_UNSUPPORTED,
                message="这个外部工具不支持该操作",
                internal_detail=f"server={server_name} tool={tool_name} not allowlisted",
            )
        return await self._target(record), tool_name

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _require_enabled(self) -> None:
        """确认能力开关已打开。

        Raises:
            ApiError: 未开启（20003）。
        """
        if not self._settings.mcp_enabled:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                message="当前环境尚未开启外部工具能力",
                internal_detail="mcp disabled",
            )

    def _assert_tenant(self, tenant_id: str) -> None:
        """确认目标租户合法（不接受平台哨兵）。

        Args:
            tenant_id: 租户 ID。

        Raises:
            ApiError: 租户为空或为平台哨兵（20001）。
        """
        if not tenant_id or tenant_id == OPS_TENANT_ID:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请指定具体企业",
                internal_detail=f"invalid tenant={tenant_id!r}",
            )

    def _validate_args(self, raw: Any) -> tuple[str, ...]:
        """校验 stdio 启动参数。

        Args:
            raw: 原始参数列表。

        Returns:
            tuple[str, ...]: 规范后的参数。

        Raises:
            ApiError: 参数过多/过长/非字符串（20001）。
        """
        if not isinstance(raw, list | tuple):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="启动内容格式不正确",
                internal_detail="args not a list",
            )
        if len(raw) > MAX_ARGS:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="启动内容过多",
                internal_detail=f"args count={len(raw)}",
            )
        values: list[str] = []
        for item in raw:
            text = str(item)
            if len(text) > MAX_ARG_LENGTH:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message="启动内容过长",
                    internal_detail="arg too long",
                )
            values.append(text)
        return tuple(values)

    def _validate_url(self, endpoint: str) -> None:
        """校验 streamable HTTP 地址。

        Args:
            endpoint: URL。

        Raises:
            ApiError: 形态不合法/含凭据/非 https（20001）。
        """
        if not endpoint or len(endpoint) > 2048:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请填写有效的服务地址",
                internal_detail="endpoint empty or too long",
            )
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="服务地址必须以 http:// 或 https:// 开头",
                internal_detail="invalid endpoint scheme",
            )
        if parts.username or parts.password:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请不要把账号密码写在地址里，凭据请用平台凭据编号",
                internal_detail="endpoint contains userinfo",
            )
        for key, _value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message="请不要把密钥写在地址里，凭据请用平台凭据编号",
                    internal_detail=f"endpoint contains sensitive query key={key}",
                )
        if parts.fragment:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="服务地址不能带片段标识",
                internal_detail="endpoint contains fragment",
            )

        host = parts.hostname or ""
        # H-02(a)：白名单默认拒绝。运营账号不能"先登记再探测"未声明的域。
        try:
            ensure_allowlisted(host, self._allowed_hosts())
        except UnsafeTargetError as exc:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="该服务地址不在允许范围内，请由管理员先声明目标域",
                internal_detail=f"mcp host rejected: {exc.reason}",
            ) from exc

        # H-02(a)：字面量 IP / localhost 先判内网段。`localhost` 解析后才暴露，
        # 这里提前拦，错误更直接（且不依赖 DNS 可用性）。
        literal = parse_ip(host)
        is_local_name = host.lower() == "localhost" or host.lower().endswith(".localhost")
        blocked = (literal is not None and is_blocked_ip(literal)) or is_local_name
        if blocked and not (
            self._loopback_permitted() and (literal is None or literal.is_loopback)
        ):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="服务地址不能指向内网或保留地址",
                internal_detail=f"blocked endpoint host={host!r}",
            )
        loopback = is_local_name or (literal is not None and literal.is_loopback)
        if parts.scheme == "http" and not loopback and not self._settings.mcp_allow_insecure_http:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="外部服务地址必须使用 https",
                internal_detail="insecure http rejected",
            )

    def _allowed_hosts(self) -> tuple[str, ...]:
        """解析目标白名单（逗号分隔；空串 = 空元组 = 全拒绝）。"""
        raw = self._settings.mcp_allowed_hosts
        return tuple(item.strip() for item in raw.split(",") if item.strip())

    def _loopback_permitted(self) -> bool:
        """回环是否可放行：显式开关且非 staging/production。"""
        return self._settings.mcp_allow_loopback and self._settings.env not in {
            "staging",
            "production",
        }

    def _validate_stdio(self, endpoint: str) -> None:
        """校验本地连接（默认关；生产/预发一律拒绝）。

        Args:
            endpoint: 可执行文件路径。

        Raises:
            ApiError: 未开启/环境不允许/不在白名单（20001）。
        """
        if not self._settings.mcp_allow_stdio:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="当前环境未开启本地连接",
                internal_detail="stdio disabled",
            )
        if self._settings.env in {"staging", "production"}:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="线上环境不允许启动本地进程",
                internal_detail="stdio forbidden in production",
            )
        if not endpoint or len(endpoint) > 512:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请填写本地服务路径",
                internal_detail="stdio endpoint empty or too long",
            )
        allowed = [
            item.strip()
            for item in self._settings.mcp_stdio_allowed_commands.split(",")
            if item.strip()
        ]
        if not allowed:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="本地连接未配置可执行文件白名单",
                internal_detail="stdio allowlist empty",
            )
        basename = endpoint.replace("\\", "/").rsplit("/", 1)[-1]
        if not any(
            endpoint == item or basename == item.replace("\\", "/").rsplit("/", 1)[-1]
            for item in allowed
        ):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="这个本地程序不在允许范围内",
                internal_detail="stdio command not allowlisted",
            )

    def _validate_allowed_tools(self, raw: Any) -> tuple[str, ...]:
        """校验工具白名单。

        Args:
            raw: 原始工具名列表。

        Returns:
            tuple[str, ...]: 去重后的白名单。

        Raises:
            ApiError: 为空/过多/名称非法（20001）。
        """
        if not isinstance(raw, list | tuple) or not raw:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="请至少允许一个工具",
                internal_detail="allowed_tools empty",
            )
        if len(raw) > MAX_ALLOWED_TOOLS:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=f"最多允许 {MAX_ALLOWED_TOOLS} 个工具",
                internal_detail=f"allowed_tools count={len(raw)}",
            )
        tools: list[str] = []
        for item in raw:
            name = str(item).strip()
            if not TOOL_NAME_PATTERN.match(name):
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message="工具名称格式不正确",
                    internal_detail=f"invalid tool name={name!r}",
                )
            if name not in tools:
                tools.append(name)
        return tuple(tools)

    def _split_operation(self, operation: str) -> tuple[str, str]:
        """拆分 `<server>.<tool>`。

        Args:
            operation: 动作标识。

        Returns:
            tuple[str, str]: 服务器名与工具名。

        Raises:
            ApiError: 形态非法（20001）。
        """
        server_name, sep, tool_name = operation.partition(".")
        if not sep or not SERVER_NAME_PATTERN.match(server_name):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="外部工具动作标识不正确",
                internal_detail=f"operation={operation!r}",
            )
        if not TOOL_NAME_PATTERN.match(tool_name):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="外部工具名称不正确",
                internal_detail=f"operation={operation!r}",
            )
        return server_name, tool_name

    async def _target(self, record: McpServerRecord) -> McpTarget:
        """把登记解析成连接参数（这里才解密凭据）。

        Args:
            record: 登记。

        Returns:
            McpTarget: 连接参数。

        Raises:
            ApiError: 凭据不可用（11007/30004）。
        """
        credential = ""
        if record.credential_ref:
            credential = await self._credentials.resolve_ref(
                record.tenant_id, record.credential_ref
            )
        if record.transport == TRANSPORT_HTTP:
            # H-02：登记后配置可能已收紧（白名单/内网判定），执行前必须复核，
            # 不能"登记时合法 → 永远合法"。
            self._validate_url(record.endpoint)
        return McpTarget(
            tenant_id=record.tenant_id,
            server_id=record.server_id,
            name=record.name,
            transport=record.transport,
            endpoint=record.endpoint,
            args=record.args,
            credential=credential,
            timeout_ms=record.timeout_ms,
            connect_timeout_ms=self._settings.mcp_connect_timeout_ms,
            max_output_bytes=record.max_output_bytes,
            allow_insecure_http=self._settings.mcp_allow_insecure_http,
            allow_loopback=self._loopback_permitted(),
            proxy_url=self._settings.ssrf_proxy_url,
            allowed_tools=record.allowed_tools,
        )


__all__: list[str] = [
    "MAX_ALLOWED_TOOLS",
    "SERVER_NAME_PATTERN",
    "TOOL_NAME_PATTERN",
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "McpServerService",
    "McpTarget",
]
