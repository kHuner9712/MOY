/**
 * 前端补测第十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：计费域展示组件的渲染合同 —— PlanCompare 当前套餐高亮/下架/权限
 * 与「前端只做展示换算不硬编码额度」、UsageCharts 无数据空态与图表标注。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PlanItem, UsageBreakdownItem, UsageDailyTrendItem } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { PlanCompare } from './plan-compare';
import { UsageCharts } from './usage-charts';

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
