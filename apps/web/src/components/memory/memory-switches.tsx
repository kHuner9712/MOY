'use client';

import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { MEMORY_CATEGORY_LABELS, MEMORY_CATEGORY_SWITCH_HINT } from '@/lib/memory-labels';
import { MEMORY_CATEGORIES, type MemoryCategory, type MemorySettings } from '@/lib/types';
import { cn } from '@/lib/utils';

/** 开关面板属性 */
export interface MemorySwitchesProps {
  /** 当前开关状态（来自 MM1，写完由页面回填） */
  settings: MemorySettings;
  /** 是否正在保存（禁用全部开关，避免连点） */
  saving?: boolean;
  /** 全局开关变更 */
  onToggleEnabled: (enabled: boolean) => void;
  /** 分类开关变更 */
  onToggleCategory: (category: MemoryCategory, enabled: boolean) => void;
  /** 附加类名 */
  className?: string;
}

/** 单个开关（无障碍：`role=switch` + `aria-checked`） */
function SwitchButton({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: (next: boolean) => void;
}): React.JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors',
        checked ? 'border-transparent bg-primary' : 'border-input bg-muted',
        disabled ? 'cursor-not-allowed opacity-60' : '',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block h-4 w-4 transform rounded-full bg-background shadow transition-transform',
          checked ? 'translate-x-6' : 'translate-x-1',
        )}
      />
    </button>
  );
}

/**
 * 记忆开关面板：全局开关 + 6 个分类开关。
 *
 * 两条产品口径（PRD §4.4）：
 * 1. **关全局 = 停止整理 + 停止使用**，已记住的**保留**（页面顶部同时出现关闭横幅）；
 * 2. **关分类 = 该类不整理不使用**，已有条目**保留**（文案里必须写出来，
 *    否则用户会以为"关了等于删了"）。
 *
 * @param props - 组件属性
 * @returns 开关面板元素
 */
export function MemorySwitches({
  settings,
  saving = false,
  onToggleEnabled,
  onToggleCategory,
  className,
}: MemorySwitchesProps): React.JSX.Element {
  return (
    <section className={cn('space-y-4', className)} aria-label="记忆设置">
      <div className="flex items-start justify-between gap-4 rounded-lg border p-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">从新对话里了解我</p>
          <p className="text-xs text-muted-foreground">
            关闭后我不再从新对话里记东西；已记住的仍在这里，你可以删除。
          </p>
        </div>
        <SwitchButton
          checked={settings.enabled}
          disabled={saving}
          label="从新对话里了解我"
          onChange={onToggleEnabled}
        />
      </div>

      <div className="rounded-lg border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">分类</p>
          <Badge variant="outline">关闭后已有条目保留</Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{MEMORY_CATEGORY_SWITCH_HINT}</p>

        <ul className="mt-3 space-y-2">
          {MEMORY_CATEGORIES.map((category) => {
            const label = MEMORY_CATEGORY_LABELS[category];
            const checked = settings.category_switches[category] ?? true;
            return (
              <li key={category} className="flex items-center justify-between gap-3">
                <span className="text-sm">{label}</span>
                <SwitchButton
                  checked={checked}
                  disabled={saving || !settings.enabled}
                  label={`${label}：${checked ? '已开启' : '已关闭'}`}
                  onChange={(next) => onToggleCategory(category, next)}
                />
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
