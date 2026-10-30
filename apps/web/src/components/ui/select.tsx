'use client';

import { ChevronDown } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 下拉选项 */
export interface SelectOptionItem {
  /** 提交值 */
  value: string;
  /** 展示文案（业务语言） */
  label: string;
  /** 是否禁用 */
  disabled?: boolean;
}

/** 下拉选择属性 */
export interface SelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  /** 选项列表（与 children 二选一，同时给出时以 children 为准） */
  options?: SelectOptionItem[];
  /** 无选中值时的占位文案 */
  placeholder?: string;
  /** 错误态 */
  invalid?: boolean;
  /** 外层容器类名 */
  wrapperClassName?: string;
  /** 自定义选项节点（需要分组 optgroup 等高级用法时使用） */
  children?: React.ReactNode;
}

/**
 * 下拉选择：原生 `select` 封装（无额外依赖），外观与输入框保持一致。
 *
 * 选择原生实现而不是自绘浮层：移动端直接调用系统选择器，可访问性与键盘操作零成本。
 *
 * @param props - 组件属性
 * @returns 下拉选择元素
 */
export function Select({
  options,
  placeholder,
  invalid,
  className,
  wrapperClassName,
  children,
  ...props
}: SelectProps): React.JSX.Element {
  return (
    <div className={cn('relative inline-flex w-full items-center', wrapperClassName)}>
      <select
        aria-invalid={invalid || undefined}
        className={cn(
          'h-10 w-full cursor-pointer appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm ring-offset-background transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
          invalid && 'border-destructive focus-visible:ring-destructive',
          className,
        )}
        {...props}
      >
        {placeholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {children ??
          options?.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          ))}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 h-4 w-4 text-muted-foreground"
      />
    </div>
  );
}

/** 紧凑型下拉属性（用于表格工具条）；`size` 覆盖原生同名属性 */
export interface CompactSelectProps extends Omit<SelectProps, 'size'> {
  /** 紧凑尺寸，默认 `sm` */
  size?: 'sm' | 'md';
}

/**
 * 紧凑下拉：用于表格工具条、分页条等空间受限处。
 *
 * @param props - 组件属性
 * @returns 下拉选择元素
 */
export function CompactSelect({
  size = 'sm',
  className,
  ...props
}: CompactSelectProps): React.JSX.Element {
  return (
    <Select className={cn(size === 'sm' ? 'h-9 text-xs' : 'h-10 text-sm', className)} {...props} />
  );
}
