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
#: 业务数据存储后端（D-02）。`memory` 仅允许 development/test；
#: `postgres` 为生产唯一允许值（staging/production 置 memory 启动即失败）。
BusinessStore = Literal["memory", "postgres"]
#: 凭据解析模式（D-10）。`bound` = 按库凭据优先；`global` = 应急强制走全局凭据。
CredentialMode = Literal["bound", "global"]
#: 支付渠道（B09）。`wechat_pay` = 微信支付；`fake` = 测试用假网关（不产生真实请求）。
PayChannel = Literal["wechat_pay", "fake"]


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
    # ------------------------------------------------------------------
    # 档位路由的 4 个底座应用密钥（阶段四 P4-10 / 架构 §6.3）
    #
    # 变量名与 `app/registry/profile_routes.yaml` 的 `default_app_env` **逐字对应**
    # （`OA_DIFY_CODE_APP_KEY` / `..._LONG_CONTEXT_APP_KEY` / `..._REASONING_APP_KEY` /
    # `..._FALLBACK_APP_KEY`）。此前这 4 个只在 yaml 里被引用、Settings 里没有声明，
    # 靠 `registry/loader.py:106-113` 直接读 `os.environ` 兜住——结果是：
    # 契约测试无从断言"4 档 4 个 key"，而漏配一个 key 只会在运行时静默降级到默认档。
    # 缺省空串 = 该档不可用（`resolve_app_key()` 返回空 → M3 目录 `status=unavailable`）。
    # ------------------------------------------------------------------
    dify_code_app_key: str = ""
    dify_long_context_app_key: str = ""
    dify_reasoning_app_key: str = ""
    #: 降级链末端密钥（通用档 fallback；四个档位都可能落到它）
    dify_fallback_app_key: str = ""
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
    # 业务库（阶段二 D-02，架构文档 2.1 / 11.5）
    #
    # 连接池容量口径：副本数 × (pool_size + max_overflow) ≤ PG max_connections × 0.6。
    # `business_store=memory` 时**完全不创建**异步引擎（不产生任何连接尝试），
    # 保证既有的 421 个用例在无 PostgreSQL 环境下也能全绿。
    # ------------------------------------------------------------------
    business_store: BusinessStore = "memory"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0)
    db_auto_migrate: bool = False
    #: 迁移 SQL 目录覆盖项（缺省按「容器 /app/deploy」→「仓库 deploy」自动发现）
    db_sql_dir: str = ""

    # ------------------------------------------------------------------
    # per-kb 凭据加密（阶段二 D-10，架构文档 2.3 / 11.5）
    #
    # `credential_encryption_key` 为 32 字节密钥的 base64；
    # `credential_encryption_keys_legacy` 为轮换期旧密钥，格式 `1:base64,2:base64`
    # （数字为 `credential_store.key_version`）。缺失时**不静默降级**：
    # staging/production/business_store=postgres 启动即失败（30004）。
    # ------------------------------------------------------------------
    credential_encryption_key: str = ""
    credential_encryption_keys_legacy: str = ""
    credential_mode: CredentialMode = "bound"
    credential_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    #: 知识库元数据（`base_id -> credential_ref`）本地缓存 TTL（架构文档 2.3 第 2 条 30s）
    knowledge_base_cache_ttl_seconds: int = Field(default=30, ge=0, le=3_600)

    # ------------------------------------------------------------------
    # 片段稳定映射与引用回链（阶段二 D-11，架构文档 2.4 / 11.5）
    #
    # `segment_backfill_enabled=false` 是 D-11 的回滚开关：关闭后引用回填不写库，
    # 接口返回的 `segment_id` 退化为空串（前端降级为「只展示片段内容」）。
    # `segment_map_retention_days` 为 stale 行的保留期，过期由每日清理任务物理删除。
    # ------------------------------------------------------------------
    segment_backfill_enabled: bool = True
    segment_map_retention_days: int = Field(default=90, ge=1, le=3_650)

    # ------------------------------------------------------------------
    # 可见性判定缓存（阶段二 D-11 / PRD P2-KB-03 验收 2：授权变更 ≤ 5s 生效）
    #
    # 缓存只存「某个用户在某个租户下可见的知识库集合与授权级别」，
    # TTL 上限 5s：授权/成员关系变更后最迟 5s 自然失效，无需依赖跨进程广播。
    # ------------------------------------------------------------------
    permission_cache_ttl_seconds: float = Field(default=5.0, ge=0.0, le=300.0)

    # ------------------------------------------------------------------
    # 计量（阶段二 D-12，架构文档 2.5 / 11.5）
    #
    # `usage_write_enabled` 是 D-12 的回滚开关：置 false 后不再写新事件与聚合，
    # M4 / BL5 回落到「读 usage_monthly 现有行」（只读历史聚合）；
    # 折算参数集中在此，**不改代码调参**（架构文档 2.5 第 3 条）。
    # ------------------------------------------------------------------
    usage_write_enabled: bool = True
    #: 每个额度单位对应的字符数（中文 1 字 ≈ 1 字数额度）
    usage_chars_per_unit: int = Field(default=1, ge=1)
    #: 流式中断且下游未回传 prompt 用量时的兜底输入额度（架构文档 2.5 第 3 条）
    usage_estimate_prompt_units: int = Field(default=800, ge=0)

    # ------------------------------------------------------------------
    # 计费与支付（阶段二 B09，架构文档 §4.5 / 11.5）
    #
    # 设计口径：
    # 1. **密钥缺失不阻断启动**：`WechatPayGateway` 在构造时不校验密钥，
    #    只在真正发起支付/验签时抛 `30004`（配置异常，业务语言文案）；
    #    staging/production 缺密钥属部署事故，但不应让整个服务起不来
    #    （否则「账单页打不开」会扩散成「全站不可用」）。
    # 2. `pay_channel=fake` 仅用于测试与本地联调；`mock_mode`（未开启真实下游）
    #    且 env 为 development/test 时自动回落 fake 网关并打 WARNING——
    #    与既有「桩模式」哲学一致；真实环境缺密钥一律报错，绝不静默降级。
    # 3. `order_expire_minutes` 是「未支付自动关闭」的唯一口径（BL6 验收 ⑦）。
    # ------------------------------------------------------------------
    pay_channel: PayChannel = "wechat_pay"
    pay_mch_id: str = ""
    pay_api_v3_key: str = ""
    pay_cert_serial: str = ""
    pay_private_key_path: str = ""
    pay_notify_url: str = ""
    #: 对公转账收款信息（JSON 字符串，原样下发给前端展示；不含任何凭据）
    pay_bank_info: str = ""
    #: 订单未支付自动关闭的分钟数（BL6）
    order_expire_minutes: int = Field(default=30, ge=1, le=1440)

    # ------------------------------------------------------------------
    # 插件任务引擎（阶段三 B19，架构文档 §3.3 / §3.5）
    #
    # `plugin_task_concurrency`：**同租户同插件**的在跑任务上限（PRD E-12）。
    # 第 N+1 个任务不报错、不排队到 4xx，而是 `200 + status=queued +
    # queue_position`——4xx 会被前端 `unwrap()` 渲染成整屏报错，用户会以为
    # 任务没创建成功而反复重试，反而制造更多排队任务。
    # `plugin_task_timeout_seconds`：任务级超时（PRD P0-P3-02 默认 900s）。
    # `plugin_artifact_retention_days`：产物留存期（Q3-5，默认 180 天），
    # 过期后 `download_url=null` 且前端提示「产物已过期，可重新发起任务」。
    # `plugin_retry_backoff_ms`：`on_failure: retry` 的退避（最多 1 次，§3.5）。
    # ------------------------------------------------------------------
    plugin_task_concurrency: int = Field(default=3, ge=1, le=20)
    plugin_task_timeout_seconds: int = Field(default=900, ge=30, le=86_400)
    plugin_artifact_retention_days: int = Field(default=180, ge=1, le=3_650)
    #: 产物下载令牌有效期（秒）。默认 900（15 分钟），上限 86400：
    #: 链接是短期凭证不是资源标识，泄漏后可自然失效（S9，2026-10-23）。
    artifact_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    plugin_retry_backoff_ms: int = Field(default=800, ge=0, le=5_000)
    #: 产物下载地址前缀（平台代理，**不暴露对象存储地址**，§3.5）
    plugin_artifact_download_prefix: str = "/api/v1/agent/artifacts"

    # ------------------------------------------------------------------
    # 数据保留期与记忆（阶段四 §3.5 口径表）
    #
    # 全部可配、**不得写死在代码里**：保留期是合规条款的一部分（私有化客户合同里
    # 会写具体天数），而"改一个天数要重新发版"是没人愿意做的操作。
    # `retention_*` 的消费者是后台任务 `retention_cleanup`（T4-05.3）；
    # `memory_*` 的消费者是记忆抽取与召回（T4-03）。
    # ------------------------------------------------------------------
    #: 审计日志保留天数（**DBA 执行**：应用角色对 audit_log 无 DELETE 权限，见 F-9）
    retention_audit_days: int = Field(default=180, ge=1, le=3_650)
    #: 用量事件保留月数（13 = 12 个月账单 + 1 个月缓冲）
    retention_usage_months: int = Field(default=13, ge=1, le=120)
    #: 插件任务/产物保留天数（与 `plugin_artifact_retention_days` 同口径，单独一项以便解耦）
    retention_task_days: int = Field(default=180, ge=1, le=3_650)
    #: 会话保留天数（硬删；软删由 `conversation_soft_delete_recover_days` 管）
    retention_conversation_days: int = Field(default=365, ge=1, le=3_650)
    #: 会话软删后可恢复天数（MC3 返回 `recoverable_until`）
    conversation_soft_delete_recover_days: int = Field(default=30, ge=1, le=365)
    #: 单用户记忆条数上限（超出按"最久未命中 + low 置信度优先"淘汰）
    memory_max_per_user: int = Field(default=200, ge=1, le=10_000)
    #: 每 N 轮对话触发一次事实抽取（0 = 关闭抽取，只保留手动与历史数据）
    memory_extract_every_turns: int = Field(default=10, ge=0, le=100)

    # ------------------------------------------------------------------
    # 记忆向量化（阶段四 §3.3.1 / 决策 A-1）
    #
    # 默认**关闭**（A-1）：Dify Service API 不提供 raw embedding，DeepSeek 系模型
    # 也没有 embeddings 端点。关闭时召回退化为"类别 + 时间 + 置信度"、去重退化为
    # `difflib` 比值（可离线单测、无外网）。客户自带 OpenAI 兼容 embeddings 时再开。
    # 维度固定进索引（`vector(N)`），换模型必须重建列——这条写进运维手册。
    # ------------------------------------------------------------------
    memory_embedding_enabled: bool = False
    memory_embedding_dim: int = Field(default=1024, ge=64, le=4096)
    memory_embedding_model: str = ""
    memory_embedding_base_url: str = ""
    memory_embedding_api_key: str = ""
    #: 单次向量化请求超时（秒）；召回整体超时另由 300ms 预算控制（§4.5）
    memory_embedding_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    #: 单批向量化条数（超批会拉长单次事务，且多数网关有条数上限）
    memory_embedding_batch_size: int = Field(default=16, ge=1, le=256)

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

    @property
    def uses_postgres(self) -> bool:
        """业务数据是否落 PostgreSQL（D-02）。

        Returns:
            bool: `business_store=postgres` 时返回 True。
        """
        return self.business_store == "postgres"

    @property
    def credential_key_required(self) -> bool:
        """是否强制要求凭据加密密钥（D-10）。

        判定口径：真实部署形态（`business_store=postgres`）或 `env=production`
        必须配置密钥，否则启动即失败；development/test/staging 允许缺失，
        便于离线跑单测（首次真正需要加解密时仍会报 30004，绝不静默降级）。

        Returns:
            bool: 必须配置 `OA_CREDENTIAL_ENCRYPTION_KEY` 时返回 True。
        """
        return self.uses_postgres or self.env == "production"

    @property
    def payment_credentials_ready(self) -> bool:
        """支付渠道密钥是否齐备（B09）。

        Returns:
            bool: `pay_channel=fake` 时恒为 True；微信支付需商户号 + APIv3 密钥齐备。
        """
        if self.pay_channel == "fake":
            return True
        return bool(self.pay_mch_id.strip()) and bool(self.pay_api_v3_key.strip())

    @property
    def use_fake_payment_gateway(self) -> bool:
        """是否使用测试用假支付网关（B09）。

        判定口径：显式 `pay_channel=fake`，**或**「桩模式 + 开发/测试环境」。
        真实环境（staging/production）即使处于桩模式也**不**回落假网关——
        否则上线后支付回调可被任意伪造。

        Returns:
            bool: 使用假网关时返回 True。
        """
        if self.pay_channel == "fake":
            return True
        return self.mock_mode and self.env in {"development", "test"}


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
