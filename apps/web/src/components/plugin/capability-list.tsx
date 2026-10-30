'use client';

import { Lock } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Tooltip } from '@/components/ui/tooltip';
import { capabilityLabel, isOrgCapability } from '@/lib/capability-labels';
import { cn } from '@/lib/utils';
import type { PluginCapabilityItem } from '@/lib/types';

/** 能力清单属性 */
export interface CapabilityListProps {
  /** PL2 / PL4 返回的能力条目（与声明 1:1） */
  items: PluginCapabilityItem[];
  /** 我已授权的能力 key 列表；传则展示「已授权 / 未授权」 */
  granted?: string[];
  /** 紧凑模式（抽屉内使用，不重复大标题） */
  compact?: boolean;
  className?: string;
}

/**
 * 能力清单：逐项展示「名称 / 为什么需要 / 不同意的后果」。
 *
 * 硬约束：
 * - 展示名优先用后端 `display_name`，缺失时用 `capabilityLabel()` 兜底，
 *   **能力 key 不得进 DOM**（G-16 断言）；
 * - 企业能力在企业开关关闭时给出「需管理员开启」，并且不谎报为「已授」；
 * - 个人能力不显示企业开关相关文案（personal.* 恒开）。
 *
 * @param props - 组件属性
 * @returns 能力清单元素
 */
export function CapabilityList({
  items,
  granted,
  compact = false,
  className,
}: CapabilityListProps): React.JSX.Element {
  return (
    <ul className={cn('space-y-3', className)}>
      {items.map((item) => {
        const label = capabilityLabel(item.capability_key, item.display_name);
        const orgScoped = item.category === 'org' || isOrgCapability(item.capability_key);
        const grantedState = granted ? granted.includes(item.capability_key) : undefined;
        const orgBlocked = orgScoped && !item.org_switch_enabled;

        return (
          <li key={item.capability_key} className="rounded-md border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{label}</span>
              {item.required ? (
                <Badge variant="secondary">必需</Badge>
              ) : (
                <Badge variant="outline">可选</Badge>
              )}
              {orgScoped ? <Badge variant="outline">企业级</Badge> : null}
              {grantedState === true ? <Badge variant="success">已授权</Badge> : null}
              {grantedState === false ? <Badge variant="outline">未授权</Badge> : null}
              {orgBlocked ? (
                <Tooltip content="需管理员在团队设置中开启该能力后才能授权">
                  <Badge variant="warning" className="gap-1">
                    <Lock aria-hidden="true" className="h-3 w-3" />
                    企业未开启
                  </Badge>
                </Tooltip>
              ) : null}
            </div>

            {!compact ? (
              <dl className="mt-2 space-y-1 text-xs text-muted-foreground">
                <div>
                  <dt className="inline font-medium">为什么需要：</dt>
                  <dd className="inline">{item.reason || '插件未说明用途'}</dd>
                </div>
                <div>
                  <dt className="inline font-medium">不同意的后果：</dt>
                  <dd className="inline">{item.denial_effect || '相关步骤将无法完成'}</dd>
                </div>
              </dl>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">
                {item.reason || '插件未说明用途'}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
