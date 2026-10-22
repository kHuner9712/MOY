"""阶段三骨架的**自检用例**（只在 `--self-test` 模式下按路径显式运行）。

**为什么单独成文件、且文件名不带 `test_` 前缀**：
正常全量跑必须 `0 skipped`（沿用阶段二验收口径）。若把自检放进 `test_*.py` 并用
`skipif` 门控，正常模式会多出 2 skipped，**与口径冲突，且红的原因与设计无关**。
文件名不带 `test_` → pytest 默认**不收集**；需要时按路径显式指定即可收集：

```bash
OA_P3_SKELETON_SELFTEST=1 pytest tests/integration/selftest_plugin_contract.py -q
#   期望：1 passed（d2 证明不变量抓得住错） + 1 failed（d3 故意红，证明框架会报红）→ exit 1
```

两条用例分工不同、缺一不可：
- `test_d2_negative_sample_is_caught_by_invariant`：负样本（`16003`/`16004` 互换）必须被
  不变量抓住 → **证明断言逻辑真的有效**；
- `test_d3_skeleton_reports_failure_on_purpose`：故意必红 → **证明 pytest 会把失败报出来**。

只有 d3 属于"硬造失败"，证明不了断言有效；只有 d2 则无法确认框架会报红。
"""

from __future__ import annotations

import os

import pytest

from tests.integration.test_plugin_contract_skeleton import _assert_codes_not_inverted

#: 自检开关（显式运行本文件时置 `1`；不置时本文件也不会被默认收集）
SELFTEST_ENV = "OA_P3_SKELETON_SELFTEST"

pytestmark = pytest.mark.integration

if os.environ.get(SELFTEST_ENV) != "1":
    pytest.skip(
        f"骨架自检需显式开启：{SELFTEST_ENV}=1 pytest "
        "tests/integration/selftest_plugin_contract.py",
        allow_module_level=True,
    )


def test_d2_negative_sample_is_caught_by_invariant() -> None:
    """骨架自检 ①：**负样本必须被不变量抓住**（纯内存，不碰任何真实常量）。

    做法：把 `16003`/`16004` 互换后喂给 `_assert_codes_not_inverted`，必须抛
    `AssertionError`。这条证明「断言真的会红」，且**不修改 `errors.py` /
    `ERROR_REGISTRY`**——避免「临时改回」被中断后污染进程、引发连锁失败。
    """
    with pytest.raises(AssertionError) as caught:
        _assert_codes_not_inverted(
            not_installed=16004,
            disabled=16003,
            message_not_installed="该插件已停用，请联系管理员启用",
            message_disabled="该插件还没有添加，请先在插件市场添加",
        )
    assert "[TC-P3-PL02-01 ①]" in str(caught.value), (
        f"[骨架自检 ①] 负样本被抓住了，但报错没有带用例编号：{caught.value}"
    )


def test_d3_skeleton_reports_failure_on_purpose() -> None:
    """骨架自检 ②：**故意必红**，用来确认 pytest 会把失败报出来（exit 非 0）。

    用途：`OA_P3_SKELETON_SELFTEST=1 pytest -q <本文件>` 必须 **exit 非 0**。
    若这种情况下仍然全绿，说明断言没生效（被 `xfail` 吞掉 / 文件没被收集 / 被 CI
    当成「已知失败」长期挂着），此时骨架的所有「绿」都不作数。
    平时不设该变量，本用例以 `skipif` 排除——与「未实现项用 `xfail`」是两件不同的事。
    """
    raise AssertionError("[骨架自检 ②] 这条断言故意失败：它失败才说明骨架的断言真的会报红")
