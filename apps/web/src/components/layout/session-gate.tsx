'use client';

/**
 * 会话门（F-01）—— 主界面布局的「就绪开关」。
 *
 * 要解决的问题（审计 A-01 的根因）：
 * 令牌只存在内存里，唯一的回灌点是会话恢复逻辑，而这段逻辑原先只在 `/profile`
 * 一页挂载 → **除 `/profile` 外的任何整页加载都不带身份**，表现为「硬刷新 /
 * 从收藏夹进深链就掉登录」。改造后身份由 httpOnly Cookie 承载，这个问题从根上
 * 消失（Cookie 由浏览器自动附加），但「什么时候可以渲染子页面」仍需一个判据。
 *
 * 设计口径（四条，互相支撑，改一条要先想清另外三条）：
 * 1. **以服务端会话状态 + `profile` 为就绪判据**，不是「Cookie 存在即就绪」。
 *    Cookie 是 httpOnly 的，前端读不到（也不该读），必须问 `/sso/session`；
 *    而租户头 `X-Tenant-Id` 的正确值只能从 `profile.tenant.tenant_id` 得到，
 *    提前挂载子页面会让整批请求带上错的租户。代价是硬刷新多两次轻量往返。
 * 2. **三态判据，不是单布尔**：`/sso/session` 回答四个字段，`authenticated=false`
 *    既可能是「过期但可刷新」（该去刷新）也可能是「从未登录」（该去登录页）。
 *    判据收敛在 `auth-refresh.decideSession()` 一处，本文件不自己拼条件。
 * 3. **子页面在就绪前不挂载**（条件渲染）。`AppSidebar` 内的插件分组也在门内——
 *    它一挂载就发请求，漏在门外等于刷新页面时仍有一次无身份请求。
 * 4. **会话失效只有三个入口**：本门自己的会话探针/刷新给出「刷新令牌已死」、
 *    本门请求 `profile` 拿到 20002、运行期任意请求由 `api-client` 广播
 *    （`session-events`）。页面不参与处置。
 *
 * 状态与渲染的映射（这张表就是实现契约）：
 *
 * | phase | 渲染 | 副作用 |
 * | --- | --- | --- |
 * | `restoring` | 骨架（无任何「未登录」文案） | 无 |
 * | `ready` | 侧栏 + 顶栏 + 子页面 | 租户头已在数据落缓存前写好；已排下次主动刷新 |
 * | `anonymous` | 骨架 | 跳登录（带 `next`），ref 去重 |
 * | `expired` | 骨架 | 清租户头 + 跳登录 |
 * | `error` | 错误态（带重试） | **不清会话**（网络抖动不等于失效） |
 */

import { useQueryClient } from '@tanstack/react-query';
import { usePathname, useRouter } from 'next/navigation';
import * as React from 'react';

import { ErrorState, Skeleton } from '@/components/ui/feedback';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { identityApi, setTenantId } from '@/lib/api-client';
import { decideSession, refreshWithRetry, scheduleRefresh } from '@/lib/auth-refresh';
import { shouldReauthenticate, toBusinessMessage } from '@/lib/business-errors';
import { buildLoginHref } from '@/lib/redirect';
import { onUnauthorized } from '@/lib/session-events';
import { fetchSession, logoutSession, type SessionState } from '@/lib/session-client';
import { purgeLegacySessionStorage } from '@/lib/session-store';
import type { CurrentUser } from '@/lib/types';

/** 会话阶段（五态，与状态机一一对应） */
export type SessionPhase = 'restoring' | 'ready' | 'anonymous' | 'expired' | 'error';

/**
 * 握手阶段（会话探针 + 必要时刷新之后得到的结论）。
 *
 * 与 `SessionPhase` 分开是因为二者回答的问题不同：握手阶段回答「**浏览器有没有
 * 可用会话**」，`SessionPhase` 还叠加了「资料取回来了没有」。
 */
type Handshake = 'restoring' | 'authenticated' | 'anonymous' | 'expired' | 'unavailable';

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
  /** 清查询缓存 + 通知服务端登出 + 跳登录 */
  logout: () => void;
}

/** 本地会话失效时的提示：与业务错误码 20002 的既有口径保持一致（同码同文案） */
export const SESSION_EXPIRED_MESSAGE = '登录状态已失效，请重新登录';

/**
 * 会话服务不可用时的提示。
 *
 * 与「登录状态已失效」**必须分开**：前者是临时故障（服务端在维护、网关 503），
 * 用户既没做错什么也不该被登出；后者才是「请重新登录」。混成一句就是把一次
 * 抖动升级成强制重新登录（服务端 QA-02 的同类错误在客户端的翻版）。
 */
export const SESSION_UNAVAILABLE_MESSAGE = '登录服务暂时不可用，请稍后再试';

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
  /** 重试回调（重新握手 / 重新请求资料） */
  onRetry: () => void;
}): React.JSX.Element {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <ErrorState message={message} onRetry={onRetry} />
    </div>
  );
}

/**
 * 会话门：Provider + 就绪判定 + 跳登录 + 主动刷新排期。
 *
 * @param props - 组件属性
 * @returns 门内元素或等待态
 */
export function SessionGate({ children }: { children: React.ReactNode }): React.JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();

  /** 握手阶段（见 `Handshake` 注释） */
  const [handshake, setHandshake] = React.useState<Handshake>('restoring');
  /** 握手轮次：错误态点「重试」时自增，驱动握手 effect 重跑 */
  const [epoch, setEpoch] = React.useState<number>(0);
  /** 跳登录只跳一次（否则每次重渲染都会 replace 一次，挡住用户手动返回） */
  const redirectedRef = React.useRef<boolean>(false);
  /**
   * 当前访问令牌剩余有效期（秒）。
   *
   * 用 ref 而不是 state：它只喂给刷新排期（副作用），不参与渲染。若放进 state，
   * 每次刷新成功都会触发整棵布局重渲染 —— 而刷新每几分钟就发生一次。
   */
  const expiresInRef = React.useRef<number | null>(null);

  // ① 启动清理：删掉旧版本留在客户端的令牌键（只删不读，见 session-store）
  // ② 握手：问服务端「我还登录着吗」，再按三态判据决定「直接用 / 去刷新 / 去登录」
  React.useEffect(() => {
    purgeLegacySessionStorage();
    let cancelled = false;

    const run = async (): Promise<void> => {
      let state: SessionState;
      try {
        state = await fetchSession();
      } catch {
        // 探针本身失败（网关 5xx、断网、`auth_mode=stub` 的 30001）：**不是**未登录，
        // 不能跳登录页（那会把一次维护变成全员重新登录）
        if (!cancelled) setHandshake('unavailable');
        return;
      }
      if (cancelled) return;

      const decision = decideSession(state);
      if (decision !== 'refresh') {
        if (decision === 'use') {
          expiresInRef.current = state.expires_in;
          setHandshake('authenticated');
        } else {
          // `expired=true` 说明用户确实登录过、只是令牌失效了；仅用于区分文案/日志，
          // 渲染与跳转行为完全相同（都是骨架 + 跳登录页）。
          setHandshake(state.expired ? 'expired' : 'anonymous');
        }
        return;
      }

      // decision === 'refresh'：过期但还能续期。四分类处置在 refreshWithRetry 内部。
      const outcome = await refreshWithRetry();
      if (cancelled) return;
      if (outcome.kind === 'refreshed') {
        expiresInRef.current = outcome.expires_in;
        setHandshake('authenticated');
        return;
      }
      if (outcome.kind === 'relogin') {
        // 刷新令牌已死：这是唯一「必须重新登录」的服务端结论
        setHandshake('expired');
        return;
      }
      // transient：临时故障且重试已用尽 → 停在错误态，给用户「重试」，绝不登出
      setHandshake('unavailable');
    };

    void run();

    return () => {
      cancelled = true;
    };
  }, [epoch]);

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
    {
      enabled: handshake === 'authenticated',
      staleTime: Number.POSITIVE_INFINITY,
      retry: 0,
    },
  );

  const profileError = profileQuery.error;
  const needsReauth = profileError !== null && shouldReauthenticate(profileError);
  const profile = profileQuery.data ?? null;

  // ③ 派生阶段：不做第二份状态，避免「状态与请求结果打架」
  const phase: SessionPhase =
    handshake === 'restoring'
      ? 'restoring'
      : handshake === 'unavailable'
        ? 'error'
        : handshake === 'authenticated'
          ? needsReauth
            ? 'expired'
            : profile !== null
              ? 'ready'
              : profileError !== null
                ? 'error'
                : 'restoring'
          : handshake;

  // ④ 失效处置：清租户头（`expired` 的两种来源都收敛到这里）。
  //    不在这里清任何令牌：浏览器里的令牌是 httpOnly Cookie，只有服务端能删。
  React.useEffect(() => {
    if (phase !== 'expired') return;
    expiresInRef.current = null;
    setTenantId(null);
  }, [phase]);

  // ⑤ 跳登录：`anonymous` / `expired` 都继续渲染骨架，只做一次 replace
  React.useEffect(() => {
    if (phase !== 'anonymous' && phase !== 'expired') return;
    if (redirectedRef.current) return;
    redirectedRef.current = true;
    const search = typeof window === 'undefined' ? '' : window.location.search;
    router.replace(buildLoginHref(pathname, search));
  }, [phase, pathname, router]);

  // ⑥ 主动刷新排期：提前 30–60 秒 + 0–30 秒抖动（`nextRefreshDelay` 内部实现），
  //    多标签页因此不会在同一毫秒一起刷新（服务端刷新令牌是轮换的，同时刷新会
  //    互相把对方的令牌打作废 → 401 → 跳登录页，即服务端 QA-07 的风暴回路）。
  React.useEffect(() => {
    if (phase !== 'ready') return undefined;
    return scheduleRefresh(expiresInRef.current, {
      onRefreshed: (nextExpiresIn) => {
        expiresInRef.current = nextExpiresIn;
      },
      onRelogin: () => {
        // 刷新令牌已死：走与其他失效入口一致的处置（清租户头 + 跳登录）
        expiresInRef.current = null;
        setHandshake('expired');
      },
      onUnavailable: () => {
        // 临时故障：**保持会话**，排期内部会在固定间隔后重试下一轮
      },
    });
  }, [phase]);

  // ⑦ 运行期失效：任意请求拿到 20002（且刷新失败）→ 传输层广播 → 这里收敛
  const handleUnauthorized = React.useCallback(() => {
    expiresInRef.current = null;
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
   * 错误态的重试：握手失败时重跑握手，资料失败时重发资料。
   *
   * 两者分开而不是一律重跑：资料请求失败时重新握手会多一次 `/sso/session`
   * 往返，而会话本身刚刚才确认有效。
   */
  const retry = React.useCallback(() => {
    if (handshake === 'unavailable' || handshake === 'restoring') {
      setHandshake('restoring');
      setEpoch((current) => current + 1);
      return;
    }
    void refetchProfile();
  }, [handshake, refetchProfile]);

  /**
   * 退出登录：通知服务端删 Cookie → 清本地遗留键 → 清查询缓存 → 回登录页。
   *
   * 服务端调用失败也继续本地清理：用户点的是「退出」，本地的可见状态必须立刻
   * 清干净（`queryClient.clear()` 保留旧数据会让换账号后短暂看到上一个账号的数据）。
   */
  const logout = React.useCallback(() => {
    void logoutSession().catch(() => undefined);
    purgeLegacySessionStorage();
    setTenantId(null);
    expiresInRef.current = null;
    queryClient.clear();
    setHandshake('anonymous');
  }, [queryClient]);

  const contextValue = React.useMemo<SessionContextValue>(
    () => ({
      phase,
      user: phase === 'ready' ? profile : null,
      errorMessage:
        phase === 'error'
          ? handshake === 'unavailable'
            ? SESSION_UNAVAILABLE_MESSAGE
            : toBusinessMessage(profileError)
          : phase === 'expired'
            ? SESSION_EXPIRED_MESSAGE
            : '',
      refreshProfile,
      logout,
    }),
    [phase, profile, profileError, handshake, refreshProfile, logout],
  );

  if (phase === 'error') {
    const message =
      handshake === 'unavailable' ? SESSION_UNAVAILABLE_MESSAGE : toBusinessMessage(profileError);
    return <SessionErrorState message={message} onRetry={retry} />;
  }

  if (phase !== 'ready') {
    // `restoring` / `anonymous` / `expired` 一律骨架：组件树里不存在
    // 「未登录 / 请登录」这类可见文案，跳转生效前的这一帧也不会闪错东西。
    return <SessionSkeleton />;
  }

  return <SessionContext.Provider value={contextValue}>{children}</SessionContext.Provider>;
}
