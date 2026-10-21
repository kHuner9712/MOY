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

/** 错误码 → 业务语言文案（与架构文档 3.0.3 逐条一致） */
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
  10003: '该内容暂不支持处理，请调整后重试',
  10004: '智能助手繁忙，请稍后再试',
  10005: '当前套餐可用次数不足',

  // RAG 服务域 11001-11006
  11001: '知识库不存在或已被删除',
  11002: '暂不支持该格式的文档',
  11003: '文档内容过多，请拆分后上传',
  11004: '文档还在处理中，请稍候查看',
  11005: '检索服务暂时不可用，请重试',
  11006: '文档正在处理，无法重复操作',

  // Agent 服务域 12001-12005
  12001: '任务不存在或已过期',
  12002: '任务执行未能完成，可查看详情后重试',
  12003: '任务已取消',
  12004: '任务执行超时，请重试',
  12005: '该步骤需要的工具尚未连接',

  // 工具服务域 13001-13006
  13001: '暂不支持该类工具',
  13002: '该工具尚未连接，请先在设置中完成授权',
  13003: '工具授权已过期，请重新连接',
  13004: '工具暂时没有响应，请稍后再试',
  13005: '该工具不支持此操作',
  13006: '第三方工具返回了异常结果',

  // 用户权限服务域 14001-14004
  14001: '用户不存在',
  14002: '团队不存在',
  14003: '角色不存在',
  14004: '权限点未定义',

  // 系统错误 30001-30004
  30001: '服务暂时不可用，请稍后再试',
  30002: '服务响应超时，请重试',
  30003: '系统繁忙，请稍后再试',
  30004: '服务配置异常，请联系管理员',
});

/** 未知错误码的兜底文案 */
export const UNKNOWN_ERROR_MESSAGE = '操作未完成，请稍后再试';

/**
 * 按错误码取业务语言文案。
 *
 * @param code - 业务错误码
 * @returns 面向用户的业务语言提示
 */
export function messageOf(code: number): string {
  return BUSINESS_ERROR_MESSAGES[code] ?? UNKNOWN_ERROR_MESSAGE;
}

/**
 * 归一化任意异常为业务语言文案。
 *
 * 适配层已给出 message 时优先使用（保证与后端口径一致）；
 * message 缺失时按 code 回落；两者皆无则使用通用兜底文案。
 *
 * @param error - 捕获到的异常
 * @returns 面向用户的业务语言提示
 */
export function toBusinessMessage(error: unknown): string {
  if (error instanceof BusinessError) {
    return error.message || messageOf(error.code);
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
  return error instanceof BusinessError && [20004, 11001, 12001, 14001, 14002].includes(error.code);
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
