# 本脚本只在 CI/本地被 `python` 直接执行、不接收用户输入拼命令行，故无需 S603 豁免
# （文件首行原有一条 S603 文件级豁免，已删除：它从未命中，属无效指令，会被 RUF100 判红）。
"""仓储双实现一致性门禁（G-38，架构文档 §9.6）。

## 为什么需要它

`app/db/repositories/` 下每个存储单元都是**双实现**：`InMemoryXxxStore`（development /
test）与 `PostgresXxxStore`（生产）背后是同一份 `XxxStore` Protocol。测试跑的是内存实现，
生产跑的是 PG 实现——**两条路径只在生产才分叉，本地永远只验一条**。于是出现了一类
"测试全绿、上线就坏"的缺陷：

- 协议新增一个方法，只补了内存侧，PG 侧忘了 → `AttributeError` 只在上线后出现；
- 两侧同名方法参数名/默认值不一致 → 关键字调用在一侧静默走默认值；
- 同一 record 在内存侧构造时少写一个字段 → 该字段在内存后端恒为默认值（本项目实测
  就是 `RoleRecord.member_count`：内存 `get_role` 恒 0、PG 返回真实计数，见 P1-8b）。

这类缺陷的共同点是**没有任何用例会红**：单测只跑内存实现，集成用例跑 PG 但不覆盖
"两侧形状是否一致"。本脚本把"一致性"变成**可执行**的：形状不一致即红，除非在基线里
登记了已评审的消除计划。

## 与 G-37（`validate_import_directions.py`）的关系

两者都由 `deploy/` 下的独立脚本承载，但**判定对象不同**：G-37 管"层与层之间的方向"，
G-38 管"同一层内两个实现的形状"。本脚本**不 import** G-37 的任何内部符号——只沿用它
已被评审过的外部契约（`--baseline` / `--self-test` / 退出码语义），避免两个门禁在代码层
互相耦合（改一个的解析器不该能弄红另一个）。

## 单元（unit）的两种形态

1. **文件形态**：`app/db/repositories/<name>_repo.py`（本轮 19 个）；
2. **包形态**：`app/db/repositories/<name>/`，必须同时含 `memory.py` 与 `postgres.py`
   （可选 `records.py` / `protocol.py` / 其他）。

同名并存时（拆分过渡期）取**包形态**，此时 `<name>_repo.py` 必须是**纯 re-export shim**：
只允许 docstring / `import` / `__all__` 与常量赋值；出现任何 `ClassDef` / `FunctionDef`
或其它顶层语句即 exit 2（否则"薄壳"会重新长成第二份实现，拆分等于没做）。

单元名 = 文件名去掉 `_repo` 后缀，或包目录名。

## 五条规则（unit 粒度；`[FAIL]` / `[WARN]` / `[INFO]` 见"退出码"一节）

实现三元组按**协议名**配对：`XxxStore` → `InMemoryXxxStore` / `PostgresXxxStore`；
协议名带 `Protocol` 后缀时先去掉（`DocumentStoreProtocol` → `InMemoryDocumentStore`）。
配不上三元组即 exit 2，例外只有 `NO_PROTOCOL_UNITS`（`audit`：该单元只有查询函数，
PG 写入实现故意留在 `business/audit_service.py`，见其模块注释）。

- **R1 `proto_missing`**（FAIL）：协议的公开方法必须 ⊆ 内存实现且 ⊆ PG 实现，缺一个即违规。
- **R2 `impl_asym`**（FAIL）：`(内存公开 − TEST_HOOKS) == (PG 公开 − TEST_HOOKS)`，
  不等即违规；`TEST_HOOKS` 出现在 PG 侧单独违规。
- **R3 `sig_mismatch`**（FAIL）：同名方法：位置参数名序列、keyword-only 名序列、
  `*args`/`**kwargs` 名、共同参数默认值（`ast.unparse` 文本）、同步/异步、
  `@property` 属性性全部相同。
- **R4 `record_ctor_asym`**（FAIL）：每侧把 record 的**直接构造点**关键字集合收成
  `set[frozenset[str]]`（同形构造自动去重），两侧**逐集合相等**才通过；
  任一侧没有任何直接构造点 → 只 `[INFO]` 跳过（该侧经映射器/服务层构造，无可比对象）。
- **R5 `proto_undeclared`**（只 `[INFO]`，不判红、不入基线）：双实现都有、协议未声明的方法。

**公开方法口径**：非 `_` 开头、非 dunder；`def` / `async def` / `@property` 都计入
（property 也是公开面，只是调用形态不同，所以 R3 里单独比"属性性"）。

### R4 为什么按「形状集合」逐点对齐、且**不给映射器豁免**（v2 收紧）

v1 有两条洗白通道，QA 用副本各实证了一次，两条都能让真实漂移变绿：

1. **按名字豁免**：v1 对「存在模块级 `_to_<stem>` / `_row_to_<stem>` 映射器」的 record 整体
   跳过。实测只要在实现里加一个与实现毫无关系的空 `_to_role`，同一条真实漂移
   （内存侧少 `member_count`）就从 `[FAIL]` 变成 `[INFO] 跳过`、exit 0。
   **豁免由一个自由命名的函数决定 = 给门禁开后门**，故整段映射器识别逻辑删除。
2. **并集比对**：v1 把同侧多个构造点的关键字取并集再和对侧比。实测「PG 有两个构造点、
   删掉其中一个的 `member_count`」并集仍含该字段 → 假绿。v2 改为**形状集合逐一对齐**：
   一侧多出/缺少任何一种形状都判红，因为"某个代码路径少写字段"正是在**单个构造点**上发生的。

代价与边界：只在一侧直接构造、另一侧走映射器/服务层的 record 无法比对 → `[INFO]` 跳过
（这是"看不到"，不是"没问题"，所以 INFO 会逐条打印出来，方便评审时人工确认）。

**`TEST_HOOKS = {"clear", "counts", "set_user_departments", "set_user_roles",
"set_subject_name"}`**：测试隔离钩子只允许存在于内存侧——PG 侧出现
同名方法说明有人把测试钩子带进了生产实现（grant 三写口的归类依据见
`TEST_HOOKS` 定义处注释，审计 2026-10-30 P1-8a）。

**R3 不判返回注解**：`-> list[X]` 与 `-> Sequence[X]` 都是合法收敛，判红只会制造噪声；
真正要防的是"参数形状不一致导致调用在一侧静默走默认值"。

## 基线（`deploy/repository_parity_baseline.txt`）

格式：`<rule> <unit> <subject> -- <审计编号> <理由>`

- `subject` 是**方法名或 record 类名**（R1/R2/R3 用方法名，R4 用 record 类名）。
  R2 即使一次涉及多个方法也**逐方法各报一条**，不把一组方法压成一行——基线的粒度
  就是 stale 检测的粒度，"修好两个、还剩一个"必须能被表达出来。
- 基线内的违规 → `[WARN]`，不阻断（"已评审、有消除计划"）；
- 基线外的违规 → `[FAIL]`，exit 1；
- 基线条目在本轮扫描里已消失 → `[FAIL] stale`，exit 1（**防腐烂**：修完必须删行）；
- 基线格式错误（列数不是 3 / rule 不在 `{R1,R2,R3,R4}` / unit 不在实测集 / 审计编号不以
  `P0-`/`P1-`/`P2-` 开头 / 缺理由 / 重复行）→ exit 2；
- 基线文件**不存在** → 按空基线处理并打 `[INFO]`（**fail-closed**：不豁免任何违规，
  所以 `--baseline /dev/null` 必然在存在存量违规时 exit 1，这是"门禁不是空转"的机械证明）；
- **不提供 `--update-baseline`**：只许减不许增，新增豁免必须走评审。

## fail-closed 护栏

扫描前校验 `--repositories-root` 存在且是目录；可判定单元数 < `MIN_UNITS`（默认 15）即
exit 2。没有这条护栏时，目录被移动/改名会打印「扫描 0 个单元 / 违规 0 条 / [OK] exit 0」——
门禁在最该红的场景变绿。`--min-units` **仅供 `--self-test`** 下调，CI 不得传。

## 已知盲区

AST 只看**语法**：`setattr(self, name, ...)`、`__getattr__` 动态代理、`typing.cast`
绕过签名检查的实现都判不出来；跨文件的 record 构造（比如业务层直接构造仓储 record）
也不在 R4 范围内——R4 只对比"本单元内存区 vs PG 区"。这些需要 code review 收口，
本门禁的定位是"把机械可判的那 90% 钉死"。

## 退出码

- 0：无违规，或全部违规都已在基线登记；
- 1：存在基线外违规，或基线条目已 stale；
- 2：结构性问题（目录不存在/不是目录/单元数不足/三元组配不上/shim 不纯/基线格式错误/自检失败）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tempfile
import textwrap
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final

# ===========================================================================
# 常量
# ===========================================================================

#: 默认被扫描的仓储目录（相对本脚本定位，避免"从哪个 cwd 跑"影响结果）。
DEFAULT_REPOSITORIES_ROOT: Final[Path] = (
    Path(__file__).resolve().parent.parent / "apps" / "adapter" / "app" / "db" / "repositories"
)

#: DDL 根目录（`deploy/`，R7 从这里取表结构与默认值）。
DEFAULT_DEPLOY_ROOT: Final[Path] = Path(__file__).resolve().parent

#: 默认基线文件。
DEFAULT_BASELINE: Final[Path] = Path(__file__).resolve().parent / "repository_parity_baseline.txt"

#: 可判定单元数下限（**fail-closed**：低于它就是门禁失效，不是"没违规"）。
#: 实测 20 个：19 个 `*_repo.py` 文件单元 + 1 个 `role/` 包单元。
MIN_UNITS: Final[int] = 15

#: 测试隔离钩子：只允许出现在内存侧。
#:
#: 审计 2026-10-30 P1-8a 收口：`set_user_departments` / `set_user_roles` /
#: `set_subject_name` 三个便捷写口按本机制显式归类为**测试钩子**（docstring 原文
#: 即"设置测试/内存模式下…"，app/ 内零调用点，仅 tests/ 使用）。PG 侧的部门/角色
#: 成员关系归 org / role 存储持有，grant 存储**不复制**这条写路径——因此它们
#: 与 `clear` / `counts` 同类：内存侧专属，PG 侧出现同名方法反而要判红。
TEST_HOOKS: Final[frozenset[str]] = frozenset(
    {"clear", "counts", "set_user_departments", "set_user_roles", "set_subject_name"}
)

#: 允许"没有协议"的单元（有实现必有协议之外的唯一例外）。
NO_PROTOCOL_UNITS: Final[frozenset[str]] = frozenset({"audit"})

#: 可登记进基线的规则（R5 只报 INFO，永不判红，因此不可登记）。
BASELINE_RULES: Final[frozenset[str]] = frozenset({"R1", "R2", "R3", "R4"})

#: 审计编号前缀白名单。
AUDIT_ID_PREFIXES: Final[tuple[str, ...]] = ("P0-", "P1-", "P2-")

#: 内存实现类名前缀。
MEMORY_PREFIX: Final[str] = "InMemory"

#: PG 实现类名前缀。
POSTGRES_PREFIX: Final[str] = "Postgres"

#: 协议类名可选后缀（`DocumentStoreProtocol` 与 `DocumentStore` 两种命名都要认）。
PROTOCOL_SUFFIX: Final[str] = "Protocol"

#: 包目录里不参与扫描的名字。
SKIP_PACKAGE_DIRS: Final[frozenset[str]] = frozenset({"__pycache__"})

#: 包形态必须具备的两个文件（缺一即"配不上三元组"）。
MEMORY_MODULE: Final[str] = "memory.py"

#: PG 形态必须具备的文件。
POSTGRES_MODULE: Final[str] = "postgres.py"

#: R6 豁免表：`(unit, record, field) → (原因, 失效条件)`。
#:
#: 只登记**已核实无读写点**的字段。每条都必须写清"什么情况下必须删掉本豁免"，
#: 否则豁免会变成永久后门。新增条目必须同时给出"为什么不能补映射"的证据。
R6_EXEMPTIONS: Final[dict[tuple[str, str, str], tuple[str, str]]] = {
    ("conversation", "MessageRecord", "history"): (
        "预留字段（消息编辑/多轮回放待落地）：app/ 与 tests/ 里对 `.history` 的读写为 0 处，"
        "`conversation_service` 两处构造均未传该参数，`app/schemas/` 也无对应字段",
        "消息编辑或多轮回放功能合入时必须补 `_to_message` 映射并删掉本豁免",
    ),
}

#: R6 配对按**返回注解**而不是函数名前缀：`memory._to_setting → MemorySettingRecord`
#: 就是反例（名字带 `_to_` 也可能返回别的 record，名字不带也可能返回 record）。

#: R7：`record → 表` 的配对容差。一个 record 允许有少量字段**不是表列**
#: （派生值 / 预留字段 / 展示用冗余），但超过这个比例就说明配对错了
#: （例如 `PlanRecord` 与 `billing_order` 只重叠 4 列，属误配）。
R7_TABLE_TOLERANCE: Final[float] = 0.3

#: R7：`record` 与表的列名重叠下限（低于它不认为配上了）。
R7_MIN_OVERLAP: Final[int] = 3

#: R7 覆盖率下限（**fail-closed**）：低于它就是"门禁没在看"，不是"没有缺口"。
#:
#: 为什么必须有：R7 的两处 `continue`（表不在 DDL / 配不上表）会让覆盖塌陷与
#: "全部覆盖"在输出上完全一致——实测把 `usage_event` 在 DDL 里改个名，再删掉
#: `units`，R7 会报 `WARN 0` 且 `--strict-r7` 也 exit 0。下限是唯一能红的信号。
#: 实测基线：INSERT 37 条、配上 31 个 dataclass（2026-10-26）。
R7_MIN_INSERTS: Final[int] = 30
R7_MIN_PAIRED: Final[int] = 28

#: R7 允许"配不上表"的 dataclass 白名单（`(unit, dataclass)`）。
#:
#: 只是"计数"不够：**新冒出来一个 skipped 就是覆盖塌陷的信号**（某张表漂移 →
#: 它的 record 配不上 → 静默进 skipped，而 paired 只掉 1、仍在余量内）。
#: 所以 skipped 必须与这张白名单**逐项相等**，多一个即 exit 2。
#: 新增非行式 dataclass（store / stats / patch / 纯读）时必须显式登记并说明理由。
#: R7 允许"整单元没有 INSERT、dataclass 未评估"的白名单（`(unit, dataclass)`）。
#:
#: 与 skipped 白名单同理：**未评估必须逐项相等**。否则"某单元的 INSERT 挪去业务层 /
#: 拆包后新单元还没写 SQL"会让 record 从 paired 掉进 unevaluated，而输出只多一行 INFO、
#: exit 仍 0——与 N2 修掉的那个洞是同一个，只是桶不同。
R7_UNEVALUATED_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # audit 的 PG 写入故意留在 business/audit_service.py（R1/R4 对该单元的豁免所指）；
        # 那条写入的**列数/列名/表名**由
        # tests/integration/test_sql_insert_consistency.py（G-SQL）对账，
        # 字段级覆盖 R7 看不到它——这是本白名单成立的前提，不是省略。
        ("audit", "ActionGroupEntry"),
        ("audit", "AuditQuery"),
        ("audit", "AuditQueryResult"),
    }
)

R7_SKIPPED_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("billing", "PlanRecord"),  # 套餐目录由 SQL 种子写入，代码无 INSERT
        ("knowledge_base", "KnowledgeBaseViewer"),  # 视图 DTO，非表行
        ("ops", "OpsSettings"),  # 单行设置，由 save_settings 逐字段拼装
        ("plugin", "CatalogRecord"),  # 目录来自插件注册表，非表行
        ("plugin_review", "InMemoryReviewStore"),  # 内存存储类（@dataclass），非 record
        ("role", "PermissionCatalogEntry"),  # 权限目录为常量种子（PERMISSION_CATALOG_SEED）
        ("task", "TaskStats"),  # 聚合结果，非表行
        ("usage", "QuotaRecord"),  # 由订阅/聚合推导，非表行
        ("usage", "RangeEventStats"),  # 聚合结果，非表行
    }
)

#: R7：DDL 来源（缺任一来源都会把"后补列"误判成不存在）。
R7_DDL_GLOBS: Final[tuple[str, ...]] = ("*.sql", "migrations/*.sql")

#: R7：**语义**注记（不是缺失豁免，因为该列已被 DDL 默认值覆盖）。
#: 打印成 INFO，让"两个后端取值来源不同"这类差异在门禁输出里可见、可追溯。
R7_NOTES: Final[dict[tuple[str, str, str], str]] = {
    ("usage", "UsageEventRecord", "occurred_at"): (
        "PG 侧由 DDL `DEFAULT now()` 生成（库时钟），内存侧沿用调用方传入值——"
        "语义差异而非丢字段，故不补列；出现读回路径时须复核（届时两侧取值会直接可比）"
    ),
}

#: DDL 里不算列定义的行首关键字。
DDL_NON_COLUMN_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "PRIMARY",
        "UNIQUE",
        "CONSTRAINT",
        "CHECK",
        "FOREIGN",
        "REFERENCES",
        # 注意：**不能把 `KEY` 放进来**——`PRIMARY KEY` / `FOREIGN KEY` 都以
        # PRIMARY / FOREIGN 开头，而列名本身可能就叫 `key`（自检用例 23 抓到的真缺陷：
        # 加上 `KEY` 会让 `key TEXT NOT NULL` 被当成约束行丢掉）。
        "LIKE",
        "EXCLUDE",
    }
)


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass(frozen=True, slots=True)
class MethodInfo:
    """一个方法的签名快照（只留 R3 需要的形状信息）。

    Attributes:
        name: 方法名。
        is_async: 是否 `async def`。
        is_property: 是否被 `@property` 装饰。
        positional: 位置参数名（posonly + normal，已剔除 `self`/`cls`）。
        keyword_only: keyword-only 参数名。
        vararg: `*args` 的名字，无则 None。
        kwarg: `**kwargs` 的名字，无则 None。
        defaults: `(参数名, 默认值源码文本)` 序列（无默认值的参数不出现）。
        lineno: 定义行号（报错定位用）。
    """

    name: str
    is_async: bool
    is_property: bool
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwarg: str | None
    defaults: tuple[tuple[str, str], ...]
    lineno: int


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """一个类的形状快照。

    Attributes:
        name: 类名。
        path: 定义所在文件。
        lineno: 定义行号。
        is_protocol: 是否继承 `typing.Protocol`。
        is_dataclass: 是否被 `@dataclass(...)` 装饰。
        bases: 基类源码文本序列（`ast.unparse`）。
        methods: 类体内的方法（不含嵌套类的方法）。
    """

    name: str
    path: Path
    lineno: int
    is_protocol: bool
    is_dataclass: bool
    bases: tuple[str, ...]
    methods: tuple[MethodInfo, ...]


@dataclass(frozen=True, slots=True)
class Unit:
    """一个仓储单元（文件形态或包形态）。

    Attributes:
        name: 单元名（`artifact` / `role` / ...）。
        kind: `"file"` 或 `"package"`。
        files: 参与判定的全部 `.py` 文件。
        memory_files: 内存实现所在文件。
        postgres_files: PG 实现所在文件。
        shim: 包形态并存时被保留的 `<name>_repo.py` 兼容层（文件形态为 None）。
    """

    name: str
    kind: str
    files: tuple[Path, ...]
    memory_files: tuple[Path, ...]
    postgres_files: tuple[Path, ...]
    shim: Path | None


@dataclass(frozen=True, slots=True)
class Violation:
    """一条判红（或基线内 WARN）的违规。

    Attributes:
        rule: `R1` ~ `R4`。
        unit: 单元名。
        subject: 方法名（R1/R2/R3）或 record 类名（R4）。
        detail: 给人看的具体差异。
    """

    rule: str
    unit: str
    subject: str
    detail: str

    def key(self) -> tuple[str, str, str]:
        """返回基线匹配键。

        Returns:
            tuple[str, str, str]: `(rule, unit, subject)`。
        """
        return (self.rule, self.unit, self.subject)


@dataclass(frozen=True, slots=True)
class MapperFinding:
    """R6 的一条发现：`_to_*` 映射器没有覆盖 record 的某个字段。

    Attributes:
        unit: 单元名。
        record: record 类名。
        field: 未被任何构造点写入的字段名。
        mapper: 映射器函数名。
        path: 映射器所在文件。
        lineno: 映射器定义行号。
        exempt: 豁免原因（None 表示未豁免，属真 WARN）。
        expire_when: 豁免失效条件（未豁免时为 None）。
    """

    unit: str
    record: str
    field: str
    mapper: str
    path: Path
    lineno: int
    exempt: str | None = None
    expire_when: str | None = None

    @property
    def is_exempt(self) -> bool:
        """是否命中豁免表。

        Returns:
            bool: 命中豁免返回 True。
        """
        return self.exempt is not None

    def describe(self) -> str:
        """渲染成一行人类可读说明。

        Returns:
            str: `R6 <unit> <record>.<field>（file:line，mapper）`。
        """
        where = f"{self.path.name}:{self.lineno}"
        return f"R6 {self.unit} `{self.record}.{self.field}`（{where}，mapper `{self.mapper}`）"


@dataclass(frozen=True, slots=True)
class DdlTable:
    """一张表的 DDL 形状。

    Attributes:
        columns: 表里全部列名。
        omittable: INSERT 可以不写的列——**有 `DEFAULT` 或允许 NULL** 的列。
    """

    columns: frozenset[str]
    omittable: frozenset[str]


@dataclass(frozen=True, slots=True)
class WriteFinding:
    """R7 的一条发现：某 record 的字段在写侧完全没有落点。

    Attributes:
        unit: 单元名。
        record: record 类名。
        table: 该 record 对应的表名。
        field: 既没被任何 INSERT 写入、也不在 DDL 可省略列里的字段。
        detail: 人类可读说明。
    """

    unit: str
    record: str
    table: str
    field: str
    detail: str

    @property
    def key(self) -> tuple[str, str, str]:
        """返回唯一键。

        Returns:
            tuple[str, str, str]: `(unit, record, field)`。
        """
        return (self.unit, self.record, self.field)


@dataclass(slots=True)
class WriteStats:
    """R7 的覆盖率计数（**fail-closed 的依据**：覆盖塌陷必须在数字上可见）。

    Attributes:
        dataclasses: 参与判定的 dataclass 总数（全部单元）。
        paired: 配上表的 dataclass 数。
        skipped: 配不上表而跳过的 dataclass 数（非行式：store/stats/patch/纯读）。
        unevaluated: 所在单元没有任何 INSERT、整单元未评估的 dataclass 数。
        statements: 扫描到的 `INSERT INTO <表> (...) VALUES` 语句数。
        unresolved: 表名解析不出来而被丢弃的 SQL 语句数。
        orphan_tables: 被 INSERT 写入、但 DDL 里找不到的表。
    """

    dataclasses: int = 0
    paired: int = 0
    skipped: int = 0
    unevaluated: int = 0
    statements: int = 0
    unresolved: int = 0
    orphan_tables: set[str] = field(default_factory=set)
    unexpected_skips: set[tuple[str, str]] = field(default_factory=set)
    unevaluated_pairs: set[tuple[str, str]] = field(default_factory=set)

    def summary(self) -> str:
        """渲染成一行覆盖率摘要。

        Returns:
            str: 覆盖率摘要（配上/未配上/未评估 + INSERT 条数与不可解析数 + DDL 缺表）。
        """
        orphan = "无" if not self.orphan_tables else "、".join(sorted(self.orphan_tables))
        return (
            f"dataclass {self.dataclasses} → 配上 {self.paired} / 未配上 {self.skipped} / "
            f"未评估 {self.unevaluated}；INSERT {self.statements} 条"
            f"（表名不可解析 {self.unresolved}）；DDL 缺表 {orphan}"
        )


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """基线条目。

    Attributes:
        rule: `R1` ~ `R4`。
        unit: 单元名。
        subject: 方法名（R1/R2/R3）或 record 类名（R4）。
        audit_id: 审计编号（`P0-`/`P1-`/`P2-` 开头）。
        reason: 消除计划。
        lineno: 基线文件行号（报错定位用）。
    """

    rule: str
    unit: str
    subject: str
    audit_id: str
    reason: str
    lineno: int

    def key(self) -> tuple[str, str, str]:
        """返回基线匹配键。

        Returns:
            tuple[str, str, str]: `(rule, unit, subject)`。
        """
        return (self.rule, self.unit, self.subject)


@dataclass(slots=True)
class ScanResult:
    """一次扫描的全部产出。

    Attributes:
        units: 发现的单元（已排序）。
        violations: 判红级违规（R1 ~ R4）。
        infos: 只提示不判红的说明（R5 / 映射器跳过 / 单侧无构造点）。
        problems: 结构性问题（出现任一即 exit 2）。
        r6: R6 的映射器字段覆盖发现（默认 WARN-only，`--strict-r6` 才计入退出码）。
        r7: R7 的写侧字段覆盖发现（默认 WARN-only，`--strict-r7` 才计入退出码）。
        r7_info: R7 的说明行（非表列字段、找不到对应表的 record、语义注记）。
    """

    units: list[Unit]
    violations: list[Violation]
    infos: list[str]
    problems: list[str]
    r6: list[MapperFinding] = field(default_factory=list)
    r7: list[WriteFinding] = field(default_factory=list)
    r7_info: list[str] = field(default_factory=list)
    r7_stats: WriteStats = field(default_factory=WriteStats)


# ===========================================================================
# AST 解析与形状提取
# ===========================================================================


def _parse(path: Path) -> ast.Module:
    """解析一个 Python 文件。

    Args:
        path: 文件路径。

    Returns:
        ast.Module: 语法树。
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _decorator_names(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    """提取装饰器的源码文本（`@dataclass(frozen=True)` 取 `dataclass(...)`）。

    Args:
        node: 类或函数节点。

    Returns:
        tuple[str, ...]: 装饰器文本序列。
    """
    return tuple(ast.unparse(decorator) for decorator in node.decorator_list)


def _has_decorator(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """判断是否带某个装饰器（`@name` 或 `@name(...)` 都算）。

    Args:
        node: 类或函数节点。
        name: 装饰器名字（不含 `@`）。

    Returns:
        bool: 带该装饰器返回 True。
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == name:
            return True
        if isinstance(target, ast.Attribute) and target.attr == name:
            return True
    return False


def _method_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
    """把一个方法节点压成 `MethodInfo`。

    Args:
        node: 方法节点。

    Returns:
        MethodInfo: 签名快照。
    """
    args = node.args
    all_positional = [*args.posonlyargs, *args.args]
    names = [item.arg for item in all_positional]
    # `self` / `cls` 是绑定语义的一部分，不参与"跨实现比对"（`@staticmethod` 另说，但
    # 本项目的仓储实现没有 staticmethod；即使有，`self` 也不在参数表里）。
    if names and names[0] in {"self", "cls"}:
        names = names[1:]
        all_positional = all_positional[1:]

    raw_defaults: dict[str, str] = {}
    # `zip` 两侧长度由 AST 语义保证相等（默认值个数 ≤ 位置参数个数；kwonlyargs 与
    # kw_defaults 恒同长），故显式 `strict=True`：若哪天不相等，应当立刻抛错而不是静默截断。
    paired = zip(
        all_positional[len(all_positional) - len(args.defaults) :], args.defaults, strict=True
    )
    for arg_node, default_node in paired:
        raw_defaults[arg_node.arg] = ast.unparse(default_node)
    # 变量名刻意不与上一轮复用：`kw_defaults` 的元素类型是 `expr | None`
    # （`None` 表示 keyword-only 必填），与上一轮 `args.defaults` 的 `expr` 不同；
    # 复用同名会同时误导人与类型检查器。
    for kw_arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        # keyword-only 的 `None` 默认表示"必填"（`**` 之后不能有位置默认值），
        # 用哨兵文本与真实 `None` 默认值区分开。
        raw_defaults[kw_arg.arg] = "<required>" if kw_default is None else ast.unparse(kw_default)

    ordered_defaults = tuple(
        (item.arg, raw_defaults[item.arg])
        for item in [*all_positional, *args.kwonlyargs]
        if item.arg in raw_defaults
    )
    return MethodInfo(
        name=node.name,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_property=_has_decorator(node, "property"),
        positional=tuple(names),
        keyword_only=tuple(item.arg for item in args.kwonlyargs),
        vararg=args.vararg.arg if args.vararg is not None else None,
        kwarg=args.kwarg.arg if args.kwarg is not None else None,
        defaults=ordered_defaults,
        lineno=node.lineno,
    )


def _class_info(node: ast.ClassDef, path: Path) -> ClassInfo:
    """把一个类节点压成 `ClassInfo`。

    Args:
        node: 类节点。
        path: 定义所在文件。

    Returns:
        ClassInfo: 类形状快照。
    """
    bases = tuple(ast.unparse(base) for base in node.bases)
    is_protocol = any(base == "Protocol" or base.endswith(".Protocol") for base in bases)
    methods = tuple(
        _method_info(item)
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return ClassInfo(
        name=node.name,
        path=path,
        lineno=node.lineno,
        is_protocol=is_protocol,
        is_dataclass=_has_decorator(node, "dataclass"),
        bases=bases,
        methods=methods,
    )


def _public_methods(info: ClassInfo) -> tuple[MethodInfo, ...]:
    """提取公开方法（非 `_` 开头、非 dunder；property 与 async 都算）。

    Args:
        info: 类形状快照。

    Returns:
        tuple[MethodInfo, ...]: 按源码声明序排列的公开方法。
    """
    return tuple(item for item in info.methods if not item.name.startswith("_"))


def _method_map(info: ClassInfo) -> dict[str, MethodInfo]:
    """把公开方法按名字索引（保留声明序）。

    Args:
        info: 类形状快照。

    Returns:
        dict[str, MethodInfo]: 方法名 → 快照。
    """
    return {item.name: item for item in _public_methods(info)}


# ===========================================================================
# 单元发现
# ===========================================================================


def _check_shim(path: Path) -> list[str]:
    """校验"同名并存时的 `*_repo.py`"是否是纯 re-export 壳。

    只允许：文件 docstring / `import` / `from ... import ...` / `__all__` 赋值 /
    常量赋值（大写名）/ 其它字面量表达式语句。出现 `ClassDef` / `FunctionDef` / 循环 /
    调用等任何"会长出第二份实现"的语句即违规。

    Args:
        path: shim 文件路径。

    Returns:
        list[str]: 违规说明（空表示合格）。
    """
    problems: list[str] = []
    try:
        module = _parse(path)
    except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover - 语法错误极少见
        return [f"shim {path} 无法解析：{exc}"]

    imported: set[str] = set()
    exported: list[str] = []
    seen_all = False
    for node in module.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
            continue
        if isinstance(node, ast.ClassDef):
            problems.append(
                f"shim {path} 定义了类 `{node.name}`（第 {node.lineno} 行）：包形态并存时 "
                f"`*_repo.py` 必须是纯 re-export，不得出现类/函数定义"
            )
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            problems.append(
                f"shim {path} 定义了函数 `{node.name}`（第 {node.lineno} 行）：包形态并存时 "
                f"`*_repo.py` 必须是纯 re-export，不得出现类/函数定义"
            )
            continue
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    problems.append(
                        f"shim {path} 第 {node.lineno} 行出现非名字赋值的顶层语句："
                        f"re-export 壳不允许计算型赋值"
                    )
                    continue
                if target.id == "__all__":
                    seen_all = True
                    # `ast.Assign.value` 在 typeshed 里是 `expr | None`（那是给
                    # `AnnAssign` 共用的形状）；对 `ast.Assign` 而言 value 必然存在，
                    # 断言把这一语言事实写进代码，也顺带完成类型收窄。
                    assert node.value is not None
                    exported.extend(_literal_str_sequence(node.value))
                elif not target.id.isupper():
                    problems.append(
                        f"shim {path} 第 {node.lineno} 行赋值了非常量名 `{target.id}`："
                        f"re-export 壳只允许 `__all__` 与大写常量"
                    )
            continue
        if isinstance(node, ast.Expr):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                continue
            problems.append(
                f"shim {path} 第 {node.lineno} 行有非 docstring 的表达式语句：re-export 壳不允许"
            )
            continue
        problems.append(
            f"shim {path} 第 {node.lineno} 行出现不被允许的顶层语句 "
            f"`{type(node).__name__}`：re-export 壳只允许 import 与常量赋值"
        )

    if not seen_all:
        problems.append(f"shim {path} 没有 `__all__`：无法证明它是纯 re-export")
    missing = [name for name in exported if name not in imported]
    if missing:
        problems.append(f"shim {path} 的 `__all__` 含未导入的名字：{sorted(missing)}")
    return problems


def _literal_str_sequence(node: ast.expr) -> list[str]:
    """把 `["a", "b"]` / `("a",)` 这类字面量取成字符串列表。

    Args:
        node: 表达式节点（通常是 `__all__` 的右值）。

    Returns:
        list[str]: 字面量字符串序列；非字面量返回空列表。
    """
    if not isinstance(node, ast.List | ast.Tuple):
        return []
    names: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.append(element.value)
    return names


def discover_units(repositories_root: Path) -> tuple[list[Unit], list[str]]:
    """发现所有仓储单元（文件形态 + 包形态，同名并存取包形态）。

    Args:
        repositories_root: 仓储目录（`app/db/repositories`）。

    Returns:
        tuple[list[Unit], list[str]]: `(单元列表, 结构性问题列表)`。
    """
    problems: list[str] = []
    packages: dict[str, Unit] = {}

    for entry in sorted(repositories_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in SKIP_PACKAGE_DIRS or entry.name.startswith("."):
            continue
        memory = entry / MEMORY_MODULE
        postgres = entry / POSTGRES_MODULE
        if not memory.is_file() and not postgres.is_file():
            # 不是仓储包（如只有 __init__.py 的杂项目录），不参与判定。
            continue
        if not memory.is_file() or not postgres.is_file():
            missing = MEMORY_MODULE if not memory.is_file() else POSTGRES_MODULE
            problems.append(
                f"包形态单元 `{entry.name}` 缺 `{missing}`："
                f"无法配对双实现（包形态必须同时含 memory.py 与 postgres.py）"
            )
            continue
        files = tuple(sorted(item for item in entry.glob("*.py") if item.is_file()))
        packages[entry.name] = Unit(
            name=entry.name,
            kind="package",
            files=files,
            memory_files=(memory,),
            postgres_files=(postgres,),
            shim=None,
        )

    units: list[Unit] = []
    for entry in sorted(repositories_root.glob("*_repo.py")):
        name = entry.stem[: -len("_repo")]
        if name in packages:
            shim_problems = _check_shim(entry)
            problems.extend(shim_problems)
            base = packages.pop(name)
            units.append(
                Unit(
                    name=base.name,
                    kind=base.kind,
                    files=base.files,
                    memory_files=base.memory_files,
                    postgres_files=base.postgres_files,
                    shim=entry,
                )
            )
            continue
        units.append(
            Unit(
                name=name,
                kind="file",
                files=(entry,),
                memory_files=(entry,),
                postgres_files=(entry,),
                shim=None,
            )
        )

    # 只有包形态、没有 `*_repo.py` 的单元（拆分完成后的终态）也要收进来。
    units.extend(packages.values())
    units.sort(key=lambda unit: unit.name)
    return units, problems


# ===========================================================================
# 双实现配对与逐单元判定
# ===========================================================================


def _load_classes(unit: Unit) -> tuple[dict[str, ClassInfo], dict[Path, ast.Module], list[str]]:
    """加载单元内全部类形状与语法树。

    Args:
        unit: 仓储单元。

    Returns:
        tuple[dict[str, ClassInfo], dict[Path, ast.Module], list[str]]:
        `(类名→形状, 文件→语法树, 问题列表)`。
    """
    classes: dict[str, ClassInfo] = {}
    modules: dict[Path, ast.Module] = {}
    problems: list[str] = []
    for path in unit.files:
        try:
            module = _parse(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"单元 `{unit.name}` 的文件 {path} 无法解析：{exc}")
            continue
        modules[path] = module
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                if node.name in classes:
                    problems.append(
                        f"单元 `{unit.name}` 的类名 `{node.name}` 重复定义"
                        f"（{classes[node.name].path} 与 {path}）"
                    )
                classes[node.name] = _class_info(node, path)
    return classes, modules, problems


def _pair_triples(
    unit: Unit, classes: dict[str, ClassInfo]
) -> tuple[list[tuple[ClassInfo, ClassInfo, ClassInfo]], list[str]]:
    """把协议与双实现配对成三元组。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类。

    Returns:
        tuple[list[tuple[ClassInfo, ClassInfo, ClassInfo]], list[str]]:
        `(三元组列表, 问题列表)`。
    """
    problems: list[str] = []
    triples: list[tuple[ClassInfo, ClassInfo, ClassInfo]] = []
    consumed: set[str] = set()
    protocols = [info for info in classes.values() if info.is_protocol]
    for protocol in sorted(protocols, key=lambda item: item.name):
        stem = protocol.name
        if stem.endswith(PROTOCOL_SUFFIX) and len(stem) > len(PROTOCOL_SUFFIX):
            stem = stem[: -len(PROTOCOL_SUFFIX)]
        memory_name = f"{MEMORY_PREFIX}{stem}"
        postgres_name = f"{POSTGRES_PREFIX}{stem}"
        memory = classes.get(memory_name)
        postgres = classes.get(postgres_name)
        missing = [
            name
            for name, found in ((memory_name, memory), (postgres_name, postgres))
            if found is None
        ]
        if missing:
            problems.append(
                f"单元 `{unit.name}`：协议 `{protocol.name}` 无法配对三元组（缺 {missing}）"
                f"——双实现门禁的前提是 `(Protocol, InMemory*, Postgres*)` 三者齐备"
            )
            continue
        consumed.update({protocol.name, memory_name, postgres_name})
        triples.append((protocol, memory, postgres))  # type: ignore[arg-type]

    if not triples and unit.name not in NO_PROTOCOL_UNITS:
        problems.append(
            f"单元 `{unit.name}`：找不到任何 `(Protocol, InMemory*, Postgres*)` 三元组"
            f"（实测协议 {sorted(p.name for p in protocols)}）；若该单元本就没有协议，"
            f"须登记进 NO_PROTOCOL_UNITS 并写明理由"
        )
    leftovers = sorted(
        name
        for name, info in classes.items()
        if name not in consumed
        and (name.startswith(MEMORY_PREFIX) or name.startswith(POSTGRES_PREFIX))
    )
    if leftovers:
        problems.append(
            f"单元 `{unit.name}`：实现类 {leftovers} 找不到对应协议（应为 "
            f"`{'/'.join([MEMORY_PREFIX, POSTGRES_PREFIX])}<协议名>`）"
        )
    return triples, problems


def _region_calls(
    modules: dict[Path, ast.Module], paths: Iterable[Path], prefix: str, records: frozenset[str]
) -> dict[str, set[frozenset[str]]]:
    """统计某个区内对 record 的**直接构造点**关键字集合。

    区 = 该文件里 `<prefix>*` 类的方法体 + 模块级函数体。两侧都包含模块级函数，
    是因为 PG 侧的行映射器（`_to_*`）就写在模块级——v2 **不**对它做名字豁免，
    它的构造点与类方法里的构造点一视同仁地进入形状集合（这样"映射器里少写字段"
    同样判红）。

    Args:
        modules: 文件 → 语法树。
        paths: 该区的文件。
        prefix: 类名前缀（`InMemory` / `Postgres`）。
        records: 单元内 record 类名集合。

    Returns:
        dict[str, set[frozenset[str]]]: record 名 → 关键字集合的集合。
    """
    found: dict[str, set[frozenset[str]]] = {}
    bodies: list[ast.AST] = []
    for path in paths:
        module = modules.get(path)
        if module is None:
            continue
        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name.startswith(prefix):
                bodies.extend(
                    item
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                bodies.append(node)
    for body in bodies:
        # 内层变量名与上面 `for node in module.body`（`stmt`）区分开：`ast.walk()`
        # 产出的是任意 `ast.AST`，同名会让类型检查器把两轮的 `node` 当成同一个变量。
        for walked in ast.walk(body):
            if not isinstance(walked, ast.Call):
                continue
            if not isinstance(walked.func, ast.Name) or walked.func.id not in records:
                continue
            keywords = frozenset(item.arg for item in walked.keywords if item.arg is not None)
            found.setdefault(walked.func.id, set()).add(keywords)
    return found


def _signature_diff(memory: MethodInfo, postgres: MethodInfo) -> list[str]:
    """比较两侧同名方法的签名形状（R3）。

    Args:
        memory: 内存侧方法。
        postgres: PG 侧方法。

    Returns:
        list[str]: 差异说明（空表示一致）。
    """
    issues: list[str] = []
    if memory.positional != postgres.positional:
        issues.append(f"位置参数 {list(memory.positional)} != {list(postgres.positional)}")
    if memory.keyword_only != postgres.keyword_only:
        issues.append(
            f"keyword-only 参数 {list(memory.keyword_only)} != {list(postgres.keyword_only)}"
        )
    if memory.vararg != postgres.vararg:
        issues.append(f"`*args` 名 {memory.vararg} != {postgres.vararg}")
    if memory.kwarg != postgres.kwarg:
        issues.append(f"`**kwargs` 名 {memory.kwarg} != {postgres.kwarg}")
    if memory.is_async != postgres.is_async:
        issues.append(
            f"同步性不一致（内存侧 {'async' if memory.is_async else 'sync'} / "
            f"PG 侧 {'async' if postgres.is_async else 'sync'}）"
        )
    if memory.is_property != postgres.is_property:
        issues.append("`@property` 属性性不一致")
    memory_defaults = dict(memory.defaults)
    postgres_defaults = dict(postgres.defaults)
    for name in sorted(set(memory_defaults) & set(postgres_defaults)):
        if memory_defaults[name] != postgres_defaults[name]:
            issues.append(f"默认值 `{name}`={memory_defaults[name]} != {postgres_defaults[name]}")
    return issues


def _check_impl_pair(
    unit: Unit, protocol: ClassInfo, memory: ClassInfo, postgres: ClassInfo
) -> tuple[list[Violation], list[str]]:
    """对一个三元组跑 R1 / R2 / R3 / R5。

    Args:
        unit: 仓储单元。
        protocol: 协议类。
        memory: 内存实现类。
        postgres: PG 实现类。

    Returns:
        tuple[list[Violation], list[str]]: `(违规列表, INFO 列表)`。
    """
    violations: list[Violation] = []
    infos: list[str] = []
    protocol_methods = _method_map(protocol)
    memory_methods = _method_map(memory)
    postgres_methods = _method_map(postgres)

    # R1：协议公开方法 ⊆ 两侧。
    for name in protocol_methods:
        if name not in memory_methods:
            violations.append(
                Violation(
                    rule="R1",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"协议 `{protocol.name}` 声明了 `{name}`，内存实现 `{memory.name}` 缺失"
                        f"（{memory.path.name}）"
                    ),
                )
            )
        if name not in postgres_methods:
            violations.append(
                Violation(
                    rule="R1",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"协议 `{protocol.name}` 声明了 `{name}`，PG 实现 `{postgres.name}` 缺失"
                        f"（{postgres.path.name}）"
                    ),
                )
            )

    # R2：两侧公开面（去掉测试钩子）必须逐字相同；测试钩子出现在 PG 侧单独判红。
    pg_hooks = [name for name in postgres_methods if name in TEST_HOOKS]
    for name in pg_hooks:
        violations.append(
            Violation(
                rule="R2",
                unit=unit.name,
                subject=name,
                detail=(
                    f"测试钩子 `{name}` 出现在 PG 实现 `{postgres.name}` 上"
                    f"（TEST_HOOKS 只允许内存侧，PG 侧出现即视为测试钩子漏进生产实现）"
                ),
            )
        )
    memory_only = [
        name for name in memory_methods if name not in postgres_methods and name not in TEST_HOOKS
    ]
    postgres_only = [
        name for name in postgres_methods if name not in memory_methods and name not in TEST_HOOKS
    ]
    if memory_only or postgres_only:
        # 逐方法各发一条（而不是把一组方法压成一个 `/` 连接的 subject）：基线的粒度
        # 决定了 stale 检测的粒度——"修好两个、还剩一个"必须能表达出来，否则只能
        # 全有或全无地挂账。
        for name in memory_only:
            violations.append(
                Violation(
                    rule="R2",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 只在内存实现 `{memory.name}` 上存在（第 "
                        f"{memory_methods[name].lineno} 行），PG 实现 `{postgres.name}` 无同名方法"
                    ),
                )
            )
        for name in postgres_only:
            violations.append(
                Violation(
                    rule="R2",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 只在 PG 实现 `{postgres.name}` 上存在（第 "
                        f"{postgres_methods[name].lineno} 行），内存实现 `{memory.name}` 无同名方法"
                    ),
                )
            )

    # R3：同名方法签名形状一致（返回注解按设计不判）。
    for name, memory_method in memory_methods.items():
        postgres_method = postgres_methods.get(name)
        if postgres_method is None:
            continue
        issues = _signature_diff(memory_method, postgres_method)
        if issues:
            violations.append(
                Violation(
                    rule="R3",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 签名不一致（内存 {memory.name}:{memory_method.lineno} / "
                        f"PG {postgres.name}:{postgres_method.lineno}）：{'；'.join(issues)}"
                    ),
                )
            )

    # R5：双实现都有、协议未声明 —— 只提示。
    undeclared = [
        name
        for name in memory_methods
        if name in postgres_methods and name not in protocol_methods and name not in TEST_HOOKS
    ]
    if undeclared:
        infos.append(
            f"R5 {unit.name} `{protocol.name}`：双实现都有但协议未声明 {undeclared}"
            f"（只提示，不判红：补协议或删实现都由评审决定）"
        )
    return violations, infos


def _check_records(
    unit: Unit,
    classes: dict[str, ClassInfo],
    modules: dict[Path, ast.Module],
) -> tuple[list[Violation], list[str]]:
    """对一个单元跑 R4 v2（record 构造点形状**逐点对点**一致）。

    口径（v2 收紧，见模块 docstring 的「R4 为什么不用映射器豁免」）：

    - 每侧收集直接构造点的关键字集合的**集合**（`set[frozenset[str]]`，同形构造自动去重）；
    - 两侧**逐集合相等**才通过——任一侧多一个"少了某个字段"的形状即判红；
    - 任一侧**没有**任何直接构造点 → 只 `[INFO]` 跳过（该侧经映射器/服务层构造，无可比对象）；
    - **不再识别映射器**（旧 v1 的 `_to_<stem>` 豁免）：QA 实测"在实现里加一个与实现无关的
      空 `_to_role` 就能把真实漂移洗成 INFO 跳过"，名字豁免等于给门禁开后门。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类。
        modules: 文件 → 语法树。

    Returns:
        tuple[list[Violation], list[str]]: `(违规列表, INFO 列表)`。
    """
    violations: list[Violation] = []
    infos: list[str] = []
    records = frozenset(name for name, info in classes.items() if info.is_dataclass)
    if not records:
        return violations, infos
    memory_calls = _region_calls(modules, unit.memory_files, MEMORY_PREFIX, records)
    postgres_calls = _region_calls(modules, unit.postgres_files, POSTGRES_PREFIX, records)
    for record in sorted(records):
        memory_shapes = memory_calls.get(record, set())
        postgres_shapes = postgres_calls.get(record, set())
        if not memory_shapes or not postgres_shapes:
            side = "内存区" if not memory_shapes else "PG 区"
            infos.append(
                f"R4 {unit.name} `{record}`：{side}没有直接构造点"
                f"（该侧经映射器/服务层构造），无可比对象，跳过"
            )
            continue
        if memory_shapes == postgres_shapes:
            continue
        memory_only = [
            sorted(shape) for shape in sorted(memory_shapes - postgres_shapes, key=sorted)
        ]
        postgres_only = [
            sorted(shape) for shape in sorted(postgres_shapes - memory_shapes, key=sorted)
        ]
        violations.append(
            Violation(
                rule="R4",
                unit=unit.name,
                subject=record,
                detail=(
                    f"`{record}` 的构造点形状两侧不一致（每侧按**形状集合**逐一对齐，"
                    f"不接受「并集含该字段」这种粗口径）：仅内存区的形状 {memory_only}；"
                    f"仅 PG 区的形状 {postgres_only}"
                    f"（形状里缺的字段在该后端恒为默认值——P1-8b 就是这么来的）"
                ),
            )
        )
    return violations, infos


def _dataclass_field_names(modules: dict[Path, ast.Module]) -> dict[str, tuple[str, ...]]:
    """收集单元内全部 dataclass 的字段名（只取类体里的 `AnnAssign`）。

    Args:
        modules: 文件 → 语法树。

    Returns:
        dict[str, tuple[str, ...]]: `类名 → 字段名序列`（保持声明顺序）。
    """
    result: dict[str, tuple[str, ...]] = {}
    for module in modules.values():
        for node in ast.walk(module):
            if not isinstance(node, ast.ClassDef) or not _has_decorator(node, "dataclass"):
                continue
            names = tuple(
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            )
            if names:
                result[node.name] = names
    return result


def _annotation_record_name(annotation: ast.expr | None) -> str | None:
    """取返回注解里的 record 类名。

    映射器常用三种写法：`-> PlanRecord`、`-> "PlanRecord"`、`-> records.PlanRecord`。
    三种都归一化成裸类名，**不看函数名**——`memory._to_setting → MemorySettingRecord`
    证明按名字配对会漏也会错。

    Args:
        annotation: 返回注解节点（可能为 None）。

    Returns:
        str | None: 类名；无法识别时返回 None。
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.split(".")[-1].strip("'\"[] ")
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        inner = annotation.value
        return _annotation_record_name(inner) if isinstance(inner, ast.Attribute) else None
    return None


def _mapper_kwarg_shapes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    record: str,
) -> list[set[str]]:
    """收集映射器函数体里对 `record(...)` 的每个构造点的关键字集合。

    Args:
        function: 映射器函数节点。
        record: record 类名。

    Returns:
        list[set[str]]: 每个构造点的 kwarg 集合（保持出现顺序）。
    """
    shapes: list[set[str]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        called = target.id if isinstance(target, ast.Name) else None
        if called != record:
            continue
        shapes.append({keyword.arg for keyword in node.keywords if keyword.arg is not None})
    return shapes


def _check_mapper_coverage(
    unit: Unit,
    classes: dict[str, ClassInfo],
    modules: dict[Path, ast.Module],
) -> list[MapperFinding]:
    """R6：`_to_*` 映射器必须覆盖其返回 record 的全部字段。

    为什么需要它：R4 只比对**两侧直接构造点**，一侧走映射器时整条 record 会被
    `[INFO]` 跳过；映射器少写一个字段，两侧都不会报，运行期表现为"某字段恒为默认值"。

    判据（三条，缺都会漏）：

    1. **按返回注解配对**，不按函数名——`_to_setting → MemorySettingRecord` 是反例；
    2. 取该函数体内所有 `record(...)` 构造点的 kwarg **并集**；
    3. **排除 kwarg 为空的零值构造**（`TaskStats()` 这类"全默认"形状与字段集合不相交，
       不排除会把"某字段只在另一处构造点写"误判成已覆盖）。

    已知边界：**并集口径抓不到"某条分支少写字段"**（与 R4 逐形状不同）。这是刻意取舍——
    R6 的目标是"字段级完全失联"，分支级覆盖由 R4 的形状集合负责。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类（用于确认 record 是 dataclass）。
        modules: 文件 → 语法树。

    Returns:
        list[MapperFinding]: 未覆盖字段的发现列表（含豁免项）。
    """
    record_fields = {
        name: names
        for name, names in _dataclass_field_names(modules).items()
        if name in classes and classes[name].is_dataclass
    }
    if not record_fields:
        return []

    targets = set(unit.postgres_files)
    findings: list[MapperFinding] = []
    for path, module in modules.items():
        if path not in targets:
            continue
        for node in module.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            returned = _annotation_record_name(node.returns)
            if returned is None or returned not in record_fields:
                continue
            shapes = _mapper_kwarg_shapes(node, returned)
            if not shapes:
                # 完全没有直接构造点（如 `default_ops_settings` 逐个字段拼默认值、或委托给
                # 另一个 helper）：与 R4 的"单侧无构造点"同口径，不制造噪声。
                continue
            covered: set[str] = set()
            for shape in shapes:
                covered |= shape
            # 零值构造（`X()`）的 kwarg 集为空，对并集无贡献，天然被排除；
            # 但若**全部**构造点都是零值构造，covered 为空 → 全部字段都会被报出来，
            # 这是正确的（该映射器把整个 record 留成默认值，属最严重的字段失联）。
            for name in record_fields[returned]:
                if name in covered:
                    continue
                reason, expire_when = R6_EXEMPTIONS.get((unit.name, returned, name), (None, None))
                findings.append(
                    MapperFinding(
                        unit=unit.name,
                        record=returned,
                        field=name,
                        mapper=node.name,
                        path=path,
                        lineno=node.lineno,
                        exempt=reason,
                        expire_when=expire_when,
                    )
                )
    findings.sort(key=lambda item: (item.unit, item.record, item.field))
    return findings


def _merge_ddl_lines(body: str) -> list[str]:
    """把建表体按"列定义"聚合（续行并入上一条）。

    `CHECK (...)` 常写在下一行，按行取会把续行当成新列；这里只在行首是
    「标识符 + 空白 + 非空白」且不是关键字时才开新条目。

    Args:
        body: `CREATE TABLE (...)` 括号内的文本。

    Returns:
        list[str]: 每条列定义（含其续行）。
    """
    merged: list[str] = []
    for line in body.splitlines():
        head = re.match(r"\s*([a-z_][a-z0-9_]*)\s+\S", line)
        if head is not None and head.group(1).upper() not in DDL_NON_COLUMN_KEYWORDS:
            merged.append(line)
        elif merged:
            merged[-1] = f"{merged[-1]} {line.strip()}"
    return merged


def _is_omittable(definition: str, rest: str = "") -> bool:
    """该列是否可以不写进 INSERT（有 DEFAULT 或允许 NULL）。

    Args:
        definition: 列定义全文。
        rest: `ALTER TABLE` 里列名之后的剩余文本（与 definition 合并判断）。

    Returns:
        bool: 可省略返回 True。
    """
    text = f"{definition} {rest}".upper()
    return "DEFAULT" in text or "NOT NULL" not in text


def load_ddl_tables(deploy_root: Path) -> dict[str, DdlTable]:
    """解析 `deploy` 下全部 DDL，得到「表 → (列集合, 可省略列集合)」。

    必须同时读 `CREATE TABLE` 与 `ALTER TABLE … ADD COLUMN`：本项目"后补列"是常态
    （`usage_event` 的 profile / provider / prompt_tokens / completion_tokens 四列
    就只出现在 `init-schema-phase3.sql` 与 `migrations/0007`），只解析建表语句会把
    它们判成"不存在"——系统性假红。

    Args:
        deploy_root: `deploy` 目录。

    Returns:
        dict[str, DdlTable]: 表名 → DDL 形状。
    """
    tables: dict[str, tuple[set[str], set[str]]] = {}
    for pattern in R7_DDL_GLOBS:
        for path in sorted(deploy_root.glob(pattern)):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in re.finditer(
                r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+([a-z_][\w.]*)\s*\((.*?)\n\s*\)\s*;",
                text,
                re.DOTALL,
            ):
                table = match.group(1).split(".")[-1]
                columns, omittable = tables.setdefault(table, (set(), set()))
                for definition in _merge_ddl_lines(match.group(2)):
                    name_match = re.match(r"\s*([a-z_][a-z0-9_]*)", definition)
                    if name_match is None:
                        continue
                    columns.add(name_match.group(1))
                    if _is_omittable(definition):
                        omittable.add(name_match.group(1))
            for match in re.finditer(
                r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-z_][\w.]*)"
                r"\s+ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_][a-z0-9_]*)\s*([^;]*);",
                text,
                re.DOTALL | re.IGNORECASE,
            ):
                table = match.group(1).split(".")[-1]
                column, rest = match.group(2), match.group(3)
                existing = tables.get(table)
                if existing is None:
                    # **ALTER 不得凭空造表**：否则"CREATE 改了名、ALTER 还留着"时，
                    # 漂移的表会以"只有后补列"的形态继续存在 → `set(targets)-set(ddl)`
                    # 不报 orphan、dataclass 静默转 skipped，门禁于是"覆盖塌陷但全绿"。
                    # 宁可让这张表不存在（INSERT 它就会撞 orphan 判据、exit 2）。
                    continue
                columns, omittable = existing
                columns.add(column)
                if _is_omittable(rest):
                    omittable.add(column)
    return {
        table: DdlTable(columns=frozenset(columns), omittable=frozenset(omittable))
        for table, (columns, omittable) in tables.items()
    }


def _module_constants(module: ast.Module) -> dict[str, str]:
    """收集模块级字符串常量（表名都写在这里，如 `USAGE_EVENT_TABLE = "usage_event"`）。

    Args:
        module: 语法树。

    Returns:
        dict[str, str]: 常量名 → 字符串值。
    """
    constants: dict[str, str] = {}
    for node in module.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.target.id] = node.value.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


def _sql_text(node: ast.expr, constants: dict[str, str]) -> str | None:
    """把 SQL 字面量还原成文本（f-string 里的表名常量替换成真实表名）。

    Args:
        node: 表达式节点。
        constants: 模块级字符串常量。

    Returns:
        str | None: SQL 文本；不是字符串字面量时返回 None。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
        elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
            resolved = constants.get(value.value.id)
            if resolved is None:
                # 表名解析不出来要比"猜一个"安全：整条语句不参与判定，并在 INFO 里报出。
                return None
            parts.append(resolved)
        else:
            return None
    return "".join(parts)


def _iter_sql_texts(node: ast.AST, constants: dict[str, str]) -> Iterator[tuple[str | None, int]]:
    """遍历"最外层"的字符串表达式，产出 `(可解析文本 | None, INSERT 提及次数)`。

    - **只看最外层**：f-string 内部的 `Constant` 片段不会再被单独产出，否则一次
      `INSERT INTO` 会被数成两次；
    - **函数调用不算叶子**：SQL 是 `fetch_one(f"...")` 的实参，把 `Call` 当叶子会
      直接漏掉全部语句；
    - **解析不出的形态也要计数**（`"INSERT INTO " + table`、`"INSERT INTO {} ".format(...)`）：
      提及次数从它的字符串常量里数，这样"有 INSERT 但门禁看不见"必然进 `unresolved`，
      而不是安静少看几句。

    Args:
        node: 语法树节点。
        constants: 模块级字符串常量（表名解析用）。

    Yields:
        tuple[str | None, int]: `(可解析文本, INSERT 提及次数)`；不可解析时第一项为 None。
    """
    if isinstance(node, ast.JoinedStr):
        text = _sql_text(node, constants)
        mentions = sum(
            inner.value.upper().count("INSERT INTO")
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        )
        if text is not None:
            mentions = max(mentions, text.upper().count("INSERT INTO"))
        yield text, mentions
        return
    if isinstance(node, ast.BinOp):
        mentions = sum(
            inner.value.upper().count("INSERT INTO")
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        )
        yield None, mentions
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value, node.value.upper().count("INSERT INTO")
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_sql_texts(child, constants)


def _insert_targets(
    modules: dict[Path, ast.Module],
    files: tuple[Path, ...],
) -> tuple[dict[str, set[str]], int, int]:
    """收集某侧文件里所有 `INSERT INTO <表> (...) VALUES` 的「表 → 列并集」。

    **必须走 AST 而不是正则扫源码**：表名是 f-string 常量（`{USAGE_EVENT_TABLE}`），
    文本匹配 `INSERT INTO usage_event` 会 0 命中——这条坑在 2C-4 已经踩过一次。

    **解析失败必须计数**：表名是局部变量、或 SQL 用 `+` / `.format()` 拼出来时，
    该语句无法参与判定；若只 `continue`，门禁会安静地少看几句、然后报"0 缺口"——
    与它要防的扫描范围缩水是同一族缺陷。

    Args:
        modules: 文件 → 语法树。
        files: 只扫这些文件（PG 侧）。

    Returns:
        tuple[dict[str, set[str]], int, int]: `(表 → 列并集, 可判定语句数, 不可解析语句数)`。
    """
    targets: dict[str, set[str]] = {}
    statements = 0
    unresolved = 0
    wanted = set(files)
    for path, module in modules.items():
        if path not in wanted:
            continue
        constants = _module_constants(module)
        for text, mentions in _iter_sql_texts(module, constants):
            if not mentions:
                continue
            matched = 0
            if text is not None:
                for match in re.finditer(
                    r"INSERT\s+INTO\s+([a-z_][\w.]*)\s*\(([^)]*)\)\s*VALUES",
                    text,
                    re.DOTALL | re.IGNORECASE,
                ):
                    matched += 1
                    table = match.group(1).split(".")[-1]
                    columns = {item.strip() for item in match.group(2).split(",") if item.strip()}
                    targets.setdefault(table, set()).update(columns)
            statements += matched
            unresolved += max(0, mentions - matched)
    return targets, statements, unresolved


def _candidate_tables(
    record_fields: frozenset[str],
    candidates: dict[str, set[str]],
    ddl: dict[str, DdlTable],
) -> list[tuple[int, str]]:
    """列出该 record 的可能对应表（已按重叠列数降序）。

    容差的作用：`PlanRecord` 与 `billing_order` 只重叠 4 列、有 9 个字段不是该表的列，
    这种"沾边"必须排除，否则会拿错误的表去判覆盖。

    Args:
        record_fields: record 的字段集合。
        candidates: 本单元 PG 侧出现过的「表 → INSERT 列并集」。
        ddl: 表 → DDL 形状。

    Returns:
        list[tuple[int, str]]: `(重叠列数, 表名)`，按重叠降序。
    """
    scored: list[tuple[int, str]] = []
    for table in candidates:
        shape = ddl.get(table)
        if shape is None:
            continue
        non_column = len(record_fields - shape.columns)
        if non_column > R7_TABLE_TOLERANCE * len(record_fields):
            continue
        # 亲和度按**表列**算，不按 INSERT 列算：某张表只被写了 2 列时，
        # 按 INSERT 列算会得出"重叠 2 < 下限"→ 整张表被跳过，缺口永远看不见。
        overlap = len(record_fields & shape.columns)
        if overlap >= R7_MIN_OVERLAP:
            scored.append((overlap, table))
    scored.sort(reverse=True)
    return scored


def _check_write_coverage(
    unit: Unit,
    classes: dict[str, ClassInfo],
    modules: dict[Path, ast.Module],
    ddl: dict[str, DdlTable],
    stats: WriteStats,
) -> tuple[list[WriteFinding], list[str]]:
    """R7：写侧 INSERT 必须覆盖 record 的全部**表列**字段。

    判据（三层）：

    1. `record → 表` 按列名重叠最佳匹配（唯一、重叠 ≥ 3、非表列占比 ≤ 容差）；
    2. `covered` = 该表全部 INSERT 语句的列并集（同表多条 INSERT 取并集：
       `usage_event` 就有"全列"与"降级 12 列"两条静态 SQL）；
    3. `missing = (字段 ∩ 表列) - covered - DDL 可省略列`。

    三条关键口径（都踩过）：

    - **只比表列**：`queue_position` / `history` / `uploader_name` / `member_count`
      这类字段本来就不是表列（派生值 / 预留 / 展示冗余），拿它们判红就是系统性假红；
    - **可省略列白名单来自 DDL**（有 `DEFAULT` 或允许 NULL）：`occurred_at` 由
      `DEFAULT now()` 生成，不能要求 INSERT 写它；
    - **并集口径的固有盲区**：抓不到"某条分支少写字段"（fallback 路径正是设计如此），
      它只负责"字段级完全失联"。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类。
        modules: 文件 → 语法树。
        ddl: 表 → DDL 形状。
        stats: 覆盖率计数（原地累加，供 fail-closed 下限与摘要行使用）。

    Returns:
        tuple[list[WriteFinding], list[str]]: `(缺失发现, INFO 说明)`。
    """
    record_fields = {
        name: frozenset(names)
        for name, names in _dataclass_field_names(modules).items()
        if name in classes and classes[name].is_dataclass
    }
    # 先统计 INSERT：即便本单元没有 dataclass，语句数也是覆盖率下限的依据（N4）。
    targets, statements, unresolved = _insert_targets(modules, unit.postgres_files)
    stats.statements += statements
    stats.unresolved += unresolved
    if not record_fields:
        return [], []
    infos: list[str] = []
    stats.dataclasses += len(record_fields)
    if not targets:
        # 整单元没有可判定的 INSERT（如 `audit`：PG 写入故意留在 business 层）。
        # 必须计数并说一句——否则这 N 个 dataclass 会从分母里消失，而输出看上去"全绿"。
        stats.unevaluated += len(record_fields)
        stats.unevaluated_pairs.update((unit.name, name) for name in record_fields)
        infos.append(
            f"R7 {unit.name}：本单元没有 `INSERT INTO … (列) VALUES` 语句，"
            f"{len(record_fields)} 个 dataclass 未评估（不是通过）"
        )
        return [], infos

    orphan = sorted(set(targets) - set(ddl))
    if orphan:
        stats.orphan_tables.update(orphan)
        infos.append(
            f"R7 {unit.name}：表 {'、'.join(orphan)} 不在 DDL 中，"
            f"写入这些表的语句不参与判定（**覆盖塌陷，不是通过**）"
        )

    findings: list[WriteFinding] = []
    loose: list[str] = []
    ambiguous: list[str] = []
    reported: set[tuple[str, str]] = set()
    for record, fields in sorted(record_fields.items()):
        scored = _candidate_tables(fields, targets, ddl)
        if not scored:
            # 不是行式 dataclass（store / stats / patch / 无写路径的纯读 record）。
            stats.skipped += 1
            if (unit.name, record) not in R7_SKIPPED_ALLOWLIST:
                stats.unexpected_skips.add((unit.name, record))
            continue
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            ambiguous.append(f"{record}（{scored[0][1]} / {scored[1][1]} 并列）")
            stats.skipped += 1
            stats.unexpected_skips.add((unit.name, record))
            continue
        table = scored[0][1]
        shape = ddl[table]
        stats.paired += 1
        covered = targets[table] & fields
        non_column = sorted(fields - shape.columns)
        if non_column:
            loose.append(f"{record}: {', '.join(non_column)}")
        for column in sorted((fields & shape.columns) - covered - shape.omittable):
            reported.add((record, column))
            findings.append(
                WriteFinding(
                    unit=unit.name,
                    record=record,
                    table=table,
                    field=column,
                    detail=(
                        f"`{record}.{column}` 是 `{table}` 的必填列（无 DEFAULT、非 NULL），"
                        f"但本单元没有任何 INSERT 写它"
                    ),
                )
            )
    if ambiguous:
        infos.append(f"R7 {unit.name}：对应表不唯一的 dataclass（跳过）：{'；'.join(ambiguous)}")
    for (note_unit, note_record, note_field), note in sorted(R7_NOTES.items()):
        if note_unit == unit.name and (note_unit, note_record, note_field) not in {
            (unit.name, record, column) for record, column in reported
        }:
            infos.append(f"R7 语义注记 {note_unit} `{note_record}.{note_field}`：{note}")
    if loose:
        infos.append(
            f"R7 {unit.name}：非表列字段（派生/预留/展示冗余，不判红）：{'；'.join(loose)}"
        )
    return findings, infos


@lru_cache(maxsize=1)
def default_ddl_tables() -> dict[str, DdlTable]:
    """加载仓库自带的 DDL（`deploy/*.sql` + `deploy/migrations/*.sql`）。

    只加载一次：`scan()` 在自检里会被调用二十多次，重复读 16 个 SQL 文件纯属浪费。

    Returns:
        dict[str, DdlTable]: 表名 → DDL 形状。
    """
    return load_ddl_tables(DEFAULT_DEPLOY_ROOT)


def scan(repositories_root: Path, *, ddl: dict[str, DdlTable] | None = None) -> ScanResult:
    """扫描仓储目录，产出全部判定结果。

    Args:
        repositories_root: 仓储目录。
        ddl: 表 → DDL 形状（默认读仓库自带的 `deploy/**/*.sql`；自检可注入假 DDL）。

    Returns:
        ScanResult: 扫描结果。
    """
    tables = default_ddl_tables() if ddl is None else ddl
    result = ScanResult(units=[], violations=[], infos=[], problems=[])
    units, problems = discover_units(repositories_root)
    result.units = units
    result.problems.extend(problems)
    for unit in units:
        classes, modules, load_problems = _load_classes(unit)
        result.problems.extend(load_problems)
        if load_problems:
            continue
        triples, pair_problems = _pair_triples(unit, classes)
        result.problems.extend(pair_problems)
        for protocol, memory, postgres in triples:
            violations, infos = _check_impl_pair(unit, protocol, memory, postgres)
            result.violations.extend(violations)
            result.infos.extend(infos)
        if triples:
            violations, infos = _check_records(unit, classes, modules)
            result.violations.extend(violations)
            result.infos.extend(infos)
        # R6 不依赖三元组：它只比对「PG 侧映射器的构造点」与「record 字段集」。
        # 放在 `if triples:` 内会让**无协议单元**（`audit`）的映射器永远不被检查——
        # 那是一处"看着有判据、实际不执行"的假覆盖，2026-10-26 独立复核抓到的。
        result.r6.extend(_check_mapper_coverage(unit, classes, modules))
        # R7 同理：只依赖「INSERT 语句」与「record 字段集」，与协议三元组无关。
        write_findings, write_notes = _check_write_coverage(
            unit, classes, modules, tables, result.r7_stats
        )
        result.r7.extend(write_findings)
        result.r7_info.extend(write_notes)
    result.violations.sort(key=lambda item: item.key())
    return result


# ===========================================================================
# 基线
# ===========================================================================


def load_baseline(
    path: Path, unit_names: set[str]
) -> tuple[dict[tuple[str, str, str], BaselineEntry], list[str], list[str]]:
    """读取并校验基线文件。

    Args:
        path: 基线路径。
        unit_names: 本轮实测的单元名集合（用于校验"unit 不在实测集"）。

    Returns:
        tuple[dict, list[str], list[str]]: `(基线条目, 格式错误, 提示)`。
    """
    entries: dict[tuple[str, str, str], BaselineEntry] = {}
    errors: list[str] = []
    notices: list[str] = []
    if not path.exists():
        notices.append(
            f"基线文件不存在（{path}），按**空基线**处理：任何违规都判红"
            f"（fail-closed；`--baseline /dev/null` 的 exit 1 就靠这条生效）"
        )
        return entries, errors, notices
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 权限问题极少见
        errors.append(f"基线文件 {path} 读取失败：{exc}")
        return entries, errors, notices

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, tail = line.partition("--")
        if not separator:
            errors.append(
                f"基线第 {lineno} 行：缺少 `--` 分隔符"
                f"（格式 `<rule> <unit> <subject> -- <审计编号> <理由>`）"
            )
            continue
        head_tokens = head.split()
        if len(head_tokens) != 3:
            errors.append(
                f"基线第 {lineno} 行：`--` 前应为 3 列（rule / unit / subject），"
                f"实际 {len(head_tokens)} 列：{head_tokens}"
            )
            continue
        rule, unit, subject = head_tokens
        tail_tokens = tail.split(maxsplit=1)
        if not tail_tokens:
            errors.append(f"基线第 {lineno} 行：`--` 后缺审计编号与理由")
            continue
        audit_id = tail_tokens[0]
        reason = tail_tokens[1].strip() if len(tail_tokens) > 1 else ""
        if rule not in BASELINE_RULES:
            errors.append(
                f"基线第 {lineno} 行：rule `{rule}` 不在白名单 {sorted(BASELINE_RULES)}"
                f"（R5 只报 INFO，永不判红，因此不可登记）"
            )
            continue
        if unit not in unit_names:
            errors.append(
                f"基线第 {lineno} 行：unit `{unit}` 不在本轮实测单元集"
                f"（{sorted(unit_names)}）——拼错的基线条目会静默失效，故直接判错"
            )
            continue
        if not audit_id.startswith(AUDIT_ID_PREFIXES):
            errors.append(
                f"基线第 {lineno} 行：审计编号 `{audit_id}` 必须以 "
                f"{'/'.join(AUDIT_ID_PREFIXES)} 开头"
            )
            continue
        if not reason:
            errors.append(f"基线第 {lineno} 行：缺理由（必须写消除计划，否则基线就是免死金牌）")
            continue
        entry = BaselineEntry(
            rule=rule,
            unit=unit,
            subject=subject,
            audit_id=audit_id,
            reason=reason,
            lineno=lineno,
        )
        if entry.key() in entries:
            errors.append(
                f"基线第 {lineno} 行：与第 {entries[entry.key()].lineno} 行重复"
                f"（{rule} {unit} {subject}）"
            )
            continue
        entries[entry.key()] = entry
    return entries, errors, notices


# ===========================================================================
# 主流程
# ===========================================================================


def _format_unit_summary(units: Sequence[Unit]) -> str:
    """生成单元形态摘要（包形态必须逐一点名，这是拆分进度的机械可读证据）。

    Args:
        units: 单元列表。

    Returns:
        str: 摘要文本。
    """
    packages = [unit.name for unit in units if unit.kind == "package"]
    files = [unit.name for unit in units if unit.kind == "file"]
    return (
        f"单元 {len(units)} 个（包形态 {len(packages)}：{packages or '无'}；"
        f"文件形态 {len(files)}：{files}）"
    )


def _print_unit_table(units: Sequence[Unit]) -> None:
    """打印单元明细表（`--show-units`）。

    Args:
        units: 单元列表。
    """
    for unit in units:
        shim = f" + shim {unit.shim.name}" if unit.shim is not None else ""
        print(
            f"[INFO] 单元 {unit.name}（{unit.kind}）"
            f" 文件 {[item.name for item in unit.files]}{shim}"
        )


def run(
    repositories_root: Path,
    baseline_path: Path,
    min_units: int,
    show_units: bool = False,
    strict_r6: bool = False,
    strict_r7: bool = False,
    ddl: dict[str, DdlTable] | None = None,
    r7_min_inserts: int = 0,
    r7_min_paired: int = 0,
    r7_coverage_guard: bool = False,
) -> int:
    """执行一次完整门禁。

    Args:
        repositories_root: 仓储目录。
        baseline_path: 基线路径。
        min_units: 可判定单元数下限。
        show_units: 是否打印单元明细。
        strict_r6: 是否把 R6 的 WARN 计入退出码（默认关闭，WARN-only）。
        strict_r7: 是否把 R7 的 WARN 计入退出码（默认关闭，WARN-only）。
        ddl: 表 → DDL 形状（默认读仓库自带 SQL；自检可注入）。
        r7_min_inserts: R7 扫描到的 INSERT 条数下限（低于即 exit 2；自检传 0）。
        r7_min_paired: R7 配上表的 dataclass 数下限（低于即 exit 2；自检传 0）。
        r7_coverage_guard: 是否校验"未评估白名单"。**默认关**，只由 `main()` 在真实仓开启；
            自检的合成仓储天然没有 INSERT，默认开会把每条用例都判死。

    Returns:
        int: 退出码（0 / 1 / 2）。
    """
    if not repositories_root.exists():
        print(f"[FAIL] 仓储目录不存在：{repositories_root}（fail-closed：目录被移动必须判红）")
        return 2
    if not repositories_root.is_dir():
        print(f"[FAIL] 仓储路径不是目录：{repositories_root}（fail-closed）")
        return 2

    result = scan(repositories_root, ddl=ddl)
    if result.problems:
        for problem in result.problems:
            print(f"[FAIL] {problem}")
        print(f"[FAIL] 结构性问题 {len(result.problems)} 条 → exit 2")
        return 2
    if show_units:
        _print_unit_table(result.units)
    for info in result.infos:
        print(f"[INFO] {info}")

    # R6：映射器字段覆盖（默认 WARN-only；只有 --strict-r6 才计入退出码）。
    r6_warn = 0
    for finding in result.r6:
        if finding.is_exempt:
            print(
                f"[INFO] {finding.describe()} -- 豁免：{finding.exempt}"
                f"（失效条件：{finding.expire_when}）"
            )
            continue
        r6_warn += 1
        print(
            f"[WARN] {finding.describe()} -- 映射器未写该字段，"
            f"读回时恒为 record 默认值（R6 只报不判红；要判红请用 --strict-r6）"
        )

    # R7：写侧 INSERT 字段覆盖（默认 WARN-only；只有 --strict-r7 才计入退出码）。
    for info in result.r7_info:
        print(f"[INFO] {info}")
    print(f"[INFO] R7 覆盖：{result.r7_stats.summary()}")
    r7_warn = 0
    # 与上面 R6 那轮（`MapperFinding`）区分开：R7 的元素是 `WriteFinding`，
    # 两者字段集不同（R7 有 table/detail），复用 `finding` 会让读代码的人
    # 和类型检查器都以为是同一个对象。
    for r7_finding in result.r7:
        r7_warn += 1
        print(
            f"[WARN] R7 {r7_finding.unit} `{r7_finding.record}.{r7_finding.field}`"
            f"（表 `{r7_finding.table}`） -- {r7_finding.detail}"
            f"（R7 只报不判红；要判红请用 --strict-r7）"
        )
    if result.r7_stats.unresolved:
        print(
            f"[FAIL] R7 有 {result.r7_stats.unresolved} 条 INSERT 的语句形态无法静态解析"
            f"（表名用局部变量 / `+` 拼接 / `.format()`）——这些语句不参与判定，"
            f"结论不成立。请改成表名取模块常量的 f-string，或登记豁免"
        )
        return 2
    if r7_coverage_guard:
        extra_unevaluated = result.r7_stats.unevaluated_pairs - R7_UNEVALUATED_ALLOWLIST
        # 只有在**该单元本轮确实存在**时才算白名单过期；否则合成仓储（没有 audit 单元）
        # 会把整张白名单误判成 stale。
        present_units = {unit.name for unit in result.units}
        stale_unevaluated = {
            pair for pair in R7_UNEVALUATED_ALLOWLIST if pair[0] in present_units
        } - result.r7_stats.unevaluated_pairs
        if extra_unevaluated:
            pairs = "、".join(f"{unit}.{name}" for unit, name in sorted(extra_unevaluated))
            print(
                f"[FAIL] R7 有 {len(extra_unevaluated)} 个 dataclass 所在单元没有 INSERT、"
                f"且不在未评估白名单：{pairs}——写路径可能挪走了或还没写，"
                f"覆盖率已塌陷（须查清后登进 R7_UNEVALUATED_ALLOWLIST）"
            )
            return 2
        if stale_unevaluated:
            pairs = "、".join(f"{unit}.{name}" for unit, name in sorted(stale_unevaluated))
            print(
                f"[FAIL] R7 未评估白名单有 {len(stale_unevaluated)} 项已过期（该单元现在有 INSERT "
                f"或被判成配对/跳过）：{pairs}——请删掉这几行白名单"
            )
            return 2
    if result.r7_stats.unexpected_skips:
        pairs = "、".join(
            f"{unit}.{record}" for unit, record in sorted(result.r7_stats.unexpected_skips)
        )
        print(
            f"[FAIL] R7 有 {len(result.r7_stats.unexpected_skips)} 个 dataclass "
            f"配不上表且不在白名单：{pairs}——要么发生了表名漂移（覆盖塌陷），"
            f"要么新增了非行式 dataclass（须登进 R7_SKIPPED_ALLOWLIST 并说明理由）"
        )
        return 2
    if result.r7_stats.orphan_tables:
        print(
            f"[FAIL] R7 有 {len(result.r7_stats.orphan_tables)} 张被 INSERT 写入的表不在 DDL 里："
            f"{'、'.join(sorted(result.r7_stats.orphan_tables))}"
            f"——要么 SQL 会运行期失败，要么 DDL 解析漏表（两种都必须先查清，不能当通过）"
        )
        return 2
    if result.r7_stats.statements < r7_min_inserts:
        print(
            f"[FAIL] R7 只扫描到 {result.r7_stats.statements} 条 INSERT"
            f"（下限 {r7_min_inserts}）——扫描范围疑似缩水，结论不成立"
        )
        return 2
    if result.r7_stats.paired < r7_min_paired:
        print(
            f"[FAIL] R7 只配上 {result.r7_stats.paired} 个 dataclass"
            f"（下限 {r7_min_paired}）——覆盖塌陷（表名漂移/DDL 解析失效），结论不成立"
        )
        return 2

    if len(result.units) < min_units:
        print(
            f"[FAIL] 可判定单元数 {len(result.units)} < 下限 {min_units}"
            f"（fail-closed：单元数骤降通常意味着目录被移动/重命名，而不是'没违规'）"
        )
        return 2

    unit_names = {unit.name for unit in result.units}
    entries, errors, notices = load_baseline(baseline_path, unit_names)
    for notice in notices:
        print(f"[INFO] {notice}")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print(f"[FAIL] 基线格式错误 {len(errors)} 条 → exit 2")
        return 2

    matched: set[tuple[str, str, str]] = set()
    warn = 0
    fail = 0
    for violation in result.violations:
        entry = entries.get(violation.key())
        if entry is None:
            fail += 1
            print(
                f"[FAIL] {violation.rule} {violation.unit} {violation.subject} -- "
                f"{violation.detail}（未登记：请修复，或评审后进基线）"
            )
            continue
        matched.add(violation.key())
        warn += 1
        print(
            f"[WARN] {violation.rule} {violation.unit} {violation.subject} -- "
            f"{entry.audit_id} {entry.reason} | {violation.detail}"
        )

    stale = 0
    for key in sorted(entries):
        if key in matched:
            continue
        entry = entries[key]
        stale += 1
        print(
            f"[FAIL] stale {entry.rule} {entry.unit} {entry.subject} -- "
            f"基线第 {entry.lineno} 行登记的违规本轮已消失，请删掉该行"
            f"（基线只许减不许增）"
        )

    print(f"[INFO] {_format_unit_summary(result.units)}")
    code = 0 if fail == 0 and stale == 0 else 1
    if strict_r6 and r6_warn:
        code = 1
    if strict_r7 and r7_warn:
        code = 1
    tag = "[OK]" if code == 0 else "[FAIL]"
    print(
        f"{tag} 仓储双实现一致性：{len(result.units)} 单元 / "
        f"{len(result.violations)} 条判定 / {warn} WARN / {fail} FAIL / {stale} stale / "
        f"R6 WARN {r6_warn} / R7 WARN {r7_warn} → exit {code}"
    )
    return code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表（None 表示 sys.argv）。

    Returns:
        argparse.Namespace: 解析结果。
    """
    parser = argparse.ArgumentParser(
        description=(
            "仓储双实现一致性门禁（G-38）：内存实现与 PG 实现的公开面/签名/record "
            + "构造点必须一致。"
        ),
    )
    parser.add_argument(
        "--repositories-root",
        type=Path,
        default=DEFAULT_REPOSITORIES_ROOT,
        help="仓储目录（默认 apps/adapter/app/db/repositories）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="基线文件（默认 deploy/repository_parity_baseline.txt）",
    )
    parser.add_argument(
        "--min-units",
        type=int,
        default=MIN_UNITS,
        help=f"可判定单元数下限（默认 {MIN_UNITS}；**仅供 --self-test 下调**，CI 不得传）",
    )
    parser.add_argument(
        "--show-units",
        action="store_true",
        help="打印单元明细（形态 / 文件 / 兼容层）",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="在临时目录合成最小仓储样本，逐条证明门禁能红、能白、会 fail-closed",
    )
    parser.add_argument(
        "--strict-r6",
        action="store_true",
        help="把 R6（映射器字段覆盖）的 WARN 计入退出码（默认 WARN-only，不入基线）",
    )
    parser.add_argument(
        "--strict-r7",
        action="store_true",
        help="把 R7（写侧 INSERT 字段覆盖）的 WARN 计入退出码（默认 WARN-only，不入基线）",
    )
    return parser.parse_args(argv)


# ===========================================================================
# 自检（可红证明）
# ===========================================================================

#: 自检样本里的"三方一致"方法体。
_SAMPLE_GET: Final[str] = """
async def get(self, key: str) -> str | None:
    return None
"""

#: 自检样本里的 `put`。
_SAMPLE_PUT: Final[str] = """
async def put(self, key: str, value: str) -> None:
    return None
"""

#: R4 v2 用例 13：内存侧少写 `value`，同时模块级存在一个**与实现无关的空映射器**
#: `_to_widget`。v1 按映射器名字豁免，这条真实漂移会被洗成 `[INFO] 跳过`、exit 0；
#: v2 不做名字豁免，必须仍判红。
_R4_MAPPER_SAMPLE: Final[str] = (
    "# 自检样例：内存侧少写字段 + 一个与实现无关的空映射器（v1 会误跳过）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    '    value: str = ""\n'
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "def _to_widget(row: object) -> None:\n"
    "    del row\n"
    "    return None\n"
)

#: R4 v2 用例 14：PG 侧有**两个**构造点，其中一个少写 `value`（并集仍含该字段）。
#: v1 用并集比对 → 假绿；v2 逐形状对齐 → 必红。
_R4_PARTIAL_SITE_SAMPLE: Final[str] = (
    "# 自检样例：同侧两个构造点，其中一个少写字段（并集仍含该字段）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    '    value: str = ""\n'
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        cached = WidgetRecord(key=key)\n"
    "        del cached\n"
    "        return WidgetRecord(key=key, value=key)\n"
)


#: R6 自检样例：PG 侧映射器 `_to_widget` 覆盖 `WidgetRecord` 的全部字段。
#: 变体由替换 `_R6_MAPPER_FULL_BODY` 得到，保证「只改映射器」这一处变量。
_R6_MAPPER_FULL_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    return WidgetRecord(key=key, value=key)\n"
)

#: R6 变体 1：映射器少写 `value`（字段失联的最常见形态）。
_R6_MAPPER_PARTIAL_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    return WidgetRecord(key=key)\n"
)

#: R6 变体 2：映射器只做零值构造（最严重的失联，全部字段都会被报）。
_R6_MAPPER_ZERO_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    del key\n    return WidgetRecord()\n"
)

#: R6 自检样例的完整模块（record + 协议 + 双实现 + 映射器）。
_R6_MAPPER_SAMPLE: Final[str] = (
    "# 自检样例：PG 映射器的字段覆盖（R6）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    '    value: str = ""\n'
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n" + _R6_MAPPER_FULL_BODY
)


def _r7_repo_source(postgres_body: str, extra_dataclass: str = "") -> str:
    """拼一个"带 INSERT"的自检仓储模块（R7 用）。

    Args:
        postgres_body: PG 实现类的方法体（内含 INSERT）。
        extra_dataclass: 额外插入的 dataclass 源码（用于"非行式 dataclass"用例）。

    Returns:
        str: 模块源码。
    """
    return (
        '"""自检样例：写侧 INSERT 字段覆盖（R7）。"""\n'
        "from __future__ import annotations\n"
        "\n"
        "from dataclasses import dataclass\n"
        "from typing import Final, Protocol\n"
        "\n"
        'WIDGET_TABLE: Final[str] = "widget"\n'
        "\n"
        "\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class WidgetRecord:\n"
        "    key: str\n"
        '    value: str = ""\n'
        '    note: str = ""\n'
        '    computed: str = ""\n'
        "\n"
        "\n" + extra_dataclass + "class WidgetStore(Protocol):\n"
        "    async def save(self, record: WidgetRecord) -> None:\n"
        "        ...\n"
        "\n"
        "\n"
        "class InMemoryWidgetStore:\n"
        "    async def save(self, record: WidgetRecord) -> None:\n"
        "        return None\n"
        "\n"
        "\n"
        "class PostgresWidgetStore:\n"
        + textwrap.indent(textwrap.dedent(postgres_body).strip() + "\n", "    ")
    )


#: R7 自检用的假 DDL：`note` 无默认（必填），`created_at` 有默认。
_R7_DDL: Final[dict[str, DdlTable]] = {
    "widget": DdlTable(
        columns=frozenset({"key", "value", "note", "created_at"}),
        omittable=frozenset({"created_at"}),
    )
}


def _write_module(path: Path, body: str) -> None:
    """写一个自检样例模块（自动去公共缩进）。

    Args:
        path: 目标路径。
        body: 源码文本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).strip("\n") + "\n", encoding="utf-8")


def _copy_module(source: Path, target: Path) -> None:
    """把已写好的自检样例原样复制到另一处（避免二次 dedent 改坏样例）。

    Args:
        source: 源文件。
        target: 目标文件。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _repo_source(protocol_body: str, memory_body: str, postgres_body: str) -> str:
    """拼一个"文件形态"的自检仓储模块。

    Args:
        protocol_body: 协议方法体。
        memory_body: 内存实现方法体。
        postgres_body: PG 实现方法体。

    Returns:
        str: 模块源码。
    """
    return (
        '"""自检样例仓储。"""\n'
        "from __future__ import annotations\n"
        "\n"
        "from typing import Protocol\n"
        "\n"
        "\n"
        "class WidgetStore(Protocol):\n"
        f"{textwrap.indent(textwrap.dedent(protocol_body).strip(), '    ')}\n"
        "\n"
        "\n"
        "class InMemoryWidgetStore:\n"
        f"{textwrap.indent(textwrap.dedent(memory_body).strip(), '    ')}\n"
        "\n"
        "\n"
        "class PostgresWidgetStore:\n"
        f"{textwrap.indent(textwrap.dedent(postgres_body).strip(), '    ')}\n"
    )


def _rules(result: ScanResult) -> list[tuple[str, str, str]]:
    """把扫描结果压成 `(rule, unit, subject)` 列表（自检断言用）。

    Args:
        result: 扫描结果。

    Returns:
        list[tuple[str, str, str]]: 违规键列表。
    """
    return [violation.key() for violation in result.violations]


def _case(name: str, ok: bool, detail: str) -> list[str]:
    """生成一条自检结论。

    Args:
        name: 用例名。
        ok: 是否通过。
        detail: 失败时的说明。

    Returns:
        list[str]: 失败时返回一行 `[FAIL]`，通过时返回空列表。
    """
    if ok:
        return []
    return [f"[FAIL] 自检用例「{name}」不通过：{detail}"]


def _self_test() -> int:  # 自检是一个"用例清单"，拆函数只会让清单更难核对（C901 未启用）
    """在临时目录合成样本，逐条证明门禁会红、会白、会 fail-closed。

    Returns:
        int: 0 表示全部用例通过；2 表示有用例不通过（与 pytest 不同，自检失败即门禁失效）。
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="oa-parity-selftest-") as tmp_name:
        tmp = Path(tmp_name)

        # ---------------- 用例 1：三方一致 → exit 0 ----------------
        clean_root = tmp / "clean"
        _write_module(
            clean_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT, _SAMPLE_GET + _SAMPLE_PUT, _SAMPLE_GET + _SAMPLE_PUT
            ),
        )
        clean_baseline = tmp / "clean-baseline.txt"
        clean_baseline.write_text("", encoding="utf-8")
        code = run(clean_root, clean_baseline, 0)
        failures += _case("三方一致 exit 0", code == 0, f"实际 exit {code}")
        failures += _case(
            "三方一致无违规",
            _rules(scan(clean_root)) == [],
            f"实际违规 {_rules(scan(clean_root))}",
        )

        # ---------------- 用例 2：协议缺方法（内存侧未实现）→ R1 ----------------
        mem_missing_root = tmp / "mem-missing"
        _write_module(
            mem_missing_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def delete(self, key: str) -> None:\n    return None\n",
            ),
        )
        mem_missing = scan(mem_missing_root)
        failures += _case(
            "协议缺方法（内存侧）→ R1",
            ("R1", "widget", "delete") in _rules(mem_missing),
            f"实际违规 {_rules(mem_missing)}",
        )
        failures += _case(
            "内存侧缺方法的说明指向内存实现",
            any("内存实现" in violation.detail for violation in mem_missing.violations),
            "detail 未指明是哪一侧缺失",
        )

        # ---------------- 用例 3：协议缺方法（PG 侧未实现）→ R1 ----------------
        pg_missing_root = tmp / "pg-missing"
        _write_module(
            pg_missing_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
            ),
        )
        pg_missing = scan(pg_missing_root)
        failures += _case(
            "协议缺方法（PG 侧）→ R1",
            ("R1", "widget", "delete") in _rules(pg_missing),
            f"实际违规 {_rules(pg_missing)}",
        )

        # ---------------- 用例 4：PG 多一个公开方法 → R2 ----------------
        pg_extra_root = tmp / "pg-extra"
        _write_module(
            pg_extra_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def archive(self, key: str) -> None:\n    return None\n",
            ),
        )
        pg_extra = scan(pg_extra_root)
        failures += _case(
            "PG 多公开方法 → R2",
            ("R2", "widget", "archive") in _rules(pg_extra),
            f"实际违规 {_rules(pg_extra)}",
        )

        # ---------------- 用例 5：内存多 `clear` → 白名单放行 ----------------
        mem_hook_root = tmp / "mem-hook"
        _write_module(
            mem_hook_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT + "\ndef clear(self) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
            ),
        )
        mem_hook = scan(mem_hook_root)
        failures += _case(
            "内存多 clear → 白名单放行（0 违规）",
            _rules(mem_hook) == [],
            f"实际违规 {_rules(mem_hook)}",
        )

        # ---------------- 用例 6：PG 出现 `clear` → R2 ----------------
        pg_hook_root = tmp / "pg-hook"
        _write_module(
            pg_hook_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT + "\ndef clear(self) -> None:\n    return None\n",
            ),
        )
        pg_hook = scan(pg_hook_root)
        failures += _case(
            "PG 出现 clear → R2",
            ("R2", "widget", "clear") in _rules(pg_hook),
            f"实际违规 {_rules(pg_hook)}",
        )

        # ---------------- 用例 7：参数名不同 → R3 ----------------
        rename_root = tmp / "sig-rename"
        _write_module(
            rename_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET
                + "\nasync def put(self, name: str, value: str) -> None:\n    return None\n",
            ),
        )
        rename = scan(rename_root)
        failures += _case(
            "参数名不同 → R3",
            ("R3", "widget", "put") in _rules(rename),
            f"实际违规 {_rules(rename)}",
        )

        # ---------------- 用例 8：默认值不同 → R3 ----------------
        default_root = tmp / "sig-default"
        _write_module(
            default_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET
                + '\nasync def put(self, key: str, value: str = "") -> None:\n    return None\n',
                _SAMPLE_GET
                + '\nasync def put(self, key: str, value: str = "x") -> None:\n    return None\n',
            ),
        )
        default_diff = scan(default_root)
        failures += _case(
            "默认值不同 → R3",
            ("R3", "widget", "put") in _rules(default_diff),
            f"实际违规 {_rules(default_diff)}",
        )

        # ---------------- 用例 9：sync / async 不一致 → R3 ----------------
        async_root = tmp / "sig-async"
        _write_module(
            async_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                "def get(self, key: str) -> str | None:\n    return None\n" + _SAMPLE_PUT,
            ),
        )
        async_diff = scan(async_root)
        failures += _case(
            "sync↔async 不一致 → R3",
            ("R3", "widget", "get") in _rules(async_diff),
            f"实际违规 {_rules(async_diff)}",
        )

        # ---------------- 用例 10：基线三态（WARN / FAIL / stale） ----------------
        warn_baseline = tmp / "warn-baseline.txt"
        warn_baseline.write_text(
            "R2 widget archive -- P2-99 自检样例：登记后应降级为 WARN\n", encoding="utf-8"
        )
        code = run(pg_extra_root, warn_baseline, 0)
        failures += _case("基线命中 → WARN 且 exit 0", code == 0, f"实际 exit {code}")

        empty_baseline = tmp / "empty-baseline.txt"
        empty_baseline.write_text("", encoding="utf-8")
        code = run(pg_extra_root, empty_baseline, 0)
        failures += _case("基线外违规 → exit 1", code == 1, f"实际 exit {code}")

        stale_baseline = tmp / "stale-baseline.txt"
        stale_baseline.write_text(
            "R2 widget archive -- P2-99 自检样例：登记后应降级为 WARN\n"
            "R2 widget ghost -- P2-99 自检样例：本条应判 stale\n",
            encoding="utf-8",
        )
        code = run(pg_extra_root, stale_baseline, 0)
        failures += _case("基线条目消失 → stale 且 exit 1", code == 1, f"实际 exit {code}")

        # 缺失基线文件（等价于 `--baseline /dev/null`）：按空基线处理 → 存量违规必红。
        code = run(pg_extra_root, tmp / "no-such-baseline.txt", 0)
        failures += _case(
            "基线文件缺失 → 空基线 → exit 1（不是空转）", code == 1, f"实际 exit {code}"
        )

        # ---------------- 用例 11：基线格式错误（5 态）→ exit 2 ----------------
        format_cases: list[tuple[str, str]] = [
            ("列数不足", "R2 widget -- P1-1 只有两列\n"),
            ("rule 不在白名单", "R9 widget archive -- P1-1 rule 白名单之外\n"),
            ("unit 不在实测集", "R2 widget2 archive -- P1-1 单元名拼错\n"),
            ("审计编号前缀不合规", "R2 widget archive -- AUDIT-1 编号前缀不对\n"),
            (
                "重复行",
                "R2 widget archive -- P1-1 第一行\nR2 widget archive -- P1-1 重复行\n",
            ),
        ]
        for label, content in format_cases:
            bad_baseline = tmp / f"bad-baseline-{abs(hash(label))}.txt"
            bad_baseline.write_text(content, encoding="utf-8")
            code = run(pg_extra_root, bad_baseline, 0)
            failures += _case(f"基线格式错误（{label}）→ exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 12a：fail-closed 3 态 ----------------
        code = run(tmp / "no-such-root", clean_baseline, 0)
        failures += _case("仓储目录不存在 → exit 2", code == 2, f"实际 exit {code}")
        not_a_dir = tmp / "not-a-dir.py"
        not_a_dir.write_text("x = 1\n", encoding="utf-8")
        code = run(not_a_dir, clean_baseline, 0)
        failures += _case("仓储路径是文件 → exit 2", code == 2, f"实际 exit {code}")
        empty_root = tmp / "empty-root"
        empty_root.mkdir()
        code = run(empty_root, clean_baseline, MIN_UNITS)
        failures += _case("单元数低于下限 → exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 12b：包形态召回（role/ 三文件 + 纯 shim = 1 单元） ----------------
        package_root = tmp / "package"
        package_dir = package_root / "widget"
        _write_module(
            package_dir / "records.py",
            """
            '''自检样例 record。'''

            from __future__ import annotations

            from dataclasses import dataclass


            @dataclass(frozen=True, slots=True)
            class WidgetRecord:
                '''自检样例记录。'''

                key: str
                value: str = ""
            """,
        )
        _write_module(
            package_dir / "protocol.py",
            """
            '''自检样例协议。'''

            from __future__ import annotations

            from typing import Protocol

            from app.db.repositories.widget.records import WidgetRecord


            class WidgetStore(Protocol):
                '''自检样例契约。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    ...
            """,
        )
        _write_module(
            package_dir / "memory.py",
            """
            '''自检样例内存实现。'''

            from __future__ import annotations

            from app.db.repositories.widget.records import WidgetRecord


            class InMemoryWidgetStore:
                '''自检样例内存实现。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    del key
                    return WidgetRecord(key="k", value="v")

                def clear(self) -> None:
                    '''测试钩子。'''
                    return None
            """,
        )
        _write_module(
            package_dir / "postgres.py",
            """
            '''自检样例 PG 实现。'''

            from __future__ import annotations

            from app.db.repositories.widget.records import WidgetRecord


            class PostgresWidgetStore:
                '''自检样例 PG 实现。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    del key
                    return WidgetRecord(key="k", value="v")
            """,
        )
        _write_module(
            package_dir / "__init__.py",
            """
            '''自检样例公开出口。'''

            from __future__ import annotations

            from app.db.repositories.widget.memory import InMemoryWidgetStore
            from app.db.repositories.widget.postgres import PostgresWidgetStore
            from app.db.repositories.widget.protocol import WidgetStore
            from app.db.repositories.widget.records import WidgetRecord

            __all__ = ["InMemoryWidgetStore", "PostgresWidgetStore", "WidgetRecord", "WidgetStore"]
            """,
        )
        _write_module(
            package_root / "widget_repo.py",
            """
            '''兼容层：纯 re-export（自检样例）。'''

            from __future__ import annotations

            from app.db.repositories.widget import (
                InMemoryWidgetStore,
                PostgresWidgetStore,
                WidgetRecord,
                WidgetStore,
            )

            __all__ = ["InMemoryWidgetStore", "PostgresWidgetStore", "WidgetRecord", "WidgetStore"]
            """,
        )
        package_scan = scan(package_root)
        failures += _case(
            "包形态召回：文件 + 包并存识别为 1 单元",
            len(package_scan.units) == 1 and package_scan.units[0].kind == "package",
            f"实际 {[(unit.name, unit.kind) for unit in package_scan.units]}",
        )
        failures += _case(
            "包形态判定生效（无违规）",
            package_scan.violations == [] and package_scan.problems == [],
            f"实际违规 {_rules(package_scan)} / 问题 {package_scan.problems}",
        )
        code = run(package_root, clean_baseline, 0)
        failures += _case("包形态 + 纯 shim → exit 0", code == 0, f"实际 exit {code}")

        # 包形态下 R2 依然生效：给 PG 侧加一个内存侧没有的方法。
        drift_root = tmp / "package-drift"
        drift_package = drift_root / "widget"
        for source_file in sorted(package_dir.glob("*.py")):
            _copy_module(source_file, drift_package / source_file.name)
        _copy_module(package_root / "widget_repo.py", drift_root / "widget_repo.py")
        _write_module(
            drift_package / "postgres.py",
            (package_dir / "postgres.py").read_text(encoding="utf-8")
            + "\n    async def archive(self, key: str) -> None:\n        '''多出来的方法。'''\n"
            "        del key\n        return None\n",
        )
        drift_scan = scan(drift_root)
        failures += _case(
            "包形态下 R2 依然生效（PG 多方法）",
            ("R2", "widget", "archive") in _rules(drift_scan),
            f"实际违规 {_rules(drift_scan)}",
        )

        # ---------------- 用例 12c：shim 内含 ClassDef → exit 2 ----------------
        impure_root = tmp / "package-impure"
        impure_package = impure_root / "widget"
        for source_file in sorted(package_dir.glob("*.py")):
            _copy_module(source_file, impure_package / source_file.name)
        _write_module(
            impure_root / "widget_repo.py",
            """
            '''自检样例：会长出第二份实现的壳。'''

            from __future__ import annotations

            from app.db.repositories.widget.protocol import WidgetStore

            __all__ = ["WidgetStore"]


            class LegacyWidgetStore:
                '''模拟"薄壳重新长成第二份实现"。'''
            """,
        )
        code = run(impure_root, clean_baseline, 0)
        failures += _case("shim 内含 ClassDef → exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 13：空 `_to_xxx` 不得洗白 R4（v1 的假绿通道） ----------------
        mapper_root = tmp / "r4-empty-mapper"
        _write_module(mapper_root / "widget_repo.py", _R4_MAPPER_SAMPLE)
        mapper_scan = scan(mapper_root)
        failures += _case(
            "R4 v2：空 `_to_widget` 不再导致跳过（漂移仍判红）",
            ("R4", "widget", "WidgetRecord") in _rules(mapper_scan),
            f"实际违规 {_rules(mapper_scan)} / INFO {mapper_scan.infos}",
        )
        failures += _case(
            "R4 v2：空映射器场景下不得出现该 record 的跳过 INFO",
            not any("WidgetRecord" in info for info in mapper_scan.infos),
            f"实际 INFO {mapper_scan.infos}",
        )
        code = run(mapper_root, clean_baseline, 0, r7_coverage_guard=False)
        failures += _case("R4 v2：空映射器场景 exit 1", code == 1, f"实际 exit {code}")

        # ------- 用例 14：同侧两个构造点其中一处少字段（并集仍含该字段）→ 必红 -------
        partial_root = tmp / "r4-partial-site"
        _write_module(partial_root / "widget_repo.py", _R4_PARTIAL_SITE_SAMPLE)
        partial_scan = scan(partial_root)
        failures += _case(
            "R4 v2：同侧一处少字段（并集仍含该字段）→ 判红",
            ("R4", "widget", "WidgetRecord") in _rules(partial_scan),
            f"实际违规 {_rules(partial_scan)}",
        )
        failures += _case(
            "R4 v2：detail 必须列出那一处少字段的形状",
            any(
                "仅 PG 区的形状" in violation.detail
                for violation in partial_scan.violations
                if violation.rule == "R4"
            ),
            f"实际 detail {[v.detail for v in partial_scan.violations if v.rule == 'R4']}",
        )
        code = run(partial_root, clean_baseline, 0, r7_coverage_guard=False)
        failures += _case("R4 v2：同侧少字段场景 exit 1", code == 1, f"实际 exit {code}")

        # ---------------- 用例 15：R6 映射器全覆盖 → 零发现 ----------------
        r6_full_root = tmp / "r6-full"
        _write_module(r6_full_root / "widget_repo.py", _R6_MAPPER_SAMPLE)
        r6_full = scan(r6_full_root)
        failures += _case(
            "R6：映射器覆盖全部字段 → 零发现",
            r6_full.r6 == [],
            f"实际 {[finding.describe() for finding in r6_full.r6]}",
        )
        code = run(r6_full_root, clean_baseline, 0)
        failures += _case("R6：全覆盖场景 exit 0", code == 0, f"实际 exit {code}")

        # ---------------- 用例 16：映射器少写一个字段 → 必须报（可红） ----------------
        r6_partial_root = tmp / "r6-partial"
        _write_module(
            r6_partial_root / "widget_repo.py",
            _R6_MAPPER_SAMPLE.replace(_R6_MAPPER_FULL_BODY, _R6_MAPPER_PARTIAL_BODY),
        )
        r6_partial = scan(r6_partial_root)
        failures += _case(
            "R6：映射器少写 value → 报出 (WidgetRecord, value)",
            [(item.record, item.field) for item in r6_partial.r6] == [("WidgetRecord", "value")],
            f"实际 {[(item.record, item.field) for item in r6_partial.r6]}",
        )
        failures += _case(
            "R6：发现里必须带 mapper 名与行号（可定位）",
            bool(r6_partial.r6)
            and r6_partial.r6[0].mapper == "_to_widget"
            and r6_partial.r6[0].lineno > 0,
            f"实际 {[item.describe() for item in r6_partial.r6]}",
        )
        code = run(r6_partial_root, clean_baseline, 0)
        failures += _case("R6：WARN-only 默认不改变 exit（0）", code == 0, f"实际 exit {code}")
        code = run(r6_partial_root, clean_baseline, 0, strict_r6=True)
        failures += _case("R6：--strict-r6 计入 exit（1）", code == 1, f"实际 exit {code}")

        # ---------------- 用例 17：只做零值构造 → 全部字段都报 ----------------
        r6_zero_root = tmp / "r6-zero"
        _write_module(
            r6_zero_root / "widget_repo.py",
            _R6_MAPPER_SAMPLE.replace(_R6_MAPPER_FULL_BODY, _R6_MAPPER_ZERO_BODY),
        )
        r6_zero = scan(r6_zero_root)
        failures += _case(
            "R6：只做零值构造 → 全部字段都报（不是「无可判定」）",
            [(item.record, item.field) for item in r6_zero.r6]
            == [("WidgetRecord", "key"), ("WidgetRecord", "value")],
            f"实际 {[(item.record, item.field) for item in r6_zero.r6]}",
        )

        # ---------------- 用例 18：命中豁免 → 降级为 INFO（不是消失） ----------------
        R6_EXEMPTIONS[("widget", "WidgetRecord", "value")] = (
            "自检注入的豁免",
            "自检结束立即删除",
        )
        try:
            r6_exempt = scan(r6_partial_root)
        finally:
            R6_EXEMPTIONS.pop(("widget", "WidgetRecord", "value"), None)
        failures += _case(
            "R6：命中豁免 → 发现仍在但标记为豁免",
            len(r6_exempt.r6) == 1 and r6_exempt.r6[0].is_exempt,
            f"实际 {[(item.describe(), item.is_exempt) for item in r6_exempt.r6]}",
        )
        failures += _case(
            "R6：豁免失效后恢复为真 WARN（不是永久后门）",
            bool(r6_partial.r6) and not r6_partial.r6[0].is_exempt,
            f"实际 {[(item.describe(), item.is_exempt) for item in r6_partial.r6]}",
        )

        # ---------------- 用例 19：R7 全覆盖 → 零发现 ----------------
        r7_full_body = """
        async def save(self, record: WidgetRecord) -> None:
            sql = f"INSERT INTO {WIDGET_TABLE} (key, value, note) VALUES (:k, :v, :n)"
            del sql
            return None
        """
        r7_full_root = tmp / "r7-full"
        _write_module(r7_full_root / "widget_repo.py", _r7_repo_source(r7_full_body))
        r7_full = scan(r7_full_root, ddl=_R7_DDL)
        failures += _case(
            "R7：INSERT 覆盖全部表列 → 零发现",
            r7_full.r7 == [],
            f"实际 {[(item.record, item.field) for item in r7_full.r7]} / INFO {r7_full.r7_info}",
        )
        failures += _case(
            "R7：非表列字段（computed）必须进 INFO 而不是 WARN",
            any("computed" in info for info in r7_full.r7_info)
            and not any(item.field == "computed" for item in r7_full.r7),
            f"实际 INFO {r7_full.r7_info} / WARN {[item.field for item in r7_full.r7]}",
        )
        code = run(r7_full_root, clean_baseline, 0, ddl=_R7_DDL)
        failures += _case("R7：全覆盖场景 exit 0", code == 0, f"实际 exit {code}")

        # ---------------- 用例 20：漏写必填列 → 必须报（可红） ----------------
        r7_miss_body = """
        async def save(self, record: WidgetRecord) -> None:
            sql = f"INSERT INTO {WIDGET_TABLE} (key, value) VALUES (:k, :v)"
            del sql
            return None
        """
        r7_miss_root = tmp / "r7-miss"
        _write_module(r7_miss_root / "widget_repo.py", _r7_repo_source(r7_miss_body))
        r7_miss = scan(r7_miss_root, ddl=_R7_DDL)
        failures += _case(
            "R7：漏写无默认列 note → 报 (WidgetRecord, note)",
            [(item.record, item.field) for item in r7_miss.r7] == [("WidgetRecord", "note")],
            f"实际 {[(item.record, item.field) for item in r7_miss.r7]}",
        )
        failures += _case(
            "R7：发现里必须带表名（可定位）",
            bool(r7_miss.r7) and r7_miss.r7[0].table == "widget",
            f"实际 {[item.table for item in r7_miss.r7]}",
        )
        code = run(r7_miss_root, clean_baseline, 0, ddl=_R7_DDL)
        failures += _case("R7：WARN-only 默认不改变 exit（0）", code == 0, f"实际 exit {code}")
        code = run(r7_miss_root, clean_baseline, 0, strict_r7=True, ddl=_R7_DDL)
        failures += _case("R7：--strict-r7 计入 exit（1）", code == 1, f"实际 exit {code}")

        # ---------------- 用例 21：DDL 说该列有默认 → 不得再报 ----------------
        relaxed_ddl = {
            "widget": DdlTable(
                columns=frozenset({"key", "value", "note", "created_at"}),
                omittable=frozenset({"created_at", "note"}),
            )
        }
        r7_relaxed = scan(r7_miss_root, ddl=relaxed_ddl)
        failures += _case(
            "R7：该列在 DDL 里有 DEFAULT → 不报（白名单来自 DDL，不是硬编码）",
            r7_relaxed.r7 == [],
            f"实际 {[(item.record, item.field) for item in r7_relaxed.r7]}",
        )

        # ---------------- 用例 22：同表多条 INSERT 取并集（降级路径不算缺口） ----------------
        r7_union_body = """
        async def save(self, record: WidgetRecord) -> None:
            full = f"INSERT INTO {WIDGET_TABLE} (key, value, note) VALUES (:k, :v, :n)"
            degraded = f"INSERT INTO {WIDGET_TABLE} (key, value) VALUES (:k, :v)"
            del full, degraded
            return None
        """
        r7_union_root = tmp / "r7-union"
        _write_module(r7_union_root / "widget_repo.py", _r7_repo_source(r7_union_body))
        r7_union = scan(r7_union_root, ddl=_R7_DDL)
        failures += _case(
            "R7：同表两条 INSERT 取并集 → 降级路径不产生假红",
            r7_union.r7 == [],
            f"实际 {[(item.record, item.field) for item in r7_union.r7]}",
        )

        # ---------------- 用例 23：真 DDL 解析器（ALTER 后补列 + 跨行 CHECK） ----------------
        ddl_dir = tmp / "ddl"
        ddl_dir.mkdir(exist_ok=True)
        (ddl_dir / "schema.sql").write_text(
            textwrap.dedent(
                """
                CREATE TABLE IF NOT EXISTS widget (
                    key        TEXT        NOT NULL,
                    value      TEXT        NOT NULL DEFAULT '',
                    note       TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT ck_widget CHECK (length(key) > 0)
                );

                ALTER TABLE widget ADD COLUMN IF NOT EXISTS extra TEXT NOT NULL DEFAULT 'x';
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        parsed = load_ddl_tables(ddl_dir)
        widget = parsed.get("widget")
        failures += _case(
            "R7 真解析器：CREATE TABLE + ALTER ADD COLUMN 都要进列集合",
            widget is not None
            and {"key", "value", "note", "created_at", "extra"} <= widget.columns,
            f"实际列 {sorted(widget.columns) if widget else None}",
        )
        failures += _case(
            "R7 真解析器：可省略列 = 有 DEFAULT 或可 NULL（NOT NULL 无默认必须排除）",
            widget is not None
            and {"value", "note", "created_at", "extra"} <= widget.omittable
            and "key" not in widget.omittable,
            f"实际可省略 {sorted(widget.omittable) if widget else None}",
        )
        failures += _case(
            "R7 真解析器：跨行 CHECK 续行不得被当成新列",
            widget is not None
            and "CHECK" not in widget.columns
            and "CONSTRAINT" not in widget.columns,
            f"实际列 {sorted(widget.columns) if widget else None}",
        )

        # ---------------- 用例 24：_is_omittable 四种组合 ----------------
        failures += _case(
            "R7 _is_omittable：DEFAULT / 可 NULL / NOT NULL 无默认 三态判定正确",
            _is_omittable("x TEXT NOT NULL DEFAULT ''")
            and _is_omittable("x TEXT")
            and not _is_omittable("x TEXT NOT NULL")
            and _is_omittable("x BIGINT", " NOT NULL DEFAULT 0"),
            "三态判定与预期不符",
        )

        # ---------------- 用例 25：表名解析失败必须计数（不是静默丢弃） ----------------
        r7_unresolved_body = """
        async def save(self, record: WidgetRecord) -> None:
            table_name = WIDGET_TABLE
            sql = f"INSERT INTO {table_name} (key, value, note) VALUES (:k, :v, :n)"
            del sql
            return None
        """
        r7_unresolved_root = tmp / "r7-unresolved"
        _write_module(r7_unresolved_root / "widget_repo.py", _r7_repo_source(r7_unresolved_body))
        r7_unresolved = scan(r7_unresolved_root, ddl=_R7_DDL)
        failures += _case(
            "R7：表名不可解析的 INSERT 必须计入 unresolved（不是静默丢弃）",
            r7_unresolved.r7_stats.unresolved == 1,
            f"实际 unresolved={r7_unresolved.r7_stats.unresolved}",
        )

        # ---------------- 用例 26：DDL 缺表必须可见（覆盖塌陷不能伪装成通过） ----------------
        other_ddl = {
            "other": DdlTable(columns=frozenset({"key", "value", "note"}), omittable=frozenset())
        }
        r7_orphan = scan(r7_full_root, ddl=other_ddl)
        failures += _case(
            "R7：INSERT 目标表不在 DDL 时必须报出来（而不是静默变绿）",
            r7_orphan.r7_stats.orphan_tables == {"widget"}
            and any("不在 DDL" in info for info in r7_orphan.r7_info),
            f"实际 orphan={r7_orphan.r7_stats.orphan_tables} / INFO {r7_orphan.r7_info}",
        )

        # ---------------- 用例 27：覆盖率下限 fail-closed（覆盖塌陷的唯一硬信号） ----------------
        code = run(r7_full_root, clean_baseline, 0, ddl=_R7_DDL, r7_min_paired=999)
        failures += _case(
            "R7：配上数低于下限 → exit 2（fail-closed）", code == 2, f"实际 exit {code}"
        )
        code = run(r7_full_root, clean_baseline, 0, ddl=_R7_DDL, r7_min_inserts=999)
        failures += _case(
            "R7：INSERT 条数低于下限 → exit 2（扫描缩水即判死）", code == 2, f"实际 exit {code}"
        )

        # ---------------- 用例 28：不可解析的语句形态必须判死（不是"记个数就算") ----------------
        code = run(r7_unresolved_root, clean_baseline, 0, ddl=_R7_DDL, r7_coverage_guard=True)
        failures += _case(
            "R7：表名不可解析 → exit 2（看不见 ≠ 没问题）", code == 2, f"实际 exit {code}"
        )

        # ---------------- 用例 30：未评估（单元无 INSERT）也要逐项对白名单 ----------------
        no_insert_body = """
        async def save(self, record: WidgetRecord) -> None:
            return None
        """
        r7_noinsert_root = tmp / "r7-noinsert"
        _write_module(r7_noinsert_root / "widget_repo.py", _r7_repo_source(no_insert_body))
        r7_noinsert = scan(r7_noinsert_root, ddl=_R7_DDL)
        failures += _case(
            "R7：单元无 INSERT → dataclass 记为未评估（不是通过）",
            r7_noinsert.r7_stats.unevaluated_pairs == {("widget", "WidgetRecord")},
            f"实际 {r7_noinsert.r7_stats.unevaluated_pairs}",
        )
        code = run(r7_noinsert_root, clean_baseline, 0, ddl=_R7_DDL, r7_coverage_guard=True)
        failures += _case(
            "R7：未评估且不在白名单 → exit 2（覆盖率塌陷的另一个桶）",
            code == 2,
            f"实际 exit {code}",
        )

        # ---------------- 用例 31：未评估白名单"过期"也要判死（防止豁免腐坏） ----------------
        saved_allow = R7_UNEVALUATED_ALLOWLIST
        try:
            # 把 widget.WidgetRecord 塞进白名单：它本轮其实是 paired，条目即"过期"
            globals()["R7_UNEVALUATED_ALLOWLIST"] = saved_allow | {("widget", "WidgetRecord")}
            code = run(r7_full_root, clean_baseline, 0, ddl=_R7_DDL, r7_coverage_guard=True)
        finally:
            globals()["R7_UNEVALUATED_ALLOWLIST"] = saved_allow
        failures += _case(
            "R7：未评估白名单条目已过期 → exit 2（豁免不得腐坏）", code == 2, f"实际 exit {code}"
        )

        # ---------------- 用例 29：非白名单的 skipped 必须判死 ----------------
        ghost = textwrap.dedent(
            """
            @dataclass(frozen=True, slots=True)
            class GhostRecord:
                alpha: int = 0
                beta: int = 0
                gamma: int = 0


            """
        ).lstrip()
        r7_ghost_root = tmp / "r7-ghost"
        _write_module(
            r7_ghost_root / "widget_repo.py",
            _r7_repo_source(r7_full_body, extra_dataclass=ghost),
        )
        r7_ghost = scan(r7_ghost_root, ddl=_R7_DDL)
        failures += _case(
            "R7：新增非行式 dataclass 会被记为 unexpected_skips",
            r7_ghost.r7_stats.unexpected_skips == {("widget", "GhostRecord")},
            f"实际 {r7_ghost.r7_stats.unexpected_skips}",
        )
        code = run(r7_ghost_root, clean_baseline, 0, ddl=_R7_DDL, r7_coverage_guard=True)
        failures += _case(
            "R7：非白名单 skipped → exit 2（单表漂移不能藏在计数余量里）",
            code == 2,
            f"实际 exit {code}",
        )

    if failures:
        for failure in failures:
            print(failure)
        print(f"[FAIL] 自检未通过：{len(failures)} 条 → exit 2")
        return 2

    print(
        "[OK] 自检通过：三方一致 exit 0 / 协议缺方法（内存侧·PG 侧）R1 / PG 多公开方法 R2 / "
        "内存多 clear 白名单放行 / PG 出现 clear R2 / 参数名不同 R3 / 默认值不同 R3 / "
        "sync↔async R3 / 基线命中 WARN exit 0 / 基线外 exit 1 / 条目消失 stale exit 1 / "
        "基线缺失=空基线 exit 1 / 基线格式错误 5 态 exit 2 / 目录不存在-非目录-单元数不足 exit 2 / "
        "包形态召回（三文件 + 纯 shim 识别为 1 单元且判定生效）/ shim 内含 ClassDef exit 2 / "
        "R4 v2：空 `_to_xxx` 不洗白（仍红）/ R4 v2：同侧一处少字段（并集含该字段）仍红 / "
        "R6：全覆盖零发现 / R6：少写字段可红（含 mapper 与行号）/ "
        "R6：WARN-only 不改 exit、--strict-r6 计入 exit / "
        "R6：只做零值构造时全字段都报 / R6：豁免降级为 INFO 且可恢复为 WARN / "
        "R7：INSERT 覆盖全部表列零发现（非表列进 INFO）/ R7：漏写无默认列可红（含表名）/ "
        "R7：WARN-only 不改 exit、--strict-r7 计入 exit / "
        "R7：DDL 有 DEFAULT 即不报（白名单来自 DDL）/ "
        "R7：同表多条 INSERT 取并集（降级路径不假红）"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """入口。

    Args:
        argv: 参数列表（None 表示 sys.argv）。

    Returns:
        int: 退出码。
    """
    args = _parse_args(argv)
    if args.self_test:
        return _self_test()
    return run(
        repositories_root=args.repositories_root,
        baseline_path=args.baseline,
        min_units=args.min_units,
        show_units=args.show_units,
        strict_r6=args.strict_r6,
        strict_r7=args.strict_r7,
        r7_min_inserts=R7_MIN_INSERTS,
        r7_min_paired=R7_MIN_PAIRED,
        r7_coverage_guard=True,
    )


if __name__ == "__main__":
    sys.exit(main())
