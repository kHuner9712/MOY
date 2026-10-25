import { describe, expect, it } from 'vitest';

import type { PluginInputSpec } from '@/lib/types';

import { mergeMissingDefaults, validateField } from './use-declaration-form';

/** 工作台真实声明的一个最小子集（与 plg_dev_workbench 同形） */
const SPECS: PluginInputSpec[] = [
  { key: 'title', label: '需求标题', type: 'text', required: true, max_length: 80 },
  { key: 'requirement', label: '需求描述', type: 'textarea', required: true, max_length: 4000 },
  { key: 'repo', label: '目标仓库', type: 'select', required: true },
  {
    key: 'branch_policy',
    label: '分支策略',
    type: 'select',
    required: true,
    options: ['新建分支', '指定分支'],
  },
  { key: 'notify', label: '通知邮箱', type: 'email', required: false },
];

describe('mergeMissingDefaults', () => {
  it('声明异步到达后补齐缺失键，下拉取首项', () => {
    const merged = mergeMissingDefaults({}, SPECS);

    expect(merged.branch_policy).toBe('新建分支');
    expect(merged.title).toBe('');
    expect(merged.repo).toBe('');
  });

  it('用户已填的值一律不被覆盖，无变化时返回原对象', () => {
    const filled = {
      title: '把登录页品牌化',
      requirement: '换成麦子品牌',
      repo: 'moy/web',
      branch_policy: '指定分支',
      notify: 'dev@example.com',
    };

    const merged = mergeMissingDefaults(filled, SPECS);

    expect(merged).toBe(filled);
    expect(merged).toEqual(filled);
  });

  it('空串下拉在有候选值时补首项，用户选过的值保持不动', () => {
    const merged = mergeMissingDefaults({ branch_policy: '' }, SPECS);
    expect(merged.branch_policy).toBe('新建分支');

    const picked = mergeMissingDefaults({ branch_policy: '指定分支' }, SPECS);
    expect(picked.branch_policy).toBe('指定分支');
  });

  it('幂等：同一份声明重复合并不会产生新对象', () => {
    const first = mergeMissingDefaults({}, SPECS);
    const second = mergeMissingDefaults(first, SPECS);
    expect(second).toBe(first);
  });
});

describe('validateField', () => {
  it('必填为空时给出业务文案', () => {
    expect(validateField(SPECS[0]!, '')).toBe('请填写需求标题');
  });

  it('空候选下拉不做候选校验（降级为文本框后仍可提交）', () => {
    expect(validateField(SPECS[2]!, 'https://git.example.com/moy/web')).toBe('');
  });

  it('有候选值时拒绝不在候选内的值', () => {
    expect(validateField(SPECS[3]!, '不存在的分支')).toBe('请选择有效的分支策略');
  });

  it('邮箱格式错误时给出可读提示', () => {
    expect(validateField(SPECS[4]!, 'not-an-email')).toBe('请填写有效的邮箱地址');
  });
});
