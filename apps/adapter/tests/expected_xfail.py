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
EXPECTED_XFAIL: Final[int] = 2

#: 各批次剩余的 `xfail` 条数：本批结束时，本批归属项必须归 0
EXPECTED_XFAIL_BY_BATCH: Final[dict[str, int]] = {"B19": 2}

#: 当前 `status=red`（已实现但红着）的条数——**缺陷不得沉淀为背景红**
EXPECTED_RED: Final[int] = 0

#: 「未闭口」总量 = xfail + red。**单调不增**，批次结束时必须与实测相等。
#:
#: 为什么单列：若只盯 `xfailed`，把 `xfail` 改成 `red`（断言写了但红）就能让
#: `xfailed` 从 3 降到 0 而**一条能力都没多**——这是"把红变黄"的变体：
#: 改状态标记而不改实现。把两个桶之和纳入同一计数，状态迁移可以，但总量不许涨。
EXPECTED_OPEN: Final[int] = 2

#: 未闭口总量的历史序列（append-only，只增条目不改旧值），用于校验"单调不增"
OPEN_HISTORY: Final[tuple[int, ...]] = (6, 6, 2)  # B16 结束 6、B17 结束 6、B18 结束 2

#: `red` 条数的历史序列（append-only）。**必须单调不增**——否则「把 xfail 改成 red」
#: 会让 red 从 3 涨到 6、同时 xfailed 归 0，总量看似没变而进度也没变（洗标记）。
#: B16 结束 0、B17 结束 3（B17 三条缺陷）、B18 结束 0（已随 B18 全部关闭）
RED_HISTORY: Final[tuple[int, ...]] = (0, 3, 0)

__all__: list[str] = [
    "EXPECTED_OPEN",
    "EXPECTED_RED",
    "EXPECTED_XFAIL",
    "EXPECTED_XFAIL_BY_BATCH",
    "OPEN_HISTORY",
    "RED_HISTORY",
]
