import { describe, expect, it } from 'vitest';

import { statusDescriptor } from '@/components/common/status-badge';
import { isPluginInstalled } from './plugin-status';

describe('插件安装态判定（Batch 3）', () => {
  it('已安装包含 enabled / disabled / installed，uninstalled 不算已安装', () => {
    expect(isPluginInstalled('enabled')).toBe(true);
    expect(isPluginInstalled('disabled')).toBe(true);
    expect(isPluginInstalled('installed')).toBe(true);
    expect(isPluginInstalled('uninstalled')).toBe(false);
    expect(isPluginInstalled('none')).toBe(false);
  });
});

describe('插件安装态徽标文案（Batch 3）', () => {
  it('五值都有业务文案，不再把 installed 误标成「已停用」', () => {
    expect(statusDescriptor('enabled').label).toBe('已添加');
    expect(statusDescriptor('disabled').label).toBe('已停用');
    expect(statusDescriptor('installed').label).toBe('已安装');
    expect(statusDescriptor('uninstalled').label).toBe('未添加');
    expect(statusDescriptor('none').label).toBe('待确认');
  });
});
