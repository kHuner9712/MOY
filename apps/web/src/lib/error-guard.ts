'use client';

/**
 * 全局错误兜底（B15）。
 *
 * 目标：任何「用户能感知到但没人处理」的错误，都以业务语言提示一次，
 * 且**同一错误 3 秒内只提示一次**——否则一个失败的轮询能在几秒内刷出一屏 toast。
 *
 * 覆盖三类入口：
 * 1. `window.onerror` / `unhandledrejection`（未捕获异常）；
 * 2. 服务端异常与适配层不可用（5xx / 网络层，`isServerError`）；
 * 3. 流式中断（`reportStreamFailure`，由对话页在 onError 时调用）。
 *
 * 铁律：只展示业务语言文案，绝不展示错误码、堆栈、下游原始报文。
 */

import { toast } from '@/components/ui/toast';
import { AdapterUnavailableError, BusinessError } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';

/** 去重窗口（毫秒）：同一错误 3 秒内只提示一次 */
const DEDUPE_WINDOW_MS = 3_000;

/** 未捕获异常的统一文案（不给用户看堆栈） */
const UNCAUGHT_MESSAGE = '页面刚刚遇到一个问题，请重试刚才的操作';

/** 去重表：`code:message` → 最近一次提示时间 */
const recentToasts = new Map<string, number>();

/**
 * 判断是否为「服务端 / 网络层」异常（5xx 语义）。
 *
 * @param error - 捕获到的异常
 * @returns 需要提示用户时返回 true
 */
export function isServerError(error: unknown): boolean {
  if (error instanceof AdapterUnavailableError) {
    return true;
  }
  return error instanceof BusinessError && error.code >= 30001 && error.code <= 30999;
}

/**
 * 生成去重键。
 *
 * @param message - 业务语言文案
 * @param code - 业务错误码（未知时传 0）
 * @returns 去重键
 */
function dedupeKey(message: string, code: number): string {
  return `${code}:${message}`;
}

/**
 * 判断这次提示是否应被去重丢弃。
 *
 * @param message - 业务语言文案
 * @param code - 业务错误码
 * @returns 需要提示返回 true（同时记录时间）
 */
function shouldNotify(message: string, code: number): boolean {
  const now = Date.now();
  const key = dedupeKey(message, code);

  // 顺手清理过期键，避免长会话下 Map 无限增长
  for (const [existingKey, stamp] of recentToasts) {
    if (now - stamp > DEDUPE_WINDOW_MS) {
      recentToasts.delete(existingKey);
    }
  }

  const last = recentToasts.get(key);
  if (last !== undefined && now - last <= DEDUPE_WINDOW_MS) {
    return false;
  }
  recentToasts.set(key, now);
  return true;
}

/**
 * 提示一次业务语言错误（带 3 秒去重）。
 *
 * 服务端/网络层异常统一归一为「服务暂时不可用」，这样连续多次超时只提示一次；
 * 业务错误保留后端文案（同码不同文案的场景需要各自的措辞）。
 *
 * @param error - 捕获到的异常
 * @returns 实际是否弹出了提示
 */
export function reportError(error: unknown): boolean {
  const message = isServerError(error) ? '服务暂时不可用，请稍后再试' : toBusinessMessage(error);
  const code = error instanceof BusinessError ? error.code : 0;
  if (!shouldNotify(message, code)) {
    return false;
  }
  toast.error(message);
  return true;
}

/**
 * 提示流式中断 / 失败。
 *
 * 用户主动点「停止」不算失败，不应调用本函数；
 * 只在服务端下发 error 事件或网络中断时调用。
 *
 * @param message - 业务语言文案；为空时使用兜底文案
 * @returns 实际是否弹出了提示
 */
export function reportStreamFailure(message: string): boolean {
  const text = message.trim() || '这次回答没有完成，可以点「重新发送」再试一次';
  if (!shouldNotify(text, 0)) {
    return false;
  }
  toast.error(text);
  return true;
}

/**
 * 安装全局错误兜底。
 *
 * 返回卸载函数，供 React effect 清理；重复调用是安全的（只挂一次监听）。
 *
 * @returns 卸载函数
 */
export function installErrorGuard(): () => void {
  if (typeof window === 'undefined') {
    return () => undefined;
  }

  /** 未捕获异常 */
  const handleError = (event: ErrorEvent): void => {
    const message =
      event.error instanceof Error ? toBusinessMessage(event.error) : UNCAUGHT_MESSAGE;
    if (shouldNotify(message, 0)) {
      toast.error(message);
    }
    // 控制台保留原始信息，便于开发排查（用户不可见）
    console.error('[error-guard]', event.error ?? event.message);
  };

  /** 未处理的 Promise 拒绝 */
  const handleRejection = (event: PromiseRejectionEvent): void => {
    const reason: unknown = event.reason;
    const message = reason instanceof Error ? toBusinessMessage(reason) : UNCAUGHT_MESSAGE;
    if (shouldNotify(message, 0)) {
      toast.error(message);
    }
    console.error('[error-guard] unhandled rejection', reason);
  };

  window.addEventListener('error', handleError);
  window.addEventListener('unhandledrejection', handleRejection);

  return () => {
    window.removeEventListener('error', handleError);
    window.removeEventListener('unhandledrejection', handleRejection);
  };
}

/** 仅供测试/切换会话时清零去重表 */
export function resetErrorGuardThrottle(): void {
  recentToasts.clear();
}
