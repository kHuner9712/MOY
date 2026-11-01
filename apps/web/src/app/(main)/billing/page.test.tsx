/**
 * 前端补测第二十三批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：套餐与账单页（P-02）的编排合同 ——
 * 额度 80% 阈值提醒、订阅摘要与待生效切换提示、用量卡超额标红、
 * 「选套餐」三分流（免费→新开订单 / 付费→升级订单 / 更便宜→降级二次确认），
 * 以及订单 / 账单两卡的三态与下载入口。
 *
 * PlanCompare / OrderDrawer / UsageCharts 的内部合同已由补测第十六、十八批覆盖，
 * 本批用最小替身回显关键 props，只测页面自身的分流与状态编排。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatCents, formatDate, formatPercent } from '@/lib/format';
import { act, renderComponent } from '@/test/render-hook';

import BillingPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  listPlans: vi.fn(),
  getSubscription: vi.fn(),
  getUsage: vi.fn(),
  listOrders: vi.fn(),
  listInvoices: vi.fn(),
  changeSubscription: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  billingApi: {
    listPlans: mocks.listPlans,
    getSubscription: mocks.getSubscription,
    getUsage: mocks.getUsage,
    listOrders: mocks.listOrders,
    listInvoices: mocks.listInvoices,
    changeSubscription: mocks.changeSubscription,
  },
}));

// 子组件用最小替身：回显分流结果（订单抽屉开没开、开的是哪种），
// 「选择」按钮直接把套餐回传给页面的 handleChoosePlan。
vi.mock('@/components/billing/plan-compare', async () => {
  const react = await import('react');
  return {
    PlanCompare: (props: {
      plans: Array<{ plan_code: string; display_name: string }>;
      currentPlanCode: string;
      onChoose: (plan: unknown) => void;
    }) =>
      react.createElement(
        'div',
        null,
        props.plans.map((plan) =>
          react.createElement(
            'button',
            {
              key: plan.plan_code,
              type: 'button',
              'data-current': String(plan.plan_code === props.currentPlanCode),
              onClick: () => props.onChoose(plan),
            },
            `选择${plan.display_name}`,
          ),
        ),
      ),
  };
});

vi.mock('@/components/billing/order-drawer', async () => {
  const react = await import('react');
  return {
    OrderDrawer: (props: {
      open: boolean;
      plan: { plan_code: string } | null;
      orderType: string;
    }) =>
      props.open
        ? react.createElement('div', null, `订单抽屉 ${props.plan?.plan_code} ${props.orderType}`)
        : null,
  };
});

vi.mock('@/components/billing/usage-charts', async () => {
  const react = await import('react');
  return {
    UsageCharts: () => react.createElement('div', null, '用量明细图表'),
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

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找 */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

const PLAN_FREE = {
  plan_code: 'free',
  display_name: '免费版',
  price_per_seat_month_cents: 0,
  currency: 'CNY',
  min_seats: 1,
  quota_units_per_seat_month: 10000,
  quota_unit_label: '字',
  purchasable_quota: false,
  features: [],
  status: 'active',
  trial_days: 0,
};

const PLAN_PRO = {
  ...PLAN_FREE,
  plan_code: 'professional',
  display_name: '专业版',
  price_per_seat_month_cents: 9900,
  quota_units_per_seat_month: 100000,
};

const PLAN_ENT = {
  ...PLAN_FREE,
  plan_code: 'enterprise',
  display_name: '企业版',
  price_per_seat_month_cents: 29900,
  quota_units_per_seat_month: 500000,
};

const SUBSCRIPTION = {
  subscription_id: 'sub_1',
  plan_code: 'professional',
  plan_name: '专业版',
  status: 'active',
  seats_total: 10,
  seats_used: 3,
  started_at: '2026-08-01T00:00:00Z',
  current_period_end: '2026-11-30T00:00:00Z',
  quota_units_total: 1000000,
  quota_units_used: 85400,
  auto_renew: true,
  pending_change: null,
};

const USAGE = {
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

const ORDER = {
  order_id: 'ord_1',
  order_type: 'upgrade',
  plan_code: 'professional',
  seats: 10,
  amount_cents: 29700,
  status: 'paid',
  payment_method: 'online',
  created_at: '2026-10-31T10:00:00Z',
};

const INVOICE = {
  invoice_id: 'inv_1',
  period: '2026-10',
  amount_cents: 29700,
  status: 'issued',
  download_url: 'https://files.example.com/inv_1.pdf',
};

/** 五路查询的默认数据（各测试按需覆盖其中一路；显式传 null 表示「空体」） */
function mockBillingData(
  overrides: {
    subscription?: unknown;
    usage?: unknown;
    plans?: unknown;
    orders?: unknown;
    invoices?: unknown;
  } = {},
): void {
  // 不能用 `??`：测试要能显式传 null（后端空体），而 null 会被 `??` 吞掉
  const pick = (key: keyof typeof overrides, fallback: unknown): unknown =>
    key in overrides ? overrides[key] : fallback;

  mocks.listPlans.mockResolvedValue(pick('plans', { plans: [PLAN_FREE, PLAN_PRO, PLAN_ENT] }));
  mocks.getSubscription.mockResolvedValue(pick('subscription', SUBSCRIPTION));
  mocks.getUsage.mockResolvedValue(pick('usage', USAGE));
  mocks.listOrders.mockResolvedValue(
    pick('orders', {
      items: [ORDER],
      meta: { page: 1, page_size: 5, total: 1, total_pages: 1 },
    }),
  );
  mocks.listInvoices.mockResolvedValue(pick('invoices', { items: [INVOICE] }));
}

/** 等按钮真正渲染出来再点（数据没落定就点会静默点空） */
async function clickWhenReady(root: ParentNode, text: string): Promise<void> {
  await act(async () => {
    await vi.waitFor(() => {
      expect(findButton(root, text)).toBeDefined();
    });
  });
  click(findButton(root, text));
}

describe('套餐与账单页', () => {
  it('额度达到阈值给顶部提醒，低于阈值不打扰', async () => {
    mockBillingData();
    const first = renderWithClient(<BillingPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(first.container.textContent).toContain('本月额度已用');
      });
    });
    expect(first.container.textContent).toContain(formatPercent(85.4));
    expect(first.container.textContent).toContain('升级套餐可恢复完整能力');
    first.unmount();

    mockBillingData({ usage: { ...USAGE, usage_percent: 30 } });
    const second = renderWithClient(<BillingPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(second.container.textContent).toContain('当前套餐');
      });
    });
    expect(second.container.textContent).not.toContain('本月额度已用');
    second.unmount();
  });

  it('订阅摘要展示席位、账期与自动续费', async () => {
    mockBillingData();
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('专业版');
      });
    });
    expect(container.textContent).toContain('3 / 10');
    expect(container.textContent).toContain(formatDate('2026-11-30T00:00:00Z'));
    expect(container.textContent).toContain(formatDate('2026-08-01T00:00:00Z'));
    expect(container.textContent).toContain('已开启');
    expect(container.textContent).toContain('席位使用');
    unmount();
  });

  it('待生效切换与自动续费关闭都照实说', async () => {
    mockBillingData({
      subscription: {
        ...SUBSCRIPTION,
        auto_renew: false,
        pending_change: { plan_code: 'enterprise', effective_at: '2026-12-01T00:00:00Z' },
      },
    });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('已安排在');
      });
    });
    expect(container.textContent).toContain('切换到');
    expect(container.textContent).toContain('enterprise');
    expect(container.textContent).toContain('已关闭');
    unmount();
  });

  it('订阅加载失败给业务文案并可原地重试', async () => {
    mockBillingData();
    mocks.getSubscription.mockRejectedValue(new Error('订阅信息暂时取不回来'));
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('订阅信息暂时取不回来');
      });
    });
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getSubscription).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('没有订阅信息时空态引导开通', async () => {
    mockBillingData({ subscription: null });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有订阅信息');
      });
    });
    expect(container.textContent).toContain('免费版可随时升级');
    unmount();
  });

  it('额度卡展示账期进度，超额时明说已超出', async () => {
    mockBillingData({ usage: { ...USAGE, over_quota_warning: true } });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('账期 2026-11');
      });
    });
    expect(container.textContent).toContain(`剩余 ${(14600).toLocaleString('zh-CN')}`);
    expect(container.textContent).toContain('已超出当前额度');
    // 用量明细卡拿到同一份数据
    expect(container.textContent).toContain('用量明细图表');
    unmount();
  });

  it('没有用量时额度与明细两卡都给空态', async () => {
    mockBillingData({ usage: null });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有用量记录');
      });
    });
    expect(container.textContent).toContain('还没有用量明细');
    unmount();
  });

  it('套餐目录为空时说明由平台统一维护', async () => {
    mockBillingData({ plans: { plans: [] } });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('暂时没有可购买的套餐');
      });
    });
    expect(container.textContent).toContain('请联系平台管理员确认上下架状态');
    unmount();
  });

  it('免费版用户选套餐走新开订单', async () => {
    mockBillingData({
      subscription: { ...SUBSCRIPTION, plan_code: 'free', plan_name: '' },
    });
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择专业版');

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('订单抽屉 professional new');
      });
    });
    // 免费版不算降级，不该弹降级确认
    expect(document.body.textContent).not.toContain('确认降级套餐？');
    unmount();
  });

  it('付费用户选更贵套餐走升级订单', async () => {
    mockBillingData();
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择企业版');

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('订单抽屉 enterprise upgrade');
      });
    });
    unmount();
  });

  it('选更便宜的套餐走降级二次确认，写明目标与席位', async () => {
    mockBillingData();
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择免费版');

    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('确认降级套餐？');
      });
    });
    expect(document.body.textContent).toContain('降级在当前账期结束后生效');
    expect(document.body.textContent).toContain('目标套餐：免费版');
    expect(document.body.textContent).toContain('席位 10 个');
    // 降级不走下单抽屉
    expect(container.textContent).not.toContain('订单抽屉');
    unmount();
  });

  it('确认降级提交变更并给周期末生效提示条', async () => {
    mockBillingData();
    mocks.changeSubscription.mockResolvedValue({
      subscription_id: 'sub_1',
      status: 'active',
      effective_at: '2026-12-01T00:00:00Z',
      order_id: '',
      over_quota_warning: false,
      notice_code: 15005,
      notice_message: '',
    });
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择免费版');
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('确认降级套餐？');
      });
    });
    click(dialogButton('确认降级'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.changeSubscription).toHaveBeenCalledWith({
          plan_code: 'free',
          seats: 10,
          action: 'downgrade',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('降级将在当前账期结束后生效');
      });
    });
    // 后端没给 notice_message 时兜底文案要带生效日期
    expect(container.textContent).toContain(formatDate('2026-12-01T00:00:00Z'));
    // 降级是正常业务结果：不弹错误
    expect(mocks.toast.error).not.toHaveBeenCalled();
    unmount();
  });

  it('降级提示优先用后端文案', async () => {
    mockBillingData();
    mocks.changeSubscription.mockResolvedValue({
      subscription_id: 'sub_1',
      status: 'active',
      effective_at: '2026-12-01T00:00:00Z',
      order_id: '',
      over_quota_warning: false,
      notice_code: 15005,
      notice_message: '已为你保留当前额度到本账期结束',
    });
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择免费版');
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('确认降级套餐？');
      });
    });
    click(dialogButton('确认降级'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('已为你保留当前额度到本账期结束');
      });
    });
    unmount();
  });

  it('降级失败以业务文案提示', async () => {
    mockBillingData();
    mocks.changeSubscription.mockRejectedValue(new Error('当前有进行中的订单，请先完成支付'));
    const { container, unmount } = renderWithClient(<BillingPage />);
    await clickWhenReady(container, '选择免费版');
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('确认降级套餐？');
      });
    });
    click(dialogButton('确认降级'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('当前有进行中的订单，请先完成支付');
      });
    });
    unmount();
  });

  it('最近订单表格展示金额与状态', async () => {
    mockBillingData();
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('ord_1');
      });
    });
    expect(container.textContent).toContain('仅展示最近 5 笔');
    expect(container.textContent).toContain('ord_1');
    expect(container.textContent).toContain(formatCents(29700));
    unmount();
  });

  it('账单区分可下载与待开具', async () => {
    mockBillingData({
      invoices: {
        items: [
          INVOICE,
          {
            invoice_id: 'inv_2',
            period: '2026-11',
            amount_cents: 29700,
            status: 'pending',
            download_url: '',
          },
        ],
      },
    });
    const { container, unmount } = renderWithClient(<BillingPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('已开具');
      });
    });
    const link = container.querySelector('a[href="https://files.example.com/inv_1.pdf"]');
    expect(link).not.toBeNull();
    expect(link?.getAttribute('target')).toBe('_blank');

    // 没有下载链接的账单禁用按钮，不给死链
    const disabled = Array.from(container.querySelectorAll('button')).filter(
      (item) => item.textContent?.includes('下载') && item.disabled,
    );
    expect(disabled.length).toBeGreaterThanOrEqual(1);
    unmount();
  });
});
