"""产物二进制内容列的**静态门禁**（缺陷 P3-B22-03 的防回归钉子）。

为什么要有这一层（纯静态、不连库）：

- 真 PG 用例（`tests/integration/test_artifact_content_pg.py`）只在 `--run-pg` 时跑。
  CI 默认档、以及"某个只看默认档的人"，需要一层**不需要数据库**的判据，
  否则这条缺陷的修复完全依赖"今天恰好有人跑带库的用例"。
- 这类缺陷的复发形态不是"代码写错"，而是**有人把取舍改回去**：
  "对象存储更优雅，PG 里别存字节了吧"——一句话就能把 `content` 列删掉，
  而所有内存后端用例依旧全绿。所以判据必须钉在**DDL 与 SQL 文本**上。

判据（缺一条都不成立）：

1. `deploy/migrations/0005_*.sql` 存在、幂等（`ADD COLUMN IF NOT EXISTS content BYTEA`）、
   自足（`to_regclass` 守卫）、权限安全（处理 `insufficient_privilege`）；
2. `deploy/init-schema-phase3.sql` 的 `plugin_artifact` 建表语句里**真的有** `content` 列
   （否则新建库会被 0005 判"缺列"，而已初始化库上应用角色又不是表属主）；
3. 仓储的 `INSERT` 列清单里有 `content`、值清单里有 `:content`；
4. `_to_artifact` 能把 `bytea` 读回来的三种形态（`memoryview` / `bytes` / `None`）规范成 `bytes`。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Final

from app.db.repositories.artifact_repo import _as_bytes, _to_artifact

#: 顶层逗号切分器（单一实现，避免"判据里的切分逻辑"再写一份而不一致）
from tests.integration.test_sql_insert_consistency import split_top

#: `apps/adapter/tests/unit/test_x.py` → parents[4] 才是仓库根
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]

#: 迁移文件
MIGRATION_0005: Final[Path] = (
    REPO_ROOT / "deploy" / "migrations" / "0005_phase3_artifact_content.sql"
)

#: 阶段三基线脚本
INIT_PHASE3: Final[Path] = REPO_ROOT / "deploy" / "init-schema-phase3.sql"

#: 仓储实现
ARTIFACT_REPO: Final[Path] = (
    REPO_ROOT / "apps" / "adapter" / "app" / "db" / "repositories" / "artifact_repo.py"
)

#: 迁移门禁脚本（静态判据的**唯一真源**，不在这里另写一份规则）
VALIDATOR: Final[Path] = REPO_ROOT / "deploy" / "validate_migrations.py"


def _load_validator() -> Any:
    """按文件路径加载 `deploy/validate_migrations.py`。

    不复写一份"守卫规则"：判据同源才能保证"门禁放过的"和"用例放过的"是同一批。

    Returns:
        Any: 模块对象。
    """
    spec = importlib.util.spec_from_file_location("_deploy_validate_migrations", VALIDATOR)
    assert spec is not None and spec.loader is not None, f"无法加载 {VALIDATOR}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# 1. 迁移脚本本身
# ===========================================================================
def test_migration_0005_exists_and_is_named_per_convention() -> None:
    """`0005` 存在且命名符合 `NNNN_snake_case.sql`（迁移靠文件名排序）。"""
    assert MIGRATION_0005.is_file(), f"缺少迁移 {MIGRATION_0005.name}"
    assert re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", MIGRATION_0005.name)


def test_migration_0005_is_idempotent_add_column_bytea() -> None:
    """`ADD COLUMN IF NOT EXISTS content BYTEA`（幂等 + 类型正确）。

    没有 `IF NOT EXISTS` 时第二遍 `--apply` 会报 `column already exists`，
    而"空库连跑两遍"是 G-11 的固定判据。
    """
    text = MIGRATION_0005.read_text(encoding="utf-8")
    assert re.search(
        r"ALTER\s+TABLE\s+plugin_artifact\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+content\s+BYTEA",
        text,
        re.IGNORECASE,
    ), "0005 必须用 `ADD COLUMN IF NOT EXISTS content BYTEA`（幂等，类型 bytea）"


def test_migration_0005_passes_the_deploy_gate_rules() -> None:
    """`0005` 必须过 `deploy/validate_migrations.py` 的两条静态判据。

    美元引号按栈配对（`DO $guard$ … END $guard$`）+ 含 `ALTER TABLE` 的脚本必须处理
    `insufficient_privilege`。这两条是踩过事故的：标签不配对整个空库路径语法崩；
    无条件 `ALTER` 在已初始化库上必 `must be owner of table`。
    """
    validator = _load_validator()
    violations = list(validator.check_dollar_quotes(MIGRATION_0005))
    violations.extend(validator.check_guard_presence(MIGRATION_0005))
    assert not violations, [item.render() for item in violations]


def test_migration_0005_is_self_sufficient_on_empty_database() -> None:
    """`to_regclass` 守卫在（空库 / 未启用阶段三的库上必须**提示后返回**，不是 RAISE）。"""
    text = MIGRATION_0005.read_text(encoding="utf-8")
    assert "to_regclass('public.plugin_artifact')" in text
    assert "RETURN;" in text, "守卫命中后必须 RETURN，否则空库路径会卡在这条迁移上"


def test_migration_0005_uses_lf_line_endings() -> None:
    """`deploy/.gitattributes` 已设 `* text=auto eol=lf`，落盘不许是 CRLF。

    换行符漂移会让 checksum 摘要变化 → `--apply` 报 `migration checksum drift`（30004），
    现象像"迁移内容被改了"，实际只是编辑器把行尾改了。
    """
    raw = MIGRATION_0005.read_bytes()
    assert b"\r\n" not in raw, "0005 被写成了 CRLF，请改回 LF"


# ===========================================================================
# 2. init 基线：新库必须一次建全
# ===========================================================================
def test_init_schema_phase3_declares_content_column() -> None:
    """`init-schema-phase3.sql` 的 `plugin_artifact` 建表语句里必须有 `content`。

    这一条不是"文档要求"：缺了它，新库会走 0005 的"补列"分支，
    而已初始化库上应用角色不是表属主 → 迁移直接报权限错（两条路径同时红，最难查）。
    """
    validator = _load_validator()
    text = validator.strip_sql_noise(INIT_PHASE3.read_text(encoding="utf-8"))
    # 刻意复用门禁那份（私有的）列解析器：判据同源，避免"用例的解析规则"与
    # "门禁的解析规则"各写一份后悄悄漂移。
    assert validator._column_declared(text, "plugin_artifact", "content"), (  # noqa: SLF001
        "init-schema-phase3.sql 的 plugin_artifact 建表语句缺 content 列"
    )


def test_init_schema_phase3_content_column_is_bytea() -> None:
    """列类型必须是 `BYTEA`（存 docx / zip 的原始字节，不是文本）。"""
    text = INIT_PHASE3.read_text(encoding="utf-8")
    assert re.search(r"^\s*content\s+BYTEA\b", text, re.MULTILINE | re.IGNORECASE), (
        "plugin_artifact.content 必须是 BYTEA"
    )


# ===========================================================================
# 3. 仓储：真的写、真的读
# ===========================================================================
def test_repository_insert_carries_content_column() -> None:
    """`INSERT INTO plugin_artifact` 的列清单与值清单都含 `content`。

    只改 DDL 不改 SQL，是"加了列但没人写"的经典空转：库里列一直 NULL，
    下载依旧回落文本预览——功能和没加列一模一样。
    """
    text = ARTIFACT_REPO.read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO\s+\{?ARTIFACT_TABLE\}?\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*RETURNING",
        text,
        re.DOTALL,
    )
    assert match is not None, "未找到 plugin_artifact 的 INSERT 语句"
    # 切分必须走**括号深度扫描**：值清单里有 `NULLIF(:external_url, '')`，
    # 朴素 `split(",")` 会把它切成两段 → 值数虚高 → 误判（那个坑已在
    # `test_sql_insert_consistency.test_splitter_counterexample` 里立了反例）。
    columns = split_top(match.group(1))
    values = split_top(match.group(2))
    assert "content" in columns, f"INSERT 列清单缺 content：{columns}"
    assert ":content" in values, f"INSERT 值清单缺 :content：{values}"
    assert len(columns) == len(values), f"列数 {len(columns)} != 值数 {len(values)}"


def test_repository_save_version_updates_content_column() -> None:
    """`save_version` 的 `UPDATE` 必须写 `content`（否则改版后下载还是旧文件）。"""
    text = ARTIFACT_REPO.read_text(encoding="utf-8")
    pattern = r"UPDATE\s+\{?ARTIFACT_TABLE\}?\s+SET[^;]*?content\s*=\s*:content"
    assert re.search(pattern, text, re.S), "save_version 的 UPDATE 没有写 content 列"


# ===========================================================================
# 4. `_to_artifact`：读回来的 bytea 三种形态
# ===========================================================================
def _row(content: Any = None, *, omit: bool = False) -> dict[str, Any]:
    """构造一行最小可用的 `plugin_artifact` 映射。

    Args:
        content: `content` 列的值。
        omit: 是否**完全不出现** `content` 键（模拟未跑 0005 的老库）。

    Returns:
        dict[str, Any]: 行映射。
    """
    row: dict[str, Any] = {
        "tenant_id": "tnt_static",
        "artifact_id": "art_static",
        "task_id": "task_static",
        "step_id": "step_static",
        "output_key": "document",
        "type": "file",
        "name": "行政公文",
        "inline_text": "预览",
        "size_bytes": 4,
        "content_sha256": "0" * 64,
    }
    if not omit:
        row["content"] = content
    return row


def test_to_artifact_reads_bytes_from_memoryview() -> None:
    """asyncpg 可能把 `bytea` 读成 `memoryview`，必须规范成 `bytes`。"""
    record = _to_artifact(_row(memoryview(b"PK\x03\x04")))
    assert isinstance(record.content, bytes)
    assert record.content == b"PK\x03\x04"


def test_to_artifact_reads_bytes_and_null() -> None:
    """`bytes` 原样返回；`NULL`（文本类产物）回落 `b""`。"""
    assert _to_artifact(_row(b"PK\x03\x04")).content == b"PK\x03\x04"
    assert _to_artifact(_row(None)).content == b""
    assert _to_artifact(_row(bytearray(b"PK"))).content == b"PK"


def test_to_artifact_tolerates_missing_content_column() -> None:
    """老库没跑 0005 时行里没有该列 → `b""`，读取链路不崩（降级行为与修复前一致）。"""
    record = _to_artifact(_row(omit=True))
    assert record.content == b""


def test_as_bytes_never_shares_buffer() -> None:
    """`bytearray` 转 `bytes` 是拷贝（不与行对象共享可变缓冲）。"""
    source = bytearray(b"PK")
    converted = _as_bytes(source)
    source[0] = 0x41
    assert converted == b"PK"
