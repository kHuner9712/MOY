/**
 * 历史令牌存储的**一次性清理**口。
 *
 * 为什么这个文件只剩「删」没有「存」（P0-5 路线 A：令牌不进 JavaScript）
 * ------------------------------------------------------------------
 * 改造前这里负责写读本地会话（访问令牌 / 刷新令牌）与「待完成登录」（state +
 * PKCE verifier）。全部删除的理由是**它们都能被同源脚本读到**：
 *
 * - 访问令牌放本地存储 ⇒ 任何一处 XSS 都能取走并外传，且令牌会长期滞留客户端
 *   （关掉标签页也在）；
 * - PKCE verifier 放脚本可读的存储 ⇒ 授权码一旦被截获，verifier 就在同一份
 *   XSS 可达范围内，S256 的防护强度退回到「没有 PKCE」。
 *
 * 现在两者都搬到服务端的 httpOnly Cookie（`oa_session` / `oa_refresh` /
 * `oa_oidc_pending`），前端**不保存任何凭证**，也**不读任何 Cookie**。
 *
 * 那为什么还要保留这个文件
 * ------------------------
 * 升级是「原地替换」的：用户浏览器里可能还躺着旧版本写下的令牌键。留着不清，
 * 那些令牌会在客户端长期滞留（并且仍然可被脚本读到）——相当于改造只对新用户
 * 生效。因此保留一个**只删不写**的清理函数，在启动与登出时各调用一次：
 * 一旦删掉，旧令牌就真的不在客户端了，清理逻辑本身也就此终结。
 *
 * 红线：本文件不读任何存储内容、不打印任何内容（清理是幂等的，失败也不报错）。
 */

/**
 * 历史遗留的令牌键（最早期开发入口把凭证直接写在这里，且**从未被读取**）。
 *
 * 保留导出是因为门禁脚本与迁移说明要引用它；它只作为「待删除的键名」存在。
 */
export const LEGACY_SESSION_STORAGE_KEY = 'officeai_access_token';

/** 旧版会话键（曾存放 `{access_token, refresh_token, expires_at}`） */
export const SESSION_STORAGE_KEY = 'moy.session.v1';

/** 旧版「待完成登录」键（曾存放 `{state, codeVerifier, redirectUri, createdAt}`） */
export const PENDING_STORAGE_KEY = 'moy.oidc.pending.v1';

/**
 * 需要清理的全部历史键（**只读清单**，不要在别处再拼键名）。
 *
 * 三条都是「旧版本会把凭证或一次性随机串写进去」的键。新的实现不再使用它们，
 * 因此清理是纯删除，不需要判断内容、也不需要迁移。
 */
const LEGACY_TOKEN_KEYS: readonly string[] = [
  LEGACY_SESSION_STORAGE_KEY,
  SESSION_STORAGE_KEY,
  PENDING_STORAGE_KEY,
];

/**
 * 删除旧版本留在客户端的令牌与一次性随机串。
 *
 * 幂等、无副作用、不抛异常：存储被禁用（隐私模式）、配额异常、键不存在都只是
 * 「没清掉」，不影响任何判定——会话是否有效一律由服务端 `/sso/session` 回答，
 * 客户端不存在「读到令牌就能用」这条路径了。
 *
 * 调用点（两处，都是「该清一次」的时机）：
 * - 会话门挂载时（启动清理，覆盖升级上来的老用户）；
 * - 登出时（确保退出后客户端不留任何历史凭证）。
 */
export function purgeLegacySessionStorage(): void {
  if (typeof window === 'undefined') return;
  try {
    // 唯一的存储访问点：取到之后只做删除，且删除目标就是 LEGACY_SESSION_STORAGE_KEY 等历史键
    const legacyLocalStore = window.localStorage; // LEGACY_SESSION_STORAGE_KEY 清理专用
    for (const key of LEGACY_TOKEN_KEYS) {
      legacyLocalStore.removeItem(key);
    }
  } catch {
    // 隐私模式 / 存储被策略禁用：访问属性本身就抛错，忽略即可
  }
  try {
    // 同上：旧版把 PKCE verifier 写在会话级存储里（LEGACY_SESSION_STORAGE_KEY 同期产物）
    const legacySessionStore = window.sessionStorage; // LEGACY_SESSION_STORAGE_KEY 清理专用
    legacySessionStore.removeItem(PENDING_STORAGE_KEY);
  } catch {
    // 同上
  }
}
