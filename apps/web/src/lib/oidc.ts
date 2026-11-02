/**
 * 统一登录回跳参数的**纯解析层**。
 *
 * 这个文件为什么只剩一个函数（P0-5 路线 A：令牌不进 JavaScript）
 * ------------------------------------------------------------
 * 改造前这里承担了三件事：生成 PKCE 对（verifier + S256 挑战）、拼授权地址、
 * 用授权码在**浏览器里**兑换令牌。三件事现在全部搬到服务端：
 *
 * - `/sso/start` 生成 verifier 并存进 httpOnly 的暂存 Cookie，返回授权地址；
 *   前端只负责跳转，**verifier 不再经过 JavaScript**（改造前它存在脚本可读的
 *   会话级存储里，一旦同源被 XSS，S256 的防护强度退回到「没有 PKCE」）；
 * - `/sso/complete` 在服务端兑换令牌，并以 `Set-Cookie` 下发 httpOnly 会话 Cookie；
 *   响应体里没有任何令牌，因此浏览器里没有「持有令牌」这一步。
 *
 * 唯一必须留在浏览器的是**读地址栏**：授权码与状态串是 IdP 用 302 带回来的
 * 查询参数，只有页面能拿到它们。解析本身是纯函数（无存储、无网络、无副作用），
 * 因此仍单独一层，便于穷举断言与门禁脚本直测。
 *
 * 红线：本文件不解析令牌、不写存储、不记录日志，也不判断「这次登录是否有效」——
 * 那是服务端的事（state 与 verifier 都只存在于 httpOnly Cookie 里，前端无从伪造）。
 */

/** 回跳参数不合格的具体原因（只用于内部分支，绝不直接展示给用户） */
export type CallbackErrorReason = 'provider-error' | 'missing-code' | 'missing-state';

/** 回跳参数的解析结果（判别联合：调用方必须处理全部分支） */
export type CallbackParseResult =
  | { kind: 'ok'; code: string; state: string }
  | { kind: 'error'; reason: CallbackErrorReason }
  | { kind: 'missing' };

/**
 * 解析回跳参数。
 *
 * 判定顺序刻意如此（先看服务端拒绝，再看缺失，最后才看正常）：
 * 服务端明确拒绝时，即使地址里同时带着半截参数也不能当成「正常」——
 * 否则一次被 IdP 明确拒绝的登录会被当成「参数齐全」走到兑换，用户看到的
 * 就不再是「这次登录被拒绝」，而是一句莫名其妙的失败文案。
 *
 * @param search - 地址栏 query（含或不含问号均可）
 * @returns 判别联合：ok / error / missing
 */
export function parseCallbackQuery(search: string): CallbackParseResult {
  const raw = typeof search === 'string' ? search : '';
  const params = new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : raw);

  if ((params.get('error') ?? '').trim() !== '') {
    return { kind: 'error', reason: 'provider-error' };
  }

  const code = (params.get('code') ?? '').trim();
  const state = (params.get('state') ?? '').trim();

  // 两个都没有：用户是直接打开本页的，不是从统一登录回来的
  if (code === '' && state === '') {
    return { kind: 'missing' };
  }
  if (state === '') {
    return { kind: 'error', reason: 'missing-state' };
  }
  if (code === '') {
    return { kind: 'error', reason: 'missing-code' };
  }

  return { kind: 'ok', code, state };
}
