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

里程碑口径（2026-10-23 定稿）：**B16 = 6 → B17 = 2 → B19 = 0**。
"""

from __future__ import annotations

from typing import Final

#: 当前仍应处于 `xfail` 的条数（正常模式门禁：`xfailed` 必须等于它）
EXPECTED_XFAIL: Final[int] = 6

#: 各批次剩余的 `xfail` 条数：本批结束时，本批归属项必须归 0
EXPECTED_XFAIL_BY_BATCH: Final[dict[str, int]] = {"B17": 4, "B19": 2}

__all__: list[str] = ["EXPECTED_XFAIL", "EXPECTED_XFAIL_BY_BATCH"]
