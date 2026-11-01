/**
 * 前端补测第十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：能力档位目录 Hook 的加载/成功/失败/重试分支。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BusinessError } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { ProfileItem } from '@/lib/types';
import { act, renderHook } from '@/test/render-hook';
import { useModelCatalog } from './use-model-catalog';

vi.mock('@/lib/api-client', () => ({
  modelApi: { catalog: vi.fn() },
  BusinessError: class BusinessError extends Error {
    readonly code: number;
    constructor(code: number, message: string) {
      super(message);
      this.code = code;
    }
  },
}));

import { modelApi } from '@/lib/api-client';

const PROFILES: ProfileItem[] = [
  {
    profile: 'general',
    display_name: '通用',
    description: '日常问答',
    max_context_turns: 20,
    status: 'available',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useModelCatalog', () => {
  it('初始处于加载中、空档位', () => {
    const { result, unmount } = renderHook(() => useModelCatalog());
    expect(result.current.loading).toBe(true);
    expect(result.current.profiles).toEqual([]);
    expect(result.current.errorMessage).toBe('');
    unmount();
  });

  it('reload 成功后写入档位并结束加载', async () => {
    vi.mocked(modelApi.catalog).mockResolvedValue({ profiles: PROFILES });
    const { result, unmount } = renderHook(() => useModelCatalog());

    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.profiles).toEqual(PROFILES);
    expect(result.current.errorMessage).toBe('');
    unmount();
  });

  it('失败时以业务语言文案提示', async () => {
    const error = new BusinessError(10001, '服务开小差了');
    vi.mocked(modelApi.catalog).mockRejectedValue(error);
    const { result, unmount } = renderHook(() => useModelCatalog());

    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.errorMessage).toBe(toBusinessMessage(error));
    unmount();
  });

  it('失败后重试成功会清掉错误提示', async () => {
    vi.mocked(modelApi.catalog).mockRejectedValueOnce(new Error('网络抖动'));
    const { result, unmount } = renderHook(() => useModelCatalog());

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.errorMessage).not.toBe('');

    vi.mocked(modelApi.catalog).mockResolvedValue({ profiles: PROFILES });
    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.errorMessage).toBe('');
    expect(result.current.profiles).toEqual(PROFILES);
    unmount();
  });
});
