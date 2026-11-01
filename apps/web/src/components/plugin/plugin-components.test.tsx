/**
 * 前端补测第十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：插件域展示组件的渲染合同 —— PluginCard 四态主按钮与回调分流、
 * CapabilityList 授权矩阵与「能力 key 不得进 DOM」红线（G-16）、
 * ReviewList 三态与「0 条不渲染空表格」合同。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PluginCapabilityItem, PluginMarketItem, PluginReviewItem } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { CapabilityList } from './capability-list';
import { PluginCard } from './plugin-card';
import { ReviewForm } from './review-form';
import { ReviewList } from './review-list';

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

const BASE_PLUGIN: PluginMarketItem = {
  plugin_id: 'plg_1',
  name: '周报助手',
  summary: '自动整理周报',
  category: 'efficiency',
  icon_key: 'report',
  rating_avg: 4.5,
  rating_count: 12,
  latest_version: '1.2.0',
  installed_status: 'none',
  has_update: false,
  installable: true,
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// PluginCard
// ---------------------------------------------------------------------------

describe('PluginCard', () => {
  it('未安装时主按钮为「查看并安装」', () => {
    const onInstall = vi.fn();
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={BASE_PLUGIN}
        onOpenDetail={vi.fn()}
        onInstall={onInstall}
        onOpenWorkbench={vi.fn()}
      />,
    );
    expect(container.textContent).toContain('查看并安装');

    clickButton(container, '查看并安装');
    expect(onInstall).toHaveBeenCalledWith(BASE_PLUGIN);
    unmount();
  });

  it('已启用时主按钮为「打开工作台」并回调工作台', () => {
    const onOpenWorkbench = vi.fn();
    const onInstall = vi.fn();
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={{ ...BASE_PLUGIN, installed_status: 'enabled' }}
        onOpenDetail={vi.fn()}
        onInstall={onInstall}
        onOpenWorkbench={onOpenWorkbench}
      />,
    );
    expect(container.textContent).toContain('打开工作台');

    clickButton(container, '打开工作台');
    expect(onOpenWorkbench).toHaveBeenCalledWith('plg_1');
    expect(onInstall).not.toHaveBeenCalled();
    unmount();
  });

  it('已停用时主按钮为「去启用」', () => {
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={{ ...BASE_PLUGIN, installed_status: 'disabled' }}
        onOpenDetail={vi.fn()}
        onInstall={vi.fn()}
        onOpenWorkbench={vi.fn()}
      />,
    );
    expect(container.textContent).toContain('去启用');
    unmount();
  });

  it('有新版本时主按钮为「去更新」并展示徽标', () => {
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={{ ...BASE_PLUGIN, installed_status: 'enabled', has_update: true }}
        onOpenDetail={vi.fn()}
        onInstall={vi.fn()}
        onOpenWorkbench={vi.fn()}
      />,
    );
    // 「去更新」优先级高于「打开工作台」
    expect(container.textContent).toContain('去更新');
    expect(container.textContent).toContain('有新版本');
    unmount();
  });

  it('详情按钮打开详情', () => {
    const onOpenDetail = vi.fn();
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={BASE_PLUGIN}
        onOpenDetail={onOpenDetail}
        onInstall={vi.fn()}
        onOpenWorkbench={vi.fn()}
      />,
    );
    clickButton(container, '详情');
    expect(onOpenDetail).toHaveBeenCalledWith('plg_1');
    unmount();
  });

  it('套餐不含插件能力时卡片照常展示（免费版不降级）', () => {
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={{
          ...BASE_PLUGIN,
          installable: false,
          install_block_reason: '当前套餐不含插件能力',
        }}
        onOpenDetail={vi.fn()}
        onInstall={vi.fn()}
        onOpenWorkbench={vi.fn()}
      />,
    );
    // 不隐藏、不灰整卡：名称与主按钮照常
    expect(container.textContent).toContain('周报助手');
    expect(container.textContent).toContain('查看并安装');
    unmount();
  });

  it('简介缺失时占位「暂无简介」', () => {
    const { container, unmount } = renderComponent(
      <PluginCard
        plugin={{ ...BASE_PLUGIN, summary: '' }}
        onOpenDetail={vi.fn()}
        onInstall={vi.fn()}
        onOpenWorkbench={vi.fn()}
      />,
    );
    expect(container.textContent).toContain('暂无简介');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// CapabilityList
// ---------------------------------------------------------------------------

const ORG_CAPABILITY: PluginCapabilityItem = {
  capability_key: 'org.repo.write',
  display_name: '写入代码仓库',
  reason: '需要提交生成的代码',
  denial_effect: '代码无法自动提交',
  category: 'org',
  required: true,
  org_switch_enabled: true,
};

const PERSONAL_CAPABILITY: PluginCapabilityItem = {
  capability_key: 'personal.tool.use',
  display_name: '使用工具',
  reason: '',
  denial_effect: '',
  category: 'personal',
  required: false,
  org_switch_enabled: true,
};

describe('CapabilityList', () => {
  it('能力 key 不进 DOM（G-16 红线）', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[ORG_CAPABILITY, PERSONAL_CAPABILITY]} />,
    );
    expect(container.innerHTML).not.toContain('org.repo.write');
    expect(container.innerHTML).not.toContain('personal.tool.use');
    // 展示名照常
    expect(container.textContent).toContain('写入代码仓库');
    unmount();
  });

  it('必需与可选徽标按 required 区分', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[ORG_CAPABILITY, PERSONAL_CAPABILITY]} />,
    );
    expect(container.textContent).toContain('必需');
    expect(container.textContent).toContain('可选');
    unmount();
  });

  it('企业能力展示「企业级」徽标', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[ORG_CAPABILITY, PERSONAL_CAPABILITY]} />,
    );
    expect(container.textContent).toContain('企业级');
    // 个人能力不带企业标记
    const items = Array.from(container.querySelectorAll('li'));
    const personal = items.find((li) => li.textContent?.includes('使用工具'));
    expect(personal?.textContent).not.toContain('企业级');
    unmount();
  });

  it('传 granted 时区分已授权 / 未授权', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[ORG_CAPABILITY, PERSONAL_CAPABILITY]} granted={['org.repo.write']} />,
    );
    expect(container.textContent).toContain('已授权');
    expect(container.textContent).toContain('未授权');
    unmount();
  });

  it('企业未开启的能力给出「企业未开启」且不谎报已授', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList
        items={[{ ...ORG_CAPABILITY, org_switch_enabled: false }]}
        granted={['org.repo.write']}
      />,
    );
    expect(container.textContent).toContain('企业未开启');
    expect(container.textContent).toContain('已授权');
    unmount();
  });

  it('未说明用途与后果时给兜底文案', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[PERSONAL_CAPABILITY]} />,
    );
    expect(container.textContent).toContain('插件未说明用途');
    expect(container.textContent).toContain('相关步骤将无法完成');
    unmount();
  });

  it('compact 模式只留用途一句话，不渲染后果段', () => {
    const { container, unmount } = renderComponent(
      <CapabilityList items={[ORG_CAPABILITY]} compact />,
    );
    expect(container.textContent).toContain('需要提交生成的代码');
    expect(container.textContent).not.toContain('不同意的后果');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// ReviewList
// ---------------------------------------------------------------------------

const REVIEW: PluginReviewItem = {
  review_id: 'r1',
  rating: 5,
  content: '很好用',
  author_name: '张三',
  department_name: '研发部',
  created_at: '2026-11-01T10:00:00Z',
};

describe('ReviewList', () => {
  it('加载中展示加载态', () => {
    const { container, unmount } = renderComponent(<ReviewList items={[]} loading total={0} />);
    expect(container.textContent).toContain('正在加载评价');
    unmount();
  });

  it('0 条评价不渲染空表格，给空态引导', () => {
    const { container, unmount } = renderComponent(<ReviewList items={[]} total={0} />);
    expect(container.querySelector('table')).toBeNull();
    expect(container.textContent).toContain('还没有人评价');
    unmount();
  });

  it('有总条数但当前页为空时提示翻页', () => {
    const { container, unmount } = renderComponent(<ReviewList items={[]} total={8} />);
    expect(container.textContent).toContain('当前页没有评价，可以翻到其他页看看');
    unmount();
  });

  it('渲染评价行（评价人 / 部门 / 内容）', () => {
    const { container, unmount } = renderComponent(<ReviewList items={[REVIEW]} total={1} />);
    expect(container.textContent).toContain('张三');
    expect(container.textContent).toContain('研发部');
    expect(container.textContent).toContain('很好用');
    unmount();
  });

  it('匿名评价人展示「某位同事」，内容缺失给占位', () => {
    const { container, unmount } = renderComponent(
      <ReviewList items={[{ ...REVIEW, author_name: '', content: '' }]} total={1} />,
    );
    expect(container.textContent).toContain('某位同事');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// ReviewForm
// ---------------------------------------------------------------------------

interface FormOverrides {
  rating?: number;
  content?: string;
  anonymous?: boolean;
  submitting?: boolean;
  canReview?: boolean;
}

function renderForm(overrides: FormOverrides = {}): {
  container: HTMLElement;
  unmount: () => void;
  onSubmit: ReturnType<typeof vi.fn>;
  onAnonymousChange: ReturnType<typeof vi.fn>;
} {
  const onSubmit = vi.fn();
  const onAnonymousChange = vi.fn();
  const rendered = renderComponent(
    <ReviewForm
      rating={overrides.rating ?? 0}
      onRatingChange={vi.fn()}
      content={overrides.content ?? ''}
      onContentChange={vi.fn()}
      anonymous={overrides.anonymous ?? false}
      onAnonymousChange={onAnonymousChange}
      onSubmit={onSubmit}
      submitting={overrides.submitting ?? false}
      canReview={overrides.canReview ?? true}
    />,
  );
  return { ...rendered, onSubmit, onAnonymousChange };
}

describe('ReviewForm', () => {
  it('未选星级时禁止提交（前端只拦这一条）', () => {
    const { container, onSubmit, unmount } = renderForm({ rating: 0 });
    const submit = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('提交'),
    );
    expect(submit?.disabled).toBe(true);

    act(() => {
      submit?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onSubmit).not.toHaveBeenCalled();
    unmount();
  });

  it('已选星级可提交', () => {
    const { container, onSubmit, unmount } = renderForm({ rating: 4, content: '好用' });
    const form = container.querySelector('form');
    act(() => {
      form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    expect(onSubmit).toHaveBeenCalled();
    unmount();
  });

  it('内容长度上限 200 字', () => {
    const { container, unmount } = renderForm({ rating: 5 });
    const textarea = container.querySelector('textarea');
    expect(textarea?.getAttribute('maxlength')).toBe('200');
    unmount();
  });

  it('匿名开关受控回调', () => {
    const { container, onAnonymousChange, unmount } = renderForm({ rating: 5 });
    const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
    act(() => {
      checkbox?.click();
    });
    expect(onAnonymousChange).toHaveBeenCalledWith(true);
    unmount();
  });

  it('提交中禁用全部输入（防连点）', () => {
    const { container, unmount } = renderForm({ rating: 5, submitting: true });
    const textarea = container.querySelector('textarea');
    expect(textarea?.disabled).toBe(true);
    const submit = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('提交'),
    );
    expect(submit?.disabled).toBe(true);
    unmount();
  });

  it('不可评价时整体置灰', () => {
    const { container, unmount } = renderForm({ rating: 5, canReview: false });
    const textarea = container.querySelector('textarea');
    expect(textarea?.disabled).toBe(true);
    unmount();
  });
});
