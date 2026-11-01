/**
 * 前端补测第十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：记忆域展示组件的渲染合同 —— MemoryCard 三口径（类别中文名不上内部
 * 标识 / 推测标「待确认」/ 历史值折叠）、MemoryEmpty 六态文案与出口按钮、
 * MemorySwitches 开关语义（关 = 停用不删除）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MemoryItem, MemorySettings } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { MemoryCard } from './memory-card';
import { MemoryEmpty } from './memory-empty';
import { MemorySwitches } from './memory-switches';

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

const ITEM: MemoryItem = {
  memory_id: 'mem_1',
  category: 'identity',
  content: '你叫李明',
  confidence: 'high',
  source_conversation_id: 'conv_1',
  updated_at: '2026-11-01T10:00:00Z',
  superseded_by: '',
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// MemoryCard
// ---------------------------------------------------------------------------

describe('MemoryCard', () => {
  it('类别用中文名，内部标识不上屏', () => {
    const { container, unmount } = renderComponent(<MemoryCard item={ITEM} onDelete={vi.fn()} />);
    // aria-label 与徽标都是中文类名
    expect(container.textContent).toContain('称呼');
    expect(container.innerHTML).not.toContain('identity');
    unmount();
  });

  it('推测性信息标「待确认」', () => {
    const { container, unmount } = renderComponent(
      <MemoryCard item={{ ...ITEM, confidence: 'low' }} onDelete={vi.fn()} />,
    );
    expect(container.textContent).toContain('待确认');
    unmount();
  });

  it('明确陈述不标「待确认」', () => {
    const { container, unmount } = renderComponent(<MemoryCard item={ITEM} onDelete={vi.fn()} />);
    expect(container.textContent).not.toContain('待确认');
    unmount();
  });

  it('历史值标「历史」并折叠「你之前说过…」', () => {
    const { container, unmount } = renderComponent(
      <MemoryCard item={{ ...ITEM, superseded_by: 'mem_2' }} onDelete={vi.fn()} />,
    );
    expect(container.textContent).toContain('历史');
    expect(container.textContent).toContain('你之前说过…');
    unmount();
  });

  it('来源标注：对话产生 / 设置产生', () => {
    const fromChat = renderComponent(<MemoryCard item={ITEM} onDelete={vi.fn()} />);
    expect(fromChat.container.textContent).toContain('来自一次对话');
    fromChat.unmount();

    const fromSetting = renderComponent(
      <MemoryCard item={{ ...ITEM, source_conversation_id: '' }} onDelete={vi.fn()} />,
    );
    expect(fromSetting.container.textContent).toContain('来自设置');
    fromSetting.unmount();
  });

  it('删除按钮回调并携带条目；删除中禁用', () => {
    const onDelete = vi.fn();
    const { container, unmount } = renderComponent(<MemoryCard item={ITEM} onDelete={onDelete} />);
    clickButton(container, '删除');
    expect(onDelete).toHaveBeenCalledWith(ITEM);
    unmount();

    const saving = renderComponent(<MemoryCard item={ITEM} onDelete={vi.fn()} deleting />);
    const button = Array.from(saving.container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('删除'),
    );
    expect(button?.disabled).toBe(true);
    saving.unmount();
  });
});

// ---------------------------------------------------------------------------
// MemoryEmpty
// ---------------------------------------------------------------------------

describe('MemoryEmpty', () => {
  it('从未产生：引导去看对话', () => {
    const onGoChat = vi.fn();
    const { container, unmount } = renderComponent(
      <MemoryEmpty kind="never" onRetry={vi.fn()} onGoChat={onGoChat} />,
    );
    expect(container.textContent).toContain('我还不太了解你');
    clickButton(container, '去看对话');
    expect(onGoChat).toHaveBeenCalled();
    unmount();
  });

  it('已全部删除：说明之后会重新开始了解', () => {
    const { container, unmount } = renderComponent(
      <MemoryEmpty kind="cleared" onRetry={vi.fn()} />,
    );
    expect(container.textContent).toContain('你的记忆已全部删除');
    // 该状态没有动作按钮
    expect(container.querySelectorAll('button')).toHaveLength(0);
    unmount();
  });

  it('已关闭：提供「重新开启」，开启中显示进行时文案', () => {
    const onEnable = vi.fn();
    const { container, unmount } = renderComponent(
      <MemoryEmpty kind="disabled" onRetry={vi.fn()} onEnable={onEnable} />,
    );
    expect(container.textContent).toContain('记忆已关闭');
    clickButton(container, '重新开启');
    expect(onEnable).toHaveBeenCalled();
    unmount();

    const enabling = renderComponent(
      <MemoryEmpty kind="disabled" onRetry={vi.fn()} onEnable={vi.fn()} enabling />,
    );
    expect(enabling.container.textContent).toContain('正在开启…');
    enabling.unmount();
  });

  it('加载失败：错误态必须有重试出口', () => {
    const onRetry = vi.fn();
    const { container, unmount } = renderComponent(<MemoryEmpty kind="failed" onRetry={onRetry} />);
    clickButton(container, '重试');
    expect(onRetry).toHaveBeenCalled();
    unmount();
  });

  it('筛选无结果与暂时不可用各有文案', () => {
    const filtered = renderComponent(<MemoryEmpty kind="filtered" onRetry={vi.fn()} />);
    expect(filtered.container.textContent).toContain('这个类别下还没有信息');
    filtered.unmount();

    const unavailable = renderComponent(<MemoryEmpty kind="unavailable" onRetry={vi.fn()} />);
    expect(unavailable.container.textContent).toBeTruthy();
    unavailable.unmount();
  });
});

// ---------------------------------------------------------------------------
// MemorySwitches
// ---------------------------------------------------------------------------

const SETTINGS: MemorySettings = {
  enabled: true,
  category_switches: {
    identity: true,
    preference: false,
  },
  updated_at: '2026-11-01T10:00:00Z',
};

describe('MemorySwitches', () => {
  it('渲染全局开关与各分类开关（role=switch）', () => {
    const { container, unmount } = renderComponent(
      <MemorySwitches settings={SETTINGS} onToggleEnabled={vi.fn()} onToggleCategory={vi.fn()} />,
    );
    const switches = container.querySelectorAll('[role="switch"]');
    // 1 个全局 + 6 个分类
    expect(switches.length).toBeGreaterThanOrEqual(2);
    unmount();
  });

  it('开关状态反映到 aria-checked', () => {
    const { container, unmount } = renderComponent(
      <MemorySwitches settings={SETTINGS} onToggleEnabled={vi.fn()} onToggleCategory={vi.fn()} />,
    );
    const switches = Array.from(container.querySelectorAll('[role="switch"]'));
    const checked = switches.filter((item) => item.getAttribute('aria-checked') === 'true');
    const unchecked = switches.filter((item) => item.getAttribute('aria-checked') === 'false');
    expect(checked.length).toBeGreaterThanOrEqual(1);
    expect(unchecked.length).toBeGreaterThanOrEqual(1);
    unmount();
  });

  it('点击全局开关回调取反值', () => {
    const onToggleEnabled = vi.fn();
    const { container, unmount } = renderComponent(
      <MemorySwitches
        settings={SETTINGS}
        onToggleEnabled={onToggleEnabled}
        onToggleCategory={vi.fn()}
      />,
    );
    const globalSwitch = container.querySelector('[role="switch"]');
    act(() => {
      globalSwitch?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onToggleEnabled).toHaveBeenCalledWith(false);
    unmount();
  });

  it('点击分类开关回调类别与取反值', () => {
    const onToggleCategory = vi.fn();
    const { container, unmount } = renderComponent(
      <MemorySwitches
        settings={SETTINGS}
        onToggleEnabled={vi.fn()}
        onToggleCategory={onToggleCategory}
      />,
    );
    // preference 当前为 false，点击应请求开启
    const switches = Array.from(container.querySelectorAll('[role="switch"]'));
    const target = switches.find((item) => item.getAttribute('aria-checked') === 'false');
    act(() => {
      target?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onToggleCategory).toHaveBeenCalledWith('preference', true);
    unmount();
  });

  it('保存中禁用全部开关（防连点）', () => {
    const { container, unmount } = renderComponent(
      <MemorySwitches
        settings={SETTINGS}
        saving
        onToggleEnabled={vi.fn()}
        onToggleCategory={vi.fn()}
      />,
    );
    const switches = Array.from(container.querySelectorAll('[role="switch"]'));
    expect(switches.length).toBeGreaterThan(0);
    for (const item of switches) {
      expect((item as HTMLButtonElement).disabled).toBe(true);
    }
    unmount();
  });
});
