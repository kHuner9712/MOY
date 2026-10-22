"""阶段三 xfail 预期数（**唯一真源**，供骨架与批次门禁共用）。

为什么单独成模块：
1. `tests/integration/test_plugin_contract_skeleton.py`（骨架）与批次门禁脚本需要**同一个**
   预期值，写在两个地方必然漂移；
2. 数值必须是**显式常量**而非由用例列表长度推导——否则「删一条用例 + 删一条登记」
   会让两个数一起降、汇总断言仍绿，漂移被掩盖；
3. 每批应下降多少，由 `EXPECTED_XFAIL_BY_BATCH` 表达，验收报告直接引它，
   不必靠人回忆「这批该转几条」。

维护规则（**人工，禁止自动推导**）：
- 某条延后用例在批次 N 转绿后：把骨架里该条的 `landed_in` 填成 `"BN"`，
  同时把 `EXPECTED_XFAIL` 减 1、`EXPECTED_XFAIL_BY_BATCH["BN"]` 减 1；
- 阶段终态：`EXPECTED_XFAIL == 0`。

里程碑口径（2026-10-23 定稿；PL14 挪 B18 后校正）：
**B16 = 6 → B17 结束 3 → B18 结束 2 → B19 结束 0**。
"""

from __future__ import annotations

from typing import Final

#: 当前仍应处于 `xfail` 的条数（正常模式门禁：`xfailed` 必须等于它）
EXPECTED_XFAIL: Final[int] = 0

#: 各批次剩余的 `xfail` 条数：本批结束时，本批归属项必须归 0
EXPECTED_XFAIL_BY_BATCH: Final[dict[str, int]] = {}

#: 当前 `status=red`（已实现但红着）的条数——**缺陷不得沉淀为背景红**
EXPECTED_RED: Final[int] = 0

#: 「未闭口**用例**」总量 = xfail + red。
#: **注意与缺陷台账的 `status="open"` 不是一个轴**：
#: 本常量数的是断言层（骨架用例），台账数的是缺陷层。
#: 命名带 `CASES` 就是为了不再被当成同一个数。**单调不增**，批次结束时必须与实测相等。
#:
#: 为什么单列：若只盯 `xfailed`，把 `xfail` 改成 `red`（断言写了但红）就能让
#: `xfailed` 从 3 降到 0 而**一条能力都没多**——这是"把红变黄"的变体：
#: 改状态标记而不改实现。把两个桶之和纳入同一计数，状态迁移可以，但总量不许涨。
EXPECTED_OPEN_CASES: Final[int] = 0

OPEN_CASES_HISTORY: Final[tuple[int, ...]] = (6, 6, 2, 0)  # B16=6、B17=6、B18=2、B19=0

#: `red` 条数的历史序列（append-only）。**必须单调不增**——否则「把 xfail 改成 red」
#: 会让 red 从 3 涨到 6、同时 xfailed 归 0，总量看似没变而进度也没变（洗标记）。
#: B16 结束 0、B17 结束 3（B17 三条缺陷）、B18 结束 0（已随 B18 全部关闭）
RED_HISTORY: Final[tuple[int, ...]] = (0, 3, 0)

#: 缺陷台账（append-only）。`red` 条目只能用这里 `status="open"` 的编号——
#: 防「编造编号」与「3 条红共用一个号」两种伪造（骨架汇总 ⑭⑮）。
#:
#: 语义边界（易混，写清楚）：
#: - `red`：**已被骨架断言钉住、且当前是红的**缺陷；
#: - `open`：**尚未关闭**的缺陷（可能由骨架钉，也可能由别的用例钉，如 g11）。
#: 因此 ⑮ 用**子集**（red ⊆ open），不是双向相等——否则会把「g11 钉住的迁移缺陷」
#: 硬塞进骨架，扩大骨架职责。
DEFECT_LEDGER: Final[tuple[dict[str, str], ...]] = (
    # B17 的两条 P1（已随 B18 关闭）
    {"id": "P3-B17-01", "level": "P1", "status": "fixed"},
    {"id": "P3-B17-02", "level": "P1", "status": "fixed"},
    # B18 缺陷（等级按方案 §7.1「后果面」定：迁移不可重复 = P0，门禁假红 = P1）
    {"id": "P3-B18-01", "level": "P0", "status": "fixed"},  # 0003 只对账 → b5dbd69 幂等建表
    {"id": "P3-B18-02", "level": "P0", "status": "fixed"},  # checksum drift → 同源 b5dbd69
    {"id": "P3-B18-03", "level": "P1", "status": "fixed"},  # G-13/17002 → c340e70 visibility 豁免
    # 新增号（勿与 B18-02 混用）：改名遗留的 schema_migration 孤儿行。
    # 影响面（按代码事实，勿夸大）：`build_plan()` 只遍历磁盘脚本，孤儿行不进
    # pending/drifted/checked → **不影响 `--apply`**；30004 的真因是 checksum 漂移
    # （P3-B18-02，环境态）。本条只是版本表残留旧名，属数据卫生/可观测性 → P2。
    {
        "id": "P3-B18-04",
        "level": "P2",
        "status": "fixed",  # dd28d13：orphans/aliased 可见化 + --prune-orphans + 0002 别名映射
        "pinned_by": "test_real_postgres_migration_is_idempotent",
    },
    # B19：任务推进从未执行（任务永远 running 且不报错），由骨架外的单测抓到 → 已修
    {"id": "P3-B19-01", "level": "P0", "status": "fixed"},  # ae69a4a（test_task_flow 抓到）
    # B19：0004 迁移守卫块标签不匹配（DO $guard$ vs END $$）→ 已修
    {"id": "P3-B19-02", "level": "P0", "status": "fixed"},  # b75f0ab（空库 apply 直接语法失败）
    # 同一文件的第二个缺陷：0004 无条件 ALTER 在已初始化库必失败（权限模型，非语法）→ 三分支
    {"id": "P3-B19-03", "level": "P1", "status": "fixed"},  # d75aa3a（init 脚本补列）
    # B19 漏网 P0：create_task() 的 PG INSERT 列数/值数不匹配 + 引用不存在的 queue_position 列。
    # **HEAD 仍在**（25 列/24 值）；工作区已改（23/23）但**未提交**。
    # 闭合条件必须是「已提交 + PG create_task 用例走通」，不能只看工作区。
    {
        "id": "P3-B19-04",
        "level": "P0",
        "status": "fixed",  # 9a85de6 修；改坏即红已验证（删一列→报不匹配→20 红）
        "pinned_by": "SQL↔DDL 静态门禁 + PG create_task 用例",
        "due_batch": "B21",
        "due_action": "①修复已提交②PG create_task 走通③改坏即红（工作区不算）",
    },
    # B20 两条挂账（到期 B21，到期即红）
    # 升级 P1→P0：缺口落在核心路径（task 47.81% / artifact 60.67% / plugin 67.80%），
    # 且未覆盖是**整段方法**（586-612 / 645-656 / 685-708 …）而非异常分支
    # → 按 §7.1 后果面「生产实现路径未被验」定 P0。**P1 可带出批次，P0 不能。**
    {
        "id": "P3-B20-01",
        "level": "P0",
        # 依 lead 权威数据关闭：分支 2404/2840=84.65%（需 2176，余量 +228）；
        # QA 侧独立复算同一冻结点为 2170/2840=76.41%，差 234 分支待溯因（疑 skip 差异），已在报告标注
        "status": "fixed",
        "pinned_by": "G-05 覆盖率（business+db）",
        "due_batch": "B21",
        "due_action": "补三 PG repo 语句与分支至终裁四档（总88/biz+db86/分支76.6/核心85+60）",
    },
    {
        "id": "P3-B20-02",
        "level": "P1",
        "status": "fixed",  # 6f81495：guardrails 44 passed（含 面议 两处样本）
        "pinned_by": "TC-P3-HR23-02 / 追溯表 §3.4 ⑨ 组",
        "due_batch": "B21",
        "due_action": "补 R3 样本 5 条（3 面议类必判失败 + 2 合法值通过）并实测",
    },
)


#: 当前批次是**运行时上下文**，不是跨批事实——所以用函数读环境变量，**不写常量**：
#: 写成常量会和 `EXPECTED_XFAIL` 一样漂移，而「默认成最新批」更糟：
#: 最新批的 `due_batch` 最宽松，一切都会绿 → 该红的不红，P2 永久挂账。
#: 因此**缺失即报错**（KeyError → 红），默认值必须指向"报错"而不是"放行"。
def current_batch() -> str:
    """读取当前批次（`OA_BATCH`，如 `B19`）。

    Returns:
        str: 批次号。

    Raises:
        KeyError: 未设置 `OA_BATCH` —— **故意不设默认值**，缺失即红。
    """
    import os

    try:
        return os.environ["OA_BATCH"]
    except KeyError as exc:  # pragma: no cover - 由断言路径覆盖
        raise RuntimeError(
            "未设置 OA_BATCH：请设置当前批次，例如 `OA_BATCH=B19`（用于缺陷到期检查）"
        ) from exc


__all__: list[str] = [
    "DEFECT_LEDGER",
    "EXPECTED_OPEN_CASES",
    "EXPECTED_RED",
    "EXPECTED_XFAIL",
    "EXPECTED_XFAIL_BY_BATCH",
    "OPEN_CASES_HISTORY",
    "RED_HISTORY",
    "current_batch",
]
