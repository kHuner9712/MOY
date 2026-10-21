'use client';

import { useCallback, useEffect, useState } from 'react';

import { identityApi, setAccessToken, setTenantId } from '@/lib/api-client';
import { shouldReauthenticate, toBusinessMessage } from '@/lib/business-errors';
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
  /** 退出登录（清除本地令牌） */
  logout: () => void;
}

/**
 * 会话 Hook：加载当前登录用户信息。
 *
 * 设计要点：
 * - 登录流程由身份服务托管页承担，本 Hook 只消费访问令牌与资料接口；
 * - 令牌失效（20002）时清理本地状态，由上层跳转登录页；
 * - 错误一律转换为业务语言文案。
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
    try {
      const profile = await identityApi.profile();
      setTenantId(profile.tenant.tenant_id);
      setState({ loading: false, user: profile, errorMessage: '' });
    } catch (error) {
      if (shouldReauthenticate(error)) {
        setAccessToken(null);
      }
      setState({ loading: false, user: null, errorMessage: toBusinessMessage(error) });
    }
  }, []);

  const logout = useCallback(() => {
    setAccessToken(null);
    setTenantId('default');
    setState({ loading: false, user: null, errorMessage: '' });
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { ...state, reload, logout };
}
