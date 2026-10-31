/** 样式工具与品牌常量回归网。 */

import { describe, expect, it } from 'vitest';

import { BRAND } from './brand';
import { cn } from './utils';

describe('cn', () => {
  it('合并类名并让后者覆盖同类属性', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });

  it('支持条件类名与嵌套数组', () => {
    expect(cn('base', false && 'hidden', ['text-sm', undefined], null)).toBe('base text-sm');
  });

  it('无输入时返回空串', () => {
    expect(cn()).toBe('');
  });
});

describe('BRAND', () => {
  it('品牌唯一来源字段完整且非空', () => {
    for (const value of Object.values(BRAND)) {
      expect(typeof value).toBe('string');
      expect(value.length).toBeGreaterThan(0);
    }
    expect(BRAND.name).toBe('麦子');
    expect(BRAND.latin).toBe('Moy');
    expect(BRAND.tagline).toBe('Mate Of You');
  });
});
