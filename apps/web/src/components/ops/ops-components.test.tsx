/**
 * 前端补测第十八批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：运营域组件的渲染合同 —— ConfirmOrderDialog 两步核销
 * （表单校验 → 二次确认念单 → 提交），核销立即生效不可撤回因此
 * 必须二次确认。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { OpsOrderItem } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { ConfirmOrderDialog } from './confirm-order-dialog';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  confirmOrder: vi.fn(),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  opsApi: { confirmOrder: mocks.confirmOrder },
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

import { opsApi } from '@/lib/api-client';

const ORDER: OpsOrderItem = {
  order_id: 'ord_1',
  tenant_id: 'tnt_1',
  tenant_name: '示例科技',
  order_type: 'new',
  plan_code: 'pro',
  seats: 5,
  amount_cents: 9900,
  status: 'pending',
  payment_method: 'offline_transfer',
  created_at: '2026-11-01T09:00:00Z',
  paid_at: '',
};

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement>(selector);
  act(() => {
    if (field) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

function clickButton(container: ParentNode, label: string): void {
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

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('ConfirmOrderDialog', () => {
  it('打开时展示订单摘要并回填应付金额（分→元）', () => {
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    expect(document.body.textContent).toContain('ord_1');
    expect(document.body.textContent).toContain('示例科技');
    expect(document.body.textContent).toContain('¥99.00');

    const amount = document.body.querySelector<HTMLInputElement>('#confirm-order-amount');
    expect(amount?.value).toBe('99.00');
    unmount();
  });

  it('金额为空时拦截并提示', async () => {
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    typeInto(document.body, '#confirm-order-amount', '');
    typeInto(document.body, '#confirm-order-remark', '已到账');
    await act(async () => {
      clickButton(document.body, '下一步');
    });
    expect(document.body.textContent).toContain('请填写实收金额');
    expect(opsApi.confirmOrder).not.toHaveBeenCalled();
    unmount();
  });

  it('备注为空时拦截并提示', async () => {
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    typeInto(document.body, '#confirm-order-remark', '');
    await act(async () => {
      clickButton(document.body, '下一步');
    });
    expect(document.body.textContent).toContain('请填写核销备注，便于后续对账与追溯');
    unmount();
  });

  it('校验通过进入二次确认（核销不可撤回）', async () => {
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    typeInto(document.body, '#confirm-order-remark', '对公转账已到账');
    await act(async () => {
      clickButton(document.body, '下一步');
    });
    expect(document.body.textContent).toContain('确认核销这笔订单？');
    expect(opsApi.confirmOrder).not.toHaveBeenCalled();
    unmount();
  });

  it('确认后按「元→分」提交金额与备注', async () => {
    vi.mocked(opsApi.confirmOrder).mockResolvedValue({
      order_id: 'ord_1',
      status: 'paid',
      subscription_status: 'active',
    });
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    typeInto(document.body, '#confirm-order-amount', '98.50');
    typeInto(document.body, '#confirm-order-remark', '手续费差额');
    await act(async () => {
      clickButton(document.body, '下一步');
    });

    const dialog = document.body.querySelector('[role="dialog"]');
    await act(async () => {
      clickButton(dialog ?? document.body, '确认核销');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(opsApi.confirmOrder).toHaveBeenCalledWith('ord_1', {
          paid_amount_cents: 9850,
          remark: '手续费差额',
        });
      });
    });
    unmount();
  });

  it('核销成功提示已开通并回调', async () => {
    vi.mocked(opsApi.confirmOrder).mockResolvedValue({
      order_id: 'ord_1',
      status: 'paid',
      subscription_status: 'active',
    });
    const onConfirmed = vi.fn();
    const onOpenChange = vi.fn();
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog
        open
        onOpenChange={onOpenChange}
        order={ORDER}
        onConfirmed={onConfirmed}
      />,
    );
    typeInto(document.body, '#confirm-order-remark', '已到账');
    await act(async () => {
      clickButton(document.body, '下一步');
    });
    const dialog = document.body.querySelector('[role="dialog"]');
    await act(async () => {
      clickButton(dialog ?? document.body, '确认核销');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('核销完成，该企业套餐已开通');
      });
    });
    expect(onConfirmed).toHaveBeenCalledWith(expect.objectContaining({ order_id: 'ord_1' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    unmount();
  });

  it('核销失败以业务文案提示', async () => {
    vi.mocked(opsApi.confirmOrder).mockRejectedValue(new Error('金额与应付不一致'));
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={ORDER} />,
    );
    typeInto(document.body, '#confirm-order-remark', '已到账');
    await act(async () => {
      clickButton(document.body, '下一步');
    });
    const dialog = document.body.querySelector('[role="dialog"]');
    await act(async () => {
      clickButton(dialog ?? document.body, '确认核销');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalled();
      });
    });
    unmount();
  });

  it('无待核销订单时不渲染表单', () => {
    const { unmount } = renderWithClient(
      <ConfirmOrderDialog open onOpenChange={vi.fn()} order={null} />,
    );
    expect(document.body.querySelector('#confirm-order-amount')).toBeNull();
    unmount();
  });
});
