/**
 * 前端补测第十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：计费域展示组件的渲染合同 —— PlanCompare 当前套餐高亮/下架/权限
 * 与「前端只做展示换算不硬编码额度」、UsageCharts 无数据空态与图表标注。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PlanItem, UsageBreakdownItem, UsageDailyTrendItem } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { OrderDrawer } from './order-drawer';
import { PlanCompare } from './plan-compare';
import { UsageCharts } from './usage-charts';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  createOrder: vi.fn(),
  /** 幂等键作用域的替身（P1-12）：透传执行，但能断言「重试拿到了同一把键」 */
  withIdempotencyKey: vi.fn((...args: [string, () => Promise<unknown>]) => args[1]()),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  withIdempotencyKey: mocks.withIdempotencyKey,
  billingApi: { createOrder: mocks.createOrder },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

import { billingApi } from '@/lib/api-client';

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

function clickButton(container: HTMLElement, label: string): void {
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

const PRO_PLAN: PlanItem = {
  plan_code: 'pro',
  display_name: '专业版',
  price_per_seat_month_cents: 9900,
  currency: 'CNY',
  min_seats: 1,
  quota_units_per_seat_month: 500_000,
  quota_unit_label: '字',
  purchasable_quota: true,
  features: [
    { feature_key: 'kb', display_name: '知识库问答', included: true, limit: null },
    { feature_key: 'seats', display_name: '团队协作', included: true, limit: 50 },
    { feature_key: 'plugin', display_name: '插件能力', included: false, limit: null },
  ],
  status: 'available',
  trial_days: 7,
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // 抽屉/对话框走 portal 挂在 body 上，unmount 不回收，必须显式清理
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------
// PlanCompare
// ---------------------------------------------------------------------------

describe('PlanCompare', () => {
  it('展示套餐名、价格换算与额度换算（分→元、字→万字）', () => {
    const { container, unmount } = renderComponent(
      <PlanCompare plans={[PRO_PLAN]} currentPlanCode="free" />,
    );
    expect(container.textContent).toContain('专业版');
    expect(container.textContent).toContain('¥99.00');
    expect(container.textContent).toContain('/席位/月');
    expect(container.textContent).toContain('50 万字');
    expect(container.textContent).toContain('可试用 7 天');
    unmount();
  });

  it('当前套餐高亮并置灰按钮', () => {
    const onChoose = vi.fn();
    const { container, unmount } = renderComponent(
      <PlanCompare plans={[PRO_PLAN]} currentPlanCode="pro" onChoose={onChoose} />,
    );
    expect(container.textContent).toContain('当前套餐');

    clickButton(container, '当前套餐');
    expect(onChoose).not.toHaveBeenCalled();
    unmount();
  });

  it('非当前套餐可选择', () => {
    const onChoose = vi.fn();
    const { container, unmount } = renderComponent(
      <PlanCompare plans={[PRO_PLAN]} currentPlanCode="free" onChoose={onChoose} />,
    );
    clickButton(container, '选择该套餐');
    expect(onChoose).toHaveBeenCalledWith(PRO_PLAN);
    unmount();
  });

  it('已下架套餐按钮置灰并标注', () => {
    const onChoose = vi.fn();
    const { container, unmount } = renderComponent(
      <PlanCompare
        plans={[{ ...PRO_PLAN, status: 'retired' }]}
        currentPlanCode="free"
        onChoose={onChoose}
      />,
    );
    clickButton(container, '该套餐已下架');
    expect(onChoose).not.toHaveBeenCalled();
    unmount();
  });

  it('无购买权限时选择置灰', () => {
    const onChoose = vi.fn();
    const { container, unmount } = renderComponent(
      <PlanCompare
        plans={[PRO_PLAN]}
        currentPlanCode="free"
        onChoose={onChoose}
        canPurchase={false}
      />,
    );
    const button = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('选择该套餐'),
    );
    expect(button?.disabled).toBe(true);
    unmount();
  });

  it('功能项区分包含与不含，上限只对包含项展示', () => {
    const { container, unmount } = renderComponent(
      <PlanCompare plans={[PRO_PLAN]} currentPlanCode="free" />,
    );
    expect(container.textContent).toContain('知识库问答');
    expect(container.textContent).toContain('团队协作');
    expect(container.textContent).toContain('（上限 50）');
    // 不含项照常列出（灰色表达），不硬删
    expect(container.textContent).toContain('插件能力');
    // 不限项不带「上限」字样
    const unlimited = container.textContent ?? '';
    expect(unlimited).not.toContain('（上限 ）');
    unmount();
  });

  it('套餐名缺失时用标识兜底', () => {
    const { container, unmount } = renderComponent(
      <PlanCompare plans={[{ ...PRO_PLAN, display_name: '' }]} currentPlanCode="free" />,
    );
    expect(container.textContent).toContain('pro');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// UsageCharts
// ---------------------------------------------------------------------------

const TREND: UsageDailyTrendItem[] = [
  { date: '2026-10-30', units: 1200 },
  { date: '2026-10-31', units: 3600 },
];

const BREAKDOWN: UsageBreakdownItem[] = [
  { key: 'member_1', display_name: '张三', units: 3000, percent: 62.5 },
  { key: 'member_2', display_name: '', units: 1800, percent: 37.5 },
];

describe('UsageCharts', () => {
  it('无数据时给空态，不渲染空白区域', () => {
    const { container, unmount } = renderComponent(<UsageCharts breakdown={[]} dailyTrend={[]} />);
    expect(container.textContent).toContain('这段时间还没有用量');
    expect(container.textContent).toContain('还没有分项数据');
    expect(container.querySelector('[aria-label="按日用量柱状图"]')).toBeNull();
    unmount();
  });

  it('按日趋势渲染柱状图并标注日期', () => {
    const { container, unmount } = renderComponent(
      <UsageCharts breakdown={BREAKDOWN} dailyTrend={TREND} />,
    );
    expect(container.querySelector('[aria-label="按日用量柱状图"]')).not.toBeNull();
    expect(container.textContent).toContain('10-30');
    expect(container.textContent).toContain('10-31');
    unmount();
  });

  it('分项展示名称、用量与占比；名称缺失占位', () => {
    const { container, unmount } = renderComponent(
      <UsageCharts breakdown={BREAKDOWN} dailyTrend={TREND} />,
    );
    expect(container.textContent).toContain('张三');
    expect(container.textContent).toContain('未命名分项');
    expect(container.textContent).toContain('62.5%');
    unmount();
  });

  it('区间合计与统计口径同源标注', () => {
    const { container, unmount } = renderComponent(
      <UsageCharts breakdown={BREAKDOWN} dailyTrend={TREND} />,
    );
    // 1200 + 3600 = 4800 字
    expect(container.textContent).toContain('4,800 字');
    expect(container.textContent).toContain('统计口径');
    unmount();
  });

  it('额度单位文案由外部传入（前端不硬编码）', () => {
    const { container, unmount } = renderComponent(
      <UsageCharts breakdown={BREAKDOWN} dailyTrend={TREND} quotaUnitLabel="次" />,
    );
    expect(container.textContent).toContain('单位 次');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// OrderDrawer
// ---------------------------------------------------------------------------

describe('OrderDrawer', () => {
  it('未选择套餐时给空态', () => {
    const { unmount } = renderWithClient(<OrderDrawer open onOpenChange={vi.fn()} plan={null} />);
    expect(document.body.textContent).toContain('还没有选择套餐');
    unmount();
  });

  it('表单步展示套餐、单价与应付金额（席位联动）', () => {
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    expect(document.body.textContent).toContain('专业版');
    expect(document.body.textContent).toContain('¥99.00');
    // 3 席 × 99 元 = 297 元
    expect(document.body.textContent).toContain('¥297.00');
    unmount();
  });

  it('确认下单先进二次确认', async () => {
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    expect(mocks.createOrder).not.toHaveBeenCalled();
    unmount();
  });

  it('确认后按席位与支付方式下单', async () => {
    vi.mocked(billingApi.createOrder).mockResolvedValue({
      order_id: 'ord_9',
      amount_cents: 29700,
      currency: 'CNY',
      status: 'pending',
      payment_method: 'online',
      pay_url: 'https://pay.example/ord_9',
      bank_info: null,
      expires_at: '2026-11-02T00:00:00Z',
    });
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });

    // Drawer 与确认框两个 [role=dialog] 并存，「提交订单」全局唯一
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(billingApi.createOrder).toHaveBeenCalledWith(
          expect.objectContaining({ plan_code: 'pro', seats: 3 }),
        );
      });
    });
    unmount();
  });

  it('下单成功展示支付指引（在线支付给入口）', async () => {
    vi.mocked(billingApi.createOrder).mockResolvedValue({
      order_id: 'ord_9',
      amount_cents: 29700,
      currency: 'CNY',
      status: 'pending',
      payment_method: 'online',
      pay_url: 'https://pay.example/ord_9',
      bank_info: null,
      expires_at: '2026-11-02T00:00:00Z',
    });
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    // Drawer 与确认框两个 [role=dialog] 并存，「提交订单」全局唯一
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('订单已创建');
      });
    });
    expect(document.body.textContent).toContain('ord_9');
    expect(document.body.textContent).toContain('去支付');
    unmount();
  });

  it('对公转账展示收款信息', async () => {
    vi.mocked(billingApi.createOrder).mockResolvedValue({
      order_id: 'ord_9',
      amount_cents: 29700,
      currency: 'CNY',
      status: 'pending',
      payment_method: 'offline_transfer',
      pay_url: '',
      bank_info: { 开户行: '示例银行', 账号: '6222****' },
      expires_at: '2026-11-02T00:00:00Z',
    });
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    // Drawer 与确认框两个 [role=dialog] 并存，「提交订单」全局唯一
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('对公转账信息');
      });
    });
    expect(document.body.textContent).toContain('示例银行');
    unmount();
  });

  it('下单失败展示业务文案与重试', async () => {
    vi.mocked(billingApi.createOrder).mockRejectedValue(new Error('已有待支付订单'));
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    // Drawer 与确认框两个 [role=dialog] 并存，「提交订单」全局唯一
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('重试');
      });
    });
    expect(mocks.toast.error).toHaveBeenCalled();
    unmount();
  });

  it('下单失败点重试复用同一把幂等键，改席位才换新键（P1-12）', async () => {
    vi.mocked(billingApi.createOrder).mockRejectedValue(new Error('服务暂时不可用'));
    const { unmount } = renderWithClient(
      <OrderDrawer open onOpenChange={vi.fn()} plan={PRO_PLAN} defaultSeats={3} />,
    );
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createOrder).toHaveBeenCalledTimes(1);
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('重试');
      });
    });

    // 重试 = 同一笔下单动作：键必须复用，否则「已扣款但响应丢失」会被重试成第二笔订单
    await act(async () => {
      clickButton(document.body, '重试');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createOrder).toHaveBeenCalledTimes(2);
      });
    });

    const keys = mocks.withIdempotencyKey.mock.calls.map((call) => call[0] as string);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(keys[1]).toBe(keys[0]);

    // 改席位 = 另一次动作：必须换新键，否则后端会把「加席」当成上一笔的重复投递
    const seatsInput = document.body.querySelector<HTMLInputElement>('#order-seats');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(seatsInput, '5');
      seatsInput?.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      clickButton(document.body, '确认下单');
    });
    await act(async () => {
      clickButton(document.body, '提交订单');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createOrder).toHaveBeenCalledTimes(3);
      });
    });

    const afterChange = mocks.withIdempotencyKey.mock.calls.map((call) => call[0] as string);
    expect(afterChange).toHaveLength(3);
    expect(afterChange[2]).not.toBe(keys[0]);
    unmount();
  });
});
