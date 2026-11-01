/**
 * 前端补测第十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：部门层级树的渲染合同 —— 选中与键盘可达、展开折叠、
 * 5 层上限置灰、权限与移动中的拖拽约束、拖拽移动回调。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DepartmentNode } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { DepartmentTree } from './department-tree';

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
