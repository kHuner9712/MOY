/** 样式工具：类名合并（shadcn/ui 风格约定）。 */

import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * 合并 Tailwind 类名，后者覆盖前者的同类属性。
 *
 * @param inputs - 任意数量的类名或条件类名
 * @returns 合并后的类名字符串
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
