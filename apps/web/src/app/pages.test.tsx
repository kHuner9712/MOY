/**
 * 前端补测第二十一批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：page 组件层的渲染与数据合同 ——
 * 1. 关于页：版本信息 / 开源组件许可证清单 / 合规入口「配置了才给链接」双分支；
 * 2. 个人中心页：会话兜底、套餐与组织角色业务语言映射、用量三态与刷新按钮；
 * 3. 对话主页：额度提醒阈值（80% 才打扰、非有限值不提醒）与加载/失败分支；
 * 4. 我的记忆页：三态、六套空态分流、删除二次确认、删除全部关键字合同、
 *    删空当前页后自动回退一页。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatNumber, formatPercent, formatUnits } from '@/lib/format';
import {
  MEMORY_DELETE_ALL_CONFIRM,
  MEMORY_DELETE_CONFIRM,
  MEMORY_DISABLED_BANNER,
  MEMORY_EMPTY_COPY,
} from '@/lib/memory-labels';
import type { CurrentUser, MemoryItem, MemoryPage } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';

import webPackage from '../../package.json';
import AboutPage from './(main)/about/page';
import ChatPage from './(main)/chat/page';
import MemoriesPage from './(main)/profile/memories/page';
import ProfilePage from './(main)/profile/page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  push: vi.fn(),
  logout: vi.fn(),
  reload: vi.fn(),
  usage: vi.fn(),
  getUsage: vi.fn(),
  memoryList: vi.fn(),
  memoryRemove: vi.fn(),
  memoryRemoveMany: vi.fn(),
  memoryUpdateSettings: vi.fn(),
  session: {
    loading: false,
    user: null as unknown,
    errorMessage: '',
  },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));

vi.mock('@/hooks/use-session', () => ({
  useSession: () => ({
    loading: mocks.session.loading,
    user: mocks.session.user,
    errorMessage: mocks.session.errorMessage,
    reload: mocks.reload,
    logout: mocks.logout,
  }),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  modelApi: { usage: mocks.usage },
  billingApi: { getUsage: mocks.getUsage },
  memoryApi: {
    list: mocks.memoryList,
    remove: mocks.memoryRemove,
    removeMany: mocks.memoryRemoveMany,
    updateSettings: mocks.memoryUpdateSettings,
  },
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

// 对话窗口仅在客户端加载（SSE），本批只测页面自身的额度合同；
// 动态导入换成 loading 占位，避免把 chat-window 的依赖面拉进本文件。
vi.mock('next/dynamic', () => ({
  default: (_loader: unknown, options?: { loading?: () => unknown }) =>
    (options?.loading as (() => unknown) | undefined) ?? (() => null),
}));

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

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找（卡片上也有「删除」） */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

function setInputValue(input: HTMLInputElement | null, value: string): void {
  if (!input) return;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

// ---------------------------------------------------------------------------
// 关于页
// ---------------------------------------------------------------------------

describe('关于页', () => {
  it('展示产品、版本与交付形态', () => {
    const { container, unmount } = renderWithClient(<AboutPage />);
    expect(container.textContent).toContain('麦子（Moy）');
    expect(container.textContent).toContain(`v${webPackage.version}`);
    expect(container.textContent).toContain('私有化部署 · 轻量版');
    expect(container.textContent).toContain('由您的运维人员在维护窗口内升级');
    expect(container.textContent).toContain('与你一起把工作做好');
    unmount();
  });

  it('开源组件清单列明许可证并指向完整机器可读清单', () => {
    const { container, unmount } = renderWithClient(<AboutPage />);
    const list = container.querySelectorAll('ul.divide-y > li');
    expect(list.length).toBe(16);
    expect(container.textContent).toContain('React');
    expect(container.textContent).toContain('Apache-2.0（修改版，附使用限制）');
    expect(container.textContent).toContain('PostgreSQL License');
    expect(container.textContent).toContain('记忆检索所需的数据库扩展');
    expect(container.textContent).toContain('sbom.cdx.json');
    unmount();
  });

  it('未配置合规入口时只给交付路径，不生成死链', () => {
    delete process.env.NEXT_PUBLIC_COMPLIANCE_BASE_URL;
    const { container, unmount } = renderWithClient(<AboutPage />);
    expect(container.textContent).toContain('隐私政策');
    expect(container.textContent).toContain('docs/compliance/privacy-policy.md');
    expect(container.textContent).toContain('在交付包的');
    expect(container.querySelectorAll('a')).toHaveLength(0);
    unmount();
  });

  it('配置合规入口后三份文件渲染成可点链接', () => {
    process.env.NEXT_PUBLIC_COMPLIANCE_BASE_URL = 'https://portal.example.com/compliance';
    try {
      const { container, unmount } = renderWithClient(<AboutPage />);
      const links = Array.from(container.querySelectorAll('a'));
      expect(links.map((item) => item.getAttribute('href'))).toEqual([
        'https://portal.example.com/compliance/privacy-policy',
        'https://portal.example.com/compliance/dpa-template',
        'https://portal.example.com/compliance/security-whitepaper',
      ]);
      expect(links[0]?.textContent).toContain('打开隐私政策');
      unmount();
    } finally {
      delete process.env.NEXT_PUBLIC_COMPLIANCE_BASE_URL;
    }
  });
});

// ---------------------------------------------------------------------------
// 个人中心页
// ---------------------------------------------------------------------------

const USER: CurrentUser = {
  user_id: 'u_1',
  display_name: '陈麦',
  email: 'chenmai@example.com',
  tenant: { tenant_id: 't_1', name: '华农科技', plan: 'professional' },
  organizations: [
    { org_id: 'o_1', name: '产品部', role: 'owner' },
    { org_id: 'o_2', name: '交付部', role: 'org_admin' },
    { org_id: 'o_3', name: '客户成功', role: 'member' },
    { org_id: 'o_4', name: '外部顾问', role: 'guest' },
  ],
};

describe('个人中心页', () => {
  it('会话未就绪时只给加载占位，且不拉用量', async () => {
    mocks.session.user = null;
    const { container, unmount } = renderWithClient(<ProfilePage />);
    expect(container.textContent).toContain('正在加载账户信息…');
    await act(async () => {});
    expect(mocks.usage).not.toHaveBeenCalled();
    unmount();
  });

  it('账户信息按业务语言映射套餐与组织角色', async () => {
    mocks.session.user = USER;
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 0,
      document_qa_count: 0,
      quota: { plan_name: '', total_units: 0, used_units: 0, remaining_units: 0 },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);

    expect(container.textContent).toContain('陈麦');
    expect(container.textContent).toContain('chenmai@example.com');
    expect(container.textContent).toContain('华农科技');
    expect(container.textContent).toContain('专业版');
    expect(container.textContent).toContain('产品部');
    expect(container.textContent).toContain('所有者');
    expect(container.textContent).toContain('企业管理员');
    expect(container.textContent).toContain('成员');
    expect(container.textContent).toContain('访客');
    // 组织角色是内部标识，任何未映射值不得直接上屏以外的口径混用
    expect(container.textContent).not.toContain('org_admin');
    unmount();
  });

  it('未加入组织与未知套餐各有兜底文案', () => {
    mocks.session.user = {
      ...USER,
      tenant: { tenant_id: 't_1', name: '华农科技', plan: 'trial' },
      organizations: [],
    };
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 0,
      document_qa_count: 0,
      quota: { plan_name: '', total_units: 0, used_units: 0, remaining_units: 0 },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);
    expect(container.textContent).toContain('还没有加入任何组织；联系企业管理员邀请后即可协作。');
    expect(container.textContent).toContain('trial');
    unmount();
  });

  it('退出登录触发清会话与成功提示', () => {
    mocks.session.user = USER;
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 0,
      document_qa_count: 0,
      quota: { plan_name: '', total_units: 0, used_units: 0, remaining_units: 0 },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);

    click(findButton(container, '退出登录'));
    expect(mocks.logout).toHaveBeenCalledTimes(1);
    expect(mocks.toast.success).toHaveBeenCalledWith('已退出登录');
    unmount();
  });

  it('用量加载中给等待提示', () => {
    mocks.session.user = USER;
    mocks.usage.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<ProfilePage />);
    expect(container.textContent).toContain('正在加载用量…');
    unmount();
  });

  it('用量失败给业务文案并可原地重试', async () => {
    mocks.session.user = USER;
    mocks.usage.mockRejectedValue(new Error('用量信息暂时取不回来'));
    const { container, unmount } = renderWithClient(<ProfilePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('用量信息暂时取不回来');
      });
    });
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.usage).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('用量展示次数与额度进度（口径与账单一致）', async () => {
    mocks.session.user = USER;
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 1234,
      document_qa_count: 56,
      quota: {
        plan_name: '专业版',
        total_units: 100000,
        used_units: 40000,
        remaining_units: 60000,
      },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(formatNumber(1234));
      });
    });
    expect(container.textContent).toContain(formatNumber(56));
    expect(container.textContent).toContain('专业版额度');
    expect(container.textContent).toContain(`剩余 ${formatUnits(60000)}`);
    unmount();
  });

  it('额度上限为 0 时明说未设上限', async () => {
    mocks.session.user = USER;
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 3,
      document_qa_count: 1,
      quota: { plan_name: '', total_units: 0, used_units: 0, remaining_units: 0 },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('当前套餐未设置额度上限，可放心使用。');
      });
    });
    unmount();
  });

  it('还没有用量记录时给空态而不是空白', async () => {
    mocks.session.user = USER;
    // 后端未生成用量记录时返回空体（falsy data），页面必须给空态而不是留白
    mocks.usage.mockResolvedValue(null as never);
    const { container, unmount } = renderWithClient(<ProfilePage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有用量记录');
      });
    });
    expect(container.textContent).toContain('发起一次对话或文档问答后，这里会显示本月用量');
    unmount();
  });

  it('刷新按钮在拉取中禁用并显示进度', async () => {
    mocks.session.user = USER;
    mocks.usage.mockResolvedValue({
      period: '2026-11',
      conversation_count: 3,
      document_qa_count: 1,
      quota: { plan_name: '', total_units: 0, used_units: 0, remaining_units: 0 },
    });
    const { container, unmount } = renderWithClient(<ProfilePage />);
    // 等首个请求落定（拉取中按钮是禁用态，点不中）
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('当前套餐未设置额度上限，可放心使用。');
      });
    });

    mocks.usage.mockImplementation(() => new Promise(() => {}));
    click(findButton(container, '刷新'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.usage).toHaveBeenCalledTimes(2);
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        const busy = findButton(container, '刷新中…');
        expect(busy).toBeDefined();
        expect(busy?.disabled).toBe(true);
      });
    });
    unmount();
  });
});

// ---------------------------------------------------------------------------
// 对话主页
// ---------------------------------------------------------------------------

const BILLING_USAGE = {
  period: '2026-11',
  quota_units_total: 100000,
  quota_units_used: 85400,
  quota_units_remaining: 14600,
  usage_percent: 85.4,
  over_quota_warning: false,
  quota_unit: '',
  breakdown: [],
  daily_trend: [],
};

describe('对话主页', () => {
  it('额度加载中只给骨架，不渲染提醒', () => {
    mocks.getUsage.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<ChatPage />);
    expect(container.querySelector('.skeleton')).not.toBeNull();
    expect(container.textContent).not.toContain('去升级套餐');
    unmount();
  });

  it('额度信息加载失败给可重试错误态', async () => {
    mocks.getUsage.mockRejectedValue(new Error('额度信息暂时取不回来'));
    const { container, unmount } = renderWithClient(<ChatPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('额度信息暂时取不回来');
      });
    });
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getUsage).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('用量低于阈值不打扰用户', async () => {
    mocks.getUsage.mockResolvedValue({ ...BILLING_USAGE, usage_percent: 30 });
    const { container, unmount } = renderWithClient(<ChatPage />);

    // 等额度数据真正落定再断言，避免「数据没到所以没提醒」蒙混过关
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getUsage).toHaveBeenCalledTimes(1);
      });
    });
    await act(async () => {});
    expect(container.textContent).not.toContain('去升级套餐');
    unmount();
  });

  it('用量达到阈值给提醒条与升级入口', async () => {
    mocks.getUsage.mockResolvedValue(BILLING_USAGE);
    const { container, unmount } = renderWithClient(<ChatPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('本月额度已用');
      });
    });
    expect(container.textContent).toContain(formatPercent(85.4));
    expect(container.textContent).toContain((14600).toLocaleString('zh-CN'));
    // 额度单位缺省是「字」，且必须是业务语言
    expect(container.textContent).toContain('接近上限时会切换为常规模式');
    const link = container.querySelector('a[href="/billing"]');
    expect(link?.textContent).toContain('去升级套餐');
    unmount();
  });

  it('自定义额度单位生效；非有限百分比不误报提醒', async () => {
    mocks.getUsage.mockResolvedValue({
      ...BILLING_USAGE,
      usage_percent: 80,
      quota_units_remaining: 20000,
      quota_unit: '次',
    });
    const first = renderWithClient(<ChatPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(first.container.textContent).toContain('本月额度已用');
      });
    });
    expect(first.container.textContent).toContain((20000).toLocaleString('zh-CN'));
    expect(first.container.textContent).toContain('次');
    first.unmount();

    mocks.getUsage.mockResolvedValue({ ...BILLING_USAGE, usage_percent: Number.NaN });
    const second = renderWithClient(<ChatPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getUsage).toHaveBeenCalledTimes(2);
      });
    });
    await act(async () => {});
    expect(second.container.textContent).not.toContain('本月额度已用');
    second.unmount();
  });
});

// ---------------------------------------------------------------------------
// 我的记忆页
// ---------------------------------------------------------------------------

function memoryItem(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    memory_id: 'mem_1',
    category: 'identity',
    content: '你习惯用中文写周报',
    confidence: 'high',
    source_conversation_id: 'conv_1',
    updated_at: '2026-10-31T10:00:00Z',
    superseded_by: '',
    ...overrides,
  };
}

function memoryPage(overrides: Partial<MemoryPage> = {}): MemoryPage {
  return {
    items: [memoryItem()],
    meta: { page: 1, page_size: 20, total: 1, total_pages: 1 },
    enabled: true,
    category_switches: {},
    ...overrides,
  };
}

describe('我的记忆页', () => {
  it('加载中给读取提示', () => {
    mocks.memoryList.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<MemoriesPage />);
    expect(container.textContent).toContain('正在读取关于你的信息…');
    unmount();
  });

  it('加载失败由页面自渲染错误态并可重试', async () => {
    mocks.memoryList.mockRejectedValue(new Error('记忆服务不可用'));
    const { container, unmount } = renderWithClient(<MemoriesPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(MEMORY_EMPTY_COPY.failed.title);
      });
    });
    expect(container.textContent).toContain(MEMORY_EMPTY_COPY.failed.description);
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryList).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('关闭状态给横幅与空态，可一键重新开启', async () => {
    mocks.memoryList.mockResolvedValue(
      memoryPage({
        items: [],
        enabled: false,
        meta: { page: 1, page_size: 20, total: 0, total_pages: 1 },
      }),
    );
    const { container, unmount } = renderWithClient(<MemoriesPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(MEMORY_DISABLED_BANNER);
      });
    });
    expect(container.textContent).toContain(MEMORY_EMPTY_COPY.disabled.title);

    click(findButton(container, MEMORY_EMPTY_COPY.disabled.actionLabel ?? '开启'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryUpdateSettings).toHaveBeenCalledWith({ enabled: true });
      });
    });
    unmount();
  });

  it('从未记到信息时引导去对话', async () => {
    mocks.memoryList.mockResolvedValue(
      memoryPage({ items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 1 } }),
    );
    const { container, unmount } = renderWithClient(<MemoriesPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(MEMORY_EMPTY_COPY.never.title);
      });
    });
    click(findButton(container, MEMORY_EMPTY_COPY.never.actionLabel ?? '去对话'));
    expect(mocks.push).toHaveBeenCalledWith('/chat');
    unmount();
  });

  it('按类别筛选回到第 1 页，筛选无结果给筛选空态', async () => {
    mocks.memoryList.mockResolvedValueOnce(memoryPage());
    mocks.memoryList.mockResolvedValue(
      memoryPage({ items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 1 } }),
    );
    const { container, unmount } = renderWithClient(<MemoriesPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('你习惯用中文写周报');
      });
    });

    click(findButton(container, '工作习惯'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryList).toHaveBeenCalledWith({
          category: 'preference',
          page: 1,
          page_size: 20,
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(MEMORY_EMPTY_COPY.filtered.title);
      });
    });
    unmount();
  });

  it('单条删除必须二次确认后才删除', async () => {
    mocks.memoryList.mockResolvedValue(memoryPage());
    mocks.memoryRemove.mockResolvedValue({ memory_id: 'mem_1', deleted: true });
    const { container, unmount } = renderWithClient(<MemoriesPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('你习惯用中文写周报');
      });
    });

    click(container.querySelector('button[aria-label^="删除这条信息"]'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain(MEMORY_DELETE_CONFIRM.title);
      });
    });
    expect(document.body.textContent).toContain(MEMORY_DELETE_CONFIRM.description);

    click(dialogButton(MEMORY_DELETE_CONFIRM.confirmLabel));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryRemove).toHaveBeenCalledWith('mem_1');
      });
    });
    expect(mocks.toast.success).toHaveBeenCalledWith('已删除，我不会再用这条信息回答你');
    unmount();
  });

  it('删除全部必须输入关键字，输入不匹配即拦截', async () => {
    mocks.memoryList.mockResolvedValue(memoryPage());
    mocks.memoryRemoveMany.mockResolvedValue({ deleted_count: 1, category: '' });
    const { container, unmount } = renderWithClient(<MemoriesPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('你习惯用中文写周报');
      });
    });

    click(findButton(container, '删除我的全部记忆'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain(MEMORY_DELETE_ALL_CONFIRM.title);
      });
    });

    // 没输入关键字就确认：拦截、不发请求、弹窗不关
    click(dialogButton(MEMORY_DELETE_ALL_CONFIRM.confirmLabel));
    await act(async () => {});
    expect(mocks.toast.error).toHaveBeenCalledWith(
      `请输入「${MEMORY_DELETE_ALL_CONFIRM.keyword}」以确认`,
    );
    expect(mocks.memoryRemoveMany).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(MEMORY_DELETE_ALL_CONFIRM.title);

    // 输入不匹配：给出不匹配提示
    setInputValue(
      document.body.querySelector<HTMLInputElement>(
        `input[aria-label="输入${MEMORY_DELETE_ALL_CONFIRM.keyword}以确认"]`,
      ),
      '删',
    );
    expect(document.body.textContent).toContain('不匹配，请重新输入');

    // 输入正确后确认：整域删除成功并给「已清空」空态
    mocks.memoryList.mockResolvedValue(
      memoryPage({ items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 1 } }),
    );
    setInputValue(
      document.body.querySelector<HTMLInputElement>(
        `input[aria-label="输入${MEMORY_DELETE_ALL_CONFIRM.keyword}以确认"]`,
      ),
      MEMORY_DELETE_ALL_CONFIRM.keyword,
    );
    click(dialogButton(MEMORY_DELETE_ALL_CONFIRM.confirmLabel));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryRemoveMany).toHaveBeenCalledWith('');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain(MEMORY_EMPTY_COPY.cleared.title);
      });
    });
    expect(mocks.toast.success).toHaveBeenCalledWith('已删除 1 条信息');
    unmount();
  });

  it('删空当前页后自动回退一页，不留在空页上', async () => {
    mocks.memoryList.mockResolvedValueOnce(
      memoryPage({
        meta: { page: 1, page_size: 20, total: 21, total_pages: 2 },
      }),
    );
    mocks.memoryList.mockResolvedValue(
      memoryPage({
        items: [],
        meta: { page: 2, page_size: 20, total: 21, total_pages: 2 },
      }),
    );
    const { container, unmount } = renderWithClient(<MemoriesPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('你习惯用中文写周报');
      });
    });

    click(container.querySelector('button[aria-label="下一页"]'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.memoryList).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }));
      });
    });
    // 第 2 页被删空（还有 21 条在前页）→ 自动回退第 1 页并恢复列表
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('你习惯用中文写周报');
      });
    });
    expect(container.textContent).toContain('第 1 / 2 页');
    unmount();
  });
});
