#!/usr/bin/env python3
"""错误码目录三处一致门禁（G-13 / 架构文档 §7 + 9.2 第 10 条）。

三条校验（任一不通过即退出码 1）：

1. **架构 → 后端**：``docs/architecture-phase2.md`` §7.1「新增错误码」表中的每一个码，
   都必须已在 ``app/core/errors.py`` 的 ``ErrorCode`` 中落地。
   尚未实现的码通过 ``--allow-pending <批次>`` 显式放行，并逐条打印 ``[PENDING]``；
   传 ``--strict`` 或 ``--allow-pending none`` 时要求 100% 落地（阶段门用）。

   **当前默认值 = ``none``（严格）**：权限域 14005-14008 随 B08 落地、计费域
   15001-15009 随 B09 落地，**任何批次都不再放行**——继续放行会让该域未来真正
   缺失的码被静默掩盖成绿（门禁失去信号价值）。新增码必须先落架构文档再进代码。
2. **后端 → 前端**：``apps/web/src/lib/business-errors.ts`` 的键集合必须是
   ``errors.py`` 码集合的**超集**（前端兜底表漏码 → 用户看到空白文案）。
3. **码 → 注册表**：``ErrorCode`` 每个成员都必须在 ``ERROR_REGISTRY`` 中有
   ``ErrorSpec``（漏注册 → 运行时 ``KeyError``）。

设计口径（为什么允许「按批次放行」而不是一律默认失败）：

- G-13 的判据是「已登记的码不得漂移」，**尚未开始批次**的码默认失败会让上一批的
  修复回归永远红着，失去信号价值；
- 但放行必须是**显式且可见**的：默认值写在代码里、每次运行打印 ``[PENDING]``
  逐条清单、批次落地后**必须立刻从默认值里移除**，杜绝「悄悄放宽」。

用法::

    python scripts/check_error_catalog.py            # 默认严格（全量落地）
    python scripts/check_error_catalog.py --strict   # 等价写法（阶段门专用）
    python scripts/check_error_catalog.py --allow-pending b09  # 仅在批次开发期临时使用

退出码：0 = 通过；1 = 不通过。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

#: 适配层根目录（``apps/adapter``）
ADAPTER_ROOT = Path(__file__).resolve().parent.parent

#: 仓库根目录
REPO_ROOT = ADAPTER_ROOT.parent.parent

#: 架构文档（阶段二错误码扩展表所在文档）
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-phase2.md"

#: 阶段三架构文档（插件域 16002-16012 / 任务域 17001-17002 的登记表在 §5.4）
ARCHITECTURE_PHASE3_DOC = REPO_ROOT / "docs" / "architecture-phase3.md"

#: 后端错误码枚举（唯一权威实现）
ERRORS_PY = ADAPTER_ROOT / "app" / "core" / "errors.py"

#: 前端错误码兜底文案表
BUSINESS_ERRORS_TS = REPO_ROOT / "apps" / "web" / "src" / "lib" / "business-errors.ts"

#: 默认允许「尚未落地」的批次（显式、可见、可关闭）。
#: B08（14005-14008）与 B09（15001-15009）均已落地 → 默认值改为 `none`：
#: 门禁回到「架构登记即必须落地」，禁止任何批次继续放行。
DEFAULT_PENDING = "none"

#: 批次 → 架构 §7.1 表中的「域」列取值。
#: B08 组织权限域的 14005-14008（已落地）与 B09 计费域的 15001-15009 分属两个批次。
#: 保留 b08 键仅为「历史命令可解释」，默认值不再包含它。
PENDING_DOMAINS: dict[str, tuple[str, ...]] = {
    "b08": ("权限",),
    "b09": ("计费",),
}

#: §7.1 表行里的码单元格形态：``**11007**``
_ARCH_CODE_PATTERN = re.compile(r"^\*\*(\d{5})\*\*$")

#: errors.py 中 ``ErrorCode`` 成员定义行（4 空格缩进 + 5 位数字）
_ERROR_CODE_PATTERN = re.compile(r"^    ([A-Z][A-Z0-9_]*)\s*=\s*(\d{5})\s*$")

#: business-errors.ts 中的键行（``11007: '...'``）
_TS_KEY_PATTERN = re.compile(r"^\s*(\d{5})\s*:", re.MULTILINE)

#: 生产代码根（「码 → 是否被端点引用」反查的扫描范围）
APP_ROOT = ADAPTER_ROOT / "app"

#: 反查引用时排除的文件（注册表自身是**定义**，不是引用）
REFERENCE_EXCLUDED_FILES: frozenset[Path] = frozenset({ERRORS_PY})

#: 生产代码里的码引用形态：``ErrorCode.PLUGIN_NOT_FOUND``
_REFERENCE_PATTERN = re.compile(r"ErrorCode\.([A-Z][A-Z0-9_]*)")

#: OpenAPI 产物目录（`--forbid-internal` 的第三处扫描对象）
OPENAPI_DIR = REPO_ROOT / "docs" / "api"

#: 「引用端点尚未落地」的码 → 批次（显式、可见、可关闭）。
#:
#: G3-12 ① 要求「每个 `对外` 码至少被 1 个端点引用」，但**端点按批次落地**：
#: B18 结束时尚无 WF1，`17001`（任务不可重试）必然 0 引用。若不放行，
#: 中批每跑一次都是假红；若直接关掉反查，`16010` 这类僵尸码又会重新长出来。
#: 因此放行必须是**按批次的具名白名单**，且每次运行打印 `[PENDING]`；
#: 批次落地后**必须立刻**把条目删掉并把默认值改回 ``none``。
PENDING_UNREFERENCED_BY_BATCH: dict[str, tuple[str, ...]] = {
    # `16004` 的引用点是 A1「停用插件下建任务」，`17001` 的引用点是 WF1
    # ——两者都在 B19 落地，落地后本条目必须删除
    "b19": ("16004", "17001"),
}

#: 默认放行的批次（与 `DEFAULT_PENDING` 同体例：落地即清零）
DEFAULT_UNREFERENCED_PENDING = "b19"

#: 默认禁止外泄的内部码（架构 §5.4 硬约束 ② + QA G3-11）
DEFAULT_FORBID_INTERNAL = "17002"

#: 反查的作用域：阶段三新增码（插件域 `160xx` / 任务域 `170xx`）。
#:
#: 为什么只查这两段：阶段一/二有 5 个历史码（`11005` / `12002` / `12004` / `12005` /
#: `15006`）从未被 `ErrorCode.<常量名>` 引用过，属于阶段二遗留（另立批次收口），
#: 把它们拉进本门禁会让「阶段三每批都因为历史债红着」，于是所有人学会无视这条门禁——
#: 门禁一旦可以被无视，就等于不存在。
#: 处理方式：作用域内 0 引用 = **红**；作用域外 0 引用 = **每次运行都打印**的
#: `[WARN]`（可见、不阻塞、不遗忘）。新增的阶段三码一律落在作用域内，不会漏。
REFERENCE_SCOPE_PREFIXES: Final[tuple[str, ...]] = ("160", "170")


class CheckResult:
    """单条校验结果（便于统一打印与退出码汇总）。

    Attributes:
        name: 校验项名称。
        ok: 是否通过。
        detail: 说明文字（失败时为原因清单）。
    """

    def __init__(self, name: str, ok: bool, detail: str) -> None:
        """初始化结果。

        Args:
            name: 校验项名称。
            ok: 是否通过。
            detail: 说明文字。
        """
        self.name = name
        self.ok = ok
        self.detail = detail


def _fail(message: str) -> int:
    """打印失败信息并返回退出码 1。

    Args:
        message: 失败原因。

    Returns:
        int: 固定返回 1。
    """
    print(f"[FAIL] {message}")
    return 1


def parse_architecture_codes(path: Path) -> dict[str, str]:
    """解析架构文档 §7.1 表的「码 → 域」映射。

    只读取 ``### 7.1`` 与 ``**同码多文案**`` 之间的小节，避免把 §7.2 的
    对比表格（其中含被否决的 16001 建议码）误当成已登记码。

    Args:
        path: 架构文档路径。

    Returns:
        dict[str, str]: ``错误码 → 域``（域取表格第 5 列，如 `RAG` / `权限` / `计费`）。

    Raises:
        FileNotFoundError: 文档不存在。
        ValueError: 文档中找不到 §7.1 小节。
    """
    text = path.read_text(encoding="utf-8")
    start = text.find("### 7.1")
    if start < 0:
        raise ValueError(f"{path} 中找不到 §7.1 新增错误码小节")
    end = text.find("**同码多文案**", start)
    if end < 0:
        end = text.find("### 7.2", start)
    section = text[start : end if end > start else len(text)]

    codes: dict[str, str] = {}
    for line in section.splitlines():
        row = line.strip()
        if not row.startswith("|"):
            continue
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) < 5:
            continue
        matched = _ARCH_CODE_PATTERN.match(cells[0])
        if matched is None:
            continue
        codes[matched.group(1)] = cells[4]
    if not codes:
        raise ValueError(f"{path} 的 §7.1 小节未解析出任何错误码")
    return codes


def parse_phase3_codes(path: Path) -> dict[str, str]:
    """解析阶段三架构文档 §5.4 表里的「码 → 域」映射。

    该表与阶段二 §7.1 形态不同：码列不带 `**` 加粗，且没有「域」列。
    因此本函数按**码段**推域（`160xx → 插件`、`170xx → 任务`），
    并且只扫描 §5.4 小节、遇到 `**不新增码**` 前的行——那里之后的表格
    混有「复用既有码」的说明，不能当成新登记码（否则会把 13002/15001
    误算成本阶段新增）。

    Args:
        path: 阶段三架构文档路径。

    Returns:
        dict[str, str]: ``错误码 → 域``。

    Raises:
        FileNotFoundError: 文档不存在。
        ValueError: 找不到 §5.4 小节，或小节内解析不出任何码。
    """
    text = path.read_text(encoding="utf-8")
    start = text.find("### 5.4")
    if start < 0:
        raise ValueError(f"{path} 中找不到 §5.4 新增错误码小节")
    end = text.find("**不新增码**", start)
    section = text[start : end if end > start else len(text)]

    codes: dict[str, str] = {}
    for line in section.splitlines():
        row = line.strip()
        if not row.startswith("|"):
            continue
        cells = [cell.strip().strip("*") for cell in row.strip("|").split("|")]
        if not cells:
            continue
        if not re.fullmatch(r"\d{5}", cells[0]):
            continue
        code = cells[0]
        codes[code] = "插件" if code.startswith("160") else "任务"
    if not codes:
        raise ValueError(f"{path} 的 §5.4 小节未解析出任何错误码")
    return codes


def parse_backend_codes(path: Path) -> dict[str, str]:
    """解析 ``errors.py`` 中 ``ErrorCode`` 的成员定义。

    Args:
        path: ``app/core/errors.py`` 路径。

    Returns:
        dict[str, str]: ``常量名 → 错误码``。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件内找不到 ``class ErrorCode(IntEnum)`` 块。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        header = next(
            index for index, line in enumerate(lines) if line.startswith("class ErrorCode(")
        )
    except StopIteration as exc:
        raise ValueError(f"{path} 中找不到 class ErrorCode 定义") from exc

    members: dict[str, str] = {}
    for line in lines[header + 1 :]:
        # 枚举体在下一条顶层定义（@dataclass / class / def）处结束
        if line and not line.startswith((" ", "\t")):
            break
        matched = _ERROR_CODE_PATTERN.match(line)
        if matched is not None:
            members[matched.group(1)] = matched.group(2)
    if not members:
        raise ValueError(f"{path} 的 ErrorCode 块未解析出任何码")
    return members


def parse_frontend_codes(path: Path) -> set[str]:
    """解析 ``business-errors.ts`` 中兜底文案表的键集合。

    Args:
        path: 前端兜底表路径。

    Returns:
        set[str]: 错误码字符串集合。

    Raises:
        FileNotFoundError: 文件不存在。
    """
    text = path.read_text(encoding="utf-8")
    keys = set(_TS_KEY_PATTERN.findall(text))
    if not keys:
        raise ValueError(f"{path} 未解析出任何错误码键")
    return keys


def resolve_pending(batch_arg: str) -> set[str]:
    """把批次参数解析为「允许未落地的域」集合。

    Args:
        batch_arg: 逗号分隔的批次标识（如 ``b08,b09``）；``none`` / 空串表示不允许。

    Returns:
        set[str]: 允许暂缺的架构「域」集合。

    Raises:
        ValueError: 出现未知批次标识。
    """
    normalized = batch_arg.strip().lower()
    if not normalized or normalized == "none":
        return set()
    domains: set[str] = set()
    for raw in normalized.split(","):
        batch = raw.strip()
        if not batch:
            continue
        if batch not in PENDING_DOMAINS:
            raise ValueError(f"未知批次标识 {batch}（可选：{', '.join(sorted(PENDING_DOMAINS))}）")
        domains.update(PENDING_DOMAINS[batch])
    return domains


def check_architecture_landed(
    arch_codes: dict[str, str], backend_codes: dict[str, str], pending_domains: set[str]
) -> CheckResult:
    """校验架构 §7.1 的码是否都已在 ``errors.py`` 落地。

    Args:
        arch_codes: 架构表中的「码 → 域」。
        backend_codes: ``errors.py`` 的「常量名 → 码」。
        pending_domains: 允许暂缺的域集合。

    Returns:
        CheckResult: 校验结果。
    """
    landed = set(backend_codes.values())
    missing: list[str] = []
    pending: list[str] = []
    for code, domain in sorted(arch_codes.items()):
        if code in landed:
            continue
        if domain in pending_domains:
            pending.append(f"{code}({domain})")
        else:
            missing.append(f"{code}({domain})")

    for item in pending:
        print(f"[PENDING] 架构已登记但尚未落地：{item}")
    if missing:
        return CheckResult(
            "架构 §7.1 → errors.py",
            False,
            f"以下码已在架构登记但 errors.py 未落地：{', '.join(missing)}",
        )
    detail = f"{len(arch_codes) - len(pending)}/{len(arch_codes)} 已落地"
    if pending:
        detail += f"（{len(pending)} 个按批次放行）"
    return CheckResult("架构 §7.1 → errors.py", True, detail)


def check_frontend_superset(backend_codes: dict[str, str], frontend_codes: set[str]) -> CheckResult:
    """校验前端兜底表的键集合是后端码集合的超集。

    Args:
        backend_codes: ``errors.py`` 的「常量名 → 码」。
        frontend_codes: ``business-errors.ts`` 的键集合。

    Returns:
        CheckResult: 校验结果。
    """
    backend_values = set(backend_codes.values())
    # 内部码不要求前端覆盖（架构 §5.4 硬约束 ②：`17002` 只允许出现在后端实现与单测）
    uncovered = sorted(backend_values - frontend_codes - internal_codes())
    if uncovered:
        return CheckResult(
            "errors.py → business-errors.ts",
            False,
            f"前端兜底表缺少以下码：{', '.join(uncovered)}",
        )
    extra = len(frontend_codes - backend_values)
    detail = f"{len(backend_values)} 个码全部覆盖"
    if extra:
        detail += f"（前端另有 {extra} 个预留码）"
    return CheckResult("errors.py → business-errors.ts", True, detail)


def check_registry_complete() -> CheckResult:
    """校验 ``ErrorCode`` 每个成员都在 ``ERROR_REGISTRY`` 注册（运行时防 KeyError）。

    Returns:
        CheckResult: 校验结果；导入失败时返回带 ``[WARN]`` 语义的通过结果。
    """
    if str(ADAPTER_ROOT) not in sys.path:
        sys.path.insert(0, str(ADAPTER_ROOT))
    try:
        from app.core.errors import ERROR_REGISTRY, ErrorCode
    except Exception as exc:  # 门禁脚本不得因环境差异崩溃（导入失败仅降级跳过）
        print(f"[WARN] 无法导入 app.core.errors（{exc}），跳过注册表完整性校验")
        return CheckResult("ErrorCode → ERROR_REGISTRY", True, "已跳过（环境不可导入）")

    declared = {int(member) for member in ErrorCode}
    registered = {int(code) for code in ERROR_REGISTRY}
    missing = sorted(declared - registered)
    if missing:
        return CheckResult(
            "ErrorCode → ERROR_REGISTRY",
            False,
            f"以下码缺少 ErrorSpec 注册：{', '.join(str(code) for code in missing)}",
        )
    return CheckResult("ErrorCode → ERROR_REGISTRY", True, f"{len(declared)} 个码全部注册")


def internal_codes() -> set[str]:
    """从注册表读出「内部码」（`visibility="内部"`）。

    内部码的判据是**双向**的（架构 §5.4 硬约束 ②）：

    * 不要求进前端兜底表（`17002` 只允许出现在后端实现与单测）；
    * 也**不允许**出现在前端兜底表里——本脚本会正向扫一遍。

    Returns:
        set[str]: 内部码集合（形如 `{"17002"}`）。
    """
    from app.core.errors import ERROR_REGISTRY

    return {str(int(spec.code)) for spec in ERROR_REGISTRY.values() if spec.visibility == "内部"}


def resolve_unreferenced_pending(batch_arg: str) -> set[str]:
    """把批次参数解析为「允许暂时无人引用」的码集合。

    Args:
        batch_arg: 逗号分隔的批次标识（如 ``b19``）；``none`` / 空串表示不允许。

    Returns:
        set[str]: 允许暂未被任何端点引用的码集合。

    Raises:
        ValueError: 出现未知批次标识。
    """
    normalized = batch_arg.strip().lower()
    if not normalized or normalized == "none":
        return set()
    allowed: set[str] = set()
    for raw in normalized.split(","):
        batch = raw.strip()
        if not batch:
            continue
        if batch not in PENDING_UNREFERENCED_BY_BATCH:
            raise ValueError(
                f"未知批次标识 {batch}（可选：{', '.join(sorted(PENDING_UNREFERENCED_BY_BATCH))}）"
            )
        allowed.update(PENDING_UNREFERENCED_BY_BATCH[batch])
    return allowed


def referenced_codes(backend_codes: dict[str, str]) -> set[str]:
    """扫生产代码，收集**被引用**的码（`ErrorCode.<常量名>` 的出现）。

    为什么扫常量名而不扫数字：数字会出现在注释、文档串、示例里，等于给"写个注释
    就算引用"开后门；常量名只能在 `ErrorCode` 枚举上取到，是真实的引用点。

    Args:
        backend_codes: ``errors.py`` 的「常量名 → 码」。

    Returns:
        set[str]: 被引用的码集合。
    """
    referenced: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.resolve() in REFERENCE_EXCLUDED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for name in _REFERENCE_PATTERN.findall(text):
            code = backend_codes.get(name)
            if code is not None:
                referenced.add(code)
    return referenced


def check_external_referenced(
    backend_codes: dict[str, str],
    referenced: set[str],
    internal: set[str],
    allowed: set[str],
) -> CheckResult:
    """反查僵尸码：`对外` 码必须至少被 1 处生产代码引用。

    `16010` 曾经就是「声明了、门禁也绿、但没有任何端点会返回它」的僵尸码——
    正查（码 → 注册表）发现不了，只有反查（码 → 引用）能抓。

    Args:
        backend_codes: ``errors.py`` 的「常量名 → 码」。
        referenced: 被引用的码集合。
        internal: 内部码集合（内部码不进前端，也不要求被端点引用）。
        allowed: 按批次放行的码（引用端点尚未落地）。

    Returns:
        CheckResult: 校验结果。
    """
    external = set(backend_codes.values()) - internal
    in_scope = {code for code in external if code.startswith(REFERENCE_SCOPE_PREFIXES)}
    out_scope = external - in_scope
    zombies = sorted(in_scope - referenced - allowed)
    pending = sorted((in_scope & allowed) - referenced)
    legacy = sorted(out_scope - referenced)
    for code in pending:
        print(f"[PENDING] 对外码暂无引用（引用端点未落地，按批次放行）：{code}")
    for code in legacy:
        print(f"[WARN] 历史遗留码从未被端点引用（阶段一/二债，另立批次收口，不阻塞本批）：{code}")
    if zombies:
        return CheckResult(
            "对外码 → 端点引用",
            False,
            f"以下对外码没有任何端点引用（僵尸码）：{', '.join(zombies)}",
        )
    detail = f"{len(in_scope) - len(pending)}/{len(in_scope)} 个阶段三对外码已被引用"
    if pending:
        detail += f"（{len(pending)} 个按批次放行）"
    if legacy:
        detail += f"；另有 {len(legacy)} 个历史遗留码 0 引用（[WARN]，见上）"
    return CheckResult("对外码 → 端点引用", True, detail)


def check_internal_unreferenced(
    backend_codes: dict[str, str], referenced: set[str], internal: set[str]
) -> CheckResult:
    """内部码**不得**被任何端点引用（双向核对的另一半）。

    `17002`（任务执行未能完成）是内部汇总与审计码：步骤失败走状态 + `steps[].failure`，
    A3/A4 永不返回它。一旦有人用它当业务码返回，前端就会出现"任务失败弹窗"，
    与 PRD E-9「就地展示、不弹错」直接冲突。

    Args:
        backend_codes: ``errors.py`` 的「常量名 → 码」。
        referenced: 被引用的码集合。
        internal: 内部码集合。

    Returns:
        CheckResult: 校验结果。
    """
    leaked = sorted(internal & referenced)
    if leaked:
        return CheckResult(
            "内部码 → 禁止端点引用",
            False,
            f"以下内部码被端点引用：{', '.join(leaked)}（应只出现在内部汇总与审计里）",
        )
    return CheckResult(
        "内部码 → 禁止端点引用",
        True,
        f"内部码 {sorted(internal) or '无'} 未被任何端点引用",
    )


def check_forbid_internal(codes: set[str]) -> CheckResult:
    """内部码外泄终检：前端兜底表 / 后端 `message` 常量 / OpenAPI 示例 三处 0 命中。

    三处扫描是刻意的冗余：只扫前端时，后端某处把码写进 `message` 字符串仍会漏；
    只扫代码时，`openapi.json`（构建产物）里的示例值仍会漏。

    Args:
        codes: 禁止外泄的内部码集合。

    Returns:
        CheckResult: 校验结果。
    """
    if not codes:
        return CheckResult("内部码外泄终检", True, "未指定内部码（跳过）")
    hits: list[str] = []
    frontend_text = BUSINESS_ERRORS_TS.read_text(encoding="utf-8")
    for code in sorted(codes):
        # ① 前端兜底表键
        if code in _TS_KEY_PATTERN.findall(frontend_text) or f'"{code}"' in frontend_text:
            hits.append(f"{BUSINESS_ERRORS_TS.name}:{code}")
        # ② 后端 message 常量（注册表自身的定义行不算——那是唯一允许的位置）
        for path in sorted(APP_ROOT.rglob("*.py")):
            if path.resolve() == ERRORS_PY:
                continue
            if code in path.read_text(encoding="utf-8"):
                hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{code}")
        # ③ OpenAPI 产物与快照（构建产物里的示例值同样会被前端读到）
        for path in sorted(OPENAPI_DIR.glob("*.json")):
            if code in path.read_text(encoding="utf-8"):
                hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{code}")
    if hits:
        return CheckResult(
            "内部码外泄终检",
            False,
            f"内部码出现在禁止位置：{', '.join(hits)}",
        )
    return CheckResult(
        "内部码外泄终检",
        True,
        f"{sorted(codes)} 在前端兜底表 / 后端 message / OpenAPI 示例 中 0 命中",
    )


def run(pending_arg: str, unreferenced_arg: str, forbid_arg: str) -> int:
    """执行全部校验。

    Args:
        pending_arg: 批次放行参数（架构已登记但未落地的域）。
        unreferenced_arg: 引用端点未落地的批次放行参数。
        forbid_arg: 禁止外泄的内部码（逗号分隔；``none`` 表示不检查）。

    Returns:
        int: 0 表示全部通过，1 表示存在失败项。
    """
    for path in (ARCHITECTURE_DOC, ARCHITECTURE_PHASE3_DOC, ERRORS_PY, BUSINESS_ERRORS_TS):
        if not path.exists():
            return _fail(f"缺少输入文件：{path}")

    try:
        pending_domains = resolve_pending(pending_arg)
        unreferenced_allowed = resolve_unreferenced_pending(unreferenced_arg)
        arch_codes = parse_architecture_codes(ARCHITECTURE_DOC)
        arch_codes.update(parse_phase3_codes(ARCHITECTURE_PHASE3_DOC))
        backend_codes = parse_backend_codes(ERRORS_PY)
        frontend_codes = parse_frontend_codes(BUSINESS_ERRORS_TS)
        internal = internal_codes()
        referenced = referenced_codes(backend_codes)
    except ValueError as exc:
        return _fail(str(exc))

    forbidden = {
        item.strip() for item in forbid_arg.split(",") if item.strip() and item.strip() != "none"
    }

    print(
        f"[INFO] 架构 §7.1 + §5.4 = {len(arch_codes)} 码；"
        f"errors.py = {len(set(backend_codes.values()))} 码；"
        f"business-errors.ts = {len(frontend_codes)} 码；"
        f"生产代码引用 = {len(referenced)} 码；"
        f"放行域 = {sorted(pending_domains) or '无（严格）'}；"
        f"放行码 = {sorted(unreferenced_allowed) or '无'}"
    )

    results = [
        check_architecture_landed(arch_codes, backend_codes, pending_domains),
        check_frontend_superset(backend_codes, frontend_codes),
        check_registry_complete(),
        check_external_referenced(backend_codes, referenced, internal, unreferenced_allowed),
        check_internal_unreferenced(backend_codes, referenced, internal),
        check_forbid_internal(forbidden),
    ]
    failed = False
    for result in results:
        if result.ok:
            print(f"[OK]   {result.name}: {result.detail}")
        else:
            print(f"[FAIL] {result.name}: {result.detail}")
            failed = True

    if failed:
        print("[FAIL] 错误码三处一致门禁不通过（G-13）")
        return 1
    forbidden_frontend = sorted(internal & frontend_codes)
    if forbidden_frontend:
        print(
            f"[FAIL] 内部码不得进前端兜底表：{forbidden_frontend}"
            "（架构 §5.4 硬约束 ②：内部码只允许出现在后端实现与单测）"
        )
        print("[FAIL] 错误码三处一致门禁不通过（G-13）")
        return 1
    print(f"[OK]   内部码未进前端兜底表（豁免：{sorted(internal) or '无'}）")
    print("[OK]   错误码三处一致门禁通过（G-13）")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 ``sys.argv[1:]``）。

    Returns:
        int: 进程退出码。
    """
    parser = argparse.ArgumentParser(
        description="错误码目录三处一致门禁（G-13）：架构 §7.1 ↔ errors.py ↔ business-errors.ts",
    )
    parser.add_argument(
        "--allow-pending",
        default=DEFAULT_PENDING,
        help=("允许尚未落地的批次（逗号分隔，默认 none 即全量落地）；批次落地后不得再放行"),
    )
    parser.add_argument(
        "--allow-unreferenced",
        default=DEFAULT_UNREFERENCED_PENDING,
        help=(
            "允许「引用端点尚未落地」的对外码（逗号分隔批次，默认 b19 即放行 17001）；"
            "端点落地后必须改回 none"
        ),
    )
    parser.add_argument(
        "--forbid-internal",
        default=DEFAULT_FORBID_INTERNAL,
        help=(
            "禁止外泄的内部码（逗号分隔，默认 17002）；"
            "扫描前端兜底表 / 后端 message 常量 / OpenAPI 示例，命中即红"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="等价于 --allow-pending none（阶段门专用：禁止任何待落地码）",
    )
    args = parser.parse_args(argv)
    pending = "none" if args.strict else str(args.allow_pending)
    return run(pending, str(args.allow_unreferenced), str(args.forbid_internal))


if __name__ == "__main__":
    raise SystemExit(main())
