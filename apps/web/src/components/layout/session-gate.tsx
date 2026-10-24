'use client';

/**
 * 会话门（F-01）—— 主界面布局的「就绪开关」。
 *
 * 要解决的问题（审计 A-01 的根因）：
 * 令牌只存在 `api-client` 的模块内存里，唯一的回灌点是会话恢复逻辑，而这段逻辑
 * 原先只在 `/profile` 一页挂载 → **除 `/profile` 外的任何整页加载都不带身份**，
 * 表现为「硬刷新 / 从收藏夹进深链就掉登录」。
 *
 * 设计口径（三条，互相支撑，改一条要先想清另外两条）：
 * 1. **以 `profile` 返回为就绪判据**，不是「令牌注入即就绪」。因为租户请求头
 *    `X-Tenant-Id` 的正确值只能从 `profile.tenant.tenant_id` 得到，提前挂载子页面
 *    会让整批请求带上错的租户。代价是硬刷新多一次轻量往返。
 * 2. **子页面在就绪前不挂载**（条件渲染）。`AppSidebar` 内的插件分组也在门内——
 *    它一挂载就发请求，漏在门外等于刷新页面时仍有一次无身份请求。
 * 3. **会话失效只有两个入口**：本门自己请求 `profile` 拿到 20002，或运行期任意请求
 *    由 `api-client` 广播（`session-events`）。页面不参与处置。
 * 4. **租户头由服务端教**：就绪前 `setTenantId(null)`（请求不带该头），`profile` 回来后
 *    才写入真租户。反向（先猜一个）会让请求头压过令牌声明，SaaS 下首个 `profile` 即 403。
 *
 * 状态与渲染的映射（这张表就是实现契约）：
 *
 * | phase | 渲染 | 副作用 |
 * | --- | --- | --- |
 * | `restoring` | 骨架（无任何「未登录」文案） | 无 |
 * | `ready` | 侧栏 + 顶栏 + 子页面 | 租户头已在数据落缓存前写好 |
 * | `anonymous` | 骨架 | 跳登录（带 `next`），ref 去重 |
 * | `expired` | 骨架 | 清会话 + 清租户头 + 跳登录 |
 * | `error` | 错误态（带重试） | **不清会话**（网络抖动不等于失效） |
 */

import { useQueryClient } from '@tanstack/react-query';
import { usePathname, useRouter } from 'next/navigation';
import * as React from 'react';

import { ErrorState, Skeleton } from '@/components/ui/feedback';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { identityApi, setAccessToken, setTenantId } from '@/lib/api-client';
import { shouldReauthenticate, toBusinessMessage } from '@/lib/business-errors';
import { buildLoginHref } from '@/lib/redirect';
import { onUnauthorized } from '@/lib/session-events';
import { clearSession, isSessionExpired, loadSession } from '@/lib/session-store';
import type { CurrentUser } from '@/lib/types';

/** 会话阶段（五态，与状态机一一对应） */
export type SessionPhase = 'restoring' | 'ready' | 'anonymous' | 'expired' | 'error';

/** 会话上下文（对外 API） */
export interface SessionContextValue {
  /** 当前阶段 */
  phase: SessionPhase;
  /** 就绪后必有值；其余态为 null */
  user: CurrentUser | null;
  /** 仅 error 态有业务语言文案 */
  errorMessage: string;
  /** 重新请求资料（error 态的重试，也可做「刷新账户信息」） */
  refreshProfile: () => void;
  /** 清会话 + 清查询缓存 + 跳登录 */
  logout: () => void;
}

/** 本地会话失效时的提示：与业务错误码 20002 的既有口径保持一致（同码同文案） */
export const SESSION_EXPIRED_MESSAGE = '登录状态已失效，请重新登录';

const SessionContext = React.createContext<SessionContextValue | null>(null);

/**
 * 读取会话上下文。
 *
 * @returns 会话上下文
 * @throws Error 在会话门之外调用时抛出（开发期立即暴露误用；该文案不会进用户界面）
 */
export function useSessionContext(): SessionContextValue {
  const context = React.useContext(SessionContext);
  if (context === null) {
    throw new Error('会话状态只能在主界面布局内使用');
  }
  return context;
}

/**
 * 会话骨架：只画块，不写任何文案。
 *
 * 为什么不放「正在登录…」之外的任何提示：`anonymous` / `expired` 两态**同样**
 * 渲染本组件（跳转生效前的那一刻），任何「未登录 / 请登录」字样都会在那里闪一下，
 * 这既是体验缺陷，也会暴露内部状态词。
 *
 * @returns 骨架元素
 */
export function SessionSkeleton(): React.JSX.Element {
  return (
    <div className="flex min-h-screen" aria-busy="true" role="status">
      <div className="hidden w-60 shrink-0 flex-col gap-3 border-r bg-muted/20 p-4 md:flex">
        <Skeleton className="h-8 w-3/4" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-5/6" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-16 items-center border-b px-6">
          <Skeleton className="h-5 w-32" />
        </div>
        <div className="flex-1 space-y-4 p-6">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </div>
    </div>
  );
}

/**
 * 会话错误态：给用户一条出路（重试），并且**不清会话**。
 *
 * @param props - 组件属性
 * @returns 错误态元素
 */
export function SessionErrorState({
  message,
  onRetry,
}: {
  /** 业务语言文案 */
  message: string;
  /** 重试回调（重新请求资料） */
  onRetry: () => void;
}): React.JSX.Element {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <ErrorState message={message} onRetry={onRetry} />
    </div>
  );
}

/**
 * 会话门：Provider + 就绪判定 + 跳登录。
 *
 * @param props - 组件属性
 * @returns 门内元素或等待态
 */
export function SessionGate({ children }: { children: React.ReactNode }): React.JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();

  /**
   * 「本地会话握手」阶段：与请求结果无关，只看存储。
   *
   * 之所以与查询状态分开存：`anonymous` / `expired` 是**推断出来的前置结论**
   * （没有会话 / 已过期），此时连请求都不该发，不能用「请求成功与否」表达。
   */
  const [handshake, setHandshake] = React.useState<'restoring' | 'anonymous' | 'expired'>(
    'restoring',
  );
  /** 令牌已注入，可以请求资料（没有本地会话时保持 false，不发匿名请求） */
  const [tokenReady, setTokenReady] = React.useState<boolean>(false);
  /** 跳登录只跳一次（否则每次重渲染都会 replace 一次，挡住用户手动返回） */
  const redirectedRef = React.useRef<boolean>(false);

  // ① 挂载：读本地会话（同步存储）→ 决定「没有 / 过期 / 可以取资料」
  React.useEffect(() => {
    const stored = loadSession();
    if (!stored) {
      setAccessToken(null);
      setHandshake('anonymous');
      return;
    }
    if (isSessionExpired(stored)) {
      // 过期即视为未登录：别再拿一个必然被拒的令牌去请求
      clearSession();
      setAccessToken(null);
      setTenantId(null);
      setHandshake('expired');
      return;
    }
    setAccessToken(stored.access_token);
    setTokenReady(true);
  }, []);

  const profileQuery = useApiQuery(
    QUERY_KEYS.profile(),
    async () => {
      const profile = await identityApi.profile();
      // 关键顺序：租户头必须在 profile **落进缓存之前**写好。
      // 写在 effect 里会晚于子页面挂载（同一 commit 内子页面的首批请求已经发出），
      // 那样第一批请求的 X-Tenant-Id 仍是未知态。
      //
      // 注意这一次 `profile` 请求本身**不带** `X-Tenant-Id`（`setTenantId(null)` 的语义），
      // 后端按令牌声明解析出真租户再回显——这是唯一能让客户端学到租户的顺序。
      // 若在这里预设一个默认租户，请求头会压过令牌声明，形成自举循环（详见 D-2）。
      setTenantId(profile.tenant.tenant_id);
      return profile;
    },
    { enabled: tokenReady, staleTime: Number.POSITIVE_INFINITY, retry: 0 },
  );

  const profileError = profileQuery.error;
  const needsReauth = profileError !== null && shouldReauthenticate(profileError);
  const profile = profileQuery.data ?? null;

  // ② 派生阶段：不做第二份状态，避免「状态与请求结果打架」
  const phase: SessionPhase =
    handshake !== 'restoring'
      ? handshake
      : needsReauth
        ? 'expired'
        : profile !== null
          ? 'ready'
          : profileError !== null
            ? 'error'
            : 'restoring';

  // ③ 失效处置：清会话 + 清租户头（`expired` 的两种来源都收敛到这里）
  React.useEffect(() => {
    if (phase !== 'expired') return;
    clearSession();
    setAccessToken(null);
    setTenantId(null);
  }, [phase]);

  // ④ 跳登录：`anonymous` / `expired` 都继续渲染骨架，只做一次 replace
  React.useEffect(() => {
    if (phase !== 'anonymous' && phase !== 'expired') return;
    if (redirectedRef.current) return;
    redirectedRef.current = true;
    const search = typeof window === 'undefined' ? '' : window.location.search;
    router.replace(buildLoginHref(pathname, search));
  }, [phase, pathname, router]);

  // ⑤ 运行期失效：任意请求拿到 20002 → 传输层广播 → 这里收敛
  const handleUnauthorized = React.useCallback(() => {
    setTokenReady(false);
    setHandshake('expired');
  }, []);

  React.useEffect(() => onUnauthorized(handleUnauthorized), [handleUnauthorized]);

  /**
   * 重新请求资料（error 态的重试入口）。
   *
   * 只重发这一个请求，不刷新整页：整页刷新会把用户正在填的内容一起丢掉。
   * 依赖取 `refetch` 而不是整个查询结果对象：后者每次渲染都是新引用，
   * 会让下面的 `useMemo` 每次都重建，等于没有 memo。
   */
  const { refetch: refetchProfile } = profileQuery;
  const refreshProfile = React.useCallback(() => {
    void refetchProfile();
  }, [refetchProfile]);

  /**
   * 退出登录：清会话 + 清查询缓存 + 回登录页。
   *
   * 必须 `queryClient.clear()`（而不是 `invalidateQueries`）：后者保留旧数据直到新数据
   * 回来，换账号后会在界面上短暂看到上一个账号的插件/知识库列表。
   */
  const logout = React.useCallback(() => {
    clearSession();
    setAccessToken(null);
    setTenantId(null);
    setTokenReady(false);
    queryClient.clear();
    setHandshake('anonymous');
  }, [queryClient]);

  const contextValue = React.useMemo<SessionContextValue>(
    () => ({
      phase,
      user: phase === 'ready' ? profile : null,
      errorMessage:
        phase === 'error'
          ? toBusinessMessage(profileError)
          : phase === 'expired'
            ? SESSION_EXPIRED_MESSAGE
            : '',
      refreshProfile,
      logout,
    }),
    [phase, profile, profileError, refreshProfile, logout],
  );

  if (phase === 'error') {
    return (
      <SessionErrorState
        message={toBusinessMessage(profileError)}
        onRetry={refreshProfile}
      />
    );
  }

  if (phase !== 'ready') {
    // `restoring` / `anonymous` / `expired` 一律骨架：组件树里不存在
    // 「未登录 / 请登录」这类可见文案，跳转生效前的这一帧也不会闪错东西。
    return <SessionSkeleton />;
  }

  return <SessionContext.Provider value={contextValue}>{children}</SessionContext.Provider>;
}
