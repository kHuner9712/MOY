/**
 * 会话失效广播通道 —— 传输层与会话门之间的唯一联系点。
 *
 * 为什么需要这一层（而不是让页面各自处理）：
 * 「登录状态已失效」这类结局会从**任意**一个请求里冒出来（20 个页面 × 平均 3 个查询），
 * 让每个调用点自己写一次「清会话 + 跳登录」既必然漏写，也会把内部状态词
 * （「令牌」「租户」）暴露到界面上。把它收敛到 `api-client` 一个 chokepoint，
 * 再由本模块把信号交给会话门，页面就完全不需要知道这件事。
 *
 * 为什么不用 React Query 的 `QueryCache.onError`：`QueryClient` 在 `QueryProvider`
 * 里创建，而 Provider 必须位于会话门**之上**（门内要用 `useQuery`）。要让缓存回调
 * 拿到「清会话 + 跳转」的能力，就得把回调注入到 Provider 创建之前，形成
 * 「门依赖 Provider、Provider 依赖门的回调」的装配倒置。模块级通道把这一层拍平。
 *
 * 约束（刻意保持）：
 * - **零 import**：本文件被 `api-client` 引入，而 `api-client` 是几乎所有模块的依赖，
 *   任何 import 都可能绕回成环；
 * - 派发时遍历**副本**并逐个 try/catch：一个监听器抛错不得影响其它监听器，
 *   更不能把异常抛回请求链路（否则一次清理失败会把正常错误覆盖成未知异常）；
 * - 本文件不打印任何内容（不落任何令牌/会话信息）。
 */

/** 会话失效监听器 */
export type UnauthorizedListener = () => void;

/** 当前订阅者集合（用 Set 保证同一函数不会重复注册） */
const listeners = new Set<UnauthorizedListener>();

/**
 * 订阅「会话已失效」事件。
 *
 * @param listener - 监听器（无入参）
 * @returns 取消订阅函数（卸载时必须调用，避免布局重挂载后重复处理）
 */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 广播「会话已失效」事件。
 *
 * 语义是**通知**而不是**命令**：本函数不改变任何状态，也不保证有人在场
 * （登录页本身没有会话门，广播出去无人接收是正常情况）。
 *
 * 幂等性由订阅方负责（会话门用 ref 去重跳转），本模块不做节流——
 * 节流会把「第一次失效」也延迟，而会话失效恰恰是要立刻处理的那一件。
 */
export function emitUnauthorized(): void {
  // 先复制再遍历：监听器内部可能取消订阅（改集合），直接遍历会让迭代顺序错乱
  for (const listener of Array.from(listeners)) {
    try {
      listener();
    } catch {
      // 单个监听器失败不得中断广播，也不得把异常抛回请求链路
    }
  }
}
