'use client';

import { ChevronLeft, ChevronRight } from 'lucide-react';
import * as React from 'react';

import { Select } from '@/components/ui/select';
import { cn } from '@/lib/utils';

/** 省略号占位标识 */
type PageToken = number | 'gap-left' | 'gap-right';

/** 默认每页条数候选 */
const DEFAULT_PAGE_SIZE_OPTIONS: number[] = [10, 20, 50, 100];

/**
 * 把页码收敛到 `[1, totalPages]`。
 *
 * 抽成纯函数是为了能被直接断言（无渲染器环境），也让「显示值」与「通知父级的值」
 * 用的是同一份规则——两处各写一遍，改一处漏一处就会出现「显示第 1 页、请求第 5 页」。
 *
 * @param page - 期望页码（可能越界或非整数）
 * @param totalPages - 总页数（至少按 1 页算）
 * @returns 收敛后的页码（≥1 且 ≤totalPages）
 */
export function clampPage(page: number, totalPages: number): number {
  const max = Math.max(1, Math.trunc(totalPages) || 1);
  return Math.min(Math.max(1, Math.trunc(page) || 1), max);
}

/**
 * 由「页码 / 每页条数 / 总条数」推出分页条自身需要的四个值。
 *
 * 单拆一层的原因：`total` 变小（删数据、换筛选条件）时 `totalPages` 会收缩，
 * 此时**父级的 `page` 仍然是旧值**——本函数给出「应该收敛到第几页」，
 * 组件再用 effect 把这个修正值通知父级。
 *
 * @param page - 父级当前页码
 * @param pageSize - 每页条数（非法值按 10 处理）
 * @param total - 总条数（非法值按 0 处理）
 * @returns 安全化后的每页条数、总条数、总页数，以及收敛后的当前页
 */
export function resolvePageState(
  page: number,
  pageSize: number,
  total: number,
): { safePageSize: number; safeTotal: number; totalPages: number; currentPage: number } {
  const safePageSize = Number.isFinite(pageSize) && pageSize > 0 ? Math.trunc(pageSize) : 10;
  const safeTotal = Number.isFinite(total) && total > 0 ? Math.trunc(total) : 0;
  const totalPages = Math.max(1, Math.ceil(safeTotal / safePageSize));
  return { safePageSize, safeTotal, totalPages, currentPage: clampPage(page, totalPages) };
}

/**
 * 生成页码序列：页数较少时全部平铺，页数较多时收起中间部分。
 *
 * @param current - 当前页（1 起）
 * @param totalPages - 总页数
 * @param siblingCount - 当前页左右各展示的页码数量
 * @returns 页码与省略号占位序列
 */
function buildPageTokens(current: number, totalPages: number, siblingCount: number): PageToken[] {
  const totalSlots = siblingCount * 2 + 5;
  if (totalPages <= totalSlots) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const leftSibling = Math.max(current - siblingCount, 1);
  const rightSibling = Math.min(current + siblingCount, totalPages);
  const showLeftGap = leftSibling > 2;
  const showRightGap = rightSibling < totalPages - 1;

  const tokens: PageToken[] = [1];

  if (showLeftGap) {
    tokens.push('gap-left');
  } else {
    for (let pageNumber = 2; pageNumber < leftSibling; pageNumber += 1) {
      tokens.push(pageNumber);
    }
  }

  for (
    let pageNumber = Math.max(leftSibling, 2);
    pageNumber <= Math.min(rightSibling, totalPages - 1);
    pageNumber += 1
  ) {
    tokens.push(pageNumber);
  }

  if (showRightGap) {
    tokens.push('gap-right');
  } else {
    for (let pageNumber = rightSibling + 1; pageNumber < totalPages; pageNumber += 1) {
      tokens.push(pageNumber);
    }
  }

  tokens.push(totalPages);
  return tokens;
}

/** 分页条属性 */
export interface PaginationProps {
  /** 当前页（1 起） */
  page: number;
  /** 每页条数 */
  pageSize: number;
  /** 总条数 */
  total: number;
  /** 翻页回调 */
  onPageChange: (page: number) => void;
  /** 每页条数候选，默认 10 / 20 / 50 / 100 */
  pageSizeOptions?: number[];
  /** 每页条数变更回调；不传则不展示该选择器 */
  onPageSizeChange?: (pageSize: number) => void;
  /** 当前页左右各展示的页码数量，默认 1 */
  siblingCount?: number;
  /** 是否禁用 */
  disabled?: boolean;
  className?: string;
}

/**
 * 分页条：左侧「共 N 条」，中间页码，右侧每页条数。
 *
 * 文案固定使用业务语言（条 / 页），不出现任何接口字段名。
 *
 * @param props - 组件属性
 * @returns 分页条元素
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  onPageSizeChange,
  siblingCount = 1,
  disabled = false,
  className,
}: PaginationProps): React.JSX.Element {
  const { safePageSize, safeTotal, totalPages, currentPage } = resolvePageState(
    page,
    pageSize,
    total,
  );
  const tokens = buildPageTokens(currentPage, totalPages, Math.max(0, Math.trunc(siblingCount)));
  const navDisabled = disabled || totalPages <= 1;

  // `onPageChange` 用 ref 固定：9 个消费点里只要有一个传内联箭头函数，直接把它写进
  // effect 依赖就会「每次渲染换引用 → effect 重跑 → 再通知父级 → 再渲染」地循环。
  // 用 ref 既能拿到最新的回调，又不会因引用变化触发 effect。
  const onPageChangeRef = React.useRef(onPageChange);
  React.useEffect(() => {
    onPageChangeRef.current = onPageChange;
  }, [onPageChange]);

  // 父级 `page` 越界时（典型：删掉最后一条数据，`totalPages` 由 3 变 1），把修正值
  // **通知父级**。只改显示不改父级的话，`useApiQuery` 的 key 仍带旧页码，
  // 父级会一直请求一个空页——用户看到「共 12 条，第 3 / 1 页，列表空白」。
  // 依赖只放两个原始值：`currentPage === page` 时直接返回，因此不会自激循环。
  React.useEffect(() => {
    if (currentPage === page) return;
    onPageChangeRef.current(currentPage);
  }, [currentPage, page]);

  /** 跳转到指定页（越界自动收敛） */
  const goTo = (next: number): void => {
    const target = Math.min(Math.max(1, next), totalPages);
    if (target === currentPage) return;
    onPageChange(target);
  };

  return (
    <nav
      aria-label="分页"
      className={cn(
        'flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <p className="text-xs text-muted-foreground tabular-nums">
        共 {safeTotal} 条
        {safeTotal > 0 ? (
          <span className="ml-2">
            第 {currentPage} / {totalPages} 页
          </span>
        ) : null}
      </p>

      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="上一页"
          disabled={navDisabled || currentPage <= 1}
          onClick={() => goTo(currentPage - 1)}
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        {tokens.map((token) =>
          typeof token === 'number' ? (
            <button
              key={`page-${token}`}
              type="button"
              aria-label={`第 ${token} 页`}
              aria-current={token === currentPage ? 'page' : undefined}
              disabled={disabled}
              onClick={() => goTo(token)}
              className={cn(
                'inline-flex h-9 min-w-9 items-center justify-center rounded-md border px-2 text-sm font-medium tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
                token === currentPage
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-transparent text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              {token}
            </button>
          ) : (
            <span
              key={token}
              aria-hidden="true"
              className="inline-flex h-9 w-6 items-center justify-center text-sm text-muted-foreground"
            >
              …
            </span>
          ),
        )}

        <button
          type="button"
          aria-label="下一页"
          disabled={navDisabled || currentPage >= totalPages}
          onClick={() => goTo(currentPage + 1)}
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>

      {onPageSizeChange ? (
        <div className="flex items-center gap-2">
          <span className="whitespace-nowrap text-xs text-muted-foreground">每页</span>
          <Select
            aria-label="每页条数"
            wrapperClassName="w-28"
            className="h-9 text-xs"
            value={String(safePageSize)}
            disabled={disabled}
            onChange={(event) => {
              const nextSize = Number(event.target.value);
              if (Number.isFinite(nextSize) && nextSize > 0) onPageSizeChange(nextSize);
            }}
            options={pageSizeOptions.map((option) => ({
              value: String(option),
              label: `${option} 条`,
            }))}
          />
        </div>
      ) : null}
    </nav>
  );
}
