/**
 * 浏览器侧会话存储 —— 本地会话与「待完成登录」的唯一读写口。
 *
 * 为什么要单独一层：
 * 1. **不抛异常**：SSR 阶段没有 window、隐私模式禁用存储、配额写满时 `setItem`
 *    都会直接抛错。这些情况都必须降级成「没有会话」（返回 null / false），
 *    否则一个存储异常会把整个页面变成白屏——用户看到的是「登录不了」而不是
 *    「存储不可用」，排障成本极高；
 * 2. **只有这一处知道键名**：键名散落各处时，改一处漏一处会让登录态时有时无；
 * 3. **读写都要校验形状**：localStorage 里的内容任何脚本都能改，读到脏数据
 *    （少字段、类型不对、过期）必须当作「没有会话」，不能把半截对象当登录态用。
 *
 * 存放位置的选择：
 * - 会话放 `localStorage`：刷新与重开标签页都要还在；
 * - 「待完成登录」（state / verifier）放 `sessionStorage`：它只在**当前标签页**的
 *   这一次跳转里有效，天然随标签页关闭而消失，也不会被同源的其它标签页误用。
 *
 * ⚠️ 已知权衡（V1.0 明确接受，后续版本再收敛）：`localStorage` 对同源脚本完全可读，
 * 因此**任何同源 XSS 都能直接取走访问令牌**（HttpOnly Cookie 才免疫 XSS，但会引入
 * CSRF 与跨域复杂度，且本轮适配层只认 `Authorization: Bearer`）。取舍理由：本产品
 * 的自渲染面只有自家的 app 路由，不加载第三方脚本；真正的兜底是收紧 CSP 与依赖审计。
 * 反过来，`refresh_token` 目前**只存不用**（本轮不做自动续期），风险面同源：一旦引入
 * 续期，必须先评估「续期令牌是否该改放内存/短时效」。
 *
 * 会话字段用 snake_case（与身份服务传输口径一致），并额外允许 `expires_at: null`：
 * 开发联调用的令牌没有过期信息，此时**不得**判为过期，否则一进来就被踢回登录页。
 *
 * 红线：本文件不打印任何内容（令牌与随机串一律不落日志）。
 */

import type { OidcSession, PendingAuthorization } from './oidc';

/** 会话存储键（带版本号：结构变更时换键，旧数据自动失效而不是被误读） */
export const SESSION_STORAGE_KEY = 'moy.session.v1';

/** 待完成登录的存储键（同上） */
export const PENDING_STORAGE_KEY = 'moy.oidc.pending.v1';

/**
 * 历史遗留的会话键。
 *
 * 早期开发入口把凭证写在这里，但**全仓没有任何地方读它**（审计 A-01 的另一半：
 * 令牌只写不读）。现已统一到 `SESSION_STORAGE_KEY`，这里只保留一次性的
 * **临时兼容**读取：读到就迁移到新键并删除旧键，避免已经粘过凭证的开发环境
 * 因为换键而突然「掉线」。等下一个版本（V1.1）确认无旧数据后即可整段删除。
 */
export const LEGACY_SESSION_STORAGE_KEY = 'officeai_access_token';

/**
 * 会话在存储里的形状。
 *
 * 与 `OidcSession` 的唯一差别：`expires_at` 允许为 null（开发令牌无过期信息），
 * 因此 `OidcSession` 可直接存进来（number 是 number | null 的子集）。
 */
export interface StoredSession extends Omit<OidcSession, 'expires_at'> {
  /** 过期时刻（毫秒时间戳）；null 表示没有过期信息，**不视为已过期** */
  expires_at: number | null;
}

/** 存储介质类型 */
type StorageKind = 'local' | 'session';

/**
 * 安全地取到某个存储介质。
 *
 * @param kind - 存储介质（local = localStorage，session = sessionStorage）
 * @returns 可用的 Storage；SSR 或不可访问时返回 null
 */
function getStorage(kind: StorageKind): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    const storage = kind === 'local' ? window.localStorage : window.sessionStorage;
    return storage ?? null;
  } catch {
    // 隐私模式 / 浏览器策略禁用存储时，访问属性本身就会抛错
    return null;
  }
}

/**
 * 判断过期时刻字段是否合法。
 *
 * @param value - 原始值
 * @returns number 或 null 返回 true
 */
function isExpiryField(value: unknown): value is number | null {
  if (value === null) return true;
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 判断一个值是否是合法的会话对象。
 *
 * @param value - 待判定值
 * @returns 合法返回 true
 */
function isStoredSession(value: unknown): value is StoredSession {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.access_token !== 'string' || candidate.access_token.trim() === '') {
    return false;
  }
  if (!isExpiryField(candidate.expires_at)) return false;
  const refreshToken = candidate.refresh_token;
  return refreshToken === null || typeof refreshToken === 'string';
}

/**
 * 判断一个值是否是合法的「待完成登录」对象。
 *
 * @param value - 待判定值
 * @returns 合法返回 true
 */
function isPendingAuthorization(value: unknown): value is PendingAuthorization {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  const state = candidate.state;
  const codeVerifier = candidate.codeVerifier;
  const redirectUri = candidate.redirectUri;
  const createdAt = candidate.createdAt;
  return (
    typeof state === 'string' &&
    state !== '' &&
    typeof codeVerifier === 'string' &&
    codeVerifier !== '' &&
    typeof redirectUri === 'string' &&
    redirectUri !== '' &&
    typeof createdAt === 'number' &&
    Number.isFinite(createdAt)
  );
}

/**
 * 读取并迁移历史遗留的会话键（临时兼容，见 `LEGACY_SESSION_STORAGE_KEY`）。
 *
 * 迁移是**尽力而为**：写不进新键时仍然返回内存对象，让这一次访问能用，
 * 但**不删除**旧键（下次还能再试一次迁移），也就不会把用户凭证弄丢。
 *
 * @param storage - 可用的 localStorage
 * @returns 迁移出的会话；没有旧数据时返回 null
 */
function migrateLegacySession(storage: Storage): StoredSession | null {
  let legacy: string | null;
  try {
    legacy = storage.getItem(LEGACY_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
  const token = (legacy ?? '').trim();
  if (token === '') return null;

  const migrated: StoredSession = {
    access_token: token,
    refresh_token: null,
    // 旧键是手粘凭证，没有过期信息：按「无过期信息」处理，不判过期
    expires_at: null,
  };

  if (saveSession(migrated)) {
    try {
      storage.removeItem(LEGACY_SESSION_STORAGE_KEY);
    } catch {
      // 删不掉也无害：新键已存在，下次读取会命中新键、不再走迁移
    }
  }
  return migrated;
}

/**
 * 写入本地会话。
 *
 * @param session - 会话数据
 * @returns 写入成功返回 true；存储不可用或配额溢出返回 false（**不抛异常**）
 */
export function saveSession(session: StoredSession): boolean {
  const storage = getStorage('local');
  if (!storage) return false;
  if (!isStoredSession(session)) return false;
  try {
    storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    return true;
  } catch {
    // 配额溢出（Safari 隐私模式常见）时降级为「这次没存上」
    return false;
  }
}

/**
 * 读取本地会话。
 *
 * 顺序固定：新键 → 旧键迁移 → 都没有视为未登录。
 *
 * @returns 会话数据；不存在、解析失败或形状不对时返回 null
 */
export function loadSession(): StoredSession | null {
  const storage = getStorage('local');
  if (!storage) return null;

  let raw: string | null;
  try {
    raw = storage.getItem(SESSION_STORAGE_KEY);
  } catch {
    raw = null;
  }

  if (raw) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (isStoredSession(parsed)) return parsed;
    } catch {
      // 脏数据：继续尝试旧键，最后当作没有会话（切勿把损坏的内容当登录态）
    }
  }

  // 临时兼容：旧键迁移（详见 LEGACY_SESSION_STORAGE_KEY 的说明）
  return migrateLegacySession(storage);
}

/**
 * 清除本地会话（同时清掉历史遗留键，避免迁移逻辑把它又救回来）。
 */
export function clearSession(): void {
  const storage = getStorage('local');
  if (!storage) return;
  try {
    storage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // 清不掉也不影响后续判定：过期与形状校验都在读取侧兜底
  }
  try {
    storage.removeItem(LEGACY_SESSION_STORAGE_KEY);
  } catch {
    // 同上
  }
}

/**
 * 判断会话是否已过期。
 *
 * 口径：
 * - `expires_at === null`（无过期信息）→ **不判过期**；
 * - 时间戳非法（NaN / Infinity）→ 判过期（宁可让用户重来，也不接受不可信的值）；
 * - 有值且 `<= now` → 判过期（本轮不做自动续期）。
 *
 * @param session - 会话数据
 * @param now - 当前时刻（毫秒），默认取系统时间
 * @returns 过期返回 true
 */
export function isSessionExpired(session: StoredSession, now: number = Date.now()): boolean {
  const expiresAt = session?.expires_at;
  if (expiresAt === null) return false;
  if (typeof expiresAt !== 'number' || !Number.isFinite(expiresAt)) return true;
  return expiresAt <= now;
}

/**
 * 写入「待完成登录」。
 *
 * @param pending - 待完成登录
 * @returns 写入成功返回 true；存储不可用返回 false（调用方必须据此中止跳转）
 */
export function savePending(pending: PendingAuthorization): boolean {
  const storage = getStorage('session');
  if (!storage) return false;
  if (!isPendingAuthorization(pending)) return false;
  try {
    storage.setItem(PENDING_STORAGE_KEY, JSON.stringify(pending));
    return true;
  } catch {
    return false;
  }
}

/**
 * 读取「待完成登录」。
 *
 * @returns 待完成登录；不存在或形状不对时返回 null
 */
export function loadPending(): PendingAuthorization | null {
  const storage = getStorage('session');
  if (!storage) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(PENDING_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isPendingAuthorization(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * 删除「待完成登录」（一次性使用：校验通过或判定过期后立即调用）。
 */
export function clearPending(): void {
  const storage = getStorage('session');
  if (!storage) return;
  try {
    storage.removeItem(PENDING_STORAGE_KEY);
  } catch {
    // 同上：失败不影响安全判定
  }
}
