/**
 * 能力 key → 业务名称的**兜底**映射（B23）。
 *
 * 口径（team-lead 裁定）：
 * - 权威来源是 PL4/PL2 返回的 `display_name`，后端给了就用后端的；
 * - 本表只在该字段缺失/为空时兜底，避免前端硬编码与后端漂移；
 * - `capability_key` 本身**不得进入 DOM**（G-16 断言），一律经本模块转成中文；
 * - 只收录声明白名单内的 6 项（架构 §2.2），新增能力不在此表时按「按需授权」兜底。
 */

import type { PluginCapabilityKey } from '@/lib/types';

/** 能力 key 的业务名称兜底表（键与架构 §2.2 逐字一致） */
export const CAPABILITY_FALLBACK_LABELS: Readonly<Record<string, string>> = Object.freeze({
  'personal.knowledge.read': '读取你本人的知识库内容',
  'personal.tool.use': '使用你已连接的办公工具',
  'personal.file.read': '读取你本人上传的文件',
  'org.directory.read': '读取企业通讯录与组织架构',
  'org.repo.write': '写入代码仓库',
  'org.external.publish': '向外部平台发布内容',
});

/** 个人能力三项（恒开启，不可被企业关闭） */
export const PERSONAL_CAPABILITY_KEYS: readonly PluginCapabilityKey[] = Object.freeze([
  'personal.knowledge.read',
  'personal.tool.use',
  'personal.file.read',
]);

/** 企业能力三项（受企业总开关控制） */
export const ORG_CAPABILITY_KEYS: readonly PluginCapabilityKey[] = Object.freeze([
  'org.directory.read',
  'org.repo.write',
  'org.external.publish',
]);

/** 未登记能力时的兜底文案（不暴露 key） */
export const UNKNOWN_CAPABILITY_LABEL = '按插件说明授权';

/** 企业级能力的 key 前缀（受企业总开关控制） */
const ORG_CAPABILITY_PREFIX = 'org.';

/**
 * 取能力的展示名称。
 *
 * @param capabilityKey - 能力键（如 `personal.tool.use`）
 * @param displayName - 后端返回的展示名；有值时优先使用
 * @returns 业务语言名称；两者皆无时返回兜底文案（绝不回显 key）
 */
export function capabilityLabel(capabilityKey: string, displayName?: string | null): string {
  const fromServer = typeof displayName === 'string' ? displayName.trim() : '';
  if (fromServer) return fromServer;
  return CAPABILITY_FALLBACK_LABELS[capabilityKey] ?? UNKNOWN_CAPABILITY_LABEL;
}

/**
 * 是否企业级能力（`org.*`）。
 *
 * @param capabilityKey - 能力键
 * @returns 企业级返回 true
 */
export function isOrgCapability(capabilityKey: string): boolean {
  return capabilityKey.startsWith(ORG_CAPABILITY_PREFIX);
}

/**
 * 是否个人级能力（`personal.*`，恒开启、不可被企业关闭）。
 *
 * @param capabilityKey - 能力键
 * @returns 个人级返回 true
 */
export function isPersonalCapability(capabilityKey: string): boolean {
  return capabilityKey.startsWith('personal.');
}
