import type React from 'react';
import type { Metadata } from 'next';

import { CallbackFlow } from './callback-flow';

/**
 * 统一登录回跳页（`/auth/callback`）。
 *
 * 本页只做壳与元信息，真正的流程在 `CallbackFlow`（客户端组件）里：
 * 校验一次性随机值 → S256 兑换 → 写会话 → 注入内存 → 进入工作台。
 *
 * 为什么严格按这个顺序（而不是「先存下来再说」）：回跳参数只有在与发起方的
 * 本地状态严格配对、并用 PKCE 证明「verifier 就在这台浏览器上」之后才能兑换。
 * 少了这一步就落库，等于把「任何人构造一个链接即可触发登录态写入」的缺口留在线上。
 *
 * 两个刻意的细节：
 * 1. `referrer: no-referrer`：不向外部站点传递含参数的完整地址；
 * 2. 地址栏参数在客户端流程的第一步就被清掉（`history.replaceState`），
 *    不新增历史记录——用户点「后退」不会又回到带参数的那一页。
 *
 * @returns 回跳页元素
 */
export const metadata: Metadata = {
  title: '登录',
  // 不向外部站点传递完整地址（含回跳参数）
  referrer: 'no-referrer',
};

/**
 * 统一登录回跳页组件（服务端壳）。
 *
 * @returns 回跳页元素
 */
export default function AuthCallbackPage(): React.JSX.Element {
  return <CallbackFlow />;
}
