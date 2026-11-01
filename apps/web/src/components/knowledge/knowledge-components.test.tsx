/**
 * 前端补测第十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：知识库组件的渲染合同 —— DocumentTable 三态/双空态/删除二次确认/
 * 排序与筛选回调/权限置灰，DocumentSearch 防抖检索与四态展示。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { KnowledgeDocumentItem, SearchSegment } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import DocumentSearch from './document-search';
import { DocumentTable, type DocumentFilters } from './document-table';

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: { search: vi.fn() },
}));

import { knowledgeApi } from '@/lib/api-client';

const DOC: KnowledgeDocumentItem = {
  document_id: 'doc_1',
  file_name: '员工手册.pdf',
  file_size: 2048,
  uploader_name: '张三',
  uploaded_at: '2026-11-01T10:00:00Z',
  index_status: 'completed',
  stage: 'completed',
  stage_display_name: '可使用',
  progress: 100,
  estimated_remaining_seconds: null,
  segment_count: 12,
  error_message: null,
};

const NO_FILTERS: DocumentFilters = { keyword: '', stage: '', sort: 'uploaded_at:desc' };

interface TableOverrides {
  items?: KnowledgeDocumentItem[];
  loading?: boolean;
  errorMessage?: string;
  filters?: DocumentFilters;
  canManage?: boolean;
  onDelete?: (document: KnowledgeDocumentItem) => Promise<void>;
}

function renderTable(overrides: TableOverrides = {}): ReturnType<typeof renderComponent> {
  return renderComponent(
    <DocumentTable
      items={overrides.items ?? [DOC]}
      total={1}
      page={1}
      pageSize={20}
      loading={overrides.loading ?? false}
      errorMessage={overrides.errorMessage ?? ''}
      filters={overrides.filters ?? NO_FILTERS}
      onFiltersChange={vi.fn()}
      onPageChange={vi.fn()}
      onPageSizeChange={vi.fn()}
      onRetry={vi.fn()}
      onDelete={overrides.onDelete ?? vi.fn(async () => undefined)}
      canManage={overrides.canManage ?? true}
    />,
  );
}

function clickButton(container: ParentNode, label: string): void {
  const button = Array.from(container.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(label),
  );
  if (!button) {
    throw new Error(`按钮未找到：${label}`);
  }
  act(() => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------
// DocumentTable
// ---------------------------------------------------------------------------

describe('DocumentTable · 三态与空态', () => {
  it('加载中渲染骨架，不渲染数据行', () => {
    const { container, unmount } = renderTable({ loading: true });
    expect(container.textContent).not.toContain('员工手册.pdf');
    unmount();
  });

  it('出错时展示业务文案与重试入口', () => {
    const onRetry = vi.fn();
    const { container, unmount } = renderComponent(
      <DocumentTable
        items={[]}
        total={0}
        page={1}
        pageSize={20}
        loading={false}
        errorMessage="文档服务暂时不可用"
        filters={NO_FILTERS}
        onFiltersChange={vi.fn()}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        onRetry={onRetry}
        onDelete={vi.fn()}
        canManage
      />,
    );
    expect(container.textContent).toContain('文档服务暂时不可用');
    clickButton(container, '重试');
    expect(onRetry).toHaveBeenCalled();
    unmount();
  });

  it('无筛选的空数据引导上传', () => {
    const { container, unmount } = renderTable({ items: [] });
    expect(container.textContent).toContain('还没有上传文档');
    unmount();
  });

  it('筛选无结果时提示清空筛选', () => {
    const { container, unmount } = renderTable({
      items: [],
      filters: { ...NO_FILTERS, keyword: '合同' },
    });
    expect(container.textContent).toContain('没有符合条件的文档');
    unmount();
  });
});

describe('DocumentTable · 行渲染', () => {
  it('展示文档名 / 大小 / 片段数 / 上传人', () => {
    const { container, unmount } = renderTable();
    expect(container.textContent).toContain('员工手册.pdf');
    expect(container.textContent).toContain('张三');
    expect(container.textContent).toContain('可使用');
    unmount();
  });

  it('上传人缺失时占位「—」', () => {
    const { container, unmount } = renderTable({
      items: [{ ...DOC, uploader_name: '' }],
    });
    expect(container.textContent).toContain('—');
    unmount();
  });

  it('处理中展示进度条与阶段文案', () => {
    const { container, unmount } = renderTable({
      items: [
        {
          ...DOC,
          index_status: 'processing',
          stage: 'parsing',
          stage_display_name: '正在读取内容',
          progress: 30,
        },
      ],
    });
    expect(container.textContent).toContain('正在读取内容');
    expect(container.querySelector('[aria-label="文档处理进度"]')).not.toBeNull();
    unmount();
  });

  it('处理失败时展示原因', () => {
    const { container, unmount } = renderTable({
      items: [
        {
          ...DOC,
          index_status: 'failed',
          stage: 'failed',
          stage_display_name: '处理失败',
          error_message: '文档已加密，无法读取',
        },
      ],
    });
    expect(container.textContent).toContain('文档已加密，无法读取');
    unmount();
  });
});

describe('DocumentTable · 权限与删除', () => {
  it('无管理权限时删除置灰', () => {
    const { container, unmount } = renderTable({ canManage: false });
    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="删除文档 员工手册.pdf"]',
    );
    expect(button?.disabled).toBe(true);
    unmount();
  });

  it('删除需二次确认后才调用删除', async () => {
    const onDelete = vi.fn(async () => undefined);
    const { container, unmount } = renderTable({ onDelete });

    // 第一步：点行内删除，弹确认框
    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="删除文档 员工手册.pdf"]',
    );
    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.textContent).toContain('删除这份文档？');
    expect(onDelete).not.toHaveBeenCalled();

    // 第二步：在确认框内确认删除（行内也有同名按钮，必须限定 dialog 范围）
    const dialog = document.body.querySelector('[role="dialog"]');
    await act(async () => {
      clickButton(dialog ?? document.body, '删除');
    });
    expect(onDelete).toHaveBeenCalledWith(DOC);
    unmount();
  });
});

describe('DocumentTable · 筛选与排序', () => {
  it('关键字输入触发筛选变更', () => {
    const onFiltersChange = vi.fn();
    const { container, unmount } = renderComponent(
      <DocumentTable
        items={[DOC]}
        total={1}
        page={1}
        pageSize={20}
        loading={false}
        errorMessage=""
        filters={NO_FILTERS}
        onFiltersChange={onFiltersChange}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
        canManage
      />,
    );
    const input = container.querySelector<HTMLInputElement>('input[aria-label="搜索文档名称"]');
    act(() => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '手册');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    expect(onFiltersChange).toHaveBeenCalledWith(expect.objectContaining({ keyword: '手册' }));
    unmount();
  });

  it('上传时间排序在升降序间切换', () => {
    const onFiltersChange = vi.fn();
    const { container, unmount } = renderComponent(
      <DocumentTable
        items={[DOC]}
        total={1}
        page={1}
        pageSize={20}
        loading={false}
        errorMessage=""
        filters={{ ...NO_FILTERS, sort: 'uploaded_at:desc' }}
        onFiltersChange={onFiltersChange}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
        canManage
      />,
    );
    clickButton(container, '上传时间');
    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'uploaded_at:asc' }),
    );
    unmount();
  });
});

// ---------------------------------------------------------------------------
// DocumentSearch
// ---------------------------------------------------------------------------

const SEGMENT: SearchSegment = {
  document_id: 'doc_1',
  document_name: '员工手册.pdf',
  segment_content: '年假按工龄计算',
  score: 0.86,
  position: null,
};

describe('DocumentSearch', () => {
  it('输入防抖后发起检索（400ms）', async () => {
    vi.useFakeTimers();
    vi.mocked(knowledgeApi.search).mockResolvedValue({ segments: [SEGMENT] });
    const { container, unmount } = renderComponent(<DocumentSearch baseId="kb_1" />);

    const input = container.querySelector<HTMLInputElement>('input[type="search"]');
    act(() => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '年假');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    // 防抖窗口内不发起
    expect(knowledgeApi.search).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(knowledgeApi.search).toHaveBeenCalledWith(
      expect.objectContaining({ base_id: 'kb_1', query: '年假' }),
    );
    unmount();
    vi.useRealTimers();
  });

  it('检索结果展示文档名 / 内容 / 相关度', async () => {
    vi.useFakeTimers();
    vi.mocked(knowledgeApi.search).mockResolvedValue({ segments: [SEGMENT] });
    const { container, unmount } = renderComponent(<DocumentSearch baseId="kb_1" />);

    const input = container.querySelector<HTMLInputElement>('input[type="search"]');
    await act(async () => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '年假');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        await vi.advanceTimersByTimeAsync(400);
      }
    });
    expect(container.textContent).toContain('员工手册.pdf');
    expect(container.textContent).toContain('年假按工龄计算');
    expect(container.textContent).toContain('相关度 86%');
    unmount();
    vi.useRealTimers();
  });

  it('无结果时提示换关键词', async () => {
    vi.useFakeTimers();
    vi.mocked(knowledgeApi.search).mockResolvedValue({ segments: [] });
    const { container, unmount } = renderComponent(<DocumentSearch baseId="kb_1" />);

    const input = container.querySelector<HTMLInputElement>('input[type="search"]');
    act(() => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '不存在的词');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(container.textContent).toContain('没有找到相关内容');
    unmount();
    vi.useRealTimers();
  });

  it('检索失败以业务文案提示并可重试', async () => {
    vi.useFakeTimers();
    vi.mocked(knowledgeApi.search).mockRejectedValue(new Error('检索服务不可用'));
    const { container, unmount } = renderComponent(<DocumentSearch baseId="kb_1" />);

    const input = container.querySelector<HTMLInputElement>('input[type="search"]');
    act(() => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '年假');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(container.textContent).toContain('重试');
    unmount();
    vi.useRealTimers();
  });

  it('空关键字不发起检索', async () => {
    vi.useFakeTimers();
    const { container, unmount } = renderComponent(<DocumentSearch baseId="kb_1" />);
    const input = container.querySelector<HTMLInputElement>('input[type="search"]');
    await act(async () => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '   ');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        await vi.advanceTimersByTimeAsync(400);
      }
    });
    expect(knowledgeApi.search).not.toHaveBeenCalled();
    unmount();
    vi.useRealTimers();
  });
});
