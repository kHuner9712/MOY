import { Wheat } from 'lucide-react';
import * as React from 'react';

import { BRAND } from '@/lib/brand';
import { cn } from '@/lib/utils';

/** 品牌标识尺寸 */
export type BrandMarkSize = 'sm' | 'md' | 'lg';

/** 品牌标识属性 */
export interface BrandMarkProps {
  /** 尺寸：侧栏 sm / 默认 md / 登录页 lg */
  size?: BrandMarkSize;
  /** 是否显示中文名（默认显示；窄屏可只留麦穗方块） */
  showName?: boolean;
  /** 是否显示英文名（默认显示） */
  showLatin?: boolean;
  /** 外层类名 */
  className?: string;
}

/** 各尺寸的方块、图标、字号 */
const SIZE_STYLES: Record<
  BrandMarkSize,
  { tile: string; icon: string; name: string; latin: string; gap: string }
> = {
  sm: {
    tile: 'h-7 w-7 rounded-lg',
    icon: 'h-4 w-4',
    name: 'text-[13px]',
    latin: 'text-[9px]',
    gap: 'gap-2',
  },
  md: {
    tile: 'h-8 w-8 rounded-lg',
    icon: 'h-[18px] w-[18px]',
    name: 'text-sm',
    latin: 'text-[10px]',
    gap: 'gap-2.5',
  },
  lg: {
    tile: 'h-12 w-12 rounded-2xl',
    icon: 'h-6 w-6',
    name: 'text-xl',
    latin: 'text-[11px]',
    gap: 'gap-3',
  },
};

/**
 * 品牌标识：麦穗方块 + 中文名 + 英文名。
 *
 * 为什么用麦穗而不是抽象几何：品牌名「麦子」本身就有明确意象（生长、积累、收获），
 * 用通用图标会让产品看起来像任何一个后台。图标只承担识别，不承担解释；
 * 主张（Mate Of You）放在登录页与 metadata，不塞进每一个导航项。
 *
 * @param props - 组件属性
 * @returns 品牌标识元素
 */
export function BrandMark({
  size = 'md',
  showName = true,
  showLatin = true,
  className,
}: BrandMarkProps): React.JSX.Element {
  const styles = SIZE_STYLES[size];

  return (
    <span className={cn('inline-flex items-center', styles.gap, className)}>
      <span
        className={cn(
          'inline-flex shrink-0 items-center justify-center bg-primary text-primary-foreground',
          'shadow-sm ring-1 ring-primary/20',
          styles.tile,
        )}
      >
        <Wheat className={styles.icon} aria-hidden="true" strokeWidth={2.2} />
      </span>
      {showName ? (
        <span className="flex min-w-0 flex-col leading-none">
          <span className={cn('font-semibold tracking-tight text-foreground', styles.name)}>
            {BRAND.name}
          </span>
          {showLatin ? (
            <span
              className={cn(
                'mt-1 font-medium uppercase tracking-[0.18em] text-muted-foreground',
                styles.latin,
              )}
            >
              {BRAND.latin}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
