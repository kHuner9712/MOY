/**
 * 全局错误兜底回归网（B15）。
 *
 * 断言三条铁律：
 * 1. 只展示业务语言文案（不出现错误码 / 堆栈）；
 * 2. 同一错误 3 秒内只提示一次；
 * 3. 未捕获异常与未处理拒绝都走同一去重通道，且卸载后不再提示。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { toast } from '@/components/ui/toast';

import { AdapterUnavailableError, BusinessError } from './api-client';
import {
  installErrorGuard,
  isServerError,
  reportError,
  reportStreamFailure,
  resetErrorGuardThrottle,
} from './error-guard';

vi.mock('@/components/ui/toast', () => ({
  toast: { error: vi.fn() },
}));

/** 取出 toast.error 的 mock 便于断言 */
const toastError = vi.mocked(toast.error);

beforeEach(() => {
  toastError.mockClear();
  resetErrorGuardThrottle();
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-31T00:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('isServerError', () => {
  it('适配层不可用与 30001-30999 均视为服务端异常', () => {
    expect(isServerError(new AdapterUnavailableError())).toBe(true);
    expect(isServerError(new BusinessError(30004, '数据库不可用'))).toBe(true);
    expect(isServerError(new BusinessError(30001, '内部错误'))).toBe(true);
  });

  it('业务错误与未知值不算服务端异常', () => {
    expect(isServerError(new BusinessError(20003, '无权限'))).toBe(false);
    expect(isServerError(new BusinessError(15003, '席位已满'))).toBe(false);
    expect(isServerError(new Error('x'))).toBe(false);
    expect(isServerError('boom')).toBe(false);
  });
});

describe('reportError', () => {
  it('服务端异常统一归一为「服务暂时不可用」', () => {
    expect(reportError(new AdapterUnavailableError())).toBe(true);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0]?.[0]).toBe('服务暂时不可用，请稍后再试');
  });

  it('业务错误保留后端文案', () => {
    reportError(new BusinessError(14006, '组织层级最多 5 层，请调整后重试'));
    expect(toastError.mock.calls[0]?.[0]).toBe('组织层级最多 5 层，请调整后重试');
  });

  it('业务错误 message 为空时按 code 兜底', () => {
    reportError(new BusinessError(20005, ''));
    expect(toastError.mock.calls[0]?.[0]).toBe('操作冲突，请刷新后重试');
  });

  it('同一错误 3 秒内只提示一次，超过窗口后恢复', () => {
    const first = reportError(new BusinessError(20003, '无权限'));
    expect(first).toBe(true);
    expect(reportError(new BusinessError(20003, '无权限'))).toBe(false);
    expect(toastError).toHaveBeenCalledTimes(1);
    vi.setSystemTime(new Date('2026-10-31T00:00:04Z'));
    expect(reportError(new BusinessError(20003, '无权限'))).toBe(true);
    expect(toastError).toHaveBeenCalledTimes(2);
  });

  it('同码不同文案各自去重', () => {
    reportError(new BusinessError(20005, '角色下还有成员'));
    reportError(new BusinessError(20005, '部门下还有成员'));
    expect(toastError).toHaveBeenCalledTimes(2);
  });
});

describe('reportStreamFailure', () => {
  it('空文案使用兜底文案，空白同样视为空', () => {
    expect(reportStreamFailure('   ')).toBe(true);
    expect(toastError.mock.calls[0]?.[0]).toBe('这次回答没有完成，可以点「重新发送」再试一次');
  });

  it('非空文案去首尾空白后提示，并参与去重', () => {
    expect(reportStreamFailure(' 网络中断 ')).toBe(true);
    expect(toastError.mock.calls[0]?.[0]).toBe('网络中断');
    expect(reportStreamFailure('网络中断')).toBe(false);
  });
});

describe('installErrorGuard', () => {
  it('未捕获异常与未处理拒绝都提示，卸载后移除监听', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const uninstall = installErrorGuard();

    // 直接调用安装的监听器（而不是 dispatchEvent：jsdom 会把未处理拒绝上报给进程）
    const errorHandler = addSpy.mock.calls.find((call) => call[0] === 'error')?.[1] as (
      event: ErrorEvent,
    ) => void;
    const rejectionHandler = addSpy.mock.calls.find(
      (call) => call[0] === 'unhandledrejection',
    )?.[1] as (event: PromiseRejectionEvent) => void;

    errorHandler({ error: new Error('崩溃') } as ErrorEvent);
    expect(toastError).toHaveBeenCalledTimes(1);

    rejectionHandler({ reason: new Error('拒绝') } as PromiseRejectionEvent);
    expect(toastError).toHaveBeenCalledTimes(2);

    uninstall();
    expect(removeSpy).toHaveBeenCalledWith('error', errorHandler);
    expect(removeSpy).toHaveBeenCalledWith('unhandledrejection', rejectionHandler);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it('非 Error 的未捕获值使用统一文案且不外泄原始报文', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const uninstall = installErrorGuard();
    const errorHandler = addSpy.mock.calls.find((call) => call[0] === 'error')?.[1] as (
      event: ErrorEvent,
    ) => void;
    errorHandler({ message: 'raw stack trace' } as ErrorEvent);
    expect(toastError.mock.calls[0]?.[0]).toBe('页面刚刚遇到一个问题，请重试刚才的操作');
    expect(toastError.mock.calls[0]?.[0]).not.toContain('stack');
    uninstall();
    addSpy.mockRestore();
  });

  it('重复安装/重复卸载不抛错（卸载幂等）', () => {
    const uninstallA = installErrorGuard();
    const uninstallB = installErrorGuard();
    uninstallA();
    uninstallB();
    expect(() => uninstallA()).not.toThrow();
  });
});
