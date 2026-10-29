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
2. 所在模块的 `pytestmark` 含 `pytest.mark.pg`（文件级标记）；或
3. **所在类**带 `@pytest.mark.pg` 装饰器（**类级标记对其内所有方法生效**，与 pytest 一致）。

> 类级这一条是 2026-10-29 补的：此前门禁只认函数自身装饰器与文件级 `pytestmark`，
> 于是**合法的类级 `@pytest.mark.pg` 会被判红**——pytest 会正确跳过这些用例，门禁却
> 报"缺标记"。守门脚本自己判错比漏标记更该修：它会逼人往方法上加重复标记，从此
> 再也证明不了门禁支持类级。

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
from collections.abc import Iterator
from pathlib import Path

#: 视为「需要真实 PostgreSQL」的夹具名
PG_FIXTURES: frozenset[str] = frozenset({"pg_dsn", "pg_engine", "pg_settings"})

#: 仓库根（deploy/ 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 默认测试根目录
DEFAULT_TESTS_ROOT = REPO_ROOT / "apps" / "adapter" / "tests"


def _unwrap(node: ast.AST) -> ast.AST:
    """剥掉装饰器外层的调用（`pytest.mark.pg(...)` → `pytest.mark.pg`）。

    形参收 `ast.AST` 而非 `ast.expr`：`_is_pg_mark()` 的实参来自 `ast.walk()`，
    后者产出的是任意 AST 节点（含 `ast.Load` 之类非表达式节点）。实参比形参宽时
    旧的 `ast.expr` 注解就是**写窄了**，而不是调用方有问题——`isinstance` 逐个分支
    判断的实现在运行时本就安全。

    Args:
        node: 装饰器或标记表达式（任意 AST 节点）。

    Returns:
        ast.AST: 去掉 Call 后的节点（非 Call 时原样返回）。
    """
    if isinstance(node, ast.Call):
        return node.func
    return node


def _is_pg_mark(node: ast.AST) -> bool:
    """判断表达式是否为 `pytest.mark.pg`。

    Args:
        node: 装饰器或标记表达式（`ast.walk()` 产出的任意 AST 节点均可）。

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


def _class_has_pg_mark(node: ast.ClassDef) -> bool:
    """判断类装饰器里是否含 `pytest.mark.pg`（类级标记）。

    Args:
        node: 类定义节点。

    Returns:
        bool: 类带 pg 标记返回 True。
    """
    return any(_is_pg_mark(decorator) for decorator in node.decorator_list)


def _iter_scoped_functions(
    node: ast.AST, inherited_pg: bool = False
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """深度优先遍历，产出 `(函数节点, 该函数是否由所在类继承了 pg 标记)`。

    **为什么不用 `ast.walk()`**：它是**无序遍历**，拿不到父子关系。用"遍历到 `ClassDef`
    就设一个全局标志"的写法，在多类文件里会把前一个类的标记漏给后一个类（`ast.walk`
    的兄弟节点顺序不保证），类里套类时更会串味。这里显式递归下降，标记的作用域天然
    与 AST 结构一致：进入类时把"类带了标记"并进 `inherited_pg`，离开类即失效。

    Args:
        node: 当前遍历到的节点（模块 / 类 / 函数 / 其它）。
        inherited_pg: 外层作用域是否已带 pg 标记。

    Returns:
        Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]: 函数与其继承状态。
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _iter_scoped_functions(child, inherited_pg or _class_has_pg_mark(child))
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child, inherited_pg
            # 函数体内还可能再定义函数（不是 pytest 用例，但别丢节点）
            yield from _iter_scoped_functions(child, inherited_pg)
        else:
            yield from _iter_scoped_functions(child, inherited_pg)


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
        for node, class_marked in _iter_scoped_functions(tree):
            if not node.name.startswith("test_"):
                continue
            if not (_fixture_params(node) & PG_FIXTURES):
                continue
            if module_marked or class_marked or _func_has_pg_decorator(node):
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
    print(
        "修复（三者任一）：函数加 @pytest.mark.pg，或所在类加 @pytest.mark.pg，"
        "或所属文件 pytestmark 加入 pytest.mark.pg。"
    )
    return 1


def _self_test() -> int:
    """门禁自检（族 B：成功时 exit 0）。

    三条样本缺一不可：
      ① 基础样本：函数级标记放行、无标记的被拦（防"恒过"）；
      ② **类级正样本**：只有一个类级 `@pytest.mark.pg`、方法上完全无标记 → 必须放行；
      ③ **类级负样本**：类上写的是 `@pytest.mark.integration`（不是 pg），且紧邻另一个
         同样用 PG 夹具的普通类 → 两个方法都必须被拦住。这条同时验证"标记不会串味"
         （若实现用全局标志，第二个类的方法会被误放行）。

    Returns:
        int: 0 自检通过 / 2 自检失败。
    """
    base_sample = """
import pytest

def pg_dsn():
    return "postgresql://x"

def test_dark(pg_dsn):
    pass

@pytest.mark.pg
def test_marked(pg_dsn):
    pass
"""
    class_marked_sample = """
import pytest

def pg_dsn():
    return "postgresql://x"


@pytest.mark.pg
class TestPgScoped:
    async def test_first(self, pg_dsn):
        pass

    def test_second(self, pg_dsn):
        pass
"""
    class_other_mark_sample = """
import pytest

def pg_dsn():
    return "postgresql://x"


@pytest.mark.integration
class TestNotPg:
    def test_in_class(self, pg_dsn):
        pass


class TestPlainSibling:
    def test_in_sibling(self, pg_dsn):
        pass
"""
    cases = [
        ("基础（函数级）", base_sample, {"test_dark"}),
        ("类级正样本（方法无标记）", class_marked_sample, set()),
        (
            "类级负样本（非 pg 标记 + 相邻类）",
            class_other_mark_sample,
            {"test_in_class", "test_in_sibling"},
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index, (label, sample, expected) in enumerate(cases):
            path = root / f"test_sample_{index}.py"
            path.write_text(sample, encoding="utf-8")
            names = {name for _path, _lineno, name in scan(path.parent) if _path == path}
            if names != expected:
                print(f"[FAIL] 自检「{label}」预期拦截 {sorted(expected)}，实际 {sorted(names)}")
                return 2
            print(f"[OK] 自检「{label}」拦截 {sorted(names) or '无（放行）'}（符合预期）")
    print("[OK] 自检通过：未标记用例被拦截、函数级与类级标记均被识别、标记不跨类串味")
    return 0


if __name__ == "__main__":
    sys.exit(main())
