"""环境变量与配置模型。

设计要点（架构文档 9.5 环境变量规范）：
- 所有环境变量统一使用大写 + `OA_` 前缀；
- 配置项通过 Pydantic Settings 强类型化，缺失必需项在启动时即暴露（错误码 30004）；
- 部署模式（`OA_DEPLOY_MODE`）是双模式切换的唯一开关，代码路径完全一致。

下游（底座 / IAM）连接配置仅在本模块出现，禁止外泄到上层。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import Request
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DeployMode = Literal["saas", "private"]
AuthMode = Literal["oidc", "stub"]
LogFormat = Literal["json", "console"]
IdempotencyBackend = Literal["memory", "redis"]


class Settings(BaseSettings):
    """适配层全局配置。

    所有字段均可用 `OA_` 前缀环境变量覆盖，例如 `OA_DIFY_BASE_URL`。
    """

    model_config = SettingsConfigDict(
        env_prefix="OA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # 运行环境
    # ------------------------------------------------------------------
    env: Literal["development", "test", "staging", "production"] = "development"
    deploy_mode: DeployMode = "saas"
    app_name: str = "officeai-adapter"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"  # noqa: S104 - 容器内监听所有网卡，由编排层暴露
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = False

    # ------------------------------------------------------------------
    # 日志（架构文档 9.3）
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: LogFormat = "json"

    # ------------------------------------------------------------------
    # 鉴权（架构文档 3.0.2）
    # ------------------------------------------------------------------
    auth_mode: AuthMode = "stub"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "officeai-adapter"
    jwt_issuer: str = "officeai"
    default_tenant_id: str = "default"

    # ------------------------------------------------------------------
    # 底座（Dify）连接配置 —— 仅适配层可见
    # ------------------------------------------------------------------
    downstream_enabled: bool = False
    dify_base_url: str = "http://dify-api:5001"
    dify_default_app_key: str = ""
    # Agent 任务专用工作流应用密钥（缺省回退 dify_default_app_key）；
    # 会话（chat）与任务（workflow）在底座侧是两类应用，密钥必须可分离
    dify_agent_app_key: str = ""
    dify_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    dify_read_timeout_seconds: float = Field(default=60.0, gt=0)
    dify_stream_first_byte_timeout_seconds: float = Field(default=15.0, gt=0)
    dify_max_retries: int = Field(default=2, ge=0, le=5)
    dify_retry_backoff_ms: int = Field(default=200, ge=0)

    # ------------------------------------------------------------------
    # 身份底座（Keycloak）连接配置 —— 仅适配层可见
    # ------------------------------------------------------------------
    keycloak_base_url: str = "http://keycloak:8080"
    keycloak_realm: str = "officeai"
    keycloak_client_id: str = "officeai-adapter"
    keycloak_client_secret: str = ""
    keycloak_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    keycloak_read_timeout_seconds: float = Field(default=5.0, gt=0)

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    database_url: str = ""
    milvus_uri: str = ""
    # 对象存储连接（SeaweedFS，S3 兼容；2026-10-21 由 minio_* 统一改名，
    # 消除「名为 MinIO 实为 SeaweedFS」的指代漂移，见 QA 附录 A P2-4）
    object_store_endpoint: str = ""
    object_store_access_key: str = ""
    object_store_secret_key: str = ""
    object_store_bucket: str = "officeai"

    # ------------------------------------------------------------------
    # 幂等与限流（架构文档 3.0.4）
    # ------------------------------------------------------------------
    idempotency_backend: IdempotencyBackend = "memory"
    idempotency_ttl_seconds: int = Field(default=86_400, ge=60)
    ratelimit_enabled: bool = True
    ratelimit_default_per_minute: int = Field(default=120, ge=1)

    # ------------------------------------------------------------------
    # 上传限制（错误码 20008 / 11003）
    # ------------------------------------------------------------------
    max_upload_bytes: int = Field(default=52_428_800, ge=1)  # 50MB
    max_document_chars: int = Field(default=5_000_000, ge=1)

    # ------------------------------------------------------------------
    # 校验器
    # ------------------------------------------------------------------
    @field_validator("api_prefix")
    @classmethod
    def _normalize_api_prefix(cls, value: str) -> str:
        """确保 API 前缀以斜杠开头且不以斜杠结尾。"""
        normalized = "/" + value.strip().strip("/")
        return normalized

    @property
    def is_private_mode(self) -> bool:
        """是否为私有化部署模式（单租户，默认租户 `default`）。"""
        return self.deploy_mode == "private"

    @property
    def mock_mode(self) -> bool:
        """是否处于桩模式（未开启真实下游调用）。

        T01 阶段恒为 True：接口契约与错误码可验证，但不产生真实底座请求。
        """
        return not self.downstream_enabled


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例（带缓存）。

    仅作为**兜底**使用：请求链路上的配置一律从 `app.state.settings` 读取
    （见 `get_request_settings`），避免注入式构造应用时配置失效。

    Returns:
        Settings: 进程内唯一的配置实例。
    """
    return Settings()


def reset_settings_cache() -> None:
    """清空配置缓存，供测试与热加载使用。"""
    get_settings.cache_clear()


def get_request_settings(request: Request) -> Settings:
    """获取当前请求生效的配置。

    优先级：`app.state.settings`（`create_app(settings=...)` 注入）> 全局单例。

    **根因修复（QA BUG-01 / BUG-02）**：此前鉴权与租户链路直接读取全局
    `get_settings()` 单例，导致 `create_app(settings=...)` 注入的配置被忽略——
    `auth_mode=oidc` 会被降级为 stub（伪造令牌可登录），
    `deploy_mode=private` 的单租户隔离也会失效。

    Args:
        request: FastAPI 请求对象。

    Returns:
        Settings: 当前请求生效的配置。
    """
    app_settings = getattr(request.app.state, "settings", None)
    if isinstance(app_settings, Settings):
        return app_settings
    return get_settings()
