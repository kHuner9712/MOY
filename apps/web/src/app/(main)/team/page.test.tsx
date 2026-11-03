/**
 * 前端补测第二十七批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：团队管理页（P-04 深化）四个标签的编排合同 ——
 * 1. 成员：角色下拉即时保存、移出二次确认（知识库转交口径）、
 *    邀请表单校验与失败文案落在表单内、owner 行与非管理者锁定；
 * 2. 组织架构：入口卡片；
 * 3. 插件能力：保存前「个人 3 项恒开 + 企业 3 项」合成、只提交企业能力、
 *    保存后回填最近修改人、非管理者只读；
 * 4. 操作记录：标签切换才拉数据、动作筛选项来自 ID11 目录、
 *    筛选参数拼装与双空态。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import TeamPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  profile: vi.fn(),
  listMembers: vi.fn(),
  inviteMember: vi.fn(),
  updateMember: vi.fn(),
  removeMember: vi.fn(),
  listAuditActions: vi.fn(),
  auditLogs: vi.fn(),
  market: vi.fn(),
  setCapabilityPolicy: vi.fn(),
  /**
   * 幂等键作用域的直接替身（P1-12）：透传执行 `fn`，但要能被断言「两次点击拿到了同一把键」。
   * 只透传不实现，是因为作用域本身的语义已由 `api-client-idempotency.test.ts` 锁死。
   */
  withIdempotencyKey: vi.fn((...args: [string, () => Promise<unknown>]) => args[1]()),
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
  withIdempotencyKey: mocks.withIdempotencyKey,
  identityApi: {
    profile: mocks.profile,
    listMembers: mocks.listMembers,
    inviteMember: mocks.inviteMember,
    auditLogs: mocks.auditLogs,
  },
  identityExtApi: {
    updateMember: mocks.updateMember,
    removeMember: mocks.removeMember,
    listAuditActions: mocks.listAuditActions,
  },
  pluginsApi: {
    market: mocks.market,
    setCapabilityPolicy: mocks.setCapabilityPolicy,
  },
}));

/** 标签状态读地址栏（useUrlTab），整文件用假 location 替身 */
interface FakeLocation {
  origin: string;
  pathname: string;
  search: string;
  hash: string;
  assign: ReturnType<typeof vi.fn>;
}

function installFakeLocation(search = ''): FakeLocation {
  const fake: FakeLocation = {
    origin: 'http://localhost:3000',
    pathname: '/team',
    search,
    hash: '',
    assign: vi.fn(),
  };
  Object.defineProperty(window, 'location', {
    value: fake,
    writable: true,
    configurable: true,
  });
  return fake;
}

beforeEach(() => {
  installFakeLocation();
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
 * 等异步链路的 DOM 落盘。
 *
 * 补测第二十四/二十六批沉淀的口径：promise 续体与 effect 二次提交驱动的
 * 更新在一个长 act 内部轮询观察不到（提交压到 act 退出才落盘）；而
 * `vi.waitFor` 的睡眠区间又在 act 外，会刷「not wrapped in act」警告。
 * 因此手动逐拍：睡眠放 act 窗口内（提交被 act 收口），断言放窗口外
 * （act 退出即落盘，看得见），窗口之间只有同步代码。
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

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找（行内也有「移出」） */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

/** 在受控输入框里输入文本 */
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

/** 受控下拉换值（原生 select） */
function setSelectValue(select: HTMLSelectElement | null | undefined, value: string): void {
  if (!select) return;
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

/** 提交表单（jsdom 点提交按钮不一定触发表单提交，直接发 submit 事件） */
function submitForm(root: ParentNode): void {
  act(() => {
    root
      .querySelector('form')
      ?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

/** 显式传 null 表示「空体」，不能用 `??`（null 会被吞掉） */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

const OWNER_ROW = {
  user_id: 'u_owner',
  display_name: '王总',
  email: 'wang@example.com',
  role: 'owner',
  joined_at: '2026-01-01T10:00:00Z',
};

const MEMBER_ROW = {
  user_id: 'u_2',
  display_name: '李四',
  email: 'lisi@example.com',
  role: 'member',
  joined_at: '2026-10-01T10:00:00Z',
};

const PROFILE = {
  user_id: 'u_1',
  display_name: '陈麦',
  email: 'chenmai@example.com',
  tenant: { tenant_id: 't_1', name: '华农科技', plan: 'professional' },
  organizations: [{ org_id: 'org_1', name: '华农科技', role: 'owner' }],
};

const MARKET = {
  items: [],
  page: 1,
  page_size: 1,
  total: 0,
  total_pages: 1,
  org_capabilities: [
    { capability_key: 'org.directory.read', org_switch_enabled: true },
    { capability_key: 'org.repo.write', org_switch_enabled: false },
    { capability_key: 'org.external.publish', org_switch_enabled: true },
  ],
};

const AUDIT_ACTIONS = {
  groups: [
    {
      group: 'member',
      group_name: '成员管理',
      actions: [{ action: 'invite_member', display_name: '邀请成员' }],
    },
  ],
};

const LOGS = {
  items: [
    {
      log_id: 'log_1',
      action: 'invite_member',
      action_display_name: '邀请成员',
      actor_name: '王总',
      actor_role: 'owner',
      result: 'success',
      change_summary: '邀请了 lisi@example.com',
      ip_masked: '10.0.0.*',
      created_at: '2026-10-31T10:00:00Z',
    },
    {
      log_id: 'log_2',
      action: 'remove_member',
      action_display_name: '',
      actor_name: '',
      actor_role: 'viewer',
      result: 'failed',
      change_summary: '',
      ip_masked: '',
      created_at: '2026-10-30T10:00:00Z',
    },
  ],
  meta: { page: 1, page_size: 20, total: 2, total_pages: 1 },
};

/** 各路查询的默认数据（按需覆盖；显式 null 表示空体） */
function mockTeamData(
  overrides: {
    profile?: unknown;
    members?: unknown;
    market?: unknown;
    auditActions?: unknown;
    logs?: unknown;
  } = {},
): void {
  mocks.profile.mockResolvedValue(pick('profile', PROFILE, overrides));
  mocks.listMembers.mockResolvedValue(
    pick(
      'members',
      {
        items: [OWNER_ROW, MEMBER_ROW],
        meta: { page: 1, page_size: 20, total: 2, total_pages: 1 },
      },
      overrides,
    ),
  );
  mocks.market.mockResolvedValue(pick('market', MARKET, overrides));
  mocks.listAuditActions.mockResolvedValue(pick('auditActions', AUDIT_ACTIONS, overrides));
  mocks.auditLogs.mockResolvedValue(pick('logs', LOGS, overrides));
}

describe('团队管理页', () => {
  it('加载与失败三态齐备', async () => {
    mockTeamData();
    mocks.profile.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<TeamPage />);
    expect(loading.container.textContent).toContain('正在加载团队信息…');
    loading.unmount();

    mocks.profile.mockClear();
    mocks.profile.mockRejectedValue(new Error('团队信息暂时取不回来'));
    const failed = renderWithClient(<TeamPage />);
    await waitForText(failed.container, '团队信息暂时取不回来');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.profile).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();
  });

  it('未加入企业给空态引导', async () => {
    mockTeamData({
      profile: { ...PROFILE, organizations: [] },
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '还没有加入任何企业');
    expect(container.textContent).toContain('请联系管理员确认你的成员身份后刷新页面');
    unmount();
  });

  it('成员表格：owner 行锁定，角色下拉是业务语言', async () => {
    mockTeamData();
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    const ownerSelect = container.querySelector<HTMLSelectElement>(
      'select[aria-label="调整 王总 的角色"]',
    );
    expect(ownerSelect?.disabled).toBe(true);
    const ownerRemove = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent?.includes('移出') && item.disabled,
    );
    expect(ownerRemove).toBeDefined();

    // 角色下拉全部业务语言，不出现内部标识
    const labels = Array.from(ownerSelect?.options ?? []).map((option) => option.textContent);
    expect(labels).toEqual(['所有者', '企业管理员', '成员', '访客']);

    // 管理者看普通成员：可改可移出
    const memberSelect = container.querySelector<HTMLSelectElement>(
      'select[aria-label="调整 李四 的角色"]',
    );
    expect(memberSelect?.disabled).toBe(false);
    unmount();
  });

  it('非管理者整页只读', async () => {
    mockTeamData({
      profile: {
        ...PROFILE,
        organizations: [{ org_id: 'org_1', name: '华农科技', role: 'member' }],
      },
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    expect(findButton(container, '邀请成员')?.disabled).toBe(true);
    expect(
      container.querySelector<HTMLSelectElement>('select[aria-label="调整 李四 的角色"]')?.disabled,
    ).toBe(true);
    unmount();
  });

  it('角色下拉换值即时保存', async () => {
    mockTeamData();
    mocks.updateMember.mockResolvedValue({ user_id: 'u_2', role: 'org_admin' });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="调整 李四 的角色"]'),
      'org_admin',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateMember).toHaveBeenCalledWith('org_1', 'u_2', { role: 'org_admin' });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('成员角色已更新');
      });
    });
    unmount();
  });

  it('邀请表单校验邮箱并按所选角色发出', async () => {
    mockTeamData();
    mocks.inviteMember.mockResolvedValue({ invitation_id: 'i1', status: 'pending' });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    click(findButton(container, '邀请成员'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('邀请成员');
      });
    });

    // 邮箱为空：拦截且不发请求
    submitForm(document.body);
    await act(async () => {});
    expect(document.body.textContent).toContain('请填写成员邮箱');
    expect(mocks.inviteMember).not.toHaveBeenCalled();

    typeInto(document.body, '#invite-email', '  new@example.com  ');
    setSelectValue(document.body.querySelector<HTMLSelectElement>('#invite-role'), 'org_admin');
    submitForm(document.body);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.inviteMember).toHaveBeenCalledWith('org_1', {
          email: 'new@example.com',
          role: 'org_admin',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('邀请已发送，对方接受后即可加入');
      });
    });
    // 成功后弹窗关闭并重置表单
    expect(document.body.querySelector('form')).toBeNull();
    unmount();
  });

  it('同一邀请意图双击复用同一把幂等键，换邮箱才换新键（P1-12）', async () => {
    mockTeamData();
    // 让请求失败：弹窗保持打开，才能模拟「对着同一个提交按钮连点」
    mocks.inviteMember.mockRejectedValue(new Error('服务暂时不可用'));
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    click(findButton(container, '邀请成员'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('邀请成员');
      });
    });

    typeInto(document.body, '#invite-email', 'dup@example.com');
    submitForm(document.body);
    await act(async () => {});
    submitForm(document.body);
    await act(async () => {});
    submitForm(document.body);
    await act(async () => {});

    const keys = mocks.withIdempotencyKey.mock.calls.map((call) => call[0] as string);
    expect(keys).toHaveLength(3);
    // 同一邮箱三次点击 = 一次用户动作 → 同一把键，后端才能去重
    expect(keys[0]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);

    // 换邮箱 = 另一次动作 → 必须换新键（否则第二个人会被后端认成第一个人的重复投递）
    typeInto(document.body, '#invite-email', 'other@example.com');
    submitForm(document.body);
    await act(async () => {});
    const after = mocks.withIdempotencyKey.mock.calls.map((call) => call[0] as string);
    expect(after).toHaveLength(4);
    expect(after[3]).not.toBe(keys[0]);
    unmount();
  });

  it('邀请失败把业务文案留在表单里', async () => {
    mockTeamData();
    mocks.inviteMember.mockRejectedValue(new Error('席位不足，请先扩容'));
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    click(findButton(container, '邀请成员'));
    typeInto(document.body, '#invite-email', 'new@example.com');
    submitForm(document.body);

    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('席位不足，请先扩容');
      });
    });
    // 失败留在弹窗里可改了重试
    expect(document.body.querySelector('form')).not.toBeNull();
    unmount();
  });

  it('移出成员二次确认并说明知识库转交口径', async () => {
    mockTeamData();
    mocks.removeMember.mockResolvedValue({
      member_id: 'u_2',
      removed: true,
      transferred_knowledge_base_count: 2,
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    const remove = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent?.includes('移出') && !item.disabled,
    );
    click(remove);
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('移出该成员？');
      });
    });
    expect(document.body.textContent).toContain('其创建的知识库会自动转交企业管理员');
    click(dialogButton('移出成员'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.removeMember).toHaveBeenCalledWith('org_1', 'u_2');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith(
          '成员已移出，其 2 个知识库已转交企业管理员',
        );
      });
    });
    unmount();
  });

  it('移出没有知识库的成员用简短提示', async () => {
    mockTeamData();
    mocks.removeMember.mockResolvedValue({
      member_id: 'u_2',
      removed: true,
      transferred_knowledge_base_count: 0,
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '李四');

    click(
      Array.from(container.querySelectorAll('button')).find(
        (item) => item.textContent?.includes('移出') && !item.disabled,
      ),
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('移出该成员？');
      });
    });
    click(dialogButton('移出成员'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('成员已移出');
      });
    });
    unmount();
  });

  it('成员空态给邀请引导', async () => {
    mockTeamData({
      members: { items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 1 } },
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '还没有其他成员');
    expect(container.textContent).toContain('邀请同事加入后，就可以一起维护知识库与共享问答');
    unmount();
  });

  it('组织架构标签给进入入口', async () => {
    installFakeLocation('?tab=structure');
    mockTeamData();
    const { container, unmount } = renderWithClient(<TeamPage />);

    await waitForText(container, '维护部门层级');
    const link = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('进入组织架构'),
    );
    expect(link?.getAttribute('href')).toBe('/team/organization');
    unmount();
  });

  it('能力开关保存前合成六项，个人能力恒开不可勾', async () => {
    installFakeLocation('?tab=capabilities');
    mockTeamData();
    const { container, unmount } = renderWithClient(<TeamPage />);
    // 等数据分支（列表）落定：标题在加载/失败分支也会渲染，不能当就绪信号
    await waitForText(container, '个人能力，始终开启');

    expect(container.textContent).toContain('个人能力（读取你本人的资料）恒开启');
    // 个人 3 项 + 企业 3 项
    const boxes = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    );
    expect(boxes.length).toBe(6);
    expect(boxes.filter((box) => box.disabled).length).toBe(3);
    expect(container.textContent).toContain('恒开启');
    // 企业项按 PL1 顶层读数渲染
    expect(container.textContent).toContain('已关闭');
    // G-16：能力 key 不进 DOM
    expect(container.textContent).not.toContain('org.repo.write');
    unmount();
  });

  it('保存只提交企业能力，成功后回填最近修改', async () => {
    installFakeLocation('?tab=capabilities');
    mockTeamData();
    mocks.setCapabilityPolicy.mockResolvedValue({
      policies: [
        {
          capability_key: 'org.directory.read',
          display_name: '读取企业通讯录与组织架构',
          org_switch_enabled: true,
          updated_by_name: '张三',
          updated_at: '2026-10-31T10:00:00Z',
        },
      ],
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    // 「保存开关」按钮只在数据分支渲染，用它当就绪信号
    await waitForText(container, '保存开关');

    click(findButton(container, '保存开关'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.setCapabilityPolicy).toHaveBeenCalledWith({
          policies: [
            { capability_key: 'org.directory.read', org_switch_enabled: true },
            { capability_key: 'org.repo.write', org_switch_enabled: false },
            { capability_key: 'org.external.publish', org_switch_enabled: true },
          ],
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('企业能力开关已保存');
      });
    });
    await waitForText(container, '最近修改：张三');
    unmount();
  });

  it('非管理者不能保存企业能力', async () => {
    installFakeLocation('?tab=capabilities');
    mockTeamData({
      profile: {
        ...PROFILE,
        organizations: [{ org_id: 'org_1', name: '华农科技', role: 'member' }],
      },
    });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '保存开关');

    expect(findButton(container, '保存开关')?.disabled).toBe(true);
    unmount();
  });

  it('没有企业能力可配时诚实提示', async () => {
    installFakeLocation('?tab=capabilities');
    mockTeamData({ market: { ...MARKET, org_capabilities: [] } });
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '保存开关');

    click(findButton(container, '保存开关'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('还没有可配置的企业能力');
      });
    });
    expect(mocks.setCapabilityPolicy).not.toHaveBeenCalled();
    unmount();
  });

  it('能力开关加载失败可原地重试', async () => {
    installFakeLocation('?tab=capabilities');
    mockTeamData();
    mocks.market.mockRejectedValue(new Error('能力开关暂时取不回来'));
    const { container, unmount } = renderWithClient(<TeamPage />);

    await waitForText(container, '能力开关暂时取不回来');
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.market).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('操作记录只在切到该标签后才拉取', async () => {
    mockTeamData();
    const membersView = renderWithClient(<TeamPage />);
    await waitForText(membersView.container, '李四');
    expect(mocks.listAuditActions).not.toHaveBeenCalled();
    expect(mocks.auditLogs).not.toHaveBeenCalled();
    membersView.unmount();

    installFakeLocation('?tab=logs');
    const logsView = renderWithClient(<TeamPage />);
    await waitForText(logsView.container, '邀请了 lisi@example.com');
    expect(mocks.listAuditActions).toHaveBeenCalled();
    logsView.unmount();
  });

  it('操作记录展示口径：动作名回落、角色映射、失败说未成功', async () => {
    installFakeLocation('?tab=logs');
    mockTeamData();
    const { container, unmount } = renderWithClient(<TeamPage />);

    await waitForText(container, '邀请了 lisi@example.com');
    expect(container.textContent).toContain('邀请成员');
    expect(container.textContent).toContain('王总（所有者）');
    expect(container.textContent).toContain('成功');
    // 动作名缺省回落动作标识；失败结果说「未成功」而不是错误码
    expect(container.textContent).toContain('remove_member');
    expect(container.textContent).toContain('未成功');
    // 未知角色原样展示，不崩
    expect(container.textContent).toContain('viewer');
    unmount();
  });

  it('操作记录筛选项来自目录，换筛选换查询参数', async () => {
    installFakeLocation('?tab=logs');
    mockTeamData();
    const { container, unmount } = renderWithClient(<TeamPage />);
    await waitForText(container, '邀请了 lisi@example.com');

    // 动作筛选项 = 目录分组 · 动作名
    const actionSelect = container.querySelector<HTMLSelectElement>(
      'select[aria-label="按动作筛选"]',
    );
    const optionLabels = Array.from(actionSelect?.options ?? []).map((item) => item.textContent);
    expect(optionLabels).toContain('成员管理 · 邀请成员');

    setSelectValue(actionSelect, 'invite_member');
    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按结果筛选"]'),
      'failed',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.auditLogs).toHaveBeenCalledWith({
          page: 1,
          page_size: 20,
          action: 'invite_member',
          result: 'failed',
        });
      });
    });
    unmount();
  });

  it('操作记录双空态区分「还没有」与「筛选无结果」', async () => {
    installFakeLocation('?tab=logs');
    mockTeamData({
      logs: { items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 1 } },
    });
    const { container, unmount } = renderWithClient(<TeamPage />);

    await waitForText(container, '还没有操作记录');

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按结果筛选"]'),
      'failed',
    );
    await waitForText(container, '没有符合条件的记录');
    unmount();
  });
});
