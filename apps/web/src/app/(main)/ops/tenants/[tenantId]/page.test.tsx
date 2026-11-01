/**
 * 前端补测第三十一批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：运营-企业详情页（OPS2）的展示合同 ——
 * 三态与返回入口、页头最小可见口径（名称回落标识、停用状态与原因、
 * 部署方式）、基本信息（套餐名回落、席位、账期、自动续费、在册成员）、
 * 用量摘要（账期与使用比例）、近期订单双形态（空态与列表回落口径）。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatCents, formatDate, formatPercent } from '@/lib/format';
import { act, renderComponent } from '@/test/render-hook';

import OpsTenantDetailPage from './page';

const mocks = vi.hoisted(() => ({
  getTenant: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useParams: () => ({ tenantId: 't_1' }) }));

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
  opsApi: { getTenant: mocks.getTenant },
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

const DETAIL = {
  tenant: {
    tenant_id: 't_1',
    name: '华农科技',
    status: 'suspended',
    deploy_mode: '私有化部署 · 轻量版',
    suspended_reason: '欠费停用',
    created_at: '2026-10-01T00:00:00Z',
  },
  subscription: {
    plan_name: '专业版',
    plan_code: 'professional',
    seats_used: 3,
    seats_total: 10,
    current_period_end: '2026-11-30T00:00:00Z',
    auto_renew: true,
  },
  usage: {
    period: '2026-11',
    quota_units_used: 85400,
    quota_units_total: 100000,
    usage_percent: 85.4,
    member_count: 7,
  },
  recent_orders: [
    {
      order_id: 'ord_1',
      order_type: 'upgrade',
      plan_code: 'professional',
      seats: 10,
      amount_cents: 29700,
      status: 'paid',
      created_at: '2026-10-31T10:00:00Z',
    },
  ],
};

/** 显式传 null 表示「空体」 */
function mockDetail(value: unknown = DETAIL): void {
  mocks.getTenant.mockResolvedValue(value);
}

describe('运营-企业详情', () => {
  it('三态齐备：加载、失败可重试、空体按失败兜底', async () => {
    mocks.getTenant.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<OpsTenantDetailPage />);
    expect(loading.container.textContent).toContain('正在加载企业详情…');
    loading.unmount();

    mocks.getTenant.mockClear();
    mockDetail();
    mocks.getTenant.mockRejectedValue(new Error('企业详情暂时取不回来'));
    const failed = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(failed.container, '企业详情暂时取不回来');
    // 失败也保留返回入口
    const back = Array.from(failed.container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('返回企业列表'),
    );
    expect(back?.getAttribute('href')).toBe('/ops/tenants');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getTenant).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mockDetail(null);
    const empty = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(empty.container, '重试');
    empty.unmount();
  });

  it('页头：名称回落标识，停用状态与原因照实说', async () => {
    mockDetail();
    const { container, unmount } = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(container, '华农科技');

    expect(container.textContent).toContain('已停用');
    expect(container.textContent).toContain('部署方式：私有化部署 · 轻量版');
    expect(container.textContent).toContain('停用原因：欠费停用');
    unmount();

    mockDetail({
      ...DETAIL,
      tenant: {
        ...DETAIL.tenant,
        name: '',
        status: 'active',
        suspended_reason: '',
      },
    });
    const fallback = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(fallback.container, 't_1');
    expect(fallback.container.textContent).toContain('正常');
    // 无停用原因不渲染该行
    expect(fallback.container.textContent).not.toContain('停用原因');
    fallback.unmount();
  });

  it('基本信息：套餐名回落档位，自动续费照实说', async () => {
    mockDetail({
      ...DETAIL,
      subscription: { ...DETAIL.subscription, plan_name: '', auto_renew: false },
    });
    const { container, unmount } = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(container, '基本信息');

    expect(container.textContent).toContain('professional');
    expect(container.textContent).toContain('3 / 10');
    expect(container.textContent).toContain(formatDate('2026-11-30T00:00:00Z'));
    expect(container.textContent).toContain('已关闭');
    expect(container.textContent).toContain(formatDate('2026-10-01T00:00:00Z'));
    expect(container.textContent).toContain('7');
    unmount();
  });

  it('用量摘要只给聚合数字，含账期与使用比例', async () => {
    mockDetail();
    const { container, unmount } = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(container, '用量摘要');

    expect(container.textContent).toContain('仅聚合数字，不含任何内容信息');
    expect(container.textContent).toContain('账期 2026-11');
    expect(container.textContent).toContain(`使用比例 ${formatPercent(85.4)}`);
    unmount();
  });

  it('近期订单：空态与列表双形态', async () => {
    mockDetail({ ...DETAIL, recent_orders: [] });
    const empty = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(empty.container, '该企业还没有订单');
    expect(empty.container.textContent).toContain('可到订单管理处理核销');
    empty.unmount();

    mockDetail();
    const listed = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(listed.container, 'ord_1');
    expect(listed.container.textContent).toContain(formatCents(29700));
    expect(listed.container.textContent).toContain('upgrade');
    listed.unmount();
  });

  it('订单字段缺失时按占位回落，不露空格', async () => {
    mockDetail({
      ...DETAIL,
      recent_orders: [
        {
          order_id: 'ord_2',
          order_type: '',
          plan_code: '',
          seats: 1,
          amount_cents: 0,
          status: 'pending_payment',
          created_at: '2026-10-30T10:00:00Z',
        },
      ],
    });
    const { container, unmount } = renderWithClient(<OpsTenantDetailPage />);
    await waitForText(container, 'ord_2');

    // 类型与套餐缺省回落占位符
    expect(container.textContent).toContain('—');
    expect(container.textContent).toContain(formatCents(0));
    unmount();
  });
});
