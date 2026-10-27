"""审计动作常量（架构文档 §3.11 / §4.4 U6）。

**为什么要下沉到 `core`**（审计 P1-4）：这些常量原先定义在
`business/audit_service.py`，于是 `db/repositories/audit_repo.py` 为了拿到
`ACTION_DISPLAY_NAMES` / `AUDIT_TABLE` 必须**反向依赖业务层**——仓储层跟着业务层
的动作清单一起漂移。动作编码是**跨层共享的词汇表**（业务写审计、仓储查审计、
前端展示分组都用它），不是业务服务的实现细节，因此归 `core`。

**单一真源**：`business/audit_service.py` 从这里 import 后原样 re-export，
调用方（49 处）与既有测试的 import 路径零改动。

本模块只放**常量**，不 import 任何其他 `app` 模块（保持 `core` 是最底层）。
"""

from __future__ import annotations

from typing import Final

#: 审计表名
AUDIT_TABLE: Final[str] = "audit_log"

#: 操作：创建知识库
ACTION_KB_CREATE: Final[str] = "knowledge_base.create"

#: 操作：更新知识库
ACTION_KB_UPDATE: Final[str] = "knowledge_base.update"

#: 操作：删除知识库
ACTION_KB_DELETE: Final[str] = "knowledge_base.delete"

#: 操作：新增成员授权（B05，PRD P2-KB-03 验收 6）
ACTION_GRANT_CREATE: Final[str] = "knowledge_base.grant_create"

#: 操作：撤销成员授权（B05）
ACTION_GRANT_REVOKE: Final[str] = "knowledge_base.grant_revoke"

#: 操作：额度预警（≥80%，P4-11 ④ 管理员通知）
ACTION_QUOTA_WARN_80: Final[str] = "billing.quota_warn_80"

#: 操作：额度用尽（≥100%，P4-11 ④ 管理员通知）
ACTION_QUOTA_EXHAUSTED_100: Final[str] = "billing.quota_exhausted_100"

#: 操作：上传文档
ACTION_DOC_UPLOAD: Final[str] = "document.upload"

#: 操作：删除文档
ACTION_DOC_DELETE: Final[str] = "document.delete"

#: 操作：新建部门（B08）
ACTION_DEPT_CREATE: Final[str] = "department.create"

#: 操作：修改/移动部门（B08）
ACTION_DEPT_UPDATE: Final[str] = "department.update"

#: 操作：删除部门（B08）
ACTION_DEPT_DELETE: Final[str] = "department.delete"

#: 操作：变更成员角色/部门/状态（B08）
ACTION_MEMBER_UPDATE: Final[str] = "member.update"

#: 操作：移出成员（B08）
ACTION_MEMBER_REMOVE: Final[str] = "member.remove"

#: 操作：批量变更成员（B08）
ACTION_MEMBER_BATCH: Final[str] = "member.batch_update"

#: 操作：删除角色（B08）
ACTION_ROLE_DELETE: Final[str] = "role.delete"

#: 操作：计量对账偏差（B07，G-23）
ACTION_RECONCILE_MISMATCH: Final[str] = "billing.reconcile_mismatch"

#: 操作：开通/变更套餐与席位（B09，BL3/BL4）
ACTION_SUBSCRIPTION_UPDATE: Final[str] = "billing.subscription_update"

#: 操作：创建订单（B09，BL6）
ACTION_ORDER_CREATED: Final[str] = "billing.order_created"

#: 操作：订单支付成功（B09，BL10 回调 / OPS5 核销）
ACTION_ORDER_PAID: Final[str] = "billing.order_paid"

#: 操作：启用/停用企业（B10，OPS3）
ACTION_TENANT_STATUS: Final[str] = "ops.tenant_status"

#: 操作：订单核销（B10，OPS5）
ACTION_ORDER_CONFIRMED: Final[str] = "ops.order_confirm"

#: 操作：修改系统配置（B10，OPS8）
ACTION_SETTINGS_UPDATE: Final[str] = "ops.settings_update"

#: 操作：添加插件（PL5，阶段三 B17）
ACTION_PLUGIN_INSTALL: Final[str] = "plugin.install"

#: 操作：变更企业能力开关（PL13，阶段三 B17）
ACTION_PLUGIN_CAPABILITY_POLICY: Final[str] = "plugin.capability_policy"

#: 操作：启用或停用插件（PL6，阶段三 B18）
ACTION_PLUGIN_STATE_CHANGE: Final[str] = "plugin.state_change"

#: 操作：移除插件（PL7，阶段三 B18）
ACTION_PLUGIN_UNINSTALL: Final[str] = "plugin.uninstall"

#: 操作：更新或恢复插件版本（PL8 / PL9，阶段三 B18）
ACTION_PLUGIN_VERSION_CHANGE: Final[str] = "plugin.version_change"

#: 操作：变更插件能力授权（阶段三 B18）
ACTION_PLUGIN_CAPABILITY_GRANT: Final[str] = "plugin.capability_grant"

#: 任务域动作（阶段三 B19，架构文档 §8.2）
ACTION_TASK_START: Final[str] = "task.start"
ACTION_TASK_FINISH: Final[str] = "task.finish"
ACTION_TASK_RETRY: Final[str] = "task.retry"

# 记忆域（阶段四 T4-03.5，PRD §4.8）：只记**动作与条数**，绝不记正文
ACTION_MEMORY_DELETE: Final[str] = "memory.delete"
ACTION_MEMORY_DELETE_CATEGORY: Final[str] = "memory.delete_category"
ACTION_MEMORY_DELETE_ALL: Final[str] = "memory.delete_all"
ACTION_MEMORY_SETTINGS: Final[str] = "memory.settings_update"

# 安全域（阶段四 T4-04.5）：权限被拒也要留痕（拒绝本身已生效，审计失败不影响）
ACTION_PERMISSION_DENIED: Final[str] = "security.permission_denied"

#: 数据权利域（阶段四 T4-05.3，PRD P4-05 ③④）
#:
#: 导出/删除是**企业管理员代表企业行使数据权利**，动作必须留痕：客户退租时
#: "谁在什么时候导出过、删过"要和删除证明一起成为可核查的事实。
#: `change_summary` 只放条数与范围，**不放任何正文**。
ACTION_TENANT_DATA_EXPORT: Final[str] = "tenant_data.export"
ACTION_TENANT_DATA_DELETE: Final[str] = "tenant_data.delete"

#: 操作：支付回调被拒绝（签名/金额/订单状态校验未通过，B09 安全加固）
#:
#: 拒绝事件**必须**进审计：`payment_callback_log` 只记「已接受并处理」的回调，
#: 若把未验签的载荷写进去，攻击者只要带上真实 `transaction_id` 就能占掉幂等键，
#: 让真实支付永远无法开通（P0-07）。因此拒绝事件只有 `audit_log` 一份归属，
#: 且未验签的渠道流水号**不进入**幂等键空间。
ACTION_PAYMENT_CALLBACK_REJECTED: Final[str] = "billing.payment_callback_rejected"

#: 操作 → 用户可见名称（写入 `action_display_name`，供操作记录页直接展示）
ACTION_DISPLAY_NAMES: Final[dict[str, str]] = {
    ACTION_KB_CREATE: "创建知识库",
    ACTION_KB_UPDATE: "修改知识库设置",
    ACTION_KB_DELETE: "删除知识库",
    ACTION_GRANT_CREATE: "添加成员授权",
    ACTION_GRANT_REVOKE: "撤销成员授权",
    ACTION_QUOTA_WARN_80: "额度用到八成",
    ACTION_QUOTA_EXHAUSTED_100: "额度已用尽",
    ACTION_DOC_UPLOAD: "上传文档",
    ACTION_DOC_DELETE: "删除文档",
    ACTION_DEPT_CREATE: "新建部门",
    ACTION_DEPT_UPDATE: "修改部门",
    ACTION_DEPT_DELETE: "删除部门",
    ACTION_MEMBER_UPDATE: "变更成员角色与部门",
    ACTION_MEMBER_REMOVE: "移出成员",
    ACTION_MEMBER_BATCH: "批量变更成员",
    ACTION_ROLE_DELETE: "删除角色",
    ACTION_RECONCILE_MISMATCH: "计量对账偏差",
    ACTION_SUBSCRIPTION_UPDATE: "变更套餐与席位",
    ACTION_ORDER_CREATED: "创建订单",
    ACTION_ORDER_PAID: "订单支付完成",
    ACTION_TENANT_STATUS: "启用或停用企业",
    ACTION_ORDER_CONFIRMED: "订单核销",
    ACTION_SETTINGS_UPDATE: "修改系统配置",
    ACTION_PAYMENT_CALLBACK_REJECTED: "支付回调被拒绝",
    ACTION_PLUGIN_INSTALL: "添加插件",
    ACTION_PLUGIN_CAPABILITY_POLICY: "变更企业能力开关",
    ACTION_PLUGIN_STATE_CHANGE: "启用或停用插件",
    ACTION_PLUGIN_UNINSTALL: "移除插件",
    ACTION_PLUGIN_VERSION_CHANGE: "更新或恢复插件版本",
    ACTION_PLUGIN_CAPABILITY_GRANT: "变更插件能力授权",
    ACTION_TASK_START: "发起自动化任务",
    ACTION_TASK_FINISH: "自动化任务结束",
    ACTION_TASK_RETRY: "重试自动化任务",
    ACTION_MEMORY_DELETE: "删除一条记忆",
    ACTION_MEMORY_DELETE_CATEGORY: "按类别删除记忆",
    ACTION_MEMORY_DELETE_ALL: "清空记忆",
    ACTION_MEMORY_SETTINGS: "修改记忆设置",
    ACTION_PERMISSION_DENIED: "权限校验未通过",
    ACTION_TENANT_DATA_EXPORT: "导出企业数据",
    ACTION_TENANT_DATA_DELETE: "删除企业数据",
}

__all__: list[str] = [
    "ACTION_DEPT_CREATE",
    "ACTION_DEPT_DELETE",
    "ACTION_DEPT_UPDATE",
    "ACTION_DISPLAY_NAMES",
    "ACTION_DOC_DELETE",
    "ACTION_DOC_UPLOAD",
    "ACTION_GRANT_CREATE",
    "ACTION_GRANT_REVOKE",
    "ACTION_KB_CREATE",
    "ACTION_KB_DELETE",
    "ACTION_KB_UPDATE",
    "ACTION_MEMBER_BATCH",
    "ACTION_MEMBER_REMOVE",
    "ACTION_MEMBER_UPDATE",
    "ACTION_MEMORY_DELETE",
    "ACTION_MEMORY_DELETE_ALL",
    "ACTION_MEMORY_DELETE_CATEGORY",
    "ACTION_MEMORY_SETTINGS",
    "ACTION_ORDER_CONFIRMED",
    "ACTION_ORDER_CREATED",
    "ACTION_ORDER_PAID",
    "ACTION_PAYMENT_CALLBACK_REJECTED",
    "ACTION_PERMISSION_DENIED",
    "ACTION_PLUGIN_CAPABILITY_GRANT",
    "ACTION_PLUGIN_CAPABILITY_POLICY",
    "ACTION_PLUGIN_INSTALL",
    "ACTION_PLUGIN_STATE_CHANGE",
    "ACTION_PLUGIN_UNINSTALL",
    "ACTION_PLUGIN_VERSION_CHANGE",
    "ACTION_RECONCILE_MISMATCH",
    "ACTION_ROLE_DELETE",
    "ACTION_SETTINGS_UPDATE",
    "ACTION_SUBSCRIPTION_UPDATE",
    "ACTION_TASK_FINISH",
    "ACTION_TASK_RETRY",
    "ACTION_TASK_START",
    "ACTION_TENANT_DATA_DELETE",
    "ACTION_TENANT_DATA_EXPORT",
    "ACTION_TENANT_STATUS",
    "AUDIT_TABLE",
]
