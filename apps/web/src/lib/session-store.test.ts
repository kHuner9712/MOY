import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PendingAuthorization } from './oidc';

import {
  clearPending,
  clearSession,
  isSessionExpired,
  LEGACY_SESSION_STORAGE_KEY,
  loadPending,
  loadSession,
  PENDING_STORAGE_KEY,
  savePending,
  saveSession,
  SESSION_STORAGE_KEY,
  type StoredSession,
} from './session-store';

/**
 * 浏览器侧会话存储的契约：
 * 1. **不抛异常**：存储被禁用 / 配额写满时降级为「没有会话」（false / null），
 *    一次存储故障不能把整页变成白屏；
 * 2. **读写都校验形状**：localStorage 里任何脚本都能写脏数据，
 *    少字段 / 类型不对 / 解析失败一律当作「没有会话」，绝不把半截对象当登录态；
 * 3. **历史遗留键一次性迁移**：读到就搬到新键并删旧键；
 *    迁移是尽力而为——写不进新键时旧键必须保留，不能把用户凭证弄丢。
 *
 * jsdom 自带 localStorage / sessionStorage，直接用真实实现，
 * 只在模拟故障（配额溢出、存储被禁用）时局部 mock。
 */

/** 构造一份合法会话。 */
function makeSession(overrides: Partial<StoredSession> = {}): StoredSession {
  return {
    access_token: 'token-abc',
    refresh_token: 'refresh-abc',
    expires_at: 1_700_000_000_000,
    ...overrides,
  };
}

/** 构造一份合法的「待完成登录」。 */
function makePending(overrides: Partial<PendingAuthorization> = {}): PendingAuthorization {
  return {
    state: 'state-123',
    codeVerifier: 'pkce-verifier',
    redirectUri: 'https://app.example.com/callback',
    createdAt: 1_700_000_000_000,
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('saveSession / loadSession', () => {
  it('写入后能原样读回', () => {
    const session = makeSession();
    expect(saveSession(session)).toBe(true);
    expect(loadSession()).toEqual(session);
    // 键名带版本号且只有这一处知道
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBe(JSON.stringify(session));
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('开发令牌 expires_at 为 null 也合法', () => {
    const session = makeSession({ refresh_token: null, expires_at: null });
    expect(saveSession(session)).toBe(true);
    expect(loadSession()).toEqual(session);
  });

  it('形状不对的会话拒绝写入且不落盘', () => {
    const invalids: StoredSession[] = [
      makeSession({ access_token: '' }),
      makeSession({ access_token: '   ' }),
      { access_token: 42, refresh_token: null, expires_at: null } as unknown as StoredSession,
      makeSession({ expires_at: undefined as unknown as number }),
      makeSession({ expires_at: Number.NaN }),
      makeSession({ expires_at: Number.POSITIVE_INFINITY }),
      makeSession({ expires_at: '1700000000000' as unknown as number }),
      makeSession({ refresh_token: 42 as unknown as string }),
      null as unknown as StoredSession,
      'token' as unknown as StoredSession,
    ];
    for (const invalid of invalids) {
      expect(saveSession(invalid)).toBe(false);
    }
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it('没有会话时读到 null', () => {
    expect(loadSession()).toBeNull();
  });

  it('脏数据（JSON 损坏 / 字段缺失）一律当作没有会话', () => {
    window.localStorage.setItem(SESSION_STORAGE_KEY, '{oops');
    expect(loadSession()).toBeNull();

    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ access_token: '' }));
    expect(loadSession()).toBeNull();

    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ expires_at: 1 }));
    expect(loadSession()).toBeNull();
  });

  it('配额写满时 saveSession 返回 false 而不是抛异常', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(saveSession(makeSession())).toBe(false);
    expect(() => saveSession(makeSession())).not.toThrow();
  });

  it('存储读取抛错时 loadSession 返回 null', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(loadSession()).toBeNull();
  });

  it('存储属性访问被禁用（隐私模式）时全部降级，不抛异常', () => {
    // jsdom 的 localStorage 是 window 自有属性，替换成抛错 getter 后必须按原描述符还原，
    // 直接 delete 会把后续用例的存储整个删没
    const descriptor = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('storage disabled');
      },
    });
    try {
      expect(saveSession(makeSession())).toBe(false);
      expect(loadSession()).toBeNull();
      expect(() => clearSession()).not.toThrow();
    } finally {
      if (descriptor) {
        Object.defineProperty(window, 'localStorage', descriptor);
      }
    }
  });
});

describe('历史遗留键迁移', () => {
  it('读到旧键就迁移到新键并删除旧键', () => {
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, '  legacy-token  ');

    const migrated = loadSession();
    expect(migrated).toEqual({
      access_token: 'legacy-token',
      refresh_token: null,
      expires_at: null,
    });
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBe(JSON.stringify(migrated));
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBeNull();

    // 迁移是持久的：下次直接命中新键
    expect(loadSession()).toEqual(migrated);
  });

  it('旧键只有空白时不迁移', () => {
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, '   ');
    expect(loadSession()).toBeNull();
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBe('   ');
  });

  it('新键优先于旧键，旧键不动', () => {
    const session = makeSession();
    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, 'legacy-token');

    expect(loadSession()).toEqual(session);
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBe('legacy-token');
  });

  it('迁移写不进新键时仍返回会话，且旧键保留（凭证不丢）', () => {
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, 'legacy-token');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });

    expect(loadSession()).toEqual({
      access_token: 'legacy-token',
      refresh_token: null,
      expires_at: null,
    });
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBe('legacy-token');
  });

  it('迁移后删不掉旧键也无害（新键已存在）', () => {
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, 'legacy-token');
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });

    expect(loadSession()?.access_token).toBe('legacy-token');
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toContain('legacy-token');
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBe('legacy-token');
  });
});

describe('clearSession', () => {
  it('同时清掉新键与旧键（避免迁移逻辑把旧键救回来）', () => {
    saveSession(makeSession());
    window.localStorage.setItem(LEGACY_SESSION_STORAGE_KEY, 'legacy-token');

    clearSession();
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(LEGACY_SESSION_STORAGE_KEY)).toBeNull();
    expect(loadSession()).toBeNull();
  });

  it('removeItem 抛错时不向外抛', () => {
    saveSession(makeSession());
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(() => clearSession()).not.toThrow();
  });
});

describe('isSessionExpired', () => {
  const now = 1_700_000_000_000;

  it('expires_at 为 null（无过期信息）不判过期', () => {
    expect(isSessionExpired(makeSession({ expires_at: null }), now)).toBe(false);
  });

  it('未过期 false，已过期 / 恰好到期 true', () => {
    expect(isSessionExpired(makeSession({ expires_at: now + 1 }), now)).toBe(false);
    expect(isSessionExpired(makeSession({ expires_at: now }), now)).toBe(true);
    expect(isSessionExpired(makeSession({ expires_at: now - 1 }), now)).toBe(true);
  });

  it('时间戳非法（NaN / Infinity / 非数字）判过期', () => {
    expect(isSessionExpired(makeSession({ expires_at: Number.NaN }), now)).toBe(true);
    expect(isSessionExpired(makeSession({ expires_at: Number.POSITIVE_INFINITY }), now)).toBe(true);
    expect(isSessionExpired(makeSession({ expires_at: 'x' as unknown as number }), now)).toBe(true);
    expect(isSessionExpired(makeSession({ expires_at: undefined as unknown as number }), now)).toBe(
      true,
    );
  });
});

describe('savePending / loadPending / clearPending', () => {
  it('写入后能原样读回（存在 sessionStorage，不进 localStorage）', () => {
    const pending = makePending({ returnTo: '/plugins?tab=installed' });
    expect(savePending(pending)).toBe(true);
    expect(loadPending()).toEqual(pending);
    expect(window.sessionStorage.getItem(PENDING_STORAGE_KEY)).toBe(JSON.stringify(pending));
    expect(window.localStorage.getItem(PENDING_STORAGE_KEY)).toBeNull();
  });

  it('升级前写下的旧对象没有 returnTo 也合法', () => {
    const pending = makePending();
    expect(savePending(pending)).toBe(true);
    expect(loadPending()).toEqual(pending);
  });

  it('形状不对的待完成登录拒绝写入', () => {
    const invalids: PendingAuthorization[] = [
      makePending({ state: '' }),
      makePending({ state: undefined as unknown as string }),
      makePending({ codeVerifier: '' }),
      makePending({ codeVerifier: undefined as unknown as string }),
      makePending({ redirectUri: '' }),
      makePending({ redirectUri: undefined as unknown as string }),
      makePending({ createdAt: Number.NaN }),
      makePending({ createdAt: 'now' as unknown as number }),
      makePending({ createdAt: undefined as unknown as number }),
      null as unknown as PendingAuthorization,
    ];
    for (const invalid of invalids) {
      expect(savePending(invalid)).toBe(false);
    }
    expect(window.sessionStorage.getItem(PENDING_STORAGE_KEY)).toBeNull();
    expect(loadPending()).toBeNull();
  });

  it('配额写满时 savePending 返回 false', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(savePending(makePending())).toBe(false);
  });

  it('脏数据与存储故障都当作「没有待完成登录」', () => {
    expect(loadPending()).toBeNull();

    window.sessionStorage.setItem(PENDING_STORAGE_KEY, '{oops');
    expect(loadPending()).toBeNull();

    window.sessionStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify({ state: '' }));
    expect(loadPending()).toBeNull();

    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(loadPending()).toBeNull();
  });

  it('clearPending 删除待完成登录，且存储故障不向外抛', () => {
    savePending(makePending());
    clearPending();
    expect(window.sessionStorage.getItem(PENDING_STORAGE_KEY)).toBeNull();

    savePending(makePending());
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(() => clearPending()).not.toThrow();
  });

  it('sessionStorage 被禁用时全部降级', () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, 'sessionStorage');
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() {
        throw new Error('storage disabled');
      },
    });
    try {
      expect(savePending(makePending())).toBe(false);
      expect(loadPending()).toBeNull();
      expect(() => clearPending()).not.toThrow();
    } finally {
      if (descriptor) {
        Object.defineProperty(window, 'sessionStorage', descriptor);
      }
    }
  });
});
