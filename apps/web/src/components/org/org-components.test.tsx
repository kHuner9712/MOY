/**
 * 前端补测第十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：部门层级树的渲染合同 —— 选中与键盘可达、展开折叠、
 * 5 层上限置灰、权限与移动中的拖拽约束、拖拽移动回调。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DepartmentNode, PermissionGroup, RoleItem } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { DepartmentTree } from './department-tree';
import { RolePermissionDialog } from './role-permission-dialog';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  listPermissions: vi.fn(),
  listRoles: vi.fn(),
  upsertRole: vi.fn(),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  identityExtApi: {
    listPermissions: mocks.listPermissions,
    listRoles: mocks.listRoles,
  },
  identityApi: { upsertRole: mocks.upsertRole },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

import { identityApi } from '@/lib/api-client';

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

function node(partial: Partial<DepartmentNode> & { department_id: string }): DepartmentNode {
  return {
    name: '部门',
    parent_id: '',
    path_names: [],
    level: 1,
    manager_name: '',
    member_count: 0,
    children: [],
    ...partial,
  };
}

const TREE: DepartmentNode[] = [
  node({
    department_id: 'dep_root',
    name: '总公司',
    level: 1,
    member_count: 10,
    children: [
      node({
        department_id: 'dep_rnd',
        name: '研发部',
        parent_id: 'dep_root',
        level: 2,
        member_count: 5,
      }),
    ],
  }),
];

function treeItem(container: HTMLElement, name: string): HTMLElement {
  const item = Array.from(container.querySelectorAll('[role="treeitem"]')).find((el) =>
    el.textContent?.includes(name),
  );
  if (!item) {
    throw new Error(`树节点未找到：${name}`);
  }
  return item as HTMLElement;
}

/** 构造带 dataTransfer 的拖拽事件（jsdom 不提供 DragEvent 的数据对象） */
function fireDrag(el: Element, type: string): void {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: {
      setData: vi.fn(),
      getData: vi.fn(() => ''),
      effectAllowed: '',
      dropEffect: '',
    },
  });
  // act 包裹：拖拽状态（draggedId/dropTargetId）要先 flush 再进入下一次拖拽事件
  act(() => {
    el.dispatchEvent(event);
  });
}

interface TreeOverrides {
  selectedId?: string;
  canManage?: boolean;
  moving?: boolean;
  departments?: DepartmentNode[];
}

function renderTree(overrides: TreeOverrides = {}): {
  container: HTMLElement;
  unmount: () => void;
  onSelect: ReturnType<typeof vi.fn>;
  onCreateChild: ReturnType<typeof vi.fn>;
  onMove: ReturnType<typeof vi.fn>;
} {
  const onSelect = vi.fn();
  const onCreateChild = vi.fn();
  const onMove = vi.fn();
  const rendered = renderComponent(
    <DepartmentTree
      departments={overrides.departments ?? TREE}
      selectedId={overrides.selectedId ?? ''}
      onSelect={onSelect}
      onCreateChild={onCreateChild}
      onMove={onMove}
      canManage={overrides.canManage ?? true}
      moving={overrides.moving ?? false}
    />,
  );
  return { ...rendered, onSelect, onCreateChild, onMove };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DepartmentTree · 渲染与选中', () => {
  it('渲染树节点名称、层级与成员数', () => {
    const { container, unmount } = renderTree();
    expect(container.textContent).toContain('总公司');
    expect(container.textContent).toContain('研发部');
    expect(container.textContent).toContain('10');
    unmount();
  });

  it('点击节点回调选中', () => {
    const { container, onSelect, unmount } = renderTree();
    act(() => {
      treeItem(container, '研发部').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ department_id: 'dep_rnd' }));
    unmount();
  });

  it('选中节点标记 aria-selected', () => {
    const { container, unmount } = renderTree({ selectedId: 'dep_rnd' });
    expect(treeItem(container, '研发部').getAttribute('aria-selected')).toBe('true');
    expect(treeItem(container, '总公司').getAttribute('aria-selected')).toBe('false');
    unmount();
  });

  it('Enter 键可达选中（键盘无障碍）', () => {
    const { container, onSelect, unmount } = renderTree();
    act(() => {
      treeItem(container, '总公司').dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
      );
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ department_id: 'dep_root' }));
    unmount();
  });

  it('第一层有子部门的默认展开，可收起', () => {
    const { container, unmount } = renderTree();
    // 首次自动展开：子部门可见
    expect(container.textContent).toContain('研发部');

    const toggle = container.querySelector<HTMLButtonElement>('button[aria-label="收起下级部门"]');
    act(() => {
      toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.textContent).not.toContain('研发部');
    unmount();
  });
});

describe('DepartmentTree · 层级上限与权限', () => {
  it('第 5 层节点禁止新建子部门', () => {
    const deep = node({
      department_id: 'dep_deep',
      name: '五层部门',
      level: 5,
    });
    const { container, onCreateChild, unmount } = renderTree({ departments: [deep] });

    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="在 五层部门 下新建部门"]',
    );
    expect(button?.disabled).toBe(true);

    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onCreateChild).not.toHaveBeenCalled();
    unmount();
  });

  it('未满 5 层的可新建子部门', () => {
    const { container, onCreateChild, unmount } = renderTree();
    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="在 研发部 下新建部门"]',
    );
    expect(button?.disabled).toBe(false);

    act(() => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onCreateChild).toHaveBeenCalledWith(
      expect.objectContaining({ department_id: 'dep_rnd' }),
    );
    unmount();
  });

  it('无管理权限时新建置灰、不可拖拽', () => {
    const { container, unmount } = renderTree({ canManage: false });
    const button = container.querySelector<HTMLButtonElement>(
      'button[aria-label="在 研发部 下新建部门"]',
    );
    expect(button?.disabled).toBe(true);
    expect(treeItem(container, '研发部').getAttribute('draggable')).toBe('false');
    unmount();
  });

  it('移动执行期间禁用拖拽（防并发移动）', () => {
    const { container, unmount } = renderTree({ moving: true });
    expect(treeItem(container, '研发部').getAttribute('draggable')).toBe('false');
    unmount();
  });
});

describe('DepartmentTree · 拖拽移动', () => {
  it('拖到目标部门上触发移动回调', () => {
    const { container, onMove, unmount } = renderTree();

    fireDrag(treeItem(container, '研发部'), 'dragstart');
    fireDrag(treeItem(container, '总公司'), 'drop');

    expect(onMove).toHaveBeenCalledWith('dep_rnd', 'dep_root');
    unmount();
  });

  it('拖到自身不触发移动', () => {
    const { container, onMove, unmount } = renderTree();

    fireDrag(treeItem(container, '研发部'), 'dragstart');
    fireDrag(treeItem(container, '研发部'), 'drop');

    expect(onMove).not.toHaveBeenCalled();
    unmount();
  });

  it('无管理权限时拖放不触发移动', () => {
    const { container, onMove, unmount } = renderTree({ canManage: false });

    fireDrag(treeItem(container, '研发部'), 'dragstart');
    fireDrag(treeItem(container, '总公司'), 'drop');

    expect(onMove).not.toHaveBeenCalled();
    unmount();
  });
});

// ---------------------------------------------------------------------------
// RolePermissionDialog
// ---------------------------------------------------------------------------

const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    group: 'knowledge',
    group_name: '知识库',
    permissions: [
      { permission: 'knowledge:base:read', display_name: '查看知识库', description: '' },
      {
        permission: 'knowledge:base:write',
        display_name: '维护知识库',
        description: '上传与删除文档',
      },
    ],
  },
];

const ROLES: RoleItem[] = [
  {
    role_id: 'r_owner',
    role_key: 'owner',
    display_name: '所有者',
    is_builtin: true,
    member_count: 1,
    permissions: ['knowledge:base:read', 'knowledge:base:write'],
  },
  {
    role_id: 'r_member',
    role_key: 'member',
    display_name: '成员',
    is_builtin: true,
    member_count: 5,
    permissions: ['knowledge:base:read'],
  },
];

describe('RolePermissionDialog', () => {
  it('加载中展示加载态', () => {
    mocks.listPermissions.mockReturnValue(new Promise(() => undefined));
    mocks.listRoles.mockReturnValue(new Promise(() => undefined));
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    expect(document.body.textContent).toContain('正在加载权限点');
    unmount();
  });

  it('加载失败展示业务文案与重试', async () => {
    mocks.listPermissions.mockRejectedValue(new Error('权限服务不可用'));
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('重试');
      });
    });
    unmount();
  });

  it('渲染权限矩阵（角色列 / 权限行 / 内置标注）', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });
    expect(document.body.textContent).toContain('知识库');
    expect(document.body.textContent).toContain('所有者');
    expect(document.body.textContent).toContain('内置');
    unmount();
  });

  it('只读态锁定，点「编辑」后可改', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });

    const checkbox = document.body.querySelector<HTMLInputElement>(
      'input[aria-label="成员 的 查看知识库"]',
    );
    expect(checkbox?.disabled).toBe(true);

    // 进入编辑态
    act(() => {
      const edit = Array.from(document.body.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('编辑'),
      );
      edit?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(checkbox?.disabled).toBe(false);
    unmount();
  });

  it('所有者列恒勾选且不可改（编辑态也锁定）', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });
    act(() => {
      const edit = Array.from(document.body.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('编辑'),
      );
      edit?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const ownerCheckbox = document.body.querySelector<HTMLInputElement>(
      'input[aria-label="所有者 的 维护知识库"]',
    );
    expect(ownerCheckbox?.checked).toBe(true);
    expect(ownerCheckbox?.disabled).toBe(true);
    unmount();
  });

  it('无变化时保存置灰；修改后只提交变化的角色', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    mocks.upsertRole.mockResolvedValue({ role_id: 'r_member', role_key: 'member' });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });
    act(() => {
      const edit = Array.from(document.body.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('编辑'),
      );
      edit?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // 未修改：保存置灰
    const save = Array.from(document.body.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('保存'),
    );
    expect(save?.disabled).toBe(true);

    // 给成员加「维护知识库」
    const checkbox = document.body.querySelector<HTMLInputElement>(
      'input[aria-label="成员 的 维护知识库"]',
    );
    act(() => {
      checkbox?.click();
    });
    expect(save?.disabled).toBe(false);

    await act(async () => {
      save?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(identityApi.upsertRole).toHaveBeenCalledTimes(1);
      });
    });
    // 只提交有变化的 member，owner 不参与保存
    expect(identityApi.upsertRole).toHaveBeenCalledWith(
      'org_1',
      expect.objectContaining({ role_key: 'member' }),
    );
    expect(mocks.toast.success).toHaveBeenCalledWith('角色权限已保存');
    unmount();
  });

  it('保存失败以业务文案提示', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    mocks.upsertRole.mockRejectedValue(new Error('没有权限'));
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });
    act(() => {
      const edit = Array.from(document.body.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('编辑'),
      );
      edit?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const checkbox = document.body.querySelector<HTMLInputElement>(
      'input[aria-label="成员 的 维护知识库"]',
    );
    act(() => {
      checkbox?.click();
    });
    const save = Array.from(document.body.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('保存'),
    );
    await act(async () => {
      save?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalled();
      });
    });
    unmount();
  });

  it('无管理权限时编辑置灰', async () => {
    mocks.listPermissions.mockResolvedValue({ groups: PERMISSION_GROUPS });
    mocks.listRoles.mockResolvedValue({ items: ROLES });
    const { unmount } = renderWithClient(
      <RolePermissionDialog open onOpenChange={vi.fn()} orgId="org_1" canManage={false} />,
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('查看知识库');
      });
    });
    const edit = Array.from(document.body.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('编辑'),
    );
    expect(edit?.disabled).toBe(true);
    unmount();
  });
});
