"""数据权利域的共享常量（阶段四 T4-05.3，PRD P4-05）。

**为什么要下沉到 `core`**（审计 P1-5）：`EXPORT_SCOPES` 原先定义在
`business/tenant_data_service.py`，导致 `schemas/tenant_data.py`（请求模型）
必须**反向依赖业务服务实现**——DTO 被服务实现细节绑住，「序列化契约」与
「业务实现」再也无法独立演进。

导出类别是**契约词汇表**（请求模型 / 服务实现 / 前端展示共用），不是服务
实现细节，因此归 `core`。`business/tenant_data_service.py` 从这里 import 后
原样 re-export，既有调用方 import 路径零改动。

`EXPORT_FILE_NAMES`（类别 → zip 内文件名）**故意留在 business**：它是导出包的
**实现细节**（打包命名），不是契约词汇；`tenant_data_service` 里有断言测试
保证 `set(EXPORT_FILE_NAMES) == set(EXPORT_SCOPES)`，两者不会漂移。

本模块只放**常量**，不 import 任何其他 `app` 模块。
"""

from __future__ import annotations

from typing import Final

#: 导出范围（固定顺序；请求缺省 = 全部 5 类，响应按此顺序输出）
EXPORT_SCOPES: Final[tuple[str, ...]] = ("audit", "usage", "chat", "memory", "artifact")

__all__: list[str] = ["EXPORT_SCOPES"]
