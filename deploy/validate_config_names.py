#!/usr/bin/env python3
"""编排配置名一致性门禁（G-35，配置名漂移防复发）。

**为什么要有这条门禁**（付过的代价）：

三套编排（`docker-compose.yml` / `docker-compose.saas.yml` / `docker-compose.lite.yml`）
都给适配层注入 `OA_KEYCLOAK_URL: http://keycloak:8080`，而 `Settings` 的字段名是
`keycloak_base_url`，对应环境变量是 `OA_KEYCLOAK_BASE_URL`，且**没有配 alias**。
pydantic-settings 的 `extra="ignore"` 会把这个注入**静默丢弃**，`Settings()` 里
`keycloak_base_url` 仍是默认值。

它之所以能潜伏这么久，是因为默认值恰好也是 `http://keycloak:8080`——"配了等于没配"
没有任何可观察症状：启动日志正常、`/readyz` 正常、IAM 联调也通。一旦有人把编排里的
值改成客户内网 IAM 地址，适配层仍会去打默认地址，排查方向会被引向网络与防火墙。
**静默失效的配置比报错的配置贵得多**，所以这条约束必须落在代码上，而不是注释里。

判据（两条）：

- R1：编排注入的每个 `OA_*`，要么是 `Settings` 的字段（`OA_` + 字段名大写），
  要么在下方白名单里**带理由显式登记**（底座容器自身消费、宿主机侧脚本消费等）。
- R2（G-36，防「构建期静默丢弃」）：编排里传给前端的每个 `NEXT_PUBLIC_*`
  构建参数，**必须在 `apps/web/Dockerfile` 里有同名 `ARG` 声明**。
  依据是 Docker 的行为：未声明的 `ARG` 会让 `build.args` 被**静默忽略**（只有构建日志
  里一行 warning），镜像照常产出、只是用了默认值——于是 `NEXT_PUBLIC_OIDC_ISSUER`
  恒为空串，登录页老实降级成「统一登录尚未配置」，**登录入口在容器里根本不渲染**，
  而 `npm run dev` 一切正常。这类缺口没有报错、只有"功能完整但不可用"。

不做的事：不校验值（值由 `validate_secrets.py` 管）、不校验 `.env` 模板与编排的对齐关系
（`.env` 是部署者填的，缺项由 `${VAR:?}` 在启动期拦）。

用法::

    python deploy/validate_config_names.py                 # 扫描（CI 默认）
    python deploy/validate_config_names.py --self-test     # 负样本自检（通过 = 退出码 0）
    python deploy/validate_config_names.py --json          # 机器可读输出

退出码：0 = 通过（含自检通过）；1 = 发现未登记的配置名 / 自检未拦住负样本；
2 = 门禁自身异常（目录缺失、适配层导入失败）。

**自检退出码为什么与 `validate_secrets.py --self-test` 相反**：那边把自检当"负样本探针"，
命中即退出 1；本脚本按仓库主流口径 **通过 0 / 拦截 1**。自检通过意味着"门禁确实能拦住
坏键"，属于门禁健康，所以退出 0——CI 里逐条断开时不需要记住哪个门禁是反向语义。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Final

try:  # pragma: no cover - 缺依赖属于环境问题，给出可操作提示而不是 traceback
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

#: 仓库根目录（本脚本位于 <repo>/deploy/）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 前端 Dockerfile —— R2 判据的「另一端」：编排传的构建参数必须在这里有 ARG
WEB_DOCKERFILE: Final[Path] = REPO_ROOT / "apps" / "web" / "Dockerfile"

#: 前端构建期变量前缀（Next 只对 `NEXT_PUBLIC_*` 做构建期内联）
NEXT_PUBLIC_PREFIX: Final[str] = "NEXT_PUBLIC_"

#: Dockerfile 的 ARG 声明（`ARG NAME` 或 `ARG NAME=默认值`）
DOCKERFILE_ARG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)

#: 适配层源码目录（导入 `Settings` 需要）
ADAPTER_DIR: Final[Path] = REPO_ROOT / "apps" / "adapter"

#: 配置项前缀（架构文档 9.5：所有环境变量统一大写 + `OA_` 前缀）
ENV_PREFIX: Final[str] = "OA_"

#: 参与扫描的编排文件（`deploy/` 下三套 + bench 探针编排；新增编排按此 glob 自动纳入）
COMPOSE_GLOBS: Final[tuple[str, ...]] = (
    "deploy/docker-compose*.yml",
    "scripts/bench/*/docker-compose*.yml",
)

#: 编排中出现的 `OA_*` 变量名
INJECT_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bOA_[A-Z0-9_]+\b")

#: 建议字段名时使用的字符相似度下限（低于此值的候选不会出现在建议里）
SUGGESTION_MIN_RATIO: Final[float] = 0.6

#: 建议字段名时要求的最少分词重合数（下划线分词）
SUGGESTION_MIN_OVERLAP: Final[int] = 2

#: 底座容器**自身**消费的变量：由 `docker-compose*.yml` 映射成底座自己的环境变量
#: （`KC_*` / `POSTGRES_*` / `DB_*` 等），从不进入适配层进程，因此不算漂移。
CONSUMED_BY_BASE_CONTAINERS: Final[dict[str, str]] = {
    # PostgreSQL（initdb 初始化超级用户与库名）
    "OA_POSTGRES_USER": "底座数据库容器的 initdb 参数（POSTGRES_USER）",
    "OA_POSTGRES_PASSWORD": "底座数据库容器的 initdb 参数（POSTGRES_PASSWORD）",
    "OA_POSTGRES_DB": "底座数据库容器的 initdb 参数（POSTGRES_DB）",
    # IAM 容器（KC_* 映射；适配层只消费 OA_KEYCLOAK_BASE_URL/REALM/CLIENT_ID/CLIENT_SECRET）
    "OA_KEYCLOAK_DB": "IAM 容器自身的库名（KC_DB_URL）",
    "OA_KEYCLOAK_ADMIN": "IAM 容器引导管理员用户名（KC_BOOTSTRAP_ADMIN_USERNAME）",
    "OA_KEYCLOAK_ADMIN_PASSWORD": "IAM 容器引导管理员口令（KC_BOOTSTRAP_ADMIN_PASSWORD）",
    # 模型底座（dify-* 容器）
    "OA_DIFY_DB": "模型底座自身的库名",
    "OA_DIFY_MODE": "模型底座容器角色（api/worker），不是适配层配置",
    "OA_DIFY_SECRET_KEY": "模型底座自身的会话签名密钥",
    "OA_DIFY_SANDBOX_KEY": "模型底座沙箱容器的调用密钥",
    "OA_DIFY_S3_BUCKET": "模型底座对象存储桶名",
    # 缓存（valkey）
    "OA_VALKEY_PASSWORD": "缓存容器口令（同时也是适配层 OA_REDIS_URL 的组成部分）",
    "OA_VALKEY_MAXMEMORY": "缓存容器内存上限",
    # 插件守护（plugin_daemon）
    "OA_PLUGIN_DAEMON_KEY": "插件守护容器服务端密钥",
    "OA_PLUGIN_DAEMON_URL": "插件守护容器自身监听地址",
    "OA_PLUGIN_INNER_API_KEY": "插件守护与底座之间的内部调用密钥",
    # 向量库 / 对象存储 / 出网代理 / 监控
    "OA_MILVUS_PORT": "向量库容器监听端口",
    "OA_MILVUS_METRICS_PORT": "向量库容器指标端口",
    "OA_OBJECT_STORE_ADMIN_PORT": "对象存储容器管理端口",
    "OA_OBJECT_STORE_API_PORT": "对象存储容器 S3 端口",
    "OA_OBJECT_STORE_REGION": "对象存储容器区域名",
    "OA_SSRF_PROXY_URL": "出网代理容器自身的地址（适配层用的是 OA_HTTP_PROXY 一类字段）",
    "OA_PROMETHEUS_PORT": "监控容器监听端口",
    # 前端与接入口
    "OA_WEB_PORT": "前端容器监听端口",
    "OA_WEB_ADAPTER_BASE_URL": "前端浏览器侧可见的适配层地址（NEXT_PUBLIC_*，构建期注入）",
    # 统一登录：只在前端构建期被消费（NEXT_PUBLIC_OIDC_ISSUER / _CLIENT_ID），
    # 不进入适配层进程。两项都是公开接入信息，不含密钥；留空时登录页只显示
    # 「统一登录尚未配置」，登录入口不可达——所以必须在编排里显式传下去。
    "OA_WEB_OIDC_ISSUER": "统一登录服务的公开接入地址（NEXT_PUBLIC_*，构建期注入）",
    "OA_WEB_OIDC_CLIENT_ID": "统一登录的公开客户端标识（NEXT_PUBLIC_*，构建期注入）",
    "OA_LITE_DIFY_WEB_PORT": "简版底座前端容器端口",
    "OA_LITE_PROXY_PORT": "简版 Nginx 反代端口",
    "OA_LITE_PUBLIC_URL": "简版部署对外访问地址（反代 server_name 用）",
}

#: 宿主机侧（编排插值 / 安装脚本 / 初始化 SQL）消费的变量：不进容器进程。
#:
#: **准入标准（QA 复核定的口径）**：该键必须真的有读方——`app/` 的字段、某个脚本、
#: 或编排自身的插值/端口映射；**只写不读的一律不进白名单**（进了就是给"静默失效"
#: 盖合格章）。曾经的 `OA_OFFLINE_MODE` 就是反面教材：install.sh 只把它写进 .env，
#: 适配层没有对应字段，于是它既不生效也不报错——现已从白名单与注入里双双删除。
CONSUMED_BY_HOST_SIDE: Final[dict[str, str]] = {
    "OA_IMAGE_TAG": "编排层的镜像 tag 插值，不注入容器环境",
    "OA_ADAPTER_PORT": "编排层的宿主机端口映射（仅左侧取值）",
    "OA_EDGE_HTTP_PORT": ("K4-min：边缘服务的宿主机端口映射（仅左侧取值），不进容器环境"),
    "OA_EDGE_HTTPS_PORT": ("G1：边缘 TLS(443) 的宿主机端口映射（仅左侧取值），不进容器环境"),
    "OA_LITE_PROXY_TLS_PORT": ("G1：简版边缘 TLS(443) 的宿主机端口映射（仅左侧取值）"),
    "OA_DB_APP_USER": "初始化 SQL（init-app-role.sql）建应用角色用，由 DBA/initdb 执行",
    "OA_DB_APP_PASSWORD": "同上，应用角色的口令",
}

#: 放行集合（名称 → 理由）；新增项必须写清「谁在消费」
WHITELIST: Final[dict[str, str]] = {
    **CONSUMED_BY_BASE_CONTAINERS,
    **CONSUMED_BY_HOST_SIDE,
}


class Drift:
    """一条配置名漂移记录。"""

    def __init__(self, file: str, line: int, name: str, text: str) -> None:
        """初始化。

        Args:
            file: 相对仓库根的编排文件路径。
            line: 行号（1 起）。
            name: 编排注入的变量名。
            text: 该行原文（截断到 120 字符，便于定位）。
        """
        self.file = file
        self.line = line
        self.name = name
        self.text = text

    def render(self, suggestion: str) -> str:
        """渲染为一行文本。

        Args:
            suggestion: 最接近的 `Settings` 字段名（无候选时为空串）。

        Returns:
            str: 明细 + 建议。
        """
        hint = f"，最接近的字段名：{suggestion}" if suggestion else ""
        return f"  - {self.file}:{self.line}  {self.name}{hint}\n      {self.text}"


def _ensure_adapter_on_path() -> None:
    """把适配层源码目录加入 `sys.path`（脚本可从任意目录运行）。"""
    adapter = str(ADAPTER_DIR)
    if adapter not in sys.path:
        sys.path.insert(0, adapter)


def consumable_names() -> frozenset[str]:
    """收集「适配层真能吃到」的环境变量名。

    **只看字段名，不实例化 `Settings`**：实例化会读工作目录下的 `.env`，
    让门禁结果依赖运行位置（CI 与本地就会不一致）。

    Returns:
        frozenset[str]: `OA_` + 字段名大写 的集合。

    Raises:
        SystemExit: 适配层无法导入（缺少依赖或路径不对），属门禁自身异常（码 2）。
    """
    _ensure_adapter_on_path()
    try:
        from app.core.config import Settings
    except Exception as exc:  # 脚本入口，导入失败一律按环境问题处理
        print(f"[FAIL] 无法导入 app.core.config.Settings: {type(exc).__name__}: {exc}")
        raise SystemExit(2) from exc
    return frozenset(f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields)


def compose_files() -> list[Path]:
    """列出参与扫描的编排文件。

    Returns:
        list[Path]: 去重后的编排文件路径（升序）。
    """
    found: dict[Path, None] = {}
    for pattern in COMPOSE_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                found[path] = None
    return list(found)


def _rel_to_repo(path: Path) -> str:
    """把路径转成相对仓库根的形式；不在仓库内时退回文件名。

    Args:
        path: 目标路径。

    Returns:
        str: 相对路径（POSIX 分隔符）；无法相对化时返回文件名。
    """
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def scan_files(paths: list[Path], allowed: frozenset[str]) -> list[Drift]:
    """扫描编排文件，返回「注入名不在放行集合里」的漂移项。

    Args:
        paths: 待扫描的编排文件。
        allowed: 放行集合（`Settings` 可消费名 ∪ 白名单）。

    Returns:
        list[Drift]: 漂移项；文件不可读时跳过该项（由调用方另行报告）。
    """
    drifts: list[Drift] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            # 注释行不参与：注释里出现示例键名是文档，不是注入
            if stripped.startswith("#"):
                continue
            for name in set(INJECT_PATTERN.findall(line)):
                if name not in allowed:
                    drifts.append(Drift(_rel_to_repo(path), lineno, name, stripped[:120]))
    return drifts


class BuildArgDrift:
    """一条「编排传了、Dockerfile 没声明」的构建参数漂移。"""

    def __init__(self, file: str, lineno: int, service: str, name: str) -> None:
        """初始化。

        Args:
            file: 相对仓库根的编排文件路径。
            lineno: 该参数在编排里的大致行号（0 表示未定位到）。
            service: 传该参数的服务名。
            name: 构建参数名（`NEXT_PUBLIC_*`）。
        """
        self.file = file
        self.lineno = lineno
        self.service = service
        self.name = name

    def render(self) -> str:
        """渲染为一行文本（结论必须自解释，运维不需要再查文档）。

        Returns:
            str: 明细 + 影响说明。
        """
        where = f"{self.file}:{self.lineno}" if self.lineno else self.file
        return (
            f"  - {where}  服务 {self.service} 传了 {self.name}\n"
            f"      apps/web/Dockerfile 里没有 `ARG {self.name}` 声明"
            f" → 构建期会被静默丢弃，前端拿到空值"
        )


def _require_yaml() -> None:
    """确认 PyYAML 可用（R2 需要解析编排结构，而不是靠正则猜缩进）。

    Raises:
        SystemExit: 缺少依赖，属门禁自身异常（码 2）。
    """
    if yaml is None:  # pragma: no cover - 仅在依赖缺失的环境触发
        print("[FAIL] 缺少 PyYAML，无法校验构建参数（pip install pyyaml）")
        raise SystemExit(2)


def dockerfile_args(path: Path = WEB_DOCKERFILE) -> frozenset[str]:
    """收集 Dockerfile 里声明过的 `ARG` 名字。

    Args:
        path: Dockerfile 路径（自检时传临时文件）。

    Returns:
        frozenset[str]: 已声明的参数名；文件不可读时返回空集合。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    return frozenset(DOCKERFILE_ARG_PATTERN.findall(text))


def web_build_args(path: Path) -> list[tuple[str, str]]:
    """列出编排里传给前端的 `NEXT_PUBLIC_*` 构建参数。

    只看 `build.args`（`build` 为字符串短形式时没有构建参数，跳过）。
    `args` 支持字典与列表两种 YAML 写法，列表项按 `KEY=VALUE` 取键。

    Args:
        path: 编排文件路径。

    Returns:
        list[tuple[str, str]]: `(服务名, 参数名)` 列表；解析失败时返回空列表。
    """
    _require_yaml()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    services = raw.get("services")
    if not isinstance(services, dict):
        return []

    pairs: list[tuple[str, str]] = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        args = build.get("args")
        keys: list[str] = []
        if isinstance(args, dict):
            keys = [str(key) for key in args]
        elif isinstance(args, list):
            keys = [str(item).split("=", 1)[0] for item in args]
        for key in keys:
            if key.startswith(NEXT_PUBLIC_PREFIX):
                pairs.append((str(service_name), key))
    return pairs


def _locate_line(path: Path, needle: str) -> int:
    """在文件里找首个包含 needle 的行号（YAML 解析不保留行号，这里补回来）。

    Args:
        path: 文件路径。
        needle: 要定位的文本。

    Returns:
        int: 行号（1 起）；未找到或不可读时返回 0。
    """
    try:
        content = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0
    for lineno, line in enumerate(content, start=1):
        if needle in line:
            return lineno
    return 0


def scan_build_args(paths: list[Path], declared: frozenset[str]) -> list[BuildArgDrift]:
    """R2：找出「编排传了、Dockerfile 没声明」的构建参数。

    Args:
        paths: 待扫描的编排文件。
        declared: Dockerfile 已声明的 ARG 名集合。

    Returns:
        list[BuildArgDrift]: 漂移项（空列表表示全部已声明）。
    """
    drifts: list[BuildArgDrift] = []
    for path in paths:
        for service, name in web_build_args(path):
            if name in declared:
                continue
            drifts.append(
                BuildArgDrift(_rel_to_repo(path), _locate_line(path, name), service, name)
            )
    return drifts


def suggest(name: str, candidates: frozenset[str]) -> str:
    """给出最接近的字段名，帮助定位「本来想写哪个」。

    **为什么不能只用 `difflib` 的字符相似度**：`OA_KEYCLOAK_URL` 与
    `OA_KEYCLOAK_REALM` 的字符相似度比与 `OA_KEYCLOAK_BASE_URL` 更高（后者更长），
    于是建议会指向一个毫不相关的字段。真正想找的是"共享词根"的那个，
    所以按「下划线分词重合数优先、字符相似度次之」排序。

    Args:
        name: 编排里的变量名。
        candidates: 候选集合（`Settings` 可消费名）。

    Returns:
        str: 最接近的候选名；无足够相似项时返回空串。
    """
    tokens = set(name.split("_"))
    scored: list[tuple[int, float, str]] = []
    for item in candidates:
        overlap = len(tokens & set(item.split("_")))
        ratio = difflib.SequenceMatcher(None, name, item).ratio()
        if overlap >= SUGGESTION_MIN_OVERLAP or ratio >= SUGGESTION_MIN_RATIO:
            scored.append((overlap, ratio, item))
    if not scored:
        return ""
    # 重合词数多的优先，其次字符相似度高的优先；最后按名称排序保证输出稳定
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return scored[0][2]


# ===========================================================================
# 负样本自检（证明门禁「能红」）
# ===========================================================================


def self_test() -> int:
    """负样本自检：7 条断言（A×2 / B / C / D / E / F），覆盖「该拦的拦住、该放的放行」。

    - A：真实发生过的漂移键（`OA_KEYCLOAK_URL`）→ 必须拦，且给出正确建议字段名；
    - B：正确键名 + 注释里的示例键名 → 必须放行（注释是文档，不是注入）；
    - C：白名单变量（底座容器消费，`OA_POSTGRES_USER`）→ 必须放行；
    - D：**已删除的死配置回归锁**（`OA_OFFLINE_MODE`，install.sh 只写 .env、适配层
      无字段、底座容器也不消费）→ 若有人把注入加回来、或把白名单条目加回来，
      本条立刻红。这是本轮 QA 复核出的那类"门禁自己给静默失效盖章"的反向证明；
    - E：**R2 负样本**——编排传了 `NEXT_PUBLIC_OIDC_ISSUER`、Dockerfile 未声明
      `ARG` → 必须拦，且提示要写明「构建期会被静默丢弃」；
    - F：声明齐全 → 必须放行（证明门禁不会逼人写重复声明）。

    自检**不读仓库**（用临时目录），否则编排改好了自检就跟着变绿，失去探针意义。

    Returns:
        int: 行为符合预期返回 0（门禁健康），否则返回 1。
    """
    allowed = consumable_names() | frozenset(WHITELIST)
    checks: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)

        bad = target / "bad.yml"
        bad.write_text(
            "services:\n"
            "  adapter:\n"
            "    environment:\n"
            # 负样本 A：真实发生过的漂移键（字段是 keycloak_base_url）
            "      OA_KEYCLOAK_URL: http://keycloak:8080\n",
            encoding="utf-8",
        )
        got = scan_files([bad], allowed)
        checks.append(("A 漂移键（IAM 地址旧键名）须被拦截", len(got) == 1))
        checks.append(
            (
                "A 须给出最接近的字段名 OA_KEYCLOAK_BASE_URL",
                bool(got) and suggest(got[0].name, consumable_names()) == "OA_KEYCLOAK_BASE_URL",
            )
        )

        good = target / "good.yml"
        good.write_text(
            "services:\n"
            "  adapter:\n"
            "    environment:\n"
            "      OA_KEYCLOAK_BASE_URL: http://keycloak:8080\n"
            "      OA_AUTH_MODE: oidc\n"
            "      # 注释里的示例键名不算注入\n"
            "      # OA_KEYCLOAK_URL: http://example\n",
            encoding="utf-8",
        )
        checks.append(("B 正确键名 + 注释里的示例键名须放行", not scan_files([good], allowed)))

        base = target / "base.yml"
        base.write_text(
            "services:\n"
            "  postgres:\n"
            "    environment:\n"
            "      POSTGRES_USER: ${OA_POSTGRES_USER:?x}\n"
            "      # 白名单另一类：IAM 容器自身的库名\n"
            "      KC_DB_URL: jdbc:postgresql://postgres:5432/${OA_KEYCLOAK_DB:-keycloak}\n",
            encoding="utf-8",
        )
        checks.append(
            ("C 白名单变量（底座容器消费：数据库/IAM）须放行", not scan_files([base], allowed))
        )

        dead = target / "dead.yml"
        dead.write_text(
            "services:\n"
            "  adapter:\n"
            "    environment:\n"
            # 负样本 D：已删除的死配置，谁也不读；再出现必须拦
            "      OA_OFFLINE_MODE: ${OA_OFFLINE_MODE:-true}\n",
            encoding="utf-8",
        )
        dead_hits = scan_files([dead], allowed)
        checks.append(("D 已删除的死配置（无任何读方）再被注入须被拦截", len(dead_hits) == 1))

        # ---- R2：编排传了构建参数，Dockerfile 必须同名声明 ARG ----
        compose_r2 = target / "r2.yml"
        compose_r2.write_text(
            "services:\n"
            "  web:\n"
            "    build:\n"
            "      context: ../apps/web\n"
            "      args:\n"
            "        NEXT_PUBLIC_ADAPTER_BASE_URL: http://localhost:8000\n"
            "        NEXT_PUBLIC_OIDC_ISSUER: https://sso.example.com\n",
            encoding="utf-8",
        )
        docker_partial = target / "Dockerfile.partial"
        docker_partial.write_text(
            "FROM node:22-alpine\nARG NEXT_PUBLIC_ADAPTER_BASE_URL=http://adapter:8000\n",
            encoding="utf-8",
        )
        got_r2 = scan_build_args([compose_r2], dockerfile_args(docker_partial))
        rendered = got_r2[0].render() if got_r2 else ""
        # 负样本 E：正是本轮 QA 复核抓到的形态（传了 OIDC，但 Dockerfile 没有对应 ARG）
        checks.append(
            (
                "E 编排传了 NEXT_PUBLIC_OIDC_ISSUER 但 Dockerfile 未声明 ARG 须被拦截"
                "（且提示写明「构建期会被静默丢弃」）",
                [item.name for item in got_r2] == ["NEXT_PUBLIC_OIDC_ISSUER"]
                and "构建期会被静默丢弃" in rendered,
            )
        )

        # 正样本 F：声明齐全时不得误报
        docker_full = target / "Dockerfile.full"
        docker_full.write_text(
            "FROM node:22-alpine\n"
            "ARG NEXT_PUBLIC_ADAPTER_BASE_URL=http://adapter:8000\n"
            "ARG NEXT_PUBLIC_OIDC_ISSUER=\n"
            "ARG NEXT_PUBLIC_OIDC_CLIENT_ID=\n",
            encoding="utf-8",
        )
        checks.append(
            (
                "F 声明齐全时须放行（不误报）",
                not scan_build_args([compose_r2], dockerfile_args(docker_full)),
            )
        )

    print("负样本自检结果：")
    for label, ok in checks:
        print(f"    {'✅ 符合预期' if ok else '❌ 行为不符'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"[FAIL] 自检未通过：{failed}")
        return 1
    print("[PASS] 自检通过：能拦漂移键与死配置，也不误伤正确键名与白名单")
    return 0


# ===========================================================================
# CLI
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 退出码（0 通过 / 1 漂移或自检未通过 / 2 门禁异常）。
    """
    parser = argparse.ArgumentParser(description="编排配置名一致性门禁（G-35）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（通过 = 退出码 0）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    print("=" * 70)
    print("编排配置名一致性校验（G-35 注入名可消费 / G-36 构建参数有同名 ARG）")
    print("=" * 70)

    consumable = consumable_names()
    allowed = consumable | frozenset(WHITELIST)
    paths = compose_files()
    if not paths:
        print(f"[FAIL] 未找到任何编排文件（glob: {', '.join(COMPOSE_GLOBS)}）")
        return 2

    drifts = scan_files(paths, allowed)
    declared_args = dockerfile_args()
    arg_drifts = scan_build_args(paths, declared_args)
    print(
        f"[INFO] 扫描 {len(paths)} 个编排文件；"
        f"可消费名 {len(consumable)} 个、白名单 {len(WHITELIST)} 个；"
        f"Dockerfile 已声明 ARG {len(declared_args)} 个"
    )
    for path in paths:
        print(f"       - {_rel_to_repo(path)}")
    print()

    arg_payload: list[dict[str, object]] = []
    if arg_drifts:
        print(
            "[FAIL] 编排传了、但 apps/web/Dockerfile 未声明的构建参数"
            "（构建期会被静默丢弃，前端拿到空值）："
        )
        for item in arg_drifts:
            print(item.render())
            arg_payload.append(
                {
                    "file": item.file,
                    "line": item.lineno,
                    "service": item.service,
                    "name": item.name,
                }
            )
        print()
        print("处置：在 apps/web/Dockerfile 的 builder 阶段补 `ARG <同名>` 并转成 ENV，")
        print("      否则该值在镜像里恒为默认值（NEXT_PUBLIC_* 不会在运行期生效）。")
        print()

    if drifts:
        print("[FAIL] 发现编排注入了适配层吃不到的配置名（会被静默丢弃）：")
        for drift in drifts:
            hint = suggest(drift.name, consumable)
            print(drift.render(hint))
        print()
        print("处置：改键名对齐 `Settings` 字段（`OA_` + 字段名大写），")
        print("      或在 validate_config_names.py 的白名单里写明「谁在消费」。")

    if drifts or arg_drifts:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "drifts": [
                            {
                                "file": item.file,
                                "line": item.line,
                                "name": item.name,
                                "suggestion": suggest(item.name, consumable),
                                "text": item.text,
                            }
                            for item in drifts
                        ],
                        "buildArgDrifts": arg_payload,
                    },
                    ensure_ascii=False,
                )
            )
        return 1

    # 两项校验**并列输出**（不合并成一句 OK）：失败时能一眼看出是哪一类问题
    print("[OK]   G-35 注入名可消费：编排注入的 OA_* 全部能被 Settings 或白名单解释")
    print(
        f"[OK]   G-36 构建参数有声明：web 的 NEXT_PUBLIC_* 均在 Dockerfile 有同名 ARG"
        f"（已声明 {len(declared_args)} 个）"
    )
    if args.json:
        print(json.dumps({"ok": True, "drifts": [], "buildArgDrifts": []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
