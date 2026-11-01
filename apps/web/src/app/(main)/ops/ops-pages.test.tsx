/**
 * 前端补测第二十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：运营域四个页面的渲染与写入合同 ——
 * 1. 平台统计：指标卡口径（前端不做二次汇总）、区间切换换参数、
 *    套餐分布与用量前列企业两块的双空态；
 * 2. 订单管理：核销按钮只对「待支付」开放、筛选参数拼装、双空态；
 * 3. 系统配置：表单回填、三项校验拦截、保存参数口径（round/trim）与
 *    「setQueryData 直更不再拉一次」合同、套餐上下架只读展示；
 * 4. 企业列表：最小可见口径、停用/恢复二次确认与参数、筛选与双空态。
 *
 * ConfirmOrderDialog 的内部合同已由补测第十八批覆盖，本批用最小替身
 * 回显「核销的是哪张订单」，只测页面自身的编排。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatCents, formatNumber, formatUnits } from '@/lib/format';
import { act, renderComponent } from '@/test/render-hook';

import OpsMetricsPage from './metrics/page';
import OpsOrdersPage from './orders/page';
import OpsSettingsPage from './settings/page';
import OpsTenantsPage from './tenants/page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  getMetrics: vi.fn(),
  listOrders: vi.fn(),
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  listTenants: vi.fn(),
  updateTenantStatus: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  opsApi: {
    getMetrics: mocks.getMetrics,
    listOrders: mocks.listOrders,
    getSettings: mocks.getSettings,
    updateSettings: mocks.updateSettings,
    listTenants: mocks.listTenants,
    updateTenantStatus: mocks.updateTenantStatus,
  },
}));

// next/link 依赖 App Router 上下文，jsdom 里没有；换成渲染 <a> 的最小替身。
vi.mock('next/link', async () => {
  const react = await import('react');
  return {
    default: ({ href, children }: { href: string; children?: unknown }) =>
      react.createElement('a', { href }, children as never),
  };
});

// 核销弹窗内部合同已测过，这里只回显「核销的是哪张订单」。
vi.mock('@/components/ops/confirm-order-dialog', async () => {
  const react = await import('react');
  return {
    ConfirmOrderDialog: (props: { open: boolean; order: { order_id: string } | null }) =>
      props.open ? react.createElement('div', null, `核销弹窗 ${props.order?.order_id}`) : null,
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

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找（行内也有「停用/恢复」） */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

/** 在受控输入框（input / textarea）里输入文本 */
function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  act(() => {
    if (field) {
      const proto =
        field instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
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

/**
 * 等「查询落定 → effect setForm → 二次提交」的表单渲染出来。
 *
 * 这类 effect 驱动的二次提交有两个坑：
 * 1. 在一个长 act 内部轮询永远观察不到（提交被压到 act 退出才落盘）；
 * 2. 在 act 外轮询能观察到，但会报「update not wrapped in act」警告。
 * 因此每拍在 act 内放行一拍再断言：既能看到提交，又保持无警告。
 */
async function waitForSettingsForm(container: HTMLElement): Promise<void> {
  // 先在 act 内放行一拍：把「查询落定 → setForm」两次提交收进 act，
  // 否则它们会落在渲染 act 与首次轮询之间，报 not-wrapped-in-act 警告
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await vi.waitFor(async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.querySelector('#quota-warn-threshold')).not.toBeNull();
  });
}

// ---------------------------------------------------------------------------
// 平台统计
// ---------------------------------------------------------------------------

const METRICS = {
  range: '7d',
  tenant_count: 1234,
  active_tenant_count: 567,
  active_user_count: 8900,
  conversation_count: 45678,
  document_count: 910,
  quota_units_total: 2500000,
  plan_distribution: [
    { plan_code: 'professional', plan_name: '专业版', tenant_count: 740 },
    { plan_code: 'legacy', plan_name: '', tenant_count: 494 },
  ],
  top_tenants: [
    {
      tenant_id: 't_1',
      name: '华农科技',
      plan_code: 'professional',
      quota_units_used: 123456,
    },
    { tenant_id: 't_2', name: '', plan_code: 'free', quota_units_used: 7890 },
  ],
};

describe('运营-平台统计', () => {
  it('指标卡直接取后端聚合值，切区间换参数', async () => {
    mocks.getMetrics.mockResolvedValue(METRICS);
    const { container, unmount } = renderWithClient(<OpsMetricsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('企业总数');
      });
    });
    expect(container.textContent).toContain(formatNumber(1234));
    expect(container.textContent).toContain('区间活跃企业');
    expect(container.textContent).toContain(formatNumber(8900));
    expect(container.textContent).toContain(formatUnits(2500000));
    expect(container.textContent).toContain('统计口径与账期用量同源');

    click(findButton(container, '近 30 天'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getMetrics).toHaveBeenCalledWith('30d');
      });
    });
    unmount();
  });

  it('套餐分布按档位展示数量与占比', async () => {
    mocks.getMetrics.mockResolvedValue(METRICS);
    const { container, unmount } = renderWithClient(<OpsMetricsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('套餐分布');
      });
    });
    expect(container.textContent).toContain('专业版');
    expect(container.textContent).toContain('740 家 · 60%');
    // 套餐名缺失时回落档位标识
    expect(container.textContent).toContain('legacy');
    expect(container.textContent).toContain('494 家 · 40%');
    unmount();
  });

  it('套餐分布与用量前列各有双空态', async () => {
    mocks.getMetrics.mockResolvedValue({
      ...METRICS,
      plan_distribution: [],
      top_tenants: [],
    });
    const { container, unmount } = renderWithClient(<OpsMetricsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有套餐分布数据');
      });
    });
    expect(container.textContent).toContain('该区间还没有用量');
    unmount();
  });

  it('用量前列展示企业名与聚合用量', async () => {
    mocks.getMetrics.mockResolvedValue(METRICS);
    const { container, unmount } = renderWithClient(<OpsMetricsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('华农科技');
      });
    });
    // 名称缺失回落企业标识
    expect(container.textContent).toContain('t_2');
    expect(container.textContent).toContain(formatUnits(123456));
    unmount();
  });

  it('统计加载失败可原地重试，无数据给空态', async () => {
    mocks.getMetrics.mockRejectedValue(new Error('统计服务暂时不可用'));
    const first = renderWithClient(<OpsMetricsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(first.container.textContent).toContain('统计服务暂时不可用');
      });
    });
    click(findButton(first.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getMetrics).toHaveBeenCalledTimes(2);
      });
    });
    first.unmount();

    mocks.getMetrics.mockResolvedValue(null);
    const second = renderWithClient(<OpsMetricsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(second.container.textContent).toContain('该区间还没有统计数据');
      });
    });
    second.unmount();
  });
});

// ---------------------------------------------------------------------------
// 订单管理
// ---------------------------------------------------------------------------

const OPS_ORDER = {
  order_id: 'ord_1',
  tenant_id: 't_1',
  tenant_name: '华农科技',
  order_type: 'upgrade',
  plan_code: 'professional',
  seats: 10,
  amount_cents: 29700,
  status: 'pending_payment',
  payment_method: 'online',
  created_at: '2026-10-31T10:00:00Z',
  paid_at: '',
};

function mockOrders(items: unknown[]): void {
  mocks.listOrders.mockResolvedValue({
    items,
    page: 1,
    page_size: 20,
    total: items.length,
    total_pages: 1,
  });
}

describe('运营-订单管理', () => {
  it('只有待支付订单可核销，其余置灰', async () => {
    mockOrders([OPS_ORDER, { ...OPS_ORDER, order_id: 'ord_2', tenant_name: '', status: 'paid' }]);
    const { container, unmount } = renderWithClient(<OpsOrdersPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('ord_1');
      });
    });
    expect(container.textContent).toContain(formatCents(29700));
    // 企业名缺失回落企业标识
    expect(container.textContent).toContain('t_1');

    const buttons = Array.from(container.querySelectorAll('button')).filter((item) =>
      item.textContent?.includes('核销'),
    );
    expect(buttons.map((item) => item.disabled)).toEqual([false, true]);
    unmount();
  });

  it('点核销把该订单交给核销弹窗', async () => {
    mockOrders([OPS_ORDER]);
    const { container, unmount } = renderWithClient(<OpsOrdersPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('ord_1');
      });
    });

    click(findButton(container, '核销'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('核销弹窗 ord_1');
      });
    });
    unmount();
  });

  it('企业标识去空白，状态筛选与分页换查询参数', async () => {
    mockOrders([OPS_ORDER]);
    const { container, unmount } = renderWithClient(<OpsOrdersPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listOrders).toHaveBeenCalledWith({
          tenant_id: '',
          status: '',
          page: 1,
          page_size: 20,
        });
      });
    });

    typeInto(container, 'input[aria-label="按企业标识筛选"]', '  t_9  ');
    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按订单状态筛选"]'),
      'paid',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listOrders).toHaveBeenCalledWith({
          tenant_id: 't_9',
          status: 'paid',
          page: 1,
          page_size: 20,
        });
      });
    });
    unmount();
  });

  it('双空态区分「还没有」与「筛选无结果」', async () => {
    mockOrders([]);
    const { container, unmount } = renderWithClient(<OpsOrdersPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有订单');
      });
    });

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按订单状态筛选"]'),
      'paid',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('没有符合条件的订单');
      });
    });
    unmount();
  });

  it('订单加载失败可原地重试', async () => {
    mocks.listOrders.mockRejectedValue(new Error('订单服务暂时不可用'));
    const { container, unmount } = renderWithClient(<OpsOrdersPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('订单服务暂时不可用');
      });
    });
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listOrders).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });
});

// ---------------------------------------------------------------------------
// 系统配置
// ---------------------------------------------------------------------------

const SETTINGS = {
  quota_warn_threshold: 80,
  trial_days: 14,
  quota_unit_label: '字数额度',
  announcement: '本周五晚维护',
  plan_status: { professional: 'available', legacy: 'off' },
  updated_by: 'ops_admin',
  updated_at: '2026-10-31T10:00:00Z',
};

describe('运营-系统配置', () => {
  it('加载与失败三态齐备', async () => {
    mocks.getSettings.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<OpsSettingsPage />);
    expect(loading.container.textContent).toContain('正在加载系统配置…');
    loading.unmount();

    // 清掉上面那次悬挂调用的计数，只看失败渲染 + 重试
    mocks.getSettings.mockClear();
    mocks.getSettings.mockRejectedValue(new Error('配置服务暂时不可用'));
    const failed = renderWithClient(<OpsSettingsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(failed.container.textContent).toContain('配置服务暂时不可用');
      });
    });
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getSettings).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mocks.getSettings.mockResolvedValue(null);
    const empty = renderWithClient(<OpsSettingsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(empty.container.textContent).toContain('还没有系统配置');
      });
    });
    empty.unmount();
  });

  it('表单回填现有配置并展示最近修改', async () => {
    mocks.getSettings.mockResolvedValue(SETTINGS);
    const { container, unmount } = renderWithClient(<OpsSettingsPage />);

    await waitForSettingsForm(container);
    expect(container.querySelector<HTMLInputElement>('#quota-warn-threshold')?.value).toBe('80');
    expect(container.querySelector<HTMLInputElement>('#trial-days')?.value).toBe('14');
    expect(container.querySelector<HTMLInputElement>('#quota-unit-label')?.value).toBe('字数额度');
    expect(container.querySelector<HTMLTextAreaElement>('#announcement')?.value).toBe(
      '本周五晚维护',
    );
    expect(container.textContent).toContain('最近修改：ops_admin');
    unmount();
  });

  it('三项校验各自拦截，不发保存请求', async () => {
    mocks.getSettings.mockResolvedValue(SETTINGS);
    const { container, unmount } = renderWithClient(<OpsSettingsPage />);
    await waitForSettingsForm(container);

    typeInto(container, '#quota-warn-threshold', '150');
    submitForm(container);
    await act(async () => {});
    expect(container.textContent).toContain('额度提醒比例需在 0-100 之间');

    typeInto(container, '#quota-warn-threshold', '80');
    typeInto(container, '#trial-days', '400');
    submitForm(container);
    await act(async () => {});
    expect(container.textContent).toContain('试用天数需在 0-365 之间');

    typeInto(container, '#trial-days', '14');
    typeInto(container, '#quota-unit-label', '   ');
    submitForm(container);
    await act(async () => {});
    expect(container.textContent).toContain('请填写额度单位文案');
    expect(mocks.updateSettings).not.toHaveBeenCalled();
    unmount();
  });

  it('保存按后端口径提交并直更缓存（不重复拉取）', async () => {
    mocks.getSettings.mockResolvedValue(SETTINGS);
    mocks.updateSettings.mockResolvedValue({
      ...SETTINGS,
      quota_warn_threshold: 91,
      quota_unit_label: '万字',
    });
    const { container, unmount } = renderWithClient(<OpsSettingsPage />);
    await waitForSettingsForm(container);

    typeInto(container, '#quota-warn-threshold', '90.6');
    typeInto(container, '#quota-unit-label', '  万字  ');
    typeInto(container, '#announcement', '  新公告  ');
    submitForm(container);

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateSettings).toHaveBeenCalledWith({
          quota_warn_threshold: 91,
          trial_days: 14,
          quota_unit_label: '万字',
          announcement: '新公告',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('系统配置已保存');
      });
    });
    // 设计约定：保存后 setQueryData 直更，不再发一次配置查询
    expect(mocks.getSettings).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('保存失败以业务文案提示', async () => {
    mocks.getSettings.mockResolvedValue(SETTINGS);
    mocks.updateSettings.mockRejectedValue(new Error('没有平台配置权限'));
    const { container, unmount } = renderWithClient(<OpsSettingsPage />);
    await waitForSettingsForm(container);

    submitForm(container);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('没有平台配置权限');
      });
    });
    unmount();
  });

  it('套餐上下架只读展示，空时也有话可说', async () => {
    mocks.getSettings.mockResolvedValue(SETTINGS);
    const first = renderWithClient(<OpsSettingsPage />);
    await waitForSettingsForm(first.container);
    expect(first.container.textContent).toContain('上下架在套餐目录页由产品配置');
    expect(first.container.textContent).toContain('已在架');
    expect(first.container.textContent).toContain('已下架');
    first.unmount();

    mocks.getSettings.mockResolvedValue({ ...SETTINGS, plan_status: {} });
    const second = renderWithClient(<OpsSettingsPage />);
    await waitForSettingsForm(second.container);
    expect(second.container.textContent).toContain('还没有套餐状态');
    second.unmount();
  });
});

// ---------------------------------------------------------------------------
// 企业列表
// ---------------------------------------------------------------------------

const TENANT = {
  tenant_id: 't_1',
  name: '华农科技',
  plan_code: 'professional',
  seats_total: 10,
  seats_used: 3,
  status: 'active',
  created_at: '2026-10-01T00:00:00Z',
  quota_units_used: 85400,
};

function mockTenants(items: unknown[]): void {
  mocks.listTenants.mockResolvedValue({
    items,
    page: 1,
    page_size: 20,
    total: items.length,
    total_pages: 1,
  });
}

describe('运营-企业列表', () => {
  it('表格只展示最小可见口径，详情入口带企业标识', async () => {
    mockTenants([TENANT, { ...TENANT, tenant_id: 't_2', name: '', status: 'suspended' }]);
    const { container, unmount } = renderWithClient(<OpsTenantsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('华农科技');
      });
    });
    expect(container.textContent).toContain('3 / 10');
    expect(container.textContent).toContain(formatUnits(85400));
    // 名称缺失回落企业标识；停用状态有业务说法
    expect(container.textContent).toContain('t_2');
    expect(container.textContent).toContain('已停用');

    const detail = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('详情'),
    );
    expect(detail?.getAttribute('href')).toBe('/ops/tenants/t_1');
    unmount();
  });

  it('停用企业走二次确认并写明影响', async () => {
    mockTenants([TENANT]);
    mocks.updateTenantStatus.mockResolvedValue({ tenant_id: 't_1', status: 'suspended' });
    const { container, unmount } = renderWithClient(<OpsTenantsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('华农科技');
      });
    });

    click(findButton(container, '停用'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('停用该企业？');
      });
    });
    expect(document.body.textContent).toContain('数据保留不删除');
    click(dialogButton('停用'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateTenantStatus).toHaveBeenCalledWith('t_1', {
          status: 'suspended',
          reason: '运营后台人工停用',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('该企业已停用');
      });
    });
    unmount();
  });

  it('已停用企业可恢复', async () => {
    mockTenants([{ ...TENANT, status: 'suspended' }]);
    mocks.updateTenantStatus.mockResolvedValue({ tenant_id: 't_1', status: 'active' });
    const { container, unmount } = renderWithClient(<OpsTenantsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('华农科技');
      });
    });

    click(findButton(container, '恢复'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('恢复该企业？');
      });
    });
    click(dialogButton('恢复'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateTenantStatus).toHaveBeenCalledWith('t_1', {
          status: 'active',
          reason: '',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('该企业已恢复正常');
      });
    });
    unmount();
  });

  it('停用失败以业务文案提示', async () => {
    mockTenants([TENANT]);
    mocks.updateTenantStatus.mockRejectedValue(new Error('该企业还有进行中的任务'));
    const { container, unmount } = renderWithClient(<OpsTenantsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('华农科技');
      });
    });

    click(findButton(container, '停用'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('停用该企业？');
      });
    });
    click(dialogButton('停用'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('该企业还有进行中的任务');
      });
    });
    unmount();
  });

  it('筛选参数去空白，双空态与失败态齐备', async () => {
    mockTenants([]);
    const { container, unmount } = renderWithClient(<OpsTenantsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有企业');
      });
    });

    typeInto(container, 'input[aria-label="搜索企业名称"]', '  华农  ');
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTenants).toHaveBeenCalledWith({
          keyword: '华农',
          status: '',
          page: 1,
          page_size: 20,
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('没有符合条件的企业');
      });
    });

    mocks.listTenants.mockRejectedValue(new Error('企业服务暂时不可用'));
    const failed = renderWithClient(<OpsTenantsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(failed.container.textContent).toContain('企业服务暂时不可用');
      });
    });
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTenants.mock.calls.length).toBeGreaterThanOrEqual(3);
      });
    });
    unmount();
    failed.unmount();
  });
});
