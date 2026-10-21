import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import * as React from 'react';

import { EmptyState } from '@/components/ui/feedback';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/** 表格容器属性 */
export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  /** 外层滚动容器类名 */
  containerClassName?: string;
}

/**
 * 表格容器：负责横向滚动与基础排版。
 *
 * @param props - 组件属性
 * @returns 表格元素
 */
export function Table({
  className,
  containerClassName,
  ...props
}: TableProps): React.JSX.Element {
  return (
    <div className={cn('relative w-full overflow-x-auto', containerClassName)}>
      <table
        className={cn('w-full caption-bottom border-collapse text-sm', className)}
        {...props}
      />
    </div>
  );
}

/**
 * 表头区域。
 *
 * @param props - 组件属性
 * @returns 表头元素
 */
export function TableHeader({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement>): React.JSX.Element {
  return <thead className={cn('[&_tr]:border-b', className)} {...props} />;
}

/**
 * 表体区域。
 *
 * @param props - 组件属性
 * @returns 表体元素
 */
export function TableBody({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement>): React.JSX.Element {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />;
}

/**
 * 表尾区域（合计等）。
 *
 * @param props - 组件属性
 * @returns 表尾元素
 */
export function TableFooter({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement>): React.JSX.Element {
  return (
    <tfoot
      className={cn('border-t bg-muted/40 font-medium [&>tr]:last:border-b-0', className)}
      {...props}
    />
  );
}

/**
 * 表格行。
 *
 * @param props - 组件属性
 * @returns 行元素
 */
export function TableRow({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableRowElement>): React.JSX.Element {
  return (
    <tr
      className={cn('border-b transition-colors hover:bg-muted/40', className)}
      {...props}
    />
  );
}

/**
 * 表头单元格。
 *
 * @param props - 组件属性
 * @returns 表头单元格元素
 */
export function TableHead({
  className,
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement>): React.JSX.Element {
  return (
    <th
      className={cn(
        'h-11 whitespace-nowrap px-3 text-left align-middle text-xs font-medium text-muted-foreground',
        className,
      )}
      {...props}
    />
  );
}

/**
 * 数据单元格。
 *
 * @param props - 组件属性
 * @returns 数据单元格元素
 */
export function TableCell({
  className,
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement>): React.JSX.Element {
  return <td className={cn('px-3 py-3 align-middle', className)} {...props} />;
}

/**
 * 表格说明文字。
 *
 * @param props - 组件属性
 * @returns 说明元素
 */
export function TableCaption({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableCaptionElement>): React.JSX.Element {
  return <caption className={cn('mt-3 text-xs text-muted-foreground', className)} {...props} />;
}

/** 排序方向：null 表示未排序 */
export type SortDirection = 'asc' | 'desc' | null;

/** 可排序表头属性 */
export interface TableSortHeadProps extends React.ThHTMLAttributes<HTMLTableCellElement> {
  /** 当前排序方向 */
  direction?: SortDirection;
  /** 点击切换排序 */
  onSortChange?: () => void;
}

/**
 * 可排序表头（点击后在升序/降序间切换，由使用方维护状态）。
 *
 * @param props - 组件属性
 * @returns 可排序表头元素
 */
export function TableSortHead({
  direction = null,
  onSortChange,
  className,
  children,
  ...props
}: TableSortHeadProps): React.JSX.Element {
  const Icon = direction === 'asc' ? ArrowUp : direction === 'desc' ? ArrowDown : ChevronsUpDown;
  const ariaSort =
    direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none';
  return (
    <TableHead className={className} aria-sort={ariaSort} {...props}>
      <button
        type="button"
        onClick={onSortChange}
        className="inline-flex items-center gap-1 whitespace-nowrap font-medium transition-colors hover:text-foreground"
      >
        {children}
        <Icon
          className={cn(
            'h-3.5 w-3.5',
            direction ? 'text-primary' : 'text-muted-foreground/60',
          )}
        />
      </button>
    </TableHead>
  );
}

/** 空态插槽属性 */
export interface TableEmptyProps {
  /** 合并列数（必填，用于占满整行） */
  colSpan: number;
  /** 主文案，默认「暂无数据」 */
  title?: string;
  /** 补充说明（业务语言，讲清为什么空、下一步做什么） */
  description?: string;
  /** 行动按钮 */
  action?: React.ReactNode;
  className?: string;
}

/**
 * 表格空态插槽：必须作为 `TableBody` 的子元素使用。
 *
 * @param props - 组件属性
 * @returns 空态行元素
 */
export function TableEmpty({
  colSpan,
  title = '暂无数据',
  description,
  action,
  className,
}: TableEmptyProps): React.JSX.Element {
  return (
    <TableRow className={cn('hover:bg-transparent', className)}>
      <TableCell colSpan={colSpan} className="p-0">
        <EmptyState title={title} description={description} action={action} />
      </TableCell>
    </TableRow>
  );
}

/** 加载态插槽属性 */
export interface TableLoadingProps {
  /** 列数（必填，用于对齐表头） */
  columns: number;
  /** 占位行数，默认 5 */
  rows?: number;
  className?: string;
}

/**
 * 表格加载态插槽：必须作为 `TableBody` 的子元素使用。
 *
 * @param props - 组件属性
 * @returns 骨架行元素
 */
export function TableLoading({
  columns,
  rows = 5,
  className,
}: TableLoadingProps): React.JSX.Element {
  const safeColumns = Math.max(1, Math.trunc(columns));
  const safeRows = Math.max(1, Math.trunc(rows));
  return (
    <>
      {Array.from({ length: safeRows }).map((_, rowIndex) => (
        <TableRow
          key={`table-loading-row-${rowIndex}`}
          className={cn('hover:bg-transparent', className)}
        >
          {Array.from({ length: safeColumns }).map((__, colIndex) => (
            <TableCell key={`table-loading-cell-${rowIndex}-${colIndex}`}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}
