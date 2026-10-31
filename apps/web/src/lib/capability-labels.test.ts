/**
 * 能力文案映射回归网（B23 / G-16：能力 key 不得进入 DOM）。
 *
 * 覆盖三条口径：后端展示名优先、白名单兜底、未登记一律走「按需授权」兜底。
 */

import { describe, expect, it } from 'vitest';

import {
  CAPABILITY_FALLBACK_LABELS,
  ORG_CAPABILITY_KEYS,
  PERSONAL_CAPABILITY_KEYS,
  UNKNOWN_CAPABILITY_LABEL,
  capabilityLabel,
  isOrgCapability,
  isPersonalCapability,
} from './capability-labels';

describe('capabilityLabel', () => {
  it('后端给出展示名时优先使用（并去除首尾空白）', () => {
    expect(capabilityLabel('personal.tool.use', '  使用工具  ')).toBe('使用工具');
    expect(capabilityLabel('未登记能力', '服务端名称')).toBe('服务端名称');
  });

  it('展示名为空 / 仅空白 / 非字符串时回落到白名单文案', () => {
    expect(capabilityLabel('personal.knowledge.read')).toBe('读取你本人的知识库内容');
    expect(capabilityLabel('org.repo.write', '   ')).toBe('写入代码仓库');
    expect(capabilityLabel('org.repo.write', null)).toBe('写入代码仓库');
    // 非字符串展示名按缺失处理
    expect(capabilityLabel('org.directory.read', 123 as unknown as string)).toBe(
      '读取企业通讯录与组织架构',
    );
  });

  it('未登记能力返回兜底文案，绝不回显 key', () => {
    const label = capabilityLabel('unknown.capability.key');
    expect(label).toBe(UNKNOWN_CAPABILITY_LABEL);
    expect(label).not.toContain('unknown');
  });

  it('白名单恰好 6 项且不含未登记键', () => {
    expect(Object.keys(CAPABILITY_FALLBACK_LABELS)).toHaveLength(6);
    expect(Object.keys(CAPABILITY_FALLBACK_LABELS)).toEqual([
      ...PERSONAL_CAPABILITY_KEYS,
      ...ORG_CAPABILITY_KEYS,
    ]);
  });
});

describe('能力层级判定', () => {
  it('org.* 为企业级，personal.* 为个人级', () => {
    expect(isOrgCapability('org.repo.write')).toBe(true);
    expect(isOrgCapability('personal.tool.use')).toBe(false);
    expect(isPersonalCapability('personal.file.read')).toBe(true);
    expect(isPersonalCapability('org.directory.read')).toBe(false);
    expect(isPersonalCapability('unknown.key')).toBe(false);
  });
});
