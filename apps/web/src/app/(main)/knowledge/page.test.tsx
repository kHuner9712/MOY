/**
 * 前端补测第二十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：知识库列表页（P-06 深化）的渲染与写入合同 ——
 * 列表三态、卡片四类徽标（可见范围 / 我的权限 / 切分精细度 / 文档数）、
 * 新建弹窗的名称校验、参数口径（snake_case）与失败降级。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import KnowledgePage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  listBases: vi.fn(),
  createBase: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeApi: { listBases: mocks.listBases, createBase: mocks.createBase },
}));

// next/link 依赖 App Router 上下文，jsdom 里没有；换成渲染 <a> 的最小替身，
// href 合同（导航去向）仍然可断言。
vi.mock('next/link', async () => {
  const react = await import('react');
  return {
    default: ({ href, children }: { href: string; children?: unknown }) =>
      react.createElement('a', { href }, children as never),
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

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
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

/** 受控下拉换值（原生 select） */
function setSelectValue(select: HTMLSelectElement | null | undefined, value: string): void {
  if (!select) return;
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

/** 提交新建表单（jsdom 点提交按钮不一定触发表单提交，直接发 submit 事件） */
function submitCreateForm(): void {
  act(() => {
    document.body
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

const BASE = {
  base_id: 'kb_1',
  name: '人事制度库',
  description: '员工手册、考勤与假期制度',
  visibility: 'organization',
  document_count: 1234,
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-31T10:00:00Z',
  chunk_policy: 'fine',
  my_role: 'owner',
};

describe('知识库列表页', () => {
  it('加载中给读取提示', () => {
    mocks.listBases.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<KnowledgePage />);
    expect(container.textContent).toContain('正在加载知识库…');
    unmount();
  });

  it('加载失败给业务文案并可原地重试', async () => {
    mocks.listBases.mockRejectedValue(new Error('知识库服务暂时不可用'));
    const { container, unmount } = renderWithClient(<KnowledgePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('知识库服务暂时不可用');
      });
    });
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listBases).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('还没有知识库时空态引导新建', async () => {
    mocks.listBases.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 100, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<KnowledgePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有知识库');
      });
    });
    expect(container.textContent).toContain('创建第一个知识库并上传资料');

    click(findButton(container, '新建知识库'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('新建知识库');
      });
    });
    expect(document.body.textContent).toContain('回答时会自动标注引用来源');
    unmount();
  });

  it('搜索无结果给筛选空态，清空搜索回到全量空态', async () => {
    mocks.listBases.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 100, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<KnowledgePage />);

    typeInto(container, 'input[aria-label="搜索知识库名称"]', '制度');
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listBases).toHaveBeenCalledWith({
          page: 1,
          page_size: 100,
          keyword: '制度',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('没有符合条件的知识库');
      });
    });

    click(findButton(container, '清空搜索'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有知识库');
      });
    });
    unmount();
  });

  it('卡片四类徽标按业务语言展示，不暴露内部标识', async () => {
    mocks.listBases.mockResolvedValue({
      items: [
        BASE,
        {
          ...BASE,
          base_id: 'kb_2',
          name: '合同范本库',
          description: '',
          visibility: 'private',
          document_count: 0,
          created_at: '2026-10-02T10:00:00Z',
          updated_at: undefined,
          chunk_policy: 'default',
          my_role: undefined,
        },
      ],
      meta: { page: 1, page_size: 100, total: 2, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<KnowledgePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('人事制度库');
      });
    });
    // 我的权限：映射成「能做什么」，未知角色回落「可提问」
    expect(container.textContent).toContain('所有者');
    expect(container.textContent).toContain('可提问');
    // 切分精细度只说效果，不说参数
    expect(container.textContent).toContain('精细切分');
    expect(container.textContent).toContain('标准切分');
    // 文档数与描述占位
    expect(container.textContent).toContain('1,234 份文档');
    expect(container.textContent).toContain('还没有填写描述');
    // 无更新时间时回落创建时间
    expect(container.textContent).toContain('更新于 2026-10-02');
    // 任何内部标识不得上屏
    expect(container.textContent).not.toContain('owner');
    expect(container.textContent).not.toContain('chunk_policy');

    const enter = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('进入'),
    );
    expect(enter?.getAttribute('href')).toBe('/knowledge/kb_1');
    unmount();
  });

  it('名称为空给校验提示，不发创建请求', async () => {
    mocks.listBases.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 100, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<KnowledgePage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有知识库');
      });
    });
    click(findButton(container, '新建知识库'));

    typeInto(document.body, '#new-base-name', '   ');
    submitCreateForm();
    await act(async () => {});
    expect(document.body.textContent).toContain('请填写知识库名称');
    expect(mocks.createBase).not.toHaveBeenCalled();
    unmount();
  });

  it('创建按后端口径提交并重置表单', async () => {
    mocks.listBases.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 100, total: 0, total_pages: 1 },
    });
    mocks.createBase.mockResolvedValue({
      base_id: 'kb_9',
      name: '人事制度库',
      created_at: '2026-11-01T10:00:00Z',
    });
    const { container, unmount } = renderWithClient(<KnowledgePage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有知识库');
      });
    });
    click(findButton(container, '新建知识库'));

    typeInto(document.body, '#new-base-name', '  人事制度库  ');
    typeInto(document.body, '#new-base-description', '员工手册与假期制度');
    setSelectValue(
      document.body.querySelector<HTMLSelectElement>('#new-base-visibility'),
      'restricted',
    );
    setSelectValue(document.body.querySelector<HTMLSelectElement>('#new-base-chunk'), 'fine');
    submitCreateForm();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createBase).toHaveBeenCalledWith({
          name: '人事制度库',
          description: '员工手册与假期制度',
          chunk_policy: 'fine',
          visibility: 'restricted',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('知识库已创建，可以上传资料了');
      });
    });

    // 弹窗关闭并重置表单：重开后各字段回到默认值
    expect(document.body.querySelector('form')).toBeNull();
    click(findButton(container, '新建知识库'));
    const nameInput = document.body.querySelector<HTMLInputElement>('#new-base-name');
    expect(nameInput?.value).toBe('');
    const chunkSelect = document.body.querySelector<HTMLSelectElement>('#new-base-chunk');
    expect(chunkSelect?.value).toBe('default');
    unmount();
  });

  it('创建失败以业务文案提示且弹窗不关', async () => {
    mocks.listBases.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 100, total: 0, total_pages: 1 },
    });
    mocks.createBase.mockRejectedValue(new Error('知识库名称已存在'));
    const { container, unmount } = renderWithClient(<KnowledgePage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有知识库');
      });
    });
    click(findButton(container, '新建知识库'));

    typeInto(document.body, '#new-base-name', '人事制度库');
    submitCreateForm();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('知识库名称已存在');
      });
    });
    // 失败留在弹窗里，用户可原地改名重试
    expect(document.body.textContent).toContain('新建知识库');
    unmount();
  });
});
