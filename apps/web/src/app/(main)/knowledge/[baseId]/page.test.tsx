/**
 * 前端补测第二十八批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：知识库详情页（P-01）的编排合同 ——
 * 三态与「列表里找不到 = 已删除」口径、我的权限对设置/删除/上传的锁定、
 * 上传本地校验（格式白名单与 50MB 上限）、上传成功/失败提示、
 * 删除文档与删除知识库二次确认（文档数说明与转出口径）、基本信息标签。
 *
 * BaseSettingsDialog / DocumentTable / GrantPanel 的内部合同已由补测
 * 第十五、十八、十九批覆盖，本批用最小替身回显关键 props 并回放回调，
 * 只测页面自身的编排与权限分流。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { messageOf } from '@/lib/business-errors';
import { act, renderComponent } from '@/test/render-hook';

import KnowledgeBaseDetailPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  push: vi.fn(),
  listBases: vi.fn(),
  uploadDocument: vi.fn(),
  deleteDocument: vi.fn(),
  listDocuments: vi.fn(),
  deleteBase: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ baseId: 'kb_1' }),
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: {
    listBases: mocks.listBases,
    uploadDocument: mocks.uploadDocument,
    deleteDocument: mocks.deleteDocument,
  },
  knowledgeExtApi: {
    listDocuments: mocks.listDocuments,
    deleteBase: mocks.deleteBase,
  },
}));

// 子组件用最小替身：回显权限口径并回放回调
vi.mock('@/components/knowledge/base-settings-dialog', async () => {
  const react = await import('react');
  return {
    BaseSettingsDialog: (props: { open: boolean; canEdit: boolean }) =>
      props.open
        ? react.createElement('div', null, `设置弹窗|可编辑${String(props.canEdit)}`)
        : null,
  };
});

vi.mock('@/components/knowledge/document-table', async () => {
  const react = await import('react');
  return {
    DocumentTable: (props: {
      items: unknown[];
      total: number;
      canManage: boolean;
      onDelete: (document: { document_id: string }) => Promise<void>;
    }) =>
      react.createElement(
        'div',
        null,
        `文档表${props.items.length}/${props.total}|可管理${String(props.canManage)}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => void props.onDelete({ document_id: 'doc_1' }) },
          '模拟删除文档',
        ),
      ),
  };
});

vi.mock('@/components/knowledge/grant-panel', async () => {
  const react = await import('react');
  return {
    GrantPanel: (props: { canManage: boolean }) =>
      react.createElement('div', null, `授权面板|可管理${String(props.canManage)}`),
  };
});

afterEach(() => {
  document.body.innerHTML = '';
});

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

/**
 * 等异步链路的 DOM 落盘（act 窗口内睡眠、窗口外断言；
 * 口径见补测第二十七批 waitForText）。
 */
async function waitForText(root: ParentNode, text: string): Promise<void> {
  for (let tick = 0; tick < 60; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    if (root.textContent?.includes(text)) return;
  }
  expect(root.textContent).toContain(text);
}

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
}

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找（页面上也有「删除」） */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

const BASE = {
  base_id: 'kb_1',
  name: '人事制度库',
  description: '员工手册与假期制度',
  visibility: 'organization',
  document_count: 3,
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-31T10:00:00Z',
  chunk_policy: 'fine',
  my_role: 'owner',
};

const DOCUMENTS = {
  items: [
    {
      document_id: 'doc_1',
      file_name: '员工手册.pdf',
      index_status: 'completed',
      progress: 100,
      segment_count: 12,
      error_message: '',
    },
  ],
  meta: { page: 1, page_size: 20, total: 1, total_pages: 1 },
};

/** 显式传 null 表示「空体」 */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

function mockBaseData(overrides: { base?: unknown; documents?: unknown } = {}): void {
  // 详情页复用 R2 列表定位单库：列表里没有 = 已删除（11001 口径）
  const base = pick('base', BASE, overrides);
  mocks.listBases.mockResolvedValue({
    items: base ? [base] : [],
    meta: { page: 1, page_size: 200, total: base ? 1 : 0, total_pages: 1 },
  });
  mocks.listDocuments.mockResolvedValue(pick('documents', DOCUMENTS, overrides));
}

describe('知识库详情页', () => {
  it('加载中给提示；列表里找不到按已删除口径提示', async () => {
    mockBaseData();
    mocks.listBases.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<KnowledgeBaseDetailPage />);
    expect(loading.container.textContent).toContain('正在加载知识库…');
    loading.unmount();

    mockBaseData({ base: null });
    const gone = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(gone.container, messageOf(11001));
    // 返回入口始终在
    click(findButton(gone.container, '返回知识库列表'));
    expect(mocks.push).toHaveBeenCalledWith('/knowledge');
    gone.unmount();
  });

  it('详情加载失败可原地重试', async () => {
    mockBaseData();
    mocks.listBases.mockRejectedValue(new Error('知识库服务暂时不可用'));
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);

    await waitForText(container, '知识库服务暂时不可用');
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listBases).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('所有者全权：设置与删除可操作', async () => {
    mockBaseData();
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    expect(container.textContent).toContain('我的权限：所有者');
    expect(findButton(container, '设置')?.disabled).toBe(false);
    expect(findButton(container, '删除')?.disabled).toBe(false);

    click(findButton(container, '设置'));
    await waitForText(container, '设置弹窗|可编辑true');
    unmount();
  });

  it('只读成员锁定设置/删除/上传', async () => {
    mockBaseData({ base: { ...BASE, my_role: 'viewer' } });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    expect(container.textContent).toContain('我的权限：可提问');
    expect(findButton(container, '设置')?.disabled).toBe(true);
    expect(findButton(container, '删除')?.disabled).toBe(true);
    // 上传入口禁用
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput?.disabled).toBe(true);
    unmount();
  });

  it('上传前本地校验格式与大小', async () => {
    mockBaseData();
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');

    // 格式不在白名单
    act(() => {
      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input?.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(mocks.toast.error).toHaveBeenCalledWith(
      'photo.png 的格式无法处理，请另存为 PDF、Word、Excel、Markdown 或 TXT 后重试',
    );
    expect(mocks.uploadDocument).not.toHaveBeenCalled();

    // 超出 50MB
    act(() => {
      const file = new File(['x'], 'big.pdf', { type: 'application/pdf' });
      Object.defineProperty(file, 'size', { value: 60 * 1024 * 1024 });
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input?.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(mocks.toast.error).toHaveBeenCalledWith('big.pdf 超出大小限制（上限 50MB）');
    expect(mocks.uploadDocument).not.toHaveBeenCalled();
    unmount();
  });

  it('上传成功给处理中提示并刷新清单', async () => {
    mockBaseData();
    mocks.uploadDocument.mockResolvedValue({
      document_id: 'doc_9',
      file_name: '手册.pdf',
      index_status: 'processing',
      progress: 0,
    });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    act(() => {
      const file = new File(['x'], '手册.pdf', { type: 'application/pdf' });
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input?.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('手册.pdf 已上传，正在读取内容');
      });
    });
    expect(mocks.uploadDocument).toHaveBeenCalledWith('kb_1', expect.any(File));
    unmount();
  });

  it('上传失败以业务文案提示', async () => {
    mockBaseData();
    mocks.uploadDocument.mockRejectedValue(new Error('文件无法解析'));
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    act(() => {
      const file = new File(['x'], '手册.pdf', { type: 'application/pdf' });
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input?.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('文件无法解析');
      });
    });
    unmount();
  });

  it('删除文档走清单回调', async () => {
    mockBaseData();
    mocks.deleteDocument.mockResolvedValue({ document_id: 'doc_1', deleted: true });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '文档表1/1');

    click(findButton(container, '模拟删除文档'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.deleteDocument).toHaveBeenCalledWith('kb_1', 'doc_1');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('文档已删除');
      });
    });
    unmount();
  });

  it('删除知识库二次确认并说明文档处置', async () => {
    mockBaseData();
    mocks.deleteBase.mockResolvedValue({ base_id: 'kb_1', deleted_document_count: 3 });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    click(findButton(container, '删除'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('删除这个知识库？');
      });
    });
    expect(document.body.textContent).toContain('删除后库内文档将一并移除，且无法恢复');
    expect(document.body.textContent).toContain('3');
    click(dialogButton('删除知识库'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.deleteBase).toHaveBeenCalledWith('kb_1');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('知识库已删除，同时移除 3 份文档');
      });
    });
    // 删除后回到列表页
    expect(mocks.push).toHaveBeenCalledWith('/knowledge');
    unmount();
  });

  it('基本信息标签展示切分口径与时间', async () => {
    mockBaseData({
      base: { ...BASE, chunk_policy: 'default', updated_at: undefined, description: '' },
    });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    click(findButton(container, '基本信息'));
    await waitForText(container, '内容切分精细度');
    expect(container.textContent).toContain('标准（处理更快）');
    expect(container.textContent).toContain('还没有填写描述');
    // 无更新时间回落「—」
    expect(container.textContent).toContain('最近更新');
    unmount();
  });

  it('成员授权标签把管理权交给授权面板', async () => {
    mockBaseData({ base: { ...BASE, my_role: 'owner' } });
    const { container, unmount } = renderWithClient(<KnowledgeBaseDetailPage />);
    await waitForText(container, '人事制度库');

    click(findButton(container, '成员授权'));
    await waitForText(container, '授权面板|可管理true');
    unmount();
  });
});
