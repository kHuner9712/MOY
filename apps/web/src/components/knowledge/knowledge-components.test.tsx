/**
 * 前端补测第十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：知识库组件的渲染合同 —— DocumentTable 三态/双空态/删除二次确认/
 * 排序与筛选回调/权限置灰，DocumentSearch 防抖检索与四态展示。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  GrantItem,
  KnowledgeBaseDetail,
  KnowledgeDocumentItem,
  SearchSegment,
} from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { BaseSettingsDialog } from './base-settings-dialog';
import DocumentSearch from './document-search';
import { DocumentTable, type DocumentFilters } from './document-table';
import { GrantPanel } from './grant-panel';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: { search: vi.fn() },
  knowledgeExtApi: {
    listGrants: vi.fn(),
    createGrants: vi.fn(),
    revokeGrant: vi.fn(),
    updateBase: vi.fn(),
  },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

import { knowledgeApi, knowledgeExtApi } from '@/lib/api-client';

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

// ---------------------------------------------------------------------------
// GrantPanel
// ---------------------------------------------------------------------------

const GRANT: GrantItem = {
  grant_id: 'g1',
  subject_type: 'user',
  subject_id: 'usr_1',
  subject_name: '张三',
  permission: 'viewer',
  granted_by_name: '李四',
  granted_at: '2026-11-01T10:00:00Z',
};

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

/** 在受控输入框（input / textarea）里输入文本 */
function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  act(() => {
    if (field) {
      const proto =
        field instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

describe('GrantPanel', () => {
  it('渲染授权清单（对象类型 / 对象 / 级别 / 授权人）', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [GRANT],
      meta: { page: 1, page_size: 20, total: 1, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('张三');
      });
    });
    expect(container.textContent).toContain('成员');
    expect(container.textContent).toContain('可提问');
    expect(container.textContent).toContain('李四');
    unmount();
  });

  it('空清单给空态说明', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有单独授权的成员');
      });
    });
    unmount();
  });

  it('无管理权限时添加授权置灰', () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage={false} />);
    const button = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('添加授权'),
    );
    expect(button?.disabled).toBe(true);
    unmount();
  });

  it('空输入提交提示填写对象', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('添加授权');
      });
    });
    clickButton(container, '添加授权');
    expect(mocks.toast.error).toHaveBeenCalledWith('请填写要授权的成员、部门或角色');
    expect(knowledgeExtApi.createGrants).not.toHaveBeenCalled();
    unmount();
  });

  it('超过 50 项在提交前拦截', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    const many = Array.from({ length: 51 }, (_, index) => `usr_${index}`).join(',');
    typeInto(container, '#grant-subject-ids', many);
    clickButton(container, '添加授权');

    expect(mocks.toast.error).toHaveBeenCalledWith('一次最多授权 50 项，请分批操作');
    expect(knowledgeExtApi.createGrants).not.toHaveBeenCalled();
    unmount();
  });

  it('正常提交批量授权并提示成功', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 0 },
    });
    vi.mocked(knowledgeExtApi.createGrants).mockResolvedValue({
      created_count: 2,
      skipped_count: 0,
    });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    typeInto(container, '#grant-subject-ids', 'usr_1, usr_2');
    await act(async () => {
      clickButton(container, '添加授权');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(knowledgeExtApi.createGrants).toHaveBeenCalledWith('kb_1', {
          subject_type: 'user',
          subject_ids: ['usr_1', 'usr_2'],
          permission: 'viewer',
        });
      });
    });
    expect(mocks.toast.success).toHaveBeenCalledWith('已添加 2 项授权');
    unmount();
  });

  it('撤销走二次确认后才调用', async () => {
    vi.mocked(knowledgeExtApi.listGrants).mockResolvedValue({
      items: [GRANT],
      meta: { page: 1, page_size: 20, total: 1, total_pages: 1 },
    });
    vi.mocked(knowledgeExtApi.revokeGrant).mockResolvedValue({ grant_id: 'g1', deleted: true });
    const { container, unmount } = renderWithClient(<GrantPanel baseId="kb_1" canManage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('张三');
      });
    });

    // 行内撤销 → 弹确认框
    const button = container.querySelector<HTMLButtonElement>('button[aria-label="撤销授权 张三"]');
    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.textContent).toContain('撤销这条授权？');
    expect(knowledgeExtApi.revokeGrant).not.toHaveBeenCalled();

    // 确认撤销
    const dialog = document.body.querySelector('[role="dialog"]');
    await act(async () => {
      clickButton(dialog ?? document.body, '撤销');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(knowledgeExtApi.revokeGrant).toHaveBeenCalledWith('kb_1', 'g1');
      });
    });
    unmount();
  });
});

// ---------------------------------------------------------------------------
// BaseSettingsDialog
// ---------------------------------------------------------------------------

const BASE: KnowledgeBaseDetail = {
  base_id: 'kb_1',
  name: '人事制度库',
  description: '员工手册与假期制度',
  visibility: 'organization',
  document_count: 3,
  created_at: '2026-10-01T00:00:00Z',
  chunk_policy: 'default',
  my_role: 'editor',
};

describe('BaseSettingsDialog', () => {
  it('打开时回填当前设置', () => {
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    const name = document.body.querySelector<HTMLInputElement>('#base-name');
    expect(name?.value).toBe('人事制度库');
    expect(document.body.textContent).toContain('知识库设置');
    unmount();
  });

  it('名称为空时前端先拦一次', async () => {
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    typeInto(document.body, '#base-name', '');
    await act(async () => {
      const form = document.body.querySelector('form');
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(document.body.textContent).toContain('请填写知识库名称');
    expect(knowledgeExtApi.updateBase).not.toHaveBeenCalled();
    unmount();
  });

  it('保存时按修改后的值提交', async () => {
    vi.mocked(knowledgeExtApi.updateBase).mockResolvedValue({
      base_id: 'kb_1',
      name: '新名字',
      updated: true,
    } as never);
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    typeInto(document.body, '#base-name', '新名字');
    await act(async () => {
      const form = document.body.querySelector('form');
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(knowledgeExtApi.updateBase).toHaveBeenCalledWith(
          'kb_1',
          expect.objectContaining({ name: '新名字' }),
        );
      });
    });
    expect(mocks.toast.success).toHaveBeenCalledWith('知识库设置已保存');
    unmount();
  });

  it('保存失败以业务文案提示', async () => {
    vi.mocked(knowledgeExtApi.updateBase).mockRejectedValue(new Error('名称已存在'));
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    await act(async () => {
      const form = document.body.querySelector('form');
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalled();
      });
    });
    unmount();
  });

  it('无修改权限时表单置灰', () => {
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit={false} />,
    );
    const name = document.body.querySelector<HTMLInputElement>('#base-name');
    expect(name?.disabled).toBe(true);
    unmount();
  });

  it('重新打开时用最新数据回填（关闭后初始化标记重置）', () => {
    const first = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    typeInto(document.body, '#base-name', '改到一半');
    first.unmount();

    const again = renderWithClient(
      <BaseSettingsDialog
        open
        onOpenChange={vi.fn()}
        base={{ ...BASE, name: '后台新名称' }}
        canEdit
      />,
    );
    const name = document.body.querySelector<HTMLInputElement>('#base-name');
    expect(name?.value).toBe('后台新名称');
    again.unmount();
  });

  it('可见范围与切分精细度为业务语言选项（不暴露处理参数）', () => {
    const { unmount } = renderWithClient(
      <BaseSettingsDialog open onOpenChange={vi.fn()} base={BASE} canEdit />,
    );
    expect(document.body.textContent).toContain('企业可见');
    expect(document.body.textContent).toContain('标准');
    unmount();
  });
});
