"""插件声明加载器与注册表（阶段三 B16，`architecture-phase3.md` §2.1 / §3.2 / D-18）。

**三重校验的第一道**（另两道是 CI G-26 与运行期 `ActionGateway.resolve()`）：
本模块在**加载期**把一份声明可能出错的地方一次性查完，任何一项不通过都
**拒绝加载并留档原因**，绝不"带病注册"。检查项：

1. 形态：6 段 schema（`extra="forbid"`）+ 序号连续 + 产物/依赖引用闭合（`schema.py`）；
2. 能力：`capabilities[].key` ⊆ 6 项封闭枚举；
3. 原语：`steps[].action` ⊆ 6 项白名单；`tool` 步骤的 `tool_type/operation`
   必须存在于 `app/registry/tool_registry.yaml`（含 `tool_alternatives`）；
4. 安全：递归扫描禁用键名（凭据/代码/外部地址）；文案过禁词表；
5. 业务：对外动作必须有 `confirm`；`retry` 不得用于不可重试的原语；
6. 版本不可变（D-18）：同一 `(plugin_id, version)` **注册两次即拒绝**，
   已注册条目字段保持不变（`PluginRegistry.load_manifest` 的硬约束）。

**为什么不"自动修"**：声明是契约。静默纠正（例如把 `1,3,4` 补成 `1,2,3`）
会让 DB 快照与磁盘文件不一致，而 `plugin_version.manifest` 是历史任务渲染的唯一
依据（R2 风险对策）——所以宁可加载失败。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.plugins.actions import (
    ACTION_RETRYABLE,
    ACTION_WHITELIST,
    CAPABILITY_KEYS,
    is_outbound_operation,
)
from app.plugins.schema import (
    PluginManifest,
    find_banned_terms,
    find_forbidden_keys,
)
from app.registry.loader import get_tool_registry

logger = get_logger(__name__)

#: 声明目录（相对 `app/plugins`）
MANIFESTS_DIRNAME: Final[str] = "manifests"

#: 声明文件后缀
MANIFEST_SUFFIX: Final[str] = ".json"

#: 注册失败的阶段标识（用于日志与测试断言）
STAGE_SCHEMA: Final[str] = "schema"
STAGE_FORBIDDEN_KEY: Final[str] = "forbidden_key"
STAGE_BANNED_TERM: Final[str] = "banned_term"
STAGE_CAPABILITY: Final[str] = "capability"
STAGE_ACTION: Final[str] = "action"
STAGE_TOOL: Final[str] = "tool"
STAGE_CONFIRM: Final[str] = "confirm"
STAGE_RETRY: Final[str] = "retry"
STAGE_DUPLICATE: Final[str] = "duplicate"
STAGE_IO: Final[str] = "io"


@dataclass(frozen=True, slots=True)
class LoadFailure:
    """一条加载失败记录（**留档**而不是抛异常中断整批加载）。

    Attributes:
        source: 来源文件名。
        stage: 失败阶段（`schema` / `capability` / `tool` / `duplicate` ...）。
        reason: 业务语言原因（可直接打印到 CI 输出）。
        detail: 明细（字段路径 / 正则等）。
    """

    source: str
    stage: str
    reason: str
    detail: str = ""

    def render(self) -> str:
        """渲染为单行文本（CI 输出用）。

        Returns:
            str: 形如 `plg_x.json [schema] 步骤序号必须连续：...`。
        """
        suffix = f"（{self.detail}）" if self.detail else ""
        return f"{self.source} [{self.stage}] {self.reason}{suffix}"


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    """注册表条目：声明本身 + 内容摘要。

    Attributes:
        manifest: 已校验的声明。
        sha256: 声明原文摘要（写入 `plugin_version.manifest_sha256`，防篡改）。
        source: 来源文件名。
    """

    manifest: PluginManifest
    sha256: str
    source: str

    @property
    def plugin_id(self) -> str:
        """插件标识。

        Returns:
            str: 插件标识。
        """
        return self.manifest.plugin_id

    @property
    def version(self) -> str:
        """插件版本。

        Returns:
            str: 版本号。
        """
        return self.manifest.version


def manifest_digest(payload: dict[str, Any]) -> str:
    """计算声明的规范化摘要（键排序、紧凑分隔、保留中文原字）。

    与 `plugin_version.manifest_sha256` 的口径必须一致：CI（G-26）、加载器、
    种子 SQL 三处都调用本函数，避免"三种 sha 各算各的"。

    Args:
        payload: 声明原始字典。

    Returns:
        str: 十六进制 SHA-256。
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PluginRegistry:
    """插件声明注册表（进程内单例，见 `get_registry`）。

    Attributes:
        _records: `(plugin_id, version) → ManifestRecord`。
        _failures: 加载失败记录（累计）。
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._records: dict[tuple[str, str], ManifestRecord] = {}
        self._failures: list[LoadFailure] = []

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def load_all(self, directory: Path | None = None) -> list[LoadFailure]:
        """加载目录下全部声明（失败不中断，逐条留档）。

        Args:
            directory: 声明目录；缺省 `app/plugins/manifests`。

        Returns:
            list[LoadFailure]: **本次加载**新增的失败记录（成功的条目已注册）。
        """
        target = directory or default_manifests_dir()
        if not target.is_dir():
            missing = LoadFailure(
                source=str(target),
                stage=STAGE_IO,
                reason="插件声明目录不存在",
                detail="",
            )
            self._failures.append(missing)
            logger.error("plugin_manifest_dir_missing", path=str(target))
            return [missing]

        newly: list[LoadFailure] = []
        for path in sorted(target.glob(f"*{MANIFEST_SUFFIX}")):
            failure = self.load_file(path)
            if failure is not None:
                newly.append(failure)
        logger.info(
            "plugin_manifests_loaded",
            directory=str(target),
            registered=len(self._records),
            failed=len(newly),
        )
        return newly

    def load_file(self, path: Path) -> LoadFailure | None:
        """加载单份声明文件。

        Args:
            path: 声明文件路径。

        Returns:
            LoadFailure | None: 失败时返回记录；成功返回 None。
        """
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return self._record_failure(
                LoadFailure(path.name, STAGE_IO, "声明文件读取失败", str(exc))
            )
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return self._record_failure(
                LoadFailure(path.name, STAGE_SCHEMA, "声明不是合法 JSON", f"第 {exc.lineno} 行")
            )
        if not isinstance(payload, dict):
            return self._record_failure(LoadFailure(path.name, STAGE_SCHEMA, "声明顶层必须是对象"))
        return self.load_manifest(payload, source=path.name)

    def load_manifest(
        self, payload: dict[str, Any], *, source: str = "<inline>"
    ) -> LoadFailure | None:
        """校验并注册一份已解析的声明。

        Args:
            payload: 声明原始字典。
            source: 来源标识（文件名，便于定位）。

        Returns:
            LoadFailure | None: 失败时返回记录；成功注册返回 None。
        """
        # ---- 1. 安全：禁用键名（凭据/代码/外部地址）递归扫描 ----
        forbidden = find_forbidden_keys(payload)
        if forbidden:
            return self._record_failure(
                LoadFailure(
                    source,
                    STAGE_FORBIDDEN_KEY,
                    "声明包含不允许的字段（凭据、代码或外部地址）",
                    ", ".join(forbidden[:5]),
                )
            )

        # ---- 2. 形态：6 段 schema ----
        try:
            manifest = PluginManifest.model_validate(payload)
        except ValidationError as exc:
            return self._record_failure(
                LoadFailure(source, STAGE_SCHEMA, "声明格式不符合规范", _render_errors(exc))
            )

        # ---- 3. 文案：禁词 / 占位词 ----
        for where, text in manifest.text_fields():
            hits = find_banned_terms(text)
            if hits:
                return self._record_failure(
                    LoadFailure(
                        source,
                        STAGE_BANNED_TERM,
                        f"文案含内部用语：{', '.join(hits)}",
                        where,
                    )
                )

        # ---- 4. 能力：⊆ 6 项封闭枚举 ----
        unknown_caps = [key for key in manifest.capability_keys if key not in CAPABILITY_KEYS]
        if unknown_caps:
            return self._record_failure(
                LoadFailure(
                    source,
                    STAGE_CAPABILITY,
                    "声明了平台未开放的能力",
                    ", ".join(unknown_caps),
                )
            )
        if len(set(manifest.capability_keys)) != len(manifest.capability_keys):
            return self._record_failure(LoadFailure(source, STAGE_CAPABILITY, "能力声明重复"))

        # ---- 5. 原语与工具 ----
        action_failure = self._check_actions(manifest, source)
        if action_failure is not None:
            return self._record_failure(action_failure)

        # ---- 6. 人工接管：对外动作必须有 confirm ----
        confirm_failure = self._check_confirm(manifest, source)
        if confirm_failure is not None:
            return self._record_failure(confirm_failure)

        # ---- 7. 重试策略：不可重试的原语不得用 retry ----
        for step in manifest.steps:
            if step.on_failure == "retry" and not ACTION_RETRYABLE.get(step.action, False):
                return self._record_failure(
                    LoadFailure(
                        source,
                        STAGE_RETRY,
                        f"步骤 {step.seq} 声明了自动重试，但该步骤不允许重试",
                        step.action,
                    )
                )

        # ---- 8. 版本不可变：同 (plugin_id, version) 重复注册即拒绝 ----
        key = (manifest.plugin_id, manifest.version)
        if key in self._records:
            existing = self._records[key]
            return self._record_failure(
                LoadFailure(
                    source,
                    STAGE_DUPLICATE,
                    "同一插件的同一版本已注册，版本不可覆盖",
                    f"{manifest.plugin_id}@{manifest.version} 已由 {existing.source} 注册",
                )
            )

        self._records[key] = ManifestRecord(
            manifest=manifest,
            sha256=manifest_digest(payload),
            source=source,
        )
        return None

    def _check_actions(self, manifest: PluginManifest, source: str) -> LoadFailure | None:
        """校验原语白名单与工具类型/操作是否已登记。

        Args:
            manifest: 已通过形态校验的声明。
            source: 来源标识。

        Returns:
            LoadFailure | None: 失败记录；通过返回 None。
        """
        unknown = [action for action in manifest.action_keys if action not in ACTION_WHITELIST]
        if unknown:
            return LoadFailure(
                source,
                STAGE_ACTION,
                "使用了平台未开放的能力",
                ", ".join(unknown),
            )
        registry = get_tool_registry()
        for step in manifest.steps:
            if step.action != "tool":
                continue
            pairs = [(step.tool_type or "", step.operation or "")]
            pairs.extend((alt.tool_type, alt.operation) for alt in step.tool_alternatives)
            for tool_type, operation in pairs:
                spec = registry.get(tool_type)
                if spec is None:
                    return LoadFailure(
                        source,
                        STAGE_TOOL,
                        f"步骤 {step.seq} 使用了未连接的办公工具",
                        tool_type,
                    )
                if not spec.has_operation(operation):
                    return LoadFailure(
                        source,
                        STAGE_TOOL,
                        f"步骤 {step.seq} 使用了工具不支持的动作",
                        f"{tool_type}.{operation}",
                    )
        return None

    def _check_confirm(self, manifest: PluginManifest, source: str) -> LoadFailure | None:
        """校验对外动作必须声明人工接管点。

        Args:
            manifest: 声明。
            source: 来源标识。

        Returns:
            LoadFailure | None: 失败记录；通过返回 None。
        """
        for step in manifest.steps:
            if step.action != "tool" or step.confirm is not None:
                continue
            if is_outbound_operation(step.tool_type or "", step.operation or ""):
                return LoadFailure(
                    source,
                    STAGE_CONFIRM,
                    f"步骤 {step.seq} 会对外发送内容，必须先请用户确认",
                )
        return None

    def _record_failure(self, failure: LoadFailure) -> LoadFailure:
        """留档并打日志。

        Args:
            failure: 失败记录。

        Returns:
            LoadFailure: 原记录。
        """
        self._failures.append(failure)
        logger.error(
            "plugin_manifest_rejected",
            source=failure.source,
            stage=failure.stage,
            reason=failure.reason,
            detail=failure.detail,
        )
        return failure

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, plugin_id: str, version: str = "") -> PluginManifest | None:
        """按标识取声明（`version` 为空取最新版本）。

        Args:
            plugin_id: 插件标识。
            version: 版本号；空串表示最新。

        Returns:
            PluginManifest | None: 声明；未注册返回 None。
        """
        if version:
            record = self._records.get((plugin_id, version))
            return record.manifest if record is not None else None
        latest = self.latest(plugin_id)
        if not latest:
            return None
        record = self._records.get((plugin_id, latest))
        return record.manifest if record is not None else None

    def record(self, plugin_id: str, version: str = "") -> ManifestRecord | None:
        """按标识取注册记录（含 sha256）。

        Args:
            plugin_id: 插件标识。
            version: 版本号；空串表示最新。

        Returns:
            ManifestRecord | None: 记录；未注册返回 None。
        """
        resolved = version or self.latest(plugin_id)
        if not resolved:
            return None
        return self._records.get((plugin_id, resolved))

    def latest(self, plugin_id: str) -> str:
        """取某插件的最新版本号（按语义化版本数值比较，非字符串比较）。

        Args:
            plugin_id: 插件标识。

        Returns:
            str: 版本号；未注册返回空串。
        """
        versions = [version for (pid, version) in self._records if pid == plugin_id]
        if not versions:
            return ""
        return max(versions, key=version_tuple)

    def versions(self, plugin_id: str) -> list[str]:
        """列出某插件的全部已注册版本（升序）。

        Args:
            plugin_id: 插件标识。

        Returns:
            list[str]: 版本列表。
        """
        versions = [version for (pid, version) in self._records if pid == plugin_id]
        return sorted(versions, key=version_tuple)

    def plugin_ids(self) -> list[str]:
        """列出全部已注册插件标识（升序）。

        Returns:
            list[str]: 插件标识列表。
        """
        return sorted({pid for (pid, _version) in self._records})

    def all_manifests(self) -> list[PluginManifest]:
        """列出全部声明（每个插件取最新版本，按标识升序）。

        Returns:
            list[PluginManifest]: 声明列表。
        """
        result: list[PluginManifest] = []
        for plugin_id in self.plugin_ids():
            manifest = self.get(plugin_id)
            if manifest is not None:
                result.append(manifest)
        return result

    def titles(self, plugin_id: str, version: str = "") -> list[str]:
        """取某插件某版本的步骤标题（历史任务按快照渲染用）。

        Args:
            plugin_id: 插件标识。
            version: 版本号；空串表示最新。

        Returns:
            list[str]: 步骤标题列表；未注册返回空列表。
        """
        manifest = self.get(plugin_id, version)
        return manifest.step_titles() if manifest is not None else []

    @property
    def failures(self) -> list[LoadFailure]:
        """全部加载失败记录（累计）。

        Returns:
            list[LoadFailure]: 失败记录列表。
        """
        return list(self._failures)

    def clear(self) -> None:
        """清空注册表与失败记录（测试隔离用）。"""
        self._records.clear()
        self._failures.clear()

    def __len__(self) -> int:
        """已注册的 `(plugin_id, version)` 条目数。

        Returns:
            int: 条目数。
        """
        return len(self._records)


def version_tuple(version: str) -> tuple[int, int, int]:
    """把 `x.y.z` 转为可比较元组。

    Args:
        version: 版本号。

    Returns:
        tuple[int, int, int]: 数值元组（非法形态按 0 处理，不抛异常）。
    """
    parts: list[int] = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _render_errors(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成一行业务化描述。

    Args:
        exc: 校验异常。

    Returns:
        str: 形如 `steps.0.seq: 序号必须连续`。
    """
    chunks: list[str] = []
    for error in exc.errors()[:4]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", ""))
        chunks.append(f"{location}: {message}" if location else message)
    return "; ".join(chunks)


def default_manifests_dir() -> Path:
    """默认声明目录 `app/plugins/manifests`。

    Returns:
        Path: 目录路径。
    """
    return Path(__file__).resolve().parent / MANIFESTS_DIRNAME


_registry: PluginRegistry | None = None


def get_registry(load: bool = True) -> PluginRegistry:
    """获取进程内注册表（首次调用时加载声明）。

    Args:
        load: 是否在首次获取时加载声明目录。

    Returns:
        PluginRegistry: 注册表单例。

    Raises:
        ApiError: 存在无法加载的声明（`30004`，启动期失败好过运行期带病）。
    """
    global _registry
    if _registry is not None:
        return _registry
    registry = PluginRegistry()
    failures = registry.load_all() if load else []
    _registry = registry
    if failures:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail="; ".join(item.render() for item in failures[:5]),
        )
    return registry


def reset_registry() -> None:
    """重置注册表单例（测试隔离用）。"""
    global _registry
    _registry = None


__all__: list[str] = [
    "MANIFESTS_DIRNAME",
    "MANIFEST_SUFFIX",
    "STAGE_ACTION",
    "STAGE_BANNED_TERM",
    "STAGE_CAPABILITY",
    "STAGE_CONFIRM",
    "STAGE_DUPLICATE",
    "STAGE_FORBIDDEN_KEY",
    "STAGE_IO",
    "STAGE_RETRY",
    "STAGE_SCHEMA",
    "STAGE_TOOL",
    "LoadFailure",
    "ManifestRecord",
    "PluginRegistry",
    "default_manifests_dir",
    "get_registry",
    "manifest_digest",
    "reset_registry",
    "version_tuple",
]
