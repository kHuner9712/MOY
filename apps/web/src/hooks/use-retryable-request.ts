'use client';

/**
 * 可重试请求 Hook（B15 体验兜底）。
 *
 * 设计要点：
 * 1. **只重发这一个请求**：`retry()` 仅调用传入的 `run`，绝不调用 `router.refresh()`
 *    或整页 reload —— 后者会把用户的输入、滚动位置、抽屉状态全部清掉；
 * 2. **失败计数在 30 秒滑动窗口内累加**：窗口外的旧失败会被自然淘汰，
 *    避免「上午失败两次 + 下午失败一次」也被算成「连续 3 次」；
 * 3. 计数状态由本 Hook 持有（不进组件 state），组件只负责展示，
 *    因此任何使用 `<ErrorState>` 的地方都自动获得聚合提示能力。
 */

import { useCallback, useRef, useState } from 'react';

/** 失败累加窗口（毫秒）：窗口内的重试才视为「连续」 */
export const RETRY_WINDOW_MS = 30_000;

/** 达到该次数后切换为聚合提示 */
export const RETRY_AGGREGATE_THRESHOLD = 3;

/** 连续失败后的聚合文案（业务语言，不解释技术原因） */
export const RETRY_AGGREGATE_HINT = '多次尝试未成功，请稍后再试或联系管理员';

/** `useRetryableRequest` 返回值 */
export interface UseRetryableRequestResult {
  /** 当前窗口内的失败次数 */
  attempts: number;
  /** 是否已达到聚合提示阈值 */
  exhausted: boolean;
  /** 只重发这一个请求（不刷新整页） */
  retry: () => void;
  /** 清零计数（请求成功后调用，或切换筛选条件时调用） */
  reset: () => void;
}

/** Hook 选项 */
export interface UseRetryableRequestOptions {
  /** 聚合提示阈值，默认 3 */
  threshold?: number;
  /** 滑动窗口（毫秒），默认 30 秒 */
  windowMs?: number;
}

/**
 * 跟踪单个请求的连续失败次数。
 *
 * 用法：
 * ```tsx
 * const retryable = useRetryableRequest(() => query.refetch());
 * return <ErrorState message={msg} onRetry={retryable.retry} />;
 * ```
 *
 * @param run - 只重发这一个请求的函数（不得内含整页刷新）
 * @param options - 阈值与窗口配置
 * @returns 次数、聚合标记与重试方法
 */
export function useRetryableRequest(
  run: () => void | Promise<void>,
  options: UseRetryableRequestOptions = {},
): UseRetryableRequestResult {
  const threshold = Math.max(1, Math.trunc(options.threshold ?? RETRY_AGGREGATE_THRESHOLD));
  const windowMs = Math.max(1_000, Math.trunc(options.windowMs ?? RETRY_WINDOW_MS));

  const stampsRef = useRef<number[]>([]);
  const [attempts, setAttempts] = useState<number>(0);

  const reset = useCallback((): void => {
    stampsRef.current = [];
    setAttempts(0);
  }, []);

  const retry = useCallback((): void => {
    const now = Date.now();
    // 淘汰窗口外的旧失败，再记本次
    const recent = stampsRef.current.filter((stamp) => now - stamp <= windowMs);
    recent.push(now);
    stampsRef.current = recent;
    setAttempts(recent.length);

    // 只重发这一个请求：不刷新页面、不重建查询客户端
    try {
      const result = run();
      if (result instanceof Promise) {
        void result.catch(() => {
          // 失败由上层错误态/toast 承担，这里只保证不产生未捕获的 rejection
        });
      }
    } catch {
      // 同步抛错同样交给上层展示，这里不重复抛出
    }
  }, [run, windowMs]);

  return { attempts, exhausted: attempts >= threshold, retry, reset };
}
