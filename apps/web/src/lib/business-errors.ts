/**
 * 业务错误码 → 业务语言文案映射（架构文档 3.0.3 / 9.2）。
 *
 * 铁律（架构文档 9.2 + 总方案 5.5）：
 * - 前端**只展示 message**，严禁展示 code、堆栈、下游原始错误；
 * - 本映射是**兜底**：适配层已返回 message 时直接使用，
 *   仅当 message 缺失或为空时才回落到本表；
 * - 新增错误码必须先在架构文档 3.0.3 登记，再同步本表。
 */

import { BusinessError } from './api-client';

/**
 * 错误码 → 业务语言文案（与架构文档 3.0.3 逐条一致）。
 *
 * 覆盖口径：后端 `errors.py` 注册表的**全部对外码**（含 `12004` 预留码——
 * 它随时可能上线，前端要继续能翻译它）；另有历史说明性键保留。
 * Agent 域的两个历史遗留码已按「注销值不复用」原则移除，**不得**再填回本表。
 */
export const BUSINESS_ERROR_MESSAGES: Readonly<Record<number, string>> = Object.freeze({
  // 通用业务错误 20001-20008
  20001: '请求内容不完整，请检查后重试',
  20002: '登录状态已失效，请重新登录',
  20003: '当前账号没有该操作的权限',
  20004: '你访问的内容不存在或已被删除',
  20005: '操作冲突，请刷新后重试',
  20006: '当前使用人数较多，请稍后再试',
  20007: '重复的请求已被忽略',
  20008: '文件超出大小限制（上限 50MB）',

  // 模型服务域 10001-10005
  10001: 'AI 暂时没有给出回答，请重试',
  10002: '当前对话内容过长，请新建会话或精简内容',
  10003: '当前套餐不包含该能力，升级后即可使用',
  10004: '智能助手繁忙，请稍后再试',
  10005: '当前套餐可用次数不足',

  // RAG 服务域 11001-11007
  11001: '知识库不存在或已被删除',
  11002: '该文档格式无法处理，请另存为 PDF、Word 或 TXT 后重试',
  11003: '文档内容过多，请拆分后上传',
  11004: '文档还在处理中，请稍候查看',
  11005: '检索服务暂时不可用，请重试',
  11006: '文档正在处理，无法重复操作',
  // 11007：库级凭据失效。重试无意义，必须给用户可执行的出路（联系管理员）
  11007: '该知识库暂时无法访问，请联系管理员',

  // Agent 服务域 12001-12004（12002/12005 已注销、禁止复用）
  12001: '任务不存在或已过期',
  12003: '任务已取消',
  12004: '任务执行超时，请重试',

  // 工具服务域 13001-13006
  13001: '该工具尚未连接，请先在设置中完成连接',
  13002: '该工具尚未连接，请先在设置中完成授权',
  13003: '工具授权已过期，请重新连接',
  13004: '工具暂时没有响应，请稍后再试',
  13005: '该工具不支持此操作',
  13006: '第三方工具返回了异常结果',

  // 用户权限服务域 14001-14008
  14001: '用户不存在',
  14002: '团队不存在',
  14003: '角色不存在',
  14004: '权限点未定义',
  14005: '该部门不存在或已被删除',
  14006: '该部门下仍有成员或子部门，请先迁移后再删除',
  14007: '不能移除企业所有者，请先转移所有权',
  14008: '你没有权限修改该成员',

  // 计费与订阅域 15001-15009（阶段二新增）
  15001: '当前套餐不支持该功能，升级后即可使用',
  15002: '该套餐已下架，请选择其他套餐',
  15003: '成员席位已用满，请先增加席位',
  15004: '当前账户有未完成订单，请先完成支付',
  15005: '降级将在当前周期结束后生效',
  15006: '本月额度已用完，当前为常规模式，升级套餐可恢复完整能力',
  15007: '支付未完成，请重新支付',
  15008: '该订单已处理，请勿重复操作',
  15009: '订单金额与应付金额不一致，请核对后重试',

  // 插件域 16002-16012（阶段三）：文案即后端 message，前端仅兜底
  16002: '该插件已下架，去市场看看其他插件',
  16003: '该插件还没有添加，请先在插件市场添加',
  16004: '该插件已停用，请联系管理员启用',
  16005: '缺少所需能力，请重新授权后再试',
  16006: '该能力尚未对企业开放，请联系管理员开启',
  16007: '该插件需要更新版本的产品后才能使用，请联系管理员',
  16008: '只能恢复到上一个版本，如需更早版本请联系管理员',
  16009: '该插件需要先添加其他插件才能使用',
  16010: '插件状态已变化，请刷新后重试',
  16011: '使用过这个插件才能评价',
  16012: '该插件版本不存在',

  // 任务域 17001（WF1 拒绝重试时的用户可见文案）
  // 注意：任务域内部的汇总码不进前端兜底表（G3-11 门禁要求）
  17001: '该任务无法重试，请查看任务状态',

  // 系统错误 30001-30004
  30001: '服务暂时不可用，请稍后再试',
  30002: '服务响应超时，请重试',
  30003: '系统繁忙，请稍后再试',
  30004: '服务配置异常，请联系管理员',
});

/** 未知错误码的兜底文案 */
export const UNKNOWN_ERROR_MESSAGE = '操作未完成，请稍后再试';

/**
 * 同码多文案的「上下文键」。
 *
 * 后端在个别场景用 `ApiError(message=...)` 覆盖默认文案而**不改码值**
 * （架构文档 §7.1「同码多文案」表）。前端在已知上下文里可以主动选用更贴切的文案，
 * 但**绝不能**把码值展示给用户：文案始终是业务语言。
 */
export type ErrorContext =
  | 'department.level_exceeded'
  | 'department.move_into_subtree'
  | 'department.delete_non_empty'
  | 'document.delete_processing'
  | 'role.delete_referenced'
  | 'grant.guest_editor'
  | 'audit.range_exceeded'
  | 'member.batch_limit'
  | 'contact.batch_limit';

/**
 * 同码多文案映射：`code → 上下文 → 文案`。
 *
 * 与架构文档 §7.1 的 9 条覆盖逐条对应；未命中的上下文回落到默认文案。
 */
export const CONTEXT_ERROR_MESSAGES: Readonly<
  Record<number, Readonly<Partial<Record<ErrorContext, string>>>>
> = Object.freeze({
  // 部门层级冲突：建第 6 层 / 移动到自身子树 / 删除非空部门
  14006: Object.freeze({
    'department.level_exceeded': '组织层级最多 5 层，请调整后重试',
    'department.move_into_subtree': '不能把部门移动到自己的下级部门',
    'department.delete_non_empty': '该部门下仍有成员或子部门，请先迁移后再删除',
  }),
  // 文档仍在处理中时删除
  11006: Object.freeze({
    'document.delete_processing': '文档正在处理，请稍后再删除',
  }),
  // 角色被成员引用不可删除
  20005: Object.freeze({
    'role.delete_referenced': '该角色下还有成员，请先调整成员角色',
  }),
  // 参数类覆盖文案
  20001: Object.freeze({
    'grant.guest_editor': '访客只能查看，不能上传或管理文档',
    'audit.range_exceeded': '查询时间范围最长 90 天，请缩小范围',
    'member.batch_limit': '一次最多处理 100 位成员，请分批操作',
    'contact.batch_limit': '一次最多发送给 200 人，请分批发送',
  }),
});

/** 错误码分段（用于核对兜底表覆盖率，口径与适配层注册表一致） */
export const BUSINESS_ERROR_SEGMENTS: Readonly<Record<string, readonly [number, number]>> =
  Object.freeze({
    common: [20001, 20008],
    model: [10001, 10005],
    knowledge: [11001, 11007],
    task: [12001, 12004],
    tool: [13001, 13006],
    identity: [14001, 14008],
    billing: [15001, 15009],
    plugin: [16002, 16012],
    system: [30001, 30004],
  });

/**
 * 找出兜底表里缺失的码（用于门禁核对后端注册表是否有新增码未同步）。
 *
 * @param codes - 后端注册表里的全部业务错误码
 * @returns 未登记的码列表；空数组表示覆盖完整
 */
export function findMissingErrorCodes(codes: readonly number[]): number[] {
  return codes.filter((code) => BUSINESS_ERROR_MESSAGES[code] === undefined);
}

/**
 * 按错误码取业务语言文案。
 *
 * @param code - 业务错误码
 * @param context - 可选上下文；提供时优先返回同码多文案里的贴切写法
 * @returns 面向用户的业务语言提示
 */
export function messageOf(code: number, context?: ErrorContext): string {
  if (context) {
    const scoped = CONTEXT_ERROR_MESSAGES[code]?.[context];
    if (scoped) return scoped;
  }
  return BUSINESS_ERROR_MESSAGES[code] ?? UNKNOWN_ERROR_MESSAGE;
}

/**
 * 归一化任意异常为业务语言文案。
 *
 * 适配层已给出 message 时优先使用（保证与后端口径一致）；
 * message 缺失时按 code + 上下文回落；两者皆无则使用通用兜底文案。
 *
 * @param error - 捕获到的异常
 * @param context - 可选上下文（同码多文案场景）
 * @returns 面向用户的业务语言提示
 */
export function toBusinessMessage(error: unknown, context?: ErrorContext): string {
  if (error instanceof BusinessError) {
    return error.message || messageOf(error.code, context);
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return UNKNOWN_ERROR_MESSAGE;
}

/**
 * 判断错误是否需要重新登录。
 *
 * @param error - 捕获到的异常
 * @returns 需要跳转登录时返回 true
 */
export function shouldReauthenticate(error: unknown): boolean {
  return error instanceof BusinessError && error.code === 20002;
}

/**
 * 判断错误是否为「资源不存在」类（可引导用户返回列表）。
 *
 * @param error - 捕获到的异常
 * @returns 命中资源不存在类错误时返回 true
 */
export function isNotFound(error: unknown): boolean {
  return (
    error instanceof BusinessError &&
    [20004, 11001, 12001, 14001, 14002, 14005].includes(error.code)
  );
}

/**
 * 判断错误是否为「重复请求已忽略」（静默处理，不弹错误提示）。
 *
 * @param error - 捕获到的异常
 * @returns 命中幂等重复时返回 true
 */
export function isIdempotentDuplicate(error: unknown): boolean {
  return error instanceof BusinessError && error.code === 20007;
}
