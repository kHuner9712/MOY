'use client';

/**
 * 会话 Hook（兼容薄壳）。
 *
 * 会话状态的真实持有者已经上提到布局层的会话门（`components/layout/session-gate.tsx`）：
 * 只有它知道「什么时候算登录成功」，也只有它在门内挂载子页面，从而保证
 * 子页面的第一批请求一定带上正确的身份与租户头。
 *
 * 本文件保留原有导出名、类型名与签名不变，只是把读值改成消费 Context，
 * 原因有两个：
 * 1. 还有调用方按旧契约写（例如 `/profile`），一次性改完会让改动面失控；
 * 2. 更重要的是**防止重复请求**：如果页面自己再跑一遍「读存储 + 取资料」，
 *    一次硬刷新就会请求两次资料，还可能拿到两个租户头交替的时刻。
 *
 * @deprecated 新代码请直接用 `useSessionContext()`；本函数只是兼容壳。
 */

import { useSessionContext } from '@/components/layout/session-gate';
import type { CurrentUser } from '@/lib/types';

/** 会话状态 */
export interface SessionState {
  /** 是否仍在恢复会话（等价于门的 `restoring` 阶段） */
  loading: boolean;
  /** 当前登录用户；未登录为 null */
  user: CurrentUser | null;
  /** 业务语言错误提示 */
  errorMessage: string;
}

/** `useSession` 返回值 */
export interface UseSessionResult extends SessionState {
  /** 重新加载会话（重新请求资料，不刷新整页） */
  reload: () => Promise<void>;
  /** 退出登录（清会话 + 清查询缓存 + 回登录页） */
  logout: () => void;
}

/**
 * 会话 Hook：从会话上下文映射出旧的返回形状。
 *
 * `reload` 的签名仍是 `() => Promise<void>`（调用方写 `void session.reload()`），
 * 内部只是同步触发一次重新请求，语义未变。
 *
 * @returns 会话状态与操作方法
 */
export function useSession(): UseSessionResult {
  const context = useSessionContext();
  return {
    loading: context.phase === 'restoring',
    user: context.user,
    errorMessage: context.errorMessage,
    reload: async () => {
      context.refreshProfile();
    },
    logout: context.logout,
  };
}
