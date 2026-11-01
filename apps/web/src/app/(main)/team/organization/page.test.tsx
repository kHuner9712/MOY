/**
 * 前端补测第三十批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：组织架构页（P-04）的编排合同 ——
 * 三态、默认选中首个一级部门与点选切换、新建/重命名表单校验与参数口径、
 * 拖拽移动（含上下文错误文案兜底）、删除二次确认（成员与下级数说明）、
 * 层级上限拦截、非管理者只读、角色与权限入口。
 *
 * DepartmentTree / RolePermissionDialog 的内部合同已由补测第十五、十九批
 * 覆盖，本批用最小替身回显关键 props 并回放回调，只测页面自身的编排。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import OrganizationPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  profile: vi.fn(),
  listDepartments: vi.fn(),
  createDepartment: vi.fn(),
  updateDepartment: vi.fn(),
  deleteDepartment: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

// next/link 依赖 App Router 上下文，jsdom 里没有；换成渲染 <a> 的最小替身。
vi.mock('next/link', async () => {
  const react = await import('react');
  return {
    default: ({ href, children }: { href: string; children?: unknown }) =>
      react.createElement('a', { href }, children as never),
  };
});

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  identityApi: { profile: mocks.profile },
  identityExtApi: {
    listDepartments: mocks.listDepartments,
    createDepartment: mocks.createDepartment,
    updateDepartment: mocks.updateDepartment,
    deleteDepartment: mocks.deleteDepartment,
  },
}));

// 子组件用最小替身：回显关键 props 并回放回调
vi.mock('@/components/org/department-tree', async () => {
  const react = await import('react');
  return {
    DepartmentTree: (props: {
      departments: Array<{ department_id: string; children: unknown[] }>;
      selectedId: string;
      canManage: boolean;
      onSelect: (node: { department_id: string }) => void;
      onCreateChild: (parent: unknown) => void;
      onMove: (departmentId: string, targetParentId: string) => void;
    }) =>
      react.createElement(
        'div',
        null,
        `部门树${props.departments.length}|选中${props.selectedId}|可管理${String(props.canManage)}`,
        react.createElement(
          'button',
          {
            type: 'button',
            onClick: () =>
              props.onSelect({ department_id: props.departments[0]?.children[0] ? 'd2' : 'd1' }),
          },
          '模拟选子部门',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onCreateChild(props.departments[0]) },
          '模拟建下级',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onMove('d2', 'd1') },
          '模拟移动',
        ),
      ),
  };
});

vi.mock('@/components/org/role-permission-dialog', async () => {
  const react = await import('react');
  return {
    RolePermissionDialog: (props: { open: boolean; canManage: boolean }) =>
      props.open
        ? react.createElement('div', null, `角色权限弹窗|可管理${String(props.canManage)}`)
        : null,
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

/** 等异步链路的 DOM 落盘（act 窗口内睡眠、窗口外断言） */
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

function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  act(() => {
    if (field) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

function submitForm(root: ParentNode): void {
  act(() => {
    root
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

const CHILD = {
  department_id: 'd2',
  name: '设计组',
  parent_id: 'd1',
  level: 2,
  path_names: ['产品部'],
  manager_name: '',
  member_count: 3,
  children: [],
};

const DEPT = {
  department_id: 'd1',
  name: '产品部',
  parent_id: '',
  level: 1,
  path_names: [],
  manager_name: '李四',
  member_count: 8,
  children: [CHILD],
};

const PROFILE = {
  user_id: 'u_1',
  display_name: '陈麦',
  email: 'chenmai@example.com',
  tenant: { tenant_id: 't_1', name: '华农科技', plan: 'professional' },
  organizations: [{ org_id: 'org_1', name: '华农科技', role: 'owner' }],
};

/** 显式传 null 表示「空体」 */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

function mockOrgData(overrides: { profile?: unknown; departments?: unknown } = {}): void {
  mocks.profile.mockResolvedValue(pick('profile', PROFILE, overrides));
  mocks.listDepartments.mockResolvedValue(pick('departments', { departments: [DEPT] }, overrides));
}

describe('组织架构页', () => {
  it('三态齐备：加载、失败可重试、未加入企业', async () => {
    mockOrgData();
    mocks.profile.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<OrganizationPage />);
    expect(loading.container.textContent).toContain('正在加载组织信息…');
    loading.unmount();

    mocks.profile.mockClear();
    mocks.profile.mockRejectedValue(new Error('组织信息暂时取不回来'));
    const failed = renderWithClient(<OrganizationPage />);
    await waitForText(failed.container, '组织信息暂时取不回来');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.profile).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mockOrgData({ profile: { ...PROFILE, organizations: [] } });
    const empty = renderWithClient(<OrganizationPage />);
    await waitForText(empty.container, '还没有加入任何企业');
    empty.unmount();
  });

  it('部门树三态：加载、失败可重试、空引导', async () => {
    mockOrgData();
    mocks.listDepartments.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<OrganizationPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(loading.container.textContent).toContain('正在加载部门…');
      });
    });
    loading.unmount();

    // 清掉上面悬挂调用的计数，只看失败渲染 + 重试
    mocks.listDepartments.mockClear();
    mockOrgData();
    mocks.listDepartments.mockRejectedValue(new Error('部门服务暂时不可用'));
    const failed = renderWithClient(<OrganizationPage />);
    await waitForText(failed.container, '部门服务暂时不可用');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listDepartments).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mockOrgData({ departments: { departments: [] } });
    const empty = renderWithClient(<OrganizationPage />);
    await waitForText(empty.container, '还没有部门');
    expect(empty.container.textContent).toContain('先建一个一级部门');
    empty.unmount();
  });

  it('默认选中首个一级部门，详情给层级与规模', async () => {
    mockOrgData();
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    expect(container.textContent).toContain('部门树1|选中d1|可管理true');
    expect(container.textContent).toContain('第 1 层 / 共 5 层');
    expect(container.textContent).toContain('李四');
    expect(container.textContent).toContain('8');
    expect(container.textContent).toContain('1 个');
    // 负责人缺失给「未设置」
    click(findButton(container, '模拟选子部门'));
    await waitForText(container, '设计组');
    expect(container.textContent).toContain('未设置');
    unmount();
  });

  it('非管理者整页只读', async () => {
    mockOrgData({
      profile: {
        ...PROFILE,
        organizations: [{ org_id: 'org_1', name: '华农科技', role: 'member' }],
      },
    });
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    expect(findButton(container, '角色与权限')?.disabled).toBe(true);
    expect(findButton(container, '新建一级部门')?.disabled).toBe(true);
    expect(findButton(container, '重命名')?.disabled).toBe(true);
    expect(findButton(container, '删除')?.disabled).toBe(true);
    unmount();
  });

  it('新建一级部门：名称校验拦截，成功后关闭弹窗', async () => {
    mockOrgData();
    mocks.createDepartment.mockResolvedValue({ department_id: 'd9', name: '研发部' });
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '新建一级部门'));
    await waitForText(document.body, '将创建一个一级部门。');

    submitForm(document.body);
    await act(async () => {});
    expect(document.body.textContent).toContain('请填写部门名称');
    expect(mocks.createDepartment).not.toHaveBeenCalled();

    typeInto(document.body, '#department-name', '  研发部  ');
    submitForm(document.body);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createDepartment).toHaveBeenCalledWith('org_1', {
          name: '研发部',
          parent_id: '',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('部门已创建');
      });
    });
    expect(document.body.querySelector('form')).toBeNull();
    unmount();
  });

  it('新建下级部门带上父节点，层级满 5 层拦截', async () => {
    mockOrgData();
    const deep = {
      ...DEPT,
      level: 5,
      children: [],
    };
    mockOrgData({ departments: { departments: [deep] } });
    mocks.createDepartment.mockResolvedValue({ department_id: 'd9', name: '小组' });
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    // 第 5 层不能再建下级
    expect(findButton(container, '新建下级部门')?.disabled).toBe(true);

    mockOrgData();
    const normal = renderWithClient(<OrganizationPage />);
    await waitForText(normal.container, '产品部');
    click(findButton(normal.container, '模拟建下级'));
    await waitForText(document.body, '将在「产品部」下创建下级部门。');
    typeInto(document.body, '#department-name', '小组');
    submitForm(document.body);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createDepartment).toHaveBeenCalledWith('org_1', {
          name: '小组',
          parent_id: 'd1',
        });
      });
    });
    normal.unmount();
    unmount();
  });

  it('重命名预填原名并只改名称', async () => {
    mockOrgData();
    mocks.updateDepartment.mockResolvedValue({ department_id: 'd1', name: '产品中心' });
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '重命名'));
    await waitForText(document.body, '重命名部门');
    expect(document.body.querySelector<HTMLInputElement>('#department-name')?.value).toBe('产品部');

    typeInto(document.body, '#department-name', '产品中心');
    submitForm(document.body);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateDepartment).toHaveBeenCalledWith('d1', { name: '产品中心' });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('部门名称已更新');
      });
    });
    unmount();
  });

  it('拖拽移动走上下文错误文案兜底', async () => {
    mockOrgData();
    mocks.updateDepartment.mockRejectedValue(new Error('不能移动到自己的子树'));
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '模拟移动'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateDepartment).toHaveBeenCalledWith('d2', { parent_id: 'd1' });
      });
    });
    // 移动冲突用「移动到子树」上下文文案，而不是「部门下仍有成员」
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('不能移动到自己的子树');
      });
    });
    unmount();
  });

  it('删除二次确认写明成员与下级数', async () => {
    mockOrgData();
    mocks.deleteDepartment.mockResolvedValue({ department_id: 'd1', deleted: true });
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '删除'));
    await waitForText(document.body, '删除这个部门？');
    expect(document.body.textContent).toContain('部门下仍有成员或子部门时无法删除');
    expect(document.body.textContent).toContain('成员 8 人，下级部门 1 个');
    click(dialogButton('删除'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.deleteDepartment).toHaveBeenCalledWith('d1');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('部门已删除');
      });
    });
    unmount();
  });

  it('删除失败用「非空」上下文文案', async () => {
    mockOrgData();
    mocks.deleteDepartment.mockRejectedValue(new Error('部门下仍有成员'));
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '删除'));
    await waitForText(document.body, '删除这个部门？');
    click(dialogButton('删除'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('部门下仍有成员');
      });
    });
    unmount();
  });

  it('角色与权限入口把管理权交给弹窗', async () => {
    mockOrgData();
    const { container, unmount } = renderWithClient(<OrganizationPage />);
    await waitForText(container, '产品部');

    click(findButton(container, '角色与权限'));
    await waitForText(container, '角色权限弹窗|可管理true');
    unmount();
  });
});
