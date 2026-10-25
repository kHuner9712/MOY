import { describe, expect, it } from 'vitest';

import { buildLoginHref, DEFAULT_LANDING_PATH, resolveNextPath } from './redirect';

/**
 * 登录回跳地址的开放重定向防护。
 *
 * 这些用例的价值不在“覆盖函数”，而在锁住一条安全红线：
 * `next` 是任何人都能构造的查询参数，只要有一条路径能把它变成站外地址，
 * 登录就会变成钓鱼跳板。
 */
describe('resolveNextPath', () => {
  it('保留合法站内路径与查询串', () => {
    expect(resolveNextPath('/chat')).toBe('/chat');
    expect(resolveNextPath('/plugins?tab=installed&category=dev')).toBe(
      '/plugins?tab=installed&category=dev',
    );
    expect(resolveNextPath(encodeURIComponent('/knowledge/kb_01?x=1'))).toBe(
      '/knowledge/kb_01?x=1',
    );
  });

  it('拒绝协议相对地址', () => {
    expect(resolveNextPath('//evil.example')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('///evil.example')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('%2F%2Fevil.example')).toBe(DEFAULT_LANDING_PATH);
  });

  it('拒绝反斜杠与绝对地址变体', () => {
    expect(resolveNextPath('/\\evil.example')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('https://evil.example')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('javascript:alert(1)')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('chat')).toBe(DEFAULT_LANDING_PATH);
  });

  it('编码损坏与空值回落默认页', () => {
    expect(resolveNextPath('%E0%A4%A')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath('')).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath(null)).toBe(DEFAULT_LANDING_PATH);
    expect(resolveNextPath(undefined)).toBe(DEFAULT_LANDING_PATH);
  });
});

describe('buildLoginHref', () => {
  it('把当前路径与查询串编码进 next', () => {
    expect(buildLoginHref('/plugins', '?tab=installed')).toBe(
      '/login?next=%2Fplugins%3Ftab%3Dinstalled',
    );
  });

  it('没有查询串时只带路径', () => {
    expect(buildLoginHref('/chat')).toBe('/login?next=%2Fchat');
  });

  it('缺失前导斜杠时补齐，避免生成非站内地址', () => {
    expect(buildLoginHref('chat')).toBe('/login?next=%2Fchat');
  });
});
