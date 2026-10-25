#!/usr/bin/env python3
"""插件域契约生成器（W-01 契约换源）。

为什么需要它
------------
`apps/web/contracts/phase3.pl.json` 原先是从架构文档 §5.2/§5.3 **手抄**的快照，
已经抄错 8 处（见 `docs/phase4-batch2-design-2026-10-25.md` 附录 A）。手抄文本没有
真相源，错了只能靠人再看一遍文档——这正是 Batch 1 五次前端崩溃的形态
（`meta` → 扁平四字段、`changelog` 增删、`inputs` 缺失）。

本脚本把**后端 OpenAPI 文档**变成插件域契约的真相源：它用 `importlib` 加载
`deploy/validate_openapi.py`（**不复制**它那三行 `sys.path` 处理），调用它的
`build_schema()` 拿到 `create_app().openapi()`，再按白名单从
`components.schemas` 里挑出插件域的白名单模型，**只导出键集合**。

可选性口径 = A（主理人裁决）
---------------------------
生成的契约**只负责键集合**，不负责可选性。实测依据：插件域白名单模型全部字段带
默认值（`= ""` / `= 0` / `Field(default=...)`），Pydantic v2 因此一个 `required`
数组都不输出——照字面用 `required` 判可选性会得到「175 个字段全部可选」，首跑
170+ 条误报，门禁直接不可用。可选性仍由**手写快照 ↔ `types.ts`** 的既有
`compareInterface()` 守（`apps/web/scripts/check-plugin-contract.mjs`）。
故本文件写 `optionality_authority: "snapshot"`，让读契约的人一眼看到谁负责什么。

排除项
------
- 任务域 8 个接口在 `app/schemas/task.py`，属另一模块，本批不动；
- `OrgCapabilitySwitch` 是**前端投影类型**（后端 `MarketData.org_capabilities` 的
  元素是 7 键的 `PluginCapabilityItem`），不比对；
- 96 端点全量契约不生成（计划 §2.3 已排除）。

用法（仓库根目录执行）::

    python deploy/export_plugin_contract.py            # 生成/覆盖 gen 文件
    python deploy/export_plugin_contract.py --check    # 只比对不写盘（CI 用）
    python deploy/export_plugin_contract.py --stdout    # 打印到标准输出

退出码：
    0 = 成功（`--check` 下表示「盘上文件与后端一致」）
    2 = 白名单里的模型在 OpenAPI 文档中缺失（列出全部缺失项，不静默跳过）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Final

#: 仓库根目录（本文件位于 `<repo>/deploy/`）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 复用同一份接口基线脚本，保证「契约生成」与「端点基线门禁」看同一份文档
VALIDATE_OPENAPI_PATH: Final[Path] = REPO_ROOT / "deploy" / "validate_openapi.py"

#: 生成物落盘位置（**入库**：前端门禁在无 Python 的环境里也能读）
GEN_OUTPUT_PATH: Final[Path] = REPO_ROOT / "apps" / "web" / "contracts" / "plugin-domain.gen.json"

#: 契约版本号：形状变更（增删顶层字段）时 +1
CONTRACT_VERSION: Final[str] = "plugin-domain-v1"

#: 白名单版本号：插件域接口增删时 +1
WHITELIST_VERSION: Final[int] = 1

#: TS 接口名 → OpenAPI `components.schemas` 名（25 项，实测全部存在）
PLUGIN_DOMAIN_MAP: Final[dict[str, str]] = {
    "PluginMarketItem": "MarketItem",
    "PluginCategoryItem": "CategoryCountItem",
    "PluginMarketData": "MarketData",
    "PluginCapabilityItem": "PluginCapabilityItem",
    "PluginCapabilitiesData": "CapabilityListData",
    "PluginOutputSpec": "OutputBriefItem",
    "PluginSettingSpec": "SettingBriefItem",
    "PluginVersionBrief": "VersionBriefItem",
    "PluginDetail": "MarketDetailData",
    "InstalledPluginItem": "InstalledItem",
    "PluginInstallResult": "InstallData",
    "PluginStateResult": "StateData",
    "PluginUninstallResult": "UninstallData",
    "PluginUpgradeResult": "VersionData",
    "PluginRollbackResult": "VersionData",
    "PluginSettingsResult": "SettingsData",
    "PluginReviewItem": "ReviewItem",
    "PluginReviewsData": "ReviewListData",
    "PluginReviewResult": "ReviewData",
    "CapabilityPolicyItem": "CapabilityPolicyItem",
    "CapabilityPolicyRequest": "CapabilityPolicyRequest",
    "CapabilityPolicyRequestItem": "CapabilityPolicyRequestItem",
    "PluginGrantsUpdateResult": "UpdateGrantsData",
    "PluginGrantsUpdateRequest": "UpdateGrantsRequest",
    "PluginInputSpec": "InputBriefItem",
}

#: 任务域接口：在 `app/schemas/task.py`，本批不生成、不比对（各自有独立门禁）
TASK_DOMAIN_EXCLUDED: Final[tuple[str, ...]] = (
    "TaskChecklistItem",
    "TaskStepFailure",
    "TaskArtifact",
    "TaskDetail",
    "TaskRetryRequest",
    "TaskRetryResult",
    "TaskArtifactUpdateRequest",
    "TaskArtifactUpdateResult",
)

#: 前端投影类型：后端没有同名同形的 schema，不比对
PROJECTION_EXCLUDED: Final[tuple[str, ...]] = ("OrgCapabilitySwitch",)

#: 前端键 → 后端键的改名声明（附录 A-7 / Batch 3 请求形状盲区）。
#:
#: 后端 `CapabilityPolicyItem` 与 `CapabilityPolicyRequestItem` 都用 `enabled`，
#: 前端业务名是 `org_switch_enabled`。裁决是**不改后端字段名**（改了要动 6 个消费方 +
#: 迁移），由前端在 `setCapabilityPolicy` 做请求/响应双向映射；门禁这边声明改名，
#: 避免把同一业务概念的两侧字段名当成真的缺键/多键。
FIELD_RENAME: Final[dict[str, dict[str, str]]] = {
    "CapabilityPolicyItem": {"org_switch_enabled": "enabled"},
    "CapabilityPolicyRequestItem": {"org_switch_enabled": "enabled"},
}


class SchemaMissingError(RuntimeError):
    """白名单里声明的模型在 OpenAPI 文档中不存在。"""

    def __init__(self, missing: list[str]) -> None:
        """初始化。

        Args:
            missing: 缺失的 `components.schemas` 名称列表。
        """
        super().__init__("OpenAPI 文档缺少插件域模型: " + ", ".join(missing))
        self.missing = missing


def load_build_schema() -> Callable[[], dict[str, Any]]:
    """加载 `deploy/validate_openapi.py` 并返回它的 `build_schema`。

    用 `importlib.util.spec_from_file_location` 按**文件路径**加载，而不是
    `import validate_openapi`：脚本可从任意工作目录运行，且不污染 `sys.path`。
    该模块自带 `if __name__ == "__main__"` 守卫，因此导入不会执行 CLI。

    Returns:
        Callable[[], dict[str, Any]]: 构建 OpenAPI 文档的函数。

    Raises:
        FileNotFoundError: `deploy/validate_openapi.py` 不存在。
        AttributeError: 该模块没有 `build_schema`（被改名/删除）。
    """
    if not VALIDATE_OPENAPI_PATH.is_file():
        raise FileNotFoundError(f"找不到 {VALIDATE_OPENAPI_PATH}")

    spec = importlib.util.spec_from_file_location("validate_openapi", VALIDATE_OPENAPI_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {VALIDATE_OPENAPI_PATH} 创建模块规格")
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    build_schema = getattr(module, "build_schema", None)
    if not callable(build_schema):
        raise AttributeError("deploy/validate_openapi.py 未提供可调用的 build_schema()")
    return build_schema  # type: ignore[no-any-return]


def collect_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """收集一个 OpenAPI schema 的顶层属性。

    `$ref` / `allOf` / `anyOf` 只取键名，**不展开类型**（本批口径见设计 §2.1
    映射规则表）。`allOf`/`anyOf` 的每个分支若有 `properties` 键一并合并。

    Args:
        schema: `components.schemas[<name>]` 对象。

    Returns:
        dict[str, Any]: 属性名 → 属性 schema（顺序与文档一致）。
    """
    properties: dict[str, Any] = {}
    direct = schema.get("properties")
    if isinstance(direct, dict):
        properties.update(direct)

    for combinator in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(combinator)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            nested = branch.get("properties")
            if isinstance(nested, dict):
                properties.update(nested)

    return properties


def collect_property_enums(schema_name: str, properties: dict[str, Any]) -> dict[str, list[Any]]:
    """收集属性级 `enum`，供人工核对（**不判红**，见设计 §7-2）。

    Args:
        schema_name: OpenAPI 模型名（如 `MarketItem`）。
        properties: 该模型的属性表。

    Returns:
        dict[str, list[Any]]: `"<模型>.<属性>"` → 枚举取值。
    """
    enums: dict[str, list[Any]] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        values = prop_schema.get("enum")
        if isinstance(values, list):
            enums[f"{schema_name}.{prop_name}"] = list(values)
    return enums


def derive(document: dict[str, Any]) -> dict[str, Any]:
    """从 OpenAPI 文档派生出插件域契约（只承载键集合）。

    Args:
        document: `create_app().openapi()` 的返回值。

    Returns:
        dict[str, Any]: 可直接写入 `plugin-domain.gen.json` 的结构。

    Raises:
        SchemaMissingError: 白名单里的模型在文档中缺失。
    """
    components = document.get("components")
    schemas: dict[str, Any] = {}
    if isinstance(components, dict):
        raw_schemas = components.get("schemas")
        if isinstance(raw_schemas, dict):
            schemas = raw_schemas

    missing: list[str] = []
    interfaces: dict[str, dict[str, list[str]]] = {}
    backend_schema: dict[str, str] = {}
    enums_observed: dict[str, list[Any]] = {}

    for ts_name, schema_name in PLUGIN_DOMAIN_MAP.items():
        schema = schemas.get(schema_name)
        if not isinstance(schema, dict):
            missing.append(schema_name)
            continue

        properties = collect_properties(schema)
        if not properties:
            # 存在但一个属性都没有，与「缺失」同样致命：契约会退化成空键集合，
            # 门禁永远绿。按缺失处理，让问题在生成期就暴露。
            missing.append(f"{schema_name}(无 properties)")
            continue

        interfaces[ts_name] = {"keys": list(properties.keys())}
        backend_schema[ts_name] = schema_name
        enums_observed.update(collect_property_enums(schema_name, properties))

    if missing:
        raise SchemaMissingError(missing)

    return {
        "version": CONTRACT_VERSION,
        "generated_from": "create_app().openapi() @ components.schemas",
        "whitelist_version": WHITELIST_VERSION,
        "optionality_authority": "snapshot",
        "interfaces": interfaces,
        "enums_observed": enums_observed,
        "backend_schema": backend_schema,
        "excluded": {
            "task_domain": list(TASK_DOMAIN_EXCLUDED),
            "projection": list(PROJECTION_EXCLUDED),
        },
        "rename": {name: dict(mapping) for name, mapping in FIELD_RENAME.items()},
    }


def render(contract: dict[str, Any]) -> str:
    """把契约渲染为稳定的 JSON 文本（`indent=2` + 键排序 + 末尾换行）。

    Args:
        contract: `derive()` 的返回值。

    Returns:
        str: 文件内容。
    """
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数（不含程序名）。

    Returns:
        int: 退出码。
    """
    parser = argparse.ArgumentParser(description="从 OpenAPI 派生插件域契约（只承载键集合）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比对盘上 gen 文件是否与后端一致，不写盘（不一致退出 1）",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="打印到标准输出，不写盘",
    )
    args = parser.parse_args(argv)

    build_schema = load_build_schema()
    try:
        contract = derive(build_schema())
    except SchemaMissingError as error:
        print(f"[FAIL] {error}")
        print("[HINT] 白名单见 deploy/export_plugin_contract.py 的 PLUGIN_DOMAIN_MAP；")
        print("       后端改名/删除模型时，必须同步白名单与前端契约，不允许静默跳过。")
        return 2

    content = render(contract)
    interface_count = len(contract["interfaces"])
    field_count = sum(len(item["keys"]) for item in contract["interfaces"].values())

    if args.stdout:
        sys.stdout.write(content)
        return 0

    relative_path = GEN_OUTPUT_PATH.relative_to(REPO_ROOT).as_posix()

    if args.check:
        try:
            on_disk = GEN_OUTPUT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"[FAIL] 契约文件不存在：{relative_path}")
            print("[HINT] 先跑 python deploy/export_plugin_contract.py 生成并提交。")
            return 1
        if on_disk != content:
            print(f"[FAIL] {relative_path} 与后端 OpenAPI 不一致（后端改了但没重新生成）")
            print("[HINT] 跑 python deploy/export_plugin_contract.py 后把生成物一起提交。")
            return 1
        print(
            f"[OK]   插件域契约与后端一致：{interface_count} 个接口 / {field_count} 个字段"
            f"（{relative_path}）"
        )
        return 0

    GEN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEN_OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(
        f"[OK]   已生成 {relative_path}：{interface_count} 个接口 / {field_count} 个字段 / "
        f"{len(contract['enums_observed'])} 处属性级枚举（只记录，不判红）"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    sys.exit(main(sys.argv[1:]))
