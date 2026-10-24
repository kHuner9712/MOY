"""无数据库环境下的 `tenant_scope` 内存替身（数据权利 / 保留期用例的公共夹具）。

## 为什么需要它

`tenant_data_service`（TD1/TD2）与 `retention_cleanup` 的全部业务逻辑都写在
`app` 层，但每条语句都要经 `tenant_scope()` 开事务。本机没有测试库，
这批逻辑整片处于"零覆盖"状态。于是本文件提供一个**只模拟 SQL 引擎**的替身，
让 app 层代码原样跑起来：

- 被测对象仍是真实代码：分批删除的循环终止条件、键集分页的游标推进、
  zip 拼装与清单、两段式删除证明、日志与审计写入；
- 替身只做四件事：按 `tenant_id` 过滤行、记住写入了什么、回放 keyset 分页、
  返回 `rowcount`。

## 它**不**解释什么（重要，避免误读为"SQL 也被测了"）

替身不解析 `WHERE` 谓词，因此**不判定"哪一行过期 / 哪一个库不可见"**：

- 过期判据（`t0.created_at < ...`）与权限判据（`ctid IN (...)`）属 SQL 层事实，
  由 `tests/integration/test_retention_export_delete.py` 在真库上覆盖（本机 skip）；
- 本文件支撑的断言一律是**编排行为**：命中哪张表、分了几批、每批多少行、
  事务边界、写入顺序、返回值与副产物。

特例是导出路径的 keyset 分页：`_fetch_page()` 的 SQL 里带 `ORDER BY t0.<ts>, t0.<id>`，
替身据此真正做"游标之后的下一页"，因此"超过 1000 行走第二页"这条逻辑是被真实执行的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 表名 / 列名的白名单形态（与 `app.db.repositories.base.assert_identifier` 同口径）
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_COUNT_RE = re.compile(rf"SELECT\s+count\(\*\)\s+FROM\s+(?P<table>{_IDENT})", re.IGNORECASE)
_DELETE_RE = re.compile(rf"^\s*DELETE\s+FROM\s+(?P<table>{_IDENT})", re.IGNORECASE)
_INSERT_RE = re.compile(rf"^\s*INSERT\s+INTO\s+(?P<table>{_IDENT})", re.IGNORECASE)
_UPDATE_RE = re.compile(rf"^\s*UPDATE\s+(?P<table>{_IDENT})", re.IGNORECASE)
#: 导出取数语句：`SELECT <列> FROM <表> AS t0 ... ORDER BY t0.<ts>, t0.<id> LIMIT ...`
_KEYSET_RE = re.compile(
    rf"SELECT\s+(?P<columns>.+?)\s+FROM\s+(?P<table>{_IDENT})\s+AS\s+t0.+?"
    rf"ORDER\s+BY\s+t0\.(?P<ts>{_IDENT})\s*,\s*t0\.(?P<id>{_IDENT})",
    re.IGNORECASE | re.DOTALL,
)
_SELECT_RE = re.compile(
    rf"SELECT\s+(?P<columns>.+?)\s+FROM\s+(?P<table>{_IDENT})(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)

#: 未显式给出单批行数时的缺省值（与 `PAGE_SIZE` / `DEFAULT_BATCH_SIZE` 一致）
_DEFAULT_LIMIT = 1000


def normalize(sql: str) -> str:
    """把多行 SQL 压成单行（断言与失败信息更可读）。

    Args:
        sql: 原始语句。

    Returns:
        str: 空白归一化后的语句。
    """
    return " ".join(sql.split())


@dataclass
class FakeResult:
    """`execute()` 的返回值（业务层只读 `rowcount`，所以只提供它）。

    Attributes:
        rowcount: 受影响行数。
    """

    rowcount: int = 0


@dataclass
class FakeDatabase:
    """极简内存"库"：表 → 行列表，外加全部语句与事务边界的记录。

    Attributes:
        tables: 表名 → 行（`dict`）。
        statements: 按执行顺序记录的 `(单行 SQL, 参数)`。
        scopes: 按开启顺序记录的 `(tenant_id, read_only)`，用于断事务边界与次数。
        failures: 表名 → 该表 `DELETE` 时抛出的异常（模拟"应用角色无 DELETE 权限"）。
        racing_rows: 表名 → 该表 `count(*)` 之后"并发写入"的行（模拟边导边写）。
        counts: 表名 → 最近一次 `count(*)` 的返回值（观察点）。
        batches: 按执行顺序记录的 `(表名, 该批受影响行数)`。
    """

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    statements: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    scopes: list[tuple[str, bool]] = field(default_factory=list)
    failures: dict[str, BaseException] = field(default_factory=dict)
    racing_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    batches: list[tuple[str, int]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 造数据与断言用的小工具
    # ------------------------------------------------------------------
    def seed(self, table: str, rows: list[dict[str, Any]]) -> None:
        """向某表追加行（造数据）。

        Args:
            table: 表名。
            rows: 行列表。
        """
        self.tables.setdefault(table, []).extend(rows)

    def rows(self, table: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """读某表的行（可按租户过滤）。

        Args:
            table: 表名。
            tenant_id: 租户 ID；None 表示不过滤。

        Returns:
            list[dict[str, Any]]: 行列表。
        """
        items = self.tables.get(table, [])
        if tenant_id is None:
            return list(items)
        return [row for row in items if row.get("tenant_id") == tenant_id]

    def row_count(self, table: str, tenant_id: str | None = None) -> int:
        """数某表的行数。

        Args:
            table: 表名。
            tenant_id: 租户 ID；None 表示不过滤。

        Returns:
            int: 行数。
        """
        return len(self.rows(table, tenant_id))

    def deletes(self, table: str) -> list[dict[str, Any]]:
        """取该表上执行过的全部 `DELETE` 参数（按执行顺序）。

        Args:
            table: 表名。

        Returns:
            list[dict[str, Any]]: 参数列表。
        """
        return [
            params
            for statement, params in self.statements
            if (matched := _DELETE_RE.match(statement)) is not None
            and matched.group("table") == table
        ]

    def delete_batches(self, table: str) -> list[int]:
        """该表每批 `DELETE` 的受影响行数（按执行顺序）。

        **这是本替身最有价值的观察点**：分批语义（每批 ≤ batch_size、
        最后一批不足即停在原地）只能从这里看出来。

        Args:
            table: 表名。

        Returns:
            list[int]: 每批行数。
        """
        return [count for name, count in self.batches if name == table]

    def deleted_total(self, table: str) -> int:
        """该表累计被删除的行数（分批之和）。

        Args:
            table: 表名。

        Returns:
            int: 累计行数。
        """
        return sum(self.delete_batches(table))

    def touched_tables(self) -> list[str]:
        """按首次出现顺序返回被 `DELETE` 触及的表名。

        Returns:
            list[str]: 表名列表。
        """
        seen: list[str] = []
        for statement, _params in self.statements:
            matched = _DELETE_RE.match(statement)
            if matched is not None and matched.group("table") not in seen:
                seen.append(matched.group("table"))
        return seen


class FakeUnitOfWork:
    """`tenant_scope()` 产出的工作单元替身（事务边界只做记录）。"""

    def __init__(self, database: FakeDatabase, tenant_id: str, *, read_only: bool = False) -> None:
        """初始化。

        Args:
            database: 内存库。
            tenant_id: 生效租户。
            read_only: 是否只读事务。
        """
        self._db = database
        self.tenant_id = tenant_id
        self.read_only = read_only

    async def __aenter__(self) -> FakeUnitOfWork:
        """进入事务（记录边界）。

        Returns:
            FakeUnitOfWork: 自身。
        """
        self._db.scopes.append((self.tenant_id, self.read_only))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """退出事务（不吞异常）。

        Args:
            exc_type: 异常类型。
            exc: 异常实例。
            tb: 回溯。

        Returns:
            bool: 恒为 False。
        """
        return False

    # ------------------------------------------------------------------
    # 语句
    # ------------------------------------------------------------------
    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> FakeResult:
        """执行一条写语句（DELETE / INSERT / UPDATE）。

        Args:
            statement: SQL 语句（业务层传字符串）。
            parameters: 命名参数。

        Returns:
            FakeResult: 只带 `rowcount`。

        Raises:
            NotImplementedError: 出现替身未建模的语句（宁可红也不静默通过）。
            BaseException: `failures` 里登记的表（模拟权限不足）。
        """
        sql = normalize(str(statement))
        params = dict(parameters or {})
        self._db.statements.append((sql, params))

        delete = _DELETE_RE.match(sql)
        if delete is not None:
            return FakeResult(rowcount=self._delete(delete.group("table"), params))

        insert = _INSERT_RE.match(sql)
        if insert is not None:
            self._db.seed(insert.group("table"), [dict(params)])
            return FakeResult(rowcount=1)

        update = _UPDATE_RE.match(sql)
        if update is not None:
            return FakeResult(rowcount=self._update(update.group("table"), params))

        raise NotImplementedError(f"替身未建模的写语句: {sql}")

    async def fetch_all(
        self, statement: Any, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """查询多行（支持 keyset 分页与全表选列）。

        Args:
            statement: SQL 语句。
            parameters: 命名参数。

        Returns:
            list[dict[str, Any]]: 行列表。

        Raises:
            NotImplementedError: 出现替身未建模的查询。
        """
        sql = normalize(str(statement))
        params = dict(parameters or {})
        self._db.statements.append((sql, params))

        keyset = _KEYSET_RE.search(sql)
        if keyset is not None:
            return self._keyset_page(keyset, params)

        simple = _SELECT_RE.search(sql)
        if simple is not None:
            table = simple.group("table")
            columns = _split_columns(simple.group("columns"))
            rows = self._db.rows(table, params.get("tenant_id"))
            rows.sort(key=lambda row: str(row.get("tenant_id", "")))
            return [_project(row, columns) for row in rows]

        raise NotImplementedError(f"替身未建模的查询: {sql}")

    async def fetch_one(
        self, statement: Any, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """查询单行（只有"按租户取某一列"这一种形态被建模）。

        Args:
            statement: SQL 语句。
            parameters: 命名参数。

        Returns:
            dict[str, Any] | None: 行；无结果返回 None。

        Raises:
            NotImplementedError: 出现替身未建模的查询。
        """
        sql = normalize(str(statement))
        params = dict(parameters or {})
        self._db.statements.append((sql, params))

        simple = _SELECT_RE.search(sql)
        if simple is None:
            raise NotImplementedError(f"替身未建模的查询: {sql}")
        table = simple.group("table")
        columns = _split_columns(simple.group("columns"))
        rows = self._db.rows(table, params.get("tenant_id"))
        return _project(rows[0], columns) if rows else None

    async def fetch_scalar(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """查询单值（只有 `count(*)` 这一种形态被建模）。

        Args:
            statement: SQL 语句。
            parameters: 命名参数。

        Returns:
            Any: 计数结果。

        Raises:
            NotImplementedError: 出现替身未建模的查询。
        """
        sql = normalize(str(statement))
        params = dict(parameters or {})
        self._db.statements.append((sql, params))

        counted = _COUNT_RE.search(sql)
        if counted is None:
            raise NotImplementedError(f"替身未建模的标量查询: {sql}")
        table = counted.group("table")
        tenant_id = params.get("tenant_id")
        value = self._db.row_count(table, tenant_id)
        self._db.counts[table] = value
        racing = self._db.racing_rows.pop(table, None)
        if racing:
            # 模拟"统计之后、取数之前又写入了新行"（并发写入导致边导边写）
            self._db.seed(table, racing)
        return value

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _delete(self, table: str, params: dict[str, Any]) -> int:
        """删掉该租户最多 `limit` 行，返回受影响行数。

        Args:
            table: 表名。
            params: 命名参数（含 `tenant_id` 与单批上限）。

        Returns:
            int: 受影响行数。

        Raises:
            BaseException: `failures` 里登记的表。
        """
        failure = self._db.failures.get(table)
        if failure is not None:
            raise failure
        limit = int(params.get("page_size") or params.get("batch_size") or _DEFAULT_LIMIT)
        tenant_id = params.get("tenant_id")
        rows = self._db.rows(table, tenant_id)
        victims = rows[:limit]
        if victims:
            victim_ids = {id(row) for row in victims}
            self._db.tables[table] = [
                row for row in self._db.tables[table] if id(row) not in victim_ids
            ]
        self._db.batches.append((table, len(victims)))
        return len(victims)

    def _update(self, table: str, params: dict[str, Any]) -> int:
        """更新一处（当前只建模 `tenant` 表的停用）。

        Args:
            table: 表名。
            params: 命名参数。

        Returns:
            int: 受影响行数。
        """
        rows = self._db.rows(table, params.get("tenant_id"))
        for row in rows:
            row["status"] = "suspended"
            row["suspended_reason"] = params.get("reason", "")
        return len(rows)

    def _keyset_page(self, matched: re.Match[str], params: dict[str, Any]) -> list[dict[str, Any]]:
        """按 `(ts, id) > (cursor_ts, cursor_id)` 取一页（真实执行键集分页）。

        Args:
            matched: 匹配到的 keyset 语句分组。
            params: 命名参数。

        Returns:
            list[dict[str, Any]]: 本页行（按 `(ts, id)` 升序，已投影）。
        """
        table = matched.group("table")
        ts_column = matched.group("ts")
        id_column = matched.group("id")
        columns = _split_columns(matched.group("columns"))
        cursor = (params["cursor_ts"], params["cursor_id"])
        rows = [
            row
            for row in self._db.rows(table, params.get("tenant_id"))
            if (row[ts_column], row[id_column]) > cursor
        ]
        rows.sort(key=lambda row: (row[ts_column], row[id_column]))
        page = rows[: int(params["page_size"])]
        return [_project(row, columns) for row in page]


def _split_columns(raw: str) -> list[str]:
    """把 `SELECT` 的列清单拆成列名列表。

    Args:
        raw: 形如 `a, b, t0.c`。

    Returns:
        list[str]: 列名列表（去掉表别名前缀与空白）。
    """
    return [part.strip().split(".")[-1] for part in raw.split(",") if part.strip()]


def _project(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    """按列清单投影一行（缺列即 KeyError——这是有意的"金丝雀"）。

    Args:
        row: 完整行。
        columns: 列清单。

    Returns:
        dict[str, Any]: 投影后的行。
    """
    return {column: row[column] for column in columns}


def install_tenant_scope(monkeypatch: Any, module: Any, database: FakeDatabase) -> FakeDatabase:
    """把模块里的 `tenant_scope` 换成内存替身。

    Args:
        monkeypatch: pytest 的 monkeypatch。
        module: 目标模块（`app.business.tenant_data_service` 等）。
        database: 内存库（用例自带，避免跨用例共享可变状态）。

    Returns:
        FakeDatabase: 传入的库（便于链式使用）。
    """

    def fake_tenant_scope(tenant_id: str, **kwargs: Any) -> FakeUnitOfWork:
        return FakeUnitOfWork(database, tenant_id, read_only=bool(kwargs.get("read_only")))

    monkeypatch.setattr(module, "tenant_scope", fake_tenant_scope)
    return database


def install_platform_scope(monkeypatch: Any, module: Any, database: FakeDatabase) -> FakeDatabase:
    """把模块里的 `platform_scope` 换成内存替身（只读跨租户查询）。

    Args:
        monkeypatch: pytest 的 monkeypatch。
        module: 目标模块。
        database: 内存库。

    Returns:
        FakeDatabase: 传入的库。
    """

    def fake_platform_scope(*_args: Any, **_kwargs: Any) -> FakeUnitOfWork:
        return FakeUnitOfWork(database, "__platform__", read_only=True)

    monkeypatch.setattr(module, "platform_scope", fake_platform_scope)
    return database


__all__: list[str] = [
    "FakeDatabase",
    "FakeResult",
    "FakeUnitOfWork",
    "install_platform_scope",
    "install_tenant_scope",
    "normalize",
]
