'use client';

import { CheckCircle2, CircleDashed, Clock } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/feedback';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { orPlaceholder } from '@/lib/format';
import type { TaskChecklistItem } from '@/lib/types';

/** 核查清单属性 */
export interface ChecklistPanelProps {
  /** A3 `result.checklist[]` */
  items: TaskChecklistItem[];
}

/** 状态 → 展示（业务语言） */
const STATUS_VIEW: Record<
  TaskChecklistItem['status'],
  { label: string; variant: 'success' | 'warning' | 'outline'; icon: typeof CheckCircle2 }
> = {
  checked: { label: '已核查', variant: 'success', icon: CheckCircle2 },
  pending: { label: '待人工确认', variant: 'warning', icon: Clock },
  unchecked: { label: '未核查', variant: 'outline', icon: CircleDashed },
};

/**
 * 结果物核查清单（P0-P3-14）。
 *
 * 三插件项数固定（DEV 4 / CNT 3 / HR 4），名称由后端按声明返回，前端不硬编码。
 * 这是「结果物可核查」的落点：用户据此逐条确认产出是否齐备。
 *
 * @param props - 组件属性
 * @returns 清单元素
 */
export function ChecklistPanel({ items }: ChecklistPanelProps): React.JSX.Element {
  if (items.length === 0) {
    return (
      <EmptyState
        title="本次没有核查清单"
        description="该插件未声明核查项，或任务还没有进入汇总阶段"
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>核查项</TableHead>
          <TableHead>来源步骤</TableHead>
          <TableHead>状态</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => {
          const view = STATUS_VIEW[item.status] ?? STATUS_VIEW.unchecked;
          const Icon = view.icon;
          return (
            <TableRow key={item.key}>
              <TableCell className="text-sm">{orPlaceholder(item.name, '未命名核查项')}</TableCell>
              <TableCell className="text-sm text-muted-foreground tabular-nums">
                第 {item.source_step} 步
              </TableCell>
              <TableCell>
                <Badge variant={view.variant} className="gap-1">
                  <Icon aria-hidden="true" className="h-3 w-3" />
                  {view.label}
                </Badge>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
