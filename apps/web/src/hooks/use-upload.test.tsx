/**
 * 前端补测第十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：文档上传 Hook 的本地预校验（格式/大小）、上传流程、
 * 索引轮询推进与停止条件、错误降级。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { toBusinessMessage } from '@/lib/business-errors';
import type { IndexStatus } from '@/lib/types';
import { act, renderHook } from '@/test/render-hook';
import { useUpload } from './use-upload';

// 部分 mock：保留 BusinessError 等真实导出（business-errors 依赖它做 instanceof），
// 只替换走网络的 knowledgeApi
vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: {
    uploadDocument: vi.fn(),
    documentStatus: vi.fn(),
  },
}));

import { knowledgeApi } from '@/lib/api-client';

/** 构造指定大小的测试文件（不真实占用内存） */
function fileOf(name: string, size: number): File {
  const file = new File(['x'], name);
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

const MAX_SIZE = 50 * 1024 * 1024;

/** 上传成功的初始响应 */
const CREATED = {
  document_id: 'doc_1',
  file_name: '笔记.md',
  index_status: 'processing' as IndexStatus,
  progress: 10,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useUpload · 本地预校验', () => {
  it('初始状态', () => {
    const { result, unmount } = renderHook(() => useUpload());
    expect(result.current.uploading).toBe(false);
    expect(result.current.fileName).toBe('');
    expect(result.current.progress).toBe(0);
    expect(result.current.errorMessage).toBe('');
    unmount();
  });

  it('扩展名不在白名单时拒绝并提示换用格式', async () => {
    const { result, unmount } = renderHook(() => useUpload());
    await act(async () => {
      await result.current.upload('kb_1', fileOf('恶意.exe', 1024));
    });
    expect(result.current.errorMessage).toContain('PDF');
    expect(knowledgeApi.uploadDocument).not.toHaveBeenCalled();
    unmount();
  });

  it('无扩展名的文件同样拒绝', async () => {
    const { result, unmount } = renderHook(() => useUpload());
    await act(async () => {
      await result.current.upload('kb_1', fileOf('README', 1024));
    });
    expect(result.current.errorMessage).not.toBe('');
    expect(knowledgeApi.uploadDocument).not.toHaveBeenCalled();
    unmount();
  });

  it('超过 50MB 上限时拒绝', async () => {
    const { result, unmount } = renderHook(() => useUpload());
    await act(async () => {
      await result.current.upload('kb_1', fileOf('big.pdf', MAX_SIZE + 1));
    });
    expect(result.current.errorMessage).toContain('50MB');
    expect(knowledgeApi.uploadDocument).not.toHaveBeenCalled();
    unmount();
  });

  it('恰好 50MB 允许上传', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    const { result, unmount } = renderHook(() => useUpload());
    await act(async () => {
      await result.current.upload('kb_1', fileOf('edge.pdf', MAX_SIZE));
    });
    expect(knowledgeApi.uploadDocument).toHaveBeenCalled();
    unmount();
  });
});

describe('useUpload · 上传与轮询', () => {
  it('上传成功后记录文档并进入轮询', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    vi.mocked(knowledgeApi.documentStatus).mockResolvedValue({
      document_id: 'doc_1',
      index_status: 'processing',
      progress: 40,
      segment_count: 0,
      error_message: null,
    });
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('笔记.md', 1024));
    });
    expect(result.current.documentId).toBe('doc_1');
    expect(result.current.uploading).toBe(false);
    expect(result.current.progress).toBe(10);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(knowledgeApi.documentStatus).toHaveBeenCalledWith('kb_1', 'doc_1');
    expect(result.current.progress).toBe(40);
    unmount();
  });

  it('索引完成后停止轮询', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    vi.mocked(knowledgeApi.documentStatus).mockResolvedValue({
      document_id: 'doc_1',
      index_status: 'completed',
      progress: 100,
      segment_count: 5,
      error_message: null,
    });
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('笔记.md', 1024));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(result.current.indexStatus).toBe('completed');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    // 完成后不再发起新的进度查询
    expect(knowledgeApi.documentStatus).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('索引失败时展示错误并停止轮询', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    vi.mocked(knowledgeApi.documentStatus).mockResolvedValue({
      document_id: 'doc_1',
      index_status: 'failed',
      progress: 30,
      segment_count: 0,
      error_message: '文档解析失败',
    });
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('笔记.md', 1024));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(result.current.errorMessage).toBe('文档解析失败');
    expect(result.current.indexStatus).toBe('failed');
    unmount();
  });

  it('进度查询失败时以业务文案提示并停止轮询', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    vi.mocked(knowledgeApi.documentStatus).mockRejectedValue(new Error('网络断开'));
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('笔记.md', 1024));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(result.current.errorMessage).toBe(toBusinessMessage(new Error('网络断开')));
    unmount();
  });

  it('上传失败时以业务文案提示并保留文件名', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockRejectedValue(new Error('上传中断'));
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('年报.pdf', 2048));
    });
    expect(result.current.errorMessage).toBe(toBusinessMessage(new Error('上传中断')));
    expect(result.current.fileName).toBe('年报.pdf');
    expect(result.current.uploading).toBe(false);
    unmount();
  });

  it('reset 回到初始状态', async () => {
    vi.mocked(knowledgeApi.uploadDocument).mockResolvedValue(CREATED);
    const { result, unmount } = renderHook(() => useUpload());

    await act(async () => {
      await result.current.upload('kb_1', fileOf('笔记.md', 1024));
    });
    act(() => {
      result.current.reset();
    });
    expect(result.current.documentId).toBe('');
    expect(result.current.fileName).toBe('');
    expect(result.current.progress).toBe(0);
    unmount();
  });
});
