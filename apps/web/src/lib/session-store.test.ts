/**
 * 历史令牌键清理测试（P0-5 路线 A）。
 *
 * 这个模块只剩「删」：它存在的唯一目的是把旧版本留在客户端的令牌与一次性随机串
 * 清掉，避免升级后旧令牌继续躺在本地存储里被同源脚本读到。
 *
 * 因此断言也只有两条主线：**该删的都删掉**、**删不掉也不能把页面弄崩**。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LEGACY_SESSION_STORAGE_KEY,
  PENDING_STORAGE_KEY,
  purgeLegacySessionStorage,
  SESSION_STORAGE_KEY,
} from './session-store';

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('purgeLegacySessionStorage', () => {
  it('删除三条历史键：早期令牌键、旧会话键、旧待完成登录键', () => {
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, 'legacy-token');
    window.localStorage.setItem(SESSION_STORAGE_KEY, '{"access_token":"tok"}');
    window.sessionStorage.setItem(PENDING_STORAGE_KEY, '{"state":"s","codeVerifier":"v"}');

    purgeLegacySessionStorage();

    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(PENDING_STORAGE_KEY)).toBeNull();
  });

  it('不碰其它键（只删令牌相关，不做「顺手清空」）', () => {
    window.localStorage.setItem('moy.theme', 'dark');
    purgeLegacySessionStorage();
    expect(window.localStorage.getItem('moy.theme')).toBe('dark');
  });

  it('幂等：没有历史键时调用不报错，连调两次也一样', () => {
    expect(() => purgeLegacySessionStorage()).not.toThrow();
    expect(() => purgeLegacySessionStorage()).not.toThrow();
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('本地存储被禁用（隐私模式）时不向外抛', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    expect(() => purgeLegacySessionStorage()).not.toThrow();
  });

  it('会话级存储被禁用时同样不向外抛', () => {
    vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    expect(() => purgeLegacySessionStorage()).not.toThrow();
  });

  it('服务端渲染（没有 window）直接返回，不访问存储', () => {
    vi.stubGlobal('window', undefined);
    try {
      expect(() => purgeLegacySessionStorage()).not.toThrow();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
