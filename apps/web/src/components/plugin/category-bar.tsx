'use client';

import * as React from 'react';

import { Input } from '@/components/ui/input';
import { CompactSelect } from '@/components/ui/select';
import { Tabs } from '@/components/ui/tabs';
import type { PluginCategoryItem, PluginMarketSort } from '@/lib/types';

/** 排序选项（业务语言） */
const SORT_OPTIONS: Array<{ value: PluginMarketSort; label: string }> = [
  { value: 'recommended', label: '推荐排序' },
  { value: 'newest', label: '最新上架' },
  { value: 'rating', label: '评分最高' },
];

/** 分类横筛属性 */
export interface CategoryBarProps {
  /** PL1 返回的分类（含数量） */
  categories: PluginCategoryItem[];
  /** 当前分类 key；空串表示全部 */
  value: string;
  /** 分类变更 */
  onValueChange: (key: string) => void;
  /** 排序值 */
  sort: PluginMarketSort;
  /** 排序变更 */
  onSortChange: (sort: PluginMarketSort) => void;
  /** 关键字（受控） */
  keyword: string;
  /** 关键字变更 */
  onKeywordChange: (keyword: string) => void;
  className?: string;
}

/**
 * 市场分类横筛 + 排序下拉 + 关键字搜索。
 *
 * 设计要点：
 * - 分类项与数量全部来自 PL1 的 `categories[]`，前端不硬编码分类；
 * - 「全部」由前端补一项（后端只返回有插件的分类）；
 * - 搜索框即时过滤（受控），分页由页面负责重置到第 1 页。
 *
 * @param props - 组件属性
 * @returns 筛选条元素
 */
export function CategoryBar({
  categories,
  value,
  onValueChange,
  sort,
  onSortChange,
  keyword,
  onKeywordChange,
  className,
}: CategoryBarProps): React.JSX.Element {
  const tabItems = [
    { value: '', label: '全部' },
    ...categories.map((category) => ({
      value: category.key,
      label: category.name,
      count: category.count,
    })),
  ];

  return (
    <div className={className}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <Tabs
          items={tabItems}
          value={value}
          onValueChange={onValueChange}
          ariaLabel="插件分类"
          className="border-b-0"
        />
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="搜索插件名称或简介"
            aria-label="搜索插件"
            className="sm:w-56"
          />
          <div className="sm:w-36">
            <CompactSelect
              aria-label="排序方式"
              value={sort}
              onChange={(event) => onSortChange(event.target.value as PluginMarketSort)}
              options={SORT_OPTIONS}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
