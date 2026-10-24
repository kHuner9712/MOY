'use client';

import { useCallback, useEffect, useState } from 'react';

import { identityApi, setAccessToken, setTenantId } from '@/lib/api-client';
import { shouldReauthenticate, toBusinessMessage } from '@/lib/business-errors';
import { clearSession, isSessionExpired, loadSession } from '@/lib/session-store';
import type { CurrentUser } from '@/lib/types';

/** 会话状态 */
export interface SessionState {
  /** 是否已完成初始加载 */
  loading: boolean;
  /** 当前登录用户；未登录为 null */
  user: CurrentUser | null;
  /** 业务语言错误提示 */
  errorMessage: string;
}

/** `useSession` 返回值 */
export interface UseSessionResult extends SessionState {
  /** 重新加载会话 */
  reload: () => Promise<void>;
  /** 退出登录（清除本地会话） */
  logout: () => void;
}

/** 本地会话失效（缺失 / 过期）时的提示：与业务错误码 20002 的既有口径一致 */
const SESSION_EXPIRED_MESSAGE = '登录状态已失效，请重新登录';

/**
 * 会话 Hook：加载当前登录用户信息。
 *
 * 本批次的关键改动（审计 A-01：令牌只写不读）：请求资料**之前**先从本地会话
 * 恢复访问令牌并注入请求层。此前的写法是 mount 就直接请求 profile，
 * 导致统一登录完成后页面一刷新就变成「未登录」，且所有请求都不带身份。
 *
 * 判定顺序（不可颠倒）：
 * 1. 没有本地会话 → 直接置未登录（**不发请求**，避免拿匿名请求去撞后端）；
 *    读取顺序由 `loadSession` 固定为「新键 → 旧键迁移 → 都没有」，
 *    因此已经粘过凭证的开发环境不会因为换键而掉线；
 * 2. 会话已过期 → 清掉本地会话并置未登录（本轮不做自动续期，过期即回登录页）；
 *    `expires_at: null`（开发令牌）**不判过期**；
 * 3. 会话有效 → 注入令牌 → 请求资料；返回「登录状态已失效」时同样清掉本地会话。
 *
 * 错误一律转换为业务语言文案；不打印任何令牌。
 *
 * @returns 会话状态与操作方法
 */
export function useSession(): UseSessionResult {
  const [state, setState] = useState<SessionState>({
    loading: true,
    user: null,
    errorMessage: '',
  });

  const reload = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, errorMessage: '' }));

    const stored = loadSession();
    if (!stored) {
      setAccessToken(null);
      setState({ loading: false, user: null, errorMessage: '' });
      return;
    }
    if (isSessionExpired(stored)) {
      // 过期即视为未登录：清掉存储，别再拿一个必然被拒的令牌去请求
      clearSession();
      setAccessToken(null);
      setState({ loading: false, user: null, errorMessage: SESSION_EXPIRED_MESSAGE });
      return;
    }

    setAccessToken(stored.access_token);

    try {
      const profile = await identityApi.profile();
      setTenantId(profile.tenant.tenant_id);
      setState({ loading: false, user: profile, errorMessage: '' });
    } catch (error) {
      if (shouldReauthenticate(error)) {
        // 服务端判定令牌失效：本地会话一定也失效了，一起清掉
        clearSession();
        setAccessToken(null);
      }
      setState({ loading: false, user: null, errorMessage: toBusinessMessage(error) });
    }
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setAccessToken(null);
    setTenantId('default');
    setState({ loading: false, user: null, errorMessage: '' });
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { ...state, reload, logout };
}
