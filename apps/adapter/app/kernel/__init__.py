"""L0 运行时内核（设计文档 §7.1 的 `app/kernel/**`）。

本包是**内核层**：Agent Loop、事件日志、上下文、审批与沙箱、模型网关、工具运行时
都在这里。依赖方向恒为 L4 → L3 → L2 → L1 → L0，内核**不得** import `app.api.*`
与 `app.business.*`（设计文档 §3 第 144 行）。内核可依赖 `app.core`（配置/错误/ID）
与 `app.db`（仓储实现；接口仍是内核定义的协议）。

T02 只落地事件日志（`events.py`）；loop / context / approval / sandbox /
model_gateway / tool_runtime 属 T04–T08，尚未创建——不要在导入期引用它们的符号。
"""

from __future__ import annotations

__all__: list[str] = []
