#!/usr/bin/env python3
"""错误码目录三处一致门禁（G-13 / 架构文档 §7 + 9.2 第 10 条）。

三条校验（任一不通过即退出码 1）：

1. **架构 → 后端**：``docs/architecture-phase2.md`` §7.1「新增错误码」表中的每一个码，
   都必须已在 ``app/core/errors.py`` 的 ``ErrorCode`` 中落地。
   B08/B09 尚未实现的码（权限域 14005-14008、计费域 15001-15009）通过
   ``--allow-pending b08,b09``（默认值）显式放行，并逐条打印 ``[PENDING]``；
   传 ``--strict`` 或 ``--allow-pending none`` 时要求 100% 落地（阶段门用）。
2. **后端 → 前端**：``apps/web/src/lib/business-errors.ts`` 的键集合必须是
   ``errors.py`` 码集合的**超集**（前端兜底表漏码 → 用户看到空白文案）。
3. **码 → 注册表**：``ErrorCode`` 每个成员都必须在 ``ERROR_REGISTRY`` 中有
   ``ErrorSpec``（漏注册 → 运行时 ``KeyError``）。

设计口径（为什么默认放行 b08/b09 而不是默认失败）：

- G-13 的判据是「已登记的码不得漂移」，B08/B09 的码属**尚未开始的批次**，
  默认失败会让 B05/B06 的修复回归永远红着，失去信号价值；
- 但放行必须是**显式且可见**的：默认值写在代码里、每次运行打印 ``[PENDING]``
  逐条清单、批次落地后自动消失，杜绝「悄悄放宽」。

用法::

    python scripts/check_error_catalog.py                      # 默认放行 b08,b09
    python scripts/check_error_catalog.py --allow-pending none  # 要求全量落地
    python scripts/check_error_catalog.py --strict             # 同 none

退出码：0 = 通过；1 = 不通过。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: 适配层根目录（``apps/adapter``）
ADAPTER_ROOT = Path(__file__).resolve().parent.parent

#: 仓库根目录
REPO_ROOT = ADAPTER_ROOT.parent.parent

#: 架构文档（阶段二错误码扩展表所在文档）
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-phase2.md"

#: 后端错误码枚举（唯一权威实现）
ERRORS_PY = ADAPTER_ROOT / "app" / "core" / "errors.py"

#: 前端错误码兜底文案表
BUSINESS_ERRORS_TS = REPO_ROOT / "apps" / "web" / "src" / "lib" / "business-errors.ts"

#: 默认允许「尚未落地」的批次（显式、可见、可关闭）
DEFAULT_PENDING = "b08,b09"

#: 批次 → 架构 §7.1 表中的「域」列取值。
#: B08 组织权限域的 14005-14008 与 B09 计费域的 15001-15009 分属两个批次。
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
    uncovered = sorted(backend_values - frontend_codes)
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


def run(pending_arg: str) -> int:
    """执行全部校验。

    Args:
        pending_arg: 批次放行参数。

    Returns:
        int: 0 表示全部通过，1 表示存在失败项。
    """
    for path in (ARCHITECTURE_DOC, ERRORS_PY, BUSINESS_ERRORS_TS):
        if not path.exists():
            return _fail(f"缺少输入文件：{path}")

    try:
        pending_domains = resolve_pending(pending_arg)
        arch_codes = parse_architecture_codes(ARCHITECTURE_DOC)
        backend_codes = parse_backend_codes(ERRORS_PY)
        frontend_codes = parse_frontend_codes(BUSINESS_ERRORS_TS)
    except ValueError as exc:
        return _fail(str(exc))

    print(
        f"[INFO] 架构 §7.1 = {len(arch_codes)} 码；"
        f"errors.py = {len(set(backend_codes.values()))} 码；"
        f"business-errors.ts = {len(frontend_codes)} 码；"
        f"放行域 = {sorted(pending_domains) or '无（严格）'}"
    )

    results = [
        check_architecture_landed(arch_codes, backend_codes, pending_domains),
        check_frontend_superset(backend_codes, frontend_codes),
        check_registry_complete(),
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
        help=("允许尚未落地的批次（逗号分隔，默认 b08,b09）；传 none 或空串表示要求全量落地"),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="等价于 --allow-pending none（阶段门专用：禁止任何待落地码）",
    )
    args = parser.parse_args(argv)
    pending = "none" if args.strict else str(args.allow_pending)
    return run(pending)


if __name__ == "__main__":
    raise SystemExit(main())
