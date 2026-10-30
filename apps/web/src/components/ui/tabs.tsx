'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

/** 标签项定义 */
export interface TabItem {
  /** 稳定标识（同时用作分享链接里的参数值，变更会导致旧链接失效） */
  value: string;
  /** 展示文案（业务语言） */
  label: string;
  /** 右侧数量徽标，如「成员 12」 */
  count?: number;
  /** 是否禁用 */
  disabled?: boolean;
}

/** 标签组属性 */
export interface TabsProps {
  /** 标签项列表 */
  items: TabItem[];
  /** 受控值 */
  value?: string;
  /** 非受控初始值（缺省取第一项） */
  defaultValue?: string;
  /** 切换回调 */
  onValueChange?: (value: string) => void;
  /** 无障碍名称 */
  ariaLabel?: string;
  className?: string;
}

/**
 * 顶部标签组（受控/非受控皆可，支持键盘左右切换）。
 *
 * 与 URL 联动请搭配 `useUrlTab`：把返回的 value/setValue 传给这里，
 * 用户刷新或把链接发给同事后仍停留在同一个标签（第二阶段三标签页面统一用法）。
 *
 * @param props - 组件属性
 * @returns 标签组元素
 */
export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  ariaLabel = '视图切换',
  className,
}: TabsProps): React.JSX.Element {
  const [innerValue, setInnerValue] = React.useState<string>(defaultValue ?? items[0]?.value ?? '');
  const current: string = value !== undefined ? value : innerValue;

  /** 选中某个标签 */
  const selectTab = React.useCallback(
    (next: string): void => {
      if (next === current) return;
      if (value === undefined) setInnerValue(next);
      onValueChange?.(next);
    },
    [current, onValueChange, value],
  );

  /** 键盘导航：左右箭头 / Home / End */
  const handleKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>): void => {
    const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const nodes = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'),
    );
    if (nodes.length === 0) return;
    const activeIndex = nodes.findIndex((node) => node === document.activeElement);
    const safeIndex = activeIndex < 0 ? 0 : activeIndex;
    let nextIndex = safeIndex;
    if (event.key === 'ArrowRight') nextIndex = (safeIndex + 1) % nodes.length;
    else if (event.key === 'ArrowLeft') nextIndex = (safeIndex - 1 + nodes.length) % nodes.length;
    else if (event.key === 'Home') nextIndex = 0;
    else nextIndex = nodes.length - 1;
    event.preventDefault();
    nodes[nextIndex]?.focus();
  }, []);

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={handleKeyDown}
      className={cn('flex items-center gap-1 overflow-x-auto border-b', className)}
    >
      {items.map((item) => {
        const active = item.value === current;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            id={`tab-${item.value}`}
            aria-selected={active}
            aria-controls={`panel-${item.value}`}
            tabIndex={active ? 0 : -1}
            disabled={item.disabled}
            onClick={() => selectTab(item.value)}
            className={cn(
              '-mb-px inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
              active
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {item.label}
            {typeof item.count === 'number' ? (
              <span
                className={cn(
                  'rounded-full px-1.5 text-xs font-medium tabular-nums',
                  active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                )}
              >
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** 标签面板属性 */
export interface TabPanelProps {
  /** 与 `TabItem.value` 对应 */
  value: string;
  /** 当前选中的标签值 */
  activeValue: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * 标签内容面板（与 `Tabs` 的 value 对齐，非激活时不渲染）。
 *
 * @param props - 组件属性
 * @returns 面板元素
 */
export function TabPanel({
  value,
  activeValue,
  children,
  className,
}: TabPanelProps): React.JSX.Element | null {
  if (value !== activeValue) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${value}`}
      aria-labelledby={`tab-${value}`}
      tabIndex={0}
      className={cn('focus-visible:outline-none', className)}
    >
      {children}
    </div>
  );
}

/**
 * 把标签选中值同步到地址栏查询参数（`?key=value`），实现「链接可分享、刷新不丢页」。
 *
 * 说明：故意不使用框架自带的路由 hooks，避免页面在构建期被要求包一层 Suspense；
 * 首次渲染使用 fallback，挂载后再从地址栏回填，不会产生内容闪烁。
 *
 * @param paramKey - 查询参数名，如 `tab`
 * @param fallback - 缺省标签值（地址栏无该参数且未选择时使用）
 * @returns `[当前值, 切换函数]`
 */
export function useUrlTab(
  paramKey: string,
  fallback: string,
): readonly [string, (next: string) => void] {
  const [value, setValue] = React.useState<string>(fallback);

  // 挂载后读取地址栏，保证分享链接直达对应标签
  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const fromUrl = new URLSearchParams(window.location.search).get(paramKey);
    if (fromUrl) setValue(fromUrl);
  }, [paramKey]);

  /** 切换并写回地址栏（替换历史记录，避免后退键被标签切换刷屏） */
  const setTab = React.useCallback(
    (next: string): void => {
      setValue(next);
      if (typeof window === 'undefined') return;
      const params = new URLSearchParams(window.location.search);
      if (next === fallback) params.delete(paramKey);
      else params.set(paramKey, next);
      const query = params.toString();
      const hash = window.location.hash;
      const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}${hash}`;
      window.history.replaceState(null, '', nextUrl);
    },
    [fallback, paramKey],
  );

  return [value, setTab] as const;
}
