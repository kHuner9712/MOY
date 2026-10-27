"""校验「需要真实 PostgreSQL 的用例」都带 `pg` 标记（审计 P2-16 自动断言）。

## 为什么需要它

未加 `--run-pg` 时，`apps/adapter/tests/conftest.py::pytest_collection_modifyitems`
**只跳过带 `pg` 标记的用例**；依赖 PG 夹具却没有标记的用例会在运行时由夹具
`pytest.skip` 静默跳过——它们既不在 `-m pg` 集合里，也不在「带库 skipped=0」
断言的覆盖范围内，形成「本地永远跳过、账面上没人知道」的口径黑洞。
本仓历史上就有 17 条这样的用例（审计 P2-16 实测：`-m pg` 收集 186 条，
无库实跑却跳过 203 条）。

## 规则

测试函数参数中出现 PG 夹具（`pg_dsn` / `pg_engine` / `pg_settings`）时，必须满足其一：

1. 函数带 `@pytest.mark.pg` 装饰器；或
2. 所在模块的 `pytestmark` 含 `pytest.mark.pg`（文件级标记）。

## 退出码

- 0：全部合规；
- 1：命中未标记用例（按行打印，可直接作 CI 门禁）。

自检（门禁可红证明）：`python deploy/validate_pg_markers.py --self-test`
——在临时目录构造「缺标记 / 带标记」两份样本，断言前者被拦住、后者放行。
"""

from __future__ import annotations

import argparse
import ast
import sys
import tempfile
from pathlib import Path

#: 视为「需要真实 PostgreSQL」的夹具名
PG_FIXTURES: frozenset[str] = frozenset({"pg_dsn", "pg_engine", "pg_settings"})

#: 仓库根（deploy/ 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 默认测试根目录
DEFAULT_TESTS_ROOT = REPO_ROOT / "apps" / "adapter" / "tests"


def _unwrap(node: ast.expr) -> ast.expr:
    """剥掉装饰器外层的调用（`pytest.mark.pg(...)` → `pytest.mark.pg`）。

    Args:
        node: 装饰器表达式。

    Returns:
        ast.expr: 去掉 Call 后的表达式。
    """
    if isinstance(node, ast.Call):
        return node.func
    return node


def _is_pg_mark(node: ast.expr) -> bool:
    """判断表达式是否为 `pytest.mark.pg`。

    Args:
        node: 装饰器或标记表达式。

    Returns:
        bool: 是 pg 标记返回 True。
    """
    target = _unwrap(node)
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "pg"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
    )


def _func_has_pg_decorator(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """判断函数装饰器里是否含 `pytest.mark.pg`。

    Args:
        function: 测试函数节点。

    Returns:
        bool: 含 pg 装饰器返回 True。
    """
    return any(_is_pg_mark(decorator) for decorator in function.decorator_list)


def _module_has_pg_mark(tree: ast.Module) -> bool:
    """判断模块级 `pytestmark` 是否含 pg 标记。

    Args:
        tree: 模块 AST。

    Returns:
        bool: 模块级含 pg 标记返回 True。
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if "pytestmark" not in targets:
            continue
        return any(_is_pg_mark(child) for child in ast.walk(node.value))
    return False


def _fixture_params(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """收集函数参数名（含仅位置 / 仅关键字参数）。

    Args:
        function: 测试函数节点。

    Returns:
        set[str]: 参数名集合。
    """
    arguments = function.args
    names = {arg.arg for arg in arguments.posonlyargs}
    names |= {arg.arg for arg in arguments.args}
    names |= {arg.arg for arg in arguments.kwonlyargs}
    return names


def scan(tests_root: Path) -> list[tuple[Path, int, str]]:
    """扫描测试目录，返回缺失 pg 标记的用例清单。

    Args:
        tests_root: pytest 测试根目录。

    Returns:
        list[tuple[Path, int, str]]: `(文件, 行号, 函数名)` 列表。
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in sorted(tests_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - 语法错误应由 ruff/pytest 先暴露
            print(f"[FAIL] 无法解析 {path}: {exc}")
            offenders.append((path, 0, "<syntax-error>"))
            continue
        module_marked = _module_has_pg_mark(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if not (_fixture_params(node) & PG_FIXTURES):
                continue
            if module_marked or _func_has_pg_decorator(node):
                continue
            offenders.append((path, node.lineno, node.name))
    return offenders


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 0 通过 / 1 命中未标记用例 / 2 自检失败。
    """
    parser = argparse.ArgumentParser(description="校验 PG 用例都带 pg 标记")
    parser.add_argument("--tests-root", type=Path, default=DEFAULT_TESTS_ROOT)
    parser.add_argument("--self-test", action="store_true", help="门禁可红证明")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    offenders = scan(args.tests_root)
    if not offenders:
        print("[OK] 所有依赖 PG 夹具的用例都带 pg 标记")
        return 0
    print(
        f"[FAIL] {len(offenders)} 条依赖 PG 夹具的用例缺少 pg 标记（未加 --run-pg 时会静默跳过）："
    )
    for path, lineno, name in offenders:
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:  # pragma: no cover - 仅自检临时目录会走这里
            shown = path
        print(f"  - {shown}:{lineno} {name}")
    print("修复：加 @pytest.mark.pg，或所属文件 pytestmark 加入 pytest.mark.pg。")
    return 1


def _self_test() -> int:
    """门禁自检：未标记样本必须被拦住，标记样本必须放行。

    Returns:
        int: 0 自检通过 / 2 自检失败。
    """
    sample = """
import pytest

def pg_dsn():
    return "postgresql://x"

def test_dark(pg_dsn):
    pass

@pytest.mark.pg
def test_marked(pg_dsn):
    pass
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "test_sample.py").write_text(sample, encoding="utf-8")
        offenders = scan(root)
        names = {name for _path, _lineno, name in offenders}
        if names != {"test_dark"}:
            print(f"[FAIL] 自检未按预期拦截：{sorted(names)}")
            return 2
    print("[OK] 自检通过：未标记用例被拦截、带标记用例放行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
